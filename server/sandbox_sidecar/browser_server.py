"""Unix-socket gateway and separated egress for Wave 7 browser MCPs.

``python -m sandbox_sidecar.browser_server`` runs in the network-less browser
execution container.  ``python -m sandbox_sidecar.browser_server egress`` runs
in a distinct, Internet-connected container and owns DNS resolution plus the
pinned outbound socket.  The two processes share only a private Unix socket.
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import ipaddress
import json
import os
import re
import secrets
import signal
import shutil
import socket
import ssl
import stat
import struct
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .browser_contracts import (
    BROWSER_ADAPTERS,
    BROWSER_LIMITS,
    BROWSER_SCHEMA_SHA256,
    CONTRACT_VERSION,
    IDLE_TTL_SECONDS,
    MAX_ACTIONS,
    MAX_ARGUMENT_BYTES,
    MAX_ARTIFACT_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_SESSIONS,
    NAVIGATION_TIMEOUT_SECONDS,
    SESSION_TTL_SECONDS,
    TOOL_CALL_TIMEOUT_SECONDS,
    UPSTREAM_SCHEMA_SHA256,
    BrowserPolicyError,
    assert_non_sensitive_interaction,
    session_expiry,
    validate_pinned_addresses,
    validate_pinned_records,
    validate_browser_url,
    validate_ref,
)
from .browser_mcp import (
    CHROME_FILL_ROLES,
    PLAYWRIGHT_FILL_ROLE_TYPES,
    SnapshotElement,
    SnapshotStructureError,
    extract_snapshot,
    page_digest,
    public_tools,
    result_failed,
    result_text,
    to_upstream_arguments,
    upstream_command,
    upstream_schema_digest,
)
from .engine import SandboxEngineError


SOCKET_PATH = Path(
    os.getenv("MCP_BROWSER_SOCKET_PATH", "/run/modelmirror-browser-mcp/browser-mcp.sock")
)
EGRESS_SOCKET_PATH = Path(
    os.getenv(
        "MCP_BROWSER_EGRESS_SOCKET_PATH",
        "/run/modelmirror-browser-egress/browser-egress.sock",
    )
)
EGRESS_CONTROL_PATH = EGRESS_SOCKET_PATH.with_name("browser-egress.control")
PROFILE_ROOT = Path(os.getenv("MCP_BROWSER_PROFILE_ROOT", "/profiles"))
ARTIFACT_ROOT = Path(os.getenv("MCP_BROWSER_ARTIFACT_ROOT", "/artifacts"))
TRUSTED_CLIENT_UID = int(os.getenv("MCP_BROWSER_TRUSTED_CLIENT_UID", "0"))
MAX_REQUEST_BYTES = 64 * 1024
MAX_MCP_MESSAGE_BYTES = MAX_OUTPUT_BYTES + 16 * 1024
MAX_PROXY_HEADER_BYTES = 64 * 1024
MAX_EGRESS_TUNNELS_PER_SESSION = 12
MAX_EGRESS_BYTES_PER_SESSION = 64 * 1024 * 1024
EGRESS_TUNNEL_IDLE_SECONDS = 30
EGRESS_TUNNEL_TTL_SECONDS = 120
DNS_TIMEOUT_SECONDS = 5
DOH_MAX_HEADER_BYTES = 16 * 1024
DOH_MAX_BODY_BYTES = 64 * 1024
DOH_MAX_ANSWERS = 64
DOH_RESOLVERS = (
    ("1.1.1.1", "cloudflare-dns.com"),
    ("1.0.0.1", "cloudflare-dns.com"),
)
REQUEST_PREAMBLE_TIMEOUT_SECONDS = 2
EGRESS_WATCH_BOOTSTRAP_SECONDS = 15
# Docker only activates a container restart policy after ten seconds of
# successful runtime.  Keep the control key and catalog socket private until
# that window has passed so even a very short first session can tear down and
# reliably restart the one-shot pair.
DOCKER_RESTART_ARM_SECONDS = 11.0
ALLOWED_ADAPTERS = frozenset(BROWSER_ADAPTERS)
PR_SET_DUMPABLE = 4
PR_GET_DUMPABLE = 3
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
AUTHORITY = re.compile(r"^(?P<host>[^:@/]+):(?P<port>[0-9]{1,5})$")
SESSION_DIRECTORY = re.compile(r"^[0-9a-f]{32}$")
ARTIFACT_SESSION_CHILDREN = frozenset({"staging", "registered"})
BROWSER_UPSTREAM_FAILURE_PATTERNS = (
    ("no usable sandbox", "chromium_sandbox"),
    ("sandbox", "chromium_sandbox"),
    ("failed to launch", "browser_launch"),
    ("browser process", "browser_process"),
    ("target closed", "target_closed"),
    ("session closed", "session_closed"),
    ("connection closed", "connection_closed"),
    ("not connected", "browser_not_connected"),
    ("response must have a page", "page_missing"),
    ("no page", "page_missing"),
    ("accessibility", "accessibility_failed"),
    ("protocol error", "protocol_error"),
    ("permission denied", "permission_denied"),
    ("enoent", "path_missing"),
    ("timed out", "upstream_timeout"),
)
BROWSER_UPSTREAM_FAILURE_CATEGORIES = frozenset(
    {category for _, category in BROWSER_UPSTREAM_FAILURE_PATTERNS}
    | {"unclassified"}
)
BROWSER_STDERR_PATTERNS = (
    (b"no usable sandbox", "no_usable_sandbox"),
    (b"sys_chroot", "chroot_failed"),
    (b"failed to move to new namespace", "namespace_denied"),
    (b"operation not permitted", "namespace_denied"),
    (b"bad system call", "seccomp_denied"),
    (b"sigsys", "seccomp_denied"),
    (b"seccomp", "seccomp_denied"),
    (b"zygote", "zygote_failed"),
    (b"devtoolsactiveport", "devtools_failed"),
    (b"permission denied", "permission_denied"),
    (b"target closed", "target_closed"),
    (b"out of memory", "out_of_memory"),
    (b"fatal", "generic_fatal"),
)
BROWSER_STDERR_CATEGORY_PRIORITY = (
    "no_usable_sandbox",
    "chroot_failed",
    "namespace_denied",
    "seccomp_denied",
    "zygote_failed",
    "devtools_failed",
    "permission_denied",
    "target_closed",
    "out_of_memory",
    "generic_fatal",
)
BROWSER_STDERR_CATEGORIES = frozenset(
    {*BROWSER_STDERR_CATEGORY_PRIORITY, "none"}
)
BROWSER_PREFLIGHT_ERROR_CODES = frozenset(
    {
        "browser_upstream_identity_drift",
        "browser_upstream_schema_drift",
        "browser_upstream_representative_call_failed",
        "browser_upstream_page_contract_failed",
        "browser_upstream_output_invalid",
        "browser_upstream_stdio_unavailable",
    }
    | {
        f"browser_upstream_representative_{category}"
        for category in BROWSER_UPSTREAM_FAILURE_CATEGORIES
    }
    | {
        f"browser_upstream_representative_target_closed_{category}"
        for category in BROWSER_STDERR_CATEGORIES
    }
)

BROWSER_RUNTIME_EVENT_CODES = frozenset(
    {
        "rpc_timeout",
        "rpc_transport_failure",
        "proxy_policy_violation",
        "proxy_policy_cross_origin",
        "proxy_policy_origin_unset",
        "proxy_policy_request_invalid",
        "proxy_policy_headers_too_large",
        "proxy_policy_websocket_or_auth",
        "proxy_policy_authority_invalid",
        "proxy_policy_method_denied",
        "proxy_policy_host_invalid",
        "proxy_policy_host_mismatch",
        "proxy_policy_egress_unavailable",
        "proxy_policy_egress_tunnel_limit",
        "proxy_policy_egress_byte_budget",
        "proxy_policy_dns_failed",
        "proxy_policy_dns_timeout",
        "proxy_policy_dns_answer_invalid",
        "proxy_policy_dns_private",
        "proxy_policy_dns_synthetic",
        "proxy_policy_tls_clienthello_invalid",
        "proxy_policy_tls_sni_denied",
        "proxy_policy_tls_ech_denied",
        "proxy_policy_egress_tunnel_deadline",
        "proxy_policy_egress_tunnel_idle",
        "proxy_policy_egress_capability_denied",
        "egress_revoked",
        "unregistered_artifact",
        "unregistered_artifact_console_log",
        "unregistered_artifact_download",
        "unregistered_artifact_trace",
        "unregistered_artifact_other",
        "unregistered_artifact_mixed",
        "proxy_policy_android_client_background_blocked_no_taint",
        "browser_policy_page_count",
        "browser_policy_snapshot_failed",
        "browser_policy_snapshot_structure",
        "browser_policy_output_too_large",
        "browser_policy_cross_origin",
        "browser_policy_unregistered_artifact",
    }
    | {
        f"proxy_policy_cross_origin_{relation}_{phase}"
        for relation in (
            "network_time", "connectivity", "component_update", "safe_browsing",
            "account", "optimization", "translate", "autofill", "push",
            "search_domain", "search_suggest", "google_home_connect", "google_home_root", "google_home_other",
            "google_chrome_service", "google_favicon", "static_service", "font_service",
            "google_api", "usercontent_service", "vendor_other", "external_host",
            "google_subdomain",
            "google_client", "clients1", "android_client",
            "scheme", "port", "unknown",
        )
        for phase in ("before_forward", "after_forward")
    }
    | {
        f"upstream_result_{category}"
        for category in BROWSER_UPSTREAM_FAILURE_CATEGORIES
    }
)

BROWSER_BACKGROUND_HOST_CATEGORIES = {
    "clients1.google.com": "clients1",
    "clients2.google.com": "network_time",
    "clients3.google.com": "connectivity",
    "connectivitycheck.gstatic.com": "connectivity",
    "clients4.google.com": "component_update",
    "update.googleapis.com": "component_update",
    "redirector.gvt1.com": "component_update",
    "edgedl.me.gvt1.com": "component_update",
    "safebrowsing.googleapis.com": "safe_browsing",
    "safebrowsing.google.com": "safe_browsing",
    "accounts.google.com": "account",
    "optimizationguide-pa.googleapis.com": "optimization",
    "chromemodelexecution-pa.googleapis.com": "optimization",
    "chromemodelquality-pa.googleapis.com": "optimization",
    "translate.googleapis.com": "translate",
    "content-autofill.googleapis.com": "autofill",
    "mtalk.google.com": "push",
    "www.google.com": "google_home",
    "google.com": "google_home",
    "ssl.gstatic.com": "static_service",
    "www.gstatic.com": "static_service",
    "gstatic.com": "static_service",
    "fonts.googleapis.com": "font_service",
    "fonts.gstatic.com": "font_service",
    "apis.google.com": "google_api",
    "googleapis.com": "google_api",
    "clients2.googleusercontent.com": "component_update",
    "googleusercontent.com": "usercontent_service",
    "tools.google.com": "component_update",
    "android.clients.google.com": "android_client",
    "chrome.google.com": "google_chrome_service",
    "dl.google.com": "component_update",
}
BROWSER_VENDOR_HOST_SUFFIXES = (
    "google.com", "googleapis.com", "gstatic.com", "googleusercontent.com", "gvt1.com"
)
BROWSER_VENDOR_SUFFIX_CATEGORIES = {
    "google.com": "google_subdomain",
    "googleapis.com": "google_api",
    "gstatic.com": "static_service",
    "googleusercontent.com": "usercontent_service",
    "gvt1.com": "component_update",
}

BROWSER_PROXY_RUNTIME_EVENTS = {
    "browser_cross_origin_denied": "proxy_policy_cross_origin",
    "browser_origin_not_set": "proxy_policy_origin_unset",
    "browser_proxy_request_invalid": "proxy_policy_request_invalid",
    "browser_proxy_headers_too_large": "proxy_policy_headers_too_large",
    "browser_websocket_or_proxy_auth_denied": "proxy_policy_websocket_or_auth",
    "browser_proxy_authority_invalid": "proxy_policy_authority_invalid",
    "browser_http_method_denied": "proxy_policy_method_denied",
    "browser_http_host_invalid": "proxy_policy_host_invalid",
    "browser_http_host_mismatch": "proxy_policy_host_mismatch",
    "browser_egress_unavailable": "proxy_policy_egress_unavailable",
    "browser_egress_tunnel_limit": "proxy_policy_egress_tunnel_limit",
    "browser_egress_byte_budget": "proxy_policy_egress_byte_budget",
    "browser_dns_failed": "proxy_policy_dns_failed",
    "browser_dns_timeout": "proxy_policy_dns_timeout",
    "browser_dns_answer_invalid": "proxy_policy_dns_answer_invalid",
    "browser_private_dns_denied": "proxy_policy_dns_private",
    "browser_dns_mixed_or_synthetic_denied": "proxy_policy_dns_synthetic",
    "browser_tls_client_hello_invalid": "proxy_policy_tls_clienthello_invalid",
    "browser_tls_sni_denied": "proxy_policy_tls_sni_denied",
    "browser_tls_ech_denied": "proxy_policy_tls_ech_denied",
    "browser_egress_tunnel_deadline": "proxy_policy_egress_tunnel_deadline",
    "browser_egress_tunnel_idle": "proxy_policy_egress_tunnel_idle",
    "browser_egress_capability_denied": "proxy_policy_egress_capability_denied",
}

BROWSER_POLICY_RUNTIME_EVENTS = {
    "browser_page_count_violation": "browser_policy_page_count",
    "browser_snapshot_verification_failed": "browser_policy_snapshot_failed",
    "browser_snapshot_ref_structure_invalid": "browser_policy_snapshot_structure",
    "browser_output_too_large": "browser_policy_output_too_large",
    "browser_cross_origin_denied": "browser_policy_cross_origin",
    "browser_unregistered_artifact": "browser_policy_unregistered_artifact",
}


def _emit_browser_runtime_event(code: str) -> None:
    safe_code = code if code in BROWSER_RUNTIME_EVENT_CODES else "rpc_transport_failure"
    print(
        json.dumps({"browser_runtime_event": safe_code}, separators=(",", ":")),
        file=sys.stderr,
        flush=True,
    )


def _proxy_runtime_event(reason: str) -> str:
    """Map an internal proxy denial to a fixed, content-free diagnostic."""

    return BROWSER_PROXY_RUNTIME_EVENTS.get(reason, "proxy_policy_violation")


def _disable_process_dumping() -> None:
    if os.name != "posix":
        raise RuntimeError("browser_prctl_unavailable")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise RuntimeError("browser_pr_set_dumpable_failed")
    if libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise RuntimeError("browser_pr_get_dumpable_failed")


def _trusted_peer_uid(writer: asyncio.StreamWriter) -> int:
    """Return the kernel-authenticated peer UID for the catalog UDS client."""

    if not sys.platform.startswith("linux") or not hasattr(socket, "SO_PEERCRED"):
        raise SandboxEngineError("Browser peer credentials unavailable.", code="browser_peer_unavailable")
    transport_socket = writer.get_extra_info("socket")
    if transport_socket is None:
        raise SandboxEngineError("Browser peer credentials unavailable.", code="browser_peer_unavailable")
    try:
        credentials = transport_socket.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        )
        _pid, uid, _gid = struct.unpack("3i", credentials)
    except (OSError, struct.error) as exc:
        raise SandboxEngineError(
            "Browser peer credentials unavailable.", code="browser_peer_unavailable"
        ) from exc
    return int(uid)


def _require_supervisor_pid1() -> None:
    """Fail closed unless the Python supervisor owns the container PID namespace."""

    if not sys.platform.startswith("linux") or os.getpid() != 1:
        raise RuntimeError("browser_supervisor_must_be_pid1")


def _safe_preflight_error_code(exc: BaseException) -> str | None:
    """Expose only reviewed machine codes, never upstream text or arguments."""

    if isinstance(exc, asyncio.TimeoutError):
        return "browser_upstream_preflight_timeout"
    reason = str(exc)
    return reason if reason in BROWSER_PREFLIGHT_ERROR_CODES else None


def _classify_upstream_failure(payload: object) -> str:
    """Reduce an untrusted upstream error body to a reviewed fixed category."""

    lowered = result_text(payload).lower()
    for token, category in BROWSER_UPSTREAM_FAILURE_PATTERNS:
        if token in lowered:
            return category
    return "unclassified"


class SafeStderrClassifier:
    """Classify a stream without retaining or exposing its untrusted text."""

    def __init__(self) -> None:
        self.categories: set[str] = set()
        self.changed = asyncio.Event()
        self._overlap = b""
        self._overlap_bytes = max(len(token) for token, _ in BROWSER_STDERR_PATTERNS) - 1

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        combined = self._overlap + chunk.lower()
        before = len(self.categories)
        for token, category in BROWSER_STDERR_PATTERNS:
            if token in combined:
                self.categories.add(category)
        if len(self.categories) != before:
            self.changed.set()
        self._overlap = combined[-self._overlap_bytes :]

    def primary(self) -> str:
        return next(
            (
                category
                for category in BROWSER_STDERR_CATEGORY_PRIORITY
                if category in self.categories
            ),
            "none",
        )


class BrowserOneShotLifecycle:
    """Atomically admit one trusted catalog session without queueing successors."""

    def __init__(self) -> None:
        self._claimed = False
        self.finished = asyncio.Event()

    @property
    def claimed(self) -> bool:
        return self._claimed

    def claim(self) -> None:
        # This method intentionally contains no await.  All handlers run on the
        # same event loop, so check-and-set is atomic with respect to peers.
        if self._claimed:
            raise SandboxEngineError(
                "Browser container session already consumed.",
                code="browser_container_session_consumed",
            )
        self._claimed = True

    def finish(self) -> None:
        if self._claimed:
            self.finished.set()


class RestartPolicyReadinessGate:
    """Delay readiness (or an early failed exit) until Docker can restart PID1."""

    def __init__(self, started_at: float) -> None:
        self.started_at = started_at
        self.armed = False

    def remaining(self) -> float:
        return max(
            0.0,
            self.started_at + DOCKER_RESTART_ARM_SECONDS - time.monotonic(),
        )

    async def wait_before_arm(self) -> None:
        remaining = self.remaining()
        if remaining > 0:
            await asyncio.sleep(remaining)

    def arm(self) -> None:
        self.armed = True

    async def hold_early_exit(self) -> None:
        """Finish the bounded activation wait even when the main task is cancelled."""

        if self.armed:
            return
        remaining = self.remaining()
        if remaining <= 0:
            return
        delay = asyncio.create_task(asyncio.sleep(remaining))
        try:
            while not delay.done():
                try:
                    await asyncio.shield(delay)
                except asyncio.CancelledError:
                    continue
        finally:
            if not delay.done():
                delay.cancel()
            await asyncio.gather(delay, return_exceptions=True)


def _is_symlink_or_non_directory(path: Path) -> bool:
    metadata = path.lstat()
    return stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode)


def _validate_artifact_tree(path: Path) -> None:
    """Validate a stale artifact tree without following attacker-created links."""

    for child in path.iterdir():
        metadata = child.lstat()
        if stat.S_ISLNK(metadata.st_mode) or os.path.ismount(child):
            raise RuntimeError("browser_runtime_cleanup_unsafe_entry")
        if stat.S_ISDIR(metadata.st_mode):
            _validate_artifact_tree(child)
        elif not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("browser_runtime_cleanup_unsafe_entry")


def _cleanup_stale_runtime_root(root: Path, *, artifacts: bool) -> None:
    """Remove only strict session directories from a dedicated runtime root.

    The roots are container-owned tmpfs/volumes.  Unexpected direct children,
    session symlinks, nested mounts, and special files fail closed so a restart
    can never turn cleanup into traversal of an administrator-provided path.
    """

    try:
        metadata = root.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError("browser_runtime_root_missing") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeError("browser_runtime_root_unsafe")
    for session_dir in root.iterdir():
        if not SESSION_DIRECTORY.fullmatch(session_dir.name):
            raise RuntimeError("browser_runtime_cleanup_unexpected_entry")
        if _is_symlink_or_non_directory(session_dir) or os.path.ismount(session_dir):
            raise RuntimeError("browser_runtime_cleanup_unsafe_entry")
        if artifacts:
            children = {child.name: child for child in session_dir.iterdir()}
            if not set(children).issubset(ARTIFACT_SESSION_CHILDREN):
                raise RuntimeError("browser_runtime_cleanup_unexpected_entry")
            for child in children.values():
                if _is_symlink_or_non_directory(child) or os.path.ismount(child):
                    raise RuntimeError("browser_runtime_cleanup_unsafe_entry")
                _validate_artifact_tree(child)
        shutil.rmtree(session_dir)


def _cleanup_stale_runtime_roots() -> None:
    _cleanup_stale_runtime_root(PROFILE_ROOT, artifacts=False)
    _cleanup_stale_runtime_root(ARTIFACT_ROOT, artifacts=True)


def _register_png_artifact(
    artifact_path: Path,
    registered_dir: Path,
) -> tuple[Path, str, int]:
    """Copy a validated PNG into a new trusted inode, then unlink its source."""

    source_descriptor = -1
    registered_directory_descriptor = -1
    registered_descriptor = -1
    registered_created = False
    registered_name = artifact_path.name.removeprefix(".modelmirror-")
    if re.fullmatch(r"browser_[0-9a-f]{32}\.png", registered_name) is None:
        raise BrowserPolicyError("browser_artifact_name_denied")
    registered_path = registered_dir / registered_name
    try:
        before = artifact_path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size <= 0
            or before.st_size > MAX_ARTIFACT_BYTES
        ):
            raise BrowserPolicyError("browser_artifact_metadata_denied")
        source_descriptor = os.open(
            artifact_path,
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(source_descriptor)
        identity = (opened.st_dev, opened.st_ino)
        if (
            identity != (before.st_dev, before.st_ino)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_size != before.st_size
        ):
            raise BrowserPolicyError("browser_artifact_race_denied")
        registered_directory_descriptor = os.open(
            registered_dir,
            os.O_RDONLY
            | os.O_CLOEXEC
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        registered_directory = os.fstat(registered_directory_descriptor)
        if not stat.S_ISDIR(registered_directory.st_mode):
            raise BrowserPolicyError("browser_artifact_directory_denied")
        try:
            registered_descriptor = os.open(
                registered_name,
                os.O_RDWR
                | os.O_CREAT
                | os.O_EXCL
                | os.O_CLOEXEC
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=registered_directory_descriptor,
            )
        except FileExistsError as exc:
            raise BrowserPolicyError("browser_artifact_collision") from exc
        registered_created = True
        created = os.fstat(registered_descriptor)
        if not stat.S_ISREG(created.st_mode) or created.st_nlink != 1:
            raise BrowserPolicyError("browser_artifact_metadata_denied")
        registered_identity = (created.st_dev, created.st_ino)

        hasher = hashlib.sha256()
        prefix = bytearray()
        copied_size = 0
        while True:
            chunk = os.read(source_descriptor, 64 * 1024)
            if not chunk:
                break
            if len(prefix) < len(PNG_MAGIC):
                prefix.extend(chunk[: len(PNG_MAGIC) - len(prefix)])
            hasher.update(chunk)
            copied_size += len(chunk)
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(registered_descriptor, remaining)
                if written <= 0:
                    raise OSError("browser_artifact_copy_failed")
                remaining = remaining[written:]
        if bytes(prefix) != PNG_MAGIC:
            raise BrowserPolicyError("browser_artifact_type_denied")
        after_read = os.fstat(source_descriptor)
        if (
            (after_read.st_dev, after_read.st_ino) != identity
            or after_read.st_nlink != 1
            or after_read.st_size != opened.st_size
            or after_read.st_mtime_ns != opened.st_mtime_ns
            or after_read.st_ctime_ns != opened.st_ctime_ns
            or copied_size != opened.st_size
        ):
            raise BrowserPolicyError("browser_artifact_race_denied")
        os.fsync(registered_descriptor)
        os.fchmod(registered_descriptor, 0o440)
        os.fsync(registered_descriptor)
        final = os.fstat(registered_descriptor)
        if (
            (final.st_dev, final.st_ino) != registered_identity
            or not stat.S_ISREG(final.st_mode)
            or final.st_nlink != 1
            or final.st_size != opened.st_size
        ):
            raise BrowserPolicyError("browser_artifact_race_denied")
        if (final.st_mode & 0o777) != 0o440:
            raise BrowserPolicyError("browser_artifact_permissions_denied")

        os.lseek(registered_descriptor, 0, os.SEEK_SET)
        registered_hasher = hashlib.sha256()
        registered_size = 0
        while True:
            chunk = os.read(registered_descriptor, 64 * 1024)
            if not chunk:
                break
            registered_hasher.update(chunk)
            registered_size += len(chunk)
        digest = hasher.hexdigest()
        if registered_size != copied_size or registered_hasher.hexdigest() != digest:
            raise BrowserPolicyError("browser_artifact_copy_mismatch")

        before_unlink = artifact_path.lstat()
        if (
            (before_unlink.st_dev, before_unlink.st_ino) != identity
            or before_unlink.st_nlink != 1
        ):
            raise BrowserPolicyError("browser_artifact_race_denied")
        artifact_path.unlink()
        if os.fstat(source_descriptor).st_nlink != 0:
            raise BrowserPolicyError("browser_artifact_race_denied")
        return registered_path, digest, registered_size
    except Exception:
        if registered_created and registered_directory_descriptor >= 0:
            try:
                os.unlink(
                    registered_name,
                    dir_fd=registered_directory_descriptor,
                )
            except FileNotFoundError:
                pass
        raise
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if registered_descriptor >= 0:
            os.close(registered_descriptor)
        if registered_directory_descriptor >= 0:
            os.close(registered_directory_descriptor)


def _rpc_result(request_id: object, result: object) -> bytes:
    return (
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "result": result},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _rpc_error(
    request_id: object,
    code: int,
    message: str,
    *,
    reason: str,
    retryable: bool = False,
) -> bytes:
    return (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": code,
                    "message": message,
                    "data": {"reason": reason, "retryable": retryable},
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )


def _validate_handshake(adapter_id: str, value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "project_id",
        "contract_version",
        "tool_schema_sha256",
        "limits",
    }:
        raise SandboxEngineError("Browser handshake denied.", code="browser_handshake_contract_mismatch")
    if value.get("project_id") != adapter_id:
        raise SandboxEngineError("Browser handshake denied.", code="browser_handshake_project_mismatch")
    if value.get("contract_version") != CONTRACT_VERSION:
        raise SandboxEngineError("Browser handshake denied.", code="browser_handshake_version_mismatch")
    if value.get("tool_schema_sha256") != BROWSER_SCHEMA_SHA256.get(adapter_id):
        raise SandboxEngineError("Browser handshake denied.", code="browser_handshake_schema_mismatch")
    if value.get("limits") != BROWSER_LIMITS:
        raise SandboxEngineError("Browser handshake denied.", code="browser_handshake_limits_mismatch")


async def _read_json_line(
    reader: asyncio.StreamReader,
    *,
    limit: int = MAX_REQUEST_BYTES,
) -> dict[str, Any]:
    raw = await reader.readline()
    if not raw or len(raw) > limit:
        raise SandboxEngineError("Request is empty or too large.", code="invalid_request")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SandboxEngineError("Request JSON is invalid.", code="invalid_request") from exc
    if not isinstance(value, dict):
        raise SandboxEngineError("Request must be an object.", code="invalid_request")
    return value


async def _relay(source: asyncio.StreamReader, destination: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await source.read(64 * 1024)
            if not chunk:
                break
            destination.write(chunk)
            await destination.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            destination.write_eof()
        except (AttributeError, ConnectionError, OSError):
            pass


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), timeout=1)
    except (asyncio.TimeoutError, ConnectionError, OSError):
        pass


def _synthetic_dns_enabled() -> bool:
    return os.getenv("MCP_BROWSER_ALLOW_SYNTHETIC_DNS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _parse_doh_answers(payload: object, record_type: str) -> tuple[str, ...]:
    expected_type = {"A": 1, "AAAA": 28}.get(record_type)
    if expected_type is None or not isinstance(payload, dict):
        raise BrowserPolicyError("browser_dns_answer_invalid")
    status = payload.get("Status")
    if not isinstance(status, int) or isinstance(status, bool):
        raise BrowserPolicyError("browser_dns_answer_invalid")
    if status != 0:
        raise BrowserPolicyError("browser_dns_failed")
    answers = payload.get("Answer", [])
    if not isinstance(answers, list) or len(answers) > DOH_MAX_ANSWERS:
        raise BrowserPolicyError("browser_dns_answer_invalid")
    addresses: list[str] = []
    for answer in answers:
        if not isinstance(answer, dict):
            raise BrowserPolicyError("browser_dns_answer_invalid")
        answer_type = answer.get("type")
        if not isinstance(answer_type, int) or isinstance(answer_type, bool):
            raise BrowserPolicyError("browser_dns_answer_invalid")
        if answer_type != expected_type:
            continue
        raw_address = answer.get("data")
        if not isinstance(raw_address, str) or not raw_address:
            raise BrowserPolicyError("browser_dns_answer_invalid")
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise BrowserPolicyError("browser_dns_answer_invalid") from exc
        if (record_type == "A") != isinstance(address, ipaddress.IPv4Address):
            raise BrowserPolicyError("browser_dns_answer_invalid")
        addresses.append(str(address))
    return tuple(dict.fromkeys(addresses))


async def _read_doh_http_body(reader: asyncio.StreamReader) -> bytes:
    try:
        header_block = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
        raise BrowserPolicyError("browser_dns_failed") from exc
    if len(header_block) > DOH_MAX_HEADER_BYTES:
        raise BrowserPolicyError("browser_dns_answer_invalid")
    lines = header_block[:-4].split(b"\r\n")
    if not lines or lines[0] not in {b"HTTP/1.1 200 OK", b"HTTP/1.0 200 OK"}:
        raise BrowserPolicyError("browser_dns_failed")
    selected_headers: dict[bytes, bytes] = {}
    selected_names = {
        b"content-length",
        b"content-type",
        b"content-encoding",
        b"transfer-encoding",
    }
    for line in lines[1:]:
        if b":" not in line:
            raise BrowserPolicyError("browser_dns_answer_invalid")
        name, value = line.split(b":", 1)
        name = name.strip().lower()
        value = value.strip().lower()
        if not name or any(byte < 33 or byte > 126 for byte in name):
            raise BrowserPolicyError("browser_dns_answer_invalid")
        if name in selected_names:
            if name in selected_headers:
                raise BrowserPolicyError("browser_dns_answer_invalid")
            selected_headers[name] = value
    content_type = selected_headers.get(b"content-type", b"")
    if not content_type.startswith(b"application/dns-json"):
        raise BrowserPolicyError("browser_dns_answer_invalid")
    if selected_headers.get(b"content-encoding"):
        raise BrowserPolicyError("browser_dns_answer_invalid")
    transfer_encoding = selected_headers.get(b"transfer-encoding", b"")
    content_length = selected_headers.get(b"content-length")
    if transfer_encoding:
        if transfer_encoding != b"chunked" or content_length is not None:
            raise BrowserPolicyError("browser_dns_answer_invalid")
        body = bytearray()
        while True:
            try:
                size_line = await reader.readuntil(b"\r\n")
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
                raise BrowserPolicyError("browser_dns_failed") from exc
            if len(size_line) > 128:
                raise BrowserPolicyError("browser_dns_answer_invalid")
            size_token = size_line[:-2].split(b";", 1)[0]
            try:
                chunk_size = int(size_token, 16)
            except ValueError as exc:
                raise BrowserPolicyError("browser_dns_answer_invalid") from exc
            if chunk_size < 0 or len(body) + chunk_size > DOH_MAX_BODY_BYTES:
                raise BrowserPolicyError("browser_dns_answer_invalid")
            if chunk_size == 0:
                trailer_size = 0
                while True:
                    try:
                        trailer_line = await reader.readuntil(b"\r\n")
                    except (
                        asyncio.IncompleteReadError,
                        asyncio.LimitOverrunError,
                    ) as exc:
                        raise BrowserPolicyError("browser_dns_failed") from exc
                    trailer_size += len(trailer_line)
                    if trailer_size > DOH_MAX_HEADER_BYTES:
                        raise BrowserPolicyError("browser_dns_answer_invalid")
                    if trailer_line == b"\r\n":
                        break
                break
            try:
                body.extend(await reader.readexactly(chunk_size))
                if await reader.readexactly(2) != b"\r\n":
                    raise BrowserPolicyError("browser_dns_answer_invalid")
            except asyncio.IncompleteReadError as exc:
                raise BrowserPolicyError("browser_dns_failed") from exc
        return bytes(body)
    if content_length is not None:
        try:
            length = int(content_length)
        except ValueError as exc:
            raise BrowserPolicyError("browser_dns_answer_invalid") from exc
        if length < 0 or length > DOH_MAX_BODY_BYTES:
            raise BrowserPolicyError("browser_dns_answer_invalid")
        try:
            return await reader.readexactly(length)
        except asyncio.IncompleteReadError as exc:
            raise BrowserPolicyError("browser_dns_failed") from exc
    body = await reader.read(DOH_MAX_BODY_BYTES + 1)
    if len(body) > DOH_MAX_BODY_BYTES:
        raise BrowserPolicyError("browser_dns_answer_invalid")
    return body


async def _query_doh_once(
    resolver_address: str,
    resolver_name: str,
    host: str,
    record_type: str,
) -> tuple[str, ...]:
    writer: asyncio.StreamWriter | None = None

    async def query() -> tuple[str, ...]:
        nonlocal writer
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        reader, writer = await asyncio.open_connection(
            resolver_address,
            443,
            ssl=context,
            server_hostname=resolver_name,
        )
        target = f"/dns-query?name={quote(host, safe='')}&type={record_type}"
        request = (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: {resolver_name}\r\n"
            "Accept: application/dns-json\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        writer.write(request)
        await writer.drain()
        body = await _read_doh_http_body(reader)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BrowserPolicyError("browser_dns_answer_invalid") from exc
        return _parse_doh_answers(payload, record_type)

    try:
        return await asyncio.wait_for(query(), timeout=DNS_TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise BrowserPolicyError("browser_dns_timeout") from exc
    except BrowserPolicyError:
        raise
    except (ConnectionError, OSError, ssl.SSLError) as exc:
        raise BrowserPolicyError("browser_dns_failed") from exc
    finally:
        if writer is not None:
            await _close_writer(writer)


async def _resolve_fixed_doh(host: str) -> tuple[str, ...]:
    failures: list[str] = []
    for resolver_address, resolver_name in DOH_RESOLVERS:
        try:
            ipv4 = await _query_doh_once(
                resolver_address, resolver_name, host, "A"
            )
            ipv6 = await _query_doh_once(
                resolver_address, resolver_name, host, "AAAA"
            )
            return validate_pinned_addresses((*ipv4, *ipv6))
        except BrowserPolicyError as exc:
            reason = str(exc)
            if reason in {
                "browser_private_dns_denied",
                "browser_dns_mixed_or_synthetic_denied",
            }:
                raise
            failures.append(reason)
    if failures and all(reason == "browser_dns_timeout" for reason in failures):
        raise BrowserPolicyError("browser_dns_timeout")
    raise BrowserPolicyError("browser_dns_failed")


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    """Terminate the upstream process group, including Chromium descendants."""

    if process.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=3)
        return
    except asyncio.TimeoutError:
        pass
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except asyncio.TimeoutError:
        # The supervisor is PID1 in production; returning lets container exit
        # destroy any descendant that escaped the original process group.
        return


@dataclass(slots=True)
class EgressGrant:
    origin: str = ""
    host: str = ""
    port: int = 0
    expires_at: float = 0.0
    active_tunnels: int = 0
    transferred_bytes: int = 0


async def _relay_limited(
    source: asyncio.StreamReader,
    destination: asyncio.StreamWriter,
    service: "BrowserEgressService",
    capability: str,
    deadline: float,
    expected_sni: str | None = None,
) -> None:
    if expected_sni is not None:
        records = bytearray()
        handshake = bytearray()
        try:
            for _ in range(4):
                remaining = deadline - time.monotonic()
                header = await asyncio.wait_for(
                    source.readexactly(5),
                    timeout=min(EGRESS_TUNNEL_IDLE_SECONDS, remaining),
                )
                record_length = int.from_bytes(header[3:5], "big")
                if header[0] != 22 or record_length <= 0 or record_length > 18 * 1024:
                    raise BrowserPolicyError("browser_tls_client_hello_invalid")
                body = await asyncio.wait_for(
                    source.readexactly(record_length),
                    timeout=min(EGRESS_TUNNEL_IDLE_SECONDS, remaining),
                )
                records.extend(header)
                records.extend(body)
                handshake.extend(body)
                if len(records) > 64 * 1024:
                    raise BrowserPolicyError("browser_tls_client_hello_invalid")
                if len(handshake) >= 4:
                    expected_length = 4 + int.from_bytes(handshake[1:4], "big")
                    if expected_length <= len(handshake):
                        break
            else:
                raise BrowserPolicyError("browser_tls_client_hello_invalid")
        except (asyncio.IncompleteReadError, asyncio.TimeoutError) as exc:
            await service.revoke(capability, "browser_tls_client_hello_invalid")
            raise BrowserPolicyError("browser_tls_client_hello_invalid") from exc
        try:
            sni = _client_hello_sni(bytes(handshake[:expected_length]))
        except BrowserPolicyError as exc:
            await service.revoke(capability, str(exc))
            raise
        if sni != expected_sni:
            await service.revoke(capability, "browser_tls_sni_denied")
            raise BrowserPolicyError("browser_tls_sni_denied")
        await service.consume(capability, len(records))
        destination.write(records)
        await destination.drain()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            await service.revoke(capability, "browser_egress_tunnel_deadline")
            raise BrowserPolicyError("browser_egress_tunnel_deadline")
        try:
            chunk = await asyncio.wait_for(
                source.read(64 * 1024),
                timeout=min(EGRESS_TUNNEL_IDLE_SECONDS, remaining),
            )
        except asyncio.TimeoutError as exc:
            raise BrowserPolicyError("browser_egress_tunnel_idle") from exc
        if not chunk:
            break
        await service.consume(capability, len(chunk))
        destination.write(chunk)
        await destination.drain()


def _client_hello_sni(data: bytes) -> str:
    """Parse a bounded reassembled TLS ClientHello handshake."""

    hello = memoryview(data)
    if len(hello) < 42 or hello[0] != 1:
        raise BrowserPolicyError("browser_tls_client_hello_invalid")
    handshake_length = int.from_bytes(hello[1:4], "big")
    if handshake_length + 4 > len(hello):
        raise BrowserPolicyError("browser_tls_client_hello_invalid")
    offset = 4 + 2 + 32
    session_length = int(hello[offset])
    offset += 1 + session_length
    if offset + 2 > len(hello):
        raise BrowserPolicyError("browser_tls_client_hello_invalid")
    cipher_length = int.from_bytes(hello[offset : offset + 2], "big")
    offset += 2 + cipher_length
    if offset >= len(hello):
        raise BrowserPolicyError("browser_tls_client_hello_invalid")
    compression_length = int(hello[offset])
    offset += 1 + compression_length
    if offset + 2 > len(hello):
        raise BrowserPolicyError("browser_tls_client_hello_invalid")
    extensions_length = int.from_bytes(hello[offset : offset + 2], "big")
    offset += 2
    end = offset + extensions_length
    if end > len(hello):
        raise BrowserPolicyError("browser_tls_client_hello_invalid")
    found_sni: str | None = None
    seen_sni_extension = False
    while offset + 4 <= end:
        extension_type = int.from_bytes(hello[offset : offset + 2], "big")
        extension_length = int.from_bytes(hello[offset + 2 : offset + 4], "big")
        offset += 4
        extension_end = offset + extension_length
        if extension_end > end:
            raise BrowserPolicyError("browser_tls_client_hello_invalid")
        if extension_type == 0xFE0D:
            raise BrowserPolicyError("browser_tls_ech_denied")
        if extension_type == 0:
            if seen_sni_extension:
                raise BrowserPolicyError("browser_tls_sni_denied")
            seen_sni_extension = True
            if extension_length < 5:
                raise BrowserPolicyError("browser_tls_sni_denied")
            names_length = int.from_bytes(hello[offset : offset + 2], "big")
            cursor = offset + 2
            names_end = cursor + names_length
            if names_end != extension_end:
                raise BrowserPolicyError("browser_tls_sni_denied")
            while cursor + 3 <= names_end:
                name_type = int(hello[cursor])
                name_length = int.from_bytes(hello[cursor + 1 : cursor + 3], "big")
                cursor += 3
                raw_name = bytes(hello[cursor : cursor + name_length])
                cursor += name_length
                if name_type == 0:
                    if found_sni is not None:
                        raise BrowserPolicyError("browser_tls_sni_denied")
                    try:
                        found_sni = raw_name.decode("ascii").lower().rstrip(".")
                    except UnicodeError as exc:
                        raise BrowserPolicyError("browser_tls_sni_denied") from exc
            if cursor != names_end:
                raise BrowserPolicyError("browser_tls_sni_denied")
        offset = extension_end
    if offset != end or found_sni is None:
        raise BrowserPolicyError("browser_tls_sni_denied")
    return found_sni


class BrowserEgressService:
    """Internet-connected half of the fail-closed browser proxy."""

    def __init__(self, control_key: str) -> None:
        self.control_key = control_key
        self.grants: dict[str, EgressGrant] = {}
        self.lock = asyncio.Lock()
        self.watchers: set[asyncio.StreamWriter] = set()
        self.tunnel_writers: set[asyncio.StreamWriter] = set()
        self.shutdown_event = asyncio.Event()
        self.watcher_ready = asyncio.Event()
        self._watcher_claimed = False
        self._shutting_down = False

    async def shutdown(self) -> None:
        """Revoke every grant, close active tunnels, and stop the egress PID1."""

        async with self.lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            self.grants.clear()
            writers = tuple(self.tunnel_writers)
            self.tunnel_writers.clear()
        for tunnel_writer in writers:
            tunnel_writer.close()
        if writers:
            await asyncio.gather(
                *(_close_writer(tunnel_writer) for tunnel_writer in writers),
                return_exceptions=True,
            )
        self.shutdown_event.set()

    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        # Synthetic fixture DNS is an explicit acceptance-only mode. Production
        # resolution bypasses host DNS entirely because Docker Desktop may
        # return synthetic 198.18/15 answers even when a public resolver is set.
        if not _synthetic_dns_enabled():
            return await _resolve_fixed_doh(host)
        try:
            records = await asyncio.wait_for(
                asyncio.get_running_loop().getaddrinfo(
                    host,
                    port,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                ),
                timeout=DNS_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise BrowserPolicyError("browser_dns_timeout") from exc
        except socket.gaierror as exc:
            raise BrowserPolicyError("browser_dns_failed") from exc
        return validate_pinned_records(records)

    async def _notify(self, capability: str, reason: str) -> None:
        payload = json.dumps(
            {"event": "revoked", "capability": capability, "reason": reason},
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        stale: list[asyncio.StreamWriter] = []
        for watcher in tuple(self.watchers):
            try:
                watcher.write(payload)
                await asyncio.wait_for(watcher.drain(), timeout=1)
            except (asyncio.TimeoutError, ConnectionError, OSError):
                stale.append(watcher)
        for watcher in stale:
            self.watchers.discard(watcher)
        if stale:
            await asyncio.gather(
                *(_close_writer(watcher) for watcher in stale),
                return_exceptions=True,
            )
            await self.shutdown()

    async def revoke(self, capability: str, reason: str) -> None:
        async with self.lock:
            existed = self.grants.pop(capability, None) is not None
        if existed:
            await self._notify(capability, reason)

    async def consume(self, capability: str, amount: int) -> None:
        exceeded = False
        async with self.lock:
            grant = self._grant(capability)
            if amount < 0 or grant.transferred_bytes + amount > MAX_EGRESS_BYTES_PER_SESSION:
                self.grants.pop(capability, None)
                exceeded = True
            else:
                grant.transferred_bytes += amount
        if exceeded:
            await self._notify(capability, "browser_egress_byte_budget")
            raise BrowserPolicyError("browser_egress_byte_budget")

    async def _authenticate_control(self, request: dict[str, Any]) -> None:
        if not secrets.compare_digest(str(request.get("control_key") or ""), self.control_key):
            raise SandboxEngineError("Egress control denied.", code="browser_egress_control_denied")

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        tunnel = False
        shutdown_after_response = False
        try:
            request = await _read_json_line(reader)
            action = str(request.get("action") or "")
            if action == "health":
                response = {
                    "ok": True,
                    "protocol": "modelmirror-browser-egress-v1",
                    "active_grants": len(self.grants),
                    "max_tunnels_per_session": MAX_EGRESS_TUNNELS_PER_SESSION,
                    "max_bytes_per_session": MAX_EGRESS_BYTES_PER_SESSION,
                }
            elif action == "watch":
                await self._authenticate_control(request)
                async with self.lock:
                    if self._watcher_claimed or self._shutting_down:
                        raise SandboxEngineError(
                            "Egress watcher already consumed.",
                            code="browser_egress_watcher_consumed",
                        )
                    self._watcher_claimed = True
                    self.watchers.add(writer)
                try:
                    writer.write(b'{"ok":true,"protocol":"modelmirror-browser-egress-v1"}\n')
                    await asyncio.wait_for(writer.drain(), timeout=2)
                    self.watcher_ready.set()
                    await reader.read()
                finally:
                    self.watchers.discard(writer)
                    await _close_writer(writer)
                    await self.shutdown()
                return
            elif action == "register":
                await self._authenticate_control(request)
                capability = str(request.get("capability") or "")
                if len(capability) != 64:
                    raise SandboxEngineError("Egress capability invalid.", code="browser_egress_capability_invalid")
                async with self.lock:
                    if self._shutting_down:
                        raise SandboxEngineError(
                            "Egress is shutting down.", code="browser_egress_unavailable"
                        )
                    self._purge()
                    if capability not in self.grants and len(self.grants) >= MAX_SESSIONS:
                        raise SandboxEngineError("Egress session limit reached.", code="browser_session_limit")
                    self.grants[capability] = EgressGrant(
                        expires_at=time.monotonic() + SESSION_TTL_SECONDS
                    )
                response = {"ok": True, "protocol": "modelmirror-browser-egress-v1"}
            elif action == "authorize":
                await self._authenticate_control(request)
                capability = str(request.get("capability") or "")
                normalized, origin, host, port = validate_browser_url(request.get("url"))
                del normalized
                # Validate every DNS answer at authorization time and again at
                # connection time to close DNS rebinding races.
                await self.resolve(host, port)
                async with self.lock:
                    grant = self._grant(capability)
                    if grant.origin and (origin, host, port) != (
                        grant.origin,
                        grant.host,
                        grant.port,
                    ):
                        raise SandboxEngineError(
                            "Cross-origin authorization denied.",
                            code="browser_cross_origin_denied",
                        )
                    grant.origin = origin
                    grant.host = host
                    grant.port = port
                response = {"ok": True, "protocol": "modelmirror-browser-egress-v1"}
            elif action == "revoke":
                await self._authenticate_control(request)
                await self.revoke(str(request.get("capability") or ""), "browser_session_closed")
                response = {"ok": True, "protocol": "modelmirror-browser-egress-v1"}
            elif action == "shutdown":
                await self._authenticate_control(request)
                response = {"ok": True, "protocol": "modelmirror-browser-egress-v1"}
                shutdown_after_response = True
            elif action == "connect":
                capability = str(request.get("capability") or "")
                normalized, origin, host, port = validate_browser_url(request.get("url"), allow_login_path=True)
                del normalized
                async with self.lock:
                    grant = self._grant(capability)
                    if (origin, host, port) != (grant.origin, grant.host, grant.port):
                        raise SandboxEngineError("Cross-origin egress denied.", code="browser_cross_origin_denied")
                    if grant.active_tunnels >= MAX_EGRESS_TUNNELS_PER_SESSION:
                        raise SandboxEngineError("Egress tunnel limit reached.", code="browser_egress_tunnel_limit")
                    grant.active_tunnels += 1
                    self.tunnel_writers.add(writer)
                remote_writer: asyncio.StreamWriter | None = None
                try:
                    addresses = await self.resolve(host, port)
                    remote_reader, remote_writer = await asyncio.wait_for(
                        asyncio.open_connection(addresses[0], port), timeout=10
                    )
                    self.tunnel_writers.add(remote_writer)
                    # Re-check after DNS and connect, before acknowledging the
                    # tunnel.  A concurrent revoke/expiry must close the newly
                    # opened remote socket without forwarding a byte.
                    async with self.lock:
                        current = self._grant(capability)
                        if (origin, host, port) != (
                            current.origin,
                            current.host,
                            current.port,
                        ):
                            raise SandboxEngineError(
                                "Cross-origin egress denied.",
                                code="browser_cross_origin_denied",
                            )
                    writer.write(b'{"ok":true,"protocol":"modelmirror-browser-egress-v1"}\n')
                    await writer.drain()
                    tunnel = True
                    deadline = time.monotonic() + EGRESS_TUNNEL_TTL_SECONDS
                    tasks = [
                        asyncio.create_task(
                            _relay_limited(
                                reader,
                                remote_writer,
                                self,
                                capability,
                                deadline,
                                expected_sni=host if origin.startswith("https://") else None,
                            )
                        ),
                        asyncio.create_task(
                            _relay_limited(remote_reader, writer, self, capability, deadline)
                        ),
                    ]
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    results = await asyncio.gather(*done, *pending, return_exceptions=True)
                    for result in results:
                        if isinstance(result, BrowserPolicyError):
                            raise result
                    return
                finally:
                    async with self.lock:
                        grant = self.grants.get(capability)
                        if grant is not None:
                            grant.active_tunnels = max(0, grant.active_tunnels - 1)
                        self.tunnel_writers.discard(writer)
                        if remote_writer is not None:
                            self.tunnel_writers.discard(remote_writer)
                    if remote_writer is not None:
                        await _close_writer(remote_writer)
                    if tunnel:
                        await _close_writer(writer)
            else:
                raise SandboxEngineError("Egress action denied.", code="action_denied")
        except BrowserPolicyError as exc:
            response = {"ok": False, "code": str(exc), "error": "browser_egress_denied"}
        except SandboxEngineError as exc:
            response = {"ok": False, "code": exc.code, "error": "browser_egress_denied"}
        except (asyncio.TimeoutError, ConnectionError, OSError):
            response = {"ok": False, "code": "browser_egress_unavailable", "error": "browser_egress_denied"}
        if not tunnel:
            try:
                writer.write(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
                await asyncio.wait_for(writer.drain(), timeout=2)
            except (asyncio.TimeoutError, ConnectionError, OSError):
                pass
            finally:
                await _close_writer(writer)
                if shutdown_after_response:
                    await self.shutdown()

    def _purge(self) -> None:
        now = time.monotonic()
        for capability in [key for key, grant in self.grants.items() if grant.expires_at <= now]:
            self.grants.pop(capability, None)

    def _grant(self, capability: str) -> EgressGrant:
        if self._shutting_down:
            raise SandboxEngineError("Egress is shutting down.", code="browser_egress_unavailable")
        self._purge()
        grant = self.grants.get(capability)
        if grant is None:
            raise SandboxEngineError("Egress capability denied.", code="browser_egress_capability_denied")
        return grant


class BrowserEgressClient:
    def __init__(self, control_key: str) -> None:
        self.control_key = control_key
        self.capability = secrets.token_hex(32)
        self.revocation_reason = ""

    async def _control(self, action: str, **values: object) -> None:
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(EGRESS_SOCKET_PATH)), timeout=2
            )
            request = {
                "action": action,
                "control_key": self.control_key,
                "capability": self.capability,
                **values,
            }
            writer.write(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
            await asyncio.wait_for(writer.drain(), timeout=2)
            response = await asyncio.wait_for(
                _read_json_line(reader, limit=4096), timeout=2
            )
        finally:
            if writer is not None:
                await _close_writer(writer)
        if response.get("ok") is not True:
            raise BrowserPolicyError(str(response.get("code") or "browser_egress_unavailable"))

    async def register(self) -> None:
        await self._control("register")
        EGRESS_CLIENTS[self.capability] = self

    async def authorize(self, url: str) -> None:
        await self._control("authorize", url=url)

    async def revoke(self) -> None:
        try:
            await self._control("revoke")
        except (asyncio.TimeoutError, BrowserPolicyError, ConnectionError, OSError):
            pass
        finally:
            EGRESS_CLIENTS.pop(self.capability, None)

    async def open_tunnel(self, url: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        writer: asyncio.StreamWriter | None = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(EGRESS_SOCKET_PATH)), timeout=2
            )
            writer.write(
                json.dumps(
                    {"action": "connect", "capability": self.capability, "url": url},
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            await asyncio.wait_for(writer.drain(), timeout=2)
            response = await asyncio.wait_for(
                _read_json_line(reader, limit=4096), timeout=10
            )
            if response.get("ok") is not True:
                raise BrowserPolicyError(
                    str(response.get("code") or "browser_egress_unavailable")
                )
            return reader, writer
        except BaseException:
            if writer is not None:
                await _close_writer(writer)
            raise


EGRESS_CLIENTS: dict[str, BrowserEgressClient] = {}


async def _watch_egress(control_key: str, ready: asyncio.Event) -> None:
    """Exit the browser supervisor when egress restarts or becomes unavailable."""

    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(str(EGRESS_SOCKET_PATH)), timeout=2
    )
    writer.write(
        json.dumps(
            {"action": "watch", "control_key": control_key}, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
    )
    await asyncio.wait_for(writer.drain(), timeout=2)
    response = await asyncio.wait_for(_read_json_line(reader, limit=4096), timeout=2)
    if response.get("ok") is not True:
        await _close_writer(writer)
        raise RuntimeError("browser_egress_watch_denied")
    ready.set()
    try:
        while True:
            raw = await reader.readline()
            if not raw:
                raise RuntimeError("browser_egress_restarted")
            event = json.loads(raw.decode("utf-8"))
            if not isinstance(event, dict) or event.get("event") != "revoked":
                continue
            client = EGRESS_CLIENTS.get(str(event.get("capability") or ""))
            if client is not None:
                client.revocation_reason = str(
                    event.get("reason") or "browser_egress_revoked"
                )
                fixed_event = BROWSER_PROXY_RUNTIME_EVENTS.get(
                    client.revocation_reason
                )
                if fixed_event:
                    _emit_browser_runtime_event(fixed_event)
    finally:
        await _close_writer(writer)


async def _request_egress_shutdown(control_key: str) -> None:
    """Best-effort authenticated pair shutdown after the browser PID1 stops."""

    writer: asyncio.StreamWriter | None = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(EGRESS_SOCKET_PATH)), timeout=2
        )
        writer.write(
            json.dumps(
                {"action": "shutdown", "control_key": control_key},
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        await asyncio.wait_for(writer.drain(), timeout=2)
        await asyncio.wait_for(_read_json_line(reader, limit=4096), timeout=2)
    except (asyncio.TimeoutError, ConnectionError, OSError, SandboxEngineError):
        # Egress may already have exited or rotated its one-shot key.  Either
        # case is fail-closed, and Docker will restart the pair.
        return
    finally:
        if writer is not None:
            await _close_writer(writer)


async def _enforce_egress_watch_bootstrap(service: BrowserEgressService) -> None:
    """Rotate the pair if the browser dies after consuming the control key."""

    try:
        await asyncio.wait_for(
            service.watcher_ready.wait(), timeout=EGRESS_WATCH_BOOTSTRAP_SECONDS
        )
    except asyncio.TimeoutError:
        await service.shutdown()


class LoopbackBrowserProxy:
    """Browser-facing proxy; it has no Internet route of its own."""

    def __init__(self, egress: BrowserEgressClient) -> None:
        self.egress = egress
        self.server: asyncio.AbstractServer | None = None
        self.origin = ""
        self.host = ""
        self.port = 0
        self.last_violation = ""
        self.last_violation_event = ""
        self.forwarded_requests = 0
        self.authorized_at = 0.0
        self.suppressed_android_client_probes = 0

    async def start(self) -> str:
        self.server = await asyncio.start_server(self.handle, host="127.0.0.1", port=0)
        socket_info = self.server.sockets[0].getsockname()
        return f"http://127.0.0.1:{int(socket_info[1])}"

    async def authorize(self, url: str) -> str:
        normalized, origin, host, port = validate_browser_url(url)
        if self.origin and (origin, host, port) != (self.origin, self.host, self.port):
            raise BrowserPolicyError("browser_cross_origin_denied")
        await self.egress.authorize(normalized)
        self.origin, self.host, self.port = origin, host, port
        self.last_violation = ""
        self.last_violation_event = ""
        self.forwarded_requests = 0
        self.authorized_at = time.monotonic()
        self.suppressed_android_client_probes = 0
        return normalized

    def _suppress_blocked_android_client_probe(
        self, method: str, target: str, event: str, remote_writer: object | None
    ) -> bool:
        """Keep a small bounded set of Chrome GCM retries blocked without taint.

        The request has already failed the exact-origin check, so this never
        reaches DNS or the egress service.  A fifth attempt, another method,
        another authority, or an attempt outside the bounded bootstrap window
        retains the normal fail-closed taint behavior.
        """

        age = time.monotonic() - self.authorized_at
        if not (
            remote_writer is None
            and method.upper() == "CONNECT"
            and target.lower() == "android.clients.google.com:443"
            and event == "proxy_policy_cross_origin_android_client_after_forward"
            and self.forwarded_requests > 0
            and self.suppressed_android_client_probes < 4
            and 0 <= age <= 180
        ):
            return False
        self.suppressed_android_client_probes += 1
        _emit_browser_runtime_event(
            "proxy_policy_android_client_background_blocked_no_taint"
        )
        return True

    def _cross_origin_event(self, method: str, target: str) -> str:
        """Classify only the mismatch shape; never retain or emit the target."""

        from urllib.parse import urlsplit

        relation = "unknown"
        try:
            candidate = f"https://{target}/" if method.upper() == "CONNECT" else target
            split = urlsplit(candidate)
            scheme = split.scheme.lower()
            host = (split.hostname or "").lower().rstrip(".")
            port = split.port or (443 if scheme == "https" else 80)
            expected_scheme = self.origin.split(":", 1)[0]
            if host != self.host:
                relation = BROWSER_BACKGROUND_HOST_CATEGORIES.get(host, "")
                if relation == "google_home":
                    if method.upper() == "CONNECT":
                        relation = "google_home_connect"
                    elif split.path.startswith("/searchdomaincheck"):
                        relation = "search_domain"
                    elif split.path.endswith(("/generate_204", "/gen_204")):
                        relation = "connectivity"
                    elif split.path.startswith("/complete/"):
                        relation = "search_suggest"
                    elif split.path.startswith(("/chrome/", "/_/Chrome")):
                        relation = "google_chrome_service"
                    elif split.path.endswith("/favicon.ico"):
                        relation = "google_favicon"
                    elif split.path in {"", "/"}:
                        relation = "google_home_root"
                    else:
                        relation = "google_home_other"
                if not relation:
                    relation = next(
                        (
                            category
                            for suffix, category in BROWSER_VENDOR_SUFFIX_CATEGORIES.items()
                            if host == suffix or host.endswith(f".{suffix}")
                        ),
                        "external_host",
                    )
            elif scheme != expected_scheme:
                relation = "scheme"
            elif port != self.port:
                relation = "port"
        except (TypeError, ValueError):
            relation = "unknown"
        phase = "after_forward" if self.forwarded_requests else "before_forward"
        return f"proxy_policy_cross_origin_{relation}_{phase}"

    def _target(self, url: str, *, allow_login_path: bool = False) -> tuple[str, str, str, int]:
        normalized, origin, host, port = validate_browser_url(url, allow_login_path=allow_login_path)
        if not self.origin:
            raise BrowserPolicyError("browser_origin_not_set")
        if (origin, host, port) != (self.origin, self.host, self.port):
            raise BrowserPolicyError("browser_cross_origin_denied")
        return normalized, origin, host, port

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        remote_writer: asyncio.StreamWriter | None = None
        method = ""
        target = ""
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=10)
            if not request_line or len(request_line) > 8192:
                raise BrowserPolicyError("browser_proxy_request_invalid")
            header_bytes = bytearray()
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=10)
                if not line:
                    raise BrowserPolicyError("browser_proxy_request_invalid")
                header_bytes.extend(line)
                if len(header_bytes) > MAX_PROXY_HEADER_BYTES:
                    raise BrowserPolicyError("browser_proxy_headers_too_large")
                if line in {b"\r\n", b"\n"}:
                    break
            first = request_line.decode("latin-1").strip().split(" ")
            if len(first) != 3:
                raise BrowserPolicyError("browser_proxy_request_invalid")
            method, target, version = first
            headers_text = bytes(header_bytes).decode("latin-1")
            parsed_headers: list[tuple[str, str, str]] = []
            for line in headers_text.splitlines():
                if not line or ":" not in line:
                    if line:
                        raise BrowserPolicyError("browser_proxy_request_invalid")
                    continue
                raw_name, raw_value = line.split(":", 1)
                name = raw_name.strip().lower()
                value = raw_value.strip()
                if not name:
                    raise BrowserPolicyError("browser_proxy_request_invalid")
                connection_tokens = {
                    token.strip().lower() for token in value.split(",") if token.strip()
                }
                if name in {"proxy-authorization", "upgrade"} or (
                    name == "connection" and "upgrade" in connection_tokens
                ):
                    raise BrowserPolicyError("browser_websocket_or_proxy_auth_denied")
                parsed_headers.append((name, value, line))
            if method.upper() == "CONNECT":
                match = AUTHORITY.fullmatch(target)
                if not match:
                    raise BrowserPolicyError("browser_proxy_authority_invalid")
                host = match.group("host")
                port = int(match.group("port"))
                normalized, _, _, _ = self._target(
                    f"https://{host}:{port}/", allow_login_path=True
                )
                remote_reader, remote_writer = await self.egress.open_tunnel(normalized)
                self.forwarded_requests += 1
                writer.write(b"HTTP/1.1 200 Connection Established\r\nConnection: close\r\n\r\n")
                await writer.drain()
                tasks = [
                    asyncio.create_task(_relay(reader, remote_writer)),
                    asyncio.create_task(_relay(remote_reader, writer)),
                ]
            else:
                if method.upper() not in {"GET", "HEAD", "POST"}:
                    raise BrowserPolicyError("browser_http_method_denied")
                normalized, _, _, _ = self._target(target)
                parsed = validate_browser_url(normalized)[0]
                from urllib.parse import urlsplit

                split = urlsplit(parsed)
                path = split.path or "/"
                if split.query:
                    path = f"{path}?{split.query}"
                filtered = []
                host_values: list[str] = []
                for name, value, line in parsed_headers:
                    if name == "host":
                        host_values.append(value.lower())
                        continue
                    if name in {"proxy-connection", "proxy-authorization", "connection"}:
                        continue
                    filtered.append(line)
                expected_host = self.host if self.port in {80, 443} else f"{self.host}:{self.port}"
                if len(host_values) != 1 or host_values[0].rstrip(".") != expected_host:
                    raise BrowserPolicyError("browser_http_host_mismatch")
                filtered.append(f"Host: {expected_host}")
                filtered.append("Connection: close")
                remote_reader, remote_writer = await self.egress.open_tunnel(normalized)
                self.forwarded_requests += 1
                remote_writer.write(
                    f"{method.upper()} {path} {version}\r\n".encode("latin-1")
                    + "\r\n".join(filtered).encode("latin-1")
                    + b"\r\n\r\n"
                )
                await remote_writer.drain()
                tasks = [
                    asyncio.create_task(_relay(reader, remote_writer)),
                    asyncio.create_task(_relay(remote_reader, writer)),
                ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        except BrowserPolicyError as exc:
            violation = str(exc)
            violation_event = (
                self._cross_origin_event(method, target)
                if violation == "browser_cross_origin_denied"
                else _proxy_runtime_event(violation)
            )
            if not self._suppress_blocked_android_client_probe(
                method, target, violation_event, remote_writer
            ):
                self.last_violation = violation
                self.last_violation_event = violation_event
            writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            try:
                await writer.drain()
            except (ConnectionError, OSError):
                pass
        except (asyncio.TimeoutError, ConnectionError, OSError):
            self.last_violation = "browser_egress_unavailable"
            self.last_violation_event = "proxy_policy_egress_unavailable"
        finally:
            if remote_writer is not None:
                await _close_writer(remote_writer)
            await _close_writer(writer)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            try:
                await asyncio.wait_for(self.server.wait_closed(), timeout=2)
            except asyncio.TimeoutError:
                pass


class UpstreamRpc:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        artifact_dir: Path,
    ) -> None:
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("browser_upstream_stdio_unavailable")
        self.process = process
        self.stdin = process.stdin
        self.stdout = process.stdout
        self.artifact_dir = artifact_dir
        self.counter = 0

    async def notify(self, method: str, params: object | None = None) -> None:
        payload: dict[str, object] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self.stdin.write(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
        await self.stdin.drain()

    async def request(
        self,
        method: str,
        params: object,
        *,
        timeout: float = TOOL_CALL_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        self.counter += 1
        request_id = f"modelmirror-internal-{self.counter}"
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        self.stdin.write(json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n")
        await self.stdin.drain()
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            raw = await asyncio.wait_for(self.stdout.readline(), timeout=remaining)
            if not raw or len(raw) > MAX_MCP_MESSAGE_BYTES:
                raise RuntimeError("browser_upstream_output_invalid")
            try:
                response = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(response, dict):
                continue
            if response.get("method") == "roots/list" and response.get("id") is not None:
                root_uri = self.artifact_dir.resolve().as_uri()
                self.stdin.write(
                    _rpc_result(
                        response["id"],
                        {"roots": [{"uri": root_uri, "name": "browser-artifacts"}]},
                    )
                )
                await self.stdin.drain()
                continue
            if response.get("id") == request_id:
                return response


@dataclass(frozen=True, slots=True)
class RefBinding:
    generation: str
    revision: int
    digest: str
    context: str
    role: str
    label: str


@dataclass(slots=True)
class BrowserSessionState:
    adapter_id: str
    session_id: str
    generation: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_activity: float = field(default_factory=time.monotonic)
    page_revision: int = 0
    page_digest: str = field(default_factory=lambda: hashlib.sha256(b"about:blank").hexdigest())
    current_origin: str = ""
    action_count: int = 0
    tainted: bool = False
    taint_reason: str = ""
    refs: dict[str, RefBinding] = field(default_factory=dict)

    def bump(self) -> None:
        self.page_revision += 1
        self.refs.clear()
        self.last_activity = time.monotonic()

    def taint(self, reason: str) -> None:
        self.tainted = True
        self.taint_reason = reason
        self.refs.clear()
        self.page_revision += 1
        self.last_activity = time.monotonic()

    def status(self) -> dict[str, object]:
        return {
            "status": "tainted" if self.tainted else "active",
            "generation": self.generation,
            "page_revision": self.page_revision,
            "page_digest": self.page_digest,
            "current_origin": self.current_origin,
            "action_count": self.action_count,
            "max_actions": MAX_ACTIONS,
            "tainted": self.tainted,
            "expires_at": session_expiry(self.started_at),
        }


def _safe_element(
    ref: str, element: SnapshotElement, digest: str
) -> dict[str, str] | None:
    try:
        assert_non_sensitive_interaction(element.context)
    except BrowserPolicyError:
        return None
    role = element.role
    label = re.sub(r"\s+", " ", element.name).strip(" -\t")[:120]
    if not label:
        label = role
    return {"ref": ref, "role": role, "label": label, "page_digest": digest}


def _page_count(adapter_id: str, payload: object) -> int:
    text = result_text(payload)
    if adapter_id == "chrome-devtools-mcp":
        return len(re.findall(r"(?m)^\s*[0-9]+:\s", text))
    return len(re.findall(r"(?m)^\s*-\s*[0-9]+:\s", text))


async def _drain_stderr(
    stream: asyncio.StreamReader | None,
    classifier: SafeStderrClassifier | None = None,
) -> None:
    if stream is None:
        return
    while chunk := await stream.read(4096):
        if classifier is not None:
            classifier.feed(chunk)


class BrowserGatewaySession:
    def __init__(
        self,
        adapter_id: str,
        state: BrowserSessionState,
        process: asyncio.subprocess.Process,
        rpc: UpstreamRpc,
        proxy: LoopbackBrowserProxy,
        artifact_dir: Path,
        staging_dir: Path,
        registered_dir: Path,
    ) -> None:
        self.adapter_id = adapter_id
        self.contract = BROWSER_ADAPTERS[adapter_id]
        self.state = state
        self.process = process
        self.rpc = rpc
        self.proxy = proxy
        self.artifact_dir = artifact_dir
        self.staging_dir = staging_dir
        self.registered_dir = registered_dir
        self.registered_artifacts: set[Path] = set()
        self.unregistered_artifact_event = "unregistered_artifact"
        self.initialize_result: dict[str, object] = {}

    async def preflight(self) -> None:
        init = await self.rpc.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": {"name": "ModelMirror browser gateway", "version": CONTRACT_VERSION},
            },
        )
        result = init.get("result")
        info = result.get("serverInfo") if isinstance(result, dict) else None
        if not isinstance(info, dict) or (
            info.get("name"), info.get("version")
        ) != (self.contract.upstream_server_name, self.contract.upstream_server_version):
            raise RuntimeError("browser_upstream_identity_drift")
        self.initialize_result = dict(result)
        await self.rpc.notify("notifications/initialized", {})
        listing = await self.rpc.request("tools/list", {})
        list_result = listing.get("result")
        tools = list_result.get("tools") if isinstance(list_result, dict) else None
        digest = upstream_schema_digest(self.adapter_id, tools)
        if digest != UPSTREAM_SCHEMA_SHA256.get(self.adapter_id):
            raise RuntimeError("browser_upstream_schema_drift")
        # This representative call starts the single browser process and proves
        # that the locked package can talk to the pinned Chromium build.
        snapshot = await self._internal_snapshot()
        if result_failed(snapshot):
            category = _classify_upstream_failure(snapshot)
            raise RuntimeError(f"browser_upstream_representative_{category}")
        pages = await self._internal_pages()
        if result_failed(pages) or _page_count(self.adapter_id, pages) != 1:
            raise RuntimeError("browser_upstream_page_contract_failed")

    async def _internal_snapshot(self) -> dict[str, Any]:
        name = "take_snapshot" if self.adapter_id == "chrome-devtools-mcp" else "browser_snapshot"
        return await self.rpc.request("tools/call", {"name": name, "arguments": {}})

    async def _internal_pages(self) -> dict[str, Any]:
        if self.adapter_id == "chrome-devtools-mcp":
            return await self.rpc.request("tools/call", {"name": "list_pages", "arguments": {}})
        return await self.rpc.request(
            "tools/call", {"name": "browser_tabs", "arguments": {"action": "list"}}
        )

    async def _observe(
        self,
    ) -> tuple[str, dict[str, SnapshotElement], str, str, str]:
        pages = await self._internal_pages()
        if result_failed(pages) or _page_count(self.adapter_id, pages) != 1:
            raise BrowserPolicyError("browser_page_count_violation")
        snapshot = await self._internal_snapshot()
        if result_failed(snapshot):
            raise BrowserPolicyError("browser_snapshot_verification_failed")
        try:
            text, refs, observed_url, title = extract_snapshot(self.adapter_id, snapshot)
        except SnapshotStructureError as exc:
            await self._taint("browser_snapshot_ref_structure_invalid")
            raise BrowserPolicyError("browser_snapshot_ref_structure_invalid") from exc
        if len(text.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise BrowserPolicyError("browser_output_too_large")
        if observed_url and observed_url != "about:blank":
            _, observed_origin, _, _ = validate_browser_url(observed_url)
            if self.state.current_origin and observed_origin != self.state.current_origin:
                raise BrowserPolicyError("browser_cross_origin_denied")
        digest = page_digest(self.state.current_origin, observed_url, title, text)
        return text, refs, observed_url, title, digest

    async def _verify_bound_refs(self, refs: list[str]) -> None:
        if not refs:
            raise BrowserPolicyError("browser_ref_invalid")
        for ref in refs:
            binding = self.state.refs.get(ref)
            if binding is None or (
                binding.generation,
                binding.revision,
                binding.digest,
            ) != (
                self.state.generation,
                self.state.page_revision,
                self.state.page_digest,
            ):
                raise BrowserPolicyError("browser_ref_stale")
        _, observed_refs, _, _, digest = await self._observe()
        if digest != self.state.page_digest or any(ref not in observed_refs for ref in refs):
            self.state.bump()
            self.state.page_digest = digest
            raise BrowserPolicyError("browser_state_drift")

    async def _terminate_upstream(self) -> None:
        await _terminate_process_group(self.process)

    async def _taint(self, reason: str) -> None:
        self.state.taint(reason)
        await self._terminate_upstream()

    def _purge_unregistered_artifacts(self, *, allowed_pending: Path | None = None) -> bool:
        """Reject and best-effort remove every unregistered artifact entry.

        Playwright writes downloads into its output directory.  Keeping a
        before/after inventory around every tool call makes a click-triggered
        download an immediate taint instead of a durable artifact escape.  The
        inventory deliberately uses ``lstat``-style metadata and never follows
        browser-created links.  Nested directories and special files are
        policy violations too; they must not disappear from the inventory just
        because ``Path.is_file()`` returns false.
        """

        unexpected = False
        categories: set[str] = set()
        lexical = lambda value: Path(os.path.abspath(os.fspath(value)))
        allowed = {lexical(path) for path in self.registered_artifacts}
        if allowed_pending is not None:
            allowed.add(lexical(allowed_pending))

        expected_directories = {
            lexical(self.staging_dir),
            lexical(self.registered_dir),
        }
        seen_directories: set[Path] = set()

        def record_unexpected(path: Path, metadata: os.stat_result | None) -> None:
            nonlocal unexpected
            unexpected = True
            mode = metadata.st_mode if metadata is not None else 0
            if metadata is not None and stat.S_ISREG(mode):
                name = path.name
                if re.fullmatch(
                    r"console-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9-]+Z\.log",
                    name,
                ):
                    categories.add("console_log")
                elif (
                    name.startswith("download-")
                    or name.endswith(".download")
                    or path.suffix == ".bin"
                ):
                    categories.add("download")
                elif path.suffix in {".trace", ".network", ".stacks", ".crtrace"}:
                    categories.add("trace")
                else:
                    categories.add("other")
            else:
                categories.add("other")

            # Removing an entry never follows its final symlink.  Unexpected
            # non-empty directories remain for the session-level teardown,
            # after the caller has tainted and killed the upstream process.
            try:
                if metadata is not None and stat.S_ISDIR(mode):
                    path.rmdir()
                else:
                    path.unlink()
            except OSError:
                pass

        try:
            root_metadata = os.lstat(self.artifact_dir)
            root_entries = list(os.scandir(self.artifact_dir))
        except OSError:
            self.unregistered_artifact_event = "unregistered_artifact_other"
            return True
        if not stat.S_ISDIR(root_metadata.st_mode):
            self.unregistered_artifact_event = "unregistered_artifact_other"
            return True

        for root_entry in root_entries:
            path = lexical(Path(root_entry.path))
            try:
                metadata = root_entry.stat(follow_symlinks=False)
            except OSError:
                record_unexpected(path, None)
                continue
            if path not in expected_directories or not stat.S_ISDIR(metadata.st_mode):
                record_unexpected(path, metadata)
                continue
            seen_directories.add(path)
            try:
                children = list(os.scandir(path))
            except OSError:
                record_unexpected(path, metadata)
                continue
            for child in children:
                child_path = lexical(Path(child.path))
                try:
                    child_metadata = child.stat(follow_symlinks=False)
                except OSError:
                    record_unexpected(child_path, None)
                    continue
                if (
                    stat.S_ISREG(child_metadata.st_mode)
                    and child_metadata.st_nlink == 1
                    and child_path in allowed
                ):
                    continue
                record_unexpected(child_path, child_metadata)

        if seen_directories != expected_directories:
            unexpected = True
            categories.add("other")
        if unexpected:
            category = next(iter(categories)) if len(categories) == 1 else "mixed"
            self.unregistered_artifact_event = f"unregistered_artifact_{category}"
        else:
            self.unregistered_artifact_event = "unregistered_artifact"
        return unexpected

    def _check_lifetime(self) -> None:
        now = time.monotonic()
        age = (datetime.now(UTC) - self.state.started_at).total_seconds()
        if age >= SESSION_TTL_SECONDS:
            raise BrowserPolicyError("browser_session_expired")
        if now - self.state.last_activity >= IDLE_TTL_SECONDS:
            raise BrowserPolicyError("browser_session_idle_expired")

    async def status(self) -> dict[str, object]:
        self._check_lifetime()
        if self.proxy.egress.revocation_reason and not self.state.tainted:
            await self._taint(self.proxy.egress.revocation_reason)
        if not self.state.tainted and self.process.returncode is None:
            try:
                _, _, _, _, digest = await self._observe()
                if digest != self.state.page_digest:
                    self.state.bump()
                    self.state.page_digest = digest
            except BrowserPolicyError as exc:
                await self._taint(str(exc))
        return self.state.status()

    def _validate_public_arguments(self, tool_name: str, value: object) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise BrowserPolicyError("browser_arguments_invalid")
        arguments = dict(value)
        if len(json.dumps(arguments, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_ARGUMENT_BYTES:
            raise BrowserPolicyError("browser_arguments_too_large")
        if tool_name in {"navigate_page", "browser_navigate"}:
            if set(arguments) != {"url"}:
                raise BrowserPolicyError("browser_arguments_invalid")
        elif tool_name in {"take_snapshot", "browser_snapshot", "browser_session_status"}:
            if arguments:
                raise BrowserPolicyError("browser_arguments_invalid")
        elif tool_name in {"click", "browser_click"}:
            if set(arguments) != {"ref"}:
                raise BrowserPolicyError("browser_arguments_invalid")
            arguments["ref"] = validate_ref(arguments["ref"])
        elif tool_name in {"fill", "browser_fill_form"}:
            if set(arguments) != {"ref", "value"}:
                raise BrowserPolicyError("browser_arguments_invalid")
            arguments["ref"] = validate_ref(arguments["ref"])
            if not isinstance(arguments["value"], str):
                raise BrowserPolicyError("browser_value_invalid")
        elif tool_name in {"take_screenshot", "browser_take_screenshot"}:
            if not set(arguments) <= {"full_page"} or (
                "full_page" in arguments and not isinstance(arguments["full_page"], bool)
            ):
                raise BrowserPolicyError("browser_arguments_invalid")
        else:
            raise BrowserPolicyError("browser_tool_denied")
        return arguments

    async def call(self, tool_name: str, raw_arguments: object) -> dict[str, Any]:
        self._check_lifetime()
        if self.proxy.egress.revocation_reason and not self.state.tainted:
            await self._taint(self.proxy.egress.revocation_reason)
        if self.state.tainted or self.process.returncode is not None:
            raise BrowserPolicyError(self.state.taint_reason or "browser_session_tainted")
        if tool_name not in self.contract.tools:
            raise BrowserPolicyError("browser_tool_denied")
        arguments = self._validate_public_arguments(tool_name, raw_arguments)
        if tool_name == "browser_session_status":
            if self._purge_unregistered_artifacts():
                await self._taint("browser_unregistered_artifact")
                raise BrowserPolicyError("browser_unregistered_artifact")
            return {"result": {"content": [{"type": "text", "text": "浏览器会话状态已更新。"}], "structuredContent": await self.status()}}
        if self.state.action_count >= MAX_ACTIONS:
            raise BrowserPolicyError("browser_action_limit")
        if self._purge_unregistered_artifacts():
            await self._taint("browser_unregistered_artifact")
            raise BrowserPolicyError("browser_unregistered_artifact")
        self.state.action_count += 1
        self.state.last_activity = time.monotonic()

        artifact_path: Path | None = None
        forwarded_ref_roles: dict[str, str] = {}
        if tool_name in {"navigate_page", "browser_navigate"}:
            normalized = await self.proxy.authorize(str(arguments["url"]))
            arguments["url"] = normalized
            self.state.current_origin = validate_browser_url(normalized)[1]
        elif tool_name in {"click", "browser_click", "fill", "browser_fill_form"}:
            ref = str(arguments["ref"])
            await self._verify_bound_refs([ref])
            binding = self.state.refs[ref]
            if tool_name == "fill" and binding.role not in CHROME_FILL_ROLES:
                raise BrowserPolicyError("browser_fill_role_denied")
            if (
                tool_name == "browser_fill_form"
                and binding.role not in PLAYWRIGHT_FILL_ROLE_TYPES
            ):
                raise BrowserPolicyError("browser_fill_role_denied")
            forwarded_ref_roles[ref] = binding.role
            assert_non_sensitive_interaction(
                binding.context,
                arguments.get("value") if tool_name in {"fill", "browser_fill_form"} else None,
            )
        elif tool_name in {"take_screenshot", "browser_take_screenshot"}:
            artifact_id = f"browser_{uuid.uuid4().hex}"
            artifact_path = self.staging_dir / f".modelmirror-{artifact_id}.png"

        self.state.bump()
        upstream_name = self.contract.tools[tool_name].upstream_name
        assert upstream_name is not None
        upstream_arguments = to_upstream_arguments(
            self.adapter_id,
            tool_name,
            arguments,
            ref_roles=forwarded_ref_roles,
            artifact_path=artifact_path,
        )
        timeout = NAVIGATION_TIMEOUT_SECONDS if tool_name in {"navigate_page", "browser_navigate"} else TOOL_CALL_TIMEOUT_SECONDS
        effect = self.contract.tools[tool_name].effect
        try:
            response = await self.rpc.request(
                "tools/call",
                {"name": upstream_name, "arguments": upstream_arguments},
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            _emit_browser_runtime_event("rpc_timeout")
            if artifact_path is not None:
                artifact_path.unlink(missing_ok=True)
            if effect == "state-write":
                await self._taint("unknown_outcome")
                raise BrowserPolicyError("unknown_outcome")
            if effect == "artifact-create":
                await self._taint("browser_artifact_outcome_unknown")
                raise BrowserPolicyError("browser_artifact_outcome_unknown")
            raise BrowserPolicyError("browser_upstream_unavailable")
        except (ConnectionError, OSError, RuntimeError):
            _emit_browser_runtime_event("rpc_transport_failure")
            if artifact_path is not None:
                artifact_path.unlink(missing_ok=True)
            if effect == "state-write":
                await self._taint("unknown_outcome")
                raise BrowserPolicyError("unknown_outcome")
            if effect == "artifact-create":
                await self._taint("browser_artifact_outcome_unknown")
                raise BrowserPolicyError("browser_artifact_outcome_unknown")
            raise BrowserPolicyError("browser_upstream_unavailable")

        if self.proxy.last_violation:
            reason = self.proxy.last_violation
            _emit_browser_runtime_event(
                getattr(self.proxy, "last_violation_event", "")
                or _proxy_runtime_event(reason)
            )
            await self._taint(reason)
            if effect == "state-write":
                raise BrowserPolicyError("unknown_outcome")
            if effect == "artifact-create":
                raise BrowserPolicyError("browser_artifact_outcome_unknown")
            raise BrowserPolicyError(reason)
        if self.proxy.egress.revocation_reason:
            _emit_browser_runtime_event("egress_revoked")
            reason = self.proxy.egress.revocation_reason
            await self._taint(reason)
            if effect == "state-write":
                raise BrowserPolicyError("unknown_outcome")
            if effect == "artifact-create":
                raise BrowserPolicyError("browser_artifact_outcome_unknown")
            raise BrowserPolicyError(reason)
        if self._purge_unregistered_artifacts(allowed_pending=artifact_path):
            _emit_browser_runtime_event(self.unregistered_artifact_event)
            await self._taint("browser_unregistered_artifact")
            if effect == "state-write":
                raise BrowserPolicyError("unknown_outcome")
            if effect == "artifact-create":
                raise BrowserPolicyError("browser_artifact_outcome_unknown")
            raise BrowserPolicyError("browser_unregistered_artifact")
        if result_failed(response):
            _emit_browser_runtime_event(
                f"upstream_result_{_classify_upstream_failure(response)}"
            )
            if effect == "state-write":
                await self._taint("unknown_outcome")
                raise BrowserPolicyError("unknown_outcome")
            if effect == "artifact-create":
                if artifact_path is not None:
                    artifact_path.unlink(missing_ok=True)
                await self._taint("browser_artifact_outcome_unknown")
                raise BrowserPolicyError("browser_artifact_outcome_unknown")
            raise BrowserPolicyError("browser_upstream_call_failed")

        if tool_name in {"take_snapshot", "browser_snapshot"}:
            try:
                text, refs, observed_url, title = extract_snapshot(
                    self.adapter_id, response
                )
            except SnapshotStructureError as exc:
                await self._taint("browser_snapshot_ref_structure_invalid")
                raise BrowserPolicyError(
                    "browser_snapshot_ref_structure_invalid"
                ) from exc
            if len(text.encode("utf-8")) > MAX_OUTPUT_BYTES:
                raise BrowserPolicyError("browser_output_too_large")
            if observed_url and observed_url != "about:blank":
                try:
                    _, origin, _, _ = validate_browser_url(observed_url)
                except BrowserPolicyError as exc:
                    await self._taint(str(exc))
                    raise
                if self.state.current_origin and origin != self.state.current_origin:
                    await self._taint("browser_cross_origin_denied")
                    raise BrowserPolicyError("browser_cross_origin_denied")
            digest = page_digest(self.state.current_origin, observed_url, title, text)
            self.state.page_digest = digest
            elements: list[dict[str, str]] = []
            for ref, element in refs.items():
                item = _safe_element(ref, element, digest)
                if item is None:
                    continue
                binding = RefBinding(
                    self.state.generation,
                    self.state.page_revision,
                    digest,
                    element.context,
                    item["role"],
                    item["label"],
                )
                self.state.refs[ref] = binding
                elements.append(item)
                if len(elements) >= 100:
                    break
            result = response.get("result")
            assert isinstance(result, dict)
            result["structuredContent"] = {
                "generation": self.state.generation,
                "page_revision": self.state.page_revision,
                "page_digest": digest,
                "elements": elements,
            }
            return response

        if artifact_path is not None:
            try:
                registered_path, digest, artifact_size = _register_png_artifact(
                    artifact_path, self.registered_dir
                )
                self.registered_artifacts.add(registered_path.resolve())
            except (OSError, BrowserPolicyError):
                artifact_path.unlink(missing_ok=True)
                await self._taint("browser_artifact_invalid")
                raise BrowserPolicyError("browser_artifact_invalid")
            relative_path = f"{self.state.session_id}/registered/{registered_path.name}"
            return {
                "result": {
                    "content": [{"type": "text", "text": "PNG 截图已生成并登记为受控产物。"}],
                    "structuredContent": {
                        "artifact_id": registered_path.stem,
                        "relative_path": relative_path,
                        "sha256": digest,
                        "size": artifact_size,
                        "mime": "image/png",
                    },
                }
            }

        # A successful state-changing action must still leave exactly one page
        # at the authorized origin.  Failure is ambiguous, so the session is
        # tainted and never retried.
        if effect == "state-write":
            try:
                _, _, _, _, digest = await self._observe()
                self.state.page_digest = digest
            except BrowserPolicyError as exc:
                await self._taint(str(exc))
                raise BrowserPolicyError("unknown_outcome") from exc
        return response


async def _send_client_response(writer: asyncio.StreamWriter, payload: bytes) -> None:
    if len(payload) > MAX_MCP_MESSAGE_BYTES:
        payload = _rpc_error(None, -32013, "浏览器输出超过限制。", reason="browser_output_too_large")
    writer.write(payload)
    await asyncio.wait_for(writer.drain(), timeout=2)


async def _serve_mcp_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    gateway: BrowserGatewaySession,
) -> None:
    while True:
        age = (datetime.now(UTC) - gateway.state.started_at).total_seconds()
        remaining = min(
            max(SESSION_TTL_SECONDS - age, 0.0),
            max(IDLE_TTL_SECONDS - (time.monotonic() - gateway.state.last_activity), 0.0),
        )
        if remaining <= 0:
            break
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        if not raw:
            break
        if len(raw) > MAX_MCP_MESSAGE_BYTES:
            await _send_client_response(
                writer,
                _rpc_error(None, -32600, "MCP 请求超过限制。", reason="mcp_message_too_large"),
            )
            continue
        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(request, dict):
            continue
        request_id = request.get("id")
        method = request.get("method")
        if request_id is None:
            continue
        if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
            await _send_client_response(
                writer,
                _rpc_error(None, -32600, "MCP 请求 ID 无效。", reason="invalid_request_id"),
            )
            continue
        try:
            if method == "initialize":
                result = dict(gateway.initialize_result)
                response = _rpc_result(request_id, result)
            elif method == "ping":
                response = _rpc_result(request_id, {})
            elif method == "tools/list":
                response = _rpc_result(request_id, {"tools": public_tools(gateway.adapter_id)})
            elif method == "tools/call":
                params = request.get("params")
                if not isinstance(params, dict):
                    raise BrowserPolicyError("browser_arguments_invalid")
                name = params.get("name")
                if not isinstance(name, str):
                    raise BrowserPolicyError("browser_tool_denied")
                outcome = await gateway.call(name, params.get("arguments", {}))
                result = outcome.get("result")
                response = _rpc_result(request_id, result)
            else:
                response = _rpc_error(request_id, -32601, "MCP 方法未开放。", reason="method_denied")
        except BrowserPolicyError as exc:
            reason = str(exc)
            _emit_browser_runtime_event(
                BROWSER_POLICY_RUNTIME_EVENTS.get(reason, "proxy_policy_violation")
            )
            if reason == "unknown_outcome":
                response = _rpc_error(
                    request_id,
                    -32008,
                    "浏览器状态写入结果未知，会话已终止且不得自动重试。",
                    reason="unknown_outcome",
                )
            else:
                response = _rpc_error(
                    request_id,
                    -32011,
                    "浏览器操作被安全策略拒绝。",
                    reason=reason,
                )
        await _send_client_response(writer, response)


async def _browser_stdio(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    request: dict[str, Any],
    control_key: str,
    lifecycle: BrowserOneShotLifecycle,
) -> None:
    adapter_id = str(request.get("adapter_id") or "")
    if adapter_id not in ALLOWED_ADAPTERS:
        raise SandboxEngineError("Browser adapter denied.", code="mcp_adapter_denied")
    _validate_handshake(adapter_id, request.get("configuration"))
    request["configuration"] = None

    if not lifecycle.claimed:
        raise SandboxEngineError(
            "Browser session was not claimed.", code="browser_session_not_claimed"
        )
    if lifecycle.claimed:
        session_id = uuid.uuid4().hex
        profile_dir = (PROFILE_ROOT / session_id).resolve()
        artifact_dir = (ARTIFACT_ROOT / session_id).resolve()
        if profile_dir.parent != PROFILE_ROOT.resolve() or artifact_dir.parent != ARTIFACT_ROOT.resolve():
            raise SandboxEngineError("Browser session path denied.", code="unsafe_workspace")
        profile_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        artifact_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        runtime_tmp_dir = profile_dir / "tmp"
        staging_dir = artifact_dir / "staging"
        registered_dir = artifact_dir / "registered"
        runtime_tmp_dir.mkdir(mode=0o700)
        staging_dir.mkdir(mode=0o700)
        registered_dir.mkdir(mode=0o700)
        egress = BrowserEgressClient(control_key)
        proxy = LoopbackBrowserProxy(egress)
        process: asyncio.subprocess.Process | None = None
        stderr_classifier = SafeStderrClassifier()
        stderr_task: asyncio.Task[None] | None = None
        handshake_sent = False
        try:
            await egress.register()
            proxy_url = await proxy.start()
            env = {
                "PATH": "/opt/browser-upstream/node_modules/.bin:/usr/local/bin:/usr/bin:/bin",
                "PYTHONPATH": "/opt/modelmirror",
                "HOME": str(profile_dir),
                "TMPDIR": str(runtime_tmp_dir),
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "TZ": "UTC",
                "CI": "1",
                "CHROME_DEVTOOLS_MCP_NO_USAGE_STATISTICS": "1",
                "NODE_DISABLE_COMPILE_CACHE": "1",
                "DO_NOT_TRACK": "1",
                "NO_PROXY": "",
                "no_proxy": "",
                "HTTP_PROXY": proxy_url,
                "HTTPS_PROXY": proxy_url,
                "http_proxy": proxy_url,
                "https_proxy": proxy_url,
            }
            command = upstream_command(
                adapter_id,
                profile_dir=profile_dir,
                artifact_dir=staging_dir,
                proxy_url=proxy_url,
            )
            if any(
                forbidden in argument
                for argument in command
                for forbidden in ("--no-sandbox", "--disable-web-security", "--remote-debugging-address=0.0.0.0")
            ):
                raise SandboxEngineError("Unsafe Chromium argv denied.", code="browser_unsafe_argv")
            command = [
                sys.executable,
                "-m",
                "sandbox_sidecar.browser_mcp",
                "landlock",
                str(profile_dir),
                str(staging_dir),
                "--",
                *command,
            ]
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(profile_dir),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=MAX_MCP_MESSAGE_BYTES + 1,
            )
            env.clear()
            stderr_task = asyncio.create_task(
                _drain_stderr(process.stderr, stderr_classifier)
            )
            try:
                rpc = UpstreamRpc(process, staging_dir)
                state = BrowserSessionState(adapter_id, session_id)
                gateway = BrowserGatewaySession(
                    adapter_id,
                    state,
                    process,
                    rpc,
                    proxy,
                    artifact_dir,
                    staging_dir,
                    registered_dir,
                )
                await asyncio.wait_for(gateway.preflight(), timeout=45)
            except (asyncio.TimeoutError, RuntimeError) as exc:
                safe_code = _safe_preflight_error_code(exc)
                if safe_code is None:
                    raise
                if safe_code == "browser_upstream_representative_target_closed":
                    if stderr_classifier.primary() == "none":
                        try:
                            await asyncio.wait_for(
                                stderr_classifier.changed.wait(), timeout=0.25
                            )
                        except asyncio.TimeoutError:
                            pass
                    candidate = (
                        "browser_upstream_representative_target_closed_"
                        f"{stderr_classifier.primary()}"
                    )
                    if candidate in BROWSER_PREFLIGHT_ERROR_CODES:
                        safe_code = candidate
                raise SandboxEngineError(
                    "Browser upstream preflight failed.", code=safe_code
                ) from exc
            writer.write(
                json.dumps(
                    {
                        "ok": True,
                        "adapter_id": adapter_id,
                        "protocol": "modelmirror-browser-mcp-stdio-v1",
                        "contract_version": CONTRACT_VERSION,
                        "tool_schema_sha256": BROWSER_SCHEMA_SHA256[adapter_id],
                        "upstream": {
                            "package": gateway.contract.package_name,
                            "version": gateway.contract.package_version,
                            "verified": True,
                        },
                        "limits": BROWSER_LIMITS,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            await asyncio.wait_for(writer.drain(), timeout=2)
            handshake_sent = True
            await _serve_mcp_client(reader, writer, gateway)
        finally:
            if process is not None:
                await _terminate_process_group(process)
            if stderr_task is not None:
                stderr_task.cancel()
                await asyncio.gather(stderr_task, return_exceptions=True)
            await proxy.close()
            await egress.revoke()
            shutil.rmtree(profile_dir, ignore_errors=True)
            if "gateway" in locals():
                gateway._purge_unregistered_artifacts()
            shutil.rmtree(artifact_dir, ignore_errors=True)
            if handshake_sent:
                await _close_writer(writer)


async def handle_browser_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    control_key: str,
    lifecycle: BrowserOneShotLifecycle,
) -> None:
    response: dict[str, Any] | None = None
    session_claimed = False
    try:
        peer_uid = _trusted_peer_uid(writer)
        service_uid = os.getuid() if hasattr(os, "getuid") else -1
        if peer_uid not in {TRUSTED_CLIENT_UID, service_uid}:
            raise SandboxEngineError(
                "Browser sidecar peer denied.", code="browser_peer_denied"
            )
        request = await asyncio.wait_for(
            _read_json_line(reader), timeout=REQUEST_PREAMBLE_TIMEOUT_SECONDS
        )
        action = str(request.get("action") or "")
        if action == "mcp_stdio":
            if peer_uid != TRUSTED_CLIENT_UID:
                raise SandboxEngineError(
                    "Browser catalog peer denied.", code="browser_peer_denied"
                )
            lifecycle.claim()
            session_claimed = True
            await _browser_stdio(reader, writer, request, control_key, lifecycle)
            return
        if action != "health":
            raise SandboxEngineError("Browser sidecar action denied.", code="action_denied")
        response = {
            "ok": True,
            "protocol": "modelmirror-browser-mcp-stdio-v1",
            "mcp_browser_adapters": sorted(ALLOWED_ADAPTERS),
            "mcp_browser_max_sessions": MAX_SESSIONS,
            "mcp_message_limit_bytes": MAX_OUTPUT_BYTES,
            "network_mode": "egress-unix-socket-only",
        }
    except asyncio.TimeoutError:
        response = {
            "ok": False,
            "error": "browser_sidecar_rejected",
            "code": "browser_request_timeout",
        }
    except SandboxEngineError as exc:
        response = {"ok": False, "error": "browser_sidecar_rejected", "code": exc.code}
    except Exception:
        response = {
            "ok": False,
            "error": "browser_sidecar_internal_error",
            "code": "browser_sidecar_internal_error",
        }
    finally:
        if response is not None:
            try:
                writer.write(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
                await writer.drain()
            except (ConnectionError, OSError):
                pass
        await _close_writer(writer)
        if session_claimed:
            lifecycle.finish()


async def _wait_for_control_key() -> str:
    for _ in range(300):
        try:
            value = EGRESS_CONTROL_PATH.read_text(encoding="ascii").strip()
            if len(value) == 64:
                EGRESS_CONTROL_PATH.unlink(missing_ok=True)
                return value
        except OSError:
            pass
        await asyncio.sleep(0.1)
    raise RuntimeError("browser_egress_control_unavailable")


def _publish_control_key(control_key: str) -> None:
    """Atomically publish a new one-shot key only after egress is listening."""

    encoded = control_key.encode("ascii")
    temporary = EGRESS_CONTROL_PATH.with_name(
        f".{EGRESS_CONTROL_PATH.name}.{secrets.token_hex(8)}.tmp"
    )
    descriptor = -1
    directory_descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        written = os.write(descriptor, encoded)
        if written != len(encoded):
            raise RuntimeError("browser_egress_control_write_failed")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, EGRESS_CONTROL_PATH)
        directory_descriptor = os.open(
            EGRESS_CONTROL_PATH.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        os.fsync(directory_descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if directory_descriptor >= 0:
            os.close(directory_descriptor)
        temporary.unlink(missing_ok=True)


async def browser_main() -> None:
    readiness = RestartPolicyReadinessGate(time.monotonic())
    lifecycle = BrowserOneShotLifecycle()
    watch_ready = asyncio.Event()
    control_key: str | None = None
    watch_task: asyncio.Task[None] | None = None
    ready_task: asyncio.Task[bool] | None = None
    server: asyncio.AbstractServer | None = None
    serve_task: asyncio.Task[None] | None = None
    session_task: asyncio.Task[bool] | None = None
    try:
        _require_supervisor_pid1()
        _disable_process_dumping()
        _cleanup_stale_runtime_roots()
        control_key = await _wait_for_control_key()
        watch_task = asyncio.create_task(_watch_egress(control_key, watch_ready))
        ready_task = asyncio.create_task(watch_ready.wait())
        done, _ = await asyncio.wait(
            {watch_task, ready_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if watch_task in done:
            watch_task.result()
        ready_task.result()
        if not watch_ready.is_set():
            raise RuntimeError("browser_egress_watch_not_ready")

        # Do not expose the catalog socket until the authenticated egress watch
        # has acknowledged this exact key and Docker's restart policy is armed.
        await readiness.wait_before_arm()
        readiness.arm()
        SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        SOCKET_PATH.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(
            lambda reader, writer: handle_browser_client(
                reader, writer, control_key, lifecycle
            ),
            path=str(SOCKET_PATH),
            limit=MAX_MCP_MESSAGE_BYTES + 1,
        )
        os.chmod(SOCKET_PATH, 0o660)
        serve_task = asyncio.create_task(server.serve_forever())
        session_task = asyncio.create_task(lifecycle.finished.wait())
        done, pending = await asyncio.wait(
            {watch_task, serve_task, session_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            task.result()
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        try:
            if server is not None:
                server.close()
                try:
                    await asyncio.wait_for(server.wait_closed(), timeout=2)
                except asyncio.TimeoutError:
                    pass
            tasks = [
                task
                for task in (watch_task, ready_task, serve_task, session_task)
                if task is not None
            ]
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if control_key is not None:
                await _request_egress_shutdown(control_key)
            SOCKET_PATH.unlink(missing_ok=True)
        finally:
            # Early failures remain unavailable while waiting: the listener is
            # closed and no upstream browser has been admitted.
            await readiness.hold_early_exit()


async def egress_main() -> None:
    readiness = RestartPolicyReadinessGate(time.monotonic())
    service: BrowserEgressService | None = None
    server: asyncio.AbstractServer | None = None
    serve_task: asyncio.Task[None] | None = None
    shutdown_task: asyncio.Task[bool] | None = None
    bootstrap_task: asyncio.Task[None] | None = None
    try:
        _require_supervisor_pid1()
        EGRESS_SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)
        EGRESS_SOCKET_PATH.unlink(missing_ok=True)
        EGRESS_CONTROL_PATH.unlink(missing_ok=True)
        control_key = secrets.token_hex(32)
        service = BrowserEgressService(control_key)
        server = await asyncio.start_unix_server(
            service.handle,
            path=str(EGRESS_SOCKET_PATH),
            limit=MAX_PROXY_HEADER_BYTES + 1,
            start_serving=False,
        )
        os.chmod(EGRESS_SOCKET_PATH, 0o660)
        await readiness.wait_before_arm()
        await server.start_serving()
        readiness.arm()
        _publish_control_key(control_key)
        # Bootstrap starts only after the one-shot key is published; the 11s
        # Docker activation wait is not charged against the watcher timeout.
        serve_task = asyncio.create_task(server.serve_forever())
        shutdown_task = asyncio.create_task(service.shutdown_event.wait())
        bootstrap_task = asyncio.create_task(_enforce_egress_watch_bootstrap(service))
        done, pending = await asyncio.wait(
            {serve_task, shutdown_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            task.result()
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        try:
            if service is not None:
                await service.shutdown()
            if server is not None:
                server.close()
                try:
                    await asyncio.wait_for(server.wait_closed(), timeout=2)
                except asyncio.TimeoutError:
                    pass
            tasks = [
                task
                for task in (serve_task, shutdown_task, bootstrap_task)
                if task is not None
            ]
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            EGRESS_SOCKET_PATH.unlink(missing_ok=True)
            EGRESS_CONTROL_PATH.unlink(missing_ok=True)
        finally:
            # No key is published and no listener is serving while an early
            # failure waits out Docker's bounded restart activation window.
            await readiness.hold_early_exit()


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "egress":
        asyncio.run(egress_main())
        return
    if len(sys.argv) != 1:
        raise SystemExit("usage: browser_server.py [egress]")
    asyncio.run(browser_main())


if __name__ == "__main__":
    main()
