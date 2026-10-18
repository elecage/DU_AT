"""Pure validation/rendering for NEW local configuration. No I/O or logging."""
import ast
import base64
import hashlib
import hmac
import json
import re
from install_common import InstallError, require
from carecall_write_initial_secret import secret_content

DEVICES = ('button01', 'button02', 'button03')
USERS = ('carecall_receiver',) + DEVICES


def validate_passwords(passwords):
    require(isinstance(passwords, dict) and set(passwords) == set(USERS), 'MQTT_USERS_INVALID')
    for value in passwords.values():
        require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{16,63}', value),
                'MQTT_USE_16_TO_63_ASCII_LETTERS_DIGITS_UNDERSCORE_HYPHEN')
    require(len(set(passwords.values())) == len(USERS), 'USE_DISTINCT_MQTT_PASSWORDS')


def validate_bot(username, token):
    require(isinstance(username, str) and re.fullmatch(r'[A-Za-z0-9_]{5,32}', username)
            and username.lower().endswith('bot'), 'BOT_USERNAME_WITHOUT_AT_REQUIRED')
    secret_content('telegram-token', token)


def configured_bot_source(original, username):
    require(re.fullmatch(r'[A-Za-z0-9_]{5,32}', username) and username.lower().endswith('bot'),
            'BOT_USERNAME_WITHOUT_AT_REQUIRED')
    text = original.decode('utf-8')
    require(text.count('BOT_USERNAME = ""') == 1, 'PUBLIC_BOT_SETTING_REQUIRED')
    result = text.replace('BOT_USERNAME = ""', 'BOT_USERNAME = ' + json.dumps(username))
    ast.parse(result)
    return result.encode('utf-8')


def configured_bot_name(data):
    tree = ast.parse(data)
    values = [n.value.value for n in tree.body if isinstance(n, ast.Assign)
              and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
              and n.targets[0].id == 'BOT_USERNAME' and isinstance(n.value, ast.Constant)]
    require(len(values) == 1 and isinstance(values[0], str), 'BOT_SETTING_INVALID')
    return values[0]


def verify_password_file(data, passwords=None):
    """Verify Mosquitto $7$ PBKDF2-SHA512 records, optionally against local inputs."""
    try:
        records = {}
        for line in data.decode('ascii').splitlines():
            username, value = line.split(':', 1)
            require(username not in records, 'MQTT_PASSWORD_FILE_INVALID')
            parts = value.split('$')
            require(len(parts) == 5 and parts[:2] == ['', '7'], 'MQTT_PASSWORD_FILE_INVALID')
            rounds = int(parts[2])
            # Mosquitto 2.0.x uses 101 rounds by default for its $7$ format.
            require(100 <= rounds <= 2_000_000, 'MQTT_HASH_ITERATIONS_UNSUPPORTED')
            salt = base64.b64decode(parts[3], validate=True)
            digest = base64.b64decode(parts[4], validate=True)
            require(len(salt) == 12 and len(digest) == 64, 'MQTT_PASSWORD_FILE_INVALID')
            records[username] = (rounds, salt, digest)
        require(set(records) == set(USERS), 'MQTT_PASSWORD_FILE_USERS_INVALID')
        if passwords is not None:
            validate_passwords(passwords)
            for username, (rounds, salt, digest) in records.items():
                actual = hashlib.pbkdf2_hmac('sha512', passwords[username].encode('ascii'), salt, rounds, 64)
                require(hmac.compare_digest(actual, digest), 'MQTT_PASSWORD_HASH_MISMATCH')
        return records
    except (ValueError, UnicodeError, TypeError):
        raise InstallError('MQTT_PASSWORD_FILE_INVALID') from None


def receiver_password_matches(password_file, receiver_env):
    records = verify_password_file(password_file)
    prefix = 'CARECALL_RECEIVER_MQTT_PASSWORD='
    try:
        text = receiver_env.decode('ascii')
        require(text.startswith(prefix) and text.endswith('\n') and text.count('\n') == 1,
                'RECEIVER_ENV_INVALID')
        password = text[len(prefix):-1]
        secret_content('receiver-env', password)
        rounds, salt, digest = records['carecall_receiver']
        actual = hashlib.pbkdf2_hmac('sha512', password.encode('ascii'), salt, rounds, 64)
        require(hmac.compare_digest(actual, digest), 'RECEIVER_PASSWORD_MISMATCH')
    except (ValueError, UnicodeError, TypeError):
        raise InstallError('RECEIVER_ENV_INVALID') from None


def mqtt_main_is_plain(main, fragments):
    """Accept only a simple package-style broker config; never merge auth plugins."""
    require(not fragments, 'EXISTING_MOSQUITTO_FRAGMENT_REQUIRES_REVIEW')
    allowed = {'pid_file', 'persistence', 'persistence_location', 'log_dest', 'include_dir', 'user'}
    includes = []
    try:
        for line in main.decode('utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, value = line.split(None, 1)
            require(key in allowed, 'MOSQUITTO_MAIN_CONFIG_REQUIRES_REVIEW')
            if key == 'include_dir':
                includes.append(value)
            if key == 'user':
                require(value == 'mosquitto', 'MOSQUITTO_USER_REQUIRES_REVIEW')
        require(includes == ['/etc/mosquitto/conf.d'], 'MOSQUITTO_INCLUDE_DIRECTORY_INVALID')
    except (ValueError, UnicodeError):
        raise InstallError('MOSQUITTO_MAIN_CONFIG_REQUIRES_REVIEW') from None


def ap_settings(ssid, password):
    require(isinstance(ssid, str) and re.fullmatch(r'CareCall-Pi-[A-Za-z0-9_-]{1,21}', ssid),
            'AP_SSID_USE_CARECALL_PI_PREFIX_MAX_32_ASCII')
    require(isinstance(password, str) and re.fullmatch(r'[A-Za-z0-9]{16,63}', password),
            'AP_PASSWORD_USE_16_TO_63_ASCII_LETTERS_OR_DIGITS')
    return {'ssid': ssid, 'password': password}


def load_netplan(raw):
    """Reject duplicate YAML keys instead of silently choosing one password."""
    import yaml
    class UniqueLoader(yaml.SafeLoader):
        pass
    def mapping(loader, node, deep=False):
        pairs = loader.construct_pairs(node, deep=deep)
        result = {}
        for key, value in pairs:
            if key in result:
                raise InstallError('NETPLAN_DUPLICATE_KEY')
            result[key] = value
        return result
    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    try:
        return yaml.load(raw, Loader=UniqueLoader)
    except (yaml.YAMLError, ValueError, TypeError, UnicodeError):
        raise InstallError('NETPLAN_PARSE_REQUIRES_REVIEW') from None


def profile_from_netplan(raw):
    """Use the initial OS Wi-Fi credentials locally, without changing Netplan."""
    try:
        doc = load_netplan(raw)
        require(isinstance(doc, dict) and set(doc) == {'network'}, 'NETPLAN_LAYOUT_REQUIRES_REVIEW')
        network = doc['network']
        require(isinstance(network, dict) and network.get('version') == 2
                and set(network) <= {'version', 'renderer', 'ethernets', 'wifis'}
                and network.get('renderer', 'networkd') == 'networkd'
                and set(network['ethernets']) == {'eth0'} and set(network['wifis']) == {'wlan0'},
                'NETPLAN_LAYOUT_REQUIRES_REVIEW')
        wifi = network['wifis']['wlan0']
        require(wifi.get('dhcp4') is True and wifi.get('renderer', 'networkd') == 'networkd'
                and set(wifi) <= {'dhcp4','dhcp6','optional','access-points','renderer','regulatory-domain'},
                'NETPLAN_WLAN0_REQUIRES_REVIEW')
        aps = wifi['access-points']
        require(isinstance(aps, dict) and len(aps) == 1, 'ONE_INITIAL_WIFI_NETWORK_REQUIRED')
        ssid, config = next(iter(aps.items()))
        require(isinstance(ssid, str) and 1 <= len(ssid.encode('utf-8')) <= 32
                and all(ord(c) >= 32 and ord(c) != 127 for c in ssid), 'INITIAL_SSID_INVALID')
        require(isinstance(config, dict) and set(config) <= {'password','auth','hidden'},
                'INITIAL_WIFI_AUTH_REQUIRES_REVIEW')
        hidden = config.get('hidden', False)
        require(type(hidden) is bool, 'INITIAL_WIFI_HIDDEN_INVALID')
        if 'auth' in config:
            auth = config['auth']
            require('password' not in config and isinstance(auth, dict)
                    and set(auth) == {'key-management','password'} and auth['key-management'] == 'psk',
                    'INITIAL_WIFI_AUTH_REQUIRES_REVIEW')
            password = auth['password']
        else:
            password = config.get('password')
        require(isinstance(password, str), 'INITIAL_WIFI_PASSWORD_INVALID')
        if re.fullmatch(r'[A-Fa-f0-9]{64}', password):
            psk = password.lower()
        else:
            require(8 <= len(password) <= 63 and all(32 <= ord(c) <= 126 for c in password),
                    'INITIAL_WIFI_PASSWORD_INVALID')
            psk = hashlib.pbkdf2_hmac('sha1', password.encode('ascii'), ssid.encode('utf-8'),4096,32).hex()
        country = wifi.get('regulatory-domain')
        require(country is None or isinstance(country, str) and re.fullmatch(r'[A-Za-z]{2}|00',country),
                'INITIAL_WIFI_COUNTRY_INVALID')
        return {'ssid': ssid, 'psk_hex': psk, 'hidden': hidden,
                'regulatory_domain': country.upper() if country else None}
    except (KeyError, AttributeError, TypeError, UnicodeError):
        raise InstallError('NETPLAN_LAYOUT_REQUIRES_REVIEW') from None
