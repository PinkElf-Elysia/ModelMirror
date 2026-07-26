from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import time
from collections.abc import Callable
from typing import Any

try:
    from server.meta_agent.schemas import MetaPlannerCapabilitySnapshot
    from server.xperts.models import XpertDefinition, XpertDraft
except ModuleNotFoundError:
    from meta_agent.schemas import MetaPlannerCapabilitySnapshot
    from xperts.models import XpertDefinition, XpertDraft

from .models import (
    STRUCTURE_MUTATION_OPERATIONS,
    EvolutionMutationPolicy,
    EvolutionRunRequest,
    EvolutionStructureScope,
    StructureMutation,
)
from .mutations import (
    SAFE_CONTROL_NODE_KINDS,
    StructureMutationCompiler,
    public_workflow_graph,
)
from .store import EvolutionConflictError, EvolutionStateError, XpertEvolutionStore


PLACEHOLDER_PATTERN = re.compile(r"{{\s*([^{}]+?)\s*}}")
SENSITIVE_PATTERNS = [
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\b(?:api[_ -]?key|token|secret|password)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\b(?:chain[- ]of[- ]thought|hidden reasoning|隐藏推理|思维链)\b"),
    re.compile(r"(?:[A-Za-z]:\\|/(?:home|Users|var|etc)/)[^\s]+"),
]


class XpertEvolutionService:
    """Builds immutable Prompt baselines and guarded candidate snapshots."""

    def __init__(
        self,
        store: XpertEvolutionStore,
        *,
        evaluation_store: Any,
        evaluation_service: Any,
        xpert_store: Any,
        prompt_store: Any,
        proposal_store: Any,
        capability_snapshot_builder: Callable[
            [], MetaPlannerCapabilitySnapshot
        ]
        | None = None,
    ) -> None:
        self.store = store
        self.evaluation_store = evaluation_store
        self.evaluation_service = evaluation_service
        self.xpert_store = xpert_store
        self.prompt_store = prompt_store
        self.proposal_store = proposal_store
        self.capability_snapshot_builder = capability_snapshot_builder

    def capabilities(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "version": "evoagentx-evolution-v2",
            "evolution_kinds": ["prompt", "structure"],
            "target_kinds": ["xpert", "prompt_profile"],
            "xpert_fields": ["rolePrompt", "promptSuffix"],
            "structure": {
                "operations": list(STRUCTURE_MUTATION_OPERATIONS),
                "safe_control_node_kinds": sorted(SAFE_CONTROL_NODE_KINDS),
                "nodes": [],
                "middleware": [],
                "external_xperts": [],
                "knowledge_bases": [],
                "toolsets": [],
                "plugins": [],
                "capability_snapshot_version": None,
                "capability_snapshot_hash": None,
            },
            "limits": {
                "max_prompt_fields": 3,
                "generations": [1, 3],
                "population_size": [2, 5],
                "max_finalists": 3,
                "max_operations_per_candidate": [1, 8],
                "max_added_nodes": 4,
                "max_removed_nodes": 4,
            },
            "default_gate": {
                "min_score_delta": 0.01,
                "max_metric_regression": 0.02,
                "max_model_call_increase_ratio": 1.0,
                "max_token_increase_ratio": 1.0,
                "max_p95_latency_increase_ratio": 1.0,
            },
            "approval": "authoring_proposal_only",
        }
        if self.capability_snapshot_builder is None:
            return payload
        snapshot = self.capability_snapshot_builder()
        safe_nodes = [
            copy.deepcopy(item)
            for item in snapshot.nodes
            if str(item.get("kind") or "") in SAFE_CONTROL_NODE_KINDS
        ]
        safe_middleware = [
            copy.deepcopy(item)
            for item in snapshot.middleware
            if not bool(item.get("high_risk"))
        ]
        payload["structure"].update(
            {
                "nodes": safe_nodes,
                "middleware": safe_middleware,
                "external_xperts": copy.deepcopy(snapshot.external_xperts),
                "knowledge_bases": copy.deepcopy(snapshot.knowledge_bases),
                "toolsets": copy.deepcopy(snapshot.toolsets),
                "plugins": copy.deepcopy(snapshot.plugins),
                "capability_snapshot_version": snapshot.version,
                "capability_snapshot_hash": snapshot.snapshot_hash,
                "default_scope": {
                    "allowed_node_kinds": sorted(
                        {
                            str(item.get("kind") or "")
                            for item in safe_nodes
                            if item.get("kind")
                        }
                    ),
                    "external_xpert_ids": [],
                    "knowledge_base_ids": [],
                    "toolset_ids": [],
                    "plugin_ids": [],
                    "middleware_ids": [],
                },
            }
        )
        return payload

    def preflight(self, request: EvolutionRunRequest) -> dict[str, Any]:
        prepared = self._prepare(request)
        return {
            "valid": True,
            "target": self._public_target(prepared["target"]),
            "dataset": {
                key: prepared["dataset"].get(key)
                for key in ("dataset_id", "version", "name", "case_count", "checksum")
            },
            "train_case_count": len(prepared["train_case_ids"]),
            "validation_case_count": len(prepared["validation_case_ids"]),
            "warnings": prepared["warnings"],
        }

    def create_run(self, request: EvolutionRunRequest) -> dict[str, Any]:
        prepared = self._prepare(request)
        return self.store.create_run(
            request=request.model_dump(mode="json"),
            target=prepared["target"],
            dataset=prepared["dataset"],
            train_case_ids=prepared["train_case_ids"],
            validation_case_ids=prepared["validation_case_ids"],
            warnings=prepared["warnings"],
        )

    def run_detail(self, run_id: str) -> dict[str, Any]:
        run = self.store.require(run_id)
        run["stale"] = self.is_stale(run)
        return self.store.payload(run, include_detail=True)

    def is_stale(self, run: dict[str, Any]) -> bool:
        target = dict(run.get("target") or {})
        try:
            if target.get("kind") == "xpert":
                current = self.xpert_store.get_xpert(str(target["target_id"]))
                return current.draft_revision != int(target["base_revision"])
            current = self.prompt_store.get_profile(str(target["target_id"]))
            return current.draft_revision != int(target["base_revision"])
        except Exception:
            return True

    def build_candidate(
        self,
        run: dict[str, Any],
        *,
        fields: dict[str, str],
        generation: int,
        index: int,
        summary: str,
    ) -> dict[str, Any]:
        target = dict(run["target"])
        baseline = dict(target["baseline_prompts"])
        unknown = sorted(set(fields) - set(baseline))
        missing = sorted(set(baseline) - set(fields))
        if unknown or missing:
            raise EvolutionStateError(
                "Candidate fields must exactly match the selected Prompt fields."
            )
        clean_fields = {key: str(value or "").strip() for key, value in fields.items()}
        self._validate_prompt_safety(
            baseline=baseline,
            candidate=clean_fields,
            train_cases=self.selected_cases(run, "train"),
            profile=target.get("kind") == "prompt_profile",
        )
        checksum = self.prompt_checksum(clean_fields)
        candidate_id = f"g{generation}-c{index}-{checksum[:10]}"
        if target["kind"] == "xpert":
            xpert = XpertDefinition.model_validate(target["baseline_xpert"])
            self.apply_xpert_fields(xpert, clean_fields)
            snapshot, warnings = self.evaluation_service.snapshot_xpert_draft(
                xpert,
                source={
                    "kind": "evolution_candidate",
                    "evolution_run_id": run["run_id"],
                    "generation": generation,
                    "candidate_id": candidate_id,
                    "base_revision": target["base_revision"],
                },
                label=f"Generation {generation} candidate {index}",
                model_policy=run["request"]["model_policy"],
                override_model_id=run["request"].get("override_model_id"),
                target_id=f"evolution:{run['run_id']}:{candidate_id}",
            )
            xpert_payload = xpert.model_dump(mode="json")
        else:
            snapshot = copy.deepcopy(target["baseline_snapshot"])
            snapshot["target_id"] = f"evolution:{run['run_id']}:{candidate_id}"
            snapshot["label"] = f"Generation {generation} candidate {index}"
            snapshot["input_template"] = clean_fields["template"]
            snapshot["source"] = {
                "kind": "evolution_candidate",
                "evolution_run_id": run["run_id"],
                "generation": generation,
                "candidate_id": candidate_id,
                "base_revision": target["base_revision"],
            }
            snapshot["checksum"] = self.checksum(
                {
                    "host": target["baseline_snapshot"]["checksum"],
                    "template": clean_fields["template"],
                }
            )
            warnings = list(snapshot.get("warnings") or [])
            xpert_payload = None
        return {
            "candidate_id": candidate_id,
            "generation": generation,
            "fields": clean_fields,
            "summary": str(summary or "")[:500],
            "checksum": checksum,
            "snapshot": snapshot,
            "xpert": xpert_payload,
            "warnings": warnings,
            "created_at": time.time(),
        }

    def build_structure_candidate(
        self,
        run: dict[str, Any],
        *,
        mutations: list[dict[str, Any]],
        generation: int,
        index: int,
        summary: str,
        parent: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = dict(run["target"])
        if target.get("evolution_kind") != "structure":
            raise EvolutionStateError("Run is not a structure evolution.")
        parsed = [StructureMutation.model_validate(item) for item in mutations]
        snapshot = MetaPlannerCapabilitySnapshot.model_validate(
            target["capability_snapshot"]
        )
        parent_xpert = XpertDefinition.model_validate(
            (parent or {}).get("xpert") or target["baseline_xpert"]
        )
        compiler = StructureMutationCompiler(
            snapshot=snapshot,
            scope=EvolutionStructureScope.model_validate(run["request"]["scope"]),
            policy=EvolutionMutationPolicy.model_validate(
                run["request"]["mutation_policy"]
            ),
            default_agent_model_id=target["default_agent_model_id"],
            candidate_seed=(
                f"{run['run_id']}:{generation}:{index}:"
                f"{StructureMutationCompiler.graph_checksum(parent_xpert.draft.workflow)}"
            ),
        )
        candidate_xpert, local_diff = compiler.apply(parent_xpert, parsed)
        checksum = compiler.graph_checksum(candidate_xpert.draft.workflow)
        candidate_id = f"g{generation}-c{index}-{checksum[:10]}"
        snapshot_payload, warnings = self.evaluation_service.snapshot_xpert_draft(
            candidate_xpert,
            source={
                "kind": "structure_evolution_candidate",
                "evolution_run_id": run["run_id"],
                "generation": generation,
                "candidate_id": candidate_id,
                "base_revision": target["base_revision"],
                "capability_snapshot_hash": target["capability_snapshot_hash"],
            },
            label=f"Structure generation {generation} candidate {index}",
            model_policy=run["request"]["model_policy"],
            override_model_id=run["request"].get("override_model_id"),
            target_id=f"evolution:{run['run_id']}:{candidate_id}",
        )
        baseline_xpert = XpertDefinition.model_validate(target["baseline_xpert"])
        aggregate_diff = compiler.graph_diff(
            baseline_xpert.draft.workflow,
            candidate_xpert.draft.workflow,
            manifest=[
                item.model_dump(mode="json", exclude_none=True) for item in parsed
            ],
        )
        return {
            "candidate_id": candidate_id,
            "generation": generation,
            "fields": {},
            "summary": str(summary or "")[:500],
            "checksum": checksum,
            "snapshot": snapshot_payload,
            "xpert": candidate_xpert.model_dump(mode="json"),
            "mutations": [
                item.model_dump(mode="json", exclude_none=True) for item in parsed
            ],
            "diff": aggregate_diff,
            "local_diff": local_diff,
            "parent_candidate_id": (parent or {}).get("candidate_id", "baseline"),
            "warnings": warnings,
            "created_at": time.time(),
        }

    def baseline_candidate(self, run: dict[str, Any]) -> dict[str, Any]:
        target = run["target"]
        if target.get("evolution_kind") == "structure":
            xpert = XpertDefinition.model_validate(target["baseline_xpert"])
            checksum = StructureMutationCompiler.graph_checksum(xpert.draft.workflow)
            return {
                "candidate_id": "baseline",
                "generation": 0,
                "fields": {},
                "summary": "Original workflow structure baseline",
                "checksum": checksum,
                "snapshot": copy.deepcopy(target["baseline_snapshot"]),
                "xpert": copy.deepcopy(target["baseline_xpert"]),
                "mutations": [],
                "diff": StructureMutationCompiler.graph_diff(
                    xpert.draft.workflow,
                    xpert.draft.workflow,
                    manifest=[],
                ),
                "warnings": list(target["baseline_snapshot"].get("warnings") or []),
            }
        return {
            "candidate_id": "baseline",
            "generation": 0,
            "fields": copy.deepcopy(target["baseline_prompts"]),
            "summary": "Original Prompt baseline",
            "checksum": self.prompt_checksum(target["baseline_prompts"]),
            "snapshot": copy.deepcopy(target["baseline_snapshot"]),
            "xpert": copy.deepcopy(target.get("baseline_xpert")),
            "warnings": list(target["baseline_snapshot"].get("warnings") or []),
        }

    def selected_cases(self, run: dict[str, Any], split: str) -> list[dict[str, Any]]:
        selected = set(
            run["train_case_ids"] if split == "train" else run["validation_case_ids"]
        )
        return [
            copy.deepcopy(case)
            for case in run["dataset"].get("cases") or []
            if str(case.get("case_id")) in selected
        ]

    def create_proposal(
        self,
        run: dict[str, Any],
        candidate: dict[str, Any],
    ) -> Any:
        if self.is_stale(run):
            raise EvolutionConflictError(
                "Target draft changed while evolution was running."
            )
        target = run["target"]
        if target["kind"] == "xpert":
            payload = {
                "xpert_id": target["target_id"],
                "patch": {"draft": candidate["xpert"]["draft"]},
                "evolution_report": self.safe_report(run, candidate),
            }
            kind = "xpert_update"
        else:
            payload = {
                "profile_id": target["target_id"],
                "patch": {"template": candidate["fields"]["template"]},
                "evolution_report": self.safe_report(run, candidate),
            }
            kind = "prompt_profile_update"
        structure = target.get("evolution_kind") == "structure"
        if structure:
            payload["structure_mutation_manifest"] = copy.deepcopy(
                candidate.get("mutations") or []
            )
            payload["structure_diff"] = copy.deepcopy(candidate.get("diff") or {})
            payload["capability_snapshot"] = {
                "version": target.get("capability_snapshot_version"),
                "hash": target.get("capability_snapshot_hash"),
            }
        return self.proposal_store.create(
            kind=kind,
            title=(
                f"Structure evolution: {target['name']}"
                if structure
                else f"Prompt evolution: {target['name']}"
            ),
            payload=payload,
            source_type=(
                "structure_evolution" if structure else "prompt_evolution"
            ),
            source_id=run["run_id"],
            source_run_id=run["run_id"],
            target_id=target["target_id"],
            base_revision=int(target["base_revision"]),
        )

    @staticmethod
    def safe_report(run: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        report = dict(run.get("report") or {})
        return {
            "run_id": run["run_id"],
            "dataset_id": run["dataset"].get("dataset_id"),
            "dataset_version": run["dataset"].get("version"),
            "candidate_id": candidate["candidate_id"],
            "candidate_checksum": candidate["checksum"],
            "evolution_kind": run["request"].get("evolution_kind", "prompt"),
            "structure_diff": copy.deepcopy(candidate.get("diff") or {}),
            "gate": copy.deepcopy(report.get("gate") or {}),
            "validation": copy.deepcopy(report.get("validation") or {}),
        }

    def candidate_graph(self, run_id: str, candidate_id: str) -> dict[str, Any]:
        run = self.store.require(run_id)
        candidate: dict[str, Any] | None = None
        if candidate_id == "baseline":
            candidate = self.baseline_candidate(run)
        else:
            for generation in run.get("generations") or []:
                candidate = next(
                    (
                        item
                        for item in generation.get("candidates") or []
                        if item.get("candidate_id") == candidate_id
                    ),
                    None,
                )
                if candidate is not None:
                    break
        if candidate is None or not candidate.get("xpert"):
            raise EvolutionStateError("Structure candidate graph was not found.")
        return self.public_candidate_graph(candidate)

    @staticmethod
    def public_candidate_graph(candidate: dict[str, Any]) -> dict[str, Any]:
        xpert = XpertDefinition.model_validate(candidate["xpert"])
        return public_workflow_graph(
            xpert.draft.workflow,
            candidate.get("diff") or {},
        )

    @staticmethod
    def apply_xpert_fields(xpert: XpertDefinition, fields: dict[str, str]) -> None:
        nodes = {node.id: node for node in xpert.draft.workflow.nodes}
        for path, value in fields.items():
            node_id, field_name = XpertEvolutionService.parse_field_path(path)
            node = nodes.get(node_id)
            if node is None:
                raise EvolutionStateError(f"Workflow Agent node not found: {node_id}")
            data = node.data if isinstance(node.data, dict) else {}
            data[field_name] = value
            node.data = data

    @staticmethod
    def parse_field_path(path: str) -> tuple[str, str]:
        node_id, separator, field_name = str(path).rpartition(".")
        if not separator or field_name not in {"rolePrompt", "promptSuffix"}:
            raise EvolutionStateError(f"Invalid Prompt field: {path}")
        return node_id, field_name

    def _prepare(self, request: EvolutionRunRequest) -> dict[str, Any]:
        dataset = self.evaluation_store.get_dataset_version(
            request.dataset_id, request.dataset_version
        )
        cases = list(dataset.get("cases") or [])
        train_ids, validation_ids, split_warnings = self.split_cases(
            cases, request.seed
        )
        if request.evolution_kind == "structure":
            if self.capability_snapshot_builder is None:
                raise EvolutionStateError(
                    "Structure evolution capability snapshot is unavailable."
                )
            xpert = self.xpert_store.get_xpert(request.target_id)
            if xpert.draft_revision != request.target_revision:
                raise EvolutionConflictError(
                    "Xpert draft changed. Reload before starting evolution."
                )
            capability_snapshot = self.capability_snapshot_builder()
            self._validate_structure_scope(request, capability_snapshot)
            default_agent_model_id = str(
                request.default_agent_model_id
                or self._first_agent_model_id(xpert)
                or ""
            ).strip()
            if not default_agent_model_id:
                raise EvolutionStateError(
                    "Structure evolution requires a default Agent model."
                )
            baseline_snapshot, snapshot_warnings = (
                self.evaluation_service.snapshot_xpert_draft(
                    xpert.model_copy(deep=True),
                    source={
                        "kind": "structure_evolution_baseline",
                        "xpert_id": xpert.id,
                        "draft_revision": xpert.draft_revision,
                        "capability_snapshot_hash": capability_snapshot.snapshot_hash,
                    },
                    label=f"{xpert.name} structure r{xpert.draft_revision}",
                    model_policy=request.model_policy,
                    override_model_id=request.override_model_id,
                    target_id=(
                        f"structure-evolution-baseline:{xpert.id}:"
                        f"r{xpert.draft_revision}"
                    ),
                )
            )
            target = {
                "kind": "xpert",
                "evolution_kind": "structure",
                "target_id": xpert.id,
                "base_revision": xpert.draft_revision,
                "name": xpert.name,
                "selected_fields": [],
                "baseline_prompts": {},
                "baseline_xpert": xpert.model_dump(mode="json"),
                "baseline_snapshot": baseline_snapshot,
                "default_agent_model_id": default_agent_model_id,
                "capability_snapshot": capability_snapshot.model_dump(mode="json"),
                "capability_snapshot_version": capability_snapshot.version,
                "capability_snapshot_hash": capability_snapshot.snapshot_hash,
                "baseline_graph_checksum": StructureMutationCompiler.graph_checksum(
                    xpert.draft.workflow
                ),
            }
        elif request.target_kind == "xpert":
            xpert = self.xpert_store.get_xpert(request.target_id)
            if xpert.draft_revision != request.target_revision:
                raise EvolutionConflictError(
                    "Xpert draft changed. Reload before starting evolution."
                )
            prompts: dict[str, str] = {}
            nodes = {node.id: node for node in xpert.draft.workflow.nodes}
            for path in request.prompt_fields:
                node_id, field_name = self.parse_field_path(path)
                node = nodes.get(node_id)
                data = node.data if node is not None and isinstance(node.data, dict) else {}
                kind = str(data.get("kind") or (node.type if node is not None else ""))
                if node is None or kind != "workflow_agent":
                    raise EvolutionStateError(
                        f"Prompt field must belong to workflow_agent: {path}"
                    )
                prompts[path] = str(data.get(field_name) or "")
            baseline_snapshot, snapshot_warnings = (
                self.evaluation_service.snapshot_xpert_draft(
                    xpert.model_copy(deep=True),
                    source={
                        "kind": "evolution_baseline",
                        "xpert_id": xpert.id,
                        "draft_revision": xpert.draft_revision,
                    },
                    label=f"{xpert.name} draft r{xpert.draft_revision}",
                    model_policy=request.model_policy,
                    override_model_id=request.override_model_id,
                    target_id=f"evolution-baseline:{xpert.id}:r{xpert.draft_revision}",
                )
            )
            target = {
                "kind": "xpert",
                "evolution_kind": "prompt",
                "target_id": xpert.id,
                "base_revision": xpert.draft_revision,
                "name": xpert.name,
                "selected_fields": list(request.prompt_fields),
                "baseline_prompts": prompts,
                "baseline_xpert": xpert.model_dump(mode="json"),
                "baseline_snapshot": baseline_snapshot,
            }
        else:
            profile = self.prompt_store.get_profile(request.target_id)
            if profile.draft_revision != request.target_revision:
                raise EvolutionConflictError(
                    "Prompt Profile draft changed. Reload before starting evolution."
                )
            profile_variables = sorted(PLACEHOLDER_PATTERN.findall(profile.template))
            if profile_variables != ["args"]:
                raise EvolutionStateError(
                    "Prompt Profile must contain exactly one {{args}} placeholder."
                )
            host_ref = {
                "kind": "xpert_version",
                "xpert_id": request.host_xpert_id,
                "version": request.host_xpert_version,
                "label": f"Prompt host {request.host_xpert_id} v{request.host_xpert_version}",
            }
            baseline_snapshot, snapshot_warnings = (
                self.evaluation_service.snapshot_target(
                    host_ref,
                    model_policy=request.model_policy,
                    override_model_id=request.override_model_id,
                )
            )
            baseline_snapshot["target_id"] = (
                f"evolution-baseline:{profile.id}:r{profile.draft_revision}"
            )
            baseline_snapshot["input_template"] = profile.template
            baseline_snapshot["checksum"] = self.checksum(
                {"host": baseline_snapshot["checksum"], "template": profile.template}
            )
            target = {
                "kind": "prompt_profile",
                "evolution_kind": "prompt",
                "target_id": profile.id,
                "base_revision": profile.draft_revision,
                "name": profile.name,
                "selected_fields": ["template"],
                "baseline_prompts": {"template": profile.template},
                "baseline_profile": profile.model_dump(mode="json"),
                "baseline_snapshot": baseline_snapshot,
                "host_xpert_id": request.host_xpert_id,
                "host_xpert_version": request.host_xpert_version,
            }
        return {
            "target": target,
            "dataset": dataset,
            "train_case_ids": train_ids,
            "validation_case_ids": validation_ids,
            "warnings": list(dict.fromkeys([*split_warnings, *snapshot_warnings])),
        }

    @staticmethod
    def _first_agent_model_id(xpert: XpertDefinition) -> str | None:
        for node in xpert.draft.workflow.nodes:
            data = node.data if isinstance(node.data, dict) else {}
            if str(data.get("kind") or node.type) == "workflow_agent":
                value = str(data.get("modelId") or "").strip()
                if value:
                    return value
        return None

    @staticmethod
    def _validate_structure_scope(
        request: EvolutionRunRequest,
        snapshot: MetaPlannerCapabilitySnapshot,
    ) -> None:
        node_kinds = {
            str(item.get("kind") or "")
            for item in snapshot.nodes
            if item.get("kind")
        }
        unsafe_nodes = sorted(
            set(request.scope.allowed_node_kinds)
            - (node_kinds & SAFE_CONTROL_NODE_KINDS)
        )
        if unsafe_nodes:
            raise EvolutionStateError(
                "Structure scope includes unsafe or unavailable node kinds: "
                + ", ".join(unsafe_nodes)
            )
        available = {
            "external_xpert_ids": {
                str(item.get("id") or "") for item in snapshot.external_xperts
            },
            "knowledge_base_ids": {
                str(item.get("id") or "") for item in snapshot.knowledge_bases
            },
            "toolset_ids": {
                str(item.get("id") or "") for item in snapshot.toolsets
            },
            "plugin_ids": {
                str(item.get("id") or "") for item in snapshot.plugins
            },
            "middleware_ids": {
                str(item.get("id") or "")
                for item in snapshot.middleware
                if not bool(item.get("high_risk"))
            },
        }
        for field, allowed in available.items():
            unknown = sorted(set(getattr(request.scope, field)) - allowed)
            if unknown:
                raise EvolutionStateError(
                    f"Structure scope contains unauthorized {field}: "
                    + ", ".join(unknown)
                )

    @staticmethod
    def split_cases(
        cases: list[dict[str, Any]], seed: int
    ) -> tuple[list[str], list[str], list[str]]:
        ids = [str(case.get("case_id") or "") for case in cases]
        if not ids:
            raise EvolutionStateError("Published DatasetVersion has no cases.")
        shuffled = list(ids)
        random.Random(seed).shuffle(shuffled)
        if len(shuffled) < 5:
            return (
                shuffled,
                shuffled,
                [
                    "Dataset has fewer than five cases; optimization and validation "
                    "share cases, so overfitting risk is high."
                ],
            )
        validation_count = max(1, round(len(shuffled) * 0.2))
        validation = shuffled[:validation_count]
        train = shuffled[validation_count:]
        return train, validation, []

    def _validate_prompt_safety(
        self,
        *,
        baseline: dict[str, str],
        candidate: dict[str, str],
        train_cases: list[dict[str, Any]],
        profile: bool,
    ) -> None:
        for field, value in candidate.items():
            if not value:
                raise EvolutionStateError(f"Candidate Prompt is empty: {field}")
            baseline_variables = sorted(PLACEHOLDER_PATTERN.findall(baseline[field]))
            candidate_variables = sorted(PLACEHOLDER_PATTERN.findall(value))
            if baseline_variables != candidate_variables:
                raise EvolutionStateError(
                    f"Candidate changed template variables for {field}."
                )
            if profile and candidate_variables != ["args"]:
                raise EvolutionStateError(
                    "Prompt Profile candidates must retain the {{args}} contract."
                )
            for pattern in SENSITIVE_PATTERNS:
                if pattern.search(value):
                    raise EvolutionStateError(
                        f"Candidate failed Prompt safety checks: {field}."
                    )
            normalized = " ".join(value.casefold().split())
            for case in train_cases:
                corpus = [
                    str(case.get("message") or ""),
                    json.dumps(case.get("expected") or {}, ensure_ascii=False),
                ]
                for text in corpus:
                    sample = " ".join(text.casefold().split())
                    if len(sample) >= 160 and sample[:160] in normalized:
                        raise EvolutionStateError(
                            "Candidate appears to copy a long evaluation sample."
                        )

    @staticmethod
    def _public_target(target: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "kind": target["kind"],
            "evolution_kind": target.get("evolution_kind", "prompt"),
            "target_id": target["target_id"],
            "base_revision": target["base_revision"],
            "name": target["name"],
            "selected_fields": list(target["selected_fields"]),
        }
        if target.get("evolution_kind") == "structure":
            payload.update(
                {
                    "baseline_checksum": target["baseline_graph_checksum"],
                    "default_agent_model_id": target["default_agent_model_id"],
                    "capability_snapshot_version": target[
                        "capability_snapshot_version"
                    ],
                    "capability_snapshot_hash": target["capability_snapshot_hash"],
                }
            )
        else:
            payload["baseline_checksum"] = XpertEvolutionService.prompt_checksum(
                target["baseline_prompts"]
            )
        return payload

    @staticmethod
    def prompt_checksum(fields: dict[str, str]) -> str:
        normalized = {
            str(key): "\n".join(
                line.rstrip()
                for line in str(value or "").replace("\r\n", "\n").split("\n")
            ).strip()
            for key, value in fields.items()
        }
        return XpertEvolutionService.checksum(normalized)

    @staticmethod
    def checksum(value: Any) -> str:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
