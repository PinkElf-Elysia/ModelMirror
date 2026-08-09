from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any

from pydantic import ValidationError

try:
    from server.evaluations.models import EvaluationCaseInput
    from server.evaluations.service import XpertEvaluationService
    from server.evaluations.store import (
        EvaluationConflictError,
        EvaluationStateError,
        XpertEvaluationStore,
    )
except ModuleNotFoundError:
    from evaluations.models import EvaluationCaseInput
    from evaluations.service import XpertEvaluationService
    from evaluations.store import (
        EvaluationConflictError,
        EvaluationStateError,
        XpertEvaluationStore,
    )


SUPPORTED_COVERAGE = {
    "instruction_following",
    "structured_output",
    "multi_turn",
    "tool_routing",
    "knowledge_citation",
    "prompt_command",
}

PRESSURE_TYPES_BY_COVERAGE: dict[str, tuple[str, ...]] = {
    "instruction_following": ("competing_constraints", "domain_exception", "ambiguity"),
    "structured_output": ("schema_boundary", "competing_constraints"),
    "multi_turn": ("conflicting_context", "cross_turn_override"),
    "tool_routing": ("tool_decoy", "competing_constraints"),
    "knowledge_citation": ("missing_evidence", "conflicting_context"),
    "prompt_command": ("ambiguity", "competing_constraints"),
}

GENERIC_FOCUS_TERMS = {
    "agent",
    "answer",
    "assistant",
    "complete",
    "context",
    "current",
    "directly",
    "execute",
    "final",
    "follow",
    "model",
    "output",
    "request",
    "response",
    "result",
    "task",
    "user",
    "workflow",
    "and",
    "are",
    "evaluate",
    "for",
    "from",
    "must",
    "review",
    "should",
    "the",
    "with",
    "you",
    "\u4e00\u4e2a",
    "\u4e0a\u4e0b\u6587",
    "\u4efb\u52a1",
    "\u4f7f\u7528",
    "\u52a9\u624b",
    "\u56de\u7b54",
    "\u5b8c\u6210",
    "\u5f53\u524d",
    "\u6267\u884c",
    "\u667a\u80fd\u4f53",
    "\u6a21\u578b",
    "\u7528\u6237",
    "\u76f4\u63a5",
    "\u7ed3\u679c",
    "\u7ed3\u5408",
    "\u8bf7\u6c42",
    "\u8f93\u51fa",
    "\u4f60\u662f",
    "\u4f5c\u4e3a",
    "\u4e13\u5bb6",
    "\u8bc4\u4f30",
    "\u6838\u5bf9",
    "\u8d1f\u8d23",
    "\u5fc5\u987b",
}

GENERIC_EVIDENCE_TERMS = GENERIC_FOCUS_TERMS | {
    "analysis",
    "assess",
    "case",
    "decision",
    "describe",
    "determine",
    "evidence",
    "explain",
    "professional",
    "provide",
    "recommend",
    "scenario",
    "specialist",
    "verify",
}


class BenchmarkGenerationError(RuntimeError):
    pass


class BenchmarkGenerationService:
    """Builds safe target snapshots and validates generated benchmark drafts."""

    def __init__(
        self,
        *,
        evaluation_store: XpertEvaluationStore,
        evaluation_service: XpertEvaluationService,
        xpert_store: Any,
        proposal_store: Any,
        prompt_store: Any,
        context_store: Any,
        rag_service: Any | None = None,
        toolset_store: Any | None = None,
    ) -> None:
        self.evaluation_store = evaluation_store
        self.evaluation_service = evaluation_service
        self.xpert_store = xpert_store
        self.proposal_store = proposal_store
        self.prompt_store = prompt_store
        self.context_store = context_store
        self.rag_service = rag_service
        self.toolset_store = toolset_store

    def capabilities(self) -> dict[str, Any]:
        return {
            "version": "evoagentx-benchmark-generator-v6",
            "target_kinds": [
                "xpert_draft",
                "xpert_version",
                "proposal",
                "prompt_profile",
            ],
            "coverage": sorted(SUPPORTED_COVERAGE),
            "case_count": {"default": 12, "min": 6, "max": 30},
            "locales": ["zh-CN", "en-US"],
            "generator_model_required": True,
            "generation_model_calls": {"generate": 1, "repair": 1},
            "calibration": {
                "repetitions": 1,
                "max_concurrency": 2,
                "case_timeout_seconds": 120,
                "rewrites_gold": False,
                "generic_counterfactual": True,
                "targeting_advantage_threshold": 0.10,
            },
            "targeting": {
                "required": True,
                "difficulties": ["basic", "edge", "adversarial"],
                "target_refs_required_per_case": True,
                "rationale_required_per_case": True,
                "capability_matrix_size": {"min": 1, "max": 3},
                "combined_case_min_ratio": 0.60,
                "professional_focus_required_when_available": True,
                "pressure_types_required_for": ["edge", "adversarial"],
            },
        }

    def preflight(
        self,
        *,
        target_reference: dict[str, Any],
        requested_coverage: list[str] | None = None,
        conversation_selections: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        snapshot, warnings = self.snapshot_target(target_reference)
        coverage = self.detect_coverage(snapshot)
        selected = [str(item) for item in list(requested_coverage or []) if str(item)]
        unknown = sorted(set(selected) - SUPPORTED_COVERAGE)
        unavailable = sorted(set(selected) - set(coverage["available"]))
        seeds = self.conversation_seeds(conversation_selections or [])
        target_anchors = self.target_anchors(snapshot)
        focus_terms = list(
            dict.fromkeys(
                str(term)
                for anchor in target_anchors
                for term in list(anchor.get("focus_terms") or [])
                if str(term).strip()
            )
        )
        if not focus_terms:
            warnings.append(
                "Target exposes no professional focus terms; generated cases can only "
                "prove generic execution contracts until the target prompt is specialized."
            )
        issues: list[dict[str, Any]] = []
        if unknown:
            issues.append(
                {
                    "code": "benchmark_coverage_unknown",
                    "message": f"Unsupported coverage: {', '.join(unknown)}",
                }
            )
        if unavailable:
            issues.append(
                {
                    "code": "benchmark_coverage_unavailable",
                    "message": (
                        "Target does not expose the requested capability: "
                        + ", ".join(unavailable)
                    ),
                }
            )
        return {
            "valid": not issues,
            "target": self.public_target(snapshot),
            "coverage": coverage,
            "target_anchors": target_anchors,
            "targeting": {
                "focus_term_count": len(focus_terms),
                "focus_terms": focus_terms[:20],
                "domain_anchor_count": sum(
                    1 for anchor in target_anchors if anchor.get("axis") == "domain"
                ),
                "resource_anchor_count": sum(
                    1 for anchor in target_anchors if anchor.get("axis") == "resource"
                ),
            },
            "conversation_seed_count": len(seeds),
            "warnings": list(dict.fromkeys(warnings)),
            "issues": issues,
        }

    def snapshot_target(
        self, reference: dict[str, Any]
    ) -> tuple[dict[str, Any], list[str]]:
        kind = str(reference.get("kind") or "")
        if kind in {"xpert_version", "proposal"}:
            snapshot, warnings = self.evaluation_service.snapshot_target(
                reference,
                model_policy="snapshot",
                override_model_id=None,
            )
            return snapshot, warnings
        if kind == "xpert_draft":
            xpert_id = str(reference.get("xpert_id") or "")
            revision = int(reference.get("draft_revision") or 0)
            xpert = self.xpert_store.get_xpert(xpert_id)
            if xpert.draft_revision != revision:
                raise EvaluationConflictError(
                    "Xpert draft changed before the Benchmark snapshot was created."
                )
            return self.evaluation_service.snapshot_xpert_draft(
                xpert,
                source={
                    "kind": "xpert_draft",
                    "xpert_id": xpert.id,
                    "draft_revision": revision,
                },
                label=str(reference.get("label") or f"{xpert.name} draft r{revision}"),
                model_policy="snapshot",
                override_model_id=None,
                target_id=f"xpert-draft:{xpert.id}:r{revision}",
            )
        if kind == "prompt_profile":
            profile_id = str(reference.get("prompt_profile_id") or "")
            profile_revision = int(reference.get("prompt_profile_revision") or 0)
            profile = self.prompt_store.get_profile(profile_id)
            if profile.draft_revision != profile_revision:
                raise EvaluationConflictError(
                    "Prompt Profile changed before the Benchmark snapshot was created."
                )
            host_reference = {
                "kind": "xpert_version",
                "xpert_id": str(reference.get("host_xpert_id") or ""),
                "version": int(reference.get("host_xpert_version") or 0),
                "label": str(reference.get("label") or profile.name),
            }
            snapshot, warnings = self.evaluation_service.snapshot_target(
                host_reference,
                model_policy="snapshot",
                override_model_id=None,
            )
            snapshot["target_id"] = (
                f"prompt-profile:{profile.id}:r{profile_revision}:host:"
                f"{host_reference['xpert_id']}:v{host_reference['version']}"
            )
            snapshot["label"] = str(
                reference.get("label") or f"{profile.name} r{profile_revision}"
            )[:160]
            snapshot["source"] = {
                "kind": "prompt_profile",
                "prompt_profile_id": profile.id,
                "prompt_profile_revision": profile_revision,
                "host_xpert_id": host_reference["xpert_id"],
                "host_xpert_version": host_reference["version"],
            }
            snapshot["input_template"] = profile.template
            snapshot["prompt_profile"] = {
                "id": profile.id,
                "name": profile.name,
                "aliases": list(profile.aliases),
                "argument_hint": profile.argument_hint,
                "template": profile.template,
            }
            snapshot["checksum"] = self._checksum(
                {
                    "host_checksum": snapshot.get("checksum"),
                    "profile_id": profile.id,
                    "profile_revision": profile_revision,
                    "template": profile.template,
                }
            )
            return snapshot, warnings
        raise EvaluationStateError("Unsupported Benchmark generation target kind.")

    def target_is_fresh(self, reference: dict[str, Any], checksum: str) -> bool:
        try:
            snapshot, _ = self.snapshot_target(reference)
        except Exception:
            return False
        return str(snapshot.get("checksum") or "") == str(checksum or "")

    def detect_coverage(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        workflow = dict(snapshot.get("workflow") or {})
        nodes = list(workflow.get("nodes") or [])
        available = {"instruction_following", "multi_turn"}
        reasons: dict[str, str] = {
            "instruction_following": "The target contains an executable Agent prompt.",
            "multi_turn": "The Xpert execution contract accepts bounded conversation history.",
        }
        tool_names: list[str] = self._resolved_tool_names(snapshot)
        knowledge_ids: list[str] = []
        for node in nodes:
            data = dict(node.get("data") or {}) if isinstance(node, dict) else {}
            kind = str(data.get("kind") or node.get("type") or "")
            if kind == "workflow_agent":
                if str(data.get("outputSchemaMode") or "none") not in {"", "none"}:
                    available.add("structured_output")
                    reasons["structured_output"] = "The Agent declares a structured output contract."
            elif kind in {"mcp_tool", "toolset_resource", "external_xpert"}:
                pass
            elif kind in {"knowledge_base", "knowledge_retrieval", "knowledge_citation"}:
                available.add("knowledge_citation")
                reasons["knowledge_citation"] = "The workflow includes knowledge retrieval or citation."
                kb_id = str(data.get("knowledgeBaseId") or data.get("kbId") or "")
                if kb_id:
                    knowledge_ids.append(kb_id)
            elif kind == "runtime_middleware" and str(
                data.get("middlewareId") or data.get("middleware_id") or ""
            ) == "structured_output":
                available.add("structured_output")
                reasons["structured_output"] = "Structured output middleware is bound."
        prompt_profile = dict(snapshot.get("prompt_profile") or {})
        prompt_profiles = list(snapshot.get("prompt_profiles") or [])
        if prompt_profile or prompt_profiles:
            available.add("prompt_command")
            reasons["prompt_command"] = "The target exposes fixed Prompt Profile commands."
        if tool_names:
            available.add("tool_routing")
            reasons["tool_routing"] = "The target exposes fixed callable tool names."
        knowledge_documents = self._knowledge_document_names(snapshot)
        if knowledge_ids and knowledge_documents:
            available.add("knowledge_citation")
            reasons["knowledge_citation"] = (
                "The fixed active knowledge version exposes safe document references."
            )
        elif "knowledge_citation" in available:
            available.remove("knowledge_citation")
            reasons.pop("knowledge_citation", None)
        return {
            "available": sorted(available),
            "recommended": sorted(available),
            "reasons": reasons,
            "tool_names": sorted(set(tool_names))[:100],
            "knowledge_base_ids": sorted(set(knowledge_ids))[:20],
        }

    def target_anchors(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        """Return bounded, safe evidence that generated cases must explicitly cite."""

        anchors: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(
            anchor_id: str,
            *,
            kind: str,
            axis: str,
            label: str,
            summary: str,
            coverage: list[str],
        ) -> None:
            normalized_id = str(anchor_id or "")[:160]
            if not normalized_id or normalized_id in seen:
                return
            seen.add(normalized_id)
            anchors.append(
                {
                    "id": normalized_id,
                    "kind": kind[:80],
                    "axis": axis[:40],
                    "label": str(label or kind)[:160],
                    "summary": self._safe_excerpt(summary, 280),
                    "focus_terms": self._focus_terms(summary),
                    "coverage": [
                        item for item in coverage if item in SUPPORTED_COVERAGE
                    ],
                }
            )

        xpert = dict(snapshot.get("xpert") or {})
        description = str(xpert.get("description") or "").strip()
        if description:
            add(
                "xpert:purpose",
                kind="xpert_purpose",
                axis="domain",
                label="Xpert purpose",
                summary=description,
                coverage=["instruction_following"],
            )

        workflow = dict(snapshot.get("workflow") or {})
        knowledge_document_names = self._knowledge_document_names(snapshot)
        for index, node in enumerate(list(workflow.get("nodes") or [])):
            if not isinstance(node, dict):
                continue
            data = dict(node.get("data") or {})
            node_kind = str(data.get("kind") or node.get("type") or "")
            node_key = self._anchor_key(node.get("id") or f"node-{index + 1}")
            title = str(data.get("title") or node_kind or f"Node {index + 1}")[:160]
            if node_kind == "workflow_agent":
                role_prompt = str(data.get("rolePrompt") or "").strip()
                prompt_suffix = str(data.get("promptSuffix") or "").strip()
                task_input = str(data.get("taskInput") or "").strip()
                output_schema = str(data.get("outputSchemaJson") or "").strip()
                if role_prompt:
                    add(
                        f"agent:{node_key}:role_prompt",
                        kind="agent_prompt",
                        axis="domain",
                        label=f"{title} rolePrompt",
                        summary=role_prompt,
                        coverage=["instruction_following"],
                    )
                else:
                    add(
                        f"agent:{node_key}:execution_contract",
                        kind="agent_contract",
                        axis="contract",
                        label=f"{title} execution contract",
                        summary=f"Agent {title} must directly complete its assigned task.",
                        coverage=["instruction_following"],
                    )
                if prompt_suffix:
                    add(
                        f"agent:{node_key}:prompt_suffix",
                        kind="agent_prompt",
                        axis="domain",
                        label=f"{title} promptSuffix",
                        summary=prompt_suffix,
                        coverage=["instruction_following"],
                    )
                if "conversation_history" in task_input:
                    add(
                        f"agent:{node_key}:conversation_contract",
                        kind="conversation_contract",
                        axis="contract",
                        label=f"{title} conversation history",
                        summary="The Agent receives bounded conversation_history and must resolve recent context and conflicts.",
                        coverage=["multi_turn"],
                    )
                if str(data.get("outputSchemaMode") or "none") not in {"", "none"}:
                    add(
                        f"agent:{node_key}:output_schema",
                        kind="output_schema",
                        axis="contract",
                        label=f"{title} structured output",
                        summary=output_schema or "The Agent final output must satisfy its configured JSON Schema.",
                        coverage=["structured_output"],
                    )
                for tool_name in self._string_list(data.get("toolNames"))[:20]:
                    add(
                        f"tool:{self._anchor_key(tool_name)}",
                        kind="tool",
                        axis="resource",
                        label=f"Tool {tool_name}",
                        summary=f"The Agent may route an eligible request to {tool_name}.",
                        coverage=["tool_routing"],
                    )
            elif node_kind in {"mcp_tool", "toolset_resource", "external_xpert"}:
                add(
                    f"resource:{node_key}",
                    kind=node_kind,
                    axis="resource",
                    label=title,
                    summary=str(data.get("description") or f"Callable {node_kind} resource {title}."),
                    coverage=["tool_routing"],
                )
            elif node_kind in {"knowledge_base", "knowledge_retrieval", "knowledge_citation"}:
                kb_id = str(data.get("knowledgeBaseId") or data.get("kbId") or node_key)
                add(
                    f"knowledge:{self._anchor_key(kb_id)}",
                    kind="knowledge",
                    axis="resource",
                    label=title,
                    summary=(
                        str(
                            data.get("description")
                            or f"Knowledge source {title} must ground retrievable claims."
                        )
                        + (
                            " Fixed document names: "
                            + ", ".join(knowledge_document_names[:20])
                            if knowledge_document_names
                            else ""
                        )
                    ),
                    coverage=["knowledge_citation"],
                )
            elif node_kind == "runtime_middleware" and str(
                data.get("middlewareId") or data.get("middleware_id") or ""
            ) == "structured_output":
                add(
                    f"middleware:{node_key}:structured_output",
                    kind="output_schema",
                    axis="contract",
                    label=f"{title} structured output",
                    summary=str(data.get("outputSchemaJson") or "The final answer must satisfy the bound structured-output contract."),
                    coverage=["structured_output"],
                )

        if not any("multi_turn" in anchor["coverage"] for anchor in anchors):
            add(
                "runtime:conversation_history",
                kind="conversation_contract",
                axis="contract",
                label="Bounded conversation contract",
                summary="The Xpert runtime supplies up to 20 history messages; the current user message remains authoritative.",
                coverage=["multi_turn"],
            )

        prompt_profiles = [dict(item) for item in list(snapshot.get("prompt_profiles") or []) if isinstance(item, dict)]
        direct_profile = dict(snapshot.get("prompt_profile") or {})
        if direct_profile:
            prompt_profiles.insert(0, direct_profile)
        for index, profile in enumerate(prompt_profiles[:10]):
            profile_key = self._anchor_key(profile.get("id") or profile.get("name") or index)
            aliases = ", ".join(str(item) for item in list(profile.get("aliases") or [])[:5])
            add(
                f"prompt_profile:{profile_key}",
                kind="prompt_command",
                axis="resource",
                label=str(profile.get("name") or aliases or "Prompt command"),
                summary=(
                    (f"Command aliases: {aliases}. " if aliases else "")
                    + str(profile.get("template") or profile.get("description") or "")
                ),
                coverage=["prompt_command"],
            )

        for tool_name in self._resolved_tool_names(snapshot)[:40]:
            add(
                f"tool:{self._anchor_key(tool_name)}",
                kind="tool",
                axis="resource",
                label=f"Tool {tool_name}",
                summary=f"The fixed target may route eligible requests to {tool_name}.",
                coverage=["tool_routing"],
            )

        if not any("instruction_following" in anchor["coverage"] for anchor in anchors):
            add(
                "xpert:execution_contract",
                kind="agent_contract",
                axis="contract",
                label="Xpert execution contract",
                summary="The fixed Xpert must directly fulfill the current user task through its published workflow.",
                coverage=["instruction_following"],
            )
        return anchors[:40]

    def conversation_seeds(
        self, selections: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        seeds: list[dict[str, Any]] = []
        for selection in selections[:20]:
            conversation = self.context_store.get_conversation(
                str(selection.get("xpert_id") or ""),
                str(selection.get("conversation_id") or ""),
            )
            selected_ids = {
                str(item) for item in list(selection.get("message_ids") or []) if str(item)
            }
            history: list[dict[str, str]] = []
            for message in conversation.messages:
                if message.role == "user" and message.message_id in selected_ids:
                    seeds.append(
                        {
                            "message_id": message.message_id,
                            "message": str(message.content)[:8_000],
                            "messages": copy.deepcopy(history[-6:]),
                        }
                    )
                history.append(
                    {
                        "role": message.role,
                        "content": str(message.content)[:4_000],
                    }
                )
        return seeds[:30]

    def generation_prompt(
        self,
        *,
        snapshot: dict[str, Any],
        case_count: int,
        locales: list[str],
        requested_coverage: list[str],
        conversation_seeds: list[dict[str, Any]],
        seed: int,
    ) -> tuple[str, str, dict[str, Any]]:
        coverage = self.detect_coverage(snapshot)
        selected = requested_coverage or list(coverage["recommended"])
        selected = [item for item in selected if item in coverage["available"]]
        context = self._safe_generation_context(snapshot)
        target_anchors = list(context.get("target_anchors") or [])
        case_blueprints = self.case_blueprints(
            case_count=case_count,
            locales=locales,
            selected_coverage=selected,
            target_anchors=target_anchors,
            seed=seed,
            tool_names=list(context.get("allowed_tool_names") or []),
            document_names=list(context.get("knowledge_document_names") or []),
            prompt_aliases=list(context.get("prompt_command_aliases") or []),
            structured_output_schemas=self._structured_output_schemas(snapshot),
        )
        contract = {
            "dataset": {
                "name": "short descriptive name",
                "description": "what this generated set validates",
                "cases": [
                    {
                        "name": "short name",
                        "targeting": {
                            "blueprint_id": "copy the matching server blueprint ID",
                            "rationale": "which exact target behavior this case verifies",
                            "challenge": "the concrete failure mode or pressure applied",
                            "discriminator": "observable behavior that a generic assistant would likely miss",
                        },
                        "message": "current user message",
                        "messages": [{"role": "user|assistant", "content": "bounded history"}],
                        "expected": {
                            "exact_answer": "optional deterministic answer",
                            "contains": ["optional required fragments"],
                        },
                        "weights": {"contains": 1.0},
                    }
                ],
                "assumptions": ["short public assumption"],
            }
        }
        system = (
            "You design deterministic, repeatable benchmark cases for one fixed AI "
            "target. Every case must test an explicit prompt, contract, tool, knowledge, "
            "or command anchor supplied by the target snapshot. Return JSON only. Never "
            "include hidden reasoning, credentials, "
            "file paths, attachment content, memories, or private tool output. Do not "
            "derive expected answers from assistant messages. Gold expectations must "
            "be independently stated and machine-checkable. Generic trivia, arithmetic, "
            "translation, or arbitrary formatting are invalid unless an explicit target "
            "anchor requires them. A benchmark case is invalid if it could be reused for "
            "an unrelated general-purpose assistant by only changing its title. The server "
            "has already designed each case's capability, evidence, pressure, locale, and "
            "difficulty blueprint. Return only blueprint_id plus rationale, challenge, and "
            "discriminator in targeting; the server injects all other targeting and fixed "
            "Gold metadata. Do not repeat full JSON Schemas or resource identifiers. "
            "Concentrate on a realistic "
            "professional scenario and deterministic Gold that make the blueprint observable."
        )
        user = (
            f"Create exactly {case_count} cases. Seed={seed}. Locales={locales}. "
            f"Coverage={selected}. Balance both locales when both are selected. Each "
            "case whose blueprint has no fixed resource or schema expectation must define "
            "at least one deterministic exact_answer or contains expectation. Never invent citation "
            "IDs. Conversation samples are input inspiration only, never Gold. Produce cases "
            "in the exact order of case_blueprints and copy each blueprint_id. The server "
            "will enforce the blueprint's locale, coverage, target_refs, capability_matrix, "
            "focus_terms, pressure_types, difficulty, and exact resource requirements. "
            "When a blueprint contains required_prompt_alias, the message must begin exactly "
            "with /<required_prompt_alias>. The server inserts required/forbidden tools, "
            "knowledge documents, and structured output schemas from the blueprint. The professional "
            "case input must visibly exercise either a required focus term or multiple "
            "domain markers from its cited anchors. The rationale must "
            "explain the exact target-specific "
            "behavior and the challenge must name the likely failure mode; write both in "
            "the same language as the case. discriminator must state a concrete observable "
            "difference from an unconfigured general-purpose model. Meet every item in each "
            "blueprint's observable_requirements. Edge cases must contain the specified "
            "concrete pressure rather than merely naming it. Adversarial cases must combine "
            "all specified pressures and include enough contradictory, incomplete, boundary, "
            "or decoy evidence for the deterministic Gold to discriminate the configured "
            "target. Do not make adversarial cases into direct fully specified questions.\n\n"
            f"Server-authored case blueprints:\n{json.dumps(case_blueprints, ensure_ascii=False)}\n\n"
            f"Target context:\n{json.dumps(context, ensure_ascii=False)}\n\n"
            f"Explicit user seeds:\n{json.dumps(conversation_seeds, ensure_ascii=False)}\n\n"
            f"JSON contract:\n{json.dumps(contract, ensure_ascii=False)}"
        )
        return system, user, {
            "selected": selected,
            **coverage,
            "target_anchors": target_anchors,
            "target_anchor_hash": self._checksum(target_anchors),
            "case_blueprints": case_blueprints,
            "case_blueprint_hash": self._checksum(case_blueprints),
            "knowledge_document_names": list(
                context.get("knowledge_document_names") or []
            ),
            "prompt_command_aliases": list(
                context.get("prompt_command_aliases") or []
            ),
        }

    def case_blueprints(
        self,
        *,
        case_count: int,
        locales: list[str],
        selected_coverage: list[str],
        target_anchors: list[dict[str, Any]],
        seed: int,
        tool_names: list[str] | None = None,
        document_names: list[str] | None = None,
        prompt_aliases: list[str] | None = None,
        structured_output_schemas: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the evaluation design before asking a model to write case content."""

        selected = [item for item in selected_coverage if item in SUPPORTED_COVERAGE]
        if not selected:
            raise BenchmarkGenerationError("At least one supported capability is required.")
        locale_values = [item for item in locales if item in {"zh-CN", "en-US"}]
        if not locale_values:
            locale_values = ["zh-CN", "en-US"]
        anchors = [
            dict(item)
            for item in target_anchors
            if isinstance(item, dict) and str(item.get("id") or "")
        ]
        if not anchors:
            raise BenchmarkGenerationError("Target snapshot exposes no benchmark anchors.")
        fixed_tools = [str(item) for item in list(tool_names or []) if str(item)]
        fixed_documents = [
            str(item) for item in list(document_names or []) if str(item)
        ]
        fixed_aliases = [
            str(item).lstrip("/").casefold()
            for item in list(prompt_aliases or [])
            if str(item).strip()
        ]

        rotation = int(seed) % len(selected)
        wheel = selected[rotation:] + selected[:rotation]
        combined_count = (
            min(case_count - 1, math.ceil(case_count * 0.60))
            if len(wheel) >= 2
            else 0
        )
        basic_count = max(1, math.floor(case_count * 0.20))
        remaining = case_count - basic_count
        edge_count = max(math.ceil(case_count * 0.25), remaining // 2)
        edge_count = min(edge_count, remaining - math.ceil(case_count * 0.25))
        difficulties = [
            *(["basic"] * basic_count),
            *(["edge"] * edge_count),
            *(["adversarial"] * (case_count - basic_count - edge_count)),
        ]
        difficulty_rotation = int(seed) % len(difficulties)
        difficulties = difficulties[difficulty_rotation:] + difficulties[:difficulty_rotation]

        professional_anchors = [
            item
            for item in anchors
            if item.get("axis") in {"domain", "resource"}
            and list(item.get("focus_terms") or [])
        ]

        def anchor_for(capability: str) -> dict[str, Any]:
            candidates = [
                item
                for item in anchors
                if capability in set(item.get("coverage") or [])
            ]
            if not candidates:
                raise BenchmarkGenerationError(
                    f"Target snapshot has no anchor for capability: {capability}."
                )
            axis_priority = {
                "instruction_following": {"domain": 0, "contract": 1, "resource": 2},
                "structured_output": {"contract": 0, "domain": 1, "resource": 2},
                "multi_turn": {"contract": 0, "domain": 1, "resource": 2},
                "tool_routing": {"resource": 0, "domain": 1, "contract": 2},
                "knowledge_citation": {"resource": 0, "domain": 1, "contract": 2},
                "prompt_command": {"resource": 0, "domain": 1, "contract": 2},
            }.get(capability, {})
            return sorted(
                candidates,
                key=lambda item: (
                    axis_priority.get(str(item.get("axis") or ""), 3),
                    0 if list(item.get("focus_terms") or []) else 1,
                    str(item.get("id") or ""),
                ),
            )[0]

        output: list[dict[str, Any]] = []
        for index in range(case_count):
            primary = wheel[index % len(wheel)]
            difficulty = difficulties[index]
            if index < combined_count:
                matrix_size = 3 if len(wheel) >= 3 and index % 2 else 2
                matrix = [
                    wheel[(index + offset) % len(wheel)]
                    for offset in range(matrix_size)
                ]
                matrix = list(dict.fromkeys(matrix))
            else:
                matrix = [primary]
            if difficulty == "adversarial" and len(matrix) == 1 and len(wheel) > 1:
                matrix.append(wheel[(index + 1) % len(wheel)])
                matrix = list(dict.fromkeys(matrix))

            selected_anchors: list[dict[str, Any]] = []
            for capability in matrix:
                candidate = anchor_for(capability)
                if candidate not in selected_anchors:
                    selected_anchors.append(candidate)
            if professional_anchors and not any(
                item.get("axis") in {"domain", "resource"}
                for item in selected_anchors
            ):
                selected_anchors.insert(0, professional_anchors[index % len(professional_anchors)])
            selected_anchors = selected_anchors[:5]

            focus_terms: list[str] = []
            for anchor in selected_anchors:
                for term in list(anchor.get("focus_terms") or []):
                    if str(term).strip() and str(term) not in focus_terms:
                        focus_terms.append(str(term))
                        break
                if len(focus_terms) >= 3:
                    break
            pressure_pool: list[str] = []
            for capability in matrix:
                pressure_pool.extend(PRESSURE_TYPES_BY_COVERAGE.get(capability, ()))
            pressure_pool = list(dict.fromkeys(pressure_pool))
            minimum_pressure = 2 if difficulty == "adversarial" else 1 if difficulty == "edge" else 0
            if pressure_pool:
                pressure_offset = (int(seed) + index) % len(pressure_pool)
                pressure_pool = pressure_pool[pressure_offset:] + pressure_pool[:pressure_offset]
            pressure_types = pressure_pool[:minimum_pressure]
            required_tool_name = (
                fixed_tools[index % len(fixed_tools)]
                if "tool_routing" in matrix and fixed_tools
                else None
            )
            forbidden_tool_names: list[str] = []
            if required_tool_name and "tool_decoy" in pressure_types:
                anchored_tools = [
                    str(item.get("id") or "").removeprefix("tool:")
                    for item in selected_anchors
                    if str(item.get("id") or "").startswith("tool:")
                ]
                decoys = [
                    item
                    for item in [*anchored_tools, *fixed_tools]
                    if item and item != required_tool_name
                ]
                if decoys:
                    forbidden_tool_names = [decoys[0]]
                    decoy_anchor_id = f"tool:{decoys[0]}"
                    decoy_anchor = next(
                        (
                            item
                            for item in anchors
                            if str(item.get("id") or "") == decoy_anchor_id
                        ),
                        None,
                    )
                    if decoy_anchor is not None and decoy_anchor not in selected_anchors:
                        selected_anchors.append(decoy_anchor)
                        selected_anchors = selected_anchors[:5]
            required_document_name = (
                fixed_documents[index % len(fixed_documents)]
                if "knowledge_citation" in matrix and fixed_documents
                else None
            )
            required_prompt_alias = (
                fixed_aliases[index % len(fixed_aliases)]
                if "prompt_command" in matrix and fixed_aliases
                else None
            )
            required_json_schema: dict[str, Any] | None = None
            if "structured_output" in matrix:
                schemas = structured_output_schemas or {}
                for anchor in selected_anchors:
                    candidate = schemas.get(str(anchor.get("id") or ""))
                    if isinstance(candidate, dict):
                        required_json_schema = copy.deepcopy(candidate)
                        break
                if required_json_schema is None and schemas:
                    required_json_schema = copy.deepcopy(next(iter(schemas.values())))

            observable_requirements: list[str] = [
                "State deterministic Gold that is not copied into the input.",
            ]
            if focus_terms:
                observable_requirements.insert(
                    0, "Use every required_focus_term verbatim in message or history."
                )
            if "structured_output" in matrix:
                observable_requirements.append(
                    "The server will inject the fixed structured response schema."
                )
            if "multi_turn" in matrix:
                observable_requirements.append(
                    "Provide at least two history messages, including an earlier assistant response."
                )
            if "tool_routing" in matrix:
                observable_requirements.append(
                    "The server will inject required_tool_name into deterministic Gold."
                )
                if forbidden_tool_names:
                    observable_requirements.append(
                        "Mention the forbidden decoy tool in the input while making clear it must not be selected."
                    )
            if "knowledge_citation" in matrix:
                observable_requirements.append(
                    "The server will inject required_document_name; do not invent citation or chunk IDs."
                )
            if "prompt_command" in matrix:
                observable_requirements.append(
                    "Begin message exactly with /<required_prompt_alias> followed by one space and the professional task."
                )
            if difficulty == "adversarial":
                observable_requirements.append(
                    "Expose at least two independent challenge signals in the actual input and Gold."
                )
            elif difficulty == "edge":
                observable_requirements.append(
                    "Expose at least one non-routine boundary or conflict in the actual input and Gold."
                )

            output.append(
                {
                    "blueprint_id": f"case-{index + 1:02d}",
                    "locale": locale_values[index % len(locale_values)],
                    "primary_coverage": primary,
                    "capability_matrix": matrix,
                    "difficulty": difficulty,
                    "target_refs": [str(item["id"]) for item in selected_anchors],
                    "required_focus_terms": focus_terms,
                    "pressure_types": pressure_types,
                    "required_tool_name": required_tool_name,
                    "forbidden_tool_names": forbidden_tool_names,
                    "required_document_name": required_document_name,
                    "required_prompt_alias": required_prompt_alias,
                    "required_json_schema": required_json_schema,
                    "anchor_evidence": [
                        {
                            "id": str(item["id"]),
                            "label": str(item.get("label") or "")[:160],
                            "summary": str(item.get("summary") or "")[:280],
                        }
                        for item in selected_anchors
                    ],
                    "observable_requirements": observable_requirements,
                }
            )
        return output

    def parse_generated_cases(
        self,
        raw: str,
        *,
        expected_count: int,
        allowed_coverage: list[str],
        allowed_tool_names: list[str],
        allowed_target_anchors: list[dict[str, Any]] | None = None,
        case_blueprints: list[dict[str, Any]] | None = None,
        allowed_document_names: list[str] | None = None,
        allowed_prompt_aliases: list[str] | None = None,
    ) -> dict[str, Any]:
        payload = self._extract_json(raw)
        dataset = payload.get("dataset") if isinstance(payload, dict) else None
        if not isinstance(dataset, dict):
            raise BenchmarkGenerationError("Generator output is missing dataset.")
        raw_cases = dataset.get("cases")
        if not isinstance(raw_cases, list) or len(raw_cases) != expected_count:
            raise BenchmarkGenerationError(
                f"Generator must return exactly {expected_count} cases."
            )
        allowed_tools = set(allowed_tool_names)
        anchors = {
            str(item.get("id") or ""): item
            for item in list(allowed_target_anchors or [])
            if isinstance(item, dict) and str(item.get("id") or "")
        }
        if not anchors:
            raise BenchmarkGenerationError("Target snapshot exposes no benchmark anchors.")
        blueprints = [dict(item) for item in list(case_blueprints or []) if isinstance(item, dict)]
        if blueprints and len(blueprints) != expected_count:
            raise BenchmarkGenerationError(
                f"Server blueprint must contain exactly {expected_count} cases."
            )
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        seen_coverage: set[str] = set()
        referenced_anchor_ids: set[str] = set()
        for index, raw_case in enumerate(raw_cases, start=1):
            if not isinstance(raw_case, dict):
                raise BenchmarkGenerationError(f"Case {index} is not an object.")
            case = copy.deepcopy(raw_case)
            blueprint = blueprints[index - 1] if blueprints else None
            removed_server_owned_contains: list[str] = []
            if blueprint:
                case["case_id"] = str(blueprint.get("blueprint_id") or f"case-{index:02d}")
                case["locale"] = str(blueprint.get("locale") or "zh-CN")
                case["coverage"] = str(
                    blueprint.get("primary_coverage") or "instruction_following"
                )
                generated_targeting = dict(case.get("targeting") or {})
                generated_targeting.update(
                    {
                        "blueprint_id": str(blueprint.get("blueprint_id") or ""),
                        "difficulty": str(blueprint.get("difficulty") or "basic"),
                        "target_refs": list(blueprint.get("target_refs") or []),
                        "capability_matrix": list(
                            blueprint.get("capability_matrix") or []
                        ),
                        "focus_terms": list(
                            blueprint.get("required_focus_terms") or []
                        ),
                        "pressure_types": list(
                            blueprint.get("pressure_types") or []
                        ),
                    }
                )
                case["targeting"] = generated_targeting
                expectation = dict(case.get("expected") or {})
                required_tool_name = str(blueprint.get("required_tool_name") or "")
                if required_tool_name:
                    expectation["required_tools"] = list(
                        dict.fromkeys(
                            [
                                *list(expectation.get("required_tools") or []),
                                required_tool_name,
                            ]
                        )
                    )
                forbidden_tool_names = [
                    str(item)
                    for item in list(blueprint.get("forbidden_tool_names") or [])
                    if str(item)
                ]
                if forbidden_tool_names:
                    expectation["forbidden_tools"] = list(
                        dict.fromkeys(
                            [
                                *list(expectation.get("forbidden_tools") or []),
                                *forbidden_tool_names,
                            ]
                        )
                    )
                required_document_name = str(
                    blueprint.get("required_document_name") or ""
                )
                if required_document_name:
                    expectation["document_names"] = list(
                        dict.fromkeys(
                            [
                                *list(expectation.get("document_names") or []),
                                required_document_name,
                            ]
                        )
                    )
                required_json_schema = blueprint.get("required_json_schema")
                if isinstance(required_json_schema, dict):
                    expectation["json_schema"] = copy.deepcopy(required_json_schema)
                removed_server_owned_contains = self._strip_server_owned_contains(
                    expectation,
                    blueprint,
                )
                case["expected"] = expectation
            coverage = str(case.pop("coverage", "instruction_following"))
            locale = str(case.pop("locale", ""))
            if locale not in {"zh-CN", "en-US"}:
                raise BenchmarkGenerationError(f"Case {index} has an invalid locale.")
            targeting = case.get("targeting")
            if not isinstance(targeting, dict):
                raise BenchmarkGenerationError(
                    f"Case {index} is missing target-specific evidence."
                )
            normalization_notes: list[str] = []
            if removed_server_owned_contains:
                normalization_notes.append(
                    "moved server-owned resource identifiers out of expected.contains: "
                    + ", ".join(removed_server_owned_contains)
                )
            target_refs = list(
                dict.fromkeys(
                    str(item) for item in list(targeting.get("target_refs") or []) if str(item)
                )
            )
            unknown_refs = sorted(set(target_refs) - set(anchors))
            if unknown_refs:
                raise BenchmarkGenerationError(
                    f"Case {index} references unknown target anchors: {', '.join(unknown_refs)}"
                )
            if not target_refs:
                raise BenchmarkGenerationError(
                    f"Case {index} must reference at least one target anchor."
                )
            target_has_professional_anchors = any(
                anchor.get("axis") in {"domain", "resource"}
                for anchor in anchors.values()
            )
            capability_matrix = list(
                dict.fromkeys(
                    str(item)
                    for item in list(targeting.get("capability_matrix") or [])
                    if str(item)
                )
            )
            if not 1 <= len(capability_matrix) <= 3:
                raise BenchmarkGenerationError(
                    f"Case {index} must define one to three capability_matrix values."
                )
            unknown_matrix = sorted(set(capability_matrix) - set(allowed_coverage))
            if unknown_matrix:
                capability_matrix = [
                    item for item in capability_matrix if item in allowed_coverage
                ]
                normalization_notes.append(
                    "removed unavailable matrix capabilities: "
                    + ", ".join(unknown_matrix)
                )
            if coverage not in allowed_coverage:
                if not capability_matrix:
                    raise BenchmarkGenerationError(
                        f"Case {index} uses unavailable coverage: {coverage}."
                    )
                previous_coverage = coverage
                coverage = capability_matrix[0]
                normalization_notes.append(
                    f"replaced unavailable primary coverage {previous_coverage} with {coverage}"
                )
            if coverage not in capability_matrix:
                capability_matrix = list(
                    dict.fromkeys([coverage, *capability_matrix])
                )[:3]
                normalization_notes.append("restored primary coverage in capability_matrix")
            for capability in capability_matrix:
                if not any(
                    capability in set(anchors[ref].get("coverage") or [])
                    for ref in target_refs
                ):
                    supporting_refs = sorted(
                        ref
                        for ref, anchor in anchors.items()
                        if capability in set(anchor.get("coverage") or [])
                    )
                    if not supporting_refs:
                        raise BenchmarkGenerationError(
                            f"Case {index} has no target anchor for matrix capability "
                            f"{capability}."
                        )
                    target_refs.append(supporting_refs[0])
                    target_refs = list(dict.fromkeys(target_refs))
                    normalization_notes.append(
                        f"added supporting target anchor for {capability}"
                    )

            professional_refs = [
                ref
                for ref in target_refs
                if anchors[ref].get("axis") in {"domain", "resource"}
            ]
            if target_has_professional_anchors and not professional_refs:
                candidates = sorted(
                    ref
                    for ref, anchor in anchors.items()
                    if anchor.get("axis") in {"domain", "resource"}
                )
                if not candidates or len(target_refs) >= 5:
                    raise BenchmarkGenerationError(
                        f"Case {index} must cite a professional domain or resource anchor."
                    )
                target_refs.append(candidates[0])
                professional_refs = [candidates[0]]
                normalization_notes.append("added professional target anchor")

            allowed_focus_terms = {
                self._normalize_text(term): str(term)
                for ref in target_refs
                for term in list(anchors[ref].get("focus_terms") or [])
                if str(term).strip()
            }
            target_anchor_summaries = [
                self._normalize_text(anchors[ref].get("summary") or "")
                for ref in target_refs
            ]

            def focus_is_grounded(term: str, *, summaries: list[str]) -> bool:
                normalized = self._normalize_text(term)
                return bool(normalized) and (
                    normalized in allowed_focus_terms
                    or any(normalized in summary for summary in summaries)
                )

            focus_terms = list(
                dict.fromkeys(
                    str(item).strip()
                    for item in list(targeting.get("focus_terms") or [])
                    if str(item).strip()
                )
            )
            if allowed_focus_terms and not focus_terms:
                raise BenchmarkGenerationError(
                    f"Case {index} must use professional focus_terms from its target anchors."
                )
            unknown_focus = sorted(
                term
                for term in focus_terms
                if not focus_is_grounded(term, summaries=target_anchor_summaries)
            )
            unresolved_focus: list[str] = []
            for term in unknown_focus:
                normalized_term = self._normalize_text(term)
                supporting_refs = sorted(
                    ref
                    for ref, anchor in anchors.items()
                    if normalized_term
                    and normalized_term
                    in self._normalize_text(anchor.get("summary") or "")
                )
                if supporting_refs and len(target_refs) < 5:
                    target_refs.append(supporting_refs[0])
                    target_refs = list(dict.fromkeys(target_refs))
                    normalization_notes.append(
                        f"added target anchor for declared focus term: {term}"
                    )
                else:
                    unresolved_focus.append(term)
            if unresolved_focus:
                raise BenchmarkGenerationError(
                    f"Case {index} invents focus terms outside its target anchors: "
                    + ", ".join(unresolved_focus)
                )
            if unknown_focus and not unresolved_focus:
                professional_refs = [
                    ref
                    for ref in target_refs
                    if anchors[ref].get("axis") in {"domain", "resource"}
                ]
                allowed_focus_terms = {
                    self._normalize_text(term): str(term)
                    for ref in target_refs
                    for term in list(anchors[ref].get("focus_terms") or [])
                    if str(term).strip()
                }
                target_anchor_summaries = [
                    self._normalize_text(anchors[ref].get("summary") or "")
                    for ref in target_refs
                ]
            professional_focus_terms = {
                self._normalize_text(term)
                for ref in professional_refs
                for term in list(anchors[ref].get("focus_terms") or [])
                if str(term).strip()
            }
            professional_summaries = [
                self._normalize_text(anchors[ref].get("summary") or "")
                for ref in professional_refs
            ]
            if professional_focus_terms and not any(
                self._normalize_text(term) in professional_focus_terms
                or any(
                    self._normalize_text(term) in summary
                    for summary in professional_summaries
                )
                for term in focus_terms
            ):
                raise BenchmarkGenerationError(
                    f"Case {index} must use a focus term from its professional anchor."
                )
            professional_evidence = self._professional_evidence(
                case=case,
                anchors=[anchors[ref] for ref in professional_refs],
            )
            if professional_refs and not professional_evidence["sufficient"]:
                raise BenchmarkGenerationError(
                    f"Case {index} lacks observable professional evidence from its "
                    "cited target anchors."
                )

            difficulty = str(targeting.get("difficulty") or "")
            pressure_types = list(
                dict.fromkeys(
                    str(item)
                    for item in list(targeting.get("pressure_types") or [])
                    if str(item)
                )
            )
            minimum_pressure = 2 if difficulty == "adversarial" else 1 if difficulty == "edge" else 0
            if len(pressure_types) < minimum_pressure:
                raise BenchmarkGenerationError(
                    f"Case {index} {difficulty} difficulty needs at least "
                    f"{minimum_pressure} pressure_types."
                )
            if len(str(targeting.get("challenge") or "").strip()) < 8:
                raise BenchmarkGenerationError(
                    f"Case {index} must describe its concrete challenge."
                )
            if len(str(targeting.get("discriminator") or "").strip()) < 20:
                raise BenchmarkGenerationError(
                    f"Case {index} must explain how it discriminates this target "
                    "from a generic model."
                )
            targeting["target_refs"] = target_refs
            targeting["capability_matrix"] = capability_matrix
            targeting["focus_terms"] = focus_terms
            targeting["pressure_types"] = pressure_types
            targeting["professional_evidence"] = professional_evidence
            targeting["normalization_notes"] = normalization_notes[:8]
            case["targeting"] = targeting
            case.setdefault("tags", [])
            case["tags"] = list(
                dict.fromkeys(
                    [*case["tags"], "generated", *capability_matrix, locale]
                )
            )[:20]
            expectation = dict(case.get("expected") or {})
            if blueprint:
                self._validate_blueprint_observability(
                    index=index,
                    case=case,
                    capability_matrix=capability_matrix,
                    difficulty=difficulty,
                    expectation=expectation,
                    allowed_document_names=list(allowed_document_names or []),
                    allowed_prompt_aliases=list(allowed_prompt_aliases or []),
                    blueprint=blueprint,
                )
            referenced_tools = {
                str(item)
                for field in ("required_tools", "forbidden_tools", "tool_order")
                for item in list(expectation.get(field) or [])
                if str(item)
            }
            unknown_tools = sorted(referenced_tools - allowed_tools)
            if unknown_tools:
                raise BenchmarkGenerationError(
                    f"Case {index} references unavailable tools: {', '.join(unknown_tools)}"
                )
            if not self._has_deterministic_expectation(expectation):
                raise BenchmarkGenerationError(
                    f"Case {index} has no deterministic expectation."
                )
            try:
                validated = EvaluationCaseInput.model_validate(case).model_dump(mode="json")
            except ValidationError as exc:
                raise BenchmarkGenerationError(
                    f"Case {index} failed schema validation: {str(exc)[:500]}"
                ) from exc
            signature = self._normalize_text(validated["message"])
            if signature in seen:
                raise BenchmarkGenerationError(f"Case {index} duplicates another message.")
            seen.add(signature)
            if self._gold_leaks_into_input(validated):
                raise BenchmarkGenerationError(
                    f"Case {index} copies its deterministic Gold into the input."
                )
            normalized.append(validated)
            seen_coverage.add(coverage)
            referenced_anchor_ids.update(target_refs)
        if expected_count >= 6:
            missing_coverage = sorted(set(allowed_coverage) - seen_coverage)
            if missing_coverage:
                raise BenchmarkGenerationError(
                    "Generated cases do not cover all selected capabilities: "
                    + ", ".join(missing_coverage)
                )
            difficulty_counts = self.targeting_summary(normalized)["difficulty_counts"]
            minimum_challenging = max(1, math.ceil(expected_count * 0.25))
            maximum_basic = max(1, math.ceil(expected_count * 0.30))
            if int(difficulty_counts.get("basic") or 0) < 1:
                raise BenchmarkGenerationError("Generated cases need at least one basic case.")
            if int(difficulty_counts.get("basic") or 0) > maximum_basic:
                raise BenchmarkGenerationError(
                    f"At most {maximum_basic} of {expected_count} cases may be basic."
                )
            for difficulty in ("edge", "adversarial"):
                if int(difficulty_counts.get(difficulty) or 0) < minimum_challenging:
                    raise BenchmarkGenerationError(
                        f"At least {minimum_challenging} cases must be {difficulty}."
                    )
            required_anchor_diversity = min(2, len(anchors))
            if len(referenced_anchor_ids) < required_anchor_diversity:
                raise BenchmarkGenerationError(
                    "Generated cases do not exercise enough distinct target anchors."
                )
            if len(allowed_coverage) >= 2:
                summary = self.targeting_summary(normalized)
                minimum_combined = math.ceil(expected_count * 0.60)
                if int(summary.get("combined_case_count") or 0) < minimum_combined:
                    raise BenchmarkGenerationError(
                        f"At least {minimum_combined} of {expected_count} cases must "
                        "combine multiple capabilities."
                    )
                combined_capabilities = set(summary.get("combined_capabilities") or [])
                missing_combined = sorted(set(allowed_coverage) - combined_capabilities)
                if missing_combined:
                    raise BenchmarkGenerationError(
                        "Selected capabilities missing from combined cases: "
                        + ", ".join(missing_combined)
                    )
                possible_signatures = sum(
                    math.comb(len(allowed_coverage), size)
                    for size in range(2, min(3, len(allowed_coverage)) + 1)
                )
                required_signatures = min(3, possible_signatures)
                if len(summary.get("capability_matrix_counts") or {}) < required_signatures:
                    raise BenchmarkGenerationError(
                        f"Generated cases need at least {required_signatures} distinct "
                        "multi-capability combinations."
                    )
        targeting_summary = self.targeting_summary(normalized)
        return {
            "name": str(dataset.get("name") or "Generated target benchmark")[:160],
            "description": str(dataset.get("description") or "")[:2_000],
            "cases": normalized,
            "assumptions": [
                str(item)[:500]
                for item in list(dataset.get("assumptions") or [])[:20]
                if str(item).strip()
            ],
            "targeting": targeting_summary,
        }

    def repair_prompt(
        self,
        raw: str,
        error: str,
        *,
        expected_count: int,
        allowed_coverage: list[str] | None = None,
        target_anchors: list[dict[str, Any]] | None = None,
        case_blueprints: list[dict[str, Any]] | None = None,
        allowed_document_names: list[str] | None = None,
        allowed_prompt_aliases: list[str] | None = None,
    ) -> tuple[str, str]:
        repair_context = {
            "allowed_coverage": list(allowed_coverage or []),
            "target_anchors": list(target_anchors or []),
            "case_blueprints": list(case_blueprints or []),
            "allowed_document_names": list(allowed_document_names or []),
            "allowed_prompt_aliases": list(allowed_prompt_aliases or []),
            "difficulty_policy": {
                "basic_max_ratio": 0.30,
                "edge_min_ratio": 0.25,
                "adversarial_min_ratio": 0.25,
            },
            "matrix_policy": {
                "combined_case_min_ratio": 0.60,
                "distinct_combinations_min": 3,
                "professional_focus_required_when_available": True,
            },
            "case_contract": {
                "exact_case_count": expected_count,
                "primary_coverage_must_be_in_capability_matrix": True,
                "each_matrix_capability_needs_a_supporting_target_ref": True,
                "focus_terms_must_be_verbatim_in_cited_anchor_summary": True,
                "professional_evidence_must_be_observable_in_input": True,
                "edge_min_pressure_types": 1,
                "adversarial_min_pressure_types": 2,
                "discriminator_required": True,
                "deterministic_expectation_required": True,
            },
        }
        return (
            "Repair a benchmark JSON document. Return JSON only and do not add commentary.",
            (
                f"The document must contain exactly {expected_count} cases and obey the "
                "original contract. Return one object shaped as "
                "{\"dataset\": {\"name\": str, \"description\": str, "
                "\"cases\": [...], \"assumptions\": [...]}}. Do not omit, duplicate, "
                "or add cases. For every case, keep primary coverage inside "
                "capability_matrix; cite enough target_refs so each matrix capability is "
                "supported by a cited anchor. Every declared focus_term must be grounded "
                "in a cited anchor. The case input must visibly exercise either an exact "
                "focus term or at least two professional markers from those anchors. "
                "Follow the server case_blueprints in exact order; "
                "their metadata is authoritative. Preserve deterministic expectations, "
                "difficulty distribution, pressure counts, discriminator, and exact "
                "required_tool_name, required_document_name, and required_prompt_alias. "
                "A prompt-command case message must start with the slash alias. Fix only the listed "
                "validation problems. Never add "
                "credentials, paths, hidden reasoning, or private content.\n\n"
                f"Allowed targeting contract:\n{json.dumps(repair_context, ensure_ascii=False)}\n\n"
                f"Validation error: {error[:1_000]}\n\n"
                f"Invalid JSON/output:\n{raw[:40_000]}"
            ),
        )

    @staticmethod
    def _validate_blueprint_observability(
        *,
        index: int,
        case: dict[str, Any],
        capability_matrix: list[str],
        difficulty: str,
        expectation: dict[str, Any],
        allowed_document_names: list[str],
        allowed_prompt_aliases: list[str],
        blueprint: dict[str, Any],
    ) -> None:
        messages = [
            dict(item) for item in list(case.get("messages") or []) if isinstance(item, dict)
        ]
        if "structured_output" in capability_matrix and not isinstance(
            expectation.get("json_schema"), dict
        ):
            raise BenchmarkGenerationError(
                f"Case {index} must provide expected.json_schema for structured_output."
            )
        if "multi_turn" in capability_matrix and (
            len(messages) < 2
            or not any(str(item.get("role") or "") == "assistant" for item in messages)
        ):
            raise BenchmarkGenerationError(
                f"Case {index} must include two history messages and an assistant turn for multi_turn."
            )
        if "tool_routing" in capability_matrix and not any(
            bool(expectation.get(field))
            for field in ("required_tools", "forbidden_tools", "tool_order")
        ):
            raise BenchmarkGenerationError(
                f"Case {index} must define deterministic tool expectations for tool_routing."
            )
        required_tool_name = str(blueprint.get("required_tool_name") or "")
        if required_tool_name and required_tool_name not in {
            str(item)
            for field in ("required_tools", "tool_order")
            for item in list(expectation.get(field) or [])
        }:
            raise BenchmarkGenerationError(
                f"Case {index} must require blueprint tool {required_tool_name}."
            )
        required_forbidden_tools = {
            str(item)
            for item in list(blueprint.get("forbidden_tool_names") or [])
            if str(item)
        }
        actual_forbidden_tools = {
            str(item)
            for item in list(expectation.get("forbidden_tools") or [])
            if str(item)
        }
        missing_forbidden_tools = sorted(
            required_forbidden_tools - actual_forbidden_tools
        )
        if missing_forbidden_tools:
            raise BenchmarkGenerationError(
                f"Case {index} must forbid blueprint decoy tools: "
                + ", ".join(missing_forbidden_tools)
            )
        if required_forbidden_tools:
            visible_input = BenchmarkGenerationService._normalize_text(
                " ".join(
                    [
                        str(case.get("message") or ""),
                        *[
                            str(item.get("content") or "")
                            for item in messages
                            if isinstance(item, dict)
                        ],
                    ]
                )
            )
            hidden_decoys = sorted(
                item
                for item in required_forbidden_tools
                if BenchmarkGenerationService._normalize_text(item) not in visible_input
            )
            if hidden_decoys:
                raise BenchmarkGenerationError(
                    f"Case {index} must expose blueprint decoy tools in its input: "
                    + ", ".join(hidden_decoys)
                )
        if "knowledge_citation" in capability_matrix and not any(
            bool(expectation.get(field))
            for field in ("citation_ids", "chunk_ids", "document_names")
        ):
            raise BenchmarkGenerationError(
                f"Case {index} must define fixed citation expectations for knowledge_citation."
            )
        if "knowledge_citation" in capability_matrix:
            expected_documents = {
                str(item)
                for item in list(expectation.get("document_names") or [])
                if str(item)
            }
            unknown_documents = sorted(
                expected_documents - set(allowed_document_names)
            )
            if not expected_documents or unknown_documents:
                detail = ", ".join(unknown_documents) if unknown_documents else "none provided"
                raise BenchmarkGenerationError(
                    f"Case {index} must use fixed knowledge document_names; invalid: {detail}."
                )
            if expectation.get("citation_ids") or expectation.get("chunk_ids"):
                raise BenchmarkGenerationError(
                    f"Case {index} may not invent citation_ids or chunk_ids during Xpert generation."
                )
            required_document_name = str(
                blueprint.get("required_document_name") or ""
            )
            if required_document_name and required_document_name not in expected_documents:
                raise BenchmarkGenerationError(
                    f"Case {index} must cite blueprint document {required_document_name}."
                )
        if "prompt_command" in capability_matrix:
            message = str(case.get("message") or "").lstrip().casefold()
            required_alias = str(blueprint.get("required_prompt_alias") or "").lstrip(
                "/"
            ).casefold()
            prefixes = (
                [f"/{required_alias}"]
                if required_alias
                else [
                    f"/{str(alias).lstrip('/').casefold()}"
                    for alias in allowed_prompt_aliases
                    if str(alias).strip()
                ]
            )
            if not prefixes or not any(
                message == prefix or message.startswith(prefix + " ")
                for prefix in prefixes
            ):
                raise BenchmarkGenerationError(
                    f"Case {index} must invoke one fixed Prompt Command alias."
                )

        observable_signals = 0
        if len(capability_matrix) > 1:
            observable_signals += 1
        if len(messages) >= 2:
            observable_signals += 1
        if isinstance(expectation.get("json_schema"), dict):
            observable_signals += 1
        if expectation.get("required_tools"):
            observable_signals += 1
        if expectation.get("forbidden_tools"):
            observable_signals += 1
        if len(list(expectation.get("tool_order") or [])) >= 2:
            observable_signals += 1
        if any(
            bool(expectation.get(field))
            for field in ("citation_ids", "chunk_ids", "document_names")
        ):
            observable_signals += 1
        if len(list(expectation.get("contains") or [])) >= 2:
            observable_signals += 1
        if "prompt_command" in capability_matrix:
            observable_signals += 1
        minimum = 2 if difficulty == "adversarial" else 1 if difficulty == "edge" else 0
        if observable_signals < minimum:
            raise BenchmarkGenerationError(
                f"Case {index} {difficulty} difficulty exposes only {observable_signals} "
                f"observable challenge signals; at least {minimum} are required."
            )

    def calibration_result(
        self,
        *,
        dataset: dict[str, Any],
        evaluation_run: dict[str, Any],
        target_reference: dict[str, Any],
        target_checksum: str,
    ) -> dict[str, Any]:
        if int(dataset.get("revision") or 0) != int(
            (evaluation_run.get("dataset") or {}).get("draft_revision")
            or dataset.get("revision")
        ):
            return {"status": "stale", "reason": "dataset_revision_changed"}
        if not self.target_is_fresh(target_reference, target_checksum):
            return {"status": "stale", "reason": "target_changed"}
        if evaluation_run.get("status") in {"failed", "cancelled"}:
            return {
                "status": "failed",
                "reason": str(evaluation_run.get("error") or evaluation_run.get("status"))[:500],
            }
        targets = list(evaluation_run.get("targets") or [])
        specialist_target = next(
            (
                item
                for item in targets
                if str(item.get("checksum") or "") == str(target_checksum)
                and not bool(item.get("benchmark_counterfactual"))
            ),
            targets[-1] if targets else {},
        )
        specialist_target_id = str(specialist_target.get("target_id") or "")
        generic_target = next(
            (item for item in targets if bool(item.get("benchmark_counterfactual"))),
            None,
        )
        generic_target_id = str((generic_target or {}).get("target_id") or "")
        all_items = list(evaluation_run.get("items") or [])
        items = [
            item
            for item in all_items
            if not specialist_target_id
            or str(item.get("target_id") or "") == specialist_target_id
        ]
        completed = [item for item in items if item.get("status") == "completed"]
        if not completed:
            return {"status": "failed", "reason": "no_cases_completed"}
        warnings: list[str] = []
        failed_count = len(items) - len(completed)
        if failed_count:
            warnings.append(f"{failed_count} calibration cases failed to execute.")
        scores = [float(item.get("score") or 0.0) for item in completed]
        easy_count = sum(score >= 0.95 for score in scores)
        hard_count = sum(score <= 0.20 for score in scores)
        report_targets = {
            str(item.get("target_id") or ""): item
            for item in list((evaluation_run.get("report") or {}).get("targets") or [])
        }
        specialist_score = float(
            (report_targets.get(specialist_target_id) or {}).get("score") or 0.0
        )
        generic_score = (
            float((report_targets.get(generic_target_id) or {}).get("score") or 0.0)
            if generic_target_id
            else None
        )
        targeting_advantage = (
            specialist_score - generic_score if generic_score is not None else None
        )
        if easy_count / len(scores) >= 0.80 and (
            targeting_advantage is None or targeting_advantage < 0.10
        ):
            warnings.append(
                "At least 80% of cases are easy and the fixed target does not "
                "outperform the generic counterfactual by 0.10."
            )
        if hard_count / len(scores) >= 0.80:
            warnings.append("At least 80% of cases are very hard for the fixed baseline.")
        duplicate_count = self._near_duplicate_count(list(dataset.get("cases") or []))
        if duplicate_count:
            warnings.append(f"Detected {duplicate_count} near-duplicate case pairs.")
        leak_count = sum(
            1 for case in list(dataset.get("cases") or []) if self._gold_leaks_into_input(case)
        )
        if leak_count:
            warnings.append(f"Detected possible Gold leakage in {leak_count} cases.")
        targeting = self.targeting_summary(list(dataset.get("cases") or []))
        if str(dataset.get("origin") or "manual") == "generated" and targeting[
            "missing_count"
        ]:
            warnings.append(
                f"{targeting['missing_count']} generated cases lack target-specific evidence."
            )
        if targeting_advantage is not None and targeting_advantage < 0.10:
            warnings.append(
                "Target-specific advantage over the generic counterfactual is below 0.10."
            )
        return {
            "status": "warning" if warnings else "calibrated",
            "evaluation_run_id": evaluation_run.get("run_id"),
            "target_checksum": target_checksum,
            "target_reference": copy.deepcopy(target_reference),
            "case_count": len(items),
            "completed_count": len(completed),
            "failed_count": failed_count,
            "baseline_score": round(specialist_score, 6),
            "generic_counterfactual_score": (
                round(generic_score, 6) if generic_score is not None else None
            ),
            "targeting_advantage": (
                round(targeting_advantage, 6)
                if targeting_advantage is not None
                else None
            ),
            "targeting_advantage_threshold": 0.10,
            "easy_count": easy_count,
            "hard_count": hard_count,
            "duplicate_count": duplicate_count,
            "leak_count": leak_count,
            "warnings": warnings,
            "targeting": targeting,
        }

    def generic_counterfactual_snapshot(
        self, snapshot: dict[str, Any]
    ) -> dict[str, Any]:
        """Build a same-model target with specialization and bound resources removed."""

        generic = copy.deepcopy(snapshot)
        workflow = dict(generic.get("workflow") or {})
        nodes = [dict(item) for item in list(workflow.get("nodes") or [])]
        edges = [dict(item) for item in list(workflow.get("edges") or [])]
        binding_handles = {"expert", "knowledge", "toolset", "plugin", "middleware"}
        binding_sources = {
            str(edge.get("source") or "")
            for edge in edges
            if str(edge.get("targetHandle") or "") in binding_handles
        }
        resource_kinds = {
            "external_xpert",
            "knowledge_base",
            "toolset_resource",
            "plugin_resource",
            "runtime_middleware",
        }
        retained_nodes: list[dict[str, Any]] = []
        removed_node_ids: set[str] = set()
        for node in nodes:
            data = dict(node.get("data") or {})
            kind = str(data.get("kind") or node.get("type") or "")
            node_id = str(node.get("id") or "")
            if kind in resource_kinds and node_id in binding_sources:
                removed_node_ids.add(node_id)
                continue
            if kind == "workflow_agent":
                data.update(
                    {
                        "rolePrompt": (
                            "You are a general-purpose assistant. Answer the current "
                            "request accurately without relying on private domain instructions."
                        ),
                        "promptSuffix": "",
                        "toolMode": "none",
                        "toolNames": "",
                        "memoryReadEnabled": False,
                        "memoryWriteEnabled": False,
                        "knowledgeReadEnabled": False,
                        "knowledgeWriteEnabled": False,
                        "knowledgeBaseIds": [],
                        "enableFileUnderstanding": False,
                    }
                )
                node["data"] = data
            retained_nodes.append(node)
        workflow["nodes"] = retained_nodes
        workflow["edges"] = [
            edge
            for edge in edges
            if str(edge.get("source") or "") not in removed_node_ids
            and str(edge.get("target") or "") not in removed_node_ids
            and str(edge.get("targetHandle") or "") not in binding_handles
        ]
        generic["workflow"] = workflow
        generic["prompt_profiles"] = []
        generic["input_template"] = None
        generic["resources"] = {
            "toolsets": [],
            "plugins": [],
            "knowledge_versions": [],
            "external_xperts": [],
        }
        generic["target_id"] = f"{snapshot.get('target_id')}:generic-counterfactual"
        generic["label"] = f"{snapshot.get('label')} generic counterfactual"[:160]
        generic["source"] = {
            "kind": "benchmark_generic_counterfactual",
            "based_on_target_id": snapshot.get("target_id"),
        }
        generic["benchmark_counterfactual"] = True
        generic["checksum"] = self._checksum(
            {
                "workflow": workflow,
                "input_template": None,
                "based_on": snapshot.get("checksum"),
            }
        )
        return generic

    def assert_dataset_target_fresh(self, dataset: dict[str, Any]) -> None:
        if str(dataset.get("origin") or "manual") != "generated":
            return
        calibration = dict(dataset.get("calibration") or {})
        reference = dict(calibration.get("target_reference") or {})
        checksum = str(calibration.get("target_checksum") or "")
        if not reference or not checksum or not self.target_is_fresh(reference, checksum):
            self.evaluation_store.set_dataset_calibration(
                str(dataset["dataset_id"]),
                revision=int(dataset["revision"]),
                calibration={
                    **calibration,
                    "status": "stale",
                    "reason": "target_changed",
                },
            )
            raise EvaluationStateError(
                "Generated dataset calibration is stale because its target changed."
            )

    def _safe_generation_context(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        workflow = dict(snapshot.get("workflow") or {})
        agents: list[dict[str, Any]] = []
        for node in list(workflow.get("nodes") or []):
            data = dict(node.get("data") or {}) if isinstance(node, dict) else {}
            if str(data.get("kind") or node.get("type") or "") != "workflow_agent":
                continue
            agents.append(
                {
                    "node_id": str(node.get("id") or "")[:120],
                    "title": str(data.get("title") or "Agent")[:160],
                    "role_prompt": str(data.get("rolePrompt") or "")[:6_000],
                    "prompt_suffix": str(data.get("promptSuffix") or "")[:2_000],
                    "task_input": str(data.get("taskInput") or "")[:2_000],
                    "output_schema_mode": str(data.get("outputSchemaMode") or "none"),
                    "output_schema": str(data.get("outputSchemaJson") or "")[:4_000],
                }
            )
        profile = dict(snapshot.get("prompt_profile") or {})
        prompt_command_aliases = self._prompt_aliases(snapshot)
        return {
            "target": {
                "name": str((snapshot.get("xpert") or {}).get("name") or snapshot.get("label") or ""),
                "description": str((snapshot.get("xpert") or {}).get("description") or "")[:2_000],
                "kind": str((snapshot.get("source") or {}).get("kind") or ""),
            },
            "agents": agents[:10],
            "input_variable": snapshot.get("input_variable"),
            "output_variable": snapshot.get("output_variable"),
            "allowed_tool_names": self.detect_coverage(snapshot)["tool_names"],
            "knowledge_base_ids": self.detect_coverage(snapshot)["knowledge_base_ids"],
            "knowledge_document_names": self._knowledge_document_names(snapshot),
            "prompt_command_aliases": prompt_command_aliases,
            "target_anchors": self.target_anchors(snapshot),
            "prompt_command": {
                "aliases": list(profile.get("aliases") or [])[:5],
                "template": str(profile.get("template") or "")[:8_000],
                "argument_hint": str(profile.get("argument_hint") or "")[:500],
            }
            if profile
            else None,
        }

    @staticmethod
    def _prompt_aliases(snapshot: dict[str, Any]) -> list[str]:
        profiles = [
            dict(item)
            for item in list(snapshot.get("prompt_profiles") or [])
            if isinstance(item, dict)
        ]
        direct = dict(snapshot.get("prompt_profile") or {})
        if direct:
            profiles.insert(0, direct)
        aliases = [
            str(alias).lstrip("/").strip().casefold()
            for profile in profiles
            for alias in list(profile.get("aliases") or [])
            if str(alias).strip()
        ]
        return list(dict.fromkeys(aliases))[:50]

    def _structured_output_schemas(
        self,
        snapshot: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        schemas: dict[str, dict[str, Any]] = {}
        workflow = dict(snapshot.get("workflow") or {})
        for index, node in enumerate(list(workflow.get("nodes") or [])):
            if not isinstance(node, dict):
                continue
            data = dict(node.get("data") or {})
            node_kind = str(data.get("kind") or node.get("type") or "")
            node_key = self._anchor_key(node.get("id") or f"node-{index + 1}")
            anchor_id = ""
            if node_kind == "workflow_agent" and str(
                data.get("outputSchemaMode") or "none"
            ) not in {"", "none"}:
                anchor_id = f"agent:{node_key}:output_schema"
            elif node_kind == "runtime_middleware" and str(
                data.get("middlewareId") or data.get("middleware_id") or ""
            ) == "structured_output":
                anchor_id = f"middleware:{node_key}:structured_output"
            if not anchor_id:
                continue
            raw_schema = str(data.get("outputSchemaJson") or "").strip()
            try:
                parsed = json.loads(raw_schema)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                schemas[anchor_id] = parsed
        return schemas

    def _resolved_tool_names(self, snapshot: dict[str, Any]) -> list[str]:
        names: list[str] = []
        workflow = dict(snapshot.get("workflow") or {})
        for node in list(workflow.get("nodes") or []):
            data = dict(node.get("data") or {}) if isinstance(node, dict) else {}
            kind = str(data.get("kind") or node.get("type") or "")
            if kind == "workflow_agent":
                names.extend(self._string_list(data.get("toolNames")))
                if self._truthy(data.get("memoryReadEnabled")):
                    names.extend(["memory_search", "memory_get"])
                if self._truthy(data.get("knowledgeReadEnabled")):
                    names.extend(["knowledge_search", "knowledge_get", "knowledge_cite"])
            elif kind in {"mcp_tool", "external_xpert"}:
                names.extend(
                    self._string_list(
                        data.get("toolName")
                        or data.get("mcpToolName")
                        or data.get("title")
                    )
                )
            elif kind == "toolset_resource" and self.toolset_store is not None:
                try:
                    version = self.toolset_store.get_version(
                        str(data.get("toolsetId") or ""),
                        int(data.get("pinnedVersion") or 0),
                    )
                    for tool in list(getattr(version, "tools", []) or []):
                        if bool(getattr(tool, "enabled", False)):
                            names.append(
                                str(
                                    getattr(tool, "exposed_name", None)
                                    or getattr(tool, "alias", None)
                                    or getattr(tool, "name", "")
                                )
                            )
                except Exception:
                    continue
        return sorted({name.strip() for name in names if str(name).strip()})[:100]

    def _knowledge_document_names(self, snapshot: dict[str, Any]) -> list[str]:
        if self.rag_service is None:
            return []
        names: list[str] = []
        resources = dict(snapshot.get("resources") or {})
        for item in list(resources.get("knowledge_versions") or []):
            if not isinstance(item, dict) or not str(item.get("version_id") or ""):
                continue
            try:
                version = self.rag_service.get_pipeline_version(str(item["version_id"]))
            except Exception:
                continue
            for source in list(version.get("source_summary") or []):
                if not isinstance(source, dict):
                    continue
                name = str(
                    source.get("filename") or source.get("document_name") or ""
                ).strip()
                if name:
                    names.append(name[:240])
        return list(dict.fromkeys(names))[:100]

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def public_target(snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "target_id": snapshot.get("target_id"),
            "label": snapshot.get("label"),
            "source": copy.deepcopy(snapshot.get("source") or {}),
            "xpert": copy.deepcopy(snapshot.get("xpert") or {}),
            "checksum": snapshot.get("checksum"),
            "warnings": list(snapshot.get("warnings") or []),
        }

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any]:
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise BenchmarkGenerationError("Generator did not return a JSON object.")
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise BenchmarkGenerationError(f"Generator returned invalid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise BenchmarkGenerationError("Generator output must be a JSON object.")
        return value

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return [str(item).strip() for item in list(value or []) if str(item).strip()]

    @staticmethod
    def _has_deterministic_expectation(expected: dict[str, Any]) -> bool:
        return any(
            [
                isinstance(expected.get("exact_answer"), str),
                bool(expected.get("contains")),
                isinstance(expected.get("json_schema"), dict),
                bool(expected.get("citation_ids")),
                bool(expected.get("chunk_ids")),
                bool(expected.get("document_names")),
                bool(expected.get("required_tools")),
                bool(expected.get("forbidden_tools")),
                bool(expected.get("tool_order")),
            ]
        )

    @classmethod
    def _strip_server_owned_contains(
        cls,
        expectation: dict[str, Any],
        blueprint: dict[str, Any],
    ) -> list[str]:
        prompt_alias = str(blueprint.get("required_prompt_alias") or "").lstrip("/")
        reserved_values = {
            str(blueprint.get("required_tool_name") or ""),
            str(blueprint.get("required_document_name") or ""),
            *(
                str(item)
                for item in list(blueprint.get("forbidden_tool_names") or [])
            ),
            prompt_alias,
            f"/{prompt_alias}" if prompt_alias else "",
        }
        normalized_reserved = {
            cls._normalize_text(item) for item in reserved_values if item
        }
        original = [
            str(item)
            for item in list(expectation.get("contains") or [])
            if str(item)
        ]
        kept = [
            item
            for item in original
            if cls._normalize_text(item) not in normalized_reserved
        ]
        removed = [item for item in original if item not in kept]
        if removed:
            expectation["contains"] = kept
        return removed

    @classmethod
    def _gold_leaks_into_input(cls, case: dict[str, Any]) -> bool:
        message = cls._normalize_text(case.get("message") or "")
        expected = dict(case.get("expected") or {})
        values = []
        exact = expected.get("exact_answer")
        if isinstance(exact, str):
            values.append(exact)
        values.extend(list(expected.get("contains") or []))
        return any(
            len(cls._normalize_text(item)) >= 24
            and cls._normalize_text(item) in message
            for item in values
        )

    @classmethod
    def _near_duplicate_count(cls, cases: list[dict[str, Any]]) -> int:
        signatures = [set(cls._normalize_text(case.get("message") or "").split()) for case in cases]
        count = 0
        for index, left in enumerate(signatures):
            for right in signatures[index + 1 :]:
                union = left | right
                if union and len(left & right) / len(union) >= 0.85:
                    count += 1
        return count

    @staticmethod
    def targeting_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
        difficulty_counts = {"basic": 0, "edge": 0, "adversarial": 0}
        target_ref_counts: dict[str, int] = {}
        coverage_counts: dict[str, int] = {}
        capability_matrix_counts: dict[str, int] = {}
        focus_term_counts: dict[str, int] = {}
        pressure_type_counts: dict[str, int] = {}
        combined_capabilities: set[str] = set()
        combined_case_count = 0
        cases_with_focus = 0
        discriminator_count = 0
        blueprint_case_count = 0
        normalized_case_count = 0
        normalization_note_counts: dict[str, int] = {}
        professional_evidence_case_count = 0
        professional_evidence_scores: list[float] = []
        missing_count = 0
        for case in cases:
            targeting = case.get("targeting") if isinstance(case, dict) else None
            if not isinstance(targeting, dict):
                missing_count += 1
                continue
            difficulty = str(targeting.get("difficulty") or "")
            if difficulty in difficulty_counts:
                difficulty_counts[difficulty] += 1
            else:
                missing_count += 1
            for ref in list(targeting.get("target_refs") or []):
                key = str(ref)
                if key:
                    target_ref_counts[key] = target_ref_counts.get(key, 0) + 1
            matrix = sorted(
                {
                    str(item)
                    for item in list(targeting.get("capability_matrix") or [])
                    if str(item) in SUPPORTED_COVERAGE
                }
            )
            if len(matrix) > 1:
                combined_case_count += 1
                combined_capabilities.update(matrix)
                signature = " + ".join(matrix)
                capability_matrix_counts[signature] = (
                    capability_matrix_counts.get(signature, 0) + 1
                )
            focus_terms = [
                str(item)
                for item in list(targeting.get("focus_terms") or [])
                if str(item).strip()
            ]
            if focus_terms:
                cases_with_focus += 1
            for term in focus_terms:
                focus_term_counts[term] = focus_term_counts.get(term, 0) + 1
            for pressure_type in list(targeting.get("pressure_types") or []):
                key = str(pressure_type)
                if key:
                    pressure_type_counts[key] = pressure_type_counts.get(key, 0) + 1
            if str(targeting.get("discriminator") or "").strip():
                discriminator_count += 1
            if str(targeting.get("blueprint_id") or "").strip():
                blueprint_case_count += 1
            professional_evidence = targeting.get("professional_evidence")
            if isinstance(professional_evidence, dict) and bool(
                professional_evidence.get("sufficient")
            ):
                professional_evidence_case_count += 1
                professional_evidence_scores.append(
                    float(professional_evidence.get("score") or 0.0)
                )
            notes = [
                str(item)
                for item in list(targeting.get("normalization_notes") or [])
                if str(item).strip()
            ]
            if notes:
                normalized_case_count += 1
            for note in notes:
                normalization_note_counts[note] = (
                    normalization_note_counts.get(note, 0) + 1
                )
            for tag in list(case.get("tags") or []):
                key = str(tag)
                if key in SUPPORTED_COVERAGE:
                    coverage_counts[key] = coverage_counts.get(key, 0) + 1
        return {
            "difficulty_counts": difficulty_counts,
            "target_ref_counts": dict(sorted(target_ref_counts.items())),
            "coverage_counts": dict(sorted(coverage_counts.items())),
            "capability_matrix_counts": dict(sorted(capability_matrix_counts.items())),
            "combined_case_count": combined_case_count,
            "combined_capabilities": sorted(combined_capabilities),
            "focus_term_counts": dict(sorted(focus_term_counts.items())),
            "cases_with_focus": cases_with_focus,
            "pressure_type_counts": dict(sorted(pressure_type_counts.items())),
            "discriminator_count": discriminator_count,
            "blueprint_case_count": blueprint_case_count,
            "normalized_case_count": normalized_case_count,
            "normalization_note_counts": dict(
                sorted(normalization_note_counts.items())
            ),
            "professional_evidence_case_count": professional_evidence_case_count,
            "professional_evidence_score_average": round(
                sum(professional_evidence_scores)
                / max(1, len(professional_evidence_scores)),
                4,
            ),
            "target_anchor_count": len(target_ref_counts),
            "missing_count": missing_count,
        }

    @classmethod
    def _professional_evidence(
        cls,
        *,
        case: dict[str, Any],
        anchors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        input_text = cls._normalize_text(
            " ".join(
                [
                    str(case.get("message") or ""),
                    *[
                        str(item.get("content") or "")
                        for item in list(case.get("messages") or [])
                        if isinstance(item, dict)
                    ],
                ]
            )
        )
        support_text = cls._normalize_text(
            " ".join(
                [
                    json.dumps(case.get("expected") or {}, ensure_ascii=False),
                    *[
                        str((case.get("targeting") or {}).get(field) or "")
                        for field in ("rationale", "challenge", "discriminator")
                    ],
                ]
            )
        )
        focus_terms = list(
            dict.fromkeys(
                str(term).strip()
                for anchor in anchors
                for term in list(anchor.get("focus_terms") or [])
                if str(term).strip()
            )
        )
        marker_terms = list(focus_terms)
        for anchor in anchors:
            summary = str(anchor.get("summary") or "")
            for token in re.findall(r"[A-Za-z][A-Za-z0-9_.+:/-]{2,}", summary):
                normalized = cls._normalize_text(token.strip(".,;:()[]{}"))
                if normalized and normalized not in GENERIC_EVIDENCE_TERMS:
                    marker_terms.append(token.strip(".,;:()[]{}"))
            marker_terms.extend(cls._focus_terms(summary, limit=24))
        marker_terms = list(
            dict.fromkeys(
                term
                for term in marker_terms
                if cls._normalize_text(term)
                and cls._normalize_text(term) not in GENERIC_EVIDENCE_TERMS
            )
        )
        exact_input = [
            term for term in focus_terms if cls._normalize_text(term) in input_text
        ]
        matched_input = [
            term for term in marker_terms if cls._normalize_text(term) in input_text
        ]
        matched_support = [
            term for term in marker_terms if cls._normalize_text(term) in support_text
        ]
        distinct_input = list(dict.fromkeys(matched_input))
        distinct_support = list(dict.fromkeys(matched_support))
        sufficient = bool(exact_input) or len(distinct_input) >= 2 or (
            len(distinct_input) >= 1 and len(distinct_support) >= 2
        )
        raw_score = min(
            1.0,
            (2 * len(set(exact_input)) + len(distinct_input) + 0.5 * len(distinct_support))
            / 4.0,
        )
        return {
            "sufficient": sufficient,
            "score": round(raw_score, 4),
            "exact_focus_matches": exact_input[:8],
            "input_markers": distinct_input[:12],
            "support_markers": distinct_support[:12],
            "anchor_ids": [str(anchor.get("id") or "") for anchor in anchors][:8],
        }

    @staticmethod
    def _anchor_key(value: Any) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip())
        return normalized.strip("-")[:96] or "resource"

    @staticmethod
    def _safe_excerpt(value: Any, limit: int) -> str:
        text = " ".join(str(value or "").split())
        text = re.sub(
            r"(?i)\b(api[_ -]?key|token|secret|password)\b\s*[:=]\s*[^\s,;]+",
            r"\1=[redacted]",
            text,
        )
        text = re.sub(r"(?i)\bsk-[a-z0-9_-]{8,}\b", "[redacted]", text)
        return text[:limit]

    @classmethod
    def _focus_terms(cls, value: Any, limit: int = 16) -> list[str]:
        """Extract stable domain terms without exposing more prompt text."""

        text = cls._safe_excerpt(value, 2_000)
        candidates: list[str] = []
        words = re.findall(r"[A-Za-z][A-Za-z0-9_.+:/-]{1,}", text)
        meaningful_words = [
            word.strip(".,;:()[]{}")
            for word in words
            if len(word.casefold().strip("._+:/-")) >= 3
            and word.casefold().strip("._+:/-") not in GENERIC_FOCUS_TERMS
            and not word.isdigit()
        ]
        candidates.extend(
            word
            for word in meaningful_words
            if word.isupper() or "-" in word or "_" in word
        )
        candidates.extend(
            f"{left} {right}"
            for left, right in zip(meaningful_words, meaningful_words[1:])
            if len(left) + len(right) >= 8
        )
        candidates.extend(meaningful_words)

        cjk_chunks = re.findall(r"[\u3400-\u9fff]{2,18}", text)
        for chunk in cjk_chunks:
            cleaned = chunk
            for prefix in ("\u4f60\u662f", "\u4f5c\u4e3a", "\u8bf7", "\u8d1f\u8d23"):
                if cleaned.startswith(prefix):
                    cleaned = cleaned[len(prefix) :]
            for suffix in ("\u667a\u80fd\u4f53", "\u52a9\u624b", "\u4e13\u5bb6"):
                if cleaned.endswith(suffix):
                    cleaned = cleaned[: -len(suffix)]
            for part in re.split(r"[\u4e0e\u53ca\u548c\u6216]", cleaned):
                term = part.strip()
                if 2 <= len(term) <= 14 and term not in GENERIC_FOCUS_TERMS:
                    candidates.append(term)

        output: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            normalized = cls._normalize_text(candidate)
            if not normalized or normalized in seen or normalized in GENERIC_FOCUS_TERMS:
                continue
            seen.add(normalized)
            output.append(candidate[:80])
            if len(output) >= limit:
                break
        return output

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return " ".join(str(value or "").casefold().split())

    @staticmethod
    def _checksum(value: Any) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
