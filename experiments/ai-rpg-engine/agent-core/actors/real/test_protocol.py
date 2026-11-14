import asyncio,importlib.util,json
from pathlib import Path
import pytest
from fastapi import HTTPException
SPEC=importlib.util.spec_from_file_location("r6_protocol",Path(__file__).with_name("protocol.py"));m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)
class Request:
 def __init__(self,value):self.value=value
 async def stream(self):yield json.dumps(self.value).encode()
def value():return dict(sessionId="session",requestId="request",selectionId="selection",selectionRevision="revision",
 parameters=dict(temperature=.7,top_p=.8,max_tokens=8192),
 messages=[dict(role="system",content="host"),dict(role="assistant",content="frozen opening"),dict(role="user",content="question"),dict(role="system",content="character post")],tools=[],tool_choice="none")
def test_default_disabled(monkeypatch):
 monkeypatch.delenv("RPG_ACTORS_REAL_ENABLED",raising=False)
 with pytest.raises(HTTPException):asyncio.run(m.Protocol().body(Request(value()),"core-query-r3"))
def test_character_messages_valid(monkeypatch):
 monkeypatch.setenv("RPG_ACTORS_REAL_ENABLED","true");monkeypatch.setenv("RPG_CORE_REAL_ENABLED","true")
 assert asyncio.run(m.Protocol().body(Request(value()),"core-query-r3"))==value()
@pytest.mark.parametrize("change",["tool","identity","parameters","extra","orphan-tool"])
def test_invalid(monkeypatch,change):
 monkeypatch.setenv("RPG_ACTORS_REAL_ENABLED","true");monkeypatch.setenv("RPG_CORE_REAL_ENABLED","true");v=value()
 if change=="tool":v["tools"]=[dict(type="function",function=dict(name="execute"))]
 if change=="identity":v["sessionId"]="../other"
 if change=="parameters":v["parameters"]["max_tokens"]=100000
 if change=="extra":v["endpoint"]="elsewhere"
 if change=="orphan-tool":v["messages"].append(dict(role="tool",content="secret",tool_call_id="absent"))
 with pytest.raises(HTTPException):asyncio.run(m.Protocol().body(Request(v),"core-query-r3"))
