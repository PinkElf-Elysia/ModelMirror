from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from .sandbox_client import SandboxClientError, SandboxClientProtocol
from .sandbox_store import SandboxNotFoundError, SandboxValidationError, SandboxWorkspaceStore


router = APIRouter(prefix="/api/runtime", tags=["runtime-sandbox"])
_store: SandboxWorkspaceStore | None = None
_client: SandboxClientProtocol | None = None


def configure_runtime_sandbox(store: SandboxWorkspaceStore, client: SandboxClientProtocol) -> None:
    global _store, _client
    _store = store
    _client = client


def _require_store() -> SandboxWorkspaceStore:
    if _store is None:
        raise HTTPException(status_code=503, detail="Sandbox runtime is not configured.")
    return _store


def _require_client() -> SandboxClientProtocol:
    if _client is None:
        raise HTTPException(status_code=503, detail="Sandbox sidecar is not configured.")
    return _client


def _raise_store_error(exc: Exception) -> None:
    if isinstance(exc, SandboxNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, SandboxValidationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/sandbox/capabilities")
async def sandbox_capabilities() -> dict[str, Any]:
    try:
        sidecar = await _require_client().request({"action": "health"})
        return {
            "version": "sandbox-capabilities-v1",
            "available": True,
            "network": "none",
            "max_workspace_mb": 1024,
            "default_workspace_mb": 256,
            "max_command_seconds": 300,
            "allowed_commands": sidecar.get("allowed_commands", []),
            "landlock_required": bool(sidecar.get("landlock_required", True)),
        }
    except (SandboxClientError, HTTPException) as exc:
        if isinstance(exc, HTTPException):
            raise
        return {
            "version": "sandbox-capabilities-v1",
            "available": False,
            "network": "none",
            "error": str(exc),
        }


@router.get("/sandbox-workspaces")
async def list_sandbox_workspaces(
    scope_type: str | None = None,
    scope_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    store = _require_store()
    items = store.list_workspaces(scope_type=scope_type, scope_id=scope_id, limit=limit)
    return {
        "version": "sandbox-workspaces-v1",
        "items": [store.workspace_payload(item, store.list_operations(item.workspace_id, limit=20)) for item in items],
        "total": len(items),
    }


@router.get("/sandbox-workspaces/{workspace_id}")
async def get_sandbox_workspace(workspace_id: str) -> dict[str, Any]:
    store = _require_store()
    try:
        item = store.get_workspace(workspace_id)
        artifacts = [store.artifact_payload(store.get_artifact(value)) for value in item.artifact_ids]
        return {
            **store.workspace_payload(item, store.list_operations(workspace_id, limit=100)),
            "artifacts": artifacts,
        }
    except Exception as exc:
        _raise_store_error(exc)


@router.get("/sandbox-workspaces/{workspace_id}/files")
async def list_sandbox_files(workspace_id: str, path: str = "") -> dict[str, Any]:
    try:
        _require_store().get_workspace(workspace_id)
        return await _require_client().request({"action": "list_files", "workspace_id": workspace_id, "path": path})
    except SandboxClientError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _raise_store_error(exc)


@router.get("/sandbox-workspaces/{workspace_id}/files/content")
async def read_sandbox_file(workspace_id: str, path: str) -> dict[str, Any]:
    try:
        _require_store().get_workspace(workspace_id)
        return await _require_client().request({"action": "read_file", "workspace_id": workspace_id, "path": path, "max_chars": 20_000})
    except SandboxClientError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        _raise_store_error(exc)


@router.get("/sandbox-artifacts/{artifact_id}")
async def get_sandbox_artifact(artifact_id: str) -> dict[str, Any]:
    store = _require_store()
    try:
        return store.artifact_payload(store.get_artifact(artifact_id))
    except Exception as exc:
        _raise_store_error(exc)


@router.get("/sandbox-artifacts/{artifact_id}/download")
async def download_sandbox_artifact(artifact_id: str):
    store = _require_store()
    try:
        artifact = store.get_artifact(artifact_id)
        path = store.artifact_path(artifact_id)
        if not path.exists() or not path.is_file():
            raise SandboxNotFoundError("Sandbox artifact file is unavailable.")
        return FileResponse(
            Path(path),
            media_type=artifact.content_type,
            filename=artifact.filename,
        )
    except Exception as exc:
        _raise_store_error(exc)
