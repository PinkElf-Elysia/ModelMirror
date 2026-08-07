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
import subprocess
import sys
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
    PROJECT_ID_PATTERN,
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


HELPER_VERSION = "1.0.1"
STATE_MAGIC = b"MMCPH1\n"
MAX_CONTROL_MESSAGE_BYTES = 256 * 1024
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
        if PROJECT_ID_PATTERN.fullmatch(project_id) is None or "path" not in project:
            raise ProjectHostHelperError("project_registry_invalid")
        with self._lock:
            self._state["projects"][project_id] = dict(project)
            self._persist()

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
            return dict(value)

    def create_snapshot(self, project_id: str, destination: Path) -> HostSnapshotResult:
        registered = self.project(project_id)
        inspected = inspect_git_project(
            registered["path"],
            self.device_secret,
        )
        if inspected["project_id"] != project_id:
            raise ProjectHostHelperError("project_identity_changed")
        inspected["name"] = registered["name"]
        self.remember_project(inspected)
        try:
            result = create_host_snapshot_archive(
                Path(inspected["path"]),
                destination,
                project_id=project_id,
                name=inspected["name"],
                branch=inspected["branch"],
                head=inspected["head"],
            )
            rechecked = inspect_git_project(
                registered["path"],
                self.device_secret,
            )
            if any(
                rechecked[key] != inspected[key]
                for key in ("project_id", "branch", "head")
            ):
                destination.unlink(missing_ok=True)
                raise ProjectHostHelperError("project_changed")
            return result
        except Exception as exc:
            raise ProjectHostHelperError(getattr(exc, "code", "snapshot_failed")) from exc

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
                raise ValueError("invalid state")
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
) -> dict[str, str]:
    project_path = Path(path)
    if not project_path.is_absolute() or not project_path.is_dir():
        raise ProjectHostHelperError("project_path_invalid")
    if enforce_windows:
        _validate_windows_local_path(project_path)
    elif project_path.is_symlink():
        raise ProjectHostHelperError("project_reparse_point_not_allowed")
    resolved = project_path.resolve(strict=True)
    git_path = resolved / ".git"
    if git_path.is_symlink() or git_path.is_file():
        raise ProjectHostHelperError("git_worktree_not_allowed")
    if not git_path.is_dir():
        raise ProjectHostHelperError("git_repository_required")
    if (git_path / "commondir").exists():
        raise ProjectHostHelperError("git_shared_directory_not_allowed")
    alternates = git_path / "objects" / "info" / "alternates"
    if alternates.is_symlink() or (alternates.is_file() and alternates.stat().st_size > 0):
        raise ProjectHostHelperError("git_alternates_not_allowed")

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

    canonical = os.path.normcase(str(resolved)).encode("utf-8", errors="strict")
    digest = hmac.new(device_secret, canonical, hashlib.sha256).hexdigest()[:32]
    return {
        "project_id": f"hostgit_{digest}",
        "name": _normalize_name(resolved.name),
        "branch": branch,
        "head": head,
        "state": "available",
        "reason": "",
        "path": str(resolved),
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
            }
        await websocket.send(json.dumps(first, separators=(",", ":")))
        response = _parse_message(await asyncio.wait_for(websocket.recv(), timeout=15))
        if response.get("type") == "error":
            raise ProjectHostHelperError("project_host_authentication_rejected")
        if response.get("type") != "welcome" or response.get("protocol") != PROJECT_HOST_PROTOCOL:
            raise ProjectHostHelperError("project_host_handshake_failed")
        if response.get("paired") is True:
            self.registry.save_credentials(
                str(response.get("host_id") or ""),
                str(response.get("host_token") or ""),
            )

    async def _send_inventory(self, websocket: Any) -> None:
        projects: list[dict[str, Any]] = []
        for registered in self.registry.projects():
            try:
                inspected = inspect_git_project(
                    registered["path"],
                    self.registry.device_secret,
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
            if PROJECT_ID_PATTERN.fullmatch(project_id) is None or not re.fullmatch(
                r"[a-f0-9]{32}", transfer_id
            ):
                await self._error(websocket, request_id, "snapshot_transfer_invalid")
                return
            transfer_root = self.registry.path.parent / "transfers"
            transfer_root.mkdir(parents=True, exist_ok=True)
            archive = transfer_root / f"{transfer_id}.tar.gz"
            try:
                result = await asyncio.to_thread(
                    self.registry.create_snapshot,
                    project_id,
                    archive,
                )
                await asyncio.to_thread(self._upload_snapshot, transfer_id, archive)
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
            finally:
                archive.unlink(missing_ok=True)
        else:
            raise ProjectHostHelperError("project_host_message_unsupported")

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
    if getattr(path.lstat(), "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT:
        raise ProjectHostHelperError("project_reparse_point_not_allowed")


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
    if not isinstance(value, dict) or value.get("version") != 1:
        return False
    if DEVICE_ID_PATTERN.fullmatch(str(value.get("device_id") or "")) is None:
        return False
    try:
        secret = base64.b64decode(value.get("device_secret", ""), validate=True)
    except (TypeError, ValueError):
        return False
    if len(secret) != 32 or not isinstance(value.get("projects"), dict):
        return False
    for project_id, project in value["projects"].items():
        if PROJECT_ID_PATTERN.fullmatch(str(project_id)) is None or not isinstance(project, dict):
            return False
        if project.get("project_id") != project_id or not isinstance(project.get("path"), str):
            return False
    return True


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
