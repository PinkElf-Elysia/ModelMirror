"""Isolated ancient-card review. No production route or shared configuration changes."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parents[2]
ROOT = ENGINE.parents[1]
BASE = ENGINE / 'agent-core'
DATA = ENGINE / '.rpg03-work/ancient-map-gemini'
sys.path[:0] = [str(ROOT), str(BASE / 'scenes/real')]
# This module is named bridge too. Load the reviewed shared service by filename.
import importlib.util
spec = importlib.util.spec_from_file_location('frontier_base_bridge', BASE/'scenes/real/bridge.py')
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)
from credential import module as dpapi
from fastapi.responses import JSONResponse
import httpx

MODEL = 'google/gemini-3.8-flash'
PORT = 18987
class Review:
    root = DATA
    def save_auth(self, run, data):
        (run/'local-auth.bin').write_bytes(dpapi.transform(json.dumps(data).encode(), True))

    def bind_task_credential(self,repo):
        local=repo._fernet
        original,wrapped=self.envelope
        class Selected:
            def decrypt(self, ciphertext):
                if ciphertext!=original:
                    raise ValueError('unselected credential')
                return local.decrypt(wrapped)
        repo._fernet=Selected()

    def register(self,app,stable,run,mode):
        self.calls=0
        protocol=shared.load_protocol('frontier_protocol',BASE/'scenes/real/protocol.py')
        original=protocol.b.parameters_for
        # Lower only this private process's certified text/tool output limit.
        def parameters(model,profile='default'):
            p=original(model,profile)
            return {**p,'max_tokens':4096} if p and model==MODEL and profile=='core-query-r3' else p
        protocol.b.parameters_for=parameters
        class MapProtocol(protocol.Protocol):
            @staticmethod
            def validate_wire(data):
                result=subprocess.run(['node',str(HERE/'wire.mjs')],input=json.dumps(data),capture_output=True,encoding='utf-8',timeout=30)
                if result.returncode: raise ValueError('map_wire_invalid')
                return json.loads(result.stdout)
        protocol.register(app,stable,MapProtocol())
        @app.middleware('http')
        async def restrict(req,next_handler):
            if req.method=='POST' and req.url.path!='/task/stop':
                if req.url.path!='/api/rpg/v1/scene/chat/completions' or req.query_params.get('profile')!='core-query-r3' or self.calls>=6:
                    return JSONResponse({'error':'review_scope'},status_code=409)
                self.calls+=1
            return await next_handler(req)

    async def mock_response(self,req):
        p=json.loads(req.content)
        assert p['model']==MODEL and p['provider']['only']==['google-ai-studio'] and not p['provider']['allow_fallbacks'] and p['max_tokens']==4096
        assert req.headers['host']=='openrouter.ai'
        n=self.calls
        if n%2:
            delta={'tool_calls':[{'index':0,'id':'query-'+str(n),'type':'function','function':{'name':'query_map','arguments':'{"node":"宁远"}'},'extra_content':{'google':{'thought_signature':'mock-only'}}}]}
            finish='tool_calls'
        else:
            assert p['messages'][-2]['tool_calls'][0]['extra_content']['google']['thought_signature']=='mock-only'
            result=json.loads(p['messages'][-1]['content'])
            assert result['name']=='宁远'
            assert (len(result['evidence']),result['control'])=={2:(0,'明'),4:(1,'明'),6:(2,'后金')}[n]
            delta={'content':{2:'宁远旧登记属明，眼下情形未明。',4:'抵京关报记载欠饷鼓噪，失城未核；登记仍属明。',6:'京师核准接防回执并确认交割，本档宁远由明改登后金，其余三地如旧。'}[n]}
            finish='stop'
        frames=[{'model':MODEL,'choices':[{'delta':delta,'finish_reason':finish}]},{'choices':[],'usage':{'prompt_tokens':200,'completion_tokens':30,'total_tokens':230,'cost':0.00026}}]
        return httpx.Response(200,content=(''.join('data: '+json.dumps(f)+'\n\n' for f in frames)+'data: [DONE]\n\n').encode(),headers={'content-type':'text/event-stream'})

def snapshot(mode):
    tree=ast.parse((BASE/'actions/real/launch.py').read_text(encoding='utf-8'))
    query=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(getattr(t,'id',None)=='REMOTE' for t in n.targets))
    guard='assert json.loads(rows["provider_catalog_models"][0]["metadata_json"])["rpg_provider_tag"]=="google-ai-studio"'
    assert query.count(guard)==1 and query.count('print(json.dumps(')==1
    query=query[:query.index(guard)]+"""
from cryptography.fernet import Fernet
assert len(rows['provider_catalog_models'])==1
r=rows['provider_catalog_models'][0]
assert r['connection_id']==conn and r['model_id']==model
m=json.loads(r['metadata_json']); assert m.get('rpg_provider_tag') in (None,'google-ai-studio')
m['rpg_provider_tag']='google-ai-studio'; r['metadata_json']=json.dumps(m)
orig=next(row['api_key_ciphertext'] for row in rows['router_connections'] if row['id']==conn)
ephemeral=Fernet.generate_key()
value=Fernet(SQLiteRouterRepository._normalize_key(key)).decrypt(orig.encode()) if REAL_MODE else Fernet.generate_key()
wrapped=Fernet(ephemeral).encrypt(value); del value,key
for m in rows['router_metadata']:
 if m['key']=='credential_master_key_fingerprint':m['value']=SQLiteRouterRepository._key_fingerprint(ephemeral)
print(json.dumps(dict(envelope=[orig,wrapped.decode()],schemas=schemas,rows=rows,key=ephemeral.decode(),version=d.execute('pragma user_version').fetchone()[0])))
""".replace('REAL_MODE',repr(mode=='real'))
    p=subprocess.run(['docker','exec','-i','modelmirror-server','python','-'],input=query,encoding='utf-8',capture_output=True)
    if p.returncode:raise RuntimeError('selected route snapshot unavailable; no dispatch')
    return json.loads(p.stdout)

if __name__=='__main__':
    mode=sys.argv[1];assert mode in ('mock','real')
    run=DATA/mode
    # The common service requires a fresh mode-prefixed directory.
    run=DATA/(mode+'-01')
    if sys.argv[2:]==['--local-auth']:
        print(json.loads(dpapi.transform((run/'local-auth.bin').read_bytes(),False))['token']);sys.exit()
    assert not run.exists() and len(sys.argv)==2
    if mode=='real':
        result=json.loads((DATA/'mock-01/result.json').read_text(encoding='utf-8'))
        assert result['passed'] and len(result['calls'])==6
    # Only one local bridge can own this run; verify the port before capturing any credential.
    import socket
    with socket.socket() as test:test.bind(('127.0.0.1',PORT))
    experiment=Review();data=snapshot(mode);experiment.envelope=tuple(x.encode() for x in data.pop('envelope'))
    shared.serve(run,mode,data,PORT,experiment=experiment)
