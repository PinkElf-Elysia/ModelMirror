from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from server.skills.application_receipts import (
    SkillApplicationReceiptStore,
    SkillApplicationScope,
    build_application_contract,
)
from server.skills.package_validation import compute_skill_content_digest
from server.sandbox_sidecar.engine import SandboxEngine
from server.xpert_runtime.approval_store import RuntimeApprovalStore
from server.xpert_runtime.core_middlewares import RuntimeMiddlewareSpec
from server.xpert_runtime.hitl_middleware import build_human_in_the_loop_middleware
from server.xpert_runtime.middleware import MiddlewarePipeline
from server.xpert_runtime.models import (
    MiddlewareContext,
    ModelCallRequest,
    ModelCallResponse,
    ToolCallRequest,
    ToolCallResponse,
)
from server.xpert_runtime.plugin_hooks_v2 import (
    SkillHookRuntimeError,
    build_plugin_hooks_v2_middleware,
    drain_skill_hook_status_events,
    typed_hook_skill_ids,
)
from server.xpert_runtime.sandbox_store import SandboxWorkspaceStore
from server.xpert_runtime.sandbox_toolset import SandboxToolsetProvider
from server.xpert_runtime.toolset import RuntimeToolResult


SKILL_ID = "typed-hook"
VERSION_ID = "skillver_typed_hook_1"
PACKAGE_DIGEST = "a" * 64


class FakeSkillManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.activation_calls: list[tuple[str, dict[str, Any]]] = []

    def get_skill_directory(
        self, skill_id: str, *, version_id: str | None = None
    ) -> Path:
        assert skill_id == SKILL_ID
        assert version_id == VERSION_ID
        return self.root / VERSION_ID / skill_id

    def require_activation(self, skill_id: str, **kwargs: Any) -> None:
        self.activation_calls.append((skill_id, kwargs))


class RealReceiptObserver:
    def __init__(self, root: Path) -> None:
        self.store = SkillApplicationReceiptStore(root)

    def record(self, **kwargs: Any):
        contract = build_application_contract(
            skill_id=kwargs["skill_id"],
            source_kind="workspace_draft",
            version_id=kwargs.get("version_id"),
            content_digest=PACKAGE_DIGEST,
            policy=kwargs.get("policy", "advisory"),
        )
        return self.store.observe(
            contract,
            SkillApplicationScope(
                run_id=kwargs.get("run_id"),
                task_id=kwargs.get("task_id"),
                node_id=kwargs.get("node_id"),
                runtime_kind=kwargs.get("runtime_kind", "workflow"),
            ),
            method=kwargs.get("method"),
            tool_name=kwargs.get("tool_name"),
            error_code=kwargs.get("error_code"),
            hook_evidence=kwargs.get("hook_evidence", ()),
        )


class FakeSandboxProvider:
    def __init__(
        self,
        skill_root: Path,
        result_factory: Callable[[dict[str, Any], list[str]], dict[str, Any]],
    ) -> None:
        self.skill_root = skill_root
        self.result_factory = result_factory
        self.files: dict[str, str] = {}
        self.calls: list[Any] = []
        self.write_history: list[tuple[str, str]] = []
        self.shell_count = 0
        self.fail_shell = False

    async def call_tool(self, call: Any) -> RuntimeToolResult:
        self.calls.append(call)
        if call.tool_name == "skill_stage":
            return RuntimeToolResult(output='{"ok":true}')
        if call.tool_name == "sandbox_write_file":
            path = str(call.arguments["path"])
            content = str(call.arguments.get("content") or "")
            self.files[path] = content
            self.write_history.append((path, content))
            return RuntimeToolResult(output=json.dumps({"ok": True, "path": path}))
        if call.tool_name == "sandbox_read_file":
            path = str(call.arguments["path"])
            if path.startswith(f"skills/{SKILL_ID}/"):
                relative = path.removeprefix(f"skills/{SKILL_ID}/")
                content = (self.skill_root / relative).read_text(encoding="utf-8")
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                return RuntimeToolResult(
                    output=json.dumps({"ok": True, "path": path, "content": content}),
                    metadata={"sandbox_accessed_digests": {path: digest}},
                )
            content = self.files[path]
            return RuntimeToolResult(
                output=json.dumps({"ok": True, "path": path, "content": content})
            )
        if call.tool_name == "sandbox_shell":
            self.shell_count += 1
            if self.fail_shell:
                raise RuntimeError("sidecar unavailable with secret body")
            argv = list(call.arguments["argv"])
            context_path = "work/" + argv[argv.index("--context") + 1]
            result_path = "work/" + argv[argv.index("--result") + 1]
            context = json.loads(self.files[context_path])
            self.files[result_path] = json.dumps(
                self.result_factory(context, argv), ensure_ascii=False
            )
            return RuntimeToolResult(
                output=json.dumps(
                    {
                        "ok": True,
                        "exit_code": 0,
                        "stdout": "ignored secret stdout",
                        "stderr": "",
                    }
                )
            )
        raise AssertionError(f"unexpected tool: {call.tool_name}")


def _write_skill(
    tmp_path: Path,
    hooks: list[dict[str, Any]],
) -> tuple[Path, FakeSkillManager]:
    root = tmp_path / VERSION_ID / SKILL_ID
    (root / "hooks").mkdir(parents=True)
    (root / "scripts").mkdir()
    scripts = {str(hook["script_path"]) for hook in hooks}
    for script in scripts:
        path = root / script
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("print('hook')\n", encoding="utf-8")
    (root / "hooks" / "manifest.json").write_text(
        json.dumps(
            {"version": "modelmirror-hook-manifest-v2", "hooks": hooks},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root, FakeSkillManager(tmp_path)


def _hook(
    *,
    hook_id: str,
    event: str,
    mode: str,
    tool_names: list[str] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "hook_id": hook_id,
        "event": event,
        "mode": mode,
        "script_path": f"scripts/{hook_id}.py",
        "purpose": "Synthetic runtime check",
        "acceptance_checks": ["Returns a typed result"],
        "timeout_seconds": 5,
    }
    if tool_names is not None:
        result["tool_names"] = tool_names
    return result


def _context(task_input: str = "test task") -> MiddlewareContext:
    return MiddlewareContext(
        task_id="task-1",
        trace_id="run-1",
        metadata={
            "run_id": "run-1",
            "node_id": "agent-1",
            "runtime_run_type": "workflow",
            "hook_task_input": task_input,
            "skill_version_bindings": {SKILL_ID: VERSION_ID},
        },
    )


def _middleware(
    manager: FakeSkillManager,
    sandbox: FakeSandboxProvider,
    observer: RealReceiptObserver,
):
    return build_plugin_hooks_v2_middleware(
        RuntimeMiddlewareSpec(
            node_id="hooks",
            middleware_id="plugin_hooks",
            config={"hook_mode": "typed_v2", "skill_ids": SKILL_ID},
        ),
        skill_manager=manager,
        sandbox_provider=sandbox,
        application_observer=observer,
    )


@pytest.mark.asyncio
async def test_session_annotation_is_injected_and_private_context_is_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "true")
    root, manager = _write_skill(
        tmp_path,
        [_hook(hook_id="start_note", event="session_start", mode="annotation")],
    )
    sandbox = FakeSandboxProvider(
        root,
        lambda _context, _argv: {
            "version": "modelmirror-hook-result-v1",
            "outputs": [
                {
                    "type": "annotation",
                    "code": "release.naming",
                    "severity": "warning",
                    "message": "Check the release naming convention.",
                }
            ],
        },
    )
    observer = RealReceiptObserver(tmp_path / "receipts")
    pipeline = MiddlewarePipeline([_middleware(manager, sandbox, observer)])
    task_prefix = "publish C:\\Users\\private\\release "
    task_input = (
        task_prefix
        + ("x" * (8_182 - len(task_prefix)))
        + " token=super-secret-token"
    )
    context = _context(task_input)

    await pipeline.before_agent({}, context)

    captured_messages: list[dict[str, Any]] = []

    async def model_handler(request: ModelCallRequest) -> ModelCallResponse:
        captured_messages.extend(request.messages)
        return ModelCallResponse(text="ok")

    await pipeline.run_model_call(
        ModelCallRequest("model", [{"role": "user", "content": "go"}]),
        model_handler,
        context,
    )

    assert any("release.naming" in item["content"] for item in captured_messages)
    context_writes = [
        content
        for path, content in sandbox.write_history
        if path.endswith("context.json") and content != "{}"
    ]
    assert context_writes
    persisted_context = context_writes[0]
    assert "super-secret-token" not in persisted_context
    assert "token=sup" not in persisted_context
    assert "C:\\Users\\private" not in persisted_context
    receipts = observer.store.list_receipts(run_id="run-1", skill_id=SKILL_ID)
    assert receipts[0].hook_evidence[0].verified is True
    serialized = json.dumps(
        [event for event in drain_skill_hook_status_events(context)],
        ensure_ascii=False,
    )
    assert "super-secret-token" not in serialized
    assert "Check the release naming convention" not in serialized
    assert all(
        content == "{}"
        for path, content in sandbox.files.items()
        if path.endswith(("context.json", "result.json"))
    )


@pytest.mark.asyncio
async def test_guard_deny_happens_before_hitl_and_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "true")
    root, manager = _write_skill(
        tmp_path,
        [
            _hook(
                hook_id="deny_delete",
                event="pre_tool_use",
                mode="guard",
                tool_names=["delete_file"],
            )
        ],
    )
    sandbox = FakeSandboxProvider(
        root,
        lambda _context, _argv: {
            "version": "modelmirror-hook-result-v1",
            "outputs": [
                {"type": "deny", "code": "delete.denied", "message": "No"}
            ],
        },
    )
    approvals = RuntimeApprovalStore(tmp_path / "approvals")
    observer = RealReceiptObserver(tmp_path / "receipts")
    pipeline = MiddlewarePipeline(
        [
            _middleware(manager, sandbox, observer),
            build_human_in_the_loop_middleware(
                RuntimeMiddlewareSpec(
                    node_id="hitl",
                    middleware_id="human_in_the_loop",
                    config={"interrupt_on_tools": "*"},
                ),
                approvals,
            ),
        ]
    )
    provider_called = False
    context = _context()

    async def handler(_request: ToolCallRequest) -> ToolCallResponse:
        nonlocal provider_called
        provider_called = True
        return ToolCallResponse(output="must not run")

    with pytest.raises(SkillHookRuntimeError) as caught:
        await pipeline.run_tool_call(
            ToolCallRequest("delete_file", {"path": "work/release.txt"}),
            handler,
            context,
        )
    assert caught.value.code == "skill_hook_denied"
    assert provider_called is False
    assert approvals.list_requests() == []
    statuses = [event["status"] for event in drain_skill_hook_status_events(context)]
    assert "denied" in statuses
    assert "completed" not in statuses


@pytest.mark.asyncio
async def test_hitl_edit_is_revalidated_with_edited_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "true")
    root, manager = _write_skill(
        tmp_path,
        [
            _hook(
                hook_id="protect_path",
                event="pre_tool_use",
                mode="guard",
                tool_names=["write_file"],
            )
        ],
    )

    def result(context: dict[str, Any], _argv: list[str]) -> dict[str, Any]:
        path = str(context["tool"]["arguments"].get("path") or "")
        output = (
            {"type": "deny", "code": "path.denied", "message": "No"}
            if path == "work/blocked.txt"
            else {
                "type": "validation",
                "code": "path.allowed",
                "passed": True,
                "message": "OK",
            }
        )
        return {"version": "modelmirror-hook-result-v1", "outputs": [output]}

    sandbox = FakeSandboxProvider(root, result)
    observer = RealReceiptObserver(tmp_path / "receipts")
    pipeline = MiddlewarePipeline(
        [
            _middleware(manager, sandbox, observer),
            build_human_in_the_loop_middleware(
                RuntimeMiddlewareSpec(
                    node_id="hitl",
                    middleware_id="human_in_the_loop",
                    config={"interrupt_on_tools": "*"},
                ),
                RuntimeApprovalStore(tmp_path / "approvals"),
            ),
        ]
    )
    provider_called = False

    async def handler(_request: ToolCallRequest) -> ToolCallResponse:
        nonlocal provider_called
        provider_called = True
        return ToolCallResponse(output="must not run")

    request = ToolCallRequest(
        "write_file",
        {"path": "work/allowed.txt"},
        metadata={
            "resolved_approval": {
                "approval_id": "approval-1",
                "decision": "edit",
                "edited_arguments": {"path": "work/blocked.txt"},
            }
        },
    )
    with pytest.raises(SkillHookRuntimeError) as caught:
        await pipeline.run_tool_call(request, handler, _context())
    assert caught.value.code == "skill_hook_denied"
    assert provider_called is False
    assert sandbox.shell_count == 2


@pytest.mark.asyncio
async def test_hitl_rejection_does_not_forge_post_tool_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "true")
    root, manager = _write_skill(
        tmp_path,
        [
            _hook(
                hook_id="validate_result",
                event="post_tool_use",
                mode="validation",
                tool_names=["write_file"],
            )
        ],
    )
    sandbox = FakeSandboxProvider(
        root,
        lambda _context, _argv: {
            "version": "modelmirror-hook-result-v1",
            "outputs": [
                {
                    "type": "validation",
                    "code": "result.valid",
                    "passed": True,
                    "message": "OK",
                }
            ],
        },
    )
    observer = RealReceiptObserver(tmp_path / "receipts")
    pipeline = MiddlewarePipeline(
        [
            _middleware(manager, sandbox, observer),
            build_human_in_the_loop_middleware(
                RuntimeMiddlewareSpec(
                    node_id="hitl",
                    middleware_id="human_in_the_loop",
                    config={"interrupt_on_tools": "*"},
                ),
                RuntimeApprovalStore(tmp_path / "approvals"),
            ),
        ]
    )
    provider_called = False

    async def handler(_request: ToolCallRequest) -> ToolCallResponse:
        nonlocal provider_called
        provider_called = True
        return ToolCallResponse(output="must not run")

    response = await pipeline.run_tool_call(
        ToolCallRequest(
            "write_file",
            {"path": "work/release.txt"},
            metadata={
                "resolved_approval": {
                    "approval_id": "approval-1",
                    "decision": "reject",
                    "message": "Rejected by user",
                }
            },
        ),
        handler,
        _context(),
    )

    assert response.metadata["approval_rejected"] is True
    assert provider_called is False
    assert sandbox.shell_count == 0
    assert observer.store.list_receipts(run_id="run-1", skill_id=SKILL_ID) == []


@pytest.mark.asyncio
async def test_parallel_batch_preflight_is_all_or_none_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "true")
    root, manager = _write_skill(
        tmp_path,
        [
            _hook(
                hook_id="batch_guard",
                event="pre_tool_use",
                mode="guard",
                tool_names=["safe_a", "safe_b"],
            )
        ],
    )

    def result(context: dict[str, Any], _argv: list[str]) -> dict[str, Any]:
        denied = context["tool"]["name"] == "safe_b"
        return {
            "version": "modelmirror-hook-result-v1",
            "outputs": [
                (
                    {"type": "deny", "code": "batch.denied", "message": "No"}
                    if denied
                    else {
                        "type": "validation",
                        "code": "batch.allowed",
                        "passed": True,
                        "message": "OK",
                    }
                )
            ],
        }

    sandbox = FakeSandboxProvider(root, result)
    pipeline = MiddlewarePipeline(
        [_middleware(manager, sandbox, RealReceiptObserver(tmp_path / "receipts"))]
    )
    requests = [ToolCallRequest("safe_a", {}), ToolCallRequest("safe_b", {})]
    with pytest.raises(SkillHookRuntimeError) as caught:
        await pipeline.before_tool_batch(requests, _context())
    assert caught.value.code == "skill_hook_denied"
    assert sandbox.shell_count == 2


@pytest.mark.asyncio
async def test_verified_pre_tool_evidence_is_reused_after_context_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "true")
    root, manager = _write_skill(
        tmp_path,
        [
            _hook(
                hook_id="stable_guard",
                event="pre_tool_use",
                mode="guard",
                tool_names=["publish"],
            )
        ],
    )
    sandbox = FakeSandboxProvider(
        root,
        lambda _context, _argv: {
            "version": "modelmirror-hook-result-v1",
            "outputs": [
                {
                    "type": "validation",
                    "code": "publish.allowed",
                    "passed": True,
                    "message": "OK",
                }
            ],
        },
    )
    observer = RealReceiptObserver(tmp_path / "receipts")
    request = ToolCallRequest("publish", {"path": "work/release.txt"})

    await MiddlewarePipeline([_middleware(manager, sandbox, observer)]).before_tool_batch(
        [request], _context()
    )
    recovered_pipeline = MiddlewarePipeline([_middleware(manager, sandbox, observer)])

    async def handler(_request: ToolCallRequest) -> ToolCallResponse:
        return ToolCallResponse(output="published")

    await recovered_pipeline.run_tool_call(request, handler, _context())

    assert sandbox.shell_count == 1


@pytest.mark.asyncio
async def test_parallel_post_hooks_do_not_expose_concurrent_context_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "true")
    root, manager = _write_skill(
        tmp_path,
        [
            _hook(
                hook_id="post_check",
                event="post_tool_use",
                mode="validation",
                tool_names=["safe_a", "safe_b"],
            )
        ],
    )

    class ConcurrentSandbox(FakeSandboxProvider):
        active_shells = 0
        max_active_shells = 0

        async def call_tool(self, call: Any) -> RuntimeToolResult:
            if call.tool_name != "sandbox_shell":
                return await super().call_tool(call)
            self.active_shells += 1
            self.max_active_shells = max(self.max_active_shells, self.active_shells)
            try:
                await asyncio.sleep(0.02)
                return await super().call_tool(call)
            finally:
                self.active_shells -= 1

    sandbox = ConcurrentSandbox(
        root,
        lambda _context, _argv: {
            "version": "modelmirror-hook-result-v1",
            "outputs": [
                {
                    "type": "validation",
                    "code": "post.valid",
                    "passed": True,
                    "message": "OK",
                }
            ],
        },
    )
    pipeline = MiddlewarePipeline(
        [_middleware(manager, sandbox, RealReceiptObserver(tmp_path / "receipts"))]
    )
    context = _context()

    async def handler(_request: ToolCallRequest) -> ToolCallResponse:
        return ToolCallResponse(output="ok")

    await asyncio.gather(
        pipeline.run_tool_call(ToolCallRequest("safe_a", {}), handler, context),
        pipeline.run_tool_call(ToolCallRequest("safe_b", {}), handler, context),
    )

    assert sandbox.shell_count == 2
    assert sandbox.max_active_shells == 1


@pytest.mark.asyncio
async def test_annotation_failure_opens_but_validation_failure_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "true")
    annotation_root, annotation_manager = _write_skill(
        tmp_path / "annotation",
        [_hook(hook_id="note", event="session_start", mode="annotation")],
    )
    annotation_sandbox = FakeSandboxProvider(
        annotation_root, lambda _context, _argv: {}
    )
    annotation_sandbox.fail_shell = True
    annotation_context = _context()
    annotation_observer = RealReceiptObserver(
        tmp_path / "annotation-receipts"
    )
    await MiddlewarePipeline(
        [
            _middleware(
                annotation_manager,
                annotation_sandbox,
                annotation_observer,
            )
        ]
    ).before_agent({}, annotation_context)
    assert "skill_hook_execution_failed" in annotation_context.metadata[
        "middleware_warnings"
    ][0]
    failed_receipt = annotation_observer.store.list_receipts(
        run_id="run-1", skill_id=SKILL_ID
    )[0]
    assert failed_receipt.error_codes == ("skill_hook_execution_failed",)
    assert failed_receipt.hook_evidence[0].verified is False

    validation_root, validation_manager = _write_skill(
        tmp_path / "validation",
        [_hook(hook_id="check", event="session_start", mode="validation")],
    )
    validation_sandbox = FakeSandboxProvider(
        validation_root, lambda _context, _argv: {}
    )
    validation_sandbox.fail_shell = True
    with pytest.raises(SkillHookRuntimeError) as caught:
        await MiddlewarePipeline(
            [
                _middleware(
                    validation_manager,
                    validation_sandbox,
                    RealReceiptObserver(tmp_path / "validation-receipts"),
                )
            ]
        ).before_agent({}, _context())
    assert caught.value.code == "skill_hook_execution_failed"


@pytest.mark.asyncio
async def test_evidence_store_failure_opens_only_for_annotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "true")

    class FailingReceiptObserver:
        def record(self, **_kwargs: Any):
            raise RuntimeError("receipt store unavailable")

    def passed_result(mode: str) -> dict[str, Any]:
        output = (
            {
                "type": "annotation",
                "code": "note.ready",
                "severity": "info",
                "message": "Ready",
            }
            if mode == "annotation"
            else {
                "type": "validation",
                "code": "check.ready",
                "passed": True,
                "message": "Ready",
            }
        )
        return {"version": "modelmirror-hook-result-v1", "outputs": [output]}

    annotation_root, annotation_manager = _write_skill(
        tmp_path / "annotation-store",
        [_hook(hook_id="note", event="session_start", mode="annotation")],
    )
    annotation_context = _context()
    await MiddlewarePipeline(
        [
            _middleware(
                annotation_manager,
                FakeSandboxProvider(
                    annotation_root,
                    lambda _context, _argv: passed_result("annotation"),
                ),
                FailingReceiptObserver(),  # type: ignore[arg-type]
            )
        ]
    ).before_agent({}, annotation_context)
    assert "skill_hook_evidence_unavailable" in annotation_context.metadata[
        "middleware_warnings"
    ][0]

    validation_root, validation_manager = _write_skill(
        tmp_path / "validation-store",
        [_hook(hook_id="check", event="session_start", mode="validation")],
    )
    with pytest.raises(SkillHookRuntimeError) as caught:
        await MiddlewarePipeline(
            [
                _middleware(
                    validation_manager,
                    FakeSandboxProvider(
                        validation_root,
                        lambda _context, _argv: passed_result("validation"),
                    ),
                    FailingReceiptObserver(),  # type: ignore[arg-type]
                )
            ]
        ).before_agent({}, _context())
    assert caught.value.code == "skill_hook_evidence_unavailable"


@pytest.mark.asyncio
async def test_workspace_provision_failure_does_not_write_to_fallback_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "true")
    root, manager = _write_skill(
        tmp_path,
        [_hook(hook_id="check", event="session_start", mode="validation")],
    )

    class FailingProvisionSandbox(FakeSandboxProvider):
        async def provision_skill_hook_workspace(self, **_kwargs: Any):
            raise RuntimeError("protected workspace unavailable")

    sandbox = FailingProvisionSandbox(root, lambda _context, _argv: {})

    with pytest.raises(SkillHookRuntimeError) as caught:
        await MiddlewarePipeline(
            [_middleware(manager, sandbox, RealReceiptObserver(tmp_path / "receipts"))]
        ).before_agent({}, _context())

    assert caught.value.code == "skill_hook_execution_failed"
    assert sandbox.calls == []
    assert sandbox.write_history == []


@pytest.mark.asyncio
async def test_event_timeout_budget_fails_before_any_hook_executes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "true")
    hooks = [
        _hook(hook_id=f"budget_{index}", event="session_start", mode="validation")
        for index in range(3)
    ]
    for hook in hooks:
        hook["timeout_seconds"] = 60
    root, manager = _write_skill(tmp_path, hooks)
    sandbox = FakeSandboxProvider(root, lambda _context, _argv: {})

    with pytest.raises(SkillHookRuntimeError) as caught:
        await MiddlewarePipeline(
            [_middleware(manager, sandbox, RealReceiptObserver(tmp_path / "receipts"))]
        ).before_agent({}, _context())

    assert caught.value.code == "skill_hook_budget_exceeded"
    assert sandbox.shell_count == 0


@pytest.mark.asyncio
async def test_guard_fails_closed_when_sanitized_context_exceeds_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "true")
    root, manager = _write_skill(
        tmp_path,
        [
            _hook(
                hook_id="bounded_guard",
                event="pre_tool_use",
                mode="guard",
                tool_names=["publish"],
            )
        ],
    )
    sandbox = FakeSandboxProvider(root, lambda _context, _argv: {})
    arguments = {f"field_{index}": "x" * 2_000 for index in range(40)}

    with pytest.raises(SkillHookRuntimeError) as caught:
        await MiddlewarePipeline(
            [_middleware(manager, sandbox, RealReceiptObserver(tmp_path / "receipts"))]
        ).before_tool_batch([ToolCallRequest("publish", arguments)], _context())

    assert caught.value.code == "skill_hook_context_invalid"
    assert sandbox.shell_count == 0


@pytest.mark.asyncio
async def test_post_tool_validation_failure_reports_irreversible_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "true")
    root, manager = _write_skill(
        tmp_path,
        [
            _hook(
                hook_id="verify_result",
                event="post_tool_use",
                mode="validation",
                tool_names=["publish"],
            )
        ],
    )
    sandbox = FakeSandboxProvider(
        root,
        lambda _context, _argv: {
            "version": "modelmirror-hook-result-v1",
            "outputs": [
                {
                    "type": "validation",
                    "code": "publish.invalid",
                    "passed": False,
                    "message": "Invalid",
                }
            ],
        },
    )
    pipeline = MiddlewarePipeline(
        [_middleware(manager, sandbox, RealReceiptObserver(tmp_path / "receipts"))]
    )
    provider_called = False

    async def handler(_request: ToolCallRequest) -> ToolCallResponse:
        nonlocal provider_called
        provider_called = True
        return ToolCallResponse(output="published")

    with pytest.raises(SkillHookRuntimeError) as caught:
        await pipeline.run_tool_call(
            ToolCallRequest("publish", {}), handler, _context()
        )
    assert caught.value.code == "skill_hook_validation_failed"
    assert "not rolled back" in str(caught.value)
    assert provider_called is True


def test_typed_hook_runtime_is_enabled_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SKILL_PLUGIN_HOOK_V2_ENABLED", raising=False)
    root, manager = _write_skill(
        tmp_path,
        [_hook(hook_id="note", event="session_start", mode="annotation")],
    )
    middleware = _middleware(
        manager,
        FakeSandboxProvider(root, lambda _context, _argv: {}),
        RealReceiptObserver(tmp_path / "receipts"),
    )
    assert middleware is not None


def test_typed_hook_runtime_has_an_explicit_false_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "false")
    root, manager = _write_skill(
        tmp_path,
        [_hook(hook_id="note", event="session_start", mode="annotation")],
    )
    with pytest.raises(SkillHookRuntimeError) as caught:
        _middleware(
            manager,
            FakeSandboxProvider(root, lambda _context, _argv: {}),
            RealReceiptObserver(tmp_path / "receipts"),
        )
    assert caught.value.code == "skill_hook_v2_disabled"


def test_typed_hook_runtime_rejects_an_empty_skill_binding() -> None:
    with pytest.raises(SkillHookRuntimeError) as caught:
        typed_hook_skill_ids(
            RuntimeMiddlewareSpec(
                node_id="hooks",
                middleware_id="plugin_hooks",
                config={"hook_mode": "typed_v2", "skill_ids": ""},
            )
        )
    assert caught.value.code == "skill_hook_manifest_invalid"


@pytest.mark.asyncio
async def test_session_end_runs_once_when_later_finalization_reports_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "true")
    root, manager = _write_skill(
        tmp_path,
        [_hook(hook_id="end_check", event="session_end", mode="validation")],
    )
    sandbox = FakeSandboxProvider(
        root,
        lambda _context, _argv: {
            "version": "modelmirror-hook-result-v1",
            "outputs": [
                {
                    "type": "validation",
                    "code": "session.complete",
                    "passed": True,
                    "message": "OK",
                }
            ],
        },
    )
    pipeline = MiddlewarePipeline(
        [_middleware(manager, sandbox, RealReceiptObserver(tmp_path / "receipts"))]
    )
    context = _context()

    await pipeline.after_agent(
        {"status": "completed", "output_length": 2, "output_digest": "b" * 64},
        context,
    )
    await pipeline.after_agent({"status": "error"}, context)

    assert sandbox.shell_count == 1


@pytest.mark.asyncio
async def test_gold_hook_executes_through_real_sandbox_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SKILL_PLUGIN_HOOK_V2_ENABLED", "true")
    fixture_source = (
        Path(__file__).parent / "fixtures" / "skill_hook_v2_gold"
    ).resolve()
    fixture = tmp_path / "gold-skill"
    shutil.copytree(fixture_source, fixture)
    package = {
        path.relative_to(fixture).as_posix(): path.read_bytes()
        for path in fixture.rglob("*")
        if path.is_file()
    }
    package_digest = compute_skill_content_digest(package)

    class GoldManager:
        def __init__(self) -> None:
            self.lifecycle_store = SimpleNamespace(
                require_version=lambda version_id: SimpleNamespace(
                    skill_id=SKILL_ID,
                    version_id=version_id,
                    package_digest=package_digest,
                    source_kind="workspace_draft",
                    trust_fingerprint=None,
                )
            )

        def get_skill_directory(
            self, skill_id: str, *, version_id: str | None = None
        ) -> Path:
            assert skill_id == SKILL_ID
            assert version_id == VERSION_ID
            return fixture

        def list_installed_skills(self):
            return [SimpleNamespace(skill_id=SKILL_ID)]

        def require_activation(self, _skill_id: str, **_kwargs: Any) -> None:
            return None

    class EngineClient:
        def __init__(self, engine: SandboxEngine) -> None:
            self.engine = engine

        async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
            return self.engine.dispatch(payload)

        async def health(
            self, *, required_profile: str | None = None
        ) -> dict[str, Any]:
            result = self.engine.dispatch(
                {"action": "health", "workspace_id": "health"}
            )
            assert required_profile in result["profiles"]
            return result

    manager = GoldManager()
    sandbox_root = tmp_path / "sandbox"
    provider = SandboxToolsetProvider(
        SandboxWorkspaceStore(
            tmp_path / "sandbox-store",
            workspace_root=sandbox_root,
        ),
        EngineClient(SandboxEngine(sandbox_root, require_landlock=True)),
        skill_manager=manager,
    )
    observer = RealReceiptObserver(tmp_path / "receipts")
    middleware = build_plugin_hooks_v2_middleware(
        RuntimeMiddlewareSpec(
            node_id="hooks",
            middleware_id="plugin_hooks",
            config={"hook_mode": "typed_v2", "skill_ids": SKILL_ID},
        ),
        skill_manager=manager,
        sandbox_provider=provider,
        application_observer=observer,
    )
    pipeline = MiddlewarePipeline([middleware])
    provider_called = False
    initial_context = _context()

    async def handler(_request: ToolCallRequest) -> ToolCallResponse:
        nonlocal provider_called
        provider_called = True
        return ToolCallResponse(output="written")

    with pytest.raises(SkillHookRuntimeError) as caught:
        await pipeline.run_tool_call(
            ToolCallRequest(
                "sandbox_write_file", {"path": "work/release.exe", "content": "x"}
            ),
            handler,
            initial_context,
        )
    assert caught.value.code == "skill_hook_denied"
    assert provider_called is False
    receipts = observer.store.list_receipts(run_id="run-1", skill_id=SKILL_ID)
    assert receipts[0].hook_evidence[0].code == "release_extension_denied"
    markers = list(sandbox_root.glob("*/.modelmirror/profile.json"))
    assert len(markers) == 1
    marker = json.loads(markers[0].read_text(encoding="utf-8"))
    assert marker["profile"] == "skill_authoring_v1"
    workspace = markers[0].parents[1]
    assert (
        workspace / "skills" / "authoring-resource" / "scripts" / "check_release.py"
    ).read_bytes() == (fixture / "scripts" / "check_release.py").read_bytes()
    assert all(
        path.read_text(encoding="utf-8") == "{}"
        for path in (workspace / "work" / ".modelmirror-hooks").rglob("*.json")
    )

    recovered_context = _context()
    await pipeline.run_tool_call(
        ToolCallRequest(
            "sandbox_write_file", {"path": "work/release.txt", "content": "x"}
        ),
        handler,
        recovered_context,
    )
    assert provider_called is True
    assert len(list(sandbox_root.glob("*/.modelmirror/profile.json"))) == 1
    provider_called = False

    (fixture / "scripts" / "check_release.py").write_text(
        "print('tampered')\n", encoding="utf-8"
    )
    with pytest.raises(SkillHookRuntimeError) as stale:
        await pipeline.run_tool_call(
            ToolCallRequest(
                "sandbox_write_file", {"path": "work/release.txt", "content": "x"}
            ),
            handler,
            _context(),
        )
    assert stale.value.code == "skill_hook_contract_stale"
    assert provider_called is False

    await pipeline.after_agent({"status": "error"}, recovered_context)
    assert list(sandbox_root.glob("*/.modelmirror/profile.json")) == []
