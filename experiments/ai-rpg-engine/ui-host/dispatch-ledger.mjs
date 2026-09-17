import { hashValue } from './setup.mjs';
const fail = code => Object.assign(Error(code), { code, status: 409 });
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/u.test(value);
const kinds = ['certification', 'gu', 'gu', 'gu', 'minecraft', 'minecraft', 'minecraft', 'cancellation'];
// A dedicated record store holds only this run. Reservations survive failure/restart.
// Slot 1 cannot be reassigned when existing certification is reusable.
export async function createDispatchLedger({ store, freezeSha256, certificationRequired, continuation }) {
  if (!store?.read || !store?.write || !store?.list || !digest(freezeSha256) || typeof certificationRequired !== 'boolean') throw fail('DISPATCH_LEDGER_CONFIGURATION_INVALID');
  if (continuation && (!certificationRequired || continuation.format !== 'rpg05-authorized-continuation/1' || continuation.authorization !== 'user-approved-plus-two-after-slot3-failure' || !digest(continuation.priorFreezeSha256) || continuation.priorFreezeSha256 === freezeSha256 || !Array.isArray(continuation.priorRecords) || continuation.priorRecords.length !== 3 || continuation.priorRecords.some(value => !digest(value)))) throw fail('DISPATCH_CONTINUATION_INVALID');
  const extension = continuation?.extension;
  if (extension && (extension.authorization !== 'user-approved-plus-one-after-slot5-failure' || !digest(extension.priorFreezeSha256) || !digest(extension.priorPolicySha256) || !Array.isArray(extension.priorRecords) || extension.priorRecords.length !== 5 || extension.priorRecords.some(value => !digest(value)))) throw fail('DISPATCH_EXTENSION_INVALID');
  const activeKinds = extension ? ['certification','gu','gu','certification','gu','gu','gu','minecraft','minecraft','minecraft','cancellation'] : continuation ? ['certification', 'gu', 'gu', 'certification', 'gu', 'gu', 'minecraft', 'minecraft', 'minecraft', 'cancellation'] : kinds;
  const maximum = extension ? 11 : continuation ? 10 : 8;
  const originalFreeze = continuation?.priorFreezeSha256 ?? freezeSha256;
  const policy = { format: 'modelmirror.ai-rpg.rpg05-dispatch-policy/0.5.0', freezeSha256: originalFreeze, certificationRequired, modelId: 'gpt-5.6-luna', maxDispatches: 8, kinds, maxTokens: 4096, certificationMaxTokens: 512, automaticRetry: false };
  let policyRecord = await store.read('bundle', 'dispatch-policy');
  if (!policyRecord && continuation) throw fail('DISPATCH_CONTINUATION_WITHOUT_HISTORY');
  if (!policyRecord) policyRecord = await store.write('bundle', 'dispatch-policy', policy, 0);
  if (policyRecord.revision !== 1 || hashValue(policyRecord.payload) !== hashValue(policy)) throw fail('DISPATCH_POLICY_DRIFT');
  let continuationRecord;
  const continuationId = extension ? 'dispatch-extension-slot5' : 'dispatch-continuation';
  const historyCount = extension ? 5 : 3;
  const historyHashes = extension?.priorRecords ?? continuation?.priorRecords;
  if (extension && (await store.read('bundle', 'dispatch-continuation'))?.sha256 !== extension.priorPolicySha256) throw fail('DISPATCH_EXTENSION_HISTORY_DRIFT');
  const acceptedHistory = record => continuation && record.payload.slot <= historyCount && record.sha256 === historyHashes[record.payload.slot - 1];
  async function snapshot() {
    const saved = await store.read('bundle', 'dispatch-policy');
    if (saved?.sha256 !== policyRecord.sha256) throw fail('DISPATCH_POLICY_DRIFT');
    const records = (await store.list('operation')).sort((a, b) => Number(a.id.slice(9)) - Number(b.id.slice(9)));
    const first = certificationRequired ? 1 : 2;
    if (records.length > maximum + 1 - first) throw fail('DISPATCH_LEDGER_CORRUPT');
    for (let i = 0; i < records.length; i++) {
      const record = records[i], slot = first + i, p = record.payload;
      if (record.id !== 'dispatch.' + slot || p.slot !== slot || p.freezeSha256 !== (continuation && slot <= 3 ? originalFreeze : extension && slot <= 5 ? extension.priorFreezeSha256 : freezeSha256) || p.kind !== activeKinds[slot - 1] || !['reserved', 'completed'].includes(p.status) || record.revision !== (p.status === 'reserved' ? 1 : 2)) throw fail('DISPATCH_LEDGER_CORRUPT');
      if (continuation && slot <= historyCount && (!acceptedHistory(record) || p.status !== 'completed' || p.outcome !== ([3,5].includes(slot) ? 'failed' : 'succeeded'))) throw fail('DISPATCH_HISTORY_DRIFT');
      if (i < records.length - 1 && !acceptedHistory(record) && (p.status !== 'completed' || p.outcome !== 'succeeded')) throw fail('DISPATCH_LEDGER_CORRUPT');
    }
    if (continuation && records.length < historyCount) throw fail('DISPATCH_CONTINUATION_WITHOUT_HISTORY');
    if (continuationRecord && (await store.read('bundle', continuationId))?.sha256 !== continuationRecord.sha256) throw fail('DISPATCH_CONTINUATION_DRIFT');
    return records;
  }
  await snapshot();
  if (continuation) {
    const payload = { ...continuation, freezeSha256, originalPolicySha256: policyRecord.sha256, maxDispatches: maximum, kinds: activeKinds };
    continuationRecord = await store.read('bundle', continuationId);
    if (!continuationRecord) continuationRecord = await store.write('bundle', continuationId, payload, 0);
    if (continuationRecord.revision !== 1 || hashValue(continuationRecord.payload) !== hashValue(payload)) throw fail('DISPATCH_CONTINUATION_DRIFT');
  }
  return Object.freeze({
    snapshot,
    async reserve({ kind, requestSha256, operationId, maxTokens }) {
      if (!digest(requestSha256) || typeof operationId !== 'string' || !/^[a-z0-9][a-z0-9._-]{0,95}$/u.test(operationId)) throw fail('DISPATCH_REQUEST_INVALID');
      const records = await snapshot(), last = records.at(-1);
      if (records.some(record => record.payload.operationId === operationId)) throw fail('DISPATCH_REPLAY_FORBIDDEN');
      if (last && !acceptedHistory(last) && (last.payload.status !== 'completed' || last.payload.outcome !== 'succeeded')) throw fail('DISPATCH_BATCH_STOPPED');
      const slot = (certificationRequired ? 1 : 2) + records.length;
      if (slot > maximum) throw fail('DISPATCH_BUDGET_EXHAUSTED');
      if (kind !== activeKinds[slot - 1] || !Number.isSafeInteger(maxTokens) || maxTokens < 1 || maxTokens > (kind === 'certification' ? 512 : 4096)) throw fail('DISPATCH_SCOPE_MISMATCH');
      // Exclusive revision publication prevents concurrent reservations from both winning.
      return store.write('operation', 'dispatch.' + slot, { slot, kind, freezeSha256, requestSha256, operationId, maxTokens, status: 'reserved', outcome: 'unknown', reservedAt: new Date().toISOString() }, 0);
    },
    async complete(reservation, { outcome, dispatched, reportSha256 }) {
      if (!['succeeded', 'failed', 'cancelled', 'unknown'].includes(outcome) || ![true, false, null].includes(dispatched) || !digest(reportSha256)) throw fail('DISPATCH_RESULT_INVALID');
      const records = await snapshot(), record = records.find(item => item.id === reservation?.id);
      if (!record || record.sha256 !== reservation.sha256 || record.payload.status !== 'reserved') throw fail('DISPATCH_COMPLETION_CONFLICT');
      // Even known preflight failure remains consumed. No reset/refund path exists.
      return store.write('operation', record.id, { ...record.payload, status: 'completed', outcome, dispatched, reportSha256, completedAt: new Date().toISOString() }, 1);
    },
  });
}
