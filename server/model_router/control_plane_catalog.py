from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from .chat_canary import ProviderChatCanaryService
from .provider_catalog import PROVIDER_CATALOG_CONTRACT_VERSION
from .provider_chat import PROVIDER_CHAT_CONTRACT_VERSION
from .repository import RouterRepositoryError, utc_now
from .schemas import (
    ControlPlaneCatalogModel,
    ControlPlaneCatalogResponse,
    ControlPlaneOperationCount,
    OperationReadinessProjection,
    ProviderCatalogOfferingSummary,
    ProviderCatalogOfferingsResponse,
    ProviderCatalogPrice,
    ProviderControlPlaneOverview,
    RouterConnection,
)
from .service import ModelRouterService, RouterServiceError


_VERIFICATION_PRIORITY = {
    "not_applicable": 0,
    "failed": 1,
    "manual_required": 2,
    "verified": 3,
    "contract_verified": 4,
}
_INTERACTION_PRIORITY = {"disabled": 0, "planned": 1, "ready": 2}
_SUPPORTED_OPERATIONS = {
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


@dataclass
class _Evidence:
    operation: str
    interaction_status: str
    availability_status: str
    verification_status: str
    invocable: bool
    access_modes: set[str] = field(default_factory=set)
    reason_codes: set[str] = field(default_factory=set)
    observed_at: str | None = None
    stale: bool = False
    pricing: list[ProviderCatalogPrice] = field(default_factory=list)


@dataclass
class _ProjectedModel:
    model_id: str
    presences: set[str] = field(default_factory=set)
    operations: dict[str, list[_Evidence]] = field(default_factory=dict)

    def add(self, evidence: _Evidence) -> None:
        self.operations.setdefault(evidence.operation, []).append(evidence)


class ControlPlaneCatalogService:
    """Side-effect-free Catalog and readiness projection.

    All provider refreshes remain explicit. Optional catalog arguments must be
    snapshots obtained through ``peek_catalog`` and are never fetched here.
    """

    def __init__(
        self,
        router_service: ModelRouterService,
        *,
        general_catalog: object | None = None,
        audio_catalog: object | None = None,
        image_catalog: object | None = None,
        video_catalog: object | None = None,
    ) -> None:
        self.router_service = router_service
        self.repository = router_service.repository
        self.general_catalog = general_catalog
        self.audio_catalog = audio_catalog
        self.image_catalog = image_catalog
        self.video_catalog = video_catalog

    def public_catalog(
        self,
        *,
        model_id: str | None = None,
        operation: str | None = None,
        include_unavailable: bool = False,
        cursor: str | None = None,
        limit: int = 200,
    ) -> ControlPlaneCatalogResponse:
        projected, revision = self._build_projection()
        models = self._materialize(projected, operation=operation)
        self._add_canary_modes(models)
        if model_id is not None:
            models = [item for item in models if item.model_id == model_id]
        if not include_unavailable:
            models = [
                item
                for item in models
                if any(readiness.invocable for readiness in item.operations)
            ]
        offset = self._decode_cursor(cursor, revision)
        bounded_limit = max(1, min(int(limit), 500))
        catalog_stale = any(
            readiness.stale
            for item in models
            for readiness in item.operations
        )
        page = models[offset : offset + bounded_limit]
        next_offset = offset + len(page)
        next_cursor = (
            self._encode_cursor(revision, next_offset)
            if next_offset < len(models)
            else None
        )
        return ControlPlaneCatalogResponse(
            contract_version=PROVIDER_CATALOG_CONTRACT_VERSION,
            catalog_revision=revision,
            generated_at=utc_now(),
            stale=catalog_stale,
            next_cursor=next_cursor,
            models=page,
        )

    def offerings(
        self,
        *,
        connection_id: str | None = None,
        model_id: str | None = None,
        operation: str | None = None,
        status: str | None = None,
        cursor: str | None = None,
        limit: int = 200,
    ) -> ProviderCatalogOfferingsResponse:
        projected, revision = self._build_projection()
        del projected
        offset = self._decode_cursor(cursor, revision)
        bounded_limit = max(1, min(int(limit), 500))
        rows = self._all_catalog_offerings(
            connection_id=connection_id,
            model_id=model_id,
            operation=operation,
        )
        connections = {item.id: item for item in self.router_service.list_connections()}
        models = {
            (str(row["connection_id"]), str(row["model_id"])): row
            for row in self._all_catalog_models(
                connection_id=connection_id,
                model_id=model_id,
            )
        }
        refreshes = {
            str(row["id"]): row
            for row in self._catalog_refresh_rows(connections)
        }
        certifications = {
            (str(row["connection_id"]), str(row["requested_model"])): row
            for row in self.repository.list_latest_chat_certifications_by_model(
                self.router_service.tenant_id
            )
        }
        summaries: list[ProviderCatalogOfferingSummary] = []
        for row in rows:
            connection = connections.get(str(row["connection_id"]))
            inventory = models.get(
                (str(row["connection_id"]), str(row["model_id"]))
            )
            if connection is None or inventory is None:
                continue
            evidence = self._managed_evidence(
                row,
                inventory,
                connection,
                refreshes.get(str(row["last_refresh_id"])),
                certifications.get((connection.id, str(row["model_id"]))),
            )
            if status is not None:
                inventory_status = str(inventory["status"])
                if status != inventory_status and not (
                    status == "invocable" and evidence.invocable
                ):
                    continue
            summaries.append(
                ProviderCatalogOfferingSummary(
                    connection_id=connection.id,
                    connection_name=connection.name,
                    provider_kind=connection.kind,
                    model_id=str(row["model_id"]),
                    operation=str(row["operation"]),
                    access_mode=str(row["access_mode"]),
                    capability_source=str(row["capability_source"]),
                    inventory_status=str(inventory["status"]),
                    connection_health=connection.health,
                    verification_status=evidence.verification_status,
                    invocable=evidence.invocable,
                    reason_codes=sorted(evidence.reason_codes),
                    refresh_id=str(row["last_refresh_id"]),
                    observed_at=str(row["observed_at"]),
                    stale=evidence.stale,
                    pricing=(evidence.pricing[0] if evidence.pricing else None),
                )
            )
        summaries.sort(
            key=lambda item: (item.model_id, item.operation, item.connection_id)
        )
        page = summaries[offset : offset + bounded_limit]
        next_offset = offset + len(page)
        return ProviderCatalogOfferingsResponse(
            contract_version=PROVIDER_CATALOG_CONTRACT_VERSION,
            next_cursor=(
                self._encode_cursor(revision, next_offset)
                if next_offset < len(summaries)
                else None
            ),
            offerings=page,
        )

    def overview(self) -> ProviderControlPlaneOverview:
        projected, revision = self._build_projection()
        models = self._materialize(projected)
        self._add_canary_modes(models)
        connections = self.router_service.list_connections()
        counts: list[ControlPlaneOperationCount] = []
        operations = sorted(
            {
                readiness.operation
                for item in models
                for readiness in item.operations
            }
        )
        for operation in operations:
            rows = [
                readiness
                for item in models
                for readiness in item.operations
                if readiness.operation == operation
            ]
            counts.append(
                ControlPlaneOperationCount(
                    operation=operation,
                    total=len(rows),
                    invocable=sum(item.invocable for item in rows),
                    stale=sum(item.stale for item in rows),
                    blocked=sum(not item.invocable for item in rows),
                )
            )
        blockers: list[str] = []
        if not connections:
            blockers.append("provider_not_configured")
        if connections and not any(item.health == "online" for item in connections):
            blockers.append("provider_not_online")
        if not models:
            blockers.append("catalog_not_available")
        if any(
            readiness.stale
            for item in models
            for readiness in item.operations
        ):
            blockers.append("catalog_contains_stale_evidence")
        return ProviderControlPlaneOverview(
            contract_version=PROVIDER_CATALOG_CONTRACT_VERSION,
            catalog_revision=revision,
            generated_at=utc_now(),
            provider_count=len(connections),
            online_provider_count=sum(
                item.enabled and item.health == "online" for item in connections
            ),
            discovered_model_count=len(models),
            stale_model_count=sum(
                item.catalog_presence == "stale" for item in models
            ),
            operation_counts=counts,
            blocking_reason_codes=blockers,
        )

    def _build_projection(self) -> tuple[dict[str, _ProjectedModel], str]:
        tenant_id = self.router_service.tenant_id
        connections = {item.id: item for item in self.router_service.list_connections()}
        model_rows = self._all_catalog_models()
        offering_rows = self._all_catalog_offerings()
        refresh_rows = self._catalog_refresh_rows(connections)
        refreshes = {str(row["id"]): row for row in refresh_rows}
        certifications = {
            (str(row["connection_id"]), str(row["requested_model"])): row
            for row in self.repository.list_latest_chat_certifications_by_model(
                tenant_id
            )
        }
        projected: dict[str, _ProjectedModel] = {}
        inventory = {
            (str(row["connection_id"]), str(row["model_id"])): row
            for row in model_rows
        }
        for row in model_rows:
            model = projected.setdefault(
                str(row["model_id"]), _ProjectedModel(str(row["model_id"]))
            )
            model.presences.add(str(row["status"]))
        for row in offering_rows:
            connection = connections.get(str(row["connection_id"]))
            model_row = inventory.get(
                (str(row["connection_id"]), str(row["model_id"]))
            )
            if connection is None or model_row is None:
                continue
            model = projected.setdefault(
                str(row["model_id"]), _ProjectedModel(str(row["model_id"]))
            )
            model.add(
                self._managed_evidence(
                    row,
                    model_row,
                    connection,
                    refreshes.get(str(row["last_refresh_id"])),
                    certifications.get((connection.id, str(row["model_id"]))),
                )
            )
        self._add_general_snapshot(projected, self.general_catalog)
        for catalog in (self.audio_catalog, self.image_catalog, self.video_catalog):
            self._add_specialized_snapshot(projected, catalog)
        revision_material = {
            "refreshes": [
                [
                    row.get("id"),
                    row.get("status"),
                    row.get("catalog_fingerprint"),
                    row.get("completed_at"),
                ]
                for row in refresh_rows
            ],
            "snapshots": [
                [
                    getattr(catalog, "source", None),
                    getattr(catalog, "status", None)
                    or getattr(catalog, "router_status", None),
                    getattr(catalog, "synced_at", None),
                    getattr(catalog, "stale", None),
                ]
                for catalog in (
                    self.general_catalog,
                    self.audio_catalog,
                    self.image_catalog,
                    self.video_catalog,
                )
            ],
            "connections": [
                [
                    connection.id,
                    connection.kind,
                    connection.enabled,
                    connection.health,
                    connection.model_count,
                    connection.last_checked_at,
                    connection.updated_at,
                ]
                for connection in connections.values()
            ],
            "certifications": [
                [
                    row.get("id"),
                    row.get("connection_id"),
                    row.get("requested_model"),
                    row.get("connection_fingerprint"),
                    row.get("status"),
                    row.get("completed_at"),
                ]
                for row in certifications.values()
            ],
            "canary": self._canary_revision_material(),
        }
        revision = hashlib.sha256(
            json.dumps(
                revision_material,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return projected, revision

    def _all_catalog_models(
        self,
        *,
        connection_id: str | None = None,
        model_id: str | None = None,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        offset = 0
        while True:
            page = self.repository.list_catalog_models(
                self.router_service.tenant_id,
                connection_id=connection_id,
                model_id=model_id,
                limit=5_000,
                offset=offset,
            )
            rows.extend(page)
            if len(page) < 5_000:
                return rows
            offset += len(page)

    def _all_catalog_offerings(
        self,
        *,
        connection_id: str | None = None,
        model_id: str | None = None,
        operation: str | None = None,
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        offset = 0
        while True:
            page = self.repository.list_catalog_offerings(
                self.router_service.tenant_id,
                connection_id=connection_id,
                model_id=model_id,
                operation=operation,
                include_stale=True,
                limit=5_000,
                offset=offset,
            )
            rows.extend(page)
            if len(page) < 5_000:
                return rows
            offset += len(page)

    def _catalog_refresh_rows(
        self, connections: dict[str, RouterConnection]
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for connection_id in sorted(connections):
            rows.extend(
                self.repository.list_catalog_refreshes(
                    self.router_service.tenant_id,
                    connection_id=connection_id,
                    limit=500,
                )
            )
        return rows

    def _canary_revision_material(self) -> dict[str, object]:
        policy_reader = getattr(self.repository, "get_chat_canary_policy", None)
        run_reader = getattr(self.repository, "list_chat_canary_runs", None)
        policy = (
            policy_reader(self.router_service.tenant_id)
            if callable(policy_reader)
            else None
        )
        runs = (
            run_reader(self.router_service.tenant_id, limit=100)
            if callable(run_reader)
            else []
        )
        return {
            "feature_enabled": os.getenv(
                "MODEL_MIRROR_PROVIDER_CHAT_CANARY_ENABLED", "false"
            ),
            "policy": (
                [
                    policy.get("connection_id"),
                    policy.get("enabled"),
                    policy.get("updated_at"),
                ]
                if policy is not None
                else None
            ),
            "runs": [
                [
                    row.get("id"),
                    row.get("connection_id"),
                    row.get("requested_model"),
                    row.get("certification_id"),
                    row.get("status"),
                    row.get("result_class"),
                    row.get("error_code"),
                    row.get("completed_at"),
                ]
                for row in runs
            ],
        }

    def _managed_evidence(
        self,
        offering: dict[str, object],
        inventory: dict[str, object],
        connection: RouterConnection,
        refresh: dict[str, object] | None,
        certification: dict[str, object] | None,
    ) -> _Evidence:
        stale = bool(offering["stale"]) or inventory["status"] != "active"
        try:
            current_fingerprint = self.repository.connection_config_fingerprint(
                self.router_service.tenant_id, connection.id
            )
        except RouterRepositoryError:
            current_fingerprint = None
            stale = True
        if (
            refresh is None
            or current_fingerprint is None
            or refresh.get("connection_fingerprint") != current_fingerprint
        ):
            stale = True
        reasons: set[str] = set()
        if not connection.enabled:
            availability = "disabled"
            reasons.add("connection_disabled")
        elif connection.health == "offline":
            availability = "upstream_unavailable"
            reasons.add("connection_offline")
        elif connection.health == "untested":
            availability = "needs_configuration"
            reasons.add("connection_untested")
        elif stale:
            availability = "verification_required"
            reasons.add("catalog_stale")
        else:
            availability = "available"
            reasons.add("managed_catalog_present")
        verification = "manual_required"
        if str(offering["operation"]) == "chat" and certification is not None:
            certification_current = (
                certification.get("connection_fingerprint") == current_fingerprint
                and certification.get("contract_version")
                == PROVIDER_CHAT_CONTRACT_VERSION
            )
            status = str(certification.get("status"))
            if status == "passed" and certification_current:
                verification = "contract_verified"
                reasons.add("chat_contract_certified")
            elif status in {"failed", "uncertain"}:
                verification = "failed"
                reasons.add("chat_certification_failed")
            else:
                reasons.add("chat_certification_required")
        elif str(offering["operation"]) == "chat":
            reasons.add("chat_certification_required")
        invocable = (
            connection.enabled
            and connection.health == "online"
            and not stale
            and availability == "available"
        )
        price = self._price_from_row(offering, stale=stale)
        return _Evidence(
            operation=str(offering["operation"]),
            interaction_status="ready" if invocable else "planned",
            availability_status=availability,
            verification_status=verification,
            invocable=invocable,
            access_modes={"native"},
            reason_codes=reasons,
            observed_at=str(offering["observed_at"]),
            stale=stale,
            pricing=[price] if price is not None else [],
        )

    @staticmethod
    def _price_from_row(
        offering: dict[str, object], *, stale: bool
    ) -> ProviderCatalogPrice | None:
        raw = offering.get("pricing_json")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return None
            if stale:
                payload["status"] = "stale"
            payload["billing_authoritative"] = False
            return ProviderCatalogPrice.model_validate(payload)
        except (ValueError, TypeError):
            return None

    def _add_general_snapshot(
        self, projected: dict[str, _ProjectedModel], catalog: object | None
    ) -> None:
        if catalog is None:
            return
        source = str(getattr(catalog, "source", "bundled"))
        mode = "omniroute" if source == "omniroute" else (
            "native" if source == "native" else "default"
        )
        stale = bool(getattr(catalog, "stale", False))
        observed_at = getattr(catalog, "synced_at", None)
        for candidate in getattr(catalog, "models", []):
            model_id = str(getattr(candidate, "invocation_id", "")).strip()
            if not model_id:
                continue
            model = projected.setdefault(model_id, _ProjectedModel(model_id))
            model.presences.add("stale" if stale else "active")
            invocable = bool(getattr(candidate, "invocable", False)) and not stale
            raw_interaction = str(getattr(candidate, "interaction_status", "planned"))
            interaction = (
                "disabled" if raw_interaction == "unsupported" else raw_interaction
            )
            availability_value = str(getattr(candidate, "availability", "offline"))
            availability = {
                "live": "available",
                "degraded": "upstream_unavailable",
                "offline": "upstream_unavailable",
                "disabled": "disabled",
            }.get(availability_value, "verification_required")
            for operation in getattr(candidate, "operations", ["chat"]):
                model.add(
                    _Evidence(
                        operation=str(operation),
                        interaction_status=interaction,
                        availability_status=availability,
                        verification_status=("verified" if invocable else "not_applicable"),
                        invocable=invocable,
                        access_modes={mode},
                        reason_codes={
                            "general_catalog_available"
                            if invocable
                            else "general_catalog_unavailable"
                        },
                        observed_at=observed_at,
                        stale=stale,
                    )
                )

    def _add_specialized_snapshot(
        self, projected: dict[str, _ProjectedModel], catalog: object | None
    ) -> None:
        if catalog is None:
            return
        stale = bool(getattr(catalog, "stale", False))
        observed_at = getattr(catalog, "synced_at", None)
        for profile in getattr(catalog, "profiles", []):
            model_id = str(getattr(profile, "model_id", "")).strip()
            if not model_id:
                continue
            model = projected.setdefault(model_id, _ProjectedModel(model_id))
            model.presences.add("stale" if stale else "active")
            readiness_rows = list(getattr(profile, "operation_readiness", []))
            if not readiness_rows:
                raw_operations = getattr(profile, "operations", None)
                if not raw_operations:
                    raw_operation = getattr(profile, "operation", None)
                    raw_operations = [raw_operation] if raw_operation else []
                readiness_rows = [
                    type(
                        "Readiness",
                        (),
                        {
                            "operation": operation,
                            "interaction_status": getattr(
                                profile, "interaction_status", "planned"
                            ),
                            "availability_status": (
                                "available"
                                if bool(getattr(profile, "invocable", False))
                                else "verification_required"
                            ),
                            "verification_status": "manual_required",
                        },
                    )()
                    for operation in raw_operations
                ]
            for readiness in readiness_rows:
                operation = str(getattr(readiness, "operation", "")).strip()
                if operation not in _SUPPORTED_OPERATIONS:
                    # Specialized catalogs may expose provider-specific actions
                    # (for example voice cloning). Keep those in their native
                    # catalog until the public control-plane contract defines
                    # an operation for them.
                    continue
                interaction = str(
                    getattr(readiness, "interaction_status", "planned")
                )
                if interaction == "unsupported":
                    interaction = "disabled"
                availability = str(
                    getattr(readiness, "availability_status", "verification_required")
                )
                invocable = (
                    availability == "available"
                    and interaction == "ready"
                    and not stale
                )
                model.add(
                    _Evidence(
                        operation=operation,
                        interaction_status=interaction,
                        availability_status=availability,
                        verification_status=str(
                            getattr(readiness, "verification_status", "manual_required")
                        ),
                        invocable=invocable,
                        access_modes={"specialized"},
                        reason_codes={
                            "specialized_catalog_available"
                            if invocable
                            else "specialized_catalog_not_ready"
                        },
                        observed_at=observed_at,
                        stale=stale,
                        pricing=self._specialized_prices(profile, observed_at, stale),
                    )
                )

    @staticmethod
    def _specialized_prices(
        profile: object, observed_at: str | None, stale: bool
    ) -> list[ProviderCatalogPrice]:
        if observed_at is None:
            return []
        result: list[ProviderCatalogPrice] = []
        for item in getattr(profile, "pricing", []):
            cost = getattr(item, "cost_usd", None)
            if cost is None:
                continue
            result.append(
                ProviderCatalogPrice(
                    currency="USD",
                    unit=str(getattr(item, "unit", "unknown")),
                    input_price=None,
                    output_price=format(Decimal(str(cost)), "f"),
                    source="specialized_catalog",
                    observed_at=observed_at,
                    status="stale" if stale else "reported",
                    billing_authoritative=False,
                )
            )
        generation_price = getattr(profile, "price_per_generation_usd", None)
        if generation_price is not None:
            result.append(
                ProviderCatalogPrice(
                    currency="USD",
                    unit="generation",
                    output_price=format(Decimal(str(generation_price)), "f"),
                    source="specialized_catalog",
                    observed_at=observed_at,
                    status="stale" if stale else "reported",
                    billing_authoritative=False,
                )
            )
        return result

    def _materialize(
        self,
        projected: dict[str, _ProjectedModel],
        *,
        operation: str | None = None,
    ) -> list[ControlPlaneCatalogModel]:
        result: list[ControlPlaneCatalogModel] = []
        for model_id in sorted(projected):
            model = projected[model_id]
            readiness: list[OperationReadinessProjection] = []
            for operation_name in sorted(model.operations):
                if operation is not None and operation_name != operation:
                    continue
                evidence = model.operations[operation_name]
                invocable = any(item.invocable for item in evidence)
                interaction = max(
                    (item.interaction_status for item in evidence),
                    key=lambda value: _INTERACTION_PRIORITY.get(value, -1),
                )
                if invocable:
                    availability = "available"
                elif any(item.availability_status == "needs_configuration" for item in evidence):
                    availability = "needs_configuration"
                elif any(item.availability_status == "verification_required" for item in evidence):
                    availability = "verification_required"
                elif any(item.availability_status == "upstream_unavailable" for item in evidence):
                    availability = "upstream_unavailable"
                else:
                    availability = "disabled"
                verification = max(
                    (item.verification_status for item in evidence),
                    key=lambda value: _VERIFICATION_PRIORITY.get(value, -1),
                )
                prices = self._dedupe_prices(
                    price for item in evidence for price in item.pricing
                )
                if len(
                    {
                        (price.currency, price.unit, price.input_price, price.output_price)
                        for price in prices
                    }
                ) > 1:
                    prices = [
                        price.model_copy(update={"status": "ambiguous"})
                        for price in prices
                    ]
                readiness.append(
                    OperationReadinessProjection(
                        operation=operation_name,
                        interaction_status=interaction,
                        availability_status=availability,
                        verification_status=verification,
                        invocable=invocable,
                        access_modes=sorted(
                            {mode for item in evidence for mode in item.access_modes}
                        ),
                        reason_codes=sorted(
                            {reason for item in evidence for reason in item.reason_codes}
                        ),
                        observed_at=max(
                            (
                                item.observed_at
                                for item in evidence
                                if item.observed_at is not None
                            ),
                            default=None,
                        ),
                        stale=all(item.stale for item in evidence),
                        pricing=prices,
                    )
                )
            if operation is not None and not readiness:
                continue
            presence = (
                "present"
                if "active" in model.presences
                else "stale"
                if "stale" in model.presences
                else "retired"
                if "retired" in model.presences
                else "unknown"
            )
            result.append(
                ControlPlaneCatalogModel(
                    model_id=model_id,
                    catalog_presence=presence,
                    operations=readiness,
                )
            )
        return result

    def _add_canary_modes(self, models: list[ControlPlaneCatalogModel]) -> None:
        service = ProviderChatCanaryService(self.router_service)
        if not service.enabled():
            return
        status = service.admin_status(
            limit=1,
            default_gateway_url=os.getenv("LLM_GATEWAY_URL"),
        )
        if not status.policy_enabled:
            return
        available_models = {
            item.model_id
            for connection in status.connections
            for item in connection.models
            if item.available
        }
        for model in models:
            chat = next(
                (item for item in model.operations if item.operation == "chat"),
                None,
            )
            if chat is None or model.model_id not in available_models:
                continue
            chat.access_modes = sorted(
                set(chat.access_modes) | {"newapi_canary"}
            )
            chat.invocable = True
            chat.availability_status = "available"
            chat.reason_codes = sorted(
                set(chat.reason_codes) | {"newapi_canary_available"}
            )

    @staticmethod
    def _dedupe_prices(
        prices: Iterable[ProviderCatalogPrice],
    ) -> list[ProviderCatalogPrice]:
        result: list[ProviderCatalogPrice] = []
        seen: set[tuple[object, ...]] = set()
        for price in prices:
            key = (
                price.currency,
                price.unit,
                price.input_price,
                price.output_price,
                price.source,
                price.observed_at,
            )
            if key not in seen:
                seen.add(key)
                result.append(price)
        return result

    @staticmethod
    def _encode_cursor(revision: str, offset: int) -> str:
        payload = json.dumps(
            {"revision": revision, "offset": offset},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str | None, revision: str) -> int:
        if not cursor:
            return 0
        try:
            padding = "=" * (-len(cursor) % 4)
            payload = json.loads(
                base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
            )
            offset = int(payload["offset"])
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise RouterServiceError(
                "invalid_catalog_cursor",
                "Catalog cursor is invalid.",
                status_code=422,
            ) from exc
        if payload.get("revision") != revision:
            raise RouterServiceError(
                "catalog_cursor_stale",
                "Catalog changed while paging; restart from the first page.",
                status_code=409,
            )
        if offset < 0:
            raise RouterServiceError(
                "invalid_catalog_cursor",
                "Catalog cursor is invalid.",
                status_code=422,
            )
        return offset
