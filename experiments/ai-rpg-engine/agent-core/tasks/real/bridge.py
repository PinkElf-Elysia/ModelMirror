"""Isolated test bridge. Caller supplies a selected, encrypted snapshot in memory."""
import asyncio, json, os, secrets, sqlite3, sys, shutil
from pathlib import Path
import httpx
from fastapi import FastAPI, Depends
from server.model_router import rpg_bridge as b
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService
from server.model_router.chat_stable import ProviderChatStableService
from server.model_router.egress import ProviderEgressPolicy, ProviderEgressError
import importlib.util
def load_protocol(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

from credential import save_auth
ROOT=Path(__file__).resolve().parents[5]
sys.path.insert(0,str(ROOT/"experiments/ai-rpg-engine/local-state/tools"))
from isolated_dns import resolve
MODEL="google/gemini-3.8-flash"
PRICE="https://openrouter.ai/api/v1/models/google/gemini-3.8-flash/endpoints"

def save(run,name,value):
    (run/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
async def preflight(run):
    policy=ProviderEgressPolicy(resolver=resolve,internal_allowlist=[])
    target=await policy.authorize(PRICE)
    negative=[]
    for ip in ["198.18.0.168","127.0.0.1","169.254.169.254","10.0.0.1"]:
        try:await ProviderEgressPolicy(resolver=lambda h,p:[ip],internal_allowlist=[]).authorize(PRICE)
        except ProviderEgressError as e:negative.append(e.code=="provider_address_blocked")
        else:negative.append(False)
    assert all(negative)
    async with httpx.AsyncClient(trust_env=False,timeout=25,follow_redirects=False) as client:
        r=await policy.request(client,"GET",PRICE);r.raise_for_status();data=r.json()
    endpoint=next(e for e in data["data"]["endpoints"] if e["tag"]=="google-ai-studio" and e["service_tier"] is None and e["status"]==0)
    assert float(endpoint["pricing"]["prompt"])<=.00000075 and float(endpoint["pricing"]["completion"])<=.00000375 and float(endpoint["pricing"]["internal_reasoning"])<=.00000375
    assert all(k in endpoint["supported_parameters"] for k in ["tools","tool_choice","max_tokens","temperature","top_p"])
    save(run,"preflight.json",dict(endpoint=endpoint,negativeChecks=negative,hostHeader=target.host_header,sni=target.sni_hostname,providerCalls=0))
    return endpoint

def serve(run,mode,snapshot,port):
    run=Path(run).resolve();allowed=Path("C:/tmp/mm-r7-live").resolve()
    assert run.parent==allowed and run.name.startswith(mode+"-") and mode in ("mock","real")
    run.mkdir(parents=True,exist_ok=True);dest=run/"router"
    assert not dest.exists(),"Previous isolated runtime must not be overwritten"
    endpoint=asyncio.run(preflight(run));dest.mkdir()
    repo=None;initial_runs=[]
    try:
        assert snapshot["version"]==20
        for row in snapshot["rows"]["provider_catalog_models"]:
            meta=json.loads(row["metadata_json"])
            assert meta["rpg_provider_tag"]=="google-ai-studio"
            meta["supported_parameters"]=endpoint["supported_parameters"]
            row["metadata_json"]=json.dumps(meta,ensure_ascii=False,separators=(",",":"))
        db=sqlite3.connect(dest/"router.sqlite3")
        for item in sorted(snapshot["schemas"],key=lambda r:0 if r["type"]=="table" else 1):db.execute(item["sql"])
        for table,rows in snapshot["rows"].items():
            for row in rows:db.execute('insert into "'+table+'" ('+','.join('"'+c+'"' for c in row)+') values ('+','.join("?" for _ in row)+')',list(row.values()))
        initial_runs=[r[0] for r in db.execute("select id from provider_chat_runs")]
        db.execute("pragma user_version=20");db.commit();db.close()
        os.environ["MODEL_MIRROR_CREDENTIAL_MASTER_KEY"]=snapshot.pop("key");snapshot.clear()
        auth=secrets.token_urlsafe(48);save_auth(run,dict(token=auth))
        os.environ.update(RPG_S2S_TOKEN=auth,RPG_S2S_ENABLED="true",RPG_LOCAL_STATE_TOOLS_ENABLED="true",RPG_CORE_REAL_ENABLED="true",RPG_ACTORS_REAL_ENABLED="true",RPG_ACTIONS_REAL_ENABLED="true",MODEL_CONTROL_CHAT_ENABLED="true")
        repo=SQLiteRouterRepository.open(dest,recover_chat_control_on_startup=False)
        service=ModelRouterService(repository=repo,egress_policy=ProviderEgressPolicy(resolver=resolve,internal_allowlist=[]))
        stable=ProviderChatStableService(service)
        assert stable.readiness_scoped_certified(MODEL)==(True,None),"Selected qualification unavailable"
        mock=[];active=0;max_active=0
        async def upstream(request):
            nonlocal active,max_active
            payload=json.loads(request.content)
            assert len(mock)<3 and payload["model"]==MODEL and request.headers["host"]=="openrouter.ai"
            assert payload["provider"]["only"]==["google-ai-studio"] and payload["provider"]["allow_fallbacks"] is False
            assert payload["tool_choice"]=="none" and payload["tools"]==[]
            actor="【宿主角色任务】" in payload["messages"][0]["content"]
            witness=actor and "quantityUsed" in json.dumps(payload["messages"])
            slot=2 if witness else 3 if actor else 1
            assert not any(x["slot"]==slot for x in mock)
            assert payload["max_tokens"]==(32768 if slot==1 else 8192)
            mock.append(dict(slot=slot,request=payload));active+=1;max_active=max(max_active,active)
            save(run,"mock-outbound.json",dict(requests=mock,maxConcurrent=max_active))
            await asyncio.sleep(.8)
            body={1:"你服下刚才确认的两份养息丹，将余下的三份收好。廊下的风吹散了药香，你停了一会儿，准备再决定接下来的安排。",
                  2:"我刚才看见你服下两份养息丹。但我没查看过你的库存，不能凭这件事确认你还剩多少。你想先歇一会儿吗？",
                  3:"我刚才不在廊下，没有看见发生了什么，也不知道你的丹药余量。你愿意告诉我吗？"}[slot]
            frames=[{"model":MODEL,"choices":[{"delta":{"content":body},"finish_reason":"stop"}]},
                    {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":30,"total_tokens":130,"cost":.0001875}}]
            sse="".join("data: "+json.dumps(f,ensure_ascii=False)+"\n\n" for f in frames)+"data: [DONE]\n\n"
            active-=1
            return httpx.Response(200,content=sse.encode(),headers={"content-type":"text/event-stream"})
        if mode=="mock":stable.transport.client_kwargs=lambda:{"transport":httpx.MockTransport(upstream),"trust_env":False,"follow_redirects":False}
        app=FastAPI();app.include_router(b.router);app.dependency_overrides[b.get_stable]=lambda:stable;load_protocol("r7_world_protocol",ROOT/"experiments/ai-rpg-engine/agent-core/actions/real/protocol.py").register(app,stable);load_protocol("r7_actor_protocol",ROOT/"experiments/ai-rpg-engine/agent-core/actors/real/protocol.py").register(app,stable)
        import uvicorn
        server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,access_log=False))
        @app.post("/task/stop",dependencies=[Depends(b.require_service)])
        async def stop():server.should_exit=True;return {"stopping":True}
        save(run,"bridge-ready.json",dict(mode=mode,port=port,model=MODEL,endpoint="google-ai-studio",noAutomaticRetries=True))
        print("Isolated bridge ready: "+mode,flush=True);server.run()
    finally:
        if repo:repo.close()
        if (dest/"router.sqlite3").exists():
            db=sqlite3.connect(dest/"router.sqlite3");db.row_factory=sqlite3.Row
            rows=[dict(r) for r in db.execute("select id,status from provider_chat_runs") if r["id"] not in initial_runs]
            save(run,"dispatch-audit.json",rows);db.close()
        # Only the resolved, task-owned credential directory is removed.
        assert dest.resolve().parent==run and dest.name=="router" and run.is_relative_to(allowed)
        if dest.exists(): shutil.rmtree(dest)
        (run/"local-auth.bin").unlink(missing_ok=True)
        os.environ.pop("MODEL_MIRROR_CREDENTIAL_MASTER_KEY",None);os.environ.pop("RPG_S2S_TOKEN",None)
        save(run,"cleanup.json",dict(credentialDirectoryRemoved=not dest.exists(),localTokenRemoved=not (run/"local-auth.bin").exists(),masterKeyWrittenToDisk=False,sharedConfigurationChanged=False))
