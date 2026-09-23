// Service-internal configuration transfer. No credential output or persistence.
import {execFileSync,spawn} from 'node:child_process';
import {readFileSync,writeFileSync,openSync,existsSync} from 'node:fs';
import {randomBytes} from 'node:crypto';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../../../',import.meta.url)),work=root+'experiments/ai-rpg-engine/.rpg04-work/memory-palace-b5';
const name='modelmirror-rpg-memory-palace-b5-control',image='sha256:e56718c59edc0ae4b77501384165711a038f169267f503f978151c158fef7d79';
const docker=(args,options={})=>execFileSync('docker',args,{windowsHide:true,stdio:['pipe','pipe','pipe'],maxBuffer:64*1024*1024,...options});
const save=(file,x)=>writeFileSync(work+'/'+file,JSON.stringify(x,null,2)+'\n',{flag:'wx'});
try{
 const a=JSON.parse(readFileSync(work+'/AUTHORIZATION.json'));if(a.id!=='rpg-memory-palace-b5-20260922'||a.generationLimit!==4||a.authenticationLimit!==1||!a.noRetry)throw Error('AUTHORIZATION_MISMATCH');
 const action=process.argv[2];
 if(action==='start-control'){
  if(existsSync(work+'/owner.json'))throw Error('ALREADY_INITIALIZED');
  const source=JSON.parse(docker(['inspect','modelmirror-server']))[0];
  if(source.Image!==image||!source.State.Running||!source.Mounts.some(m=>m.Destination==='/app/model_router/storage'&&m.Name==='modelmirror-provider-router-data'))throw Error('SOURCE_CHANGED');
  const values=Object.fromEntries(source.Config.Env.map(e=>{const i=e.indexOf('=');return[e.slice(0,i),e.slice(i+1)];})),env={...process.env};
  const keys=['MODEL_MIRROR_CREDENTIAL_MASTER_KEY','MODEL_ROUTER_CREDENTIAL_MASTER_KEY','MODEL_MIRROR_REQUIRE_EXTERNAL_CREDENTIAL_MASTER_KEY','MODEL_CONTROL_CHAT_CERTIFICATION_MAX_AGE_SECONDS'];for(const k of keys)if(k in values)env[k]=values[k];
  Object.assign(env,{RPG_S2S_TOKEN:randomBytes(48).toString('base64url'),RPG_S2S_ENABLED:'true',MODEL_CONTROL_CHAT_ENABLED:'true',MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED:'true',PYTHONDONTWRITEBYTECODE:'1',PYTHONPATH:'/candidate'});
  const script="import sqlite3,sys; s=sqlite3.connect('file:/app/model_router/storage/router.sqlite3?mode=ro',uri=True); d=sqlite3.connect(':memory:'); s.backup(d); s.close(); sys.stdout.buffer.write(('-- M3 B5 memory snapshot'+chr(10)+chr(10).join(d.iterdump())).encode('utf-8')); d.close()";
  const snapshot=docker(['exec','modelmirror-server','python','-B','-c',script]);if(!snapshot.toString('utf8',0,24).startsWith('-- M3 B5 memory snapshot'))throw Error('SNAPSHOT_FAILED');
  const args=['run','-i','--name',name,'--label','modelmirror.task=rpg-memory-palace-b5','--network','modelmirror-provider','--read-only','--tmpfs','/tmp:rw,nosuid,nodev','-p','127.0.0.1:18493:18493','--mount','type=volume,source=modelmirror-provider-router-data,target=/source-router,readonly','--mount','type=bind,source='+root+',target=/candidate,readonly','--mount','type=bind,source='+work+',target=/work','--workdir','/candidate'];
  for(const k of [...keys,'RPG_S2S_TOKEN','RPG_S2S_ENABLED','MODEL_CONTROL_CHAT_ENABLED','MODEL_MIRROR_PROVIDER_CHAT_CERTIFICATION_ENABLED','PYTHONDONTWRITEBYTECODE','PYTHONPATH'])if(k in env)args.push('--env',k);
  args.push(image,'python','-B','/candidate/experiments/ai-rpg-engine/tooling/memory-palace-b5-control.py');
  const log=openSync(work+'/control-private.log','a'),p=spawn('docker',args,{env,windowsHide:true,detached:true,stdio:['pipe',log,log]});p.stdin.end(snapshot);await new Promise((ok,no)=>{p.stdin.on('finish',ok);p.stdin.on('error',no);});p.unref();snapshot.fill(0);
  save('owner.json',{name,dockerPid:p.pid,sourceImage:image,sourceReadOnly:true,credentialsWritten:false});console.log(JSON.stringify({started:true,port:18493,providerCalls:0}));
 }else{
  const owner=JSON.parse(readFileSync(work+'/owner.json')),instance=JSON.parse(docker(['inspect',name]))[0];
  if(instance.Name!=='/'+owner.name||instance.Config.Labels['modelmirror.task']!=='rpg-memory-palace-b5'||!instance.State.Running)throw Error('CONTROL_OWNER_OR_STATE_INVALID');
  const values=Object.fromEntries(instance.Config.Env.map(e=>{const i=e.indexOf('=');return[e.slice(0,i),e.slice(i+1)];}));
  if(action==='start-host'){
   if(existsSync(work+'/node-owner.json'))throw Error('HOST_ALREADY_INITIALIZED');
   const log=openSync(work+'/host-private.log','a'),p=spawn(process.execPath,[root+'experiments/ai-rpg-engine/tooling/memory-palace-b5-host.mjs'],{cwd:root,env:{...process.env,RPG_S2S_TOKEN:values.RPG_S2S_TOKEN},windowsHide:true,detached:true,stdio:['ignore',log,log]});p.unref();save('node-owner.json',{pid:p.pid,ui:18497,backend:18495,limit:4});console.log(JSON.stringify({started:true,pid:p.pid,providerCalls:0}));
  }else{
   if(!['prepare','status','certify'].includes(action))throw Error('UNSUPPORTED_ACTION');
   if(action==='certify')save('authentication-invocation.json',{at:new Date().toISOString(),limit:1,noRetry:true,budget:a.id+'-authentication'});
   const response=await fetch('http://127.0.0.1:18493/m3/'+(action==='certify'?'certify/gemini':action),{method:action==='status'?'GET':'POST',headers:{Authorization:'Bearer '+values.RPG_S2S_TOKEN},signal:AbortSignal.timeout(85000)});
   const data=await response.json(),output=JSON.stringify(data);if(output.includes(values.RPG_S2S_TOKEN))throw Error('SENSITIVE_RESPONSE');
   save(action+'-'+Date.now()+'.json',{httpStatus:response.status,result:data});console.log(output);if(!response.ok)process.exitCode=1;
  }
 }
}catch(e){console.error(JSON.stringify({error:/^[A-Z_]+$/.test(e.message)?e.message:e.name,noRetry:true,secretsPrinted:false}));process.exitCode=1;}
