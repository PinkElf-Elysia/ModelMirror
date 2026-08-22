from __future__ import annotations

import os
import threading
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status

try:
    from server.omniroute.catalog import OmniRouteCatalogService
    from server.omniroute.config import get_omniroute_settings
    from server.omniroute.schemas import ModelCatalogResponse, RouterStatusResponse
except ModuleNotFoundError:
    from omniroute.catalog import OmniRouteCatalogService
    from omniroute.config import get_omniroute_settings
    from omniroute.schemas import ModelCatalogResponse, RouterStatusResponse

from .repository import RouterRepositoryError
from .egress import ProviderEgressError
from .admin_auth import (
    AdminPairingRequest,
    AdminSessionResponse,
    ProviderControlPrincipal,
    get_provider_admin_auth,
    require_provider_admin,
    require_provider_admin_csrf,
)
from .schemas import (
    ConnectionTestResult,
    ProviderChatCertificationListResponse,
    ProviderChatCertificationRequest,
    ProviderChatCertificationSummary,
    ProviderChatCapability,
    ProviderChatControlGateResponse,
    ProviderChatControlPolicyResponse,
    ProviderChatControlPolicyUpdate,
    ProviderChatControlPublicStatus,
    ProviderChatControlReceiptsResponse,
    ProviderChatCanaryAdminResponse,
    ProviderChatCanaryPolicyUpdate,
    ProviderChatCanaryPublicStatus,
    ProviderCatalogRefreshResponse,
    ProviderCatalogOfferingsResponse,
    ProviderControlPlaneOverview,
    ControlPlaneCatalogResponse,
    OperationName,
    ProviderModelsRefreshResponse,
    RouterConnection,
    RouterConnectionCreate,
    RouterConnectionUpdate,
    RouterGateApprovalRequest,
    RouterPolicy,
    RouterStatus,
)
from .chat_certification import ProviderChatCertificationService
from .chat_control import ProviderChatControlService
from .chat_canary import ProviderChatCanaryService
from .provider_catalog import ProviderCatalogService
from .control_plane_catalog import ControlPlaneCatalogService
from .service import (
    ModelRouterService,
    RouterServiceError,
    translate_repository_error,
)


router = APIRouter(prefix="/api/router", tags=["model-router"])
models_router = APIRouter(prefix="/api/models", tags=["model-catalog"])
_service: ModelRouterService | None = None
_service_lock = threading.Lock()
_catalog_coordinator: object | None = None
_native_engine: object | None = None


def configure_model_router(service: ModelRouterService) -> None:
    global _service, _native_engine, _catalog_coordinator
    with _service_lock:
        _service = service
        _native_engine = None
        _catalog_coordinator = None


def get_model_router_service() -> ModelRouterService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = ModelRouterService()
    return _service


def get_catalog_coordinator():
    global _catalog_coordinator
    if _catalog_coordinator is None:
        from .catalog import CatalogCoordinator

        _catalog_coordinator = CatalogCoordinator(
            OmniRouteCatalogService(get_omniroute_settings)
        )
    return _catalog_coordinator


def get_native_router_engine():
    global _native_engine
    if _native_engine is None:
        from .engine import NativeRouterEngine

        _native_engine = NativeRouterEngine(get_model_router_service())
    return _native_engine


def get_control_plane_catalog_service() -> ControlPlaneCatalogService:
    try:
        from server.multimodal.api import (
            get_audio_catalog_service,
            get_image_catalog_service,
            get_video_catalog_service,
        )
    except ModuleNotFoundError:
        from multimodal.api import (
            get_audio_catalog_service,
            get_image_catalog_service,
            get_video_catalog_service,
        )
    return ControlPlaneCatalogService(
        get_model_router_service(),
        general_catalog=get_catalog_coordinator().peek_catalog(),
        audio_catalog=get_audio_catalog_service().peek_catalog(),
        image_catalog=get_image_catalog_service().peek_catalog(),
        video_catalog=get_video_catalog_service().peek_catalog(),
    )


def _raise_public_error(exc: Exception) -> None:
    public = (
        exc
        if isinstance(exc, RouterServiceError)
        else translate_repository_error(exc)
    )
    raise HTTPException(
        status_code=public.status_code,
        detail={"code": public.code, "message": public.hint},
    ) from exc


@router.post("/admin/session", response_model=AdminSessionResponse)
def pair_admin_session(
    payload: AdminPairingRequest,
    request: Request,
    response: Response,
) -> AdminSessionResponse:
    return get_provider_admin_auth().pair(
        request,
        response,
        payload.pairing_secret.get_secret_value(),
    )


@router.get("/admin/session", response_model=AdminSessionResponse)
def get_admin_session(request: Request) -> AdminSessionResponse:
    return get_provider_admin_auth().status(request)


@router.delete(
    "/admin/session",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_admin_session(request: Request) -> Response:
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    get_provider_admin_auth().logout(request, response)
    return response


@router.get("/connections", response_model=list[RouterConnection])
def list_connections(
    _principal: ProviderControlPrincipal = Depends(require_provider_admin),
) -> list[RouterConnection]:
    try:
        return get_model_router_service().list_connections()
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.post(
    "/connections",
    response_model=RouterConnection,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    payload: RouterConnectionCreate,
    _principal: ProviderControlPrincipal = Depends(require_provider_admin_csrf),
) -> RouterConnection:
    try:
        return await get_model_router_service().create_connection(payload)
    except (ProviderEgressError, RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.patch("/connections/{connection_id}", response_model=RouterConnection)
async def update_connection(
    connection_id: str,
    payload: RouterConnectionUpdate,
    _principal: ProviderControlPrincipal = Depends(require_provider_admin_csrf),
) -> RouterConnection:
    try:
        return await get_model_router_service().update_connection(connection_id, payload)
    except (ProviderEgressError, RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.post(
    "/connections/test",
    response_model=ConnectionTestResult,
)
async def test_unsaved_connection(
    payload: RouterConnectionCreate,
    _principal: ProviderControlPrincipal = Depends(require_provider_admin_csrf),
) -> ConnectionTestResult:
    try:
        return await get_model_router_service().test_unsaved_connection(payload)
    except (ProviderEgressError, RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.post(
    "/connections/{connection_id}/test",
    response_model=ConnectionTestResult,
)
async def test_saved_connection(
    connection_id: str,
    _principal: ProviderControlPrincipal = Depends(require_provider_admin_csrf),
) -> ConnectionTestResult:
    try:
        return await get_model_router_service().test_saved_connection(connection_id)
    except (ProviderEgressError, RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.post(
    "/connections/{connection_id}/catalog/refresh",
    response_model=ProviderCatalogRefreshResponse,
)
async def refresh_provider_catalog(
    connection_id: str,
    _principal: ProviderControlPrincipal = Depends(require_provider_admin_csrf),
) -> ProviderCatalogRefreshResponse:
    try:
        return await ProviderCatalogService(
            get_model_router_service()
        ).refresh_connection(connection_id)
    except (ProviderEgressError, RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.post(
    "/connections/{connection_id}/models/refresh",
    response_model=ProviderModelsRefreshResponse,
)
async def refresh_connection_models(
    connection_id: str,
    _principal: ProviderControlPrincipal = Depends(require_provider_admin_csrf),
) -> ProviderModelsRefreshResponse:
    try:
        return await ProviderChatCertificationService(
            get_model_router_service()
        ).refresh_models(connection_id)
    except (ProviderEgressError, RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.get(
    "/certifications/chat",
    response_model=ProviderChatCertificationListResponse,
)
def list_chat_certifications(
    _principal: ProviderControlPrincipal = Depends(require_provider_admin),
) -> ProviderChatCertificationListResponse:
    try:
        return ProviderChatCertificationService(get_model_router_service()).list()
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.post(
    "/connections/{connection_id}/certifications/chat",
    response_model=ProviderChatCertificationSummary,
)
async def run_chat_certification(
    connection_id: str,
    payload: ProviderChatCertificationRequest,
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    _principal: ProviderControlPrincipal = Depends(require_provider_admin_csrf),
) -> ProviderChatCertificationSummary:
    try:
        return await ProviderChatCertificationService(get_model_router_service()).run(
            connection_id,
            model_id=payload.model_id,
            capability=payload.capability,
            acknowledge_billed_call=payload.acknowledge_billed_call,
            idempotency_key=idempotency_key,
        )
    except (ProviderEgressError, RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.get(
    "/chat-control/policy",
    response_model=ProviderChatControlPolicyResponse,
)
def get_chat_control_policy(
    _principal: ProviderControlPrincipal = Depends(require_provider_admin),
) -> ProviderChatControlPolicyResponse:
    try:
        return ProviderChatControlService(get_model_router_service()).get_policy()
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.put(
    "/chat-control/policy",
    response_model=ProviderChatControlPolicyResponse,
)
def update_chat_control_policy(
    payload: ProviderChatControlPolicyUpdate,
    _principal: ProviderControlPrincipal = Depends(require_provider_admin_csrf),
) -> ProviderChatControlPolicyResponse:
    try:
        return ProviderChatControlService(
            get_model_router_service()
        ).update_policy(payload)
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.get(
    "/chat-control/gate",
    response_model=ProviderChatControlGateResponse,
)
def get_chat_control_gate(
    _principal: ProviderControlPrincipal = Depends(require_provider_admin),
) -> ProviderChatControlGateResponse:
    try:
        return ProviderChatControlService(get_model_router_service()).gate()
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.get(
    "/chat-control/receipts",
    response_model=ProviderChatControlReceiptsResponse,
)
def get_chat_control_receipts(
    limit: int = 50,
    cursor: str | None = None,
    _principal: ProviderControlPrincipal = Depends(require_provider_admin),
) -> ProviderChatControlReceiptsResponse:
    try:
        return ProviderChatControlService(get_model_router_service()).receipts(
            limit=max(1, min(limit, 100)),
            cursor=cursor,
        )
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.get(
    "/canaries/chat",
    response_model=ProviderChatCanaryAdminResponse,
)
def get_chat_canary_admin_status(
    limit: int = 50,
    _principal: ProviderControlPrincipal = Depends(require_provider_admin),
) -> ProviderChatCanaryAdminResponse:
    try:
        return ProviderChatCanaryService(
            get_model_router_service()
        ).admin_status(
            limit=max(1, min(limit, 100)),
            default_gateway_url=os.getenv("LLM_GATEWAY_URL"),
        )
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.put(
    "/canaries/chat",
    response_model=ProviderChatCanaryAdminResponse,
)
def update_chat_canary_policy(
    payload: ProviderChatCanaryPolicyUpdate,
    _principal: ProviderControlPrincipal = Depends(require_provider_admin_csrf),
) -> ProviderChatCanaryAdminResponse:
    try:
        return ProviderChatCanaryService(
            get_model_router_service()
        ).update_policy(
            payload.connection_id,
            enabled=payload.enabled,
            default_gateway_url=os.getenv("LLM_GATEWAY_URL"),
        )
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.get("/policy", response_model=RouterPolicy)
def get_policy(
    _principal: ProviderControlPrincipal = Depends(require_provider_admin),
) -> RouterPolicy:
    try:
        return get_model_router_service().get_policy()
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.put("/policy", response_model=RouterPolicy)
def save_policy(
    policy: RouterPolicy,
    _principal: ProviderControlPrincipal = Depends(require_provider_admin_csrf),
) -> RouterPolicy:
    try:
        return get_model_router_service().save_policy(policy)
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.get("/status", response_model=RouterStatus)
def get_status(
    _principal: ProviderControlPrincipal = Depends(require_provider_admin),
) -> RouterStatus:
    try:
        return get_model_router_service().status()
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.get(
    "/control-plane/overview",
    response_model=ProviderControlPlaneOverview,
)
def get_control_plane_overview(
    _principal: ProviderControlPrincipal = Depends(require_provider_admin),
) -> ProviderControlPlaneOverview:
    try:
        return get_control_plane_catalog_service().overview()
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.get(
    "/catalog/offerings",
    response_model=ProviderCatalogOfferingsResponse,
)
def get_provider_catalog_offerings(
    connection_id: str | None = None,
    model_id: str | None = None,
    operation: OperationName | None = None,
    status_filter: Literal["active", "stale", "retired", "invocable"] | None = Query(
        default=None, alias="status"
    ),
    cursor: str | None = None,
    limit: int = 200,
    _principal: ProviderControlPrincipal = Depends(require_provider_admin),
) -> ProviderCatalogOfferingsResponse:
    try:
        return get_control_plane_catalog_service().offerings(
            connection_id=connection_id,
            model_id=model_id,
            operation=operation,
            status=status_filter,
            cursor=cursor,
            limit=limit,
        )
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.get("/diagnostics")
def get_diagnostics(
    _principal: ProviderControlPrincipal = Depends(require_provider_admin),
) -> dict[str, object]:
    try:
        return get_model_router_service().diagnostics()
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.put("/gate/approval")
def approve_native_gate(
    payload: RouterGateApprovalRequest,
    _principal: ProviderControlPrincipal = Depends(require_provider_admin_csrf),
) -> dict[str, object]:
    try:
        return get_model_router_service().approve_native_gate(
            no_open_p0_p1=payload.no_open_p0_p1,
            drills=payload.drills,
        )
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.delete("/gate/approval")
def revoke_native_gate(
    _principal: ProviderControlPrincipal = Depends(require_provider_admin_csrf),
) -> dict[str, object]:
    try:
        return get_model_router_service().revoke_native_gate()
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@models_router.get("/catalog", response_model=ModelCatalogResponse)
async def get_model_catalog() -> ModelCatalogResponse:
    return await get_catalog_coordinator().get_catalog()


@models_router.get("/router-status", response_model=RouterStatusResponse)
async def get_public_router_status() -> RouterStatusResponse:
    return await get_catalog_coordinator().get_status()


@models_router.get(
    "/control-plane-catalog",
    response_model=ControlPlaneCatalogResponse,
)
def get_public_control_plane_catalog(
    response: Response,
    model_id: str | None = None,
    operation: OperationName | None = None,
    include_unavailable: bool = False,
    cursor: str | None = None,
    limit: int = 200,
) -> ControlPlaneCatalogResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return get_control_plane_catalog_service().public_catalog(
            model_id=model_id,
            operation=operation,
            include_unavailable=include_unavailable,
            cursor=cursor,
            limit=max(1, min(limit, 500)),
        )
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@models_router.get(
    "/provider-chat-canary",
    response_model=ProviderChatCanaryPublicStatus,
)
def get_public_chat_canary_status(
    model_id: str,
    response: Response,
) -> ProviderChatCanaryPublicStatus:
    response.headers["Cache-Control"] = "no-store"
    try:
        return ProviderChatCanaryService(
            get_model_router_service()
        ).public_status(
            model_id,
            default_gateway_url=os.getenv("LLM_GATEWAY_URL"),
        )
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@models_router.get(
    "/provider-chat-control",
    response_model=ProviderChatControlPublicStatus,
)
def get_public_chat_control_status(
    model_id: str,
    response: Response,
    capability: ProviderChatCapability = "chat_text",
) -> ProviderChatControlPublicStatus:
    response.headers["Cache-Control"] = "no-store"
    try:
        return ProviderChatControlService(
            get_model_router_service()
        ).public_status(model_id, capability)
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)
