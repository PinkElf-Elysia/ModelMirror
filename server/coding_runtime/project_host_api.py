from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field

from .host_snapshot import MAX_HOST_ARCHIVE_BYTES
from .project_host import (
    PROJECT_HOST_PLATFORM,
    PROJECT_HOST_PROTOCOL,
    PROJECT_ID_PATTERN,
    ProjectHostError,
    ProjectHostStore,
)


PROJECT_HOST_REQUEST_TIMEOUT_SECONDS = 150.0
PROJECT_HOST_MAX_MESSAGE_BYTES = 256 * 1024
UPLOAD_ROOT_MARKER = ".modelmirror-coding-project-uploads"
SAFE_TRANSFER_ID = re.compile(r"^[a-f0-9]{32}$")
logger = logging.getLogger(__name__)


class PairingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="本地项目助手", min_length=1, max_length=80)


class ProjectRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)


@dataclass(slots=True)
class _Transfer:
    transfer_id: str
    request_id: str
    host_id: str
    project_id: str
    status: str
    created_at: float
    size: int = 0
    sha256: str | None = None


class ProjectHostRuntime:
    def __init__(self, store: ProjectHostStore, upload_root: Path) -> None:
        self.store = store
        self.upload_root = Path(upload_root)
        self._prepare_upload_root()
        self._connections: dict[str, WebSocket] = {}
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._pending_hosts: dict[str, str] = {}
        self._transfers: dict[str, _Transfer] = {}
        self._lock = asyncio.Lock()

    def capability(self, *, enabled: bool = True, reason: str | None = None) -> dict[str, Any]:
        status_payload = self.store.host_status()
        return {
            "enabled": enabled,
            "paired": status_payload["paired"],
            "available": status_payload["available"],
            "platform": PROJECT_HOST_PLATFORM,
            "selection": True,
            "remembers_projects": True,
            "direct_writeback": False,
            **({"reason": reason or "project_host_offline"} if not status_payload["available"] else {}),
        }

    def status(self) -> dict[str, Any]:
        return self.store.host_status()

    def list_projects(self) -> list[dict[str, Any]]:
        return self.store.list_projects()

    def public_project(self, project_id: str) -> dict[str, Any]:
        project = self.store.require_project(project_id)
        host = self.store.require_host(project.host_id)
        return project.to_public_dict(host_online=host.status == "online")

    def check_project(self, project_id: str, head: str, branch: str | None) -> dict[str, Any]:
        project = self.store.require_project(project_id)
        host = self.store.require_host(project.host_id)
        if host.status != "online":
            raise ProjectHostError("project_host_offline")
        if project.state != "available" or project.head != head or (branch is not None and project.branch != branch):
            raise ProjectHostError("project_changed")
        return project.to_public_dict(host_online=True)

    async def create_selection(self) -> dict[str, Any]:
        selection = self.store.create_selection()
        await self._send(
            selection.host_id,
            {"type": "select_project", "request_id": selection.request_id},
        )
        return selection.to_public_dict()

    async def reconnect(self) -> dict[str, Any]:
        status_payload = self.store.host_status()
        host_id = status_payload.get("host_id")
        if not isinstance(host_id, str) or not host_id:
            raise ProjectHostError("project_host_not_found")
        async with self._lock:
            connection = self._connections.get(host_id)
        if connection is None:
            raise ProjectHostError("project_host_offline")
        try:
            await connection.send_json({"type": "heartbeat"})
        except Exception as exc:
            raise ProjectHostError("project_host_offline") from exc
        for _attempt in range(10):
            await asyncio.sleep(0.1)
            refreshed = self.store.host_status()
            if refreshed.get("available") is True:
                return refreshed
        raise ProjectHostError("project_host_offline")

    def selection(self, request_id: str) -> dict[str, Any]:
        return self.store.require_selection(request_id).to_public_dict()

    async def rename_project(self, project_id: str, name: str) -> dict[str, Any]:
        project = self.store.require_project(project_id)
        await self._command(
            project.host_id,
            {"type": "rename_project", "project_id": project_id, "name": name},
        )
        updated = self.store.rename_project(project_id, name)
        return updated.to_public_dict(host_online=True)

    async def remove_project(self, project_id: str) -> dict[str, Any]:
        project = self.store.require_project(project_id)
        await self._command(
            project.host_id,
            {"type": "remove_project", "project_id": project_id},
        )
        self.store.remove_project(project_id)
        return {"removed": True}

    async def request_snapshot(
        self,
        project_id: str,
        *,
        expected_head: str | None = None,
        expected_branch: str | None = None,
    ) -> dict[str, Any]:
        project = self.store.require_project(project_id)
        host = self.store.require_host(project.host_id)
        if host.status != "online" or project.state != "available":
            raise ProjectHostError(project.reason or "project_host_offline")
        if expected_head is not None and project.head != expected_head:
            raise ProjectHostError("project_changed")
        if expected_branch is not None and project.branch != expected_branch:
            raise ProjectHostError("project_changed")
        request_id = f"phreq_{uuid.uuid4().hex}"
        transfer_id = secrets.token_hex(16)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        transfer = _Transfer(
            transfer_id=transfer_id,
            request_id=request_id,
            host_id=host.host_id,
            project_id=project_id,
            status="awaiting_upload",
            created_at=time.time(),
        )
        async with self._lock:
            self._pending[request_id] = future
            self._pending_hosts[request_id] = host.host_id
            self._transfers[transfer_id] = transfer
        completed = False
        try:
            await self._send(
                host.host_id,
                {
                    "type": "snapshot_project",
                    "request_id": request_id,
                    "project_id": project_id,
                    "transfer_id": transfer_id,
                },
            )
            result = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=PROJECT_HOST_REQUEST_TIMEOUT_SECONDS,
            )
            completed = True
            return result
        except TimeoutError as exc:
            raise ProjectHostError("project_host_snapshot_timeout") from exc
        finally:
            async with self._lock:
                self._pending.pop(request_id, None)
                self._pending_hosts.pop(request_id, None)
                if not future.done():
                    future.cancel()
            if not completed:
                self.finish_transfer(transfer_id)

    def finish_transfer(self, transfer_id: str) -> None:
        transfer = self._transfers.pop(transfer_id, None)
        if transfer is not None:
            (self.upload_root / f"{transfer_id}.uploading").unlink(missing_ok=True)
            (self.upload_root / f"{transfer_id}.tar.gz").unlink(missing_ok=True)

    async def receive_transfer(
        self,
        *,
        host_id: str,
        token: str,
        transfer_id: str,
        content_length: int,
        body: AsyncIterator[bytes],
    ) -> dict[str, Any]:
        self.store.authenticate(host_id, token)
        if SAFE_TRANSFER_ID.fullmatch(transfer_id) is None or not 0 < content_length <= MAX_HOST_ARCHIVE_BYTES:
            raise ProjectHostError("snapshot_upload_invalid")
        async with self._lock:
            transfer = self._transfers.get(transfer_id)
            if transfer is None or transfer.host_id != host_id or transfer.status != "awaiting_upload":
                raise ProjectHostError("snapshot_transfer_unavailable")
            transfer.status = "uploading"
        temporary = self.upload_root / f"{transfer_id}.uploading"
        final = self.upload_root / f"{transfer_id}.tar.gz"
        digest = hashlib.sha256()
        total = 0
        try:
            with temporary.open("xb") as stream:
                async for chunk in body:
                    if not isinstance(chunk, bytes):
                        raise ProjectHostError("snapshot_upload_invalid")
                    total += len(chunk)
                    if total > content_length or total > MAX_HOST_ARCHIVE_BYTES:
                        raise ProjectHostError("snapshot_archive_too_large")
                    digest.update(chunk)
                    stream.write(chunk)
            if total != content_length:
                raise ProjectHostError("snapshot_upload_truncated")
            os.replace(temporary, final)
            async with self._lock:
                transfer.status = "ready"
                transfer.size = total
                transfer.sha256 = digest.hexdigest()
            return {"received": True, "size": total}
        except BaseException:
            temporary.unlink(missing_ok=True)
            final.unlink(missing_ok=True)
            async with self._lock:
                transfer.status = "failed"
            raise

    async def connection_loop(self, websocket: WebSocket) -> None:
        await websocket.accept()
        connection_id = f"phconn_{uuid.uuid4().hex}"
        host_id = ""
        try:
            first = await asyncio.wait_for(self._receive(websocket), timeout=15)
            if not isinstance(first, dict) or first.get("protocol") != PROJECT_HOST_PROTOCOL:
                raise ProjectHostError("project_host_protocol_mismatch")
            message_type = first.get("type")
            if message_type == "pair":
                host, token = self.store.consume_pairing(
                    str(first.get("pairing_code") or ""),
                    device_id=str(first.get("device_id") or ""),
                    version=str(first.get("version") or ""),
                    platform=str(first.get("platform") or ""),
                )
                host_id = host.host_id
                self.store.connect(host_id, token, connection_id=connection_id, version=host.version)
                await websocket.send_json(
                    {
                        "type": "welcome",
                        "protocol": PROJECT_HOST_PROTOCOL,
                        "paired": True,
                        "host_id": host_id,
                        "host_token": token,
                        "heartbeat_seconds": 20,
                    }
                )
            elif message_type == "authenticate":
                host_id = str(first.get("host_id") or "")
                token = str(first.get("host_token") or "")
                host = self.store.connect(
                    host_id,
                    token,
                    connection_id=connection_id,
                    version=str(first.get("version") or ""),
                )
                if first.get("device_id") != host.device_id or first.get("platform") != host.platform:
                    raise ProjectHostError("project_host_identity_mismatch")
                await websocket.send_json(
                    {
                        "type": "welcome",
                        "protocol": PROJECT_HOST_PROTOCOL,
                        "paired": False,
                        "host_id": host_id,
                        "heartbeat_seconds": 20,
                    }
                )
            else:
                raise ProjectHostError("project_host_handshake_failed")
            async with self._lock:
                previous = self._connections.get(host_id)
                self._connections[host_id] = websocket
            if previous is not None and previous is not websocket:
                with contextlib.suppress(Exception):
                    await previous.close(code=4001)
            pending = self.store.next_selection(host_id)
            if pending is not None:
                await websocket.send_json({"type": "select_project", "request_id": pending.request_id})
            while True:
                message = await self._receive(websocket)
                await self._incoming(host_id, connection_id, message)
        except WebSocketDisconnect:
            logger.info("Coding project host connection closed by peer")
        except Exception as exc:
            logger.warning(
                "Coding project host connection failed: %s",
                getattr(exc, "code", type(exc).__name__),
            )
            with contextlib.suppress(Exception):
                await websocket.send_json({"type": "error", "code": getattr(exc, "code", "project_host_internal_error")})
                await websocket.close(code=4003)
        finally:
            if host_id:
                was_current = False
                with contextlib.suppress(ProjectHostError):
                    was_current = (
                        self.store.require_host(host_id).connection_id
                        == connection_id
                    )
                async with self._lock:
                    if self._connections.get(host_id) is websocket:
                        self._connections.pop(host_id, None)
                if was_current:
                    self.store.disconnect(host_id, connection_id)
                    async with self._lock:
                        disconnected = [
                            self._pending[request_id]
                            for request_id, pending_host_id in self._pending_hosts.items()
                            if pending_host_id == host_id
                            and request_id in self._pending
                        ]
                    for future in disconnected:
                        if not future.done():
                            future.set_exception(ProjectHostError("project_host_offline"))

    @staticmethod
    async def _receive(websocket: WebSocket) -> dict[str, Any]:
        raw = await websocket.receive_text()
        if len(raw.encode("utf-8")) > PROJECT_HOST_MAX_MESSAGE_BYTES:
            raise ProjectHostError("project_host_message_too_large")
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProjectHostError("project_host_message_invalid") from exc
        if not isinstance(message, dict):
            raise ProjectHostError("project_host_message_invalid")
        return message

    async def _incoming(self, host_id: str, connection_id: str, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "heartbeat":
            self.store.heartbeat(host_id, connection_id)
            return
        if message_type == "inventory":
            projects = message.get("projects")
            if not isinstance(projects, list) or len(projects) > 50:
                raise ProjectHostError("invalid_project_host_response")
            for project in projects:
                self.store.register_project(host_id, project)
            return
        request_id = str(message.get("request_id") or "")
        if message_type == "selection_result":
            self.store.complete_selection(host_id, request_id, project=message.get("project"))
            return
        if message_type == "snapshot_result":
            transfer_id = str(message.get("transfer_id") or "")
            project_payload = message.get("project")
            async with self._lock:
                transfer = self._transfers.get(transfer_id)
                future = self._pending.get(request_id)
            if (
                transfer is None
                or future is None
                or transfer.request_id != request_id
                or transfer.host_id != host_id
                or transfer.status != "ready"
                or transfer.sha256 is None
                or not isinstance(project_payload, dict)
                or project_payload.get("project_id") != transfer.project_id
            ):
                raise ProjectHostError("snapshot_transfer_mismatch")
            project = self.store.register_project(host_id, project_payload)
            if not future.done():
                future.set_result(
                    {
                        "upload_id": transfer_id,
                        "archive_sha256": transfer.sha256,
                        "project": {
                            "project_id": project.project_id,
                            "name": project.name,
                            "branch": project.branch,
                            "head": project.head,
                        },
                    }
                )
            return
        if message_type == "request_result":
            async with self._lock:
                future = self._pending.get(request_id)
            if future is None:
                raise ProjectHostError("project_host_request_not_found")
            if not future.done():
                future.set_result({"ok": True})
            return
        if message_type == "request_error":
            error = str(message.get("error") or "project_host_request_failed")
            with contextlib.suppress(ProjectHostError):
                selection = self.store.require_selection(request_id)
                if selection.host_id == host_id:
                    self.store.complete_selection(host_id, request_id, error=error)
                    return
            async with self._lock:
                future = self._pending.get(request_id)
            if future is not None and not future.done():
                future.set_exception(ProjectHostError(error))
                return
        raise ProjectHostError("project_host_message_unsupported")

    async def _command(self, host_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = f"phreq_{uuid.uuid4().hex}"
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        async with self._lock:
            self._pending[request_id] = future
            self._pending_hosts[request_id] = host_id
        try:
            await self._send(host_id, {**payload, "request_id": request_id})
            return await asyncio.wait_for(asyncio.shield(future), timeout=30)
        except TimeoutError as exc:
            raise ProjectHostError("project_host_request_timeout") from exc
        finally:
            async with self._lock:
                self._pending.pop(request_id, None)
                self._pending_hosts.pop(request_id, None)
                if not future.done():
                    future.cancel()

    async def _send(self, host_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            connection = self._connections.get(host_id)
        if connection is None:
            raise ProjectHostError("project_host_offline")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > PROJECT_HOST_MAX_MESSAGE_BYTES:
            raise ProjectHostError("project_host_message_too_large")
        await connection.send_text(encoded)

    def _prepare_upload_root(self) -> None:
        if not self.upload_root.is_absolute() or self.upload_root.is_symlink():
            raise ProjectHostError("snapshot_upload_root_unsafe")
        self.upload_root.mkdir(parents=True, exist_ok=True)
        marker = self.upload_root / UPLOAD_ROOT_MARKER
        if not marker.exists():
            if any(self.upload_root.iterdir()):
                raise ProjectHostError("snapshot_upload_root_unsafe")
            marker.write_text("v1\n", encoding="ascii")
        if marker.is_symlink() or marker.read_text(encoding="ascii") != "v1\n":
            raise ProjectHostError("snapshot_upload_root_unsafe")
        for child in self.upload_root.glob("*.uploading"):
            if SAFE_TRANSFER_ID.fullmatch(child.name.removesuffix(".uploading")) and child.is_file():
                child.unlink()


def create_project_host_runtime() -> ProjectHostRuntime:
    state_path = Path(
        os.getenv(
            "CODING_PROJECT_HOST_STATE_PATH",
            "/var/lib/modelmirror/coding-project-host/state.json",
        )
    )
    upload_root = Path(os.getenv("CODING_PROJECT_UPLOAD_ROOT", "/project-uploads"))
    if not state_path.is_absolute():
        raise ProjectHostError("project_host_state_not_configured")
    return ProjectHostRuntime(ProjectHostStore(state_path), upload_root)


project_host_router = APIRouter()


def _service() -> Any:
    from .api import get_coding_service

    return get_coding_service()


def _runtime() -> ProjectHostRuntime:
    runtime = _service().project_host
    if runtime is None:
        raise HTTPException(status_code=503, detail={"code": "project_host_unavailable"})
    return runtime


def _raise(exc: ProjectHostError) -> None:
    code = exc.code
    if code in {"project_not_found", "project_host_not_found", "project_host_request_not_found"}:
        http_status = status.HTTP_404_NOT_FOUND
    elif code in {"project_host_authentication_failed", "project_host_unavailable"}:
        http_status = status.HTTP_401_UNAUTHORIZED
    elif code.endswith("offline") or code.endswith("unavailable"):
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        http_status = status.HTTP_409_CONFLICT
    raise HTTPException(status_code=http_status, detail={"code": code}) from exc


@project_host_router.get("/project-host")
async def project_host_status(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return _runtime().status()


@project_host_router.post("/project-host/pairings")
async def create_pairing(payload: PairingRequest, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    pairing, code = _runtime().store.create_pairing(payload.name)
    return {
        "pairing_id": pairing.pairing_id,
        "pairing_code": code,
        "expires_at": pairing.expires_at,
        "single_use": True,
    }


@project_host_router.post("/project-host/reconnect")
async def reconnect_project_host(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _runtime().reconnect()
    except ProjectHostError as exc:
        _raise(exc)


@project_host_router.delete("/project-host/{host_id}")
async def revoke_project_host(host_id: str, response: Response) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    service = _service()
    if await service.any_host_project_locked():
        raise HTTPException(status_code=409, detail={"code": "project_active"})
    try:
        _runtime().store.revoke(host_id)
        return {"revoked": True}
    except ProjectHostError as exc:
        _raise(exc)


@project_host_router.post("/projects/selections", status_code=202)
async def select_project(response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await _runtime().create_selection()
    except ProjectHostError as exc:
        _raise(exc)


@project_host_router.get("/projects/selections/{request_id}")
async def project_selection_status(request_id: str, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    try:
        return _runtime().selection(request_id)
    except ProjectHostError as exc:
        _raise(exc)


@project_host_router.patch("/projects/{project_id}")
async def rename_project(project_id: str, payload: ProjectRenameRequest, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    if await _service().project_locked(project_id):
        raise HTTPException(status_code=409, detail={"code": "project_active"})
    try:
        return await _runtime().rename_project(project_id, payload.name)
    except ProjectHostError as exc:
        _raise(exc)


@project_host_router.delete("/projects/{project_id}")
async def remove_project(project_id: str, response: Response) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    if await _service().project_locked(project_id):
        raise HTTPException(status_code=409, detail={"code": "project_active"})
    try:
        return await _runtime().remove_project(project_id)
    except ProjectHostError as exc:
        _raise(exc)


@project_host_router.websocket("/project-host/connect")
async def connect_project_host(websocket: WebSocket) -> None:
    try:
        runtime = _runtime()
    except HTTPException:
        await websocket.close(code=1013)
        return
    await runtime.connection_loop(websocket)


@project_host_router.put("/project-host/transfers/{transfer_id}")
async def upload_project_snapshot(
    transfer_id: str,
    request: Request,
    response: Response,
    authorization: str | None = Header(default=None),
    host_id: str | None = Header(default=None, alias="X-ModelMirror-Project-Host-Id"),
    content_length: int | None = Header(default=None),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    token = authorization.removeprefix("Bearer ").strip() if authorization and authorization.startswith("Bearer ") else ""
    if not token or not host_id or content_length is None:
        raise HTTPException(status_code=401, detail={"code": "project_host_authentication_failed"})
    try:
        return await _runtime().receive_transfer(
            host_id=host_id,
            token=token,
            transfer_id=transfer_id,
            content_length=content_length,
            body=request.stream(),
        )
    except ProjectHostError as exc:
        _raise(exc)
