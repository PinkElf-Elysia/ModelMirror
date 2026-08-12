from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from server.mcp.workspace import FILE_PROJECTS, PROJECT_EXTENSIONS
from server.sandbox_sidecar import file_analysis
from server.sandbox_sidecar.file_analysis import (
    WAVE18B_BUILDERS,
    WAVE18B_SCHEMA_SHA256,
    WAVE18B_TOOL_NAMES,
)
from server.sandbox_sidecar.file_mcp import WorkspaceContext, opaque_file_id
from server.sandbox_sidecar.file_server import (
    DEFAULT_ALLOWED_ADAPTERS,
    STAGED_FILE_ADAPTERS,
    _allowed_adapters,
)


WAVE18B_IDS = frozenset(
    {
        "cyberchitta-llm-context-py",
        "haris-musa-excel-mcp-server",
        "dataeval-dingo",
    }
)
WAVE20_READY_IDS = frozenset({"ozgurcd-gograph"})


def _workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    files: dict[str, bytes],
) -> tuple[WorkspaceContext, dict[str, str]]:
    workspace_id = "mcpws_" + "b" * 32
    input_base = tmp_path / "inputs"
    output_base = tmp_path / "outputs"
    memory_base = tmp_path / "memory"
    input_root = input_base / workspace_id
    input_root.mkdir(parents=True)
    ids: dict[str, str] = {}
    for relative, content in files.items():
        target = input_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        ids[relative] = opaque_file_id(workspace_id, relative)
    monkeypatch.setenv("MCP_FILE_WORKSPACE_ID", workspace_id)
    monkeypatch.setenv("MCP_FILE_INPUT_ROOT", str(input_base))
    monkeypatch.setenv("MCP_FILE_OUTPUT_ROOT", str(output_base))
    monkeypatch.setenv("MCP_FILE_MEMORY_ROOT", str(memory_base))
    return WorkspaceContext(), ids


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
async def test_wave18b_tool_contracts_are_frozen_and_do_not_expose_open_surfaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _ = _workspace(tmp_path, monkeypatch, {})
    forbidden = {
        "root_path",
        "rule_name",
        "filepath",
        "url",
        "headers",
        "environment",
        "command",
        "formula",
        "evaluation_type",
        "kwargs",
        "api_key",
        "output_dir",
    }
    for adapter_id, builder in WAVE18B_BUILDERS.items():
        tools = await builder(context).list_tools()
        assert {tool.name for tool in tools} == set(WAVE18B_TOOL_NAMES[adapter_id])
        assert _digest(tools) == WAVE18B_SCHEMA_SHA256[adapter_id]
        for tool in tools:
            assert tool.inputSchema["additionalProperties"] is False
            properties = tool.inputSchema.get("properties", {})
            assert forbidden.isdisjoint(properties)
            if "file_id" in properties:
                assert properties["file_id"]["x-modelmirror-input"] == "workspace-file"
            if "artifact_name" in properties:
                assert properties["artifact_name"]["x-modelmirror-input"] == "artifact-name"


def test_wave18b_is_default_allowed_after_acceptance_and_has_narrow_workspace_formats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert WAVE18B_IDS.isdisjoint(STAGED_FILE_ADAPTERS)
    assert WAVE20_READY_IDS.isdisjoint(STAGED_FILE_ADAPTERS)
    assert WAVE18B_IDS.issubset(DEFAULT_ALLOWED_ADAPTERS)
    assert WAVE20_READY_IDS.issubset(DEFAULT_ALLOWED_ADAPTERS)
    assert WAVE18B_IDS.issubset(FILE_PROJECTS)
    assert PROJECT_EXTENSIONS["haris-musa-excel-mcp-server"] == {".xlsx"}
    assert PROJECT_EXTENSIONS["dataeval-dingo"] == {".jsonl", ".json", ".csv", ".txt"}
    assert ".py" in PROJECT_EXTENSIONS["cyberchitta-llm-context-py"]
    assert ".exe" not in PROJECT_EXTENSIONS["cyberchitta-llm-context-py"]
    monkeypatch.setenv("MCP_FILE_ALLOWED_ADAPTERS", ",".join(sorted(WAVE18B_IDS)))
    assert _allowed_adapters() == WAVE18B_IDS
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    allowlist = next(
        line for line in compose.splitlines() if "MCP_FILE_ALLOWED_ADAPTERS:" in line
    )
    assert all(adapter_id in allowlist for adapter_id in WAVE18B_IDS)


@pytest.mark.asyncio
async def test_llm_context_preview_and_outline_use_only_sealed_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _ = _workspace(
        tmp_path,
        monkeypatch,
        {
            "src/app.py": b"class App:\n    pass\n\ndef run():\n    return 1\n",
            "README.md": b"# Example\n\n## Usage\n",
        },
    )
    mcp = WAVE18B_BUILDERS["cyberchitta-llm-context-py"](context)
    preview = await mcp.call_tool("lc_preview", {})
    outline = await mcp.call_tool(
        "lc_outlines",
        {"artifact_name": "outline.md", "max_files": 20},
    )
    assert preview and outline
    data = (context.output_root / "outline.md").read_text(encoding="utf-8")
    assert "class App" in data
    assert "FunctionDef run" in data
    assert "heading # Example" in data
    assert str(tmp_path) not in data
    with pytest.raises(Exception, match="Extra inputs are not permitted"):
        await mcp.call_tool("lc_preview", {"root_path": "/etc"})


def _safe_workbook_bytes(tmp_path: Path, *, formula: bool = False) -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / ("formula.xlsx" if formula else "safe.xlsx")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    sheet["A1"] = "name"
    sheet["B1"] = "value"
    sheet["A2"] = "alpha"
    sheet["B2"] = "=1+1" if formula else 2
    workbook.save(path)
    workbook.close()
    return path.read_bytes()


@pytest.mark.asyncio
async def test_excel_reads_and_generates_deterministic_formula_free_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _safe_workbook_bytes(tmp_path)
    context, ids = _workspace(tmp_path / "workspace", monkeypatch, {"safe.xlsx": source})
    mcp = WAVE18B_BUILDERS["haris-musa-excel-mcp-server"](context)
    metadata = await mcp.call_tool(
        "get_workbook_metadata",
        {"file_id": ids["safe.xlsx"], "include_ranges": True},
    )
    rows = await mcp.call_tool(
        "read_data_from_excel",
        {
            "file_id": ids["safe.xlsx"],
            "sheet_name": "Data",
            "start_cell": "A1",
            "end_cell": "B2",
        },
    )
    first = await mcp.call_tool(
        "write_data_to_excel",
        {
            "file_id": ids["safe.xlsx"],
            "sheet_name": "Data",
            "start_cell": "B2",
            "data": [[7]],
            "artifact_name": "first.xlsx",
        },
    )
    second = await mcp.call_tool(
        "write_data_to_excel",
        {
            "file_id": ids["safe.xlsx"],
            "sheet_name": "Data",
            "start_cell": "B2",
            "data": [[7]],
            "artifact_name": "second.xlsx",
        },
    )
    assert metadata and rows and first and second
    assert (context.output_root / "first.xlsx").read_bytes() == (
        context.output_root / "second.xlsx"
    ).read_bytes()
    assert hashlib.sha256(source).hexdigest() == hashlib.sha256(
        (context.input_root / "safe.xlsx").read_bytes()
    ).hexdigest()


@pytest.mark.asyncio
async def test_excel_rejects_formulas_external_links_macros_and_formula_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    formula = _safe_workbook_bytes(tmp_path, formula=True)
    safe = _safe_workbook_bytes(tmp_path / "safe-base")
    external_path = tmp_path / "external.xlsx"
    external_path.write_bytes(_safe_workbook_bytes(tmp_path / "external-base"))
    with zipfile.ZipFile(external_path, "a") as archive:
        archive.writestr("xl/externalLinks/externalLink1.xml", b"<externalLink/>")
    macro_path = tmp_path / "macro.xlsx"
    macro_path.write_bytes(_safe_workbook_bytes(tmp_path / "macro-base"))
    with zipfile.ZipFile(macro_path, "a") as archive:
        archive.writestr("xl/vbaProject.bin", b"macro")
    context, ids = _workspace(
        tmp_path / "workspace",
        monkeypatch,
        {
            "formula.xlsx": formula,
            "safe.xlsx": safe,
            "external.xlsx": external_path.read_bytes(),
            "macro.xlsx": macro_path.read_bytes(),
        },
    )
    mcp = WAVE18B_BUILDERS["haris-musa-excel-mcp-server"](context)
    for name in ("external.xlsx", "macro.xlsx"):
        with pytest.raises(Exception, match="外部|宏"):
            await mcp.call_tool("get_workbook_metadata", {"file_id": ids[name]})
    with pytest.raises(Exception, match="公式"):
        await mcp.call_tool(
            "write_data_to_excel",
            {
                "file_id": ids["formula.xlsx"],
                "sheet_name": "Data",
                "data": [[1]],
            },
        )
    with pytest.raises(Exception, match="公式"):
        await mcp.call_tool(
            "write_data_to_excel",
            {
                "file_id": ids["safe.xlsx"],
                "sheet_name": "Data",
                "data": [["=WEBSERVICE(\"https://example.com\")"]],
            },
        )


@pytest.mark.asyncio
async def test_excel_read_result_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    source_path = tmp_path / "large.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Data"
    for row in range(1, 201):
        for column in range(1, 51):
            sheet.cell(row=row, column=column, value="x" * 32)
    workbook.save(source_path)
    workbook.close()
    context, ids = _workspace(
        tmp_path / "workspace",
        monkeypatch,
        {"large.xlsx": source_path.read_bytes()},
    )
    mcp = WAVE18B_BUILDERS["haris-musa-excel-mcp-server"](context)
    with pytest.raises(Exception, match="220000"):
        await mcp.call_tool(
            "read_data_from_excel",
            {
                "file_id": ids["large.xlsx"],
                "sheet_name": "Data",
                "start_cell": "A1",
                "end_cell": "AX200",
            },
        )
@pytest.mark.asyncio
async def test_dingo_runs_only_fixed_rules_without_echoing_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = (
        '{"content":"good text"}\n'
        '{"content":""}\n'
        '{"content":"unfinished:"}\n'
        '{"content":"�"}\n'
    ).encode("utf-8")
    context, ids = _workspace(tmp_path, monkeypatch, {"records.jsonl": source})
    mcp = WAVE18B_BUILDERS["dataeval-dingo"](context)
    components = await mcp.call_tool(
        "list_dingo_components",
        {"component_type": "rule_groups", "include_details": True},
    )
    result = await mcp.call_tool(
        "run_dingo_evaluation",
        {
            "file_id": ids["records.jsonl"],
            "artifact_name": "report.json",
        },
    )
    assert components and result
    report = json.loads((context.output_root / "report.json").read_text(encoding="utf-8"))
    assert report["evaluation_type"] == "rule"
    assert report["num_bad"] == 3
    assert report["rules"] == [
        "RuleContentNull",
        "RuleColonEnd",
        "RuleSpecialCharacter",
    ]
    assert "good text" not in json.dumps(report)
    with pytest.raises(Exception, match="Extra inputs are not permitted"):
        await mcp.call_tool(
            "run_dingo_evaluation",
            {"file_id": ids["records.jsonl"], "evaluation_type": "llm"},
        )


def test_wave18b_dockerfile_copies_facade_and_does_not_add_upstream_runtime() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "server/sandbox_sidecar/Dockerfile.files").read_text(
        encoding="utf-8"
    )
    assert "file_analysis.py" in dockerfile
    assert "dingo-python" not in dockerfile
    assert "llm-context" not in dockerfile
    assert "excel-mcp-server" not in dockerfile
