import asyncio, importlib.util, json, os
from pathlib import Path
import pytest
from fastapi import HTTPException
SPEC=importlib.util.spec_from_file_location('r4_protocol',Path(__file__).with_name('protocol.py'))
m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)
class Request:
 def __init__(self,value):self.value=value
 async def stream(self):yield json.dumps(self.value).encode()
def body(adapter='lighthouse'):
 return dict(sessionId='session',requestId='request',selectionId='selection',selectionRevision='revision',parameters=dict(temperature=.7,top_p=.8,max_tokens=8192 if adapter=='lighthouse' else 32768),messages=[dict(role='system',content='rules'),dict(role='user',content='input')],tools=[m.TOOLS[adapter]],tool_choice='none')
def test_direct_validated_without_continuation_and_keeps_none(monkeypatch):
 monkeypatch.setenv('RPG_CORE_REAL_ENABLED','true');monkeypatch.setenv('RPG_LOCAL_STATE_TOOLS_ENABLED','true')
 for adapter,profile in [('lighthouse','core-query-r3'),('cultivation','god-long-v1')]:
  original=body(adapter);assert asyncio.run(m.Protocol(adapter).body(Request(original),profile))==original
@pytest.mark.parametrize('change',['auto','role','parameters','tool','unknown-key','continuation'])
def test_direct_rejects_invalid_or_continuation(monkeypatch,change):
 monkeypatch.setenv('RPG_CORE_REAL_ENABLED','true');monkeypatch.setenv('RPG_LOCAL_STATE_TOOLS_ENABLED','true');value=body()
 if change=='auto':value['tool_choice']='auto'
 if change=='role':value['messages'][1]['role']='system'
 if change=='parameters':value['parameters']['max_tokens']=99999
 if change=='tool':value['tools'][0]={'type':'function','function':{'name':'write_state'}}
 if change=='unknown-key':value['api_key']='must-not-be-accepted'
 if change=='continuation':value['messages'].extend([{'role':'assistant','content':None,'tool_calls':[]},{'role':'tool','tool_call_id':'x','content':'{}'}])
 with pytest.raises(HTTPException):asyncio.run(m.Protocol('lighthouse').body(Request(value),'core-query-r3'))
def test_disabled_by_default(monkeypatch):
 monkeypatch.delenv('RPG_CORE_REAL_ENABLED',raising=False)
 with pytest.raises(HTTPException):asyncio.run(m.Protocol('lighthouse').body(Request(body()),'core-query-r3'))
