from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import math
import os
import queue
import re
import stat
import sys
import threading
import time
import uuid
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

try:
    from server.mcp.manager import MCPClientManager
except ModuleNotFoundError:
    from mcp.manager import MCPClientManager


MAX_RENDER_SPEC_BYTES = 2 * 1024 * 1024
MAX_RENDERED_BYTES = 50 * 1024 * 1024
MAX_TEXT_CHARS = 500_000
MAX_TABLE_COLUMNS = 200
MAX_TABLE_CELLS = 100_000
MAX_WORKBOOK_SHEETS = 20
MAX_PRESENTATION_SLIDES = 100
OUTPUT_RENDER_TIMEOUT_SECONDS = 60.0
OUTPUT_RENDER_ADAPTER_ID = "output-renderer-mcp"
OUTPUT_RENDER_TOOL_NAME = "render_output_document"
OUTPUT_RENDER_MARKER_NAME = ".modelmirror-output-renderer.json"
OUTPUT_RENDER_MARKER_OWNER = "modelmirror.file_assets.output_renderer.v1"

_WORKSPACE_PATTERN = re.compile(r"^mcpws_[0-9a-f]{32}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_LOCAL_FORMATS = {"plain_text", "markdown", "json", "csv"}
_SIDECAR_FORMATS = {"pdf", "docx", "xlsx", "pptx"}
_SUFFIXES = {
    "plain_text": ".txt",
    "markdown": ".md",
    "json": ".json",
    "csv": ".csv",
    "pdf": ".pdf",
    "docx": ".docx",
    "xlsx": ".xlsx",
    "pptx": ".pptx",
}
_MEDIA_TYPES = {
    "plain_text": "text/plain",
    "markdown": "text/markdown",
    "json": "application/json",
    "csv": "text/csv",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class OutputRenderError(RuntimeError):
    def __init__(self, status_code: int, error_code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


class OutputBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["heading", "paragraph", "list", "table"]
    text: str | None = Field(default=None, max_length=20_000)
    level: int = Field(default=1, ge=1, le=6)
    items: tuple[str, ...] = Field(default=(), max_length=2_000)
    rows: tuple[tuple[str, ...], ...] = Field(default=(), max_length=10_000)

    @model_validator(mode="after")
    def validate_shape(self) -> "OutputBlock":
        if self.kind in {"heading", "paragraph"} and self.text is None:
            raise ValueError("text is required for heading and paragraph blocks")
        if self.kind == "list" and not self.items:
            raise ValueError("items are required for list blocks")
        if self.kind == "table" and not self.rows:
            raise ValueError("rows are required for table blocks")
        if any(len(row) > MAX_TABLE_COLUMNS for row in self.rows):
            raise ValueError("table column limit exceeded")
        return self


class OutputSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=31)
    rows: tuple[tuple[str | int | float | bool | None, ...], ...] = Field(
        default=(), max_length=100_000
    )

    @field_validator("name")
    @classmethod
    def safe_sheet_name(cls, value: str) -> str:
        clean = value.strip()
        if not clean or any(character in clean for character in "[]:*?/\\"):
            raise ValueError("invalid worksheet name")
        return clean


class OutputSlide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=2_000)
    blocks: tuple[OutputBlock, ...] = Field(default=(), max_length=1_000)


class OutputRenderSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format_id: Literal[
        "plain_text", "markdown", "json", "csv", "pdf", "docx", "xlsx", "pptx"
    ]
    filename: str = Field(min_length=1, max_length=160)
    title: str = Field(default="", max_length=2_000)
    content: str | dict[str, Any] | list[Any] | None = None
    rows: tuple[tuple[str | int | float | bool | None, ...], ...] = Field(
        default=(), max_length=100_000
    )
    blocks: tuple[OutputBlock, ...] = Field(default=(), max_length=10_000)
    sheets: tuple[OutputSheet, ...] = Field(default=(), max_length=MAX_WORKBOOK_SHEETS)
    slides: tuple[OutputSlide, ...] = Field(default=(), max_length=MAX_PRESENTATION_SLIDES)

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        clean = Path(value.strip()).name
        if clean != value.strip() or clean in {"", ".", ".."}:
            raise ValueError("invalid filename")
        return clean

    @model_validator(mode="after")
    def validate_format_shape(self) -> "OutputRenderSpec":
        suffix = _SUFFIXES[self.format_id]
        if not self.filename.casefold().endswith(suffix):
            raise ValueError("filename suffix does not match format")
        if self.format_id in {"plain_text", "markdown"} and not isinstance(self.content, str):
            raise ValueError("text content is required")
        if self.format_id == "json" and self.content is None:
            raise ValueError("JSON content is required")
        if self.format_id == "csv" and not self.rows:
            raise ValueError("CSV rows are required")
        if self.format_id in {"pdf", "docx"} and not self.blocks:
            raise ValueError("document blocks are required")
        if self.format_id == "xlsx" and not self.sheets:
            raise ValueError("workbook sheets are required")
        if self.format_id == "pptx" and not self.slides:
            raise ValueError("presentation slides are required")
        _validate_cell_budget(self.rows)
        workbook_cells = sum(_cell_count(sheet.rows) for sheet in self.sheets)
        if workbook_cells > MAX_TABLE_CELLS:
            raise ValueError("workbook cell limit exceeded")
        if len({sheet.name.casefold() for sheet in self.sheets}) != len(self.sheets):
            raise ValueError("worksheet names must be unique")
        total_chars = _count_strings(self.model_dump(mode="python"))
        if total_chars > MAX_TEXT_CHARS:
            raise ValueError("render specification character limit exceeded")
        _assert_finite(self.model_dump(mode="python"))
        return self


class RenderedOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    content: bytes
    filename: str
    format_id: str
    media_type: str
    warnings: tuple[str, ...] = ()


def validate_render_spec(payload: Any) -> OutputRenderSpec:
    try:
        spec = OutputRenderSpec.model_validate(payload)
        encoded = json.dumps(
            spec.model_dump(mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except Exception as exc:
        raise OutputRenderError(
            422,
            "output_spec_invalid",
            "The requested file specification is invalid or exceeds its safe limits.",
        ) from exc
    if len(encoded) > MAX_RENDER_SPEC_BYTES:
        raise OutputRenderError(
            413,
            "output_spec_too_large",
            "The requested file specification exceeds 2 MiB.",
        )
    return spec


class FileOutputRenderer:
    def __init__(self, *, sidecar: "OutputRenderSidecar | None" = None) -> None:
        self.sidecar = sidecar or OutputRenderSidecar()

    def render(self, payload: Any) -> RenderedOutput:
        spec = validate_render_spec(payload)
        if spec.format_id in _LOCAL_FORMATS:
            content, warnings = _render_local(spec)
        else:
            content, warnings = self.sidecar.render(spec)
        if not content or len(content) > MAX_RENDERED_BYTES:
            raise OutputRenderError(
                422,
                "output_render_size_invalid",
                "The rendered file is empty or exceeds 50 MiB.",
            )
        return RenderedOutput(
            content=content,
            filename=spec.filename,
            format_id=spec.format_id,
            media_type=_MEDIA_TYPES[spec.format_id],
            warnings=warnings,
        )


class OutputRenderSidecar:
    def __init__(
        self,
        *,
        input_root: str | Path | None = None,
        output_root: str | Path | None = None,
        manager_factory: Callable[[], Any] | None = None,
        operation_timeout: float = OUTPUT_RENDER_TIMEOUT_SECONDS,
        now: Callable[[], float] = time.time,
    ) -> None:
        base = Path(__file__).resolve().parents[1]
        self.input_root = Path(
            input_root or os.getenv("MCP_FILE_INPUT_ROOT", "").strip() or base / "mcp-file-inputs"
        )
        self.output_root = Path(
            output_root or os.getenv("MCP_FILE_OUTPUT_ROOT", "").strip() or base / "mcp-file-outputs"
        )
        self.manager_factory = manager_factory or self._default_manager
        self.operation_timeout = max(0.01, min(float(operation_timeout), 60.0))
        self.now = now

    @staticmethod
    def _default_manager() -> MCPClientManager:
        return MCPClientManager(operation_timeout=OUTPUT_RENDER_TIMEOUT_SECONDS, idle_timeout_seconds=60)

    def render(self, spec: OutputRenderSpec) -> tuple[bytes, tuple[str, ...]]:
        if spec.format_id not in _SIDECAR_FORMATS:
            raise OutputRenderError(422, "output_render_format_invalid", "This format does not use the isolated renderer.")
        workspace: Path | None = None
        try:
            workspace, workspace_id, file_id = self._stage_spec(spec)
            payload = _run_coroutine_sync(
                lambda: self._call_sidecar(workspace_id=workspace_id, file_id=file_id)
            )
            content = self._read_artifact(workspace_id, payload, expected_format=spec.format_id)
            warnings = _bounded_warnings(payload.get("warnings"))
            return content, warnings
        finally:
            if workspace is not None:
                self._remove_workspace(workspace)

    def _root(self, path: Path) -> Path:
        try:
            path.mkdir(parents=True, exist_ok=True)
            if path.is_symlink() or not path.is_dir():
                raise OSError("invalid root")
            return path.resolve(strict=True)
        except OSError as exc:
            raise OutputRenderError(503, "output_renderer_storage_unavailable", "The isolated output renderer is unavailable.") from exc

    def _stage_spec(self, spec: OutputRenderSpec) -> tuple[Path, str, str]:
        content = json.dumps(
            spec.model_dump(mode="json"), ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        if len(content) > MAX_RENDER_SPEC_BYTES:
            raise OutputRenderError(413, "output_spec_too_large", "The requested file specification exceeds 2 MiB.")
        root = self._root(self.input_root)
        self._root(self.output_root)
        workspace_id = f"mcpws_{uuid.uuid4().hex}"
        workspace = root / workspace_id
        digest = hashlib.sha256(content).hexdigest()
        try:
            workspace.mkdir(mode=0o755, exist_ok=False)
            marker = {
                "owner": OUTPUT_RENDER_MARKER_OWNER,
                "workspace_id": workspace_id,
                "created_at": self.now(),
                "source_name": "spec.json",
                "source_sha256": digest,
                "format_id": spec.format_id,
            }
            _atomic_write(workspace / OUTPUT_RENDER_MARKER_NAME, json.dumps(marker, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            _atomic_write(workspace / "spec.json", content)
            os.chmod(workspace, 0o555)
        except Exception as exc:
            self._remove_workspace(workspace, allow_unmarked=True)
            raise OutputRenderError(503, "output_renderer_staging_failed", "The file specification could not be staged safely.") from exc
        file_id = "mcpf_" + hashlib.sha256(f"{workspace_id}:spec.json".encode()).hexdigest()[:24]
        return workspace, workspace_id, file_id

    async def _call_sidecar(self, *, workspace_id: str, file_id: str) -> dict[str, Any]:
        manager = self.manager_factory()
        session_id: str | None = None
        proxy = Path(__file__).resolve().parents[1] / "mcp" / "file_proxy.py"
        environment = {"MCP_FILE_WORKSPACE_ID": workspace_id}
        socket_path = os.getenv("MCP_FILES_SOCKET_PATH", "").strip()
        if socket_path:
            environment["MCP_FILES_SOCKET_PATH"] = socket_path
        profile = {
            "transport": "stdio",
            "server_command": [sys.executable, str(proxy), OUTPUT_RENDER_ADAPTER_ID],
            "environment": environment,
            "network_policy": "catalog-files-none",
            "reconnect_attempts": 0,
            "operation_timeout": self.operation_timeout,
        }
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.operation_timeout

        async def before_deadline(factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            return await asyncio.wait_for(factory(), timeout=remaining)

        try:
            session_id = await before_deadline(lambda: manager.connect_profile(**profile))
            result = await before_deadline(
                lambda: manager.call_tool(session_id, OUTPUT_RENDER_TOOL_NAME, {"file_id": file_id})
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise OutputRenderError(503, "output_renderer_timeout", "The isolated file renderer timed out.") from exc
        except OutputRenderError:
            raise
        except Exception as exc:
            raise OutputRenderError(503, "output_renderer_unavailable", "The isolated file renderer is unavailable.") from exc
        finally:
            if session_id is not None and deadline > loop.time():
                try:
                    await before_deadline(lambda: manager.disconnect(session_id))
                except Exception:
                    pass
        if bool(getattr(result, "isError", False)):
            raise OutputRenderError(422, "output_render_failed", "The file could not be rendered safely.")
        payload = getattr(result, "structuredContent", None)
        if not isinstance(payload, dict):
            raise OutputRenderError(422, "output_renderer_invalid_output", "The isolated renderer returned an invalid result.")
        try:
            encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode()
        except Exception as exc:
            raise OutputRenderError(422, "output_renderer_invalid_output", "The isolated renderer returned an invalid result.") from exc
        if len(encoded) > 64 * 1024:
            raise OutputRenderError(422, "output_renderer_invalid_output", "The isolated renderer result is too large.")
        return payload

    def _read_artifact(self, workspace_id: str, payload: dict[str, Any], *, expected_format: str) -> bytes:
        required = {"artifact_name", "relative_path", "size_bytes", "sha256", "format_id", "media_type"}
        if not required.issubset(payload) or payload.get("format_id") != expected_format:
            raise OutputRenderError(422, "output_renderer_invalid_output", "The isolated renderer returned an invalid result.")
        name = str(payload.get("relative_path") or "")
        if Path(name).name != name or not name.endswith(_SUFFIXES[expected_format]):
            raise OutputRenderError(422, "output_renderer_invalid_output", "The isolated renderer returned an invalid artifact name.")
        root = self._root(self.output_root)
        workspace = root / workspace_id
        path = workspace / name
        expected_size = payload.get("size_bytes")
        expected_sha = str(payload.get("sha256") or "")
        try:
            if workspace.is_symlink() or path.is_symlink() or not path.is_file():
                raise OSError("artifact unavailable")
            if path.resolve(strict=True).parent != workspace.resolve(strict=True):
                raise OSError("artifact escaped workspace")
            info = path.stat(follow_symlinks=False)
            if not isinstance(expected_size, int) or expected_size <= 0 or expected_size > MAX_RENDERED_BYTES:
                raise OSError("invalid artifact size")
            if info.st_size != expected_size or not _SHA256_PATTERN.fullmatch(expected_sha):
                raise OSError("artifact metadata mismatch")
            content = path.read_bytes()
            if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_sha:
                raise OSError("artifact digest mismatch")
            return content
        except OSError as exc:
            raise OutputRenderError(422, "output_renderer_integrity_failed", "The rendered file failed its integrity check.") from exc

    def _read_marker(self, workspace: Path) -> dict[str, Any] | None:
        try:
            path = workspace / OUTPUT_RENDER_MARKER_NAME
            if path.is_symlink() or not path.is_file():
                return None
            raw = path.read_bytes()
            if len(raw) > 4096:
                return None
            payload = json.loads(raw)
        except Exception:
            return None
        if payload.get("owner") != OUTPUT_RENDER_MARKER_OWNER or payload.get("workspace_id") != workspace.name:
            return None
        return payload

    def _remove_workspace(self, workspace: Path, *, allow_unmarked: bool = False) -> None:
        try:
            input_root = self._root(self.input_root)
            if workspace.is_symlink() or workspace.resolve(strict=False).parent != input_root:
                return
            if not _WORKSPACE_PATTERN.fullmatch(workspace.name):
                return
            marker = self._read_marker(workspace)
            if marker is None and not allow_unmarked:
                return
            _remove_tree(workspace)
            output = self._root(self.output_root) / workspace.name
            if not output.is_symlink() and output.resolve(strict=False).parent == self._root(self.output_root):
                _remove_tree(output)
        except (OSError, OutputRenderError):
            return


def _render_local(spec: OutputRenderSpec) -> tuple[bytes, tuple[str, ...]]:
    if spec.format_id in {"plain_text", "markdown"}:
        return str(spec.content).encode("utf-8"), ()
    if spec.format_id == "json":
        try:
            return (
                json.dumps(spec.content, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8") + b"\n",
                (),
            )
        except (TypeError, ValueError) as exc:
            raise OutputRenderError(422, "output_json_invalid", "The JSON output contains unsupported values.") from exc
    if spec.format_id == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream, lineterminator="\n")
        neutralized = False
        for row in spec.rows:
            rendered: list[Any] = []
            for value in row:
                if isinstance(value, str) and _looks_like_formula(value):
                    rendered.append("'" + value)
                    neutralized = True
                else:
                    rendered.append(value)
            writer.writerow(rendered)
        warnings = ("Spreadsheet-like formulas were neutralized as text.",) if neutralized else ()
        return stream.getvalue().encode("utf-8-sig"), warnings
    raise OutputRenderError(422, "output_render_format_invalid", "This local output format is not supported.")


def _looks_like_formula(value: str) -> bool:
    stripped = value.lstrip()
    if not stripped or stripped[0] not in "=+-@":
        return False
    if stripped[0] == "-":
        try:
            number = float(stripped)
            return not math.isfinite(number)
        except ValueError:
            return True
    if stripped[0] == "+":
        try:
            number = float(stripped)
            return not math.isfinite(number)
        except ValueError:
            return True
    return True


def _cell_count(rows: tuple[tuple[Any, ...], ...]) -> int:
    if any(len(row) > MAX_TABLE_COLUMNS for row in rows):
        raise ValueError("table column limit exceeded")
    return sum(len(row) for row in rows)


def _validate_cell_budget(rows: tuple[tuple[Any, ...], ...]) -> None:
    if _cell_count(rows) > MAX_TABLE_CELLS:
        raise ValueError("table cell limit exceeded")


def _count_strings(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(len(str(key)) + _count_strings(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return sum(_count_strings(item) for item in value)
    return 0


def _assert_finite(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite number")
    if isinstance(value, dict):
        for item in value.values():
            _assert_finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_finite(item)


def _bounded_warnings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value[:20]:
        clean = str(item).strip()[:500]
        if clean and clean not in result:
            result.append(clean)
    return tuple(result)


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o444)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def _remove_tree(root: Path) -> None:
    if not root.exists() or root.is_symlink():
        return
    for current_root, directories, files in os.walk(root, topdown=False, followlinks=False):
        current = Path(current_root)
        try:
            os.chmod(current, 0o700)
        except OSError:
            pass
        for name in files:
            path = current / name
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
            path.unlink(missing_ok=True)
        for name in directories:
            path = current / name
            if path.is_symlink():
                path.unlink(missing_ok=True)
            else:
                try:
                    os.chmod(path, 0o700)
                except OSError:
                    pass
                path.rmdir()
    root.rmdir()


def _run_coroutine_sync(factory: Callable[[], Coroutine[Any, Any, dict[str, Any]]]) -> dict[str, Any]:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    responses: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def runner() -> None:
        try:
            responses.put((True, asyncio.run(factory())))
        except BaseException as exc:
            responses.put((False, exc))

    thread = threading.Thread(target=runner, name="output-renderer-bridge", daemon=True)
    thread.start()
    thread.join()
    ok, value = responses.get()
    if ok:
        return value
    raise value


__all__ = [
    "FileOutputRenderer",
    "OutputRenderError",
    "OutputRenderSidecar",
    "OutputRenderSpec",
    "RenderedOutput",
    "validate_render_spec",
]
