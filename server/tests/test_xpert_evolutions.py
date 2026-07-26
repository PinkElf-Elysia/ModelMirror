from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from server.evolutions.executor import XpertEvolutionExecutor
from server.evolutions.service import XpertEvolutionService
from server.evolutions.store import (
    EvolutionConflictError,
    EvolutionStateError,
    XpertEvolutionStore,
)
from server.prompts.store import PromptProfileStore
from server.skills.draft_store import WorkspaceSkillDraftStore
from server.xpert_runtime.authoring_service import AuthoringService
from server.xpert_runtime.authoring_store import AuthoringProposalStore
from server.xperts import XpertStore


class _EvaluationStore:
    def __init__(self, cases: list[dict[str, Any]]) -> None:
        self.version = {
            "dataset_id": "dataset-one",
            "version": 1,
            "name": "Prompt benchmark",
            "case_count": len(cases),
            "checksum": "dataset-checksum",
            "cases": cases,
        }

    def get_dataset_version(self, dataset_id: str, version: int) -> dict[str, Any]:
        assert dataset_id == "dataset-one"
        assert version == 1
        return copy.deepcopy(self.version)


class _EvaluationService:
    def snapshot_xpert_draft(
        self,
        xpert: Any,
        *,
        source: dict[str, Any],
        label: str,
        model_policy: str,
        override_model_id: str | None,
        target_id: str,
    ) -> tuple[dict[str, Any], list[str]]:
        return (
            {
                "target_id": target_id,
                "label": label,
                "source": source,
                "xpert": {"id": xpert.id, "name": xpert.name},
                "workflow": xpert.draft.workflow.model_dump(mode="json"),
                "input_variable": xpert.draft.input_variable,
                "history_variable": xpert.draft.history_variable,
                "output_variable": xpert.draft.output_variable,
                "checksum": target_id,
                "resources": {},
                "warnings": [],
            },
            [],
        )


def _cases(count: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "case_id": f"case-{index}",
            "message": f"Question {index}",
            "expected": {"contains": [f"answer-{index}"]},
        }
        for index in range(count)
    ]


def _service(tmp_path: Path, *, case_count: int = 10) -> tuple[XpertEvolutionService, Any]:
    xperts = XpertStore(tmp_path / "xperts")
    xpert = xperts.create_xpert(name="Prompt Worker")
    workflow = xpert.draft.workflow.model_copy(deep=True)
    agent = next(
        node
        for node in workflow.nodes
        if str((node.data or {}).get("kind") or node.type) == "workflow_agent"
    )
    agent.data["rolePrompt"] = "Use {{user_input}} and answer clearly."
    agent.data["promptSuffix"] = "Keep the response concise."
    xpert = xperts.update_xpert(xpert.id, {"draft": {"workflow": workflow}})
    proposal_store = AuthoringProposalStore(tmp_path / "runtime")
    service = XpertEvolutionService(
        XpertEvolutionStore(tmp_path / "evolutions"),
        evaluation_store=_EvaluationStore(_cases(case_count)),
        evaluation_service=_EvaluationService(),
        xpert_store=xperts,
        prompt_store=PromptProfileStore(tmp_path / "prompts"),
        proposal_store=proposal_store,
    )
    return service, xpert


def test_holdout_split_is_seeded_and_isolated() -> None:
    cases = _cases(10)
    first = XpertEvolutionService.split_cases(cases, 42)
    second = XpertEvolutionService.split_cases(cases, 42)
    different = XpertEvolutionService.split_cases(cases, 7)

    assert first == second
    assert set(first[0]).isdisjoint(first[1])
    assert len(first[0]) == 8
    assert len(first[1]) == 2
    assert first != different


def test_small_dataset_shares_cases_with_overfitting_warning() -> None:
    train, validation, warnings = XpertEvolutionService.split_cases(_cases(4), 42)

    assert train == validation
    assert len(train) == 4
    assert "overfitting" in warnings[0]


def test_xpert_preflight_fixes_revision_and_selected_fields(tmp_path: Path) -> None:
    service, xpert = _service(tmp_path)
    agent = next(
        node
        for node in xpert.draft.workflow.nodes
        if str((node.data or {}).get("kind") or node.type) == "workflow_agent"
    )
    request = {
        "target_kind": "xpert",
        "target_id": xpert.id,
        "target_revision": xpert.draft_revision,
        "prompt_fields": [f"{agent.id}.rolePrompt", f"{agent.id}.promptSuffix"],
        "dataset_id": "dataset-one",
        "dataset_version": 1,
        "optimizer_model_id": "model-one",
    }
    from server.evolutions.models import EvolutionRunRequest

    result = service.preflight(EvolutionRunRequest.model_validate(request))

    assert result["valid"] is True
    assert result["target"]["base_revision"] == xpert.draft_revision
    assert result["target"]["selected_fields"] == request["prompt_fields"]
    assert result["train_case_count"] == 8
    assert result["validation_case_count"] == 2


def test_candidate_preserves_variables_and_rejects_sample_copy(tmp_path: Path) -> None:
    service, xpert = _service(tmp_path)
    agent = next(
        node
        for node in xpert.draft.workflow.nodes
        if str((node.data or {}).get("kind") or node.type) == "workflow_agent"
    )
    from server.evolutions.models import EvolutionRunRequest

    run = service.create_run(
        EvolutionRunRequest(
            target_kind="xpert",
            target_id=xpert.id,
            target_revision=xpert.draft_revision,
            prompt_fields=[f"{agent.id}.rolePrompt"],
            dataset_id="dataset-one",
            dataset_version=1,
            optimizer_model_id="model-one",
        )
    )
    with pytest.raises(EvolutionStateError, match="template variables"):
        service.build_candidate(
            run,
            fields={f"{agent.id}.rolePrompt": "Answer without the input variable."},
            generation=1,
            index=1,
            summary="Removed variable",
        )


def test_evolution_store_recovers_running_run(tmp_path: Path) -> None:
    service, xpert = _service(tmp_path)
    from server.evolutions.models import EvolutionRunRequest

    agent = next(
        node
        for node in xpert.draft.workflow.nodes
        if str((node.data or {}).get("kind") or node.type) == "workflow_agent"
    )
    run = service.create_run(
        EvolutionRunRequest(
            target_kind="xpert",
            target_id=xpert.id,
            target_revision=xpert.draft_revision,
            prompt_fields=[f"{agent.id}.rolePrompt"],
            dataset_id="dataset-one",
            dataset_version=1,
            optimizer_model_id="model-one",
        )
    )
    claimed = service.store.claim_next()
    assert claimed and claimed["status"] == "running"

    restored = XpertEvolutionStore(tmp_path / "evolutions")
    assert restored.recover() == 1
    assert restored.require(run["run_id"])["status"] == "queued"


def test_non_degradation_gate_rejects_metric_regression(tmp_path: Path) -> None:
    service, _xpert = _service(tmp_path)
    executor = XpertEvolutionExecutor(
        service.store,
        service,
        evaluation_service=None,
        evaluation_store=None,
        evaluation_executor=None,
        optimizer_runner=None,  # type: ignore[arg-type]
    )
    baseline = {"snapshot": {"target_id": "baseline"}}
    finalist = {
        "candidate_id": "candidate",
        "checksum": "checksum",
        "snapshot": {"target_id": "candidate"},
    }
    run = {
        "request": {
            "min_score_delta": 0.01,
            "max_metric_regression": 0.02,
        }
    }
    evaluation = {
        "report": {
            "targets": [
                {
                    "target_id": "baseline",
                    "score": 0.7,
                    "metrics": {"contains": 0.9, "rubric_judge": 0.5},
                    "failed_count": 0,
                },
                {
                    "target_id": "candidate",
                    "score": 0.75,
                    "metrics": {"contains": 0.8, "rubric_judge": 0.7},
                    "failed_count": 0,
                },
            ]
        }
    }

    gate = executor._gate(run, baseline, [finalist], evaluation)

    assert gate["passed"] is False
    assert gate["metric_regressions"]["contains"] == pytest.approx(-0.1)


def test_optimizer_generation_parser_is_json_only_and_bounded() -> None:
    parsed = XpertEvolutionExecutor._parse_generation(
        """
        {"candidates": [
          {"fields": {"node.rolePrompt": "first"}, "summary": "one"},
          {"fields": {"node.rolePrompt": "second"}, "summary": "two"},
          {"fields": {"node.rolePrompt": "third"}, "summary": "three"}
        ]}
        """,
        2,
    )

    assert [item["fields"]["node.rolePrompt"] for item in parsed] == [
        "first",
        "second",
    ]
    with pytest.raises(EvolutionStateError, match="JSON object"):
        XpertEvolutionExecutor._parse_generation("not-json", 2)
    with pytest.raises(EvolutionStateError, match="requires fields"):
        XpertEvolutionExecutor._parse_generation(
            '{"candidates": [{"summary": "missing fields"}]}',
            2,
        )


def test_prompt_checksum_normalizes_line_endings_and_trailing_space() -> None:
    first = XpertEvolutionService.prompt_checksum(
        {"node.rolePrompt": "Use {{user_input}}.\r\nReturn evidence.   "}
    )
    second = XpertEvolutionService.prompt_checksum(
        {"node.rolePrompt": "Use {{user_input}}.\nReturn evidence."}
    )

    assert first == second


def test_stale_target_revision_blocks_proposal_creation(tmp_path: Path) -> None:
    service, xpert = _service(tmp_path)
    agent = next(
        node
        for node in xpert.draft.workflow.nodes
        if str((node.data or {}).get("kind") or node.type) == "workflow_agent"
    )
    from server.evolutions.models import EvolutionRunRequest

    run = service.create_run(
        EvolutionRunRequest(
            target_kind="xpert",
            target_id=xpert.id,
            target_revision=xpert.draft_revision,
            prompt_fields=[f"{agent.id}.rolePrompt"],
            dataset_id="dataset-one",
            dataset_version=1,
            optimizer_model_id="model-one",
        )
    )
    candidate = service.build_candidate(
        run,
        fields={
            f"{agent.id}.rolePrompt": (
                "Use {{user_input}}, verify the answer, and respond clearly."
            )
        },
        generation=1,
        index=1,
        summary="Add verification",
    )
    service.xpert_store.update_xpert(
        xpert.id,
        {"description": "Human edit", "draft": xpert.draft.model_dump(mode="json")},
    )

    assert service.is_stale(run) is True
    with pytest.raises(EvolutionConflictError, match="changed"):
        service.create_proposal(run, candidate)


def test_prompt_profile_proposal_updates_draft_only(tmp_path: Path) -> None:
    proposal_store = AuthoringProposalStore(tmp_path / "runtime")
    prompt_store = PromptProfileStore(tmp_path / "prompts")
    profile = prompt_store.create_profile(
        name="Review",
        aliases=["review"],
        template="Review this:\n{{args}}",
    )
    service = AuthoringService(
        proposal_store,
        XpertStore(tmp_path / "xperts"),
        WorkspaceSkillDraftStore(tmp_path / "runtime"),
        prompt_store,
    )
    proposal = proposal_store.create(
        kind="prompt_profile_update",
        title="Improve Review Prompt",
        payload={
            "profile_id": profile.id,
            "patch": {"template": "Review carefully and return evidence:\n{{args}}"},
        },
        source_type="prompt_evolution",
        source_id="xevo-run",
        target_id=profile.id,
        base_revision=profile.draft_revision,
    )

    approved = service.approve(
        proposal.proposal_id,
        revision=proposal.revision,
        operator="tester",
    )
    updated = prompt_store.get_profile(profile.id)

    assert approved.status == "approved"
    assert "evidence" in updated.template
    assert updated.published_version is None
