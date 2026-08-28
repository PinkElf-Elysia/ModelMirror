from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ai_research_control.project_store import (
    ProjectConflict,
    ProjectIntegrityError,
    ProjectStore,
)


def store(tmp_path: Path) -> ProjectStore:
    value = ProjectStore(tmp_path / "projects", source_lock_sha256="a" * 64)
    value.prepare()
    return value


def test_project_create_is_idempotent_and_persists_yaml(tmp_path: Path) -> None:
    projects = store(tmp_path)
    first, created = projects.create(
        title="Agent 评测复现性",
        research_question="当前 Agent 评测的复现性缺口是什么？",
        idempotency_key="project:create:001",
    )
    repeated, repeated_created = projects.create(
        title=first["title"],
        research_question=first["researchQuestion"],
        idempotency_key="project:create:001",
    )

    assert created is True
    assert repeated_created is False
    assert repeated["projectId"] == first["projectId"]
    assert projects.get(first["projectId"])["literature"]["phase"] == "not_started"
    assert (tmp_path / "projects" / first["projectId"] / "research.yaml").is_file()


def test_idempotency_conflict_and_edit_freeze(tmp_path: Path) -> None:
    projects = store(tmp_path)
    project, _ = projects.create(
        title="A", research_question="研究问题 A", idempotency_key="project:create:002"
    )
    with pytest.raises(ProjectConflict):
        projects.create(
            title="B", research_question="研究问题 B", idempotency_key="project:create:002"
        )

    updated = projects.update(
        project["projectId"], title="新标题", research_question=None
    )
    assert updated["title"] == "新标题"
    projects.begin_attempt(
        project["projectId"],
        idempotency_key="literature:start:001",
        model_id="fixed/model",
        collection_id=None,
    )
    with pytest.raises(ProjectConflict):
        projects.update(project["projectId"], title="不可修改", research_question=None)


def test_concurrent_create_with_same_key_produces_one_project(tmp_path: Path) -> None:
    projects = store(tmp_path)

    def create() -> tuple[str, bool]:
        project, created = projects.create(
            title="并发幂等",
            research_question="同一请求并发到达时会发生什么？",
            idempotency_key="project:create:concurrent",
        )
        return project["projectId"], created

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: create(), range(16)))

    assert len({project_id for project_id, _ in results}) == 1
    assert sum(1 for _, created in results if created) == 1
    assert len(projects.list(after_project_id=None, limit=100)) == 1


def test_attempt_lifecycle_preserves_raw_status_and_completed_result(tmp_path: Path) -> None:
    projects = store(tmp_path)
    project, _ = projects.create(
        title="A", research_question="研究问题", idempotency_key="project:create:003"
    )
    project, attempt, created = projects.begin_attempt(
        project["projectId"],
        idempotency_key="literature:start:002",
        model_id="fixed/model",
        collection_id="1",
    )
    assert created is True
    assert attempt["searchEngine"] == "openalex"
    assert attempt["collectionId"] == "1"
    assert attempt["strategy"] == "langgraph-agent"
    _, repeated, repeated_created = projects.begin_attempt(
        project["projectId"],
        idempotency_key="literature:start:002",
        model_id="fixed/model",
        collection_id="1",
    )
    assert repeated_created is False
    assert repeated["runId"] == attempt["runId"]
    with pytest.raises(ProjectConflict, match="other literature input"):
        projects.begin_attempt(
            project["projectId"],
            idempotency_key="literature:start:002",
            model_id="fixed/model",
            collection_id="2",
        )

    final, final_attempt = projects.update_attempt(
        project["projectId"],
        attempt["runId"],
        {
            "phase": "terminal",
            "outcome": "completed",
            "rawStatus": "completed",
            "terminalAt": "2026-08-24T00:00:00Z",
        },
    )
    assert final["literature"]["completedRunId"] is None
    assert final_attempt["rawStatus"] == "completed"
    final, _ = projects.update_attempt(
        project["projectId"],
        attempt["runId"],
        {"integrityStatus": "verified"},
    )
    assert final["literature"]["completedRunId"] == attempt["runId"]
    with pytest.raises(ProjectConflict):
        projects.begin_attempt(
            project["projectId"],
            idempotency_key="literature:start:003",
            model_id="fixed/model",
            collection_id=None,
        )


def test_symlink_project_and_unsafe_ids_fail_closed(tmp_path: Path) -> None:
    projects = store(tmp_path)
    with pytest.raises(KeyError):
        projects.get("../outside")

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "projects" / ("rp_" + "b" * 32)
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available")
    with pytest.raises(ProjectIntegrityError):
        projects.list(after_project_id=None, limit=10)


def test_symlink_literature_directory_fails_closed(tmp_path: Path) -> None:
    projects = store(tmp_path)
    project, _ = projects.create(
        title="A", research_question="研究问题", idempotency_key="project:create:symlink"
    )
    target = tmp_path / "outside-literature"
    target.mkdir()
    link = tmp_path / "projects" / project["projectId"] / "literature"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not available")

    with pytest.raises(ProjectIntegrityError, match="symlink"):
        projects.run_directory(
            project["projectId"], "lr_" + "c" * 32, must_exist=False
        )
