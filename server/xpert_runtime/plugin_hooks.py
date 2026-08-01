from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core_middlewares import RuntimeMiddlewareSpec
from .middleware import AgentMiddleware
from .models import MiddlewareContext, ToolCallRequest, ToolCallResponse
from .toolset import RuntimeToolCall


SUPPORTED_HOOK_EVENTS = {"SessionStart", "PreToolUse", "PostToolUse", "SessionEnd"}


def build_plugin_hooks_middleware(
    spec: RuntimeMiddlewareSpec,
    *,
    skill_manager: Any,
    sandbox_provider: Any,
) -> AgentMiddleware:
    """Run explicitly installed Skill hooks inside the offline Sandbox."""

    skill_ids = _csv(spec.config.get("skill_ids"))[:10]
    fail_closed = _truthy(spec.config.get("fail_closed", False))

    async def invoke(event: str, context: MiddlewareContext, tool_name: str | None = None) -> None:
        for skill_id in skill_ids:
            try:
                manifest = _load_manifest(skill_manager, skill_id)
                for index, hook in enumerate(manifest.get("hooks", [])):
                    if not isinstance(hook, dict) or hook.get("event") != event:
                        continue
                    matcher = str(hook.get("matcher") or "*").strip()
                    if tool_name and matcher not in {"*", tool_name}:
                        continue
                    argv = hook.get("argv")
                    if not isinstance(argv, list) or not argv or any(not isinstance(item, str) for item in argv):
                        raise ValueError("Plugin hook argv must be a non-empty string array.")
                    await sandbox_provider.call_tool(
                        RuntimeToolCall(
                            tool_name="skill_stage",
                            arguments={"skill_id": skill_id},
                            metadata={
                                **context.metadata,
                                "skills_config": {"skill_ids": skill_ids, "auto_discover": False},
                                "iteration": f"plugin-stage:{event}:{skill_id}",
                            },
                        )
                    )
                    await sandbox_provider.call_tool(
                        RuntimeToolCall(
                            tool_name="sandbox_shell",
                            arguments={
                                "argv": argv,
                                "cwd": f"skills/{skill_id}",
                                "timeout_seconds": max(1, min(int(hook.get("timeout_seconds") or 60), 300)),
                            },
                            metadata={
                                **context.metadata,
                                "sandbox_config": {"allowed_commands": str(argv[0]), "timeout_seconds": hook.get("timeout_seconds") or 60},
                                "skills_config": {"skill_ids": skill_ids, "auto_discover": False},
                                "iteration": f"plugin-hook:{event}:{skill_id}:{index}:{tool_name or ''}",
                            },
                        )
                    )
            except Exception as exc:
                context.metadata.setdefault("middleware_warnings", []).append(
                    f"plugin_hooks {event} failed for {skill_id}: {str(exc)[:180]}"
                )
                if fail_closed:
                    raise

    async def before_agent(state: dict[str, Any], context: MiddlewareContext) -> None:
        await invoke("SessionStart", context)

    async def after_agent(state: dict[str, Any], context: MiddlewareContext) -> None:
        await invoke("SessionEnd", context)

    async def wrap_tool(
        request: ToolCallRequest,
        handler: Any,
        context: MiddlewareContext,
    ) -> ToolCallResponse:
        await invoke("PreToolUse", context, request.tool_name)
        response = await handler(request)
        await invoke("PostToolUse", context, request.tool_name)
        return response

    return AgentMiddleware(
        name="plugin_hooks",
        before_agent=before_agent,
        after_agent=after_agent,
        wrap_tool_call=wrap_tool,
    )


def _load_manifest(skill_manager: Any, skill_id: str) -> dict[str, Any]:
    root = Path(skill_manager.get_skill_directory(skill_id)).resolve()
    path = (root / "modelmirror-hooks.json").resolve()
    if root not in path.parents or not path.is_file() or path.is_symlink():
        return {"hooks": []}
    if path.stat().st_size > 100_000:
        raise ValueError("Plugin hook manifest is too large.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("hooks", []), list):
        raise ValueError("Plugin hook manifest is invalid.")
    for hook in payload.get("hooks", []):
        if not isinstance(hook, dict) or hook.get("event") not in SUPPORTED_HOOK_EVENTS:
            raise ValueError("Plugin hook event is not supported.")
    return payload


def _csv(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value or "").replace("\n", ",").split(",") if item.strip()]


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
