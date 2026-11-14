import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,join,relative} from 'node:path';
import {SessionRecords} from '../../authoring/session-storage.mjs';
import {CoreHost} from '../host.mjs';
import {adapters} from '../composition.mjs';
import {runtimeManifest} from '../manifest.mjs';
import {CoreRealProvider,MODEL} from './provider.mjs';
import {hash,clone,need} from '../contracts.mjs';
const [mode,path,port]=process.argv.slice(2),root=process.cwd(),directory=resolve(path||'.');
need(['mock','real'].includes(mode)&&relative(resolve(root,'experiments/ai-rpg-engine/.rpg03-work'),directory).startsWith('agent-core-r3-')&&['18842','18843'].includes(port),'CORE_REAL_CLI');
const save=(name,data)=>writeFile(join(directory,name),typeof data==='string'?data:JSON.stringify(data,null,2)+'\n',{flag:'wx'});
const auth=JSON.parse(await readFile(join(directory,'local-auth.json'),'utf8'));
const baseURL='http://127.0.0.1:'+port,headers={Authorization:'Bearer '+auth.token};
const preflight=JSON.parse(await readFile(join(directory,'preflight.json'),'utf8'));
const ready=JSON.parse(await readFile(join(directory,'bridge-ready.json'),'utf8'));need(ready.mode===mode,'CORE_REAL_MODE');
const selections={};for(const profile of ['god-long-v1','core-query-r3']){const response=await fetch(baseURL+'/api/rpg/v1/models?profile='+profile,{headers,redirect:'error'});need(response.ok,'CORE_REAL_CATALOG');const data=await response.json();selections[profile]=data.models.find(m=>m.model===MODEL&&m.available);need(selections[profile],'CORE_REAL_SELECTION');}
const authorization={id:'core-r3-'+mode+'-20261114',maximumCalls:3,maximumUSD:.90,expiresAt:new Date(Date.now()+86400000).toISOString(),pricing:{inputUSD:Number(preflight.endpoint.pricing.prompt),outputUSD:Number(preflight.endpoint.pricing.completion),quoteId:hash(preflight.endpoint)}};
const registry=await adapters();
const manifest=await runtimeManifest([new URL('./run.mjs',import.meta.url)], [{url:new URL('./protocol.py',import.meta.url)},{url:new URL('./bridge.py',import.meta.url)},{url:new URL('./tools.json',import.meta.url)}]);
const backend={};for(const name of ['rpg_bridge.py','rpg_local_state_tools.py'])backend[name]=hash((await readFile(new URL('../../../../server/model_router/'+name,import.meta.url))).toString('base64'));manifest.backend=backend;manifest.hash=hash({files:manifest.files,backend});
const cases=[{adapter:'cultivation',input:'服用两份养息丹，随后与周禾简短交谈，其他状态不变。',options:{contextOptions:{quantityOnly:{id:'medicine',delta:-2}}}},{adapter:'lighthouse',input:'先核对登记的灯油和绷带；若灯油足够，添油两份。绷带只查询，其他状态不变。',options:{}}];
await save('execution-claim.json',{mode,authorization,selections,manifest,cases,retries:0,perCallUSD:[.60,.10,.20]});
const ledger=await SessionRecords.open(join(directory,'budget'),{scope:'rpg-core-r3-real-budget',recoverDeadOwner:true});
const outputs=[];let liveHost;
try{
 for(const item of cases){
  const adapter=registry.list.find(a=>a.id===item.adapter);adapter.binding={...adapter.binding,manifestHash:hash({core:adapter.binding.manifestHash,real:manifest.hash})};
  const provider=new CoreRealProvider({records:ledger,url:baseURL,serviceToken:auth.token,selections,adapter:item.adapter,authorization,mode});
  liveHost=await CoreHost.open({directory:join(directory,item.adapter),adapter,offlinePreview:mode==='mock',controlledReal:mode==='real',provider});
  const initial=adapter.initial();await save(item.adapter+'-initial.json',initial);
  const r=await liveHost.create({operationId:'create-'+item.adapter,initial,confirmed:true,source:{kind:'new',id:'r3-'+mode,history:[]}});
  console.log(JSON.stringify({starting:item.adapter,mode,callCeilings:item.adapter==='cultivation'?[.60]:[.10,.20]}));
  const record=await liveHost.turn(r.id,{operationId:'turn-'+item.adapter,revision:r.revision,input:item.input},item.options);
  const view=await liveHost.view(r.id),op=record.operations['turn-'+item.adapter];
  const expected=clone(initial);if(item.adapter==='cultivation')expected.items.find(i=>i.id==='medicine').quantity=3;else expected.supplies.find(i=>i.id==='lamp-oil').quantity=3;
  const correct=hash(expected)===hash(record.state);await save(item.adapter+'-session.json',record);
  outputs.push({adapter:item.adapter,input:item.input,status:op.status,error:op.error??null,exactExpectedState:correct,query:op.query,view});
  console.log(JSON.stringify({finished:item.adapter,status:op.status,exactExpectedState:correct,stages:op.stages.length}));
  await liveHost.close();liveHost=null;
 }
}catch(e){await save('execution-error.json',{code:e.code||e.message});throw e;}
finally{
 if(liveHost)await liveHost.close();
 const data=(await ledger.get('core-real-budget',x=>x))?.data||{calls:[],reservedMicros:0};
 await save('transport-evidence.json',data);
 for(const c of data.calls){await save('reply-'+c.slot+'.txt',c.response?.body?.raw??c.result?.message?.content??'');await save('stage-'+c.slot+'.json',c);}
 await save('review.json',{mode,model:MODEL,outputs,calls:data.calls.map(c=>({slot:c.slot,adapter:c.adapter,capUSD:c.capUSD,reservedUSD:c.reserveMicros/1e6,status:c.result?.status,finishReason:c.result?.finishReason,usage:c.result?.usage??null,accounting:c.result?.accounting??null,timing:c.result?.timing??null,toolCalls:c.result?.message?.tool_calls??null})),reservedUSD:data.reservedMicros/1e6,limits:{calls:3,totalUSD:.90,retries:0}});
 await ledger.close();
 const stop=await fetch(baseURL+'/task/stop',{method:'POST',headers,redirect:'error'});need(stop.ok,'CORE_REAL_BRIDGE_STOP');
}
if(mode==='mock')need(outputs.length===2&&outputs.every(o=>o.status==='completed'&&o.exactExpectedState)&&outputs[0].view.operations['turn-cultivation'].stages.length===1&&outputs[1].view.operations['turn-lighthouse'].stages.length===2,'CORE_MOCK_CHAIN');
console.log('Saved replies, stage receipts and final state; no retries.');
