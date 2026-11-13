import {canonical,sha,fail} from '../plugins/catalog.mjs';
import {freezeContext,validateContext,validatePlan,validateResult} from '../authoring/comparison-contract.mjs';
import {controlEvidence} from '../authoring/controlled-receipt.mjs';
import {wrapParameterChoice} from './card-generation.mjs';
import {sealMemoryTasks} from './memory-task-state.mjs';
import {overflow,compressionMessages,compressionEvidence,COMPRESSION_MAX} from './summary-compression.mjs';
const clone=structuredClone,digest=v=>sha(canonical(v)),same=(a,b)=>canonical(a)===canonical(b);
const wireHash=t=>sha(JSON.stringify({sessionId:t.sessionId,requestId:t.requestId,selectionId:t.selection.selectionId,selectionRevision:t.selection.selectionRevision,messages:t.messages,parameters:t.selection.parameters}));
// One adapter for all six existing storage/assembly profiles. No card-name branch.
export function createCardComparisonAdapter({store,profile,plugins,transport,control}){
 const m=profile.state.memory,summary=profile.state.summary,settlements=new Map();
 const auth=async id=>{const context=await store.memoryAuthorizations(id),grant=await plugins.comparisonAuthorization(id);if(!grant.enabled)throw fail('COMPARISON_AUTHORIZATION_REQUIRED');return {context,grant,revision:digest({context,grant})};};
 const read=async id=>{const s=await store.read(id);await store.requireRuntime(s);m.requireMemoryTasks(s);for(const o of Object.values(s.memoryTasks.operations)){const c=o.comparison;if(!c)continue;validatePlan(c.plan);if(c.plan.intent.sessionId!==s.id||c.plan.intent.operationId!==o.input.operationId||!Array.isArray(c.commits)||c.commits.length>12)throw fail('COMPARISON_HOST_RECORD_INVALID');if(c.context)validateContext(c.context);const ids=new Set();for(const v of c.commits){const {hash,...body}=v;if(hash!==digest(body)||ids.has(v.operationId))throw fail('COMPARISON_HOST_RECORD_INVALID');ids.add(v.operationId);}}return s;};
 const save=async s=>{s.memoryTasks=sealMemoryTasks(s.memoryTasks);await store.write(s);return s;};
 const find=(s,batch)=>Object.values(s.memoryTasks.operations).find(o=>o.comparison?.plan.id===batch);
 async function start(plan){validatePlan(plan);const s=await read(plan.intent.sessionId),old=find(s,plan.id);if(old){if(!same(old.comparison.plan,plan))throw fail('COMPARISON_OPERATION_COLLISION');return {s,op:old};}
  await store.requireSelectable(s);const a=await auth(s.id);if(s.revision!==plan.state.contextRevision||digest(s)!==plan.state.sourceHash||a.revision!==plan.state.authorizationRevision)throw fail('COMPARISON_CONTEXT_CHANGED');
  if(Object.hasOwn(s.memoryTasks.operations,plan.intent.operationId))throw fail('COMPARISON_OPERATION_COLLISION');const policy=m.memoryContextPolicy(s,a.context),input={operationId:plan.intent.operationId,expectedSessionRevision:s.revision,expectedMemoryRevision:s.memoryPalace.revision,expectedContextPolicyRevision:policy.revision,input:plan.intent.input},native=m.memoryPlan(s,a.context);
  const generation=await transport.freeze(s,input,native.map(t=>t.purpose));const op={sessionId:s.id,input,inputHash:digest(input),kind:'send',authorization:a.context,policyRevision:policy.revision,plan:native,reusedOverflow:null,status:'pending',tasks:[],leases:[],skipped:[],blockedPurpose:null,cancelRequested:false,error:null,generation,comparison:{plan:clone(plan),context:null,commits:[]}};
  s.memoryTasks.operations[input.operationId]=op;s.draft=input.input;await save(s);return {s,op:find(s,plan.id)};
 }
 function task(s,op,stage,choice=null){let t=op.tasks.find(t=>t.stageId===stage);if(t)return t;const selected=op.plan.find(t=>t.stageId===stage);if(!selected)throw fail('COMPARISON_STAGE_INVALID');const p=selected.purpose;
  const candidate=p==='compression'?op.tasks.find(t=>t.stageId===stage.replace('compression','summary')&&t.overflow):null;if(p==='compression'&&!candidate)return null;
  const source=p==='memory'?m.memorySource(s):p==='story'?null:summary.summaryUpdateSource(s),selection=choice??op.generation.choices[p==='compression'?'summary':p];if(!selection)throw fail('PARAMETER_MODEL_REQUIRED');
  const story=p==='story'?profile.context.memory(s,op.input.input,m.memoryContextPolicy(s,op.authorization)):null;
  const messages=p==='story'?story.messages:p==='memory'?[{role:'system',content:m.MEMORY_INSTRUCTION},{role:'user',content:JSON.stringify({entries:source.entries,turns:source.turns})}]:p==='compression'?compressionMessages(compressionEvidence(candidate).raw):[{role:'system',content:summary.SUMMARY_INSTRUCTION},{role:'user',content:JSON.stringify({previousSummary:source.previousSummary,completedTurns:source.source})}];
  t={...selected,sessionId:s.id,requestId:digest({sessionId:s.id,operationId:op.input.operationId,stage}),selection:clone(selection),messages:clone(messages),messagesHash:digest(messages),source,sourceHash:digest(source),story,lease:null,status:'pending',output:null,...(candidate?{candidate:clone(candidate)}:{})};op.tasks.push(t);return t;
 }
 const native=t=>({id:t.requestId,sessionId:t.sessionId,kind:t.purpose,messages:t.messages,parameters:t.selection.parameters,modelChoice:t.selection.parameterChoice});
 function projection(t,p){validateResult(p.result,p.request);const e=p.result.receipt.evidence;controlEvidence(e,p.request);if(e.status!=='completed'||e.receipt.finishReason!=='stop'||p.request.sessionId!==t.sessionId||!same(p.request.modelChoice,t.selection.parameterChoice)||!same(p.request.messages,t.messages)||p.request.kind!==t.purpose||t.purpose!=='story'&&p.request.nativeRequestHash!==digest(native(t)))throw fail('COMPARISON_RESULT_NOT_ADOPTABLE');
  return {raw:e.raw,finishReason:e.receipt.finishReason,dispatched:true,evidence:{kind:'comparison-card-control',comparison:{request:clone(p.request),result:clone(p.result)},record:{status:'complete',sessionId:t.sessionId,requestId:t.requestId,requestedModel:t.selection.model,actualModel:e.receipt.actualModel,parameters:clone(t.selection.parameters),requestHash:wireHash(t),rawHash:sha(e.raw),controlRequestHash:e.receipt.requestHash,sourceRequestHash:e.receipt.sourceRequestHash,retries:0}}};
 }
 function committed(op,p){const c=op?.comparison.commits.find(c=>c.operationId===p.operationId);if(c&&!same(c.payloadHash,digest(p)))throw fail('COMPARISON_OPERATION_COLLISION');return c?clone(c):null;}
 async function receipt(s,op,p){const body={operationId:p.operationId,payloadHash:digest(p),contextRevision:s.revision},c={...body,hash:digest(body)};op.comparison.commits.push(c);await save(s);return c;}
 const api={
  comparisonFutureStatus:id=>store.comparisonFutureStatus(id),comparisonFutureOperation:(id,op)=>store.comparisonFutureOperation(id,op),configureFutureStory:(i,c)=>store.configureFutureStory(i,c),
  async comparisonInspect(id){const s=await read(id);await store.requireSelectable(s);const a=await auth(id),p=m.memoryPlan(s,a.context);return {contextRevision:s.revision,authorizationRevision:a.revision,sourceHash:digest(s),summaryBefore:p.some(t=>t.stageId==='summary-before'),summaryAfter:p.some(t=>t.stageId==='summary-after'),memoryAfter:p.some(t=>t.purpose==='memory')};},
  async comparisonOptions(id){const s=await read(id);await auth(id);const grant=await plugins.parameterAuthorization(id),base=s.modelState.current.parameterChoice;return {catalog:await control.parameterCatalog(),defaultChoice:grant.enabled?s.generationParameters?.values?.story??base:base};},
  async comparisonValidate(choice){await control.parameterCatalog();return control.validate(choice);},comparisonResolve:selection=>control.selectParameters(selection),
  async comparisonGuard(id,revision,action){const a=await auth(id);if(a.revision!==revision)throw fail('COMPARISON_AUTHORIZATION_CHANGED');return plugins.guardComparison(id,a.grant.revision,a.context,action);},
  comparisonFreeze({plan}){return store.exclusive(plan.intent.sessionId,async()=>{const {s,op}=await start(plan);if(op.comparison.context)return clone(op.comparison.context);const t=task(s,op,'story',wrapParameterChoice(plan.intent.candidates[0].choice,{revision:op.generation.revision,authorization:op.generation.authorization,values:op.generation.values}));
   const context=freezeContext({contextRevision:s.revision,source:{input:plan.intent.input,history:clone(s.turns),requirements:s.requirements??null,variables:null,summary:clone(t.story.contextPolicy.summarySnapshot),memory:clone(t.story.memoryPolicy.usedSnapshot),worldbook:{contextPolicy:clone(t.story.contextPolicy)}},messages:clone(t.messages)});op.comparison.context=context;await save(s);return clone(context);});},
  comparisonPrepare({plan,stage}){return store.exclusive(plan.intent.sessionId,async()=>{const {s,op}=await start(plan),t=task(s,op,stage.name);if(!t)return null;await save(s);return {id:stage.id,operationId:stage.id,sessionId:s.id,kind:stage.purpose,batchId:plan.id,nativeRequestHash:digest(native(t)),messages:clone(t.messages),messagesHash:t.messagesHash,modelChoice:clone(t.selection.parameterChoice),parameters:clone(t.selection.parameters)};});},
  async comparisonLookup(p){const s=await read(p.sessionId);return committed(find(s,p.batchId),p);},
  comparisonApply(p){return store.exclusive(p.sessionId,async()=>{const s=await read(p.sessionId),op=find(s,p.batchId);if(!op)throw fail('COMPARISON_HOST_RECORD_MISSING');const prior=committed(op,p);if(prior)return prior;
   if(p.step==='variables'||p.step==='cg'){if(!op.tasks.some(t=>t.purpose==='story'&&t.status==='complete'))throw fail('COMPARISON_ADOPTION_CONFLICT');return receipt(s,op,p);}
   const name=p.step==='formal'?'story':p.step,t=op.tasks.find(t=>t.stageId===name);if(!t)throw fail('COMPARISON_BACKGROUND_NOT_READY');
   if(p.step==='formal'){
    if(p.expectedContextRevision!==op.comparison.context.contextRevision||!same(p.context,op.comparison.context)||p.request.contextHash!==op.comparison.context.hash||!op.comparison.plan.intent.candidates.some(c=>same(c.choice,p.request.modelChoice)&&p.request.id===op.comparison.plan.stages.find(t=>t.candidateId===c.id)?.id))throw fail('COMPARISON_CONTEXT_CHANGED');
    if(t.status!=='complete')t.selection=wrapParameterChoice(p.request.modelChoice,{revision:op.generation.revision,authorization:op.generation.authorization,values:op.generation.values});
   }
   const result=projection(t,p);if(t.status==='complete'){if(!same(t.output,result))throw fail('COMPARISON_ADOPTION_CONFLICT');return receipt(s,op,p);}
   if(t.purpose==='story')await profile.context.commitMemory(s,t,result);
   else if(t.purpose==='memory')m.publishMemoryResult(s,t,result);
   else if(t.purpose==='summary'&&overflow(result.raw,result.finishReason))t.overflow=true;
   else {if(t.purpose==='compression'&&(Array.from(result.raw).length>COMPRESSION_MAX||!result.raw.trim()))throw fail('SUMMARY_COMPRESSION_REJECTED');const v=summary.modelSummaryVersion(s,{raw:result.raw,finishReason:result.finishReason,model:t.selection,receipt:{requestId:t.requestId,requestHash:wireHash(t),responseHash:sha(result.raw)},expectedActiveVersionId:t.source.previousVersionId,targetThrough:t.source.targetThrough,...(t.purpose==='compression'?{compression:compressionEvidence(t.candidate)}:{})});s.rollingSummary=summary.withSummaryVersion(s,v);s.revision++;t.versionId=v.id;}
   t.output=result;t.status='complete';s.draft='';return receipt(s,op,p);
  });},
  comparisonDraft(p,plan){return store.exclusive(p.sessionId,async()=>{const {s,op}=await start(plan),prior=committed(op,p);if(prior)return prior;if(op.tasks.some(t=>t.purpose==='story'&&t.status==='complete')||p.expectedContextRevision!==(op.comparison.context?.contextRevision??plan.state.contextRevision))throw fail('COMPARISON_ADOPTION_CONFLICT');s.draft=p.input;return receipt(s,op,p);});},
  async comparisonSettled(batch){
   if(batch.status!=='settled')return;
   const key=batch.plan.id,id=batch.plan.intent.sessionId;
   if(settlements.has(key))return settlements.get(key);
   const pending=(async()=>{
    // Polling a completed older batch must not take the lock needed by a new
    // adoption. Still validate the runtime and durable record on every read.
    if(find(await read(id),key)?.status!=='pending')return;
    return store.exclusive(id,async()=>{
     // The unlocked snapshot may be stale. Only this lock-protected read can
     // decide whether to settle; never write that earlier snapshot back.
     const s=await read(id),op=find(s,key);if(!op||op.status!=='pending')return;
     op.status=batch.decision?.kind==='adopt'?'complete':'cancelled';for(const t of op.tasks)if(t.status==='pending')t.status='cancelled';
     if(op.status==='complete'){op.skipped=op.plan.filter(p=>!op.tasks.some(t=>t.stageId===p.stageId)).map(p=>p.stageId);profile.context.settleMemory(s,op);}await save(s);
    });
   })();
   // Concurrent GET/watch/adopt completion shares one final save. A rejected
   // save is not cached as success: explicit original-ID recovery can resume.
   settlements.set(key,pending);try{return await pending;}finally{settlements.delete(key);}
  }
 };
 return api;
}
