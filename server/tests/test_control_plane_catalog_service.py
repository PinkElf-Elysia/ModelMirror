from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock
import sqlite3

import httpx
import pytest

from server.model_router.chat_canary import ProviderChatCanaryService
from server.model_router.control_plane_catalog import ControlPlaneCatalogService
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService
from server.tests.test_provider_chat_control_service import _connection, _certify, MODEL_ID
from server.model_router.schemas import ProviderChatControlPolicyUpdate, ProviderChatControlRouteUpdate
from server.model_router.chat_control import ProviderChatControlService
from server.model_router.workload_control import ProviderWorkloadControlService
from server.model_router.workload_control import ENTRY_FEATURE_FLAGS
from server.model_router.catalog_admission import SPECIALIZED_ENTRY
from server.model_router.schemas import (
    ProviderWorkloadActivationRequest, ProviderWorkloadBindingUpdate,
    ProviderWorkloadPolicyUpdate,
)


def test_online_inventory_without_admission_is_not_invocable(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "false")
    service = _service(tmp_path)
    connection = _connection(service.repository, name="newAPI", kind="newapi")
    offering = service.offerings(connection_id=connection).offerings[0]
    assert offering.connection_health == "online"
    assert offering.inventory_status == "active"
    assert not offering.invocable
    assert service.public_catalog().models == []
    assert len(service.public_catalog(include_unavailable=True).models) == 1
    assert service.overview().discovered_model_count == 1


def test_certified_inventory_does_not_activate_a_route(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    service = _service(tmp_path)
    connection = _connection(service.repository, name="newAPI", kind="newapi")
    _certify(service.repository, connection, "chat_text")
    row = service.offerings(connection_id=connection).offerings[0]
    assert row.verification_status == "contract_verified"
    assert not row.invocable


def test_active_managed_route_and_expiry_change_catalog_revision(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    service = _service(tmp_path)
    connection = _connection(service.repository, name="newAPI", kind="newapi")
    _certify(service.repository, connection, "chat_text")
    ProviderChatControlService(service.router_service).update_policy(ProviderChatControlPolicyUpdate(
        expected_revision=0, mode="newapi_preferred", stable_model_ids=[MODEL_ID],
        routes=[ProviderChatControlRouteUpdate(capability="chat_text", connection_ids=[connection])],
    ))
    active = service.public_catalog(include_unavailable=True)
    assert active.models[0].operations[0].invocable
    with sqlite3.connect(service.repository.database_path) as db:
        db.execute("UPDATE provider_qualification_events SET completed_at='2020-01-01T00:00:00+00:00', expires_at='2020-01-02T00:00:00+00:00' WHERE tenant_id='local'")
        db.execute("UPDATE provider_qualification_intervals SET valid_from='2020-01-01T00:00:00+00:00' WHERE tenant_id='local'")
    expired = service.public_catalog(include_unavailable=True)
    assert not expired.models[0].operations[0].invocable
    assert expired.catalog_revision != active.catalog_revision
    assert service.offerings().offerings[0].verification_status != "contract_verified"


def test_catalog_keeps_entry_admission_separate_and_has_no_write_or_network(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_CONTROL_ROUTE_AGENT_ENABLED", "true")
    monkeypatch.setenv("MODEL_CONTROL_TEAM_CHAT_ENABLED", "true")
    service = _service(tmp_path)
    connection = _connection(service.repository, name="newAPI", kind="newapi")
    other = _connection(service.repository, name="Unselected", kind="openrouter")
    for identifier in (connection, other):
        _certify(service.repository, identifier, "chat_text")
    control = ProviderWorkloadControlService(service.router_service)
    for entry in ("route_agent", "team_chat"):
        saved = control.update_policy(entry, ProviderWorkloadPolicyUpdate(
            expected_revision=0, bindings=[ProviderWorkloadBindingUpdate(
                execution_shape="chat_text", model_id=MODEL_ID, connection_id=connection,
            )],
        ))
        if entry == "route_agent":
            control.activate(entry, ProviderWorkloadActivationRequest(
                expected_revision=saved.revision, no_open_p0_p1=True, acknowledge_fail_closed=True,
            ))
    monkeypatch.setattr(httpx.AsyncClient, "send", Mock(side_effect=AssertionError("network from Catalog")))
    monkeypatch.setattr(service.repository, "sync_chat_control_gate_epoch", Mock(side_effect=AssertionError("Catalog wrote gate")))
    with sqlite3.connect(service.repository.database_path) as db:
        before = list(db.iterdump())
    first = service.public_catalog(include_unavailable=True)
    second = service.public_catalog(include_unavailable=True)
    assert first.catalog_revision == second.catalog_revision
    readiness = first.models[0].operations[0]
    assert readiness.invocable
    entries = {entry.entry_id: entry for entry in readiness.managed_readiness}
    assert entries["route_agent"].available
    assert not entries["team_chat"].available
    assert "chat_default" not in entries
    assert not control.public_status("team_chat", MODEL_ID, "chat_text").available
    # A different connection with the same certified model cannot borrow a binding.
    offerings = {row.connection_id: row for row in service.offerings().offerings}
    assert offerings[connection].invocable
    assert not offerings[other].invocable
    assert service.overview().discovered_model_count == 1
    with sqlite3.connect(service.repository.database_path) as db:
        assert list(db.iterdump()) == before
    public = first.model_dump_json()
    for private in (connection, other, "connection_id", "certification_id", "tenant_id", "policy_fingerprint", "newapi.example"):
        assert private not in public


def test_default_catalog_snapshot_cannot_override_managed_block(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "true")
    service = _service(tmp_path, general_catalog=SimpleNamespace(
        source="bundled", stale=False, synced_at="2026-10-09T00:00:00+00:00",
        models=[SimpleNamespace(invocation_id=MODEL_ID, invocable=True,
                                interaction_status="ready", availability="live", operations=["chat"])],
    ))
    connection = _connection(service.repository, name="newAPI", kind="newapi")
    _certify(service.repository, connection, "chat_text")
    ProviderChatControlService(service.router_service).update_policy(ProviderChatControlPolicyUpdate(
        expected_revision=0, mode="newapi_preferred", stable_model_ids=[MODEL_ID],
        routes=[ProviderChatControlRouteUpdate(capability="chat_text", connection_ids=[connection])],
    ))
    with sqlite3.connect(service.repository.database_path) as db:
        db.execute("UPDATE provider_qualification_events SET completed_at='2020-01-01T00:00:00+00:00', expires_at='2020-01-02T00:00:00+00:00' WHERE tenant_id='local'")
        db.execute("UPDATE provider_qualification_intervals SET valid_from='2020-01-01T00:00:00+00:00' WHERE tenant_id='local'")
    readiness = service.public_catalog(include_unavailable=True).models[0].operations[0]
    assert not readiness.invocable
    assert "provider_chat_certification_expired" in readiness.reason_codes
    # Disabling the deployment gate leaves the old default path unchanged.
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED", "false")
    legacy = service.public_catalog(include_unavailable=True).models[0].operations[0]
    assert legacy.invocable
    assert not any(entry.available for entry in legacy.managed_readiness)


def _service(tmp_path, **catalogs) -> ControlPlaneCatalogService:
    router = ModelRouterService(SQLiteRouterRepository.open(tmp_path))
    return ControlPlaneCatalogService(router, **catalogs)


@pytest.mark.parametrize("operation", sorted(SPECIALIZED_ENTRY))
def test_specialized_snapshot_cannot_override_missing_managed_binding(
    tmp_path, monkeypatch, operation,
):
    entry, shape = SPECIALIZED_ENTRY[operation]
    for flag in ENTRY_FEATURE_FLAGS[entry]:
        monkeypatch.setenv(flag, "true")
    snapshot = SimpleNamespace(stale=False, profiles=[SimpleNamespace(
        model_id=MODEL_ID, pricing=[], operation_readiness=[SimpleNamespace(
            operation=operation, interaction_status="ready",
            availability_status="available", verification_status="verified",
        )],
    )])
    service = _service(tmp_path, audio_catalog=snapshot)
    control = ProviderWorkloadControlService(service.router_service)
    control.update_policy(entry, ProviderWorkloadPolicyUpdate(
        expected_revision=0, bindings=[],
    ))
    # Corrupt an already-required synthetic policy by removing its bindings.
    # This is not an activation bypass in product code.
    with sqlite3.connect(service.repository.database_path) as db:
        db.execute(
            "UPDATE provider_workload_policies SET status='managed_required' "
            "WHERE tenant_id='local' AND entry_id=?", (entry,),
        )
    status = control.public_status(entry, MODEL_ID, shape)
    assert status.blocks_before_dispatch
    blocked = service.public_catalog(include_unavailable=True)
    readiness = blocked.models[0].operations[0]
    assert not readiness.invocable
    assert status.reason_code in readiness.reason_codes
    for flag in ENTRY_FEATURE_FLAGS[entry]:
        monkeypatch.setenv(flag, "false")
    legacy = service.public_catalog(include_unavailable=True)
    assert legacy.models[0].operations[0].invocable
    assert legacy.catalog_revision != blocked.catalog_revision


def test_canary_only_model_is_included_before_available_filter(
    tmp_path, monkeypatch
) -> None:
    catalog = SimpleNamespace(
        source="bundled",
        stale=False,
        synced_at="2026-08-21T00:00:00+00:00",
        models=[
            SimpleNamespace(
                invocation_id="provider/canary-only",
                invocable=False,
                interaction_status="planned",
                availability="offline",
                operations=["chat"],
            )
        ],
    )
    monkeypatch.setattr(
        ProviderChatCanaryService,
        "enabled",
        lambda *_args, **_kwargs: True,
    )
    admin_status = Mock(
        return_value=SimpleNamespace(
            policy_enabled=True,
            connections=[
                SimpleNamespace(
                    models=[
                        SimpleNamespace(
                            model_id="provider/canary-only",
                            available=True,
                        )
                    ]
                )
            ],
        )
    )
    monkeypatch.setattr(ProviderChatCanaryService, "admin_status", admin_status)

    service = _service(tmp_path, general_catalog=catalog)
    response = service.public_catalog()

    assert [item.model_id for item in response.models] == ["provider/canary-only"]
    readiness = response.models[0].operations[0]
    assert readiness.invocable is True
    assert readiness.access_modes == ["default", "newapi_canary"]
    assert "newapi_canary_available" in readiness.reason_codes
    overview = service.overview()
    assert overview.operation_counts[0].invocable == 1
    assert admin_status.call_count == 2


def test_catalog_stale_flag_describes_all_pages_not_only_current_page(tmp_path) -> None:
    general = SimpleNamespace(
        source="bundled",
        stale=False,
        synced_at="2026-08-21T00:00:00+00:00",
        models=[
            SimpleNamespace(
                invocation_id="a/current",
                invocable=True,
                interaction_status="ready",
                availability="live",
                operations=["chat"],
            )
        ],
    )
    image = SimpleNamespace(
        stale=True,
        synced_at="2026-08-20T00:00:00+00:00",
        profiles=[
            SimpleNamespace(
                model_id="z/stale",
                operation_readiness=[
                    SimpleNamespace(
                        operation="generate_image",
                        interaction_status="ready",
                        availability_status="available",
                        verification_status="verified",
                    )
                ],
                pricing=[],
                price_per_generation_usd=None,
            )
        ],
    )

    first_page = _service(
        tmp_path,
        general_catalog=general,
        image_catalog=image,
    ).public_catalog(include_unavailable=True, limit=1)

    assert first_page.models[0].model_id == "a/current"
    assert first_page.models[0].operations[0].stale is False
    assert first_page.stale is True
    assert first_page.next_cursor is not None


def test_internal_projection_reads_beyond_one_repository_page(tmp_path) -> None:
    service = _service(tmp_path)
    first = [{"model_id": f"model-{index}"} for index in range(5_000)]
    final = [{"model_id": "model-5000"}]
    reader = Mock(side_effect=[first, final])
    service.repository.list_catalog_models = reader

    rows = service._all_catalog_models()

    assert len(rows) == 5_001
    assert reader.call_args_list[0].kwargs["offset"] == 0
    assert reader.call_args_list[1].kwargs["offset"] == 5_000
