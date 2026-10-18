"""Durable Telegram outbox. Network I/O must never run in a DB transaction."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time
from device_registry import default_set, device_set

OUTBOX_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS notification_recipients (
    set_id TEXT NOT NULL REFERENCES carecall_sets(set_id),
    chat_id INTEGER NOT NULL CHECK (chat_id > 0),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    PRIMARY KEY (set_id, chat_id)
) STRICT;
CREATE TABLE IF NOT EXISTS notification_outbox (
    notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL REFERENCES call_events(event_id),
    device_id TEXT NOT NULL,
    set_id TEXT NOT NULL REFERENCES carecall_sets(set_id),
    chat_id INTEGER NOT NULL CHECK (chat_id > 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'cancelled')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_at TEXT NOT NULL,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    last_attempt_at TEXT,
    sent_at TEXT,
    telegram_message_id INTEGER,
    last_error TEXT,
    UNIQUE (event_id, chat_id),
    FOREIGN KEY (set_id, chat_id)
        REFERENCES notification_recipients(set_id, chat_id)
) STRICT;
CREATE INDEX IF NOT EXISTS idx_notification_due
ON notification_outbox(status, next_attempt_at, notification_id);
"""

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

def enqueue_notifications(connection, event_id, device_id, timestamp):
    """Caller owns BEGIN/COMMIT. A failure must roll back the call event too."""
    connection.execute("""
        INSERT INTO notification_outbox(event_id, device_id, set_id, chat_id, created_at)
        SELECT ?, ?, set_id, chat_id, ? FROM notification_recipients
        WHERE set_id = ? AND enabled = 1
    """, (event_id, device_id, timestamp, device_set(connection, device_id)))

class NotificationStore:
    def __init__(self, database_path):
        self.path = Path(database_path).resolve()
        if not self.path.is_file():
            raise RuntimeError("Existing call database is required")

    def connect(self):
        # mode=rw prevents accidental creation of an empty production database.
        connection = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True,
                                     timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def register_first_recipient(self, chat_id: int):
        if type(chat_id) is not int or not 0 < chat_id < 2**52:
            raise ValueError("Invalid private chat ID")
        with closing(self.connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if connection.execute("SELECT count(*) FROM notification_recipients").fetchone()[0]:
                    raise RuntimeError("Recipient already configured; initial setup cannot overwrite it")
                connection.execute("""INSERT INTO notification_recipients
                    (set_id,chat_id,enabled,created_at) VALUES (?,?,1,?)""",
                    (default_set(connection), chat_id, utc_now()))

    def recover_interrupted(self):
        """Only call while holding the exclusive worker file lock.

        A preceding send might have succeeded. Retrying favors eventual delivery
        over exactly-once delivery; Telegram sendMessage has no idempotency key.
        """
        with closing(self.connect()) as connection:
            connection.execute("""UPDATE notification_outbox SET status='pending',
                last_error='restart_ambiguous' WHERE status='sending'""")

    def claim(self, now=None):
        now = time.time() if now is None else now
        with closing(self.connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                cooldown = connection.execute("""SELECT MAX(next_attempt_at)
                    FROM notification_outbox WHERE last_error='http_429'""").fetchone()[0]
                if cooldown is not None and cooldown > now:
                    return None
                connection.execute("""UPDATE notification_outbox SET status='cancelled',
                    last_error='recipient_disabled' WHERE status='pending' AND NOT EXISTS
                    (SELECT 1 FROM notification_recipients r WHERE
                     r.set_id=notification_outbox.set_id AND
                     r.chat_id=notification_outbox.chat_id AND r.enabled=1)""")
                row = connection.execute("""SELECT o.*,e.received_at
                    FROM notification_outbox o JOIN call_events e USING(event_id)
                    WHERE o.status='pending' AND o.next_attempt_at <= ?
                    ORDER BY o.next_attempt_at,o.notification_id LIMIT 1""", (now,)).fetchone()
                if row is None:
                    return None
                connection.execute("""UPDATE notification_outbox SET status='sending',
                    attempts=attempts+1,last_attempt_at=? WHERE notification_id=?""",
                    (utc_now(), row['notification_id']))
                job = dict(row)
                job['attempts'] += 1
                return job

    def finish(self, job_id, *, message_id=None, error=None, retry_at=None):
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            if message_id is not None:
                cursor = connection.execute("""UPDATE notification_outbox SET status='sent',
                    sent_at=?,telegram_message_id=?,last_error=NULL
                    WHERE notification_id=? AND status='sending'""",
                    (utc_now(), message_id, job_id))
            else:
                cursor = connection.execute("""UPDATE notification_outbox SET status=?,
                    last_error=?,next_attempt_at=?
                    WHERE notification_id=? AND status='sending'""",
                    ('pending' if retry_at is not None else 'failed', error,
                     retry_at or 0, job_id))
            if cursor.rowcount != 1:
                raise RuntimeError("Outbox state changed unexpectedly")
            if message_id is not None:
                from confirmation_store import register_message
                register_message(connection, job_id)
