"""Read-only status report. Excludes tokens, chat IDs and raw event payloads."""
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess

def main():
    for service in ('carecall-receiver.service', 'carecall-telegram.service'):
        result = subprocess.run(['systemctl', 'is-active', service], text=True, capture_output=True)
        print(f"{service}={result.stdout.strip() or 'unknown'}")
    path = Path(__file__).resolve().parent / 'data/carecall_events.db'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as connection:
        routes = connection.execute('SELECT count(*) FROM notification_recipients WHERE enabled=1').fetchone()[0]
        print(f'enabled_recipient_count={routes}')
        for status, count in connection.execute('SELECT status,count(*) FROM notification_outbox GROUP BY status'):
            print(f'{status}={count}')
        rows = connection.execute('''SELECT event_id,status,attempts,telegram_message_id,last_error
            FROM notification_outbox ORDER BY notification_id DESC LIMIT 10''').fetchall()
        print('=== LATEST NOTIFICATIONS (no chat IDs) ===')
        for event_id, status, attempts, message_id, error in rows:
            label = ''.join(c if c.isprintable() else ' ' for c in event_id)[:160]
            print(f'event_id={label} status={status} attempts={attempts} message_id={message_id} error={error}')
        print(f'latest_row_count={len(rows)}')

if __name__ == '__main__':
    main()
