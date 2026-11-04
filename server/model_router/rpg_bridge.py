"""RPG server-to-server bridge. No catalog refresh, certification or retries."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import secrets

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from . import rpg_progress
from .api import get_model_router_service
from .chat_stable import ProviderChatStableService
from .provider_catalog import text_parameter_policy, TEXT_PARAMETER_CAPABILITIES_FORMAT

router = APIRouter(prefix="/api/rpg/v1", tags=["rpg-service"])
PARAMETERS = {"temperature": 0.7, "top_p": 0.8, "max_tokens": 16384}
def parameters_for(model, profile="default"):
    if profile not in ("default", "god-long-v1"):
        raise HTTPException(422, "rpg_parameter_profile_invalid")
    if profile == "god-long-v1":
        return {**PARAMETERS, "max_tokens": 32768} if model == "google/gemini-3.8-flash" else None
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


def get_authoring_stable(stable=Depends(get_stable)):
    from .rpg_authoring_route import enabled, AuthoringStableService
    return AuthoringStableService(stable.router_service, transport=stable.transport) if enabled() else stable


def json_object(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def catalog(stable, profile="default"):
    """Project cached, exact-route evidence. Missing compatibility stays unavailable."""
    parameters_for("google/gemini-3.8-flash", profile)
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
                effective_parameters = parameters_for(model, profile)
                if effective_parameters is None:
                    continue
                if model in result and result[model]["available"]:
                    continue
                meta = json_object(row.get("metadata_json"))
                qualification, _ = stable.control.current_qualification(connection_id=connection, model_id=model, capability="chat_text", require_exact_model=True)
                ready, _ = stable.readiness_scoped_certified(model)
                supported = meta.get("supported_parameters")
                context = meta.get("context_length")
                output = meta.get("max_output_tokens")
                compatible = (type(context) is int and context > effective_parameters["max_tokens"] and
                              type(output) is int and output >= effective_parameters["max_tokens"] and
                              isinstance(supported, list) and all(p in supported for p in effective_parameters))
                binding = {"model": model, "connection": connection, "policy": policy.policy_fingerprint,
                           "qualification": qualification, "refresh": row.get("last_refresh_id"), "metadata": meta}
                if profile != "default":
                    binding.update(parameterProfile=profile, parameters=effective_parameters)
                identity = [connection, model] if profile == "default" else [connection, model, profile]
                item = {"selectionId": digest(identity), "selectionRevision": digest(binding),
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
def models(stable=Depends(get_stable), profile: str = "default"):
    return {"models": [{k: v for k, v in x.items() if not k.startswith("_")} for x in catalog(stable, profile)]}


async def body(request, profile="default"):
    profile_parameters = parameters_for("google/gemini-3.8-flash", profile)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > (2 * MAX_REQUEST_BYTES + 1024 if request.headers.get("X-Rpg-Effective-Messages") == "1" else MAX_REQUEST_BYTES):
            raise HTTPException(413, "rpg_request_too_large")
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        raise HTTPException(422, "rpg_request_invalid") from None
    custom = value.pop("customStoryMessages", None) if isinstance(value, dict) else None
    fields = {"sessionId", "requestId", "selectionId", "selectionRevision", "messages", "parameters"}
    if not isinstance(value, dict) or set(value) != fields:
        raise HTTPException(422, "rpg_request_invalid")
    for key in fields - {"messages", "parameters"}:
        if not isinstance(value[key], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value[key]):
            raise HTTPException(422, "rpg_identity_invalid")
    allowed_parameters = (PARAMETERS, {"max_tokens": 16384}) if profile == "default" else (profile_parameters,)
    if (value["parameters"] not in allowed_parameters or not isinstance(value["parameters"], dict) or
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
    if custom is not None:
        if max(len(json.dumps({**value, "messages": items}, ensure_ascii=False, separators=(",", ":")).encode()) for items in (custom, messages)) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "rpg_request_too_large")
        if not isinstance(custom, list) or len(custom) < 2 or len(custom) > len(messages) + 1:
            raise HTTPException(422, "rpg_custom_story_invalid")
        for i, item in enumerate(custom):
            role = "system" if i == 0 else ("user" if i % 2 else "assistant")
            if (not isinstance(item, dict) or set(item) != {"role", "content"} or
                    item["role"] != role or not isinstance(item["content"], str)):
                raise HTTPException(422, "rpg_custom_story_invalid")
        if not custom[0]["content"].startswith(messages[0]["content"]):
            raise HTTPException(422, "rpg_custom_system_invalid")
        request.state.rpg_custom_story = custom
    elif len(data) > MAX_REQUEST_BYTES:
        raise HTTPException(413, "rpg_request_too_large")
    return value


def claim(stable, value, effective=None):
    """An incomplete claim is intentionally never replayable, including after restart."""
    folder = stable.repository.storage_dir / "rpg-bridge-requests"
    folder.mkdir(parents=True, exist_ok=True)
    identity = digest([stable.router_service.tenant_id, value["sessionId"], value["requestId"]])
    try:
        with (folder / (identity + ".json")).open("x", encoding="utf-8") as output:
            record = {"requestHash": digest(value), "status": "claimed-no-replay"}
            if effective is not None:
                record["effectiveRequestHash"] = digest({**value, "messages": effective})
            json.dump(record, output)
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


@router.get("/progress/{session_id}/{request_id}", dependencies=[Depends(require_service)])
def story_progress(session_id: str, request_id: str):
    preview = rpg_progress.active.get((session_id, request_id))
    return JSONResponse({"text": preview.visible() if preview else ""}, headers={"Cache-Control": "no-store"})


@router.post("/chat/completions", dependencies=[Depends(require_service)])
async def completion(request: Request, stable=Depends(get_stable), profile: str = "default"):
    value = await body(request, profile)
    selected = next((m for m in catalog(stable, profile) if m["selectionId"] == value["selectionId"]), None)
    if not selected or not selected["available"] or selected["selectionRevision"] != value["selectionRevision"]:
        raise HTTPException(409, "rpg_selection_unavailable_or_changed")
    if value["parameters"] != selected["parameters"]:
        raise HTTPException(422, "rpg_parameters_mismatch")
    # Conservative byte-based admission estimate, not an exact model tokenizer.
    # Never truncate history to make a request fit.
    custom = getattr(request.state, "rpg_custom_story", None)
    messages = value["messages"] if custom is None else custom
    estimated = sum(len(m["content"].encode()) + 32 for m in messages) + 256
    if estimated + selected["parameters"]["max_tokens"] > selected["contextLength"]:
        raise HTTPException(409, "rpg_context_capacity_exceeded")
    claim(stable, value, custom)
    preflight = await stable.begin_rpg_certified(selected["model"])
    dispatch = preflight.dispatch
    if not dispatch:
        # A completed preflight rejection is not an unknown Provider outcome.
        reason = "rpg_fallback_limit_exhausted" if preflight.error_code == "rpg_fallback_limit_exhausted" else "rpg_route_unavailable"
        receipt = {"gateway": "rpg_scoped", "requestId": value["requestId"], "sessionId": value["sessionId"],
                   "runId": None, "attemptId": None, "selectionId": selected["selectionId"],
                   "selectionRevision": selected["selectionRevision"], "requestedModel": selected["model"],
                   "actualModel": None, "parameters": dict(selected["parameters"]), "httpStatus": None,
                   "dispatched": False, "usage": None, "error": reason, "retries": 0,
                   "requestHash": digest(value), "rawHash": hashlib.sha256(b"").hexdigest(),
                   "sseHash": hashlib.sha256(b"").hexdigest(), "status": "failed"}
        return JSONResponse({"raw": "", "sse": "", "receipt": receipt}, status_code=409)
    binding = selected["_binding"]
    qualification = binding["qualification"]
    if (dispatch.target.connection_id != binding["connection"] or dispatch.policy_fingerprint != binding["policy"] or
            dispatch.certification_id != qualification["certification_id"] or
            dispatch.connection_fingerprint != qualification["connection_fingerprint"]):
        stable.fail_undispatched(dispatch, error_code="rpg_selection_changed")
        raise HTTPException(409, "rpg_selection_changed")
    payload = {"model": selected["model"], "messages": messages, **selected["parameters"],
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
    progress_key = (value["sessionId"], value["requestId"])
    preview = rpg_progress.TextPreview(selected["model"], (dispatch.target.api_key, os.getenv("RPG_S2S_TOKEN")))
    rpg_progress.active[progress_key] = preview
    try:
        async with asyncio.timeout(240), httpx.AsyncClient(**stable.transport.client_kwargs()) as client:
            outbound = stable.transport.build_authorized_stream_request(client, dispatch.target, dispatch.authorized, payload)
            fresh = next((m for m in catalog(stable, profile) if m["selectionId"] == selected["selectionId"]), None)
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
                    preview.feed(chunk)
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
    finally:
        rpg_progress.active.pop(progress_key, None)
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
    if custom is not None:
        receipt["effectiveRequestHash"] = digest({**value, "messages": messages})
    return JSONResponse({"raw": raw, "sse": sse, "receipt": receipt}, status_code=502 if error else 200)


AUTHORING_CATALOG_FORMAT = "modelmirror.rpg.control-model-catalog/1"
AUTHORING_CATALOG_SOURCE = "control-plane-cache"
AUTHORING_CATALOG_LIMIT = 1000


def _authoring_policy(meta, model):
    declaration = meta.get("parameter_capabilities")
    if "parameter_capabilities" in meta:
        try:
            if (not isinstance(declaration, dict) or set(declaration) != {"status", "format", "policy"}
                    or declaration["status"] != "declared" or declaration["format"] != TEXT_PARAMETER_CAPABILITIES_FORMAT):
                raise ValueError("invalid")
            policy = text_parameter_policy(declaration["policy"])
            supported = meta.get("supported_parameters")
            if (not isinstance(supported, list) or any(f["wireName"] not in supported for f in policy["fields"].values())
                    or type(meta.get("max_output_tokens")) is not int
                    or policy["fields"]["maxOutputTokens"]["maximum"] > meta["max_output_tokens"]):
                raise ValueError("invalid")
            return policy, "provider-declared"
        except (TypeError, ValueError, KeyError, OverflowError):
            return None, "invalid"
    # An old fixed profile is evidence only for those exact values, not a range.
    fixed = parameters_for(model)
    supported = meta.get("supported_parameters")
    if not isinstance(supported, list) or any(k not in supported for k in fixed):
        return None, "unknown"
    names = {"temperature": "temperature", "top_p": "topP", "max_tokens": "maxOutputTokens"}
    fields = {names[k]: {"minimum": n, "maximum": n, "defaultValue": n, "required": True, "wireName": k}
              for k, n in fixed.items()}
    return {"kind": "fixed", "fields": fields, "exclusive": []}, "legacy-fixed"


def _authoring_observed_metadata(stable, policy, connection, row, meta):
    """Use a bounded operator-captured OpenRouter record without editing the shared catalog."""
    if "parameter_capabilities" in meta or not hasattr(policy, "authoring_model_ids"):
        return meta
    capture = os.getenv("RPG_AUTHORING_OPENROUTER_OBSERVATION", "")
    if not capture or len(capture) > 16384:
        return meta
    try:
        from datetime import UTC, datetime, timedelta
        from .provider_catalog import normalize_provider_catalog
        observation = json.loads(capture, object_pairs_hook=_unique_json_object)
        expected = {"connectionId", "connectionFingerprint", "refreshId", "url", "readAt", "model"}
        if not isinstance(observation, dict) or set(observation) != expected:
            return meta
        raw = observation["model"]
        if (observation["url"] != "https://openrouter.ai/api/v1/models" or observation["connectionId"] != connection
                or observation["connectionFingerprint"] != policy.state["connectionFingerprint"]
                or observation["refreshId"] != row["last_refresh_id"]
                or set(raw) != {"id", "context_length", "top_provider", "supported_parameters"}
                or raw["id"] != row["model_id"]):
            return meta
        age = datetime.now(UTC) - datetime.fromisoformat(observation["readAt"])
        if not timedelta(0) <= age < timedelta(days=7):
            return meta
        target = stable.repository.get_connection(stable.router_service.tenant_id, connection)
        if target.kind != "openrouter" or str(target.base_url).rstrip("/") != "https://openrouter.ai/api/v1":
            return meta
        rows, _, count, truncated = normalize_provider_catalog([raw], connection=target, observed_at=observation["readAt"])
        if count != 1 or truncated:
            return meta
        verified = rows[0]["metadata"]
        if (verified.get("parameter_capabilities_source") != "openrouter-models-and-documented-api-ranges/1"
                or any(meta.get(k) != verified.get(k) for k in ("context_length", "max_output_tokens","supported_parameters"))):
            return meta
        return {**meta, "parameter_capabilities": verified["parameter_capabilities"],
                "parameter_capabilities_source": verified["parameter_capabilities_source"]}
    except (TypeError, ValueError, OverflowError, KeyError, RecursionError):
        return meta


def authoring_catalog(stable):
    """Read cached evidence only. Each connection/model is a distinct selection.

    Availability is a catalog admission snapshot, never a dispatch/budget permit.
    Unlike legacy catalog(), this does not advance a control-policy gate epoch.
    """
    policy = stable.control.peek_policy()
    tenant = stable.router_service.tenant_id
    items = []
    seen = set()
    for connection in stable.control.route_candidates(policy, "chat_text"):
        offset = 0
        while True:
            rows = stable.repository.list_catalog_models(tenant, connection_id=connection, status="active", limit=500, offset=offset)
            for row in rows:
                model = row["model_id"]
                if hasattr(policy, "authoring_model_ids") and model not in policy.authoring_model_ids:
                    continue
                if (connection, model) in seen or len(items) >= AUTHORING_CATALOG_LIMIT:
                    raise HTTPException(503, "rpg_authoring_catalog_limit_or_duplicate")
                seen.add((connection, model))
                meta = json_object(row.get("metadata_json"))
                name = meta.get("name") or model
                if any(not isinstance(s, str) or not s.strip() or any(0xD800 <= ord(c) <= 0xDFFF for c in s)
                       or len(s.encode("utf-16-le")) > 400 for s in (model, name)):
                    raise HTTPException(503, "rpg_authoring_catalog_invalid")
                admitted, error = stable.control.route_admission(policy, model, scoped_certified=True)
                qualification, reason = (None, error)
                if admitted and error is None:
                    # Most discovered models have no exact certification. Avoid
                    # decrypting route credentials repeatedly for those rows.
                    if stable.repository.get_latest_chat_certification(tenant, connection, model, "chat_text") is None:
                        reason = "provider_chat_capability_certification_required"
                    else:
                        qualification, reason = stable.control.current_qualification(connection_id=connection, model_id=model,
                                                                                     capability="chat_text", require_exact_model=True)
                meta = _authoring_observed_metadata(stable, policy, connection, row, meta)
                parameters, evidence = _authoring_policy(meta, model)
                context, output = meta.get("context_length"), meta.get("max_output_tokens")
                context = context if type(context) is int and 0 < context <= 100_000_000 else None
                output = output if type(output) is int and 0 < output <= 1_000_000 else None
                compatible = bool(parameters and context and output and parameters["fields"]["maxOutputTokens"]["maximum"] <= output
                                  and parameters["fields"]["maxOutputTokens"]["minimum"] < context)
                binding = {"tenant": tenant, "connection": connection, "model": model, "policy": policy.policy_fingerprint,
                           "admitted": bool(admitted and error is None), "qualification": qualification, "reason": reason,
                           "refresh": row.get("last_refresh_id"), "metadata": meta, "parameterPolicy": parameters}
                items.append({"selectionId": digest(["authoring", tenant, connection, model]), "selectionRevision": digest(binding),
                              "modelId": model, "name": name, "available": bool(admitted and not error and qualification and compatible),
                              "contextLength": context, "maxOutputTokens": output, "parameterPolicy": parameters,
                              "parameterEvidence": evidence})
            if len(rows) < 500:
                break
            offset += len(rows)
    result = {"format": AUTHORING_CATALOG_FORMAT, "source": AUTHORING_CATALOG_SOURCE,
              "catalogId": "rpg-" + digest([tenant, "authoring-models-v1"]), "models": items}
    return {**result, "revision": digest(result)}


@router.get("/authoring/models", dependencies=[Depends(require_service)])
def authoring_models(stable=Depends(get_authoring_stable)):
    return JSONResponse(authoring_catalog(stable), headers={"Cache-Control": "no-store"})


def _unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value or key in ("__proto__", "constructor", "prototype"):
            raise ValueError("duplicate_or_reserved_key")
        value[key] = item
    return value


def authoring_parameters(entry, parameters):
    if not entry["available"]:
        raise HTTPException(409, "rpg_authoring_selection_unavailable")
    fields = entry["parameterPolicy"]["fields"]
    if not isinstance(parameters, dict) or not set(parameters) <= set(fields):
        raise HTTPException(422, "rpg_authoring_parameter_unsupported")
    for key, value in parameters.items():
        field = fields[key]
        if (type(value) not in (int, float) or not field["minimum"] <= value <= field["maximum"]
                or not math.isfinite(value)
                or (key == "maxOutputTokens" and int(value) != value)):
            raise HTTPException(422, "rpg_authoring_parameter_range")
    if any(f["required"] and key not in parameters for key, f in fields.items()):
        raise HTTPException(422, "rpg_authoring_parameter_required")
    if any(sum(key in parameters for key in group) > 1 for group in entry["parameterPolicy"]["exclusive"]):
        raise HTTPException(422, "rpg_authoring_parameter_conflict")
    return {fields[key]["wireName"]: value for key, value in parameters.items()}


@router.post("/authoring/parameters/validate", dependencies=[Depends(require_service)])
async def validate_authoring_parameters(request: Request, stable=Depends(get_authoring_stable)):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 16 * 1024:
            raise HTTPException(413, "rpg_authoring_request_too_large")
    try:
        value = json.loads(data, object_pairs_hook=_unique_json_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non_finite")))
        if (not isinstance(value, dict) or set(value) != {"catalogRevision", "selectionId", "selectionRevision", "parameters"}
                or any(not isinstance(value[k], str) or not re.fullmatch(r"[a-f0-9]{64}", value[k])
                       for k in ("catalogRevision", "selectionId", "selectionRevision"))):
            raise ValueError("invalid")
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "rpg_authoring_request_invalid") from None
    current = authoring_catalog(stable)
    if value["catalogRevision"] != current["revision"]:
        raise HTTPException(409, "rpg_authoring_catalog_changed")
    entry = next((e for e in current["models"] if e["selectionId"] == value["selectionId"]), None)
    if not entry or entry["selectionRevision"] != value["selectionRevision"]:
        raise HTTPException(409, "rpg_authoring_selection_changed")
    wire = authoring_parameters(entry, value["parameters"])
    return JSONResponse({"format": "modelmirror.rpg.parameter-check/1", "catalogRevision": current["revision"],
                         "selectionId": entry["selectionId"], "selectionRevision": entry["selectionRevision"],
                         "wireParameters": wire, "dispatched": False}, headers={"Cache-Control": "no-store"})


# Separate durable authoring protocol; legacy card endpoints retain their contract.
from .rpg_authoring_execution import register_authoring_execution
register_authoring_execution(router, require_service, get_authoring_stable, authoring_catalog,
                             authoring_parameters, _unique_json_object, digest)

from .rpg_authoring_route import register_route_api
register_route_api(router, require_service, get_stable, _unique_json_object)
