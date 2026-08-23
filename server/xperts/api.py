from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from .context import (
    MAX_FILE_BYTES,
    CandidateStatus,
    MemoryScope,
    XpertContextConflictError,
    XpertContextError,
    XpertContextNotFoundError,
    XpertContextStore,
    XpertContextValidationError,
)

from .models import (
    XpertDefinition,
    XpertDraft,
    XpertStatus,
    XpertSummary,
    XpertValidationResult,
    XpertVersion,
)
from .store import (
    XpertConflictError,
    XpertNotFoundError,
    XpertStore,
    XpertStoreError,
    XpertValidationError,
)
from .validation import validate_xpert_definition

try:
    from server.file_assets.contracts import FileInputKind, FilePurpose
    from server.file_assets.registry import get_file_format_registry
    from server.multimodal.vision_understanding import SUPPORTED_IMAGE_EXTENSIONS
    from server.data_tables.api import get_agent_table_store
    from server.rag.api import get_rag_service
    from server.plugins.registry import get_plugin_store
    from server.prompts import get_prompt_profile_store
    from server.prompts.models import ResolvedPromptProfile
    from server.skills.api import get_skill_manager
    from server.skills.skill_manager import SkillManagerError
    from server.workflow_native.schemas import NativeWorkflowDefinition, ValidationIssue
    from server.workflow_native.node_contracts import node_policy_service
    from server.workflow_native.secure_http import (
        WorkflowHttpRequestError,
        is_http_request_v2,
        validate_http_request_credential,
    )
    from server.workflow_native.r20_nodes import (
        WorkflowR20NodeError,
        contract_version as r20_contract_version,
    )
except ModuleNotFoundError:
    from file_assets.contracts import FileInputKind, FilePurpose
    from file_assets.registry import get_file_format_registry
    from multimodal.vision_understanding import SUPPORTED_IMAGE_EXTENSIONS
    from data_tables.api import get_agent_table_store
    from rag.api import get_rag_service
    from plugins.registry import get_plugin_store
    from prompts import get_prompt_profile_store
    from prompts.models import ResolvedPromptProfile
    from skills.api import get_skill_manager
    from skills.skill_manager import SkillManagerError
    from workflow_native.schemas import NativeWorkflowDefinition, ValidationIssue
    from workflow_native.node_contracts import node_policy_service
    from workflow_native.secure_http import (
        WorkflowHttpRequestError,
        is_http_request_v2,
        validate_http_request_credential,
    )
    from workflow_native.r20_nodes import (
        WorkflowR20NodeError,
        contract_version as r20_contract_version,
    )


router = APIRouter(prefix="/api/xperts", tags=["xperts"])
_xpert_store: XpertStore | None = None
_xpert_context_store: XpertContextStore | None = None
_memory_writeback_runner: Callable[..., Awaitable[list[dict]]] | None = None
_workflow_http_credential_lookup: Callable[[str], object] | None = None
_workflow_mcp_tool_validator: Callable[[dict], object] | None = None


def configure_workflow_http_credential_lookup(
    lookup: Callable[[str], object] | None,
) -> None:
    global _workflow_http_credential_lookup
    _workflow_http_credential_lookup = lookup


def configure_workflow_mcp_tool_validator(
    validator: Callable[[dict], object] | None,
) -> None:
    global _workflow_mcp_tool_validator
    _workflow_mcp_tool_validator = validator


def _validate_installed_skills(
    xpert: XpertDefinition,
    validation: XpertValidationResult,
) -> XpertValidationResult:
    manager = get_skill_manager()
    installed = {item.skill_id for item in manager.list_installed_skills()}
    issues = list(validation.issues)
    for node in xpert.draft.workflow.nodes:
        data = node.data if isinstance(node.data, dict) else {}
        if str(data.get("runtimeMiddlewareId") or "") != "skills_runtime":
            continue
        config = data.get("runtimeMiddlewareConfig") or {}
        if str(config.get("auto_discover", False)).lower() in {"true", "1", "yes"}:
            continue
        skill_ids = {
            value.strip()
            for value in re.split(r"[,\n]+", str(config.get("skill_ids") or ""))
            if value.strip()
        }
        missing = sorted(skill_ids - installed)
        if missing:
            issues.append(
                ValidationIssue(
                    code="xpert_skills_not_installed",
                    message=f"Install referenced Skills before publish: {', '.join(missing)}.",
                    node_id=node.id,
                )
            )
        for skill_id in sorted(skill_ids - set(missing)):
            try:
                manager.require_activation(skill_id, check_runtime=False)
            except SkillManagerError as exc:
                issues.append(
                    ValidationIssue(
                        code=str(
                            getattr(exc, "code", "")
                            or "skill_trust_receipt_missing"
                        ),
                        message=str(exc),
                        node_id=node.id,
                    )
                )
    return validation.model_copy(
        update={
            "valid": not any(issue.severity == "error" for issue in issues),
            "issues": issues,
        }
    )


def _prepare_published_resource_snapshot(
    xpert: XpertDefinition,
) -> tuple[NativeWorkflowDefinition, list[ValidationIssue]]:
    store = get_xpert_store()
    workflow = xpert.draft.workflow.model_copy(deep=True)
    issues: list[ValidationIssue] = []
    nodes_by_id = {node.id: node for node in workflow.nodes}

    def hitl_rules_for_agent(agent_node_id: str) -> set[str]:
        rules: set[str] = set()
        for edge in workflow.edges:
            if edge.target != agent_node_id or edge.targetHandle != "middleware":
                continue
            middleware_node = nodes_by_id.get(edge.source)
            if middleware_node is None:
                continue
            middleware_data = (
                middleware_node.data
                if isinstance(middleware_node.data, dict)
                else {}
            )
            if (
                str(middleware_data.get("runtimeMiddlewareId") or "")
                != "human_in_the_loop"
            ):
                continue
            config = middleware_data.get("runtimeMiddlewareConfig") or {}
            rules.update(
                value.strip()
                for value in re.split(
                    r"[,\n]+",
                    str(config.get("interrupt_on_tools") or ""),
                )
                if value.strip()
            )
        return rules

    def external_nodes(
        source_workflow: NativeWorkflowDefinition,
    ) -> list:
        return [
            node
            for node in source_workflow.nodes
            if str((node.data or {}).get("kind") or node.type or "")
            == "external_xpert"
        ]

    for node in workflow.nodes:
        data = node.data if isinstance(node.data, dict) else {}
        kind = str(data.get("kind") or node.type or "")
        if kind in {
            "data_table_query",
            "data_table_insert",
            "data_table_update",
            "data_table_delete",
        }:
            reference = str(data.get("tableId") or "").strip()
            try:
                if not reference:
                    raise ValueError("Agent Table resource ID is required.")
                policy = str(data.get("versionPolicy") or "latest").strip()
                pinned_value = data.get("pinnedSchemaVersion")
                pinned_version = (
                    int(pinned_value)
                    if pinned_value not in {None, ""}
                    else None
                )
                schema = get_agent_table_store().resolve_schema_version(
                    reference,
                    version_policy=policy,
                    pinned_version=pinned_version,
                    write=kind != "data_table_query",
                )
                get_agent_table_store().validate_workflow_node_contract(
                    reference,
                    schema_version=schema.version,
                    kind=kind,
                    data=data,
                )
                data["tableId"] = schema.table_id
                data["versionPolicy"] = "pinned"
                data["pinnedSchemaVersion"] = schema.version
                node.data = data
            except Exception as exc:
                issues.append(
                    ValidationIssue(
                        code="xpert_agent_table_invalid",
                        message=str(exc),
                        node_id=node.id,
                    )
                )
            continue
        if kind == "plugin_resource":
            reference = str(data.get("pluginId") or "").strip()
            try:
                plugin = get_plugin_store().get_plugin(reference)
                policy = str(data.get("versionPolicy") or "latest").strip()
                version_number = (
                    int(data.get("pinnedVersion") or 0)
                    if policy == "pinned"
                    else int(plugin.published_version or 0)
                )
                if plugin.status != "published" or version_number < 1:
                    raise ValueError(
                        f"Plugin must be published before Xpert publish: {reference}."
                    )
                snapshot = get_plugin_store().get_version(
                    plugin.id, version_number
                )
                data["pluginId"] = plugin.id
                data["pluginName"] = snapshot.name
                data["versionPolicy"] = "pinned"
                data["pinnedVersion"] = snapshot.version
                node.data = data
            except Exception as exc:
                issues.append(
                    ValidationIssue(
                        code="xpert_plugin_resource_invalid",
                        message=str(exc),
                        node_id=node.id,
                    )
                )
            continue
        if kind == "toolset_resource":
            reference = str(data.get("toolsetId") or "").strip()
            try:
                try:
                    from server.toolsets import get_toolset_service
                except ModuleNotFoundError:
                    from toolsets import get_toolset_service

                toolset_service = get_toolset_service()
                toolset = toolset_service.store.get_toolset(reference)
                policy = str(
                    data.get("versionPolicy") or "current_published"
                ).strip()
                version_number = (
                    int(data.get("pinnedVersion") or 0)
                    if policy == "pinned"
                    else int(toolset.published_version or 0)
                )
                if toolset.status != "published" or version_number < 1:
                    raise ValueError(
                        f"Toolset must be published before Xpert publish: {reference}."
                    )
                version = toolset_service.store.get_version(
                    toolset.id,
                    version_number,
                )
                data["toolsetId"] = toolset.id
                data["versionPolicy"] = "pinned"
                data["pinnedVersion"] = version_number
                node.data = data
                mutating_approval_names: list[str] = []
                sensitive_approval_names: list[str] = []
                prefix = version.connection.tool_prefix
                for tool in version.tools:
                    if not (tool.requires_approval or tool.sensitive):
                        continue
                    exposed_name = tool.exposed_name
                    resolved_name = (
                        f"{prefix}_{exposed_name}" if prefix else exposed_name
                    )
                    if tool.requires_approval:
                        mutating_approval_names.append(resolved_name)
                    if tool.sensitive:
                        sensitive_approval_names.append(resolved_name)
                if mutating_approval_names or sensitive_approval_names:
                    binding = next(
                        (
                            edge
                            for edge in workflow.edges
                            if edge.source == node.id
                            and edge.targetHandle == "toolset"
                        ),
                        None,
                    )
                    rules = (
                        hitl_rules_for_agent(binding.target)
                        if binding is not None
                        else set()
                    )
                    uncovered_mutating = sorted(
                        name
                        for name in mutating_approval_names
                        if "*" not in rules and name not in rules
                    )
                    uncovered_sensitive = sorted(
                        name
                        for name in sensitive_approval_names
                        if "*" not in rules and name not in rules
                    )
                    if uncovered_mutating:
                        issues.append(
                            ValidationIssue(
                                code="xpert_toolset_mutating_hitl_required",
                                message=(
                                    "Mutating or approval-required Toolset operations require "
                                    "human_in_the_loop coverage: "
                                    + ", ".join(uncovered_mutating)
                                ),
                                node_id=node.id,
                            )
                        )
                    sensitive_only = sorted(
                        set(uncovered_sensitive) - set(uncovered_mutating)
                    )
                    if sensitive_only:
                        issues.append(
                            ValidationIssue(
                                code="xpert_toolset_sensitive_hitl_required",
                                message=(
                                    "Sensitive Toolset operations require "
                                    "human_in_the_loop coverage: "
                                    + ", ".join(sensitive_only)
                                ),
                                node_id=node.id,
                            )
                        )
            except Exception as exc:
                issues.append(
                    ValidationIssue(
                        code="xpert_toolset_resource_invalid",
                        message=str(exc),
                        node_id=node.id,
                    )
                )
            continue
        if kind in {"knowledge_base", "knowledge_retrieval", "knowledge_citation"}:
            kb_id = str(data.get("knowledgeBaseId") or "").strip()
            rag_service = get_rag_service()
            if not kb_id:
                is_legacy_consumer = kind in {
                    "knowledge_retrieval",
                    "knowledge_citation",
                } and data.get("contractVersion") is None
                if is_legacy_consumer:
                    knowledge_bases = rag_service.list_knowledge_bases()
                    if len(knowledge_bases) == 1:
                        kb_id = str(knowledge_bases[0].get("id") or "").strip()
                        issues.append(
                            ValidationIssue(
                                code="xpert_knowledge_legacy_base_resolved",
                                message=(
                                    "Legacy knowledge node omitted knowledgeBaseId; "
                                    "the only available knowledge base will be used."
                                ),
                                severity="warning",
                                node_id=node.id,
                            )
                        )
                    else:
                        issues.append(
                            ValidationIssue(
                                code="xpert_knowledge_base_required",
                                message=(
                                    "Legacy knowledge nodes without knowledgeBaseId "
                                    "require exactly one available knowledge base."
                                ),
                                node_id=node.id,
                            )
                        )
                        continue
                else:
                    continue
            try:
                rag_service.get_pipeline_draft(kb_id)
                active = rag_service.get_active_pipeline_version(kb_id)
                if active is None:
                    issues.append(
                        ValidationIssue(
                            code="xpert_knowledge_active_version_missing",
                            message=(
                                "The bound knowledge base has no active index; "
                                "runtime searches will return no results until one is activated."
                            ),
                            severity="warning",
                            node_id=node.id,
                        )
                    )
            except Exception:
                issues.append(
                    ValidationIssue(
                        code="xpert_knowledge_base_not_found",
                        message=f"Bound knowledge base not found: {kb_id}.",
                        node_id=node.id,
                    )
                )
            continue
        if kind != "external_xpert":
            continue
        reference = str(data.get("xpertId") or "").strip()
        try:
            target = store.resolve_xpert(reference)
            if target.id == xpert.id:
                raise XpertValidationError(
                    "An Xpert cannot bind itself as an external expert."
                )
            policy = str(data.get("versionPolicy") or "current_published")
            version_number = (
                int(data.get("pinnedVersion") or 0)
                if policy == "pinned"
                else int(target.published_version or 0)
            )
            if target.status != "published" or version_number < 1:
                raise XpertValidationError(
                    f"External Xpert must be published: {reference}."
                )
            store.get_version(target.id, version_number)
            data["xpertId"] = target.id
            data["versionPolicy"] = "pinned"
            data["pinnedVersion"] = version_number
            node.data = data
        except (XpertStoreError, TypeError, ValueError) as exc:
            issues.append(
                ValidationIssue(
                    code="xpert_external_resource_invalid",
                    message=str(exc),
                    node_id=node.id,
                )
            )

    nodes_by_id = {node.id: node for node in workflow.nodes}
    names_by_agent: dict[str, set[str]] = {}
    for edge in workflow.edges:
        if str(edge.targetHandle or "") != "toolset":
            continue
        resource = nodes_by_id.get(edge.source)
        agent = nodes_by_id.get(edge.target)
        if resource is None or agent is None:
            continue
        resource_data = resource.data if isinstance(resource.data, dict) else {}
        try:
            try:
                from server.toolsets import get_toolset_service
            except ModuleNotFoundError:
                from toolsets import get_toolset_service

            toolset_service = get_toolset_service()
            toolset_id = str(resource_data.get("toolsetId") or "")
            version_number = int(resource_data.get("pinnedVersion") or 0)
            snapshot = toolset_service.store.get_version(toolset_id, version_number)
            prefix = snapshot.connection.tool_prefix
            bound_names = {
                f"{prefix}_{tool.exposed_name}" if prefix else tool.exposed_name
                for tool in snapshot.tools
            }
            existing = names_by_agent.setdefault(edge.target, set())
            duplicates = sorted(existing.intersection(bound_names))
            inline_names = {
                item.strip()
                for item in re.split(
                    r"[,\n]+",
                    str(agent.data.get("toolNames") or ""),
                )
                if item.strip()
            }
            inline_conflicts = sorted(inline_names.intersection(bound_names))
            if duplicates or inline_conflicts:
                conflict_names = sorted(set(duplicates + inline_conflicts))
                issues.append(
                    ValidationIssue(
                        code="xpert_toolset_name_conflict",
                        message=(
                            "Bound Toolset names conflict for this workflow_agent: "
                            + ", ".join(conflict_names)
                        ),
                        node_id=resource.id,
                    )
                )
            existing.update(bound_names)
        except Exception:
            continue

    middleware_ids_by_agent: dict[str, set[str]] = {}
    for edge in workflow.edges:
        if str(edge.targetHandle or "") != "middleware":
            continue
        source = nodes_by_id.get(edge.source)
        if source is None:
            continue
        middleware_id = str(
            (source.data or {}).get("runtimeMiddlewareId") or ""
        ).strip()
        if middleware_id:
            middleware_ids_by_agent.setdefault(edge.target, set()).add(
                middleware_id
            )

    for edge in workflow.edges:
        if str(edge.targetHandle or "") != "plugin":
            continue
        resource = nodes_by_id.get(edge.source)
        agent = nodes_by_id.get(edge.target)
        if resource is None or agent is None:
            continue
        data = resource.data if isinstance(resource.data, dict) else {}
        try:
            snapshot = get_plugin_store().get_version(
                str(data.get("pluginId") or ""),
                int(data.get("pinnedVersion") or 0),
            )
            existing_names = names_by_agent.setdefault(edge.target, set())
            inline_names = {
                item.strip()
                for item in re.split(
                    r"[,\n]+",
                    str(agent.data.get("toolNames") or ""),
                )
                if item.strip()
            }
            for reference in snapshot.toolsets:
                try:
                    from server.toolsets import get_toolset_service
                except ModuleNotFoundError:
                    from toolsets import get_toolset_service
                toolset_snapshot = get_toolset_service().store.get_version(
                    reference.toolset_id,
                    reference.version,
                )
                if toolset_snapshot.schema_hash != reference.schema_hash:
                    raise ValueError(
                        f"Plugin Toolset schema hash changed: {reference.toolset_id}."
                    )
                prefix = toolset_snapshot.connection.tool_prefix
                plugin_names = {
                    (
                        f"{prefix}_{tool.exposed_name}"
                        if prefix
                        else tool.exposed_name
                    )
                    for tool in toolset_snapshot.tools
                    if tool.enabled
                }
                conflicts = sorted(
                    existing_names.intersection(plugin_names)
                    | inline_names.intersection(plugin_names)
                )
                if conflicts:
                    issues.append(
                        ValidationIssue(
                            code="xpert_plugin_tool_name_conflict",
                            message=(
                                "Plugin Toolset names conflict for this "
                                "workflow_agent: " + ", ".join(conflicts)
                            ),
                            node_id=resource.id,
                        )
                    )
                existing_names.update(plugin_names)
            middleware_ids = middleware_ids_by_agent.setdefault(
                edge.target, set()
            )
            for preset in snapshot.middleware_presets:
                if preset.middleware_id in middleware_ids:
                    issues.append(
                        ValidationIssue(
                            code="xpert_plugin_middleware_conflict",
                            message=(
                                "Plugin middleware conflicts for this workflow_agent: "
                                f"{preset.middleware_id}."
                            ),
                            node_id=resource.id,
                        )
                    )
                middleware_ids.add(preset.middleware_id)
        except Exception as exc:
            issues.append(
                ValidationIssue(
                    code="xpert_plugin_dependency_invalid",
                    message=str(exc),
                    node_id=resource.id,
                )
            )

    def walk_dependencies(
        owner_id: str,
        owner_workflow: NativeWorkflowDefinition,
        path: tuple[str, ...],
    ) -> None:
        for resource_node in external_nodes(owner_workflow):
            resource_data = resource_node.data or {}
            reference = str(resource_data.get("xpertId") or "").strip()
            try:
                target = store.resolve_xpert(reference)
                policy = str(
                    resource_data.get("versionPolicy") or "current_published"
                )
                version_number = (
                    int(resource_data.get("pinnedVersion") or 0)
                    if policy == "pinned"
                    else int(target.published_version or 0)
                )
                if version_number < 1:
                    continue
                if target.id in path:
                    issues.append(
                        ValidationIssue(
                            code="xpert_external_resource_cycle",
                            message=(
                                "External Xpert collaboration cycle detected: "
                                + " -> ".join((*path, target.id))
                            ),
                            node_id=(
                                resource_node.id if owner_id == xpert.id else None
                            ),
                        )
                    )
                    continue
                target_version = store.get_version(target.id, version_number)
                walk_dependencies(
                    target.id,
                    target_version.workflow,
                    (*path, target.id),
                )
            except (XpertStoreError, TypeError, ValueError):
                continue

    walk_dependencies(xpert.id, workflow, (xpert.id,))
    return workflow, issues


def _resolve_published_prompt_profiles(
    xpert: XpertDefinition,
    workflow: NativeWorkflowDefinition,
) -> tuple[list[ResolvedPromptProfile], list[ValidationIssue]]:
    resolved: list[ResolvedPromptProfile] = []
    issues: list[ValidationIssue] = []
    prompt_store = get_prompt_profile_store()
    plugin_store = get_plugin_store()

    for binding in xpert.draft.prompt_profiles:
        if not binding.enabled:
            continue
        try:
            profile = prompt_store.get_profile(binding.profile_id)
            version_number = (
                int(binding.pinned_version or 0)
                if binding.version_policy == "pinned"
                else int(profile.published_version or 0)
            )
            if profile.status != "published" or version_number < 1:
                raise ValueError(
                    f"Prompt Profile must be published: {binding.profile_id}."
                )
            snapshot = prompt_store.get_version(profile.id, version_number)
            resolved.append(
                ResolvedPromptProfile(
                    profile_id=profile.id,
                    slug=profile.slug,
                    version=snapshot.version,
                    name=snapshot.name,
                    description=snapshot.description,
                    aliases=list(snapshot.aliases),
                    template=snapshot.template,
                    argument_hint=snapshot.argument_hint,
                    public_app_allowed=snapshot.public_app_allowed,
                    checksum=snapshot.checksum,
                    source="direct",
                )
            )
        except Exception as exc:
            issues.append(
                ValidationIssue(
                    code="xpert_prompt_profile_invalid",
                    message=str(exc),
                )
            )

    for node in workflow.nodes:
        data = node.data if isinstance(node.data, dict) else {}
        if str(data.get("kind") or node.type or "") != "plugin_resource":
            continue
        try:
            plugin_id = str(data.get("pluginId") or "").strip()
            version_number = int(data.get("pinnedVersion") or 0)
            snapshot = plugin_store.get_version(plugin_id, version_number)
            resolved.extend(profile.model_copy(deep=True) for profile in snapshot.prompts)
        except Exception as exc:
            issues.append(
                ValidationIssue(
                    code="xpert_plugin_prompt_invalid",
                    message=str(exc),
                    node_id=node.id,
                )
            )

    aliases: dict[str, str] = {}
    for profile in resolved:
        for alias in profile.aliases:
            owner = aliases.get(alias)
            if owner is not None:
                issues.append(
                    ValidationIssue(
                        code="xpert_prompt_alias_conflict",
                        message=(
                            f"Prompt command alias '{alias}' is provided by both "
                            f"{owner} and {profile.name}."
                        ),
                    )
                )
            else:
                aliases[alias] = profile.name
    return resolved, issues


def preview_xpert_for_publish(
    xpert: XpertDefinition,
) -> tuple[
    XpertValidationResult,
    NativeWorkflowDefinition,
    list[ResolvedPromptProfile],
]:
    workflow, resource_issues = _prepare_published_resource_snapshot(xpert)
    prompt_profiles, prompt_issues = _resolve_published_prompt_profiles(
        xpert, workflow
    )
    candidate = xpert.model_copy(deep=True)
    candidate.draft.workflow = workflow
    validation = _validate_installed_skills(
        candidate,
        validate_xpert_definition(candidate),
    )
    feature_issues: list[ValidationIssue] = []
    for node in candidate.draft.workflow.nodes:
        data = node.data if isinstance(node.data, dict) else {}
        kind = str(data.get("kind") or node.type or "")
        policy = node_policy_service.decision(kind, "xpert")
        if not policy.allowed:
            feature_issues.append(
                ValidationIssue(
                    code=policy.code or "xpert_node_forbidden",
                    message=policy.message
                    or f"Xpert publish does not allow node kind: {kind}.",
                    node_id=node.id,
                )
            )
        if kind == "http_request":
            if not is_http_request_v2(data):
                feature_issues.append(
                    ValidationIssue(
                        code="xpert_http_request_migration_required",
                        message="Legacy HTTP request nodes must be explicitly migrated before publish.",
                        node_id=node.id,
                    )
                )
                continue
            if os.getenv("WORKFLOW_HTTP_REQUESTS_ENABLED", "false").strip().lower() not in {
                "1", "true", "yes", "on"
            }:
                feature_issues.append(
                    ValidationIssue(
                        code="xpert_http_requests_disabled",
                        message="Secure workflow HTTP requests are disabled.",
                        node_id=node.id,
                    )
                )
            try:
                validate_http_request_credential(
                    data,
                    _workflow_http_credential_lookup,
                )
            except WorkflowHttpRequestError as exc:
                feature_issues.append(
                    ValidationIssue(
                        code="xpert_http_credential_unavailable",
                        message=exc.safe_message,
                        node_id=node.id,
                    )
                )
        if kind in {"human_intervention", "mcp_tool", "variable_assign"} and (
            r20_contract_version(data) != 2
        ):
            feature_issues.append(
                ValidationIssue(
                    code=f"xpert_{kind}_migration_required",
                    message=f"Legacy {kind} nodes must be explicitly migrated before publish.",
                    node_id=node.id,
                )
            )
        if kind == "knowledge_citation":
            feature_issues.append(
                ValidationIssue(
                    code="xpert_knowledge_citation_migration_required",
                    message="Knowledge citation nodes must be migrated to knowledge_retrieval before publish.",
                    node_id=node.id,
                )
            )
        if kind == "mcp_tool" and r20_contract_version(data) == 2:
            if os.getenv("WORKFLOW_MCP_TOOLS_ENABLED", "false").strip().lower() not in {
                "1", "true", "yes", "on"
            }:
                feature_issues.append(
                    ValidationIssue(
                        code="xpert_mcp_tools_disabled",
                        message="Workflow MCP tools are disabled.",
                        node_id=node.id,
                    )
                )
            if _workflow_mcp_tool_validator is None:
                feature_issues.append(
                    ValidationIssue(
                        code="xpert_mcp_registry_unavailable",
                        message="Workflow MCP tool registry validation is unavailable.",
                        node_id=node.id,
                    )
                )
            else:
                try:
                    _workflow_mcp_tool_validator(data)
                except WorkflowR20NodeError as exc:
                    feature_issues.append(
                        ValidationIssue(
                            code=f"xpert_{exc.code.lower()}",
                            message=exc.safe_message,
                            node_id=node.id,
                        )
                    )
        if kind == "file_output" and os.getenv(
            "FILE_OUTPUT_ASSETS_ENABLED", "false"
        ).strip().lower() not in {"1", "true", "yes", "on"}:
            feature_issues.append(
                ValidationIssue(
                    code="xpert_file_output_disabled",
                    message="Workflow file output is disabled.",
                    node_id=node.id,
                )
            )
        if kind == "document_extractor" and str(
            data.get("assetIdVariable") or ""
        ).strip() and os.getenv(
            "WORKFLOW_FILE_ASSETS_ENABLED", "false"
        ).strip().lower() not in {"1", "true", "yes", "on"}:
            feature_issues.append(
                ValidationIssue(
                    code="xpert_workflow_files_disabled",
                    message="Workflow file assets are disabled.",
                    node_id=node.id,
                )
            )
        if kind == "document_extractor":
            asset_id_variable = str(data.get("assetIdVariable") or "").strip()
            legacy_path_variable = str(data.get("sourcePathVariable") or "").strip()
            if legacy_path_variable:
                feature_issues.append(
                    ValidationIssue(
                        code="xpert_document_asset_migration_required",
                        message=(
                            "Private Xperts cannot publish legacy path-based document "
                            "nodes. Migrate the node to a scoped file asset first."
                        ),
                        node_id=node.id,
                    )
                )
            elif asset_id_variable != "selected_file_asset_id":
                feature_issues.append(
                    ValidationIssue(
                        code="xpert_document_asset_binding_required",
                        message=(
                            "Private Xpert document nodes must use the "
                            "selected_file_asset_id run variable."
                        ),
                        node_id=node.id,
                    )
                )
    features = candidate.draft.features
    if (
        features.text_to_speech.enabled
        and not features.text_to_speech.model_id.strip()
    ):
        feature_issues.append(
            ValidationIssue(
                code="xpert_tts_model_required",
                message="Select a speech model before enabling text-to-speech.",
            )
        )
    if (
        features.speech_to_text.enabled
        and not features.speech_to_text.model_id.strip()
    ):
        feature_issues.append(
            ValidationIssue(
                code="xpert_stt_model_required",
                message="Select a transcription model before enabling speech-to-text.",
            )
        )
    supported_file_extensions = set(
        get_file_format_registry().extensions_for(
            FilePurpose.AGENT,
            FileInputKind.DOCUMENT,
        )
    ) | set(SUPPORTED_IMAGE_EXTENSIONS)
    configured_extensions = {
        (
            value.strip().lower()
            if value.strip().startswith(".")
            else f".{value.strip().lower()}"
        )
        for value in features.file_upload.allowed_extensions
        if value.strip()
    }
    unsupported_extensions = sorted(
        configured_extensions - supported_file_extensions
    )
    if unsupported_extensions:
        feature_issues.append(
            ValidationIssue(
                code="xpert_file_extension_unsupported",
                message=(
                    "Unsupported Xpert file extensions: "
                    + ", ".join(unsupported_extensions)
                ),
            )
        )
    issues = [
        *validation.issues,
        *resource_issues,
        *prompt_issues,
        *feature_issues,
    ]
    return (
        validation.model_copy(
            update={
                "valid": not any(issue.severity == "error" for issue in issues),
                "issues": issues,
            }
        ),
        workflow,
        prompt_profiles,
    )


# Compatibility for existing internal callers while the public service name is adopted.
_validate_xpert_for_publish = preview_xpert_for_publish


class XpertCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, max_length=64)
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    starters: list[str] = Field(default_factory=list, max_length=8)


class XpertUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=20)
    starters: list[str] | None = Field(default=None, max_length=8)
    status: XpertStatus | None = None
    draft: XpertDraft | None = None


class XpertPublishRequest(BaseModel):
    release_notes: str = Field(default="", max_length=2000)


class XpertListResponse(BaseModel):
    version: str = "xpert-definition-v1"
    items: list[XpertSummary]
    total: int


class XpertConversationCreateRequest(BaseModel):
    title: str = Field(default="", max_length=120)


class XpertMemoryCreateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20000)
    scope: MemoryScope = "xpert"
    conversation_id: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=10)
    source_type: str = Field(default="user", max_length=80)
    source_id: str | None = Field(default=None, max_length=200)
    type: Literal["user", "feedback", "project", "reference"] = "project"
    title: str = Field(default="", max_length=120)
    summary: str = Field(default="", max_length=500)
    source_refs: list[str] = Field(default_factory=list, max_length=20)
    confidence: float | None = Field(default=None, ge=0, le=1)


class XpertMemoryUpdateRequest(BaseModel):
    revision: int = Field(ge=1)
    content: str | None = Field(default=None, min_length=1, max_length=20000)
    type: Literal["user", "feedback", "project", "reference"] | None = None
    title: str | None = Field(default=None, max_length=120)
    summary: str | None = Field(default=None, max_length=500)
    tags: list[str] | None = Field(default=None, max_length=20)
    source_refs: list[str] | None = Field(default=None, max_length=20)
    confidence: float | None = Field(default=None, ge=0, le=1)


class XpertMemoryCandidateUpdateRequest(BaseModel):
    revision: int = Field(ge=1)
    content: str | None = Field(default=None, min_length=1, max_length=20000)
    type: Literal["user", "feedback", "project", "reference"] | None = None
    title: str | None = Field(default=None, max_length=120)
    summary: str | None = Field(default=None, max_length=500)
    tags: list[str] | None = Field(default=None, max_length=20)
    action: Literal["create", "update"] | None = None
    target_memory_id: str | None = Field(default=None, max_length=200)
    base_revision: int | None = Field(default=None, ge=1)
    source_refs: list[str] | None = Field(default=None, max_length=20)
    confidence: float | None = Field(default=None, ge=0, le=1)


class XpertMemoryDecisionRequest(BaseModel):
    revision: int | None = Field(default=None, ge=1)


class XpertMemoryWritebackRequest(BaseModel):
    conversation_id: str | None = Field(default=None, max_length=200)
    model_id: str | None = Field(default=None, max_length=300)
    scope: MemoryScope = "xpert"


def configure_memory_writeback_runner(
    runner: Callable[..., Awaitable[list[dict]]] | None,
) -> None:
    global _memory_writeback_runner
    _memory_writeback_runner = runner


def get_xpert_store() -> XpertStore:
    global _xpert_store
    if _xpert_store is None:
        _xpert_store = XpertStore()
    return _xpert_store


def set_xpert_store_for_tests(store: XpertStore | None) -> None:
    global _xpert_store
    _xpert_store = store


def get_xpert_context_store() -> XpertContextStore:
    global _xpert_context_store
    if _xpert_context_store is None:
        _xpert_context_store = XpertContextStore()
    return _xpert_context_store


def set_xpert_context_store_for_tests(store: XpertContextStore | None) -> None:
    global _xpert_context_store
    _xpert_context_store = store


def _raise_store_error(exc: Exception) -> None:
    if isinstance(exc, XpertNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, XpertValidationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if isinstance(exc, XpertConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


def _raise_context_error(exc: Exception) -> None:
    if isinstance(exc, XpertContextNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, XpertContextConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, XpertContextValidationError):
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail=str(exc)) from exc


async def _ensure_xpert_exists(xpert_id: str) -> None:
    try:
        await asyncio.to_thread(get_xpert_store().get_xpert, xpert_id)
    except XpertStoreError as exc:
        _raise_store_error(exc)


@router.get("", response_model=XpertListResponse)
async def list_xperts(
    status: XpertStatus | None = None,
    search: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
) -> XpertListResponse:
    try:
        items = await asyncio.to_thread(
            get_xpert_store().list_xperts,
            status=status,
            search=search,
            limit=limit,
        )
        return XpertListResponse(items=items, total=len(items))
    except XpertStoreError as exc:
        _raise_store_error(exc)


@router.post("", response_model=XpertDefinition)
async def create_xpert(payload: XpertCreateRequest) -> XpertDefinition:
    try:
        return await asyncio.to_thread(
            get_xpert_store().create_xpert,
            name=payload.name,
            slug=payload.slug,
            description=payload.description,
            tags=payload.tags,
            starters=payload.starters,
        )
    except XpertStoreError as exc:
        _raise_store_error(exc)


@router.get("/{xpert_id}", response_model=XpertDefinition)
async def get_xpert(xpert_id: str) -> XpertDefinition:
    try:
        return await asyncio.to_thread(get_xpert_store().get_xpert, xpert_id)
    except XpertStoreError as exc:
        _raise_store_error(exc)


@router.patch("/{xpert_id}", response_model=XpertDefinition)
async def update_xpert(xpert_id: str, payload: XpertUpdateRequest) -> XpertDefinition:
    try:
        return await asyncio.to_thread(
            get_xpert_store().update_xpert,
            xpert_id,
            payload.model_dump(exclude_unset=True, mode="json"),
        )
    except XpertStoreError as exc:
        _raise_store_error(exc)


@router.post("/{xpert_id}/validate", response_model=XpertValidationResult)
async def validate_xpert(xpert_id: str) -> XpertValidationResult:
    try:
        xpert = await asyncio.to_thread(get_xpert_store().get_xpert, xpert_id)
        validation, _, _ = await asyncio.to_thread(
            _validate_xpert_for_publish,
            xpert,
        )
        return validation
    except XpertStoreError as exc:
        _raise_store_error(exc)


@router.post("/{xpert_id}/publish", response_model=XpertVersion)
async def publish_xpert(xpert_id: str, payload: XpertPublishRequest) -> XpertVersion:
    try:
        store = get_xpert_store()
        xpert = await asyncio.to_thread(store.get_xpert, xpert_id)
        validation, workflow, prompt_profiles = await asyncio.to_thread(
            _validate_xpert_for_publish,
            xpert,
        )
        if not validation.valid:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Xpert publish preflight failed.",
                    "issues": [issue.model_dump(mode="json") for issue in validation.issues],
                },
            )
        return await asyncio.to_thread(
            store.publish_xpert,
            xpert_id,
            release_notes=payload.release_notes,
            expected_revision=xpert.draft_revision,
            workflow_override=workflow,
            prompt_profiles_override=prompt_profiles,
        )
    except HTTPException:
        raise
    except XpertStoreError as exc:
        _raise_store_error(exc)


@router.get("/{xpert_id}/versions", response_model=list[XpertVersion])
async def list_xpert_versions(xpert_id: str) -> list[XpertVersion]:
    try:
        return await asyncio.to_thread(get_xpert_store().list_versions, xpert_id)
    except XpertStoreError as exc:
        _raise_store_error(exc)


@router.get("/{xpert_id}/versions/{version}", response_model=XpertVersion)
async def get_xpert_version(xpert_id: str, version: int) -> XpertVersion:
    try:
        return await asyncio.to_thread(get_xpert_store().get_version, xpert_id, version)
    except XpertStoreError as exc:
        _raise_store_error(exc)


@router.post("/{xpert_id}/conversations")
async def create_xpert_conversation(
    xpert_id: str,
    payload: XpertConversationCreateRequest,
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        item = await asyncio.to_thread(
            get_xpert_context_store().create_conversation,
            xpert_id,
            title=payload.title,
        )
        return get_xpert_context_store().conversation_payload(item, include_messages=True)
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.get("/{xpert_id}/conversations")
async def list_xpert_conversations(
    xpert_id: str,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        items = await asyncio.to_thread(store.list_conversations, xpert_id, limit=limit)
        return {
            "version": "xpert-conversation-v1",
            "items": [store.conversation_payload(item, include_messages=False) for item in items],
            "total": len(items),
        }
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.get("/{xpert_id}/conversations/{conversation_id}")
async def get_xpert_conversation(xpert_id: str, conversation_id: str) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        item = await asyncio.to_thread(store.get_conversation, xpert_id, conversation_id)
        return store.conversation_payload(item, include_messages=True)
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.get("/{xpert_id}/conversations/{conversation_id}/tool-memory")
async def list_xpert_conversation_tool_memory(
    xpert_id: str,
    conversation_id: str,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        items = await asyncio.to_thread(
            store.list_tool_memories,
            xpert_id,
            conversation_id,
            limit=limit,
        )
        return {
            "version": "xpert-tool-memory-v1",
            "items": [store.tool_memory_payload(item) for item in items],
            "total": len(items),
        }
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.delete(
    "/{xpert_id}/conversations/{conversation_id}/tool-memory/{memory_id}"
)
async def archive_xpert_conversation_tool_memory(
    xpert_id: str,
    conversation_id: str,
    memory_id: str,
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        item = await asyncio.to_thread(
            store.archive_tool_memory,
            xpert_id,
            conversation_id,
            memory_id,
        )
        return store.tool_memory_payload(item)
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.post("/{xpert_id}/conversations/{conversation_id}/files")
async def upload_xpert_conversation_file(
    xpert_id: str,
    conversation_id: str,
    file: UploadFile = File(...),
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    content = await file.read(MAX_FILE_BYTES + 1)
    try:
        store = get_xpert_context_store()
        item = await asyncio.to_thread(
            store.add_file,
            xpert_id,
            conversation_id,
            filename=file.filename or "",
            content=content,
        )
        return store.file_payload(item)
    except XpertContextError as exc:
        _raise_context_error(exc)
    finally:
        await file.close()


@router.get("/{xpert_id}/conversations/{conversation_id}/files")
async def list_xpert_conversation_files(
    xpert_id: str,
    conversation_id: str,
    include_archived: bool = False,
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        items = await asyncio.to_thread(
            store.list_files,
            xpert_id,
            conversation_id,
            include_archived=include_archived,
        )
        return {"items": [store.file_payload(item) for item in items], "total": len(items)}
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.delete(
    "/{xpert_id}/conversations/{conversation_id}/files/{asset_id}",
    deprecated=True,
)
async def archive_xpert_conversation_file(
    xpert_id: str,
    conversation_id: str,
    asset_id: str,
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        item = await asyncio.to_thread(
            store.archive_file,
            xpert_id,
            conversation_id,
            asset_id,
        )
        return store.file_payload(item)
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.delete(
    "/{xpert_id}/conversations/{conversation_id}/files/{asset_id}/purge"
)
async def purge_xpert_conversation_file(
    xpert_id: str,
    conversation_id: str,
    asset_id: str,
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        item = await asyncio.to_thread(
            store.purge_file,
            xpert_id,
            conversation_id,
            asset_id,
        )
        return {
            "asset_id": item.asset_id,
            "deleted": True,
        }
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.get("/{xpert_id}/memories")
async def list_xpert_memories(
    xpert_id: str,
    scope: str = Query(default="both", pattern="^(conversation|xpert|both)$"),
    conversation_id: str | None = None,
    search: str = Query(default="", max_length=500),
    type: str | None = Query(
        default=None,
        pattern="^(user|feedback|project|reference)$",
    ),
    status: str = Query(default="active", pattern="^(active|archived|conflict)$"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        items = await asyncio.to_thread(
            store.list_memories,
            xpert_id,
            scope=scope,
            conversation_id=conversation_id,
            search=search,
            memory_type=type,
            status=status,
            limit=limit,
        )
        return {"items": [store.memory_payload(item) for item in items], "total": len(items)}
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.post("/{xpert_id}/memories")
async def create_xpert_memory(xpert_id: str, payload: XpertMemoryCreateRequest) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        item = await asyncio.to_thread(
            store.create_memory,
            xpert_id,
            content=payload.content,
            scope=payload.scope,
            conversation_id=payload.conversation_id,
            tags=payload.tags,
            source_type=payload.source_type,
            source_id=payload.source_id,
            memory_type=payload.type,
            title=payload.title,
            summary=payload.summary,
            source_refs=payload.source_refs,
            confidence=payload.confidence,
        )
        return store.memory_payload(item)
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.delete("/{xpert_id}/memories/{memory_id}")
async def archive_xpert_memory(
    xpert_id: str,
    memory_id: str,
    revision: int | None = Query(default=None, ge=1),
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        item = await asyncio.to_thread(
            store.archive_memory,
            xpert_id,
            memory_id,
            revision=revision,
        )
        return store.memory_payload(item)
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.get("/{xpert_id}/memory-candidates")
async def list_xpert_memory_candidates(
    xpert_id: str,
    status: CandidateStatus | None = None,
    conversation_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        items = await asyncio.to_thread(
            store.list_candidates,
            xpert_id,
            status=status,
            conversation_id=conversation_id,
            limit=limit,
        )
        return {"items": [store.candidate_payload(item) for item in items], "total": len(items)}
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.post("/{xpert_id}/memory-candidates/{candidate_id}/approve")
async def approve_xpert_memory_candidate(
    xpert_id: str,
    candidate_id: str,
    payload: XpertMemoryDecisionRequest | None = None,
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        item = await asyncio.to_thread(
            store.decide_candidate,
            xpert_id,
            candidate_id,
            approve=True,
            revision=payload.revision if payload else None,
        )
        return store.candidate_payload(item)
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.post("/{xpert_id}/memory-candidates/{candidate_id}/reject")
async def reject_xpert_memory_candidate(
    xpert_id: str,
    candidate_id: str,
    payload: XpertMemoryDecisionRequest | None = None,
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        item = await asyncio.to_thread(
            store.decide_candidate,
            xpert_id,
            candidate_id,
            approve=False,
            revision=payload.revision if payload else None,
        )
        return store.candidate_payload(item)
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.patch("/{xpert_id}/memory-candidates/{candidate_id}")
async def update_xpert_memory_candidate(
    xpert_id: str,
    candidate_id: str,
    payload: XpertMemoryCandidateUpdateRequest,
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        item = await asyncio.to_thread(
            store.update_candidate,
            xpert_id,
            candidate_id,
            revision=payload.revision,
            content=payload.content,
            memory_type=payload.type,
            title=payload.title,
            summary=payload.summary,
            tags=payload.tags,
            action=payload.action,
            target_memory_id=payload.target_memory_id,
            base_revision=payload.base_revision,
            source_refs=payload.source_refs,
            confidence=payload.confidence,
        )
        return store.candidate_payload(item)
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.get("/{xpert_id}/file-memory/index")
async def get_xpert_file_memory_index(xpert_id: str) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        return await asyncio.to_thread(
            get_xpert_context_store().file_memory_index,
            xpert_id,
        )
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.get("/{xpert_id}/file-memory/signals")
async def list_xpert_file_memory_signals(
    xpert_id: str,
    memory_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        items = await asyncio.to_thread(
            get_xpert_context_store().file_memory_signals,
            xpert_id,
            memory_id=memory_id,
            limit=limit,
        )
        return {"items": items, "total": len(items)}
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.get("/{xpert_id}/file-memory/{memory_id}")
async def get_xpert_file_memory(xpert_id: str, memory_id: str) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        item = await asyncio.to_thread(
            store.get_memory,
            xpert_id,
            memory_id,
            record_detail_read=True,
        )
        if item.scope != "xpert":
            raise XpertContextNotFoundError("Xpert file memory not found.")
        return store.memory_payload(item)
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.patch("/{xpert_id}/file-memory/{memory_id}")
async def update_xpert_file_memory(
    xpert_id: str,
    memory_id: str,
    payload: XpertMemoryUpdateRequest,
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        item = await asyncio.to_thread(
            store.update_memory,
            xpert_id,
            memory_id,
            revision=payload.revision,
            content=payload.content,
            memory_type=payload.type,
            title=payload.title,
            summary=payload.summary,
            tags=payload.tags,
            source_refs=payload.source_refs,
            confidence=payload.confidence,
        )
        if item.scope != "xpert":
            raise XpertContextNotFoundError("Xpert file memory not found.")
        return store.memory_payload(item)
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.delete("/{xpert_id}/file-memory/{memory_id}")
async def archive_xpert_file_memory(
    xpert_id: str,
    memory_id: str,
    revision: int = Query(ge=1),
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    try:
        store = get_xpert_context_store()
        item = await asyncio.to_thread(
            store.archive_memory,
            xpert_id,
            memory_id,
            revision=revision,
        )
        return store.memory_payload(item)
    except XpertContextError as exc:
        _raise_context_error(exc)


@router.post("/{xpert_id}/file-memory/writeback")
async def run_xpert_file_memory_writeback(
    xpert_id: str,
    payload: XpertMemoryWritebackRequest,
) -> dict:
    await _ensure_xpert_exists(xpert_id)
    if _memory_writeback_runner is None:
        raise HTTPException(status_code=503, detail="Memory writeback runner is unavailable.")
    try:
        items = await _memory_writeback_runner(
            xpert_id=xpert_id,
            conversation_id=payload.conversation_id,
            model_id=payload.model_id,
            scope=payload.scope,
        )
        return {"items": items, "total": len(items)}
    except XpertContextError as exc:
        _raise_context_error(exc)
