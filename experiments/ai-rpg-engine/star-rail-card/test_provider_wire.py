"""Send turns through the local card + FastAPI bridge; inspect provider-bound HTTP bodies."""
import json
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from server.tests.test_rpg_bridge import TOKEN, setup, stream


CARD = Path(__file__).resolve().parent
MODEL = "google/gemini-3.8-flash"
BATTLE = "战斗机制"
EXAMPLE = "文风正反例"
ENGINES = [f"引擎{i:02}执行" for i in range(1, 6)]
MACRO, DUEL, EMOTION, PUZZLE, DESIRE = ENGINES
CASES = [
    ("拔剑迎敌，准备弱点击破。", {BATTLE, DUEL, EXAMPLE}),
    ("继续。", {BATTLE, DUEL}),  # Prior assistant narrative is still in combat.
    ("重逢后告别。", {EMOTION}),
    ("循着线索推理。", {PUZZLE}),
    ("纪元更迭，见证宇宙生灭。", {MACRO}),
    ("我与他展开权力交易。", {DESIRE}),
    ("喝茶闲聊，校正文风。", {EXAMPLE}),
    ("我们喝茶。", set()),
    ("门打开后会发生什么？", {BATTLE, *ENGINES}),
    ("对决之际告别，再用终结技。", {BATTLE, DUEL, EMOTION}),
]
ANCHORS = {
    BATTLE: ("战斗场景调用〈战斗机制〉", "## 第五章"),
    MACRO: ("宇宙生灭|纪元更迭", "- 引擎02:"),
    DUEL: ("个人搏杀|小队火拼", "- 引擎03:"),
    EMOTION: ("情感极致爆发|宿命相遇与别离", "- 引擎04:"),
    PUZZLE: ("悬疑解谜|多方博弈", "- 引擎05:"),
    DESIRE: ("[OOC绝对否决权]", "[裸露细节规则]"),
    EXAMPLE: ("[输出前自检]", "### 5.3."),
}

NODE_SESSION = r"""import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {randomUUID} from 'node:crypto';
import {once} from 'node:events';
import {installStarRail} from './integration.mjs';

const [directory, bridgeURL, fixtureToken, inputs] = process.argv.slice(1);
const server = createServer();
server.listen(0, '127.0.0.1');
await once(server, 'listening');
const origin='http://127.0.0.1:'+server.address().port;
const control={enabled:true,limit:20,baseURL:bridgeURL,serviceToken:fixtureToken};
installStarRail(server,{directory,control,origins:[origin],autoSummaryV2:false});
const root=origin+'/rpg-app/star-rail/chat/api/sessions';
async function call(url, data) {
  const response=await fetch(url,data===undefined?{}:{
    method:'POST',headers:{Origin:origin,'Content-Type':'application/json'},body:JSON.stringify(data)
  });
  const value=await response.json();
  assert.ok(response.ok,value.error || String(response.status));
  return value;
}
try {
  let session=await call(root,{opening:''});
  const url=root+'/'+session.id;
  for(const input of JSON.parse(inputs)){
    const state=await call(url+'/memory-palace');
    const before=session.turns.length;
    session=await call(url+'/send',{operationId:randomUUID(),expectedSessionRevision:session.revision,
      expectedMemoryRevision:state.memoryRevision,expectedContextPolicyRevision:state.effectivePolicy.revision,input});
    assert.equal(session.turns.length,before+1);
  }
} finally {
  await new Promise(resolve=>server.close(resolve));
}"""


@pytest.mark.parametrize("setup", [MODEL], indirect=True)
def test_provider_wire_contains_current_rules_at_their_source_positions(setup, tmp_path):
    client, _, _, _, captured, reply = setup
    # A different fixture reply after turn one makes continuation observable.
    reply["content"] = stream(model=MODEL, raw="<z><p>敌人仍在攻击。</p></z>")
    app = client.app

    @app.middleware("http")
    async def next_reply(request, call_next):
        if captured:
            reply["content"] = stream(model=MODEL, raw="<z><p>车窗映着茶杯。</p></z>")
        return await call_next(request)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    url = "http://127.0.0.1:" + str(sock.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        inputs = [text for text, _ in CASES]
        subprocess.run(["node", "--input-type=module", "-e", NODE_SESSION, str(tmp_path/"card"),
                        url, TOKEN, json.dumps(inputs)], check=True, cwd=CARD, timeout=90)
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
    book = json.loads((CARD/"resources/worldbook.json").read_text(encoding="utf8"))
    rules = {entry["title"]: entry["body"] for entry in book if entry["mode"] == "rule"}
    assert len(captured) == len(CASES)
    for payload, (text, expected) in zip(captured, CASES, strict=True):
        assert payload["model"] == MODEL
        messages = payload["messages"]
        assert messages[-1] == {"role": "user", "content": text}
        assert messages[0]["role"] == "system"
        content = messages[0]["content"]
        assert "⟦" not in content
        for required in ("- 基盘分歧 B：", "- 七条合并：", "6. 【记忆配置】："):
            assert required in content
        for name, body in rules.items():
            count = content.count(body)
            assert count == int(name in expected), (text, name, count)
            if count:
                left, right = ANCHORS[name]
                assert content.index(left) < content.index(body) < content.index(right), (text, name)
