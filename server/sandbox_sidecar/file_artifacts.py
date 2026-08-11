"""Deterministic, network-free artifact facades for catalog Wave 18A."""

from __future__ import annotations

import io
import math
import os
import re
import subprocess
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field


FileId = Annotated[
    str,
    Field(
        description="从当前受控工作区选择文件，不接受本机路径或 URI。",
        json_schema_extra={"x-modelmirror-input": "workspace-file"},
    ),
]
ArtifactName = Annotated[
    str,
    Field(
        description="产物文件名；产物只会写入可清理目录。",
        json_schema_extra={"x-modelmirror-input": "artifact-name"},
    ),
]

ARTIFACT_CREATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)

MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_MARKDOWN_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_CHART_POINTS = 200
PANDOC_VERSION = "3.10.1"
PANDOC_PATH = Path("/usr/local/bin/pandoc")
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{6}")
CORE_TIME_PATTERN = re.compile(
    rb"(<dcterms:(?:created|modified)\b[^>]*>).*?(</dcterms:(?:created|modified)>)",
    re.DOTALL,
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _freeze_strict_tool_contract(mcp: FastMCP) -> FastMCP:
    """Reject undeclared top-level arguments in the pinned FastMCP runtime."""

    for tool in mcp._tool_manager._tools.values():
        argument_model = tool.fn_metadata.arg_model
        argument_model.model_config = ConfigDict(
            **dict(argument_model.model_config),
            extra="forbid",
        )
        argument_model.model_rebuild(force=True)
        tool.parameters = argument_model.model_json_schema(by_alias=True)
    return mcp


class LineDatum(_StrictModel):
    time: str = Field(min_length=1, max_length=80)
    value: float = Field(ge=-1_000_000_000_000, le=1_000_000_000_000)
    group: str | None = Field(default=None, min_length=1, max_length=80)


class CategoryDatum(_StrictModel):
    category: str = Field(min_length=1, max_length=80)
    value: float = Field(ge=-1_000_000_000_000, le=1_000_000_000_000)
    group: str | None = Field(default=None, min_length=1, max_length=80)


class PieDatum(_StrictModel):
    category: str = Field(min_length=1, max_length=80)
    value: float = Field(ge=0, le=1_000_000_000_000)


class ChartStyle(_StrictModel):
    backgroundColor: str | None = Field(default=None, max_length=7)
    palette: list[str] | None = Field(default=None, min_length=1, max_length=10)
    texture: Literal["default"] = "default"


class LineStyle(ChartStyle):
    startAtZero: bool = False
    lineWidth: float = Field(default=2.0, ge=0.5, le=8.0)


def _bounded_source(context: Any, file_id: str, suffixes: set[str]) -> Path:
    path = context.resolve_file(file_id)
    if path.suffix.lower() not in suffixes:
        raise ValueError("所选文件格式与该转换工具不匹配。")
    size = path.stat().st_size
    if size <= 0 or size > MAX_SOURCE_BYTES:
        raise ValueError("输入文件必须为非空且不超过 16 MiB。")
    return path


def _write_text_artifact(context: Any, artifact_name: str, text: str) -> dict[str, Any]:
    normalized = "\n".join(
        line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ).rstrip() + "\n"
    data = normalized.encode("utf-8")
    if not data or len(data) > MAX_MARKDOWN_BYTES:
        raise ValueError("Markdown 产物为空或超过 4 MiB。")
    target = context.artifact_path(artifact_name, ".md")
    target.write_bytes(data)
    return context.artifact_payload(target)


def _markdownify(context: Any, file_id: str, artifact_name: str, suffixes: set[str]) -> dict[str, Any]:
    from markitdown import MarkItDown

    path = _bounded_source(context, file_id, suffixes)
    try:
        result = MarkItDown(enable_plugins=False).convert_local(path)
        text = str(result.markdown or "")
    except Exception as exc:
        raise ValueError("本地文件无法安全转换为 Markdown。") from exc
    return _write_text_artifact(context, artifact_name, text)


def build_markdownify(context: Any) -> FastMCP:
    """Expose the reviewed local-file subset of Markdownify MCP v1.1.0."""

    mcp = FastMCP("ModelMirror Markdownify MCP")

    @mcp.tool(name="pdf-to-markdown", annotations=ARTIFACT_CREATE)
    def pdf_to_markdown(file_id: FileId, artifact_name: ArtifactName = "converted.md") -> dict[str, Any]:
        """Convert a selected local PDF to a registered Markdown artifact."""
        return _markdownify(context, file_id, artifact_name, {".pdf"})

    @mcp.tool(name="docx-to-markdown", annotations=ARTIFACT_CREATE)
    def docx_to_markdown(file_id: FileId, artifact_name: ArtifactName = "converted.md") -> dict[str, Any]:
        """Convert a selected local DOCX to a registered Markdown artifact."""
        return _markdownify(context, file_id, artifact_name, {".docx"})

    @mcp.tool(name="xlsx-to-markdown", annotations=ARTIFACT_CREATE)
    def xlsx_to_markdown(file_id: FileId, artifact_name: ArtifactName = "converted.md") -> dict[str, Any]:
        """Convert a selected local XLSX to a registered Markdown artifact."""
        return _markdownify(context, file_id, artifact_name, {".xlsx"})

    @mcp.tool(name="pptx-to-markdown", annotations=ARTIFACT_CREATE)
    def pptx_to_markdown(file_id: FileId, artifact_name: ArtifactName = "converted.md") -> dict[str, Any]:
        """Convert a selected local PPTX to a registered Markdown artifact."""
        return _markdownify(context, file_id, artifact_name, {".pptx"})

    return _freeze_strict_tool_contract(mcp)


def _pandoc_input_format(value: str, path: Path) -> str:
    mapping = {
        "markdown": ({".md", ".markdown"}, "markdown-raw_html-raw_attribute"),
        "html": ({".html", ".htm"}, "html-raw_html"),
        "txt": ({".txt"}, "plain"),
    }
    suffixes, pandoc_format = mapping[value]
    if path.suffix.lower() not in suffixes:
        raise ValueError("input_format 与所选文件扩展名不匹配。")
    return pandoc_format


def _normalize_docx(source: Path, target: Path) -> None:
    total = 0
    with zipfile.ZipFile(source, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(infos) > 5_000 or len(names) != len(set(names)):
            raise ValueError("Pandoc DOCX 包结构无效。")
        for info in infos:
            pure = PurePosixPath(info.filename)
            if pure.is_absolute() or ".." in pure.parts or info.is_dir():
                raise ValueError("Pandoc DOCX 包路径无效。")
            total += info.file_size
            if total > MAX_ARTIFACT_BYTES:
                raise ValueError("Pandoc DOCX 产物超过 32 MiB。")
        payloads = {info.filename: archive.read(info) for info in infos}

    core_name = "docProps/core.xml"
    if core_name in payloads:
        payloads[core_name] = CORE_TIME_PATTERN.sub(
            lambda match: (
                match.group(1) + b"1980-01-01T00:00:00Z" + match.group(2)
            ),
            payloads[core_name],
        )
    temporary = target.with_name(f".{target.name}.normalized")
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as output:
            for name in sorted(payloads):
                info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                output.writestr(info, payloads[name])
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _pandoc_convert(
    context: Any,
    file_id: str,
    input_format: str,
    output_format: str,
    artifact_name: str,
) -> dict[str, Any]:
    path = _bounded_source(
        context, file_id, {".md", ".markdown", ".html", ".htm", ".txt"}
    )
    source_format = _pandoc_input_format(input_format, path)
    output = {
        "markdown": ("gfm-raw_html", ".md"),
        "html": ("html5", ".html"),
        "docx": ("docx", ".docx"),
    }[output_format]
    pandoc_format, suffix = output
    target = context.artifact_path(artifact_name, suffix)
    raw_target = target
    if output_format == "docx":
        raw_target = Path(os.getenv("TMPDIR", "/tmp")) / "pandoc-output.docx"
        raw_target.unlink(missing_ok=True)
    command = [
        str(PANDOC_PATH),
        "--sandbox",
        f"--from={source_format}",
        f"--to={pandoc_format}",
        "--strip-comments",
        "--wrap=none",
        "--eol=lf",
        f"--output={raw_target}",
        str(path),
    ]
    if output_format in {"html", "docx"}:
        command.insert(-2, "--standalone")
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": os.getenv("HOME", "/tmp"),
        "TMPDIR": os.getenv("TMPDIR", "/tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": "315532800",
    }
    try:
        result = subprocess.run(
            command,
            cwd=env["TMPDIR"],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Pandoc 转换超过 30 秒限制。") from exc
    if result.returncode != 0 or not raw_target.is_file():
        raw_target.unlink(missing_ok=True)
        raise ValueError("Pandoc 无法按固定安全契约完成转换。")
    if raw_target.stat().st_size <= 0 or raw_target.stat().st_size > MAX_ARTIFACT_BYTES:
        raw_target.unlink(missing_ok=True)
        raise ValueError("Pandoc 产物为空或超过 32 MiB。")
    if output_format == "docx":
        try:
            _normalize_docx(raw_target, target)
        finally:
            raw_target.unlink(missing_ok=True)
    else:
        data = raw_target.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        target.write_bytes(data.rstrip() + b"\n")
    payload = context.artifact_payload(target)
    payload.update({"input_format": input_format, "output_format": output_format})
    return payload


def build_pandoc(context: Any) -> FastMCP:
    """Expose a path-free subset of mcp-pandoc v0.11.0 using Pandoc 3.10.1."""

    mcp = FastMCP("ModelMirror MCP Pandoc")

    @mcp.tool(name="convert-contents", annotations=ARTIFACT_CREATE)
    def convert_contents(
        file_id: FileId,
        input_format: Literal["markdown", "html", "txt"],
        output_format: Literal["markdown", "html", "docx"],
        artifact_name: ArtifactName = "converted",
    ) -> dict[str, Any]:
        """Convert a selected sealed file using fixed formats and no filters or templates."""
        return _pandoc_convert(
            context, file_id, input_format, output_format, artifact_name
        )

    return _freeze_strict_tool_contract(mcp)


def _validate_chart_common(
    *,
    width: int,
    height: int,
    title: str,
    axis_x_title: str = "",
    axis_y_title: str = "",
    style: ChartStyle | None,
) -> tuple[list[str], str, str]:
    if not 320 <= width <= 1600 or not 240 <= height <= 1200:
        raise ValueError("图表尺寸必须在 320x240 到 1600x1200 之间。")
    if any(len(value) > 120 for value in (title, axis_x_title, axis_y_title)):
        raise ValueError("图表标题和坐标轴标题不能超过 120 个字符。")
    palette = ["#2563EB", "#DC2626", "#059669", "#7C3AED", "#D97706"]
    background = "#FFFFFF"
    if style is not None:
        if style.backgroundColor is not None:
            if not COLOR_PATTERN.fullmatch(style.backgroundColor):
                raise ValueError("backgroundColor 必须是六位十六进制颜色。")
            background = style.backgroundColor.upper()
        if style.palette is not None:
            if any(not COLOR_PATTERN.fullmatch(color) for color in style.palette):
                raise ValueError("palette 只接受六位十六进制颜色。")
            palette = [color.upper() for color in style.palette]
    return palette, background, title


def _save_chart(context: Any, artifact_name: str, figure: Any, points: int) -> dict[str, Any]:
    from PIL import Image

    target = context.artifact_path(artifact_name, ".png")
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=100, metadata={"Software": "ModelMirror"})
    import matplotlib.pyplot as plt

    plt.close(figure)
    buffer.seek(0)
    with Image.open(buffer) as image:
        image.save(target, format="PNG", compress_level=9, optimize=False)
    if target.stat().st_size <= 0 or target.stat().st_size > MAX_ARTIFACT_BYTES:
        target.unlink(missing_ok=True)
        raise ValueError("PNG 产物为空或超过 32 MiB。")
    payload = context.artifact_payload(target)
    payload["data_points"] = points
    return payload


def _chart_axes(width: int, height: int, background: str, theme: str) -> tuple[Any, Any]:
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    dark = theme == "dark"
    face = "#111827" if dark else background
    text = "#F9FAFB" if dark else "#111827"
    with plt.rc_context(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.unicode_minus": False,
            "svg.hashsalt": "modelmirror-wave18a",
        }
    ):
        figure, axes = plt.subplots(figsize=(width / 100, height / 100), dpi=100)
    figure.patch.set_facecolor(face)
    axes.set_facecolor(face)
    axes.tick_params(colors=text)
    axes.xaxis.label.set_color(text)
    axes.yaxis.label.set_color(text)
    axes.title.set_color(text)
    for spine in axes.spines.values():
        spine.set_color(text)
    return figure, axes


def _finite(values: list[float]) -> None:
    if not values or any(not math.isfinite(value) for value in values):
        raise ValueError("图表数值必须为有限数字。")


def build_antv_chart(context: Any) -> FastMCP:
    """Expose a deterministic local subset of AntV MCP Server Chart 0.9.10."""

    mcp = FastMCP("ModelMirror AntV Chart Compatible Facade")

    @mcp.tool(name="generate_line_chart", annotations=ARTIFACT_CREATE)
    def generate_line_chart(
        data: Annotated[list[LineDatum], Field(min_length=1, max_length=MAX_CHART_POINTS)],
        style: LineStyle | None = None,
        theme: Literal["default", "academy", "dark"] = "default",
        width: int = 600,
        height: int = 400,
        title: str = "",
        axisXTitle: str = "",
        axisYTitle: str = "",
        artifact_name: ArtifactName = "line-chart.png",
    ) -> dict[str, Any]:
        palette, background, clean_title = _validate_chart_common(
            width=width,
            height=height,
            title=title,
            axis_x_title=axisXTitle,
            axis_y_title=axisYTitle,
            style=style,
        )
        values = [item.value for item in data]
        _finite(values)
        figure, axes = _chart_axes(width, height, background, theme)
        grouped: dict[str, list[LineDatum]] = defaultdict(list)
        for item in data:
            grouped[item.group or "value"].append(item)
        for index, (group_name, items) in enumerate(grouped.items()):
            axes.plot(
                [item.time for item in items],
                [item.value for item in items],
                color=palette[index % len(palette)],
                linewidth=style.lineWidth if style else 2.0,
                marker="o",
                label=group_name if len(grouped) > 1 else None,
            )
        if style and style.startAtZero:
            axes.set_ylim(bottom=0)
        axes.set_title(clean_title)
        axes.set_xlabel(axisXTitle)
        axes.set_ylabel(axisYTitle)
        axes.grid(True, alpha=0.2)
        if len(grouped) > 1:
            axes.legend()
        figure.tight_layout()
        payload = _save_chart(context, artifact_name, figure, len(data))
        payload["chart_type"] = "line"
        return payload

    @mcp.tool(name="generate_bar_chart", annotations=ARTIFACT_CREATE)
    def generate_bar_chart(
        data: Annotated[list[CategoryDatum], Field(min_length=1, max_length=MAX_CHART_POINTS)],
        group: bool = False,
        stack: bool = True,
        style: ChartStyle | None = None,
        theme: Literal["default", "academy", "dark"] = "default",
        width: int = 600,
        height: int = 400,
        title: str = "",
        axisXTitle: str = "",
        axisYTitle: str = "",
        artifact_name: ArtifactName = "bar-chart.png",
    ) -> dict[str, Any]:
        if group and stack:
            raise ValueError("group 与 stack 不能同时为 true。")
        values = [item.value for item in data]
        _finite(values)
        palette, background, clean_title = _validate_chart_common(
            width=width,
            height=height,
            title=title,
            axis_x_title=axisXTitle,
            axis_y_title=axisYTitle,
            style=style,
        )
        figure, axes = _chart_axes(width, height, background, theme)
        categories = list(dict.fromkeys(item.category for item in data))
        groups = list(dict.fromkeys((item.group or "value") for item in data))
        matrix = {
            (category, group_name): sum(
                item.value
                for item in data
                if item.category == category and (item.group or "value") == group_name
            )
            for category in categories
            for group_name in groups
        }
        positions = list(range(len(categories)))
        if group and len(groups) > 1:
            bar_height = 0.8 / len(groups)
            for index, group_name in enumerate(groups):
                offset = (index - (len(groups) - 1) / 2) * bar_height
                axes.barh(
                    [position + offset for position in positions],
                    [matrix[(category, group_name)] for category in categories],
                    height=bar_height,
                    color=palette[index % len(palette)],
                    label=group_name,
                )
        elif stack and len(groups) > 1:
            left = [0.0] * len(categories)
            for index, group_name in enumerate(groups):
                current = [matrix[(category, group_name)] for category in categories]
                axes.barh(
                    positions,
                    current,
                    left=left,
                    color=palette[index % len(palette)],
                    label=group_name,
                )
                left = [left_value + value for left_value, value in zip(left, current)]
        else:
            category_values = [
                sum(item.value for item in data if item.category == category)
                for category in categories
            ]
            axes.barh(positions, category_values, color=palette[0])
        axes.set_yticks(positions, categories)
        axes.set_title(clean_title)
        axes.set_xlabel(axisXTitle)
        axes.set_ylabel(axisYTitle)
        axes.grid(True, axis="x", alpha=0.2)
        if len(groups) > 1:
            axes.legend()
        figure.tight_layout()
        payload = _save_chart(context, artifact_name, figure, len(data))
        payload["chart_type"] = "bar"
        return payload

    @mcp.tool(name="generate_pie_chart", annotations=ARTIFACT_CREATE)
    def generate_pie_chart(
        data: Annotated[list[PieDatum], Field(min_length=1, max_length=MAX_CHART_POINTS)],
        innerRadius: float = Field(default=0.0, ge=0.0, le=0.8),
        style: ChartStyle | None = None,
        theme: Literal["default", "academy", "dark"] = "default",
        width: int = 600,
        height: int = 400,
        title: str = "",
        artifact_name: ArtifactName = "pie-chart.png",
    ) -> dict[str, Any]:
        values = [item.value for item in data]
        _finite(values)
        if sum(values) <= 0:
            raise ValueError("饼图数值之和必须大于零。")
        palette, background, clean_title = _validate_chart_common(
            width=width, height=height, title=title, style=style
        )
        figure, axes = _chart_axes(width, height, background, theme)
        wedgeprops = {"width": 1 - innerRadius} if innerRadius > 0 else None
        axes.pie(
            values,
            labels=[item.category for item in data],
            colors=[palette[index % len(palette)] for index in range(len(data))],
            wedgeprops=wedgeprops,
        )
        axes.set_title(clean_title)
        axes.axis("equal")
        figure.tight_layout()
        payload = _save_chart(context, artifact_name, figure, len(data))
        payload["chart_type"] = "pie"
        return payload

    return _freeze_strict_tool_contract(mcp)


WAVE18A_BUILDERS = {
    "zcaceres-markdownify-mcp": build_markdownify,
    "vivekvells-mcp-pandoc": build_pandoc,
    "antvis-mcp-server-chart": build_antv_chart,
}

WAVE18A_TOOL_NAMES = {
    "zcaceres-markdownify-mcp": (
        "pdf-to-markdown",
        "docx-to-markdown",
        "xlsx-to-markdown",
        "pptx-to-markdown",
    ),
    "vivekvells-mcp-pandoc": ("convert-contents",),
    "antvis-mcp-server-chart": (
        "generate_line_chart",
        "generate_bar_chart",
        "generate_pie_chart",
    ),
}

# Filled from the actual FastMCP tools/list response and enforced by tests/smoke.
WAVE18A_SCHEMA_SHA256 = {
    "zcaceres-markdownify-mcp": (
        "3980779d679e49797985fbb20bd537362a6c8049d1e8f1cc1b72e0b9536e03d7"
    ),
    "vivekvells-mcp-pandoc": (
        "33c536ccdb70ec575d105ad0931d40a737bb61b9b3171011d2f7297c3e4a5166"
    ),
    "antvis-mcp-server-chart": (
        "2762f1d064817d7c5ccb203b221bb2c26414c59e48b7ceaa8b41a69357e6c15f"
    ),
}
