import { createExecutionFreeze } from './execution-freeze.mjs';
import { freezeSetup, hashValue } from './setup.mjs';
import { parsePlayerText, suggestionText } from './input.mjs';
import { REVIEWED_PROTOCOL_VERSION, REVIEWED_SEMANTICS_SHA256 } from './keyed-protocol.mjs';
const fail = code => Object.assign(Error(code), { code, status: 409 });
const digest = value => typeof value === 'string' && /^[a-f0-9]{64}$/u.test(value);
const id = value => typeof value === 'string' && /^[a-z0-9][a-z0-9._-]{0,95}$/u.test(value);
const policyIds = ['dispatch-policy','dispatch-continuation','dispatch-extension-slot5','dispatch-structured-slot7','dispatch-keyed-slot12','dispatch-protocol-slot16','dispatch-reviewed-slot18'];
export const MANUAL_AUTHORIZATION = 'user-authorized-five-manual-dispatches-after-automatic26';
function validateApproval(approval) {
  if (approval?.authorization !== MANUAL_AUTHORIZATION || approval.operator !== 'user' || approval.maxDispatches !== 5 || approval.automaticConsumed !== 26 || approval.automaticRetry !== false || approval.automaticRecords?.length !== 26 || approval.automaticRecords.some(v => !digest(v)) || approval.automaticPolicies?.length !== policyIds.length || new Set(approval.automaticPolicies.map(p => p.id)).size !== policyIds.length || approval.automaticPolicies.some(p => !policyIds.includes(p.id) || !digest(p.sha256))) throw fail('MANUAL_APPROVAL_INVALID');
}
// A separate append-only ledger; the automatic allowance and its failed history
// are never edited, reset, or used by manual requests.
export const MANUAL_SOURCE_AUTHORIZATION = 'user-approved-rpg05-closeout-runtime-cutover';
export const MANUAL_SOURCE_VERSION = 'closeout-1';
const bindingId = 'manual-source-binding.' + MANUAL_SOURCE_VERSION;
function validateSourceBinding(binding, freezeSha256) {
  if (binding === undefined) return null;
  if (binding?.format !== 'rpg05-manual-source-binding/1' || binding.authorization !== MANUAL_SOURCE_AUTHORIZATION || binding.version !== MANUAL_SOURCE_VERSION || !digest(binding.priorFreezeSha256) || binding.priorFreezeSha256 === freezeSha256 || !digest(binding.priorPolicySha256) || !Array.isArray(binding.priorRecords) || binding.priorRecords.length > 5 || binding.priorRecords.some(value => !digest(value)) || new Set(binding.priorRecords).size !== binding.priorRecords.length) throw fail('MANUAL_SOURCE_BINDING_INVALID');
  return structuredClone(binding);
}
export async function createManualLedger({ store, automaticStore, freezeSha256, approval, sourceBinding }) {
  validateApproval(approval);
  if (!digest(freezeSha256)) throw fail('MANUAL_APPROVAL_INVALID');
  const frozen = structuredClone(approval), binding = validateSourceBinding(sourceBinding, freezeSha256);
  const originalFreeze = binding?.priorFreezeSha256 ?? freezeSha256;
  const policyPayload = { ...frozen, freezeSha256: originalFreeze, modelId:'gpt-5.6-luna', maxTokens:4096, temperature:0, failureAndCancellationConsume:true };
  let policy = await store.read('bundle','manual-policy');
  if (binding && (!policy || policy.sha256 !== binding.priorPolicySha256)) throw fail('MANUAL_PRIOR_POLICY_REQUIRED');
  if (policy && (policy.revision !== 1 || hashValue(policy.payload) !== hashValue(policyPayload))) throw fail('MANUAL_POLICY_DRIFT');
  const bindingPayload = binding ? { ...binding, freezeSha256 } : null;
  let bound = binding ? await store.read('bundle',bindingId) : null;
  if (bound && (bound.revision !== 1 || hashValue(bound.payload) !== hashValue(bindingPayload))) throw fail('MANUAL_SOURCE_BINDING_DRIFT');
  async function history() {
    const previous = (await automaticStore.list('operation')).sort((a,b) => a.payload.slot-b.payload.slot);
    if (previous.length !== 26 || previous.some((r,i) => r.id !== 'dispatch.'+(i+1) || r.payload.slot !== i+1 || r.payload.status !== 'completed' || r.sha256 !== frozen.automaticRecords[i])) throw fail('MANUAL_AUTOMATIC_HISTORY_DRIFT');
    for (const p of frozen.automaticPolicies) if ((await automaticStore.read('bundle',p.id))?.sha256 !== p.sha256) throw fail('MANUAL_AUTOMATIC_HISTORY_DRIFT');
  }
  async function snapshot() {
    await history();
    if (policy && (await store.read('bundle','manual-policy'))?.sha256 !== policy.sha256) throw fail('MANUAL_POLICY_DRIFT');
    const versions = (await store.list('bundle')).filter(r => r.id.startsWith('manual-source-binding.'));
    if (!binding && versions.length) throw fail('MANUAL_SOURCE_VERSION_REQUIRED');
    if (binding && (versions.length > 1 || versions.some(r => r.id !== bindingId) || bound && (versions.length !== 1 || versions[0].sha256 !== bound.sha256) || !bound && versions.length)) throw fail('MANUAL_SOURCE_BINDING_DRIFT');
    const records = (await store.list('operation')).sort((a,b) => a.payload.slot-b.payload.slot);
    if (records.length > 5) throw fail('MANUAL_LEDGER_CORRUPT');
    for (const [i,r] of records.entries()) {
      const p = r.payload, expectedFreeze = binding && i >= binding.priorRecords.length ? freezeSha256 : originalFreeze;
      if (r.id !== 'dispatch.'+(i+1) || p.slot !== i+1 || p.cumulativeSlot !== 27+i || p.kind !== 'manual' || p.freezeSha256 !== expectedFreeze || !id(p.operationId) || !digest(p.requestSha256) || p.maxTokens !== 4096 || !['reserved','completed'].includes(p.status) || r.revision !== (p.status === 'completed' ? 2 : 1) || (p.status === 'reserved' ? p.outcome !== 'unknown' : !['succeeded','failed','cancelled','unknown'].includes(p.outcome) || !digest(p.reportSha256) || ![true,false,null].includes(p.dispatched)) || i < records.length-1 && (p.status !== 'completed' || p.outcome === 'unknown')) throw fail('MANUAL_LEDGER_CORRUPT');
    }
    if (new Set(records.map(r => r.payload.operationId)).size !== records.length) throw fail('MANUAL_LEDGER_CORRUPT');
    if (binding) {
      if (records.length < binding.priorRecords.length || binding.priorRecords.some((hash,i) => records[i]?.sha256 !== hash || records[i].payload.status !== 'completed' || records[i].payload.outcome === 'unknown') || !bound && records.length !== binding.priorRecords.length) throw fail('MANUAL_PRIOR_RECORDS_DRIFT');
    }
    return records;
  }
  await snapshot();
  if (!policy) policy = await store.write('bundle','manual-policy',policyPayload,0);
  // Append only after validating source, original policy and every consumed slot.
  // The original policy and all dispatch revisions remain byte-for-byte intact.
  if (binding && !bound) bound = await store.write('bundle',bindingId,bindingPayload,0);
  return Object.freeze({ snapshot, async reserve({ kind, operationId, requestSha256, maxTokens }) {
    if (kind !== 'manual' || !id(operationId) || !digest(requestSha256) || maxTokens !== 4096) throw fail('MANUAL_REQUEST_INVALID');
    const records = await snapshot(), slot = records.length+1, last = records.at(-1);
    if (records.some(r => r.payload.operationId === operationId)) throw fail('DISPATCH_REPLAY_FORBIDDEN');
    if (slot > 5) throw fail('DISPATCH_BUDGET_EXHAUSTED');
    if (last && (last.payload.status !== 'completed' || last.payload.outcome === 'unknown')) throw fail('MANUAL_DISPATCH_UNRESOLVED');
    return store.write('operation','dispatch.'+slot,{slot,cumulativeSlot:26+slot,kind,operationId,requestSha256,maxTokens,freezeSha256,status:'reserved',outcome:'unknown',reservedAt:new Date().toISOString()},0);
  }, async complete(reservation,{outcome,dispatched,reportSha256}) {
    if (!['succeeded','failed','cancelled','unknown'].includes(outcome) || ![true,false,null].includes(dispatched) || !digest(reportSha256)) throw fail('MANUAL_RESULT_INVALID');
    const current = (await snapshot()).find(r => r.id === reservation?.id);
    if (!current || current.sha256 !== reservation.sha256 || current.payload.status !== 'reserved') throw fail('DISPATCH_COMPLETION_CONFLICT');
    return store.write('operation',current.id,{...current.payload,status:'completed',outcome,dispatched,reportSha256,completedAt:new Date().toISOString()},1);
  } });
}
// Only the CLI constructs this guard. Initial data comes from the host's frozen
// journey record, not a browser-supplied system message, route, or file path.
export function createManualGuard({ sourceGuard, expectedSha256, hostTemplate }) {
  if (!digest(expectedSha256) || typeof sourceGuard?.verify !== 'function') throw fail('MANUAL_APPROVAL_INVALID');
  return Object.freeze({sha256:expectedSha256, verify:()=>sourceGuard.verify(), async admit({sessionId,initial,input,turnCount,kind}) {
    await sourceGuard.verify();
    if (!id(sessionId) || !['generate','regenerate'].includes(kind) || !Number.isSafeInteger(turnCount) || turnCount < 0) throw fail('MANUAL_OPERATION_INVALID');
    let valid = false;
    try {
      const frozen = freezeSetup(initial.playerSetup,{cardPackage:initial.cardPackage,contextProfile:initial.contextProfile,hostTemplate},initial.sceneRef);
      const text = suggestionText({inputKind:input.kind,text:input.text,commandRef:input.commandRef});
      valid = frozen.valid && hashValue(frozen.value) === hashValue(initial) && initial.contextProfile.budget.outputLimit === 4096 && hashValue(parsePlayerText(text,initial.cardPackage)) === hashValue(input);
    } catch { /* Invalid untrusted data must fail before reservation. */ }
    if (!valid) throw fail('MANUAL_INITIAL_OR_INPUT_INVALID');
    return Object.freeze({evidenceKind:'real',modelId:'gpt-5.6-luna',maxTokens:4096,temperature:0,dispatchKind:'manual',responseMode:'json_schema',wireFormat:'fixed_information_v1',protocolMode:'keyed_protocol_v2'});
  }});
}
export async function createManualAccess({root,manifest,expectedSha256,store,automaticStore,hostTemplate}) {
  if (!digest(expectedSha256) || hashValue(manifest) !== expectedSha256 || manifest.format !== 'modelmirror.ai-rpg.rpg05-manual-freeze/1' || manifest.modelId !== 'gpt-5.6-luna' || manifest.baseUrl !== 'http://127.0.0.1:18305' || manifest.maxTokens !== 4096 || manifest.temperature !== 0) throw fail('MANUAL_APPROVAL_INVALID');
  validateApproval(manifest.approval);
  const source = manifest.sourceFreeze;
  if (source?.protocolProjection?.format !== REVIEWED_PROTOCOL_VERSION || source.protocolProjection.semanticTextSha256 !== REVIEWED_SEMANTICS_SHA256 || ['ui-host/manual-access.mjs','ui-host/manual-cli.mjs','tests/ui-host-manual-access.test.mjs'].some(p => !source.files?.some(f => f.path === p))) throw fail('MANUAL_SOURCE_BINDING_INVALID');
  const sourceGuard = await createExecutionFreeze({root,manifest:source,expectedSha256:hashValue(source)});
  const ledger = await createManualLedger({store,automaticStore,freezeSha256:expectedSha256,approval:manifest.approval,sourceBinding:manifest.sourceBinding});
  const guard = createManualGuard({sourceGuard,expectedSha256,hostTemplate});
  return Object.freeze({guard,ledger});
}