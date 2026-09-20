import {SUMMARY_INSTRUCTION,summaryUpdateSource,requireSummaryState,modelSummaryVersion,withSummaryVersion,effectiveContextPolicy} from './summary-state.mjs';
import {canonical,sha,fail} from '../plugins/catalog.mjs';
import {exact,operationId,revision} from './history-state.mjs';
import {controlledChoice} from './model-runtime.mjs';
import {reservationId} from './summary-budget.mjs';
const pending=new Set(['pending','unknown']);
export function summaryTaskBlock(s){return Object.values(s.summaryTasks?.operations||{}).some(o=>pending.has(o.status));}
export function requireTaskState(s){
 const state=s.summaryTasks;if(!state)return {operations:{},blocked:null};
 if(!exact(state,['operations','blocked','hash'])||state.hash!==sha(canonical({operations:state.operations,blocked:state.blocked}))||!state.operations||typeof state.operations!=='object'||Array.isArray(state.operations))throw fail('SUMMARY_TASK_STATE_INVALID');
 for(const [key,op] of Object.entries(state.operations))if(!operationId(key)||op.inputHash!==sha(canonical(op.input))||!['pending','unknown','complete','failed','cancelled','revoked'].includes(op.status)||!Array.isArray(op.tasks)||op.tasks.some(t=>t.sessionId!==s.id||!['story','summary'].includes(t.purpose)||t.messagesHash!==sha(canonical(t.messages))))throw fail('SUMMARY_TASK_STATE_INVALID');
 if(state.blocked!==null&&!Object.hasOwn(state.operations,state.blocked))throw fail('SUMMARY_TASK_STATE_INVALID');return state;
}
const sealTasks=value=>{const {hash,...body}=value;return {...body,hash:sha(canonical(body))};};
const taskId=(id,key,purpose)=>sha(canonical({id,key,purpose}));
const summaryMessages=source=>[{role:'system',content:SUMMARY_INSTRUCTION},{role:'user',content:JSON.stringify({previousSummary:source.previousSummary,completedTurns:source.source})}];
// Trusted host callbacks supply story assembly/publication at B3. No callbacks come from HTTP/plugin input.
export function createSummaryTasks({store,plugins,transport,buildStory=null,commitStory=null,admit=null}){
 const active=new Map();
 async function save(s){s.summaryTasks=sealTasks(s.summaryTasks);await store.write(s);}
 async function auth(s){return {summary:await plugins.summaryAuthorization(s.id),history:await plugins.historyAuthorization(s.id)};}
 async function guard(s,operation,action){
  return plugins.guardSummaryTask(s.id,operation.authorization.summary.revision,async a=>{
   if((await plugins.historyAuthorization(s.id)).revision!==operation.authorization.history.revision)throw fail('HISTORY_AUTHORIZATION_CHANGED_BEFORE_DISPATCH');
   await store.requireRuntime(await store.read(s.id));return action(a);
  });
 }
 async function choose(choice,sessionId){
  const list=await transport.catalog({sessionId});const found=list.find(m=>m.available&&m.model===choice.model&&(choice.kind==='fixed'||m.selectionId===choice.selectionId&&m.selectionRevision===choice.selectionRevision));
  if(!found)throw fail('MODEL_SELECTION_UNAVAILABLE');return controlledChoice(found);
 }
 async function finishTask(s,operation,task,result){
  task.output=structuredClone(result);const record=result.evidence?.record;
  if(!record||record.status!=='complete'){
   task.status=!result.dispatched||result.failureKnown?'failed':'unknown';throw fail(task.status==='unknown'?'SUMMARY_TASK_RESULT_UNKNOWN':'SUMMARY_TASK_FAILED');
  }
  if(record.sessionId!==s.id||record.requestId!==task.requestId||record.requestedModel!==task.selection.model||canonical(record.parameters)!==canonical(task.selection.parameters)||record.rawHash!==sha(result.raw))throw fail('SUMMARY_TASK_RECEIPT_INVALID');
  if(task.purpose==='summary'){
   const v=modelSummaryVersion(s,{raw:result.raw,finishReason:result.finishReason,model:task.selection,receipt:{requestId:task.requestId,requestHash:record.requestHash,responseHash:record.rawHash},expectedActiveVersionId:task.source.previousVersionId,targetThrough:task.source.targetThrough});
   await guard(s,operation,async()=>{s.rollingSummary=withSummaryVersion(s,v);s.revision++;task.versionId=v.id;task.status='complete';await save(s);});
  }else{
   if(typeof result.raw!=='string'||!result.raw.trim())throw fail('MODEL_EMPTY_OUTPUT');
   await guard(s,operation,async()=>{await commitStory(s,task,result);task.status='complete';await save(s);});
  }
 }
 async function step(s,operation,purpose,lease,controller){
  if(controller.signal.aborted)throw fail('CANCELLED_BEFORE_DISPATCH');
  const source=purpose==='summary'?summaryUpdateSource(s):null;
  const selection=await choose(purpose==='summary'?requireSummaryState(s).config.model:s.modelState.current,s.id);
  const story=purpose==='story'?await buildStory(s,operation.input.input,effectiveContextPolicy(s,operation.authorization.summary,operation.authorization.history)):null;
  const messages=purpose==='summary'?summaryMessages(source):story.messages;
  const task={purpose,sessionId:s.id,requestId:taskId(s.id,operation.input.operationId,purpose),selection,messages:structuredClone(messages),messagesHash:sha(canonical(messages)),source,story,lease,status:'pending',output:null};
  operation.tasks.push(task);await save(s);
  const result=await transport.execute(task,controller.signal,action=>guard(s,operation,action));
  task.output=structuredClone(result);
  if(controller.signal.aborted){task.status='cancelled';throw fail('CANCELLED');}
  try{await finishTask(s,operation,task,result);}catch(e){
   if(task.status==='pending')task.status=['SUMMARY_AUTHORIZATION_CHANGED','HISTORY_AUTHORIZATION_CHANGED_BEFORE_DISPATCH'].includes(e.code)?'revoked':e.code==='SUMMARY_TASK_RECEIPT_INVALID'?'unknown':'failed';throw e;
  }
 }
 async function work(id,input,kind,admission=input){
  return store.exclusive(id,async()=>{
   const s=await store.read(id);await store.requireSummarySession(s);const state=requireTaskState(s),prior=Object.hasOwn(state.operations,input.operationId)?state.operations[input.operationId]:null;
   if(prior){if(prior.inputHash!==sha(canonical(input)))throw fail('SUMMARY_TASK_OPERATION_COLLISION');return {...structuredClone(prior),replayed:true};}
   if(admit)await admit(s);
   if(summaryTaskBlock(s)||s.historyWindow.pending||s.rollingSummary.pending||Object.values(s.requests).some(r=>r.status==='pending'))throw fail('SUMMARY_TASK_UNCONFIRMED');
   const authorization=await auth(s),policy=effectiveContextPolicy(s,authorization.summary,authorization.history);
   if(s.revision!==admission.expectedSessionRevision||policy.revision!==admission.expectedContextPolicyRevision)throw fail('SUMMARY_TASK_REVISION_CONFLICT');
   if(authorization.summary.enabled&&!s.rollingSummary.config.model)throw fail('SUMMARY_MODEL_REQUIRED');
   if(kind!=='send'&&!authorization.summary.enabled)throw fail('PLUGIN_NOT_AUTHORIZED');
   if(kind==='send'&&state.blocked&&authorization.summary.enabled)throw fail('SUMMARY_UPDATE_REQUIRES_EXPLICIT_ACTION');
   const needs=authorization.summary.enabled&&summaryUpdateSource(s).needsUpdate;
   const purposes=kind==='send'?[...(needs?['summary']:[]),'story']:needs?['summary']:[];
   const operation={input:structuredClone(input),inputHash:sha(canonical(input)),kind,authorization,policyRevision:policy.revision,status:'pending',tasks:[],leases:[],error:null};
   s.summaryTasks={...state,operations:{...state.operations,[input.operationId]:operation}};await save(s);
   const controller=new AbortController();store.running.set(id,controller);
   try{
    if(purposes.length){operation.leases=await transport.budget.reserve(reservationId(id,input.operationId),id,purposes);await save(s);}
    for(let i=0;i<purposes.length;i++)await step(s,operation,purposes[i],operation.leases[i],controller);
    operation.status='complete';s.summaryTasks.blocked=null;
   }catch(e){
    const last=operation.tasks.at(-1);operation.status=controller.signal.aborted?'cancelled':last?.status==='unknown'?'unknown':last?.status==='revoked'?'revoked':'failed';
    operation.error=['BUDGET_EXHAUSTED','MODEL_SELECTION_UNAVAILABLE','SUMMARY_OUTPUT_REJECTED','SUMMARY_AUTHORIZATION_CHANGED','HISTORY_AUTHORIZATION_CHANGED_BEFORE_DISPATCH','SUMMARY_TASK_RESULT_UNKNOWN','CANCELLED'].includes(e.code)?e.code:'SUMMARY_TASK_FAILED';
    // Summary failure blocks future automatic work. Story failure can reuse a committed summary.
    if(last?.purpose==='summary'||kind!=='send'||!last&&needs&&e.code!=='BUDGET_EXHAUSTED')s.summaryTasks.blocked=input.operationId;
   }finally{
    for(const lease of operation.leases)try{await transport.budget.release(lease);}catch{(operation.releaseErrors??=[]).push(lease.index);}
    store.running.delete(id);
   }
   await save(s);return structuredClone(operation);
  });
 }
 function validate(input,send){
  const keys=['operationId','expectedSessionRevision','expectedContextPolicyRevision',...(send?['input']:[])];
  if(!exact(input,keys)||!operationId(input.operationId)||!revision(input.expectedSessionRevision)||typeof input.expectedContextPolicyRevision!=='string'||! /^[a-f0-9]{64}$/.test(input.expectedContextPolicyRevision)||(send&&(typeof input.input!=='string'||!input.input.trim()||input.input.length>20000)))throw fail('SUMMARY_TASK_INPUT_INVALID',400);
 }
 function launch(id,input,kind,admission=input){const promise=work(id,input,kind,admission);active.set(id,{kind,promise});promise.finally(()=>{if(active.get(id)?.promise===promise)active.delete(id);}).catch(()=>{});return promise;}
 const coordinator={
  async send(id,input){
   validate(input,true);input=structuredClone(input);if(!buildStory||!commitStory)throw fail('SUMMARY_ASSEMBLY_NOT_CONNECTED');
   let admission=input;
   const previous=active.get(id);if(previous){
    if(previous.kind!=='background')throw fail('SESSION_BUSY');
    const before=await store.read(id),a=await auth(before),p=effectiveContextPolicy(before,a.summary,a.history);
    if(before.revision!==input.expectedSessionRevision||p.revision!==input.expectedContextPolicyRevision)throw fail('SUMMARY_TASK_REVISION_CONFLICT');
    await previous.promise;
    const after=await store.read(id),b=await auth(after);admission={...input,expectedSessionRevision:after.revision,expectedContextPolicyRevision:effectiveContextPolicy(after,b.summary,b.history).revision};
   }
   const op=await launch(id,input,'send',admission);
   if(op.status==='complete'&&!op.replayed){
    const s=await store.read(id),a=await auth(s);
    if(a.summary.enabled&&s.rollingSummary.config.timing==='after'&&summaryUpdateSource(s).needsUpdate){
     const policy=effectiveContextPolicy(s,a.summary,a.history),next={operationId:taskId(id,input.operationId,'background'),expectedSessionRevision:s.revision,expectedContextPolicyRevision:policy.revision};
     launch(id,next,'background').catch(()=>{});
    }
   }
   return op;
  },
  async update(id,input){validate(input,false);if(active.has(id))throw fail('SESSION_BUSY');return launch(id,structuredClone(input),'update');},
  async wait(id){return active.get(id)?.promise||null;},
  async status(id){const s=await store.read(id);await store.requireSummarySession(s);return structuredClone(requireTaskState(s));},
  async recover(id,key){
   if(!operationId(key))throw fail('SUMMARY_TASK_INPUT_INVALID',400);if(active.has(id))throw fail('SESSION_BUSY');
   return store.exclusive(id,async()=>{
    const s=await store.read(id);await store.requireSummarySession(s);const state=requireTaskState(s),op=Object.hasOwn(state.operations,key)?state.operations[key]:null;
    if(!op)return {status:'not-found'};if(!pending.has(op.status))return structuredClone(op);
    s.summaryTasks=state;
    for(const task of op.tasks){if(task.status==='complete')continue;
     const result=await transport.recover(task);
     try{await finishTask(s,op,task,result);}catch(e){task.output=structuredClone(result);if(task.status==='pending'||task.status==='unknown')task.status=['SUMMARY_AUTHORIZATION_CHANGED','HISTORY_AUTHORIZATION_CHANGED_BEFORE_DISPATCH'].includes(e.code)?'revoked':['SUMMARY_OUTPUT_REJECTED','MODEL_EMPTY_OUTPUT'].includes(e.code)?'failed':result.dispatched?'unknown':'failed';}
    }
    const group=await transport.budget.recover(reservationId(id,key));op.releaseErrors=[];for(const lease of group?.leases||[])try{await transport.budget.release(lease);}catch{op.releaseErrors.push(lease.index);}
    op.status=op.tasks.some(t=>t.status==='unknown')?'unknown':op.tasks.some(t=>t.status==='revoked')?'revoked':op.tasks.some(t=>t.status!=='complete')?'failed':op.tasks.length?(op.tasks.some(t=>t.purpose==='story')||op.kind!=='send'?'complete':'failed'):op.kind!=='send'&&!summaryUpdateSource(s).needsUpdate?'complete':'failed';
    // Recovery never launches an unstarted story or background task.
    if(op.status==='complete'||op.status==='revoked'||op.status!=='unknown'&&op.tasks.filter(t=>t.purpose==='summary').every(t=>t.status==='complete'))state.blocked=null;else if(op.tasks.some(t=>t.purpose==='summary'&&t.status!=='complete'))state.blocked=key;
    await save(s);return structuredClone(op);
   });
  }
 };
 return coordinator;
}
