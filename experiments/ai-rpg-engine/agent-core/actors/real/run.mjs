import {readFile,writeFile} from 'node:fs/promises';import {resolve,join} from 'node:path';
import {execFileSync} from 'node:child_process';import {fileURLToPath} from 'node:url';
import {SessionRecords} from '../../../authoring/session-storage.mjs';import {openRuntime} from '../composition.mjs';
import {ActorRealProvider,MODEL,SLOTS} from './provider.mjs';import {hash,clone,need} from '../../contracts.mjs';
import {runtimeManifest} from '../../manifest.mjs';
export const INPUTS={boundary:'刚才在廊下发生了什么？我的养息丹还剩多少？',query:'你刚才看到了什么？请核对我登记的养息丹余量，再说说接下来可以怎么安排。'};
const [mode,path,port]=process.argv.slice(2),directory=resolve(path||'.'),root=process.cwd();
need(['mock','real'].includes(mode)&&directory.startsWith(resolve(root,'experiments/ai-rpg-engine/.rpg03-work/actors-r6-'+mode+'-'))&&port===({mock:'18892',real:'18893'}[mode]),'R6_REAL_CLI');
const save=(name,data)=>writeFile(join(directory,name),typeof data==='string'?data:JSON.stringify(data,null,2)+'\n',{flag:'wx'});
const auth=JSON.parse(execFileSync('python',[fileURLToPath(new URL('./credential.py',import.meta.url)),'--read',directory],{encoding:'utf8',windowsHide:true}));
const url='http://127.0.0.1:'+port,headers={Authorization:'Bearer '+auth.token};
let runtime,ledger,h,r,initial,expected,error=null,authorization,selections,manifest,started=performance.now();
try{
 const preflight=JSON.parse(await readFile(join(directory,'preflight.json'),'utf8')),ready=JSON.parse(await readFile(join(directory,'bridge-ready.json'),'utf8'));need(ready.mode===mode,'R6_REAL_MODE');
 selections={};for(const profile of ['core-query-r3','god-long-v1']){
  const response=await fetch(url+'/api/rpg/v1/models?profile='+profile,{headers,redirect:'error'});need(response.ok,'R6_CATALOG');
  selections[profile]=(await response.json()).models.find(m=>m.model===MODEL&&m.available);need(selections[profile],'R6_SELECTION');
 }
 runtime=await openRuntime(join(directory,'runtime'),{offlinePreview:true});h=runtime.host;
 manifest=await runtimeManifest([new URL('./run.mjs',import.meta.url)],[{url:new URL('./protocol.py',import.meta.url)},{url:new URL('./bridge.py',import.meta.url)},{url:new URL('./launch.py',import.meta.url)},{url:new URL('./credential.py',import.meta.url)}]);
 r=await h.create({operationId:'r6-real-create',inventory:true});initial=clone((await h.world.store.read(r.worldId)).state);await save('initial.json',initial);
 await save('confirmation.json',{kind:'user-authorized-test-confirmation',operationId:'r6-use-once',action:{actionId:'use_medicine',itemId:'medicine',quantity:2},expected:{before:5,after:3},generatedByModel:false});
 r=await h.worldAction(r.id,{operationId:'r6-use-once',revision:0});need(r.tasks['r6-use-once'].status==='completed','R6_EXECUTION');
 await h.narrateWorld(r.id);await h.deliver(r.id); // R5 fixed fixture, explicitly not a paid world narration.
 expected=clone(initial);expected.items.find(i=>i.id==='medicine').quantity=3;
 need(hash((await h.world.store.read(r.worldId)).state)===hash(expected),'R6_STATE_BEFORE_MODEL');
 await save('executed-world.json',await h.world.store.read(r.worldId));
 const ids={1:'stage-'+hash([r.id,'r6-boundary',0]).slice(0,40),2:'stage-'+hash([r.id,'r6-query',0]).slice(0,40),3:'stage-'+hash([r.id,'r6-query',1]).slice(0,40)};
 authorization={id:'r6-'+mode+'-'+directory.split(/[\\/]/).at(-1),maximumCalls:3,maximumUSD:.50,expiresAt:new Date(Date.now()+6*3600000).toISOString(),
  pricing:{inputUSD:Number(preflight.endpoint.pricing.prompt),outputUSD:Number(preflight.endpoint.pricing.completion),quoteId:hash(preflight.endpoint)},
  requests:Object.fromEntries(Object.entries(ids).map(([slot,id])=>[id,{slot:Number(slot),session:r.id,inputHash:hash(Number(slot)===1?INPUTS.boundary:INPUTS.query)}]))};
 await save('execution-claim.json',{mode,model:MODEL,authorization,selections,manifest,inputs:INPUTS,slots:SLOTS,retries:0,worldNarration:'offline-fixture',existingPreviewUnchanged:true});
 ledger=await SessionRecords.open(join(directory,'budget'),{scope:'rpg-actors-r6-real-budget',recoverDeadOwner:true});
 h.provider=new ActorRealProvider({records:ledger,url,serviceToken:auth.token,selections,authorization,mode});
 h.requestPolicy=(request,{actorId})=>actorId==='outsider'?{...request,tools:[],tool_choice:'none'}:request;
 console.log('R6 boundary request; maximum three dispatches, no automatic retry.');
 r=await h.respond(r.id,{operationId:'r6-boundary',actorId:'outsider',input:INPUTS.boundary});await save('after-boundary.json',r);
 need(r.tasks['r6-boundary'].status==='completed','R6_BOUNDARY_INCOMPLETE');
 need(hash((await h.world.store.read(r.worldId)).state)===hash(expected),'R6_BOUNDARY_CHANGED_WORLD');
 console.log('R6 native query request; continuation only after valid read-only tool.');
 r=await h.respond(r.id,{operationId:'r6-query',actorId:'witness',input:INPUTS.query});await save('after-query.json',r);
 const task=r.tasks['r6-query'];need(task.status==='completed','R6_QUERY_INCOMPLETE');need(task.stages.length===2&&task.stages[0].toolResult?.status==='matched','R6_QUERY_NOT_OBSERVED');
 need(task.stages[0].toolResult.items.some(i=>i.id==='medicine'&&i.quantity===3),'R6_QUERY_WRONG_SCOPE');
 need(hash((await h.world.store.read(r.worldId)).state)===hash(expected),'R6_RESPONSE_CHANGED_WORLD');
}catch(e){error=e.code||e.message;await save('execution-error.json',{code:error});console.log('Stopped without retry: '+error);}
finally{
 if(h&&r){r=await h.read(r.id);await save('bridge-record.json',r);await save('world-record.json',await h.world.store.read(r.worldId));}
 const data=ledger?(await ledger.get('actors-r6-budget'))?.data||{calls:[],reservedMicros:0}:{calls:[],reservedMicros:0};
 await save('transport-evidence.json',data);
 for(const c of data.calls){await save('reply-'+c.slot+'.txt',c.response?.body?.raw??c.result?.message?.content??'');await save('stage-'+c.slot+'.json',c);}
 const world=h&&r?await h.world.store.read(r.worldId):null;
 await save('review.json',{mode,model:MODEL,error,limits:{maximumCalls:3,totalUSD:.50,perCallUSD:[.15,.15,.20],retries:0},
  bridgeId:r?.id??null,worldNarration:'offline-fixture-not-paid',exactExpectedState:world?hash(world.state)===hash(expected):false,
  actionEvents:world?.events.filter(e=>e.kind==='action').length??0,medicine:world?.state.items.find(i=>i.id==='medicine').quantity??null,
  totalElapsedMs:performance.now()-started,calls:data.calls.map(c=>({slot:c.slot,capUSD:c.capUSD,reservedUSD:c.reserveMicros/1e6,status:c.result?.status,finishReason:c.result?.finishReason,usage:c.result?.usage??null,accounting:c.result?.accounting??null,timing:c.result?.timing??null}))});
 if(runtime)await runtime.close();if(ledger)await ledger.close();
 const stop=await fetch(url+'/task/stop',{method:'POST',headers,redirect:'error'});need(stop.ok,'R6_BRIDGE_STOP');
}
if(mode==='mock')need(!error,'R6_MOCK_CHAIN');
console.log('Saved original replies, native tools, state and accounting; bridge stopping.');
