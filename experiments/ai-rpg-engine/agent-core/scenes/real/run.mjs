import {readFile,writeFile} from 'node:fs/promises';import {resolve,join,dirname,basename} from 'node:path';import {execFileSync} from 'node:child_process';import {fileURLToPath} from 'node:url';
import {AuthoringStore} from '../../../authoring/store.mjs';import {openAssembly} from '../../assembly/runtime.mjs';import {SceneRuntime} from '../runtime.mjs';import {ensureParticipants} from '../participants.mjs';
import {hash,clone,need} from '../../contracts.mjs';import {runtimeManifest} from '../../manifest.mjs';import {totals} from '../../tasks/policy.mjs';
import {RealTransport} from './transport.mjs';import {MODEL,INPUTS,SLOTS,liveCatalog} from './contract.mjs';
const [mode,arg,phase]=process.argv.slice(2),directory=resolve(arg||'.');
need(['mock','real'].includes(mode)&&dirname(directory)===resolve('C:/tmp/mm-r9-live')&&basename(directory).startsWith(mode+'-')&&['setup','1','2','adopt-2','3','adopt-3','finish'].includes(phase),'R9_REAL_SCOPE');
const read=n=>readFile(join(directory,n),'utf8').then(JSON.parse),save=(n,v,exclusive=true)=>writeFile(join(directory,n),typeof v==='string'?v:JSON.stringify(v,null,2)+String.fromCharCode(10),exclusive?{flag:'wx'}:{});
const auth=JSON.parse(execFileSync('python',[fileURLToPath(new URL('./credential.py',import.meta.url)),'--read',directory],{encoding:'utf8',windowsHide:true}));
const url='http://127.0.0.1:'+({mock:18919,real:18920}[mode]),headers={Authorization:'Bearer '+auth.token};
let rt,tr,works,run,initial,expected,error=null,claim=null;const began=performance.now();
try{
 const preflight=await read('preflight.json'),ready=await read('bridge-ready.json');need(ready.mode===mode,'R9_MODE');
 const quote={inputUSD:Number(preflight.endpoint.pricing.prompt),outputUSD:Number(preflight.endpoint.pricing.completion),quoteId:hash(preflight.endpoint)};
 let selections={},authorization;
 if(phase==='setup'){
  for(const profile of ['god-long-v1','core-query-r3']){const r=await fetch(url+'/api/rpg/v1/models?profile='+profile,{headers,redirect:'error'});need(r.ok,'R9_CATALOG');selections[profile]=(await r.json()).models.find(m=>m.model===MODEL&&m.available);need(selections[profile],'R9_SELECTION');}
  authorization={id:'r9-'+mode+'-'+basename(directory),maximumCalls:3,maximumUSD:.30,expiresAt:new Date(Date.now()+7200000).toISOString(),pricing:quote,requests:[]};
 }else{claim=await read('execution-claim.json');need(claim.mode===mode&&hash(claim.authorization.pricing)===hash(quote),'R9_FROZEN_ROUTE');({selections,authorization,initial,expected}=claim);}
 const manifest=await runtimeManifest([new URL('./run.mjs',import.meta.url)],[...['bridge.py','launch.py','credential.py','protocol.py','wire.mjs'].map(n=>({url:new URL('./'+n,import.meta.url)})),{url:new URL('../../../package-lock.json',import.meta.url)}]);
 if(claim)need(manifest.hash===claim.manifest.hash,'R9_SOURCE_CHANGED');
 works=await AuthoringStore.open(join(directory,'templates'),{recoverDeadOwner:true});const participants=await ensureParticipants(works),catalog=await liveCatalog(quote,participants);
 const core=await openAssembly(join(directory,'runtime'),{catalog,controlledReal:true,manifestHash:manifest.hash,limits:{calls:3,microUSD:300000},transportFactory:async path=>tr=await RealTransport.open({directory:path,url,token:auth.token,selections,authorization,mode})});
 core.closers.unshift(works);rt=new SceneRuntime(core,participants);
 if(phase==='setup')await rt.ledger.change(r=>{need(!Object.keys(r.tasks).length,'R9_NO_REPLAY');r.accountingMode=mode;});
 run=await rt.create({operationId:'r9-live-create',adapterId:'lighthouse'});const p=rt.ports.get('lighthouse');
 if(phase==='setup'){
  initial=clone(run.world.state);expected=clone(initial);const resource=p.adapter.resources(initial)[0];expected.supplies.find(x=>x.id===resource.id).quantity=3;
  const operations={1:'narrate-'+hash([run.worldId,'r9-execute-once']).slice(0,32),2:'r9-keeper',3:'r9-visitor'},inputs={1:p.adapter.rule.label+' '+resource.name+' × 2',...INPUTS};
  authorization.requests=Object.values(SLOTS).map(s=>({slot:s.id,purpose:s.purpose,taskId:rt.taskId(run,s.purpose,operations[s.id]),session:run.worldId,inputHash:hash(inputs[s.id])}));
  claim={mode,model:MODEL,authorization,selections,manifest,slots:SLOTS,inputs,operations,initial,expected,runId:run.id,templates:participants.map(p=>({id:p.id,reference:p.reference,configurationHash:p.configurationHash})),retries:0,fallback:false};
  await save('execution-claim.json',claim);
  const proposed=await p.store.propose(run.worldId,{operationId:'r9-local-plan',revision:run.world.revision,action:{actionId:p.adapter.rule.id,itemId:resource.id,quantity:2}});
  need(hash(proposed.state)===hash(initial),'R9_PROPOSAL_READONLY');await save('before-confirm.json',proposed);
  const proposal=proposed.proposals['r9-local-plan'].proposal,confirmation={operationId:'r9-execute-once',revision:proposed.revision,proposalId:'r9-local-plan',proposalHash:proposal.hash};
  await save('confirmation.json',{kind:'user-authorized-local-test',...confirmation});
  run=await rt.confirm(run.id,confirmation);need(hash(run.world.state)===hash(expected)&&run.world.events.filter(e=>e.kind==='action').length===1,'R9_EXECUTION');
  await save('after-confirm.json',run);console.log('Setup complete: lamp oil 5 -> 3; provider requests 0.');
 }else if(/^[123]$/.test(phase)){
  const n=Number(phase),slot=SLOTS[n],taskId=authorization.requests.find(g=>g.slot===n).taskId;
  need(!(await rt.ledger.read()).tasks[taskId],'R9_SLOT_ALREADY_STARTED');
  if(n===2)need(run.tasks.some(t=>t.purpose==='world-narration'&&t.adoption==='completed'),'R9_NARRATION_REQUIRED');
  if(n===3)need(run.scene.entries.some(e=>e.taskId===authorization.requests.find(g=>g.slot===2).taskId),'R9_KEEPER_ADOPTION_REQUIRED');
  await save('slot-'+n+'-dispatch-claim.json',{slot:n,taskId,at:new Date().toISOString(),attempt:1});
  run=n===1?await rt.narrate(run.id,{strategy:'slot-1'}):await rt.submit(run.id,{operationId:claim.operations[n],ownerId:slot.purpose,input:claim.inputs[n],strategy:'slot-'+n});
  await save('after-slot-'+n+'.json',run);
  const task=run.tasks.find(t=>t.id===taskId);need(task?.adoption===(n===1?'completed':'awaiting-adoption')&&hash(run.world.state)===hash(expected),'R9_RESULT_REQUIRED');
  console.log(JSON.stringify({slot:n,status:task.adoption,body:task.body,recordedCalls:run.budget.calls}));
 }else if(phase.startsWith('adopt-')){
  const n=Number(phase.at(-1)),taskId=authorization.requests.find(g=>g.slot===n).taskId;
  const before=await rt.view(run.id);need(before.tasks.find(t=>t.id===taskId)?.adoption==='awaiting-adoption','R9_REVIEW_PENDING');
  await save('adoption-'+n+'.json',{taskId,kind:'authorized-test-script-after-operator-review',receiptHash:hash(before.tasks.find(t=>t.id===taskId).body),at:new Date().toISOString()});
  run=await rt.adopt(run.id,taskId);const revision=run.world.revision,calls=run.budget.calls;run=await rt.adopt(run.id,taskId);
  need(run.world.revision===revision&&run.budget.calls===calls&&hash(run.world.state)===hash(expected),'R9_ADOPTION_IDEMPOTENT');
  await save('after-adopt-'+n+'.json',run);console.log(JSON.stringify({adopted:n,entries:run.scene.entries.length,calls}));
 }else{
  need(run.scene.entries.length===2&&hash(run.world.state)===hash(expected),'R9_FINISH_REQUIRED');
  const w=await p.store.read(run.worldId),context=await p.adapter.assembleTask(w,'继续刚才的交谈。');await save('next-world-context-offline.json',{kind:'assembly-only-no-dispatch',messages:context.messages,hash:context.hash});
  await save('final-view.json',run);console.log('Finished: saved adopted dialogue and next-world context; no additional dispatch.');
 }
}catch(e){error=e.code||e.message;await save('error-'+phase+'.json',{code:error});console.log('Stopped without retry: '+error);}
finally{
 try{
  const data=tr?await tr.evidence():{mode,calls:[],maxConcurrent:0};await save('transport-evidence.json',data,false);
  for(const c of data.calls){await save('stage-'+c.slot+'.json',c,false);await save('reply-'+c.slot+'.txt',c.result?.message?.content??c.response?.body?.raw??'',false);}
  const ledger=rt?await rt.ledger.read():null;await save('task-ledger.json',ledger,false);let world=null;
  if(rt&&run){world=await rt.ports.get('lighthouse').store.read(run.worldId);await save('world-record.json',world,false);}
  await save('review.json',{mode,model:MODEL,phase,error,limits:{calls:3,perCallUSD:[.15,.10,.05],totalUSD:.30,retries:0,fallback:false},actualCalls:data.calls.length,
   exactExpectedState:!!world&&!!expected&&hash(world.state)===hash(expected),actionEvents:world?.events.filter(e=>e.kind==='action').length??0,oil:world?.state.supplies[0]?.quantity??null,
   phaseElapsedMs:performance.now()-began,budget:ledger?totals(ledger):null,calls:data.calls.map(c=>({slot:c.slot,status:c.result?.status,usage:c.result?.usage,accounting:c.result?.accounting,timing:c.result?.timing}))},false);
 }finally{await rt?.close();if(!rt){await tr?.close();await works?.close();}
  if(error||phase==='finish')try{await fetch(url+'/task/stop',{method:'POST',headers,redirect:'error'});}catch{console.log('Bridge stop requires local cleanup check.');}
 }
}
if(error)process.exitCode=1;
