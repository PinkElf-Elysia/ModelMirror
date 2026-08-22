from __future__ import annotations

import hashlib
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
import uuid
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field, model_validator

from .broker_rpc import BrokerRPCClient
from .contracts import StrictModel


_DIGEST = r"^[a-f0-9]{64}$"


class BrokerPlanItem(StrictModel):
    step: str = Field(min_length=1, max_length=4096)
    status: Literal["pending", "in_progress", "completed"] = "pending"


class BrokerWriteChange(StrictModel):
    kind: Literal["write"]
    path: str = Field(min_length=1, max_length=1024)
    expected_sha256: str | None = Field(default=None, pattern=_DIGEST)
    expected_absent: bool = False
    content: str

    @model_validator(mode="after")
    def exact_preimage(self) -> "BrokerWriteChange":
        if self.expected_absent == (self.expected_sha256 is not None):
            raise ValueError("write requires one exact preimage condition")
        return self


class BrokerDeleteChange(StrictModel):
    kind: Literal["delete"]
    path: str = Field(min_length=1, max_length=1024)
    expected_sha256: str = Field(pattern=_DIGEST)


class BrokerMoveChange(StrictModel):
    kind: Literal["move"]
    path: str = Field(min_length=1, max_length=1024)
    destination: str = Field(min_length=1, max_length=1024)
    expected_sha256: str = Field(pattern=_DIGEST)
    destination_expected_absent: Literal[True] = True


class BrokerPatchChange(StrictModel):
    kind: Literal["patch"]
    path: str = Field(min_length=1, max_length=1024)
    expected_sha256: str = Field(pattern=_DIGEST)
    patch: str = Field(min_length=1)


class BrokerReplaceChange(StrictModel):
    """Replace one unique UTF-8 fragment while preserving all other bytes."""

    kind: Literal["replace"]
    path: str = Field(min_length=1, max_length=1024)
    expected_sha256: str = Field(pattern=_DIGEST)
    old_text: str = Field(min_length=1, max_length=32 * 1024 * 1024)
    new_text: str = Field(max_length=32 * 1024 * 1024)


BrokerChange = Annotated[
    BrokerWriteChange
    | BrokerDeleteChange
    | BrokerMoveChange
    | BrokerPatchChange
    | BrokerReplaceChange,
    Field(discriminator="kind"),
]


def _replace_change_as_write(
    change: BrokerReplaceChange, *, workspace: Path | None = None
) -> dict[str, Any]:
    """Resolve a unique replace inside the trusted adapter.

    The model supplies the exact tree and file preimage bindings. This adapter
    reads only the current task workspace and converts the focused replacement
    into the private write contract; the Broker still performs the authoritative
    tree/preimage CAS before publishing the batch.
    """
    relative = PurePosixPath(change.path)
    if (
        relative.is_absolute()
        or relative.as_posix() != change.path
        or any(part in {"", ".", ".."} for part in relative.parts)
        or relative.parts[0] == ".git"
        or "\\" in change.path
        or "\x00" in change.path
    ):
        raise ValueError("replace path must be a canonical workspace-relative path")
    root = (workspace or Path.cwd()).resolve(strict=True)
    lexical = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        metadata = current.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("replace path cannot traverse a symbolic link")
    candidate = lexical.resolve(strict=True)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("replace path escapes the task workspace") from exc
    metadata = candidate.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 32 * 1024 * 1024:
        raise ValueError("replace target must be a bounded regular file")
    raw = candidate.read_bytes()
    if hashlib.sha256(raw).hexdigest() != change.expected_sha256:
        raise ValueError("replace target no longer matches expected_sha256")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("replace target must be UTF-8 text") from exc
    if text.count(change.old_text) != 1:
        raise ValueError("old_text must occur exactly once")
    updated = text.replace(change.old_text, change.new_text, 1)
    return {
        "kind": "write",
        "path": change.path,
        "expected_sha256": change.expected_sha256,
        "expected_absent": False,
        "content": updated,
        "content_sha256": hashlib.sha256(updated.encode("utf-8")).hexdigest(),
    }


def _workspace_relative_shell_script(
    script: str, *, workspace: Path | None = None
) -> str:
    """Replace only exact references to the current task root with ``.``.

    Providers run inside the task workspace and can therefore observe its private
    process path. The Executor intentionally runs shell scripts in a separate
    operation-owned clone. Carrying the provider path into that clone would either
    escape the clone or be rejected by its sandbox. Other absolute paths remain
    unchanged and are rejected by the normal Broker/Executor policy.
    """
    root = (workspace or Path.cwd()).resolve(strict=True)
    values = {str(root), root.as_posix()}
    normalized = script
    for value in sorted(values, key=len, reverse=True):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_.-]){re.escape(value)}(?=$|[/\\\s'\";&|()<>])"
        )
        normalized = pattern.sub(".", normalized)
    return normalized


def _workspace_relative_cwd(value: str, *, workspace: Path | None = None) -> str:
    root = (workspace or Path.cwd()).resolve(strict=True)
    for spelling in {str(root), root.as_posix()}:
        if value == spelling:
            return "."
        prefix = spelling.rstrip("/\\")
        if value.startswith(prefix + "/") or value.startswith(prefix + "\\"):
            relative = value[len(prefix) + 1 :].replace("\\", "/")
            return relative or "."
    return value


def build_server(client: BrokerRPCClient) -> FastMCP:
    mcp = FastMCP("ModelMirror Coding Worker Tools")

    async def call(
        tool_name: str,
        arguments: dict[str, Any],
        *,
        operation_id: str | None = None,
        lease_id: str | None = None,
        network_lease_id: str | None = None,
    ) -> dict[str, Any]:
        return await client.call(
            operation_id=operation_id or f"inspect_{uuid.uuid4().hex}",
            tool_name=tool_name,
            arguments=arguments,
            lease_id=lease_id,
            network_lease_id=network_lease_id,
        )

    @mcp.tool()
    async def list_files() -> dict[str, Any]:
        """List task workspace entries using opaque, workspace-relative identities."""
        return await call("list_files", {})

    @mcp.tool()
    async def read_file(path: str) -> dict[str, Any]:
        """Read one workspace-relative text file or return binary metadata."""
        return await call("read_file", {"path": path})

    @mcp.tool()
    async def read_file_range(
        path: str, start_line: int = 1, end_line: int = 200
    ) -> dict[str, Any]:
        """Read a bounded line range from one workspace-relative UTF-8 file."""
        return await call(
            "read_file_range",
            {"path": path, "start_line": start_line, "end_line": end_line},
        )

    @mcp.tool()
    async def glob_files(pattern: str) -> dict[str, Any]:
        """List workspace entries matching one bounded workspace-relative glob."""
        return await call("glob_files", {"pattern": pattern})

    @mcp.tool()
    async def search_text(query: str) -> dict[str, Any]:
        """Search text inside the current task workspace."""
        return await call("search_text", {"query": query})

    @mcp.tool()
    async def search_regex(
        pattern: str,
        glob: str = "**/*",
        case_sensitive: bool = True,
    ) -> dict[str, Any]:
        """Run one bounded safe-regex search inside matching workspace files."""
        return await call(
            "search_regex",
            {"pattern": pattern, "glob": glob, "case_sensitive": case_sensitive},
        )

    @mcp.tool()
    async def workspace_diff() -> dict[str, Any]:
        """Return the current synthetic-H0 Git diff."""
        return await call("diff", {})

    @mcp.tool()
    async def read_operation_output(
        operation_id: str, after: int = 0
    ) -> dict[str, Any]:
        """Read bounded streamed output for one task-owned operation."""
        return await call(
            "read_operation_output",
            {"operation_id": operation_id, "after": after},
        )

    @mcp.tool()
    async def code_symbols(entry_id: str) -> dict[str, Any]:
        """List symbols for one opaque Python or TypeScript workspace entry."""
        return await call("code_symbols", {"entry_id": entry_id})

    @mcp.tool()
    async def code_definition(
        entry_id: str, line: int, character: int
    ) -> dict[str, Any]:
        """Resolve a position to task-bound opaque workspace entries."""
        return await call(
            "code_definition",
            {"entry_id": entry_id, "line": line, "character": character},
        )

    @mcp.tool()
    async def code_references(
        entry_id: str, line: int, character: int
    ) -> dict[str, Any]:
        """Find references without exposing Executor paths or LSP frames."""
        return await call(
            "code_references",
            {"entry_id": entry_id, "line": line, "character": character},
        )

    @mcp.tool()
    async def code_hover(
        entry_id: str, line: int, character: int
    ) -> dict[str, Any]:
        """Return bounded hover text for one task-bound source position."""
        return await call(
            "code_hover",
            {"entry_id": entry_id, "line": line, "character": character},
        )

    @mcp.tool()
    async def code_diagnostics(entry_id: str) -> dict[str, Any]:
        """Return diagnostics bound to one entry and current workspace tree."""
        return await call("code_diagnostics", {"entry_id": entry_id})

    @mcp.tool()
    async def write_file(operation_id: str, path: str, content: str) -> dict[str, Any]:
        """Atomically write UTF-8 text using a stable operation id."""
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return await call(
            "write_file",
            {"path": path, "content": content, "content_sha256": content_sha256},
            operation_id=operation_id,
        )

    @mcp.tool()
    async def delete_file(
        operation_id: str, path: str, expected_sha256: str
    ) -> dict[str, Any]:
        """Delete an unchanged workspace file using a stable operation id."""
        return await call(
            "delete_file",
            {"path": path, "expected_sha256": expected_sha256},
            operation_id=operation_id,
        )

    @mcp.tool()
    async def apply_changeset(
        operation_id: str,
        base_tree_hash: str,
        changes: list[BrokerChange],
    ) -> dict[str, Any]:
        """Atomically publish a preimage-bound write, replace, patch, move, or delete batch."""
        encoded_changes: list[dict[str, Any]] = []
        for change in changes:
            if isinstance(change, BrokerReplaceChange):
                encoded_changes.append(_replace_change_as_write(change))
                continue
            encoded = change.model_dump(mode="json", exclude_none=True)
            if isinstance(change, BrokerWriteChange):
                encoded["content_sha256"] = hashlib.sha256(
                    change.content.encode("utf-8")
                ).hexdigest()
            elif isinstance(change, BrokerPatchChange):
                encoded["patch_sha256"] = hashlib.sha256(
                    change.patch.encode("utf-8")
                ).hexdigest()
            encoded_changes.append(encoded)
        return await call(
            "apply_changeset",
            {"base_tree_hash": base_tree_hash, "changes": encoded_changes},
            operation_id=operation_id,
        )

    @mcp.tool()
    async def list_acceptance_checks() -> dict[str, Any]:
        """List the immutable acceptance checks allowed for this task."""
        return await call("list_acceptance_checks", {})

    @mcp.tool()
    async def run_check(check_id: str) -> dict[str, Any]:
        """Run one check returned by list_acceptance_checks."""
        return await call("run_check", {"check_id": check_id})

    @mcp.tool()
    async def run_command(
        operation_id: str,
        argv: list[str],
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Request approval, then run one exact argv-only command."""
        return await call(
            "run_command",
            {"argv": argv, "timeout_seconds": timeout_seconds},
            operation_id=operation_id,
        )

    @mcp.tool()
    async def run_shell(
        operation_id: str,
        script: str,
        cwd: str = ".",
        mode: str = "inspect",
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        """Run an approved Bash script in a disposable clone; use relative paths only."""
        normalized_script = _workspace_relative_shell_script(script)
        normalized_cwd = _workspace_relative_cwd(cwd)
        return await call(
            "run_shell",
            {
                "script": normalized_script,
                "cwd": normalized_cwd,
                "mode": mode,
                "timeout_seconds": timeout_seconds,
            },
            operation_id=operation_id,
        )

    @mcp.tool()
    async def install_dependencies(
        operation_id: str,
        manager: str = "npm",
        action: str = "ci",
        requirements: str | None = None,
    ) -> dict[str, Any]:
        """Run one platform-frozen npm, uv, or hash-locked pip dependency plan."""
        arguments: dict[str, Any] = {"manager": manager, "action": action}
        if requirements is not None:
            arguments["requirements"] = requirements
        return await call(
            "install_dependencies",
            arguments,
            operation_id=operation_id,
        )

    @mcp.tool()
    async def query_documentation(
        operation_id: str,
        resource_id: str,
        document_path: str,
    ) -> dict[str, Any]:
        """Fetch one registered official document through exact approval and egress leases."""
        return await call(
            "query_documentation",
            {"resource_id": resource_id, "document_path": document_path},
            operation_id=operation_id,
        )

    @mcp.tool()
    async def start_service(
        operation_id: str,
        argv: list[str],
        ttl_seconds: int = 900,
        preview_port: int | None = None,
    ) -> dict[str, Any]:
        """Request approval, then start one task-owned background service."""
        return await call(
            "start_service",
            {
                "argv": argv,
                "ttl_seconds": ttl_seconds,
                "preview_port": preview_port,
            },
            operation_id=operation_id,
        )

    @mcp.tool()
    async def service_status(service_id: str) -> dict[str, Any]:
        """Read task-owned service state without exposing its process id."""
        return await call("service_status", {"service_id": service_id})

    @mcp.tool()
    async def service_input(
        operation_id: str, service_id: str, data: str
    ) -> dict[str, Any]:
        """Send bounded input to a task-owned service."""
        return await call(
            "service_input",
            {"service_id": service_id, "data": data},
            operation_id=operation_id,
        )

    @mcp.tool()
    async def create_subtask(
        operation_id: str,
        client_subtask_id: str,
        kind: str,
        objective: str,
    ) -> dict[str, Any]:
        """Delegate one depth-one explore, implement, or review task to an isolated fork."""
        return await call(
            "create_subtask",
            {
                "client_subtask_id": client_subtask_id,
                "kind": kind,
                "objective": objective,
            },
            operation_id=operation_id,
        )

    @mcp.tool()
    async def merge_subtask(
        operation_id: str, child_task_id: str
    ) -> dict[str, Any]:
        """Merge a ready implement subtask by exact preimage and parent-tree CAS."""
        return await call(
            "merge_subtask",
            {"child_task_id": child_task_id},
            operation_id=operation_id,
        )

    @mcp.tool()
    async def stop_service(operation_id: str, service_id: str) -> dict[str, Any]:
        """Send Ctrl-C to a task-owned service and archive its output."""
        return await call(
            "stop_service",
            {"service_id": service_id},
            operation_id=operation_id,
        )

    @mcp.tool()
    async def update_plan(
        operation_id: str,
        items: list[BrokerPlanItem],
        explanation: str | None = None,
    ) -> dict[str, Any]:
        """Replace the platform-owned structured plan for the current turn."""
        return await call(
            "update_plan",
            {
                "items": [item.model_dump(mode="json") for item in items],
                "explanation": explanation,
            },
            operation_id=operation_id,
        )

    @mcp.tool()
    async def update_todo(
        operation_id: str, items: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Replace the platform-owned Todo list for the current turn."""
        return await call(
            "update_todo", {"items": items}, operation_id=operation_id
        )

    @mcp.tool()
    async def request_user_input(
        operation_id: str,
        question_id: str,
        prompt: str,
        options: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Park the turn at one durable, single-settlement user question."""
        return await call(
            "request_user_input",
            {
                "question_id": question_id,
                "prompt": prompt,
                "options": options or [],
            },
            operation_id=operation_id,
        )

    @mcp.tool()
    async def compact_context(operation_id: str, note: str | None = None) -> dict[str, Any]:
        """Request platform-controlled compaction at the current complete tool boundary."""
        return await call(
            "compact_context", {"note": note}, operation_id=operation_id
        )

    return mcp


def main() -> None:
    endpoint = os.environ.get("CODING_WORKER_BROKER_ENDPOINT", "")
    token = os.environ.get("CODING_WORKER_BROKER_TOKEN", "")
    task_id = os.environ.get("CODING_WORKER_TASK_ID", "")
    if not endpoint or not token or not task_id:
        raise SystemExit("Coding Worker broker binding is unavailable")
    build_server(BrokerRPCClient(endpoint, token=token, task_id=task_id)).run()


if __name__ == "__main__":
    main()
