from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from httpx import MockTransport, Request, Response

from server.model_router.egress import ProviderEgressError, ProviderEgressPolicy
from server.model_router.provider_catalog import (
    ProviderCatalogService,
    normalize_provider_catalog,
)
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import RouterConnectionCreate, RouterConnectionUpdate
from server.model_router.service import ModelRouterService, RouterServiceError


def _service(
    tmp_path: Path,
    handler,
    *,
    scopes: list[str] | None = None,
    kind: str = "newapi",
    base_url: str = "https://newapi.example/v1",
    resolver=lambda _host, _port: ["8.8.8.8"],
) -> tuple[ProviderCatalogService, SQLiteRouterRepository, str, list[Request]]:
    requests: list[Request] = []

    def recording_handler(request: Request) -> Response:
        requests.append(request)
        return handler(request)

    repository = SQLiteRouterRepository(tmp_path)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="newAPI",
            kind=kind,
            base_url=base_url,
            api_key="catalog-secret",
            scopes=scopes or ["chat"],
        ),
    )
    router_service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(recording_handler),
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=ProviderEgressPolicy(resolver=resolver),
    )
    return ProviderCatalogService(router_service), repository, connection.id, requests


@pytest.mark.asyncio
async def test_refresh_persists_normalized_inventory_without_paid_call(
    tmp_path: Path,
) -> None:
    def handler(request: Request) -> Response:
        return Response(
            200,
            json={
                "data": [
                    {"id": "provider/model", "owned_by": "provider"},
                    {"id": "provider/model"},
                    {"id": "provider/other", "name": "Other"},
                ]
            },
        )

    service, repository, connection_id, requests = _service(tmp_path, handler)

    result = await service.refresh_connection(connection_id)

    assert result.status == "succeeded"
    assert result.model_count == 2
    assert [request.method for request in requests] == ["GET"]
    assert requests[0].url.host == "8.8.8.8"
    rows = repository.list_catalog_models("local", connection_id=connection_id)
    assert [row["model_id"] for row in rows] == [
        "provider/model",
        "provider/other",
    ]
    assert "catalog-secret" not in json.dumps(rows)
    connection = repository.get_connection("local", connection_id)
    assert connection.health == "online"
    assert connection.model_count == 2


@pytest.mark.asyncio
async def test_openrouter_embedding_scope_merges_dedicated_model_catalog(
    tmp_path: Path,
) -> None:
    def handler(request: Request) -> Response:
        if request.url.path == "/api/v1/embeddings/models":
            return Response(
                200,
                json={"data": [{"id": "openai/text-embedding-3-small"}]},
            )
        return Response(200, json={"data": [{"id": "provider/chat-model"}]})

    service, repository, connection_id, requests = _service(
        tmp_path,
        handler,
        scopes=["chat", "embedding"],
        kind="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )

    result = await service.refresh_connection(connection_id)

    assert result.status == "succeeded"
    assert result.model_count == 2
    assert [request.url.path for request in requests] == [
        "/api/v1/models",
        "/api/v1/embeddings/models",
    ]
    offerings = repository.list_catalog_offerings("local")
    assert {
        (row["model_id"], row["operation"], row["capability_source"])
        for row in offerings
    } == {
        ("provider/chat-model", "chat", "connection_scope"),
        (
            "openai/text-embedding-3-small",
            "embed",
            "provider_operation_catalog",
        ),
    }


@pytest.mark.asyncio
async def test_non_chat_connection_can_refresh_without_inferred_chat_offering(
    tmp_path: Path,
) -> None:
    def handler(request: Request) -> Response:
        return Response(
            200,
            json={
                "data": [
                    {"id": "provider/audio", "operations": ["synthesize_speech"]},
                    {"id": "provider/unclassified"},
                ]
            },
        )

    service, repository, connection_id, requests = _service(
        tmp_path,
        handler,
        scopes=["audio"],
    )

    result = await service.refresh_connection(connection_id)

    assert result.status == "succeeded"
    assert [request.method for request in requests] == ["GET"]
    assert {
        row["model_id"] for row in repository.list_catalog_models("local")
    } == {"provider/audio", "provider/unclassified"}
    offerings = repository.list_catalog_offerings("local")
    assert [(row["model_id"], row["operation"]) for row in offerings] == [
        ("provider/audio", "synthesize_speech")
    ]


@pytest.mark.asyncio
async def test_failed_refresh_keeps_previous_inventory_stale(tmp_path: Path) -> None:
    responses = iter(
        [
            Response(200, json={"data": [{"id": "provider/model"}]}),
            Response(503),
        ]
    )
    service, repository, connection_id, _requests = _service(
        tmp_path, lambda _request: next(responses)
    )

    assert (await service.refresh_connection(connection_id)).status == "succeeded"
    failed = await service.refresh_connection(connection_id)

    assert failed.status == "failed"
    assert failed.error_code == "unreachable"
    rows = repository.list_catalog_models("local", connection_id=connection_id)
    assert rows[0]["status"] == "stale"
    connection = repository.get_connection("local", connection_id)
    assert connection.health == "offline"
    assert connection.last_error_code == "unreachable"


@pytest.mark.asyncio
async def test_blocked_fake_ip_records_stable_failure_and_offline_health(
    tmp_path: Path,
) -> None:
    service, repository, connection_id, requests = _service(
        tmp_path,
        lambda _request: Response(200, json={"data": []}),
        resolver=lambda _host, _port: ["198.18.0.1"],
    )

    with pytest.raises(ProviderEgressError) as exc_info:
        await service.refresh_connection(connection_id)

    assert exc_info.value.code == "provider_address_blocked"
    assert requests == []
    refresh = repository.list_catalog_refreshes(
        "local", connection_id=connection_id
    )[0]
    assert refresh["status"] == "failed"
    assert refresh["error_code"] == "provider_address_blocked"
    connection = repository.get_connection("local", connection_id)
    assert connection.health == "offline"
    assert connection.last_error_code == "provider_address_blocked"


@pytest.mark.asyncio
async def test_disabled_connection_fails_before_network(tmp_path: Path) -> None:
    service, repository, connection_id, requests = _service(
        tmp_path,
        lambda _request: Response(200, json={"data": [{"id": "model"}]}),
    )
    repository.update_connection(
        "local",
        connection_id,
        payload=RouterConnectionUpdate(enabled=False),
    )

    with pytest.raises(RouterServiceError, match="已停用"):
        await service.refresh_connection(connection_id)
    assert requests == []


def test_normalization_does_not_infer_capabilities_or_implicit_pricing(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="provider",
            kind="newapi",
            base_url="https://provider.example/v1",
            api_key="secret",
            scopes=["chat"],
        ),
    )

    models, offerings, count, truncated = normalize_provider_catalog(
        [
            {
                "id": "image-looking-name",
                "pricing": {"input": "0.1", "output": "0.2"},
            }
        ],
        connection=connection,
        observed_at="2026-08-21T00:00:00+00:00",
    )

    assert count == 1 and truncated is False
    assert models[0]["capability_state"] == "capabilities_unclassified"
    assert offerings[0]["operation"] == "chat"
    assert offerings[0]["capability_source"] == "connection_scope"
    assert offerings[0]["pricing"] is None


def test_explicit_price_requires_currency_and_unit(tmp_path: Path) -> None:
    repository = SQLiteRouterRepository(tmp_path)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="provider",
            kind="newapi",
            base_url="https://provider.example/v1",
            api_key="secret",
            scopes=["chat"],
        ),
    )
    _models, offerings, _count, _truncated = normalize_provider_catalog(
        [
            {
                "id": "priced-model",
                "pricing": {
                    "currency": "usd",
                    "unit": "per_token",
                    "input": "0.000001",
                    "output": "0.000002",
                },
            }
        ],
        connection=connection,
        observed_at="2026-08-21T00:00:00+00:00",
    )
    assert offerings[0]["pricing"] == {
        "currency": "USD",
        "unit": "per_token",
        "input_price": "0.000001",
        "output_price": "0.000002",
        "source": "provider_catalog",
        "observed_at": "2026-08-21T00:00:00+00:00",
        "status": "reported",
        "billing_authoritative": False,
    }
