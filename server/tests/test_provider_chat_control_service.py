from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from server.model_router.chat_control import ProviderChatControlService
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import (
    ProviderChatControlPolicyUpdate,
    ProviderChatControlRouteUpdate,
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
