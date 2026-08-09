from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import ctypes
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit, urlunsplit

from server.coding_runtime.project_host import (
    DEVICE_ID_PATTERN,
    OBJECT_ID_PATTERN,
    PROJECT_HOST_PLATFORM,
    PROJECT_HOST_PROTOCOL,
    PROJECT_HOST_V2_CAPABILITIES,
    PROJECT_ID_PATTERN,
)
from server.coding_runtime.applier_client import _receipt_from_response, _receipt_to_payload
from server.coding_runtime.committer_client import (
    _commit_receipt_from_response,
    _commit_receipt_to_payload,
)
from server.coding_runtime.commit_models import validate_commit_branch
from server.coding_project_host.host_apply_engine import (
    HostApplyError,
    HostGitApplyEngine,
    _guard_directories,
)
from server.coding_project_host.host_commit_engine import (
    HostCommitError,
    HostGitCommitEngine,
    _safe_git_namespace,
)
from server.coding_project_host.host_file_transaction import (
    HostFileTransactionError,
    _windows_close_handle,
    _windows_handle_identity,
    _windows_open_existing,
    _windows_read_all,
    file_identity,
)
from server.coding_project_host.operation_log import (
    HostOperationJournal,
    HostOperationLogError,
)
from server.coding_runtime.host_snapshot import (
    HostSnapshotResult,
    create_host_snapshot_archive,
)
from server.coding_runtime.projects import (
    MAX_PROJECT_NAME_CHARS,
    build_safe_git_command,
    build_safe_git_environment,
    validate_git_tree,
)


HELPER_VERSION = "1.1.0"
STATE_MAGIC = b"MMCPH1\n"
MAX_CONTROL_MESSAGE_BYTES = 256 * 1024
MAX_OPERATION_PAYLOAD_BYTES = 1200 * 1024
MAX_GIT_GUARDED_METADATA_BYTES = 64 * 1024 * 1024
MAX_GIT_CONFIG_BYTES = 2 * 1024 * 1024
MAX_GIT_INDEX_BYTES = 32 * 1024 * 1024
MAX_GIT_PACKED_REFS_BYTES = 16 * 1024 * 1024
MAX_GIT_INFO_LEAF_BYTES = 4 * 1024 * 1024
MAX_REGISTERED_PROJECTS = 50
OPERATION_ACTIONS = frozenset({"apply", "revert", "commit", "undo", "reconcile"})
OPERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
PAYLOAD_ID_PATTERN = re.compile(r"^phop_[a-f0-9]{32}$")
_FILE_IDENTITY_PATTERN = re.compile(r"^[a-f0-9]+-[a-f0-9]+$")
_UNSAFE_LOCAL_CONFIG_PATTERN = re.compile(
    r"^(?:(?:include(?:if)?|filter|credential|diff|url)(?:\..+)?|"
    r"core\.(?:worktree|excludesfile)|"
    r"extensions\.(?:worktreeconfig|partialclone|refstorage)|"
    r"remote\..*\.(?:promisor|partialclonefilter))$",
    re.IGNORECASE,
)
DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
HEARTBEAT_INTERVAL_SECONDS = 20.0
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
DRIVE_REMOTE = 4
PROJECT_SELECTION_STATUS = {
    "git_repository_required": "请选择独立 Git 项目的根目录",
    "git_branch_required": "项目当前未处于普通分支，请先切换分支",
    "git_head_required": "项目还没有可读取的提交，请先创建初始提交",
    "git_repository_dirty": "项目中有尚未提交或未跟踪的文件，请先整理干净",
    "git_tree_unreadable": "无法读取项目当前版本的文件列表",
    "git_status_unreadable": "无法确认项目是否干净，请在 Git 中检查后重试",
}


class ProjectHostHelperError(RuntimeError):
    def __init__(self, code: str, message: str = "Local project helper failed") -> None:
        super().__init__(message)
        self.code = code


class DataProtector(Protocol):
    def protect(self, value: bytes) -> bytes: ...

    def unprotect(self, value: bytes) -> bytes: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


class WindowsDpapiProtector:
    """Current-user DPAPI wrapper without pywin32 or plaintext fallback."""

    _description = "ModelMirror Coding Project Host"

    def __init__(self) -> None:
        if os.name != "nt":
            raise ProjectHostHelperError("windows_required")
        self._crypt32 = ctypes.windll.crypt32
        self._kernel32 = ctypes.windll.kernel32

    @staticmethod
    def _blob(value: bytes) -> tuple[_DataBlob, Any]:
        buffer = ctypes.create_string_buffer(value)
        return _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer

    def protect(self, value: bytes) -> bytes:
        source, source_buffer = self._blob(value)
        destination = _DataBlob()
        ok = self._crypt32.CryptProtectData(
            ctypes.byref(source),
            ctypes.c_wchar_p(self._description),
            None,
            None,
            None,
            0x1,
            ctypes.byref(destination),
        )
        del source_buffer
        if not ok:
            raise ProjectHostHelperError("dpapi_protect_failed")
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            self._kernel32.LocalFree(destination.pbData)

    def unprotect(self, value: bytes) -> bytes:
        source, source_buffer = self._blob(value)
        destination = _DataBlob()
        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            None,
            None,
            None,
            0x1,
            ctypes.byref(destination),
        )
        del source_buffer
        if not ok:
            raise ProjectHostHelperError("dpapi_unprotect_failed")
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            self._kernel32.LocalFree(destination.pbData)


def default_state_path() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if not local_app_data:
        raise ProjectHostHelperError("local_app_data_unavailable")
    root = Path(local_app_data)
    if not root.is_absolute():
        raise ProjectHostHelperError("local_app_data_unavailable")
    return root / "ModelMirror" / "CodingProjectHost" / "state.bin"


class ProjectHostRegistry:
    def __init__(self, path: Path, protector: DataProtector) -> None:
        self.path = Path(path)
        self.protector = protector
        self._lock = threading.RLock()
        self._state = self._load()
        self.operations = HostOperationJournal(
            self.path.with_name("operations.bin"),
            protector,
        )

    @property
    def device_id(self) -> str:
        return self._state["device_id"]

    @property
    def device_secret(self) -> bytes:
        return base64.b64decode(self._state["device_secret"], validate=True)

    @property
    def credentials(self) -> tuple[str, str] | None:
        host_id = self._state.get("host_id")
        token = self._state.get("host_token")
        if isinstance(host_id, str) and isinstance(token, str) and host_id and token:
            return host_id, token
        return None

    def save_credentials(self, host_id: str, token: str) -> None:
        if not isinstance(host_id, str) or not host_id.startswith("phost_"):
            raise ProjectHostHelperError("project_host_credentials_invalid")
        if not isinstance(token, str) or len(token) < 32:
            raise ProjectHostHelperError("project_host_credentials_invalid")
        with self._lock:
            self._state["host_id"] = host_id
            self._state["host_token"] = token
            self._persist()

    def clear_credentials(self) -> None:
        with self._lock:
            self._state.pop("host_id", None)
            self._state.pop("host_token", None)
            self._persist()

    def projects(self) -> list[dict[str, str]]:
        with self._lock:
            return [dict(value) for value in self._state["projects"].values()]

    def remember_project(self, project: dict[str, str]) -> None:
        project_id = project.get("project_id", "")
        if (
            PROJECT_ID_PATTERN.fullmatch(project_id) is None
            or not isinstance(project.get("path"), str)
            or _FILE_IDENTITY_PATTERN.fullmatch(str(project.get("root_identity") or ""))
            is None
            or _FILE_IDENTITY_PATTERN.fullmatch(str(project.get("git_identity") or ""))
            is None
        ):
            raise ProjectHostHelperError("project_registry_invalid")
        with self._lock:
            if (
                project_id not in self._state["projects"]
                and len(self._state["projects"]) >= MAX_REGISTERED_PROJECTS
            ):
                # Match the Server catalog bound without first persisting a
                # 51st entry that would make every subsequent inventory frame
                # invalid. The user can remove the unavailable old selection
                # and then explicitly select the replacement repository.
                raise ProjectHostHelperError("project_limit_exceeded")
            for key, value in tuple(self._state["projects"].items()):
                legacy = (
                    _FILE_IDENTITY_PATTERN.fullmatch(
                        str(value.get("root_identity") or "")
                    )
                    is None
                    or _FILE_IDENTITY_PATTERN.fullmatch(
                        str(value.get("git_identity") or "")
                    )
                    is None
                )
                if legacy or (
                    key != project_id and value.get("path") == project.get("path")
                ):
                    tombstone = dict(value)
                    tombstone.pop("root_identity", None)
                    tombstone.pop("git_identity", None)
                    tombstone["state"] = "unavailable"
                    tombstone["reason"] = "project_reselection_required"
                    self._state["projects"][key] = tombstone
            self._state["projects"][project_id] = dict(project)
            self._persist()

    def update_project_head(
        self,
        project_id: str,
        *,
        branch: str,
        expected_heads: set[str],
        head: str,
    ) -> None:
        try:
            normalized_branch = validate_commit_branch(branch)
        except ValueError as exc:
            raise ProjectHostHelperError("project_registry_invalid") from exc
        if (
            PROJECT_ID_PATTERN.fullmatch(project_id) is None
            or OBJECT_ID_PATTERN.fullmatch(head) is None
            or not expected_heads
            or any(OBJECT_ID_PATTERN.fullmatch(item) is None for item in expected_heads)
        ):
            raise ProjectHostHelperError("project_registry_invalid")
        with self._lock:
            project = self._state["projects"].get(project_id)
            if (
                project is None
                or project.get("branch") != normalized_branch
                or project.get("head") not in expected_heads
            ):
                raise ProjectHostHelperError("project_changed")
            if project["head"] == head:
                return
            previous = dict(project)
            project["head"] = head
            try:
                self._persist()
            except (OSError, ProjectHostHelperError):
                self._state["projects"][project_id] = previous
                raise

    def rename_project(self, project_id: str, name: str) -> None:
        normalized = _normalize_name(name)
        with self._lock:
            project = self._state["projects"].get(project_id)
            if project is None:
                raise ProjectHostHelperError("project_not_found")
            project["name"] = normalized
            self._persist()

    def remove_project(self, project_id: str) -> None:
        with self._lock:
            if self._state["projects"].pop(project_id, None) is None:
                raise ProjectHostHelperError("project_not_found")
            self._persist()

    def project(self, project_id: str) -> dict[str, str]:
        with self._lock:
            value = self._state["projects"].get(project_id)
            if value is None:
                raise ProjectHostHelperError("project_not_found")
            if (
                _FILE_IDENTITY_PATTERN.fullmatch(
                    str(value.get("root_identity") or "")
                )
                is None
                or _FILE_IDENTITY_PATTERN.fullmatch(
                    str(value.get("git_identity") or "")
                )
                is None
            ):
                raise ProjectHostHelperError("project_reselection_required")
            return dict(value)

    def create_snapshot(
        self,
        project_id: str,
        destination: Path,
        *,
        expected_head: str | None = None,
        expected_branch: str | None = None,
        managed_operation_id: str | None = None,
    ) -> tuple[HostSnapshotResult, str]:
        registered = self.project(project_id)
        managed_recovery = bool(
            expected_head
            and expected_branch
            and managed_operation_id is not None
            and self.has_managed_operation(
                project_id,
                branch=expected_branch,
                expected_head=expected_head,
                operation_id=managed_operation_id,
            )
        )
        archive_identity: str | None = None
        try:
            # Keep the selected Git namespace and identity-bearing leaves
            # protected for the complete inspect -> archive -> reinspect use
            # interval.  In particular, cat-file must not outlive preflight.
            with _guard_git_read_session(
                registered["path"],
                enforce_windows=True,
                expected_root_identity=str(registered.get("root_identity") or ""),
                expected_git_identity=str(registered.get("git_identity") or ""),
            ) as (resolved, git_path, guarded):
                inspected = (
                    _inspect_git_project_for_recovery_guarded(
                        resolved,
                        git_path,
                        self.device_secret,
                        expected_project_id=project_id,
                        expected_branch=str(expected_branch),
                        expected_head=str(expected_head),
                    )
                    if managed_recovery
                    else _inspect_git_project_guarded(
                        resolved,
                        git_path,
                        self.device_secret,
                        guarded_metadata=guarded,
                    )
                )
                if inspected["project_id"] != project_id:
                    raise ProjectHostHelperError("project_identity_changed")
                if managed_operation_id is not None and not managed_recovery:
                    if (
                        expected_head is None
                        or expected_branch is None
                        or inspected.get("head") != expected_head
                        or inspected.get("branch") != expected_branch
                    ):
                        raise ProjectHostHelperError("project_changed")
                inspected["name"] = registered["name"]
                if not managed_recovery:
                    self.remember_project(inspected)
                result = create_host_snapshot_archive(
                    resolved,
                    destination,
                    project_id=project_id,
                    name=inspected["name"],
                    branch=str(expected_branch or inspected["branch"]),
                    head=str(expected_head or inspected["head"]),
                    identity_provider=file_identity,
                    identity_cleanup=_remove_regular_identity,
                )
                archive_identity = result.archive_identity
                if _FILE_IDENTITY_PATTERN.fullmatch(str(archive_identity or "")) is None:
                    raise ProjectHostHelperError("snapshot_failed")
                assert archive_identity is not None
                rechecked = (
                    _inspect_git_project_for_recovery_guarded(
                        resolved,
                        git_path,
                        self.device_secret,
                        expected_project_id=project_id,
                        expected_branch=str(expected_branch),
                        expected_head=str(expected_head),
                    )
                    if managed_recovery
                    else _inspect_git_project_guarded(
                        resolved,
                        git_path,
                        self.device_secret,
                        guarded_metadata=guarded,
                    )
                )
                if any(
                    rechecked[key] != inspected[key]
                    for key in (
                        ("project_id", "branch")
                        if managed_recovery
                        else ("project_id", "branch", "head")
                    )
                ):
                    raise ProjectHostHelperError("project_changed")
                return result, archive_identity
        except Exception as exc:
            if archive_identity is not None:
                try:
                    _remove_regular_identity(destination, archive_identity)
                except Exception as cleanup_exc:
                    raise ProjectHostHelperError("snapshot_cleanup_failed") from cleanup_exc
            raise ProjectHostHelperError(getattr(exc, "code", "snapshot_failed")) from exc

    def has_managed_operation(
        self,
        project_id: str,
        *,
        branch: str,
        expected_head: str,
        operation_id: str,
    ) -> bool:
        # The journal is loaded and authenticated by HostOperationJournal.  A
        # matching durable intent is the only reason a helper may bypass the
        # initial clean-worktree qualification after a managed writeback.
        try:
            record = self.operations.get(operation_id)
        except HostOperationLogError:
            return False
        return bool(
            record is not None
            and record.action in {"apply", "commit"}
            and record.project_id == project_id
            and record.branch == branch
            and record.expected_head == expected_head
            and record.state
            in {
                "applying",
                "applied",
                "committing",
                "committed",
            }
        )

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "version": 1,
                "device_id": f"pdev_{secrets.token_hex(16)}",
                "device_secret": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
                "projects": {},
            }
        try:
            encoded = self.path.read_bytes()
            if not encoded.startswith(STATE_MAGIC):
                raise ValueError("invalid magic")
            protected = base64.b64decode(encoded[len(STATE_MAGIC) :], validate=True)
            state = json.loads(self.protector.unprotect(protected).decode("utf-8", errors="strict"))
            if not _valid_registry_state(state):
                if not _valid_legacy_registry_without_identities(state):
                    raise ValueError("invalid state")
                # Preserve legacy entries only as unavailable inventory
                # tombstones. Operations reject their missing identities and
                # explicit selection replaces them with a new identity-bound
                # project id.
                for project in state["projects"].values():
                    project["state"] = "unavailable"
                    project["reason"] = "project_reselection_required"
            return state
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise ProjectHostHelperError("project_host_registry_corrupt") from exc

    def _persist(self) -> None:
        encoded = json.dumps(
            self._state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        protected = self.protector.protect(encoded)
        payload = STATE_MAGIC + base64.b64encode(protected)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(8)}.tmp")
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


def inspect_git_project(
    path: str | Path,
    device_secret: bytes,
    *,
    enforce_windows: bool = True,
    expected_root_identity: str | None = None,
    expected_git_identity: str | None = None,
) -> dict[str, str]:
    with _guard_git_read_session(
        path,
        enforce_windows=enforce_windows,
        expected_root_identity=expected_root_identity,
        expected_git_identity=expected_git_identity,
    ) as (resolved, git_path, guarded):
        return _inspect_git_project_guarded(
            resolved,
            git_path,
            device_secret,
            guarded_metadata=guarded,
        )


def _inspect_git_project_guarded(
    resolved: Path,
    git_path: Path,
    device_secret: bytes,
    *,
    guarded_metadata: frozenset[str],
) -> dict[str, str]:
    inside = _git_text(
        resolved,
        "rev-parse",
        "--is-inside-work-tree",
        error_code="git_repository_required",
    )
    if inside != "true":
        raise ProjectHostHelperError("git_repository_required")
    branch = _git_text(
        resolved,
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        error_code="git_branch_required",
    )
    if not _valid_branch(branch):
        raise ProjectHostHelperError("git_branch_required")
    head = _git_text(
        resolved,
        "rev-parse",
        "--verify",
        "HEAD^{commit}",
        error_code="git_head_required",
    ).lower()
    if OBJECT_ID_PATTERN.fullmatch(head) is None:
        raise ProjectHostHelperError("git_head_invalid")
    # An unborn repository reaches git_head_required above.  Once HEAD names a
    # real commit, the initial clean-worktree qualification must use the exact
    # index object that was guarded before the first Git command.
    if "index" not in guarded_metadata:
        raise ProjectHostHelperError("git_metadata_unsafe")
    tree = _run_git(resolved, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
    if tree.returncode != 0:
        raise ProjectHostHelperError("git_tree_unreadable")
    try:
        validate_git_tree(tree.stdout)
    except Exception as exc:
        code = getattr(exc, "code", "git_tree_invalid")
        raise ProjectHostHelperError(str(code)) from exc
    status = _run_git(resolved, "status", "--porcelain=v2", "--untracked-files=all")
    if status.returncode != 0:
        raise ProjectHostHelperError("git_status_unreadable")
    if status.stdout:
        raise ProjectHostHelperError("git_repository_dirty")

    root_identity = file_identity(resolved)
    git_identity = file_identity(git_path)
    canonical = b"\0".join(
        (
            os.path.normcase(str(resolved)).encode("utf-8", errors="strict"),
            root_identity.encode("ascii"),
            git_identity.encode("ascii"),
        )
    )
    digest = hmac.new(device_secret, canonical, hashlib.sha256).hexdigest()[:32]
    return {
        "project_id": f"hostgit_{digest}",
        "name": _normalize_name(resolved.name),
        "branch": branch,
        "head": head,
        "state": "available",
        "reason": "",
        "path": str(resolved),
        "root_identity": root_identity,
        "git_identity": git_identity,
    }


def inspect_git_project_for_recovery(
    path: str | Path,
    device_secret: bytes,
    *,
    expected_project_id: str,
    expected_branch: str,
    expected_head: str,
    expected_root_identity: str,
    expected_git_identity: str,
    enforce_windows: bool = True,
) -> dict[str, str]:
    """Inspect only immutable identity needed to rebuild a managed baseline.

    This deliberately does not require a clean worktree or current HEAD: an
    applied draft is dirty by design and a committed task has advanced HEAD.
    The caller must first prove a matching authenticated operation journal.
    """
    with _guard_git_read_session(
        path,
        enforce_windows=enforce_windows,
        expected_root_identity=expected_root_identity,
        expected_git_identity=expected_git_identity,
    ) as (resolved, _git_path, _guarded):
        return _inspect_git_project_for_recovery_guarded(
            resolved,
            _git_path,
            device_secret,
            expected_project_id=expected_project_id,
            expected_branch=expected_branch,
            expected_head=expected_head,
        )


def _inspect_git_project_for_recovery_guarded(
    resolved: Path,
    git_path: Path,
    device_secret: bytes,
    *,
    expected_project_id: str,
    expected_branch: str,
    expected_head: str,
) -> dict[str, str]:
    branch = _git_text(
        resolved,
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        error_code="git_branch_required",
    )
    if branch != expected_branch or not _valid_branch(branch):
        raise ProjectHostHelperError("project_changed")
    historical_head = _git_text(
        resolved,
        "rev-parse",
        "--verify",
        f"{expected_head}^{{commit}}",
        error_code="git_head_required",
    ).lower()
    if historical_head != expected_head or OBJECT_ID_PATTERN.fullmatch(historical_head) is None:
        raise ProjectHostHelperError("project_changed")
    tree = _run_git(resolved, "ls-tree", "-r", "-z", "--full-tree", expected_head)
    if tree.returncode != 0:
        raise ProjectHostHelperError("git_tree_unreadable")
    try:
        validate_git_tree(tree.stdout)
    except Exception as exc:
        raise ProjectHostHelperError(getattr(exc, "code", "git_tree_invalid")) from exc
    root_identity = file_identity(resolved)
    git_identity = file_identity(git_path)
    canonical = b"\0".join(
        (
            os.path.normcase(str(resolved)).encode("utf-8", errors="strict"),
            root_identity.encode("ascii"),
            git_identity.encode("ascii"),
        )
    )
    digest = hmac.new(device_secret, canonical, hashlib.sha256).hexdigest()[:32]
    project_id = f"hostgit_{digest}"
    if project_id != expected_project_id:
        raise ProjectHostHelperError("project_identity_changed")
    return {
        "project_id": project_id,
        "name": _normalize_name(resolved.name),
        "branch": branch,
        "head": expected_head,
        "state": "available",
        "reason": "",
        "path": str(resolved),
        "root_identity": root_identity,
        "git_identity": git_identity,
    }


def public_project(value: dict[str, str]) -> dict[str, Any]:
    return {
        "project_id": value["project_id"],
        "name": value["name"],
        "branch": value["branch"],
        "head": value["head"],
        "state": value.get("state") or "available",
        "reason": value.get("reason") or None,
    }


def validate_server_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ProjectHostHelperError("server_url_must_be_loopback")
    try:
        port = parsed.port or 80
    except ValueError as exc:
        raise ProjectHostHelperError("server_url_must_be_loopback") from exc
    if not 1 <= port <= 65535:
        raise ProjectHostHelperError("server_url_must_be_loopback")
    return urlunsplit(("http", f"127.0.0.1:{port}", "", "", ""))


class ProjectHostTransport:
    def __init__(
        self,
        registry: ProjectHostRegistry,
        server_url: str,
        pairing_code: str | None,
        *,
        select_folder: Callable[[], str | None],
        status_changed: Callable[[str], None] | None = None,
    ) -> None:
        self.registry = registry
        self.server_url = validate_server_url(server_url)
        self.pairing_code = str(pairing_code or "").strip()
        self.select_folder = select_folder
        self.status_changed = status_changed or (lambda _value: None)
        self.direct_writeback = False

    async def run_forever(self) -> None:
        from websockets.asyncio.client import connect

        ws_url = self.server_url.replace("http://", "ws://", 1) + "/api/coding/project-host/connect"
        delay = 1.0
        while True:
            try:
                self.status_changed("正在连接")
                async with connect(
                    ws_url,
                    open_timeout=10,
                    close_timeout=5,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=MAX_CONTROL_MESSAGE_BYTES,
                    compression=None,
                ) as websocket:
                    await self._authenticate(websocket)
                    self.status_changed("已连接")
                    delay = 1.0
                    heartbeat = asyncio.create_task(self._heartbeat_loop(websocket))
                    try:
                        await self._send_inventory(websocket)
                        async for raw in websocket:
                            await self._handle_message(websocket, raw)
                    finally:
                        heartbeat.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await heartbeat
            except asyncio.CancelledError:
                raise
            except ProjectHostHelperError as exc:
                if (
                    exc.code == "project_host_authentication_rejected"
                    and self.registry.credentials is not None
                ):
                    self.registry.clear_credentials()
                    self.pairing_code = ""
                    self.status_changed("连接凭据已失效，请生成新连接码后重新连接")
                    return
                if self.registry.credentials is None:
                    self.status_changed("连接失败，请检查配对码后重试")
                    return
                self.status_changed("连接已断开，正在重试")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 15.0)
            except Exception:
                if self.registry.credentials is None:
                    self.status_changed("连接失败，请检查配对码后重试")
                    return
                self.status_changed("连接已断开，正在重试")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 15.0)

    @staticmethod
    async def _heartbeat_loop(websocket: Any) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            await websocket.send('{"type":"heartbeat"}')

    async def _authenticate(self, websocket: Any) -> None:
        credentials = self.registry.credentials
        if credentials is not None:
            host_id, token = credentials
            first = {
                "type": "authenticate",
                "protocol": PROJECT_HOST_PROTOCOL,
                "host_id": host_id,
                "host_token": token,
                "device_id": self.registry.device_id,
                "version": HELPER_VERSION,
                "platform": PROJECT_HOST_PLATFORM,
                "capabilities": list(PROJECT_HOST_V2_CAPABILITIES),
            }
        else:
            if len(self.pairing_code) != 8 or not self.pairing_code.isdigit():
                raise ProjectHostHelperError("pairing_code_required")
            first = {
                "type": "pair",
                "protocol": PROJECT_HOST_PROTOCOL,
                "pairing_code": self.pairing_code,
                "device_id": self.registry.device_id,
                "version": HELPER_VERSION,
                "platform": PROJECT_HOST_PLATFORM,
                "capabilities": list(PROJECT_HOST_V2_CAPABILITIES),
            }
        await websocket.send(json.dumps(first, separators=(",", ":")))
        response = _parse_message(await asyncio.wait_for(websocket.recv(), timeout=15))
        if response.get("type") == "error":
            raise ProjectHostHelperError("project_host_authentication_rejected")
        capabilities = response.get("capabilities")
        if (
            response.get("type") != "welcome"
            or response.get("protocol") != PROJECT_HOST_PROTOCOL
            or not isinstance(capabilities, list)
            or "snapshot" not in capabilities
            or any(item not in PROJECT_HOST_V2_CAPABILITIES for item in capabilities)
            or not isinstance(response.get("direct_writeback"), bool)
        ):
            raise ProjectHostHelperError("project_host_handshake_failed")
        self.direct_writeback = bool(
            response["direct_writeback"]
            and {"writeback", "commit", "reconcile"}.issubset(capabilities)
        )
        if response.get("paired") is True:
            self.registry.save_credentials(
                str(response.get("host_id") or ""),
                str(response.get("host_token") or ""),
            )

    async def _send_inventory(self, websocket: Any) -> None:
        projects: list[dict[str, Any]] = []
        for registered in self.registry.projects():
            if (
                _FILE_IDENTITY_PATTERN.fullmatch(
                    str(registered.get("root_identity") or "")
                )
                is None
                or _FILE_IDENTITY_PATTERN.fullmatch(
                    str(registered.get("git_identity") or "")
                )
                is None
            ):
                projects.append(
                    {
                        "project_id": registered["project_id"],
                        "name": registered["name"],
                        "branch": registered["branch"],
                        "head": registered["head"],
                        "state": "unavailable",
                        "reason": "project_reselection_required",
                    }
                )
                continue
            try:
                inspected = inspect_git_project(
                    registered["path"],
                    self.registry.device_secret,
                    expected_root_identity=str(registered.get("root_identity") or ""),
                    expected_git_identity=str(registered.get("git_identity") or ""),
                )
                inspected["name"] = registered["name"]
                self.registry.remember_project(inspected)
                projects.append(public_project(inspected))
            except ProjectHostHelperError as exc:
                projects.append(
                    {
                        "project_id": registered["project_id"],
                        "name": registered["name"],
                        "branch": registered["branch"],
                        "head": registered["head"],
                        "state": "unavailable",
                        "reason": exc.code,
                    }
                )
        await websocket.send(json.dumps({"type": "inventory", "projects": projects}, separators=(",", ":")))

    async def _handle_message(self, websocket: Any, raw: Any) -> None:
        message = _parse_message(raw)
        message_type = message.get("type")
        if message_type == "heartbeat":
            return
        request_id = str(message.get("request_id") or "")
        if message_type == "select_project":
            selected = await asyncio.to_thread(self.select_folder)
            if not selected:
                await self._error(websocket, request_id, "project_selection_cancelled")
                return
            try:
                project = inspect_git_project(selected, self.registry.device_secret)
                self.registry.remember_project(project)
                # Publish the old-id tombstone before acknowledging the new
                # selection so no frame can leave the stale id looking writable.
                await self._send_inventory(websocket)
                await websocket.send(
                    json.dumps(
                        {"type": "selection_result", "request_id": request_id, "project": public_project(project)},
                        separators=(",", ":"),
                    )
                )
            except ProjectHostHelperError as exc:
                self.status_changed(
                    PROJECT_SELECTION_STATUS.get(
                        exc.code,
                        "无法读取这个 Git 项目，请检查项目状态后重试",
                    )
                )
                await self._error(websocket, request_id, exc.code)
        elif message_type == "rename_project":
            try:
                self.registry.rename_project(str(message.get("project_id") or ""), str(message.get("name") or ""))
                await websocket.send(json.dumps({"type": "request_result", "request_id": request_id, "ok": True}))
            except ProjectHostHelperError as exc:
                await self._error(websocket, request_id, exc.code)
        elif message_type == "remove_project":
            try:
                self.registry.remove_project(str(message.get("project_id") or ""))
                await websocket.send(json.dumps({"type": "request_result", "request_id": request_id, "ok": True}))
            except ProjectHostHelperError as exc:
                await self._error(websocket, request_id, exc.code)
        elif message_type == "snapshot_project":
            project_id = str(message.get("project_id") or "")
            transfer_id = str(message.get("transfer_id") or "")
            expected_head = message.get("expected_head")
            expected_branch = message.get("expected_branch")
            managed_operation_id = message.get("managed_operation_id")
            if PROJECT_ID_PATTERN.fullmatch(project_id) is None or not re.fullmatch(
                r"[a-f0-9]{32}", transfer_id
            ):
                await self._error(websocket, request_id, "snapshot_transfer_invalid")
                return
            transfer_root = self.registry.path.parent / "transfers"
            transfer_root.mkdir(parents=True, exist_ok=True)
            archive = transfer_root / f"{transfer_id}.tar.gz"
            try:
                result, archive_identity = await asyncio.to_thread(
                    self.registry.create_snapshot,
                    project_id,
                    archive,
                    expected_head=(
                        str(expected_head) if expected_head is not None else None
                    ),
                    expected_branch=(
                        str(expected_branch) if expected_branch is not None else None
                    ),
                    managed_operation_id=(
                        str(managed_operation_id)
                        if managed_operation_id is not None
                        else None
                    ),
                )
                await asyncio.to_thread(
                    self._upload_snapshot_exact,
                    transfer_id,
                    archive,
                    archive_identity,
                )
                await websocket.send(
                    json.dumps(
                        {
                            "type": "snapshot_result",
                            "request_id": request_id,
                            "transfer_id": transfer_id,
                            "project": {
                                "project_id": result.project_id,
                                "name": result.name,
                                "branch": result.branch,
                                "head": result.head,
                                "state": "available",
                                "reason": None,
                            },
                        },
                        separators=(",", ":"),
                    )
                )
            except ProjectHostHelperError as exc:
                await self._error(websocket, request_id, exc.code)
        elif message_type == "execute_operation":
            try:
                if not self.direct_writeback:
                    raise ProjectHostHelperError("project_host_writeback_disabled")
                result = await asyncio.to_thread(
                    self._handle_operation_message,
                    message,
                )
                await websocket.send(
                    json.dumps(
                        {
                            "type": "operation_result",
                            "request_id": request_id,
                            "project_id": str(message.get("project_id") or ""),
                            "operation_id": str(message.get("operation_id") or ""),
                            "action": str(message.get("action") or ""),
                            "result": result,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                )
            except (ProjectHostHelperError, HostApplyError, HostCommitError) as exc:
                await self._error(websocket, request_id, exc.code)
        else:
            raise ProjectHostHelperError("project_host_message_unsupported")

    def _handle_operation_message(self, message: dict[str, Any]) -> dict[str, Any]:
        expected_keys = {
            "type",
            "request_id",
            "project_id",
            "operation_id",
            "action",
            "payload_id",
            "payload_sha256",
            "payload_size",
            "payload_expires_at",
        }
        project_id = str(message.get("project_id") or "")
        operation_id = str(message.get("operation_id") or "")
        action = str(message.get("action") or "")
        payload_id = str(message.get("payload_id") or "")
        digest = str(message.get("payload_sha256") or "")
        size = message.get("payload_size")
        expires_at = message.get("payload_expires_at")
        if (
            set(message) != expected_keys
            or PROJECT_ID_PATTERN.fullmatch(project_id) is None
            or OPERATION_ID_PATTERN.fullmatch(operation_id) is None
            or action not in OPERATION_ACTIONS
            or PAYLOAD_ID_PATTERN.fullmatch(payload_id) is None
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or not 0 < size <= MAX_OPERATION_PAYLOAD_BYTES
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
            or not time.time() < float(expires_at) <= time.time() + 120.0
        ):
            raise ProjectHostHelperError("operation_request_invalid")
        body = self._download_operation_payload(
            payload_id=payload_id,
            project_id=project_id,
            operation_id=operation_id,
            action=action,
            expected_size=size,
            expected_sha256=digest,
        )
        try:
            envelope = json.loads(body.decode("utf-8", errors="strict"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ProjectHostHelperError("operation_payload_invalid") from exc
        if (
            not isinstance(envelope, dict)
            or set(envelope)
            != {
                "version",
                "host_id",
                "project_id",
                "operation_id",
                "action",
                "branch",
                "head",
                "payload",
            }
            or envelope.get("version") != 1
            or envelope.get("project_id") != project_id
            or envelope.get("operation_id") != operation_id
            or envelope.get("action") != action
            or not isinstance(envelope.get("payload"), dict)
        ):
            raise ProjectHostHelperError("operation_payload_mismatch")
        credentials = self.registry.credentials
        if credentials is None or envelope.get("host_id") != credentials[0]:
            raise ProjectHostHelperError("operation_payload_mismatch")
        registered = self.registry.project(project_id)
        try:
            branch = validate_commit_branch(str(envelope.get("branch") or ""))
        except ValueError as exc:
            raise ProjectHostHelperError("operation_payload_invalid") from exc
        baseline_head = str(envelope.get("head") or "").lower()
        if OBJECT_ID_PATTERN.fullmatch(baseline_head) is None:
            raise ProjectHostHelperError("operation_payload_invalid")
        result = self._execute_project_operation(
            registered,
            operation_id=operation_id,
            action=action,
            payload=envelope["payload"],
            branch=branch,
            baseline_head=baseline_head,
        )
        try:
            self._update_registry_after_operation(
                registered,
                branch=branch,
                baseline_head=baseline_head,
                result=result,
            )
        except (OSError, ProjectHostHelperError) as exc:
            raise ProjectHostHelperError("operation_result_unknown") from exc
        return result

    def _download_operation_payload(
        self,
        *,
        payload_id: str,
        project_id: str,
        operation_id: str,
        action: str,
        expected_size: int,
        expected_sha256: str,
    ) -> bytes:
        credentials = self.registry.credentials
        if credentials is None:
            raise ProjectHostHelperError("project_host_credentials_invalid")
        host_id, token = credentials
        parsed = urlsplit(self.server_url)
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            parsed.port or 80,
            timeout=30,
        )
        try:
            connection.request(
                "GET",
                f"/api/coding/project-host/operations/{payload_id}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-ModelMirror-Project-Host-Id": host_id,
                    "X-ModelMirror-Project-Id": project_id,
                    "X-ModelMirror-Operation-Id": operation_id,
                    "X-ModelMirror-Operation-Action": action,
                    "Accept": "application/json",
                },
            )
            response = connection.getresponse()
            content_length = response.getheader("Content-Length")
            content_type = response.getheader("Content-Type") or ""
            cache_control = response.getheader("Cache-Control") or ""
            if response.status != 200:
                response.read(64 * 1024)
                raise ProjectHostHelperError("operation_payload_unavailable")
            if (
                content_length != str(expected_size)
                or not content_type.lower().startswith("application/json")
                or "no-store" not in cache_control.lower()
            ):
                raise ProjectHostHelperError("operation_payload_invalid")
            body = response.read(expected_size + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise ProjectHostHelperError("operation_payload_unavailable") from exc
        finally:
            connection.close()
        if (
            len(body) != expected_size
            or hashlib.sha256(body).hexdigest() != expected_sha256
        ):
            raise ProjectHostHelperError("operation_payload_invalid")
        return body

    def _execute_project_operation(
        self,
        registered: dict[str, str],
        *,
        operation_id: str,
        action: str,
        payload: dict[str, Any],
        branch: str,
        baseline_head: str,
    ) -> dict[str, Any]:
        with _guard_registered_project_mutation(
            registered["path"],
            expected_root_identity=str(registered.get("root_identity") or ""),
            expected_git_identity=str(registered.get("git_identity") or ""),
        ) as resolved:
            bound = dict(registered)
            bound["path"] = str(resolved)
            return self._execute_project_operation_guarded(
                bound,
                operation_id=operation_id,
                action=action,
                payload=payload,
                branch=branch,
                baseline_head=baseline_head,
            )

    def _execute_project_operation_guarded(
        self,
        registered: dict[str, str],
        *,
        operation_id: str,
        action: str,
        payload: dict[str, Any],
        branch: str,
        baseline_head: str,
    ) -> dict[str, Any]:
        project_id = registered["project_id"]
        root = Path(registered["path"])
        apply_engine = HostGitApplyEngine(root, project_id, self.registry.operations)
        commit_engine = HostGitCommitEngine(root, project_id, self.registry.operations)
        try:
            if action == "apply":
                _require_payload_keys(
                    payload,
                    {
                        "kind",
                        "revision",
                        "expected_head",
                        "snapshot_fingerprint",
                        "patch",
                        "paths",
                    },
                    kind="apply",
                )
                _require_expected_head(payload, baseline_head)
                receipt = apply_engine.apply(
                    operation_id=operation_id,
                    revision=int(payload["revision"]),
                    branch=branch,
                    expected_head=baseline_head,
                    snapshot_fingerprint=str(payload["snapshot_fingerprint"]),
                    patch=str(payload["patch"]),
                    paths=_string_paths(payload["paths"]),
                )
                return {"state": "applied", "receipt": _receipt_to_payload(receipt)}
            if action == "revert":
                _require_payload_keys(
                    payload,
                    {"kind", "expected_head", "apply_receipt"},
                    kind="revert",
                )
                _require_expected_head(payload, baseline_head)
                receipt = _apply_receipt(payload.get("apply_receipt"))
                _require_bound_apply_record(
                    self.registry.operations,
                    project_id=project_id,
                    branch=branch,
                    expected_head=baseline_head,
                    receipt=receipt,
                )
                reverted = apply_engine.revert(
                    operation_id=operation_id,
                    apply_receipt=receipt,
                    branch=branch,
                    expected_head=baseline_head,
                )
                return {"state": "reverted", "receipt": _receipt_to_payload(reverted)}
            if action == "commit":
                _require_payload_keys(
                    payload,
                    {"kind", "expected_head", "apply_receipt", "message"},
                    kind="commit",
                )
                _require_expected_head(payload, baseline_head)
                apply_receipt = _apply_receipt(payload.get("apply_receipt"))
                _require_bound_apply_record(
                    self.registry.operations,
                    project_id=project_id,
                    branch=branch,
                    expected_head=baseline_head,
                    receipt=apply_receipt,
                )
                receipt = commit_engine.commit(
                    operation_id=operation_id,
                    apply_receipt=apply_receipt,
                    branch=branch,
                    expected_head=baseline_head,
                    message=str(payload["message"]),
                )
                return {"state": "committed", "receipt": _commit_receipt_to_payload(receipt)}
            if action == "undo":
                _require_payload_keys(
                    payload,
                    {
                        "kind",
                        "expected_head",
                        "apply_receipt",
                        "commit_receipt",
                    },
                    kind="undo",
                )
                _require_expected_head(payload, baseline_head)
                apply_receipt = _apply_receipt(payload.get("apply_receipt"))
                commit_receipt = _commit_receipt(payload.get("commit_receipt"))
                _require_bound_apply_record(
                    self.registry.operations,
                    project_id=project_id,
                    branch=branch,
                    expected_head=baseline_head,
                    receipt=apply_receipt,
                )
                _require_bound_commit_record(
                    self.registry.operations,
                    project_id=project_id,
                    branch=branch,
                    apply_receipt=apply_receipt,
                    receipt=commit_receipt,
                )
                receipt = commit_engine.undo(
                    operation_id=operation_id,
                    apply_receipt=apply_receipt,
                    commit_receipt=commit_receipt,
                    branch=branch,
                )
                return {"state": "undone", "receipt": _commit_receipt_to_payload(receipt)}
            if action == "reconcile":
                kind = payload.get("kind")
                if kind == "apply":
                    return self._reconcile_apply_operation(
                        apply_engine,
                        project_id=project_id,
                        operation_id=operation_id,
                        branch=branch,
                        baseline_head=baseline_head,
                        payload=payload,
                    )
                if kind == "commit":
                    return self._reconcile_commit_operation(
                        apply_engine,
                        commit_engine,
                        project_id=project_id,
                        operation_id=operation_id,
                        branch=branch,
                        baseline_head=baseline_head,
                        payload=payload,
                    )
        except (ValueError, TypeError, KeyError) as exc:
            raise ProjectHostHelperError("operation_payload_invalid") from exc
        raise ProjectHostHelperError("operation_request_invalid")

    def _update_registry_after_operation(
        self,
        registered: dict[str, str],
        *,
        branch: str,
        baseline_head: str,
        result: dict[str, Any],
    ) -> None:
        state = result.get("state")
        if state == "conflict":
            return
        head = baseline_head
        expected_heads = {baseline_head}
        if state in {"committed", "undone"}:
            receipt_payload = (
                result.get("commit_receipt")
                if "commit_receipt" in result
                else result.get("receipt")
            )
            receipt = _commit_receipt(receipt_payload)
            head = receipt.commit_sha if state == "committed" else receipt.parent_sha
            expected_heads.update({receipt.parent_sha, receipt.commit_sha})
        elif state not in {
            "applied",
            "reverted",
            "not_applied",
            "not_committed",
        }:
            raise ProjectHostHelperError("operation_result_unknown")
        self.registry.update_project_head(
            registered["project_id"],
            branch=branch,
            expected_heads=expected_heads | {head},
            head=head,
        )

    def _reconcile_apply_operation(
        self,
        engine: HostGitApplyEngine,
        *,
        project_id: str,
        operation_id: str,
        branch: str,
        baseline_head: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _require_payload_keys(
            payload,
            {
                "kind",
                "revision",
                "expected_head",
                "snapshot_fingerprint",
                "patch_sha256",
                "paths",
            },
            kind="apply",
        )
        _require_expected_head(payload, baseline_head)
        record = self.registry.operations.get(operation_id)
        if record is None:
            return {"state": "not_applied", "receipt": None}
        if (
            record.action != "apply"
            or record.project_id != project_id
            or record.branch != branch
            or record.expected_head != baseline_head
            or record.patch_sha256 != payload.get("patch_sha256")
            or record.revision != payload.get("revision")
        ):
            return {"state": "conflict", "receipt": None}
        receipt = (
            _apply_receipt(record.apply_receipt)
            if record.apply_receipt is not None
            else None
        )
        if receipt is not None and tuple(item.path for item in receipt.files) != _string_paths(
            payload.get("paths")
        ):
            return {"state": "conflict", "receipt": None}
        revert_id = _followup_operation_id("revert", operation_id)
        revert_record = self.registry.operations.get(revert_id)
        if revert_record is not None:
            if (
                receipt is None
                or revert_record.action != "revert"
                or revert_record.project_id != project_id
                or revert_record.branch != branch
                or revert_record.expected_head != baseline_head
                or revert_record.apply_receipt != _receipt_to_payload(receipt)
                or revert_record.state == "conflict"
            ):
                return {"state": "conflict", "receipt": None}
            if revert_record.state != "reverted":
                engine.revert(
                    operation_id=revert_id,
                    apply_receipt=receipt,
                    branch=branch,
                    expected_head=baseline_head,
                )
            return {"state": "not_applied", "receipt": None}
        state, restored = engine.reconcile_apply(
            operation_id=operation_id,
            snapshot_fingerprint=str(payload["snapshot_fingerprint"]),
        )
        return {
            "state": state,
            "receipt": _receipt_to_payload(restored) if restored is not None else None,
        }

    def _reconcile_commit_operation(
        self,
        apply_engine: HostGitApplyEngine,
        commit_engine: HostGitCommitEngine,
        *,
        project_id: str,
        operation_id: str,
        branch: str,
        baseline_head: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        _require_payload_keys(
            payload,
            {
                "kind",
                "apply_operation_id",
                "revision",
                "expected_head",
                "snapshot_fingerprint",
                "patch_sha256",
                "paths",
                "apply_receipt",
                "message",
            },
            kind="commit",
        )
        _require_expected_head(payload, baseline_head)
        apply_operation_id = str(payload.get("apply_operation_id") or "")
        if OPERATION_ID_PATTERN.fullmatch(apply_operation_id) is None:
            raise ProjectHostHelperError("operation_payload_invalid")
        apply_receipt = _apply_receipt(payload.get("apply_receipt"))
        apply_record = self.registry.operations.get(apply_operation_id)
        if (
            apply_record is None
            or apply_record.action != "apply"
            or apply_record.project_id != project_id
            or apply_record.branch != branch
            or apply_record.expected_head != baseline_head
            or apply_record.patch_sha256 != payload.get("patch_sha256")
            or apply_record.revision != payload.get("revision")
            or apply_record.apply_receipt != _receipt_to_payload(apply_receipt)
            or tuple(item.path for item in apply_receipt.files)
            != _string_paths(payload.get("paths"))
        ):
            raise ProjectHostHelperError("operation_conflict")
        commit_record = self.registry.operations.get(operation_id)
        if commit_record is None:
            apply_state, restored_apply = apply_engine.reconcile_apply(
                operation_id=apply_operation_id,
                snapshot_fingerprint=str(payload["snapshot_fingerprint"]),
            )
            if apply_state != "applied" or restored_apply != apply_receipt:
                raise ProjectHostHelperError("operation_conflict")
            return {
                "state": "not_committed",
                "apply_receipt": _receipt_to_payload(apply_receipt),
                "commit_receipt": None,
            }
        if (
            commit_record.action != "commit"
            or commit_record.project_id != project_id
            or commit_record.branch != branch
            or commit_record.expected_head != baseline_head
            or commit_record.commit_message != payload.get("message")
            or commit_record.revision != apply_receipt.revision
            or commit_record.patch_sha256 != apply_record.patch_sha256
            or commit_record.apply_receipt != _receipt_to_payload(apply_receipt)
        ):
            raise ProjectHostHelperError("operation_conflict")
        undo_id = _followup_operation_id("undo", operation_id)
        undo_record = self.registry.operations.get(undo_id)
        if undo_record is not None:
            stored_commit_receipt = (
                _commit_receipt(commit_record.commit_receipt)
                if commit_record.commit_receipt is not None
                else None
            )
            if (
                stored_commit_receipt is None
                or undo_record.action != "undo"
                or undo_record.project_id != project_id
                or undo_record.branch != branch
                or undo_record.expected_head != stored_commit_receipt.commit_sha
                or undo_record.revision != apply_receipt.revision
                or undo_record.patch_sha256 != apply_record.patch_sha256
                or undo_record.apply_receipt != _receipt_to_payload(apply_receipt)
                or undo_record.commit_receipt
                != _commit_receipt_to_payload(stored_commit_receipt)
            ):
                raise ProjectHostHelperError("operation_conflict")
        target_operation = undo_id if undo_record is not None else operation_id
        state, receipt = commit_engine.reconcile(target_operation)
        if state == "conflict":
            raise ProjectHostHelperError("operation_conflict")
        return {
            "state": state,
            "apply_receipt": _receipt_to_payload(apply_receipt),
            "commit_receipt": (
                _commit_receipt_to_payload(receipt) if receipt is not None else None
            ),
        }

    def _upload_snapshot(self, transfer_id: str, archive: Path) -> None:
        credentials = self.registry.credentials
        if credentials is None:
            raise ProjectHostHelperError("project_host_credentials_invalid")
        host_id, token = credentials
        parsed = urlsplit(self.server_url)
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            parsed.port or 80,
            timeout=150,
        )
        size = archive.stat().st_size
        try:
            connection.putrequest(
                "PUT",
                f"/api/coding/project-host/transfers/{transfer_id}",
            )
            connection.putheader("Authorization", f"Bearer {token}")
            connection.putheader("X-ModelMirror-Project-Host-Id", host_id)
            connection.putheader("Content-Type", "application/gzip")
            connection.putheader("Content-Length", str(size))
            connection.endheaders()
            with archive.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    connection.send(chunk)
            response = connection.getresponse()
            response.read(64 * 1024)
            if response.status != 200:
                raise ProjectHostHelperError("snapshot_upload_failed")
        except (OSError, http.client.HTTPException) as exc:
            raise ProjectHostHelperError("snapshot_upload_failed") from exc
        finally:
            connection.close()

    def _upload_snapshot_exact(
        self,
        transfer_id: str,
        archive: Path,
        archive_identity: str,
    ) -> None:
        try:
            with _guard_regular_identity(archive, archive_identity):
                self._upload_snapshot(transfer_id, archive)
        finally:
            _remove_regular_identity(archive, archive_identity)

    @staticmethod
    async def _error(websocket: Any, request_id: str, code: str) -> None:
        await websocket.send(
            json.dumps(
                {"type": "request_error", "request_id": request_id, "error": code},
                separators=(",", ":"),
            )
        )


class ProjectHostWindow:
    def __init__(self, registry: ProjectHostRegistry, server_url: str) -> None:
        import tkinter as tk
        from tkinter import filedialog, ttk

        self._tk = tk
        self._filedialog = filedialog
        self.root = tk.Tk()
        self.root.title("ModelMirror 本地项目助手")
        self.root.geometry("460x220")
        self.root.resizable(False, False)
        self.registry = registry
        self.server_url = server_url
        self.status = tk.StringVar(value="等待连接")
        self.code = tk.StringVar()
        ttk.Label(self.root, text="ModelMirror 本地项目助手", font=("Segoe UI", 15, "bold")).pack(pady=(20, 8))
        ttk.Label(self.root, text="只会读取你明确选择的 Git 项目，不会自动写入。", wraplength=410).pack()
        form = ttk.Frame(self.root)
        form.pack(pady=14)
        ttk.Label(form, text="配对码").grid(row=0, column=0, padx=6)
        entry = ttk.Entry(form, textvariable=self.code, width=18)
        entry.grid(row=0, column=1, padx=6)
        self.code_entry = entry
        ttk.Button(form, text="连接", command=self._start).grid(row=0, column=2, padx=6)
        if registry.credentials is not None:
            entry.configure(state="disabled")
        ttk.Label(self.root, textvariable=self.status).pack(pady=4)
        self._thread: threading.Thread | None = None

    def select_folder(self) -> str | None:
        selected: list[str | None] = [None]
        ready = threading.Event()

        def choose() -> None:
            value = self._filedialog.askdirectory(title="选择一个干净的 Git 项目")
            selected[0] = value or None
            ready.set()

        self.root.after(0, choose)
        ready.wait()
        return selected[0]

    def _start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        transport = ProjectHostTransport(
            self.registry,
            self.server_url,
            self.code.get(),
            select_folder=self.select_folder,
            status_changed=lambda value: self.root.after(0, self._set_status, value),
        )
        self._thread = threading.Thread(
            target=lambda: asyncio.run(transport.run_forever()),
            name="modelmirror-project-host",
            daemon=True,
        )
        self._thread.start()

    def _set_status(self, value: str) -> None:
        self.status.set(value)
        self.code_entry.configure(
            state="disabled" if self.registry.credentials is not None else "normal"
        )

    def run(self) -> None:
        if self.registry.credentials is not None:
            self._start()
        self.root.mainloop()


def _require_bound_apply_record(
    journal: HostOperationJournal,
    *,
    project_id: str,
    branch: str,
    expected_head: str,
    receipt: Any,
) -> None:
    try:
        record = journal.get(receipt.apply_id)
    except HostOperationLogError as exc:
        raise ProjectHostHelperError("operation_conflict") from exc
    if (
        record is None
        or record.action != "apply"
        or record.state != "applied"
        or record.project_id != project_id
        or record.branch != branch
        or record.expected_head != expected_head
        or record.revision != receipt.revision
        or record.apply_receipt != _receipt_to_payload(receipt)
    ):
        raise ProjectHostHelperError("operation_conflict")


def _require_bound_commit_record(
    journal: HostOperationJournal,
    *,
    project_id: str,
    branch: str,
    apply_receipt: Any,
    receipt: Any,
) -> None:
    try:
        record = journal.get(receipt.commit_id)
    except HostOperationLogError as exc:
        raise ProjectHostHelperError("operation_conflict") from exc
    if (
        record is None
        or record.action != "commit"
        or record.state != "committed"
        or record.project_id != project_id
        or record.branch != branch
        or record.expected_head != receipt.parent_sha
        or record.revision != apply_receipt.revision
        or record.apply_receipt != _receipt_to_payload(apply_receipt)
        or record.commit_receipt != _commit_receipt_to_payload(receipt)
    ):
        raise ProjectHostHelperError("operation_conflict")


def _require_payload_keys(
    payload: dict[str, Any],
    expected: set[str],
    *,
    kind: str,
) -> None:
    if set(payload) != expected or payload.get("kind") != kind:
        raise ProjectHostHelperError("operation_payload_invalid")


def _require_expected_head(payload: dict[str, Any], expected_head: str) -> None:
    if payload.get("expected_head") != expected_head:
        raise ProjectHostHelperError("project_changed")


def _string_paths(value: Any) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= 20
        or any(not isinstance(item, str) or not item for item in value)
        or tuple(value) != tuple(sorted(set(value)))
    ):
        raise ProjectHostHelperError("operation_payload_invalid")
    return tuple(value)


def _apply_receipt(value: Any):
    if not isinstance(value, dict):
        raise ProjectHostHelperError("operation_payload_invalid")
    try:
        return _receipt_from_response({"receipt": value})
    except Exception as exc:
        raise ProjectHostHelperError("operation_payload_invalid") from exc


def _commit_receipt(value: Any):
    if not isinstance(value, dict):
        raise ProjectHostHelperError("operation_payload_invalid")
    try:
        return _commit_receipt_from_response({"receipt": value})
    except Exception as exc:
        raise ProjectHostHelperError("operation_payload_invalid") from exc


def _followup_operation_id(action: str, operation_id: str) -> str:
    if action not in {"revert", "undo"} or OPERATION_ID_PATTERN.fullmatch(operation_id) is None:
        raise ProjectHostHelperError("operation_request_invalid")
    digest = hashlib.sha256(f"{action}\0{operation_id}".encode("utf-8")).hexdigest()
    return f"{action}_{digest[:40]}"


def _validate_windows_local_path(path: Path) -> None:
    if os.name != "nt":
        raise ProjectHostHelperError("windows_required")
    raw = str(path)
    if raw.startswith("\\\\") or not path.drive:
        raise ProjectHostHelperError("network_path_not_allowed")
    drive_root = f"{path.drive}\\"
    drive_type = ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive_root))
    if drive_type == DRIVE_REMOTE:
        raise ProjectHostHelperError("network_path_not_allowed")
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ProjectHostHelperError("project_path_invalid") from exc
    except OSError as exc:
        raise ProjectHostHelperError("project_path_invalid") from exc
    if getattr(metadata, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
        raise ProjectHostHelperError("project_reparse_point_not_allowed")


def _project_directory_chain(path: Path) -> tuple[Path, ...]:
    parts = path.parts
    if not parts or not path.anchor or any(part in {"", ".", ".."} for part in parts[1:]):
        raise ProjectHostHelperError("project_path_invalid")
    current = Path(parts[0])
    chain = [current]
    for part in parts[1:]:
        current = current / part
        chain.append(current)
    return tuple(chain)


@contextlib.contextmanager
def _guard_registered_project_mutation(
    path: str | Path,
    *,
    expected_root_identity: str,
    expected_git_identity: str,
):
    if (
        _FILE_IDENTITY_PATTERN.fullmatch(expected_root_identity) is None
        or _FILE_IDENTITY_PATTERN.fullmatch(expected_git_identity) is None
    ):
        raise ProjectHostHelperError("project_identity_changed")
    project_path = Path(path)
    if not project_path.is_absolute():
        raise ProjectHostHelperError("project_identity_changed")
    _validate_windows_local_path(project_path)
    if not project_path.is_dir():
        raise ProjectHostHelperError("project_identity_changed")
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(_guard_directories(_project_directory_chain(project_path)))
            root_identity = file_identity(project_path)
            resolved = project_path.resolve(strict=True)
            if (
                root_identity != expected_root_identity
                or file_identity(resolved) != expected_root_identity
            ):
                raise ProjectHostHelperError("project_identity_changed")
            git_path = resolved / ".git"
            if _is_link_or_reparse(git_path) or not git_path.is_dir():
                raise ProjectHostHelperError("project_identity_changed")
            stack.enter_context(_guard_directories((git_path,)))
            if file_identity(git_path) != expected_git_identity:
                raise ProjectHostHelperError("project_identity_changed")
            yield resolved
            if (
                file_identity(resolved) != expected_root_identity
                or file_identity(git_path) != expected_git_identity
            ):
                raise ProjectHostHelperError("project_identity_changed")
    except ProjectHostHelperError:
        raise
    except (HostApplyError, HostFileTransactionError, OSError) as exc:
        raise ProjectHostHelperError("project_identity_changed") from exc


@contextlib.contextmanager
def _guard_git_read_session(
    path: str | Path,
    *,
    enforce_windows: bool,
    expected_root_identity: str | None = None,
    expected_git_identity: str | None = None,
):
    """Hold trusted Git metadata across every command that consumes it.

    Static namespace scans reject every Git metadata reparse point, special
    file, and hard-linked leaf.  Held directory identities plus exact no-follow
    handles for identity-bearing leaves stay open across inspect, cat-file
    archive creation, and reinspect.  This does not claim to make arbitrary
    same-user object-leaf mutations atomic.
    """

    project_path = Path(path)
    if (expected_root_identity is None) != (expected_git_identity is None) or (
        expected_root_identity is not None
        and (
            _FILE_IDENTITY_PATTERN.fullmatch(expected_root_identity) is None
            or _FILE_IDENTITY_PATTERN.fullmatch(str(expected_git_identity)) is None
        )
    ):
        raise ProjectHostHelperError("project_identity_changed")
    if not project_path.is_absolute():
        raise ProjectHostHelperError("project_path_invalid")
    if enforce_windows:
        _validate_windows_local_path(project_path)
    elif _is_link_or_reparse(project_path):
        raise ProjectHostHelperError("project_reparse_point_not_allowed")
    if not project_path.is_dir():
        raise ProjectHostHelperError("project_path_invalid")
    directory_chain = _project_directory_chain(project_path)
    try:
        # The selected spelling is guarded before resolve(), so a junction or
        # rename cannot silently rebind selection to a different directory.
        with contextlib.ExitStack() as path_stack:
            path_stack.enter_context(_guard_directories(directory_chain))
            selected_identity = file_identity(project_path)
            if (
                expected_root_identity is not None
                and selected_identity != expected_root_identity
            ):
                raise ProjectHostHelperError("project_identity_changed")
            resolved = project_path.resolve(strict=True)
            if file_identity(resolved) != selected_identity:
                raise ProjectHostHelperError("project_reparse_point_not_allowed")
            git_path = resolved / ".git"
            if _is_link_or_reparse(git_path) or git_path.is_file():
                raise ProjectHostHelperError("git_worktree_not_allowed")
            if not git_path.is_dir():
                raise ProjectHostHelperError("git_repository_required")
            path_stack.enter_context(_guard_directories((git_path,)))
            git_identity = file_identity(git_path)
            if expected_git_identity is not None and git_identity != expected_git_identity:
                raise ProjectHostHelperError("project_identity_changed")
            directories = _safe_git_namespace(git_path)
            _assert_git_redirections_absent(git_path)
            with _guard_directories((resolved, *directories)):
                if file_identity(resolved) != selected_identity:
                    raise ProjectHostHelperError("project_reparse_point_not_allowed")
                if file_identity(git_path) != git_identity:
                    raise ProjectHostHelperError("git_metadata_unsafe")
                _safe_git_namespace(git_path)
                _assert_git_redirections_absent(git_path)
                guarded: dict[str, tuple[Path, str]] = {}
                guarded_bytes = 0
                with contextlib.ExitStack() as stack:

                    def guard_leaf(
                        name: str,
                        candidate: Path,
                        *,
                        required: bool,
                        maximum_bytes: int,
                    ) -> bytes | None:
                        nonlocal guarded_bytes
                        if not _path_entry_exists(candidate):
                            if required:
                                raise ProjectHostHelperError("git_metadata_unsafe")
                            return None
                        content, current_identity = stack.enter_context(
                            _guard_bounded_regular_object(candidate, maximum_bytes)
                        )
                        guarded_bytes += len(content)
                        if guarded_bytes > MAX_GIT_GUARDED_METADATA_BYTES:
                            raise ProjectHostHelperError("git_metadata_unsafe")
                        guarded[name] = (candidate, current_identity)
                        return content

                    guard_leaf(
                        "config",
                        git_path / "config",
                        required=True,
                        maximum_bytes=MAX_GIT_CONFIG_BYTES,
                    )
                    head = guard_leaf(
                        "HEAD",
                        git_path / "HEAD",
                        required=True,
                        maximum_bytes=4096,
                    )
                    guard_leaf(
                        "index",
                        git_path / "index",
                        required=False,
                        maximum_bytes=MAX_GIT_INDEX_BYTES,
                    )
                    guard_leaf(
                        "packed-refs",
                        git_path / "packed-refs",
                        required=False,
                        maximum_bytes=MAX_GIT_PACKED_REFS_BYTES,
                    )
                    guard_leaf(
                        "config.worktree",
                        git_path / "config.worktree",
                        required=False,
                        maximum_bytes=MAX_GIT_CONFIG_BYTES,
                    )
                    guard_leaf(
                        "shallow",
                        git_path / "shallow",
                        required=False,
                        maximum_bytes=MAX_GIT_PACKED_REFS_BYTES,
                    )

                    info_path = git_path / "info"
                    if _path_entry_exists(info_path):
                        for candidate in sorted(info_path.rglob("*")):
                            metadata = candidate.lstat()
                            if stat.S_ISDIR(metadata.st_mode):
                                continue
                            if not stat.S_ISREG(metadata.st_mode):
                                raise ProjectHostHelperError("git_metadata_unsafe")
                            guard_leaf(
                                f"info:{candidate.relative_to(info_path).as_posix()}",
                                candidate,
                                required=True,
                                maximum_bytes=MAX_GIT_INFO_LEAF_BYTES,
                            )

                    if head is not None:
                        try:
                            head_text = head.decode("utf-8", errors="strict").strip()
                        except UnicodeError as exc:
                            raise ProjectHostHelperError("git_encoding_not_supported") from exc
                        if head_text.startswith("ref: refs/heads/"):
                            relative = head_text.removeprefix("ref: ")
                            branch = relative.removeprefix("refs/heads/")
                            if (
                                not relative
                                or "\\" in relative
                                or any(part in {"", ".", ".."} for part in relative.split("/"))
                            ):
                                raise ProjectHostHelperError("git_metadata_unsafe")
                            try:
                                validate_commit_branch(branch)
                            except ValueError as exc:
                                raise ProjectHostHelperError("git_metadata_unsafe") from exc
                            guard_leaf(
                                "branch-ref",
                                git_path.joinpath(*relative.split("/")),
                                required=False,
                                maximum_bytes=4096,
                            )

                    _safe_git_namespace(git_path)
                    _assert_git_redirections_absent(git_path)
                    # This is deliberately the first Git command in the session.
                    _assert_no_unsafe_git_sources(resolved, git_path)
                    yield resolved, git_path, frozenset(guarded)
                    if (
                        file_identity(resolved) != selected_identity
                        or file_identity(git_path) != git_identity
                    ):
                        raise ProjectHostHelperError("git_metadata_unsafe")
                    _safe_git_namespace(git_path)
                    _assert_git_redirections_absent(git_path)
                    for candidate, expected_identity in guarded.values():
                        metadata = os.stat(candidate, follow_symlinks=False)
                        if (
                            not stat.S_ISREG(metadata.st_mode)
                            or metadata.st_nlink != 1
                            or file_identity(candidate) != expected_identity
                        ):
                            raise ProjectHostHelperError("git_metadata_unsafe")
    except ProjectHostHelperError:
        raise
    except HostApplyError as exc:
        if exc.code == "project_reparse_point_not_allowed":
            raise ProjectHostHelperError("project_reparse_point_not_allowed") from exc
        raise ProjectHostHelperError("git_metadata_unsafe") from exc
    except (HostCommitError, HostFileTransactionError, OSError) as exc:
        raise ProjectHostHelperError("git_metadata_unsafe") from exc


@contextlib.contextmanager
def _guard_bounded_regular_object(path: Path, maximum_bytes: int):
    if maximum_bytes <= 0:
        raise ProjectHostHelperError("git_metadata_unsafe")
    if os.name == "nt":
        handle = _windows_open_existing(
            path,
            access=0x80000000 | 0x00000080,  # GENERIC_READ | FILE_READ_ATTRIBUTES
            share=0x00000001,  # FILE_SHARE_READ; deny write/delete while in use
            allow_missing=False,
        )
        assert handle is not None
        try:
            identity = _windows_handle_identity(handle, require_directory=False)
            metadata = os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size > maximum_bytes
                or file_identity(path) != identity
            ):
                raise ProjectHostHelperError("git_metadata_unsafe")
            content = _windows_read_all(handle)
            if len(content) > maximum_bytes:
                raise ProjectHostHelperError("git_metadata_unsafe")
            yield content, identity
            current = os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or current.st_size > maximum_bytes
                or file_identity(path) != identity
                or _windows_handle_identity(handle, require_directory=False) != identity
                or _windows_read_all(handle) != content
            ):
                raise ProjectHostHelperError("git_metadata_unsafe")
        finally:
            _windows_close_handle(handle)
        return

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProjectHostHelperError("git_metadata_unsafe") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size > maximum_bytes
            or metadata.st_dev <= 0
            or metadata.st_ino <= 0
        ):
            raise ProjectHostHelperError("git_metadata_unsafe")
        identity = f"{metadata.st_dev:x}-{metadata.st_ino:x}"

        def read_current() -> bytes:
            os.lseek(descriptor, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            remaining = maximum_bytes + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            value = b"".join(chunks)
            if len(value) > maximum_bytes:
                raise ProjectHostHelperError("git_metadata_unsafe")
            return value

        content = read_current()
        if file_identity(path) != identity:
            raise ProjectHostHelperError("git_metadata_unsafe")
        yield content, identity
        current = os.fstat(descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or current.st_size > maximum_bytes
            or f"{current.st_dev:x}-{current.st_ino:x}" != identity
            or file_identity(path) != identity
            or read_current() != content
        ):
            raise ProjectHostHelperError("git_metadata_unsafe")
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def _guard_regular_identity(path: Path, expected_identity: str):
    if os.name == "nt":
        handle = _windows_open_existing(
            path,
            access=0x80000000 | 0x00000080,
            share=0x00000001,
            allow_missing=False,
        )
        assert handle is not None
        try:
            identity = _windows_handle_identity(handle, require_directory=False)
            metadata = os.stat(path, follow_symlinks=False)
            if (
                identity != expected_identity
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or file_identity(path) != identity
            ):
                raise ProjectHostHelperError("snapshot_upload_failed")
            yield
            current = os.stat(path, follow_symlinks=False)
            if (
                _windows_handle_identity(handle, require_directory=False) != identity
                or not stat.S_ISREG(current.st_mode)
                or current.st_nlink != 1
                or file_identity(path) != identity
            ):
                raise ProjectHostHelperError("snapshot_upload_failed")
        finally:
            _windows_close_handle(handle)
        return

    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise ProjectHostHelperError("snapshot_upload_failed") from exc
    try:
        metadata = os.fstat(descriptor)
        identity = f"{metadata.st_dev:x}-{metadata.st_ino:x}"
        if (
            identity != expected_identity
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or file_identity(path) != identity
        ):
            raise ProjectHostHelperError("snapshot_upload_failed")
        yield
        current = os.fstat(descriptor)
        if (
            f"{current.st_dev:x}-{current.st_ino:x}" != identity
            or not stat.S_ISREG(current.st_mode)
            or current.st_nlink != 1
            or file_identity(path) != identity
        ):
            raise ProjectHostHelperError("snapshot_upload_failed")
    except OSError as exc:
        raise ProjectHostHelperError("snapshot_upload_failed") from exc
    finally:
        os.close(descriptor)


def _remove_regular_identity(path: Path, expected_identity: str) -> None:
    """Delete only the exact archive object created by this snapshot call."""

    if os.name == "nt":
        class _FileDispositionInfo(ctypes.Structure):
            _fields_ = [("delete_file", wintypes.BOOLEAN)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
        try:
            handle = _windows_open_existing(
                path,
                access=0x00010000 | 0x00000080,  # DELETE | FILE_READ_ATTRIBUTES
                share=0x00000001,
                allow_missing=False,
            )
        except HostFileTransactionError as exc:
            raise ProjectHostHelperError("snapshot_cleanup_failed") from exc
        assert handle is not None
        try:
            identity = _windows_handle_identity(handle, require_directory=False)
            metadata = os.stat(path, follow_symlinks=False)
            if (
                identity != expected_identity
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or file_identity(path) != identity
            ):
                raise ProjectHostHelperError("snapshot_cleanup_failed")
            disposition = _FileDispositionInfo(True)
            if not kernel32.SetFileInformationByHandle(
                handle,
                4,  # FileDispositionInfo
                ctypes.byref(disposition),
                ctypes.sizeof(disposition),
            ):
                raise ProjectHostHelperError("snapshot_cleanup_failed")
        finally:
            _windows_close_handle(handle)
        if _path_entry_exists(path):
            raise ProjectHostHelperError("snapshot_cleanup_failed")
        return

    parent_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent = os.open(path.parent, parent_flags)
    except OSError as exc:
        raise ProjectHostHelperError("snapshot_cleanup_failed") from exc
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        metadata = os.fstat(descriptor)
        identity = f"{metadata.st_dev:x}-{metadata.st_ino:x}"
        named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (
            identity != expected_identity
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or not stat.S_ISREG(named.st_mode)
            or named.st_nlink != 1
            or (named.st_dev, named.st_ino) != (metadata.st_dev, metadata.st_ino)
        ):
            raise ProjectHostHelperError("snapshot_cleanup_failed")
        os.unlink(path.name, dir_fd=parent)
        os.fsync(parent)
    except OSError as exc:
        raise ProjectHostHelperError("snapshot_cleanup_failed") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ProjectHostHelperError("git_metadata_unsafe") from exc
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    )


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ProjectHostHelperError("git_metadata_unsafe") from exc


def _assert_git_redirections_absent(git_path: Path) -> None:
    if _path_entry_exists(git_path / "commondir"):
        raise ProjectHostHelperError("git_shared_directory_not_allowed")
    for name in ("alternates", "http-alternates"):
        if _path_entry_exists(git_path / "objects" / "info" / name):
            raise ProjectHostHelperError("git_alternates_not_allowed")
    for candidate in (git_path / "refs" / "replace", git_path / "info" / "grafts"):
        if _path_entry_exists(candidate):
            raise ProjectHostHelperError("git_metadata_unsafe")


def _run_git(path: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            build_safe_git_command(path, arguments),
            cwd=path,
            env=build_safe_git_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectHostHelperError("git_inspection_failed") from exc


def _assert_no_unsafe_git_sources(path: Path, git_path: Path) -> None:
    try:
        # Run outside every repository so even core.worktree/worktreeConfig in
        # the selected file cannot affect Git startup before we reject it.
        with tempfile.TemporaryDirectory(prefix="modelmirror-git-config-") as safe_cwd:
            configured = subprocess.run(
                build_safe_git_command(
                    path,
                    (
                        "config",
                        "--file",
                        str(git_path / "config"),
                        "--no-includes",
                        "--name-only",
                        "--list",
                    ),
                ),
                cwd=safe_cwd,
                env=build_safe_git_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=15,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
                ),
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectHostHelperError("git_inspection_failed") from exc
    if configured.returncode != 0:
        raise ProjectHostHelperError("git_inspection_failed")
    try:
        names = configured.stdout.decode("utf-8", errors="strict").splitlines()
    except UnicodeError as exc:
        raise ProjectHostHelperError("git_encoding_not_supported") from exc
    if any(_UNSAFE_LOCAL_CONFIG_PATTERN.fullmatch(name) for name in names):
        raise ProjectHostHelperError("git_config_unsafe")


def _git_text(
    path: Path,
    *arguments: str,
    error_code: str = "git_inspection_failed",
) -> str:
    result = _run_git(path, *arguments)
    if result.returncode != 0:
        raise ProjectHostHelperError(error_code)
    try:
        return result.stdout.decode("utf-8", errors="strict").strip()
    except UnicodeError as exc:
        raise ProjectHostHelperError("git_encoding_not_supported") from exc


def _normalize_name(value: Any) -> str:
    if not isinstance(value, str) or value != unicodedata.normalize("NFC", value):
        raise ProjectHostHelperError("project_name_invalid")
    if not value or value != value.strip() or len(value) > MAX_PROJECT_NAME_CHARS:
        raise ProjectHostHelperError("project_name_invalid")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise ProjectHostHelperError("project_name_invalid")
    return value


def _valid_branch(value: str) -> bool:
    return bool(value and len(value) <= 200 and value == value.strip())


def _valid_registry_state(value: Any) -> bool:
    if not _valid_registry_base(value):
        return False
    for project_id, project in value["projects"].items():
        if PROJECT_ID_PATTERN.fullmatch(str(project_id)) is None or not isinstance(project, dict):
            return False
        authorized = (
            _FILE_IDENTITY_PATTERN.fullmatch(str(project.get("root_identity") or ""))
            is not None
            and _FILE_IDENTITY_PATTERN.fullmatch(str(project.get("git_identity") or ""))
            is not None
        )
        tombstone = (
            "root_identity" not in project
            and "git_identity" not in project
            and project.get("state") == "unavailable"
            and project.get("reason") == "project_reselection_required"
        )
        if (
            project.get("project_id") != project_id
            or not isinstance(project.get("path"), str)
            or not (authorized or tombstone)
        ):
            return False
    return True


def _valid_legacy_registry_without_identities(value: Any) -> bool:
    if not _valid_registry_base(value):
        return False
    for project_id, project in value["projects"].items():
        if (
            PROJECT_ID_PATTERN.fullmatch(str(project_id)) is None
            or not isinstance(project, dict)
            or project.get("project_id") != project_id
            or not isinstance(project.get("path"), str)
            or "root_identity" in project
            or "git_identity" in project
        ):
            return False
    return True


def _valid_registry_base(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("version") != 1:
        return False
    if DEVICE_ID_PATTERN.fullmatch(str(value.get("device_id") or "")) is None:
        return False
    try:
        secret = base64.b64decode(value.get("device_secret", ""), validate=True)
    except (TypeError, ValueError):
        return False
    return len(secret) == 32 and isinstance(value.get("projects"), dict)


def _parse_message(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_CONTROL_MESSAGE_BYTES:
        raise ProjectHostHelperError("project_host_message_invalid")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProjectHostHelperError("project_host_message_invalid") from exc
    if not isinstance(value, dict):
        raise ProjectHostHelperError("project_host_message_invalid")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ModelMirror Windows local project helper")
    parser.add_argument("--server", default=DEFAULT_SERVER_URL)
    parser.add_argument("--state", type=Path, default=None)
    arguments = parser.parse_args(argv)
    try:
        registry = ProjectHostRegistry(
            arguments.state or default_state_path(),
            WindowsDpapiProtector(),
        )
        ProjectHostWindow(registry, validate_server_url(arguments.server)).run()
        return 0
    except ProjectHostHelperError:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
