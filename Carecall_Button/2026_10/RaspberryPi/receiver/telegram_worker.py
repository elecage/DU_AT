"""One durable Telegram sender per database. No MQTT callbacks or getUpdates."""
from __future__ import annotations
from telegram_settings import get_bot_username

from contextlib import contextmanager
from datetime import datetime
import fcntl
import logging
import os
from pathlib import Path
import signal
import threading
import time
from zoneinfo import ZoneInfo

from delivery_gate import delivery_gate
from notification_store import NotificationStore
from confirmation_store import ConfirmationStore
from telegram_api import TelegramClient, TelegramError

LOGGER = logging.getLogger('carecall_telegram')
DATABASE_PATH = Path(__file__).resolve().parent / 'data/carecall_events.db'

@contextmanager
def worker_lock(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('worker_already_running') from None
        yield
    finally:
        os.close(fd)

def safe_label(value, length=160):
    return ''.join(c if c.isprintable() else ' ' for c in str(value))[:length]

def render_message(job):
    stamp = datetime.fromisoformat(job['received_at']).astimezone(ZoneInfo('Asia/Seoul'))
    return (
        '[사용자가 보호자를 호출]\n'
        f"호출 시간 : {stamp:%Y/%m/%d %H:%M:%S}\n"
        '호출 메시지 : 보호자의 도움을 요청하고 있습니다. 확인해주시길 바랍니다.'
    )

def retry_delay(attempts, retry_after=0):
    return max(min(300, 5 * 2**min(max(attempts - 1, 0), 6)), retry_after)

def process_one(store, client, now=None):
    with delivery_gate(store.path):
        return _process_one_locked(store, client, now)

def _process_one_locked(store, client, now=None):
    confirmation = ConfirmationStore(store.path)
    markup_job = confirmation.next_markup(now)
    if markup_job is not None:
        markup = {'inline_keyboard': []}
        if markup_job['desired'] == 'button':
            markup = {'inline_keyboard': [[{'text': '확인했습니다.',
                'callback_data': 'c:' + markup_job['token']}]]}
        confirmation.begin_markup(markup_job)
        try:
            client.edit_markup(markup_job['chat_id'], markup_job['telegram_message_id'], markup)
        except TelegramError as exc:
            if exc.code == 'message_not_modified':
                confirmation.finish_markup(markup_job)
            elif exc.retryable or exc.fatal:
                delay = max(300 if exc.fatal else 0,
                            retry_delay(markup_job['attempts'] + 1, exc.retry_after))
                confirmation.finish_markup(markup_job, retry_at=time.time() + delay)
                LOGGER.warning('Call button update deferred job=%s code=%s',
                               markup_job['notification_id'], exc.code)
                if exc.fatal:
                    raise
                return max(1.1, delay) if exc.code == 'http_429' else 1.1
            else:
                confirmation.finish_markup(markup_job, gone=True)
                LOGGER.warning('Call button unavailable job=%s code=%s',
                               markup_job['notification_id'], exc.code)
        else:
            confirmation.finish_markup(markup_job)
        return 1.1
    job = store.claim(now)
    if job is None:
        return 1.0
    label = safe_label(job['event_id'])
    try:
        message_id = client.send(job['chat_id'], render_message(job))
    except TelegramError as exc:
        if exc.retryable or exc.fatal:
            delay = max(300 if exc.fatal else 0, retry_delay(job['attempts'], exc.retry_after))
            # Schedule from response/failure time, never from the earlier claim time.
            store.finish(job['notification_id'], error=exc.code, retry_at=time.time() + delay)
            LOGGER.warning('Telegram retry scheduled event_id=%s job=%s attempts=%s code=%s wait=%s',
                           label, job['notification_id'], job['attempts'], exc.code, delay)
            if exc.fatal:
                raise
            # Global cooldown: respect 429 for all recipients.
            return max(1.1, delay) if exc.code == 'http_429' else 1.1
        store.finish(job['notification_id'], error=exc.code)
        LOGGER.error('Telegram permanent failure event_id=%s job=%s code=%s',
                     label, job['notification_id'], exc.code)
        return 1.1
    store.finish(job['notification_id'], message_id=message_id)
    LOGGER.info('Telegram sent event_id=%s job=%s message_id=%s attempts=%s',
                label, job['notification_id'], message_id, job['attempts'])
    return 1.1

def read_credential():
    directory = os.environ.get('CREDENTIALS_DIRECTORY')
    if not directory:
        raise TelegramError('systemd_credential_missing', fatal=True)
    try:
        token = (Path(directory) / 'telegram_bot_token').read_text().strip()
    except OSError:
        raise TelegramError('credential_unreadable', fatal=True) from None
    return token

def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    stopped = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopped.set())
    try:
        client = TelegramClient(read_credential())
        store = NotificationStore(DATABASE_PATH)
        with worker_lock(DATABASE_PATH.parent / '.telegram-worker.lock'):
            while not stopped.is_set():
                try:
                    client.verify_bot()
                    break
                except TelegramError as exc:
                    if not exc.retryable:
                        raise
                    LOGGER.warning('Telegram startup retry code=%s', exc.code)
                    stopped.wait(max(5, exc.retry_after))
            if stopped.is_set():
                return 0
            store.recover_interrupted()
            LOGGER.info('Telegram worker ready bot=@%s', get_bot_username())
            while not stopped.is_set():
                stopped.wait(process_one(store, client))
        return 0
    except TelegramError as exc:
        LOGGER.error('Telegram worker stopped code=%s', exc.code)
        return 2 if exc.fatal else 1
    except Exception as exc:
        # Do not log exception text/tracebacks: HTTP request URLs contain the token.
        LOGGER.error('Telegram worker stopped error_type=%s', type(exc).__name__)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
