from __future__ import annotations

import os
from pathlib import Path

import pytest

from server.coding_worker.contracts import WorkspaceSource
from server.coding_worker.workspace import (
    InMemoryWorkspaceSourceAdapter,
    SourceFile,
    SourceSnapshot,
    WorkspaceBroker,
    WorkspaceError,
    WorkspaceSourceAdapter,
)


def _source(kind: str = "manifest") -> WorkspaceSource:
    return WorkspaceSource(kind=kind, source_id="source-01", revision="revision-01")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_workspace_builds_remote_free_h0_and_preserves_binary_files(tmp_path: Path) -> None:
    source = _source()
    broker = WorkspaceBroker(
        tmp_path / "worker",
        {
            "manifest": InMemoryWorkspaceSourceAdapter(
                {
                    (source.source_id, source.revision): {
                        "src/main.py": b"print('ok')\n",
                        "assets/pixel.bin": b"\x00\xff\x10",
                    }
                }
            )
        },
        id_key=b"w" * 32,
    )
    record = await broker.prepare(source)
    assert record.file_count == 2
    assert len(record.baseline_commit) == 40
    assert record.baseline_tree_hash == broker.current_tree_hash(record.workspace_id)
    repository = broker.repository_path(record.workspace_id)
    assert not (repository / ".git" / "config").read_text().count("remote ")
    assert (repository / "assets" / "pixel.bin").read_bytes() == b"\x00\xff\x10"

    files = [entry for entry in broker.tree(record.workspace_id) if entry.kind == "file"]
    main = next(entry for entry in files if entry.name == "main.py")
    assert broker.read_entry(record.workspace_id, main.entry_id) == b"print('ok')\n"
    assert str(repository) not in main.entry_id


@pytest.mark.asyncio
async def test_all_three_opaque_source_kinds_use_the_same_isolation_contract(tmp_path: Path) -> None:
    adapters: dict[str, WorkspaceSourceAdapter] = {}
    for kind in ("builtin", "manifest", "host_snapshot"):
        source = _source(kind)
        adapters[kind] = InMemoryWorkspaceSourceAdapter(
            {(source.source_id, source.revision): {f"{kind}.txt": kind.encode()}}
        )
    broker = WorkspaceBroker(tmp_path / "worker", adapters, id_key=b"i" * 32)
    records = [await broker.prepare(_source(kind)) for kind in adapters]
    assert len({record.workspace_id for record in records}) == 3
    assert all(not (broker.repository_path(record.workspace_id) / ".git" / "refs" / "remotes").exists() for record in records)


class UnsafeAdapter:
    def __init__(self, source: WorkspaceSource, files: tuple[SourceFile, ...]) -> None:
        self.source = source
        self.files = files

    async def acquire(self, source: WorkspaceSource) -> SourceSnapshot:
        return SourceSnapshot(source=self.source, files=self.files)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ("../secret", "/absolute", ".git/config", "a\\b"))
async def test_workspace_rejects_traversal_and_git_metadata(tmp_path: Path, path: str) -> None:
    source = _source()
    broker = WorkspaceBroker(
        tmp_path / "worker",
        {"manifest": UnsafeAdapter(source, (SourceFile(path=path, content=b"x"),))},
        id_key=b"p" * 32,
    )
    with pytest.raises(WorkspaceError) as raised:
        await broker.prepare(source)
    assert raised.value.code == "source_path_invalid"
    assert not any((tmp_path / "worker" / "workspaces").iterdir())


@pytest.mark.asyncio
async def test_workspace_tree_detects_links_and_diff_uses_private_index(tmp_path: Path) -> None:
    source = _source()
    broker = WorkspaceBroker(
        tmp_path / "worker",
        {
            "manifest": InMemoryWorkspaceSourceAdapter(
                {(source.source_id, source.revision): {"tracked.txt": b"old\n"}}
            )
        },
        id_key=b"d" * 32,
    )
    record = await broker.prepare(source)
    repository = broker.repository_path(record.workspace_id)
    original_index = (repository / ".git" / "index").read_bytes()
    (repository / "tracked.txt").write_bytes(b"new\n")
    (repository / "new.bin").write_bytes(b"\x00\x01")
    diff = broker.diff(record.workspace_id)
    assert b"tracked.txt" in diff and b"new.bin" in diff
    assert (repository / ".git" / "index").read_bytes() == original_index

    if os.name != "nt":
        (repository / "unsafe-link").symlink_to(repository / "tracked.txt")
        with pytest.raises(WorkspaceError, match="link"):
            broker.tree(record.workspace_id)


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.name == "nt" or not hasattr(os, "geteuid") or os.geteuid() != 0,
    reason="requires Linux root to reproduce the sidecar ownership boundary",
)
async def test_diff_accepts_worker_owned_synthetic_repository(tmp_path: Path) -> None:
    source = _source()
    broker = WorkspaceBroker(
        tmp_path / "control",
        {
            "manifest": InMemoryWorkspaceSourceAdapter(
                {(source.source_id, source.revision): {"tracked.txt": b"old\n"}}
            )
        },
        id_key=b"o" * 32,
        slot_roots={"slot-a": tmp_path / "slot-a"},
        slot_owner=(65532, 65532),
    )
    record = await broker.prepare(source, slot_id="slot-a")
    (broker.repository_path(record.workspace_id) / "tracked.txt").write_bytes(b"new\n")

    assert b"tracked.txt" in broker.diff(record.workspace_id)


@pytest.mark.asyncio
async def test_dedicated_slots_keep_workspace_files_in_separate_roots(
    tmp_path: Path,
) -> None:
    source = _source()
    broker = WorkspaceBroker(
        tmp_path / "control",
        {
            "manifest": InMemoryWorkspaceSourceAdapter(
                {(source.source_id, source.revision): {"main.py": b"print('ok')\n"}}
            )
        },
        id_key=b"s" * 32,
        slot_roots={
            "slot-a": tmp_path / "slot-a",
            "slot-b": tmp_path / "slot-b",
        },
    )

    first = await broker.prepare(source, slot_id="slot-a")
    second = await broker.prepare(source, slot_id="slot-b")

    assert first.slot_id == "slot-a"
    assert second.slot_id == "slot-b"
    assert broker.workspace_slot(first.workspace_id) == "slot-a"
    assert broker.workspace_slot(second.workspace_id) == "slot-b"
    assert broker.repository_path(first.workspace_id).is_relative_to(tmp_path / "slot-a")
    assert broker.repository_path(second.workspace_id).is_relative_to(tmp_path / "slot-b")
    with pytest.raises(WorkspaceError) as raised:
        await broker.prepare(source)
    assert raised.value.code == "workspace_slot_unavailable"
