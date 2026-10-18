"""Serialize recipient deletion with both senders, without holding a DB transaction.

Every process that sends to a recipient or deletes a recipient must use this gate.
The existing receiver remains independent and may continue recording calls.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path


@contextmanager
def delivery_gate(database_path):
    path = Path(database_path).resolve().parent / '.recipient-delivery.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)
