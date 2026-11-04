// Browser operation journal. Reading or recovering never dispatches a new task.
const kinds={send:'/sessions/send',update:'/sessions/summary/update',continue:'/sessions/summary/continue',settings:'/summary-settings/change'};
const terminal=new Set(['completed','incomplete','invalid','failed','not_dispatched']);
const summaryTerminal=new Set(['applied','rejected','revoked','not_dispatched','blocked','stale','awaiting_compression']);
const canon=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
const fail=code=>{throw {code};};
const prefix=id=>'modelmirror.authoring.session-pending/'+id+'/';
export function sessionPolicyText(v){const p=v?.effectivePolicy;
 if(!p)return '尚未读取会话';if(p.mode==='rolling-summary')return '历史摘要 + 最近 1 回合';
 if(p.mode==='history-window')return '最近 '+p.historyConfig.turns+' 回合'+(p.historyConfig.includeInitialCharacter?' + 开局原文':'');return '完整历史';
}
export function summarySettingsIntent(s,action,operationId){return {action,sessionId:s.sessionId,operationId,expectedRevision:s.sessionRevision,
 expectedSummaryRevision:s.summaryRevision,expectedModelRevision:s.modelRevision,expectedContextPolicyRevision:s.effectivePolicy.revision,
 expectedRegistryRevision:s.registryRevision,...(action==='enable'?{permissions:structuredClone(s.plugin.permissions)}:{})};}
export function summaryTaskIntent(v,operationId,kind){return {sessionId:v.session.id,operationId,expectedRevision:v.session.revision,
 expectedSummaryRevision:v.session.summary.snapshot.revision,...(kind==='update'?{expectedModelRevision:v.session.models.revision,expectedContextPolicyRevision:v.effectivePolicy.revision}:{})};}
export function storyIntent(v,operationId,input){return {sessionId:v.session.id,operationId,input,expectedRevision:v.session.revision,expectedModelRevision:v.session.models.revision,expectedContextPolicyRevision:v.effectivePolicy.revision};}
export function sessionPending(storage,sessionId,workId){
 try{return Object.keys(storage).filter(k=>k.startsWith(prefix(sessionId))).sort().map(k=>{
  const r=JSON.parse(storage.getItem(k));if(!r||!Object.hasOwn(kinds,r.kind)||r.sessionId!==sessionId||r.workId!==workId||r.body?.sessionId!==sessionId||typeof r.body.operationId!=='string'||k!==prefix(sessionId)+r.body.operationId)fail('SESSION_LOCAL_RECORD_INVALID');return r;
 });}catch{fail('SESSION_LOCAL_RECORD_INVALID');}
}
export function createSessionClient({api,storage,sessionId,workId}){
 const pending=()=>sessionPending(storage,sessionId,workId);
 function store(r,remove=false){try{const k=prefix(sessionId)+r.body.operationId;if(remove)storage.removeItem(k);else{const value=JSON.stringify(r);storage.setItem(k,value);if(storage.getItem(k)!==value)fail('SESSION_LOCAL_STORAGE_UNAVAILABLE');}}catch{fail('SESSION_LOCAL_STORAGE_UNAVAILABLE');}}
 function check(r){if(r.workId!==workId||r.sessionId!==sessionId||r.body?.sessionId!==sessionId||!Object.hasOwn(kinds,r.kind))fail('SESSION_RECEIPT_MISMATCH');}
 function match(r,result){check(r);
  if(r.kind==='settings')return canon(result?.intent)===canon(r.body)&&result.settings?.sessionId===sessionId&&result.state==='applied'&&result.receipt?.operationId===r.body.operationId;
  if(result?.sessionId!==sessionId)return false;
  if(r.kind==='send')return canon(result.operation?.intent)===canon(r.body);
  if(r.kind==='continue')return result.job?.operationId===r.body.operationId;
  return canon(result.job?.intent)===canon(r.body);
 }
 async function view(){const v=await api('/sessions/'+sessionId);if(v?.session?.id!==sessionId||v.session.binding?.workId!==workId)fail('SESSION_RECEIPT_MISMATCH');return v;}
 async function submit(kind,body,again=false){
  if(!Object.hasOwn(kinds,kind)||body.sessionId!==sessionId)fail('SESSION_OPERATION_INVALID');
  const r={kind,sessionId,workId,body:structuredClone(body)},old=pending();
  if(old.length&&!(again&&old.length===1&&canon(old[0])===canon(r)))fail('SESSION_RESULT_PENDING');
  store(r);
  try{const result=await api(kinds[kind],body);if(!match(r,result))fail('SESSION_RECEIPT_MISMATCH');
   // A confirmed unknown result stays in both journals until explicit recovery.
   if(kind==='settings'||kind==='send'&&terminal.has(result.operation.state)||kind!=='send'&&summaryTerminal.has(result.job?.state))store(r,true);
   return result;
  }catch(e){
   if(e.httpStatus&&e.httpStatus<500&&!/UNKNOWN|STORAGE|WRITE|OWNER|CORRUPT/.test(e.code||''))store(r,true);
   throw e;
  }
 }
 async function recover(r){check(r);
  if(r.kind==='settings'){
   const result=await api('/summary-settings/reconcile',r.body);if(canon(result.intent)!==canon(r.body)||result.settings?.sessionId!==sessionId)fail('SESSION_RECEIPT_MISMATCH');
   if(result.state==='applied'){if(!match(r,result))fail('SESSION_RECEIPT_MISMATCH');store(r,true);}
   else if(!['not_applied','not_submitted'].includes(result.state))fail('SESSION_RECEIPT_MISMATCH');
   return {state:result.state,result,record:r};
  }
  await api('/sessions/reconcile',{sessionId});let v=await view();
  const op=r.kind==='send'?v.session.operations.find(o=>o.operationId===r.body.operationId):v.session.summary.jobs.find(j=>j.operationId===r.body.operationId);
  if(!op)return {state:'unknown',record:r};
  if(r.kind!=='continue'&&canon(op.intent)!==canon(r.body))fail('SESSION_RECEIPT_MISMATCH');
  const result=await api(r.kind==='send'?'/sessions/recover':'/sessions/summary/recover',r.kind==='send'?{sessionId,operationId:r.body.operationId,expectedRevision:v.session.revision}:summaryTaskIntent(v,r.body.operationId,'recover'));
  if(!match(r,result))fail('SESSION_RECEIPT_MISMATCH');
  const settled=r.kind==='send'?terminal.has(result.operation.state):summaryTerminal.has(result.job.state);
  if(settled)store(r,true);return {state:settled?'settled':'unknown',result,record:r};
 }
 return Object.freeze({pending,view,submit,recover,
  async discardConfirmed(r){const result=await recover(r);if(!['not_applied','not_submitted'].includes(result.state))fail('SESSION_RESULT_PENDING');store(r,true);},
 });
}
