from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import ai_research_control.app as app_module
from ai_research_control.project_store import ProjectStore


class ProjectApiService:
    root: Path

    def __init__(self, settings: object) -> None:
        self.projects = ProjectStore(self.root, source_lock_sha256="a" * 64)
        self.projects.prepare()

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


def client(monkeypatch, tmp_path: Path) -> TestClient:
    ProjectApiService.root = tmp_path / "projects"
    monkeypatch.setattr(app_module, "ResearchService", ProjectApiService)
    return TestClient(app_module.app)


def test_project_http_journey_is_idempotent_and_recovers(monkeypatch, tmp_path: Path) -> None:
    payload = {
        "title": "  Agent 评测的可复现性  ",
        "researchQuestion": "  公开研究中报告了哪些复现缺口？  ",
        "idempotencyKey": "project:create:http-001",
    }
    with client(monkeypatch, tmp_path) as api:
        created = api.post("/api/v1/projects", json=payload)
        assert created.status_code == 201
        project = created.json()
        assert project["title"] == "Agent 评测的可复现性"
        assert project["literaturePhase"] == "not_started"

        repeated = api.post("/api/v1/projects", json=payload)
        assert repeated.status_code == 200
        assert repeated.json()["projectId"] == project["projectId"]

        edited = api.patch(
            f"/api/v1/projects/{project['projectId']}",
            json={"title": "Agent 评测复现性"},
        )
        assert edited.status_code == 200
        assert edited.json()["title"] == "Agent 评测复现性"

    with client(monkeypatch, tmp_path) as restarted:
        restored = restarted.get(f"/api/v1/projects/{project['projectId']}")
        assert restored.status_code == 200
        assert restored.json()["researchQuestion"] == "公开研究中报告了哪些复现缺口？"
        listing = restarted.get("/api/v1/projects?q=复现&literaturePhase=not_started")
        assert [item["projectId"] for item in listing.json()["items"]] == [
            project["projectId"]
        ]


def test_project_http_surface_fails_closed(monkeypatch, tmp_path: Path) -> None:
    with client(monkeypatch, tmp_path) as api:
        valid = {
            "title": "A",
            "researchQuestion": "B",
            "idempotencyKey": "project:create:http-002",
        }
        assert api.post("/api/v1/projects", json=valid).status_code == 201
        assert api.post("/api/v1/projects", json={**valid, "path": "../x"}).status_code == 422
        assert api.post(
            "/api/v1/projects", json={**valid, "title": "另一个标题"}
        ).status_code == 409
        assert api.post(
            "/api/v1/projects",
            json={**valid, "idempotencyKey": "project:create:http-003", "title": "   "},
        ).status_code == 422
        assert api.patch("/api/v1/projects/rp_" + "0" * 32, json={}).status_code == 422
        assert api.get("/api/v1/projects?literaturePhase=unknown").status_code == 422
        assert api.get("/api/v1/projects?cursor=rp_" + "f" * 32).status_code == 422
