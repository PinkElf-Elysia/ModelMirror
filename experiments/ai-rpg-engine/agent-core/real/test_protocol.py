import asyncio, copy, importlib.util, json, os
from pathlib import Path
import pytest
from fastapi import HTTPException
from server.model_router import rpg_bridge as b, rpg_local_state_tools as old
spec=importlib.util.spec_from_file_location("core_protocol",Path(__file__).with_name("protocol.py"))
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
class Request:
    def __init__(self,v):self.v=v
    async def stream(self):yield json.dumps(self.v).encode()
def payload(adapter="lighthouse"):
    return dict(sessionId="s",requestId="r",selectionId="x",selectionRevision="v",messages=[dict(role="system",content="s"),dict(role="user",content="u")],parameters=dict(temperature=.7,top_p=.8,max_tokens=8192),tools=[copy.deepcopy(p.TOOLS[adapter])],tool_choice="auto")
def test_default_off_and_exact_schema(monkeypatch):
    monkeypatch.setenv("RPG_LOCAL_STATE_TOOLS_ENABLED","true")
    monkeypatch.delenv("RPG_CORE_REAL_ENABLED",raising=False)
    with pytest.raises(HTTPException):asyncio.run(p.Protocol("lighthouse").body(Request(payload()),"core-query-r3"))
    monkeypatch.setenv("RPG_CORE_REAL_ENABLED","true")
    v=payload();assert asyncio.run(p.Protocol("lighthouse").body(Request(v),"core-query-r3"))==v
    with pytest.raises(HTTPException):asyncio.run(old.body(Request(v),"core-query-r3"))
    v["tools"][0]["function"]["description"]="untrusted replacement"
    with pytest.raises(HTTPException):asyncio.run(p.Protocol("lighthouse").body(Request(v),"core-query-r3"))
    assert b.parameters_for("google/gemini-3.8-flash","default")["max_tokens"]==16384
    assert b.parameters_for("google/gemini-3.8-flash","god-long-v1")["max_tokens"]==32768

def test_injected_arguments_stay_card_scoped_and_preserve_metadata():
    args=json.dumps({"queries":[{"domain":"supplies"}]})
    call=dict(index=0,id="q",type="function",function=dict(name="query_local_state",arguments=args),extra_content={"google":{"thought_signature":"opaque"}})
    frame={"model":"m","choices":[{"delta":{"tool_calls":[call]},"finish_reason":"tool_calls"}]}
    s="data: "+json.dumps(frame)+"\n\ndata: [DONE]\n\n"
    evidence={};p.Protocol("lighthouse").parse_sse(s,"m",evidence)
    assert evidence["message"]["tool_calls"][0]["extra_content"]==call["extra_content"]
    with pytest.raises(ValueError):old.parse_sse(s,"m")
    with pytest.raises(ValueError):p.lighthouse_args({"queries":[{"domain":"items"}]})
    with pytest.raises(ValueError):p.lighthouse_args({"queries":[{"domain":"supplies","path":"secret"}]})
    with pytest.raises(ValueError):p.lighthouse_args({"queries":[{"domain":"supplies","limit":True}]})
