"""Phone Wi-Fi setup, durable commit, and boot/offline AP recovery."""
import argparse
import fcntl
import hmac
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

import common as c
import firewall
import router
import transaction as tx

UNIT = 'carecall-wifi-manager.service'
OWNER = c.ETC / 'owner'
REQUEST = c.ETC / 'activation-request.json'
ACTIVATION = c.ETC / 'activation-result.json'
SETUP_REQUEST = c.RUN / 'setup-request'
stopping = False
raw_command = c.command


def notify(message='WATCHDOG=1'):
    address = os.environ.get('NOTIFY_SOCKET')
    if address:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as connection:
                connection.connect('\0' + address[1:] if address.startswith('@') else address)
                connection.sendall(message.encode())
        except OSError:
            pass


def command(*args, **kwargs):
    notify()
    result = raw_command(*args, **kwargs)
    notify()
    return result


c.command = command


def stop_signal(*_):
    global stopping
    stopping = True


def pause(seconds):
    end = c.now() + seconds
    while c.now() < end:
        notify()
        if stopping:
            raise c.TrialError('STOP_REQUESTED')
        time.sleep(min(0.5, max(0, end - c.now())))


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def state_update(**values):
    state = c.read_state() or {}
    state.update(values)
    c.save_state(state)
    return state


def setup_runtime():
    c.RUN.mkdir(mode=0o755, exist_ok=True)
    account = pwd.getpwnam('carecall-wifi-web')
    public = c.RUN / 'public'
    public.mkdir(mode=0o770, exist_ok=True)
    os.chown(public, 0, account.pw_gid)
    os.chmod(public, 0o770)
    router.private().mkdir(mode=0o700, exist_ok=True)
    os.chmod(router.private(), 0o700)
    for name in ('request.json', 'confirmed', 'session.json'):
        (public / name).unlink(missing_ok=True)
    for path in public.glob('.request-*'):
        path.unlink(missing_ok=True)
    state = {'version': c.VERSION, 'phase': 'starting', 'mode': 'router',
             'test_id': secrets.token_hex(12), 'token': secrets.token_hex(24),
             'attempts': 0, 'can_confirm': False, 'notice': 'READY',
             'last_failure': None, 'boot_id': boot_id(), 'link_ready': False}
    c.save_state(state)


def public_state(notice, pending=False):
    state = state_update(notice=notice, can_confirm=pending)
    data = {'mode': 'router', 'token': state['token'], 'notice': notice,
            'can_submit': not pending, 'can_confirm': pending, 'attempts': state['attempts']}
    path = c.RUN / 'public/session.json'
    c.atomic(path, json.dumps(data), 0o640)
    os.chown(path, 0, pwd.getpwnam('carecall-wifi-web').pw_gid)


def confirmed():
    path = c.RUN / 'public/confirmed'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    try:
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            tx.require(stat.S_ISREG(info.st_mode) and info.st_size <= 64 and
                       info.st_uid == pwd.getpwnam('carecall-wifi-web').pw_uid,
                       'CONFIRMATION_INVALID')
            return hmac.compare_digest(handle.read(64), c.read_state()['token'].encode())
    finally:
        path.unlink(missing_ok=True)


def stop_ap():
    for name in ('web', 'dhcp', 'hostapd'):
        c.command('systemctl', 'stop', c.PREFIX + name + '.service')
    if c.AP_NETWORK.exists():
        tx.require(c.AP_NETWORK.read_text().startswith(c.MARKER), 'UNKNOWN_AP_OVERRIDE')
        c.AP_NETWORK.unlink()
    c.command('ip', '-4', 'address', 'del', c.AP_IP + '/24', 'dev', 'wlan0', check=False)


def stop_candidate():
    router.stop_candidate()
    (router.private() / 'candidate.conf').unlink(missing_ok=True)


def managed_link(clear_addresses=False):
    c.command('ip', 'link', 'set', 'wlan0', 'down')
    c.command('iw', 'dev', 'wlan0', 'set', 'type', 'managed')
    if clear_addresses:
        data = json.loads(c.command('ip', '-j', '-4', 'address', 'show', 'dev', 'wlan0').stdout)
        for link in data:
            for address in link.get('addr_info', []):
                if address.get('family') == 'inet' and address.get('scope') == 'global':
                    c.command('ip', '-4', 'address', 'del',
                              f'{address["local"]}/{address["prefixlen"]}', 'dev', 'wlan0', check=False)
    c.command('ip', 'link', 'set', 'wlan0', 'up')


def generate(root=None):
    argv = ['/usr/libexec/netplan/generate']
    if root is not None:
        argv += ['--root-dir', str(root)]
    notify()
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=40, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    notify()
    tx.require(result.returncode == 0, 'NETPLAN_GENERATE_FAILED')


def validate_render(candidate):
    rendered = tx.document(candidate)
    with tempfile.TemporaryDirectory(prefix='render-', dir=router.private()) as folder:
        root = Path(folder)
        path = root / 'etc/netplan/50-cloud-init.yaml'
        c.atomic(path, rendered)
        generate(root)
        config = tx.read(root / 'run/netplan/wpa-wlan0.conf', private=True).decode()
        tx.require(re.findall(r'^\s*psk=(\S+)\s*$', config, re.MULTILINE) == [candidate['psk_hex']],
                   'GENERATED_PSK_INVALID')
    return rendered


def start_saved(regenerate=False):
    stop_ap()
    stop_candidate()
    c.command('systemctl', 'stop', c.STATION)
    managed_link(clear_addresses=True)
    if regenerate:
        generate()
        c.command('systemctl', 'daemon-reload')
    c.command('networkctl', 'reload')
    c.command('systemctl', 'reset-failed', c.STATION, check=False)
    c.command('systemctl', 'start', c.STATION)
    c.command('networkctl', 'reconfigure', 'wlan0')


def decode_ssid(reply):
    """Decode wpa_supplicant GET_NETWORK ssid (hex or quoted printf bytes)."""
    text = reply.strip()
    if re.fullmatch(r'(?:[0-9a-fA-F]{2}){1,32}', text):
        return bytes.fromhex(text)
    if not (len(text) >= 2 and text.startswith('"') and text.endswith('"')):
        return None
    text = text[1:-1]
    output = bytearray()
    index = 0
    escapes = {'\\': b'\\', '"': b'"', 'n': b'\n', 'r': b'\r', 't': b'\t', 'e': b'\x1b'}
    while index < len(text):
        if text[index] != '\\':
            output.extend(text[index].encode('utf-8'))
            index += 1
            continue
        index += 1
        if index >= len(text):
            return None
        if text[index] == 'x' and re.fullmatch('[0-9a-fA-F]{2}', text[index + 1:index + 3]):
            output.append(int(text[index + 1:index + 3], 16))
            index += 3
        elif text[index] in escapes:
            output.extend(escapes[text[index]])
            index += 1
        else:
            return None
    return bytes(output)


def wpa_query(*args):
    result = c.command('wpa_cli', '-p', '/run/wpa_supplicant', '-s', str(router.private()),
                       '-i', 'wlan0', *args, check=False, timeout=5)
    return result.stdout.strip() if result.returncode == 0 else ''


def saved_ready(profile):
    try:
        if not c.active(c.STATION) or c.active(router.UNIT):
            return False
        values = dict(line.split('=', 1) for line in wpa_query('status').splitlines() if '=' in line)
        if values.get('wpa_state') != 'COMPLETED' or not re.fullmatch('[0-9]+', values.get('id', '')):
            return False
        identity = decode_ssid(wpa_query('get_network', values['id'], 'ssid'))
        if identity != profile['ssid'].encode('utf-8'):
            return False
        data = json.loads(c.command('ip', '-j', '-4', 'address', 'show', 'dev', 'wlan0').stdout)
        networks = [ipaddress.ip_network(f'{a["local"]}/{a["prefixlen"]}', strict=False)
                    for link in data for a in link.get('addr_info', [])
                    if a.get('family') == 'inet' and a.get('scope') == 'global']
        ap = ipaddress.ip_network(c.AP_IP + '/24', strict=False)
        if not networks or any(n.overlaps(ap) for n in networks):
            return False
        routes = json.loads(c.command('ip', '-j', '-4', 'route', 'show', 'default').stdout)
        return any(route.get('dev') == 'wlan0' and route.get('gateway') and
                   ipaddress.ip_address(route['gateway']) in network
                   for network in networks for route in routes)
    except (ValueError, KeyError, subprocess.TimeoutExpired, c.TrialError):
        return False


def sync_lan():
    try:
        firewall.sync_current()
        state_update(lan_failure=None)
        return True
    except c.TrialError as exc:
        state_update(lan_failure=str(exc))
        return False


def wait_saved(profile, seconds=90):
    end = c.now() + seconds
    while c.now() < end:
        if saved_ready(profile):
            return sync_lan()
        pause(1)
    return False


def open_ap(notice='READY', pending=False):
    firewall.check()
    stop_candidate()
    c.command('systemctl', 'stop', c.STATION)
    stop_ap()
    managed_link(clear_addresses=True)
    # Refuse the known setup subnet if another interface already uses it.
    data = json.loads(c.command('ip', '-j', '-4', 'address', 'show').stdout)
    for link in data:
        if link.get('ifname') == 'wlan0':
            continue
        for item in link.get('addr_info', []):
            if item.get('family') == 'inet':
                subnet = ipaddress.ip_network(f'{item["local"]}/{item["prefixlen"]}', strict=False)
                tx.require(not subnet.overlaps(ipaddress.ip_network(c.AP_IP + '/24', strict=False)),
                           'SETUP_SUBNET_CONFLICT')
    (c.RUN / 'public/request.json').unlink(missing_ok=True)
    (c.RUN / 'public/confirmed').unlink(missing_ok=True)
    state_update(phase='opening_ap', token=secrets.token_hex(24), link_ready=False)
    public_state(notice, pending)
    network, hostapd, dnsmasq = c.configs(c.settings())
    c.atomic(c.RUN / 'hostapd.conf', hostapd)
    c.atomic(c.RUN / 'dnsmasq.conf', dnsmasq)
    c.atomic(c.AP_NETWORK, network, 0o644)
    c.command('networkctl', 'reload')
    c.command('networkctl', 'reconfigure', 'wlan0')
    end = c.now() + 25
    while c.AP_IP not in c.ipv4_addresses():
        tx.require(c.now() < end, 'AP_ADDRESS_TIMEOUT')
        pause(0.5)
    for name in ('hostapd', 'dhcp', 'web'):
        c.command('systemctl', 'start', c.PREFIX + name + '.service')
    pause(2)
    tx.require(all(c.active(c.PREFIX + name + '.service') for name in ('hostapd', 'dhcp', 'web')),
               'AP_SERVICES_NOT_READY')
    state_update(phase='setup_ap')


def trial(candidate):
    tx.validate_candidate(candidate)
    state = state_update(phase='testing_router', attempts=c.read_state()['attempts'] + 1,
                         test_id=secrets.token_hex(12), last_control_result='NOT_STARTED',
                         candidate_identity_match=None, last_readiness='STARTING')
    stop_ap()
    c.command('systemctl', 'stop', c.STATION)
    stop_candidate()
    managed_link(clear_addresses=True)
    c.atomic(router.private() / 'candidate.conf', router.supplicant_config(
        candidate, state['test_id'], candidate.get('regulatory_domain')))
    c.command('networkctl', 'reload')
    c.command('networkctl', 'reconfigure', 'wlan0')
    c.command('systemctl', 'reset-failed', router.UNIT, check=False)
    c.command('systemctl', 'start', router.UNIT)
    try:
        end = c.now() + 75
        while c.now() < end:
            subnet = router.candidate_ready(state)
            c.save_state(state)
            if subnet:
                return 'CONNECTED'
            pause(1)
        return 'CONNECT_TIMEOUT'
    except c.TrialError as exc:
        if str(exc) == 'ROUTER_SUBNET_CONFLICT':
            return 'ROUTER_SUBNET_CONFLICT'
        raise
    finally:
        stop_candidate()


def apply_candidate(candidate):
    state_update(phase='saving', last_failure=None)
    committed = False
    try:
        rendered = validate_render(candidate)
        profile = tx.begin(candidate, rendered, boot_id())
        start_saved(regenerate=True)
        tx.require(wait_saved(profile), 'PERSISTENT_CONNECTION_TIMEOUT')
        tx.commit()
        committed = True
        c.atomic(ACTIVATION, json.dumps({'result': 'COMMITTED', 'version': c.VERSION,
                                      'boot_id': boot_id(), 'failure': None}) + '\n')
        state_update(phase='online', link_ready=True, last_failure=None)
        return True
    except Exception as exc:
        if committed:
            # A committed configuration is durable. Restart to reconstruct status;
            # never misreport a post-commit status-write error as rollback.
            raise
        code = str(exc) if isinstance(exc, c.TrialError) else type(exc).__name__
        tx.recover()
        c.atomic(ACTIVATION, json.dumps({'result': 'ROLLED_BACK_OR_NOT_APPLIED',
                                      'failure': code}) + '\n')
        state_update(last_failure=code)
        start_saved(regenerate=True)
        # Re-open the phone UI even when the previous router is no longer present.
        open_ap('SAVE_FAILED')
        return False


def main_loop():
    global stopping
    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    setup_runtime()
    notify('READY=1\nWATCHDOG=1')
    recovered = tx.recover()
    if recovered:
        REQUEST.unlink(missing_ok=True)
        tx.sync_directory(c.ETC)
        c.atomic(ACTIVATION, json.dumps({'result': 'RECOVERED_INTERRUPTED_WRITE', 'failure': None}) + '\n')
    if REQUEST.exists():
        request = tx.obj(REQUEST)
        profile = tx.obj(tx.PROFILE)
        if profile.get('committed_boot_id'):
            # Power loss after commit but before deleting the initial request.
            start_saved()
            if wait_saved(profile):
                state_update(phase='online', link_ready=True)
            else:
                open_ap()
        else:
            tx.require(request.get('profile_sha256') == tx.digest(tx.read(tx.PROFILE)),
                       'ACTIVATION_PROFILE_CHANGED')
            apply_candidate(profile)
        REQUEST.unlink()
        tx.sync_directory(REQUEST.parent)
    else:
        profile = tx.obj(tx.PROFILE)
        tx.validate_candidate(profile)
        start_saved(regenerate=recovered)
        if wait_saved(profile):
            state_update(phase='online', link_ready=True)
        else:
            open_ap()
    pending = None
    offline_since = None
    while not stopping:
        state = c.read_state()
        if state['phase'] == 'online':
            if SETUP_REQUEST.exists():
                SETUP_REQUEST.unlink()
                open_ap()
                offline_since = None
                continue
            if saved_ready(tx.obj(tx.PROFILE)):
                if not sync_lan():
                    open_ap('SAVED_UNAVAILABLE')
                    offline_since = None
                    continue
                offline_since = None
                state_update(link_ready=True)
            else:
                state_update(link_ready=False)
                offline_since = c.now() if offline_since is None else offline_since
                if c.now() - offline_since >= 90:
                    open_ap()
            pause(3)
            continue
        if state['phase'] != 'setup_ap':
            open_ap('RECOVERED')
        if confirmed():
            pause(2)
            if pending is not None:
                apply_candidate(pending)
                pending = None
            else:
                state_update(phase='connecting_saved')
                start_saved()
                if wait_saved(tx.obj(tx.PROFILE)):
                    state_update(phase='online', link_ready=True)
                else:
                    open_ap('SAVED_UNAVAILABLE')
            continue
        candidate = router.take_request(c.read_state())
        if candidate is not None and pending is None:
            candidate['regulatory_domain'] = tx.obj(tx.PROFILE).get('regulatory_domain')
            pause(2)
            result = trial(candidate)
            pending = candidate if result == 'CONNECTED' else None
            open_ap(result, pending=pending is not None)
        if not all(c.active(c.PREFIX + name + '.service') for name in ('hostapd', 'dhcp', 'web')):
            open_ap('CONNECTED' if pending else 'RECOVERED', pending=pending is not None)
        pause(0.5)


def activate():
    profile = tx.obj(tx.PROFILE)
    tx.validate_candidate(profile)
    pending = tx.obj(tx.JOURNAL).get('phase') == 'pending' if tx.JOURNAL.exists() else False
    tx.require(not pending, 'ACTIVATION_IN_PROGRESS_WAIT_FOR_STATUS')
    if profile.get('committed_boot_id'):
        tx.require(OWNER.exists(), 'OWNERSHIP_MARKER_MISSING')
        c.command('systemctl', 'enable', UNIT)
        c.command('systemctl', 'start', '--no-block', UNIT)
        print('WIFI_PERSIST_ACTIVATION=ALREADY_COMMITTED')
        return
    tx.cloud_policy_check()
    for suffix in ('test', 'guard', 'hostapd', 'dhcp', 'web', 'router'):
        tx.require(not c.active('carecall-wifi-aptrial-' + suffix + '.service'), 'OLD_TRIAL_STILL_RUNNING')
    snapshot = tx.obj(c.ETC / 'install-baseline.json')
    for filename, expected in snapshot['activation_hashes'].items():
        tx.require(tx.digest(tx.read(Path(filename))) == expected, 'SOURCE_CHANGED_AFTER_STAGE')
    if c.active(UNIT):
        state = c.read_state() or {}
        tx.require(state.get('phase') in ('online', 'setup_ap'), 'MANAGER_BUSY')
        c.command('systemctl', 'stop', UNIT, timeout=20)
    c.atomic(REQUEST, json.dumps({'profile_sha256': tx.digest(tx.read(tx.PROFILE))}) + '\n')
    c.atomic(OWNER, c.VERSION + '\n')
    try:
        c.command('systemctl', 'enable', UNIT)
        print('WIFI_PERSIST_ACTIVATION=SCHEDULED', flush=True)
        print('SSH_MAY_DISCONNECT=YES', flush=True)
        print('ON_FAILURE=RESTORE_PREVIOUS_CONFIG_AND_OPEN_SETUP_AP', flush=True)
        c.command('systemctl', 'start', '--no-block', UNIT)
    except Exception:
        # If queueing failed, remove ownership only when no process has started.
        if not c.active(UNIT):
            c.command('systemctl', 'disable', UNIT, check=False)
            OWNER.unlink(missing_ok=True)
            REQUEST.unlink(missing_ok=True)
        raise


def status():
    print('VERSION=' + c.VERSION)
    running = c.active(UNIT)
    print('MANAGER_SERVICE=' + ('active' if running else 'inactive'))
    enabled = c.command('systemctl', 'is-enabled', '--quiet', UNIT, check=False).returncode == 0
    print('BOOT_AUTOSTART_ENABLED=' + ('YES' if enabled else 'NO'))
    state = c.read_state() or {}
    print('PHASE=' + state.get('phase', 'staged'))
    print('LINK_READY=' + str(state.get('link_ready', False)))
    record = tx.obj(ACTIVATION) if ACTIVATION.exists() else {}
    print('PERSIST_RESULT=' + record.get('result', 'NOT_ACTIVATED'))
    print('LAST_FAILURE=' + str(state.get('last_failure') or record.get('failure')))
    print('LAN_FAILURE=' + str(state.get('lan_failure')))
    profile = tx.obj(tx.PROFILE)
    pending = tx.obj(tx.JOURNAL).get('phase') == 'pending' if tx.JOURNAL.exists() else False
    committed = bool(profile.get('committed_boot_id')) and not pending
    print('PERSISTENT_PROFILE_COMMITTED=' + ('YES' if committed else 'NO'))
    verified = (running and state.get('phase') == 'online' and state.get('link_ready') is True and
                state.get('boot_id') == boot_id() and committed and profile['committed_boot_id'] != boot_id())
    print('WIFI_VERIFIED_AFTER_REBOOT=' + ('YES' if verified else 'NO'))
    print('TRANSACTION_PENDING=' + str(pending))
    for name in c.CARECALL:
        print('SERVICE ' + name + '=' + ('active' if c.active(name + '.service') else 'not_active'))
    for name in ('hostapd', 'dhcp', 'web', 'router'):
        print('SETUP_SERVICE ' + name + '=' + ('active' if c.active(c.PREFIX + name + '.service') else 'inactive'))
    try:
        firewall.check()
        print('FIREWALL_PRECHECK=PASS')
    except c.TrialError as exc:
        print('FIREWALL_PRECHECK=' + str(exc))
    try:
        firewall.report()
    except c.TrialError as exc:
        print('LAN_FIREWALL_ERROR=' + str(exc))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('activate', 'run', 'status', 'setup'))
    args = parser.parse_args()
    if os.geteuid() != 0:
        print('ERROR=RUN_WITH_SUDO')
        return 1
    os.umask(0o077)
    try:
        if args.action == 'status':
            status()
        elif args.action == 'setup':
            tx.require(c.active(UNIT), 'MANAGER_NOT_ACTIVE')
            tx.require((c.read_state() or {}).get('phase') == 'online', 'MANAGER_NOT_ONLINE')
            c.atomic(SETUP_REQUEST, 'setup\n')
            print('SETUP_AP_REQUESTED=YES')
            print('SSH_MAY_DISCONNECT=YES')
        elif args.action == 'activate':
            activate()
        else:
            c.RUN.mkdir(mode=0o755, exist_ok=True)
            with (c.RUN / 'manager.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                main_loop()
        return 0
    except Exception as exc:
        if stopping:
            return 0
        code = str(exc) if isinstance(exc, c.TrialError) else type(exc).__name__
        code = code if re.fullmatch('[A-Za-z0-9_]+', code) else 'UNEXPECTED_ERROR'
        print('WIFI_MANAGER_ERROR=' + code, flush=True)
        # systemd restarts the process; pending durable transactions are recovered first.
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
