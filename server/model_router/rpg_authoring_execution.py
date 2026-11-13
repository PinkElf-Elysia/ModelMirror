"""Default-closed, exact-route RPG authoring tasks with durable no-replay lookup.

Only the trusted local host can call this service. A disconnected client does
not cancel a dispatched task. An incomplete claim after restart stays unknown.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .egress import ProviderEgressError
from .openrouter_errors import read_openrouter_error_code

FORMAT = "modelmirror.rpg.authoring-task/1"
FORMAT_V2 = "modelmirror.rpg.authoring-task/2"
FORMAT_COMPARISON = "modelmirror.rpg.authoring-task/3"
from . import rpg_comparison as comparison
RECEIPT_FORMAT = "modelmirror.rpg.authoring-task-receipt/1"
MAX_BODY = 8 * 1024 * 1024
MAX_RESPONSE = 16 * 1024 * 1024
PREFLIGHT_TIMEOUT_SECONDS = 15
OPENROUTER_FORBIDDEN_CODES = {
    "permission_denied": "rpg_authoring_upstream_permission_denied",
    "content_policy_violation": "rpg_authoring_upstream_content_policy",
    "refusal": "rpg_authoring_upstream_refusal",
    "guardrail": "rpg_authoring_upstream_guardrail",
}
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_tasks: set[asyncio.Task] = set()


def _execution_enabled():
    return os.getenv("RPG_AUTHORING_EXECUTION_ENABLED", "").lower() == "true"


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _paths(stable, session_id, request_id):
    identity = _hash(json.dumps([stable.router_service.tenant_id, session_id, request_id], separators=(",", ":")))
    folder = stable.repository.storage_dir / "rpg-authoring-tasks"
    return folder / (identity + ".claim.json"), folder / (identity + ".result.json")


def _write_new(path, value):
    with path.open("x", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        output.flush()
        os.fsync(output.fileno())


def _persist(path, value):
    # Only the exclusive claim owner may write this result. A failed write is
    # never grounds to replay the Provider call.
    temporary = path.with_suffix(".pending")
    _write_new(temporary, value)
    os.replace(temporary, path)


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(503, "rpg_authoring_record_unreadable") from None


def _lookup(stable, session, request, request_hash):
    claim, result = _paths(stable, session, request)
    if not claim.exists():
        return {"status": "absent", "requestHash": request_hash}
    saved = _read(claim)
    if saved.get("requestHash") != request_hash:
        raise HTTPException(409, "rpg_authoring_request_conflict")
    return _read(result) if result.exists() else {"status": "unknown", "requestHash": request_hash}


async def _body(request, unique):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BODY:
            raise HTTPException(413, "rpg_authoring_request_too_large")
    try:
        value = json.loads(data, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        fields = {"format", "sessionId", "requestId", "operationId", "purpose", "sourceRequestHash",
                  "catalogRevision", "selectionId", "selectionRevision", "parameters", "messages"}
        version_two = isinstance(value, dict) and value.get("format") in (FORMAT_V2, FORMAT_COMPARISON)
        if version_two:
            fields.add("capabilityRevision")
        if isinstance(value, dict) and value.get("format") == FORMAT_COMPARISON:
            fields.add("comparison")
            comparison.task_binding(value)
        if not isinstance(value, dict) or set(value) != fields or value["format"] not in (FORMAT, FORMAT_V2, FORMAT_COMPARISON):
            raise ValueError()
        if version_two and (not isinstance(value["capabilityRevision"], str) or not _HASH.fullmatch(value["capabilityRevision"])):
            raise ValueError()
        if any(not isinstance(value[k], str) or not _ID.fullmatch(value[k]) for k in ("sessionId", "requestId", "operationId")):
            raise ValueError()
        if any(not isinstance(value[k], str) or not _HASH.fullmatch(value[k]) for k in ("sourceRequestHash", "catalogRevision", "selectionId", "selectionRevision")):
            raise ValueError()
        if value["purpose"] not in ("story", "summary", "compression", "memory") or not isinstance(value["parameters"], dict):
            raise ValueError()
        messages = value["messages"]
        if not isinstance(messages, list) or not 2 <= len(messages) <= 2202 or len(messages) % 2:
            raise ValueError()
        for i, m in enumerate(messages):
            role = "system" if i == 0 else "user" if i % 2 else "assistant"
            if not isinstance(m, dict) or set(m) != {"role", "content"} or m["role"] != role or not isinstance(m["content"], str):
                raise ValueError()
            m["content"].encode("utf-8", errors="strict")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HTTPException(422, "rpg_authoring_request_invalid") from None
    return value


def _parse(sse, model, evidence):
    """Preserve partial text while distinguishing terminal truncation from unknown."""
    blocks = sse.replace("\r\n", "\n").split("\n\n")
    tail = blocks.pop()
    done = False
    for block in blocks:
        data = "\n".join(line[5:].lstrip() for line in block.split("\n") if line.startswith("data:"))
        if not data:
            continue
        if done:
            raise ValueError("rpg_authoring_invalid_output")
        if data == "[DONE]":
            done = True
            continue
        e = json.loads(data)
        if not isinstance(e, dict) or e.get("error"):
            raise ValueError("rpg_authoring_provider_error")
        if e.get("model"):
            evidence["actualModel"] = e["model"]
            if e["model"] != model:
                raise ValueError("rpg_authoring_model_mismatch")
        if isinstance(e.get("usage"), dict):
            evidence["usage"] = {k: v for k, v in e["usage"].items()
                                 if k in ("prompt_tokens", "completion_tokens", "total_tokens") and type(v) is int and v >= 0}
        choices = e.get("choices") or []
        if not isinstance(choices, list) or len(choices) > 1:
            raise ValueError("rpg_authoring_invalid_output")
        if choices:
            c = choices[0]
            delta = c.get("delta") or {}
            if delta.get("tool_calls") or delta.get("function_call"):
                raise ValueError("rpg_authoring_non_text_output")
            text = delta.get("content")
            if text is not None and not isinstance(text, str):
                raise ValueError("rpg_authoring_non_text_output")
            evidence["raw"] += text or ""
            evidence["finishReason"] = c.get("finish_reason") or evidence["finishReason"]
    if tail.strip() or not done or evidence["finishReason"] is None:
        raise ValueError("rpg_authoring_result_unknown")
    if evidence["finishReason"] not in ("stop", "length") or not evidence["raw"].strip():
        raise ValueError("rpg_authoring_invalid_output")


def register_authoring_execution(router, auth, get_stable, catalog, parameters, unique, digest, parameters_v2):
    def selection(stable, value):
        # Other catalog rows may refresh; this exact row revision must not change.
        current = catalog(stable, 2, include_private=True) if value["format"] in (FORMAT_V2, FORMAT_COMPARISON) else catalog(stable)
        entry = next((e for e in current["models"] if e["selectionId"] == value["selectionId"]), None)
        if not entry or not entry["available"] or entry["selectionRevision"] != value["selectionRevision"]:
            raise ValueError("rpg_authoring_selection_changed")
        policy = stable.control.peek_policy()
        connection = next((c for c in stable.control.route_candidates(policy, "chat_text")
                           if digest(["authoring", stable.router_service.tenant_id, c, entry["modelId"]]) == entry["selectionId"]), None)
        if connection is None:
            raise ValueError("rpg_authoring_selection_changed")
        return entry, connection

    async def execute(stable, value, request_hash, result_path):
        dispatch = None
        sent = False
        preflighting = False
        wire = None
        resolution = None
        provider_request_hash = None
        selected = None
        evidence = {"raw": "", "actualModel": None, "usage": None, "finishReason": None}
        sse, error, http_status = "", None, None
        upstream_http_error = None
        status = "failed"
        try:
            if not _execution_enabled():
                raise ValueError("rpg_authoring_execution_disabled")
            selected, connection = selection(stable, value)
            if value["format"] in (FORMAT_V2, FORMAT_COMPARISON):
                resolution = parameters_v2(selected, value["parameters"], value["purpose"], value["capabilityRevision"])
                wire = resolution["wireParameters"]
                output_limit = resolution["effectiveParameters"]["maxOutputTokens"]
            else:
                wire = parameters(selected, value["parameters"])
                output_limit = value["parameters"]["maxOutputTokens"]
            estimate = sum(len(m["content"].encode()) + 32 for m in value["messages"]) + 256
            if estimate + output_limit > selected["contextLength"]:
                raise ValueError("rpg_authoring_context_capacity")
            preflighting = True
            async with asyncio.timeout(PREFLIGHT_TIMEOUT_SECONDS):
                if value["format"] == FORMAT_COMPARISON:
                    preflight = await stable.begin_rpg_authoring_certified(selected["modelId"], connection, comparison_request=value)
                else:
                    preflight = await stable.begin_rpg_authoring_certified(selected["modelId"], connection)
            preflighting = False
            dispatch = preflight.dispatch
            if dispatch is None:
                raise ValueError(preflight.error_code or "rpg_authoring_route_unavailable")
            if dispatch.target.connection_id != connection:
                raise ValueError("rpg_authoring_selection_changed")
            payload = {"model": selected["modelId"], "messages": value["messages"], **wire,
                       "stream": True, "stream_options": {"include_usage": True}}
            if dispatch.target.provider_kind == "openrouter":
                # Endpoint metadata is selected from the exact current route only.
                row, offset = None, 0
                while row is None:
                    rows = stable.repository.list_catalog_models(stable.router_service.tenant_id, connection_id=connection,
                                                                  status="active", limit=500, offset=offset)
                    row = next((r for r in rows if r["model_id"] == selected["modelId"]), None)
                    if row is not None or len(rows) < 500:
                        break
                    offset += len(rows)
                if row is None:
                    raise ValueError("rpg_authoring_selection_changed")
                meta = json.loads(row.get("metadata_json") or "{}")
                tag = meta.get("rpg_provider_tag")
                provider = {"allow_fallbacks": False, "require_parameters": True}
                if tag is not None:
                    if not isinstance(tag, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_/-]{0,100}", tag):
                        raise ValueError("rpg_authoring_endpoint_invalid")
                    provider.update(only=[tag], order=[tag])
                payload.update(provider=provider, transforms=[])
            provider_request_hash = digest(payload)
            async with asyncio.timeout(240), httpx.AsyncClient(**stable.transport.client_kwargs()) as client:
                outbound = stable.transport.build_authorized_stream_request(client, dispatch.target, dispatch.authorized, payload)
                selection(stable, value)
                if not _execution_enabled():
                    raise ValueError("rpg_authoring_execution_disabled")
                stable.mark_dispatched(dispatch)
                sent = True
                response = await stable.transport.send_authorized_stream(client, outbound)
                try:
                    http_status = response.status_code
                    if not response.is_success or "text/event-stream" not in response.headers.get("content-type", ""):
                        # The response status is conclusive even if the optional
                        # diagnostic body cannot be read or the stream fails to close.
                        upstream_http_error = "rpg_authoring_upstream_http"
                        if http_status == 403 and dispatch.target.provider_kind == "openrouter":
                            detail = await read_openrouter_error_code(response, OPENROUTER_FORBIDDEN_CODES)
                            if detail is not None:
                                upstream_http_error = detail
                        raise ValueError("rpg_authoring_upstream_http")
                    chunks, size, stream_error = [], 0, None
                    try:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_RESPONSE:
                                raise ValueError("rpg_authoring_response_capacity")
                            chunks.append(chunk)
                    except (Exception, asyncio.CancelledError) as exc:
                        stream_error = exc
                    # Keep every received, bounded complete SSE fragment even if
                    # the transport fails before DONE. It is evidence, not a turn.
                    sse = b"".join(chunks).decode("utf-8", errors="strict")
                    if stream_error is not None:
                        try:
                            _parse(sse, selected["modelId"], evidence)
                        except Exception:
                            pass
                        raise stream_error
                    _parse(sse, selected["modelId"], evidence)
                    if hasattr(stable.control.peek_policy(), "authoring_model_ids"):
                        selection(stable, value)  # Revoked late output remains evidence only.
                    status = "completed"
                finally:
                    await response.aclose()
        except (Exception, asyncio.CancelledError) as exc:
            code = exc.detail if isinstance(exc, HTTPException) else str(exc)
            # Only classify transport errors while preflighting, before any
            # provider dispatch. Never expose exception messages, URLs or keys,
            # and never turn a dispatched timeout into a confirmed failure.
            if preflighting and not sent:
                if isinstance(exc, ProviderEgressError):
                    code = {"provider_dns_failed": "rpg_authoring_dns_failed",
                            "provider_address_blocked": "rpg_authoring_address_blocked",
                            "invalid_address": "rpg_authoring_endpoint_invalid"}.get(exc.code)
                elif isinstance(exc, (httpx.TimeoutException, TimeoutError)):
                    code = "rpg_authoring_preflight_timeout"
                elif isinstance(exc, httpx.HTTPError):
                    code = "rpg_authoring_preflight_network"
            allowed = {"rpg_authoring_execution_disabled", "rpg_authoring_selection_changed", "rpg_authoring_context_capacity", "rpg_authoring_route_unavailable",
                       "rpg_authoring_endpoint_invalid", "rpg_authoring_upstream_http", "rpg_authoring_response_capacity",
                       "rpg_authoring_invalid_output", "rpg_authoring_non_text_output", "rpg_authoring_model_mismatch",
                       "rpg_authoring_provider_error", "rpg_authoring_result_unknown", "rpg_authoring_parameter_unsupported",
                       "rpg_authoring_parameter_range", "rpg_authoring_parameter_required", "rpg_authoring_parameter_conflict",
                       "rpg_authoring_capability_changed", "rpg_authoring_parameter_invalid",
                       "rpg_authoring_parameter_dependency", "rpg_authoring_parameter_purpose", "rpg_authoring_selection_unavailable",
                       "rpg_authoring_budget_exhausted", "rpg_authoring_routing_disabled", "rpg_authoring_model_not_allowed",
                       "rpg_authoring_dns_failed", "rpg_authoring_address_blocked",
                       "rpg_authoring_preflight_timeout", "rpg_authoring_preflight_network"}
            if upstream_http_error is not None:
                error = upstream_http_error
            else:
                error = code if isinstance(code, str) and code in allowed else "rpg_authoring_result_unknown" if sent else "rpg_authoring_preflight_failed"
            status = "unknown" if sent and error in {"rpg_authoring_result_unknown", "rpg_authoring_response_capacity"} else "failed"
        if dispatch:
            secret = dispatch.target.api_key
            if secret and (secret in evidence["raw"] or secret in sse or secret in str(evidence["actualModel"])):
                evidence.update(raw="", actualModel=None, finishReason=None)
                sse, error, status = "", "rpg_authoring_credential_echo_blocked", "failed"
            try:
                if sent:
                    classification, hard = "success" if status == "completed" else "request_failure", False
                    if upstream_http_error is not None and error == upstream_http_error and http_status is not None:
                        classification, _, hard = stable.classify_http_failure(http_status)
                    persisted = stable.complete(dispatch, status="succeeded" if status == "completed" else "failed",
                                                result_class=classification, error_code=error, hard_failure=hard,
                                                actual_model=evidence["actualModel"], **(evidence["usage"] or {}))
                    if not persisted:
                        status, error = "unknown", "rpg_authoring_receipt_pending"
                else:
                    stable.fail_undispatched(dispatch, error_code=error or "rpg_authoring_not_dispatched")
            except Exception:
                status, error = "unknown", "rpg_authoring_receipt_pending"
        receipt = {"format": RECEIPT_FORMAT, "gateway": "rpg_authoring", "requestHash": request_hash,
                   "sourceRequestHash": value["sourceRequestHash"], "sessionId": value["sessionId"], "requestId": value["requestId"],
                   "operationId": value["operationId"], "purpose": value["purpose"], "selectionId": value["selectionId"],
                   "selectionRevision": value["selectionRevision"], "catalogRevision": value["catalogRevision"],
                   "requestedModel": selected["modelId"] if selected else None, "actualModel": evidence["actualModel"],
                   "parameters": wire, "runId": dispatch.run_id if dispatch else None, "attemptId": dispatch.attempt_id if dispatch else None,
                   "dispatched": sent, "status": status, "finishReason": evidence["finishReason"], "error": error,
                   "httpStatus": http_status, "usage": evidence["usage"], "retries": 0,
                   "rawHash": _hash(evidence["raw"]), "sseHash": _hash(sse)}
        if value["format"] in (FORMAT_V2, FORMAT_COMPARISON):
            receipt.update(format="modelmirror.rpg.authoring-task-receipt/2",
                           capabilityRevision=value["capabilityRevision"], parameterResolution=resolution,
                           providerRequestHash=provider_request_hash)
        result = {"status": status, "requestHash": request_hash, "raw": evidence["raw"], "sse": sse,
                  "receipt": {**receipt, "sha256": digest(receipt)}}
        _persist(result_path, result)
        comparison.settle_result(stable, value, result)
        return result

    @router.get("/authoring/readiness", dependencies=[Depends(auth)])
    def readiness():
        # Read-only gate projection; this does not qualify a model or reserve credit.
        return JSONResponse({"format": "modelmirror.rpg.authoring-readiness/1",
                             "executionEnabled": _execution_enabled(), "dispatched": False},
                            headers={"Cache-Control": "no-store"})

    @router.post("/authoring/tasks", dependencies=[Depends(auth)])
    async def start(request: Request, stable=Depends(get_stable)):
        value = await _body(request, unique)
        request_hash = digest(value)
        prior = _lookup(stable, value["sessionId"], value["requestId"], request_hash)
        if prior["status"] != "absent":
            if "receipt" in prior:
                comparison.settle_result(stable, value, prior)
            return JSONResponse(prior, headers={"Cache-Control": "no-store"})
        if value["format"] == FORMAT_COMPARISON:
            with stable.repository._lock, stable.repository._connect() as db:
                comparison.validate_task(db, stable.router_service.tenant_id, value)
        claim, result = _paths(stable, value["sessionId"], value["requestId"])
        claim.parent.mkdir(parents=True, exist_ok=True)
        try:
            _write_new(claim, {"format": value["format"], "requestHash": request_hash, "request": value})
        except FileExistsError:
            return JSONResponse(_lookup(stable, value["sessionId"], value["requestId"], request_hash), headers={"Cache-Control": "no-store"})
        task = asyncio.create_task(execute(stable, value, request_hash, result))
        _tasks.add(task)
        def settled(t):
            _tasks.discard(t)
            if not t.cancelled():
                t.exception()  # Observe errors even when the original client left.
        task.add_done_callback(settled)
        try:
            value = await asyncio.shield(task)
        except Exception:
            value = {"status": "unknown", "requestHash": request_hash}
        return JSONResponse(value, headers={"Cache-Control": "no-store"})

    @router.get("/authoring/tasks/{session_id}/{request_id}", dependencies=[Depends(auth)])
    def lookup(session_id: str, request_id: str, sourceRequestHash: str, stable=Depends(get_stable)):
        if not _ID.fullmatch(session_id) or not _ID.fullmatch(request_id) or not _HASH.fullmatch(sourceRequestHash):
            raise HTTPException(422, "rpg_authoring_identity_invalid")
        claim, _ = _paths(stable, session_id, request_id)
        if not claim.exists():
            return JSONResponse({"status": "absent", "sourceRequestHash": sourceRequestHash}, headers={"Cache-Control": "no-store"})
        saved = _read(claim)
        if saved.get("request", {}).get("sourceRequestHash") != sourceRequestHash:
            raise HTTPException(409, "rpg_authoring_request_conflict")
        # Server hashes are opaque across Python/JS number serialization.
        result = _lookup(stable, session_id, request_id, saved["requestHash"])
        if "receipt" in result:
            comparison.settle_result(stable, saved["request"], result)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
