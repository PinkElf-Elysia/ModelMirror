from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.xpert_runtime.core_middlewares import RuntimeMiddlewareSpec
from server.xpert_runtime.middleware import MiddlewarePipeline
from server.xpert_runtime.models import (
    MiddlewareContext,
    ToolCallRequest,
    ToolCallResponse,
)
from server.xpert_runtime.plugin_hooks import build_plugin_hooks_middleware
from server.xpert_runtime.toolset import RuntimeToolResult


class FakeSkillManager:
    def __init__(self, root: Path) -> None:
        self.root = root

    def get_skill_directory(self, skill_id: str) -> Path:
        return self.root / skill_id


class FakeSandboxProvider:
    def __init__(self) -> None:
        self.calls = []

    async def call_tool(self, call):
        self.calls.append(call)
        return RuntimeToolResult(output="ok")


@pytest.mark.asyncio
async def test_plugin_hooks_stage_skill_and_wrap_matching_tool(tmp_path: Path) -> None:
    skill_dir = tmp_path / "review-skill"
    skill_dir.mkdir()
    (skill_dir / "modelmirror-hooks.json").write_text(
        json.dumps(
            {
                "hooks": [
                    {"event": "SessionStart", "argv": ["python", "start.py"]},
                    {
                        "event": "PreToolUse",
                        "matcher": "fetch",
                        "argv": ["python", "pre.py"],
                    },
                    {
                        "event": "PostToolUse",
                        "matcher": "fetch",
                        "argv": ["python", "post.py"],
                    },
                    {"event": "SessionEnd", "argv": ["python", "end.py"]},
                ]
            }
        ),
        encoding="utf-8",
    )
    sandbox = FakeSandboxProvider()
    pipeline = MiddlewarePipeline(
        [
            build_plugin_hooks_middleware(
                RuntimeMiddlewareSpec(
                    node_id="hooks",
                    middleware_id="plugin_hooks",
                    config={"skill_ids": "review-skill", "fail_closed": True},
                ),
                skill_manager=FakeSkillManager(tmp_path),
                sandbox_provider=sandbox,
            )
        ]
    )
    context = MiddlewareContext(
        task_id="task-1",
        metadata={"scope_type": "workflow", "scope_id": "task-1:agent"},
    )

    await pipeline.before_agent({}, context)

    async def handler(request: ToolCallRequest) -> ToolCallResponse:
        return ToolCallResponse(output=f"called {request.tool_name}")

    response = await pipeline.run_tool_call(
        ToolCallRequest("fetch", {"query": "test"}),
        handler,
        context,
    )
    await pipeline.after_agent({}, context)

    shell_calls = [call for call in sandbox.calls if call.tool_name == "sandbox_shell"]
    assert response.output == "called fetch"
    assert [call.arguments["argv"][1] for call in shell_calls] == [
        "start.py",
        "pre.py",
        "post.py",
        "end.py",
    ]
    assert all(call.arguments["cwd"] == "skills/review-skill" for call in shell_calls)
    assert all(
        call.tool_name in {"skill_stage", "sandbox_shell"}
        for call in sandbox.calls
    )


@pytest.mark.asyncio
async def test_plugin_hooks_fail_open_or_closed_for_invalid_manifest(tmp_path: Path) -> None:
    skill_dir = tmp_path / "broken-skill"
    skill_dir.mkdir()
    (skill_dir / "modelmirror-hooks.json").write_text(
        '{"hooks":[{"event":"Unsupported","argv":["python","hook.py"]}]}',
        encoding="utf-8",
    )
    context = MiddlewareContext(metadata={})
    manager = FakeSkillManager(tmp_path)

    open_pipeline = MiddlewarePipeline(
        [
            build_plugin_hooks_middleware(
                RuntimeMiddlewareSpec(
                    node_id="hooks-open",
                    middleware_id="plugin_hooks",
                    config={"skill_ids": "broken-skill", "fail_closed": False},
                ),
                skill_manager=manager,
                sandbox_provider=FakeSandboxProvider(),
            )
        ]
    )
    await open_pipeline.before_agent({}, context)
    assert "event is not supported" in context.metadata["middleware_warnings"][0]

    closed_pipeline = MiddlewarePipeline(
        [
            build_plugin_hooks_middleware(
                RuntimeMiddlewareSpec(
                    node_id="hooks-closed",
                    middleware_id="plugin_hooks",
                    config={"skill_ids": "broken-skill", "fail_closed": True},
                ),
                skill_manager=manager,
                sandbox_provider=FakeSandboxProvider(),
            )
        ]
    )
    with pytest.raises(ValueError, match="event is not supported"):
        await closed_pipeline.before_agent({}, MiddlewareContext(metadata={}))
