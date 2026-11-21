"""Single task, loopback bridge. Shared router is opened read-only by the existing snapshot query."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import httpx
from fastapi.responses import JSONResponse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
sys.path[:0] = [str(ROOT), str(HERE.parent/'real')]
from bridge import serve, load_protocol
from credential import module as dpapi

class Experiment:
    root = Path('C:/tmp/mm-r10-live').resolve()
    def __init__(self):
        self.outbound = 0
        self.accepted = 0

    def bind_task_credential(self, repo):
        if not hasattr(self,'selected_envelope'):
            return
        from cryptography.fernet import InvalidToken
        local_cipher=repo._fernet
        original,wrapped=self.selected_envelope
        class SelectedOnly:
            def decrypt(self,ciphertext):
                if ciphertext!=original: raise InvalidToken
                return local_cipher.decrypt(wrapped)
        repo._fernet=SelectedOnly()

    def save_auth(self, run, data):
        (run/'local-auth.bin').write_bytes(dpapi.transform(json.dumps(data).encode(), True))

    def register(self, app, stable, run, mode):
        os.environ['RPG_ASSEMBLY_REAL_ENABLED']='true'
        world=load_protocol('r10_world',HERE.parents[1]/'assembly/real/protocol.py')
        self.gift_intent = run.name in ('real-gift-policy-02', 'mock-gift-policy-02')
        self.single = self.gift_intent or run.name in ('real-gift-policy-01', 'mock-gift-policy-01')
        if '-resume-' in run.name or self.single:
            parent=(self.root/'real-01'/'ledger.json').read_bytes()
            assert hashlib.sha256(parent).hexdigest()=='b503956fa2950b1386aab93df1edf9c070be9c3c960cc05c3af8e18a3921bf99'
            task=next(iter(json.loads(parent)['tasks'].values()))
            saved=task['stages'][1]
            assert not saved['attempts'] and saved['result']['dispatched'] is False
            if self.single:
                saved['request']['messages'][0]['content']='use_medicine 仅供玩家本人服药。只有玩家明确自服且药名、数量相符时才可准备该提案。赠药、他人服药和否定自服只作交流，有歧义先澄清。\n'+saved['request']['messages'][0]['content']
                if self.gift_intent:
                    prefix, remainder=saved['request']['messages'][0]['content'].split('\n',1)
                    extra='赠药意图只写打算，不能写成交付、服药或药效；没有赠药行动回执时保持关系、信任、印象和最近接触原值，赠药回合返回空增量。'
                    saved['request']['messages'][0]['content']=prefix+extra+'\n'+remainder
            original_wire=world.wire
            def scoped_wire(data):
                if data['kind']=='request' and self.accepted==1:
                    if data['request']!=saved['request']: raise ValueError('r10_resume_binding')
                    return {'ok':True}
                return original_wire(data)
            world.wire=scoped_wire
        world.register(app,stable)
        scene=load_protocol('r10_scene',HERE.parent/'real/protocol.py')
        class Observation(scene.Protocol):
            @staticmethod
            def validate_wire(data):
                p=subprocess.run(['node',str(HERE/'wire.mjs')],input=json.dumps(data),capture_output=True,
                                 encoding='utf-8',timeout=30)
                if p.returncode: raise ValueError('r10_observation_wire')
                return json.loads(p.stdout)
        scene.register(app,stable,Observation())
        @app.middleware('http')
        async def scope(request, call_next):
            if request.method=='POST' and 'completions' in request.url.path:
                n=self.accepted+1
                route='assembly' if n<=3 else 'scene'
                profile='god-long-v1' if n==3 else 'core-query-r3'
                if n>(1 if self.single else 5) or request.url.path!=f'/api/rpg/v1/{route}/chat/completions' or request.query_params.get('profile')!=profile:
                    return JSONResponse({'error':'r10_slot_closed'},status_code=409)
                self.accepted=n
            return await call_next(request)

    async def mock_response(self, request):
        self.outbound+=1
        n=self.outbound
        p=json.loads(request.content)
        assert n==self.accepted and n<=5
        assert p['model']=='google/gemini-3.8-flash' and p['provider']['only']==['google-ai-studio']
        assert not p['provider']['allow_fallbacks'] and p['max_tokens']==(32768 if n==3 else 8192)
        reply={1:'你说明丹药是准备送给周禾的，仍将它留在囊中，等待确认。\n<state_delta>{"operations":[]}</state_delta>',
               3:'你亲自服下两份养息丹，收好剩下的三份。',5:'登记显示，养息丹还剩三份。'}
        if self.gift_intent:
            delta={'content':'周禾接过丹药服下。\n<state_delta>{"operations":[]}</state_delta>'};finish='stop'
        elif self.single or n in (2,4):
            name,args=('prepare_action',{'actionId':'use_medicine','itemId':'medicine','quantity':2}) if self.single or n==2 else ('query_scene_state',{'domain':'resources'})
            call={'id':f'call{n}','index':0,'type':'function','function':{'name':name,'arguments':json.dumps(args)},'extra_content':{'google':{'thought_signature':'mock-signature'}}}
            delta={'tool_calls':[call]};finish='tool_calls'
        else:
            delta={'content':reply[n]};finish='stop'
        if n==5: assert p['messages'][-2]['tool_calls'][0]['extra_content']['google']['thought_signature']=='mock-signature'
        frames=[{'model':p['model'],'choices':[{'delta':delta,'finish_reason':finish}]},
                {'choices':[],'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,'cost':.00015}}]
        body=''.join('data: '+json.dumps(f,ensure_ascii=False)+'\n\n' for f in frames)+'data: [DONE]\n\n'
        return httpx.Response(200,content=body.encode(),headers={'content-type':'text/event-stream'})

if __name__=='__main__':
    mode,folder=sys.argv[1:3];run=Path(folder).resolve();exp=Experiment()
    assert run.parent==exp.root and run.name.startswith(mode+'-')
    if sys.argv[3:]==['--read-token']:
        print(dpapi.transform((run/'local-auth.bin').read_bytes(),False).decode());sys.exit()
    assert mode in ('mock','real') and len(sys.argv)==3 and not run.exists(),'Run must be fresh'
    if mode=='real':
        assert '-resume-' not in run.name or run.name=='real-resume-01'
        marker='resume-mock-passed.json' if '-resume-' in run.name else 'mock-passed.json'
        approved=json.loads((exp.root/marker).read_text(encoding='utf-8'))
        if run.name=='real-gift-policy-02':
            approved=json.loads((exp.root/'gift-policy-mock-02.json').read_text(encoding='utf-8'))
            assert approved['passed'] and approved['calls']==1
        elif run.name=='real-gift-policy-01':
            approved=json.loads((exp.root/'gift-policy-mock.json').read_text(encoding='utf-8'))
            assert approved['passed'] and approved['calls']==1
        else: assert approved['passed'] and approved['calls']==5
    source=ast.parse((HERE.parents[1]/'actions/real/launch.py').read_text(encoding='utf-8'))
    query=next(ast.literal_eval(n.value) for n in source.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='REMOTE' for t in n.targets))
    # An unpinned catalog refresh may omit the optional tag. Constrain only the
    # captured in-memory row to the approved endpoint; preserve any conflicting binding.
    guard='assert json.loads(rows["provider_catalog_models"][0]["metadata_json"])["rpg_provider_tag"]=="google-ai-studio"'
    assert query.count(guard)==1
    query=query.replace(guard, """
assert len(rows['provider_catalog_models'])==1
catalog_row=rows['provider_catalog_models'][0]
assert catalog_row['connection_id']==conn and catalog_row['model_id']==model
metadata=json.loads(catalog_row['metadata_json'])
assert metadata.get('rpg_provider_tag') in (None,'google-ai-studio')
metadata['rpg_provider_tag']='google-ai-studio'
catalog_row['metadata_json']=json.dumps(metadata)
""")
    scoped=run.name in ('real-gift-policy-02','mock-gift-policy-02')
    if scoped:
        # Preserve the original ciphertext as the certified route identity. Export only
        # a task-wrapped copy of the one authorized credential; the shared key stays here.
        export='print(json.dumps('
        assert query.count(export)==1
        query=query.replace(export,'''
from cryptography.fernet import Fernet
selected=next(row for row in rows['router_connections'] if row['id']==conn)
original=selected['api_key_ciphertext']
task_key=Fernet.generate_key()
value=Fernet(SQLiteRouterRepository._normalize_key(key)).decrypt(original.encode()) if REAL_TEST else b'isolated-mock-only'
wrapped=Fernet(task_key).encrypt(value).decode()
del value
key=task_key
for row in rows['router_metadata']:
 if row['key']=='credential_master_key_fingerprint': row['value']=SQLiteRouterRepository._key_fingerprint(task_key)
print(json.dumps('''.replace('REAL_TEST',repr(mode=='real')))
        query=query.replace('{"schemas":schemas','{"selected_envelope":[original,wrapped],"schemas":schemas')
    p=subprocess.run(['docker','exec','-i','modelmirror-server','python','-'],input=query,capture_output=True,encoding='utf-8')
    if p.returncode:sys.exit('Selected route snapshot unavailable')
    snapshot=json.loads(p.stdout);del p
    if scoped: exp.selected_envelope=tuple(s.encode() for s in snapshot.pop('selected_envelope'))
    serve(run,mode,snapshot,18925 if mode=='mock' else 18926,experiment=exp)
