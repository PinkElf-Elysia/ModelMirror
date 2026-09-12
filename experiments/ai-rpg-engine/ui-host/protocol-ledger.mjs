import { hashValue } from './setup.mjs';
const fail = code => Object.assign(Error(code), { code, status:409 });
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/u.test(value);
export const PROTOCOL_AUTHORIZATION = 'user-authorized-targeted-protocol-retest-total24';
export const REVIEWED_PROTOCOL_AUTHORIZATION = 'user-authorized-reviewed-targeted-retest-total26';
const historicKinds = ['certification','gu','gu','certification','gu','gu','gu','certification','certification','gu','minecraft','minecraft','certification','gu','gu','gu'];
const failedHistory = new Set([3,5,7,12,16]);
const policyIds = ['dispatch-policy','dispatch-continuation','dispatch-extension-slot5','dispatch-structured-slot7','dispatch-keyed-slot12'];
const nextKinds = ['certification','gu','gu','gu','minecraft','minecraft','minecraft','cancellation'];
// Exact new authorization appends slots17-24. All previous policies and failures
// remain immutable; the old total20 policy is not modified or reset.
export function createProtocolLedger({ store, freezeSha256, protocolContinuation: approval }) {
 return createAuthorizedLedger({store,freezeSha256,approval},{authorization:PROTOCOL_AUTHORIZATION,priorCount:16,maxDispatches:24,requiredPolicyIds:policyIds,policyId:'dispatch-protocol-slot16',history:historicKinds});
}
// Separate explicit authorization after the slot18 content review. It cannot
// reinterpret the old total24 policy or change any previous dispatch outcome.
export function createReviewedProtocolLedger({ store, freezeSha256, reviewedContinuation: approval }) {
 return createAuthorizedLedger({store,freezeSha256,approval},{authorization:REVIEWED_PROTOCOL_AUTHORIZATION,priorCount:18,maxDispatches:26,requiredPolicyIds:[...policyIds,'dispatch-protocol-slot16'],policyId:'dispatch-reviewed-slot18',history:[...historicKinds,'certification','gu']});
}
async function createAuthorizedLedger({store,freezeSha256,approval},{authorization,priorCount,maxDispatches,requiredPolicyIds,policyId,history}) {
 if (!digest(freezeSha256) || approval?.authorization !== authorization || approval.maxDispatches !== maxDispatches || approval.priorRecords?.length !== priorCount || approval.priorRecords.some(v=>!digest(v)) || approval.priorPolicies?.length !== requiredPolicyIds.length || new Set(approval.priorPolicies.map(p=>p.id)).size !== requiredPolicyIds.length || approval.priorPolicies.some(p=>!requiredPolicyIds.includes(p.id)||!digest(p.sha256))) throw fail('DISPATCH_PROTOCOL_APPROVAL_INVALID');
 const frozen=structuredClone(approval);let policy;
 async function snapshot() {
  for(const prior of frozen.priorPolicies)if((await store.read('bundle',prior.id))?.sha256!==prior.sha256)throw fail('DISPATCH_HISTORY_DRIFT');
  const records=(await store.list('operation')).sort((a,b)=>a.payload.slot-b.payload.slot);
  if(records.length<priorCount||records.length>maxDispatches)throw fail('DISPATCH_HISTORY_DRIFT');
  for(const [i,r] of records.entries()) {
   const p=r.payload,slot=i+1;
   if(r.id!=='dispatch.'+slot||p.slot!==slot||r.revision!==(p.status==='completed'?2:1))throw fail('DISPATCH_LEDGER_CORRUPT');
   if(slot<=priorCount){if(r.sha256!==frozen.priorRecords[i]||p.status!=='completed'||p.kind!==history[i]||p.outcome!==(failedHistory.has(slot)?'failed':'succeeded'))throw fail('DISPATCH_HISTORY_DRIFT');}
   else if(p.freezeSha256!==freezeSha256||p.kind!==nextKinds[slot-priorCount-1]||!['reserved','completed'].includes(p.status)||(i<records.length-1&&(p.status!=='completed'||p.outcome!=='succeeded')))throw fail('DISPATCH_LEDGER_CORRUPT');
  }
  if(policy&&(await store.read('bundle',policyId))?.sha256!==policy.sha256)throw fail('DISPATCH_POLICY_DRIFT');return records;
 }
 await snapshot();const payload={...frozen,freezeSha256,modelId:'gpt-5.6-luna',nextKinds,maxTokens:4096,certificationMaxTokens:512,automaticRetry:false};
 policy=await store.read('bundle',policyId);if(!policy)policy=await store.write('bundle',policyId,payload,0);
 if(policy.revision!==1||hashValue(policy.payload)!==hashValue(payload))throw fail('DISPATCH_POLICY_DRIFT');
 return Object.freeze({snapshot,async reserve({kind,operationId,requestSha256,maxTokens}){
  if(!digest(requestSha256)||typeof operationId!=='string'||!/^[a-z0-9][a-z0-9._-]{0,95}$/u.test(operationId))throw fail('DISPATCH_REQUEST_INVALID');
  const records=await snapshot(),slot=records.length+1,last=records.at(-1);
  if(records.some(r=>r.payload.operationId===operationId))throw fail('DISPATCH_REPLAY_FORBIDDEN');
  if(slot>maxDispatches)throw fail('DISPATCH_BUDGET_EXHAUSTED');
  if(slot>priorCount+1&&(last.payload.status!=='completed'||last.payload.outcome!=='succeeded'))throw fail('DISPATCH_BATCH_STOPPED');
  if(kind!==nextKinds[slot-priorCount-1]||!Number.isSafeInteger(maxTokens)||maxTokens<1||maxTokens>(kind==='certification'?512:4096))throw fail('DISPATCH_SCOPE_MISMATCH');
  return store.write('operation','dispatch.'+slot,{slot,kind,operationId,requestSha256,maxTokens,freezeSha256,status:'reserved',outcome:'unknown',reservedAt:new Date().toISOString()},0);
 },async complete(reservation,{outcome,dispatched,reportSha256}){
  if(!['succeeded','failed','cancelled','unknown'].includes(outcome)||![true,false,null].includes(dispatched)||!digest(reportSha256))throw fail('DISPATCH_RESULT_INVALID');
  const current=(await snapshot()).find(r=>r.id===reservation?.id);if(!current||current.sha256!==reservation.sha256||current.payload.status!=='reserved')throw fail('DISPATCH_COMPLETION_CONFLICT');
  return store.write('operation',current.id,{...current.payload,status:'completed',outcome,dispatched,reportSha256,completedAt:new Date().toISOString()},1);
 }});
}
