#!/usr/bin/env python3
"""NEW Pi file/account/venv preparation. Default: read-only. No service activation.

OS packages must already be installed. This is not a complete installer:
broker authentication, DB, Telegram, Wi-Fi profiles, Netplan, firewall and
activation remain separate. Existing CareCall installations are refused.
"""
from pathlib import Path
import argparse
import grp
import hashlib
import json
import os
import platform
import pwd
import signal
import sqlite3
import stat
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from install_common import (InstallError, require, no_symlinks, checked_directory,
                            fsync_directory)
from carecall_host_inventory import os_summary, board_model

VERSION = '20261016-new-pi-prepare-1'
CONFIRM = 'PREPARE_NEW_CARECALL_PI'
MANIFEST_SHA256 = 'a1d1fcc966834a60703b8f686fd178667902b3183b3951a943bf3e1fdab9b57a'
PAHO_SHA256 = '6db9ba9b34ed5bc6b6e3812718c7e06e2fd7444540df2455d2c51bd58808feee'
APP = '/home/carecallserver/carecall_receiver'
WIFI = '/opt/carecall-wifi-manager'
RECEIPT = '/var/lib/carecall-install'
PACKAGES = ('python3-venv', 'python3-yaml', 'mosquitto', 'mosquitto-clients',
            'avahi-daemon', 'sqlite3', 'hostapd', 'dnsmasq-base', 'wpasupplicant',
            'iw', 'netplan.io', 'ufw', 'iptables', 'nftables')
PROTECTED = (APP, WIFI, '/etc/carecall', '/etc/carecall-wifi-manager', RECEIPT,
             '/etc/mosquitto/passwd', '/etc/mosquitto/acl',
             '/etc/mosquitto/conf.d/carecall.conf',
             '/opt/carecall-wifi-trial', '/etc/carecall-wifi-trial')


class Layout:
    """Testable path mapping; the CLI always uses the real root, with no override."""
    def __init__(self, root=Path('/')):
        self.root = no_symlinks(root)

    def path(self, absolute):
        require(absolute.startswith('/') and '..' not in Path(absolute).parts,
                'INVALID_DESTINATION')
        return no_symlinks(self.root / absolute.lstrip('/'))


def read_regular(path):
    path = no_symlinks(path)
    require(path.is_file() and stat.S_ISREG(path.stat().st_mode), 'SOURCE_NOT_REGULAR')
    require(path.stat().st_size <= 2 * 1024 * 1024, 'SOURCE_TOO_LARGE')
    return path.read_bytes()


def source_payload(source):
    """Snapshot only approved public files in memory; never glob local secrets."""
    source = no_symlinks(source)
    raw = read_regular(source / 'install/source_baseline.json')
    require(hashlib.sha256(raw).hexdigest() == MANIFEST_SHA256, 'SOURCE_MANIFEST_CHANGED')
    manifest = json.loads(raw)['sha256']
    require(len(manifest) == 43, 'SOURCE_COUNT_INVALID')
    payload = {}
    for relative, digest in manifest.items():
        parts = Path(relative).parts
        require(not Path(relative).is_absolute() and '..' not in parts, 'SOURCE_PATH_INVALID')
        data = read_regular(source / relative)
        require(hashlib.sha256(data).hexdigest() == digest, 'SOURCE_BASELINE_MISMATCH')
        if parts[0] == 'receiver':
            destination = APP + '/' + '/'.join(parts[1:])
        elif parts[0] == 'wifi-manager':
            destination = WIFI + '/' + '/'.join(parts[1:])
        elif parts[0] == 'systemd':
            destination = '/etc/systemd/system/' + '/'.join(parts[1:])
        else:
            require(parts[0] == 'config', 'SOURCE_SCOPE_INVALID')
            continue  # Templates must not become active configuration yet.
        payload[destination] = data
    require(len(payload) == 39, 'PAYLOAD_COUNT_INVALID')
    lock = read_regular(source / 'install/requirements.bootstrap.txt')
    lines = [line.strip() for line in lock.decode('ascii').splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    require(lines == ['paho-mqtt==2.1.0 --hash=sha256:' + PAHO_SHA256],
            'BOOTSTRAP_REQUIREMENTS_CHANGED')
    payload[RECEIPT + '/requirements.bootstrap.txt'] = lock
    return payload


def guard_existing(layout):
    for name in PROTECTED:
        require(not os.path.lexists(layout.path(name)), 'EXISTING_CARECALL_INSTALLATION')
    for parent in ('/etc/systemd/system', '/run/systemd/system', '/usr/lib/systemd/system'):
        directory = layout.path(parent)
        if directory.is_dir():
            # Includes disabled units, drop-ins, and enabled-unit links.
            require(not any(p.name.lower().startswith('carecall')
                            for p in directory.rglob('*')), 'EXISTING_CARECALL_UNIT')


class System:
    def command(self, argv, code, *, timeout=30):
        """No shell. Never print subprocess errors or credential-bearing output."""
        env = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C',
               'PIP_CONFIG_FILE': '/dev/null', 'PYTHONDONTWRITEBYTECODE': '1'}
        try:
            with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, env=env, start_new_session=True) as child:
                try:
                    out, _ = child.communicate(timeout=timeout)
                except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        child.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(child.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        child.communicate()
                    if isinstance(exc, KeyboardInterrupt):
                        raise
                    raise InstallError(code)
                require(child.returncode == 0, code)
                return out.strip()
        except OSError:
            raise InstallError(code) from None

    def check_host(self):
        require(os.geteuid() == 0, 'RUN_WITH_SUDO')
        report = os_summary(platform.freedesktop_os_release(), platform.machine(), board_model())
        require(report['TARGET_UBUNTU_24_04_5_PI5'] == 'MATCH', 'TARGET_OS_OR_BOARD_MISMATCH')
        require(sys.version_info[:2] == (3, 12) and sqlite3.sqlite_version_info >= (3, 37, 0),
                'PYTHON_OR_SQLITE_VERSION_UNSUPPORTED')
        for command in ('/usr/sbin/useradd', '/usr/sbin/runuser', '/usr/bin/python3',
                        '/usr/bin/dpkg-query', '/usr/bin/systemctl', '/usr/sbin/nologin'):
            require(os.path.isfile(command) and os.access(command, os.X_OK),
                    'REQUIRED_SYSTEM_COMMAND_MISSING')
        for subcommand in ('list-unit-files', 'list-units'):
            text = self.command(['/usr/bin/systemctl', subcommand, '--all', '--no-legend',
                                 '--no-pager', '--plain', 'carecall*'], 'SYSTEMD_QUERY_FAILED')
            require(not text, 'EXISTING_CARECALL_UNIT')

    def missing_packages(self):
        missing = []
        for package in PACKAGES:
            try:
                status = self.command(['/usr/bin/dpkg-query', '-W', '-f=${Status}', package],
                                      'PACKAGE_QUERY_FAILED')
                if status != 'install ok installed':
                    missing.append(package)
            except InstallError:
                missing.append(package)
        return missing

    def accounts(self):
        result = {}
        for name in ('carecallserver', 'carecall-wifi-web'):
            try:
                user = pwd.getpwnam(name)
            except KeyError:
                try:
                    grp.getgrnam(name)
                except KeyError:
                    result[name] = None
                    continue
                raise InstallError('ACCOUNT_GROUP_WITHOUT_USER') from None
            group = grp.getgrnam(name)
            require(user.pw_uid != 0 and user.pw_gid == group.gr_gid and group.gr_gid != 0,
                    'ACCOUNT_IDENTITY_INVALID')
            if name == 'carecallserver':
                require(user.pw_uid >= 1000 and user.pw_dir == '/home/carecallserver',
                        'SERVER_ACCOUNT_HOME_INVALID')
                checked_directory(Path(user.pw_dir), user.pw_uid)
            else:
                require(user.pw_shell in ('/usr/sbin/nologin', '/sbin/nologin', '/bin/false'),
                        'WEB_ACCOUNT_MUST_BE_NONLOGIN')
            result[name] = (user.pw_uid, user.pw_gid)
        if result['carecallserver'] is None:
            require(not os.path.lexists('/home/carecallserver'), 'UNOWNED_SERVER_HOME_EXISTS')
        return result

    def create_accounts(self, accounts):
        if accounts['carecallserver'] is None:
            self.command(['/usr/sbin/useradd', '--create-home', '--user-group',
                          '--home-dir', '/home/carecallserver', '--shell', '/bin/bash',
                          'carecallserver'], 'SERVER_ACCOUNT_CREATION_FAILED')
        if accounts['carecall-wifi-web'] is None:
            self.command(['/usr/sbin/useradd', '--system', '--user-group', '--no-create-home',
                          '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin',
                          'carecall-wifi-web'], 'WEB_ACCOUNT_CREATION_FAILED')
        return self.accounts()

    def prepare_python(self, app, lock):
        require(not os.path.lexists(app / '.venv'), 'VENV_ALREADY_EXISTS')
        prefix = ['/usr/sbin/runuser', '--user', 'carecallserver', '--']
        self.command(prefix + ['/usr/bin/python3', '-I', '-B', '-m', 'venv', '--copies',
                               str(app / '.venv')], 'VENV_CREATION_FAILED', timeout=180)
        python = str(app / '.venv/bin/python')
        self.command(prefix + [python, '-I', '-B', '-m', 'pip', '--isolated',
                     '--disable-pip-version-check', 'install', '--no-input', '--no-cache-dir',
                     '--require-virtualenv', '--require-hashes', '--only-binary=:all:',
                     '--no-deps', '--index-url', 'https://pypi.org/simple', '-r', str(lock)],
                     'PYTHON_DEPENDENCY_INSTALL_FAILED', timeout=240)
        version = self.command(prefix + [python, '-I', '-B', '-c',
                     'import importlib.metadata; import paho.mqtt.client; '
                     'print(importlib.metadata.version("paho-mqtt"))'], 'PYTHON_IMPORT_FAILED')
        require(version == '2.1.0', 'PAHO_VERSION_MISMATCH')


def new_directory(path, mode, uid, gid):
    path = no_symlinks(path)
    path.mkdir(mode=mode)  # No exist_ok, no recursive creation over an existing tree.
    os.chown(path, uid, gid, follow_symlinks=False)
    os.chmod(path, mode, follow_symlinks=False)
    fsync_directory(path)
    fsync_directory(path.parent)


def new_file(path, data, mode, uid, gid):
    path = no_symlinks(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        os.fchown(stream.fileno(), uid, gid)
        os.fchmod(stream.fileno(), mode)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_directory(path.parent)


def prepare(source, layout, system, *, apply=False, confirm=None, emit=print):
    require((apply and confirm == CONFIRM) or (not apply and confirm is None),
            'APPLY_CONFIRMATION_INVALID')
    guard_existing(layout)  # Protect the running Pi before any account/package action.
    system.check_host()
    payload = source_payload(source)
    for parent in ('/home', '/opt', '/etc', '/etc/systemd/system', '/var/lib'):
        checked_directory(layout.path(parent), 0)
    accounts = system.accounts()
    missing = system.missing_packages()
    emit('SOURCE_BASELINE=PASS')
    emit('PREPARE_PAYLOAD_FILES=' + str(len(payload)))
    emit('OS_PACKAGES_MISSING=' + (','.join(missing) if missing else 'NONE'))
    emit('PACKAGE_INSTALLATION_PERFORMED=NO')
    require(not missing, 'INSTALL_REQUIRED_OS_PACKAGES_FIRST')
    for name in accounts:
        emit('ACCOUNT_' + name.upper().replace('-', '_') + '=' +
             ('PRESENT' if accounts[name] else 'CREATE_ON_APPLY'))
    if not apply:
        emit('SYSTEM_CONFIGURATION_WRITTEN=NO')
        emit('PYPI_DOWNLOAD_REQUESTED=NO')
        return 'READY_NO_WRITE'
    # A second check catches a concurrent installer before the first mutation.
    guard_existing(layout)
    new_directory(layout.path(RECEIPT), 0o755, 0, 0)
    new_file(layout.path(RECEIPT + '/prepare-started.json'),
             json.dumps({'version': VERSION, 'phase': 'started'}).encode() + b'\n', 0o600, 0, 0)
    emit('NEW_PI_PREPARE_STEP=ACCOUNTS')
    accounts = system.create_accounts(accounts)
    uid, gid = accounts['carecallserver']
    checked_directory(layout.path('/home/carecallserver'), uid)
    for name, mode, owner, group in (
            (APP, 0o750, uid, gid), (APP + '/data', 0o700, uid, gid),
            (WIFI, 0o755, 0, 0), ('/etc/carecall', 0o700, 0, 0),
            ('/etc/carecall/telegram', 0o700, 0, 0),
            ('/etc/carecall-wifi-manager', 0o700, 0, 0),
            ('/etc/systemd/system/carecall-wifi-manager.service.d', 0o755, 0, 0)):
        new_directory(layout.path(name), mode, owner, group)
    emit('NEW_PI_PREPARE_STEP=PUBLIC_FILES')
    for name, data in payload.items():
        is_receiver = name.startswith(APP + '/')
        new_file(layout.path(name), data, 0o640 if is_receiver else 0o644,
                 uid if is_receiver else 0, gid if is_receiver else 0)
    emit('NEW_PI_PREPARE_STEP=PYTHON_ENVIRONMENT')
    emit('PYPI_DOWNLOAD_REQUESTED=YES')
    system.prepare_python(layout.path(APP), layout.path(RECEIPT + '/requirements.bootstrap.txt'))
    receipt = {'version': VERSION, 'phase': 'files_prepared',
               'source_manifest_sha256': MANIFEST_SHA256,
               'sha256': {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()},
               'services_activated': False, 'installation_complete': False}
    new_file(layout.path(RECEIPT + '/prepare-complete.json'),
             (json.dumps(receipt, indent=2) + '\n').encode(), 0o600, 0, 0)
    emit('SYSTEM_CONFIGURATION_WRITTEN=YES')
    return 'FILES_PREPARED_SERVICES_NOT_ACTIVATED'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm')
    args = parser.parse_args(argv)
    os.umask(0o077)
    print('TOOL_VERSION=' + VERSION)
    try:
        result = prepare(args.source_root, Layout(), System(), apply=args.apply, confirm=args.confirm)
        print('NEW_PI_PREPARE=' + result)
        status = 0
    except (Exception, KeyboardInterrupt) as exc:
        code = str(exc) if isinstance(exc, InstallError) else type(exc).__name__
        print('NEW_PI_PREPARE=STOPPED CODE=' + code)
        print('PARTIAL_PREPARATION_POSSIBLE=' + ('YES' if args.apply else 'NO'))
        status = 2
    print('OS_PACKAGE_UPGRADE_PERFORMED=NO')
    print('NETPLAN_OR_FIREWALL_CHANGED=NO')
    print('SERVICES_STARTED_OR_ENABLED=NO')
    print('DATABASE_OR_CREDENTIALS_CREATED=NO')
    print('INSTALLATION_COMPLETE=NOT_CERTIFIED')
    return status


if __name__ == '__main__':
    raise SystemExit(main())
