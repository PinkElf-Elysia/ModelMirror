from __future__ import annotations

import hashlib
import json
import math
import statistics
import base64
from collections import Counter, defaultdict
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import SAFE_ID, StrictModel


PARITY_PROTOCOL_V1 = "modelmirror-coding-parity/v1"
PARITY_PROTOCOL = "modelmirror-coding-parity/v2"
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


class PublicParityFile(StrictModel):
    path: str = Field(min_length=1, max_length=512)
    encoding: Literal["utf-8", "base64"] = "utf-8"
    content: str = Field(max_length=262_144)

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        candidate = PurePosixPath(value)
        if (
            candidate.is_absolute()
            or "\\" in value
            or any(part in {"", ".", "..", ".git"} for part in candidate.parts)
        ):
            raise ValueError("parity fixture path is unsafe")
        return value

    def content_bytes(self) -> bytes:
        if self.encoding == "utf-8":
            return self.content.encode("utf-8")
        try:
            return base64.b64decode(self.content, validate=True)
        except ValueError as exc:
            raise ValueError("parity fixture base64 content is invalid") from exc


class PublicParityCheck(StrictModel):
    check_id: str
    argv: tuple[str, ...] = Field(min_length=1, max_length=16)
    cwd: str = "."

    @field_validator("check_id")
    @classmethod
    def validate_check_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("parity visible check id is invalid")
        return value

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or "\0" in item or len(item) > 512 for item in value):
            raise ValueError("parity visible check command is invalid")
        return value

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, value: str) -> str:
        if value == ".":
            return value
        candidate = PurePosixPath(value)
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            raise ValueError("parity visible check cwd is unsafe")
        return value


class PublicParityFixture(StrictModel):
    fixture_id: str
    fixture_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    initial_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    files: tuple[PublicParityFile, ...] = Field(min_length=1, max_length=128)
    visible_checks: tuple[PublicParityCheck, ...] = Field(min_length=1, max_length=16)

    @field_validator("fixture_id")
    @classmethod
    def validate_fixture_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("parity fixture id is invalid")
        return value

    @model_validator(mode="after")
    def validate_fixture_hashes(self) -> "PublicParityFixture":
        paths = [item.path for item in self.files]
        checks = [item.check_id for item in self.visible_checks]
        if len(paths) != len(set(paths)) or len(checks) != len(set(checks)):
            raise ValueError("parity fixture entries must be unique")
        if self.fixture_revision != self.canonical_revision():
            raise ValueError("parity fixture revision is invalid")
        if self.initial_tree_hash != self.canonical_tree_hash():
            raise ValueError("parity fixture tree hash is invalid")
        return self

    def canonical_revision(self) -> str:
        payload = {
            "fixture_id": self.fixture_id,
            "files": [item.model_dump(mode="json") for item in self.files],
            "visible_checks": [
                item.model_dump(mode="json") for item in self.visible_checks
            ],
        }
        return _canonical_sha256(payload)

    def canonical_tree_hash(self) -> str:
        digest = hashlib.sha256()
        for entry in sorted(self.files, key=lambda item: item.path):
            relative = entry.path.encode("utf-8")
            content = entry.content_bytes()
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(hashlib.sha256(content).digest())
        return digest.hexdigest()


class PublicParityFixtureBundle(StrictModel):
    schema_version: Literal[1] = 1
    fixtures: tuple[PublicParityFixture, ...] = Field(min_length=24, max_length=24)

    @model_validator(mode="after")
    def validate_bundle(self) -> "PublicParityFixtureBundle":
        ids = [item.fixture_id for item in self.fixtures]
        if len(ids) != len(set(ids)):
            raise ValueError("parity fixture ids must be unique")
        return self

    def canonical_sha256(self) -> str:
        return _canonical_sha256(self.model_dump(mode="json"))


class ParityRunnerImages(StrictModel):
    native_opencode: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    modelmirror_worker: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    checker: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    controller: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")

    def canonical_sha256(self) -> str:
        return _canonical_sha256(self.model_dump(mode="json"))


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
    protocol: Literal["modelmirror-coding-parity/v2"] = PARITY_PROTOCOL
    schema_version: Literal[2] = 2
    baseline_engine: Literal["opencode-1.18.9"] = NATIVE_BASELINE
    repetitions: Literal[3] = ATTEMPTS_PER_ENGINE
    fixture_bundle_id: str
    fixture_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    hidden_checker_bundle_id: str
    hidden_checker_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_route: str
    model_route_catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runner_images: ParityRunnerImages
    tasks: tuple[FrozenParityTask, ...] = Field(min_length=24, max_length=24)

    @field_validator(
        "fixture_bundle_id", "hidden_checker_bundle_id", "model_route"
    )
    @classmethod
    def validate_manifest_ids(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None and value != "coding/default":
            raise ValueError("parity manifest identifier is invalid")
        return value

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
        return _canonical_sha256(self.model_dump(mode="json"))


class ParityRunOutcome(StrictModel):
    protocol: Literal["modelmirror-coding-parity/v2"] = PARITY_PROTOCOL
    run_id: str
    task_id: str
    engine: ParityEngine
    attempt: int = Field(ge=1, le=ATTEMPTS_PER_ENGINE)
    engine_version: str = Field(min_length=1, max_length=128)
    model_route_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fixture_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    hidden_checker_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runner_image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    raw_artifact_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    checker_receipt_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
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
    round_id: str
    candidate_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    task_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fixture_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    hidden_checker_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_route_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runner_images_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    metrics: tuple[ParityGateMetric, ...]
    gap_audit: tuple[str, ...]


class ParityReport(StrictModel):
    protocol: Literal["modelmirror-coding-parity/v2"] = PARITY_PROTOCOL
    report_id: str
    round_id: str
    candidate_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    task_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fixture_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    hidden_checker_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_route_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runner_images: ParityRunnerImages
    raw_artifact_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    baseline_version: Literal["opencode-1.18.9"] = NATIVE_BASELINE
    latest_opencode_version: str = Field(min_length=1, max_length=64)
    latest_opencode_audit_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    gap_audit_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    gap_audit: tuple[str, ...]
    platform_coordination_failures: int = Field(ge=0)
    duplicate_side_effects: int = Field(ge=0)
    unsettled_operations: int = Field(ge=0)
    orphaned_interactions: int = Field(ge=0)
    runs: tuple[ParityRunOutcome, ...] = Field(
        min_length=EXPECTED_RUN_COUNT, max_length=EXPECTED_RUN_COUNT
    )

    @field_validator("report_id", "round_id")
    @classmethod
    def validate_report_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("report id is invalid")
        return value

    @model_validator(mode="after")
    def validate_matrix(self) -> "ParityReport":
        if any(run.checker_receipt_sha256 is None for run in self.runs):
            raise ValueError("parity certification requires sealed checker receipts")
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
            or run.fixture_bundle_sha256 != self.fixture_bundle_sha256
            or run.hidden_checker_bundle_sha256
            != self.hidden_checker_bundle_sha256
            or run.model_route_receipt_sha256
            != self.model_route_receipt_sha256
            for run in self.runs
        ):
            raise ValueError("parity runs are not bound to this report")
        if len({run.model_route_receipt_sha256 for run in self.runs}) != 1:
            raise ValueError("parity runs must use the same controlled model route receipt")
        if any(
            run.engine is ParityEngine.NATIVE_OPENCODE
            and run.engine_version != "1.18.9"
            for run in self.runs
        ):
            raise ValueError("native baseline version differs from OpenCode 1.18.9")
        expected_images = {
            ParityEngine.NATIVE_OPENCODE: self.runner_images.native_opencode,
            ParityEngine.MODELMIRROR_WORKER: self.runner_images.modelmirror_worker,
        }
        if any(
            run.runner_image_digest != expected_images[run.engine]
            for run in self.runs
        ):
            raise ValueError("parity run image binding is invalid")
        if self.raw_artifact_manifest_sha256 != self.artifact_ledger_sha256():
            raise ValueError("parity artifact ledger binding is invalid")
        if (
            self.latest_opencode_version
            != self.baseline_version.removeprefix("opencode-")
            and not self.gap_audit
        ):
            raise ValueError("latest OpenCode differences require an explicit gap audit")
        return self

    def artifact_ledger_sha256(self) -> str:
        return parity_artifact_ledger_sha256(self.runs)

    def evaluate(self, manifest: FrozenParityManifest) -> ParityDecision:
        manifest_hash = manifest.canonical_sha256()
        if manifest_hash != self.task_manifest_sha256:
            raise ValueError("report does not match the frozen task manifest")
        task_by_id = {task.task_id: task for task in manifest.tasks}
        if set(task_by_id) != {run.task_id for run in self.runs}:
            raise ValueError("report tasks differ from the frozen manifest")
        if (
            self.fixture_bundle_sha256 != manifest.fixture_bundle_sha256
            or self.hidden_checker_bundle_sha256
            != manifest.hidden_checker_bundle_sha256
            or self.runner_images != manifest.runner_images
        ):
            raise ValueError("report assets differ from the frozen manifest")
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

        coordination_counts = {
            "platform_coordination_failures": self.platform_coordination_failures,
            "duplicate_side_effects": self.duplicate_side_effects,
            "unsettled_operations": self.unsettled_operations,
            "orphaned_interactions": self.orphaned_interactions,
        }
        for metric, value in coordination_counts.items():
            metrics.append(
                ParityGateMetric(
                    metric=metric,
                    passed=value == 0,
                    observed=value,
                    threshold=0,
                )
            )

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
            round_id=self.round_id,
            candidate_sha=self.candidate_sha,
            task_manifest_sha256=self.task_manifest_sha256,
            fixture_bundle_sha256=self.fixture_bundle_sha256,
            hidden_checker_bundle_sha256=self.hidden_checker_bundle_sha256,
            model_route_receipt_sha256=self.model_route_receipt_sha256,
            runner_images_sha256=self.runner_images.canonical_sha256(),
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
        if self.first.round_id == self.second.round_id:
            raise ValueError("parity rounds must be independently identified")
        if (
            self.first.candidate_sha != self.second.candidate_sha
            or self.first.task_manifest_sha256 != self.second.task_manifest_sha256
            or self.first.fixture_bundle_sha256
            != self.second.fixture_bundle_sha256
            or self.first.hidden_checker_bundle_sha256
            != self.second.hidden_checker_bundle_sha256
            or self.first.model_route_receipt_sha256
            != self.second.model_route_receipt_sha256
            or self.first.runner_images_sha256
            != self.second.runner_images_sha256
        ):
            raise ValueError("parity rounds must use the same certified bindings")
        return self


class LegacyParityReport(StrictModel):
    protocol: Literal["modelmirror-coding-parity/v1"] = PARITY_PROTOCOL_V1
    report_id: str
    candidate_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    task_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    read_only: Literal[True] = True


def load_frozen_manifest(path: Path) -> FrozenParityManifest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return FrozenParityManifest.model_validate(payload)


def load_public_fixture_bundle(path: Path) -> PublicParityFixtureBundle:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return PublicParityFixtureBundle.model_validate(payload)


def load_parity_report(path: Path) -> ParityReport | LegacyParityReport:
    encoded = path.read_bytes()
    payload = json.loads(encoded.decode("utf-8"))
    if payload.get("protocol") == PARITY_PROTOCOL:
        return ParityReport.model_validate(payload)
    runs = payload.get("runs")
    if payload.get("protocol") in {None, PARITY_PROTOCOL_V1} and isinstance(
        runs, list
    ):
        return LegacyParityReport(
            report_id=str(payload.get("report_id", "legacy_report")),
            candidate_sha=str(payload["candidate_sha"]),
            task_manifest_sha256=str(payload["task_manifest_sha256"]),
            source_sha256=hashlib.sha256(encoded).hexdigest(),
        )
    raise ValueError("parity report protocol is unsupported")


def parity_artifact_ledger_sha256(
    runs: tuple[ParityRunOutcome, ...] | list[ParityRunOutcome],
) -> str:
    return _canonical_sha256(
        [
            {
                "run_id": run.run_id,
                "artifact_manifest_sha256": run.raw_artifact_manifest_sha256,
            }
            for run in sorted(runs, key=lambda item: item.run_id)
        ]
    )


def _bounded_ratio(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 1.0 if numerator == 0 else math.inf
    return numerator / denominator


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
