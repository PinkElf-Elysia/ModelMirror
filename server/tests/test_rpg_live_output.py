"""Offline transports: no external model, route update or certification."""
import asyncio
import json

import httpx
import pytest

from server.model_router import rpg_live_output as live
from server.tests.test_rpg_authoring_execution import task, enabled, PATH
from server.tests.test_rpg_bridge import setup, HEADERS


@pytest.mark.parametrize("stop", [False, True])
def test_visible_prefix_before_terminal_and_original_request_only(setup, monkeypatch, stop):
    fixture_client, stable, _, _, _, _ = setup
    enabled(monkeypatch)
    value = task(setup)
    value["messageContract"] = live.GROUP
    path = f"/api/rpg/v1/authoring/live/{value['sessionId']}/{value['requestId']}"
    params = {"sourceRequestHash": value["sourceRequestHash"]}

    async def exercise():
        waiting, release, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        count = 0

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                event = {"choices": [{"delta": {"reasoning_content": "hidden", "content": "早🌱"}}]}
                b = ("data: " + json.dumps(event, ensure_ascii=False) + "\r\n\r\n").encode()
                cut = b.index("早".encode()) + 1
                yield b[:cut]
                yield b[cut:]
                waiting.set()
                await release.wait()
                yield b'data: {"choices":[{"delta":{"content":"!"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

            async def aclose(self):
                closed.set()

        def upstream(request):
            nonlocal count
            count += 1
            return httpx.Response(200, stream=Stream(), headers={"content-type": "text/event-stream"})

        monkeypatch.setattr(stable.transport, "client_kwargs", lambda: {"transport": httpx.MockTransport(upstream)})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture_client.app), base_url="http://test") as client:
            running = asyncio.create_task(client.post(PATH, headers=HEADERS, json=value))
            try:
                await asyncio.wait_for(waiting.wait(), 10)
                first = await client.get(path, params=params, headers=HEADERS)
                assert first.json()["text"] == "早🌱" and first.json()["next"] == 2 and not running.done()
                assert (await client.get(path, params={**params, "cursor": 2}, headers=HEADERS)).json()["text"] == ""
                assert (await client.post(path + "/stop", params={"sourceRequestHash": "0"*64}, headers=HEADERS)).status_code == 409
                assert (await client.get(path, params=params)).status_code == 401
                if stop:
                    assert (await client.post(path + "/stop", params=params, headers=HEADERS)).json()["stopRequested"]
                    await asyncio.wait_for(closed.wait(), 5)
                else:
                    release.set()
                body = (await asyncio.wait_for(running, 5)).json()
                assert body["status"] == ("unknown" if stop else "completed")
                assert body["raw"] == ("早🌱" if stop else "早🌱!")
                assert body["receipt"]["dispatched"] and body["receipt"]["retries"] == 0
                assert (await client.post(PATH, headers=HEADERS,json=value)).json() == body
                final = (await client.get(path, params=params, headers=HEADERS)).json()
                assert final["status"] == body["status"] and final["text"] == body["raw"]
                assert "hidden" not in json.dumps(final)
                assert closed.is_set() and count == 1
            finally:
                release.set()
                await running
    asyncio.run(exercise())


def test_secrets_tools_and_restart_partial_never_enter_display(tmp_path):
    def packet(delta):
        return ("data: "+json.dumps({"choices":[{"delta":delta}]})+"\n\n").encode()
    path = tmp_path / "request.result.json"
    ch = live.Channel(path)
    ch.begin("neutral", ("secret-credential",))
    ch.feed(packet({"content":"ok secret-"}))
    assert path.with_suffix(".visible").read_text() == "ok "
    ch.feed(packet({"content":"credential"}))
    ch.feed(packet({"content":"later"}))
    assert path.with_suffix(".visible").read_text() == "ok "
    ch = live.Channel(tmp_path/"other.result.json")
    ch.begin("neutral", ())
    ch.feed(packet({"tool_calls":[{"id":"hidden"}],"content":"discard"}))
    assert not ch.path.with_suffix(".visible").exists()


def test_proposal_message_contract_never_relaxes_old_stories(setup, monkeypatch):
    client, _, _, _, captured, _ = setup
    value = task(setup)
    enabled(monkeypatch)
    value["messageContract"] = live.PROPOSAL
    assert client.post(PATH, headers=HEADERS, json=value).status_code == 422
    value["purpose"] = "memory"
    assert client.post(PATH, headers=HEADERS,json=value).json()["status"] == "completed"
    value["messageContract"] = "modelmirror.rpg.character-messages/1"
    assert client.post(PATH, headers=HEADERS,json=value).status_code == 422
    assert len(captured) == 1

def test_node_desk_follows_and_stops_actual_gateway_stream(setup, monkeypatch, tmp_path):
    import socket
    import subprocess
    import threading
    from pathlib import Path

    import uvicorn
    from server.tests.test_rpg_bridge import TOKEN, _declared_parameters

    client, stable, _, metadata, _, _ = setup
    _declared_parameters(metadata)
    enabled(monkeypatch)
    closed = threading.Event()
    calls = []

    class Waiting(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield 'data: {"choices":[{"delta":{"content":"可见片段🌱"}}]}\n\n'.encode()
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    def upstream(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, stream=Waiting(), headers={"content-type": "text/event-stream"})

    monkeypatch.setattr(stable.transport, "client_kwargs", lambda: {"transport": httpx.MockTransport(upstream)})
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        server = uvicorn.Server(uvicorn.Config(client.app, lifespan="off", log_level="error"))
        worker = threading.Thread(target=server.run, kwargs={"sockets":[sock]}, daemon=True)
        worker.start()
        try:
            origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
            cwd = Path(__file__).resolve().parents[2]/"experiments/ai-rpg-engine"
            child = subprocess.run(["node","character/live-memory/gateway-acceptance.mjs",str(tmp_path),origin,TOKEN],
                                   cwd=cwd, capture_output=True, text=True, encoding="utf8", timeout=60)
            assert child.returncode == 0, child.stdout + child.stderr
            assert closed.is_set() and len(calls) == 1
        finally:
            server.should_exit = True
            worker.join(timeout=5)
