from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from ai_research_control.ldr_client import LdrProtocolError
from ai_research_control.service import ResearchService


class TerminalRaceStore:
    def get(self, run_id: str) -> dict[str, object]:
        return {"run_id": run_id, "phase": "running", "case_id": "success"}

    def request_cancel(self, run_id: str) -> dict[str, object]:
        return {
            "run_id": run_id,
            "phase": "terminal",
            "outcome": "success",
            "case_id": "success",
            "cancel_requested": False,
        }


class UnexpectedWorker:
    async def cancel(self, run_id: str) -> dict[str, object]:
        raise AssertionError(f"terminal run was sent to Worker: {run_id}")


def test_cancel_race_does_not_send_terminal_run_to_worker() -> None:
    service = object.__new__(ResearchService)
    service.store = TerminalRaceStore()
    service.worker = UnexpectedWorker()

    result = asyncio.run(service.cancel("ar0_terminal_race"))

    assert result["phase"] == "terminal"
    assert result["outcome"] == "success"
    assert result["cancel_requested"] is False


class BridgeResponse:
    status = 200

    def __init__(self, value: dict[str, object]) -> None:
        self.content = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.content[:limit]


def test_model_bridge_probe_is_exact_and_uses_no_proxy(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Opener:
        def open(self, request, timeout: float):
            captured["url"] = request.full_url
            captured["authorization"] = request.headers["Authorization"]
            captured["timeout"] = timeout
            return BridgeResponse(
                {"data": [{"id": "provider/fixed", "object": "model"}]}
            )

    def opener(proxy):
        captured["proxies"] = proxy.proxies
        return Opener()

    monkeypatch.setattr("ai_research_control.service.build_opener", opener)
    service = object.__new__(ResearchService)
    service.settings = SimpleNamespace(
        model_bridge_url="http://host.docker.internal:8000/api/ai-research/v1",
        model_bridge_token="bridge-secret",
        literature_model_id="provider/fixed",
    )
    service._probe_model_bridge()

    assert captured == {
        "proxies": {},
        "url": "http://host.docker.internal:8000/api/ai-research/v1/models",
        "authorization": "Bearer bridge-secret",
        "timeout": 5.0,
    }


def test_model_bridge_probe_rejects_another_or_multiple_models(monkeypatch) -> None:
    class Opener:
        def open(self, request, timeout: float):
            return BridgeResponse(
                {"data": [{"id": "provider/fixed"}, {"id": "provider/other"}]}
            )

    monkeypatch.setattr(
        "ai_research_control.service.build_opener", lambda proxy: Opener()
    )
    service = object.__new__(ResearchService)
    service.settings = SimpleNamespace(
        model_bridge_url="http://host.docker.internal:8000/api/ai-research/v1",
        model_bridge_token="bridge-secret",
        literature_model_id="provider/fixed",
    )
    try:
        service._probe_model_bridge()
    except RuntimeError as exc:
        assert "not eligible" in str(exc)
    else:
        raise AssertionError("multiple models must fail closed")


def test_literature_capability_degrades_on_malformed_ldr_session() -> None:
    class MalformedLdr:
        def probe(self):
            return True

        def session_status(self):
            raise LdrProtocolError("malformed session")

    service = object.__new__(ResearchService)
    service.settings = SimpleNamespace(
        model_bridge_url="http://host.docker.internal:8000/api/ai-research/v1",
        model_bridge_token="bridge-secret",
        literature_model_id="provider/fixed",
    )
    service.ldr = MalformedLdr()
    service._probe_model_bridge = lambda: None

    result = asyncio.run(service.literature_capability())

    assert result["status"] == "not_ready"
    assert result["serviceStatus"] == "not_ready"
    assert result["sessionStatus"] == "locked"


def test_literature_capability_fails_closed_when_model_qualification_is_lost() -> None:
    class ReadyLdr:
        def probe(self):
            return True

        def session_status(self):
            return {"status": "ready", "username": "researcher"}

    service = object.__new__(ResearchService)
    service.settings = SimpleNamespace(
        model_bridge_url="http://host.docker.internal:8000/api/ai-research/v1",
        model_bridge_token="bridge-secret",
        literature_model_id="provider/fixed",
    )
    service.ldr = ReadyLdr()
    service._probe_model_bridge = lambda: (_ for _ in ()).throw(
        RuntimeError("provider qualification is not ready")
    )

    result = asyncio.run(service.literature_capability())

    assert result["status"] == "not_ready"
    assert result["serviceStatus"] == "ready"
    assert result["sessionStatus"] == "ready"
    assert result["modelBridgeStatus"] == "not_ready"
