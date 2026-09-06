import assert from "node:assert/strict";
import test from "node:test";
import * as runtime from "../runtime/index.mjs";
import { baseRuntimeFixture, compileVerifiedContent, sha256 } from "./runtime-fixtures.mjs";

const request = () => ({ sessionId: "session.fixture", generationId: "generation.1", exchangeId: "exchange.1", expectedRevision: 0, input: { kind: "action", text: "Look around." }, messages: [{ role: "user", content: "Return JSON." }], modelId: "provider/model", settings: { temperature: 0, maxTokens: 512 } });
const proposal = () => ({ narrative: "A quiet scene.", suggestedActions: [], informationModules: [], stateProposals: [{ fieldRef: "state.scene-note", proposedValue: "quiet", rationale: "scene" }], uncertainties: [] });
function receipt(session, status = "succeeded", revision = 2) { return { format: runtime.RUNTIME_FORMATS.generationReceipt, formatVersion: runtime.RUNTIME_FORMAT_VERSION, sessionId: session.sessionId, cardPackageSha256: session.resources.cardPackage.sha256, playerSetupSha256: session.resources.playerSetup.sha256, generationId: "generation.1", exchangeId: "exchange.1", revision, evidenceKind: "mock", status, outcome: status === "succeeded" ? "completed" : status, requestedModel: "provider/model", observedModel: status === "succeeded" ? null : null, serverReceipt: null, cancellation: { requested: status === "cancelled", clientAborted: status === "cancelled", upstreamConfirmed: null }, outputSha256: status === "succeeded" ? runtime.computeProposalSha256(proposal(), sha256).value : null, usage: { input: null, output: null, total: null }, costUsd: null }; }

test("exports independent runtime 0.1.0 schemas and validators", () => {
  assert.equal(runtime.RUNTIME_FORMAT_VERSION, "0.1.0");
  for (const name of ["RUNTIME_SESSION_SCHEMA", "RUNTIME_EVENT_SCHEMA", "GENERATION_RECEIPT_SCHEMA", "PLUGIN_AUTHORIZATION_SCHEMA", "CREATE_SESSION_REQUEST_SCHEMA", "GENERATE_TURN_REQUEST_SCHEMA", "COMMIT_TURN_REQUEST_SCHEMA"]) { assert.equal(runtime[name].$schema, "https://json-schema.org/draft/2020-12/schema"); assert.equal(Object.isFrozen(runtime[name]), true); }
});

test("canonical JSON sorts keys and rejects accessors, cycles, functions, symbols and prototypes without invoking getters", () => {
  assert.equal(runtime.canonicalJson({ b: 1, a: [true, null] }).value, '{"a":[true,null],"b":1}');
  let invoked = false; const accessor = {}; Object.defineProperty(accessor, "secret", { enumerable: true, get() { invoked = true; return "private"; } });
  assert.equal(runtime.canonicalJson(accessor).valid, false); assert.equal(invoked, false);
  const cycle = {}; cycle.self = cycle; assert.equal(runtime.canonicalJson(cycle).valid, false);
  assert.equal(runtime.canonicalJson({ value() {} }).valid, false); assert.equal(runtime.canonicalJson({ [Symbol("x")]: 1 }).valid, false); assert.equal(runtime.canonicalJson(new Date()).valid, false);
  assert.equal(runtime.canonicalJson(Array(1)).valid, false); const extra = []; extra.named = 1; assert.equal(runtime.canonicalJson(extra).valid, false); const hidden = {}; Object.defineProperty(hidden, "x", { value: 1 }); assert.equal(runtime.canonicalJson(hidden).valid, false);
});

test("canonical JSON rejects huge sparse arrays before property traversal and preserves input", () => {
  let invoked = false; const huge = []; huge.length = 1_000_000_000; Object.defineProperty(huge, "probe", { enumerable: true, get() { invoked = true; return "private"; } });
  const beforeLength = huge.length, result = runtime.canonicalJson(huge);
  assert.equal(result.valid, false); assert.equal(result.diagnostics[0].code, "RUNTIME_JSON_NODE_LIMIT"); assert.equal(invoked, false); assert.equal(huge.length, beforeLength); assert.equal(Object.hasOwn(huge, "probe"), true);
});

test("validates zero-plugin session without mutating frozen inputs", () => {
  const fixture = baseRuntimeFixture(), before = structuredClone(fixture);
  assert.equal(runtime.validateRuntimeSession(fixture.session, fixture.cardPackage, fixture.playerSetup, sha256).valid, true); assert.equal(runtime.validateRuntimeResourceBindings(fixture.cardPackage, fixture.playerSetup, fixture.session.resources, sha256).valid, true); assert.deepEqual(fixture, before);
  fixture.session.resources.cardPackage.sha256 = "0".repeat(64); assert.equal(runtime.validateRuntimeResourceBindings(fixture.cardPackage, fixture.playerSetup, fixture.session.resources, sha256).valid, false);
});

test("real RPG02 helper replays compile and verifies source hash", () => { const value = compileVerifiedContent(); assert.equal(value.cardPackage.resources.worlds.length, 2); assert.equal(value.playerSetup.runtimePermissions.length, 0); });

test("generate request enforces prepared message count and total without truncation", () => {
  assert.equal(runtime.validateGenerateTurnRequest(request()).valid, true);
  const tooMany = request(); tooMany.messages = Array.from({ length: 81 }, () => ({ role: "user", content: "x" })); assert.equal(runtime.validateGenerateTurnRequest(tooMany).valid, false);
  const tooLarge = request(); tooLarge.messages = Array.from({ length: 5 }, () => ({ role: "user", content: "x".repeat(60000) })); assert.equal(runtime.validateGenerateTurnRequest(tooLarge).diagnostics[0].code, "RUNTIME_MESSAGES_TOTAL_LIMIT");
});

test("input hash excludes expectedRevision and generationId but binds exchange and semantic input", () => {
  const { session } = baseRuntimeFixture(), first = request(), second = request(); second.expectedRevision = 99; second.generationId = "generation.retry";
  assert.equal(runtime.computeGenerationInputSha256(first, session, sha256).value, runtime.computeGenerationInputSha256(second, session, sha256).value);
  second.exchangeId = "exchange.2"; assert.notEqual(runtime.computeGenerationInputSha256(first, session, sha256).value, runtime.computeGenerationInputSha256(second, session, sha256).value);
  assert.equal(runtime.computeGenerationInputSha256(first, session, async () => "a".repeat(64)).diagnostics[0].code, "RUNTIME_HASH_ASYNC");
});

test("model proposal requires exactly five keys then delegates frozen turn validation", () => {
  const { cardPackage } = baseRuntimeFixture(), input = request().input; assert.equal(runtime.validateModelProposal(proposal(), "exchange.1", input, cardPackage).valid, true);
  const extra = proposal(); extra.privateText = "do-not-echo"; const result = runtime.validateModelProposal(extra, "exchange.1", input, cardPackage); assert.equal(result.valid, false); assert.equal(JSON.stringify(result).includes("privateText"), false); assert.equal(JSON.stringify(result).includes("do-not-echo"), false);
  assert.equal(runtime.validateModelProposal(proposal(), "exchange.1", { kind: "query", text: "status" }, cardPackage).valid, false);
});

test("receipt and receipt event bind evidence and reject failed output", () => {
  const { session } = baseRuntimeFixture(), ok = receipt(session); assert.equal(ok.outputSha256, runtime.computeProposalSha256(proposal(), sha256).value); assert.equal(runtime.validateGenerationReceipt(ok).valid, true);
  const failed = receipt(session, "failed"); failed.outputSha256 = "b".repeat(64); assert.equal(runtime.validateGenerationReceipt(failed).valid, false);
  const event = { format: runtime.RUNTIME_FORMATS.event, formatVersion: runtime.RUNTIME_FORMAT_VERSION, sessionId: session.sessionId, cardPackageSha256: session.resources.cardPackage.sha256, playerSetupSha256: session.resources.playerSetup.sha256, generationId: "generation.1", exchangeId: "exchange.1", revision: 2, evidenceKind: "mock", seq: 1, type: "receipt", receipt: ok };
  assert.equal(runtime.validateRuntimeEvent(event).valid, true); event.exchangeId = "exchange.other"; assert.equal(runtime.validateRuntimeEvent(event).valid, false);
  const race = receipt(session); race.cancellation.requested = true; assert.equal(runtime.validateGenerationReceipt(race).valid, true); race.cancellation.upstreamConfirmed = true; assert.equal(runtime.validateGenerationReceipt(race).valid, false);
});

test("receipt structural validation is hash-optional and hash verification fails closed", () => {
  const { session } = baseRuntimeFixture(), ok = receipt(session), before = structuredClone(ok);
  assert.equal(runtime.validateGenerationReceipt(ok).valid, true); assert.equal(runtime.validateGenerationReceipt(ok, proposal(), sha256).valid, true);
  const drift = proposal(); drift.narrative = "different"; assert.equal(runtime.validateGenerationReceipt(ok, drift, sha256).valid, false);
  assert.equal(runtime.validateGenerationReceipt(ok, proposal(), () => { throw new Error("hash failed"); }).valid, false); assert.equal(runtime.validateGenerationReceipt(ok, proposal(), async () => "a".repeat(64)).valid, false);
  const malformed = structuredClone(ok); malformed.outputSha256 = null; assert.doesNotThrow(() => runtime.validateGenerationReceipt(malformed, proposal(), sha256)); assert.equal(runtime.validateGenerationReceipt(malformed, proposal(), sha256).valid, false); assert.deepEqual(ok, before);
});

test("failed receipt may preserve an actually observed mismatched model", () => {
  const { session } = baseRuntimeFixture(), failed = receipt(session, "failed"); failed.observedModel = "provider/error-model"; assert.equal(runtime.validateGenerationReceipt(failed).valid, true);
  const succeeded = receipt(session); succeeded.observedModel = "provider/error-model"; assert.equal(runtime.validateGenerationReceipt(succeeded).valid, false);
});

test("session rejects duplicate generation IDs, future revisions and invalid pending references", () => {
  const fixture = baseRuntimeFixture(), session = fixture.session; session.revision = 1; session.generations.push({ generationId: "generation.1", exchangeId: "exchange.1", inputSha256: "c".repeat(64), modelId: "provider/model", evidenceKind: "mock", status: "active", requestRevision: 0, startedRevision: 1, draftText: "" });
  assert.equal(runtime.validateRuntimeSession(session, fixture.cardPackage, fixture.playerSetup).valid, true);
  session.generations.push(structuredClone(session.generations[0])); session.generations[1].startedRevision = 2; const codes = runtime.validateRuntimeSession(session, fixture.cardPackage, fixture.playerSetup).diagnostics.map((entry) => entry.code); assert.equal(codes.includes("RUNTIME_GENERATION_ID_DUPLICATE"), true); assert.equal(codes.includes("RUNTIME_GENERATION_REVISION"), true);
  session.generations.pop(); session.pending = { generationId: "generation.1", exchangeId: "exchange.1" }; assert.equal(runtime.validateRuntimeSession(session, fixture.cardPackage, fixture.playerSetup).valid, false);
});

test("committed turn requires matching generation and accepted proposal subset", () => {
  const fixture = baseRuntimeFixture(), exchange = runtime.validateModelProposal(proposal(), "exchange.1", request().input, fixture.cardPackage).value, session = fixture.session;
  session.revision = 3; session.generations.push({ generationId: "generation.1", exchangeId: "exchange.1", inputSha256: "d".repeat(64), modelId: "provider/model", evidenceKind: "mock", status: "committed", requestRevision: 0, startedRevision: 1, finishedRevision: 2, resolvedRevision: 3, draftText: "", exchange, receipt: receipt(session) }); session.turns.push({ generationId: "generation.1", exchange: structuredClone(exchange), committedRevision: 3, acceptedStateFields: ["state.scene-note"] }); session.state.find((entry) => entry.fieldRef === "state.scene-note").value = "quiet";
  assert.equal(runtime.validateRuntimeSession(session, fixture.cardPackage, fixture.playerSetup).valid, true); session.turns[0].acceptedStateFields = ["state.player-alert"]; assert.equal(runtime.validateRuntimeSession(session, fixture.cardPackage, fixture.playerSetup).valid, false);
});

test("bad persisted exchange fails structurally without entering replay", () => {
  const fixture = baseRuntimeFixture(), session = fixture.session; session.revision = 2; session.pending = { generationId: "generation.1", exchangeId: "exchange.1" };
  session.generations.push({ generationId: "generation.1", exchangeId: "exchange.1", inputSha256: "9".repeat(64), modelId: "provider/model", evidenceKind: "mock", status: "pending", requestRevision: 0, startedRevision: 1, finishedRevision: 2, draftText: "", exchange: {}, receipt: receipt(session) });
  assert.doesNotThrow(() => runtime.validateRuntimeSession(session, fixture.cardPackage, fixture.playerSetup)); assert.equal(runtime.validateRuntimeSession(session, fixture.cardPackage, fixture.playerSetup).valid, false);
});

test("pending and discarded generations bind persisted exchange IDs directly", () => {
  for (const status of ["pending", "discarded"]) {
    const fixture = baseRuntimeFixture(), session = fixture.session, exchange = runtime.validateModelProposal(proposal(), "exchange.other", request().input, fixture.cardPackage).value;
    session.revision = status === "pending" ? 2 : 3; session.generations.push({ generationId: "generation.1", exchangeId: "exchange.1", inputSha256: "8".repeat(64), modelId: "provider/model", evidenceKind: "mock", status, requestRevision: 0, startedRevision: 1, finishedRevision: 2, ...(status === "discarded" ? { resolvedRevision: 3 } : {}), draftText: "", exchange, receipt: receipt(session) });
    if (status === "pending") session.pending = { generationId: "generation.1", exchangeId: "exchange.1" };
    const before = structuredClone(session), result = runtime.validateRuntimeSession(session, fixture.cardPackage, fixture.playerSetup); assert.equal(result.valid, false); assert.equal(result.diagnostics.some((entry) => entry.code === "RUNTIME_GENERATION_EXCHANGE_BINDING"), true); assert.deepEqual(session, before);
  }
});

test("session resource and output hash verification reject independent drift", () => {
  const fixture = baseRuntimeFixture(), changedCard = structuredClone(fixture.cardPackage); changedCard.package.displayName = "changed"; assert.equal(runtime.validateRuntimeSession(fixture.session, changedCard, fixture.playerSetup).valid, true); assert.equal(runtime.validateRuntimeSession(fixture.session, changedCard, fixture.playerSetup, sha256).diagnostics.some((entry) => entry.code === "RUNTIME_RESOURCE_HASH_MISMATCH"), true);
  const session = fixture.session, exchange = runtime.validateModelProposal(proposal(), "exchange.1", request().input, fixture.cardPackage).value; session.revision = 2; session.pending = { generationId: "generation.1", exchangeId: "exchange.1" }; const badReceipt = receipt(session); badReceipt.outputSha256 = "7".repeat(64);
  session.generations.push({ generationId: "generation.1", exchangeId: "exchange.1", inputSha256: "6".repeat(64), modelId: "provider/model", evidenceKind: "mock", status: "pending", requestRevision: 0, startedRevision: 1, finishedRevision: 2, draftText: "", exchange, receipt: badReceipt }); assert.equal(runtime.validateRuntimeSession(session, fixture.cardPackage, fixture.playerSetup).valid, true); assert.equal(runtime.validateRuntimeSession(session, fixture.cardPackage, fixture.playerSetup, sha256).diagnostics.some((entry) => entry.code === "RUNTIME_RECEIPT_OUTPUT_HASH_MISMATCH"), true);
});

test("revision counters reject integers beyond Number.MAX_SAFE_INTEGER", () => {
  const generate = request(); generate.expectedRevision = Number.MAX_SAFE_INTEGER + 1; assert.equal(runtime.validateGenerateTurnRequest(generate).valid, false);
  const { session } = baseRuntimeFixture(); session.revision = Number.MAX_SAFE_INTEGER + 1; assert.equal(runtime.validateRuntimeSession(session).valid, false);
});

test("commit request requires unique selected fields", () => {
  const commit = { format: runtime.RUNTIME_FORMATS.turnCommit, formatVersion: runtime.RUNTIME_FORMAT_VERSION, sessionId: "session.fixture", generationId: "generation.1", exchangeId: "exchange.1", expectedRevision: 2, acceptedStateFields: ["state.scene-note"] };
  assert.equal(runtime.validateCommitTurnRequest(commit).valid, true); commit.acceptedStateFields.push("state.scene-note"); assert.equal(runtime.validateCommitTurnRequest(commit).valid, false);
});

test("plugin authorization is strict declarative data only", () => {
  const { session } = baseRuntimeFixture(), value = { format: runtime.RUNTIME_FORMATS.pluginAuthorization, formatVersion: runtime.RUNTIME_FORMAT_VERSION, sessionId: session.sessionId, cardPackageSha256: session.resources.cardPackage.sha256, playerSetupSha256: session.resources.playerSetup.sha256, revision: 0, evidenceKind: "mock", action: "authorize", pluginId: "plugin.fixture", version: "1.0.0", manifestSha256: "e".repeat(64), artifactSha256: "f".repeat(64), permissions: ["card.read"], read: ["card"], propose: [], settings: [{ key: "limit", value: 4 }] };
  assert.equal(runtime.validatePluginAuthorization(value).valid, true); value.entrypoint = "private"; const result = runtime.validatePluginAuthorization(value); assert.equal(result.valid, false); assert.equal(JSON.stringify(result).includes("entrypoint"), false);
});
