from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from server.coding_worker.contracts import WorkspaceSource
from server.coding_worker.source_adapters import (
    BuiltinGitWorkspaceSourceAdapter,
    HostSnapshotWorkspaceSourceAdapter,
    ProjectSnapshotLeaseGate,
    ProjectSnapshotWorkspaceSourceAdapter,
)
from server.coding_worker.workspace import (
    InMemoryWorkspaceSourceAdapter,
    MAX_EXTERNAL_SOURCE_FILE_BYTES,
    MAX_SOURCE_FILE_BYTES,
    WorkspaceBroker,
    WorkspaceError,
    WorkspaceSourceUnavailableError,
)
from server.coding_worker.runtime import (
    CodingWorkerRuntimeError,
    build_runtime_from_environment,
    register_workspace_source_adapter,
)


class FakeProjectSource:
    def __init__(self, lease: dict[str, object]) -> None:
        self.lease = lease
        self.acquired: list[tuple[str, str | None]] = []
        self.released: list[tuple[str, str]] = []
        self.release_result = True
        self.imports: list[dict[str, str]] = []
        self.checked: list[tuple[str, str]] = []

    async def check(self, project_id: str, expected_head: str) -> dict[str, object]:
        self.checked.append((project_id, expected_head))
        return {
            "id": self.lease["project_id"],
            "kind": "local_clone",
            "state": "available",
            "head": self.lease["head"],
        }

    async def acquire(
        self, project_id: str, *, expected_head: str | None = None
    ) -> dict[str, object]:
        self.acquired.append((project_id, expected_head))
        return dict(self.lease)

    async def release(self, project_id: str, lease_id: str) -> bool:
        self.released.append((project_id, lease_id))
        return self.release_result

    async def import_uploaded(self, **payload: str) -> dict[str, object]:
        self.imports.append(payload)
        return dict(self.lease)


class SingleLeaseProjectSource(FakeProjectSource):
    def __init__(self, lease: dict[str, object]) -> None:
        super().__init__(lease)
        self.active = False
        self.max_active = 0

    async def acquire(
        self, project_id: str, *, expected_head: str | None = None
    ) -> dict[str, object]:
        if self.active:
            raise RuntimeError("snapshot_busy")
        self.active = True
        self.max_active = max(self.max_active, 1)
        self.acquired.append((project_id, expected_head))
        await asyncio.sleep(0.02)
        return {**self.lease, "project_id": project_id, "head": expected_head}

    async def release(self, project_id: str, lease_id: str) -> bool:
        assert self.active is True
        self.active = False
        return await super().release(project_id, lease_id)


class CancelledAcquireProjectSource(SingleLeaseProjectSource):
    def __init__(self, lease: dict[str, object]) -> None:
        super().__init__(lease)
        self.first_acquire_started = asyncio.Event()

    async def acquire(
        self, project_id: str, *, expected_head: str | None = None
    ) -> dict[str, object]:
        self.acquired.append((project_id, expected_head))
        if not self.active:
            self.active = True
            self.first_acquire_started.set()
            await asyncio.Event().wait()
        return {**self.lease, "project_id": project_id, "head": expected_head}


class FakeProjectHost:
    def __init__(self, project: dict[str, object]) -> None:
        self.project = project
        self.requests: list[tuple[str, str | None]] = []
        self.finished: list[str] = []
        self.checked: list[tuple[str, str, str | None]] = []

    def check_project(
        self, project_id: str, head: str, branch: str | None
    ) -> dict[str, object]:
        self.checked.append((project_id, head, branch))
        return {
            "id": self.project.get("project_id"),
            "kind": "host_git",
            "state": "available",
            "head": self.project.get("head"),
        }

    async def request_snapshot(
        self,
        project_id: str,
        *,
        expected_head: str | None = None,
        expected_branch: str | None = None,
        managed_operation_id: str | None = None,
    ) -> dict[str, object]:
        self.requests.append((project_id, expected_head))
        return {
            "upload_id": "1" * 32,
            "archive_sha256": "2" * 64,
            "project": dict(self.project),
        }

    def finish_transfer(self, transfer_id: str) -> None:
        self.finished.append(transfer_id)


class RejectingAdmissionAdapter:
    def __init__(self, code: str) -> None:
        self.code = code

    async def admit(self, source: WorkspaceSource) -> object:
        raise WorkspaceError("private adapter failure", code=self.code)

    async def acquire(self, source: WorkspaceSource) -> object:
        raise AssertionError("admission must not acquire a source")


class AcquireOnlyAdapter:
    def __init__(self) -> None:
        self.acquired = False

    async def acquire(self, source: WorkspaceSource) -> object:
        self.acquired = True
        raise AssertionError("admission must not acquire a legacy adapter")


def test_runtime_rejects_source_adapter_without_admission_contract() -> None:
    with pytest.raises(CodingWorkerRuntimeError) as caught:
        register_workspace_source_adapter(
            "test-acquire-only", AcquireOnlyAdapter()  # type: ignore[arg-type]
        )

    assert caught.value.code == "coding_worker_adapter_invalid"


@pytest.mark.asyncio
async def test_source_admission_rejects_adapter_without_preflight_contract(
    tmp_path: Path,
) -> None:
    adapter = AcquireOnlyAdapter()
    broker = WorkspaceBroker(
        tmp_path,
        {"manifest": adapter},  # type: ignore[dict-item]
        id_key=b"a" * 32,
    )

    with pytest.raises(WorkspaceSourceUnavailableError) as caught:
        await broker.admit(
            WorkspaceSource(
                kind="manifest", source_id="opaque-source", revision="exact-revision"
            )
        )

    assert caught.value.reason == "not_registered"
    assert adapter.acquired is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("private_code", "public_reason"),
    (
        ("source_not_found", "not_registered"),
        ("source_revision_changed", "revision_changed"),
        ("project_host_offline", "temporarily_unavailable"),
        ("source_snapshot_unsafe", "unsafe"),
        ("source_limit_exceeded", "limit_exceeded"),
    ),
)
async def test_source_admission_maps_private_failures_to_safe_reasons(
    tmp_path: Path, private_code: str, public_reason: str
) -> None:
    broker = WorkspaceBroker(
        tmp_path,
        {"manifest": RejectingAdmissionAdapter(private_code)},
        id_key=b"a" * 32,
    )
    source = WorkspaceSource(
        kind="manifest", source_id="opaque-source", revision="exact-revision"
    )

    with pytest.raises(WorkspaceSourceUnavailableError) as caught:
        await broker.admit(source)

    assert caught.value.reason == public_reason

def _fingerprint(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(files.items()):
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def _source_root(tmp_path: Path, files: dict[str, bytes]) -> Path:
    workspace = tmp_path / "current" / "workspace"
    workspace.mkdir(parents=True)
    for relative, content in files.items():
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return tmp_path


def _lease(kind: str, files: dict[str, bytes]) -> dict[str, object]:
    return {
        "kind": kind,
        "lease_id": "lease_1",
        "project_id": "hostgit_" + "1" * 32 if kind == "host_git" else "local-" + "1" * 24,
        "head": "a" * 40,
        "file_count": len(files),
        "total_bytes": sum(map(len, files.values())),
        "fingerprint": _fingerprint(files),
    }


@pytest.mark.asyncio
async def test_project_snapshot_adapter_copies_exact_lease_and_releases(
    tmp_path: Path,
) -> None:
    files = {"README.md": b"hello\n", "src/app.py": b"print('ok')\n"}
    lease = _lease("local_clone", files)
    client = FakeProjectSource(lease)
    source = WorkspaceSource(
        kind="manifest",
        source_id=str(lease["project_id"]),
        revision=str(lease["head"]),
    )

    snapshot = await ProjectSnapshotWorkspaceSourceAdapter(
        client, _source_root(tmp_path, files)
    ).acquire(source)

    assert [(item.path, item.content) for item in snapshot.files] == sorted(files.items())
    assert client.acquired == [(source.source_id, source.revision)]
    assert client.released == [(source.source_id, "lease_1")]


@pytest.mark.asyncio
async def test_project_snapshot_adapter_serializes_complete_single_lease_lifecycle(
    tmp_path: Path,
) -> None:
    files = {"README.md": b"hello\n"}
    lease = _lease("local_clone", files)
    client = SingleLeaseProjectSource(lease)
    gate = ProjectSnapshotLeaseGate()
    adapter = ProjectSnapshotWorkspaceSourceAdapter(
        client,
        _source_root(tmp_path, files),
        lease_gate=gate,
    )
    sources = [
        WorkspaceSource(
            kind="manifest",
            source_id="local-" + marker * 24,
            revision=str(lease["head"]),
        )
        for marker in ("1", "2")
    ]

    snapshots = await asyncio.gather(*(adapter.acquire(source) for source in sources))

    assert [snapshot.source for snapshot in snapshots] == sources
    assert client.acquired == [
        (source.source_id, source.revision) for source in sources
    ]
    assert client.released == [
        (source.source_id, "lease_1") for source in sources
    ]
    assert client.active is False


@pytest.mark.asyncio
async def test_project_snapshot_adapter_reconciles_cancelled_acquire_side_effect(
    tmp_path: Path,
) -> None:
    files = {"README.md": b"hello\n"}
    lease = _lease("local_clone", files)
    client = CancelledAcquireProjectSource(lease)
    source = WorkspaceSource(
        kind="manifest",
        source_id=str(lease["project_id"]),
        revision=str(lease["head"]),
    )
    task = asyncio.create_task(
        ProjectSnapshotWorkspaceSourceAdapter(
            client, _source_root(tmp_path, files)
        ).acquire(source)
    )
    await client.first_acquire_started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.acquired == [
        (source.source_id, source.revision),
        (source.source_id, source.revision),
    ]
    assert client.released == [(source.source_id, "lease_1")]
    assert client.active is False


@pytest.mark.asyncio
async def test_project_source_admission_checks_without_acquiring_snapshot(
    tmp_path: Path,
) -> None:
    files = {"README.md": b"hello\n"}
    lease = _lease("local_clone", files)
    client = FakeProjectSource(lease)
    source = WorkspaceSource(
        kind="manifest",
        source_id=str(lease["project_id"]),
        revision=str(lease["head"]),
    )

    receipt = await ProjectSnapshotWorkspaceSourceAdapter(
        client, _source_root(tmp_path, files)
    ).admit(source)

    assert receipt.source == source
    assert receipt.facts["adapter"] == "project_source"
    assert client.checked == [(source.source_id, source.revision)]
    assert client.acquired == []


@pytest.mark.asyncio
async def test_project_source_admission_accepts_checked_public_head_prefix(
    tmp_path: Path,
) -> None:
    lease = _lease("local_clone", {"README.md": b"hello\n"})
    source = WorkspaceSource(
        kind="manifest",
        source_id=str(lease["project_id"]),
        revision=str(lease["head"]),
    )
    client = FakeProjectSource({**lease, "head": source.revision[:12]})

    receipt = await ProjectSnapshotWorkspaceSourceAdapter(
        client, _source_root(tmp_path, {"README.md": b"hello\n"})
    ).admit(source)

    assert receipt.source == source
    assert client.checked == [(source.source_id, source.revision)]


@pytest.mark.asyncio
async def test_project_source_admission_rejects_returned_head_mismatch(
    tmp_path: Path,
) -> None:
    lease = _lease("local_clone", {"README.md": b"hello\n"})
    source = WorkspaceSource(
        kind="manifest",
        source_id=str(lease["project_id"]),
        revision=str(lease["head"]),
    )
    client = FakeProjectSource({**lease, "head": "b" * 40})

    with pytest.raises(WorkspaceError) as caught:
        await ProjectSnapshotWorkspaceSourceAdapter(
            client, _source_root(tmp_path, {"README.md": b"hello\n"})
        ).admit(source)

    assert caught.value.code == "source_revision_changed"
    assert client.acquired == []


@pytest.mark.asyncio
async def test_host_snapshot_adapter_requests_imports_and_releases_exact_transfer(
    tmp_path: Path,
) -> None:
    files = {"README.md": b"hello\n", "src/app.py": b"print('ok')\n"}
    lease = _lease("host_git", files)
    project = {
        "project_id": lease["project_id"],
        "name": "中文 Host Project",
        "branch": "feature/v14",
        "head": lease["head"],
    }
    client = FakeProjectSource(lease)
    host = FakeProjectHost(project)
    source = WorkspaceSource(
        kind="host_snapshot",
        source_id=str(lease["project_id"]),
        revision=str(lease["head"]),
    )

    snapshot = await HostSnapshotWorkspaceSourceAdapter(
        host, client, _source_root(tmp_path, files)
    ).acquire(source)

    assert [(item.path, item.content) for item in snapshot.files] == sorted(files.items())
    assert host.requests == [(source.source_id, source.revision)]
    assert client.acquired == []
    assert client.imports == [
        {
            "upload_id": "1" * 32,
            "archive_sha256": "2" * 64,
            "project_id": source.source_id,
            "name": "中文 Host Project",
            "branch": "feature/v14",
            "head": source.revision,
        }
    ]
    assert client.released == [(source.source_id, "lease_1")]
    assert host.finished == ["1" * 32]


@pytest.mark.asyncio
async def test_host_source_admission_checks_catalog_without_snapshot_transfer(
    tmp_path: Path,
) -> None:
    files = {"README.md": b"hello\n"}
    lease = _lease("host_git", files)
    project = {
        "project_id": lease["project_id"],
        "name": "Host Project",
        "branch": "main",
        "head": lease["head"],
    }
    client = FakeProjectSource(lease)
    host = FakeProjectHost(project)
    source = WorkspaceSource(
        kind="host_snapshot",
        source_id=str(lease["project_id"]),
        revision=str(lease["head"]),
    )

    receipt = await HostSnapshotWorkspaceSourceAdapter(
        host, client, _source_root(tmp_path, files)
    ).admit(source)

    assert receipt.source == source
    assert receipt.facts["adapter"] == "project_host"
    assert host.checked == [(source.source_id, source.revision, None)]
    assert host.requests == []
    assert client.imports == []


@pytest.mark.asyncio
async def test_host_source_admission_accepts_checked_public_head_prefix(
    tmp_path: Path,
) -> None:
    lease = _lease("host_git", {"README.md": b"hello\n"})
    source = WorkspaceSource(
        kind="host_snapshot",
        source_id=str(lease["project_id"]),
        revision=str(lease["head"]),
    )
    host = FakeProjectHost(
        {
            "project_id": lease["project_id"],
            "name": "Host Project",
            "branch": "main",
            "head": source.revision[:12],
        }
    )

    receipt = await HostSnapshotWorkspaceSourceAdapter(
        host,
        FakeProjectSource(lease),
        _source_root(tmp_path, {"README.md": b"hello\n"}),
    ).admit(source)

    assert receipt.source == source
    assert host.checked == [(source.source_id, source.revision, None)]


@pytest.mark.asyncio
async def test_host_source_admission_rejects_returned_head_mismatch(
    tmp_path: Path,
) -> None:
    lease = _lease("host_git", {"README.md": b"hello\n"})
    source = WorkspaceSource(
        kind="host_snapshot",
        source_id=str(lease["project_id"]),
        revision=str(lease["head"]),
    )
    host = FakeProjectHost(
        {
            "project_id": lease["project_id"],
            "name": "Host Project",
            "branch": "main",
            "head": "b" * 40,
        }
    )

    with pytest.raises(WorkspaceError) as caught:
        await HostSnapshotWorkspaceSourceAdapter(
            host,
            FakeProjectSource(lease),
            _source_root(tmp_path, {"README.md": b"hello\n"}),
        ).admit(source)

    assert caught.value.code == "source_revision_changed"
    assert host.requests == []


def test_runtime_wires_host_snapshot_to_live_project_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = FakeProjectHost({})
    import server.coding_runtime.api as coding_api

    monkeypatch.setattr(
        coding_api,
        "get_coding_service",
        lambda: SimpleNamespace(project_host=host),
    )
    environment = {
        "CODING_WORKER_STATE_ROOT": str(tmp_path / "state"),
        "CODING_WORKER_SLOT_A_ROOT": str(tmp_path / "slot-a"),
        "CODING_WORKER_SLOT_B_ROOT": str(tmp_path / "slot-b"),
        "CODING_WORKER_SLOT_A_TOKEN": "a" * 32,
        "CODING_WORKER_SLOT_B_TOKEN": "b" * 32,
        "CODING_WORKER_EXECUTOR_A_TOKEN": "c" * 32,
        "CODING_WORKER_EXECUTOR_B_TOKEN": "d" * 32,
        "CODING_PROJECT_SOURCE_SOCKET_PATH": str(tmp_path / "source.sock"),
        "CODING_WORKER_PROJECT_SNAPSHOT_ROOT": str(tmp_path / "snapshots"),
        "CODING_WORKER_BROKER_SOCKET": str(tmp_path / "broker.sock"),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CODING_WORKER_BUILTIN_SOURCE_ROOT", raising=False)
    monkeypatch.delenv("CODING_WORKER_BUILTIN_REVISION", raising=False)

    runtime = build_runtime_from_environment()

    assert isinstance(
        runtime.workspace_broker._adapters["host_snapshot"],
        HostSnapshotWorkspaceSourceAdapter,
    )
    assert isinstance(
        runtime.workspace_broker._adapters["manifest"],
        ProjectSnapshotWorkspaceSourceAdapter,
    )
    assert set(runtime.tool_broker.frozen_checks) >= {
        "python-compile",
        "python-pytest",
        "react-test",
        "react-build",
    }


def test_runtime_parity_profile_registers_only_public_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = Path(__file__).parent / "fixtures" / "coding_worker_v17_parity_assets.json"
    environment = {
        "CODING_WORKER_STATE_ROOT": str(tmp_path / "state"),
        "CODING_WORKER_SLOT_A_ROOT": str(tmp_path / "slot-a"),
        "CODING_WORKER_SLOT_B_ROOT": str(tmp_path / "slot-b"),
        "CODING_WORKER_SLOT_A_TOKEN": "a" * 32,
        "CODING_WORKER_SLOT_B_TOKEN": "b" * 32,
        "CODING_WORKER_EXECUTOR_A_TOKEN": "c" * 32,
        "CODING_WORKER_EXECUTOR_B_TOKEN": "d" * 32,
        "CODING_WORKER_BROKER_SOCKET": str(tmp_path / "broker.sock"),
        "CODING_WORKER_PARITY_ENABLED": "true",
        "CODING_WORKER_PARITY_PUBLIC_FIXTURES": str(assets),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("CODING_WORKER_BUILTIN_SOURCE_ROOT", raising=False)
    monkeypatch.delenv("CODING_WORKER_BUILTIN_REVISION", raising=False)

    runtime = build_runtime_from_environment()

    assert isinstance(
        runtime.workspace_broker._adapters["builtin"],
        InMemoryWorkspaceSourceAdapter,
    )
    assert {"pytest", "npm_test"}.issubset(runtime.tool_broker.frozen_checks)


def test_runtime_harness_v3_profile_registers_only_compiled_h0(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = (
        Path(__file__).parents[2]
        / "benchmarks"
        / "coding-worker-v18"
        / "fixture-bundle.json"
    )
    environment = {
        "CODING_WORKER_STATE_ROOT": str(tmp_path / "state"),
        "CODING_WORKER_SLOT_A_ROOT": str(tmp_path / "slot-a"),
        "CODING_WORKER_SLOT_B_ROOT": str(tmp_path / "slot-b"),
        "CODING_WORKER_SLOT_A_TOKEN": "a" * 32,
        "CODING_WORKER_SLOT_B_TOKEN": "b" * 32,
        "CODING_WORKER_EXECUTOR_A_TOKEN": "c" * 32,
        "CODING_WORKER_EXECUTOR_B_TOKEN": "d" * 32,
        "CODING_WORKER_BROKER_SOCKET": str(tmp_path / "broker.sock"),
        "CODING_WORKER_HARNESS_V3_ENABLED": "true",
        "CODING_WORKER_HARNESS_V3_FIXTURES": str(assets),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    for key in (
        "CODING_WORKER_PARITY_ENABLED",
        "CODING_WORKER_PARITY_PUBLIC_FIXTURES",
        "CODING_WORKER_BUILTIN_SOURCE_ROOT",
        "CODING_WORKER_BUILTIN_REVISION",
    ):
        monkeypatch.delenv(key, raising=False)

    runtime = build_runtime_from_environment()

    adapter = runtime.workspace_broker._adapters["builtin"]
    assert isinstance(adapter, InMemoryWorkspaceSourceAdapter)
    assert len(adapter._snapshots) == 12
    assert len(runtime.tool_broker.frozen_checks) >= 12


def test_runtime_harness_v3_rejects_registered_builtin_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import server.coding_worker.runtime as runtime_module

    assets = (
        Path(__file__).parents[2]
        / "benchmarks"
        / "coding-worker-v18"
        / "fixture-bundle.json"
    )
    environment = {
        "CODING_WORKER_STATE_ROOT": str(tmp_path / "state"),
        "CODING_WORKER_SLOT_A_ROOT": str(tmp_path / "slot-a"),
        "CODING_WORKER_SLOT_B_ROOT": str(tmp_path / "slot-b"),
        "CODING_WORKER_SLOT_A_TOKEN": "a" * 32,
        "CODING_WORKER_SLOT_B_TOKEN": "b" * 32,
        "CODING_WORKER_EXECUTOR_A_TOKEN": "c" * 32,
        "CODING_WORKER_EXECUTOR_B_TOKEN": "d" * 32,
        "CODING_WORKER_BROKER_SOCKET": str(tmp_path / "broker.sock"),
        "CODING_WORKER_HARNESS_V3_ENABLED": "true",
        "CODING_WORKER_HARNESS_V3_FIXTURES": str(assets),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    for key in (
        "CODING_WORKER_PARITY_ENABLED",
        "CODING_WORKER_PARITY_PUBLIC_FIXTURES",
        "CODING_WORKER_BUILTIN_SOURCE_ROOT",
        "CODING_WORKER_BUILTIN_REVISION",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setitem(
        runtime_module._SOURCE_ADAPTERS,
        "builtin",
        InMemoryWorkspaceSourceAdapter({("other", "revision"): {"x.py": b""}}),
    )

    with pytest.raises(CodingWorkerRuntimeError, match="registered builtin"):
        build_runtime_from_environment()


def test_runtime_rejects_parity_profile_mixed_with_builtin_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assets = Path(__file__).parent / "fixtures" / "coding_worker_v17_parity_assets.json"
    values = {
        "CODING_WORKER_STATE_ROOT": str(tmp_path / "state"),
        "CODING_WORKER_SLOT_A_ROOT": str(tmp_path / "slot-a"),
        "CODING_WORKER_SLOT_B_ROOT": str(tmp_path / "slot-b"),
        "CODING_WORKER_SLOT_A_TOKEN": "a" * 32,
        "CODING_WORKER_SLOT_B_TOKEN": "b" * 32,
        "CODING_WORKER_EXECUTOR_A_TOKEN": "c" * 32,
        "CODING_WORKER_EXECUTOR_B_TOKEN": "d" * 32,
        "CODING_WORKER_PARITY_ENABLED": "true",
        "CODING_WORKER_PARITY_PUBLIC_FIXTURES": str(assets),
        "CODING_WORKER_BUILTIN_SOURCE_ROOT": str(tmp_path),
        "CODING_WORKER_BUILTIN_REVISION": "a" * 40,
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    with pytest.raises(CodingWorkerRuntimeError, match="cannot replace"):
        build_runtime_from_environment()


@pytest.mark.asyncio
async def test_project_snapshot_adapter_rejects_changed_fingerprint_and_releases(
    tmp_path: Path,
) -> None:
    files = {"README.md": b"changed\n"}
    lease = _lease("host_git", {"README.md": b"original\n"})
    client = FakeProjectSource(lease)
    source = WorkspaceSource(
        kind="host_snapshot",
        source_id=str(lease["project_id"]),
        revision=str(lease["head"]),
    )

    project = {
        "project_id": lease["project_id"],
        "name": "Host Project",
        "branch": "feature/v14",
        "head": lease["head"],
    }
    host = FakeProjectHost(project)
    with pytest.raises(WorkspaceError, match="does not match") as caught:
        await HostSnapshotWorkspaceSourceAdapter(
            host, client, _source_root(tmp_path, files)
        ).acquire(source)

    assert caught.value.code == "source_revision_changed"
    assert client.released == [(source.source_id, "lease_1")]
    assert host.finished == ["1" * 32]


@pytest.mark.asyncio
async def test_project_snapshot_adapter_fails_closed_when_release_is_uncertain(
    tmp_path: Path,
) -> None:
    files = {"README.md": b"hello\n"}
    lease = _lease("local_clone", files)
    client = FakeProjectSource(lease)
    client.release_result = False
    source = WorkspaceSource(
        kind="manifest",
        source_id=str(lease["project_id"]),
        revision=str(lease["head"]),
    )

    with pytest.raises(WorkspaceError, match="released") as caught:
        await ProjectSnapshotWorkspaceSourceAdapter(
            client, _source_root(tmp_path, files)
        ).acquire(source)

    assert caught.value.code == "source_release_failed"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


@pytest.mark.asyncio
async def test_builtin_adapter_reads_only_tracked_blobs_from_exact_revision(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "ModelMirror Test")
    _git(tmp_path, "config", "user.email", "test@modelmirror.local")
    (tmp_path / "app.py").write_text("print('tracked')\n", encoding="utf-8")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-m", "baseline")
    revision = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "secret.env").write_text("TOKEN=do-not-copy\n", encoding="utf-8")
    source = WorkspaceSource(
        kind="builtin", source_id="modelmirror", revision=revision
    )

    snapshot = await BuiltinGitWorkspaceSourceAdapter(
        tmp_path, source_id="modelmirror", revision=revision
    ).acquire(source)

    assert [(item.path, item.content) for item in snapshot.files] == [
        ("app.py", b"print('tracked')\n")
    ]


@pytest.mark.asyncio
async def test_builtin_adapter_rejects_unregistered_revision_before_reading(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    source = WorkspaceSource(
        kind="builtin", source_id="modelmirror", revision="b" * 40
    )
    adapter = BuiltinGitWorkspaceSourceAdapter(
        tmp_path, source_id="modelmirror", revision="a" * 40
    )

    with pytest.raises(WorkspaceError) as caught:
        await adapter.acquire(source)

    assert caught.value.code == "source_not_found"


@pytest.mark.asyncio
async def test_builtin_admission_accepts_trusted_file_above_external_limit(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "ModelMirror Test")
    _git(tmp_path, "config", "user.email", "test@modelmirror.local")
    content = b"x" * (MAX_EXTERNAL_SOURCE_FILE_BYTES + 1)
    (tmp_path / "trusted-index.json").write_bytes(content)
    _git(tmp_path, "add", "trusted-index.json")
    _git(tmp_path, "commit", "-m", "large trusted index")
    revision = _git(tmp_path, "rev-parse", "HEAD")
    source = WorkspaceSource(
        kind="builtin", source_id="modelmirror", revision=revision
    )
    adapter = BuiltinGitWorkspaceSourceAdapter(
        tmp_path, source_id="modelmirror", revision=revision
    )

    receipt = await adapter.admit(source)
    snapshot = await adapter.acquire(source)

    assert receipt.facts["limit_policy"] == "builtin-16m-v1"
    assert receipt.facts["total_bytes"] == len(content)
    assert snapshot.files[0].content == content


@pytest.mark.asyncio
async def test_builtin_admission_rejects_file_above_trusted_limit(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "ModelMirror Test")
    _git(tmp_path, "config", "user.email", "test@modelmirror.local")
    (tmp_path / "oversized.bin").write_bytes(b"x" * (MAX_SOURCE_FILE_BYTES + 1))
    _git(tmp_path, "add", "oversized.bin")
    _git(tmp_path, "commit", "-m", "oversized")
    revision = _git(tmp_path, "rev-parse", "HEAD")
    source = WorkspaceSource(
        kind="builtin", source_id="modelmirror", revision=revision
    )

    with pytest.raises(WorkspaceError) as caught:
        await BuiltinGitWorkspaceSourceAdapter(
            tmp_path, source_id="modelmirror", revision=revision
        ).admit(source)

    assert caught.value.code == "source_limit_exceeded"


@pytest.mark.asyncio
async def test_external_project_snapshot_keeps_eight_mib_file_limit(
    tmp_path: Path,
) -> None:
    files = {"large.bin": b"x" * (MAX_EXTERNAL_SOURCE_FILE_BYTES + 1)}
    lease = _lease("local_clone", files)
    client = FakeProjectSource(lease)
    source = WorkspaceSource(
        kind="manifest",
        source_id=str(lease["project_id"]),
        revision=str(lease["head"]),
    )

    with pytest.raises(WorkspaceError) as caught:
        await ProjectSnapshotWorkspaceSourceAdapter(
            client, _source_root(tmp_path, files)
        ).acquire(source)

    assert caught.value.code == "source_file_limit_exceeded"
    assert client.released == [(source.source_id, "lease_1")]


@pytest.mark.asyncio
async def test_host_snapshot_keeps_eight_mib_file_limit(tmp_path: Path) -> None:
    files = {"large.bin": b"x" * (MAX_EXTERNAL_SOURCE_FILE_BYTES + 1)}
    lease = _lease("host_git", files)
    project = {
        "project_id": lease["project_id"],
        "name": "Host Project",
        "branch": "main",
        "head": lease["head"],
    }
    client = FakeProjectSource(lease)
    host = FakeProjectHost(project)
    source = WorkspaceSource(
        kind="host_snapshot",
        source_id=str(lease["project_id"]),
        revision=str(lease["head"]),
    )

    with pytest.raises(WorkspaceError) as caught:
        await HostSnapshotWorkspaceSourceAdapter(
            host, client, _source_root(tmp_path, files)
        ).acquire(source)

    assert caught.value.code == "source_file_limit_exceeded"
    assert host.finished == ["1" * 32]
