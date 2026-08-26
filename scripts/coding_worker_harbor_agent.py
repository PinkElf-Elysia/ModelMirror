from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import shlex
import tarfile
import time
import tomllib
import uuid
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

try:  # Harbor remains an evaluation-only dependency.
    from harbor.agents.base import BaseAgent
    from harbor.environments.base import BaseEnvironment
    from harbor.models.agent.context import AgentContext

    HARBOR_AVAILABLE = True
except ModuleNotFoundError:  # Allows repository unit tests without Harbor installed.
    BaseAgent = object  # type: ignore[assignment,misc]
    BaseEnvironment = Any  # type: ignore[assignment,misc]
    AgentContext = Any  # type: ignore[assignment,misc]
    HARBOR_AVAILABLE = False


ATIF_SCHEMA_VERSION = "ATIF-v1.7"
TERMINAL_STATES = {
    "completed",
    "blocked",
    "failed",
    "cancelled",
    "budget_limited",
    "expired",
}
def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _response_stdout(result: object) -> str:
    value = getattr(result, "stdout", "")
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="strict")
    return str(value)


class ModelMirrorWorkerAgent(BaseAgent):  # type: ignore[misc]
    """Thin Harbor adapter for the provider-neutral Coding Worker API."""

    SUPPORTS_ATIF = True
    SUPPORTS_WINDOWS = False

    def __init__(
        self,
        *args: Any,
        worker_url: str | None = None,
        benchmark_root: str | None = None,
        model_route: str | None = None,
        poll_seconds: float = 0.5,
        timeout_seconds: int = 900,
        **kwargs: Any,
    ) -> None:
        if not HARBOR_AVAILABLE:
            raise RuntimeError("Harbor 0.21.0 is required for ModelMirrorWorkerAgent")
        super().__init__(*args, **kwargs)
        self._worker_url = (
            worker_url
            or os.getenv("MODELMIRROR_WORKER_URL")
            or "http://127.0.0.1:8000/api/coding-worker/v1"
        ).rstrip("/")
        self._benchmark_root = Path(
            benchmark_root
            or os.getenv("MODELMIRROR_HARBOR_BENCHMARK_ROOT", "benchmarks/coding-worker-v18")
        ).resolve()
        self._model_route = (
            model_route
            or os.getenv("MODELMIRROR_WORKER_MODEL_ROUTE")
            or "coding/default"
        )
        self._poll_seconds = max(0.1, float(poll_seconds))
        self._timeout_seconds = max(30, int(timeout_seconds))

    @staticmethod
    def name() -> str:
        return "modelmirror-worker"

    def version(self) -> str | None:
        return "v18-harness-v3"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        binding = await self._read_binding(environment)
        task_id = str(binding["task_id"])
        scenario = self._load_scenario(task_id)
        if hashlib.sha256(instruction.encode("utf-8")).hexdigest() != binding.get(
            "instruction_sha256"
        ):
            raise RuntimeError("Harbor instruction does not match its frozen fixture")
        scenario_path = self._benchmark_root / "tasks" / task_id / "scenario.json"
        observed_scenario = (
            hashlib.sha256(scenario_path.read_bytes()).hexdigest()
            if scenario_path.exists()
            else None
        )
        if observed_scenario != binding.get("scenario_sha256"):
            raise RuntimeError("Harbor scenario does not match its frozen fixture")
        if _canonical_sha256(binding["acceptance"]) != binding.get("acceptance_sha256"):
            raise RuntimeError("Harbor acceptance does not match its frozen fixture")
        request = self._task_request(binding, instruction)
        timeout = httpx.Timeout(30.0, read=30.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            task = await self._request(client, "POST", "/tasks", json=request)
            worker_task_id = str(task["task_id"])
            deadline = time.monotonic() + self._timeout_seconds
            actions_done: set[str] = set()
            approvals_seen: dict[str, dict[str, Any]] = {}
            while str(task["state"]) not in TERMINAL_STATES:
                if time.monotonic() >= deadline:
                    await self._request(
                        client, "POST", f"/tasks/{worker_task_id}/cancel"
                    )
                    raise RuntimeError("ModelMirror Worker task timed out")
                state = str(task["state"])
                if state == "waiting_approval":
                    approvals = await self._request(
                        client, "GET", f"/tasks/{worker_task_id}/approvals"
                    )
                    await self._drive_scenario_actions(
                        client,
                        worker_task_id,
                        state,
                        scenario,
                        actions_done,
                        approvals=tuple(approvals.get("approvals", [])),
                    )
                    for approval in approvals.get("approvals", []):
                        approvals_seen[str(approval["approval_id"])] = approval
                        if approval.get("status") != "pending":
                            continue
                        if not self._approval_allowed(approval, scenario, task_id):
                            decision = "reject"
                        else:
                            decision = "approve_once"
                        await self._request(
                            client,
                            "POST",
                            f"/tasks/{worker_task_id}/approvals",
                            json={
                                "approval_id": approval["approval_id"],
                                "decision": decision,
                                "ttl_seconds": 900,
                            },
                        )
                else:
                    await self._drive_scenario_actions(
                        client,
                        worker_task_id,
                        state,
                        scenario,
                        actions_done,
                    )
                if state == "waiting_input":
                    questions = await self._request(
                        client, "GET", f"/tasks/{worker_task_id}/questions"
                    )
                    for question in questions.get("questions", []):
                        if question.get("status") != "pending":
                            continue
                        answer = self._question_answer(question, scenario)
                        await self._request(
                            client,
                            "POST",
                            f"/tasks/{worker_task_id}/questions/{question['question_id']}",
                            json=answer,
                        )
                await asyncio.sleep(self._poll_seconds)
                task = await self._request(
                    client, "GET", f"/tasks/{worker_task_id}"
                )

            approvals = await self._request(
                client, "GET", f"/tasks/{worker_task_id}/approvals"
            )
            for approval in approvals.get("approvals", []):
                approvals_seen[str(approval["approval_id"])] = approval
            events = await self._terminal_events(client, worker_task_id)
            artifact = await self._request(
                client,
                "POST",
                f"/tasks/{worker_task_id}/workspace/parity-export",
            )
            export = await self._request(
                client, "GET", f"/tasks/{worker_task_id}/export"
            )
            self._validate_scenario_completion(
                scenario=scenario,
                actions_done=actions_done,
                export=export,
                events=events,
            )
            content = await self._download_artifact(
                client, worker_task_id, str(artifact["artifact_id"])
            )

        archive_path = self.logs_dir / "modelmirror-workspace.tar"
        archive_path.write_bytes(content)
        self._validate_workspace_archive(
            archive_path,
            expected_sha256=str(artifact["sha256"]),
            expected_size=int(artifact["size"]),
        )
        remote_archive = f"/tmp/modelmirror-workspace-{artifact['sha256'][:16]}.tar"
        await environment.upload_file(
            source_path=archive_path,
            target_path=remote_archive,
        )
        result = await environment.exec(
            command=self._workspace_install_command(
                remote_archive,
                expected_sha256=str(artifact["sha256"]),
                expected_size=int(artifact["size"]),
            )
        )
        if int(getattr(result, "return_code", getattr(result, "exit_code", 0))) != 0:
            raise RuntimeError("Harbor environment rejected the Worker workspace export")

        input_tokens, output_tokens = self._usage(events)
        trajectory = self._trajectory(
            instruction=instruction,
            worker_task_id=worker_task_id,
            export=export,
            approvals=tuple(approvals_seen.values()),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        trajectory_path = self.logs_dir / "trajectory.json"
        trajectory_path.write_text(
            json.dumps(trajectory, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        facts = self._facts(
            export=export,
            events=events,
            approvals=tuple(approvals_seen.values()),
            trajectory=trajectory,
            run_binding={
                "fixture_task_id": task_id,
                "worker_task_id": worker_task_id,
                "source_id": binding["source_id"],
                "revision": binding["revision"],
                "instruction_sha256": binding["instruction_sha256"],
                "acceptance_sha256": binding["acceptance_sha256"],
                "scenario_sha256": binding["scenario_sha256"],
                "workspace_artifact": artifact,
            },
        )
        ledger = {
            "run_binding": {
                "fixture_task_id": task_id,
                "worker_task_id": worker_task_id,
                "source_id": binding["source_id"],
                "revision": binding["revision"],
                "instruction_sha256": binding["instruction_sha256"],
                "acceptance_sha256": binding["acceptance_sha256"],
                "scenario_sha256": binding["scenario_sha256"],
                "workspace_artifact": artifact,
            },
            "export": export,
            "events": [
                summary
                for item in events
                if (summary := self._ledger_event(item)) is not None
            ],
            "approvals": list(approvals_seen.values()),
        }
        (self.logs_dir / "modelmirror-harness-ledger.json").write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (self.logs_dir / "modelmirror-harness-facts.json").write_text(
            json.dumps(facts, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        context.n_input_tokens = input_tokens
        context.n_output_tokens = output_tokens
        context.n_cache_tokens = 0

    async def _read_binding(self, environment: BaseEnvironment) -> dict[str, Any]:
        result = await environment.exec(command="cat /opt/modelmirror/source.json")
        if int(getattr(result, "return_code", getattr(result, "exit_code", 0))) != 0:
            raise RuntimeError("Harbor task omitted its ModelMirror source binding")
        payload = json.loads(_response_stdout(result))
        required = {
            "task_id",
            "source_id",
            "revision",
            "instruction_sha256",
            "scenario_sha256",
            "acceptance_sha256",
            "acceptance",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise RuntimeError("Harbor task source binding is invalid")
        return payload

    def _load_scenario(self, task_id: str) -> dict[str, Any]:
        path = self._benchmark_root / "tasks" / task_id / "scenario.json"
        if not path.exists():
            return {
                "allowed_approvals": [],
                "questions": [],
                "actions": [],
                "required_events": [],
            }
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Harbor task scenario is invalid")
        return payload

    def _task_request(self, binding: Mapping[str, Any], instruction: str) -> dict[str, Any]:
        acceptance = binding["acceptance"]
        if not isinstance(acceptance, dict):
            raise RuntimeError("Harbor task acceptance binding is invalid")
        return {
            "client_task_id": f"harbor-{uuid.uuid4().hex}",
            "objective": instruction,
            "workspace_source": {
                "kind": "builtin",
                "source_id": binding["source_id"],
                "revision": binding["revision"],
            },
            "acceptance": acceptance,
            "policy_profile": "develop",
            "model_route": self._model_route,
            "budget": {
                "max_seconds": self._timeout_seconds,
                "max_turns": 128,
                "max_tool_calls": 512,
                "max_output_bytes": 16 * 1024 * 1024,
            },
            "context_refs": [],
        }

    def _approval_allowed(
        self,
        approval: Mapping[str, Any],
        scenario: Mapping[str, Any],
        task_id: str,
    ) -> bool:
        request = approval.get("request")
        allowed = scenario.get("allowed_approvals", [])
        if not isinstance(request, dict):
            return False
        if any(
            request == item or self._shell_approval_matches(request, item)
            for item in allowed
            if isinstance(item, dict)
        ):
            return True
        manifest_path = self._benchmark_root / "tasks" / task_id / "task.toml"
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        metadata = manifest.get("metadata", {}).get("modelmirror", {})
        checks = metadata.get("visible_checks", [])
        commands = tuple(
            (
                tuple(str(value) for value in check.get("argv", [])),
                int(check.get("timeout_seconds", 300)),
            )
            for check in checks
            if isinstance(check, dict) and check.get("argv")
        )
        argv = request.get("argv")
        if isinstance(argv, list):
            return (
                set(request) == {"argv", "timeout_seconds"}
                and all(isinstance(value, str) for value in argv)
                and any(
                    tuple(argv) == command
                    and request.get("timeout_seconds") == timeout_seconds
                    for command, timeout_seconds in commands
                )
            )
        script_sha256 = request.get("script_sha256")
        if not isinstance(script_sha256, str):
            return False
        if set(request) != {
            "operation_id",
            "script_sha256",
            "cwd",
            "mode",
            "timeout_seconds",
            "network_scope_sha256",
        }:
            return False
        if (
            request.get("cwd") != "."
            or request.get("mode") != "inspect"
            or request.get("network_scope_sha256") is not None
        ):
            return False
        return any(
            request.get("timeout_seconds") == timeout_seconds
            and script_sha256
            in {
                hashlib.sha256(rendered.encode("utf-8")).hexdigest()
                for rendered in (shlex.join(command), " ".join(command))
            }
            for command, timeout_seconds in commands
        )

    @staticmethod
    def _shell_approval_matches(
        request: Mapping[str, Any], expected: Mapping[str, Any]
    ) -> bool:
        if set(expected) != {"script", "cwd", "mode", "timeout_seconds"}:
            return False
        script = expected.get("script")
        return (
            isinstance(script, str)
            and set(request)
            == {
                "operation_id",
                "script_sha256",
                "cwd",
                "mode",
                "timeout_seconds",
                "network_scope_sha256",
            }
            and request.get("script_sha256")
            == hashlib.sha256(script.encode("utf-8")).hexdigest()
            and request.get("cwd") == expected.get("cwd")
            and request.get("mode") == expected.get("mode")
            and request.get("timeout_seconds") == expected.get("timeout_seconds")
            and request.get("network_scope_sha256") is None
        )

    @staticmethod
    def _question_answer(
        question: Mapping[str, Any], scenario: Mapping[str, Any]
    ) -> dict[str, Any]:
        prompt = str(question.get("prompt", ""))
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        for answer in scenario.get("questions", []):
            if answer.get("prompt_sha256") == prompt_sha256:
                selected = answer.get("selected_option_id")
                if isinstance(selected, str) and selected:
                    return {"option_id": selected}
                return {"answer": answer.get("answer")}
        raise RuntimeError("Worker requested an unfrozen Harbor question")

    @staticmethod
    def _validate_scenario_completion(
        *,
        scenario: Mapping[str, Any],
        actions_done: set[str],
        export: Mapping[str, Any],
        events: list[dict[str, Any]],
    ) -> None:
        expected_actions = {
            str(action.get("action_id") or "")
            for action in scenario.get("actions", [])
            if isinstance(action, Mapping)
        }
        if actions_done != expected_actions:
            raise RuntimeError("Worker omitted a required Harbor scenario action")

        expected_questions = {
            str(item["prompt_sha256"]): item
            for item in scenario.get("questions", [])
            if isinstance(item, Mapping)
        }
        raw_questions = export.get("questions")
        if not isinstance(raw_questions, (list, tuple)):
            raise RuntimeError("Worker export omitted its question fact source")
        observed_questions: dict[str, Mapping[str, Any]] = {}
        for question in raw_questions:
            if not isinstance(question, Mapping) or not isinstance(
                question.get("prompt"), str
            ):
                raise RuntimeError("Worker question export is invalid")
            prompt_sha256 = hashlib.sha256(
                question["prompt"].encode("utf-8")
            ).hexdigest()
            if prompt_sha256 in observed_questions:
                raise RuntimeError("Worker repeated a frozen Harbor question")
            observed_questions[prompt_sha256] = question
        if set(observed_questions) != set(expected_questions):
            raise RuntimeError("Worker question set does not match its Harbor scenario")
        for prompt_sha256, question in observed_questions.items():
            expected = expected_questions[prompt_sha256]
            if question.get("status") != "resolved":
                raise RuntimeError("Worker left a Harbor question unresolved")
            selected = expected.get("selected_option_id")
            if isinstance(selected, str) and selected:
                if question.get("selected_option_id") != selected:
                    raise RuntimeError("Worker resolved a Harbor question differently")
            elif question.get("answer") != expected.get("answer"):
                raise RuntimeError("Worker resolved a Harbor question differently")

        observed_event_types = {
            str(item.get("event_type") or "") for item in events
        }
        required_event_types = set(scenario.get("required_events", []))
        if not required_event_types.issubset(observed_event_types):
            raise RuntimeError("Worker omitted a required Harbor scenario event")

    async def _drive_scenario_actions(
        self,
        client: httpx.AsyncClient,
        worker_task_id: str,
        state: str,
        scenario: Mapping[str, Any],
        actions_done: set[str],
        *,
        approvals: tuple[Mapping[str, Any], ...] = (),
    ) -> None:
        for action in scenario.get("actions", []):
            action_id = str(action.get("action_id", ""))
            if not action_id or action_id in actions_done or action.get("when_state") != state:
                continue
            kind = action.get("kind")
            if kind == "message":
                await self._request(
                    client,
                    "POST",
                    f"/tasks/{worker_task_id}/messages",
                    json={"message": action["message"]},
                )
            elif kind == "pause_resume":
                await self._request(client, "POST", f"/tasks/{worker_task_id}/pause")
                await self._request(client, "POST", f"/tasks/{worker_task_id}/resume")
            elif kind == "resume":
                await self._request(client, "POST", f"/tasks/{worker_task_id}/resume")
            elif kind == "component_fault":
                expected = action.get("approval")
                matching = tuple(
                    approval
                    for approval in approvals
                    if approval.get("status") == "pending"
                    and isinstance(approval.get("request"), dict)
                    and isinstance(expected, dict)
                    and self._shell_approval_matches(approval["request"], expected)
                )
                if not matching:
                    continue
                if len(matching) != 1:
                    raise RuntimeError("Harness fault matched multiple approvals")
                await self._request_controller_fault(worker_task_id, action)
            else:
                raise RuntimeError("Harbor task scenario action is unsupported")
            actions_done.add(action_id)

    async def _request_controller_fault(
        self, worker_task_id: str, action: Mapping[str, Any]
    ) -> None:
        token = os.getenv("MODELMIRROR_HARNESS_CONTROLLER_TOKEN")
        if not token or len(token) < 32:
            raise RuntimeError("Harness controller is unavailable for fault injection")
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            response = await client.post(
                f"{self._worker_url}/harness/faults",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "task_id": worker_task_id,
                    "component": action["component"],
                    "point": action["point"],
                },
            )
        if response.status_code != 202:
            raise RuntimeError("Harness controller rejected fault injection")

    async def _terminal_events(
        self, client: httpx.AsyncClient, worker_task_id: str
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        async with client.stream(
            "GET", f"{self._worker_url}/tasks/{worker_task_id}/events?after=0"
        ) as response:
            response.raise_for_status()
            event_type: str | None = None
            for_line_data: list[str] = []
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    for_line_data.append(line[6:])
                elif not line and for_line_data:
                    payload = json.loads("\n".join(for_line_data))
                    if isinstance(payload, dict):
                        payload["event_type"] = event_type
                        events.append(payload)
                    event_type = None
                    for_line_data = []
        return events

    async def _download_artifact(
        self, client: httpx.AsyncClient, worker_task_id: str, artifact_id: str
    ) -> bytes:
        response = await client.get(
            f"{self._worker_url}/tasks/{worker_task_id}/artifacts/{artifact_id}"
        )
        response.raise_for_status()
        return response.content

    @staticmethod
    def _ledger_event(item: Mapping[str, Any]) -> dict[str, Any] | None:
        """Retain the public fields needed to independently derive V18 facts."""

        event_type = item.get("event_type")
        allowed = {
            "task_state": ("from", "to", "reason"),
            "tool_operation": ("operation_id", "state"),
            "operation_reconciled": ("operation_id", "state"),
            "context_compacted": ("boundary_sequence", "workspace_tree_hash"),
            "approval_requested": ("approval_id", "capability"),
            "approval_decided": ("approval_id", "status"),
            "question_requested": ("question_id",),
            "question_resolved": ("question_id",),
            "subtask_created": ("child_task_id", "kind"),
            "subtask_completed": ("child_task_id", "kind", "merge_state"),
            "subtask_failed": ("child_task_id", "kind", "merge_state"),
        }
        keys = allowed.get(str(event_type))
        if keys is None:
            return None
        payload = item.get("payload")
        if not isinstance(payload, Mapping):
            return None
        return {
            "sequence": item.get("sequence"),
            "event_type": event_type,
            "payload": {key: payload.get(key) for key in keys},
        }

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await client.request(method, f"{self._worker_url}{path}", json=json)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("ModelMirror Worker returned an invalid response")
        return payload

    @staticmethod
    def _validate_workspace_archive(
        path: Path, *, expected_sha256: str, expected_size: int
    ) -> None:
        content = path.read_bytes()
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_sha256:
            raise RuntimeError("Worker workspace artifact binding changed")
        with tarfile.open(path, "r:") as archive:
            members = archive.getmembers()
            if len(members) > 20_000:
                raise RuntimeError("Worker workspace artifact exceeds entry limits")
            for member in members:
                pure = PurePosixPath(member.name)
                if (
                    pure.is_absolute()
                    or ".." in pure.parts
                    or not pure.parts
                    or member.issym()
                    or member.islnk()
                    or not member.isfile()
                ):
                    raise RuntimeError("Worker workspace artifact is unsafe")

    @staticmethod
    def _workspace_install_command(
        archive_path: str, *, expected_sha256: str, expected_size: int
    ) -> str:
        if (
            re.fullmatch(r"/tmp/modelmirror-workspace-[a-f0-9]{16}\.tar", archive_path)
            is None
            or len(expected_sha256) != 64
            or any(value not in "0123456789abcdef" for value in expected_sha256)
            or expected_size < 0
        ):
            raise RuntimeError("Worker workspace artifact binding is invalid")
        return (
            f'test "$(sha256sum {archive_path} | awk \'{{print $1}}\')" = '
            f'"{expected_sha256}" && '
            f'test "$(wc -c < {archive_path})" -eq "{expected_size}" && '
            "find /workspace -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && "
            f"tar -xf {archive_path} -C /workspace"
        )

    def _trajectory(
        self,
        *,
        instruction: str,
        worker_task_id: str,
        export: Mapping[str, Any],
        approvals: tuple[dict[str, Any], ...],
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> dict[str, Any]:
        steps: list[dict[str, Any]] = [
            {
                "step_id": 1,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": "user",
                "message": instruction,
                "extra": {},
            }
        ]
        pending_tools: dict[str, dict[str, Any]] = {}
        for item in export.get("session_ledger", []):
            kind = item.get("kind")
            payload = item.get("payload", {})
            operation_id = item.get("operation_id")
            if kind == "tool_started" and isinstance(operation_id, str):
                pending_tools[operation_id] = item
            elif kind == "tool_finished" and isinstance(operation_id, str):
                started = pending_tools.pop(operation_id, None)
                if started is None:
                    continue
                started_payload = started.get("payload", {})
                steps.append(
                    {
                        "step_id": len(steps) + 1,
                        "timestamp": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(float(started.get("created_at", time.time()))),
                        ),
                        "source": "agent",
                        "message": str(started_payload.get("summary", "Tool call")),
                        "tool_calls": [
                            {
                                "tool_call_id": operation_id,
                                "function_name": str(started_payload.get("tool_name", "tool")),
                                "arguments": {},
                            }
                        ],
                        "observation": {
                            "results": [
                                {
                                    "source_call_id": operation_id,
                                    "content": str(payload.get("summary", "Tool result")),
                                }
                            ]
                        },
                        "extra": {"result_state": payload.get("result_state")},
                    }
                )
            elif kind == "public_message" and payload.get("role") in {"assistant", "tool"}:
                steps.append(
                    {
                        "step_id": len(steps) + 1,
                        "timestamp": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(float(item.get("created_at", time.time()))),
                        ),
                        "source": "agent",
                        "message": str(payload.get("text", "")),
                        "extra": {},
                    }
                )
            elif kind in {
                "plan",
                "todo",
                "question",
                "compaction",
                "check_evidence",
                "turn_started",
                "turn_finished",
            }:
                steps.append(
                    {
                        "step_id": len(steps) + 1,
                        "timestamp": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(float(item.get("created_at", time.time()))),
                        ),
                        "source": "agent",
                        "message": f"Platform {kind.replace('_', ' ')} record",
                        "extra": {
                            "platform_record": kind,
                            "turn_id": item.get("turn_id"),
                            "payload": payload,
                        },
                    }
                )
        for approval in approvals:
            steps.append(
                {
                    "step_id": len(steps) + 1,
                    "timestamp": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(float(approval.get("created_at", time.time()))),
                    ),
                    "source": "agent",
                    "message": "Platform approval record",
                    "extra": {
                        "platform_record": "approval",
                        "approval_id": approval.get("approval_id"),
                        "operation_id": approval.get("operation_id"),
                        "status": approval.get("status"),
                    },
                }
            )
        for subtask in export.get("subtask_index", []):
            steps.append(
                {
                    "step_id": len(steps) + 1,
                    "timestamp": time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(float(subtask.get("created_at", time.time()))),
                    ),
                    "source": "agent",
                    "message": "Platform subtask record",
                    "extra": {
                        "platform_record": "subtask",
                        "child_task_id": subtask.get("child_task_id"),
                        "kind": subtask.get("kind"),
                        "merge_state": subtask.get("merge_state"),
                    },
                }
            )
        return {
            "schema_version": ATIF_SCHEMA_VERSION,
            "session_id": worker_task_id,
            "agent": {
                "name": "modelmirror-worker",
                "version": self.version(),
                "model_name": self._model_route,
                "extra": {},
            },
            "steps": steps,
            "final_metrics": {
                "total_prompt_tokens": input_tokens,
                "total_completion_tokens": output_tokens,
                "total_steps": len(steps),
                "extra": {},
            },
            "extra": {"provider_neutral": True},
        }

    @staticmethod
    def _usage(events: list[dict[str, Any]]) -> tuple[int, int]:
        """Return the largest normalized cumulative usage snapshot."""

        best = (0, 0)
        for item in events:
            if item.get("event_type") != "provider_event":
                continue
            envelope = item.get("payload")
            if not isinstance(envelope, Mapping):
                envelope = item
            if envelope.get("kind") != "usage":
                continue
            data = envelope.get("data")
            usage = data.get("usage") if isinstance(data, dict) else None
            if not isinstance(usage, dict):
                continue
            raw_input = usage.get("input_tokens")
            raw_output = usage.get("output_tokens")
            if (
                isinstance(raw_input, int)
                and not isinstance(raw_input, bool)
                and raw_input >= 0
                and isinstance(raw_output, int)
                and not isinstance(raw_output, bool)
                and raw_output >= 0
                and raw_input + raw_output > sum(best)
            ):
                best = (raw_input, raw_output)
        return best

    @staticmethod
    def _facts(
        *,
        export: Mapping[str, Any],
        events: list[dict[str, Any]],
        approvals: tuple[dict[str, Any], ...],
        trajectory: Mapping[str, Any],
        run_binding: Mapping[str, Any],
    ) -> dict[str, Any]:
        ModelMirrorWorkerAgent._validate_run_binding(
            export=export,
            trajectory=trajectory,
            run_binding=run_binding,
        )
        required_indexes: dict[str, tuple[Mapping[str, Any], ...]] = {}
        for name in ("operation_index", "questions", "subtask_index"):
            raw_index = export.get(name)
            if not isinstance(raw_index, (list, tuple)) or any(
                not isinstance(item, Mapping) for item in raw_index
            ):
                raise RuntimeError(f"Worker export omitted its {name} fact source")
            required_indexes[name] = tuple(raw_index)

        operations: list[dict[str, Any]] = []
        operation_records: dict[str, Mapping[str, Any]] = {}
        for item in required_indexes["operation_index"]:
            side_effecting = item.get("side_effecting")
            if not isinstance(side_effecting, bool):
                raise RuntimeError("Worker operation omitted its side-effect fact")
            operation_id = str(item.get("operation_id") or "")
            if not operation_id or operation_id in operation_records:
                raise RuntimeError("Worker operation index is not canonical")
            operation_records[operation_id] = item
            operations.append(
                {
                    "evidence_id": f"operation_{operation_id}",
                    "operation_id": operation_id,
                    "intent_sha256": item["intent_sha256"],
                    "state": item["state"],
                    "side_effecting": side_effecting,
                }
            )
        interactions = [
            {
                "evidence_id": f"approval_{item['approval_id']}",
                "interaction_id": item["approval_id"],
                "kind": "approval",
                "state": (
                    "resolved" if item.get("status") == "approved" else item.get("status")
                ),
            }
            for item in approvals
        ]
        interactions.extend(
            {
                "evidence_id": f"question_{item['question_id']}",
                "interaction_id": item["question_id"],
                "kind": "question",
                "state": item["status"],
            }
            for item in required_indexes["questions"]
        )
        subtask_states = {
            "pending": "pending",
            "ready": "resolved",
            "merged": "resolved",
            "not_applicable": "resolved",
            "conflicted": "rejected",
            "failed": "rejected",
        }
        interactions.extend(
            {
                "evidence_id": f"subtask_{item['child_task_id']}",
                "interaction_id": item["child_task_id"],
                "kind": "subtask",
                "state": subtask_states[str(item["merge_state"])],
            }
            for item in required_indexes["subtask_index"]
        )
        task = export.get("task", {})
        worker_task_id = str(task.get("task_id") or "task_unknown")
        coordination: list[dict[str, object]] = []
        approval_ids = {str(item.get("approval_id") or "") for item in approvals}
        question_ids = {
            str(item.get("question_id") or "")
            for item in required_indexes["questions"]
        }
        subtask_ids = {
            str(item.get("child_task_id") or "")
            for item in required_indexes["subtask_index"]
        }
        reconciled_counts: dict[str, int] = {}
        for item in events:
            event_type = item.get("event_type")
            payload = item.get("payload")
            if not isinstance(payload, Mapping):
                raise RuntimeError("Worker event ledger omitted its public payload")
            sequence = int(item.get("sequence", 0))
            if event_type in {"tool_operation", "operation_reconciled"}:
                operation_id = str(payload.get("operation_id") or "")
                operation = operation_records.get(operation_id)
                if operation is None:
                    coordination.append(
                        {
                            "evidence_id": f"event_{worker_task_id}_{sequence or 1}",
                            "stage": "tool_validation",
                            "failed": True,
                        }
                    )
                    continue
                if event_type == "operation_reconciled":
                    reconciled_counts[operation_id] = reconciled_counts.get(operation_id, 0) + 1
                    if reconciled_counts[operation_id] > 1 and bool(
                        operation.get("side_effecting")
                    ):
                        operations.append(
                            {
                                "evidence_id": f"event_{worker_task_id}_{sequence or 1}",
                                "operation_id": f"{operation_id}_replay_{sequence or 1}",
                                "intent_sha256": operation["intent_sha256"],
                                "state": "completed",
                                "side_effecting": True,
                            }
                        )
                continue
            requested: tuple[str, str, set[str]] | None = None
            if event_type == "approval_requested":
                requested = ("approval", "approval_id", approval_ids)
            elif event_type == "question_requested":
                requested = ("question", "question_id", question_ids)
            elif event_type == "subtask_created":
                requested = ("subtask", "child_task_id", subtask_ids)
            if requested is not None:
                kind, key, final_ids = requested
                interaction_id = str(payload.get(key) or "")
                if interaction_id and interaction_id not in final_ids:
                    interactions.append(
                        {
                            "evidence_id": f"event_{worker_task_id}_{sequence or 1}",
                            "interaction_id": interaction_id,
                            "kind": kind,
                            "state": "pending",
                        }
                    )
                continue
            if event_type != "task_state":
                continue
            reason = str(payload.get("reason") or "")
            stage = ModelMirrorWorkerAgent._failure_stage(reason)
            if stage is None:
                continue
            coordination.append(
                {
                    "evidence_id": f"event_{worker_task_id}_{sequence or 1}",
                    "stage": stage,
                    "failed": True,
                }
            )
        reason = str(task.get("reason") or "")
        task_state = str(task.get("state") or "")
        stage = ModelMirrorWorkerAgent._failure_stage(reason)
        if task_state != "completed" and stage is None:
            stage = "agent_outcome"
        if stage is not None and not coordination:
            sequence = max(
                (int(item.get("sequence", 0)) for item in events), default=0
            )
            coordination.append(
                {
                    "evidence_id": f"event_{worker_task_id}_{sequence or 1}",
                    "stage": stage,
                    "failed": True,
                }
            )
        return {
            "export_sha256": _canonical_sha256(export),
            "trajectory_sha256": _canonical_sha256(trajectory),
            "complete": True,
            "operations": operations,
            "interactions": interactions,
            "coordination": coordination,
        }

    @staticmethod
    def _validate_run_binding(
        *,
        export: Mapping[str, Any],
        trajectory: Mapping[str, Any],
        run_binding: Mapping[str, Any],
    ) -> None:
        task = export.get("task")
        spec = task.get("spec") if isinstance(task, Mapping) else None
        source = spec.get("workspace_source") if isinstance(spec, Mapping) else None
        acceptance = spec.get("acceptance") if isinstance(spec, Mapping) else None
        artifact = run_binding.get("workspace_artifact")
        metadata = artifact.get("metadata") if isinstance(artifact, Mapping) else None
        worker_task_id = run_binding.get("worker_task_id")
        if (
            not isinstance(task, Mapping)
            or not isinstance(source, Mapping)
            or not isinstance(artifact, Mapping)
            or not isinstance(metadata, Mapping)
            or task.get("task_id") != worker_task_id
            or trajectory.get("session_id") != worker_task_id
            or source.get("source_id") != run_binding.get("source_id")
            or source.get("revision") != run_binding.get("revision")
            or not isinstance(spec.get("objective"), str)
            or hashlib.sha256(spec["objective"].encode("utf-8")).hexdigest()
            != run_binding.get("instruction_sha256")
            or not isinstance(acceptance, Mapping)
            or _canonical_sha256(acceptance) != run_binding.get("acceptance_sha256")
            or artifact.get("task_id") != worker_task_id
            or metadata.get("workspace_tree_hash") != export.get("workspace_tree_hash")
        ):
            raise RuntimeError("Worker Harness run binding changed")
        steps = trajectory.get("steps")
        first = steps[0] if isinstance(steps, list) and steps else None
        if (
            not isinstance(first, Mapping)
            or first.get("source") != "user"
            or not isinstance(first.get("message"), str)
            or hashlib.sha256(first["message"].encode("utf-8")).hexdigest()
            != run_binding.get("instruction_sha256")
        ):
            raise RuntimeError("Worker Harness instruction binding changed")
        exported_artifact = next(
            (
                item
                for item in export.get("artifact_index", ())
                if isinstance(item, Mapping)
                and item.get("artifact_id") == artifact.get("artifact_id")
            ),
            None,
        )
        if not isinstance(exported_artifact, Mapping) or any(
            exported_artifact.get(key) != artifact.get(key)
            for key in ("artifact_id", "media_type", "sha256", "size", "metadata")
        ):
            raise RuntimeError("Worker Harness artifact binding changed")

    @staticmethod
    def _failure_stage(reason: str) -> str | None:
        if not reason:
            return None
        expected_states = {
            "approval_resume_required",
            "user_input_required",
            "subtasks_running",
            "subtasks_settled",
            "acceptance_runner_pending",
            "steering_pending",
            "user_paused",
            "turn_undo",
            "turn_redo",
        }
        if reason in expected_states or reason.startswith("turn_parked_"):
            return None
        if reason.startswith("harness_transport_"):
            return "provider_transport"
        if reason.startswith(
            (
                "harness_protocol_",
                "harness_authentication_",
                "harness_rate_",
            )
        ):
            return "provider_protocol"
        if reason.startswith("harness_policy_"):
            return "policy"
        if reason.startswith("harness_budget_"):
            return "budget"
        if reason in {
            "harness_interrupted",
            "harness_driver_internal",
            "control_plane_internal_error",
            "tool_broker_internal_error",
            "operation_result_unknown",
        }:
            return "harness"
        mapping = (
            (("source",), "source_admission"),
            (("slot", "scheduler", "route_unavailable", "model_route"), "scheduler"),
            (("provider_transport", "provider_offline"), "provider_transport"),
            (("provider", "checkpoint"), "provider_protocol"),
            (
                ("approval", "question", "interaction", "turn", "compaction"),
                "interaction",
            ),
            (("executor", "command", "shell"), "executor"),
            (("workspace", "changeset", "tree"), "workspace_cas"),
            (("acceptance", "evidence"), "visible_acceptance"),
            (("policy", "forbidden"), "policy"),
            (("budget",), "budget"),
            (("tool_",), "tool_validation"),
            (("service_shutdown", "runner_cancelled"), "harness"),
        )
        for markers, stage in mapping:
            if any(marker in reason for marker in markers):
                return stage
        return "agent_outcome"


class NearMissPatchAgent(BaseAgent):  # type: ignore[misc]
    """Evaluation-only agent that applies the task's known-insufficient patch."""

    SUPPORTS_ATIF = False
    SUPPORTS_WINDOWS = False

    def __init__(self, *args: Any, benchmark_root: str | None = None, **kwargs: Any) -> None:
        if not HARBOR_AVAILABLE:
            raise RuntimeError("Harbor 0.21.0 is required for NearMissPatchAgent")
        super().__init__(*args, **kwargs)
        self._benchmark_root = Path(
            benchmark_root
            or os.getenv("MODELMIRROR_HARBOR_BENCHMARK_ROOT", "benchmarks/coding-worker-v18")
        ).resolve()

    @staticmethod
    def name() -> str:
        return "modelmirror-near-miss"

    def version(self) -> str | None:
        return "v18-harness-v3"

    async def setup(self, environment: BaseEnvironment) -> None:
        return None

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        result = await environment.exec(command="cat /opt/modelmirror/source.json")
        if int(getattr(result, "return_code", getattr(result, "exit_code", 0))) != 0:
            raise RuntimeError("Harbor task omitted its source binding")
        binding = json.loads(_response_stdout(result))
        task_id = str(binding["task_id"])
        patch_path = self._benchmark_root / "tasks" / task_id / "near_miss.patch"
        if not patch_path.is_file():
            raise RuntimeError("Harbor task omitted its near-miss patch")
        uploaded = self.logs_dir / "near_miss.patch"
        uploaded.write_bytes(patch_path.read_bytes())
        await environment.upload_file(
            source_path=uploaded,
            target_path="/tmp/modelmirror-near-miss.patch",
        )
        applied = await environment.exec(
            command="cd /workspace && patch -p1 < /tmp/modelmirror-near-miss.patch"
        )
        if int(getattr(applied, "return_code", getattr(applied, "exit_code", 0))) != 0:
            raise RuntimeError("Harbor near-miss patch could not be applied")


__all__ = ["ModelMirrorWorkerAgent", "NearMissPatchAgent"]
