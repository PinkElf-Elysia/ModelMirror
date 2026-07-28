from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from jsonschema import Draft202012Validator

try:
    from server.context_engine import (
        estimate_messages_tokens,
        estimate_text_tokens,
        optimize_context,
    )
except ModuleNotFoundError:
    from context_engine import (
        estimate_messages_tokens,
        estimate_text_tokens,
        optimize_context,
    )

from .middleware import AgentMiddleware
from .models import MiddlewareContext, ModelCallRequest
from .toolset import RuntimeTool


ModelTextCallback = Callable[[str, list[dict[str, Any]], int], Awaitable[str]]


@dataclass(slots=True)
class RuntimeMiddlewareSpec:
    node_id: str
    middleware_id: str
    priority: int = 100
    config: dict[str, Any] = field(default_factory=dict)
    binding: str = "agent"


def is_middleware_binding_edge(edge: Any) -> bool:
    return str(getattr(edge, "targetHandle", None) or "").strip() == "middleware"


RESOURCE_BINDING_HANDLES = {"expert", "knowledge", "toolset", "plugin"}


def is_resource_binding_edge(edge: Any) -> bool:
    return (
        str(getattr(edge, "targetHandle", None) or "").strip()
        in RESOURCE_BINDING_HANDLES
    )


def is_non_control_binding_edge(edge: Any) -> bool:
    return is_middleware_binding_edge(edge) or is_resource_binding_edge(edge)


def control_flow_edges(edges: list[Any]) -> list[Any]:
    return [edge for edge in edges if not is_non_control_binding_edge(edge)]


def bound_resource_nodes(
    nodes_by_id: dict[str, Any],
    edges: list[Any],
    agent_node_id: str,
    binding_handle: str,
) -> list[Any]:
    if binding_handle not in RESOURCE_BINDING_HANDLES:
        return []
    result: list[Any] = []
    for edge in edges:
        if (
            str(getattr(edge, "target", "")) != agent_node_id
            or str(getattr(edge, "targetHandle", None) or "").strip()
            != binding_handle
        ):
            continue
        node = nodes_by_id.get(str(getattr(edge, "source", "")))
        if node is not None:
            result.append(node)
    return sorted(result, key=lambda item: str(getattr(item, "id", "")))


def middleware_spec_from_node(node: Any, *, binding: str) -> RuntimeMiddlewareSpec:
    data = getattr(node, "data", {})
    data = data if isinstance(data, dict) else {}
    config = data.get("runtimeMiddlewareConfig")
    config = dict(config) if isinstance(config, dict) else {}
    try:
        priority = int(str(data.get("middlewarePriority", 100)))
    except (TypeError, ValueError):
        priority = 100
    return RuntimeMiddlewareSpec(
        node_id=str(getattr(node, "id", "")),
        middleware_id=str(data.get("runtimeMiddlewareId") or "").strip(),
        priority=max(0, min(priority, 1000)),
        config=config,
        binding=binding,
    )


def bound_middleware_specs(
    nodes_by_id: dict[str, Any],
    edges: list[Any],
    agent_node_id: str,
) -> list[RuntimeMiddlewareSpec]:
    specs: list[RuntimeMiddlewareSpec] = []
    for edge in edges:
        if not is_middleware_binding_edge(edge) or str(edge.target) != agent_node_id:
            continue
        node = nodes_by_id.get(str(edge.source))
        if node is not None:
            specs.append(middleware_spec_from_node(node, binding="agent"))
    return sorted(specs, key=lambda spec: (spec.priority, spec.node_id))


def middleware_spec(
    specs: list[RuntimeMiddlewareSpec],
    middleware_id: str,
) -> RuntimeMiddlewareSpec | None:
    return next(
        (spec for spec in reversed(specs) if spec.middleware_id == middleware_id),
        None,
    )


def build_context_compression_middleware(
    spec: RuntimeMiddlewareSpec,
) -> AgentMiddleware:
    async def before_model(
        request: ModelCallRequest,
        context: MiddlewareContext,
    ) -> dict[str, Any] | None:
        config = spec.config
        max_context_tokens = _int(config.get("max_context_tokens"), 24_000, 2_048, 200_000)
        trigger_ratio = _float(config.get("trigger_ratio"), 0.8, 0.5, 0.95)
        keep_recent = _int(config.get("keep_recent_messages"), 8, 2, 40)
        summary_max_tokens = _int(config.get("summary_max_tokens"), 1_500, 256, 4_000)
        max_tool_output_chars = _int(config.get("max_tool_output_chars"), 4_000, 500, 20_000)
        existing_summary = str(context.metadata.get("conversation_summary") or "").strip()
        summary_boundary = str(
            context.metadata.get("conversation_summary_through_message_id") or ""
        ).strip()
        summarizer = context.metadata.get("middleware_model_text")
        summary_model_id = str(config.get("summary_model_id") or request.model_id).strip()
        persist_summary = context.metadata.get("persist_conversation_summary")
        optimization = await optimize_context(
            request.messages,
            profile="standard",
            max_context_tokens=max_context_tokens,
            max_output_tokens=0,
            safety_margin_tokens=0,
            trigger_ratio=trigger_ratio,
            keep_recent_messages=keep_recent,
            max_tool_output_chars=max_tool_output_chars,
            summary_model_id=summary_model_id,
            summary_max_tokens=summary_max_tokens,
            summarizer=summarizer if callable(summarizer) else None,
            existing_summary=existing_summary,
            summary_boundary=summary_boundary,
            persist_summary=persist_summary if callable(persist_summary) else None,
        )
        report = optimization.report
        context.metadata["context_compression"] = {
            **report.as_dict(),
            "before_tokens": report.original_tokens,
            "after_tokens": report.final_tokens,
            "summarized_messages": report.summarized_messages,
            "reused_summary": report.reused_summary,
        }
        if report.fallback_reason in {"summary_failed", "summary_unavailable"}:
            context.metadata.setdefault("middleware_warnings", []).append(
                "context_compression summarizer is unavailable"
                if report.fallback_reason == "summary_unavailable"
                else "context_compression failed: summary unavailable"
            )
            if report.original_tokens >= int(max_context_tokens * trigger_ratio):
                system_messages = [
                    message
                    for message in request.messages
                    if message.get("role") == "system"
                ]
                non_system = [
                    message
                    for message in request.messages
                    if message.get("role") != "system"
                ]
                return {
                    "messages": [
                        *system_messages,
                        *non_system[-keep_recent:],
                    ]
                }
        if optimization.messages != request.messages:
            return {"messages": optimization.messages}
        return None

    return AgentMiddleware(name="context_compression", before_model=before_model)


async def validate_structured_output(
    text: str,
    *,
    schema: dict[str, Any],
    model_id: str,
    repair_attempts: int,
    model_text: ModelTextCallback,
) -> str:
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    current = text.strip()
    attempts = max(0, min(int(repair_attempts), 1))
    for attempt in range(attempts + 1):
        try:
            parsed = json.loads(_extract_json_text(current))
            errors = sorted(validator.iter_errors(parsed), key=lambda error: list(error.path))
            if not errors:
                return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            issue = "; ".join(error.message for error in errors[:5])
        except Exception as exc:
            issue = str(exc)[:500]
        if attempt >= attempts:
            raise ValueError(f"Structured output validation failed: {issue}")
        repair_prompt = (
            "Return only valid JSON matching the supplied JSON Schema. "
            "Do not include Markdown or commentary.\n\n"
            f"Schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
            f"Validation problem:\n{issue}\n\n"
            f"Invalid output:\n{current[:20_000]}"
        )
        current = await model_text(
            model_id,
            [{"role": "user", "content": repair_prompt}],
            2_048,
        )
    raise ValueError("Structured output validation failed.")


async def select_runtime_tools(
    tools: list[RuntimeTool],
    *,
    user_prompt: str,
    model_id: str,
    max_selected_tools: int,
    required_tools: set[str],
    model_text: ModelTextCallback,
) -> tuple[list[RuntimeTool], dict[str, Any]]:
    if not tools:
        return [], {"mode": "empty", "selected": []}
    limit = max(1, min(int(max_selected_tools), 20))
    tool_by_name = {tool.name: tool for tool in tools if tool.name}
    required = [name for name in sorted(required_tools) if name in tool_by_name]
    selectable = [tool for tool in tools if tool.name not in required_tools]
    slots = max(0, limit - len(required))
    if len(selectable) <= slots:
        selected_names = [*required, *(tool.name for tool in selectable)]
        return [tool_by_name[name] for name in selected_names], {
            "mode": "all",
            "selected": selected_names,
        }

    catalog = [
        {
            "name": tool.name,
            "description": (tool.description or "")[:500],
            "provider": tool.provider,
        }
        for tool in selectable
    ]
    selector_prompt = (
        "Select the smallest useful set of tools for the user request. Return strict JSON "
        'as {"tools":["name"]}. Only choose names from the catalog.\n\n'
        f"User request:\n{user_prompt[:12_000]}\n\n"
        f"Maximum tools: {slots}\nCatalog:\n{json.dumps(catalog, ensure_ascii=False)}"
    )
    warning: str | None = None
    mode = "llm"
    try:
        raw = await model_text(
            model_id,
            [{"role": "user", "content": selector_prompt}],
            1_024,
        )
        decision = json.loads(_extract_json_text(raw))
        names = decision.get("tools") if isinstance(decision, dict) else None
        if not isinstance(names, list):
            raise ValueError("selector response is missing tools")
        chosen = []
        for value in names:
            name = str(value or "").strip()
            if name in tool_by_name and name not in required and name not in chosen:
                chosen.append(name)
            if len(chosen) >= slots:
                break
    except Exception as exc:
        mode = "lexical_fallback"
        warning = str(exc)[:200]
        chosen = _lexical_tool_names(selectable, user_prompt, slots)
    selected_names = [*required, *chosen]
    return [tool_by_name[name] for name in selected_names], {
        "mode": mode,
        "selected": selected_names,
        "warning": warning,
    }


def todo_planning_instruction(items: list[dict[str, Any]]) -> str:
    active = [item for item in items if item.get("status") in {"pending", "in_progress"}]
    current = json.dumps(active[:50], ensure_ascii=False)
    return (
        "For multi-step tasks, maintain a concise Todo plan with todo_create and todo_update. "
        "Mark one active item in_progress, complete items as work finishes, and do not create "
        f"duplicate items. Current active Todo items: {current}"
    )


def middleware_config_int(
    config: dict[str, Any],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    return _int(config.get(name), default, minimum, maximum)


def middleware_config_schema(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("schema_json")
    if isinstance(raw, dict):
        schema = dict(raw)
    else:
        schema = json.loads(str(raw or "{}"))
    if not isinstance(schema, dict) or not schema:
        raise ValueError("structured_output schema_json must be a JSON object.")
    Draft202012Validator.check_schema(schema)
    return schema


def _extract_json_text(text: str) -> str:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL | re.IGNORECASE)
    return fenced.group(1).strip() if fenced else stripped


def _lexical_tool_names(
    tools: list[RuntimeTool],
    query: str,
    limit: int,
) -> list[str]:
    query_terms = set(re.findall(r"[a-z0-9_\-]+|[\u3400-\u9fff]", query.lower()))
    ranked: list[tuple[int, str]] = []
    for tool in tools:
        text = f"{tool.name} {tool.description}".lower()
        terms = set(re.findall(r"[a-z0-9_\-]+|[\u3400-\u9fff]", text))
        score = len(query_terms & terms)
        if tool.name.lower() in query.lower():
            score += 5
        ranked.append((score, tool.name))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in ranked[:limit]]


def _int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))
