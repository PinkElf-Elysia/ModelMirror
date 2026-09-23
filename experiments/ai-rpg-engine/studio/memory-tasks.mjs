import {canonical,sha,fail,MEMORY_PALACE_ID} from '../plugins/catalog.mjs';
import {validSettings} from '../plugins/memory-palace.mjs';
import {exact,revision,operationId} from './history-state.mjs';
import {controlledChoice,validChoice} from './model-runtime.mjs';
import {requireMemoryState,sealMemory} from './memory-state.mjs';
import {requireMemoryTasks,sealMemoryTasks,memoryTaskBlock,memoryContextPolicy,memoryPlan,memorySource,MEMORY_INSTRUCTION,publishMemoryResult} from './memory-task-state.mjs';
import {SUMMARY_INSTRUCTION,summaryUpdateSource,modelSummaryVersion,withSummaryVersion} from './summary-state.mjs';
import {overflow,compressionMessages,compressionEvidence,COMPRESSION_MAX} from './summary-compression.mjs';
import {reservationId} from './memory-budget.mjs';
const wireHash=t=>sha(JSON.stringify({sessionId:t.sessionId,requestId:t.requestId,selectionId:t.selection.selectionId,selectionRevision:t.selection.selectionRevision,messages:t.messages,parameters:t.selection.parameters}));
const pending=o=>['pending','unknown'].includes(o.status);
const identity=(id,key,stageId)=>sha(canonical({id,key,stageId}));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
// No caller-supplied callbacks/configuration cross HTTP. The M3 host connects these
// trusted boundaries; B3 supplies production story assembly, publication and snapshots.
export function createMemoryTasks({store,plugins,transport,buildStory=null,commitStory=null,settleStory=null,legacySend=null}){
 const active=new Map();
 const authorizations=async id=>({memory:await plugins.memoryAuthorization(id),summary:await plugins.summaryAuthorization(id),history:await plugins.historyAuthorization(id)});
 const save=async s=>{s.memoryTasks.hash=sealMemoryTasks(s.memoryTasks).hash;await store.write(s);};
 async function read(id){const s=await store.read(id);if(!(await store.pluginSession(id)).memoryPalaceCompatible)throw fail('MEMORY_RUNTIME_INCOMPATIBLE');await store.requireRuntime(s);requireMemoryState(s);requireMemoryTasks(s);return s;}
 async function guard(s,op,action){return plugins.guardMemoryTask(s.id,op.authorization,async()=>{await store.requireRuntime(await store.read(s.id));return action();});}
 async function choose(choice,id){
  if(!validChoice(choice))throw fail('MEMORY_MODEL_REQUIRED');
  const found=(await transport.catalog({sessionId:id})).find(m=>m.available&&m.model===choice.model&&(choice.kind==='fixed'||m.selectionId===choice.selectionId&&m.selectionRevision===choice.selectionRevision));
  if(!found)throw fail('MODEL_SELECTION_UNAVAILABLE');const selected=controlledChoice(found);
  if(choice.kind==='controlled'&&canonical(selected)!==canonical(choice))throw fail('MODEL_SELECTION_CHANGED');return selected;
 }
 function unconfirmed(s){if(memoryTaskBlock(s)||s.memoryPalace.pending||s.historyWindow?.pending||s.rollingSummary?.pending||Object.values(s.summaryTasks?.operations||{}).some(pending)||Object.values(s.requests||{}).some(r=>r.status==='pending'))throw fail('MEMORY_TASK_UNCONFIRMED');}
 function validate(input,kind){
  if(!exact(input,['operationId','expectedSessionRevision','expectedMemoryRevision','expectedContextPolicyRevision',...(kind==='send'?['input']:[])])||!operationId(input.operationId)||!revision(input.expectedSessionRevision)||!revision(input.expectedMemoryRevision)||typeof input.expectedContextPolicyRevision!=='string'||!/^[a-f0-9]{64}$/.test(input.expectedContextPolicyRevision)||kind==='send'&&(typeof input.input!=='string'||!input.input.trim()||input.input.length>20000))throw fail('MEMORY_TASK_INPUT_INVALID',400);
 }
 async function finish(s,op,task,result,cancelled=()=>op.cancelRequested){
  task.output=structuredClone(result);if(result?.cancelledKnown){task.status='cancelled';throw fail('CANCELLED');}const receipt=result?.evidence?.record;
  if(!receipt||receipt.status!=='complete'){
   task.status=result?.dispatched===false||result?.failureKnown?'failed':'unknown';throw fail(task.status==='unknown'?'MEMORY_TASK_RESULT_UNKNOWN':'MEMORY_TASK_FAILED');
  }
  if(receipt.sessionId!==s.id||receipt.requestId!==task.requestId||receipt.requestedModel!==task.selection.model||canonical(receipt.parameters)!==canonical(task.selection.parameters)||typeof result.raw!=='string'||receipt.rawHash!==sha(result.raw)||receipt.requestHash!==wireHash(task)){
   task.status='unknown';throw fail('MEMORY_TASK_RECEIPT_INVALID');
  }
  if(cancelled()){task.status='cancelled';throw fail('CANCELLED');}
  // Build candidate on a copy. Validation or a failed write cannot mutate the live
  // library/summary in a later failure-state save.
  const next=structuredClone(s),nt=next.memoryTasks.operations[op.input.operationId].tasks.find(t=>t.stageId===task.stageId);
  if(task.purpose==='memory')publishMemoryResult(next,task,result);
  else if(task.purpose==='story'){
   if(!result.raw.trim())throw fail('MODEL_EMPTY_OUTPUT');await commitStory(next,task,result);
  }else if(task.purpose==='summary'&&overflow(result.raw,result.finishReason))nt.overflow=true;
  else{
   if(task.purpose==='compression'&&(result.finishReason!=='stop'||Array.from(result.raw).length>COMPRESSION_MAX||!result.raw.trim()))throw fail('SUMMARY_COMPRESSION_REJECTED');
   const v=modelSummaryVersion(next,{raw:result.raw,finishReason:result.finishReason,model:task.selection,receipt:{requestId:task.requestId,requestHash:receipt.requestHash,responseHash:receipt.rawHash},expectedActiveVersionId:task.source.previousVersionId,targetThrough:task.source.targetThrough,...(task.purpose==='compression'?{compression:compressionEvidence(task.candidate)}:{})});
   next.rollingSummary=withSummaryVersion(next,v);next.revision++;nt.versionId=v.id;
  }
  nt.status='complete';
  await guard(s,op,async()=>{if(cancelled())throw fail('CANCELLED');try{await save(next);}catch(e){e.preserveDisk=true;throw e;}});
  // Preserve operation/task object identities while installing committed state.
  Object.assign(task,nt);const oldOp=op;Object.assign(oldOp,next.memoryTasks.operations[op.input.operationId]);
  Object.assign(s,next);s.memoryTasks.operations[oldOp.input.operationId]=oldOp;
 }
 async function step(s,op,stage,controller){
  if(controller.signal.aborted)throw fail('CANCELLED_BEFORE_DISPATCH');
  const candidate=stage.purpose==='compression'?(op.tasks.find(t=>t.stageId===stage.stageId.replace('compression','summary')&&t.overflow)||(stage.stageId==='compression-before'?op.reusedOverflow:null)):null;
  if(stage.purpose==='compression'&&!candidate){op.skipped.push(stage.stageId);await save(s);return;}
  const source=stage.purpose==='memory'?memorySource(s):stage.purpose==='story'?null:summaryUpdateSource(s);
  const selection=await choose(stage.purpose==='memory'?s.memoryTasks.config.model:stage.purpose==='story'?s.modelState.current:s.rollingSummary.config.model,s.id);
  const story=stage.purpose==='story'?await buildStory(s,op.input.input,memoryContextPolicy(s,op.authorization)):null;
  const messages=stage.purpose==='memory'?[{role:'system',content:MEMORY_INSTRUCTION},{role:'user',content:JSON.stringify({entries:source.entries,turns:source.turns})}]:stage.purpose==='story'?story.messages:stage.purpose==='compression'?compressionMessages(compressionEvidence(candidate).raw):[{role:'system',content:SUMMARY_INSTRUCTION},{role:'user',content:JSON.stringify({previousSummary:source.previousSummary,completedTurns:source.source})}];
  if(candidate&&(canonical(source)!==canonical(candidate.source)||canonical(selection)!==canonical(candidate.selection)))throw fail('SUMMARY_COMPRESSION_SOURCE_CHANGED');
  const task={...stage,sessionId:s.id,requestId:identity(s.id,op.input.operationId,stage.stageId),selection,messages:structuredClone(messages),messagesHash:sha(canonical(messages)),source,sourceHash:sha(canonical(source)),story,lease:op.leases.find(l=>l.stageId===stage.stageId),status:'pending',output:null,...(candidate?{candidate:structuredClone(candidate)}:{})};
  op.tasks.push(task);await save(s);
  let result;try{result=await transport.execute(task,controller.signal,action=>guard(s,op,action));}catch{
   task.status='unknown';throw fail('MEMORY_TASK_RESULT_UNKNOWN');
  }
  task.output=structuredClone(result);
  // Raw and receipt land durably before validation/publication. A failed final
  // write remains pending on disk and can be reconciled without a fresh dispatch.
  await save(s);
  if(controller.signal.aborted){task.status=result?.dispatched&&!result?.cancelledKnown&&!result?.failureKnown&&result?.evidence?.record?.status!=='complete'?'unknown':'cancelled';throw fail('CANCELLED');}
  try{await finish(s,op,task,result,()=>op.cancelRequested||controller.signal.aborted);}catch(e){if(task.status==='pending')task.status=e.code==='MEMORY_AUTHORIZATION_CHANGED'?'revoked':e.code==='MEMORY_TASK_RECEIPT_INVALID'?'unknown':e.code?'failed':'unknown';throw e;}
 }
 async function release(op){op.releaseErrors=[];const group=await transport.budget.recover(reservationId(op.sessionId,op.input.operationId));for(const lease of group?.leases||op.leases)try{await transport.budget.release(lease);}catch{op.releaseErrors.push(lease.index);}}
 async function work(id,input,kind,front,entry,admission=input){
  return store.exclusive(id,async()=>{
   const s=await read(id),q=s.memoryTasks,prior=Object.hasOwn(q.operations,input.operationId)?q.operations[input.operationId]:null;
   if(prior){if(prior.inputHash!==sha(canonical(input)))throw fail('MEMORY_TASK_OPERATION_COLLISION');const result={...structuredClone(prior),replayed:true};front.resolve(result);return result;}
   unconfirmed(s);const a=await authorizations(id),policy=memoryContextPolicy(s,a);
   if(s.revision!==admission.expectedSessionRevision||s.memoryPalace.revision!==admission.expectedMemoryRevision||policy.revision!==admission.expectedContextPolicyRevision)throw fail('MEMORY_TASK_REVISION_CONFLICT');
   if(kind==='send'&&q.blocked){const old=q.operations[q.blocked];if(old.blockedPurpose==='memory'?a.memory.enabled:a.summary.enabled)throw fail('MEMORY_UPDATE_REQUIRES_EXPLICIT_ACTION');}
   if(!a.memory.enabled){
    if(kind!=='send')throw fail('PLUGIN_NOT_AUTHORIZED');if(!legacySend)throw fail('MEMORY_LEGACY_PATH_NOT_CONNECTED');return {delegateLegacy:true,session:s,input};
   }
   if(kind==='send'&&(!buildStory||!commitStory))throw fail('MEMORY_ASSEMBLY_NOT_CONNECTED');
   memorySource(s);await choose(q.config.model,id);if(a.summary.enabled)await choose(s.rollingSummary.config.model,id);
   let plan=memoryPlan(s,a,kind),reusedOverflow=null;
   if(kind==='update'&&a.summary.enabled&&summaryUpdateSource(s).needsUpdate&&q.blocked){
    const source=summaryUpdateSource(s),old=q.operations[q.blocked];
    reusedOverflow=[...old.tasks].reverse().map(t=>t.purpose==='compression'?t.candidate:t).find(t=>t?.purpose==='summary'&&t.overflow&&t.status==='complete'&&canonical(t.source)===canonical(source)&&canonical(t.selection)===canonical(s.rollingSummary.config.model))||null;
    if(reusedOverflow){compressionEvidence(reusedOverflow);reusedOverflow=structuredClone(reusedOverflow);plan=plan.filter(p=>p.stageId!=='summary-before');}
   }
   if(Object.keys(q.operations).length>=10000)throw fail('MEMORY_OPERATION_LIMIT');
   const op={sessionId:id,input:structuredClone(input),inputHash:sha(canonical(input)),kind,authorization:a,policyRevision:policy.revision,plan,reusedOverflow,status:'pending',tasks:[],leases:[],skipped:[],blockedPurpose:null,cancelRequested:false,error:null};
   q.operations[input.operationId]=op;await save(s);const controller=new AbortController();store.running.set(id,controller);let preserveDisk=false;
   try{
    if(plan.length){op.leases=await transport.budget.reserve(reservationId(id,input.operationId),id,plan);await save(s);}
    for(const stage of plan){
     await step(s,op,stage,controller);
     if(stage.purpose==='story'){entry.phase='background';front.resolve({...structuredClone(op),background:true});}
    }
    op.status='complete';q.blocked=null;s.memoryTasks.blocked=null;
   }catch(e){
    preserveDisk=!!e.preserveDisk;op.cancelRequested ||= controller.signal.aborted;const last=op.tasks.at(-1);op.status=last?.status==='unknown'?'unknown':last?.status==='revoked'||e.code==='MEMORY_AUTHORIZATION_CHANGED'?'revoked':controller.signal.aborted?'cancelled':'failed';
    op.error=e.code||'MEMORY_TASK_PERSISTENCE_FAILED';
    const nextStage=plan.find(p=>!op.skipped.includes(p.stageId)&&!op.tasks.some(t=>t.stageId===p.stageId&&t.status==='complete'));
    op.blockedPurpose=nextStage?.purpose==='story'?null:nextStage?.purpose==='memory'?'memory':nextStage?'summary':null;
    if(op.blockedPurpose&&e.code!=='BUDGET_EXHAUSTED')s.memoryTasks.blocked=input.operationId;
   }finally{
    try{await release(op);}finally{store.running.delete(id);}
   }
   if(preserveDisk)throw fail('MEMORY_PUBLICATION_UNCERTAIN');
   if(settleStory&&op.tasks.some(t=>t.purpose==='story'&&t.status==='complete')&&op.status!=='unknown')await settleStory(s,op);
   await save(s);const result=structuredClone(op);front.resolve(result);return result;
  });
 }
 function launch(id,input,kind,admission=input){
  const front=deferred(),entry={phase:'foreground',promise:null};
  entry.promise=work(id,input,kind,front,entry,admission).then(async result=>{if(result?.delegateLegacy){const legacy=await legacySend(result.session,result.input);front.resolve(legacy);return legacy;}return result;});active.set(id,entry);
  entry.promise.catch(front.reject).finally(()=>{if(active.get(id)===entry)active.delete(id);}).catch(()=>{});
  return {front:front.promise,done:entry.promise};
 }
 const api={
  async status(id){const s=await read(id),a=await authorizations(id);return {sessionRevision:s.revision,memoryRevision:s.memoryPalace.revision,state:structuredClone(s.memoryTasks),effectivePolicy:memoryContextPolicy(s,a),plan:memoryPlan(s,a),maxCalls:a.memory.enabled?memoryPlan(s,a).length:null};},
  async catalog(id){await read(id);const ticket=await plugins.invoke({pluginId:MEMORY_PALACE_ID,sessionId:id,capability:'ui.memory-action'});const list=await transport.catalog({sessionId:id});await plugins.validateResult(ticket);return list;},
  async configure(id,input){
   if(!exact(input,['operationId','expectedSessionRevision','expectedMemoryRevision','selectionId','catalogRevision'])||!operationId(input.operationId)||!revision(input.expectedSessionRevision)||!revision(input.expectedMemoryRevision)||!validSettings({selectionId:input.selectionId,catalogRevision:input.catalogRevision}))throw fail('MEMORY_CONFIGURATION_INVALID',400);
   input=structuredClone(input);return store.exclusive(id,async()=>{
    const s=await read(id),q=s.memoryTasks,prior=Object.hasOwn(q.settings,input.operationId)?q.settings[input.operationId]:null;
    if(prior){if(prior.inputHash!==sha(canonical(input)))throw fail('MEMORY_OPERATION_COLLISION');return {...structuredClone(prior),replayed:true};}
    unconfirmed(s);if(s.revision!==input.expectedSessionRevision||s.memoryPalace.revision!==input.expectedMemoryRevision)throw fail('MEMORY_CONFIGURATION_CONFLICT');
    const ticket=await plugins.invoke({pluginId:MEMORY_PALACE_ID,sessionId:id,capability:'session.memory.configure',input:{selectionId:input.selectionId,catalogRevision:input.catalogRevision}});
    const selected=(await transport.catalog({sessionId:id})).find(m=>m.available&&m.selectionId===input.selectionId&&m.selectionRevision===input.catalogRevision);if(!selected)throw fail('MODEL_SELECTION_UNAVAILABLE');
    const model=controlledChoice(selected),op={input,inputHash:sha(canonical(input)),status:'pending'};
    const staged=structuredClone(s);staged.memoryTasks.settings[input.operationId]=op;staged.memoryTasks.pendingSettings=input.operationId;staged.memoryTasks=sealMemoryTasks(staged.memoryTasks);
    q.config={model,revision:q.config.revision+1};q.settings[input.operationId]={...op,status:'complete'};s.memoryPalace=sealMemory({...s.memoryPalace,revision:s.memoryPalace.revision+1});s.revision++;
    await plugins.commitResult(ticket,async()=>{await store.write(staged);await save(s);});return structuredClone(q.settings[input.operationId]);
   });
  },
  async recoverSetting(id,key){
   if(!operationId(key))throw fail('MEMORY_OPERATION_INVALID');return store.exclusive(id,async()=>{const s=await read(id),q=s.memoryTasks,o=Object.hasOwn(q.settings,key)?q.settings[key]:null;if(!o)return {status:'not-found'};if(o.status==='pending'){o.status='not-applied';q.pendingSettings=null;await save(s);}return structuredClone(o);});
  },
  async send(id,input){
   validate(input,'send');input=structuredClone(input);let admission=input;const previous=active.get(id);
   if(previous){
    if(previous.phase!=='background')throw fail('SESSION_BUSY');const before=await api.status(id);
    if(before.sessionRevision!==input.expectedSessionRevision||before.memoryRevision!==input.expectedMemoryRevision||before.effectivePolicy.revision!==input.expectedContextPolicyRevision)throw fail('MEMORY_TASK_REVISION_CONFLICT');
    const a=await authorizations(id);await previous.promise;if(canonical(await authorizations(id))!==canonical(a))throw fail('MEMORY_AUTHORIZATION_CHANGED');
    const after=await api.status(id);admission={...input,expectedSessionRevision:after.sessionRevision,expectedMemoryRevision:after.memoryRevision,expectedContextPolicyRevision:after.effectivePolicy.revision};
   }
   return launch(id,input,'send',admission).front;
  },
  async update(id,input){validate(input,'update');if(active.has(id))throw fail('SESSION_BUSY');return launch(id,structuredClone(input),'update').front;},
  async wait(id){return active.get(id)?.promise||null;},
  async recover(id,key){
   if(!operationId(key))throw fail('MEMORY_OPERATION_INVALID');if(active.has(id))throw fail('SESSION_BUSY');return store.exclusive(id,async()=>{
    const s=await read(id),q=s.memoryTasks,op=Object.hasOwn(q.operations,key)?q.operations[key]:null;if(!op)return {status:'not-found'};if(!pending(op))return structuredClone(op);
    for(const task of op.tasks){if(!pending(task))continue;
     let result;try{result=await transport.recover(task);}catch{task.status='unknown';continue;}
     try{await finish(s,op,task,result);}catch(e){if(e.preserveDisk)throw fail('MEMORY_PUBLICATION_UNCERTAIN');task.output=structuredClone(result);if(pending(task))task.status=e.code==='MEMORY_AUTHORIZATION_CHANGED'?'revoked':e.code==='MEMORY_TASK_RECEIPT_INVALID'?'unknown':result?.dispatched===false||result?.failureKnown||result?.evidence?.record?.status==='complete'?'failed':'unknown';}
    }
    await release(op);
    const done=op.plan.every(p=>op.skipped.includes(p.stageId)||op.tasks.some(t=>t.stageId===p.stageId&&t.status==='complete'));
    op.status=op.tasks.some(t=>t.status==='unknown')?'unknown':op.tasks.some(t=>t.status==='revoked')?'revoked':done?'complete':'failed';
    if(done)s.memoryTasks.blocked=null;else{const missing=op.plan.find(p=>!op.skipped.includes(p.stageId)&&!op.tasks.some(t=>t.stageId===p.stageId&&t.status==='complete'));op.blockedPurpose=missing?.purpose==='memory'?'memory':missing?.purpose==='story'?null:'summary';if(op.blockedPurpose)s.memoryTasks.blocked=key;}
    if(settleStory&&op.tasks.some(t=>t.purpose==='story'&&t.status==='complete')&&op.status!=='unknown')await settleStory(s,op);
    await save(s);return structuredClone(op);
   });
  }
 };
 return api;
}
