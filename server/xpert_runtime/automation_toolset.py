from __future__ import annotations

import json
from typing import Any

from .automation_coordinator import AutomationCoordinator
from .automation_store import AutomationStore
from .capabilities import CapabilityRegistry
from .toolset import RuntimeTool, RuntimeToolCall, RuntimeToolError, RuntimeToolResult


AUTOMATION_TOOL_NAMES = {
    "automation_create",
    "automation_list",
    "automation_get",
    "automation_pause",
    "automation_resume",
    "automation_run_now",
    "automation_archive",
}


class AutomationToolsetProvider:
    """Tools that let a private published Xpert manage its own schedules."""

    def __init__(self, store: AutomationStore, coordinator: AutomationCoordinator) -> None:
        self.store = store
        self.coordinator = coordinator

    async def list_tools(self) -> list[RuntimeTool]:
        trigger = {
            "type": "object",
            "required": ["type"],
            "properties": {
                "type": {"type": "string", "enum": ["once", "interval", "cron"]},
                "once_at": {"type": "number"},
                "interval_seconds": {"type": "integer", "minimum": 30},
                "cron": {"type": "string"},
                "timezone": {"type": "string"},
            },
            "additionalProperties": False,
        }
        return [
            RuntimeTool(
                "automation_create",
                "Create a durable schedule pinned to the current published Xpert version.",
                {
                    "type": "object",
                    "required": ["name", "prompt", "trigger"],
                    "properties": {
                        "name": {"type": "string", "maxLength": 200},
                        "prompt": {"type": "string", "maxLength": 20000},
                        "trigger": trigger,
                        "overlap_policy": {"type": "string", "enum": ["skip", "allow"]},
                        "misfire_policy": {"type": "string", "enum": ["skip", "latest", "catch_up"]},
                    },
                    "additionalProperties": False,
                },
                "automation",
            ),
            RuntimeTool("automation_list", "List schedules owned by the current Xpert.", {"type": "object", "properties": {}}, "automation"),
            RuntimeTool("automation_get", "Read one schedule and its recent executions.", {"type": "object", "required": ["automation_id"], "properties": {"automation_id": {"type": "string"}}}, "automation"),
            *[
                RuntimeTool(
                    f"automation_{action}",
                    f"{action.replace('_', ' ').title()} a schedule owned by the current Xpert.",
                    {"type": "object", "required": ["automation_id"], "properties": {"automation_id": {"type": "string"}}},
                    "automation",
                )
                for action in ("pause", "resume", "run_now", "archive")
            ],
        ]

    async def find_tool(self, tool_name: str) -> RuntimeTool | None:
        return next((item for item in await self.list_tools() if item.name == tool_name), None)

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        if str(call.metadata.get("runtime_run_type") or "") == "xpert_app":
            raise RuntimeToolError(call.tool_name, "Public Xpert Apps cannot manage automations.", code="automation_app_denied")
        xpert_id = str(call.metadata.get("xpert_id") or "").strip()
        xpert_slug = str(call.metadata.get("xpert_slug") or "").strip()
        xpert_version = int(call.metadata.get("xpert_version") or 0)
        if not xpert_id or xpert_version < 1:
            raise RuntimeToolError(call.tool_name, "Automation tools require a published Xpert version.", code="automation_target_missing")
        arguments = dict(call.arguments or {})
        automation_config = dict(call.metadata.get("automation_config") or {})
        try:
            if call.tool_name == "automation_create":
                allow_create = str(
                    automation_config.get("allow_agent_create", True)
                ).strip().lower() in {"1", "true", "yes", "on"}
                if not allow_create:
                    raise RuntimeToolError(
                        call.tool_name,
                        "This Xpert is not allowed to create automations.",
                        code="automation_create_denied",
                    )
                trigger_payload = dict(arguments.get("trigger") or {})
                trigger_payload.setdefault(
                    "timezone",
                    str(automation_config.get("default_timezone") or "UTC"),
                )
                item = self.store.create_definition(
                    name=str(arguments.get("name") or ""),
                    prompt=str(arguments.get("prompt") or ""),
                    target_xpert_id=xpert_id,
                    target_xpert_slug=xpert_slug or xpert_id,
                    target_xpert_version=xpert_version,
                    trigger=trigger_payload,
                    status="scheduled",
                    overlap_policy=str(arguments.get("overlap_policy") or "skip"),
                    misfire_policy=str(arguments.get("misfire_policy") or "latest"),
                    budget={
                        "max_runs_per_day": max(
                            1,
                            min(
                                int(
                                    automation_config.get("max_runs_per_day")
                                    or 100
                                ),
                                1000,
                            ),
                        )
                    },
                    metadata={"created_via": "scheduler_middleware", "source_run_id": call.metadata.get("run_id")},
                )
                self.coordinator.wake()
                payload: Any = AutomationStore.serialize_definition(item)
            elif call.tool_name == "automation_list":
                payload = [
                    AutomationStore.serialize_definition(item, include_prompt=False)
                    for item in self.store.list_definitions(limit=100)
                    if item.target_xpert_id == xpert_id
                ]
            else:
                automation_id = str(arguments.get("automation_id") or "").strip()
                item = self.store.require_definition(automation_id)
                if item.target_xpert_id != xpert_id:
                    raise RuntimeToolError(call.tool_name, "Automation belongs to another Xpert.", code="automation_scope_denied")
                if call.tool_name == "automation_get":
                    payload = {
                        **AutomationStore.serialize_definition(item),
                        "executions": [
                            AutomationStore.serialize_execution(value)
                            for value in self.store.list_executions(automation_id=automation_id, limit=20)
                        ],
                    }
                elif call.tool_name == "automation_pause":
                    payload = AutomationStore.serialize_definition(self.store.set_status(automation_id, "paused"))
                elif call.tool_name == "automation_resume":
                    payload = AutomationStore.serialize_definition(self.store.set_status(automation_id, "scheduled"))
                    self.coordinator.wake()
                elif call.tool_name == "automation_run_now":
                    payload = AutomationStore.serialize_execution(self.store.run_now(automation_id))
                    self.coordinator.wake()
                elif call.tool_name == "automation_archive":
                    payload = AutomationStore.serialize_definition(self.store.set_status(automation_id, "archived"))
                else:
                    raise RuntimeToolError(call.tool_name, "Automation tool not found.", code="tool_not_found")
        except RuntimeToolError:
            raise
        except Exception as exc:
            raise RuntimeToolError(call.tool_name, str(exc)[:500], code="automation_error") from exc
        output = json.dumps(payload, ensure_ascii=False)
        return RuntimeToolResult(output=output, metadata={"content_types": ["text"], "xpert_id": xpert_id})


def register_automation_toolset_capability(
    registry: CapabilityRegistry,
    provider: AutomationToolsetProvider,
) -> None:
    registry.register(
        "automation_tools",
        provider,
        description="Durable private Xpert scheduler tools.",
        metadata={"provider": "automation", "app_forbidden": True},
    )
