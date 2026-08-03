from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import os
import re
import time
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .acp_client import AcpClient, AcpProcessConfig, AcpRequestTimeout
from .draft_workspace import (
    DraftPolicyError,
    DraftRevisionError,
    DraftTransactionError,
    DraftValidationError,
    DraftWorkspace,
    DraftWorkspaceError,
)
from .models import CodingEvent, CodingEventKind, CodingSession, CodingSessionState
from .projects import (
    MAX_PROJECT_AGENTS_BYTES,
    MAX_PROJECT_SNAPSHOT_BYTES,
    MAX_PROJECT_SNAPSHOT_FILE_BYTES,
    MAX_PROJECT_SNAPSHOT_FILES,
    ProjectKind,
    project_snapshot_path_is_allowed,
)
from .verification import (
    MAX_VERIFICATION_DETAIL_CHARS,
    MAX_VERIFICATION_SUMMARY_CHARS,
    VerificationResult,
    VerificationState,
    VerificationStep,
    initial_verification_report,
    sanitize_verification_output,
    select_verification_plan,
)
from .verifier_client import (
    CodingVerifierClient,
    VerifierClientError,
    source_snapshot_fingerprint,
)

MAX_WORKER_FRAME_BYTES = 2 * 1024 * 1024
MAX_PROMPT_CHARS = 20_000
SOCKET_PATH = Path(
    os.getenv(
        "CODING_AGENT_SOCKET_PATH",
        "/run/modelmirror-coding/coding-runtime.sock",
    )
)
WORKSPACE_PATH = "/workspace"
SOURCE_SNAPSHOT_PATH = Path("/opt/modelmirror-source")
PROJECT_SNAPSHOT_PATH = Path(
    os.getenv("CODING_PROJECT_SNAPSHOT_PATH", "/project-snapshots/current")
)
CHECKPOINT_PATH = Path("/tmp/modelmirror-coding-checkpoint")
VERIFIER_SOCKET_PATH = Path(
    os.getenv(
        "CODING_VERIFIER_SOCKET_PATH",
        "/run/modelmirror-coding/verifier.sock",
    )
)
OPENCODE_PATH = "/usr/local/bin/opencode"
RIPGREP_PATH = "/usr/bin/rg"
INTERNAL_GATEWAY_BASE_URL = "http://new-api:3000/v1"
SAFE_MODEL_ID = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")
SAFE_RECOVERY_REASON = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SAFE_RUNNER_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
CODING_AGENT_MODES = frozenset({"readonly", "draft"})
MAX_AGENT_STEPS = 12
MODEL_CONTEXT_TOKENS = 131_072
MODEL_OUTPUT_TOKENS = 8_192
REQUIRED_RUNTIME_EXECUTABLES = (Path(OPENCODE_PATH), Path(RIPGREP_PATH))
RUNNER_MCP_SOCKET_PATH = "/tmp/modelmirror-runner.sock"
RUNNER_MCP_NAME = "modelmirror-runner"


class CodingWorkerError(RuntimeError):
    def __init__(self, message: str, *, code: str = "worker_error") -> None:
        super().__init__(message)
        self.code = code


class CodingWorkerProtocolError(CodingWorkerError):
    pass


def coding_agent_mode() -> str:
    mode = os.getenv("CODING_AGENT_MODE", "readonly").strip().lower()
    if mode not in CODING_AGENT_MODES:
        raise CodingWorkerError(
            "Coding Agent mode is not configured safely.",
            code="not_configured",
        )
    return mode


def validate_runtime_dependencies(
    paths: tuple[Path, ...] = REQUIRED_RUNTIME_EXECUTABLES,
) -> None:
    missing = [
        path.name
        for path in paths
        if not path.is_file() or not os.access(path, os.X_OK)
    ]
    if missing:
        raise CodingWorkerError(
            "Coding Agent runtime dependencies are unavailable.",
            code="not_configured",
        )


def _permission_for_mode(
    mode: str,
    *,
    commands_enabled: bool = False,
) -> dict[str, Any]:
    if mode not in CODING_AGENT_MODES:
        raise CodingWorkerError(
            "Coding Agent mode is not configured safely.",
            code="not_configured",
        )
    permission: dict[str, Any] = {
        "*": "deny",
        "read": {
            "*": "allow",
            "**/.git": "deny",
            "**/.git/**": "deny",
            "**/.env": "deny",
            "**/.env.*": "deny",
            "**/*.pem": "deny",
            "**/*.key": "deny",
            "**/storage/**": "deny",
            "**/uploads/**": "deny",
            "**/new-api-data/**": "deny",
        },
        "list": "allow",
        "glob": "allow",
        "grep": "allow",
        "lsp": "allow",
        "edit": "ask" if mode == "draft" else "deny",
        "bash": "deny",
        "task": "deny",
        "webfetch": "deny",
        "websearch": "deny",
        "skill": "deny",
        "external_directory": "deny",
        "question": "deny",
        "todowrite": "deny",
        "doom_loop": "deny",
    }
    if commands_enabled:
        permission[f"{RUNNER_MCP_NAME}_*"] = "allow"
    return permission


def build_opencode_config(
    model_id: str,
    mode: str = "readonly",
    *,
    project_instructions: bool = False,
    commands_enabled: bool = False,
    runner_token: str = "",
) -> dict[str, Any]:
    if not SAFE_MODEL_ID.fullmatch(model_id):
        raise CodingWorkerError(
            "Coding Agent model is not configured safely.",
            code="not_configured",
        )
    if commands_enabled and (
        mode != "draft" or SAFE_RUNNER_TOKEN.fullmatch(runner_token) is None
    ):
        raise CodingWorkerError(
            "Coding project commands are not configured safely.",
            code="not_configured",
        )
    permission = _permission_for_mode(mode, commands_enabled=commands_enabled)
    agent_name = "draft" if mode == "draft" else "readonly"
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"modelmirror/{model_id}",
        "default_agent": agent_name,
        "agent": {
            agent_name: {
                "description": (
                    "Isolated project change draft assistant"
                    if mode == "draft"
                    else "Read-only project analyst"
                ),
                "mode": "primary",
                "steps": MAX_AGENT_STEPS,
                "permission": permission,
            }
        },
        "permission": permission,
        "provider": {
            "modelmirror": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "ModelMirror Internal Gateway",
                "options": {
                    "baseURL": INTERNAL_GATEWAY_BASE_URL,
                    "apiKey": "{env:CODING_AGENT_GATEWAY_KEY}",
                },
                "models": {
                    model_id: {
                        "name": "ModelMirror Coding Model",
                        "limit": {
                            "context": MODEL_CONTEXT_TOKENS,
                            "output": MODEL_OUTPUT_TOKENS,
                        },
                    }
                },
            }
        },
        "plugin": [],
        "mcp": (
            {
                RUNNER_MCP_NAME: {
                    "type": "local",
                    "command": ["python", "-m", "coding_runtime.runner_mcp"],
                    "environment": {
                        "MODELMIRROR_RUNNER_SOCKET": RUNNER_MCP_SOCKET_PATH,
                        "MODELMIRROR_RUNNER_TOKEN": runner_token,
                    },
                    "enabled": True,
                    "timeout": 310_000,
                }
            }
            if commands_enabled
            else {}
        ),
        "instructions": ["/workspace/AGENTS.md"] if project_instructions else [],
        "share": "disabled",
        "autoupdate": False,
    }


def create_acp_client(
    mode: str | None = None,
    *,
    project_instructions: bool = False,
    commands_enabled: bool = False,
    runner_token: str = "",
) -> AcpClient:
    active_mode = mode or coding_agent_mode()
    model_id = os.getenv("CODING_AGENT_MODEL", "").strip()
    gateway_key = os.getenv("CODING_AGENT_GATEWAY_KEY", "").strip()
    if not gateway_key:
        raise CodingWorkerError(
            "Coding Agent gateway key is not configured.",
            code="not_configured",
        )
    config_content = json.dumps(
        build_opencode_config(
            model_id,
            active_mode,
            project_instructions=project_instructions,
            commands_enabled=commands_enabled,
            runner_token=runner_token,
        ),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    child_environment = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/home/coding",
        "OPENCODE_TEST_HOME": "/home/coding",
        "XDG_CONFIG_HOME": "/home/coding/.config",
        "XDG_DATA_HOME": "/home/coding/.local/share",
        "XDG_STATE_HOME": "/home/coding/.local/state",
        "XDG_CACHE_HOME": "/home/coding/.cache",
        "OPENCODE_CONFIG_CONTENT": config_content,
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
        "OPENCODE_PURE": "1",
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_AUTOCOMPACT": "1",
        "OPENCODE_DISABLE_MODELS_FETCH": "1",
        "OPENCODE_AUTH_CONTENT": "{}",
        "CODING_AGENT_GATEWAY_KEY": gateway_key,
        "NO_PROXY": "new-api,localhost,127.0.0.1",
        "no_proxy": "new-api,localhost,127.0.0.1",
    }
    return AcpClient(
        AcpProcessConfig(
            command=(OPENCODE_PATH, "acp", "--cwd", WORKSPACE_PATH),
            workspace=WORKSPACE_PATH,
            mode=active_mode,
            process_cwd=WORKSPACE_PATH,
            environment=child_environment,
            request_timeout=120.0,
            prompt_timeout=900.0,
            prompt_idle_timeout=180.0,
            shutdown_timeout=5.0,
        )
    )


@dataclass(slots=True)
class _WorkerSession:
    session: CodingSession
    adapter: AcpClient
    workspace: DraftWorkspace
    mode: str
    source: WorkspaceSource | None = None
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    verification: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceSource:
    kind: ProjectKind
    project_id: str
    name: str
    snapshot_path: Path
    fingerprint: str
    branch: str | None = None
    head: str | None = None
    lease_id: str | None = None

    @property
    def verification_available(self) -> bool:
        return self.kind is ProjectKind.BUILTIN

    def to_public_dict(self) -> dict[str, object]:
        return {
            "id": self.project_id,
            "name": self.name,
            "kind": self.kind.value,
            "branch": self.branch,
            "head": self.head[:12] if self.head else None,
        }


class CodingWorkerServer:
    """Single-instance Unix socket host for one isolated ACP session."""

    def __init__(
        self,
        socket_path: Path = SOCKET_PATH,
        *,
        source_snapshot_path: Path = SOURCE_SNAPSHOT_PATH,
        project_snapshot_path: Path = PROJECT_SNAPSHOT_PATH,
        workspace_path: Path = Path(WORKSPACE_PATH),
        checkpoint_path: Path = CHECKPOINT_PATH,
        verifier: CodingVerifierClient | None = None,
    ) -> None:
        self._socket_path = socket_path
        self._source_snapshot_path = source_snapshot_path
        self._project_snapshot_path = project_snapshot_path
        self._workspace_path = workspace_path
        self._checkpoint_path = checkpoint_path
        self._verifier = verifier or CodingVerifierClient(VERIFIER_SOCKET_PATH)
        try:
            self._source_fingerprint = source_snapshot_fingerprint(
                self._source_snapshot_path
            )
        except VerifierClientError:
            self._source_fingerprint = ""
        self._sessions: dict[str, _WorkerSession] = {}
        self._sessions_lock = asyncio.Lock()

    async def handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await reader.readline()
            if not raw or len(raw) > MAX_WORKER_FRAME_BYTES:
                raise CodingWorkerProtocolError(
                    "Coding worker request is empty or too large.",
                    code="invalid_request",
                )
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise CodingWorkerProtocolError(
                    "Coding worker request must be an object.",
                    code="invalid_request",
                )
            action = request.get("action")
            if action == "health":
                try:
                    mode = coding_agent_mode()
                except CodingWorkerError:
                    mode = "invalid"
                await self._send(
                    writer,
                    {
                        "ok": True,
                        "configured": bool(
                            os.getenv("CODING_AGENT_MODEL", "").strip()
                            and os.getenv("CODING_AGENT_GATEWAY_KEY", "").strip()
                            and mode in CODING_AGENT_MODES
                        ),
                        "version": 1,
                        "mode": mode,
                        "snapshot_fingerprint": self._source_fingerprint,
                        "verification": await self._verification_health(),
                    },
                )
            elif action == "create_session":
                await self._create_session(request, writer)
            elif action == "restore_session":
                await self._restore_session(request, writer)
            elif action == "recovery_snapshot":
                await self._recovery_snapshot(request, writer)
            elif action == "prompt":
                await self._prompt(request, writer)
            elif action == "cancel":
                await self._cancel(request, writer)
            elif action == "close":
                await self._close_session(request, writer)
            elif action == "changes":
                await self._changes(request, writer)
            elif action == "diff":
                await self._diff(request, writer)
            elif action == "patch":
                await self._patch(request, writer)
            elif action == "checkpoint_cycle":
                await self._checkpoint_cycle(request, writer)
            elif action == "validate":
                await self._validate(request, writer)
            elif action == "discard":
                await self._discard(request, writer)
            elif action == "verification_start":
                await self._verification_start(request, writer)
            elif action == "verification_status":
                await self._verification_status(request, writer)
            elif action == "verification_cancel":
                await self._verification_cancel(request, writer)
            else:
                raise CodingWorkerProtocolError(
                    "Unsupported coding worker action.",
                    code="invalid_request",
                )
        except (UnicodeDecodeError, json.JSONDecodeError):
            await self._send_error(writer, "invalid_request")
        except CodingWorkerError as exc:
            await self._send_error(writer, exc.code)
        except VerifierClientError as exc:
            await self._send_error(writer, exc.code)
        except DraftRevisionError:
            await self._send_error(writer, "stale_revision")
        except DraftValidationError as exc:
            await self._send_error(writer, str(exc))
        except DraftPolicyError:
            await self._send_error(writer, "invalid_path")
        except DraftTransactionError:
            await self._send_error(writer, "draft_busy")
        except DraftWorkspaceError:
            await self._send_error(writer, "draft_review_failed")
        except Exception:
            await self._send_error(writer, "worker_internal_error")
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def close(self) -> None:
        async with self._sessions_lock:
            records = list(self._sessions.values())
            self._sessions.clear()
        for record in records:
            await self._cleanup_record(record)

    async def _create_session(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        if set(request) not in ({"action"}, {"action", "source"}):
            raise CodingWorkerProtocolError(
                "Coding session request fields are invalid.",
                code="invalid_request",
            )
        source = await asyncio.to_thread(
            self._resolve_workspace_source,
            request.get("source"),
        )
        async with self._sessions_lock:
            if self._sessions:
                raise CodingWorkerError(
                    "Coding runtime already has an active session.",
                    code="concurrency_limit",
                )
            mode = coding_agent_mode()
            session = CodingSession()
            workspace = DraftWorkspace(
                source.snapshot_path,
                self._workspace_path,
                self._checkpoint_path,
                preserve_workspace_root=True,
            )
            try:
                workspace.initialize()
                if mode == "readonly":
                    self._set_workspace_read_only(workspace.workspace_root)
                else:
                    self._set_workspace_writable(workspace.workspace_root)
                adapter = self._create_source_adapter(mode, source)
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self._set_workspace_writable(workspace.workspace_root)
                    workspace.destroy()
                raise CodingWorkerError(
                    "Coding runtime could not prepare the workspace.",
                    code="agent_unavailable",
                ) from exc
            record = _WorkerSession(
                session=session,
                adapter=adapter,
                workspace=workspace,
                mode=mode,
                source=source,
            )
            self._sessions[session.session_id] = record
        try:
            event = _event_with_project(await adapter.open(session), source)
        except Exception:
            async with self._sessions_lock:
                self._sessions.pop(session.session_id, None)
            await self._cleanup_record(record)
            raise CodingWorkerError(
                "Coding runtime could not start the agent.",
                code="agent_unavailable",
            )
        await self._send(
            writer,
            {
                "ok": True,
                "session_id": session.session_id,
                "mode": mode,
                "project": source.to_public_dict(),
                "event": event.to_dict(),
            },
        )

    async def _restore_session(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        allowed_keys = {
            "action",
            "revision",
            "patch",
            "paths",
            "base_patch",
            "base_paths",
            "snapshot_fingerprint",
            "verification",
            "source",
        }
        if not set(request).issubset(allowed_keys):
            raise CodingWorkerProtocolError(
                "Coding recovery request fields are invalid.",
                code="invalid_request",
            )
        revision = request.get("revision")
        patch = request.get("patch")
        paths = request.get("paths")
        base_patch = request.get("base_patch", "")
        base_paths = request.get("base_paths", [])
        expected_fingerprint = request.get("snapshot_fingerprint")
        verification = request.get("verification")
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 1
            or not isinstance(patch, str)
            or not isinstance(paths, list)
            or not all(isinstance(path, str) for path in paths)
            or tuple(paths) != tuple(sorted(set(paths)))
            or not isinstance(base_patch, str)
            or not isinstance(base_paths, list)
            or not all(isinstance(path, str) for path in base_paths)
            or tuple(base_paths) != tuple(sorted(set(base_paths)))
            or bool(patch) != bool(paths)
            or bool(base_patch) != bool(base_paths)
            or not isinstance(expected_fingerprint, str)
        ):
            raise CodingWorkerProtocolError(
                "Coding recovery request is invalid.",
                code="invalid_request",
            )
        source = await asyncio.to_thread(
            self._resolve_workspace_source,
            request.get("source"),
        )
        if expected_fingerprint != source.fingerprint:
            raise CodingWorkerError(
                "Coding recovery snapshot does not match the runtime.",
                code="snapshot_mismatch",
            )
        verification_paths = sorted(set(base_paths) | set(paths))
        restored_verification = _validate_recovered_verification(
            verification,
            revision=revision,
            paths=verification_paths,
        )
        if not source.verification_available and restored_verification is not None:
            raise CodingWorkerError(
                "Project verification is unavailable for this source.",
                code="project_operation_unavailable",
            )
        mode = coding_agent_mode()
        if mode != "draft":
            raise CodingWorkerError(
                "Coding recovery requires draft mode.",
                code="draft_unavailable",
            )

        # A Server restart can leave the Runtime's ephemeral session alive
        # while the encrypted recovery record remains authoritative. Recovery
        # is the only operation allowed to reclaim that orphaned single slot.
        async with self._sessions_lock:
            orphaned = tuple(self._sessions.values())
            self._sessions.clear()
        for stale_record in orphaned:
            await self._cleanup_record(stale_record)

        async with self._sessions_lock:
            if self._sessions:
                raise CodingWorkerError(
                    "Coding runtime already has an active session.",
                    code="concurrency_limit",
                )
            session = CodingSession()
            workspace = DraftWorkspace(
                source.snapshot_path,
                self._workspace_path,
                self._checkpoint_path,
                preserve_workspace_root=True,
            )
            try:
                workspace.initialize()
                report = workspace.restore_incremental(
                    base_patch=base_patch,
                    base_paths=tuple(base_paths),
                    patch=patch,
                    revision=revision,
                    expected_paths=tuple(paths),
                )
                self._set_workspace_writable(workspace.workspace_root)
                adapter = self._create_source_adapter(mode, source)
            except (DraftWorkspaceError, OSError, UnicodeError) as exc:
                with contextlib.suppress(Exception):
                    self._set_workspace_writable(workspace.workspace_root)
                    workspace.destroy()
                raise CodingWorkerError(
                    "Coding recovery data was rejected.",
                    code="recovery_invalid",
                ) from exc
            except Exception as exc:
                with contextlib.suppress(Exception):
                    self._set_workspace_writable(workspace.workspace_root)
                    workspace.destroy()
                raise CodingWorkerError(
                    "Coding runtime could not prepare the recovered session.",
                    code="agent_unavailable",
                ) from exc
            record = _WorkerSession(
                session=session,
                adapter=adapter,
                workspace=workspace,
                mode=mode,
                source=source,
                verification=restored_verification,
            )
            self._sessions[session.session_id] = record
        try:
            event = _event_with_project(await adapter.open(session), source)
        except Exception:
            async with self._sessions_lock:
                self._sessions.pop(session.session_id, None)
            await self._cleanup_record(record)
            raise CodingWorkerError(
                "Coding runtime could not start the recovered agent.",
                code="agent_unavailable",
            )
        await self._send(
            writer,
            {
                "ok": True,
                "session_id": session.session_id,
                "mode": mode,
                "project": source.to_public_dict(),
                "event": event.to_dict(),
                "changes": report.to_dict(),
                "recovered": True,
            },
        )

    async def _recovery_snapshot(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        record = self._require_draft_session(request)
        if record.turn_lock.locked():
            raise CodingWorkerError(
                "Coding draft is still changing.",
                code="draft_busy",
            )
        await self._refresh_verification(record)
        report = record.workspace.changes()
        patch = "".join(item.diff for item in report.files)
        cumulative = record.workspace.cumulative_changes()
        cumulative_patch = "".join(item.diff for item in cumulative.files)
        verification = record.verification
        if verification is not None and (
            verification.get("state") != VerificationState.COMPLETED.value
            or verification.get("revision") != report.revision
            or verification.get("stale") is not False
            or verification.get("result")
            not in {
                VerificationResult.PASSED.value,
                VerificationResult.FAILED.value,
                VerificationResult.NOT_APPLICABLE.value,
            }
        ):
            verification = None
        await self._send(
            writer,
            {
                "ok": True,
                "snapshot_fingerprint": self._record_source(record).fingerprint,
                "project": self._record_source(record).to_public_dict(),
                "changes": report.to_dict(),
                "patch": patch,
                "base_patch": record.workspace.cycle_patch,
                "cumulative_changes": cumulative.to_dict(),
                "cumulative_patch": cumulative_patch,
                "verification": verification,
            },
        )

    async def _prompt(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        record = self._require_session(request)
        prompt = request.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise CodingWorkerProtocolError(
                "Prompt must not be empty.",
                code="invalid_prompt",
            )
        if len(prompt) > MAX_PROMPT_CHARS:
            raise CodingWorkerProtocolError(
                "Prompt exceeds the configured limit.",
                code="prompt_too_long",
            )
        if record.mode == "draft":
            await self._refresh_verification(record)
            if self._verification_running(record):
                raise CodingWorkerError(
                    "Project verification is running.",
                    code="verification_in_progress",
                )
        if record.turn_lock.locked():
            raise CodingWorkerError(
                "Coding runtime already has an active turn.",
                code="concurrency_limit",
            )
        async with record.turn_lock:
            if record.mode == "draft":
                record.workspace.begin_turn()
            terminal_event: CodingEvent | None = None
            active_turn_id: str | None = None
            try:
                event_stream = record.adapter.prompt(record.session, prompt)
                while True:
                    try:
                        event = await anext(event_stream)
                    except StopAsyncIteration:
                        break
                    except Exception as exc:
                        if record.mode == "draft":
                            record.workspace.rollback_turn()
                            self._set_workspace_writable(
                                record.workspace.workspace_root
                            )
                        await self._reset_agent_context(record)
                        failure_event = record.session.append_event(
                            CodingEventKind.FAILED,
                            turn_id=active_turn_id,
                            data={
                                "code": (
                                    "agent_turn_timeout"
                                    if isinstance(exc, AcpRequestTimeout)
                                    else "agent_turn_failed"
                                )
                            },
                        )
                        await self._send(
                            writer,
                            {"ok": True, "event": failure_event.to_dict()},
                        )
                        await self._send(writer, {"ok": True, "done": True})
                        return
                    if event.turn_id is not None:
                        active_turn_id = event.turn_id
                    if event.kind in {
                        CodingEventKind.TURN_COMPLETED,
                        CodingEventKind.FAILED,
                        CodingEventKind.CANCELLED,
                    }:
                        terminal_event = event
                    else:
                        await self._send(
                            writer,
                            {"ok": True, "event": event.to_dict()},
                        )
                if terminal_event is None:
                    raise CodingWorkerError(
                        "Coding Agent turn omitted a terminal event.",
                        code="agent_turn_failed",
                    )
                if record.mode == "draft":
                    terminal_event = self._finish_draft_turn(
                        record,
                        terminal_event,
                    )
                if terminal_event.kind is CodingEventKind.CANCELLED:
                    await self._reset_agent_context(record)
                await self._send(
                    writer,
                    {"ok": True, "event": terminal_event.to_dict()},
                )
                await self._send(writer, {"ok": True, "done": True})
            except Exception:
                if record.mode == "draft":
                    with contextlib.suppress(DraftWorkspaceError):
                        record.workspace.rollback_turn()
                        self._set_workspace_writable(
                            record.workspace.workspace_root
                        )
                raise CodingWorkerError(
                    "Coding Agent turn failed.",
                    code="agent_turn_failed",
                )

    @staticmethod
    async def _reset_agent_context(record: _WorkerSession) -> None:
        old_session = record.session
        old_adapter = record.adapter
        next_sequence = old_session._next_seq
        next_session = CodingSession(
            session_id=old_session.session_id,
            created_at=old_session.created_at,
            _next_seq=next_sequence,
        )
        next_adapter = CodingWorkerServer._create_source_adapter(
            record.mode,
            record.source,
        )
        await old_adapter.close(old_session)
        try:
            await next_adapter.open(next_session)
        except BaseException:
            with contextlib.suppress(BaseException):
                await next_adapter.close(next_session)
            raise
        # The replacement ACP session's startup event is an internal detail.
        next_session._next_seq = next_sequence
        record.session = next_session
        record.adapter = next_adapter

    async def _cancel(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        record = self._require_session(request)
        accepted = await record.adapter.cancel(record.session)
        await self._send(writer, {"ok": True, "accepted": accepted})

    async def _close_session(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        session_id = request.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise CodingWorkerProtocolError(
                "Session id is required.",
                code="invalid_request",
            )
        async with self._sessions_lock:
            record = self._sessions.pop(session_id, None)
        if record is None:
            raise CodingWorkerError(
                "Coding session is not available.",
                code="session_not_found",
            )
        await self._cleanup_record(record)
        await self._send(writer, {"ok": True})

    async def _changes(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        record = self._require_draft_session(request)
        await self._send(
            writer,
            {"ok": True, "changes": record.workspace.changes().to_dict()},
        )

    async def _diff(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        record = self._require_draft_session(request)
        revision = self._require_revision(request)
        path = request.get("path")
        if not isinstance(path, str) or not path:
            raise CodingWorkerProtocolError(
                "Draft path is required.",
                code="invalid_request",
            )
        await self._send(
            writer,
            {
                "ok": True,
                "revision": revision,
                "path": path,
                "diff": record.workspace.diff_for(path, revision),
            },
        )

    async def _patch(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        record = self._require_draft_session(request)
        revision = self._require_revision(request)
        scope = request.get("scope", "current")
        if scope not in {"current", "cumulative"}:
            raise CodingWorkerProtocolError(
                "Draft Patch scope is invalid.",
                code="invalid_request",
            )
        patch = (
            record.workspace.cumulative_patch(revision)
            if scope == "cumulative"
            else record.workspace.patch(revision)
        )
        await self._send(
            writer,
            {
                "ok": True,
                "revision": revision,
                "scope": scope,
                "patch": patch,
            },
        )

    async def _checkpoint_cycle(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        record = self._require_draft_session(request)
        revision = self._require_revision(request)
        await self._refresh_verification(record)
        if self._verification_running(record):
            raise CodingWorkerError(
                "Project verification is running.",
                code="verification_in_progress",
            )
        changes = record.workspace.checkpoint_cycle(revision).to_dict()
        record.verification = None
        await self._send(writer, {"ok": True, "changes": changes})

    async def _validate(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        record = self._require_draft_session(request)
        await self._send(
            writer,
            {"ok": True, "changes": record.workspace.validate().to_dict()},
        )

    async def _discard(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        record = self._require_draft_session(request)
        await self._refresh_verification(record)
        if self._verification_running(record):
            raise CodingWorkerError(
                "Project verification is running.",
                code="verification_in_progress",
            )
        changes = record.workspace.discard().to_dict()
        self._set_workspace_writable(record.workspace.workspace_root)
        await self._send(
            writer,
            {"ok": True, "changes": changes},
        )

    async def _verification_start(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        record = self._require_draft_session(request)
        self._require_verification_available(record)
        revision = self._require_revision(request)
        await self._refresh_verification(record)
        if self._verification_running(record):
            raise CodingWorkerError(
                "Project verification is already running.",
                code="verification_in_progress",
            )
        source = self._record_source(record)
        if not source.fingerprint:
            raise CodingWorkerError(
                "Project verification source is unavailable.",
                code="verifier_unavailable",
            )
        report = record.workspace.cumulative_changes()
        if revision != report.revision:
            raise DraftRevisionError("stale_revision")
        patch = record.workspace.cumulative_patch(revision)
        response = await self._verifier.start(
            session_id=record.session.session_id,
            revision=revision,
            patch=patch,
            paths=[item.path for item in report.files],
            expected_fingerprint=source.fingerprint,
        )
        verification = self._store_verification(
            record,
            response,
            current_revision=revision,
        )
        await self._send(
            writer,
            {"ok": True, "verification": verification},
        )

    async def _verification_status(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        record = self._require_draft_session(request)
        self._require_verification_available(record)
        requested_revision = self._require_revision(request)
        current_revision = record.workspace.changes().revision
        stored_revision = (
            record.verification.get("revision")
            if record.verification is not None
            else None
        )
        if requested_revision not in {current_revision, stored_revision}:
            raise DraftRevisionError("stale_revision")
        if record.verification is None:
            report = self._initial_verification(record)
        elif record.verification.get("revision") != requested_revision:
            report = dict(record.verification)
        else:
            await self._refresh_verification(record)
            report = dict(
                record.verification or self._initial_verification(record)
            )
        report["stale"] = report.get("revision") != current_revision
        await self._send(
            writer,
            {"ok": True, "verification": report},
        )

    async def _verification_cancel(
        self,
        request: dict[str, Any],
        writer: asyncio.StreamWriter,
    ) -> None:
        record = self._require_draft_session(request)
        self._require_verification_available(record)
        revision = self._require_revision(request)
        if (
            record.verification is None
            or record.verification.get("revision") != revision
        ):
            raise CodingWorkerError(
                "Project verification was not found.",
                code="verification_not_found",
            )
        if not self._verification_running(record):
            await self._send(
                writer,
                {
                    "ok": True,
                    "accepted": True,
                    "verification": record.verification,
                },
            )
            return
        response = await self._verifier.cancel(
            session_id=record.session.session_id,
            revision=revision,
        )
        verification = self._store_verification(
            record,
            response,
            current_revision=record.workspace.changes().revision,
        )
        await self._send(
            writer,
            {
                "ok": True,
                "accepted": response.get("accepted") is True,
                "verification": verification,
            },
        )

    async def _verification_health(self) -> dict[str, Any]:
        capability = {
            "available": False,
            "strategy": "adaptive",
            "required_for_patch": False,
            "max_duration_seconds": 600,
        }
        if not self._source_fingerprint:
            return {**capability, "reason": "snapshot_unavailable"}
        try:
            health = await self._verifier.health()
        except Exception:
            return {**capability, "reason": "verifier_unavailable"}
        if (
            health.get("configured") is not True
            or health.get("snapshot_fingerprint") != self._source_fingerprint
            or health.get("max_duration_seconds") != 600
        ):
            return {**capability, "reason": "snapshot_mismatch"}
        return {**capability, "available": True}

    async def _refresh_verification(self, record: _WorkerSession) -> None:
        if record.source is not None and not record.source.verification_available:
            record.verification = None
            return
        if not self._verification_running(record):
            return
        assert record.verification is not None
        revision = record.verification.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            record.verification = self._unavailable_verification(record)
            return
        try:
            response = await self._verifier.status(
                session_id=record.session.session_id,
                revision=revision,
            )
            self._store_verification(
                record,
                response,
                current_revision=record.workspace.changes().revision,
            )
        except Exception:
            record.verification = self._unavailable_verification(record)

    def _initial_verification(
        self,
        record: _WorkerSession,
    ) -> dict[str, Any]:
        report = record.workspace.changes()
        plan = select_verification_plan(item.path for item in report.files)
        return initial_verification_report(
            report.revision,
            plan,
        ).to_dict(current_revision=report.revision)

    @staticmethod
    def _verification_running(record: _WorkerSession) -> bool:
        return (
            record.verification is not None
            and record.verification.get("state")
            in {
                VerificationState.NOT_STARTED.value,
                VerificationState.RUNNING.value,
            }
        )

    @staticmethod
    def _store_verification(
        record: _WorkerSession,
        response: dict[str, Any],
        *,
        current_revision: int,
    ) -> dict[str, Any]:
        verification = response.get("verification")
        if not isinstance(verification, dict):
            raise CodingWorkerError(
                "Verifier response is invalid.",
                code="invalid_verifier_response",
            )
        stored = dict(verification)
        stored["stale"] = stored.get("revision") != current_revision
        record.verification = stored
        return stored

    @staticmethod
    def _unavailable_verification(
        record: _WorkerSession,
    ) -> dict[str, Any]:
        current = record.workspace.changes().revision
        previous = record.verification or {}
        revision = previous.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            revision = current
        return {
            "revision": revision,
            "state": VerificationState.COMPLETED.value,
            "result": VerificationResult.NOT_RUN.value,
            "stale": revision != current,
            "reason": "verifier_unavailable",
            "started_at": previous.get("started_at"),
            "finished_at": time.time(),
            "steps": [],
        }

    def _require_session(self, request: dict[str, Any]) -> _WorkerSession:
        session_id = request.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise CodingWorkerProtocolError(
                "Session id is required.",
                code="invalid_request",
            )
        record = self._sessions.get(session_id)
        if record is None or record.session.state in {
            CodingSessionState.FAILED,
            CodingSessionState.CLOSED,
        }:
            raise CodingWorkerError(
                "Coding session is not available.",
                code="session_not_found",
            )
        return record

    def _require_draft_session(
        self,
        request: dict[str, Any],
    ) -> _WorkerSession:
        session_id = request.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise CodingWorkerProtocolError(
                "Session id is required.",
                code="invalid_request",
            )
        record = self._sessions.get(session_id)
        if record is None or record.session.state is CodingSessionState.CLOSED:
            raise CodingWorkerError(
                "Coding session is not available.",
                code="session_not_found",
            )
        if record.mode != "draft":
            raise CodingWorkerError(
                "Draft review is not available.",
                code="draft_unavailable",
            )
        if record.turn_lock.locked():
            raise CodingWorkerError(
                "Draft review is not available during a turn.",
                code="draft_busy",
            )
        return record

    @staticmethod
    def _require_verification_available(record: _WorkerSession) -> None:
        if record.source is not None and not record.source.verification_available:
            raise CodingWorkerError(
                "Project verification is unavailable for this source.",
                code="project_operation_unavailable",
            )

    def _record_source(self, record: _WorkerSession) -> WorkspaceSource:
        if record.source is not None:
            return record.source
        return WorkspaceSource(
            kind=ProjectKind.BUILTIN,
            project_id="modelmirror",
            name="ModelMirror",
            snapshot_path=self._source_snapshot_path,
            fingerprint=self._source_fingerprint,
        )

    @staticmethod
    def _create_source_adapter(
        mode: str,
        source: WorkspaceSource | None,
    ) -> AcpClient:
        if (
            source is not None
            and source.kind is ProjectKind.LOCAL_CLONE
            and (source.snapshot_path / "AGENTS.md").is_file()
        ):
            return create_acp_client(mode, project_instructions=True)
        return create_acp_client(mode)

    def _resolve_workspace_source(self, payload: Any) -> WorkspaceSource:
        if payload is None or payload == {"kind": ProjectKind.BUILTIN.value}:
            return WorkspaceSource(
                kind=ProjectKind.BUILTIN,
                project_id="modelmirror",
                name="ModelMirror",
                snapshot_path=self._source_snapshot_path,
                fingerprint=self._source_fingerprint,
            )
        expected_keys = {
            "kind",
            "lease_id",
            "project_id",
            "name",
            "branch",
            "head",
            "fingerprint",
            "file_count",
            "total_bytes",
            "hidden_files",
            "created_at",
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            raise CodingWorkerProtocolError(
                "Coding project source is invalid.",
                code="invalid_request",
            )
        if payload.get("kind") != ProjectKind.LOCAL_CLONE.value:
            raise CodingWorkerProtocolError(
                "Coding project source is invalid.",
                code="invalid_request",
            )
        lease_path = self._project_snapshot_path / "lease.json"
        workspace_path = self._project_snapshot_path / "workspace"
        try:
            if (
                lease_path.is_symlink()
                or not lease_path.is_file()
                or lease_path.stat().st_size > 16 * 1024
            ):
                raise CodingWorkerError(
                    "Project snapshot is unavailable.",
                    code="snapshot_unavailable",
                )
            lease = json.loads(lease_path.read_text(encoding="utf-8"))
        except CodingWorkerError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CodingWorkerError(
                "Project snapshot is unavailable.",
                code="snapshot_unavailable",
            ) from exc
        expected_lease = {key: value for key, value in payload.items() if key != "kind"}
        if not isinstance(lease, dict) or lease != expected_lease:
            raise CodingWorkerError(
                "Project snapshot lease does not match.",
                code="snapshot_mismatch",
            )
        _validate_local_source_metadata(lease)
        fingerprint = _validate_local_snapshot(workspace_path, lease)
        if fingerprint != lease["fingerprint"]:
            raise CodingWorkerError(
                "Project snapshot fingerprint does not match.",
                code="snapshot_mismatch",
            )
        return WorkspaceSource(
            kind=ProjectKind.LOCAL_CLONE,
            project_id=lease["project_id"],
            name=lease["name"],
            snapshot_path=workspace_path,
            fingerprint=fingerprint,
            branch=lease["branch"],
            head=lease["head"],
            lease_id=lease["lease_id"],
        )

    @staticmethod
    def _require_revision(request: dict[str, Any]) -> int:
        revision = request.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise CodingWorkerProtocolError(
                "Draft revision is required.",
                code="invalid_request",
            )
        return revision

    @staticmethod
    def _finish_draft_turn(
        record: _WorkerSession,
        terminal_event: CodingEvent,
    ) -> CodingEvent:
        if terminal_event.kind is CodingEventKind.TURN_COMPLETED:
            try:
                record.workspace.commit_turn()
            except DraftPolicyError:
                CodingWorkerServer._set_workspace_writable(
                    record.workspace.workspace_root
                )
                return CodingEvent(
                    session_id=terminal_event.session_id,
                    seq=terminal_event.seq,
                    kind=CodingEventKind.FAILED,
                    created_at=terminal_event.created_at,
                    turn_id=terminal_event.turn_id,
                    data={"code": "draft_policy_violation"},
                )
        else:
            record.workspace.rollback_turn()
            CodingWorkerServer._set_workspace_writable(
                record.workspace.workspace_root
            )
        return terminal_event

    @staticmethod
    def _set_workspace_read_only(path: Path) -> None:
        for current, directory_names, file_names in os.walk(path, topdown=False):
            current_path = Path(current)
            for name in file_names:
                (current_path / name).chmod(0o400)
            for name in directory_names:
                (current_path / name).chmod(0o500)
        path.chmod(0o500)

    @staticmethod
    def _set_workspace_writable(path: Path) -> None:
        if not path.exists():
            return
        path.chmod(0o700)
        for current, directory_names, file_names in os.walk(path):
            current_path = Path(current)
            for name in directory_names:
                (current_path / name).chmod(0o700)
            for name in file_names:
                (current_path / name).chmod(0o600)

    async def _cleanup_record(self, record: _WorkerSession) -> None:
        with contextlib.suppress(Exception):
            await self._verifier.close(session_id=record.session.session_id)
        with contextlib.suppress(Exception):
            await record.adapter.close(record.session)
        if record.mode == "readonly":
            with contextlib.suppress(Exception):
                self._set_workspace_writable(record.workspace.workspace_root)
        with contextlib.suppress(Exception):
            record.workspace.destroy()

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        encoded = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
        )
        if len(encoded) > MAX_WORKER_FRAME_BYTES:
            raise CodingWorkerProtocolError(
                "Coding worker response is too large.",
                code="response_too_large",
            )
        writer.write(encoded)
        await writer.drain()

    async def _send_error(self, writer: asyncio.StreamWriter, code: str) -> None:
        await self._send(
            writer,
            {
                "ok": False,
                "code": code,
                "error": "Coding runtime request failed.",
            },
        )

    async def serve_forever(self) -> None:
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._socket_path.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(
            self.handle,
            path=str(self._socket_path),
            limit=MAX_WORKER_FRAME_BYTES + 1,
        )
        os.chmod(self._socket_path, 0o660)
        try:
            async with server:
                await server.serve_forever()
        finally:
            await self.close()
            self._socket_path.unlink(missing_ok=True)


class CodingWorkerClient:
    """FastAPI-side client for the private Coding Runtime Unix socket."""

    def __init__(
        self,
        socket_path: str | Path,
        *,
        timeout: float = 5.0,
    ) -> None:
        self._socket_path = str(socket_path)
        self._timeout = timeout

    async def health(self) -> dict[str, Any]:
        return await self._request({"action": "health"})

    async def create_session(
        self,
        source: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {"action": "create_session"}
        if source is not None:
            request["source"] = source
        return await self._request(request, timeout=130.0)

    async def restore_session(
        self,
        *,
        revision: int,
        patch: str,
        paths: list[str],
        snapshot_fingerprint: str,
        verification: dict[str, Any] | None = None,
        base_patch: str = "",
        base_paths: list[str] | None = None,
        source: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "action": "restore_session",
            "revision": revision,
            "patch": patch,
            "paths": paths,
            "base_patch": base_patch,
            "base_paths": base_paths or [],
            "snapshot_fingerprint": snapshot_fingerprint,
            "verification": verification,
        }
        if source is not None:
            request["source"] = source
        result = await self._request(
            request,
            timeout=130.0,
        )
        if (
            result.get("recovered") is not True
            or not isinstance(result.get("changes"), dict)
        ):
            raise CodingWorkerProtocolError(
                "Coding worker omitted recovered draft state.",
                code="invalid_response",
            )
        return result

    async def recovery_snapshot(self, session_id: str) -> dict[str, Any]:
        result = await self._request(
            {"action": "recovery_snapshot", "session_id": session_id}
        )
        if (
            not isinstance(result.get("snapshot_fingerprint"), str)
            or not isinstance(result.get("changes"), dict)
            or not isinstance(result.get("patch"), str)
            or not isinstance(result.get("base_patch"), str)
            or not isinstance(result.get("cumulative_changes"), dict)
            or not isinstance(result.get("cumulative_patch"), str)
            or (
                result.get("verification") is not None
                and not isinstance(result.get("verification"), dict)
            )
        ):
            raise CodingWorkerProtocolError(
                "Coding worker omitted recovery state.",
                code="invalid_response",
            )
        return result

    async def cancel(self, session_id: str) -> bool:
        result = await self._request(
            {"action": "cancel", "session_id": session_id}
        )
        return result.get("accepted") is True

    async def close(self, session_id: str) -> None:
        await self._request({"action": "close", "session_id": session_id})

    async def changes(self, session_id: str) -> dict[str, Any]:
        result = await self._request(
            {"action": "changes", "session_id": session_id}
        )
        changes = result.get("changes")
        if not isinstance(changes, dict):
            raise CodingWorkerProtocolError(
                "Coding worker omitted draft changes.",
                code="invalid_response",
            )
        return changes

    async def diff(self, session_id: str, path: str, revision: int) -> str:
        result = await self._request(
            {
                "action": "diff",
                "session_id": session_id,
                "path": path,
                "revision": revision,
            }
        )
        diff = result.get("diff")
        if not isinstance(diff, str):
            raise CodingWorkerProtocolError(
                "Coding worker omitted the requested diff.",
                code="invalid_response",
            )
        return diff

    async def patch(
        self,
        session_id: str,
        revision: int,
        *,
        scope: str = "current",
    ) -> str:
        result = await self._request(
            {
                "action": "patch",
                "session_id": session_id,
                "revision": revision,
                "scope": scope,
            }
        )
        patch = result.get("patch")
        if not isinstance(patch, str):
            raise CodingWorkerProtocolError(
                "Coding worker omitted the requested patch.",
                code="invalid_response",
            )
        return patch

    async def checkpoint_cycle(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        result = await self._request(
            {
                "action": "checkpoint_cycle",
                "session_id": session_id,
                "revision": revision,
            }
        )
        changes = result.get("changes")
        if not isinstance(changes, dict):
            raise CodingWorkerProtocolError(
                "Coding worker omitted the next cycle state.",
                code="invalid_response",
            )
        return changes

    async def validate(self, session_id: str) -> dict[str, Any]:
        result = await self._request(
            {"action": "validate", "session_id": session_id}
        )
        changes = result.get("changes")
        if not isinstance(changes, dict):
            raise CodingWorkerProtocolError(
                "Coding worker omitted draft validation.",
                code="invalid_response",
            )
        return changes

    async def discard(self, session_id: str) -> dict[str, Any]:
        result = await self._request(
            {"action": "discard", "session_id": session_id}
        )
        changes = result.get("changes")
        if not isinstance(changes, dict):
            raise CodingWorkerProtocolError(
                "Coding worker omitted the discarded draft state.",
                code="invalid_response",
            )
        return changes

    async def verification_start(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        return await self._verification_request(
            "verification_start",
            session_id,
            revision,
        )

    async def verification_status(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        return await self._verification_request(
            "verification_status",
            session_id,
            revision,
        )

    async def verification_cancel(
        self,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        return await self._verification_request(
            "verification_cancel",
            session_id,
            revision,
        )

    async def _verification_request(
        self,
        action: str,
        session_id: str,
        revision: int,
    ) -> dict[str, Any]:
        response = await self._request(
            {
                "action": action,
                "session_id": session_id,
                "revision": revision,
            }
        )
        verification = response.get("verification")
        if not isinstance(verification, dict):
            raise CodingWorkerProtocolError(
                "Coding worker omitted project verification.",
                code="invalid_response",
            )
        return {
            "verification": verification,
            "accepted": response.get("accepted") is True,
        }

    async def prompt(
        self,
        session_id: str,
        prompt: str,
    ) -> AsyncIterator[CodingEvent]:
        reader, writer = await self._connect()
        try:
            await self._write(
                writer,
                {
                    "action": "prompt",
                    "session_id": session_id,
                    "prompt": prompt,
                },
            )
            while True:
                frame = await self._read(reader)
                self._raise_for_error(frame)
                if frame.get("done") is True:
                    return
                event_data = frame.get("event")
                if not isinstance(event_data, dict):
                    raise CodingWorkerProtocolError(
                        "Coding worker omitted an event.",
                        code="invalid_response",
                    )
                yield _event_from_dict(event_data)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _request(
        self,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        reader, writer = await self._connect()
        try:
            await self._write(writer, payload)
            frame = await asyncio.wait_for(
                self._read(reader),
                timeout=timeout or self._timeout,
            )
            self._raise_for_error(frame)
            return frame
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _connect(
        self,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            return await asyncio.wait_for(
                asyncio.open_unix_connection(
                    self._socket_path,
                    limit=MAX_WORKER_FRAME_BYTES + 1,
                ),
                timeout=self._timeout,
            )
        except (OSError, TimeoutError) as exc:
            raise CodingWorkerError(
                "Coding runtime is unavailable.",
                code="worker_unavailable",
            ) from exc

    @staticmethod
    async def _write(
        writer: asyncio.StreamWriter,
        payload: dict[str, Any],
    ) -> None:
        encoded = (
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
            + b"\n"
        )
        if len(encoded) > MAX_WORKER_FRAME_BYTES:
            raise CodingWorkerProtocolError(
                "Coding worker request is too large.",
                code="invalid_request",
            )
        writer.write(encoded)
        await writer.drain()

    @staticmethod
    async def _read(reader: asyncio.StreamReader) -> dict[str, Any]:
        raw = await reader.readline()
        if not raw or len(raw) > MAX_WORKER_FRAME_BYTES:
            raise CodingWorkerProtocolError(
                "Coding worker response is invalid.",
                code="invalid_response",
            )
        try:
            frame = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CodingWorkerProtocolError(
                "Coding worker response is invalid.",
                code="invalid_response",
            ) from exc
        if not isinstance(frame, dict):
            raise CodingWorkerProtocolError(
                "Coding worker response must be an object.",
                code="invalid_response",
            )
        return frame

    @staticmethod
    def _raise_for_error(frame: dict[str, Any]) -> None:
        if frame.get("ok") is True:
            return
        code = frame.get("code")
        raise CodingWorkerError(
            "Coding runtime request failed.",
            code=code if isinstance(code, str) else "worker_error",
        )


def _validate_local_source_metadata(lease: dict[str, Any]) -> None:
    expected = {
        "lease_id",
        "project_id",
        "name",
        "branch",
        "head",
        "fingerprint",
        "file_count",
        "total_bytes",
        "hidden_files",
        "created_at",
    }
    if set(lease) != expected:
        raise CodingWorkerError(
            "Project snapshot metadata is invalid.",
            code="snapshot_mismatch",
        )
    lease_id = lease["lease_id"]
    project_id = lease["project_id"]
    name = lease["name"]
    branch = lease["branch"]
    head = lease["head"]
    fingerprint = lease["fingerprint"]
    counts = (lease["file_count"], lease["total_bytes"], lease["hidden_files"])
    created_at = lease["created_at"]
    if (
        not _safe_internal_id(lease_id)
        or not _safe_internal_id(project_id)
        or not project_id.startswith("local-")
        or not isinstance(name, str)
        or not name
        or name != name.strip()
        or name != unicodedata.normalize("NFC", name)
        or len(name) > 80
        or any(
            unicodedata.category(character).startswith("C")
            for character in name
        )
        or not isinstance(branch, str)
        or not branch
        or branch != branch.strip()
        or len(branch) > 200
        or any(
            unicodedata.category(character).startswith("C")
            for character in branch
        )
        or not _safe_hex(head, lengths={40, 64})
        or not _safe_hex(fingerprint, lengths={64})
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in counts
        )
        or counts[0] > MAX_PROJECT_SNAPSHOT_FILES
        or counts[1] > MAX_PROJECT_SNAPSHOT_BYTES
        or counts[2] > MAX_PROJECT_SNAPSHOT_FILES
        or isinstance(created_at, bool)
        or not isinstance(created_at, (int, float))
        or not math.isfinite(float(created_at))
        or float(created_at) <= 0
    ):
        raise CodingWorkerError(
            "Project snapshot metadata is invalid.",
            code="snapshot_mismatch",
        )


def _validate_local_snapshot(root: Path, lease: dict[str, Any]) -> str:
    try:
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise CodingWorkerError(
            "Project snapshot is unavailable.",
            code="snapshot_unavailable",
        ) from exc
    if root.is_symlink() or not resolved.is_dir() or resolved.parent == resolved:
        raise CodingWorkerError("Project snapshot is unsafe.", code="snapshot_unsafe")
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    try:
        for path in sorted(resolved.rglob("*")):
            relative = path.relative_to(resolved).as_posix()
            if path.is_symlink() or not project_snapshot_path_is_allowed(relative):
                raise CodingWorkerError(
                    "Project snapshot is unsafe.",
                    code="snapshot_unsafe",
                )
            if path.is_dir():
                continue
            if not path.is_file():
                raise CodingWorkerError(
                    "Project snapshot is unsafe.",
                    code="snapshot_unsafe",
                )
            size = path.stat().st_size
            if size > MAX_PROJECT_SNAPSHOT_FILE_BYTES:
                raise CodingWorkerError(
                    "Project snapshot exceeds its limits.",
                    code="snapshot_limit_exceeded",
                )
            file_count += 1
            total_bytes += size
            if file_count > MAX_PROJECT_SNAPSHOT_FILES or total_bytes > MAX_PROJECT_SNAPSHOT_BYTES:
                raise CodingWorkerError(
                    "Project snapshot exceeds its limits.",
                    code="snapshot_limit_exceeded",
                )
            content = path.read_bytes()
            if len(content) != size:
                raise CodingWorkerError(
                    "Project snapshot changed while loading.",
                    code="snapshot_mismatch",
                )
            if relative == "AGENTS.md":
                if size > MAX_PROJECT_AGENTS_BYTES:
                    raise CodingWorkerError(
                        "Project instructions exceed their limit.",
                        code="snapshot_limit_exceeded",
                    )
                content.decode("utf-8", errors="strict")
            content_hash = hashlib.sha256(content)
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(size).encode("ascii"))
            digest.update(b"\0")
            digest.update(content_hash.digest())
    except CodingWorkerError:
        raise
    except (OSError, UnicodeError) as exc:
        raise CodingWorkerError(
            "Project snapshot is unsafe.",
            code="snapshot_unsafe",
        ) from exc
    if file_count != lease["file_count"] or total_bytes != lease["total_bytes"]:
        raise CodingWorkerError(
            "Project snapshot metadata does not match.",
            code="snapshot_mismatch",
        )
    return digest.hexdigest()


def _safe_internal_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 20 <= len(value) <= 64
        and value.isascii()
        and all(character.isalnum() or character in "-_" for character in value)
    )


def _safe_hex(value: Any, *, lengths: set[int]) -> bool:
    return (
        isinstance(value, str)
        and len(value) in lengths
        and value.isascii()
        and all(character in "0123456789abcdef" for character in value)
    )


def _event_with_project(event: CodingEvent, source: WorkspaceSource) -> CodingEvent:
    data = dict(event.data)
    data["project"] = source.to_public_dict()
    return CodingEvent(
        session_id=event.session_id,
        seq=event.seq,
        kind=event.kind,
        created_at=event.created_at,
        turn_id=event.turn_id,
        data=data,
    )


def _validate_recovered_verification(
    value: Any,
    *,
    revision: int,
    paths: list[str],
) -> dict[str, Any] | None:
    if value is None:
        return None
    keys = {
        "revision",
        "state",
        "result",
        "stale",
        "reason",
        "started_at",
        "finished_at",
        "steps",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise CodingWorkerError(
            "Recovered verification has an invalid shape.",
            code="recovery_invalid",
        )
    reason = value["reason"]
    timestamps = (value["started_at"], value["finished_at"])
    if (
        value["revision"] != revision
        or value["state"] != VerificationState.COMPLETED.value
        or value["result"]
        not in {
            VerificationResult.PASSED.value,
            VerificationResult.FAILED.value,
            VerificationResult.NOT_APPLICABLE.value,
        }
        or value["stale"] is not False
        or (reason is not None and (
            not isinstance(reason, str)
            or SAFE_RECOVERY_REASON.fullmatch(reason) is None
        ))
        or value["finished_at"] is None
        or any(
            timestamp is not None
            and (
                isinstance(timestamp, bool)
                or not isinstance(timestamp, (int, float))
                or not math.isfinite(timestamp)
                or timestamp < 0
            )
            for timestamp in timestamps
        )
        or not isinstance(value["steps"], list)
    ):
        raise CodingWorkerError(
            "Recovered verification is inconsistent.",
            code="recovery_invalid",
        )

    plan = select_verification_plan(paths)
    if value["result"] == VerificationResult.NOT_APPLICABLE.value:
        if plan.reason != "documentation_only" or reason != "documentation_only" or value["steps"]:
            raise CodingWorkerError(
                "Recovered verification does not match the draft.",
                code="recovery_invalid",
            )
        return dict(value)
    if not plan.runnable:
        raise CodingWorkerError(
            "Recovered verification does not match the draft.",
            code="recovery_invalid",
        )

    expected_ids = tuple(step_id.value for step_id in plan.step_ids)
    actual_ids: list[str] = []
    step_results: list[str] = []
    normalized_steps: list[dict[str, Any]] = []
    step_keys = {
        "id",
        "label",
        "state",
        "result",
        "duration_ms",
        "summary",
        "details",
        "truncated",
    }
    for raw in value["steps"]:
        if not isinstance(raw, dict) or set(raw) != step_keys:
            raise CodingWorkerError(
                "Recovered verification step is invalid.",
                code="recovery_invalid",
            )
        step_id = raw["id"]
        try:
            expected_label = VerificationStep(
                step_id=next(item for item in plan.step_ids if item.value == step_id)
            ).to_dict()["label"]
        except (StopIteration, TypeError, ValueError) as exc:
            raise CodingWorkerError(
                "Recovered verification step is invalid.",
                code="recovery_invalid",
            ) from exc
        summary = raw["summary"]
        details = raw["details"]
        duration = raw["duration_ms"]
        if (
            raw["label"] != expected_label
            or raw["state"] != VerificationState.COMPLETED.value
            or raw["result"]
            not in {VerificationResult.PASSED.value, VerificationResult.FAILED.value}
            or (
                duration is not None
                and (
                    isinstance(duration, bool)
                    or not isinstance(duration, int)
                    or not 0 <= duration <= 600_000
                )
            )
            or not isinstance(summary, str)
            or len(summary) > MAX_VERIFICATION_SUMMARY_CHARS
            or sanitize_verification_output(
                summary,
                limit=MAX_VERIFICATION_SUMMARY_CHARS,
                keep_tail=False,
            ).text != summary
            or not isinstance(details, str)
            or len(details) > MAX_VERIFICATION_DETAIL_CHARS
            or sanitize_verification_output(
                details,
                limit=MAX_VERIFICATION_DETAIL_CHARS,
            ).text != details
            or not isinstance(raw["truncated"], bool)
        ):
            raise CodingWorkerError(
                "Recovered verification step is inconsistent.",
                code="recovery_invalid",
            )
        actual_ids.append(step_id)
        step_results.append(raw["result"])
        normalized_steps.append(dict(raw))
    if tuple(actual_ids) != expected_ids or (
        value["result"] == VerificationResult.PASSED.value
        and any(result != VerificationResult.PASSED.value for result in step_results)
    ) or (
        value["result"] == VerificationResult.FAILED.value
        and VerificationResult.FAILED.value not in step_results
    ):
        raise CodingWorkerError(
            "Recovered verification result is inconsistent.",
            code="recovery_invalid",
        )
    return {**value, "steps": normalized_steps}


def _event_from_dict(payload: dict[str, Any]) -> CodingEvent:
    from .models import CodingEventKind

    try:
        return CodingEvent(
            session_id=str(payload["session_id"]),
            seq=int(payload["seq"]),
            kind=CodingEventKind(str(payload["type"])),
            created_at=float(payload["created_at"]),
            turn_id=(
                str(payload["turn_id"])
                if payload.get("turn_id") is not None
                else None
            ),
            data=dict(payload.get("data") or {}),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CodingWorkerProtocolError(
            "Coding worker event is invalid.",
            code="invalid_response",
        ) from exc


async def main() -> None:
    validate_runtime_dependencies()
    await CodingWorkerServer().serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
