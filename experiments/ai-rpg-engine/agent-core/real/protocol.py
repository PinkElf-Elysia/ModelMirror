"""Task-only exact adapter protocols; not registered in the production app."""
import json, os
from pathlib import Path
from fastapi import HTTPException, Request, Depends
from server.model_router import rpg_bridge as b, rpg_local_state_tools as native
TOOLS = json.loads(Path(__file__).with_name("tools.json").read_text(encoding="utf-8"))

def enabled():
    if os.getenv("RPG_CORE_REAL_ENABLED") != "true":
        raise HTTPException(503, "rpg_core_real_disabled")

def lighthouse_args(args):
    native.require(isinstance(args, dict) and set(args) == {"queries"})
    qs = args["queries"]
    native.require(isinstance(qs, list) and 1 <= len(qs) <= 3)
    for q in qs:
        native.require(isinstance(q, dict) and set(q) <= {"domain", "id", "name", "limit"})
        native.require(q.get("domain") in ("supplies", "duty"))
        for k, n in (("id", 80), ("name", 100)):
            if k in q:
                native.require(isinstance(q[k], str) and q[k].strip() and len(q[k]) <= n)
        if "limit" in q:
            native.require(type(q["limit"]) is int and 1 <= q["limit"] <= 50)
        if q["domain"] == "duty":
            native.require("id" not in q and "name" not in q)

class Protocol:
    def __init__(self, adapter):
        if adapter not in TOOLS:
            raise HTTPException(422, "rpg_core_adapter")
        self.adapter = adapter
        self.validator = native.validate_args if adapter == "cultivation" else lighthouse_args
    async def body(self, request, profile):
        enabled()
        if profile not in (("god-long-v1",) if self.adapter == "cultivation" else ("core-query-r3", "god-long-v1")):
            raise HTTPException(422, "rpg_core_profile")
        value = await native.body(request, profile, TOOLS[self.adapter], self.validator)
        if self.adapter == "lighthouse" and (value["tool_choice"] == "auto") != (profile == "core-query-r3"):
            raise HTTPException(422, "rpg_core_stage_profile")
        return value
    supports = staticmethod(native.supports)
    def parse_sse(self, sse, model, evidence=None):
        return native.parse_sse(sse, model, evidence, self.validator)

def register(app, stable):
    async def service(_=Depends(b.require_service)):
        enabled()
        return stable
    @app.post("/api/rpg/v1/core/{adapter}/chat/completions")
    async def completion(adapter: str, request: Request, profile: str, svc=Depends(service)):
        response = await b._completion(request, svc, profile, protocol=Protocol(adapter))
        value = json.loads(response.body)
        receipt = value.get("receipt", {})
        if receipt.get("sessionId") and receipt.get("requestId"):
            path = native.result_path(svc, receipt["sessionId"], receipt["requestId"])
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("x", encoding="utf-8") as out:
                    json.dump({"httpStatus": response.status_code, "body": value}, out, ensure_ascii=False)
                    out.flush(); os.fsync(out.fileno())
            except OSError:
                from fastapi.responses import JSONResponse
                return JSONResponse({"error":"rpg_tool_receipt_pending"}, status_code=503)
        return response
    @app.get("/api/rpg/v1/core/{adapter}/results/{session}/{request_id}")
    async def lookup(adapter: str, session: str, request_id: str, svc=Depends(service)):
        Protocol(adapter)
        return native.lookup(svc, session, request_id)
