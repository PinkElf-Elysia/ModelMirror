import copy
import json
import pytest
from server.model_router import rpg_local_state_tools as tools
from server.tests.test_rpg_bridge import setup, payload, HEADERS, stream

PATH = "/api/rpg/v1/local-state/chat/completions"
def tool_stream(model):
    call = {"index":0,"id":"call-one","type":"function","function":{"name":"query_local_state","arguments":'{"queries":['},"extra_content":{"google":{"thought_signature":"opaque-signature"}}}
    chunks = [
        {"model":model,"choices":[{"delta":{"tool_calls":[call]}}]},
        {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":'{"domain":"items","kind":"丹药"}]}'}}]},"finish_reason":"tool_calls"}]},
        {"choices":[],"usage":{"prompt_tokens":20,"completion_tokens":10,"total_tokens":30}},
    ]
    return "".join("data: "+json.dumps(c)+"\n\n" for c in chunks)+"data: [DONE]\n\n"

def value_for(client):
    return {**payload(client),"tools":[tools.TOOL],"tool_choice":"auto"}

def test_native_loop_preserves_signature_and_lookup(setup,monkeypatch):
    client,stable,repo,meta,captured,response = setup
    monkeypatch.setenv("RPG_LOCAL_STATE_TOOLS_ENABLED","true")
    meta["supported_parameters"] += ["tools","tool_choice"]
    value = value_for(client)
    model = client.get("/api/rpg/v1/models",headers=HEADERS).json()["models"][0]["model"]
    response["content"] = tool_stream(model)
    result = client.post(PATH,headers=HEADERS,json=value)
    assert result.status_code == 200,result.text
    data = result.json()
    assert data["raw"] == "" and data["finishReason"] == "tool_calls"
    assert data["message"]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "opaque-signature"
    assert captured[0]["tools"] == [tools.TOOL]
    saved = client.get("/api/rpg/v1/local-state/results/"+value["sessionId"]+"/"+value["requestId"],headers=HEADERS)
    assert saved.json()["body"] == data
    assert client.post(PATH,headers=HEADERS,json=value).status_code == 409
    follow = copy.deepcopy(value); follow["requestId"] += "-final"; follow["tool_choice"] = "none"
    follow["messages"] += [data["message"],{"role":"tool","tool_call_id":"call-one","content":'{"stateRevision":0,"results":[]}'}]
    response["content"] = stream(model=model,raw="正文")
    final = client.post(PATH,headers=HEADERS,json=follow)
    assert final.status_code == 200,final.text
    assert captured[1]["messages"] == follow["messages"]
    assert len(captured) == 2
    assert final.json()["finishReason"] == "stop"

def test_default_off_capability_and_invalid_protocol(setup,monkeypatch):
    client,_,_,meta,captured,_ = setup
    v = value_for(client)
    assert client.post(PATH,headers=HEADERS,json=v).status_code == 503
    monkeypatch.setenv("RPG_LOCAL_STATE_TOOLS_ENABLED","true")
    assert client.post(PATH,headers=HEADERS,json=v).status_code == 409
    meta["supported_parameters"] += ["tools","tool_choice"]
    v = value_for(client)
    v["tools"][0] = {"type":"function","function":{"name":"write_state"}}
    assert client.post(PATH,headers=HEADERS,json=v).status_code == 422
    assert captured == []

def test_text_path_still_rejects_tools(setup):
    client,_,_,_,captured,_ = setup
    assert client.post("/api/rpg/v1/chat/completions",headers=HEADERS,json=value_for(client)).status_code == 422
    assert not captured

def test_stream_rejects_truncation_invalid_name_second_tool():
    s = tool_stream("test")
    for bad in [s.replace('"tool_calls"}','"length"}'),s.replace('query_local_state','write_state'),s.replace('"index": 0','"index": 1'),s.replace('[DONE]','')]:
        with pytest.raises(ValueError):
            tools.parse_sse(bad,"test")


def test_server_schema_matches_local_tool_without_runtime_file_dependency():
    from pathlib import Path
    source = Path(__file__).resolve().parents[2] / "experiments/ai-rpg-engine/local-state/tools/query-tool.json"
    assert tools.TOOL == json.loads(source.read_text(encoding="utf-8"))
