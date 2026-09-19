"""RPG server-to-server bridge. No catalog refresh, certification or retries."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .api import get_model_router_service
from .chat_stable import ProviderChatStableService

router = APIRouter(prefix="/api/rpg/v1", tags=["rpg-service"])
PARAMETERS = {"temperature": 0.7, "top_p": 0.8, "max_tokens": 16384}
def parameters_for(model):
    # User-approved B5 exception; no blanket parameter stripping.
    return {"max_tokens": 16384} if model == "openai/gpt-5.6-luna" else dict(PARAMETERS)


MAX_REQUEST_BYTES = 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def require_service(request: Request):
    token = os.getenv("RPG_S2S_TOKEN", "")
    if os.getenv("RPG_S2S_ENABLED", "").lower() != "true" or len(token) < 32:
        raise HTTPException(503, "rpg_service_disabled")
    if request.headers.get("origin") or request.headers.get("sec-fetch-site"):
        raise HTTPException(403, "rpg_service_only")
    supplied = request.headers.get("authorization", "")
    if not secrets.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
        raise HTTPException(401, "rpg_service_unauthorized")


def get_stable(_=Depends(require_service)):
    return ProviderChatStableService(get_model_router_service())


def json_object(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def catalog(stable):
    """Project cached, exact-route evidence. Missing compatibility stays unavailable."""
    policy = stable.control.get_policy()
    route = next((r for r in policy.routes if r.capability == "chat_text"), None)
    connections = list(route.connection_ids if route else [])
    if policy.effective_mode == "newapi_required_default":
        connections = connections[:1]
    tenant = stable.router_service.tenant_id
    result = {}
    for connection in connections:
        offset = 0
        while True:
            rows = stable.repository.list_catalog_models(tenant, connection_id=connection, status="active", limit=500, offset=offset)
            for row in rows:
                model = row["model_id"]
                if model in result and result[model]["available"]:
                    continue
                meta = json_object(row.get("metadata_json"))
                qualification, _ = stable.control.current_qualification(connection_id=connection, model_id=model, capability="chat_text", require_exact_model=True)
                ready, _ = stable.readiness_scoped_certified(model)
                supported = meta.get("supported_parameters")
                context = meta.get("context_length")
                output = meta.get("max_output_tokens")
                effective_parameters = parameters_for(model)
                compatible = (type(context) is int and context > PARAMETERS["max_tokens"] and
                              type(output) is int and output >= PARAMETERS["max_tokens"] and
                              isinstance(supported, list) and all(p in supported for p in effective_parameters))
                binding = {"model": model, "connection": connection, "policy": policy.policy_fingerprint,
                           "qualification": qualification, "refresh": row.get("last_refresh_id"), "metadata": meta}
                item = {"selectionId": digest([connection, model]), "selectionRevision": digest(binding),
                        "model": model, "name": str(meta.get("name") or model),
                        "available": bool(ready and qualification and compatible),
                        "contextLength": context if type(context) is int else None,
                        "maxOutputTokens": output if type(output) is int else None,
                        "parameters": effective_parameters, "_binding": binding}
                # Only exact-connection reported prices; absent/ambiguous prices are omitted.
                offerings = stable.repository.list_catalog_offerings(tenant, connection_id=connection, model_id=model, operation="chat", include_stale=False)
                prices = [json_object(o.get("pricing_json")) for o in offerings]
                prices = [p for p in prices if p.get("status") == "reported" and p.get("source") == "provider_catalog"]
                if len(prices) == 1:
                    item["pricing"] = {k: prices[0][k] for k in ("currency", "unit", "input_price", "output_price", "observed_at", "source", "billing_authoritative") if k in prices[0]}
                result[model] = item
            if len(rows) < 500:
                break
            offset += len(rows)
    return list(result.values())


@router.get("/models", dependencies=[Depends(require_service)])
def models(stable=Depends(get_stable)):
    return {"models": [{k: v for k, v in x.items() if not k.startswith("_")} for x in catalog(stable)]}


async def body(request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "rpg_request_too_large")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        raise HTTPException(422, "rpg_request_invalid") from None
    fields = {"sessionId", "requestId", "selectionId", "selectionRevision", "messages", "parameters"}
    if not isinstance(value, dict) or set(value) != fields:
        raise HTTPException(422, "rpg_request_invalid")
    for key in fields - {"messages", "parameters"}:
        if not isinstance(value[key], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value[key]):
            raise HTTPException(422, "rpg_identity_invalid")
    if (value["parameters"] not in (PARAMETERS, {"max_tokens": 16384}) or not isinstance(value["parameters"], dict) or
            any(type(v) not in (int, float) for v in value["parameters"].values())):
        raise HTTPException(422, "rpg_parameters_mismatch")
    messages = value["messages"]
    if not isinstance(messages, list) or len(messages) < 2 or len(messages) % 2:
        raise HTTPException(422, "rpg_messages_invalid")
    for i, message in enumerate(messages):
        role = "system" if i == 0 else ("user" if i % 2 else "assistant")
        if (not isinstance(message, dict) or set(message) != {"role", "content"} or
                message["role"] != role or not isinstance(message["content"], str)):
            raise HTTPException(422, "rpg_messages_invalid")
    return value


def claim(stable, value):
    """An incomplete claim is intentionally never replayable, including after restart."""
    folder = stable.repository.storage_dir / "rpg-bridge-requests"
    folder.mkdir(parents=True, exist_ok=True)
    identity = digest([stable.router_service.tenant_id, value["sessionId"], value["requestId"]])
    try:
        with (folder / (identity + ".json")).open("x", encoding="utf-8") as output:
            json.dump({"requestHash": digest(value), "status": "claimed-no-replay"}, output)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        raise HTTPException(409, "rpg_request_already_claimed") from None


def parse_sse(sse, model, evidence=None):
    evidence = evidence if evidence is not None else {}
    raw, actual, finish, usage, done = "", None, None, None, False
    blocks = sse.replace("\r\n", "\n").split("\n\n")
    if blocks.pop().strip():
        raise ValueError("rpg_incomplete_event")
    for block in blocks:
        data = "\n".join(line[5:].lstrip() for line in block.split("\n") if line.startswith("data:"))
        if not data:
            continue
        if done:
            raise ValueError("rpg_event_after_done")
        if data == "[DONE]":
            done = True
            continue
        event = json.loads(data)
        if not isinstance(event, dict) or event.get("error"):
            raise ValueError("rpg_provider_error")
        if event.get("model"):
            actual = event["model"]
            evidence["actual"] = actual
            if actual != model:
                raise ValueError("rpg_model_mismatch")
        if isinstance(event.get("usage"), dict):
            usage = {k: v for k, v in event["usage"].items() if k in ("prompt_tokens", "completion_tokens", "total_tokens") and type(v) is int and v >= 0}
        choices = event.get("choices") or []
        if len(choices) > 1:
            raise ValueError("rpg_multiple_choices")
        if choices:
            choice = choices[0]
            delta = choice.get("delta") or {}
            if delta.get("tool_calls") or delta.get("function_call"):
                raise ValueError("rpg_non_text_output")
            content = delta.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError("rpg_non_text_output")
            raw += content or ""
            evidence["raw"] = raw
            finish = choice.get("finish_reason") or finish
    if not done or finish != "stop" or not raw.strip():
        raise ValueError("rpg_incomplete_output")
    return raw, actual, usage


@router.post("/chat/completions", dependencies=[Depends(require_service)])
async def completion(request: Request, stable=Depends(get_stable)):
    value = await body(request)
    selected = next((m for m in catalog(stable) if m["selectionId"] == value["selectionId"]), None)
    if not selected or not selected["available"] or selected["selectionRevision"] != value["selectionRevision"]:
        raise HTTPException(409, "rpg_selection_unavailable_or_changed")
    if value["parameters"] != selected["parameters"]:
        raise HTTPException(422, "rpg_parameters_mismatch")
    # Conservative byte-based admission estimate, not an exact model tokenizer.
    # Never truncate history to make a request fit.
    estimated = sum(len(m["content"].encode()) + 32 for m in value["messages"]) + 256
    if estimated + PARAMETERS["max_tokens"] > selected["contextLength"]:
        raise HTTPException(409, "rpg_context_capacity_exceeded")
    claim(stable, value)
    preflight = await stable.begin_rpg_certified(selected["model"])
    dispatch = preflight.dispatch
    if not dispatch:
        raise HTTPException(409, "rpg_route_unavailable")
    binding = selected["_binding"]
    qualification = binding["qualification"]
    if (dispatch.target.connection_id != binding["connection"] or dispatch.policy_fingerprint != binding["policy"] or
            dispatch.certification_id != qualification["certification_id"] or
            dispatch.connection_fingerprint != qualification["connection_fingerprint"]):
        stable.fail_undispatched(dispatch, error_code="rpg_selection_changed")
        raise HTTPException(409, "rpg_selection_changed")
    payload = {"model": selected["model"], "messages": value["messages"], **selected["parameters"],
               "stream": True, "stream_options": {"include_usage": True}}
    if dispatch.target.provider_kind == "openrouter":
        provider = {"allow_fallbacks": False, "require_parameters": True}
        tag = binding["metadata"].get("rpg_provider_tag")
        if tag is not None:
            if not isinstance(tag, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_/-]{0,100}", tag):
                stable.fail_undispatched(dispatch, error_code="rpg_endpoint_binding_invalid")
                raise HTTPException(409, "rpg_endpoint_binding_invalid")
            provider["only"] = [tag]
            provider["order"] = [tag]
        payload.update(provider=provider, transforms=[])
    raw, sse, actual, usage, error, sent = "", "", None, None, None, False
    http_status = None
    parsed = {}
    try:
        async with asyncio.timeout(240), httpx.AsyncClient(**stable.transport.client_kwargs()) as client:
            outbound = stable.transport.build_authorized_stream_request(client, dispatch.target, dispatch.authorized, payload)
            fresh = next((m for m in catalog(stable) if m["selectionId"] == selected["selectionId"]), None)
            if not fresh or not fresh["available"] or fresh["selectionRevision"] != selected["selectionRevision"]:
                raise ValueError("rpg_selection_changed")
            stable.mark_dispatched(dispatch)
            sent = True
            response = await stable.transport.send_authorized_stream(client, outbound)
            try:
                http_status = response.status_code
                if not response.is_success or "text/event-stream" not in response.headers.get("content-type", ""):
                    raise ValueError("rpg_upstream_http")
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise ValueError("rpg_response_too_large")
                    chunks.append(chunk)
                sse = b"".join(chunks).decode("utf-8", errors="strict")
                raw, actual, usage = parse_sse(sse, selected["model"], parsed)
                if await request.is_disconnected():
                    raise ValueError("rpg_client_disconnected")
            finally:
                await response.aclose()
    except (Exception, asyncio.CancelledError) as exc:
        raw, actual = parsed.get("raw", ""), parsed.get("actual")
        allowed = {"rpg_selection_changed", "rpg_upstream_http", "rpg_response_too_large", "rpg_model_mismatch", "rpg_incomplete_output", "rpg_client_disconnected", "rpg_non_text_output", "rpg_incomplete_event", "rpg_provider_error", "rpg_event_after_done", "rpg_multiple_choices"}
        error = str(exc) if str(exc) in allowed else "rpg_transport_failed_or_unknown"
    secret = dispatch.target.api_key
    if secret and (secret in raw or secret in sse or secret in str(actual)):
        raw, sse, actual, error = "", "", None, "rpg_credential_echo_blocked"
    if sent:
        classification, hard_failure = ("request_failure" if error else "success"), False
        if error == "rpg_upstream_http" and http_status is not None and http_status >= 400:
            classification, _, hard_failure = stable.classify_http_failure(http_status)
        cancelled = error == "rpg_client_disconnected"
        try:
            persisted = stable.complete(dispatch, status="cancelled" if cancelled else "failed" if error else "succeeded",
                result_class=classification, error_code=error, hard_failure=hard_failure, client_cancelled=cancelled,
                actual_model=actual, **(usage or {}))
            if not persisted:
                error = "rpg_receipt_pending"
        except Exception:
            error = "rpg_receipt_pending"
    else:
        stable.fail_undispatched(dispatch, error_code=error or "rpg_not_dispatched")
    receipt = {"gateway": "rpg_scoped", "requestId": value["requestId"], "sessionId": value["sessionId"],
               "runId": dispatch.run_id, "attemptId": dispatch.attempt_id,
               "selectionId": selected["selectionId"], "selectionRevision": selected["selectionRevision"],
               "requestedModel": selected["model"], "actualModel": actual,
               "parameters": dict(selected["parameters"]), "httpStatus": http_status, "dispatched": sent,
               "usage": usage, "error": error, "retries": 0, "requestHash": digest(value),
               "rawHash": hashlib.sha256(raw.encode()).hexdigest(), "sseHash": hashlib.sha256(sse.encode()).hexdigest(),
               "status": "failed_or_unknown" if error else "complete"}
    return JSONResponse({"raw": raw, "sse": sse, "receipt": receipt}, status_code=502 if error else 200)
