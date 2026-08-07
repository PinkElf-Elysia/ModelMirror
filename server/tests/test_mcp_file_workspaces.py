from __future__ import annotations

import io
import stat
import time
import zipfile
from pathlib import Path
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent, Tool

from server.mcp.catalog import (
    CATALOG_ADAPTERS,
    CatalogAdapterPolicyError,
    CatalogApprovalRequiredError,
    CatalogConfigurationRequest,
    MCPCatalogService,
)
from server.mcp.workspace import (
    CatalogWorkspaceNotFoundError,
    CatalogWorkspacePolicyError,
    MCPCatalogWorkspaceStore,
)


def make_store(tmp_path: Path) -> MCPCatalogWorkspaceStore:
    return MCPCatalogWorkspaceStore(
        tmp_path / "metadata",
        input_root=tmp_path / "inputs",
        output_root=tmp_path / "outputs",
        memory_root=tmp_path / "memory",
    )


def test_workspace_rejects_traversal_symlink_zip_and_cross_project_access(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    workspace = store.create("git-mcp", display_name="repo")

    with pytest.raises(CatalogWorkspacePolicyError):
        store.add_upload(
            "git-mcp",
            workspace.workspace_id,
            filename="escape.txt",
            relative_path="../escape.txt",
            content=b"no",
        )

    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        info = zipfile.ZipInfo("linked")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "../target")
    with pytest.raises(CatalogWorkspacePolicyError):
        store.add_upload(
            "git-mcp",
            workspace.workspace_id,
            filename="repo.zip",
            relative_path="repo.zip",
            content=archive_bytes.getvalue(),
        )

    stored = store.add_upload(
        "git-mcp",
        workspace.workspace_id,
        filename="README.md",
        relative_path="repo/README.md",
        content=b"hello",
    )[0]
    assert stored.file_id.startswith("mcpf_")
    assert "repo" not in stored.file_id
    sealed = store.seal("git-mcp", workspace.workspace_id)
    assert sealed.status == "sealed"
    assert sealed.manifest_sha256

    with pytest.raises(CatalogWorkspacePolicyError):
        store.add_upload(
            "git-mcp",
            workspace.workspace_id,
            filename="later.txt",
            relative_path="later.txt",
            content=b"no",
        )
    with pytest.raises(CatalogWorkspaceNotFoundError):
        store.get("markitdown-mcp", workspace.workspace_id)


def test_zip_import_is_bounded_and_uses_normalized_file_ids(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    workspace = store.create("markitdown-mcp", display_name="docs")
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("docs/a.md", "A")
        archive.writestr("docs/b.txt", "B")
    files = store.add_upload(
        "markitdown-mcp",
        workspace.workspace_id,
        filename="docs.zip",
        relative_path="docs.zip",
        content=archive_bytes.getvalue(),
    )
    assert [item.relative_path for item in files] == ["docs/a.md", "docs/b.txt"]
    assert len({item.file_id for item in files}) == 2


def test_workspace_rejects_zip_bomb_special_nodes_and_name_collisions(tmp_path: Path) -> None:
    store = make_store(tmp_path)

    bomb_workspace = store.create("markitdown-mcp", display_name="bomb")
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("large.txt", b"0" * (256 * 1024))
    with pytest.raises(CatalogWorkspacePolicyError):
        store.add_upload(
            "markitdown-mcp",
            bomb_workspace.workspace_id,
            filename="bomb.zip",
            relative_path="bomb.zip",
            content=bomb.getvalue(),
        )

    device_workspace = store.create("git-mcp", display_name="device")
    device = io.BytesIO()
    with zipfile.ZipFile(device, "w") as archive:
        info = zipfile.ZipInfo("device")
        info.external_attr = (stat.S_IFCHR | 0o600) << 16
        archive.writestr(info, b"")
    with pytest.raises(CatalogWorkspacePolicyError):
        store.add_upload(
            "git-mcp",
            device_workspace.workspace_id,
            filename="device.zip",
            relative_path="device.zip",
            content=device.getvalue(),
        )

    names = store.create("markitdown-mcp", display_name="names")
    store.add_upload(
        "markitdown-mcp",
        names.workspace_id,
        filename="cafe.txt",
        relative_path="docs/cafe\u0301.txt",
        content=b"first",
    )
    with pytest.raises(CatalogWorkspacePolicyError):
        store.add_upload(
            "markitdown-mcp",
            names.workspace_id,
            filename="cafe.txt",
            relative_path="docs/caf\u00e9.txt",
            content=b"second",
        )
    store.add_upload(
        "markitdown-mcp",
        names.workspace_id,
        filename="A.txt",
        relative_path="Other/A.txt",
        content=b"first",
    )
    with pytest.raises(CatalogWorkspacePolicyError):
        store.add_upload(
            "markitdown-mcp",
            names.workspace_id,
            filename="a.TXT",
            relative_path="other/a.TXT",
            content=b"second",
        )


def test_sealed_workspace_detects_byte_file_set_and_tenant_drift(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    workspace = store.create("markitdown-mcp", display_name="sealed", tenant_id="tenant-a")
    store.add_upload(
        "markitdown-mcp",
        workspace.workspace_id,
        filename="source.txt",
        relative_path="source.txt",
        content=b"original",
        tenant_id="tenant-a",
    )
    store.seal("markitdown-mcp", workspace.workspace_id, tenant_id="tenant-a")
    with pytest.raises(CatalogWorkspaceNotFoundError):
        store.get("markitdown-mcp", workspace.workspace_id, tenant_id="tenant-b")

    source = store.input_root / workspace.workspace_id / "source.txt"
    source.write_bytes(b"changed!")
    with pytest.raises(CatalogWorkspacePolicyError):
        store.require_sealed(
            "markitdown-mcp",
            workspace.workspace_id,
            tenant_id="tenant-a",
        )
    source.write_bytes(b"original")
    (source.parent / "untracked.txt").write_bytes(b"unexpected")
    with pytest.raises(CatalogWorkspacePolicyError):
        store.require_sealed(
            "markitdown-mcp",
            workspace.workspace_id,
            tenant_id="tenant-a",
        )


def test_artifacts_are_scoped_and_expire(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    workspace = store.create("markitdown-mcp", display_name="artifact")
    store.add_upload(
        "markitdown-mcp",
        workspace.workspace_id,
        filename="source.txt",
        relative_path="source.txt",
        content=b"source",
    )
    store.seal("markitdown-mcp", workspace.workspace_id)
    output = store.output_root / workspace.workspace_id / "converted.md"
    output.write_text("converted", encoding="utf-8")
    [artifact] = store.discover_artifacts("markitdown-mcp", workspace.workspace_id)
    resolved_artifact, resolved_path = store.artifact_path(
        "markitdown-mcp",
        workspace.workspace_id,
        artifact.artifact_id,
    )
    assert resolved_artifact.sha256 == artifact.sha256
    assert resolved_path == output
    with pytest.raises(CatalogWorkspaceNotFoundError):
        store.artifact_path(
            "git-mcp",
            workspace.workspace_id,
            artifact.artifact_id,
        )
    artifact.expires_at = time.time() - 1
    with pytest.raises(CatalogWorkspaceNotFoundError):
        store.artifact_path(
            "markitdown-mcp",
            workspace.workspace_id,
            artifact.artifact_id,
        )
    assert not output.exists()


def test_ephemeral_cleanup_removes_expired_workspace_but_keeps_memory(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    ephemeral = store.create("markitdown-mcp", display_name="temporary")
    persistent = store.create("basic-memory-mcp", display_name="memory")
    ephemeral.expires_at = time.time() - 1

    removed = store.cleanup_expired()

    assert removed == [ephemeral.workspace_id]
    assert not (store.input_root / ephemeral.workspace_id).exists()
    assert store.get("basic-memory-mcp", persistent.workspace_id).persistent is True


class FakeManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.sessions: set[str] = set()
        self.session_owner = ""

    async def connect_profile(self, **kwargs: Any) -> str:
        self.sessions.add("file-session")
        self.session_owner = str(kwargs.get("session_owner") or "")
        return "file-session"

    async def list_tools(
        self,
        session_id: str,
        *,
        session_owner: str = "",
    ) -> list[Tool]:
        assert session_id in self.sessions
        assert session_owner == self.session_owner
        return [Tool(name="write_note", description="write", inputSchema={})]

    async def call_tool(
        self,
        session_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        session_owner: str = "",
    ) -> CallToolResult:
        assert session_id in self.sessions
        assert session_owner == self.session_owner
        self.calls.append((session_id, tool_name, arguments))
        return CallToolResult(content=[TextContent(type="text", text="ok")])

    async def disconnect(
        self,
        session_id: str,
        *,
        session_owner: str = "",
    ) -> None:
        assert session_owner == self.session_owner
        self.sessions.remove(session_id)


class FakeInstaller:
    def get_installed(self, project_id: str):
        return None

    def install(self, **_: Any):
        raise AssertionError("bundled adapters must not install at runtime")


class FakeRegistry:
    async def register_session_tools(self, **_: Any) -> None:
        return None

    async def unregister_session(self, _: str) -> None:
        return None


@pytest.mark.asyncio
async def test_state_write_requires_bound_one_time_approval(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    workspace = store.create("basic-memory-mcp", display_name="memory")
    store.seal("basic-memory-mcp", workspace.workspace_id)
    manager = FakeManager()
    service = MCPCatalogService(  # type: ignore[arg-type]
        manager,
        FakeInstaller(),
        FakeRegistry(),
        manifests={"basic-memory-mcp": CATALOG_ADAPTERS["basic-memory-mcp"]},
        workspace_store=store,
    )
    service.configure(
        "basic-memory-mcp",
        CatalogConfigurationRequest(workspace_id=workspace.workspace_id),
    )
    public = service.list_adapters()["adapters"][0]
    assert public["workspace_id"] == workspace.workspace_id
    await service.connect("basic-memory-mcp")

    with pytest.raises(CatalogApprovalRequiredError) as captured:
        await service.call_tool(
            "basic-memory-mcp",
            "write_note",
            {"title": "Decision", "content": "Keep inputs read-only."},
        )
    payload = captured.value.payload
    assert payload["code"] == "approval_required"
    assert "Keep inputs" not in payload["summary"]
    assert len(payload["argument_digest"]) == 64
    assert manager.calls == []

    result = await service.confirm_approval(
        "basic-memory-mcp",
        payload["approval_id"],
    )
    assert result["is_error"] is False
    assert manager.calls == [
        (
            "file-session",
            "write_note",
            {"content": "Keep inputs read-only.", "title": "Decision"},
        )
    ]
    with pytest.raises(CatalogAdapterPolicyError):
        await service.confirm_approval(
            "basic-memory-mcp",
            payload["approval_id"],
        )


@pytest.mark.asyncio
async def test_approval_expires_on_reconfigure_session_and_workspace_drift(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    workspace = store.create("basic-memory-mcp", display_name="memory")
    store.seal("basic-memory-mcp", workspace.workspace_id)
    manager = FakeManager()
    service = MCPCatalogService(  # type: ignore[arg-type]
        manager,
        FakeInstaller(),
        FakeRegistry(),
        manifests={"basic-memory-mcp": CATALOG_ADAPTERS["basic-memory-mcp"]},
        workspace_store=store,
    )
    configuration = CatalogConfigurationRequest(workspace_id=workspace.workspace_id)
    service.configure("basic-memory-mcp", configuration)
    await service.connect("basic-memory-mcp")

    async def request_approval() -> str:
        with pytest.raises(CatalogApprovalRequiredError) as captured:
            await service.call_tool(
                "basic-memory-mcp",
                "write_note",
                {"title": "Decision", "content": "frozen"},
            )
        return str(captured.value.payload["approval_id"])

    expired = await request_approval()
    service._approvals[service._approval_key(expired)].expires_at = time.time() - 1
    with pytest.raises(CatalogAdapterPolicyError):
        await service.confirm_approval("basic-memory-mcp", expired)

    reconfigured = await request_approval()
    service.configure("basic-memory-mcp", configuration)
    with pytest.raises(CatalogAdapterPolicyError):
        await service.confirm_approval("basic-memory-mcp", reconfigured)

    session_changed = await request_approval()
    service._sessions[service._scope_key("basic-memory-mcp")] = "replacement-session"
    with pytest.raises(CatalogAdapterPolicyError):
        await service.confirm_approval("basic-memory-mcp", session_changed)
    service._sessions[service._scope_key("basic-memory-mcp")] = "file-session"

    workspace_drift = await request_approval()
    unexpected = store.input_root / workspace.workspace_id / "unexpected.txt"
    unexpected.write_text("drift", encoding="utf-8")
    with pytest.raises(CatalogWorkspacePolicyError):
        await service.confirm_approval("basic-memory-mcp", workspace_drift)
    assert manager.calls == []
