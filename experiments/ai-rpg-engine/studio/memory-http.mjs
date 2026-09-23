import {exact,operationId,revision} from './history-state.mjs';
import {memoryPlan} from './memory-task-state.mjs';
import {summaryHttp} from './summary-http.mjs';
import {fail} from '../plugins/catalog.mjs';
const status=o=>o.status==='unknown'||o.status==='pending'?'pending':['revoked','not-applied'].includes(o.status)?'cancelled':o.status;
export function memoryProjection(s){
 if(s.runtime?.format!==5)return {};
 const requests=Object.fromEntries(Object.entries(s.requests).map(([k,v])=>[k,{status:v.status,error:v.error}]));
 for(const [key,o] of Object.entries(s.summaryTasks?.operations||{}))if(o.kind==='send')requests[key]={status:status(o),error:o.error};
 for(const [key,o] of Object.entries(s.memoryLegacyInputs||{}))if(!requests[key])requests[key]={status:o.status==='not-applied'?'cancelled':'pending'};
 for(const [key,o] of Object.entries(s.memoryTasks.operations))if(o.kind==='send')requests[key]={status:o.tasks.some(t=>t.purpose==='story'&&t.status==='complete')?'complete':status(o),error:o.error};
 return {memoryPalace:{supported:true},rollingSummary:{supported:true},requests};
}
const summaryTask=o=>({status:o.status,kind:o.kind,error:o.error,purpose:o.tasks.at(-1)?.purpose||null,overflow:o.tasks.some(t=>t.overflow||t.purpose==='compression')});
function validRecovery(x){return exact(x,['operationId','expectedSessionRevision','expectedMemoryRevision'])&&operationId(x.operationId)&&revision(x.expectedSessionRevision)&&revision(x.expectedMemoryRevision);}
export async function memoryHttp({route,method,data,query,store,plugins,view,send}){
 const match=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/(memory-palace|rolling-summary)(?:\/(catalog|settings|edit|update|recover))?$/);if(!match)return false;
 const [,id,feature,action]=match,s=await store.read(id);
 if(feature==='rolling-summary'&&s.runtime?.format!==5)return false;
 if(query.size)throw fail('MEMORY_OPERATION_INVALID',400);
 if(s.runtime?.format!==5){if(method==='GET'&&!action){send(200,{compatible:false,reason:'MEMORY_RUNTIME_INCOMPATIBLE',enabled:false});return true;}throw fail('MEMORY_RUNTIME_INCOMPATIBLE');}
 await store.requireRuntime(s);
 if(feature==='rolling-summary'){
  if(method==='GET'&&!action){const h=await store.summaryStatus(id,plugins),a=await store.memoryAuthorizations(id),combined={...s.summaryTasks?.operations,...s.memoryTasks.operations};
   const blocked=s.memoryTasks.blocked&&s.memoryTasks.operations[s.memoryTasks.blocked].blockedPurpose==='summary'?s.memoryTasks.blocked:h.taskState.blocked;
   h.taskState={blocked,operations:Object.fromEntries(Object.entries(combined).map(([k,o])=>[k,summaryTask(o)]))};h.updatePlan=h.enabled&&h.config.model&&h.effectivePolicy.needsUpdate?{kind:'summary',maxCalls:a.memory.enabled?memoryPlan(s,a,'update').length:2}:null;
   h.compressionAttempts=Object.entries(combined).flatMap(([operationId,o])=>o.tasks.filter(t=>t.overflow).map(t=>({operationId,status:o.status,raw:t.output.raw,compressedRaw:o.tasks.find(c=>c.candidate?.requestId===t.requestId)?.output?.raw??null})));
   send(200,{...h,busy:store.running.has(id)||store.locks.has(id)});return true;
  }
  if(method==='POST'&&action==='update'&&(await plugins.memoryAuthorization(id)).enabled){
   if(!exact(data,['operationId','expectedSessionRevision','expectedContextPolicyRevision'])||!operationId(data.operationId)||!revision(data.expectedSessionRevision))throw fail('SUMMARY_OPERATION_INVALID',400);
   const operation=await store.memoryTasks.update(id,{...data,expectedMemoryRevision:s.memoryPalace.revision});send(200,{operation:{status:operation.status,error:operation.error},session:view(await store.read(id))});return true;
  }
  if(method==='POST'&&action==='recover'&&Object.hasOwn(s.memoryTasks.operations,data?.operationId||'')){
   if(!exact(data,['operationId','expectedSessionRevision','expectedSummaryRevision'])||!operationId(data.operationId)||data.expectedSessionRevision!==s.revision||data.expectedSummaryRevision!==s.rollingSummary.revision)throw fail('SUMMARY_CONFIGURATION_CONFLICT');
   const operation=await store.recoverMemoryOperation(id,data.operationId);send(200,{operation:{status:operation.status,error:operation.error},session:view(await store.read(id))});return true;
  }
  return summaryHttp({route,method,data,query,store,plugins,view,send});
 }
 if(method==='GET'&&!action){
  const x=await store.memoryTasks.status(id),a=await store.memoryAuthorizations(id),ops=s.memoryTasks.operations;
  const operations={...Object.fromEntries(Object.entries(ops).map(([k,o])=>[k,summaryTask(o)])),...Object.fromEntries(Object.entries(s.summaryTasks?.operations||{}).map(([k,o])=>[k,summaryTask(o)]))};
  const originalOutputs=Object.entries(ops).flatMap(([operationId,o])=>o.tasks.filter(t=>t.purpose==='memory'&&t.output).map(t=>({operationId,status:t.status,raw:t.output.raw??'',sourceFrom:t.source.from,sourceThrough:t.source.through,rawHash:t.output.evidence?.record?.rawHash??null})));
  const pendingOperationId=s.memoryPalace.pending||s.memoryTasks.pendingSettings||Object.keys(s.memoryLegacyInputs||{}).find(k=>s.memoryLegacyInputs[k].status==='prepared'&&!s.summaryTasks?.operations?.[k])||null;
  send(200,{compatible:true,enabled:a.memory.enabled,busy:store.running.has(id)||store.locks.has(id),sessionRevision:s.revision,memoryRevision:s.memoryPalace.revision,config:s.memoryTasks.config,entries:s.memoryPalace.entries,processedThrough:s.memoryPalace.processedThrough,totalTurns:s.turns.length,pendingOperationId,operations,originalOutputs,effectivePolicy:x.effectivePolicy,maxCalls:x.maxCalls,updateMaxCalls:a.memory.enabled?memoryPlan(s,a,'update').length:0,blocked:s.memoryTasks.blocked,lastRecall:s.turns.at(-1)?.memoryPolicy.recall||null});return true;
 }
 if(method==='GET'&&action==='catalog'){send(200,{models:await store.memoryTasks.catalog(id)});return true;}
 if(method!=='POST')throw fail('MEMORY_OPERATION_INVALID',400);
 let result;
 if(action==='settings')result={operation:await store.memoryTasks.configure(id,data)};
 else if(action==='edit')result=await store.memoryEdits.save(id,data);
 else if(action==='update')result={operation:await store.memoryTasks.update(id,data)};
 else if(action==='recover'){
  if(!validRecovery(data))throw fail('MEMORY_OPERATION_INVALID',400);
  if(data.expectedSessionRevision!==s.revision||data.expectedMemoryRevision!==s.memoryPalace.revision)throw fail('MEMORY_CONFIGURATION_CONFLICT');
  const key=data.operationId;result={operation:Object.hasOwn(s.memoryPalace.operations,key)?await store.memoryEdits.recover(id,key):Object.hasOwn(s.memoryTasks.settings,key)?await store.memoryTasks.recoverSetting(id,key):await store.recoverMemoryOperation(id,key)};
 }else throw fail('MEMORY_OPERATION_INVALID',400);
 send(200,{operation:{status:result.operation.status,error:result.operation.error},replayed:result.replayed||result.operation.replayed||false,session:view(await store.read(id))});return true;
}
