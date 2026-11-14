from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

try:
    from server.model_router.api import get_model_router_service
    from server.model_router.service import RouterServiceError
except ModuleNotFoundError:
    from model_router.api import get_model_router_service
    from model_router.service import RouterServiceError

from .voice_library import VoiceLibrary

router = APIRouter(prefix="/voice-library")


def get_voice_library():
    return VoiceLibrary(get_model_router_service())


class CreateVoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operationId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    model: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=40)
    audio: str = Field(max_length=14_000_000)
    consent: bool


class VoiceAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operationId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")


class VoiceRename(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=40)


class VoiceAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirmed: bool


def http_error(error):
    return HTTPException(error.status_code, detail={"code": error.code, "message": error.hint})


@router.get("/catalog")
def catalog():
    return get_voice_library().control.catalog()


@router.get("/assets")
def assets():
    return {"assets": get_voice_library().control.assets()}


@router.get("/operations/{identifier}")
def operation(identifier: str):
    try:
        return get_voice_library().control.operation(identifier)
    except RouterServiceError as error:
        raise http_error(error) from None


@router.post("/create")
async def create(request: Request):
    # Bound before JSON/base64 parsing; don't echo invalid audio in validation errors.
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 14_001_024:
            raise HTTPException(413, detail={"code": "voice_sample_invalid"})
        chunks.append(chunk)
    try:
        value = CreateVoice.model_validate_json(b"".join(chunks))
    except (ValidationError, ValueError):
        raise HTTPException(422, detail={"code": "voice_input_invalid", "message": "音色创建参数无效。"}) from None
    try:
        return await get_voice_library().create(operation_id=value.operationId, model=value.model, name=value.name, audio=value.audio, consent=value.consent)
    except RouterServiceError as error:
        raise http_error(error) from None


@router.post("/operations/{identifier}/acknowledge")
def acknowledge(identifier: str, payload: VoiceAcknowledgement):
    if payload.confirmed is not True:
        raise HTTPException(422, detail={"code": "voice_input_invalid"})
    try:
        return get_voice_library().control.acknowledge(identifier)
    except RouterServiceError as error:
        raise http_error(error) from None


@router.post("/{identifier}/rename")
def rename(identifier: str, payload: VoiceRename):
    try:
        return get_voice_library().control.rename(identifier, payload.name)
    except RouterServiceError as error:
        raise http_error(error) from None


@router.post("/{identifier}/{action}")
async def change(identifier: str, action: str, payload: VoiceAction):
    try:
        return await get_voice_library().change(identifier, action, payload.operationId)
    except RouterServiceError as error:
        raise http_error(error) from None
