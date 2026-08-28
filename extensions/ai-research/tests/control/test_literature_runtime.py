from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from ai_research_control.literature_runtime import LiteratureRuntime
from ai_research_control.literature_artifacts import LiteratureArtifactStore
from ai_research_control.project_store import ProjectConflict, ProjectStore


class FakeLdr:
    def __init__(self) -> None:
        self.start_result = ("ldr-1", "success")
        self.found: str | None = None
        self.statuses: list[dict[str, Any]] = []
        self.terminate_count = 0
        self.report_value: dict[str, Any] = {
            "content": "# Review\n\nEvidence [1].",
            "sources": [{"url": "https://openalex.org/W1", "title": "Evidence"}],
        }
        self.collection_values: list[dict[str, Any]] = [
            {
                "id": "private",
                "document_count": 2,
                "indexed_document_count": 2,
                "is_public": False,
                "agent_enabled": True,
            }
        ]

    def session_status(self) -> dict[str, str | None]:
        return {"status": "ready", "username": "researcher"}

    def collections(self) -> list[dict[str, Any]]:
        return self.collection_values

    def start_research(self, **_: object) -> tuple[str, str]:
        return self.start_result

    def find_research_by_run_id(self, run_id: str) -> str | None:
        return self.found

    def research_status(self, research_id: str) -> dict[str, Any]:
        return self.statuses.pop(0)

    def terminate(self, research_id: str) -> dict[str, Any]:
        self.terminate_count += 1
        return {"status": "success"}

    def report(self, research_id: str) -> dict[str, Any]:
        return self.report_value

    def export(self, research_id: str, export_format: str) -> bytes:
        if export_format == "ris":
            return b"TY  - JOUR\nTI  - Evidence\nER  -\n"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "review.qmd",
                "---\nbibliography: references.bib\n---\nEvidence [@source1].\n",
            )
            archive.writestr(
                "references.bib", "@article{source1, title={Evidence}}\n"
            )
        return buffer.getvalue()


def setup(tmp_path: Path) -> tuple[ProjectStore, FakeLdr, LiteratureRuntime, str]:
    projects = ProjectStore(tmp_path / "projects", source_lock_sha256="a" * 64)
    projects.prepare()
    project, _ = projects.create(
        title="Agent 评测",
        research_question="Agent 评测如何确保可复现？",
        idempotency_key="project:runtime:001",
    )
    ldr = FakeLdr()
    runtime = LiteratureRuntime(
        projects=projects,
        artifacts=LiteratureArtifactStore(tmp_path / "projects"),
        ldr=ldr,  # type: ignore[arg-type]
        model_id="fixed/model",
        bridge_url="http://host.docker.internal:8000/api/ai-research/v1",
    )
    return projects, ldr, runtime, project["projectId"]


def test_start_progress_and_complete_preserve_upstream_status(tmp_path: Path) -> None:
    projects, ldr, runtime, project_id = setup(tmp_path)
    project, created = asyncio.run(
        runtime.start_run(
            project_id,
            idempotency_key="literature:runtime:001",
            collection_id=None,
        )
    )
    assert created is True
    assert project["literature"]["phase"] == "running"
    ldr.statuses = [
        {"status": "in_progress", "progress": 42},
        {"status": "completed", "progress": 100},
    ]
    asyncio.run(runtime.tick())
    assert projects.get(project_id)["literature"]["attempts"][0]["progress"] == 42
    asyncio.run(runtime.tick())
    restored = projects.get(project_id)
    assert restored["literature"]["outcome"] == "completed"
    assert restored["literature"]["attempts"][0]["rawStatus"] == "completed"
    attempt = restored["literature"]["attempts"][0]
    assert attempt["integrityStatus"] == "verified"
    assert attempt["syncedAt"]
    assert "references.bib" in attempt["artifacts"]


def test_completed_with_bad_artifact_preserves_raw_fact_and_can_resync(
    tmp_path: Path,
) -> None:
    projects, ldr, runtime, project_id = setup(tmp_path)
    asyncio.run(
        runtime.start_run(
            project_id,
            idempotency_key="literature:runtime:bad-artifact",
            collection_id=None,
        )
    )
    ldr.report_value = {"content": "Review", "sources": []}
    ldr.statuses = [{"status": "completed", "progress": 100}]
    asyncio.run(runtime.tick())
    attempt = projects.get(project_id)["literature"]["attempts"][0]
    assert attempt["rawStatus"] == "completed"
    assert attempt["outcome"] == "infrastructure_error"
    assert attempt["integrityStatus"] == "failed"
    assert attempt["errorType"] == "artifact_sync_failed"
    assert projects.get(project_id)["literature"]["completedRunId"] is None
    assert attempt["errorMessage"].endswith(
        ": LDR report did not provide sources"
    )

    ldr.report_value = {
        "content": "Review",
        "sources": [{"url": "https://openalex.org/W1"}],
    }
    asyncio.run(runtime.sync(project_id))
    attempt = projects.get(project_id)["literature"]["attempts"][0]
    assert attempt["outcome"] == "completed"
    assert attempt["integrityStatus"] == "verified"
    assert attempt["errorType"] is None
    assert projects.get(project_id)["literature"]["completedRunId"] == attempt["runId"]
    receipt = json.loads(
        (
            projects.run_directory(project_id, attempt["runId"])
            / "literature-receipt.json"
        ).read_text("utf-8")
    )
    assert receipt["rawStatus"] == "completed"
    assert receipt["outcome"] == "completed"
    assert receipt["syncedAt"] == attempt["syncedAt"]


def test_response_loss_reconciles_without_starting_a_second_research(tmp_path: Path) -> None:
    projects, ldr, runtime, project_id = setup(tmp_path)
    project, _ = asyncio.run(
        runtime.start_run(
            project_id,
            idempotency_key="literature:runtime:002",
            collection_id=None,
        )
    )
    run_id = project["literature"]["activeRunId"]
    projects.update_attempt(project_id, run_id, {"ldrResearchId": None, "phase": "queued"})
    ldr.found = "ldr-reconciled"
    ldr.statuses = [{"status": "in_progress", "progress": 1}]
    asyncio.run(runtime.tick())
    attempt = projects.get(project_id)["literature"]["attempts"][0]
    assert attempt["ldrResearchId"] == "ldr-reconciled"
    assert attempt["phase"] == "running"


def test_cancel_applied_plus_upstream_error_normalizes_without_erasing_error(
    tmp_path: Path,
) -> None:
    projects, ldr, runtime, project_id = setup(tmp_path)
    asyncio.run(
        runtime.start_run(
            project_id,
            idempotency_key="literature:runtime:003",
            collection_id=None,
        )
    )
    asyncio.run(runtime.cancel(project_id))
    ldr.statuses = [
        {
            "status": "error",
            "metadata": {
                "error_info": {"type": "terminated", "message": "TerminateTaskError"}
            },
        }
    ]
    asyncio.run(runtime.tick())
    attempt = projects.get(project_id)["literature"]["attempts"][0]
    assert attempt["cancelRequestedAt"]
    assert attempt["cancelAppliedAt"]
    assert attempt["rawStatus"] == "error"
    assert attempt["outcome"] == "cancelled"
    assert attempt["errorMessage"] == "TerminateTaskError"


def test_cancel_requested_without_visible_upstream_does_not_claim_cancellation(
    tmp_path: Path,
) -> None:
    projects, ldr, runtime, project_id = setup(tmp_path)
    project, _ = asyncio.run(
        runtime.start_run(
            project_id,
            idempotency_key="literature:runtime:cancel-before-visible",
            collection_id=None,
        )
    )
    run_id = project["literature"]["activeRunId"]
    projects.update_attempt(
        project_id,
        run_id,
        {"ldrResearchId": None, "phase": "queued", "rawStatus": None},
    )

    asyncio.run(runtime.cancel(project_id))
    for _ in range(6):
        asyncio.run(runtime.tick())

    attempt = projects.get(project_id)["literature"]["attempts"][0]
    assert attempt["phase"] == "terminal"
    assert attempt["outcome"] == "infrastructure_error"
    assert attempt["cancelRequestedAt"]
    assert attempt["cancelAppliedAt"] is None
    assert attempt["rawStatus"] is None
    assert attempt["errorType"] == "missing_upstream_run"


def test_global_active_limit_and_private_collection_fail_closed(tmp_path: Path) -> None:
    projects, _, runtime, project_id = setup(tmp_path)
    asyncio.run(
        runtime.start_run(
            project_id,
            idempotency_key="literature:runtime:004",
            collection_id=None,
        )
    )
    second, _ = projects.create(
        title="第二个项目",
        research_question="另一个问题",
        idempotency_key="project:runtime:002",
    )
    with pytest.raises(ProjectConflict, match="another"):
        asyncio.run(
            runtime.start_run(
                second["projectId"],
                idempotency_key="literature:runtime:005",
                collection_id=None,
            )
        )

    projects.update_attempt(
        project_id,
        projects.get(project_id)["literature"]["activeRunId"],
        {"phase": "terminal", "outcome": "failed"},
    )
    with pytest.raises(ProjectConflict, match="fully indexed"):
        asyncio.run(
            runtime.start_run(
                second["projectId"],
                idempotency_key="literature:runtime:006",
                collection_id="private",
            )
        )


def test_collection_tool_surface_must_exactly_match_project_selection(
    tmp_path: Path,
) -> None:
    _, ldr, runtime, project_id = setup(tmp_path)
    ldr.collection_values.extend(
        [
            {
                "id": "eligible",
                "document_count": 2,
                "indexed_document_count": 2,
                "is_public": True,
                "agent_enabled": True,
            },
            {
                "id": "other-public",
                "document_count": 1,
                "indexed_document_count": 0,
                "is_public": True,
                "agent_enabled": True,
            },
        ]
    )

    with pytest.raises(ProjectConflict, match="match the selected"):
        asyncio.run(
            runtime.start_run(
                project_id,
                idempotency_key="literature:runtime:scope:001",
                collection_id="eligible",
            )
        )

    ldr.collection_values = [
        item for item in ldr.collection_values if item["id"] != "other-public"
    ]
    project, created = asyncio.run(
        runtime.start_run(
            project_id,
            idempotency_key="literature:runtime:scope:002",
            collection_id="eligible",
        )
    )
    assert created is True
    assert project["literature"]["collectionId"] == "eligible"
