from __future__ import annotations

import copy
import hashlib
import json
import random
import re
import time
from typing import Any

try:
    from server.xperts.models import XpertDefinition, XpertDraft
except ModuleNotFoundError:
    from xperts.models import XpertDefinition, XpertDraft

from .models import EvolutionRunRequest
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
    ) -> None:
        self.store = store
        self.evaluation_store = evaluation_store
        self.evaluation_service = evaluation_service
        self.xpert_store = xpert_store
        self.prompt_store = prompt_store
        self.proposal_store = proposal_store

    def capabilities(self) -> dict[str, Any]:
        return {
            "version": "evoagentx-prompt-evolution-v1",
            "target_kinds": ["xpert", "prompt_profile"],
            "xpert_fields": ["rolePrompt", "promptSuffix"],
            "limits": {
                "max_prompt_fields": 3,
                "generations": [1, 3],
                "population_size": [2, 5],
                "max_finalists": 3,
            },
            "default_gate": {
                "min_score_delta": 0.01,
                "max_metric_regression": 0.02,
            },
            "approval": "authoring_proposal_only",
        }

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

    def baseline_candidate(self, run: dict[str, Any]) -> dict[str, Any]:
        target = run["target"]
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
        return self.proposal_store.create(
            kind=kind,
            title=f"Prompt evolution: {target['name']}",
            payload=payload,
            source_type="prompt_evolution",
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
            "gate": copy.deepcopy(report.get("gate") or {}),
            "validation": copy.deepcopy(report.get("validation") or {}),
        }

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
        if request.target_kind == "xpert":
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
        return {
            "kind": target["kind"],
            "target_id": target["target_id"],
            "base_revision": target["base_revision"],
            "name": target["name"],
            "selected_fields": list(target["selected_fields"]),
            "baseline_checksum": XpertEvolutionService.prompt_checksum(
                target["baseline_prompts"]
            ),
        }

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
