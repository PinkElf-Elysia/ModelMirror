from __future__ import annotations

import ctypes
import os
import platform
import resource
import sys
from pathlib import Path


LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38

ACCESS_FS_EXECUTE = 1 << 0
ACCESS_FS_WRITE_FILE = 1 << 1
ACCESS_FS_READ_FILE = 1 << 2
ACCESS_FS_READ_DIR = 1 << 3
ACCESS_FS_REMOVE_DIR = 1 << 4
ACCESS_FS_REMOVE_FILE = 1 << 5
ACCESS_FS_MAKE_CHAR = 1 << 6
ACCESS_FS_MAKE_DIR = 1 << 7
ACCESS_FS_MAKE_REG = 1 << 8
ACCESS_FS_MAKE_SOCK = 1 << 9
ACCESS_FS_MAKE_FIFO = 1 << 10
ACCESS_FS_MAKE_BLOCK = 1 << 11
ACCESS_FS_MAKE_SYM = 1 << 12
ACCESS_FS_REFER = 1 << 13
ACCESS_FS_TRUNCATE = 1 << 14

READ_EXECUTE = ACCESS_FS_EXECUTE | ACCESS_FS_READ_FILE | ACCESS_FS_READ_DIR
WORKSPACE_ACCESS = (
    READ_EXECUTE
    | ACCESS_FS_WRITE_FILE
    | ACCESS_FS_REMOVE_DIR
    | ACCESS_FS_REMOVE_FILE
    | ACCESS_FS_MAKE_DIR
    | ACCESS_FS_MAKE_REG
    | ACCESS_FS_MAKE_SOCK
    | ACCESS_FS_MAKE_FIFO
    | ACCESS_FS_MAKE_SYM
    | ACCESS_FS_REFER
    | ACCESS_FS_TRUNCATE
)
HANDLED_ACCESS = WORKSPACE_ACCESS | ACCESS_FS_MAKE_CHAR | ACCESS_FS_MAKE_BLOCK


class RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class PathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
        ("reserved", ctypes.c_uint32),
    ]


def _syscall_numbers() -> tuple[int, int, int]:
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64", "aarch64", "arm64"}:
        return 444, 445, 446
    raise RuntimeError(f"Unsupported Landlock architecture: {machine}")


def _apply_landlock(
    workspace: Path,
    *,
    workspace_writable: bool = True,
    skill_evaluation: bool = False,
) -> None:
    create_nr, add_nr, restrict_nr = _syscall_numbers()
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(create_nr, 0, 0, LANDLOCK_CREATE_RULESET_VERSION)
    if abi < 1:
        errno = ctypes.get_errno()
        raise RuntimeError(f"Landlock is unavailable (errno={errno}).")

    supported = HANDLED_ACCESS
    if abi < 2:
        supported &= ~ACCESS_FS_REFER
    if abi < 3:
        supported &= ~ACCESS_FS_TRUNCATE
    ruleset_attr = RulesetAttr(supported)
    ruleset_fd = libc.syscall(
        create_nr,
        ctypes.byref(ruleset_attr),
        ctypes.sizeof(ruleset_attr),
        0,
    )
    if ruleset_fd < 0:
        errno = ctypes.get_errno()
        raise RuntimeError(f"Landlock ruleset creation failed (errno={errno}).")

    def add_path(path: Path, access: int) -> None:
        if not path.exists():
            return
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            attr = PathBeneathAttr(access & supported, fd, 0)
            result = libc.syscall(
                add_nr,
                ruleset_fd,
                LANDLOCK_RULE_PATH_BENEATH,
                ctypes.byref(attr),
                0,
            )
            if result < 0:
                errno = ctypes.get_errno()
                raise RuntimeError(f"Landlock rule failed for {path} (errno={errno}).")
        finally:
            os.close(fd)

    try:
        for path in (
            Path("/usr"),
            Path("/bin"),
            Path("/lib"),
            Path("/lib64"),
            Path("/etc"),
            Path("/opt/modelmirror"),
        ):
            add_path(path, READ_EXECUTE)
        for path in (Path("/dev/null"), Path("/dev/urandom"), Path("/dev/random")):
            add_path(path, ACCESS_FS_READ_FILE | ACCESS_FS_WRITE_FILE)
        if skill_evaluation:
            add_path(workspace / "inputs", READ_EXECUTE)
            add_path(workspace / "skills", READ_EXECUTE)
            add_path(workspace / "work", WORKSPACE_ACCESS)
            add_path(workspace / ".tmp", WORKSPACE_ACCESS)
        else:
            add_path(workspace, WORKSPACE_ACCESS if workspace_writable else READ_EXECUTE)
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            errno = ctypes.get_errno()
            raise RuntimeError(f"no_new_privs failed (errno={errno}).")
        if libc.syscall(restrict_nr, ruleset_fd, 0) != 0:
            errno = ctypes.get_errno()
            raise RuntimeError(f"Landlock restrict_self failed (errno={errno}).")
    finally:
        os.close(ruleset_fd)


def _apply_compute_limits() -> None:
    limits = (
        (resource.RLIMIT_CPU, (60, 60)),
        (resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024)),
        (resource.RLIMIT_FSIZE, (0, 0)),
        (resource.RLIMIT_NOFILE, (64, 64)),
    )
    for limit, value in limits:
        resource.setrlimit(limit, value)


def main() -> int:
    options: set[str] = set()
    separator_index = 2
    while separator_index < len(sys.argv) and sys.argv[separator_index] != "--":
        option = sys.argv[separator_index]
        if option not in {
            "--read-only",
            "--compute-limits",
            "--skill-evaluation",
            "--skill-authoring",
        }:
            print(f"unsupported sandbox option: {option}", file=sys.stderr)
            return 64
        options.add(option)
        separator_index += 1
    if len(sys.argv) <= separator_index or sys.argv[separator_index] != "--":
        print(
            "usage: landlock_exec.py WORKSPACE [--read-only] "
            "[--compute-limits] [--skill-evaluation|--skill-authoring] -- COMMAND [ARGS...]",
            file=sys.stderr,
        )
        return 64
    workspace = Path(sys.argv[1]).resolve(strict=True)
    command = sys.argv[separator_index + 1 :]
    if not command:
        print("command is required", file=sys.stderr)
        return 64
    try:
        if "--compute-limits" in options:
            _apply_compute_limits()
        _apply_landlock(
            workspace,
            workspace_writable="--read-only" not in options,
            skill_evaluation=bool(
                {"--skill-evaluation", "--skill-authoring"} & options
            ),
        )
    except Exception as exc:
        print(f"sandbox isolation failed: {exc}", file=sys.stderr)
        return 126
    os.chdir(workspace / "work")
    os.execvpe(command[0], command, os.environ)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
