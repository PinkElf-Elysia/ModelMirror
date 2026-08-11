from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import yaml

try:
    from server.agent_workspace.tools import BuiltinToolRunner, ToolExecutionError, ToolResult
except ImportError:  # pragma: no cover - container package layout
    from agent_workspace.tools import BuiltinToolRunner, ToolExecutionError, ToolResult


MAX_SHADOW_FILE_BYTES = 8 * 1024 * 1024
SHADOW_CANDIDATE_ENTRYPOINT = Path("index.html")
GOAL_RELATIVE_PATH = Path(".modelmirror") / "GOAL.yaml"
GOAL_STATUSES = frozenset({"active", "complete", "blocked"})


SHADOW_TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file inside the isolated Shadow Workspace.",
        "permission": "r",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "file_path": {"type": "string"},
                "offset": {"type": "integer", "minimum": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2_000},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "write_file",
        "description": "Atomically write a UTF-8 text file inside the isolated Shadow Workspace.",
        "permission": "rw",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace exact text inside a UTF-8 Shadow Workspace file.",
        "permission": "rw",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "file_path": {"type": "string"},
                "old_string": {"type": "string", "minLength": 1},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
)


class UpstreamShadowToolBridge:
    """R3R-1 file bridge. No command, network, image or sub-agent capability."""

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        workspace: Path,
    ) -> ToolResult:
        if tool_name not in {"read_file", "write_file", "edit_file"}:
            raise ToolExecutionError(f"Shadow tool is not allowed: {tool_name}")
        if tool_name == "read_file":
            return await self._read_file(arguments, workspace)
        if tool_name == "write_file":
            return await self._write_file(arguments, workspace)
        return await self._edit_file(arguments, workspace)

    async def _read_file(self, args: dict[str, Any], workspace: Path) -> ToolResult:
        self._reject_symbolic_link_chain(workspace, args.get("file_path"))
        path = BuiltinToolRunner.resolve_read(workspace, args.get("file_path"))
        if not path.is_file() or path.is_symlink():
            raise ToolExecutionError("file_path is not a safe file")
        if path.stat().st_size > MAX_SHADOW_FILE_BYTES:
            raise ToolExecutionError("Shadow file exceeds the size limit")
        text = path.read_text(encoding="utf-8", errors="strict")
        lines = text.splitlines()
        offset = max(1, int(args.get("offset") or 1))
        limit = max(1, min(int(args.get("limit") or 2_000), 2_000))
        selected = lines[offset - 1 : offset - 1 + limit]
        return ToolResult(
            output="\n".join(
                f"{index}: {line}"
                for index, line in enumerate(selected, start=offset)
            ),
            metadata={
                "path": path.relative_to(workspace.resolve()).as_posix(),
                "line_count": len(lines),
            },
        )

    async def _write_file(self, args: dict[str, Any], workspace: Path) -> ToolResult:
        self._reject_symbolic_link_chain(workspace, args.get("file_path"))
        path = BuiltinToolRunner.resolve_write(workspace, args.get("file_path"))
        content = str(args.get("content") or "")
        self._validate_write(workspace, path, content)
        self._atomic_write(path, content)
        return ToolResult(
            output=json.dumps(
                {
                    "path": path.relative_to(workspace.resolve()).as_posix(),
                    "bytes": len(content.encode("utf-8")),
                },
                ensure_ascii=False,
            )
        )

    async def _edit_file(self, args: dict[str, Any], workspace: Path) -> ToolResult:
        self._reject_symbolic_link_chain(workspace, args.get("file_path"))
        path = BuiltinToolRunner.resolve_read(workspace, args.get("file_path"))
        if not path.is_file() or path.is_symlink():
            raise ToolExecutionError("file_path is not a safe file")
        old_text = str(args.get("old_string") or "")
        new_text = str(args.get("new_string") or "")
        if not old_text:
            raise ToolExecutionError("old_string cannot be empty")
        text = path.read_text(encoding="utf-8", errors="strict")
        occurrences = text.count(old_text)
        if not occurrences:
            raise ToolExecutionError("old_string was not found")
        replace_all = bool(args.get("replace_all", False))
        if occurrences > 1 and not replace_all:
            raise ToolExecutionError("old_string is not unique; set replace_all=true")
        updated = (
            text.replace(old_text, new_text)
            if replace_all
            else text.replace(old_text, new_text, 1)
        )
        self._validate_write(workspace, path, updated)
        self._atomic_write(path, updated)
        return ToolResult(
            output=json.dumps(
                {
                    "path": path.relative_to(workspace.resolve()).as_posix(),
                    "replacements": occurrences if replace_all else 1,
                },
                ensure_ascii=False,
            )
        )

    @classmethod
    def _validate_write(cls, workspace: Path, path: Path, content: str) -> None:
        if len(content.encode("utf-8")) > MAX_SHADOW_FILE_BYTES:
            raise ToolExecutionError("Shadow file exceeds the size limit")
        relative = path.relative_to(workspace.resolve())
        if relative.parts and relative.parts[0] == ".modelmirror":
            if relative != GOAL_RELATIVE_PATH:
                raise ToolExecutionError(".modelmirror control files are read-only")
            cls._validate_goal_mutation(path, content)

    @staticmethod
    def _validate_goal_mutation(path: Path, content: str) -> None:
        if not path.exists():
            raise ToolExecutionError("GOAL.yaml is host-owned and cannot be created by tools")
        try:
            before = yaml.safe_load(path.read_text(encoding="utf-8"))
            after = yaml.safe_load(content)
        except (UnicodeError, yaml.YAMLError) as exc:
            raise ToolExecutionError("GOAL.yaml must remain valid UTF-8 YAML") from exc
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise ToolExecutionError("GOAL.yaml must remain a mapping")
        if set(before) != set(after):
            raise ToolExecutionError("GOAL.yaml fields are host-owned")
        changed = {key for key in before if before[key] != after[key]}
        if changed - {"status"}:
            raise ToolExecutionError("Only GOAL.yaml status may be changed")
        if str(after.get("status") or "") not in GOAL_STATUSES:
            raise ToolExecutionError("GOAL.yaml status is invalid")

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _reject_symbolic_link_chain(workspace: Path, value: Any) -> None:
        raw = str(value or "").strip().replace("\\", "/")
        relative = Path(raw)
        if (
            not raw
            or "\x00" in raw
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ToolExecutionError("file_path must be a safe Workspace-relative path")
        current = workspace.resolve(strict=True)
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ToolExecutionError("Shadow Workspace symbolic links are forbidden")


def compute_shadow_candidate_sha256(workspace: Path) -> str:
    root = workspace.resolve(strict=True)
    path = root / SHADOW_CANDIDATE_ENTRYPOINT
    if not path.is_file() or path.is_symlink():
        raise ToolExecutionError("Shadow run did not produce a safe index.html candidate")
    data = path.read_bytes()
    if len(data) > MAX_SHADOW_FILE_BYTES:
        raise ToolExecutionError("Shadow candidate file exceeds the size limit")

    digest = hashlib.sha256()
    relative = SHADOW_CANDIDATE_ENTRYPOINT.as_posix().encode("utf-8")
    digest.update(len(relative).to_bytes(4, "big"))
    digest.update(relative)
    digest.update(len(data).to_bytes(8, "big"))
    digest.update(data)
    return digest.hexdigest()


def validate_shadow_goal_control(
    workspace: Path,
    *,
    expected_objective: str,
    required_status: str | None = None,
) -> None:
    control = workspace.resolve(strict=True) / GOAL_RELATIVE_PATH
    if not control.is_file() or control.is_symlink():
        raise ToolExecutionError("Shadow GOAL.yaml is missing or unsafe")
    if control.stat().st_size > MAX_SHADOW_FILE_BYTES:
        raise ToolExecutionError("Shadow GOAL.yaml exceeds the size limit")
    try:
        payload = yaml.safe_load(control.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ToolExecutionError("Shadow GOAL.yaml is not valid UTF-8 YAML") from exc
    if not isinstance(payload, dict):
        raise ToolExecutionError("Shadow GOAL.yaml must remain a mapping")
    if set(payload) - {"objective", "status"}:
        raise ToolExecutionError("Shadow GOAL.yaml contains unknown fields")
    if payload.get("objective") != expected_objective:
        raise ToolExecutionError("Shadow GOAL.yaml objective was modified")
    status = str(payload.get("status") or "")
    if status not in GOAL_STATUSES:
        raise ToolExecutionError("Shadow GOAL.yaml status is invalid")
    if required_status is not None and status != required_status:
        raise ToolExecutionError(
            f"Shadow GOAL.yaml must have status={required_status} before candidate handoff"
        )
