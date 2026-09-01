from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from inspect_ai import Task, eval
from inspect_ai.dataset import Sample
from inspect_ai.event import SandboxEvent, SpanBeginEvent, ToolEvent
from inspect_ai.model import (
    ChatMessageAssistant,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    GenerateConfig,
    ModelOutput,
    get_model,
)
from inspect_ai.solver import generate, use_tools
from inspect_ai.tool import ToolCall, ToolInfo, ToolParams
from inspect_ai.util import SandboxEnvironmentSpec

import ai_research_worker.p2r_host as host
from ai_research_worker import p2r_connectors as connectors
from ai_research_worker import p2r_phase_contracts as phase_contracts
from ai_research_worker.p2r_host import (
    EXEC_TIMEOUT_SECONDS,
    MAX_SCRIPT_BYTES,
    MAX_STREAM_BYTES,
    MAX_VISIBLE_STREAM_BYTES,
    ModelMirrorBridgeAPI,
    P2R_COHERENCE_PHASE,
    P2RHostError,
    P2R_PROTOCOL,
    P2R_SANDBOX_IMAGE,
    _atomic_deliver,
    _bridge_runtime_configuration,
    _blocking_findings,
    _modelmirror_route_run_ids,
    _phase_artifact_messages,
    _validate_coherence_schema,
    _validate_coherence_execution,
    _validated_connector_receipt,
    _validated_input_receipt,
    _validated_phase2_receipt_chain,
    _validated_tool_receipts,
    _validated_loopback_endpoint,
    p2r_python,
)
from ai_research_worker.p2r_input_gate import (
    LOCKED_V01_BUNDLE,
    LOCKED_V01_BUNDLE_SHA256,
    LOCKED_V01_RESEARCH_QUESTION,
    LOCKED_V01_SOURCE_COUNT,
    P2R_INPUT_PROTOCOL,
)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


BRIDGE_PROMPT = b"fixed prompt"
BRIDGE_RUN_ID = "p2rq_" + "a" * 32
BRIDGE_PREVIOUS_SHA256 = "b" * 64
BRIDGE_ARTIFACTS = [
    ("phase2_select/phase2_select_output.json", b'{"selected":"C01"}'),
    ("phase2_generate/phase2_generate_output.json", b'{"candidate":{}}'),
]


def bridge_api() -> ModelMirrorBridgeAPI:
    return ModelMirrorBridgeAPI(
        base_url="http://127.0.0.1:8000/api/ai-research/v1",
        token="test-token",
        phase=P2R_COHERENCE_PHASE,
        prompt=BRIDGE_PROMPT,
        qualification_run_id=BRIDGE_RUN_ID,
        previous_receipt_sha256=BRIDGE_PREVIOUS_SHA256,
        artifacts=BRIDGE_ARTIFACTS,
    )


def bridge_artifact_messages() -> list[ChatMessageUser]:
    return _phase_artifact_messages(
        phase=P2R_COHERENCE_PHASE,
        qualification_run_id=BRIDGE_RUN_ID,
        previous_receipt_sha256=BRIDGE_PREVIOUS_SHA256,
        artifacts=BRIDGE_ARTIFACTS,
    )


def test_p2r_bridge_credentials_never_fall_back_to_literature_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_RESEARCH_S2S_TOKEN", "literature-token")
    monkeypatch.delenv("AI_RESEARCH_P2R_S2S_TOKEN", raising=False)
    monkeypatch.setenv("AI_RESEARCH_HYPOTHESIS_MODEL_ID", "openai/gpt-5.4")

    with pytest.raises(P2RHostError, match="P2R-specific"):
        _bridge_runtime_configuration()

    monkeypatch.setenv("AI_RESEARCH_P2R_S2S_TOKEN", "p2r-token")
    endpoint, token = _bridge_runtime_configuration()
    assert endpoint == "http://127.0.0.1:8000/api/ai-research/v1"
    assert token == "p2r-token"


def valid_coherence(*, code: str = "print(3)", stdout: str = "3\n") -> dict[str, object]:
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
                "execution": {"mode": "executed", "script": code, "output": stdout},
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
        "verdict": "pass",
        "unrepaired": [],
        "applied_revisions": [],
    }


def locked_input_receipt(
    *,
    qualification_run_id: str = "p2rq_" + "a" * 32,
    issued_at: str | None = None,
) -> dict[str, object]:
    return {
        "protocol": P2R_INPUT_PROTOCOL,
        "status": "verified",
        "qualificationRunId": qualification_run_id,
        "issuedAt": issued_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "projectId": LOCKED_V01_BUNDLE.project_id,
        "literatureRunId": LOCKED_V01_BUNDLE.run_id,
        "title": "A7 LDR 1.10.6 citation integrity acceptance",
        "researchQuestion": LOCKED_V01_RESEARCH_QUESTION,
        "researchQuestionSha256": sha(LOCKED_V01_RESEARCH_QUESTION),
        "sourceCount": LOCKED_V01_SOURCE_COUNT,
        "bundleSha256": LOCKED_V01_BUNDLE_SHA256,
        "lockedProfile": {
            "researchYamlSha256": LOCKED_V01_BUNDLE.research_yaml_sha256,
            "manifestSha256": LOCKED_V01_BUNDLE.manifest_sha256,
            "receiptSha256": LOCKED_V01_BUNDLE.receipt_sha256,
            "sourceLockSha256": LOCKED_V01_BUNDLE.source_lock_sha256,
        },
        "handoff": {
            "mode": "eligibility_and_exact_research_question",
            "upstreamPhase0RetrievalRequired": True,
            "v01ReviewInjected": False,
        },
        "scientificClaim": "none",
        "claimLevel": "qualification_only",
    }


@pytest.mark.parametrize(
    "mutation", ["bundle", "question", "handoff", "run_identity", "issued_at", "extra"]
)
def test_input_receipt_must_bind_exact_verified_v01_handoff(mutation: str) -> None:
    receipt = locked_input_receipt()
    if mutation == "bundle":
        receipt["bundleSha256"] = "0" * 64
    elif mutation == "question":
        receipt["researchQuestion"] = "A substituted research question"
    elif mutation == "handoff":
        receipt["handoff"] = {"mode": "review_injected"}
    elif mutation == "run_identity":
        receipt["qualificationRunId"] = "p2rq_replayed"
    elif mutation == "issued_at":
        receipt["issuedAt"] = "not-a-timestamp"
    else:
        receipt["untrusted"] = True
    encoded = (json.dumps(receipt, ensure_ascii=False) + "\n").encode("utf-8")
    with pytest.raises(P2RHostError, match="input"):
        _validated_input_receipt(encoded)


def test_input_receipt_accepts_the_exact_locked_v01_handoff() -> None:
    encoded = (json.dumps(locked_input_receipt(), ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    assert _validated_input_receipt(encoded)["bundleSha256"] == LOCKED_V01_BUNDLE_SHA256


def write_connector_evidence(
    run_dir: Path,
    *,
    qualification_run_id: str = "p2rq_" + "a" * 32,
    issued_at: str | None = None,
    qualified_at: str | None = None,
) -> Path:
    input_receipt = locked_input_receipt(
        qualification_run_id=qualification_run_id, issued_at=issued_at
    )
    input_bytes = (
        json.dumps(input_receipt, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    (run_dir / "p2r-input-receipt.json").write_bytes(input_bytes)
    evidence = run_dir / "connector-qualification"
    evidence.mkdir()
    artifacts: dict[str, dict[str, object]] = {}
    facts: dict[str, dict[str, object]] = {}
    for name in connectors.CONNECTOR_ORDER:
        hits = [
            {
                "title": f"{name} result",
                "paper_url": f"https://example.org/{name}",
                "source": name,
            }
        ]
        data = (json.dumps(hits, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        filename = f"{name}-hits.json"
        (evidence / filename).write_bytes(data)
        artifacts[filename] = {"sha256": hashlib.sha256(data).hexdigest(), "sizeBytes": len(data)}
        facts[name] = {
            "status": "ready",
            "hitCount": len(hits),
            "authMode": "credentials_present" if name == "openreview" else "anonymous",
            "artifact": filename,
            "probeAttempts": [{"sequence": 1, "outcome": "ready"}],
        }
        if name == "openreview":
            facts[name]["successfulVenueCount"] = 1
    receipt = {
        "protocol": connectors.PROTOCOL,
        "status": "ready",
        "degraded": False,
        "qualificationRunId": input_receipt["qualificationRunId"],
        "p2rInputReceiptSha256": hashlib.sha256(input_bytes).hexdigest(),
        "inputIssuedAt": input_receipt["issuedAt"],
        "qualifiedAt": qualified_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "query": connectors.FIXED_QUERY,
        "asOf": connectors.QUALIFICATION_AS_OF.isoformat().replace("+00:00", "Z"),
        "researchStudioCommit": connectors.RESEARCHSTUDIO_COMMIT,
        "researchStudioReuseRoot": {
            "fileCount": phase_contracts.RESEARCHSTUDIO_REUSE_ROOT_FILE_COUNT,
            "totalBytes": phase_contracts.RESEARCHSTUDIO_REUSE_ROOT_TOTAL_BYTES,
            "aggregateSha256": (
                phase_contracts.RESEARCHSTUDIO_REUSE_ROOT_AGGREGATE_SHA256
            ),
        },
        "pythonVersion": connectors.PYTHON_VERSION,
        "baseImage": connectors.BASE_IMAGE,
        "retryPolicy": connectors.RETRY_POLICY,
        "qualifierSha256": hashlib.sha256(
            Path(connectors.__file__).read_bytes()
        ).hexdigest(),
        "requirementsLockSha256": connectors.REQUIREMENTS_LOCK_SHA256,
        "scriptSha256": connectors.SCRIPT_HASHES,
        "packageVersions": connectors.PACKAGE_VERSIONS,
        "connectors": facts,
        "artifacts": artifacts,
        "claimLevel": "qualification_only",
    }
    (evidence / "connector-receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return evidence


def write_phase_chain(run_dir: Path, input_bytes: bytes) -> bytes:
    run_id = "p2rq_" + "a" * 32

    def write_artifact(path: str, data: bytes) -> None:
        target = run_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def manifest(paths: list[str]) -> dict[str, dict[str, object]]:
        return phase_contracts.build_raw_artifact_manifest(
            run_dir,
            paths,
            allowed_paths=paths,
        )

    def receipt(
        phase: str,
        previous: bytes,
        inputs: list[str],
        outputs: list[str],
        phase_evidence: dict[str, object],
    ) -> dict[str, object]:
        action_specs = {
            "phase0": (("phase0.runtime", "scripts/run.py"),),
            "phase1": (("phase1.navigator", "scripts/next_step.py"),),
            "phase2": (
                ("phase2.prepare", "scripts/run.py"),
                ("phase2.navigator", "scripts/next_step.py"),
            ),
        }[phase]
        validator_specs = {
            "phase0": (("phase0.navigator", "scripts/next_step.py"),),
            "phase1": (("phase1.navigator", "scripts/next_step.py"),),
            "phase2": (
                (
                    "phase2.subpattern-citation-consistency",
                    "scripts/validators/subpattern_citation_consistency.py",
                ),
                (
                    "phase2.alias-collateral-coverage",
                    "scripts/validators/alias_collateral_coverage.py",
                ),
                ("phase2.user-direction", "scripts/validators/user_direction.py"),
            ),
        }[phase]
        return {
            "protocol": phase_contracts.P2R_PHASE_RECEIPT_PROTOCOL,
            "schemaVersion": phase_contracts.P2R_PHASE_RECEIPT_SCHEMA_VERSION,
            "runId": run_id,
            "phase": phase,
            "attempt": 1,
            "issuedAt": "2026-08-30T01:00:00Z",
            "upstreamCommit": phase_contracts.RESEARCHSTUDIO_COMMIT,
            "reuseRootAggregateSha256": (
                phase_contracts.RESEARCHSTUDIO_REUSE_ROOT_AGGREGATE_SHA256
            ),
            "previousReceiptSha256": hashlib.sha256(previous).hexdigest(),
            "lockedAssetManifestSha256": (
                phase_contracts.LOCKED_ASSET_MANIFEST_SHA256
            ),
            "inputArtifacts": manifest(inputs),
            "deterministicActions": [
                {
                    "id": action_id,
                    "script": script,
                    "scriptSha256": phase_contracts.LOCKED_ASSET_SHA256[script],
                    "exitCode": 0,
                    "stdoutSha256": "b" * 64,
                    "stderrSha256": "c" * 64,
                    "truncated": False,
                }
                for action_id, script in action_specs
            ],
            "validatorResults": [
                {
                    "id": validator_id,
                    "script": script,
                    "scriptSha256": phase_contracts.LOCKED_ASSET_SHA256[script],
                    "exitCode": 0,
                    "status": "passed",
                    "findings": [
                        {
                            "validator": validator_id,
                            "severity": "pass",
                            "message": "fixed phase contract passed",
                        }
                    ],
                }
                for validator_id, script in validator_specs
            ],
            "outputArtifacts": manifest(outputs),
            "navigator": {
                "nextStepSha256": phase_contracts.LOCKED_ASSET_SHA256[
                    "scripts/next_step.py"
                ],
                "beforeEmitSha256": "d" * 64,
                "afterEmitSha256": "e" * 64,
                "state": f"{phase} complete",
                "step": "next fixed phase",
                "type": "llm_subagent",
            },
            "phaseEvidence": phase_evidence,
            "rawUpstreamState": {
                "phase0": "phase0_complete",
                "phase1": "phase1_proceed",
                "phase2": "phase2_complete",
            }[phase],
            "scientificClaim": "none",
            "claimLevel": "qualification_only",
        }

    phase0_inputs = [
        "p2r-input-receipt.json",
        "connector-qualification/connector-receipt.json",
        *(f"connector-qualification/{name}-hits.json" for name in connectors.CONNECTOR_ORDER),
    ]
    phase0_outputs = [
        "phase0/.lit_grounding_mode",
        "phase0/fulltext_cache.json",
        "phase0/lit_results.json",
        "phase0/lit_table.md",
        "phase0/user_query.txt",
    ]
    write_artifact("phase0/.lit_grounding_mode", b"real")
    write_artifact("phase0/fulltext_cache.json", b"{}\n")
    write_artifact("phase0/lit_results.json", b"[]\n")
    write_artifact("phase0/lit_table.md", b"| paper_id |\n")
    write_artifact("phase0/user_query.txt", b"fixed AI research question\n")
    connector_paths = {
        name: f"connector-qualification/{name}-hits.json"
        for name in connectors.CONNECTOR_ORDER
    }
    phase0_value = receipt(
        "phase0",
        input_bytes,
        phase0_inputs,
        phase0_outputs,
        {
            "literatureGroundingMode": "real",
            "connectorsDegraded": False,
            "connectors": {
                name: {
                    "status": "ready",
                    "attemptCount": 1,
                    "artifactPaths": [path],
                }
                for name, path in connector_paths.items()
            },
        },
    )
    phase0_path = phase_contracts.write_phase_receipt(
        run_dir,
        phase0_value,
        previous_receipt_bytes=input_bytes,
    )
    phase0_bytes = phase0_path.read_bytes()

    write_artifact("phase1/phase1_output.json", b'{"state":"proceed"}\n')
    phase1_inputs = [*phase0_inputs, *phase0_outputs, "phase0/phase-receipt.json"]
    phase1_value = receipt(
        "phase1",
        phase0_bytes,
        phase1_inputs,
        ["phase1/phase1_output.json"],
        {"state": "proceed", "outputArtifactPath": "phase1/phase1_output.json"},
    )
    phase1_path = phase_contracts.write_phase_receipt(
        run_dir,
        phase1_value,
        previous_receipt_bytes=phase0_bytes,
    )
    phase1_bytes = phase1_path.read_bytes()

    write_artifact("phase2_select/phase2_select_output.json", b'{"selected":"C01"}\n')
    write_artifact("phase2_generate/phase2_generate_output.json", b'{"candidate":{}}\n')
    write_artifact("phase2_generate/closest_abstracts.json", b"[]\n")
    phase2_inputs = [
        *phase1_inputs,
        "phase1/phase1_output.json",
        "phase1/phase-receipt.json",
    ]
    phase2_value = receipt(
        "phase2",
        phase1_bytes,
        phase2_inputs,
        [
            "phase2_generate/closest_abstracts.json",
            "phase2_generate/phase2_generate_output.json",
            "phase2_select/phase2_select_output.json",
        ],
        {
            "selectionState": "complete",
            "generationState": "complete",
            "citationGatePassed": True,
        },
    )
    phase2_path = phase_contracts.write_phase_receipt(
        run_dir,
        phase2_value,
        previous_receipt_bytes=phase1_bytes,
    )
    return phase2_path.read_bytes()


def test_connector_receipt_requires_all_four_hash_bound_ready_profiles(tmp_path: Path) -> None:
    write_connector_evidence(tmp_path)
    assert _validated_connector_receipt(tmp_path)


def test_phase2_receipt_chain_binds_same_run_and_detects_middle_tamper(
    tmp_path: Path,
) -> None:
    write_connector_evidence(tmp_path)
    input_bytes = (tmp_path / "p2r-input-receipt.json").read_bytes()
    expected_phase2 = write_phase_chain(tmp_path, input_bytes)
    actual_phase2 = _validated_phase2_receipt_chain(
        tmp_path,
        input_receipt=input_bytes,
        qualification_run_id="p2rq_" + "a" * 32,
    )
    assert actual_phase2 == expected_phase2

    phase1_path = tmp_path / "phase1" / "phase-receipt.json"
    original = phase1_path.read_bytes()
    tampered = original.replace(
        b"phase1_proceed",
        b"phase1_proceef",
        1,
    )
    assert tampered != original
    phase1_path.write_bytes(tampered)
    with pytest.raises(P2RHostError, match="Phase 0-2 receipt chain"):
        _validated_phase2_receipt_chain(
            tmp_path,
            input_receipt=input_bytes,
            qualification_run_id="p2rq_" + "a" * 32,
        )


def test_connector_receipt_accepts_exact_semanticscholar_429_recovery(
    tmp_path: Path,
) -> None:
    evidence = write_connector_evidence(tmp_path)
    receipt_path = evidence / "connector-receipt.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    receipt["connectors"]["semanticscholar"]["probeAttempts"] = [
        {
            "sequence": 1,
            "outcome": "failed",
            "error": {
                "type": "HTTPError",
                "httpStatus": 429,
                "category": "rate_limited",
            },
            "backoffSeconds": 7,
        },
        {"sequence": 2, "outcome": "ready"},
    ]
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    assert _validated_connector_receipt(tmp_path)


def test_connector_receipt_cannot_be_replayed_into_another_fresh_run(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original"
    replay = tmp_path / "replay"
    original.mkdir()
    replay.mkdir()
    evidence = write_connector_evidence(original)
    replay_input = locked_input_receipt(qualification_run_id="p2rq_" + "b" * 32)
    (replay / "p2r-input-receipt.json").write_text(
        json.dumps(replay_input, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    shutil.copytree(evidence, replay / "connector-qualification")
    with pytest.raises(P2RHostError, match="connector receipt"):
        _validated_connector_receipt(replay)


def test_connector_receipt_must_be_fresh(tmp_path: Path) -> None:
    old = datetime.now(timezone.utc) - timedelta(hours=7)
    timestamp = old.isoformat().replace("+00:00", "Z")
    write_connector_evidence(tmp_path, issued_at=timestamp, qualified_at=timestamp)
    with pytest.raises(P2RHostError, match="not fresh"):
        _validated_connector_receipt(tmp_path)


@pytest.mark.parametrize(
    "mutation",
    [
        "degraded",
        "artifact",
        "auth",
        "extra",
        "fact_extra",
        "ready_error",
        "zero_hits",
        "retry_non_429",
        "openreview_retry",
    ],
)
def test_connector_receipt_fails_closed_on_degraded_or_detached_evidence(
    tmp_path: Path, mutation: str
) -> None:
    evidence = write_connector_evidence(tmp_path)
    receipt_path = evidence / "connector-receipt.json"
    receipt = json.loads(receipt_path.read_text("utf-8"))
    if mutation == "degraded":
        receipt["status"] = "degraded"
        receipt["degraded"] = True
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    elif mutation == "artifact":
        (evidence / "arxiv-hits.json").write_bytes(b"[]\n")
    elif mutation == "auth":
        receipt["connectors"]["openreview"]["authMode"] = "anonymous"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    elif mutation == "extra":
        (evidence / "untracked.json").write_text("{}", encoding="utf-8")
    elif mutation == "fact_extra":
        receipt["connectors"]["arxiv"]["error"] = {"type": "contradiction"}
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    elif mutation == "ready_error":
        receipt["connectors"]["semanticscholar"]["error"] = {
            "type": "HTTPError",
            "httpStatus": 429,
        }
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    elif mutation == "zero_hits":
        receipt["connectors"]["openalex"]["hitCount"] = 0
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    elif mutation == "retry_non_429":
        receipt["connectors"]["semanticscholar"]["probeAttempts"] = [
            {
                "sequence": 1,
                "outcome": "failed",
                "error": {
                    "type": "HTTPError",
                    "httpStatus": 500,
                    "category": "upstream_server",
                },
                "backoffSeconds": 7,
            },
            {"sequence": 2, "outcome": "ready"},
        ]
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    else:
        receipt["connectors"]["openreview"]["probeAttempts"] = [
            {
                "sequence": 1,
                "outcome": "failed",
                "error": {
                    "type": "HTTPError",
                    "httpStatus": 429,
                    "category": "rate_limited",
                },
                "backoffSeconds": 7,
            },
            {"sequence": 2, "outcome": "ready"},
        ]
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(P2RHostError, match="P2R|OpenReview"):
        _validated_connector_receipt(tmp_path)


def sample_with_receipt(*, code: str = "print(3)", stdout: str = "3\n"):
    envelope = {
        "protocol": P2R_PROTOCOL,
        "sandboxImage": P2R_SANDBOX_IMAGE,
        "command": ["python3", "-"],
        "scriptSha256": sha(code),
        "scriptSizeBytes": len(code.encode("utf-8")),
        "exitCode": 0,
        "stdout": stdout,
        "stdoutSha256": sha(stdout),
        "stdoutSizeBytes": len(stdout.encode("utf-8")),
        "stderr": "",
        "stderrSha256": sha(""),
        "stderrSizeBytes": 0,
        "limits": {
            "timeoutSeconds": EXEC_TIMEOUT_SECONDS,
            "scriptBytes": MAX_SCRIPT_BYTES,
            "streamBytes": MAX_STREAM_BYTES,
            "visibleStreamBytes": MAX_VISIBLE_STREAM_BYTES,
        },
        "truncation": {"stdout": False, "stderr": False, "captureExceeded": False},
    }
    tool = ToolEvent(
        span_id="span-solver-1",
        id="tool-1",
        function="python",
        arguments={"code": code},
        result=json.dumps(envelope, sort_keys=True, separators=(",", ":")),
    )
    tool_span = SpanBeginEvent(
        span_id="span-python-1",
        id="span-python-1",
        parent_id="span-solver-1",
        type="tool",
        name="python",
    )
    sandbox = SandboxEvent(
        span_id="span-python-1",
        action="exec",
        cmd="python3 -",
        input=code,
        result=0,
        output=stdout,
    )
    return type("Sample", (), {"events": [tool, tool_span, sandbox]})(), envelope


def test_validated_receipt_binds_tool_script_and_sandbox_event() -> None:
    sample, envelope = sample_with_receipt()
    receipts = _validated_tool_receipts(sample)
    assert receipts == [{"toolCallId": "tool-1", **envelope}]


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1:8000/api/ai-research/v1",
        "http://bridge.example:8000/api/ai-research/v1",
        "http://127.0.0.1:8000/api/ai-research/v1?redirect=1",
        "http://127.0.0.1:8000/other",
        "http://user:secret@127.0.0.1:8000/api/ai-research/v1",
    ],
)
def test_model_bridge_endpoint_rejects_non_exact_loopback_urls(endpoint: str) -> None:
    with pytest.raises(P2RHostError, match="loopback"):
        _validated_loopback_endpoint(endpoint)


def test_route_run_identity_is_required_for_every_p2r_model_turn() -> None:
    valid = ChatMessageAssistant(
        content="first",
        model="openai/gpt-5.4",
        metadata={"modelmirrorRouteRunId": "chatrun_first"},
    )
    missing = ChatMessageAssistant(content="second", model="openai/gpt-5.4")
    sample = type("Sample", (), {"messages": [valid]})()
    assert _modelmirror_route_run_ids(sample) == ["chatrun_first"]
    sample.messages.append(missing)
    with pytest.raises(P2RHostError, match="route run identity"):
        _modelmirror_route_run_ids(sample)


def test_bridge_adapter_preserves_a_bounded_python_tool_call_and_route_identity(
    monkeypatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://127.0.0.1:8000/api/ai-research/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["X-ModelMirror-P2R-Phase"] == P2R_COHERENCE_PHASE
        body = json.loads(request.content)
        assert body["temperature"] == 0.2
        assert body["max_tokens"] == 30_000
        assert [message["role"] for message in body["messages"]] == [
            "system",
            "user",
            "user",
        ]
        return httpx.Response(
            200,
            headers={"X-ModelMirror-Route-Run-Id": "chatrun_adapter"},
            json={
                "model": "openai/gpt-5.4",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_python",
                                    "type": "function",
                                    "function": {
                                        "name": "python",
                                        "arguments": json.dumps({"code": "print(1)"}),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            },
        )

    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        assert kwargs["trust_env"] is False
        assert kwargs["follow_redirects"] is False
        return real_client(
            timeout=kwargs["timeout"],
            trust_env=False,
            follow_redirects=False,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(host.httpx, "AsyncClient", client_factory)
    api = bridge_api()
    tool_info = ToolInfo(
        name="python",
        description="Execute a bounded Python dry-run in the isolated P2R sandbox.",
        parameters=ToolParams(
            properties={
                "code": {
                    "type": "string",
                    "description": "A stdlib-only Python script used to test the candidate procedure.",
                }
            },
            required=["code"],
            additionalProperties=False,
        ),
    )
    artifact_messages = bridge_artifact_messages()
    output = asyncio.run(
        api.generate(
            [
                ChatMessageSystem(content=BRIDGE_PROMPT.decode("utf-8")),
                *artifact_messages,
            ],
            [tool_info],
            "auto",
            GenerateConfig(max_tokens=30_000, temperature=0.2),
        )
    )
    message = output.message
    assert isinstance(message, ChatMessageAssistant)
    assert message.metadata == {"modelmirrorRouteRunId": "chatrun_adapter"}
    assert message.tool_calls and message.tool_calls[0].function == "python"
    assert message.tool_calls[0].arguments == {"code": "print(1)"}


def test_phase_artifact_messages_are_canonical_chunked_and_utf8_bound() -> None:
    select = ("研究🧪" + "x" * 200_005).encode("utf-8")
    candidate = b'{"candidate":{}}'
    messages = _phase_artifact_messages(
        phase=P2R_COHERENCE_PHASE,
        qualification_run_id="p2rq_" + "a" * 32,
        previous_receipt_sha256="b" * 64,
        artifacts=[
            ("phase2_select/phase2_select_output.json", select),
            ("phase2_generate/phase2_generate_output.json", candidate),
        ],
    )
    assert len(messages) == 4
    envelopes = [json.loads(message.content) for message in messages]
    assert all(
        message.content
        == json.dumps(
            envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for message, envelope in zip(messages, envelopes, strict=True)
    )
    select_chunks = [
        envelope["artifact"]
        for envelope in envelopes
        if envelope["artifact"]["path"]
        == "phase2_select/phase2_select_output.json"
    ]
    assert [chunk["chunkIndex"] for chunk in select_chunks] == [0, 1, 2]
    assert all(chunk["chunkCount"] == 3 for chunk in select_chunks)
    assert all(chunk["sha256"] == hashlib.sha256(select).hexdigest() for chunk in select_chunks)
    assert all(chunk["sizeBytes"] == len(select) for chunk in select_chunks)
    assert b"".join(chunk["content"].encode("utf-8") for chunk in select_chunks) == select


def test_bridge_adapter_rejects_content_only_initial_and_repeated_finalize_tool(
    monkeypatch,
) -> None:
    responses = [
        {
            "model": "openai/gpt-5.4",
            "choices": [
                {
                    "message": {
                        "content": '{"execution":{"mode":"executed"}}',
                        "tool_calls": [],
                    }
                }
            ],
        },
        {
            "model": "openai/gpt-5.4",
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_again",
                                "type": "function",
                                "function": {
                                    "name": "python",
                                    "arguments": '{"code":"print(2)"}',
                                },
                            }
                        ],
                    }
                }
            ],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"X-ModelMirror-Route-Run-Id": "chatrun_stage"},
            json=responses.pop(0),
        )

    real_client = httpx.AsyncClient

    def client_factory(**kwargs):
        return real_client(
            timeout=kwargs["timeout"],
            trust_env=False,
            follow_redirects=False,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(host.httpx, "AsyncClient", client_factory)
    api = bridge_api()
    tool_info = ToolInfo(
        name="python",
        description="Execute a bounded Python dry-run in the isolated P2R sandbox.",
        parameters=ToolParams(
            properties={"code": {"type": "string"}},
            required=["code"],
            additionalProperties=False,
        ),
    )
    artifacts = bridge_artifact_messages()
    with pytest.raises(P2RHostError, match="exactly one initial Python call"):
        asyncio.run(
            api.generate(
                [ChatMessageSystem(content=BRIDGE_PROMPT.decode("utf-8")), *artifacts],
                [tool_info],
                "auto",
                GenerateConfig(max_tokens=30_000, temperature=0.2),
            )
        )
    code = "print(1)"
    _, envelope = sample_with_receipt(code=code, stdout="1\n")
    with pytest.raises(P2RHostError, match="repeated a coherence tool call"):
        asyncio.run(
            api.generate(
                [
                    ChatMessageSystem(content=BRIDGE_PROMPT.decode("utf-8")),
                    *artifacts,
                    ChatMessageAssistant(
                        content="",
                        tool_calls=[
                            ToolCall(
                                id="call_python",
                                function="python",
                                arguments={"code": code},
                            )
                        ],
                    ),
                    ChatMessageTool(
                        content=json.dumps(envelope, sort_keys=True, separators=(",", ":")),
                        tool_call_id="call_python",
                        function="python",
                    ),
                ],
                [tool_info],
                "auto",
                GenerateConfig(max_tokens=30_000, temperature=0.2),
            )
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "script",
        "stream",
        "forged_stream",
        "failed_exit",
        "extra_field",
        "truncation",
        "sandbox",
        "wrong_span",
    ],
)
def test_receipt_tampering_and_missing_provenance_fail_closed(mutation: str) -> None:
    sample, _ = sample_with_receipt()
    tool = sample.events[0]
    envelope = json.loads(tool.result)
    if mutation == "script":
        envelope["scriptSha256"] = "0" * 64
    elif mutation == "stream":
        envelope["stdoutSha256"] = "0" * 64
    elif mutation == "forged_stream":
        envelope["stdout"] = "forged\n"
        envelope["stdoutSha256"] = sha("forged\n")
        envelope["stdoutSizeBytes"] = len(b"forged\n")
    elif mutation == "failed_exit":
        envelope["exitCode"] = 1
        sample.events[2].result = 1
    elif mutation == "extra_field":
        envelope["untrusted"] = True
    elif mutation == "truncation":
        envelope["truncation"]["stdout"] = True
    elif mutation == "sandbox":
        sample.events.pop()
    else:
        sample.events[2].span_id = "different-span"
    tool.result = json.dumps(envelope, sort_keys=True, separators=(",", ":"))
    with pytest.raises(P2RHostError):
        _validated_tool_receipts(sample)


def test_model_authored_executed_claim_without_matching_receipt_fails() -> None:
    value = {
        "trace_report": {
            "dry_run": {
                "execution": {
                    "mode": "executed",
                    "script": "print(4)",
                    "output": "4\n",
                }
            }
        }
    }
    with pytest.raises(P2RHostError, match="successful trusted receipt"):
        _validate_coherence_execution(value, [])


def test_coherence_schema_rejects_minimal_or_inconsistent_success_claims() -> None:
    assert _validate_coherence_schema(valid_coherence())["verdict"] == "pass"

    with pytest.raises(P2RHostError, match="trace_report"):
        _validate_coherence_schema(
            {
                "trace_report": {"dry_run": {"execution": {"mode": "executed"}}},
                "verdict": "pass",
                "unrepaired": [],
                "applied_revisions": [],
            }
        )
    patched_without_revision = valid_coherence()
    patched_without_revision["verdict"] = "patched"
    with pytest.raises(P2RHostError, match="verdict and applied revisions"):
        _validate_coherence_schema(patched_without_revision)


def test_coherence_execution_rejects_failed_or_forged_receipt() -> None:
    code = "print(3)"
    value = valid_coherence(code=code, stdout="3\n")
    _, envelope = sample_with_receipt(code=code, stdout="3\n")
    receipt = {"toolCallId": "tool-1", **envelope}
    _validate_coherence_execution(value, [receipt])

    failed = dict(receipt)
    failed["exitCode"] = 1
    with pytest.raises(P2RHostError, match="successful trusted receipt"):
        _validate_coherence_execution(value, [failed])

    forged = dict(receipt)
    forged["stdout"] = "forged\n"
    with pytest.raises(P2RHostError, match="trusted tool receipt"):
        _validate_coherence_execution(value, [forged])


def test_bridge_revalidates_canonical_artifact_and_tool_history_before_http() -> None:
    api = bridge_api()
    tool_info = ToolInfo(
        name="python",
        description="fixed",
        parameters=ToolParams(
            properties={"code": {"type": "string"}},
            required=["code"],
            additionalProperties=False,
        ),
    )
    artifacts = bridge_artifact_messages()
    tampered = list(artifacts)
    envelope = json.loads(tampered[0].content)
    envelope["artifact"]["content"] += " "
    changed = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    tampered[0] = ChatMessageUser(content=changed)
    with pytest.raises(P2RHostError, match="canonical coherence artifact envelope"):
        asyncio.run(
            api.generate(
                [ChatMessageSystem(content=BRIDGE_PROMPT.decode()), *tampered],
                [tool_info],
                "auto",
                GenerateConfig(max_tokens=30_000, temperature=0.2),
            )
        )

    code = "print(3)"
    _, receipt = sample_with_receipt(code=code)
    with pytest.raises(P2RHostError, match="bound to one Python call"):
        asyncio.run(
            api.generate(
                [
                    ChatMessageSystem(content=BRIDGE_PROMPT.decode()),
                    *artifacts,
                    ChatMessageAssistant(
                        content="",
                        tool_calls=[ToolCall(id="call-a", function="python", arguments={"code": code})],
                    ),
                    ChatMessageTool(
                        content=json.dumps(receipt, sort_keys=True, separators=(",", ":")),
                        tool_call_id="call-b",
                        function="python",
                    ),
                ],
                [tool_info],
                "auto",
                GenerateConfig(max_tokens=30_000, temperature=0.2),
            )
        )


def test_blocking_sidecar_is_exact_filtered_copy() -> None:
    blocking = {
        "finding": "broken",
        "severity": "blocking",
        "why_not_repaired": "redesign",
        "verbatim_step_quote": "step",
        "executed_evidence": "script and result",
        "reading_dependence": "reading_robust",
        "structural_requirement": "identifying signal",
    }
    note = {"finding": "caution", "severity": "note"}
    assert _blocking_findings({"unrepaired": [blocking, note]}) == [blocking]


def test_atomic_delivery_refuses_to_overwrite_existing_evidence(tmp_path: Path) -> None:
    value = {"unrepaired": []}
    first = _atomic_deliver(
        tmp_path,
        value=value,
        blocking=[],
        eval_log={"status": "success"},
        eval_archive=b"locked-eval-archive",
        receipt={"protocol": P2R_PROTOCOL},
    )
    assert json.loads((first / "blocking_findings.json").read_text(encoding="utf-8")) == []
    with pytest.raises(P2RHostError, match="immutable"):
        _atomic_deliver(
            tmp_path,
            value=value,
            blocking=[],
            eval_log={"status": "success"},
            eval_archive=b"locked-eval-archive",
            receipt={"protocol": P2R_PROTOCOL},
        )


@pytest.mark.skipif(
    os.getenv("AI_RESEARCH_P2R_SANDBOX_SMOKE") != "1",
    reason="requires an explicitly authorized local Docker sandbox",
)
def test_inspect_executes_python_in_the_locked_sandbox_and_records_receipt(
    tmp_path: Path,
) -> None:
    code = "print(sum([1, 2]))"
    first = ModelOutput.from_message(
        ChatMessageAssistant(
            content="",
            tool_calls=[ToolCall(id="tool-smoke", function="python", arguments={"code": code})],
        ),
        stop_reason="tool_calls",
    )
    second = ModelOutput.from_content(model="mockllm/model", content='{"done":true}')
    model = get_model(
        "mockllm/model", custom_outputs=[first, second], memoize=False
    )
    module_root = Path(__file__).resolve().parents[2]
    task = Task(
        dataset=[Sample(id="p2r-sandbox-smoke", input="Run the fixed Python tool once.")],
        solver=[use_tools(p2r_python(), tool_choice="auto"), generate(tool_calls="loop")],
        model=model,
        sandbox=SandboxEnvironmentSpec(
            type="docker",
            config=str(module_root / "worker/p2r-sandbox.compose.yml"),
        ),
        fail_on_error=True,
        message_limit=4,
        time_limit=120,
    )
    logs = eval(
        task,
        model=model,
        display="none",
        score=False,
        log_samples=True,
        log_dir=str(tmp_path / "logs"),
        max_samples=1,
        max_sandboxes=1,
    )
    assert len(logs) == 1
    assert logs[0].status == "success", logs[0].error
    assert logs[0].samples and len(logs[0].samples) == 1
    receipts = _validated_tool_receipts(logs[0].samples[0])
    assert len(receipts) == 1
    assert receipts[0]["scriptSha256"] == sha(code)
    assert receipts[0]["stdout"] == "3\n"
    assert receipts[0]["exitCode"] == 0
    ModelMirrorBridgeAPI,
    _modelmirror_route_run_ids,
    _validated_loopback_endpoint,
