from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from server.main import app
from server.world import api as world_api
from server.world.providers.marble import MarbleWorldProvider
from server.world.settings import MarbleSettingsError, MarbleSettingsStore


@pytest.fixture
def settings_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WORLD_PROVIDER", "mock")
    store = MarbleSettingsStore(tmp_path / "world-settings")
    world_api.set_world_settings_for_tests(store)
    yield store
    world_api.set_world_settings_for_tests(
        MarbleSettingsStore(tmp_path / "world-settings-reset")
    )


@pytest.mark.asyncio
async def test_marble_key_is_validated_encrypted_and_never_returned(
    settings_store: MarbleSettingsStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def validate(api_key: str) -> float:
        assert api_key == "wlt-secret-value"
        return 42.5

    monkeypatch.setattr(world_api, "_validate_marble_key", validate)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        saved = await client.put(
            "/api/world-generations/settings/marble",
            json={"api_key": "wlt-secret-value", "enabled": True},
        )
        fetched = await client.get("/api/world-generations/settings/marble")

    assert saved.status_code == 200
    assert saved.json() == fetched.json()
    assert saved.json()["configured"] is True
    assert saved.json()["enabled"] is True
    assert saved.json()["remaining_credits"] == 42.5
    assert "wlt-secret-value" not in saved.text
    assert "wlt-secret-value" not in settings_store.config_path.read_text("utf-8")
    credential_payload = json.loads(
        settings_store.credentials.storage_path.read_text("utf-8")
    )
    assert "wlt-secret-value" not in json.dumps(credential_payload)
    assert settings_store.resolve_api_key() == "wlt-secret-value"


@pytest.mark.asyncio
async def test_invalid_marble_key_is_not_persisted(
    settings_store: MarbleSettingsStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject(_: str) -> float:
        raise MarbleSettingsError("invalid key")

    monkeypatch.setattr(world_api, "_validate_marble_key", reject)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put(
            "/api/world-generations/settings/marble",
            json={"api_key": "bad-key", "enabled": True},
        )

    assert response.status_code == 400
    assert settings_store.public().configured is False


def test_enabled_settings_supply_saved_key_to_provider(
    settings_store: MarbleSettingsStore,
) -> None:
    settings_store.save(
        api_key="saved-marble-key",
        enabled=True,
        remaining_credits=10,
    )

    provider = world_api.get_provider()

    assert isinstance(provider, MarbleWorldProvider)
    assert provider.api_key == "saved-marble-key"


def test_disabled_ui_setting_overrides_marble_environment(
    settings_store: MarbleSettingsStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORLD_PROVIDER", "marble")
    settings_store.save(
        api_key="saved-but-disabled-key",
        enabled=False,
        remaining_credits=10,
    )

    assert world_api.active_provider_name() == "mock"


@pytest.mark.asyncio
async def test_clear_marble_settings_disables_real_mode(
    settings_store: MarbleSettingsStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def validate(_: str) -> float:
        return 3

    monkeypatch.setattr(world_api, "_validate_marble_key", validate)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.put(
            "/api/world-generations/settings/marble",
            json={"api_key": "key-to-clear", "enabled": True},
        )
        response = await client.delete("/api/world-generations/settings/marble")

    assert response.status_code == 200
    assert response.json() == {
        "configured": False,
        "enabled": False,
        "masked_key": None,
        "remaining_credits": None,
    }
    assert world_api.active_provider_name() == "mock"
