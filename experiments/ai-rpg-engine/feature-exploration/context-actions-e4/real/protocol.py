"""Task-local proposal-only protocol. No registration in the production application."""
import json, os, re
from fastapi import HTTPException, Request, Depends
from server.model_router import rpg_bridge as b, rpg_local_state_tools as native
PARAMETERS=dict(temperature=.7,top_p=.8,max_tokens=8192)
class Protocol:
    async def body(self,request,profile):
        if os.getenv("RPG_E4_REAL_ENABLED")!="true":raise HTTPException(503,"e4_disabled")
        native.require(profile=="core-query-r3")
        raw=bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>b.MAX_REQUEST_BYTES:raise HTTPException(413,"e4_capacity")
        try:
            v=json.loads(raw)
            native.require(isinstance(v,dict) and set(v)=={"sessionId","requestId","selectionId","selectionRevision","messages","parameters","tools","tool_choice"})
            for key in ("sessionId","requestId","selectionId","selectionRevision"):
                native.require(isinstance(v[key],str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}",v[key]))
            native.require(v["sessionId"] in ("e4-context-real-v1","e4-context-mock-v1") and v["requestId"] in ("proposal-1","proposal-2"))
            native.require(v["parameters"]==PARAMETERS and v["tools"]==[] and v["tool_choice"]=="none")
            native.require(isinstance(v["messages"],list) and len(v["messages"])==2)
            for message,role in zip(v["messages"],("system","user")):
                native.require(isinstance(message,dict) and set(message)=={"role","content"} and message["role"]==role and isinstance(message["content"],str) and message["content"].strip())
            return v
        except (ValueError,TypeError,KeyError):raise HTTPException(422,"e4_proposal_protocol") from None
    @staticmethod
    def supports(selected):
        return all(k in selected["_binding"]["metadata"].get("supported_parameters",[]) for k in ("max_tokens","temperature","top_p","tool_choice"))
    @staticmethod
    def parse_sse(sse,model,evidence=None):
        out={} if evidence is None else evidence
        value=native.parse_sse(sse,model,out)
        native.require(not out["message"].get("tool_calls") and out["finishReason"]=="stop","rpg_non_text_output")
        return value
def register(app,stable):
    async def service(_=Depends(b.require_service)):
        if os.getenv("RPG_E4_REAL_ENABLED")!="true":raise HTTPException(503,"e4_disabled")
        return stable
    @app.post("/api/rpg/v1/e4-context/chat/completions")
    async def completion(request:Request,profile:str,svc=Depends(service)):
        response=await b._completion(request,svc,profile,protocol=Protocol())
        value=json.loads(response.body);r=value.get("receipt",{})
        if r.get("sessionId") and r.get("requestId"):
            # Provider evidence is outside the credential directory and survives cleanup.
            path=svc.repository.storage_dir.parent/"receipts"/(b.digest([r["sessionId"],r["requestId"]])+".json")
            path.parent.mkdir(exist_ok=True)
            with path.open("x",encoding="utf8") as out:
                json.dump({"httpStatus":response.status_code,"body":value},out,ensure_ascii=False);out.flush();os.fsync(out.fileno())
        return response
    @app.get("/api/rpg/v1/e4-context/results/{session}/{request_id}")
    async def lookup(session:str,request_id:str,svc=Depends(service)):
        from fastapi.responses import JSONResponse
        path=svc.repository.storage_dir.parent/"receipts"/(b.digest([session,request_id])+".json")
        if not path.exists():raise HTTPException(404,"e4_result_missing")
        return JSONResponse(json.loads(path.read_text(encoding="utf8")))
