"""Landlock and rlimit launcher for one Wave-5 database MCP process."""

from __future__ import annotations

import ctypes
import os
import platform
import resource
import sys
from pathlib import Path

from .landlock_exec import (
    ACCESS_FS_EXECUTE,
    ACCESS_FS_MAKE_BLOCK,
    ACCESS_FS_MAKE_CHAR,
    ACCESS_FS_MAKE_DIR,
    ACCESS_FS_MAKE_FIFO,
    ACCESS_FS_MAKE_REG,
    ACCESS_FS_MAKE_SOCK,
    ACCESS_FS_MAKE_SYM,
    ACCESS_FS_READ_DIR,
    ACCESS_FS_READ_FILE,
    ACCESS_FS_REFER,
    ACCESS_FS_REMOVE_DIR,
    ACCESS_FS_REMOVE_FILE,
    ACCESS_FS_TRUNCATE,
    ACCESS_FS_WRITE_FILE,
    LANDLOCK_CREATE_RULESET_VERSION,
    LANDLOCK_RULE_PATH_BENEATH,
    PR_SET_NO_NEW_PRIVS,
    PathBeneathAttr,
    RulesetAttr,
)


READ_EXECUTE = ACCESS_FS_EXECUTE | ACCESS_FS_READ_FILE | ACCESS_FS_READ_DIR
WRITE_ACCESS = (
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
HANDLED_ACCESS = WRITE_ACCESS | ACCESS_FS_MAKE_CHAR | ACCESS_FS_MAKE_BLOCK


def _syscalls() -> tuple[int, int, int]:
    if platform.machine().lower() in {"x86_64", "amd64", "aarch64", "arm64"}:
        return 444, 445, 446
    raise RuntimeError("Unsupported Landlock architecture.")


def _apply(read_roots: list[Path], write_roots: list[Path]) -> None:
    create_nr, add_nr, restrict_nr = _syscalls()
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(create_nr, 0, 0, LANDLOCK_CREATE_RULESET_VERSION)
    if abi < 1:
        raise RuntimeError("Landlock is unavailable.")
    supported = HANDLED_ACCESS
    if abi < 2:
        supported &= ~ACCESS_FS_REFER
    if abi < 3:
        supported &= ~ACCESS_FS_TRUNCATE
    ruleset_fd = libc.syscall(
        create_nr,
        ctypes.byref(RulesetAttr(supported)),
        ctypes.sizeof(RulesetAttr),
        0,
    )
    if ruleset_fd < 0:
        raise RuntimeError("Landlock ruleset creation failed.")

    def add(path: Path, access: int) -> None:
        if not path.exists():
            return
        descriptor = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            attribute = PathBeneathAttr(access & supported, descriptor, 0)
            if libc.syscall(
                add_nr,
                ruleset_fd,
                LANDLOCK_RULE_PATH_BENEATH,
                ctypes.byref(attribute),
                0,
            ) < 0:
                raise RuntimeError("Landlock path rule failed.")
        finally:
            os.close(descriptor)

    try:
        for root in read_roots:
            add(root, READ_EXECUTE)
        for root in write_roots:
            add(root, WRITE_ACCESS)
        for path in (Path("/dev/null"), Path("/dev/urandom"), Path("/dev/random")):
            add(path, ACCESS_FS_READ_FILE | ACCESS_FS_WRITE_FILE)
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise RuntimeError("no_new_privs failed.")
        if libc.syscall(restrict_nr, ruleset_fd, 0) != 0:
            raise RuntimeError("Landlock restrict_self failed.")
    finally:
        os.close(ruleset_fd)


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] != "--":
        print("usage: database_landlock_exec.py -- COMMAND [ARGS...]", file=sys.stderr)
        return 64
    adapter_id = os.getenv("MCP_DATABASE_ADAPTER_ID", "")
    workspace_id = os.getenv("MCP_DATABASE_WORKSPACE_ID", "")
    input_base = Path(os.getenv("MCP_DATABASE_INPUT_ROOT", "/inputs")).resolve()
    temp_root = Path(os.getenv("TMPDIR", "/tmp")).resolve()
    read_roots = [
        Path("/usr"),
        Path("/bin"),
        Path("/lib"),
        Path("/lib64"),
        Path("/etc"),
        Path("/opt/modelmirror"),
    ]
    if adapter_id == "duckdb-mcp":
        input_root = (input_base / workspace_id).resolve()
        if input_root.parent != input_base or not input_root.is_dir():
            print("database workspace unavailable", file=sys.stderr)
            return 126
        read_roots.append(input_root)
    try:
        temp_root.mkdir(parents=True, exist_ok=True)
        resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
        resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
        _apply(read_roots, [temp_root])
    except Exception as exc:
        print(f"database sandbox isolation failed: {type(exc).__name__}", file=sys.stderr)
        return 126
    os.chdir(temp_root)
    os.execvpe(sys.argv[2], sys.argv[2:], os.environ)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
