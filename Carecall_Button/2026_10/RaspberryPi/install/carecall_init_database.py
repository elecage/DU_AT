#!/usr/bin/env python3
"""Create a NEW empty phase-2 database. Existing DBs are never migrated/replaced."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import argparse
import importlib
import os
import pwd
import sqlite3
import sys
import tempfile

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from install_common import (InstallError, require, no_symlinks, checked_directory,
                            private_directory, publish_file)

PROJECT = Path('/home/carecallserver/carecall_receiver')
CONFIRM = 'CREATE_EMPTY_CARECALL_DATABASE'
REGISTRY_SCHEMA = """
CREATE TABLE carecall_registry_meta (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 version INTEGER NOT NULL CHECK(version=2),
 phase TEXT NOT NULL CHECK(phase='set_calls_and_confirmations'),
 created_at TEXT NOT NULL
) STRICT;
CREATE TABLE carecall_sets (
 set_id TEXT PRIMARY KEY NOT NULL
 CHECK(length(set_id) BETWEEN 1 AND 32 AND set_id NOT GLOB '*[^A-Za-z0-9_-]*'),
 label TEXT NOT NULL CHECK(length(label) BETWEEN 1 AND 48),
 created_at TEXT NOT NULL
) STRICT;
CREATE TABLE carecall_devices (
 device_id TEXT PRIMARY KEY NOT NULL
 CHECK(length(device_id) BETWEEN 1 AND 32 AND device_id NOT GLOB '*[^A-Za-z0-9_-]*'),
 set_id TEXT NOT NULL REFERENCES carecall_sets(set_id),
 mqtt_username TEXT NOT NULL UNIQUE CHECK(mqtt_username=device_id),
 mqtt_client_id TEXT NOT NULL UNIQUE CHECK(mqtt_client_id='carecall-' || device_id),
 state TEXT NOT NULL CHECK(state IN ('active','staged')),
 created_at TEXT NOT NULL
) STRICT;
"""


def load_runtime(project):
    project = checked_directory(project, os.geteuid())
    for name in ('device_registry', 'event_database', 'notification_store',
                 'confirmation_store', 'guardian_store', 'operator_store'):
        path = no_symlinks(project / (name + '.py'))
        require(path.is_file(), 'RECEIVER_SOURCE_MISSING')
    sys.path.insert(0, str(project))
    names = ('device_registry', 'event_database', 'notification_store',
             'confirmation_store', 'operator_store')
    modules = [importlib.import_module(name) for name in names]
    require(all(Path(m.__file__).parent == project for m in modules), 'MIXED_RECEIVER_MODULES')
    return modules


def validate_new_database(database, devices, modules):
    registry = modules[0]
    with closing(sqlite3.connect(database)) as c:
        c.row_factory = sqlite3.Row
        require(c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'DB_INTEGRITY_FAILED')
        require(not c.execute('PRAGMA foreign_key_check').fetchall(), 'DB_FOREIGN_KEY_FAILED')
        registry.inspect(c)
        rows = c.execute('SELECT device_id,state FROM carecall_devices').fetchall()
        require({r['device_id'] for r in rows} == set(devices)
                and all(r['state'] == 'staged' for r in rows), 'DB_DEVICE_STATE_INVALID')
        allowed_rows = {'carecall_registry_meta': 1, 'carecall_sets': 1,
                        'carecall_devices': len(devices), 'guardian_cursor': 1}
        for (table,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            require(table.replace('_', '').isalnum(), 'DB_TABLE_INVALID')
            count = c.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0]
            require(count == allowed_rows.get(table, 0), 'DB_NOT_EMPTY')
        cursor = c.execute('SELECT last_update_id,last_update_at FROM guardian_cursor').fetchone()
        require(tuple(cursor) == (-1, 0), 'DB_CURSOR_NOT_INITIAL')


def create_database(project, set_id, label, devices, *, write=False):
    modules = load_runtime(project)
    registry, events, notifications, confirmations, operators = modules
    registry.identifier(set_id)
    require(bool(devices) and len(set(devices)) == len(devices), 'DEVICE_LIST_INVALID')
    for name in devices:
        registry.identifier(name)
    require('button01' in devices, 'BUTTON01_REQUIRED_BY_CURRENT_RUNTIME')
    require(isinstance(label, str) and 1 <= len(label) <= 48 and label.strip() == label
            and all(ch.isprintable() for ch in label), 'SET_LABEL_INVALID')
    require(sqlite3.sqlite_version_info >= (3, 37, 0), 'SQLITE_3_37_REQUIRED')
    project = no_symlinks(project)
    data = no_symlinks(project / 'data')
    target = data / 'carecall_events.db'
    for suffix in ('', '-wal', '-shm', '-journal'):
        require(not os.path.lexists(str(target) + suffix), 'DATABASE_OR_SIDECAR_ALREADY_EXISTS')
    if data.exists():
        checked_directory(data, os.geteuid(), private=True)
    if not write:
        return 'READY_NO_WRITE'
    private_directory(data, os.geteuid())
    with tempfile.TemporaryDirectory(prefix='.carecall-new-db-', dir=data) as temporary:
        database = Path(temporary) / 'carecall_events.db'
        fd = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(database)) as c:
            c.execute('PRAGMA foreign_keys=ON')
            c.executescript(REGISTRY_SCHEMA + events.SCHEMA_SQL +
                            notifications.OUTBOX_SCHEMA_SQL + confirmations.SCHEMA)
            stamp = datetime.now(timezone.utc).isoformat()
            c.execute('INSERT INTO carecall_registry_meta VALUES(1,2,?,?)', (registry.PHASE, stamp))
            c.execute('INSERT INTO carecall_sets VALUES(?,?,?)', (set_id, label, stamp))
            c.executemany('INSERT INTO carecall_devices VALUES(?,?,?,?,?,?)',
                          [(name, set_id, name, 'carecall-' + name, 'staged', stamp)
                           for name in devices])
            c.commit()
        # Existing runtime schema builders are used only on our new temporary DB.
        operators.OperatorStore(database).migrate()
        validate_new_database(database, devices, modules)
        with closing(sqlite3.connect(database, isolation_level=None)) as c:
            require(c.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal', 'WAL_REQUIRED')
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        # No connection remains open; never publish a DB depending on a sidecar.
        require(not Path(str(database) + '-wal').exists(), 'TEMP_WAL_STILL_PRESENT')
        with database.open('rb') as stream:
            os.fsync(stream.fileno())
        for suffix in ('', '-wal', '-shm', '-journal'):
            require(not os.path.lexists(str(target) + suffix), 'DATABASE_OR_SIDECAR_ALREADY_EXISTS')
        publish_file(database, target)
    return 'CREATED_EMPTY_DEVICES_STAGED'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=PROJECT)
    parser.add_argument('--set-id', default='set01')
    parser.add_argument('--label', default='CareCall set 01')
    parser.add_argument('--device', action='append', required=True)
    parser.add_argument('--create', action='store_true')
    parser.add_argument('--confirm')
    args = parser.parse_args()
    try:
        require(os.geteuid() == pwd.getpwnam('carecallserver').pw_uid,
                'RUN_AS_CARECALLSERVER')
        require((args.create and args.confirm == CONFIRM) or
                (not args.create and args.confirm is None), 'CREATE_CONFIRMATION_INVALID')
        print('DATABASE_INIT=' + create_database(args.project, args.set_id, args.label,
                                                 args.device, write=args.create))
        print('EXISTING_DATABASE_MODIFIED=NO')
        print('NETWORK_OR_SERVICES_CHANGED=NO')
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        code = str(exc) if isinstance(exc, InstallError) else type(exc).__name__
        print('DATABASE_INIT=FAILED CODE=' + code)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
