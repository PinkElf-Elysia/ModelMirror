from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from server.model_router.chat_control import ProviderChatControlService
from server.model_router.chat_stable import ProviderChatStableService
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderChatControlPolicyUpdate,
    ProviderChatControlRouteUpdate,
    RouterConnectionCreate,
    RouterConnectionUpdate,
)
from server.model_router.service import ModelRouterService, RouterServiceError


MODEL_ID = "provider/model"


def _qualified_connection(
    repository: SQLiteRouterRepository, *, name: str, kind: str
) -> str:
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name=name,
            kind=kind,
            base_url=f"https://{name.casefold()}.example/v1",
            api_key=f"{name}-secret",
            scopes=["chat"],
        ),
    )
    repository.save_test_result(
        "local",
        connection.id,
        health="online",
        model_count=1,
        checked_at="2026-08-21T00:00:00+00:00",
    )
    fingerprint = repository.connection_config_fingerprint("local", connection.id)
    refresh_id = f"refresh-{connection.id}"
    repository.claim_catalog_refresh(
        "local",
        refresh_id=refresh_id,
        connection_id=connection.id,
        connection_fingerprint=fingerprint,
    )
    repository.complete_catalog_refresh(
        "local",
        refresh_id,
        connection_id=connection.id,
        models=[
            {
                "model_id": MODEL_ID,
                "normalized_model_id": MODEL_ID,
                "capability_state": "declared",
            }
        ],
        offerings=[],
        model_count=1,
        truncated=False,
        catalog_fingerprint=f"catalog-{connection.id}",
        observed_at="2026-08-21T00:00:00+00:00",
    )
    certification, created = repository.claim_chat_certification(
        "local",
        certification_id=f"cert-{connection.id}",
        connection_id=connection.id,
        connection_fingerprint=fingerprint,
        contract_version="modelmirror-provider-chat-v1",
        capability="chat_text",
        requested_model=MODEL_ID,
        idempotency_key_hash=hashlib.sha256(connection.id.encode()).hexdigest(),
    )
    assert created is True
    repository.complete_chat_certification(
        "local",
        str(certification["id"]),
        status="passed",
        checks={"capability_verified": True},
        warning_codes=[],
        actual_model=MODEL_ID,
    )
    return connection.id


def _service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    service = ModelRouterService(
        repository,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda host, _port: (
                ["10.0.0.8"] if host.startswith("newapi") else ["8.8.8.8"]
            )
        ),
    )
    newapi_id = _qualified_connection(repository, name="newAPI", kind="newapi")
    backup_id = _qualified_connection(
        repository, name="OpenRouter", kind="openrouter"
    )
    ProviderChatControlService(service).update_policy(
        ProviderChatControlPolicyUpdate(
            expected_revision=0,
            mode="newapi_preferred",
            stable_model_ids=[MODEL_ID],
            routes=[
                ProviderChatControlRouteUpdate(
                    capability="chat_text",
                    connection_ids=[newapi_id, backup_id],
                )
            ],
        )
    )
    return ProviderChatStableService(service), repository, newapi_id, backup_id


@pytest.mark.asyncio
async def test_preferred_selects_backup_only_after_primary_preflight_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(tmp_path, monkeypatch)

    result = await service.begin(MODEL_ID)

    assert result.intercepted is True
    assert result.dispatch is not None
    assert result.dispatch.target.connection_id == backup_id
    assert result.dispatch.position == 1
    assert result.dispatch.reason_codes == (
        "provider_address_blocked",
        "provider_chat_preflight_backup_selected",
    )
    receipts = repository.list_chat_control_receipts("local")
    assert len(receipts["runs"]) == 1
    attempts = receipts["attempts"]
    assert [(item["connection_id"], item["dispatched"]) for item in attempts] == [
        (newapi_id, 0),
        (backup_id, 0),
    ]
    assert attempts[0]["error_code"] == "provider_address_blocked"


@pytest.mark.asyncio
async def test_dispatch_rechecks_policy_and_records_no_post_on_stale_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, _newapi_id, backup_id = _service(tmp_path, monkeypatch)
    result = await service.begin(MODEL_ID)
    assert result.dispatch is not None
    repository.update_connection(
        "local",
        backup_id,
        RouterConnectionUpdate(base_url="https://changed.example/v1"),
    )

    with pytest.raises(RouterServiceError) as exc_info:
        service.mark_dispatched(result.dispatch)

    assert exc_info.value.code == "provider_chat_policy_or_qualification_changed"
    receipts = repository.list_chat_control_receipts("local")
    selected = next(
        item for item in receipts["attempts"] if item["connection_id"] == backup_id
    )
    assert selected["dispatched"] == 0
    assert selected["status"] == "failed"


@pytest.mark.asyncio
async def test_dispatch_rechecks_feature_flag_before_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, _newapi_id, _backup_id = _service(tmp_path, monkeypatch)
    result = await service.begin(MODEL_ID)
    assert result.dispatch is not None
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "false")

    with pytest.raises(RouterServiceError) as exc_info:
        service.mark_dispatched(result.dispatch)

    assert exc_info.value.code == "provider_chat_policy_or_qualification_changed"
    receipts = repository.list_chat_control_receipts("local")
    selected = next(
        item
        for item in receipts["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )
    assert selected["dispatched"] == 0
    assert selected["status"] == "failed"


@pytest.mark.asyncio
async def test_dispatch_completion_is_tenant_scoped_and_stores_no_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, _newapi_id, _backup_id = _service(tmp_path, monkeypatch)
    result = await service.begin(MODEL_ID)
    assert result.dispatch is not None
    service.mark_dispatched(result.dispatch)
    service.complete(
        result.dispatch,
        status="succeeded",
        result_class="success",
        actual_model=MODEL_ID,
        ttft_ms=12.5,
        e2e_ms=42.0,
        total_tokens=3,
    )

    receipts = repository.list_chat_control_receipts("local")
    assert receipts["runs"][0]["status"] == "succeeded"
    assert receipts["attempts"][-1]["dispatched"] == 1
    assert repository.list_chat_control_receipts("other")["runs"] == []
    with sqlite3.connect(repository.database_path) as database:
        dump = "\n".join(database.iterdump())
    assert "private prompt" not in dump
    assert "model answer" not in dump


@pytest.mark.asyncio
async def test_feature_disabled_and_non_stable_models_use_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, _newapi_id, _backup_id = _service(tmp_path, monkeypatch)
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "false")
    original_get_bundle = repository.get_chat_control_policy_bundle
    repository.get_chat_control_policy_bundle = lambda _tenant: (_ for _ in ()).throw(
        AssertionError("disabled feature must not read the control policy")
    )
    disabled = await service.begin(MODEL_ID)
    repository.get_chat_control_policy_bundle = original_get_bundle
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    outside = await service.begin("provider/other")

    assert disabled.intercepted is False
    assert outside.intercepted is False
    assert repository.list_chat_control_receipts("local")["runs"] == []
