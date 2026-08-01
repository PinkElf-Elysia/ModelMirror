from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from .browser_client import BrowserClientError, BrowserClientProtocol
from .browser_store import BrowserNotFoundError, BrowserSessionStore, BrowserValidationError


router = APIRouter(prefix="/api/runtime", tags=["runtime-browser"])
_store: BrowserSessionStore | None = None
_client: BrowserClientProtocol | None = None


def configure_runtime_browser(store: BrowserSessionStore, client: BrowserClientProtocol) -> None:
    global _store, _client
    _store = store
    _client = client


def _require_store() -> BrowserSessionStore:
    if _store is None:
        raise HTTPException(status_code=503, detail="Browser runtime is not configured.")
    return _store


def _require_client() -> BrowserClientProtocol:
    if _client is None:
        raise HTTPException(status_code=503, detail="Browser sidecar is not configured.")
    return _client


def _raise_store_error(exc: Exception) -> None:
    if isinstance(exc, BrowserNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, BrowserValidationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/browser/capabilities")
async def browser_capabilities() -> dict[str, Any]:
    try:
        sidecar = await _require_client().request({"action": "health"})
        return {
            "version": "browser-capabilities-v1",
            "available": True,
            "engine": "playwright-chromium",
            "network_policy": "public_with_domain_approval",
            "private_networks_blocked": True,
            "service_workers_blocked": True,
            "max_pages": 3,
            "max_actions": 100,
            "max_download_mb": 50,
            "sidecar": {
                "chromium": bool(sidecar.get("chromium")),
                "policy": str(sidecar.get("policy") or "public_only"),
            },
        }
    except (BrowserClientError, HTTPException) as exc:
        if isinstance(exc, HTTPException):
            raise
        return {
            "version": "browser-capabilities-v1",
            "available": False,
            "network_policy": "public_with_domain_approval",
            "error": str(exc),
        }


@router.get("/browser-sessions")
async def list_browser_sessions(
    scope_type: str | None = None,
    scope_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    store = _require_store()
    items = store.list_sessions(
        scope_type=scope_type, scope_id=scope_id, status=status, limit=limit
    )
    return {
        "version": "browser-sessions-v1",
        "items": [store.session_payload(item) for item in items],
        "total": len(items),
    }


@router.get("/browser-sessions/{session_id}")
async def get_browser_session(session_id: str) -> dict[str, Any]:
    store = _require_store()
    try:
        item = store.get_session(session_id)
        return {
            **store.session_payload(item),
            "operations": [
                store.operation_payload(operation)
                for operation in store.list_operations(session_id, limit=100)
            ],
            "artifacts": [
                store.artifact_payload(artifact)
                for artifact in store.list_artifacts(session_id)
            ],
        }
    except Exception as exc:
        _raise_store_error(exc)


@router.get("/browser-sessions/{session_id}/operations")
async def list_browser_operations(
    session_id: str, limit: int = Query(default=100, ge=1, le=500)
) -> dict[str, Any]:
    store = _require_store()
    try:
        return {
            "version": "browser-operations-v1",
            "items": [
                store.operation_payload(item)
                for item in store.list_operations(session_id, limit=limit)
            ],
        }
    except Exception as exc:
        _raise_store_error(exc)


@router.get("/browser-sessions/{session_id}/screenshot")
async def latest_browser_screenshot(session_id: str):
    store = _require_store()
    try:
        screenshots = [
            item for item in store.list_artifacts(session_id) if item.kind == "screenshot"
        ]
        if not screenshots:
            raise BrowserNotFoundError("Browser screenshot not found.")
        screenshots.sort(key=lambda item: item.created_at, reverse=True)
        target = store.artifact_path(screenshots[0].artifact_id)
        if not target.exists() or not target.is_file():
            raise BrowserNotFoundError("Browser screenshot file is unavailable.")
        return FileResponse(Path(target), media_type="image/png")
    except Exception as exc:
        _raise_store_error(exc)


@router.delete("/browser-sessions/{session_id}/grants/{domain}")
async def revoke_browser_domain(session_id: str, domain: str) -> dict[str, Any]:
    store = _require_store()
    try:
        return store.session_payload(store.revoke_domain(session_id, domain))
    except Exception as exc:
        _raise_store_error(exc)


@router.post("/browser-sessions/{session_id}/close")
async def close_browser_session(session_id: str) -> dict[str, Any]:
    store = _require_store()
    try:
        item = store.get_session(session_id)
        try:
            await _require_client().request(
                {"action": "close_session", "session_id": session_id}
            )
        except BrowserClientError:
            pass
        return store.session_payload(store.close_session(item.session_id))
    except Exception as exc:
        _raise_store_error(exc)


@router.get("/browser-artifacts/{artifact_id}")
async def get_browser_artifact(artifact_id: str) -> dict[str, Any]:
    store = _require_store()
    try:
        return store.artifact_payload(store.get_artifact(artifact_id))
    except Exception as exc:
        _raise_store_error(exc)


@router.get("/browser-artifacts/{artifact_id}/download")
async def download_browser_artifact(artifact_id: str):
    store = _require_store()
    try:
        artifact = store.get_artifact(artifact_id)
        target = store.artifact_path(artifact_id)
        if not target.exists() or not target.is_file():
            raise BrowserNotFoundError("Browser artifact file is unavailable.")
        return FileResponse(
            Path(target), media_type=artifact.content_type, filename=artifact.filename
        )
    except Exception as exc:
        _raise_store_error(exc)
