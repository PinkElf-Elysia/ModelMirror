from __future__ import annotations

import hashlib
import json
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


def _service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    newapi_ip: str = "10.0.0.8",
):
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    service = ModelRouterService(
        repository,
        egress_policy=ProviderEgressPolicy(
            resolver=lambda host, _port: (
                [newapi_ip] if host.startswith("newapi") else ["8.8.8.8"]
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


def _activate_required_for_test(repository: SQLiteRouterRepository) -> None:
    policy = repository.get_chat_control_policy_bundle("local")["policy"]
    assert policy is not None
    epoch = repository.get_open_chat_control_gate_epoch(
        "local", str(policy["policy_fingerprint"])
    )
    assert epoch is not None
    drills = {
        "auth_failure": True,
        "http_429": True,
        "http_5xx": True,
        "connect_timeout": True,
        "read_timeout": True,
        "empty_stream": True,
        "invalid_sse": True,
        "stream_interrupted": True,
        "service_restart": True,
        "credential_invalid": True,
        "data_plane_offline": True,
        "preferred_fallback": True,
    }
    now = "2026-08-22T00:00:00+00:00"
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            """
            UPDATE provider_chat_stable_policies
            SET mode = 'newapi_required_default', revision = revision + 1
            WHERE tenant_id = 'local'
            """
        )
        connection.execute(
            """
            UPDATE provider_chat_gate_epochs SET status = 'active'
            WHERE tenant_id = 'local' AND id = ?
            """,
            (epoch["id"],),
        )
        connection.execute(
            """
            INSERT INTO provider_chat_gate_approvals (
                tenant_id, policy_fingerprint, epoch_id, no_open_p0_p1,
                drills_json, acknowledge_fail_closed, approved_at
            ) VALUES ('local', ?, ?, 1, ?, 1, ?)
            """,
            (
                policy["policy_fingerprint"],
                epoch["id"],
                json.dumps(drills),
                now,
            ),
        )
        for evidence_kind in (
            "newapi_quota_decrement",
            "newapi_usage_log",
            "newapi_restart_persistence",
        ):
            connection.execute(
                """
                INSERT INTO provider_chat_acceptance_evidence (
                    id, tenant_id, policy_fingerprint, epoch_id,
                    evidence_kind, correlation_hash, passed,
                    reason_codes_json, observed_at
                ) VALUES (?, 'local', ?, ?, ?, ?, 1, '[]', ?)
                """,
                (
                    f"evidence-{evidence_kind}",
                    policy["policy_fingerprint"],
                    epoch["id"],
                    evidence_kind,
                    hashlib.sha256(b"correlation").hexdigest(),
                    now,
                ),
            )


def test_readiness_checks_current_qualification_without_creating_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(tmp_path, monkeypatch)
    receipt_count = len(repository.list_chat_control_receipts("local"))

    assert service.readiness(MODEL_ID, "chat_text") == (True, None)
    assert len(repository.list_chat_control_receipts("local")) == receipt_count

    for connection_id in (newapi_id, backup_id):
        repository.save_test_result(
            "local",
            connection_id,
            health="offline",
            model_count=1,
            checked_at="2026-08-21T01:00:00+00:00",
        )
    ready, reason = service.readiness(MODEL_ID, "chat_text")

    assert ready is False
    assert reason == "provider_connection_not_online"
    assert len(repository.list_chat_control_receipts("local")) == receipt_count


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


@pytest.mark.asyncio
async def test_required_preflight_failure_never_selects_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(tmp_path, monkeypatch)
    _activate_required_for_test(repository)

    result = await service.begin(MODEL_ID)

    assert result.intercepted is True
    assert result.dispatch is None
    assert result.error_code == "provider_address_blocked"
    assert result.route_receipt is not None
    assert result.route_receipt["strategy"] == "newapi_required_default"
    receipts = repository.list_chat_control_receipts("local")
    assert [item["connection_id"] for item in receipts["attempts"]] == [newapi_id]
    assert backup_id not in {item["connection_id"] for item in receipts["attempts"]}


@pytest.mark.asyncio
async def test_required_hard_failure_stays_required_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(
        tmp_path,
        monkeypatch,
        newapi_ip="8.8.8.8",
    )
    _activate_required_for_test(repository)
    first = await service.begin(MODEL_ID)
    assert first.dispatch is not None
    assert first.dispatch.target.connection_id == newapi_id
    assert first.dispatch.strategy == "newapi_required_default"
    service.mark_dispatched(first.dispatch)
    service.complete(
        first.dispatch,
        status="failed",
        result_class="hard_failure",
        error_code="provider_chat_empty_stream",
        hard_failure=True,
    )

    policy = service.control.get_policy()
    assert policy.configured_mode == "newapi_required_default"
    blocked = await service.begin(MODEL_ID)
    assert blocked.intercepted is True
    assert blocked.dispatch is None
    assert blocked.error_code == "provider_chat_required_gate_degraded"
    receipts = repository.list_chat_control_receipts("local")
    assert len(receipts["runs"]) == 2
    assert backup_id not in {item["connection_id"] for item in receipts["attempts"]}

    current = service.control.get_policy()
    rolled_back = service.control.update_policy(
        ProviderChatControlPolicyUpdate(
            expected_revision=current.revision,
            mode="newapi_preferred",
            auto_enabled=current.auto_enabled,
            stable_model_ids=current.stable_model_ids,
            routes=[
                ProviderChatControlRouteUpdate(
                    capability=route.capability,
                    connection_ids=route.connection_ids,
                )
                for route in current.routes
            ],
        )
    )
    assert rolled_back.configured_mode == "newapi_preferred"
    assert next(
        item
        for item in rolled_back.qualifications
        if item.connection_id == newapi_id
    ).valid is False
    fallback = await service.begin(MODEL_ID)
    assert fallback.dispatch is not None
    assert fallback.dispatch.target.connection_id == backup_id
