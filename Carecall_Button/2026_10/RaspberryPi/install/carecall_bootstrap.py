#!/usr/bin/env python3
"""Consolidated NEW-install configuration; never starts services or edits networking.

Default action: plan. All writes need --apply and a phase-specific confirmation.
Use only after carecall_prepare_new_pi.py completed on the NEW target Pi.
"""
from pathlib import Path
import argparse
import getpass
import grp
import hashlib
import importlib
import json
import os
import platform
import pwd
import re
import stat
import sys
import tempfile
import warnings

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import carecall_prepare_new_pi as prep
import carecall_bootstrap_values as values
from carecall_host_inventory import os_summary, board_model
from carecall_write_initial_secret import secret_content
from install_common import InstallError, require, checked_directory, no_symlinks, fsync_directory

VERSION = '20261016-initial-config-1'
STATE = prep.RECEIPT
INITIAL = '/etc/carecall/initial'
BOT_FILE = prep.APP + '/telegram_settings.py'
NETPLAN = '/etc/netplan/50-cloud-init.yaml'
DB = prep.APP + '/data/carecall_events.db'
CONFIRMS = {'configure-core': 'CREATE_NEW_CARECALL_CORE_CONFIG',
            'configure-wifi': 'CREATE_NEW_CARECALL_WIFI_CONFIG',
            'create-db': 'CREATE_NEW_CARECALL_DATABASE'}
CORE_FILES = ('/etc/mosquitto/passwd', '/etc/mosquitto/acl',
              '/etc/carecall/receiver.env', '/etc/carecall/telegram/bot-token',
              INITIAL + '/mosquitto-carecall.conf', BOT_FILE)
WIFI_FILES = ('/etc/carecall-wifi-manager/settings.json',
              '/etc/carecall-wifi-manager/profile.json')
DB_TOOLS = {'carecall_init_database.py': '4c400b80b73a32fe9c90bbf0903811fb33144119b1c850b2fd405bbd6eeb489b',
            'install_common.py': '8f1be074465273c69daffcecc1ccc942fe611caf3a8df5995b81567bb42effe3'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_file(path, uid=0, mode=None):
    path = no_symlinks(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o022
                and info.st_size <= 2 * 1024 * 1024, 'LOCAL_FILE_NOT_TRUSTED')
        if mode is not None:
            require(stat.S_IMODE(info.st_mode) == mode, 'LOCAL_FILE_MODE_INVALID')
        return stream.read(2 * 1024 * 1024 + 1)


def read_json(path):
    try:
        obj = json.loads(read_file(path, mode=0o600))
        require(isinstance(obj, dict), 'RECEIPT_INVALID')
        return obj
    except (ValueError, UnicodeError):
        raise InstallError('RECEIPT_INVALID') from None


def receipt_path(layout, phase, complete):
    require(phase in ('core', 'wifi', 'database'), 'INVALID_PHASE')
    return layout.path(STATE + '/' + phase + ('-complete.json' if complete else '-started.json'))


def new_phase(layout, phase):
    require(not any(os.path.lexists(receipt_path(layout, phase, done)) for done in (False, True)),
            'PHASE_ALREADY_STARTED_OR_COMPLETE')


def start_phase(layout, phase):
    prep.new_file(receipt_path(layout, phase, False),
                  (json.dumps({'version': VERSION, 'phase': phase, 'state': 'started'}) + '\n').encode(),
                  0o600, 0, 0)


def complete_phase(layout, phase, files):
    obj = {'version': VERSION, 'phase': phase, 'state': 'complete',
           'sha256': {name: digest(data) for name, data in files.items()}}
    prep.new_file(receipt_path(layout, phase, True), (json.dumps(obj, indent=2) + '\n').encode(),
                  0o600, 0, 0)


def phase_record(layout, phase, names):
    path = receipt_path(layout, phase, True)
    if not path.exists():
        require(not os.path.lexists(path), 'RECEIPT_NOT_TRUSTED')
        require(not receipt_path(layout, phase, False).exists(), 'PARTIAL_INITIAL_CONFIGURATION')
        return None
    obj = read_json(path)
    require(obj.get('version') == VERSION and obj.get('phase') == phase
            and obj.get('state') == 'complete' and set(obj.get('sha256', {})) == set(names),
            'RECEIPT_INVALID')
    require(all(isinstance(s, str) and re.fullmatch('[0-9a-f]{64}', s)
                for s in obj['sha256'].values()), 'RECEIPT_INVALID')
    return obj


class System(prep.System):
    def check_target(self):
        require(os.geteuid() == 0, 'RUN_WITH_SUDO')
        report = os_summary(platform.freedesktop_os_release(), platform.machine(), board_model())
        require(report['TARGET_UBUNTU_24_04_5_PI5'] == 'MATCH', 'TARGET_OS_OR_BOARD_MISMATCH')

    def identities(self):
        user = pwd.getpwnam('carecallserver')
        group = grp.getgrnam('carecallserver')
        broker = grp.getgrnam('mosquitto')
        require(user.pw_uid >= 1000 and user.pw_gid == group.gr_gid and broker.gr_gid != 0,
                'ACCOUNT_IDENTITY_INVALID')
        return user.pw_uid, user.pw_gid, broker.gr_gid

    def guard_services(self, source):
        for path in (source / 'systemd').glob('*.service'):
            active = self.command(['/usr/bin/systemctl', 'show', '--value', '-p', 'ActiveState', path.name],
                                  'SERVICE_STATE_UNAVAILABLE')
            enabled = self.command(['/usr/bin/systemctl', 'show', '--value', '-p', 'UnitFileState', path.name],
                                   'SERVICE_STATE_UNAVAILABLE')
            require(active in ('inactive', 'failed') and enabled in ('disabled', 'static'),
                    'CARECALL_SERVICE_ACTIVE_OR_ENABLED')

    def validate_network_document(self, source, profile):
        sys.path.insert(0, str(source / 'wifi-manager'))
        tx = importlib.import_module('transaction')
        require(Path(tx.__file__).parent == source / 'wifi-manager', 'MIXED_WIFI_MODULES')
        try:
            tx.document(profile)  # In-memory rendering only.
            tx.cloud_policy_check()
        except Exception:
            raise InstallError('NETPLAN_OR_CLOUD_POLICY_REQUIRES_REVIEW') from None

    def create_database(self, layout):
        helper = layout.path(STATE + '/db-tools/carecall_init_database.py')
        argv = ['/usr/sbin/runuser', '--user', 'carecallserver', '--', '/usr/bin/python3',
                '-I', '-B', str(helper), '--project', str(layout.path(prep.APP)),
                '--set-id', 'set01', '--label', 'CareCall set 01']
        for name in values.DEVICES:
            argv += ['--device', name]
        argv += ['--create', '--confirm', 'CREATE_EMPTY_CARECALL_DATABASE']
        output = self.command(argv, 'EMPTY_DATABASE_CREATION_FAILED')
        require('DATABASE_INIT=CREATED_EMPTY_DEVICES_STAGED' in output, 'EMPTY_DATABASE_RESULT_INVALID')


def verify_prepared(source, layout, system):
    source = no_symlinks(source)
    require(layout.path(STATE + '/prepare-complete.json').is_file(), 'NEW_PI_PREPARATION_REQUIRED')
    system.check_target()
    payload = prep.source_payload(source)
    receipt = read_json(layout.path(STATE + '/prepare-complete.json'))
    expected = {name: digest(data) for name, data in payload.items()}
    require(receipt.get('version') == prep.VERSION and receipt.get('phase') == 'files_prepared'
            and receipt.get('sha256') == expected, 'PREPARATION_RECEIPT_INVALID')
    checked_directory(layout.path(STATE), 0)
    uid, gid, broker_gid = system.identities()
    core = phase_record(layout, 'core', CORE_FILES)
    for name, original in payload.items():
        owner = uid if name.startswith(prep.APP + '/') else 0
        wanted = core['sha256'][name] if core and name == BOT_FILE else digest(original)
        require(digest(read_file(layout.path(name), owner)) == wanted, 'PREPARED_SOURCE_CHANGED')
    system.guard_services(source)
    require(not os.path.lexists(layout.path('/etc/carecall-wifi-manager/owner')),
            'WIFI_ALREADY_OWNED_OR_ACTIVATED')
    require(not os.path.lexists(layout.path('/etc/mosquitto/conf.d/carecall.conf')),
            'BROKER_LISTENER_ALREADY_INSTALLED')
    return uid, gid, broker_gid


def check_phase_files(layout, phase, names, uid):
    record = phase_record(layout, phase, names)
    require(record is not None, 'INITIAL_PHASE_REQUIRED_' + phase.upper())
    for name in names:
        mode = 0o640 if name in ('/etc/mosquitto/passwd', '/etc/mosquitto/acl', BOT_FILE) else 0o600
        owner = uid if name == BOT_FILE or name == DB else 0
        data = read_file(layout.path(name), owner, mode)
        require(digest(data) == record['sha256'][name], 'INITIAL_CONFIGURATION_CHANGED')
    return record


def core_preflight(source, layout, system):
    identities = verify_prepared(source, layout, system)
    new_phase(layout, 'core')
    for name in CORE_FILES:
        if name != BOT_FILE:
            require(not os.path.lexists(layout.path(name)), 'INITIAL_CONFIG_ALREADY_EXISTS')
    for suffix in ('', '-wal', '-shm', '-journal'):
        require(not os.path.lexists(layout.path(DB + suffix)), 'DATABASE_ALREADY_EXISTS')
    require(not os.path.lexists(layout.path(INITIAL)), 'INITIAL_STAGE_ALREADY_EXISTS')
    for name in ('/etc/mosquitto', '/etc/mosquitto/conf.d'):
        checked_directory(layout.path(name), 0)
    for name in ('/etc/carecall', '/etc/carecall/telegram'):
        checked_directory(layout.path(name), 0, private=True)
    directory = layout.path('/etc/mosquitto/conf.d')
    values.mqtt_main_is_plain(read_file(layout.path('/etc/mosquitto/mosquitto.conf')),
                             list(directory.glob('*.conf')))
    return identities


def hash_passwords(passwords, parent, system):
    values.validate_passwords(passwords)
    with tempfile.TemporaryDirectory(prefix='.carecall-mqtt-', dir=parent) as directory:
        path = Path(directory) / 'passwords'
        plain = ''.join(name + ':' + passwords[name] + '\n' for name in values.USERS).encode('ascii')
        prep.new_file(path, plain, 0o600, 0, 0)
        system.command(['/usr/bin/mosquitto_passwd', '-H', 'sha512-pbkdf2', '-U', str(path)],
                       'MOSQUITTO_PASSWORD_HASH_FAILED')
        data = read_file(path, mode=0o600)
        values.verify_password_file(data, passwords)
        return data


def replace_public_bot(path, original, configured, uid, gid):
    require(read_file(path, uid) == original, 'BOT_SOURCE_CHANGED')
    with tempfile.TemporaryDirectory(prefix='.carecall-bot-', dir=path.parent) as directory:
        staged = Path(directory) / 'telegram_settings.py'
        prep.new_file(staged, configured, 0o640, uid, gid)
        require(read_file(path, uid) == original, 'BOT_SOURCE_CHANGED')
        os.replace(staged, path)
        fsync_directory(path.parent)


def configure_core(source, layout, system, passwords, username, token):
    uid, gid, broker_gid = core_preflight(source, layout, system)
    values.validate_passwords(passwords)
    values.validate_bot(username, token)
    original = prep.read_regular(source / 'receiver/telegram_settings.py')
    configured = values.configured_bot_source(original, username)
    hashed = hash_passwords(passwords, layout.path(STATE), system)
    files = {'/etc/mosquitto/passwd': hashed,
             '/etc/mosquitto/acl': prep.read_regular(source / 'config/mosquitto/acl.example'),
             '/etc/carecall/receiver.env': secret_content('receiver-env', passwords['carecall_receiver']),
             '/etc/carecall/telegram/bot-token': secret_content('telegram-token', token),
             INITIAL + '/mosquitto-carecall.conf': prep.read_regular(source / 'config/mosquitto/carecall.conf.example'),
             BOT_FILE: configured}
    # The listener fragment remains private/staged until network review.
    start_phase(layout, 'core')
    prep.new_directory(layout.path(INITIAL), 0o700, 0, 0)
    for name, data in files.items():
        if name == BOT_FILE:
            replace_public_bot(layout.path(name), original, data, uid, gid)
        else:
            broker_file = name.startswith('/etc/mosquitto/')
            prep.new_file(layout.path(name), data, 0o640 if broker_file else 0o600,
                          0, broker_gid if broker_file else 0)
    complete_phase(layout, 'core', files)
    return 'CORE_CONFIGURED_LISTENER_STAGED'


def wifi_preflight(source, layout, system):
    verify_prepared(source, layout, system)
    new_phase(layout, 'wifi')
    checked_directory(layout.path('/etc/carecall-wifi-manager'), 0, private=True)
    for name in WIFI_FILES:
        require(not os.path.lexists(layout.path(name)), 'WIFI_CONFIG_ALREADY_EXISTS')
    profile = values.profile_from_netplan(read_file(layout.path(NETPLAN), mode=0o600))
    system.validate_network_document(source, profile)
    return profile


def configure_wifi(source, layout, system, ssid, password):
    profile = wifi_preflight(source, layout, system)
    config = values.ap_settings(ssid, password)
    # psk_hex is a real secret, not an anonymized password. Keep both files 0600.
    files = {WIFI_FILES[0]: (json.dumps(config, ensure_ascii=True) + '\n').encode(),
             WIFI_FILES[1]: (json.dumps(profile, ensure_ascii=True) + '\n').encode()}
    start_phase(layout, 'wifi')
    for name, data in files.items():
        prep.new_file(layout.path(name), data, 0o600, 0, 0)
    complete_phase(layout, 'wifi', files)
    return 'WIFI_CONFIGURED_NOT_ACTIVATED'


def database_preflight(source, layout, system):
    uid, gid, _ = verify_prepared(source, layout, system)
    new_phase(layout, 'database')
    check_phase_files(layout, 'core', CORE_FILES, uid)
    checked_directory(layout.path(prep.APP + '/data'), uid, private=True)
    for suffix in ('', '-wal', '-shm', '-journal'):
        require(not os.path.lexists(layout.path(DB + suffix)), 'DATABASE_OR_SIDECAR_ALREADY_EXISTS')
    require(not os.path.lexists(layout.path(STATE + '/db-tools')), 'DB_TOOLS_ALREADY_STAGED')
    helpers = {name: prep.read_regular(source / 'install' / name) for name in DB_TOOLS}
    require(all(digest(data) == DB_TOOLS[name] for name, data in helpers.items()), 'DB_HELPER_CHANGED')
    return uid, gid, helpers


def create_database(source, layout, system):
    uid, gid, helpers = database_preflight(source, layout, system)
    start_phase(layout, 'database')
    prep.new_directory(layout.path(STATE + '/db-tools'), 0o755, 0, 0)
    for name, data in helpers.items():
        prep.new_file(layout.path(STATE + '/db-tools/' + name), data, 0o644, 0, 0)
    system.create_database(layout)
    complete_phase(layout, 'database', {DB: read_file(layout.path(DB), uid, 0o600)})
    return 'EMPTY_DATABASE_CREATED_DEVICES_STAGED'


def hidden(prompt, repeat=False):
    require(sys.stdin.isatty() and sys.stderr.isatty(), 'INTERACTIVE_TERMINAL_REQUIRED')
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        value = getpass.getpass(prompt)
        if repeat:
            require(value == getpass.getpass('Repeat (hidden): '), 'VALUES_DO_NOT_MATCH')
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='plan',
                        choices=('plan','configure-core','configure-wifi','create-db','check'))
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm')
    parser.add_argument('--online', action='store_true', help='check only: read Telegram getMe/getWebhookInfo')
    args = parser.parse_args(argv)
    os.umask(0o077)
    print('TOOL_VERSION=' + VERSION)
    try:
        require(os.geteuid() == 0, 'RUN_WITH_SUDO')
        require(not args.online or args.action == 'check', 'ONLINE_CHECK_ACTION_REQUIRED')
        if args.action in CONFIRMS:
            require((args.apply and args.confirm == CONFIRMS[args.action]) or
                    (not args.apply and args.confirm is None), 'APPLY_CONFIRMATION_INVALID')
        else:
            require(not args.apply and args.confirm is None, 'READ_ONLY_ACTION')
        source, layout, system = no_symlinks(args.source_root), prep.Layout(), System()
        if args.action == 'plan':
            prep.source_payload(source)
            print('SOURCE_BASELINE=PASS')
            print('OS_PACKAGES=' + ','.join(prep.PACKAGES))
            print('NEW_PI_ORDER=packages,prepare,configure-core,configure-wifi,create-db,check,network-review')
            print('BOOTSTRAP=PLAN_ONLY')
        elif args.action == 'check':
            from carecall_bootstrap_check import check
            return check(source, layout, system, online=args.online)
        else:
            preflights = {'configure-core': core_preflight, 'configure-wifi': wifi_preflight,
                          'create-db': database_preflight}
            preflights[args.action](source, layout, system)
            if not args.apply:
                print('BOOTSTRAP=READY_NO_WRITE')
            elif args.action == 'configure-core':
                passwords = {name: hidden(name + ' MQTT password (hidden): ', True) for name in values.USERS}
                username = hidden('New bot username without @ (hidden): ')
                token = hidden('New Telegram bot token (hidden): ', True)
                print('BOOTSTRAP=' + configure_core(source, layout, system, passwords, username, token))
            elif args.action == 'configure-wifi':
                ssid = hidden('Pi AP SSID CareCall-Pi-... (hidden): ')
                password = hidden('Pi AP fixed password (hidden): ', True)
                print('BOOTSTRAP=' + configure_wifi(source, layout, system, ssid, password))
            else:
                print('BOOTSTRAP=' + create_database(source, layout, system))
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        code = str(exc) if isinstance(exc, InstallError) else type(exc).__name__
        print('BOOTSTRAP=STOPPED CODE=' + code)
        print('PARTIAL_CONFIGURATION_POSSIBLE=' + ('YES' if args.apply else 'NO'))
        return 2
    finally:
        print('CREDENTIAL_VALUES_PRINTED=NO')
        print('SERVICES_STARTED_OR_ENABLED=NO')
        print('NETPLAN_OR_FIREWALL_CHANGED=NO')
        print('ACTIVE_MQTT_LISTENER_INSTALLED=NO')
        print('INSTALLATION_COMPLETE=NOT_CERTIFIED')


if __name__ == '__main__':
    raise SystemExit(main())
