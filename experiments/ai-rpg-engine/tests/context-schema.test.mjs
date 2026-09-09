import assert from "node:assert/strict";
import test from "node:test";
import { CONTEXT_FORMATS as F, CONTEXT_SCHEMAS, validateContextStructure as check } from "../context/index.mjs";
import { baseRuntimeFixture } from "./runtime-fixtures.mjs";
const h = "a".repeat(64);
const header = (kind) => ({ format: F[kind], formatVersion: "0.1.0" });
function fixtures() {
  const f = baseRuntimeFixture();
  const profile = { ...header("profile"), profile: { id: "profile.test", version: "0.1.0" }, cardPackage: f.session.resources.cardPackage, hostTemplate: { id: "host.test", version: "0.1.0", sha256: h }, budget: { inputLimit: 10000, loreLimit: 3000, historyLimit: 4000, outputLimit: 512 }, scenes: [], aliases: [], loreRules: [] };
  const input = { ...header("input"), ...f, profile, sceneRef: "scene.test", resourceRefs: [], generationId: "gen.test", exchangeId: "exchange.test", expectedRevision: 0, input: { kind: "query", text: "状态" }, modelId: "test/model", settings: { temperature: 0, maxTokens: 512 } };
  const bindings = { sessionId: f.session.sessionId, revision: 0, generationId: input.generationId, exchangeId: input.exchangeId, cardPackageSha256: f.session.resources.cardPackage.sha256, playerSetupSha256: f.session.resources.playerSetup.sha256, profileSha256: h, hostTemplateSha256: h };
  const receipt = { ...header("receipt"), bindings, evidenceKind: "offline", compilerVersion: "0.4.0", messagesSha256: h, generationInputSha256: h, measurement: { method: "utf8_bytes_with_overhead", accuracy: "estimate", counterRef: null, input: 120, required: 100, lore: 0, history: 0, overhead: 20, inputLimit: 10000, outputLimit: 512 }, loreDecisions: [], history: { includedExchangeIds: [], excludedTurnCount: 0 }, sourceRefs: [] };
  const request = { sessionId: f.session.sessionId, generationId: input.generationId, exchangeId: input.exchangeId, expectedRevision: 0, input: input.input, messages: [{ role: "user", content: "只返回结构化提案" }], modelId: input.modelId, settings: input.settings };
  return { profile, input, receipt, preparedTurn: { ...header("preparedTurn"), bindings, request, receipt } };
}
function freeze(value) { if (value && typeof value === "object") { Object.values(value).forEach(freeze); Object.freeze(value); } return value; }
test("four schemas compile, are deeply frozen, and accept representative shapes", () => {
  assert.equal(Object.keys(CONTEXT_SCHEMAS).length, 4);
  for (const [kind, fixture] of Object.entries(fixtures())) { assert.equal(check(kind, fixture).valid, true, kind); assert.equal(Object.isFrozen(CONTEXT_SCHEMAS[kind].properties), true); }
});
test("exact auxiliary version and closed envelopes reject unknown execution fields", () => {
  for (const [kind, value] of Object.entries(fixtures())) {
    assert.equal(check(kind, { ...value, formatVersion: "0.2.0" }).valid, false);
    for (const key of ["script", "rawHtml", "tools", "autoInstall", "endpoint"]) assert.equal(check(kind, { ...value, [key]: "untrusted" }).valid, false);
  }
});
test("nested profile declarations reject code, unbounded predicates and unknown fields", () => {
  const { profile } = fixtures();
  profile.aliases = [{ text: "村庄", kind: "worldbook", worldRef: null, resourceRef: "lore.village" }];
  profile.loreRules = [{ entryRef: "lore.village", worldRefs: [], required: false, priority: 0, trigger: { sceneRefs: [], resourceRefs: [], allKeywords: [], anyKeywords: ["村庄"], notKeywords: [], stateConditions: [{ fieldRef: "state.flag", operator: "eq", value: true }] }, conflictGroup: null, replaces: [] }];
  assert.equal(check("profile", profile).valid, true);
  profile.loreRules[0].trigger.stateConditions[0].operator = "eval";
  assert.equal(check("profile", profile).valid, false);
  profile.loreRules[0].trigger.stateConditions[0].operator = "eq";
  profile.loreRules[0].trigger.regex = ".*";
  assert.equal(check("profile", profile).valid, false);
});
test("full player remains five talents with no runtime permission; activation is explicit", () => {
  const { input } = fixtures(); assert.equal(input.playerSetup.talents.length, 5); assert.deepEqual(input.playerSetup.runtimePermissions, []);
  assert.equal(check("input", input).valid, true);
  input.playerSetup.runtimePermissions = ["root"];
  assert.equal(check("input", input).valid, false);
  input.playerSetup.runtimePermissions = []; delete input.playerSetup.talents[0].active;
  assert.equal(check("input", input).valid, false);
});
test("input kind, command reference and output bounds are structural requirements", () => {
  const { input } = fixtures();
  for (const kind of ["action", "speech", "query"]) { input.input = { kind, text: "观察" }; assert.equal(check("input", input).valid, true); }
  input.input = { kind: "command", text: "观察" }; assert.equal(check("input", input).valid, false);
  input.input.commandRef = "command.look"; assert.equal(check("input", input).valid, true);
  input.settings.maxTokens = 4097; assert.equal(check("input", input).valid, false);
});
test("public receipt rejects raw lore text, keyword or title fields", () => {
  const { receipt } = fixtures();
  receipt.loreDecisions = [{ entryRef: "lore.visible", disposition: "included", sourceRefs: [] }];
  assert.equal(check("receipt", receipt).valid, true);
  for (const key of ["title", "keywords", "content", "reason"]) { const copy = structuredClone(receipt); copy.loreDecisions[0][key] = "private"; assert.equal(check("receipt", copy).valid, false); }
});
test("prepared request preserves strict runtime structure and finite bounds", () => {
  const { preparedTurn } = fixtures();
  preparedTurn.request.messages[0].role = "tool"; assert.equal(check("preparedTurn", preparedTurn).valid, false);
  preparedTurn.request.messages[0].role = "user"; preparedTurn.bindings.revision = -1; assert.equal(check("preparedTurn", preparedTurn).valid, false);
  preparedTurn.bindings.revision = 0; preparedTurn.request.messages[0].content = "x".repeat(65537); assert.equal(check("preparedTurn", preparedTurn).valid, false);
});
test("preflight rejects getters without invoking them, cycles and nonfinite values", () => {
  let calls = 0; const value = {}; Object.defineProperty(value, "format", { enumerable: true, get() { calls++; throw new Error("secret"); } });
  assert.equal(check("profile", value).valid, false); assert.equal(calls, 0);
  const cycle = {}; cycle.self = cycle; assert.equal(check("profile", cycle).valid, false);
  const { profile } = fixtures(); profile.budget.inputLimit = Infinity; assert.equal(check("profile", profile).valid, false);
  assert.equal(check("unknown", {}).valid, false);
});
test("diagnostics are deterministic, immutable and contain no user data or unknown path", () => {
  const { profile } = fixtures(); profile["C:/private/secret"] = "confidential";
  const first = check("profile", profile); assert.deepEqual(first, check("profile", profile));
  assert.equal(JSON.stringify(first).includes("private"), false); assert.equal(JSON.stringify(first).includes("confidential"), false); assert.equal(Object.isFrozen(first.diagnostics[0]), true);
  const clean = fixtures(); const before = JSON.stringify(clean); for (const [kind, value] of Object.entries(freeze(clean))) assert.equal(check(kind, value).valid, true); assert.equal(JSON.stringify(clean), before);
});
test("structural success deliberately does not certify resource references or hash bindings", () => {
  const { input, preparedTurn } = fixtures(); input.sceneRef = "scene.missing";
  assert.equal(check("input", input).valid, true);
  preparedTurn.bindings.revision = 22; assert.equal(check("preparedTurn", preparedTurn).valid, true);
  // Semantic validators in 04A2b must reject these before a prepared turn is usable.
});
