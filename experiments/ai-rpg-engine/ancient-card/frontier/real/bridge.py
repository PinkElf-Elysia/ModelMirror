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
NINGYUAN = '--ningyuan' in sys.argv
if NINGYUAN: sys.argv.remove('--ningyuan')
OPENING = '--opening' in sys.argv
if OPENING: sys.argv.remove('--opening')
ORDINARY = '--ordinary-map' in sys.argv
if ORDINARY: sys.argv.remove('--ordinary-map')
STORY = '--story-map' in sys.argv
if STORY: sys.argv.remove('--story-map')
REGISTER = '--place-register' in sys.argv
if REGISTER: sys.argv.remove('--place-register')
ORDINARY = ORDINARY or OPENING or NINGYUAN
STORY = STORY or ORDINARY
REGISTER = REGISTER or STORY
DATA = ENGINE / '.rpg03-work' / ('story-map-gemini' if STORY else 'place-register-gemini' if REGISTER else 'ancient-map-gemini')
if STORY and os.environ.get('STORY_MAP_RETEST')=='1': DATA=DATA.with_name('story-map-gemini-prose-retest')
if ORDINARY:
    DATA=DATA.with_name('ordinary-docket-gemini' if NINGYUAN else 'ordinary-opening-gemini' if OPENING else 'ordinary-map-gemini')
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
LIMIT = 8192 if ORDINARY else 4096
PORT = 19236 if NINGYUAN else 19226 if OPENING else 19221 if ORDINARY else 19193 if STORY else 19189 if REGISTER else 18987
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
            return {**p,'max_tokens':LIMIT} if p and model==MODEL and profile=='core-query-r3' else p
        protocol.b.parameters_for=parameters
        class MapProtocol(protocol.Protocol):
            @staticmethod
            def validate_wire(data):
                if REGISTER and data['kind']=='sse':
                    (run/f'provider-{self.calls}.sse').write_text(data['sse'],encoding='utf-8')
                result=subprocess.run(['node',str(HERE/('register-wire.mjs' if REGISTER else 'wire.mjs'))],input=json.dumps({**data,'story':True,'ordinary':ORDINARY,'opening':OPENING,'ningyuan':NINGYUAN} if STORY else data),capture_output=True,encoding='utf-8',timeout=30)
                if result.returncode: raise ValueError('map_wire_invalid')
                return json.loads(result.stdout)
        if REGISTER:
            original_kwargs=stable.transport.client_kwargs
            def capture_kwargs():
                kwargs=original_kwargs()
                async def capture(req):
                    if req.method=='POST' and req.url.path.endswith('/chat/completions'):
                        body=json.loads(req.content)
                        assert req.headers['host']=='openrouter.ai' and req.extensions['sni_hostname']=='openrouter.ai' and body['model']==MODEL
                        assert body['provider']['only']==['google-ai-studio'] and body['provider']['allow_fallbacks'] is False
                        assert body['max_tokens']==LIMIT and 1<=self.calls<=(1 if NINGYUAN or OPENING else 5)
                        if OPENING:
                            expected=json.loads((DATA/'browser-request.json').read_text(encoding='utf8'))
                            assert all(body.get(k)==v for k,v in expected.items())
                        with (run/f'upstream-{self.calls}.json').open('x',encoding='utf-8') as out:
                            json.dump(body,out,ensure_ascii=False,indent=2)
                return {**kwargs,'event_hooks':{'request':[capture]}}
            stable.transport.client_kwargs=capture_kwargs
        protocol.register(app,stable,MapProtocol())
        @app.middleware('http')
        async def restrict(req,next_handler):
            if req.method=='POST' and req.url.path!='/task/stop':
                if req.url.path!='/api/rpg/v1/scene/chat/completions' or req.query_params.get('profile')!='core-query-r3' or self.calls>=(1 if NINGYUAN or OPENING else 3 if STORY else 5 if REGISTER else 6):
                    return JSONResponse({'error':'review_scope'},status_code=409)
                self.calls+=1
            return await next_handler(req)

    async def mock_response(self,req):
        p=json.loads(req.content)
        assert p['model']==MODEL and p['provider']['only']==['google-ai-studio'] and not p['provider']['allow_fallbacks'] and p['max_tokens']==LIMIT
        assert req.headers['host']=='openrouter.ai'
        n=self.calls
        if NINGYUAN:
            assert n==1
            delta={'tool_calls':[{'index':0,'id':'mock-docket','type':'function','function':{'name':'query_map','arguments':json.dumps({'node':'宁远','use':'documents'})}}]}
            finish='tool_calls'
        elif STORY:
            if n==1: delta,finish={'content':'你合起大员交割文书，命值房照已准的一处入册。'},'stop'
            elif n==2: delta,finish={'tool_calls':[{'index':0,'id':'mock-query','type':'function','function':{'name':'query_map','arguments':json.dumps({'node':'台湾','use':'scene'})}}]},'tool_calls'
            else: delta,finish={'content':'大员凭交割文书改登为明，鸡笼沿旧登记为西班牙，岛内各社自治。'},'stop'
            if ORDINARY and n in (1,3):
                titles=['时间与环境','玩家状态','当前交互角色','财产状况','世界风云','关系网络','星记忆回廊','接下来']
                scene='清晨，王承恩在京师文华殿呈上未批的奏疏。' if OPENING else delta['content']
                delta={'content':scene+'你请阁臣接着议事。'*165+''.join('<details><summary>'+t+'</summary>沿用既有登记。</details>' for t in titles)}
            elif ORDINARY and n==2:
                delta['tool_calls'][0]['extra_content']={'google':{'thought_signature':'offline-fixture'}}
        elif REGISTER:
            delta,finish=register_mock(p,n)
        elif n%2:
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

def register_mock(p,n):
    result=json.loads(p['messages'][-1]['content']) if n in (3,5) else None
    if result:
        assert p['messages'][-2]['tool_calls'][0]['extra_content']['google']['thought_signature']=='mock-only'
    if n==5:
        assert [s['holder'] for s in result['entries']]==['明','西班牙','各社自治']
        return {'content':'御前核准大员接收文书，大员改登明，鸡笼仍登记西班牙，岛内各社自治。'},'stop'
    if n==3:
        receipt=next(f for f in result['entries'][0]['evidence'] if f['kind']=='receipt')
        name,args='propose_map_change',dict(node='大员',source=receipt['id'])
    else:
        name,args={1:('propose_place_order',dict(node='大员')),2:('query_map',dict(node='大员')),4:('query_map',dict(node='台湾'))}[n]
    return {'tool_calls':[{'index':0,'id':'map-'+str(n),'type':'function','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)},'extra_content':{'google':{'thought_signature':'mock-only'}}}]},'tool_calls'

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
    run=DATA/('mock-02' if REGISTER and mode=='mock' else mode+'-01')
    if sys.argv[2:]==['--local-auth']:
        print(json.loads(dpapi.transform((run/'local-auth.bin').read_bytes(),False))['token']);sys.exit()
    assert not run.exists() and len(sys.argv)==2
    if mode=='real':
        result=json.loads((DATA/('mock-02' if REGISTER else 'mock-01')/'result.json').read_text(encoding='utf-8'))
        assert result['passed'] and len(result['calls'])==(1 if NINGYUAN or OPENING else 3 if STORY else 5 if REGISTER else 6)
    # Only one local bridge can own this run; verify the port before capturing any credential.
    import socket
    with socket.socket() as test:test.bind(('127.0.0.1',PORT))
    experiment=Review();data=snapshot(mode);experiment.envelope=tuple(x.encode() for x in data.pop('envelope'))
    shared.serve(run,mode,data,PORT,experiment=experiment)
