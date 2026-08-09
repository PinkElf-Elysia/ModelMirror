from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from server.benchmarks import api as benchmark_api
from server.benchmarks.catalog import BenchmarkCatalog
from server.benchmarks.executor import BenchmarkGeneratorOutput, BenchmarkJobExecutor
from server.benchmarks.service import (
    BenchmarkGenerationError,
    BenchmarkGenerationService,
)
from server.benchmarks.store import BenchmarkJobStore
from server.evaluations.metrics import evaluate_case_metrics
from server.evaluations.service import XpertEvaluationService
from server.evaluations.store import EvaluationStateError, XpertEvaluationStore
from server.xperts import XpertStore
from server.xperts.validation import validate_xpert_definition


class _MissingResourceStore:
    def get_version(self, *_args: Any, **_kwargs: Any) -> Any:
        raise KeyError("resource not found")


class _NoKnowledgeService:
    def get_active_pipeline_version(self, _kb_id: str) -> None:
        return None


def _generated_case(case_id: str = "generated-one") -> dict[str, Any]:
    return {
        "case_id": case_id,
        "name": "Generated case",
        "message": "Give a concise plan for the supplied task.",
        "tags": ["generated", "instruction_following", "en-US"],
        "expected": {"contains": ["plan"]},
        "weights": {"contains": 1.0},
        "targeting": {
            "difficulty": "edge",
            "target_refs": ["agent:primary:role_prompt"],
            "capability_matrix": ["instruction_following"],
            "pressure_types": ["ambiguity"],
            "rationale": "Exercises the target Agent's explicit planning instruction.",
            "challenge": "The request is underspecified and requires a concise plan.",
            "discriminator": (
                "The configured target must return its explicit planning contract, "
                "rather than a generic prose answer."
            ),
        },
    }


def test_benchmark_job_store_persists_recovers_and_cancels(tmp_path: Path) -> None:
    store = BenchmarkJobStore(tmp_path)
    created = store.create_job(kind="generation", request={"target": {"kind": "xpert_draft"}})
    claimed = store.claim_next_job()

    assert claimed is not None
    assert claimed["job_id"] == created["job_id"]
    assert claimed["status"] == "generating"
    assert claimed["attempts"] == 1

    restored = BenchmarkJobStore(tmp_path)
    assert restored.recover_jobs() == 1
    retried = restored.claim_next_job()
    assert retried is not None
    assert retried["attempts"] == 2

    queued = restored.create_job(kind="calibration", request={"dataset_id": "dataset"})
    cancelled = restored.cancel_job(queued["job_id"])
    assert cancelled["status"] == "cancelled"
    assert BenchmarkJobStore(tmp_path).require_job(queued["job_id"])["status"] == "cancelled"


def test_generated_dataset_requires_current_calibration_and_warning_ack(
    tmp_path: Path,
) -> None:
    store = XpertEvaluationStore(tmp_path)
    dataset = store.create_generated_dataset(
        name="Generated benchmark",
        description="",
        cases=[_generated_case()],
        provenance={
            "target_reference": {
                "kind": "xpert_draft",
                "xpert_id": "xpert-one",
                "draft_revision": 1,
            },
            "target_checksum": "fixed",
        },
        coverage={"selected": ["instruction_following"]},
    )

    with pytest.raises(EvaluationStateError, match="complete calibration"):
        store.publish_dataset(dataset["dataset_id"], revision=dataset["revision"])

    warned = store.set_dataset_calibration(
        dataset["dataset_id"],
        revision=dataset["revision"],
        calibration={
            "status": "warning",
            "target_reference": dataset["provenance"]["target_reference"],
            "target_checksum": "fixed",
            "warnings": ["Baseline is very easy."],
        },
    )
    with pytest.raises(EvaluationStateError, match="acknowledged"):
        store.publish_dataset(warned["dataset_id"], revision=warned["revision"])

    version = store.publish_dataset(
        warned["dataset_id"],
        revision=warned["revision"],
        acknowledge_calibration_warnings=True,
    )
    assert version["version"] == 1
    assert version["calibration"]["status"] == "warning"

    edited = store.put_cases(
        warned["dataset_id"],
        revision=warned["revision"] + 1,
        cases=[_generated_case("generated-two")],
    )
    assert edited["calibration"]["status"] == "stale"
    with pytest.raises(EvaluationStateError, match="calibration is stale"):
        store.publish_dataset(edited["dataset_id"], revision=edited["revision"])


def test_default_xpert_draft_preflight_accepts_runtime_history_variable(
    tmp_path: Path,
) -> None:
    xpert_store = XpertStore(tmp_path / "xperts")
    xpert = xpert_store.create_xpert(name="Benchmark preview")
    evaluation_store = XpertEvaluationStore(tmp_path / "evaluations")

    def prompt_preflight(candidate: Any) -> tuple[Any, Any, list[Any]]:
        return (
            validate_xpert_definition(candidate),
            candidate.draft.workflow.model_copy(deep=True),
            [],
        )

    evaluation_service = XpertEvaluationService(
        evaluation_store,
        xpert_store=xpert_store,
        proposal_store=_MissingResourceStore(),
        prompt_preflight=prompt_preflight,
        toolset_store=_MissingResourceStore(),
        plugin_store=_MissingResourceStore(),
        rag_service=_NoKnowledgeService(),
        context_store=_MissingResourceStore(),
    )
    service = BenchmarkGenerationService(
        evaluation_store=evaluation_store,
        evaluation_service=evaluation_service,
        xpert_store=xpert_store,
        proposal_store=_MissingResourceStore(),
        prompt_store=_MissingResourceStore(),
        context_store=_MissingResourceStore(),
    )

    result = service.preflight(
        target_reference={
            "kind": "xpert_draft",
            "xpert_id": xpert.id,
            "draft_revision": xpert.draft_revision,
        },
        requested_coverage=["instruction_following", "multi_turn"],
    )

    assert result["valid"] is True
    assert result["issues"] == []
    assert result["target"]["source"]["draft_revision"] == 1
    assert any(
        anchor["kind"] == "agent_prompt" for anchor in result["target_anchors"]
    )
    assert any(
        anchor["kind"] == "conversation_contract"
        for anchor in result["target_anchors"]
    )


def test_generator_validation_rejects_unknown_tools_duplicates_and_gold_leak(
    tmp_path: Path,
) -> None:
    service = BenchmarkGenerationService(
        evaluation_store=XpertEvaluationStore(tmp_path),
        evaluation_service=object(),
        xpert_store=object(),
        proposal_store=object(),
        prompt_store=object(),
        context_store=object(),
    )
    payload = {
        "dataset": {
            "name": "Tool routing",
            "description": "",
            "cases": [
                {
                    "case_id": "tool-one",
                    "name": "Use search",
                    "message": "Find current release notes.",
                    "locale": "en-US",
                    "coverage": "tool_routing",
                    "targeting": {
                        "difficulty": "edge",
                        "target_refs": ["tool:search"],
                        "capability_matrix": ["tool_routing"],
                        "pressure_types": ["tool_decoy"],
                        "rationale": "Verifies routing to the explicitly available search tool.",
                        "challenge": "The request requires current release information.",
                        "discriminator": (
                            "The configured target must invoke search instead of "
                            "answering from unsupported general knowledge."
                        ),
                    },
                    "expected": {
                        "required_tools": ["search"],
                        "forbidden_tools": ["write"],
                        "tool_order": ["search"],
                    },
                    "weights": {"tool_call_match": 1.0},
                }
            ],
        }
    }
    parsed = service.parse_generated_cases(
        json.dumps(payload),
        expected_count=1,
        allowed_coverage=["tool_routing"],
        allowed_tool_names=["search", "write"],
        allowed_target_anchors=[
            {
                "id": "tool:search",
                "kind": "tool",
                "label": "Search",
                "summary": "Search tool",
                "coverage": ["tool_routing"],
            }
        ],
    )
    assert parsed["cases"][0]["expected"]["required_tools"] == ["search"]

    unknown = copy.deepcopy(payload)
    unknown["dataset"]["cases"][0]["expected"]["required_tools"] = ["unknown"]
    with pytest.raises(BenchmarkGenerationError, match="unavailable tools"):
        service.parse_generated_cases(
            json.dumps(unknown),
            expected_count=1,
            allowed_coverage=["tool_routing"],
            allowed_tool_names=["search"],
            allowed_target_anchors=[
                {"id": "tool:search", "coverage": ["tool_routing"]}
            ],
        )

    duplicate = copy.deepcopy(payload)
    duplicate["dataset"]["cases"] = [
        copy.deepcopy(payload["dataset"]["cases"][0]),
        {**copy.deepcopy(payload["dataset"]["cases"][0]), "case_id": "tool-two"},
    ]
    with pytest.raises(BenchmarkGenerationError, match="duplicates"):
        service.parse_generated_cases(
            json.dumps(duplicate),
            expected_count=2,
            allowed_coverage=["tool_routing"],
            allowed_tool_names=["search", "write"],
            allowed_target_anchors=[
                {"id": "tool:search", "coverage": ["tool_routing"]}
            ],
        )

    leaked = copy.deepcopy(payload)
    long_gold = "This exact deterministic answer is deliberately copied into the prompt."
    leaked["dataset"]["cases"][0]["message"] = f"Repeat: {long_gold}"
    leaked["dataset"]["cases"][0]["expected"] = {"exact_answer": long_gold}
    with pytest.raises(BenchmarkGenerationError, match="Gold"):
        service.parse_generated_cases(
            json.dumps(leaked),
            expected_count=1,
            allowed_coverage=["tool_routing"],
            allowed_tool_names=[],
            allowed_target_anchors=[
                {"id": "tool:search", "coverage": ["tool_routing"]}
            ],
        )


def test_generator_requires_target_evidence_and_balanced_difficulty(
    tmp_path: Path,
) -> None:
    service = BenchmarkGenerationService(
        evaluation_store=XpertEvaluationStore(tmp_path),
        evaluation_service=object(),
        xpert_store=object(),
        proposal_store=object(),
        prompt_store=object(),
        context_store=object(),
    )
    anchors = [
        {
            "id": "agent:primary:role_prompt",
            "kind": "agent_prompt",
            "axis": "domain",
            "label": "Primary rolePrompt",
            "summary": "Review supply chain escalation constraints.",
            "focus_terms": ["supply chain"],
            "coverage": ["instruction_following"],
        },
        {
            "id": "agent:primary:conversation_contract",
            "kind": "conversation_contract",
            "axis": "contract",
            "label": "Conversation history",
            "summary": "Resolve conflicts in recent context.",
            "focus_terms": ["escalation"],
            "coverage": ["multi_turn"],
        },
    ]
    difficulties = ["basic"] * 4 + ["edge"] * 4 + ["adversarial"] * 4
    cases = []
    for index, difficulty in enumerate(difficulties):
        coverage = "instruction_following" if index % 2 == 0 else "multi_turn"
        combined = index < 8
        matrix = (
            ["instruction_following", "multi_turn"] if combined else [coverage]
        )
        target_refs = (
            [anchors[0]["id"], anchors[1]["id"]]
            if combined or coverage == "multi_turn"
            else [anchors[0]["id"] if coverage == "instruction_following" else anchors[1]["id"]]
        )
        focus_terms = ["supply chain", "escalation"] if combined or coverage == "multi_turn" else (
            ["supply chain"] if coverage == "instruction_following" else ["escalation"]
        )
        pressure_types = (
            ["conflicting_context", "competing_constraints"]
            if difficulty == "adversarial"
            else ["ambiguity"] if difficulty == "edge" else []
        )
        cases.append(
            {
                "case_id": f"targeted-{index}",
                "name": f"Targeted {index}",
                "message": f"Handle {', '.join(focus_terms)} scenario number {index}.",
                "locale": "en-US" if index % 2 == 0 else "zh-CN",
                "coverage": coverage,
                "targeting": {
                    "difficulty": difficulty,
                    "target_refs": target_refs,
                    "capability_matrix": matrix,
                    "focus_terms": focus_terms,
                    "pressure_types": pressure_types,
                    "rationale": f"Verifies the fixed {coverage} contract for scenario {index}.",
                    "challenge": f"Applies {difficulty} pressure without changing the Gold.",
                    "discriminator": (
                        "The configured supply-chain reviewer must preserve its "
                        "escalation contract instead of giving generic advice."
                    ),
                },
                "expected": {"exact_answer": f"result-{index}"},
                "weights": {"exact_match": 1.0},
            }
        )
    payload = {"dataset": {"name": "Targeted", "cases": cases}}

    parsed = service.parse_generated_cases(
        json.dumps(payload),
        expected_count=12,
        allowed_coverage=["instruction_following", "multi_turn"],
        allowed_tool_names=[],
        allowed_target_anchors=anchors,
    )

    assert parsed["targeting"]["difficulty_counts"] == {
        "basic": 4,
        "edge": 4,
        "adversarial": 4,
    }
    assert parsed["targeting"]["target_anchor_count"] == 2
    assert parsed["targeting"]["combined_case_count"] == 8
    assert parsed["targeting"]["cases_with_focus"] == 12

    unknown = copy.deepcopy(payload)
    unknown["dataset"]["cases"][0]["targeting"]["target_refs"] = ["unknown"]
    with pytest.raises(BenchmarkGenerationError, match="unknown target anchors"):
        service.parse_generated_cases(
            json.dumps(unknown),
            expected_count=12,
            allowed_coverage=["instruction_following", "multi_turn"],
            allowed_tool_names=[],
            allowed_target_anchors=anchors,
        )

    unbalanced = copy.deepcopy(payload)
    for case in unbalanced["dataset"]["cases"]:
        case["targeting"]["difficulty"] = "basic"
    with pytest.raises(BenchmarkGenerationError, match="may be basic"):
        service.parse_generated_cases(
            json.dumps(unbalanced),
            expected_count=12,
            allowed_coverage=["instruction_following", "multi_turn"],
            allowed_tool_names=[],
            allowed_target_anchors=anchors,
        )


def test_generator_enforces_professional_focus_and_three_axis_matrices(
    tmp_path: Path,
) -> None:
    service = BenchmarkGenerationService(
        evaluation_store=XpertEvaluationStore(tmp_path),
        evaluation_service=object(),
        xpert_store=object(),
        proposal_store=object(),
        prompt_store=object(),
        context_store=object(),
    )
    anchors = [
        {
            "id": "agent:supplier:role_prompt",
            "kind": "agent_prompt",
            "axis": "domain",
            "label": "Supplier quality role",
            "summary": "Assess PPAP evidence and supplier risk.",
            "focus_terms": ["PPAP"],
            "coverage": ["instruction_following"],
        },
        {
            "id": "agent:supplier:output_schema",
            "kind": "output_schema",
            "axis": "contract",
            "label": "Risk schema",
            "summary": "Return a numeric risk_score in strict JSON.",
            "focus_terms": ["risk_score"],
            "coverage": ["structured_output"],
        },
        {
            "id": "agent:supplier:conversation_contract",
            "kind": "conversation_contract",
            "axis": "contract",
            "label": "Escalation history",
            "summary": "Resolve supplier escalation conflicts across turns.",
            "focus_terms": ["supplier escalation"],
            "coverage": ["multi_turn"],
        },
    ]
    matrices = [
        ["instruction_following"],
        ["structured_output"],
        ["instruction_following", "structured_output"],
        ["multi_turn", "instruction_following"],
        ["multi_turn", "structured_output"],
        ["instruction_following", "structured_output", "multi_turn"],
    ]
    difficulties = ["basic", "basic", "edge", "edge", "adversarial", "adversarial"]
    anchor_by_capability = {
        "instruction_following": anchors[0],
        "structured_output": anchors[1],
        "multi_turn": anchors[2],
    }
    cases: list[dict[str, Any]] = []
    for index, (matrix, difficulty) in enumerate(zip(matrices, difficulties)):
        selected_anchors = list(
            {
                anchor["id"]: anchor
                for anchor in [anchors[0], *[anchor_by_capability[item] for item in matrix]]
            }.values()
        )
        focus_terms = [anchor["focus_terms"][0] for anchor in selected_anchors]
        pressure_types = (
            ["conflicting_context", "schema_boundary"]
            if difficulty == "adversarial"
            else ["competing_constraints"] if difficulty == "edge" else []
        )
        cases.append(
            {
                "case_id": f"supplier-{index}",
                "name": f"Supplier audit {index}",
                "message": (
                    "Review the professional supplier case using "
                    + ", ".join(focus_terms)
                    + f"; scenario {index}."
                ),
                "locale": "en-US",
                "coverage": matrix[0],
                "targeting": {
                    "difficulty": difficulty,
                    "target_refs": [anchor["id"] for anchor in selected_anchors],
                    "capability_matrix": matrix,
                    "focus_terms": focus_terms,
                    "pressure_types": pressure_types,
                    "rationale": "Tests the supplier-quality contract and its bound output behavior.",
                    "challenge": "Requires evidence-aware handling of a specialized supplier exception.",
                    "discriminator": (
                        "A generic assistant is unlikely to preserve PPAP evidence, "
                        "risk_score schema, and escalation precedence together."
                    ),
                },
                "expected": {"contains": [f"supplier-result-{index}"]},
                "weights": {"contains": 1.0},
            }
        )
    payload = {"dataset": {"name": "Professional supplier benchmark", "cases": cases}}

    parsed = service.parse_generated_cases(
        json.dumps(payload),
        expected_count=6,
        allowed_coverage=[
            "instruction_following",
            "structured_output",
            "multi_turn",
        ],
        allowed_tool_names=[],
        allowed_target_anchors=anchors,
    )

    assert parsed["targeting"]["combined_case_count"] == 4
    assert len(parsed["targeting"]["capability_matrix_counts"]) == 4
    assert parsed["targeting"]["combined_capabilities"] == [
        "instruction_following",
        "multi_turn",
        "structured_output",
    ]
    assert parsed["targeting"]["cases_with_focus"] == 6
    assert parsed["targeting"]["discriminator_count"] == 6

    normalized_metadata = copy.deepcopy(payload)
    normalized_case = normalized_metadata["dataset"]["cases"][2]
    normalized_case["targeting"]["capability_matrix"].append("tool_routing")
    normalized_case["targeting"]["target_refs"] = [anchors[0]["id"]]
    normalized_case["targeting"]["focus_terms"].append("supplier risk")
    coverage_case = normalized_metadata["dataset"]["cases"][0]
    coverage_case["coverage"] = "tool_routing"
    coverage_case["targeting"]["focus_terms"].append("risk_score")
    coverage_case["message"] += " risk_score"
    normalized = service.parse_generated_cases(
        json.dumps(normalized_metadata),
        expected_count=6,
        allowed_coverage=[
            "instruction_following",
            "structured_output",
            "multi_turn",
        ],
        allowed_tool_names=[],
        allowed_target_anchors=anchors,
    )
    normalized_targeting = normalized["cases"][2]["targeting"]
    assert "tool_routing" not in normalized_targeting["capability_matrix"]
    assert anchors[1]["id"] in normalized_targeting["target_refs"]
    assert "supplier risk" in normalized_targeting["focus_terms"]
    assert len(normalized_targeting["normalization_notes"]) == 2
    normalized_coverage = normalized["cases"][0]["targeting"]
    assert anchors[1]["id"] in normalized_coverage["target_refs"]
    assert "risk_score" in normalized_coverage["focus_terms"]
    assert any(
        note.startswith("replaced unavailable primary coverage")
        for note in normalized_coverage["normalization_notes"]
    )
    assert any(
        note.startswith("added target anchor for declared focus term")
        for note in normalized_coverage["normalization_notes"]
    )
    assert normalized["targeting"]["normalized_case_count"] == 2

    grounded_phrase = copy.deepcopy(payload)
    grounded_phrase["dataset"]["cases"][0]["targeting"]["focus_terms"] = [
        "supplier risk"
    ]
    grounded_phrase["dataset"]["cases"][0]["message"] += " supplier risk"
    service.parse_generated_cases(
        json.dumps(grounded_phrase),
        expected_count=6,
        allowed_coverage=[
            "instruction_following",
            "structured_output",
            "multi_turn",
        ],
        allowed_tool_names=[],
        allowed_target_anchors=anchors,
    )

    professional_markers = copy.deepcopy(payload)
    marker_case = professional_markers["dataset"]["cases"][0]
    marker_case["message"] = (
        "Review supplier risk using production-part approval evidence and return "
        "a disposition for the launch decision."
    )
    parsed_markers = service.parse_generated_cases(
        json.dumps(professional_markers),
        expected_count=6,
        allowed_coverage=[
            "instruction_following",
            "structured_output",
            "multi_turn",
        ],
        allowed_tool_names=[],
        allowed_target_anchors=anchors,
    )
    assert parsed_markers["cases"][0]["targeting"]["professional_evidence"][
        "sufficient"
    ] is True

    generic_input = copy.deepcopy(payload)
    generic_case = generic_input["dataset"]["cases"][0]
    generic_case["message"] = "Review this case and provide a useful recommendation."
    with pytest.raises(BenchmarkGenerationError, match="professional evidence"):
        service.parse_generated_cases(
            json.dumps(generic_input),
            expected_count=6,
            allowed_coverage=[
                "instruction_following",
                "structured_output",
                "multi_turn",
            ],
            allowed_tool_names=[],
            allowed_target_anchors=anchors,
        )

    invented_focus = copy.deepcopy(payload)
    invented_focus["dataset"]["cases"][0]["targeting"]["focus_terms"] = ["Basel III"]
    with pytest.raises(BenchmarkGenerationError, match="invents focus terms"):
        service.parse_generated_cases(
            json.dumps(invented_focus),
            expected_count=6,
            allowed_coverage=[
                "instruction_following",
                "structured_output",
                "multi_turn",
            ],
            allowed_tool_names=[],
            allowed_target_anchors=anchors,
        )

    weak_adversarial = copy.deepcopy(payload)
    weak_adversarial["dataset"]["cases"][4]["targeting"]["pressure_types"] = [
        "schema_boundary"
    ]
    with pytest.raises(BenchmarkGenerationError, match="needs at least 2 pressure_types"):
        service.parse_generated_cases(
            json.dumps(weak_adversarial),
            expected_count=6,
            allowed_coverage=[
                "instruction_following",
                "structured_output",
                "multi_turn",
            ],
            allowed_tool_names=[],
            allowed_target_anchors=anchors,
        )

    repair_system, repair_user = service.repair_prompt(
        "{invalid}",
        "Case 1 does not visibly use its declared focus terms",
        expected_count=6,
        allowed_coverage=[
            "instruction_following",
            "structured_output",
            "multi_turn",
        ],
        target_anchors=anchors,
    )
    assert "Return JSON only" in repair_system
    assert '"exact_case_count": 6' in repair_user
    assert "must visibly exercise either an exact" in repair_user
    assert "each matrix capability is supported by a cited anchor" in repair_user


def test_server_blueprints_generalize_across_all_supported_capabilities(
    tmp_path: Path,
) -> None:
    service = BenchmarkGenerationService(
        evaluation_store=XpertEvaluationStore(tmp_path),
        evaluation_service=object(),
        xpert_store=object(),
        proposal_store=object(),
        prompt_store=object(),
        context_store=object(),
    )
    anchors = [
        {
            "id": "agent:supplier:role",
            "kind": "agent_prompt",
            "axis": "domain",
            "label": "Supplier quality specialist",
            "summary": "Assess PPAP evidence for automotive supplier quality decisions.",
            "focus_terms": ["PPAP"],
            "coverage": ["instruction_following"],
        },
        {
            "id": "agent:supplier:schema",
            "kind": "output_schema",
            "axis": "contract",
            "label": "Risk result schema",
            "summary": "Return risk_score and disposition in strict JSON.",
            "focus_terms": ["risk_score"],
            "coverage": ["structured_output"],
        },
        {
            "id": "agent:supplier:history",
            "kind": "conversation_contract",
            "axis": "contract",
            "label": "Supplier escalation history",
            "summary": "Resolve supplier escalation conflicts across turns.",
            "focus_terms": ["supplier escalation"],
            "coverage": ["multi_turn"],
        },
        {
            "id": "tool:supplier_search",
            "kind": "tool",
            "axis": "resource",
            "label": "supplier_search",
            "summary": "Route evidence lookup through supplier_search.",
            "focus_terms": ["supplier_search"],
            "coverage": ["tool_routing"],
        },
        {
            "id": "knowledge:quality",
            "kind": "knowledge",
            "axis": "resource",
            "label": "Quality manual",
            "summary": "Ground claims in quality_manual.md.",
            "focus_terms": ["quality_manual.md"],
            "coverage": ["knowledge_citation"],
        },
        {
            "id": "prompt_profile:audit",
            "kind": "prompt_command",
            "axis": "resource",
            "label": "Audit command",
            "summary": "Command alias /audit runs the supplier audit profile.",
            "focus_terms": ["audit"],
            "coverage": ["prompt_command"],
        },
    ]
    coverage = [
        "instruction_following",
        "structured_output",
        "multi_turn",
        "tool_routing",
        "knowledge_citation",
        "prompt_command",
    ]
    fixed_output_schema = {
        "type": "object",
        "required": ["risk_score", "disposition"],
        "properties": {
            "risk_score": {"type": "number"},
            "disposition": {"type": "string"},
        },
    }
    blueprints = service.case_blueprints(
        case_count=12,
        locales=["zh-CN", "en-US"],
        selected_coverage=coverage,
        target_anchors=anchors,
        seed=3,
        tool_names=["supplier_search"],
        document_names=["quality_manual.md"],
        prompt_aliases=["audit"],
        structured_output_schemas={
            "agent:supplier:schema": fixed_output_schema,
        },
    )

    assert len(blueprints) == 12
    assert {item["primary_coverage"] for item in blueprints} == set(coverage)
    assert sum(len(item["capability_matrix"]) > 1 for item in blueprints) >= 8
    assert {
        capability
        for item in blueprints
        if len(item["capability_matrix"]) > 1
        for capability in item["capability_matrix"]
    } == set(coverage)
    assert {item["locale"] for item in blueprints} == {"zh-CN", "en-US"}
    assert {item["difficulty"] for item in blueprints} == {
        "basic",
        "edge",
        "adversarial",
    }
    assert all(
        item["required_tool_name"] == "supplier_search"
        for item in blueprints
        if "tool_routing" in item["capability_matrix"]
    )
    assert all(
        item["required_document_name"] == "quality_manual.md"
        for item in blueprints
        if "knowledge_citation" in item["capability_matrix"]
    )
    assert all(
        item["required_prompt_alias"] == "audit"
        for item in blueprints
        if "prompt_command" in item["capability_matrix"]
    )

    cases: list[dict[str, Any]] = []
    for blueprint in blueprints:
        matrix = list(blueprint["capability_matrix"])
        focus = " ".join(blueprint["required_focus_terms"])
        message = (
            f"Assess the professional exception for {focus}; "
            f"scenario {blueprint['blueprint_id']}."
        )
        if "prompt_command" in matrix:
            message = f"/audit {message}"
        messages = []
        if "multi_turn" in matrix or blueprint["difficulty"] == "adversarial":
            messages = [
                {"role": "user", "content": "Earlier evidence was incomplete."},
                {"role": "assistant", "content": "I requested a corrected evidence packet."},
            ]
        expected: dict[str, Any] = {
            "contains": ["approved-state", "evidence-logged"],
        }
        weights: dict[str, float] = {"contains": 1.0}
        if "structured_output" in matrix:
            weights["json_schema"] = 1.0
        if "tool_routing" in matrix:
            expected["required_tools"] = ["supplier_search"]
            expected["forbidden_tools"] = []
            expected["tool_order"] = ["supplier_search"]
            weights["tool_call_match"] = 1.0
        if "knowledge_citation" in matrix:
            expected["document_names"] = ["quality_manual.md"]
            weights["citation_hit"] = 1.0
        cases.append(
            {
                "case_id": "model-controlled-id",
                "name": f"Professional case {blueprint['blueprint_id']}",
                "locale": "en-US",
                "coverage": "instruction_following",
                "message": message,
                "messages": messages,
                "targeting": {
                    "difficulty": "basic",
                    "target_refs": [anchors[0]["id"]],
                    "capability_matrix": ["instruction_following"],
                    "focus_terms": ["PPAP"],
                    "pressure_types": [],
                    "rationale": "Checks the fixed supplier-quality behavior and contract.",
                    "challenge": "Uses a realistic evidence exception with a bounded answer.",
                    "discriminator": (
                        "The configured specialist must preserve the exact supplier evidence, "
                        "routing, citation, and response contracts represented by this blueprint."
                    ),
                },
                "expected": expected,
                "weights": weights,
            }
        )

    parsed = service.parse_generated_cases(
        json.dumps({"dataset": {"name": "Six-axis benchmark", "cases": cases}}),
        expected_count=12,
        allowed_coverage=coverage,
        allowed_tool_names=["supplier_search"],
        allowed_target_anchors=anchors,
        case_blueprints=blueprints,
        allowed_document_names=["quality_manual.md"],
        allowed_prompt_aliases=["audit"],
    )

    assert [item["case_id"] for item in parsed["cases"]] == [
        item["blueprint_id"] for item in blueprints
    ]
    assert parsed["targeting"]["blueprint_case_count"] == 12
    assert parsed["targeting"]["normalized_case_count"] == 0
    for case, blueprint in zip(parsed["cases"], blueprints):
        assert case["targeting"]["difficulty"] == blueprint["difficulty"]
        assert case["targeting"]["capability_matrix"] == blueprint["capability_matrix"]
        assert case["targeting"]["target_refs"] == blueprint["target_refs"]
        if "structured_output" in blueprint["capability_matrix"]:
            assert case["expected"]["json_schema"] == fixed_output_schema

    missing_schema = copy.deepcopy(cases)
    blueprints_without_schema = copy.deepcopy(blueprints)
    schema_index = next(
        index
        for index, item in enumerate(blueprints)
        if "structured_output" in item["capability_matrix"]
    )
    blueprints_without_schema[schema_index]["required_json_schema"] = None
    with pytest.raises(BenchmarkGenerationError, match="expected.json_schema"):
        service.parse_generated_cases(
            json.dumps({"dataset": {"name": "Invalid", "cases": missing_schema}}),
            expected_count=12,
            allowed_coverage=coverage,
            allowed_tool_names=["supplier_search"],
            allowed_target_anchors=anchors,
            case_blueprints=blueprints_without_schema,
            allowed_document_names=["quality_manual.md"],
            allowed_prompt_aliases=["audit"],
        )


def test_single_capability_adversarial_tool_blueprints_use_fixed_decoy_gold(
    tmp_path: Path,
) -> None:
    service = BenchmarkGenerationService(
        evaluation_store=XpertEvaluationStore(tmp_path),
        evaluation_service=object(),
        xpert_store=object(),
        proposal_store=object(),
        prompt_store=object(),
        context_store=object(),
    )
    anchors = [
        {
            "id": "tool:case_narrative_lookup",
            "kind": "tool",
            "axis": "resource",
            "label": "case_narrative_lookup",
            "summary": "Retrieve the complete pharmacovigilance case narrative.",
            "focus_terms": ["case_narrative_lookup"],
            "coverage": ["tool_routing"],
        },
        {
            "id": "tool:signal_history_lookup",
            "kind": "tool",
            "axis": "resource",
            "label": "signal_history_lookup",
            "summary": "Retrieve the validated pharmacovigilance signal history.",
            "focus_terms": ["signal_history_lookup"],
            "coverage": ["tool_routing"],
        },
    ]
    blueprints = service.case_blueprints(
        case_count=6,
        locales=["zh-CN", "en-US"],
        selected_coverage=["tool_routing"],
        target_anchors=anchors,
        seed=95002,
        tool_names=["case_narrative_lookup", "signal_history_lookup"],
    )

    adversarial = [
        item for item in blueprints if item["difficulty"] == "adversarial"
    ]
    assert adversarial
    assert all(item["forbidden_tool_names"] for item in adversarial)

    cases = []
    for blueprint in blueprints:
        visible_terms = [
            *blueprint["required_focus_terms"],
            *blueprint["forbidden_tool_names"],
        ]
        cases.append(
            {
                "name": f"PV routing {blueprint['blueprint_id']}",
                "targeting": {
                    "blueprint_id": blueprint["blueprint_id"],
                    "rationale": "Checks the fixed pharmacovigilance routing contract.",
                    "challenge": "A decoy lookup competes with the required signal lookup.",
                    "discriminator": (
                        "The configured reviewer must select the required signal tool and "
                        "must not dispatch the visible narrative decoy."
                    ),
                },
                "message": (
                    f"Case {blueprint['blueprint_id']}: use the required signal workflow; "
                    + " ".join(visible_terms)
                ),
                "messages": [],
                "expected": {},
                "weights": {"tool_call_match": 1.0},
            }
        )

    parsed = service.parse_generated_cases(
        json.dumps({"dataset": {"name": "PV routing", "cases": cases}}),
        expected_count=6,
        allowed_coverage=["tool_routing"],
        allowed_tool_names=["case_narrative_lookup", "signal_history_lookup"],
        allowed_target_anchors=anchors,
        case_blueprints=blueprints,
    )

    for case, blueprint in zip(parsed["cases"], blueprints):
        assert case["expected"]["required_tools"] == [
            blueprint["required_tool_name"]
        ]
        assert case["expected"].get("forbidden_tools", []) == blueprint[
            "forbidden_tool_names"
        ]


def test_server_owned_resource_gold_is_removed_from_generated_contains() -> None:
    expectation = {
        "contains": [
            "SPC_Western_Electric_Rules.md",
            "metrology_msa_lookup",
            "lot_genealogy_lookup",
            "/excursion-review",
            "keep the lot on hold until MSA is verified",
        ]
    }
    removed = BenchmarkGenerationService._strip_server_owned_contains(
        expectation,
        {
            "required_tool_name": "metrology_msa_lookup",
            "forbidden_tool_names": ["lot_genealogy_lookup"],
            "required_document_name": "SPC_Western_Electric_Rules.md",
            "required_prompt_alias": "excursion-review",
        },
    )

    assert removed == [
        "SPC_Western_Electric_Rules.md",
        "metrology_msa_lookup",
        "lot_genealogy_lookup",
        "/excursion-review",
    ]
    assert expectation["contains"] == [
        "keep the lot on hold until MSA is verified"
    ]


def test_fixed_resource_names_drive_tool_and_knowledge_coverage(tmp_path: Path) -> None:
    class _ToolsetStore:
        @staticmethod
        def get_version(_toolset_id: str, _version: int) -> Any:
            return SimpleNamespace(
                tools=[
                    SimpleNamespace(enabled=True, exposed_name="supplier_search"),
                    SimpleNamespace(enabled=False, exposed_name="supplier_write"),
                ]
            )

    class _RagService:
        @staticmethod
        def get_pipeline_version(_version_id: str) -> dict[str, Any]:
            return {
                "source_summary": [
                    {"filename": "quality_manual.md"},
                    {"filename": "supplier_controls.xlsx"},
                ]
            }

    service = BenchmarkGenerationService(
        evaluation_store=XpertEvaluationStore(tmp_path),
        evaluation_service=object(),
        xpert_store=object(),
        proposal_store=object(),
        prompt_store=object(),
        context_store=object(),
        toolset_store=_ToolsetStore(),
        rag_service=_RagService(),
    )
    snapshot = {
        "workflow": {
            "nodes": [
                {
                    "id": "toolset",
                    "type": "toolset_resource",
                    "data": {
                        "kind": "toolset_resource",
                        "toolsetId": "supplier-tools",
                        "pinnedVersion": 2,
                        "title": "Supplier tools",
                    },
                },
                {
                    "id": "knowledge",
                    "type": "knowledge_base",
                    "data": {
                        "kind": "knowledge_base",
                        "knowledgeBaseId": "supplier-kb",
                        "title": "Supplier knowledge",
                    },
                },
            ]
        },
        "resources": {
            "knowledge_versions": [
                {"knowledge_base_id": "supplier-kb", "version_id": "version-7"}
            ]
        },
        "prompt_profiles": [
            {
                "id": "audit-profile",
                "name": "Audit",
                "aliases": ["audit"],
                "template": "Audit {{args}} against supplier controls.",
            }
        ],
    }

    coverage = service.detect_coverage(snapshot)
    assert "tool_routing" in coverage["available"]
    assert "knowledge_citation" in coverage["available"]
    assert "prompt_command" in coverage["available"]
    assert coverage["tool_names"] == ["supplier_search"]
    context = service._safe_generation_context(snapshot)
    assert context["knowledge_document_names"] == [
        "quality_manual.md",
        "supplier_controls.xlsx",
    ]
    assert context["prompt_command_aliases"] == ["audit"]
    assert any(
        "quality_manual.md" in anchor["summary"]
        for anchor in context["target_anchors"]
        if anchor["kind"] == "knowledge"
    )


def test_generic_counterfactual_removes_specialization_and_bound_resources(
    tmp_path: Path,
) -> None:
    service = BenchmarkGenerationService(
        evaluation_store=XpertEvaluationStore(tmp_path),
        evaluation_service=object(),
        xpert_store=object(),
        proposal_store=object(),
        prompt_store=object(),
        context_store=object(),
    )
    snapshot = {
        "target_id": "xpert:supplier:v2",
        "label": "Supplier specialist",
        "checksum": "specialist-checksum",
        "source": {"kind": "xpert_version", "xpert_id": "supplier", "version": 2},
        "workflow": {
            "nodes": [
                {
                    "id": "agent",
                    "type": "workflow_agent",
                    "data": {
                        "kind": "workflow_agent",
                        "rolePrompt": "Apply PPAP and APQP escalation rules.",
                        "promptSuffix": "Cite supplier evidence.",
                        "modelId": "same-model",
                        "toolMode": "mcp_tools",
                        "toolNames": "supplier_search",
                        "outputSchemaMode": "json_schema",
                    },
                },
                {
                    "id": "knowledge",
                    "type": "knowledge_base",
                    "data": {"kind": "knowledge_base", "knowledgeBaseId": "supplier-kb"},
                },
            ],
            "edges": [
                {
                    "id": "knowledge-agent",
                    "source": "knowledge",
                    "target": "agent",
                    "targetHandle": "knowledge",
                }
            ],
        },
        "prompt_profiles": [{"name": "Supplier audit"}],
        "input_template": "/audit {{args}}",
        "resources": {"knowledge_versions": [{"knowledge_base_id": "supplier-kb"}]},
    }

    generic = service.generic_counterfactual_snapshot(snapshot)

    agent = generic["workflow"]["nodes"][0]["data"]
    assert agent["modelId"] == "same-model"
    assert agent["outputSchemaMode"] == "json_schema"
    assert agent["toolMode"] == "none"
    assert "PPAP" not in agent["rolePrompt"]
    assert generic["workflow"]["edges"] == []
    assert len(generic["workflow"]["nodes"]) == 1
    assert generic["prompt_profiles"] == []
    assert generic["input_template"] is None
    assert generic["benchmark_counterfactual"] is True


def test_calibration_reports_target_advantage_over_generic_counterfactual(
    tmp_path: Path,
) -> None:
    service = BenchmarkGenerationService(
        evaluation_store=XpertEvaluationStore(tmp_path),
        evaluation_service=object(),
        xpert_store=object(),
        proposal_store=object(),
        prompt_store=object(),
        context_store=object(),
    )
    service.target_is_fresh = lambda _reference, _checksum: True  # type: ignore[method-assign]
    dataset = {
        "revision": 1,
        "origin": "generated",
        "cases": [_generated_case("case-one")],
    }
    run = {
        "status": "completed",
        "dataset": {"draft_revision": 1},
        "targets": [
            {
                "target_id": "generic",
                "checksum": "generic-checksum",
                "benchmark_counterfactual": True,
            },
            {"target_id": "specialist", "checksum": "specialist-checksum"},
        ],
        "items": [
            {"target_id": "generic", "case_id": "case-one", "status": "completed", "score": 0.4},
            {"target_id": "specialist", "case_id": "case-one", "status": "completed", "score": 1.0},
        ],
        "report": {
            "targets": [
                {"target_id": "specialist", "score": 1.0},
                {"target_id": "generic", "score": 0.4},
            ]
        },
        "run_id": "run-one",
    }

    result = service.calibration_result(
        dataset=dataset,
        evaluation_run=run,
        target_reference={"kind": "xpert_draft"},
        target_checksum="specialist-checksum",
    )

    assert result["baseline_score"] == 1.0
    assert result["generic_counterfactual_score"] == 0.4
    assert result["targeting_advantage"] == 0.6
    assert not any("easy and" in warning for warning in result["warnings"])


@pytest.mark.asyncio
async def test_tool_call_match_uses_names_and_stable_order() -> None:
    result = await evaluate_case_metrics(
        case={
            "message": "Research and summarize",
            "expected": {
                "required_tools": ["search", "read"],
                "forbidden_tools": ["write"],
                "tool_order": ["search", "read"],
            },
            "weights": {"tool_call_match": 1.0},
        },
        output="done",
        citations={},
        tool_calls=["search", "read"],
    )
    assert result["score"] == 1.0
    assert result["metrics"][0]["kind"] == "tool_call_match"

    failed = await evaluate_case_metrics(
        case={
            "message": "Research and summarize",
            "expected": {
                "required_tools": ["search", "read"],
                "forbidden_tools": ["write"],
                "tool_order": ["search", "read"],
            },
        },
        output="done",
        citations={},
        tool_calls=["read", "write", "search"],
    )
    assert failed["score"] == pytest.approx(0.5)


class _FakeGeneratorService:
    def __init__(self, evaluation_store: XpertEvaluationStore) -> None:
        self.evaluation_store = evaluation_store
        self.parse_calls = 0

    def snapshot_target(self, reference: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        return {
            "target_id": "target-one",
            "label": "Target One",
            "source": copy.deepcopy(reference),
            "xpert": {"id": "xpert-one", "name": "Xpert One"},
            "workflow": {"nodes": [], "edges": []},
            "checksum": "fixed-checksum",
        }, []

    @staticmethod
    def detect_coverage(_snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "available": ["instruction_following"],
            "recommended": ["instruction_following"],
            "tool_names": [],
        }

    @staticmethod
    def conversation_seeds(_selections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return []

    @staticmethod
    def generation_prompt(**_kwargs: Any) -> tuple[str, str, dict[str, Any]]:
        return "system", "user", {
            "selected": ["instruction_following"],
            "available": ["instruction_following"],
            "recommended": ["instruction_following"],
            "tool_names": [],
        }

    def parse_generated_cases(self, raw: str, **_kwargs: Any) -> dict[str, Any]:
        self.parse_calls += 1
        if raw == "invalid":
            raise BenchmarkGenerationError("invalid generated JSON")
        return {
            "name": "Generated",
            "description": "",
            "cases": [_generated_case()],
            "assumptions": [],
        }

    @staticmethod
    def repair_prompt(_raw: str, _error: str, **_kwargs: Any) -> tuple[str, str]:
        return "repair-system", "repair-user"

    @staticmethod
    def public_target(snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "target_id": snapshot["target_id"],
            "label": snapshot["label"],
            "source": snapshot["source"],
            "checksum": snapshot["checksum"],
        }


class _FakeEvaluationService:
    def __init__(self, store: XpertEvaluationStore) -> None:
        self.store = store

    def create_run_from_snapshots(self, **payload: Any) -> dict[str, Any]:
        return self.store.create_run(
            dataset_version=payload["dataset_version"],
            cases=payload["cases"],
            baseline=payload["baseline"],
            candidates=payload["candidates"],
            config=payload["config"],
            warnings=payload["warnings"],
        )


class _WakeOnlyExecutor:
    def __init__(self) -> None:
        self.wake_count = 0

    def wake(self) -> None:
        self.wake_count += 1


class _ApiGenerationService:
    @staticmethod
    def capabilities() -> dict[str, Any]:
        return {"version": "test-generator"}

    @staticmethod
    def preflight(**_kwargs: Any) -> dict[str, Any]:
        return {
            "valid": True,
            "target": {"target_id": "fixed"},
            "coverage": {
                "available": ["instruction_following"],
                "recommended": ["instruction_following"],
            },
            "conversation_seed_count": 1,
            "warnings": [],
            "issues": [],
        }


@pytest.mark.asyncio
async def test_generation_repairs_once_and_reuses_persisted_dataset(
    tmp_path: Path,
) -> None:
    evaluation_store = XpertEvaluationStore(tmp_path / "evaluations")
    job_store = BenchmarkJobStore(tmp_path / "jobs")
    service = _FakeGeneratorService(evaluation_store)
    evaluation_executor = _WakeOnlyExecutor()
    responses = iter(["invalid", "valid"])
    calls: list[tuple[str, float]] = []

    async def generator(
        model_id: str,
        _system: str,
        _user: str,
        temperature: float,
        _max_tokens: int,
    ) -> str:
        calls.append((model_id, temperature))
        return next(responses)

    executor = BenchmarkJobExecutor(
        job_store,
        service=service,  # type: ignore[arg-type]
        generator_runner=generator,
        evaluation_store=evaluation_store,
        evaluation_service=_FakeEvaluationService(evaluation_store),
        evaluation_executor=evaluation_executor,
    )
    created = job_store.create_job(
        kind="generation",
        request={
            "target": {
                "kind": "xpert_draft",
                "xpert_id": "xpert-one",
                "draft_revision": 1,
            },
            "generator_model_id": "planner",
            "case_count": 1,
            "coverage": ["instruction_following"],
            "locales": ["zh-CN", "en-US"],
        },
    )
    claimed = job_store.claim_next_job()
    assert claimed is not None
    await executor._run_generation(claimed)

    current = job_store.require_job(created["job_id"])
    assert current["status"] == "calibrating"
    assert current["generation"]["repair_used"] is True
    assert calls == [("planner", 0.2), ("planner", 0.0)]
    assert service.parse_calls == 2
    assert len(evaluation_store.list_datasets()) == 1

    job_store.update_job(created["job_id"], status="generating")
    await executor._run_generation(job_store.require_job(created["job_id"]))
    assert len(evaluation_store.list_datasets()) == 1
    assert len(calls) == 2
    assert evaluation_executor.wake_count == 2


@pytest.mark.asyncio
async def test_generation_repairs_recoverable_contract_failure_and_keeps_diagnostics(
    tmp_path: Path,
) -> None:
    evaluation_store = XpertEvaluationStore(tmp_path / "evaluations")
    job_store = BenchmarkJobStore(tmp_path / "jobs")
    service = _FakeGeneratorService(evaluation_store)
    evaluation_executor = _WakeOnlyExecutor()
    responses: Any = iter(
        [
            BenchmarkGeneratorOutput(
                diagnostics={
                    "finish_reason": "stop",
                    "content_chars": 0,
                    "reasoning_chars": 900,
                    "candidate_top_level_keys": ["type"],
                    "private_detail": "must never be persisted",
                },
                error_code="contract_missing",
                error_message="Generator response did not contain the required JSON contract.",
            ),
            BenchmarkGeneratorOutput(
                text="valid",
                diagnostics={
                    "finish_reason": "stop",
                    "content_chars": 120,
                    "reasoning_chars": 0,
                    "candidate_top_level_keys": ["dataset"],
                },
            ),
        ]
    )

    async def generator(
        _model_id: str,
        _system: str,
        _user: str,
        _temperature: float,
        _max_tokens: int,
    ) -> BenchmarkGeneratorOutput:
        return next(responses)

    executor = BenchmarkJobExecutor(
        job_store,
        service=service,  # type: ignore[arg-type]
        generator_runner=generator,
        evaluation_store=evaluation_store,
        evaluation_service=_FakeEvaluationService(evaluation_store),
        evaluation_executor=evaluation_executor,
    )
    created = job_store.create_job(
        kind="generation",
        request={
            "target": {
                "kind": "xpert_draft",
                "xpert_id": "xpert-one",
                "draft_revision": 1,
            },
            "generator_model_id": "planner",
            "case_count": 1,
            "coverage": ["instruction_following"],
            "locales": ["en-US"],
        },
    )

    claimed = job_store.claim_next_job()
    assert claimed is not None
    await executor._run_generation(claimed)

    current = job_store.require_job(created["job_id"])
    assert current["status"] == "calibrating"
    assert current["generation"]["repair_used"] is True
    assert service.parse_calls == 1
    assert [item["attempt"] for item in current["generation_attempts"]] == [
        "initial",
        "repair",
    ]
    assert current["generation_attempts"][0]["error_code"] == "contract_missing"
    assert current["generation_attempts"][0]["diagnostics"][
        "candidate_top_level_keys"
    ] == ["type"]
    assert "private_detail" not in current["generation_attempts"][0]["diagnostics"]


@pytest.mark.asyncio
async def test_generation_api_creates_safe_job_and_hides_conversation_selections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(benchmark_api.router)
    job_store = BenchmarkJobStore(tmp_path / "jobs")
    executor = _WakeOnlyExecutor()
    monkeypatch.setattr(benchmark_api, "_catalog", BenchmarkCatalog())
    monkeypatch.setattr(
        benchmark_api,
        "_evaluation_store",
        XpertEvaluationStore(tmp_path / "evaluations"),
    )
    monkeypatch.setattr(benchmark_api, "_job_store", job_store)
    monkeypatch.setattr(benchmark_api, "_service", _ApiGenerationService())
    monkeypatch.setattr(benchmark_api, "_executor", executor)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/benchmarks/generations",
            json={
                "target": {
                    "kind": "xpert_draft",
                    "xpert_id": "xpert-one",
                    "draft_revision": 1,
                },
                "generator_model_id": "planner-model",
                "case_count": 6,
                "coverage": ["instruction_following"],
                "conversation_selections": [
                    {
                        "xpert_id": "xpert-one",
                        "conversation_id": "private-conversation",
                        "message_ids": ["message-one"],
                    }
                ],
            },
        )
        assert response.status_code == 200
        created = response.json()
        assert created["status"] == "queued"
        assert "conversation_selections" not in created["request"]

        listed = await client.get("/api/benchmarks/generations")
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
        assert "conversation_selections" not in listed.json()["items"][0]["request"]

    assert executor.wake_count == 1
