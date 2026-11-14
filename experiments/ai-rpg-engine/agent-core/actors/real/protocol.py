"""Task-only R6 actor protocol, not registered on the production application."""
import json, os, re, subprocess
from pathlib import Path
from fastapi import HTTPException, Depends, Request
from server.model_router import rpg_bridge as b, rpg_local_state_tools as native

def enabled():
    if os.getenv("RPG_ACTORS_REAL_ENABLED") != "true":
        raise HTTPException(503, "rpg_actors_real_disabled")

def wire(data):
    result = subprocess.run(["node", str(Path(__file__).with_name("wire.mjs"))],
        input=json.dumps(data,ensure_ascii=False),capture_output=True,text=True,encoding="utf-8",timeout=30)
    if result.returncode:
        raise ValueError("rpg_actors_wire_invalid")
    value=json.loads(result.stdout)
    native.require(not value.get("error"))
    return value

class Protocol:
    supports=staticmethod(native.supports)
    async def body(self,request,profile):
        enabled()
        data=bytearray()
        async for part in request.stream():
            data.extend(part)
            if len(data)>min(b.MAX_REQUEST_BYTES,2*1024*1024):raise HTTPException(413,"rpg_request_too_large")
        try:
            value=json.loads(data)
            native.require(isinstance(value,dict) and set(value)=={"sessionId","requestId","selectionId","selectionRevision","messages","parameters","tools","tool_choice"})
            for k in ("sessionId","requestId","selectionId","selectionRevision"):
                native.require(isinstance(value[k],str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}",value[k]))
            native.require(profile in ("core-query-r3","god-long-v1"))
            native.require(value["parameters"]==b.parameters_for("google/gemini-3.8-flash",profile))
            native.require(value["tool_choice"] in ("none","auto"))
            native.require(profile!="god-long-v1" or value["tool_choice"]=="none")
            wire({"kind":"request","request":{k:value[k] for k in ("messages","tools","tool_choice")}})
            return value
        except (ValueError,TypeError,KeyError,subprocess.SubprocessError):
            raise HTTPException(422,"rpg_actors_protocol") from None
    def parse_sse(self,sse,model,evidence=None):
        result=wire({"kind":"sse","sse":sse,"model":model})
        if evidence is not None:evidence.update({k:result[k] for k in ("raw","actual","message","finishReason")})
        return result["raw"],result["actual"],result["usage"]

def register(app,stable):
    async def service(_=Depends(b.require_service)):
        enabled();return stable
    @app.post("/api/rpg/v1/actors/chat/completions")
    async def completion(request: Request,profile:str,svc=Depends(service)):
        response=await b._completion(request,svc,profile,protocol=Protocol())
        value=json.loads(response.body);receipt=value.get("receipt",{})
        if receipt.get("sessionId") and receipt.get("requestId"):
            path=native.result_path(svc,receipt["sessionId"],receipt["requestId"]);path.parent.mkdir(parents=True,exist_ok=True)
            try:
                with path.open("x",encoding="utf-8") as out:
                    json.dump({"httpStatus":response.status_code,"body":value},out,ensure_ascii=False);out.flush();os.fsync(out.fileno())
            except OSError:
                from fastapi.responses import JSONResponse
                return JSONResponse({"error":"rpg_tool_receipt_pending"},status_code=503)
        return response
    @app.get("/api/rpg/v1/actors/results/{session}/{request_id}")
    async def lookup(session:str,request_id:str,svc=Depends(service)):
        return native.lookup(svc,session,request_id)
