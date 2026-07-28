from __future__ import annotations

from fastapi import APIRouter

from .catalog import OmniRouteCatalogService
from .config import get_omniroute_settings
from .schemas import ModelCatalogResponse, RouterStatusResponse

router = APIRouter(prefix="/api/models", tags=["omniroute"])
catalog_service = OmniRouteCatalogService(get_omniroute_settings)


@router.get("/catalog", response_model=ModelCatalogResponse)
async def get_model_catalog() -> ModelCatalogResponse:
    return await catalog_service.get_catalog()


@router.get("/router-status", response_model=RouterStatusResponse)
async def get_router_status() -> RouterStatusResponse:
    return await catalog_service.get_status()
