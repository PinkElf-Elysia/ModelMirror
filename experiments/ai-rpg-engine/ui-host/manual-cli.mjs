import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { startUiHost } from './server.mjs';
import { createRecordStore } from './storage.mjs';
import { createUiService } from './service.mjs';
import { loadBuiltinBundle } from './setup.mjs';
import { createJourneyEngine } from './journeys.mjs';
import { createGuardedAdapter } from './real-adapter.mjs';
import { createManualAccess, MANUAL_SOURCE_VERSION } from './manual-access.mjs';
import { startManualHost } from './manual-start.mjs';
const args = process.argv.slice(2);
if (![2,4].includes(args.length) || args[0] !== '--execute' || !/^[a-f0-9]{64}$/u.test(args[1]) || args.length === 4 && (args[2] !== '--source-version' || args[3] !== MANUAL_SOURCE_VERSION)) throw Error('Usage: node ui-host/manual-cli.mjs --execute APPROVED_MANUAL_FREEZE_SHA256 [--source-version closeout-1]');
const root = fileURLToPath(new URL('../',import.meta.url));
const local = fileURLToPath(new URL('./.local/real/',import.meta.url));
const versioned = args.length === 4;
const manifest = JSON.parse(await fs.readFile(local+(versioned ? 'manual/freezes/'+MANUAL_SOURCE_VERSION+'.json' : 'manual/freeze.json'),'utf8'));
if (versioned !== (manifest.sourceBinding?.version === MANUAL_SOURCE_VERSION)) throw Error('MANUAL_SOURCE_VERSION_REQUIRED');
const builtin = loadBuiltinBundle(); builtin.contextProfile.budget.outputLimit = 4096;
let access, store, evidenceStore;
const started = await startManualHost({
  async qualify() {
    const response = await fetch('http://127.0.0.1:18305/api/models/provider-chat-control?model_id=gpt-5.6-luna&capability=chat_text',{signal:AbortSignal.timeout(10000),redirect:'error'});
    const qualification = await response.json();
    if (response.status !== 200 || qualification.available !== true || qualification.reason_code !== 'qualified' || qualification.model_id !== 'gpt-5.6-luna') throw Error('REAL_ROUTE_UNQUALIFIED');
  },
  async acquireOwner() {
    store = await createRecordStore(local+'records'); evidenceStore = await createRecordStore(local+'manual/evidence');
    return createJourneyEngine({store,root:local+'runtime',hostTemplate:builtin.hostTemplate,evidenceKind:'real',adapterFor:(_card,admission)=>createGuardedAdapter({...access,evidenceStore,admission})});
  },
  async bindSource() {
    access = await createManualAccess({root,manifest,expectedSha256:args[1],store:await createRecordStore(local+'manual/ledger'),automaticStore:await createRecordStore(local+'ledger'),hostTemplate:builtin.hostTemplate});
    return access;
  },
  async listen(engine) { return startUiHost({port:18409,service:createUiService({store,builtin,engine}),maxBytes:1048576}); },
});
const {host,engine} = started;
console.log(JSON.stringify({service:'rpg05-manual-real-ui',origin:host.origin,pid:process.pid,mode:'user_manual_bounded',automaticConsumed:26,manualConsumed:(await access.ledger.snapshot()).length,manualMaximum:5,model:'gpt-5.6-luna',maxTokens:4096,automaticRetry:false,sourceVersion:versioned?MANUAL_SOURCE_VERSION:'initial',freezeSha256:args[1]}));
let closing = false;
async function close() { if (closing) return; closing = true; await host.close(); await engine.close(); }
process.once('SIGINT',close); process.once('SIGTERM',close);