from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

try:
    from server.model_router.api import get_model_router_service
except ModuleNotFoundError:
    from model_router.api import get_model_router_service

from .audio_catalog import (
    AudioCatalogService,
    AudioModelCatalogResponse,
)
from .chat_attachments import (
    ChatAttachmentDeleteResponse,
    ChatAttachmentResponse,
    ChatAttachmentStore,
)
from .stt import (
    MAX_AUDIO_BYTES,
    MultimodalServiceError,
    TranscriptionResult,
    TranscriptionService,
)
from .tts import SpeechResult, SpeechService
from .video_analysis import (
    MAX_VIDEO_BYTES,
    VideoAnalysisResult,
    VideoAnalysisService,
)
from .video_catalog import (
    VideoCatalogService,
    VideoModelCatalogResponse,
)
from .video_jobs import (
    MAX_FIRST_FRAME_BYTES,
    VideoJob,
    VideoJobDeleteResult,
    VideoJobList,
    VideoJobService,
)


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


class VideoAnalysisUsageResponse(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    cost_kind: str


class VideoAnalysisResponse(BaseModel):
    text: str
    requested_model: str
    actual_model: str
    provider: str
    request_id: str
    source_kind: Literal["file", "url"]
    usage: VideoAnalysisUsageResponse


@asynccontextmanager
async def _multimodal_lifespan(_: object) -> AsyncIterator[None]:
    enabled = any(
        os.getenv(name, "false").strip().lower()
        in {"1", "true", "yes", "on"}
        for name in (
            "MULTIMODAL_CHAT_AUDIO_ENABLED",
            "MULTIMODAL_CHAT_VIDEO_ENABLED",
        )
    )
    store = get_chat_attachment_store() if enabled else None
    if store is not None:
        await asyncio.to_thread(store.cleanup_expired)
    try:
        yield
    finally:
        if store is not None and _chat_attachment_store is store:
            configure_chat_attachment_store(None)


router = APIRouter(
    prefix="/api/multimodal",
    tags=["multimodal"],
    lifespan=_multimodal_lifespan,
)
_transcription_service: TranscriptionService | None = None
_speech_service: SpeechService | None = None
_audio_catalog_service: AudioCatalogService | None = None
_chat_attachment_store: ChatAttachmentStore | None = None
_video_catalog_service: VideoCatalogService | None = None
_video_analysis_service: VideoAnalysisService | None = None
_video_job_service: VideoJobService | None = None


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


def configure_audio_catalog_service(
    service: AudioCatalogService | None,
) -> None:
    global _audio_catalog_service
    _audio_catalog_service = service


def get_audio_catalog_service() -> AudioCatalogService:
    global _audio_catalog_service
    if _audio_catalog_service is None:
        _audio_catalog_service = AudioCatalogService(
            get_model_router_service()
        )
    return _audio_catalog_service


def configure_chat_attachment_store(
    store: ChatAttachmentStore | None,
) -> None:
    global _chat_attachment_store
    current = _chat_attachment_store
    _chat_attachment_store = store
    if current is not None and current is not store:
        current.close()


def get_chat_attachment_store() -> ChatAttachmentStore:
    global _chat_attachment_store
    if _chat_attachment_store is None:
        router_service = get_model_router_service()
        _chat_attachment_store = ChatAttachmentStore(
            tenant_id=router_service.tenant_id
        )
    return _chat_attachment_store


def configure_video_catalog_service(
    service: VideoCatalogService | None,
) -> None:
    global _video_catalog_service
    _video_catalog_service = service


def get_video_catalog_service() -> VideoCatalogService:
    global _video_catalog_service
    if _video_catalog_service is None:
        _video_catalog_service = VideoCatalogService(
            get_model_router_service()
        )
    return _video_catalog_service


def configure_video_analysis_service(
    service: VideoAnalysisService | None,
) -> None:
    global _video_analysis_service
    _video_analysis_service = service


def get_video_analysis_service() -> VideoAnalysisService:
    global _video_analysis_service
    if _video_analysis_service is None:
        _video_analysis_service = VideoAnalysisService(
            get_model_router_service(),
            get_video_catalog_service(),
        )
    return _video_analysis_service


def configure_video_job_service(service: VideoJobService | None) -> None:
    global _video_job_service
    _video_job_service = service


def get_video_job_service() -> VideoJobService:
    global _video_job_service
    if _video_job_service is None:
        _video_job_service = VideoJobService(
            get_model_router_service(),
            get_video_catalog_service(),
        )
    return _video_job_service


@router.get("/audio/models", response_model=AudioModelCatalogResponse)
async def get_audio_models() -> AudioModelCatalogResponse:
    return await get_audio_catalog_service().get_catalog()


@router.post(
    "/chat/attachments",
    response_model=ChatAttachmentResponse,
)
async def create_chat_attachment(
    kind: Literal["audio", "video"] = Form(...),
    file: UploadFile = File(...),
) -> ChatAttachmentResponse:
    limit = MAX_AUDIO_BYTES if kind == "audio" else MAX_VIDEO_BYTES
    try:
        content = await file.read(limit + 1)
        store = get_chat_attachment_store()
        return await asyncio.to_thread(
            store.create,
            kind=kind,
            filename=file.filename or kind,
            content_type=file.content_type,
            content=content,
        )
    except MultimodalServiceError as exc:
        raise _http_error(exc) from exc
    finally:
        await file.close()


@router.delete(
    "/chat/attachments/{attachment_id}",
    response_model=ChatAttachmentDeleteResponse,
)
async def delete_chat_attachment(
    attachment_id: str,
) -> ChatAttachmentDeleteResponse:
    try:
        return await asyncio.to_thread(
            get_chat_attachment_store().delete,
            attachment_id,
        )
    except MultimodalServiceError as exc:
        raise _http_error(exc) from exc


@router.get("/video/models", response_model=VideoModelCatalogResponse)
async def get_video_models() -> VideoModelCatalogResponse:
    return await get_video_catalog_service().get_catalog()


@router.post(
    "/video/analysis",
    response_model=VideoAnalysisResponse,
)
async def analyze_video(
    model_id: str = Form(...),
    prompt: str = Form(...),
    source_type: Literal["file", "url"] = Form(...),
    file: UploadFile | None = File(default=None),
    video_url: str | None = Form(default=None),
) -> VideoAnalysisResponse:
    try:
        content = (
            await file.read(MAX_VIDEO_BYTES + 1)
            if file is not None
            else None
        )
        result = await get_video_analysis_service().analyze(
            model_id=model_id,
            prompt=prompt,
            source_type=source_type,
            filename=file.filename if file is not None else None,
            content_type=file.content_type if file is not None else None,
            content=content,
            video_url=video_url,
        )
        return _video_analysis_response(result)
    except MultimodalServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    finally:
        if file is not None:
            await file.close()


@router.post("/video/jobs", response_model=VideoJob)
async def create_video_job(
    model_id: str = Form(...),
    prompt: str = Form(...),
    idempotency_key: str = Form(...),
    duration: int | None = Form(default=None),
    resolution: str | None = Form(default=None),
    aspect_ratio: str | None = Form(default=None),
    generate_audio: bool = Form(default=False),
    seed: int | None = Form(default=None),
    first_frame: UploadFile | None = File(default=None),
) -> VideoJob:
    try:
        content = (
            await first_frame.read(MAX_FIRST_FRAME_BYTES + 1)
            if first_frame is not None
            else None
        )
        return await get_video_job_service().create(
            model_id=model_id,
            prompt=prompt,
            idempotency_key=idempotency_key,
            duration=duration,
            resolution=resolution,
            aspect_ratio=aspect_ratio,
            generate_audio=generate_audio,
            seed=seed,
            first_frame_filename=(
                first_frame.filename if first_frame is not None else None
            ),
            first_frame_content_type=(
                first_frame.content_type
                if first_frame is not None
                else None
            ),
            first_frame_content=content,
        )
    except MultimodalServiceError as exc:
        raise _http_error(exc) from exc
    finally:
        if first_frame is not None:
            await first_frame.close()


@router.get("/video/jobs", response_model=VideoJobList)
async def list_video_jobs(limit: int = 50) -> VideoJobList:
    try:
        return get_video_job_service().list(limit=limit)
    except MultimodalServiceError as exc:
        raise _http_error(exc) from exc


@router.get("/video/jobs/{job_id}", response_model=VideoJob)
async def get_video_job(job_id: str) -> VideoJob:
    try:
        return get_video_job_service().get(job_id)
    except MultimodalServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/video/jobs/{job_id}/refresh", response_model=VideoJob)
async def refresh_video_job(job_id: str) -> VideoJob:
    try:
        return await get_video_job_service().refresh(job_id)
    except MultimodalServiceError as exc:
        raise _http_error(exc) from exc


@router.get("/video/jobs/{job_id}/content")
async def get_video_job_content(
    job_id: str,
    index: int = 0,
) -> StreamingResponse:
    try:
        content = await get_video_job_service().content(
            job_id, index=index
        )
    except MultimodalServiceError as exc:
        raise _http_error(exc) from exc
    headers = {
        "Content-Disposition": (
            f'attachment; filename="modelmirror-video-{index + 1}.mp4"'
        ),
        "X-Content-Type-Options": "nosniff",
    }
    if content.content_length is not None:
        headers["Content-Length"] = str(content.content_length)
    return StreamingResponse(
        content.chunks,
        media_type=content.media_type,
        headers=headers,
    )


@router.delete(
    "/video/jobs/{job_id}",
    response_model=VideoJobDeleteResult,
)
async def delete_video_job(job_id: str) -> VideoJobDeleteResult:
    try:
        return get_video_job_service().delete(job_id)
    except MultimodalServiceError as exc:
        raise _http_error(exc) from exc


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


def _video_analysis_response(
    result: VideoAnalysisResult,
) -> VideoAnalysisResponse:
    return VideoAnalysisResponse(
        text=result.text,
        requested_model=result.requested_model,
        actual_model=result.actual_model,
        provider=result.provider,
        request_id=result.request_id,
        source_kind=result.source_kind,
        usage=VideoAnalysisUsageResponse(
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            total_tokens=result.usage.total_tokens,
            cost_usd=result.usage.cost_usd,
            cost_kind=result.usage.cost_kind,
        ),
    )


def _http_error(exc: MultimodalServiceError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )
