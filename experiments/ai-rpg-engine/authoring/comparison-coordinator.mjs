import {SessionRecords,SESSION_RECORD_LIMIT} from './session-storage.mjs';
import {canonical,keys,isId,isHash,fail} from './contracts.mjs';
import {comparisonLedgerPort} from './comparison-ledger.mjs';
import {COMPARISON_FORMAT,COMPARISON_LIMITS,clone,digest,same,integer,terminal,batchId,stageId,comparisonIntent,comparisonPlan,validatePlan,freezeContext,validateContext,candidateRequest,validateRequest,validateResult,resultEnvelope} from './comparison-contract.mjs';
const taskStates=['queued','prepared','dispatching','unknown','completed','failed','cancelled','not_dispatched','released'];
const code=e=>typeof e?.code==='string'&&/^[A-Z0-9_]{1,100}$/.test(e.code)?e.code:'COMPARISON_INTERRUPTED';
const unfinished=b=>b.status!=='settled';
const running=t=>['prepared','dispatching','unknown'].includes(t.state);
function validateSession(s){
 keys(s,['format','id','revision','batches']);if(s.format!==COMPARISON_FORMAT||!isId(s.id)||!integer(s.revision)||!Array.isArray(s.batches)||s.batches.length>COMPARISON_LIMITS.batches)fail('COMPARISON_RECORD_INVALID');
 const ids=new Set();for(const b of s.batches){
  keys(b,['plan','revision','status','diagnostic','reservations','context','tasks','decision','steps','stopped','stopOperations','events']);validatePlan(b.plan);
  if(b.plan.intent.sessionId!==s.id||ids.has(b.plan.id)||!integer(b.revision)||!['reserving','preparing','generating','awaiting-choice','adopting','blocked','settled'].includes(b.status)||typeof b.stopped!=='boolean'||
   !(b.diagnostic===null||typeof b.diagnostic==='string')||!Array.isArray(b.tasks)||b.tasks.length!==b.plan.stages.length)fail('COMPARISON_RECORD_INVALID');ids.add(b.plan.id);
  keys(b.reservations,['local','control']);if(Object.values(b.reservations).some(v=>typeof v!=='boolean'))fail('COMPARISON_RECORD_INVALID');
  if(b.context){validateContext(b.context);if(b.context.source.input!==b.plan.intent.input)fail('COMPARISON_RECORD_INVALID');}
  for(const [i,t]of b.tasks.entries()){keys(t,['id','state','request','result','applied']);if(t.id!==b.plan.stages[i].id||!taskStates.includes(t.state)||typeof t.applied!=='boolean')fail('COMPARISON_RECORD_INVALID');
   if(t.request)validateRequest(t.request,b.plan,b.plan.stages[i]);
   if(running(t)&&!t.request||t.result&&!t.request||t.result&&!same(validateResult(t.result,t.request),t.result)||t.result&&t.state!==t.result.status||['completed','failed','cancelled','not_dispatched'].includes(t.state)&&!t.result)fail('COMPARISON_RECORD_INVALID');
   if(b.plan.stages[i].phase==='candidate'&&t.request&&(!b.context||!same(t.request,candidateRequest(b.plan,b.plan.stages[i],b.context))))fail('COMPARISON_RECORD_INVALID');
  }
  if(!Array.isArray(b.events)||b.events.length>1000||b.events.some(e=>!['failure','recovered'].includes(e.type)||typeof e.code!=='string'||!(/^[A-Z0-9_]{1,100}$/).test(e.code)||!(e.stageId===null||b.plan.stages.some(s=>s.id===e.stageId))))fail('COMPARISON_RECORD_INVALID');
  if(!Array.isArray(b.stopOperations)||b.stopOperations.length>1000||new Set(b.stopOperations.map(o=>o.operationId)).size!==b.stopOperations.length)fail('COMPARISON_RECORD_INVALID');
  if(!Array.isArray(b.steps)||b.steps.some(x=>!['formal','variables','cg','draft'].includes(x.name)||!isId(x.operationId)||!(x.receipt===null||isHash(x.receipt.hash))))fail('COMPARISON_RECORD_INVALID');
  if(b.decision){keys(b.decision,['kind','intent']);if(!['adopt','discard'].includes(b.decision.kind))fail('COMPARISON_RECORD_INVALID');decisionIntent(b.decision.intent,b.decision.kind);}
 }
 if(s.batches.filter(unfinished).length>1)fail('COMPARISON_SESSION_BUSY');return s;
}
function decisionIntent(v,kind){
 keys(v,['operationId','expectedContextRevision',...(kind==='adopt'?['candidateId']:[])]);
 if(!isId(v.operationId)||!integer(v.expectedContextRevision)||kind==='adopt'&&!isId(v.candidateId))fail('COMPARISON_DECISION_INVALID');return clone(v);
}
// Trusted host adapter only. Its guard and apply/restoreDraft operations must be
// atomic against formal session writes. Every mutating hook is idempotent by ID.
// No routes or browser-provided functions are installed by this core module.
export class ComparisonCoordinator{
 #records;#budgets;#host;#provider;#fault;#queue=Promise.resolve();#jobs=new Map();#adoptions=new Map();#closed=false;#closing=false;#timeout;
 constructor(records,options){this.#timeout=options.timeoutMs??250000;if(!integer(this.#timeout)||this.#timeout<10||this.#timeout>300000)fail('COMPARISON_TIMEOUT_INVALID');this.#records=records;this.#budgets={local:comparisonLedgerPort(options.localBudget),control:comparisonLedgerPort(options.controlBudget)};
  for(const k of ['inspect','validateChoice','freeze','prepareTask','guard','apply','lookupCommit','restoreDraft'])if(typeof options.host?.[k]!=='function')fail('COMPARISON_HOST_REQUIRED');
  for(const k of ['dispatch','lookup'])if(typeof options.provider?.[k]!=='function')fail('COMPARISON_PROVIDER_REQUIRED');
  this.#host=Object.freeze(Object.fromEntries(['inspect','validateChoice','freeze','prepareTask','guard','apply','lookupCommit','restoreDraft'].map(k=>[k,options.host[k].bind(options.host)])));
  this.#provider=Object.freeze(Object.fromEntries(['dispatch','lookup'].map(k=>[k,options.provider[k].bind(options.provider)])));this.#fault=options.fault??(()=>{});
 }
 static async open(options){for(const [key,level]of [['localBudget','local'],['controlBudget','control']]){if((await comparisonLedgerPort(options[key]).status()).level!==level)fail('COMPARISON_LEDGER_LEVEL_INVALID');}const records=await SessionRecords.open(options.directory,{recoverDeadOwner:options.recoverDeadOwner,fault:options.storageFault,scope:'comparison-coordinator'});try{return new ComparisonCoordinator(records,options);}catch(e){await records.close();throw e;}}
 #serial(fn){if(this.#closed)fail('COMPARISON_CLOSED');const p=this.#queue.then(fn);this.#queue=p.catch(()=>{});return p;}
 async #load(session){if(!isId(session))fail('COMPARISON_SESSION_INVALID');return await this.#records.get(session,validateSession)??{hash:null,data:{format:COMPARISON_FORMAT,id:session,revision:0,batches:[]}};}
 #find(r,id){const b=r.data.batches.find(b=>b.plan.id===id);if(!b)fail('COMPARISON_NOT_FOUND');return b;}
 async #save(r){r.data.revision++;validateSession(r.data);return this.#records.put(r.data.id,r.hash,r.data);}
 #edit(session,id,action){return this.#serial(async()=>{const r=await this.#load(session),b=this.#find(r,id),before=digest(b);await action(b);if(digest(b)===before)return clone(b);b.revision++;await this.#save(r);return clone(b);});}
 read(session,id){return this.#serial(async()=>clone(this.#find(await this.#load(session),id)));}
 list(session){return this.#serial(async()=>clone((await this.#load(session)).data.batches));}
 assertIdle(session){return this.#serial(async()=>{if((await this.#load(session)).data.batches.some(unfinished))fail('COMPARISON_SESSION_BUSY');return true;});}
 async preflight(value){const intent=comparisonIntent(value);await this.assertIdle(intent.sessionId);const state=await this.#host.inspect(intent.sessionId),plan=comparisonPlan(intent,state);
  for(const c of intent.candidates)await this.#host.validateChoice(c.choice);
  const budgets=await Promise.all(Object.values(this.#budgets).map(b=>b.status()));if(budgets.some(b=>b.remaining<plan.maximumCalls))fail('COMPARISON_BUDGET_EXHAUSTED');
  return {plan,maximumCalls:plan.maximumCalls,dispatched:false};}
 async create(value){
  if(this.#closing)fail('COMPARISON_CLOSED');
  const intent=comparisonIntent(value),session=intent.sessionId,id=batchId(session,intent.operationId);
  const existing=await this.list(session),prior=existing.find(b=>b.plan.id===id);if(prior){if(!same(prior.plan.intent,intent))fail('COMPARISON_OPERATION_COLLISION');return prior;}
  const {plan}=await this.preflight(intent);
  let created=false;await this.#serial(async()=>{const r=await this.#load(session),prior=r.data.batches.find(b=>b.plan.id===id);if(prior){if(!same(prior.plan.intent,intent))fail('COMPARISON_OPERATION_COLLISION');return;}if(r.data.batches.some(unfinished))fail('COMPARISON_SESSION_BUSY');
   if(r.data.batches.length>=COMPARISON_LIMITS.batches||Buffer.byteLength(canonical(r.data))+COMPARISON_LIMITS.contextBytes+plan.maximumCalls*COMPARISON_LIMITS.replyBytes*3>SESSION_RECORD_LIMIT)fail('COMPARISON_RECORD_CAPACITY');
   r.data.batches.push({plan,revision:0,status:'reserving',diagnostic:null,reservations:{local:false,control:false},context:null,tasks:plan.stages.map(t=>({id:t.id,state:'queued',request:null,result:null,applied:false})),decision:null,steps:[],stopped:false,stopOperations:[],events:[]});await this.#save(r);created=true;
  });
  if(!created)return this.read(session,id);
  try{for(const [level,ledger]of Object.entries(this.#budgets)){await ledger.reserve(plan);await this.#edit(session,id,b=>{b.reservations[level]=true;});}
   await this.#edit(session,id,b=>{b.status='preparing';});
  }catch(e){await this.#edit(session,id,b=>{b.status='blocked';b.diagnostic=code(e);b.events.push({type:'failure',stageId:null,code:code(e)});}).catch(()=>{});
   if(e.code==='COMPARISON_BUDGET_EXHAUSTED'){await this.#releaseQueued(session,id);await this.#edit(session,id,b=>{b.stopped=true;});}return this.read(session,id);}
  this.#start(session,id);return this.read(session,id);
 }
 #start(session,id){if(this.#jobs.has(id))return;const job=this.#runBatch(session,id).catch(e=>this.#block(session,id,e));this.#jobs.set(id,job);job.finally(()=>this.#jobs.delete(id));}
 async #block(session,id,e){await this.#edit(session,id,b=>{b.status='blocked';b.diagnostic=code(e);b.events.push({type:'failure',stageId:null,code:code(e)});}).catch(()=>{});}
 async wait(session,id){while(this.#jobs.has(id)||this.#adoptions.has(id))await Promise.all([this.#jobs.get(id),this.#adoptions.get(id)].filter(Boolean));return this.read(session,id);}
 async #guard(b,fn){return this.#host.guard(b.plan.intent.sessionId,b.plan.state.authorizationRevision,fn);}
 async #runBatch(session,id){
  let b=await this.read(session,id);for(const stage of b.plan.stages.filter(s=>s.phase==='before')){if(!await this.#runStage(session,id,stage))return;}
  b=await this.read(session,id);if(b.stopped)return this.#finish(session,id);
  if(!b.context){const context=await this.#guard(b,()=>this.#host.freeze({plan:clone(b.plan),operationId:stageId(id,'freeze')})),frozen=freezeContext(context);
   if(frozen.source.input!==b.plan.intent.input)fail('COMPARISON_CONTEXT_INVALID');
   // All models see exactly the same bytes. Admission precedes any candidate call.
   for(const stage of b.plan.stages.filter(s=>s.phase==='candidate'))candidateRequest(b.plan,stage,frozen);
   await this.#edit(session,id,b=>{b.context=frozen;b.status='generating';});await this.#fault('afterFreeze',{session,id});
  }
  const workers=await Promise.allSettled([this.#worker(session,id),this.#worker(session,id)]);const failed=workers.find(r=>r.status==='rejected');if(failed)throw failed.reason;await this.#finish(session,id);
 }
 async #next(session,id){let next=null;await this.#edit(session,id,b=>{
   if(b.stopped||b.tasks.filter(running).length>=COMPARISON_LIMITS.parallel)return;
   const stage=b.plan.stages.find(s=>s.phase==='candidate'&&b.tasks.find(t=>t.id===s.id).state==='queued');if(!stage)return;
   const task=b.tasks.find(t=>t.id===stage.id);task.request=candidateRequest(b.plan,stage,b.context);task.state='prepared';next=clone(stage);
  });return next;}
 async #worker(session,id){for(;;){const stage=await this.#next(session,id);if(!stage)return;await this.#execute(session,id,stage);}}
 async #runStage(session,id,stage){
  let b=await this.read(session,id),task=b.tasks.find(t=>t.id===stage.id);if(task.state==='released')return true;
  if(task.result){await this.#settle(session,id,stage);return this.#applyTask(session,id,stage);}
  if(running(task))return false;
  if(b.stopped&&stage.phase==='before')return false;
  const request=await this.#guard(b,()=>this.#host.prepareTask({plan:clone(b.plan),stage:clone(stage),context:clone(b.context),decision:clone(b.decision),tasks:clone(b.tasks)}));
  if(request===null){if(stage.purpose!=='compression')fail('COMPARISON_TASK_REQUIRED');await this.#release(session,id,stage.id);return true;}
  validateRequest(request,b.plan,stage);await this.#edit(session,id,b=>{const t=b.tasks.find(t=>t.id===stage.id);t.request=clone(request);t.state='prepared';});
  await this.#execute(session,id,stage);return this.#applyTask(session,id,stage);
 }
 async #bounded(pending){let timer;try{return await Promise.race([pending,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Object.assign(Error('timeout'),{code:'COMPARISON_RESULT_UNKNOWN'})),this.#timeout);})]);}finally{clearTimeout(timer);}}
 async #execute(session,id,stage){
  let b=await this.read(session,id),task=b.tasks.find(t=>t.id===stage.id);if(task.state!=='prepared')return;let transportStarted=false;
  try{
   const started=await this.#guard(b,async()=>{
    const current=await this.read(session,id);if(current.stopped&&stage.phase==='candidate'){await this.#release(session,id,stage.id);return null;}
    await this.#host.validateChoice(task.request.modelChoice);
    await this.#edit(session,id,b=>{b.tasks.find(t=>t.id===stage.id).state='dispatching';});
    for(const ledger of Object.values(this.#budgets)){const claimed=await ledger.claim(id,stage.id,digest(task.request));if(!claimed.dispatch)fail('COMPARISON_REQUEST_ALREADY_CLAIMED');}
    await this.#fault('beforeDispatch',{session,id,stage:stage.name});
    // Start inside the authorization guard, await outside it for true concurrency.
    transportStarted=true;return {pending:this.#provider.dispatch(clone(task.request))};
   });if(!started)return;
   const result=validateResult(await this.#bounded(started.pending),task.request);
   await this.#edit(session,id,b=>{const t=b.tasks.find(t=>t.id===stage.id);t.result=clone(result);t.state=result.status;});
   await this.#fault('afterResultSaved',{session,id,stage:stage.name});
   if(result.status!=='unknown')await this.#settle(session,id,stage);
  }catch(e){
   // This proof is issued only when this process has not invoked transport at
   // all. Once dispatch() is entered, even a synchronous throw remains unknown.
   if(!transportStarted){try{
    const request=task.request,result=resultEnvelope(request,{status:'not_dispatched',receipt:{kind:'host-preflight',error:code(e),dispatched:false,retries:0,requestId:request.id,requestHash:digest(request),modelId:request.modelChoice.entry.modelId,parameters:clone(request.parameters),messagesHash:request.messagesHash}});
    await this.#edit(session,id,b=>{const t=b.tasks.find(t=>t.id===stage.id);t.result=result;t.state='not_dispatched';b.diagnostic=code(e);b.events.push({type:'failure',stageId:stage.id,code:code(e)});});await this.#settle(session,id,stage);return;
   }catch{ /* Failed persistence is reconciled, never inferred as a refund. */ }}
   await this.#edit(session,id,b=>{const t=b.tasks.find(t=>t.id===stage.id);if(!t.result&&!terminal(t.state))t.state='unknown';b.diagnostic=code(e);b.events.push({type:'failure',stageId:stage.id,code:code(e)});}).catch(()=>{});
  }
 }
 async #settle(session,id,stage){const b=await this.read(session,id),t=b.tasks.find(t=>t.id===stage.id);if(!t.result||t.result.status==='unknown')return;
  for(const ledger of Object.values(this.#budgets)){const g=await ledger.read(id),slot=g?.tasks.find(x=>x.id===stage.id);if(!slot)fail('COMPARISON_RESERVATION_MISSING');
   if(['reserved','released'].includes(slot.state)&&t.result.status==='not_dispatched'){if(slot.state==='reserved')await ledger.release(id,stage.id);}else await ledger.settle(id,stage.id,digest(t.request),t.result);}
 }
 async #applyTask(session,id,stage){const b=await this.read(session,id),t=b.tasks.find(t=>t.id===stage.id);if(t.state==='released')return true;
  if(t.state!=='completed'){await this.#block(session,id,{code:t.state==='unknown'?'COMPARISON_RESULT_UNKNOWN':'COMPARISON_BACKGROUND_FAILED'});return false;}
  if(t.applied)return true;const payload={operationId:stageId(id,'apply-'+stage.name),sessionId:session,batchId:id,step:stage.name,request:clone(t.request),result:clone(t.result)};
  const prior=await this.#host.lookupCommit(payload);const receipt=prior??await this.#guard(b,()=>this.#host.apply(payload));this.#checkCommit(receipt,payload);
  await this.#fault('afterTaskApply',{session,id,stage:stage.name});await this.#edit(session,id,b=>{b.tasks.find(t=>t.id===stage.id).applied=true;});return true;
 }
 #checkCommit(r,p){if(!r)fail('COMPARISON_COMMIT_RECEIPT_INVALID');keys(r,['operationId','payloadHash','contextRevision','hash']);const {hash,...body}=r;if(r.operationId!==p.operationId||r.payloadHash!==digest(p)||!integer(r.contextRevision)||hash!==digest(body))fail('COMPARISON_COMMIT_RECEIPT_INVALID');}
 async #release(session,id,taskId){for(const ledger of Object.values(this.#budgets)){const g=await ledger.read(id),t=g?.tasks.find(t=>t.id===taskId);if(t?.state==='reserved')await ledger.release(id,taskId);else if(t&&!['released','not_dispatched'].includes(t.state))fail('COMPARISON_DISPATCH_RETAINED');}
  await this.#edit(session,id,b=>{const t=b.tasks.find(t=>t.id===taskId);if(!t.result)t.state='released';});}
 async #releaseQueued(session,id,phase=null){const b=await this.read(session,id);for(const s of b.plan.stages){const t=b.tasks.find(t=>t.id===s.id);if((!phase||s.phase===phase)&&t.state==='queued')await this.#release(session,id,s.id);}}
 async stop(session,id,value){
  if(this.#closing)fail('COMPARISON_CLOSED');
  keys(value,['operationId','expectedRevision']);const {operationId,expectedRevision}=value;
  if(!isId(operationId)||!integer(expectedRevision))fail('COMPARISON_STOP_INVALID');
  await this.#edit(session,id,b=>{const old=b.stopOperations.find(o=>o.operationId===operationId);if(old){if(!same(old,value))fail('COMPARISON_OPERATION_COLLISION');return;}
   if(operationId===b.plan.intent.operationId||b.decision?.intent.operationId===operationId)fail('COMPARISON_OPERATION_COLLISION');if(b.revision!==expectedRevision)fail('COMPARISON_PROGRESS_CHANGED');if(b.stopOperations.length>=1000)fail('COMPARISON_RECORD_CAPACITY');b.stopOperations.push(clone(value));b.stopped=true;});
  await this.#releaseQueued(session,id,'candidate');await this.#finish(session,id);return this.read(session,id);
 }
 async adopt(session,id,value){if(this.#closing)fail('COMPARISON_CLOSED');const intent=decisionIntent(value,'adopt'),before=await this.read(session,id);
  const record=()=>this.#serial(async()=>{const r=await this.#load(session),b=this.#find(r,id);
   if(b.decision){if(b.decision.kind!=='adopt'||!same(b.decision.intent,intent))fail('COMPARISON_DECISION_CONFLICT');return;}
   if(intent.operationId===b.plan.intent.operationId||b.stopOperations.some(o=>o.operationId===intent.operationId))fail('COMPARISON_OPERATION_COLLISION');
   if(!b.context||b.context.contextRevision!==intent.expectedContextRevision)fail('COMPARISON_CONTEXT_CHANGED');
   const stage=b.plan.stages.find(t=>t.candidateId===intent.candidateId),task=b.tasks.find(t=>t.id===stage?.id);if(!task||task.state!=='completed')fail('COMPARISON_CANDIDATE_NOT_READY');
   b.decision={kind:'adopt',intent};b.status='adopting';b.steps=['formal','variables','cg'].map(name=>({name,operationId:stageId(id,'commit-'+name),receipt:null}));b.revision++;await this.#save(r);
  });
  if(before.decision)await record();else await this.#guard(before,record);
  await this.#fault('afterAdoptionDecision',{session,id});await this.#adoption(session,id);return this.read(session,id);
 }
 #adoption(session,id){if(this.#adoptions.has(id))return this.#adoptions.get(id).then(()=>this.#adoption(session,id));const job=this.#resumeAdoption(session,id).catch(e=>this.#block(session,id,e));this.#adoptions.set(id,job);job.finally(()=>this.#adoptions.delete(id));return job;}
 async #resumeAdoption(session,id){let b=await this.read(session,id);if(b.decision?.kind!=='adopt')return;
  const stage=b.plan.stages.find(t=>t.candidateId===b.decision.intent.candidateId),task=b.tasks.find(t=>t.id===stage.id);
  // A durable decision is not permission to commit a late revoked candidate.
  for(const step of b.steps){if(step.receipt)continue;
   const payload={operationId:step.operationId,sessionId:session,batchId:id,step:step.name,expectedContextRevision:b.context.contextRevision,context:clone(b.context),request:clone(task.request),result:clone(task.result)};
   // Once formal commit is durable, finish its local projections even if a later
   // revocation prevents new model tasks. Host.apply must return the original receipt.
   const action=async()=>{const r=await this.#host.apply(payload);this.#checkCommit(r,payload);return r;};
   const prior=await this.#host.lookupCommit(payload);const receipt=prior??(step.name==='formal'?await this.#guard(b,action):await action());this.#checkCommit(receipt,payload);
   await this.#fault('afterCommitStep',{session,id,step:step.name});
   b=await this.#edit(session,id,b=>{b.steps.find(s=>s.name===step.name).receipt=clone(receipt);});
  }
  b=await this.read(session,id);await this.#fault('beforePostCheck',{session,id});if(b.plan.stages.some(s=>s.phase==='candidate'&&!terminal(b.tasks.find(t=>t.id===s.id).state)))return;
  for(const stage of b.plan.stages.filter(s=>s.phase==='after'))if(!await this.#runStage(session,id,stage))return;
  await this.#edit(session,id,b=>{b.status='settled';b.diagnostic=null;});
 }
 async discard(session,id,value){if(this.#closing)fail('COMPARISON_CLOSED');const intent=decisionIntent(value,'discard');await this.#edit(session,id,b=>{
   if(b.decision){if(b.decision.kind!=='discard'||!same(b.decision.intent,intent))fail('COMPARISON_DECISION_CONFLICT');return;}
   if(intent.operationId===b.plan.intent.operationId||b.stopOperations.some(o=>o.operationId===intent.operationId))fail('COMPARISON_OPERATION_COLLISION');
   if(intent.expectedContextRevision!==(b.context?.contextRevision??b.plan.state.contextRevision))fail('COMPARISON_CONTEXT_CHANGED');b.decision={kind:'discard',intent};b.stopped=true;b.steps=[{name:'draft',operationId:stageId(id,'restore-draft'),receipt:null}];
  });await this.#releaseQueued(session,id);await this.#restoreDraft(session,id);await this.#finish(session,id);return this.read(session,id);
 }
 async #restoreDraft(session,id){const b=await this.read(session,id),step=b.steps.find(s=>s.name==='draft');if(!step||step.receipt)return;
  const payload={operationId:step.operationId,sessionId:session,batchId:id,expectedContextRevision:b.decision.intent.expectedContextRevision,input:b.plan.intent.input},receipt=await this.#host.restoreDraft(payload);this.#checkCommit(receipt,payload);
  await this.#edit(session,id,b=>{b.steps[0].receipt=clone(receipt);});
 }
 async #finish(session,id){const b=await this.read(session,id);
  if(b.decision?.kind==='adopt')return this.#adoption(session,id);
  if(b.decision?.kind==='discard'){if(b.tasks.every(t=>terminal(t.state))&&b.steps.every(s=>s.receipt))await this.#edit(session,id,b=>{b.status='settled';});return;}
  if(b.context)await this.#edit(session,id,b=>{b.status=b.tasks.some(t=>t.state==='unknown')?'blocked':'awaiting-choice';});
 }
 async reconcile(session,id){
  if(this.#closing)fail('COMPARISON_CLOSED');
  if(this.#jobs.has(id)||this.#adoptions.has(id))fail('COMPARISON_STILL_RUNNING');
  await this.#serial(()=>this.#records.reconcile(session));for(const l of Object.values(this.#budgets))await l.reconcile();
  let b=await this.read(session,id);for(const [level,l]of Object.entries(this.#budgets)){const group=await l.read(id);if(group&&!same(group.plan,b.plan))fail('COMPARISON_RESERVATION_CONFLICT');await this.#edit(session,id,b=>{b.reservations[level]=!!group;});}
  b=await this.read(session,id);if(!Object.values(b.reservations).every(Boolean)){await this.#releaseQueued(session,id);await this.#block(session,id,{code:'COMPARISON_RESERVATION_UNCONFIRMED'});return this.read(session,id);}
  // On restart/lookup never dispatch a candidate or repeat a worldbook draw.
  await this.#edit(session,id,b=>{b.stopped=true;});await this.#releaseQueued(session,id,'candidate');
  b=await this.read(session,id);
  for(const stage of b.plan.stages){let t=b.tasks.find(t=>t.id===stage.id);if(running(t)||t.result?.status==='unknown'){
    const result=await this.#bounded(this.#provider.lookup(clone(t.request)));if(!result)continue;validateResult(result,t.request);
    await this.#edit(session,id,b=>{const t=b.tasks.find(t=>t.id===stage.id);b.events.push({type:'recovered',stageId:stage.id,code:'COMPARISON_ORIGINAL_RESULT'});t.result=clone(result);t.state=result.status;});
   }
   b=await this.read(session,id);t=b.tasks.find(t=>t.id===stage.id);if(t.result&&t.result.status!=='unknown'){await this.#settle(session,id,stage);if(stage.phase==='before'||stage.phase==='after'&&b.decision?.kind==='adopt')await this.#applyTask(session,id,stage);}
  }
  b=await this.read(session,id);if(b.decision?.kind==='discard'){await this.#releaseQueued(session,id);await this.#restoreDraft(session,id);}
  await this.#finish(session,id);return this.read(session,id);
 }
 async close(){if(this.#closed)return;this.#closing=true;while(this.#jobs.size||this.#adoptions.size)await Promise.all([...this.#jobs.values(),...this.#adoptions.values()]);await this.#queue;this.#closed=true;await this.#records.close();}
}
