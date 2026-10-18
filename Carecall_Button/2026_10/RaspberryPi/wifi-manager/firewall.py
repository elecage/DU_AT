"""Reconcile only two wlan0 LAN allowances; retain all reviewed other rules.

Called only after saved WPA identity/address verification. A durable transition
allows recovery from interruption between separate UFW operations.
"""
import contextlib
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import stat
import subprocess
import time
import common as c
import firewall_base as base

ORIGINAL = '192.168.0.0/24'
PORTS = ('22', '1883')
PRIVATE = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
RESERVED = tuple(ipaddress.ip_network(n) for n in ('192.168.77.0/24', '192.168.78.0/24'))
_cached_network = None
_cached_until = 0


def require(value, code):
    if not value:
        raise c.TrialError(code)


def state_path():
    return c.ETC / 'lan-firewall.json'


def validate_network(value):
    try:
        net = ipaddress.ip_network(value, strict=True)
        require(net.version == 4 and net.prefixlen <= 30 and str(net) == value, 'LAN_SUBNET_INVALID')
        require(any(net.subnet_of(parent) for parent in PRIVATE), 'LAN_PRIVATE_IPV4_REQUIRED')
        require(not any(net.overlaps(n) for n in RESERVED), 'LAN_SETUP_SUBNET_CONFLICT')
        return net
    except (ValueError, TypeError):
        raise c.TrialError('LAN_SUBNET_INVALID') from None


def load_state():
    path = state_path()
    if not path.exists():
        require(not path.is_symlink(), 'LAN_STATE_NOT_TRUSTED')
        return {'version': 1, 'phase': 'committed', 'networks': [ORIGINAL], 'target': ORIGINAL}
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
                not info.st_mode & 0o077 and info.st_size <= 4096, 'LAN_STATE_NOT_TRUSTED')
        try:
            data = json.loads(stream.read(4097))
        except (ValueError, UnicodeError):
            raise c.TrialError('LAN_STATE_INVALID') from None
    require(isinstance(data, dict) and set(data) == {'version', 'phase', 'networks', 'target'} and
            data['version'] == 1 and data['phase'] in ('pending', 'committed'), 'LAN_STATE_INVALID')
    networks = data['networks']
    require(isinstance(networks, list) and 1 <= len(networks) <= 3 and
            all(isinstance(n, str) for n in networks) and len(set(networks)) == len(networks), 'LAN_STATE_INVALID')
    for network in networks:
        validate_network(network)
    require(data['target'] in networks and
            (data['phase'] == 'pending' or networks == [data['target']]), 'LAN_STATE_INVALID')
    return data


def save_state(phase, networks, target):
    c.atomic(state_path(), json.dumps({'version': 1, 'phase': phase,
                                     'networks': sorted(set(networks)), 'target': target}) + '\n')


@contextlib.contextmanager
def lock():
    c.RUN.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd = os.open(c.RUN / 'lan-firewall.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def lan_record(network, port):
    validate_network(network)
    require(port in PORTS, 'LAN_PORT_INVALID')
    return f'R ACCEPT 6 -- wlan0 * {network} 0.0.0.0/0 tcp dpt:{port}'


def split_lan(records, networks):
    """Strip exact rules only INSIDE ufw-user-input, never another chain."""
    wanted = {lan_record(n, p): (n, p) for n in networks for p in PORTS}
    fixed, found = [], set()
    inside = False
    for line in records:
        if line.startswith('C ') or line.startswith('F '):
            inside = line == 'C ufw-user-input -'
        if inside and line in wanted:
            item = wanted[line]
            require(item not in found, 'LAN_DUPLICATE_RULE')
            found.add(item)
        else:
            fixed.append(line)
    return fixed, found


def classify(records, state):
    fixed, found = split_lan(records, state['networks'])
    expected, _ = split_lan(base.expected(len(base.RULES)), [ORIGINAL])
    require(fixed == expected, 'UFW_RULESET_DIFFERS_FROM_REVIEWED_LOG')
    if state['phase'] == 'committed':
        require(found == {(state['target'], p) for p in PORTS}, 'LAN_COMMITTED_RULES_MISSING')
    return found


def inspect():
    status = base.run_ufw('status', 'verbose')
    require(any(line.strip() == 'Status: active' for line in status.splitlines()), 'REVIEWED_UFW_MUST_REMAIN_ACTIVE')
    raw = base.run_ufw('show', 'raw')
    state = load_state()
    found = classify(base.normalize(raw), state)
    base.check_extra_nft_tables()
    return state, found


def check():
    with lock():
        inspect()


def select_network(addresses, routes):
    require(isinstance(addresses, list) and isinstance(routes, list) and
            all(isinstance(x, dict) for x in addresses + routes), 'LAN_ADDRESS_OR_ROUTE_INVALID')
    candidates = set()
    for route in routes:
        if route.get('dev') != 'wlan0' or route.get('dst', 'default') != 'default' or not route.get('gateway'):
            continue
        if route.get('type', 'unicast') != 'unicast':
            continue
        try:
            gateway = ipaddress.IPv4Address(route['gateway'])
            for link in addresses:
                if link.get('ifname') != 'wlan0':
                    continue
                for item in link.get('addr_info', []):
                    if item.get('family') != 'inet' or item.get('scope') != 'global':
                        continue
                    iface = ipaddress.IPv4Interface(f"{item['local']}/{item['prefixlen']}")
                    net = iface.network
                    if gateway in net and gateway != iface.ip and iface.ip not in (net.network_address, net.broadcast_address) and gateway not in (net.network_address, net.broadcast_address):
                        candidates.add(str(net))
        except (ValueError, KeyError, TypeError):
            raise c.TrialError('LAN_ADDRESS_OR_ROUTE_INVALID') from None
    require(len(candidates) == 1, 'LAN_ADDRESS_OR_ROUTE_AMBIGUOUS')
    result = candidates.pop()
    validate_network(result)
    return result


def current_network():
    try:
        addresses = json.loads(c.command('ip', '-j', '-4', 'address', 'show', 'dev', 'wlan0').stdout)
        routes = json.loads(c.command('ip', '-j', '-4', 'route', 'show', 'default').stdout)
        return select_network(addresses, routes)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        raise c.TrialError('LAN_ADDRESS_OR_ROUTE_UNAVAILABLE') from None


def flush_rules():
    # Persist UFW files before publishing a committed state. Pending state accepts
    # both old and new rules if power is lost between separate UFW operations.
    directory = Path('/etc/ufw')
    for name in ('user.rules', 'user6.rules'):
        with (directory / name).open('rb') as stream:
            os.fsync(stream.fileno())
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def rule_args(network, port):
    validate_network(network)
    require(port in PORTS, 'LAN_PORT_INVALID')
    return ['allow', 'in', 'on', 'wlan0', 'proto', 'tcp', 'from', network, 'to', 'any', 'port', port]


def reconcile(target):
    """Root caller; used by the manager, and by guarded installer rollback."""
    validate_network(target)
    with lock():
        state, found = inspect()
        desired = {(target, p) for p in PORTS}
        if found == desired:
            if not state_path().exists() or state['phase'] != 'committed' or state['target'] != target:
                flush_rules()
                save_state('committed', [target], target)
            return target
        networks = sorted(set(state['networks']) | {n for n, _ in found} | {target})
        require(len(networks) <= 3, 'LAN_TRANSITION_TOO_MANY_NETWORKS')
        save_state('pending', networks, target)
        # Add both replacements before deleting either old allowance.
        for port in PORTS:
            if (target, port) not in found:
                base.run_ufw(*rule_args(target, port), 'comment', 'CareCall managed LAN ' + ('SSH' if port == '22' else 'MQTT'))
                _, actual = inspect()
                require((target, port) in actual, 'LAN_RULE_ADD_NOT_EFFECTIVE')
        _, actual = inspect()
        require(desired <= actual, 'LAN_REPLACEMENT_RULES_NOT_READY')
        for network, port in sorted(actual - desired):
            base.run_ufw('--force', 'delete', *rule_args(network, port))
        _, actual = inspect()
        require(actual == desired, 'LAN_RULE_RECONCILE_INCOMPLETE')
        flush_rules()
        save_state('committed', [target], target)
        return target


def sync_current():
    global _cached_network, _cached_until
    target = current_network()
    if target == _cached_network and time.monotonic() < _cached_until:
        return target
    result = reconcile(target)
    _cached_network, _cached_until = result, time.monotonic() + 30
    return result


def report():
    with lock():
        state, _ = inspect()
    print('MQTT_NEW_SUBNET_SUPPORT=IMPLEMENTED_PRIVATE_IPV4')
    print('LAN_FIREWALL_PHASE=' + state['phase'])
    print('LAN_ALLOWED_SUBNET=' + state['target'])
    print('LAN_ALLOWED_PORTS=22,1883')
    try:
        current = current_network()
        ready = state['phase'] == 'committed' and state['target'] == current and state_path().exists()
        print('LAN_RULES_MATCH_CURRENT_NETWORK=' + ('YES' if ready else 'NO'))
    except c.TrialError:
        print('LAN_RULES_MATCH_CURRENT_NETWORK=NOT_ON_SUPPORTED_LAN')
