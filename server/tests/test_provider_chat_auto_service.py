from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from server.model_router.chat_auto import (
    AUTO_SIDECAR_ATTEMPTS_NOT_OBSERVED,
    ProviderChatAutoAuditService,
)
from server.model_router.chat_control import ProviderChatControlService
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import ProviderChatControlPolicyUpdate
from server.model_router.service import ModelRouterService, RouterServiceError


def _service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    auto_enabled: bool = True,
) -> tuple[
    ProviderChatAutoAuditService,
    ProviderChatControlService,
    SQLiteRouterRepository,
]:
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    repository = SQLiteRouterRepository(tmp_path, master_key=b"x" * 32)
    router_service = ModelRouterService(repository)
    control = ProviderChatControlService(router_service)
    control.update_policy(
        ProviderChatControlPolicyUpdate(
            expected_revision=0,
            mode="legacy",
            auto_enabled=auto_enabled,
        )
    )
    return ProviderChatAutoAuditService(router_service), control, repository


def test_auto_gate_defaults_to_no_evidence_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _control, repository = _service(
        tmp_path, monkeypatch, auto_enabled=False
    )

    assert service.enabled() is False
    assert service.begin("auto", strategy="auto_native") is None
    assert repository.list_chat_control_receipts("local")["runs"] == []


def test_sidecar_is_one_attempt_and_marks_internal_attempts_unobserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, control, repository = _service(tmp_path, monkeypatch)

    run = service.begin(
        "auto", strategy="auto_sidecar", sidecar_boundary=True
    )
    assert run is not None
    attempt = service.claim_attempt(
        run,
        position=0,
        connection_id=None,
        provider_kind="omniroute",
    )
    service.mark_dispatched(run, attempt)
    service.complete_attempt(
        run,
        attempt,
        status="succeeded",
        result_class="success",
        actual_model="provider/model",
        ttft_ms=12.0,
        e2e_ms=34.0,
        total_tokens=7,
    )
    service.complete_run(
        run,
        status="succeeded",
        result_class="success",
        actual_model="provider/model",
        ttft_ms=12.0,
        e2e_ms=34.0,
        total_tokens=7,
    )

    receipts = repository.list_chat_control_receipts("local")
    assert len(receipts["runs"]) == 1
    assert receipts["runs"][0]["gateway"] == "auto"
    assert receipts["runs"][0]["primary_newapi"] == 0
    assert receipts["runs"][0]["is_real_user"] == 1
    assert receipts["runs"][0]["reason_codes_json"] == (
        f'["{AUTO_SIDECAR_ATTEMPTS_NOT_OBSERVED}"]'
    )
    assert len(receipts["attempts"]) == 1
    assert receipts["attempts"][0]["provider_kind"] == "omniroute"
    assert receipts["attempts"][0]["connection_id"] is None
    assert receipts["attempts"][0]["dispatched"] == 1
    admin_receipt = control.receipts().runs[0]
    assert admin_receipt.gateway == "auto"
    assert admin_receipt.strategy == "auto_sidecar"


def test_native_attempts_are_independent_and_tenant_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _control, repository = _service(tmp_path, monkeypatch)
    run = service.begin("auto", strategy="auto_native")
    assert run is not None

    first = service.claim_attempt(
        run,
        position=0,
        connection_id="conn-a",
        provider_kind="newapi",
    )
    service.mark_dispatched(run, first)
    service.complete_attempt(
        run,
        first,
        status="failed",
        result_class="transient_failure",
        error_code="provider_chat_http_503",
    )
    second = service.claim_attempt(
        run,
        position=1,
        connection_id="conn-b",
        provider_kind="openrouter",
    )
    service.mark_dispatched(run, second)
    service.complete_attempt(
        run,
        second,
        status="succeeded",
        result_class="success",
        actual_model="provider/model-b",
    )
    service.complete_run(
        run,
        status="succeeded",
        result_class="success",
        reason_codes=["provider_chat_auto_fallback_used"],
        actual_model="provider/model-b",
    )

    receipts = repository.list_chat_control_receipts("local")
    assert [item["position"] for item in receipts["attempts"]] == [0, 1]
    assert [item["connection_id"] for item in receipts["attempts"]] == [
        "conn-a",
        "conn-b",
    ]
    assert repository.list_chat_control_receipts("other")["runs"] == []


def test_policy_change_blocks_dispatch_before_provider_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, control, repository = _service(tmp_path, monkeypatch)
    run = service.begin("auto", strategy="auto_native")
    assert run is not None
    attempt = service.claim_attempt(
        run,
        position=0,
        connection_id="conn-a",
        provider_kind="newapi",
    )
    control.update_policy(
        ProviderChatControlPolicyUpdate(
            expected_revision=1,
            mode="legacy",
            auto_enabled=False,
        )
    )

    with pytest.raises(RouterServiceError) as exc_info:
        service.mark_dispatched(run, attempt)

    assert exc_info.value.code == "provider_chat_auto_policy_changed"
    receipts = repository.list_chat_control_receipts("local")
    assert receipts["attempts"][0]["dispatched"] == 0
    assert receipts["attempts"][0]["status"] == "failed"
    assert receipts["runs"][0]["status"] == "failed"


def test_auto_evidence_never_stores_prompt_or_model_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _control, repository = _service(tmp_path, monkeypatch)
    run = service.begin("auto", strategy="auto_sidecar", sidecar_boundary=True)
    assert run is not None
    attempt = service.claim_attempt(
        run,
        position=0,
        connection_id=None,
        provider_kind="omniroute",
    )
    service.mark_dispatched(run, attempt)
    service.complete_attempt(
        run,
        attempt,
        status="succeeded",
        result_class="success",
        actual_model="provider/model",
    )
    service.complete_run(
        run,
        status="succeeded",
        result_class="success",
        actual_model="provider/model",
    )

    with sqlite3.connect(repository.database_path) as database:
        dump = "\n".join(database.iterdump())
    assert "private prompt" not in dump
    assert "model answer" not in dump
