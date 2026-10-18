"""Initial local-admin enrollment. No token or chat ID in argv, env, or logs."""
from __future__ import annotations
import getpass
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import warnings

from telegram_api import TelegramClient, TelegramError

PROJECT = Path('/home/carecallserver/carecall_receiver')
SECRET_DIR = Path('/etc/carecall/telegram')
SECRET_FILE = SECRET_DIR / 'bot-token'

def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError('Credential directory must be root-owned mode 700; inspect before changing')

def create_secret(path, token):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        handle.write(token + '\n')
        handle.flush()
        os.fsync(handle.fileno())

def run_as_receiver(code, data=None):
    # Input uses a pipe; never place credentials or recipient IDs on the command line.
    result = subprocess.run(
        ['runuser', '-u', 'carecallserver', '--', str(PROJECT / '.venv/bin/python'),
         '-B', '-c', code], cwd=PROJECT, input=data, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Database enrollment failed; source exception is intentionally not printed')
    return result.stdout.strip()

def rotate_token():
    private_directory(SECRET_DIR)
    info = SECRET_FILE.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError('Existing credential must be a root-owned regular file, mode 600')
    token = getpass.getpass('재발급받은 Bot Token (숨김): ').strip()
    TelegramClient(token).verify_bot()
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'carecall-telegram.service']).returncode == 0
    enabled = subprocess.run(['systemctl', 'is-enabled', '--quiet', 'carecall-telegram.service']).returncode == 0
    subprocess.run(['systemctl', 'stop', 'carecall-telegram.service'], check=True)
    previous = SECRET_DIR / ('bot-token.previous-' + os.urandom(4).hex())
    create_secret(previous, SECRET_FILE.read_text().strip())
    temporary = SECRET_DIR / ('bot-token.new-' + os.urandom(4).hex())
    create_secret(temporary, token)
    os.replace(temporary, SECRET_FILE)
    print('TOKEN_ROTATION=SUCCESS; recipient mapping preserved')
    if active or enabled:
        subprocess.run(['systemctl', 'reset-failed', 'carecall-telegram.service'], check=True)
        subprocess.run(['systemctl', 'start', 'carecall-telegram.service'], check=True)

def main():
    if os.geteuid() != 0:
        raise RuntimeError('Run this configuration tool with sudo')
    warnings.simplefilter('error', getpass.GetPassWarning)
    if sys.argv[1:] == ['--rotate-token']:
        rotate_token()
        return
    if sys.argv[1:]:
        raise RuntimeError('Only --rotate-token is supported as an optional argument')
    if SECRET_FILE.exists() or SECRET_FILE.is_symlink():
        raise RuntimeError('Token file already exists; initial enrollment will not overwrite it')
    if run_as_receiver("from notification_store import NotificationStore; "
                       "from contextlib import closing; "
                       "s=NotificationStore('data/carecall_events.db'); "
                       "c=s.connect(); print(c.execute('SELECT count(*) FROM notification_recipients').fetchone()[0]); c.close()") != '0':
        raise RuntimeError('Recipient already exists; initial enrollment cannot replace it')
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'carecall-telegram.service'])
    if active.returncode == 0:
        raise RuntimeError('Sender is already active; this tool is for initial enrollment')
    print('초기 연구 테스트용: 앞서 본인 휴대폰에서 확인한 TEST_CHAT_ID를 사용하세요.')
    token = getpass.getpass('Bot Token (숨김): ').strip()
    chat_text = getpass.getpass('앞서 확인한 TEST_CHAT_ID 숫자 (숨김): ').strip()
    if not chat_text.isascii() or not chat_text.isdigit() or not 0 < int(chat_text) < 2**52:
        raise RuntimeError('Invalid private chat ID')
    chat_id = int(chat_text)
    client = TelegramClient(token)
    client.verify_bot()
    client.verify_private_chat(chat_id)
    print('BOT_AND_PRIVATE_CHAT_CHECK=SUCCESS')
    private_directory(SECRET_DIR)
    create_secret(SECRET_FILE, token)
    try:
        run_as_receiver(
            "import json,sys; from notification_store import NotificationStore; "
            "NotificationStore('data/carecall_events.db').register_first_recipient(json.load(sys.stdin)['chat_id'])",
            json.dumps({'chat_id': chat_id}))
    except BaseException:
        # Preserve the sensitive file for diagnosis, never leave a usable partial config.
        failed = SECRET_DIR / ('bot-token.incomplete-' + os.urandom(4).hex())
        os.rename(SECRET_FILE, failed)
        raise
    print('CREDENTIAL_FILE=CONFIGURED_ROOT_600')
    print('RECIPIENT_MAPPING=default_set_to_verified_test_account')
    print('PAST_EVENTS_BACKFILLED=0')
    print('CONFIGURE=SUCCESS')
    print('다음 명령: sudo systemctl enable --now carecall-telegram.service')

if __name__ == '__main__':
    try:
        main()
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        raise SystemExit('CONFIGURE=STOPPED: hidden input unavailable or cancelled') from None
    except TelegramError as exc:
        raise SystemExit('CONFIGURE=FAILED: ' + exc.code) from None
    except RuntimeError as exc:
        raise SystemExit('CONFIGURE=FAILED: ' + str(exc)) from None
    except Exception as exc:
        raise SystemExit('CONFIGURE=FAILED: ' + type(exc).__name__) from None
