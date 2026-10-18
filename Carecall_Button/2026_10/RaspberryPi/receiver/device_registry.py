"""Phase 2 device admission and set membership; no credential provisioning."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3

VERSION = 2
PHASE = 'set_calls_and_confirmations'
LEGACY_DEVICE = 'button01'

class RegistryError(RuntimeError):
    pass

def require(condition, code):
    if not condition:
        raise RegistryError(code)

def identifier(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,32}', value),
            'INVALID_IDENTIFIER')
    return value

def connect(database, *, readonly=False):
    path = Path(database).expanduser().absolute()
    require(path.is_file() and not path.is_symlink(), 'EXISTING_DATABASE_REQUIRED')
    c = sqlite3.connect(path.as_uri() + ('?mode=ro' if readonly else '?mode=rw'),
                        uri=True, timeout=5, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA query_only=ON' if readonly else 'PRAGMA synchronous=FULL')
    return c

def inspect(c):
    row = c.execute('SELECT version,phase FROM carecall_registry_meta WHERE singleton=1').fetchone()
    require(row is not None and tuple(row) == (VERSION, PHASE), 'PHASE_2_MIGRATION_REQUIRED')
    require(c.execute("SELECT 1 FROM carecall_devices WHERE device_id='button01'").fetchone(),
            'LEGACY_DEVICE_MISSING')
    return 'installed'

def default_set(c):
    inspect(c)
    return c.execute("SELECT set_id FROM carecall_devices WHERE device_id='button01'").fetchone()[0]

def active_devices(c):
    inspect(c)
    return frozenset(r[0] for r in c.execute("SELECT device_id FROM carecall_devices WHERE state='active'"))

def device_set(c, device_id):
    inspect(c)
    row = c.execute("SELECT set_id FROM carecall_devices WHERE device_id=? AND state='active'",
                    (device_id,)).fetchone()
    require(row is not None, 'DEVICE_NOT_ACTIVE')
    return row[0]

def add_set(database, set_id, label):
    identifier(set_id)
    require(isinstance(label, str) and 1 <= len(label) <= 48 and label.strip() == label
            and all(c.isprintable() for c in label), 'INVALID_SET_LABEL')
    with closing(connect(database)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        inspect(c)
        old = c.execute('SELECT label FROM carecall_sets WHERE set_id=?', (set_id,)).fetchone()
        if old:
            require(old[0] == label, 'SET_ALREADY_EXISTS_WITH_DIFFERENT_LABEL')
            return 'already_registered'
        c.execute('INSERT INTO carecall_sets VALUES(?,?,?)',
                  (set_id, label, datetime.now(timezone.utc).isoformat()))
        return 'registered'

def stage_device(database, device_id, set_id):
    identifier(device_id); identifier(set_id)
    with closing(connect(database)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        inspect(c)
        require(c.execute('SELECT 1 FROM carecall_sets WHERE set_id=?', (set_id,)).fetchone(),
                'SET_NOT_REGISTERED')
        old = c.execute('SELECT set_id FROM carecall_devices WHERE device_id=?', (device_id,)).fetchone()
        if old:
            require(old[0] == set_id, 'DEVICE_ALREADY_ASSIGNED_TO_ANOTHER_SET')
            return 'already_registered'
        c.execute('INSERT INTO carecall_devices VALUES(?,?,?,?,?,?)',
                  (device_id,set_id,device_id,'carecall-'+device_id,'staged',
                   datetime.now(timezone.utc).isoformat()))
        return 'staged'

def activate_device(database, device_id):
    identifier(device_id)
    with closing(connect(database)) as c, c:
        c.execute('BEGIN IMMEDIATE')
        inspect(c)
        row = c.execute('SELECT state FROM carecall_devices WHERE device_id=?', (device_id,)).fetchone()
        require(row is not None, 'STAGE_DEVICE_FIRST')
        c.execute("UPDATE carecall_devices SET state='active' WHERE device_id=?", (device_id,))
        return 'active'

def status(database):
    with closing(connect(database, readonly=True)) as c, c:
        c.execute('BEGIN')
        inspect(c)
        devices = [dict(r) for r in c.execute('SELECT device_id,set_id,state,mqtt_username,mqtt_client_id FROM carecall_devices ORDER BY device_id')]
        return {'phase':PHASE, 'registry':'installed', 'multi_button_server_enabled':True,
                'additional_button_calls_enabled':any(d['device_id'] != LEGACY_DEVICE and d['state']=='active' for d in devices),
                'sets':[dict(r) for r in c.execute('SELECT set_id,label FROM carecall_sets ORDER BY set_id')],
                'devices':devices}
