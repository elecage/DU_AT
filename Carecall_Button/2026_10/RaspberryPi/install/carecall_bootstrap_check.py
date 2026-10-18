"""Read-only checks for the prepared NEW Pi. Never approves arbitrary firewalls."""
from contextlib import closing
from pathlib import Path
import importlib
import json
import re
import socket
import sqlite3
import sys

import carecall_bootstrap as boot
import carecall_bootstrap_values as values
import carecall_prepare_new_pi as prep
from carecall_host_inventory import wifi_capability
from install_common import InstallError, require


def empty_database(path):
    for suffix in ('-wal', '-shm', '-journal'):
        require(not Path(str(path) + suffix).exists(), 'INITIAL_DATABASE_HAS_SIDECAR')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'DATABASE_INTEGRITY_FAILED')
        require(not db.execute('PRAGMA foreign_key_check').fetchall(), 'DATABASE_FOREIGN_KEY_FAILED')
        rows = db.execute('SELECT device_id,state FROM carecall_devices').fetchall()
        require(set(rows) == {(name, 'staged') for name in values.DEVICES}, 'INITIAL_DEVICE_STATES_INVALID')
        require(db.execute('SELECT version,phase FROM carecall_registry_meta').fetchall()
                == [(2, 'set_calls_and_confirmations')], 'INITIAL_REGISTRY_INVALID')
        expected = {'carecall_registry_meta':1, 'carecall_sets':1, 'carecall_devices':3, 'guardian_cursor':1}
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            require(re.fullmatch('[A-Za-z0-9_]+', table), 'DATABASE_TABLE_NAME_INVALID')
            count = db.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0]
            require(count == expected.get(table, 0), 'DATABASE_HAS_RUNTIME_DATA')


def expected_current_rules(base, managed, network):
    """Only substitute the two reviewed LAN rules; retain every other rule."""
    managed.validate_network(network)
    expected = base.expected(4)
    for port in managed.PORTS:
        original = managed.lan_record(managed.ORIGINAL, port)
        require(expected.count(original) == 1, 'REVIEWED_FIREWALL_BASELINE_INVALID')
        expected[expected.index(original)] = managed.lan_record(network, port)
    return expected


def network_report(source, layout):
    report = {'NETPLAN_AND_PROFILE': 'REVIEW_REQUIRED', 'WLAN0_AP_CAPABILITY': wifi_capability(),
              'NETWORK_SERVICES': 'REVIEW_REQUIRED', 'FIREWALL_CURRENT_LAN_RULESET': 'REVIEW_REQUIRED',
              'MDNS_HOSTNAME_MATCH': 'YES' if socket.gethostname().lower().split('.')[0]
                                      == 'carecall-mqtt-server' else 'REVIEW_REQUIRED'}
    try:
        sys.path.insert(0, str(source / 'wifi-manager'))
        tx = importlib.import_module('transaction')
        base = importlib.import_module('firewall_base')
        managed = importlib.import_module('firewall')
        common = importlib.import_module('common')
        require(all(Path(m.__file__).parent == source / 'wifi-manager'
                    for m in (tx, base, managed, common)), 'MIXED_WIFI_MODULES')
        saved = json.loads(boot.read_file(layout.path(boot.WIFI_FILES[1]), mode=0o600))
        actual = values.profile_from_netplan(boot.read_file(layout.path(boot.NETPLAN), mode=0o600))
        require(saved == actual, 'INITIAL_PROFILE_DIFFERS_FROM_NETPLAN')
        tx.document(saved)
        tx.cloud_policy_check()
        report['NETPLAN_AND_PROFILE'] = 'PASS'
        if (common.station_ready() and common.active('systemd-networkd.service')
                and not any(common.active(name + '.service')
                            for name in ('NetworkManager', 'hostapd', 'dnsmasq'))):
            report['NETWORK_SERVICES'] = 'PASS'
        network = managed.current_network()
        status = base.run_ufw('status', 'verbose')
        require(re.search(r'^Status: active\s*$', status, re.MULTILINE), 'ACTIVE_UFW_REQUIRED')
        records = base.normalize(base.run_ufw('show', 'raw'))
        require(records == expected_current_rules(base, managed, network), 'FIREWALL_RULESET_DIFFERS')
        base.check_extra_nft_tables()
        if managed.state_path().exists():
            state, found = managed.inspect()
            require(state['phase'] == 'committed' and state['target'] == network
                    and found == {(network, port) for port in managed.PORTS}, 'LAN_STATE_REQUIRES_REVIEW')
        report['FIREWALL_CURRENT_LAN_RULESET'] = 'PASS'
    except Exception:
        # Parser/system exceptions may contain SSIDs or credentials; never echo them.
        pass
    return report


def verify_telegram(source, layout):
    app = layout.path(prep.APP)
    sys.path.insert(0, str(app))
    api = importlib.import_module('telegram_api')
    settings = importlib.import_module('telegram_settings')
    require(Path(api.__file__).parent == app and Path(settings.__file__).parent == app,
            'MIXED_TELEGRAM_MODULES')
    token = boot.read_file(layout.path('/etc/carecall/telegram/bot-token'), mode=0o600).decode('ascii').strip()
    client = api.TelegramClient(token)
    client.verify_bot()  # Only getMe; no message or registration is sent.
    require(client.request('getWebhookInfo').get('url') == '', 'TELEGRAM_WEBHOOK_MISSING_OR_CONFIGURED')


def check(source, layout, system, *, online=False, emit=print):
    uid, gid, broker_gid = boot.verify_prepared(source, layout, system)
    checks = {}
    for phase, names in (('core', boot.CORE_FILES), ('wifi', boot.WIFI_FILES), ('database', (boot.DB,))):
        try:
            boot.check_phase_files(layout, phase, names, uid)
            checks[phase] = True
        except Exception:
            checks[phase] = False
        emit(phase.upper() + '_FILES=' + ('PASS' if checks[phase] else 'MISSING_OR_REVIEW_REQUIRED'))
    if checks['core']:
        try:
            for name in ('/etc/mosquitto/passwd', '/etc/mosquitto/acl'):
                require(layout.path(name).stat().st_gid == broker_gid, 'BROKER_FILE_GROUP_INVALID')
            values.receiver_password_matches(boot.read_file(layout.path('/etc/mosquitto/passwd'), mode=0o640),
                                             boot.read_file(layout.path('/etc/carecall/receiver.env'), mode=0o600))
            values.mqtt_main_is_plain(boot.read_file(layout.path('/etc/mosquitto/mosquitto.conf')),
                                     list(layout.path('/etc/mosquitto/conf.d').glob('*.conf')))
            emit('MQTT_INITIAL_CONFIG=PASS_LISTENER_STAGED')
        except Exception:
            checks['core'] = False
            emit('MQTT_INITIAL_CONFIG=REVIEW_REQUIRED')
    if checks['database']:
        try:
            empty_database(layout.path(boot.DB))
            emit('EMPTY_DATABASE_AND_STAGED_DEVICES=PASS')
        except Exception:
            checks['database'] = False
            emit('EMPTY_DATABASE_AND_STAGED_DEVICES=REVIEW_REQUIRED')
    report = network_report(source, layout) if checks['wifi'] else {'WIFI_CONFIG': 'REQUIRED'}
    for key, value in report.items():
        emit(key + '=' + value)
    network_ok = all(value in ('YES', 'PASS') for value in report.values())
    telegram_ok = not online
    if online and checks['core']:
        try:
            verify_telegram(source, layout)
            telegram_ok = True
        except Exception:
            pass
    emit('TELEGRAM_API_VERIFICATION=' + ('PASS' if online and telegram_ok else
                                        'REVIEW_REQUIRED' if online else 'NOT_REQUESTED'))
    okay = all(checks.values()) and network_ok and telegram_ok
    emit('NEW_PI_LOCAL_CHECK=' + ('PASS_PRE_ACTIVATION_ONLY' if okay else 'REVIEW_REQUIRED'))
    emit('NETWORK_ACTIVATION=SEPARATE_REVIEW_REQUIRED')
    return 0 if okay else 2
