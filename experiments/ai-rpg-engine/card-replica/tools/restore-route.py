"""Restore only the explicitly approved isolated RPG route. Never print config values."""
from pathlib import Path
import hashlib,json,os,socket,subprocess,sys,time
BASE=Path(__file__).resolve().parents[1]
WORK=BASE/'.local/route-recovery-20260917'
ORIGINAL=Path(r'C:\tmp\modelmirror-ai-rpg-rpg05\experiments\ai-rpg-engine\.rpg04-work\rpg05-structured14-20260912')
CONFIG=Path(r'C:\tmp\modelmirror-ai-rpg-rpg04\experiments\ai-rpg-engine\.rpg04-work\f-real-20260909-01\controlled-launch-environment.json')
NAME='modelmirror-ai-rpg-rpg04-newapi'
EXPECTED='a99065ea116f383038e9ce62c807df0f143c55ac0f3d43e70bd579073e414615'
def docker(*args):
 p=subprocess.run(['docker',*args],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
 if p.returncode: raise RuntimeError('ISOLATED_DOCKER_OPERATION_FAILED')
 return p.stdout.strip()
def main():
 for port in (18305,):
  with socket.socket() as s:s.bind(('127.0.0.1',port))
 assert docker('inspect',NAME,'--format','{{.Id}}')==EXPECTED
 assert docker('inspect',NAME,'--format','{{index .Config.Labels "modelmirror.task"}}')=='ai-rpg-rpg04'
 assert docker('inspect',NAME,'--format','{{.Config.Image}}')=='sha256:d600f20c2781e1a173c2a02f8c33b0c4b1b4e8e5a8b107bafaf2442ae2c9386c'
 manifest=json.loads((ORIGINAL/'candidate-source.json').read_text())
 assert manifest['candidateTreeSha256']=='e47aa6e91cbf88c5ff97c19395d40df176082044a7da866484178615c1f7b435'
 WORK.mkdir(parents=True,exist_ok=True)
 target=WORK/'code'
 for row in manifest['files']:
  rel=Path(row['path'])
  assert rel.parts[0]=='server' and '..' not in rel.parts and not any(x in ('storage','uploads','__pycache__') or x.startswith('.env') for x in rel.parts)
  p=ORIGINAL/'candidate-code'/rel
  assert not p.is_symlink()
  data=p.read_bytes()
  assert hashlib.sha256(data).hexdigest()==row['sha256']
  q=target/rel;q.parent.mkdir(parents=True,exist_ok=True)
  if q.exists():assert q.read_bytes()==data
  else:q.write_bytes(data)
 # User explicitly permits service-internal loading of existing configuration.
 # No credential is printed, copied to evidence, or passed in argv.
 env=json.loads(CONFIG.read_text())
 env['RPG05_STRUCTURED_OUTPUT_ENABLED']='false'
 env['PYTHONDONTWRITEBYTECODE']='1'
 docker('start',NAME)
 with (WORK/('service-private-'+str(time.time_ns())+'.log')).open('x',encoding='utf-8') as log:
  process=subprocess.Popen([sys.executable,'-B','-m','uvicorn','server.main:app','--host','127.0.0.1','--port','18305','--lifespan','off','--no-access-log'],cwd=target,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
 (WORK/'owner.json').write_text(json.dumps({'routePid':process.pid,'launcherPid':os.getpid(),'containerId':EXPECTED,'sourceTreeSha256':manifest['candidateTreeSha256'],'sourceFiles':len(manifest['files']),'route':'http://127.0.0.1:18305','providerDispatchesAtLaunch':0,'oldSourceAndEvidenceWritten':False},indent=2),encoding='utf-8')
 print(json.dumps({'routePid':process.pid,'providerDispatches':0}),flush=True)
if __name__=='__main__':
 try:main()
 except Exception as e:
  print(json.dumps({'status':'restore_failed','errorType':type(e).__name__,'credentialsPrinted':False}),flush=True)
  sys.exit(1)
