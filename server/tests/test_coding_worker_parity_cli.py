from __future__ import annotations

import json
import time
from pathlib import Path

from scripts.coding_worker_parity import (
    OFFICIAL_LATEST_SOURCE,
    DeterministicFakeRunner,
    ParityRoundResult,
    main,
)
from server.coding_worker.parity import ParityEngine, load_frozen_manifest
from server.coding_worker.parity_runner import run_parity_matrix


FIXTURES = Path(__file__).parent / "fixtures"
MANIFEST = FIXTURES / "coding_worker_v16_parity.json"
ASSETS = FIXTURES / "coding_worker_v17_parity_assets.json"
CANDIDATE = "a" * 40
ROUTE_RECEIPT = "b" * 64
CHECKER_CONTENT = b"modelmirror-v17-sealed-checker-bundle-contract-v1"


def _write_validation_locks(tmp_path: Path) -> tuple[Path, Path, Path]:
    manifest = load_frozen_manifest(MANIFEST)
    checker = tmp_path / "checker.bundle"
    checker.write_bytes(CHECKER_CONTENT)
    images = tmp_path / "runner-images.json"
    images.write_text(
        json.dumps({"runner_images": manifest.runner_images.model_dump(mode="json")}),
        encoding="utf-8",
    )
    route = tmp_path / "route.json"
    route.write_text(
        json.dumps(
            {
                "model_route": manifest.model_route,
                "catalog_sha256": manifest.model_route_catalog_sha256,
            }
        ),
        encoding="utf-8",
    )
    return checker, images, route


def _full_round(round_id: str) -> ParityRoundResult:
    manifest = load_frozen_manifest(MANIFEST)
    runs = run_parity_matrix(
        manifest=manifest,
        runners={
            engine: DeterministicFakeRunner(engine) for engine in ParityEngine
        },
        candidate_sha=CANDIDATE,
        model_route_receipt_sha256=ROUTE_RECEIPT,
    )
    return ParityRoundResult(
        round_id=round_id,
        candidate_sha=CANDIDATE,
        task_manifest_sha256=manifest.canonical_sha256(),
        fixture_bundle_sha256=manifest.fixture_bundle_sha256,
        hidden_checker_bundle_sha256=manifest.hidden_checker_bundle_sha256,
        model_route_receipt_sha256=ROUTE_RECEIPT,
        runner_images=manifest.runner_images,
        runs=runs,
    )


def test_validate_binds_fixture_checker_route_and_runner_images(
    tmp_path: Path, capsys
) -> None:
    checker, images, route = _write_validation_locks(tmp_path)
    arguments = [
        "validate",
        "--manifest",
        str(MANIFEST),
        "--fixtures",
        str(ASSETS),
        "--checker-bundle",
        str(checker),
        "--runner-images-lock",
        str(images),
        "--route-catalog-lock",
        str(route),
    ]
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["tasks"] == 24

    checker.write_bytes(b"tampered")
    assert main(arguments) == 2
    assert "checker bundle digest" in capsys.readouterr().err


def test_smoke_runs_one_cell_per_engine_and_category(tmp_path: Path) -> None:
    output = tmp_path / "smoke.json"
    assert (
        main(
            [
                "smoke",
                "--manifest",
                str(MANIFEST),
                "--fixtures",
                str(ASSETS),
                "--candidate-sha",
                CANDIDATE,
                "--route-receipt-sha256",
                ROUTE_RECEIPT,
                "--round-id",
                "round_smoke",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    result = ParityRoundResult.model_validate_json(output.read_text(encoding="utf-8"))
    assert len(result.runs) == 8
    assert {run.engine for run in result.runs} == set(ParityEngine)
    assert len({run.task_id for run in result.runs}) == 4


def test_report_and_certify_require_two_v2_rounds_with_same_bindings(
    tmp_path: Path,
) -> None:
    audit = tmp_path / "latest-opencode.json"
    audit.write_text(
        json.dumps(
            {
                "source": OFFICIAL_LATEST_SOURCE,
                "queried_at": time.time(),
                "version": "1.18.9",
                "gap_audit": [],
            }
        ),
        encoding="utf-8",
    )
    reports: list[Path] = []
    for index in (1, 2):
        round_path = tmp_path / f"round-{index}.json"
        round_path.write_text(
            _full_round(f"round_{index}").model_dump_json(), encoding="utf-8"
        )
        report_path = tmp_path / f"report-{index}.json"
        assert (
            main(
                [
                    "report",
                    "--manifest",
                    str(MANIFEST),
                    "--round",
                    str(round_path),
                    "--latest-opencode-audit",
                    str(audit),
                    "--report-id",
                    f"report_{index}",
                    "--output",
                    str(report_path),
                ]
            )
            == 0
        )
        reports.append(report_path)

    certification = tmp_path / "certification.json"
    assert (
        main(
            [
                "certify",
                "--manifest",
                str(MANIFEST),
                "--first",
                str(reports[0]),
                "--second",
                str(reports[1]),
                "--output",
                str(certification),
            ]
        )
        == 0
    )
    payload = json.loads(certification.read_text(encoding="utf-8"))
    assert payload["first"]["passed"] is True
    assert payload["second"]["passed"] is True
