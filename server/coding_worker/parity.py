from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import SAFE_ID, StrictModel


PARITY_PROTOCOL = "modelmirror-coding-parity/v1"
FROZEN_TASK_COUNT = 24
ATTEMPTS_PER_ENGINE = 3
EXPECTED_RUN_COUNT = FROZEN_TASK_COUNT * ATTEMPTS_PER_ENGINE * 2
NATIVE_BASELINE = "opencode-1.18.9"


class ParityCategory(StrEnum):
    PYTHON = "python"
    TYPESCRIPT_REACT = "typescript_react"
    REPOSITORY = "repository"
    SESSION_COLLABORATION = "session_collaboration"


class ParityEngine(StrEnum):
    NATIVE_OPENCODE = "native_opencode"
    MODELMIRROR_WORKER = "modelmirror_worker"


class ParityFailureKind(StrEnum):
    HIDDEN_CHECK = "hidden_check"
    DIFF_POLICY = "diff_policy"
    POLICY_VIOLATION = "policy_violation"
    WRONG_TREE = "wrong_tree"
    TIMEOUT = "timeout"
    BUDGET_LIMITED = "budget_limited"
    STUCK = "stuck"
    MANUAL_REPAIR = "manual_repair"
    UNDECLARED_SIDE_EFFECT = "undeclared_side_effect"
    RUNNER_ERROR = "runner_error"


class SafetyDimension(StrEnum):
    SAFETY = "safety"
    ATOMICITY = "atomicity"
    CROSS_TASK_ISOLATION = "cross_task_isolation"
    RESTART_UNIQUENESS = "restart_uniqueness"


class FrozenParityBudget(StrictModel):
    max_active_seconds: int = Field(ge=30, le=7200)
    max_turns: int = Field(ge=1, le=256)
    max_output_bytes: int = Field(ge=1024, le=64 * 1024 * 1024)


class FrozenParityTask(StrictModel):
    task_id: str
    category: ParityCategory
    objective: str = Field(min_length=1, max_length=4096)
    fixture_id: str
    fixture_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    initial_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    hidden_check_bundle_id: str
    hidden_check_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    budget: FrozenParityBudget
    safety_dimensions: tuple[SafetyDimension, ...] = ()

    @field_validator("task_id", "fixture_id", "hidden_check_bundle_id")
    @classmethod
    def validate_opaque_ids(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("parity identifiers must be opaque safe ids")
        return value

    @model_validator(mode="after")
    def unique_safety_dimensions(self) -> "FrozenParityTask":
        if len(self.safety_dimensions) != len(set(self.safety_dimensions)):
            raise ValueError("safety dimensions must be unique")
        return self


class FrozenParityManifest(StrictModel):
    schema_version: Literal[1] = 1
    baseline_engine: Literal["opencode-1.18.9"] = NATIVE_BASELINE
    repetitions: Literal[3] = ATTEMPTS_PER_ENGINE
    tasks: tuple[FrozenParityTask, ...] = Field(min_length=24, max_length=24)

    @model_validator(mode="after")
    def validate_frozen_shape(self) -> "FrozenParityManifest":
        ids = [task.task_id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("parity task ids must be unique")
        category_counts = Counter(task.category for task in self.tasks)
        if category_counts != Counter({category: 6 for category in ParityCategory}):
            raise ValueError("parity manifest requires six tasks per category")
        covered = {item for task in self.tasks for item in task.safety_dimensions}
        if covered != set(SafetyDimension):
            raise ValueError("parity manifest does not cover every safety dimension")
        return self

    def canonical_sha256(self) -> str:
        payload = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


class ParityRunOutcome(StrictModel):
    protocol: Literal["modelmirror-coding-parity/v1"] = PARITY_PROTOCOL
    run_id: str
    task_id: str
    engine: ParityEngine
    attempt: int = Field(ge=1, le=ATTEMPTS_PER_ENGINE)
    engine_version: str = Field(min_length=1, max_length=128)
    model_route_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    task_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    initial_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    final_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    hidden_checks_passed: bool
    allowed_diff: bool
    policy_violations: tuple[str, ...] = Field(default=(), max_length=32)
    timeout: bool = False
    budget_limited: bool = False
    stuck: bool = False
    manual_repair: bool = False
    undeclared_side_effect: bool = False
    accepted: bool
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    active_seconds: float = Field(ge=0)
    failure_kind: ParityFailureKind | None = None

    @field_validator("run_id", "task_id")
    @classmethod
    def validate_ids(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("parity run identifiers are invalid")
        return value

    @model_validator(mode="after")
    def validate_acceptance(self) -> "ParityRunOutcome":
        expected = (
            self.hidden_checks_passed
            and self.allowed_diff
            and not self.policy_violations
            and not self.timeout
            and not self.budget_limited
            and not self.stuck
            and not self.manual_repair
            and not self.undeclared_side_effect
        )
        if self.accepted != expected:
            raise ValueError("accepted must reflect all frozen success conditions")
        if self.accepted != (self.failure_kind is None):
            raise ValueError("failure kind must exist exactly for rejected runs")
        if not math.isfinite(self.active_seconds):
            raise ValueError("active time must be finite")
        return self

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ParityGateMetric(StrictModel):
    metric: str
    passed: bool
    observed: float | int | str
    threshold: float | int | str


class ParityDecision(StrictModel):
    passed: bool
    candidate_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    task_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    metrics: tuple[ParityGateMetric, ...]
    gap_audit: tuple[str, ...]


class ParityReport(StrictModel):
    report_id: str
    candidate_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    task_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    baseline_version: Literal["opencode-1.18.9"] = NATIVE_BASELINE
    latest_opencode_version: str = Field(min_length=1, max_length=64)
    gap_audit_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    gap_audit: tuple[str, ...]
    runs: tuple[ParityRunOutcome, ...] = Field(
        min_length=EXPECTED_RUN_COUNT, max_length=EXPECTED_RUN_COUNT
    )

    @field_validator("report_id")
    @classmethod
    def validate_report_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("report id is invalid")
        return value

    @model_validator(mode="after")
    def validate_matrix(self) -> "ParityReport":
        keys = [(run.engine, run.task_id, run.attempt) for run in self.runs]
        if len(keys) != len(set(keys)):
            raise ValueError("parity run matrix contains duplicate cells")
        tasks = {run.task_id for run in self.runs}
        if len(tasks) != FROZEN_TASK_COUNT:
            raise ValueError("parity report must contain all frozen tasks")
        expected = {
            (engine, task_id, attempt)
            for engine in ParityEngine
            for task_id in tasks
            for attempt in range(1, ATTEMPTS_PER_ENGINE + 1)
        }
        if set(keys) != expected:
            raise ValueError("parity report matrix is incomplete")
        if any(
            run.candidate_sha != self.candidate_sha
            or run.task_manifest_sha256 != self.task_manifest_sha256
            for run in self.runs
        ):
            raise ValueError("parity runs are not bound to this candidate and manifest")
        if len({run.model_route_receipt_sha256 for run in self.runs}) != 1:
            raise ValueError("parity runs must use the same controlled model route receipt")
        if any(
            run.engine is ParityEngine.NATIVE_OPENCODE
            and run.engine_version != "1.18.9"
            for run in self.runs
        ):
            raise ValueError("native baseline version differs from OpenCode 1.18.9")
        if self.latest_opencode_version != self.baseline_version and not self.gap_audit:
            raise ValueError("latest OpenCode differences require an explicit gap audit")
        return self

    def evaluate(self, manifest: FrozenParityManifest) -> ParityDecision:
        manifest_hash = manifest.canonical_sha256()
        if manifest_hash != self.task_manifest_sha256:
            raise ValueError("report does not match the frozen task manifest")
        task_by_id = {task.task_id: task for task in manifest.tasks}
        if set(task_by_id) != {run.task_id for run in self.runs}:
            raise ValueError("report tasks differ from the frozen manifest")
        for run in self.runs:
            if run.initial_tree_hash != task_by_id[run.task_id].initial_tree_hash:
                raise ValueError("run initial tree differs from the frozen fixture")

        by_engine = {
            engine: [run for run in self.runs if run.engine is engine]
            for engine in ParityEngine
        }
        rates = {
            engine: sum(run.accepted for run in runs) / len(runs)
            for engine, runs in by_engine.items()
        }
        metrics: list[ParityGateMetric] = []

        worker_rate = rates[ParityEngine.MODELMIRROR_WORKER]
        native_rate = rates[ParityEngine.NATIVE_OPENCODE]
        metrics.append(
            ParityGateMetric(
                metric="worker_accepted_rate",
                passed=worker_rate >= 0.85,
                observed=worker_rate,
                threshold=0.85,
            )
        )
        delta = worker_rate - native_rate
        metrics.append(
            ParityGateMetric(
                metric="success_rate_delta",
                passed=delta >= -0.05,
                observed=delta,
                threshold=-0.05,
            )
        )

        categories = {task.task_id: task.category for task in manifest.tasks}
        for category in ParityCategory:
            runs = [
                run
                for run in by_engine[ParityEngine.MODELMIRROR_WORKER]
                if categories[run.task_id] is category
            ]
            rate = sum(run.accepted for run in runs) / len(runs)
            metrics.append(
                ParityGateMetric(
                    metric=f"category:{category.value}",
                    passed=rate >= 0.80,
                    observed=rate,
                    threshold=0.80,
                )
            )

        per_task: dict[tuple[ParityEngine, str], int] = defaultdict(int)
        for run in self.runs:
            per_task[(run.engine, run.task_id)] += int(run.accepted)
        zero_on_native_majority = sorted(
            task_id
            for task_id in task_by_id
            if per_task[(ParityEngine.NATIVE_OPENCODE, task_id)] >= 2
            and per_task[(ParityEngine.MODELMIRROR_WORKER, task_id)] == 0
        )
        metrics.append(
            ParityGateMetric(
                metric="no_zero_on_native_majority",
                passed=not zero_on_native_majority,
                observed=",".join(zero_on_native_majority) or "none",
                threshold="none",
            )
        )

        protected_tasks = {
            task.task_id for task in manifest.tasks if task.safety_dimensions
        }
        protected_failures = [
            run.run_id
            for run in by_engine[ParityEngine.MODELMIRROR_WORKER]
            if run.task_id in protected_tasks and not run.accepted
        ]
        metrics.append(
            ParityGateMetric(
                metric="protected_cases",
                passed=not protected_failures,
                observed=len(protected_failures),
                threshold=0,
            )
        )

        native_tokens = statistics.median(
            run.total_tokens for run in by_engine[ParityEngine.NATIVE_OPENCODE]
        )
        worker_tokens = statistics.median(
            run.total_tokens for run in by_engine[ParityEngine.MODELMIRROR_WORKER]
        )
        token_ratio = _bounded_ratio(worker_tokens, native_tokens)
        metrics.append(
            ParityGateMetric(
                metric="median_token_ratio",
                passed=token_ratio <= 1.5,
                observed=token_ratio,
                threshold=1.5,
            )
        )

        native_active = statistics.median(
            run.active_seconds for run in by_engine[ParityEngine.NATIVE_OPENCODE]
        )
        worker_active = statistics.median(
            run.active_seconds for run in by_engine[ParityEngine.MODELMIRROR_WORKER]
        )
        active_ratio = _bounded_ratio(worker_active, native_active)
        metrics.append(
            ParityGateMetric(
                metric="median_active_time_ratio",
                passed=active_ratio <= 1.5,
                observed=active_ratio,
                threshold=1.5,
            )
        )
        return ParityDecision(
            passed=all(metric.passed for metric in metrics),
            candidate_sha=self.candidate_sha,
            task_manifest_sha256=self.task_manifest_sha256,
            metrics=tuple(metrics),
            gap_audit=self.gap_audit,
        )


class ParityCertification(StrictModel):
    first: ParityDecision
    second: ParityDecision

    @model_validator(mode="after")
    def require_two_consecutive_passes(self) -> "ParityCertification":
        if not self.first.passed or not self.second.passed:
            raise ValueError("both complete parity rounds must pass")
        if (
            self.first.candidate_sha != self.second.candidate_sha
            or self.first.task_manifest_sha256 != self.second.task_manifest_sha256
        ):
            raise ValueError("parity rounds must use the same candidate and manifest")
        return self


def load_frozen_manifest(path: Path) -> FrozenParityManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return FrozenParityManifest.model_validate(payload)


def _bounded_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 1.0 if numerator == 0 else math.inf
    return numerator / denominator
