"""Group-only wire branch; no real providers or shared route state."""
import pytest
from server.tests.test_rpg_bridge import setup, HEADERS
from server.tests.test_rpg_authoring_execution import task, lookup, enabled

PATH = '/api/rpg/v1/authoring/tasks'
CONTRACT = 'modelmirror.rpg.character-group-messages/1'

@pytest.mark.parametrize('roles', [
    ('system', 'user'), ('system', 'user', 'system'),
    ('system', 'assistant', 'user', 'user', 'assistant', 'user'),
    ('system', 'assistant', 'assistant', 'user', 'system'),
])
def test_group_preserves_speakers_and_never_replays(setup, monkeypatch, roles):
    client, _, _, _, captured, _ = setup
    enabled(monkeypatch)
    value = {**task(setup), 'messageContract': CONTRACT,
             'messages': [{'role': role, 'content': f'成员引用 {i}'} for i, role in enumerate(roles)]}
    response = client.post(PATH, headers=HEADERS, json=value)
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'completed'
    assert captured[0]['messages'] == value['messages']
    assert 'messageContract' not in captured[0]
    assert lookup(client, value).json() == response.json()
    assert client.post(PATH, headers=HEADERS, json=value).json() == response.json()
    assert len(captured) == 1

@pytest.mark.parametrize('roles', [(), ('system',), ('user','user'),
    ('system','assistant'), ('system','tool','user'),
    ('system','system','user'), ('system','user','system','system')])
def test_invalid_group_roles_never_claim(setup, monkeypatch, roles):
    client, _, repo, _, captured, _ = setup
    enabled(monkeypatch)
    value = {**task(setup), 'messageContract': CONTRACT,
             'messages': [{'role': role, 'content': 'x'} for role in roles]}
    assert client.post(PATH, headers=HEADERS, json=value).status_code == 422
    assert not captured and not list(repo.storage_dir.glob('rpg-authoring-tasks/*'))

@pytest.mark.parametrize('mutation', ['memory','unknown','tool','content'])
def test_group_does_not_grant_tools_or_other_purposes(setup, monkeypatch, mutation):
    client, _, _, _, captured, _ = setup
    enabled(monkeypatch)
    value = {**task(setup), 'messageContract': CONTRACT}
    if mutation == 'memory': value['purpose'] = 'memory'
    if mutation == 'unknown': value['messageContract'] = 'unknown'
    if mutation == 'tool': value['messages'][1]['tool_calls'] = []
    if mutation == 'content': value['messages'][1]['content'] = {'text':'x'}
    assert client.post(PATH, headers=HEADERS, json=value).status_code == 422
    assert not captured

def test_actual_group_host_to_mock_gateway(setup, monkeypatch, tmp_path):
    import json
    import subprocess
    import threading
    from pathlib import Path
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from server.tests.test_rpg_bridge import TOKEN, _declared_parameters
    client, _, _, meta, captured, _ = setup
    _declared_parameters(meta); enabled(monkeypatch)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def forward(self):
            body = self.rfile.read(int(self.headers.get('content-length', '0')))
            reply = client.request(self.command, self.path, content=body,
                headers={k:v for k,v in self.headers.items() if k.lower() not in ('host','content-length')})
            self.send_response(reply.status_code)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(reply.content)))
            self.end_headers(); self.wfile.write(reply.content)
        do_GET = forward
        do_POST = forward
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker = threading.Thread(target=server.serve_forever,daemon=True); worker.start()
    try:
        settings={'origin':f'http://127.0.0.1:{server.server_port}','serviceToken':TOKEN,'directory':str(tmp_path/'group')}
        result=subprocess.run(['node','character/group/gateway-acceptance.mjs',json.dumps(settings)],
            cwd=Path(__file__).resolve().parents[2]/'experiments/ai-rpg-engine',capture_output=True,text=True,encoding='utf-8',timeout=90)
        assert result.returncode == 0, result.stdout+result.stderr
        assert json.loads(result.stdout)=={'messages':3,'calls':2,'recovered':True}
        assert len(captured)==2
        assert [x['role'] for x in captured[1]['messages']]==['system','user','user','user']
        assert 'EXCLUDED_AUTHOR' not in json.dumps(captured)
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=5)
