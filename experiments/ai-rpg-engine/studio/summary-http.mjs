import {exact,operationId,revision} from './history-state.mjs';
import {requireTaskState} from './summary-tasks.mjs';
import {fail} from '../plugins/catalog.mjs';
export function summaryProjection(s){
 if(s.runtime?.format!==4)return {};
 const ops=Object.entries(requireTaskState(s).operations);
 return {rollingSummary:{supported:true},requests:{...Object.fromEntries(Object.entries(s.requests).map(([k,v])=>[k,{status:v.status,error:v.error}])),...Object.fromEntries(ops.filter(([,o])=>o.kind==='send').map(([k,o])=>[k,{status:o.status==='unknown'?'pending':o.status==='revoked'?'cancelled':o.status,error:o.error}]))}};
}
export async function summaryHttp({route,method,data,query,store,plugins,view,send}){
 const m=route.match(/^api\/sessions\/([a-zA-Z0-9-]+)\/rolling-summary(?:\/(catalog|settings|edit|update|recover))?$/);if(!m)return false;
 const id=m[1],action=m[2];
 if(query.size)throw fail('SUMMARY_OPERATION_INVALID',400);
 if(method==='GET'&&!action){
  const status=await store.summaryStatus(id,plugins);
  if(status.taskState)status.taskState={blocked:status.taskState.blocked,operations:Object.fromEntries(Object.entries(status.taskState.operations).map(([key,o])=>[key,{status:o.status,kind:o.kind,error:o.error,purpose:o.tasks.at(-1)?.purpose||null}]))};
  send(200,{...status,busy:store.running.has(id)||store.locks.has(id)});return true;
 }
 if(method==='GET'&&action==='catalog'){send(200,{models:await store.summaryCatalog(id,plugins)});return true;}
 if(method!=='POST'||!action)throw fail('SUMMARY_OPERATION_INVALID',400);
 let result;
 if(action==='settings')result=await store.saveSummaryForm(id,data,plugins);
 else if(action==='edit')result=await store.reviseSummary(id,data,plugins);
 else if(action==='update')result={operation:await store.summaryTasks.update(id,data)};
 else if(action==='recover'){
  if(!exact(data,['operationId','expectedSessionRevision','expectedSummaryRevision'])||!operationId(data.operationId)||!revision(data.expectedSessionRevision)||!revision(data.expectedSummaryRevision))throw fail('SUMMARY_OPERATION_INVALID',400);
  const s=await store.read(id);await store.requireSummarySession(s);
  if(s.revision!==data.expectedSessionRevision||s.rollingSummary.revision!==data.expectedSummaryRevision)throw fail('SUMMARY_CONFIGURATION_CONFLICT');
  const configured=Object.hasOwn(s.rollingSummary.operations,data.operationId);
  result={operation:configured?await store.recoverSummaryOperation(id,data.operationId):await store.summaryTasks.recover(id,data.operationId)};
 }else throw fail('SUMMARY_OPERATION_INVALID',400);
 send(200,{...result,session:view(await store.read(id))});return true;
}
