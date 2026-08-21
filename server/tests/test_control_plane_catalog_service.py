from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from server.model_router.chat_canary import ProviderChatCanaryService
from server.model_router.control_plane_catalog import ControlPlaneCatalogService
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService


def _service(tmp_path, **catalogs) -> ControlPlaneCatalogService:
    router = ModelRouterService(SQLiteRouterRepository(tmp_path))
    return ControlPlaneCatalogService(router, **catalogs)


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
