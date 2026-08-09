from __future__ import annotations

import asyncio
import hashlib
import json
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
from typing import TYPE_CHECKING, Any

try:
    from server.mcp.manager import MCPClientManager
except ModuleNotFoundError:
    from mcp.manager import MCPClientManager

if TYPE_CHECKING:
    from .document_parser import ParsedDocument


OFFICE_ADAPTER_ID = "office-parser-mcp"
OFFICE_TOOL_NAME = "extract_office_document"
OFFICE_OPERATION_TIMEOUT_SECONDS = 30.0
OFFICE_RESULT_MAX_BYTES = 2 * 1024 * 1024
OFFICE_SOURCE_MAX_BYTES = 10 * 1024 * 1024
OFFICE_ORPHAN_TTL_SECONDS = 30 * 60
OFFICE_MARKER_NAME = ".modelmirror-office-parser.json"
OFFICE_MARKER_OWNER = "modelmirror.file_assets.office_sidecar.v1"
_WORKSPACE_PATTERN = re.compile(r"^mcpws_[0-9a-f]{32}$")
_FORMAT_SUFFIX = {"docx": ".docx", "pptx": ".pptx"}


class OfficeSidecarError(RuntimeError):
    """Stable, redacted Office parser bridge failure."""

    def __init__(self, status_code: int, error_code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


class OfficeSidecarParser:
    """Stage one validated Office blob and parse it in the network-free sidecar."""

    def __init__(
        self,
        *,
        input_root: str | Path | None = None,
        manager_factory: Callable[[], Any] | None = None,
        operation_timeout: float = OFFICE_OPERATION_TIMEOUT_SECONDS,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.input_root = Path(
            input_root
            or os.getenv("MCP_FILE_INPUT_ROOT", "").strip()
            or Path(__file__).resolve().parents[1] / "mcp-file-inputs"
        )
        self.manager_factory = manager_factory or self._default_manager
        # Production uses the locked 30-second ceiling.  A smaller injectable
        # value keeps timeout behavior testable without weakening that default.
        self.operation_timeout = max(0.01, min(float(operation_timeout), 30.0))
        self.now = now
        self.cleanup_orphans()

    @staticmethod
    def _default_manager() -> MCPClientManager:
        return MCPClientManager(
            operation_timeout=OFFICE_OPERATION_TIMEOUT_SECONDS,
            idle_timeout_seconds=60,
        )

    def parse(
        self,
        path: str | Path,
        *,
        format_id: str,
        title: str | None,
    ) -> "ParsedDocument":
        clean_format = str(format_id or "").strip().lower()
        if clean_format not in _FORMAT_SUFFIX:
            raise OfficeSidecarError(
                422,
                "office_format_not_supported",
                "当前隔离解析器只接受 DOCX 或 PPTX。",
            )
        self.cleanup_orphans()
        workspace: Path | None = None
        try:
            workspace, workspace_id, file_id = self._stage_source(
                Path(path),
                format_id=clean_format,
            )
            parsed = _run_coroutine_sync(
                lambda: self._call_sidecar(
                    workspace_id=workspace_id,
                    file_id=file_id,
                    format_id=clean_format,
                )
            )
            return parsed.model_copy(update={"title": title or None})
        finally:
            if workspace is not None:
                self._remove_owned_workspace(workspace)

    def cleanup_orphans(self) -> tuple[str, ...]:
        root = self._resolved_input_root()
        now = self.now()
        removed: list[str] = []
        try:
            candidates = tuple(root.iterdir())
        except OSError as exc:
            raise OfficeSidecarError(
                503,
                "office_parser_storage_unavailable",
                "Office 隔离解析暂不可用，请稍后重试。",
            ) from exc
        for candidate in candidates:
            if (
                not _WORKSPACE_PATTERN.fullmatch(candidate.name)
                or candidate.is_symlink()
                or not candidate.is_dir()
            ):
                continue
            marker = self._read_owned_marker(candidate)
            if marker is None:
                continue
            try:
                marker_mtime = (candidate / OFFICE_MARKER_NAME).stat(
                    follow_symlinks=False
                ).st_mtime
                created_at = float(marker.get("created_at") or marker_mtime)
            except (OSError, TypeError, ValueError):
                continue
            if now - max(marker_mtime, created_at) <= OFFICE_ORPHAN_TTL_SECONDS:
                continue
            self._remove_owned_workspace(candidate)
            if not candidate.exists():
                removed.append(candidate.name)
        return tuple(sorted(removed))

    def _resolved_input_root(self) -> Path:
        try:
            self.input_root.mkdir(parents=True, exist_ok=True)
            if self.input_root.is_symlink() or not self.input_root.is_dir():
                raise OSError("not a regular directory")
            return self.input_root.resolve(strict=True)
        except OSError as exc:
            raise OfficeSidecarError(
                503,
                "office_parser_storage_unavailable",
                "Office 隔离解析暂不可用，请稍后重试。",
            ) from exc

    def _stage_source(
        self,
        source: Path,
        *,
        format_id: str,
    ) -> tuple[Path, str, str]:
        try:
            if source.is_symlink() or not source.is_file():
                raise OfficeSidecarError(
                    422,
                    "office_source_unavailable",
                    "Office 原件无法安全读取，请重新上传。",
                )
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(source, flags)
            try:
                source_stat = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(source_stat.st_mode)
                    or source_stat.st_size <= 0
                    or source_stat.st_size > OFFICE_SOURCE_MAX_BYTES
                ):
                    raise OfficeSidecarError(
                        422,
                        "office_source_size_invalid",
                        "Office 原件为空或超过当前 10 MiB 上限。",
                    )
                chunks: list[bytes] = []
                received = 0
                while received <= OFFICE_SOURCE_MAX_BYTES:
                    chunk = os.read(
                        descriptor,
                        min(1024 * 1024, OFFICE_SOURCE_MAX_BYTES + 1 - received),
                    )
                    if not chunk:
                        break
                    chunks.append(chunk)
                    received += len(chunk)
                content = b"".join(chunks)
                if (
                    not content
                    or len(content) > OFFICE_SOURCE_MAX_BYTES
                    or len(content) != source_stat.st_size
                ):
                    raise OfficeSidecarError(
                        422,
                        "office_source_size_invalid",
                        "Office 原件为空、读取中发生变化或超过当前 10 MiB 上限。",
                    )
            finally:
                os.close(descriptor)
        except OfficeSidecarError:
            raise
        except OSError as exc:
            raise OfficeSidecarError(
                422,
                "office_source_unavailable",
                "Office 原件无法安全读取，请重新上传。",
            ) from exc
        root = self._resolved_input_root()
        workspace_id = f"mcpws_{uuid.uuid4().hex}"
        workspace = root / workspace_id
        source_name = f"source{_FORMAT_SUFFIX[format_id]}"
        sha256 = hashlib.sha256(content).hexdigest()
        try:
            workspace.mkdir(mode=0o755, exist_ok=False)
            marker = {
                "owner": OFFICE_MARKER_OWNER,
                "workspace_id": workspace_id,
                "created_at": self.now(),
                "source_name": source_name,
                "source_sha256": sha256,
                "format_id": format_id,
            }
            self._atomic_write(
                workspace / OFFICE_MARKER_NAME,
                json.dumps(
                    marker,
                    ensure_ascii=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
            target = workspace / source_name
            self._atomic_write(target, content)
            if (
                target.is_symlink()
                or target.stat(follow_symlinks=False).st_size != len(content)
                or hashlib.sha256(target.read_bytes()).hexdigest() != sha256
            ):
                raise OSError("staged source digest mismatch")
            os.chmod(workspace, 0o555)
        except Exception as exc:
            self._remove_owned_workspace(workspace, allow_unmarked=True)
            if isinstance(exc, OfficeSidecarError):
                raise
            raise OfficeSidecarError(
                503,
                "office_parser_staging_failed",
                "Office 原件未能安全送入隔离解析器，请稍后重试。",
            ) from exc

        file_id = "mcpf_" + hashlib.sha256(
            f"{workspace_id}:{source_name}".encode("utf-8")
        ).hexdigest()[:24]
        return workspace, workspace_id, file_id

    @staticmethod
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

    async def _call_sidecar(
        self,
        *,
        workspace_id: str,
        file_id: str,
        format_id: str,
    ) -> "ParsedDocument":
        manager = self.manager_factory()
        session_id: str | None = None
        proxy = Path(__file__).resolve().parents[1] / "mcp" / "file_proxy.py"
        environment = {"MCP_FILE_WORKSPACE_ID": workspace_id}
        socket_path = os.getenv("MCP_FILES_SOCKET_PATH", "").strip()
        if socket_path:
            environment["MCP_FILES_SOCKET_PATH"] = socket_path
        profile = {
            "transport": "stdio",
            "server_command": [
                sys.executable,
                str(proxy),
                OFFICE_ADAPTER_ID,
            ],
            "environment": environment,
            "network_policy": "catalog-files-none",
            "reconnect_attempts": 0,
            "operation_timeout": self.operation_timeout,
        }
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.operation_timeout

        async def before_deadline(
            factory: Callable[[], Coroutine[Any, Any, Any]],
        ) -> Any:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            return await asyncio.wait_for(factory(), timeout=remaining)

        try:
            session_id = await before_deadline(
                lambda: manager.connect_profile(**profile)
            )
            result = await before_deadline(
                lambda: manager.call_tool(
                    session_id, OFFICE_TOOL_NAME, {"file_id": file_id}
                )
            )
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise OfficeSidecarError(
                503,
                "office_parser_timeout",
                "Office 隔离解析超时，请拆分文档后重试。",
            ) from exc
        except OfficeSidecarError:
            raise
        except Exception as exc:
            raise OfficeSidecarError(
                503,
                "office_parser_unavailable",
                "Office 隔离解析暂不可用，请稍后重试。",
            ) from exc
        finally:
            if session_id is not None and deadline > loop.time():
                try:
                    await before_deadline(lambda: manager.disconnect(session_id))
                except Exception:
                    pass
        return _validate_structured_result(result, format_id=format_id)

    def _read_owned_marker(self, workspace: Path) -> dict[str, Any] | None:
        marker_path = workspace / OFFICE_MARKER_NAME
        try:
            if marker_path.is_symlink() or not marker_path.is_file():
                return None
            raw = marker_path.read_bytes()
            if len(raw) > 4 * 1024:
                return None
            payload = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("owner") != OFFICE_MARKER_OWNER
            or payload.get("workspace_id") != workspace.name
        ):
            return None
        return payload

    def _remove_owned_workspace(
        self,
        workspace: Path,
        *,
        allow_unmarked: bool = False,
    ) -> None:
        try:
            root = self._resolved_input_root()
            marker_path = workspace / OFFICE_MARKER_NAME
            marker = self._read_owned_marker(workspace)
            if (
                workspace.is_symlink()
                or workspace.resolve(strict=False).parent != root
                or not _WORKSPACE_PATTERN.fullmatch(workspace.name)
                or (
                    marker is None
                    and (
                        not allow_unmarked
                        or marker_path.exists()
                    )
                )
            ):
                return
            for current_root, directories, files in os.walk(
                workspace,
                topdown=False,
                followlinks=False,
            ):
                current = Path(current_root)
                # Inputs are intentionally staged under 0555 directories.
                # Restore owner-write before unlinking children so cleanup
                # also works for the cap-drop API container without relying
                # on DAC override capabilities.
                try:
                    os.chmod(current, 0o700)
                except OSError:
                    pass
                for name in files:
                    candidate = current / name
                    try:
                        os.chmod(candidate, stat.S_IWUSR | stat.S_IRUSR)
                    except OSError:
                        pass
                    candidate.unlink(missing_ok=True)
                for name in directories:
                    candidate = current / name
                    if candidate.is_symlink():
                        candidate.unlink(missing_ok=True)
                    else:
                        try:
                            os.chmod(candidate, 0o700)
                        except OSError:
                            pass
                        candidate.rmdir()
            try:
                os.chmod(workspace, 0o700)
            except OSError:
                pass
            workspace.rmdir()
        except (OSError, OfficeSidecarError):
            # The owned marker allows the next startup/parse sweep to retry.
            return


def _validate_structured_result(result: Any, *, format_id: str) -> "ParsedDocument":
    if bool(getattr(result, "isError", False)):
        raise OfficeSidecarError(
            422,
            "office_parse_failed",
            "Office 内容无法安全提取，请重新导出后再试。",
        )
    payload = getattr(result, "structuredContent", None)
    if not isinstance(payload, dict):
        raise OfficeSidecarError(
            422,
            "office_parser_invalid_output",
            "Office 隔离解析结果无效，请重新导出后再试。",
        )
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise OfficeSidecarError(
            422,
            "office_parser_invalid_output",
            "Office 隔离解析结果无效，请重新导出后再试。",
        ) from exc
    if len(encoded) > OFFICE_RESULT_MAX_BYTES:
        raise OfficeSidecarError(
            422,
            "office_parser_output_too_large",
            "Office 隔离解析结果超过安全上限，请拆分文档后重试。",
        )

    try:
        from .document_parser import ParsedDocument, ParsedSection

        allowed_root = set(ParsedDocument.model_fields)
        required_root = {
            "format",
            "sections",
            "warnings",
            "extracted_chars",
            "truncated",
        }
        if not required_root.issubset(payload) or not set(payload).issubset(
            allowed_root
        ):
            raise ValueError("invalid root shape")
        raw_sections = payload.get("sections")
        if not isinstance(raw_sections, list) or not raw_sections:
            raise ValueError("invalid sections")
        allowed_section = set(ParsedSection.model_fields)
        for section in raw_sections:
            if (
                not isinstance(section, dict)
                or "text" not in section
                or not set(section).issubset(allowed_section)
            ):
                raise ValueError("invalid section shape")
        parsed = ParsedDocument.model_validate(payload)
        retained_chars = sum(len(section.text) for section in parsed.sections)
        if (
            parsed.format != format_id
            or not parsed.sections
            or parsed.extracted_chars < retained_chars
        ):
            raise ValueError("invalid document contract")
        return parsed
    except OfficeSidecarError:
        raise
    except Exception as exc:
        raise OfficeSidecarError(
            422,
            "office_parser_invalid_output",
            "Office 隔离解析结果无效，请重新导出后再试。",
        ) from exc


def _run_coroutine_sync(
    factory: Callable[[], Coroutine[Any, Any, "ParsedDocument"]],
) -> "ParsedDocument":
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    responses: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def runner() -> None:
        try:
            responses.put((True, asyncio.run(factory())))
        except BaseException as exc:  # relay without leaking details.
            responses.put((False, exc))

    thread = threading.Thread(target=runner, name="office-sidecar-bridge", daemon=True)
    thread.start()
    thread.join()
    ok, value = responses.get()
    if ok:
        return value
    raise value


__all__ = [
    "OFFICE_ADAPTER_ID",
    "OFFICE_TOOL_NAME",
    "OfficeSidecarError",
    "OfficeSidecarParser",
]
