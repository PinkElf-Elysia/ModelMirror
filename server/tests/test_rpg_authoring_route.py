"""Explicit RPG route tests use only synthetic credentials and MockTransport."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from server.model_router import rpg_bridge as bridge
from server.model_router.chat_control import ProviderChatControlService
from server.model_router.chat_stable import ProviderChatStableService
from server.model_router.egress import ProviderEgressPolicy
from server.model_router.provider_catalog import ProviderCatalogService
from server.model_router.repository import SQLiteRouterRepository
from server.model_router.rpg_authoring_route import AuthoringStableService, AuthoringRouteControl
from server.model_router.schemas import RouterConnectionCreate, RouterConnectionUpdate
from server.model_router.service import ModelRouterService, RouterServiceError
from server.tests.test_provider_chat_control_service import _certify, MODEL_ID
from server.tests.test_rpg_bridge import HEADERS, TOKEN, stream

PATH='/api/rpg/v1/authoring/route';TASK='/api/rpg/v1/authoring/tasks'

@pytest.fixture
def route(tmp_path,monkeypatch):
    for k,v in {'RPG_S2S_ENABLED':'true','RPG_S2S_TOKEN':TOKEN,'RPG_AUTHORING_ROUTING_ENABLED':'true',
                'RPG_AUTHORING_EXECUTION_ENABLED':'true','MODEL_CONTROL_CHAT_ENABLED':'false'}.items():monkeypatch.setenv(k,v)
    repo=SQLiteRouterRepository.open(tmp_path)
    connection=repo.create_connection('local',RouterConnectionCreate(name='Synthetic OpenRouter',kind='openrouter',base_url='https://openrouter.ai/api/v1',api_key='synthetic-secret',scopes=['chat']))
    raw={'id':MODEL_ID,'name':'Synthetic','context_length':128000,'top_provider':{'max_completion_tokens':65536},'supported_parameters':['temperature','top_p','max_tokens']}
    router=ModelRouterService(repo,egress_policy=ProviderEgressPolicy(resolver=lambda *_:['8.8.8.8']),
        client_factory=lambda:httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={'data':[raw]})),follow_redirects=False,trust_env=False))
    asyncio.run(ProviderCatalogService(router).refresh_connection(connection.id));_certify(repo,connection.id,'chat_text')
    stable=ProviderChatStableService(router);captured=[]
    def respond(request):
        captured.append(json.loads(request.content));return httpx.Response(200,content=stream(model=MODEL_ID),headers={'content-type':'text/event-stream'})
    monkeypatch.setattr(stable.transport,'client_kwargs',lambda:{'transport':httpx.MockTransport(respond),'trust_env':False})
    app=FastAPI();app.include_router(bridge.router);app.dependency_overrides[bridge.get_stable]=lambda:stable
    client=TestClient(app)
    config={'operationId':'route-1','expectedRevision':0,'enabled':True,'connectionId':connection.id,'modelIds':[MODEL_ID],'limit':2}
    yield client,stable,repo,config,captured
    repo.close()

def configure(route):
    client,_,_,config,_=route
    response=client.put(PATH,headers=HEADERS,json=config)
    assert response.status_code==200,response.text
    return response.json()

def task(route,number=1):
    client,*_=route;c=client.get('/api/rpg/v1/authoring/models',headers=HEADERS).json();e=next(x for x in c['models'] if x['available'])
    return {'format':'modelmirror.rpg.authoring-task/1','catalogRevision':c['revision'],'selectionId':e['selectionId'],
            'selectionRevision':e['selectionRevision'],'parameters':{'temperature':.7,'topP':.8,'maxOutputTokens':16384},
            'sessionId':'sample-session','requestId':'sample-request-'+str(number),'operationId':'sample-operation-'+str(number),
            'purpose':'story','sourceRequestHash':str(number)*64,'messages':[{'role':'system','content':'system原文'},{'role':'user','content':'player原文'}]}

def test_default_off_auth_cas_and_global_policy_preserved(route,monkeypatch):
    client,stable,repo,config,captured=route
    before=ProviderChatControlService(stable.router_service).peek_policy().model_dump()
    assert client.get('/api/rpg/v1/authoring/models',headers=HEADERS).json()['models']==[]
    assert client.put(PATH,json=config).status_code==401
    assert client.put(PATH,headers={**HEADERS,'Origin':'http://example'},json=config).status_code==403
    saved=configure(route)
    assert saved['revision']==1 and saved['spent']==0
    assert client.put(PATH,headers=HEADERS,json=config).json()==saved
    assert client.put(PATH,headers=HEADERS,json={**config,'limit':3}).status_code==409
    assert client.put(PATH,headers=HEADERS,json={**config,'operationId':'stale'}).status_code==409
    assert client.put(PATH,headers=HEADERS,json={**config,'operationId':'bad','expectedRevision':1,'apiKey':'forbidden'}).status_code==422
    assert ProviderChatControlService(stable.router_service).peek_policy().model_dump()==before
    assert before['configured_mode']=='legacy' and not before['feature_enabled'] and not captured
    monkeypatch.setenv('RPG_AUTHORING_ROUTING_ENABLED','false')
    assert client.put(PATH,headers=HEADERS,json=config).status_code==503

def test_real_task_protocol_exact_route_budget_and_restart_lookup(route):
    client,stable,repo,config,captured=route;configure(route)
    first=task(route);selection=first['selectionRevision']
    result=client.post(TASK,headers=HEADERS,json=first).json()
    assert result['status']=='completed',result
    assert result['receipt']['dispatched'] and len(captured)==1
    assert captured[0]['provider']=={'allow_fallbacks':False,'require_parameters':True}
    assert captured[0]['model']==MODEL_ID and captured[0]['messages']==first['messages']
    from server.model_router.call_context_storage import ProviderContextStore
    receipt = result["receipt"]
    origin = ProviderContextStore(repo).read("local", receipt["attemptId"])
    assert (origin.entry_id, origin.actor_kind, origin.actor_reference, origin.purpose) == ("rpg_bridge", "service", "rpg-bridge", "user_run")
    assert origin.parent_run_id == receipt["runId"] and origin.policy_revision == 1
    assert task(route,2)['selectionRevision']==selection
    second=client.post(TASK,headers=HEADERS,json=task(route,2)).json()
    assert second['status']=='completed' and len(captured)==2
    third=client.post(TASK,headers=HEADERS,json=task(route,3)).json()
    assert third['status']=='failed' and not third['receipt']['dispatched'] and len(captured)==2
    assert third['receipt']['error']=='rpg_authoring_budget_exhausted'
    app=FastAPI();app.include_router(bridge.router);app.dependency_overrides[bridge.get_stable]=lambda:stable
    recovered=TestClient(app).post(TASK,headers=HEADERS,json=first).json()
    assert recovered==result and len(captured)==2
    state=client.get(PATH,headers=HEADERS).json();assert state['spent']==2
    assert AuthoringRouteControl(stable.router_service).state()==state
    changed=client.put(PATH,headers=HEADERS,json={**config,'operationId':'disable','expectedRevision':1,'enabled':False}).json()
    assert changed['spent']==2 and changed['revision']==2
    assert client.put(PATH,headers=HEADERS,json={**config,'operationId':'reset','expectedRevision':2,'limit':1}).status_code==409
    runs=repo.list_chat_control_receipts('local')['runs']
    assert all(r['strategy']=='rpg_authoring_explicit' and not r['primary_newapi'] for r in runs)

@pytest.mark.asyncio
async def test_atomic_last_quota_and_stale_revision(route):
    client,old,repo,config,captured=route;config['limit']=1;configure(route)
    stable=AuthoringStableService(old.router_service,transport=old.transport)
    a=(await stable.begin_rpg_authoring_certified(MODEL_ID,config['connectionId'])).dispatch
    b=(await stable.begin_rpg_authoring_certified(MODEL_ID,config['connectionId'])).dispatch
    assert a and b
    def mark(d):
        try:stable.mark_dispatched(d);return 'sent'
        except RouterServiceError as exc:return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(mark,[a,b]))
    assert sorted(results)==['rpg_authoring_budget_exhausted','sent']
    assert stable.control.state()['spent']==1 and not captured
    with pytest.raises(RouterServiceError):stable.mark_dispatched(a)
    assert stable.control.state()['spent']==1

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['disabled','credential','flag','model','zero'])
async def test_change_before_dispatch_never_sends(route,monkeypatch,change):
    client,old,repo,config,captured=route;configure(route)
    stable=AuthoringStableService(old.router_service,transport=old.transport)
    result=await stable.begin_rpg_authoring_certified(MODEL_ID,config['connectionId']);assert result.dispatch
    if change=='credential':repo.update_connection('local',config['connectionId'],RouterConnectionUpdate(api_key='changed-synthetic'))
    elif change=='flag':monkeypatch.setenv('RPG_AUTHORING_ROUTING_ENABLED','false')
    else:
        values={**config,'operationId':'change','expectedRevision':1}
        if change=='disabled':values['enabled']=False
        if change=='model':values['modelIds']=['another-model']
        if change=='zero':values['limit']=0
        stable.control.configure(values)
    with pytest.raises(RouterServiceError):stable.mark_dispatched(result.dispatch)
    assert stable.control.state()['spent']==0 and not captured

@pytest.mark.asyncio
async def test_unknown_claim_remains_unknown_after_application_restart(route):
    client,stable,repo,config,captured=route;configure(route);value=task(route)
    from server.model_router.rpg_authoring_execution import _paths,_write_new
    claim,_=_paths(stable,value['sessionId'],value['requestId']);claim.parent.mkdir()
    _write_new(claim,{'requestHash':bridge.digest(value),'request':value})
    assert client.post(TASK,headers=HEADERS,json=value).json()['status']=='unknown'
    assert not captured and client.get(PATH,headers=HEADERS).json()['spent']==0


def test_late_result_after_route_revocation_is_preserved_but_not_applied(route,monkeypatch):
    client,old,repo,config,captured=route;configure(route);value=task(route)
    def revoke(request):
        captured.append(json.loads(request.content))
        AuthoringRouteControl(old.router_service).configure({**config,'operationId':'late-revoke','expectedRevision':1,'enabled':False})
        return httpx.Response(200,content=stream(model=MODEL_ID),headers={'content-type':'text/event-stream'})
    monkeypatch.setattr(old.transport,'client_kwargs',lambda:{'transport':httpx.MockTransport(revoke),'trust_env':False})
    result=client.post(TASK,headers=HEADERS,json=value).json()
    assert result['status']=='failed' and result['receipt']['error']=='rpg_authoring_selection_changed'
    assert result['receipt']['dispatched'] and result['raw'].strip() and len(captured)==1
    assert client.get(PATH,headers=HEADERS).json()['spent']==1
    assert client.post(TASK,headers=HEADERS,json=value).json()==result and len(captured)==1


def test_route_state_survives_repository_restart_without_quota_reset(route,tmp_path):
    client,old,repo,config,captured=route;configure(route)
    assert client.post(TASK,headers=HEADERS,json=task(route)).json()['status']=='completed'
    state=AuthoringRouteControl(old.router_service).state();repo.close()
    reopened=SQLiteRouterRepository.open(tmp_path)
    try:
        control=AuthoringRouteControl(ModelRouterService(reopened))
        assert control.state()==state and state['spent']==1
        assert control.configure({**config,'operationId':'restart-disable','expectedRevision':1,'enabled':False})['spent']==1
    finally:reopened.close()


def test_local_observation_for_legacy_catalog(route, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from server.model_router.rpg_authoring_route import AuthoringStableService

    client, stable, repo, config, captured = route
    configure(route)
    target = config["connectionId"]
    scoped = AuthoringStableService(stable.router_service)
    scope = scoped.control.peek_policy()
    listing = repo.list_catalog_models
    row = listing("local", connection_id=target, status="active", limit=1, offset=0)[0]
    original = json.loads(row["metadata_json"])
    legacy = {k: original[k] for k in ("context_length", "max_output_tokens", "supported_parameters")}
    raw = {"id": row["model_id"], "context_length": legacy["context_length"],
           "top_provider": {"max_completion_tokens": legacy["max_output_tokens"]},
           "supported_parameters": legacy["supported_parameters"]}
    now = datetime.now(UTC)
    proof = {"connectionId": target, "connectionFingerprint": scope.state["connectionFingerprint"],
             "refreshId": row["last_refresh_id"], "url": "https://openrouter.ai/api/v1/models",
             "readAt": now.isoformat(), "model": raw}
    def legacy_listing(*args, **kwargs):
        return [{**r, "metadata_json": json.dumps(legacy)} for r in listing(*args, **kwargs)]
    monkeypatch.setattr(repo, "list_catalog_models", legacy_listing)
    def catalog():
        response = client.get("/api/rpg/v1/authoring/models", headers=HEADERS)
        assert response.status_code == 200
        return response.json()["models"][0]

    monkeypatch.delenv("RPG_AUTHORING_OPENROUTER_OBSERVATION", raising=False)
    prior = catalog()
    assert prior["parameterPolicy"]["kind"] == "fixed"
    monkeypatch.setenv("RPG_AUTHORING_OPENROUTER_OBSERVATION", json.dumps(proof))
    observed = catalog()
    assert observed["selectionId"] == prior["selectionId"]
    assert observed["selectionRevision"] != prior["selectionRevision"]
    assert observed["parameterPolicy"]["kind"] == "editable"
    assert observed["parameterPolicy"]["fields"]["maxOutputTokens"]["maximum"] == legacy["max_output_tokens"]
    for key, wrong in (("connectionId", "other"), ("connectionFingerprint", "changed"), ("refreshId", "changed"),
                       ("url", "https://other.invalid/models"), ("readAt", (now - timedelta(days=8)).isoformat()),
                       ("readAt", (now + timedelta(days=1)).isoformat()), ("model", {**raw, "id": "other"}),
                       ("model", {**raw, "supported_parameters": ["max_tokens"]}),
                       ("model", {**raw, "context_length": 128}), ("model", {**raw, "top_provider": {"max_completion_tokens": 128}})):
        monkeypatch.setenv("RPG_AUTHORING_OPENROUTER_OBSERVATION", json.dumps({**proof, key: wrong}))
        assert bridge._authoring_observed_metadata(scoped, scope, target, row, legacy) is legacy
    assert catalog() == prior
    monkeypatch.setenv("RPG_AUTHORING_OPENROUTER_OBSERVATION", json.dumps(proof))
    invalid = {**legacy, "parameter_capabilities": {"status": "invalid"}}
    assert bridge._authoring_observed_metadata(scoped, scope, target, row, invalid) is invalid
    assert bridge._authoring_observed_metadata(scoped, object(), target, row, legacy) is legacy
    assert json.loads(listing("local", connection_id=target, status="active", limit=1, offset=0)[0]["metadata_json"]) == original
    assert not captured

@pytest.mark.asyncio
async def test_authoring_checks_persisted_origin_inside_quota_transaction(route):
    from server.model_router.rpg_authoring_route import AuthoringStableService
    _, primary, repo, config, captured = route
    configure(route)
    stable = AuthoringStableService(primary.router_service, transport=primary.transport)
    dispatch = (await stable.begin_rpg_authoring_certified(MODEL_ID, config["connectionId"])).dispatch
    with repo._lock, repo._connect() as db:
        db.execute("DELETE FROM provider_call_contexts WHERE tenant_id=? AND attempt_id=?", ("local", dispatch.attempt_id))
    with pytest.raises(RouterServiceError):
        stable.mark_dispatched(dispatch)
    assert stable.control.state()["spent"] == 0 and not captured
