"""Authorized local deployment. Secrets remain in memory and existing service files."""
import json, os, re, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
WORK=ROOT/'experiments/ai-rpg-engine/.rpg04-work/studio-deploy'
WORK.mkdir(parents=True,exist_ok=True)
def run(args, env=None):
 r=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',env=env)
 if r.returncode:raise RuntimeError('COMMAND_FAILED: '+args[0]+' '+args[1]+' (details suppressed to protect configuration)')
 return r.stdout
def inspect(names):return json.loads(run(['docker','inspect',*names]))
def envmap(item):return dict(x.split('=',1) for x in item['Config']['Env'])
items=inspect(run(['docker','ps','-aq','--filter','label=com.docker.compose.project=modelmirror']).split())
byname={x['Name'].lstrip('/'):x for x in items};server=byname['modelmirror-server']
planfile=WORK/'plan.json'
files=json.loads(planfile.read_text(encoding='utf-8'))['originalComposeFiles'] if planfile.exists() else server['Config']['Labels']['com.docker.compose.project.config_files'].split(',')
values={}
for item in items:
 for k,v in envmap(item).items():values.setdefault(k,v)
values.update(envmap(server));childenv=os.environ.copy()
for file in files:
 for name in re.findall(r'\$\{([A-Za-z_][A-Za-z0-9_]*)',Path(file).read_text(encoding='utf-8')):
  if name in values:childenv[name]=values[name]
args=['docker','compose','-p','modelmirror','--profile','*']
for f in files:args+=['-f',f]
config=json.loads(run(args+['config','--format','json'],childenv))
override={'services':{},'secrets':{'rpg_provider_env':{'file':os.environ.get('RPG_ENV_FILE',str(ROOT/'server/.env'))}}}
for service in ['server','client']:
 item=byname['modelmirror-'+service];mapping={}
 for k,v in envmap(item).items():
  alias='STUDIO_PRESERVE_'+service.upper()+'_'+k;childenv[alias]=v;mapping[k]='${'+alias+'}'
 if service=='client':mapping['RPG_TARGET']='http://rpg:18420'
 override['services'][service]={'image':'modelmirror-'+service+':studio-candidate','build':{'context':str(ROOT/service)},'environment':mapping}
override['services']['rpg']={'image':'modelmirror-rpg:studio-candidate','build':{'context':str(ROOT/'experiments/ai-rpg-engine'),'dockerfile':'studio/Dockerfile'}}
# Only variable references and non-secret paths are written.
ovfile=WORK/'preserve.override.json';ovfile.write_text(json.dumps(override,indent=2),encoding='utf-8')
childenv['RPG_ENV_FILE']=override['secrets']['rpg_provider_env']['file']
args+=['-f',str(ROOT/'docker-compose.rpg.yml'),'-f',str(ovfile)]
merged=json.loads(run(args+['config','--format','json'],childenv))
for service in ['server','client']:
 actual=envmap(byname['modelmirror-'+service]);proposed=merged['services'][service]['environment']
 drift=[k for k,v in actual.items() if proposed.get(k)!=v and not(service=='client' and k=='RPG_TARGET')]
 if drift:raise RuntimeError('ENVIRONMENT_DRIFT_KEYS:'+','.join(drift))
 def props(c):return {k:c.get(k) for k in ['volumes','networks','ports','secrets']}
 if props(config['services'][service])!=props(merged['services'][service]):raise RuntimeError('MOUNTS_NETWORK_PORT_DRIFT:'+service)
plan={'base':run(['git','-C',str(ROOT),'rev-parse','HEAD']).strip(),'originalComposeFiles':files,'services':['server','client','rpg'],'environment':'preserve exact existing values; client adds RPG_TARGET only','before':{s:{'image':byname['modelmirror-'+s]['Image'],'mounts':byname['modelmirror-'+s]['Mounts'],'networks':list(byname['modelmirror-'+s]['NetworkSettings']['Networks']),'environmentKeys':sorted(envmap(byname['modelmirror-'+s]))} for s in ['server','client']},'credentials':'existing file mounted read-only; never copied into image or repository'}
if not planfile.exists():planfile.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
original_plan=json.loads(planfile.read_text(encoding='utf-8'))
mode=sys.argv[1] if len(sys.argv)>1 else 'plan'
if mode=='plan':print(json.dumps({'config':'passed','services':plan['services'],'preservedEnvironmentKeys':{s:len(plan['before'][s]['environmentKeys']) for s in ['server','client']}}))
elif mode=='deploy':
 for service in ['server','client']:
  run(['docker','image','tag',original_plan['before'][service]['image'],'modelmirror-'+service+':pre-studio-20260917'])
 print(run(args+['up','-d','--no-build','--no-deps','rpg','server','client'],childenv))
 after={x['Name'].lstrip('/'):x for x in inspect(['modelmirror-server','modelmirror-client','modelmirror-rpg'])}
 checks={}
 for s in ['server','client']:
  before=byname['modelmirror-'+s];current=after['modelmirror-'+s]
  drift=[k for k,v in envmap(before).items() if envmap(current).get(k)!=v and not(s=='client' and k=='RPG_TARGET')]
  def source_path(value):
   value=value.replace('\\','/')
   if value.startswith('/run/desktop/mnt/host/'):
    value=value.removeprefix('/run/desktop/mnt/host/');value=value[0]+':'+value[1:]
   return value.casefold() if re.match(r'^[A-Za-z]:/',value) else value
  def mounts(x):return sorted((m['Type'],source_path(m['Source']),m['Destination'],m['RW']) for m in x['Mounts'])
  checks[s]={'envChangedKeys':drift,'mountsPreserved':mounts(before)==mounts(current),'networksPreserved':set(before['NetworkSettings']['Networks'])==set(current['NetworkSettings']['Networks']),'image':current['Image']}
 (WORK/('deployment-'+__import__('datetime').datetime.now().strftime('%Y%m%dT%H%M%S')+'.json')).write_text(json.dumps(checks,indent=2),encoding='utf-8');print(json.dumps(checks))
 if any(x['envChangedKeys'] or not x['mountsPreserved'] or not x['networksPreserved'] for x in checks.values()):raise RuntimeError('POST_DEPLOY_DRIFT')
else:raise RuntimeError('Use plan or deploy; rollback tags are preserved, never remove volumes.')
