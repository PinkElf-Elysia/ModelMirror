from __future__ import annotations

import json
import hashlib
import os
import stat
import subprocess
from pathlib import Path

import pytest

import server.coding_project_host.host_commit_engine as host_commit_engine_module
from server.coding_project_host.host_apply_engine import HostGitApplyEngine
from server.coding_project_host.host_commit_engine import (
    HostCommitError,
    HostGitCommitEngine,
)
from server.coding_project_host.operation_log import (
    HostOperationJournal,
    HostOperationLogError,
)
from server.coding_runtime.apply_models import ApplyFileReceipt, ApplyReceipt
from server.coding_runtime.commit_models import validate_commit_branch
from server.coding_runtime.draft_workspace import DraftWorkspace
from server.coding_runtime.project_host import (
    PROJECT_HOST_PROTOCOL_V1,
    PROJECT_HOST_PROTOCOL_V2,
    ProjectHostError,
    ProjectHostStore,
)
from server.coding_runtime.project_host_api import ProjectHostRuntime


def _pair(store: ProjectHostStore) -> tuple[str, str]:
    _pairing, code = store.create_pairing("Windows 项目助手")
    host, token = store.consume_pairing(
        code,
        device_id="pdev_0123456789abcdef0123456789abcdef",
        version="1.0.0",
        platform="windows",
    )
    store.connect(host.host_id, token, connection_id="conn-r7m3", version="1.0.0")
    return host.host_id, token


def _project(project_id: str = "hostgit_0123456789abcdef0123456789abcdef") -> dict[str, object]:
    return {
        "project_id": project_id,
        "name": "随机项目 星云 k8r3",
        "branch": "feature/local-r8v3",
        "head": "a" * 40,
        "state": "available",
        "reason": None,
    }


def test_pairing_is_single_use_and_persists_only_hashed_secrets(tmp_path) -> None:
    state = tmp_path / "project-host.json"
    store = ProjectHostStore(state)
    pairing, code = store.create_pairing("本地助手")
    host, token = store.consume_pairing(
        code,
        device_id="pdev_0123456789abcdef0123456789abcdef",
        version="1.2.3",
        platform="windows",
    )

    with pytest.raises(ProjectHostError) as reused:
        store.consume_pairing(
            code,
            device_id="pdev_abcdefabcdefabcdefabcdefabcdefab",
            version="1.2.3",
            platform="windows",
        )
    assert reused.value.code == "project_host_pairing_invalid"
    persisted = state.read_text(encoding="utf-8")
    assert code not in persisted
    assert token not in persisted
    assert pairing.code_hash not in persisted
    assert token[:8] not in persisted
    assert set(json.loads(persisted)["hosts"][0]) == {
        "host_id",
        "device_id",
        "name",
        "token_hash",
        "version",
        "platform",
        "protocol",
        "status",
        "connection_id",
        "created_at",
        "updated_at",
        "last_heartbeat_at",
    }

    restored = ProjectHostStore(state)
    assert restored.host_status()["paired"] is True
    assert restored.host_status()["available"] is False
    assert restored.host_status()["direct_writeback"] is False


class _XorProtector:
    fail_protect = False

    def protect(self, value: bytes) -> bytes:
        if self.fail_protect:
            raise OSError("simulated DPAPI failure")
        return bytes(item ^ 0x6D for item in value)

    def unprotect(self, value: bytes) -> bytes:
        return bytes(item ^ 0x6D for item in value)


def test_v2_protocol_advertises_writeback_while_v1_remains_read_only(tmp_path) -> None:
    store = ProjectHostStore(tmp_path / "state.json")
    pairing, code = store.create_pairing("v2 helper")
    assert pairing.consumed is False
    host, token = store.consume_pairing(
        code,
        device_id="pdev_0123456789abcdef0123456789abcdef",
        version="1.1.0",
        platform="windows",
        protocol=PROJECT_HOST_PROTOCOL_V2,
    )
    store.connect(
        host.host_id,
        token,
        connection_id="conn-v2",
        version="1.1.0",
        protocol=PROJECT_HOST_PROTOCOL_V2,
    )
    assert store.host_status()["direct_writeback"] is True

    restored = ProjectHostStore(tmp_path / "state.json")
    assert restored.host_status()["protocol"] == PROJECT_HOST_PROTOCOL_V2
    assert restored.host_status()["direct_writeback"] is True

    with pytest.raises(ProjectHostError) as downgrade:
        restored.connect(
            host.host_id,
            token,
            connection_id="conn-v1",
            version="1.0.1",
            protocol=PROJECT_HOST_PROTOCOL_V1,
        )
    assert downgrade.value.code == "project_host_protocol_upgrade_requires_pairing"
    assert restored.host_status()["direct_writeback"] is True


def test_operation_journal_encrypts_patch_and_rejects_conflicting_reuse(tmp_path) -> None:
    path = tmp_path / "operations.bin"
    journal = HostOperationJournal(path, _XorProtector(), clock=lambda: 1000.0)
    patch = "diff --git a/marker.txt b/marker.txt\n+nebula-v13-r7k3\n"
    record = journal.create(
        operation_id="operation_v13_0123456789",
        action="apply",
        project_id="hostgit_0123456789abcdef0123456789abcdef",
        revision=7,
        branch="feature/local-r7k3",
        expected_head="a" * 40,
        patch_sha256=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        patch=patch,
    )
    assert record.state == "prepared"
    assert patch.encode("utf-8") not in path.read_bytes()

    restored = HostOperationJournal(path, _XorProtector(), clock=lambda: 1001.0)
    assert restored.get(record.operation_id) == record
    apply_receipt = {
        "apply_id": record.operation_id,
        "revision": 7,
        "snapshot_fingerprint": "b" * 64,
        "files": [
            {
                "path": "marker.txt",
                "existed_before": True,
                "before_sha256": "c" * 64,
                "after_sha256": "d" * 64,
            }
        ],
        "applied_at": 1001.0,
    }
    with pytest.raises(Exception) as missing_receipt:
        restored.transition(record.operation_id, "applied")
    assert getattr(missing_receipt.value, "code", None) == "operation_record_invalid"
    transitioned = restored.transition(
        record.operation_id,
        "applied",
        apply_receipt=apply_receipt,
        file_identities=("1-2:marker.txt",),
    )
    assert transitioned.state == "applied"
    assert transitioned.apply_receipt is not None
    transitioned.apply_receipt["revision"] = 99
    assert restored.get(record.operation_id).apply_receipt["revision"] == 7
    with pytest.raises(Exception) as invalid_state:
        restored.transition(record.operation_id, "committed")
    assert getattr(invalid_state.value, "code", None) == "operation_state_invalid"


def test_operation_journal_persists_created_directory_identity_once(tmp_path) -> None:
    path = tmp_path / "operations.bin"
    journal = HostOperationJournal(path, _XorProtector())
    patch = "diff --git a/nested/new.txt b/nested/new.txt\n+stable\n"
    record = journal.create(
        operation_id="operation_v13_directories",
        action="apply",
        project_id="hostgit_0123456789abcdef0123456789abcdef",
        revision=4,
        branch="feature/local-k8m2",
        expected_head="1" * 40,
        patch_sha256=hashlib.sha256(patch.encode()).hexdigest(),
        patch=patch,
    )
    receipt = {
        "apply_id": record.operation_id,
        "revision": 4,
        "snapshot_fingerprint": "f" * 64,
        "files": [
            {
                "path": "nested/new.txt",
                "existed_before": False,
                "before_sha256": None,
                "after_sha256": "a" * 64,
            }
        ],
        "applied_at": 1.0,
    }
    journal.transition(record.operation_id, "applying", apply_receipt=receipt)
    updated = journal.transition(
        record.operation_id,
        "applying",
        created_directories=("1a-2b@3c-4d:nested",),
    )
    assert updated.created_directories == ("1a-2b@3c-4d:nested",)
    restored = HostOperationJournal(path, _XorProtector())
    assert restored.get(record.operation_id).created_directories == (
        "1a-2b@3c-4d:nested",
    )
    with pytest.raises(Exception) as conflict:
        restored.transition(
            record.operation_id,
            "applying",
            created_directories=("1a-2c@3c-4d:nested",),
        )
    assert getattr(conflict.value, "code", None) == "operation_conflict"

    with pytest.raises(Exception) as conflict:
        restored.create(
            operation_id=record.operation_id,
            action="apply",
            project_id=record.project_id,
            revision=8,
            branch=record.branch,
            expected_head=record.expected_head,
            patch_sha256=record.patch_sha256,
            patch=patch,
        )
    assert getattr(conflict.value, "code", None) == "operation_conflict"

    with pytest.raises(Exception) as mismatched_patch:
        restored.create(
            operation_id="operation_v13_abcdefghijk",
            action="apply",
            project_id=record.project_id,
            revision=8,
            branch=record.branch,
            expected_head=record.expected_head,
            patch_sha256="e" * 64,
            patch=patch,
        )
    assert getattr(mismatched_patch.value, "code", None) == "operation_record_invalid"


def test_operation_journal_does_not_accept_memory_state_when_persist_fails(tmp_path) -> None:
    protector = _XorProtector()
    patch = "diff --git a/a.txt b/a.txt\n+stable\n"
    journal = HostOperationJournal(tmp_path / "operations.bin", protector)
    record = journal.create(
        operation_id="operation_v13_persist_01",
        action="apply",
        project_id="hostgit_0123456789abcdef0123456789abcdef",
        revision=3,
        branch="feature/persist-r8v3",
        expected_head="a" * 40,
        patch_sha256=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        patch=patch,
    )
    apply_receipt = {
        "apply_id": record.operation_id,
        "revision": 3,
        "snapshot_fingerprint": "b" * 64,
        "files": [
            {
                "path": "a.txt",
                "existed_before": False,
                "before_sha256": None,
                "after_sha256": "d" * 64,
            }
        ],
        "applied_at": 1001.0,
    }
    protector.fail_protect = True
    with pytest.raises(HostOperationLogError) as unavailable:
        journal.transition(
            record.operation_id,
            "applying",
            apply_receipt=apply_receipt,
        )
    assert unavailable.value.code == "operation_log_unavailable"
    protector.fail_protect = False
    assert journal.get(record.operation_id).state == "prepared"


def test_operation_journal_binds_commit_receipt_to_head_and_applied_files(tmp_path) -> None:
    journal = HostOperationJournal(tmp_path / "operations.bin", _XorProtector())
    patch = "diff --git a/a.txt b/a.txt\n+stable\n"
    apply_receipt = {
        "apply_id": "apply_v13_binding_012345",
        "revision": 4,
        "snapshot_fingerprint": "b" * 64,
        "files": [
            {
                "path": "a.txt",
                "existed_before": True,
                "before_sha256": "c" * 64,
                "after_sha256": "d" * 64,
            }
        ],
        "applied_at": 1001.0,
    }
    operation_id = "commit_v13_binding_0123"
    record = journal.create(
        operation_id=operation_id,
        action="commit",
        project_id="hostgit_0123456789abcdef0123456789abcdef",
        revision=4,
        branch="feature/binding-r8v3",
        expected_head="a" * 40,
        patch_sha256=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        patch=patch,
        commit_message="feature: update marker",
        apply_receipt=apply_receipt,
        file_identities=("1-2:a.txt",),
    )
    commit_receipt = {
        "commit_id": operation_id,
        "revision": 4,
        "apply_id": apply_receipt["apply_id"],
        "commit_sha": "e" * 40,
        "parent_sha": "a" * 40,
        "tree_sha": "f" * 40,
        "message": "feature: update marker",
        "files": ["a.txt"],
        "branch": record.branch,
        "committed_at": 1002.0,
    }
    wrong_parent = {**commit_receipt, "parent_sha": "9" * 40}
    with pytest.raises(Exception) as parent_conflict:
        journal.transition(operation_id, "committing", commit_receipt=wrong_parent)
    assert getattr(parent_conflict.value, "code", None) == "operation_record_invalid"
    wrong_files = {**commit_receipt, "files": ["other.txt"]}
    with pytest.raises(Exception) as file_conflict:
        journal.transition(operation_id, "committing", commit_receipt=wrong_files)
    assert getattr(file_conflict.value, "code", None) == "operation_record_invalid"
    committing = journal.transition(
        operation_id,
        "committing",
        commit_receipt=commit_receipt,
        index_sha256="1" * 64,
        index_before_sha256="6" * 64,
        index_identity="2-3",
        index_before_identity="4-5",
        reflog_metadata=(
            f"HEAD:{'7' * 64}@6-7",
            f"branch:{'8' * 64}@8-9",
        ),
    )
    assert committing.commit_receipt == commit_receipt
    committed = journal.transition(operation_id, "committed")
    assert committed.commit_receipt == commit_receipt


def test_authentication_revoke_and_stale_heartbeat_fail_closed(tmp_path) -> None:
    now = [1_000.0]
    store = ProjectHostStore(tmp_path / "state.json", clock=lambda: now[0])
    host_id, token = _pair(store)
    assert store.authenticate(host_id, token).host_id == host_id
    with pytest.raises(ProjectHostError) as invalid:
        store.authenticate(host_id, "wrong-token")
    assert invalid.value.code == "project_host_authentication_failed"

    now[0] += 61
    assert store.host_status()["available"] is False
    store.heartbeat(host_id, "conn-r7m3")
    assert store.host_status()["available"] is True
    store.register_project(host_id, _project())
    store.revoke(host_id)
    assert store.list_projects() == []
    with pytest.raises(ProjectHostError) as revoked:
        store.authenticate(host_id, token)
    assert revoked.value.code == "project_host_unavailable"


@pytest.mark.asyncio
async def test_reconnect_probes_authenticated_stale_connection(tmp_path) -> None:
    now = [1_000.0]
    store = ProjectHostStore(tmp_path / "state.json", clock=lambda: now[0])
    host_id, _token = _pair(store)
    runtime = ProjectHostRuntime(store, tmp_path / "uploads")
    sent: list[dict[str, str]] = []

    class FakeWebSocket:
        async def send_json(self, payload: dict[str, str]) -> None:
            sent.append(payload)
            await runtime._incoming(host_id, "conn-r7m3", {"type": "heartbeat"})

    runtime._connections[host_id] = FakeWebSocket()  # type: ignore[assignment]
    now[0] += 61
    assert store.host_status()["available"] is False

    status = await runtime.reconnect()

    assert status["available"] is True
    assert sent == [{"type": "heartbeat"}]


@pytest.mark.parametrize("version", ["2.0.0", "0.9.0", "1.0", "latest"])
def test_pairing_rejects_incompatible_or_malformed_helper_versions(tmp_path, version) -> None:
    store = ProjectHostStore(tmp_path / "state.json")
    _pairing, code = store.create_pairing("Windows 项目助手")

    with pytest.raises(ProjectHostError) as invalid:
        store.consume_pairing(
            code,
            device_id="pdev_0123456789abcdef0123456789abcdef",
            version=version,
            platform="windows",
        )
    assert invalid.value.code == "project_host_version_invalid"


def test_second_device_requires_explicitly_revoking_the_paired_host(tmp_path) -> None:
    store = ProjectHostStore(tmp_path / "state.json")
    _first_host, _token = _pair(store)
    _pairing, code = store.create_pairing("另一台助手")

    with pytest.raises(ProjectHostError) as conflict:
        store.consume_pairing(
            code,
            device_id="pdev_abcdefabcdefabcdefabcdefabcdefab",
            version="1.0.0",
            platform="windows",
        )
    assert conflict.value.code == "project_host_already_paired"


def test_public_project_never_contains_path_remote_or_server_secrets(tmp_path) -> None:
    store = ProjectHostStore(tmp_path / "state.json")
    host_id, _token = _pair(store)
    store.register_project(host_id, _project())

    public = store.list_projects()[0]
    encoded = json.dumps(public, ensure_ascii=False)
    assert public["kind"] == "host_git"
    assert public["features"]["verification"] is True
    assert public["features"]["apply"] is False
    assert public["writeback_reason"] == "project_host_writeback_not_available"
    assert "path" not in encoded.casefold()
    assert "remote" not in encoded.casefold()
    assert "C:\\" not in encoded


def test_selection_dispatch_completion_is_idempotent_and_host_bound(tmp_path) -> None:
    store = ProjectHostStore(tmp_path / "state.json")
    host_id, _token = _pair(store)
    selection = store.create_selection()
    assert store.next_selection(host_id).request_id == selection.request_id
    assert store.next_selection(host_id) is None

    completed = store.complete_selection(host_id, selection.request_id, project=_project())
    repeated = store.complete_selection(host_id, selection.request_id, project=_project())
    assert completed.status == repeated.status == "completed"
    assert completed.project_id == repeated.project_id

    other = ProjectHostStore()
    other_host, _other_token = _pair(other)
    with pytest.raises(ProjectHostError) as mismatch:
        other.complete_selection(other_host, selection.request_id, project=_project())
    assert mismatch.value.code == "project_host_request_not_found"


@pytest.mark.parametrize(
    "payload",
    [
        {**_project(), "project_id": "local-deadbeef"},
        {**_project(), "head": "not-a-head"},
        {**_project(), "branch": ""},
        {**_project(), "path": "C:/private"},
        {**_project(), "remote": "https://example.invalid/private.git"},
    ],
)
def test_project_registration_rejects_malformed_or_path_bearing_payloads(tmp_path, payload) -> None:
    store = ProjectHostStore(tmp_path / "state.json")
    host_id, _token = _pair(store)
    with pytest.raises(ProjectHostError) as error:
        store.register_project(host_id, payload)
    assert error.value.code == "invalid_project_host_response"


HOST_COMMIT_PROJECT_ID = "hostgit_89abcdef0123456789abcdef01234567"
HOST_COMMIT_FINGERPRINT = "8" * 64


def _host_commit_git(root: Path, *arguments: str, input_bytes: bytes | None = None) -> str:
    stdin = subprocess.DEVNULL if input_bytes is None else None
    completed = subprocess.run(
        ("git", *arguments),
        cwd=root,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        input=input_bytes,
        check=False,
        timeout=20,
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    return completed.stdout.decode("utf-8", errors="strict").strip()


def _host_commit_repository(
    tmp_path: Path,
    *,
    branch: str = "feature/local-k8m2",
    content: bytes = b"before-r7k3\n",
    autocrlf: bool = False,
) -> tuple[Path, str]:
    root = tmp_path / "host commit project"
    root.mkdir()
    _host_commit_git(root, "init", "-b", branch)
    _host_commit_git(root, "config", "user.name", "Acceptance User")
    _host_commit_git(root, "config", "user.email", "acceptance@example.invalid")
    _host_commit_git(root, "config", "core.autocrlf", "true" if autocrlf else "false")
    (root / "marker.txt").write_bytes(content)
    _host_commit_git(root, "add", "--", "marker.txt")
    _host_commit_git(root, "commit", "-m", "baseline")
    return root, _host_commit_git(root, "rev-parse", "HEAD")


def _host_commit_patch(path: str, before: bytes, after: bytes) -> str:
    return DraftWorkspace._unified_diff(
        path,
        before.decode("utf-8"),
        after.decode("utf-8"),
        status="modified",
    )


def _host_commit_applied(
    tmp_path: Path,
    *,
    branch: str = "feature/local-k8m2",
    before: bytes = b"before-r7k3\n",
    after: bytes = b"after-n4p7\n",
    autocrlf: bool = False,
) -> tuple[Path, str, HostOperationJournal, ApplyReceipt]:
    root, head = _host_commit_repository(
        tmp_path,
        branch=branch,
        content=before,
        autocrlf=autocrlf,
    )
    journal = HostOperationJournal(tmp_path / "host-operations.bin", _XorProtector())
    receipt = HostGitApplyEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        enforce_windows=False,
    ).apply(
        operation_id="apply_host_commit_0123456789",
        revision=13,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=HOST_COMMIT_FINGERPRINT,
        patch=_host_commit_patch("marker.txt", before, after),
        paths=("marker.txt",),
    )
    return root, head, journal, receipt


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "@",
        "HEAD",
        "-danger",
        "/danger",
        "danger/",
        "danger.",
        "feature//name",
        ".hidden/name",
        "feature/name.lock",
        "feature/a..b",
        "feature/a@{b",
        "feature/a\\b",
        "feature/a b",
        "feature/a~b",
        "feature/a^b",
        "feature/a:b",
        "feature/a?b",
        "feature/a*b",
        "feature/a[b",
        "feature/a\nb",
    ],
)
def test_host_commit_branch_validation_rejects_unsafe_refs(invalid: str) -> None:
    with pytest.raises(ValueError):
        validate_commit_branch(invalid)


def test_host_commit_dynamic_unicode_branch_remote_idempotency_and_undo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = "feature/本地-k8m2"
    assert validate_commit_branch(branch) == branch
    root, head, journal, apply_receipt = _host_commit_applied(
        tmp_path,
        branch=branch,
    )
    _host_commit_git(
        root,
        "remote",
        "add",
        "origin",
        "https://example.invalid/must-not-connect.git",
    )
    config_before = (root / ".git" / "config").read_bytes()
    head_reflog = root / ".git" / "logs" / "HEAD"
    branch_reflog = root / ".git" / "logs" / "refs" / "heads" / Path(branch)
    reflogs_before = (head_reflog.read_bytes(), branch_reflog.read_bytes())

    import server.coding_project_host.host_commit_engine as commit_module

    real_run = subprocess.run
    observed: list[tuple[str, ...]] = []
    observed_environments: list[dict[str, str]] = []

    def observe_run(arguments, **kwargs):
        observed.append(tuple(str(item) for item in arguments))
        if "env" in kwargs:
            observed_environments.append(dict(kwargs["env"]))
        return real_run(arguments, **kwargs)

    monkeypatch.setattr(commit_module.subprocess, "run", observe_run)
    engine = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        enforce_windows=False,
    )
    receipt = engine.commit(
        operation_id="commit_host_basic_01234567",
        apply_receipt=apply_receipt,
        branch=branch,
        expected_head=head,
        message="feature: 保存中文分支修改",
    )
    repeated = engine.commit(
        operation_id="commit_host_basic_01234567",
        apply_receipt=apply_receipt,
        branch=branch,
        expected_head=head,
        message="feature: 保存中文分支修改",
    )

    assert repeated == receipt
    assert receipt.branch == branch
    assert _host_commit_git(root, "rev-parse", "HEAD") == receipt.commit_sha
    assert _host_commit_git(root, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert (root / ".git" / "config").read_bytes() == config_before
    assert (head_reflog.read_bytes(), branch_reflog.read_bytes()) == reflogs_before
    assert not any(
        forbidden in command
        for command in observed
        for forbidden in ("remote", "fetch", "push", "ls-remote")
    )
    assert any(
        "update-ref" in command
        and "--no-deref" in command
        and "--no-create-reflog" in command
        for command in observed
    )
    operation_environments = [
        environment for environment in observed_environments if "GIT_DIR" in environment
    ]
    assert operation_environments
    assert all(
        environment["GIT_DIR"] == str(root / ".git")
        and environment["GIT_WORK_TREE"] == str(root)
        and environment["GIT_COMMON_DIR"] == str(root / ".git")
        and environment["GIT_NO_LAZY_FETCH"] == "1"
        and environment["GIT_NO_REPLACE_OBJECTS"] == "1"
        and "GIT_OBJECT_DIRECTORY" in environment
        and "GIT_ALTERNATE_OBJECT_DIRECTORIES" in environment
        for environment in operation_environments
    )
    assert all(
        environment["GIT_NO_LAZY_FETCH"] == "1"
        and environment["GIT_NO_REPLACE_OBJECTS"] == "1"
        for environment in observed_environments
    )

    undone = engine.undo(
        operation_id="undo_host_basic_0123456789",
        apply_receipt=apply_receipt,
        commit_receipt=receipt,
        branch=branch,
    )
    assert undone == receipt
    assert _host_commit_git(root, "rev-parse", "HEAD") == head
    assert (root / "marker.txt").read_bytes() == b"after-n4p7\n"
    assert _host_commit_git(root, "status", "--porcelain=v1", "--untracked-files=all") == "M marker.txt"
    assert engine.undo(
        operation_id="undo_host_basic_0123456789",
        apply_receipt=apply_receipt,
        commit_receipt=receipt,
        branch=branch,
    ) == receipt
    assert (root / ".git" / "config").read_bytes() == config_before


def test_host_commit_operation_id_cannot_change_message(tmp_path: Path) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    engine = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        enforce_windows=False,
    )
    engine.commit(
        operation_id="commit_message_bound_012345",
        apply_receipt=apply_receipt,
        branch="feature/local-k8m2",
        expected_head=head,
        message="feature: first immutable message",
    )

    with pytest.raises(HostCommitError) as conflict:
        engine.commit(
            operation_id="commit_message_bound_012345",
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: changed message",
        )
    assert conflict.value.code == "operation_conflict"


@pytest.mark.parametrize(
    "phase",
    ["commit_after_receipt", "commit_after_index", "commit_after_ref"],
)
def test_host_commit_reconciles_each_metadata_crash_window(
    tmp_path: Path,
    phase: str,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)

    def crash(current: str) -> None:
        if current == phase:
            raise RuntimeError(f"crash:{phase}")

    interrupted = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        mutation_hook=crash,
        enforce_windows=False,
    )
    operation_id = "commit_crash_window_012345"
    with pytest.raises(RuntimeError, match=f"crash:{phase}"):
        interrupted.commit(
            operation_id=operation_id,
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: reconcile interrupted commit",
        )

    restarted = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        HostOperationJournal(tmp_path / "host-operations.bin", _XorProtector()),
        enforce_windows=False,
    )
    state, receipt = restarted.reconcile(operation_id)
    assert state == "committed"
    assert receipt is not None
    assert _host_commit_git(root, "rev-parse", "HEAD") == receipt.commit_sha
    assert _host_commit_git(root, "rev-list", "--count", f"{head}..HEAD") == "1"
    assert _host_commit_git(root, "status", "--porcelain=v1", "--untracked-files=all") == ""


@pytest.mark.parametrize(
    "phase",
    ["undo_after_intent", "undo_after_index", "undo_after_ref"],
)
def test_host_commit_reconciles_each_undo_crash_window(
    tmp_path: Path,
    phase: str,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    receipt = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        enforce_windows=False,
    ).commit(
        operation_id="commit_before_undo_01234567",
        apply_receipt=apply_receipt,
        branch="feature/local-k8m2",
        expected_head=head,
        message="feature: prepare undo crash test",
    )

    def crash(current: str) -> None:
        if current == phase:
            raise RuntimeError(f"crash:{phase}")

    interrupted = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        mutation_hook=crash,
        enforce_windows=False,
    )
    operation_id = "undo_crash_window_01234567"
    with pytest.raises(RuntimeError, match=f"crash:{phase}"):
        interrupted.undo(
            operation_id=operation_id,
            apply_receipt=apply_receipt,
            commit_receipt=receipt,
            branch="feature/local-k8m2",
        )

    restarted = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        HostOperationJournal(tmp_path / "host-operations.bin", _XorProtector()),
        enforce_windows=False,
    )
    state, reconciled = restarted.reconcile(operation_id)
    assert state == "undone"
    assert reconciled == receipt
    assert _host_commit_git(root, "rev-parse", "HEAD") == head
    assert (root / "marker.txt").read_bytes() == b"after-n4p7\n"
    assert _host_commit_git(root, "status", "--porcelain=v1", "--untracked-files=all") == "M marker.txt"


def test_host_commit_preserves_crlf_worktree_but_writes_normalized_blob(
    tmp_path: Path,
) -> None:
    before = b"before-r7k3\r\n"
    after = b"after-n4p7\r\n"
    root, head = _host_commit_repository(
        tmp_path,
        content=before,
        autocrlf=True,
    )
    assert _host_commit_git(root, "show", f"{head}:marker.txt") == "before-r7k3"
    target = root / "marker.txt"
    target.write_bytes(after)
    patch = _host_commit_patch("marker.txt", b"before-r7k3\n", b"after-n4p7\n")
    journal = HostOperationJournal(tmp_path / "host-operations.bin", _XorProtector())
    apply_receipt = ApplyReceipt(
        apply_id="apply_host_commit_0123456789",
        revision=13,
        snapshot_fingerprint=HOST_COMMIT_FINGERPRINT,
        files=(
            ApplyFileReceipt(
                path="marker.txt",
                existed_before=True,
                before_sha256=hashlib.sha256(before).hexdigest(),
                after_sha256=hashlib.sha256(after).hexdigest(),
            ),
        ),
        applied_at=1_000.0,
    )
    receipt_payload = {
        "apply_id": apply_receipt.apply_id,
        "revision": apply_receipt.revision,
        "snapshot_fingerprint": apply_receipt.snapshot_fingerprint,
        "files": [
            {
                "path": "marker.txt",
                "existed_before": True,
                "before_sha256": hashlib.sha256(before).hexdigest(),
                "after_sha256": hashlib.sha256(after).hexdigest(),
            }
        ],
        "applied_at": apply_receipt.applied_at,
    }
    journal.create(
        operation_id=apply_receipt.apply_id,
        action="apply",
        project_id=HOST_COMMIT_PROJECT_ID,
        revision=13,
        branch="feature/local-k8m2",
        expected_head=head,
        patch_sha256=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
        patch=patch,
    )
    journal.transition(
        apply_receipt.apply_id,
        "applying",
        apply_receipt=receipt_payload,
    )
    metadata = target.stat(follow_symlinks=False)
    journal.transition(
        apply_receipt.apply_id,
        "applied",
        file_identities=(f"{metadata.st_dev:x}-{metadata.st_ino:x}:marker.txt",),
    )
    assert target.read_bytes() == after

    receipt = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        enforce_windows=False,
    ).commit(
        operation_id="commit_crlf_normalized_0123",
        apply_receipt=apply_receipt,
        branch="feature/local-k8m2",
        expected_head=head,
        message="feature: preserve CRLF checkout",
    )

    assert (root / "marker.txt").read_bytes() == after
    blob = subprocess.run(
        ("git", "show", f"{receipt.commit_sha}:marker.txt"),
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=20,
    ).stdout
    assert blob == b"after-n4p7\n"
    assert _host_commit_git(root, "status", "--porcelain=v1", "--untracked-files=all") == ""


def test_host_commit_rejects_same_content_replacement_identity(tmp_path: Path) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    target = root / "marker.txt"
    replacement = root / ".manual-replacement"
    replacement.write_bytes(target.read_bytes())
    os.replace(replacement, target)

    with pytest.raises(HostCommitError) as conflict:
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            enforce_windows=False,
        ).commit(
            operation_id="commit_identity_guard_01234",
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: reject replaced object",
        )
    assert conflict.value.code == "target_changed"
    assert target.read_bytes() == b"after-n4p7\n"
    assert _host_commit_git(root, "rev-parse", "HEAD") == head


@pytest.mark.skipif(os.name == "nt", reason="POSIX fail-closed contract")
def test_host_commit_production_mode_rejects_posix_before_side_effect(
    tmp_path: Path,
) -> None:
    root, head = _host_commit_repository(tmp_path)
    status_before = _host_commit_git(root, "status", "--porcelain=v1", "--untracked-files=all")

    with pytest.raises(HostCommitError) as rejected:
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            HostOperationJournal(tmp_path / "production-operations.bin", _XorProtector()),
        )

    assert rejected.value.code == "windows_required"
    assert _host_commit_git(root, "rev-parse", "HEAD") == head
    assert _host_commit_git(root, "status", "--porcelain=v1", "--untracked-files=all") == status_before


@pytest.mark.parametrize("metadata_leaf", ["ref", "reflog", "loose_object"])
def test_host_commit_rejects_git_metadata_leaf_symlinks(
    tmp_path: Path,
    metadata_leaf: str,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    branch = "feature/local-k8m2"
    if metadata_leaf == "ref":
        target = root / ".git" / "refs" / "heads" / Path(*branch.split("/"))
    elif metadata_leaf == "reflog":
        target = root / ".git" / "logs" / "refs" / "heads" / Path(*branch.split("/"))
    else:
        object_id = _host_commit_git(root, "rev-parse", f"{head}:marker.txt")
        target = root / ".git" / "objects" / object_id[:2] / object_id[2:]
    outside = tmp_path / f"outside-{metadata_leaf}"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation is not available on this host")
    outside_before = outside.read_bytes()

    with pytest.raises(HostCommitError) as rejected:
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            enforce_windows=False,
        ).commit(
            operation_id=f"commit_{metadata_leaf}_link_012345",
            apply_receipt=apply_receipt,
            branch=branch,
            expected_head=head,
            message="feature: reject Git metadata link",
        )

    assert rejected.value.code == "repository_unsafe"
    assert outside.read_bytes() == outside_before
    assert target.is_symlink()
    assert _host_commit_git(root, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("replaced_path", ["index", "index.lock"])
def test_host_commit_does_not_overwrite_same_content_index_replacement(
    tmp_path: Path,
    replaced_path: str,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    operation_id = f"commit_replace_{replaced_path.replace('.', '_')}_012345"
    replacement_identity: str | None = None
    replacement_content: bytes | None = None

    def replace_metadata(phase: str) -> None:
        nonlocal replacement_identity, replacement_content
        if phase != "metadata_before_index":
            return
        target = root / ".git" / replaced_path
        replacement = root / f"manual-{replaced_path.replace('.', '-')}"
        replacement_content = target.read_bytes()
        replacement.write_bytes(replacement_content)
        os.replace(replacement, target)
        metadata = target.stat(follow_symlinks=False)
        replacement_identity = f"{metadata.st_dev:x}-{metadata.st_ino:x}"

    with pytest.raises(HostCommitError) as conflict:
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            mutation_hook=replace_metadata,
            enforce_windows=False,
        ).commit(
            operation_id=operation_id,
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: reject replaced index metadata",
        )

    assert conflict.value.code == "index_changed"
    record = journal.get(operation_id)
    assert record is not None
    assert record.state == "conflict"
    assert record.commit_receipt is not None
    assert _host_commit_git(root, "rev-parse", "HEAD") == head
    target = root / ".git" / replaced_path
    assert target.read_bytes() == replacement_content
    metadata = target.stat(follow_symlinks=False)
    assert f"{metadata.st_dev:x}-{metadata.st_ino:x}" == replacement_identity
    if replaced_path == "index":
        assert not (root / ".git" / "index.lock").exists()
        assert (
            root
            / ".git"
            / "modelmirror-transactions"
            / f"{operation_id}.index-conflict"
        ).exists()
    else:
        assert (root / ".git" / "index").exists()


def test_host_commit_quarantines_owned_lock_after_pre_ref_conflict(tmp_path: Path) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    operation_id = "commit_owned_lock_conflict_0123"

    def introduce_conflict(phase: str) -> None:
        if phase == "metadata_before_ref":
            (root / "manual-race.txt").write_text("manual-race-q9t2\n", encoding="utf-8")

    with pytest.raises(HostCommitError) as conflict:
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            mutation_hook=introduce_conflict,
            enforce_windows=False,
        ).commit(
            operation_id=operation_id,
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: quarantine owned index lock",
        )

    assert conflict.value.code == "head_changed"
    record = journal.get(operation_id)
    assert record is not None and record.state == "conflict"
    assert _host_commit_git(root, "rev-parse", "HEAD") == head
    assert (root / ".git" / "index").is_file()
    assert not (root / ".git" / "index.lock").exists()
    artifact = (
        root
        / ".git"
        / "modelmirror-transactions"
        / f"{operation_id}.index-conflict"
    )
    assert artifact.is_file()
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() == record.index_sha256


def test_host_commit_preserves_unknown_lock_while_quarantining_owned_stage(
    tmp_path: Path,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    operation_id = "commit_unknown_lock_conflict_012"

    def crash_after_intent(phase: str) -> None:
        if phase == "commit_after_receipt":
            raise RuntimeError("simulated restart")

    with pytest.raises(RuntimeError, match="simulated restart"):
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            mutation_hook=crash_after_intent,
            enforce_windows=False,
        ).commit(
            operation_id=operation_id,
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: preserve unknown index lock",
        )
    unknown_lock = root / ".git" / "index.lock"
    unknown_content = b"manual-unknown-lock-r8v3"
    unknown_lock.write_bytes(unknown_content)
    metadata = unknown_lock.stat(follow_symlinks=False)
    unknown_identity = f"{metadata.st_dev:x}-{metadata.st_ino:x}"

    state, _receipt = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        HostOperationJournal(tmp_path / "host-operations.bin", _XorProtector()),
        enforce_windows=False,
    ).reconcile(operation_id)

    assert state == "conflict"
    assert unknown_lock.read_bytes() == unknown_content
    metadata = unknown_lock.stat(follow_symlinks=False)
    assert f"{metadata.st_dev:x}-{metadata.st_ino:x}" == unknown_identity
    assert not (
        root
        / ".git"
        / "modelmirror-transactions"
        / f"{operation_id}.commit-index"
    ).exists()
    assert (
        root
        / ".git"
        / "modelmirror-transactions"
        / f"{operation_id}.index-conflict"
    ).exists()
    assert _host_commit_git(root, "rev-parse", "HEAD") == head


def test_host_commit_conflict_marker_survives_journal_failure_and_blocks_replay(
    tmp_path: Path,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    operation_id = "commit_failed_conflict_log_0123"

    def crash_after_intent(phase: str) -> None:
        if phase == "commit_after_receipt":
            raise RuntimeError("simulated restart")

    with pytest.raises(RuntimeError, match="simulated restart"):
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            mutation_hook=crash_after_intent,
            enforce_windows=False,
        ).commit(
            operation_id=operation_id,
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: persist fail-closed conflict",
        )
    conflict_file = root / "manual-conflict.txt"
    conflict_file.write_text("conflict-z8k4\n", encoding="utf-8")
    journal.protector.fail_protect = True

    with pytest.raises(HostCommitError) as unavailable:
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            enforce_windows=False,
        ).reconcile(operation_id)
    assert unavailable.value.code == "operation_log_unavailable"
    marker = (
        root
        / ".git"
        / "modelmirror-transactions"
        / f"{operation_id}.commit-conflict"
    )
    assert marker.read_bytes() == f"{operation_id}\n".encode("ascii")
    assert journal.get(operation_id).state == "committing"
    assert not (root / ".git" / "index.lock").exists()

    conflict_file.unlink()
    journal.protector.fail_protect = False
    restarted = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        HostOperationJournal(tmp_path / "host-operations.bin", _XorProtector()),
        enforce_windows=False,
    )
    state, receipt = restarted.reconcile(operation_id)

    assert state == "conflict"
    assert receipt is not None
    assert _host_commit_git(root, "rev-parse", "HEAD") == head
    assert restarted.journal.get(operation_id).state == "conflict"


def test_host_commit_recovers_hard_stop_between_index_backup_and_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    operation_id = "commit_two_phase_index_012345"
    import server.coding_project_host.host_commit_engine as commit_module

    real_move = commit_module._move_verified_no_replace
    crashed = False

    def crash_after_backup(source, destination, *args, **kwargs):
        nonlocal crashed
        result = real_move(source, destination, *args, **kwargs)
        expected_backup = (
            root
            / ".git"
            / "modelmirror-transactions"
            / f"{operation_id}.index-before"
        )
        if not crashed and Path(source) == root / ".git" / "index" and Path(destination) == expected_backup:
            crashed = True
            raise SystemExit("simulated hard stop")
        return result

    monkeypatch.setattr(commit_module, "_move_verified_no_replace", crash_after_backup)
    with pytest.raises(SystemExit, match="simulated hard stop"):
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            enforce_windows=False,
        ).commit(
            operation_id=operation_id,
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: recover two phase index publish",
        )
    monkeypatch.setattr(commit_module, "_move_verified_no_replace", real_move)
    backup = (
        root
        / ".git"
        / "modelmirror-transactions"
        / f"{operation_id}.index-before"
    )
    assert not (root / ".git" / "index").exists()
    assert backup.is_file()
    assert (root / ".git" / "index.lock").is_file()

    restarted = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        HostOperationJournal(tmp_path / "host-operations.bin", _XorProtector()),
        enforce_windows=False,
    )
    state, receipt = restarted.reconcile(operation_id)

    assert state == "committed"
    assert receipt is not None
    assert _host_commit_git(root, "rev-parse", "HEAD") == receipt.commit_sha
    assert _host_commit_git(root, "rev-list", "--count", f"{head}..HEAD") == "1"
    assert _host_commit_git(root, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert (root / ".git" / "index").is_file()
    assert not (root / ".git" / "index.lock").exists()
    assert not backup.exists()


def test_host_commit_replay_after_undo_does_not_recreate_commit(tmp_path: Path) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    engine = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        enforce_windows=False,
    )
    operation_id = "commit_then_undo_replay_01234"
    receipt = engine.commit(
        operation_id=operation_id,
        apply_receipt=apply_receipt,
        branch="feature/local-k8m2",
        expected_head=head,
        message="feature: do not replay old commit",
    )
    engine.undo(
        operation_id="undo_then_replay_012345678",
        apply_receipt=apply_receipt,
        commit_receipt=receipt,
        branch="feature/local-k8m2",
    )

    with pytest.raises(HostCommitError) as stale:
        engine.commit(
            operation_id=operation_id,
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: do not replay old commit",
        )
    assert stale.value.code == "commit_not_current"
    assert _host_commit_git(root, "rev-parse", "HEAD") == head
    assert _host_commit_git(root, "status", "--porcelain=v1", "--untracked-files=all") == "M marker.txt"
    assert not (root / ".git" / "index.lock").exists()


def test_host_commit_only_allows_latest_linear_commit_to_be_undone(tmp_path: Path) -> None:
    root, head, journal, first_apply = _host_commit_applied(tmp_path)
    branch = "feature/local-k8m2"
    engine = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        enforce_windows=False,
    )
    first_commit = engine.commit(
        operation_id="commit_linear_first_0123456",
        apply_receipt=first_apply,
        branch=branch,
        expected_head=head,
        message="feature: first linear commit",
    )
    patch = DraftWorkspace._unified_diff(
        "second.txt",
        "",
        "second-round-q9t2\n",
        status="added",
    )
    second_apply = HostGitApplyEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        enforce_windows=False,
    ).apply(
        operation_id="apply_linear_second_0123456",
        revision=14,
        branch=branch,
        expected_head=first_commit.commit_sha,
        snapshot_fingerprint=HOST_COMMIT_FINGERPRINT,
        patch=patch,
        paths=("second.txt",),
    )
    second_commit = engine.commit(
        operation_id="commit_linear_second_012345",
        apply_receipt=second_apply,
        branch=branch,
        expected_head=first_commit.commit_sha,
        message="feature: second linear commit",
    )
    engine.undo(
        operation_id="undo_linear_second_01234567",
        apply_receipt=second_apply,
        commit_receipt=second_commit,
        branch=branch,
    )

    with pytest.raises(HostCommitError) as stale:
        engine.undo(
            operation_id="undo_linear_stale_012345678",
            apply_receipt=first_apply,
            commit_receipt=first_commit,
            branch=branch,
        )
    assert stale.value.code == "undo_conflict"
    assert _host_commit_git(root, "rev-parse", "HEAD") == first_commit.commit_sha
    assert _host_commit_git(root, "status", "--porcelain=v1", "--untracked-files=all") == "?? second.txt"
    assert journal.get("undo_linear_stale_012345678").state == "conflict"
    assert not (root / ".git" / "index.lock").exists()


def test_host_commit_does_not_restore_tampered_index_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    operation_id = "commit_tampered_backup_012345"
    import server.coding_project_host.host_commit_engine as commit_module

    real_move = commit_module._move_verified_no_replace

    def stop_after_backup(source, destination, *args, **kwargs):
        result = real_move(source, destination, *args, **kwargs)
        if Path(source) == root / ".git" / "index" and Path(destination).name.endswith(
            ".index-before"
        ):
            raise SystemExit("simulated stop after index backup")
        return result

    monkeypatch.setattr(commit_module, "_move_verified_no_replace", stop_after_backup)
    with pytest.raises(SystemExit, match="simulated stop"):
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            enforce_windows=False,
        ).commit(
            operation_id=operation_id,
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: preserve tampered index evidence",
        )
    monkeypatch.setattr(commit_module, "_move_verified_no_replace", real_move)
    backup = (
        root
        / ".git"
        / "modelmirror-transactions"
        / f"{operation_id}.index-before"
    )
    before_identity = backup.stat(follow_symlinks=False)
    tampered = bytearray(backup.read_bytes())
    tampered[-1] ^= 1
    with backup.open("r+b") as handle:
        handle.seek(0)
        handle.write(tampered)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
    after_identity = backup.stat(follow_symlinks=False)
    assert (before_identity.st_dev, before_identity.st_ino) == (
        after_identity.st_dev,
        after_identity.st_ino,
    )

    restarted = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        HostOperationJournal(tmp_path / "host-operations.bin", _XorProtector()),
        enforce_windows=False,
    )
    with pytest.raises(HostCommitError) as unavailable:
        restarted.reconcile(operation_id)

    assert unavailable.value.code == "operation_log_unavailable"
    assert backup.read_bytes() == bytes(tampered)
    assert not (root / ".git" / "index").exists()
    assert restarted.journal.get(operation_id).state == "committing"


def test_host_commit_terminal_records_ignore_later_same_tree_index_identity(
    tmp_path: Path,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    engine = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        enforce_windows=False,
    )
    commit_id = "commit_terminal_index_012345"
    receipt = engine.commit(
        operation_id=commit_id,
        apply_receipt=apply_receipt,
        branch="feature/local-k8m2",
        expected_head=head,
        message="feature: terminal index identity",
    )
    index = root / ".git" / "index"
    replacement = root / "replacement-index"
    replacement.write_bytes(index.read_bytes())
    os.replace(replacement, index)

    state, recovered = engine.reconcile(commit_id)
    assert state == "committed"
    assert recovered == receipt

    undo_id = "undo_terminal_index_01234567"
    engine.undo(
        operation_id=undo_id,
        apply_receipt=apply_receipt,
        commit_receipt=receipt,
        branch="feature/local-k8m2",
    )
    replacement.write_bytes(index.read_bytes())
    os.replace(replacement, index)

    state, recovered = engine.reconcile(undo_id)
    assert state == "undone"
    assert recovered == receipt


def test_host_commit_terminal_undo_stays_terminal_after_later_head_change(
    tmp_path: Path,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    engine = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        enforce_windows=False,
    )
    receipt = engine.commit(
        operation_id="commit_terminal_history_0123",
        apply_receipt=apply_receipt,
        branch="feature/local-k8m2",
        expected_head=head,
        message="feature: preserve terminal history",
    )
    undo_id = "undo_terminal_history_012345"
    engine.undo(
        operation_id=undo_id,
        apply_receipt=apply_receipt,
        commit_receipt=receipt,
        branch="feature/local-k8m2",
    )
    _host_commit_git(root, "commit", "--allow-empty", "-m", "manual later commit")

    with pytest.raises(HostCommitError) as stale:
        engine.reconcile(undo_id)

    assert stale.value.code == "undo_not_current"
    assert journal.get(undo_id).state == "undone"


def test_host_commit_persists_conflict_when_marker_cannot_be_created(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    operation_id = "commit_marker_unavailable_0123"
    import server.coding_project_host.host_commit_engine as commit_module

    def stop_after_intent(phase: str) -> None:
        if phase == "commit_after_receipt":
            raise RuntimeError("simulated restart")

    with pytest.raises(RuntimeError, match="simulated restart"):
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            mutation_hook=stop_after_intent,
            enforce_windows=False,
        ).commit(
            operation_id=operation_id,
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: journal conflict fallback",
        )
    (root / "manual-conflict.txt").write_text("manual-q7v4\n", encoding="utf-8")
    real_write = commit_module._write_durable_no_replace

    def reject_conflict_marker(path, *args, **kwargs):
        if Path(path).name.endswith(".commit-conflict"):
            raise OSError("simulated marker failure")
        return real_write(path, *args, **kwargs)

    monkeypatch.setattr(commit_module, "_write_durable_no_replace", reject_conflict_marker)
    state, recovered = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        enforce_windows=False,
    ).reconcile(operation_id)

    assert state == "conflict"
    assert recovered is not None
    assert journal.get(operation_id).state == "conflict"
    assert _host_commit_git(root, "rev-parse", "HEAD") == head
    assert not (root / ".git" / "index.lock").exists()


def test_host_commit_rescans_objects_after_commit_tree(
    tmp_path: Path,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    object_root = root / ".git" / "objects"
    existing = {path for path in object_root.rglob("*") if path.is_file()}
    outside = tmp_path / "outside-new-object"
    replaced: Path | None = None

    def replace_new_object(phase: str) -> None:
        nonlocal replaced
        if phase != "commit_after_tree":
            return
        new_objects = [
            path for path in object_root.rglob("*") if path.is_file() and path not in existing
        ]
        assert new_objects
        replaced = new_objects[0]
        outside.write_bytes(replaced.read_bytes())
        try:
            replaced.unlink()
            replaced.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"Metadata replacement is blocked on this host: {exc}")

    with pytest.raises(HostCommitError) as rejected:
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            mutation_hook=replace_new_object,
            enforce_windows=False,
        ).commit(
            operation_id="commit_post_tree_link_012345",
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: reject replaced new object",
        )

    assert rejected.value.code == "repository_unsafe"
    assert replaced is not None and replaced.is_symlink()
    assert _host_commit_git(root, "rev-parse", "HEAD") == head


def test_host_commit_binds_original_index_before_commit_tree(
    tmp_path: Path,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    replacement_identity: str | None = None

    def replace_index(phase: str) -> None:
        nonlocal replacement_identity
        if phase != "commit_after_tree":
            return
        index = root / ".git" / "index"
        manual = root / "manual-index-before-stage"
        manual.write_bytes(index.read_bytes())
        os.replace(manual, index)
        metadata = index.stat(follow_symlinks=False)
        replacement_identity = f"{metadata.st_dev:x}-{metadata.st_ino:x}"

    with pytest.raises(HostCommitError) as rejected:
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            mutation_hook=replace_index,
            enforce_windows=False,
        ).commit(
            operation_id="commit_pre_stage_index_012345",
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: bind original index",
        )

    assert rejected.value.code == "index_changed"
    assert _host_commit_git(root, "rev-parse", "HEAD") == head
    current = (root / ".git" / "index").stat(follow_symlinks=False)
    assert f"{current.st_dev:x}-{current.st_ino:x}" == replacement_identity


def test_host_commit_preserves_untouched_skip_worktree_index_state(
    tmp_path: Path,
) -> None:
    root, _ = _host_commit_repository(tmp_path)
    hidden = root / "hidden" / "b.txt"
    hidden.parent.mkdir()
    hidden.write_text("hidden-r8v3\n", encoding="utf-8")
    _host_commit_git(root, "add", "--", "hidden/b.txt")
    _host_commit_git(root, "commit", "-m", "add hidden baseline")
    head = _host_commit_git(root, "rev-parse", "HEAD")
    _host_commit_git(root, "update-index", "--skip-worktree", "--", "hidden/b.txt")
    hidden.unlink()
    assert _host_commit_git(root, "status", "--porcelain=v1", "--untracked-files=all") == ""
    assert _host_commit_git(root, "ls-files", "-t", "--", "hidden/b.txt").startswith("S ")

    before = (root / "marker.txt").read_bytes()
    after = b"skip-worktree-safe-n4p7\n"
    journal = HostOperationJournal(tmp_path / "skip-operations.bin", _XorProtector())
    apply_receipt = HostGitApplyEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        enforce_windows=False,
    ).apply(
        operation_id="apply_skip_worktree_012345",
        revision=13,
        branch="feature/local-k8m2",
        expected_head=head,
        snapshot_fingerprint=HOST_COMMIT_FINGERPRINT,
        patch=_host_commit_patch("marker.txt", before, after),
        paths=("marker.txt",),
    )
    engine = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        enforce_windows=False,
    )
    receipt = engine.commit(
        operation_id="commit_skip_worktree_01234",
        apply_receipt=apply_receipt,
        branch="feature/local-k8m2",
        expected_head=head,
        message="feature: preserve sparse index flags",
    )

    assert _host_commit_git(root, "ls-files", "-t", "--", "hidden/b.txt").startswith("S ")
    assert not hidden.exists()
    assert _host_commit_git(root, "status", "--porcelain=v1", "--untracked-files=all") == ""

    engine.undo(
        operation_id="undo_skip_worktree_01234567",
        apply_receipt=apply_receipt,
        commit_receipt=receipt,
        branch="feature/local-k8m2",
    )
    assert _host_commit_git(root, "ls-files", "-t", "--", "hidden/b.txt").startswith("S ")
    assert not hidden.exists()
    assert _host_commit_git(root, "status", "--porcelain=v1", "--untracked-files=all") == "M marker.txt"


@pytest.mark.skipif(os.name != "nt", reason="Windows helper native contract")
def test_host_commit_windows_native_apply_commit_and_undo(tmp_path: Path) -> None:
    branch = "feature/windows-native-r7k3"
    root, head = _host_commit_repository(tmp_path, branch=branch)
    journal = HostOperationJournal(tmp_path / "windows-operations.bin", _XorProtector())
    apply_receipt = HostGitApplyEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
    ).apply(
        operation_id="apply_windows_native_012345",
        revision=13,
        branch=branch,
        expected_head=head,
        snapshot_fingerprint=HOST_COMMIT_FINGERPRINT,
        patch=_host_commit_patch(
            "marker.txt",
            b"before-r7k3\n",
            b"windows-after-n4p7\n",
        ),
        paths=("marker.txt",),
    )
    engine = HostGitCommitEngine(root, HOST_COMMIT_PROJECT_ID, journal)
    receipt = engine.commit(
        operation_id="commit_windows_native_0123",
        apply_receipt=apply_receipt,
        branch=branch,
        expected_head=head,
        message="feature: verify Windows local commit",
    )
    assert _host_commit_git(root, "rev-parse", "HEAD") == receipt.commit_sha
    assert _host_commit_git(root, "status", "--porcelain=v1", "--untracked-files=all") == ""

    engine.undo(
        operation_id="undo_windows_native_012345",
        apply_receipt=apply_receipt,
        commit_receipt=receipt,
        branch=branch,
    )
    assert _host_commit_git(root, "rev-parse", "HEAD") == head
    assert (root / "marker.txt").read_bytes() == b"windows-after-n4p7\n"
    assert _host_commit_git(root, "status", "--porcelain=v1", "--untracked-files=all") == "M marker.txt"


@pytest.mark.skipif(os.name != "nt", reason="Windows helper native contract")
@pytest.mark.parametrize("replacement_kind", ["junction", "same_content"])
def test_host_commit_windows_private_object_cleanup_rejects_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    outside_dir = tmp_path / f"outside-private-object-{replacement_kind}"
    outside_dir.mkdir()
    sentinel = outside_dir / "sentinel.txt"
    sentinel.write_bytes(b"outside-private-object-r8v3\n")
    replaced_path: Path | None = None
    outside_object: Path | None = None
    parked_parent: Path | None = None
    real_remove = host_commit_engine_module._windows_remove_private_loose_object

    def replace_before_handle_open(
        path: Path,
        expected: bytes,
        *,
        expected_identity: str,
    ) -> None:
        nonlocal outside_object, parked_parent, replaced_path
        if replaced_path is None:
            replaced_path = path
            # Simulate a concurrent actor winning the exact interval between
            # the caller's first read and the private-store delete handle.
            if replacement_kind == "junction":
                parked_parent = path.parent.with_name(f"{path.parent.name}-parked")
                path.parent.rename(parked_parent)
                outside_object = outside_dir / path.name
                outside_object.write_bytes(expected)
                os.chmod(outside_object, stat.S_IREAD)
                created = subprocess.run(
                    [
                        "cmd.exe",
                        "/d",
                        "/c",
                        "mklink",
                        "/J",
                        str(path.parent),
                        str(outside_dir),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                if created.returncode != 0:
                    pytest.skip("directory reparse replacement is unavailable")
            else:
                os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
                path.unlink()
                path.write_bytes(expected)
        real_remove(
            path,
            expected,
            expected_identity=expected_identity,
        )

    monkeypatch.setattr(
        host_commit_engine_module,
        "_windows_remove_private_loose_object",
        replace_before_handle_open,
    )
    try:
        with pytest.raises(HostCommitError) as rejected:
            HostGitCommitEngine(
                root,
                HOST_COMMIT_PROJECT_ID,
                journal,
            ).commit(
                operation_id=f"commit_private_cleanup_{replacement_kind}_01",
                apply_receipt=apply_receipt,
                branch="feature/local-k8m2",
                expected_head=head,
                message="feature: reject private object cleanup replacement",
            )

        assert rejected.value.code == "repository_unsafe"
        assert replaced_path is not None
        assert _host_commit_git(root, "rev-parse", "HEAD") == head
        assert sentinel.read_bytes() == b"outside-private-object-r8v3\n"
        if replacement_kind == "junction":
            assert outside_object is not None
            assert outside_object.read_bytes() == replaced_path.read_bytes()
            assert outside_object.stat().st_file_attributes & 0x00000001
        else:
            assert replaced_path.is_file()
            assert not replaced_path.is_symlink()
    finally:
        if outside_object is not None:
            os.chmod(outside_object, stat.S_IREAD | stat.S_IWRITE)
        if parked_parent is not None and parked_parent.exists():
            if replaced_path is not None and replaced_path.parent.exists():
                os.rmdir(replaced_path.parent)
            parked_parent.rename(replaced_path.parent)


@pytest.mark.parametrize("reflog_name", ["HEAD", "branch"])
@pytest.mark.parametrize("replacement_kind", ["hardlink", "symlink"])
def test_host_commit_never_writes_reflog_replacement(
    tmp_path: Path,
    reflog_name: str,
    replacement_kind: str,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    outside = tmp_path / f"outside-{reflog_name}-{replacement_kind}"
    outside.write_bytes(b"outside-reflog-r8v3\n")
    outside_before = outside.read_bytes()
    branch = "feature/local-k8m2"

    def replace_parked_reflog(phase: str) -> None:
        if phase != "metadata_before_ref":
            return
        target = (
            root / ".git" / "logs" / "HEAD"
            if reflog_name == "HEAD"
            else root / ".git" / "logs" / "refs" / "heads" / Path(branch)
        )
        assert not target.exists()
        try:
            if replacement_kind == "hardlink":
                os.link(outside, target)
            else:
                target.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"reflog replacement is not available on this host: {exc}")

    with pytest.raises(HostCommitError):
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            mutation_hook=replace_parked_reflog,
            enforce_windows=False,
        ).commit(
            operation_id=f"commit_reflog_{reflog_name.lower()}_{replacement_kind}_01",
            apply_receipt=apply_receipt,
            branch=branch,
            expected_head=head,
            message="feature: reject reflog replacement",
        )

    assert outside.read_bytes() == outside_before
    assert _host_commit_git(root, "rev-parse", "HEAD") == head


@pytest.mark.parametrize(
    "redirect_name",
    ["commondir", "alternates", "http-alternates"],
)
def test_host_commit_rechecks_git_redirections_before_ref_side_effect(
    tmp_path: Path,
    redirect_name: str,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    outside = tmp_path / "outside-object-store"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("outside-q7m4\n", encoding="utf-8")
    branch_ref = root / ".git" / "refs" / "heads" / "feature" / "local-k8m2"
    branch_ref_before = branch_ref.read_bytes()

    def inject_redirect(phase: str) -> None:
        if phase != "metadata_before_ref":
            return
        target = (
            root / ".git" / "commondir"
            if redirect_name == "commondir"
            else root / ".git" / "objects" / "info" / redirect_name
        )
        target.write_text(str(outside), encoding="utf-8")

    with pytest.raises(HostCommitError) as rejected:
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            mutation_hook=inject_redirect,
            enforce_windows=False,
        ).commit(
            operation_id=f"commit_redirect_{redirect_name.replace('-', '_')}_012",
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: reject dynamic Git redirect",
        )

    assert rejected.value.code == "repository_unsafe"
    assert sentinel.read_text(encoding="utf-8") == "outside-q7m4\n"
    assert tuple(outside.iterdir()) == (sentinel,)
    assert branch_ref.read_bytes() == branch_ref_before == f"{head}\n".encode("ascii")


def test_host_commit_rejects_nested_symbolic_branch_ref_before_update(
    tmp_path: Path,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    branch = "feature/local-k8m2"
    other = "refs/heads/manual-target-r8v3"
    _host_commit_git(root, "update-ref", other, head)
    branch_ref = root / ".git" / "refs" / "heads" / Path(branch)
    branch_ref.write_text(f"ref: {other}\n", encoding="ascii")

    with pytest.raises(HostCommitError) as rejected:
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            enforce_windows=False,
        ).commit(
            operation_id="commit_nested_symbolic_ref_0123",
            apply_receipt=apply_receipt,
            branch=branch,
            expected_head=head,
            message="feature: reject nested symbolic ref",
        )

    assert rejected.value.code in {"branch_changed", "head_changed"}
    assert branch_ref.read_text(encoding="ascii") == f"ref: {other}\n"
    assert _host_commit_git(root, "rev-parse", other) == head


def test_host_commit_reflog_parent_link_cannot_move_external_log(
    tmp_path: Path,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    outside_refs = tmp_path / "outside-refs"
    outside_log = outside_refs / "heads" / "feature" / "local-k8m2"
    outside_log.parent.mkdir(parents=True)
    outside_log.write_bytes(b"outside-parent-chain-q7m4\n")

    def replace_parent(phase: str) -> None:
        if phase != "metadata_before_reflog_bind":
            return
        refs = root / ".git" / "logs" / "refs"
        refs.rename(root / ".git" / "logs" / "refs-real")
        try:
            refs.symlink_to(outside_refs, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"parent link is not available on this host: {exc}")

    with pytest.raises(HostCommitError) as rejected:
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            mutation_hook=replace_parent,
            enforce_windows=False,
        ).commit(
            operation_id="commit_reflog_parent_link_0123",
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: guard reflog parent chain",
        )

    assert rejected.value.code == "repository_unsafe"
    assert outside_log.read_bytes() == b"outside-parent-chain-q7m4\n"
    assert _host_commit_git(root, "rev-parse", "HEAD") == head


def test_host_commit_private_object_publish_rejects_missing_fanout_link(
    tmp_path: Path,
) -> None:
    before = b"before-r7k3\n"
    root, head = _host_commit_repository(tmp_path, content=before)
    counter = 0
    while True:
        after = f"private-object-{counter}-q9t2\n".encode("ascii")
        header = f"blob {len(after)}\0".encode("ascii")
        object_id = hashlib.sha1(header + after).hexdigest()
        if not (root / ".git" / "objects" / object_id[:2]).exists():
            break
        counter += 1
    journal = HostOperationJournal(tmp_path / "host-operations.bin", _XorProtector())
    apply_receipt = HostGitApplyEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        journal,
        enforce_windows=False,
    ).apply(
        operation_id="apply_private_object_0123456",
        revision=13,
        branch="feature/local-k8m2",
        expected_head=head,
        snapshot_fingerprint=HOST_COMMIT_FINGERPRINT,
        patch=_host_commit_patch("marker.txt", before, after),
        paths=("marker.txt",),
    )
    outside = tmp_path / "outside-fanout"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("outside-k8m2\n", encoding="utf-8")

    def insert_fanout_link(phase: str) -> None:
        if phase != "commit_before_object_publish":
            return
        target = root / ".git" / "objects" / object_id[:2]
        try:
            target.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"fanout link is not available on this host: {exc}")

    with pytest.raises(HostCommitError) as rejected:
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            mutation_hook=insert_fanout_link,
            enforce_windows=False,
        ).commit(
            operation_id="commit_private_fanout_012345",
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: isolate Git object publication",
        )

    assert rejected.value.code == "repository_unsafe"
    assert tuple(outside.iterdir()) == (sentinel,)
    assert _host_commit_git(root, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("ref_advanced", [False, True])
def test_host_commit_recovers_parked_reflogs_after_restart(
    tmp_path: Path,
    ref_advanced: bool,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    operation_id = f"commit_reflog_restart_{int(ref_advanced)}_012345"

    def stop_after_intent(phase: str) -> None:
        if phase == "commit_after_receipt":
            raise RuntimeError("simulated process stop")

    with pytest.raises(RuntimeError, match="simulated process stop"):
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            mutation_hook=stop_after_intent,
            enforce_windows=False,
        ).commit(
            operation_id=operation_id,
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: recover exact reflog state",
        )
    record = journal.get(operation_id)
    assert record is not None and record.commit_receipt is not None
    reflogs = (
        (
            root / ".git" / "logs" / "HEAD",
            root
            / ".git"
            / "modelmirror-transactions"
            / f"{operation_id}.head-reflog-before",
        ),
        (
            root / ".git" / "logs" / "refs" / "heads" / "feature" / "local-k8m2",
            root
            / ".git"
            / "modelmirror-transactions"
            / f"{operation_id}.branch-reflog-before",
        ),
    )
    reflogs_before = tuple(source.read_bytes() for source, _backup in reflogs)
    for source, backup in reflogs:
        os.replace(source, backup)
    if ref_advanced:
        _host_commit_git(
            root,
            "-c",
            "core.logAllRefUpdates=false",
            "update-ref",
            "--no-deref",
            "--no-create-reflog",
            "refs/heads/feature/local-k8m2",
            str(record.commit_receipt["commit_sha"]),
            head,
        )

    state, receipt = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        HostOperationJournal(tmp_path / "host-operations.bin", _XorProtector()),
        enforce_windows=False,
    ).reconcile(operation_id)

    assert state == "committed"
    assert receipt is not None
    assert _host_commit_git(root, "rev-parse", "HEAD") == receipt.commit_sha
    assert tuple(source.read_bytes() for source, _backup in reflogs) == reflogs_before
    assert not any(backup.exists() for _source, backup in reflogs)


def test_host_commit_retries_after_objects_were_already_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    operation_id = "commit_objects_published_012345"
    stopped = False

    def stop_after_publish(phase: str) -> None:
        nonlocal stopped
        if phase == "commit_after_tree" and not stopped:
            stopped = True
            raise RuntimeError("simulated stop after object publish")

    real_cleanup = HostGitCommitEngine._cleanup_private_object_store
    monkeypatch.setattr(
        HostGitCommitEngine,
        "_cleanup_private_object_store",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="simulated stop"):
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            mutation_hook=stop_after_publish,
            enforce_windows=False,
        ).commit(
            operation_id=operation_id,
            apply_receipt=apply_receipt,
            branch="feature/local-k8m2",
            expected_head=head,
            message="feature: retry published objects",
        )
    private_store = (
        root
        / ".git"
        / "modelmirror-transactions"
        / f"{operation_id}.private-objects"
    )
    assert private_store.is_dir()
    monkeypatch.setattr(
        HostGitCommitEngine,
        "_cleanup_private_object_store",
        real_cleanup,
    )

    receipt = HostGitCommitEngine(
        root,
        HOST_COMMIT_PROJECT_ID,
        HostOperationJournal(tmp_path / "host-operations.bin", _XorProtector()),
        enforce_windows=False,
    ).commit(
        operation_id=operation_id,
        apply_receipt=apply_receipt,
        branch="feature/local-k8m2",
        expected_head=head,
        message="feature: retry published objects",
    )

    assert _host_commit_git(root, "rev-parse", "HEAD") == receipt.commit_sha
    assert not private_store.exists()


def test_host_commit_rejects_replace_refs_and_disables_replace_objects(
    tmp_path: Path,
) -> None:
    root, head, journal, _apply_receipt = _host_commit_applied(tmp_path)
    tree = _host_commit_git(root, "rev-parse", f"{head}^{{tree}}")
    replacement = _host_commit_git(
        root,
        "commit-tree",
        tree,
        "-p",
        head,
        input_bytes=b"replacement must not be observed\n",
    )
    _host_commit_git(root, "replace", head, replacement)

    with pytest.raises(HostCommitError) as rejected:
        HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            enforce_windows=False,
        )

    assert rejected.value.code == "repository_unsafe"


@pytest.mark.parametrize("action", ["commit", "undo", "reconcile"])
@pytest.mark.parametrize(
    "unsafe_kind",
    ["extensions.refStorage", "core.excludesFile", "info-grafts"],
)
def test_host_commit_rejects_unsafe_metadata_before_operation_artifacts(
    tmp_path: Path,
    action: str,
    unsafe_kind: str,
) -> None:
    root, head, journal, apply_receipt = _host_commit_applied(tmp_path)
    branch = "feature/local-k8m2"
    committed: CommitReceipt | None = None
    if action == "undo":
        committed = HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            enforce_windows=False,
        ).commit(
            operation_id="commit_before_unsafe_undo_0123",
            apply_receipt=apply_receipt,
            branch=branch,
            expected_head=head,
            message="feature: prepare guarded undo",
        )

    if unsafe_kind == "info-grafts":
        (root / ".git" / "info" / "grafts").write_text(
            head + "\n",
            encoding="ascii",
        )
    else:
        value = "reftable" if unsafe_kind == "extensions.refStorage" else "../outside-ignore"
        _host_commit_git(root, "config", unsafe_kind, value)

    branch_ref = root / ".git" / "refs" / "heads" / Path(branch)
    ref_before = branch_ref.read_bytes()
    transaction_root = root / ".git" / "modelmirror-transactions"
    artifacts_before = tuple(
        sorted(path.relative_to(transaction_root).as_posix() for path in transaction_root.rglob("*"))
    )
    operation_id = f"{action}_unsafe_metadata_{unsafe_kind.replace('.', '_').replace('-', '_')}_01"

    with pytest.raises(HostCommitError) as rejected:
        engine = HostGitCommitEngine(
            root,
            HOST_COMMIT_PROJECT_ID,
            journal,
            enforce_windows=False,
        )
        if action == "commit":
            engine.commit(
                operation_id=operation_id,
                apply_receipt=apply_receipt,
                branch=branch,
                expected_head=head,
                message="feature: reject unsafe metadata",
            )
        elif action == "undo":
            assert committed is not None
            engine.undo(
                operation_id=operation_id,
                apply_receipt=apply_receipt,
                commit_receipt=committed,
                branch=branch,
            )
        else:
            engine.reconcile(operation_id)

    expected_code = (
        "repository_unsafe"
        if unsafe_kind == "info-grafts"
        else "repository_config_unsafe"
    )
    assert rejected.value.code == expected_code
    assert journal.get(operation_id) is None
    assert branch_ref.read_bytes() == ref_before
    assert tuple(
        sorted(path.relative_to(transaction_root).as_posix() for path in transaction_root.rglob("*"))
    ) == artifacts_before
