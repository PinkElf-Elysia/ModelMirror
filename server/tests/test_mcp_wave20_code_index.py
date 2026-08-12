from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from server.mcp import file_proxy
from server.mcp.catalog import CATALOG_ADAPTERS
from server.mcp.catalog_expansion_v2 import CATALOG_EXPANSION_V2_ADAPTERS
from server.mcp.workspace import FILE_PROJECTS, PROJECT_EXTENSIONS
from server.sandbox_sidecar import file_code_index, smoke_file_code_index
from server.sandbox_sidecar.file_code_index import (
    GOGRAPH_ADAPTER_ID,
    GOGRAPH_AMD64_SHA256,
    GOGRAPH_ARM64_SHA256,
    GOGRAPH_COMMIT,
    GOGRAPH_GO_IMAGE_DIGEST,
    GOGRAPH_GO_VERSION,
    GOGRAPH_UPSTREAM_SCHEMA_SHA256,
    GOGRAPH_VERSION,
    GoGraphRuntime,
    WAVE20_BUILDERS,
    WAVE20_SCHEMA_SHA256,
    WAVE20_TOOL_NAMES,
)
from server.sandbox_sidecar.file_server import (
    DEFAULT_ALLOWED_ADAPTERS,
    STAGED_FILE_ADAPTERS,
    _allowed_adapters,
)


ROOT = Path(__file__).resolve().parents[2]
SUPERSEDED_IDS = {
    "deusdata-codebase-memory-mcp",
    "shashankss1205-codegraphcontext",
}
EXPECTED_TOOLS = {
    "index_repository",
    "search_symbols",
    "get_symbol_context",
    "get_source",
    "get_callers",
    "get_repository_summary",
}


def _context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    input_root = tmp_path / "input"
    input_root.mkdir()
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    monkeypatch.setenv("TMPDIR", str(temp_root))
    return SimpleNamespace(input_root=input_root)


def _digest(tools: list[Any]) -> str:
    reviewed = [
        {"name": tool.name, "inputSchema": tool.inputSchema}
        for tool in sorted(tools, key=lambda item: item.name)
    ]
    return hashlib.sha256(
        json.dumps(
            reviewed,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


@pytest.mark.asyncio
async def test_wave20_tool_contract_is_exact_and_closes_open_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = await WAVE20_BUILDERS[GOGRAPH_ADAPTER_ID](
        _context(tmp_path, monkeypatch)
    ).list_tools()
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert set(WAVE20_TOOL_NAMES[GOGRAPH_ADAPTER_ID]) == EXPECTED_TOOLS
    assert _digest(tools) == WAVE20_SCHEMA_SHA256[GOGRAPH_ADAPTER_ID]
    forbidden = {
        "path",
        "repo_path",
        "root",
        "project",
        "persist_refresh",
        "git_ref",
        "since",
        "config",
        "uncommitted",
        "mermaid",
        "no_tests",
        "terms",
        "url",
        "headers",
        "environment",
        "command",
        "cwd",
        "output",
        "content",
    }
    for tool in tools:
        assert tool.inputSchema["additionalProperties"] is False
        assert forbidden.isdisjoint(tool.inputSchema.get("properties", {}))
        assert tool.annotations is not None
        if tool.name == "index_repository":
            assert tool.annotations.readOnlyHint is False
            assert tool.annotations.idempotentHint is False
        else:
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.idempotentHint is True


def test_wave20_is_ready_default_allowed_and_workspace_formats_are_go_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert GOGRAPH_ADAPTER_ID not in STAGED_FILE_ADAPTERS
    assert GOGRAPH_ADAPTER_ID in DEFAULT_ALLOWED_ADAPTERS
    assert GOGRAPH_ADAPTER_ID in file_proxy.ALLOWED_ADAPTERS
    assert GOGRAPH_ADAPTER_ID in FILE_PROJECTS
    assert PROJECT_EXTENSIONS[GOGRAPH_ADAPTER_ID] == {".go", ".mod", ".sum", ".work"}
    monkeypatch.delenv("MCP_FILE_ALLOWED_ADAPTERS", raising=False)
    assert GOGRAPH_ADAPTER_ID in _allowed_adapters()
    monkeypatch.setenv("MCP_FILE_ALLOWED_ADAPTERS", GOGRAPH_ADAPTER_ID)
    assert _allowed_adapters() == frozenset({GOGRAPH_ADAPTER_ID})
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    allowlist = next(
        line for line in compose.splitlines() if "MCP_FILE_ALLOWED_ADAPTERS:" in line
    )
    assert GOGRAPH_ADAPTER_ID in allowlist


@pytest.mark.asyncio
async def test_wave20_runtime_freezes_index_and_query_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = GoGraphRuntime(_context(tmp_path, monkeypatch))
    calls: list[tuple[str, dict[str, Any], int]] = []

    async def fake_start() -> None:
        return None

    async def fake_call(
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout: int = file_code_index.QUERY_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        calls.append((tool_name, arguments, timeout))
        if tool_name == "gograph_stats":
            return {"complete": True, "files": 2}
        return {"results": [{"name": "Wave20Handler"}]}

    monkeypatch.setattr(runtime, "_start", fake_start)
    monkeypatch.setattr(runtime, "_call_tool", fake_call)
    with pytest.raises(ValueError, match="code_index_not_prepared"):
        await runtime.query("gograph_query", {"term": "Wave20"})
    indexed = await runtime.index_repository()
    assert indexed["persistence"] == "session-memory-only"
    assert indexed["language"] == "go"
    assert calls == [
        ("gograph_stats", {}, file_code_index.QUERY_TIMEOUT_SECONDS)
    ]
    await runtime.query("gograph_query", {"term": "Wave20Handler"})
    assert calls[-1] == (
        "gograph_query",
        {"term": "Wave20Handler"},
        file_code_index.QUERY_TIMEOUT_SECONDS,
    )
    with pytest.raises(ValueError, match="code_index_already_prepared"):
        await runtime.index_repository()
    with pytest.raises(ValueError, match="code_index_tool_denied"):
        await runtime.query("gograph_doc", {"query": "fmt.Println"})


def test_wave20_output_sanitizer_drops_roots_and_confines_absolute_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = GoGraphRuntime(_context(tmp_path, monkeypatch))
    source = runtime.input_root / "internal" / "handler.go"
    source.parent.mkdir()
    source.write_text("package internal\n", encoding="utf-8")
    assert runtime._sanitize(
        {
            "root": str(runtime.input_root),
            "results": [{"file": str(source), "name": "Wave20Handler"}],
        }
    ) == {
        "results": [{"file": "internal/handler.go", "name": "Wave20Handler"}]
    }
    with pytest.raises(ValueError, match="code_index_path_disclosure"):
        runtime._sanitize({"file": "/etc/passwd"})
    with pytest.raises(ValueError, match="code_index_path_disclosure"):
        runtime._sanitize({"path": "C:\\host\\secret.go"})
    with pytest.raises(ValueError, match="query_path_denied"):
        file_code_index._safe_text("../secret", field="query")


@pytest.mark.asyncio
async def test_wave20_timeout_terminates_the_upstream_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = GoGraphRuntime(_context(tmp_path, monkeypatch))
    terminated: list[bool] = []

    class SlowStdout:
        async def readline(self) -> bytes:
            await asyncio.sleep(10)
            return b""

    class FakeStdin:
        def write(self, _raw: bytes) -> None:
            return None

        async def drain(self) -> None:
            return None

    runtime._process = SimpleNamespace(
        stdout=SlowStdout(), stdin=FakeStdin(), returncode=None
    )

    async def fake_terminate() -> None:
        terminated.append(True)

    monkeypatch.setattr(runtime, "_terminate", fake_terminate)
    with pytest.raises(ValueError, match="code_index_timeout"):
        await runtime._request("tools/list", {}, timeout=0.001)
    assert terminated == [True]


def test_wave20_upstream_identity_and_docker_contract_are_frozen() -> None:
    assert GOGRAPH_VERSION == "1.5.6"
    assert GOGRAPH_COMMIT == "aa4d6d549e64f35c492664263630ba1350c66920"
    assert GOGRAPH_AMD64_SHA256 == (
        "1ef375a88cc8825ca7879b1170720352702e59723d1e3b06d33101a50a6f7030"
    )
    assert GOGRAPH_ARM64_SHA256 == (
        "c8b6d8a42326264858f14c7819200f47d00d0fcd58520b6c6d1e1b16b022a6b5"
    )
    assert GOGRAPH_GO_VERSION == "1.26.5"
    assert GOGRAPH_GO_IMAGE_DIGEST == (
        "sha256:53eeac89074db483fdf0ab3be1df32bf6e47562263d2d0d6baa7f26acb4957dd"
    )
    assert GOGRAPH_UPSTREAM_SCHEMA_SHA256 == (
        "a2c8f2fcf028067f2e080d018e482a52bd7ba8c3546ac92ba254b6b8b3fca25f"
    )
    dockerfile = (ROOT / "server" / "sandbox_sidecar" / "Dockerfile.files").read_text(
        encoding="utf-8"
    )
    assert "gograph_Linux_${GOGRAPH_ARCH}.tar.gz" in dockerfile
    assert GOGRAPH_AMD64_SHA256 in dockerfile
    assert GOGRAPH_ARM64_SHA256 in dockerfile
    assert GOGRAPH_GO_IMAGE_DIGEST in dockerfile
    assert 'test "$(gograph version)" = "gograph version v1.5.6"' in dockerfile
    assert 'test "$(go env GOVERSION)" = "go1.26.5"' in dockerfile
    assert "smoke_file_code_index --contract-only" in dockerfile
    assert "codebase-memory-mcp" not in dockerfile
    landlock_launcher = (
        ROOT / "server" / "sandbox_sidecar" / "file_landlock_exec.py"
    ).read_text(encoding="utf-8")
    assert "deusdata-codebase-memory-mcp" not in landlock_launcher


def test_wave20_smoke_tree_digest_accepts_directories_and_rejects_symlinks(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "main.go"
    source.parent.mkdir()
    source.write_text("package main\n", encoding="utf-8")
    assert len(smoke_file_code_index._tree_digest(tmp_path)) == 64
    link = tmp_path / "linked.go"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(RuntimeError, match="wave20_fixture_shape_invalid"):
        smoke_file_code_index._tree_digest(tmp_path)


def test_wave20_catalog_state_selects_gograph_and_supersedes_two() -> None:
    catalog = {item.project_id: item for item in CATALOG_EXPANSION_V2_ADAPTERS}
    selected = catalog[GOGRAPH_ADAPTER_ID]
    assert selected.availability == "ready"
    assert selected.adapter_version == (
        "1.5.6-reviewed-commit-aa4d6d54-compatible-native-v1"
    )
    assert selected.decision_reason_code == "ready-isolated-code-index-facade"
    manifest = CATALOG_ADAPTERS[GOGRAPH_ADAPTER_ID]
    assert manifest.availability == "ready"
    assert manifest.enabled_by_default is True
    assert manifest.runtime_image == "modelmirror-mcp-files:wave3-v1"
    assert manifest.filesystem_policy == "sealed-input-read-only,ephemeral-index"
    assert set(manifest.tool_policies) == EXPECTED_TOOLS
    assert manifest.tool_policies["index_repository"].requires_approval is True
    assert manifest.tool_policies["index_repository"].effect == "state-write"
    assert all(
        manifest.tool_policies[name].read_only
        for name in EXPECTED_TOOLS - {"index_repository"}
    )
    for adapter_id in SUPERSEDED_IDS:
        item = catalog[adapter_id]
        assert item.availability == "blocked"
        assert item.decision_reason_code == (
            "blocked-superseded-code-index-implementation"
        )
