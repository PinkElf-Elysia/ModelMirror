import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';import {execFileSync} from 'node:child_process';import {fileURLToPath} from 'node:url';
import {openRuntime} from '../composition.mjs';import {TaskExecutor} from '../executor.mjs';import {hash,clone,need} from '../../contracts.mjs';import {runtimeManifest} from '../../manifest.mjs';import {totals} from '../policy.mjs';
import {RealTransport} from './transport.mjs';import {MODEL,INPUT,SLOTS,policyFor} from './contract.mjs';
const [mode,arg,bridgeArg]=process.argv.slice(2),directory=resolve(arg||'.'),bridgeDirectory=resolve(bridgeArg||'.');need(['mock','real'].includes(mode)&&directory.startsWith(resolve('C:/tmp/mm-r7-live',mode+'-')),'R7_REAL_SCOPE');
const save=(n,v)=>writeFile(join(directory,n),typeof v==='string'?v:JSON.stringify(v,null,2)+'\n',{flag:'wx'});
need(bridgeDirectory===resolve('C:/tmp/mm-r7-live',mode+'-resume-bridge'),'R7_RESUME_BRIDGE');
const auth=JSON.parse(execFileSync('python',[fileURLToPath(new URL('./credential.py',import.meta.url)),'--read',bridgeDirectory],{encoding:'utf8',windowsHide:true}));
const url='http://127.0.0.1:'+({mock:18896,real:18897}[mode]),headers={Authorization:'Bearer '+auth.token};
let rt,tr,run,initial,expected,authorization,error=null,pairCompleted=false;const began=performance.now();
try{
 const claim=JSON.parse(await readFile(join(directory,'execution-claim.json'),'utf8'));
 authorization=claim.authorization;need(Date.now()<Date.parse(authorization.expiresAt),'R7_AUTHORIZATION_EXPIRED');
 const decision=JSON.parse(await readFile(join(directory,'continue-third.json'),'utf8'));need(decision.continueThird===true,'R7_STOP_BEFORE_THIRD');
 const old=JSON.parse(await readFile(join(directory,'pair-transport.json'),'utf8'));
 need(old.calls.length===2&&old.calls.every(c=>[1,2].includes(c.slot)&&c.result.status==='completed'),'R7_PAIR_NOT_COMPLETE');
 const fresh=await fetch(url+'/api/rpg/v1/models?profile=core-query-r3',{headers,redirect:'error'});need(fresh.ok,'R7_CATALOG');
 const selection=(await fresh.json()).models.find(m=>m.model===MODEL&&m.available);
 need(selection?.selectionId===claim.selections['core-query-r3'].selectionId&&selection?.selectionRevision===claim.selections['core-query-r3'].selectionRevision,'R7_SELECTION_CHANGED');
 rt=await openRuntime(join(directory,'runtime'),{offlinePreview:true,limits:{calls:3,microUSD:600000}});
 const ledger=await rt.ledger.read(),tasks=Object.values(ledger.tasks);
 need(tasks.length===2&&tasks.every(t=>t.adoption==='completed'&&t.stages.length===1&&t.stages[0].attempts.length===1),'R7_RESUME_ONLY_UNSTARTED_THIRD');
 const runs=Object.values(ledger.runs);need(runs.length===1,'R7_RUN');run=await rt.view(runs[0].id);
 initial=JSON.parse(await readFile(join(directory,'initial.json'),'utf8'));expected=clone(initial);expected.items.find(i=>i.id==='medicine').quantity=3;
 const w=rt.worlds.get('cultivation');need(hash((await w.store.read(run.worldId)).state)===hash(expected),'R7_RESUME_STATE');
 tr=await RealTransport.open({directory:join(directory,'transport'),url,token:auth.token,selections:claim.selections,authorization,mode});
 need((await tr.evidence()).calls.length===2,'R7_THIRD_ALREADY_STARTED');
 await save('resume-claim.json',{mode,onlySlot:3,priorRequests:old.calls.map(c=>c.result.receipt.requestId),priorRepliesHash:hash(old),beforeCalls:totals(ledger).calls,startedAt:new Date().toISOString(),noReplay:true});
 const provider=new TaskExecutor(rt.ledger,tr,{controlledReal:true});rt.actorProviders.set('cultivation',provider);rt.bridge.provider=provider;
 rt.taskPolicyFor=()=>policyFor(SLOTS[3],authorization.pricing);rt.bridge.requestPolicy=request=>({...request,tools:[],tool_choice:'none'});
 pairCompleted=true;console.log('Dispatching only previously unstarted slot 3; no retry or replay.');
 await rt.respond(run.id,{operationId:'r7-outsider',actorId:'outsider',input:INPUT});
 const v=await rt.view(run.id);need(v.tasks.at(-1).adoption==='completed','R7_BOUNDARY_NOT_COMPLETE');need(hash((await w.store.read(run.worldId)).state)===hash(expected),'R7_FINAL_STATE');
}catch(e){error=e.code||e.message;await save('execution-error.json',{code:error});console.log('Stopped without retry: '+error);}
finally{
 const data=tr?await tr.evidence():{mode,calls:[],maxConcurrent:0};await save('transport-evidence.json',data);
 for(const c of data.calls){await save('stage-'+c.slot+'.json',c);await save('reply-'+c.slot+'.txt',c.response?.body?.raw??c.result?.message?.content??'');}
 const ledger=rt?await rt.ledger.read():null;await save('task-ledger.json',ledger);
 let world=null;if(rt&&run){world=await rt.worlds.get('cultivation').store.read(run.worldId);await save('world-record.json',world);await save('bridge-record.json',await rt.bridge.read(run.bridgeId));}
 await save('review.json',{mode,model:MODEL,error,pairCompleted,limits:{calls:3,perCallUSD:[.30,.15,.15],totalUSD:.60,retries:0,fallback:false},actualCalls:data.calls.length,maxConcurrent:Math.max(data.maxConcurrent,JSON.parse(await readFile(join(directory,'pair-transport.json'),'utf8')).maxConcurrent),
  exactExpectedState:!!world&&hash(world.state)===hash(expected),actionEvents:world?.events.filter(e=>e.kind==='action').length??0,medicine:world?.state.items.find(i=>i.id==='medicine').quantity??null,
  resumeElapsedMs:performance.now()-began,totalElapsedMs:null,budget:ledger?totals(ledger):null,calls:data.calls.map(c=>({slot:c.slot,sentAt:c.sentAt,completedAt:c.completedAt,status:c.result?.status,usage:c.result?.usage,accounting:c.result?.accounting,timing:c.result?.timing}))});
 await tr?.close();await rt?.close();
 try{await fetch(url+'/task/stop',{method:'POST',headers,redirect:'error'});}catch{console.log('Bridge stop requires local cleanup check.');}
}
if(error)process.exitCode=1;
