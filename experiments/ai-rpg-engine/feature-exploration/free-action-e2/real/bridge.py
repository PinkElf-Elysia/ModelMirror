"""Isolated E2 real/mock bridge using the existing certified route and egress policy."""
import asyncio,json,os,secrets,sqlite3,sys,shutil,threading,subprocess,time
from pathlib import Path
import httpx
from fastapi import FastAPI,Depends
from server.model_router import rpg_bridge as b
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService
from server.model_router.chat_stable import ProviderChatStableService
from server.model_router.egress import ProviderEgressPolicy,ProviderEgressError
from protocol import register
ROOT=Path(__file__).resolve().parents[5]
sys.path.insert(0,str(ROOT/"experiments/ai-rpg-engine/local-state/tools"))
from isolated_dns import resolve
MODEL="google/gemini-3.8-flash"
PRICE="https://openrouter.ai/api/v1/models/google/gemini-3.8-flash/endpoints"
def save(run,name,value):
    (run/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf8")
async def preflight(run):
    policy=ProviderEgressPolicy(resolver=resolve,internal_allowlist=[])
    target=await policy.authorize(PRICE)
    checks=[]
    for ip in ("198.18.0.168","127.0.0.1","169.254.169.254","10.0.0.1"):
        try:await ProviderEgressPolicy(resolver=lambda h,p:[ip],internal_allowlist=[]).authorize(PRICE)
        except ProviderEgressError as e:checks.append(e.code=="provider_address_blocked")
        else:checks.append(False)
    assert all(checks)
    async with httpx.AsyncClient(trust_env=False,timeout=25,follow_redirects=False) as client:
        r=await policy.request(client,"GET",PRICE);r.raise_for_status();data=r.json()
    endpoint=next(e for e in data["data"]["endpoints"] if e["tag"]=="google-ai-studio" and e["service_tier"] is None and e["status"]==0)
    assert float(endpoint["pricing"]["prompt"])<=.00000075 and float(endpoint["pricing"]["completion"])<=.00000375 and float(endpoint["pricing"]["internal_reasoning"])<=.00000375
    assert all(k in endpoint["supported_parameters"] for k in ("tools","tool_choice","max_tokens","temperature","top_p"))
    save(run,"preflight.json",dict(endpoint=endpoint,negativeChecks=checks,hostHeader=target.host_header,sni=target.sni_hostname,providerCalls=0))
    return endpoint
def serve(run,mode,snapshot,port):
    allowed=ROOT/"experiments/ai-rpg-engine/.rpg03-work"
    assert run.parent==allowed and run.name=="e2-proposal-"+mode+"-v1" and port==({"mock":18863,"real":18864}[mode])
    assert not run.exists(),"Never overwrite a previous experiment"
    run.mkdir(parents=True);dest=run/"router";repo=None;initial=[];worker=None;return_code=[]
    try:
        endpoint=asyncio.run(preflight(run));dest.mkdir()
        assert snapshot["version"]==20
        for row in snapshot["rows"]["provider_catalog_models"]:
            meta=json.loads(row["metadata_json"]);assert meta["rpg_provider_tag"]=="google-ai-studio"
            meta["supported_parameters"]=endpoint["supported_parameters"];row["metadata_json"]=json.dumps(meta,ensure_ascii=False,separators=(",",":"))
        db=sqlite3.connect(dest/"router.sqlite3")
        for item in sorted(snapshot["schemas"],key=lambda r:0 if r["type"]=="table" else 1):db.execute(item["sql"])
        for table,rows in snapshot["rows"].items():
            for row in rows:db.execute('insert into "'+table+'" ('+','.join('"'+c+'"' for c in row)+') values ('+','.join("?" for _ in row)+')',list(row.values()))
        initial=[r[0] for r in db.execute("select id from provider_chat_runs")]
        db.execute("pragma user_version=20");db.commit();db.close()
        os.environ["MODEL_MIRROR_CREDENTIAL_MASTER_KEY"]=snapshot.pop("key");snapshot.clear()
        auth=secrets.token_urlsafe(48)
        os.environ.update(RPG_S2S_TOKEN=auth,RPG_S2S_ENABLED="true",RPG_CORE_REAL_ENABLED="true",RPG_E2_REAL_ENABLED="true",MODEL_CONTROL_CHAT_ENABLED="true")
        repo=SQLiteRouterRepository.open(dest,recover_chat_control_on_startup=False)
        service=ModelRouterService(repository=repo,egress_policy=ProviderEgressPolicy(resolver=resolve,internal_allowlist=[]));stable=ProviderChatStableService(service)
        assert stable.readiness_scoped_certified(MODEL)==(True,None),"Selected qualification unavailable"
        mock=[]
        def upstream(request):
            payload=json.loads(request.content);index=len(mock)+1
            assert index<=2 and payload["model"]==MODEL and request.headers["host"]=="openrouter.ai"
            assert payload["provider"]["only"]==["google-ai-studio"] and payload["provider"]["allow_fallbacks"] is False
            assert payload["tool_choice"]=="none" and payload["tools"]==[] and payload["max_tokens"]==8192
            v=dict(goal="拟赠丹药后交谈",steps=["拟先询问意愿，若同意再安排交付，之后商谈杂务。"],refs=[dict(domain="items",id="medicine"),dict(domain="relations",id="zhou")],requirements=[dict(itemId="medicine",quantity=2)],questions=[],limitations=["未交付，人物是否接受仍未知。"]) if index==1 else dict(goal="澄清赠送安排",steps=[],refs=[],requirements=[],questions=["准备给谁？给哪种丹药、多少份？"],limitations=["未发起行动。"])
            body=json.dumps(v,ensure_ascii=False)
            frames=[dict(model=MODEL,choices=[dict(delta=dict(content=body),finish_reason="stop")]),dict(choices=[],usage=dict(prompt_tokens=100,completion_tokens=20,total_tokens=120))]
            sse="".join("data: "+json.dumps(f,ensure_ascii=False)+"\n\n" for f in frames)+"data: [DONE]\n\n"
            mock.append(dict(index=index,request=payload));save(run,"mock-outbound.json",mock)
            return httpx.Response(200,content=sse.encode(),headers={"content-type":"text/event-stream"})
        if mode=="mock":stable.transport.client_kwargs=lambda:dict(transport=httpx.MockTransport(upstream),trust_env=False,follow_redirects=False)
        app=FastAPI();app.include_router(b.router);app.dependency_overrides[b.get_stable]=lambda:stable;register(app,stable)
        import uvicorn
        server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,access_log=False))
        @app.post("/task/stop",dependencies=[Depends(b.require_service)])
        async def stop():server.should_exit=True;return {"stopping":True}
        save(run,"bridge-ready.json",dict(mode=mode,port=port,model=MODEL,endpoint="google-ai-studio",noAutomaticRetries=True))
        def runner():
            while not server.started and not server.should_exit:time.sleep(.1)
            if not server.started:return
            env=dict(os.environ,E2_SERVICE_TOKEN=auth);env.pop("MODEL_MIRROR_CREDENTIAL_MASTER_KEY",None);env.pop("MODEL_ROUTER_CREDENTIAL_MASTER_KEY",None)
            try:return_code.append(subprocess.run(["node",str(Path(__file__).with_name("run.mjs")),mode,str(run),str(port)],cwd=ROOT,env=env).returncode)
            finally:server.should_exit=True
        worker=threading.Thread(target=runner,daemon=True);worker.start()
        print("E2 bridge ready: "+mode,flush=True);server.run();worker.join(timeout=10)
        if not return_code or return_code[0]!=0:raise RuntimeError("E2 runner did not complete; inspect preserved results, do not retry")
    finally:
        snapshot.clear()
        if repo:repo.close()
        if (dest/"router.sqlite3").exists():
            db=sqlite3.connect(dest/"router.sqlite3");db.row_factory=sqlite3.Row
            save(run,"dispatch-audit.json",[dict(r) for r in db.execute("select id,status from provider_chat_runs") if r["id"] not in initial]);db.close()
        assert dest.resolve().parent==run and run.parent==allowed and dest.name=="router"
        if dest.exists():shutil.rmtree(dest)
        for name in ("MODEL_MIRROR_CREDENTIAL_MASTER_KEY","RPG_S2S_TOKEN","RPG_E2_REAL_ENABLED"):os.environ.pop(name,None)
        save(run,"cleanup.json",dict(credentialDirectoryRemoved=not dest.exists(),localTokenWrittenToDisk=False,masterKeyWrittenToDisk=False,sharedConfigurationChanged=False))
