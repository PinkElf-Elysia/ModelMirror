from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import shlex
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

try:  # Harbor is an evaluation-only dependency.
    from harbor.agents.installed.base import with_prompt_template
    from harbor.agents.installed.opencode import OpenCode
    from harbor.environments.base import BaseEnvironment
    from harbor.models.agent.context import AgentContext

    HARBOR_AVAILABLE = True
except ModuleNotFoundError:
    OpenCode = object  # type: ignore[assignment,misc]
    BaseEnvironment = Any  # type: ignore[assignment,misc]
    AgentContext = Any  # type: ignore[assignment,misc]
    HARBOR_AVAILABLE = False

    def with_prompt_template(function: Any) -> Any:
        return function


NATIVE_OPENCODE_VERSION = "1.18.9"
CONTROL_SCHEMA = "modelmirror-native-opencode-control/v1"
SERVER_PORT = 43781
REMOTE_ROOT = "/tmp/modelmirror-native-opencode"
REMOTE_HELPER = f"{REMOTE_ROOT}/control.mjs"
REMOTE_EVENTS = f"{REMOTE_ROOT}/events.jsonl"
REMOTE_TOOL_SHELL = f"{REMOTE_ROOT}/tool-shell"
REMOTE_TOOL_HOME = "/tmp/modelmirror-native-tool-home"
FAULT_COMMAND = "python -m build_index"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_sha256(payload: object) -> str:
    return _sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _stdout(result: object) -> str:
    value = getattr(result, "stdout", "") or ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


class NativeOpenCodeHarnessAgent(OpenCode):  # type: ignore[misc]
    """Harbor OpenCode 1.18.9 adapter with an auditable Session controller.

    The controller uses only the pinned server API and persists a deliberately
    reduced event ledger. Vendor reasoning frames, credentials, and raw model
    responses are never copied into Harbor artifacts.
    """

    SUPPORTS_ATIF = True
    SUPPORTS_RESUME = False

    async def install(self, environment: BaseEnvironment) -> None:
        result = await self.exec_as_root(
            environment,
            command=(
                'test "$(id -u)" = 0; '
                "test -x /usr/local/bin/node; "
                "test -x /usr/local/bin/opencode; "
                f'test "$(opencode --version)" = "{NATIVE_OPENCODE_VERSION}"'
            ),
        )
        if int(getattr(result, "return_code", 0)) != 0:
            raise RuntimeError("native OpenCode offline runtime is unavailable")

    def __init__(
        self,
        *args: Any,
        benchmark_root: str | None = None,
        poll_seconds: float = 0.2,
        timeout_seconds: int = 900,
        **kwargs: Any,
    ) -> None:
        if not HARBOR_AVAILABLE:
            raise RuntimeError("Harbor 0.21.0 is required")
        super().__init__(*args, **kwargs)
        self._benchmark_root = Path(
            benchmark_root
            or os.getenv(
                "MODELMIRROR_HARBOR_BENCHMARK_ROOT",
                "benchmarks/coding-worker-v18",
            )
        ).resolve()
        self._poll_seconds = max(0.1, float(poll_seconds))
        self._timeout_seconds = max(30, int(timeout_seconds))

    async def setup(self, environment: BaseEnvironment) -> None:
        await super().setup(environment)
        helper = Path(__file__).with_name("coding_worker_native_control.mjs")
        if not helper.is_file():
            raise RuntimeError("native OpenCode control helper is unavailable")
        await self.exec_as_agent(
            environment,
            command=(
                f"mkdir -p {shlex.quote(REMOTE_ROOT)} && "
                f"chmod 700 {shlex.quote(REMOTE_ROOT)}"
            ),
        )
        await self._upload_agent_owned_file(environment, helper, REMOTE_HELPER)
        shell_wrapper = Path(__file__).with_name(
            "coding_worker_native_shell_wrapper.sh"
        )
        if not shell_wrapper.is_file():
            raise RuntimeError("native tool shell wrapper is unavailable")
        await self._upload_agent_owned_file(
            environment, shell_wrapper, REMOTE_TOOL_SHELL
        )
        await self.exec_as_agent(
            environment,
            command=(
                f"chmod 500 {shlex.quote(REMOTE_HELPER)} "
                f"{shlex.quote(REMOTE_TOOL_SHELL)}"
            ),
        )

    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        binding = await self._read_binding(environment)
        task_id = str(binding.get("task_id") or "")
        if _sha256(instruction.encode("utf-8")) != binding.get("instruction_sha256"):
            raise RuntimeError("native instruction does not match its frozen fixture")
        scenario_path = self._benchmark_root / "tasks" / task_id / "scenario.json"
        scenario = self._load_scenario(scenario_path)
        observed_scenario_sha256 = (
            _sha256(scenario_path.read_bytes()) if scenario_path.is_file() else None
        )
        if observed_scenario_sha256 != binding.get("scenario_sha256"):
            raise RuntimeError("native scenario does not match its frozen fixture")
        if not self.model_name or "/" not in self.model_name:
            raise RuntimeError("native model name must include its provider")
        provider_id, model_id = self.model_name.split("/", 1)
        workspace = str(environment.task_env_config.workdir or "/workspace")
        directory_query = f"directory={quote(workspace, safe='')}"
        environment_id = str(environment.environment_id)
        if len(environment_id) != 32 or any(
            character not in "0123456789abcdef" for character in environment_id
        ):
            raise RuntimeError("Harbor environment identity is invalid")

        env = dict(self.model_connection.env)
        env.update(
            {
                "OPENCODE_FAKE_VCS": "git",
                "XDG_DATA_HOME": f"{REMOTE_ROOT}/xdg-data",
                "XDG_STATE_HOME": f"{REMOTE_ROOT}/xdg-state",
                "XDG_CACHE_HOME": f"{REMOTE_ROOT}/xdg-cache",
                "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
                "OPENCODE_PURE": "1",
                "OPENCODE_DISABLE_AUTOUPDATE": "1",
                "OPENCODE_DISABLE_AUTOCOMPACT": "1",
                "OPENCODE_DISABLE_MODELS_FETCH": "1",
                "OPENCODE_AUTH_CONTENT": "{}",
            }
        )
        await self._prepare_shell_boundary(environment, workspace)
        if self._fault_action(scenario) is not None:
            await self._arm_fault_gate(environment)
        register = self._build_register_config_command()
        if register:
            await self.exec_as_agent(environment, command=register, env=env)

        control: list[dict[str, Any]] = []
        session_id: str | None = None
        try:
            await self._start_server(environment, env, directory_query)
            await self._assert_version(environment, directory_query)
            created = await self._request(
                environment,
                "POST",
                f"/session?{directory_query}",
                {"title": f"ModelMirror V18 {task_id}"},
            )
            session_id = str(created.get("id") or "")
            if not session_id.startswith("ses"):
                raise RuntimeError("native OpenCode returned an invalid session")
            await self._request(
                environment,
                "POST",
                f"/session/{quote(session_id)}/prompt_async?{directory_query}",
                {
                    "model": {"providerID": provider_id, "modelID": model_id},
                    "agent": "build",
                    "parts": [{"type": "text", "text": instruction}],
                },
            )
            control.append(
                {
                    "event_type": "initial_prompt",
                    "session_id": session_id,
                    "message_sha256": _sha256(instruction.encode("utf-8")),
                }
            )
            await self._drive_session(
                environment=environment,
                env=env,
                directory_query=directory_query,
                session_id=session_id,
                provider_id=provider_id,
                model_id=model_id,
                scenario=scenario,
                control=control,
            )
            messages = await self._request(
                environment,
                "GET",
                f"/session/{quote(session_id)}/message?{directory_query}",
            )
            if not isinstance(messages, list):
                raise RuntimeError("native OpenCode message ledger is invalid")
            events = await self._events(environment)
            self._validate_scenario(
                scenario=scenario,
                session_id=session_id,
                events=events,
                control=control,
            )
            public_messages = self._public_message_projection(messages)
            trajectory = self._trajectory(
                instruction=instruction,
                session_id=session_id,
                messages=messages,
            )
            self._write_artifacts(
                trajectory=trajectory,
                ledger={
                    "schema": CONTROL_SCHEMA,
                    "run_binding": {
                        "task_id": task_id,
                        "instruction_sha256": binding["instruction_sha256"],
                        "scenario_sha256": binding["scenario_sha256"],
                        "session_id": session_id,
                        "environment_id": environment_id,
                        "model_name": self.model_name,
                        "opencode_version": NATIVE_OPENCODE_VERSION,
                    },
                    "events": events,
                    "control": control,
                    "scenario_contract": {
                        "required_events": list(
                            scenario.get("required_events", []) if scenario else []
                        ),
                        "action_ids": [
                            str(item["action_id"])
                            for item in (scenario.get("actions", []) if scenario else [])
                        ],
                        "question_prompt_sha256": [
                            str(item["prompt_sha256"])
                            for item in (scenario.get("questions", []) if scenario else [])
                        ],
                    },
                    "public_messages": public_messages,
                    "public_messages_sha256": _canonical_sha256(public_messages),
                },
            )
            metrics = trajectory.get("final_metrics", {})
            context.n_input_tokens = int(metrics.get("total_prompt_tokens") or 0)
            context.n_output_tokens = int(metrics.get("total_completion_tokens") or 0)
            context.n_cache_tokens = int(metrics.get("total_cached_tokens") or 0)
            context.cost_usd = metrics.get("total_cost_usd")
        finally:
            try:
                await self._stop_server(environment)
            finally:
                await self.exec_as_agent(
                    environment,
                    command=(
                        f"rm -rf -- {shlex.quote(REMOTE_ROOT)} "
                        f"{shlex.quote(REMOTE_TOOL_HOME)}"
                    ),
                )

    def populate_context_post_run(self, context: AgentContext) -> None:
        # run() already wrote the normalized ATIF trajectory and usage.
        return None

    async def _drive_session(
        self,
        *,
        environment: BaseEnvironment,
        env: Mapping[str, str],
        directory_query: str,
        session_id: str,
        provider_id: str,
        model_id: str,
        scenario: dict[str, Any] | None,
        control: list[dict[str, Any]],
    ) -> None:
        deadline = time.monotonic() + self._timeout_seconds
        busy_seen = False
        answered: set[str] = set()
        actions_done: set[str] = set()
        compaction_requested = False
        compaction_seen = False
        faulted_call_id: str | None = None
        faulted_intent_sha256: str | None = None
        reconciled = False
        resume_sent = False
        resume_event_offset: int | None = None
        resume_busy_seen = False
        idle_after_work = False
        fault_enabled = self._fault_action(scenario) is not None

        while time.monotonic() < deadline:
            events = await self._events(environment)
            relevant = [event for event in events if self._event_session(event) == session_id]
            if resume_event_offset is not None:
                resume_busy_seen = resume_busy_seen or any(
                    event.get("type") == "session.status"
                    and self._event_session(event) == session_id
                    and (event.get("properties") or {}).get("status", {}).get("type")
                    == "busy"
                    for event in events[resume_event_offset:]
                )
            if any(event.get("type") in {"session.error", "session.cancelled"} for event in relevant):
                raise RuntimeError("native OpenCode session failed")
            if any(
                event.get("type") == "session.status"
                and (event.get("properties") or {}).get("status", {}).get("type") == "busy"
                for event in relevant
            ):
                busy_seen = True

            for event in relevant:
                if event.get("type") != "question.asked":
                    continue
                properties = event.get("properties") or {}
                request_id = str(properties.get("id") or "")
                if not request_id or request_id in answered:
                    continue
                question_spec = self._question_spec(properties, scenario)
                control.append(
                    {
                        "event_type": "question_requested",
                        "interaction_id": request_id,
                        "prompt_sha256": question_spec["prompt_sha256"],
                    }
                )
                await self._request(
                    environment,
                    "POST",
                    f"/question/{quote(request_id)}/reply?{directory_query}",
                    {"answers": [[question_spec["selected_option_id"]]]},
                )
                answered.add(request_id)
                control.append(
                    {
                        "event_type": "question_resolved",
                        "interaction_id": request_id,
                    }
                )

            actions = scenario.get("actions", []) if scenario else []
            for action in actions:
                action_id = str(action["action_id"])
                if action_id in actions_done or action.get("when_state") != "running":
                    continue
                if action.get("kind") == "message" and busy_seen:
                    await self._request(
                        environment,
                        "POST",
                        f"/session/{quote(session_id)}/prompt_async?{directory_query}",
                        {
                            "model": {"providerID": provider_id, "modelID": model_id},
                            "agent": "build",
                            "parts": [{"type": "text", "text": action["message"]}],
                        },
                    )
                    actions_done.add(action_id)
                    control.append(
                        {
                            "event_type": "steering_sent",
                            "action_id": action_id,
                            "message_sha256": _sha256(
                                str(action["message"]).encode("utf-8")
                            ),
                        }
                    )

            fault_intent = self._fault_tool_intent(relevant, scenario)
            fault_result = (
                await self._fault_result(environment) if fault_enabled else None
            )
            if fault_intent is not None and fault_result is not None and faulted_call_id is None:
                faulted_call_id, _command, fault_arguments = fault_intent
                faulted_intent_sha256 = _canonical_sha256(
                    {"function_name": "bash", "arguments": fault_arguments}
                )
                control.append(
                    {
                        "event_type": "operation_unknown",
                        "operation_id": faulted_call_id,
                        "intent_sha256": faulted_intent_sha256,
                    }
                )
                await self._stop_server(environment)
                await self._disarm_fault_gate(environment)
                control.append(
                    {
                        "event_type": "component_fault_injected",
                        "component": "executor",
                        "operation_id": faulted_call_id,
                    }
                )
                await self._start_server(environment, env, directory_query)
                await self._assert_version(environment, directory_query)

            if faulted_call_id is not None and not reconciled:
                if faulted_intent_sha256 is None:
                    raise RuntimeError("native fault intent binding is unavailable")
                messages = await self._request(
                    environment,
                    "GET",
                    f"/session/{quote(session_id)}/message?{directory_query}",
                )
                if self._message_has_call(messages, faulted_call_id):
                    reconciled = True
                    control.append(
                        {
                            "event_type": "operation_reconciled",
                            "operation_id": faulted_call_id,
                            "intent_sha256": faulted_intent_sha256,
                            "result_sha256": _canonical_sha256(
                                {"command": FAULT_COMMAND, "exit_code": 0}
                            ),
                        }
                    )
            if reconciled and not resume_sent:
                resume_event_offset = len(events)
                idle_after_work = False
                resume_message = (
                    "Resume after the injected component restart. The completed "
                    "command was reconciled; do not run it again. Finish the task "
                    "and run only the remaining frozen visible check."
                )
                await self._request(
                    environment,
                    "POST",
                    f"/session/{quote(session_id)}/prompt_async?{directory_query}",
                    {
                        "model": {"providerID": provider_id, "modelID": model_id},
                        "agent": "build",
                        "parts": [
                            {
                                "type": "text",
                                "text": resume_message,
                            }
                        ],
                    },
                )
                resume_sent = True
                control.append(
                    {
                        "event_type": "resume_sent",
                        "operation_id": faulted_call_id,
                        "message_sha256": _sha256(resume_message.encode("utf-8")),
                    }
                )

            status = await self._request(environment, "GET", f"/session/status?{directory_query}")
            status_info = status.get(session_id) if isinstance(status, dict) else None
            idle = not isinstance(status_info, dict) or status_info.get("type") == "idle"
            if idle and busy_seen and (not resume_sent or resume_busy_seen):
                idle_after_work = True

            steering_done = any(item.get("event_type") == "steering_sent" for item in control)
            if steering_done and idle and not compaction_requested:
                await self._request(
                    environment,
                    "POST",
                    f"/session/{quote(session_id)}/summarize?{directory_query}",
                    {"providerID": provider_id, "modelID": model_id, "auto": False},
                )
                compaction_requested = True
                idle_after_work = False
                control.append({"event_type": "compaction_requested"})
            if any(event.get("type") == "session.compacted" for event in relevant):
                if not compaction_seen:
                    compaction_seen = True
                    control.append({"event_type": "context_compacted"})

            scenario_complete = self._scenario_actions_complete(
                scenario=scenario,
                actions_done=actions_done,
                answered=answered,
                compaction_seen=compaction_seen,
                reconciled=reconciled,
                resume_sent=resume_sent,
                resume_busy_seen=resume_busy_seen,
            )
            if idle_after_work and idle and scenario_complete:
                return
            await asyncio.sleep(self._poll_seconds)
        raise RuntimeError("native OpenCode session timed out")

    async def _start_server(
        self,
        environment: BaseEnvironment,
        env: Mapping[str, str],
        directory_query: str,
    ) -> None:
        command = (
            f"mkdir -p {shlex.quote(REMOTE_ROOT)}; umask 077; "
            "command -v setsid >/dev/null; "
            f"if [ ! -s {shlex.quote(f'{REMOTE_ROOT}/server-password')} ]; then "
            "node -e \"process.stdout.write(require('crypto').randomBytes(32).toString('base64url'))\" "
            f"> {shlex.quote(f'{REMOTE_ROOT}/server-password')}; fi; "
            f"OPENCODE_SERVER_PASSWORD=\"$(cat {shlex.quote(f'{REMOTE_ROOT}/server-password')})\" "
            f"nohup setsid opencode serve --hostname 127.0.0.1 --port {SERVER_PORT} "
            f"> {shlex.quote(f'{REMOTE_ROOT}/server.log')} 2>&1 </dev/null & "
            f"echo $! > {shlex.quote(f'{REMOTE_ROOT}/server.pid')}"
        )
        await self.exec_as_agent(environment, command=command, env=dict(env))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                await self._request(
                    environment,
                    "GET",
                    "/global/health",
                    timeout_ms=2_000,
                )
                break
            except RuntimeError:
                await asyncio.sleep(0.1)
        else:
            raise RuntimeError("native OpenCode server did not become ready")
        collector = (
            f"rm -f {shlex.quote(f'{REMOTE_ROOT}/collector.ready')}; "
            f"nohup setsid node {shlex.quote(REMOTE_HELPER)} collect "
            f"{shlex.quote(f'/event?{directory_query}')} "
            f"> {shlex.quote(f'{REMOTE_ROOT}/collector.log')} 2>&1 </dev/null & "
            f"echo $! > {shlex.quote(f'{REMOTE_ROOT}/collector.pid')}"
        )
        await self.exec_as_agent(environment, command=collector)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            ready = await environment.exec(
                command=f"test -s {shlex.quote(f'{REMOTE_ROOT}/collector.ready')}"
            )
            if int(getattr(ready, "return_code", 1)) == 0:
                return
            await asyncio.sleep(0.05)
        raise RuntimeError("native OpenCode event collector did not become ready")

    async def _stop_server(self, environment: BaseEnvironment) -> None:
        command = self._stop_server_command()
        result = await self.exec_as_agent(environment, command=command)
        if int(getattr(result, "return_code", 0)) != 0:
            raise RuntimeError("native OpenCode process group did not stop cleanly")

    @staticmethod
    def _stop_server_command() -> str:
        return (
            f"for name in collector server; do file={shlex.quote(REMOTE_ROOT)}/$name.pid; "
            "if [ -s \"$file\" ]; then pid=$(cat \"$file\"); "
            "case \"$pid\" in ''|*[!0-9]*) exit 1;; esac; "
            "if [ \"$pid\" -le 1 ]; then exit 1; fi; "
            "kill -TERM -\"$pid\" 2>/dev/null || true; "
            "for i in 1 2 3 4 5; do kill -0 -\"$pid\" 2>/dev/null || break; sleep 0.1; done; "
            "kill -KILL -\"$pid\" 2>/dev/null || true; "
            "for i in 1 2 3 4 5; do kill -0 -\"$pid\" 2>/dev/null || break; sleep 0.1; done; "
            "if kill -0 -\"$pid\" 2>/dev/null; then exit 1; fi; "
            "rm -f \"$file\"; fi; done"
        )

    async def _assert_version(self, environment: BaseEnvironment, directory_query: str) -> None:
        health = await self._request(environment, "GET", "/global/health")
        if health != {"healthy": True, "version": NATIVE_OPENCODE_VERSION}:
            raise RuntimeError("native OpenCode version is not fixed at 1.18.9")

    async def _request(
        self,
        environment: BaseEnvironment,
        method: str,
        target: str,
        payload: object | None = None,
        *,
        timeout_ms: int | None = None,
    ) -> Any:
        encoded = ""
        if payload is not None:
            encoded = base64.urlsafe_b64encode(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            ).decode("ascii").rstrip("=")
        command = (
            f"node {shlex.quote(REMOTE_HELPER)} request {shlex.quote(method)} "
            f"{shlex.quote(target)}"
            + (f" {shlex.quote(encoded)}" if encoded else "")
        )
        try:
            request_env = (
                {"MODELMIRROR_NATIVE_CONTROL_TIMEOUT_MS": str(timeout_ms)}
                if timeout_ms is not None
                else None
            )
            result = await self.exec_as_agent(
                environment,
                command=command,
                env=request_env,
            )
            envelope = json.loads(_stdout(result))
        except Exception as exc:
            raise RuntimeError("native OpenCode control request failed") from exc
        if not isinstance(envelope, dict) or "body" not in envelope:
            raise RuntimeError("native OpenCode control response is invalid")
        return envelope["body"]

    async def _events(self, environment: BaseEnvironment) -> list[dict[str, Any]]:
        result = await environment.exec(
            command=f"test ! -f {shlex.quote(REMOTE_EVENTS)} || cat {shlex.quote(REMOTE_EVENTS)}"
        )
        if int(getattr(result, "return_code", 0)) != 0:
            raise RuntimeError("native OpenCode event ledger is unavailable")
        events: list[dict[str, Any]] = []
        for line in _stdout(result).splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError("native OpenCode event ledger is corrupt") from exc
            if not isinstance(item, dict):
                raise RuntimeError("native OpenCode event ledger is invalid")
            events.append(item)
        return events

    @staticmethod
    async def _read_binding(environment: BaseEnvironment) -> dict[str, Any]:
        result = await environment.exec(command="cat /opt/modelmirror/source.json")
        try:
            value = json.loads(_stdout(result))
        except json.JSONDecodeError as exc:
            raise RuntimeError("native source binding is invalid") from exc
        if int(getattr(result, "return_code", 0)) != 0 or not isinstance(value, dict):
            raise RuntimeError("native source binding is unavailable")
        return value

    @staticmethod
    def _load_scenario(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("native scenario is invalid")
        return value

    @staticmethod
    def _event_session(event: Mapping[str, Any]) -> str | None:
        properties = event.get("properties")
        return str(properties.get("sessionID")) if isinstance(properties, Mapping) and properties.get("sessionID") else None

    @staticmethod
    def _question_spec(
        properties: Mapping[str, Any], scenario: dict[str, Any] | None
    ) -> dict[str, str]:
        expected = scenario.get("questions", []) if scenario else []
        questions = properties.get("questions")
        if len(expected) != 1 or not isinstance(questions, list) or len(questions) != 1:
            raise RuntimeError("native OpenCode asked an unfrozen question")
        question = questions[0]
        if not isinstance(question, dict) or not isinstance(question.get("question"), str):
            raise RuntimeError("native OpenCode question is invalid")
        prompt_sha256 = _sha256(question["question"].encode("utf-8"))
        expected_question = expected[0]
        selected = str(expected_question["selected_option_id"])
        options = question.get("options")
        labels = {
            str(item.get("label"))
            for item in options or []
            if isinstance(item, dict) and item.get("label") is not None
        }
        if prompt_sha256 != expected_question.get("prompt_sha256") or selected not in labels:
            raise RuntimeError("native OpenCode question does not match the frozen answer")
        return {"prompt_sha256": prompt_sha256, "selected_option_id": selected}

    @staticmethod
    def _fault_action(scenario: dict[str, Any] | None) -> dict[str, Any] | None:
        actions = scenario.get("actions", []) if scenario else []
        fault = next((item for item in actions if item.get("kind") == "component_fault"), None)
        if fault is None:
            return None
        approval = fault.get("approval") or {}
        if (
            fault.get("component") != "executor"
            or fault.get("point") != "after_side_effect_before_receipt"
            or approval
            != {
                "script": FAULT_COMMAND,
                "cwd": ".",
                "mode": "mutate",
                "timeout_seconds": 120,
            }
        ):
            raise RuntimeError("native component fault is not the frozen exact gate")
        return fault

    async def _prepare_shell_boundary(
        self, environment: BaseEnvironment, workspace: str
    ) -> None:
        if workspace != "/workspace":
            raise RuntimeError("native task workspace is not the frozen mount")
        command = (
            "test \"$(id -u)\" = 0; "
            "test \"$(id -u nobody)\" = 65534; "
            "test \"$(id -g nobody)\" = 65534; "
            "test -x /usr/bin/setpriv; test -x /usr/bin/env; "
            f"mkdir -p {shlex.quote(REMOTE_TOOL_HOME)}; "
            f"chown 65534:65534 {shlex.quote(REMOTE_TOOL_HOME)}; "
            f"chmod 700 {shlex.quote(REMOTE_TOOL_HOME)}; "
            f"chown -h -R -P 65534:65534 {shlex.quote(workspace)}"
        )
        result = await self.exec_as_agent(environment, command=command)
        if int(getattr(result, "return_code", 0)) != 0:
            raise RuntimeError("native tool shell boundary is unavailable")

    async def _arm_fault_gate(self, environment: BaseEnvironment) -> None:
        result = await self.exec_as_agent(
            environment,
            command=(
                f": > {shlex.quote(f'{REMOTE_ROOT}/fault.arm')}; "
                f"chmod 600 {shlex.quote(f'{REMOTE_ROOT}/fault.arm')}"
            ),
        )
        if int(getattr(result, "return_code", 0)) != 0:
            raise RuntimeError("native fault gate could not be armed")

    @staticmethod
    async def _disarm_fault_gate(environment: BaseEnvironment) -> None:
        result = await environment.exec(
            command=f"rm -f -- {shlex.quote(f'{REMOTE_ROOT}/fault.arm')}"
        )
        if int(getattr(result, "return_code", 1)) != 0:
            raise RuntimeError("native fault gate could not be disarmed")

    @staticmethod
    async def _fault_result(environment: BaseEnvironment) -> dict[str, Any] | None:
        result = await environment.exec(
            command=(
                f"if [ -f {shlex.quote(f'{REMOTE_ROOT}/fault.result')} ]; then "
                f"cat {shlex.quote(f'{REMOTE_ROOT}/fault.result')}; fi"
            )
        )
        if int(getattr(result, "return_code", 1)) != 0:
            raise RuntimeError("native fault result is unavailable")
        content = _stdout(result)
        if not content:
            return None
        if content != f"command={FAULT_COMMAND}\nexit=0\n":
            raise RuntimeError("native fault result is invalid")
        return {"command": FAULT_COMMAND, "exit_code": 0}

    @staticmethod
    def _fault_tool_intent(
        events: list[dict[str, Any]], scenario: dict[str, Any] | None
    ) -> tuple[str, str, dict[str, Any]] | None:
        fault = NativeOpenCodeHarnessAgent._fault_action(scenario)
        if fault is None:
            return None
        expected = fault.get("approval") or {}
        command = expected.get("script")
        for event in events:
            if event.get("type") not in {"message.part.updated", "message.part.delta"}:
                continue
            part = (event.get("properties") or {}).get("part") or {}
            state = part.get("state") or {}
            if (
                part.get("tool") == "bash"
                and state.get("status") in {"pending", "running"}
                and (state.get("input") or {}).get("command") == command
                and isinstance(part.get("callID"), str)
            ):
                return str(part["callID"]), str(command), dict(state["input"])
        return None

    @staticmethod
    def _message_has_call(messages: object, call_id: str) -> bool:
        if not isinstance(messages, list):
            return False
        for message in messages:
            if not isinstance(message, dict):
                continue
            for part in message.get("parts", []) or []:
                if not isinstance(part, dict):
                    continue
                if (
                    part.get("type") == "tool"
                    and part.get("callID") == call_id
                ):
                    return True
        return False

    @staticmethod
    def _scenario_actions_complete(
        *,
        scenario: dict[str, Any] | None,
        actions_done: set[str],
        answered: set[str],
        compaction_seen: bool,
        reconciled: bool,
        resume_sent: bool,
        resume_busy_seen: bool,
    ) -> bool:
        if scenario is None:
            return True
        questions_complete = not scenario.get("questions") or bool(answered)
        message_actions = {
            str(item["action_id"])
            for item in scenario.get("actions", [])
            if item.get("kind") == "message"
        }
        has_fault = any(
            item.get("kind") == "component_fault" for item in scenario.get("actions", [])
        )
        required = set(scenario.get("required_events", []))
        return (
            questions_complete
            and message_actions.issubset(actions_done)
            and ("context_compacted" not in required or compaction_seen)
            and (not has_fault or (reconciled and resume_sent and resume_busy_seen))
        )

    @staticmethod
    def _validate_scenario(
        *,
        scenario: dict[str, Any] | None,
        session_id: str,
        events: list[dict[str, Any]],
        control: list[dict[str, Any]],
    ) -> None:
        if scenario is None:
            return
        observed = {str(item.get("event_type")) for item in control}
        missing = set(scenario.get("required_events", [])) - observed
        if missing:
            raise RuntimeError("native scenario omitted required control evidence")
        question_ids = {
            str((item.get("properties") or {}).get("id"))
            for item in events
            if item.get("type") == "question.asked"
            and NativeOpenCodeHarnessAgent._event_session(item) == session_id
        }
        resolved_ids = {
            str(item.get("interaction_id"))
            for item in control
            if item.get("event_type") == "question_resolved"
        }
        replied_ids = {
            str((item.get("properties") or {}).get("requestID"))
            for item in events
            if item.get("type") == "question.replied"
            and NativeOpenCodeHarnessAgent._event_session(item) == session_id
        }
        if question_ids != resolved_ids or resolved_ids != replied_ids:
            raise RuntimeError("native scenario contains an orphaned question")
        compacted_events = sum(
            item.get("type") == "session.compacted"
            and NativeOpenCodeHarnessAgent._event_session(item) == session_id
            for item in events
        )
        compacted_controls = sum(
            item.get("event_type") == "context_compacted" for item in control
        )
        if compacted_events != compacted_controls or compacted_events > 1:
            raise RuntimeError("native scenario compaction evidence is invalid")
        unknown = [
            item for item in control if item.get("event_type") == "operation_unknown"
        ]
        reconciled = [
            item for item in control if item.get("event_type") == "operation_reconciled"
        ]
        faulted = [
            item
            for item in control
            if item.get("event_type") == "component_fault_injected"
        ]
        if len(unknown) != len(reconciled) or len(reconciled) != len(faulted):
            raise RuntimeError("native operation reconciliation is incomplete")
        if len(reconciled) > 1:
            raise RuntimeError("native operation was reconciled more than once")
        if reconciled:
            operation_id = reconciled[0].get("operation_id")
            fault_intent = NativeOpenCodeHarnessAgent._fault_tool_intent(events, scenario)
            if fault_intent is None:
                raise RuntimeError("native operation intent evidence is unavailable")
            fault_call_id, _fault_command, fault_arguments = fault_intent
            expected_intent = _canonical_sha256(
                {"function_name": "bash", "arguments": fault_arguments}
            )
            expected_result = _canonical_sha256(
                {"command": FAULT_COMMAND, "exit_code": 0}
            )
            if (
                operation_id != fault_call_id
                or operation_id != unknown[0].get("operation_id")
                or operation_id != faulted[0].get("operation_id")
                or unknown[0].get("intent_sha256") != expected_intent
                or reconciled[0].get("intent_sha256") != expected_intent
                or reconciled[0].get("result_sha256") != expected_result
            ):
                raise RuntimeError("native operation reconciliation evidence is invalid")

    @staticmethod
    def _public_message_projection(messages: list[Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            info = message.get("info") or {}
            parts: list[dict[str, Any]] = []
            for part in message.get("parts", []) or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    parts.append({"type": "text", "text": str(part.get("text") or "")})
                elif part.get("type") == "tool":
                    state = part.get("state") or {}
                    parts.append(
                        {
                            "type": "tool",
                            "callID": part.get("callID"),
                            "tool": part.get("tool"),
                            "status": state.get("status"),
                            "input": state.get("input") if isinstance(state.get("input"), dict) else {},
                        }
                    )
            result.append({"role": info.get("role"), "parts": parts})
        return result

    def _trajectory(
        self,
        *,
        instruction: str,
        session_id: str,
        messages: list[Any],
    ) -> dict[str, Any]:
        steps: list[dict[str, Any]] = [
            {"step_id": 1, "source": "user", "message": instruction}
        ]
        total_input = total_output = total_cache = 0
        total_cost = 0.0
        for message in messages:
            if not isinstance(message, dict):
                continue
            info = message.get("info")
            if not isinstance(info, dict) or info.get("role") != "assistant":
                continue
            text_parts: list[str] = []
            tool_calls: list[dict[str, Any]] = []
            observations: list[dict[str, Any]] = []
            for part in message.get("parts", []) or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
                elif part.get("type") == "tool" and isinstance(part.get("callID"), str):
                    state = part.get("state") if isinstance(part.get("state"), dict) else {}
                    tool_calls.append(
                        {
                            "tool_call_id": part["callID"],
                            "function_name": str(part.get("tool") or "unknown"),
                            "arguments": state.get("input") if isinstance(state.get("input"), dict) else {},
                        }
                    )
                    if state.get("status") in {"completed", "error"}:
                        observations.append(
                            {
                                "source_call_id": part["callID"],
                                "content": str(state.get("output") or state.get("error") or "")[:16_384],
                            }
                        )
            tokens = info.get("tokens") if isinstance(info.get("tokens"), dict) else {}
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            input_tokens = int(tokens.get("input") or 0)
            output_tokens = int(tokens.get("output") or 0)
            cache_read = int(cache.get("read") or 0)
            cost = float(info.get("cost") or 0)
            total_input += input_tokens + cache_read
            total_output += output_tokens
            total_cache += cache_read
            total_cost += cost
            step: dict[str, Any] = {
                "step_id": len(steps) + 1,
                "source": "agent",
                "message": "\n".join(text_parts),
                "model_name": self.model_name,
                "llm_call_count": 1,
                "metrics": {
                    "prompt_tokens": input_tokens + cache_read,
                    "completion_tokens": output_tokens,
                    "cached_tokens": cache_read or None,
                    "cost_usd": cost or None,
                },
            }
            if tool_calls:
                step["tool_calls"] = tool_calls
            if observations:
                step["observation"] = {"results": observations}
            steps.append(step)
        if len(steps) == 1:
            raise RuntimeError("native OpenCode produced no assistant messages")
        return {
            "schema_version": "ATIF-v1.7",
            "session_id": session_id,
            "agent": {
                "name": "opencode",
                "version": NATIVE_OPENCODE_VERSION,
                "model_name": self.model_name,
            },
            "steps": steps,
            "final_metrics": {
                "total_prompt_tokens": total_input,
                "total_completion_tokens": total_output,
                "total_cached_tokens": total_cache or None,
                "total_cost_usd": total_cost or None,
                "total_steps": len(steps),
            },
        }

    def _write_artifacts(
        self, *, trajectory: dict[str, Any], ledger: dict[str, Any]
    ) -> None:
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "trajectory.json").write_text(
            json.dumps(trajectory, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.logs_dir / "modelmirror-native-harness-ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.logs_dir / "opencode.txt").write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in ledger["events"])
            + "\n",
            encoding="utf-8",
        )
