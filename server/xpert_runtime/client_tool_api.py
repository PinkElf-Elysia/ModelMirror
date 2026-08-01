from __future__ import annotations

import io
import json
import os
import uuid
import zipfile
from html import escape
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    File,
    Header,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from .client_tool_coordinator import (
    ClientToolConnectionManager,
    ClientToolCoordinator,
)
from .client_tool_store import (
    ClientToolAuthenticationError,
    ClientToolConflictError,
    ClientToolNotFoundError,
    ClientToolStore,
)
from .client_toolset import CLIENT_TOOLS, client_tool_schema_hash
from .office_toolset import OFFICE_TOOLS, OFFICE_TOOL_REQUIREMENTS
from .sandbox_store import SandboxWorkspaceStore


router = APIRouter(prefix="/api/runtime", tags=["runtime-client-tools"])
_store: ClientToolStore | None = None
_connections: ClientToolConnectionManager | None = None
_coordinator: ClientToolCoordinator | None = None
_sandbox_store: SandboxWorkspaceStore | None = None


class PairingCreateRequest(BaseModel):
    name: str = Field(default="Chrome Host", max_length=100)
    host_type: str = Field(default="chrome", pattern="^(chrome|office)$")


def configure_runtime_client_tools(
    store: ClientToolStore,
    connections: ClientToolConnectionManager,
    coordinator: ClientToolCoordinator | None = None,
    sandbox_store: SandboxWorkspaceStore | None = None,
) -> None:
    global _store, _connections, _coordinator, _sandbox_store
    _store = store
    _connections = connections
    _coordinator = coordinator
    _sandbox_store = sandbox_store


def configure_client_tool_coordinator(coordinator: ClientToolCoordinator) -> None:
    global _coordinator
    _coordinator = coordinator


def get_store() -> ClientToolStore:
    if _store is None:
        raise RuntimeError("Client tool runtime is not configured.")
    return _store


def get_connections() -> ClientToolConnectionManager:
    if _connections is None:
        raise RuntimeError("Client tool connections are not configured.")
    return _connections


def _serialize_request_with_host(
    request: Any, *, include_result: bool = False
) -> dict[str, Any]:
    payload = get_store().serialize_request(request, include_result=include_result)
    try:
        host = get_store().require_host(request.host_id)
    except Exception:
        return payload
    payload.update(
        {
            "host_type": host.host_type,
            "office_app": host.office_app or None,
            "document_binding": (
                {
                    "bound": bool(host.document_binding.get("bound")),
                    "title": str(host.document_binding.get("title") or "")[:300],
                    "binding_id": str(
                        host.document_binding.get("binding_id") or ""
                    )[:120],
                }
                if host.host_type == "office"
                else None
            ),
        }
    )
    return payload


def _raise_error(exc: Exception) -> None:
    if isinstance(exc, ClientToolNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, ClientToolAuthenticationError):
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if isinstance(exc, ClientToolConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/client-tools/capabilities")
async def client_tool_capabilities() -> dict[str, Any]:
    return {
        "version": "modelmirror-client-tools-v1",
        "minimum_chrome_version": 116,
        "pairing_ttl_seconds": 300,
        "heartbeat_seconds": 20,
        "max_read_characters": 24_000,
        "max_snapshot_elements": 500,
        "max_screenshot_bytes": 5 * 1024 * 1024,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "schema_hash": client_tool_schema_hash(tool),
            }
            for tool in CLIENT_TOOLS
        ],
    }


@router.get("/office-host/capabilities")
async def office_host_capabilities() -> dict[str, Any]:
    return {
        "version": "modelmirror-office-tools-v1",
        "protocol": "modelmirror-client-tools-v1",
        "hosts": ["word", "excel", "powerpoint"],
        "limits": {
            "word_characters": 20_000,
            "excel_rows": 1_000,
            "excel_columns": 200,
            "powerpoint_shapes": 200,
            "input_artifact_bytes": 10 * 1024 * 1024,
        },
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "schema_hash": client_tool_schema_hash(tool),
                "requirements": [
                    {"set": set_name, "version": version}
                    for set_name, version in OFFICE_TOOL_REQUIREMENTS.get(
                        tool.name, []
                    )
                ],
                "mutating": tool.name not in {
                    "office_powerpoint_snapshot",
                    "office_powerpoint_select_slide",
                    "office_word_snapshot",
                    "office_word_search_text",
                    "office_excel_snapshot",
                    "office_excel_get_range",
                },
            }
            for tool in OFFICE_TOOLS
        ],
    }


@router.get("/office-host/manifest.xml")
async def office_host_manifest() -> Response:
    base_url = os.getenv("OFFICE_HOST_BASE_URL", "https://localhost:8443").rstrip("/")
    source = escape(f"{base_url}/taskpane.html", quote=True)
    icon = escape(f"{base_url}/assets/icon.svg", quote=True)
    manifest = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<OfficeApp xmlns="http://schemas.microsoft.com/office/appforoffice/1.1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:type="TaskPaneApp">
  <Id>86d31d02-3ba5-4d5a-991b-5d4a620e0f09</Id>
  <Version>1.0.0.0</Version>
  <ProviderName>ModelMirror</ProviderName>
  <DefaultLocale>zh-CN</DefaultLocale>
  <DisplayName DefaultValue="ModelMirror Xpert Office Host"/>
  <Description DefaultValue="Bind the current Word, Excel, or PowerPoint document to a private Xpert runtime."/>
  <IconUrl DefaultValue="{icon}"/>
  <SupportUrl DefaultValue="{source}"/>
  <AppDomains><AppDomain>{escape(base_url)}</AppDomain></AppDomains>
  <Hosts>
    <Host Name="Document"/>
    <Host Name="Workbook"/>
    <Host Name="Presentation"/>
  </Hosts>
  <DefaultSettings><SourceLocation DefaultValue="{source}"/></DefaultSettings>
  <Permissions>ReadWriteDocument</Permissions>
</OfficeApp>"""
    return Response(
        manifest,
        media_type="application/xml",
        headers={
            "Content-Disposition": 'attachment; filename="modelmirror-office-addin.xml"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/client-tool-coordinator/status")
async def client_tool_coordinator_status() -> dict[str, Any]:
    if _coordinator is None:
        return {"enabled": False, "running": False}
    return await _coordinator.status()


@router.post("/client-hosts/pairings")
async def create_client_host_pairing(
    payload: PairingCreateRequest,
) -> dict[str, Any]:
    pairing, code = get_store().create_pairing(
        name=payload.name, host_type=payload.host_type
    )
    return {
        "pairing_id": pairing.pairing_id,
        "pairing_code": code,
        "expires_at": pairing.expires_at,
        "single_use": True,
        "host_type": pairing.host_type,
    }


@router.get("/client-hosts")
async def list_client_hosts() -> dict[str, Any]:
    return {
        "hosts": [
            get_store().serialize_host(item) for item in get_store().list_hosts()
        ]
    }


@router.get("/client-hosts/extension.zip")
async def download_client_host_extension() -> Response:
    source = Path(__file__).resolve().parent.parent / "client_extension"
    if not source.is_dir():
        raise HTTPException(status_code=404, detail="Client extension source is unavailable.")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(source.parent))
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="modelmirror-client-host.zip"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/client-hosts/{host_id}")
async def get_client_host(host_id: str) -> dict[str, Any]:
    try:
        return get_store().serialize_host(get_store().require_host(host_id))
    except Exception as exc:
        _raise_error(exc)


@router.post("/client-hosts/{host_id}/revoke")
async def revoke_client_host(host_id: str) -> dict[str, Any]:
    try:
        item = get_store().revoke_host(host_id)
        return get_store().serialize_host(item)
    except Exception as exc:
        _raise_error(exc)


@router.post("/client-hosts/{host_id}/unbind")
async def unbind_client_host(host_id: str) -> dict[str, Any]:
    try:
        return get_store().serialize_host(get_store().unbind_host(host_id))
    except Exception as exc:
        _raise_error(exc)


@router.get("/client-tools/fixture", response_class=HTMLResponse)
async def client_tool_fixture() -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>ModelMirror Client Tool Fixture</title></head>
<body><main aria-label="Client tool test page"><h1>Client tool test page</h1>
<label>Name <input name="display_name" autocomplete="name"></label>
<label>Role <select name="role"><option>Researcher</option><option>Editor</option></select></label>
<button type="button" id="save">Save</button><output id="result" aria-live="polite"></output>
<script>document.querySelector('#save').addEventListener('click',()=>{document.querySelector('#result').textContent='Saved: '+document.querySelector('[name=display_name]').value;});</script>
</main></body></html>"""


@router.get("/client-tool-requests")
async def list_client_tool_requests(
    status: str | None = None,
    host_id: str | None = None,
    task_id: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    items = get_store().list_requests(
        status=status,
        host_id=host_id,
        task_id=task_id,
        scope_type=scope_type,
        scope_id=scope_id,
        limit=limit,
    )
    return {"requests": [_serialize_request_with_host(item) for item in items]}


@router.get("/client-tool-requests/{request_id}")
async def get_client_tool_request(request_id: str) -> dict[str, Any]:
    try:
        return _serialize_request_with_host(
            get_store().require_request(request_id), include_result=True
        )
    except Exception as exc:
        _raise_error(exc)


@router.post("/client-tool-requests/{request_id}/retry")
async def retry_client_tool_request(request_id: str) -> dict[str, Any]:
    try:
        item = get_store().retry_request(request_id)
        if _coordinator is not None:
            _coordinator.wake()
        return get_store().serialize_request(item)
    except Exception as exc:
        _raise_error(exc)


@router.post("/client-tool-requests/{request_id}/cancel")
async def cancel_client_tool_request(request_id: str) -> dict[str, Any]:
    try:
        item = get_store().cancel_request(request_id)
        if _coordinator is not None:
            _coordinator.wake()
        return get_store().serialize_request(item)
    except Exception as exc:
        _raise_error(exc)


def _bearer_token(value: str | None) -> str:
    if not value or not value.startswith("Bearer "):
        raise ClientToolAuthenticationError("Client host bearer token is required.")
    return value.removeprefix("Bearer ").strip()


@router.post("/client-tool-requests/{request_id}/artifact")
async def upload_client_tool_artifact(
    request_id: str,
    file: UploadFile = File(...),
    x_modelmirror_client_host_id: str = Header(alias="X-ModelMirror-Client-Host-Id"),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    try:
        token = _bearer_token(authorization)
        host = get_store().authenticate(x_modelmirror_client_host_id, token)
        request = get_store().require_request(request_id)
        if request.host_id != host.host_id or request.tool_name != "host_page_screenshot":
            raise ClientToolConflictError("Artifact does not belong to this host request.")
        data = await file.read(5 * 1024 * 1024 + 1)
        artifact = get_store().register_artifact(
            request_id=request_id,
            host_id=host.host_id,
            filename=file.filename or "client-screenshot.png",
            content_type=file.content_type or "application/octet-stream",
            data=data,
        )
        return get_store().serialize_artifact(artifact)
    except Exception as exc:
        _raise_error(exc)


@router.get(
    "/client-tool-requests/{request_id}/input-artifacts/{artifact_id}"
)
async def download_office_input_artifact(
    request_id: str,
    artifact_id: str,
    x_modelmirror_client_host_id: str = Header(alias="X-ModelMirror-Client-Host-Id"),
    authorization: str | None = Header(default=None),
) -> FileResponse:
    try:
        token = _bearer_token(authorization)
        host = get_store().authenticate(x_modelmirror_client_host_id, token)
        request = get_store().require_request(request_id)
        if request.host_id != host.host_id or host.host_type != "office":
            raise ClientToolConflictError("Artifact request belongs to another host.")
        if request.tool_name != "office_powerpoint_insert_image":
            raise ClientToolConflictError("Only PowerPoint image insertion accepts artifacts.")
        if str(request.arguments.get("artifact_id") or "") != artifact_id:
            raise ClientToolConflictError("Artifact is not assigned to this tool request.")
        if _sandbox_store is None:
            raise ClientToolNotFoundError("Sandbox artifact store is unavailable.")
        artifact = _sandbox_store.get_artifact(artifact_id)
        workspace = _sandbox_store.get_workspace(artifact.workspace_id)
        if (
            workspace.scope_type != request.scope_type
            or workspace.scope_id != request.scope_id
        ):
            raise ClientToolConflictError("Artifact belongs to another runtime scope.")
        if not artifact.content_type.startswith("image/") or artifact.size_bytes > 10 * 1024 * 1024:
            raise ClientToolConflictError("Office input artifact must be a bounded image.")
        path = _sandbox_store.artifact_path(artifact_id)
        return FileResponse(path, media_type=artifact.content_type, filename=artifact.filename)
    except Exception as exc:
        _raise_error(exc)


@router.get("/client-tool-artifacts/{artifact_id}")
async def get_client_tool_artifact(artifact_id: str) -> dict[str, Any]:
    try:
        return get_store().serialize_artifact(
            get_store().require_artifact(artifact_id)
        )
    except Exception as exc:
        _raise_error(exc)


@router.get("/client-tool-artifacts/{artifact_id}/download")
async def download_client_tool_artifact(artifact_id: str) -> FileResponse:
    try:
        artifact = get_store().require_artifact(artifact_id)
        path = get_store().artifact_path(artifact)
        return FileResponse(
            path,
            media_type=artifact.content_type,
            filename=artifact.filename,
        )
    except Exception as exc:
        _raise_error(exc)


async def _first_frame(websocket: WebSocket) -> dict[str, Any]:
    try:
        value = await websocket.receive_json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise ClientToolAuthenticationError("Client host first frame must be JSON.") from exc
    if not isinstance(value, dict):
        raise ClientToolAuthenticationError("Client host first frame must be an object.")
    return value


@router.websocket("/client-tools/connect")
async def connect_client_tool_host(websocket: WebSocket) -> None:
    await websocket.accept()
    connection_id = f"conn_{uuid.uuid4().hex}"
    host_id = ""
    try:
        first = await _first_frame(websocket)
        message_type = str(first.get("type") or "")
        capabilities = (
            list(first.get("capabilities") or [])
            if isinstance(first.get("capabilities"), list)
            else []
        )
        schema_hashes = (
            dict(first.get("schema_hashes") or {})
            if isinstance(first.get("schema_hashes"), dict)
            else {}
        )
        version = str(first.get("version") or "")
        host_type = str(first.get("host_type") or "chrome")
        office_app = str(first.get("office_app") or "")
        document_binding = (
            dict(first.get("document_binding") or {})
            if isinstance(first.get("document_binding"), dict)
            else {}
        )
        requirement_sets = (
            list(first.get("requirement_sets") or [])
            if isinstance(first.get("requirement_sets"), list)
            else []
        )
        if message_type == "pair":
            host, token = get_store().consume_pairing(
                str(first.get("pairing_code") or ""),
                version=version,
                capabilities=capabilities,
                schema_hashes=schema_hashes,
                host_type=host_type,
                office_app=office_app,
                document_binding=document_binding,
                requirement_sets=requirement_sets,
            )
            host_id = host.host_id
            await websocket.send_json(
                {
                    "type": "welcome",
                    "protocol": "modelmirror-client-tools-v1",
                    "host_id": host.host_id,
                    "host_token": token,
                    "token_prefix": host.token_prefix,
                    "paired": True,
                    "heartbeat_seconds": 20,
                    "host_type": host.host_type,
                }
            )
        elif message_type == "authenticate":
            host_id = str(first.get("host_id") or "")
            host = get_store().authenticate(
                host_id, str(first.get("host_token") or "")
            )
            await websocket.send_json(
                {
                    "type": "welcome",
                    "protocol": "modelmirror-client-tools-v1",
                    "host_id": host.host_id,
                    "token_prefix": host.token_prefix,
                    "paired": False,
                    "heartbeat_seconds": 20,
                    "host_type": host.host_type,
                }
            )
        else:
            raise ClientToolAuthenticationError(
                "First frame must be pair or authenticate."
            )

        get_store().connect_host(
            host_id,
            connection_id=connection_id,
            version=version,
            capabilities=capabilities,
            schema_hashes=schema_hashes,
            bound_tab=(
                dict(first.get("bound_tab") or {})
                if isinstance(first.get("bound_tab"), dict)
                else {}
            ),
            host_type=host_type,
            office_app=office_app,
            document_binding=document_binding,
            requirement_sets=requirement_sets,
        )
        await get_connections().attach(host_id, connection_id, websocket)
        if _coordinator is not None:
            _coordinator.wake()

        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict):
                continue
            message_type = str(message.get("type") or "")
            if message_type in {"heartbeat", "host_state"}:
                get_store().heartbeat(
                    host_id,
                    connection_id=connection_id,
                    bound_tab=(
                        dict(message.get("bound_tab") or {})
                        if isinstance(message.get("bound_tab"), dict)
                        else None
                    ),
                    document_binding=(
                        dict(message.get("document_binding") or {})
                        if isinstance(message.get("document_binding"), dict)
                        else None
                    ),
                    office_app=(
                        str(message.get("office_app"))
                        if message.get("office_app") is not None
                        else None
                    ),
                    requirement_sets=(
                        list(message.get("requirement_sets") or [])
                        if isinstance(message.get("requirement_sets"), list)
                        else None
                    ),
                )
                await websocket.send_json({"type": "heartbeat", "ok": True})
            elif message_type == "tool_accepted":
                get_store().mark_running(
                    str(message.get("request_id") or ""), host_id=host_id
                )
            elif message_type == "tool_result":
                get_store().complete_request(
                    str(message.get("request_id") or ""),
                    host_id=host_id,
                    operation_id=str(message.get("operation_id") or ""),
                    tool_call_id=str(message.get("tool_call_id") or ""),
                    result=str(message.get("result") or ""),
                    metadata=(
                        dict(message.get("metadata") or {})
                        if isinstance(message.get("metadata"), dict)
                        else {}
                    ),
                )
                if _coordinator is not None:
                    _coordinator.wake()
            elif message_type == "tool_error":
                get_store().fail_request(
                    str(message.get("request_id") or ""),
                    host_id=host_id,
                    operation_id=str(message.get("operation_id") or ""),
                    tool_call_id=str(message.get("tool_call_id") or ""),
                    error=str(message.get("error") or "Client tool failed."),
                )
                if _coordinator is not None:
                    _coordinator.wake()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "message": str(exc)[:500]})
            await websocket.close(code=4003)
        except Exception:
            pass
    finally:
        if host_id:
            await get_connections().detach(host_id, connection_id)
