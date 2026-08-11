from __future__ import annotations

import asyncio
import inspect
import json
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

import yaml

try:
    from server.agent_workspace.gateway import (
        GatewayCapabilityError,
        GatewayNotConfiguredError,
        GatewayRequestError,
        GatewayTurn,
        OpenAICompatibleGateway,
    )
    from server.agent_workspace.tools import ToolExecutionError
    from server.model_router.api import get_catalog_coordinator
except ImportError:  # pragma: no cover - container package layout
    from agent_workspace.gateway import (
        GatewayCapabilityError,
        GatewayNotConfiguredError,
        GatewayRequestError,
        GatewayTurn,
        OpenAICompatibleGateway,
    )
    from agent_workspace.tools import ToolExecutionError
    from model_router.api import get_catalog_coordinator

from .models import (
    EngineShadowEvent,
    EngineShadowRunCreate,
    EngineShadowRunDetail,
    EngineShadowRunRecord,
    EngineShadowWorkspaceEntry,
    ResolvedShadowModel,
    TERMINAL_STATUSES,
)
from .port import (
    AppBuildEnginePort,
    EnginePortError,
    EngineProtocolError,
    EngineRequest,
    EngineShadowRunSpec,
    EngineUnavailableError,
    EngineWatchdogError,
    NodeUpstreamEnginePort,
)
from .store import (
    EngineShadowConflict,
    EngineShadowNotFound,
    EngineShadowStore,
    EngineShadowStoreError,
)
from .tools import (
    GOAL_RELATIVE_PATH,
    SHADOW_TOOL_DEFINITIONS,
    UpstreamShadowToolBridge,
    validate_shadow_goal_control,
)


CatalogProvider = Callable[[], Any | Awaitable[Any]]


class EngineShadowServiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        public_message: str,
        *,
        status_code: int = 400,
    ) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.status_code = status_code


class EngineShadowService:
    """Host-owned control plane for a pinned Penguin Core Shadow run.

    Model messages and gateway credentials are intentionally memory-only. The
    durable control plane stores aggregate counters, candidate hashes, and
    public error codes, never prompts, model output, tool arguments, or tool
    output.
    """

    _FIXED_SKILLS = ("web-design", "software-engineering")
    _PUBLIC_TERMINAL_ERRORS = {
        "blocked": "The upstream engine is blocked by an unavailable dependency.",
        "budget_limited": "The upstream Goal budget was exhausted.",
        "stopped": "The upstream shadow run was stopped.",
        "failed": "The upstream shadow run failed.",
    }

    def __init__(
        self,
        *,
        store: EngineShadowStore | None = None,
        port: AppBuildEnginePort | None = None,
        gateway: OpenAICompatibleGateway | None = None,
        tool_bridge: UpstreamShadowToolBridge | None = None,
        catalog_provider: CatalogProvider | None = None,
        package_root: Path | None = None,
    ) -> None:
        self.store = store or EngineShadowStore()
        self.port = port or NodeUpstreamEnginePort(package_root=package_root)
        self.gateway = gateway or OpenAICompatibleGateway()
        self.tool_bridge = tool_bridge or UpstreamShadowToolBridge()
        self._catalog_provider = catalog_provider
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._histories: dict[str, list[dict[str, Any]]] = {}
        self._history_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()

    async def create_run(self, payload: EngineShadowRunCreate) -> EngineShadowRunRecord:
        model = await self.resolve_model(payload.model_base_id)
        try:
            record = self.store.create_run(payload, model)
            workspace = self.store.workspace(record.run_id)
            self._initialize_control_files(workspace, record.objective)
            self._materialize_skills(workspace)
        except Exception:
            # Store.create_run already rolls back its own partial directory. If
            # a host-owned control file fails afterwards, terminalize the row
            # instead of leaving a pending run that cannot be stopped.
            if "record" in locals():
                try:
                    self.store.finish(
                        record.run_id,
                        "failed",
                        error_code="shadow_workspace_initialization_failed",
                        public_error="The Shadow Workspace could not be initialized.",
                    )
                except EngineShadowStoreError:
                    pass
            raise

        system_prompt = self._system_prompt(record.objective)
        async with self._history_lock:
            self._histories[record.run_id] = [
                {"role": "system", "content": system_prompt}
            ]
        self.store.mark_running(record.run_id)
        spec = EngineShadowRunSpec(
            run_id=record.run_id,
            session_id=record.session_id,
            objective=record.objective,
            workspace_dir=workspace,
            goal_file_path=workspace / GOAL_RELATIVE_PATH,
            system_prompt=system_prompt,
            thinking_level=record.thinking_level,
            token_budget=record.token_budget,
            max_goal_rounds=record.max_goal_rounds,
            max_task_turns=record.max_task_turns,
            model_base_id=model.invocation_id,
            model_context_window=model.context_window,
            tools=SHADOW_TOOL_DEFINITIONS,
        )
        task = asyncio.create_task(
            self._run(spec, model),
            name=f"upstream-shadow-{record.run_id}",
        )
        async with self._lifecycle_lock:
            self._tasks[record.run_id] = task
        task.add_done_callback(
            lambda finished, run_id=record.run_id: asyncio.create_task(
                self._forget_task(run_id, finished)
            )
        )
        return self.store.get_run(record.run_id)

    async def resolve_model(self, requested_base_id: str) -> ResolvedShadowModel:
        clean = requested_base_id.strip()
        if not clean:
            raise EngineShadowServiceError(
                "model_not_found", "The requested model is not registered.", status_code=404
            )
        try:
            catalog = await self._catalog()
        except Exception as exc:
            raise EngineShadowServiceError(
                "model_catalog_unavailable",
                "The model catalog is currently unavailable.",
                status_code=503,
            ) from exc
        models = getattr(catalog, "models", None)
        if not isinstance(models, list):
            raise EngineShadowServiceError(
                "model_catalog_unavailable",
                "The model catalog did not return a usable model list.",
                status_code=503,
            )
        target = self._normalized_model_key(clean)
        matches = []
        for candidate in models:
            keys = {
                self._normalized_model_key(str(getattr(candidate, field, "") or ""))
                for field in ("invocation_id", "profile_id", "root", "name")
            }
            invocation = str(getattr(candidate, "invocation_id", "") or "")
            suffix = self._normalized_model_key(invocation.rsplit("/", 1)[-1])
            if target in keys or target == suffix:
                matches.append(candidate)
        if not matches:
            raise EngineShadowServiceError(
                "model_not_found", "The requested model is not registered.", status_code=404
            )
        usable = [candidate for candidate in matches if self._candidate_is_usable(candidate)]
        if not usable:
            raise EngineShadowServiceError(
                "model_unavailable",
                "The requested model is not currently available for Tool Calling.",
                status_code=409,
            )
        candidate = sorted(
            usable,
            key=lambda item: (
                self._normalized_model_key(str(getattr(item, "invocation_id", "")))
                != target,
                str(getattr(item, "invocation_id", "")),
            ),
        )[0]
        return ResolvedShadowModel(
            requested_base_id=clean,
            invocation_id=str(candidate.invocation_id),
            context_window=max(32_000, int(candidate.context_length or 128_000)),
            max_output_tokens=max(1_024, min(int(candidate.max_output_tokens or 32_000), 32_000)),
        )

    def list_runs(self, *, limit: int = 100) -> list[EngineShadowRunRecord]:
        return self.store.list_runs(limit=limit)

    def get_detail(self, run_id: str) -> EngineShadowRunDetail:
        return self.store.get_detail(run_id)

    def list_events(
        self, run_id: str, *, after: int = 0, limit: int = 500
    ) -> list[EngineShadowEvent]:
        return self.store.list_events(run_id, after=after, limit=limit)

    def list_workspace(
        self, run_id: str, relative_path: str = ""
    ) -> list[EngineShadowWorkspaceEntry]:
        return self.store.list_workspace(run_id, relative_path)

    def read_workspace_file(self, run_id: str, relative_path: str) -> tuple[str, int]:
        return self.store.read_workspace_file(run_id, relative_path)

    async def stop_run(self, run_id: str) -> EngineShadowRunRecord:
        record = self.store.get_run(run_id)
        if record.status in TERMINAL_STATUSES:
            return record
        await self.port.stop_run(run_id)
        try:
            return self.store.finish(
                run_id,
                "stopped",
                error_code="user_stopped",
                public_error="The upstream shadow run was stopped.",
            )
        except EngineShadowConflict:
            return self.store.get_run(run_id)

    async def shutdown(self) -> None:
        await self.port.shutdown()
        async with self._lifecycle_lock:
            running = list(self._tasks.items())
        for run_id, task in running:
            if not task.done():
                task.cancel()
                try:
                    self.store.finish(
                        run_id,
                        "interrupted",
                        error_code="server_shutdown",
                        public_error="The server stopped while the upstream shadow run was active.",
                    )
                except EngineShadowStoreError:
                    pass
        await asyncio.gather(*(task for _, task in running), return_exceptions=True)
        async with self._history_lock:
            self._histories.clear()

    async def _run(
        self, spec: EngineShadowRunSpec, model: ResolvedShadowModel
    ) -> None:
        try:
            result = await self.port.start_run(
                spec,
                on_event=lambda event_type, payload: self._on_event(
                    spec.run_id, event_type, payload
                ),
                execute_model=lambda request: self._execute_model(request, model),
                execute_tool=lambda request: self._execute_tool(
                    request, spec.workspace_dir
                ),
            )
            candidate_sha256 = ""
            status = result.status
            error_code = ""
            public_error = result.public_error[:1_000]
            if status == "candidate_ready":
                try:
                    validate_shadow_goal_control(
                        spec.workspace_dir,
                        expected_objective=spec.objective,
                        required_status="complete",
                    )
                    candidate_sha256 = self.store.candidate_hash(spec.run_id)
                except (ToolExecutionError, OSError) as exc:
                    status = "failed"
                    error_code = "candidate_integrity_failed"
                    public_error = str(exc)[:1_000]
            elif status != "stopped":
                error_code = f"worker_{status}"
                public_error = public_error or self._PUBLIC_TERMINAL_ERRORS.get(
                    status, "The upstream shadow run failed."
                )
            persisted = self.store.get_run(spec.run_id)
            self.store.finish(
                spec.run_id,
                status,
                candidate_sha256=candidate_sha256,
                error_code=error_code,
                public_error=public_error,
                progress={
                    "goal_round": result.goal_round,
                    "model_turns": result.model_turns,
                    "retry_count": result.retry_count,
                    "token_total": max(persisted.token_total, result.tokens_used),
                    "usage_source": "estimated",
                    "tool_calls": result.tool_calls,
                },
            )
        except asyncio.CancelledError:
            raise
        except EngineWatchdogError:
            self._finish_if_active(
                spec.run_id,
                "budget_limited",
                "host_watchdog_exhausted",
                "The 60-minute host watchdog expired.",
            )
        except EngineUnavailableError:
            self._finish_if_active(
                spec.run_id,
                "blocked",
                "upstream_worker_unavailable",
                "The pinned upstream worker is unavailable.",
            )
        except (EngineProtocolError, EnginePortError):
            self._finish_if_active(
                spec.run_id,
                "failed",
                "upstream_worker_protocol_failed",
                "The pinned upstream worker protocol failed.",
            )
        except Exception:
            self._finish_if_active(
                spec.run_id,
                "failed",
                "shadow_run_failed",
                "The upstream shadow run failed.",
            )
        finally:
            async with self._history_lock:
                self._histories.pop(spec.run_id, None)

    async def _on_event(
        self, run_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        if event_type == "worker_started":
            retry_count = max(0, int(payload.get("retry_count") or 0))
            self._update_if_active(run_id, retry_count=retry_count)
            self.store.append_event(run_id, "worker_started", {"retry_count": retry_count})
            return
        if event_type != "run.progress":
            # Port already strips OmniMessage and trace bodies. Persist only a
            # type marker so the control plane cannot become a model transcript.
            if event_type in {"engine.omni", "engine.trace", "engine.trace_rotated"}:
                return
            self.store.append_event(run_id, event_type, {})
            return
        kind = str(payload.get("kind") or "")
        fields: dict[str, Any] = {}
        event: dict[str, Any] = {"kind": kind}
        if kind == "goal_round":
            fields["goal_round"] = max(0, int(payload.get("rounds") or 0))
            event["goal_round"] = fields["goal_round"]
        elif kind == "token_usage":
            fields["token_total"] = max(0, int(payload.get("tokens_used") or 0))
            fields["usage_source"] = "estimated"
            event["token_total"] = fields["token_total"]
        elif kind == "tool":
            fields["tool_calls"] = max(0, int(payload.get("tool_calls") or 0))
            event["tool_calls"] = fields["tool_calls"]
        if fields:
            self._update_if_active(run_id, **fields)
        self.store.append_event(run_id, "run_progress", event)

    async def _execute_tool(
        self, request: EngineRequest, workspace: Path
    ) -> dict[str, Any]:
        name = request.payload.get("name")
        arguments = request.payload.get("arguments")
        if not isinstance(name, str) or not isinstance(arguments, dict):
            raise ToolExecutionError("Shadow tool request is malformed")
        current = self.store.get_run(request.run_id)
        self._update_if_active(request.run_id, tool_calls=current.tool_calls + 1)
        try:
            result = await self.tool_bridge.execute(
                tool_name=name,
                arguments=arguments,
                workspace=workspace,
            )
        except ToolExecutionError as exc:
            current = self.store.get_run(request.run_id)
            self._update_if_active(
                request.run_id, tool_failures=current.tool_failures + 1
            )
            self.store.append_event(
                request.run_id,
                "tool_completed",
                {
                    "name": name,
                    "ok": False,
                    "error_category": self._tool_error_category(exc),
                },
            )
            raise
        self.store.append_event(
            request.run_id, "tool_completed", {"name": name, "ok": True}
        )
        return {"output": result.output}

    async def _execute_model(
        self, request: EngineRequest, model: ResolvedShadowModel
    ) -> dict[str, Any]:
        raw_messages = request.payload.get("new_messages")
        if not isinstance(raw_messages, list):
            return self._model_failure("malformed", "The upstream model request was malformed.")
        try:
            converted = self._convert_omni_messages(raw_messages)
        except ValueError:
            return self._model_failure("malformed", "The upstream model request was malformed.")
        async with self._history_lock:
            history = list(self._histories.get(request.run_id, ()))
        if not history:
            return self._model_failure("failed", "The upstream model session is unavailable.")
        messages = history + converted
        thinking_parts: list[str] = []

        async def on_delta(kind: str, payload: dict[str, Any]) -> None:
            if kind == "thinking_delta":
                delta = payload.get("delta")
                if isinstance(delta, str):
                    thinking_parts.append(delta)

        try:
            turn = await self.gateway.stream_turn(
                model_id=model.invocation_id,
                messages=messages,
                tools=self._gateway_tools(),
                max_tokens=model.max_output_tokens,
                thinking_level=str(request.payload.get("thinking_level") or "medium"),
                timeout_ms=120_000,
                on_delta=on_delta,
            )
        except GatewayNotConfiguredError:
            return self._model_failure("auth", "The LLM gateway is not configured.")
        except GatewayCapabilityError:
            return self._model_failure(
                "failed", "The selected model does not support native Tool Calling."
            )
        except GatewayRequestError:
            return self._model_failure("failed", "The model gateway request failed.")

        segments = self._turn_segments(turn, thinking_parts)
        assistant_message = self._assistant_history_message(turn)
        async with self._history_lock:
            if request.run_id in self._histories:
                self._histories[request.run_id].extend(converted)
                self._histories[request.run_id].append(assistant_message)
        input_tokens = self._estimate_tokens(messages)
        output_tokens = self._estimate_tokens(
            [assistant_message, {"thinking": "".join(thinking_parts)}]
        )
        current = self.store.get_run(request.run_id)
        self._update_if_active(request.run_id, model_turns=current.model_turns + 1)
        return {
            "segments": segments,
            "usage": {
                "cache_read": 0,
                "cache_write": 0,
                "output": output_tokens,
                "total": input_tokens + output_tokens,
            },
            "outcome": {"status": "completed"},
        }

    async def _catalog(self) -> Any:
        if self._catalog_provider is not None:
            value = self._catalog_provider()
            return await value if inspect.isawaitable(value) else value
        return await get_catalog_coordinator().get_catalog()

    def _initialize_control_files(self, workspace: Path, objective: str) -> None:
        control = workspace / GOAL_RELATIVE_PATH
        self._atomic_text(
            control,
            yaml.safe_dump(
                {"objective": objective, "status": "active"},
                allow_unicode=True,
                sort_keys=False,
            ),
        )

    def _materialize_skills(self, workspace: Path) -> None:
        vendor_skills = (
            Path(__file__).resolve().parent
            / "vendor"
            / "penguin_harness"
            / "packages"
            / "skills"
            / "skills"
        )
        target_root = workspace / ".modelmirror" / "skills"
        target_root.mkdir(parents=True, exist_ok=True)
        for skill_id in self._FIXED_SKILLS:
            source = vendor_skills / skill_id
            if not source.is_dir() or not (source / "SKILL.md").is_file():
                raise EngineShadowServiceError(
                    "upstream_skill_missing",
                    f"The pinned upstream Skill is missing: {skill_id}.",
                    status_code=503,
                )
            shutil.copytree(source, target_root / skill_id, dirs_exist_ok=False)

    @staticmethod
    def _system_prompt(objective: str) -> str:
        return (
            "You are the pinned Penguin Core execution engine hosted by ModelMirror.\n"
            "Work only inside the supplied Shadow Workspace. This is an unverified, "
            "unpublished shadow build: never claim that Browser acceptance, publication, "
            "deployment, AppVersion creation, or Artifact promotion occurred.\n"
            "Read .modelmirror/skills/web-design/SKILL.md and "
            ".modelmirror/skills/software-engineering/SKILL.md before editing candidate files.\n"
            "Use PLAN.md for durable implementation state. Treat .modelmirror/GOAL.yaml as "
            "the authority: its objective is immutable and only its status may be changed.\n"
            "The R3R-1 candidate contract is one self-contained index.html at the Shadow "
            "Workspace root. PLAN.md and other scratch files are not part of its candidate hash.\n"
            "Build and inspect real files, verify your own work with the available file tools, "
            "and set GOAL status to complete only when the requested candidate is ready for "
            "ModelMirror host review.\n\nObjective:\n"
            + objective
        )

    @staticmethod
    def _tool_error_category(error: ToolExecutionError) -> str:
        message = str(error).lower()
        if "not found" in message or "missing" in message:
            return "not_found"
        if "not unique" in message:
            return "conflict"
        if "size limit" in message or "exceeds" in message:
            return "size_limit"
        if any(
            marker in message
            for marker in (
                "safe file",
                "safe workspace-relative",
                "symbolic link",
                "read-only",
                "not allowed",
            )
        ):
            return "unsafe_path"
        if any(marker in message for marker in ("invalid", "malformed", "must remain", "cannot be empty")):
            return "validation"
        return "unknown"

    @staticmethod
    def _convert_omni_messages(raw_messages: Iterable[Any]) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in raw_messages:
            if not isinstance(message, dict) or message.get("type") != "model_msg":
                continue
            payload = message.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("invalid OmniMessage payload")
            payload_type = payload.get("type")
            if payload_type == "text":
                role = payload.get("role")
                text = payload.get("text")
                if role not in {"user", "assistant"} or not isinstance(text, str):
                    raise ValueError("invalid text message")
                converted.append({"role": role, "content": text})
            elif payload_type == "tool_call":
                name = payload.get("name")
                arguments = payload.get("arguments")
                call_id = payload.get("tool_call_id")
                if not all(isinstance(value, str) and value for value in (name, arguments, call_id)):
                    raise ValueError("invalid tool call")
                converted.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ],
                    }
                )
            elif payload_type == "tool_call_output":
                output = payload.get("output")
                call_id = payload.get("tool_call_id")
                if not isinstance(output, str) or not isinstance(call_id, str) or not call_id:
                    raise ValueError("invalid tool output")
                converted.append(
                    {"role": "tool", "tool_call_id": call_id, "content": output}
                )
            elif payload_type in {
                "thinking",
                "partial_text",
                "partial_thinking",
                "partial_tool_call",
                "partial_tool_call_output",
                "inline_thinking",
            }:
                continue
            elif payload_type in {"image_url", "inline_data"}:
                raise ValueError("R3R-1 does not expose image input")
        return converted

    @staticmethod
    def _gateway_tools() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                },
            }
            for tool in SHADOW_TOOL_DEFINITIONS
        ]

    @staticmethod
    def _turn_segments(
        turn: GatewayTurn, thinking_parts: list[str]
    ) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        thinking = "".join(thinking_parts)
        if thinking:
            segments.append({"type": "thinking", "text": thinking})
        if turn.content:
            segments.append({"type": "text", "text": turn.content})
        for call in turn.tool_calls:
            segments.append(
                {
                    "type": "tool_call",
                    "name": call.name,
                    "arguments": call.arguments,
                    "tool_call_id": call.call_id,
                }
            )
        return segments

    @staticmethod
    def _assistant_history_message(turn: GatewayTurn) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant", "content": turn.content}
        if turn.tool_calls:
            message["tool_calls"] = [call.as_message_value() for call in turn.tool_calls]
        return message

    @staticmethod
    def _estimate_tokens(value: Any) -> int:
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return max(1, math.ceil(len(serialized.encode("utf-8")) / 4))

    @staticmethod
    def _model_failure(status: str, message: str) -> dict[str, Any]:
        return {
            "segments": [],
            "usage": {"cache_read": 0, "cache_write": 0, "output": 0, "total": 0},
            "outcome": {"status": status, "error_message": message},
        }

    @staticmethod
    def _candidate_is_usable(candidate: Any) -> bool:
        return bool(getattr(candidate, "invocable", False)) and str(
            getattr(candidate, "availability", "")
        ) in {"live", "degraded"} and "chat" in set(
            getattr(candidate, "operations", ()) or ()
        ) and str(getattr(candidate, "interaction_status", "")) == "ready"

    @staticmethod
    def _normalized_model_key(value: str) -> str:
        return value.strip().lower().replace("_", "-")

    @staticmethod
    def _atomic_text(path: Path, content: str) -> None:
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

    def _update_if_active(self, run_id: str, **fields: Any) -> None:
        try:
            self.store.update_progress(run_id, **fields)
        except EngineShadowConflict:
            pass

    def _finish_if_active(
        self, run_id: str, status: str, error_code: str, public_error: str
    ) -> None:
        try:
            self.store.finish(
                run_id,
                status,
                error_code=error_code,
                public_error=public_error,
            )
        except (EngineShadowConflict, EngineShadowNotFound):
            pass

    async def _forget_task(
        self, run_id: str, finished: asyncio.Task[None]
    ) -> None:
        try:
            finished.exception()
        except (asyncio.CancelledError, Exception):
            pass
        async with self._lifecycle_lock:
            if self._tasks.get(run_id) is finished:
                self._tasks.pop(run_id, None)
