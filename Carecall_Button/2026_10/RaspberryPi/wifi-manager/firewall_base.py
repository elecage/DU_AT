"""Validate the reviewed UFW ruleset, ignoring only traffic counters/formatting.

This is deliberately specific to the research-lab AP trial. It never disables
UFW or treats an arbitrary active firewall as approved.
"""
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess

import common as c

UFW = '/usr/sbin/ufw'
BASELINE = Path(__file__).with_name('reviewed_firewall.json')
# Rule order is part of the reviewed configuration. All rules are IPv4/wlan0.
RULES = (
    ('tcp', '192.168.77.0/24', None, '192.168.77.1', '8080', 'CareCall AP trial web'),
    ('udp', '0.0.0.0/32', '68', '255.255.255.255', '67', 'CareCall AP trial DHCP initial'),
    ('udp', '192.168.77.0/24', '68', '255.255.255.255', '67', 'CareCall AP trial DHCP broadcast'),
    ('udp', '192.168.77.0/24', '68', '192.168.77.1', '67', 'CareCall AP trial DHCP unicast'),
)


def address(text):
    network = ipaddress.ip_network(text, strict=False)
    return str(network.network_address) if network.prefixlen == network.max_prefixlen else str(network)


def rule_args(rule):
    proto, source, source_port, destination, port, comment = rule
    args = ['allow', 'in', 'on', 'wlan0', 'proto', proto, 'from', source]
    if source_port:
        args += ['port', source_port]
    return args + ['to', destination, 'port', port, 'comment', comment]


def rule_record(rule):
    proto, source, source_port, destination, port, _ = rule
    value = ['R', 'ACCEPT', {'tcp': '6', 'udp': '17'}[proto], '--', 'wlan0', '*',
             address(source), address(destination), proto]
    if source_port:
        value.append('spt:' + source_port)
    value.append('dpt:' + port)
    return ' '.join(value)


def normalize(raw):
    """Keep all chains, policies and rule fields; strip counters and whitespace."""
    records = []
    family = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line == 'IPV4 (raw):':
            family = '4'
            records.append('F 4')
            continue
        if line == 'IPV6:':
            family = '6'
            records.append('F 6')
            continue
        if family is None:
            raise c.TrialError('UNRECOGNIZED_UFW_REPORT')
        if re.fullmatch(r'pkts\s+bytes\s+target\s+prot\s+opt\s+in\s+out\s+source\s+destination', line):
            continue
        match = re.fullmatch(r'Chain (\S+) \(policy (\S+) \d+ packets, \d+ bytes\)', line)
        if match:
            records.append('C ' + match[1] + ' ' + match[2])
            continue
        match = re.fullmatch(r'Chain (\S+) \(\d+ references\)', line)
        if match:
            records.append('C ' + match[1] + ' -')
            continue
        fields = line.split()
        if len(fields) < 9 or not fields[0].isdigit() or not fields[1].isdigit():
            raise c.TrialError('UNRECOGNIZED_UFW_REPORT')
        fields = fields[2:]
        # Addresses may be printed as host or /32 (/128). They mean the same thing.
        try:
            fields[5] = address(fields[5])
            fields[6] = address(fields[6])
        except ValueError:
            raise c.TrialError('UNRECOGNIZED_UFW_ADDRESS') from None
        records.append('R ' + ' '.join(fields))
    if records.count('F 4') != 1 or records.count('F 6') != 1:
        raise c.TrialError('INCOMPLETE_UFW_REPORT')
    return records


def expected(count):
    if not 0 <= count <= len(RULES):
        raise ValueError('invalid rule count')
    records = json.loads(BASELINE.read_text())
    start = records.index('C ufw-user-input -') + 1
    end = start
    while end < len(records) and records[end].startswith('R '):
        end += 1
    records[end:end] = [rule_record(rule) for rule in RULES[:count]]
    return records


def reviewed_count(records):
    for count in range(len(RULES) + 1):
        if records == expected(count):
            return count
    raise c.TrialError('UFW_RULESET_DIFFERS_FROM_REVIEWED_LOG')


def run_ufw(*args, timeout=30):
    try:
        result = subprocess.run([UFW, *args], text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout,
                                env=dict(os.environ, LC_ALL='C'))
    except (OSError, subprocess.TimeoutExpired):
        raise c.TrialError('UFW_COMMAND_UNAVAILABLE_OR_TIMED_OUT') from None
    if result.returncode:
        raise c.TrialError('UFW_COMMAND_FAILED')
    return result.stdout


def check_extra_nft_tables():
    # ufw show raw covers iptables tables, not arbitrary native nft tables.
    # Refuse unknown tables rather than silently approving another firewall.
    nft = Path('/usr/sbin/nft')
    if not nft.exists():
        return
    try:
        result = subprocess.run([str(nft), '-j', 'list', 'ruleset'], text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        if result.returncode:
            raise c.TrialError('NFT_INSPECTION_FAILED')
        data = json.loads(result.stdout)
        for item in data['nftables']:
            table = item.get('table')
            if table and (table.get('family') not in ('ip', 'ip6') or
                          table.get('name') not in ('filter', 'nat', 'mangle', 'raw')):
                raise c.TrialError('ADDITIONAL_NFT_TABLE_REQUIRES_REVIEW')
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        raise c.TrialError('NFT_INSPECTION_FAILED') from None


def inspect():
    status = run_ufw('status', 'verbose')
    if not re.search(r'^Status: active\s*$', status, re.MULTILINE):
        raise c.TrialError('REVIEWED_UFW_MUST_REMAIN_ACTIVE')
    raw = run_ufw('show', 'raw')
    count = reviewed_count(normalize(raw))
    check_extra_nft_tables()
    return count, status, raw


def check():
    count, _, _ = inspect()
    if count != len(RULES):
        raise c.TrialError('SETUP_AP_UFW_RULES_INCOMPLETE')
