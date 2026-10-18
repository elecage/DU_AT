"""Durable, bounded Wi-Fi file transaction; no process/network operations here."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat

import yaml
import common as c

NETPLAN = Path('/etc/netplan/50-cloud-init.yaml')
CLOUD = Path('/etc/cloud/cloud.cfg.d/99-carecall-disable-network-config.cfg')
PROFILE = c.ETC / 'profile.json'
JOURNAL = c.ETC / 'transaction.json'
CLOUD_TEXT = '# CareCall: manage network settings independently of cloud-init.\nnetwork:\n  config: disabled\n'


def require(value, code):
    if not value:
        raise c.TrialError(code)


def read(path, private=False):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        info = os.fstat(f.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
                and info.st_size <= 2 * 1024 * 1024, 'FILE_NOT_TRUSTED')
        if private:
            require(not info.st_mode & 0o077, 'PRIVATE_FILE_REQUIRED')
        return f.read(2 * 1024 * 1024 + 1)


def obj(path):
    value = json.loads(read(path, private=True))
    require(isinstance(value, dict), 'OBJECT_REQUIRED')
    return value


def validate_candidate(candidate):
    ssid = candidate.get('ssid')
    require(isinstance(ssid, str) and 1 <= len(ssid.encode('utf-8')) <= 32 and
            all(ord(ch) >= 32 and ord(ch) != 127 for ch in ssid), 'INVALID_SSID')
    require(isinstance(candidate.get('psk_hex'), str) and
            re.fullmatch('[0-9a-f]{64}', candidate['psk_hex']) and
            type(candidate.get('hidden')) is bool, 'INVALID_CREDENTIALS')
    country = candidate.get('regulatory_domain')
    require(country is None or isinstance(country, str) and
            re.fullmatch('[A-Z]{2}|00', country), 'INVALID_COUNTRY')


def document(candidate):
    validate_candidate(candidate)
    actual = [p for directory in ('/lib/netplan', '/etc/netplan', '/run/netplan')
              for p in Path(directory).glob('*.yaml')]
    require(actual == [NETPLAN], 'NETPLAN_LAYOUT_CHANGED')
    value = yaml.safe_load(read(NETPLAN, private=True))
    network = value['network']
    require(set(value) == {'network'} and network.get('version') == 2 and
            set(network) <= {'version', 'renderer', 'ethernets', 'wifis'} and
            network.get('renderer', 'networkd') == 'networkd' and
            set(network['wifis']) == {'wlan0'} and set(network['ethernets']) == {'eth0'},
            'NETPLAN_LAYOUT_CHANGED')
    wifi = network['wifis']['wlan0']
    require(wifi.get('dhcp4') is True and wifi.get('renderer', 'networkd') == 'networkd' and
            set(wifi) <= {'dhcp4', 'dhcp6', 'optional', 'access-points', 'renderer', 'regulatory-domain'},
            'WIFI_LAYOUT_CHANGED')
    country = wifi.get('regulatory-domain')
    require((country.upper() if isinstance(country, str) else country) == candidate.get('regulatory_domain'),
            'COUNTRY_CHANGED')
    value = copy.deepcopy(value)
    value['network']['wifis']['wlan0']['access-points'] = {candidate['ssid']: {
        'auth': {'key-management': 'psk', 'password': candidate['psk_hex']},
        'hidden': candidate['hidden']}}
    return '# Managed by CareCall Wi-Fi manager.\n' + yaml.safe_dump(value, sort_keys=False, allow_unicode=True)


def cloud_policy_check():
    # Kernel network config has precedence over /etc/cloud configuration.
    tokens = Path('/proc/cmdline').read_text().split()
    require(not any(t.startswith('ip=') or (t.startswith('network-config=') and
                    t != 'network-config=disabled') for t in tokens), 'KERNEL_NETWORK_POLICY_CONFLICT')
    paths = [Path('/etc/cloud/cloud.cfg')] + sorted(Path('/etc/cloud/cloud.cfg.d').glob('*.cfg'))
    for path in paths:
        if not path.exists():
            continue
        config = yaml.safe_load(read(path)) or {}
        require(isinstance(config, dict), 'CLOUD_CONFIG_MAPPING_REQUIRED')
        require(not any(k in config for k in ('merge_how', 'merge_type', 'merge_types')),
                'CUSTOM_CLOUD_MERGE_REQUIRES_REVIEW')
        network = config.get('network')
        if path.parent == CLOUD.parent and path.name > CLOUD.name and network is not None:
            require(isinstance(network, dict) and network.get('config', 'disabled') == 'disabled',
                    'LATER_CLOUD_NETWORK_POLICY_CONFLICT')
    if CLOUD.exists():
        require(read(CLOUD).decode() == CLOUD_TEXT, 'CLOUD_POLICY_FILE_CONFLICT')


def targets():
    return {'netplan': NETPLAN, 'cloud': CLOUD, 'profile': PROFILE}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def begin(candidate, rendered, boot_id):
    require(not JOURNAL.exists() or obj(JOURNAL).get('phase') != 'pending', 'PENDING_TRANSACTION')
    cloud_policy_check()
    validate_candidate(candidate)
    profile = {k: candidate.get(k) for k in ('ssid', 'psk_hex', 'hidden', 'regulatory_domain')}
    profile.update(version=c.VERSION, committed_boot_id=boot_id)
    after = {'netplan': rendered.encode(), 'cloud': CLOUD_TEXT.encode(),
             'profile': (json.dumps(profile, ensure_ascii=True) + '\n').encode()}
    before = {key: base64.b64encode(read(path)).decode() if path.exists() else None
              for key, path in targets().items()}
    record = {'phase': 'pending', 'before': before,
              'after_hashes': {key: digest(value) for key, value in after.items()}}
    # The complete rollback data is durable before any live file is changed.
    c.atomic(JOURNAL, json.dumps(record) + '\n')
    for key, path in targets().items():
        c.atomic(path, after[key].decode())
    return profile


def commit():
    record = obj(JOURNAL)
    require(record.get('phase') == 'pending', 'NO_PENDING_TRANSACTION')
    require(set(record['after_hashes']) == set(targets()) and
            all(digest(read(path)) == record['after_hashes'][key] for key, path in targets().items()),
            'FILES_CHANGED_DURING_COMMIT')
    record['phase'] = 'committed'
    c.atomic(JOURNAL, json.dumps(record) + '\n')


def recover():
    if not JOURNAL.exists():
        return False
    record = obj(JOURNAL)
    if record.get('phase') != 'pending':
        return False
    require(set(record['before']) == set(targets()) and set(record['after_hashes']) == set(targets()),
            'TRANSACTION_LAYOUT_INVALID')
    old = {key: base64.b64decode(value, validate=True) if value is not None else None
           for key, value in record['before'].items()}
    for key, path in targets().items():
        current = read(path) if path.exists() else None
        require(current == old[key] or current is not None and
                digest(current) == record['after_hashes'][key], 'TRANSACTION_EXTERNAL_FILE_CHANGE')
    for key, path in targets().items():
        if old[key] is None:
            path.unlink(missing_ok=True)
            sync_directory(path.parent)
        else:
            c.atomic(path, old[key].decode())
    record['phase'] = 'rolled_back'
    c.atomic(JOURNAL, json.dumps(record) + '\n')
    return True
