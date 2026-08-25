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
        "cancel_requested_at": None,
        "cancel_applied_at": None,
        "terminal_at": None,
        "evidence_synced_at": None,
        "updated_at": "2026-08-23T00:00:00Z",
    }


class FakeStore:
    def get(self, run_id: str) -> dict[str, Any]:
        if run_id != "ar0_test":
            raise KeyError(run_id)
        return record()

    def list(self, *, after_run_id: str | None, limit: int, **_: object) -> list[dict[str, Any]]:
        return [record()]

    def summary(self) -> dict[str, Any]:
        return {
            "total": 1,
            "phases": {"queued": 1, "running": 0, "terminal": 0},
            "outcomes": {
                "success": 0,
                "task_error": 0,
                "cancelled": 0,
                "infrastructure_error": 0,
            },
            "evidence_states": {"pending": 1, "synced": 0, "failed": 0},
            "updated_at": "2026-08-23T00:00:00Z",
        }

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

    async def system_status(self) -> dict[str, object]:
        return {
            "status": "degraded",
            "checks": [
                {"id": "controlLedger", "status": "ready", "required": True},
                {"id": "worker", "status": "ready", "required": True},
                {"id": "tracking", "status": "ready", "required": True},
                {"id": "inspectView", "status": "not_ready", "required": False},
            ],
            "checkedAt": "2026-08-23T00:00:00Z",
        }

    async def evidence(self, run_id: str) -> dict[str, object]:
        self.store.get(run_id)
        return {
            "runId": run_id,
            "evidenceState": "synced",
            "integrityStatus": "verified",
            "integrityError": None,
            "verifiedAt": "2026-08-23T00:00:00Z",
            "receipt": {"runId": run_id},
            "artifacts": [
                {
                    "name": "eval-log.json",
                    "sizeBytes": 2,
                    "sha256": "a" * 64,
                    "downloadUrl": f"/api/v1/runs/{run_id}/artifacts/eval-log.json",
                }
            ],
            "mlflow": {"runId": "mlflow-run", "experimentId": "1", "traceId": "trace"},
            "outbox": {"state": "synced", "attemptCount": 0},
        }

    async def artifact(self, run_id: str, name: str) -> tuple[bytes, str]:
        self.store.get(run_id)
        if name != "eval-log.json":
            raise KeyError(name)
        return b"{}", "a" * 64

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
        assert client.get("/api/v1/runs/summary").json()["total"] == 1
        assert client.get("/api/v1/runs?caseId=success&phase=queued").status_code == 200
        assert client.get("/api/v1/system").json()["status"] == "degraded"
        assert client.get("/api/v1/runs/ar0_test/evidence").json()["integrityStatus"] == "verified"
        artifact = client.get("/api/v1/runs/ar0_test/artifacts/eval-log.json")
        assert artifact.content == b"{}"
        assert artifact.headers["x-artifact-sha256"] == "a" * 64
        assert artifact.headers["x-content-type-options"] == "nosniff"
        assert client.get("/api/v1/runs/ar0_test/events?afterSeq=0").json()[
            "nextSequence"
        ] == 1
        assert client.post("/api/v1/runs/ar0_test/cancel").status_code == 200
        assert client.get("/docs").status_code == 404
        deep_link = client.get("/runs/ar0_test/evidence", headers={"Accept": "text/html"})
        assert deep_link.status_code == 200
        assert "<title>模镜科研控制台</title>" in deep_link.text
        assert deep_link.headers["cache-control"] == "no-store"
        missing_api = client.get("/api/v1/does-not-exist", headers={"Accept": "text/html"})
        assert missing_api.status_code == 404
        assert missing_api.headers["content-type"].startswith("application/json")
        missing_asset = client.get("/assets/does-not-exist.js", headers={"Accept": "text/html"})
        assert missing_asset.status_code == 404
        assert "<title>模镜科研控制台</title>" not in missing_asset.text


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
        assert client.get("/api/v1/runs?phase=unknown").status_code == 422
        assert client.get("/api/v1/runs?evidenceState=unknown").status_code == 422
        assert client.get("/healthz", headers={"Host": "evil.example"}).status_code == 400


def test_maximum_page_uses_one_row_lookahead_and_emits_cursor(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "ResearchService", FakeService)
    requested_limits: list[int] = []
    with TestClient(app_module.app) as client:
        store = app_module.app.state.research.store

        def page(*, after_run_id: str | None, limit: int, **_: object) -> list[dict[str, Any]]:
            requested_limits.append(limit)
            return [{**record(), "run_id": f"ar0_{index:03d}"} for index in range(limit)]

        monkeypatch.setattr(store, "list", page)
        response = client.get("/api/v1/runs?limit=100")

    assert response.status_code == 200
    assert requested_limits == [101]
    assert len(response.json()["items"]) == 100
    assert response.json()["nextCursor"] == "ar0_099"
