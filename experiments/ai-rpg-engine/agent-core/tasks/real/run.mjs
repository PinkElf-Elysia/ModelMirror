import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';import {execFileSync} from 'node:child_process';import {fileURLToPath} from 'node:url';
import {openRuntime} from '../composition.mjs';import {TaskExecutor} from '../executor.mjs';import {hash,clone,need} from '../../contracts.mjs';import {runtimeManifest} from '../../manifest.mjs';import {totals} from '../policy.mjs';
import {RealTransport} from './transport.mjs';import {MODEL,INPUT,SLOTS,policyFor} from './contract.mjs';
const [mode,arg]=process.argv.slice(2),directory=resolve(arg||'.');need(['mock','real'].includes(mode)&&directory.startsWith(resolve('C:/tmp/mm-r7-live',mode+'-')),'R7_REAL_SCOPE');
const save=(n,v)=>writeFile(join(directory,n),typeof v==='string'?v:JSON.stringify(v,null,2)+'\n',{flag:'wx'});
const auth=JSON.parse(execFileSync('python',[fileURLToPath(new URL('./credential.py',import.meta.url)),'--read',directory],{encoding:'utf8',windowsHide:true}));
const url='http://127.0.0.1:'+({mock:18896,real:18897}[mode]),headers={Authorization:'Bearer '+auth.token};
let rt,tr,run,initial,expected,authorization,error=null,pairCompleted=false;const began=performance.now();
try{
 const preflight=JSON.parse(await readFile(join(directory,'preflight.json'),'utf8')),ready=JSON.parse(await readFile(join(directory,'bridge-ready.json'),'utf8'));need(ready.mode===mode,'R7_MODE');
 const selections={};for(const profile of ['god-long-v1','core-query-r3']){const r=await fetch(url+'/api/rpg/v1/models?profile='+profile,{headers,redirect:'error'});need(r.ok,'R7_CATALOG');selections[profile]=(await r.json()).models.find(m=>m.model===MODEL&&m.available);need(selections[profile],'R7_SELECTION');}
 rt=await openRuntime(join(directory,'runtime'),{offlinePreview:true,limits:{calls:3,microUSD:600000}});await rt.ledger.change(r=>{need(!Object.keys(r.tasks).length,'R7_NO_REPLAY');r.accountingMode=mode;});
 run=await rt.create({operationId:'r7-real-create',inventory:false});const w=rt.worlds.get('cultivation');initial=clone((await w.store.read(run.worldId)).state);await save('initial.json',initial);
 const startedAction=performance.now();await save('confirmation.json',{kind:'user-authorized-test-confirmation',operationId:'r7-use-once',action:{actionId:'use_medicine',itemId:'medicine',quantity:2},expected:{before:5,after:3}});
 await rt.bridge.worldAction(run.bridgeId,{operationId:'r7-use-once',revision:0});await rt.bridge.deliver(run.bridgeId);const localFeedbackMs=performance.now()-startedAction;
 const world=await w.store.read(run.worldId);expected=clone(initial);expected.items.find(i=>i.id==='medicine').quantity=3;need(hash(world.state)===hash(expected),'R7_EXECUTED_STATE');await save('executed-world.json',world);
 const ownerRun=await rt.run(run.id),operations={1:world.pending,2:'r7-witness',3:'r7-outsider'};
 const grants=Object.values(SLOTS).map(s=>({slot:s.id,purpose:s.purpose,taskId:'task-'+hash([run.id,s.purpose,operations[s.id]]).slice(0,40),session:s.id===1?run.worldId:run.bridgeId,inputHash:hash(s.id===1?world.operations[world.pending].input:INPUT)}));
 authorization={id:'r7-'+mode+'-'+directory.split(/[\\/]/).at(-1),maximumCalls:3,maximumUSD:.60,expiresAt:new Date(Date.now()+3600000).toISOString(),pricing:{inputUSD:Number(preflight.endpoint.pricing.prompt),outputUSD:Number(preflight.endpoint.pricing.completion),quoteId:hash(preflight.endpoint)},requests:grants};
 const manifest=await runtimeManifest([new URL('./run.mjs',import.meta.url)],[{url:new URL('./bridge.py',import.meta.url)},{url:new URL('./launch.py',import.meta.url)},{url:new URL('./credential.py',import.meta.url)}]);
 await save('execution-claim.json',{mode,model:MODEL,authorization,selections,manifest,input:INPUT,slots:SLOTS,retries:0,fallback:false,worldNarration:'real',localFeedbackMs});
 tr=await RealTransport.open({directory:join(directory,'transport'),url,token:auth.token,selections,authorization,mode});
 const worldProvider=new TaskExecutor(rt.ledger,tr,{controlledReal:true}),actorProvider=new TaskExecutor(rt.ledger,tr,{controlledReal:true});
 rt.worldProviders.set('cultivation',worldProvider);rt.actorProviders.set('cultivation',actorProvider);w.provider=worldProvider;rt.bridge.provider=actorProvider;
 rt.taskPolicyFor=({purpose,actorId})=>policyFor(SLOTS[purpose==='world-narration'?1:actorId==='witness'?2:3],authorization.pricing);
 rt.bridge.requestPolicy=request=>({...request,tools:[],tool_choice:'none'});
 console.log('Dispatching authorized parallel pair (slots 1 and 2); no retry.');
 await rt.exclusive(run.id,()=>Promise.allSettled([rt.task(ownerRun,{operationId:operations[1],purpose:'world-narration'}),rt.task(ownerRun,{operationId:operations[2],purpose:'actor-response',actorId:'witness',input:INPUT})]));
 await save('after-pair.json',await rt.view(run.id));await save('pair-transport.json',await tr.evidence());
 const pair=(await rt.view(run.id)).tasks;need(pair.length===2&&pair.every(t=>t.adoption==='completed'),'R7_PAIR_NOT_COMPLETE');need(hash((await w.store.read(run.worldId)).state)===hash(expected),'R7_PAIR_STATE');pairCompleted=true;
 console.log('PAIR_SAVED: inspect original replies before releasing slot 3.');
 if(mode==='real'){
  const until=Date.now()+15*60000;let decision;
  while(Date.now()<until){try{decision=JSON.parse(await readFile(join(directory,'continue-third.json'),'utf8'));break;}catch(e){if(e.code!=='ENOENT')throw e;}await new Promise(r=>setTimeout(r,500));}
  need(decision?.continueThird===true,'R7_STOP_BEFORE_THIRD');
 }
 await rt.respond(run.id,{operationId:operations[3],actorId:'outsider',input:INPUT});const v=await rt.view(run.id);need(v.tasks.at(-1).adoption==='completed','R7_BOUNDARY_NOT_COMPLETE');need(hash((await w.store.read(run.worldId)).state)===hash(expected),'R7_FINAL_STATE');
}catch(e){error=e.code||e.message;await save('execution-error.json',{code:error});console.log('Stopped without retry: '+error);}
finally{
 const data=tr?await tr.evidence():{mode,calls:[],maxConcurrent:0};await save('transport-evidence.json',data);
 for(const c of data.calls){await save('stage-'+c.slot+'.json',c);await save('reply-'+c.slot+'.txt',c.response?.body?.raw??c.result?.message?.content??'');}
 const ledger=rt?await rt.ledger.read():null;await save('task-ledger.json',ledger);
 let world=null;if(rt&&run){world=await rt.worlds.get('cultivation').store.read(run.worldId);await save('world-record.json',world);await save('bridge-record.json',await rt.bridge.read(run.bridgeId));}
 await save('review.json',{mode,model:MODEL,error,pairCompleted,limits:{calls:3,perCallUSD:[.30,.15,.15],totalUSD:.60,retries:0,fallback:false},actualCalls:data.calls.length,maxConcurrent:data.maxConcurrent,
  exactExpectedState:!!world&&hash(world.state)===hash(expected),actionEvents:world?.events.filter(e=>e.kind==='action').length??0,medicine:world?.state.items.find(i=>i.id==='medicine').quantity??null,
  totalElapsedMs:performance.now()-began,budget:ledger?totals(ledger):null,calls:data.calls.map(c=>({slot:c.slot,sentAt:c.sentAt,completedAt:c.completedAt,status:c.result?.status,usage:c.result?.usage,accounting:c.result?.accounting,timing:c.result?.timing}))});
 await tr?.close();await rt?.close();
 try{await fetch(url+'/task/stop',{method:'POST',headers,redirect:'error'});}catch{console.log('Bridge stop requires local cleanup check.');}
}
if(error)process.exitCode=1;
