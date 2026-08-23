from __future__ import annotations

import base64
import hashlib
import json
from collections import Counter, defaultdict
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from .contracts import SAFE_ID, StrictModel


HARNESS_PROTOCOL = "modelmirror-coding-harness/v3"
HARNESS_SCHEMA_VERSION = 3
HARBOR_VERSION = "0.21.0"
NATIVE_OPENCODE_VERSION = "1.18.9"
ATIF_SCHEMA_VERSION = "ATIF-v1.7"
CODING_WORKER_HARNESS_CODE_FILES = (
    "__init__.py",
    "acp_driver.py",
    "adapters.py",
    "api.py",
    "broker_mcp.py",
    "broker_rpc.py",
    "changeset.py",
    "claude_provider.py",
    "codex_app_server_driver.py",
    "code_intelligence.py",
    "contracts.py",
    "crypto.py",
    "egress_proxy.py",
    "evaluation.py",
    "evaluation_driver.py",
    "evaluation_loader.py",
    "evaluation_sidecar.py",
    "evidence.py",
    "executor.py",
    "harness_contracts.py",
    "harness_driver.py",
    "harness_protocol.py",
    "harness_v3.py",
    "network_policy.py",
    "opencode_provider.py",
    "parity.py",
    "parity_runner.py",
    "parity_sidecar.py",
    "ports.py",
    "process_manager.py",
    "provider.py",
    "provider_rpc.py",
    "runtime.py",
    "sdk.py",
    "service.py",
    "shell_sandbox.py",
    "sidecar.py",
    "source_adapters.py",
    "store.py",
    "tool_broker.py",
    "unified_patch.py",
    "workspace.py",
)
SERVER_HARNESS_CODE_FILES = CODING_WORKER_HARNESS_CODE_FILES
PROVIDER_HARNESS_CODE_FILES = CODING_WORKER_HARNESS_CODE_FILES


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def harness_code_bundle_sha256(package_root: Path, names: tuple[str, ...]) -> str:
    """Hash the exact installed Python sources that implement a Harness boundary."""

    records: list[tuple[str, bytes]] = []
    for name in sorted(names, key=lambda value: (value.casefold(), value)):
        path = package_root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Harness code source is unavailable: {name}")
        records.append((name, path.read_bytes()))
    digest = hashlib.sha256()
    for name, content in records:
        encoded = name.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


class HarnessCategory(StrEnum):
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    REPOSITORY = "repository"
    SESSION = "session"


class HarnessFailureStage(StrEnum):
    SOURCE_ADMISSION = "source_admission"
    SCHEDULER = "scheduler"
    PROVIDER_TRANSPORT = "provider_transport"
    PROVIDER_PROTOCOL = "provider_protocol"
    TOOL_VALIDATION = "tool_validation"
    INTERACTION = "interaction"
    EXECUTOR = "executor"
    WORKSPACE_CAS = "workspace_cas"
    VISIBLE_ACCEPTANCE = "visible_acceptance"
    SEALED_CHECKER = "sealed_checker"
    POLICY = "policy"
    BUDGET = "budget"
    HARNESS = "harness"
    AGENT_OUTCOME = "agent_outcome"


PLATFORM_COORDINATION_STAGES = frozenset(
    {
        HarnessFailureStage.SOURCE_ADMISSION,
        HarnessFailureStage.SCHEDULER,
        HarnessFailureStage.PROVIDER_TRANSPORT,
        HarnessFailureStage.PROVIDER_PROTOCOL,
        HarnessFailureStage.INTERACTION,
        HarnessFailureStage.EXECUTOR,
        HarnessFailureStage.WORKSPACE_CAS,
        HarnessFailureStage.HARNESS,
    }
)


class HarnessFixtureFile(StrictModel):
    path: str = Field(min_length=1, max_length=1024)
    content_base64: str = Field(max_length=12 * 1024 * 1024)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    executable: bool = False
    binary_canary: bool = False

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        pure = PurePosixPath(value)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts or pure.parts[0] == ".git":
            raise ValueError("harness fixture path is unsafe")
        return pure.as_posix()

    @model_validator(mode="after")
    def validate_content(self) -> "HarnessFixtureFile":
        try:
            content = base64.b64decode(self.content_base64, validate=True)
        except ValueError as exc:
            raise ValueError("harness fixture content is invalid") from exc
        if hashlib.sha256(content).hexdigest() != self.sha256:
            raise ValueError("harness fixture content hash is invalid")
        return self

    def content(self) -> bytes:
        return base64.b64decode(self.content_base64, validate=True)


class HarnessVisibleCheck(StrictModel):
    check_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    argv: tuple[str, ...] = Field(min_length=1, max_length=64)
    timeout_seconds: int = Field(default=300, ge=1, le=1800)


class HarnessFixture(StrictModel):
    task_id: str
    category: HarnessCategory
    source_id: str
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    initial_tree_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    task_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    instruction_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    environment_spec_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    solution_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verifier_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    task_package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scenario_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    near_miss_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    files: tuple[HarnessFixtureFile, ...] = Field(min_length=5, max_length=512)
    visible_checks: tuple[HarnessVisibleCheck, ...] = Field(min_length=1, max_length=16)
    required_modified_files: int = Field(default=2, ge=2, le=64)
    long_context: bool = False

    @field_validator("task_id", "source_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("harness fixture id is invalid")
        return value

    @model_validator(mode="after")
    def validate_shape(self) -> "HarnessFixture":
        paths = [item.path for item in self.files]
        check_ids = [item.check_id for item in self.visible_checks]
        if len(paths) != len(set(paths)) or len(check_ids) != len(set(check_ids)):
            raise ValueError("harness fixture entries must be unique")
        if self.long_context and len(self.files) < 30:
            raise ValueError("long-context harness fixture is too small")
        if not any(item.binary_canary for item in self.files):
            raise ValueError("harness fixture must contain a binary protection canary")
        if self.revision != self.canonical_revision():
            raise ValueError("harness fixture revision is invalid")
        if self.initial_tree_hash != self.canonical_tree_hash():
            raise ValueError("harness fixture tree hash is invalid")
        return self

    def canonical_revision(self) -> str:
        return _canonical_sha256(
            {
                "task_id": self.task_id,
                "source_id": self.source_id,
                "task_manifest_sha256": self.task_manifest_sha256,
                "instruction_sha256": self.instruction_sha256,
                "environment_spec_sha256": self.environment_spec_sha256,
                "solution_bundle_sha256": self.solution_bundle_sha256,
                "verifier_bundle_sha256": self.verifier_bundle_sha256,
                "task_package_sha256": self.task_package_sha256,
                "scenario_sha256": self.scenario_sha256,
                "near_miss_sha256": self.near_miss_sha256,
                "files": [item.model_dump(mode="json") for item in self.files],
                "visible_checks": [
                    item.model_dump(mode="json") for item in self.visible_checks
                ],
            }
        )

    def canonical_tree_hash(self) -> str:
        digest = hashlib.sha256()
        for entry in sorted(self.files, key=lambda item: item.path):
            path = entry.path.encode("utf-8")
            content = entry.content()
            digest.update(len(path).to_bytes(4, "big"))
            digest.update(path)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(hashlib.sha256(content).digest())
        return digest.hexdigest()


class HarnessFixtureBundle(StrictModel):
    protocol: Literal["modelmirror-coding-harness/v3"] = HARNESS_PROTOCOL
    schema_version: Literal[3] = HARNESS_SCHEMA_VERSION
    harbor_version: Literal["0.21.0"] = HARBOR_VERSION
    native_opencode_version: Literal["1.18.9"] = NATIVE_OPENCODE_VERSION
    report_mode: Literal["calibration"] = "calibration"
    fixtures: tuple[HarnessFixture, ...] = Field(min_length=12, max_length=12)

    @model_validator(mode="after")
    def validate_matrix(self) -> "HarnessFixtureBundle":
        task_ids = [item.task_id for item in self.fixtures]
        source_bindings = [(item.source_id, item.revision) for item in self.fixtures]
        if len(task_ids) != len(set(task_ids)) or len(source_bindings) != len(set(source_bindings)):
            raise ValueError("harness fixture identities must be unique")
        if Counter(item.category for item in self.fixtures) != {
            HarnessCategory.PYTHON: 3,
            HarnessCategory.TYPESCRIPT: 3,
            HarnessCategory.REPOSITORY: 3,
            HarnessCategory.SESSION: 3,
        }:
            raise ValueError("harness fixture category matrix is incomplete")
        return self

    def canonical_sha256(self) -> str:
        return _canonical_sha256(self.model_dump(mode="json"))

    def source_snapshots(self) -> dict[tuple[str, str], dict[str, bytes]]:
        return {
            (fixture.source_id, fixture.revision): {
                entry.path: entry.content() for entry in fixture.files
            }
            for fixture in self.fixtures
        }


class HarnessOperationFact(StrictModel):
    evidence_id: str
    operation_id: str
    intent_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: Literal["prepared", "running", "unknown", "completed", "failed"]
    side_effecting: bool

    @field_validator("evidence_id", "operation_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("harness operation evidence id is invalid")
        return value


class HarnessInteractionFact(StrictModel):
    evidence_id: str
    interaction_id: str
    kind: Literal["approval", "question", "subtask"]
    state: Literal["pending", "resolved", "rejected", "cancelled", "expired"]

    @field_validator("evidence_id", "interaction_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("harness interaction evidence id is invalid")
        return value


class HarnessCoordinationFact(StrictModel):
    evidence_id: str
    stage: HarnessFailureStage
    failed: bool

    @field_validator("evidence_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("harness coordination evidence id is invalid")
        return value


class HarnessFactSet(StrictModel):
    export_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    trajectory_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    complete: Literal[True] = True
    operations: tuple[HarnessOperationFact, ...] = ()
    interactions: tuple[HarnessInteractionFact, ...] = ()
    coordination: tuple[HarnessCoordinationFact, ...] = ()

    @model_validator(mode="after")
    def validate_unique_facts(self) -> "HarnessFactSet":
        operation_ids = [item.operation_id for item in self.operations]
        interaction_ids = [item.interaction_id for item in self.interactions]
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("harness operation facts must be canonical")
        if len(interaction_ids) != len(set(interaction_ids)):
            raise ValueError("harness interaction facts must be canonical")
        return self


class HarnessDiagnostics(StrictModel):
    platform_coordination_failures: int = Field(ge=0)
    duplicate_side_effects: int = Field(ge=0)
    unsettled_operations: int = Field(ge=0)
    orphaned_interactions: int = Field(ge=0)
    evidence: dict[
        Literal[
            "platform_coordination_failures",
            "duplicate_side_effects",
            "unsettled_operations",
            "orphaned_interactions",
        ],
        tuple[str, ...],
    ]


class HarnessArtifactSummary(StrictModel):
    artifact_id: Literal[
        "harbor_result",
        "trajectory",
        "workspace",
        "worker_facts",
        "worker_ledger",
        "native_ledger",
        "agent_log",
    ]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0, le=2 * 1024 * 1024 * 1024)


def derive_diagnostics(facts: HarnessFactSet) -> HarnessDiagnostics:
    coordination = tuple(
        item.evidence_id
        for item in facts.coordination
        if item.failed and item.stage in PLATFORM_COORDINATION_STAGES
    )
    completed_by_intent: dict[str, list[HarnessOperationFact]] = defaultdict(list)
    for operation in facts.operations:
        if operation.side_effecting and operation.state == "completed":
            completed_by_intent[operation.intent_sha256].append(operation)
    duplicates = tuple(
        operation.evidence_id
        for operations in completed_by_intent.values()
        for operation in operations[1:]
    )
    unsettled = tuple(
        item.evidence_id
        for item in facts.operations
        if item.state in {"prepared", "running", "unknown"}
    )
    orphaned = tuple(
        item.evidence_id for item in facts.interactions if item.state == "pending"
    )
    evidence = {
        "platform_coordination_failures": coordination,
        "duplicate_side_effects": duplicates,
        "unsettled_operations": unsettled,
        "orphaned_interactions": orphaned,
    }
    return HarnessDiagnostics(
        platform_coordination_failures=len(coordination),
        duplicate_side_effects=len(duplicates),
        unsettled_operations=len(unsettled),
        orphaned_interactions=len(orphaned),
        evidence=evidence,
    )


def aggregate_run_diagnostics(
    runs: tuple["HarnessRunRecord", ...],
) -> HarnessDiagnostics:
    """Aggregate independent trials without treating repeated intents as replay."""

    keys = (
        "platform_coordination_failures",
        "duplicate_side_effects",
        "unsettled_operations",
        "orphaned_interactions",
    )
    evidence = {
        key: tuple(
            evidence_id
            for run in runs
            for evidence_id in run.diagnostics.evidence[key]
        )
        for key in keys
    }
    return HarnessDiagnostics(
        platform_coordination_failures=sum(
            run.diagnostics.platform_coordination_failures for run in runs
        ),
        duplicate_side_effects=sum(
            run.diagnostics.duplicate_side_effects for run in runs
        ),
        unsettled_operations=sum(
            run.diagnostics.unsettled_operations for run in runs
        ),
        orphaned_interactions=sum(
            run.diagnostics.orphaned_interactions for run in runs
        ),
        evidence=evidence,
    )


class HarnessRunRecord(StrictModel):
    run_id: str
    task_id: str
    engine: Literal["native-opencode", "modelmirror-worker"]
    attempt: int = Field(ge=1, le=5)
    candidate_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    runner_image_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    task_package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verifier_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    harbor_task_checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    route_binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sealed_checker_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    accepted: bool
    failure_stage: HarnessFailureStage | None = None
    duration_seconds: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    artifacts: tuple[HarnessArtifactSummary, ...] = Field(min_length=2, max_length=7)
    facts: HarnessFactSet
    diagnostics: HarnessDiagnostics

    @field_validator("run_id", "task_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if SAFE_ID.fullmatch(value) is None:
            raise ValueError("harness run id is invalid")
        return value

    @model_validator(mode="after")
    def validate_diagnostics(self) -> "HarnessRunRecord":
        if self.diagnostics != derive_diagnostics(self.facts):
            raise ValueError("harness diagnostics are not fact-derived")
        if self.accepted and any(item.failed for item in self.facts.coordination):
            raise ValueError("accepted harness run contains a failed outcome fact")
        if self.accepted and any(
            (
                self.diagnostics.platform_coordination_failures,
                self.diagnostics.duplicate_side_effects,
                self.diagnostics.unsettled_operations,
                self.diagnostics.orphaned_interactions,
            )
        ):
            raise ValueError("accepted harness run contains an unsettled platform fact")
        if self.accepted == (self.failure_stage is not None):
            raise ValueError("harness run outcome is inconsistent")
        artifact_ids = [item.artifact_id for item in self.artifacts]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("harness run artifacts must be unique")
        required = {"harbor_result", "workspace"}
        if not required.issubset(artifact_ids):
            raise ValueError("harness run omitted required raw artifacts")
        if self.accepted and "trajectory" not in artifact_ids:
            raise ValueError("accepted harness run omitted its trajectory")
        if self.accepted and self.engine == "modelmirror-worker":
            if not {"worker_facts", "worker_ledger"}.issubset(artifact_ids):
                raise ValueError("accepted worker run omitted its fact source")
        if self.accepted and self.engine == "native-opencode":
            if "native_ledger" not in artifact_ids:
                raise ValueError("accepted native run omitted its fact source")
        return self


class HarnessReport(StrictModel):
    protocol: Literal["modelmirror-coding-harness/v3"] = HARNESS_PROTOCOL
    schema_version: Literal[3] = HARNESS_SCHEMA_VERSION
    report_mode: Literal["calibration", "certifying"]
    candidate_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    fixture_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    sealed_checker_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runner_image_sha256: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    route_binding_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runs: tuple[HarnessRunRecord, ...] = Field(min_length=1, max_length=512)
    diagnostics: HarnessDiagnostics

    @model_validator(mode="after")
    def validate_report(self) -> "HarnessReport":
        if any(
            run.route_binding_sha256 != self.route_binding_sha256
            for run in self.runs
        ):
            raise ValueError("harness report route binding does not match its runs")
        if any(
            run.sealed_checker_sha256 != self.sealed_checker_sha256
            for run in self.runs
        ):
            raise ValueError("harness report sealed checker does not match its runs")
        if any(run.candidate_sha != self.candidate_sha for run in self.runs):
            raise ValueError("harness report candidate does not match its runs")
        if any(
            run.runner_image_sha256 != self.runner_image_sha256
            for run in self.runs
        ):
            raise ValueError("harness report runner image does not match its runs")
        if self.diagnostics != aggregate_run_diagnostics(self.runs):
            raise ValueError("harness report diagnostics are not fact-derived")
        return self


def build_harness_report(**payload: Any) -> HarnessReport:
    runs = tuple(HarnessRunRecord.model_validate(item) for item in payload.pop("runs"))
    return HarnessReport(
        **payload,
        runs=runs,
        diagnostics=aggregate_run_diagnostics(runs),
    )


def load_harness_fixture_bundle(path: Path) -> HarnessFixtureBundle:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return HarnessFixtureBundle.model_validate(payload)


def report_eligibility(payload: dict[str, Any]) -> Literal[
    "structural_only", "calibration", "certifying"
]:
    protocol = payload.get("protocol")
    if protocol in {None, "modelmirror-coding-parity/v1", "modelmirror-coding-parity/v2"}:
        return "structural_only"
    report = HarnessReport.model_validate(payload)
    return report.report_mode
