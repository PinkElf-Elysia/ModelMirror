from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

try:
    from server.model_router.api import get_model_router_service
except ModuleNotFoundError:
    from model_router.api import get_model_router_service

from .stt import (
    MAX_AUDIO_BYTES,
    MultimodalServiceError,
    TranscriptionResult,
    TranscriptionService,
)
from .tts import SpeechResult, SpeechService


class TranscriptionUsageResponse(BaseModel):
    audio_seconds: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    cost_kind: str


class TranscriptionResponse(BaseModel):
    text: str
    requested_model: str
    actual_model: str
    provider: str
    request_id: str
    usage: TranscriptionUsageResponse


class SpeechRequest(BaseModel):
    model_id: str = Field(min_length=1, max_length=256)
    input: str = Field(min_length=1, max_length=4_000)
    voice: str = Field(min_length=1, max_length=128)
    response_format: Literal["mp3"] = "mp3"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


router = APIRouter(prefix="/api/multimodal", tags=["multimodal"])
_transcription_service: TranscriptionService | None = None
_speech_service: SpeechService | None = None


def configure_transcription_service(
    service: TranscriptionService | None,
) -> None:
    global _transcription_service
    _transcription_service = service


def get_transcription_service() -> TranscriptionService:
    global _transcription_service
    if _transcription_service is None:
        _transcription_service = TranscriptionService(
            get_model_router_service()
        )
    return _transcription_service


def configure_speech_service(service: SpeechService | None) -> None:
    global _speech_service
    _speech_service = service


def get_speech_service() -> SpeechService:
    global _speech_service
    if _speech_service is None:
        _speech_service = SpeechService(get_model_router_service())
    return _speech_service


@router.post(
    "/transcriptions",
    response_model=TranscriptionResponse,
)
async def transcribe_audio(
    model_id: str = Form(...),
    file: UploadFile = File(...),
    language: str = Form(default="auto"),
) -> TranscriptionResponse:
    try:
        content = await file.read(MAX_AUDIO_BYTES + 1)
        result = await get_transcription_service().transcribe(
            model_id=model_id,
            filename=file.filename or "audio",
            content_type=file.content_type,
            content=content,
            language=language,
        )
        return _response(result)
    except MultimodalServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    finally:
        await file.close()


@router.post("/speech")
async def synthesize_speech(payload: SpeechRequest) -> Response:
    try:
        result = await get_speech_service().synthesize(
            model_id=payload.model_id,
            text=payload.input,
            voice=payload.voice,
            response_format=payload.response_format,
            speed=payload.speed,
        )
    except MultimodalServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return _speech_response(result)


def _response(result: TranscriptionResult) -> TranscriptionResponse:
    return TranscriptionResponse(
        text=result.text,
        requested_model=result.requested_model,
        actual_model=result.actual_model,
        provider=result.provider,
        request_id=result.request_id,
        usage=TranscriptionUsageResponse(
            audio_seconds=result.usage.audio_seconds,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            total_tokens=result.usage.total_tokens,
            cost_usd=result.usage.cost_usd,
            cost_kind=result.usage.cost_kind,
        ),
    )


def _speech_response(result: SpeechResult) -> Response:
    headers = {
        "Content-Disposition": (
            'attachment; filename="modelmirror-speech.mp3"'
        ),
        "X-ModelMirror-Request-Id": result.request_id,
        "X-ModelMirror-Actual-Model": result.actual_model,
        "X-ModelMirror-Provider": result.provider,
        "X-ModelMirror-Cost-Kind": result.cost_kind,
        "X-ModelMirror-Output-Bytes": str(result.output_bytes),
    }
    if result.generation_id:
        headers["X-ModelMirror-Generation-Id"] = result.generation_id
    return Response(
        content=result.content,
        media_type="audio/mpeg",
        headers=headers,
    )
