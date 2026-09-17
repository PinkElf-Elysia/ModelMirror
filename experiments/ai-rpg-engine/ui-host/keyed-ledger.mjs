import { hashValue } from './setup.mjs';
const fail = code => Object.assign(Error(code), { code, status: 409 });
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/u.test(value);
export const KEYED_AUTHORIZATION = 'user-authorized-keyed-real-plan-total20-after-offline-closeout';
const historicKinds = ['certification','gu','gu','certification','gu','gu','gu','certification','certification','gu','minecraft','minecraft'];
const policyIds = ['dispatch-policy','dispatch-continuation','dispatch-extension-slot5','dispatch-structured-slot7'];
const nextKinds = ['certification','gu','gu','gu','minecraft','minecraft','minecraft','cancellation'];
// New authorization appends to the same immutable ledger. It never edits old failures.
export async function createKeyedLedger({ store, freezeSha256, targetedContinuation: approval }) {
  if (!digest(freezeSha256) || approval?.authorization !== KEYED_AUTHORIZATION || approval.maxDispatches !== 20 || approval.priorRecords?.length !== 12 || approval.priorRecords.some(v => !digest(v)) || approval.priorPolicies?.length !== 4 || new Set(approval.priorPolicies.map(p=>p.id)).size !== 4 || approval.priorPolicies.some(p=>!policyIds.includes(p.id)||!digest(p.sha256))) throw fail('DISPATCH_KEYED_APPROVAL_INVALID');
  const frozen = structuredClone(approval), policyId = 'dispatch-keyed-slot12'; let policy;
  async function snapshot() {
    for (const prior of frozen.priorPolicies) if ((await store.read('bundle', prior.id))?.sha256 !== prior.sha256) throw fail('DISPATCH_HISTORY_DRIFT');
    const records = (await store.list('operation')).sort((a,b)=>a.payload.slot-b.payload.slot);
    if (records.length < 12 || records.length > 20) throw fail('DISPATCH_HISTORY_DRIFT');
    for (const [index, r] of records.entries()) {
      const p = r.payload, slot = index + 1;
      if (r.id !== 'dispatch.' + slot || p.slot !== slot || r.revision !== (p.status === 'completed' ? 2 : 1)) throw fail('DISPATCH_LEDGER_CORRUPT');
      if (slot <= 12) {
        if (r.sha256 !== frozen.priorRecords[index] || p.status !== 'completed' || p.kind !== historicKinds[index] || p.outcome !== ([3,5,7,12].includes(slot) ? 'failed' : 'succeeded')) throw fail('DISPATCH_HISTORY_DRIFT');
      } else if (p.freezeSha256 !== freezeSha256 || p.kind !== nextKinds[slot-13] || !['reserved','completed'].includes(p.status) || (index < records.length-1 && (p.status !== 'completed' || p.outcome !== 'succeeded'))) throw fail('DISPATCH_LEDGER_CORRUPT');
    }
    if (policy && (await store.read('bundle', policyId))?.sha256 !== policy.sha256) throw fail('DISPATCH_POLICY_DRIFT');
    return records;
  }
  await snapshot();
  const payload = { ...frozen, freezeSha256, modelId:'gpt-5.6-luna', nextKinds, maxTokens:4096, certificationMaxTokens:512, automaticRetry:false };
  policy = await store.read('bundle', policyId);
  if (!policy) policy = await store.write('bundle', policyId, payload, 0);
  if (policy.revision !== 1 || hashValue(policy.payload) !== hashValue(payload)) throw fail('DISPATCH_POLICY_DRIFT');
  return Object.freeze({ snapshot, async reserve({kind,operationId,requestSha256,maxTokens}) {
    if (!digest(requestSha256) || typeof operationId !== 'string' || !/^[a-z0-9][a-z0-9._-]{0,95}$/u.test(operationId)) throw fail('DISPATCH_REQUEST_INVALID');
    const records = await snapshot(), slot = records.length + 1, last = records.at(-1);
    if (records.some(r=>r.payload.operationId===operationId)) throw fail('DISPATCH_REPLAY_FORBIDDEN');
    if (slot > 20) throw fail('DISPATCH_BUDGET_EXHAUSTED');
    if (slot > 13 && (last.payload.status !== 'completed' || last.payload.outcome !== 'succeeded')) throw fail('DISPATCH_BATCH_STOPPED');
    if (kind !== nextKinds[slot-13] || !Number.isSafeInteger(maxTokens) || maxTokens < 1 || maxTokens > (kind === 'certification' ? 512 : 4096)) throw fail('DISPATCH_SCOPE_MISMATCH');
    return store.write('operation', 'dispatch.' + slot, {slot,kind,operationId,requestSha256,maxTokens,freezeSha256,status:'reserved',outcome:'unknown',reservedAt:new Date().toISOString()}, 0);
  }, async complete(reservation,{outcome,dispatched,reportSha256}) {
    if (!['succeeded','failed','cancelled','unknown'].includes(outcome) || ![true,false,null].includes(dispatched) || !digest(reportSha256)) throw fail('DISPATCH_RESULT_INVALID');
    const current = (await snapshot()).find(r=>r.id===reservation?.id);
    if (!current || current.sha256 !== reservation.sha256 || current.payload.status !== 'reserved') throw fail('DISPATCH_COMPLETION_CONFLICT');
    return store.write('operation', current.id, {...current.payload,status:'completed',outcome,dispatched,reportSha256,completedAt:new Date().toISOString()}, 1);
  }});
}
