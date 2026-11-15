import {readFile,writeFile} from 'node:fs/promises';import {resolve,join,dirname} from 'node:path';import {execFileSync} from 'node:child_process';import {fileURLToPath} from 'node:url';
import {openAssembly} from '../runtime.mjs';import {hash,clone,need} from '../../contracts.mjs';import {runtimeManifest} from '../../manifest.mjs';import {totals} from '../../tasks/policy.mjs';
import {RealTransport} from './transport.mjs';import {MODEL,PLAN_INPUT,OBS_INPUT,SLOTS,liveCatalog} from './contract.mjs';
const [mode,arg]=process.argv.slice(2),directory=resolve(arg||'.');need(['mock','real'].includes(mode)&&dirname(directory)===resolve('C:/tmp/mm-r8-live')&&directory.split(/[\\/]/).at(-1).startsWith(mode+'-'),'R8_REAL_SCOPE');
const save=(n,v)=>writeFile(join(directory,n),typeof v==='string'?v:JSON.stringify(v,null,2)+'\n',{flag:'wx'});
const auth=JSON.parse(execFileSync('python',[fileURLToPath(new URL('./credential.py',import.meta.url)),'--read',directory],{encoding:'utf8',windowsHide:true}));
const url='http://127.0.0.1:'+({mock:18909,real:18910}[mode]),headers={Authorization:'Bearer '+auth.token};
let rt,tr,run,initial,expected,error=null,confirmation=null,localFeedbackMs=null;const began=performance.now();
try{
 const preflight=JSON.parse(await readFile(join(directory,'preflight.json'),'utf8')),ready=JSON.parse(await readFile(join(directory,'bridge-ready.json'),'utf8'));need(ready.mode===mode,'R8_MODE');
 const selections={};for(const profile of ['god-long-v1','core-query-r3']){const r=await fetch(url+'/api/rpg/v1/models?profile='+profile,{headers,redirect:'error'});need(r.ok,'R8_CATALOG');selections[profile]=(await r.json()).models.find(m=>m.model===MODEL&&m.available);need(selections[profile],'R8_SELECTION');}
 const quote={inputUSD:Number(preflight.endpoint.pricing.prompt),outputUSD:Number(preflight.endpoint.pricing.completion),quoteId:hash(preflight.endpoint)};
 const authorization={id:'r8-'+mode+'-'+directory.split(/[\\/]/).at(-1),maximumCalls:3,maximumUSD:.50,expiresAt:new Date(Date.now()+3600000).toISOString(),pricing:quote,requests:[]};
 const manifest=await runtimeManifest([new URL('./run.mjs',import.meta.url)],[...['bridge.py','launch.py','credential.py','protocol.py','wire.mjs'].map(n=>({url:new URL('./'+n,import.meta.url)})),{url:new URL('../../../package-lock.json',import.meta.url)}]);
 const catalog=await liveCatalog(quote);
 rt=await openAssembly(join(directory,'runtime'),{catalog,controlledReal:true,manifestHash:manifest.hash,limits:{calls:3,microUSD:500000},transportFactory:async path=>tr=await RealTransport.open({directory:path,url,token:auth.token,selections,authorization,mode})});
 await rt.ledger.change(r=>{need(!Object.keys(r.tasks).length,'R8_NO_REPLAY');r.accountingMode=mode;});
 run=await rt.create({operationId:'r8-real-create',adapterId:'cultivation'});const p=rt.ports.get('cultivation');initial=clone(run.world.state);expected=clone(initial);expected.items.find(i=>i.id==='medicine').quantity=3;await save('initial.json',initial);
 const narrativeId='narrate-'+hash([run.worldId,'r8-execute-once']).slice(0,32),operations={1:'r8-plan',2:narrativeId,3:'r8-observe'};
 const inputs={1:PLAN_INPUT,2:p.adapter.rule.label+' '+initial.items.find(i=>i.id==='medicine').name+' × 2',3:OBS_INPUT};
 authorization.requests=Object.values(SLOTS).map(s=>({slot:s.id,purpose:s.purpose,taskId:rt.taskId(run,s.purpose,operations[s.id]),session:run.worldId,inputHash:hash(inputs[s.id])}));
 await save('execution-claim.json',{mode,model:MODEL,authorization,selections,manifest,slots:SLOTS,inputs,retries:0,fallback:false,assembly:rt.compiled.descriptor});
 console.log('Dispatch slot 1: registered native proposal; one attempt.');
 let v=await rt.submit(run.id,{operationId:operations[1],ownerId:'world-dialogue',input:PLAN_INPUT,strategy:'slot-1'});await save('after-proposal.json',v);
 need(v.tasks.at(-1).adoption==='proposed'&&hash(v.world.state)===hash(initial),'R8_PROPOSAL_REQUIRED');
 const proposals=Object.entries(v.world.proposals).filter(([,x])=>x.status==='review');need(proposals.length===1,'R8_ONE_PROPOSAL');const [proposalId,row]=proposals[0];
 need(hash(row.proposal.action)===hash({actionId:'use_medicine',itemId:'medicine',quantity:2})&&row.proposal.expected.length===1&&row.proposal.expected[0].before===5&&row.proposal.expected[0].after===3,'R8_EXPECTED_PROPOSAL');
 confirmation={operationId:'r8-execute-once',revision:v.world.revision,proposalId,proposalHash:row.proposal.hash};
 await save('confirmation.json',{kind:'user-authorized-test-confirmation',...confirmation,expected:{before:5,after:3}});const started=performance.now();
 v=await rt.confirm(run.id,confirmation);localFeedbackMs=performance.now()-started;
 need(hash(v.world.state)===hash(expected)&&v.world.events.filter(e=>e.kind==='action').length===1,'R8_EXECUTED_STATE');await save('executed-world.json',await p.store.read(run.worldId));
 console.log('Confirmed once: medicine 5 -> 3. Dispatch slot 2: receipt narration.');
 v=await rt.narrate(run.id,{strategy:'slot-2'});await save('after-narration.json',v);need(v.tasks.at(-1).adoption==='completed'&&hash(v.world.state)===hash(expected),'R8_NARRATION_REQUIRED');
 console.log('Dispatch slot 3: independent public observation; no inventory access.');
 v=await rt.submit(run.id,{operationId:operations[3],ownerId:'observation-note',input:OBS_INPUT,strategy:'slot-3'});await save('before-observation-adopt.json',v);
 const note=v.tasks.at(-1);need(note.adoption==='awaiting-adoption'&&hash(v.world.state)===hash(expected),'R8_OBSERVATION_REQUIRED');
 need(hash(Object.keys(note.observation).sort())===hash(['events','prose','session','stateRevision'].sort()),'R8_OBSERVATION_SCOPE');
 v=await rt.adopt(run.id,note.id);need(v.tasks.at(-1).adoption==='completed'&&hash(v.world.state)===hash(expected),'R8_FINAL_STATE');await save('final-view.json',v);
}catch(e){error=e.code||e.message;await save('execution-error.json',{code:error});console.log('Stopped without retry: '+error);}
finally{
 try{
  const data=tr?await tr.evidence():{mode,calls:[],maxConcurrent:0};await save('transport-evidence.json',data);
  for(const c of data.calls){await save('stage-'+c.slot+'.json',c);await save('reply-'+c.slot+'.txt',c.result?.message?.content??c.response?.body?.raw??'');}
  const ledger=rt?await rt.ledger.read():null;await save('task-ledger.json',ledger);let world=null;
  if(rt&&run){world=await rt.ports.get('cultivation').store.read(run.worldId);await save('world-record.json',world);}
  await save('review.json',{mode,model:MODEL,error,limits:{calls:3,perCallUSD:[.15,.30,.05],totalUSD:.50,retries:0,fallback:false},actualCalls:data.calls.length,maxConcurrent:data.maxConcurrent,
   exactExpectedState:!!world&&!!expected&&hash(world.state)===hash(expected),actionEvents:world?.events.filter(e=>e.kind==='action').length??0,medicine:world?.state.items.find(i=>i.id==='medicine').quantity??null,
   localFeedbackMs,totalElapsedMs:performance.now()-began,budget:ledger?totals(ledger):null,calls:data.calls.map(c=>({slot:c.slot,sentAt:c.sentAt,completedAt:c.completedAt,status:c.result?.status,usage:c.result?.usage,accounting:c.result?.accounting,timing:c.result?.timing}))});
 }finally{await rt?.close();if(!rt)await tr?.close();try{await fetch(url+'/task/stop',{method:'POST',headers,redirect:'error'});}catch{console.log('Bridge stop requires local cleanup check.');}}
}
if(error)process.exitCode=1;
