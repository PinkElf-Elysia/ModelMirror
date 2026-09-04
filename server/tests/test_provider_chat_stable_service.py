from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from server.model_router.chat_control import ProviderChatControlService
from server.model_router.cleanup_chat_receipts import main as cleanup_receipts_main
from server.model_router.chat_stable import (
    ProviderChatCertificationBinding,
    ProviderChatStableService,
)
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.repository import (
    RouterCredentialUnavailable,
    RouterRepositoryError,
    SQLiteRouterRepository,
)
from server.model_router.schemas import (
    ProviderChatControlPolicyUpdate,
    ProviderChatControlRouteUpdate,
    RouterConnectionCreate,
    RouterConnectionUpdate,
)
from server.model_router.service import ModelRouterService, RouterServiceError


MODEL_ID = "provider/model"
SCOPED_MODEL_ID = "provider/scoped-model"


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
    for capability in ("chat_text", "chat_tools"):
        suffix = "" if capability == "chat_text" else f"-{capability}"
        idempotency_seed = (
            connection.id
            if capability == "chat_text"
            else f"{connection.id}:{capability}"
        )
        certification, created = repository.claim_chat_certification(
            "local",
            certification_id=f"cert-{connection.id}{suffix}",
            connection_id=connection.id,
            connection_fingerprint=fingerprint,
            contract_version="modelmirror-provider-chat-v1",
            capability=capability,
            requested_model=MODEL_ID,
            idempotency_key_hash=hashlib.sha256(
                idempotency_seed.encode()
            ).hexdigest(),
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
                ),
                ProviderChatControlRouteUpdate(
                    capability="chat_tools",
                    connection_ids=[newapi_id, backup_id],
                ),
            ],
        )
    )
    return ProviderChatStableService(service), repository, newapi_id, backup_id


def _qualify_scoped_model(
    repository: SQLiteRouterRepository,
    connection_id: str,
    *,
    revision: str = "",
) -> None:
    suffix = f"-{revision}" if revision else ""
    fingerprint = repository.connection_config_fingerprint("local", connection_id)
    refresh_id = f"refresh-scoped-{connection_id}{suffix}"
    repository.claim_catalog_refresh(
        "local",
        refresh_id=refresh_id,
        connection_id=connection_id,
        connection_fingerprint=fingerprint,
    )
    repository.complete_catalog_refresh(
        "local",
        refresh_id,
        connection_id=connection_id,
        models=[
            {
                "model_id": model_id,
                "normalized_model_id": model_id,
                "capability_state": "declared",
            }
            for model_id in (MODEL_ID, SCOPED_MODEL_ID)
        ],
        offerings=[],
        model_count=2,
        truncated=False,
        catalog_fingerprint=f"catalog-scoped-{connection_id}{suffix}",
        observed_at="2026-08-21T00:01:00+00:00",
    )
    for capability in ("chat_text", "chat_tools"):
        capability_suffix = "" if capability == "chat_text" else f"-{capability}"
        certification, created = repository.claim_chat_certification(
            "local",
            certification_id=(
                f"cert-scoped-{connection_id}{capability_suffix}{suffix}"
            ),
            connection_id=connection_id,
            connection_fingerprint=fingerprint,
            contract_version="modelmirror-provider-chat-v1",
            capability=capability,
            requested_model=SCOPED_MODEL_ID,
            idempotency_key_hash=hashlib.sha256(
                f"scoped-{connection_id}:{capability}:{revision}".encode()
            ).hexdigest(),
        )
        assert created is True
        repository.complete_chat_certification(
            "local",
            str(certification["id"]),
            status="passed",
            checks={"capability_verified": True},
            warning_codes=[],
            actual_model=SCOPED_MODEL_ID,
        )


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
    assert [
        (
            binding.capability,
            binding.connection_id,
            binding.certification_id,
        )
        for binding in result.dispatch.required_certifications
    ] == [
        ("chat_text", backup_id, result.dispatch.certification_id),
    ]
    receipts = repository.list_chat_control_receipts("local")
    assert len(receipts["runs"]) == 1
    attempts = receipts["attempts"]
    assert [(item["connection_id"], item["dispatched"]) for item in attempts] == [
        (newapi_id, 0),
        (backup_id, 0),
    ]
    assert attempts[0]["error_code"] == "provider_address_blocked"


@pytest.mark.asyncio
async def test_scoped_certified_model_uses_current_certificate_without_stable_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(tmp_path, monkeypatch)
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)

    assert service.readiness(SCOPED_MODEL_ID, "chat_text") == (
        False,
        "provider_chat_no_qualified_route",
    )
    assert service.readiness_scoped_certified(SCOPED_MODEL_ID, "chat_text") == (
        True,
        None,
    )

    result = await service.begin_scoped_certified(SCOPED_MODEL_ID, "chat_text")

    assert result.intercepted is True
    assert result.dispatch is not None
    assert result.dispatch.target.connection_id == backup_id
    assert result.dispatch.certification_id == f"cert-scoped-{backup_id}"
    receipts = repository.list_chat_control_receipts("local")
    assert receipts["runs"][0]["gateway"] == "ai_research_scoped"


@pytest.mark.asyncio
async def test_scoped_multi_capability_requires_one_fully_qualified_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(tmp_path, monkeypatch)
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)
    with sqlite3.connect(repository.database_path) as database:
        database.execute(
            """
            UPDATE provider_chat_certifications
            SET status = 'failed'
            WHERE tenant_id = 'local' AND requested_model = ?
              AND ((connection_id = ? AND capability = 'chat_tools')
                OR (connection_id = ? AND capability = 'chat_text'))
            """,
            (SCOPED_MODEL_ID, newapi_id, backup_id),
        )
        database.commit()

    required = ("chat_text", "chat_tools")
    ready, _reason = service.readiness_scoped_certified(
        SCOPED_MODEL_ID,
        "chat_tools",
        required_capabilities=required,
    )
    rejected = await service.begin_scoped_certified(
        SCOPED_MODEL_ID,
        "chat_tools",
        required_capabilities=required,
    )

    assert ready is False
    assert rejected.intercepted is True
    assert rejected.dispatch is None
    assert all(
        item["dispatched"] == 0
        for item in repository.list_chat_control_receipts("local")["attempts"]
    )

    with sqlite3.connect(repository.database_path) as database:
        database.execute(
            """
            UPDATE provider_chat_certifications
            SET status = 'passed'
            WHERE tenant_id = 'local' AND requested_model = ?
              AND connection_id = ? AND capability = 'chat_text'
            """,
            (SCOPED_MODEL_ID, backup_id),
        )
        database.commit()

    assert service.readiness_scoped_certified(
        SCOPED_MODEL_ID,
        "chat_tools",
        required_capabilities=required,
    ) == (True, None)
    accepted = await service.begin_scoped_certified(
        SCOPED_MODEL_ID,
        "chat_tools",
        required_capabilities=required,
    )
    assert accepted.dispatch is not None
    assert accepted.dispatch.target.connection_id == backup_id
    assert {
        binding.connection_id
        for binding in accepted.dispatch.required_certifications
    } == {backup_id}


@pytest.mark.asyncio
async def test_atomic_dispatch_rejects_cross_connection_certificate_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(tmp_path, monkeypatch)
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)
    result = await service.begin_scoped_certified(
        SCOPED_MODEL_ID,
        "chat_tools",
        required_capabilities=("chat_text", "chat_tools"),
    )
    assert result.dispatch is not None
    assert result.dispatch.target.connection_id == backup_id
    tampered = replace(
        result.dispatch,
        required_certifications=tuple(
            ProviderChatCertificationBinding(
                capability=binding.capability,
                connection_id=newapi_id,
                certification_id=f"cert-scoped-{newapi_id}",
            )
            if binding.capability == "chat_text"
            else binding
            for binding in result.dispatch.required_certifications
        ),
    )

    with pytest.raises(RouterServiceError) as exc_info:
        service.mark_dispatched(tampered)

    assert exc_info.value.code == "provider_chat_policy_or_qualification_changed"
    selected = next(
        item
        for item in repository.list_chat_control_receipts("local")["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )
    assert selected["dispatched"] == 0
    assert selected["status"] == "failed"


@pytest.mark.asyncio
async def test_scoped_certification_requires_exact_actual_model_at_readiness_and_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(tmp_path, monkeypatch)
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)

    with sqlite3.connect(repository.database_path) as database:
        database.execute(
            """
            UPDATE provider_chat_certifications
            SET actual_model = NULL
            WHERE tenant_id = 'local' AND requested_model = ?
            """,
            (SCOPED_MODEL_ID,),
        )
        database.commit()

    assert service.readiness_scoped_certified(SCOPED_MODEL_ID, "chat_text") == (
        False,
        "provider_chat_certification_model_identity_required",
    )

    with sqlite3.connect(repository.database_path) as database:
        database.execute(
            """
            UPDATE provider_chat_certifications
            SET actual_model = ?
            WHERE tenant_id = 'local' AND requested_model = ?
            """,
            (SCOPED_MODEL_ID, SCOPED_MODEL_ID),
        )
        database.commit()

    result = await service.begin_scoped_certified(SCOPED_MODEL_ID, "chat_text")
    assert result.dispatch is not None
    with sqlite3.connect(repository.database_path) as database:
        database.execute(
            """
            UPDATE provider_chat_certifications
            SET actual_model = NULL
            WHERE tenant_id = 'local' AND id = ?
            """,
            (result.dispatch.certification_id,),
        )
        database.commit()

    monkeypatch.setattr(service, "ensure_dispatch_current", lambda _dispatch: None)
    with pytest.raises(RouterServiceError) as exc_info:
        service.mark_dispatched(result.dispatch)

    assert exc_info.value.code == "provider_chat_policy_or_qualification_changed"
    selected = next(
        item
        for item in repository.list_chat_control_receipts("local")["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )
    assert selected["dispatched"] == 0
    assert selected["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "connection_update",
    [
        pytest.param(
            {"base_url": "https://changed.example/v1"},
            id="base-url",
        ),
        pytest.param({"api_key": "rotated-secret"}, id="credential"),
        pytest.param({"scopes": ["embedding"]}, id="scopes"),
    ],
)
async def test_scoped_dispatch_rechecks_connection_fingerprint_before_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    connection_update: dict[str, object],
) -> None:
    service, repository, newapi_id, backup_id = _service(tmp_path, monkeypatch)
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)
    result = await service.begin_scoped_certified(SCOPED_MODEL_ID, "chat_text")
    assert result.dispatch is not None
    repository.update_connection(
        "local",
        backup_id,
        RouterConnectionUpdate(**connection_update),
    )

    with pytest.raises(RouterServiceError) as exc_info:
        service.mark_dispatched(result.dispatch)

    assert exc_info.value.code == "provider_chat_policy_or_qualification_changed"
    selected = next(
        item
        for item in repository.list_chat_control_receipts("local")["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )
    assert selected["dispatched"] == 0
    assert selected["status"] == "failed"


@pytest.mark.asyncio
async def test_scoped_dispatch_binds_one_connection_snapshot_before_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(tmp_path, monkeypatch)
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)
    original_snapshot = repository.get_connection_credential_snapshot
    rotated = False

    def snapshot_with_interleaved_rotation(
        tenant_id: str, connection_id: str
    ) -> tuple[object, str, str]:
        nonlocal rotated
        snapshot = original_snapshot(tenant_id, connection_id)
        if connection_id == backup_id and not rotated:
            rotated = True
            repository.update_connection(
                tenant_id,
                connection_id,
                RouterConnectionUpdate(
                    base_url="https://rotated.example/v1",
                    api_key="rotated-secret",
                ),
            )
            repository.save_test_result(
                tenant_id,
                connection_id,
                health="online",
                model_count=2,
                checked_at="2026-08-21T00:02:00+00:00",
            )
            _qualify_scoped_model(
                repository,
                connection_id,
                revision="rotated",
            )
        return snapshot

    monkeypatch.setattr(
        repository,
        "get_connection_credential_snapshot",
        snapshot_with_interleaved_rotation,
    )
    result = await service.begin_scoped_certified(SCOPED_MODEL_ID, "chat_text")

    assert result.dispatch is not None
    assert rotated is True
    assert result.dispatch.target.endpoints.base_url == "https://openrouter.example/v1"
    assert (
        result.dispatch.connection_fingerprint
        != repository.connection_config_fingerprint("local", backup_id)
    )
    with pytest.raises(RouterServiceError) as exc_info:
        service.mark_dispatched(result.dispatch)

    assert exc_info.value.code == "provider_chat_policy_or_qualification_changed"
    selected = next(
        item
        for item in repository.list_chat_control_receipts("local")["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )
    assert selected["dispatched"] == 0
    assert selected["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("actual_capability", "invalidated_capability"),
    (("chat_text", "chat_tools"), ("chat_tools", "chat_text")),
)
async def test_scoped_dispatch_rechecks_every_required_certificate_before_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    actual_capability: str,
    invalidated_capability: str,
) -> None:
    service, repository, newapi_id, backup_id = _service(tmp_path, monkeypatch)
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)
    result = await service.begin_scoped_certified(
        SCOPED_MODEL_ID,
        actual_capability,
        required_capabilities=("chat_text", "chat_tools"),
    )
    assert result.dispatch is not None
    bindings = {
        binding.capability: binding
        for binding in result.dispatch.required_certifications
    }
    assert set(bindings) == {"chat_text", "chat_tools"}
    assert (
        bindings[actual_capability].certification_id
        == result.dispatch.certification_id
    )

    original_current_qualification = service.control.current_qualification
    invalidated_after_valid_read = False

    def current_qualification_with_concurrent_invalidation(**fields):
        nonlocal invalidated_after_valid_read
        qualification = original_current_qualification(**fields)
        if (
            fields["capability"] == invalidated_capability
            and not invalidated_after_valid_read
        ):
            assert qualification[0] is not None
            with sqlite3.connect(repository.database_path) as database:
                database.execute(
                    """
                    UPDATE provider_chat_certifications
                    SET status = 'failed'
                    WHERE tenant_id = 'local' AND id = ?
                    """,
                    (bindings[invalidated_capability].certification_id,),
                )
                database.commit()
            invalidated_after_valid_read = True
        return qualification

    monkeypatch.setattr(
        service.control,
        "current_qualification",
        current_qualification_with_concurrent_invalidation,
    )

    with pytest.raises(RouterServiceError) as exc_info:
        service.mark_dispatched(result.dispatch)

    assert invalidated_after_valid_read is True
    assert exc_info.value.code == "provider_chat_policy_or_qualification_changed"
    selected = next(
        item
        for item in repository.list_chat_control_receipts("local")["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )
    assert selected["dispatched"] == 0
    assert selected["status"] == "failed"


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
async def test_duplicate_mark_does_not_rewrite_a_dispatched_attempt_as_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, _newapi_id, _backup_id = _service(tmp_path, monkeypatch)
    result = await service.begin(MODEL_ID)
    assert result.dispatch is not None
    service.mark_dispatched(result.dispatch)

    with pytest.raises(RouterServiceError) as exc_info:
        service.mark_dispatched(result.dispatch)

    assert exc_info.value.code == "provider_chat_policy_or_qualification_changed"
    receipts = repository.list_chat_control_receipts("local")
    selected = next(
        item
        for item in receipts["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )
    run = next(
        item for item in receipts["runs"] if item["id"] == result.dispatch.run_id
    )
    assert selected["dispatched"] == 1
    assert selected["status"] == "running"
    assert run["status"] == "running"

    service.complete(
        result.dispatch,
        status="succeeded",
        result_class="success",
        actual_model=MODEL_ID,
    )
    completed = repository.list_chat_control_receipts("local")
    assert completed["attempts"][-1]["status"] == "succeeded"
    assert completed["runs"][-1]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_atomic_dispatch_serializes_a_concurrent_certificate_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(tmp_path, monkeypatch)
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)
    result = await service.begin_scoped_certified(
        SCOPED_MODEL_ID,
        "chat_text",
        required_capabilities=("chat_text", "chat_tools"),
    )
    assert result.dispatch is not None
    transaction_open = threading.Event()
    release_transaction = threading.Event()
    dispatch_errors: list[BaseException] = []
    original_envelope = repository._validate_chat_dispatch_envelope

    def pause_inside_write_transaction(*args, **kwargs):
        value = original_envelope(*args, **kwargs)
        transaction_open.set()
        if not release_transaction.wait(timeout=10):
            raise AssertionError("dispatch transaction was not released")
        return value

    monkeypatch.setattr(
        repository,
        "_validate_chat_dispatch_envelope",
        pause_inside_write_transaction,
    )

    def mark_dispatch() -> None:
        try:
            service.mark_dispatched(result.dispatch)
        except BaseException as exc:  # pragma: no cover - asserted below
            dispatch_errors.append(exc)

    dispatch_thread = threading.Thread(target=mark_dispatch, daemon=True)
    dispatch_thread.start()
    assert transaction_open.wait(timeout=10)
    try:
        with sqlite3.connect(repository.database_path, timeout=0) as database:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                database.execute(
                    """
                    UPDATE provider_chat_certifications
                    SET status = 'failed'
                    WHERE tenant_id = 'local' AND id = ?
                    """,
                    (
                        result.dispatch.required_certifications[0]
                        .certification_id,
                    ),
                )
    finally:
        release_transaction.set()
        dispatch_thread.join(timeout=10)

    assert dispatch_thread.is_alive() is False
    assert dispatch_errors == []
    selected = next(
        item
        for item in repository.list_chat_control_receipts("local")["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )
    assert selected["dispatched"] == 1

    with sqlite3.connect(repository.database_path, timeout=1) as database:
        database.execute(
            """
            UPDATE provider_chat_certifications
            SET status = 'failed'
            WHERE tenant_id = 'local' AND id = ?
            """,
            (
                result.dispatch.required_certifications[0].certification_id,
            ),
        )
        database.commit()


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
async def test_scoped_dispatch_completion_rolls_back_attempt_when_run_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(
        tmp_path,
        monkeypatch,
        newapi_ip="8.8.8.8",
    )
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)
    result = await service.begin_scoped_certified(SCOPED_MODEL_ID)
    assert result.dispatch is not None
    service.mark_dispatched(result.dispatch)

    with sqlite3.connect(repository.database_path) as database:
        database.execute(
            f"""
            CREATE TRIGGER abort_chat_run_completion
            BEFORE UPDATE OF status ON provider_chat_runs
            WHEN OLD.tenant_id = 'local'
              AND OLD.id = '{result.dispatch.run_id}'
              AND NEW.status != 'running'
            BEGIN
                SELECT RAISE(ABORT, 'simulated_run_write_failure');
            END
            """
        )

    assert service.complete(
        result.dispatch,
        status="failed",
        result_class="hard_failure",
        error_code="provider_chat_http_401",
        hard_failure=True,
    ) is False

    receipts = repository.list_chat_control_receipts("local")
    attempt = next(
        item
        for item in receipts["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )
    run = next(
        item for item in receipts["runs"] if item["id"] == result.dispatch.run_id
    )
    assert attempt["status"] == "running"
    assert attempt["result_class"] is None
    assert run["status"] == "running"
    assert run["hard_failure"] == 0
    pending = list(repository.chat_completion_outbox_dir.glob("*.json"))
    assert len(pending) == 1
    assert "private prompt" not in pending[0].read_text(encoding="utf-8")
    default_result = await service.begin(MODEL_ID)
    assert default_result.dispatch is not None
    service.mark_dispatched(default_result.dispatch)
    assert service.complete(
        default_result.dispatch,
        status="succeeded",
        result_class="success",
    ) is True
    with pytest.raises(RouterServiceError) as exc_info:
        await service.begin_scoped_certified(SCOPED_MODEL_ID)
    assert exc_info.value.code == (
        "provider_chat_completion_reconciliation_pending"
    )

    restarted_repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    retained = restarted_repository.list_chat_control_receipts("local")["runs"]
    assert next(run for run in retained if run["id"] == result.dispatch.run_id)["status"] == "running"
    restarted = ProviderChatStableService(
        ModelRouterService(
            restarted_repository,
            egress_policy=ProviderEgressPolicy(
                resolver=lambda _host, _port: ["8.8.8.8"]
            ),
        )
    )
    with pytest.raises(RouterServiceError) as exc_info:
        await restarted.begin_scoped_certified(SCOPED_MODEL_ID)
    assert exc_info.value.code == (
        "provider_chat_completion_reconciliation_pending"
    )
    pending_after_restart = restarted_repository.list_chat_control_receipts("local")
    assert next(
        item
        for item in pending_after_restart["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )["status"] == "running"
    with sqlite3.connect(repository.database_path) as database:
        database.execute("DROP TRIGGER abort_chat_run_completion")
    await restarted.begin_scoped_certified(SCOPED_MODEL_ID)
    completed = repository.list_chat_control_receipts("local")
    attempt = next(
        item
        for item in completed["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )
    run = next(
        item for item in completed["runs"] if item["id"] == result.dispatch.run_id
    )
    assert attempt["result_class"] == "hard_failure"
    assert run["result_class"] == "hard_failure"
    assert run["hard_failure"] == 1
    assert list(repository.chat_completion_outbox_dir.glob("*.json")) == []


@pytest.mark.asyncio
async def test_default_dispatch_completion_does_not_use_scoped_outbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, _newapi_id, _backup_id = _service(
        tmp_path,
        monkeypatch,
        newapi_ip="8.8.8.8",
    )
    result = await service.begin(MODEL_ID)
    assert result.dispatch is not None
    assert result.dispatch.gateway == "default"
    service.mark_dispatched(result.dispatch)

    with pytest.raises(
        RouterRepositoryError,
        match="provider_chat_completion_scope_invalid",
    ):
        repository.stage_chat_control_completion(
            "local",
            result.dispatch.attempt_id,
            expected_run_id=result.dispatch.run_id,
            status="failed",
            result_class="hard_failure",
        )
    assert service.complete(
        result.dispatch,
        status="uncertain",
        result_class="uncertain",
        error_code="provider_chat_unexpected_error",
    ) is True

    receipts = repository.list_chat_control_receipts("local")
    attempt = next(
        item for item in receipts["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )
    run = next(
        item for item in receipts["runs"] if item["id"] == result.dispatch.run_id
    )
    assert attempt["status"] == "uncertain"
    assert attempt["error_code"] == "provider_chat_unexpected_error"
    assert run["status"] == "uncertain"
    assert list(repository.chat_completion_outbox_dir.glob("*.json")) == []


@pytest.mark.asyncio
async def test_default_dispatched_run_remains_uncertain_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, _newapi_id, _backup_id = _service(
        tmp_path,
        monkeypatch,
        newapi_ip="8.8.8.8",
    )
    result = await service.begin(MODEL_ID)
    assert result.dispatch is not None
    service.mark_dispatched(result.dispatch)

    restarted = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    receipts = restarted.list_chat_control_receipts("local")
    attempt = next(
        item for item in receipts["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )
    run = next(
        item for item in receipts["runs"] if item["id"] == result.dispatch.run_id
    )
    assert attempt["status"] == "uncertain"
    assert attempt["error_code"] == "server_restarted"
    assert run["status"] == "uncertain"
    assert run["reason_codes_json"] == '["server_restarted"]'


@pytest.mark.asyncio
async def test_scoped_undispatched_failure_cleans_up_without_outbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(
        tmp_path,
        monkeypatch,
        newapi_ip="8.8.8.8",
    )
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)
    result = await service.begin_scoped_certified(SCOPED_MODEL_ID)
    assert result.dispatch is not None
    assert result.dispatch.gateway == "ai_research_scoped"

    assert service.fail_undispatched(
        result.dispatch,
        error_code="ai_research_bridge_transport_failed",
    ) is True
    receipts = repository.list_chat_control_receipts("local")
    attempt = next(
        item for item in receipts["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )
    run = next(
        item for item in receipts["runs"] if item["id"] == result.dispatch.run_id
    )
    assert attempt["status"] == "failed"
    assert attempt["result_class"] == "preflight_failure"
    assert run["status"] == "failed"
    assert run["result_class"] == "preflight_failure"
    assert list(repository.chat_completion_outbox_dir.glob("*.json")) == []

    retried = await service.begin_scoped_certified(SCOPED_MODEL_ID)
    assert retried.dispatch is not None


def test_cleanup_preserves_hard_failure_receipts_before_cutoff(
    tmp_path: Path,
) -> None:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    for run_id, hard_failure, attempt_hard_failure in (
        ("ordinary-run", False, False),
        ("hard-failure-run", True, True),
        ("attempt-hard-failure-run", False, True),
    ):
        repository.claim_chat_control_run(
            "local",
            run_id=run_id,
            policy_fingerprint="policy",
            capability="chat_text",
            requested_model="provider/model",
            strategy="explicit_session",
        )
        repository.claim_chat_control_attempt(
            "local",
            attempt_id=f"{run_id}-attempt",
            run_id=run_id,
            capability="chat_text",
            position=0,
            connection_id="conn-1",
            provider_kind="newapi",
        )
        repository.complete_chat_control_attempt(
            "local",
            f"{run_id}-attempt",
            status="failed",
            result_class="hard_failure" if attempt_hard_failure else "transient_failure",
            error_code=(
                "provider_chat_http_401"
                if attempt_hard_failure
                else "provider_chat_http_503"
            ),
        )
        repository.complete_chat_control_run(
            "local",
            run_id,
            status="failed",
            result_class="hard_failure" if hard_failure else "transient_failure",
            hard_failure=hard_failure,
        )
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            """
            UPDATE provider_chat_runs
            SET completed_at = ?, updated_at = ?
            """,
            ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
        )
        connection.execute(
            """
            UPDATE provider_chat_attempts
            SET completed_at = ?, updated_at = ?
            """,
            ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00"),
        )

    dry_run = repository.cleanup_chat_control_receipts(
        "local", before="2021-01-01T00:00:00+00:00"
    )
    assert dry_run["runs"] == 1
    assert dry_run["attempts"] == 1
    result = repository.cleanup_chat_control_receipts(
        "local",
        before="2021-01-01T00:00:00+00:00",
        apply=True,
    )

    assert result == {
        "applied": True,
        "before": "2021-01-01T00:00:00+00:00",
        "runs": 1,
        "attempts": 1,
    }
    retained = repository.list_chat_control_receipts("local")
    assert {item["id"] for item in retained["runs"]} == {
        "hard-failure-run", "attempt-hard-failure-run"
    }
    assert {item["id"] for item in retained["attempts"]} == {
        "hard-failure-run-attempt", "attempt-hard-failure-run-attempt"
    }


@pytest.mark.asyncio
async def test_active_dispatch_does_not_block_an_independent_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _repository, _newapi_id, _backup_id = _service(
        tmp_path, monkeypatch, newapi_ip="8.8.8.8"
    )
    first = await service.begin(MODEL_ID)
    assert first.dispatch is not None
    service.mark_dispatched(first.dispatch)

    second = await service.begin(MODEL_ID)
    assert second.dispatch is not None
    assert second.dispatch.run_id != first.dispatch.run_id
    service.mark_dispatched(second.dispatch)
    for dispatch in (first.dispatch, second.dispatch):
        assert service.complete(
            dispatch, status="succeeded", result_class="success"
        ) is True


@pytest.mark.asyncio
async def test_completion_reconcile_is_idempotent_after_commit_before_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(
        tmp_path, monkeypatch
    )
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)
    result = await service.begin_scoped_certified(SCOPED_MODEL_ID)
    assert result.dispatch is not None
    service.mark_dispatched(result.dispatch)
    fields = {
        "expected_run_id": result.dispatch.run_id,
        "status": "succeeded",
        "result_class": "success",
        "actual_model": SCOPED_MODEL_ID,
        "reason_codes": [],
    }
    staged = repository.stage_chat_control_completion(
        "local", result.dispatch.attempt_id, **fields
    )
    monkeypatch.setattr(
        "server.model_router.repository.utc_now", lambda: "2099-01-01T00:00:00+00:00"
    )
    restaged = repository.stage_chat_control_completion(
        "local", result.dispatch.attempt_id, **fields
    )
    assert restaged == staged
    assert restaged["stagedAt"] != "2099-01-01T00:00:00+00:00"
    repository.complete_chat_control_dispatch(
        "local", result.dispatch.attempt_id, **fields
    )
    repeated = repository.complete_chat_control_dispatch(
        "local", result.dispatch.attempt_id, **fields
    )
    assert repeated["attempt"]["status"] == "succeeded"
    with pytest.raises(
        RouterRepositoryError,
        match="provider_chat_completion_conflict",
    ):
        repository.complete_chat_control_dispatch(
            "local",
            result.dispatch.attempt_id,
            **{**fields, "status": "failed"},
        )

    reconciled = repository.reconcile_chat_control_completions("local")

    assert reconciled == {"applied": 1, "pending": 0}
    assert list(repository.chat_completion_outbox_dir.glob("*.json")) == []


@pytest.mark.asyncio
async def test_maintenance_cleanup_does_not_recover_an_active_scoped_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(tmp_path, monkeypatch)
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)
    result = await service.begin_scoped_certified(SCOPED_MODEL_ID)
    assert result.dispatch is not None
    service.mark_dispatched(result.dispatch)

    monkeypatch.setenv("MODEL_MIRROR_CREDENTIAL_MASTER_KEY", "x" * 32)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cleanup_chat_receipts",
            "--storage-dir",
            str(tmp_path),
            "--older-than-days",
            "1",
        ],
    )
    assert cleanup_receipts_main() == 0
    active = repository.list_chat_control_receipts("local")
    assert next(
        item for item in active["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )["status"] == "running"
    assert next(
        item for item in active["runs"]
        if item["id"] == result.dispatch.run_id
    )["status"] == "running"

    assert service.complete(
        result.dispatch,
        status="succeeded",
        result_class="success",
        actual_model=SCOPED_MODEL_ID,
    ) is True
    assert list(repository.chat_completion_outbox_dir.glob("*.json")) == []
    follow_up = await service.begin_scoped_certified(SCOPED_MODEL_ID)
    assert follow_up.dispatch is not None


@pytest.mark.asyncio
async def test_concurrent_reconcilers_do_not_report_a_completed_unlink_as_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(tmp_path, monkeypatch)
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)
    result = await service.begin_scoped_certified(SCOPED_MODEL_ID)
    assert result.dispatch is not None
    service.mark_dispatched(result.dispatch)
    repository.stage_chat_control_completion(
        "local",
        result.dispatch.attempt_id,
        expected_run_id=result.dispatch.run_id,
        status="succeeded",
        result_class="success",
        actual_model=SCOPED_MODEL_ID,
        reason_codes=[],
    )
    outbox_path = next(repository.chat_completion_outbox_dir.glob("*.json"))
    first = SQLiteRouterRepository(
        tmp_path,
        master_key=b"x" * 32,
        recover_chat_control_on_startup=False,
    )
    second = SQLiteRouterRepository(
        tmp_path,
        master_key=b"x" * 32,
        recover_chat_control_on_startup=False,
    )
    unlink_barrier = threading.Barrier(2)
    original_unlink = Path.unlink

    def synchronized_unlink(path: Path, *args, **kwargs):
        if path == outbox_path:
            unlink_barrier.wait(timeout=10)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", synchronized_unlink)
    reconciled: list[dict[str, int]] = []
    errors: list[BaseException] = []

    def reconcile(candidate: SQLiteRouterRepository) -> None:
        try:
            reconciled.append(
                candidate.reconcile_chat_control_completions("local")
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=reconcile, args=(candidate,), daemon=True)
        for candidate in (first, second)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert all(thread.is_alive() is False for thread in threads)
    assert errors == []
    assert reconciled == [
        {"applied": 1, "pending": 0},
        {"applied": 1, "pending": 0},
    ]
    assert list(repository.chat_completion_outbox_dir.glob("*.json")) == []
    completed = repository.list_chat_control_receipts("local")
    assert next(
        item for item in completed["attempts"]
        if item["id"] == result.dispatch.attempt_id
    )["status"] == "succeeded"


@pytest.mark.asyncio
async def test_cleanup_refuses_pending_completion_before_deleting_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(
        tmp_path, monkeypatch
    )
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)
    result = await service.begin_scoped_certified(SCOPED_MODEL_ID)
    assert result.dispatch is not None
    service.mark_dispatched(result.dispatch)

    original_unlink = Path.unlink

    def fail_outbox_unlink(path: Path, *args, **kwargs) -> None:
        if (
            path.parent == repository.chat_completion_outbox_dir
            and path.suffix == ".json"
        ):
            raise OSError("simulated completion outbox unlink failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_outbox_unlink)
    assert service.complete(
        result.dispatch,
        status="succeeded",
        result_class="success",
        actual_model=SCOPED_MODEL_ID,
    ) is False
    outbox_path = next(repository.chat_completion_outbox_dir.glob("*.json"))
    outbox_bytes = outbox_path.read_bytes()

    with pytest.raises(
        RouterRepositoryError,
        match="provider_chat_completion_reconciliation_pending",
    ):
        repository.cleanup_chat_control_receipts(
            "local",
            before="2100-01-01T00:00:00+00:00",
            apply=True,
        )
    retained = repository.list_chat_control_receipts("local")
    assert any(item["id"] == result.dispatch.run_id for item in retained["runs"])
    assert any(
        item["id"] == result.dispatch.attempt_id for item in retained["attempts"]
    )
    retained_run_count = len(retained["runs"])
    retained_attempt_count = len(retained["attempts"])
    assert outbox_path.read_bytes() == outbox_bytes

    monkeypatch.setattr(Path, "unlink", original_unlink)
    cleaned = repository.cleanup_chat_control_receipts(
        "local",
        before="2100-01-01T00:00:00+00:00",
        apply=True,
    )
    assert cleaned["runs"] == retained_run_count
    assert cleaned["attempts"] == retained_attempt_count
    assert list(repository.chat_completion_outbox_dir.glob("*.json")) == []
    receipts = repository.list_chat_control_receipts("local")
    assert receipts["runs"] == []
    assert receipts["attempts"] == []


@pytest.mark.asyncio
async def test_mismatched_master_key_fails_before_startup_reconciliation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, backup_id = _service(
        tmp_path, monkeypatch
    )
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)
    result = await service.begin_scoped_certified(SCOPED_MODEL_ID)
    assert result.dispatch is not None
    service.mark_dispatched(result.dispatch)
    repository.stage_chat_control_completion(
        "local",
        result.dispatch.attempt_id,
        expected_run_id=result.dispatch.run_id,
        status="succeeded",
        result_class="success",
        actual_model=MODEL_ID,
        reason_codes=[],
    )
    outbox_path = next(repository.chat_completion_outbox_dir.glob("*.json"))
    outbox_bytes = outbox_path.read_bytes()

    with pytest.raises(RouterCredentialUnavailable):
        SQLiteRouterRepository(tmp_path, master_key=b"y" * 32)

    with sqlite3.connect(repository.database_path) as database:
        attempt_status = database.execute(
            "SELECT status FROM provider_chat_attempts WHERE id = ?",
            (result.dispatch.attempt_id,),
        ).fetchone()[0]
        run_status = database.execute(
            "SELECT status FROM provider_chat_runs WHERE id = ?",
            (result.dispatch.run_id,),
        ).fetchone()[0]
    assert attempt_status == "running"
    assert run_status == "running"
    assert outbox_path.is_file()
    assert outbox_path.read_bytes() == outbox_bytes

    recovered = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    receipts = recovered.list_chat_control_receipts("local")
    assert next(
        attempt
        for attempt in receipts["attempts"]
        if attempt["id"] == result.dispatch.attempt_id
    )["status"] == "succeeded"
    assert next(
        run
        for run in receipts["runs"]
        if run["id"] == result.dispatch.run_id
    )["status"] == "succeeded"
    assert list(recovered.chat_completion_outbox_dir.glob("*.json")) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tampered_field", ["status", "stagedAt", "broken_symlink"])
async def test_unsafe_completion_outbox_fails_closed_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tampered_field: str
) -> None:
    service, repository, newapi_id, backup_id = _service(
        tmp_path,
        monkeypatch,
        newapi_ip="8.8.8.8",
    )
    _qualify_scoped_model(repository, newapi_id)
    _qualify_scoped_model(repository, backup_id)
    result = await service.begin_scoped_certified(SCOPED_MODEL_ID)
    assert result.dispatch is not None
    service.mark_dispatched(result.dispatch)
    with sqlite3.connect(repository.database_path) as database:
        database.execute(
            f"""
            CREATE TRIGGER abort_chat_run_completion
            BEFORE UPDATE OF status ON provider_chat_runs
            WHEN OLD.tenant_id = 'local'
              AND OLD.id = '{result.dispatch.run_id}'
              AND NEW.status != 'running'
            BEGIN
                SELECT RAISE(ABORT, 'simulated_run_write_failure');
            END
            """
        )
    assert service.complete(
        result.dispatch,
        status="failed",
        result_class="hard_failure",
        error_code="provider_chat_http_401",
        hard_failure=True,
    ) is False
    outbox_path = next(repository.chat_completion_outbox_dir.glob("*.json"))
    if tampered_field == "broken_symlink":
        outbox_path.unlink()
        try:
            outbox_path.symlink_to(outbox_path.with_suffix(".missing"))
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation is unavailable")
    else:
        encoded = outbox_path.read_bytes()
        marker = f'"{tampered_field}":"'.encode()
        offset = encoded.index(marker) + len(marker)
        altered = bytearray(encoded)
        altered[offset] = ord("3") if tampered_field == "stagedAt" else ord("x")
        assert sum(a != b for a, b in zip(encoded, altered)) == 1
        outbox_path.write_bytes(altered)
    with sqlite3.connect(repository.database_path) as database:
        database.execute("DROP TRIGGER abort_chat_run_completion")

    restarted_repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    restarted = ProviderChatStableService(
        ModelRouterService(
            restarted_repository,
            egress_policy=ProviderEgressPolicy(
                resolver=lambda _host, _port: ["8.8.8.8"]
            ),
        )
    )
    runs_before = len(
        restarted_repository.list_chat_control_receipts("local")["runs"]
    )
    retained_run = next(
        run for run in restarted_repository.list_chat_control_receipts("local")["runs"]
        if run["id"] == result.dispatch.run_id
    )
    assert retained_run["status"] == "running"
    with pytest.raises(RouterServiceError) as exc_info:
        await restarted.begin_scoped_certified(SCOPED_MODEL_ID)
    assert exc_info.value.code == (
        "provider_chat_completion_reconciliation_pending"
    )
    assert len(restarted_repository.list_chat_control_receipts("local")["runs"]) == (
        runs_before
    )
    assert outbox_path.exists() or outbox_path.is_symlink()


@pytest.mark.asyncio
async def test_restart_rejects_orphaned_dispatched_hard_failure_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, repository, newapi_id, _backup_id = _service(
        tmp_path,
        monkeypatch,
        newapi_ip="8.8.8.8",
    )
    _activate_required_for_test(repository)
    failed = await service.begin(MODEL_ID)
    pending = await service.begin(MODEL_ID)
    assert failed.dispatch is not None
    assert pending.dispatch is not None
    service.mark_dispatched(failed.dispatch)

    failure_time = "2099-08-31T00:00:00+00:00"
    with sqlite3.connect(repository.database_path) as database:
        database.execute(
            """
            UPDATE provider_chat_attempts
            SET status = 'failed', result_class = 'hard_failure',
                error_code = 'provider_chat_http_401',
                updated_at = ?, completed_at = ?
            WHERE tenant_id = 'local' AND id = ? AND dispatched = 1
            """,
            (failure_time, failure_time, failed.dispatch.attempt_id),
        )
    stale_receipts = repository.list_chat_control_receipts("local")
    stale_run = next(
        item
        for item in stale_receipts["runs"]
        if item["id"] == failed.dispatch.run_id
    )
    assert stale_run["status"] == "running"
    assert stale_run["hard_failure"] == 0

    monkeypatch.setattr(service, "ensure_dispatch_current", lambda _dispatch: None)
    with pytest.raises(RouterServiceError) as exc_info:
        service.mark_dispatched(pending.dispatch)
    assert exc_info.value.code == "provider_chat_policy_or_qualification_changed"
    rejected = repository.list_chat_control_receipts("local")
    pending_attempt = next(
        item
        for item in rejected["attempts"]
        if item["id"] == pending.dispatch.attempt_id
    )
    assert pending_attempt["dispatched"] == 0
    assert pending_attempt["status"] == "failed"

    restarted_repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    restarted = ProviderChatStableService(
        ModelRouterService(
            restarted_repository,
            egress_policy=ProviderEgressPolicy(
                resolver=lambda _host, _port: ["8.8.8.8"]
            ),
        )
    )
    qualification, reason = restarted.control.current_qualification(
        connection_id=newapi_id,
        model_id=MODEL_ID,
        capability="chat_text",
    )
    assert qualification is None
    assert reason == "provider_chat_hard_failure_recertification_required"
    ready, readiness_reason = restarted.readiness(MODEL_ID, "chat_text")
    assert ready is False
    assert readiness_reason == "provider_chat_required_gate_degraded"


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
