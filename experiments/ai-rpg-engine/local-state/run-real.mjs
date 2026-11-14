import {readFile,writeFile,mkdir,readdir} from 'node:fs/promises';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {join} from 'node:path';
import {hash,need} from './definition.mjs';
import {loadAdapter} from './adapter.mjs';
import {assembleContext} from './context.mjs';
import {LocalStateStore} from './store.mjs';
import {candidate} from './rules.mjs';
import {createControlledProvider} from '../studio/controlled-provider.mjs';
import {accounting,normalizeUsage} from '../harness/measurement.mjs';
export const RUN=fileURLToPath(new URL('../.rpg03-work/local-state-real-20261113/',import.meta.url));
export function admission(messages,parameters,pricing){
 const units=messages.reduce((n,m)=>n+Buffer.byteLength(m.content,'utf8')+32,256);
 const reserveUSD=600000*Number(pricing.prompt)+parameters.max_tokens*Number(pricing.completion);
 need(units<=600000&&parameters.max_tokens===32768&&reserveUSD<=0.60,'REAL_BUDGET');return {units,reserveUSD};
}
export function scenarioCheck(id,before,receipt){
 const p=candidate(before,receipt),medicine=p.state.items.find(i=>i.id==='medicine'),checks=[];
 if(id==='R1'){
  checks.push(['medicine5to3',before.items.find(i=>i.id==='medicine').quantity===5&&medicine.quantity===3]);
  checks.push(['helpEvent',p.operations.some(o=>o.type==='relation'&&o.id==='zhou'&&o.axis==='relation'&&o.event==='help'&&o.delta>=5&&o.delta<=15)]);
  checks.push(['noEconomicReward',hash(before.balances)===hash(p.state.balances)]);
 }else checks.push(['noChange',p.operations.length===0&&hash(before)===hash(p.state)&&medicine.quantity===3]);
 return {passed:checks.every(c=>c[1]),checks:Object.fromEntries(checks),proposal:p};
}
async function json(name){return JSON.parse(await readFile(join(RUN,name),'utf8'));}
async function freeze(name,value){await writeFile(join(RUN,name),JSON.stringify(value,null,2)+'\n',{flag:'wx'});}
export async function execute(id){
 need(['R1','R2'].includes(id),'REAL_REQUEST');
 const plan=await json('plan.json');need(hash(plan)==='5f0ea36133ce084844dc16509f705f1f2d040e1b702cf1671fac70bb41628cc9','REAL_PLAN_CHANGED');
 const adapter=await loadAdapter();need(hash(adapter.binding)===hash(plan.binding),'REAL_ADAPTER_CHANGED');
 const previous=id==='R2'?await json('R1-result.json'):null;
 if(previous)need(previous.committed&&previous.scenario?.passed&&previous.withinBudget===true,'REAL_R1_REQUIRED');
 const used=(await readdir(RUN)).filter(n=>/^R[12]-claim.json$/.test(n));need(used.length<2&&!used.includes(id+'-claim.json'),'REAL_NO_RETRY');
 const priceURL=plan.pricing.source,priceResponse=await fetch(priceURL,{signal:AbortSignal.timeout(20000)});need(priceResponse.ok,'REAL_PRICE_UNAVAILABLE');
 const catalog=await priceResponse.json(),endpoint=catalog.data?.endpoints?.find(e=>e.tag==='google-ai-studio'&&e.service_tier===null&&e.status===0);
 need(endpoint&&Number(endpoint.pricing.prompt)<=0.00000075&&Number(endpoint.pricing.completion)<=0.00000375&&Number(endpoint.pricing.internal_reasoning)<=0.00000375,'REAL_PRICE_CHANGED');
 const route=await json('route-binding.json');need(route.providerTag==='google-ai-studio'&&route.selectionId&&route.selectionRevision,'REAL_ROUTE_PROOF_REQUIRED');
 const modelPolicy={cardId:'god',parametersFor:()=>({...plan.parameters}),sameParameters:(_model,p)=>hash(p)===hash(plan.parameters)};
 // god-long-v1 is the existing bridge's parameter profile only; no God card prompt or save is used.
 const transport=await createControlledProvider({directory:join(RUN,'transport'),baseURL:'http://127.0.0.1:8000/',serviceToken:process.env.RPG_S2S_TOKEN,enabled:true,limit:2,measurement:{pollIntervalMs:250},cardId:'god',parameterProfile:'god-long-v1',modelPolicy,catalogModels:[plan.model]});
 const selection=(await transport.catalog()).find(m=>m.model===plan.model&&m.available);
 need(selection?.selectionId===route.selectionId&&selection.selectionRevision===route.selectionRevision,'REAL_ROUTE_CHANGED');
 const store=await LocalStateStore.open({directory:join(RUN,'sessions'),binding:adapter.binding,recoverDeadOwner:true});
 try{
  let record=await store.create({operationId:'real-initial',initial:await json('initial.json'),confirmed:true});
  need(record.turns.length===(id==='R1'?0:1)&&!record.pending,'REAL_SEQUENCE');
  let context;
  if(id==='R1'){context=await json('R1-context.json');need(hash(context.messages)===plan.r1MessagesHash,'REAL_INPUT_CHANGED');}
  else {context=await assembleContext(record,plan.inputs[1]);context.messages[0].content+='\n\n'+plan.testConstraint;context.hash=hash(context.messages);await freeze('R2-context.json',context);}
  const admitted=admission(context.messages,plan.parameters,endpoint.pricing);
  const before=structuredClone(record.state),operationId='real-'+id.toLowerCase();
  await freeze(id+'-price.json',{source:priceURL,observedAt:new Date().toISOString(),endpoint});
  await freeze(id+'-claim.json',{id,operationId,sessionId:record.id,at:new Date().toISOString(),selection,parameters:plan.parameters,messagesHash:context.hash,admission:admitted,perCallCeilingUSD:0.60,totalCeilingUSD:1.20,retries:0});
  const started=await store.startTurn(record.id,{operationId,revision:record.revision,input:plan.inputs[id==='R1'?0:1]},context);need(started.dispatch,'REAL_ALREADY_STARTED');
  console.log(JSON.stringify({dispatching:id,model:plan.model,maxOutputTokens:32768,perCallCeilingUSD:0.60,totalCeilingUSD:1.20}));
  let transportError=null;
  try{await transport.generate('god',{messages:context.messages,params:plan.parameters,sessionId:record.id,requestId:operationId,selection});}catch(e){transportError=e.code||'TRANSPORT_UNKNOWN';}
  const out=await transport.recoveredOutput(record.id,operationId);
  const raw=out?.raw||'',usage=out?.measurement?.usage||normalizeUsage(null),cost=accounting(usage,plan.pricing);
  const complete=!transportError&&out?.finishReason==='stop'&&out?.evidence?.record.status==='complete';
  const receipt={status:complete?'completed':out?.failureKnown?'failed':'unknown',finishReason:out?.finishReason||null,text:raw};
  const result={id,model:plan.model,sessionId:record.id,operationId,input:plan.inputs[id==='R1'?0:1],status:receipt.status,finishReason:receipt.finishReason,code:out?.failureCode||transportError,raw,usage,accounting:cost,timing:out?.measurement?.timing||null,evidence:out?.evidence||null,before,after:before,committed:false,scenario:null,withinBudget:usage.issues.length===0&&(usage.inputTokens??0)<=600000&&(usage.outputTokens??0)<=32768&&(cost.reportedCostMicros??572880)<=600000};
  // Preserve the original response and statistics before local validation or any state commit.
  await writeFile(join(RUN,id+'-reply.txt'),raw,{flag:'wx'});await freeze(id+'-received.json',result);
  await store.receive(record.id,operationId,receipt);
  if(complete){
   try{const checked=scenarioCheck(id,before,receipt);result.scenario={passed:checked.passed,checks:checked.checks};result.operations=checked.proposal.operations;}
   catch(e){result.validationError=e.code||e.message;}
   record=await store.finish(record.id,operationId);
   result.committed=record.operations[operationId].status==='completed';result.validationError??=record.operations[operationId].error||null;result.after=record.state;
   if(result.committed)result.body=record.turns.at(-1).body;
  }
  await freeze(id+'-result.json',result);await freeze(id+'-session.json',await store.read(record.id));
  console.log(JSON.stringify({id,status:result.status,finishReason:result.finishReason,committed:result.committed,scenario:result.scenario,validationError:result.validationError,cost, timing:result.timing}));
 }finally{await store.close();}
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
 if(process.argv[2]!=='--execute')throw Error('EXPLICIT_EXECUTION_REQUIRED');
 await execute(process.argv[3]);
}
