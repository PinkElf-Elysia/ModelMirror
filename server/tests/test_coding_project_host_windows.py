from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import server.coding_project_host.windows_helper as windows_helper_module
import server.coding_runtime.host_snapshot as host_snapshot_module

from server.coding_runtime.applier_client import _receipt_to_payload
from server.coding_runtime.apply_models import ApplyFileReceipt, ApplyReceipt
from server.coding_runtime.commit_models import CommitReceipt
from server.coding_runtime.committer_client import _commit_receipt_to_payload

from server.coding_project_host.windows_helper import (
    ProjectHostHelperError,
    ProjectHostRegistry,
    ProjectHostTransport,
    inspect_git_project,
    inspect_git_project_for_recovery,
    public_project,
    validate_server_url,
)
from server.coding_runtime.projects import build_safe_git_command, build_safe_git_environment


ROOT_IDENTITY = "1-2"
GIT_IDENTITY = "1-3"


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
        encoding="utf-8",
        errors="strict",
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _repository(
    tmp_path: Path,
    *,
    branch: str = "feature/current-q7m4",
) -> Path:
    project = tmp_path / "随机 项目 nebula-k8r3"
    project.mkdir()
    _git(project, "init", "-b", branch)
    _git(project, "config", "core.autocrlf", "false")
    (project / "README.md").write_bytes(b"marker: q7m4\n")
    _git(project, "add", "README.md")
    _git(project, "-c", "user.name=Test", "-c", "user.email=test@modelmirror.local", "commit", "-m", "initial")
    return project


def _directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ("cmd", "/c", "mklink", "/J", str(link), str(target)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip(f"Directory junctions are unavailable: {result.stderr}")
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are unavailable on this test host")


def _replace_repository_identity(project: Path, tmp_path: Path, target: str) -> None:
    if target == "root":
        backup = tmp_path / "original-project"
        project.rename(backup)
        shutil.copytree(backup, project)
        return
    if target == "git":
        backup = tmp_path / "original-dot-git"
        (project / ".git").rename(backup)
        shutil.copytree(backup, project / ".git")
        return
    raise AssertionError(target)


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
            "root_identity": ROOT_IDENTITY,
            "git_identity": GIT_IDENTITY,
        }
    )

    raw = state_path.read_bytes()
    assert token.encode() not in raw
    assert project_path.encode("utf-8") not in raw
    assert base64.b64encode(registry.device_secret) not in raw
    restored = ProjectHostRegistry(state_path, XorProtector())
    assert restored.credentials == ("phost_0123456789abcdef0123456789abcdef", token)
    assert restored.projects()[0]["path"] == project_path


def test_registry_never_persists_an_inventory_above_server_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    monkeypatch.setattr(registry, "_persist", lambda: None)
    for index in range(50):
        registry.remember_project(
            {
                "project_id": f"hostgit_{index:032x}",
                "name": f"project-{index}",
                "branch": "main",
                "head": "a" * 40,
                "state": "available",
                "reason": "",
                "path": f"C:\\projects\\project-{index}",
                "root_identity": f"1-{index + 10:x}",
                "git_identity": f"2-{index + 10:x}",
            }
        )

    with pytest.raises(ProjectHostHelperError) as rejected:
        registry.remember_project(
            {
                "project_id": f"hostgit_{50:032x}",
                "name": "project-50",
                "branch": "main",
                "head": "a" * 40,
                "state": "available",
                "reason": "",
                "path": "C:\\projects\\project-50",
                "root_identity": "1-3c",
                "git_identity": "2-3c",
            }
        )

    assert rejected.value.code == "project_limit_exceeded"
    assert len(registry.projects()) == 50


def test_legacy_registry_project_is_loaded_only_as_reselection_tombstone(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_path = tmp_path / "state.bin"
    protector = XorProtector()
    seed = ProjectHostRegistry(state_path, protector)
    project_id = "hostgit_0123456789abcdef0123456789abcdef"
    legacy = dict(seed._state)
    legacy["projects"] = {
        project_id: {
            "project_id": project_id,
            "name": "legacy-project",
            "branch": "main",
            "head": "a" * 40,
            "state": "available",
            "reason": "",
            "path": "C:\\legacy\\project",
        }
    }
    encoded = json.dumps(
        legacy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    state_path.write_bytes(
        windows_helper_module.STATE_MAGIC
        + base64.b64encode(protector.protect(encoded))
    )

    restored = ProjectHostRegistry(state_path, protector)
    [tombstone] = restored.projects()
    assert tombstone["state"] == "unavailable"
    assert tombstone["reason"] == "project_reselection_required"
    assert "root_identity" not in tombstone
    assert "git_identity" not in tombstone
    with pytest.raises(ProjectHostHelperError) as rejected:
        restored.project(project_id)
    assert rejected.value.code == "project_reselection_required"

    monkeypatch.setattr(
        windows_helper_module,
        "inspect_git_project",
        lambda *_args, **_kwargs: pytest.fail("Legacy inventory must not bind a new identity"),
    )
    sent: list[dict[str, object]] = []

    class FakeWebSocket:
        async def send(self, message: str) -> None:
            sent.append(json.loads(message))

    transport = ProjectHostTransport(
        restored,
        "http://127.0.0.1:8000",
        "12345678",
        select_folder=lambda: None,
    )
    asyncio.run(transport._send_inventory(FakeWebSocket()))
    assert sent[0]["projects"][0]["state"] == "unavailable"  # type: ignore[index]
    assert sent[0]["projects"][0]["reason"] == "project_reselection_required"  # type: ignore[index]


def test_registry_head_update_is_idempotent_and_rejects_wrong_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "hostgit_0123456789abcdef0123456789abcdef"
    baseline = "a" * 40
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    registry.remember_project(
        {
            "project_id": project_id,
            "name": "nebula-k8r3",
            "branch": "feature/current-q7m4",
            "head": baseline,
            "state": "available",
            "reason": "",
            "path": "C:\\random\\nebula-k8r3",
            "root_identity": ROOT_IDENTITY,
            "git_identity": GIT_IDENTITY,
        }
    )
    persist_calls = 0

    def count_persist() -> None:
        nonlocal persist_calls
        persist_calls += 1

    monkeypatch.setattr(registry, "_persist", count_persist)

    registry.update_project_head(
        project_id,
        branch="feature/current-q7m4",
        expected_heads={baseline},
        head=baseline,
    )
    assert persist_calls == 0

    with pytest.raises(ProjectHostHelperError) as changed:
        registry.update_project_head(
            project_id,
            branch="feature/other-r8v3",
            expected_heads={baseline},
            head="b" * 40,
        )

    assert changed.value.code == "project_changed"
    assert registry.project(project_id)["head"] == baseline
    assert persist_calls == 0


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


def test_git_inspection_accepts_utf8_symbolic_head_branch(tmp_path: Path) -> None:
    branch = "feature/中文分支-q7m4"
    project = _repository(tmp_path, branch=branch)

    inspected = inspect_git_project(project, b"k" * 32, enforce_windows=False)

    assert inspected["branch"] == branch
    assert inspected["head"] == _git(project, "rev-parse", "HEAD")


def test_git_inspection_rejects_invalid_utf8_head_before_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _repository(tmp_path)
    (project / ".git" / "HEAD").write_bytes(b"ref: refs/heads/feature/\xff\n")
    monkeypatch.setattr(
        windows_helper_module,
        "_run_git",
        lambda *_args, **_kwargs: pytest.fail(
            "Invalid HEAD encoding must be rejected before Git"
        ),
    )

    with pytest.raises(ProjectHostHelperError) as rejected:
        inspect_git_project(project, b"k" * 32, enforce_windows=False)

    assert rejected.value.code == "git_encoding_not_supported"


@pytest.mark.parametrize("replacement", ["root", "git"])
def test_bound_inspection_rejects_same_repository_content_with_new_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    project = _repository(tmp_path)
    secret = b"k" * 32
    baseline = inspect_git_project(project, secret, enforce_windows=False)
    _replace_repository_identity(project, tmp_path, replacement)
    assert _git(project, "symbolic-ref", "--short", "HEAD") == baseline["branch"]
    assert _git(project, "rev-parse", "HEAD") == baseline["head"]

    original_run_git = windows_helper_module._run_git
    monkeypatch.setattr(
        windows_helper_module,
        "_run_git",
        lambda *_args, **_kwargs: pytest.fail(
            "Identity mismatch must be rejected before any repository Git command"
        ),
    )
    with pytest.raises(ProjectHostHelperError) as rejected:
        inspect_git_project(
            project,
            secret,
            enforce_windows=False,
            expected_root_identity=baseline["root_identity"],
            expected_git_identity=baseline["git_identity"],
        )
    assert rejected.value.code == "project_identity_changed"

    monkeypatch.setattr(windows_helper_module, "_run_git", original_run_git)
    reselected = inspect_git_project(project, secret, enforce_windows=False)
    assert reselected["project_id"] != baseline["project_id"]
    assert reselected["branch"] == baseline["branch"]
    assert reselected["head"] == baseline["head"]


@pytest.mark.asyncio
async def test_reselection_publishes_old_identity_tombstone_before_new_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _repository(tmp_path)
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    original = inspect_git_project(project, registry.device_secret, enforce_windows=False)
    registry.remember_project(original)
    _replace_repository_identity(project, tmp_path, "root")
    replacement = inspect_git_project(project, registry.device_secret, enforce_windows=False)
    assert replacement["project_id"] != original["project_id"]
    assert replacement["branch"] == original["branch"]
    assert replacement["head"] == original["head"]

    def selected_or_bound(*_args, **kwargs):
        if kwargs:
            assert kwargs["expected_root_identity"] == replacement["root_identity"]
            assert kwargs["expected_git_identity"] == replacement["git_identity"]
        return dict(replacement)

    monkeypatch.setattr(
        windows_helper_module,
        "inspect_git_project",
        selected_or_bound,
    )
    sent: list[dict[str, object]] = []

    class FakeWebSocket:
        async def send(self, message: str) -> None:
            sent.append(json.loads(message))

    transport = ProjectHostTransport(
        registry,
        "http://127.0.0.1:8000",
        "12345678",
        select_folder=lambda: str(project),
    )
    await transport._handle_message(
        FakeWebSocket(),
        json.dumps(
            {
                "type": "select_project",
                "request_id": "phreq_0123456789abcdef0123456789abcdef",
            }
        ),
    )

    assert [message["type"] for message in sent] == ["inventory", "selection_result"]
    inventory = {
        item["project_id"]: item
        for item in sent[0]["projects"]  # type: ignore[index]
    }
    assert inventory[original["project_id"]]["state"] == "unavailable"
    assert (
        inventory[original["project_id"]]["reason"]
        == "project_reselection_required"
    )
    assert inventory[replacement["project_id"]]["state"] == "available"
    with pytest.raises(ProjectHostHelperError) as stale:
        registry.project(original["project_id"])
    assert stale.value.code == "project_reselection_required"
    assert registry.project(replacement["project_id"])["root_identity"] == replacement[
        "root_identity"
    ]


@pytest.mark.parametrize("inspection", ["initial", "recovery"])
@pytest.mark.parametrize(
    ("config_name", "config_value"),
    [
        ("include.path", "../outside-config"),
        ("includeIf.onbranch:main.path", "../outside-config"),
        ("filter.nebula.clean", "outside-filter"),
        ("credential.helper", "outside-credential-helper"),
        ("diff.nebula.textconv", "outside-textconv"),
        ("diff.nebula.external", "outside-diff-driver"),
        ("url.https://example.invalid/.insteadOf", "private:"),
        ("core.worktree", "../outside-worktree"),
        ("core.excludesFile", "../outside-excludes"),
        ("extensions.worktreeConfig", "true"),
        ("extensions.refStorage", "reftable"),
        ("extensions.partialClone", "origin"),
        ("remote.origin.promisor", "true"),
        ("remote..promisor", "true"),
        ("remote.origin.partialCloneFilter", "blob:none"),
    ],
)
def test_git_inspection_rejects_unsafe_config_before_object_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inspection: str,
    config_name: str,
    config_value: str,
) -> None:
    project = _repository(tmp_path)
    baseline = inspect_git_project(project, b"k" * 32, enforce_windows=False)
    _git(project, "config", config_name, config_value)
    original_run = subprocess.run
    commands: list[tuple[str, ...]] = []
    environments: list[dict[str, str]] = []

    def traced_run(command, **kwargs):
        commands.append(tuple(command))
        environments.append(dict(kwargs["env"]))
        return original_run(command, **kwargs)

    monkeypatch.setattr(windows_helper_module.subprocess, "run", traced_run)
    with pytest.raises(ProjectHostHelperError) as rejected:
        if inspection == "initial":
            inspect_git_project(project, b"k" * 32, enforce_windows=False)
        else:
            inspect_git_project_for_recovery(
                project,
                b"k" * 32,
                expected_project_id=baseline["project_id"],
                expected_branch=baseline["branch"],
                expected_head=baseline["head"],
                expected_root_identity=baseline["root_identity"],
                expected_git_identity=baseline["git_identity"],
                enforce_windows=False,
            )

    assert rejected.value.code == "git_config_unsafe"
    assert commands
    assert all("config" in command for command in commands)
    object_commands = {"rev-parse", "ls-tree", "cat-file", "diff", "status"}
    assert all(object_commands.isdisjoint(command) for command in commands)
    assert all(env["GIT_NO_LAZY_FETCH"] == "1" for env in environments)
    safe_environment = build_safe_git_environment()
    assert safe_environment["GIT_NO_LAZY_FETCH"] == "1"
    assert safe_environment["GIT_NO_REPLACE_OBJECTS"] == "1"
    safe_command = build_safe_git_command(project, ("status", "--porcelain=v2"))
    assert f"core.excludesFile={os.devnull}" in safe_command
    assert (project / "README.md").read_bytes() == b"marker: q7m4\n"


@pytest.mark.parametrize("inspection", ["initial", "recovery"])
def test_git_inspection_rejects_http_alternates_without_running_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inspection: str,
) -> None:
    project = _repository(tmp_path)
    baseline = inspect_git_project(project, b"k" * 32, enforce_windows=False)
    alternate = project / ".git" / "objects" / "info" / "http-alternates"
    alternate.write_text("https://example.invalid/objects\n", encoding="utf-8")
    monkeypatch.setattr(
        windows_helper_module,
        "_run_git",
        lambda *_args, **_kwargs: pytest.fail("Git must not run before rejecting http-alternates"),
    )

    with pytest.raises(ProjectHostHelperError) as rejected:
        if inspection == "initial":
            inspect_git_project(project, b"k" * 32, enforce_windows=False)
        else:
            inspect_git_project_for_recovery(
                project,
                b"k" * 32,
                expected_project_id=baseline["project_id"],
                expected_branch=baseline["branch"],
                expected_head=baseline["head"],
                expected_root_identity=baseline["root_identity"],
                expected_git_identity=baseline["git_identity"],
                enforce_windows=False,
            )

    assert rejected.value.code == "git_alternates_not_allowed"
    assert (project / "README.md").read_bytes() == b"marker: q7m4\n"


@pytest.mark.parametrize("inspection", ["initial", "recovery"])
@pytest.mark.parametrize(
    "unsafe_metadata",
    [
        "config_hardlink",
        "head_hardlink",
        "index_hardlink",
        "objects_reparse",
        "refs_reparse",
        "info_reparse",
        "replace_refs",
        "grafts",
    ],
)
def test_git_inspection_rejects_unsafe_metadata_before_running_git(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    inspection: str,
    unsafe_metadata: str,
) -> None:
    project = _repository(tmp_path)
    baseline = inspect_git_project(project, b"k" * 32, enforce_windows=False)
    git_path = project / ".git"
    if unsafe_metadata.endswith("_hardlink"):
        name = unsafe_metadata.removesuffix("_hardlink")
        source = git_path / {"config": "config", "head": "HEAD", "index": "index"}[name]
        try:
            os.link(source, tmp_path / f"{name}.outside-link")
        except OSError:
            pytest.skip("Hard links are unavailable on this test host")
    elif unsafe_metadata.endswith("_reparse"):
        name = unsafe_metadata.removesuffix("_reparse")
        source = git_path / name
        target = git_path / f"{name}-real"
        source.rename(target)
        _directory_link(source, target)
    elif unsafe_metadata == "replace_refs":
        (git_path / "refs" / "replace").mkdir()
    else:
        (git_path / "info" / "grafts").write_text(
            baseline["head"] + "\n",
            encoding="ascii",
        )

    monkeypatch.setattr(
        windows_helper_module,
        "_run_git",
        lambda *_args, **_kwargs: pytest.fail(
            "Git must not run before rejecting unsafe metadata"
        ),
    )
    with pytest.raises(ProjectHostHelperError) as rejected:
        if inspection == "initial":
            inspect_git_project(project, b"k" * 32, enforce_windows=False)
        else:
            inspect_git_project_for_recovery(
                project,
                b"k" * 32,
                expected_project_id=baseline["project_id"],
                expected_branch=baseline["branch"],
                expected_head=baseline["head"],
                expected_root_identity=baseline["root_identity"],
                expected_git_identity=baseline["git_identity"],
                enforce_windows=False,
            )

    assert rejected.value.code == "git_metadata_unsafe"
    assert (project / "README.md").read_bytes() == b"marker: q7m4\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows handle sharing gate")
def test_registry_snapshot_holds_config_guard_across_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _repository(tmp_path)
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    inspected = inspect_git_project(project, registry.device_secret)
    registry.remember_project(inspected)
    blocked_writes: list[bool] = []

    def guarded_archive(*_args, **_kwargs):
        config = project / ".git" / "config"
        try:
            config.write_bytes(config.read_bytes() + b"\n[unsafe]\n")
        except PermissionError:
            blocked_writes.append(True)
        else:
            pytest.fail("Config write must be blocked while snapshot cat-file is running")
        destination = Path(_args[1])
        destination.write_bytes(b"guarded-archive")
        return SimpleNamespace(
            project_id=inspected["project_id"],
            archive_identity=windows_helper_module.file_identity(destination),
        )

    monkeypatch.setattr(
        windows_helper_module,
        "create_host_snapshot_archive",
        guarded_archive,
    )
    result, archive_identity = registry.create_snapshot(
        inspected["project_id"],
        tmp_path / "snapshot.tar.gz",
    )

    assert result.project_id == inspected["project_id"]
    assert archive_identity
    assert blocked_writes == [True]


@pytest.mark.skipif(os.name != "nt", reason="Windows snapshot identity gate")
def test_snapshot_never_uploads_or_deletes_post_publish_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _repository(tmp_path)
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    inspected = inspect_git_project(project, registry.device_secret)
    registry.remember_project(inspected)
    archive = tmp_path / "snapshot.tar.gz"
    replacement = b"manual replacement must survive\n"
    replacement_identity: str | None = None
    real_publish = host_snapshot_module._publish_archive_no_replace

    def publish_then_replace(*args, **kwargs):
        nonlocal replacement_identity
        identity = real_publish(*args, **kwargs)
        destination = Path(args[1])
        destination.unlink()
        destination.write_bytes(replacement)
        replacement_identity = windows_helper_module.file_identity(destination)
        return identity

    monkeypatch.setattr(
        host_snapshot_module,
        "_publish_archive_no_replace",
        publish_then_replace,
    )
    _result, owned_identity = registry.create_snapshot(
        inspected["project_id"],
        archive,
    )
    transport = ProjectHostTransport(
        registry,
        "http://127.0.0.1:8000",
        "12345678",
        select_folder=lambda: None,
    )
    monkeypatch.setattr(
        transport,
        "_upload_snapshot",
        lambda *_args, **_kwargs: pytest.fail("Replacement must never be uploaded"),
    )

    with pytest.raises(ProjectHostHelperError):
        transport._upload_snapshot_exact("a" * 32, archive, owned_identity)

    assert archive.read_bytes() == replacement
    assert windows_helper_module.file_identity(archive) == replacement_identity


@pytest.mark.skipif(os.name != "nt", reason="Windows helper safety gate")
def test_registry_snapshot_rejects_unsafe_config_before_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _repository(tmp_path)
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    inspected = inspect_git_project(project, registry.device_secret)
    registry.remember_project(inspected)
    _git(project, "config", "filter.nebula.clean", "outside-filter")
    monkeypatch.setattr(
        windows_helper_module,
        "create_host_snapshot_archive",
        lambda *_args, **_kwargs: pytest.fail("Unsafe project must not be archived"),
    )

    with pytest.raises(ProjectHostHelperError) as rejected:
        registry.create_snapshot(inspected["project_id"], tmp_path / "snapshot.tar.gz")

    assert rejected.value.code == "git_config_unsafe"
    assert not (tmp_path / "snapshot.tar.gz").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows selected-project identity gate")
@pytest.mark.parametrize("replacement", ["root", "git"])
def test_snapshot_and_operation_reject_replaced_selected_identity_before_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    project = _repository(tmp_path)
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    inspected = inspect_git_project(project, registry.device_secret)
    registry.remember_project(inspected)
    registered = registry.project(inspected["project_id"])
    readme = (project / "README.md").read_bytes()
    _replace_repository_identity(project, tmp_path, replacement)
    assert _git(project, "symbolic-ref", "--short", "HEAD") == inspected["branch"]
    assert _git(project, "rev-parse", "HEAD") == inspected["head"]

    monkeypatch.setattr(
        windows_helper_module,
        "_run_git",
        lambda *_args, **_kwargs: pytest.fail(
            "Identity mismatch must be rejected before Git"
        ),
    )
    monkeypatch.setattr(
        windows_helper_module,
        "create_host_snapshot_archive",
        lambda *_args, **_kwargs: pytest.fail(
            "Identity mismatch must be rejected before snapshot creation"
        ),
    )
    monkeypatch.setattr(
        windows_helper_module,
        "HostGitApplyEngine",
        lambda *_args, **_kwargs: pytest.fail(
            "Identity mismatch must be rejected before engine construction"
        ),
    )
    monkeypatch.setattr(
        windows_helper_module,
        "HostGitCommitEngine",
        lambda *_args, **_kwargs: pytest.fail(
            "Identity mismatch must be rejected before engine construction"
        ),
    )
    archive = tmp_path / "snapshot.tar.gz"
    with pytest.raises(ProjectHostHelperError) as snapshot_rejected:
        registry.create_snapshot(inspected["project_id"], archive)
    assert snapshot_rejected.value.code == "project_identity_changed"

    transport = ProjectHostTransport(
        registry,
        "http://127.0.0.1:8000",
        "12345678",
        select_folder=lambda: None,
    )
    with pytest.raises(ProjectHostHelperError) as operation_rejected:
        transport._execute_project_operation(
            registered,
            operation_id="apply_0123456789abcdef012345",
            action="apply",
            payload={},
            branch=inspected["branch"],
            baseline_head=inspected["head"],
        )
    assert operation_rejected.value.code == "project_identity_changed"
    assert not archive.exists()
    assert not (project / ".git" / "modelmirror-transactions").exists()
    assert (project / "README.md").read_bytes() == readme


@pytest.mark.skipif(os.name != "nt", reason="Windows helper safety gate")
def test_managed_snapshot_without_journal_requires_current_exact_baseline(
    tmp_path: Path,
) -> None:
    project = _repository(tmp_path)
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    inspected = inspect_git_project(project, registry.device_secret, enforce_windows=False)
    registry.remember_project(inspected)

    with pytest.raises(ProjectHostHelperError) as changed:
        registry.create_snapshot(
            inspected["project_id"],
            tmp_path / "snapshot.tar.gz",
            expected_head="f" * 40,
            expected_branch=inspected["branch"],
            managed_operation_id="apply_0123456789abcdef012345",
        )

    assert changed.value.code == "project_changed"
    assert not (tmp_path / "snapshot.tar.gz").exists()


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


def test_git_inspection_explains_missing_head_and_detached_branch(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    _git(empty, "init", "-b", "main")
    with pytest.raises(ProjectHostHelperError) as missing_head:
        inspect_git_project(empty, b"k" * 32, enforce_windows=False)
    assert missing_head.value.code == "git_head_required"

    project = _repository(tmp_path)
    _git(project, "switch", "--detach")
    with pytest.raises(ProjectHostHelperError) as detached:
        inspect_git_project(project, b"k" * 32, enforce_windows=False)
    assert detached.value.code == "git_branch_required"


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


@pytest.mark.asyncio
async def test_rejected_saved_credentials_stop_retry_and_require_new_pairing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    registry.save_credentials(
        "phost_0123456789abcdef0123456789abcdef",
        "expired-token-" + "x" * 48,
    )
    attempts = 0
    statuses: list[str] = []

    class FakeWebSocket:
        async def send(self, _message: str) -> None:
            return None

        async def recv(self) -> str:
            return json.dumps({"type": "error", "code": "project_host_unavailable"})

    class FakeConnection:
        async def __aenter__(self) -> FakeWebSocket:
            return FakeWebSocket()

        async def __aexit__(self, *_args: object) -> None:
            return None

    def fake_connect(*_args: object, **_kwargs: object) -> FakeConnection:
        nonlocal attempts
        attempts += 1
        return FakeConnection()

    monkeypatch.setattr("websockets.asyncio.client.connect", fake_connect)
    transport = ProjectHostTransport(
        registry,
        "http://127.0.0.1:8000",
        "12345678",
        select_folder=lambda: None,
        status_changed=statuses.append,
    )

    await transport.run_forever()

    assert attempts == 1
    assert registry.credentials is None
    assert statuses == ["正在连接", "连接凭据已失效，请生成新连接码后重新连接"]


@pytest.mark.asyncio
async def test_helper_sends_periodic_heartbeat_without_echo_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    transport = ProjectHostTransport(
        registry,
        "http://127.0.0.1:8000",
        "12345678",
        select_folder=lambda: None,
    )
    sent: list[str] = []
    delivered = asyncio.Event()

    class FakeWebSocket:
        async def send(self, message: str) -> None:
            sent.append(message)
            delivered.set()

    monkeypatch.setattr(
        "server.coding_project_host.windows_helper.HEARTBEAT_INTERVAL_SECONDS",
        0.001,
    )
    heartbeat = asyncio.create_task(transport._heartbeat_loop(FakeWebSocket()))
    await asyncio.wait_for(delivered.wait(), timeout=1)
    heartbeat.cancel()
    with pytest.raises(asyncio.CancelledError):
        await heartbeat

    await transport._handle_message(FakeWebSocket(), '{"type":"heartbeat"}')

    assert sent == ['{"type":"heartbeat"}']


@pytest.mark.asyncio
async def test_helper_handles_snapshot_request_without_closing_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    project_id = "hostgit_0123456789abcdef0123456789abcdef"
    result = type(
        "SnapshotResult",
        (),
        {
            "project_id": project_id,
            "name": "snapshot-project",
            "branch": "main",
            "head": "a" * 40,
        },
    )()
    monkeypatch.setattr(
        registry,
        "create_snapshot",
        lambda *_args, **_kwargs: (result, "1-2"),
    )
    transport = ProjectHostTransport(
        registry,
        "http://127.0.0.1:8000",
        "12345678",
        select_folder=lambda: None,
    )
    monkeypatch.setattr(transport, "_upload_snapshot_exact", lambda *_args: None)
    sent: list[dict[str, object]] = []

    class FakeWebSocket:
        async def send(self, message: str) -> None:
            sent.append(json.loads(message))

    await transport._handle_message(
        FakeWebSocket(),
        json.dumps(
            {
                "type": "snapshot_project",
                "request_id": "phreq_0123456789abcdef0123456789abcdef",
                "project_id": project_id,
                "transfer_id": "b" * 32,
            }
        ),
    )

    assert sent == [
        {
            "type": "snapshot_result",
            "request_id": "phreq_0123456789abcdef0123456789abcdef",
            "transfer_id": "b" * 32,
            "project": {
                "project_id": project_id,
                "name": "snapshot-project",
                "branch": "main",
                "head": "a" * 40,
                "state": "available",
                "reason": None,
            },
        }
    ]


@pytest.mark.asyncio
async def test_open_folder_picker_does_not_block_snapshot_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    project_id = "hostgit_0123456789abcdef0123456789abcdef"
    result = type(
        "SnapshotResult",
        (),
        {
            "project_id": project_id,
            "name": "snapshot-project",
            "branch": "main",
            "head": "a" * 40,
        },
    )()
    monkeypatch.setattr(
        registry,
        "create_snapshot",
        lambda *_args, **_kwargs: (result, "1-2"),
    )
    picker_started = threading.Event()
    release_picker = threading.Event()

    def select_folder() -> None:
        picker_started.set()
        assert release_picker.wait(timeout=5)
        return None

    transport = ProjectHostTransport(
        registry,
        "http://127.0.0.1:8000",
        "12345678",
        select_folder=select_folder,
    )
    monkeypatch.setattr(transport, "_upload_snapshot_exact", lambda *_args: None)
    sent: list[dict[str, object]] = []

    class FakeWebSocket:
        async def send(self, message: str) -> None:
            sent.append(json.loads(message))

    websocket = FakeWebSocket()
    await transport._dispatch_message(
        websocket,
        json.dumps(
            {
                "type": "select_project",
                "request_id": "phreq_11111111111111111111111111111111",
            }
        ),
    )
    assert await asyncio.to_thread(picker_started.wait, 1)

    await transport._dispatch_message(
        websocket,
        json.dumps(
            {
                "type": "snapshot_project",
                "request_id": "phreq_22222222222222222222222222222222",
                "project_id": project_id,
                "transfer_id": "b" * 32,
            }
        ),
    )

    assert [item["type"] for item in sent] == ["snapshot_result"]
    release_picker.set()
    selection_task = transport._selection_task
    assert selection_task is not None
    await selection_task
    assert [item["type"] for item in sent] == [
        "snapshot_result",
        "request_error",
    ]


@pytest.mark.parametrize(
    ("content_length", "expected_sha256"),
    [
        (13, "0" * 64),
        (14, hashlib.sha256(b'{"safe":true}').hexdigest()),
    ],
)
def test_helper_rejects_operation_payload_size_or_digest_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content_length: int,
    expected_sha256: str,
) -> None:
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    registry.save_credentials(
        "phost_0123456789abcdef0123456789abcdef",
        "token-" + "x" * 48,
    )
    body = b'{"safe":true}'
    connections: list[object] = []

    class FakeResponse:
        status = 200

        @staticmethod
        def getheader(name: str) -> str | None:
            return {
                "Content-Length": str(content_length),
                "Content-Type": "application/json",
                "Cache-Control": "no-store",
            }.get(name)

        @staticmethod
        def read(_limit: int) -> bytes:
            return body

    class FakeConnection:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            assert (host, port, timeout) == ("127.0.0.1", 8000, 30)
            connections.append(self)

        def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
            assert method == "GET"
            assert path.startswith("/api/coding/project-host/operations/phop_")
            assert headers["Authorization"].startswith("Bearer ")

        @staticmethod
        def getresponse() -> FakeResponse:
            return FakeResponse()

        @staticmethod
        def close() -> None:
            return None

    monkeypatch.setattr(
        windows_helper_module.http.client,
        "HTTPConnection",
        FakeConnection,
    )
    transport = ProjectHostTransport(
        registry,
        "http://127.0.0.1:8000",
        "12345678",
        select_folder=lambda: None,
    )

    with pytest.raises(ProjectHostHelperError) as rejected:
        transport._download_operation_payload(
            payload_id="phop_" + "1" * 32,
            project_id="hostgit_0123456789abcdef0123456789abcdef",
            operation_id="apply_0123456789abcdef012345",
            action="apply",
            expected_size=len(body),
            expected_sha256=expected_sha256,
        )

    assert rejected.value.code == "operation_payload_invalid"
    assert len(connections) == 1


def test_helper_reconciles_committed_head_without_replaying_apply_or_crossing_project(
    tmp_path: Path,
) -> None:
    project_id = "hostgit_0123456789abcdef0123456789abcdef"
    other_project_id = "hostgit_fedcba9876543210fedcba9876543210"
    branch = "feature/nebula-k8r3"
    baseline = "a" * 40
    fingerprint = "b" * 64
    apply_operation_id = "apply_0123456789abcdef012345"
    commit_operation_id = "commit_0123456789abcdef0123"
    patch = "diff --git a/src/nebula.py b/src/nebula.py\n"
    patch_sha256 = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    applied = ApplyReceipt(
        apply_id=apply_operation_id,
        revision=3,
        snapshot_fingerprint=fingerprint,
        files=(
            ApplyFileReceipt(
                path="src/nebula.py",
                existed_before=True,
                before_sha256="c" * 64,
                after_sha256="d" * 64,
            ),
        ),
        applied_at=1_785_600_000.0,
    )
    committed = CommitReceipt(
        commit_id=commit_operation_id,
        revision=applied.revision,
        apply_id=applied.apply_id,
        commit_sha="e" * 40,
        parent_sha=baseline,
        tree_sha="f" * 40,
        message="feature: update nebula",
        files=("src/nebula.py",),
        branch=branch,
        committed_at=1_785_600_001.0,
    )
    records = {
        apply_operation_id: SimpleNamespace(
            action="apply",
            state="applied",
            project_id=project_id,
            branch=branch,
            expected_head=baseline,
            patch_sha256=patch_sha256,
            revision=applied.revision,
            apply_receipt=_receipt_to_payload(applied),
        ),
        commit_operation_id: SimpleNamespace(
            action="commit",
            state="committed",
            project_id=project_id,
            branch=branch,
            expected_head=baseline,
            patch_sha256=patch_sha256,
            revision=applied.revision,
            apply_receipt=_receipt_to_payload(applied),
            commit_receipt=_commit_receipt_to_payload(committed),
            commit_message=committed.message,
        ),
    }
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    registry.operations = SimpleNamespace(get=lambda operation_id: records.get(operation_id))  # type: ignore[assignment]
    transport = ProjectHostTransport(
        registry,
        "http://127.0.0.1:8000",
        "12345678",
        select_folder=lambda: None,
    )

    class ApplyEngine:
        def reconcile_apply(self, **_kwargs: object) -> object:
            raise AssertionError("committed reconciliation must not replay apply")

    class CommitEngine:
        def reconcile(self, operation_id: str) -> tuple[str, CommitReceipt]:
            assert operation_id == commit_operation_id
            return "committed", committed

    payload = {
        "kind": "commit",
        "apply_operation_id": apply_operation_id,
        "revision": applied.revision,
        "expected_head": baseline,
        "snapshot_fingerprint": fingerprint,
        "patch_sha256": patch_sha256,
        "paths": ["src/nebula.py"],
        "apply_receipt": _receipt_to_payload(applied),
        "message": committed.message,
    }

    result = transport._reconcile_commit_operation(
        ApplyEngine(),  # type: ignore[arg-type]
        CommitEngine(),  # type: ignore[arg-type]
        project_id=project_id,
        operation_id=commit_operation_id,
        branch=branch,
        baseline_head=baseline,
        payload=payload,
    )
    assert result == {
        "state": "committed",
        "apply_receipt": _receipt_to_payload(applied),
        "commit_receipt": _commit_receipt_to_payload(committed),
    }

    with pytest.raises(ProjectHostHelperError) as crossed:
        transport._reconcile_commit_operation(
            ApplyEngine(),  # type: ignore[arg-type]
            CommitEngine(),  # type: ignore[arg-type]
            project_id=other_project_id,
            operation_id=commit_operation_id,
            branch=branch,
            baseline_head=baseline,
            payload=payload,
        )
    assert crossed.value.code == "operation_conflict"


def test_helper_registry_tracks_commit_undo_and_revert_heads(tmp_path: Path) -> None:
    project_id = "hostgit_0123456789abcdef0123456789abcdef"
    branch = "feature/current-q7m4"
    baseline = "a" * 40
    committed_head = "b" * 40
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    registry.remember_project(
        {
            "project_id": project_id,
            "name": "nebula-k8r3",
            "branch": branch,
            "head": baseline,
            "state": "available",
            "reason": "",
            "path": "C:\\random\\nebula-k8r3",
            "root_identity": ROOT_IDENTITY,
            "git_identity": GIT_IDENTITY,
        }
    )
    transport = ProjectHostTransport(
        registry,
        "http://127.0.0.1:8000",
        "12345678",
        select_folder=lambda: None,
    )
    commit_receipt = CommitReceipt(
        commit_id="commit_0123456789abcdef0123",
        revision=1,
        apply_id="apply_0123456789abcdef012345",
        commit_sha=committed_head,
        parent_sha=baseline,
        tree_sha="c" * 40,
        message="feature: advance nebula",
        files=("src/nebula.py",),
        branch=branch,
        committed_at=1_785_600_001.0,
    )
    registered = registry.project(project_id)

    transport._update_registry_after_operation(
        registered,
        branch=branch,
        baseline_head=baseline,
        result={
            "state": "committed",
            "commit_receipt": _commit_receipt_to_payload(commit_receipt),
        },
    )
    assert registry.project(project_id)["head"] == committed_head

    transport._update_registry_after_operation(
        registry.project(project_id),
        branch=branch,
        baseline_head=baseline,
        result={
            "state": "undone",
            "receipt": _commit_receipt_to_payload(commit_receipt),
        },
    )
    assert registry.project(project_id)["head"] == baseline

    transport._update_registry_after_operation(
        registry.project(project_id),
        branch=branch,
        baseline_head=baseline,
        result={"state": "reverted"},
    )
    assert registry.project(project_id)["head"] == baseline


@pytest.mark.asyncio
async def test_second_cycle_dirty_inventory_uses_committed_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "hostgit_0123456789abcdef0123456789abcdef"
    branch = "feature/current-q7m4"
    baseline = "a" * 40
    committed_head = "b" * 40
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    registry.remember_project(
        {
            "project_id": project_id,
            "name": "nebula-k8r3",
            "branch": branch,
            "head": baseline,
            "state": "available",
            "reason": "",
            "path": "C:\\random\\nebula-k8r3",
            "root_identity": ROOT_IDENTITY,
            "git_identity": GIT_IDENTITY,
        }
    )
    transport = ProjectHostTransport(
        registry,
        "http://127.0.0.1:8000",
        "12345678",
        select_folder=lambda: None,
    )
    receipt = CommitReceipt(
        commit_id="commit_0123456789abcdef0123",
        revision=1,
        apply_id="apply_0123456789abcdef012345",
        commit_sha=committed_head,
        parent_sha=baseline,
        tree_sha="c" * 40,
        message="feature: advance nebula",
        files=("src/nebula.py",),
        branch=branch,
        committed_at=1_785_600_001.0,
    )
    transport._update_registry_after_operation(
        registry.project(project_id),
        branch=branch,
        baseline_head=baseline,
        result={
            "state": "committed",
            "commit_receipt": _commit_receipt_to_payload(receipt),
        },
    )

    def dirty_project(*_args: object, **_kwargs: object) -> dict[str, str]:
        raise ProjectHostHelperError("git_repository_dirty")

    monkeypatch.setattr(windows_helper_module, "inspect_git_project", dirty_project)
    sent: list[dict[str, object]] = []

    class FakeWebSocket:
        async def send(self, message: str) -> None:
            sent.append(json.loads(message))

    await transport._send_inventory(FakeWebSocket())

    assert sent == [
        {
            "type": "inventory",
            "projects": [
                {
                    "project_id": project_id,
                    "name": "nebula-k8r3",
                    "branch": branch,
                    "head": committed_head,
                    "state": "unavailable",
                    "reason": "git_repository_dirty",
                }
            ],
        }
    ]


def test_registry_persist_failure_turns_operation_result_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host_id = "phost_0123456789abcdef0123456789abcdef"
    project_id = "hostgit_0123456789abcdef0123456789abcdef"
    operation_id = "commit_0123456789abcdef0123"
    branch = "feature/current-q7m4"
    baseline = "a" * 40
    committed_head = "b" * 40
    registry = ProjectHostRegistry(tmp_path / "state.bin", XorProtector())
    registry.save_credentials(host_id, "token-" + "x" * 48)
    registry.remember_project(
        {
            "project_id": project_id,
            "name": "nebula-k8r3",
            "branch": branch,
            "head": baseline,
            "state": "available",
            "reason": "",
            "path": "C:\\random\\nebula-k8r3",
            "root_identity": ROOT_IDENTITY,
            "git_identity": GIT_IDENTITY,
        }
    )
    transport = ProjectHostTransport(
        registry,
        "http://127.0.0.1:8000",
        "12345678",
        select_folder=lambda: None,
    )
    receipt = CommitReceipt(
        commit_id=operation_id,
        revision=1,
        apply_id="apply_0123456789abcdef012345",
        commit_sha=committed_head,
        parent_sha=baseline,
        tree_sha="c" * 40,
        message="feature: advance nebula",
        files=("src/nebula.py",),
        branch=branch,
        committed_at=1_785_600_001.0,
    )
    envelope = json.dumps(
        {
            "version": 1,
            "host_id": host_id,
            "project_id": project_id,
            "operation_id": operation_id,
            "action": "commit",
            "branch": branch,
            "head": baseline,
            "payload": {},
        },
        separators=(",", ":"),
    ).encode("utf-8")
    monkeypatch.setattr(transport, "_download_operation_payload", lambda **_kwargs: envelope)
    monkeypatch.setattr(
        transport,
        "_execute_project_operation",
        lambda *_args, **_kwargs: {
            "state": "committed",
            "commit_receipt": _commit_receipt_to_payload(receipt),
        },
    )
    monkeypatch.setattr(
        registry,
        "update_project_head",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    message = {
        "type": "execute_operation",
        "request_id": "phreq_0123456789abcdef0123456789abcdef",
        "project_id": project_id,
        "operation_id": operation_id,
        "action": "commit",
        "payload_id": "phop_0123456789abcdef0123456789abcdef",
        "payload_sha256": hashlib.sha256(envelope).hexdigest(),
        "payload_size": len(envelope),
        "payload_expires_at": windows_helper_module.time.time() + 30.0,
    }

    with pytest.raises(ProjectHostHelperError) as unknown:
        transport._handle_operation_message(message)

    assert unknown.value.code == "operation_result_unknown"
