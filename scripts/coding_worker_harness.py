from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import http.client
import ipaddress
import json
import os
import random
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import tomllib
import uuid
import urllib.parse
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from server.coding_worker.harness_v3 import (
    HARBOR_VERSION,
    HARNESS_PROTOCOL,
    NATIVE_OPENCODE_VERSION,
    PROVIDER_HARNESS_CODE_FILES,
    SERVER_HARNESS_CODE_FILES,
    HarnessFixture,
    HarnessArtifactSummary,
    HarnessCoordinationFact,
    HarnessFactSet,
    HarnessFailureStage,
    HarnessFixtureBundle,
    HarnessFixtureFile,
    HarnessInteractionFact,
    HarnessOperationFact,
    HarnessReport,
    HarnessRunRecord,
    HarnessVisibleCheck,
    build_harness_report,
    derive_diagnostics,
    harness_code_bundle_sha256,
    load_harness_fixture_bundle,
    report_eligibility,
)


DEFAULT_ROOT = Path("benchmarks/coding-worker-v18")
TASK_SCHEMA_VERSION = "1.4"
CALIBRATION_MAX_TURNS = 128
CALIBRATION_MAX_TOOL_CALLS = 512
CALIBRATION_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
CALIBRATION_MAX_TOTAL_TOKENS = 200_000
DOCKER_DESKTOP_PROBE_ENVIRONMENT = (
    "scripts.coding_worker_harbor_environment:"
    "DockerDesktopAllowlistProbeEnvironment"
)
NATIVE_TASK_RUNTIME_IMAGE = "modelmirror-coding-worker-v14:local"
NATIVE_TASK_RUNTIME_IMAGE_SHA256 = (
    "sha256:7c2a7f29867cbc35c236a0c0e62201af0ff0c3c6eb9749f8e5f7bbfb259eab39"
)


class HarnessCliError(RuntimeError):
    pass


def _native_task_runtime_image_sha256() -> str:
    completed = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            NATIVE_TASK_RUNTIME_IMAGE,
            "--format",
            "{{.Id}}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    observed = completed.stdout.strip().lower()
    if completed.returncode != 0 or observed != NATIVE_TASK_RUNTIME_IMAGE_SHA256:
        raise HarnessCliError("native task runtime image is unavailable or changed")
    return observed


def _assert_native_runtime_fixture_coverage(
    root: Path, bundle: HarnessFixtureBundle
) -> None:
    expected = f"FROM {NATIVE_TASK_RUNTIME_IMAGE}"
    missing = []
    for fixture in bundle.fixtures:
        dockerfile = root / "tasks" / fixture.task_id / "environment" / "Dockerfile"
        if expected not in dockerfile.read_text(encoding="utf-8"):
            missing.append(fixture.task_id)
    if missing:
        raise HarnessCliError(
            "native offline runtime coverage is incomplete: " + ",".join(missing)
        )


def _harbor_subprocess_environment() -> dict[str, str]:
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(REPOSITORY_ROOT), existing) if value
    )
    environment.setdefault("PYTHONUTF8", "1")
    environment.setdefault("PYTHONIOENCODING", "utf-8")
    return environment


def _harbor_engine_environment(
    base: Mapping[str, str],
    *,
    engine: str,
    controller_token: str,
    benchmark_root: Path,
    worker_url: str,
    worker_model_route: str,
) -> dict[str, str]:
    environment = dict(base)
    for variable in tuple(environment):
        if variable.startswith("OPENCODE_"):
            environment.pop(variable, None)
    for variable in (
        "CODING_WORKER_HARNESS_CONTROLLER_TOKEN",
        "MODELMIRROR_HARNESS_CONTROLLER_TOKEN",
        "MODELMIRROR_HARBOR_BENCHMARK_ROOT",
        "MODELMIRROR_WORKER_URL",
        "MODELMIRROR_WORKER_MODEL_ROUTE",
    ):
        environment.pop(variable, None)
    if engine in {"modelmirror-worker", "native-opencode"}:
        environment["MODELMIRROR_HARBOR_BENCHMARK_ROOT"] = str(benchmark_root)
    if engine == "modelmirror-worker":
        environment.update(
            {
                "MODELMIRROR_HARNESS_CONTROLLER_TOKEN": controller_token,
                "MODELMIRROR_WORKER_URL": worker_url,
                "MODELMIRROR_WORKER_MODEL_ROUTE": worker_model_route,
            }
        )
    elif engine != "native-opencode":
        raise HarnessCliError("Harness engine is invalid")
    return environment


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


def _model_agent_hosts(values: list[str]) -> tuple[str, ...]:
    hosts = tuple(sorted({value.strip().lower() for value in values if value.strip()}))
    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    pattern = re.compile(rf"^{label}(?:\.{label})*$")
    if not hosts:
        raise HarnessCliError("at least one exact model gateway hostname is required")
    for host in hosts:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise HarnessCliError("model gateway IP literals are not allowed")
        if (
            host == "localhost"
            or host.endswith(".localhost")
            or pattern.fullmatch(host) is None
        ):
            raise HarnessCliError("model gateway hostname is invalid")
    return hosts


def _require_frozen_candidate(candidate_sha: str) -> None:
    if re.fullmatch(r"[a-f0-9]{40}", candidate_sha) is None:
        raise HarnessCliError("candidate SHA must be a full lowercase commit id")
    resolved = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if resolved.returncode != 0 or resolved.stdout.strip().lower() != candidate_sha:
        raise HarnessCliError("candidate SHA does not match the evaluation checkout")
    status = subprocess.run(
        ["git", "-C", str(REPOSITORY_ROOT), "status", "--porcelain=v1"],
        check=False,
        capture_output=True,
        text=True,
    )
    if status.returncode != 0 or status.stdout.strip():
        raise HarnessCliError("evaluation candidate checkout is not clean")


def _loopback_worker_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        address = ipaddress.ip_address(parsed.hostname or "")
        port = parsed.port
    except ValueError as exc:
        raise HarnessCliError("Worker URL must use a valid loopback address") from exc
    if (
        parsed.scheme != "http"
        or not address.is_loopback
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/api/coding-worker/v1"
    ):
        raise HarnessCliError(
            "Worker URL must be an exact loopback Harness API base"
        )
    return value.rstrip("/")


def _worker_attestation_sha256(
    *,
    worker_url: str,
    controller_token: str,
    model: str,
    model_route: str,
) -> str:
    loopback_url = _loopback_worker_url(worker_url)
    parsed = urllib.parse.urlsplit(loopback_url)
    connection = http.client.HTTPConnection(
        parsed.hostname,
        parsed.port,
        timeout=30,
    )
    try:
        connection.request(
            "GET",
            f"{parsed.path}/harness/attestation",
            headers={"Authorization": f"Bearer {controller_token}"},
        )
        response = connection.getresponse()
        if response.status != 200:
            raise HarnessCliError("Worker Harness attestation was rejected")
        payload = json.loads(response.read())
    except (
        OSError,
        http.client.HTTPException,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as exc:
        raise HarnessCliError("Worker Harness attestation is unavailable") from exc
    finally:
        connection.close()
    if not isinstance(payload, Mapping):
        raise HarnessCliError("Worker Harness attestation is invalid")
    if payload.get("protocol") != "modelmirror-coding-harness-attestation/v1":
        raise HarnessCliError("Worker Harness attestation protocol is invalid")
    package_root = REPOSITORY_ROOT / "server" / "coding_worker"
    expected_server_digest = harness_code_bundle_sha256(
        package_root,
        SERVER_HARNESS_CODE_FILES,
    )
    expected_provider_digest = harness_code_bundle_sha256(
        package_root,
        PROVIDER_HARNESS_CODE_FILES,
    )
    if payload.get("server_code_bundle_sha256") != expected_server_digest:
        raise HarnessCliError("Worker Server code does not match the candidate")
    if re.fullmatch(r"[a-f0-9]{32}", str(payload.get("server_generation") or "")) is None:
        raise HarnessCliError("Worker Server generation is invalid")
    generation = payload.get("controller_generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise HarnessCliError("Worker controller generation is invalid")
    providers = payload.get("providers")
    if not isinstance(providers, Mapping) or len(providers) != 2:
        raise HarnessCliError("Worker Provider attestation is incomplete")
    expected_model_digest = _sha256(model.encode("utf-8"))
    for slot_id, attestation in providers.items():
        if not isinstance(slot_id, str) or not isinstance(attestation, Mapping):
            raise HarnessCliError("Worker Provider attestation is invalid")
        if attestation.get("route_id") != model_route:
            raise HarnessCliError("Worker Provider route does not match the round")
        if attestation.get("model_identity_sha256") != expected_model_digest:
            raise HarnessCliError("Worker Provider model does not match the round")
        if attestation.get("engine") != f"opencode-{NATIVE_OPENCODE_VERSION}":
            raise HarnessCliError("Worker Provider engine does not match the round")
        if (
            re.fullmatch(
                r"[a-f0-9]{32}",
                str(attestation.get("sidecar_generation") or ""),
            )
            is None
        ):
            raise HarnessCliError("Worker Provider generation is invalid")
        if attestation.get("code_bundle_sha256") != expected_provider_digest:
            raise HarnessCliError("Worker Provider code does not match the candidate")
    return _canonical_sha256(payload)


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise HarnessCliError(f"invalid task manifest: {path}") from exc


def _validate_scenario(path: Path) -> None:
    payload = _read_json_object(path)
    if set(payload) != {
        "allowed_approvals",
        "questions",
        "actions",
        "required_events",
    }:
        raise HarnessCliError(f"task scenario contract is invalid: {path}")
    approvals = payload["allowed_approvals"]
    questions = payload["questions"]
    actions = payload["actions"]
    required_events = payload["required_events"]
    if not all(
        isinstance(value, list)
        for value in (approvals, questions, actions, required_events)
    ):
        raise HarnessCliError(f"task scenario collections are invalid: {path}")
    allowed_required_events = {
        "question_requested",
        "question_resolved",
        "context_compacted",
        "operation_reconciled",
    }
    if (
        any(not isinstance(value, str) for value in required_events)
        or len(required_events) != len(set(required_events))
        or not set(required_events).issubset(allowed_required_events)
    ):
        raise HarnessCliError(f"task scenario required events are invalid: {path}")

    def valid_shell_scope(value: object) -> bool:
        return (
            isinstance(value, dict)
            and set(value) == {"script", "cwd", "mode", "timeout_seconds"}
            and isinstance(value.get("script"), str)
            and 0 < len(value["script"]) <= 65_536
            and value.get("cwd") == "."
            and value.get("mode") in {"inspect", "mutate"}
            and isinstance(value.get("timeout_seconds"), int)
            and not isinstance(value.get("timeout_seconds"), bool)
            and 1 <= value["timeout_seconds"] <= 1_800
        )

    for approval in approvals:
        command_scope = (
            isinstance(approval, dict)
            and set(approval) == {"argv", "timeout_seconds"}
            and isinstance(approval.get("argv"), list)
            and bool(approval["argv"])
            and all(isinstance(item, str) and item for item in approval["argv"])
            and isinstance(approval.get("timeout_seconds"), int)
            and not isinstance(approval.get("timeout_seconds"), bool)
            and 1 <= approval["timeout_seconds"] <= 1_800
        )
        if not command_scope and not valid_shell_scope(approval):
            raise HarnessCliError(f"task scenario approval is invalid: {path}")
    for question in questions:
        if (
            not isinstance(question, dict)
            or set(question) != {"prompt_sha256", "answer", "selected_option_id"}
            or re.fullmatch(r"[a-f0-9]{64}", str(question.get("prompt_sha256", "")))
            is None
            or not isinstance(question.get("answer"), str)
            or not isinstance(question.get("selected_option_id"), str)
        ):
            raise HarnessCliError(f"task scenario question is invalid: {path}")
    seen_actions: set[str] = set()
    for action in actions:
        if not isinstance(action, dict):
            raise HarnessCliError(f"task scenario action is invalid: {path}")
        action_id = action.get("action_id")
        kind = action.get("kind")
        if (
            not isinstance(action_id, str)
            or not action_id
            or action_id in seen_actions
            or action.get("when_state")
            not in {"running", "waiting_approval", "interrupted"}
        ):
            raise HarnessCliError(f"task scenario action binding is invalid: {path}")
        seen_actions.add(action_id)
        required = {"action_id", "when_state", "kind"}
        if kind == "message":
            valid = set(action) == required | {"message"} and isinstance(
                action.get("message"), str
            )
        elif kind in {"pause_resume", "resume"}:
            valid = set(action) == required
        elif kind == "component_fault":
            valid = (
                set(action) == required | {"component", "point", "approval"}
                and action.get("when_state") == "waiting_approval"
                and action.get("component") == "executor"
                and action.get("point") == "after_side_effect_before_receipt"
                and valid_shell_scope(action.get("approval"))
                and action["approval"].get("mode") == "mutate"
                and action["approval"] in approvals
            )
        else:
            valid = False
        if not valid:
            raise HarnessCliError(f"task scenario action is invalid: {path}")


def _scenario_payload(task_root: Path) -> dict[str, Any] | None:
    path = task_root / "scenario.json"
    if not path.exists():
        return None
    _validate_scenario(path)
    return _read_json_object(path)


def _native_shell_policy(
    task_root: Path, fixture: HarnessFixture
) -> dict[str, bool]:
    commands = {shlex.join(check.argv): False for check in fixture.visible_checks}
    scenario = _scenario_payload(task_root)
    if scenario is not None:
        for approval in scenario["allowed_approvals"]:
            if "argv" in approval:
                command = shlex.join(tuple(approval["argv"]))
                side_effecting = False
            else:
                command = str(approval["script"])
                side_effecting = approval["mode"] == "mutate"
            commands[command] = commands.get(command, False) or side_effecting
    if not commands or any(not command.strip() for command in commands):
        raise HarnessCliError(f"task shell policy is empty: {fixture.task_id}")
    return dict(sorted(commands.items()))


def _native_allowed_shell_commands(
    task_root: Path, fixture: HarnessFixture
) -> tuple[str, ...]:
    return tuple(_native_shell_policy(task_root, fixture))


def _native_opencode_config(
    allowed_commands: tuple[str, ...], *, allow_question: bool = False
) -> dict[str, Any]:
    bash_rules: dict[str, str] = {"*": "deny"}
    bash_rules.update({command: "allow" for command in allowed_commands})
    return {
        "share": "disabled",
        "shell": "/tmp/modelmirror-native-opencode/tool-shell",
        "permission": {
            "*": "deny",
            "read": "allow",
            "edit": {
                "*": "allow",
                "**/opencode.json": "deny",
                "**/opencode.jsonc": "deny",
                "**/.opencode/**": "deny",
            },
            "glob": "allow",
            "grep": "allow",
            "list": "allow",
            "lsp": "deny",
            "todowrite": "allow",
            "todoread": "allow",
            "bash": bash_rules,
            "external_directory": "deny",
            "task": "deny",
            "skill": "deny",
            "webfetch": "deny",
            "websearch": "deny",
            "question": "allow" if allow_question else "deny",
        },
    }


def _assert_native_interaction_parity_available(
    bundle: HarnessFixtureBundle, *, root: Path | None = None
) -> None:
    agent = REPOSITORY_ROOT / "scripts" / "coding_worker_native_agent.py"
    helper = REPOSITORY_ROOT / "scripts" / "coding_worker_native_control.mjs"
    shell_wrapper = (
        REPOSITORY_ROOT / "scripts" / "coding_worker_native_shell_wrapper.sh"
    )
    if not agent.is_file() or not helper.is_file() or not shell_wrapper.is_file():
        raise HarnessCliError("native OpenCode interaction controller is unavailable")
    supported_actions = {"message", "component_fault", "resume"}
    supported_events = {
        "question_requested",
        "question_resolved",
        "context_compacted",
        "operation_reconciled",
    }
    benchmark_root = (root or DEFAULT_ROOT).resolve()
    for fixture in bundle.fixtures:
        if fixture.scenario_sha256 is None:
            continue
        scenario = _scenario_payload(benchmark_root / "tasks" / fixture.task_id)
        if scenario is None:
            raise HarnessCliError(f"native scenario is unavailable: {fixture.task_id}")
        kinds = {str(item.get("kind")) for item in scenario.get("actions", [])}
        required = {str(item) for item in scenario.get("required_events", [])}
        if not kinds.issubset(supported_actions) or not required.issubset(supported_events):
            raise HarnessCliError(
                f"native OpenCode scenario is unsupported: {fixture.task_id}"
            )


def _runtime_runner_image_sha256(
    *,
    docker_executable: str = "/usr/local/bin/docker",
    docker_marker: Path = Path("/.dockerenv"),
    hostname: str | None = None,
    platform_name: str = os.name,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """Attest the immutable image ID of the running Linux controller.

    The value is derived from the controller container identity and Docker
    daemon, not from a CLI argument or environment variable.
    """

    if platform_name == "nt" or not docker_marker.is_file():
        raise HarnessCliError("run-round requires the frozen Linux controller container")
    observed_hostname = hostname or socket.gethostname()
    if re.fullmatch(r"[a-f0-9]{12,64}", observed_hostname) is None:
        raise HarnessCliError("controller container identity is unavailable")
    executable = Path(docker_executable)
    if not executable.is_absolute() or not executable.is_file():
        raise HarnessCliError("controller Docker client is unavailable")
    inspect = run_command(
        [
            str(executable),
            "inspect",
            "--type",
            "container",
            "--format",
            "{{.Id}} {{.Image}}",
            observed_hostname,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    fields = inspect.stdout.strip().split() if inspect.returncode == 0 else []
    if (
        len(fields) != 2
        or re.fullmatch(r"[a-f0-9]{64}", fields[0]) is None
        or not fields[0].startswith(observed_hostname)
        or re.fullmatch(r"sha256:[a-f0-9]{64}", fields[1]) is None
    ):
        raise HarnessCliError("controller container image attestation failed")
    image_id = fields[1]
    if image_id == f"sha256:{'0' * 64}":
        raise HarnessCliError("controller image identity is a placeholder")
    image = run_command(
        [
            str(executable),
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            image_id,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if image.returncode != 0 or image.stdout.strip() != image_id:
        raise HarnessCliError("controller image identity could not be reconciled")
    return image_id


def _is_reparse_point(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _validate_regular_tree(root: Path) -> None:
    if not root.is_dir() or root.is_symlink() or _is_reparse_point(root):
        raise HarnessCliError(f"task package tree is unsafe: {root}")
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        if path.is_symlink() or _is_reparse_point(path):
            raise HarnessCliError(f"task package contains a link: {path}")
        if path.is_dir():
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise HarnessCliError(f"task package contains an unsafe file: {path}")


def _canonical_tree_files(root: Path) -> tuple[Path, ...]:
    """Return files in an OS-independent order.

    WindowsPath comparisons are case-insensitive while PosixPath comparisons are
    case-sensitive.  A fixture digest must not change when the same package is
    validated inside the Linux Harbor container.
    """

    files = (item for item in root.rglob("*") if item.is_file())
    return tuple(
        sorted(
            files,
            key=lambda path: (
                path.relative_to(root).as_posix().casefold(),
                path.relative_to(root).as_posix(),
            ),
        )
    )


def _declared_executable(path: Path) -> bool:
    """Derive the portable Task 1.4 executable bit from the package contract.

    NTFS bind mounts commonly expose every file as mode 0777 in Linux.  The V18
    packages declare shell entrypoints by their ``.sh`` suffix; source and data
    files are deliberately non-executable.
    """

    return path.suffix.casefold() == ".sh"


def _tree_digest(root: Path, *, exclude: frozenset[str] = frozenset()) -> str:
    entries: list[dict[str, object]] = []
    for path in _canonical_tree_files(root):
        relative = path.relative_to(root).as_posix()
        if relative in exclude:
            continue
        content = path.read_bytes()
        entries.append(
            {
                "path": relative,
                "sha256": _sha256(content),
                "size": len(content),
                "executable": _declared_executable(path),
            }
        )
    return _canonical_sha256(entries)


def _validate_docker_boundaries(task_root: Path) -> None:
    environment = (task_root / "environment" / "Dockerfile").read_text(
        encoding="utf-8"
    ).lower()
    verifier = (task_root / "tests" / "Dockerfile").read_text(
        encoding="utf-8"
    ).lower()
    if "solution" in environment or "tests" in environment:
        raise HarnessCliError(
            f"task {task_root.name} leaks evaluator files into the agent image"
        )
    if "solution" in verifier or "environment/project" in verifier:
        raise HarnessCliError(
            f"task {task_root.name} leaks solution or source into the verifier image"
        )
    npm_install = environment.find("run npm ci")
    workspace = environment.find("workdir /workspace")
    if npm_install >= 0 and 0 <= workspace < npm_install:
        raise HarnessCliError(
            f"task {task_root.name} installs dependencies into the exported workspace"
        )


def _fixture_files(project_root: Path, binary_canary: str) -> tuple[HarnessFixtureFile, ...]:
    if not project_root.is_dir():
        raise HarnessCliError(f"task project directory is missing: {project_root}")
    files: list[HarnessFixtureFile] = []
    for path in _canonical_tree_files(project_root):
        relative = path.relative_to(project_root).as_posix()
        content = path.read_bytes()
        files.append(
            HarnessFixtureFile(
                path=relative,
                content_base64=base64.b64encode(content).decode("ascii"),
                sha256=_sha256(content),
                executable=_declared_executable(path),
                binary_canary=relative == binary_canary,
            )
        )
    return tuple(files)


def _validate_native_project_config(files: tuple[HarnessFixtureFile, ...]) -> None:
    for entry in files:
        path = entry.path.casefold()
        if path in {"opencode.json", "opencode.jsonc"} or path.startswith(
            ".opencode/"
        ):
            raise HarnessCliError(
                "task project may not override the native OpenCode policy"
            )


def _tree_hash(files: tuple[HarnessFixtureFile, ...]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(files, key=lambda item: item.path):
        path = entry.path.encode("utf-8")
        content = entry.content()
        digest.update(len(path).to_bytes(4, "big"))
        digest.update(path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def _task_fixture(task_root: Path) -> tuple[HarnessFixture, dict[str, Any]]:
    _validate_regular_tree(task_root)
    required_paths = (
        task_root / "task.toml",
        task_root / "instruction.md",
        task_root / "environment" / "Dockerfile",
        task_root / "environment" / "project",
        task_root / "solution" / "solve.sh",
        task_root / "tests" / "Dockerfile",
        task_root / "tests" / "test.sh",
        task_root / "near_miss.patch",
    )
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        raise HarnessCliError(f"task {task_root.name} is incomplete: {missing}")
    manifest_path = task_root / "task.toml"
    manifest_bytes = manifest_path.read_bytes()
    manifest = _load_toml(manifest_path)
    if manifest.get("schema_version") != TASK_SCHEMA_VERSION:
        raise HarnessCliError(f"task {task_root.name} is not Harbor schema 1.4")
    if manifest.get("artifacts") != ["/workspace"]:
        raise HarnessCliError(
            f"task {task_root.name} does not export the final workspace"
        )
    task_section = manifest.get("task")
    metadata = manifest.get("metadata")
    modelmirror = metadata.get("modelmirror") if isinstance(metadata, dict) else None
    if not isinstance(task_section, dict) or not isinstance(modelmirror, dict):
        raise HarnessCliError(f"task {task_root.name} omitted ModelMirror metadata")
    if task_section.get("name") != f"modelmirror-coding-v18/{task_root.name}":
        raise HarnessCliError(f"task {task_root.name} package identity is invalid")
    if task_section.get("version") != "1.0.0":
        raise HarnessCliError(f"task {task_root.name} package version is not fixed")
    environment = manifest.get("environment")
    verifier = manifest.get("verifier")
    verifier_environment = verifier.get("environment") if isinstance(verifier, dict) else None
    if (
        not isinstance(environment, dict)
        or environment.get("network_mode") != "no-network"
        or not isinstance(verifier, dict)
        or verifier.get("environment_mode") != "separate"
        or not isinstance(verifier_environment, dict)
        or verifier_environment.get("network_mode") != "no-network"
    ):
        raise HarnessCliError(f"task {task_root.name} does not isolate agent and verifier")
    _validate_docker_boundaries(task_root)
    visible = modelmirror.get("visible_checks")
    if not isinstance(visible, list) or not visible:
        raise HarnessCliError(f"task {task_root.name} omitted visible checks")
    checks = tuple(HarnessVisibleCheck.model_validate(item) for item in visible)
    files = _fixture_files(
        task_root / "environment" / "project",
        str(modelmirror.get("binary_canary", "")),
    )
    _validate_native_project_config(files)
    sealed_checker_file = modelmirror.get("sealed_checker_file")
    sealed_checker_sha256 = modelmirror.get("sealed_checker_sha256")
    if (
        sealed_checker_file not in {"test_hidden.py", "hidden.test.tsx"}
        or not isinstance(sealed_checker_sha256, str)
        or re.fullmatch(r"[a-f0-9]{64}", sealed_checker_sha256) is None
        or (task_root / "tests" / str(sealed_checker_file)).exists()
    ):
        raise HarnessCliError(
            f"task {task_root.name} sealed checker binding is invalid"
        )
    policy = _read_json_object(task_root / "tests" / "workspace-policy.json")
    expected_baseline = {item.path: item.sha256 for item in files}
    if policy != {
        "baseline": expected_baseline,
        "binary_canary": modelmirror.get("binary_canary"),
        "required_modified_files": modelmirror.get("required_modified_files", 2),
    }:
        raise HarnessCliError(
            f"task {task_root.name} verifier policy is not bound to H0"
        )
    instruction = (task_root / "instruction.md").read_bytes()
    near_miss = (task_root / "near_miss.patch").read_bytes()
    binary_canaries = [item for item in files if item.binary_canary]
    if len(binary_canaries) != 1 or b"\x00" not in binary_canaries[0].content():
        raise HarnessCliError(
            f"task {task_root.name} must contain one real binary canary"
        )
    environment_spec_sha256 = _tree_digest(
        task_root / "environment", exclude=frozenset({"source.json"})
    )
    solution_bundle_sha256 = _tree_digest(task_root / "solution")
    public_verifier_sha256 = _tree_digest(task_root / "tests")
    verifier_bundle_sha256 = _canonical_sha256(
        {
            "public_verifier_sha256": public_verifier_sha256,
            "sealed_checker_file": sealed_checker_file,
            "sealed_checker_sha256": sealed_checker_sha256,
        }
    )
    task_package_sha256 = _canonical_sha256(
        {
            "manifest": _sha256(manifest_bytes),
            "instruction": _sha256(instruction),
            "environment": environment_spec_sha256,
            "solution": solution_bundle_sha256,
            "public_verifier": public_verifier_sha256,
            "sealed_checker_file": sealed_checker_file,
            "sealed_checker_sha256": sealed_checker_sha256,
            "near_miss": _sha256(near_miss),
            "scenario": (
                _sha256((task_root / "scenario.json").read_bytes())
                if (task_root / "scenario.json").exists()
                else None
            ),
        }
    )
    scenario_path = task_root / "scenario.json"
    if scenario_path.exists():
        _validate_scenario(scenario_path)
    scenario_sha256 = _sha256(scenario_path.read_bytes()) if scenario_path.exists() else None
    raw: dict[str, Any] = {
        "task_id": task_root.name,
        "category": modelmirror.get("category"),
        "source_id": f"v18-{task_root.name}",
        "initial_tree_hash": _tree_hash(files),
        "task_manifest_sha256": _sha256(manifest_bytes),
        "instruction_sha256": _sha256(instruction),
        "environment_spec_sha256": environment_spec_sha256,
        "solution_bundle_sha256": solution_bundle_sha256,
        "verifier_bundle_sha256": verifier_bundle_sha256,
        "task_package_sha256": task_package_sha256,
        "scenario_sha256": scenario_sha256,
        "near_miss_sha256": _sha256(near_miss),
        "files": [item.model_dump(mode="json") for item in files],
        "visible_checks": [item.model_dump(mode="json") for item in checks],
        "required_modified_files": modelmirror.get("required_modified_files", 2),
        "long_context": modelmirror.get("long_context", False),
    }
    raw["revision"] = _canonical_sha256(
        {
            "task_id": raw["task_id"],
            "source_id": raw["source_id"],
            "task_manifest_sha256": raw["task_manifest_sha256"],
            "instruction_sha256": raw["instruction_sha256"],
            "environment_spec_sha256": raw["environment_spec_sha256"],
            "solution_bundle_sha256": raw["solution_bundle_sha256"],
            "verifier_bundle_sha256": raw["verifier_bundle_sha256"],
            "task_package_sha256": raw["task_package_sha256"],
            "scenario_sha256": raw["scenario_sha256"],
            "near_miss_sha256": raw["near_miss_sha256"],
            "files": raw["files"],
            "visible_checks": raw["visible_checks"],
        }
    )
    fixture = HarnessFixture.model_validate(raw)
    acceptance = _fixture_acceptance(fixture)
    source_binding = {
        "task_id": fixture.task_id,
        "source_id": fixture.source_id,
        "revision": fixture.revision,
        "instruction_sha256": fixture.instruction_sha256,
        "scenario_sha256": fixture.scenario_sha256,
        "acceptance_sha256": _canonical_sha256(acceptance),
        "acceptance": acceptance,
    }
    return fixture, source_binding


def _fixture_acceptance(fixture: HarnessFixture) -> dict[str, Any]:
    return {
        "contract_id": f"v18-{fixture.task_id}",
        "required_checks": [
            {
                "check_id": check.check_id,
                "label": check.check_id,
                "kind": "command",
                "required": True,
            }
            for check in fixture.visible_checks
        ],
        "required_artifacts": [],
    }


def _write_workspace_policy(task_root: Path) -> None:
    manifest = _load_toml(task_root / "task.toml")
    metadata = manifest.get("metadata")
    modelmirror = metadata.get("modelmirror") if isinstance(metadata, dict) else None
    if not isinstance(modelmirror, dict):
        raise HarnessCliError(f"task {task_root.name} omitted ModelMirror metadata")
    files = _fixture_files(
        task_root / "environment" / "project",
        str(modelmirror.get("binary_canary", "")),
    )
    policy = {
        "baseline": {item.path: item.sha256 for item in files},
        "binary_canary": modelmirror.get("binary_canary"),
        "required_modified_files": modelmirror.get("required_modified_files", 2),
    }
    (task_root / "tests" / "workspace-policy.json").write_text(
        json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def compile_bundle(root: Path, *, write: bool) -> HarnessFixtureBundle:
    tasks_root = root / "tasks"
    task_roots = sorted(path for path in tasks_root.iterdir() if path.is_dir())
    if len(task_roots) != 12:
        raise HarnessCliError("V18 calibration set must contain exactly 12 tasks")
    fixtures: list[HarnessFixture] = []
    bindings: dict[Path, dict[str, Any]] = {}
    for task_root in task_roots:
        if write:
            _write_workspace_policy(task_root)
        fixture, binding = _task_fixture(task_root)
        fixtures.append(fixture)
        bindings[task_root / "environment" / "source.json"] = binding
    bundle = HarnessFixtureBundle(fixtures=tuple(fixtures))
    bundle_path = root / "fixture-bundle.json"
    encoded = bundle.model_dump_json(indent=2) + "\n"
    if write:
        for path, binding in bindings.items():
            path.write_text(
                json.dumps(binding, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        bundle_path.write_text(encoded, encoding="utf-8")
    else:
        if not bundle_path.exists() or bundle_path.read_text(encoding="utf-8") != encoded:
            raise HarnessCliError("fixture-bundle.json is stale; run compile --write")
        for path, binding in bindings.items():
            expected = json.dumps(binding, ensure_ascii=False, indent=2) + "\n"
            if not path.exists() or path.read_text(encoding="utf-8") != expected:
                raise HarnessCliError(f"source binding is stale: {path}")
    return bundle


def _sealed_checker_binding(task_root: Path) -> tuple[str, str]:
    manifest = _load_toml(task_root / "task.toml")
    metadata = manifest.get("metadata")
    modelmirror = metadata.get("modelmirror") if isinstance(metadata, dict) else None
    if not isinstance(modelmirror, dict):
        raise HarnessCliError(f"task {task_root.name} omitted ModelMirror metadata")
    filename = modelmirror.get("sealed_checker_file")
    sha256 = modelmirror.get("sealed_checker_sha256")
    if (
        filename not in {"test_hidden.py", "hidden.test.tsx"}
        or not isinstance(sha256, str)
        or re.fullmatch(r"[a-f0-9]{64}", sha256) is None
    ):
        raise HarnessCliError(
            f"task {task_root.name} sealed checker binding is invalid"
        )
    return str(filename), sha256


def _read_bound_checker(path: Path, expected_sha256: str) -> bytes:
    try:
        before = path.lstat()
        if (
            path.is_symlink()
            or _is_reparse_point(path)
            or not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
        ):
            raise HarnessCliError("sealed checker file is unsafe")
        content = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise HarnessCliError("sealed checker file is unavailable") from exc
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        getattr(before, "st_file_attributes", 0),
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        getattr(after, "st_file_attributes", 0),
    )
    if before_identity != after_identity or len(content) != before.st_size:
        raise HarnessCliError("sealed checker changed while reading")
    if _sha256(content) != expected_sha256:
        raise HarnessCliError("sealed checker hash changed")
    return content


def _validate_sealed_checkers(
    root: Path, bundle: HarnessFixtureBundle, *, public_root: Path
) -> str:
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise HarnessCliError("sealed checker root is unavailable") from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or root.is_symlink() or _is_reparse_point(root):
        raise HarnessCliError("sealed checker root is unsafe")
    _validate_regular_tree(root)
    expected_tasks = {fixture.task_id for fixture in bundle.fixtures}
    observed_tasks = {path.name for path in root.iterdir() if path.is_dir()}
    if observed_tasks != expected_tasks:
        raise HarnessCliError("sealed checker task set does not match the fixture bundle")
    entries: list[dict[str, str]] = []
    fixtures = {fixture.task_id: fixture for fixture in bundle.fixtures}
    for task_id in sorted(expected_tasks):
        task_root = public_root / "tasks" / task_id
        if not task_root.is_dir():
            raise HarnessCliError(f"public task is unavailable: {task_id}")
        filename, expected_sha256 = _sealed_checker_binding(task_root)
        task_directory = root / task_id
        files = tuple(path for path in task_directory.iterdir() if path.is_file())
        checker = task_directory / filename
        if len(files) != 1 or not checker.is_file():
            raise HarnessCliError(f"sealed checker layout is invalid: {task_id}")
        try:
            content = _read_bound_checker(checker, expected_sha256)
        except HarnessCliError as exc:
            raise HarnessCliError(f"{exc}: {task_id}") from exc
        observed_sha256 = _sha256(content)
        fixture = fixtures[task_id]
        public_verifier_sha256 = _tree_digest(task_root / "tests")
        expected_bundle_sha256 = _canonical_sha256(
            {
                "public_verifier_sha256": public_verifier_sha256,
                "sealed_checker_file": filename,
                "sealed_checker_sha256": observed_sha256,
            }
        )
        if fixture.verifier_bundle_sha256 != expected_bundle_sha256:
            raise HarnessCliError(f"sealed verifier bundle changed: {task_id}")
        entries.append(
            {
                "task_id": task_id,
                "filename": filename,
                "sha256": observed_sha256,
            }
        )
    return _canonical_sha256(
        {
            "fixture_bundle_sha256": bundle.canonical_sha256(),
            "checkers": entries,
        }
    )


@contextlib.contextmanager
def _materialized_tasks_root(
    public_root: Path,
    sealed_root: Path,
    bundle: HarnessFixtureBundle,
    *,
    harbor_executable: str,
    checksum_task: Callable[[str, Path], tuple[str, str]] | None = None,
) -> Any:
    checksum = checksum_task or _harbor_task_checksums
    sealed_bundle_sha256 = _validate_sealed_checkers(
        sealed_root, bundle, public_root=public_root
    )
    with tempfile.TemporaryDirectory(prefix="modelmirror-sealed-tasks-") as directory:
        tasks_root = Path(directory) / "tasks"
        shutil.copytree(public_root / "tasks", tasks_root, symlinks=True)
        _validate_regular_tree(tasks_root)
        _validate_materialized_public_tasks(tasks_root, bundle)
        for fixture in bundle.fixtures:
            filename, expected_sha256 = _sealed_checker_binding(
                public_root / "tasks" / fixture.task_id
            )
            content = _read_bound_checker(
                sealed_root / fixture.task_id / filename, expected_sha256
            )
            destination = tasks_root / fixture.task_id / "tests" / filename
            destination.write_bytes(content)
            if _sha256(destination.read_bytes()) != expected_sha256:
                raise HarnessCliError(
                    f"materialized sealed checker changed: {fixture.task_id}"
                )
        _validate_regular_tree(tasks_root)
        task_checksums = {
            fixture.task_id: dict(
                zip(
                    ("content", "result"),
                    checksum(harbor_executable, tasks_root / fixture.task_id),
                    strict=True,
                )
            )
            for fixture in bundle.fixtures
        }
        yield tasks_root, sealed_bundle_sha256, task_checksums


def _validate_materialized_public_tasks(
    tasks_root: Path, bundle: HarnessFixtureBundle
) -> None:
    expected_tasks = {fixture.task_id for fixture in bundle.fixtures}
    observed_tasks = {path.name for path in tasks_root.iterdir() if path.is_dir()}
    if observed_tasks != expected_tasks:
        raise HarnessCliError("materialized task set changed")
    for fixture in bundle.fixtures:
        copied_fixture, _binding = _task_fixture(tasks_root / fixture.task_id)
        if copied_fixture != fixture:
            raise HarnessCliError(
                f"materialized public task changed: {fixture.task_id}"
            )


def _harbor_task_checksums(
    harbor_executable: str, task_root: Path
) -> tuple[str, str]:
    """Return Harbor's publishable-content and result.json task digests."""

    located = shutil.which(harbor_executable)
    executable = Path(located or harbor_executable).resolve()
    python_name = "python.exe" if executable.suffix.lower() == ".exe" else "python"
    harbor_python = executable.with_name(python_name)
    if not harbor_python.is_file():
        harbor_python = Path(sys.executable)
    script = (
        "from pathlib import Path\n"
        "import json\n"
        "from dirhash import dirhash\n"
        "from harbor.publisher.packager import Packager\n"
        "task = Path(__import__('sys').argv[1])\n"
        "content, _files = Packager.compute_content_hash(task)\n"
        "print(json.dumps({'content': content, 'result': dirhash(task, 'sha256')}))\n"
    )
    completed = subprocess.run(
        [str(harbor_python), "-c", script, str(task_root)],
        env=_harbor_subprocess_environment(),
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise HarnessCliError("Harbor task checksum is unavailable") from exc
    if (
        completed.returncode != 0
        or not isinstance(payload, dict)
        or set(payload) != {"content", "result"}
        or any(
            not isinstance(payload[key], str)
            or re.fullmatch(r"[a-f0-9]{64}", payload[key]) is None
            for key in ("content", "result")
        )
    ):
        raise HarnessCliError("Harbor task checksum is unavailable")
    return payload["content"], payload["result"]


def _harbor_version(executable: str) -> None:
    result = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    observed = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0 or HARBOR_VERSION not in observed:
        raise HarnessCliError(f"Harbor {HARBOR_VERSION} is required")


def _harbor_trial_reward(jobs_root: Path) -> float:
    trial_results: list[dict[str, Any]] = []
    for path in sorted(jobs_root.rglob("result.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HarnessCliError(f"invalid Harbor result artifact: {path}") from exc
        if isinstance(payload, dict) and "verifier_result" in payload:
            trial_results.append(payload)
    if len(trial_results) != 1:
        raise HarnessCliError(
            "a task gate must produce exactly one Harbor trial result"
        )
    trial = trial_results[0]
    if trial.get("exception_info") is not None:
        raise HarnessCliError("Harbor trial ended with an exception")
    verifier = trial.get("verifier_result")
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    if isinstance(reward, bool) or not isinstance(reward, (int, float)):
        raise HarnessCliError("Harbor trial omitted the numeric reward")
    numeric = float(reward)
    if not 0.0 <= numeric <= 1.0:
        raise HarnessCliError("Harbor trial reward is outside [0, 1]")
    return numeric


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HarnessCliError(f"invalid Harness artifact: {path}") from exc
    if not isinstance(payload, dict):
        raise HarnessCliError(f"Harness artifact is not an object: {path}")
    return payload


def _single_trial_dir(job_root: Path) -> Path:
    candidates = sorted(
        path.parent
        for path in job_root.glob("*/result.json")
        if path.parent != job_root
    )
    if len(candidates) != 1:
        raise HarnessCliError("a Harness run must contain exactly one Harbor trial")
    return candidates[0]


def _regular_tree_summary(root: Path) -> tuple[str, int]:
    _validate_regular_tree(root)
    entries: list[dict[str, object]] = []
    total_size = 0
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        content = path.read_bytes()
        total_size += len(content)
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256(content),
                "size": len(content),
            }
        )
    return _canonical_sha256(entries), total_size


def _workspace_tree_hash(root: Path) -> str:
    """Recompute the Worker tree hash from the workspace delivered to Harbor."""

    _validate_regular_tree(root)
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def _artifact_summary(
    artifact_id: str, path: Path
) -> HarnessArtifactSummary:
    if path.is_dir():
        digest, size = _regular_tree_summary(path)
    else:
        metadata = path.lstat()
        if (
            path.is_symlink()
            or _is_reparse_point(path)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
        ):
            raise HarnessCliError(f"Harness artifact is unsafe: {path}")
        content = path.read_bytes()
        digest, size = _sha256(content), len(content)
    return HarnessArtifactSummary(
        artifact_id=artifact_id,
        sha256=digest,
        size=size,
    )


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _failure_from_exception(exception: object) -> HarnessFailureStage:
    rendered = json.dumps(exception, ensure_ascii=False, sort_keys=True).lower()
    if any(marker in rendered for marker in ("api", "connection", "network", "auth")):
        return HarnessFailureStage.PROVIDER_TRANSPORT
    if any(marker in rendered for marker in ("provider", "trajectory", "protocol")):
        return HarnessFailureStage.PROVIDER_PROTOCOL
    if "budget" in rendered or "timeout" in rendered:
        return HarnessFailureStage.BUDGET
    if "policy" in rendered or "forbidden" in rendered:
        return HarnessFailureStage.POLICY
    return HarnessFailureStage.HARNESS


def _native_facts(
    *,
    run_id: str,
    trajectory: dict[str, Any],
    ledger: dict[str, Any],
    workspace_sha256: str,
    command_policy: Mapping[str, bool],
) -> HarnessFactSet:
    if trajectory.get("schema_version") != "ATIF-v1.7":
        raise HarnessCliError("native trajectory is not ATIF-v1.7")
    if ledger.get("schema") != "modelmirror-native-opencode-control/v1":
        raise HarnessCliError("native interaction ledger protocol is invalid")
    raw_events = ledger.get("events")
    control = ledger.get("control")
    scenario_contract = ledger.get("scenario_contract")
    public_messages = ledger.get("public_messages")
    if (
        not isinstance(raw_events, list)
        or any(not isinstance(item, dict) for item in raw_events)
        or not isinstance(control, list)
        or any(not isinstance(item, dict) for item in control)
        or not isinstance(scenario_contract, dict)
        or not isinstance(scenario_contract.get("required_events"), list)
        or not isinstance(public_messages, list)
        or any(not isinstance(item, dict) for item in public_messages)
        or ledger.get("public_messages_sha256") != _canonical_sha256(public_messages)
    ):
        raise HarnessCliError("native interaction ledger is incomplete")
    operations: list[HarnessOperationFact] = []
    operation_indexes: dict[str, int] = {}
    operation_intents: dict[str, str] = {}
    operation_commands: dict[str, str] = {}
    duplicate_call_ids: set[str] = set()
    sequence = 0
    for step in trajectory.get("steps", []):
        if not isinstance(step, dict):
            raise HarnessCliError("native ATIF trajectory contains an invalid step")
        observed = {
            str(item.get("source_call_id"))
            for item in (step.get("observation") or {}).get("results", [])
            if isinstance(item, dict) and item.get("source_call_id") is not None
        }
        for call in step.get("tool_calls", []) or []:
            if not isinstance(call, dict):
                raise HarnessCliError("native ATIF trajectory contains an invalid tool call")
            sequence += 1
            raw_call_id = str(call.get("tool_call_id") or f"call-{sequence}")
            function_name = str(call.get("function_name") or "unknown")
            arguments = call.get("arguments")
            command = arguments.get("command") if isinstance(arguments, dict) else None
            side_effecting = (
                command_policy.get(command, True)
                if function_name.lower() == "bash" and isinstance(command, str)
                else function_name.lower()
                not in {
                    "read",
                    "read_file",
                    "glob",
                    "grep",
                    "search",
                    "list",
                    "lsp",
                }
            )
            intent_sha256 = _canonical_sha256(
                {"function_name": function_name, "arguments": arguments}
            )
            operation_id = (
                f"native_{_sha256(f'{run_id}:{raw_call_id}:{sequence}'.encode())[:24]}"
            )
            if raw_call_id in operation_indexes:
                duplicate_call_ids.add(raw_call_id)
            else:
                operation_indexes[raw_call_id] = len(operations)
                operation_intents[raw_call_id] = intent_sha256
                if isinstance(command, str):
                    operation_commands[raw_call_id] = command
            operations.append(
                HarnessOperationFact(
                    evidence_id=f"evidence_{operation_id}",
                    operation_id=operation_id,
                    intent_sha256=intent_sha256,
                    state="completed" if raw_call_id in observed else "unknown",
                    side_effecting=side_effecting,
                )
            )
    asked: dict[str, dict[str, Any]] = {}
    replied: set[str] = set()
    for event in raw_events:
        properties = event.get("properties")
        if not isinstance(properties, dict):
            continue
        if event.get("type") == "question.asked" and isinstance(properties.get("id"), str):
            asked[str(properties["id"])] = properties
        elif event.get("type") == "question.replied" and isinstance(
            properties.get("requestID"), str
        ):
            replied.add(str(properties["requestID"]))
    interactions = tuple(
        HarnessInteractionFact(
            evidence_id=f"native_question_{question_id}",
            interaction_id=question_id,
            kind="question",
            state="resolved" if question_id in replied else "pending",
        )
        for question_id in sorted(asked)
    )
    coordination: list[HarnessCoordinationFact] = []
    public_user_message_hashes = {
        _sha256(str(part.get("text")).encode("utf-8"))
        for message in public_messages
        if message.get("role") == "user"
        for part in (message.get("parts") or [])
        if isinstance(part, dict)
        and part.get("type") == "text"
        and isinstance(part.get("text"), str)
    }
    message_controls = [
        item
        for item in control
        if item.get("event_type") in {"initial_prompt", "steering_sent", "resume_sent"}
    ]
    if any(
        not isinstance(item.get("message_sha256"), str)
        or item["message_sha256"] not in public_user_message_hashes
        for item in message_controls
    ):
        coordination.append(
            HarnessCoordinationFact(
                evidence_id=f"native_message_{_sha256(run_id.encode())[:24]}",
                stage=HarnessFailureStage.INTERACTION,
                failed=True,
            )
        )
    control_events = {str(item.get("event_type")) for item in control}
    required_events = {str(item) for item in scenario_contract["required_events"]}
    if not required_events.issubset(control_events):
        coordination.append(
            HarnessCoordinationFact(
                evidence_id=f"native_scenario_{_sha256(run_id.encode())[:24]}",
                stage=HarnessFailureStage.INTERACTION,
                failed=True,
            )
        )
    resolved_control = {
        str(item.get("interaction_id"))
        for item in control
        if item.get("event_type") == "question_resolved"
    }
    if resolved_control != replied or not replied.issubset(asked):
        coordination.append(
            HarnessCoordinationFact(
                evidence_id=f"native_interaction_{_sha256(run_id.encode())[:24]}",
                stage=HarnessFailureStage.INTERACTION,
                failed=True,
            )
        )
    compacted_events = sum(event.get("type") == "session.compacted" for event in raw_events)
    compacted_control = sum(
        item.get("event_type") == "context_compacted" for item in control
    )
    if compacted_control != compacted_events or compacted_control > 1:
        coordination.append(
            HarnessCoordinationFact(
                evidence_id=f"native_compaction_{_sha256(run_id.encode())[:24]}",
                stage=HarnessFailureStage.INTERACTION,
                failed=True,
            )
        )
    reconciled_events = [
        item
        for item in control
        if item.get("event_type") == "operation_reconciled"
    ]
    unknown_events = [
        item
        for item in control
        if item.get("event_type") == "operation_unknown"
    ]
    fault_events = [
        item
        for item in control
        if item.get("event_type") == "component_fault_injected"
    ]
    resume_events = [
        item for item in control if item.get("event_type") == "resume_sent"
    ]
    reconciled = [str(item.get("operation_id")) for item in reconciled_events]
    unknown = [str(item.get("operation_id")) for item in unknown_events]
    unknown_by_id = {str(item.get("operation_id")): item for item in unknown_events}
    reconciled_by_id = {
        str(item.get("operation_id")): item for item in reconciled_events
    }
    reconciliation_valid = (
        not duplicate_call_ids
        and len(unknown) == len(set(unknown))
        and len(reconciled) == len(set(reconciled))
        and set(unknown) == set(reconciled)
        and all(value in operation_indexes for value in reconciled)
        and len(fault_events) == len(reconciled)
        and len(resume_events) == len(reconciled)
        and {str(item.get("operation_id")) for item in fault_events}
        == set(reconciled)
        and {str(item.get("operation_id")) for item in resume_events}
        == set(reconciled)
        and all(item.get("component") == "executor" for item in fault_events)
    )
    if reconciliation_valid:
        for call_id in reconciled:
            expected_intent = operation_intents[call_id]
            command = operation_commands.get(call_id)
            expected_result = (
                _canonical_sha256({"command": command, "exit_code": 0})
                if command is not None
                else None
            )
            if (
                unknown_by_id[call_id].get("intent_sha256") != expected_intent
                or reconciled_by_id[call_id].get("intent_sha256") != expected_intent
                or reconciled_by_id[call_id].get("result_sha256") != expected_result
            ):
                reconciliation_valid = False
                break
    if not reconciliation_valid:
        coordination.append(
            HarnessCoordinationFact(
                evidence_id=f"native_reconcile_{_sha256(run_id.encode())[:24]}",
                stage=HarnessFailureStage.TOOL_VALIDATION,
                failed=True,
            )
        )
    else:
        for call_id in reconciled:
            index = operation_indexes[call_id]
            operations[index] = operations[index].model_copy(update={"state": "completed"})
    return HarnessFactSet(
        export_sha256=workspace_sha256,
        trajectory_sha256=_canonical_sha256(trajectory),
        operations=tuple(operations),
        interactions=interactions,
        coordination=tuple(coordination),
    )


def _worker_facts(
    *,
    trial_dir: Path,
    trajectory: dict[str, Any],
    expected_fixture_task_id: str,
    expected_source_id: str,
    expected_revision: str,
    expected_instruction_sha256: str,
    expected_acceptance_sha256: str,
    expected_scenario_sha256: str | None,
    workspace_path: Path,
) -> tuple[HarnessFactSet, tuple[HarnessArtifactSummary, ...]]:
    facts_path = trial_dir / "agent" / "modelmirror-harness-facts.json"
    ledger_path = trial_dir / "agent" / "modelmirror-harness-ledger.json"
    stored = HarnessFactSet.model_validate(_read_json_object(facts_path))
    ledger = _read_json_object(ledger_path)
    export = ledger.get("export")
    events = ledger.get("events")
    approvals = ledger.get("approvals")
    run_binding = ledger.get("run_binding")
    if (
        not isinstance(export, dict)
        or not isinstance(events, list)
        or not isinstance(approvals, list)
        or not isinstance(run_binding, dict)
    ):
        raise HarnessCliError("Worker Harness ledger is incomplete")
    artifact = run_binding.get("workspace_artifact")
    metadata = artifact.get("metadata") if isinstance(artifact, dict) else None
    if (
        run_binding.get("fixture_task_id") != expected_fixture_task_id
        or run_binding.get("source_id") != expected_source_id
        or run_binding.get("revision") != expected_revision
        or run_binding.get("instruction_sha256") != expected_instruction_sha256
        or run_binding.get("acceptance_sha256") != expected_acceptance_sha256
        or run_binding.get("scenario_sha256") != expected_scenario_sha256
        or not isinstance(metadata, dict)
        or metadata.get("workspace_tree_hash") != _workspace_tree_hash(workspace_path)
    ):
        raise HarnessCliError("Worker Harness task or workspace binding changed")
    from scripts.coding_worker_harbor_agent import ModelMirrorWorkerAgent

    try:
        expected = HarnessFactSet.model_validate(
            ModelMirrorWorkerAgent._facts(
                export=export,
                events=events,
                approvals=tuple(approvals),
                trajectory=trajectory,
                run_binding=run_binding,
            )
        )
    except (RuntimeError, ValueError, TypeError) as exc:
        raise HarnessCliError("Worker Harness fact source is invalid") from exc
    if stored != expected:
        raise HarnessCliError("Worker Harness facts do not match their source ledger")
    return stored, (
        _artifact_summary("worker_facts", facts_path),
        _artifact_summary("worker_ledger", ledger_path),
    )


def _trajectory_tool_calls(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for step in trajectory.get("steps", []) or []:
        if not isinstance(step, dict):
            raise HarnessCliError("ATIF trajectory contains an invalid step")
        for call in step.get("tool_calls", []) or []:
            if not isinstance(call, dict):
                raise HarnessCliError("ATIF trajectory contains an invalid tool call")
            calls.append(call)
    return calls


def _run_guard_facts(
    *,
    run_id: str,
    engine: str,
    trajectory: dict[str, Any],
    native_command_policy: Mapping[str, bool] | None,
    input_tokens: int | None,
    output_tokens: int | None,
    artifacts: list[HarnessArtifactSummary],
) -> tuple[HarnessCoordinationFact, ...]:
    failures: set[tuple[HarnessFailureStage, str]] = set()
    calls = _trajectory_tool_calls(trajectory)
    agent_turns = sum(
        1
        for step in trajectory.get("steps", []) or []
        if isinstance(step, dict) and step.get("source") == "agent"
    )
    if len(calls) > CALIBRATION_MAX_TOOL_CALLS:
        failures.add((HarnessFailureStage.BUDGET, "tool_calls"))
    if agent_turns > CALIBRATION_MAX_TURNS:
        failures.add((HarnessFailureStage.BUDGET, "turns"))
    if (
        isinstance(input_tokens, int)
        and not isinstance(input_tokens, bool)
        and isinstance(output_tokens, int)
        and not isinstance(output_tokens, bool)
        and input_tokens + output_tokens > CALIBRATION_MAX_TOTAL_TOKENS
    ):
        failures.add((HarnessFailureStage.BUDGET, "tokens"))
    if any(
        artifact.artifact_id in {"trajectory", "agent_log"}
        and artifact.size > CALIBRATION_MAX_OUTPUT_BYTES
        for artifact in artifacts
    ):
        failures.add((HarnessFailureStage.BUDGET, "output"))

    if engine == "native-opencode":
        if native_command_policy is None:
            raise HarnessCliError("native OpenCode command policy is unavailable")
        allowed = set(native_command_policy)
        forbidden_tools = {"task", "skill", "webfetch", "websearch"}
        for call in calls:
            function_name = str(call.get("function_name") or "").lower()
            arguments = call.get("arguments")
            if function_name == "bash":
                command = arguments.get("command") if isinstance(arguments, dict) else None
                if not isinstance(command, str) or command not in allowed:
                    failures.add((HarnessFailureStage.POLICY, "bash"))
            elif function_name in forbidden_tools or function_name.startswith("mcp_"):
                failures.add((HarnessFailureStage.POLICY, function_name or "tool"))

    return tuple(
        HarnessCoordinationFact(
            evidence_id=(
                "guard_"
                + _sha256(f"{run_id}:{stage.value}:{detail}".encode("utf-8"))[:24]
            ),
            stage=stage,
            failed=True,
        )
        for stage, detail in sorted(failures, key=lambda item: (item[0].value, item[1]))
    )


def _collect_run_record(
    *,
    job_root: Path,
    run_id: str,
    task_id: str,
    engine: str,
    attempt: int,
    candidate_sha: str,
    runner_image_sha256: str,
    task_package_sha256: str,
    verifier_bundle_sha256: str,
    route_binding_sha256: str,
    sealed_checker_sha256: str,
    expected_model_name: str,
    expected_instruction_sha256: str,
    expected_harbor_task_checksum: str,
    expected_source_id: str | None = None,
    expected_revision: str | None = None,
    expected_acceptance_sha256: str | None = None,
    expected_scenario_sha256: str | None = None,
    native_command_policy: Mapping[str, bool] | None = None,
) -> HarnessRunRecord:
    trial_dir = _single_trial_dir(job_root)
    result_path = trial_dir / "result.json"
    result = _read_json_object(result_path)
    if result.get("task_name") != f"modelmirror-coding-v18/{task_id}":
        raise HarnessCliError("Harbor trial task binding changed")
    harbor_task_checksum = result.get("task_checksum")
    if (
        not isinstance(harbor_task_checksum, str)
        or re.fullmatch(r"[a-f0-9]{64}", harbor_task_checksum) is None
    ):
        raise HarnessCliError("Harbor trial omitted its task checksum")
    if harbor_task_checksum != expected_harbor_task_checksum:
        raise HarnessCliError("Harbor trial task checksum changed")
    agent_info = result.get("agent_info")
    if not isinstance(agent_info, dict):
        raise HarnessCliError("Harbor trial omitted agent identity")
    expected_agent = "opencode" if engine == "native-opencode" else "modelmirror-worker"
    if agent_info.get("name") != expected_agent:
        raise HarnessCliError("Harbor trial agent binding changed")
    if engine == "native-opencode" and agent_info.get("version") != NATIVE_OPENCODE_VERSION:
        raise HarnessCliError("native OpenCode version is not fixed at 1.18.9")

    workspace_path = trial_dir / "artifacts" / "workspace"
    workspace_summary = _artifact_summary("workspace", workspace_path)
    summaries: list[HarnessArtifactSummary] = [
        _artifact_summary("harbor_result", result_path),
        workspace_summary,
    ]
    trajectory_path = trial_dir / "agent" / "trajectory.json"
    exception = result.get("exception_info")
    if trajectory_path.is_file():
        trajectory = _read_json_object(trajectory_path)
        agent = trajectory.get("agent")
        if (
            not isinstance(agent, dict)
            or agent.get("model_name") != expected_model_name
        ):
            raise HarnessCliError("Harbor trajectory model binding changed")
        user_steps = [
            step
            for step in trajectory.get("steps", [])
            if isinstance(step, dict) and step.get("source") == "user"
        ]
        if (
            len(user_steps) != 1
            or not isinstance(user_steps[0].get("message"), str)
            or _sha256(user_steps[0]["message"].encode("utf-8"))
            != expected_instruction_sha256
        ):
            raise HarnessCliError("Harbor trajectory instruction binding changed")
        summaries.append(_artifact_summary("trajectory", trajectory_path))
        if engine == "modelmirror-worker":
            if (
                expected_source_id is None
                or expected_revision is None
                or expected_acceptance_sha256 is None
            ):
                raise HarnessCliError("Worker source binding is unavailable")
            facts, worker_summaries = _worker_facts(
                trial_dir=trial_dir,
                trajectory=trajectory,
                expected_fixture_task_id=task_id,
                expected_source_id=expected_source_id,
                expected_revision=expected_revision,
                expected_instruction_sha256=expected_instruction_sha256,
                expected_acceptance_sha256=expected_acceptance_sha256,
                expected_scenario_sha256=expected_scenario_sha256,
                workspace_path=workspace_path,
            )
            summaries.extend(worker_summaries)
        else:
            if native_command_policy is None:
                raise HarnessCliError("native OpenCode command policy is unavailable")
            native_ledger_path = (
                trial_dir / "agent" / "modelmirror-native-harness-ledger.json"
            )
            native_ledger = _read_json_object(native_ledger_path)
            binding = native_ledger.get("run_binding")
            if (
                not isinstance(binding, dict)
                or binding.get("task_id") != task_id
                or binding.get("instruction_sha256") != expected_instruction_sha256
                or binding.get("scenario_sha256") != expected_scenario_sha256
                or binding.get("session_id") != trajectory.get("session_id")
                or binding.get("model_name") != expected_model_name
                or binding.get("opencode_version") != NATIVE_OPENCODE_VERSION
                or not isinstance(binding.get("environment_id"), str)
                or re.fullmatch(r"[a-f0-9]{32}", binding["environment_id"]) is None
            ):
                raise HarnessCliError("native interaction ledger binding changed")
            facts = _native_facts(
                run_id=run_id,
                trajectory=trajectory,
                ledger=native_ledger,
                workspace_sha256=workspace_summary.sha256,
                command_policy=native_command_policy,
            )
            summaries.append(_artifact_summary("native_ledger", native_ledger_path))
    else:
        facts = HarnessFactSet(
            export_sha256=workspace_summary.sha256,
            trajectory_sha256=_canonical_sha256(
                {"missing": True, "result_sha256": summaries[0].sha256}
            ),
            coordination=(
                HarnessCoordinationFact(
                    evidence_id=f"result_{summaries[0].sha256[:24]}",
                    stage=_failure_from_exception(exception),
                    failed=True,
                ),
            ),
        )
    if exception is not None and not any(item.failed for item in facts.coordination):
        facts = facts.model_copy(
            update={
                "coordination": facts.coordination
                + (
                    HarnessCoordinationFact(
                        evidence_id=f"result_{summaries[0].sha256[:24]}",
                        stage=_failure_from_exception(exception),
                        failed=True,
                    ),
                )
            }
        )

    agent_log = trial_dir / "agent" / "opencode.txt"
    if agent_log.is_file():
        summaries.append(_artifact_summary("agent_log", agent_log))

    started = _parse_timestamp(result.get("started_at"))
    finished = _parse_timestamp(result.get("finished_at"))
    if started is None or finished is None or finished < started:
        raise HarnessCliError("Harbor trial timing is invalid")
    agent_result = result.get("agent_result")
    input_tokens = agent_result.get("n_input_tokens") if isinstance(agent_result, dict) else None
    output_tokens = agent_result.get("n_output_tokens") if isinstance(agent_result, dict) else None
    verifier = result.get("verifier_result")
    rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
    reward = rewards.get("reward") if isinstance(rewards, dict) else None
    if isinstance(reward, bool) or not isinstance(reward, (int, float)):
        raise HarnessCliError("Harbor trial omitted the numeric reward")
    if (
        float(reward) == 1.0
        and exception is None
        and (
            not isinstance(input_tokens, int)
            or isinstance(input_tokens, bool)
            or not isinstance(output_tokens, int)
            or isinstance(output_tokens, bool)
            or input_tokens + output_tokens <= 0
        )
    ):
        raise HarnessCliError("accepted Harness trial omitted provable token usage")
    if trajectory_path.is_file():
        guard_facts = _run_guard_facts(
            run_id=run_id,
            engine=engine,
            trajectory=trajectory,
            native_command_policy=native_command_policy,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            artifacts=summaries,
        )
        if guard_facts:
            facts = facts.model_copy(
                update={"coordination": facts.coordination + guard_facts}
            )
    diagnostics = derive_diagnostics(facts)
    accepted = (
        float(reward) == 1.0
        and exception is None
        and not any(item.failed for item in facts.coordination)
        and diagnostics.platform_coordination_failures == 0
        and diagnostics.duplicate_side_effects == 0
        and diagnostics.unsettled_operations == 0
        and diagnostics.orphaned_interactions == 0
    )
    failure_stage: HarnessFailureStage | None = None
    if not accepted:
        failed_coordination = next(
            (item.stage for item in facts.coordination if item.failed), None
        )
        if failed_coordination is not None:
            failure_stage = failed_coordination
        elif diagnostics.duplicate_side_effects or diagnostics.unsettled_operations:
            failure_stage = HarnessFailureStage.TOOL_VALIDATION
        elif diagnostics.orphaned_interactions:
            failure_stage = HarnessFailureStage.INTERACTION
        elif exception is not None:
            failure_stage = _failure_from_exception(exception)
        else:
            failure_stage = HarnessFailureStage.SEALED_CHECKER
    return HarnessRunRecord(
        run_id=run_id,
        task_id=task_id,
        engine=engine,
        attempt=attempt,
        candidate_sha=candidate_sha,
        runner_image_sha256=runner_image_sha256,
        task_package_sha256=task_package_sha256,
        verifier_bundle_sha256=verifier_bundle_sha256,
        harbor_task_checksum=harbor_task_checksum,
        route_binding_sha256=route_binding_sha256,
        sealed_checker_sha256=sealed_checker_sha256,
        accepted=accepted,
        failure_stage=failure_stage,
        duration_seconds=(finished - started).total_seconds(),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        artifacts=tuple(summaries),
        facts=facts,
        diagnostics=diagnostics,
    )


def _run_harbor_gate(
    executable: str,
    *,
    task_root: Path,
    agent: str,
    environment_type: str,
    network_overlay: Path | None = None,
) -> float:
    with tempfile.TemporaryDirectory(prefix="modelmirror-harbor-gate-") as directory:
        jobs_root = Path(directory)
        command = [
            executable,
            "run",
            "-p",
            str(task_root),
            "--jobs-dir",
            str(jobs_root),
            "--job-name",
            f"gate-{uuid.uuid4().hex}",
            "--quiet",
            "--n-concurrent",
            "1",
            "-e",
            environment_type,
        ]
        if network_overlay is not None:
            command.extend(["--extra-docker-compose", str(network_overlay)])
        command.extend(["-a", agent])
        subprocess.run(
            command,
            env=_harbor_subprocess_environment(),
            check=False,
        )
        # Harbor may use a non-zero process status for a valid reward-0 trial.
        # Gate truth comes only from the bound trial result, never CLI success.
        return _harbor_trial_reward(jobs_root)


def _run_harbor_batch_gate(
    executable: str,
    *,
    tasks_root: Path,
    agent: str,
    repetitions: int,
    n_concurrent: int,
    environment_type: str,
    network_overlay: Path | None = None,
) -> dict[str, tuple[float, ...]]:
    """Run one complete deterministic gate without 60 controller startups."""

    with tempfile.TemporaryDirectory(prefix="modelmirror-harbor-gate-") as directory:
        jobs_root = Path(directory)
        job_name = f"gate-{uuid.uuid4().hex}"
        command = [
            executable,
            "run",
            "-p",
            str(tasks_root),
            "--jobs-dir",
            str(jobs_root),
            "--job-name",
            job_name,
            "--quiet",
            "--n-concurrent",
            str(n_concurrent),
            "--n-attempts",
            str(repetitions),
            "-e",
            environment_type,
        ]
        if network_overlay is not None:
            command.extend(["--extra-docker-compose", str(network_overlay)])
        command.extend(["-a", agent])
        subprocess.run(
            command,
            env=_harbor_subprocess_environment(),
            check=False,
        )

        results: dict[str, list[float]] = defaultdict(list)
        job_root = jobs_root / job_name
        for result_path in sorted(job_root.glob("*/result.json")):
            result = _read_json_object(result_path)
            task_name = result.get("task_name")
            verifier = result.get("verifier_result")
            rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
            reward = rewards.get("reward") if isinstance(rewards, dict) else None
            if (
                not isinstance(task_name, str)
                or "/" not in task_name
                or result.get("exception_info") is not None
                or isinstance(reward, bool)
                or not isinstance(reward, (int, float))
                or not 0.0 <= float(reward) <= 1.0
            ):
                raise HarnessCliError("Harbor batch gate produced an invalid trial")
            results[task_name.rsplit("/", 1)[-1]].append(float(reward))
        return {task_id: tuple(values) for task_id, values in results.items()}


def task_gate(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    bundle = compile_bundle(root, write=False)
    _harbor_version(args.harbor)
    agents = (
        ("oracle", True),
        ("nop", False),
        ("scripts.coding_worker_harbor_agent:NearMissPatchAgent", False),
    )
    expected_tasks = {item.task_id for item in bundle.fixtures}
    with _materialized_tasks_root(
        root,
        args.sealed_checker_root.resolve(),
        bundle,
        harbor_executable=args.harbor,
    ) as (tasks_root, sealed_checker_sha256, task_checksums):
        for agent, expected_pass in agents:
            rewards = _run_harbor_batch_gate(
                args.harbor,
                tasks_root=tasks_root,
                agent=agent,
                repetitions=args.repetitions,
                n_concurrent=args.n_concurrent,
                environment_type=args.environment,
                network_overlay=args.network_overlay,
            )
            if set(rewards) != expected_tasks:
                raise HarnessCliError(f"{agent} gate did not cover the exact task set")
            for task_id, task_rewards in rewards.items():
                if len(task_rewards) != args.repetitions:
                    raise HarnessCliError(
                        f"{task_id} {agent} gate did not produce {args.repetitions} trials"
                    )
                expected_reward = 1.0 if expected_pass else 0.0
                if any(reward != expected_reward for reward in task_rewards):
                    raise HarnessCliError(
                        f"{task_id} {agent} gate produced rewards {task_rewards}"
                    )
            for task_id, expected_checksums in task_checksums.items():
                observed_checksums = dict(
                    zip(
                        ("content", "result"),
                        _harbor_task_checksums(args.harbor, tasks_root / task_id),
                        strict=True,
                    )
                )
                if observed_checksums != expected_checksums:
                    raise HarnessCliError(f"Harbor task changed during gate: {task_id}")
        print(
            json.dumps(
                {
                    "status": "valid",
                    "sealed_checker_sha256": sealed_checker_sha256,
                    "trials": len(bundle.fixtures) * len(agents) * args.repetitions,
                },
                sort_keys=True,
            )
        )


class DeterministicHarnessFakeRunner:
    """Exercise the v3 record contract without Docker or a model provider."""

    @staticmethod
    def run(fixture: HarnessFixture, engine: str) -> HarnessRunRecord:
        run_id = f"smoke_{fixture.task_id}_{engine}"
        export_sha256 = _sha256(f"{run_id}:export".encode())
        trajectory_sha256 = _sha256(f"{run_id}:trajectory".encode())
        facts = HarnessFactSet(
            export_sha256=export_sha256,
            trajectory_sha256=trajectory_sha256,
        )
        artifact_ids = ["harbor_result", "workspace", "trajectory"]
        if engine == "modelmirror-worker":
            artifact_ids.extend(["worker_facts", "worker_ledger"])
        else:
            artifact_ids.append("native_ledger")
        artifacts = tuple(
            HarnessArtifactSummary(
                artifact_id=artifact_id,
                sha256=_sha256(f"{run_id}:{artifact_id}".encode()),
                size=0,
            )
            for artifact_id in artifact_ids
        )
        return HarnessRunRecord(
            run_id=run_id,
            task_id=fixture.task_id,
            engine=engine,
            attempt=1,
            candidate_sha="0" * 40,
            runner_image_sha256=f"sha256:{'0' * 64}",
            task_package_sha256=fixture.task_package_sha256,
            verifier_bundle_sha256=fixture.verifier_bundle_sha256,
            harbor_task_checksum=_sha256(f"fake:{fixture.task_id}".encode()),
            route_binding_sha256=_canonical_sha256({"runner": "fake"}),
            sealed_checker_sha256=_canonical_sha256({"checker": "fake"}),
            accepted=True,
            duration_seconds=0,
            artifacts=artifacts,
            facts=facts,
            diagnostics=derive_diagnostics(facts),
        )


def smoke(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    bundle = compile_bundle(root, write=False)
    selected: list[HarnessFixture] = []
    for category in ("python", "typescript", "repository", "session"):
        selected.append(next(item for item in bundle.fixtures if item.category == category))
    runner = DeterministicHarnessFakeRunner()
    records = tuple(
        runner.run(fixture, engine)
        for fixture in selected
        for engine in ("native-opencode", "modelmirror-worker")
    )
    print(
        json.dumps(
            {
                "status": "valid",
                "runner": "fake",
                "categories": sorted({item.category for item in selected}),
                "records": len(records),
                "records_sha256": _canonical_sha256(
                    [item.model_dump(mode="json") for item in records]
                ),
            },
            sort_keys=True,
        )
    )


def run_round(args: argparse.Namespace) -> None:
    if args.environment == DOCKER_DESKTOP_PROBE_ENVIRONMENT:
        raise HarnessCliError(
            "Docker Desktop probe environment cannot run calibration"
        )
    root = args.root.resolve()
    bundle = compile_bundle(root, write=False)
    _assert_native_runtime_fixture_coverage(root, bundle)
    native_task_runtime_sha256 = _native_task_runtime_image_sha256()
    _assert_native_interaction_parity_available(bundle, root=root)
    _harbor_version(args.harbor)
    _require_frozen_candidate(args.candidate_sha)
    runner_image_sha256 = _runtime_runner_image_sha256()
    if args.repetitions != 2:
        raise HarnessCliError("V18 real calibration is fixed at two repetitions")
    controller_token = os.getenv("CODING_WORKER_HARNESS_CONTROLLER_TOKEN", "")
    if len(controller_token) < 32:
        raise HarnessCliError("Harness controller token is unavailable")
    worker_url = _loopback_worker_url(args.worker_url)
    worker_attestation_sha256 = _worker_attestation_sha256(
        worker_url=worker_url,
        controller_token=controller_token,
        model=args.model,
        model_route=args.worker_model_route,
    )
    jobs_dir = args.jobs_dir.resolve()
    runs_dir = args.runs_dir.resolve()
    jobs_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    runs = [
        (fixture, engine, attempt)
        for fixture in bundle.fixtures
        for engine in ("native-opencode", "modelmirror-worker")
        for attempt in range(1, args.repetitions + 1)
    ]
    random.Random(args.seed).shuffle(runs)
    environment = _harbor_subprocess_environment()
    allowed_agent_hosts = _model_agent_hosts(args.allow_agent_host)
    native_command_policies = {
        fixture.task_id: _native_shell_policy(
            root / "tasks" / fixture.task_id, fixture
        )
        for fixture in bundle.fixtures
    }
    native_opencode_configs = {
        fixture.task_id: _native_opencode_config(
            tuple(native_command_policies[fixture.task_id]),
            allow_question=bool(
                (_scenario_payload(root / "tasks" / fixture.task_id) or {}).get(
                    "questions"
                )
            ),
        )
        for fixture in bundle.fixtures
    }
    calibration_policy_sha256 = _canonical_sha256(
        {
            "native_command_policies": native_command_policies,
            "native_opencode_configs": native_opencode_configs,
            "max_turns": CALIBRATION_MAX_TURNS,
            "max_tool_calls": CALIBRATION_MAX_TOOL_CALLS,
            "max_output_bytes": CALIBRATION_MAX_OUTPUT_BYTES,
            "max_total_tokens": CALIBRATION_MAX_TOTAL_TOKENS,
        }
    )
    route_binding_sha256 = _canonical_sha256(
        {
            "native_model": args.model,
            "worker_model_route": args.worker_model_route,
            "allowed_agent_hosts": allowed_agent_hosts,
            "calibration_policy_sha256": calibration_policy_sha256,
            "worker_attestation_sha256": worker_attestation_sha256,
            "runner_image_sha256": runner_image_sha256,
            "native_task_runtime_sha256": native_task_runtime_sha256,
        }
    )
    with _materialized_tasks_root(
        root,
        args.sealed_checker_root.resolve(),
        bundle,
        harbor_executable=args.harbor,
    ) as (tasks_root, sealed_checker_sha256, task_checksums):
        for fixture, engine, attempt in runs:
            run_id = f"run_{fixture.task_id}_{engine}_{attempt}"
            output_path = runs_dir / f"{run_id}.json"
            job_root = jobs_dir / run_id
            if output_path.exists() or job_root.exists():
                raise HarnessCliError(f"Harness run output already exists: {run_id}")
            command = [
                args.harbor,
                "run",
                "-p",
                str(tasks_root / fixture.task_id),
                "-m",
                args.model,
                "--jobs-dir",
                str(jobs_dir),
                "--job-name",
                run_id,
                "--quiet",
                "--yes",
                "--n-concurrent",
                "1",
            ]
            for hostname in allowed_agent_hosts:
                command.extend(["--allow-agent-host", hostname])
            if args.environment:
                command.extend(["-e", args.environment])
            if args.network_overlay is not None:
                command.extend(["--extra-docker-compose", str(args.network_overlay)])
            if engine == "native-opencode":
                opencode_config = json.dumps(
                    native_opencode_configs[fixture.task_id],
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
                command.extend(
                    [
                        "-a",
                        "scripts.coding_worker_native_agent:NativeOpenCodeHarnessAgent",
                        "--agent-kwarg",
                        f"version={NATIVE_OPENCODE_VERSION}",
                        "--agent-kwarg",
                        f"opencode_config={opencode_config}",
                    ]
                )
            else:
                command.extend(
                    ["-a", "scripts.coding_worker_harbor_agent:ModelMirrorWorkerAgent"]
                )
            run_environment = _harbor_engine_environment(
                environment,
                engine=engine,
                controller_token=controller_token,
                benchmark_root=root,
                worker_url=worker_url,
                worker_model_route=args.worker_model_route,
            )
            completed = subprocess.run(command, env=run_environment, check=False)
            try:
                observed_checksums = dict(
                    zip(
                        ("content", "result"),
                        _harbor_task_checksums(
                            args.harbor, tasks_root / fixture.task_id
                        ),
                        strict=True,
                    )
                )
                if observed_checksums != task_checksums[fixture.task_id]:
                    raise HarnessCliError(
                        f"Harbor task changed during its round: {fixture.task_id}"
                    )
                current_fixture, _binding = _task_fixture(
                    root / "tasks" / fixture.task_id
                )
                if current_fixture != fixture:
                    raise HarnessCliError(
                        f"Harness task changed during its round: {fixture.task_id}"
                    )
                record = _collect_run_record(
                    job_root=job_root,
                    run_id=run_id,
                    task_id=fixture.task_id,
                    engine=engine,
                    attempt=attempt,
                    candidate_sha=args.candidate_sha,
                    runner_image_sha256=runner_image_sha256,
                    task_package_sha256=fixture.task_package_sha256,
                    verifier_bundle_sha256=fixture.verifier_bundle_sha256,
                    route_binding_sha256=route_binding_sha256,
                    sealed_checker_sha256=sealed_checker_sha256,
                    expected_model_name=(
                        args.model
                        if engine == "native-opencode"
                        else args.worker_model_route
                    ),
                    expected_instruction_sha256=fixture.instruction_sha256,
                    expected_harbor_task_checksum=task_checksums[fixture.task_id][
                        "result"
                    ],
                    expected_source_id=fixture.source_id,
                    expected_revision=fixture.revision,
                    expected_acceptance_sha256=_canonical_sha256(
                        _fixture_acceptance(fixture)
                    ),
                    expected_scenario_sha256=fixture.scenario_sha256,
                    native_command_policy=(
                        native_command_policies[fixture.task_id]
                        if engine == "native-opencode"
                        else None
                    ),
                )
            except HarnessCliError as exc:
                raise HarnessCliError(
                    f"{run_id} did not produce a provable run record "
                    f"(Harbor exit {completed.returncode}): {exc}"
                ) from exc
            temporary = output_path.with_suffix(".json.tmp")
            temporary.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
            temporary.replace(output_path)
    if (
        _worker_attestation_sha256(
            worker_url=worker_url,
            controller_token=controller_token,
            model=args.model,
            model_route=args.worker_model_route,
        )
        != worker_attestation_sha256
    ):
        raise HarnessCliError("Worker Harness attestation changed during its round")
    if _native_task_runtime_image_sha256() != native_task_runtime_sha256:
        raise HarnessCliError("native task runtime image changed during its round")


def create_report(args: argparse.Namespace) -> None:
    runs = tuple(
        HarnessRunRecord.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(args.runs.glob("*.json"))
    )
    if not runs:
        raise HarnessCliError("no fact-derived Harness v3 run records were found")
    bundle = load_harness_fixture_bundle(args.root / "fixture-bundle.json")
    expected = {
        (fixture.task_id, engine, attempt)
        for fixture in bundle.fixtures
        for engine in ("native-opencode", "modelmirror-worker")
        for attempt in (1, 2)
    }
    observed = {(item.task_id, item.engine, item.attempt) for item in runs}
    if len(runs) != 48 or observed != expected:
        raise HarnessCliError("a V18 calibration report requires the exact 48-run matrix")
    fixtures = {item.task_id: item for item in bundle.fixtures}
    task_checksums: dict[str, set[str]] = defaultdict(set)
    route_bindings = {run.route_binding_sha256 for run in runs}
    if len(route_bindings) != 1:
        raise HarnessCliError("model route binding changed within the calibration")
    sealed_checkers = {run.sealed_checker_sha256 for run in runs}
    if len(sealed_checkers) != 1:
        raise HarnessCliError("sealed checker binding changed within the calibration")
    candidates = {run.candidate_sha for run in runs}
    if len(candidates) != 1:
        raise HarnessCliError("candidate commit changed within the calibration")
    runner_images = {run.runner_image_sha256 for run in runs}
    if len(runner_images) != 1:
        raise HarnessCliError("runner image changed within the calibration")
    for run in runs:
        fixture = fixtures[run.task_id]
        if (
            run.task_package_sha256 != fixture.task_package_sha256
            or run.verifier_bundle_sha256 != fixture.verifier_bundle_sha256
        ):
            raise HarnessCliError("a Harness run is not bound to the fixture bundle")
        task_checksums[run.task_id].add(run.harbor_task_checksum)
    if any(len(values) != 1 for values in task_checksums.values()):
        raise HarnessCliError("Harbor task checksums changed within the calibration")
    report = build_harness_report(
        report_mode="calibration",
        candidate_sha=next(iter(candidates)),
        fixture_bundle_sha256=bundle.canonical_sha256(),
        sealed_checker_sha256=next(iter(sealed_checkers)),
        runner_image_sha256=next(iter(runner_images)),
        route_binding_sha256=next(iter(route_bindings)),
        runs=runs,
    )
    args.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")


def certify(args: argparse.Namespace) -> None:
    reports: list[HarnessReport] = []
    for path in args.reports:
        payload = json.loads(path.read_text(encoding="utf-8"))
        eligibility = report_eligibility(payload)
        if eligibility != "certifying":
            raise HarnessCliError(
                f"{path} is {eligibility}; V18 calibration reports cannot certify"
            )
        reports.append(HarnessReport.model_validate(payload))
    if len(reports) != 2:
        raise HarnessCliError("certification requires exactly two certifying v3 reports")
    bindings = {
        (
            item.candidate_sha,
            item.fixture_bundle_sha256,
            item.sealed_checker_sha256,
            item.runner_image_sha256,
            item.route_binding_sha256,
        )
        for item in reports
    }
    if len(bindings) != 1:
        raise HarnessCliError("certification report bindings differ")
    print("certifying-v3-bindings-valid")


def validate(args: argparse.Namespace) -> None:
    root = args.root.resolve()
    bundle = compile_bundle(root, write=False)
    if bundle.protocol != HARNESS_PROTOCOL:
        raise HarnessCliError("Harness v3 protocol binding changed")
    sealed_checker_sha256 = _validate_sealed_checkers(
        args.sealed_checker_root.resolve(), bundle, public_root=root
    )
    print(
        json.dumps(
            {
                "status": "valid",
                "tasks": len(bundle.fixtures),
                "bundle_sha256": bundle.canonical_sha256(),
                "sealed_checker_sha256": sealed_checker_sha256,
                "harbor": HARBOR_VERSION,
                "native_opencode": NATIVE_OPENCODE_VERSION,
                "report_mode": bundle.report_mode,
            },
            sort_keys=True,
        )
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="ModelMirror Coding Worker Harness v3")
    result.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    subparsers = result.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser("compile")
    compile_parser.add_argument("--write", action="store_true")
    compile_parser.set_defaults(handler=lambda args: compile_bundle(args.root.resolve(), write=args.write))

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--sealed-checker-root", type=Path, required=True)
    validate_parser.set_defaults(handler=validate)

    gate_parser = subparsers.add_parser("task-gate")
    gate_parser.add_argument("--harbor", default="harbor")
    gate_parser.add_argument("--sealed-checker-root", type=Path, required=True)
    gate_parser.add_argument("--repetitions", type=int, choices=(5,), default=5)
    gate_parser.add_argument("--n-concurrent", type=int, choices=(1, 2), default=2)
    gate_parser.add_argument(
        "--environment",
        default=(
            "scripts.coding_worker_harbor_environment:"
            "StaticNoNetworkDockerEnvironment"
        ),
    )
    gate_parser.add_argument("--network-overlay", type=Path)
    gate_parser.set_defaults(handler=task_gate)

    smoke_parser = subparsers.add_parser("smoke")
    smoke_parser.set_defaults(handler=smoke)

    calibration_parser = subparsers.add_parser("run-round")
    calibration_parser.add_argument("--harbor", default="harbor")
    calibration_parser.add_argument("--candidate-sha", required=True)
    calibration_parser.add_argument("--model", required=True)
    calibration_parser.add_argument("--worker-url", required=True)
    calibration_parser.add_argument("--worker-model-route", required=True)
    calibration_parser.add_argument("--sealed-checker-root", type=Path, required=True)
    calibration_parser.add_argument(
        "--allow-agent-host",
        action="append",
        required=True,
        help="Exact model gateway hostname allowed during Harbor agent.run().",
    )
    calibration_parser.add_argument("--seed", type=int, required=True)
    calibration_parser.add_argument("--repetitions", type=int, choices=(2,), default=2)
    calibration_parser.add_argument("--jobs-dir", type=Path, required=True)
    calibration_parser.add_argument("--runs-dir", type=Path, required=True)
    calibration_parser.add_argument("--environment")
    calibration_parser.add_argument("--network-overlay", type=Path)
    calibration_parser.set_defaults(handler=run_round)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--runs", type=Path, required=True)
    report_parser.add_argument("--output", type=Path, required=True)
    report_parser.set_defaults(handler=create_report)

    certify_parser = subparsers.add_parser("certify")
    certify_parser.add_argument("reports", type=Path, nargs="+")
    certify_parser.set_defaults(handler=certify)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        args.handler(args)
    except HarnessCliError as exc:
        print(f"harness-error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
