from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

import ai_research_control.app as app_module


def record() -> dict[str, Any]:
    return {
        "run_id": "ar0_test",
        "fixture_id": "inspect-smoke-v1",
        "case_id": "success",
        "tenant_id": "local",
        "project_id": "local",
        "actor_id": "local",
        "phase": "queued",
        "outcome": None,
        "inspect_status": None,
        "cancel_requested": False,
        "cancel_applied": False,
        "evidence_state": "pending",
        "error_type": None,
        "error_message": None,
        "replay_verified": False,
        "mlflow_run_id": None,
        "created_at": "2026-08-23T00:00:00Z",
        "started_at": None,
        "terminal_at": None,
        "updated_at": "2026-08-23T00:00:00Z",
    }


class FakeStore:
    def get(self, run_id: str) -> dict[str, Any]:
        if run_id != "ar0_test":
            raise KeyError(run_id)
        return record()

    def list(self, *, after_run_id: str | None, limit: int) -> list[dict[str, Any]]:
        return [record()]

    def events(self, run_id: str, after_sequence: int) -> list[dict[str, Any]]:
        self.get(run_id)
        return [
            {
                "sequence": 1,
                "event_type": "run.queued",
                "payload": {},
                "created_at": "2026-08-23T00:00:00Z",
            }
        ]


class FakeService:
    def __init__(self, settings: object) -> None:
        self.store = FakeStore()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def readiness(self) -> dict[str, str]:
        return {"controlLedger": "ready", "worker": "ready", "tracking": "ready"}

    async def create_run(self, request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        return record(), True

    async def cancel(self, run_id: str) -> dict[str, Any]:
        return self.store.get(run_id)


def test_frozen_http_contract(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "ResearchService", FakeService)
    with TestClient(app_module.app) as client:
        assert client.get("/healthz").json() == {"status": "alive"}
        assert client.get("/readyz").status_code == 200
        payload = {
            "fixtureId": "inspect-smoke-v1",
            "caseId": "success",
            "idempotencyKey": "fixture:key-001",
        }
        created = client.post("/api/v1/runs", json=payload)
        assert created.status_code == 201
        assert created.json()["runId"] == "ar0_test"
        assert client.get("/api/v1/runs").json()["items"][0]["runId"] == "ar0_test"
        assert client.get("/api/v1/runs/ar0_test/events?afterSeq=0").json()[
            "nextSequence"
        ] == 1
        assert client.post("/api/v1/runs/ar0_test/cancel").status_code == 200
        assert client.get("/docs").status_code == 404


def test_request_surface_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "ResearchService", FakeService)
    with TestClient(app_module.app) as client:
        base = {
            "fixtureId": "inspect-smoke-v1",
            "caseId": "success",
            "idempotencyKey": "fixture:key-001",
        }
        assert client.post("/api/v1/runs", json={**base, "tenantId": "other"}).status_code == 422
        assert client.post("/api/v1/runs", json={**base, "command": "id"}).status_code == 422
        assert client.get("/healthz", headers={"Host": "evil.example"}).status_code == 400
