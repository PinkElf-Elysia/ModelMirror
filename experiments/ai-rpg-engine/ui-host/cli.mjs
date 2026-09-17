import { fileURLToPath } from 'node:url';
import { startUiHost } from './server.mjs';
import { createRecordStore } from './storage.mjs';
import { createUiService } from './service.mjs';
import { loadBuiltinBundle } from './setup.mjs';
import { createJourneyEngine } from './journeys.mjs';
import { createOfflineAdapter } from './mock.mjs';
const args = process.argv.slice(2);
if (args.length && (args.length !== 2 || args[0] !== '--port' || !/^\d{1,5}$/u.test(args[1]))) throw Error('Usage: node ui-host/cli.mjs [--port NUMBER]');
const store = await createRecordStore(fileURLToPath(new URL('./.local/records/', import.meta.url))), builtin = loadBuiltinBundle();
const engine = await createJourneyEngine({ store, root: fileURLToPath(new URL('./.local/runtime/', import.meta.url)), hostTemplate: builtin.hostTemplate, adapterFor: card => createOfflineAdapter(card) });
let host;
try { host = await startUiHost({ port: args.length ? Number(args[1]) : 18405, service: createUiService({ store, builtin, engine }), maxBytes: 1048576 }); }
catch (cause) { await engine.close(); throw cause; }
console.log(JSON.stringify({ service: 'rpg05-ui', origin: host.origin, pid: process.pid, providerDispatch: 'disabled', evidenceKind: 'mock', capabilities: ['drafts', 'imports', 'creation', 'mock-play'] }));
let closing = false;
async function close() {
  if (closing) return; closing = true;
  await host.close(); await engine.close(); process.exitCode = 0;
}
process.once('SIGINT', close); process.once('SIGTERM', close);
