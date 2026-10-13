"""Process-lifetime ownership of a single local Runtime storage directory."""
from __future__ import annotations

import atexit
import os
from pathlib import Path
import stat


class RuntimeDirectoryError(RuntimeError):
    """Safe failure: never include a storage path or underlying OS message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}：{message}")
        self.code = code


class RuntimeDirectoryOwner:
    def __init__(self, fd: int, path: Path) -> None:
        self._fd: int | None = fd
        self._path = path
        self._pid = os.getpid()

    @classmethod
    def acquire(cls, storage_dir: str | Path | None = None) -> RuntimeDirectoryOwner:
        fd = None
        acquired = False
        try:
            root = Path(storage_dir or os.getenv("AGENT_TASK_STORAGE_DIR", "").strip()
                        or Path(__file__).resolve().parent / "storage").resolve()
            root.mkdir(parents=True, exist_ok=True)
            path = root / ".runtime-owner.lock"
            try:
                before = path.lstat()
            except FileNotFoundError:
                before = None
            if before is not None and (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1):
                raise OSError("unsafe lock file")
            fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_CLOEXEC", 0), 0o600)
            os.set_inheritable(fd, False)
            actual = os.fstat(fd)
            if (not stat.S_ISREG(actual.st_mode) or actual.st_nlink != 1
                or not os.path.samestat(actual, path.lstat())):
                raise OSError("lock identity changed")
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeDirectoryError(
                    "RUNTIME_DIRECTORY_BUSY", "运行目录已被占用或无法取得独占锁；请停止原后端后重试，不要删除锁文件。",
                ) from None
            owner = cls(fd, path)
            owner.assert_owned()
            atexit.register(owner.close)
            acquired = True
            return owner
        except (OSError, ValueError):
            raise RuntimeDirectoryError(
                "RUNTIME_DIRECTORY_UNAVAILABLE", "无法安全取得运行目录所有权，已停止加载运行状态。",
            ) from None
        finally:
            # Only a returned owner keeps the descriptor alive.
            if fd is not None and not acquired:
                os.close(fd)

    def assert_owned(self) -> None:
        try:
            if self._fd is None or os.getpid() != self._pid:
                raise OSError("not the owning process")
            current = self._path.lstat()
            if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                or not os.path.samestat(os.fstat(self._fd), current)):
                raise OSError("lock identity changed")
        except OSError:
            raise RuntimeDirectoryError(
                "RUNTIME_DIRECTORY_OWNERSHIP_LOST", "运行目录所有权无效；禁止继承旧进程句柄或继续启动后台执行。",
            ) from None

    def close(self) -> None:
        fd, self._fd = self._fd, None
        atexit.unregister(self.close)
        if fd is not None:
            # Closing releases OS ownership, including on abnormal process exit.
            # Never unlink: another process may already be waiting on this inode.
            os.close(fd)

    def __enter__(self) -> RuntimeDirectoryOwner:
        self.assert_owned()
        return self

    def __exit__(self, *_args) -> None:
        self.close()
