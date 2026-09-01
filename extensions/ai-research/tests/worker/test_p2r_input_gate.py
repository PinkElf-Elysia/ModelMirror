from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from ai_research_worker.p2r_input_gate import (
    EXPECTED_OUTPUT_FILES,
    P2R_INPUT_PROTOCOL,
    QUALIFICATION_RUN_ID_PATTERN,
    LiteratureBundleProfile,
    P2RInputError,
    _seed_qualification_run,
    _verify_literature_project,
)


PROJECT_ID = "rp_" + "a" * 32
RUN_ID = "lr_" + "b" * 32
SOURCE_LOCK_SHA256 = "c" * 64


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def artifact_fact(data: bytes) -> dict[str, object]:
    return {"sha256": sha(data), "sizeBytes": len(data)}


def write_bundle(
    root: Path,
    *,
    source_url: str = "https://doi.org/10.1000/example",
    integrity: str = "verified",
) -> LiteratureBundleProfile:
    project_root = root / PROJECT_ID
    run_dir = project_root / "literature" / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    research = {
        "schemaVersion": 1,
        "projectId": PROJECT_ID,
        "title": "Verified V0.1 bundle",
        "researchQuestion": "How can agent evaluations be reproducible?",
        "domain": "ai_agent",
        "literature": {
            "phase": "terminal",
            "outcome": "completed",
            "completedRunId": RUN_ID,
            "attempts": [
                {
                    "runId": RUN_ID,
                    "phase": "terminal",
                    "outcome": "completed",
                    "rawStatus": "completed",
                    "integrityStatus": integrity,
                    "searchEngine": "openalex",
                    "egress": "public_only",
                }
            ],
        },
    }
    research_bytes = yaml.safe_dump(
        research, allow_unicode=True, sort_keys=False
    ).encode("utf-8")
    (project_root / "research.yaml").write_bytes(research_bytes)

    output_bytes = {
        "literature-review.md": b"# Review\n",
        "literature-review.qmd": b"# Review\n",
        "references.bib": b"@article{example,title={Example}}\n",
        "references.ris": b"TY  - JOUR\nER  -\n",
        "sources.json": json_bytes([{"title": "Example", "url": source_url}]),
        "upstream-quarto.zip": b"fixed-zip-fixture",
    }
    assert set(output_bytes) == EXPECTED_OUTPUT_FILES
    for name, data in output_bytes.items():
        (run_dir / name).write_bytes(data)
    outputs = {name: artifact_fact(data) for name, data in output_bytes.items()}
    receipt = {
        "schemaVersion": 1,
        "projectId": PROJECT_ID,
        "literatureRunId": RUN_ID,
        "ldrVersion": "1.10.6",
        "ldrCommit": "641308272b2143df89c7a946051d2f05ca29b3c1",
        "sourceLockSha256": SOURCE_LOCK_SHA256,
        "generatedBy": "upstream_local_deep_research",
        "outcome": "completed",
        "rawStatus": "completed",
        "searchEngine": "openalex",
        "egress": "public_only",
        "scientificClaim": "none",
        "outputs": outputs,
    }
    receipt_bytes = json_bytes(receipt)
    (run_dir / "literature-receipt.json").write_bytes(receipt_bytes)
    manifest = {
        "schemaVersion": 1,
        "artifacts": {
            **outputs,
            "literature-receipt.json": artifact_fact(receipt_bytes),
        },
    }
    manifest_bytes = json_bytes(manifest)
    (run_dir / "artifact-manifest.json").write_bytes(manifest_bytes)
    return LiteratureBundleProfile(
        project_id=PROJECT_ID,
        run_id=RUN_ID,
        research_yaml_sha256=sha(research_bytes),
        manifest_sha256=sha(manifest_bytes),
        receipt_sha256=sha(receipt_bytes),
        source_lock_sha256=SOURCE_LOCK_SHA256,
    )


def test_verified_bundle_recomputes_every_artifact_and_public_source(tmp_path: Path) -> None:
    profile = write_bundle(tmp_path)
    result = _verify_literature_project(tmp_path / PROJECT_ID, profile=profile)
    assert result.project_id == PROJECT_ID
    assert result.run_id == RUN_ID
    assert result.source_count == 1
    assert len(result.bundle_sha256) == 64


def test_self_reported_verified_does_not_survive_artifact_tampering(tmp_path: Path) -> None:
    profile = write_bundle(tmp_path)
    report = tmp_path / PROJECT_ID / "literature" / "runs" / RUN_ID / "literature-review.md"
    report.write_bytes(report.read_bytes() + b"tampered")
    with pytest.raises(P2RInputError, match="artifact integrity"):
        _verify_literature_project(tmp_path / PROJECT_ID, profile=profile)


def test_semantically_unverified_attempt_fails_even_with_matching_file_hashes(
    tmp_path: Path,
) -> None:
    profile = write_bundle(tmp_path, integrity="pending")
    with pytest.raises(P2RInputError, match="integrity-verified"):
        _verify_literature_project(tmp_path / PROJECT_ID, profile=profile)


@pytest.mark.parametrize(
    "source_url",
    [
        "http://doi.org/10.1000/example",
        "https://localhost/private",
        "https://127.0.0.1/private",
        "file:///private/paper.pdf",
        "https://user:password@example.com/paper",
    ],
)
def test_non_public_sources_fail_even_when_all_bundle_hashes_match(
    tmp_path: Path, source_url: str
) -> None:
    profile = write_bundle(tmp_path, source_url=source_url)
    with pytest.raises(P2RInputError, match="public HTTPS"):
        _verify_literature_project(tmp_path / PROJECT_ID, profile=profile)


def test_unknown_artifact_fails_closed(tmp_path: Path) -> None:
    profile = write_bundle(tmp_path)
    run_dir = tmp_path / PROJECT_ID / "literature" / "runs" / RUN_ID
    (run_dir / "untracked.txt").write_text("unknown", encoding="utf-8")
    with pytest.raises(P2RInputError, match="unexpected artifacts"):
        _verify_literature_project(tmp_path / PROJECT_ID, profile=profile)


def test_seed_receipt_binds_verified_bundle_and_requires_upstream_phase0(
    tmp_path: Path,
) -> None:
    bundle_parent = tmp_path / "bundle"
    output_parent = tmp_path / "qualification"
    bundle_parent.mkdir()
    output_parent.mkdir()
    profile = write_bundle(bundle_parent)
    output = _seed_qualification_run(
        bundle_parent / PROJECT_ID,
        output_parent,
        profile=profile,
    )
    receipt_bytes = (output / "p2r-input-receipt.json").read_bytes()
    receipt = json.loads(receipt_bytes)
    verified = _verify_literature_project(bundle_parent / PROJECT_ID, profile=profile)
    assert receipt["protocol"] == P2R_INPUT_PROTOCOL
    assert QUALIFICATION_RUN_ID_PATTERN.fullmatch(receipt["qualificationRunId"])
    issued_at = datetime.fromisoformat(receipt["issuedAt"].replace("Z", "+00:00"))
    assert issued_at.tzinfo == timezone.utc
    assert receipt["bundleSha256"] == verified.bundle_sha256
    assert receipt["researchQuestion"] == "How can agent evaluations be reproducible?"
    assert receipt["handoff"] == {
        "mode": "eligibility_and_exact_research_question",
        "upstreamPhase0RetrievalRequired": True,
        "v01ReviewInjected": False,
    }
    assert receipt["scientificClaim"] == "none"
    assert not (output / "phase0").exists()
    with pytest.raises(P2RInputError, match="immutable"):
        _seed_qualification_run(
            bundle_parent / PROJECT_ID,
            output_parent,
            profile=profile,
        )


def test_each_fresh_seed_gets_a_distinct_qualification_identity(tmp_path: Path) -> None:
    bundle_parent = tmp_path / "bundle"
    first_parent = tmp_path / "first"
    second_parent = tmp_path / "second"
    bundle_parent.mkdir()
    first_parent.mkdir()
    second_parent.mkdir()
    profile = write_bundle(bundle_parent)
    first = _seed_qualification_run(
        bundle_parent / PROJECT_ID, first_parent, profile=profile
    )
    second = _seed_qualification_run(
        bundle_parent / PROJECT_ID, second_parent, profile=profile
    )
    first_receipt = json.loads((first / "p2r-input-receipt.json").read_text("utf-8"))
    second_receipt = json.loads((second / "p2r-input-receipt.json").read_text("utf-8"))
    assert first_receipt["qualificationRunId"] != second_receipt["qualificationRunId"]


def test_seed_receipt_is_never_written_inside_source_project(tmp_path: Path) -> None:
    profile = write_bundle(tmp_path)
    project_root = tmp_path / PROJECT_ID
    output_parent = project_root / "qualification"
    output_parent.mkdir()
    with pytest.raises(P2RInputError, match="must not be written"):
        _seed_qualification_run(project_root, output_parent, profile=profile)
