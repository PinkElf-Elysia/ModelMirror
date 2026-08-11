from __future__ import annotations

import argparse
import ctypes
import os
import platform
import resource
from pathlib import Path


_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

_EXECUTE = 1 << 0
_WRITE_FILE = 1 << 1
_READ_FILE = 1 << 2
_READ_DIR = 1 << 3
_REMOVE_DIR = 1 << 4
_REMOVE_FILE = 1 << 5
_MAKE_CHAR = 1 << 6
_MAKE_DIR = 1 << 7
_MAKE_REG = 1 << 8
_MAKE_SOCK = 1 << 9
_MAKE_FIFO = 1 << 10
_MAKE_BLOCK = 1 << 11
_MAKE_SYM = 1 << 12
_REFER = 1 << 13
_TRUNCATE = 1 << 14

_READ_EXECUTE = _EXECUTE | _READ_FILE | _READ_DIR
_WORKSPACE_ACCESS = (
    _READ_EXECUTE
    | _WRITE_FILE
    | _REMOVE_DIR
    | _REMOVE_FILE
    | _MAKE_DIR
    | _MAKE_REG
    | _MAKE_SOCK
    | _MAKE_FIFO
    | _MAKE_SYM
    | _REFER
    | _TRUNCATE
)
_HANDLED_ACCESS = _WORKSPACE_ACCESS | _MAKE_CHAR | _MAKE_BLOCK


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
        ("reserved", ctypes.c_uint32),
    ]


def _syscall_numbers() -> tuple[int, int, int]:
    if platform.machine().lower() in {"x86_64", "amd64", "aarch64", "arm64"}:
        return 444, 445, 446
    raise RuntimeError("shell_sandbox_architecture_unsupported")


def _apply_landlock(
    repository: Path,
    home: Path,
    temporary: Path,
    *,
    repository_writable: bool,
) -> None:
    create_nr, add_nr, restrict_nr = _syscall_numbers()
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(create_nr, 0, 0, _LANDLOCK_CREATE_RULESET_VERSION)
    if abi < 1:
        raise RuntimeError("shell_sandbox_landlock_unavailable")
    supported = _HANDLED_ACCESS
    if abi < 2:
        supported &= ~_REFER
    if abi < 3:
        supported &= ~_TRUNCATE
    attr = _RulesetAttr(supported)
    ruleset_fd = libc.syscall(
        create_nr, ctypes.byref(attr), ctypes.sizeof(attr), 0
    )
    if ruleset_fd < 0:
        raise RuntimeError("shell_sandbox_ruleset_failed")

    def allow(path: Path, access: int) -> None:
        if not path.exists():
            return
        descriptor = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            rule = _PathBeneathAttr(access & supported, descriptor, 0)
            if (
                libc.syscall(
                    add_nr,
                    ruleset_fd,
                    _LANDLOCK_RULE_PATH_BENEATH,
                    ctypes.byref(rule),
                    0,
                )
                < 0
            ):
                raise RuntimeError("shell_sandbox_rule_failed")
        finally:
            os.close(descriptor)

    try:
        for path in (
            Path("/usr"),
            Path("/bin"),
            Path("/lib"),
            Path("/lib64"),
            Path("/etc"),
            Path("/opt/modelmirror"),
        ):
            allow(path, _READ_EXECUTE)
        for path in (Path("/dev/null"), Path("/dev/urandom"), Path("/dev/random")):
            allow(path, _READ_FILE | _WRITE_FILE)
        allow(repository, _WORKSPACE_ACCESS if repository_writable else _READ_EXECUTE)
        allow(home, _WORKSPACE_ACCESS)
        allow(temporary, _WORKSPACE_ACCESS)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise RuntimeError("shell_sandbox_no_new_privs_failed")
        if libc.syscall(restrict_nr, ruleset_fd, 0) != 0:
            raise RuntimeError("shell_sandbox_restrict_failed")
    finally:
        os.close(ruleset_fd)


def _apply_limits() -> None:
    limits = (
        (resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024)),
        (resource.RLIMIT_NOFILE, (256, 256)),
        (resource.RLIMIT_NPROC, (128, 128)),
    )
    for kind, value in limits:
        resource.setrlimit(kind, value)


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--temporary", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--repository-read-only", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    command = list(arguments.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        return 64
    try:
        repository = Path(arguments.repository).resolve(strict=True)
        home = Path(arguments.home).resolve(strict=True)
        temporary = Path(arguments.temporary).resolve(strict=True)
        cwd = Path(arguments.cwd).resolve(strict=True)
        if not cwd.is_relative_to(repository):
            return 64
        _apply_limits()
        _apply_landlock(
            repository,
            home,
            temporary,
            repository_writable=not arguments.repository_read_only,
        )
        os.chdir(cwd)
        os.execvpe(command[0], command, os.environ)
    except Exception:
        return 126
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
