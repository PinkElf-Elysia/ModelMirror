import { createProtocolLedger, createReviewedProtocolLedger } from './protocol-ledger.mjs';
import { createStructuredLedger } from './structured-ledger.mjs';
import { createKeyedLedger } from './keyed-ledger.mjs';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { startUiHost } from './server.mjs';
import { createRecordStore } from './storage.mjs';
import { createUiService } from './service.mjs';
import { loadBuiltinBundle } from './setup.mjs';
import { createJourneyEngine } from './journeys.mjs';
import { createExecutionFreeze } from './execution-freeze.mjs';
import { createDispatchLedger } from './dispatch-ledger.mjs';
import { createGuardedAdapter } from './real-adapter.mjs';
const args = process.argv.slice(2);
const prepare = args.length === 1 && args[0] === '--prepare';
if (!prepare && !(args.length === 2 && ['--execute', '--continue', '--continue-slot5', '--structured', '--keyed', '--protocol', '--reviewed'].includes(args[0]) && /^[a-f0-9]{64}$/u.test(args[1]))) throw Error('Usage: node ui-host/real-cli.mjs --prepare | --execute APPROVED_FREEZE_SHA256 | --continue APPROVED_CONTINUATION_SHA256 | --keyed APPROVED_KEYED_FREEZE_SHA256 | --protocol APPROVED_PROTOCOL_FREEZE_SHA256 | --reviewed APPROVED_REVIEWED_FREEZE_SHA256');
const local = fileURLToPath(new URL('./.local/real/', import.meta.url));
const store = await createRecordStore(local + 'records'), builtin = loadBuiltinBundle();
// This UI-created profile is distinct from the frozen RPG04 source profile.
builtin.contextProfile.budget.outputLimit = 4096;
let engine = null, host;
if (!prepare) {
  const manifest = JSON.parse(await fs.readFile(local + (args[0] === '--reviewed' ? 'freeze-reviewed.json' : args[0] === '--protocol' ? 'freeze-protocol.json' : args[0] === '--keyed' ? 'freeze-keyed.json' : args[0] === '--structured' ? 'freeze-structured.json' : args[0] === '--continue-slot5' ? 'freeze-slot5.json' : args[0] === '--continue' ? 'freeze-continuation.json' : 'freeze.json'), 'utf8'));
  if ((['--keyed','--protocol','--reviewed'].includes(args[0])) !== (manifest.structuredOutput?.format === 'rpg05-strict-output/2')) throw Error('KEYED_FREEZE_MODE_MISMATCH');
  if ((args[0] === '--protocol') !== Boolean(manifest.protocolContinuation)) throw Error('PROTOCOL_FREEZE_MODE_MISMATCH');
  if ((args[0] === '--reviewed') !== Boolean(manifest.reviewedContinuation)) throw Error('REVIEWED_FREEZE_MODE_MISMATCH');
  const guard = await createExecutionFreeze({ root: fileURLToPath(new URL('../', import.meta.url)), manifest, expectedSha256: args[1] });
  const ledger = await (manifest.reviewedContinuation ? createReviewedProtocolLedger : manifest.protocolContinuation ? createProtocolLedger : manifest.targetedContinuation ? createKeyedLedger : manifest.structuredContinuation ? createStructuredLedger : createDispatchLedger)({ reviewedContinuation:manifest.reviewedContinuation, protocolContinuation:manifest.protocolContinuation, targetedContinuation:manifest.targetedContinuation, structuredContinuation:manifest.structuredContinuation, store: await createRecordStore(local + 'ledger'), freezeSha256: manifest.dispatchFreezeSha256 ?? guard.sha256, certificationRequired: manifest.certificationRequired, continuation: manifest.continuation });
  if (manifest.structuredContinuation && (await ledger.snapshot()).filter(r=>r.payload.kind==='certification'&&r.payload.slot>7&&r.payload.status==='completed'&&r.payload.outcome==='succeeded').length !== (manifest.maxDispatches===14?2:1)) throw Error('STRUCTURED_QUALIFICATION_NOT_COMPLETE');
  if (manifest.targetedContinuation && !(await ledger.snapshot()).some(r=>r.payload.slot===13&&r.payload.kind==='certification'&&r.payload.status==='completed'&&r.payload.outcome==='succeeded')) throw Error('KEYED_QUALIFICATION_NOT_COMPLETE');
  if (manifest.protocolContinuation && !(await ledger.snapshot()).some(r=>r.payload.slot===17&&r.payload.kind==='certification'&&r.payload.status==='completed'&&r.payload.outcome==='succeeded')) throw Error('PROTOCOL_QUALIFICATION_NOT_COMPLETE');
  if (manifest.reviewedContinuation && !(await ledger.snapshot()).some(r=>r.payload.slot===19&&r.payload.kind==='certification'&&r.payload.status==='completed'&&r.payload.outcome==='succeeded'&&r.sha256===manifest.protocolProjection.qualificationRecordSha256)) throw Error('REVIEWED_QUALIFICATION_NOT_COMPLETE');
  const evidenceStore = await createRecordStore(local + 'evidence');
  const qualification = await fetch('http://127.0.0.1:18305/api/models/provider-chat-control?model_id=gpt-5.6-luna&capability=chat_text', { signal: AbortSignal.timeout(10000), redirect: 'error' });
  const qualified = await qualification.json();
  if (qualification.status !== 200 || qualified.available !== true || qualified.reason_code !== 'qualified' || qualified.model_id !== 'gpt-5.6-luna') throw Error('REAL_ROUTE_UNQUALIFIED');
  engine = await createJourneyEngine({ store, root: local + 'runtime', hostTemplate: builtin.hostTemplate, evidenceKind: 'real', adapterFor: (_card, admission) => createGuardedAdapter({ guard, ledger, evidenceStore, admission }) });
}
try { host = await startUiHost({ port: 18409, service: createUiService({ store, builtin, engine }), maxBytes: 1048576 }); }
catch (cause) { await engine?.close(); throw cause; }
console.log(JSON.stringify({ service: 'rpg05-real-ui', origin: host.origin, pid: process.pid, mode: prepare ? 'prepare_no_provider' : 'frozen_real', providerDispatch: prepare ? 'disabled' : 'bounded_by_frozen_ledger' }));
let closing = false;
async function close() { if (closing) return; closing = true; await host.close(); await engine?.close(); }
process.once('SIGINT', close); process.once('SIGTERM', close);
