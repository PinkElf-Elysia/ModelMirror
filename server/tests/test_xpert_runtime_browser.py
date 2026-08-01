from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from server.xpert_runtime import (
    BrowserSessionStore,
    BrowserToolsetProvider,
    BrowserValidationError,
    RuntimeApprovalStore,
    RuntimeInterrupt,
    RuntimeToolCall,
    RuntimeToolError,
)
from server.xpert_runtime import browser_api


class FakeBrowserClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(dict(payload))
        action = str(payload.get("action") or "")
        if action == "health":
            return {"ok": True, "chromium": True, "policy": "public_only"}
        if action == "ensure_session":
            return {"ok": True, "session": {"url": "", "domain": "", "title": ""}}
        if action == "close_session":
            return {"ok": True, "status": "closed"}
        return {
            "ok": True,
            "page": {
                "url": "https://docs.example.com/guide?access_token=private#section",
                "domain": "docs.example.com",
                "title": "Public guide",
            },
        }


def browser_metadata(**extra: Any) -> dict[str, Any]:
    return {
        "task_id": "task-1",
        "run_id": "run-1",
        "node_id": "agent-1",
        "node_title": "Research agent",
        "iteration": 1,
        "browser_config": {
            "networkPolicy": "public_with_domain_approval",
            "persistSession": True,
            "maxPages": 3,
            "maxActions": 100,
        },
        **extra,
    }


def test_browser_store_persists_safe_metadata_and_hides_physical_path(
    tmp_path: Path,
) -> None:
    store = BrowserSessionStore(tmp_path / "runtime", data_root=tmp_path / "data")
    session = store.get_or_create_session(
        scope_type="workflow",
        scope_id="task-1:agent-1",
        node_id="agent-1",
    )
    store.update_page(
        session.session_id,
        url="https://user:secret@example.com/report?token=private#result",
        title="Report",
    )
    store.grant_domain(session.session_id, "example.com", operator="tester")
    artifact = store.register_artifact(
        artifact_id="artifact-1",
        session_id=session.session_id,
        filename="report.txt",
        relative_path=f"{session.session_id}/downloads/report.txt",
        size_bytes=12,
        content_type="text/plain",
        kind="download",
    )

    public = store.session_payload(store.get_session(session.session_id))
    serialized = json.dumps(public)
    assert public["current_url"] == "https://example.com/report"
    assert "secret" not in serialized
    assert "token" not in serialized
    assert "relative_path" not in store.artifact_payload(artifact)

    reloaded = BrowserSessionStore(tmp_path / "runtime", data_root=tmp_path / "data")
    assert reloaded.get_session(session.session_id).current_url == "https://example.com/report"
    assert reloaded.has_domain_grant(session.session_id, "example.com")


def test_browser_store_enforces_action_limit(tmp_path: Path) -> None:
    store = BrowserSessionStore(tmp_path, data_root=tmp_path / "data")
    session = store.get_or_create_session(
        scope_type="workflow",
        scope_id="task-1:agent-1",
        node_id="agent-1",
        max_actions=1,
    )
    store.start_operation(
        "operation-1",
        session_id=session.session_id,
        tool_name="browser_read",
    )
    with pytest.raises(BrowserValidationError, match="limit"):
        store.start_operation(
            "operation-2",
            session_id=session.session_id,
            tool_name="browser_read",
        )


def test_browser_store_marks_interrupted_operation_failed_after_restart(
    tmp_path: Path,
) -> None:
    storage_dir = tmp_path / "runtime"
    data_root = tmp_path / "data"
    store = BrowserSessionStore(storage_dir, data_root=data_root)
    session = store.get_or_create_session(
        scope_type="workflow",
        scope_id="task-1:agent-1",
        node_id="agent-1",
    )
    store.start_operation(
        "operation-interrupted",
        session_id=session.session_id,
        tool_name="browser_click",
    )

    restored = BrowserSessionStore(storage_dir, data_root=data_root)
    operation = restored.list_operations(session.session_id)[0]

    assert operation.status == "failed"
    assert "service restart" in str(operation.error)


@pytest.mark.asyncio
async def test_browser_navigate_requires_session_domain_approval_and_reuses_it(
    tmp_path: Path,
) -> None:
    approvals = RuntimeApprovalStore(tmp_path / "approvals")
    client = FakeBrowserClient()
    store = BrowserSessionStore(tmp_path / "runtime", data_root=tmp_path / "data")
    provider = BrowserToolsetProvider(store, client, approvals)
    call = RuntimeToolCall(
        "browser_navigate",
        {"url": "https://docs.example.com/guide?token=private"},
        browser_metadata(),
    )

    with pytest.raises(RuntimeInterrupt) as interrupted:
        await provider.call_tool(call)
    approval = approvals.require(interrupted.value.approval_id)
    assert approval.request_type == "browser_domain"
    assert approval.arguments == {"domain": "docs.example.com", "scheme": "https"}
    assert client.requests == []

    decided = approvals.decide(
        approval.approval_id,
        revision=approval.revision,
        decision="approve",
        operator="tester",
    )
    result = await provider.call_tool(
        RuntimeToolCall(
            call.tool_name,
            call.arguments,
            {
                **call.metadata,
                "resolved_approval": approvals.serialize(decided),
            },
        )
    )
    assert "Public guide" in result.output
    session = store.list_sessions(scope_type="workflow")[0]
    assert store.has_domain_grant(session.session_id, "docs.example.com")
    assert session.current_url == "https://docs.example.com/guide"

    repeat = await provider.call_tool(
        RuntimeToolCall(
            "browser_navigate",
            {"url": "https://docs.example.com/other"},
            {**browser_metadata(iteration=2)},
        )
    )
    assert "Public guide" in repeat.output
    assert len(
        [
            item
            for item in approvals.list_requests()
            if item.request_type == "browser_domain"
        ]
    ) == 1


@pytest.mark.asyncio
async def test_browser_provider_rejects_public_app_and_blocked_domain(
    tmp_path: Path,
) -> None:
    provider = BrowserToolsetProvider(
        BrowserSessionStore(tmp_path / "runtime", data_root=tmp_path / "data"),
        FakeBrowserClient(),
        RuntimeApprovalStore(tmp_path / "approvals"),
    )
    with pytest.raises(RuntimeToolError, match="Public Xpert Apps"):
        await provider.call_tool(
            RuntimeToolCall(
                "browser_read",
                {},
                browser_metadata(runtime_run_type="xpert_app"),
            )
        )
    with pytest.raises(RuntimeToolError) as blocked:
        await provider.call_tool(
            RuntimeToolCall(
                "browser_navigate",
                {"url": "https://blocked.example.com"},
                browser_metadata(
                    browser_config={
                        "networkPolicy": "public_with_domain_approval",
                        "blockedDomains": "blocked.example.com",
                    }
                ),
            )
        )
    assert blocked.value.code == "browser_domain_blocked"


@pytest.mark.asyncio
async def test_browser_artifact_upload_is_limited_to_same_private_scope(
    tmp_path: Path,
) -> None:
    store = BrowserSessionStore(tmp_path / "runtime", data_root=tmp_path / "data")
    owner = store.get_or_create_session(
        scope_type="workflow",
        scope_id="task-1:agent-1",
        node_id="agent-1",
    )
    store.register_artifact(
        artifact_id="download-1",
        session_id=owner.session_id,
        filename="report.txt",
        relative_path=f"{owner.session_id}/downloads/report.txt",
        size_bytes=20,
        content_type="text/plain",
        kind="download",
    )
    client = FakeBrowserClient()
    provider = BrowserToolsetProvider(
        store,
        client,
        RuntimeApprovalStore(tmp_path / "approvals"),
    )
    await provider.call_tool(
        RuntimeToolCall(
            "browser_upload_file",
            {"ref": "r1", "path": "browser-artifact:download-1"},
            browser_metadata(),
        )
    )
    assert any(
        request.get("browser_artifact_relative_path")
        for request in client.requests
    )

    with pytest.raises(RuntimeToolError) as denied:
        await provider.call_tool(
            RuntimeToolCall(
                "browser_upload_file",
                {"ref": "r1", "path": "browser-artifact:download-1"},
                browser_metadata(task_id="task-2", run_id="run-2"),
            )
        )
    assert denied.value.code == "browser_upload_denied"


@pytest.mark.asyncio
async def test_browser_api_returns_safe_capabilities_and_session_payload(
    tmp_path: Path,
) -> None:
    store = BrowserSessionStore(tmp_path / "runtime", data_root=tmp_path / "data")
    client = FakeBrowserClient()
    previous_store = browser_api._store
    previous_client = browser_api._client
    try:
        browser_api.configure_runtime_browser(store, client)
        session = store.get_or_create_session(
            scope_type="goal",
            scope_id="goal-1:step-1",
            node_id="agent-1",
        )
        store.update_page(
            session.session_id,
            url="https://example.com/path?api_key=private",
            title="Example",
        )

        capabilities = await browser_api.browser_capabilities()
        listing = await browser_api.list_browser_sessions(scope_type="goal", limit=10)
        serialized = json.dumps({"capabilities": capabilities, "listing": listing})
        assert capabilities["available"] is True
        assert listing["total"] == 1
        assert "api_key=private" not in serialized
        assert "https://example.com/path" in serialized
        assert "api_key" not in serialized
        assert "/api/runtime/browser/capabilities" in {
            route.path for route in browser_api.router.routes
        }
    finally:
        browser_api._store = previous_store
        browser_api._client = previous_client
