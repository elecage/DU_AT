#!/usr/bin/env python3
"""Prompt locally for ONE initial secret. No overwrite, network or service action."""
from pathlib import Path
import argparse
import getpass
import os
import re
import sys
import warnings

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from install_common import (InstallError, require, checked_directory,
                            private_directory, create_private_file)


def secret_content(kind, value):
    if kind == 'receiver-env':
        # A deliberate safe subset for a systemd EnvironmentFile, without escaping.
        require(bool(re.fullmatch(r'[A-Za-z0-9_-]{16,128}', value)),
                'USE_16_TO_128_ASCII_LETTERS_DIGITS_UNDERSCORE_HYPHEN')
        return ('CARECALL_RECEIVER_MQTT_PASSWORD=' + value + '\n').encode('ascii')
    require(kind == 'telegram-token', 'SECRET_KIND_INVALID')
    require(bool(re.fullmatch(r'[0-9]{5,20}:[A-Za-z0-9_-]{20,100}', value)),
            'BOT_TOKEN_FORMAT_INVALID')
    return (value + '\n').encode('ascii')


def write_secret(kind, value, etc=Path('/etc')):
    content = secret_content(kind, value)
    checked_directory(etc, os.geteuid())
    base = private_directory(etc / 'carecall', os.geteuid())
    target = base / 'receiver.env'
    if kind == 'telegram-token':
        target = private_directory(base / 'telegram', os.geteuid()) / 'bot-token'
    create_private_file(target, content, os.geteuid())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=('receiver-env', 'telegram-token'), required=True)
    parser.add_argument('--confirm', required=True)
    args = parser.parse_args()
    try:
        require(os.geteuid() == 0, 'RUN_WITH_SUDO')
        require(args.confirm == 'CREATE_NEW_LOCAL_SECRET', 'CONFIRMATION_INVALID')
        require(sys.stdin.isatty() and sys.stderr.isatty(), 'INTERACTIVE_TERMINAL_REQUIRED')
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            value = getpass.getpass('New local value (hidden): ')
            confirmation = getpass.getpass('Repeat value (hidden): ')
        require(value == confirmation, 'VALUES_DO_NOT_MATCH')
        write_secret(args.kind, value)
        print('INITIAL_SECRET=CREATED')
        print('SECRET_VALUE_PRINTED=NO')
        print('EXISTING_SECRET_REPLACED=NO')
        print('NETWORK_OR_SERVICES_CHANGED=NO')
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        code = str(exc) if isinstance(exc, InstallError) else type(exc).__name__
        print('INITIAL_SECRET=FAILED CODE=' + code)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
