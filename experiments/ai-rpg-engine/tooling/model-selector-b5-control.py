"""Isolated real B5 control plane. Existing configuration stays in memory.
Source router volume is mounted read-only; no credential DB/key copy is written.
Only scoped certification and RPG bridge code from the candidate is exercised.
"""
import asyncio, hashlib, json, os, sqlite3, threading, sys
from pathlib import Path
import httpx, uvicorn
from fastapi import FastAPI, Depends, HTTPException
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.service import ModelRouterService
from server.model_router.chat_stable import ProviderChatStableService
from server.model_router.chat_certification import ProviderChatCertificationService
from server.model_router.provider_catalog import ProviderCatalogService
from server.model_router import rpg_bridge as bridge
WORK=Path('/work'); WORK.mkdir(exist_ok=True)
TARGETS={'luna':('openai/gpt-5.6-luna','openai'),'gemini':('google/gemini-3.8-flash','google-ai-studio'),'deepseek':('deepseek/deepseek-v4-flash-0731','fireworks')}
class MemoryRepository(SQLiteRouterRepository):
    def __init__(self):
        self.uri='file:b5-control?mode=memory&cache=shared'
        self.keeper=sqlite3.connect(self.uri,uri=True,check_same_thread=False)
        snapshot=sys.stdin.buffer.read()
        if not snapshot.startswith(b'-- B5 memory snapshot'): raise RuntimeError('INVALID_MEMORY_SNAPSHOT')
        self.keeper.executescript(snapshot.decode('utf-8'))
        del snapshot
        master=os.environ.get('MODEL_MIRROR_CREDENTIAL_MASTER_KEY') or os.environ.get('MODEL_ROUTER_CREDENTIAL_MASTER_KEY')
        if not master: master=Path('/source-router/credential-master.key').read_bytes().strip()
        super().__init__(storage_dir=WORK/'control-evidence',master_key=master,recover_chat_control_on_startup=False)
    def _connect(self):
        db=sqlite3.connect(self.uri,uri=True,timeout=15,check_same_thread=False)
        db.row_factory=sqlite3.Row; db.execute('PRAGMA foreign_keys=ON'); return db
repo=MemoryRepository(); service=ModelRouterService(repo); stable=ProviderChatStableService(service)
routes=next(r.connection_ids for r in stable.control.get_policy().routes if r.capability=='chat_text')
connections=[c for c in service.list_connections() if c.id in routes and c.kind=='openrouter' and c.base_url.rstrip('/')=='https://openrouter.ai/api/v1' and c.enabled]
if len(connections)!=1: raise RuntimeError('EXACT_OPENROUTER_ROUTE_REQUIRED')
connection=connections[0]
app=FastAPI();app.include_router(bridge.router);app.dependency_overrides[bridge.get_stable]=lambda:stable
metadata={};lock=asyncio.Lock()
def save(name,obj):
    path=WORK/name; tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,path)
def overlay():
    with repo._lock,repo._connect() as db:
        for model,item in metadata.items():
            row=db.execute('SELECT metadata_json FROM provider_catalog_models WHERE tenant_id=? AND connection_id=? AND model_id=?',('local',connection.id,model)).fetchone()
            if not row: continue
            current=json.loads(row['metadata_json']);current.update(item)
            db.execute('UPDATE provider_catalog_models SET metadata_json=? WHERE tenant_id=? AND connection_id=? AND model_id=?',(json.dumps(current),'local',connection.id,model))
async def prepare():
    refreshed=await ProviderCatalogService(service).refresh_connection(connection.id)
    async with httpx.AsyncClient(timeout=25,trust_env=False,follow_redirects=False) as client:
        for label,(model,tag) in TARGETS.items():
            url='https://openrouter.ai/api/v1/models/'+model+'/endpoints';response=await client.get(url);response.raise_for_status();data=response.json()
            e=next(e for e in data['data']['endpoints'] if e.get('tag')==tag and e.get('status')==0)
            effective=bridge.parameters_for(model)
            if not all(k in e.get('supported_parameters',[]) for k in effective): raise RuntimeError('PARAMETER_INCOMPATIBLE')
            if e['max_completion_tokens']<16384: raise RuntimeError('OUTPUT_LIMIT_INCOMPATIBLE')
            evidence={'url':url,'sha256':hashlib.sha256(response.content).hexdigest(),'endpoint':e}
            save('metadata-'+label+'.json',evidence)
            metadata[model]={'context_length':e['context_length'],'max_output_tokens':e['max_completion_tokens'],'supported_parameters':e['supported_parameters'],'rpg_provider_tag':tag,'rpg_endpoint_evidence':{'url':url,'sha256':evidence['sha256']}}
    overlay();return {'prepared':True,'connectionId':connection.id,'models':[{k:v for k,v in m.items() if not k.startswith('_')} for m in bridge.catalog(stable) if m['model'] in metadata]}
class Certification(ProviderChatCertificationService):
    @staticmethod
    def _certification_payload(model_id,capability):
        if capability!='chat_text': raise RuntimeError('TEXT_ONLY')
        tag=next(tag for model,tag in TARGETS.values() if model==model_id)
        # More than the generic 64-token probe leaves room for reasoning, still bounded.
        payload={'model':model_id,'stream':True,'max_tokens':2048,'messages':[{'role':'user','content':'Reply with OK.'}],'provider':{'only':[tag],'order':[tag],'allow_fallbacks':False,'require_parameters':True},'transforms':[]}
        if model_id!='openai/gpt-5.6-luna': payload['temperature']=0
        return payload
@app.get('/b5/status',dependencies=[Depends(bridge.require_service)])
def status():
    return {'models':[{k:v for k,v in m.items() if not k.startswith('_')} for m in bridge.catalog(stable) if m['model'] in metadata],'authenticationSlots':len(list(WORK.glob('authentication-slot-*.json'))),'realGenerationOwnership':'Node independent ledger','sourceStore':'read-only; SQLite live snapshot only in memory'}
@app.post('/b5/prepare',dependencies=[Depends(bridge.require_service)])
async def preparation():
    async with lock: return await prepare()
@app.post('/b5/certify/{label}',dependencies=[Depends(bridge.require_service)])
async def certify(label:str):
    if label not in TARGETS or not metadata:raise HTTPException(409,'PREPARE_REQUIRED')
    async with lock:
        model,_=TARGETS[label]
        existing,reason=stable.control.current_qualification(connection_id=connection.id,model_id=model,capability='chat_text',require_exact_model=True)
        if existing:return {'reused':True,'qualification':existing}
        slots=list(WORK.glob('authentication-slot-*.json'))
        if any(json.loads(p.read_text()).get('label')==label for p in slots):raise HTTPException(409,'AUTH_ATTEMPT_ALREADY_RESERVED_NO_RETRY')
        if len(slots)>=3:raise HTTPException(409,'AUTH_BUDGET_EXHAUSTED')
        slot=len(slots)+1;identity='model-selector-b5-'+label
        reservation={'slot':slot,'label':label,'model':model,'status':'reserved-before-service-call','automaticRetry':False}
        with (WORK/f'authentication-slot-{slot}.json').open('x',encoding='utf-8') as f:json.dump(reservation,f);f.flush();os.fsync(f.fileno())
        try:
            result=await Certification(service).run(connection.id,model_id=model,capability='chat_text',acknowledge_billed_call=True,idempotency_key=identity)
            safe=result.model_dump(mode='json');save(f'authentication-result-{slot}.json',safe)
            overlay();return {'slot':slot,'result':safe}
        except Exception as e:
            save(f'authentication-result-{slot}.json',{'status':'failed-or-unknown','code':getattr(e,'code',type(e).__name__)})
            raise HTTPException(502,'AUTH_FAILED_OR_UNKNOWN_NO_RETRY') from None
if __name__=='__main__':
    uvicorn.run(app,host='0.0.0.0',port=18446,access_log=False,log_level='error')
