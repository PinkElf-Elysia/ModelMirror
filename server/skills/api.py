from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .skill_manager import (
    InstalledSkill,
    SkillInstallError,
    SkillManager,
    SkillManagerError,
    SkillNotFoundError,
    SkillValidationError,
)
from .draft_store import (
    SkillDraftConflictError,
    SkillDraftError,
    SkillDraftNotFoundError,
    SkillDraftValidationError,
    WorkspaceSkillDraftStore,
)


router = APIRouter(prefix="/api/skills", tags=["skills"])
_skill_manager: SkillManager | None = None
_skill_draft_store: WorkspaceSkillDraftStore | None = None


class SkillInstallRequest(BaseModel):
    repo_url: str = Field(min_length=1, max_length=500)
    sub_path: str = Field(default="", max_length=260)


class SkillPayload(BaseModel):
    skill_id: str
    name: str
    description: str
    repo_url: str
    sub_path: str
    installed_at: float


class InstalledSkillsResponse(BaseModel):
    skills: list[SkillPayload]


class SkillContentResponse(BaseModel):
    skill_id: str
    content: str


class SkillDraftActionRequest(BaseModel):
    revision: int = Field(ge=1)


class SkillDraftPatchRequest(BaseModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=1000)
    skill_markdown: str | None = Field(default=None, max_length=1_048_576)
    files: dict[str, str] | None = None


def get_skill_manager() -> SkillManager:
    """Return the process-wide Skill manager."""

    global _skill_manager
    if _skill_manager is None:
        _skill_manager = SkillManager()
    return _skill_manager


def set_skill_manager_for_tests(manager: SkillManager | None) -> None:
    """Replace the global Skill manager in tests."""

    global _skill_manager
    _skill_manager = manager


def get_skill_draft_store() -> WorkspaceSkillDraftStore:
    global _skill_draft_store
    if _skill_draft_store is None:
        _skill_draft_store = WorkspaceSkillDraftStore()
    return _skill_draft_store


def set_skill_draft_store_for_tests(
    store: WorkspaceSkillDraftStore | None,
) -> None:
    global _skill_draft_store
    _skill_draft_store = store


def _payload_from_skill(skill: InstalledSkill) -> SkillPayload:
    return SkillPayload(
        skill_id=skill.skill_id,
        name=skill.name,
        description=skill.description,
        repo_url=skill.repo_url,
        sub_path=skill.sub_path,
        installed_at=skill.installed_at,
    )


@router.get("/installed", response_model=InstalledSkillsResponse)
async def list_installed_skills() -> InstalledSkillsResponse:
    skills = await asyncio.to_thread(get_skill_manager().list_installed_skills)
    return InstalledSkillsResponse(
        skills=[_payload_from_skill(skill) for skill in skills]
    )


def _raise_draft_error(exc: Exception) -> None:
    if isinstance(exc, SkillDraftNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, SkillDraftConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, SkillDraftValidationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/drafts")
async def list_skill_drafts(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    items = await asyncio.to_thread(
        get_skill_draft_store().list, status=status, limit=limit
    )
    return {
        "version": "workspace-skill-drafts-v1",
        "items": [WorkspaceSkillDraftStore.serialize(item) for item in items],
        "total": len(items),
    }


@router.get("/drafts/{draft_id}")
async def get_skill_draft(draft_id: str):
    try:
        item = await asyncio.to_thread(get_skill_draft_store().require, draft_id)
        return WorkspaceSkillDraftStore.serialize(item, include_content=True)
    except SkillDraftError as exc:
        _raise_draft_error(exc)


@router.patch("/drafts/{draft_id}")
async def patch_skill_draft(draft_id: str, payload: SkillDraftPatchRequest):
    try:
        item = await asyncio.to_thread(
            get_skill_draft_store().update,
            draft_id,
            revision=payload.revision,
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
            skill_markdown=payload.skill_markdown,
            files=payload.files,
        )
        return WorkspaceSkillDraftStore.serialize(item, include_content=True)
    except SkillDraftError as exc:
        _raise_draft_error(exc)


@router.post("/drafts/{draft_id}/validate")
async def validate_skill_draft(draft_id: str, payload: SkillDraftActionRequest):
    try:
        item = await asyncio.to_thread(get_skill_draft_store().require, draft_id)
        if item.revision != payload.revision:
            raise SkillDraftConflictError(
                "Skill draft changed. Reload it before validation."
            )
        result = await asyncio.to_thread(
            get_skill_draft_store().validate_draft, draft_id
        )
        await asyncio.to_thread(
            get_skill_draft_store().set_validation,
            draft_id,
            revision=payload.revision,
            validation=result,
        )
        return result
    except SkillDraftError as exc:
        _raise_draft_error(exc)


@router.post("/drafts/{draft_id}/install")
async def install_skill_draft(draft_id: str, payload: SkillDraftActionRequest):
    try:
        store = get_skill_draft_store()
        item = await asyncio.to_thread(store.require, draft_id)
        if item.revision != payload.revision:
            raise SkillDraftConflictError(
                "Skill draft changed. Reload it before installation."
            )
        await asyncio.to_thread(store.validate_draft, draft_id)
        installed = await asyncio.to_thread(
            get_skill_manager().install_workspace_draft,
            draft_id=item.draft_id,
            slug=item.slug,
            skill_markdown=item.skill_markdown,
            files=item.files,
        )
        updated = await asyncio.to_thread(
            store.mark_installed,
            draft_id,
            revision=payload.revision,
            skill_id=installed.skill_id,
        )
        return {
            "draft": WorkspaceSkillDraftStore.serialize(updated),
            "installed": _payload_from_skill(installed).model_dump(mode="json"),
        }
    except SkillDraftError as exc:
        _raise_draft_error(exc)
    except (SkillInstallError, SkillValidationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/drafts/{draft_id}/archive")
async def archive_skill_draft(draft_id: str, payload: SkillDraftActionRequest):
    try:
        item = await asyncio.to_thread(
            get_skill_draft_store().archive,
            draft_id,
            revision=payload.revision,
        )
        return WorkspaceSkillDraftStore.serialize(item)
    except SkillDraftError as exc:
        _raise_draft_error(exc)


@router.post("/install", response_model=SkillPayload)
async def install_skill(payload: SkillInstallRequest) -> SkillPayload:
    try:
        skill = await asyncio.to_thread(
            get_skill_manager().install_skill,
            payload.repo_url,
            payload.sub_path,
        )
        return _payload_from_skill(skill)
    except SkillValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SkillInstallError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SkillManagerError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/{skill_id}")
async def uninstall_skill(skill_id: str) -> dict[str, bool]:
    try:
        await asyncio.to_thread(get_skill_manager().uninstall_skill, skill_id)
        return {"ok": True}
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{skill_id}/content", response_model=SkillContentResponse)
async def get_skill_content(skill_id: str) -> SkillContentResponse:
    try:
        content = await asyncio.to_thread(
            get_skill_manager().get_skill_content,
            skill_id,
        )
        return SkillContentResponse(skill_id=skill_id, content=content)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SkillValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

