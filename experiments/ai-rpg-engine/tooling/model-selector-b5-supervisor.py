"""Service-internal configuration transfer; never print/persist credential values.
Explicit commands: prepare/status/certify <approved label>. Each certification
requires an explicit command; no retries and no generation from this launcher.
"""
import json,os,secrets,socket,subprocess,sys,time,urllib.request,urllib.error
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];WORK=ROOT/'experiments/ai-rpg-engine/.rpg04-work/model-selector-b5';WORK.mkdir(exist_ok=True)
NAME='modelmirror-rpg-selector-b5-control'
def docker(args,env=None):
 p=subprocess.run(['docker',*args],capture_output=True,encoding='utf-8',env=env,creationflags=subprocess.CREATE_NO_WINDOW)
 if p.returncode:raise RuntimeError('DOCKER_'+args[0]+'_FAILED')
 return p.stdout.strip()
def memory_snapshot():
 code="import sqlite3,sys; s=sqlite3.connect('file:/app/model_router/storage/router.sqlite3?mode=ro',uri=True); d=sqlite3.connect(':memory:'); s.backup(d); s.close(); sys.stdout.buffer.write(('-- B5 memory snapshot'+chr(10)+chr(10).join(d.iterdump())).encode('utf-8')); d.close()"
 result=subprocess.run(['docker','exec','modelmirror-server','python','-B','-c',code],capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
 assert result.returncode==0 and result.stdout.startswith(b'-- B5 memory snapshot'),'SNAPSHOT_FAILED'
 return result.stdout

def main():
 for port in [18446,18447,18449]:
  with socket.socket() as sock:sock.bind(('127.0.0.1',port))
 source=json.loads(docker(['inspect','modelmirror-server']))[0]
 assert source['Image']=='sha256:e2e5c8a2c76a0d572bb7854e34d7d1380ac2d78baf911ab5abb3ac78e04e3857','SOURCE_IMAGE_CHANGED'
 mount=next(x for x in source['Mounts'] if x['Destination']=='/app/model_router/storage');assert mount['Name']=='modelmirror-provider-router-data'
 existing=dict(e.split('=',1) for e in source['Config']['Env'] if '=' in e)
 child=os.environ.copy()
 for k in ['MODEL_MIRROR_CREDENTIAL_MASTER_KEY','MODEL_ROUTER_CREDENTIAL_MASTER_KEY','MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY','MODEL_CONTROL_CHAT_CERTIFICATION_MAX_AGE_SECONDS']:
  if k in existing:child[k]=existing[k]
 child.update(RPG_S2S_TOKEN=secrets.token_urlsafe(48),RPG_S2S_ENABLED='true',MODEL_CONTROL_CHAT_ENABLED='true',MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED='true',PYTHONDONTWRITEBYTECODE='1',PYTHONPATH='/candidate')
 snapshot=memory_snapshot()
 args=['run','-i','--name',NAME,'--label','modelmirror.task=rpg-model-selector-b5','--network','modelmirror-provider','--read-only','--tmpfs','/tmp:rw,nosuid,nodev','-p','127.0.0.1:18446:18446','--mount','type=volume,source='+mount['Name']+',target=/source-router,readonly','--mount','type=bind,source='+str(ROOT)+',target=/candidate,readonly','--mount','type=bind,source='+str(WORK)+',target=/work','--workdir','/candidate']
 for k in ['MODEL_MIRROR_CREDENTIAL_MASTER_KEY','MODEL_ROUTER_CREDENTIAL_MASTER_KEY','MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY','MODEL_CONTROL_CHAT_CERTIFICATION_MAX_AGE_SECONDS','RPG_S2S_TOKEN','RPG_S2S_ENABLED','MODEL_CONTROL_CHAT_ENABLED','MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED','PYTHONDONTWRITEBYTECODE','PYTHONPATH']:
  if k in child:args+=['--env',k]
 args += [source['Image'],'python','-B','/candidate/experiments/ai-rpg-engine/tooling/model-selector-b5-control.py']
 with (WORK/'control-private.log').open('ab') as log:
  control=subprocess.Popen(['docker',*args],stdin=subprocess.PIPE,stdout=log,stderr=log,env=child,creationflags=subprocess.CREATE_NO_WINDOW)
  control.stdin.write(snapshot);control.stdin.close()
 del snapshot
 cid=json.loads(docker(['inspect',NAME]))[0]['Id']
 with (WORK/'host-private.log').open('ab') as log:node=subprocess.Popen(['node',str(ROOT/'experiments/ai-rpg-engine/tooling/model-selector-real-host.mjs')],cwd=ROOT,env=child,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
 (WORK/'owner.json').write_text(json.dumps({'container':NAME,'containerId':cid,'nodePid':node.pid,'supervisorPid':os.getpid(),'sourceImage':source['Image'],'sourceRouterReadOnly':True,'credentialsCopiedToFiles':False},indent=2))
 print(json.dumps({'started':True,'container':NAME,'nodePid':node.pid,'noAutomaticDispatch':True}),flush=True)
 def request(path):
  req=urllib.request.Request('http://127.0.0.1:18446'+path,headers={'Authorization':'Bearer '+child['RPG_S2S_TOKEN']},method='GET' if path=='/b5/status' else 'POST')
  try:
   with urllib.request.urlopen(req,timeout=85) as resp:body=json.load(resp)
   text=json.dumps(body,ensure_ascii=True)
   assert child['RPG_S2S_TOKEN'] not in text
   print(text,flush=True)
  except urllib.error.HTTPError as e:print(json.dumps({'httpStatus':e.code,'body':e.read(300).decode('utf-8','replace')}),flush=True)
  except Exception as e:print(json.dumps({'errorType':type(e).__name__,'noRetry':True}),flush=True)
 for line in sys.stdin:
  command=line.strip()
  if command in ['prepare','status']:request('/b5/'+command)
  elif command in ['certify luna','certify gemini','certify deepseek']:request('/b5/certify/'+command.split()[1])
  else:print('{"error":"UNSUPPORTED_COMMAND"}',flush=True)
def command():
 action=' '.join(sys.argv[1:]);assert action in ['recover','prepare','status','certify luna','certify gemini','certify deepseek']
 owner=json.loads((WORK/'owner.json').read_text());instance=json.loads(docker(['inspect',NAME]))[0]
 assert instance['Id']==owner['containerId'] and instance['Config']['Labels'].get('modelmirror.task')=='rpg-model-selector-b5'
 values=dict(e.split('=',1) for e in instance['Config']['Env'] if '=' in e)
 if action=='recover':
  assert instance['State']['Status']=='exited' and not list(WORK.glob('authentication-slot-*.json'))
  # SQLite backup runs read-only in its original service namespace so its existing
  # WAL shared-memory file is accessible; only an in-memory snapshot crosses stdin.
  snapshot=memory_snapshot()
  (WORK/'failed-start.txt').write_text(docker(['logs',NAME]),encoding='utf-8')
  docker(['rm',NAME])
  child=os.environ.copy();child.update(values)
  args=['docker','run','-i','--name',NAME,'--label','modelmirror.task=rpg-model-selector-b5','--network','modelmirror-provider','--read-only','--tmpfs','/tmp:rw,nosuid,nodev','-p','127.0.0.1:18446:18446','--mount','type=volume,source=modelmirror-provider-router-data,target=/source-router,readonly','--mount','type=bind,source='+str(ROOT)+',target=/candidate,readonly','--mount','type=bind,source='+str(WORK)+',target=/work','--workdir','/candidate']
  for key in values:args+=['--env',key]
  args += [instance['Image'],'python','-B','/candidate/experiments/ai-rpg-engine/tooling/model-selector-b5-control.py']
  with (WORK/'control-private.log').open('ab') as log:
   process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=log,stderr=log,env=child,creationflags=subprocess.CREATE_NO_WINDOW)
   process.stdin.write(snapshot);process.stdin.close()
  # Docker container creation may finish just after the pipe write.
  for attempt in range(20):
   try:current=json.loads(docker(['inspect',NAME]))[0];break
   except RuntimeError:time.sleep(.1)
  owner['containerId']=current['Id'];owner['controlDockerPid']=process.pid;owner['snapshot']='read-only source backup to memory; stdin only';(WORK/'owner.json').write_text(json.dumps(owner,indent=2))
  print('{"recovered":true,"providerCalls":0}',flush=True);return
 path='/b5/certify/'+action.split()[1] if action.startswith('certify ') else '/b5/'+action
 req=urllib.request.Request('http://127.0.0.1:18446'+path,headers={'Authorization':'Bearer '+values['RPG_S2S_TOKEN']},method='GET' if action=='status' else 'POST')
 try:
  with urllib.request.urlopen(req,timeout=85) as resp: result=json.load(resp)
  print(json.dumps(result,ensure_ascii=True),flush=True)
 except urllib.error.HTTPError as e:print(json.dumps({'httpStatus':e.code,'body':e.read(300).decode('utf-8','replace')}),flush=True)
 except Exception as e:print(json.dumps({'errorType':type(e).__name__,'noRetry':True}),flush=True)
if __name__=='__main__':
 try:command() if len(sys.argv)>1 else main()
 except Exception as e:print(json.dumps({'errorType':type(e).__name__,'code':str(e) if str(e).isupper() else 'START_FAILED','secretsPrinted':False}),flush=True);sys.exit(1)
