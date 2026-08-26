from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal, InvalidOperation
from typing import Iterable

from .egress import ProviderEgressError
from .repository import RouterRepositoryError, utc_now
from .schemas import (
    ProviderCatalogRefreshResponse,
    ProviderModelsRefreshResponse,
    RouterConnection,
)
from .service import ModelRouterService, RouterServiceError


PROVIDER_CATALOG_CONTRACT_VERSION = "modelmirror-provider-catalog-v1"
MAX_PROVIDER_CATALOG_MODELS = 5_000
MAX_MODEL_ID_LENGTH = 512
KNOWN_OPERATIONS = {
    "chat",
    "analyze_document",
    "analyze_image",
    "generate_image",
    "transcribe",
    "synthesize_speech",
    "generate_audio",
    "analyze_audio",
    "realtime_voice",
    "analyze_video",
    "generate_video",
    "generate_world",
    "embed",
    "rerank",
}


def _clean_model_id(value: object) -> str | None:
    model_id = str(value or "").strip()
    if not model_id or len(model_id) > MAX_MODEL_ID_LENGTH:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in model_id):
        return None
    return model_id


def _safe_scalar(value: object, *, limit: int = 512) -> object | None:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return value[:limit]
    return None


def _decimal_text(value: object) -> str | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed < 0:
        return None
    return format(parsed.normalize(), "f")


def _reported_pricing(record: dict[str, object], observed_at: str) -> dict[str, object] | None:
    pricing = record.get("pricing")
    if not isinstance(pricing, dict):
        return None
    currency = pricing.get("currency")
    unit = pricing.get("unit")
    if not isinstance(currency, str) or not currency.strip():
        return None
    if not isinstance(unit, str) or not unit.strip():
        return None
    input_price = _decimal_text(pricing.get("input", pricing.get("prompt")))
    output_price = _decimal_text(pricing.get("output", pricing.get("completion")))
    if input_price is None and output_price is None:
        return None
    return {
        "currency": currency.strip()[:16].upper(),
        "unit": unit.strip()[:64],
        "input_price": input_price,
        "output_price": output_price,
        "source": "provider_catalog",
        "observed_at": observed_at,
        "status": "reported",
        "billing_authoritative": False,
    }


def _declared_operations(record: dict[str, object]) -> set[str]:
    candidates = record.get("operations")
    capabilities = record.get("capabilities")
    if not isinstance(candidates, list) and isinstance(capabilities, dict):
        candidates = capabilities.get("operations")
    if not isinstance(candidates, list):
        return set()
    return {
        str(value).strip()
        for value in candidates
        if str(value).strip() in KNOWN_OPERATIONS
    }


def normalize_provider_catalog(
    records: Iterable[dict[str, object]],
    *,
    connection: RouterConnection,
    observed_at: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]], int, bool]:
    normalized: dict[str, dict[str, object]] = {}
    offerings: dict[tuple[str, str, str], dict[str, object]] = {}
    for record in records:
        model_id = _clean_model_id(record.get("id"))
        if model_id is None:
            continue
        declared = _declared_operations(record)
        operation_catalog = str(record.get("_catalog_operation") or "").strip()
        if operation_catalog in KNOWN_OPERATIONS:
            declared.add(operation_catalog)
        else:
            operation_catalog = ""
        metadata = {
            key: safe
            for key in ("object", "owned_by", "created", "name")
            if (safe := _safe_scalar(record.get(key))) is not None
        }
        if model_id not in normalized:
            normalized[model_id] = {
                "model_id": model_id,
                "normalized_model_id": model_id.casefold(),
                "metadata": metadata,
                "capability_state": (
                    "declared" if declared else "capabilities_unclassified"
                ),
            }
        elif declared:
            normalized[model_id]["capability_state"] = "declared"
        pricing = _reported_pricing(record, observed_at)
        operation_sources: dict[str, str] = {
            operation: "provider_declared" for operation in declared
        }
        if operation_catalog:
            operation_sources[operation_catalog] = "provider_operation_catalog"
        if "chat" in connection.scopes and not operation_catalog:
            operation_sources.setdefault("chat", "connection_scope")
        for operation, capability_source in operation_sources.items():
            key = (model_id, operation, "managed")
            offerings[key] = {
                "model_id": model_id,
                "operation": operation,
                "access_mode": "managed",
                "capability_source": capability_source,
                "pricing": pricing,
                "pricing_source": "provider_catalog" if pricing else None,
                "pricing_status": "reported" if pricing else "unknown",
                "pricing_observed_at": observed_at if pricing else None,
            }

    model_count = len(normalized)
    truncated = model_count > MAX_PROVIDER_CATALOG_MODELS
    kept_ids = sorted(normalized)[:MAX_PROVIDER_CATALOG_MODELS]
    kept_id_set = set(kept_ids)
    kept = [normalized[model_id] for model_id in kept_ids]
    kept_offerings = [
        offering
        for key, offering in sorted(offerings.items())
        if key[0] in kept_id_set
    ]
    return kept, kept_offerings, model_count, truncated


class ProviderCatalogService:
    def __init__(self, router_service: ModelRouterService) -> None:
        self.router_service = router_service
        self.repository = router_service.repository

    async def refresh_connection(
        self, connection_id: str
    ) -> ProviderCatalogRefreshResponse:
        connection = self.repository.get_connection(
            self.router_service.tenant_id, connection_id
        )
        if not connection.enabled:
            raise RouterServiceError(
                "connection_disabled",
                "该模型服务已停用。",
                status_code=409,
            )
        refresh_id = f"catalog_{uuid.uuid4().hex}"
        fingerprint = self.repository.connection_config_fingerprint(
            self.router_service.tenant_id, connection_id
        )
        try:
            self.repository.claim_catalog_refresh(
                self.router_service.tenant_id,
                refresh_id=refresh_id,
                connection_id=connection_id,
                connection_fingerprint=fingerprint,
            )
        except RouterRepositoryError as exc:
            if str(exc) == "provider_catalog_refresh_in_progress":
                raise RouterServiceError(
                    "provider_catalog_refresh_in_progress",
                    "该连接已有目录刷新正在运行。",
                    status_code=409,
                ) from exc
            raise

        try:
            result, records = await self.router_service.fetch_connection_model_records(
                connection_id,
                persist_result=False,
                require_chat_scope=False,
            )
            embedding_result = None
            if connection.kind == "openrouter" and "embedding" in connection.scopes:
                embedding_result, embedding_records = (
                    await self.router_service.fetch_connection_embedding_model_records(
                        connection_id
                    )
                )
                records.extend(
                    {
                        **record,
                        "_catalog_operation": "embed",
                    }
                    for record in embedding_records
                )
        except ProviderEgressError as exc:
            checked_at = utc_now()
            self.repository.fail_catalog_refresh(
                self.router_service.tenant_id,
                refresh_id,
                connection_id=connection_id,
                error_code=exc.code,
                health="offline",
                checked_at=checked_at,
                error_hint=exc.message,
            )
            raise
        except Exception:
            self._safe_fail(refresh_id, connection_id, "provider_catalog_request_failed")
            raise

        if not result.ok:
            error_code = self.router_service._result_error_code(result)
            self.repository.fail_catalog_refresh(
                self.router_service.tenant_id,
                refresh_id,
                connection_id=connection_id,
                error_code=error_code,
                health=result.health,
                model_count=result.model_count,
                checked_at=result.checked_at,
                error_hint=result.message,
            )
            return ProviderCatalogRefreshResponse(
                contract_version=PROVIDER_CATALOG_CONTRACT_VERSION,
                refresh_id=refresh_id,
                connection_id=connection_id,
                status="failed",
                model_count=0,
                checked_at=result.checked_at,
                error_code=error_code,
                message=result.message,
            )

        if embedding_result is not None and not embedding_result.ok:
            error_code = self.router_service._result_error_code(embedding_result)
            self.repository.fail_catalog_refresh(
                self.router_service.tenant_id,
                refresh_id,
                connection_id=connection_id,
                error_code=error_code,
                health=embedding_result.health,
                model_count=embedding_result.model_count,
                checked_at=embedding_result.checked_at,
                error_hint=embedding_result.message,
            )
            return ProviderCatalogRefreshResponse(
                contract_version=PROVIDER_CATALOG_CONTRACT_VERSION,
                refresh_id=refresh_id,
                connection_id=connection_id,
                status="failed",
                model_count=0,
                checked_at=embedding_result.checked_at,
                error_code=error_code,
                message=embedding_result.message,
            )

        models, offerings, model_count, truncated = normalize_provider_catalog(
            records,
            connection=connection,
            observed_at=result.checked_at,
        )
        if not models:
            self.repository.fail_catalog_refresh(
                self.router_service.tenant_id,
                refresh_id,
                connection_id=connection_id,
                error_code="incompatible_catalog",
                health="offline",
                model_count=0,
                checked_at=result.checked_at,
                error_hint="服务已连接，但模型目录没有可保存的规范模型 ID。",
            )
            return ProviderCatalogRefreshResponse(
                contract_version=PROVIDER_CATALOG_CONTRACT_VERSION,
                refresh_id=refresh_id,
                connection_id=connection_id,
                status="failed",
                model_count=0,
                checked_at=result.checked_at,
                error_code="incompatible_catalog",
                message="服务已连接，但模型目录没有可保存的规范模型 ID。",
            )

        fingerprint_payload = {
            "models": [item["model_id"] for item in models],
            "offerings": [
                [item["model_id"], item["operation"], item["access_mode"]]
                for item in offerings
            ],
            "truncated": truncated,
        }
        catalog_fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        row = self.repository.complete_catalog_refresh(
            self.router_service.tenant_id,
            refresh_id,
            connection_id=connection_id,
            models=models,
            offerings=offerings,
            model_count=model_count,
            truncated=truncated,
            catalog_fingerprint=catalog_fingerprint,
            observed_at=result.checked_at,
        )
        return ProviderCatalogRefreshResponse(
            contract_version=PROVIDER_CATALOG_CONTRACT_VERSION,
            refresh_id=refresh_id,
            connection_id=connection_id,
            status="succeeded",
            model_ids=[str(item["model_id"]) for item in models[:500]],
            model_count=model_count,
            checked_at=result.checked_at,
            truncated=truncated,
            catalog_fingerprint=str(row["catalog_fingerprint"]),
            message=(
                f"目录刷新成功，保存 {len(models)} 个模型。"
                + ("目录已截断，未退休未再次出现的旧模型。" if truncated else "")
            ),
        )

    async def refresh_connection_legacy(
        self, connection_id: str
    ) -> ProviderModelsRefreshResponse:
        refreshed = await self.refresh_connection(connection_id)
        return ProviderModelsRefreshResponse(
            connection_id=connection_id,
            ok=refreshed.status == "succeeded",
            model_ids=refreshed.model_ids,
            model_count=refreshed.model_count,
            checked_at=refreshed.checked_at,
            truncated=refreshed.truncated or refreshed.model_count > 500,
            message=refreshed.message,
        )

    def _safe_fail(
        self, refresh_id: str, connection_id: str, error_code: str
    ) -> None:
        try:
            self.repository.fail_catalog_refresh(
                self.router_service.tenant_id,
                refresh_id,
                connection_id=connection_id,
                error_code=error_code,
            )
        except RouterRepositoryError:
            return
