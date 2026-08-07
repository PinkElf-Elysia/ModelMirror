from __future__ import annotations

import json

import pytest

from server.coding_runtime.project_host import ProjectHostError, ProjectHostStore
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
        "status",
        "connection_id",
        "created_at",
        "updated_at",
        "last_heartbeat_at",
    }

    restored = ProjectHostStore(state)
    assert restored.host_status()["paired"] is True
    assert restored.host_status()["available"] is False


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
