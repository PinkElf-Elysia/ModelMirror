from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from typing import Any, Iterable

from .node_adapters import (
    META_PLANNER_COMPILABLE_NODE_KINDS,
    planner_capability_metadata,
)
from .graph_patch import (
    GRAPH_PATCH_MAX_JSON_DEPTH,
    GRAPH_PATCH_MAX_OPERATIONS,
    GRAPH_PATCH_MAX_REQUEST_BYTES,
    GRAPH_PATCH_PROTOCOL_VERSION,
    graph_patch_schema,
)
from .schemas import MetaPlannerCapabilitySnapshot, MetaPlannerScope


HIGH_RISK_SECURITY_CATEGORIES = {
    "authoring",
    "automation",
    "browser",
    "client",
    "client_tools",
    "code_execution",
    "sandbox",
}
HIGH_RISK_MIDDLEWARE_IDS = {
    "automation_scheduler",
    "browser_automation",
    "client_tools",
    "office_automation",
    "sandbox_files",
    "sandbox_shell",
    "skill_creator",
    "skills_runtime",
    "xpert_authoring",
}


def _canonical_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_resource(
    *,
    resource_id: str,
    name: str,
    description: str = "",
    status: str,
    version: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": str(resource_id),
        "name": str(name)[:160],
        "description": str(description)[:600],
        "status": str(status)[:40],
        "published_version": int(version) if version else None,
        "metadata": dict(metadata or {}),
    }


def _immutable_version_catalog(resource: Any) -> list[dict[str, Any]]:
    """Project immutable version identity without exposing version contents."""

    catalog: list[dict[str, Any]] = []
    for version in list(getattr(resource, "versions", None) or []):
        if hasattr(version, "model_dump"):
            payload = version.model_dump(mode="json")
        elif isinstance(version, dict):
            payload = dict(version)
        else:
            payload = dict(vars(version))
        try:
            version_number = int(payload.get("version"))
        except (TypeError, ValueError):
            continue
        checksum = str(
            payload.get("checksum")
            or payload.get("schema_hash")
            or payload.get("package_checksum")
            or _canonical_hash(payload)
        )
        catalog.append({"version": version_number, "checksum": checksum})
    return sorted(catalog, key=lambda item: int(item["version"]))


def build_capability_snapshot(
    *,
    workflow_registry: Any,
    middleware_registry: Any,
    external_xperts: Iterable[Any],
    knowledge_bases: Iterable[dict[str, Any]],
    toolsets: Iterable[Any],
    plugins: Iterable[Any],
    prompt_profiles: Iterable[Any],
    model_ids: Iterable[str],
    agents: Iterable[Any] = (),
) -> MetaPlannerCapabilitySnapshot:
    node_payload = workflow_registry.to_payload()
    nodes: list[dict[str, Any]] = []
    for section in node_payload.get("sections", []):
        for item in section.get("items", []):
            planner = dict(item.get("planner") or {})
            contract = dict(item.get("contract") or {})
            compiler = planner_capability_metadata(str(item.get("kind") or ""))
            if (
                not item.get("enabled")
                or not planner.get("enabled")
                or compiler is None
                or contract.get("contract_status") != "complete"
                or contract.get("checksum") != compiler["contract_checksum"]
                or contract.get("compiler_checksum")
                != compiler["compiler_checksum"]
            ):
                continue
            nodes.append(
                {
                    "kind": item["kind"],
                    "title": item.get("title", item["kind"]),
                    "description": item.get("description", "")[:600],
                    "category": item.get("category", ""),
                    "tags": list(item.get("tags") or [])[:12],
                    "planner": {
                        "enabled": True,
                        "support": compiler["support"],
                        "compilable": True,
                        "ir_version": compiler["ir_version"],
                        "adapter_version": compiler["adapter_version"],
                        "task_binding": compiler["task_binding"],
                        "contract_version": compiler["contract_version"],
                        "contract_checksum": compiler["contract_checksum"],
                        "compiler_checksum": compiler["compiler_checksum"],
                        "adapter_checksum": compiler["adapter_checksum"],
                        "authoring_checksum": compiler["authoring_checksum"],
                        "default_data": dict(planner.get("default_data") or {}),
                        "config_constraints": dict(
                            planner.get("config_constraints") or {}
                        ),
                    },
                    "contracts": dict(item.get("contracts") or {}),
                    "contract": contract,
                }
            )
    for item in node_payload.get("knowledge_pipeline", {}).get("items", []):
        planner = dict(item.get("planner") or {})
        contract = dict(item.get("contract") or {})
        compiler = planner_capability_metadata(str(item.get("kind") or ""))
        if (
            item.get("enabled")
            and planner.get("enabled")
            and compiler is not None
            and contract.get("contract_status") == "complete"
            and contract.get("checksum") == compiler["contract_checksum"]
            and contract.get("compiler_checksum")
            == compiler["compiler_checksum"]
        ):
            nodes.append(
                {
                    "kind": item["kind"],
                    "title": item.get("title", item["kind"]),
                    "description": item.get("description", "")[:600],
                    "category": item.get("category", "knowledge"),
                    "tags": list(item.get("tags") or [])[:12],
                    "planner": {
                        "enabled": True,
                        "support": compiler["support"],
                        "compilable": True,
                        "ir_version": compiler["ir_version"],
                        "adapter_version": compiler["adapter_version"],
                        "task_binding": compiler["task_binding"],
                        "contract_version": compiler["contract_version"],
                        "contract_checksum": compiler["contract_checksum"],
                        "compiler_checksum": compiler["compiler_checksum"],
                        "adapter_checksum": compiler["adapter_checksum"],
                        "authoring_checksum": compiler["authoring_checksum"],
                        "default_data": dict(planner.get("default_data") or {}),
                        "config_constraints": dict(
                            planner.get("config_constraints") or {}
                        ),
                    },
                    "contracts": dict(item.get("contracts") or {}),
                    "contract": contract,
                }
            )
    nodes.sort(key=lambda item: item["kind"])

    middleware: list[dict[str, Any]] = []
    safe_middleware_ids: list[str] = []
    for node in middleware_registry.list():
        if not node.enabled or node.execution_status != "real":
            continue
        fields = []
        defaults: dict[str, Any] = {}
        for field in node.fields:
            field_payload = asdict(field)
            fields.append(field_payload)
            if field.default is not None:
                defaults[field.name] = field.default
        high_risk = (
            node.app_policy == "forbidden"
            or node.security_category in HIGH_RISK_SECURITY_CATEGORIES
            or node.id in HIGH_RISK_MIDDLEWARE_IDS
            or bool(node.metadata.get("app_forbidden"))
        )
        if not high_risk:
            safe_middleware_ids.append(node.id)
        middleware.append(
            {
                "id": node.id,
                "kind": node.kind,
                "title": node.title,
                "description": node.description[:600],
                "category": node.category,
                "config_version": node.config_version,
                "requires_tool_mode": node.requires_tool_mode,
                "app_policy": node.app_policy,
                "security_category": node.security_category,
                "high_risk": high_risk,
                "fields": fields,
                "default_config": defaults,
            }
        )
    middleware.sort(key=lambda item: item["id"])

    xperts = [
        _safe_resource(
            resource_id=item.id,
            name=item.name,
            description=item.description,
            status=item.status,
            version=item.published_version,
            metadata={
                "slug": item.slug,
                "available_versions": _immutable_version_catalog(item),
            },
        )
        for item in external_xperts
        if item.status == "published" and item.published_version
    ]
    kbs = [
        _safe_resource(
            resource_id=str(item.get("id") or item.get("kb_id") or ""),
            name=str(item.get("name") or item.get("title") or "Knowledge base"),
            description=str(item.get("description") or ""),
            status=(
                "active"
                if item.get("active_version_id") or item.get("active_version")
                else "no_active_index"
            ),
            metadata={
                "active_version_id": item.get("active_version_id")
                or item.get("active_version"),
            },
        )
        for item in knowledge_bases
        if item.get("id") or item.get("kb_id")
    ]
    safe_toolsets = [
        _safe_resource(
            resource_id=item.id,
            name=item.name,
            description=item.description,
            status=item.status,
            version=item.published_version,
            metadata={
                "kind": item.kind,
                "available_versions": _immutable_version_catalog(item),
            },
        )
        for item in toolsets
        if item.status == "published" and item.published_version
    ]
    safe_plugins = [
        _safe_resource(
            resource_id=item.id,
            name=item.name,
            description=item.description,
            status=item.status,
            version=item.published_version,
            metadata={
                "slug": item.slug,
                "available_versions": _immutable_version_catalog(item),
            },
        )
        for item in plugins
        if item.status == "published" and item.published_version
    ]
    prompts = [
        _safe_resource(
            resource_id=item.id,
            name=item.name,
            description=item.description,
            status=item.status,
            version=item.published_version,
            metadata={
                "slug": item.slug,
                "aliases": list(item.aliases)[:5],
                "available_versions": _immutable_version_catalog(item),
            },
        )
        for item in prompt_profiles
        if item.status == "published" and item.published_version
    ]
    models = [
        {"id": model_id, "label": model_id, "safe": True}
        for model_id in sorted({str(value).strip() for value in model_ids if value})
    ]
    expert_summaries: list[dict[str, Any]] = []
    seen_agent_ids: set[str] = set()
    for item in agents:
        if isinstance(item, dict):
            agent_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or agent_id).strip()
            department = str(item.get("department") or "未分类").strip()
            description = str(
                item.get("expertise") or item.get("description") or ""
            ).strip()
        else:
            agent_id = str(getattr(item, "id", "") or "").strip()
            name = str(getattr(item, "name", agent_id) or agent_id).strip()
            department = str(
                getattr(item, "department", "未分类") or "未分类"
            ).strip()
            description = str(
                getattr(item, "expertise", "")
                or getattr(item, "description", "")
                or ""
            ).strip()
        if not agent_id or agent_id in seen_agent_ids:
            continue
        seen_agent_ids.add(agent_id)
        expert_summaries.append(
            {
                "id": agent_id[:160],
                "name": name[:200],
                "department": department[:120],
                "description": description[:600],
            }
        )
    expert_summaries.sort(key=lambda item: item["id"])

    core_node_kinds = set(META_PLANNER_COMPILABLE_NODE_KINDS)
    available_node_kinds = {item["kind"] for item in nodes}
    default_scope = MetaPlannerScope(
        allowed_node_kinds=sorted(core_node_kinds & available_node_kinds),
        external_xpert_ids=[item["id"] for item in xperts],
        knowledge_base_ids=[item["id"] for item in kbs],
        toolset_ids=[item["id"] for item in safe_toolsets],
        plugin_ids=[item["id"] for item in safe_plugins],
        prompt_profile_ids=[item["id"] for item in prompts],
        middleware_ids=sorted(safe_middleware_ids),
        agent_ids=[item["id"] for item in expert_summaries],
    )
    payload = {
        "version": "evoagentx-meta-planner-capabilities-v6",
        "ir_version": 3,
        "supported_ir_versions": [2, 3],
        "contract_version": int(node_payload.get("contract_version") or 0),
        "contract_checksum": str(node_payload.get("contract_checksum") or ""),
        "node_registry_version": workflow_registry.version,
        "nodes": nodes,
        "middleware": middleware,
        "external_xperts": xperts,
        "knowledge_bases": kbs,
        "toolsets": safe_toolsets,
        "plugins": safe_plugins,
        "prompt_profiles": prompts,
        "models": models,
        "agents": expert_summaries,
        "default_scope": default_scope.model_dump(mode="json"),
        "authoring_protocol_version": GRAPH_PATCH_PROTOCOL_VERSION,
        "authoring_operation_schema": graph_patch_schema(),
        "authoring_adapter_checksums": {
            item["kind"]: str(item["planner"]["authoring_checksum"])
            for item in nodes
        },
        "authoring_limits": {
            "max_operations": GRAPH_PATCH_MAX_OPERATIONS,
            "max_receipts": 20,
            "max_request_bytes": GRAPH_PATCH_MAX_REQUEST_BYTES,
            "max_json_depth": GRAPH_PATCH_MAX_JSON_DEPTH,
        },
    }
    return MetaPlannerCapabilitySnapshot(
        **payload,
        snapshot_hash=_canonical_hash(payload),
        generated_at=time.time(),
    )


def assert_scope_is_authorized(
    scope: MetaPlannerScope,
    snapshot: MetaPlannerCapabilitySnapshot,
) -> None:
    allowed = {
        "allowed_node_kinds": {item["kind"] for item in snapshot.nodes},
        "external_xpert_ids": {item["id"] for item in snapshot.external_xperts},
        "knowledge_base_ids": {item["id"] for item in snapshot.knowledge_bases},
        "toolset_ids": {item["id"] for item in snapshot.toolsets},
        "plugin_ids": {item["id"] for item in snapshot.plugins},
        "prompt_profile_ids": {item["id"] for item in snapshot.prompt_profiles},
        "middleware_ids": {item["id"] for item in snapshot.middleware},
        "agent_ids": {item["id"] for item in snapshot.agents},
    }
    for field_name, known_values in allowed.items():
        requested = set(getattr(scope, field_name))
        unknown = sorted(requested - known_values)
        if unknown:
            raise ValueError(
                f"Meta Planner scope contains unavailable {field_name}: "
                + ", ".join(unknown)
            )
