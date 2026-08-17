from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

try:
    from server.omniroute.catalog import OmniRouteCatalogService
    from server.omniroute.config import get_omniroute_settings
    from server.omniroute.schemas import ModelCatalogResponse, RouterStatusResponse
except ModuleNotFoundError:
    from omniroute.catalog import OmniRouteCatalogService
    from omniroute.config import get_omniroute_settings
    from omniroute.schemas import ModelCatalogResponse, RouterStatusResponse

from .repository import RouterRepositoryError
from .schemas import (
    ConnectionTestResult,
    RouterConnection,
    RouterConnectionCreate,
    RouterConnectionUpdate,
    RouterGateApprovalRequest,
    RouterPolicy,
    RouterStatus,
)
from .service import (
    ModelRouterService,
    RouterServiceError,
    translate_repository_error,
)


router = APIRouter(prefix="/api/router", tags=["model-router"])
models_router = APIRouter(prefix="/api/models", tags=["model-catalog"])
_service: ModelRouterService | None = None
_catalog_coordinator: object | None = None
_native_engine: object | None = None


def configure_model_router(service: ModelRouterService) -> None:
    global _service, _native_engine, _catalog_coordinator
    _service = service
    _native_engine = None
    _catalog_coordinator = None


def get_model_router_service() -> ModelRouterService:
    global _service
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


@router.get("/connections", response_model=list[RouterConnection])
def list_connections() -> list[RouterConnection]:
    try:
        return get_model_router_service().list_connections()
    except RouterRepositoryError as exc:
        _raise_public_error(exc)


@router.post(
    "/connections",
    response_model=RouterConnection,
    status_code=status.HTTP_201_CREATED,
)
def create_connection(payload: RouterConnectionCreate) -> RouterConnection:
    try:
        return get_model_router_service().create_connection(payload)
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.patch("/connections/{connection_id}", response_model=RouterConnection)
def update_connection(
    connection_id: str, payload: RouterConnectionUpdate
) -> RouterConnection:
    try:
        return get_model_router_service().update_connection(connection_id, payload)
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.post(
    "/connections/test",
    response_model=ConnectionTestResult,
)
async def test_unsaved_connection(
    payload: RouterConnectionCreate,
) -> ConnectionTestResult:
    try:
        return await get_model_router_service().test_unsaved_connection(payload)
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.post(
    "/connections/{connection_id}/test",
    response_model=ConnectionTestResult,
)
async def test_saved_connection(connection_id: str) -> ConnectionTestResult:
    try:
        return await get_model_router_service().test_saved_connection(connection_id)
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.get("/policy", response_model=RouterPolicy)
def get_policy() -> RouterPolicy:
    try:
        return get_model_router_service().get_policy()
    except RouterRepositoryError as exc:
        _raise_public_error(exc)


@router.put("/policy", response_model=RouterPolicy)
def save_policy(policy: RouterPolicy) -> RouterPolicy:
    try:
        return get_model_router_service().save_policy(policy)
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.get("/status", response_model=RouterStatus)
def get_status() -> RouterStatus:
    try:
        return get_model_router_service().status()
    except RouterRepositoryError as exc:
        _raise_public_error(exc)


@router.get("/diagnostics")
def get_diagnostics() -> dict[str, object]:
    try:
        return get_model_router_service().diagnostics()
    except RouterRepositoryError as exc:
        _raise_public_error(exc)


@router.put("/gate/approval")
def approve_native_gate(
    payload: RouterGateApprovalRequest,
) -> dict[str, object]:
    try:
        return get_model_router_service().approve_native_gate(
            no_open_p0_p1=payload.no_open_p0_p1,
            drills=payload.drills,
        )
    except (RouterServiceError, RouterRepositoryError) as exc:
        _raise_public_error(exc)


@router.delete("/gate/approval")
def revoke_native_gate() -> dict[str, object]:
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
