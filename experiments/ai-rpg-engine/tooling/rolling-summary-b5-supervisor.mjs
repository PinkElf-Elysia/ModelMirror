// Reuse a live qualified route; service token stays in memory. No control writes or certification.
import {execFileSync,spawn} from 'node:child_process';
import {readFileSync,writeFileSync,openSync,existsSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const root=fileURLToPath(new URL('../../../',import.meta.url));
const work=root+'experiments/ai-rpg-engine/.rpg04-work/rolling-summary-b5';
const name='modelmirror-rpg-history-window-b4-control';
const save=(name,v)=>writeFileSync(work+'/'+name,JSON.stringify(v,null,2)+'\n',{flag:'wx'});
try {
 const authorization=JSON.parse(readFileSync(work+'/AUTHORIZATION.json'));
 if(authorization.limit!==4||authorization.id!=='rpg-rolling-summary-b5-20260920')throw Error('AUTHORIZATION_MISMATCH');
 const instance=JSON.parse(execFileSync('docker',['inspect',name],{windowsHide:true,stdio:['ignore','pipe','pipe']}))[0];
 if(instance.Name!=='/'+name||instance.Config.Labels['modelmirror.task']!=='rpg-history-window-b4'||!instance.State.Running)throw Error('ROUTE_OWNER_MISMATCH');
 const entry=instance.Config.Env.find(e=>e.startsWith('RPG_S2S_TOKEN='));if(!entry)throw Error('SERVICE_TOKEN_MISSING');
 const token=entry.slice('RPG_S2S_TOKEN='.length);
 const action=process.argv[2];
 if(action==='status'){
  const response=await fetch('http://127.0.0.1:18458/m1/status',{headers:{Authorization:'Bearer '+token},signal:AbortSignal.timeout(15000)});
  const data=await response.json();const output=JSON.stringify(data);if(output.includes(token))throw Error('SENSITIVE_RESPONSE');
  save('control-status-'+Date.now()+'.json',{httpStatus:response.status,result:data,readOnly:true});
  console.log(output);
 }else if(action==='start-extension'){
  const a=JSON.parse(readFileSync(work+'/AUTHORIZATION-EXTENSION-1.json'));
  if(a.id!=='rpg-rolling-summary-b5-extension-1'||a.cumulativeLimit!==5||a.additionalLimit!==1||a.noStory!==true||a.purpose!=='summary')throw Error('EXTENSION_AUTHORIZATION_MISMATCH');
  if(existsSync(work+'/node-owner-extension.json'))throw Error('EXTENSION_ALREADY_STARTED');
  const old=JSON.parse(readFileSync(work+'/node-owner.json'));let alive=false;try{process.kill(old.pid,0);alive=true;}catch(e){if(e.code!=='ESRCH')throw e;}if(alive)throw Error('OLD_HOST_STILL_RUNNING');
  const log=openSync(work+'/host-extension-private.log','a');
  const p=spawn(process.execPath,[root+'experiments/ai-rpg-engine/tooling/rolling-summary-b5-host.mjs'],{cwd:root,env:{...process.env,RPG_S2S_TOKEN:token},windowsHide:true,detached:true,stdio:['ignore',log,log]});p.unref();
  save('node-owner-extension.json',{pid:p.pid,previousPid:old.pid,ui:18473,backend:18471,limit:5,previousDispatches:4,credentialsWritten:false});console.log(JSON.stringify({started:true,pid:p.pid,providerCalls:0}));
 }else if(action==='start-host'){
  if(existsSync(work+'/node-owner.json'))throw Error('HOST_ALREADY_INITIALIZED');
  const log=openSync(work+'/host-private.log','a');
  const p=spawn(process.execPath,[root+'experiments/ai-rpg-engine/tooling/rolling-summary-b5-host.mjs'],{cwd:root,env:{...process.env,RPG_S2S_TOKEN:token},windowsHide:true,detached:true,stdio:['ignore',log,log]});p.unref();
  save('node-owner.json',{pid:p.pid,ui:18473,backend:18471,control:name,limit:4,credentialsWritten:false,sharedServicesChanged:false});console.log(JSON.stringify({started:true,pid:p.pid,providerCalls:0}));
 }else throw Error('UNSUPPORTED_ACTION');
}catch(e){console.error(JSON.stringify({error:/^[A-Z_]+$/.test(e.message)?e.message:e.name,noRetry:true,secretsPrinted:false}));process.exitCode=1;}
