// Browser intent journal. A lost acknowledgement never creates a second request.
export const candidateStates={queued:'排队中',reserved:'等待派发',dispatching:'生成中',running:'生成中',completed:'已完成',failed:'未完成',unknown:'结果待核对',cancelled:'已结束',not_dispatched:'未派发',released:'未派发'};
const copy=structuredClone;
const fail=code=>{throw {code};};
const stable=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);
const same=(a,b)=>stable(a)===stable(b);
export function comparisonCandidates(batch){return batch.plan.intent.candidates.map(c=>({...c,stage:batch.plan.stages.find(s=>s.candidateId===c.id),task:batch.tasks.find(t=>t.id===batch.plan.stages.find(s=>s.candidateId===c.id)?.id)}));}
export function unconfirmedOutsideComparison(operations,ownedIds=[],localPending=false){return !!localPending||Object.entries(operations||{}).some(([id,o])=>['pending','unknown'].includes(o.status)&&!ownedIds.includes(id));}
export function comparisonBlocked(batch){return batch&&batch.status!=='settled';}
export function decisionBody(batch,action,candidateId,operationId){
 return action==='stop'?{operationId,expectedRevision:batch.revision}:{operationId,expectedContextRevision:batch.context?.contextRevision??batch.plan.state.contextRevision,...(action==='adopt'?{candidateId}:{})};
}
export function comparisonProgress(batch){
 const done=comparisonCandidates(batch).filter(c=>c.task.state==='completed').length,total=batch.plan.intent.candidates.length;
 if(batch.decision?.kind==='adopt')return batch.status==='settled'?'已采用 · 对比已收束':'已选定 · 正在收束';
 if(batch.decision?.kind==='discard')return batch.status==='settled'?'均未采用':'均未采用 · 正在收束';
 return batch.tasks.some(t=>t.state==='unknown')?'有结果待核对':`${done} / ${total} 份已完成`;
}
export function comparisonReceiptMatches(record,batch){
 if(!batch)return false;
 if(record.action==='future')return batch.status==='applied'&&same(batch.intent,{sessionId:record.sessionId,batchId:record.batchId,...record.body});
 if(record.action==='create')return batch.plan.intent.operationId===record.body.intent.operationId&&batch.plan.intent.sessionId===record.body.intent.sessionId&&batch.plan.intent.input===record.body.intent.input&&same(batch.plan.intent.candidates.map(c=>({id:c.id,selectionId:c.choice.entry.selectionId,profile:c.choice.parameterProfile})),record.body.intent.candidates.map(c=>({id:c.id,selectionId:c.choice.selectionId,profile:c.choice.profile})));
 if(record.action==='stop')return batch.stopOperations.some(o=>same(o,record.body));
 return batch.decision?.kind===record.action&&same(batch.decision.intent,record.body);
}
export function createComparisonClient({api,base,storage,sessionId}){
 const key='modelmirror.rpg.comparison-pending/'+base+'/'+sessionId;
 const pending=()=>{let r;try{r=JSON.parse(storage.getItem(key)||'null');}catch{fail('COMPARISON_LOCAL_RECORD_INVALID');}if(r&&(!['create','adopt','discard','stop','future'].includes(r.action)||r.sessionId!==sessionId||!r.body?.operationId&&r.action!=='create'))fail('COMPARISON_LOCAL_RECORD_INVALID');return r;};
 const write=r=>{try{if(r){const s=JSON.stringify(r);storage.setItem(key,s);if(storage.getItem(key)!==s)throw Error();}else{storage.removeItem(key);if(storage.getItem(key)!==null)throw Error();}}catch{fail('COMPARISON_STORAGE_UNAVAILABLE');}};
 const list=()=>api(base);
 async function recover(){const r=pending(),v=await list();if(!r)return v;if(r.action==='future'){const receipt=await api(base+'/'+r.batchId+'/future/'+r.body.operationId);if(comparisonReceiptMatches(r,receipt))write(null);return v;}const b=v.batches.find(b=>r.action==='create'?b.plan.intent.operationId===r.body.intent.operationId:b.plan.id===r.batchId);if(comparisonReceiptMatches(r,b))write(null);return v;}
 return {pending,list,recover,preflight:intent=>api(base,{action:'preflight',intent}),options:()=>api(base+'/options'),futureStatus:batchId=>api(base+'/'+batchId+'/future'),
  async mutate(action,body,batchId=null){if(pending())fail('COMPARISON_RESULT_PENDING');const record={sessionId,action,body:copy(body),batchId};write(record);
   try{const b=await api(base+(batchId?'/'+batchId+'/'+action:''),body);if(!comparisonReceiptMatches(record,b))fail('COMPARISON_RECEIPT_MISMATCH');write(null);return b;}
   catch(e){const status=e.httpStatus??e.status;if(status>=400&&status<500&&!/UNKNOWN|STORAGE|WRITE|OWNER|CORRUPT|PENDING/.test(e.code||e.message||''))write(null);throw e;}
  },reconcile:batch=>api(base+'/'+batch.plan.id+'/reconcile',{})};
}
