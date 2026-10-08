"""OS-owned, directory-scoped leases. Never delete a lock to recover ownership."""
from __future__ import annotations

import os
from pathlib import Path
import stat


class ProviderStorageError(RuntimeError):
    pass


def assert_database_identity(path: Path) -> None:
    """A directory lease cannot protect a DB shared through another file path."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return  # A runtime owner may create its new database.
    except OSError:
        raise ProviderStorageError("provider_storage_database_unavailable") from None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ProviderStorageError("provider_storage_database_unsafe")


class StorageLease:
    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()
        self.path = self.directory / ".provider-writer.lock"
        self.fd: int | None = None
        self.pid = os.getpid()

    def acquire(self, *, create: bool) -> None:
        if self.fd is not None:
            raise ProviderStorageError("provider_storage_already_open")
        if create:
            self.directory.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink():
            raise ProviderStorageError("provider_storage_lock_unsafe")
        flags = (os.O_RDWR | os.O_CREAT) if create else os.O_RDONLY
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError:
            raise ProviderStorageError("provider_storage_lock_unavailable") from None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ProviderStorageError("provider_storage_lock_unsafe")
            # Lock beyond EOF: no PID write, truncation or stale-file removal.
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.fd = descriptor
            self.assert_owned()
        except BaseException as exc:
            self.fd = None
            os.close(descriptor)
            if isinstance(exc, OSError):
                raise ProviderStorageError("provider_storage_writer_busy") from None
            raise

    def assert_owned(self) -> None:
        if self.fd is None or self.pid != os.getpid():
            raise ProviderStorageError("provider_storage_not_owned")
        try:
            current = self.path.lstat()
            held = os.fstat(self.fd)
            if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                    or (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino)):
                raise ProviderStorageError("provider_storage_lock_changed")
        except OSError:
            raise ProviderStorageError("provider_storage_lock_changed") from None

    def close(self) -> None:
        descriptor, self.fd = self.fd, None
        if descriptor is not None:
            # Closing the descriptor releases ownership. Do not issue LOCK_UN
            # from a fork child against its parent's shared open-file description.
            os.close(descriptor)

    def __del__(self) -> None:
        self.close()
