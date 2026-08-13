from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.skills.api import (
    router,
    set_skill_lifecycle_service_for_tests,
    set_skill_manager_for_tests,
)
from server.skills.lifecycle import (
    SkillLifecycleMigrationService,
    SkillLifecycleStore,
)
from server.skills.skill_manager import SkillManager


def _client(tmp_path: Path, *, enabled: bool) -> tuple[TestClient, SkillLifecycleStore]:
    manager = SkillManager(
        installed_dir=tmp_path / "installed",
        tmp_dir=tmp_path / "tmp",
    )
    store = SkillLifecycleStore(tmp_path / "lifecycle", enabled=enabled)
    service = SkillLifecycleMigrationService(store=store, manager=manager)
    set_skill_manager_for_tests(manager)
    set_skill_lifecycle_service_for_tests(service)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), store


def teardown_function() -> None:
    set_skill_lifecycle_service_for_tests(None)
    set_skill_manager_for_tests(None)


def test_status_is_always_readable_and_disabled_migration_is_hidden(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, enabled=False)

    status = client.get("/api/skills/lifecycle/status")
    assert status.status_code == 200
    assert status.json()["enabled"] is False
    assert status.json()["version"] == "skill-lifecycle-v1"
    assert client.get("/api/skills/lifecycle/migration").status_code == 200

    response = client.post(
        "/api/skills/lifecycle/migration", json={"confirmed": True}
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "skill_lifecycle_disabled"


def test_migration_requires_explicit_confirmation(tmp_path: Path) -> None:
    client, store = _client(tmp_path, enabled=True)

    rejected = client.post(
        "/api/skills/lifecycle/migration", json={"confirmed": False}
    )
    assert rejected.status_code == 409
    assert (
        rejected.json()["detail"]["code"]
        == "skill_lifecycle_confirmation_required"
    )

    accepted = client.post(
        "/api/skills/lifecycle/migration", json={"confirmed": True}
    )
    assert accepted.status_code == 200
    assert accepted.json()["counts"]["total"] == 0
    assert store.status()["counts"]["versions"] == 0


def test_corrupt_installed_metadata_returns_structured_unavailable(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, enabled=True)
    metadata = tmp_path / "installed" / "installed.json"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text("{not-json", encoding="utf-8")

    response = client.get("/api/skills/lifecycle/migration")
    assert response.status_code == 503
    assert (
        response.json()["detail"]["code"]
        == "skill_lifecycle_installed_metadata_unavailable"
    )
    assert client.get("/api/skills/installed").status_code == 503
    assert metadata.read_text(encoding="utf-8") == "{not-json"
