from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import server.coding_project_host.windows_helper as windows_helper_module

from server.coding_runtime.applier_client import _receipt_to_payload
from server.coding_runtime.apply_models import ApplyFileReceipt, ApplyReceipt
from server.coding_runtime.commit_models import CommitReceipt
from server.coding_runtime.committer_client import _commit_receipt_to_payload

from server.coding_project_host.windows_helper import (
    ProjectHostHelperError,
    ProjectHostRegistry,
    ProjectHostTransport,
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
    _git(project, "config", "core.autocrlf", "false")
    (project / "README.md").write_bytes(b"marker: q7m4\n")
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
        lambda *_args, **_kwargs: result,
    )
    transport = ProjectHostTransport(
        registry,
        "http://127.0.0.1:8000",
        "12345678",
        select_folder=lambda: None,
    )
    monkeypatch.setattr(transport, "_upload_snapshot", lambda *_args: None)
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
