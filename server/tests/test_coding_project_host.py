from __future__ import annotations

import json
import hashlib

import pytest

from server.coding_project_host.operation_log import HostOperationJournal
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
    )
    assert transitioned.state == "applied"
    assert transitioned.apply_receipt is not None
    transitioned.apply_receipt["revision"] = 99
    assert restored.get(record.operation_id).apply_receipt["revision"] == 7
    with pytest.raises(Exception) as invalid_state:
        restored.transition(record.operation_id, "committed")
    assert getattr(invalid_state.value, "code", None) == "operation_state_invalid"

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
    protector.fail_protect = True
    with pytest.raises(OSError):
        journal.transition(record.operation_id, "applying")
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
        apply_receipt=apply_receipt,
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
        journal.transition(operation_id, "committed", commit_receipt=wrong_parent)
    assert getattr(parent_conflict.value, "code", None) == "operation_record_invalid"
    wrong_files = {**commit_receipt, "files": ["other.txt"]}
    with pytest.raises(Exception) as file_conflict:
        journal.transition(operation_id, "committed", commit_receipt=wrong_files)
    assert getattr(file_conflict.value, "code", None) == "operation_record_invalid"
    committed = journal.transition(
        operation_id,
        "committed",
        commit_receipt=commit_receipt,
    )
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
