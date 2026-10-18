#!/usr/bin/env python3
"""Read-only source/host checks. Never prints secret values or enables services."""
from pathlib import Path
import argparse
import hashlib
import importlib
import json
import os
import platform
import pwd
import shutil
import sqlite3
import stat
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from install_common import no_symlinks
from carecall_host_inventory import (os_summary, package_summary, wifi_capability,
                                     firewall_summary, board_model)

PROJECT = Path('/home/carecallserver/carecall_receiver')


def source_check(root):
    manifest = json.loads(Path(__file__).with_name('source_baseline.json').read_text())
    missing = changed = 0
    for relative, digest in manifest['sha256'].items():
        parts = Path(relative).parts
        if Path(relative).is_absolute() or '..' in parts:
            raise ValueError('invalid_manifest_path')
        path = no_symlinks(root / relative)
        if not path.is_file():
            missing += 1
        elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            changed += 1
    print('SOURCE_BASELINE=' + ('PASS' if missing == changed == 0 else 'REVIEW_REQUIRED'))
    print('SOURCE_FILE_COUNT=' + str(len(manifest['sha256'])))
    print('SOURCE_MISSING_COUNT=' + str(missing))
    print('SOURCE_CHANGED_COUNT=' + str(changed))
    return missing == changed == 0


def account_exists(name):
    try:
        return pwd.getpwnam(name)
    except KeyError:
        return None


def private_file_state(path):
    try:
        p = no_symlinks(path)
        s = p.stat()
        if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o077:
            return 'OWNER_OR_MODE_REVIEW_REQUIRED'
        return 'PRESENT_PRIVATE' if s.st_size else 'EMPTY'
    except FileNotFoundError:
        return 'MISSING'
    except Exception:
        return 'CHECK_FAILED'


def active(unit):
    try:
        r = subprocess.run(['systemctl', 'is-active', '--quiet', unit],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        return 'ACTIVE' if r.returncode == 0 else 'NOT_ACTIVE'
    except (OSError, subprocess.TimeoutExpired):
        return 'UNAVAILABLE'


def host_check(source_root):
    if os.geteuid() != 0:
        print('HOST_CHECK=RUN_WITH_SUDO_FOR_PRIVATE_FILE_METADATA')
        return False
    try:
        release = platform.freedesktop_os_release()
    except OSError:
        release = {}
    report = os_summary(release, platform.machine(), board_model())
    for key, value in report.items():
        print(key + '=' + value)
    for key, value in package_summary().items():
        print(key + '=' + value)
    print('WLAN0_AP_CAPABILITY=' + wifi_capability())
    print('PYTHON=' + platform.python_version())
    print('SQLITE_STRICT_TABLE_SUPPORT=' + ('YES' if sqlite3.sqlite_version_info >= (3, 37, 0) else 'NO'))
    for name in ('carecallserver', 'carecall-wifi-web'):
        print('ACCOUNT_' + name.upper().replace('-', '_') + '=' +
              ('PRESENT' if account_exists(name) else 'MISSING'))
    for command in ('mosquitto', 'mosquitto_passwd', 'netplan', 'ufw', 'hostapd',
                    'dnsmasq', 'wpa_supplicant', 'iw', 'ip', 'nft'):
        print('COMMAND_' + command.upper() + '=' + ('PRESENT' if shutil.which(command) else 'MISSING'))
    for unit in ('NetworkManager.service', 'systemd-networkd.service', 'netplan-wpa-wlan0.service',
                 'hostapd.service', 'dnsmasq.service', 'carecall-wifi-manager.service',
                 'mosquitto.service', 'carecall-receiver.service',
                 'carecall-telegram.service', 'carecall-registration.service'):
        print('SERVICE_' + unit.split('.')[0].upper().replace('-', '_') + '=' + active(unit))
    for key, path in (
        ('RECEIVER_ENV', '/etc/carecall/receiver.env'),
        ('TELEGRAM_TOKEN', '/etc/carecall/telegram/bot-token'),
        ('WIFI_SETTINGS', '/etc/carecall-wifi-manager/settings.json'),
        ('WIFI_PROFILE', '/etc/carecall-wifi-manager/profile.json'),
        ('WIFI_INSTALL_BASELINE', '/etc/carecall-wifi-manager/install-baseline.json'),
        ('WIFI_LAN_STATE', '/etc/carecall-wifi-manager/lan-firewall.json'),
    ):
        print(key + '=' + private_file_state(Path(path)))
    print('RECEIVER_DATABASE=' + ('PRESENT' if (PROJECT / 'data/carecall_events.db').is_file() else 'MISSING'))
    print('WIFI_OWNER_MARKER=' + ('PRESENT' if Path('/etc/carecall-wifi-manager/owner').exists() else 'MISSING'))
    # The exact current project's validators perform only reads in these calls.
    sys.path.insert(0, str(source_root / 'wifi-manager'))
    network_ok = firewall_ok = False
    try:
        tx = importlib.import_module('transaction')
        wifi = tx.yaml.safe_load(tx.read(tx.NETPLAN, private=True))['network']['wifis']['wlan0']
        country = wifi.get('regulatory-domain')
        candidate = {'ssid': 'CareCall-preflight-placeholder', 'psk_hex': '0' * 64,
                     'hidden': False, 'regulatory_domain': country.upper() if isinstance(country, str) else country}
        tx.document(candidate)  # Render in memory only; never write/apply Netplan.
        tx.cloud_policy_check()
        network_ok = True
    except Exception:
        pass
    print('NETPLAN_CURRENT_CODE_COMPATIBLE=' + ('YES' if network_ok else 'REVIEW_REQUIRED'))
    try:
        firewall = importlib.import_module('firewall_base')
        managed = importlib.import_module('firewall')
        summary = firewall_summary(firewall, managed)
        for key, value in summary.items():
            print(key + '=' + value)
        firewall_ok = summary['WIFI_REVIEWED_FIREWALL_MATCH'] == 'YES'
    except Exception:
        print('WIFI_REVIEWED_FIREWALL_MATCH=CHECK_FAILED')
    print('REPORT_COMPLETE=YES')
    print('WIFI_AUTO_ACTIVATION=NOT_PERFORMED')
    print('INSTALLATION_COMPLETE=NOT_CERTIFIED')
    # Host preflight is a report, not a certificate for complete installation.
    return network_ok and firewall_ok and report['TARGET_UBUNTU_24_04_5_PI5'] == 'MATCH'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--host', action='store_true')
    args = p.parse_args()
    try:
        root = no_symlinks(args.source_root)
        if not source_check(root):
            return 2
        okay = host_check(root) if args.host else True
        print('SYSTEM_CONFIGURATION_WRITTEN=NO')
        print('CREDENTIAL_VALUES_PRINTED=NO')
        print('NETWORK_CONNECTION_TESTED=NO')
        return 0 if okay else 2
    except (Exception, KeyboardInterrupt) as exc:
        print('PREFLIGHT=FAILED TYPE=' + type(exc).__name__)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
