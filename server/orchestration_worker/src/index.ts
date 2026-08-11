import process from 'node:process';

import { JsonlChannel } from './channel.js';
import { handleRequest } from './service.js';
import {
  AGENCY_BRIDGE_PROTOCOL,
  AgencyBridgeError,
  parseRequest,
} from './protocol.js';

const diagnostic = (...values: unknown[]): void => {
  process.stderr.write(`${values.map(String).join(' ')}\n`);
};

// Upstream Compose uses console.log for CLI progress. stdout is reserved for
// the versioned JSONL protocol, so all diagnostics are redirected to stderr.
console.log = diagnostic;
console.info = diagnostic;
console.warn = diagnostic;
console.error = diagnostic;

const channel = new JsonlChannel(process.stdin, process.stdout);
let requestId = 'unknown';

try {
  const request = parseRequest(await channel.read());
  requestId = request.id;
  const result = await handleRequest(request, channel);
  channel.write({
    protocol: AGENCY_BRIDGE_PROTOCOL,
    type: 'response',
    id: request.id,
    ok: true,
    result,
  });
} catch (error) {
  const known = error instanceof AgencyBridgeError;
  channel.write({
    protocol: AGENCY_BRIDGE_PROTOCOL,
    type: 'response',
    id: requestId,
    ok: false,
    error: {
      code: known ? error.code : 'worker_failed',
      message: known ? error.message : 'Agency worker failed.',
    },
  });
  if (!known) diagnostic(error instanceof Error ? error.stack ?? error.message : error);
} finally {
  channel.close();
}
