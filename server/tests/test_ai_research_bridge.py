from __future__ import annotations

import asyncio
import copy
import json
import hashlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import server.model_router.ai_research_bridge as bridge
from server.model_router.ai_research_bridge import router, stable_service


MODEL_ID = "provider/fixed-research-model"
HYPOTHESIS_MODEL_ID = "provider/fixed-hypothesis-model"
TOKEN = "test-ai-research-service-token"
P2R_TOKEN = "test-ai-research-p2r-service-token"
LOCKED_TEXT_PHASE_PROMPT = "Locked ResearchStudio phase prompt. Return a JSON object."
TEXT_PHASE = "researchstudio.phase0.intent"
COHERENCE_PHASE = "researchstudio.phase2.coherence"
QUALIFICATION_RUN_ID = "p2rq_" + "a" * 32
PREVIOUS_RECEIPT_SHA256 = "b" * 64


@dataclass
class FakeDispatch:
    run_id: str = "chatrun_test"
    target: object = None
    authorized: object = None


class FakeTransport:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.last_client: httpx.AsyncClient | None = None

    def client_kwargs(self) -> dict[str, object]:
        return {
            "transport": httpx.MockTransport(self.handler),
            "trust_env": False,
            "follow_redirects": False,
        }

    def build_authorized_stream_request(
        self,
        client: httpx.AsyncClient,
        target: object,
        authorized: object,
        payload: dict[str, Any],
        *,
        headers: dict[str, str],
    ) -> httpx.Request:
        return client.build_request(
            "POST", "https://provider.example/v1/chat/completions", json=payload, headers=headers
        )

    async def send_authorized_stream(
        self, client: httpx.AsyncClient, request: httpx.Request
    ) -> httpx.Response:
        self.last_client = client
        return await client.send(request, stream=True)


class FakeStable:
    def __init__(self, handler) -> None:
        self.transport = FakeTransport(handler)
        self.completed: list[dict[str, Any]] = []
        self.dispatched = 0
        self.capabilities: list[str] = []
        self.scoped_capabilities: list[str] = []
        self.scoped_requirements: list[tuple[str, ...]] = []
        self.ready = True
        self.readiness_reason: str | None = None
        self.readiness_calls: list[tuple[str, str]] = []
        self.readiness_overrides: dict[tuple[str, str], tuple[bool, str | None]] = {}
        self.service_resolutions = 0

    def readiness(self, model_id: str, capability: str):
        assert model_id in {MODEL_ID, HYPOTHESIS_MODEL_ID}
        self.readiness_calls.append((model_id, capability))
        if (model_id, capability) in self.readiness_overrides:
            return self.readiness_overrides[(model_id, capability)]
        return self.ready, self.readiness_reason

    async def begin(self, model_id: str, capability: str):
        assert model_id in {MODEL_ID, HYPOTHESIS_MODEL_ID}
        self.capabilities.append(capability)
        return SimpleNamespace(
            intercepted=True,
            dispatch=FakeDispatch(),
            error_code=None,
        )

    def readiness_scoped_certified(self, model_id: str, capability: str):
        return self.readiness(model_id, capability)

    async def begin_scoped_certified(
        self,
        model_id: str,
        capability: str,
        *,
        required_capabilities: tuple[str, ...] = (),
    ):
        assert model_id == HYPOTHESIS_MODEL_ID
        self.scoped_capabilities.append(capability)
        self.scoped_requirements.append(required_capabilities)
        return SimpleNamespace(
            intercepted=True,
            dispatch=FakeDispatch(),
            error_code=None,
        )

    def mark_dispatched(self, dispatch: FakeDispatch) -> None:
        self.dispatched += 1

    def complete(self, dispatch: FakeDispatch, **fields: Any) -> None:
        self.completed.append(fields)

    @staticmethod
    def classify_http_failure(status_code: int) -> tuple[str, str, bool]:
        return "transient_failure", f"provider_chat_http_{status_code}", False


class CancelOnReadStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.close_calls = 0

    async def __aiter__(self):
        raise asyncio.CancelledError
        yield b""  # pragma: no cover - keeps this an async iterator

    async def aclose(self) -> None:
        self.close_calls += 1


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = chunks
        self.close_calls = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.close_calls += 1


class UnexpectedReadStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.close_calls = 0

    async def __aiter__(self):
        raise RuntimeError("unexpected response read failure")
        yield b""  # pragma: no cover - keeps this an async iterator

    async def aclose(self) -> None:
        self.close_calls += 1


class UnexpectedCloseStream(ChunkedStream):
    async def aclose(self) -> None:
        self.close_calls += 1
        raise RuntimeError("unexpected response close failure")


class HardFailureStable(FakeStable):
    @staticmethod
    def classify_http_failure(status_code: int) -> tuple[str, str, bool]:
        assert status_code == 401
        return "hard_failure", "provider_chat_http_401", True


def app_for(stable: FakeStable) -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    def resolve_stable() -> FakeStable:
        stable.service_resolutions += 1
        return stable

    app.dependency_overrides[stable_service] = resolve_stable
    return app


async def call_direct(stable: FakeStable):
    return await bridge.chat_completions(
        bridge.ChatCompletionRequest.model_validate(valid_payload()),
        bridge_auth=bridge.ChatBridgeAuthorization(
            settings=bridge.BridgeSettings(
                enabled=True,
                token=TOKEN,
                p2r_token=P2R_TOKEN,
                literature_model_id=MODEL_ID,
                hypothesis_model_id="",
                p2r_enabled=False,
                p2r_tools_enabled=False,
            ),
            phase=None,
        ),
        stable=stable,
    )


def direct_stream(
    stable: FakeStable, stream: httpx.AsyncByteStream
) -> tuple[httpx.AsyncClient, Any]:
    client = httpx.AsyncClient(trust_env=False)
    stable.transport.last_client = client
    response = httpx.Response(200, stream=stream)
    return client, bridge._stream_response(
        stable=stable,
        dispatch=FakeDispatch(),
        response=response,
        client=client,
        requested_model=MODEL_ID,
        started=0.0,
        allow_tool_calls=False,
    )


def enable(monkeypatch) -> None:
    monkeypatch.setenv("AI_RESEARCH_S2S_ENABLED", "true")
    monkeypatch.setenv("AI_RESEARCH_S2S_TOKEN", TOKEN)
    monkeypatch.setenv("AI_RESEARCH_LITERATURE_MODEL_ID", MODEL_ID)


def enable_hypothesis(monkeypatch) -> None:
    enable(monkeypatch)
    monkeypatch.setenv("AI_RESEARCH_P2R_S2S_TOKEN", P2R_TOKEN)
    monkeypatch.setenv("AI_RESEARCH_HYPOTHESIS_MODEL_ID", HYPOTHESIS_MODEL_ID)
    monkeypatch.setenv("AI_RESEARCH_P2R_ENABLED", "true")
    monkeypatch.setenv("AI_RESEARCH_P2R_TOOLS_ENABLED", "true")


def bind_phase_prompt(monkeypatch, phase: str, prompt: str) -> None:
    contracts = copy.deepcopy(bridge.P2R_PHASE_CONTRACTS)
    contracts[phase]["promptSha256"] = hashlib.sha256(
        prompt.encode("utf-8")
    ).hexdigest()
    monkeypatch.setattr(bridge, "P2R_PHASE_CONTRACTS", contracts)


def enable_p2r(monkeypatch, prompt: str) -> None:
    enable_hypothesis(monkeypatch)
    bind_phase_prompt(monkeypatch, COHERENCE_PHASE, prompt)


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


def p2r_headers(phase: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {P2R_TOKEN}",
        "X-ModelMirror-P2R-Phase": phase,
    }


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def artifact_messages(
    *,
    phase: str,
    artifacts: list[tuple[str, str]],
    qualification_run_id: str = QUALIFICATION_RUN_ID,
    previous_receipt_sha256: str = PREVIOUS_RECEIPT_SHA256,
    chunk_chars: int = bridge.P2R_MAX_ARTIFACT_CHUNK_CHARS,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for path, content in artifacts:
        raw = content.encode("utf-8")
        chunks = [
            content[offset : offset + chunk_chars]
            for offset in range(0, len(content), chunk_chars)
        ] or [""]
        for index, chunk in enumerate(chunks):
            envelope = {
                "protocol": bridge.P2R_PHASE_REQUEST_PROTOCOL,
                "qualificationRunId": qualification_run_id,
                "phase": phase,
                "previousReceiptSha256": previous_receipt_sha256,
                "artifact": {
                    "path": path,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "sizeBytes": len(raw),
                    "chunkIndex": index,
                    "chunkCount": len(chunks),
                    "chunkSha256": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                    "content": chunk,
                },
            }
            messages.append({"role": "user", "content": canonical_json(envelope)})
    return messages


def valid_payload(*, stream: bool = False) -> dict[str, Any]:
    return {
        "model": MODEL_ID,
        "messages": [
            {"role": "system", "content": "Write a literature synthesis."},
            {"role": "user", "content": "What makes agent evaluation reproducible?"},
        ],
        "temperature": 0.2,
        "max_tokens": 512,
        "stream": stream,
        **({"stream_options": {"include_usage": True}} if stream else {}),
    }


def test_main_app_registers_restricted_ai_research_routes(monkeypatch) -> None:
    from server.main import app as main_app

    registered = [
        (route.path, tuple(sorted(route.methods or set())), route.endpoint)
        for route in main_app.routes
        if route.path.startswith("/api/ai-research/v1")
    ]
    assert len(registered) == 2
    assert registered == [
        ("/api/ai-research/v1/models", ("GET",), bridge.models),
        (
            "/api/ai-research/v1/chat/completions",
            ("POST",),
            bridge.chat_completions,
        ),
    ]

    client = TestClient(main_app)
    monkeypatch.delenv("AI_RESEARCH_S2S_ENABLED", raising=False)
    monkeypatch.delenv("AI_RESEARCH_S2S_TOKEN", raising=False)
    monkeypatch.delenv("AI_RESEARCH_LITERATURE_MODEL_ID", raising=False)
    assert client.get("/api/ai-research/v1/models").status_code == 503
    assert client.post(
        "/api/ai-research/v1/chat/completions", json=valid_payload()
    ).status_code == 503

    enable(monkeypatch)
    models_response = client.get(
        "/api/ai-research/v1/models",
        headers={"Authorization": "Bearer wrong-service-token"},
    )
    assert models_response.status_code == 401
    chat_response = client.post(
        "/api/ai-research/v1/chat/completions",
        json=valid_payload(),
        headers={"Authorization": "Bearer wrong-service-token"},
    )
    assert chat_response.status_code == 401


def test_p2r_phase_registry_is_closed() -> None:
    assert set(bridge.P2R_PHASE_CONTRACTS) == {
        "researchstudio.phase0.intent",
        "researchstudio.phase0.partition",
        "researchstudio.phase0.pattern_summary",
        "researchstudio.phase0.coverage",
        "researchstudio.phase1.bottleneck",
        "researchstudio.phase2.select",
        "researchstudio.phase2.generate",
        "researchstudio.phase2.coherence",
    }
    assert len(bridge.P2R_LOCKED_STATIC_ARTIFACT_SHA256) == 34
    assert bridge.P2R_LOCKED_STATIC_ARTIFACT_SHA256[
        "references/ideation-sub-patterns/C00.md"
    ] == "0bc790076bc61bc5a902094b3b1326375b20a2210540b9665c97728f8a869d56"
    assert bridge.P2R_LOCKED_STATIC_ARTIFACT_SHA256[
        "references/ideation-sub-patterns/C30.md"
    ] == "7e91d3605898cddc818268020289617ca76496905342b58adc4358eedc146497"
    assert "references/ideation-sub-patterns/C31.md" not in (
        bridge.P2R_LOCKED_STATIC_ARTIFACT_SHA256
    )


def tool_payload(*, stream: bool = False) -> dict[str, Any]:
    payload = valid_payload(stream=stream)
    payload["tools"] = [
        {
            "type": "function",
            "function": {
                "name": "search_openalex",
                "description": "Search public academic metadata.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
    ]
    payload["tool_choice"] = "auto"
    payload["parallel_tool_calls"] = False
    return payload


def p2r_text_payload(
    prompt: str = LOCKED_TEXT_PHASE_PROMPT,
    *,
    phase: str = TEXT_PHASE,
    artifacts: list[tuple[str, str]] | None = None,
    response_shape: str = "object",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": HYPOTHESIS_MODEL_ID,
        "messages": [
            {"role": "system", "content": prompt},
            *artifact_messages(
                phase=phase,
                artifacts=artifacts or [("phase0/user_query.txt", "Agent evaluation")],
            ),
        ],
        "temperature": bridge.P2R_FIXED_TEMPERATURE,
        "max_tokens": bridge.P2R_FIXED_MAX_TOKENS,
        "stream": False,
    }
    if response_shape == "object":
        payload["response_format"] = {"type": "json_object"}
    return payload


def ordinary_hypothesis_payload() -> dict[str, Any]:
    return {
        "model": HYPOTHESIS_MODEL_ID,
        "messages": [
            {
                "role": "system",
                "content": "Return a JSON object containing one bounded hypothesis.",
            },
            {
                "role": "user",
                "content": "Propose one testable agent-evaluation hypothesis.",
            },
        ],
        "temperature": 0.2,
        "max_tokens": 512,
        "stream": False,
        "response_format": {"type": "json_object"},
    }


def p2r_tool_payload(prompt: str) -> dict[str, Any]:
    payload = p2r_text_payload(
        prompt,
        phase=COHERENCE_PHASE,
        artifacts=[
            ("phase2_select/phase2_select_output.json", '{"selected":"C01"}'),
            ("phase2_generate/phase2_generate_output.json", '{"candidate":{}}'),
        ],
    )
    payload["tools"] = [
        {
            "type": "function",
            "function": {
                "name": "python",
                "description": bridge.P2R_PYTHON_TOOL_DESCRIPTION,
                "parameters": copy.deepcopy(bridge.P2R_PYTHON_PARAMETERS),
                "strict": True,
            },
        }
    ]
    payload["tool_choice"] = "auto"
    payload["parallel_tool_calls"] = False
    return payload


def p2r_python_receipt(code: str) -> dict[str, Any]:
    stdout = "dry-run ok\n"
    stderr = ""
    return {
        "protocol": bridge.P2R_PYTHON_RECEIPT_PROTOCOL,
        "sandboxImage": bridge.P2R_PYTHON_SANDBOX_IMAGE,
        "command": ["python3", "-"],
        "scriptSha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        "scriptSizeBytes": len(code.encode("utf-8")),
        "exitCode": 0,
        "stdout": stdout,
        "stdoutSha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stdoutSizeBytes": len(stdout.encode("utf-8")),
        "stderr": stderr,
        "stderrSha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
        "stderrSizeBytes": len(stderr.encode("utf-8")),
        "limits": copy.deepcopy(bridge.P2R_PYTHON_LIMITS),
        "truncation": {"captureExceeded": False, "stderr": False, "stdout": False},
    }


def p2r_finalize_payload(prompt: str, *, code: str = "print('dry-run')") -> dict[str, Any]:
    payload = p2r_tool_payload(prompt)
    payload["messages"].extend(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_python",
                        "type": "function",
                        "function": {
                            "name": "python",
                            "arguments": canonical_json({"code": code}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_python",
                "content": canonical_json(p2r_python_receipt(code)),
            },
        ]
    )
    return payload


def test_disabled_wrong_token_and_models_fail_closed(monkeypatch) -> None:
    stable = FakeStable(lambda request: httpx.Response(500))
    api = TestClient(app_for(stable))
    assert api.get("/api/ai-research/v1/models").status_code == 503

    enable(monkeypatch)
    assert api.get(
        "/api/ai-research/v1/models", headers={"Authorization": "Bearer wrong"}
    ).status_code == 401
    models = api.get("/api/ai-research/v1/models", headers=headers())
    assert models.status_code == 200
    assert [item["id"] for item in models.json()["data"]] == [MODEL_ID]
    assert stable.readiness_calls == [(MODEL_ID, "chat_tools")]

    stable.ready = False
    stable.readiness_reason = "provider_chat_hard_failure_recertification_required"
    not_ready = api.get("/api/ai-research/v1/models", headers=headers())
    assert not_ready.status_code == 503
    assert not_ready.json()["detail"] == (
        "provider_chat_hard_failure_recertification_required"
    )


def test_models_remains_generic_credential_surface_with_phase_header(
    monkeypatch,
) -> None:
    enable_hypothesis(monkeypatch)
    stable = FakeStable(lambda request: httpx.Response(500))
    with TestClient(app_for(stable)) as api:
        p2r_credential = api.get(
            "/api/ai-research/v1/models",
            headers=p2r_headers(TEXT_PHASE),
        )
        assert p2r_credential.status_code == 401
        assert stable.service_resolutions == 0
        assert stable.readiness_calls == []

        generic_credential = api.get(
            "/api/ai-research/v1/models",
            headers={
                **headers(),
                "X-ModelMirror-P2R-Phase": TEXT_PHASE,
            },
        )

    assert generic_credential.status_code == 401
    assert stable.service_resolutions == 0
    assert stable.readiness_calls == []
    assert stable.capabilities == []
    assert stable.scoped_capabilities == []
    assert stable.dispatched == 0


def test_chat_credentials_cannot_cross_generic_and_p2r_surfaces(monkeypatch) -> None:
    enable_hypothesis(monkeypatch)
    bind_phase_prompt(monkeypatch, TEXT_PHASE, LOCKED_TEXT_PHASE_PROMPT)
    stable = FakeStable(lambda request: httpx.Response(500))
    with TestClient(app_for(stable)) as api:
        generic_on_phase = api.post(
            "/api/ai-research/v1/chat/completions",
            json=p2r_text_payload(),
            headers={
                **headers(),
                "X-ModelMirror-P2R-Phase": TEXT_PHASE,
            },
        )
        wrong_on_phase = api.post(
            "/api/ai-research/v1/chat/completions",
            json=p2r_text_payload(),
            headers={
                "Authorization": "Bearer wrong",
                "X-ModelMirror-P2R-Phase": TEXT_PHASE,
            },
        )
        p2r_without_phase = api.post(
            "/api/ai-research/v1/chat/completions",
            json=p2r_text_payload(),
            headers={"Authorization": f"Bearer {P2R_TOKEN}"},
        )

    assert [
        generic_on_phase.status_code,
        wrong_on_phase.status_code,
        p2r_without_phase.status_code,
    ] == [401, 401, 401]
    assert stable.service_resolutions == 0
    assert stable.readiness_calls == []
    assert stable.capabilities == []
    assert stable.scoped_capabilities == []
    assert stable.dispatched == 0


@pytest.mark.parametrize("p2r_token", [None, TOKEN])
def test_phase_requests_require_distinct_configured_p2r_credential(
    monkeypatch,
    p2r_token: str | None,
) -> None:
    enable_hypothesis(monkeypatch)
    bind_phase_prompt(monkeypatch, TEXT_PHASE, LOCKED_TEXT_PHASE_PROMPT)
    if p2r_token is None:
        monkeypatch.delenv("AI_RESEARCH_P2R_S2S_TOKEN", raising=False)
    else:
        monkeypatch.setenv("AI_RESEARCH_P2R_S2S_TOKEN", p2r_token)
    monkeypatch.setenv("AI_RESEARCH_P2R_ENABLED", "false")
    monkeypatch.setenv("AI_RESEARCH_P2R_TOOLS_ENABLED", "false")

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": HYPOTHESIS_MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"hypothesis":"bounded"}',
                        },
                    }
                ],
            },
        )

    stable = FakeStable(upstream)
    stable.readiness_overrides[(HYPOTHESIS_MODEL_ID, "chat_tools")] = (
        False,
        "provider_chat_no_qualified_tools_route",
    )
    with TestClient(app_for(stable)) as api:
        generic_models = api.get(
            "/api/ai-research/v1/models",
            headers=headers(),
        )
        assert generic_models.status_code == 200
        assert stable.service_resolutions == 1
        stable.readiness_calls.clear()

        no_phase = api.post(
            "/api/ai-research/v1/chat/completions",
            json=ordinary_hypothesis_payload(),
            headers=headers(),
        )
        phase_request = api.post(
            "/api/ai-research/v1/chat/completions",
            json=p2r_text_payload(),
            headers={
                **headers(),
                "X-ModelMirror-P2R-Phase": TEXT_PHASE,
            },
        )

    assert no_phase.status_code == 200
    assert phase_request.status_code == 503
    assert phase_request.json()["detail"] == (
        "AI Research P2R service credential is not configured"
    )
    assert stable.readiness_calls == [(HYPOTHESIS_MODEL_ID, "chat_text")]
    assert stable.service_resolutions == 2
    assert stable.capabilities == []
    assert stable.scoped_capabilities == ["chat_text"]
    assert stable.scoped_requirements == [("chat_text",)]
    assert stable.dispatched == 1


def test_ordinary_hypothesis_requires_only_current_text_certification(
    monkeypatch,
) -> None:
    enable_hypothesis(monkeypatch)
    stable = FakeStable(lambda request: httpx.Response(500))
    stable.readiness_overrides[(HYPOTHESIS_MODEL_ID, "chat_text")] = (
        False,
        "provider_chat_no_qualified_text_route",
    )

    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=ordinary_hypothesis_payload(),
            headers=headers(),
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "provider_chat_no_qualified_text_route"
    assert stable.readiness_calls == [(HYPOTHESIS_MODEL_ID, "chat_text")]
    assert stable.scoped_capabilities == []
    assert stable.dispatched == 0


def test_ordinary_hypothesis_rejects_tools_before_readiness(monkeypatch) -> None:
    enable_hypothesis(monkeypatch)
    stable = FakeStable(lambda request: httpx.Response(500))
    stable.ready = False
    stable.readiness_reason = "must not be observed"
    payload = tool_payload()
    payload["model"] = HYPOTHESIS_MODEL_ID

    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=payload,
            headers=headers(),
        )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "tools are not enabled for ordinary hypothesis requests"
    )
    assert stable.readiness_calls == []
    assert stable.capabilities == []
    assert stable.scoped_capabilities == []
    assert stable.dispatched == 0


def test_hypothesis_model_requires_text_and_tool_certification(
    monkeypatch,
) -> None:
    enable_hypothesis(monkeypatch)
    bind_phase_prompt(monkeypatch, TEXT_PHASE, LOCKED_TEXT_PHASE_PROMPT)

    def upstream(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == HYPOTHESIS_MODEL_ID
        assert "tools" not in body
        return httpx.Response(
            200,
            json={
                "model": HYPOTHESIS_MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"state":"proceed"}'},
                    }
                ],
            },
        )

    stable = FakeStable(upstream)
    payload = p2r_text_payload()
    tool_request = tool_payload()
    tool_request["model"] = HYPOTHESIS_MODEL_ID

    with TestClient(app_for(stable)) as api:
        models_response = api.get(
            "/api/ai-research/v1/models", headers=headers()
        )
        completion = api.post(
            "/api/ai-research/v1/chat/completions",
            json=payload,
            headers=p2r_headers(TEXT_PHASE),
        )
        rejected_tool = api.post(
            "/api/ai-research/v1/chat/completions",
            json=tool_request,
            headers=headers(),
        )

    assert models_response.status_code == 200
    assert [item["id"] for item in models_response.json()["data"]] == [
        MODEL_ID,
        HYPOTHESIS_MODEL_ID,
    ]
    assert stable.readiness_calls == [
        (MODEL_ID, "chat_tools"),
        (HYPOTHESIS_MODEL_ID, "chat_text"),
        (HYPOTHESIS_MODEL_ID, "chat_tools"),
        (HYPOTHESIS_MODEL_ID, "chat_text"),
        (HYPOTHESIS_MODEL_ID, "chat_tools"),
    ]
    assert completion.status_code == 200
    assert rejected_tool.status_code == 422
    assert stable.capabilities == []
    assert stable.scoped_capabilities == ["chat_text"]
    assert stable.scoped_requirements == [("chat_text", "chat_tools")]
    assert stable.dispatched == 1


def test_hypothesis_discovery_requires_both_flags_and_capabilities(
    monkeypatch,
) -> None:
    enable_hypothesis(monkeypatch)
    stable = FakeStable(lambda request: httpx.Response(500))
    monkeypatch.setenv("AI_RESEARCH_P2R_TOOLS_ENABLED", "false")
    with TestClient(app_for(stable)) as api:
        flags_off = api.get("/api/ai-research/v1/models", headers=headers())
    assert [item["id"] for item in flags_off.json()["data"]] == [MODEL_ID]
    assert stable.readiness_calls == [(MODEL_ID, "chat_tools")]

    monkeypatch.setenv("AI_RESEARCH_P2R_TOOLS_ENABLED", "true")
    stable.readiness_calls.clear()
    stable.readiness_overrides[(HYPOTHESIS_MODEL_ID, "chat_tools")] = (
        False,
        "provider_chat_no_qualified_tools_route",
    )
    with TestClient(app_for(stable)) as api:
        tools_unready = api.get("/api/ai-research/v1/models", headers=headers())
    assert [item["id"] for item in tools_unready.json()["data"]] == [MODEL_ID]
    assert stable.readiness_calls == [
        (MODEL_ID, "chat_tools"),
        (HYPOTHESIS_MODEL_ID, "chat_text"),
        (HYPOTHESIS_MODEL_ID, "chat_tools"),
    ]


def test_direct_p2r_calls_require_both_scoped_certificates(monkeypatch) -> None:
    enable_hypothesis(monkeypatch)
    bind_phase_prompt(monkeypatch, TEXT_PHASE, LOCKED_TEXT_PHASE_PROMPT)
    text_stable = FakeStable(lambda request: httpx.Response(500))
    text_stable.readiness_overrides[(HYPOTHESIS_MODEL_ID, "chat_tools")] = (
        False,
        "provider_chat_no_qualified_tools_route",
    )
    with TestClient(app_for(text_stable)) as api:
        text_response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=p2r_text_payload(),
            headers=p2r_headers(TEXT_PHASE),
        )
    assert text_response.status_code == 503
    assert text_response.json()["detail"] == "provider_chat_no_qualified_tools_route"
    assert text_stable.readiness_calls == [
        (HYPOTHESIS_MODEL_ID, "chat_text"),
        (HYPOTHESIS_MODEL_ID, "chat_tools"),
    ]
    assert text_stable.scoped_capabilities == []
    assert text_stable.dispatched == 0

    coherence_prompt = "Locked ResearchStudio coherence JSON prompt."
    enable_p2r(monkeypatch, coherence_prompt)
    tools_stable = FakeStable(lambda request: httpx.Response(500))
    tools_stable.readiness_overrides[(HYPOTHESIS_MODEL_ID, "chat_text")] = (
        False,
        "provider_chat_no_qualified_text_route",
    )
    with TestClient(app_for(tools_stable)) as api:
        tools_response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=p2r_tool_payload(coherence_prompt),
            headers=p2r_headers(COHERENCE_PHASE),
        )
    assert tools_response.status_code == 503
    assert tools_response.json()["detail"] == "provider_chat_no_qualified_text_route"
    assert tools_stable.readiness_calls == [
        (HYPOTHESIS_MODEL_ID, "chat_text"),
    ]
    assert tools_stable.scoped_capabilities == []
    assert tools_stable.dispatched == 0


def test_p2r_coherence_tools_require_exact_prompt_schema_and_scoped_qualification(
    monkeypatch,
) -> None:
    prompt = "Locked ResearchStudio coherence JSON prompt."
    enable_p2r(monkeypatch, prompt)

    def upstream(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == HYPOTHESIS_MODEL_ID
        assert body["tools"] == p2r_tool_payload(prompt)["tools"]
        return httpx.Response(
            200,
            json={
                "model": HYPOTHESIS_MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_python",
                                    "type": "function",
                                    "function": {
                                        "name": "python",
                                        "arguments": '{"code":"print(1)"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )

    stable = FakeStable(upstream)
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=p2r_tool_payload(prompt),
            headers=p2r_headers(COHERENCE_PHASE),
        )

    assert response.status_code == 200
    assert stable.capabilities == []
    assert stable.scoped_capabilities == ["chat_tools"]
    assert stable.dispatched == 1


def test_p2r_tool_contract_mutations_fail_before_provider_dispatch(monkeypatch) -> None:
    prompt = "Locked ResearchStudio coherence JSON prompt."
    enable_p2r(monkeypatch, prompt)
    stable = FakeStable(lambda request: httpx.Response(500))
    mutations: list[dict[str, Any]] = []

    wrong_prompt = p2r_tool_payload(prompt)
    wrong_prompt["messages"][0]["content"] += " changed"
    mutations.append(wrong_prompt)

    wrong_name = p2r_tool_payload(prompt)
    wrong_name["tools"][0]["function"]["name"] = "bash"
    mutations.append(wrong_name)

    wrong_schema = p2r_tool_payload(prompt)
    wrong_schema["tools"][0]["function"]["parameters"]["properties"]["path"] = {
        "type": "string"
    }
    mutations.append(wrong_schema)

    not_strict = p2r_tool_payload(prompt)
    not_strict["tools"][0]["function"]["strict"] = False
    mutations.append(not_strict)

    parallel = p2r_tool_payload(prompt)
    parallel["parallel_tool_calls"] = True
    mutations.append(parallel)

    required = p2r_tool_payload(prompt)
    required["tool_choice"] = "required"
    mutations.append(required)

    streamed = p2r_tool_payload(prompt)
    streamed["stream"] = True
    mutations.append(streamed)

    with TestClient(app_for(stable)) as api:
        for payload in mutations:
            response = api.post(
                "/api/ai-research/v1/chat/completions",
                json=payload,
                headers=p2r_headers(COHERENCE_PHASE),
            )
            assert response.status_code == 422

    assert stable.dispatched == 0
    assert stable.scoped_capabilities == []


def test_p2r_coherence_content_only_response_is_a_hard_failure(monkeypatch) -> None:
    prompt = "Locked ResearchStudio coherence JSON prompt."
    enable_p2r(monkeypatch, prompt)
    stable = FakeStable(
        lambda request: httpx.Response(
            200,
            json={
                "model": HYPOTHESIS_MODEL_ID,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"execution":{"mode":"executed"}}',
                        }
                    }
                ],
            },
        )
    )
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=p2r_tool_payload(prompt),
            headers=p2r_headers(COHERENCE_PHASE),
        )
    assert response.status_code == 502
    assert stable.completed[0]["error_code"] == (
        "ai_research_bridge_missing_p2r_tool_call"
    )


def test_p2r_completion_requires_explicit_fixed_model_identity(monkeypatch) -> None:
    enable_hypothesis(monkeypatch)
    bind_phase_prompt(monkeypatch, TEXT_PHASE, LOCKED_TEXT_PHASE_PROMPT)
    stable = FakeStable(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"state":"proceed"}',
                        }
                    }
                ]
            },
        )
    )

    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=p2r_text_payload(),
            headers=p2r_headers(TEXT_PHASE),
        )

    assert response.status_code == 502
    assert stable.dispatched == 1
    assert len(stable.completed) == 1
    assert stable.completed[0]["status"] == "failed"
    assert stable.completed[0]["result_class"] == "hard_failure"
    assert stable.completed[0]["error_code"] == (
        "ai_research_bridge_model_identity_required"
    )
    assert stable.completed[0]["hard_failure"] is True
    assert "actual_model" not in stable.completed[0]


def test_p2r_coherence_finalize_binds_receipt_and_forbids_tampering(
    monkeypatch,
) -> None:
    prompt = "Locked ResearchStudio coherence JSON prompt."
    enable_p2r(monkeypatch, prompt)

    def upstream(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["messages"][-1]["role"] == "tool"
        return httpx.Response(
            200,
            json={
                "model": HYPOTHESIS_MODEL_ID,
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": '{"state":"proceed"}',
                        }
                    }
                ],
            },
        )

    stable = FakeStable(upstream)
    valid = p2r_finalize_payload(prompt)
    tampered = p2r_finalize_payload(prompt)
    receipt = json.loads(tampered["messages"][-1]["content"])
    receipt["stdout"] += "tampered"
    tampered["messages"][-1]["content"] = canonical_json(receipt)
    with TestClient(app_for(stable)) as api:
        accepted = api.post(
            "/api/ai-research/v1/chat/completions",
            json=valid,
            headers=p2r_headers(COHERENCE_PHASE),
        )
        rejected = api.post(
            "/api/ai-research/v1/chat/completions",
            json=tampered,
            headers=p2r_headers(COHERENCE_PHASE),
        )
    assert accepted.status_code == 200
    assert rejected.status_code == 422
    assert stable.dispatched == 1


def test_p2r_artifact_binding_mutations_fail_before_dispatch(monkeypatch) -> None:
    enable_hypothesis(monkeypatch)
    bind_phase_prompt(monkeypatch, TEXT_PHASE, LOCKED_TEXT_PHASE_PROMPT)
    stable = FakeStable(lambda request: httpx.Response(500))

    tampered_hash = p2r_text_payload()
    envelope = json.loads(tampered_hash["messages"][1]["content"])
    envelope["artifact"]["sha256"] = "0" * 64
    tampered_hash["messages"][1]["content"] = canonical_json(envelope)

    wrong_phase = p2r_text_payload()
    envelope = json.loads(wrong_phase["messages"][1]["content"])
    envelope["phase"] = "researchstudio.phase0.partition"
    wrong_phase["messages"][1]["content"] = canonical_json(envelope)

    noncanonical = p2r_text_payload()
    envelope = json.loads(noncanonical["messages"][1]["content"])
    noncanonical["messages"][1]["content"] = json.dumps(envelope, ensure_ascii=False)

    with TestClient(app_for(stable)) as api:
        for payload in (tampered_hash, wrong_phase, noncanonical):
            response = api.post(
                "/api/ai-research/v1/chat/completions",
                json=payload,
                headers=p2r_headers(TEXT_PHASE),
            )
            assert response.status_code == 422
    assert stable.dispatched == 0


def test_p2r_locked_reference_tamper_and_unknown_card_fail_before_dispatch(
    monkeypatch,
) -> None:
    select_phase = "researchstudio.phase2.select"
    generate_phase = "researchstudio.phase2.generate"
    select_prompt = "Locked select prompt."
    generate_prompt = "Locked generate prompt."
    enable_hypothesis(monkeypatch)
    bind_phase_prompt(monkeypatch, select_phase, select_prompt)
    bind_phase_prompt(monkeypatch, generate_phase, generate_prompt)
    stable = FakeStable(lambda request: httpx.Response(500))

    tampered_static = p2r_text_payload(
        select_prompt,
        phase=select_phase,
        artifacts=[
            ("phase0/user_query.txt", "Agent evaluation"),
            ("phase1/phase1_output.json", "{}"),
            ("references/ideation-patterns/overview.md", "tampered"),
            ("references/ideation-patterns/companion-combos.md", "tampered"),
            ("phase0/lit_table.md", "table"),
            ("phase2_generate/closest_abstracts.json", "[]"),
            ("references/ideation-sub-patterns/overview.md", "tampered"),
        ],
    )
    unknown_card = p2r_text_payload(
        generate_prompt,
        phase=generate_phase,
        artifacts=[
            ("phase2_select/phase2_select_output.json", "{}"),
            ("phase1/phase1_output.json", "{}"),
            ("phase2_generate/closest_abstracts.json", "[]"),
            ("phase0/fulltext_cache.json", "{}"),
            ("references/ideation-sub-patterns/overview.md", "tampered"),
            ("references/ideation-sub-patterns/C31.md", "invented"),
        ],
    )

    with TestClient(app_for(stable)) as api:
        for phase, payload in (
            (select_phase, tampered_static),
            (generate_phase, unknown_card),
        ):
            response = api.post(
                "/api/ai-research/v1/chat/completions",
                json=payload,
                headers=p2r_headers(phase),
            )
            assert response.status_code == 422
    assert stable.readiness_calls == []
    assert stable.scoped_capabilities == []
    assert stable.dispatched == 0


def test_p2r_array_phase_uses_declared_top_level_shape(monkeypatch) -> None:
    phase = "researchstudio.phase0.partition"
    prompt = "Partition the papers and return a JSON array."
    enable_hypothesis(monkeypatch)
    bind_phase_prompt(monkeypatch, phase, prompt)
    payload = p2r_text_payload(
        prompt,
        phase=phase,
        response_shape="array",
        artifacts=[
            ("phase0/user_query.txt", "Agent evaluation"),
            ("phase0/lit_results.json", "[]"),
        ],
    )
    assert "response_format" not in payload

    accepted_stable = FakeStable(
        lambda request: httpx.Response(
            200,
            json={
                "model": HYPOTHESIS_MODEL_ID,
                "choices": [{"message": {"role": "assistant", "content": "[]"}}],
            },
        )
    )
    with TestClient(app_for(accepted_stable)) as api:
        accepted = api.post(
            "/api/ai-research/v1/chat/completions",
            json=payload,
            headers=p2r_headers(phase),
        )
    assert accepted.status_code == 200

    wrong_shape_stable = FakeStable(
        lambda request: httpx.Response(
            200,
            json={
                "model": HYPOTHESIS_MODEL_ID,
                "choices": [
                    {"message": {"role": "assistant", "content": "{}"}}
                ],
            },
        )
    )
    with TestClient(app_for(wrong_shape_stable)) as api:
        rejected = api.post(
            "/api/ai-research/v1/chat/completions",
            json=payload,
            headers=p2r_headers(phase),
        )
    assert rejected.status_code == 502
    assert wrong_shape_stable.completed[0]["error_code"] == (
        "ai_research_bridge_invalid_p2r_shape"
    )


def test_p2r_text_phase_requires_exact_locked_prompt_before_dispatch(monkeypatch) -> None:
    enable_hypothesis(monkeypatch)
    bind_phase_prompt(monkeypatch, TEXT_PHASE, LOCKED_TEXT_PHASE_PROMPT)
    stable = FakeStable(lambda request: httpx.Response(500))

    mutations: list[dict[str, Any]] = []
    wrong_prompt = p2r_text_payload()
    wrong_prompt["messages"][0]["content"] = LOCKED_TEXT_PHASE_PROMPT + " changed"
    mutations.append(wrong_prompt)

    extra_system = p2r_text_payload()
    extra_system["messages"].insert(
        1, {"role": "system", "content": LOCKED_TEXT_PHASE_PROMPT}
    )
    mutations.append(extra_system)

    no_structured_output = p2r_text_payload()
    no_structured_output.pop("response_format")
    mutations.append(no_structured_output)

    streamed = p2r_text_payload()
    streamed["stream"] = True
    mutations.append(streamed)

    caller_sampling = p2r_text_payload()
    caller_sampling["temperature"] = 0.7
    mutations.append(caller_sampling)

    arbitrary_text = p2r_text_payload()
    arbitrary_text["messages"][1]["content"] = "Return an unrelated workload"
    mutations.append(arbitrary_text)

    with TestClient(app_for(stable)) as api:
        for payload in mutations:
            response = api.post(
                "/api/ai-research/v1/chat/completions",
                json=payload,
                headers=p2r_headers(TEXT_PHASE),
            )
            assert response.status_code == 422

        missing_header = api.post(
            "/api/ai-research/v1/chat/completions",
            json=p2r_text_payload(),
            headers={"Authorization": f"Bearer {P2R_TOKEN}"},
        )
        assert missing_header.status_code == 401

    assert stable.dispatched == 0
    assert stable.scoped_capabilities == []


def test_duplicate_hypothesis_model_configuration_fails_closed(monkeypatch) -> None:
    enable(monkeypatch)
    monkeypatch.setenv("AI_RESEARCH_HYPOTHESIS_MODEL_ID", MODEL_ID)
    stable = FakeStable(lambda request: httpx.Response(500))
    with TestClient(app_for(stable)) as api:
        assert api.get("/api/ai-research/v1/models", headers=headers()).status_code == 503
        assert api.post(
            "/api/ai-research/v1/chat/completions",
            json=valid_payload(),
            headers=headers(),
        ).status_code == 503
    assert stable.readiness_calls == []
    assert stable.dispatched == 0


def test_unqualified_hypothesis_model_does_not_break_literature_discovery(
    monkeypatch,
) -> None:
    enable_hypothesis(monkeypatch)
    stable = FakeStable(lambda request: httpx.Response(500))
    stable.readiness_overrides[(HYPOTHESIS_MODEL_ID, "chat_text")] = (
        False,
        "provider_chat_no_qualified_route",
    )
    with TestClient(app_for(stable)) as api:
        response = api.get("/api/ai-research/v1/models", headers=headers())
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == [MODEL_ID]
    assert stable.readiness_calls == [
        (MODEL_ID, "chat_tools"),
        (HYPOTHESIS_MODEL_ID, "chat_text"),
        (HYPOTHESIS_MODEL_ID, "chat_tools"),
    ]


def test_unqualified_literature_model_does_not_break_hypothesis_discovery(
    monkeypatch,
) -> None:
    enable_hypothesis(monkeypatch)
    stable = FakeStable(lambda request: httpx.Response(500))
    stable.readiness_overrides[(MODEL_ID, "chat_tools")] = (
        False,
        "provider_chat_no_qualified_route",
    )
    with TestClient(app_for(stable)) as api:
        response = api.get("/api/ai-research/v1/models", headers=headers())
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == [
        HYPOTHESIS_MODEL_ID
    ]
    assert stable.readiness_calls == [
        (MODEL_ID, "chat_tools"),
        (HYPOTHESIS_MODEL_ID, "chat_text"),
        (HYPOTHESIS_MODEL_ID, "chat_tools"),
    ]


def test_rejects_wrong_model_multimodal_and_unknown_fields(monkeypatch) -> None:
    enable(monkeypatch)
    stable = FakeStable(lambda request: httpx.Response(500))
    with TestClient(app_for(stable)) as api:
        wrong = valid_payload()
        wrong["model"] = "provider/other"
        assert api.post(
            "/api/ai-research/v1/chat/completions", json=wrong, headers=headers()
        ).status_code == 422
        for field, value in (("user", "caller-selected-user"),):
            invalid = valid_payload()
            invalid[field] = value
            assert api.post(
                "/api/ai-research/v1/chat/completions",
                json=invalid,
                headers=headers(),
            ).status_code == 422
        multimodal = valid_payload()
        multimodal["messages"][1]["content"] = [
            {"type": "image_url", "image_url": {"url": "https://example.test/x"}}
        ]
        assert api.post(
            "/api/ai-research/v1/chat/completions",
            json=multimodal,
            headers=headers(),
        ).status_code == 422
    assert stable.dispatched == 0


def test_json_object_response_format_is_bounded_and_preserved(monkeypatch) -> None:
    enable_hypothesis(monkeypatch)

    def upstream(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == HYPOTHESIS_MODEL_ID
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "model": HYPOTHESIS_MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"ok":true}'},
                    }
                ],
            },
        )

    stable = FakeStable(upstream)
    accepted = ordinary_hypothesis_payload()
    missing_instruction = ordinary_hypothesis_payload()
    missing_instruction["messages"][0]["content"] = "No structured output instruction."
    with TestClient(app_for(stable)) as api:
        assert api.post(
            "/api/ai-research/v1/chat/completions",
            json=accepted,
            headers=headers(),
        ).status_code == 200
        for invalid_format in (
            {"type": "json_schema"},
            {"type": "json_object", "schema": {}},
            {"type": "text"},
        ):
            invalid = ordinary_hypothesis_payload()
            invalid["response_format"] = invalid_format
            assert api.post(
                "/api/ai-research/v1/chat/completions",
                json=invalid,
                headers=headers(),
            ).status_code == 422
        assert api.post(
            "/api/ai-research/v1/chat/completions",
            json=missing_instruction,
            headers=headers(),
        ).status_code == 422
    assert stable.dispatched == 1
    assert stable.capabilities == []
    assert stable.scoped_capabilities == ["chat_text"]
    assert stable.scoped_requirements == [("chat_text",)]


def test_literature_model_rejects_response_format_before_dispatch(monkeypatch) -> None:
    enable_hypothesis(monkeypatch)
    stable = FakeStable(lambda request: httpx.Response(500))
    payload = valid_payload()
    payload["messages"][0]["content"] += " Return a JSON object."
    payload["response_format"] = {"type": "json_object"}

    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=payload,
            headers=headers(),
        )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "response_format is only enabled for the hypothesis model"
    )
    assert stable.dispatched == 0
    assert stable.capabilities == []
    assert stable.scoped_capabilities == []


def test_literature_model_rejects_p2r_phase_header_before_dispatch(monkeypatch) -> None:
    enable(monkeypatch)
    monkeypatch.setenv("AI_RESEARCH_P2R_S2S_TOKEN", P2R_TOKEN)
    stable = FakeStable(lambda request: httpx.Response(500))
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=valid_payload(),
            headers=p2r_headers(TEXT_PHASE),
        )
    assert response.status_code == 422
    assert stable.dispatched == 0


def test_tool_request_uses_qualified_tools_route_and_preserves_contract(monkeypatch) -> None:
    enable(monkeypatch)

    def upstream(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["tools"][0]["function"]["name"] == "search_openalex"
        assert body["tool_choice"] == "auto"
        assert body["parallel_tool_calls"] is False
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_openalex",
                                    "type": "function",
                                    "function": {
                                        "name": "search_openalex",
                                        "arguments": '{"query":"AgentBench"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
        )

    stable = FakeStable(upstream)
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=tool_payload(),
            headers=headers(),
        )
    assert response.status_code == 200
    assert stable.capabilities == ["chat_tools"]
    assert stable.completed[0]["status"] == "succeeded"


def test_tool_followup_requires_declared_matched_calls(monkeypatch) -> None:
    enable(monkeypatch)
    stable = FakeStable(lambda request: httpx.Response(500))
    valid = tool_payload()
    valid["messages"].extend(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_openalex",
                        "type": "function",
                        "function": {
                            "name": "search_openalex",
                            "arguments": '{"query":"AgentBench"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_openalex",
                "content": "One public result",
            },
        ]
    )
    invalid_cases = []
    undeclared = tool_payload()
    undeclared["messages"].append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_bad",
                    "type": "function",
                    "function": {"name": "shell", "arguments": "{}"},
                }
            ],
        }
    )
    invalid_cases.append(undeclared)
    orphan = tool_payload()
    orphan["messages"].append(
        {"role": "tool", "tool_call_id": "call_missing", "content": "x"}
    )
    invalid_cases.append(orphan)
    bad_schema = tool_payload()
    bad_schema["tools"][0]["function"]["parameters"] = {"type": "string"}
    invalid_cases.append(bad_schema)

    def upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Synthesis"},
                    }
                ],
            },
        )

    stable.transport.handler = upstream
    with TestClient(app_for(stable)) as api:
        accepted = api.post(
            "/api/ai-research/v1/chat/completions", json=valid, headers=headers()
        )
        assert accepted.status_code == 200
        for invalid in invalid_cases:
            assert api.post(
                "/api/ai-research/v1/chat/completions",
                json=invalid,
                headers=headers(),
            ).status_code == 422
    assert stable.dispatched == 1


def test_streaming_tool_call_is_a_valid_terminal(monkeypatch) -> None:
    enable(monkeypatch)
    events = (
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{"tool_calls":[{{"index":0,"id":"call_openalex","type":"function","function":{{"name":"search_openalex","arguments":"{{\\"query\\":\\"AgentBench\\"}}"}}}}]}},"finish_reason":null}}]}}\n\n'
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{}},"finish_reason":"tool_calls"}}]}}\n\n'
        "data: [DONE]\n\n"
    )
    stable = FakeStable(
        lambda request: httpx.Response(
            200,
            content=events.encode("utf-8"),
            headers={"content-type": "text/event-stream"},
        )
    )
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=tool_payload(stream=True),
            headers=headers(),
        )
    assert response.status_code == 200
    assert response.text.endswith("data: [DONE]\n\n")
    assert stable.capabilities == ["chat_tools"]
    assert stable.completed[0]["status"] == "succeeded"


def test_completion_token_alias_is_bounded_normalized_and_exclusive(monkeypatch) -> None:
    enable(monkeypatch)

    def upstream(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["max_tokens"] == 30_000
        assert "max_completion_tokens" not in body
        return httpx.Response(
            200,
            json={
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Review"},
                    }
                ],
            },
        )

    stable = FakeStable(upstream)
    with TestClient(app_for(stable)) as api:
        alias_payload = valid_payload()
        alias_payload.pop("max_tokens")
        alias_payload["max_completion_tokens"] = 30_000
        assert api.post(
            "/api/ai-research/v1/chat/completions",
            json=alias_payload,
            headers=headers(),
        ).status_code == 200

        both_payload = valid_payload()
        both_payload["max_completion_tokens"] = 512
        assert api.post(
            "/api/ai-research/v1/chat/completions",
            json=both_payload,
            headers=headers(),
        ).status_code == 422

        oversized_payload = valid_payload()
        oversized_payload.pop("max_tokens")
        oversized_payload["max_completion_tokens"] = 32_769
        assert api.post(
            "/api/ai-research/v1/chat/completions",
            json=oversized_payload,
            headers=headers(),
        ).status_code == 422
    assert stable.dispatched == 1


def test_non_streaming_request_uses_fixed_control_and_records_usage(monkeypatch) -> None:
    enable(monkeypatch)

    def upstream(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == MODEL_ID
        assert set(body) <= {
            "model",
            "messages",
            "temperature",
            "max_tokens",
            "top_p",
            "stop",
            "stream",
            "stream_options",
            "response_format",
        }
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": MODEL_ID,
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "Review"}}
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 2,
                    "total_tokens": 13,
                },
            },
        )

    stable = FakeStable(upstream)
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=valid_payload(),
            headers=headers(),
        )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Review"
    assert response.headers["cache-control"] == "no-store"
    assert stable.dispatched == 1
    assert stable.completed[0]["status"] == "succeeded"
    assert stable.completed[0]["total_tokens"] == 13


def test_non_streaming_send_cancellation_closes_and_terminalizes() -> None:
    stable = FakeStable(lambda request: httpx.Response(500))

    async def cancel_send(
        client: httpx.AsyncClient, request: httpx.Request
    ) -> httpx.Response:
        stable.transport.last_client = client
        raise asyncio.CancelledError

    stable.transport.send_authorized_stream = cancel_send

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(call_direct(stable))

    assert stable.dispatched == 1
    assert stable.transport.last_client is not None
    assert stable.transport.last_client.is_closed
    assert stable.completed == [
        {
            "status": "cancelled",
            "result_class": "client_cancelled",
            "error_code": "provider_chat_client_cancelled",
            "client_cancelled": True,
            "e2e_ms": stable.completed[0]["e2e_ms"],
        }
    ]


def test_non_streaming_2xx_read_cancellation_closes_and_terminalizes() -> None:
    stream = CancelOnReadStream()
    stable = FakeStable(lambda request: httpx.Response(200, stream=stream))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(call_direct(stable))

    assert stable.dispatched == 1
    assert stream.close_calls >= 1
    assert stable.transport.last_client is not None
    assert stable.transport.last_client.is_closed
    assert len(stable.completed) == 1
    assert stable.completed[0]["status"] == "cancelled"
    assert stable.completed[0]["result_class"] == "client_cancelled"
    assert stable.completed[0]["error_code"] == "provider_chat_client_cancelled"
    assert stable.completed[0]["client_cancelled"] is True


def test_non_streaming_401_drain_cancellation_preserves_hard_failure() -> None:
    stream = CancelOnReadStream()
    stable = HardFailureStable(lambda request: httpx.Response(401, stream=stream))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(call_direct(stable))

    assert stable.dispatched == 1
    assert stream.close_calls >= 1
    assert stable.transport.last_client is not None
    assert stable.transport.last_client.is_closed
    assert len(stable.completed) == 1
    assert stable.completed[0]["status"] == "failed"
    assert stable.completed[0]["result_class"] == "hard_failure"
    assert stable.completed[0]["error_code"] == "provider_chat_http_401"
    assert stable.completed[0]["hard_failure"] is True
    assert "client_cancelled" not in stable.completed[0]


@pytest.mark.parametrize(
    "stream",
    [
        UnexpectedReadStream(),
        UnexpectedCloseStream(
            json.dumps(
                {
                    "model": MODEL_ID,
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Review",
                            }
                        }
                    ],
                }
            ).encode("utf-8")
        ),
    ],
)
def test_non_streaming_unexpected_read_or_close_failure_terminalizes_once(
    monkeypatch, stream: httpx.AsyncByteStream
) -> None:
    enable(monkeypatch)
    stable = FakeStable(lambda request: httpx.Response(200, stream=stream))

    with TestClient(app_for(stable), raise_server_exceptions=False) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=valid_payload(),
            headers=headers(),
        )

    assert response.status_code == 503
    assert stable.dispatched == 1
    assert len(stable.completed) == 1
    assert stable.completed[0]["status"] == "failed"
    assert stable.completed[0]["result_class"] == "transient_failure"
    assert stable.completed[0]["error_code"] == (
        "ai_research_bridge_response_read_failed"
    )
    assert stable.transport.last_client is not None
    assert stable.transport.last_client.is_closed


def test_non_streaming_http_hard_failure_wins_over_unexpected_close_failure(
    monkeypatch,
) -> None:
    enable(monkeypatch)
    stream = UnexpectedCloseStream(b'{"error":"denied"}')
    stable = HardFailureStable(lambda request: httpx.Response(401, stream=stream))

    with TestClient(app_for(stable), raise_server_exceptions=False) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=valid_payload(),
            headers=headers(),
        )

    assert response.status_code == 502
    assert len(stable.completed) == 1
    assert stable.completed[0]["status"] == "failed"
    assert stable.completed[0]["result_class"] == "hard_failure"
    assert stable.completed[0]["error_code"] == "provider_chat_http_401"
    assert stable.completed[0]["hard_failure"] is True


def test_non_streaming_response_with_other_model_fails_closed(monkeypatch) -> None:
    enable(monkeypatch)
    stable = FakeStable(
        lambda request: httpx.Response(
            200,
            json={
                "model": "provider/unexpected-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "wrong route"},
                    }
                ],
            },
        )
    )
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=valid_payload(),
            headers=headers(),
        )
    assert response.status_code == 502
    assert "wrong route" not in response.text
    assert stable.completed[0]["status"] == "failed"
    assert stable.completed[0]["result_class"] == "hard_failure"
    assert stable.completed[0]["error_code"] == "ai_research_bridge_model_mismatch"


def test_non_streaming_invalid_responses_store_only_contract_category(monkeypatch) -> None:
    enable(monkeypatch)
    cases = (
        (b"not-json", "ai_research_bridge_invalid_json"),
        (
            json.dumps({"model": MODEL_ID, "choices": []}).encode("utf-8"),
            "ai_research_bridge_missing_choices",
        ),
        (
            json.dumps(
                {
                    "model": MODEL_ID,
                    "choices": [{"message": {"role": "assistant", "content": ""}}],
                }
            ).encode("utf-8"),
            "ai_research_bridge_empty_completion",
        ),
    )
    for content, expected_code in cases:
        stable = FakeStable(lambda request, value=content: httpx.Response(200, content=value))
        with TestClient(app_for(stable)) as api:
            response = api.post(
                "/api/ai-research/v1/chat/completions",
                json=valid_payload(),
                headers=headers(),
            )
        assert response.status_code == 502
        assert response.json()["detail"] == "fixed model returned an invalid response"
        assert stable.completed[0]["error_code"] == expected_code


def test_rejects_total_text_over_bridge_limit_before_dispatch(monkeypatch) -> None:
    enable(monkeypatch)
    stable = FakeStable(lambda request: httpx.Response(500))
    payload = valid_payload()
    payload["messages"] = [
        {"role": "user", "content": "x" * 32_768} for _ in range(4)
    ]
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=payload,
            headers=headers(),
        )
    assert response.status_code == 422
    assert stable.dispatched == 0


def test_ordinary_hypothesis_accepts_total_text_over_literature_limit(
    monkeypatch,
) -> None:
    enable_hypothesis(monkeypatch)
    stable = FakeStable(
        lambda request: httpx.Response(
            200,
            json={
                "model": HYPOTHESIS_MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"hypothesis":"bounded"}',
                        },
                    }
                ],
            },
        )
    )
    payload = ordinary_hypothesis_payload()
    payload["messages"] = [
        {"role": "system", "content": "Return a JSON object."},
        {"role": "user", "content": "a" * 70_000},
        {"role": "user", "content": "b" * 70_000},
    ]

    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=payload,
            headers=headers(),
        )

    assert response.status_code == 200
    assert stable.readiness_calls == [(HYPOTHESIS_MODEL_ID, "chat_text")]
    assert stable.scoped_capabilities == ["chat_text"]
    assert stable.scoped_requirements == [("chat_text",)]
    assert stable.dispatched == 1


def test_hypothesis_model_accepts_bounded_canonical_chunked_artifact_context(
    monkeypatch,
) -> None:
    enable_hypothesis(monkeypatch)
    bind_phase_prompt(monkeypatch, TEXT_PHASE, LOCKED_TEXT_PHASE_PROMPT)
    stable = FakeStable(
        lambda request: httpx.Response(
            200,
            json={
                "model": HYPOTHESIS_MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"ok":true}'},
                    }
                ],
            },
        )
    )
    payload = p2r_text_payload(
        artifacts=[("phase0/user_query.txt", "研究" + "x" * 220_000)]
    )
    assert len(payload["messages"]) == 4

    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=payload,
            headers=p2r_headers(TEXT_PHASE),
        )

    assert response.status_code == 200
    assert stable.dispatched == 1
    assert stable.scoped_capabilities == ["chat_text"]


def test_hypothesis_model_rejects_context_over_scoped_limit(monkeypatch) -> None:
    enable_hypothesis(monkeypatch)
    stable = FakeStable(lambda request: httpx.Response(500))
    payload = valid_payload()
    payload["model"] = HYPOTHESIS_MODEL_ID
    payload["messages"] = [
        {"role": "user", "content": "x" * 110_000}
        for _ in range(5)
    ]

    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=payload,
            headers=headers(),
        )

    assert response.status_code == 422
    assert stable.dispatched == 0


def test_accepts_single_ldr_synthesis_message_over_legacy_limit(monkeypatch) -> None:
    enable(monkeypatch)
    stable = FakeStable(
        lambda request: httpx.Response(
            200,
            json={
                "id": "chatcmpl-ldr-synthesis",
                "object": "chat.completion",
                "model": MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Review"},
                    }
                ],
            },
        )
    )
    payload = valid_payload()
    payload["messages"] = [{"role": "user", "content": "x" * 32_769}]
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=payload,
            headers=headers(),
        )
    assert response.status_code == 200
    assert stable.dispatched == 1


def test_streaming_request_preserves_sse_and_records_terminal(monkeypatch) -> None:
    enable(monkeypatch)
    events = (
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{"content":"Review"}},"finish_reason":null}}]}}\n\n'
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{}},"finish_reason":"stop"}}],"usage":{{"prompt_tokens":11,"completion_tokens":2,"total_tokens":13}}}}\n\n'
        "data: [DONE]\n\n"
    )
    stable = FakeStable(
        lambda request: httpx.Response(
            200, content=events.encode("utf-8"), headers={"content-type": "text/event-stream"}
        )
    )
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=valid_payload(stream=True),
            headers=headers(),
        )
    assert response.status_code == 200
    assert response.text.endswith("data: [DONE]\n\n")
    assert response.headers["x-modelmirror-route-run-id"] == "chatrun_test"
    assert stable.completed[0]["status"] == "succeeded"
    assert stable.completed[0]["total_tokens"] == 13


def test_streaming_buffers_until_identity_then_allows_omitted_model() -> None:
    stable = FakeStable(lambda request: httpx.Response(500))
    before_identity = (
        'data: {"choices":[{"delta":{"content":"held"},"finish_reason":null}]}\n\n'
    )
    identity = (
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{"content":"bound"}},"finish_reason":null}}]}}\n\n'
    )
    after_identity = (
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    )
    done = "data: [DONE]\n\n"
    stream = ChunkedStream(
        before_identity.encode("utf-8"),
        identity.encode("utf-8"),
        after_identity.encode("utf-8"),
        done.encode("utf-8"),
    )

    async def exercise() -> list[str]:
        _client, body = direct_stream(stable, stream)
        return [chunk async for chunk in body]

    yielded = asyncio.run(exercise())
    assert yielded[0] == before_identity + identity
    assert "".join(yielded) == before_identity + identity + after_identity + done
    assert len(stable.completed) == 1
    assert stable.completed[0]["status"] == "succeeded"
    assert stable.completed[0]["actual_model"] == MODEL_ID


@pytest.mark.parametrize(
    ("event", "expected_code"),
    [
        (
            'data: {"model":"provider/unexpected-model","choices":[{"delta":{"content":"wrong"},"finish_reason":"stop"}]}\n\n',
            "ai_research_bridge_model_mismatch",
        ),
        (
            'data: {"choices":[{"delta":{"content":"unattributed"},"finish_reason":"stop"}]}\n\n',
            "ai_research_bridge_model_identity_required",
        ),
    ],
)
def test_streaming_identity_failure_releases_no_payload(
    event: str, expected_code: str
) -> None:
    stable = FakeStable(lambda request: httpx.Response(500))
    stream = ChunkedStream(event.encode("utf-8"), b"data: [DONE]\n\n")
    yielded: list[str] = []

    async def exercise() -> httpx.AsyncClient:
        client, body = direct_stream(stable, stream)
        with pytest.raises(RuntimeError, match=expected_code):
            async for chunk in body:
                yielded.append(chunk)
        return client

    client = asyncio.run(exercise())
    assert yielded == []
    assert len(stable.completed) == 1
    assert stable.completed[0]["status"] == "failed"
    assert stable.completed[0]["result_class"] == "hard_failure"
    assert stable.completed[0]["error_code"] == expected_code
    assert client.is_closed
    assert stream.close_calls >= 1


def test_streaming_late_model_mismatch_does_not_release_mismatched_event() -> None:
    stable = FakeStable(lambda request: httpx.Response(500))
    accepted = (
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{"content":"safe"}},"finish_reason":null}}]}}\n\n'
    )
    rejected = (
        'data: {"model":"provider/unexpected-model","choices":[{"delta":{"content":"wrong"},"finish_reason":"stop"}]}\n\n'
    )
    stream = ChunkedStream(accepted.encode("utf-8"), rejected.encode("utf-8"))
    yielded: list[str] = []

    async def exercise() -> None:
        _client, body = direct_stream(stable, stream)
        with pytest.raises(RuntimeError, match="ai_research_bridge_model_mismatch"):
            async for chunk in body:
                yielded.append(chunk)

    asyncio.run(exercise())
    assert yielded == [accepted]
    assert "wrong" not in "".join(yielded)
    assert len(stable.completed) == 1
    assert stable.completed[0]["error_code"] == "ai_research_bridge_model_mismatch"


@pytest.mark.parametrize(
    "bad_event",
    [
        "data: provider-secret\n\n",
        'data: {"error":{"message":"provider-secret","type":"upstream"}}\n\n',
    ],
)
@pytest.mark.parametrize("identity_first", [False, True])
def test_streaming_invalid_event_is_rejected_before_release(
    bad_event: str, identity_first: bool
) -> None:
    stable = FakeStable(lambda request: httpx.Response(500))
    accepted = (
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{"content":"safe"}},"finish_reason":null}}]}}\n\n'
    )
    chunks = (
        (accepted.encode("utf-8"), bad_event.encode("utf-8"))
        if identity_first
        else (bad_event.encode("utf-8"), accepted.encode("utf-8"))
    )
    stream = ChunkedStream(*chunks)
    yielded: list[str] = []

    async def exercise() -> None:
        _client, body = direct_stream(stable, stream)
        with pytest.raises(RuntimeError, match="provider_chat_invalid_sse"):
            async for chunk in body:
                yielded.append(chunk)

    asyncio.run(exercise())
    assert yielded == ([accepted] if identity_first else [])
    assert "provider-secret" not in "".join(yielded)
    assert len(stable.completed) == 1
    assert stable.completed[0]["status"] == "failed"
    assert stable.completed[0]["result_class"] == "hard_failure"
    assert stable.completed[0]["error_code"] == "provider_chat_invalid_sse"


@pytest.mark.parametrize("invalid_model", [None, 7, ""])
def test_streaming_explicit_invalid_model_before_binding_releases_no_payload(
    invalid_model: object,
) -> None:
    stable = FakeStable(lambda request: httpx.Response(500))
    invalid = (
        "data: "
        + json.dumps(
            {
                "model": invalid_model,
                "choices": [
                    {"delta": {"content": "unattributed"}, "finish_reason": None}
                ],
            }
        )
        + "\n\n"
    )
    identity = (
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{"content":"bound"}},"finish_reason":"stop"}}]}}\n\n'
    )
    stream = ChunkedStream(invalid.encode("utf-8"), identity.encode("utf-8"))
    yielded: list[str] = []

    async def exercise() -> None:
        _client, body = direct_stream(stable, stream)
        with pytest.raises(
            RuntimeError,
            match="ai_research_bridge_model_identity_invalid",
        ):
            async for chunk in body:
                yielded.append(chunk)

    asyncio.run(exercise())
    assert yielded == []
    assert len(stable.completed) == 1
    assert stable.completed[0]["result_class"] == "hard_failure"
    assert stable.completed[0]["error_code"] == (
        "ai_research_bridge_model_identity_invalid"
    )


@pytest.mark.parametrize("invalid_model", [None, 7, ""])
def test_streaming_explicit_invalid_model_after_binding_is_not_released(
    invalid_model: object,
) -> None:
    stable = FakeStable(lambda request: httpx.Response(500))
    accepted = (
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{"content":"safe"}},"finish_reason":null}}]}}\n\n'
    )
    invalid = (
        "data: "
        + json.dumps(
            {
                "model": invalid_model,
                "choices": [
                    {"delta": {"content": "unattributed"}, "finish_reason": "stop"}
                ],
            }
        )
        + "\n\n"
    )
    stream = ChunkedStream(accepted.encode("utf-8"), invalid.encode("utf-8"))
    yielded: list[str] = []

    async def exercise() -> None:
        _client, body = direct_stream(stable, stream)
        with pytest.raises(
            RuntimeError,
            match="ai_research_bridge_model_identity_invalid",
        ):
            async for chunk in body:
                yielded.append(chunk)

    asyncio.run(exercise())
    assert yielded == [accepted]
    assert "unattributed" not in "".join(yielded)
    assert len(stable.completed) == 1
    assert stable.completed[0]["result_class"] == "hard_failure"
    assert stable.completed[0]["error_code"] == (
        "ai_research_bridge_model_identity_invalid"
    )


def test_streaming_identity_buffer_is_bounded_before_any_payload_release() -> None:
    stable = FakeStable(lambda request: httpx.Response(500))
    stream = ChunkedStream(
        b"data: " + b"x" * (bridge.MAX_STREAM_IDENTITY_BUFFER_BYTES + 1)
    )
    yielded: list[str] = []

    async def exercise() -> None:
        _client, body = direct_stream(stable, stream)
        with pytest.raises(
            RuntimeError,
            match="ai_research_bridge_stream_identity_buffer_exceeded",
        ):
            async for chunk in body:
                yielded.append(chunk)

    asyncio.run(exercise())
    assert yielded == []
    assert len(stable.completed) == 1
    assert stable.completed[0]["result_class"] == "hard_failure"


def test_streaming_final_failed_pending_event_is_not_released() -> None:
    stable = FakeStable(lambda request: httpx.Response(500))
    accepted = (
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{"content":"safe"}},"finish_reason":null}}]}}\n\n'
    )
    invalid_pending = 'data: {"choices":['
    stream = ChunkedStream(
        accepted.encode("utf-8"), invalid_pending.encode("utf-8")
    )
    yielded: list[str] = []

    async def exercise() -> None:
        _client, body = direct_stream(stable, stream)
        with pytest.raises(RuntimeError, match="provider_chat_invalid_sse"):
            async for chunk in body:
                yielded.append(chunk)

    asyncio.run(exercise())
    assert yielded == [accepted]
    assert invalid_pending not in "".join(yielded)
    assert len(stable.completed) == 1
    assert stable.completed[0]["status"] == "failed"
    assert stable.completed[0]["error_code"] == "provider_chat_invalid_sse"


def test_streaming_aclose_terminalizes_client_cancelled_exactly_once() -> None:
    stable = FakeStable(lambda request: httpx.Response(500))
    event = (
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{"content":"Review"}},"finish_reason":null}}]}}\n\n'
    )
    stream = ChunkedStream(event.encode("utf-8"), b"data: [DONE]\n\n")

    async def exercise() -> httpx.AsyncClient:
        client, body = direct_stream(stable, stream)
        assert await anext(body) == event
        await body.aclose()
        await body.aclose()
        return client

    client = asyncio.run(exercise())
    assert stable.completed == [
        {
            "status": "cancelled",
            "result_class": "client_cancelled",
            "error_code": "provider_chat_client_cancelled",
            "client_cancelled": True,
            "e2e_ms": stable.completed[0]["e2e_ms"],
        }
    ]
    assert client.is_closed
    assert stream.close_calls >= 1


def test_streaming_aclose_close_failure_still_closes_client_once() -> None:
    stable = FakeStable(lambda request: httpx.Response(500))
    event = (
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{"content":"Review"}},"finish_reason":null}}]}}\n\n'
    )
    stream = UnexpectedCloseStream(event.encode("utf-8"), b"data: [DONE]\n\n")

    async def exercise() -> httpx.AsyncClient:
        client, body = direct_stream(stable, stream)
        assert await anext(body) == event
        with pytest.raises(RuntimeError, match="unexpected response close failure"):
            await body.aclose()
        return client

    client = asyncio.run(exercise())
    assert len(stable.completed) == 1
    assert stable.completed[0]["status"] == "cancelled"
    assert stable.completed[0]["result_class"] == "client_cancelled"
    assert client.is_closed
    assert stream.close_calls >= 1


def test_streaming_natural_close_failure_propagates_without_reterminalizing() -> None:
    stable = FakeStable(lambda request: httpx.Response(500))
    event = (
        f'data: {{"model":"{MODEL_ID}","choices":[{{"delta":{{"content":"Review"}},"finish_reason":"stop"}}]}}\n\n'
    )
    done = "data: [DONE]\n\n"
    stream = UnexpectedCloseStream(
        event.encode("utf-8"), done.encode("utf-8")
    )
    yielded: list[str] = []

    async def exercise() -> httpx.AsyncClient:
        client, body = direct_stream(stable, stream)
        with pytest.raises(RuntimeError, match="unexpected response close failure"):
            async for chunk in body:
                yielded.append(chunk)
        return client

    client = asyncio.run(exercise())
    assert yielded == [event, done]
    assert len(stable.completed) == 1
    assert stable.completed[0]["status"] == "failed"
    assert stable.completed[0]["result_class"] == "transient_failure"
    assert stable.completed[0]["error_code"] == "ai_research_bridge_stream_failed"
    assert client.is_closed
    assert stream.close_calls >= 1


def test_upstream_failure_is_sanitized_and_finalized(monkeypatch) -> None:
    enable(monkeypatch)
    stable = FakeStable(
        lambda request: httpx.Response(
            500, json={"error": "provider-secret-should-not-pass-through"}
        )
    )
    with TestClient(app_for(stable)) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=valid_payload(),
            headers=headers(),
        )
    assert response.status_code == 503
    assert "provider-secret" not in response.text
    assert stable.completed[0]["status"] == "failed"


def test_unexpected_dispatch_failure_is_sanitized_and_finalized(monkeypatch) -> None:
    enable(monkeypatch)

    def fail_before_response(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("provider-secret-in-unexpected-error")

    stable = FakeStable(fail_before_response)
    with TestClient(app_for(stable), raise_server_exceptions=False) as api:
        response = api.post(
            "/api/ai-research/v1/chat/completions",
            json=valid_payload(),
            headers=headers(),
        )
    assert response.status_code == 503
    assert "provider-secret" not in response.text
    assert stable.completed[0]["status"] == "failed"
    assert stable.completed[0]["error_code"] == "ai_research_bridge_dispatch_failed"
