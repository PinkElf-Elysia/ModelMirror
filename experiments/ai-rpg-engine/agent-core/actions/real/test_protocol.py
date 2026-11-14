import asyncio,importlib.util,json
from pathlib import Path
import pytest
from fastapi import HTTPException
SPEC=importlib.util.spec_from_file_location("r5_protocol",Path(__file__).with_name("protocol.py"))
m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)
class Request:
 def __init__(self,value):self.value=value
 async def stream(self):yield json.dumps(self.value).encode()
def value():
 return dict(sessionId="session",requestId="request",selectionId="selection",selectionRevision="revision",parameters=dict(temperature=.7,top_p=.8,max_tokens=32768),messages=[dict(role="system",content="rules"),dict(role="user",content="input")],tools=[],tool_choice="none")
def test_default_disabled(monkeypatch):
 monkeypatch.delenv("RPG_ACTIONS_REAL_ENABLED",raising=False)
 with pytest.raises(HTTPException):asyncio.run(m.Protocol().body(Request(value()),"god-long-v1"))
def test_direct_valid(monkeypatch):
 monkeypatch.setenv("RPG_ACTIONS_REAL_ENABLED","true");v=value()
 assert asyncio.run(m.Protocol().body(Request(v),"god-long-v1"))==v
@pytest.mark.parametrize("change",["tool","profile","identity","extra","messages"])
def test_invalid(monkeypatch,change):
 monkeypatch.setenv("RPG_ACTIONS_REAL_ENABLED","true");v=value()
 if change=="tool":v["tools"]=[{"function":{"name":"execute"}}]
 if change=="profile":v["parameters"]["max_tokens"]=99999
 if change=="identity":v["sessionId"]="../other"
 if change=="extra":v["endpoint"]="external"
 if change=="messages":v["messages"][1]["role"]="system"
 with pytest.raises(HTTPException):asyncio.run(m.Protocol().body(Request(v),"god-long-v1"))
def test_native_sse_both_tools_metadata_and_model():
 for name,args in [("query_local_state",{"queries":[{"domain":"items"}]}),("prepare_action",{"actionId":"use_medicine","itemId":"medicine","quantity":2})]:
  call={"index":0,"id":"call","type":"function","function":{"name":name,"arguments":json.dumps(args)},"extra_content":{"google":{"thought_signature":"opaque"}}}
  sse='data: '+json.dumps({"model":"google/gemini-3.8-flash","choices":[{"delta":{"tool_calls":[call]},"finish_reason":"tool_calls"}]})+'\n\ndata: [DONE]\n\n'
  evidence={};m.Protocol().parse_sse(sse,"google/gemini-3.8-flash",evidence)
  assert evidence["message"]["tool_calls"][0]["extra_content"]==call["extra_content"]
  with pytest.raises(ValueError):m.Protocol().parse_sse(sse,"different-model",{})
