"""Offline character transport regression: real JS host and ASGI, mocked upstream."""
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from server.model_router import rpg_authoring_execution as tasks
from server.tests.test_rpg_bridge import setup, HEADERS, TOKEN, _declared_parameters
from server.tests.test_rpg_authoring_execution import task, lookup, enabled

PATH = "/api/rpg/v1/authoring/tasks"
CONTRACT = "modelmirror.rpg.character-messages/1"


def character_task(setup, roles=("system", "assistant", "user", "system")):
    value = task(setup)
    value["messageContract"] = CONTRACT
    value["messages"] = [{"role": role, "content": f"文本 {i}\n保留"} for i, role in enumerate(roles)]
    return value


@pytest.mark.parametrize("roles", [
    ("system", "assistant", "user"),
    ("system", "assistant", "user", "system"),
    ("system", "assistant", "user", "assistant", "user"),
    ("system", "assistant", "user", "assistant", "user", "system"),
])
def test_character_messages_exact_roundtrip_and_no_replay(setup, monkeypatch, roles):
    client, _, _, _, captured, _ = setup
    value = character_task(setup, roles); enabled(monkeypatch)
    result = client.post(PATH, headers=HEADERS, json=value)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "completed" and body["receipt"]["retries"] == 0
    assert captured[0]["messages"] == value["messages"]
    assert "messageContract" not in captured[0]  # host-only transport metadata
    assert lookup(client, value).json() == body
    assert client.post(PATH, headers=HEADERS, json=value).json() == body
    changed = {**value, "messageContract": "unknown"}
    assert client.post(PATH, headers=HEADERS, json=changed).status_code == 422
    changed = {**value, "messages": [dict(m) for m in value["messages"]]}
    changed["messages"][-1]["content"] += "changed"
    assert client.post(PATH, headers=HEADERS, json=changed).status_code == 409
    assert len(captured) == 1


@pytest.mark.parametrize("roles", [
    (), ("system", "assistant"), ("system", "user"),
    ("user", "assistant", "user"), ("system", "user", "assistant"),
    ("system", "assistant", "assistant", "user"),
    ("system", "assistant", "user", "assistant"),
    ("system", "assistant", "system", "user"),
    ("system", "assistant", "user", "system", "system"),
    ("system", "assistant", "tool"),
])
def test_character_invalid_sequences_never_claim(setup, monkeypatch, roles):
    client, _, repo, _, captured, _ = setup
    enabled(monkeypatch)
    result = client.post(PATH, headers=HEADERS, json=character_task(setup, roles))
    assert result.status_code == 422
    assert captured == [] and not list(repo.storage_dir.glob("rpg-authoring-tasks/*"))


@pytest.mark.parametrize("kind", ["missing", "unknown", "null", "memory", "extra-field", "wrong-content", "too-long"])
def test_character_contract_is_explicit_and_bounded(setup, monkeypatch, kind):
    client, _, repo, _, captured, _ = setup
    value = character_task(setup); enabled(monkeypatch)
    if kind == "missing": value.pop("messageContract")
    elif kind == "unknown": value["messageContract"] = "arbitrary"
    elif kind == "null": value["messageContract"] = None
    elif kind == "memory": value["purpose"] = "memory"
    elif kind == "extra-field": value["messages"][1]["tool_calls"] = []
    elif kind == "wrong-content": value["messages"][1]["content"] = {"text": "x"}
    elif kind == "too-long": value["messages"] = value["messages"][:2] + [{"role": "user" if i % 2 == 0 else "assistant", "content": "x"} for i in range(2203)]
    assert client.post(PATH, headers=HEADERS, json=value).status_code == 422
    assert captured == [] and not list(repo.storage_dir.glob("rpg-authoring-tasks/*"))


def test_character_auth_and_execution_gate_unchanged(setup, monkeypatch):
    client, _, _, _, captured, _ = setup
    value = character_task(setup)
    assert client.post(PATH, json=value).status_code == 401
    assert client.post(PATH, headers={**HEADERS, "Origin": "http://localhost"}, json=value).status_code == 403
    monkeypatch.setenv("RPG_AUTHORING_EXECUTION_ENABLED", "false")
    result = client.post(PATH, headers=HEADERS, json=value).json()
    assert result["receipt"]["dispatched"] is False
    enabled(monkeypatch)
    assert client.post(PATH, headers=HEADERS, json=value).json() == result
    assert captured == []


def test_actual_character_host_through_gateway(setup, monkeypatch, tmp_path):
    client, _, _, meta, captured, _ = setup
    _declared_parameters(meta); enabled(monkeypatch)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def forward(self):
            body = self.rfile.read(int(self.headers.get("content-length", "0")))
            reply = client.request(self.command, self.path, content=body,
                headers={k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")})
            self.send_response(reply.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply.content)))
            self.end_headers(); self.wfile.write(reply.content)
        do_GET = forward
        do_POST = forward
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    module = Path(__file__).resolve().parents[2] / "experiments" / "ai-rpg-engine"
    settings = {"origin": f"http://127.0.0.1:{server.server_port}", "serviceToken": TOKEN,
                "directory": str(tmp_path / "character-host")}
    try:
        result = subprocess.run(["node", "character/gateway-acceptance.mjs", json.dumps(settings)],
            cwd=module, capture_output=True, text=True, encoding="utf-8", timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        evidence = json.loads(result.stdout)
        assert evidence["turns"] == 2 and evidence["siblingTurns"] == 0 and evidence["recovered"]
        assert len(captured) == 2
        assert [m["role"] for m in captured[0]["messages"]] == ["system", "assistant", "user", "system"]
        assert [m["role"] for m in captured[1]["messages"]] == ["system", "assistant", "user", "assistant", "user", "system"]
        assert captured[0]["messages"][1]["content"] == "你好，玩家。测试夹具在灯塔等你。"
        assert captured[0]["messages"][-1]["content"] == "角色后置规则：保留玩家选择。"
        assert "示例专用句" in captured[0]["messages"][0]["content"]
        assert "作者说明不可进入模型" not in json.dumps(captured, ensure_ascii=False)
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=5)


@pytest.mark.parametrize("terminal", ["length", "unknown"])
def test_character_non_success_retains_original_without_retry(setup, monkeypatch, terminal):
    from server.tests.test_rpg_bridge import stream
    client, _, _, _, captured, response = setup
    value = character_task(setup); enabled(monkeypatch)
    response["content"] = stream(raw="保留的部分回复", finish="length" if terminal == "length" else "stop", done=terminal != "unknown")
    body = client.post(PATH, headers=HEADERS, json=value).json()
    assert body["raw"] == "保留的部分回复"
    assert body["status"] == ("completed" if terminal == "length" else "unknown")
    assert body["receipt"]["retries"] == 0
    assert lookup(client, value).json() == body
    assert client.post(PATH, headers=HEADERS, json=value).json() == body
    assert len(captured) == 1


def test_character_typed_parameters_keep_receipt_and_message_binding(setup, monkeypatch):
    from server.tests.test_rpg_parameters_v2 import declare, config, task as typed_task
    client, _, _, meta, captured, _ = setup
    declare(meta); enabled(monkeypatch)
    value = typed_task(client, config({"temperature": .7, "maxOutputTokens": 1536, "thinking.enabled": False}))
    value["messageContract"] = CONTRACT
    value["messages"] = [{"role": r, "content": "原文"} for r in ["system", "assistant", "user", "system"]]
    body = client.post(PATH, headers=HEADERS, json=value).json()
    assert body["status"] == "completed"
    assert body["receipt"]["format"] == "modelmirror.rpg.authoring-task-receipt/2"
    assert body["receipt"]["providerRequestHash"]
    assert captured[0]["messages"] == value["messages"]
    assert captured[0]["reasoning"] == {"enabled": False}
    assert "messageContract" not in captured[0]
    assert lookup(client, value).json() == body
