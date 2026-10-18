"""One-time operator binding from the trusted Raspberry Pi account. No token input."""
from telegram_settings import get_bot_username
import argparse
from contextlib import closing
import getpass
import os
from pathlib import Path
import pwd
import warnings
from operator_store import OperatorStore

PROJECT = Path('/home/carecallserver/carecall_receiver')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('setup', 'confirm', 'status', 'reset'))
    args = parser.parse_args()
    if os.geteuid() != pwd.getpwnam('carecallserver').pw_uid:
        raise RuntimeError('Run as carecallserver without sudo')
    store = OperatorStore(PROJECT / 'data/carecall_events.db')
    if args.action == 'setup':
        link = store.begin_bootstrap()
        print('본인이 운영자로 사용할 Telegram 계정에서 아래 링크를 열고 시작하세요.')
        print('15분 동안 유효합니다. 운영자 등록용 링크이므로 다른 사람에게 전달하지 마세요.')
        print(link)
        print('휴대폰에 확인 숫자가 오면 이 터미널에서 operator_admin.py confirm 을 실행하세요.')
        print('OPERATOR_SETUP=WAITING_FOR_CONFIRMATION')
    elif args.action == 'confirm':
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            code = getpass.getpass('본인 휴대폰의 CareCall 운영자 등록 확인 숫자 6자리: ').strip()
        store.confirm_bootstrap(code)
        print('OPERATOR_REGISTRATION=SUCCESS')
        print(f'운영자 휴대폰에서 @{get_bot_username()} 에 /admin 을 보내세요.')
    elif args.action == 'status':
        with closing(store.connect()) as c:
            print('operator_configured=' + str(store._operator(c) is not None).lower())
            print('enabled_recipient_count=' + str(c.execute(
                'SELECT count(*) FROM notification_recipients WHERE enabled=1').fetchone()[0]))
            print('pending_request_count=' + str(c.execute(
                "SELECT count(*) FROM guardian_invites WHERE state='pending' AND expires_at>unixepoch()").fetchone()[0]))
    elif args.action == 'reset':
        if input('운영자 권한을 제거하려면 RESET OPERATOR 를 입력하세요: ') != 'RESET OPERATOR':
            raise RuntimeError('Reset cancelled')
        store.reset_operator()
        print('OPERATOR_RESET=SUCCESS; guardian subscriptions retained')


if __name__ == '__main__':
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        raise SystemExit('OPERATOR_ADMIN=CANCELLED') from None
    except Exception as exc:
        # Errors are controlled local diagnostics, never Telegram HTTP payloads.
        raise SystemExit('OPERATOR_ADMIN=FAILED: ' + str(exc)) from None
