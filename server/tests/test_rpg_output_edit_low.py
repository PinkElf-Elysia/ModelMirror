import asyncio
import copy
import json
from types import SimpleNamespace
import httpx
import pytest
from server.model_router.rpg_output_edit_low import ReasoningLowDiagnosticTransport, digest


def baseline():
    return {"model":"google/gemini-3.8-flash", "messages":[{"role":"system","content":"synthetic"},{"role":"user","content":"neutral"}],
        "temperature":0.7,"top_p":0.8,"max_tokens":16384,"stream":True,"stream_options":{"include_usage":True},
        "provider":{"allow_fallbacks":False,"require_parameters":True},"transforms":[]}


class Base:
    def __init__(self):self.calls=0
    def build_authorized_stream_request(self, client, target, authorized, payload):
        return httpx.Request("POST","https://example.invalid/",json=payload,headers={"Authorization":"Bearer synthetic-secret"})
    async def send_authorized_stream(self, client, request):
        self.calls+=1
        return httpx.Response(200,content=b"synthetic")


def test_only_reasoning_changes_and_one_call_survives_restart(tmp_path):
    base=Base();original=baseline();checks=[]
    def create():return ReasoningLowDiagnosticTransport(base,directory=tmp_path,baseline=original,connection_id="route",purpose="story",pre_send_check=lambda:checks.append(True))
    transport=create();target=SimpleNamespace(connection_id="route",provider_kind="openrouter")
    request=transport.build_authorized_stream_request(None,target,None,original)
    wire=json.loads(request.content);assert wire.pop("reasoning")=={"effort":"low"};assert wire==original
    assert original==baseline() and checks==[True]
    asyncio.run(transport.send_authorized_stream(None,request));assert base.calls==1
    with pytest.raises(FileExistsError):asyncio.run(transport.send_authorized_stream(None,request))
    with pytest.raises(ValueError,match="ALREADY_CLAIMED"):create().build_authorized_stream_request(None,target,None,original)
    assert base.calls==1
    profile=json.loads((tmp_path/"profile.json").read_text());assert profile['effectiveRequestHash']==digest(json.loads(request.content))
    assert all('synthetic-secret' not in p.read_text() for p in tmp_path.iterdir())


@pytest.mark.parametrize("field,value",[("max_tokens",32768),("temperature",1),("model","other"),("messages",[]),("reasoning",{"effort":"high"}),("provider",{"allow_fallbacks":False,"require_parameters":True,"only":["google-ai-studio"]})])
def test_any_other_wire_change_blocks_before_send(tmp_path,field,value):
    base=Base();transport=ReasoningLowDiagnosticTransport(base,directory=tmp_path,baseline=baseline(),connection_id="route",purpose="story",pre_send_check=lambda:None)
    changed=baseline();changed[field]=value
    with pytest.raises(ValueError,match="BASELINE_CHANGED"):transport.build_authorized_stream_request(None,SimpleNamespace(connection_id="route",provider_kind="openrouter"),None,changed)
    assert base.calls==0 and not list(tmp_path.iterdir())


def test_route_and_freshness_fail_closed(tmp_path):
    base=Base()
    def reject():raise ValueError('STALE_CATALOG')
    transport=ReasoningLowDiagnosticTransport(base,directory=tmp_path,baseline=baseline(),connection_id="route",purpose="story",pre_send_check=reject)
    with pytest.raises(ValueError,match="ROUTE_CHANGED"):transport.build_authorized_stream_request(None,SimpleNamespace(connection_id="other",provider_kind="openrouter"),None,baseline())
    with pytest.raises(ValueError,match="STALE_CATALOG"):transport.build_authorized_stream_request(None,SimpleNamespace(connection_id="route",provider_kind="openrouter"),None,baseline())
    assert base.calls==0 and not list(tmp_path.iterdir())


def test_b_profile_rejects_c_and_hidden_reasoning(tmp_path):
    for changes in ({"max_tokens":32768},{"reasoning":{"effort":"low"}},{"stream":False}):
        with pytest.raises(ValueError,match="BASELINE_INVALID"):ReasoningLowDiagnosticTransport(Base(),directory=tmp_path,baseline={**baseline(),**changes},connection_id="route",purpose="story",pre_send_check=lambda:None)


def test_failed_send_never_retries(tmp_path):
    class Failure(Base):
        async def send_authorized_stream(self,client,request):self.calls+=1;raise TimeoutError()
    base=Failure();transport=ReasoningLowDiagnosticTransport(base,directory=tmp_path,baseline=baseline(),connection_id="route",purpose="story",pre_send_check=lambda:None)
    request=transport.build_authorized_stream_request(None,SimpleNamespace(connection_id="route",provider_kind="openrouter"),None,baseline())
    with pytest.raises(TimeoutError):asyncio.run(transport.send_authorized_stream(None,request))
    with pytest.raises(FileExistsError):asyncio.run(transport.send_authorized_stream(None,request))
    assert base.calls==1


def test_call_budget_has_exact_two_story_two_memory_two_small_slots(tmp_path):
    from server.model_router.rpg_output_edit_low import claim_call
    for purpose in ['story','memory','small']:
        for n in range(2):claim_call(tmp_path,purpose,{'sessionId':'fixture','requestId':purpose+str(n)})
        with pytest.raises(ValueError,match='CALL_LIMIT'):claim_call(tmp_path,purpose,{'sessionId':'fixture','requestId':purpose+'extra'})
    with pytest.raises(ValueError,match='PURPOSE_INVALID'):claim_call(tmp_path,'refresh',{'sessionId':'fixture','requestId':'x'})
    assert len(list(tmp_path.glob('*/job.json')))==6
