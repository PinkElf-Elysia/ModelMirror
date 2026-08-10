from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from server.coding_worker.contracts import WorkspaceSource
from server.coding_worker.source_adapters import (
    ProjectSnapshotWorkspaceSourceAdapter,
)
from server.coding_worker.workspace import WorkspaceError


class FakeProjectSource:
    def __init__(self, lease: dict[str, object]) -> None:
        self.lease = lease
        self.acquired: list[tuple[str, str | None]] = []
        self.released: list[tuple[str, str]] = []
        self.release_result = True

    async def acquire(
        self, project_id: str, *, expected_head: str | None = None
    ) -> dict[str, object]:
        self.acquired.append((project_id, expected_head))
        return dict(self.lease)

    async def release(self, project_id: str, lease_id: str) -> bool:
        self.released.append((project_id, lease_id))
        return self.release_result


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
@pytest.mark.parametrize(
    ("source_kind", "lease_kind"),
    (("manifest", "local_clone"), ("host_snapshot", "host_git")),
)
async def test_project_snapshot_adapter_copies_exact_lease_and_releases(
    tmp_path: Path, source_kind: str, lease_kind: str
) -> None:
    files = {"README.md": b"hello\n", "src/app.py": b"print('ok')\n"}
    lease = _lease(lease_kind, files)
    client = FakeProjectSource(lease)
    source = WorkspaceSource(
        kind=source_kind,
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

    with pytest.raises(WorkspaceError, match="does not match") as caught:
        await ProjectSnapshotWorkspaceSourceAdapter(
            client, _source_root(tmp_path, files)
        ).acquire(source)

    assert caught.value.code == "source_revision_changed"
    assert client.released == [(source.source_id, "lease_1")]


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
