from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from httpx import MockTransport, Request, Response

from server.model_router.chat_certification import (
    CHAT_TEXT_CERTIFICATION_MAX_TOKENS,
    ProviderChatCertificationService,
    SYNTHETIC_FILE_TOOL_NAME,
    SYNTHETIC_CERTIFICATION_PROMPT,
    SYNTHETIC_TOOL_NAME,
)
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.schemas import RouterConnectionCreate, RouterConnectionUpdate
from server.model_router.service import ModelRouterService, RouterServiceError


def _service(
    tmp_path: Path,
    handler,
    *,
    addresses: list[str] | None = None,
    kind: str = "newapi",
) -> tuple[ProviderChatCertificationService, str, list[Request]]:
    requests: list[Request] = []

    def recording_handler(request: Request) -> Response:
        requests.append(request)
        return handler(request)

    repository = SQLiteRouterRepository(tmp_path)
    connection = repository.create_connection(
        "local",
        RouterConnectionCreate(
            name="newAPI",
            kind=kind,
            base_url="https://newapi.example/v1",
            api_key="certification-secret",
            scopes=["chat"],
        ),
    )
    router_service = ModelRouterService(
        repository,
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(recording_handler),
            follow_redirects=False,
            trust_env=False,
        ),
        egress_policy=ProviderEgressPolicy(
            resolver=lambda _host, _port: addresses or ["8.8.8.8"]
        ),
    )
    certification = ProviderChatCertificationService(
        router_service,
        client_factory=lambda: httpx.AsyncClient(
            transport=MockTransport(recording_handler),
            follow_redirects=False,
            trust_env=False,
        ),
    )
    return certification, connection.id, requests


def _handler(request: Request) -> Response:
    if request.method == "GET":
        return Response(200, json={"data": [{"id": "provider/model"}]})
    body = json.loads(request.content)
    assert body == {
        "model": "provider/model",
        "messages": [{"role": "user", "content": SYNTHETIC_CERTIFICATION_PROMPT}],
        "stream": True,
        "temperature": 0,
        "max_tokens": CHAT_TEXT_CERTIFICATION_MAX_TOKENS,
    }
    return Response(
        200,
        content=(
            b": keepalive\n\n"
            b"data: {\"model\":\"provider/model\",\"choices\":[{\"delta\":\n"
            b"data: {\"content\":\"OK\"},\"finish_reason\":null}]}\n\n"
            b"data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}],"
            b"\"usage\":{\"prompt_tokens\":3,\"completion_tokens\":1,"
            b"\"total_tokens\":4}}\n\n"
            b"data: [DONE]\n\n"
        ),
        headers={"content-type": "text/event-stream"},
    )


@pytest.mark.asyncio
async def test_certification_passes_with_one_chat_post_and_redacted_record(
    tmp_path: Path,
) -> None:
    service, connection_id, requests = _service(tmp_path, _handler)

    result = await service.run(
        connection_id,
        model_id="provider/model",
        acknowledge_billed_call=True,
        idempotency_key="one-call",
    )

    assert result.status == "passed"
    assert result.checks.model_dump() == {
        "catalog_ok": True,
        "model_present": True,
        "chat_http_ok": True,
        "text_delta_observed": True,
        "tool_call_observed": False,
        "file_output_contract_observed": False,
        "capability_verified": True,
        "stream_completed": True,
        "terminal_observed": True,
    }
    assert result.total_tokens == 4
    assert [request.method for request in requests] == ["GET", "POST"]
    assert requests[1].url.host == "8.8.8.8"
    assert "certification-secret" not in result.model_dump_json()
    assert SYNTHETIC_CERTIFICATION_PROMPT not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["newapi", "openrouter", "openai_compatible"])
async def test_text_certification_supports_every_managed_chat_provider_kind(
    tmp_path: Path,
    kind: str,
) -> None:
    service, connection_id, requests = _service(tmp_path, _handler, kind=kind)

    result = await service.run(
        connection_id,
        model_id="provider/model",
        capability="chat_text",
        acknowledge_billed_call=True,
        idempotency_key=f"kind-{kind}",
    )

    assert result.status == "passed"
    assert result.capability == "chat_text"
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_certification_rejects_connection_without_chat_scope_before_catalog_get(
    tmp_path: Path,
) -> None:
    service, connection_id, requests = _service(tmp_path, _handler)
    service.repository.update_connection(
        "local",
        connection_id,
        RouterConnectionUpdate(scopes=["audio"]),
    )

    with pytest.raises(RouterServiceError) as exc_info:
        await service.run(
            connection_id,
            model_id="provider/model",
            capability="chat_text",
            acknowledge_billed_call=True,
            idempotency_key="no-chat-scope",
        )

    assert exc_info.value.code == "connection_chat_scope_required"
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability,tool_name,arguments,check_name",
    [
        (
            "chat_tools",
            SYNTHETIC_TOOL_NAME,
            '{"value":"OK"}',
            "tool_call_observed",
        ),
        (
            "chat_file_output",
            SYNTHETIC_FILE_TOOL_NAME,
            '{"format_id":"plain_text","filename":"certification.txt",'
            '"content":"OK"}',
            "file_output_contract_observed",
        ),
    ],
)
async def test_capability_certification_observes_bounded_tool_contract_without_execution(
    tmp_path: Path,
    capability: str,
    tool_name: str,
    arguments: str,
    check_name: str,
) -> None:
    def handler(request: Request) -> Response:
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/model"}]})
        body = json.loads(request.content)
        assert body["tool_choice"]["function"]["name"] == tool_name
        assert body["parallel_tool_calls"] is False
        split_at = max(1, len(arguments) // 2)
        first = json.dumps(
            {
                "model": "provider/model",
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": arguments[:split_at],
                                    },
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            },
            separators=(",", ":"),
        )
        second = json.dumps(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {
                                        "arguments": arguments[split_at:],
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
            separators=(",", ":"),
        )
        return Response(
            200,
            content=(
                f"data: {first}\n\ndata: {second}\n\ndata: [DONE]\n\n"
            ).encode(),
            headers={"content-type": "text/event-stream"},
        )

    service, connection_id, requests = _service(tmp_path, handler)
    result = await service.run(
        connection_id,
        model_id="provider/model",
        capability=capability,
        acknowledge_billed_call=True,
        idempotency_key=f"capability-{capability}",
    )

    assert result.status == "passed"
    assert result.capability == capability
    assert result.checks.capability_verified is True
    assert getattr(result.checks, check_name) is True
    assert [request.method for request in requests] == ["GET", "POST"]


@pytest.mark.asyncio
async def test_idempotent_retry_never_sends_second_chat_post(tmp_path: Path) -> None:
    service, connection_id, requests = _service(tmp_path, _handler)
    first = await service.run(
        connection_id,
        model_id="provider/model",
        acknowledge_billed_call=True,
        idempotency_key="same-key",
    )
    second = await service.run(
        connection_id,
        model_id="provider/model",
        acknowledge_billed_call=True,
        idempotency_key="same-key",
    )

    assert second.certification_id == first.certification_id
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_missing_model_stops_before_paid_call(tmp_path: Path) -> None:
    service, connection_id, requests = _service(tmp_path, _handler)

    with pytest.raises(RouterServiceError) as exc_info:
        await service.run(
            connection_id,
            model_id="missing/model",
            acknowledge_billed_call=True,
            idempotency_key="missing",
        )

    assert exc_info.value.code == "provider_certification_model_not_found"
    assert [request.method for request in requests] == ["GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,error_code",
    [(401, "provider_chat_http_401"), (429, "provider_chat_http_429"), (503, "provider_chat_http_5xx")],
)
async def test_http_failures_are_stable_and_do_not_store_body(
    tmp_path: Path, status: int, error_code: str
) -> None:
    secret_body = "upstream-secret-response-body"

    def handler(request: Request) -> Response:
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/model"}]})
        return Response(status, text=secret_body)

    service, connection_id, requests = _service(tmp_path, handler)
    result = await service.run(
        connection_id,
        model_id="provider/model",
        acknowledge_billed_call=True,
        idempotency_key=f"http-{status}",
    )

    assert result.status == "failed"
    assert result.error_code == error_code
    assert secret_body not in result.model_dump_json()
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_invalid_or_empty_sse_fails_without_retry(tmp_path: Path) -> None:
    def handler(request: Request) -> Response:
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/model"}]})
        return Response(200, content=b"data: not-json\n\n")

    service, connection_id, requests = _service(
        tmp_path, handler, addresses=["8.8.8.8", "1.1.1.1"]
    )
    result = await service.run(
        connection_id,
        model_id="provider/model",
        acknowledge_billed_call=True,
        idempotency_key="invalid-sse",
    )

    assert result.error_code == "provider_chat_invalid_sse"
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content,error_code",
    [
        (b": keepalive\n\n", "provider_chat_empty_stream"),
        (
            b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n',
            "provider_chat_missing_terminal",
        ),
    ],
)
async def test_empty_or_nonterminal_stream_fails_closed(
    tmp_path: Path, content: bytes, error_code: str
) -> None:
    def handler(request: Request) -> Response:
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/model"}]})
        return Response(200, content=content)

    service, connection_id, requests = _service(tmp_path, handler)
    result = await service.run(
        connection_id,
        model_id="provider/model",
        acknowledge_billed_call=True,
        idempotency_key=error_code,
    )

    assert result.status == "failed"
    assert result.error_code == error_code
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_reasoning_only_length_stream_reports_visible_text_budget_exhausted(
    tmp_path: Path,
) -> None:
    def handler(request: Request) -> Response:
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/model"}]})
        return Response(
            200,
            content=(
                b'data: {"model":"provider/model","choices":[{"delta":'
                b'{"reasoning":"thinking"},"finish_reason":null}]}\n\n'
                b'data: {"choices":[{"delta":{},"finish_reason":"length"}],'
                b'"usage":{"prompt_tokens":87,"completion_tokens":64,'
                b'"total_tokens":151}}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    service, connection_id, requests = _service(tmp_path, handler)
    result = await service.run(
        connection_id,
        model_id="provider/model",
        acknowledge_billed_call=True,
        idempotency_key="reasoning-budget-exhausted",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_chat_visible_text_budget_exhausted"
    assert result.checks.stream_completed is True
    assert result.checks.terminal_observed is True
    assert result.checks.text_delta_observed is False
    assert result.completion_tokens == 64
    assert [request.method for request in requests].count("POST") == 1


@pytest.mark.asyncio
async def test_interrupted_stream_is_failed_and_never_replayed(tmp_path: Path) -> None:
    class InterruptedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"O"}}]}\n\n'
            raise httpx.ReadError("interrupted")

    def handler(request: Request) -> Response:
        if request.method == "GET":
            return Response(200, json={"data": [{"id": "provider/model"}]})
        return Response(200, stream=InterruptedStream())

    service, connection_id, requests = _service(
        tmp_path, handler, addresses=["8.8.8.8", "1.1.1.1"]
    )
    result = await service.run(
        connection_id,
        model_id="provider/model",
        acknowledge_billed_call=True,
        idempotency_key="interrupted",
    )

    assert result.status == "failed"
    assert result.error_code == "provider_chat_stream_interrupted"
    assert [request.method for request in requests].count("POST") == 1


def test_list_derives_stale_and_disabled_without_rewriting_record(tmp_path: Path) -> None:
    service, connection_id, _requests = _service(tmp_path, _handler)
    repository = service.repository
    repository.save_test_result(
        "local",
        connection_id,
        health="online",
        model_count=1,
        checked_at="2026-08-20T00:00:00+00:00",
    )
    row, _ = repository.claim_chat_certification(
        "local",
        certification_id="cert-stale",
        connection_id=connection_id,
        connection_fingerprint=repository.connection_config_fingerprint(
            "local", connection_id
        ),
        contract_version="modelmirror-provider-chat-v1",
        requested_model="provider/model",
        idempotency_key_hash="hash",
    )
    repository.complete_chat_certification(
        "local",
        str(row["id"]),
        status="passed",
        checks={},
        warning_codes=[],
    )
    repository.update_connection(
        "local",
        connection_id,
        RouterConnectionUpdate(base_url="https://changed.example/v1"),
    )
    stale = service.list().certifications[0]
    assert stale.status == "stale"

    repository.update_connection(
        "local", connection_id, RouterConnectionUpdate(enabled=False)
    )
    disabled = service.list().certifications[0]
    assert disabled.status == "stale"
    assert disabled.can_run is False
    assert disabled.blocked_reason == "connection_disabled"
