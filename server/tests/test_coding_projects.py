from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from server.coding_runtime.projects import (
    MAX_PROJECTS,
    ProjectCatalogError,
    ProjectState,
    build_project_id,
    inspect_project,
    load_project_manifest,
    validate_git_tree,
)


def _git(path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments),
        cwd=path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _create_repository(root: Path, relative_path: str = "team/example-project") -> Path:
    project = root.joinpath(*relative_path.split("/"))
    project.mkdir(parents=True)
    _git(project, "init", "-b", "main")
    (project / "README.md").write_text("random marker: 7qP4mZ\n", encoding="utf-8")
    _git(project, "add", "README.md")
    _git(
        project,
        "-c",
        "user.name=ModelMirror Test",
        "-c",
        "user.email=test@modelmirror.local",
        "commit",
        "-m",
        "initial",
    )
    return project


def _write_manifest(
    root: Path,
    projects: list[dict[str, object]],
    *,
    version: int = 1,
) -> None:
    (root / ".modelmirror-coding-projects.json").write_text(
        json.dumps({"version": version, "projects": projects}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_clean_independent_repository_is_available_without_path_or_remote_leak(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    project = _create_repository(root)
    _git(project, "remote", "add", "origin", "https://example.invalid/private/repository.git")
    _write_manifest(root, [{"name": "随机示例项目", "path": "team/example-project"}])

    entry = load_project_manifest(root)[0]
    summary = inspect_project(root, entry)
    public = summary.to_public_dict()

    assert summary.state is ProjectState.AVAILABLE
    assert summary.branch == "main"
    assert summary.head == _git(project, "rev-parse", "HEAD")
    assert public["id"].startswith("local-")
    assert public["features"]["draft"] is True
    assert public["features"]["verification"] is False
    assert public["features"]["apply"] is False
    assert public["writeback_reason"] == "writeback_not_enabled"
    assert "path" not in public
    assert "remote" not in public
    assert "example-project" not in json.dumps(public, ensure_ascii=False)


def test_project_id_is_stable_for_path_and_independent_of_display_name(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    _write_manifest(root, [{"name": "第一名称", "path": "team/example-project"}])
    first = load_project_manifest(root)[0]
    _write_manifest(root, [{"name": "第二名称", "path": "team/example-project"}])
    second = load_project_manifest(root)[0]

    assert first.project_id == second.project_id == build_project_id("team/example-project")
    assert "team" not in first.project_id


def test_manifest_v3_requires_explicit_boolean_writeback_contract(
    tmp_path: Path,
) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    _write_manifest(
        root,
        [
            {
                "name": "Writable",
                "path": "team/writable",
                "writeback": {"enabled": True},
            }
        ],
        version=3,
    )

    assert load_project_manifest(root)[0].writeback_enabled is True

    _write_manifest(
        root,
        [
            {
                "name": "Invalid",
                "path": "team/invalid",
                "writeback": {"enabled": "yes"},
            }
        ],
        version=3,
    )
    with pytest.raises(ProjectCatalogError) as error:
        load_project_manifest(root)
    assert error.value.code == "project_writeback_invalid"


def test_writeback_requires_fixed_branch_and_no_remote(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    project = _create_repository(root)
    entry_payload = {
        "name": "Writable random q7m4",
        "path": "team/example-project",
        "writeback": {"enabled": True},
    }
    _write_manifest(root, [entry_payload], version=3)

    wrong_branch = inspect_project(root, load_project_manifest(root)[0])
    assert wrong_branch.state is ProjectState.AVAILABLE
    assert wrong_branch.features.apply is False
    assert wrong_branch.writeback_reason == "writeback_branch_required"

    _git(project, "switch", "-c", "coding/local-draft")
    _git(project, "remote", "add", "origin", "https://example.invalid/private.git")
    with_remote = inspect_project(root, load_project_manifest(root)[0])
    assert with_remote.state is ProjectState.AVAILABLE
    assert with_remote.features.apply is False
    assert with_remote.writeback_reason == "git_remote_not_allowed"

    _git(project, "remote", "remove", "origin")
    eligible = inspect_project(root, load_project_manifest(root)[0])
    assert eligible.state is ProjectState.AVAILABLE
    assert eligible.features.apply is eligible.features.commit is True
    assert eligible.features.publish is False
    assert eligible.writeback_reason is None


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("../outside", "manifest_path_invalid"),
        ("/absolute/project", "manifest_path_invalid"),
        ("C:/windows/project", "manifest_path_invalid"),
        ("team\\windows", "manifest_path_invalid"),
        ("team/./project", "manifest_path_invalid"),
        ("team/project/", "manifest_path_invalid"),
    ],
)
def test_manifest_rejects_noncanonical_or_escaping_paths(
    tmp_path: Path,
    path: str,
    code: str,
) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    _write_manifest(root, [{"name": "示例", "path": path}])

    with pytest.raises(ProjectCatalogError) as error:
        load_project_manifest(root)

    assert error.value.code == code


def test_manifest_rejects_duplicate_case_conflicts_and_invalid_names(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    _write_manifest(
        root,
        [
            {"name": "Upper", "path": "Team/Example"},
            {"name": "Lower", "path": "team/example"},
        ],
    )
    with pytest.raises(ProjectCatalogError) as conflict:
        load_project_manifest(root)
    assert conflict.value.code == "manifest_path_case_conflict"

    _write_manifest(root, [{"name": "bad\u0000name", "path": "team/example"}])
    with pytest.raises(ProjectCatalogError) as invalid_name:
        load_project_manifest(root)
    assert invalid_name.value.code == "manifest_name_invalid"


def test_manifest_enforces_project_limit_and_absolute_root(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    _write_manifest(
        root,
        [{"name": f"Project {index}", "path": f"team/project-{index}"} for index in range(MAX_PROJECTS + 1)],
    )
    with pytest.raises(ProjectCatalogError) as too_many:
        load_project_manifest(root)
    assert too_many.value.code == "project_manifest_invalid"

    with pytest.raises(ProjectCatalogError) as relative:
        load_project_manifest(Path("relative-project-root"))
    assert relative.value.code == "projects_root_not_absolute"


@pytest.mark.parametrize("dirty_kind", ["tracked", "untracked"])
def test_dirty_repository_is_unavailable(tmp_path: Path, dirty_kind: str) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    project = _create_repository(root)
    _write_manifest(root, [{"name": "示例", "path": "team/example-project"}])
    if dirty_kind == "tracked":
        (project / "README.md").write_text("changed\n", encoding="utf-8")
    else:
        (project / "untracked-9mK2.txt").write_text("new\n", encoding="utf-8")

    summary = inspect_project(root, load_project_manifest(root)[0])

    assert summary.state is ProjectState.UNAVAILABLE
    assert summary.reason == "git_repository_dirty"


def test_git_worktree_pointer_and_alternates_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    pointer = root / "team" / "pointer"
    pointer.mkdir(parents=True)
    (pointer / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    _write_manifest(root, [{"name": "Pointer", "path": "team/pointer"}])
    pointer_summary = inspect_project(root, load_project_manifest(root)[0])
    assert pointer_summary.reason == "git_worktree_not_allowed"

    project = _create_repository(root, "team/alternate")
    alternates = project / ".git" / "objects" / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text("/tmp/other-objects\n", encoding="utf-8")
    _write_manifest(root, [{"name": "Alternate", "path": "team/alternate"}])
    alternate_summary = inspect_project(root, load_project_manifest(root)[0])
    assert alternate_summary.reason == "git_alternates_not_allowed"


def test_detached_head_is_rejected_as_missing_branch(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    project = _create_repository(root)
    _git(project, "switch", "--detach", "HEAD")
    _write_manifest(root, [{"name": "Detached", "path": "team/example-project"}])

    summary = inspect_project(root, load_project_manifest(root)[0])

    assert summary.reason == "git_branch_required"


def test_project_path_symlink_is_rejected_when_supported(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    target = _create_repository(root, "actual/repository")
    link = root / "team" / "linked"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is not available on this host")
    _write_manifest(root, [{"name": "Linked", "path": "team/linked"}])

    summary = inspect_project(root, load_project_manifest(root)[0])

    assert summary.reason == "project_symlink_not_allowed"


@pytest.mark.parametrize(
    ("record", "code"),
    [
        (b"120000 blob " + b"a" * 40 + b"\tlinked\0", "git_symlink_not_allowed"),
        (b"160000 commit " + b"b" * 40 + b"\tvendor/module\0", "git_submodule_not_allowed"),
        (b"100644 blob " + b"c" * 40 + b"\tbad-\xff\0", "git_path_encoding_not_supported"),
    ],
)
def test_git_tree_rejects_links_submodules_and_non_utf8_paths(record: bytes, code: str) -> None:
    with pytest.raises(ProjectCatalogError) as error:
        validate_git_tree(record)
    assert error.value.code == code


def test_git_tree_rejects_case_conflicting_paths() -> None:
    payload = (
        b"100644 blob " + b"a" * 40 + b"\tDocs/Guide.md\0"
        b"100644 blob " + b"b" * 40 + b"\tdocs/guide.md\0"
    )
    with pytest.raises(ProjectCatalogError) as error:
        validate_git_tree(payload)
    assert error.value.code == "git_path_case_conflict"
