from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from server.evolutions.executor import XpertEvolutionExecutor
from server.evolutions.models import (
    EvolutionMutationPolicy,
    EvolutionRunRequest,
    EvolutionStructureScope,
    StructureMutation,
)
from server.evolutions.mutations import StructureMutationCompiler
from server.evolutions.service import XpertEvolutionService
from server.evolutions.store import EvolutionStateError, XpertEvolutionStore
from server.meta_agent.schemas import MetaPlannerCapabilitySnapshot, MetaPlannerScope
from server.prompts.store import PromptProfileStore
from server.xpert_runtime.authoring_store import AuthoringProposalStore
from server.xpert_runtime.workflow_node_registry import workflow_node_registry
from server.xperts import XpertStore


class _EvaluationStore:
    def get_dataset_version(self, dataset_id: str, version: int) -> dict[str, Any]:
        assert dataset_id == "dataset-one"
        assert version == 1
        return {
            "dataset_id": dataset_id,
            "version": version,
            "name": "Structure benchmark",
            "case_count": 5,
            "checksum": "dataset-checksum",
            "cases": [
                {
                    "case_id": f"case-{index}",
                    "message": f"Question {index}",
                    "expected": {"contains": [f"answer-{index}"]},
                }
                for index in range(5)
            ],
        }


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
        del model_policy, override_model_id
        return (
            {
                "target_id": target_id,
                "label": label,
                "source": source,
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


def _snapshot() -> MetaPlannerCapabilitySnapshot:
    registry_payload = workflow_node_registry.to_payload()
    nodes = [
        copy.deepcopy(item)
        for section in registry_payload["sections"]
        for item in section["items"]
        if item.get("enabled") and item.get("planner", {}).get("enabled")
    ]
    nodes.extend(
        copy.deepcopy(item)
        for item in registry_payload["knowledge_pipeline"]["items"]
        if item.get("enabled") and item.get("planner", {}).get("enabled")
    )
    return MetaPlannerCapabilitySnapshot(
        version="evoagentx-meta-planner-capabilities-v3",
        ir_version=2,
        contract_version=registry_payload["contract_version"],
        contract_checksum=registry_payload["contract_checksum"],
        snapshot_hash="snapshot-hash",
        generated_at=1,
        node_registry_version="registry-v1",
        nodes=nodes,
        middleware=[
            {
                "id": "context_compression",
                "kind": "runtime_middleware.context_compression",
                "title": "Context Compression",
                "description": "Compress",
                "high_risk": False,
                "requires_tool_mode": False,
                "config_version": 1,
                "security_category": "model_context",
                "fields": [],
                "default_config": {},
            },
            {
                "id": "browser_automation",
                "kind": "runtime_middleware.browser_automation",
                "title": "Browser",
                "description": "Unsafe in evaluation",
                "high_risk": True,
                "fields": [],
                "default_config": {},
            },
        ],
        external_xperts=[
            {
                "id": "specialist",
                "name": "Specialist",
                "description": "Read-only expert",
                "status": "published",
                "version": 2,
                "metadata": {"slug": "specialist"},
            }
        ],
        knowledge_bases=[
            {
                "id": "kb-one",
                "name": "Knowledge",
                "description": "Active KB",
                "status": "active",
                "version": None,
                "metadata": {"active_version_id": "rag-v2"},
            }
        ],
        toolsets=[
            {
                "id": "toolset-one",
                "name": "Search",
                "description": "Read-only tools",
                "status": "published",
                "version": 1,
                "metadata": {},
            }
        ],
        plugins=[],
        prompt_profiles=[],
        models=[{"id": "model-one", "label": "Model One", "safe": True}],
        default_scope=MetaPlannerScope(),
    )


def _compiler(seed: str = "seed") -> StructureMutationCompiler:
    return StructureMutationCompiler(
        snapshot=_snapshot(),
        scope=EvolutionStructureScope(
            allowed_node_kinds=["workflow_agent"],
            external_xpert_ids=["specialist"],
            knowledge_base_ids=["kb-one"],
            toolset_ids=["toolset-one"],
            middleware_ids=["context_compression"],
        ),
        policy=EvolutionMutationPolicy(),
        default_agent_model_id="model-one",
        candidate_seed=seed,
    )


def _xpert(tmp_path: Path) -> Any:
    return XpertStore(tmp_path / "xperts").create_xpert(name="Structure Worker")


def _service(tmp_path: Path) -> tuple[XpertEvolutionService, Any]:
    xpert_store = XpertStore(tmp_path / "xperts")
    xpert = xpert_store.create_xpert(name="Structure Worker")
    service = XpertEvolutionService(
        XpertEvolutionStore(tmp_path / "evolutions"),
        evaluation_store=_EvaluationStore(),
        evaluation_service=_EvaluationService(),
        xpert_store=xpert_store,
        prompt_store=PromptProfileStore(tmp_path / "prompts"),
        proposal_store=AuthoringProposalStore(tmp_path / "runtime"),
        capability_snapshot_builder=_snapshot,
    )
    return service, xpert


def test_resource_and_middleware_bindings_are_deterministic(tmp_path: Path) -> None:
    baseline = _xpert(tmp_path)
    mutations = [
        StructureMutation(
            op="bind_resource",
            ref="knowledge",
            kind="knowledge_base",
            resource_id="kb-one",
            agent_node_id="workflow-agent-1",
        ),
        StructureMutation(
            op="bind_middleware",
            ref="compress",
            middleware_id="context_compression",
            agent_node_id="workflow-agent-1",
        ),
    ]
    first, first_diff = _compiler("fixed").apply(baseline, mutations)
    second, second_diff = _compiler("fixed").apply(baseline, mutations)

    assert StructureMutationCompiler.graph_checksum(
        first.draft.workflow
    ) == StructureMutationCompiler.graph_checksum(second.draft.workflow)
    assert first_diff["added_nodes"] == second_diff["added_nodes"]
    handles = {
        edge.targetHandle
        for edge in first.draft.workflow.edges
        if edge.target == "workflow-agent-1"
    }
    assert {"knowledge", "middleware"} <= handles


def test_protected_nodes_and_unsafe_middleware_are_rejected(tmp_path: Path) -> None:
    baseline = _xpert(tmp_path)
    with pytest.raises(EvolutionStateError, match="cannot be removed"):
        _compiler().apply(
            baseline,
            [StructureMutation(op="remove_control_node", node_id="output-1")],
        )
    with pytest.raises(EvolutionStateError, match="not authorized"):
        _compiler().apply(
            baseline,
            [
                StructureMutation(
                    op="bind_middleware",
                    ref="browser",
                    middleware_id="browser_automation",
                    agent_node_id="workflow-agent-1",
                )
            ],
        )


def test_invalid_control_cycle_is_rejected_before_evaluation(tmp_path: Path) -> None:
    baseline = _xpert(tmp_path)
    with pytest.raises(EvolutionStateError, match="invalid workflow"):
        _compiler().apply(
            baseline,
            [
                StructureMutation(
                    op="add_control_edge",
                    source="workflow-agent-1",
                    target="workflow-agent-1",
                )
            ],
        )


def test_control_add_remove_edges_and_reject_agent_replacement(tmp_path: Path) -> None:
    baseline = _xpert(tmp_path)
    added, added_diff = _compiler("add").apply(
        baseline,
        [
            StructureMutation(op="remove_control_edge", edge_id="edge-agent-output"),
            StructureMutation(
                op="add_control_node",
                ref="format",
                kind="workflow_agent",
            ),
            StructureMutation(
                op="add_control_edge",
                source="workflow-agent-1",
                target="format",
            ),
            StructureMutation(
                op="add_control_edge",
                source="format",
                target="output-1",
            ),
        ],
    )
    added_node_id = added_diff["added_nodes"][0]["node_id"]
    with pytest.raises(EvolutionStateError, match="cannot be replaced"):
        _compiler("replace").apply(
            added,
            [
                StructureMutation(
                    op="replace_control_node",
                    node_id=added_node_id,
                    kind="workflow_agent",
                )
            ],
        )

    incoming = next(
        edge
        for edge in added.draft.workflow.edges
        if edge.target == added_node_id
    )
    outgoing = next(
        edge
        for edge in added.draft.workflow.edges
        if edge.source == added_node_id
    )
    removed, removed_diff = _compiler("remove").apply(
        added,
        [
            StructureMutation(op="remove_control_edge", edge_id=incoming.id),
            StructureMutation(op="remove_control_edge", edge_id=outgoing.id),
            StructureMutation(op="remove_control_node", node_id=added_node_id),
            StructureMutation(
                op="add_control_edge",
                source="workflow-agent-1",
                target="output-1",
            ),
        ],
    )

    assert removed_diff["removed_nodes"][0]["node_id"] == added_node_id
    assert len(removed.draft.workflow.nodes) == len(baseline.draft.workflow.nodes)


def test_resource_and_middleware_unbind_operations(tmp_path: Path) -> None:
    baseline = _xpert(tmp_path)
    bound, diff = _compiler("bind").apply(
        baseline,
        [
            StructureMutation(
                op="bind_resource",
                ref="expert",
                kind="external_xpert",
                resource_id="specialist",
                agent_node_id="workflow-agent-1",
            ),
            StructureMutation(
                op="bind_middleware",
                ref="compress",
                middleware_id="context_compression",
                agent_node_id="workflow-agent-1",
            ),
        ],
    )
    resource_id = next(
        item["node_id"]
        for item in diff["added_nodes"]
        if item["kind"] == "external_xpert"
    )
    middleware_id = next(
        item["node_id"]
        for item in diff["added_nodes"]
        if item["kind"] == "runtime_middleware"
    )
    unbound, unbound_diff = _compiler("unbind").apply(
        bound,
        [
            StructureMutation(op="unbind_resource", node_id=resource_id),
            StructureMutation(op="unbind_middleware", node_id=middleware_id),
        ],
    )

    assert len(unbound_diff["removed_nodes"]) == 2
    assert len(unbound.draft.workflow.nodes) == len(baseline.draft.workflow.nodes)


def test_structure_preflight_and_candidate_graph_are_safe(tmp_path: Path) -> None:
    service, xpert = _service(tmp_path)
    request = EvolutionRunRequest(
        evolution_kind="structure",
        target_kind="xpert",
        target_id=xpert.id,
        target_revision=xpert.draft_revision,
        dataset_id="dataset-one",
        dataset_version=1,
        optimizer_model_id="model-one",
        default_agent_model_id="model-one",
        scope=EvolutionStructureScope(
            knowledge_base_ids=["kb-one"],
            middleware_ids=["context_compression"],
        ),
    )
    preflight = service.preflight(request)
    run = service.create_run(request)
    candidate = service.build_structure_candidate(
        run,
        mutations=[
            {
                "op": "bind_resource",
                "ref": "knowledge",
                "kind": "knowledge_base",
                "resource_id": "kb-one",
                "agent_node_id": "workflow-agent-1",
            }
        ],
        generation=1,
        index=1,
        summary="Bind active knowledge",
    )
    service.store.mutate(
        run["run_id"],
        lambda item: item["generations"].append(
            {
                "generation": 1,
                "candidates": [copy.deepcopy(candidate)],
                "ranking": [],
            }
        ),
    )
    graph = service.candidate_graph(run["run_id"], candidate["candidate_id"])

    assert preflight["target"]["evolution_kind"] == "structure"
    assert preflight["target"]["capability_snapshot_hash"] == "snapshot-hash"
    agent = next(
        node for node in graph["nodes"] if node["id"] == "workflow-agent-1"
    )
    assert "rolePrompt" not in agent["data"]
    assert graph["diff"]["added_nodes"][0]["kind"] == "knowledge_base"


def test_structure_gate_rejects_cost_regression(tmp_path: Path) -> None:
    service, _xpert_value = _service(tmp_path)
    executor = XpertEvolutionExecutor(
        service.store,
        service,
        evaluation_service=None,
        evaluation_store=None,
        evaluation_executor=None,
        optimizer_runner=None,  # type: ignore[arg-type]
    )
    baseline = {"snapshot": {"target_id": "baseline"}}
    candidate = {
        "candidate_id": "candidate",
        "checksum": "checksum",
        "snapshot": {"target_id": "candidate"},
        "diff": {
            "added_nodes": [{"node_id": "new"}],
            "removed_nodes": [],
            "node_delta": 1,
            "candidate_node_count": 4,
        },
    }
    run = {
        "request": {
            "gate": {
                "min_score_delta": 0.01,
                "max_metric_regression": 0.02,
                "max_model_call_increase_ratio": 0.25,
                "max_token_increase_ratio": 0.25,
                "max_p95_latency_increase_ratio": 0.25,
            },
            "mutation_policy": {"max_added_nodes": 4},
        }
    }
    evaluation = {
        "report": {
            "targets": [
                {
                    "target_id": "baseline",
                    "score": 0.7,
                    "metrics": {"contains": 0.7},
                    "failed_count": 0,
                    "model_calls": 10,
                    "estimated_tokens": 1_000,
                    "p95_latency_ms": 100,
                },
                {
                    "target_id": "candidate",
                    "score": 0.8,
                    "metrics": {"contains": 0.8},
                    "failed_count": 0,
                    "model_calls": 20,
                    "estimated_tokens": 1_100,
                    "p95_latency_ms": 110,
                },
            ]
        }
    }

    gate = executor._structure_gate(
        run, baseline, [candidate], evaluation
    )

    assert gate["passed"] is False
    assert gate["cost_regressions"] == ["model_calls"]


def test_structure_proposal_updates_draft_only_after_approval(
    tmp_path: Path,
) -> None:
    service, xpert = _service(tmp_path)
    request = EvolutionRunRequest(
        evolution_kind="structure",
        target_kind="xpert",
        target_id=xpert.id,
        target_revision=xpert.draft_revision,
        dataset_id="dataset-one",
        dataset_version=1,
        optimizer_model_id="model-one",
        default_agent_model_id="model-one",
        scope=EvolutionStructureScope(knowledge_base_ids=["kb-one"]),
    )
    run = service.create_run(request)
    candidate = service.build_structure_candidate(
        run,
        mutations=[
            {
                "op": "bind_resource",
                "ref": "knowledge",
                "kind": "knowledge_base",
                "resource_id": "kb-one",
                "agent_node_id": "workflow-agent-1",
            }
        ],
        generation=1,
        index=1,
        summary="Bind knowledge",
    )
    proposal = service.create_proposal(run, candidate)
    unchanged = service.xpert_store.get_xpert(xpert.id)

    assert proposal.kind == "xpert_update"
    assert proposal.source_type == "structure_evolution"
    assert proposal.payload["structure_mutation_manifest"][0]["op"] == "bind_resource"
    assert unchanged.draft_revision == xpert.draft_revision
    assert unchanged.published_version is None


def test_structure_generation_parser_requires_typed_mutations() -> None:
    parsed = XpertEvolutionExecutor._parse_structure_generation(
        """
        {"candidates": [
          {"mutations": [{"op": "remove_control_edge", "edge_id": "edge-a"}],
           "summary": "replace route"}
        ]}
        """,
        4,
    )

    assert parsed[0]["mutations"][0]["op"] == "remove_control_edge"
    with pytest.raises(EvolutionStateError, match="requires mutations"):
        XpertEvolutionExecutor._parse_structure_generation(
            '{"candidates": [{"summary": "missing"}]}',
            4,
        )
