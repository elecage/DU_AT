"""Run as the existing Raspberry Pi administrator; no Bot Token input required."""
from __future__ import annotations
import argparse
import getpass
import os
import pwd
import subprocess
import time
import warnings
from datetime import datetime
from zoneinfo import ZoneInfo
from operator_store import OperatorStore as GuardianStore
from contextlib import closing
from device_registry import default_set
from telegram_worker import DATABASE_PATH

def main():
    if os.geteuid() != pwd.getpwnam('carecallserver').pw_uid:
        raise RuntimeError('Run as carecallserver without sudo')
    warnings.simplefilter('error', getpass.GetPassWarning)
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='action', required=True)
    invite = sub.add_parser('invite')
    invite.add_argument('--label', required=True, help='Local nickname, e.g. 보호자1')
    invite.add_argument('--minutes', type=int, default=15)
    sub.add_parser('pending')
    approve = sub.add_parser('approve')
    approve.add_argument('request_id', type=int)
    revoke = sub.add_parser('revoke')
    revoke.add_argument('request_id', type=int)
    sub.add_parser('recipients')
    disable = sub.add_parser('disable')
    disable.add_argument('recipient_id', type=int)
    args = p.parse_args()
    store = GuardianStore(DATABASE_PATH)
    if args.action == 'invite':
        request_id, link = store.invite(args.label, args.minutes)
        print(f'REQUEST_ID={request_id}')
        with closing(store.connect()) as connection:
            print('SET=' + default_set(connection))
        print(f'EXPIRES_IN_MINUTES={args.minutes}')
        print('본인 확인한 보호자에게만 아래 링크를 전달하세요. 공개 게시하지 마세요.')
        print(link)
        print('INVITE=CREATED; guardian must press Start, then administrator approves')
    elif args.action == 'pending':
        for row in store.requests():
            state = row['state']
            if state in ('issued','pending') and row['expires_at'] <= time.time():
                state = 'expired'
            expires = datetime.fromtimestamp(row['expires_at'], ZoneInfo('Asia/Seoul'))
            print(f"request={row['id']} label={row['label']} set={row['set_id']} "
                  f"state={state} expires={expires:%Y/%m/%d-%H:%M:%S}")
    elif args.action == 'approve':
        print('연결할 보호자의 신원을 별도로 확인하고, 그분의 봇 화면에 나온 6자리 숫자를 입력하세요.')
        code = getpass.getpass('보호자가 알려준 확인 숫자 (숨김): ').strip()
        store.approve(args.request_id, code)
        print('REGISTRATION=APPROVED; existing recipients retained; past calls not backfilled')
    elif args.action == 'revoke':
        store.revoke(args.request_id)
        print('INVITE=REVOKED')
    elif args.action == 'recipients':
        for row in store.recipients():
            print(f"recipient={row['recipient_id']} label={row['label']} "
                  f"set={row['set_id']} enabled={row['enabled']}")
    else:
        for name in ('carecall-telegram.service', 'carecall-registration.service'):
            state = subprocess.run(['systemctl','show',name,'-p','ActiveState','--value'],
                                   text=True, capture_output=True)
            if state.returncode or state.stdout.strip() not in ('inactive','failed'):
                raise RuntimeError('Stop both Telegram services before disabling a recipient')
        store.disable(args.recipient_id)
        print('RECIPIENT=DISABLED; queued calls cancelled for that recipient')

if __name__ == '__main__':
    try:
        main()
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
        raise SystemExit('ADMIN=STOPPED: hidden input unavailable or cancelled') from None
    except (ValueError, RuntimeError) as exc:
        raise SystemExit('ADMIN=FAILED: ' + str(exc)) from None
    except Exception as exc:
        raise SystemExit('ADMIN=FAILED: ' + type(exc).__name__) from None
