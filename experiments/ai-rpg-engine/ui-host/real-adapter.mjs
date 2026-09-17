import { createStructuredAdapter } from './structured-http.mjs';
import { compileStructuredSchema } from './structured-schema.mjs';
import { compileKeyedSchema } from './keyed-turn.mjs';
import { projectKeyedRequest, projectReviewedKeyedRequest } from './keyed-protocol.mjs';
import { diagnoseOutput } from './output-diagnostics.mjs';
import { createModelMirrorAdapter } from '../runtime/node.mjs';
import { hashValue } from './setup.mjs';
const registered = new WeakMap();
const fail = code => Object.assign(Error(code), { code, status: 409 });
export function executionFor(adapter) {
  if (adapter?.evidenceKind === 'mock') return { modelId: 'model.rpg05-offline-mock', maxTokens: 2048, temperature: 0 };
  const record = registered.get(adapter); if (!record) throw fail('REAL_EXECUTION_NOT_AUTHORIZED');
  return record.execution;
}
export async function settleExecution(adapter, status) {
  const record = registered.get(adapter); if (!record || !record.reservation || record.settled) return;
  record.settled = true;
  const dispatched = record.transportStarted ? record.report?.value?.dispatched ?? null : false;
  const terminalKnown = record.report?.valid === true || Boolean(record.transportEvidence?.done && record.report?.value?.serverReceipt);
  const outcome = status === 'committed' ? 'succeeded' : status === 'cancelled' ? 'cancelled' : dispatched === false || terminalKnown ? 'failed' : 'unknown';
  // A UI failure does not prove that a dispatched request reached a terminal result.
  await record.ledger.complete(record.reservation, { outcome, dispatched, reportSha256: hashValue(record.report ?? { status: 'unknown' }) });
}
// Only a host-supplied freeze + dedicated immutable ledger can construct a real adapter.
// Public HTTP handlers cannot submit these objects or a provider address.
export async function createGuardedAdapter({ guard, ledger, evidenceStore, admission }) {
  const execution = await guard.admit(admission);
  if (execution.evidenceKind !== 'real' || execution.modelId !== 'gpt-5.6-luna' || execution.maxTokens !== 4096) throw fail('REAL_EXECUTION_NOT_AUTHORIZED');
  const structured = execution.responseMode === 'json_schema';
  if (execution.responseMode !== undefined && !structured) throw fail('REAL_RESPONSE_MODE_INVALID');
  const wireFormat = execution.wireFormat ?? 'legacy', protocolMode = execution.protocolMode ?? 'none';
  if (!['none','keyed_protocol_v1','keyed_protocol_v2'].includes(protocolMode) || protocolMode !== 'none' && (!structured || wireFormat !== 'fixed_information_v1')) throw fail('REAL_PROTOCOL_MODE_INVALID');
  const cardPackage = structured ? structuredClone(admission.initial?.cardPackage) : null;
  if (!['legacy', 'fixed_information_v1'].includes(wireFormat) || !structured && wireFormat !== 'legacy') throw fail('REAL_WIRE_FORMAT_INVALID');
  const created = (structured ? createStructuredAdapter : createModelMirrorAdapter)({ ...(structured ? {cardPackage, wireFormat, protocolMode} : {}), baseUrl: 'http://127.0.0.1:18305', evidenceKind: 'real', timeoutMs: 60000, trustedOutputBudget: { maxTokens: 4096 } });
  if (!created.valid || !(await created.value.initialize()).valid) throw fail('REAL_ROUTE_PREFLIGHT_FAILED');
  const record = { execution, ledger, reservation: null, report: null, transportStarted: false, transportEvidence: null, settled: false };
  const adapter = Object.freeze({ evidenceKind: 'real', async generate(request, options) {
    const originalRequest = structuredClone(request);
    if (record.reservation) throw fail('DISPATCH_REPLAY_FORBIDDEN');
    await guard.admit(admission);
    if (originalRequest.sessionId !== admission.sessionId || hashValue(originalRequest.input) !== hashValue(admission.input) || originalRequest.modelId !== execution.modelId || originalRequest.settings.maxTokens !== 4096 || originalRequest.settings.temperature !== 0) throw fail('REAL_REQUEST_BINDING_DRIFT');
    const schema = structured ? (wireFormat === 'fixed_information_v1' ? compileKeyedSchema : compileStructuredSchema)(cardPackage, originalRequest) : null;
    const projection = protocolMode !== 'none' ? (protocolMode === 'keyed_protocol_v2' ? projectReviewedKeyedRequest : projectKeyedRequest)(cardPackage, originalRequest) : null;
    const requestSha256 = projection?.requestSha256 ?? hashValue(schema ? {request: originalRequest, responseFormat: schema.responseFormat} : originalRequest);
    record.reservation = await ledger.reserve({ kind: execution.dispatchKind, operationId: originalRequest.generationId, requestSha256, maxTokens: 4096 });
    // Private evidence is persisted before dispatch. Failure here still consumes the slot.
    await evidenceStore.write('bundle', 'request.' + record.reservation.payload.slot, { request: originalRequest, ...(projection ? {transportRequest: projection.request, requestBinding: projection.binding} : {}), freezeSha256: guard.sha256, ...(schema ? {responseFormat:schema.responseFormat, schemaSha256:schema.sha256, requestSha256, wireFormat, compilerVersion:schema.compilerVersion, ...(schema.codecVersion ? {codecVersion:schema.codecVersion} : {})} : {}) }, 0);
    record.transportStarted = true;
    try { record.report = await created.value.generate(originalRequest, options); }
    catch { record.report = { valid: false, diagnostics: [{ code: 'UNCONFIRMED_DISPATCH' }] }; }
    const transportEvidence = schema ? created.value.evidence() : null;
    record.transportEvidence = transportEvidence;
    const transportReport = record.report;
    if (projection && record.report?.value?.dispatched === true && transportEvidence?.requestBinding?.requestSha256 !== requestSha256) record.report = {valid:false,diagnostics:[{code:'REAL_TRANSPORT_BINDING_DRIFT'}],value:{...record.report.value,status:'failed',outcome:'failed'}};
    const outputDiagnostics = diagnoseOutput(record.report, originalRequest, cardPackage, transportEvidence);
    if (schema) Object.assign(outputDiagnostics, {rawSseCaptured:Boolean(transportEvidence?.rawBytes), terminalEvidence:'client_received_sse_observation', observedTerminal:transportEvidence ? {finishReason:transportEvidence.finishReason, done:transportEvidence.done, rawComplete:transportEvidence.rawComplete, refusal:transportEvidence.refusal} : null});
    await evidenceStore.write('bundle', 'response.' + record.reservation.payload.slot, { report: record.report, ...(transportReport !== record.report ? {transportReport} : {}), requestSha256, ...(schema ? {transportEvidence} : {}), outputDiagnostics }, 0);
    return record.report;
  } });
  registered.set(adapter, record); return adapter;
}
