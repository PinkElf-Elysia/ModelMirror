import test from "node:test";
import assert from "node:assert/strict";
import { createRuntime } from "../runtime/index.mjs";
import { createDeveloperDriver, safeCommandEnvelope } from "../tooling/runtime-cli.mjs";
import { baseRuntimeFixture, sha256 } from "./runtime-fixtures.mjs";

const ok = (value) => ({ valid: true, diagnostics: [], value });
class Store {
  constructor() { this.values = new Map(); }
  async read(id) { return ok(this.values.has(id) ? structuredClone(this.values.get(id)) : null); }
  async write(value, { expectedRevision }) { const old = this.values.get(value.sessionId); if (expectedRevision === null ? old || value.revision !== 0 : !old || old.revision !== expectedRevision || value.revision !== expectedRevision + 1) return { valid: false, diagnostics: [] }; this.values.set(value.sessionId, structuredClone(value)); return ok(structuredClone(value)); }
}
class Adapter {
  constructor() { this.evidenceKind = "mock"; this.calls = 0; }
  async generate(_request, { onText }) { this.calls += 1; const text = JSON.stringify({ narrative: "private story", suggestedActions: [], informationModules: [], stateProposals: [], uncertainties: [] }); await onText(text); return ok({ status: "succeeded", outcome: "completed", dispatched: true, text, observedModel: null, serverReceipt: null, cancellation: { requested: false, clientAborted: false, upstreamConfirmed: null }, usage: { input: null, output: null, total: null } }); }
}
function setup() { const fixture = baseRuntimeFixture(), adapter = new Adapter(), runtime = createRuntime({ store: new Store(), modelAdapter: adapter, hash: sha256 }).value, driver = createDeveloperDriver({ runtime, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }).value; return { fixture, adapter, driver }; }
const create = (id = "request.create") => ({ requestId: id, operation: "create", input: { sessionId: "session.fixture" } });
const generate = () => ({ requestId: "request.generate", operation: "generate", input: { sessionId: "session.fixture", generationId: "generation.1", exchangeId: "exchange.1", expectedRevision: 0, input: { kind: "action", text: "private input" }, messages: [{ role: "user", content: "private prompt" }], modelId: "provider/model", settings: { temperature: 0, maxTokens: 512 } } });

test("driver creates and reads only safe session summaries with fixed resources", async () => {
  const { driver } = setup(), created = await driver.runCommand(create());
  assert.equal(created.valid, true); assert.equal(created.value.sessionId, "session.fixture"); assert.equal(created.value.revision, 0); assert.equal(created.value.turnCount, 0);
  assert.equal(JSON.stringify(created).includes("state.scene-note"), false);
  const read = await driver.runCommand({ requestId: "request.read", operation: "read", input: { sessionId: "session.fixture" } }); assert.equal(read.valid, true); assert.deepEqual(read.value, created.value);
});

test("generate emits text-free event summaries and never exposes prompt, story, state, or draft", async () => {
  const { driver, adapter } = setup(); await driver.runCommand(create()); const events = [];
  const result = await driver.runCommand(generate(), { onEvent: (event) => events.push(event) });
  assert.equal(result.valid, true); assert.equal(adapter.calls, 1); assert.equal(result.value.status, "pending"); assert.equal(events.length > 0, true);
  const serialized = JSON.stringify([events, result]); for (const secret of ["private input", "private prompt", "private story", "state.scene-note", "narrative"]) assert.equal(serialized.includes(secret), false);
  assert.equal(events.every((event) => !Object.hasOwn(event, "text") && (event.type !== "draft" || Number.isInteger(event.textLength))), true);
});

test("commands are strict, immutable, and request ids cannot be replayed or changed", async () => {
  const { driver } = setup(), command = create(); const before = structuredClone(command); assert.equal((await driver.runCommand(command)).valid, true); assert.deepEqual(command, before);
  assert.equal((await driver.runCommand(command)).valid, false);
  assert.equal((await driver.runCommand({ ...command, operation: "read" })).valid, false);
  for (const invalid of [null, [], { requestId: "bad", operation: "unknown", input: {} }, { ...create("request.extra"), extra: true }, { requestId: "request.inject", operation: "read", input: { sessionId: "session.fixture", cardPackage: {} } }]) assert.equal((await driver.runCommand(invalid)).valid, false);
  const cyclic = {}; cyclic.self = cyclic; assert.equal((await driver.runCommand(cyclic)).valid, false);
});

test("runtime exceptions and observer exceptions become stable diagnostics without private text", async () => {
  const fixture = baseRuntimeFixture(), runtime = { async createSession() { throw new Error("private-secret C:\\path"); } }, driver = createDeveloperDriver({ runtime, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }).value;
  const failed = await driver.runCommand(create(), { onEvent() { throw new Error("observer-secret"); } }); assert.equal(failed.valid, false); assert.equal(JSON.stringify(failed).includes("secret"), false); assert.equal(failed.diagnostics[0].path, "");
});

test("public envelopes and summaries reject malicious identifiers, phases, statuses, hashes, usage, and raw text", async () => {
  const raw = "private-secret C:\\users\\secret\\card.json";
  const envelope = safeCommandEnvelope({ requestId: raw, operation: raw }, { valid: false, diagnostics: [{ phase: raw, severity: raw, code: raw, path: raw }], value: null });
  assert.equal(envelope.requestId, null); assert.equal(envelope.operation, null); assert.deepEqual(envelope.diagnostics, [{ phase: "runtime", severity: "error", code: "RUNTIME_FAILURE", path: "" }]); assert.equal(JSON.stringify(envelope).includes("private-secret"), false);
  const fixture = baseRuntimeFixture(), hostileRuntime = { async createSession() { return { valid: true, diagnostics: [{ phase: raw, severity: "warning", code: "SAFE_CODE", path: raw }], value: { format: "modelmirror.ai-rpg.runtime-session", sessionId: raw, revision: raw, resources: { cardPackage: { sha256: raw }, playerSetup: { sha256: raw } }, turns: [] } }; } };
  const driver = createDeveloperDriver({ runtime: hostileRuntime, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }).value, result = await driver.runCommand(create());
  assert.equal(result.valid, true); assert.equal(result.value.sessionId, null); assert.equal(result.value.revision, null); assert.equal(result.value.cardPackageSha256, null); assert.equal(result.diagnostics[0].phase, "runtime"); assert.equal(JSON.stringify(result).includes("private-secret"), false);
  const invalid = safeCommandEnvelope({ requestId: raw, operation: raw }, await driver.runCommand({ requestId: raw, operation: raw, input: { raw } })); assert.equal(JSON.stringify(invalid).includes(raw), false);
});
