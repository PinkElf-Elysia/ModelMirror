import { hashValue } from './setup.mjs';
const fail=code=>Object.assign(Error(code),{code,status:409});
const digest=v=>typeof v==='string'&&/^[a-f0-9]{64}$/u.test(v);
const historicKinds=['certification','gu','gu','certification','gu','gu','gu'];
const thirteenKinds=['certification','gu','minecraft','minecraft','minecraft','cancellation'];
export async function createStructuredLedger({store,freezeSha256,structuredContinuation:approval}) {
 const recertify=approval?.authorization==='user-approved-total14-including-expired-recertification',maximum=recertify?14:13,nextKinds=recertify?['certification',...thirteenKinds]:thirteenKinds;
 if(!digest(freezeSha256)||(!recertify&&approval?.authorization!=='user-approved-total13-after-slot7')||approval.maxDispatches!==maximum||approval.priorRecords?.length!==7||approval.priorRecords.some(v=>!digest(v))||approval.priorPolicies?.length!==3||approval.priorPolicies.some(p=>!['dispatch-policy','dispatch-continuation','dispatch-extension-slot5'].includes(p.id)||!digest(p.sha256))||new Set(approval.priorPolicies.map(p=>p.id)).size!==3)throw fail('DISPATCH_STRUCTURED_APPROVAL_INVALID');
 const frozen=structuredClone(approval),policyId='dispatch-structured-slot7';let policy;
 async function snapshot(){
  for(const prior of frozen.priorPolicies)if((await store.read('bundle',prior.id))?.sha256!==prior.sha256)throw fail('DISPATCH_HISTORY_DRIFT');
  const records=(await store.list('operation')).sort((a,b)=>a.payload.slot-b.payload.slot);
  if(records.length<7||records.length>maximum)throw fail('DISPATCH_HISTORY_DRIFT');
  for(const [i,r] of records.entries()){
   const p=r.payload,slot=i+1;
   if(r.id!=='dispatch.'+slot||p.slot!==slot||r.revision!==(p.status==='completed'?2:1))throw fail('DISPATCH_LEDGER_CORRUPT');
   if(slot<=7){if(r.sha256!==frozen.priorRecords[i]||p.status!=='completed'||p.kind!==historicKinds[i]||p.outcome!==([3,5,7].includes(slot)?'failed':'succeeded'))throw fail('DISPATCH_HISTORY_DRIFT');}
   else if(p.freezeSha256!==freezeSha256||p.kind!==nextKinds[slot-8]||!['reserved','completed'].includes(p.status)||(i<records.length-1&&(p.status!=='completed'||p.outcome!=='succeeded')))throw fail('DISPATCH_LEDGER_CORRUPT');
  }
  if(policy&&(await store.read('bundle',policyId))?.sha256!==policy.sha256)throw fail('DISPATCH_POLICY_DRIFT');return records;
 }
 await snapshot();const payload={...frozen,freezeSha256,modelId:'gpt-5.6-luna',nextKinds,maxTokens:4096,certificationMaxTokens:512,automaticRetry:false};
 policy=await store.read('bundle',policyId);if(!policy)policy=await store.write('bundle',policyId,payload,0);
 if(policy.revision!==1||hashValue(policy.payload)!==hashValue(payload))throw fail('DISPATCH_POLICY_DRIFT');
 return Object.freeze({snapshot,async reserve({kind,operationId,requestSha256,maxTokens}){
  if(!digest(requestSha256)||typeof operationId!=='string'||!/^[a-z0-9][a-z0-9._-]{0,95}$/u.test(operationId))throw fail('DISPATCH_REQUEST_INVALID');
  const records=await snapshot(),last=records.at(-1),slot=records.length+1;
  if(records.some(r=>r.payload.operationId===operationId))throw fail('DISPATCH_REPLAY_FORBIDDEN');
  if(slot>8&&(last.payload.status!=='completed'||last.payload.outcome!=='succeeded'))throw fail('DISPATCH_BATCH_STOPPED');
  if(slot>maximum)throw fail('DISPATCH_BUDGET_EXHAUSTED');
  if(kind!==nextKinds[slot-8]||!Number.isSafeInteger(maxTokens)||maxTokens<1||maxTokens>(kind==='certification'?512:4096))throw fail('DISPATCH_SCOPE_MISMATCH');
  return store.write('operation','dispatch.'+slot,{slot,kind,operationId,requestSha256,maxTokens,freezeSha256,status:'reserved',outcome:'unknown',reservedAt:new Date().toISOString()},0);
 },async complete(reservation,{outcome,dispatched,reportSha256}){
  if(!['succeeded','failed','cancelled','unknown'].includes(outcome)||![true,false,null].includes(dispatched)||!digest(reportSha256))throw fail('DISPATCH_RESULT_INVALID');
  const current=(await snapshot()).find(r=>r.id===reservation?.id);
  if(!current||current.sha256!==reservation.sha256||current.payload.status!=='reserved')throw fail('DISPATCH_COMPLETION_CONFLICT');
  return store.write('operation',current.id,{...current.payload,status:'completed',outcome,dispatched,reportSha256,completedAt:new Date().toISOString()},1);
 }});
}
