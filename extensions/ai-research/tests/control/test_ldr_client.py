from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from ai_research_control.ldr_client import (
    AGENT_TOOL_KEYS,
    ENABLED_ACADEMIC_TOOLS,
    LdrAuthenticationError,
    LdrClient,
    LdrProtocolError,
    LdrSessionExpired,
)


LDR_V1_10_5_ENGINE_KEYS = {
    "arxiv",
    "brave",
    "ddg",
    "elasticsearch",
    "exa",
    "github",
    "google_pse",
    "guardian",
    "gutenberg",
    "mojeek",
    "nasa_ads",
    "openalex",
    "openlibrary",
    "paperless",
    "pubchem",
    "pubmed",
    "scaleserp",
    "searxng",
    "semantic_scholar",
    "serpapi",
    "serper",
    "sofya",
    "stackexchange",
    "tavily",
    "tinyfish",
    "wayback",
    "wikinews",
    "wikipedia",
    "zenodo",
}


class Response:
    def __init__(
        self,
        status: int,
        *,
        value: object | None = None,
        text: str = "",
        lines: list[str] | None = None,
    ) -> None:
        self.status_code = status
        self._value = value
        self._content = text.encode("utf-8") if value is None else json.dumps(value).encode()
        self.headers = {"content-length": str(len(self._content))}
        self._lines = lines or []

    @property
    def content(self) -> bytes:
        return self._content

    @property
    def text(self) -> str:
        return self._content.decode("utf-8")

    def json(self) -> object:
        if self._value is None:
            raise ValueError
        return self._value

    def iter_lines(self, *, decode_unicode: bool) -> list[str]:
        assert decode_unicode is True
        return self._lines


class ScriptedSession:
    def __init__(self, responses: list[Response]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.trust_env = True

    def close(self) -> None:
        return None

    def _next(self, method: str, url: str, **kwargs: Any) -> Response:
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> Response:
        return self._next("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Response:
        return self._next("POST", url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> Response:
        return self._next(method, url, **kwargs)


def unlocked_client(script: list[Response]) -> tuple[LdrClient, ScriptedSession]:
    session = ScriptedSession(
        [
            Response(200, text='<input value="login-csrf" name="csrf_token">'),
            Response(302),
            Response(200, value={"authenticated": True, "username": "researcher"}),
            Response(200, value={"csrf_token": "api-csrf"}),
            *script,
        ]
    )
    client = LdrClient(
        "http://ai-research-ldr:5000",
        session=session,  # type: ignore[arg-type]
        session_factory=lambda: session,  # type: ignore[return-value]
    )
    client.unlock("researcher", "correct horse battery staple")
    return client, session


def test_unlock_and_fixed_profile_use_real_csrf_contract() -> None:
    first = ScriptedSession([])
    active = ScriptedSession(
        [
            Response(200, text='<input value="login-csrf" name="csrf_token">'),
            Response(302),
            Response(200, value={"authenticated": True, "username": "researcher"}),
            Response(200, value={"csrf_token": "api-csrf"}),
            Response(200, value={"csrf_token": "fresh-api-csrf"}),
            Response(200, value={"status": "success"}),
        ]
    )
    client = LdrClient(
        "http://ai-research-ldr:5000",
        session=first,  # type: ignore[arg-type]
        session_factory=lambda: active,  # type: ignore[return-value]
    )

    assert client.unlock("researcher", "not-stored-after-call") == {
        "status": "ready",
        "username": "researcher",
    }
    client.configure_fixed_profile(
        model_id="fixed/model",
        bridge_url="http://host.docker.internal:8000/api/ai-research/v1",
        bridge_token="bridge-only-token",
    )

    method, url, kwargs = active.calls[-1]
    assert (method, url) == (
        "POST",
        "http://ai-research-ldr:5000/settings/save_all_settings",
    )
    assert kwargs["headers"]["X-CSRF-Token"] == "fresh-api-csrf"
    profile = kwargs["json"]
    assert profile["search.tool"] == "openalex"
    assert profile["search.search_strategy"] == "langgraph-agent"
    assert (
        profile[
            "search.engine.web.openalex.default_params.enable_llm_relevance_filter"
        ]
        is False
    )
    assert profile["policy.egress_scope"] == "public_only"
    assert profile["langgraph_agent.max_iterations"] == 10
    assert profile["langgraph_agent.max_sub_iterations"] == 3
    assert profile["langgraph_agent.include_sub_research"] is False
    assert profile["langgraph_agent.max_subagent_workers"] == 1
    assert profile["embeddings.require_local"] is True
    assert profile["local_search_embedding_model"] == (
        "/data/models/sentence-transformers/all-MiniLM-L6-v2"
    )
    enabled = {
        key.split(".")[-2]
        for key, value in profile.items()
        if key.endswith(".agent_enabled") and value is True
    }
    configured = {
        key.split(".")[-2]
        for key in profile
        if key.endswith(".agent_enabled")
    }
    assert set(AGENT_TOOL_KEYS) == LDR_V1_10_5_ENGINE_KEYS
    assert configured == LDR_V1_10_5_ENGINE_KEYS
    assert enabled == ENABLED_ACADEMIC_TOOLS
    assert "not-stored-after-call" not in repr(client.__dict__)


def test_start_is_fixed_and_response_loss_can_reconcile_by_metadata() -> None:
    run_id = "lr_" + "a" * 32
    client, session = unlocked_client(
        [
            Response(200, value={"csrf_token": "fresh-api-csrf"}),
            Response(200, value={"status": "success", "research_id": "ldr-1"}),
            Response(
                200,
                value={
                    "status": "success",
                    "items": [
                        {
                            "id": "ldr-1",
                            "metadata": {"modelmirror_literature_run_id": run_id},
                        }
                    ],
                },
            ),
        ]
    )
    research_id, raw_status = client.start_research(
        question="Agent 评测如何确保可复现？",
        control_run_id=run_id,
        model_id="fixed/model",
        bridge_url="http://host.docker.internal:8000/api/ai-research/v1",
        collection_id="8f76dc23-8f1f-4c19-bca5-f055920a37c7",
    )
    assert research_id == "ldr-1"
    assert raw_status == "success"
    payload = session.calls[-1][2]["json"]
    assert payload["search_engine"] == "openalex"
    assert payload["strategy"] == "langgraph-agent"
    assert payload["max_results"] == 15
    assert payload["iterations"] == 2
    assert payload["questions_per_iteration"] == 3
    assert set(payload) >= {"metadata", "policy_egress_scope"}
    assert payload["metadata"]["modelmirror_collection_id"] == (
        "8f76dc23-8f1f-4c19-bca5-f055920a37c7"
    )
    assert client.find_research_by_run_id(run_id) == "ldr-1"


def test_export_refreshes_csrf_after_a_long_running_research() -> None:
    client, session = unlocked_client(
        [
            Response(200, value={"csrf_token": "rotated-api-csrf"}),
            Response(200, text="quarto-zip"),
        ]
    )

    assert client.export("ldr-1", "quarto") == b"quarto-zip"
    refresh_method, refresh_url, _ = session.calls[-2]
    export_method, export_url, export_kwargs = session.calls[-1]
    assert (refresh_method, refresh_url) == (
        "GET",
        "http://ai-research-ldr:5000/auth/csrf-token",
    )
    assert (export_method, export_url) == (
        "POST",
        "http://ai-research-ldr:5000/api/v1/research/ldr-1/export/quarto",
    )
    assert export_kwargs["headers"]["X-CSRF-Token"] == "rotated-api-csrf"


def test_failed_login_and_expired_session_fail_closed() -> None:
    rejected = ScriptedSession(
        [
            Response(200, text='<input name="csrf_token" value="token">'),
            Response(401),
        ]
    )
    client = LdrClient(
        "http://ai-research-ldr:5000",
        session=ScriptedSession([]),  # type: ignore[arg-type]
        session_factory=lambda: rejected,  # type: ignore[return-value]
    )
    with pytest.raises(LdrAuthenticationError):
        client.unlock("researcher", "wrong-password")

    expired, _ = unlocked_client([Response(401, value={"error": "expired"})])
    with pytest.raises(LdrSessionExpired):
        expired.collections()
    assert expired.username is None


def test_index_requires_terminal_sse_event() -> None:
    client, _ = unlocked_client(
        [Response(200, lines=['data: {"type":"progress"}'])]
    )
    with pytest.raises(LdrProtocolError, match="terminal"):
        client.index_collection("collection-1")


def test_unsafe_research_ids_never_enter_ldr_paths() -> None:
    client, session = unlocked_client([])
    with pytest.raises(LdrProtocolError, match="research id"):
        client.research_status("../settings")
    with pytest.raises(LdrProtocolError, match="research id"):
        client.export("id?format=secret", "quarto")
    assert all("../settings" not in url for _, url, _ in session.calls)
