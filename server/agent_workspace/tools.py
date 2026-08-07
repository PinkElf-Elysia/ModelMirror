from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import shutil
import signal
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

try:
    import pwd
except ImportError:  # pragma: no cover - Windows keeps command tools unavailable.
    pwd = None  # type: ignore[assignment]

from .gateway import OpenAICompatibleGateway


MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_PROCESS_BUFFER = 256_000
AGENT_TOOL_USER = "agenttool"


class ToolExecutionError(RuntimeError):
    pass


class SubagentController(Protocol):
    async def run_subagent_tool(
        self, *, session_id: str, workspace: Path, arguments: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def input_subagent_tool(
        self, *, session_id: str, workspace: Path, arguments: dict[str, Any]
    ) -> dict[str, Any]: ...


@dataclass(slots=True)
class ToolResult:
    output: str
    process_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class _OwnedProcess:
    process_id: str
    session_id: str
    process: asyncio.subprocess.Process
    started_at: float
    output: bytearray = field(default_factory=bytearray)
    read_offset: int = 0
    truncated: bool = False
    reader_task: asyncio.Task[None] | None = None


class ProcessRegistry:
    """In-memory ownership guard for commands started by Agent Workspace."""

    def __init__(
        self,
        *,
        allow_commands: bool | None = None,
        command_prefix: list[str] | None = None,
    ) -> None:
        self.allow_commands = (
            Path("/.dockerenv").exists()
            if allow_commands is None
            else bool(allow_commands)
        )
        self._command_prefix = command_prefix
        self._items: dict[str, _OwnedProcess] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        *,
        session_id: str,
        workspace: Path,
        command: str,
        yield_time_ms: int,
    ) -> ToolResult:
        if not self.allow_commands or os.name != "posix":
            raise ToolExecutionError(
                "命令工具仅在 ModelMirror Linux 容器内可用。"
            )
        clean_command = str(command or "").strip()
        if not clean_command:
            raise ToolExecutionError("command cannot be blank")
        if len(clean_command) > 32_000:
            raise ToolExecutionError("command is too long")
        workspace = workspace.resolve(strict=True)
        prefix = self._effective_prefix()
        environment = self._sanitized_environment(workspace)
        self.prepare_workspace(workspace)
        process = await asyncio.create_subprocess_exec(
            *prefix,
            "/bin/sh",
            "-lc",
            clean_command,
            cwd=str(workspace),
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        process_id = uuid.uuid4().hex
        item = _OwnedProcess(
            process_id=process_id,
            session_id=session_id,
            process=process,
            started_at=time.monotonic(),
        )
        item.reader_task = asyncio.create_task(self._read_output(item))
        async with self._lock:
            self._items[process_id] = item
        try:
            await asyncio.wait_for(
                process.wait(), timeout=max(0.25, min(yield_time_ms / 1000, 30.0))
            )
        except TimeoutError:
            pass
        return await self.poll(session_id=session_id, process_id=process_id)

    async def poll(
        self,
        *,
        session_id: str,
        process_id: str,
        input_text: str | None = None,
        yield_time_ms: int = 250,
    ) -> ToolResult:
        item = await self._owned(session_id, process_id)
        if input_text is not None:
            if item.process.returncode is not None:
                raise ToolExecutionError("process already finished")
            if item.process.stdin is None:
                raise ToolExecutionError("process stdin is unavailable")
            item.process.stdin.write(input_text.encode("utf-8"))
            await item.process.stdin.drain()
        if item.process.returncode is None:
            try:
                await asyncio.wait_for(
                    item.process.wait(),
                    timeout=max(0.25, min(yield_time_ms / 1000, 30.0)),
                )
            except TimeoutError:
                pass
        if item.process.returncode is not None and item.reader_task is not None:
            await asyncio.gather(item.reader_task, return_exceptions=True)
        raw = bytes(item.output[item.read_offset :])
        item.read_offset = len(item.output)
        payload = {
            "process_id": process_id,
            "running": item.process.returncode is None,
            "exit_code": item.process.returncode,
            "output": raw.decode("utf-8", errors="replace"),
            "output_truncated": item.truncated,
            "duration_ms": round((time.monotonic() - item.started_at) * 1000),
        }
        return ToolResult(
            output=json.dumps(payload, ensure_ascii=False),
            process_id=process_id if item.process.returncode is None else None,
            metadata=payload,
        )

    async def terminate_session(self, session_id: str) -> None:
        async with self._lock:
            owned = [item for item in self._items.values() if item.session_id == session_id]
        for item in owned:
            await self._terminate(item)

    async def shutdown(self) -> None:
        async with self._lock:
            items = list(self._items.values())
        for item in items:
            await self._terminate(item)

    async def _owned(self, session_id: str, process_id: str) -> _OwnedProcess:
        async with self._lock:
            item = self._items.get(process_id)
        if item is None or item.session_id != session_id:
            raise ToolExecutionError("process does not belong to this Session")
        return item

    async def _read_output(self, item: _OwnedProcess) -> None:
        stream = item.process.stdout
        if stream is None:
            return
        while True:
            chunk = await stream.read(8_192)
            if not chunk:
                return
            if len(item.output) + len(chunk) > MAX_PROCESS_BUFFER:
                remaining = max(0, MAX_PROCESS_BUFFER - len(item.output))
                item.output.extend(chunk[:remaining])
                item.truncated = True
                continue
            item.output.extend(chunk)

    async def _terminate(self, item: _OwnedProcess) -> None:
        if item.process.returncode is None:
            try:
                os.killpg(item.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(item.process.wait(), timeout=3)
            except TimeoutError:
                try:
                    os.killpg(item.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await item.process.wait()
        if item.reader_task is not None:
            await asyncio.gather(item.reader_task, return_exceptions=True)

    def _effective_prefix(self) -> list[str]:
        if self._command_prefix is not None:
            return list(self._command_prefix)
        try:
            if pwd is None:
                raise KeyError(AGENT_TOOL_USER)
            pwd.getpwnam(AGENT_TOOL_USER)
        except KeyError as exc:
            raise ToolExecutionError("agenttool low-privilege user is unavailable") from exc
        setpriv = shutil.which("setpriv")
        if not setpriv:
            raise ToolExecutionError("setpriv is unavailable in the server container")
        return [
            setpriv,
            f"--reuid={AGENT_TOOL_USER}",
            f"--regid={AGENT_TOOL_USER}",
            "--init-groups",
            "--reset-env",
            "--",
        ]

    @staticmethod
    def _sanitized_environment(workspace: Path) -> dict[str, str]:
        home = workspace / ".modelmirror" / "home"
        temporary = workspace / ".modelmirror" / "tmp"
        home.mkdir(parents=True, exist_ok=True)
        temporary.mkdir(parents=True, exist_ok=True)
        return {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(home),
            "TMPDIR": str(temporary),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PYTHONDONTWRITEBYTECODE": "1",
            "NO_PROXY": "*",
            "no_proxy": "*",
        }

    @staticmethod
    def prepare_workspace(workspace: Path) -> None:
        if os.name != "posix" or pwd is None or os.geteuid() != 0:
            return
        try:
            account = pwd.getpwnam(AGENT_TOOL_USER)
        except KeyError:
            return
        for root, directories, files in os.walk(workspace):
            os.chown(root, account.pw_uid, account.pw_gid)
            for name in directories:
                os.chown(Path(root) / name, account.pw_uid, account.pw_gid)
            for name in files:
                os.chown(Path(root) / name, account.pw_uid, account.pw_gid)


class BuiltinToolRunner:
    def __init__(
        self,
        *,
        gateway: OpenAICompatibleGateway,
        process_registry: ProcessRegistry | None = None,
        subagents: SubagentController | None = None,
    ) -> None:
        self.gateway = gateway
        self.process_registry = process_registry or ProcessRegistry()
        self.subagents = subagents

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        session_id: str,
        workspace: Path,
        timeout_ms: int,
        max_output_length: int,
    ) -> ToolResult:
        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            raise ToolExecutionError(f"unsupported built-in tool: {tool_name}")
        try:
            result = await asyncio.wait_for(
                handler(session_id, workspace, arguments, max_output_length),
                timeout=max(1, timeout_ms) / 1000,
            )
        except TimeoutError as exc:
            raise ToolExecutionError(f"{tool_name} timed out") from exc
        if len(result.output) > max_output_length:
            result.output = result.output[:max_output_length] + "\n[output truncated]"
            result.metadata["output_truncated"] = True
        return result

    async def _tool_read_file(
        self, _session_id: str, workspace: Path, args: dict[str, Any], _limit: int
    ) -> ToolResult:
        path = self.resolve_read(workspace, args.get("file_path"))
        if not path.is_file():
            raise ToolExecutionError("file_path is not a file")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ToolExecutionError("file is too large")
        text = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
        lines = text.splitlines()
        offset = max(1, int(args.get("offset") or 1))
        limit = max(1, min(int(args.get("limit") or 2000), 2000))
        selected = lines[offset - 1 : offset - 1 + limit]
        output = "\n".join(
            f"{index}: {line}" for index, line in enumerate(selected, start=offset)
        )
        return ToolResult(
            output=output,
            metadata={"path": self.relative(workspace, path), "line_count": len(lines)},
        )

    async def _tool_write_file(
        self, _session_id: str, workspace: Path, args: dict[str, Any], _limit: int
    ) -> ToolResult:
        path = self.resolve_write(workspace, args.get("file_path"))
        content = str(args.get("content") or "")
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ToolExecutionError("content is too large")
        await asyncio.to_thread(self.atomic_write, path, content)
        return ToolResult(
            output=json.dumps(
                {"path": self.relative(workspace, path), "bytes": len(content.encode("utf-8"))},
                ensure_ascii=False,
            )
        )

    async def _tool_edit_file(
        self, _session_id: str, workspace: Path, args: dict[str, Any], _limit: int
    ) -> ToolResult:
        path = self.resolve_read(workspace, args.get("file_path"))
        old_text = str(args.get("old_text") or "")
        new_text = str(args.get("new_text") or "")
        if not old_text:
            raise ToolExecutionError("old_text cannot be empty")
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        occurrences = text.count(old_text)
        if occurrences == 0:
            raise ToolExecutionError("old_text was not found")
        replace_all = bool(args.get("replace_all", False))
        if occurrences > 1 and not replace_all:
            raise ToolExecutionError("old_text is not unique; set replace_all=true")
        updated = text.replace(old_text, new_text) if replace_all else text.replace(old_text, new_text, 1)
        await asyncio.to_thread(self.atomic_write, path, updated)
        return ToolResult(
            output=json.dumps(
                {"path": self.relative(workspace, path), "replacements": occurrences if replace_all else 1},
                ensure_ascii=False,
            )
        )

    async def _tool_exec_command(
        self, session_id: str, workspace: Path, args: dict[str, Any], _limit: int
    ) -> ToolResult:
        return await self.process_registry.start(
            session_id=session_id,
            workspace=workspace,
            command=str(args.get("command") or ""),
            yield_time_ms=int(args.get("yield_time_ms") or 1_000),
        )

    async def _tool_input_command(
        self, session_id: str, _workspace: Path, args: dict[str, Any], _limit: int
    ) -> ToolResult:
        input_text = args.get("input")
        return await self.process_registry.poll(
            session_id=session_id,
            process_id=str(args.get("process_id") or ""),
            input_text=str(input_text) if input_text is not None else None,
            yield_time_ms=int(args.get("yield_time_ms") or 250),
        )

    async def _tool_run_subagent(
        self, session_id: str, workspace: Path, args: dict[str, Any], _limit: int
    ) -> ToolResult:
        if self.subagents is None:
            raise ToolExecutionError("sub-Agent runtime is unavailable")
        payload = await self.subagents.run_subagent_tool(
            session_id=session_id, workspace=workspace, arguments=args
        )
        return ToolResult(output=json.dumps(payload, ensure_ascii=False))

    async def _tool_input_subagent(
        self, session_id: str, workspace: Path, args: dict[str, Any], _limit: int
    ) -> ToolResult:
        if self.subagents is None:
            raise ToolExecutionError("sub-Agent runtime is unavailable")
        payload = await self.subagents.input_subagent_tool(
            session_id=session_id, workspace=workspace, arguments=args
        )
        return ToolResult(output=json.dumps(payload, ensure_ascii=False))

    async def _tool_read_image(
        self, _session_id: str, workspace: Path, args: dict[str, Any], limit: int
    ) -> ToolResult:
        path, data_url, metadata = await asyncio.to_thread(
            self._load_image, workspace, args.get("file_path")
        )
        payload = {**metadata, "data_url": data_url}
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) > limit:
            payload.pop("data_url")
            payload["data_url_omitted"] = True
            payload["next_step"] = "Use describe_image for semantic inspection."
            encoded = json.dumps(payload, ensure_ascii=False)
        return ToolResult(
            output=encoded,
            metadata={**metadata, "path": self.relative(workspace, path)},
        )

    async def _tool_describe_image(
        self, _session_id: str, workspace: Path, args: dict[str, Any], _limit: int
    ) -> ToolResult:
        path, data_url, metadata = await asyncio.to_thread(
            self._load_image, workspace, args.get("file_path")
        )
        description = await self.gateway.describe_image(
            image_path=path,
            data_url=data_url,
            prompt=str(args.get("prompt") or "请准确描述这张图片。"),
            timeout_ms=90_000,
        )
        return ToolResult(output=description, metadata=metadata)

    @classmethod
    def resolve_read(cls, workspace: Path, value: Any) -> Path:
        root = workspace.resolve(strict=True)
        relative = cls._relative_path(value)
        try:
            candidate = (root / relative).resolve(strict=True)
        except FileNotFoundError as exc:
            raise ToolExecutionError("Workspace path does not exist") from exc
        cls._require_contained(root, candidate)
        return candidate

    @classmethod
    def resolve_write(cls, workspace: Path, value: Any) -> Path:
        root = workspace.resolve(strict=True)
        relative = cls._relative_path(value)
        candidate = root / relative
        current = root
        for part in relative.parts[:-1]:
            current = current / part
            if current.exists():
                cls._require_contained(root, current.resolve(strict=True))
            else:
                current.mkdir()
        parent = candidate.parent.resolve(strict=True)
        cls._require_contained(root, parent)
        if candidate.exists():
            cls._require_contained(root, candidate.resolve(strict=True))
        return candidate

    @staticmethod
    def _relative_path(value: Any) -> Path:
        raw = str(value or "").strip().replace("\\", "/")
        if not raw or "\x00" in raw:
            raise ToolExecutionError("file_path is required")
        candidate = Path(raw)
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            raise ToolExecutionError("file_path must be a safe Workspace-relative path")
        return candidate

    @staticmethod
    def _require_contained(root: Path, candidate: Path) -> None:
        if candidate != root and root not in candidate.parents:
            raise ToolExecutionError("Workspace path escape is forbidden")

    @staticmethod
    def atomic_write(path: Path, content: str) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass

    @classmethod
    def _load_image(
        cls, workspace: Path, value: Any
    ) -> tuple[Path, str, dict[str, Any]]:
        path = cls.resolve_read(workspace, value)
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ToolExecutionError("image is too large")
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                width, height = image.size
                image_format = str(image.format or "").lower()
        except (OSError, ValueError) as exc:
            raise ToolExecutionError("file is not a valid image") from exc
        raw = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or f"image/{image_format or 'png'}"
        metadata = {
            "path": cls.relative(workspace, path),
            "mime_type": mime,
            "width": width,
            "height": height,
            "bytes": size,
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        return path, f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}", metadata

    @staticmethod
    def relative(workspace: Path, path: Path) -> str:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
