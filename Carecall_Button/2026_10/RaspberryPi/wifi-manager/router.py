"""Bounded WPA2 router trial. Never writes Netplan/cloud-init or UFW settings."""
import configparser
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import time

import common as c

TRIAL_SECONDS = 900
CONNECT_SECONDS = 75
UNIT = c.PREFIX + 'router.service'
NETPLAN = Path('/etc/netplan/50-cloud-init.yaml')
NETWORK = Path('/run/systemd/network/10-netplan-wlan0.network')


def private():
    return c.RUN / 'private'


def credentials(body):
    if not isinstance(body, dict) or set(body) != {'token', 'ssid', 'password', 'hidden'}:
        raise c.TrialError('INVALID_REQUEST')
    ssid, password = body['ssid'], body['password']
    if not isinstance(ssid, str) or not isinstance(password, str) or type(body['hidden']) is not bool:
        raise c.TrialError('INVALID_REQUEST')
    try:
        encoded = ssid.encode('utf-8')
    except UnicodeError:
        raise c.TrialError('INVALID_SSID') from None
    if not 1 <= len(encoded) <= 32 or any(ord(ch) < 32 or ord(ch) == 127 for ch in ssid):
        raise c.TrialError('INVALID_SSID')
    if re.fullmatch(r'[0-9A-Fa-f]{64}', password):
        psk = password.lower()
    elif 8 <= len(password) <= 63 and all(32 <= ord(ch) <= 126 for ch in password):
        psk = hashlib.pbkdf2_hmac('sha1', password.encode('ascii'), encoded, 4096, 32).hex()
    else:
        raise c.TrialError('INVALID_ROUTER_PASSWORD')
    return {'ssid': ssid, 'psk_hex': psk, 'hidden': body['hidden']}


def country_code(value):
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'(?:[A-Za-z]{2}|00)', value):
        raise c.TrialError('NETPLAN_REGULATORY_DOMAIN_INVALID')
    return value.upper()


def supplicant_config(candidate, test_id, regulatory_domain=None):
    if not re.fullmatch(r'[0-9a-f]{24}', test_id):
        raise c.TrialError('INVALID_TEST_ID')
    if not re.fullmatch(r'[0-9a-f]{64}', candidate['psk_hex']):
        raise c.TrialError('INVALID_PSK')
    country = country_code(regulatory_domain)
    country_line = f'country={country}\n' if country is not None else ''
    # Preserve the existing Netplan country; never guess a country from locale.
    # Values supplied by the user are encoded as hex, never interpolated as directives.
    return (f'ctrl_interface={private()}/ctrl\nupdate_config=0\nap_scan=1\n' + country_line + 'network={\n'
            f'    ssid={candidate["ssid"].encode("utf-8").hex()}\n'
            f'    psk={candidate["psk_hex"]}\n'
            f'    scan_ssid={1 if candidate["hidden"] else 0}\n'
            f'    id_str="carecall-{test_id}"\n'
            '    key_mgmt=WPA-PSK\n    proto=RSN\n    pairwise=CCMP\n    group=CCMP\n}\n')


def source_hashes():
    paths = [NETPLAN, c.ETC / 'settings.json']
    paths += sorted(Path('/etc/cloud/cloud.cfg.d').glob('*.cfg'))
    paths += [p for p in (Path('/etc/cloud/cloud.cfg'), Path('/boot/firmware/network-config')) if p.is_file()]
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def validate_layout(document, runtime_text):
    if not isinstance(document, dict) or not isinstance(document.get('network'), dict):
        raise c.TrialError('NETPLAN_NETWORK_MAPPING_REQUIRED')
    n = document['network']
    if n.get('version') != 2:
        raise c.TrialError('NETPLAN_VERSION_2_REQUIRED')
    if n.get('renderer', 'networkd') != 'networkd':
        raise c.TrialError('NETPLAN_NETWORKD_RENDERER_REQUIRED')
    if not isinstance(n.get('wifis'), dict) or set(n['wifis']) != {'wlan0'}:
        raise c.TrialError('NETPLAN_SINGLE_WLAN0_REQUIRED')
    w = n['wifis']['wlan0']
    if not isinstance(w, dict):
        raise c.TrialError('NETPLAN_WLAN0_MAPPING_REQUIRED')
    if w.get('renderer', 'networkd') != 'networkd':
        raise c.TrialError('NETPLAN_WLAN0_NETWORKD_REQUIRED')
    if w.get('dhcp4') is not True:
        raise c.TrialError('NETPLAN_WLAN0_DHCP4_REQUIRED')
    allowed = {'dhcp4', 'dhcp6', 'optional', 'access-points', 'renderer', 'regulatory-domain'}
    if set(w) - allowed:
        raise c.TrialError('NETPLAN_WLAN0_OPTIONS_REQUIRE_REVIEW')
    country = country_code(w.get('regulatory-domain'))
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(runtime_text)
    except configparser.Error:
        raise c.TrialError('RUNTIME_NETWORK_PARSE_FAILED') from None
    if parser.get('Match', 'Name', fallback='') != 'wlan0':
        raise c.TrialError('RUNTIME_WLAN0_MATCH_REQUIRED')
    if parser.get('Network', 'DHCP', fallback='').lower() not in ('yes', 'true', 'ipv4'):
        raise c.TrialError('RUNTIME_IPV4_DHCP_REQUIRED')
    if parser.has_option('Network', 'Address') or parser.has_section('Address'):
        raise c.TrialError('RUNTIME_STATIC_ADDRESS_REQUIRES_REVIEW')
    if parser.has_section('Route'):
        raise c.TrialError('RUNTIME_ROUTE_REQUIRES_REVIEW')
    return country


def read_layout():
    import yaml
    try:
        document = yaml.safe_load(NETPLAN.read_text())
    except (OSError, UnicodeError, yaml.YAMLError):
        # Parser messages can contain a Wi-Fi password. Report only fixed codes.
        raise c.TrialError('NETPLAN_READ_OR_PARSE_FAILED') from None
    try:
        runtime_text = NETWORK.read_text()
    except (OSError, UnicodeError):
        raise c.TrialError('RUNTIME_NETWORK_READ_FAILED') from None
    return validate_layout(document, runtime_text)


def preflight():
    actual = [p for directory in ('/lib/netplan', '/etc/netplan', '/run/netplan')
              for p in Path(directory).glob('*.yaml')]
    if actual != [NETPLAN] or NETPLAN.is_symlink():
        raise c.TrialError('NETPLAN_LAYOUT_CHANGED')
    read_layout()
    for name in ('wpa_cli', 'wpa_supplicant'):
        if not Path('/usr/sbin/' + name).is_file():
            raise c.TrialError('MISSING_' + name.upper())
    addresses = json.loads(c.command('ip', '-j', '-4', 'address', 'show').stdout)
    ap_subnet = ipaddress.ip_network(c.AP_IP + '/24', strict=False)
    for link in addresses:
        for address in link.get('addr_info', []):
            if address.get('family') == 'inet' and ipaddress.ip_network(
                    f'{address["local"]}/{address["prefixlen"]}', strict=False).overlaps(ap_subnet):
                raise c.TrialError('SETUP_SUBNET_CONFLICT')
    return source_hashes()


def prepare(state):
    baseline = preflight()
    country = read_layout()
    if source_hashes() != baseline:
        raise c.TrialError('SOURCE_FILES_CHANGED_DURING_PRECHECK')
    state.update(mode='router', deadline=c.now() + TRIAL_SECONDS, attempts=0,
                 router_connected=False, candidate_saved=False, last_attempt='NOT_STARTED',
                 last_wpa_state='NOT_STARTED', last_control_result='NOT_STARTED',
                 candidate_identity_match=None, last_readiness='NOT_STARTED',
                 sources_unchanged=None, source_hashes=baseline, regulatory_domain=country)
    private().mkdir(mode=0o700, parents=True, exist_ok=True)
    if private().is_symlink() or private().stat().st_uid != 0:
        raise c.TrialError('PRIVATE_DIRECTORY_INVALID')
    os.chmod(private(), 0o700)
    (private() / 'candidate.conf').unlink(missing_ok=True)
    (c.RUN / 'public' / 'request.json').unlink(missing_ok=True)
    return state


def publish(state, notice):
    data = c.public_state(state['test_id'], state['token'])
    data.update(mode='router', notice=notice, attempts=state['attempts'],
                can_submit=not state['router_connected'],
                seconds_remaining=max(0, int(state['deadline'] - c.now())))
    path = c.RUN / 'public' / 'session.json'
    c.atomic(path, json.dumps(data), 0o640)
    os.chown(path, 0, pwd.getpwnam('carecall-wifi-web').pw_gid)


def take_request(state):
    path = c.RUN / 'public' / 'request.json'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        raise c.TrialError('INVALID_REQUEST_FILE') from None
    try:
        with os.fdopen(fd, 'rb') as f:
            info = os.fstat(f.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size > 4096
                    or info.st_uid != pwd.getpwnam('carecall-wifi-web').pw_uid):
                raise c.TrialError('INVALID_REQUEST_FILE')
            try:
                body = json.loads(f.read(4097))
            except (ValueError, UnicodeError):
                raise c.TrialError('INVALID_REQUEST') from None
        if (not isinstance(body, dict) or not isinstance(body.get('token'), str)
                or not hmac.compare_digest(body['token'], state['token'])):
            raise c.TrialError('INVALID_REQUEST_TOKEN')
        return credentials(body)
    finally:
        path.unlink(missing_ok=True)


def stop_candidate():
    c.command('systemctl', 'stop', UNIT, check=False)
    if c.active(UNIT):
        raise c.TrialError('CANDIDATE_SERVICE_DID_NOT_STOP')


def remove_ap_override():
    if c.AP_NETWORK.exists():
        if not c.AP_NETWORK.read_text().startswith(c.MARKER):
            raise c.TrialError('UNEXPECTED_AP_RUNTIME_FILE')
        c.AP_NETWORK.unlink()


def candidate_ready(state):
    state['candidate_identity_match'] = None
    if not c.active(UNIT):
        state.update(last_control_result='SERVICE_INACTIVE', last_readiness='SERVICE_INACTIVE')
        return None
    # The worker and supplicant have different PrivateTmp namespaces. Both the
    # server socket (-p) AND the client's reply socket (-s) must be under /run.
    # Keep PrivateTmp enabled; the parent directory is root-only (0700).
    try:
        result = c.command('wpa_cli', '-p', str(private() / 'ctrl'),
                           '-s', str(private()), '-i', 'wlan0', 'status',
                           check=False, timeout=5)
    except subprocess.TimeoutExpired:
        state.update(last_wpa_state='UNAVAILABLE', last_control_result='QUERY_TIMEOUT',
                     last_readiness='CONTROL_UNAVAILABLE')
        return None
    if result.returncode:
        state.update(last_wpa_state='UNAVAILABLE', last_control_result='QUERY_FAILED',
                     last_readiness='CONTROL_UNAVAILABLE')
        return None
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    phase = values.get('wpa_state')
    state['last_wpa_state'] = phase if phase in (
        'DISCONNECTED', 'INTERFACE_DISABLED', 'INACTIVE', 'SCANNING', 'AUTHENTICATING',
        'ASSOCIATING', 'ASSOCIATED', '4WAY_HANDSHAKE', 'GROUP_HANDSHAKE', 'COMPLETED') else 'UNAVAILABLE'
    if state['last_wpa_state'] == 'UNAVAILABLE':
        state.update(last_control_result='INVALID_STATUS', last_readiness='CONTROL_UNAVAILABLE')
        return None
    state['last_control_result'] = 'OK'
    state['candidate_identity_match'] = values.get('id_str') == 'carecall-' + state['test_id']
    if values.get('wpa_state') != 'COMPLETED':
        state['last_readiness'] = 'WPA_NOT_COMPLETED'
        return None
    if not state['candidate_identity_match']:
        state['last_readiness'] = 'CANDIDATE_ID_MISMATCH'
        return None
    addresses = json.loads(c.command('ip', '-j', '-4', 'address', 'show', 'dev', 'wlan0').stdout)
    networks = [ipaddress.ip_network(f'{a["local"]}/{a["prefixlen"]}', strict=False)
                for link in addresses for a in link.get('addr_info', [])
                if a.get('family') == 'inet' and a.get('scope') == 'global']
    ap = ipaddress.ip_network(c.AP_IP + '/24', strict=False)
    if any(n.overlaps(ap) for n in networks):
        state['last_readiness'] = 'SUBNET_CONFLICT'
        raise c.TrialError('ROUTER_SUBNET_CONFLICT')
    if not networks:
        state['last_readiness'] = 'IPV4_MISSING'
        return None
    # iproute2 can omit the JSON "dev" field when an output-device filter was
    # supplied (filter.oifmask == -1). Read default routes without that filter,
    # then require wlan0 explicitly below. Never accept another link's gateway.
    routes = json.loads(c.command('ip', '-j', '-4', 'route', 'show', 'default').stdout)
    for network in networks:
        if any(r.get('gateway') and r.get('dev') == 'wlan0'
               and ipaddress.ip_address(r['gateway']) in network for r in routes):
            state['last_readiness'] = 'READY'
            return str(network)
    state['last_readiness'] = 'DEFAULT_ROUTE_MISSING'
    return None


def attempt(candidate, should_stop):
    with c.network_lock():
        state = c.read_state()
        state['phase'] = 'router_connecting'
        state['attempts'] += 1
        state['last_wpa_state'] = 'STARTING'
        state.update(last_control_result='NOT_STARTED', candidate_identity_match=None,
                     last_readiness='STARTING', last_attempt='IN_PROGRESS')
        c.save_state(state)
        for suffix in ('web', 'dhcp', 'hostapd'):
            c.command('systemctl', 'stop', c.PREFIX + suffix + '.service')
        c.command('systemctl', 'stop', c.STATION)
        stop_candidate()
        remove_ap_override()
        c.command('ip', 'link', 'set', 'wlan0', 'down')
        c.command('iw', 'dev', 'wlan0', 'set', 'type', 'managed')
        # Drop only wlan0 global IPv4 addresses so a stale lease cannot pass the test.
        addresses = json.loads(c.command('ip', '-j', '-4', 'address', 'show', 'dev', 'wlan0').stdout)
        for link in addresses:
            for a in link.get('addr_info', []):
                if a.get('family') == 'inet' and a.get('scope') == 'global':
                    c.command('ip', '-4', 'address', 'del', f'{a["local"]}/{a["prefixlen"]}', 'dev', 'wlan0', check=False)
        c.atomic(private() / 'candidate.conf', supplicant_config(
            candidate, state['test_id'], state.get('regulatory_domain')))
        c.command('ip', 'link', 'set', 'wlan0', 'up')
        c.command('networkctl', 'reload')
        c.command('networkctl', 'reconfigure', 'wlan0')
        c.command('systemctl', 'reset-failed', UNIT, check=False)
        c.command('systemctl', 'start', UNIT)
        end = min(c.now() + CONNECT_SECONDS, state['deadline'])
        result = 'CONNECT_TIMEOUT'
        control_observed = False
        try:
            while c.now() < end and not should_stop():
                subnet = candidate_ready(state)
                control_observed = control_observed or state.get('last_control_result') == 'OK'
                c.save_state(state)  # Expose sanitized current observations, not old LAST_*.
                if subnet:
                    saved = dict(candidate, version=c.VERSION, test_id=state['test_id'],
                                 tested_subnet=subnet, regulatory_domain=state.get('regulatory_domain'),
                                 purpose='candidate-only-not-boot-config')
                    c.atomic(c.ETC / 'tested-router-candidate.json', json.dumps(saved, ensure_ascii=True) + '\n')
                    state.update(router_connected=True, candidate_saved=True)
                    result = 'CONNECTED'
                    break
                if not c.active(UNIT):
                    result = 'CANDIDATE_SERVICE_STOPPED'
                    break
                time.sleep(1)
            if result == 'CONNECT_TIMEOUT' and not control_observed and state.get('last_control_result') in (
                    'QUERY_TIMEOUT', 'QUERY_FAILED', 'INVALID_STATUS'):
                result = 'CONTROL_STATUS_UNAVAILABLE'
        except c.TrialError as exc:
            if str(exc) != 'ROUTER_SUBNET_CONFLICT':
                raise
            result = str(exc)
        finally:
            stop_candidate()
            (private() / 'candidate.conf').unlink(missing_ok=True)
        state['last_attempt'] = result
        c.save_state(state)
        return result


def run(setup_ap, should_stop, phone_confirmed):
    publish(c.read_state(), 'READY')
    setup_ap()
    while not should_stop():
        state = c.read_state()
        if c.deadline_due(state):
            return
        if phone_confirmed(state):
            state['confirmed'] = True
            c.save_state(state)
            time.sleep(2)
            return
        try:
            candidate = take_request(state)
        except c.TrialError:
            publish(state, 'INVALID_REQUEST')
            time.sleep(1)
            continue
        if candidate is None:
            time.sleep(0.5)
            continue
        if state['router_connected']:
            continue
        publish(state, 'CONNECTING')
        time.sleep(2)  # Let the phone receive the instructions before the AP disappears.
        result = attempt(candidate, should_stop)
        if should_stop() or c.deadline_due(c.read_state()):
            return
        publish(c.read_state(), result)
        setup_ap()  # Both failure and success return to the same setup AP.


def finish_restore(state):
    stop_candidate()
    (private() / 'candidate.conf').unlink(missing_ok=True)
    (c.RUN / 'public' / 'request.json').unlink(missing_ok=True)
    for path in (c.RUN / 'public').glob('.request-*'):
        if path.is_file() or path.is_symlink():
            path.unlink(missing_ok=True)
    state['phase'] = 'restoring'
    try:
        state['sources_unchanged'] = source_hashes() == state['source_hashes']
    except Exception:
        state['sources_unchanged'] = False
    if not state['sources_unchanged']:
        state['failure'] = 'SOURCE_FILES_CHANGED_DURING_TRIAL'
    c.save_state(state)
