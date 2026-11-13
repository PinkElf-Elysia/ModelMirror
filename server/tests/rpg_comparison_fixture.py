"""Loopback-only real RPG APIs; all egress uses synthetic MockTransport."""
import asyncio
import json
import socket
import sys
import threading
from pathlib import Path
import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from server.model_router import rpg_bridge as bridge
from fastapi.responses import JSONResponse
from server.tests.test_rpg_authoring_route import route, configure
from server.tests.test_rpg_bridge import TOKEN
from server.model_router.provider_catalog import ProviderCatalogService
from server.tests.test_provider_chat_control_service import MODEL_ID

if __name__ == '__main__':
    directory=Path(sys.argv[1]);directory.mkdir(parents=True,exist_ok=True)
    patch=pytest.MonkeyPatch();fixture=route.__wrapped__(directory,patch);state=next(fixture)
    client,stable,repo,config,captured=state
    # Synthetic declared capacity accommodates every frozen card without slicing.
    catalog={'id':MODEL_ID,'name':'Synthetic long-context','context_length':2000000,'top_provider':{'max_completion_tokens':65536},'supported_parameters':['temperature','top_p','max_tokens']}
    catalogs=[catalog]
    if '--advanced' in sys.argv:
        from copy import deepcopy
        from server.tests.test_rpg_parameters_v2 import policy, declare
        full=policy()
        catalog['name']='离线模型 A · 完整参数'
        catalog['max_output_tokens']=65536
        catalogs=[declare(catalog,full)]
        limited=deepcopy(full)
        del limited['fields']['topK']
        limited['fields']['thinking.effort']['options']=['low','medium']
        second={**catalog,'id':'synthetic/limited','name':'离线模型 B · 精简参数'}
        catalogs.append(declare(second,limited))
        config['modelIds'].append(second['id'])
        row,created=repo.claim_chat_certification('local',certification_id='synthetic-limited-cert',
            connection_id=config['connectionId'],connection_fingerprint=repo.connection_config_fingerprint('local',config['connectionId']),
            contract_version='modelmirror-provider-chat-v1',capability='chat_text',requested_model=second['id'],idempotency_key_hash='synthetic-limited-cert')
        assert created
        repo.complete_chat_certification('local',str(row['id']),status='passed',checks={'capability_verified':True},warning_codes=[],actual_model=second['id'])
    stable.router_service._client_factory=lambda:httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'data':catalogs})),follow_redirects=False,trust_env=False)
    asyncio.run(ProviderCatalogService(stable.router_service).refresh_connection(config['connectionId']))
    config['limit']=200;configure(state)
    calls=[];counts={'active':0,'maximum':0};lock=threading.Lock()
    async def respond(request):
        payload=json.loads(request.content)
        with lock:
            calls.append(payload);counts['active']+=1;counts['maximum']=max(counts['maximum'],counts['active'])
        try:
            await asyncio.sleep(faults.get('delay', .08))
            system=payload['messages'][0]['content'];user=payload['messages'][-1]['content']
            text='{"operations":[]}' if '"entries"' in user else '已发生的港口会面。' if '你负责' in system and '摘要' in system else '候选原文 '+str(payload.get('temperature','default'))+'。'
            if '<variable_panel>' in user and '候选原文' in text:
                text+=' <variable_panel>'+json.dumps({'energy':round(payload.get('temperature',0)*10)})+'</variable_panel>'
            model=payload['model'];chunks=[{'model':model,'choices':[{'delta':{'content':text},'finish_reason':None}]},{'model':model,'choices':[{'delta':{},'finish_reason':'stop'}]}]
            raw=''.join('data: '+json.dumps(v,ensure_ascii=False)+'\n\n' for v in chunks)+'data: [DONE]\n\n'
            return httpx.Response(200,content=raw.encode(),headers={'content-type':'text/event-stream'})
        finally:
            with lock:counts['active']-=1
    patch.setattr(stable.transport,'client_kwargs',lambda:{'transport':httpx.MockTransport(respond),'trust_env':False})
    app=FastAPI();app.include_router(bridge.router);app.dependency_overrides[bridge.get_stable]=lambda:stable
    faults={'dropTasks':0,'blockLookup':False}
    @app.post('/__fault')
    async def fault(request: Request):
        value=await request.json();faults.update(value);return {'ok':True}
    @app.middleware('http')
    async def lose_response(request,call_next):
        is_task=request.url.path.startswith('/api/rpg/v1/authoring/tasks')
        if is_task and request.method=='GET' and faults['blockLookup']:
            return JSONResponse({'detail':'synthetic_lookup_unavailable'},status_code=503)
        response=await call_next(request)
        if is_task and request.method=='POST' and faults['dropTasks']>0:
            faults['dropTasks']-=1
            return JSONResponse({'detail':'synthetic_ack_lost'},status_code=503)
        return response
    @app.get('/__evidence')
    def evidence():return {'calls':calls,**counts}
    sock=socket.socket();sock.bind(('127.0.0.1',0));sock.listen(64)
    server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
    @app.on_event('startup')
    async def ready():print(json.dumps({'origin':'http://127.0.0.1:'+str(sock.getsockname()[1]),'token':TOKEN}),flush=True)
    try:server.run(sockets=[sock])
    finally:
        try:next(fixture)
        except StopIteration:pass
        patch.undo()
