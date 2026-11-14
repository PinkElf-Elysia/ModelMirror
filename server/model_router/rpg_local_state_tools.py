"""Opt-in native read-only tools for the isolated RPG local-state prototype."""
from __future__ import annotations
import json
import os
import re
from fastapi import HTTPException
from fastapi.responses import JSONResponse

# Kept in sync by test; the server package does not read experiment files at runtime.
TOOL = json.loads('{"type":"function","function":{"name":"query_local_state","description":"\\u53ea\\u8bfb\\u67e5\\u8be2\\u5f53\\u524d\\u4f1a\\u8bdd\\u5df2\\u767b\\u8bb0\\u72b6\\u6001\\uff1b\\u4e0d\\u63a8\\u65ad\\u672a\\u767b\\u8bb0\\u4e8b\\u5b9e\\u6216\\u5bb9\\u5668\\u5f52\\u5c5e\\uff0c\\u4e0d\\u4fee\\u6539\\u72b6\\u6001\\u3002\\u53ef\\u4e00\\u6b21\\u67e5\\u8be2\\u591a\\u4e2a\\u8303\\u56f4\\u3002","parameters":{"type":"object","additionalProperties":false,"required":["queries"],"properties":{"queries":{"type":"array","minItems":1,"maxItems":3,"items":{"type":"object","additionalProperties":false,"required":["domain"],"properties":{"domain":{"type":"string","enum":["items","abilities","relations","balances","equipment","traits"]},"id":{"type":"string","minLength":1,"maxLength":80},"name":{"type":"string","minLength":1,"maxLength":100},"kind":{"type":"string","minLength":1,"maxLength":30},"limit":{"type":"integer","minimum":1,"maximum":50}}}}}}}}')
MAX_META = 65536

def enabled():
    if os.getenv("RPG_LOCAL_STATE_TOOLS_ENABLED", "").lower() != "true":
        raise HTTPException(503, "rpg_local_state_tools_disabled")

def require(ok, code="rpg_tool_protocol"):
    if not ok:
        raise ValueError(code)

def call_valid(call):
    require(isinstance(call, dict) and set(call) <= {"id", "type", "function", "extra_content"})
    require(isinstance(call.get("id"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call["id"]))
    require(call.get("type") == "function")
    fn = call.get("function")
    require(isinstance(fn, dict) and set(fn) == {"name", "arguments"})
    require(fn["name"] == "query_local_state" and isinstance(fn["arguments"], str) and len(fn["arguments"]) <= 8192)
    args = json.loads(fn["arguments"])
    validate_args(args)
    metadata(call.get("extra_content"))

def metadata(value):
    require(value is None or isinstance(value, dict) and len(json.dumps(value)) <= MAX_META)

def validate_args(args):
    require(isinstance(args, dict) and set(args) == {"queries"})
    queries = args["queries"]
    require(isinstance(queries, list) and 1 <= len(queries) <= 3)
    for q in queries:
        require(isinstance(q, dict) and set(q) <= {"domain", "id", "name", "kind", "limit"})
        require(q.get("domain") in ("items", "abilities", "relations", "balances", "equipment", "traits"))
        for key, maximum in (("id", 80), ("name", 100), ("kind", 30)):
            if key in q:
                require(isinstance(q[key], str) and q[key].strip() and len(q[key]) <= maximum)
        if "limit" in q:
            require(type(q["limit"]) is int and 1 <= q["limit"] <= 50)
        if q["domain"] in ("balances", "equipment", "traits"):
            require("kind" not in q and "name" not in q)
        if "kind" in q:
            kinds = {"items": ["法宝", "装备", "丹药", "材料", "物资"], "abilities": ["功法", "术法", "神通", "体质能力", "丹方", "器谱", "炼丹炼器术", "天赋", "弱点"]}
            require(q["kind"] in kinds.get(q["domain"], []))

async def body(request, profile):
    from . import rpg_bridge as b
    enabled()
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > b.MAX_REQUEST_BYTES:
            raise HTTPException(413, "rpg_request_too_large")
    try:
        value = json.loads(data)
        require(isinstance(value, dict) and set(value) == {"sessionId","requestId","selectionId","selectionRevision","messages","parameters","tools","tool_choice"})
        for key in ("sessionId","requestId","selectionId","selectionRevision"):
            require(isinstance(value[key], str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value[key]))
        require(value["tools"] == [TOOL] and value["tool_choice"] in ("auto","none"))
        allowed = [b.parameters_for("google/gemini-3.8-flash",profile)]
        if profile == "default":
            allowed.append({"max_tokens":16384})
        require(value["parameters"] in allowed)
        messages = value["messages"]
        require(isinstance(messages,list) and 2 <= len(messages) <= 1004)
        prefix = messages if value["tool_choice"] == "auto" else messages[:-2]
        require(len(prefix) >= 2 and len(prefix) % 2 == 0)
        for index, message in enumerate(prefix):
            role = "system" if index == 0 else "user" if index % 2 else "assistant"
            require(isinstance(message,dict) and set(message) == {"role","content"} and message["role"] == role and isinstance(message["content"],str))
        if value["tool_choice"] == "none":
            assistant, result = messages[-2:]
            require(isinstance(assistant,dict) and set(assistant) <= {"role","content","tool_calls","extra_content"} and assistant.get("role") == "assistant")
            require(assistant.get("content") is None or isinstance(assistant["content"],str))
            require(isinstance(assistant.get("tool_calls"),list) and len(assistant["tool_calls"]) == 1)
            call_valid(assistant["tool_calls"][0]); metadata(assistant.get("extra_content"))
            require(isinstance(result,dict) and set(result) == {"role","tool_call_id","content"} and result["role"] == "tool" and result["tool_call_id"] == assistant["tool_calls"][0]["id"] and isinstance(result["content"],str))
        return value
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, "rpg_tool_protocol") from None

def supports(selected):
    supported = selected["_binding"]["metadata"].get("supported_parameters", [])
    return all(name in supported for name in ("tools","tool_choice"))

def parse_sse(sse, model, evidence=None):
    evidence = evidence if evidence is not None else {}
    parts = sse.replace("\r\n","\n").split("\n\n")
    require(not parts.pop().strip())
    content, calls, finish, done, usage, actual, extra = "", {}, None, False, None, None, None
    for block in parts:
        data = "\n".join(line[5:].lstrip() for line in block.split("\n") if line.startswith("data:"))
        if not data:
            continue
        require(not done)
        if data == "[DONE]":
            done = True
            continue
        event = json.loads(data)
        require(isinstance(event,dict) and not event.get("error"))
        if event.get("model"):
            actual = event["model"]; require(actual == model, "rpg_model_mismatch")
        if isinstance(event.get("usage"),dict):
            usage = {k:v for k,v in event["usage"].items() if k in ("prompt_tokens","completion_tokens","total_tokens") and type(v) is int and v >= 0}
        choices = event.get("choices") or []
        require(len(choices) <= 1)
        if not choices:
            continue
        choice = choices[0]; delta = choice.get("delta") or {}
        require(not delta.get("function_call"))
        if delta.get("content") is not None:
            require(isinstance(delta["content"],str)); content += delta["content"]
        if "extra_content" in delta:
            metadata(delta["extra_content"]); require(extra is None or extra == delta["extra_content"]); extra = delta["extra_content"]
        for fragment in delta.get("tool_calls") or []:
            require(isinstance(fragment,dict) and fragment.get("index") == 0 and set(fragment) <= {"index","id","type","function","extra_content"})
            call = calls.setdefault(0, {"id":"","type":"function","function":{"name":"","arguments":""}})
            for key in ("id","type"):
                if key in fragment:
                    require(isinstance(fragment[key],str) and (not call[key] or call[key] == fragment[key]))
                    call[key] = fragment[key]
            fn = fragment.get("function") or {}; require(set(fn) <= {"name","arguments"})
            for key in ("name","arguments"):
                if key in fn:
                    require(isinstance(fn[key],str)); call["function"][key] += fn[key]
            if "extra_content" in fragment:
                metadata(fragment["extra_content"])
                require("extra_content" not in call or call["extra_content"] == fragment["extra_content"])
                call["extra_content"] = fragment["extra_content"]
        finish = choice.get("finish_reason") or finish
    require(done)
    message = {"role":"assistant","content":content or None}
    if extra is not None:
        message["extra_content"] = extra
    if calls:
        require(finish == "tool_calls")
        call_valid(calls[0]); message["tool_calls"] = [calls[0]]
    else:
        require(finish == "stop" and content.strip(), "rpg_incomplete_output")
    evidence.update(raw=content,actual=actual,message=message,finishReason=finish)
    return content, actual, usage

def result_path(stable, session, request_id):
    from .rpg_bridge import digest
    identity = digest([stable.router_service.tenant_id,session,request_id])
    return stable.repository.storage_dir / "rpg-tools-results" / (identity + ".json")

async def completion(request, stable, profile):
    from . import rpg_bridge as b
    enabled()
    response = await b._completion(request, stable, profile, protocol=__import__(__name__,fromlist=[""]))
    value = json.loads(response.body)
    receipt = value.get("receipt",{})
    if receipt.get("sessionId") and receipt.get("requestId"):
        path = result_path(stable,receipt["sessionId"],receipt["requestId"])
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # The upstream claim prevents replay. Persist terminal evidence for explicit lookup.
            with path.open("x",encoding="utf-8") as out:
                json.dump({"httpStatus":response.status_code,"body":value},out,ensure_ascii=False)
                out.flush(); os.fsync(out.fileno())
        except OSError:
            return JSONResponse({"error":"rpg_tool_receipt_pending"},status_code=503)
    return response

def lookup(stable, session, request_id):
    enabled()
    if not all(re.fullmatch(r"[A-Za-z0-9_-]{1,128}",v) for v in (session,request_id)):
        raise HTTPException(422,"rpg_identity_invalid")
    try:
        return json.loads(result_path(stable,session,request_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HTTPException(404,"rpg_tool_result_unknown") from None
    except (ValueError,OSError):
        raise HTTPException(409,"rpg_tool_result_unknown") from None
