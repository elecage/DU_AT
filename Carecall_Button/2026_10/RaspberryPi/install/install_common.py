"""Small filesystem helpers for NEW installations; never replace existing files."""
from pathlib import Path
import os
import stat
import tempfile


class InstallError(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise InstallError(code)


def no_symlinks(path):
    path = Path(os.path.abspath(path))
    for item in [*reversed(path.parents), path]:
        require(not item.is_symlink(), 'SYMLINK_NOT_ALLOWED')
    return path


def checked_directory(path, uid, *, private=False):
    path = no_symlinks(path)
    st = path.stat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == uid, 'DIRECTORY_OWNER_INVALID')
    require(not st.st_mode & (0o077 if private else 0o022), 'DIRECTORY_PERMISSIONS_INVALID')
    return path


def private_directory(path, uid):
    path = no_symlinks(path)
    if not path.exists():
        path.mkdir(mode=0o700)
    return checked_directory(path, uid, private=True)


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_file(source, target):
    """Hard-link in the same filesystem: fails atomically if target exists."""
    target = no_symlinks(target)
    require(not os.path.lexists(target), 'TARGET_ALREADY_EXISTS')
    os.link(source, target, follow_symlinks=False)
    fsync_directory(target.parent)


def create_private_file(target, data, uid):
    target = no_symlinks(target)
    checked_directory(target.parent, uid, private=True)
    require(not os.path.lexists(target), 'TARGET_ALREADY_EXISTS')
    with tempfile.TemporaryDirectory(prefix='.carecall-new-', dir=target.parent) as temporary:
        source = Path(temporary) / 'new-file'
        fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        publish_file(source, target)
