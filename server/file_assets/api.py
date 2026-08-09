from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    Response,
    UploadFile,
)
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response as StarletteResponse
from starlette.types import Message

from .contracts import (
    FileAssetListResponse,
    FileAssetResponse,
    FileCapabilitiesResponse,
    FileInteractionStatus,
    FilePurpose,
)
from .document_parser import ParsedDocumentPreview
from .registry import get_file_format_registry
from .service import (
    FileAssetService,
    FileAssetServiceError,
    get_file_asset_service,
)

try:
    from server.model_router.api import get_catalog_coordinator
except ModuleNotFoundError:
    from model_router.api import get_catalog_coordinator


MIB = 1024 * 1024
MAX_FILE_UPLOAD_BYTES = 50 * MIB
MAX_MULTIPART_OVERHEAD_BYTES = 1 * MIB
MAX_FILE_UPLOAD_REQUEST_BYTES = (
    MAX_FILE_UPLOAD_BYTES + MAX_MULTIPART_OVERHEAD_BYTES
)


class FileUploadSizeLimitRoute(APIRoute):
    """Bound multipart bytes before Starlette creates or fills upload files."""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Awaitable[StarletteResponse]]:
        original_handler = super().get_route_handler()
        is_upload_route = self.path.rstrip("/") == "/api/files" and bool(
            self.methods and "POST" in self.methods
        )

        async def limited_route_handler(request: Request) -> StarletteResponse:
            if not is_upload_route:
                return await original_handler(request)

            declared_length = _content_length(request)
            if (
                declared_length is not None
                and declared_length > MAX_FILE_UPLOAD_REQUEST_BYTES
            ):
                raise _request_too_large()

            received_bytes = 0
            request_limit_exceeded = False
            upstream_receive = request.receive

            async def limited_receive() -> Message:
                nonlocal received_bytes, request_limit_exceeded
                message = await upstream_receive()
                if message["type"] == "http.request":
                    received_bytes += len(message.get("body", b""))
                    if received_bytes > MAX_FILE_UPLOAD_REQUEST_BYTES:
                        request_limit_exceeded = True
                        # Starlette closes any multipart temporary files only for
                        # MultiPartException. The wrapper translates it to our
                        # stable 413 after FastAPI unwinds the parser.
                        raise MultiPartException("file_request_too_large")
                return message

            limited_request = Request(request.scope, receive=limited_receive)
            try:
                return await original_handler(limited_request)
            except StarletteHTTPException:
                if request_limit_exceeded:
                    raise _request_too_large()
                raise

        return limited_route_handler


router = APIRouter(
    prefix="/api/files",
    tags=["file-assets"],
    route_class=FileUploadSizeLimitRoute,
)


class ChatFileConfirmationRequest(BaseModel):
    handling: Literal["native", "extract"]


class ChatFileConfirmationResponse(BaseModel):
    asset_id: str
    handling: Literal["native", "extract"]
    confirmation_revision: int = Field(ge=1)
    confirmed_at: str


@router.get("/capabilities", response_model=FileCapabilitiesResponse)
async def get_file_capabilities(
    purpose: Annotated[FilePurpose | None, Query()] = None,
    model_id: Annotated[str | None, Query(min_length=1, max_length=256)] = None,
) -> FileCapabilitiesResponse:
    """Return capabilities; native PDF requires a live catalog and OpenRouter."""

    verified_native_pdf: bool | None = None
    if purpose == FilePurpose.CHAT and model_id is not None:
        verified_native_pdf = await _verified_native_pdf(model_id)

    response = get_file_format_registry().capabilities_response(
        purpose=purpose,
        model_id=model_id,
        verified_native_pdf=verified_native_pdf,
    )
    if purpose in {None, FilePurpose.AGENT}:
        response = response.model_copy(
            update={
                "capabilities": tuple(
                    capability.model_copy(
                        update={
                            "interaction_status": FileInteractionStatus.DISABLED,
                            "status_reason": (
                                "Agent 现有文件入口仍使用 Xpert 会话存储；"
                                "统一文件资产 binding 尚未接通。"
                            ),
                            "handling_options": (),
                        }
                    )
                    if capability.purpose == FilePurpose.AGENT
                    else capability
                    for capability in response.capabilities
                )
            }
        )
    return response


async def _verified_native_pdf(model_id: str) -> bool:
    if not _active_chat_url_is_openrouter():
        return False
    try:
        catalog = await get_catalog_coordinator().get_catalog()
    except Exception:
        return False
    if (
        catalog.router_status != "online"
        or catalog.stale
        or catalog.source == "bundled"
    ):
        return False
    return any(
        candidate.invocation_id == model_id
        and candidate.invocable
        and candidate.availability == "live"
        and "file" in candidate.input_modalities
        and "text" in candidate.output_modalities
        and "analyze_document" in candidate.operations
        for candidate in catalog.models
    )


def _active_chat_url_is_openrouter() -> bool:
    llm_url = os.getenv("LLM_GATEWAY_URL", "").strip()
    llm_key = os.getenv("LLM_GATEWAY_KEY", "").strip()
    if llm_url and llm_key:
        active_url = llm_url
    elif os.getenv("OPENROUTER_API_KEY", "").strip():
        active_url = os.getenv(
            "OPENROUTER_CHAT_COMPLETIONS_URL",
            "https://openrouter.ai/api/v1/chat/completions",
        )
    else:
        return False
    return (
        active_url.strip().lower().rstrip("/")
        == "https://openrouter.ai/api/v1/chat/completions"
    )


@router.post("", response_model=FileAssetResponse, status_code=201)
async def upload_file_asset(
    purpose: Annotated[FilePurpose, Form()],
    scope_id: Annotated[
        str,
        Form(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9._:-]+$"),
    ],
    file: Annotated[UploadFile, File()],
    service: Annotated[FileAssetService, Depends(get_file_asset_service)],
) -> FileAssetResponse:
    try:
        return await asyncio.to_thread(
            service.upload,
            file.file,
            purpose=purpose,
            scope_id=scope_id,
            filename=file.filename or "",
            declared_media_type=file.content_type,
        )
    except FileAssetServiceError as exc:
        raise _http_error(exc) from exc


@router.get("", response_model=FileAssetListResponse)
def list_file_assets(
    purpose: Annotated[FilePurpose, Query()],
    scope_id: Annotated[
        str,
        Query(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9._:-]+$"),
    ],
    service: Annotated[FileAssetService, Depends(get_file_asset_service)],
) -> FileAssetListResponse:
    try:
        return service.list_assets(purpose=purpose, scope_id=scope_id)
    except FileAssetServiceError as exc:
        raise _http_error(exc) from exc


@router.delete(
    "/scopes/{scope_id}",
    status_code=204,
    responses={202: {"description": "会话绑定已移除，物理清理将在后台重试"}},
)
def delete_file_scope(
    scope_id: Annotated[
        str,
        Path(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9._:-]+$"),
    ],
    purpose: Annotated[FilePurpose, Query()],
    service: Annotated[FileAssetService, Depends(get_file_asset_service)],
) -> Response:
    try:
        cleanup_pending = service.delete_scope(
            purpose=purpose,
            scope_id=scope_id,
        )
    except FileAssetServiceError as exc:
        raise _http_error(exc) from exc
    if cleanup_pending:
        return JSONResponse(
            status_code=202,
            content={
                "status": "cleanup_pending",
                "message": "会话文件绑定已移除，物理清理暂未完成并将在后续操作中重试。",
            },
        )
    return Response(status_code=204)


@router.get("/{asset_id}", response_model=FileAssetResponse)
def get_file_asset(
    asset_id: Annotated[str, Path(min_length=1, max_length=256)],
    purpose: Annotated[FilePurpose, Query()],
    scope_id: Annotated[
        str,
        Query(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9._:-]+$"),
    ],
    service: Annotated[FileAssetService, Depends(get_file_asset_service)],
) -> FileAssetResponse:
    try:
        return service.get_asset(asset_id, purpose=purpose, scope_id=scope_id)
    except FileAssetServiceError as exc:
        raise _http_error(exc) from exc


@router.get("/{asset_id}/preview", response_model=ParsedDocumentPreview)
def preview_file_asset(
    asset_id: Annotated[str, Path(min_length=1, max_length=256)],
    purpose: Annotated[FilePurpose, Query()],
    scope_id: Annotated[
        str,
        Query(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9._:-]+$"),
    ],
    service: Annotated[FileAssetService, Depends(get_file_asset_service)],
) -> ParsedDocumentPreview:
    try:
        return service.preview_asset(
            asset_id, purpose=purpose, scope_id=scope_id
        )
    except FileAssetServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/{asset_id}/parse", response_model=ParsedDocumentPreview)
async def parse_file_asset(
    asset_id: Annotated[str, Path(min_length=1, max_length=256)],
    purpose: Annotated[FilePurpose, Query()],
    scope_id: Annotated[
        str,
        Query(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9._:-]+$"),
    ],
    service: Annotated[FileAssetService, Depends(get_file_asset_service)],
) -> ParsedDocumentPreview:
    try:
        return await asyncio.to_thread(
            service.parse_asset,
            asset_id,
            purpose=purpose,
            scope_id=scope_id,
        )
    except FileAssetServiceError as exc:
        raise _http_error(exc) from exc


@router.post(
    "/{asset_id}/confirm",
    response_model=ChatFileConfirmationResponse,
)
def confirm_chat_file_asset(
    asset_id: Annotated[str, Path(min_length=1, max_length=256)],
    payload: ChatFileConfirmationRequest,
    purpose: Annotated[FilePurpose, Query()],
    scope_id: Annotated[
        str,
        Query(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9._:-]+$"),
    ],
    service: Annotated[FileAssetService, Depends(get_file_asset_service)],
) -> ChatFileConfirmationResponse:
    if purpose != FilePurpose.CHAT:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "file_confirmation_not_supported",
                "message": "本批次仅支持确认用于当前聊天轮次的文件。",
            },
        )
    try:
        revision, confirmed_at = service.confirm_chat_input(
            asset_id,
            scope_id=scope_id,
            handling=payload.handling,
        )
    except FileAssetServiceError as exc:
        raise _http_error(exc) from exc
    return ChatFileConfirmationResponse(
        asset_id=asset_id,
        handling=payload.handling,
        confirmation_revision=revision,
        confirmed_at=confirmed_at,
    )


@router.delete(
    "/{asset_id}",
    status_code=204,
    responses={202: {"description": "绑定已移除，物理清理将在后台重试"}},
)
def delete_file_asset(
    asset_id: Annotated[str, Path(min_length=1, max_length=256)],
    purpose: Annotated[FilePurpose, Query()],
    scope_id: Annotated[
        str,
        Query(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9._:-]+$"),
    ],
    service: Annotated[FileAssetService, Depends(get_file_asset_service)],
) -> Response:
    try:
        cleanup_pending = service.delete_asset(
            asset_id, purpose=purpose, scope_id=scope_id
        )
    except FileAssetServiceError as exc:
        raise _http_error(exc) from exc
    if cleanup_pending:
        return JSONResponse(
            status_code=202,
            content={
                "status": "cleanup_pending",
                "message": "文件绑定已移除，物理清理暂未完成并将在后续操作中重试。",
            },
        )
    return Response(status_code=204)


def _http_error(exc: FileAssetServiceError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.error_code, "message": exc.message},
    )


def _content_length(request: Request) -> int | None:
    values = [
        value.strip()
        for key, value in request.scope.get("headers", ())
        if key.lower() == b"content-length"
    ]
    if not values:
        return None
    if len(values) != 1:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_content_length",
                "message": "上传请求的 Content-Length 无效。",
            },
        )
    try:
        parsed = int(values[0])
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_content_length",
                "message": "上传请求的 Content-Length 无效。",
            },
        ) from exc
    if parsed < 0:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_content_length",
                "message": "上传请求的 Content-Length 无效。",
            },
        )
    return parsed


def _request_too_large() -> HTTPException:
    return HTTPException(
        status_code=413,
        detail={
            "code": "file_request_too_large",
            "message": "上传请求超过单文件 50 MiB 加协议开销的安全上限。",
        },
    )
