from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

try:
    from server.skills.api import get_skill_manager
    from server.toolsets import get_toolset_service
    from server.xpert_runtime import runtime_middleware_registry
except ModuleNotFoundError:
    from skills.api import get_skill_manager
    from toolsets import get_toolset_service
    from xpert_runtime import runtime_middleware_registry

from .models import PluginDefinition, PluginSkillDefinition, PluginVersion
from .registry import configure_plugin_store, get_plugin_store
from .store import (
    PluginConflictError,
    PluginError,
    PluginNotFoundError,
    PluginStore,
    PluginValidationError,
)


router = APIRouter(prefix="/api/plugins", tags=["plugins"])


class PluginPatchRequest(BaseModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=30)
    license: str | None = Field(default=None, max_length=160)


class RevisionRequest(BaseModel):
    revision: int = Field(ge=1)


def _raise_store_error(exc: PluginError) -> None:
    if isinstance(exc, PluginNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, PluginConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, PluginValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


def _validate_dependencies(item: PluginDefinition) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for raw in item.manifest.get("toolsets") or []:
        try:
            toolset_id = str(raw.get("toolset_id") or "")
            version_number = int(raw.get("version") or 0)
            snapshot = get_toolset_service().store.get_version(
                toolset_id, version_number
            )
            if snapshot.schema_hash != str(raw.get("schema_hash") or ""):
                raise ValueError("Toolset schema hash does not match.")
        except Exception as exc:
            issues.append(
                {
                    "code": "plugin_toolset_dependency_invalid",
                    "message": str(exc),
                }
            )
    middleware_ids = {item.id for item in runtime_middleware_registry.list()}
    for raw in item.manifest.get("middleware_presets") or []:
        middleware_id = str(raw.get("middleware_id") or "")
        if middleware_id not in middleware_ids:
            issues.append(
                {
                    "code": "plugin_middleware_unknown",
                    "message": f"Middleware is not registered: {middleware_id}",
                }
            )
    return issues


@router.get("")
async def list_plugins(
    status: str | None = Query(default=None),
    search: str = Query(default="", max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    items = await asyncio.to_thread(
        get_plugin_store().list_plugins,
        status=status,
        search=search,
        limit=limit,
    )
    return {
        "version": "modelmirror-plugin-registry-v1",
        "items": [item.model_dump(mode="json") for item in items],
        "total": len(items),
    }


@router.post("/import", response_model=PluginDefinition)
async def import_plugin_package(
    file: UploadFile = File(...),
) -> PluginDefinition:
    try:
        content = await file.read(PluginStore.MAX_PACKAGE_BYTES + 1)
        return await asyncio.to_thread(
            get_plugin_store().import_package,
            filename=file.filename or "plugin.zip",
            content=content,
        )
    except PluginError as exc:
        _raise_store_error(exc)


@router.get("/{plugin_id}", response_model=PluginDefinition)
async def get_plugin(plugin_id: str) -> PluginDefinition:
    try:
        return await asyncio.to_thread(get_plugin_store().get_plugin, plugin_id)
    except PluginError as exc:
        _raise_store_error(exc)


@router.patch("/{plugin_id}", response_model=PluginDefinition)
async def patch_plugin(
    plugin_id: str, payload: PluginPatchRequest
) -> PluginDefinition:
    try:
        return await asyncio.to_thread(
            get_plugin_store().update_plugin,
            plugin_id,
            revision=payload.revision,
            patch=payload.model_dump(
                exclude={"revision"}, exclude_unset=True, mode="json"
            ),
        )
    except PluginError as exc:
        _raise_store_error(exc)


@router.post("/{plugin_id}/validate")
async def validate_plugin(plugin_id: str) -> dict[str, Any]:
    try:
        result = await asyncio.to_thread(
            get_plugin_store().validate_plugin, plugin_id
        )
        item = await asyncio.to_thread(get_plugin_store().get_plugin, plugin_id)
        issues = list(result["issues"]) + _validate_dependencies(item)
        return {**result, "valid": not issues, "issues": issues}
    except PluginError as exc:
        _raise_store_error(exc)


@router.post("/{plugin_id}/publish", response_model=PluginVersion)
async def publish_plugin(
    plugin_id: str, payload: RevisionRequest
) -> PluginVersion:
    try:
        store = get_plugin_store()
        item = await asyncio.to_thread(store.get_plugin, plugin_id)
        if item.draft_revision != payload.revision:
            raise PluginConflictError("Plugin draft changed. Reload and retry.")
        if item.status == "archived":
            raise PluginConflictError("Archived Plugins cannot be published.")
        validation = await validate_plugin(plugin_id)
        if not validation["valid"]:
            raise HTTPException(status_code=422, detail=validation)
        version_number = await asyncio.to_thread(store.next_version, plugin_id)
        skill_manager = get_skill_manager()
        installed_skill_ids: list[str] = []
        try:
            for raw in item.manifest.get("skills") or []:
                skill = PluginSkillDefinition.model_validate(raw)
                skill_markdown, files = await asyncio.to_thread(
                    store.read_draft_skill, item.id, skill
                )
                installed = await asyncio.to_thread(
                    skill_manager.install_plugin_skill,
                    plugin_id=item.id,
                    plugin_slug=item.slug,
                    plugin_version=version_number,
                    skill_slug=skill.slug,
                    skill_markdown=skill_markdown,
                    files=files,
                )
                installed_skill_ids.append(installed.skill_id)
            return await asyncio.to_thread(
                store.publish_plugin,
                plugin_id,
                revision=payload.revision,
                installed_skill_ids=installed_skill_ids,
            )
        except Exception:
            for skill_id in reversed(installed_skill_ids):
                try:
                    await asyncio.to_thread(
                        skill_manager.uninstall_skill, skill_id
                    )
                except Exception:
                    continue
            raise
    except HTTPException:
        raise
    except PluginError as exc:
        _raise_store_error(exc)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{plugin_id}/archive", response_model=PluginDefinition)
async def archive_plugin(
    plugin_id: str, payload: RevisionRequest
) -> PluginDefinition:
    try:
        return await asyncio.to_thread(
            get_plugin_store().archive_plugin,
            plugin_id,
            revision=payload.revision,
        )
    except PluginError as exc:
        _raise_store_error(exc)


@router.get("/{plugin_id}/versions", response_model=list[PluginVersion])
async def list_plugin_versions(plugin_id: str) -> list[PluginVersion]:
    try:
        return await asyncio.to_thread(
            get_plugin_store().list_versions, plugin_id
        )
    except PluginError as exc:
        _raise_store_error(exc)


@router.get("/{plugin_id}/versions/{version}", response_model=PluginVersion)
async def get_plugin_version(plugin_id: str, version: int) -> PluginVersion:
    try:
        return await asyncio.to_thread(
            get_plugin_store().get_version, plugin_id, version
        )
    except PluginError as exc:
        _raise_store_error(exc)
