import assert from "node:assert/strict";
import test from "node:test";
import { createPluginHost, createRuntime, validateRuntimeEvent, validateRuntimeSession } from "../runtime/index.mjs";
import { baseRuntimeFixture, compileVerifiedContent, sha256 } from "./runtime-fixtures.mjs";

const ok = (value) => ({ valid: true, diagnostics: [], value });
const bad = () => ({ valid: false, diagnostics: [{ phase: "storage", severity: "error", code: "TEST_STORE_FAILURE", path: "" }] });
class MemoryStore {
  constructor() { this.sessions = new Map(); this.failRead = false; this.failWrite = false; }
  async read(id) { if (this.failRead) return bad(); return ok(this.sessions.has(id) ? structuredClone(this.sessions.get(id)) : null); }
  async write(session, { expectedRevision }) { if (this.failWrite) return bad(); const current = this.sessions.get(session.sessionId); if (expectedRevision === null ? current || session.revision !== 0 : !current || current.revision !== expectedRevision || session.revision !== expectedRevision + 1) return bad(); this.sessions.set(session.sessionId, structuredClone(session)); return ok(structuredClone(session)); }
}
const proposal = (overrides = {}) => ({ narrative: "A quiet scene.", suggestedActions: [{ id: "suggestion.wait", label: "Wait", inputKind: "action", text: "wait" }], informationModules: [], stateProposals: [{ fieldRef: "state.scene-note", proposedValue: "quiet", rationale: "Observed" }], uncertainties: [], ...overrides });
const request = (sessionId = "session.fixture", overrides = {}) => ({ sessionId, generationId: "generation.1", exchangeId: "exchange.1", expectedRevision: 0, input: { kind: "action", text: "wait" }, messages: [{ role: "user", content: "Return JSON." }], modelId: "provider/model", settings: { temperature: 0, maxTokens: 512 }, ...overrides });
const commit = (sessionId, revision, acceptedStateFields = ["state.scene-note"]) => ({ format: "modelmirror.ai-rpg.turn-commit", formatVersion: "0.1.0", sessionId, generationId: "generation.1", exchangeId: "exchange.1", expectedRevision: revision, acceptedStateFields });
class MockAdapter {
  constructor(handler = null) { this.evidenceKind = "mock"; this.counter = { value: 0 }; this.handler = handler; Object.freeze(this); }
  get calls() { return this.counter.value; }
  async generate(value, options) { this.counter.value += 1; if (this.handler) return this.handler(value, options); const text = JSON.stringify(proposal()); if (options?.onText) await options.onText(text); return ok({ status: "succeeded", outcome: "completed", dispatched: true, text, observedModel: null, serverReceipt: null, cancellation: { requested: false, clientAborted: false, upstreamConfirmed: null }, usage: { input: null, output: null, total: null } }); }
}
function runtimeFor(store = new MemoryStore(), adapter = new MockAdapter(), pluginHost = null) { const report = createRuntime({ store, modelAdapter: adapter, hash: sha256, pluginHost }); assert.equal(report.valid, true); return { runtime: report.value, store, adapter }; }
function safeFailure(report, secret = "private-secret") { assert.equal(report.valid, false); const text = JSON.stringify(report); assert.equal(text.includes(secret), false); assert.equal(text.includes("stack"), false); return text; }
function canonicalValue(value) { if (Array.isArray(value)) return value.map(canonicalValue); if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalValue(value[key])])); return value; }
const canonicalSha = (value) => sha256(JSON.stringify(canonicalValue(value)));
function pluginManifest() {
  return { format: "modelmirror.ai-rpg.plugin-manifest", formatVersion: "0.1.0", plugin: { id: "plugin.context-basic", version: "1.0.0", displayName: "Context", description: "Context fixture." }, compatibleHostContractVersions: ["0.1.0"], capabilities: ["context.enrich"], permissions: ["card.read", "turn.read"], settings: [{ key: "context.limit", label: "Limit", description: "Limit.", valueType: "integer", required: false, minimum: 1, maximum: 32 }], dependencies: [], dataAccess: { read: ["card", "turnInput"], propose: ["context"] }, network: { mode: "none" }, lifecycle: { activation: "explicit", deactivation: "supported", failurePolicy: "isolated", uninstallData: "retain" }, provenance: { sourceReference: "fixture", sourceSha256: "a".repeat(64), licenseName: "MIT", licenseReference: "fixture", artifactSha256: "b".repeat(64) } };
}
function pluginGrant({ sessionId, cardPackage, playerSetup, manifest, revision = 0, action = "authorize", overrides = {} }) {
  const empty = action === "revoke";
  return { format: "modelmirror.ai-rpg.plugin-authorization", formatVersion: "0.1.0", sessionId, cardPackageSha256: canonicalSha(cardPackage), playerSetupSha256: canonicalSha(playerSetup), revision, evidenceKind: "mock", action, pluginId: manifest.plugin.id, version: manifest.plugin.version, manifestSha256: canonicalSha(manifest), artifactSha256: manifest.provenance.artifactSha256, permissions: empty ? [] : [...manifest.permissions], read: empty ? [] : [...manifest.dataAccess.read], propose: empty ? [] : [...manifest.dataAccess.propose], settings: empty ? [] : [{ key: "context.limit", value: 4 }], ...overrides };
}
function registeredHost(manifest, adapter = { async invoke() { return { proposals: [] }; } }) {
  const report = createPluginHost({ hash: sha256 }); assert.equal(report.valid, true); const host = report.value;
  assert.equal(host.register({ manifest, manifestSha256: canonicalSha(manifest), artifactSha256: manifest.provenance.artifactSha256, adapter }).valid, true);
  return host;
}

test("creates the real RPG02 compiled zero-plugin session with five talents and exact initial state", async () => {
  const compiled = compileVerifiedContent(), { runtime } = runtimeFor(); assert.equal(compiled.playerSetup.talents.length, 5); const result = await runtime.createSession({ sessionId: "session.real", cardPackage: compiled.cardPackage, playerSetup: compiled.playerSetup }); assert.equal(result.valid, true, JSON.stringify(result)); assert.equal(result.value.revision, 0); assert.deepEqual(result.value.state, compiled.cardPackage.stateFields.map((field) => ({ fieldRef: field.id, value: field.initialValue }))); assert.equal(result.value.pluginAuthorizations.length, 0);
});

test("action generation emits bound valid events, retries idempotently, and commits only selected state", async () => {
  const fixture = baseRuntimeFixture(), { runtime, adapter } = runtimeFor(), events = []; await runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); const generated = await runtime.generateTurn(request(), { onEvent: (event) => events.push(structuredClone(event)) }); assert.equal(generated.valid, true); assert.equal(generated.value.session.pending.generationId, "generation.1"); assert.equal(adapter.calls, 1); assert.equal(events.length > 0, true); events.forEach((event) => assert.equal(validateRuntimeEvent(event).valid, true)); assert.deepEqual(events.map((event) => event.seq), events.map((_event, index) => index));
  const retry = await runtime.generateTurn(request()); assert.equal(retry.valid, true); assert.equal(adapter.calls, 1); const committed = await runtime.commitTurn(commit(fixture.session.sessionId, generated.value.session.revision)); assert.equal(committed.valid, true); assert.equal(committed.value.turns.length, 1); assert.equal(committed.value.state.find((item) => item.fieldRef === "state.scene-note").value, "quiet"); assert.equal(committed.value.state.find((item) => item.fieldRef === "state.player-alert").value, false); assert.equal(committed.value.turns[0].exchange.proposal.suggestedActions.length, 1);
  const committedRetry = await runtime.generateTurn(request()); assert.equal(committedRetry.valid, true); assert.equal(committedRetry.value.generation.status, "committed"); assert.equal(adapter.calls, 1);
});

test("a valid query can commit no state while invalid proposal keys or undeclared/read-only state never become pending", async () => {
  { const fixture = baseRuntimeFixture(), adapter = new MockAdapter(async () => ok({ status: "succeeded", outcome: "completed", dispatched: true, text: JSON.stringify(proposal({ stateProposals: [] })), observedModel: null, serverReceipt: null, cancellation: { requested: false, clientAborted: false, upstreamConfirmed: null }, usage: { input: null, output: null, total: null } })), { runtime } = runtimeFor(new MemoryStore(), adapter); await runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); const before = structuredClone((await runtime.readSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup })).value.state), generated = await runtime.generateTurn(request(fixture.session.sessionId, { input: { kind: "query", text: "status" } })); assert.equal(generated.valid, true); const committed = await runtime.commitTurn(commit(fixture.session.sessionId, generated.value.session.revision, [])); assert.deepEqual(committed.value.state, before); }
  for (const invalid of [proposal({ extra: "private" }), proposal({ stateProposals: [{ fieldRef: "state.unknown", proposedValue: "x", rationale: "bad" }] }), proposal({ stateProposals: [{ fieldRef: "state.player-alert", proposedValue: true, rationale: "bad" }] })]) { const fixture = baseRuntimeFixture(), adapter = new MockAdapter(async () => ok({ status: "succeeded", outcome: "completed", dispatched: true, text: JSON.stringify(invalid), observedModel: null, serverReceipt: null, cancellation: { requested: false, clientAborted: false, upstreamConfirmed: null }, usage: { input: null, output: null, total: null } })), { runtime } = runtimeFor(new MemoryStore(), adapter); await runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); assert.equal((await runtime.generateTurn(request())).valid, false); assert.equal((await runtime.readSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup })).value.pending, null); }
});

test("default empty host blocks required plugins and accepts every declared recommended fallback", async () => {
  const fixture = baseRuntimeFixture(); for (const [kind, fallback, expected] of [["required", null, false], ["recommended", "core", true], ["recommended", "omit", true], ["recommended", "readOnly", true]]) { const card = structuredClone(fixture.cardPackage); if (kind === "required") card.requiredPlugins = [{ pluginId: "plugin.missing", version: "1.0.0", capabilities: ["context.enrich"] }]; else card.recommendedPlugins = [{ pluginId: "plugin.missing", version: "1.0.0", capabilities: ["memory.augment"], fallback }]; const { runtime } = runtimeFor(); const result = await runtime.createSession({ sessionId: `session.${(fallback ?? "required").toLowerCase()}`, cardPackage: card, playerSetup: fixture.playerSetup }); assert.equal(result.valid, expected, `${fallback}: ${JSON.stringify(result)}`); }
});

test("speech and known command dispatch while unknown command and query state proposals fail before persistence", async () => {
  for (const input of [{ kind: "speech", text: "hello" }, { kind: "command", commandRef: "command.inspect-status", text: "inspect" }]) { const fixture = baseRuntimeFixture(), { runtime, adapter } = runtimeFor(); await runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); const result = await runtime.generateTurn(request(fixture.session.sessionId, { input })); assert.equal(result.valid, true); assert.equal(adapter.calls, 1); }
  { const fixture = baseRuntimeFixture(), { runtime, adapter } = runtimeFor(); await runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); const result = await runtime.generateTurn(request(fixture.session.sessionId, { input: { kind: "command", commandRef: "command.unknown", text: "inspect" } })); assert.equal(result.valid, false); assert.equal(adapter.calls, 0); }
  { const fixture = baseRuntimeFixture(), adapter = new MockAdapter(async () => ok({ status: "succeeded", outcome: "completed", dispatched: true, text: JSON.stringify(proposal()), observedModel: null, serverReceipt: null, cancellation: { requested: false, clientAborted: false, upstreamConfirmed: null }, usage: { input: null, output: null, total: null } })), { runtime } = runtimeFor(new MemoryStore(), adapter); await runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); const result = await runtime.generateTurn(request(fixture.session.sessionId, { input: { kind: "query", text: "status" } })); assert.equal(result.valid, false); const session = await runtime.readSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); assert.equal(session.value.pending, null); }
});

test("pending and revision bindings block second generation, stale commit, wrong exchange, and permit atomic discard", async () => {
  const fixture = baseRuntimeFixture(), { runtime, adapter } = runtimeFor(); await runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); const first = await runtime.generateTurn(request()); const revision = first.value.session.revision; assert.equal((await runtime.generateTurn(request(fixture.session.sessionId, { generationId: "generation.2", exchangeId: "exchange.2", expectedRevision: revision }))).valid, false); assert.equal(adapter.calls, 1); assert.equal((await runtime.commitTurn(commit(fixture.session.sessionId, revision - 1))).valid, false); const wrong = commit(fixture.session.sessionId, revision); wrong.exchangeId = "exchange.other"; assert.equal((await runtime.commitTurn(wrong)).valid, false); const discarded = await runtime.discardTurn({ sessionId: fixture.session.sessionId, generationId: "generation.1", exchangeId: "exchange.1", expectedRevision: revision }); assert.equal(discarded.valid, true); assert.equal(discarded.value.pending, null); assert.equal(discarded.value.turns.length, 0);
});

test("same generation ID with changed semantic input conflicts without redispatch", async () => {
  const fixture = baseRuntimeFixture(), { runtime, adapter } = runtimeFor(); await runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); await runtime.generateTurn(request()); const changed = request(); changed.input.text = "different"; assert.equal((await runtime.generateTurn(changed)).valid, false); assert.equal(adapter.calls, 1);
});

test("store failures and thrown external ports fail safely without dispatch or secret leakage", async () => {
  const fixture = baseRuntimeFixture(), store = new MemoryStore(), adapter = new MockAdapter(); store.failWrite = true; const built = runtimeFor(store, adapter); safeFailure(await built.runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup })); assert.equal(adapter.calls, 0);
  const throwing = { evidenceKind: "mock", async generate() { throw new Error("private-secret C:\\private\\path"); } }, second = runtimeFor(new MemoryStore(), throwing); await second.runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); safeFailure(await second.runtime.generateTurn(request()));
  for (const invalid of [null, 7, "invalid"]) { const adapter = new MockAdapter(async () => ok(invalid)), store = new MemoryStore(), built = runtimeFor(store, adapter); await built.runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); const result = await built.runtime.generateTurn(request()); assert.equal(result.valid, false); const saved = store.sessions.get(fixture.session.sessionId), generation = saved.generations[0]; assert.equal(generation.status, "failed"); assert.notEqual(generation.status, "active"); assert.equal(saved.pending, null); safeFailure(result); }
});

test("commit store failure leaves no half turn and observer failures cannot alter formal state", async () => {
  const fixture = baseRuntimeFixture(), store = new MemoryStore(), { runtime } = runtimeFor(store); await runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); const generated = await runtime.generateTurn(request(), { onEvent() { throw new Error("observer-secret"); } }); assert.equal(generated.valid, true); const before = structuredClone(store.sessions.get(fixture.session.sessionId)); store.failWrite = true; assert.equal((await runtime.commitTurn(commit(fixture.session.sessionId, before.revision))).valid, false); assert.deepEqual(store.sessions.get(fixture.session.sessionId), before);
});

test("active cancellation persists request, aborts generation, and late output cannot become pending", async () => {
  let release; const adapter = new MockAdapter(async (_request, { signal, onText }) => { await onText("partial"); await new Promise((resolve) => { release = resolve; signal.addEventListener("abort", resolve, { once: true }); }); return ok({ status: "cancelled", outcome: "cancelled", dispatched: true, text: "partial", observedModel: null, serverReceipt: null, cancellation: { requested: true, clientAborted: true, upstreamConfirmed: null }, usage: { input: null, output: null, total: null } }); }), fixture = baseRuntimeFixture(), { runtime } = runtimeFor(new MemoryStore(), adapter); await runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); const running = runtime.generateTurn(request()); await new Promise((resolve) => setTimeout(resolve, 0)); const active = await runtime.readSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); const cancelled = await runtime.cancelGeneration({ sessionId: fixture.session.sessionId, generationId: "generation.1", expectedRevision: active.value.revision }); assert.equal(cancelled.valid, true); release?.(); const final = await running; assert.equal(final.value.generation.status, "cancelled"); assert.equal(final.value.session.pending, null); assert.equal(final.value.generation.draftText, "partial"); assert.equal((await runtime.cancelGeneration({ sessionId: fixture.session.sessionId, generationId: "generation.1", expectedRevision: 0 })).valid, false);
});

test("accepted cancellation defeats an adapter that ignores abort and returns late success", async () => {
  let release; const adapter = new MockAdapter(async (_request, { onText }) => { await onText("partial"); await new Promise((resolve) => { release = resolve; }); return ok({ status: "succeeded", outcome: "completed", dispatched: true, text: JSON.stringify(proposal()), observedModel: null, serverReceipt: null, cancellation: { requested: false, clientAborted: false, upstreamConfirmed: null }, usage: { input: null, output: null, total: null } }); }), fixture = baseRuntimeFixture(), { runtime } = runtimeFor(new MemoryStore(), adapter); await runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); const running = runtime.generateTurn(request()); await new Promise((resolve) => setTimeout(resolve, 0)); const active = await runtime.readSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); assert.equal((await runtime.cancelGeneration({ sessionId: fixture.session.sessionId, generationId: "generation.1", expectedRevision: active.value.revision })).valid, true); release(); const final = await running; assert.equal(final.value.session.pending, null); assert.equal(final.value.session.turns.length, 0); assert.notEqual(final.value.generation.status, "pending");
});

test("cancelling an already completed generation with current revision preserves its receipt", async () => {
  const fixture = baseRuntimeFixture(), { runtime } = runtimeFor(); await runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); const generated = await runtime.generateTurn(request()), before = structuredClone(generated.value.generation.receipt), result = await runtime.cancelGeneration({ sessionId: fixture.session.sessionId, generationId: "generation.1", expectedRevision: generated.value.session.revision }); assert.equal(result.valid, true); assert.equal(result.value.outcome, "completed_before_cancel"); assert.deepEqual(result.value.generation.receipt, before); assert.deepEqual(result.value.session.generations[0].receipt, before);
});

test("persisted cancellation survives recovery and the old ignored-abort completion cannot overwrite it", async () => {
  let release, calls = 0; const adapter = new MockAdapter(async (_request, { onText }) => { calls += 1; if (calls === 1) { const text = JSON.stringify(proposal()); await onText(text); return ok({ status: "succeeded", outcome: "completed", dispatched: true, text, observedModel: null, serverReceipt: null, cancellation: { requested: false, clientAborted: false, upstreamConfirmed: null }, usage: { input: null, output: null, total: null } }); } await onText("late draft"); await new Promise((resolve) => { release = resolve; }); return ok({ status: "succeeded", outcome: "completed", dispatched: true, text: JSON.stringify(proposal()), observedModel: null, serverReceipt: null, cancellation: { requested: false, clientAborted: false, upstreamConfirmed: null }, usage: { input: null, output: null, total: null } }); }), fixture = baseRuntimeFixture(), store = new MemoryStore(), first = runtimeFor(store, adapter); await first.runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); const initial = await first.runtime.generateTurn(request()), committed = await first.runtime.commitTurn(commit(fixture.session.sessionId, initial.value.session.revision)); assert.equal(committed.value.turns.length, 1);
  const secondRequest = request(fixture.session.sessionId, { generationId: "generation.2", exchangeId: "exchange.2", expectedRevision: committed.value.revision }), running = first.runtime.generateTurn(secondRequest); await new Promise((resolve) => setTimeout(resolve, 0)); const active = await first.runtime.readSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }), cancellation = await first.runtime.cancelGeneration({ sessionId: fixture.session.sessionId, generationId: "generation.2", expectedRevision: active.value.revision }); assert.equal(cancellation.valid, true); assert.equal(cancellation.value.outcome, "cancel_requested"); const marker = cancellation.value.generation.cancelRequestedRevision; assert.equal(marker, cancellation.value.session.revision);
  const restarted = runtimeFor(store, new MockAdapter()), recovered = await restarted.runtime.resumeSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); assert.equal(recovered.valid, true); const interrupted = recovered.value.generations.find((item) => item.generationId === "generation.2"); assert.equal(interrupted.status, "interrupted"); assert.equal(interrupted.cancelRequestedRevision, marker); assert.equal(interrupted.receipt.cancellation.requested, true); assert.equal(recovered.value.turns.length, 1); assert.equal(recovered.value.state.find((item) => item.fieldRef === "state.scene-note").value, "quiet");
  const early = structuredClone(recovered.value); early.generations[1].cancelRequestedRevision = early.generations[1].startedRevision; assert.equal(validateRuntimeSession(early, fixture.cardPackage, fixture.playerSetup, sha256).valid, false); const future = structuredClone(recovered.value); future.generations[1].cancelRequestedRevision = future.revision + 1; assert.equal(validateRuntimeSession(future, fixture.cardPackage, fixture.playerSetup, sha256).valid, false); const contradiction = structuredClone(recovered.value); contradiction.generations[1].receipt.cancellation.requested = false; assert.equal(validateRuntimeSession(contradiction, fixture.cardPackage, fixture.playerSetup, sha256).valid, false);
  release(); assert.equal((await running).valid, false); const persisted = store.sessions.get(fixture.session.sessionId); assert.equal(persisted.generations[1].status, "interrupted"); assert.equal(persisted.generations[1].cancelRequestedRevision, marker); assert.equal(persisted.turns.length, 1); assert.equal(persisted.state.find((item) => item.fieldRef === "state.scene-note").value, "quiet");
});

test("persisted active can be read then resumed as interrupted without replay", async () => {
  const fixture = baseRuntimeFixture(), store = new MemoryStore(), session = structuredClone(fixture.session); session.revision = 1; session.generations.push({ generationId: "generation.1", exchangeId: "exchange.1", inputSha256: "a".repeat(64), modelId: "provider/model", evidenceKind: "mock", status: "active", requestRevision: 0, startedRevision: 1, draftText: "partial" }); store.sessions.set(session.sessionId, session); const opened = runtimeFor(store), same = await opened.runtime.readSession({ sessionId: session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); assert.equal(same.value.generations[0].status, "active"); const resumed = await opened.runtime.resumeSession({ sessionId: session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); assert.equal(resumed.valid, true); assert.equal(resumed.value.generations[0].status, "interrupted"); assert.equal(opened.adapter.calls, 0);
});

test("generation snapshots caller input, active retry is idempotent, and real in-flight work blocks resume and other sessions", async () => {
  let finish, observed; const adapter = new MockAdapter(async (received, { onText }) => { observed = structuredClone(received); await new Promise((resolve) => { finish = resolve; }); const text = JSON.stringify(proposal()); await onText(text); return ok({ status: "succeeded", outcome: "completed", dispatched: true, text, observedModel: null, serverReceipt: null, cancellation: { requested: false, clientAborted: false, upstreamConfirmed: null }, usage: { input: null, output: null, total: null } }); }), store = new MemoryStore(), { runtime } = runtimeFor(store, adapter), one = baseRuntimeFixture(), two = baseRuntimeFixture(); two.session.sessionId = "session.two"; await runtime.createSession({ sessionId: one.session.sessionId, cardPackage: one.cardPackage, playerSetup: one.playerSetup }); await runtime.createSession({ sessionId: two.session.sessionId, cardPackage: two.cardPackage, playerSetup: two.playerSetup }); const input = request(), before = structuredClone(input), running = runtime.generateTurn(input); input.messages[0].content = "mutated"; input.input.text = "mutated"; await new Promise((resolve) => setTimeout(resolve, 0)); assert.equal((await runtime.generateTurn(before)).valid, true); assert.equal(adapter.calls, 1); assert.equal((await runtime.resumeSession({ sessionId: one.session.sessionId, cardPackage: one.cardPackage, playerSetup: one.playerSetup })).valid, false); const blocked = await runtime.generateTurn(request("session.two", { generationId: "generation.2", exchangeId: "exchange.2" })); assert.equal(blocked.valid, false); assert.deepEqual(observed.messages, before.messages); assert.deepEqual(observed.input, before.input); finish(); assert.equal((await running).valid, true); assert.equal(adapter.calls, 1);
});

test("required plugin bootstrap survives restart but resume requires explicit registration and enable", async () => {
  const fixture = baseRuntimeFixture(), manifest = pluginManifest(), sessionId = "session.plugin-bootstrap";
  fixture.cardPackage.requiredPlugins = [{ pluginId: manifest.plugin.id, version: manifest.plugin.version, capabilities: ["context.enrich"] }];
  const grant = pluginGrant({ sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup, manifest });
  const firstHost = registeredHost(manifest); assert.equal(firstHost.enable(grant).valid, true);
  const store = new MemoryStore(), first = runtimeFor(store, new MockAdapter(), firstHost);
  const created = await first.runtime.createSession({ sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup, pluginAuthorizations: [grant] });
  assert.equal(created.valid, true, JSON.stringify(created)); assert.deepEqual(created.value.pluginAuthorizations, [grant]);
  const emptyHost = createPluginHost({ hash: sha256 }).value, restarted = runtimeFor(store, new MockAdapter(), emptyHost);
  const read = await restarted.runtime.readSession({ sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup });
  assert.equal(read.valid, true); assert.deepEqual(read.value.pluginAuthorizations, [grant]);
  assert.equal((await restarted.runtime.resumeSession({ sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup })).valid, false);
  assert.equal(emptyHost.register({ manifest, manifestSha256: canonicalSha(manifest), artifactSha256: manifest.provenance.artifactSha256, adapter: { async invoke() { return { proposals: [] }; } } }).valid, true);
  assert.equal(emptyHost.enable(grant).valid, true);
  const resumed = await restarted.runtime.resumeSession({ sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup });
  assert.equal(resumed.valid, true); assert.equal(restarted.adapter.calls, 0);
});

test("plugin bootstrap rejects missing host, missing enable, duplicate plugin, revoke, and bad bindings", async () => {
  const fixture = baseRuntimeFixture(), manifest = pluginManifest(), sessionId = "session.plugin-invalid";
  fixture.cardPackage.requiredPlugins = [{ pluginId: manifest.plugin.id, version: manifest.plugin.version, capabilities: ["context.enrich"] }];
  const grant = pluginGrant({ sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup, manifest });
  assert.equal((await runtimeFor().runtime.createSession({ sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup, pluginAuthorizations: [grant] })).valid, false);
  const disabledHost = registeredHost(manifest);
  assert.equal((await runtimeFor(new MemoryStore(), new MockAdapter(), disabledHost).runtime.createSession({ sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup, pluginAuthorizations: [grant] })).valid, false);
  for (const authorizations of [[grant, structuredClone(grant)], [{ ...grant, action: "revoke", permissions: [], read: [], propose: [], settings: [] }], [{ ...grant, revision: 1 }], [{ ...grant, evidenceKind: "real" }], [{ ...grant, sessionId: "session.other" }], [{ ...grant, cardPackageSha256: "0".repeat(64) }]]) {
    const host = registeredHost(manifest); if (authorizations.length === 1 && authorizations[0].action === "authorize") host.enable(authorizations[0]);
    assert.equal((await runtimeFor(new MemoryStore(), new MockAdapter(), host).runtime.createSession({ sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup, pluginAuthorizations: authorizations })).valid, false);
  }
});

test("plugin authorization CAS persists grants and revokes but never auto-enables or revives an old grant", async () => {
  const fixture = baseRuntimeFixture(), manifest = pluginManifest(), sessionId = "session.plugin-cas", host = registeredHost(manifest), store = new MemoryStore(), built = runtimeFor(store, new MockAdapter(), host);
  await built.runtime.createSession({ sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup });
  const grant = pluginGrant({ sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup, manifest, revision: 1 });
  const granted = await built.runtime.setPluginAuthorization({ sessionId, expectedRevision: 0, authorization: grant });
  assert.equal(granted.valid, true); assert.equal(granted.value.revision, 1); assert.deepEqual(granted.value.pluginAuthorizations, [grant]);
  assert.equal((await built.runtime.setPluginAuthorization({ sessionId, expectedRevision: 0, authorization: grant })).valid, false);
  const invokeInput = { pluginId: manifest.plugin.id, capability: "context.enrich", session: granted.value, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup };
  assert.equal((await host.invoke(invokeInput)).valid, false);
  assert.equal(host.enable(grant).valid, true);
  const enabledResult = await host.invoke(invokeInput); assert.equal(enabledResult.valid, true);
  const revoke = pluginGrant({ sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup, manifest, revision: 2, action: "revoke" });
  const revoked = await built.runtime.setPluginAuthorization({ sessionId, expectedRevision: 1, authorization: revoke });
  assert.equal(revoked.valid, true); assert.deepEqual(revoked.value.pluginAuthorizations, [grant, revoke]);
  assert.equal(host.validateResult(enabledResult.value, revoked.value).valid, false);
  assert.equal(host.enable(grant).valid, true);
  assert.equal((await host.invoke({ ...invokeInput, session: revoked.value })).valid, false);
});

test("authorization history rejects global disorder, repeated plugin revision, revision-zero revoke, and duplicate settings", () => {
  const fixture = baseRuntimeFixture(), manifest = pluginManifest(), session = structuredClone(fixture.session);
  const one = pluginGrant({ sessionId: session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup, manifest, revision: 1 });
  const otherManifest = pluginManifest(); otherManifest.plugin.id = "plugin.other";
  const two = pluginGrant({ sessionId: session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup, manifest: otherManifest, revision: 2 }); session.revision = 2;
  for (const history of [[two, one], [one, { ...one }], [{ ...one, revision: 0, action: "revoke", permissions: [], read: [], propose: [], settings: [] }], [{ ...one, settings: [{ key: "context.limit", value: 4 }, { key: "context.limit", value: 5 }] }], [{ ...one, action: "revoke" }]]) {
    const candidate = structuredClone(session); candidate.pluginAuthorizations = history; assert.equal(validateRuntimeSession(candidate).valid, false);
  }
});

test("authorization write failure and active generation preserve authority and formal state", async () => {
  const fixture = baseRuntimeFixture(), manifest = pluginManifest(), host = registeredHost(manifest), store = new MemoryStore(), built = runtimeFor(store, new MockAdapter(), host);
  await built.runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup });
  const grant = pluginGrant({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup, manifest, revision: 1 });
  const before = structuredClone(store.sessions.get(fixture.session.sessionId)); store.failWrite = true;
  assert.equal((await built.runtime.setPluginAuthorization({ sessionId: fixture.session.sessionId, expectedRevision: 0, authorization: grant })).valid, false);
  assert.deepEqual(store.sessions.get(fixture.session.sessionId), before); store.failWrite = false;
  const active = structuredClone(before); active.revision = 1; active.generations.push({ generationId: "generation.plugin", exchangeId: "exchange.plugin", inputSha256: "a".repeat(64), modelId: "provider/model", evidenceKind: "mock", status: "active", requestRevision: 0, startedRevision: 1, draftText: "" }); store.sessions.set(active.sessionId, active);
  const freshAdapter = new MockAdapter(), fresh = runtimeFor(store, freshAdapter, host);
  const opened = await fresh.runtime.readSession({ sessionId: active.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); assert.equal(opened.valid, true); assert.equal(opened.value.generations[0].status, "active");
  const activeResult = await fresh.runtime.setPluginAuthorization({ sessionId: active.sessionId, expectedRevision: 1, authorization: { ...grant, revision: 2 } });
  assert.equal(activeResult.valid, false); assert.equal(activeResult.diagnostics.some((item) => item.code === "RUNTIME_AUTHORIZATION_WHILE_ACTIVE"), true); assert.equal(freshAdapter.calls, 0);
  assert.deepEqual(store.sessions.get(active.sessionId), active);
});

test("disable failure leaves saved authorization but faults the runtime until recovery", async () => {
  const fixture = baseRuntimeFixture(), manifest = pluginManifest(), store = new MemoryStore(), adapter = new MockAdapter();
  let disableFails = true;
  const host = Object.freeze({ readiness() { return { ready: true, diagnostics: [] }; }, checkAuthorization() { return ok({}); }, disable() { if (disableFails) throw new Error("private-secret C:\\private\\plugin"); return ok(null); } });
  const built = runtimeFor(store, adapter, host); await built.runtime.createSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup });
  const grant = pluginGrant({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup, manifest, revision: 1 });
  const changed = await built.runtime.setPluginAuthorization({ sessionId: fixture.session.sessionId, expectedRevision: 0, authorization: grant });
  safeFailure(changed); const saved = store.sessions.get(fixture.session.sessionId); assert.equal(saved.revision, 1); assert.deepEqual(saved.pluginAuthorizations, [grant]);
  const read = await built.runtime.readSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup });
  assert.equal(read.valid, true); assert.deepEqual(read.value.pluginAuthorizations, [grant]); assert.equal(read.diagnostics.some((item) => item.code === "RUNTIME_SESSION_FAULTED" && item.severity === "warning"), true);
  const generation = await built.runtime.generateTurn(request(fixture.session.sessionId, { expectedRevision: 1 })); safeFailure(generation); assert.equal(generation.diagnostics.some((item) => item.code === "RUNTIME_SESSION_FAULTED"), true); assert.equal(adapter.calls, 0);
  const blockedAuthorization = await built.runtime.setPluginAuthorization({ sessionId: fixture.session.sessionId, expectedRevision: 1, authorization: { ...grant, revision: 2 } });
  safeFailure(blockedAuthorization); assert.equal(blockedAuthorization.diagnostics.some((item) => item.code === "RUNTIME_SESSION_FAULTED"), true); assert.deepEqual(store.sessions.get(fixture.session.sessionId), saved);
  disableFails = false;
  const resumed = await built.runtime.resumeSession({ sessionId: fixture.session.sessionId, cardPackage: fixture.cardPackage, playerSetup: fixture.playerSetup }); assert.equal(resumed.valid, true); assert.deepEqual(resumed.value.pluginAuthorizations, [grant]);
  const afterRecovery = await built.runtime.generateTurn(request(fixture.session.sessionId, { expectedRevision: resumed.value.revision })); assert.equal(afterRecovery.valid, true); assert.equal(adapter.calls, 1);
});
