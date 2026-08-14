from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from server.coding_worker.contracts import StrictModel
from server.coding_worker.parity import (
    PARITY_PROTOCOL,
    FrozenParityManifest,
    LegacyParityReport,
    ParityCertification,
    ParityDecision,
    ParityEngine,
    ParityReport,
    ParityRunOutcome,
    ParityRunnerImages,
    PublicParityFixtureBundle,
    load_frozen_manifest,
    load_parity_report,
    load_public_fixture_bundle,
    parity_artifact_ledger_sha256,
)
from server.coding_worker.parity_runner import (
    ParityRunRequest,
    ParityRunner,
    SubprocessParityRunner,
    run_parity_matrix,
    run_parity_smoke,
)


OFFICIAL_LATEST_SOURCE = "https://github.com/anomalyco/opencode/releases/latest"
MAX_LATEST_AUDIT_AGE_SECONDS = 86_400


class RunnerLock(StrictModel):
    runner_images: ParityRunnerImages


class RouteCatalogLock(StrictModel):
    model_route: str
    catalog_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class RunnerConfig(StrictModel):
    engine: ParityEngine
    image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    argv: tuple[str, ...] = Field(min_length=1, max_length=32)
    timeout_seconds: int = Field(default=7200, ge=30, le=14_400)

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item or "\0" in item or len(item) > 2048 for item in value):
            raise ValueError("parity runner command is invalid")
        return value


class LatestOpenCodeAudit(StrictModel):
    source: Literal["https://github.com/anomalyco/opencode/releases/latest"]
    queried_at: float = Field(gt=0)
    version: str = Field(min_length=1, max_length=64)
    gap_audit: tuple[str, ...] = Field(max_length=128)


class ParityRoundResult(StrictModel):
    protocol: Literal["modelmirror-coding-parity/v2"] = PARITY_PROTOCOL
    round_id: str
    candidate_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    task_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fixture_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    hidden_checker_bundle_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_route_receipt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    runner_images: ParityRunnerImages
    platform_coordination_failures: int = Field(default=0, ge=0)
    duplicate_side_effects: int = Field(default=0, ge=0)
    unsettled_operations: int = Field(default=0, ge=0)
    orphaned_interactions: int = Field(default=0, ge=0)
    runs: tuple[ParityRunOutcome, ...]


class DeterministicFakeRunner(ParityRunner):
    def __init__(self, engine: ParityEngine) -> None:
        self._engine = engine

    @property
    def engine(self) -> ParityEngine:
        return self._engine

    def execute(self, request: ParityRunRequest) -> ParityRunOutcome:
        artifact_sha = _canonical_sha256(
            {
                "run_id": request.run_id,
                "fixture": request.fixture_revision,
                "tree": request.initial_tree_hash,
            }
        )
        return ParityRunOutcome(
            run_id=request.run_id,
            task_id=request.task_id,
            engine=request.engine,
            attempt=request.attempt,
            engine_version=(
                "1.18.9"
                if request.engine is ParityEngine.NATIVE_OPENCODE
                else "fake-worker"
            ),
            model_route_receipt_sha256=request.model_route_receipt_sha256,
            fixture_bundle_sha256=request.fixture_bundle_sha256,
            hidden_checker_bundle_sha256=request.hidden_checker_bundle_sha256,
            runner_image_digest=request.runner_image_digest,
            raw_artifact_manifest_sha256=artifact_sha,
            candidate_sha=request.candidate_sha,
            task_manifest_sha256=request.task_manifest_sha256,
            initial_tree_hash=request.initial_tree_hash,
            final_tree_hash=request.initial_tree_hash,
            hidden_checks_passed=True,
            allowed_diff=True,
            accepted=True,
            input_tokens=1,
            output_tokens=1,
            tool_calls=0,
            active_seconds=0.01,
        )


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.building")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _validate_assets(
    *,
    manifest: FrozenParityManifest,
    fixtures: PublicParityFixtureBundle,
    checker_bundle: Path,
    runner_lock: RunnerLock,
    route_lock: RouteCatalogLock,
) -> dict[str, str | int]:
    if fixtures.canonical_sha256() != manifest.fixture_bundle_sha256:
        raise ValueError("fixture bundle digest differs from manifest")
    fixture_by_id = {fixture.fixture_id: fixture for fixture in fixtures.fixtures}
    if set(fixture_by_id) != {task.fixture_id for task in manifest.tasks}:
        raise ValueError("fixture bundle task set differs from manifest")
    for task in manifest.tasks:
        fixture = fixture_by_id[task.fixture_id]
        if (
            fixture.fixture_revision != task.fixture_revision
            or fixture.initial_tree_hash != task.initial_tree_hash
        ):
            raise ValueError("fixture binding differs from manifest")
    if hashlib.sha256(checker_bundle.read_bytes()).hexdigest() != (
        manifest.hidden_checker_bundle_sha256
    ):
        raise ValueError("sealed checker bundle digest differs from manifest")
    if runner_lock.runner_images != manifest.runner_images:
        raise ValueError("runner image lock differs from manifest")
    if (
        route_lock.model_route != manifest.model_route
        or route_lock.catalog_sha256 != manifest.model_route_catalog_sha256
    ):
        raise ValueError("model route catalog lock differs from manifest")
    return {
        "protocol": PARITY_PROTOCOL,
        "tasks": len(manifest.tasks),
        "manifest_sha256": manifest.canonical_sha256(),
        "fixture_bundle_sha256": fixtures.canonical_sha256(),
        "checker_bundle_sha256": manifest.hidden_checker_bundle_sha256,
        "runner_images_sha256": manifest.runner_images.canonical_sha256(),
    }


def _runner_from_config(
    path: Path, engine: ParityEngine, manifest: FrozenParityManifest
) -> SubprocessParityRunner:
    config = RunnerConfig.model_validate_json(path.read_text(encoding="utf-8"))
    expected_digest = (
        manifest.runner_images.native_opencode
        if engine is ParityEngine.NATIVE_OPENCODE
        else manifest.runner_images.modelmirror_worker
    )
    if config.engine is not engine or config.image_digest != expected_digest:
        raise ValueError("runner configuration binding is invalid")
    return SubprocessParityRunner(
        engine=engine,
        argv=config.argv,
        timeout_seconds=config.timeout_seconds,
    )


def _round_result(
    *,
    round_id: str,
    manifest: FrozenParityManifest,
    candidate_sha: str,
    route_receipt_sha256: str,
    runs: tuple[ParityRunOutcome, ...],
) -> ParityRoundResult:
    return ParityRoundResult(
        round_id=round_id,
        candidate_sha=candidate_sha,
        task_manifest_sha256=manifest.canonical_sha256(),
        fixture_bundle_sha256=manifest.fixture_bundle_sha256,
        hidden_checker_bundle_sha256=manifest.hidden_checker_bundle_sha256,
        model_route_receipt_sha256=route_receipt_sha256,
        runner_images=manifest.runner_images,
        runs=runs,
    )


def _command_validate(args: argparse.Namespace) -> int:
    result = _validate_assets(
        manifest=load_frozen_manifest(args.manifest),
        fixtures=load_public_fixture_bundle(args.fixtures),
        checker_bundle=args.checker_bundle,
        runner_lock=RunnerLock.model_validate_json(
            args.runner_images_lock.read_text(encoding="utf-8")
        ),
        route_lock=RouteCatalogLock.model_validate_json(
            args.route_catalog_lock.read_text(encoding="utf-8")
        ),
    )
    print(json.dumps(result, sort_keys=True))
    return 0


def _command_smoke(args: argparse.Namespace) -> int:
    manifest = load_frozen_manifest(args.manifest)
    fixtures = load_public_fixture_bundle(args.fixtures)
    if fixtures.canonical_sha256() != manifest.fixture_bundle_sha256:
        raise ValueError("fixture bundle digest differs from manifest")
    runners = {
        engine: DeterministicFakeRunner(engine) for engine in ParityEngine
    }
    runs = run_parity_smoke(
        manifest=manifest,
        runners=runners,
        candidate_sha=args.candidate_sha,
        model_route_receipt_sha256=args.route_receipt_sha256,
    )
    payload = _round_result(
        round_id=args.round_id,
        manifest=manifest,
        candidate_sha=args.candidate_sha,
        route_receipt_sha256=args.route_receipt_sha256,
        runs=runs,
    )
    if args.output is not None:
        _write_json(args.output, payload.model_dump(mode="json"))
    print(json.dumps({"round_id": args.round_id, "runs": len(runs)}))
    return 0


def _command_run_round(args: argparse.Namespace) -> int:
    manifest = load_frozen_manifest(args.manifest)
    runners = {
        ParityEngine.NATIVE_OPENCODE: _runner_from_config(
            args.native_runner_config,
            ParityEngine.NATIVE_OPENCODE,
            manifest,
        ),
        ParityEngine.MODELMIRROR_WORKER: _runner_from_config(
            args.worker_runner_config,
            ParityEngine.MODELMIRROR_WORKER,
            manifest,
        ),
    }
    runs = run_parity_matrix(
        manifest=manifest,
        runners=runners,
        candidate_sha=args.candidate_sha,
        model_route_receipt_sha256=args.route_receipt_sha256,
    )
    result = _round_result(
        round_id=args.round_id,
        manifest=manifest,
        candidate_sha=args.candidate_sha,
        route_receipt_sha256=args.route_receipt_sha256,
        runs=runs,
    )
    _write_json(args.output, result.model_dump(mode="json"))
    print(json.dumps({"round_id": args.round_id, "runs": len(runs)}))
    return 0


def _command_report(args: argparse.Namespace) -> int:
    manifest = load_frozen_manifest(args.manifest)
    round_result = ParityRoundResult.model_validate_json(
        args.round.read_text(encoding="utf-8")
    )
    audit_bytes = args.latest_opencode_audit.read_bytes()
    audit = LatestOpenCodeAudit.model_validate_json(audit_bytes)
    now = time.time()
    if audit.queried_at > now + 300 or now - audit.queried_at > MAX_LATEST_AUDIT_AGE_SECONDS:
        raise ValueError("latest OpenCode audit is stale")
    if (
        round_result.task_manifest_sha256 != manifest.canonical_sha256()
        or round_result.fixture_bundle_sha256 != manifest.fixture_bundle_sha256
        or round_result.hidden_checker_bundle_sha256
        != manifest.hidden_checker_bundle_sha256
        or round_result.runner_images != manifest.runner_images
    ):
        raise ValueError("round result differs from manifest")
    report = ParityReport(
        report_id=args.report_id,
        round_id=round_result.round_id,
        candidate_sha=round_result.candidate_sha,
        task_manifest_sha256=round_result.task_manifest_sha256,
        fixture_bundle_sha256=round_result.fixture_bundle_sha256,
        hidden_checker_bundle_sha256=round_result.hidden_checker_bundle_sha256,
        model_route_receipt_sha256=round_result.model_route_receipt_sha256,
        runner_images=round_result.runner_images,
        raw_artifact_manifest_sha256=parity_artifact_ledger_sha256(
            round_result.runs
        ),
        latest_opencode_version=audit.version,
        latest_opencode_audit_sha256=hashlib.sha256(audit_bytes).hexdigest(),
        gap_audit_sha256=_canonical_sha256(audit.gap_audit),
        gap_audit=audit.gap_audit,
        platform_coordination_failures=(
            round_result.platform_coordination_failures
        ),
        duplicate_side_effects=round_result.duplicate_side_effects,
        unsettled_operations=round_result.unsettled_operations,
        orphaned_interactions=round_result.orphaned_interactions,
        runs=round_result.runs,
    )
    decision = report.evaluate(manifest)
    _write_json(args.output, report.model_dump(mode="json"))
    print(decision.model_dump_json())
    return 0 if decision.passed else 2


def _command_certify(args: argparse.Namespace) -> int:
    manifest = load_frozen_manifest(args.manifest)
    loaded = [load_parity_report(path) for path in (args.first, args.second)]
    if any(isinstance(item, LegacyParityReport) for item in loaded):
        raise ValueError("parity v1 reports are read-only and cannot certify")
    reports = [item for item in loaded if isinstance(item, ParityReport)]
    decisions: list[ParityDecision] = [
        report.evaluate(manifest) for report in reports
    ]
    certification = ParityCertification(
        first=decisions[0], second=decisions[1]
    )
    _write_json(args.output, certification.model_dump(mode="json"))
    print(certification.model_dump_json())
    return 0


def _common_round_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--route-receipt-sha256", required=True)
    parser.add_argument("--round-id", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coding Worker parity v2 harness")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--fixtures", type=Path, required=True)
    validate.add_argument("--checker-bundle", type=Path, required=True)
    validate.add_argument("--runner-images-lock", type=Path, required=True)
    validate.add_argument("--route-catalog-lock", type=Path, required=True)
    validate.set_defaults(handler=_command_validate)

    smoke = commands.add_parser("smoke")
    _common_round_arguments(smoke)
    smoke.add_argument("--fixtures", type=Path, required=True)
    smoke.add_argument("--output", type=Path)
    smoke.set_defaults(handler=_command_smoke)

    run_round = commands.add_parser("run-round")
    _common_round_arguments(run_round)
    run_round.add_argument("--native-runner-config", type=Path, required=True)
    run_round.add_argument("--worker-runner-config", type=Path, required=True)
    run_round.add_argument("--output", type=Path, required=True)
    run_round.set_defaults(handler=_command_run_round)

    report = commands.add_parser("report")
    report.add_argument("--manifest", type=Path, required=True)
    report.add_argument("--round", type=Path, required=True)
    report.add_argument("--latest-opencode-audit", type=Path, required=True)
    report.add_argument("--report-id", required=True)
    report.add_argument("--output", type=Path, required=True)
    report.set_defaults(handler=_command_report)

    certify = commands.add_parser("certify")
    certify.add_argument("--manifest", type=Path, required=True)
    certify.add_argument("--first", type=Path, required=True)
    certify.add_argument("--second", type=Path, required=True)
    certify.add_argument("--output", type=Path, required=True)
    certify.set_defaults(handler=_command_certify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
