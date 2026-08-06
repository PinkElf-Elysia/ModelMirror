from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest

from server.coding_project_host.windows_helper import (
    ProjectHostHelperError,
    ProjectHostRegistry,
    inspect_git_project,
    public_project,
    validate_server_url,
)


class XorProtector:
    def __init__(self, key: int = 0xA7) -> None:
        self.key = key

    def protect(self, value: bytes) -> bytes:
        return bytes(item ^ self.key for item in value)

    def unprotect(self, value: bytes) -> bytes:
        return self.protect(value)


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


def _repository(tmp_path: Path) -> Path:
    project = tmp_path / "随机 项目 nebula-k8r3"
    project.mkdir()
    _git(project, "init", "-b", "feature/current-q7m4")
    (project / "README.md").write_text("marker: q7m4\n", encoding="utf-8")
    _git(project, "add", "README.md")
    _git(project, "-c", "user.name=Test", "-c", "user.email=test@modelmirror.local", "commit", "-m", "initial")
    return project


def test_registry_encrypts_token_path_and_device_secret(tmp_path: Path) -> None:
    state_path = tmp_path / "state.bin"
    registry = ProjectHostRegistry(state_path, XorProtector())
    project_path = "C:\\随机项目\\nebula-k8r3"
    token = "secret-token-" + "x" * 48
    registry.save_credentials("phost_0123456789abcdef0123456789abcdef", token)
    registry.remember_project(
        {
            "project_id": "hostgit_0123456789abcdef0123456789abcdef",
            "name": "随机项目",
            "branch": "main",
            "head": "a" * 40,
            "state": "available",
            "reason": "",
            "path": project_path,
        }
    )

    raw = state_path.read_bytes()
    assert token.encode() not in raw
    assert project_path.encode("utf-8") not in raw
    assert base64.b64encode(registry.device_secret) not in raw
    restored = ProjectHostRegistry(state_path, XorProtector())
    assert restored.credentials == ("phost_0123456789abcdef0123456789abcdef", token)
    assert restored.projects()[0]["path"] == project_path


def test_git_inspection_accepts_remote_without_reading_or_returning_it(tmp_path: Path) -> None:
    project = _repository(tmp_path)
    _git(project, "remote", "add", "origin", "https://example.invalid/private.git")

    inspected = inspect_git_project(project, b"k" * 32, enforce_windows=False)
    public = public_project(inspected)
    encoded = json.dumps(public, ensure_ascii=False)
    assert inspected["branch"] == "feature/current-q7m4"
    assert inspected["head"] == _git(project, "rev-parse", "HEAD")
    assert inspected["project_id"].startswith("hostgit_")
    assert "path" not in encoded.casefold()
    assert "remote" not in encoded.casefold()
    assert "example.invalid" not in encoded


@pytest.mark.parametrize("dirty", ["tracked", "untracked"])
def test_git_inspection_rejects_dirty_repository(tmp_path: Path, dirty: str) -> None:
    project = _repository(tmp_path)
    if dirty == "tracked":
        (project / "README.md").write_text("changed\n", encoding="utf-8")
    else:
        (project / "random-r8v3.txt").write_text("new\n", encoding="utf-8")
    with pytest.raises(ProjectHostHelperError) as error:
        inspect_git_project(project, b"k" * 32, enforce_windows=False)
    assert error.value.code == "git_repository_dirty"


def test_git_inspection_rejects_worktree_pointer_alternates_and_symlink(tmp_path: Path) -> None:
    pointer = tmp_path / "pointer"
    pointer.mkdir()
    (pointer / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    with pytest.raises(ProjectHostHelperError) as worktree:
        inspect_git_project(pointer, b"k" * 32, enforce_windows=False)
    assert worktree.value.code == "git_worktree_not_allowed"

    project = _repository(tmp_path)
    alternates = project / ".git" / "objects" / "info" / "alternates"
    alternates.write_text("/tmp/objects\n", encoding="utf-8")
    with pytest.raises(ProjectHostHelperError) as alternate:
        inspect_git_project(project, b"k" * 32, enforce_windows=False)
    assert alternate.value.code == "git_alternates_not_allowed"

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this test host")
    with pytest.raises(ProjectHostHelperError) as symlink:
        inspect_git_project(link, b"k" * 32, enforce_windows=False)
    assert symlink.value.code == "project_reparse_point_not_allowed"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000",
        "http://0.0.0.0:8000",
        "http://192.168.1.5:8000",
        "https://127.0.0.1:8000",
        "http://user:password@127.0.0.1:8000",
        "http://127.0.0.1:8000/api",
    ],
)
def test_server_url_rejects_non_loopback_or_credential_bearing_values(url: str) -> None:
    with pytest.raises(ProjectHostHelperError) as error:
        validate_server_url(url)
    assert error.value.code == "server_url_must_be_loopback"


def test_server_url_normalizes_the_only_supported_endpoint() -> None:
    assert validate_server_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"
