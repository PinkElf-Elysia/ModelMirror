import importlib.util,json,os
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location("e4_protocol",Path(__file__).with_name("condition_protocol.py"));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def test_stream_text_usage_and_duplicate_finish():
    frames=[{"model":"google/gemini-3.8-flash","choices":[{"delta":{"content":"{}"},"finish_reason":"stop"}]},{"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":100,"completion_tokens":20,"total_tokens":120}}]
    sse="".join("data: "+json.dumps(x)+"\n\n" for x in frames)+"data: [DONE]\n\n"
    evidence={};raw,model,usage=m.Protocol.parse_sse(sse,"google/gemini-3.8-flash",evidence)
    assert raw=="{}" and usage["total_tokens"]==120 and evidence["finishReason"]=="stop"
def test_truncated_stream_rejected():
    with pytest.raises(Exception):m.Protocol.parse_sse('data: {"choices":[{"delta":{"content":"x"}}]}\n\n',"google/gemini-3.8-flash",{})
@pytest.mark.asyncio
async def test_only_one_fixed_text_request(monkeypatch):
    monkeypatch.setenv("RPG_E4_REAL_ENABLED","true")
    class Request:
        def __init__(self,v):self.v=v
        async def stream(self):yield json.dumps(self.v).encode()
    value=dict(sessionId="e4-condition-check-mock-v1",requestId="gift-1",selectionId="s",selectionRevision="r",messages=[dict(role="system",content="rules"),dict(role="user",content="reply")],parameters=m.PARAMETERS,tools=[],tool_choice="none")
    assert (await m.Protocol().body(Request(value),"core-query-r3"))==value
    for mutation in [dict(requestId="gift-2"),dict(tool_choice="auto"),dict(sessionId="foreign"),dict(tools=[{}])]:
        with pytest.raises(Exception):await m.Protocol().body(Request({**value,**mutation}),"core-query-r3")
