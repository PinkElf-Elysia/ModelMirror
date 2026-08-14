from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import Field, field_validator

from .contracts import SAFE_ID, StrictModel
from .parity import (
    ATTEMPTS_PER_ENGINE,
    PARITY_PROTOCOL,
    FrozenParityBudget,
    FrozenParityManifest,
    FrozenParityTask,
    ParityEngine,
    ParityFailureKind,
    ParityRunOutcome,
)


MAX_RUNNER_RESPONSE_BYTES = 1024 * 1024
MAX_EXPORTED_WORKSPACE_BYTES = 1024 * 1024 * 1024
ResponseModel = TypeVar("ResponseModel", bound=StrictModel)


class ParityRunnerError(RuntimeError):
    pass


class ParityRunRequest(StrictModel):
    protocol: str = PARITY_PROTOCOL
    run_id: str
    task_id: str
    engine: ParityEngine
    attempt: int = Field(ge=1, le=ATTEMPTS_PER_ENGINE)
    objective: str = Field(min_length=1, max_length=4096)
    fixture_id: str
    fixture_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    initial_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    hidden_check_bundle_id: str
    hidden_check_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fixture_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    hidden_checker_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runner_image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    model_route_catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    budget: FrozenParityBudget
    model_route_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    task_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("run_id", "task_id", "fixture_id", "hidden_check_bundle_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("runner identifiers must be opaque safe ids")
        return value


class ParityExecutionRequest(StrictModel):
    """Runner-visible request with no hidden-check locator or checker digest."""

    protocol: str = PARITY_PROTOCOL
    run_id: str
    task_id: str
    engine: ParityEngine
    attempt: int = Field(ge=1, le=ATTEMPTS_PER_ENGINE)
    objective: str = Field(min_length=1, max_length=4096)
    fixture_id: str
    fixture_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    initial_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    fixture_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runner_image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    model_route_catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    budget: FrozenParityBudget
    model_route_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    task_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("run_id", "task_id", "fixture_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("runner identifiers must be opaque safe ids")
        return value


class ParityArtifactReference(StrictModel):
    artifact_id: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=1, le=MAX_EXPORTED_WORKSPACE_BYTES)

    @field_validator("artifact_id")
    @classmethod
    def validate_artifact_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("parity artifact id is invalid")
        return value


class ParityExecutionExport(StrictModel):
    """Untrusted runner result before the sealed checker evaluates it."""

    protocol: str = PARITY_PROTOCOL
    run_id: str
    task_id: str
    engine: ParityEngine
    attempt: int = Field(ge=1, le=ATTEMPTS_PER_ENGINE)
    engine_version: str = Field(min_length=1, max_length=128)
    model_route_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fixture_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runner_image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    candidate_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    task_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    initial_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    final_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    workspace_export: ParityArtifactReference
    raw_artifact_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy_violations: tuple[str, ...] = Field(default=(), max_length=32)
    timeout: bool = False
    budget_limited: bool = False
    stuck: bool = False
    manual_repair: bool = False
    undeclared_side_effect: bool = False
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    active_seconds: float = Field(ge=0)

    @field_validator("run_id", "task_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("parity export identifiers are invalid")
        return value


class ParityCheckRequest(StrictModel):
    """Minimal request visible to the isolated checker.

    It deliberately excludes the objective, model route, provider identity and
    runner endpoint. The opaque artifact is resolved inside the parity profile.
    """

    protocol: str = PARITY_PROTOCOL
    run_id: str
    task_id: str
    attempt: int = Field(ge=1, le=ATTEMPTS_PER_ENGINE)
    hidden_check_bundle_id: str
    hidden_check_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    hidden_checker_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    initial_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    final_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    workspace_export: ParityArtifactReference

    @field_validator("run_id", "task_id", "hidden_check_bundle_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("parity checker identifiers are invalid")
        return value


class ParityCheckReceipt(StrictModel):
    protocol: str = PARITY_PROTOCOL
    run_id: str
    task_id: str
    attempt: int = Field(ge=1, le=ATTEMPTS_PER_ENGINE)
    hidden_check_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    hidden_checker_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    initial_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    final_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    workspace_export_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    hidden_checks_passed: bool
    allowed_diff: bool

    @field_validator("run_id", "task_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("parity checker receipt identifiers are invalid")
        return value

    def canonical_sha256(self) -> str:
        encoded = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class ParityRunner(Protocol):
    @property
    def engine(self) -> ParityEngine: ...

    def execute(self, request: ParityRunRequest) -> ParityRunOutcome: ...


class SubprocessParityRunner:
    """Strict JSON subprocess boundary for a separately packaged runner.

    The executable resolves opaque fixture/check ids inside its own controlled
    image. Physical paths, credentials, hidden check bodies, and provider
    endpoints never cross this request boundary.
    """

    def __init__(
        self,
        *,
        engine: ParityEngine,
        argv: Sequence[str],
        timeout_seconds: int = 7200,
    ) -> None:
        if not argv or len(argv) > 32 or any(not item or "\0" in item for item in argv):
            raise ValueError("runner argv is invalid")
        if timeout_seconds < 30 or timeout_seconds > 14_400:
            raise ValueError("runner timeout is invalid")
        self._engine = engine
        self._argv = tuple(argv)
        self._timeout_seconds = timeout_seconds

    @property
    def engine(self) -> ParityEngine:
        return self._engine

    def execute(self, request: ParityRunRequest) -> ParityRunOutcome:
        if request.engine is not self.engine:
            raise ParityRunnerError("runner engine binding is invalid")
        payload = json.dumps(
            request.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            completed = subprocess.run(
                list(self._argv),
                input=payload,
                capture_output=True,
                check=False,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=self._timeout_seconds,
                shell=False,
                env=_runner_environment(),
            )
        except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
            raise ParityRunnerError("parity runner is unavailable") from exc
        stdout = completed.stdout.encode("utf-8")
        if completed.returncode != 0 or not stdout or len(stdout) > MAX_RUNNER_RESPONSE_BYTES:
            raise ParityRunnerError("parity runner failed")
        try:
            outcome = ParityRunOutcome.model_validate_json(stdout)
        except ValueError as exc:
            raise ParityRunnerError("parity runner response is invalid") from exc
        _assert_bound_outcome(request, outcome)
        return outcome


class SeparatedParityRunner:
    """Run an untrusted engine and an isolated sealed checker separately.

    The runner receives only ``ParityExecutionRequest``. The checker receives
    only an opaque exported-workspace reference plus frozen checker bindings.
    Neither process can self-attest the other process' portion of the result.
    """

    def __init__(
        self,
        *,
        engine: ParityEngine,
        runner_argv: Sequence[str],
        checker_argv: Sequence[str],
        timeout_seconds: int = 7200,
        checker_timeout_seconds: int = 900,
    ) -> None:
        _validate_argv(runner_argv)
        _validate_argv(checker_argv)
        if tuple(runner_argv) == tuple(checker_argv):
            raise ValueError("runner and checker commands must be independent")
        if timeout_seconds < 30 or timeout_seconds > 14_400:
            raise ValueError("runner timeout is invalid")
        if checker_timeout_seconds < 10 or checker_timeout_seconds > 3600:
            raise ValueError("checker timeout is invalid")
        self._engine = engine
        self._runner_argv = tuple(runner_argv)
        self._checker_argv = tuple(checker_argv)
        self._timeout_seconds = timeout_seconds
        self._checker_timeout_seconds = checker_timeout_seconds

    @property
    def engine(self) -> ParityEngine:
        return self._engine

    def execute(self, request: ParityRunRequest) -> ParityRunOutcome:
        if request.engine is not self.engine:
            raise ParityRunnerError("runner engine binding is invalid")
        execution_request = _execution_request(request)
        execution = _invoke_json_process(
            argv=self._runner_argv,
            payload=execution_request.model_dump(mode="json"),
            response_type=ParityExecutionExport,
            timeout_seconds=self._timeout_seconds,
            failure="parity runner",
        )
        _assert_bound_export(execution_request, execution)

        check_request = ParityCheckRequest(
            run_id=request.run_id,
            task_id=request.task_id,
            attempt=request.attempt,
            hidden_check_bundle_id=request.hidden_check_bundle_id,
            hidden_check_sha256=request.hidden_check_sha256,
            hidden_checker_bundle_sha256=request.hidden_checker_bundle_sha256,
            initial_tree_hash=request.initial_tree_hash,
            final_tree_hash=execution.final_tree_hash,
            workspace_export=execution.workspace_export,
        )
        receipt = _invoke_json_process(
            argv=self._checker_argv,
            payload=check_request.model_dump(mode="json"),
            response_type=ParityCheckReceipt,
            timeout_seconds=self._checker_timeout_seconds,
            failure="parity checker",
        )
        _assert_bound_receipt(check_request, receipt)
        outcome = _outcome_from_checked_export(request, execution, receipt)
        _assert_bound_outcome(request, outcome)
        return outcome


def run_parity_matrix(
    *,
    manifest: FrozenParityManifest,
    runners: Mapping[ParityEngine, ParityRunner],
    candidate_sha: str,
    model_route_receipt_sha256: str,
) -> tuple[ParityRunOutcome, ...]:
    return _run_cells(
        manifest=manifest,
        runners=runners,
        candidate_sha=candidate_sha,
        model_route_receipt_sha256=model_route_receipt_sha256,
        tasks=manifest.tasks,
        attempts=range(1, ATTEMPTS_PER_ENGINE + 1),
    )


def run_parity_smoke(
    *,
    manifest: FrozenParityManifest,
    runners: Mapping[ParityEngine, ParityRunner],
    candidate_sha: str,
    model_route_receipt_sha256: str,
) -> tuple[ParityRunOutcome, ...]:
    categories: dict[str, FrozenParityTask] = {}
    for task in manifest.tasks:
        categories.setdefault(task.category.value, task)
    return _run_cells(
        manifest=manifest,
        runners=runners,
        candidate_sha=candidate_sha,
        model_route_receipt_sha256=model_route_receipt_sha256,
        tasks=tuple(categories.values()),
        attempts=(1,),
    )


def _run_cells(
    *,
    manifest: FrozenParityManifest,
    runners: Mapping[ParityEngine, ParityRunner],
    candidate_sha: str,
    model_route_receipt_sha256: str,
    tasks: Sequence[FrozenParityTask],
    attempts: Sequence[int] | range,
) -> tuple[ParityRunOutcome, ...]:
    if set(runners) != set(ParityEngine):
        raise ValueError("exactly one runner per engine is required")
    if any(runners[engine].engine is not engine for engine in ParityEngine):
        raise ValueError("runner mapping is not engine bound")
    if runners[ParityEngine.NATIVE_OPENCODE] is runners[ParityEngine.MODELMIRROR_WORKER]:
        raise ValueError("native and Worker parity runners must be independent")

    manifest_sha = manifest.canonical_sha256()
    cells = [
        (engine, task, attempt)
        for task in tasks
        for attempt in attempts
        for engine in ParityEngine
    ]
    seed = int.from_bytes(
        bytes.fromhex(manifest_sha)[:16] + bytes.fromhex(candidate_sha)[:16], "big"
    )
    random.Random(seed).shuffle(cells)
    outcomes: list[ParityRunOutcome] = []
    for engine, task, attempt in cells:
        request = _request_for_cell(
            task=task,
            engine=engine,
            attempt=attempt,
            manifest=manifest,
            candidate_sha=candidate_sha,
            manifest_sha=manifest_sha,
            model_route_receipt_sha256=model_route_receipt_sha256,
        )
        outcome = runners[engine].execute(request)
        _assert_bound_outcome(request, outcome)
        outcomes.append(outcome)
    return tuple(outcomes)


def _request_for_cell(
    *,
    task: FrozenParityTask,
    engine: ParityEngine,
    attempt: int,
    manifest: FrozenParityManifest,
    candidate_sha: str,
    manifest_sha: str,
    model_route_receipt_sha256: str,
) -> ParityRunRequest:
    return ParityRunRequest(
        run_id=f"run_{engine.value}_{task.task_id}_{attempt}",
        task_id=task.task_id,
        engine=engine,
        attempt=attempt,
        objective=task.objective,
        fixture_id=task.fixture_id,
        fixture_revision=task.fixture_revision,
        initial_tree_hash=task.initial_tree_hash,
        hidden_check_bundle_id=task.hidden_check_bundle_id,
        hidden_check_sha256=task.hidden_check_sha256,
        fixture_bundle_sha256=manifest.fixture_bundle_sha256,
        hidden_checker_bundle_sha256=manifest.hidden_checker_bundle_sha256,
        runner_image_digest=(
            manifest.runner_images.native_opencode
            if engine is ParityEngine.NATIVE_OPENCODE
            else manifest.runner_images.modelmirror_worker
        ),
        model_route_catalog_sha256=manifest.model_route_catalog_sha256,
        budget=task.budget,
        model_route_receipt_sha256=model_route_receipt_sha256,
        candidate_sha=candidate_sha,
        task_manifest_sha256=manifest_sha,
    )


def _assert_bound_outcome(
    request: ParityRunRequest, outcome: ParityRunOutcome
) -> None:
    expected = (
        request.run_id,
        request.task_id,
        request.engine,
        request.attempt,
        request.model_route_receipt_sha256,
        request.candidate_sha,
        request.task_manifest_sha256,
        request.initial_tree_hash,
        request.fixture_bundle_sha256,
        request.hidden_checker_bundle_sha256,
        request.runner_image_digest,
    )
    observed = (
        outcome.run_id,
        outcome.task_id,
        outcome.engine,
        outcome.attempt,
        outcome.model_route_receipt_sha256,
        outcome.candidate_sha,
        outcome.task_manifest_sha256,
        outcome.initial_tree_hash,
        outcome.fixture_bundle_sha256,
        outcome.hidden_checker_bundle_sha256,
        outcome.runner_image_digest,
    )
    if observed != expected:
        raise ParityRunnerError("parity outcome binding is invalid")


def _execution_request(request: ParityRunRequest) -> ParityExecutionRequest:
    return ParityExecutionRequest(
        run_id=request.run_id,
        task_id=request.task_id,
        engine=request.engine,
        attempt=request.attempt,
        objective=request.objective,
        fixture_id=request.fixture_id,
        fixture_revision=request.fixture_revision,
        initial_tree_hash=request.initial_tree_hash,
        fixture_bundle_sha256=request.fixture_bundle_sha256,
        runner_image_digest=request.runner_image_digest,
        model_route_catalog_sha256=request.model_route_catalog_sha256,
        budget=request.budget,
        model_route_receipt_sha256=request.model_route_receipt_sha256,
        candidate_sha=request.candidate_sha,
        task_manifest_sha256=request.task_manifest_sha256,
    )


def _assert_bound_export(
    request: ParityExecutionRequest, result: ParityExecutionExport
) -> None:
    expected = (
        request.run_id,
        request.task_id,
        request.engine,
        request.attempt,
        request.model_route_receipt_sha256,
        request.fixture_bundle_sha256,
        request.runner_image_digest,
        request.candidate_sha,
        request.task_manifest_sha256,
        request.initial_tree_hash,
    )
    observed = (
        result.run_id,
        result.task_id,
        result.engine,
        result.attempt,
        result.model_route_receipt_sha256,
        result.fixture_bundle_sha256,
        result.runner_image_digest,
        result.candidate_sha,
        result.task_manifest_sha256,
        result.initial_tree_hash,
    )
    if observed != expected:
        raise ParityRunnerError("parity export binding is invalid")
    if (
        request.engine is ParityEngine.NATIVE_OPENCODE
        and result.engine_version != "1.18.9"
    ):
        raise ParityRunnerError("native parity runner version is invalid")


def _assert_bound_receipt(
    request: ParityCheckRequest, receipt: ParityCheckReceipt
) -> None:
    expected = (
        request.run_id,
        request.task_id,
        request.attempt,
        request.hidden_check_sha256,
        request.hidden_checker_bundle_sha256,
        request.initial_tree_hash,
        request.final_tree_hash,
        request.workspace_export.sha256,
    )
    observed = (
        receipt.run_id,
        receipt.task_id,
        receipt.attempt,
        receipt.hidden_check_sha256,
        receipt.hidden_checker_bundle_sha256,
        receipt.initial_tree_hash,
        receipt.final_tree_hash,
        receipt.workspace_export_sha256,
    )
    if observed != expected:
        raise ParityRunnerError("parity checker receipt binding is invalid")


def _outcome_from_checked_export(
    request: ParityRunRequest,
    execution: ParityExecutionExport,
    receipt: ParityCheckReceipt,
) -> ParityRunOutcome:
    failure_kind: ParityFailureKind | None = None
    if execution.timeout:
        failure_kind = ParityFailureKind.TIMEOUT
    elif execution.budget_limited:
        failure_kind = ParityFailureKind.BUDGET_LIMITED
    elif execution.stuck:
        failure_kind = ParityFailureKind.STUCK
    elif execution.manual_repair:
        failure_kind = ParityFailureKind.MANUAL_REPAIR
    elif execution.undeclared_side_effect:
        failure_kind = ParityFailureKind.UNDECLARED_SIDE_EFFECT
    elif execution.policy_violations:
        failure_kind = ParityFailureKind.POLICY_VIOLATION
    elif not receipt.hidden_checks_passed:
        failure_kind = ParityFailureKind.HIDDEN_CHECK
    elif not receipt.allowed_diff:
        failure_kind = ParityFailureKind.DIFF_POLICY
    accepted = failure_kind is None
    return ParityRunOutcome(
        run_id=request.run_id,
        task_id=request.task_id,
        engine=request.engine,
        attempt=request.attempt,
        engine_version=execution.engine_version,
        model_route_receipt_sha256=request.model_route_receipt_sha256,
        fixture_bundle_sha256=request.fixture_bundle_sha256,
        hidden_checker_bundle_sha256=request.hidden_checker_bundle_sha256,
        runner_image_digest=request.runner_image_digest,
        raw_artifact_manifest_sha256=execution.raw_artifact_manifest_sha256,
        checker_receipt_sha256=receipt.canonical_sha256(),
        candidate_sha=request.candidate_sha,
        task_manifest_sha256=request.task_manifest_sha256,
        initial_tree_hash=request.initial_tree_hash,
        final_tree_hash=execution.final_tree_hash,
        hidden_checks_passed=receipt.hidden_checks_passed,
        allowed_diff=receipt.allowed_diff,
        policy_violations=execution.policy_violations,
        timeout=execution.timeout,
        budget_limited=execution.budget_limited,
        stuck=execution.stuck,
        manual_repair=execution.manual_repair,
        undeclared_side_effect=execution.undeclared_side_effect,
        accepted=accepted,
        input_tokens=execution.input_tokens,
        output_tokens=execution.output_tokens,
        tool_calls=execution.tool_calls,
        active_seconds=execution.active_seconds,
        failure_kind=failure_kind,
    )


def _validate_argv(argv: Sequence[str]) -> None:
    if not argv or len(argv) > 32 or any(
        not item or "\0" in item or len(item) > 2048 for item in argv
    ):
        raise ValueError("runner argv is invalid")


def _invoke_json_process(
    *,
    argv: Sequence[str],
    payload: object,
    response_type: type[ResponseModel],
    timeout_seconds: int,
    failure: str,
) -> ResponseModel:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        completed = subprocess.run(
            list(argv),
            input=encoded,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout_seconds,
            shell=False,
            env=_runner_environment(),
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise ParityRunnerError(f"{failure} is unavailable") from exc
    stdout = completed.stdout.encode("utf-8")
    if completed.returncode != 0 or not stdout or len(stdout) > MAX_RUNNER_RESPONSE_BYTES:
        raise ParityRunnerError(f"{failure} failed")
    try:
        return response_type.model_validate_json(stdout)
    except ValueError as exc:
        raise ParityRunnerError(f"{failure} response is invalid") from exc


def _runner_environment() -> dict[str, str]:
    allowed = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TMP", "TEMP")
    return {key: os.environ[key] for key in allowed if os.environ.get(key)}
