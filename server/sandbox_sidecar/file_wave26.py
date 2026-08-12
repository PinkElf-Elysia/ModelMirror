"""Wave 26A offline deterministic compatibility contracts."""

from __future__ import annotations

import ast
import math
import os
import warnings
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import ConfigDict, Field


FileId = Annotated[
    str,
    Field(
        description=(
            "Select an image from the current sealed workspace. Host paths and "
            "URIs are not accepted."
        ),
        json_schema_extra={"x-modelmirror-input": "workspace-file"},
    ),
]
ArtifactName = Annotated[
    str,
    Field(
        description="PNG artifact name written only to the managed output directory.",
        json_schema_extra={"x-modelmirror-input": "artifact-name"},
    ),
]

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
ARTIFACT_CREATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)

CALCULATOR_ADAPTER_ID = "githejie-mcp-server-calculator"
IMAGESORCERY_ADAPTER_ID = "sunriseapps-imagesorcery-mcp"
STAGED_WAVE26_ADAPTERS = frozenset({IMAGESORCERY_ADAPTER_ID})

MAX_EXPRESSION_CHARS = 256
MAX_EXPRESSION_NODES = 64
MAX_EXPRESSION_DEPTH = 16
MAX_ABSOLUTE_RESULT = 1e100
MAX_IMAGE_SOURCE_BYTES = 16 * 1024 * 1024
MAX_IMAGE_DIMENSION = 8_192
MAX_IMAGE_PIXELS = 25_000_000
MAX_IMAGE_ARTIFACT_BYTES = 32 * 1024 * 1024
ALLOWED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})


def _freeze_strict_tool_contract(mcp: FastMCP) -> FastMCP:
    for tool in mcp._tool_manager._tools.values():
        argument_model = tool.fn_metadata.arg_model
        argument_model.model_config = ConfigDict(
            **dict(argument_model.model_config),
            extra="forbid",
            allow_inf_nan=False,
        )
        argument_model.model_rebuild(force=True)
        tool.parameters = argument_model.model_json_schema(by_alias=True)
    return mcp


_CALCULATOR_FUNCTIONS: dict[str, Any] = {
    "acos": math.acos,
    "asin": math.asin,
    "atan": math.atan,
    "atan2": math.atan2,
    "ceil": math.ceil,
    "cos": math.cos,
    "degrees": math.degrees,
    "exp": math.exp,
    "fabs": math.fabs,
    "floor": math.floor,
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "radians": math.radians,
    "sin": math.sin,
    "sqrt": math.sqrt,
    "tan": math.tan,
}
_CALCULATOR_CONSTANTS = {"e": math.e, "pi": math.pi, "tau": math.tau}
_BINARY_OPERATORS = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.FloorDiv: lambda left, right: left // right,
    ast.Mod: lambda left, right: left % right,
    ast.Pow: lambda left, right: left**right,
}


def _bounded_number(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("calculator_non_numeric_result")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("calculator_non_finite_result")
    if abs(value) > MAX_ABSOLUTE_RESULT:
        raise ValueError("calculator_result_limit_exceeded")
    return value


def _expression_depth(node: ast.AST) -> int:
    children = list(ast.iter_child_nodes(node))
    return 1 if not children else 1 + max(_expression_depth(child) for child in children)


def evaluate_calculator_expression(expression: str) -> int | float:
    clean = str(expression or "").strip().replace("^", "**").replace("×", "*").replace("÷", "/")
    if not clean or len(clean) > MAX_EXPRESSION_CHARS:
        raise ValueError("calculator_expression_length_invalid")
    try:
        root = ast.parse(clean, mode="eval")
    except (SyntaxError, ValueError) as exc:
        raise ValueError("calculator_expression_invalid") from exc
    nodes = list(ast.walk(root))
    if len(nodes) > MAX_EXPRESSION_NODES or _expression_depth(root) > MAX_EXPRESSION_DEPTH:
        raise ValueError("calculator_expression_complexity_exceeded")

    def evaluate(node: ast.AST) -> int | float | Any:
        if isinstance(node, ast.Constant):
            return _bounded_number(node.value)
        if isinstance(node, ast.Name):
            if node.id in _CALCULATOR_CONSTANTS:
                return _CALCULATOR_CONSTANTS[node.id]
            if node.id in _CALCULATOR_FUNCTIONS:
                return _CALCULATOR_FUNCTIONS[node.id]
            raise ValueError("calculator_identifier_denied")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            operand = _bounded_number(evaluate(node.operand))
            return _bounded_number(-operand if isinstance(node.op, ast.USub) else operand)
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
            left = _bounded_number(evaluate(node.left))
            right = _bounded_number(evaluate(node.right))
            if isinstance(node.op, ast.Pow):
                if abs(right) > 100 or abs(left) > 1_000_000:
                    raise ValueError("calculator_power_limit_exceeded")
            try:
                return _bounded_number(_BINARY_OPERATORS[type(node.op)](left, right))
            except (ArithmeticError, OverflowError, ValueError) as exc:
                raise ValueError("calculator_arithmetic_error") from exc
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            function = _CALCULATOR_FUNCTIONS.get(node.func.id)
            if function is None or node.keywords or len(node.args) not in {1, 2}:
                raise ValueError("calculator_function_call_denied")
            arguments = [_bounded_number(evaluate(argument)) for argument in node.args]
            try:
                return _bounded_number(function(*arguments))
            except (ArithmeticError, OverflowError, TypeError, ValueError) as exc:
                raise ValueError("calculator_arithmetic_error") from exc
        raise ValueError("calculator_operation_denied")

    return _bounded_number(evaluate(root.body))


def build_calculator(_context: Any) -> FastMCP:
    """Expose the reviewed single-tool contract of calculator 0.2.1."""

    mcp = FastMCP("ModelMirror MCP Server Calculator")

    @mcp.tool(annotations=READ_ONLY)
    def calculate(
        expression: Annotated[str, Field(min_length=1, max_length=MAX_EXPRESSION_CHARS)],
    ) -> dict[str, Any]:
        """Evaluate one bounded numeric expression without code execution."""

        result = evaluate_calculator_expression(expression)
        return {"result": str(result), "numeric_result": result}

    return _freeze_strict_tool_contract(mcp)


def _load_image(context: Any, file_id: str):
    from PIL import Image

    path = context.resolve_file(file_id)
    if path.suffix.casefold() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("imagesorcery_input_format_denied")
    size = path.stat().st_size
    if size <= 0 or size > MAX_IMAGE_SOURCE_BYTES:
        raise ValueError("imagesorcery_input_size_invalid")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as probe:
                if probe.format not in ALLOWED_IMAGE_FORMATS:
                    raise ValueError("imagesorcery_input_format_denied")
                if getattr(probe, "n_frames", 1) != 1:
                    raise ValueError("imagesorcery_animated_input_denied")
                width, height = probe.size
                if (
                    width <= 0
                    or height <= 0
                    or width > MAX_IMAGE_DIMENSION
                    or height > MAX_IMAGE_DIMENSION
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    raise ValueError("imagesorcery_pixel_limit_exceeded")
                source_format = str(probe.format)
                source_mode = str(probe.mode)
                probe.verify()
            with Image.open(path) as image:
                image.load()
                use_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
                normalized = image.convert("RGBA" if use_alpha else "RGB")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("imagesorcery_image_invalid") from exc
    return path, normalized, source_format, source_mode


def _validate_output_dimensions(width: int, height: int) -> None:
    if (
        width <= 0
        or height <= 0
        or width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise ValueError("imagesorcery_output_pixel_limit_exceeded")


def _write_png_artifact(context: Any, artifact_name: str, image: Any) -> dict[str, Any]:
    target = context.artifact_path(artifact_name, ".png")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(target, flags, 0o640)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            image.save(
                stream,
                format="PNG",
                optimize=False,
                compress_level=9,
            )
            stream.flush()
            os.fsync(stream.fileno())
        if target.is_symlink() or not target.is_file():
            raise ValueError("imagesorcery_artifact_invalid")
        if target.stat().st_size <= 0 or target.stat().st_size > MAX_IMAGE_ARTIFACT_BYTES:
            raise ValueError("imagesorcery_artifact_size_exceeded")
        return context.artifact_payload(target)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        target.unlink(missing_ok=True)
        raise


def build_imagesorcery(context: Any) -> FastMCP:
    """Expose a path-free deterministic subset of ImageSorcery MCP 0.12.0."""

    from PIL import Image

    mcp = FastMCP("ModelMirror ImageSorcery MCP")

    @mcp.tool(annotations=READ_ONLY)
    def get_metainfo(file_id: FileId) -> dict[str, Any]:
        """Return bounded image metadata without host paths or filesystem times."""

        path, image, source_format, source_mode = _load_image(context, file_id)
        width, height = image.size
        image.close()
        return {
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "dimensions": {
                "width": width,
                "height": height,
                "aspect_ratio": round(width / height, 6),
            },
            "format": source_format,
            "color_mode": source_mode,
        }

    @mcp.tool(annotations=ARTIFACT_CREATE)
    def resize(
        file_id: FileId,
        width: Annotated[int | None, Field(default=None, ge=1, le=MAX_IMAGE_DIMENSION)] = None,
        height: Annotated[int | None, Field(default=None, ge=1, le=MAX_IMAGE_DIMENSION)] = None,
        scale_factor: Annotated[float | None, Field(default=None, ge=0.05, le=8.0)] = None,
        interpolation: Literal["nearest", "linear", "area", "cubic", "lanczos"] = "area",
        artifact_name: ArtifactName = "resized.png",
    ) -> dict[str, Any]:
        """Resize one selected image and create a registered PNG artifact."""

        _path, image, _source_format, _source_mode = _load_image(context, file_id)
        original_width, original_height = image.size
        if scale_factor is not None:
            target_width = max(1, round(original_width * scale_factor))
            target_height = max(1, round(original_height * scale_factor))
        elif width is not None and height is not None:
            target_width, target_height = width, height
        elif width is not None:
            target_width = width
            target_height = max(1, round(original_height * width / original_width))
        elif height is not None:
            target_height = height
            target_width = max(1, round(original_width * height / original_height))
        else:
            image.close()
            raise ValueError("imagesorcery_resize_dimensions_required")
        _validate_output_dimensions(target_width, target_height)
        methods = {
            "nearest": Image.Resampling.NEAREST,
            "linear": Image.Resampling.BILINEAR,
            "area": Image.Resampling.BOX,
            "cubic": Image.Resampling.BICUBIC,
            "lanczos": Image.Resampling.LANCZOS,
        }
        try:
            transformed = image.resize((target_width, target_height), methods[interpolation])
            return _write_png_artifact(context, artifact_name, transformed)
        finally:
            image.close()
            if "transformed" in locals():
                transformed.close()

    @mcp.tool(annotations=ARTIFACT_CREATE)
    def crop(
        file_id: FileId,
        x1: Annotated[int, Field(ge=0, le=MAX_IMAGE_DIMENSION)],
        y1: Annotated[int, Field(ge=0, le=MAX_IMAGE_DIMENSION)],
        x2: Annotated[int, Field(ge=1, le=MAX_IMAGE_DIMENSION)],
        y2: Annotated[int, Field(ge=1, le=MAX_IMAGE_DIMENSION)],
        artifact_name: ArtifactName = "cropped.png",
    ) -> dict[str, Any]:
        """Crop one selected image using a bounded rectangle."""

        _path, image, _source_format, _source_mode = _load_image(context, file_id)
        width, height = image.size
        if x2 <= x1 or y2 <= y1 or x2 > width or y2 > height:
            image.close()
            raise ValueError("imagesorcery_crop_bounds_invalid")
        try:
            transformed = image.crop((x1, y1, x2, y2))
            return _write_png_artifact(context, artifact_name, transformed)
        finally:
            image.close()
            if "transformed" in locals():
                transformed.close()

    @mcp.tool(annotations=ARTIFACT_CREATE)
    def rotate(
        file_id: FileId,
        angle: Annotated[float, Field(ge=-360.0, le=360.0)],
        artifact_name: ArtifactName = "rotated.png",
    ) -> dict[str, Any]:
        """Rotate one selected image counterclockwise and keep the full result."""

        _path, image, _source_format, _source_mode = _load_image(context, file_id)
        try:
            transformed = image.rotate(
                angle,
                resample=Image.Resampling.BICUBIC,
                expand=True,
            )
            _validate_output_dimensions(*transformed.size)
            return _write_png_artifact(context, artifact_name, transformed)
        finally:
            image.close()
            if "transformed" in locals():
                transformed.close()

    return _freeze_strict_tool_contract(mcp)


WAVE26_BUILDERS = {
    CALCULATOR_ADAPTER_ID: build_calculator,
    IMAGESORCERY_ADAPTER_ID: build_imagesorcery,
}

WAVE26_TOOL_NAMES = {
    CALCULATOR_ADAPTER_ID: ("calculate",),
    IMAGESORCERY_ADAPTER_ID: ("get_metainfo", "resize", "crop", "rotate"),
}

# Filled from the actual FastMCP tools/list response and enforced by tests/smoke.
WAVE26_SCHEMA_SHA256 = {
    CALCULATOR_ADAPTER_ID: (
        "fd720b0ecc719751f3d7fcf5702a3d2c1f7e77073de249cd812c3753bed35a9f"
    ),
    IMAGESORCERY_ADAPTER_ID: (
        "cba6ba696a976b5815e66c31b8ab02b47cf48a6d5bf7604d64ad8bb86b4bbfae"
    ),
}
