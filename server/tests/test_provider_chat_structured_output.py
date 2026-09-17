import pytest
from pydantic import ValidationError
from starlette.requests import Request
from server import main as m

def format_value():
    return {"type":"json_schema","json_schema":{"name":"rpg05_turn","strict":True,"schema":{"type":"object","properties":{"text":{"type":"string"}},"required":["text"],"additionalProperties":False}}}

def payload(**changes):
    return m.ChatRequest.model_validate({"model_id":"gpt-5.6-luna","messages":[{"role":"user","content":"neutral"}],"require_managed_route":True,"response_format":format_value(),**changes})

def test_exact_forwarding_and_legacy_unchanged():
    p=payload();out=m.build_upstream_payload(p,p.model_id)
    assert out["response_format"]==format_value()
    out["response_format"]["json_schema"]["name"]="changed"
    assert p.response_format==format_value()
    assert "response_format" not in m.build_upstream_payload(payload(response_format=None),p.model_id)

@pytest.mark.parametrize("changes",[{"require_managed_route":False},{"stop":["}"]},{"tool_mode":"mcp_tools"},{"gateway":"auto"}])
def test_bypass_shapes_rejected(changes):
    with pytest.raises(ValidationError):payload(**changes)

@pytest.mark.parametrize("mutate",[
 lambda s:s.update({"$ref":"https://outside.invalid/schema"}),
 lambda s:s.update({"additionalProperties":True}),
 lambda s:s.update({"required":[]}),
 lambda s:s.update({"allOf":[]}),
 lambda s:s.update({"properties":{"x":{"type":"string","description":"x"*100001}}}),
])
def test_invalid_schema_rejected(mutate):
    f=format_value();mutate(f["json_schema"]["schema"])
    with pytest.raises(ValidationError):payload(response_format=f)

@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,host,headers",[(False,"127.0.0.1",[]),(True,"10.0.0.2",[]),(True,"127.0.0.1",[(b"origin",b"http://evil.invalid")])])
async def test_disabled_remote_browser_rejected_before_dispatch(monkeypatch,enabled,host,headers):
    monkeypatch.setenv("RPG05_STRUCTURED_OUTPUT_ENABLED","true" if enabled else "false")
    def forbidden():raise AssertionError("must not reach provider")
    monkeypatch.setattr(m,"get_model_router_service",forbidden)
    response=await m.chat(payload(),Request({"type":"http","client":(host,1234),"headers":headers}))
    assert response.status_code==403

@pytest.mark.asyncio
@pytest.mark.parametrize("status",[200,400])
async def test_managed_fake_upstream_preserves_strict_payload_no_retry(tmp_path,monkeypatch,status):
    import httpx
    from server.tests.test_provider_chat_stable_chat import _service,_disable_runtime,_fake_client,_managed_request
    from server.model_router import configure_model_router,get_model_router_service
    original=get_model_router_service();service,_,_,_=_service(tmp_path);configure_model_router(service)
    sent=[];client=httpx.AsyncClient(transport=httpx.ASGITransport(app=m.app,client=("127.0.0.1",1234)),base_url="http://testserver")
    monkeypatch.setenv("MODEL_CONTROL_CHAT_ENABLED","true");monkeypatch.setenv("RPG05_STRUCTURED_OUTPUT_ENABLED","true")
    _disable_runtime(monkeypatch);monkeypatch.setattr(m.httpx,"AsyncClient",_fake_client(sent,status_code=status))
    try:
        async with client:
            response=await client.post("/api/chat",json={**_managed_request(),"response_format":format_value()})
    finally:configure_model_router(original)
    assert len(sent)==1
    assert sent[0]["json"]["response_format"]==format_value()
    assert sent[0]["url"]=="https://8.8.8.8/v1/chat/completions"
    if status==200:assert response.status_code==200 and "route_receipt" in response.text
    else:assert response.status_code>=400

@pytest.mark.parametrize("kind",["action","query"])
@pytest.mark.parametrize("keyed",[False,True])
def test_actual_rpg05_compiler_schema_is_accepted(kind,keyed):
    import json
    import subprocess
    from pathlib import Path
    root=Path(__file__).resolve().parents[2]/"experiments"/"ai-rpg-engine"
    script="import {compileStructuredSchema} from './ui-host/structured-schema.mjs';import {compileKeyedSchema} from './ui-host/keyed-turn.mjs';import {loadBuiltinBundle} from './ui-host/setup.mjs';console.log(JSON.stringify((process.argv[2]==='keyed'?compileKeyedSchema:compileStructuredSchema)(loadBuiltinBundle().cardPackage,{exchangeId:'ex.offline',input:{kind:process.argv[1],text:'Neutral'}}).responseFormat));"
    result=subprocess.run(["node","--input-type=module","-e",script,kind,"keyed" if keyed else "legacy"],cwd=root,capture_output=True,text=True,encoding="utf-8",check=True,timeout=30)
    schema=json.loads(result.stdout)
    assert payload(response_format=schema).response_format==schema
    assert m.build_upstream_payload(payload(response_format=schema),"gpt-5.6-luna")["response_format"]==schema

@pytest.mark.parametrize("value",[{},-1,True,"100"])
def test_invalid_schema_limits_rejected(value):
    f=format_value();f["json_schema"]["schema"]["properties"]["text"]["maxLength"]=value
    with pytest.raises(ValidationError):payload(response_format=f)
