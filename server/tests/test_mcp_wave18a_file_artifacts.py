from __future__ import annotations

import hashlib
import json
import os
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

from server.mcp.workspace import FILE_PROJECTS, PROJECT_EXTENSIONS
from server.sandbox_sidecar import file_artifacts
from server.sandbox_sidecar import smoke_file_artifacts
from server.sandbox_sidecar.file_artifacts import (
    WAVE18A_BUILDERS,
    WAVE18A_SCHEMA_SHA256,
    WAVE18A_TOOL_NAMES,
)
from server.sandbox_sidecar.file_mcp import WorkspaceContext, opaque_file_id
from server.sandbox_sidecar.file_server import (
    DEFAULT_ALLOWED_ADAPTERS,
    STAGED_FILE_ADAPTERS,
    _allowed_adapters,
)


WAVE18A_IDS = {
    "zcaceres-markdownify-mcp",
    "vivekvells-mcp-pandoc",
    "antvis-mcp-server-chart",
}
WAVE20_READY_IDS = {"ozgurcd-gograph"}


def test_runtime_smoke_limits_numeric_library_threads(tmp_path: Path) -> None:
    env = smoke_file_artifacts._base_env(tmp_path, "mcpws_" + "b" * 32)
    assert {
        key: env[key]
        for key in (
            "OPENBLAS_NUM_THREADS",
            "OMP_NUM_THREADS",
            "MKL_NUM_THREADS",
            "NUMEXPR_NUM_THREADS",
        )
    } == {
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    }


def _workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    filename: str | None = None,
    content: bytes = b"",
) -> tuple[WorkspaceContext, str | None]:
    workspace_id = "mcpws_" + "a" * 32
    input_base = tmp_path / "inputs"
    output_base = tmp_path / "outputs"
    memory_base = tmp_path / "memory"
    input_root = input_base / workspace_id
    input_root.mkdir(parents=True)
    file_id = None
    if filename is not None:
        source = input_root / filename
        source.write_bytes(content)
        file_id = opaque_file_id(workspace_id, filename)
    monkeypatch.setenv("MCP_FILE_WORKSPACE_ID", workspace_id)
    monkeypatch.setenv("MCP_FILE_INPUT_ROOT", str(input_base))
    monkeypatch.setenv("MCP_FILE_OUTPUT_ROOT", str(output_base))
    monkeypatch.setenv("MCP_FILE_MEMORY_ROOT", str(memory_base))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir(exist_ok=True)
    return WorkspaceContext(), file_id


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
async def test_wave18a_tool_names_schema_digests_and_markers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, _ = _workspace(tmp_path, monkeypatch)
    for adapter_id, builder in WAVE18A_BUILDERS.items():
        tools = await builder(context).list_tools()
        assert {tool.name for tool in tools} == set(WAVE18A_TOOL_NAMES[adapter_id])
        assert _digest(tools) == WAVE18A_SCHEMA_SHA256[adapter_id]
        serialized = json.dumps(
            [tool.model_dump(mode="json", exclude_none=True) for tool in tools],
            ensure_ascii=False,
        )
        assert "TO_BE_FILLED" not in serialized
        assert "http://" not in serialized
        assert "https://" not in serialized
        assert "filepath" not in serialized
        for tool in tools:
            assert tool.inputSchema["additionalProperties"] is False
            properties = tool.inputSchema.get("properties", {})
            if "file_id" in properties:
                assert properties["file_id"]["x-modelmirror-input"] == "workspace-file"
            assert properties["artifact_name"]["x-modelmirror-input"] == "artifact-name"


def test_wave18a_is_default_allowed_and_file_workspaces_are_narrow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert STAGED_FILE_ADAPTERS == frozenset()
    assert WAVE18A_IDS.issubset(DEFAULT_ALLOWED_ADAPTERS)
    assert WAVE20_READY_IDS.issubset(DEFAULT_ALLOWED_ADAPTERS)
    assert WAVE18A_IDS.issubset(FILE_PROJECTS)
    assert PROJECT_EXTENSIONS["zcaceres-markdownify-mcp"] == {
        ".pdf", ".docx", ".xlsx", ".pptx"
    }
    assert PROJECT_EXTENSIONS["vivekvells-mcp-pandoc"] == {
        ".md", ".markdown", ".html", ".htm", ".txt"
    }
    assert PROJECT_EXTENSIONS["antvis-mcp-server-chart"] == set()
    monkeypatch.setenv(
        "MCP_FILE_ALLOWED_ADAPTERS", ",".join(sorted(WAVE18A_IDS))
    )
    assert _allowed_adapters() == WAVE18A_IDS
    monkeypatch.setenv("MCP_FILE_ALLOWED_ADAPTERS", "unknown-adapter")
    with pytest.raises(RuntimeError, match="unknown adapter"):
        _allowed_adapters()


@pytest.mark.asyncio
async def test_markdownify_docx_generates_registered_deterministic_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Document = pytest.importorskip("docx").Document
    pytest.importorskip("markitdown")

    source = tmp_path / "source.docx"
    document = Document()
    document.add_heading("Wave 18A", level=1)
    document.add_paragraph("deterministic local markdown")
    document.save(source)
    context, file_id = _workspace(
        tmp_path / "workspace",
        monkeypatch,
        filename="source.docx",
        content=source.read_bytes(),
    )
    assert file_id is not None
    mcp = WAVE18A_BUILDERS["zcaceres-markdownify-mcp"](context)
    first = await mcp.call_tool(
        "docx-to-markdown",
        {"file_id": file_id, "artifact_name": "first.md"},
    )
    second = await mcp.call_tool(
        "docx-to-markdown",
        {"file_id": file_id, "artifact_name": "second.md"},
    )
    assert first and second
    first_bytes = (context.output_root / "first.md").read_bytes()
    second_bytes = (context.output_root / "second.md").read_bytes()
    assert first_bytes == second_bytes
    assert b"Wave 18A" in first_bytes


@pytest.mark.asyncio
async def test_pandoc_uses_only_fixed_sandbox_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, file_id = _workspace(
        tmp_path, monkeypatch, filename="source.md", content=b"# Safe\n"
    )
    assert file_id is not None
    observed: list[str] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        observed.extend(command)
        output = next(item.split("=", 1)[1] for item in command if item.startswith("--output="))
        Path(output).write_text("<!doctype html><title>Safe</title>\n", encoding="utf-8")
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert kwargs["timeout"] == 30
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(file_artifacts.subprocess, "run", fake_run)
    result = await WAVE18A_BUILDERS["vivekvells-mcp-pandoc"](context).call_tool(
        "convert-contents",
        {
            "file_id": file_id,
            "input_format": "markdown",
            "output_format": "html",
            "artifact_name": "safe.html",
        },
    )
    assert result
    assert observed[0] == "/usr/local/bin/pandoc"
    assert "--sandbox" in observed
    assert "--from=markdown-raw_html-raw_attribute" in observed
    assert "--to=html5" in observed
    assert not any(
        forbidden in item
        for item in observed
        for forbidden in ("filter", "defaults", "template", "reference", "pdf-engine")
    )


def test_docx_normalization_removes_timestamp_drift(tmp_path: Path) -> None:
    normalized: list[bytes] = []
    for index, timestamp in enumerate(("2026-08-10T01:02:03Z", "2028-09-11T04:05:06Z")):
        source = tmp_path / f"source-{index}.docx"
        target = tmp_path / f"target-{index}.docx"
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("word/document.xml", b"<document>safe</document>")
            archive.writestr(
                "docProps/core.xml",
                (
                    '<cp:coreProperties xmlns:cp="cp" xmlns:dcterms="dcterms">'
                    f'<dcterms:created>{timestamp}</dcterms:created>'
                    f'<dcterms:modified>{timestamp}</dcterms:modified>'
                    "</cp:coreProperties>"
                ).encode(),
            )
        file_artifacts._normalize_docx(source, target)
        normalized.append(target.read_bytes())
    assert normalized[0] == normalized[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("adapter_id", "tool_name", "extra_arguments"),
    (
        (
            "zcaceres-markdownify-mcp",
            "docx-to-markdown",
            {"url": "https://example.com/document.docx"},
        ),
        (
            "vivekvells-mcp-pandoc",
            "convert-contents",
            {"filters": ["/tmp/evil"]},
        ),
    ),
)
async def test_file_facades_reject_undeclared_top_level_arguments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_id: str,
    tool_name: str,
    extra_arguments: dict[str, Any],
) -> None:
    context, file_id = _workspace(
        tmp_path, monkeypatch, filename="source.docx", content=b"not executed"
    )
    assert file_id is not None
    arguments: dict[str, Any] = {
        "file_id": file_id,
        "artifact_name": "blocked",
        **extra_arguments,
    }
    if adapter_id == "vivekvells-mcp-pandoc":
        arguments.update(input_format="markdown", output_format="html")
    with pytest.raises(Exception, match="Extra inputs are not permitted"):
        await WAVE18A_BUILDERS[adapter_id](context).call_tool(tool_name, arguments)


@pytest.mark.asyncio
async def test_antv_local_facade_generates_deterministic_png_and_rejects_extra_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("matplotlib")
    pytest.importorskip("PIL")
    context, _ = _workspace(tmp_path, monkeypatch)
    mcp = WAVE18A_BUILDERS["antvis-mcp-server-chart"](context)
    arguments = {
        "data": [
            {"time": "2025", "value": 4},
            {"time": "2026", "value": 7},
        ],
        "title": "Wave 18A",
    }
    first = await mcp.call_tool(
        "generate_line_chart", {**arguments, "artifact_name": "first.png"}
    )
    second = await mcp.call_tool(
        "generate_line_chart", {**arguments, "artifact_name": "second.png"}
    )
    assert first and second
    first_bytes = (context.output_root / "first.png").read_bytes()
    second_bytes = (context.output_root / "second.png").read_bytes()
    assert first_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert first_bytes == second_bytes
    with pytest.raises(Exception, match="url"):
        await mcp.call_tool(
            "generate_line_chart",
            {**arguments, "url": "https://example.com/chart"},
        )


def test_file_image_pins_pandoc_release_and_compose_allows_accepted_ids() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "server/sandbox_sidecar/Dockerfile.files").read_text(
        encoding="utf-8"
    )
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "PANDOC_VERSION=3.10.1" in dockerfile
    assert "72948bf5784f560d5ad1876709daca27e0667f262da727bb33f77b58e52df2f5" in dockerfile
    assert "cd3963da375793a4804c65ae538b4f7b9c23f87cac7f6c74a1cf5e2fff7e8d59" in dockerfile
    allowlist_line = next(
        line for line in compose.splitlines() if "MCP_FILE_ALLOWED_ADAPTERS:" in line
    )
    assert all(adapter_id in allowlist_line for adapter_id in WAVE18A_IDS)
