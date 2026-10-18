"""Local-admin invitations. A deep link creates a request, never a recipient."""
from __future__ import annotations
from telegram_settings import get_bot_username
from contextlib import closing, contextmanager
import hashlib
import hmac
import json
import re
import secrets
import time
from notification_store import NotificationStore, utc_now
from device_registry import default_set

SCHEMA = """
CREATE TABLE IF NOT EXISTS guardian_invites (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 token_hash TEXT UNIQUE NOT NULL,
 set_id TEXT NOT NULL REFERENCES carecall_sets(set_id),
 label TEXT NOT NULL,
 created_at REAL NOT NULL,
 expires_at REAL NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('issued','pending','approved','revoked')),
 chat_id INTEGER,
 check_code TEXT,
 approved_at REAL
) STRICT;
CREATE TABLE IF NOT EXISTS guardian_cursor (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 last_update_id INTEGER NOT NULL,
 last_update_at REAL NOT NULL DEFAULT 0
) STRICT;
INSERT OR IGNORE INTO guardian_cursor(singleton,last_update_id) VALUES(1,-1);
CREATE TABLE IF NOT EXISTS guardian_replies (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 dedupe_key TEXT UNIQUE NOT NULL,
 invite_id INTEGER REFERENCES guardian_invites(id),
 chat_id INTEGER NOT NULL,
 text TEXT NOT NULL,
 created_at REAL NOT NULL,
 expires_at REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending'
   CHECK(status IN ('pending','sending','sent','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0,
 next_at REAL NOT NULL DEFAULT 0,
 message_id INTEGER,
 error_code TEXT
) STRICT;
CREATE INDEX IF NOT EXISTS guardian_replies_due ON guardian_replies(status,next_at,id);
CREATE INDEX IF NOT EXISTS guardian_replies_chat ON guardian_replies(chat_id,created_at);
CREATE TABLE IF NOT EXISTS guardian_audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 action TEXT NOT NULL,
 invite_id INTEGER,
 recipient_id INTEGER,
 created_at TEXT NOT NULL
) STRICT;
"""

GUIDE = '호출 알림을 받으려면 담당자에게 등록 링크를 요청해주세요.'

def valid_chat(value):
    return type(value) is int and 0 < value < 2**52

class GuardianStore(NotificationStore):
    @contextmanager
    def transaction(self, connection=None):
        if connection is not None:
            yield connection
        else:
            with closing(self.connect()) as c, c:
                c.execute('BEGIN IMMEDIATE')
                yield c

    def migrate(self):
        with closing(self.connect()) as c:
            c.executescript('BEGIN IMMEDIATE;\n' + SCHEMA + '\nCOMMIT;')

    def invite(self, label, minutes=15, now=None, *, connection=None):
        bot_username = get_bot_username()
        now = time.time() if now is None else now
        if not label or len(label) > 40 or any(not x.isprintable() for x in label):
            raise ValueError('Use a printable recipient label of 1-40 characters')
        if type(minutes) is not int or not 1 <= minutes <= 60:
            raise ValueError('Expiry must be 1-60 minutes')
        token = secrets.token_urlsafe(32)
        with self.transaction(connection) as c:
            row = c.execute('''INSERT INTO guardian_invites
                (token_hash,set_id,label,created_at,expires_at,state)
                VALUES(?,?,?,?,?,'issued')''',
                (hashlib.sha256(token.encode()).hexdigest(), default_set(c), label, now, now+minutes*60))
            request_id = row.lastrowid
            c.execute("INSERT INTO guardian_audit(action,invite_id,created_at) VALUES('issued',?,?)",
                      (request_id, utc_now()))
        return request_id, f'https://t.me/{bot_username}?start=join_' + token

    def cursor(self, now=None):
        now = time.time() if now is None else now
        with closing(self.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM guardian_cursor WHERE singleton=1').fetchone()
            # After >=1 week without updates Telegram can randomize update_id.
            # Retention is <=24h: reset a six-day idle cursor before that happens.
            if now - row['last_update_at'] >= 6*86400:
                c.execute('UPDATE guardian_cursor SET last_update_id=-1,last_update_at=? WHERE singleton=1', (now,))
                return -1
            return row['last_update_id']

    @staticmethod
    def _reply(c, key, chat_id, text, now, invite_id=None, expiry=None):
        c.execute('''INSERT OR IGNORE INTO guardian_replies
            (dedupe_key,invite_id,chat_id,text,created_at,expires_at)
            VALUES(?,?,?,?,?,?)''', (key,invite_id,chat_id,text,now,expiry or now+3600))

    def apply_update(self, update, now=None, *, connection=None):
        """Commit request/reply and update cursor together, then acknowledge by offset."""
        now = time.time() if now is None else now
        update_id = update.get('update_id')
        if type(update_id) is not int or update_id < 0:
            raise ValueError('invalid_update_id')
        with self.transaction(connection) as c:
            last = c.execute('SELECT last_update_id FROM guardian_cursor WHERE singleton=1').fetchone()[0]
            if update_id <= last:
                return 'replayed'
            message = update.get('message') or {}
            chat, sender = message.get('chat') or {}, message.get('from') or {}
            chat_id, text = chat.get('id'), message.get('text', '')
            outcome = 'ignored'
            if (chat.get('type') == 'private' and valid_chat(chat_id)
                    and sender.get('id') == chat_id and sender.get('is_bot') is False
                    and isinstance(text, str) and len(text) <= 150):
                match = re.fullmatch(r'/start(?:@' + re.escape(get_bot_username()) + r')?(?:\s+(\S+))?', text.strip())
                if match and (not match.group(1) or match.group(1).startswith('join_')):
                    payload = match.group(1) or ''
                    token_match = re.fullmatch(r'join_([A-Za-z0-9_-]{43})', payload)
                    row = None
                    if token_match:
                        digest = hashlib.sha256(token_match.group(1).encode()).hexdigest()
                        row = c.execute('SELECT * FROM guardian_invites WHERE token_hash=?', (digest,)).fetchone()
                    reply = GUIDE
                    invite_id, expiry = None, None
                    outcome = 'uninvited'
                    if row and row['state'] == 'approved' and row['chat_id'] == chat_id:
                        active = c.execute('''SELECT enabled FROM notification_recipients
                            WHERE set_id=? AND chat_id=?''', (row['set_id'],chat_id)).fetchone()
                        reply = '호출 알림 등록이 완료된 계정입니다.' if active and active[0] else GUIDE
                        outcome = 'already_approved'
                    elif (row and row['expires_at'] > now and row['state'] in ('issued','pending')
                            and (row['chat_id'] is None or row['chat_id'] == chat_id)):
                        code = row['check_code'] or f'{secrets.randbelow(1000000):06d}'
                        c.execute("UPDATE guardian_invites SET state='pending',chat_id=?,check_code=? WHERE id=?",
                                  (chat_id, code, row['id']))
                        invite_id, expiry = row['id'], row['expires_at']
                        reply = ('호출 알림 등록을 요청했습니다.\n'
                                 f'확인 숫자: {code}\n'
                                 '등록을 도와주는 담당자에게 이 숫자를 알려주세요.\n'
                                 '담당자가 확인하면 등록 완료 메시지를 보내드립니다.')
                        outcome = 'pending'
                    elif row:
                        reply = '사용할 수 없거나 만료된 등록 링크입니다. 담당자에게 새 링크를 요청해주세요.'
                        outcome = 'invalid_invite'
                    elif not payload:
                        active = c.execute('SELECT 1 FROM notification_recipients WHERE chat_id=? AND enabled=1',
                                           (chat_id,)).fetchone()
                        if active:
                            reply = '호출 알림 등록이 완료된 계정입니다.'
                    # Avoid flooding from repeated /start commands. No token/chat IDs in logs.
                    recent = c.execute('SELECT 1 FROM guardian_replies WHERE chat_id=? AND created_at>?',
                                       (chat_id, now-10)).fetchone()
                    if not recent:
                        digest = hashlib.sha256(json.dumps(update,sort_keys=True).encode()).hexdigest()
                        self._reply(c, f'update:{update_id}:{digest}', chat_id, reply, now, invite_id, expiry)
            c.execute('UPDATE guardian_cursor SET last_update_id=?,last_update_at=? WHERE singleton=1', (update_id,now))
            return outcome

    def approve(self, request_id, code, now=None, *, connection=None):
        now = time.time() if now is None else now
        with self.transaction(connection) as c:
            row = c.execute('SELECT * FROM guardian_invites WHERE id=?', (request_id,)).fetchone()
            if not row or row['state'] != 'pending' or row['expires_at'] <= now:
                raise ValueError('Request is not pending or has expired')
            if not re.fullmatch(r'[0-9]{6}', code) or not hmac.compare_digest(row['check_code'], code):
                raise ValueError('Confirmation digits do not match; no recipient was changed')
            c.execute('''INSERT INTO notification_recipients(set_id,chat_id,enabled,created_at)
                VALUES(?,?,1,?) ON CONFLICT(set_id,chat_id) DO UPDATE SET enabled=1''',
                (row['set_id'],row['chat_id'],utc_now()))
            c.execute("UPDATE guardian_invites SET state='approved',approved_at=? WHERE id=?", (now,request_id))
            c.execute("UPDATE guardian_replies SET status='cancelled' WHERE invite_id=? AND status='pending'",
                      (request_id,))
            self._reply(c, f'approved:{request_id}', row['chat_id'],
                        '호출 알림 등록이 완료되었습니다.\n도움 요청이 접수되면 이 대화방으로 알려드립니다.',
                        now, request_id)
            c.execute("INSERT INTO guardian_audit(action,invite_id,created_at) VALUES('approved',?,?)",
                      (request_id,utc_now()))

    def revoke(self, request_id):
        with closing(self.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM guardian_invites WHERE id=?', (request_id,)).fetchone()
            if not row or row['state'] not in ('issued','pending'):
                raise ValueError('Only an unapproved invitation can be revoked')
            c.execute("UPDATE guardian_invites SET state='revoked' WHERE id=?", (request_id,))
            c.execute("UPDATE guardian_replies SET status='cancelled' WHERE invite_id=? AND status='pending'", (request_id,))
            c.execute("INSERT INTO guardian_audit(action,invite_id,created_at) VALUES('revoked',?,?)", (request_id,utc_now()))

    def requests(self):
        with closing(self.connect()) as c:
            return [dict(r) for r in c.execute('''SELECT id,set_id,label,state,expires_at
                FROM guardian_invites ORDER BY id DESC LIMIT 30''')]

    def recipients(self):
        with closing(self.connect()) as c:
            return [dict(r) for r in c.execute('''SELECT r.rowid AS recipient_id,r.set_id,r.enabled,
                COALESCE((SELECT g.label FROM guardian_invites g WHERE g.chat_id=r.chat_id
                  AND g.set_id=r.set_id AND g.state='approved' ORDER BY g.id DESC LIMIT 1),
                  '기존 수신자') AS label FROM notification_recipients r ORDER BY r.rowid''')]

    def disable(self, recipient_id):
        """Caller must stop both Telegram services to avoid an in-flight send/approval notice."""
        with closing(self.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM notification_recipients WHERE rowid=?', (recipient_id,)).fetchone()
            if not row:
                raise ValueError('Recipient number does not exist')
            c.execute('UPDATE notification_recipients SET enabled=0 WHERE rowid=?', (recipient_id,))
            c.execute("""UPDATE notification_outbox SET status='cancelled',last_error='recipient_disabled'
                WHERE set_id=? AND chat_id=? AND status IN ('pending','sending')""", (row['set_id'],row['chat_id']))
            c.execute("UPDATE guardian_replies SET status='cancelled' WHERE chat_id=? AND status IN ('pending','sending')", (row['chat_id'],))
            # Old, pending invitations must not reactivate this recipient after removal.
            c.execute("UPDATE guardian_invites SET state='revoked' WHERE chat_id=? AND state IN ('issued','pending')", (row['chat_id'],))
            c.execute("INSERT INTO guardian_audit(action,recipient_id,created_at) VALUES('disabled',?,?)", (recipient_id,utc_now()))

    def recover_replies(self):
        with closing(self.connect()) as c:
            c.execute("UPDATE guardian_replies SET status='pending',error_code='restart_ambiguous' WHERE status='sending'")

    def claim_reply(self, now=None):
        now = time.time() if now is None else now
        with closing(self.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            cooldown = c.execute("SELECT max(next_at) FROM guardian_replies WHERE error_code='http_429'").fetchone()[0]
            if cooldown and cooldown > now:
                return None
            c.execute("UPDATE guardian_replies SET status='cancelled' WHERE status='pending' AND expires_at<=?", (now,))
            row = c.execute("SELECT * FROM guardian_replies WHERE status='pending' AND next_at<=? ORDER BY id LIMIT 1", (now,)).fetchone()
            if not row:
                return None
            c.execute("UPDATE guardian_replies SET status='sending',attempts=attempts+1 WHERE id=?", (row['id'],))
            result = dict(row)
            result['attempts'] += 1
            return result

    def finish_reply(self, reply_id, *, message_id=None, error=None, retry_at=None):
        with closing(self.connect()) as c:
            c.execute('''UPDATE guardian_replies SET status=?,message_id=?,error_code=?,next_at=?
                WHERE id=? AND status='sending' ''',
                ('sent' if message_id is not None else ('pending' if retry_at is not None else 'failed'),
                 message_id,error,retry_at or 0,reply_id))
