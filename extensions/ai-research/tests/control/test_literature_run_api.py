from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi.testclient import TestClient

import ai_research_control.app as app_module
from ai_research_control.ldr_client import LdrSessionExpired
from ai_research_control.project_store import ProjectConflict


def project() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "projectId": "rp_" + "a" * 32,
        "title": "Agent 评测",
        "researchQuestion": "Agent 评测如何确保可复现？",
        "domain": "ai_agent",
        "currentStage": "literature",
        "stages": {
            "literature": "active",
            "hypothesis_protocol": "not_available",
            "research_workspace": "not_available",
            "evaluation": "not_available",
            "analysis_report": "not_available",
        },
        "createdAt": "2026-08-24T00:00:00Z",
        "updatedAt": "2026-08-24T00:00:00Z",
        "literature": {
            "profileId": "v0.1-literature-default",
            "phase": "running",
            "outcome": None,
            "activeRunId": "lr_" + "b" * 32,
            "completedRunId": None,
            "collectionId": None,
            "modelId": "fixed/model",
            "attempts": [
                {
                    "runId": "lr_" + "b" * 32,
                    "ldrResearchId": "ldr-1",
                    "phase": "running",
                    "outcome": None,
                    "rawStatus": "in_progress",
                    "cancelRequestedAt": None,
                    "cancelAppliedAt": None,
                    "createdAt": "2026-08-24T00:00:00Z",
                    "startedAt": "2026-08-24T00:00:01Z",
                    "terminalAt": None,
                    "syncedAt": None,
                    "errorType": None,
                    "errorMessage": None,
                    "integrityStatus": "pending",
                    "progress": 35,
                    "latestLog": {"message": "Searching OpenAlex"},
                }
            ],
        },
    }


class LiteratureApiService:
    mode = "ready"

    def __init__(self, settings: object) -> None:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def start_literature(
        self,
        project_id: str,
        *,
        idempotency_key: str,
        collection_id: str | None,
    ) -> tuple[dict[str, Any], bool]:
        if self.mode == "locked":
            raise LdrSessionExpired("LDR session is not unlocked")
        if self.mode == "busy":
            raise ProjectConflict("another literature research is active")
        return deepcopy(project()), True

    async def cancel_literature(self, project_id: str) -> dict[str, Any]:
        value = deepcopy(project())
        value["literature"]["attempts"][0]["cancelRequestedAt"] = (
            "2026-08-24T00:00:02Z"
        )
        return value

    async def sync_literature(self, project_id: str) -> dict[str, Any]:
        value = deepcopy(project())
        value["literature"]["phase"] = "terminal"
        value["literature"]["outcome"] = "completed"
        value["literature"]["activeRunId"] = None
        value["literature"]["completedRunId"] = "lr_" + "b" * 32
        attempt = value["literature"]["attempts"][0]
        attempt.update(
            {
                "phase": "terminal",
                "outcome": "completed",
                "rawStatus": "completed",
                "terminalAt": "2026-08-24T00:00:03Z",
                "syncedAt": "2026-08-24T00:00:04Z",
                "integrityStatus": "verified",
            }
        )
        return value

    async def literature_sources(self, project_id: str) -> dict[str, Any]:
        return {
            "projectId": project_id,
            "literatureRunId": "lr_" + "b" * 32,
            "integrityStatus": "verified",
            "sources": [{"url": "https://openalex.org/W1", "title": "Evidence"}],
        }

    async def literature_review(self, project_id: str) -> dict[str, Any]:
        return {
            "projectId": project_id,
            "literatureRunId": "lr_" + "b" * 32,
            "integrityStatus": "verified",
            "markdown": "# Review",
        }

    async def literature_artifact(
        self, project_id: str, name: str
    ) -> tuple[bytes, str]:
        if name != "references.bib":
            raise KeyError(name)
        return b"@article{source1}\n", "a" * 64

    async def literature_collections(self) -> dict[str, Any]:
        return {"collections": [{"id": "collection-1", "is_public": False}]}

    async def index_literature_collection(self, collection_id: str) -> dict[str, Any]:
        return {
            "collectionId": collection_id,
            "status": "completed",
            "eventCount": 2,
            "terminalType": "complete",
        }

    async def literature_zotero_status(self) -> dict[str, Any]:
        return {
            "config": {"success": True, "configured": True, "has_api_key": True},
            "status": {"success": True, "collections": []},
        }

    async def sync_literature_zotero(self) -> dict[str, Any]:
        return {"success": True, "message": "sync started"}

    class Projects:
        @staticmethod
        def get(project_id: str) -> dict[str, Any]:
            if project_id != "rp_" + "a" * 32:
                raise KeyError(project_id)
            return deepcopy(project())

    projects = Projects()


def test_literature_start_status_cancel_contract(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "ResearchService", LiteratureApiService)
    project_id = "rp_" + "a" * 32
    with TestClient(app_module.app) as api:
        started = api.post(
            f"/api/v1/projects/{project_id}/literature/runs",
            json={"idempotencyKey": "literature:http:001"},
        )
        assert started.status_code == 201
        attempt = started.json()["attempts"][0]
        assert attempt["rawStatus"] == "in_progress"
        assert attempt["progress"] == 35
        assert attempt["latestLog"]["message"] == "Searching OpenAlex"
        assert api.get(f"/api/v1/projects/{project_id}/literature").status_code == 200
        cancelled = api.post(f"/api/v1/projects/{project_id}/literature/cancel")
        assert cancelled.json()["attempts"][0]["cancelRequestedAt"]


def test_literature_start_surface_maps_lock_conflict_and_unknown_fields(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "ResearchService", LiteratureApiService)
    project_id = "rp_" + "a" * 32
    with TestClient(app_module.app) as api:
        service = api.app.state.research
        service.mode = "locked"
        assert api.post(
            f"/api/v1/projects/{project_id}/literature/runs",
            json={"idempotencyKey": "literature:http:002"},
        ).status_code == 423
        service.mode = "busy"
        assert api.post(
            f"/api/v1/projects/{project_id}/literature/runs",
            json={"idempotencyKey": "literature:http:003"},
        ).status_code == 409
        assert api.post(
            f"/api/v1/projects/{project_id}/literature/runs",
            json={"idempotencyKey": "literature:http:004", "model": "arbitrary"},
        ).status_code == 422


def test_literature_results_library_and_safe_download_contract(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "ResearchService", LiteratureApiService)
    project_id = "rp_" + "a" * 32
    with TestClient(app_module.app) as api:
        synced = api.post(f"/api/v1/projects/{project_id}/literature/sync")
        assert synced.status_code == 200
        assert synced.json()["attempts"][0]["integrityStatus"] == "verified"
        sources = api.get(f"/api/v1/projects/{project_id}/sources")
        assert sources.json()["sources"][0]["url"].startswith("https://")
        assert api.get(f"/api/v1/projects/{project_id}/review").json()[
            "markdown"
        ] == "# Review"
        artifact = api.get(
            f"/api/v1/projects/{project_id}/artifacts/references.bib"
        )
        assert artifact.status_code == 200
        assert artifact.headers["x-content-sha256"] == "a" * 64
        assert artifact.headers["cache-control"] == "no-store"
        assert api.get(
            f"/api/v1/projects/{project_id}/artifacts/../../secret"
        ).status_code in {404, 422}

        assert api.get("/api/v1/literature/library/collections").status_code == 200
        assert api.post(
            "/api/v1/literature/library/collections/collection-1/index"
        ).json()["terminalType"] == "complete"
        assert api.post(
            "/api/v1/literature/library/collections/../index"
        ).status_code in {404, 422}
        zotero = api.get("/api/v1/literature/zotero/status").json()
        assert zotero["config"]["has_api_key"] is True
        assert "secret-zotero-key" not in str(zotero)
        assert api.post("/api/v1/literature/zotero/sync").status_code == 200
