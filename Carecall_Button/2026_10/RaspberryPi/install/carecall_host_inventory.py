"""Read-only, value-minimizing host inventory for public Wi-Fi installation.

No writes, package installation, network probes, firewall changes or service
activation. Returned fields contain versions, booleans and fixed status codes.
"""
from pathlib import Path
import os
import re
import subprocess

TARGET_POINT_RELEASE = '24.04.5'
PACKAGES = ('systemd', 'netplan.io', 'ufw', 'iptables', 'nftables',
            'hostapd', 'dnsmasq-base', 'wpasupplicant', 'python3-yaml', 'avahi-daemon')


def public_atom(value):
    if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9._+:~/-]{1,128}', value):
        return value
    return 'UNKNOWN'


def os_summary(release, architecture, model):
    version = release.get('VERSION', '')
    match = re.match(r'^(24\.04\.[0-9]+)(?:\s|$)', version) if isinstance(version, str) else None
    point = match[1] if match else 'UNKNOWN'
    is_pi5 = isinstance(model, str) and model.startswith('Raspberry Pi 5 Model ')
    okay = (release.get('ID') == 'ubuntu' and release.get('VERSION_ID') == '24.04'
            and point == TARGET_POINT_RELEASE and architecture == 'aarch64' and is_pi5)
    return {
        'OS_ID': public_atom(release.get('ID')),
        'OS_VERSION_ID': public_atom(release.get('VERSION_ID')),
        'OS_POINT_RELEASE': point,
        'CPU_ARCHITECTURE': public_atom(architecture),
        'RASPBERRY_PI_5': 'YES' if is_pi5 else 'NO_OR_UNDETECTED',
        'TARGET_UBUNTU_24_04_5_PI5': 'MATCH' if okay else 'REVIEW_REQUIRED',
    }


def run_readonly(argv, *, timeout=8):
    try:
        result = subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=timeout,
                                env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        return 127, ''


def package_summary(run=run_readonly):
    report = {}
    for name in PACKAGES:
        code, output = run(['/usr/bin/dpkg-query', '-W', '-f=${Version}', name])
        report['PACKAGE_' + name.upper().replace('-', '_').replace('.', '_')] = (
            public_atom(output.strip()) if code == 0 else 'MISSING_OR_UNAVAILABLE')
    return report


def wifi_capability(run=run_readonly):
    code, text = run(['/usr/sbin/iw', 'dev', 'wlan0', 'info'])
    matches = re.findall(r'^\s*wiphy ([0-9]{1,3})\s*$', text, re.MULTILINE) if code == 0 else []
    if len(matches) != 1:
        return 'UNDETECTED'
    code, text = run(['/usr/sbin/iw', 'phy', 'phy' + matches[0], 'info'])
    if code:
        return 'UNDETECTED'
    section = re.search(r'Supported interface modes:\s*\n((?:\s+\*[^\n]+\n?)+)', text)
    return 'YES' if section and re.search(r'^\s*\* AP\s*$', section[1], re.MULTILINE) else 'NO'


def firewall_summary(base, managed):
    """Recognize the current LAN state without weakening the runtime checks.

    A present state file must pass the LAN-aware validator. Never fall back to
    the original LAN after a malformed/partial managed state. The inspect
    methods perform reads; do not call check/reconcile/lock here.
    """
    if os.path.lexists(managed.state_path()):
        try:
            state, found = managed.inspect()
            if state['phase'] != 'committed':
                return {'WIFI_REVIEWED_FIREWALL_MATCH': 'REVIEW_REQUIRED',
                        'WIFI_FIREWALL_MODE': 'MANAGED_TRANSITION_PENDING'}
            if len(found) != 2:
                raise ValueError('unexpected_lan_rule_count')
            return {'WIFI_REVIEWED_FIREWALL_MATCH': 'YES',
                    'WIFI_FIREWALL_MODE': 'MANAGED_LAN_COMMITTED', 'WIFI_AP_RULE_COUNT': '4'}
        except Exception:
            return {'WIFI_REVIEWED_FIREWALL_MATCH': 'REVIEW_REQUIRED',
                    'WIFI_FIREWALL_MODE': 'MANAGED_STATE_REVIEW_REQUIRED'}
    try:
        count, _, _ = base.inspect()
        if type(count) is not int or not 0 <= count <= 4:
            raise ValueError('invalid_ap_rule_count')
        return {'WIFI_REVIEWED_FIREWALL_MATCH': 'YES',
                'WIFI_FIREWALL_MODE': 'ORIGINAL_REVIEWED_BASELINE',
                'WIFI_AP_RULE_COUNT': str(count)}
    except Exception:
        return {'WIFI_REVIEWED_FIREWALL_MATCH': 'REVIEW_REQUIRED',
                'WIFI_FIREWALL_MODE': 'UNRECOGNIZED_OR_INACTIVE'}


def board_model():
    try:
        with Path('/sys/firmware/devicetree/base/model').open('rb') as stream:
            return stream.read(256).rstrip(b'\0').decode('ascii')
    except (OSError, UnicodeError):
        return ''
