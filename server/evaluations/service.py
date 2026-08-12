from __future__ import annotations

import copy
import hashlib
import json
import time
from typing import Any, Callable

try:
    from server.workflow_native.schemas import NativeWorkflowDefinition
    from server.xperts.models import (
        XpertDefinition,
        XpertDraft,
        XpertVersion,
    )
    from server.xperts.validation import validate_xpert_workflow_graph
except ModuleNotFoundError:
    from workflow_native.schemas import NativeWorkflowDefinition
    from xperts.models import XpertDefinition, XpertDraft, XpertVersion
    from xperts.validation import validate_xpert_workflow_graph

from .store import (
    EvaluationConflictError,
    EvaluationStateError,
    XpertEvaluationStore,
)


UNSAFE_NODE_KINDS = {
    "agent_handoff",
    "handoff_router",
    "human_intervention",
    "data_table_query",
    "data_table_insert",
    "data_table_update",
    "data_table_delete",
}
UNSAFE_MIDDLEWARE_IDS = {
    "human_in_the_loop",
    "todo_planner",
    "sandbox_files",
    "sandbox_shell",
    "skills_runtime",
    "browser_automation",
    "client_tools",
    "office_automation",
    "scheduler",
    "ralph_loop",
    "knowledge_writer",
    "plugin_hooks",
    "xpert_authoring",
    "skill_creator",
}


class XpertEvaluationService:
    """Freezes safe evaluation targets without mutating Xpert resources."""

    def __init__(
        self,
        store: XpertEvaluationStore,
        *,
        xpert_store: Any,
        proposal_store: Any,
        prompt_preflight: Callable[[XpertDefinition], Any],
        toolset_store: Any,
        plugin_store: Any,
        rag_service: Any,
        context_store: Any,
    ) -> None:
        self.store = store
        self.xpert_store = xpert_store
        self.proposal_store = proposal_store
        self.prompt_preflight = prompt_preflight
        self.toolset_store = toolset_store
        self.plugin_store = plugin_store
        self.rag_service = rag_service
        self.context_store = context_store

    def snapshot_target(
        self,
        reference: dict[str, Any],
        *,
        model_policy: str,
        override_model_id: str | None,
        recursion_path: tuple[str, ...] = (),
    ) -> tuple[dict[str, Any], list[str]]:
        kind = str(reference.get("kind") or "")
        if kind == "xpert_version":
            xpert_id = str(reference.get("xpert_id") or "")
            version_number = int(reference.get("version") or 0)
            xpert = self.xpert_store.get_xpert(xpert_id)
            version = self.xpert_store.get_version(xpert.id, version_number)
            source = {
                "kind": kind,
                "xpert_id": xpert.id,
                "version": version.version,
                "proposal_id": None,
                "proposal_revision": None,
            }
            label = str(reference.get("label") or f"{xpert.name} v{version.version}")
        elif kind == "proposal":
            proposal_id = str(reference.get("proposal_id") or "")
            expected_revision = int(reference.get("proposal_revision") or 0)
            proposal = self.proposal_store.require(proposal_id)
            if proposal.revision != expected_revision:
                raise EvaluationConflictError(
                    "Authoring Proposal changed before the evaluation snapshot was created."
                )
            if proposal.kind not in {"xpert_create", "xpert_update"}:
                raise EvaluationStateError("Only Xpert authoring proposals can be evaluated.")
            xpert = self._candidate_from_proposal(proposal)
            validation, workflow, prompt_profiles = self.prompt_preflight(xpert)
            errors = [
                issue.message
                for issue in validation.issues
                if getattr(issue, "severity", "error") == "error"
            ]
            if errors:
                raise EvaluationStateError(
                    "Proposal cannot be evaluated: " + "; ".join(errors[:10])
                )
            version = XpertVersion(
                version=max(1, int(xpert.published_version or 0) + 1),
                draft_revision=xpert.draft_revision,
                workflow=workflow,
                input_variable=xpert.draft.input_variable,
                history_variable=xpert.draft.history_variable,
                output_variable=xpert.draft.output_variable,
                agent_config=xpert.draft.agent_config,
                features=xpert.draft.features,
                prompt_profiles=prompt_profiles,
                release_notes="Ephemeral evaluation snapshot",
                checksum=self._checksum(
                    {
                        "workflow": workflow.model_dump(mode="json"),
                        "revision": proposal.revision,
                    }
                ),
                published_at=time.time(),
            )
            source = {
                "kind": kind,
                "xpert_id": xpert.id,
                "version": None,
                "proposal_id": proposal.proposal_id,
                "proposal_revision": proposal.revision,
            }
            label = str(reference.get("label") or proposal.title)
        else:
            raise EvaluationStateError("Unsupported evaluation target kind.")

        if xpert.id in recursion_path:
            raise EvaluationStateError(
                "External Xpert collaboration cycle detected in evaluation target."
            )
        workflow = version.workflow.model_copy(deep=True)
        if model_policy == "override":
            clean_model = str(override_model_id or "").strip()
            if not clean_model:
                raise EvaluationStateError("Override model is required.")
            for node in workflow.nodes:
                data = node.data if isinstance(node.data, dict) else {}
                node_kind = str(data.get("kind") or node.type or "")
                if node_kind in {"llm", "workflow_agent"}:
                    data["modelId"] = clean_model
                    node.data = data

        issues, warnings, resources = self._safe_preflight(
            workflow,
            recursion_path=(*recursion_path, xpert.id),
        )
        validation = validate_xpert_workflow_graph(
            workflow,
            history_variable=version.history_variable,
        )
        issues.extend(
            {
                "code": issue.code,
                "message": issue.message,
                "node_id": issue.node_id,
            }
            for issue in validation.issues
            if issue.severity == "error"
        )
        if issues:
            raise EvaluationStateError(
                "Evaluation safety preflight failed: "
                + "; ".join(str(item["message"]) for item in issues[:10])
            )

        target_id = (
            f"xpert:{source['xpert_id']}:v{source['version']}"
            if kind == "xpert_version"
            else f"proposal:{source['proposal_id']}:r{source['proposal_revision']}"
        )
        snapshot = {
            "target_id": target_id,
            "label": label[:160],
            "source": source,
            "xpert": {
                "id": xpert.id,
                "slug": xpert.slug,
                "name": xpert.name,
                "description": xpert.description,
            },
            "workflow": workflow.model_dump(mode="json"),
            "input_variable": version.input_variable,
            "history_variable": version.history_variable,
            "output_variable": version.output_variable,
            "agent_config": (
                version.agent_config.model_dump(mode="json")
                if version.agent_config is not None
                else None
            ),
            "features": (
                version.features.model_dump(mode="json")
                if version.features is not None
                else None
            ),
            "prompt_profiles": [
                item.model_dump(mode="json") for item in version.prompt_profiles
            ],
            "checksum": self._checksum(
                {
                    "workflow": workflow.model_dump(mode="json"),
                    "source": source,
                    "resources": resources,
                    "model_policy": model_policy,
                    "override_model_id": override_model_id,
                }
            ),
            "resources": resources,
            "warnings": warnings,
            "created_at": time.time(),
        }
        return snapshot, warnings

    def preflight(
        self,
        *,
        baseline: dict[str, Any] | None,
        candidates: list[dict[str, Any]],
        model_policy: str,
        override_model_id: str | None,
    ) -> dict[str, Any]:
        snapshots: list[dict[str, Any]] = []
        warnings: list[str] = []
        baseline_snapshot = None
        if baseline:
            baseline_snapshot, target_warnings = self.snapshot_target(
                baseline,
                model_policy=model_policy,
                override_model_id=override_model_id,
            )
            warnings.extend(target_warnings)
            snapshots.append(self.public_target_payload(baseline_snapshot))
        candidate_snapshots = []
        for reference in candidates:
            snapshot, target_warnings = self.snapshot_target(
                reference,
                model_policy=model_policy,
                override_model_id=override_model_id,
            )
            candidate_snapshots.append(snapshot)
            snapshots.append(self.public_target_payload(snapshot))
            warnings.extend(target_warnings)
        return {
            "valid": True,
            "baseline": self.public_target_payload(baseline_snapshot)
            if baseline_snapshot
            else None,
            "candidates": [self.public_target_payload(item) for item in candidate_snapshots],
            "targets": snapshots,
            "warnings": list(dict.fromkeys(warnings)),
        }

    def snapshot_xpert_draft(
        self,
        xpert: XpertDefinition,
        *,
        source: dict[str, Any],
        label: str,
        model_policy: str,
        override_model_id: str | None,
        target_id: str,
        input_template: str | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Create an internal read-only snapshot without a temporary proposal."""
        validation, workflow, prompt_profiles = self.prompt_preflight(xpert)
        errors = [
            issue.message
            for issue in validation.issues
            if getattr(issue, "severity", "error") == "error"
        ]
        if errors:
            raise EvaluationStateError(
                "Evolution candidate cannot be evaluated: " + "; ".join(errors[:10])
            )
        workflow = workflow.model_copy(deep=True)
        if model_policy == "override":
            clean_model = str(override_model_id or "").strip()
            if not clean_model:
                raise EvaluationStateError("Override model is required.")
            for node in workflow.nodes:
                data = node.data if isinstance(node.data, dict) else {}
                node_kind = str(data.get("kind") or node.type or "")
                if node_kind in {"llm", "workflow_agent"}:
                    data["modelId"] = clean_model
                    node.data = data
        issues, warnings, resources = self._safe_preflight(
            workflow,
            recursion_path=(xpert.id,),
        )
        graph_validation = validate_xpert_workflow_graph(
            workflow,
            history_variable=xpert.draft.history_variable,
        )
        issues.extend(
            {
                "code": issue.code,
                "message": issue.message,
                "node_id": issue.node_id,
            }
            for issue in graph_validation.issues
            if issue.severity == "error"
        )
        if issues:
            raise EvaluationStateError(
                "Evaluation safety preflight failed: "
                + "; ".join(str(item["message"]) for item in issues[:10])
            )
        snapshot = {
            "target_id": str(target_id)[:240],
            "label": str(label)[:160],
            "source": copy.deepcopy(source),
            "xpert": {
                "id": xpert.id,
                "slug": xpert.slug,
                "name": xpert.name,
                "description": xpert.description,
            },
            "workflow": workflow.model_dump(mode="json"),
            "input_variable": xpert.draft.input_variable,
            "history_variable": xpert.draft.history_variable,
            "output_variable": xpert.draft.output_variable,
            "agent_config": (
                xpert.draft.agent_config.model_dump(mode="json")
                if xpert.draft.agent_config is not None
                else None
            ),
            "features": (
                xpert.draft.features.model_dump(mode="json")
                if xpert.draft.features is not None
                else None
            ),
            "prompt_profiles": [
                item.model_dump(mode="json") for item in prompt_profiles
            ],
            "input_template": str(input_template or "")[:20_000] or None,
            "checksum": self._checksum(
                {
                    "workflow": workflow.model_dump(mode="json"),
                    "source": source,
                    "resources": resources,
                    "model_policy": model_policy,
                    "override_model_id": override_model_id,
                    "input_template": input_template,
                }
            ),
            "resources": resources,
            "warnings": warnings,
            "created_at": time.time(),
        }
        return snapshot, warnings

    def create_run_from_snapshots(
        self,
        *,
        dataset_version: dict[str, Any],
        cases: list[dict[str, Any]],
        baseline: dict[str, Any] | None,
        candidates: list[dict[str, Any]],
        config: dict[str, Any],
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        """Internal optimizer entry that keeps public Evaluation targets unchanged."""
        if not candidates:
            raise EvaluationStateError("At least one candidate snapshot is required.")
        selected = [copy.deepcopy(item) for item in cases]
        if not selected:
            raise EvaluationStateError("No evaluation cases were selected.")
        return self.store.create_run(
            dataset_version=copy.deepcopy(dataset_version),
            cases=selected,
            baseline=copy.deepcopy(baseline),
            candidates=copy.deepcopy(candidates),
            config=copy.deepcopy(config),
            warnings=list(dict.fromkeys(warnings or [])),
        )

    def create_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        dataset = self.store.get_dataset_version(
            str(payload["dataset_id"]),
            int(payload["dataset_version"]),
        )
        selected_ids = {
            str(item) for item in list(payload.get("case_ids") or []) if str(item)
        }
        cases = [
            case
            for case in dataset.get("cases") or []
            if not selected_ids or str(case.get("case_id")) in selected_ids
        ]
        if not cases:
            raise EvaluationStateError("No evaluation cases were selected.")
        if len(cases) > self.store.MAX_RUN_CASES:
            raise EvaluationStateError("A run may select at most 100 cases.")
        model_policy = str(payload.get("model_policy") or "snapshot")
        override_model_id = payload.get("override_model_id")
        warnings: list[str] = []
        baseline_snapshot = None
        if payload.get("baseline"):
            baseline_snapshot, current = self.snapshot_target(
                dict(payload["baseline"]),
                model_policy=model_policy,
                override_model_id=override_model_id,
            )
            warnings.extend(current)
        candidate_snapshots = []
        for reference in payload.get("candidates") or []:
            snapshot, current = self.snapshot_target(
                dict(reference),
                model_policy=model_policy,
                override_model_id=override_model_id,
            )
            candidate_snapshots.append(snapshot)
            warnings.extend(current)
        return self.store.create_run(
            dataset_version=dataset,
            cases=cases,
            baseline=baseline_snapshot,
            candidates=candidate_snapshots,
            config={
                "model_policy": model_policy,
                "override_model_id": override_model_id,
                "judge_model_id": payload.get("judge_model_id"),
                "seed": int(payload.get("seed") or 0),
                "budget": copy.deepcopy(payload.get("budget") or {}),
            },
            warnings=list(dict.fromkeys(warnings)),
        )

    def run_detail(self, run_id: str) -> dict[str, Any]:
        run = self.store.require_run(run_id)
        for target in run.get("targets") or []:
            source = dict(target.get("source") or {})
            if source.get("kind") == "proposal":
                try:
                    current = self.proposal_store.require(str(source["proposal_id"]))
                    target["stale"] = (
                        current.revision != int(source.get("proposal_revision") or 0)
                    )
                except Exception:
                    target["stale"] = True
            else:
                target["stale"] = False
        return self.store.run_payload(run, include_detail=True)

    def import_conversations(
        self,
        dataset_id: str,
        *,
        revision: int,
        selections: list[dict[str, Any]],
    ) -> dict[str, Any]:
        cases: list[dict[str, Any]] = []
        for selection in selections:
            conversation = self.context_store.get_conversation(
                str(selection["xpert_id"]),
                str(selection["conversation_id"]),
            )
            selected = set(selection.get("message_ids") or [])
            history: list[dict[str, str]] = []
            for message in conversation.messages:
                if selected and message.message_id not in selected:
                    history.append({"role": message.role, "content": message.content})
                    continue
                if message.role != "user":
                    history.append({"role": message.role, "content": message.content})
                    continue
                cases.append(
                    {
                        "name": message.content[:80],
                        "message": message.content,
                        "messages": history[-20:],
                        "tags": ["conversation-import"],
                        "expected": {},
                    }
                )
                history.append({"role": message.role, "content": message.content})
        if not cases:
            raise EvaluationStateError("No selected user messages were found.")
        return self.store.put_cases(
            dataset_id,
            revision=revision,
            cases=cases,
        )

    @staticmethod
    def public_target_payload(target: dict[str, Any] | None) -> dict[str, Any] | None:
        if target is None:
            return None
        return {
            "target_id": target["target_id"],
            "label": target["label"],
            "source": copy.deepcopy(target["source"]),
            "xpert": copy.deepcopy(target["xpert"]),
            "checksum": target["checksum"],
            "resources": copy.deepcopy(target.get("resources") or {}),
            "warnings": list(target.get("warnings") or []),
            "stale": bool(target.get("stale", False)),
        }

    def _candidate_from_proposal(self, proposal: Any) -> XpertDefinition:
        payload = dict(proposal.payload or {})
        if proposal.kind == "xpert_create":
            draft = XpertDraft.model_validate(payload.get("draft"))
            return XpertDefinition(
                id=f"proposal-{proposal.proposal_id}",
                slug=str(payload.get("slug") or f"proposal-{proposal.proposal_id}")[:64],
                name=str(payload.get("name") or proposal.title),
                description=str(payload.get("description") or ""),
                tags=list(payload.get("tags") or []),
                starters=list(payload.get("starters") or []),
                draft_revision=proposal.revision,
                draft=draft,
                created_at=proposal.created_at,
                updated_at=proposal.updated_at,
            )
        target_id = proposal.target_id or str(payload.get("xpert_id") or "")
        current = self.xpert_store.get_xpert(target_id)
        if current.draft_revision != proposal.base_revision:
            raise EvaluationConflictError(
                "Target Xpert draft changed after this proposal was created."
            )
        candidate = current.model_copy(deep=True)
        patch = dict(payload.get("patch") or {})
        for field in ("name", "description", "tags", "starters"):
            if field in patch:
                setattr(candidate, field, copy.deepcopy(patch[field]))
        if "draft" in patch:
            candidate.draft = XpertDraft.model_validate(patch["draft"])
        candidate.draft_revision = proposal.revision
        return candidate

    def _safe_preflight(
        self,
        workflow: NativeWorkflowDefinition,
        *,
        recursion_path: tuple[str, ...],
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        warnings: list[str] = []
        resources: dict[str, Any] = {
            "toolsets": [],
            "knowledge_versions": [],
            "external_xperts": [],
            "plugins": [],
        }
        for node in workflow.nodes:
            data = node.data if isinstance(node.data, dict) else {}
            kind = str(data.get("kind") or node.type or "")
            if kind in UNSAFE_NODE_KINDS:
                issues.append(
                    {
                        "code": "evaluation_unsafe_node",
                        "message": f"Evaluation does not allow node kind: {kind}.",
                        "node_id": node.id,
                    }
                )
            if kind == "runtime_middleware":
                middleware_id = str(data.get("runtimeMiddlewareId") or "")
                config = dict(data.get("runtimeMiddlewareConfig") or {})
                if middleware_id in UNSAFE_MIDDLEWARE_IDS:
                    issues.append(
                        {
                            "code": "evaluation_unsafe_middleware",
                            "message": (
                                "Evaluation does not allow side-effect or waiting middleware: "
                                f"{middleware_id}."
                            ),
                            "node_id": node.id,
                        }
                    )
                if middleware_id == "xpert_file_memory" and bool(
                    config.get("writeback_enabled")
                ):
                    issues.append(
                        {
                            "code": "evaluation_memory_write",
                            "message": "Evaluation disables Xpert memory writeback.",
                            "node_id": node.id,
                        }
                    )
            if kind == "workflow_agent":
                if self._truthy(data.get("memoryWriteEnabled")):
                    issues.append(
                        {
                            "code": "evaluation_memory_write",
                            "message": "Evaluation disables memory writes.",
                            "node_id": node.id,
                        }
                    )
                if self._truthy(data.get("knowledgeWriteEnabled")):
                    issues.append(
                        {
                            "code": "evaluation_knowledge_write",
                            "message": "Evaluation disables knowledge proposals.",
                            "node_id": node.id,
                        }
                    )
            if kind == "toolset_resource":
                try:
                    toolset_id = str(data.get("toolsetId") or "").strip()
                    version_number = int(data.get("pinnedVersion") or 0)
                    snapshot = self.toolset_store.get_version(toolset_id, version_number)
                    unsafe = [
                        tool.exposed_name
                        for tool in snapshot.tools
                        if tool.enabled and (not tool.read_only or tool.sensitive)
                    ]
                    if unsafe:
                        raise ValueError(
                            "Toolset includes mutable or sensitive tools: "
                            + ", ".join(unsafe[:10])
                        )
                    resources["toolsets"].append(
                        {
                            "toolset_id": toolset_id,
                            "version": version_number,
                            "schema_hash": snapshot.schema_hash,
                        }
                    )
                except Exception as exc:
                    issues.append(
                        {
                            "code": "evaluation_toolset_unsafe",
                            "message": str(exc),
                            "node_id": node.id,
                        }
                    )
            if kind == "knowledge_base":
                kb_id = str(data.get("knowledgeBaseId") or "").strip()
                try:
                    active = self.rag_service.get_active_pipeline_version(kb_id)
                    if not active:
                        warnings.append(
                            f"Knowledge base {kb_id} has no active pipeline version."
                        )
                    resources["knowledge_versions"].append(
                        {
                            "knowledge_base_id": kb_id,
                            "version_id": (
                                str(active.get("version_id") or "") if active else None
                            ),
                        }
                    )
                    data["evaluationPinnedVersionId"] = (
                        str(active.get("version_id") or "") if active else ""
                    )
                    node.data = data
                except Exception as exc:
                    issues.append(
                        {
                            "code": "evaluation_knowledge_invalid",
                            "message": str(exc),
                            "node_id": node.id,
                        }
                    )
            if kind == "external_xpert":
                try:
                    target_id = str(data.get("xpertId") or "").strip()
                    target_version = int(data.get("pinnedVersion") or 0)
                    target = self.xpert_store.get_xpert(target_id)
                    if target.id in recursion_path:
                        raise ValueError("External Xpert evaluation cycle detected.")
                    version = self.xpert_store.get_version(target.id, target_version)
                    nested_issues, nested_warnings, _ = self._safe_preflight(
                        version.workflow.model_copy(deep=True),
                        recursion_path=(*recursion_path, target.id),
                    )
                    issues.extend(nested_issues)
                    warnings.extend(nested_warnings)
                    resources["external_xperts"].append(
                        {
                            "xpert_id": target.id,
                            "version": version.version,
                            "checksum": version.checksum,
                        }
                    )
                except Exception as exc:
                    issues.append(
                        {
                            "code": "evaluation_external_xpert_unsafe",
                            "message": str(exc),
                            "node_id": node.id,
                        }
                    )
            if kind == "plugin_resource":
                self._inspect_plugin(node, data, issues, resources)
        return issues, warnings, resources

    def _inspect_plugin(
        self,
        node: Any,
        data: dict[str, Any],
        issues: list[dict[str, Any]],
        resources: dict[str, Any],
    ) -> None:
        try:
            plugin_id = str(data.get("pluginId") or "").strip()
            version_number = int(data.get("pinnedVersion") or 0)
            snapshot = self.plugin_store.get_version(plugin_id, version_number)
            if snapshot.skills or snapshot.installed_skill_ids:
                raise ValueError("Evaluation does not allow Plugin Skill execution.")
            unsafe_presets = [
                item.middleware_id
                for item in snapshot.middleware_presets
                if item.middleware_id in UNSAFE_MIDDLEWARE_IDS
            ]
            if unsafe_presets:
                raise ValueError(
                    "Plugin contains unsafe middleware: "
                    + ", ".join(unsafe_presets[:10])
                )
            for reference in snapshot.toolsets:
                toolset = self.toolset_store.get_version(
                    reference.toolset_id, reference.version
                )
                unsafe = [
                    tool.exposed_name
                    for tool in toolset.tools
                    if tool.enabled and (not tool.read_only or tool.sensitive)
                ]
                if unsafe:
                    raise ValueError(
                        "Plugin Toolset contains mutable or sensitive tools: "
                        + ", ".join(unsafe[:10])
                    )
            resources["plugins"].append(
                {
                    "plugin_id": plugin_id,
                    "version": version_number,
                    "checksum": snapshot.package_checksum,
                }
            )
        except Exception as exc:
            issues.append(
                {
                    "code": "evaluation_plugin_unsafe",
                    "message": str(exc),
                    "node_id": node.id,
                }
            )

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @staticmethod
    def _checksum(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
