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
async def test_openrouter_audio_scope_merges_modality_filtered_catalogs(
    tmp_path: Path,
) -> None:
    def handler(request: Request) -> Response:
        modality = request.url.params.get("output_modalities")
        if modality == "transcription":
            return Response(
                200,
                json={"data": [{"id": "openai/whisper-large-v3"}]},
            )
        if modality == "speech":
            return Response(
                200,
                json={"data": [{"id": "openai/gpt-4o-mini-tts-2025-12-15"}]},
            )
        return Response(200, json={"data": [{"id": "provider/chat-model"}]})

    service, repository, connection_id, requests = _service(
        tmp_path,
        handler,
        scopes=["chat", "audio"],
        kind="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )

    result = await service.refresh_connection(connection_id)

    assert result.status == "succeeded"
    assert result.model_count == 3
    assert [request.url.params.get("output_modalities") for request in requests] == [
        None,
        "transcription",
        "speech",
    ]
    offerings = repository.list_catalog_offerings("local")
    assert {
        (row["model_id"], row["operation"], row["capability_source"])
        for row in offerings
    } == {
        ("provider/chat-model", "chat", "connection_scope"),
        (
            "openai/whisper-large-v3",
            "transcribe",
            "provider_operation_catalog",
        ),
        (
            "openai/gpt-4o-mini-tts-2025-12-15",
            "synthesize_speech",
            "provider_operation_catalog",
        ),
    }


@pytest.mark.asyncio
async def test_openrouter_empty_audio_filters_do_not_mark_connection_offline(
    tmp_path: Path,
) -> None:
    def handler(request: Request) -> Response:
        if request.url.params.get("output_modalities") is not None:
            return Response(200, json={"data": []})
        return Response(200, json={"data": [{"id": "provider/chat-model"}]})

    service, repository, connection_id, requests = _service(
        tmp_path,
        handler,
        scopes=["chat", "audio"],
        kind="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )

    result = await service.refresh_connection(connection_id)

    assert result.status == "succeeded"
    assert result.model_count == 1
    assert len(requests) == 3
    assert repository.get_connection("local", connection_id).health == "online"
    assert {
        (row["model_id"], row["operation"])
        for row in repository.list_catalog_offerings("local")
    } == {("provider/chat-model", "chat")}


@pytest.mark.asyncio
async def test_openrouter_audio_supplement_failure_keeps_previous_catalog_atomic(
    tmp_path: Path,
) -> None:
    phase = "initial"

    def handler(request: Request) -> Response:
        modality = request.url.params.get("output_modalities")
        if phase == "failed" and modality == "speech":
            return Response(503)
        suffix = "initial" if phase == "initial" else "partial"
        if modality == "transcription":
            return Response(200, json={"data": [{"id": f"audio/stt-{suffix}"}]})
        if modality == "speech":
            return Response(200, json={"data": [{"id": f"audio/tts-{suffix}"}]})
        return Response(200, json={"data": [{"id": f"chat/{suffix}"}]})

    service, repository, connection_id, _requests = _service(
        tmp_path,
        handler,
        scopes=["chat", "audio"],
        kind="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )
    first = await service.refresh_connection(connection_id)
    assert first.status == "succeeded"
    phase = "failed"

    second = await service.refresh_connection(connection_id)

    assert second.status == "failed"
    rows = repository.list_catalog_models("local", connection_id=connection_id)
    assert {row["model_id"] for row in rows} == {
        "chat/initial",
        "audio/stt-initial",
        "audio/tts-initial",
    }
    assert {row["status"] for row in rows} == {"active"}
    offerings = repository.list_catalog_offerings(
        "local", connection_id=connection_id
    )
    assert {row["model_id"] for row in offerings} == {
        "chat/initial",
        "audio/stt-initial",
        "audio/tts-initial",
    }
    assert {row["stale"] for row in offerings} == {0}
    connection = repository.get_connection("local", connection_id)
    assert connection.health == "online"
    assert connection.model_count == 3


@pytest.mark.asyncio
async def test_refresh_connection_change_does_not_persist_or_overwrite_health(
    tmp_path: Path,
) -> None:
    state: dict[str, object] = {"changed": False}

    def handler(request: Request) -> Response:
        repository = state["repository"]
        connection_id = str(state["connection_id"])
        if not state["changed"]:
            state["changed"] = True
            assert isinstance(repository, SQLiteRouterRepository)
            repository.update_connection(
                "local",
                connection_id,
                payload=RouterConnectionUpdate(
                    base_url="https://changed.example/v1",
                    api_key="changed-secret",
                ),
            )
        return Response(200, json={"data": [{"id": "transient/model"}]})

    service, repository, connection_id, requests = _service(tmp_path, handler)
    state.update(repository=repository, connection_id=connection_id)

    result = await service.refresh_connection(connection_id)

    assert result.status == "failed"
    assert result.error_code == "provider_catalog_connection_changed"
    assert len(requests) == 1
    assert repository.list_catalog_models("local", connection_id=connection_id) == []
    connection = repository.get_connection("local", connection_id)
    assert connection.base_url == "https://changed.example/v1"
    assert connection.health != "online"
    refresh = repository.list_catalog_refreshes(
        "local", connection_id=connection_id
    )[0]
    assert refresh["status"] == "failed"
    assert refresh["error_code"] == "provider_catalog_connection_changed"


@pytest.mark.asyncio
async def test_refresh_rejects_snapshot_drift_before_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, connection_id, requests = _service(
        tmp_path,
        lambda _request: Response(200, json={"data": [{"id": "model"}]}),
    )
    original_claim = repository.claim_catalog_refresh

    def claim_after_rotation(tenant_id: str, **kwargs):
        repository.update_connection(
            "local",
            connection_id,
            payload=RouterConnectionUpdate(
                base_url="https://rotated.example/v1",
                api_key="rotated-secret",
            ),
        )
        return original_claim(tenant_id, **kwargs)

    monkeypatch.setattr(repository, "claim_catalog_refresh", claim_after_rotation)

    with pytest.raises(RouterServiceError) as exc_info:
        await service.refresh_connection(connection_id)

    assert exc_info.value.code == "provider_catalog_connection_changed"
    assert requests == []
    assert repository.list_catalog_refreshes(
        "local", connection_id=connection_id
    ) == []


@pytest.mark.asyncio
async def test_audio_supplement_egress_failure_preserves_previous_catalog(
    tmp_path: Path,
) -> None:
    phase = "initial"
    failed_resolutions = 0

    def resolver(_host: str, _port: int) -> list[str]:
        nonlocal failed_resolutions
        if phase != "failed":
            return ["8.8.8.8"]
        failed_resolutions += 1
        return ["8.8.8.8"] if failed_resolutions == 1 else ["198.18.0.1"]

    def handler(request: Request) -> Response:
        modality = request.url.params.get("output_modalities")
        suffix = "initial" if phase == "initial" else "partial"
        if modality == "transcription":
            return Response(200, json={"data": [{"id": f"audio/stt-{suffix}"}]})
        if modality == "speech":
            return Response(200, json={"data": [{"id": f"audio/tts-{suffix}"}]})
        return Response(200, json={"data": [{"id": f"chat/{suffix}"}]})

    service, repository, connection_id, requests = _service(
        tmp_path,
        handler,
        scopes=["chat", "audio"],
        kind="openrouter",
        base_url="https://openrouter.ai/api/v1",
        resolver=resolver,
    )
    assert (await service.refresh_connection(connection_id)).status == "succeeded"
    initial_request_count = len(requests)
    phase = "failed"

    with pytest.raises(ProviderEgressError) as exc_info:
        await service.refresh_connection(connection_id)

    assert exc_info.value.code == "provider_address_blocked"
    assert len(requests) == initial_request_count + 1
    rows = repository.list_catalog_models("local", connection_id=connection_id)
    assert {row["model_id"] for row in rows} == {
        "chat/initial",
        "audio/stt-initial",
        "audio/tts-initial",
    }
    assert {row["status"] for row in rows} == {"active"}
    offerings = repository.list_catalog_offerings(
        "local", connection_id=connection_id
    )
    assert {row["stale"] for row in offerings} == {0}
    saved_connection = repository.get_connection("local", connection_id)
    assert saved_connection.health == "online"
    assert saved_connection.model_count == 3


@pytest.mark.asyncio
async def test_audio_supplement_exception_preserves_previous_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: Request) -> Response:
        modality = request.url.params.get("output_modalities")
        if modality == "transcription":
            return Response(200, json={"data": [{"id": "audio/stt"}]})
        if modality == "speech":
            return Response(200, json={"data": [{"id": "audio/tts"}]})
        return Response(200, json={"data": [{"id": "chat/model"}]})

    service, repository, connection_id, requests = _service(
        tmp_path,
        handler,
        scopes=["chat", "audio"],
        kind="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )
    assert (await service.refresh_connection(connection_id)).status == "succeeded"
    initial_request_count = len(requests)

    async def fail_audio_records(*_args, **_kwargs):
        raise RuntimeError("supplement parser failed")

    monkeypatch.setattr(
        service.router_service,
        "fetch_connection_audio_model_records",
        fail_audio_records,
    )

    with pytest.raises(RuntimeError, match="supplement parser failed"):
        await service.refresh_connection(connection_id)

    assert len(requests) == initial_request_count + 1
    rows = repository.list_catalog_models("local", connection_id=connection_id)
    assert {row["model_id"] for row in rows} == {
        "chat/model",
        "audio/stt",
        "audio/tts",
    }
    assert {row["status"] for row in rows} == {"active"}
    offerings = repository.list_catalog_offerings(
        "local", connection_id=connection_id
    )
    assert {row["stale"] for row in offerings} == {0}
    saved_connection = repository.get_connection("local", connection_id)
    assert saved_connection.health == "online"
    assert saved_connection.model_count == 3


@pytest.mark.asyncio
async def test_supplement_failure_stops_before_later_audio_requests(
    tmp_path: Path,
) -> None:
    phase = "initial"

    def handler(request: Request) -> Response:
        if request.url.path.endswith("/embeddings/models"):
            if phase == "failed":
                return Response(503)
            return Response(200, json={"data": [{"id": "embed/model"}]})
        modality = request.url.params.get("output_modalities")
        if modality is not None:
            return Response(200, json={"data": [{"id": f"audio/{modality}"}]})
        return Response(200, json={"data": [{"id": "chat/model"}]})

    service, repository, connection_id, requests = _service(
        tmp_path,
        handler,
        scopes=["chat", "embedding", "audio"],
        kind="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )
    assert (await service.refresh_connection(connection_id)).status == "succeeded"
    initial_request_count = len(requests)
    phase = "failed"

    result = await service.refresh_connection(connection_id)

    assert result.status == "failed"
    assert result.error_code == "unreachable"
    second_requests = requests[initial_request_count:]
    assert [request.url.path for request in second_requests] == [
        "/api/v1/models",
        "/api/v1/embeddings/models",
    ]
    assert all(
        request.url.params.get("output_modalities") is None
        for request in second_requests
    )
    rows = repository.list_catalog_models("local", connection_id=connection_id)
    assert {row["status"] for row in rows} == {"active"}
    assert repository.get_connection("local", connection_id).health == "online"


@pytest.mark.asyncio
async def test_empty_audio_supplements_retire_previous_audio_models(
    tmp_path: Path,
) -> None:
    phase = "initial"

    def handler(request: Request) -> Response:
        modality = request.url.params.get("output_modalities")
        if modality is None:
            return Response(200, json={"data": [{"id": "chat/model"}]})
        if phase == "empty":
            return Response(200, json={"data": []})
        return Response(200, json={"data": [{"id": f"audio/{modality}"}]})

    service, repository, connection_id, _requests = _service(
        tmp_path,
        handler,
        scopes=["chat", "audio"],
        kind="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )
    assert (await service.refresh_connection(connection_id)).status == "succeeded"
    phase = "empty"

    result = await service.refresh_connection(connection_id)

    assert result.status == "succeeded"
    rows = repository.list_catalog_models("local", connection_id=connection_id)
    assert {(row["model_id"], row["status"]) for row in rows} == {
        ("chat/model", "active"),
        ("audio/transcription", "retired"),
        ("audio/speech", "retired"),
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
