from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from server.model_router.chat_gate import evaluate_provider_chat_gate
from server.model_router.chat_control import ProviderChatControlService
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderChatControlPolicyUpdate,
    ProviderChatControlRouteUpdate,
    ProviderChatRequiredActivationRequest,
    RouterConnectionCreate,
    RouterConnectionUpdate,
)
from server.model_router.service import ModelRouterService, RouterServiceError


MODEL_ID = "provider/model"


def _connection(
    repository: SQLiteRouterRepository,
    *,
    name: str,
    kind: str,
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
        offerings=[
            {
                "model_id": MODEL_ID,
                "operation": "chat",
                "access_mode": "managed",
                "capability_source": "connection_scope",
            }
        ],
        model_count=1,
        truncated=False,
        catalog_fingerprint=f"catalog-{connection.id}",
        observed_at="2026-08-21T00:00:00+00:00",
    )
    return connection.id


def _certify(
    repository: SQLiteRouterRepository,
    connection_id: str,
    capability: str,
) -> str:
    certification_id = f"cert-{connection_id}-{capability}"
    row, created = repository.claim_chat_certification(
        "local",
        certification_id=certification_id,
        connection_id=connection_id,
        connection_fingerprint=repository.connection_config_fingerprint(
            "local", connection_id
        ),
        contract_version="modelmirror-provider-chat-v1",
        capability=capability,
        requested_model=MODEL_ID,
        idempotency_key_hash=f"hash-{connection_id}-{capability}",
    )
    assert created is True
    repository.complete_chat_certification(
        "local",
        str(row["id"]),
        status="passed",
        checks={"capability_verified": True},
        warning_codes=[],
        actual_model=MODEL_ID,
    )
    return certification_id


def _service(tmp_path: Path) -> tuple[ProviderChatControlService, SQLiteRouterRepository]:
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    return ProviderChatControlService(ModelRouterService(repository)), repository


def test_default_policy_is_legacy_and_r5b_public_status_never_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MODEL_CONTROL_CHAT_ENABLED", raising=False)
    service, _repository = _service(tmp_path)

    policy = service.get_policy()
    public = service.public_status(MODEL_ID, "chat_text")

    assert policy.configured_mode == "legacy"
    assert policy.effective_mode == "legacy"
    assert policy.feature_enabled is False
    assert policy.data_plane_integrated is True
    assert public.available is False
    assert public.would_block is False
    assert public.reason_code == "provider_chat_control_feature_disabled"


def test_atomic_policy_accepts_qualified_newapi_primary_and_managed_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    service, repository = _service(tmp_path)
    newapi_id = _connection(repository, name="newAPI", kind="newapi")
    openrouter_id = _connection(repository, name="OpenRouter", kind="openrouter")
    _certify(repository, newapi_id, "chat_text")
    _certify(repository, openrouter_id, "chat_text")

    policy = service.update_policy(
        ProviderChatControlPolicyUpdate(
            expected_revision=0,
            mode="newapi_preferred",
            stable_model_ids=[MODEL_ID],
            routes=[
                ProviderChatControlRouteUpdate(
                    capability="chat_text",
                    connection_ids=[newapi_id, openrouter_id],
                )
            ],
        )
    )

    assert policy.revision == 1
    assert policy.effective_mode == "newapi_preferred"
    assert policy.data_plane_integrated is True
    assert len(policy.qualifications) == 2
    assert all(item.valid for item in policy.qualifications)
    public = service.public_status(MODEL_ID, "chat_text")
    assert public.data_plane_integrated is True
    assert public.available is True
    assert public.would_block is False
    assert public.reason_code == "qualified"
    serialized = policy.model_dump_json()
    assert "newAPI-secret" not in serialized
    assert "OpenRouter-secret" not in serialized


def test_text_primary_must_be_newapi(tmp_path: Path) -> None:
    service, repository = _service(tmp_path)
    openrouter_id = _connection(repository, name="OpenRouter", kind="openrouter")
    _certify(repository, openrouter_id, "chat_text")

    with pytest.raises(RouterServiceError) as exc_info:
        service.update_policy(
            ProviderChatControlPolicyUpdate(
                expected_revision=0,
                mode="legacy",
                stable_model_ids=[MODEL_ID],
                routes=[
                    ProviderChatControlRouteUpdate(
                        capability="chat_text",
                        connection_ids=[openrouter_id],
                    )
                ],
            )
        )
    assert exc_info.value.code == "provider_chat_text_primary_newapi_required"


def test_capability_certifications_are_not_interchangeable(tmp_path: Path) -> None:
    service, repository = _service(tmp_path)
    newapi_id = _connection(repository, name="newAPI", kind="newapi")
    _certify(repository, newapi_id, "chat_text")

    with pytest.raises(RouterServiceError) as exc_info:
        service.update_policy(
            ProviderChatControlPolicyUpdate(
                expected_revision=0,
                mode="legacy",
                stable_model_ids=[MODEL_ID],
                routes=[
                    ProviderChatControlRouteUpdate(
                        capability="chat_text", connection_ids=[newapi_id]
                    ),
                    ProviderChatControlRouteUpdate(
                        capability="chat_tools", connection_ids=[newapi_id]
                    ),
                ],
            )
        )
    assert (
        exc_info.value.code
        == "provider_chat_capability_certification_required"
    )


def test_connection_change_derives_stale_qualification_without_rewrite(
    tmp_path: Path,
) -> None:
    service, repository = _service(tmp_path)
    newapi_id = _connection(repository, name="newAPI", kind="newapi")
    _certify(repository, newapi_id, "chat_text")
    saved = service.update_policy(
        ProviderChatControlPolicyUpdate(
            expected_revision=0,
            mode="legacy",
            stable_model_ids=[MODEL_ID],
            routes=[
                ProviderChatControlRouteUpdate(
                    capability="chat_text", connection_ids=[newapi_id]
                )
            ],
        )
    )
    assert saved.qualifications[0].valid is True

    repository.update_connection(
        "local",
        newapi_id,
        RouterConnectionUpdate(base_url="https://changed.example/v1"),
    )
    stale = service.get_policy()
    assert stale.qualifications[0].valid is False
    assert stale.qualifications[0].reason_code in {
        "provider_connection_not_online",
        "provider_chat_catalog_stale",
        "provider_chat_capability_certification_stale",
    }


def test_expired_certification_invalidates_current_gate_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    service, repository = _service(tmp_path)
    newapi_id = _connection(repository, name="newAPI", kind="newapi")
    _certify(repository, newapi_id, "chat_text")
    saved = service.update_policy(
        ProviderChatControlPolicyUpdate(
            expected_revision=0,
            mode="newapi_preferred",
            stable_model_ids=[MODEL_ID],
            routes=[
                ProviderChatControlRouteUpdate(
                    capability="chat_text", connection_ids=[newapi_id]
                )
            ],
        )
    )
    assert saved.qualifications[0].valid is True
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM provider_chat_gate_epochs WHERE closed_at IS NULL"
        ).fetchone()[0] == 1
        connection.execute(
            """
            UPDATE provider_chat_certifications
            SET completed_at = '2020-01-01T00:00:00+00:00'
            WHERE tenant_id = 'local' AND connection_id = ?
            """,
            (newapi_id,),
        )

    stale = service.get_policy()
    assert stale.qualifications[0].valid is False
    assert (
        stale.qualifications[0].reason_code
        == "provider_chat_certification_expired"
    )
    with sqlite3.connect(repository.database_path) as connection:
        epoch = connection.execute(
            "SELECT status, closed_at FROM provider_chat_gate_epochs"
        ).fetchone()
    assert epoch[0] == "invalidated"
    assert epoch[1] is not None


def test_required_mode_cannot_be_saved_before_r5e_gate(tmp_path: Path) -> None:
    service, _repository = _service(tmp_path)
    with pytest.raises(RouterServiceError) as exc_info:
        service.update_policy(
            ProviderChatControlPolicyUpdate(
                expected_revision=0,
                mode="newapi_required_default",
            )
        )
    assert exc_info.value.code == "provider_chat_required_activation_not_available"


def test_r5e_gate_truth_table_requires_volume_window_rate_models_and_zero_hard() -> None:
    ready = evaluate_provider_chat_gate(
        {
            "request_count": 500,
            "success_count": 495,
            "hard_failure_count": 0,
            "observed_days": 14,
            "model_successes": {MODEL_ID: 495},
        },
        stable_model_ids=[MODEL_ID],
    )
    assert ready.ready is True
    assert ready.success_rate == pytest.approx(0.99)

    blocked = evaluate_provider_chat_gate(
        {
            "request_count": 499,
            "success_count": 494,
            "hard_failure_count": 1,
            "observed_days": 13.99,
            "model_successes": {MODEL_ID: 9},
        },
        stable_model_ids=[MODEL_ID],
    )
    assert blocked.ready is False
    assert set(blocked.blocking_reason_codes) == {
        "provider_chat_gate_request_count_insufficient",
        "provider_chat_gate_observation_window_insufficient",
        "provider_chat_gate_success_rate_insufficient",
        "provider_chat_gate_model_samples_insufficient",
        "provider_chat_gate_hard_failure_observed",
    }


def test_hard_failure_invalidates_saved_qualification_until_recertification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    service, repository = _service(tmp_path)
    newapi_id = _connection(repository, name="newAPI", kind="newapi")
    _certify(repository, newapi_id, "chat_text")
    policy = service.update_policy(
        ProviderChatControlPolicyUpdate(
            expected_revision=0,
            mode="newapi_preferred",
            stable_model_ids=[MODEL_ID],
            routes=[
                ProviderChatControlRouteUpdate(
                    capability="chat_text", connection_ids=[newapi_id]
                )
            ],
        )
    )
    epoch = repository.get_open_chat_control_gate_epoch(
        "local", policy.policy_fingerprint
    )
    assert epoch is not None
    repository.claim_chat_control_run(
        "local",
        run_id="hard-run",
        policy_fingerprint=policy.policy_fingerprint,
        capability="chat_text",
        requested_model=MODEL_ID,
        strategy="newapi_preferred",
        epoch_id=str(epoch["id"]),
        is_real_user=True,
        primary_newapi=True,
    )
    repository.claim_chat_control_attempt(
        "local",
        attempt_id="hard-attempt",
        run_id="hard-run",
        capability="chat_text",
        position=0,
        connection_id=newapi_id,
        provider_kind="newapi",
    )
    repository.mark_chat_control_attempt_dispatched("local", "hard-attempt")
    repository.complete_chat_control_attempt(
        "local",
        "hard-attempt",
        status="failed",
        result_class="hard_failure",
        error_code="provider_chat_model_mismatch",
    )
    repository.complete_chat_control_run(
        "local",
        "hard-run",
        status="failed",
        result_class="hard_failure",
        reason_codes=["provider_chat_model_mismatch"],
        hard_failure=True,
    )

    stale = service.get_policy()
    assert stale.qualifications[0].valid is False
    assert stale.qualifications[0].reason_code == (
        "provider_chat_hard_failure_recertification_required"
    )
    gate = service.gate()
    assert gate.required_active is False
    assert gate.epoch_status == "degraded"
    assert gate.request_count == 1
    assert gate.hard_failure_count == 1
    assert "provider_chat_gate_hard_failure_recertification_required" in (
        gate.blocking_reason_codes
    )


def test_ready_gate_requires_explicit_atomic_activation_and_hashes_correlation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    service, repository = _service(tmp_path)
    newapi_id = _connection(repository, name="newAPI", kind="newapi")
    _certify(repository, newapi_id, "chat_text")
    policy = service.update_policy(
        ProviderChatControlPolicyUpdate(
            expected_revision=0,
            mode="newapi_preferred",
            stable_model_ids=[MODEL_ID],
            routes=[
                ProviderChatControlRouteUpdate(
                    capability="chat_text", connection_ids=[newapi_id]
                )
            ],
        )
    )
    epoch = repository.get_open_chat_control_gate_epoch(
        "local", policy.policy_fingerprint
    )
    assert epoch is not None
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(
            """
            WITH RECURSIVE seq(i) AS (
                SELECT 0 UNION ALL SELECT i + 1 FROM seq WHERE i < 499
            )
            INSERT INTO provider_chat_runs (
                id, tenant_id, policy_fingerprint, epoch_id, capability,
                requested_model, actual_model, strategy, gateway, status,
                result_class, reason_codes_json, is_real_user, primary_newapi,
                client_cancelled, hard_failure, created_at, updated_at, completed_at
            )
            SELECT 'activation-run-' || i, 'local', ?, ?, 'chat_text', ?, ?,
                   'newapi_preferred', 'default', 'succeeded', 'success', '[]',
                   1, 1, 0, 0,
                   datetime('2026-08-01T00:00:00', '+' ||
                     CAST(i * 1209600.0 / 499 AS INTEGER) || ' seconds'),
                   datetime('2026-08-01T00:00:00', '+' ||
                     CAST(i * 1209600.0 / 499 AS INTEGER) || ' seconds'),
                   datetime('2026-08-01T00:00:00', '+' ||
                     CAST(i * 1209600.0 / 499 AS INTEGER) || ' seconds')
            FROM seq
            """,
            (policy.policy_fingerprint, epoch["id"], MODEL_ID, MODEL_ID),
        )
        connection.execute(
            """
            INSERT INTO provider_chat_attempts (
                id, tenant_id, run_id, capability, position, connection_id,
                provider_kind, dispatched, status, result_class, actual_model,
                created_at, updated_at, completed_at
            )
            SELECT 'activation-attempt-' || substr(id, 16), tenant_id, id,
                   capability, 0, ?, 'newapi', 1, 'succeeded', 'success',
                   actual_model, created_at, updated_at, completed_at
            FROM provider_chat_runs WHERE epoch_id = ?
            """,
            (newapi_id, epoch["id"]),
        )

    gate = service.gate()
    assert gate.ready is True
    assert gate.required_activation_available is True
    assert gate.request_count == 500
    activated = service.activate_required(
        ProviderChatRequiredActivationRequest(
            expected_revision=policy.revision,
            no_open_p0_p1=True,
            acknowledge_fail_closed=True,
            drills={name: True for name in gate.required_drills},
            newapi_correlation_reference="opaque-newapi-usage-log-reference",
            quota_decrement_verified=True,
            usage_log_verified=True,
            restart_persistence_verified=True,
        )
    )
    assert activated.required_active is True
    assert activated.configured_mode == "newapi_required_default"
    assert activated.approval_recorded is True
    assert activated.acceptance_evidence_complete is True
    assert activated.required_activation_available is False
    with sqlite3.connect(repository.database_path) as connection:
        dump = "\n".join(connection.iterdump())
    assert "opaque-newapi-usage-log-reference" not in dump
