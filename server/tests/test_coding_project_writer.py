from __future__ import annotations

import asyncio
import difflib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from server.coding_project_source.server import ProjectSnapshotBroker
from server.coding_project_writer import (
    CodingProjectWriterEngine,
    CodingProjectWriterServer,
    ProjectWriterError,
)
from server.coding_runtime.projects import build_project_id


APPLY_ID = "apply_writer_q7m4_123456"
COMMIT_ID = "commit_writer_r8v3_12345"
PROJECT_PATH = "team/random-q7m4"


def _git(root: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ("git", *arguments),
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _deleted_patch(path: str, content: str) -> str:
    body = "".join(
        difflib.unified_diff(
            content.splitlines(keepends=True),
            [],
            fromfile=f"a/{path}",
            tofile="/dev/null",
            lineterm="\n",
        )
    )
    return f"diff --git a/{path} b/{path}\ndeleted file mode 100644\n{body}"


def _added_patch(path: str, content: str) -> str:
    body = "".join(
        difflib.unified_diff(
            [],
            content.splitlines(keepends=True),
            fromfile="/dev/null",
            tofile=f"b/{path}",
            lineterm="\n",
        )
    )
    return f"diff --git a/{path} b/{path}\nnew file mode 100644\n{body}"


def _create_fixture(tmp_path: Path) -> tuple[Path, Path, str, str]:
    projects_root = tmp_path / "projects"
    target = projects_root / "team" / "random-q7m4"
    target.mkdir(parents=True)
    _git(target, "init", "-b", "coding/local-draft")
    (target / "old-q7m4.txt").write_text("marker=q7m4-91\n", encoding="utf-8")
    (target / "keep.txt").write_text("keep=r8v3-27\n", encoding="utf-8")
    _git(target, "add", ".")
    _git(
        target,
        "-c",
        "user.name=Writer Fixture",
        "-c",
        "user.email=writer@example.test",
        "commit",
        "-m",
        "baseline",
    )
    head = _git(target, "rev-parse", "HEAD")
    (projects_root / ".modelmirror-coding-projects.json").write_text(
        json.dumps(
            {
                "version": 3,
                "projects": [
                    {
                        "name": "Random q7m4",
                        "path": PROJECT_PATH,
                        "writeback": {"enabled": True},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    slot = tmp_path / "snapshot-slot"
    lease = ProjectSnapshotBroker(projects_root, slot).acquire(
        build_project_id(PROJECT_PATH),
        head,
    )
    return projects_root, target, head, lease.fingerprint


def _request(head: str, fingerprint: str) -> dict[str, object]:
    content = "marker=q7m4-91\n"
    patch = _deleted_patch("old-q7m4.txt", content)
    patch += _added_patch("moved-r8v3.txt", content)
    return {
        "project_id": build_project_id(PROJECT_PATH),
        "expected_head": head,
        "operation_id": APPLY_ID,
        "revision": 7,
        "patch": patch,
        "paths": ["old-q7m4.txt", "moved-r8v3.txt"],
        "expected_fingerprint": fingerprint,
    }


def test_writer_applies_commits_undoes_and_reverts_delete_move(tmp_path: Path) -> None:
    root, target, head, fingerprint = _create_fixture(tmp_path)
    engine = CodingProjectWriterEngine(root, tmp_path / "temporary")
    request = _request(head, fingerprint)

    applied = engine.apply(**request)
    assert not (target / "old-q7m4.txt").exists()
    assert (target / "moved-r8v3.txt").read_text(encoding="utf-8") == "marker=q7m4-91\n"
    assert _git(target, "rev-parse", "HEAD") == head

    committed = engine.commit(
        project_id=request["project_id"],
        expected_head=head,
        operation_id=COMMIT_ID,
        apply_receipt=applied,
        message="feature: 保存随机移动 r8v3",
    )
    assert _git(target, "rev-parse", "HEAD") == committed.commit_sha
    assert _git(target, "status", "--porcelain") == ""
    assert set(_git(target, "diff-tree", "--no-commit-id", "--name-status", "-r", "HEAD").splitlines()) == {
        "D\told-q7m4.txt",
        "A\tmoved-r8v3.txt",
    }

    assert engine.undo(
        project_id=request["project_id"],
        expected_head=head,
        apply_receipt=applied,
        commit_receipt=committed,
    ) == committed
    assert _git(target, "rev-parse", "HEAD") == head
    assert not (target / "old-q7m4.txt").exists()
    assert (target / "moved-r8v3.txt").is_file()

    engine.revert(
        project_id=request["project_id"],
        expected_head=head,
        receipt=applied,
    )
    assert (target / "old-q7m4.txt").read_text(encoding="utf-8") == "marker=q7m4-91\n"
    assert not (target / "moved-r8v3.txt").exists()
    assert _git(target, "status", "--porcelain") == ""


def test_restart_reconciles_completed_apply_without_repeating_write(tmp_path: Path) -> None:
    root, target, head, fingerprint = _create_fixture(tmp_path)
    temporary = tmp_path / "temporary"
    first = CodingProjectWriterEngine(root, temporary)
    request = _request(head, fingerprint)
    receipt = first.apply(**request)

    resumed = CodingProjectWriterEngine(root, temporary)
    state, recovered = resumed.reconcile_apply(**request)

    assert state == "applied"
    assert recovered is not None
    assert recovered.apply_id == receipt.apply_id
    assert recovered.revision == receipt.revision
    assert recovered.snapshot_fingerprint == receipt.snapshot_fingerprint
    assert recovered.files == receipt.files
    assert not (target / "old-q7m4.txt").exists()
    assert (target / "moved-r8v3.txt").is_file()
    resumed.revert(
        project_id=request["project_id"],
        expected_head=head,
        receipt=recovered,
    )
    assert _git(target, "status", "--porcelain") == ""


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda root, target: _git(target, "remote", "add", "origin", "https://example.invalid/repo.git"), "git_remote_not_allowed"),
        (lambda root, target: _git(target, "switch", "-c", "wrong-branch"), "writeback_branch_required"),
        (lambda root, target: (target / "untracked.txt").write_text("dirty\n", encoding="utf-8"), "git_repository_dirty"),
    ],
)
def test_writer_fails_closed_for_ineligible_target(
    tmp_path: Path,
    mutate: object,
    expected: str,
) -> None:
    root, target, head, fingerprint = _create_fixture(tmp_path)
    mutate(root, target)
    before = (target / "old-q7m4.txt").read_bytes()
    engine = CodingProjectWriterEngine(root, tmp_path / "temporary")

    with pytest.raises(ProjectWriterError) as raised:
        engine.apply(**_request(head, fingerprint))

    assert raised.value.code == expected
    assert (target / "old-q7m4.txt").read_bytes() == before
    assert not (target / "moved-r8v3.txt").exists()


def test_writer_server_rejects_browser_path_branch_and_git_arguments(tmp_path: Path) -> None:
    root, _, head, fingerprint = _create_fixture(tmp_path)
    server = CodingProjectWriterServer(
        tmp_path / "writer.sock",
        engine=CodingProjectWriterEngine(root, tmp_path / "temporary"),
    )
    request = {"action": "apply", **_request(head, fingerprint)}

    for unsafe in (
        {"target_path": "C:/tmp/elsewhere"},
        {"branch": "main"},
        {"git_args": ["push", "--force"]},
    ):
        with pytest.raises(ProjectWriterError) as raised:
            asyncio.run(server.dispatch({**request, **unsafe}))
        assert raised.value.code == "invalid_request"


def test_writeback_compose_keeps_writer_as_only_projects_root_writer() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load(
        (repository_root / "docker-compose.coding-writeback.yml").read_text(encoding="utf-8")
    )
    services = compose["services"]
    writer = services["coding-project-writer"]

    assert writer["network_mode"] == "none"
    assert writer["read_only"] is True
    assert writer["cap_drop"] == ["ALL"]
    assert writer["security_opt"] == ["no-new-privileges:true"]
    assert "ports" not in writer
    assert all("docker.sock" not in str(volume) for volume in writer["volumes"])
    project_mount = next(
        volume for volume in writer["volumes"] if isinstance(volume, dict)
    )
    assert project_mount["target"] == "/projects-root"
    assert project_mount["read_only"] is False
    assert project_mount["bind"]["create_host_path"] is False
    assert all("/projects-root" not in str(volume) for volume in services["server"]["volumes"])

    dockerfile = (repository_root / "server/coding_project_writer.Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "USER 65532:65532" in dockerfile
    assert 'CMD ["python", "-m", "server.coding_project_writer"]' in dockerfile
