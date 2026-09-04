from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable

import pytest

import ai_research_worker.p2r_phase_contracts as contracts
from ai_research_worker.p2r_phase_contracts import (
    LOCKED_ASSET_MANIFEST_SHA256,
    LOCKED_ASSET_SHA256,
    P2R_PHASE_RECEIPT_PROTOCOL,
    RESEARCHSTUDIO_COMMIT,
    RESEARCHSTUDIO_REUSE_ROOT_AGGREGATE_SHA256,
    P2RPhaseContractError,
    build_raw_artifact_manifest,
    canonical_json_bytes,
    encode_phase_receipt,
    sha256_bytes,
    validate_phase_receipt,
    verify_locked_assets,
    verify_raw_artifact_manifest,
    verify_reuse_root,
    write_phase_receipt,
)


PREVIOUS_RECEIPT = b'{"protocol":"p2r-input"}\n'
INPUT_BYTES = {
    "connector-qualification/arxiv-hits.json": b"[]\n",
    "connector-qualification/connector-receipt.json": b"{}\n",
    "connector-qualification/openalex-hits.json": b"[]\n",
    "connector-qualification/openreview-hits.json": b"[]\n",
    "connector-qualification/semanticscholar-hits.json": b"[]\n",
    "p2r-input-receipt.json": PREVIOUS_RECEIPT,
}
INPUT_PATHS = tuple(sorted(INPUT_BYTES))
OUTPUT_BYTES = {
    "phase0/.lit_grounding_mode": b"real",
    "phase0/fulltext_cache.json": b"{}\n",
    "phase0/lit_results.json": b"[]\n",
    "phase0/lit_table.md": b"| paper_id |\n",
    "phase0/user_query.txt": b"fixed AI research question\n",
}
OUTPUT_PATHS = (
    *sorted(OUTPUT_BYTES),
)


def critique_output(
    blocking_refs: tuple[str, ...] = (),
    *,
    blocking_status: str = "refuted",
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "gap_closure_reject_check": {
            "entries": [
                {
                    "gap": "missing grounded control",
                    "main_pattern": "controlled_diagnostic_design",
                    "sub_pattern": "C01 (Design a Confound-Isolating Diagnostic)",
                    "tactical_failure_mode_quoted": "The control is not load bearing.",
                    "reject_lessons_evaluated": [
                        {
                            "lesson_quoted": "A proxy-only control does not isolate the mechanism.",
                            "candidate_match": "no",
                            "reasoning": "The downstream outcome is measured directly.",
                        }
                    ],
                    "verdict": "clear",
                }
            ],
            "verdict": "clear",
            "reasoning": "The single gap does not trigger a reject lesson.",
        },
        "recipe_application_check": {
            "entries": [
                {
                    "gap": "missing grounded control",
                    "sub_pattern": "C01 (Design a Confound-Isolating Diagnostic)",
                    "tactical_pattern_quoted": "Intervene on the load-bearing variable.",
                    "instantiation_in_core_mechanism": "Step S2 performs that intervention.",
                    "verdict": "applied",
                }
            ],
            "verdict": "applied",
            "reasoning": "The candidate instantiates the cited operation.",
        },
        "anti_pattern_check": {
            "composition_set": ["controlled_diagnostic_design"],
            "matched_pattern_id": None,
            "required_mitigation_quoted": None,
            "mitigation_substantively_delivered": "n/a",
            "reasoning": "No listed reject-favored composition matches.",
        },
        "paper_pointed_threat": {
            "threat_paper_id": "no_threat_found",
            "threat_source": "n/a",
            "threat_channel": None,
            "subsumption_argument": None,
            "addressable_via": None,
            "parametric_family_concern": None,
        },
        "falsification_structure_check": {
            "minimal_experiment_named": "yes",
            "outcome_metric_named": "yes",
            "load_bearing_variable": "the intervention strength",
            "negative_control_target": "outcome_metric",
            "numeric_bar_provenance": "none",
            "verdict": "sound",
            "reasoning": "The experiment, outcome, variable, and control are explicit.",
        },
        "blocking_findings_disposition": [
            {
                "finding_ref": ref,
                "status": blocking_status,
                "basis": "The executed evidence is dispositioned explicitly.",
            }
            for ref in blocking_refs
        ],
        "verdict": "advance",
        "verdict_layer": "soft_judgment",
        "verdict_rationale": "All structured checks clear the safe-zone contract.",
        "revision_targets": [],
    }
    if blocking_status == "upheld":
        output["verdict"] = "revise"
        output["revision_targets"] = [
            {
                "scope": "tactical",
                "field": "core_mechanism",
                "issue": "The executed blocking finding remains upheld.",
                "fix_direction": "Add the missing grounded control.",
            }
        ]
    return output


def recheck_output(
    refs: tuple[str, ...],
    *,
    valid: bool = True,
    flaw: str = "formalization_mismatch",
) -> dict[str, Any]:
    return {
        "rechecks": [
            {
                "finding_ref": ref,
                "claimed_flaw": flaw,
                "refutation_valid": valid,
                "reason": "The quoted step contradicts the executed formalization.",
            }
            for ref in refs
        ]
    }


def derive_output() -> dict[str, Any]:
    steps = [
        {
            "step_id": step_id,
            "what_to_do": f"Perform {step_id}.",
            "why_this_makes_sense": f"{step_id} preserves the mechanism.",
        }
        for step_id in ("S1", "S2")
    ]
    modules = [
        {
            "module_id": "M1",
            "purpose_oneline": "Build the contribution.",
            "step_ids": ["S1", "S2"],
        }
    ]
    return {
        "title_zh": "受控证据路由",
        "plain_motivation_en": "The method tests a grounded intervention.",
        "plain_motivation_zh": "该方法检验有依据的干预。",
        "plain_method_steps_en": copy.deepcopy(steps),
        "plain_method_steps_zh": copy.deepcopy(steps),
        "plain_method_modules_en": copy.deepcopy(modules),
        "plain_method_modules_zh": copy.deepcopy(modules),
    }


def implementability_output() -> dict[str, Any]:
    return {
        "underspecified_points": [],
        "enriched_steps": [
            {
                "step_id": step_id,
                "what_changes": f"Specify the concrete {step_id} transformation.",
                "what_to_do_en": f"Implement the concrete {step_id} transformation.",
                "what_to_do_zh": f"实现具体的 {step_id} 变换。",
            }
            for step_id in ("S1", "S2")
        ],
    }


def fact(data: bytes) -> dict[str, object]:
    return {"sha256": hashlib.sha256(data).hexdigest(), "sizeBytes": len(data)}


def phase0_receipt(
    *,
    input_artifacts: dict[str, dict[str, object]] | None = None,
    output_artifacts: dict[str, dict[str, object]] | None = None,
) -> dict[str, Any]:
    inputs = input_artifacts or {path: fact(data) for path, data in INPUT_BYTES.items()}
    outputs = output_artifacts or {path: fact(data) for path, data in OUTPUT_BYTES.items()}
    connector_paths = {
        connector: f"connector-qualification/{connector}-hits.json"
        for connector in ("arxiv", "openalex", "openreview", "semanticscholar")
    }
    return {
        "protocol": P2R_PHASE_RECEIPT_PROTOCOL,
        "schemaVersion": 1,
        "runId": "p2rq_" + "a" * 32,
        "phase": "phase0",
        "attempt": 1,
        "issuedAt": "2026-08-29T12:00:00Z",
        "upstreamCommit": RESEARCHSTUDIO_COMMIT,
        "reuseRootAggregateSha256": RESEARCHSTUDIO_REUSE_ROOT_AGGREGATE_SHA256,
        "previousReceiptSha256": sha256_bytes(PREVIOUS_RECEIPT),
        "lockedAssetManifestSha256": LOCKED_ASSET_MANIFEST_SHA256,
        "inputArtifacts": dict(sorted(inputs.items())),
        "deterministicActions": [
            {
                "id": "phase0.runtime",
                "script": "scripts/run.py",
                "scriptSha256": LOCKED_ASSET_SHA256["scripts/run.py"],
                "exitCode": 0,
                "stdoutSha256": "b" * 64,
                "stderrSha256": "c" * 64,
                "truncated": False,
            }
        ],
        "validatorResults": [
            {
                "id": "phase0.navigator",
                "script": "scripts/next_step.py",
                "scriptSha256": LOCKED_ASSET_SHA256["scripts/next_step.py"],
                "exitCode": 0,
                "status": "passed",
                "findings": [
                    {
                        "validator": "phase0_contract",
                        "severity": "pass",
                        "message": "fixed Phase 0 contract passed",
                    }
                ],
            }
        ],
        "outputArtifacts": dict(sorted(outputs.items())),
        "navigator": {
            "nextStepSha256": LOCKED_ASSET_SHA256["scripts/next_step.py"],
            "beforeEmitSha256": "d" * 64,
            "afterEmitSha256": "e" * 64,
            "state": "Phase 0 complete",
            "step": "Phase 1 bottleneck identification",
            "type": "llm_subagent",
        },
        "phaseEvidence": {
            "literatureGroundingMode": "real",
            "connectorsDegraded": False,
            "connectors": {
                connector: {
                    "status": "ready",
                    "attemptCount": 1,
                    "artifactPaths": [path],
                }
                for connector, path in connector_paths.items()
            },
        },
        "rawUpstreamState": "phase0_complete",
        "scientificClaim": "none",
        "claimLevel": "qualification_only",
    }


def successor_receipt(phase: str, previous: bytes) -> dict[str, Any]:
    prior = json.loads(previous)
    inputs = {
        **prior["inputArtifacts"],
        **prior["outputArtifacts"],
        contracts.PHASE_RECEIPT_PATHS[prior["phase"]]: fact(previous),
    }
    if phase == "phase1":
        output_bytes = {"phase1/phase1_output.json": b'{"state":"proceed"}\n'}
        actions = (("phase1.navigator", "scripts/next_step.py"),)
        validators = (("phase1.navigator", "scripts/next_step.py"),)
        evidence = {
            "state": "proceed",
            "outputArtifactPath": "phase1/phase1_output.json",
        }
        raw_state = "phase1_proceed"
    else:
        output_bytes = {
            "phase2_generate/closest_abstracts.json": b"[]\n",
            "phase2_generate/phase2_generate_output.json": b"{}\n",
            "phase2_select/phase2_select_output.json": b"{}\n",
        }
        actions = (
            ("phase2.prepare", "scripts/run.py"),
            ("phase2.navigator", "scripts/next_step.py"),
        )
        validators = (
            (
                "phase2.subpattern-citation-consistency",
                "scripts/validators/subpattern_citation_consistency.py",
            ),
            (
                "phase2.alias-collateral-coverage",
                "scripts/validators/alias_collateral_coverage.py",
            ),
            ("phase2.user-direction", "scripts/validators/user_direction.py"),
        )
        evidence = {
            "selectionState": "complete",
            "generationState": "complete",
            "citationGatePassed": True,
        }
        raw_state = "phase2_complete"
    action_values = [
        {
            "id": action_id,
            "script": script,
            "scriptSha256": LOCKED_ASSET_SHA256[script],
            "exitCode": 0,
            "stdoutSha256": "b" * 64,
            "stderrSha256": "c" * 64,
            "truncated": False,
        }
        for action_id, script in actions
    ]
    validator_values = [
        {
            "id": validator_id,
            "script": script,
            "scriptSha256": LOCKED_ASSET_SHA256[script],
            "exitCode": 0,
            "status": "passed",
            "findings": [
                {
                    "validator": validator_id,
                    "severity": "pass",
                    "message": "fixed validator passed",
                }
            ],
        }
        for validator_id, script in validators
    ]
    return {
        **phase0_receipt(),
        "phase": phase,
        "previousReceiptSha256": sha256_bytes(previous),
        "inputArtifacts": dict(sorted(inputs.items())),
        "deterministicActions": action_values,
        "validatorResults": validator_values,
        "outputArtifacts": {
            path: fact(data) for path, data in sorted(output_bytes.items())
        },
        "phaseEvidence": evidence,
        "rawUpstreamState": raw_state,
    }


def write_phase0_files(
    root: Path,
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    (root / "phase0").mkdir(parents=True)
    (root / "connector-qualification").mkdir()
    for path, data in INPUT_BYTES.items():
        (root / path).write_bytes(data)
    for path, data in OUTPUT_BYTES.items():
        (root / path).write_bytes(data)
    inputs = build_raw_artifact_manifest(
        root,
        list(INPUT_PATHS),
        allowed_paths=INPUT_PATHS,
    )
    outputs = build_raw_artifact_manifest(
        root,
        list(OUTPUT_PATHS),
        allowed_paths=OUTPUT_PATHS,
    )
    return inputs, outputs


def test_raw_manifest_hashes_exact_bytes_and_detects_tampering(tmp_path: Path) -> None:
    artifact = tmp_path / "phase0" / "value.json"
    artifact.parent.mkdir()
    raw = b'{"value": 1, "order": [2, 1]}\r\n'
    artifact.write_bytes(raw)
    allowed = {"phase0/value.json"}

    manifest = build_raw_artifact_manifest(
        tmp_path,
        ["phase0/value.json"],
        allowed_paths=allowed,
    )

    assert manifest == {
        "phase0/value.json": {"sha256": sha256_bytes(raw), "sizeBytes": len(raw)}
    }
    assert canonical_json_bytes(manifest).endswith(b"\n")
    artifact.write_bytes(json.dumps(json.loads(raw)).encode("utf-8"))
    with pytest.raises(P2RPhaseContractError, match="raw bytes"):
        verify_raw_artifact_manifest(tmp_path, manifest, allowed_paths=allowed)


@pytest.mark.parametrize(
    "paths",
    [
        ["phase0/value.json", "phase0/extra.json"],
        ["../outside.json"],
        ["phase0\\value.json"],
        ["/phase0/value.json"],
    ],
)
def test_manifest_rejects_unknown_or_noncanonical_paths(
    tmp_path: Path, paths: list[str]
) -> None:
    (tmp_path / "phase0").mkdir()
    (tmp_path / "phase0" / "value.json").write_text("{}", encoding="utf-8")
    with pytest.raises(P2RPhaseContractError):
        build_raw_artifact_manifest(
            tmp_path,
            paths,
            allowed_paths={"phase0/value.json"},
        )


def test_manifest_rejects_symbolic_link(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (tmp_path / "phase0").mkdir()
    link = tmp_path / "phase0" / "value.json"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")

    with pytest.raises(P2RPhaseContractError, match="symbolic link"):
        build_raw_artifact_manifest(
            tmp_path,
            ["phase0/value.json"],
            allowed_paths={"phase0/value.json"},
        )


def test_locked_asset_manifest_detects_single_byte_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asset = tmp_path / "scripts" / "run.py"
    asset.parent.mkdir()
    asset.write_bytes(b"locked")
    monkeypatch.setattr(
        contracts,
        "LOCKED_ASSET_SHA256",
        {"scripts/run.py": sha256_bytes(b"locked")},
    )
    pair_bytes = (
        b"scripts/run.py\0" + sha256_bytes(b"locked").encode("ascii") + b"\n"
    )
    monkeypatch.setattr(contracts, "RESEARCHSTUDIO_REUSE_ROOT_FILE_COUNT", 1)
    monkeypatch.setattr(contracts, "RESEARCHSTUDIO_REUSE_ROOT_TOTAL_BYTES", 6)
    monkeypatch.setattr(
        contracts,
        "RESEARCHSTUDIO_REUSE_ROOT_AGGREGATE_SHA256",
        sha256_bytes(pair_bytes),
    )

    assert verify_locked_assets(tmp_path) == {
        "scripts/run.py": {"sha256": sha256_bytes(b"locked"), "sizeBytes": 6}
    }
    asset.write_bytes(b"lockee")
    tampered_pair = (
        b"scripts/run.py\0" + sha256_bytes(b"lockee").encode("ascii") + b"\n"
    )
    monkeypatch.setattr(
        contracts,
        "RESEARCHSTUDIO_REUSE_ROOT_AGGREGATE_SHA256",
        sha256_bytes(tampered_pair),
    )
    with pytest.raises(P2RPhaseContractError, match="hash differs"):
        verify_locked_assets(tmp_path)


def test_reuse_root_aggregate_detects_unlisted_dynamic_card_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill = tmp_path / "SKILL.md"
    card = tmp_path / "references" / "ideation-sub-patterns" / "C01.md"
    card.parent.mkdir(parents=True)
    skill.write_bytes(b"skill\n")
    card.write_bytes(b"card\n")

    def aggregate() -> str:
        pairs = bytearray()
        for relative, data in sorted(
            {
                "SKILL.md": skill.read_bytes(),
                "references/ideation-sub-patterns/C01.md": card.read_bytes(),
            }.items(),
            key=lambda item: item[0].casefold(),
        ):
            pairs.extend(relative.encode("utf-8"))
            pairs.extend(b"\0")
            pairs.extend(sha256_bytes(data).encode("ascii"))
            pairs.extend(b"\n")
        return sha256_bytes(bytes(pairs))

    monkeypatch.setattr(contracts, "RESEARCHSTUDIO_REUSE_ROOT_FILE_COUNT", 2)
    monkeypatch.setattr(contracts, "RESEARCHSTUDIO_REUSE_ROOT_TOTAL_BYTES", 11)
    monkeypatch.setattr(
        contracts,
        "RESEARCHSTUDIO_REUSE_ROOT_AGGREGATE_SHA256",
        aggregate(),
    )
    assert verify_reuse_root(tmp_path)["fileCount"] == 2

    card.write_bytes(b"carE\n")
    with pytest.raises(P2RPhaseContractError, match="reuse-root identity"):
        verify_reuse_root(tmp_path)


def test_phase_receipt_binds_previous_raw_receipt_and_is_canonical() -> None:
    receipt = phase0_receipt()

    encoded = encode_phase_receipt(receipt, previous_receipt_bytes=PREVIOUS_RECEIPT)

    assert encoded == canonical_json_bytes(receipt)
    with pytest.raises(P2RPhaseContractError, match="hash chain"):
        validate_phase_receipt(receipt, previous_receipt_bytes=b"tampered\n")


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda receipt: receipt["phaseEvidence"].update(
                {"connectorsDegraded": True}
            ),
            "degraded or incomplete",
        ),
        (
            lambda receipt: receipt["phaseEvidence"]["connectors"].pop("openreview"),
            "degraded or incomplete",
        ),
        (
            lambda receipt: receipt["validatorResults"][0].update(
                {"status": "crashed"}
            ),
            "validator failed",
        ),
        (
            lambda receipt: receipt["validatorResults"][0].update({"exitCode": 1}),
            "validator failed",
        ),
        (
            lambda receipt: receipt["validatorResults"][0]["findings"][0].update(
                {"severity": "fail"}
            ),
            "failed finding",
        ),
    ],
)
def test_phase_receipt_rejects_degraded_or_failed_evidence(
    mutator: Callable[[dict[str, Any]], object], message: str
) -> None:
    receipt = phase0_receipt()
    mutator(receipt)

    with pytest.raises(P2RPhaseContractError, match=message):
        validate_phase_receipt(receipt, previous_receipt_bytes=PREVIOUS_RECEIPT)


def test_phase_receipt_schema_refuses_unknown_fields() -> None:
    receipt = phase0_receipt()
    receipt["scientificScore"] = 1.0

    with pytest.raises(P2RPhaseContractError, match="immutable schema"):
        validate_phase_receipt(receipt, previous_receipt_bytes=PREVIOUS_RECEIPT)


def test_writer_verifies_runtime_marker_and_refuses_replay(tmp_path: Path) -> None:
    inputs, outputs = write_phase0_files(tmp_path)
    receipt = phase0_receipt(input_artifacts=inputs, output_artifacts=outputs)

    path = write_phase_receipt(
        tmp_path,
        receipt,
        previous_receipt_bytes=PREVIOUS_RECEIPT,
    )

    assert path == tmp_path / "phase0" / "phase-receipt.json"
    assert path.read_bytes() == canonical_json_bytes(receipt)
    with pytest.raises(P2RPhaseContractError, match="replay refused"):
        write_phase_receipt(
            tmp_path,
            receipt,
            previous_receipt_bytes=PREVIOUS_RECEIPT,
        )


def test_writer_refuses_phase0_degraded_marker_even_if_receipt_lies(
    tmp_path: Path,
) -> None:
    inputs, outputs = write_phase0_files(tmp_path)
    receipt = phase0_receipt(input_artifacts=inputs, output_artifacts=outputs)
    (tmp_path / "phase0" / ".connectors_degraded").write_text(
        "openreview unavailable", encoding="utf-8"
    )

    with pytest.raises(P2RPhaseContractError, match="degraded marker"):
        write_phase_receipt(
            tmp_path,
            receipt,
            previous_receipt_bytes=PREVIOUS_RECEIPT,
        )


def test_writer_refuses_tampered_output_before_receipt_delivery(tmp_path: Path) -> None:
    inputs, outputs = write_phase0_files(tmp_path)
    receipt = phase0_receipt(input_artifacts=inputs, output_artifacts=outputs)
    (tmp_path / "phase0" / "lit_results.json").write_bytes(b"[{}]\n")

    with pytest.raises(P2RPhaseContractError, match="raw bytes"):
        write_phase_receipt(
            tmp_path,
            receipt,
            previous_receipt_bytes=PREVIOUS_RECEIPT,
        )


def test_writer_refuses_symbolic_link_phase_root(tmp_path: Path) -> None:
    inputs, outputs = write_phase0_files(tmp_path)
    receipt = phase0_receipt(input_artifacts=inputs, output_artifacts=outputs)
    elsewhere = tmp_path / "elsewhere"
    (tmp_path / "phase0").rename(elsewhere)
    try:
        (tmp_path / "phase0").symlink_to(elsewhere, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links unavailable: {exc}")

    with pytest.raises(P2RPhaseContractError, match="symbolic link"):
        write_phase_receipt(
            tmp_path,
            receipt,
            previous_receipt_bytes=PREVIOUS_RECEIPT,
        )


def test_duplicate_deterministic_action_is_replay() -> None:
    receipt = phase0_receipt()
    receipt["deterministicActions"].append(
        copy.deepcopy(receipt["deterministicActions"][0])
    )

    with pytest.raises(P2RPhaseContractError, match="replayed"):
        validate_phase_receipt(receipt, previous_receipt_bytes=PREVIOUS_RECEIPT)


def test_receipt_rejects_empty_actions_validators_and_failed_state() -> None:
    for field, expected in (
        ("deterministicActions", "fixed phase contract"),
        ("validatorResults", "fixed phase contract"),
    ):
        receipt = phase0_receipt()
        receipt[field] = []
        with pytest.raises(P2RPhaseContractError, match=expected):
            validate_phase_receipt(receipt, previous_receipt_bytes=PREVIOUS_RECEIPT)

    receipt = phase0_receipt()
    receipt["rawUpstreamState"] = "phase0_failed"
    with pytest.raises(P2RPhaseContractError, match="successful terminal state"):
        validate_phase_receipt(receipt, previous_receipt_bytes=PREVIOUS_RECEIPT)


def test_successor_handoff_is_exact_and_transitive() -> None:
    phase0 = phase0_receipt()
    phase0_bytes = canonical_json_bytes(phase0)
    phase1 = successor_receipt("phase1", phase0_bytes)
    validate_phase_receipt(phase1, previous_receipt_bytes=phase0_bytes)

    missing = copy.deepcopy(phase1)
    missing["inputArtifacts"].pop("phase0/lit_results.json")
    with pytest.raises(P2RPhaseContractError, match="exact predecessor artifact handoff"):
        validate_phase_receipt(missing, previous_receipt_bytes=phase0_bytes)

    phase1_bytes = canonical_json_bytes(phase1)
    phase2 = successor_receipt("phase2", phase1_bytes)
    validate_phase_receipt(phase2, previous_receipt_bytes=phase1_bytes)
    assert "connector-qualification/openreview-hits.json" in phase2["inputArtifacts"]
    assert "phase0/fulltext_cache.json" in phase2["inputArtifacts"]
    assert "phase1/phase1_output.json" in phase2["inputArtifacts"]


def test_successor_rejects_failed_predecessor_even_when_hash_chain_is_recomputed() -> None:
    phase0 = phase0_receipt()
    phase0["rawUpstreamState"] = "phase0_failed"
    failed_bytes = canonical_json_bytes(phase0)
    phase1 = successor_receipt("phase1", failed_bytes)

    with pytest.raises(P2RPhaseContractError, match="successful predecessor"):
        validate_phase_receipt(phase1, previous_receipt_bytes=failed_bytes)


def test_post_coherence_registry_is_closed_source_locked_and_not_activated() -> None:
    expected = {
        *(f"researchstudio.phase3.collision_terms.{variant}" for variant in ("raw", "refined")),
        *(f"researchstudio.phase3.critique.{variant}" for variant in ("raw", "refined")),
        *(f"researchstudio.phase3.critique.blocked.{variant}" for variant in ("raw", "refined")),
        *(f"researchstudio.phase3.critique.refutation.{variant}" for variant in ("raw", "refined")),
        *(f"researchstudio.phase3.refutation_recheck.{variant}" for variant in ("raw", "refined")),
        *(f"researchstudio.phase3.revise.{variant}" for variant in ("raw", "refined")),
        *(f"researchstudio.phase3.revise.subpattern.{variant}" for variant in ("raw", "refined")),
        "researchstudio.phase3.falsification_reaudit",
        "researchstudio.phase4.fill",
        "researchstudio.phase4.fill_repair",
        "researchstudio.phase4.derive",
        "researchstudio.phase4.implementability",
    }
    registry = contracts.POST_COHERENCE_PHASE_CONTRACTS

    assert set(registry) == expected
    assert len(registry) == 19
    assert contracts._PHASE_SEQUENCE == ("phase0", "phase1", "phase2")
    assert LOCKED_ASSET_MANIFEST_SHA256 == (
        "f92d9000e5d4f6ab2ecd75ebfa1efe46f8b5a9954e7d2a5cd61d1a0acda852e2"
    )
    assert all(item["tools"] is False for item in registry.values())
    assert all(item["activated"] is False for item in registry.values())
    assert all(item["responseShape"] == "object" for item in registry.values())
    assert "phase_3_failed.md" not in json.dumps(registry)
    assert registry["researchstudio.phase3.collision_terms.raw"]["outputArtifact"] == (
        "phase2_generate/phase2_generate_output.json"
    )
    assert registry["researchstudio.phase3.collision_terms.refined"]["outputArtifact"] == (
        "phase2_coherence/refined_candidate.json"
    )
    assert registry["researchstudio.phase3.collision_terms.raw"]["writeMode"] == (
        "in_place_host_merge"
    )
    for name, item in registry.items():
        artifacts = set(item["artifactPaths"])
        if name.endswith(".raw"):
            assert "phase2_generate/phase2_generate_output.json" in artifacts
            assert "phase2_coherence/refined_candidate.json" not in artifacts
        elif name.endswith(".refined"):
            assert "phase2_coherence/refined_candidate.json" in artifacts
            assert "phase2_generate/phase2_generate_output.json" not in artifacts
    assert "phase2_coherence/blocking_findings.json" in registry[
        "researchstudio.phase3.critique.blocked.raw"
    ]["artifactPaths"]
    assert "phase3_critique/refutation_recheck.json" in registry[
        "researchstudio.phase3.critique.refutation.raw"
    ]["artifactPaths"]
    assert registry["researchstudio.phase3.revise.subpattern.raw"][
        "dynamicArtifactPattern"
    ] == contracts.POST_COHERENCE_C_CARD_PATTERN
    for name, item in registry.items():
        if "dynamicArtifactPattern" in item:
            assert item["dynamicArtifactMin"] == 1
            assert item["dynamicArtifactMax"] == 3
            assert item["dynamicArtifactSelection"] == (
                "trusted_candidate_gap_closure_leading_c_card_exact"
            )
    assert registry["researchstudio.phase4.derive"]["writeMode"] == (
        "create_or_full_replace"
    )

    expected_source_order = {
        "researchstudio.phase3.critique.blocked.raw": (
            "phase2_generate/phase2_generate_output.json",
            "phase2_select/phase2_select_output.json",
            "phase0/lit_table.md",
            "phase2_coherence/blocking_findings.json",
            "phase3_collision/collision_hits.json",
            "references/anti-patterns.md",
        ),
        "researchstudio.phase3.critique.refutation.refined": (
            "phase2_coherence/refined_candidate.json",
            "phase2_select/phase2_select_output.json",
            "phase0/lit_table.md",
            "phase2_coherence/blocking_findings.json",
            "phase3_critique/refutation_recheck.json",
            "phase3_collision/collision_hits.json",
            "references/anti-patterns.md",
        ),
        "researchstudio.phase3.refutation_recheck.raw": (
            "phase2_coherence/blocking_findings.json",
            "phase3_critique/phase3_critique_output.json",
            "phase2_generate/phase2_generate_output.json",
        ),
    }
    for name, artifact_paths in expected_source_order.items():
        assert registry[name]["artifactPaths"] == artifact_paths

    expected_hashes = {
        "references/intent-recognition.md": "5560bf6a8c27ea903ce8690e73ef6ca9fcf15f0460da9f630ac1855d513babbb",
        "references/system-prompts/critique.txt": "b674386c19d0e2bbfaf887f94cb3fa1e8afadebe0e772ca20f0eb2e53e5f0e37",
        "references/system-prompts/refutation_recheck.txt": "8aca641287a84db080f67f5aa7ddffe3100bb7588b970c80ef04569d619db25d",
        "references/system-prompts/revise.txt": "879eb4df40fc45c32f7f7c3dfe387b587c8abb2872de4b4fbeb39d72b138274a",
        "references/system-prompts/falsification_reaudit.txt": "032d78e18ee9fd6e1e84bbe4d717c1fc1ab7a71e2cac4b9009e2bf9e413d9705",
        "references/system-prompts/expand.txt": "3c9261d04821f51dd8f52678afc07ccdf929a44a7359606da2a3ce15270a9feb",
        "references/system-prompts/derive_plain.txt": "40d6f2bf25a8daa9aec67b47936c4ccf459bf109a2c98bad0a38337c91010136",
        "references/system-prompts/implementability_audit.txt": "952cf7a5950831d676ed6360b95f1b2833faf561c2fbd821c2c0eaaed6402ce1",
    }
    assert {
        item["promptPath"]: item["promptSha256"] for item in registry.values()
    } == expected_hashes
    assert contracts.POST_COHERENCE_PROMPT_SHA256 == expected_hashes

    pattern = contracts.POST_COHERENCE_C_CARD_PATTERN
    assert re.fullmatch(pattern, "references/ideation-sub-patterns/C00.md")
    assert re.fullmatch(pattern, "references/ideation-sub-patterns/C30.md")
    assert re.fullmatch(pattern, "references/ideation-sub-patterns/C31.md") is None
    assert re.fullmatch(pattern, "references/ideation-sub-patterns/C99.md") is None
    assert re.fullmatch(pattern, "references/ideation-sub-patterns/C00.md.evil") is None

    receipt = phase0_receipt()
    receipt["phase"] = "phase3"
    with pytest.raises(P2RPhaseContractError, match="identity or claim boundary"):
        validate_phase_receipt(receipt, previous_receipt_bytes=PREVIOUS_RECEIPT)


def test_collision_terms_schema_cardinality_and_channel_separation() -> None:
    phase = "researchstudio.phase3.collision_terms.raw"
    valid = {
        "signature_terms": [
            "adaptive latent evidence routing",
            "causal representation repair gate",
            "evidence weighted agent planning",
        ],
        "alias_terms": [
            "conditional policy selection mechanism",
            "dynamic expert allocation control",
        ],
    }
    assert contracts.validate_post_coherence_phase_output(phase, valid)["status"] == "valid"

    for mutator in (
        lambda value: value["signature_terms"].__setitem__(0, "short"),
        lambda value: value["alias_terms"].__setitem__(0, value["signature_terms"][0]),
        lambda value: value.update({"extra": []}),
    ):
        invalid = copy.deepcopy(valid)
        mutator(invalid)
        with pytest.raises(P2RPhaseContractError):
            contracts.validate_post_coherence_phase_output(phase, invalid)


@pytest.mark.parametrize("unknown", [["advance"], {"value": "advance"}, "unknown"])
def test_critique_unknown_verdict_is_a_contract_error(unknown: object) -> None:
    output = critique_output()
    output["verdict"] = unknown
    with pytest.raises(P2RPhaseContractError, match="unknown enum"):
        contracts.validate_post_coherence_phase_output(
            "researchstudio.phase3.critique.raw",
            output,
            expected_gap_entries=(
                (
                    "missing grounded control",
                    "controlled_diagnostic_design",
                    "C01 (Design a Confound-Isolating Diagnostic)",
                ),
            ),
        )


def test_critique_rejects_unknown_nested_falsification_verdict() -> None:
    output = critique_output()
    output["falsification_structure_check"]["verdict"] = "unknown"
    with pytest.raises(P2RPhaseContractError, match="unknown enum"):
        contracts.validate_post_coherence_phase_output(
            "researchstudio.phase3.critique.refined",
            output,
            expected_gap_entries=(
                (
                    "missing grounded control",
                    "controlled_diagnostic_design",
                    "C01 (Design a Confound-Isolating Diagnostic)",
                ),
            ),
        )


def test_blocking_dispositions_require_unique_exact_coverage() -> None:
    phase = "researchstudio.phase3.critique.blocked.raw"
    gaps = (
        (
            "missing grounded control",
            "controlled_diagnostic_design",
            "C01 (Design a Confound-Isolating Diagnostic)",
        ),
    )
    valid = critique_output(("bf1", "bf2"))
    assert contracts.validate_post_coherence_phase_output(
        phase,
        valid,
        expected_gap_entries=gaps,
        expected_blocking_finding_refs=("bf1", "bf2"),
    )["status"] == "valid"

    duplicate = copy.deepcopy(valid)
    duplicate["blocking_findings_disposition"][1]["finding_ref"] = "bf1"
    with pytest.raises(P2RPhaseContractError, match="duplicate"):
        contracts.validate_post_coherence_phase_output(
            phase,
            duplicate,
            expected_gap_entries=gaps,
            expected_blocking_finding_refs=("bf1", "bf2"),
        )

    wrong = copy.deepcopy(valid)
    wrong["blocking_findings_disposition"][1]["finding_ref"] = "bf3"
    with pytest.raises(P2RPhaseContractError, match="exactly cover"):
        contracts.validate_post_coherence_phase_output(
            phase,
            wrong,
            expected_gap_entries=gaps,
            expected_blocking_finding_refs=("bf1", "bf2"),
        )


def test_critique_accepts_both_source_valid_abandon_routes() -> None:
    gaps = (
        (
            "missing grounded control",
            "controlled_diagnostic_design",
            "C01 (Design a Confound-Isolating Diagnostic)",
        ),
    )
    anti_pattern = critique_output()
    anti_pattern["anti_pattern_check"].update(
        {
            "matched_pattern_id": "audit_decomp_supervisor",
            "required_mitigation_quoted": "Emit the auditable intermediate artifact.",
            "mitigation_substantively_delivered": False,
        }
    )
    anti_pattern["verdict"] = "abandon"
    anti_pattern["verdict_layer"] = "hard_floor"
    anti_pattern["revision_targets"] = []
    assert contracts.validate_post_coherence_phase_output(
        "researchstudio.phase3.critique.raw",
        anti_pattern,
        expected_gap_entries=gaps,
    )["status"] == "valid"

    redesign = critique_output(("bf1",), blocking_status="upheld")
    redesign["verdict"] = "abandon"
    redesign["verdict_layer"] = "soft_judgment"
    redesign["revision_targets"] = []
    assert contracts.validate_post_coherence_phase_output(
        "researchstudio.phase3.critique.blocked.refined",
        redesign,
        expected_gap_entries=gaps,
        expected_blocking_finding_refs=("bf1",),
    )["status"] == "valid"


def test_refutation_critique_requires_invalid_refs_to_be_rebound_as_upheld() -> None:
    phase = "researchstudio.phase3.critique.refutation.raw"
    gaps = (
        (
            "missing grounded control",
            "controlled_diagnostic_design",
            "C01 (Design a Confound-Isolating Diagnostic)",
        ),
    )
    valid = critique_output(("bf1",), blocking_status="upheld")
    assert contracts.validate_post_coherence_phase_output(
        phase,
        valid,
        expected_gap_entries=gaps,
        expected_blocking_finding_refs=("bf1",),
        expected_invalidated_refutation_refs=("bf1",),
    )["status"] == "valid"

    wrong = critique_output(("bf1",), blocking_status="refuted")
    with pytest.raises(P2RPhaseContractError, match="rebound as upheld"):
        contracts.validate_post_coherence_phase_output(
            phase,
            wrong,
            expected_gap_entries=gaps,
            expected_blocking_finding_refs=("bf1",),
            expected_invalidated_refutation_refs=("bf1",),
        )


def test_refutation_recheck_coverage_bounce_and_arithmetic_host_stop() -> None:
    phase = "researchstudio.phase3.refutation_recheck.refined"
    valid = recheck_output(("bf1", "bf2"))
    assert contracts.validate_post_coherence_phase_output(
        phase,
        valid,
        expected_refuted_finding_refs=("bf1", "bf2"),
    )["status"] == "valid"

    duplicate = recheck_output(("bf1", "bf1"))
    with pytest.raises(P2RPhaseContractError, match="duplicate"):
        contracts.validate_post_coherence_phase_output(
            phase,
            duplicate,
            expected_refuted_finding_refs=("bf1", "bf2"),
        )
    with pytest.raises(P2RPhaseContractError, match="cardinality"):
        contracts.validate_post_coherence_phase_output(
            phase,
            recheck_output(("bf1",)),
            expected_refuted_finding_refs=("bf1", "bf2"),
        )
    with pytest.raises(P2RPhaseContractError, match="exactly cover"):
        contracts.validate_post_coherence_phase_output(
            phase,
            recheck_output(("bf1", "bf3")),
            expected_refuted_finding_refs=("bf1", "bf2"),
        )

    bounce = contracts.validate_post_coherence_phase_output(
        phase,
        recheck_output(("bf1",), valid=False),
        expected_refuted_finding_refs=("bf1",),
    )
    assert bounce == {
        "status": "requires_critique_bounce",
        "requiresHostStop": False,
        "executionClaim": "not_requested",
        "invalidFindingRefs": ["bf1"],
    }

    host_stop = contracts.validate_post_coherence_phase_output(
        phase,
        recheck_output(("bf1",), flaw="arithmetic_error"),
        expected_refuted_finding_refs=("bf1",),
    )
    assert host_stop["status"] == "requires_host_stop"
    assert host_stop["requiresHostStop"] is True
    assert host_stop["executionClaim"] == "not_executed"
    assert contracts.POST_COHERENCE_PHASE_CONTRACTS[phase]["tools"] is False


def test_revise_enforces_variant_op_value_and_field_contracts() -> None:
    normal = "researchstudio.phase3.revise.raw"
    valid = {
        "candidate_id": None,
        "applied_revisions": [
            {
                "scope": "tactical",
                "op": "append_sentence",
                "field": "core_mechanism",
                "value": "Add a grounded intervention control.",
                "outcome": "applied",
                "delta_summary": "Added the missing grounded control.",
            }
        ],
    }
    assert contracts.validate_post_coherence_phase_output(
        normal, valid, expected_revision_target_count=1
    )["status"] == "valid"

    for field, value in (
        ("core_mechanism", {"not": "a sentence"}),
        ("../core_mechanism", "text"),
        ("unknown_root", "text"),
        ("compute_budget", "text"),
    ):
        invalid = copy.deepcopy(valid)
        invalid["applied_revisions"][0]["field"] = field
        invalid["applied_revisions"][0]["value"] = value
        with pytest.raises(P2RPhaseContractError):
            contracts.validate_post_coherence_phase_output(
                normal, invalid, expected_revision_target_count=1
            )

    subpattern = copy.deepcopy(valid)
    subpattern["applied_revisions"][0].update(
        {
            "scope": "sub_pattern",
            "op": "swap_sub_pattern",
            "field": "missing grounded control",
            "value": "C02 (Design a Confound-Isolating Diagnostic)",
        }
    )
    with pytest.raises(P2RPhaseContractError, match="artifact variant"):
        contracts.validate_post_coherence_phase_output(
            normal, subpattern, expected_revision_target_count=1
        )
    assert contracts.validate_post_coherence_phase_output(
        "researchstudio.phase3.revise.subpattern.raw",
        subpattern,
        expected_revision_target_count=1,
    )["status"] == "valid"
    subpattern["applied_revisions"][0]["value"] = "C31 (Outside Locked Cards)"
    with pytest.raises(P2RPhaseContractError, match="requires swap_sub_pattern"):
        contracts.validate_post_coherence_phase_output(
            "researchstudio.phase3.revise.subpattern.raw",
            subpattern,
            expected_revision_target_count=1,
        )


def test_falsification_reaudit_requires_rewrite_proof_and_closed_verdicts() -> None:
    phase = "researchstudio.phase3.falsification_reaudit"
    valid = {
        "falsification_structure_check": {
            "minimal_experiment_named": "yes",
            "outcome_metric_named": "yes",
            "load_bearing_variable": "intervention strength",
            "negative_control_target": "outcome_metric",
            "verdict": "sound",
            "reasoning": "The repaired paragraph is structurally sound.",
        },
        "verdict": "advance",
        "verdict_rationale": "The one authorized repair now passes.",
    }
    with pytest.raises(P2RPhaseContractError, match="trusted proof"):
        contracts.validate_post_coherence_phase_output(phase, valid)
    assert contracts.validate_post_coherence_phase_output(
        phase, valid, expected_falsification_rewrite_applied=True
    )["status"] == "valid"

    unknown = copy.deepcopy(valid)
    unknown["falsification_structure_check"]["verdict"] = "unknown"
    with pytest.raises(P2RPhaseContractError, match="unknown enum"):
        contracts.validate_post_coherence_phase_output(
            phase, unknown, expected_falsification_rewrite_applied=True
        )

    unknown_outer = copy.deepcopy(valid)
    unknown_outer["verdict"] = "unknown"
    with pytest.raises(P2RPhaseContractError, match="unknown enum"):
        contracts.validate_post_coherence_phase_output(
            phase, unknown_outer, expected_falsification_rewrite_applied=True
        )

    inconsistent = copy.deepcopy(valid)
    inconsistent["falsification_structure_check"]["minimal_experiment_named"] = "no"
    inconsistent["falsification_structure_check"]["verdict"] = "deficient"
    with pytest.raises(P2RPhaseContractError, match="routing contradicts"):
        contracts.validate_post_coherence_phase_output(
            phase, inconsistent, expected_falsification_rewrite_applied=True
        )


def test_fill_map_exact_paths_shapes_enums_and_forbidden_roots() -> None:
    phase = "researchstudio.phase4.fill"
    valid: dict[str, Any] = {
        "method_name": "Grounded Control Router",
        "sub_claims": [
            {"id": "c1", "statement": "Claim one.", "supports_which_aspect": "control"},
            {"id": "c2", "statement": "Claim two.", "supports_which_aspect": "routing"},
        ],
        "method_flow.steps": [
            {
                "step_id": step_id,
                "title": f"Step {step_id}",
                "what_changes": "Apply the fixed transformation.",
                "why_this_step": "It preserves the causal test.",
                "linked_component": "theory",
                "linked_falsification": "metric_specification",
                "input": "candidate evidence",
                "output": "grounded decision",
            }
            for step_id in ("S1", "S2")
        ],
        "key_equations": [],
        "feasibility_validation.data.verdict": "feasible",
        "feasibility_validation.theoretical.verdict": "n/a",
        "feasibility_validation.engineering.verdict": "n/a",
        "feasibility_validation.falsification.verdict": "tight",
        "feasibility_validation.overall": "tight",
    }
    expected_paths = tuple(valid)
    assert contracts.validate_post_coherence_phase_output(
        phase,
        valid,
        expected_todo_paths=expected_paths,
        expected_method_step_ids=("S1", "S2"),
    )["status"] == "valid"

    missing = copy.deepcopy(valid)
    missing.pop("method_name")
    with pytest.raises(P2RPhaseContractError, match="exactly cover"):
        contracts.validate_post_coherence_phase_output(
            phase,
            missing,
            expected_todo_paths=expected_paths,
            expected_method_step_ids=("S1", "S2"),
        )

    for forbidden in (
        "falsification_prediction",
        "compute_budget.detail",
        "title_zh",
        "plain_method_steps_en",
    ):
        invalid = copy.deepcopy(valid)
        invalid[forbidden] = "forbidden"
        with pytest.raises(P2RPhaseContractError, match="forbidden or derive-owned"):
            contracts.validate_post_coherence_phase_output(
                phase,
                invalid,
                expected_todo_paths=(*expected_paths, forbidden),
                expected_method_step_ids=("S1", "S2"),
            )


def test_derive_map_has_exact_seven_keys_step_order_and_bilingual_layout() -> None:
    phase = "researchstudio.phase4.derive"
    valid = derive_output()
    assert contracts.validate_post_coherence_phase_output(
        phase, valid, expected_method_step_ids=("S1", "S2")
    )["status"] == "valid"

    extra = copy.deepcopy(valid)
    extra["extra"] = "not source-backed"
    with pytest.raises(P2RPhaseContractError, match="unknown or missing"):
        contracts.validate_post_coherence_phase_output(
            phase, extra, expected_method_step_ids=("S1", "S2")
        )

    wrong_order = copy.deepcopy(valid)
    wrong_order["plain_method_steps_en"].reverse()
    with pytest.raises(P2RPhaseContractError, match="exactly mirror"):
        contracts.validate_post_coherence_phase_output(
            phase, wrong_order, expected_method_step_ids=("S1", "S2")
        )

    wrong_layout = copy.deepcopy(valid)
    wrong_layout["plain_method_modules_zh"][0]["module_id"] = "M2"
    with pytest.raises(P2RPhaseContractError, match="layouts differ"):
        contracts.validate_post_coherence_phase_output(
            phase, wrong_layout, expected_method_step_ids=("S1", "S2")
        )


def test_implementability_is_closed_exact_and_requires_inline_open_decisions() -> None:
    phase = "researchstudio.phase4.implementability"
    valid = implementability_output()
    assert contracts.validate_post_coherence_phase_output(
        phase, valid, expected_method_step_ids=("S1", "S2")
    )["status"] == "valid"

    extra = copy.deepcopy(valid)
    extra["extra"] = []
    with pytest.raises(P2RPhaseContractError, match="unknown or missing"):
        contracts.validate_post_coherence_phase_output(
            phase, extra, expected_method_step_ids=("S1", "S2")
        )

    wrong_order = copy.deepcopy(valid)
    wrong_order["enriched_steps"].reverse()
    with pytest.raises(P2RPhaseContractError, match="exactly cover"):
        contracts.validate_post_coherence_phase_output(
            phase, wrong_order, expected_method_step_ids=("S1", "S2")
        )

    open_hole = copy.deepcopy(valid)
    open_hole["underspecified_points"] = [
        {
            "step_id": "S1",
            "hole": "The threshold source is unstated.",
            "fill": "Authors must choose a calibration source.",
            "severity": "open",
        }
    ]
    with pytest.raises(P2RPhaseContractError, match="inline annotations"):
        contracts.validate_post_coherence_phase_output(
            phase, open_hole, expected_method_step_ids=("S1", "S2")
        )

    open_hole["enriched_steps"][0]["what_changes"] += " 【author decision: calibration source】"
    open_hole["enriched_steps"][0]["what_to_do_en"] += " 【author decision: calibration source】"
    open_hole["enriched_steps"][0]["what_to_do_zh"] += " 【作者需决定：校准来源】"
    assert contracts.validate_post_coherence_phase_output(
        phase, open_hole, expected_method_step_ids=("S1", "S2")
    )["status"] == "valid"
