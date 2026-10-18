"""Durable latest-set confirmations. No Telegram/MQTT I/O in transactions."""
from contextlib import closing
import json
import re
import secrets
import time

from notification_store import NotificationStore, utc_now
from device_registry import active_devices, device_set

SCHEMA = """
CREATE TABLE IF NOT EXISTS confirmation_calls (
 event_id TEXT PRIMARY KEY REFERENCES call_events(event_id) ON DELETE CASCADE,
 device_id TEXT NOT NULL, set_id TEXT NOT NULL REFERENCES carecall_sets(set_id), boot_order TEXT NOT NULL, sequence INTEGER NOT NULL,
 confirmed_at TEXT, device_result TEXT CHECK(device_result IN ('applied','stale')),
 next_attempt_at REAL NOT NULL DEFAULT 0
) STRICT;
CREATE INDEX IF NOT EXISTS confirmation_order
 ON confirmation_calls(device_id,boot_order,sequence);
CREATE TABLE IF NOT EXISTS confirmation_latest (
 device_id TEXT PRIMARY KEY,
 event_id TEXT NOT NULL REFERENCES confirmation_calls(event_id) ON DELETE CASCADE
) STRICT;
CREATE TABLE IF NOT EXISTS confirmation_set_latest (
 set_id TEXT PRIMARY KEY REFERENCES carecall_sets(set_id),
 event_id TEXT NOT NULL REFERENCES confirmation_calls(event_id) ON DELETE CASCADE,
 revision INTEGER NOT NULL CHECK(revision>=1)
) STRICT;
CREATE TABLE IF NOT EXISTS confirmation_messages (
 notification_id INTEGER PRIMARY KEY
   REFERENCES notification_outbox(notification_id) ON DELETE CASCADE,
 token TEXT NOT NULL UNIQUE,
 applied TEXT NOT NULL DEFAULT 'empty' CHECK(applied IN ('empty','button','unknown','gone')),
 next_attempt_at REAL NOT NULL DEFAULT 0,
 attempts INTEGER NOT NULL DEFAULT 0
) STRICT;
"""


def register_call(c, call):
    """Called only for a newly inserted call, in its existing transaction.

    Within one device, persistent boot counter + sequence define generation order.
    Old/nonstandard IDs still get their original notification without a button.
    """
    match = re.fullmatch(re.escape(call.device_id) + r'-([0-9a-f]{16})-([0-9]{8,10})', call.event_id)
    if not match or int(match[2]) != call.sequence:
        return
    c.execute('''INSERT INTO confirmation_calls(event_id,device_id,set_id,boot_order,sequence)
                 VALUES(?,?,?,?,?)''', (call.event_id, call.device_id, device_set(c, call.device_id), match[1], call.sequence))
    current = c.execute('''SELECT e.* FROM confirmation_latest l
        JOIN confirmation_calls e USING(event_id) WHERE l.device_id=?''', (call.device_id,)).fetchone()
    if current is None or (match[1], call.sequence) > (current['boot_order'], current['sequence']):
        c.execute('''INSERT INTO confirmation_latest VALUES(?,?)
            ON CONFLICT(device_id) DO UPDATE SET event_id=excluded.event_id''',
            (call.device_id, call.event_id))
        # SQLite writer transactions order devices in a set. Never compare
        # the boot counter or sequence of two different devices.
        c.execute('''INSERT INTO confirmation_set_latest(set_id,event_id,revision) VALUES(?,?,1)
            ON CONFLICT(set_id) DO UPDATE SET event_id=excluded.event_id,
            revision=confirmation_set_latest.revision+1''',
            (device_set(c, call.device_id), call.event_id))


def register_message(c, notification_id):
    # A message is sent WITHOUT a keyboard. Only a known, persisted message ID
    # can later receive a keyboard. An ambiguous send cannot orphan a live button.
    row = c.execute('''SELECT 1 FROM notification_outbox o
        JOIN confirmation_calls e USING(event_id)
        WHERE o.notification_id=? AND o.status='sent' AND o.telegram_message_id>0''',
        (notification_id,)).fetchone()
    if row:
        c.execute('INSERT OR IGNORE INTO confirmation_messages(notification_id,token) VALUES(?,?)',
                  (notification_id, secrets.token_urlsafe(24)))


def apply_callback(c, query, actor):
    data = query.get('data')
    if not isinstance(data, str) or not re.fullmatch(r'c:[A-Za-z0-9_-]{32}', data):
        return 'call_forbidden'
    message = query.get('message')
    if not isinstance(message, dict) or type(message.get('message_id')) is not int:
        return 'call_forbidden'
    row = c.execute('''SELECT o.event_id,o.set_id,o.chat_id,o.telegram_message_id,
        e.confirmed_at,l.event_id AS latest_id,r.enabled
        FROM confirmation_messages m JOIN notification_outbox o USING(notification_id)
        JOIN confirmation_calls e USING(event_id)
        JOIN confirmation_set_latest l ON l.set_id=o.set_id
        JOIN notification_recipients r ON r.set_id=o.set_id AND r.chat_id=o.chat_id
        WHERE m.token=?''', (data[2:],)).fetchone()
    if (row is None or actor is None or actor != row['chat_id'] or row['enabled'] != 1
            or message['message_id'] != row['telegram_message_id']):
        return 'call_forbidden'
    if row['event_id'] != row['latest_id']:
        return 'call_stale'
    if row['confirmed_at'] is not None:
        return 'call_already_confirmed'
    c.execute('UPDATE confirmation_calls SET confirmed_at=?,next_attempt_at=0 WHERE set_id=? AND confirmed_at IS NULL',
              (utc_now(), row['set_id']))
    return 'call_confirmed'


class ConfirmationStore(NotificationStore):
    def migrate(self):
        with closing(self.connect()) as c:
            c.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nCOMMIT;')

    def next_markup(self, now=None):
        now = time.time() if now is None else now
        with closing(self.connect()) as c:
            row = c.execute('''SELECT * FROM (
                SELECT m.*,o.chat_id,o.telegram_message_id,
                  CASE WHEN l.event_id=o.event_id AND e.confirmed_at IS NULL AND r.enabled=1
                  THEN 'button' ELSE 'empty' END AS desired
                FROM confirmation_messages m JOIN notification_outbox o USING(notification_id)
                JOIN confirmation_calls e USING(event_id)
                JOIN confirmation_set_latest l ON l.set_id=o.set_id
                JOIN notification_recipients r ON r.set_id=o.set_id AND r.chat_id=o.chat_id
                WHERE m.applied!='gone' AND m.next_attempt_at<=?
            ) WHERE applied!=desired
            ORDER BY CASE desired WHEN 'empty' THEN 0 ELSE 1 END, notification_id LIMIT 1''',
            (now,)).fetchone()
            return dict(row) if row else None

    def begin_markup(self, job):
        # Persist before HTTP: a timeout/crash may still change Telegram's keyboard.
        # 'unknown' forces reconciliation even if the latest call changes meanwhile.
        with closing(self.connect()) as c:
            c.execute("UPDATE confirmation_messages SET applied='unknown' WHERE notification_id=?",
                      (job['notification_id'],))

    def finish_markup(self, job, *, retry_at=None, gone=False):
        with closing(self.connect()) as c:
            if retry_at is not None:
                c.execute('''UPDATE confirmation_messages SET next_attempt_at=?,attempts=attempts+1
                    WHERE notification_id=?''', (retry_at, job['notification_id']))
            else:
                # Use the state actually requested, not a newly computed desired state.
                # If a new call arrived during HTTP I/O, the next pass reconciles it.
                c.execute('''UPDATE confirmation_messages SET applied=?,next_attempt_at=0,attempts=0
                    WHERE notification_id=?''', ('gone' if gone else job['desired'], job['notification_id']))

    def claim_command(self, now=None):
        now = time.time() if now is None else now
        with closing(self.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('''SELECT e.* FROM confirmation_calls e
                JOIN confirmation_latest l USING(event_id)
                WHERE e.confirmed_at IS NOT NULL AND e.device_result IS NULL
                AND EXISTS (SELECT 1 FROM carecall_devices d WHERE d.device_id=e.device_id AND d.state='active')
                AND e.next_attempt_at<=? ORDER BY e.next_attempt_at LIMIT 1''', (now,)).fetchone()
            if row is None:
                return None
            c.execute('UPDATE confirmation_calls SET next_attempt_at=? WHERE event_id=?',
                      (now + 10, row['event_id']))
            return dict(row)

    def receive_device_result(self, topic, payload):
        """Return False for an ordinary call. Strictly validate confirmation reports."""
        from message_validator import _reject_duplicate_keys, _reject_non_finite_number
        try:
            document = json.loads(payload, object_pairs_hook=_reject_duplicate_keys,
                                  parse_constant=_reject_non_finite_number)
        except (ValueError, UnicodeError):
            return False  # Original call validator will reject it.
        if not isinstance(document, dict) or document.get('event_type') != 'care_confirmation_result':
            return False
        if (len(payload) > 512 or set(document) !=
                {'schema_version','event_type','event_id','device_id','status'}):
            raise ValueError('invalid_confirmation_report')
        device = document['device_id']
        if (type(document['schema_version']) is not int or document['schema_version'] != 1
                or not isinstance(device, str)
                or topic != f'carecall/v1/devices/{device}/call'
                or not isinstance(document['event_id'], str) or not 1 <= len(document['event_id']) <= 63
                or document['status'] not in ('applied','stale')):
            raise ValueError('invalid_confirmation_report')
        with closing(self.connect()) as c, c:
            c.execute("BEGIN IMMEDIATE")
            if device not in active_devices(c):
                raise ValueError("invalid_confirmation_report")
            c.execute('''UPDATE confirmation_calls SET device_result=?
                WHERE event_id=? AND device_id=? AND confirmed_at IS NOT NULL
                AND device_result IS NULL''',
                (document['status'], document['event_id'], device))
        return True
