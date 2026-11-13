// Browser write-ahead records for /5. Completed story is not completed work:
// keep the parent intent until its background stages have settled.
const endpoints={parameters:'/joint/parameters/configure',requirements:'/joint/requirements',send:'/joint/send',update:'/joint/update',settings:'/joint/settings/change',edit:'/joint/memory/edit',branch:'/joint/branch'};
const terminal=new Set(['completed','paused','not_dispatched']);
export function memoryRejectionMessage(job){
 if(job?.state!=='rejected'||job.diagnostic!=='MEMORY_PROPOSAL_FORBIDDEN'||!job.receipt?.result?.text)return null;
 let proposal;
 try{proposal=JSON.parse(job.receipt.result.text);}catch{return null;}
 if(!Array.isArray(proposal?.operations)||!Array.isArray(job.request?.source?.entries))return null;
 for(const op of proposal.operations){
  if(op?.type!=='update'||!Number.isSafeInteger(op.baseVersion))continue;
  const entry=job.request.source.entries.find(e=>e.id===op.id);
  if(entry&&!entry.protected&&entry.version!==op.baseVersion){
   return '记忆整理已暂停。模型引用了《'+entry.name+'》的第 '+op.baseVersion+' 版，本次整理时是第 '+entry.version+' 版。已保留剧情和原条目。';
  }
 }
 return null;
}
const forbiddenReasons=new Map([
 ['rpg_authoring_upstream_permission_denied','服务商报告权限不足。请核对模型连接是否有权使用所选模型。'],
 ['rpg_authoring_upstream_content_policy','服务商报告这次请求触发了内容策略。可在服务商的请求记录中核对详情。'],
 ['rpg_authoring_upstream_refusal','服务商报告模型拒绝生成这次回复。'],
 ['rpg_authoring_upstream_guardrail','服务商报告这次请求触发了安全规则。可在服务商的请求记录中核对规则。'],
]);
// Presentation projection only: never infer provider dispatch from host budget
// use, missing output, an exception string, or an offline fixture.
export function storyProblem(operation){
 if(!operation||!['failed','incomplete','invalid','not_dispatched','unknown'].includes(operation.state))return null;
 const control=operation.receipt?.kind==='control-plane'?operation.receipt.control:null;
 const status=control?.httpStatus;
 const httpStatus=Number.isInteger(status)&&status>=100&&status<=599?status:null;
 const forbidden=operation.state==='failed'&&control?.status==='failed'&&control.dispatched===true&&httpStatus===403;
 return {state:operation.state,operationId:operation.operationId,requestId:operation.request?.id??null,
  httpStatus,forbiddenReason:forbidden?(forbiddenReasons.get(control.error)??'这份回执没有记录具体原因。可按发送时间到服务商的请求记录中核对。'):null,
  dispatched:control&&typeof control.dispatched==='boolean'?control.dispatched:operation.state==='not_dispatched'?false:null,
  code:control?.error??operation.diagnostic??null,fixture:operation.receipt?.kind==='offline-fixture',
  input:operation.intent?.input??operation.request?.rawInput??'',text:operation.receipt?.result?.text??''};
}
const clone=v=>structuredClone(v),fail=code=>{throw {code};};
const canon=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
const same=(a,b)=>canon(a)===canon(b),prefix=id=>'modelmirror.authoring.joint-pending/'+id+'/';
export function jointTaskIntent(view,operationId,input=null){
 if(view.session.generationParameters&&!view.parameterSettings?.authorization?.revision)fail('JOINT_PARAMETER_STATE_REQUIRED');
 return {sessionId:view.session.id,operationId,expectedRevision:view.session.revision,
 expectedModelRevision:view.session.models.revision,expectedContextPolicyRevision:view.effectivePolicy.revision,
 expectedSummaryRevision:view.session.summary.snapshot.revision,expectedMemoryRevision:view.session.memory.snapshot.revision,
 ...(view.session.generationParameters?{expectedParameterAuthorizationRevision:view.parameterSettings.authorization.revision}:{}),kind:input===null?'update':'send',input};}
export function parameterEditIntent(view,operationId,purpose,choice){
 if(!view.session.generationParameters||!view.parameterSettings||!['story','summary','memory'].includes(purpose))fail('JOINT_PARAMETER_STATE_REQUIRED');
 return {sessionId:view.session.id,operationId,expectedRevision:view.session.revision,expectedModelRevision:view.session.models.revision,
  expectedParameterRevision:view.parameterSettings.revision,expectedPurposeRevision:view.parameterSettings.purposeRevisions[purpose],purpose,choice:clone(choice)};
}
export function pluginIntent(settings,purpose,action,operationId,config){return {purpose,action,sessionId:settings.sessionId,operationId,
 expectedRevision:settings.sessionRevision,expectedModelRevision:settings.modelRevision,expectedHistoryRevision:settings.historyRevision,
 expectedSummaryRevision:settings.summaryRevision,expectedMemoryRevision:settings.memoryRevision,expectedContextPolicyRevision:settings.effectivePolicy.revision,
 expectedRegistryRevision:settings.registryRevision,...(purpose==='parameters'?{expectedParameterRevision:settings.parameterRevision}:{}),...(action==='enable'?{permissions:clone(settings.plugins[purpose].permissions)}:{}),...(action==='configure'?{config:clone(config)}:{})};}
export function memoryEditIntent(view,operationId,change){return {sessionId:view.session.id,operationId,expectedRevision:view.session.revision,
 expectedMemoryRevision:view.session.memory.snapshot.revision,expectedContextPolicyRevision:view.effectivePolicy.revision,change:clone(change)};}
export function branchIntent(view,operationId,turn,name){return {sessionId:view.session.id,operationId,expectedRevision:view.session.revision,
 expectedSummaryRevision:view.session.summary.snapshot.revision,expectedMemoryRevision:view.session.memory.snapshot.revision,expectedContextPolicyRevision:view.effectivePolicy.revision,turn,name};}
export function requirementsEditIntent(view,operationId,text){return {sessionId:view.session.id,operationId,expectedRevision:view.session.revision,
 expectedRequirementsRevision:view.session.requirements?.revision??0,expectedContextPolicyRevision:view.effectivePolicy.revision,text};}
export function createJointClient({api,storage,sessionId,workId}){
 function checkRecord(r){if(!r||!Object.hasOwn(endpoints,r.kind)||r.sessionId!==sessionId||r.workId!==workId||r.body?.sessionId!==sessionId||typeof r.body.operationId!=='string')fail('JOINT_LOCAL_RECORD_INVALID');return r;}
 const key=r=>prefix(sessionId)+r.body.operationId;
 function pending(){try{return Object.keys(storage).filter(k=>k.startsWith(prefix(sessionId))).sort().map(k=>{const r=checkRecord(JSON.parse(storage.getItem(k)));if(k!==key(r))fail('JOINT_LOCAL_RECORD_INVALID');return r;});}catch{fail('JOINT_LOCAL_RECORD_INVALID');}}
 function write(r,remove=false){try{const k=key(r);if(remove){storage.removeItem(k);if(storage.getItem(k)!==null)fail('JOINT_LOCAL_STORAGE_UNAVAILABLE');}
  else{const text=JSON.stringify(r);storage.setItem(k,text);if(storage.getItem(k)!==text)fail('JOINT_LOCAL_STORAGE_UNAVAILABLE');}}catch{fail('JOINT_LOCAL_STORAGE_UNAVAILABLE');}}
 const branchPrefix='modelmirror.authoring.branches/'+workId+'/';
 function branches(){try{return Object.keys(storage).filter(k=>k.startsWith(branchPrefix)).map(k=>{
  const r=JSON.parse(storage.getItem(k));if(!r||typeof r.name!=='string'||!/^session-[a-f0-9]{64}$/.test(r.sessionId)||!/^session-[a-f0-9]{64}$/.test(r.parentSessionId))fail('JOINT_LOCAL_RECORD_INVALID');return r;
 });}catch{fail('JOINT_LOCAL_RECORD_INVALID');}}
 function checkBranch(r,result){
  const child=result?.session;
  if(!same(result?.intent,r.body)||!/^branch-[a-f0-9]{64}$/.test(result.checkpointId)||!child||child.id===sessionId||!/^session-[a-f0-9]{64}$/.test(child.id)||child.binding?.workId!==workId||
   child.origin?.parentSessionId!==sessionId||child.origin?.turn!==r.body.turn||child.origin?.checkpointId!==result.checkpointId)fail('JOINT_RECEIPT_MISMATCH');
  const entry={sessionId:child.id,parentSessionId:sessionId,name:r.body.name},k=branchPrefix+r.body.operationId,text=JSON.stringify(entry);
  try{const old=storage.getItem(k);if(old!==null&&old!==text)fail('JOINT_RECEIPT_MISMATCH');storage.setItem(k,text);if(storage.getItem(k)!==text)fail('JOINT_LOCAL_STORAGE_UNAVAILABLE');}
  catch(e){if(e?.code==='JOINT_RECEIPT_MISMATCH')throw e;fail('JOINT_LOCAL_STORAGE_UNAVAILABLE');}
 }
 function requireView(v){if(v?.session?.id!==sessionId||v.session.binding?.workId!==workId)fail('JOINT_RECEIPT_MISMATCH');return v;}
 function checkRun(r,run){if(!run||run.operationId!==r.body.operationId||!same(run.intent,r.body))fail('JOINT_RECEIPT_MISMATCH');return terminal.has(run.state);}
 function checkEdit(r,version){const expected={expectedRevision:r.body.expectedRevision,expectedMemoryRevision:r.body.expectedMemoryRevision,change:r.body.change};
  if(version?.operationId!==r.body.operationId||version.kind!=='manual'||!same(version.edit,expected))fail('JOINT_RECEIPT_MISMATCH');}
 function checkParameters(r,change){
  if(!same(change?.intent,r.body)||change.revision!==r.body.expectedParameterRevision+1||change.purposeRevision!==r.body.expectedPurposeRevision+1||
   change.receipt?.sessionId!==sessionId||change.receipt?.parameterRevision!==r.body.expectedParameterRevision||change.receipt?.capability!=='session.parameters.configure')fail('JOINT_RECEIPT_MISMATCH');
 }
 function checkRequirements(r,change){if(!same(change?.intent,r.body)||change.revision!==r.body.expectedRequirementsRevision+1)fail('JOINT_RECEIPT_MISMATCH');}
 function checkSettings(r,result){if(!same(result?.intent,r.body)||result.settings?.sessionId!==sessionId||!['applied','not_applied','not_submitted'].includes(result.state)||
  (result.state==='applied'&&result.receipt?.operationId!==r.body.operationId))fail('JOINT_RECEIPT_MISMATCH');}
 function observe(value){const v=requireView(value);for(const r of pending()){
  if(r.kind==='send'||r.kind==='update'){const run=v.session.runs.find(x=>x.operationId===r.body.operationId);if(run&&checkRun(r,run))write(r,true);}
  if(r.kind==='parameters'){const c=v.session.generationParameters?.changes.find(c=>c.intent.operationId===r.body.operationId);if(c){checkParameters(r,c);write(r,true);}}
  if(r.kind==='requirements'){const c=v.session.requirements?.changes.find(c=>c.intent.operationId===r.body.operationId);if(c){checkRequirements(r,c);write(r,true);}}
  if(r.kind==='edit'){const version=v.session.memory.snapshot.versions.find(x=>x.operationId===r.body.operationId);if(version){checkEdit(r,version);write(r,true);}}
 }return v;}
 async function view(){return observe(await api('/joint/sessions/'+sessionId));}
 async function submit(kind,body,again=false){
  if(!Object.hasOwn(endpoints,kind))fail('JOINT_OPERATION_INVALID');const r=checkRecord({kind,sessionId,workId,body:clone(body)}),old=pending();
  if(old.length&&!(again&&old.length===1&&same(old[0],r)))fail('JOINT_RESULT_PENDING');write(r);
  try{const result=await api(endpoints[kind],body);
   if(kind==='branch'){checkBranch(r,result);write(r,true);}
   else if(kind==='settings'){checkSettings(r,result);if(result.state==='applied')write(r,true);}
   else if(kind==='parameters'){if(result.sessionId!==sessionId)fail('JOINT_RECEIPT_MISMATCH');checkParameters(r,result.change);write(r,true);}
   else if(kind==='requirements'){if(result.sessionId!==sessionId)fail('JOINT_RECEIPT_MISMATCH');checkRequirements(r,result.change);write(r,true);}
   else if(kind==='edit'){checkEdit(r,result.version);write(r,true);}
   else {if(result.sessionId!==sessionId)fail('JOINT_RECEIPT_MISMATCH');if(checkRun(r,result.run))write(r,true);}
   return result;
  }catch(e){
   if(e.httpStatus&&e.httpStatus<500&&!/UNKNOWN|STORAGE|WRITE|OWNER|CORRUPT/.test(e.code||''))write(r,true);
   throw e;
  }
 }
 async function recover(value){const r=checkRecord(clone(value));
  if(r.kind==='parameters'){
   const result=await api('/joint/parameters/reconcile',r.body);
   if(result.sessionId!==sessionId||!same(result.intent,r.body)||!['applied','not_applied','unknown'].includes(result.state))fail('JOINT_RECEIPT_MISMATCH');
   if(result.state==='applied'){checkParameters(r,result.change);write(r,true);return {state:'settled',result,record:r};}
   if(result.change!==null)fail('JOINT_RECEIPT_MISMATCH');return {state:result.state,result,record:r};
  }
  if(r.kind==='branch'){
   const result=await api('/joint/branch/reconcile',r.body);
   if(!same(result?.intent,r.body)||!['not_submitted','checkpoint_saved','completed'].includes(result.state))fail('JOINT_RECEIPT_MISMATCH');
   if(result.state==='completed'){checkBranch(r,result);write(r,true);return {state:'settled',result,record:r};}
   return {state:result.state,result,record:r};
  }
  if(r.kind==='settings'){
   const result=await api('/joint/settings/reconcile',r.body);checkSettings(r,result);if(result.state==='applied')write(r,true);
   return {state:result.state,result,record:r};
  }
  await api('/joint/reconcile',{sessionId});const v=requireView(await api('/joint/sessions/'+sessionId));
  if(r.kind==='requirements'){const change=v.session.requirements?.changes.find(c=>c.intent.operationId===r.body.operationId);if(!change)return {state:v.busy?'unknown':'not_applied',record:r};checkRequirements(r,change);write(r,true);return {state:'settled',record:r,change};}
  if(r.kind==='edit'){
   const version=v.session.memory.snapshot.versions.find(x=>x.operationId===r.body.operationId);
   if(!version)return {state:v.busy?'unknown':'not_applied',record:r};checkEdit(r,version);write(r,true);return {state:'settled',record:r,version};
  }
  const run=v.session.runs.find(x=>x.operationId===r.body.operationId);
  if(!run)return {state:'unknown',record:r};
  if(checkRun(r,run)){write(r,true);return {state:'settled',record:r,run};}
  if(v.busy)return {state:'running',record:r,run};
  const result=await api('/joint/recover',{sessionId,operationId:r.body.operationId,expectedRevision:v.session.revision,
   expectedSummaryRevision:v.session.summary.snapshot.revision,expectedMemoryRevision:v.session.memory.snapshot.revision});
  if(result.sessionId!==sessionId)fail('JOINT_RECEIPT_MISMATCH');const settled=checkRun(r,result.run);if(settled)write(r,true);
  return {state:settled?'settled':'unknown',result,record:r};
 }
 return Object.freeze({pending,branches,view,observe,submit,recover,async discardConfirmed(r){const result=await recover(r);if(!['not_applied','not_submitted'].includes(result.state))fail('JOINT_RESULT_PENDING');write(r,true);}});
}
