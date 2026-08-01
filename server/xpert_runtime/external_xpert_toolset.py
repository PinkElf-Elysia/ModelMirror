from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .capabilities import CapabilityRegistry
from .toolset import RuntimeTool, RuntimeToolCall, RuntimeToolError, RuntimeToolResult


ExternalXpertRunner = Callable[
    [dict[str, Any], str, RuntimeToolCall],
    Awaitable[RuntimeToolResult],
]


class ExternalXpertToolsetProvider:
    """Dynamic tools backed by immutable published Xpert versions."""

    def __init__(self, runner: ExternalXpertRunner) -> None:
        self.runner = runner

    async def list_tools(
        self,
        resources: list[dict[str, Any]] | None = None,
    ) -> list[RuntimeTool]:
        tools: list[RuntimeTool] = []
        for resource in resources or []:
            tool_name = str(resource.get("tool_name") or "").strip()
            if not tool_name:
                continue
            tools.append(
                RuntimeTool(
                    name=tool_name,
                    description=str(resource.get("description") or "").strip(),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "description": "The task for the collaborating Xpert.",
                                "maxLength": 20000,
                            }
                        },
                        "required": ["task"],
                        "additionalProperties": False,
                    },
                    provider="external_xpert",
                    metadata={
                        "xpert_id": resource.get("xpert_id"),
                        "xpert_version": resource.get("pinned_version"),
                    },
                )
            )
        return tools

    async def find_tool(
        self,
        tool_name: str,
        resources: list[dict[str, Any]] | None = None,
    ) -> RuntimeTool | None:
        return next(
            (
                tool
                for tool in await self.list_tools(resources)
                if tool.name == tool_name
            ),
            None,
        )

    async def call_tool(self, call: RuntimeToolCall) -> RuntimeToolResult:
        resources = call.metadata.get("external_xpert_tools")
        if not isinstance(resources, list):
            raise RuntimeToolError(
                call.tool_name,
                "External Xpert tools require an explicit bound-resource scope.",
                code="external_xpert_scope_missing",
            )
        resource = next(
            (
                item
                for item in resources
                if isinstance(item, dict)
                and str(item.get("tool_name") or "") == call.tool_name
            ),
            None,
        )
        if resource is None:
            raise RuntimeToolError(
                call.tool_name,
                "External Xpert tool is outside the bound-resource scope.",
                code="external_xpert_scope_denied",
            )
        task = str(call.arguments.get("task") or "").strip()
        if not task:
            raise RuntimeToolError(
                call.tool_name,
                "External Xpert task is required.",
                code="invalid_argument",
            )
        try:
            return await self.runner(resource, task, call)
        except RuntimeToolError:
            raise
        except Exception as exc:
            raise RuntimeToolError(
                call.tool_name,
                str(exc),
                code="external_xpert_call_failed",
            ) from exc


def register_external_xpert_toolset_capability(
    registry: CapabilityRegistry,
    provider: ExternalXpertToolsetProvider,
) -> None:
    registry.register(
        "external_xpert_tools",
        provider,
        description="Published Xpert collaborators bound to a workflow agent.",
        metadata={"provider": "external_xpert", "dynamic": True},
    )
