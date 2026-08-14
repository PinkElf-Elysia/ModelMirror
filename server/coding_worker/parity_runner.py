from __future__ import annotations

import json
import os
import random
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from pydantic import Field, field_validator

from .contracts import SAFE_ID, StrictModel
from .parity import (
    ATTEMPTS_PER_ENGINE,
    PARITY_PROTOCOL,
    FrozenParityBudget,
    FrozenParityManifest,
    FrozenParityTask,
    ParityEngine,
    ParityRunOutcome,
)


MAX_RUNNER_RESPONSE_BYTES = 1024 * 1024


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


def run_parity_matrix(
    *,
    manifest: FrozenParityManifest,
    runners: Mapping[ParityEngine, ParityRunner],
    candidate_sha: str,
    model_route_receipt_sha256: str,
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
        for task in manifest.tasks
        for attempt in range(1, ATTEMPTS_PER_ENGINE + 1)
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


def _runner_environment() -> dict[str, str]:
    allowed = ("PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TMP", "TEMP")
    return {key: os.environ[key] for key in allowed if os.environ.get(key)}
