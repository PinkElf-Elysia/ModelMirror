import assert from "node:assert/strict";
import test from "node:test";
import { selectContextLore } from "../context/selection.mjs";
import { CONTEXT_FORMATS as F } from "../context/schemas.mjs";
import { baseRuntimeFixture } from "./runtime-fixtures.mjs";

function fixture() {
  const f = baseRuntimeFixture(), world = f.cardPackage.defaults.worldRef, opening = f.cardPackage.defaults.openingRef;
  const base = structuredClone(f.cardPackage.resources.worldbookEntries[0]);
  const entry = (id, visibility = "shared") => ({ ...structuredClone(base), id, displayName: `title-${id}`, content: `content-${id}`, visibility, worldRefs: [world] });
  f.cardPackage.resources.worldbookEntries.push(entry("lore.alpha"), entry("lore.beta"), entry("lore.host", "host"));
  const trigger = () => ({ sceneRefs: [], resourceRefs: [], allKeywords: [], anyKeywords: [], notKeywords: [], stateConditions: [] });
  const rule = (entryRef, priority = 0) => ({ entryRef, worldRefs: [world], required: false, priority, trigger: trigger(), conflictGroup: null, replaces: [] });
  const profile = { format: F.profile, formatVersion: "0.1.0", profile: { id: "profile.test", version: "0.1.0" }, cardPackage: f.session.resources.cardPackage, hostTemplate: { id: "host.test", version: "0.1.0", sha256: "a".repeat(64) }, budget: { inputLimit: 10000, loreLimit: 3000, historyLimit: 4000, outputLimit: 512 }, scenes: [{ id: "scene.test", worldRef: world, openingRef: opening, requiredResourceRefs: [] }], aliases: [], loreRules: [] };
  const input = { format: F.input, formatVersion: "0.1.0", ...f, profile, sceneRef: "scene.test", resourceRefs: [], generationId: "generation.test", exchangeId: "exchange.test", expectedRevision: 0, input: { kind: "query", text: "look" }, modelId: "test/model", settings: { temperature: 0, maxTokens: 512 } };
  return { input, rule };
}

test("combines scene resource alias keyword and state matching with stable ordering", () => {
  const { input, rule } = fixture(), a = rule("lore.alpha", 1), b = rule("lore.beta", 20);
  a.trigger.sceneRefs = [input.sceneRef]; a.trigger.allKeywords = ["look"]; a.trigger.notKeywords = ["blocked"];
  b.trigger.resourceRefs = [input.cardPackage.defaults.worldRef]; b.trigger.anyKeywords = ["look", "see"]; b.trigger.stateConditions = [{ fieldRef: input.session.state[0].fieldRef, operator: "eq", value: input.session.state[0].value }];
  input.resourceRefs = [input.cardPackage.defaults.worldRef]; input.profile.loreRules = [b, a];
  const result = selectContextLore(input);
  assert.equal(result.valid, true); assert.deepEqual(result.value.selectedEntries.map((e) => e.id), ["lore.alpha", "lore.beta"]);
  assert.deepEqual(result.value.loreDecisions.map((d) => d.disposition), ["included", "included"]);
});

test("longest explicit alias wins and optional misses are excluded normally", () => {
  const { input, rule } = fixture(), a = rule("lore.alpha"), b = rule("lore.beta");
  input.input.text = "visit silver village";
  input.profile.aliases = [{ text: "village", kind: "world", worldRef: input.profile.scenes[0].worldRef, resourceRef: input.cardPackage.defaults.worldRef }, { text: "silver village", kind: "world", worldRef: input.profile.scenes[0].worldRef, resourceRef: input.cardPackage.defaults.worldRef }];
  a.trigger.resourceRefs = [input.cardPackage.defaults.worldRef]; b.trigger.anyKeywords = ["missing"];
  input.profile.loreRules = [a, b]; const result = selectContextLore(input);
  assert.deepEqual(result.value.selectedEntries.map((e) => e.id), ["lore.alpha"]); assert.equal(result.value.loreDecisions[1].disposition, "not_matched");
});

test("required unresolved scope and unresolved conflicts fail closed", () => {
  const first = fixture(), required = first.rule("lore.alpha"); required.required = true; required.worldRefs = [];
  first.input.profile.scenes[0].requiredResourceRefs = ["lore.alpha"]; first.input.cardPackage.resources.worldbookEntries[0].worldRefs = [];
  first.input.profile.loreRules = []; assert.equal(selectContextLore(first.input).diagnostics[0].code, "CONTEXT_SELECTION_REQUIRED_UNRESOLVED");
  const second = fixture(), a = second.rule("lore.alpha"), b = second.rule("lore.beta"); a.conflictGroup = b.conflictGroup = "group.test"; second.input.profile.loreRules = [a, b];
  assert.equal(selectContextLore(second.input).diagnostics[0].code, "CONTEXT_SELECTION_CONFLICT");
});

test("replacement resolves a conflict deterministically", () => {
  const { input, rule } = fixture(), a = rule("lore.alpha"), b = rule("lore.beta"); a.conflictGroup = b.conflictGroup = "group.test"; a.replaces = [b.entryRef]; input.profile.loreRules = [b, a];
  const result = selectContextLore(input); assert.deepEqual(result.value.selectedEntries.map((e) => e.id), ["lore.alpha"]); assert.equal(result.value.loreDecisions.find((decision) => decision.entryRef === "lore.beta").disposition, "replaced");
});

test("host metadata never leaks and caller input is unchanged", () => {
  const { input, rule } = fixture(); input.profile.loreRules = [rule("lore.alpha"), rule("lore.host", 999)]; input.profile.loreRules[1].trigger.anyKeywords = ["look"];
  const before = JSON.stringify(input), result = selectContextLore(input), text = JSON.stringify(result);
  assert.equal(result.valid, true); assert.equal(JSON.stringify(input), before); assert.equal(text.includes("lore.host"), false); assert.equal(text.includes("title-lore.host"), false); assert.equal(result.value.loreDecisions.some((decision) => decision.entryRef === "lore.host"), false);
});

test("malformed input fails, while equal text in different alias kinds can coexist", () => {
  assert.equal(selectContextLore({}).valid, false);
  const { input, rule } = fixture(), worldRef = input.profile.scenes[0].worldRef; input.profile.aliases = [{ text: "look", kind: "world", worldRef, resourceRef: input.cardPackage.defaults.worldRef }, { text: "look", kind: "worldbook", worldRef, resourceRef: "lore.alpha" }]; input.profile.loreRules = [rule("lore.alpha")];
  assert.equal(selectContextLore(input).valid, true);
});

test("non-overlapping aliases coexist and longest alias wins only its overlap", () => {
  const { input, rule } = fixture(), worldRef = input.profile.scenes[0].worldRef, a = rule("lore.alpha"), b = rule("lore.beta");
  input.input.text = "silver village meets scout";
  input.profile.aliases = [{ text: "village", kind: "worldbook", worldRef, resourceRef: "lore.beta" }, { text: "silver village", kind: "worldbook", worldRef, resourceRef: "lore.alpha" }, { text: "scout", kind: "worldbook", worldRef, resourceRef: "lore.beta" }];
  a.trigger.resourceRefs = ["lore.alpha"]; b.trigger.resourceRefs = ["lore.beta"]; input.profile.loreRules = [a, b];
  assert.deepEqual(selectContextLore(input).value.selectedEntries.map((entry) => entry.id), ["lore.alpha", "lore.beta"]);
});

test("same-kind same-scope alias ambiguity is rejected by the profile contract", () => {
  const { input } = fixture(), worldRef = input.profile.scenes[0].worldRef;
  input.profile.aliases = [{ text: "look", kind: "worldbook", worldRef, resourceRef: "lore.alpha" }, { text: "look", kind: "worldbook", worldRef, resourceRef: "lore.beta" }];
  assert.equal(selectContextLore(input).diagnostics[0].code, "CONTEXT_ALIAS_AMBIGUOUS");
});

test("host aliases and host replacements cannot affect public selection", () => {
  const { input, rule } = fixture(), worldRef = input.profile.scenes[0].worldRef, visible = rule("lore.alpha"), host = rule("lore.host", 999);
  input.profile.aliases = [{ text: "secret", kind: "worldbook", worldRef, resourceRef: "lore.host" }, { text: "secret", kind: "worldbook", worldRef, resourceRef: "lore.alpha" }]; input.input.text = "secret";
  visible.trigger.resourceRefs = ["lore.host"]; host.conflictGroup = visible.conflictGroup = "group.test"; host.replaces = [visible.entryRef]; input.profile.loreRules = [host, visible];
  const result = selectContextLore(input); assert.equal(result.valid, true); assert.deepEqual(result.value.selectedEntries, []); assert.equal(JSON.stringify(result).includes("lore.host"), false);
});

test("replacement of any required rule fails closed", () => {
  const { input, rule } = fixture(), required = rule("lore.alpha"), replacer = rule("lore.beta"); required.required = true; required.conflictGroup = replacer.conflictGroup = "group.test"; replacer.replaces = [required.entryRef]; input.profile.loreRules = [required, replacer];
  assert.equal(selectContextLore(input).diagnostics[0].code, "CONTEXT_SELECTION_REQUIRED_CONFLICT");
});

test("rule declaration order does not change entries or public decisions", () => {
  const first = fixture(), a = first.rule("lore.alpha"), b = first.rule("lore.beta"); first.input.profile.loreRules = [b, a];
  const second = structuredClone(first.input); second.profile.loreRules.reverse();
  assert.deepEqual(selectContextLore(first.input).value, selectContextLore(second).value);
});

test("a longer alias cannot suppress an overlapping alias of another kind", () => {
  const { input, rule } = fixture(), worldRef = input.profile.scenes[0].worldRef, a = rule("lore.alpha"), b = rule("lore.beta"); input.input.text = "silver village";
  input.profile.aliases = [{ text: "silver village", kind: "world", worldRef, resourceRef: input.cardPackage.defaults.worldRef }, { text: "village", kind: "worldbook", worldRef, resourceRef: "lore.beta" }];
  a.trigger.resourceRefs = [input.cardPackage.defaults.worldRef]; b.trigger.resourceRefs = ["lore.beta"]; input.profile.loreRules = [a, b];
  assert.deepEqual(selectContextLore(input).value.selectedEntries.map((entry) => entry.id), ["lore.alpha", "lore.beta"]);
});

test("excessive alias occurrence work fails explicitly", () => {
  const { input } = fixture(), worldRef = input.profile.scenes[0].worldRef; input.input.text = "a".repeat(4097);
  input.profile.aliases = [{ text: "a", kind: "world", worldRef, resourceRef: input.cardPackage.defaults.worldRef }];
  assert.equal(selectContextLore(input).diagnostics[0].code, "CONTEXT_SELECTION_MATCH_LIMIT");
});
