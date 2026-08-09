"""Isolated real-container acceptance smoke for Wave 7 browser adapters.

This host-side harness never calls Compose and never addresses a shared-stack
resource. Every Docker object uses a random name below the fixed
``mm-wave7-runtime-smoke-`` prefix and is removed explicitly in ``finally``.
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final


PREFIX: Final = "mm-wave7-runtime-smoke"
ADAPTERS: Final = ("chrome-devtools-mcp", "playwright-mcp")
ADAPTER_TOKENS: Final = {
    "chrome-devtools-mcp": "cdp",
    "playwright-mcp": "pw",
}
FIXTURE_HOST: Final = "fixture.wave7.test"
TIMEOUT_FIXTURE_PATH: Final = "/__modelmirror_timeout"
TIMEOUT_FIXTURE_DELAY_SECONDS: Final = 25
TIMEOUT_RUNTIME_EVENTS: Final = (
    '"browser_runtime_event":"rpc_timeout"',
    '"browser_runtime_event":"upstream_result_upstream_timeout"',
)
RESOURCE_NAME = re.compile(r"^mm-wave7-runtime-smoke-[0-9a-f]{8}(?:-[a-z0-9-]+)?$")


FIXTURE_CODE = r'''
import http.server
import os
import time

TIMEOUT_PATH = os.environ["MM_WAVE7_TIMEOUT_PATH"]
TIMEOUT_DELAY_SECONDS = int(os.environ["MM_WAVE7_TIMEOUT_DELAY_SECONDS"])

HTML = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Wave 7 Fixture</title></head>
<body>
  <label for="wave7-input">Wave 7 text</label>
  <input id="wave7-input" aria-label="Wave 7 text" autocomplete="off">
  <button id="wave7-button" type="button"
    onclick="document.getElementById('wave7-result').textContent='clicked:'+document.getElementById('wave7-input').value">
    Apply
  </button>
  <output id="wave7-result" aria-live="polite">idle</output>
</body></html>"""

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.partition("?")[0]
        time.sleep(TIMEOUT_DELAY_SECONDS if path == TIMEOUT_PATH else 3)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(HTML)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(HTML)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_args):
        return

http.server.ThreadingHTTPServer(("0.0.0.0", 80), Handler).serve_forever()
'''


CAPABILITY_RESTART_PROBE_CODE = r'''
import asyncio
import json
import secrets
from pathlib import Path

SOCKET = Path("/run/modelmirror-browser-egress/browser-egress.sock")
CONTROL = SOCKET.with_name("browser-egress.control")
URL = "http://fixture.wave7.test/"

async def request(payload, timeout=10):
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(str(SOCKET)), timeout=timeout
    )
    writer.write(json.dumps(payload, separators=(",", ":")).encode() + b"\n")
    await writer.drain()
    raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass
    if not raw:
        raise RuntimeError("egress probe received EOF")
    return json.loads(raw.decode())

async def wait_generation(old_key=None):
    for _ in range(300):
        try:
            key = CONTROL.read_text(encoding="ascii").strip()
            if len(key) == 64 and key != old_key:
                health = await request({"action": "health"}, timeout=2)
                if health.get("ok") is True:
                    return key
        except (OSError, asyncio.TimeoutError, ConnectionError, ValueError):
            pass
        await asyncio.sleep(0.1)
    raise RuntimeError("egress generation did not become ready")

async def main():
    old_key = await wait_generation()
    capability = secrets.token_hex(32)
    registered = await request({
        "action": "register", "control_key": old_key, "capability": capability,
    })
    if registered.get("ok") is not True:
        raise RuntimeError("old capability registration failed")
    authorized = await request({
        "action": "authorize", "control_key": old_key,
        "capability": capability, "url": URL,
    })
    if authorized.get("ok") is not True:
        raise RuntimeError("old capability authorization failed")
    stopped = await request({"action": "shutdown", "control_key": old_key})
    if stopped.get("ok") is not True:
        raise RuntimeError("authenticated egress shutdown failed")
    await wait_generation(old_key)
    old_control = await request({"action": "watch", "control_key": old_key})
    if old_control.get("code") != "browser_egress_control_denied":
        raise RuntimeError("old egress control key was accepted")
    old_capability = await request({
        "action": "connect", "capability": capability, "url": URL,
    })
    if old_capability.get("code") != "browser_egress_capability_denied":
        raise RuntimeError("old egress capability was accepted")
    print("egress_generation=rotated old_control=denied old_capability=denied", flush=True)

asyncio.run(main())
'''


def _bounded_container_args(
    *, name: str, user: str, network: str, label: str,
    pids: int, memory: str, cpus: str,
) -> list[str]:
    return [
        "--name", name,
        "--label", f"com.modelmirror.wave7-runtime-smoke={label}",
        "--network", network,
        "--user", user,
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--pids-limit", str(pids),
        "--memory", memory,
        "--cpus", cpus,
        "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=33554432,uid=65532,gid=65532,mode=0700",
    ]


def _create_network(
    runner: DockerRunner, ledger: ResourceLedger, base: str, seed: int,
) -> tuple[str, str]:
    name = _resource(base, "net")
    _reserve_resource(runner, ledger, "network", name)
    for offset in range(256):
        octet = (seed + offset) % 256
        subnet = f"198.18.{octet}.0/24"
        result = runner.run(
            "network", "create", "--driver", "bridge", "--subnet", subnet,
            name, check=False,
        )
        if result.returncode == 0:
            return name, f"198.18.{octet}.10"
        diagnostic = (result.stderr or result.stdout).lower()
        if "overlap" not in diagnostic and "pool" not in diagnostic:
            raise SmokeFailure(f"isolated network creation failed: {diagnostic[-2048:]}")
    raise SmokeFailure("no isolated synthetic DNS subnet was available")


def _create_volume(
    runner: DockerRunner,
    ledger: ResourceLedger,
    name: str,
    *,
    tmpfs: bool = False,
) -> None:
    _reserve_resource(runner, ledger, "volume", name)
    arguments = ["volume", "create"]
    if tmpfs:
        arguments.extend(
            [
                "--driver", "local", "--opt", "type=tmpfs",
                "--opt", "device=tmpfs",
                "--opt", "o=size=67108864,uid=65532,gid=65532,mode=0700,nosuid,nodev,noexec",
            ]
        )
    runner.run(*arguments, name)


def _start_fixture(
    runner: DockerRunner,
    ledger: ResourceLedger,
    *,
    base: str,
    image: str,
    network: str,
    address: str,
) -> str:
    name = _resource(base, "fixture")
    _reserve_resource(runner, ledger, "container", name)
    runner.run(
        "run", "-d",
        *_bounded_container_args(
            name=name, user="65532:65532", network=network, label=base,
            pids=32, memory="128m", cpus="0.25",
        ),
        "--ip", address,
        "--network-alias", FIXTURE_HOST,
        "--env", f"MM_WAVE7_TIMEOUT_PATH={TIMEOUT_FIXTURE_PATH}",
        "--env", f"MM_WAVE7_TIMEOUT_DELAY_SECONDS={TIMEOUT_FIXTURE_DELAY_SECONDS}",
        image, "python", "-c", FIXTURE_CODE,
    )
    _assert_common_security(
        _inspect(runner, name), user="65532:65532", network_mode=network
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        ready = runner.run(
            "exec", name, "python", "-c",
            "import socket; s=socket.create_connection(('127.0.0.1',80),2); s.close()",
            check=False, timeout=5,
        )
        if ready.returncode == 0:
            return name
        time.sleep(0.2)
    logs = runner.run("logs", name, check=False).stdout[-2048:]
    raise SmokeFailure(f"synthetic fixture did not bind port 80: {logs}")


def _wait_log(
    runner: DockerRunner, name: str, marker: str, *, timeout: int = 90,
) -> str:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        result = runner.run("logs", name, check=False, timeout=10)
        last = f"{result.stdout}\n{result.stderr}"
        if marker in last:
            return last
        inspect = runner.run("inspect", name, check=False, timeout=10)
        if inspect.returncode != 0:
            break
        state = json.loads(inspect.stdout)[0]["State"]
        if state.get("Status") == "exited":
            # Docker can report the exit before the json-file log driver has
            # exposed the final buffered lines. Wait for the final status,
            # then read once more so a successful cleanup marker is not lost.
            runner.run("wait", name, check=False, timeout=10)
            final_logs = runner.run("logs", name, check=False, timeout=10)
            last = f"{final_logs.stdout}\n{final_logs.stderr}"
            if marker in last:
                return last
            break
        time.sleep(0.2)
    raise SmokeFailure(
        f"{name} did not emit {marker}: "
        f"state={json.dumps(_safe_container_state(runner, name), separators=(',', ':'))} "
        f"logs={_safe_log_tail(last)}"
    )


def _wait_exit(runner: DockerRunner, name: str, *, timeout: int = 90) -> None:
    result = runner.run("wait", name, timeout=timeout)
    try:
        code = int(result.stdout.strip())
    except ValueError as exc:
        raise SmokeFailure(f"invalid docker wait status for {name}") from exc
    if code != 0:
        logs = runner.run("logs", name, check=False).stdout[-4096:]
        raise SmokeFailure(f"{name} exited with {code}: {logs}")


def _run_seccomp_calibration(
    runner: DockerRunner, ledger: ResourceLedger, *, base: str, image: str,
) -> None:
    name = _resource(base, "seccomp-control")
    _reserve_resource(runner, ledger, "container", name)
    runner.run(
        "run", "-d",
        *_bounded_container_args(
            name=name, user="65532:65532", network="none", label=base,
            pids=16, memory="96m", cpus="0.25",
        ),
        "--security-opt", "seccomp=unconfined",
        image, "python", "-c", SECCOMP_PROBE_CODE, "allow",
    )
    payload = _inspect(runner, name)
    _assert_common_security(payload, user="65532:65532", network_mode="none")
    host = payload["HostConfig"]
    security = set(host.get("SecurityOpt") or [])
    if "seccomp=unconfined" not in security or not any(
        item.startswith("no-new-privileges") for item in security
    ):
        raise SmokeFailure("calibration container security options drifted")
    if payload.get("Mounts") or host.get("Binds") or host.get("Devices"):
        raise SmokeFailure("calibration container unexpectedly has host access")
    logs = _wait_log(runner, name, '"mode":"allow"', timeout=30)
    print(next(line for line in logs.splitlines() if '"mode":"allow"' in line))
    _wait_exit(runner, name, timeout=30)


def _wait_path(
    runner: DockerRunner, container: str, path: str, *, timeout: int = 30,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = runner.run(
            "exec", container, "python", "-c",
            f"from pathlib import Path; assert Path({path!r}).exists()",
            check=False, timeout=5,
        )
        if result.returncode == 0:
            return
        time.sleep(0.2)
    raise SmokeFailure(f"{container} did not publish {path}")


class SmokeFailure(RuntimeError):
    """A fail-closed runtime acceptance failure."""


@dataclass
class DockerRunner:
    timeout: int = 120

    def run(
        self,
        *arguments: str,
        check: bool = True,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["docker", *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or self.timeout,
        )
        if check and completed.returncode != 0:
            diagnostic = (completed.stderr or completed.stdout)[-4096:]
            raise SmokeFailure(
                f"docker {arguments[0]} failed with {completed.returncode}: {diagnostic}"
            )
        return completed


@dataclass
class ResourceLedger:
    runner: DockerRunner
    containers: list[str] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)

    def cleanup(self) -> None:
        failures: list[str] = []
        for name in reversed(self.containers):
            self.runner.run("rm", "-f", name, check=False, timeout=30)
            if _object_present(self.runner, "container", name):
                failures.append(f"container:{name}")
        for name in reversed(self.volumes):
            self.runner.run("volume", "rm", "-f", name, check=False, timeout=30)
            if _object_present(self.runner, "volume", name):
                failures.append(f"volume:{name}")
        for name in reversed(self.networks):
            self.runner.run("network", "rm", name, check=False, timeout=30)
            if _object_present(self.runner, "network", name):
                failures.append(f"network:{name}")
        if failures:
            raise SmokeFailure(
                "runtime smoke cleanup left exact resources: " + ",".join(failures)
            )


def _resource(base: str, suffix: str) -> str:
    name = f"{base}-{suffix}"
    if not RESOURCE_NAME.fullmatch(name):
        raise SmokeFailure(f"unsafe smoke resource name: {name}")
    return name


def _object_present(runner: DockerRunner, kind: str, name: str) -> bool:
    result = runner.run(kind, "inspect", name, check=False, timeout=10)
    if result.returncode == 0:
        return True
    diagnostic = f"{result.stdout}\n{result.stderr}".lower()
    if "no such" in diagnostic or "not found" in diagnostic:
        return False
    raise SmokeFailure(f"could not verify {kind} {name} absence: {diagnostic[-2048:]}")


def _reserve_resource(
    runner: DockerRunner,
    ledger: ResourceLedger,
    kind: str,
    name: str,
) -> None:
    if _object_present(runner, kind, name):
        raise SmokeFailure(f"runtime smoke {kind} already exists: {name}")
    targets = {
        "container": ledger.containers,
        "volume": ledger.volumes,
        "network": ledger.networks,
    }
    if kind not in targets:
        raise SmokeFailure(f"unsupported runtime smoke resource kind: {kind}")
    targets[kind].append(name)


def _inspect(runner: DockerRunner, name: str) -> dict[str, Any]:
    payload = json.loads(runner.run("inspect", name).stdout)
    if not isinstance(payload, list) or len(payload) != 1:
        raise SmokeFailure(f"unexpected inspect payload for {name}")
    return payload[0]


def _assert_common_security(
    payload: dict[str, Any], *, user: str, network_mode: str,
    cap_add: frozenset[str] = frozenset(),
) -> None:
    host = payload["HostConfig"]
    config = payload["Config"]
    if config.get("User") != user:
        raise SmokeFailure(f"unexpected container user: {config.get('User')}")
    if host.get("NetworkMode") != network_mode:
        raise SmokeFailure(f"unexpected network mode: {host.get('NetworkMode')}")
    if host.get("ReadonlyRootfs") is not True or host.get("Privileged") is not False:
        raise SmokeFailure("container root or privilege boundary is unsafe")
    actual_cap_add = {
        str(value).removeprefix("CAP_") for value in (host.get("CapAdd") or [])
    }
    if set(host.get("CapDrop") or []) != {"ALL"} or actual_cap_add != set(cap_add):
        raise SmokeFailure("container capability boundary is unsafe")
    security = set(host.get("SecurityOpt") or [])
    if not any(item.startswith("no-new-privileges") for item in security):
        raise SmokeFailure("no-new-privileges is missing")
    if int(host.get("PidsLimit") or 0) <= 0 or int(host.get("Memory") or 0) <= 0:
        raise SmokeFailure("container process or memory limit is missing")
    if int(host.get("NanoCpus") or 0) <= 0:
        raise SmokeFailure("container CPU limit is missing")
    forbidden_targets = {"/var/run/docker.sock", "/run/docker.sock"}
    if any(mount.get("Destination") in forbidden_targets for mount in payload.get("Mounts", [])):
        raise SmokeFailure("Docker socket mount is forbidden")
    if any(mount.get("Type") == "bind" for mount in payload.get("Mounts", [])):
        raise SmokeFailure("host bind mounts are forbidden")
    if host.get("Devices"):
        raise SmokeFailure("device access is forbidden")


def _assert_browser_procfs_boundary(payload: dict[str, Any]) -> None:
    host = payload["HostConfig"]
    readonly = set(host.get("ReadonlyPaths") or [])
    masked = set(host.get("MaskedPaths") or [])
    required_readonly = {
        "/proc/bus", "/proc/fs", "/proc/irq", "/proc/sys",
        "/proc/sysrq-trigger",
    }
    required_masked = {
        "/proc/kcore", "/proc/keys", "/proc/timer_list",
    }
    if not required_readonly.issubset(readonly):
        raise SmokeFailure("browser procfs read-only paths are incomplete")
    if not required_masked.issubset(masked):
        raise SmokeFailure("browser procfs masked paths are incomplete")
    security = set(host.get("SecurityOpt") or [])
    if any("systempaths=unconfined" in item for item in security):
        raise SmokeFailure("browser procfs system paths are unconfined")


def _state(payload: dict[str, Any]) -> tuple[int, str, int, str]:
    state = payload["State"]
    return (
        int(payload.get("RestartCount") or 0),
        str(state.get("StartedAt") or ""),
        int(state.get("Pid") or 0),
        str(payload.get("Id") or ""),
    )


SECCOMP_PROBE_CODE = r'''
import ctypes
import errno
import json
import os
import subprocess
import sys
import time

EXPECT = sys.argv[1]
if EXPECT == "allow":
    time.sleep(2)

child_code = r"""
import ctypes, os, time
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(4, 1, 0, 0, 0) != 0 or libc.prctl(3, 0, 0, 0, 0) != 1:
    raise SystemExit(21)
buffer = ctypes.create_string_buffer(b'wave7-seccomp-buffer')
print(f'{os.getpid()} {ctypes.addressof(buffer)} {len(buffer.raw)}', flush=True)
time.sleep(30)
"""

class IOVec(ctypes.Structure):
    _fields_ = [("iov_base", ctypes.c_void_p), ("iov_len", ctypes.c_size_t)]

child = subprocess.Popen(
    [sys.executable, "-c", child_code], stdout=subprocess.PIPE,
    stderr=subprocess.PIPE, text=True,
)
try:
    line = child.stdout.readline().strip()
    child_pid, remote_address, size = map(int, line.split())
    os.kill(child_pid, 0)
    libc = ctypes.CDLL(None, use_errno=True)
    local_read = ctypes.create_string_buffer(size)
    local_write = ctypes.create_string_buffer(b"wave7-seccomp-write".ljust(size, b"!"))
    read_local = IOVec(ctypes.cast(local_read, ctypes.c_void_p), size)
    read_remote = IOVec(ctypes.c_void_p(remote_address), size)
    write_local = IOVec(ctypes.cast(local_write, ctypes.c_void_p), size)
    write_remote = IOVec(ctypes.c_void_p(remote_address), size)

    ctypes.set_errno(0)
    read_result = libc.process_vm_readv(
        child_pid, ctypes.byref(read_local), 1, ctypes.byref(read_remote), 1, 0
    )
    read_errno = ctypes.get_errno()
    ctypes.set_errno(0)
    write_result = libc.process_vm_writev(
        child_pid, ctypes.byref(write_local), 1, ctypes.byref(write_remote), 1, 0
    )
    write_errno = ctypes.get_errno()
    ctypes.set_errno(0)
    ptrace_result = libc.ptrace(16, child_pid, None, None)
    ptrace_errno = ctypes.get_errno()
    if ptrace_result == 0:
        os.waitpid(child_pid, 0)
        libc.ptrace(17, child_pid, None, None)

    if EXPECT == "allow":
        if not (read_result == size and write_result == size and ptrace_result == 0):
            raise RuntimeError(
                f"calibration failed: read={read_result}/{read_errno} "
                f"write={write_result}/{write_errno} ptrace={ptrace_result}/{ptrace_errno}"
            )
    elif EXPECT == "deny":
        denied = (
            (read_result, read_errno), (write_result, write_errno),
            (ptrace_result, ptrace_errno),
        )
        if not all(result == -1 and error == errno.EPERM for result, error in denied):
            raise RuntimeError(f"seccomp denial mismatch: {denied}")
        syscall_numbers = {
            "x86_64": {
                "mount": 165, "pivot_root": 155, "open_by_handle_at": 304,
                "pidfd_getfd": 438, "bpf": 321, "keyctl": 250,
                "userfaultfd": 323, "io_uring_setup": 425,
            },
            "aarch64": {
                "mount": 40, "pivot_root": 41, "open_by_handle_at": 265,
                "pidfd_getfd": 438, "bpf": 280, "keyctl": 219,
                "userfaultfd": 282, "io_uring_setup": 425,
            },
        }.get(os.uname().machine)
        if syscall_numbers is None:
            raise RuntimeError("unsupported seccomp probe architecture")
        dangerous = {}
        for name, number in syscall_numbers.items():
            ctypes.set_errno(0)
            result = libc.syscall(number, -1, 0, 0, 0, 0, 0)
            error = ctypes.get_errno()
            if result != -1 or error not in {errno.EPERM, errno.EACCES, errno.ENOSYS}:
                raise RuntimeError(
                    f"dangerous syscall was not denied: {name}={result}/{error}"
                )
            dangerous[name] = "denied"
    else:
        raise RuntimeError("unknown seccomp probe expectation")
    ptrace_scope = open("/proc/sys/kernel/yama/ptrace_scope", encoding="ascii").read().strip()
    print(json.dumps({
        "seccomp_probe": "ok", "mode": EXPECT, "dumpable_child": True,
        "valid_remote_buffer": True, "ptrace_scope": ptrace_scope,
        "dangerous_syscalls": dangerous if EXPECT == "deny" else {},
    }, separators=(",", ":")), flush=True)
    if EXPECT == "allow":
        time.sleep(2)
finally:
    child.terminate()
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=3)
'''


CHROMIUM_PROBE_CODE = r'''
import json
import os
from pathlib import Path

rows = []
for entry in Path("/proc").iterdir():
    if not entry.name.isdigit():
        continue
    try:
        command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        continue
    executable = ""
    try:
        executable = os.readlink(entry / "exe")
    except OSError:
        pass
    argv0 = command.split(" ", 1)[0]
    if (
        "chrome-linux64/chrome" in executable
        or executable.endswith("modelmirror-chromium")
        or argv0.endswith("modelmirror-chromium")
        or os.path.basename(argv0) == "chrome"
    ):
        rows.append((entry, command))
if not rows:
    raise RuntimeError("no real Chromium process was observed")
forbidden = ("--no-sandbox", "--disable-web-security", "--remote-debugging-address=0.0.0.0")
if any(token in command for _, command in rows for token in forbidden):
    raise RuntimeError("forbidden Chromium argument observed")
required_features = (
    "AutofillServerCommunication",
    "NetworkTimeServiceQuerying",
    "PreconnectToSearch",
    "NoSearchDomainCheck",
)
if any(not any(token in command for _, command in rows) for token in required_features):
    raise RuntimeError("required Chromium network guard was not observed")
# docker exec joins the container's initial user namespace.  PID 1 is
# deliberately non-dumpable, so reading its proc namespace can be denied even
# to the same UID; the probe's own namespace is the equivalent safe baseline.
container_user = os.readlink("/proc/self/ns/user")
userns_children = 0
for entry, _ in rows:
    try:
        if os.readlink(entry / "ns/user") != container_user:
            userns_children += 1
    except OSError:
        pass
if userns_children < 1:
    raise RuntimeError("Chromium user namespace sandbox was not observed")
print(json.dumps({
    "chromium_processes": len(rows), "userns_sandboxed": userns_children,
    "no_sandbox": False, "disable_web_security": False,
    "remote_debug_all": False,
    "autofill_server_disabled": True, "network_time_disabled": True,
    "search_preconnect_disabled": True, "search_domain_check_disabled": True,
}, separators=(",", ":")), flush=True)
'''


PROCESS_SNAPSHOT_CODE = r'''
import json
import os
from pathlib import Path

safe_flags = (
    "--headless", "--no-sandbox", "--disable-web-security",
    "--remote-debugging-address", "--remote-debugging-port", "--user-data-dir",
)
rows = []
for entry in Path("/proc").iterdir():
    if not entry.name.isdigit():
        continue
    try:
        comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
        argv = [
            value.decode("utf-8", errors="replace")
            for value in (entry / "cmdline").read_bytes().split(b"\0")
            if value
        ]
    except OSError:
        continue
    executable = ""
    executable_errno = None
    try:
        executable = os.path.basename(os.readlink(entry / "exe"))
    except OSError as exc:
        executable_errno = exc.errno
    argv0 = os.path.basename(argv[0]) if argv else ""
    identity = f"{comm} {executable} {argv0}".lower()
    if not any(token in identity for token in ("chrome", "node", "python", "sleep")):
        continue
    rows.append({
        "pid": int(entry.name),
        "comm": comm[:64],
        "exe": executable[:128],
        "exe_errno": executable_errno,
        "argv0": argv0[:128],
        "flags": {
            flag: any(value == flag or value.startswith(flag + "=") for value in argv[1:])
            for flag in safe_flags
        },
    })
print(json.dumps(rows, separators=(",", ":")), flush=True)
'''


CGROUP_DIAGNOSTIC_FIELDS: Final = (
    "probe_ok",
    "pids_events_max",
    "pids_current",
    "memory_events_low",
    "memory_events_high",
    "memory_events_max",
    "memory_events_oom",
    "memory_events_oom_kill",
    "memory_events_oom_group_kill",
    "memory_current",
    "memory_peak",
)
CGROUP_CUMULATIVE_FIELDS: Final = frozenset(
    {
        "pids_events_max",
        "memory_events_low",
        "memory_events_high",
        "memory_events_max",
        "memory_events_oom",
        "memory_events_oom_kill",
        "memory_events_oom_group_kill",
        "memory_peak",
    }
)


CGROUP_DIAGNOSTIC_CODE = r'''
import json
from pathlib import Path

values = {
    "probe_ok": 1,
    "pids_events_max": -1,
    "pids_current": -1,
    "memory_events_low": -1,
    "memory_events_high": -1,
    "memory_events_max": -1,
    "memory_events_oom": -1,
    "memory_events_oom_kill": -1,
    "memory_events_oom_group_kill": -1,
    "memory_current": -1,
    "memory_peak": -1,
}

def read_scalar(name):
    try:
        return int(Path("/sys/fs/cgroup", name).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return -1

def read_events(name):
    result = {}
    try:
        lines = Path("/sys/fs/cgroup", name).read_text(encoding="ascii").splitlines()
    except OSError:
        return result
    for line in lines:
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            result[parts[0]] = int(parts[1])
        except ValueError:
            continue
    return result

pids_events = read_events("pids.events")
memory_events = read_events("memory.events")
values["pids_events_max"] = pids_events.get("max", -1)
values["pids_current"] = read_scalar("pids.current")
for event in ("low", "high", "max", "oom", "oom_kill", "oom_group_kill"):
    values["memory_events_" + event] = memory_events.get(event, -1)
values["memory_current"] = read_scalar("memory.current")
values["memory_peak"] = read_scalar("memory.peak")
print(json.dumps(values, separators=(",", ":")), flush=True)
'''


PID1_PROBE_CODE = r'''
import json
from pathlib import Path

command = Path("/proc/1/cmdline").read_bytes().replace(b"\0", b" ").decode().strip()
status = {}
for line in Path("/proc/1/status").read_text().splitlines():
    if ":" in line:
        key, value = line.split(":", 1)
        status[key] = value.strip()
if not command.startswith("python -m sandbox_sidecar.browser_server"):
    raise RuntimeError(f"unexpected PID1: {command}")
if status.get("CapEff") != "0000000000000000":
    raise RuntimeError("PID1 retained an effective capability")
if status.get("NoNewPrivs") != "1" or status.get("Seccomp") != "2":
    raise RuntimeError("PID1 hardening is inactive")
print(json.dumps({
    "pid1": command, "cap_eff": status["CapEff"],
    "no_new_privs": status["NoNewPrivs"], "seccomp": status["Seccomp"],
}, separators=(",", ":")), flush=True)
'''


PROCFS_LANDLOCK_PROBE_CODE = r'''
import errno
import os
from pathlib import Path

DENIED = {errno.EACCES, errno.EPERM, errno.EROFS, errno.ENOENT}

def expect_open_denied(path, flags):
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        if exc.errno not in DENIED:
            raise
        return
    os.close(descriptor)
    raise RuntimeError(f"procfs access unexpectedly succeeded: {path}")

for sensitive in ("/proc/1/mem", "/proc/1/environ", "/proc/1/fd/0"):
    expect_open_denied(sensitive, os.O_RDONLY)
for protected in ("/proc/sys/kernel/core_pattern", "/proc/sysrq-trigger"):
    expect_open_denied(protected, os.O_WRONLY)
expect_open_denied("/proc/modelmirror-wave7-probe", os.O_WRONLY | os.O_CREAT)
expect_open_denied("/proc/self/comm", os.O_WRONLY | os.O_TRUNC)

# WRITE_FILE is deliberately available for Chromium's user-namespace map
# setup. Prove the category without altering persistent state or another PID.
descriptor = os.open("/proc/self/comm", os.O_WRONLY)
try:
    os.write(descriptor, b"mm-wave7-probe\n")
finally:
    os.close(descriptor)
if Path("/proc/self/comm").read_text(encoding="ascii").strip() != "mm-wave7-probe":
    raise RuntimeError("procfs self-write category was not available")
print(
    "procfs_guard=ok pid1_sensitive=denied sysctl=readonly "
    "create=denied truncate=denied self_comm_write=allowed",
    flush=True,
)
'''


POST_RESTART_PROBE_CODE = r'''
import os
from pathlib import Path

for root in (Path("/profiles"), Path("/artifacts")):
    if any(root.iterdir()):
        raise RuntimeError(f"runtime residue remains under {root}")
for entry in Path("/proc").iterdir():
    if not entry.name.isdigit():
        continue
    if int(entry.name) == os.getpid():
        continue
    try:
        command = (entry / "cmdline").read_bytes().replace(b"\0", b" ")
    except OSError:
        continue
    if b"modelmirror-chromium" in command or b"chrome-linux64/chrome" in command:
        raise RuntimeError("Chromium residue survived PID1 restart")
    if b"sleep 300" in command:
        raise RuntimeError("independent setsid residue survived PID1 restart")
print("runtime_roots=empty chromium_residue=none setsid_residue=none", flush=True)
'''


UNTRUSTED_PEER_CODE = r'''
import asyncio
import json
import sys

async def main():
    reader, writer = await asyncio.open_unix_connection(
        "/run/modelmirror-browser-mcp/browser-mcp.sock"
    )
    writer.write(json.dumps({
        "action": "mcp_stdio", "adapter_id": sys.argv[1], "configuration": {},
    }, separators=(",", ":")).encode() + b"\n")
    await writer.drain()
    response = json.loads((await asyncio.wait_for(reader.readline(), 5)).decode())
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError):
        pass
    if response.get("code") != "browser_peer_denied":
        raise RuntimeError("sandbox UID unexpectedly passed SO_PEERCRED gate")
    print("peer_uid=65532 mcp_stdio=denied", flush=True)

asyncio.run(main())
'''


def _start_egress(
    runner: DockerRunner,
    ledger: ResourceLedger,
    *,
    base: str,
    token: str,
    image: str,
    network: str,
    socket_volume: str,
) -> str:
    name = _resource(base, f"{token}-egress")
    _reserve_resource(runner, ledger, "container", name)
    runner.run(
        "run", "-d", "--restart", "unless-stopped",
        *_bounded_container_args(
            name=name, user="65532:65532", network=network, label=base,
            pids=64, memory="256m", cpus="0.5",
        ),
        "-e", "MCP_BROWSER_EGRESS_SOCKET_PATH=/run/modelmirror-browser-egress/browser-egress.sock",
        "-e", "MCP_BROWSER_ALLOW_SYNTHETIC_DNS=true",
        "--mount", f"type=volume,src={socket_volume},dst=/run/modelmirror-browser-egress",
        image, "python", "-m", "sandbox_sidecar.browser_server", "egress",
    )
    payload = _inspect(runner, name)
    _assert_common_security(payload, user="65532:65532", network_mode=network)
    if payload["HostConfig"]["RestartPolicy"]["Name"] != "unless-stopped":
        raise SmokeFailure("egress automatic restart policy is missing")
    _wait_path(
        runner, name, "/run/modelmirror-browser-egress/browser-egress.control"
    )
    print(runner.run("exec", name, "python", "-c", PID1_PROBE_CODE).stdout.strip())
    return name


def _probe_egress_rotation(
    runner: DockerRunner,
    ledger: ResourceLedger,
    *,
    base: str,
    token: str,
    image: str,
    socket_volume: str,
    egress: str,
) -> None:
    before = _state(_inspect(runner, egress))
    name = _resource(base, f"{token}-cap-probe")
    _reserve_resource(runner, ledger, "container", name)
    runner.run(
        "run", "-d",
        *_bounded_container_args(
            name=name, user="65532:65532", network="none", label=base,
            pids=16, memory="96m", cpus="0.25",
        ),
        "--mount", f"type=volume,src={socket_volume},dst=/run/modelmirror-browser-egress",
        image, "python", "-c", CAPABILITY_RESTART_PROBE_CODE,
    )
    helper = _inspect(runner, name)
    _assert_common_security(helper, user="65532:65532", network_mode="none")
    allowed = {"/run/modelmirror-browser-egress"}
    if {item["Destination"] for item in helper.get("Mounts", [])} != allowed:
        raise SmokeFailure("capability probe mount surface drifted")
    logs = _wait_log(runner, name, "old_capability=denied", timeout=60)
    print(next(line for line in logs.splitlines() if "old_capability=denied" in line))
    _wait_exit(runner, name, timeout=30)
    after = _wait_restart(runner, egress, before, timeout=30)
    print(
        f"{token}: egress_preflight_restart={before[0]}->{after[0]} "
        f"started_at_changed={after[1] != before[1]}"
    )


def _start_browser(
    runner: DockerRunner,
    ledger: ResourceLedger,
    *,
    base: str,
    token: str,
    image: str,
    seccomp: Path,
    browser_socket: str,
    egress_socket: str,
    artifact_volume: str,
) -> str:
    name = _resource(base, f"{token}-browser")
    _reserve_resource(runner, ledger, "container", name)
    runner.run(
        "run", "-d", "--restart", "unless-stopped",
        *_bounded_container_args(
            name=name, user="65532:65532", network="none", label=base,
            pids=256, memory="1g", cpus="1.5",
        ),
        "--security-opt", f"seccomp={seccomp}",
        "--tmpfs", "/profiles:rw,nosuid,nodev,noexec,size=268435456,uid=65532,gid=65532,mode=0700",
        "--tmpfs", "/dev/shm:rw,nosuid,nodev,noexec,size=268435456,uid=65532,gid=65532,mode=0700",
        "-e", "MCP_BROWSER_SOCKET_PATH=/run/modelmirror-browser-mcp/browser-mcp.sock",
        "-e", "MCP_BROWSER_EGRESS_SOCKET_PATH=/run/modelmirror-browser-egress/browser-egress.sock",
        "-e", "MCP_BROWSER_PROFILE_ROOT=/profiles",
        "-e", "MCP_BROWSER_ARTIFACT_ROOT=/artifacts",
        "-e", "MCP_BROWSER_MAX_SESSIONS=1",
        "-e", "MCP_BROWSER_TRUSTED_CLIENT_UID=0",
        "--mount", f"type=volume,src={browser_socket},dst=/run/modelmirror-browser-mcp",
        "--mount", f"type=volume,src={egress_socket},dst=/run/modelmirror-browser-egress",
        "--mount", f"type=volume,src={artifact_volume},dst=/artifacts",
        image, "python", "-m", "sandbox_sidecar.browser_server",
    )
    payload = _inspect(runner, name)
    _assert_common_security(payload, user="65532:65532", network_mode="none")
    _assert_browser_procfs_boundary(payload)
    security = set(payload["HostConfig"].get("SecurityOpt") or [])
    if "seccomp=unconfined" in security or not any(
        item.startswith("seccomp=") for item in security
    ):
        raise SmokeFailure("browser custom seccomp is not active")
    if payload["HostConfig"]["RestartPolicy"]["Name"] != "unless-stopped":
        raise SmokeFailure("browser automatic restart policy is missing")
    allowed = {
        "/run/modelmirror-browser-mcp",
        "/run/modelmirror-browser-egress",
        "/artifacts",
    }
    if {item["Destination"] for item in payload.get("Mounts", [])} != allowed:
        raise SmokeFailure("browser mount surface drifted")
    _wait_path(runner, name, "/run/modelmirror-browser-mcp/browser-mcp.sock")
    print(runner.run("exec", name, "python", "-c", PID1_PROBE_CODE).stdout.strip())
    _run_landlock_procfs_probe(runner, name)
    return name


def _wait_restart(
    runner: DockerRunner,
    name: str,
    before: tuple[int, str, int, str],
    *,
    timeout: int = 45,
) -> tuple[int, str, int, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = _inspect(runner, name)
        current = _state(payload)
        if (
            current[0] > before[0]
            and current[1] != before[1]
            and current[2] > 0
            and current[2] != before[2]
            and current[3] == before[3]
            and payload["State"].get("Running") is True
        ):
            time.sleep(1)
            stable = _state(_inspect(runner, name))
            if stable[0] != current[0]:
                raise SmokeFailure(f"{name} entered a restart loop")
            return stable
        time.sleep(0.2)
    raise SmokeFailure(f"{name} did not automatically restart")


def _run_untrusted_peer_probe(
    runner: DockerRunner,
    ledger: ResourceLedger,
    *,
    base: str,
    token: str,
    image: str,
    browser_socket: str,
    adapter: str,
) -> None:
    name = _resource(base, f"{token}-peer-deny")
    _reserve_resource(runner, ledger, "container", name)
    result = runner.run(
        "run",
        *_bounded_container_args(
            name=name, user="65532:65532", network="none", label=base,
            pids=16, memory="96m", cpus="0.25",
        ),
        "--mount", f"type=volume,src={browser_socket},dst=/run/modelmirror-browser-mcp",
        image, "python", "-c", UNTRUSTED_PEER_CODE, adapter,
        timeout=30,
    )
    print(result.stdout.strip())


def _run_production_seccomp_probe(runner: DockerRunner, browser: str) -> None:
    result = runner.run(
        "exec", browser, "python", "-c", SECCOMP_PROBE_CODE, "deny", timeout=30
    )
    print(result.stdout.strip())


def _run_landlock_procfs_probe(runner: DockerRunner, browser: str) -> None:
    profile = "/profiles/modelmirror-procfs-probe"
    artifacts = "/artifacts/modelmirror-procfs-probe"
    runner.run(
        "exec", browser, "python", "-c",
        (
            "from pathlib import Path; "
            f"Path('{profile}').mkdir(mode=0o700); "
            f"Path('{artifacts}').mkdir(mode=0o700)"
        ),
        timeout=15,
    )
    try:
        result = runner.run(
            "exec", browser,
            "python", "-m", "sandbox_sidecar.browser_mcp", "landlock",
            profile, artifacts, "--", "python", "-c",
            PROCFS_LANDLOCK_PROBE_CODE,
            timeout=30,
        )
        print(result.stdout.strip())
    finally:
        runner.run(
            "exec", browser, "python", "-c",
            (
                "from pathlib import Path; "
                f"Path('{profile}').rmdir(); "
                f"Path('{artifacts}').rmdir()"
            ),
            check=False,
            timeout=15,
        )


def _start_setsid_residue(runner: DockerRunner, browser: str) -> None:
    runner.run("exec", "-d", browser, "setsid", "sleep", "300")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        listing = runner.run("top", browser, check=False, timeout=5).stdout
        if "sleep 300" in listing:
            print("setsid_descendant=running_before_pid1_exit")
            return
        time.sleep(0.2)
    raise SmokeFailure("independent setsid descendant did not start")


def _start_gateway_helper(
    runner: DockerRunner,
    ledger: ResourceLedger,
    *,
    base: str,
    token: str,
    attempt: int,
    image: str,
    browser_socket: str,
    artifact_volume: str,
    adapter: str,
    url: str,
    expect_timeout: bool = False,
) -> str:
    name = _resource(base, f"{token}-gateway-{attempt}")
    _reserve_resource(runner, ledger, "container", name)
    helper_command = [
        "python", "-m", "sandbox_sidecar.smoke_browser_adapters",
        "--gateway-url", url, "--adapter", adapter,
    ]
    if expect_timeout:
        helper_command.append("--expect-timeout")
    runner.run(
        "run", "-d",
        *_bounded_container_args(
            name=name, user="0:65532", network="none", label=base,
            pids=64, memory="256m", cpus="0.5",
        ),
        "--cap-add", "DAC_READ_SEARCH",
        "--mount", f"type=volume,src={browser_socket},dst=/run/modelmirror-browser-mcp",
        "--mount", f"type=volume,src={artifact_volume},dst=/artifacts,readonly",
        image, *helper_command,
    )
    payload = _inspect(runner, name)
    _assert_common_security(
        payload,
        user="0:65532",
        network_mode="none",
        cap_add=frozenset({"DAC_READ_SEARCH"}),
    )
    allowed = {"/run/modelmirror-browser-mcp", "/artifacts"}
    if {item["Destination"] for item in payload.get("Mounts", [])} != allowed:
        raise SmokeFailure("gateway helper mount surface drifted")
    mount_modes = {
        item["Destination"]: bool(item.get("RW")) for item in payload.get("Mounts", [])
    }
    if mount_modes != {"/run/modelmirror-browser-mcp": True, "/artifacts": False}:
        raise SmokeFailure("gateway helper volume access mode drifted")
    print(
        "acceptance_helper=uid0_gid65532 cap_add=DAC_READ_SEARCH artifact_mount=readonly "
        "browser_socket=readwrite network=none"
    )
    return name


_LOG_SECRET = re.compile(
    r"(?i)\b(control[_-]?key|capability|authorization|token)\b\s*[:=]\s*[^\s,}\]]+"
)
_LOG_URL = re.compile(r"(?i)\b(?:https?|data|file)://[^\s\"']+")
_LOG_PAYLOAD = re.compile(
    r"(?i)([\"']?(?:content|structuredContent|arguments|params|result|text)[\"']?\s*[:=]\s*).*$"
)


def _safe_log_tail(value: str, *, limit: int = 2048) -> str:
    """Keep bounded failure context without credentials, URLs, or tool payloads."""

    lines: list[str] = []
    for raw_line in value.splitlines()[-40:]:
        line = re.sub(r"\b[0-9a-fA-F]{64}\b", "<redacted-64hex>", raw_line)
        line = _LOG_SECRET.sub(r"\1=<redacted>", line)
        line = _LOG_URL.sub("<redacted-url>", line)
        line = _LOG_PAYLOAD.sub(r"\1<redacted-payload>", line)
        line = re.sub(r"\b[A-Za-z0-9+/]{128,}={0,2}\b", "<redacted-blob>", line)
        lines.append(line[:512])
    safe = "\n".join(lines)
    return safe[-limit:] if safe else "<empty>"


def _safe_container_state(runner: DockerRunner, name: str) -> dict[str, object]:
    result = runner.run("inspect", name, check=False, timeout=5)
    if result.returncode != 0:
        return {"inspect": "unavailable", "returncode": result.returncode}
    try:
        payload = json.loads(result.stdout)[0]
        state = payload["State"]
        return {
            "Status": str(state.get("Status") or ""),
            "ExitCode": int(state.get("ExitCode") or 0),
            "OOMKilled": bool(state.get("OOMKilled")),
            "Restarting": bool(state.get("Restarting")),
            "RestartCount": int(payload.get("RestartCount") or 0),
        }
    except (KeyError, TypeError, ValueError, IndexError):
        return {"inspect": "invalid"}


def _safe_process_snapshot(runner: DockerRunner, browser: str) -> list[dict[str, object]]:
    result = runner.run(
        "exec", browser, "python", "-c", PROCESS_SNAPSHOT_CODE,
        check=False, timeout=10,
    )
    if result.returncode != 0:
        return [{"probe": "unavailable", "returncode": result.returncode}]
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return [{"probe": "invalid"}]
    if not isinstance(payload, list):
        return [{"probe": "invalid"}]
    # The in-container probe emits only bounded process metadata and boolean flags.
    return payload[-32:]


def _empty_cgroup_snapshot() -> dict[str, int]:
    return {
        field: 0 if field == "probe_ok" else -1
        for field in CGROUP_DIAGNOSTIC_FIELDS
    }


def _safe_cgroup_snapshot(runner: DockerRunner, browser: str) -> dict[str, int]:
    """Return only the fixed numeric cgroup fields; never forward probe output."""

    result = runner.run(
        "exec", browser, "python", "-c", CGROUP_DIAGNOSTIC_CODE,
        check=False, timeout=10,
    )
    if result.returncode != 0:
        return _empty_cgroup_snapshot()
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return _empty_cgroup_snapshot()
    if not isinstance(payload, dict) or set(payload) != set(CGROUP_DIAGNOSTIC_FIELDS):
        return _empty_cgroup_snapshot()
    normalized: dict[str, int] = {}
    for field in CGROUP_DIAGNOSTIC_FIELDS:
        value = payload[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < -1:
            return _empty_cgroup_snapshot()
        normalized[field] = value
    if normalized["probe_ok"] != 1:
        return _empty_cgroup_snapshot()
    return normalized


def _merge_cgroup_diagnostics(
    observed: dict[str, int],
    current: dict[str, int],
) -> dict[str, int]:
    """Preserve lifetime counters/peak while currents remain the latest sample."""

    if current["probe_ok"] != 1:
        return observed
    if observed["probe_ok"] != 1:
        return current
    merged = dict(current)
    for field in CGROUP_CUMULATIVE_FIELDS:
        merged[field] = max(observed[field], current[field])
    return merged


def _chromium_failure_evidence(
    runner: DockerRunner,
    browser: str,
    helper: str,
    snapshots: list[dict[str, object]],
    last_probe: str,
    cgroup_before: dict[str, int],
    cgroup_observed: dict[str, int],
    cgroup_final: dict[str, int],
) -> str:
    helper_logs = runner.run(
        "logs", "--tail", "80", helper, check=False, timeout=10,
    )
    browser_logs = runner.run(
        "logs", "--tail", "80", browser, check=False, timeout=10,
    )
    return (
        "real Chromium security probe failed: "
        f"helper_state={json.dumps(_safe_container_state(runner, helper), separators=(',', ':'))} "
        f"browser_state={json.dumps(_safe_container_state(runner, browser), separators=(',', ':'))} "
        f"cgroup_before={json.dumps(cgroup_before, separators=(',', ':'))} "
        f"cgroup_observed={json.dumps(cgroup_observed, separators=(',', ':'))} "
        f"cgroup_final={json.dumps(cgroup_final, separators=(',', ':'))} "
        f"process_snapshots={json.dumps(snapshots[-8:], separators=(',', ':'))} "
        f"probe={_safe_log_tail(last_probe, limit=1024)!r} "
        f"helper_logs={_safe_log_tail(helper_logs.stdout + helper_logs.stderr)!r} "
        f"browser_logs={_safe_log_tail(browser_logs.stdout + browser_logs.stderr)!r}"
    )


def _wait_chromium_probe(
    runner: DockerRunner,
    browser: str,
    helper: str,
    *,
    cgroup_before: dict[str, int],
    timeout: int = 45,
) -> None:
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    next_snapshot = started
    snapshots: list[dict[str, object]] = []
    cgroup_observed = cgroup_before
    last = ""
    while time.monotonic() < deadline:
        result = runner.run(
            "exec", browser, "python", "-c", CHROMIUM_PROBE_CODE,
            check=False, timeout=10,
        )
        last = f"{result.stdout}\n{result.stderr}"
        if result.returncode == 0:
            print(result.stdout.strip())
            return
        now = time.monotonic()
        if now >= next_snapshot:
            current_cgroup = _safe_cgroup_snapshot(runner, browser)
            cgroup_observed = _merge_cgroup_diagnostics(
                cgroup_observed, current_cgroup,
            )
            snapshots.append({
                "t": round(now - started, 3),
                "processes": _safe_process_snapshot(runner, browser),
            })
            snapshots = snapshots[-8:]
            next_snapshot = now + 0.5
        helper_state = runner.run("inspect", helper, check=False, timeout=5)
        if helper_state.returncode == 0:
            state = json.loads(helper_state.stdout)[0]["State"]
            if state.get("Status") == "exited":
                cgroup_final = _safe_cgroup_snapshot(runner, browser)
                cgroup_observed = _merge_cgroup_diagnostics(
                    cgroup_observed, cgroup_final,
                )
                raise SmokeFailure(
                    _chromium_failure_evidence(
                        runner, browser, helper, snapshots, last,
                        cgroup_before, cgroup_observed, cgroup_final,
                    )
                )
        time.sleep(0.1)
    cgroup_final = _safe_cgroup_snapshot(runner, browser)
    cgroup_observed = _merge_cgroup_diagnostics(
        cgroup_observed, cgroup_final,
    )
    raise SmokeFailure(
        _chromium_failure_evidence(
            runner, browser, helper, snapshots, last,
            cgroup_before, cgroup_observed, cgroup_final,
        )
    )


def _run_gateway_attempt(
    runner: DockerRunner,
    ledger: ResourceLedger,
    *,
    base: str,
    token: str,
    attempt: int,
    image: str,
    browser: str,
    egress: str,
    browser_socket: str,
    artifact_volume: str,
    adapter: str,
    url: str,
    inspect_chromium: bool,
) -> tuple[tuple[int, str, int, str], tuple[int, str, int, str]]:
    _wait_path(runner, browser, "/run/modelmirror-browser-mcp/browser-mcp.sock")
    browser_before = _state(_inspect(runner, browser))
    egress_before = _state(_inspect(runner, egress))
    cgroup_before = _safe_cgroup_snapshot(runner, browser)
    helper = _start_gateway_helper(
        runner, ledger, base=base, token=token, attempt=attempt,
        image=image, browser_socket=browser_socket,
        artifact_volume=artifact_volume, adapter=adapter, url=url,
    )
    if inspect_chromium:
        _wait_chromium_probe(
            runner, browser, helper, cgroup_before=cgroup_before,
        )
    try:
        logs = _wait_log(runner, helper, "cleanup=ok", timeout=120)
    except SmokeFailure as exc:
        browser_logs = runner.run(
            "logs", "--tail", "80", browser, check=False, timeout=10,
        )
        raise SmokeFailure(
            f"{exc} browser_events="
            f"{_safe_log_tail(browser_logs.stdout + browser_logs.stderr)}"
        ) from exc
    required = (
        "gateway=ok", "navigate=ok", "snapshot=ok", "fill=ok",
        "click=ok", "outcome=ok", "screenshot=png", "cleanup=ok",
    )
    if any(marker not in logs for marker in required):
        raise SmokeFailure(f"gateway smoke evidence incomplete: {logs[-4096:]}")
    for line in logs.splitlines():
        if "gateway=ok" in line or "cleanup=ok" in line:
            print(line)
    _wait_exit(runner, helper, timeout=30)
    browser_after = _wait_restart(runner, browser, browser_before)
    egress_after = _wait_restart(runner, egress, egress_before)
    if browser_after[0] != browser_before[0] + 1:
        raise SmokeFailure("browser restarted more than once for one session")
    if egress_after[0] != egress_before[0] + 1:
        raise SmokeFailure("egress restarted more than once for one session")
    print(
        f"{adapter} attempt={attempt}: peer_uid=0 handshake=ok "
        f"browser_restart={browser_before[0]}->{browser_after[0]} "
        f"egress_restart={egress_before[0]}->{egress_after[0]} "
        "started_at_changed=true pid1_changed=true"
    )
    return browser_after, egress_after


def _run_gateway_timeout_attempt(
    runner: DockerRunner,
    ledger: ResourceLedger,
    *,
    base: str,
    token: str,
    attempt: int,
    image: str,
    browser: str,
    egress: str,
    browser_socket: str,
    artifact_volume: str,
    adapter: str,
    url: str,
) -> tuple[tuple[int, str, int, str], tuple[int, str, int, str]]:
    _wait_path(runner, browser, "/run/modelmirror-browser-mcp/browser-mcp.sock")
    browser_before = _state(_inspect(runner, browser))
    egress_before = _state(_inspect(runner, egress))
    helper = _start_gateway_helper(
        runner, ledger, base=base, token=token, attempt=attempt,
        image=image, browser_socket=browser_socket,
        artifact_volume=artifact_volume, adapter=adapter, url=url,
        expect_timeout=True,
    )
    try:
        logs = _wait_log(
            runner, helper, "timeout=unknown_outcome", timeout=120,
        )
    except SmokeFailure as exc:
        browser_logs = runner.run(
            "logs", "--tail", "120", browser, check=False, timeout=10,
        )
        raise SmokeFailure(
            f"{exc} browser_events="
            f"{_safe_log_tail(browser_logs.stdout + browser_logs.stderr)}"
        ) from exc
    required = (
        "timeout=unknown_outcome",
        "navigation_timeout=20s",
        "elapsed_window=ok",
        "retryable=false",
    )
    if any(marker not in logs for marker in required):
        raise SmokeFailure(
            f"gateway timeout evidence incomplete: {_safe_log_tail(logs)}"
        )

    browser_logs = runner.run(
        "logs", "--tail", "120", browser, check=False, timeout=10,
    )
    browser_events = browser_logs.stdout + browser_logs.stderr
    matched_event = next(
        (marker for marker in TIMEOUT_RUNTIME_EVENTS if marker in browser_events),
        None,
    )
    if matched_event is None:
        raise SmokeFailure(
            "browser did not emit a reviewed timeout runtime event: "
            f"{_safe_log_tail(browser_events)}"
        )
    for line in logs.splitlines():
        if "timeout=unknown_outcome" in line:
            print(line)
    print(f"browser_timeout_event={matched_event.split(':', 1)[1].strip(chr(34))}")

    _wait_exit(runner, helper, timeout=30)
    browser_after = _wait_restart(runner, browser, browser_before)
    egress_after = _wait_restart(runner, egress, egress_before)
    if browser_after[0] != browser_before[0] + 1:
        raise SmokeFailure("browser restarted more than once for timeout session")
    if egress_after[0] != egress_before[0] + 1:
        raise SmokeFailure("egress restarted more than once for timeout session")
    print(
        f"{adapter} attempt={attempt}: timeout=unknown_outcome "
        f"browser_restart={browser_before[0]}->{browser_after[0]} "
        f"egress_restart={egress_before[0]}->{egress_after[0]} "
        "started_at_changed=true pid1_changed=true"
    )
    return browser_after, egress_after


def _post_restart_cleanup(runner: DockerRunner, browser: str) -> None:
    print(
        runner.run(
            "exec", browser, "python", "-c", POST_RESTART_PROBE_CODE,
            timeout=15,
        ).stdout.strip()
    )


def _run_adapter_pair(
    runner: DockerRunner,
    ledger: ResourceLedger,
    *,
    base: str,
    image: str,
    seccomp: Path,
    network: str,
    adapter: str,
    previous_ids: set[str],
) -> set[str]:
    token = ADAPTER_TOKENS[adapter]
    egress_socket = _resource(base, f"{token}-egress-sock")
    browser_socket = _resource(base, f"{token}-browser-sock")
    artifact_volume = _resource(base, f"{token}-artifacts")
    _create_volume(runner, ledger, egress_socket)
    _create_volume(runner, ledger, browser_socket)
    _create_volume(runner, ledger, artifact_volume, tmpfs=True)
    egress = _start_egress(
        runner, ledger, base=base, token=token, image=image,
        network=network, socket_volume=egress_socket,
    )
    _probe_egress_rotation(
        runner, ledger, base=base, token=token, image=image,
        socket_volume=egress_socket, egress=egress,
    )
    browser = _start_browser(
        runner, ledger, base=base, token=token, image=image, seccomp=seccomp,
        browser_socket=browser_socket, egress_socket=egress_socket,
        artifact_volume=artifact_volume,
    )
    ids = {_state(_inspect(runner, browser))[3], _state(_inspect(runner, egress))[3]}
    if ids & previous_ids:
        raise SmokeFailure("adapter did not receive a fresh browser+egress pair")
    _run_untrusted_peer_probe(
        runner, ledger, base=base, token=token, image=image,
        browser_socket=browser_socket, adapter=adapter,
    )
    _run_production_seccomp_probe(runner, browser)
    _start_setsid_residue(runner, browser)
    url = f"http://{FIXTURE_HOST}/"
    _run_gateway_attempt(
        runner, ledger, base=base, token=token, attempt=1, image=image,
        browser=browser, egress=egress, browser_socket=browser_socket,
        artifact_volume=artifact_volume, adapter=adapter, url=url,
        inspect_chromium=True,
    )
    _post_restart_cleanup(runner, browser)
    _run_gateway_attempt(
        runner, ledger, base=base, token=token, attempt=2, image=image,
        browser=browser, egress=egress, browser_socket=browser_socket,
        artifact_volume=artifact_volume, adapter=adapter, url=url,
        inspect_chromium=False,
    )
    _post_restart_cleanup(runner, browser)
    timeout_url = f"http://{FIXTURE_HOST}{TIMEOUT_FIXTURE_PATH}"
    _run_gateway_timeout_attempt(
        runner, ledger, base=base, token=token, attempt=3, image=image,
        browser=browser, egress=egress, browser_socket=browser_socket,
        artifact_volume=artifact_volume, adapter=adapter, url=timeout_url,
    )
    _post_restart_cleanup(runner, browser)
    return previous_ids | ids


def _validate_seccomp_file(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("defaultAction") != "SCMP_ACT_ERRNO":
        raise SmokeFailure("browser seccomp default action is not deny")
    rules = payload.get("syscalls")
    if not isinstance(rules, list) or not rules:
        raise SmokeFailure("browser seccomp rules are missing")
    first = rules[0]
    if first.get("action") != "SCMP_ACT_ALLOW" or first.get("names") != [
        "chroot", "clone", "setns", "unshare"
    ]:
        raise SmokeFailure("Chromium namespace exception drifted")
    sensitive = {"ptrace", "process_vm_readv", "process_vm_writev"}
    for rule in rules:
        if sensitive.intersection(rule.get("names") or []):
            caps = set((rule.get("includes") or {}).get("caps") or [])
            if rule.get("action") == "SCMP_ACT_ALLOW" and caps != {"CAP_SYS_PTRACE"}:
                raise SmokeFailure("ptrace syscall became unconditionally available")


def _selected_adapters(adapter: str | None) -> tuple[str, ...]:
    if adapter is None:
        return ADAPTERS
    if adapter not in ADAPTERS:
        raise SmokeFailure("unsupported fixed browser adapter selection")
    return (adapter,)


def run(
    image: str,
    seccomp: Path,
    *,
    run_id: str | None = None,
    adapter: str | None = None,
) -> None:
    if not seccomp.is_file():
        raise SmokeFailure(f"seccomp profile not found: {seccomp}")
    _validate_seccomp_file(seccomp)
    runner = DockerRunner()
    runner.run("image", "inspect", image)
    identifier = run_id or secrets.token_hex(4)
    if not re.fullmatch(r"[0-9a-f]{8}", identifier):
        raise SmokeFailure("run id must be exactly eight lowercase hex characters")
    base = f"{PREFIX}-{identifier}"
    selected_adapters = _selected_adapters(adapter)
    ledger = ResourceLedger(runner)
    succeeded = False
    try:
        network, address = _create_network(
            runner, ledger, base, seed=int(identifier[:2], 16)
        )
        _start_fixture(
            runner, ledger, base=base, image=image,
            network=network, address=address,
        )
        _run_seccomp_calibration(runner, ledger, base=base, image=image)
        pair_ids: set[str] = set()
        for selected_adapter in selected_adapters:
            pair_ids = _run_adapter_pair(
                runner, ledger, base=base, image=image, seccomp=seccomp,
                network=network, adapter=selected_adapter, previous_ids=pair_ids,
            )
        succeeded = True
    finally:
        ledger.cleanup()
    if succeeded:
        print(
            f"wave7_runtime_smoke=ok adapters={','.join(selected_adapters)} "
            "shared_stack_touched=false cleanup=verified",
            flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--seccomp", required=True, type=Path)
    parser.add_argument("--adapter", choices=ADAPTERS)
    parser.add_argument("--run-id", help="optional eight-character lowercase hex suffix")
    arguments = parser.parse_args(argv)
    try:
        run(
            arguments.image,
            arguments.seccomp.resolve(),
            run_id=arguments.run_id,
            adapter=arguments.adapter,
        )
    except (SmokeFailure, subprocess.TimeoutExpired, OSError, ValueError) as exc:
        print(f"wave7_runtime_smoke=failed reason={exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
