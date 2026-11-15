import importlib.util,json
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location("e6_protocol",Path(__file__).with_name("protocol.py"));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
@pytest.mark.asyncio
async def test_fixed_two_text_requests(monkeypatch):
 monkeypatch.setenv("RPG_E6_REAL_ENABLED","true")
 class Request:
  def __init__(self,v):self.v=v
  async def stream(self):yield json.dumps(self.v).encode()
 v=dict(sessionId="e6-suggestions-mock-v1",requestId="suggestion-1",selectionId="s",selectionRevision="r",messages=[dict(role="system",content="rules"),dict(role="user",content="reply")],parameters=m.PARAMETERS,tools=[],tool_choice="none")
 for ident in ("suggestion-1","suggestion-2"):assert (await m.Protocol().body(Request({**v,"requestId":ident}),"core-query-r3"))["requestId"]==ident
 for mutation in [dict(requestId="suggestion-3"),dict(tool_choice="auto"),dict(sessionId="foreign"),dict(tools=[{}])]:
  with pytest.raises(Exception):await m.Protocol().body(Request({**v,**mutation}),"core-query-r3")
def test_truncation():
 with pytest.raises(Exception):m.Protocol.parse_sse('data: {"choices":[{"delta":{"content":"x"}}]}\n\n',"google/gemini-3.8-flash",{})
