from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

import scripts.coding_worker_harness as harness_cli
from scripts.coding_worker_harbor_agent import ModelMirrorWorkerAgent
from scripts.coding_worker_harbor_environment import (
    DockerDesktopAllowlistProbeEnvironment,
    StaticNoNetworkDockerEnvironment,
)
from scripts.coding_worker_native_agent import (
    FAULT_COMMAND,
    REMOTE_ROOT,
    REMOTE_TOOL_HOME,
    REMOTE_TOOL_SHELL,
    NativeOpenCodeHarnessAgent,
)
from scripts.coding_worker_harness import (
    DOCKER_DESKTOP_PROBE_ENVIRONMENT,
    HarnessCliError,
    _canonical_tree_files,
    _collect_run_record as _collect_run_record_raw,
    _declared_executable,
    _harbor_trial_reward,
    _harbor_engine_environment,
    _materialized_tasks_root,
    _native_task_runtime_image_sha256,
    _assert_native_runtime_fixture_coverage,
    _loopback_worker_url,
    _model_agent_hosts,
    _native_allowed_shell_commands,
    _native_opencode_config,
    _native_shell_policy,
    _assert_native_interaction_parity_available,
    _require_frozen_candidate,
    _runtime_runner_image_sha256,
    _tree_digest,
    _task_fixture,
    _validate_sealed_checkers,
    _validate_docker_boundaries,
    _validate_native_project_config,
    _validate_regular_tree,
    _validate_materialized_public_tasks,
    _validate_scenario,
    _worker_attestation_sha256,
    _worker_facts,
)
from server.coding_worker.harness_v3 import (
    CODING_WORKER_HARNESS_CODE_FILES,
    HARNESS_PROTOCOL,
    PROVIDER_HARNESS_CODE_FILES,
    SERVER_HARNESS_CODE_FILES,
    HarnessArtifactSummary,
    HarnessCoordinationFact,
    HarnessFactSet,
    HarnessFailureStage,
    HarnessFixture,
    HarnessFixtureBundle,
    HarnessFixtureFile,
    HarnessInteractionFact,
    HarnessOperationFact,
    HarnessRunRecord,
    HarnessVisibleCheck,
    build_harness_report,
    derive_diagnostics,
    harness_code_bundle_sha256,
    report_eligibility,
)


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _collect_run_record(**kwargs: object) -> HarnessRunRecord:
    kwargs.setdefault("expected_harbor_task_checksum", "9" * 64)
    kwargs.setdefault(
        "expected_instruction_sha256",
        hashlib.sha256(b"test instruction").hexdigest(),
    )
    return _collect_run_record_raw(**kwargs)


def test_model_agent_hosts_are_exact_and_canonical() -> None:
    assert _model_agent_hosts(["API.Example.com", "api.example.com"]) == (
        "api.example.com",
    )
    for invalid in ([], ["*"], ["127.0.0.1"], ["localhost"], ["bad host"]):
        with pytest.raises(HarnessCliError):
            _model_agent_hosts(invalid)


def test_docker_desktop_probe_environment_is_never_a_calibration_runner() -> None:
    with pytest.raises(HarnessCliError, match="cannot run calibration"):
        harness_cli.run_round(
            SimpleNamespace(environment=DOCKER_DESKTOP_PROBE_ENVIRONMENT)
        )


@pytest.mark.asyncio
async def test_static_gate_cleanup_preserves_shared_runtime_images() -> None:
    environment = object.__new__(StaticNoNetworkDockerEnvironment)
    environment._keep_containers = False
    environment.prepare_logs_for_host = AsyncMock()
    environment._run_docker_compose_command = AsyncMock()
    environment._cleanup_mounts_compose_file = MagicMock()
    environment._cleanup_resources_compose_file = MagicMock()
    environment._cleanup_env_compose_file = MagicMock()
    environment._cleanup_egress_control_services_compose_file = MagicMock()

    await environment.stop(delete=True)

    environment._run_docker_compose_command.assert_awaited_once_with(
        ["down", "--volumes", "--remove-orphans"]
    )
    environment._cleanup_mounts_compose_file.assert_called_once_with()
    environment._cleanup_resources_compose_file.assert_called_once_with()
    environment._cleanup_env_compose_file.assert_called_once_with()
    environment._cleanup_egress_control_services_compose_file.assert_called_once_with()


def test_docker_desktop_probe_sidecar_keeps_exact_egress_control() -> None:
    context = DockerDesktopAllowlistProbeEnvironment._EGRESS_CONTROL_SIDECAR_CONTEXT_PATH
    dockerfile = (context / "Dockerfile").read_text(encoding="utf-8")
    policy = (context / "network-policy").read_text(encoding="utf-8")

    assert "gogost/gost:3.2.7-nightly.20260602@sha256:" in dockerfile
    assert "fib daddr" not in policy
    assert "ip daddr 127.0.0.0/8 return" in policy
    assert "ip6 daddr ::1 return" in policy
    assert "DNS_IPV4=" in policy
    assert "ip daddr { $DNS_IPV4 } udp dport 53 accept" in policy
    assert "ip daddr { $DNS_IPV4 } tcp dport 53 return" in policy
    assert "meta mark $GOST_MARK return" in policy
    assert "meta l4proto tcp redirect to :$GOST_PORT" in policy
    assert "meta l4proto != tcp reject" in policy


def test_harbor_probe_network_routes_only_through_the_egress_sidecar() -> None:
    root = Path(__file__).resolve().parents[2]
    overlay = (
        root / "benchmarks" / "coding-worker-v18" / "harbor-network-overlay.yml"
    ).read_text(encoding="utf-8")

    assert "internal: false" in overlay
    assert "internal: true" not in overlay
    assert "network_mode:none" in overlay


def test_native_task_runtime_is_daemon_attested_and_all_fixtures_are_covered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        harness_cli.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=harness_cli.NATIVE_TASK_RUNTIME_IMAGE_SHA256 + "\n",
        ),
    )
    assert (
        _native_task_runtime_image_sha256()
        == harness_cli.NATIVE_TASK_RUNTIME_IMAGE_SHA256
    )

    root = Path(__file__).resolve().parents[2] / "benchmarks" / "coding-worker-v18"
    bundle = SimpleNamespace(
        fixtures=[
            SimpleNamespace(task_id=path.name)
            for path in sorted((root / "tasks").iterdir())
            if path.is_dir()
        ]
    )
    _assert_native_runtime_fixture_coverage(root, bundle)


def test_native_task_runtime_coverage_fails_closed(tmp_path: Path) -> None:
    for task_id, dockerfile in (
        ("covered", f"FROM {harness_cli.NATIVE_TASK_RUNTIME_IMAGE}\n"),
        ("missing", "FROM python:3.12-slim\n"),
    ):
        environment = tmp_path / "tasks" / task_id / "environment"
        environment.mkdir(parents=True)
        (environment / "Dockerfile").write_text(
            dockerfile,
            encoding="utf-8",
        )
    bundle = SimpleNamespace(
        fixtures=[
            SimpleNamespace(task_id="covered"),
            SimpleNamespace(task_id="missing"),
        ]
    )
    with pytest.raises(HarnessCliError, match="coverage is incomplete"):
        _assert_native_runtime_fixture_coverage(tmp_path, bundle)


def test_online_attestation_covers_the_complete_coding_worker_python_package() -> None:
    package_root = harness_cli.REPOSITORY_ROOT / "server" / "coding_worker"
    assert set(CODING_WORKER_HARNESS_CODE_FILES) == {
        path.name for path in package_root.glob("*.py")
    }


def test_worker_attestation_is_loopback_and_candidate_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = harness_cli.REPOSITORY_ROOT / "server" / "coding_worker"
    model = "openrouter/example-model"
    payload = {
        "protocol": "modelmirror-coding-harness-attestation/v1",
        "server_code_bundle_sha256": harness_code_bundle_sha256(
            package_root, SERVER_HARNESS_CODE_FILES
        ),
        "server_generation": "a" * 32,
        "controller_generation": 7,
        "providers": {
            slot: {
                "route_id": "coding/default",
                "model_identity_sha256": hashlib.sha256(model.encode()).hexdigest(),
                "engine": "opencode-1.18.9",
                "sidecar_generation": ("b" if slot == "slot-a" else "c") * 32,
                "code_bundle_sha256": harness_code_bundle_sha256(
                    package_root, PROVIDER_HARNESS_CODE_FILES
                ),
            }
            for slot in ("slot-a", "slot-b")
        },
    }

    class _Response:
        status = 200

        def read(self) -> bytes:
            return json.dumps(payload).encode()

    class _Connection:
        def __init__(self, host: str, port: int, *, timeout: int):
            assert (host, port, timeout) == ("127.0.0.1", 8000, 30)

        def request(self, method: str, path: str, *, headers: dict[str, str]):
            assert (method, path) == (
                "GET",
                "/api/coding-worker/v1/harness/attestation",
            )
            assert headers["Authorization"] == (
                "Bearer controller-token-0123456789abcdef"
            )

        def getresponse(self) -> _Response:
            return _Response()

        def close(self) -> None:
            return None

    monkeypatch.setattr(harness_cli.http.client, "HTTPConnection", _Connection)
    assert len(
        _worker_attestation_sha256(
            worker_url="http://127.0.0.1:8000/api/coding-worker/v1",
            controller_token="controller-token-0123456789abcdef",
            model=model,
            model_route="coding/default",
        )
    ) == 64
    payload["providers"]["slot-b"]["model_identity_sha256"] = "0" * 64
    with pytest.raises(HarnessCliError, match="model does not match"):
        _worker_attestation_sha256(
            worker_url="http://127.0.0.1:8000/api/coding-worker/v1",
            controller_token="controller-token-0123456789abcdef",
            model=model,
            model_route="coding/default",
        )


@pytest.mark.parametrize(
    "value",
    (
        "https://127.0.0.1:8000/api/coding-worker/v1",
        "http://example.com:8000/api/coding-worker/v1",
        "http://127.0.0.1:8000/other",
        "http://user:secret@127.0.0.1:8000/api/coding-worker/v1",
        "http://127.0.0.1:8000/api/coding-worker/v1?redirect=elsewhere",
    ),
)
def test_worker_control_url_rejects_non_loopback_or_ambiguous_targets(
    value: str,
) -> None:
    with pytest.raises(HarnessCliError):
        _loopback_worker_url(value)


def test_native_runner_never_inherits_worker_control_environment(tmp_path: Path) -> None:
    base = {
        "SAFE": "value",
        "CODING_WORKER_HARNESS_CONTROLLER_TOKEN": "server-secret",
        "MODELMIRROR_HARNESS_CONTROLLER_TOKEN": "agent-secret",
        "MODELMIRROR_HARBOR_BENCHMARK_ROOT": "forged-root",
        "MODELMIRROR_WORKER_URL": "http://forged",
        "MODELMIRROR_WORKER_MODEL_ROUTE": "forged-route",
        "OPENCODE_CONFIG_CONTENT": '{"permission":"allow"}',
        "OPENCODE_PERMISSION": '{"bash":"allow"}',
    }
    native = _harbor_engine_environment(
        base,
        engine="native-opencode",
        controller_token="controller-token-0123456789abcdef",
        benchmark_root=tmp_path,
        worker_url="http://worker",
        worker_model_route="coding/default",
    )
    assert native == {
        "SAFE": "value",
        "MODELMIRROR_HARBOR_BENCHMARK_ROOT": str(tmp_path),
    }

    worker = _harbor_engine_environment(
        base,
        engine="modelmirror-worker",
        controller_token="controller-token-0123456789abcdef",
        benchmark_root=tmp_path,
        worker_url="http://worker",
        worker_model_route="coding/default",
    )
    assert worker["SAFE"] == "value"
    assert worker["MODELMIRROR_HARNESS_CONTROLLER_TOKEN"].startswith("controller-")
    assert worker["MODELMIRROR_HARBOR_BENCHMARK_ROOT"] == str(tmp_path)
    assert worker["MODELMIRROR_WORKER_URL"] == "http://worker"
    assert worker["MODELMIRROR_WORKER_MODEL_ROUTE"] == "coding/default"
    assert "CODING_WORKER_HARNESS_CONTROLLER_TOKEN" not in worker
    assert not any(key.startswith("OPENCODE_") for key in native)
    assert not any(key.startswith("OPENCODE_") for key in worker)


def test_native_opencode_policy_allows_only_frozen_shell_commands(tmp_path: Path) -> None:
    fixture = HarnessFixture.model_construct(
        task_id="policy-task",
        category="python",
        source_id="source-policy-task",
        revision="1" * 64,
        initial_tree_hash="2" * 64,
        task_manifest_sha256="3" * 64,
        instruction_sha256="4" * 64,
        environment_spec_sha256="5" * 64,
        solution_bundle_sha256="6" * 64,
        verifier_bundle_sha256="7" * 64,
        task_package_sha256="8" * 64,
        near_miss_sha256="9" * 64,
        files=tuple(
            HarnessFixtureFile(
                path=f"file-{index}.py",
                sha256=hashlib.sha256(str(index).encode()).hexdigest(),
                content_base64=base64.b64encode(str(index).encode()).decode(),
                binary_canary=index == 0,
            )
            for index in range(5)
        ),
        visible_checks=(
            HarnessVisibleCheck(
                check_id="visible",
                argv=("python", "-m", "unittest", "-v"),
                timeout_seconds=60,
            ),
        ),
    )
    commands = _native_allowed_shell_commands(tmp_path, fixture)
    config = _native_opencode_config(commands)

    assert commands == ("python -m unittest -v",)
    assert config["shell"] == REMOTE_TOOL_SHELL
    assert config["permission"]["*"] == "deny"
    assert config["permission"]["bash"] == {
        "*": "deny",
        "python -m unittest -v": "allow",
    }
    assert config["permission"]["lsp"] == "deny"
    for denied in ("external_directory", "task", "skill", "webfetch", "websearch"):
        assert config["permission"][denied] == "deny"


def test_real_round_accepts_only_the_checked_in_native_scenario_controller() -> None:
    fixture = HarnessFixture.model_construct(
        task_id="interactive-task",
        category="session",
        source_id="source-interactive",
        revision="1" * 64,
        initial_tree_hash="2" * 64,
        task_manifest_sha256="3" * 64,
        instruction_sha256="4" * 64,
        environment_spec_sha256="5" * 64,
        solution_bundle_sha256="6" * 64,
        verifier_bundle_sha256="7" * 64,
        task_package_sha256="8" * 64,
        scenario_sha256="a" * 64,
        near_miss_sha256="9" * 64,
        files=tuple(
            HarnessFixtureFile(
                path=f"file-{index}.py",
                sha256=hashlib.sha256(str(index).encode()).hexdigest(),
                content_base64=base64.b64encode(str(index).encode()).decode(),
                binary_canary=index == 0,
            )
            for index in range(5)
        ),
        visible_checks=(
            HarnessVisibleCheck(check_id="visible", argv=("python", "-V")),
        ),
    )
    bundle = HarnessFixtureBundle.model_construct(fixtures=(fixture,))
    with pytest.raises(HarnessCliError, match="scenario is unavailable"):
        _assert_native_interaction_parity_available(bundle, root=Path("missing"))


def test_real_round_rejects_a_missing_native_shell_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "coding_worker_native_agent.py").write_text("agent\n", encoding="utf-8")
    (scripts / "coding_worker_native_control.mjs").write_text(
        "control\n", encoding="utf-8"
    )
    monkeypatch.setattr(harness_cli, "REPOSITORY_ROOT", tmp_path)

    with pytest.raises(HarnessCliError, match="controller is unavailable"):
        _assert_native_interaction_parity_available(
            HarnessFixtureBundle.model_construct(fixtures=()), root=tmp_path
        )


def test_native_question_permission_is_enabled_only_for_frozen_question_task() -> None:
    denied = _native_opencode_config(("pytest",))
    allowed = _native_opencode_config(("pytest",), allow_question=True)

    assert denied["permission"]["question"] == "deny"
    assert allowed["permission"]["question"] == "allow"


def test_clarification_fixture_requires_a_structured_interaction() -> None:
    instruction = (
        Path(__file__).resolve().parents[2]
        / "benchmarks"
        / "coding-worker-v18"
        / "tasks"
        / "session-clarify-before-edit"
        / "instruction.md"
    ).read_text(encoding="utf-8")

    assert "available structured user-input tool" in instruction
    assert "not only in assistant text" in instruction


def test_native_question_answer_and_public_ledger_are_fail_closed() -> None:
    prompt = "Which rounding policy should be used: half_up or half_even?"
    scenario = {
        "questions": [
            {
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "selected_option_id": "half_up",
            }
        ]
    }
    assert NativeOpenCodeHarnessAgent._question_spec(
        {
            "questions": [
                {
                    "question": prompt,
                    "options": [
                        {"label": "half_up", "description": "financial"},
                        {"label": "half_even", "description": "bankers"},
                    ],
                }
            ]
        },
        scenario,
    )["selected_option_id"] == "half_up"
    with pytest.raises(RuntimeError, match="frozen answer"):
        NativeOpenCodeHarnessAgent._question_spec(
            {
                "questions": [
                    {
                        "question": prompt,
                        "options": [{"label": "half_even", "description": "only"}],
                    }
                ]
            },
            scenario,
        )

    projection = NativeOpenCodeHarnessAgent._public_message_projection(
        [
            {
                "info": {"role": "assistant"},
                "parts": [
                    {"type": "reasoning", "text": "private chain"},
                    {"type": "text", "text": "public answer"},
                ],
            }
        ]
    )
    assert projection == [
        {"role": "assistant", "parts": [{"type": "text", "text": "public answer"}]}
    ]
    assert "private chain" not in json.dumps(projection)


def test_native_server_password_never_uses_the_harbor_artifact_mount() -> None:
    helper = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "coding_worker_native_control.mjs"
    ).read_text(encoding="utf-8")

    assert REMOTE_ROOT.startswith("/tmp/")
    assert "/logs/agent" not in REMOTE_ROOT
    assert "/logs/agent" not in helper
    assert "/tmp/modelmirror-native-opencode/server-password" in helper


def test_native_health_probe_is_bounded_during_server_startup() -> None:
    root = Path(__file__).resolve().parents[2]
    helper = (root / "scripts" / "coding_worker_native_control.mjs").read_text(
        encoding="utf-8"
    )
    agent = (root / "scripts" / "coding_worker_native_agent.py").read_text(
        encoding="utf-8"
    )

    assert "DEFAULT_REQUEST_TIMEOUT_MS = 120_000" in helper
    assert "MODELMIRROR_NATIVE_CONTROL_TIMEOUT_MS" in helper
    assert "value < 250 || value > DEFAULT_REQUEST_TIMEOUT_MS" in helper
    assert '"/global/health",\n                    timeout_ms=2_000,' in agent


def test_native_fault_gate_is_exact_private_and_blocks_the_receipt() -> None:
    wrapper = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "coding_worker_native_shell_wrapper.sh"
    ).read_text(encoding="utf-8")
    scenario = {
        "actions": [
            {
                "kind": "component_fault",
                "component": "executor",
                "point": "after_side_effect_before_receipt",
                "approval": {
                    "script": FAULT_COMMAND,
                    "cwd": ".",
                    "mode": "mutate",
                    "timeout_seconds": 120,
                },
            }
        ]
    }

    assert NativeOpenCodeHarnessAgent._fault_action(scenario) is not None
    assert REMOTE_TOOL_SHELL == f"{REMOTE_ROOT}/tool-shell"
    assert REMOTE_TOOL_HOME.startswith("/tmp/")
    assert "/logs/agent" not in wrapper
    assert "/usr/bin/setpriv" in wrapper
    assert "--reuid=65534" in wrapper
    assert "--regid=65534" in wrapper
    assert "/usr/bin/env -i" in wrapper
    assert "chown -h -R -P 65534:65534 /workspace" in wrapper
    assert "fault.result.tmp" in wrapper
    assert "while [ -f \"$root/fault.arm\" ]" in wrapper
    assert wrapper.index('/bin/sh -c "$command"') < wrapper.index("fault.result.tmp")
    with pytest.raises(RuntimeError, match="frozen exact gate"):
        NativeOpenCodeHarnessAgent._fault_action(
            {
                "actions": [
                    {
                        **scenario["actions"][0],
                        "approval": {
                            **scenario["actions"][0]["approval"],
                            "script": "python -m build_index --force",
                        },
                    }
                ]
            }
        )


@pytest.mark.asyncio
async def test_native_install_requires_the_prebuilt_offline_runtime() -> None:
    agent = object.__new__(NativeOpenCodeHarnessAgent)
    agent.exec_as_root = AsyncMock(
        return_value=SimpleNamespace(return_code=0, stdout="")
    )

    await agent.install(SimpleNamespace())

    command = agent.exec_as_root.await_args.kwargs["command"]
    assert "apt-get" not in command
    assert "npm" not in command
    assert "nvm" not in command
    assert "/usr/local/bin/node" in command
    assert "/usr/local/bin/opencode" in command
    assert f'"$(opencode --version)" = "{harness_cli.NATIVE_OPENCODE_VERSION}"' in command
    source = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "coding_worker_native_agent.py"
    ).read_text(encoding="utf-8")
    assert ". ~/.nvm/nvm.sh" not in source


def test_session_probes_use_the_immutable_offline_native_runtime() -> None:
    root = Path(__file__).resolve().parents[2]
    expected = f"FROM {harness_cli.NATIVE_TASK_RUNTIME_IMAGE}"
    for task_id in (
        "session-clarify-before-edit",
        "session-steering-compaction",
        "session-restart-command-reconcile",
    ):
        dockerfile = (
            root
            / "benchmarks"
            / "coding-worker-v18"
            / "tasks"
            / task_id
            / "environment"
            / "Dockerfile"
        ).read_text(encoding="utf-8")
        assert expected in dockerfile
        assert "USER root" in dockerfile
        assert "apt-get install -y --no-install-recommends patch" in dockerfile


@pytest.mark.asyncio
async def test_native_fault_result_requires_the_exact_private_marker() -> None:
    environment = SimpleNamespace(
        exec=AsyncMock(
            return_value=SimpleNamespace(
                return_code=0,
                stdout=f"command={FAULT_COMMAND}\nexit=0\n",
            )
        )
    )

    assert await NativeOpenCodeHarnessAgent._fault_result(environment) == {
        "command": FAULT_COMMAND,
        "exit_code": 0,
    }
    environment.exec.return_value.stdout = f"command={FAULT_COMMAND}\nexit=1\n"
    with pytest.raises(RuntimeError, match="result is invalid"):
        await NativeOpenCodeHarnessAgent._fault_result(environment)


def test_native_fault_only_targets_a_running_frozen_call() -> None:
    scenario = {
        "actions": [
            {
                "kind": "component_fault",
                "component": "executor",
                "point": "after_side_effect_before_receipt",
                "approval": {
                    "script": FAULT_COMMAND,
                    "cwd": ".",
                    "mode": "mutate",
                    "timeout_seconds": 120,
                },
            }
        ]
    }

    def event(status: str, command: str = FAULT_COMMAND) -> dict[str, object]:
        return {
            "type": "message.part.updated",
            "properties": {
                "part": {
                    "tool": "bash",
                    "callID": "call_fault",
                    "state": {
                        "status": status,
                        "input": {"command": command, "workdir": "/workspace"},
                    },
                }
            },
        }

    assert NativeOpenCodeHarnessAgent._fault_tool_intent(
        [event("running")], scenario
    ) == (
        "call_fault",
        FAULT_COMMAND,
        {"command": FAULT_COMMAND, "workdir": "/workspace"},
    )
    assert NativeOpenCodeHarnessAgent._fault_tool_intent(
        [event("completed")], scenario
    ) is None
    assert NativeOpenCodeHarnessAgent._fault_tool_intent(
        [event("running", "python -m build_index --force")], scenario
    ) is None


def test_native_stop_uses_dash_compatible_process_group_signals() -> None:
    command = NativeOpenCodeHarnessAgent._stop_server_command()

    assert 'kill -TERM -"$pid"' in command
    assert 'kill -KILL -"$pid"' in command
    assert 'case "$pid" in' in command
    assert 'if kill -0 -"$pid"' in command
    assert "kill -TERM --" not in command


@pytest.mark.asyncio
async def test_native_shell_boundary_requires_root_and_drops_workspace_ownership() -> None:
    agent = object.__new__(NativeOpenCodeHarnessAgent)
    agent.exec_as_agent = AsyncMock(
        return_value=SimpleNamespace(return_code=0, stdout="")
    )

    await agent._prepare_shell_boundary(SimpleNamespace(), "/workspace")

    command = agent.exec_as_agent.await_args.kwargs["command"]
    assert 'test "$(id -u)" = 0' in command
    assert "test -x /usr/bin/setpriv" in command
    assert "chown -h -R -P 65534:65534 /workspace" in command
    assert REMOTE_TOOL_HOME in command
    with pytest.raises(RuntimeError, match="frozen mount"):
        await agent._prepare_shell_boundary(SimpleNamespace(), "/tmp/workspace")


def test_native_scenario_requires_the_actual_question_reply_event() -> None:
    scenario = {
        "required_events": ["question_requested", "question_resolved"],
    }
    events = [
        {
            "type": "question.asked",
            "properties": {"sessionID": "ses_one", "id": "que_one"},
        }
    ]
    control = [
        {"event_type": "question_requested", "interaction_id": "que_one"},
        {"event_type": "question_resolved", "interaction_id": "que_one"},
    ]
    with pytest.raises(RuntimeError, match="orphaned question"):
        NativeOpenCodeHarnessAgent._validate_scenario(
            scenario=scenario,
            session_id="ses_one",
            events=events,
            control=control,
        )


def test_runner_image_digest_is_derived_from_the_running_container(
    tmp_path: Path,
) -> None:
    marker = tmp_path / ".dockerenv"
    marker.write_text("", encoding="utf-8")
    docker = tmp_path / "docker"
    docker.write_text("binary", encoding="utf-8")
    container_id = "a" * 64
    image_id = f"sha256:{'b' * 64}"
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        stdout = f"{container_id} {image_id}\n" if command[1] == "inspect" else f"{image_id}\n"
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    assert _runtime_runner_image_sha256(
        docker_executable=str(docker.resolve()),
        docker_marker=marker,
        hostname="a" * 12,
        platform_name="posix",
        run_command=run,
    ) == image_id
    assert [item[1:3] for item in calls] == [
        ["inspect", "--type"],
        ["image", "inspect"],
    ]


def test_runner_image_digest_rejects_a_caller_placeholder(tmp_path: Path) -> None:
    marker = tmp_path / ".dockerenv"
    marker.write_text("", encoding="utf-8")
    docker = tmp_path / "docker"
    docker.write_text("binary", encoding="utf-8")

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"{'a' * 64} sha256:{'0' * 64}\n",
            stderr="",
        )

    with pytest.raises(HarnessCliError, match="placeholder"):
        _runtime_runner_image_sha256(
            docker_executable=str(docker.resolve()),
            docker_marker=marker,
            hostname="a" * 12,
            platform_name="posix",
            run_command=run,
        )


def test_task_project_cannot_override_native_opencode_policy() -> None:
    content = b"{}"
    entry = HarnessFixtureFile(
        path=".opencode/opencode.json",
        sha256=hashlib.sha256(content).hexdigest(),
        content_base64=base64.b64encode(content).decode(),
    )

    with pytest.raises(HarnessCliError, match="may not override"):
        _validate_native_project_config((entry,))


def test_native_command_policy_classifies_visible_check_and_mutation() -> None:
    task_root = (
        Path(__file__).resolve().parents[2]
        / "benchmarks"
        / "coding-worker-v18"
        / "tasks"
        / "session-restart-command-reconcile"
    )
    fixture, _binding = _task_fixture(task_root)

    assert _native_shell_policy(task_root, fixture) == {
        "python -m build_index": True,
        "python -m unittest discover -s visible_tests -v": False,
    }


def test_frozen_candidate_rejects_a_non_commit_binding() -> None:
    with pytest.raises(HarnessCliError, match="full lowercase commit id"):
        _require_frozen_candidate("not-a-commit")


def test_scenario_validation_binds_fault_to_one_frozen_mutate_scope(
    tmp_path: Path,
) -> None:
    path = tmp_path / "scenario.json"
    shell = {
        "script": "python -m build_index",
        "cwd": ".",
        "mode": "mutate",
        "timeout_seconds": 120,
    }
    payload = {
        "allowed_approvals": [shell],
        "questions": [],
        "required_events": ["operation_reconciled"],
        "actions": [
            {
                "action_id": "fault",
                "when_state": "waiting_approval",
                "kind": "component_fault",
                "component": "executor",
                "point": "after_side_effect_before_receipt",
                "approval": shell,
            },
            {
                "action_id": "resume",
                "when_state": "interrupted",
                "kind": "resume",
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    _validate_scenario(path)

    payload["actions"][0]["approval"] = {**shell, "timeout_seconds": 121}
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(HarnessCliError, match="scenario action"):
        _validate_scenario(path)


@pytest.mark.asyncio
async def test_worker_agent_arms_exact_fault_through_worker_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class Response:
        status_code = 202

    class Client:
        async def __aenter__(self) -> "Client":
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> Response:
            observed["url"] = url
            observed.update(kwargs)
            return Response()

    agent = object.__new__(ModelMirrorWorkerAgent)
    agent._worker_url = "http://worker.test/api/coding-worker/v1"
    token = "harness-agent-token-0123456789abcdef"
    monkeypatch.setenv("MODELMIRROR_HARNESS_CONTROLLER_TOKEN", token)
    monkeypatch.setattr(
        "scripts.coding_worker_harbor_agent.httpx.AsyncClient",
        lambda **_kwargs: Client(),
    )

    await agent._request_controller_fault(
        "task_" + "a" * 32,
        {
            "component": "executor",
            "point": "after_side_effect_before_receipt",
        },
    )

    assert observed["url"] == (
        "http://worker.test/api/coding-worker/v1/harness/faults"
    )
    assert observed["headers"] == {"Authorization": f"Bearer {token}"}
    assert observed["json"] == {
        "task_id": "task_" + "a" * 32,
        "component": "executor",
        "point": "after_side_effect_before_receipt",
    }


def _fixture(task_id: str, category: str, *, long_context: bool = False) -> HarnessFixture:
    count = 30 if long_context else 5
    files = tuple(
        HarnessFixtureFile(
            path=("assets/canary.bin" if index == 0 else f"src/file_{index}.py"),
            content_base64=base64.b64encode(bytes([index, 0, 255])).decode("ascii"),
            sha256=hashlib.sha256(bytes([index, 0, 255])).hexdigest(),
            binary_canary=index == 0,
        )
        for index in range(count)
    )
    check = HarnessVisibleCheck(check_id=f"check-{task_id}", argv=("pytest", "-q"))
    raw = {
        "task_id": task_id,
        "category": category,
        "source_id": f"source-{task_id}",
        "task_manifest_sha256": "1" * 64,
        "instruction_sha256": "2" * 64,
        "environment_spec_sha256": "4" * 64,
        "solution_bundle_sha256": "5" * 64,
        "verifier_bundle_sha256": "6" * 64,
        "task_package_sha256": "7" * 64,
        "scenario_sha256": None,
        "near_miss_sha256": "3" * 64,
        "files": files,
        "visible_checks": (check,),
        "required_modified_files": 2,
        "long_context": long_context,
    }
    provisional = HarnessFixture.model_construct(
        **raw,
        revision="0" * 64,
        initial_tree_hash="0" * 64,
    )
    return HarnessFixture(
        **raw,
        revision=provisional.canonical_revision(),
        initial_tree_hash=provisional.canonical_tree_hash(),
    )


def _facts() -> HarnessFactSet:
    completed = HarnessOperationFact(
        evidence_id="operation_one",
        operation_id="operation_one",
        intent_sha256="a" * 64,
        state="completed",
        side_effecting=True,
    )
    duplicate = HarnessOperationFact(
        evidence_id="operation_two",
        operation_id="operation_two",
        intent_sha256="a" * 64,
        state="completed",
        side_effecting=True,
    )
    unknown = HarnessOperationFact(
        evidence_id="operation_three",
        operation_id="operation_three",
        intent_sha256="b" * 64,
        state="unknown",
        side_effecting=True,
    )
    return HarnessFactSet(
        export_sha256="c" * 64,
        trajectory_sha256="d" * 64,
        operations=(completed, duplicate, unknown),
        interactions=(
            HarnessInteractionFact(
                evidence_id="approval_pending",
                interaction_id="approval_pending",
                kind="approval",
                state="pending",
            ),
        ),
        coordination=(
            HarnessCoordinationFact(
                evidence_id="event_scheduler",
                stage=HarnessFailureStage.SCHEDULER,
                failed=True,
            ),
            HarnessCoordinationFact(
                evidence_id="event_policy",
                stage=HarnessFailureStage.POLICY,
                failed=True,
            ),
        ),
    )


def _artifacts(*, worker: bool) -> tuple[HarnessArtifactSummary, ...]:
    values = [
        HarnessArtifactSummary(artifact_id="harbor_result", sha256="1" * 64, size=1),
        HarnessArtifactSummary(artifact_id="trajectory", sha256="2" * 64, size=1),
        HarnessArtifactSummary(artifact_id="workspace", sha256="3" * 64, size=1),
    ]
    if worker:
        values.append(
            HarnessArtifactSummary(
                artifact_id="worker_facts", sha256="4" * 64, size=1
            )
        )
        values.append(
            HarnessArtifactSummary(
                artifact_id="worker_ledger", sha256="5" * 64, size=1
            )
        )
    return tuple(values)


def test_fixture_bundle_requires_real_category_matrix_and_long_context_shape() -> None:
    fixtures = tuple(
        _fixture(f"python-{index}", "python") for index in range(3)
    ) + tuple(
        _fixture(f"typescript-{index}", "typescript") for index in range(3)
    ) + tuple(
        _fixture(
            f"repository-{index}",
            "repository",
            long_context=index == 2,
        )
        for index in range(3)
    ) + tuple(_fixture(f"session-{index}", "session") for index in range(3))

    bundle = HarnessFixtureBundle(fixtures=fixtures)

    assert bundle.protocol == HARNESS_PROTOCOL
    assert len(bundle.source_snapshots()) == 12
    assert len(bundle.fixtures[8].files) == 30
    assert len(bundle.canonical_sha256()) == 64


def test_fixture_package_order_and_executable_bits_are_os_independent(
    tmp_path: Path,
) -> None:
    (tmp_path / "zeta.py").write_text("print('zeta')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    (tmp_path / "solve.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    assert [path.name for path in _canonical_tree_files(tmp_path)] == [
        "README.md",
        "solve.sh",
        "zeta.py",
    ]
    assert _declared_executable(tmp_path / "solve.sh") is True
    assert _declared_executable(tmp_path / "zeta.py") is False


def test_worker_agent_approves_only_the_complete_frozen_command_scope() -> None:
    agent = object.__new__(ModelMirrorWorkerAgent)
    agent._benchmark_root = Path(__file__).parents[2] / "benchmarks/coding-worker-v18"
    task_id = "python-async-cache"
    argv = ["python", "-m", "unittest", "discover", "-s", "visible_tests", "-v"]
    scenario = {"allowed_approvals": []}

    assert agent._approval_allowed(
        {"request": {"argv": argv, "timeout_seconds": 180}}, scenario, task_id
    )
    assert not agent._approval_allowed(
        {"request": {"argv": argv, "timeout_seconds": 181}}, scenario, task_id
    )
    assert not agent._approval_allowed(
        {"request": {"argv": argv, "timeout_seconds": 180, "cwd": "."}},
        scenario,
        task_id,
    )

    rendered = " ".join(argv)
    shell_request = {
        "operation_id": "operation_frozen_check",
        "script_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "cwd": ".",
        "mode": "inspect",
        "timeout_seconds": 180,
        "network_scope_sha256": None,
    }
    assert agent._approval_allowed({"request": shell_request}, scenario, task_id)
    assert not agent._approval_allowed(
        {"request": {**shell_request, "mode": "mutate"}}, scenario, task_id
    )
    assert not agent._approval_allowed(
        {"request": {**shell_request, "network_scope_sha256": "a" * 64}},
        scenario,
        task_id,
    )

    mutate_script = "python -m build_index"
    mutate_request = {
        **shell_request,
        "operation_id": "operation_build_index",
        "script_sha256": hashlib.sha256(mutate_script.encode("utf-8")).hexdigest(),
        "mode": "mutate",
        "timeout_seconds": 120,
    }
    mutate_scope = {
        "script": mutate_script,
        "cwd": ".",
        "mode": "mutate",
        "timeout_seconds": 120,
    }
    assert agent._approval_allowed(
        {"request": mutate_request}, {"allowed_approvals": [mutate_scope]}, task_id
    )
    assert not agent._approval_allowed(
        {"request": {**mutate_request, "timeout_seconds": 121}},
        {"allowed_approvals": [mutate_scope]},
        task_id,
    )


@pytest.mark.asyncio
async def test_worker_agent_arms_fault_only_for_the_exact_pending_shell() -> None:
    agent = object.__new__(ModelMirrorWorkerAgent)
    agent._request_controller_fault = AsyncMock()
    expected = {
        "script": "python -m build_index",
        "cwd": ".",
        "mode": "mutate",
        "timeout_seconds": 120,
    }
    action = {
        "action_id": "fault",
        "when_state": "waiting_approval",
        "kind": "component_fault",
        "component": "executor",
        "point": "after_side_effect_before_receipt",
        "approval": expected,
    }
    scenario = {"actions": [action]}
    actions_done: set[str] = set()
    unrelated = {
        "status": "pending",
        "request": {"argv": ["python", "-m", "pytest"], "timeout_seconds": 120},
    }
    await agent._drive_scenario_actions(
        object(),
        "task_" + "a" * 32,
        "waiting_approval",
        scenario,
        actions_done,
        approvals=(unrelated,),
    )
    agent._request_controller_fault.assert_not_awaited()
    assert not actions_done

    request = {
        "operation_id": "operation_build_index",
        "script_sha256": hashlib.sha256(expected["script"].encode("utf-8")).hexdigest(),
        "cwd": ".",
        "mode": "mutate",
        "timeout_seconds": 120,
        "network_scope_sha256": None,
    }
    await agent._drive_scenario_actions(
        object(),
        "task_" + "a" * 32,
        "waiting_approval",
        scenario,
        actions_done,
        approvals=({"status": "pending", "request": request},),
    )
    agent._request_controller_fault.assert_awaited_once()
    assert actions_done == {"fault"}


def test_worker_agent_uses_largest_normalized_usage_snapshot() -> None:
    events = [
        {
            "event_type": "provider_event",
            "sequence": 11,
            "payload": {
                "kind": "usage",
                "data": {"usage": {"input_tokens": 100, "output_tokens": 20}},
            },
        },
        {
            "event_type": "provider_event",
            "sequence": 12,
            "payload": {
                "kind": "usage",
                "data": {"usage": {"input_tokens": 160, "output_tokens": 40}},
            },
        },
        {
            "event_type": "provider_event",
            "sequence": 13,
            "payload": {
                "kind": "message",
                "data": {"usage": {"input_tokens": 999, "output_tokens": 999}},
            },
        },
    ]

    assert ModelMirrorWorkerAgent._usage(events) == (160, 40)


def test_worker_question_answer_uses_the_public_api_contract() -> None:
    prompt = "Choose the rounding policy."
    scenario = {
        "questions": [
            {
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "answer": "Use half up.",
                "selected_option_id": "half_up",
            }
        ]
    }

    assert ModelMirrorWorkerAgent._question_answer(
        {"prompt": prompt}, scenario
    ) == {"option_id": "half_up"}


def test_worker_scenario_completion_rejects_missing_actions_and_events() -> None:
    scenario = {
        "allowed_approvals": [],
        "questions": [],
        "actions": [
            {
                "action_id": "steer",
                "when_state": "running",
                "kind": "message",
                "message": "Preserve stable ordering.",
            }
        ],
        "required_events": ["context_compacted"],
    }

    with pytest.raises(RuntimeError, match="scenario action"):
        ModelMirrorWorkerAgent._validate_scenario_completion(
            scenario=scenario,
            actions_done=set(),
            export={"questions": []},
            events=[],
        )
    with pytest.raises(RuntimeError, match="scenario event"):
        ModelMirrorWorkerAgent._validate_scenario_completion(
            scenario=scenario,
            actions_done={"steer"},
            export={"questions": []},
            events=[],
        )


def test_worker_scenario_completion_requires_the_frozen_question() -> None:
    prompt = "Choose the rounding policy."
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    scenario = {
        "allowed_approvals": [],
        "questions": [
            {
                "prompt_sha256": prompt_sha256,
                "answer": "Use half up.",
                "selected_option_id": "half_up",
            }
        ],
        "actions": [],
        "required_events": ["question_requested", "question_resolved"],
    }

    with pytest.raises(RuntimeError, match="question set"):
        ModelMirrorWorkerAgent._validate_scenario_completion(
            scenario=scenario,
            actions_done=set(),
            export={"questions": []},
            events=[
                {"event_type": "question_requested"},
                {"event_type": "question_resolved"},
            ],
        )


def test_diagnostics_are_derived_from_canonical_facts() -> None:
    diagnostics = derive_diagnostics(_facts())

    assert diagnostics.platform_coordination_failures == 1
    assert diagnostics.duplicate_side_effects == 1
    assert diagnostics.unsettled_operations == 1
    assert diagnostics.orphaned_interactions == 1
    assert diagnostics.evidence == {
        "platform_coordination_failures": ("event_scheduler",),
        "duplicate_side_effects": ("operation_two",),
        "unsettled_operations": ("operation_three",),
        "orphaned_interactions": ("approval_pending",),
    }


def _worker_binding(task_id: str) -> tuple[dict[str, object], dict[str, object]]:
    source_id = "source-worker-binding"
    revision = "a" * 64
    tree_hash = "b" * 64
    artifact = {
        "artifact_id": "artifact_workspace",
        "task_id": task_id,
        "media_type": "application/vnd.modelmirror.harness-workspace+tar",
        "sha256": "c" * 64,
        "size": 123,
        "metadata": {"workspace_tree_hash": tree_hash},
    }
    return (
        {
            "fixture_task_id": "fixture-worker-binding",
            "worker_task_id": task_id,
            "source_id": source_id,
            "revision": revision,
            "instruction_sha256": hashlib.sha256(b"test instruction").hexdigest(),
            "acceptance_sha256": _canonical_sha256({"required_checks": []}),
            "scenario_sha256": None,
            "workspace_artifact": artifact,
        },
        {
            "spec": {
                "objective": "test instruction",
                "workspace_source": {
                    "source_id": source_id,
                    "revision": revision,
                },
                "acceptance": {"required_checks": []},
            },
            "artifact_index": [artifact],
            "workspace_tree_hash": tree_hash,
        },
    )


def test_worker_facts_do_not_treat_repeated_inspect_checks_as_side_effects() -> None:
    run_binding, export_binding = _worker_binding("task_checks")
    export = {
        "task": {
            "task_id": "task_checks",
            "state": "completed",
            "reason": None,
            "spec": export_binding["spec"],
        },
        "artifact_index": export_binding["artifact_index"],
        "workspace_tree_hash": export_binding["workspace_tree_hash"],
        "operation_index": [
            {
                "operation_id": "check_before",
                "tool_name": "run_check",
                "intent_sha256": "a" * 64,
                "state": "completed",
                "side_effecting": False,
            },
            {
                "operation_id": "check_after",
                "tool_name": "run_check",
                "intent_sha256": "a" * 64,
                "state": "completed",
                "side_effecting": False,
            },
        ],
        "questions": [],
        "subtask_index": [],
    }
    trajectory = {
        "schema_version": "ATIF-v1.7",
        "session_id": "task_checks",
        "agent": {"name": "modelmirror-worker"},
        "steps": [{"source": "user", "message": "test instruction"}],
    }

    facts = HarnessFactSet.model_validate(
        ModelMirrorWorkerAgent._facts(
            export=export,
            events=[],
            approvals=(),
            trajectory=trajectory,
            run_binding=run_binding,
        )
    )

    assert derive_diagnostics(facts).duplicate_side_effects == 0


@pytest.mark.parametrize("missing", ("operation_index", "questions", "subtask_index"))
def test_worker_facts_fail_closed_when_a_fact_source_is_missing(missing: str) -> None:
    run_binding, export_binding = _worker_binding("task_missing")
    export = {
        "task": {
            "task_id": "task_missing",
            "state": "completed",
            "reason": None,
            "spec": export_binding["spec"],
        },
        "artifact_index": export_binding["artifact_index"],
        "workspace_tree_hash": export_binding["workspace_tree_hash"],
        "operation_index": [],
        "questions": [],
        "subtask_index": [],
    }
    export.pop(missing)
    trajectory = {
        "schema_version": "ATIF-v1.7",
        "session_id": "task_missing",
        "agent": {"name": "modelmirror-worker"},
        "steps": [{"source": "user", "message": "test instruction"}],
    }

    with pytest.raises(RuntimeError, match=f"omitted its {missing} fact source"):
        ModelMirrorWorkerAgent._facts(
            export=export,
            events=[],
            approvals=(),
            trajectory=trajectory,
            run_binding=run_binding,
        )


def test_worker_facts_detect_duplicate_reconcile_and_orphaned_approval() -> None:
    run_binding, export_binding = _worker_binding("task_reconcile")
    export = {
        "task": {
            "task_id": "task_reconcile",
            "state": "completed",
            "reason": None,
            "spec": export_binding["spec"],
        },
        "artifact_index": export_binding["artifact_index"],
        "workspace_tree_hash": export_binding["workspace_tree_hash"],
        "operation_index": [
            {
                "operation_id": "operation_side_effect",
                "tool_name": "run_shell",
                "intent_sha256": "d" * 64,
                "state": "completed",
                "side_effecting": True,
            }
        ],
        "questions": [],
        "subtask_index": [],
    }
    trajectory = {
        "schema_version": "ATIF-v1.7",
        "session_id": "task_reconcile",
        "agent": {"name": "modelmirror-worker"},
        "steps": [{"source": "user", "message": "test instruction"}],
    }
    events = [
        {
            "sequence": sequence,
            "event_type": "operation_reconciled",
            "payload": {
                "operation_id": "operation_side_effect",
                "state": "completed",
            },
        }
        for sequence in (21, 22)
    ] + [
        {
            "sequence": 23,
            "event_type": "approval_requested",
            "payload": {"approval_id": "approval_orphan", "capability": "command"},
        }
    ]

    facts = HarnessFactSet.model_validate(
        ModelMirrorWorkerAgent._facts(
            export=export,
            events=events,
            approvals=(),
            trajectory=trajectory,
            run_binding=run_binding,
        )
    )
    diagnostics = derive_diagnostics(facts)

    assert diagnostics.duplicate_side_effects == 1
    assert diagnostics.orphaned_interactions == 1


def test_worker_ledger_event_retains_only_fact_derivation_fields() -> None:
    event = ModelMirrorWorkerAgent._ledger_event(
        {
            "sequence": 31,
            "event_type": "operation_reconciled",
            "payload": {
                "operation_id": "operation_bound",
                "state": "completed",
                "secret": "must-not-survive",
            },
        }
    )

    assert event == {
        "sequence": 31,
        "event_type": "operation_reconciled",
        "payload": {"operation_id": "operation_bound", "state": "completed"},
    }


def test_worker_facts_preserve_recovered_platform_failures_from_the_event_ledger() -> None:
    run_binding, export_binding = _worker_binding("task_recovered")
    trajectory = {
        "schema_version": "ATIF-v1.7",
        "session_id": "task_recovered",
        "agent": {"name": "modelmirror-worker"},
        "steps": [{"source": "user", "message": "test instruction"}],
    }
    export = {
        "task": {
            "task_id": "task_recovered",
            "state": "completed",
            "reason": None,
            "spec": export_binding["spec"],
        },
        "artifact_index": export_binding["artifact_index"],
        "workspace_tree_hash": export_binding["workspace_tree_hash"],
        "operation_index": [],
        "questions": [],
        "subtask_index": [],
    }
    events = [
        {
            "sequence": 7,
            "event_type": "task_state",
            "payload": {
                "from": "running",
                "to": "interrupted",
                "reason": "provider_failed",
            },
        },
        {
            "sequence": 8,
            "event_type": "task_state",
            "payload": {
                "from": "running",
                "to": "waiting_approval",
                "reason": "turn_parked_approval",
            },
        },
    ]

    facts = HarnessFactSet.model_validate(
        ModelMirrorWorkerAgent._facts(
            export=export,
            events=events,
            approvals=(),
            trajectory=trajectory,
            run_binding=run_binding,
        )
    )

    assert facts.coordination == (
        HarnessCoordinationFact(
            evidence_id="event_task_recovered_7",
            stage=HarnessFailureStage.PROVIDER_PROTOCOL,
            failed=True,
        ),
    )
    assert derive_diagnostics(facts).platform_coordination_failures == 1


def test_worker_facts_reject_cross_task_trajectory_and_export() -> None:
    run_binding, export_binding = _worker_binding("task_B")
    export = {
        "task": {
            "task_id": "task_B",
            "state": "completed",
            "reason": None,
            "spec": export_binding["spec"],
        },
        "artifact_index": export_binding["artifact_index"],
        "workspace_tree_hash": export_binding["workspace_tree_hash"],
        "operation_index": [],
        "questions": [],
        "subtask_index": [],
    }
    trajectory = {
        "schema_version": "ATIF-v1.7",
        "session_id": "task_A",
        "agent": {"name": "modelmirror-worker"},
        "steps": [{"source": "user", "message": "test instruction"}],
    }

    with pytest.raises(RuntimeError, match="run binding changed"):
        ModelMirrorWorkerAgent._facts(
            export=export,
            events=[],
            approvals=(),
            trajectory=trajectory,
            run_binding=run_binding,
        )


def test_worker_ledger_rejects_workspace_artifact_tree_mismatch(
    tmp_path: Path,
) -> None:
    trial = tmp_path / "trial"
    agent_root = trial / "agent"
    workspace = trial / "artifacts" / "workspace"
    agent_root.mkdir(parents=True)
    workspace.mkdir(parents=True)
    (workspace / "result.py").write_text("VALUE = 1\n", encoding="utf-8")
    run_binding, export_binding = _worker_binding("task_bound")
    tree_hash = harness_cli._workspace_tree_hash(workspace)
    run_binding["workspace_artifact"]["metadata"]["workspace_tree_hash"] = tree_hash
    export_binding["workspace_tree_hash"] = tree_hash
    export = {
        "task": {
            "task_id": "task_bound",
            "state": "completed",
            "reason": None,
            "spec": export_binding["spec"],
        },
        "artifact_index": export_binding["artifact_index"],
        "workspace_tree_hash": export_binding["workspace_tree_hash"],
        "operation_index": [],
        "questions": [],
        "subtask_index": [],
    }
    trajectory = {
        "schema_version": "ATIF-v1.7",
        "session_id": "task_bound",
        "agent": {"name": "modelmirror-worker"},
        "steps": [{"source": "user", "message": "test instruction"}],
    }
    expected_facts = HarnessFactSet.model_validate(
        ModelMirrorWorkerAgent._facts(
            export=export,
            events=[],
            approvals=(),
            trajectory=trajectory,
            run_binding=run_binding,
        )
    )
    (agent_root / "modelmirror-harness-facts.json").write_text(
        expected_facts.model_dump_json(), encoding="utf-8"
    )
    (agent_root / "modelmirror-harness-ledger.json").write_text(
        json.dumps(
            {
                "run_binding": run_binding,
                "export": export,
                "events": [],
                "approvals": [],
            }
        ),
        encoding="utf-8",
    )

    facts, _summaries = _worker_facts(
        trial_dir=trial,
        trajectory=trajectory,
        expected_fixture_task_id="fixture-worker-binding",
        expected_source_id="source-worker-binding",
        expected_revision="a" * 64,
        expected_instruction_sha256=hashlib.sha256(b"test instruction").hexdigest(),
        expected_acceptance_sha256=_canonical_sha256({"required_checks": []}),
        expected_scenario_sha256=None,
        workspace_path=workspace,
    )
    assert facts == expected_facts

    (workspace / "result.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(HarnessCliError, match="workspace binding changed"):
        _worker_facts(
            trial_dir=trial,
            trajectory=trajectory,
            expected_fixture_task_id="fixture-worker-binding",
            expected_source_id="source-worker-binding",
            expected_revision="a" * 64,
            expected_instruction_sha256=hashlib.sha256(b"test instruction").hexdigest(),
            expected_acceptance_sha256=_canonical_sha256({"required_checks": []}),
            expected_scenario_sha256=None,
            workspace_path=workspace,
        )


@pytest.mark.parametrize(
    ("reason", "stage"),
    (
        ("compaction_failed", HarnessFailureStage.INTERACTION),
        ("shell_executor_failed", HarnessFailureStage.EXECUTOR),
        ("turn_parked_compaction", None),
    ),
)
def test_worker_failure_stage_classifies_platform_control_reasons(
    reason: str, stage: HarnessFailureStage | None
) -> None:
    observed = ModelMirrorWorkerAgent._failure_stage(reason)

    assert observed == (stage.value if stage is not None else None)


def test_run_record_rejects_caller_supplied_zero_diagnostics() -> None:
    facts = _facts()
    forged = derive_diagnostics(
        HarnessFactSet(export_sha256="e" * 64, trajectory_sha256="f" * 64)
    )

    with pytest.raises(ValidationError, match="not fact-derived"):
        HarnessRunRecord(
            run_id="run-one",
            task_id="python-one",
            engine="modelmirror-worker",
            attempt=1,
            candidate_sha="0" * 40,
            runner_image_sha256=f"sha256:{'0' * 64}",
            task_package_sha256="6" * 64,
            verifier_bundle_sha256="7" * 64,
            harbor_task_checksum="8" * 64,
            route_binding_sha256="9" * 64,
            sealed_checker_sha256="a" * 64,
            accepted=False,
            failure_stage=HarnessFailureStage.SCHEDULER,
            duration_seconds=1,
            artifacts=_artifacts(worker=True),
            facts=facts,
            diagnostics=forged,
        )


def test_run_record_cannot_accept_a_failed_agent_outcome_fact() -> None:
    facts = HarnessFactSet(
        export_sha256="e" * 64,
        trajectory_sha256="f" * 64,
        coordination=(
            HarnessCoordinationFact(
                evidence_id="event_agent_outcome",
                stage=HarnessFailureStage.AGENT_OUTCOME,
                failed=True,
            ),
        ),
    )

    with pytest.raises(ValidationError, match="failed outcome fact"):
        HarnessRunRecord(
            run_id="run-agent-outcome",
            task_id="python-agent-outcome",
            engine="modelmirror-worker",
            attempt=1,
            candidate_sha="0" * 40,
            runner_image_sha256=f"sha256:{'0' * 64}",
            task_package_sha256="6" * 64,
            verifier_bundle_sha256="7" * 64,
            harbor_task_checksum="8" * 64,
            route_binding_sha256="9" * 64,
            sealed_checker_sha256="a" * 64,
            accepted=True,
            failure_stage=None,
            duration_seconds=1,
            artifacts=_artifacts(worker=True),
            facts=facts,
            diagnostics=derive_diagnostics(facts),
        )


def test_run_record_cannot_accept_duplicate_side_effect_facts() -> None:
    facts = HarnessFactSet(
        export_sha256="e" * 64,
        trajectory_sha256="f" * 64,
        operations=(
            HarnessOperationFact(
                evidence_id="operation_one",
                operation_id="operation_one",
                intent_sha256="a" * 64,
                state="completed",
                side_effecting=True,
            ),
            HarnessOperationFact(
                evidence_id="operation_two",
                operation_id="operation_two",
                intent_sha256="a" * 64,
                state="completed",
                side_effecting=True,
            ),
        ),
    )

    with pytest.raises(ValidationError, match="unsettled platform fact"):
        HarnessRunRecord(
            run_id="run-duplicate",
            task_id="python-duplicate",
            engine="modelmirror-worker",
            attempt=1,
            candidate_sha="0" * 40,
            runner_image_sha256=f"sha256:{'0' * 64}",
            task_package_sha256="6" * 64,
            verifier_bundle_sha256="7" * 64,
            harbor_task_checksum="8" * 64,
            route_binding_sha256="9" * 64,
            sealed_checker_sha256="a" * 64,
            accepted=True,
            failure_stage=None,
            duration_seconds=1,
            artifacts=_artifacts(worker=True),
            facts=facts,
            diagnostics=derive_diagnostics(facts),
        )


def test_calibration_report_is_fact_derived_but_never_certifying() -> None:
    facts = _facts()
    run = HarnessRunRecord(
        run_id="run-one",
        task_id="python-one",
        engine="modelmirror-worker",
        attempt=1,
        candidate_sha="1" * 40,
        runner_image_sha256=f"sha256:{'3' * 64}",
        task_package_sha256="6" * 64,
        verifier_bundle_sha256="7" * 64,
        harbor_task_checksum="8" * 64,
        route_binding_sha256="9" * 64,
        sealed_checker_sha256="a" * 64,
        accepted=False,
        failure_stage=HarnessFailureStage.SCHEDULER,
        duration_seconds=1,
        artifacts=_artifacts(worker=True),
        facts=facts,
        diagnostics=derive_diagnostics(facts),
    )

    report = build_harness_report(
        report_mode="calibration",
        candidate_sha="1" * 40,
        fixture_bundle_sha256="2" * 64,
        sealed_checker_sha256="a" * 64,
        runner_image_sha256=f"sha256:{'3' * 64}",
        route_binding_sha256="9" * 64,
        runs=(run,),
    )

    assert report.diagnostics == run.diagnostics
    assert report_eligibility(report.model_dump(mode="json")) == "calibration"
    assert report_eligibility({"protocol": "modelmirror-coding-parity/v2"}) == (
        "structural_only"
    )

    with pytest.raises(ValidationError, match="route binding does not match"):
        build_harness_report(
            report_mode="calibration",
            candidate_sha="1" * 40,
            fixture_bundle_sha256="2" * 64,
            sealed_checker_sha256="a" * 64,
            runner_image_sha256=f"sha256:{'3' * 64}",
            route_binding_sha256="4" * 64,
            runs=(run,),
        )

    with pytest.raises(ValidationError, match="sealed checker does not match"):
        build_harness_report(
            report_mode="calibration",
            candidate_sha="1" * 40,
            fixture_bundle_sha256="2" * 64,
            sealed_checker_sha256="b" * 64,
            runner_image_sha256=f"sha256:{'3' * 64}",
            route_binding_sha256="9" * 64,
            runs=(run,),
        )

    with pytest.raises(ValidationError, match="candidate does not match"):
        build_harness_report(
            report_mode="calibration",
            candidate_sha="2" * 40,
            fixture_bundle_sha256="2" * 64,
            sealed_checker_sha256="a" * 64,
            runner_image_sha256=f"sha256:{'3' * 64}",
            route_binding_sha256="9" * 64,
            runs=(run,),
        )

    with pytest.raises(ValidationError, match="runner image does not match"):
        build_harness_report(
            report_mode="calibration",
            candidate_sha="1" * 40,
            fixture_bundle_sha256="2" * 64,
            sealed_checker_sha256="a" * 64,
            runner_image_sha256=f"sha256:{'4' * 64}",
            route_binding_sha256="9" * 64,
            runs=(run,),
        )


def test_report_does_not_treat_same_intent_in_independent_runs_as_replay() -> None:
    def accepted(run_id: str, operation_id: str) -> HarnessRunRecord:
        facts = HarnessFactSet(
            export_sha256=hashlib.sha256(f"{run_id}:export".encode()).hexdigest(),
            trajectory_sha256=hashlib.sha256(
                f"{run_id}:trajectory".encode()
            ).hexdigest(),
            operations=(
                HarnessOperationFact(
                    evidence_id=f"evidence_{operation_id}",
                    operation_id=operation_id,
                    intent_sha256="a" * 64,
                    state="completed",
                    side_effecting=True,
                ),
            ),
        )
        return HarnessRunRecord(
            run_id=run_id,
            task_id="python-one",
            engine="modelmirror-worker",
            attempt=1,
            candidate_sha="1" * 40,
            runner_image_sha256=f"sha256:{'3' * 64}",
            task_package_sha256="6" * 64,
            verifier_bundle_sha256="7" * 64,
            harbor_task_checksum="8" * 64,
            route_binding_sha256="9" * 64,
            sealed_checker_sha256="a" * 64,
            accepted=True,
            duration_seconds=1,
            artifacts=_artifacts(worker=True),
            facts=facts,
            diagnostics=derive_diagnostics(facts),
        )

    report = build_harness_report(
        report_mode="calibration",
        candidate_sha="1" * 40,
        fixture_bundle_sha256="2" * 64,
        sealed_checker_sha256="a" * 64,
        runner_image_sha256=f"sha256:{'3' * 64}",
        route_binding_sha256="9" * 64,
        runs=(accepted("run-one", "operation_one"), accepted("run-two", "operation_two")),
    )

    assert report.diagnostics.duplicate_side_effects == 0


def test_harbor_gate_reads_trial_reward_not_cli_success(tmp_path: Path) -> None:
    job = tmp_path / "job"
    trial = job / "trial"
    trial.mkdir(parents=True)
    (job / "result.json").write_text(
        json.dumps({"stats": {"n_completed_trials": 1}}), encoding="utf-8"
    )
    (trial / "result.json").write_text(
        json.dumps(
            {
                "exception_info": None,
                "verifier_result": {"rewards": {"reward": 0.0}},
            }
        ),
        encoding="utf-8",
    )

    assert _harbor_trial_reward(tmp_path) == 0.0


def test_sealed_checker_is_bound_outside_public_task_and_materialized_only_for_harbor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public_root = tmp_path / "public"
    task_root = public_root / "tasks" / "python-sealed"
    tests_root = task_root / "tests"
    tests_root.mkdir(parents=True)
    (tests_root / "test.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    checker = b"print('sealed')\n"
    checker_sha256 = hashlib.sha256(checker).hexdigest()
    (task_root / "task.toml").write_text(
        "\n".join(
            (
                "version = '1.0'",
                "[metadata.modelmirror]",
                "sealed_checker_file = 'test_hidden.py'",
                f"sealed_checker_sha256 = '{checker_sha256}'",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    sealed_root = tmp_path / "sealed"
    sealed_task = sealed_root / "python-sealed"
    sealed_task.mkdir(parents=True)
    (sealed_task / "test_hidden.py").write_bytes(checker)
    verifier_sha256 = _canonical_sha256(
        {
            "public_verifier_sha256": _tree_digest(tests_root),
            "sealed_checker_file": "test_hidden.py",
            "sealed_checker_sha256": checker_sha256,
        }
    )
    fixture = _fixture("python-sealed", "python").model_copy(
        update={"verifier_bundle_sha256": verifier_sha256}
    )
    bundle = HarnessFixtureBundle.model_construct(fixtures=(fixture,))

    observed = _validate_sealed_checkers(
        sealed_root, bundle, public_root=public_root
    )
    assert len(observed) == 64
    changed_bundle = HarnessFixtureBundle.model_construct(
        fixtures=(fixture.model_copy(update={"task_package_sha256": "c" * 64}),)
    )
    assert (
        _validate_sealed_checkers(
            sealed_root, changed_bundle, public_root=public_root
        )
        != observed
    )
    assert not (tests_root / "test_hidden.py").exists()
    monkeypatch.setattr(harness_cli, "_validate_materialized_public_tasks", lambda *_: None)
    with _materialized_tasks_root(
        public_root,
        sealed_root,
        bundle,
        harbor_executable="harbor",
        checksum_task=lambda _executable, _root: ("a" * 64, "b" * 64),
    ) as (
        materialized,
        materialized_sha256,
        task_checksums,
    ):
        assert materialized_sha256 == observed
        assert task_checksums == {
            "python-sealed": {"content": "a" * 64, "result": "b" * 64}
        }
        assert (materialized / "python-sealed" / "tests" / "test_hidden.py").read_bytes() == checker
    assert not (tests_root / "test_hidden.py").exists()

    (sealed_task / "test_hidden.py").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(HarnessCliError, match="sealed checker hash changed"):
        _validate_sealed_checkers(sealed_root, bundle, public_root=public_root)


def test_materialized_public_task_bytes_must_match_the_frozen_fixture(
    tmp_path: Path,
) -> None:
    public_task = (
        Path(__file__).resolve().parents[2]
        / "benchmarks"
        / "coding-worker-v18"
        / "tasks"
        / "python-async-cache"
    )
    fixture, _binding = harness_cli._task_fixture(public_task)
    copied_tasks = tmp_path / "tasks"
    copied_task = copied_tasks / fixture.task_id
    import shutil

    shutil.copytree(public_task, copied_task)
    bundle = HarnessFixtureBundle.model_construct(fixtures=(fixture,))
    _validate_materialized_public_tasks(copied_tasks, bundle)

    instruction = copied_task / "instruction.md"
    instruction.write_text(
        instruction.read_text(encoding="utf-8") + "\nmutated after freeze\n",
        encoding="utf-8",
    )
    with pytest.raises(HarnessCliError, match="materialized public task changed"):
        _validate_materialized_public_tasks(copied_tasks, bundle)


@pytest.mark.parametrize(
    "payload",
    (
        {"exception_info": {"type": "VerifierError"}, "verifier_result": None},
        {"exception_info": None, "verifier_result": None},
        {"exception_info": None, "verifier_result": {"rewards": {"reward": True}}},
        {"exception_info": None, "verifier_result": {"rewards": {"reward": 2}}},
    ),
)
def test_harbor_gate_rejects_unprovable_rewards(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    trial = tmp_path / "job" / "trial"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(HarnessCliError):
        _harbor_trial_reward(tmp_path)


def test_harbor_batch_gate_reads_only_bound_numeric_trials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(
        command: list[str], *, env: dict[str, str], check: bool
    ) -> None:
        assert check is False
        assert Path(env["PYTHONPATH"].split(os.pathsep)[0]).samefile(
            harness_cli.REPOSITORY_ROOT
        )
        jobs_root = Path(command[command.index("--jobs-dir") + 1])
        job_name = command[command.index("--job-name") + 1]
        trial = jobs_root / job_name / "python-async-cache__attempt-1"
        trial.mkdir(parents=True)
        (trial / "result.json").write_text(
            json.dumps(
                {
                    "task_name": "modelmirror-coding-v18/python-async-cache",
                    "exception_info": None,
                    "verifier_result": {"rewards": {"reward": 1.0}},
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(harness_cli.subprocess, "run", fake_run)
    assert harness_cli._run_harbor_batch_gate(
        "harbor",
        tasks_root=tmp_path / "tasks",
        agent="oracle",
        repetitions=1,
        n_concurrent=1,
        environment_type="test:environment",
    ) == {"python-async-cache": (1.0,)}


def test_harbor_batch_gate_rejects_exception_backed_zero_reward(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(
        command: list[str], *, env: dict[str, str], check: bool
    ) -> None:
        jobs_root = Path(command[command.index("--jobs-dir") + 1])
        job_name = command[command.index("--job-name") + 1]
        trial = jobs_root / job_name / "python-async-cache__attempt-1"
        trial.mkdir(parents=True)
        (trial / "result.json").write_text(
            json.dumps(
                {
                    "task_name": "modelmirror-coding-v18/python-async-cache",
                    "exception_info": {"type": "HarnessError"},
                    "verifier_result": {"rewards": {"reward": 0.0}},
                }
            ),
            encoding="utf-8",
        )

    monkeypatch.setattr(harness_cli.subprocess, "run", fake_run)
    with pytest.raises(HarnessCliError, match="invalid trial"):
        harness_cli._run_harbor_batch_gate(
            "harbor",
            tasks_root=tmp_path / "tasks",
            agent="nop",
            repetitions=1,
            n_concurrent=1,
            environment_type="test:environment",
        )


def test_deterministic_fake_runner_covers_public_record_contract() -> None:
    fixture = _fixture("python-fake-smoke", "python")

    native = harness_cli.DeterministicHarnessFakeRunner.run(
        fixture, "native-opencode"
    )
    worker = harness_cli.DeterministicHarnessFakeRunner.run(
        fixture, "modelmirror-worker"
    )

    assert native.accepted is True
    assert {item.artifact_id for item in native.artifacts} == {
        "harbor_result",
        "native_ledger",
        "workspace",
        "trajectory",
    }
    assert {item.artifact_id for item in worker.artifacts}.issuperset(
        {"worker_facts", "worker_ledger"}
    )


def test_workspace_archive_validation_rejects_links(tmp_path: Path) -> None:
    archive_path = tmp_path / "workspace.tar"
    with tarfile.open(archive_path, "w:") as archive:
        member = tarfile.TarInfo("linked")
        member.type = tarfile.SYMTYPE
        member.linkname = "outside"
        archive.addfile(member)
    content = archive_path.read_bytes()

    with pytest.raises(RuntimeError, match="unsafe"):
        ModelMirrorWorkerAgent._validate_workspace_archive(
            archive_path,
            expected_sha256=hashlib.sha256(content).hexdigest(),
            expected_size=len(content),
        )


def test_workspace_install_rechecks_remote_artifact_binding() -> None:
    command = ModelMirrorWorkerAgent._workspace_install_command(
        "/tmp/modelmirror-workspace-0123456789abcdef.tar",
        expected_sha256="a" * 64,
        expected_size=1234,
    )

    assert "sha256sum /tmp/modelmirror-workspace-0123456789abcdef.tar" in command
    assert "awk '{print $1}'" in command
    assert "wc -c < /tmp/modelmirror-workspace-0123456789abcdef.tar" in command
    assert '"' + "a" * 64 + '"' in command
    assert '"1234"' in command
    assert command.index("sha256sum") < command.index("tar -xf")


def _native_trial(tmp_path: Path, *, observed: bool) -> Path:
    job = tmp_path / "run_python-async-cache_native-opencode_1"
    trial = job / "trial"
    (trial / "agent").mkdir(parents=True)
    workspace = trial / "artifacts" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    results = (
        [{"source_call_id": "call_1", "content": "ok"}] if observed else []
    )
    (trial / "agent" / "trajectory.json").write_text(
        json.dumps(
            {
                "schema_version": "ATIF-v1.7",
                "session_id": "session_1",
                "agent": {
                    "name": "opencode",
                    "version": "1.18.9",
                    "model_name": "test/model",
                },
                "steps": [
                    {
                        "step_id": 1,
                        "source": "user",
                        "message": "test instruction",
                    },
                    {
                        "step_id": 2,
                        "source": "agent",
                        "tool_calls": [
                            {
                                "tool_call_id": "call_1",
                                "function_name": "bash",
                                "arguments": {"command": "pytest"},
                            }
                        ],
                        "observation": {"results": results},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (trial / "agent" / "modelmirror-native-harness-ledger.json").write_text(
        json.dumps(
            {
                "schema": "modelmirror-native-opencode-control/v1",
                "run_binding": {
                    "task_id": "python-async-cache",
                    "instruction_sha256": hashlib.sha256(
                        b"test instruction"
                    ).hexdigest(),
                    "scenario_sha256": None,
                    "session_id": "session_1",
                    "environment_id": "a" * 32,
                    "model_name": "test/model",
                    "opencode_version": "1.18.9",
                },
                "events": [],
                "control": [],
                "scenario_contract": {
                    "required_events": [],
                    "action_ids": [],
                    "question_prompt_sha256": [],
                },
                "public_messages": [],
                "public_messages_sha256": _canonical_sha256([]),
            }
        ),
        encoding="utf-8",
    )
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": "modelmirror-coding-v18/python-async-cache",
                "task_checksum": "9" * 64,
                "agent_info": {"name": "opencode", "version": "1.18.9"},
                "agent_result": {"n_input_tokens": 10, "n_output_tokens": 4},
                "verifier_result": {"rewards": {"reward": 1.0}},
                "exception_info": None,
                "started_at": "2026-08-20T00:00:00Z",
                "finished_at": "2026-08-20T00:00:02Z",
            }
        ),
        encoding="utf-8",
    )
    return job


def test_native_run_record_is_derived_from_trial_and_trajectory(tmp_path: Path) -> None:
    record = _collect_run_record(
        job_root=_native_trial(tmp_path, observed=True),
        run_id="run_python-async-cache_native-opencode_1",
        task_id="python-async-cache",
        engine="native-opencode",
        attempt=1,
        candidate_sha="1" * 40,
        runner_image_sha256=f"sha256:{'3' * 64}",
        task_package_sha256="7" * 64,
        verifier_bundle_sha256="6" * 64,
        route_binding_sha256="5" * 64,
        sealed_checker_sha256="4" * 64,
        expected_model_name="test/model",
        native_command_policy={"pytest": False},
    )

    assert record.accepted is True
    assert record.failure_stage is None
    assert record.duration_seconds == 2
    assert record.input_tokens == 10
    assert record.diagnostics.unsettled_operations == 0


def test_native_run_record_rejects_a_changed_instruction_binding(tmp_path: Path) -> None:
    job = _native_trial(tmp_path, observed=True)
    trajectory_path = job / "trial" / "agent" / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    trajectory["steps"][0]["message"] = "tampered instruction"
    trajectory_path.write_text(json.dumps(trajectory), encoding="utf-8")

    with pytest.raises(HarnessCliError, match="instruction binding changed"):
        _collect_run_record(
            job_root=job,
            run_id="run_python-async-cache_native-opencode_1",
            task_id="python-async-cache",
            engine="native-opencode",
            attempt=1,
            candidate_sha="1" * 40,
            runner_image_sha256=f"sha256:{'3' * 64}",
            task_package_sha256="7" * 64,
            verifier_bundle_sha256="6" * 64,
            route_binding_sha256="5" * 64,
            sealed_checker_sha256="4" * 64,
            expected_model_name="test/model",
            native_command_policy={"pytest": False},
        )


def test_run_record_rejects_a_harbor_checksum_not_bound_to_materialization(
    tmp_path: Path,
) -> None:
    with pytest.raises(HarnessCliError, match="task checksum changed"):
        _collect_run_record(
            job_root=_native_trial(tmp_path, observed=True),
            run_id="run_python-async-cache_native-opencode_1",
            task_id="python-async-cache",
            engine="native-opencode",
            attempt=1,
            candidate_sha="1" * 40,
            runner_image_sha256=f"sha256:{'3' * 64}",
            task_package_sha256="7" * 64,
            verifier_bundle_sha256="6" * 64,
            route_binding_sha256="5" * 64,
            sealed_checker_sha256="4" * 64,
            expected_model_name="test/model",
            expected_harbor_task_checksum="8" * 64,
            native_command_policy={"pytest": False},
        )


def test_native_unfrozen_bash_command_is_a_policy_failure(tmp_path: Path) -> None:
    job = _native_trial(tmp_path, observed=True)
    trajectory_path = job / "trial" / "agent" / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    trajectory["steps"][1]["tool_calls"][0]["arguments"]["command"] = "curl example.com"
    trajectory_path.write_text(json.dumps(trajectory), encoding="utf-8")

    record = _collect_run_record(
        job_root=job,
        run_id="run_python-async-cache_native-opencode_1",
        task_id="python-async-cache",
        engine="native-opencode",
        attempt=1,
        candidate_sha="1" * 40,
        runner_image_sha256=f"sha256:{'3' * 64}",
        task_package_sha256="7" * 64,
        verifier_bundle_sha256="6" * 64,
        route_binding_sha256="5" * 64,
        sealed_checker_sha256="4" * 64,
        expected_model_name="test/model",
        native_command_policy={"pytest": False},
    )

    assert record.accepted is False
    assert record.failure_stage is HarnessFailureStage.POLICY


def test_native_tool_budget_is_fact_derived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(harness_cli, "CALIBRATION_MAX_TOOL_CALLS", 0)
    record = _collect_run_record(
        job_root=_native_trial(tmp_path, observed=True),
        run_id="run_python-async-cache_native-opencode_1",
        task_id="python-async-cache",
        engine="native-opencode",
        attempt=1,
        candidate_sha="1" * 40,
        runner_image_sha256=f"sha256:{'3' * 64}",
        task_package_sha256="7" * 64,
        verifier_bundle_sha256="6" * 64,
        route_binding_sha256="5" * 64,
        sealed_checker_sha256="4" * 64,
        expected_model_name="test/model",
        native_command_policy={"pytest": False},
    )

    assert record.accepted is False
    assert record.failure_stage is HarnessFailureStage.BUDGET


@pytest.mark.parametrize(
    ("side_effecting", "accepted", "duplicates"),
    ((False, True, 0), (True, False, 1)),
)
def test_repeated_native_command_distinguishes_inspect_from_mutate(
    tmp_path: Path,
    side_effecting: bool,
    accepted: bool,
    duplicates: int,
) -> None:
    job = _native_trial(tmp_path, observed=True)
    trajectory_path = job / "trial" / "agent" / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    duplicate = json.loads(json.dumps(trajectory["steps"][1]["tool_calls"][0]))
    duplicate["tool_call_id"] = "call_2"
    trajectory["steps"][1]["tool_calls"].append(duplicate)
    trajectory["steps"][1]["observation"]["results"].append(
        {"source_call_id": "call_2", "content": "ok"}
    )
    trajectory_path.write_text(json.dumps(trajectory), encoding="utf-8")

    record = _collect_run_record(
        job_root=job,
        run_id="run_python-async-cache_native-opencode_1",
        task_id="python-async-cache",
        engine="native-opencode",
        attempt=1,
        candidate_sha="1" * 40,
        runner_image_sha256=f"sha256:{'3' * 64}",
        task_package_sha256="7" * 64,
        verifier_bundle_sha256="6" * 64,
        route_binding_sha256="5" * 64,
        sealed_checker_sha256="4" * 64,
        expected_model_name="test/model",
        native_command_policy={"pytest": side_effecting},
    )

    assert record.accepted is accepted
    assert record.diagnostics.duplicate_side_effects == duplicates


def test_unsettled_native_tool_cannot_pass_even_when_checker_passes(
    tmp_path: Path,
) -> None:
    record = _collect_run_record(
        job_root=_native_trial(tmp_path, observed=False),
        run_id="run_python-async-cache_native-opencode_1",
        task_id="python-async-cache",
        engine="native-opencode",
        attempt=1,
        candidate_sha="1" * 40,
        runner_image_sha256=f"sha256:{'3' * 64}",
        task_package_sha256="7" * 64,
        verifier_bundle_sha256="6" * 64,
        route_binding_sha256="5" * 64,
        sealed_checker_sha256="4" * 64,
        expected_model_name="test/model",
        native_command_policy={"pytest": False},
    )

    assert record.accepted is False
    assert record.failure_stage is HarnessFailureStage.TOOL_VALIDATION
    assert record.diagnostics.unsettled_operations == 1


def _bind_native_reconciled_fault(job: Path, *, result_sha256: str) -> None:
    trajectory_path = job / "trial" / "agent" / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    trajectory["steps"][1]["tool_calls"][0]["arguments"] = {
        "command": FAULT_COMMAND,
        "workdir": "/workspace",
    }
    trajectory_path.write_text(json.dumps(trajectory), encoding="utf-8")
    intent_sha256 = _canonical_sha256(
        {
            "function_name": "bash",
            "arguments": {"command": FAULT_COMMAND, "workdir": "/workspace"},
        }
    )
    resume_message = "resume after exact reconciliation"
    ledger_path = job / "trial" / "agent" / "modelmirror-native-harness-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["control"] = [
        {
            "event_type": "operation_unknown",
            "operation_id": "call_1",
            "intent_sha256": intent_sha256,
        },
        {
            "event_type": "component_fault_injected",
            "component": "executor",
            "operation_id": "call_1",
        },
        {
            "event_type": "operation_reconciled",
            "operation_id": "call_1",
            "intent_sha256": intent_sha256,
            "result_sha256": result_sha256,
        },
        {
            "event_type": "resume_sent",
            "operation_id": "call_1",
            "message_sha256": hashlib.sha256(resume_message.encode()).hexdigest(),
        },
    ]
    ledger["public_messages"] = [
        {
            "role": "user",
            "parts": [{"type": "text", "text": resume_message}],
        }
    ]
    ledger["public_messages_sha256"] = _canonical_sha256(ledger["public_messages"])
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")


def test_marker_bound_native_reconcile_settles_the_original_operation(
    tmp_path: Path,
) -> None:
    job = _native_trial(tmp_path, observed=False)
    _bind_native_reconciled_fault(
        job,
        result_sha256=_canonical_sha256(
            {"command": FAULT_COMMAND, "exit_code": 0}
        ),
    )

    record = _collect_run_record(
        job_root=job,
        run_id="run_python-async-cache_native-opencode_1",
        task_id="python-async-cache",
        engine="native-opencode",
        attempt=1,
        candidate_sha="1" * 40,
        runner_image_sha256=f"sha256:{'3' * 64}",
        task_package_sha256="7" * 64,
        verifier_bundle_sha256="6" * 64,
        route_binding_sha256="5" * 64,
        sealed_checker_sha256="4" * 64,
        expected_model_name="test/model",
        native_command_policy={FAULT_COMMAND: True},
    )

    assert record.accepted is True
    assert record.diagnostics.unsettled_operations == 0
    assert record.diagnostics.platform_coordination_failures == 0
    assert record.facts.operations[0].state == "completed"


def test_native_reconcile_rejects_an_unbound_result_marker(tmp_path: Path) -> None:
    job = _native_trial(tmp_path, observed=False)
    _bind_native_reconciled_fault(job, result_sha256="0" * 64)

    record = _collect_run_record(
        job_root=job,
        run_id="run_python-async-cache_native-opencode_1",
        task_id="python-async-cache",
        engine="native-opencode",
        attempt=1,
        candidate_sha="1" * 40,
        runner_image_sha256=f"sha256:{'3' * 64}",
        task_package_sha256="7" * 64,
        verifier_bundle_sha256="6" * 64,
        route_binding_sha256="5" * 64,
        sealed_checker_sha256="4" * 64,
        expected_model_name="test/model",
        native_command_policy={FAULT_COMMAND: True},
    )

    assert record.accepted is False
    assert record.failure_stage is HarnessFailureStage.TOOL_VALIDATION
    assert record.diagnostics.unsettled_operations == 1
    assert record.diagnostics.platform_coordination_failures == 0
    assert record.facts.coordination[0].stage is HarnessFailureStage.TOOL_VALIDATION


def test_native_control_ledger_rejects_unproved_messages_and_unknown_results(
    tmp_path: Path,
) -> None:
    job = _native_trial(tmp_path, observed=True)
    ledger_path = job / "trial" / "agent" / "modelmirror-native-harness-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["control"] = [
        {
            "event_type": "initial_prompt",
            "message_sha256": hashlib.sha256(b"missing public prompt").hexdigest(),
        },
        {
            "event_type": "operation_unknown",
            "operation_id": "call_1",
        },
    ]
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    record = _collect_run_record(
        job_root=job,
        run_id="run_python-async-cache_native-opencode_1",
        task_id="python-async-cache",
        engine="native-opencode",
        attempt=1,
        candidate_sha="1" * 40,
        runner_image_sha256=f"sha256:{'3' * 64}",
        task_package_sha256="7" * 64,
        verifier_bundle_sha256="6" * 64,
        route_binding_sha256="5" * 64,
        sealed_checker_sha256="4" * 64,
        expected_model_name="test/model",
        native_command_policy={"pytest": False},
    )

    assert record.accepted is False
    assert record.failure_stage in {
        HarnessFailureStage.INTERACTION,
        HarnessFailureStage.TOOL_VALIDATION,
    }
    assert len(record.facts.coordination) == 2
    assert record.diagnostics.platform_coordination_failures == 1


def test_native_run_rejects_changed_model_binding(tmp_path: Path) -> None:
    with pytest.raises(HarnessCliError, match="model binding changed"):
        _collect_run_record(
            job_root=_native_trial(tmp_path, observed=True),
            run_id="run_python-async-cache_native-opencode_1",
            task_id="python-async-cache",
            engine="native-opencode",
            attempt=1,
            candidate_sha="1" * 40,
            runner_image_sha256=f"sha256:{'3' * 64}",
            task_package_sha256="7" * 64,
            verifier_bundle_sha256="6" * 64,
            route_binding_sha256="5" * 64,
            sealed_checker_sha256="4" * 64,
            expected_model_name="other/model",
            native_command_policy={"pytest": False},
        )


def test_accepted_run_requires_provable_nonzero_token_usage(tmp_path: Path) -> None:
    job = _native_trial(tmp_path, observed=True)
    result_path = job / "trial" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["agent_result"] = {"n_input_tokens": 0, "n_output_tokens": 0}
    result_path.write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(HarnessCliError, match="token usage"):
        _collect_run_record(
            job_root=job,
            run_id="run_python-async-cache_native-opencode_1",
            task_id="python-async-cache",
            engine="native-opencode",
            attempt=1,
            candidate_sha="1" * 40,
            runner_image_sha256=f"sha256:{'3' * 64}",
            task_package_sha256="7" * 64,
            verifier_bundle_sha256="6" * 64,
            route_binding_sha256="5" * 64,
            sealed_checker_sha256="4" * 64,
            expected_model_name="test/model",
            native_command_policy={"pytest": False},
        )


def test_native_exception_is_a_fact_derived_coordination_failure(
    tmp_path: Path,
) -> None:
    job = _native_trial(tmp_path, observed=True)
    result_path = job / "trial" / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["exception_info"] = {"type": "ProviderConnectionError"}
    result_path.write_text(json.dumps(result), encoding="utf-8")

    record = _collect_run_record(
        job_root=job,
        run_id="run_python-async-cache_native-opencode_1",
        task_id="python-async-cache",
        engine="native-opencode",
        attempt=1,
        candidate_sha="1" * 40,
        runner_image_sha256=f"sha256:{'3' * 64}",
        task_package_sha256="7" * 64,
        verifier_bundle_sha256="6" * 64,
        route_binding_sha256="5" * 64,
        sealed_checker_sha256="4" * 64,
        expected_model_name="test/model",
        native_command_policy={"pytest": False},
    )

    assert record.accepted is False
    assert record.failure_stage is HarnessFailureStage.PROVIDER_TRANSPORT
    assert record.diagnostics.platform_coordination_failures == 1
    assert record.diagnostics.evidence["platform_coordination_failures"]


def test_native_run_rejects_non_atif_trajectory(tmp_path: Path) -> None:
    job = _native_trial(tmp_path, observed=True)
    trajectory_path = job / "trial" / "agent" / "trajectory.json"
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8"))
    trajectory["schema_version"] = "ATIF-v1.6"
    trajectory_path.write_text(json.dumps(trajectory), encoding="utf-8")

    with pytest.raises(HarnessCliError, match="ATIF-v1.7"):
        _collect_run_record(
            job_root=job,
            run_id="run_python-async-cache_native-opencode_1",
            task_id="python-async-cache",
            engine="native-opencode",
            attempt=1,
            candidate_sha="1" * 40,
            runner_image_sha256=f"sha256:{'3' * 64}",
            task_package_sha256="7" * 64,
            verifier_bundle_sha256="6" * 64,
            route_binding_sha256="5" * 64,
            sealed_checker_sha256="4" * 64,
            expected_model_name="test/model",
            native_command_policy={"pytest": False},
        )


def test_task_package_hardlink_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "task"
    root.mkdir()
    original = root / "original.txt"
    original.write_text("bound", encoding="utf-8")
    try:
        os.link(original, root / "alias.txt")
    except OSError:
        pytest.skip("hardlinks are unavailable on this host")

    with pytest.raises(HarnessCliError, match="unsafe file"):
        _validate_regular_tree(root)


def test_task_package_rejects_dependencies_installed_in_exported_workspace(
    tmp_path: Path,
) -> None:
    task = tmp_path / "typescript-task"
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "environment" / "Dockerfile").write_text(
        "FROM node:24\nWORKDIR /workspace\nCOPY project/package*.json ./\n"
        "RUN npm ci --ignore-scripts\n",
        encoding="utf-8",
    )
    (task / "tests" / "Dockerfile").write_text(
        "FROM node:24\nCOPY . /tests\n", encoding="utf-8"
    )

    with pytest.raises(HarnessCliError, match="exported workspace"):
        _validate_docker_boundaries(task)
