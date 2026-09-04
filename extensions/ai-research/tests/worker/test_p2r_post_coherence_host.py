from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path

import pytest
from inspect_ai.event import SandboxEvent, SpanBeginEvent, ToolEvent
from inspect_ai.log import (
    EvalConfig,
    EvalDataset,
    EvalLog,
    EvalSample,
    EvalSpec,
    read_eval_log,
    write_eval_log,
)
from inspect_ai.model import ChatMessageAssistant, ModelOutput

from ai_research_worker import p2r_host as coherence_host
from ai_research_worker import p2r_phase_contracts as contracts
from ai_research_worker import p2r_post_coherence_host as post


RUN_ID = "p2rq_" + "a" * 32
MODULE_ROOT = Path(__file__).resolve().parents[2]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def candidate() -> dict[str, object]:
    return {
        "title": "Receipt-bound collision audit",
        "hook": "A bounded host must preserve exact evidence lineage.",
        "core_mechanism": "Route fixed evidence through a canonical receipt chain.",
        "core_mechanism_steps": ["bind", "verify"],
        "gap_closure": [
            {
                "gap": "missing grounded control",
                "main_pattern": "controlled_diagnostic_design",
                "sub_pattern": "C01 (Design a Confound-Isolating Diagnostic)",
                "how_closed": "Bind every transition to the prior raw receipt.",
            }
        ],
        "signature_terms": [
            "adaptive latent evidence routing",
            "causal representation repair gate",
            "evidence weighted agent planning",
        ],
        "alias_terms": [
            "conditional policy selection mechanism",
            "dynamic expert allocation control",
        ],
        "falsification_prediction": "A fixed negative control must fail.",
        "compute_budget": {"max_calls": 1},
    }


def coherence_output(
    *, revisions: list[dict[str, object]] | None = None, blocking: bool = False
) -> dict[str, object]:
    applied = revisions or []
    unrepaired: list[dict[str, object]] = []
    if blocking:
        unrepaired.append(
            {
                "finding": "The negative control remains observationally indistinguishable under the fixed trace.",
                "severity": "blocking",
                "why_not_repaired": "Repair would change the committed experiment.",
                "verbatim_step_quote": "S1 consumes the fixed input.",
                "executed_evidence": "The receipt-backed trace returned identical outputs.",
                "reading_dependence": "The claim depends on distinguishing those outputs.",
                "structural_requirement": "Add an independently observable negative control.",
            }
        )
    return {
        "trace_report": {
            "formalized_procedure": [
                {
                    "step": "S1",
                    "consumes": ["input"],
                    "produces": ["output"],
                    "note": "fixed minimal dataflow",
                }
            ],
            "dry_run": {
                "instance": "three fixed objects",
                "execution": {
                    "mode": "executed",
                    "script": "print(3)",
                    "output": "3\n",
                },
                "computed_quantities": [
                    {"quantity": "count", "value": "3", "arithmetic": "1 + 2 = 3"}
                ],
                "anomalies": [],
            },
            "degenerate_probes": [
                {"probe": "empty input", "behavior": "halts", "finding": None}
            ],
            "claim_step_map": [
                {
                    "claim": "deterministic output",
                    "established_by": "S1",
                    "strength_grade": "established",
                    "assumptions_missing": [],
                    "arbitration": "argument",
                    "measured": None,
                }
            ],
            "naive_comparison": {
                "declared_branch": "(ii) incremental",
                "naive_version": "return the fixed input",
                "naive_fairness": "same information and budget with fewer steps",
                "instance_behavior": {
                    "naive": "3",
                    "mechanism": "3",
                    "divergence": "none",
                    "kind": "structural",
                },
                "verdict": "confronts_obstacle",
                "reasoning": "The fixed trace exercises the declared step.",
            },
        },
        "verdict": "patched" if applied else "pass",
        "unrepaired": unrepaired,
        "applied_revisions": applied,
    }


def handoff(
    tmp_path: Path,
    *,
    revisions: list[dict[str, object]] | None = None,
    blocking: bool = False,
    raw_candidate_bytes: bytes | None = None,
) -> coherence_host._VerifiedCoherenceHandoff:
    value = candidate()
    raw = raw_candidate_bytes or post._canonical_bytes(value)
    (tmp_path / "phase2_generate").mkdir()
    (tmp_path / "phase2_generate" / "phase2_generate_output.json").write_bytes(raw)
    (tmp_path / "phase2_coherence").mkdir()
    receipt = b'{"trusted":"coherence"}\n'
    output = coherence_output(revisions=revisions, blocking=blocking)
    return coherence_host._VerifiedCoherenceHandoff(
        run_dir=tmp_path,
        run_id=RUN_ID,
        phase2_receipt_sha256="b" * 64,
        coherence_receipt=receipt,
        coherence_receipt_sha256=sha(receipt),
        raw_candidate=value,
        raw_candidate_bytes=raw,
        coherence_output=output,
        coherence_output_bytes=coherence_host._json_bytes(output),
        blocking_findings=tuple(coherence_host._blocking_findings(output)),
    )


def test_pass_emits_only_disabled_collision_action(tmp_path: Path) -> None:
    trusted = handoff(tmp_path)
    action = post._prepare_verified(trusted)
    selection = (
        tmp_path / "phase2_coherence" / "canonical-selection-receipt.json"
    ).read_bytes()
    assert action["phaseId"] == "researchstudio.phase3.collision"
    assert action["previousReceiptSha256"] == sha(selection)
    assert action["tools"] is False
    assert action["dispatchAllowed"] is False
    assert action["evidenceAcceptanceImplemented"] is False
    assert action["expectedFacts"]["connectorOrder"] == [
        "arxiv",
        "openalex",
        "semanticscholar",
        "openreview",
    ]
    assert post._prepare_verified(trusted) == action
    assert not hasattr(post, "accept_collision_evidence")
    with pytest.raises(TypeError):
        post.prepare_post_coherence(trusted)  # type: ignore[misc]


def test_public_entries_revalidate_paths_on_every_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trusted = handoff(tmp_path)
    calls: list[dict[str, Path]] = []

    def reload_from_paths(**paths: Path) -> coherence_host._VerifiedCoherenceHandoff:
        calls.append(paths)
        return trusted

    monkeypatch.setattr(post, "load_verified_coherence_handoff", reload_from_paths)
    arguments = {
        "repository_root": tmp_path,
        "skill_root": tmp_path,
        "run_dir": tmp_path,
    }
    first = post.prepare_post_coherence(**arguments)
    assert post.resume_post_coherence(**arguments) == first
    assert calls == [arguments, arguments]


def test_pass_preserves_noncanonical_upstream_candidate_bytes_end_to_end(
    tmp_path: Path,
) -> None:
    value = candidate()
    raw = (json.dumps(value, ensure_ascii=False, indent=4) + "  \n").encode("utf-8")
    assert raw != post._canonical_bytes(value)
    trusted = handoff(tmp_path, raw_candidate_bytes=raw)

    action = post._prepare_verified(trusted)
    assert action["candidate"] == post._manifest(
        "phase2_generate/phase2_generate_output.json", raw
    )
    assert action["dispatchAllowed"] is False


def test_patched_candidate_stops_before_locked_upstream_merger(
    tmp_path: Path,
) -> None:
    revisions = [
        {
            "scope": "coherence",
            "op": "append_sentence",
            "field": "core_mechanism",
            "value": "Reject stale parents.",
            "outcome": "applied",
            "delta_summary": "Added stale-parent handling.",
        },
        {
            "scope": "coherence",
            "op": "append_items",
            "field": "core_mechanism_steps",
            "value": ["replay"],
            "outcome": "applied",
            "delta_summary": "Added replay.",
        },
    ]
    trusted = handoff(tmp_path, revisions=revisions, blocking=True)
    with pytest.raises(post.P2RPostCoherenceError, match="locked upstream merger"):
        post._prepare_verified(trusted)
    assert not (tmp_path / "phase2_coherence" / "refined_candidate.json").exists()


@pytest.mark.parametrize("field", ["falsification_prediction", "compute_budget.max_calls"])
def test_no_patched_revision_is_locally_merged(tmp_path: Path, field: str) -> None:
    revision = {
        "scope": "coherence",
        "op": "replace",
        "field": field,
        "value": "weakened",
        "outcome": "applied",
        "delta_summary": "Unsafe mutation.",
    }
    with pytest.raises(post.P2RPostCoherenceError, match="locked upstream merger"):
        post._prepare_verified(handoff(tmp_path, revisions=[revision]))


def test_resume_rejects_any_untrusted_collision_evidence(tmp_path: Path) -> None:
    trusted = handoff(tmp_path)
    post._prepare_verified(trusted)
    (tmp_path / "phase3_collision").mkdir()
    with pytest.raises(post.P2RPostCoherenceError, match="not implemented"):
        post.resume_post_coherence(
            repository_root=tmp_path,
            skill_root=tmp_path,
            run_dir=tmp_path,
        )


def tool_event_bundle() -> tuple[list[object], dict[str, object]]:
    code = "print(3)"
    stdout = "3\n"
    envelope = {
        "protocol": coherence_host.P2R_PROTOCOL,
        "sandboxImage": coherence_host.P2R_SANDBOX_IMAGE,
        "command": ["python3", "-"],
        "scriptSha256": sha(code.encode()),
        "scriptSizeBytes": len(code.encode()),
        "exitCode": 0,
        "stdout": stdout,
        "stdoutSha256": sha(stdout.encode()),
        "stdoutSizeBytes": len(stdout.encode()),
        "stderr": "",
        "stderrSha256": sha(b""),
        "stderrSizeBytes": 0,
        "limits": {
            "scriptBytes": coherence_host.MAX_SCRIPT_BYTES,
            "streamBytes": coherence_host.MAX_STREAM_BYTES,
            "timeoutSeconds": coherence_host.EXEC_TIMEOUT_SECONDS,
            "visibleStreamBytes": coherence_host.MAX_VISIBLE_STREAM_BYTES,
        },
        "truncation": {"captureExceeded": False, "stderr": False, "stdout": False},
    }
    return (
        [
            ToolEvent(
                span_id="span-solver-1",
                id="tool-1",
                function="python",
                arguments={"code": code},
                result=json.dumps(envelope, sort_keys=True, separators=(",", ":")),
            ),
            SpanBeginEvent(
                span_id="span-python-1",
                id="span-python-1",
                parent_id="span-solver-1",
                type="tool",
                name="python",
            ),
            SandboxEvent(
                span_id="span-python-1",
                action="exec",
                cmd="python3 -",
                input=code,
                result=0,
                output=stdout,
            ),
        ],
        {"toolCallId": "tool-1", **envelope},
    )


def eval_log_artifacts(
    tmp_path: Path, output: dict[str, object], *, route_id: str = "route-1"
) -> tuple[bytes, bytes]:
    events, _ = tool_event_bundle()
    completion = json.dumps(output, ensure_ascii=False)
    sample = EvalSample(
        id="researchstudio-coherence",
        epoch=1,
        input="locked coherence input",
        target="",
        messages=[
            ChatMessageAssistant(
                content=completion,
                model=coherence_host.P2R_MODEL_ID,
                metadata={"modelmirrorRouteRunId": route_id},
            )
        ],
        output=ModelOutput.from_content(
            model=coherence_host.P2R_MODEL_ID, content=completion
        ),
        events=events,
    )
    log = EvalLog(
        status="success",
        eval=EvalSpec(
            created="2026-08-30T00:00:00Z",
            task="researchstudio-coherence",
            dataset=EvalDataset(name="fixed", samples=1),
            model=coherence_host.P2R_MODEL_ID,
            config=EvalConfig(),
        ),
        samples=[sample],
    )
    archive_path = tmp_path / "inspect-source.eval"
    write_eval_log(log, location=archive_path, format="eval")
    archive_bytes = archive_path.read_bytes()
    exported = read_eval_log(
        BytesIO(archive_bytes), format="eval", resolve_attachments="full"
    ).model_dump(mode="json", exclude_none=True)
    return coherence_host._json_bytes(exported), archive_bytes


def coherence_inputs(tmp_path: Path) -> tuple[coherence_host.P2RInputs, dict[str, object]]:
    value = candidate()
    candidate_bytes = post._canonical_bytes(value)
    output = coherence_output()
    output_bytes = coherence_host._json_bytes(output)
    blocking_bytes = coherence_host._json_bytes([])
    log_bytes, eval_archive = eval_log_artifacts(tmp_path, output)
    input_value = {
        "qualificationRunId": RUN_ID,
        "projectId": "rp_test",
        "literatureRunId": "lr_test",
        "bundleSha256": "1" * 64,
        "sourceCount": 4,
    }
    input_bytes = coherence_host._json_bytes(input_value)
    connector_bytes = b'{"ready":true}\n'
    phase2_bytes = b'{"phase":"phase2"}\n'
    prompt = b"locked prompt"
    select = b'{"selected":"C01"}\n'
    _, tool_receipt = tool_event_bundle()
    receipt = {
        "protocol": coherence_host.P2R_PROTOCOL,
        "phase": "researchstudio_phase2_coherence",
        "runId": RUN_ID,
        "previousReceiptSha256": sha(phase2_bytes),
        "modelId": coherence_host.P2R_MODEL_ID,
        "inspectVersion": "0.3.260",
        "sandboxImage": coherence_host.P2R_SANDBOX_IMAGE,
        "promptSha256": sha(prompt),
        "p2rInputReceiptSha256": sha(input_bytes),
        "p2rConnectorReceiptSha256": sha(connector_bytes),
        "v01Bundle": {
            "projectId": "rp_test",
            "literatureRunId": "lr_test",
            "bundleSha256": "1" * 64,
            "sourceCount": 4,
        },
        "inputArtifacts": {
            "phase2_select/phase2_select_output.json": sha(select),
            "phase2_generate/phase2_generate_output.json": sha(candidate_bytes),
        },
        "toolReceipts": [tool_receipt],
        "modelRouteRunIds": ["route-1"],
        "evalLogExport": {
            "inspectVersion": "0.3.260",
            "format": "eval",
            "headerOnly": False,
            "resolveAttachments": "full",
        },
        "blockingFindingCount": 0,
        "claimLevel": "qualification_only",
        "artifacts": {
            "phase2_coherence_output.json": {
                "sha256": sha(output_bytes),
                "sizeBytes": len(output_bytes),
            },
            "blocking_findings.json": {
                "sha256": sha(blocking_bytes),
                "sizeBytes": len(blocking_bytes),
            },
            "eval-log.json": {"sha256": sha(log_bytes), "sizeBytes": len(log_bytes)},
            "eval-log.eval": {
                "sha256": sha(eval_archive),
                "sizeBytes": len(eval_archive),
            },
        },
    }
    coherence = tmp_path / "phase2_coherence"
    coherence.mkdir()
    for name, data in {
        "phase2_coherence_output.json": output_bytes,
        "blocking_findings.json": blocking_bytes,
        "eval-log.json": log_bytes,
        "eval-log.eval": eval_archive,
        "execution_receipt.json": coherence_host._json_bytes(receipt),
    }.items():
        (coherence / name).write_bytes(data)
    return (
        coherence_host.P2RInputs(
            run_dir=tmp_path,
            input_receipt=input_bytes,
            connector_receipt=connector_bytes,
            phase2_receipt=phase2_bytes,
            prompt=prompt,
            select=select,
            candidate=candidate_bytes,
            compose=tmp_path / "unused-compose.yml",
        ),
        receipt,
    )


def test_handoff_rederives_tool_and_route_facts_from_eval_log(tmp_path: Path) -> None:
    inputs, receipt = coherence_inputs(tmp_path)
    assert coherence_host._validated_coherence_handoff(inputs).run_id == RUN_ID

    forged = dict(receipt)
    forged["modelRouteRunIds"] = ["forged-route"]
    path = tmp_path / "phase2_coherence" / "execution_receipt.json"
    path.write_bytes(coherence_host._json_bytes(forged))
    with pytest.raises(coherence_host.P2RHostError, match="EvalLog"):
        coherence_host._validated_coherence_handoff(inputs)


def test_handoff_rejects_json_rewritten_against_unchanged_eval_archive(
    tmp_path: Path,
) -> None:
    inputs, receipt = coherence_inputs(tmp_path)
    coherence = tmp_path / "phase2_coherence"
    log_path = coherence / "eval-log.json"
    forged_log = json.loads(log_path.read_bytes())
    forged_log["status"] = "error"
    forged_bytes = coherence_host._json_bytes(forged_log)
    log_path.write_bytes(forged_bytes)
    receipt["artifacts"]["eval-log.json"] = {
        "sha256": sha(forged_bytes),
        "sizeBytes": len(forged_bytes),
    }
    (coherence / "execution_receipt.json").write_bytes(
        coherence_host._json_bytes(receipt)
    )

    with pytest.raises(coherence_host.P2RHostError, match="original Inspect archive"):
        coherence_host._validated_coherence_handoff(inputs)


@pytest.mark.parametrize(
    "mutation",
    ["missing", "additional", "symlink", "tampered", "malformed_rebound", "oversized"],
)
def test_handoff_rejects_unsafe_or_unreadable_eval_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    inputs, receipt = coherence_inputs(tmp_path)
    coherence = tmp_path / "phase2_coherence"
    archive = coherence / "eval-log.eval"

    if mutation == "missing":
        archive.unlink()
    elif mutation == "additional":
        (coherence / "second.eval").write_bytes(archive.read_bytes())
    elif mutation == "symlink":
        outside = tmp_path / "outside.eval"
        outside.write_bytes(archive.read_bytes())
        archive.unlink()
        archive.symlink_to(outside)
    elif mutation == "tampered":
        data = bytearray(archive.read_bytes())
        data[len(data) // 2] ^= 1
        archive.write_bytes(data)
    elif mutation == "malformed_rebound":
        data = b"not-an-inspect-eval-archive"
        archive.write_bytes(data)
        receipt["artifacts"]["eval-log.eval"] = {
            "sha256": sha(data),
            "sizeBytes": len(data),
        }
        (coherence / "execution_receipt.json").write_bytes(
            coherence_host._json_bytes(receipt)
        )
    elif mutation == "oversized":
        monkeypatch.setattr(
            coherence_host, "MAX_EVAL_LOG_BYTES", len(archive.read_bytes()) - 1
        )

    with pytest.raises(coherence_host.P2RHostError):
        coherence_host._validated_coherence_handoff(inputs)


def test_h1a_keeps_all_19_contracts_inactive_and_version_unchanged() -> None:
    assert len(contracts.POST_COHERENCE_PHASE_CONTRACTS) == 19
    assert all(
        item["activated"] is False and item["tools"] is False
        for item in contracts.POST_COHERENCE_PHASE_CONTRACTS.values()
    )
    boundary = json.loads(
        (MODULE_ROOT / "module-boundary.json").read_text(encoding="utf-8")
    )
    assert boundary["moduleVersion"] == "0.3.0-v0.1"
    assert boundary["qualificationModes"][0]["networkPolicy"]["postCoherence"] == "inactive"
