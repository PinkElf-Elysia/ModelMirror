from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from server.mcp.catalog import CATALOG_ADAPTERS
from server.mcp.catalog_expansion_v3 import CATALOG_EXPANSION_V3_ADAPTERS
from server.mcp.workspace import FILE_PROJECTS, PROJECT_EXTENSIONS
from server.sandbox_sidecar.file_mcp import BUILDERS
from server.sandbox_sidecar.file_server import (
    DEFAULT_ALLOWED_ADAPTERS,
    STAGED_FILE_ADAPTERS,
)
from server.sandbox_sidecar.file_wave26 import (
    CALCULATOR_ADAPTER_ID,
    IMAGESORCERY_ADAPTER_ID,
    MAX_EXPRESSION_CHARS,
    WAVE26_BUILDERS,
    WAVE26_SCHEMA_SHA256,
    WAVE26_TOOL_NAMES,
    build_calculator,
    build_imagesorcery,
    evaluate_calculator_expression,
)


WAVE26_IDS = {CALCULATOR_ADAPTER_ID, IMAGESORCERY_ADAPTER_ID}


def _digest(tools: list[object]) -> str:
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


class _Context:
    def __init__(self, root: Path, files: dict[str, bytes]) -> None:
        self.input_root = root / "inputs"
        self.output_root = root / "outputs"
        self.input_root.mkdir(parents=True)
        self.output_root.mkdir(parents=True)
        self._files: dict[str, Path] = {}
        for index, (name, content) in enumerate(files.items()):
            path = self.input_root / name
            path.write_bytes(content)
            self._files[f"mcpf_{index:024x}"] = path

    def resolve_file(self, file_id: str) -> Path:
        path = self._files.get(file_id)
        if path is None:
            raise ValueError("selected workspace file does not exist")
        return path

    def artifact_path(self, name: str, suffix: str) -> Path:
        clean = Path(str(name)).name
        if clean != str(name) or clean in {"", ".", ".."}:
            raise ValueError("artifact path invalid")
        if not clean.casefold().endswith(suffix):
            clean += suffix
        target = self.output_root / clean
        if target.exists() or target.is_symlink():
            raise ValueError("artifact already exists")
        return target

    @staticmethod
    def artifact_payload(path: Path) -> dict[str, object]:
        content = path.read_bytes()
        return {
            "artifact_name": path.name,
            "relative_path": path.name,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }


def _image_bytes(size: tuple[int, int] = (96, 64)) -> bytes:
    pillow = pytest.importorskip("PIL.Image")
    import io

    stream = io.BytesIO()
    image = pillow.new("RGB", size, color=(20, 40, 80))
    image.save(stream, format="PNG", optimize=False, compress_level=9)
    image.close()
    return stream.getvalue()


def test_wave26_runtime_is_staged_default_deny_and_not_catalog_executable() -> None:
    assert set(WAVE26_BUILDERS) == WAVE26_IDS
    assert WAVE26_IDS.issubset(BUILDERS)
    assert STAGED_FILE_ADAPTERS == {IMAGESORCERY_ADAPTER_ID}
    assert CALCULATOR_ADAPTER_ID in DEFAULT_ALLOWED_ADAPTERS
    assert IMAGESORCERY_ADAPTER_ID not in DEFAULT_ALLOWED_ADAPTERS
    assert CALCULATOR_ADAPTER_ID in FILE_PROJECTS
    assert PROJECT_EXTENSIONS[CALCULATOR_ADAPTER_ID] == set()
    assert IMAGESORCERY_ADAPTER_ID not in FILE_PROJECTS
    assert IMAGESORCERY_ADAPTER_ID not in PROJECT_EXTENSIONS

    expansion = {
        item.project_id: item
        for item in CATALOG_EXPANSION_V3_ADAPTERS
        if item.project_id in WAVE26_IDS
    }
    assert set(expansion) == WAVE26_IDS
    assert expansion[CALCULATOR_ADAPTER_ID].availability == "ready"
    calculator = CATALOG_ADAPTERS[CALCULATOR_ADAPTER_ID]
    assert calculator.availability == "ready"
    assert calculator.executable is True
    assert calculator.server_command[-1] == CALCULATOR_ADAPTER_ID
    assert set(calculator.tool_policies) == {"calculate"}
    assert calculator.network_policy == "disabled"
    assert calculator.filesystem_policy == "read-only-empty-workspace"

    assert expansion[IMAGESORCERY_ADAPTER_ID].availability == "planned"
    imagesorcery = CATALOG_ADAPTERS[IMAGESORCERY_ADAPTER_ID]
    assert imagesorcery.availability == "planned"
    assert imagesorcery.server_command == ()
    assert imagesorcery.tool_policies == {}
    assert imagesorcery.network_policy == (
        "planned:planned-wave26-offline-file-or-deterministic-artifact"
    )
    assert imagesorcery.filesystem_policy == "planned:no-runtime"


def test_wave26_production_proxy_and_compose_allow_only_accepted_calculator() -> None:
    root = Path(__file__).resolve().parents[2]
    proxy = (root / "server/mcp/file_proxy.py").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert CALCULATOR_ADAPTER_ID in proxy
    assert CALCULATOR_ADAPTER_ID in compose
    assert IMAGESORCERY_ADAPTER_ID not in proxy
    assert IMAGESORCERY_ADAPTER_ID not in compose


@pytest.mark.asyncio
async def test_wave26_tool_names_and_schema_digests_are_frozen() -> None:
    for adapter_id, builder in WAVE26_BUILDERS.items():
        tools = await builder(object()).list_tools()
        assert {tool.name for tool in tools} == set(WAVE26_TOOL_NAMES[adapter_id])
        assert _digest(tools) == WAVE26_SCHEMA_SHA256[adapter_id]
        for tool in tools:
            assert tool.inputSchema.get("additionalProperties") is False


def test_calculator_evaluates_only_bounded_numeric_ast() -> None:
    assert evaluate_calculator_expression("2 + 3 * 4") == 14
    assert evaluate_calculator_expression("sqrt(81) + sin(pi / 2)") == 10.0
    assert evaluate_calculator_expression("2^8") == 256
    for expression in (
        "__import__('os').system('id')",
        "(1).__class__",
        "[1, 2, 3]",
        "sum([1, 2])",
        "9**999999",
        "x + 1",
        "1e1000",
        "1+" * (MAX_EXPRESSION_CHARS // 2) + "1",
    ):
        with pytest.raises(ValueError):
            evaluate_calculator_expression(expression)


@pytest.mark.asyncio
async def test_calculator_tool_rejects_unknown_arguments() -> None:
    mcp = build_calculator(object())
    result = await mcp.call_tool("calculate", {"expression": "6 * 7"})
    assert result
    with pytest.raises(Exception, match="Extra inputs are not permitted"):
        await mcp.call_tool(
            "calculate",
            {"expression": "6 * 7", "command": "id"},
        )


@pytest.mark.asyncio
async def test_imagesorcery_metadata_and_deterministic_artifacts(tmp_path: Path) -> None:
    source = _image_bytes()
    context = _Context(tmp_path, {"source.png": source})
    file_id = next(iter(context._files))
    mcp = build_imagesorcery(context)

    metadata = await mcp.call_tool("get_metainfo", {"file_id": file_id})
    assert metadata
    content = "".join(getattr(item, "text", "") for item in metadata)
    assert str(tmp_path) not in content
    assert "created_at" not in content
    assert "modified_at" not in content

    await mcp.call_tool(
        "resize",
        {"file_id": file_id, "width": 48, "artifact_name": "resized.png"},
    )
    await mcp.call_tool(
        "crop",
        {
            "file_id": file_id,
            "x1": 8,
            "y1": 8,
            "x2": 72,
            "y2": 48,
            "artifact_name": "cropped.png",
        },
    )
    await mcp.call_tool(
        "rotate",
        {"file_id": file_id, "angle": 90, "artifact_name": "rotated.png"},
    )
    assert (context.input_root / "source.png").read_bytes() == source
    assert {path.name for path in context.output_root.iterdir()} == {
        "resized.png",
        "cropped.png",
        "rotated.png",
    }
    for path in context.output_root.iterdir():
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    second = _Context(tmp_path / "second", {"source.png": source})
    second_id = next(iter(second._files))
    second_mcp = build_imagesorcery(second)
    for name, arguments in (
        ("resize", {"file_id": second_id, "width": 48, "artifact_name": "resized.png"}),
        (
            "crop",
            {
                "file_id": second_id,
                "x1": 8,
                "y1": 8,
                "x2": 72,
                "y2": 48,
                "artifact_name": "cropped.png",
            },
        ),
        ("rotate", {"file_id": second_id, "angle": 90, "artifact_name": "rotated.png"}),
    ):
        await second_mcp.call_tool(name, arguments)
    for name in ("resized.png", "cropped.png", "rotated.png"):
        assert (context.output_root / name).read_bytes() == (
            second.output_root / name
        ).read_bytes()


@pytest.mark.asyncio
async def test_imagesorcery_rejects_paths_urls_unsafe_tools_and_limits(tmp_path: Path) -> None:
    context = _Context(tmp_path, {"source.png": _image_bytes()})
    file_id = next(iter(context._files))
    mcp = build_imagesorcery(context)
    with pytest.raises(Exception, match="Extra inputs are not permitted"):
        await mcp.call_tool(
            "resize",
            {"file_id": file_id, "width": 32, "output_path": "/tmp/out.png"},
        )
    with pytest.raises(Exception):
        await mcp.call_tool("resize", {"file_id": "file:///etc/passwd", "width": 32})
    with pytest.raises(Exception):
        await mcp.call_tool("resize", {"file_id": file_id, "width": 9000})
    with pytest.raises(Exception):
        await mcp.call_tool(
            "crop",
            {"file_id": file_id, "x1": 0, "y1": 0, "x2": 1000, "y2": 1000},
        )
    for name in ("detect", "find", "ocr", "overlay", "config"):
        with pytest.raises(Exception):
            await mcp.call_tool(name, {})


def test_wave26_dockerfile_and_notices_pin_contract_without_upstream_packages() -> None:
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "server/sandbox_sidecar/Dockerfile.files").read_text(
        encoding="utf-8"
    )
    notices = (root / "server/sandbox_sidecar/THIRD_PARTY_NOTICES.md").read_text(
        encoding="utf-8"
    )
    assert "file_wave26.py" in dockerfile
    assert "smoke_file_wave26 --contract-only" in dockerfile
    assert "mcp-server-calculator" not in dockerfile
    assert "imagesorcery-mcp" not in dockerfile
    assert "3dcaedcd58867206627d121092b401728db202da" in notices
    assert "2f77957a0671a5cf30d90285c7024ae229d86917" in notices
    assert "pdfmux" in notices
    assert "PyMuPDF 1.27.2.3" in notices
    assert "pymupdf4llm 0.3.4" in notices
    assert "blocked and is not included" in notices
