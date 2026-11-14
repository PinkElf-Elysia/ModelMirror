import {readFile,writeFile} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {SessionRecords} from '../../../authoring/session-storage.mjs';
import {CoreHost} from '../../host.mjs';import {adapters} from '../composition.mjs';import {CONTINUITY} from '../adapters.mjs';
import {runtimeManifest} from '../../manifest.mjs';import {ContextRealProvider,MODEL,SLOTS} from './provider.mjs';
import {hash,clone,need} from '../../contracts.mjs';
const [mode,path,port]=process.argv.slice(2),root=process.cwd(),directory=resolve(path||'.');
need(['mock','real'].includes(mode)&&directory===resolve(root,'experiments/ai-rpg-engine/.rpg03-work/context-r4-'+mode+'-v1')&&port===({mock:'18852',real:'18853'}[mode]),'CONTEXT_REAL_CLI');
const save=(name,data)=>writeFile(join(directory,name),typeof data==='string'?data:JSON.stringify(data,null,2)+'\n',{flag:'wx'});
const auth=JSON.parse(await readFile(join(directory,'local-auth.json'),'utf8')),baseURL='http://127.0.0.1:'+port,headers={Authorization:'Bearer '+auth.token};
let ledger,host;const outputs=[];let authorization,manifest;
try{
 const preflight=JSON.parse(await readFile(join(directory,'preflight.json'),'utf8')),ready=JSON.parse(await readFile(join(directory,'bridge-ready.json'),'utf8'));need(ready.mode===mode,'CONTEXT_REAL_MODE');
 const selections={};for(const profile of ['god-long-v1','core-query-r3']){const response=await fetch(baseURL+'/api/rpg/v1/models?profile='+profile,{headers,redirect:'error'});need(response.ok,'CONTEXT_REAL_CATALOG');const data=await response.json();selections[profile]=data.models.find(m=>m.model===MODEL&&m.available);need(selections[profile],'CONTEXT_REAL_SELECTION');}
 authorization={id:'context-r4-'+mode+'-20261114',maximumCalls:3,maximumUSD:.50,expiresAt:new Date(Date.now()+86400000).toISOString(),pricing:{inputUSD:Number(preflight.endpoint.pricing.prompt),outputUSD:Number(preflight.endpoint.pricing.completion),quoteId:hash(preflight.endpoint)}};
 const registry=await adapters();
 manifest=await runtimeManifest([new URL('./run.mjs',import.meta.url)],[{url:new URL('./protocol.py',import.meta.url)},{url:new URL('./bridge.py',import.meta.url)},{url:new URL('./launch.py',import.meta.url)},{url:new URL('../../real/tools.json',import.meta.url)}]);
 const cases=[{slot:1,adapter:'cultivation',input:'服用两份养息丹，随后与周禾简短交谈，其他状态不变。',options:{contextOptions:{quantityOnly:{id:'medicine',delta:-2},selection:{mode:'scoped',refs:['scene:qingyun','object:medicine','object:周禾']}}}},
 {slot:2,adapter:'lighthouse',input:CONTINUITY[1].input,options:{contextOptions:{selection:{mode:'scoped',refs:['scene:lighthouse']}}}},
 {slot:3,adapter:'lighthouse',input:CONTINUITY[3].input,options:{contextOptions:{selection:{mode:'scoped',refs:['scene:lighthouse']}}}}];
 await save('execution-claim.json',{mode,authorization,selections,manifest,cases,retries:0,perCallUSD:[.30,.10,.10],initialPromise:{kind:'confirmed-test-initial',duty:CONTINUITY[0].duty},toolChoice:'none'});
 ledger=await SessionRecords.open(join(directory,'budget'),{scope:'rpg-context-r4-real-budget',recoverDeadOwner:true});
 let record,initial;
 for(const item of cases){
  const provider=new ContextRealProvider({records:ledger,url:baseURL,serviceToken:auth.token,selections,adapter:item.adapter,slotId:item.slot,authorization,mode});
  if(item.slot!==3){
   if(host)await host.close();
   const adapter=registry.list.find(a=>a.id===item.adapter);adapter.binding={...adapter.binding,manifestHash:hash({context:adapter.binding.manifestHash,real:manifest.hash})};
   host=await CoreHost.open({directory:join(directory,item.adapter),adapter,offlinePreview:mode==='mock',controlledReal:mode==='real',provider});
   initial=adapter.initial();if(item.adapter==='lighthouse')initial.duty=CONTINUITY[0].duty;
   await save(item.adapter+'-initial.json',initial);
   record=await host.create({operationId:'create-'+item.adapter,initial,confirmed:true,source:{kind:'new',id:item.adapter==='lighthouse'?'r4-confirmed-pending-promise':'r4-adult-neutral',history:[]}});
  }else{need(outputs.at(-1)?.status==='completed'&&outputs.at(-1)?.exactExpectedState,'CONTEXT_DEPENDENT_TURN_BLOCKED');host.provider=provider;}
  const before=clone(record.state),operationId='turn-'+item.slot;
  console.log(JSON.stringify({starting:item.slot,adapter:item.adapter,mode,capUSD:SLOTS[item.slot].cap}));
  record=await host.turn(record.id,{operationId,revision:record.revision,input:item.input},item.options);
  const view=await host.view(record.id),op=record.operations[operationId];let expected=clone(before);
  if(item.slot===1)expected.items.find(i=>i.id==='medicine').quantity=3;
  if(item.slot===3)expected.duty=CONTINUITY[3].duty;
  const exactExpectedState=hash(expected)===hash(record.state),nonDutyUnchanged=hash({...before,duty:null})===hash({...record.state,duty:null});
  await save('session-'+item.slot+'.json',record);
  outputs.push({slot:item.slot,adapter:item.adapter,input:item.input,status:op.status,error:op.error??null,exactExpectedState,nonDutyUnchanged,beforeState:before,afterState:record.state,expectedState:expected,query:op.query??null,contextTrace:op.context?.contextTrace??null,view});
  console.log(JSON.stringify({finished:item.slot,status:op.status,exactExpectedState,nonDutyUnchanged,stages:op.stages.length}));
  if(op.status==='unknown')break;
  if(item.slot===2&&(op.status!=='completed'||!exactExpectedState))break;
 }
}catch(e){await save('execution-error.json',{code:e.code||e.message});throw e;}
finally{
 if(host)await host.close();
 const data=ledger?(await ledger.get('context-r4-budget',x=>x))?.data||{calls:[],reservedMicros:0}:{calls:[],reservedMicros:0};
 await save('transport-evidence.json',data);
 for(const c of data.calls){await save('reply-'+c.slot+'.txt',c.response?.body?.raw??c.result?.message?.content??'');await save('stage-'+c.slot+'.json',c);}
 await save('review.json',{mode,model:MODEL,outputs,calls:data.calls.map(c=>({slot:c.slot,adapter:c.adapter,capUSD:c.capUSD,reservedUSD:c.reserveMicros/1e6,status:c.result?.status,finishReason:c.result?.finishReason,usage:c.result?.usage??null,accounting:c.result?.accounting??null,timing:c.result?.timing??null,toolCalls:c.result?.message?.tool_calls??null})),reservedUSD:data.reservedMicros/1e6,limits:{calls:3,totalUSD:.50,retries:0}});
 if(ledger)await ledger.close();
 const stop=await fetch(baseURL+'/task/stop',{method:'POST',headers,redirect:'error'});need(stop.ok,'CONTEXT_BRIDGE_STOP');
}
if(mode==='mock')need(outputs.length===3&&outputs.every(o=>o.status==='completed'&&o.exactExpectedState)&&outputs.every(o=>Object.values(o.view.operations).filter(x=>x.stages).every(x=>x.stages.length===1)),'CONTEXT_MOCK_CHAIN');
console.log('Saved original replies, frozen requests and states; no retries.');
