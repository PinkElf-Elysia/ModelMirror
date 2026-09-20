// Only this supervisor handles inherited service configuration in memory. Never print it.
import {execFileSync,spawn} from 'node:child_process';
import {readFileSync,writeFileSync,openSync,mkdirSync,existsSync} from 'node:fs';
import {randomBytes} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../../../',import.meta.url));
const work=root+'experiments/ai-rpg-engine/.rpg04-work/history-window-b4';
const name='modelmirror-rpg-history-window-b4-control';
const docker=(args,options={})=>execFileSync('docker',args,{windowsHide:true,stdio:['pipe','pipe','pipe'],maxBuffer:64*1024*1024,...options});
const save=(file,v)=>writeFileSync(work+'/'+file,JSON.stringify(v,null,2)+'\n',{flag:'wx'});
const action=process.argv[2];mkdirSync(work,{recursive:true});
try {
 if(action==='start-control'){
  if(existsSync(work+'/owner.json'))throw Error('ALREADY_INITIALIZED');
  const source=JSON.parse(docker(['inspect','modelmirror-server']))[0];
  if(source.Image!=='sha256:e2e5c8a2c76a0d572bb7854e34d7d1380ac2d78baf911ab5abb3ac78e04e3857')throw Error('SOURCE_IMAGE_CHANGED');
  if(!source.Mounts.some(m=>m.Destination==='/app/model_router/storage'&&m.Name==='modelmirror-provider-router-data'))throw Error('SOURCE_MOUNT_CHANGED');
  const old=Object.fromEntries(source.Config.Env.map(e=>{const i=e.indexOf('=');return[e.slice(0,i),e.slice(i+1)];}));
  const env={...process.env};for(const k of ['MODEL_MIRROR_CREDENTIAL_MASTER_KEY','MODEL_ROUTER_CREDENTIAL_MASTER_KEY','MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY','MODEL_CONTROL_CHAT_CERTIFICATION_MAX_AGE_SECONDS'])if(k in old)env[k]=old[k];
  Object.assign(env,{RPG_S2S_TOKEN:randomBytes(48).toString('base64url'),RPG_S2S_ENABLED:'true',MODEL_CONTROL_CHAT_ENABLED:'true',MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED:'true',PYTHONDONTWRITEBYTECODE:'1',PYTHONPATH:'/candidate'});
  const script="import sqlite3,sys; s=sqlite3.connect('file:/app/model_router/storage/router.sqlite3?mode=ro',uri=True); d=sqlite3.connect(':memory:'); s.backup(d); s.close(); sys.stdout.buffer.write(('-- M1 B4 memory snapshot'+chr(10)+chr(10).join(d.iterdump())).encode('utf-8')); d.close()";
  const snapshot=docker(['exec','modelmirror-server','python','-B','-c',script]);
  if(!snapshot.toString('utf8',0,24).startsWith('-- M1 B4 memory snapshot'))throw Error('SNAPSHOT_FAILED');
  const args=['run','-i','--name',name,'--label','modelmirror.task=rpg-history-window-b4','--network','modelmirror-provider','--read-only','--tmpfs','/tmp:rw,nosuid,nodev','-p','127.0.0.1:18458:18458','--mount','type=volume,source=modelmirror-provider-router-data,target=/source-router,readonly','--mount','type=bind,source='+root+',target=/candidate,readonly','--mount','type=bind,source='+work+',target=/work','--workdir','/candidate'];
  for(const k of ['MODEL_MIRROR_CREDENTIAL_MASTER_KEY','MODEL_ROUTER_CREDENTIAL_MASTER_KEY','MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY','MODEL_CONTROL_CHAT_CERTIFICATION_MAX_AGE_SECONDS','RPG_S2S_TOKEN','RPG_S2S_ENABLED','MODEL_CONTROL_CHAT_ENABLED','MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED','PYTHONDONTWRITEBYTECODE','PYTHONPATH'])if(k in env)args.push('--env',k);
  args.push(source.Image,'python','-B','/candidate/experiments/ai-rpg-engine/tooling/history-window-b4-control.py');
  const log=openSync(work+'/control-private.log','a'),p=spawn('docker',args,{env,windowsHide:true,detached:true,stdio:['pipe',log,log]});p.stdin.end(snapshot);await new Promise((ok,no)=>{p.stdin.on('finish',ok);p.stdin.on('error',no);});p.unref();snapshot.fill(0);
  save('owner.json',{name,dockerPid:p.pid,sourceImage:source.Image,sourceReadOnly:true,credentialsWritten:false});
  console.log(JSON.stringify({started:true,port:18458,providerCalls:0}));
 } else {
  const owner=JSON.parse(readFileSync(work+'/owner.json')),instance=JSON.parse(docker(['inspect',name]))[0];
  if(instance.Name!=='/'+owner.name||instance.Config.Labels['modelmirror.task']!=='rpg-history-window-b4')throw Error('OWNER_MISMATCH');
  const envValues=Object.fromEntries(instance.Config.Env.map(e=>{const i=e.indexOf('=');return[e.slice(0,i),e.slice(i+1)];}));
  if(action==='start-host'){
   if(existsSync(work+'/node-owner.json'))throw Error('HOST_ALREADY_INITIALIZED');
   const log=openSync(work+'/host-private.log','a'),p=spawn(process.execPath,[root+'experiments/ai-rpg-engine/tooling/history-window-b4-host.mjs'],{cwd:root,env:{...process.env,RPG_S2S_TOKEN:envValues.RPG_S2S_TOKEN},windowsHide:true,detached:true,stdio:['ignore',log,log]});p.unref();save('node-owner.json',{pid:p.pid,ui:18461,backend:18459});console.log(JSON.stringify({started:true,ui:18461,providerCalls:0}));
  } else {
   if(!['prepare','status','certify'].includes(action))throw Error('UNSUPPORTED_ACTION');
   if(action==='certify')save('authentication-invocation.json',{at:new Date().toISOString(),limit:1,noRetry:true,budget:'M1 B4 total 4'});
   const endpoint=action==='certify'?'certify/gemini':action;
   const response=await fetch('http://127.0.0.1:18458/m1/'+endpoint,{method:action==='status'?'GET':'POST',headers:{Authorization:'Bearer '+envValues.RPG_S2S_TOKEN},signal:AbortSignal.timeout(85000)});
   const data=await response.json();const output=JSON.stringify(data);if(output.includes(envValues.RPG_S2S_TOKEN))throw Error('SENSITIVE_RESPONSE');
   save(action+'-'+Date.now()+'.json',{httpStatus:response.status,result:data});console.log(output);
  }
 }
} catch(e){console.error(JSON.stringify({error: /^[A-Z_]+$/.test(e.message)?e.message:e.name,noRetry:true,secretsPrinted:false}));process.exitCode=1;}
