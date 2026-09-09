import assert from "node:assert/strict";
import test from "node:test";
import { CONTEXT_FORMATS, validateContextProfile } from "../context/index.mjs";
import { canonicalJson } from "../runtime/contracts.mjs";
import { validateCardPackage } from "../src/index.mjs";
import { baseRuntimeFixture, sha256 } from "./runtime-fixtures.mjs";

function fixture(field, value) {
  const { cardPackage } = baseRuntimeFixture();
  cardPackage.stateFields = [structuredClone(field)];
  const worldRef = cardPackage.defaults.worldRef;
  const entry = cardPackage.resources.worldbookEntries.find((item) => item.worldRefs.length === 0 || item.worldRefs.includes(worldRef));
  const profile = {
    format: CONTEXT_FORMATS.profile,
    formatVersion: "0.1.0",
    profile: { id: "profile.state-compat", version: "0.1.0" },
    cardPackage: { id: cardPackage.package.id, version: cardPackage.package.version, sha256: sha256(canonicalJson(cardPackage).value) },
    hostTemplate: { id: "host.test", version: "0.1.0", sha256: "a".repeat(64) },
    budget: { inputLimit: 10000, loreLimit: 3000, historyLimit: 4000, outputLimit: 512 },
    scenes: [{ id: "scene.test", worldRef, openingRef: cardPackage.defaults.openingRef, requiredResourceRefs: [] }],
    aliases: [],
    loreRules: [{
      entryRef: entry.id,
      worldRefs: entry.worldRefs,
      required: false,
      priority: 0,
      trigger: { sceneRefs: [], resourceRefs: [], allKeywords: [], anyKeywords: [], notKeywords: [], stateConditions: [{ fieldRef: field.id, operator: "eq", value }] },
      conflictGroup: null,
      replaces: [],
    }],
  };
  assert.equal(validateCardPackage(cardPackage).valid, true);
  return { cardPackage, profile };
}

const integer = (bounds = {}) => ({ id: "state.counter", displayName: "计数", modelMayPropose: true, valueType: "integer", initialValue: 0, ...bounds });
const shortText = { id: "state.note", displayName: "备注", modelMayPropose: true, valueType: "shortText", initialValue: "", maxLength: 32 };

test("legacy integer conditions accept omitted and independently declared bounds", () => {
  for (const [field, value] of [[integer(), 0], [integer({ minimum: -2 }), -2], [integer({ maximum: 2 }), 2], [integer({ minimum: -2, maximum: 2 }), 0]]) {
    const f = fixture(field, value); assert.equal(validateContextProfile(f.profile, f.cardPackage).valid, true);
  }
});

test("legacy empty shortText condition remains valid while its declared maximum is enforced", () => {
  const empty = fixture(shortText, ""); assert.equal(validateContextProfile(empty.profile, empty.cardPackage).valid, true);
  const tooLong = fixture(shortText, "x".repeat(33)); assert.equal(validateContextProfile(tooLong.profile, tooLong.cardPackage).valid, false);
});

test("integer bounds, safe-integer type and shortText global limit still reject invalid conditions", () => {
  for (const [field, value] of [[integer({ minimum: 0 }), -1], [integer({ maximum: 1 }), 2], [integer({ minimum: 0, maximum: 2 }), 3]]) {
    const f = fixture(field, value); assert.equal(validateContextProfile(f.profile, f.cardPackage).diagnostics[0].code, "CONTEXT_STATE_CONDITION");
  }
  for (const value of [0.5, "0"]) {
    const f = fixture(integer(), value), first = validateContextProfile(f.profile, f.cardPackage), second = validateContextProfile(f.profile, f.cardPackage);
    assert.equal(first.valid, false); assert.deepEqual(first, second);
  }
  const overGlobal = fixture({ ...shortText, maxLength: 4096 }, "x".repeat(4097));
  assert.equal(validateContextProfile(overGlobal.profile, overGlobal.cardPackage).valid, false);
});

test("state compatibility validation is deterministic and does not mutate inputs", () => {
  const f = fixture(integer({ minimum: 0, maximum: 2 }), 3), before = structuredClone(f);
  const first = validateContextProfile(f.profile, f.cardPackage), second = validateContextProfile(f.profile, f.cardPackage);
  assert.deepEqual(first, second); assert.deepEqual(f, before);
  assert.deepEqual(first.diagnostics, [{ phase: "reference", severity: "error", code: "CONTEXT_STATE_CONDITION", path: "/loreRules" }]);
});
