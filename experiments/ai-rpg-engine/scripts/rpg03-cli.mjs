import fsp from "node:fs/promises";
import path from "node:path";
import { createRuntime } from "../runtime/index.mjs";
import { createModelMirrorAdapter, openFileSessionStore, sha256 } from "../runtime/node.mjs";
import { createDeveloperDriver, safeCommandEnvelope } from "../tooling/runtime-cli.mjs";

const MAX_CONFIG = 65536, MAX_RESOURCE = 16 * 1024 * 1024, MAX_LINE = 1024 * 1024, MAX_LINES = 4096, MAX_PENDING = 32;
const emit = (value) => process.stdout.write(JSON.stringify(value) + "\n");
const fatal = (code) => { emit({ requestId: null, operation: null, valid: false, diagnostics: [{ phase: "cli", severity: "error", code, path: "" }], value: null }); process.exitCode = 1; };
function localAbsolute(value) { return typeof value === "string" && path.isAbsolute(value) && path.parse(value).root !== "\\" && !value.startsWith("\\\\") && !value.startsWith("//"); }
async function safeAncestors(file) {
  const absolute = path.resolve(file), parsed = path.parse(absolute), parts = absolute.slice(parsed.root.length).split(path.sep).filter(Boolean); let current = parsed.root;
  for (const part of parts) { current = path.join(current, part); const stat = await fsp.lstat(current); if (stat.isSymbolicLink()) return false; }
  return true;
}
async function readJsonFile(file, maximum) {
  if (!localAbsolute(file) || !(await safeAncestors(file))) throw new Error();
  const before = await fsp.lstat(file); if (!before.isFile() || before.isSymbolicLink() || before.nlink !== 1 || before.size > maximum) throw new Error();
  const handle = await fsp.open(file, "r");
  try {
    const opened = await handle.stat(); if (!opened.isFile() || opened.nlink !== 1 || opened.dev !== before.dev || opened.ino !== before.ino || opened.size > maximum) throw new Error();
    const chunks = []; let total = 0;
    while (total <= maximum) { const part = Buffer.allocUnsafe(Math.min(65536, maximum + 1 - total)); const read = await handle.read(part, 0, part.length, null); if (!read.bytesRead) break; chunks.push(part.subarray(0, read.bytesRead)); total += read.bytesRead; }
    if (total > maximum) throw new Error(); const after = await fsp.lstat(file); if (after.isSymbolicLink() || after.nlink !== 1 || after.dev !== opened.dev || after.ino !== opened.ino) throw new Error();
    const text = new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks, total)); return JSON.parse(text);
  } finally { await handle.close().catch(() => {}); }
}
async function main() {
  if (process.argv.length !== 4 || process.argv[2] !== "--config" || !localAbsolute(process.argv[3])) { fatal("RUNTIME_CLI_ARGUMENTS_INVALID"); return; }
  let config, cardPackage, playerSetup;
  try {
    config = await readJsonFile(process.argv[3], MAX_CONFIG);
    if (!config || typeof config !== "object" || Array.isArray(config) || Object.keys(config).sort().join(",") !== "baseUrl,cardPackagePath,evidenceKind,playerSetupPath,sessionDirectory" || !["mock", "real"].includes(config.evidenceKind) || ![config.sessionDirectory, config.cardPackagePath, config.playerSetupPath].every(localAbsolute)) throw new Error();
    [cardPackage, playerSetup] = await Promise.all([readJsonFile(config.cardPackagePath, MAX_RESOURCE), readJsonFile(config.playerSetupPath, MAX_RESOURCE)]);
  } catch { fatal("RUNTIME_CLI_CONFIG_INVALID"); return; }
  const adapterReport = createModelMirrorAdapter({ baseUrl: config.baseUrl, evidenceKind: config.evidenceKind, maxOutputTokens: 512 });
  if (!adapterReport.valid || !(await adapterReport.value.initialize()).valid) { fatal("RUNTIME_CLI_ADAPTER_INITIALIZE_FAILED"); return; }
  const storeReport = await openFileSessionStore({ rootDirectory: config.sessionDirectory });
  if (!storeReport.valid) { fatal("RUNTIME_CLI_STORE_OPEN_FAILED"); return; }
  const store = storeReport.value, runtimeReport = createRuntime({ store, modelAdapter: adapterReport.value, hash: sha256 });
  const driverReport = runtimeReport.valid && createDeveloperDriver({ runtime: runtimeReport.value, cardPackage, playerSetup });
  if (!driverReport?.valid) { fatal("RUNTIME_CLI_RUNTIME_CONFIG_INVALID"); await store.close(); return; }
  const pending = new Set(), ids = new Set(); let lines = 0, buffer = Buffer.alloc(0), accepting = true;
  const accept = (bytes) => {
    if (!accepting) return; lines += 1;
    if (lines > MAX_LINES || bytes.length > MAX_LINE || pending.size >= MAX_PENDING) { accepting = false; fatal("RUNTIME_CLI_INPUT_LIMIT"); return; }
    let command; try { command = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); } catch { fatal("RUNTIME_CLI_LINE_INVALID"); return; }
    if (typeof command?.requestId === "string" && ids.has(command.requestId)) { fatal("RUNTIME_CLI_REQUEST_ID_DUPLICATE"); return; }
    if (typeof command?.requestId === "string") ids.add(command.requestId);
    const identity = safeCommandEnvelope(command, { valid: true, diagnostics: [], value: null });
    const task = driverReport.value.runCommand(command, { onEvent: (event) => emit({ requestId: identity.requestId, operation: identity.operation, ...event }) }).then((result) => { emit(safeCommandEnvelope(command, result)); if (!result.valid) process.exitCode = 1; }, () => fatal("RUNTIME_CLI_COMMAND_FAILED")).finally(() => pending.delete(task)); pending.add(task);
  };
  try {
    for await (const chunk of process.stdin) {
      buffer = Buffer.concat([buffer, chunk]); if (buffer.length > MAX_LINE && !buffer.includes(10)) { accepting = false; fatal("RUNTIME_CLI_LINE_LIMIT"); break; }
      let newline; while ((newline = buffer.indexOf(10)) >= 0) { let line = buffer.subarray(0, newline); buffer = buffer.subarray(newline + 1); if (line.at(-1) === 13) line = line.subarray(0, -1); if (line.length) accept(line); }
    }
    if (buffer.length) accept(buffer);
  } catch { fatal("RUNTIME_CLI_INPUT_FAILED"); }
  finally {
    await Promise.allSettled([...pending]);
    const closed = await store.close().catch(() => null); if (!closed?.valid) fatal("RUNTIME_CLI_STORE_CLOSE_FAILED");
  }
}
await main();
