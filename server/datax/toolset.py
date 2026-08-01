from __future__ import annotations

import json
from typing import Any

try:
    from server.xpert_runtime.capabilities import CapabilityRegistry
    from server.xpert_runtime.toolset import (
        RuntimeTool,
        RuntimeToolCall,
        RuntimeToolError,
        RuntimeToolResult,
    )
except ModuleNotFoundError:
    from xpert_runtime.capabilities import CapabilityRegistry
    from xpert_runtime.toolset import (
        RuntimeTool,
        RuntimeToolCall,
        RuntimeToolError,
        RuntimeToolResult,
    )

from .service import DataXService


READ_TOOLS = {
    "datax_scope",
    "datax_model_context",
    "datax_dimension_members",
    "datax_indicator_list",
    "datax_indicator_search",
    "datax_indicator_get",
    "datax_indicator_query",
    "datax_show_result",
}
WRITE_TOOLS = {"datax_indicator_propose_create", "datax_indicator_propose_update"}


class DataXToolsetProvider:
    def __init__(self, service: DataXService) -> None:
        self.service = service

    async def list_tools(self) -> list[RuntimeTool]:
        return [
            _tool("datax_scope", "List the Data X projects and semantic models available to this agent.", {}),
            _tool("datax_model_context", "Read a scoped semantic model and its available fields.", {"model_id": {"type": "string"}}, ["model_id"]),
            _tool("datax_dimension_members", "List bounded members of one semantic dimension.", {"model_id": {"type": "string"}, "field": {"type": "string"}, "search": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, ["model_id", "field"]),
            _tool("datax_indicator_list", "List published indicators in the configured scope.", {"project_id": {"type": "string"}}),
            _tool("datax_indicator_search", "Search published indicators by business language.", {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}, ["query"]),
            _tool("datax_indicator_get", "Read one published indicator definition.", {"indicator_id": {"type": "string"}}, ["indicator_id"]),
            _tool(
                "datax_indicator_query",
                "Query published indicators through the safe Data X DSL; raw SQL is never accepted.",
                {
                    "project_id": {"type": "string"},
                    "model_id": {"type": "string"},
                    "indicators": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
                    "dimensions": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                    "filters": {"type": "array", "items": {"type": "object"}, "maxItems": 20},
                    "time_range": {"type": "object"},
                    "order_by": {"type": "array", "items": {"type": "object"}, "maxItems": 10},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    "view": {"type": "string", "enum": ["kpi", "table", "line", "bar"]},
                },
                ["project_id", "model_id", "indicators"],
            ),
            _tool("datax_show_result", "Return a safe KPI, table, line, or bar result artifact for display.", {"artifact_id": {"type": "string"}}, ["artifact_id"]),
            _tool("datax_indicator_propose_create", "Create a draft indicator proposal for human approval; never publishes it.", {"project_id": {"type": "string"}, "model_id": {"type": "string"}, "title": {"type": "string"}, "indicator": {"type": "object"}}, ["project_id", "model_id", "title", "indicator"]),
            _tool("datax_indicator_propose_update", "Propose changes to an indicator; approval only writes a draft.", {"project_id": {"type": "string"}, "model_id": {"type": "string"}, "indicator_id": {"type": "string"}, "title": {"type": "string"}, "patch": {"type": "object"}}, ["project_id", "model_id", "indicator_id", "title", "patch"]),
        ]

    async def find_tool(self, tool_name: str) -> RuntimeTool | None:
        return next((tool for tool in await self.list_tools() if tool.name == tool_name), None)

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        projects, models = self._scope(call)
        if call.tool_name in WRITE_TOOLS:
            if call.metadata.get("run_type") == "xpert_app":
                raise RuntimeToolError(call.tool_name, "Public Xpert Apps cannot create Data X proposals.", code="datax_app_write_forbidden")
            if not bool(call.metadata.get("datax_allow_proposals")):
                raise RuntimeToolError(call.tool_name, "Data X proposals are disabled for this agent.", code="datax_proposals_disabled")
        try:
            if call.tool_name == "datax_scope":
                payload = {
                    "projects": [self._project_summary(project_id) for project_id in projects],
                    "models": [self._model_summary(model_id) for model_id in models],
                    "proposal_writes": bool(call.metadata.get("datax_allow_proposals")) and call.metadata.get("run_type") != "xpert_app",
                }
            elif call.tool_name == "datax_model_context":
                model_id = self._model(call, models)
                payload = self._model_summary(model_id, include_fields=True)
            elif call.tool_name == "datax_dimension_members":
                model_id = self._model(call, models)
                payload = {
                    "model_id": model_id,
                    "field": str(call.arguments.get("field") or ""),
                    "members": self.service.dimension_members(
                        model_id,
                        str(call.arguments.get("field") or ""),
                        search=str(call.arguments.get("search") or ""),
                        limit=int(call.arguments.get("limit") or 50),
                    ),
                }
            elif call.tool_name == "datax_indicator_list":
                requested = str(call.arguments.get("project_id") or "").strip()
                targets = [self._project(requested, projects)] if requested else projects
                payload = {
                    "items": [
                        _indicator_summary(item)
                        for project_id in targets
                        for item in self.service.list_published_indicators(project_id)
                        if item.model_id in models
                    ]
                }
            elif call.tool_name == "datax_indicator_search":
                payload = self.service.search_indicators(
                    str(call.arguments.get("query") or ""),
                    project_ids=projects,
                    limit=int(call.arguments.get("limit") or 20),
                )
                payload["items"] = [item for item in payload["items"] if item.get("model_id") in models]
            elif call.tool_name == "datax_indicator_get":
                item = self.service.get_published_indicator(
                    str(call.arguments.get("indicator_id") or "")
                )
                self._assert_indicator(item, projects, models)
                payload = _indicator_summary(item, include_definition=True)
            elif call.tool_name == "datax_indicator_query":
                project_id = self._project(str(call.arguments.get("project_id") or ""), projects)
                model_id = self._model(call, models)
                if self.service.get_model(model_id).project_id != project_id:
                    raise ValueError("The selected model is outside the selected project.")
                max_rows = max(1, min(int(call.metadata.get("datax_max_result_rows") or 100), 500))
                artifact = self.service.query(
                    project_id=project_id,
                    model_id=model_id,
                    indicator_codes=[str(item) for item in call.arguments.get("indicators") or []],
                    dimensions=[str(item) for item in call.arguments.get("dimensions") or []],
                    filters=list(call.arguments.get("filters") or []),
                    time_range=call.arguments.get("time_range") if isinstance(call.arguments.get("time_range"), dict) else None,
                    order_by=list(call.arguments.get("order_by") or []),
                    limit=min(int(call.arguments.get("limit") or max_rows), max_rows),
                    view=str(call.arguments.get("view") or "table"),
                )
                payload = artifact.model_dump(mode="json")
            elif call.tool_name == "datax_show_result":
                from .models import DataXResultArtifact

                artifact = self.service.store.require(
                    "artifacts", str(call.arguments.get("artifact_id") or ""), DataXResultArtifact
                )
                if artifact.project_id not in projects or artifact.model_id not in models:
                    raise PermissionError("Data X result is outside this agent's scope.")
                payload = artifact.model_dump(mode="json")
            elif call.tool_name in WRITE_TOOLS:
                project_id = self._project(str(call.arguments.get("project_id") or ""), projects)
                model_id = self._model(call, models)
                proposal = self.service.create_proposal(
                    project_id=project_id,
                    model_id=model_id,
                    indicator_id=(str(call.arguments.get("indicator_id") or "").strip() or None),
                    title=str(call.arguments.get("title") or "Indicator proposal"),
                    payload=dict(call.arguments.get("indicator") or call.arguments.get("patch") or {}),
                    source_xpert_id=_optional(call.metadata.get("xpert_id")),
                    source_run_id=_optional(call.metadata.get("run_id")),
                    source_goal_id=_optional(call.metadata.get("goal_id")),
                    source_handoff_id=_optional(call.metadata.get("handoff_id")),
                )
                payload = {
                    "proposal_id": proposal.proposal_id,
                    "status": proposal.status,
                    "revision": proposal.revision,
                    "approval_required": True,
                    "publishing_required_after_approval": True,
                }
            else:
                raise RuntimeToolError(call.tool_name, "Data X tool not found.", code="tool_not_found")
        except RuntimeToolError:
            raise
        except Exception as exc:
            raise RuntimeToolError(call.tool_name, str(exc), code="datax_tool_error") from exc
        output = json.dumps(payload, ensure_ascii=False)
        return RuntimeToolResult(
            output=output,
            metadata={
                "content_types": ["text", "datax_result"] if call.tool_name in {"datax_indicator_query", "datax_show_result"} else ["text"],
                "datax_tool": call.tool_name,
                "output_length": len(output),
                "proposal": call.tool_name in WRITE_TOOLS,
            },
        )

    def _scope(self, call: RuntimeToolCall) -> tuple[list[str], list[str]]:
        projects = _list_metadata(call.metadata.get("datax_project_ids"), 10)
        models = _list_metadata(call.metadata.get("datax_model_ids"), 20)
        if not projects or not models:
            raise RuntimeToolError(call.tool_name, "Data X tools require explicit project and model scopes.", code="datax_scope_missing")
        for model_id in models:
            model = self.service.get_model(model_id)
            if model.project_id not in projects:
                raise RuntimeToolError(call.tool_name, "A configured Data X model is outside the project scope.", code="datax_scope_invalid")
        return projects, models

    def _project(self, project_id: str, allowed: list[str]) -> str:
        if project_id not in allowed:
            raise PermissionError("Data X project is outside this agent's configured scope.")
        self.service.get_project(project_id)
        return project_id

    def _model(self, call: RuntimeToolCall, allowed: list[str]) -> str:
        model_id = str(call.arguments.get("model_id") or "").strip()
        if model_id not in allowed:
            raise PermissionError("Data X model is outside this agent's configured scope.")
        self.service.get_model(model_id)
        return model_id

    def _assert_indicator(self, item: Any, projects: list[str], models: list[str]) -> None:
        if item.project_id not in projects or item.model_id not in models:
            raise PermissionError("Data X indicator is outside this agent's configured scope.")

    def _project_summary(self, project_id: str) -> dict[str, Any]:
        item = self.service.get_project(project_id)
        return {"project_id": item.project_id, "name": item.name, "status": item.status}

    def _model_summary(self, model_id: str, *, include_fields: bool = False) -> dict[str, Any]:
        item = self.service.get_model(model_id)
        result: dict[str, Any] = {
            "model_id": item.model_id,
            "project_id": item.project_id,
            "name": item.name,
            "description": item.description,
            "entity_count": len(item.entities),
            "field_count": len(item.fields),
        }
        if include_fields:
            result["fields"] = [
                {"name": field.name, "label": field.label, "data_type": field.data_type, "role": field.role}
                for field in item.fields
                if field.role != "hidden"
            ]
        return result


def register_datax_toolset_capability(
    capability_registry: CapabilityRegistry, provider: DataXToolsetProvider
) -> None:
    capability_registry.register(
        "datax_tools",
        provider,
        description="Scoped semantic indicators, safe analytics, and approval-gated metric proposals.",
        metadata={"provider": "datax"},
    )


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str] | None = None) -> RuntimeTool:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    write_tool = name in {
        "datax_indicator_propose_create",
        "datax_indicator_propose_update",
    }
    return RuntimeTool(
        name=name,
        description=description,
        input_schema=schema,
        provider="datax",
        read_only=not write_tool,
        memory_mode="run",
        parallel_safe=not write_tool,
    )


def _list_metadata(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))[:limit]


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _indicator_summary(item: Any, *, include_definition: bool = False) -> dict[str, Any]:
    result = {
        "indicator_id": item.indicator_id,
        "project_id": item.project_id,
        "model_id": item.model_id,
        "code": item.code,
        "name": item.name,
        "description": item.description,
        "indicator_type": item.indicator_type,
        "version": item.current_version,
        "tags": item.tags,
    }
    if include_definition:
        result.update(
            {
                "aggregation": item.aggregation,
                "measure_field": item.measure_field,
                "formula": item.formula,
                "default_dimensions": item.default_dimensions,
                "time_field": item.time_field,
                "filters": [condition.model_dump(mode="json") for condition in item.filters],
            }
        )
    return result
