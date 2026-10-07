from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from server import main


@pytest.mark.parametrize("error", [TimeoutError(), httpx.ReadTimeout(""), Exception(), Exception("   ")])
def test_candidate_failure_is_visible_without_replay(monkeypatch, error):
    monkeypatch.setattr(main, "rate_limit_or_raise", lambda _ip: None)
    monkeypatch.setattr(main, "get_model_router_service", lambda: None)
    monkeypatch.setattr(main.ManagedMetaAgentGateway, "for_router", staticmethod(
        lambda _router: SimpleNamespace(routing_mode=lambda: "legacy")
    ))
    monkeypatch.setattr(main, "get_llm_gateway_config", lambda: ("http://offline", "unused"))
    monkeypatch.setattr(main, "build_meta_planner_capability_snapshot", lambda: None)
    registry = SimpleNamespace(
        create_run=AsyncMock(return_value=SimpleNamespace(run_id="run_timeout_test")),
        record_checkpoint=AsyncMock(), update_run=AsyncMock(),
    )
    monkeypatch.setattr(main, "run_registry", registry)
    generate = AsyncMock(side_effect=error)
    monkeypatch.setattr(main.MetaPlannerV2Service, "generate", generate)
    client = TestClient(main.app, raise_server_exceptions=False)
    response = client.post("/api/meta-agent/generate-xpert-candidate", json={
        "goal": "生成一个受限中文候选", "planner_model_id": "offline/test",
        "default_agent_model_id": "offline/test", "scope": {},
    })
    assert response.status_code == 500
    message = response.json()["error"]
    assert message.strip()
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        assert "超时" in message
        assert "未知" in message
    assert "自动重试" in message
    assert response.json()["run_id"] == "run_timeout_test"
    generate.assert_awaited_once()
    registry.update_run.assert_awaited_once_with("run_timeout_test", status="failed", error=message[:500])
