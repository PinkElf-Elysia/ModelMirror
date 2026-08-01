from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .models import PromptProfileDefinition, PromptProfileVersion
from .store import (
    PromptProfileConflictError,
    PromptProfileError,
    PromptProfileNotFoundError,
    PromptProfileStore,
    PromptProfileValidationError,
)


router = APIRouter(prefix="/api/prompt-profiles", tags=["prompt-profiles"])
_store: PromptProfileStore | None = None


class PromptProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=64)
    description: str = Field(default="", max_length=2000)
    aliases: list[str] = Field(default_factory=list, max_length=5)
    template: str = Field(default="{{args}}", min_length=1, max_length=20_000)
    argument_hint: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=30)
    public_app_allowed: bool = False


class PromptProfilePatchRequest(BaseModel):
    revision: int = Field(ge=1)
    name: str | None = Field(default=None, max_length=120)
    slug: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    aliases: list[str] | None = Field(default=None, max_length=5)
    template: str | None = Field(default=None, max_length=20_000)
    argument_hint: str | None = Field(default=None, max_length=500)
    tags: list[str] | None = Field(default=None, max_length=30)
    public_app_allowed: bool | None = None


class RevisionRequest(BaseModel):
    revision: int = Field(ge=1)


def get_prompt_profile_store() -> PromptProfileStore:
    global _store
    if _store is None:
        _store = PromptProfileStore()
    return _store


def configure_prompt_profile_store(store: PromptProfileStore) -> None:
    global _store
    _store = store


def _raise_store_error(exc: PromptProfileError) -> None:
    if isinstance(exc, PromptProfileNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, PromptProfileConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, PromptProfileValidationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("")
async def list_prompt_profiles(
    status: str | None = Query(default=None),
    search: str = Query(default="", max_length=200),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    items = await asyncio.to_thread(
        get_prompt_profile_store().list_profiles,
        status=status,
        search=search,
        limit=limit,
    )
    return {
        "version": "prompt-profile-registry-v1",
        "items": [item.model_dump(mode="json") for item in items],
        "total": len(items),
    }


@router.post("", response_model=PromptProfileDefinition)
async def create_prompt_profile(
    payload: PromptProfileCreateRequest,
) -> PromptProfileDefinition:
    try:
        return await asyncio.to_thread(
            get_prompt_profile_store().create_profile,
            **payload.model_dump(mode="json"),
        )
    except PromptProfileError as exc:
        _raise_store_error(exc)


@router.get("/{profile_id}", response_model=PromptProfileDefinition)
async def get_prompt_profile(profile_id: str) -> PromptProfileDefinition:
    try:
        return await asyncio.to_thread(
            get_prompt_profile_store().get_profile, profile_id
        )
    except PromptProfileError as exc:
        _raise_store_error(exc)


@router.patch("/{profile_id}", response_model=PromptProfileDefinition)
async def patch_prompt_profile(
    profile_id: str, payload: PromptProfilePatchRequest
) -> PromptProfileDefinition:
    try:
        patch = payload.model_dump(
            exclude={"revision"}, exclude_unset=True, mode="json"
        )
        return await asyncio.to_thread(
            get_prompt_profile_store().update_profile,
            profile_id,
            revision=payload.revision,
            patch=patch,
        )
    except PromptProfileError as exc:
        _raise_store_error(exc)


@router.post("/{profile_id}/validate")
async def validate_prompt_profile(profile_id: str) -> dict[str, Any]:
    try:
        return await asyncio.to_thread(
            get_prompt_profile_store().validate_profile, profile_id
        )
    except PromptProfileError as exc:
        _raise_store_error(exc)


@router.post("/{profile_id}/publish", response_model=PromptProfileVersion)
async def publish_prompt_profile(
    profile_id: str, payload: RevisionRequest
) -> PromptProfileVersion:
    try:
        return await asyncio.to_thread(
            get_prompt_profile_store().publish_profile,
            profile_id,
            revision=payload.revision,
        )
    except PromptProfileError as exc:
        _raise_store_error(exc)


@router.post("/{profile_id}/archive", response_model=PromptProfileDefinition)
async def archive_prompt_profile(
    profile_id: str, payload: RevisionRequest
) -> PromptProfileDefinition:
    try:
        return await asyncio.to_thread(
            get_prompt_profile_store().archive_profile,
            profile_id,
            revision=payload.revision,
        )
    except PromptProfileError as exc:
        _raise_store_error(exc)


@router.get("/{profile_id}/versions", response_model=list[PromptProfileVersion])
async def list_prompt_profile_versions(
    profile_id: str,
) -> list[PromptProfileVersion]:
    try:
        return await asyncio.to_thread(
            get_prompt_profile_store().list_versions, profile_id
        )
    except PromptProfileError as exc:
        _raise_store_error(exc)


@router.get(
    "/{profile_id}/versions/{version}", response_model=PromptProfileVersion
)
async def get_prompt_profile_version(
    profile_id: str, version: int
) -> PromptProfileVersion:
    try:
        return await asyncio.to_thread(
            get_prompt_profile_store().get_version, profile_id, version
        )
    except PromptProfileError as exc:
        _raise_store_error(exc)
