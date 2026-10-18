"""Shared paths and bounded, non-secret operations for the AP trial."""
import contextlib
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time

VERSION = '20261001-lan-1'
ROOT = Path('/opt/carecall-wifi-manager')
ETC = Path('/etc/carecall-wifi-manager')
RUN = Path('/run/carecall-wifi-manager')
STATE = RUN / 'state.json'
RESULT = ETC / 'last-result.json'
AP_NETWORK = Path('/run/systemd/network/00-carecall-wifi-manager.network')
AP_IP = '192.168.77.1'
AP_PORT = 8080
STATION = 'netplan-wpa-wlan0.service'
PREFIX = 'carecall-wifi-manager-'
CARECALL = ('mosquitto', 'carecall-receiver', 'carecall-telegram', 'carecall-registration')
MARKER = '# Managed by CareCall Wi-Fi manager\n'
COMMANDS = {
    'systemctl': '/usr/bin/systemctl', 'networkctl': '/usr/bin/networkctl',
    'ip': '/usr/sbin/ip', 'iw': '/usr/sbin/iw',
    'wpa_cli': '/usr/sbin/wpa_cli',
}


class TrialError(Exception):
    pass


def command(name, *args, check=True, timeout=12):
    executable = COMMANDS[name]
    if name == 'ip' and not Path(executable).exists():
        executable = '/usr/bin/ip'
    p = subprocess.run([executable, *args], stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, text=True, timeout=timeout)
    if check and p.returncode:
        raise TrialError('COMMAND_FAILED_' + name.upper())
    return p


def active(unit):
    return command('systemctl', 'is-active', '--quiet', unit, check=False).returncode == 0


def atomic(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_state(state):
    atomic(STATE, json.dumps(state) + '\n')


def read_state():
    if not STATE.exists():
        return None
    return json.loads(STATE.read_text())


def now():
    return time.monotonic()


def deadline_due(state, current=None):
    return (now() if current is None else current) >= state['deadline']


@contextlib.contextmanager
def network_lock():
    RUN.mkdir(mode=0o755, parents=True, exist_ok=True)
    with (RUN / 'network.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def ipv4_addresses():
    p = command('ip', '-j', '-4', 'address', 'show', 'dev', 'wlan0')
    data = json.loads(p.stdout)
    return [a['local'] for link in data for a in link.get('addr_info', [])
            if a.get('family') == 'inet' and a.get('scope') == 'global']


def station_ready():
    if not active(STATION):
        return False
    p = command('iw', 'dev', 'wlan0', 'link', check=False)
    if p.returncode or 'Connected to ' not in p.stdout:
        return False
    subnet = ipaddress.ip_network(AP_IP + '/24', strict=False)
    return any(ipaddress.ip_address(addr) not in subnet for addr in ipv4_addresses())


def settings():
    config = json.loads((ETC / 'settings.json').read_text())
    if not config['ssid'].startswith('CareCall-Pi-') or len(config['ssid']) > 32:
        raise TrialError('INVALID_SETUP_SSID')
    if not 16 <= len(config['password']) <= 63 or not config['password'].isalnum():
        raise TrialError('INVALID_SETUP_KEY')
    return config


def configs(config):
    network = (MARKER + '[Match]\nName=wlan0\n[Network]\n'
               f'Address={AP_IP}/24\nDHCP=no\nIPv6AcceptRA=no\nConfigureWithoutCarrier=yes\n'
               'LinkLocalAddressing=no\n[Link]\nRequiredForOnline=no\n')
    hostapd = ('interface=wlan0\ndriver=nl80211\n'
               f'ssid={config["ssid"]}\nhw_mode=g\nchannel=1\n'
               'auth_algs=1\nwpa=2\nwpa_key_mgmt=WPA-PSK\n'
               f'rsn_pairwise=CCMP\nwpa_passphrase={config["password"]}\n'
               'wmm_enabled=1\nmax_num_sta=4\n')
    dnsmasq = ('port=0\ninterface=wlan0\nbind-interfaces\n'
               'dhcp-authoritative\ndhcp-range=192.168.77.20,192.168.77.80,255.255.255.0,5m\n'
               f'dhcp-option=3,{AP_IP}\ndhcp-option=6\n'
               f'dhcp-leasefile={RUN}/dhcp.leases\nuser=root\n')
    return network, hostapd, dnsmasq


def public_state(test_id, token):
    return {'test_id': test_id, 'token': token, 'purpose': 'ap-connectivity-trial'}


def new_state():
    return {'test_id': secrets.token_hex(12), 'token': secrets.token_hex(24),
            'started': now(), 'deadline': now() + 180,
            'phase': 'scheduled', 'mode': 'ap', 'confirmed': False, 'restored': False,
            'failure': None}


def final_report(state):
    # Deliberately excludes AP password, SSID, token, MACs and original Wi-Fi values.
    report = {'version': VERSION, 'test_id': state['test_id'],
            'phone_confirmed': bool(state.get('confirmed')),
            'restored': bool(state.get('restored')),
            'failure': state.get('failure'),
            'result': 'PASS' if state.get('confirmed') and state.get('restored')
                      and not state.get('failure') else 'NOT_PASSED'}
    report['mode'] = state.get('mode', 'ap')
    if report['mode'] == 'router':
        for key in ('router_connected', 'candidate_saved', 'sources_unchanged', 'attempts',
                    'last_attempt', 'last_wpa_state', 'last_control_result',
                    'candidate_identity_match', 'last_readiness'):
            report[key] = state.get(key)
        if not all(state.get(key) for key in ('router_connected', 'candidate_saved', 'sources_unchanged')):
            report['result'] = 'NOT_PASSED'
        report['persistent_wifi_changed'] = False
    return report
