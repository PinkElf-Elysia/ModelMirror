import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { validatePlayerSetup } from "../src/index.mjs";
import { validateContextInput, validateContextProfile } from "../context/index.mjs";
import { canonicalJson } from "../runtime/contracts.mjs";
import { buildTestScenario, createTestContextInput } from "../tooling/context-test-input.mjs";
import { loadRpg04ApprovedContextCardFixture } from "../tooling/context-approved-card.mjs";
import { loadRpg04ApprovedHost } from "../tooling/context-host.mjs";

const hash = (text) => createHash("sha256").update(text, "utf8").digest("hex");
function sessionFor(scenario) {
  return { format: "modelmirror.ai-rpg.runtime-session", formatVersion: "0.1.0", sessionId: `session.rpg04.${scenario.world}`, resources: { cardPackage: { id: scenario.cardPackage.package.id, version: scenario.cardPackage.package.version, sha256: scenario.bindings.cardPackageSha256 }, playerSetup: { setupId: scenario.playerSetup.setupId, sha256: scenario.bindings.playerSetupSha256 } }, revision: 0, state: scenario.cardPackage.stateFields.map((field) => ({ fieldRef: field.id, value: field.initialValue })), turns: [], generations: [], pending: null, pluginAuthorizations: [] };
}

test("builds valid neutral Gu and Minecraft scenarios bound to the composed approved host", () => {
  const approvedHost = loadRpg04ApprovedHost().value;
  for (const world of ["gu", "minecraft"]) {
    const scenario = buildTestScenario({ world });
    assert.equal(validatePlayerSetup(scenario.playerSetup, scenario.cardPackage).valid, true);
    assert.equal(validateContextProfile(scenario.profile, scenario.cardPackage).valid, true);
    assert.deepEqual(scenario.profile.hostTemplate, approvedHost.binding);
    assert.deepEqual(scenario.hostBinding, approvedHost.binding);
    assert.deepEqual(scenario.hostActivation, { ready: true, status: "user_approved_for_bounded_testing", blockers: [] });
    assert.equal(scenario.hostTemplate.id, "host.modelmirror-rpg04.approved-composed");
    assert.deepEqual(scenario.playerSetup.character.preferences, []);
    assert.equal(Object.hasOwn(scenario.playerSetup.character, "notes"), false);
    assert.equal(JSON.stringify(scenario.playerSetup).includes("XP"), false);
    assert.deepEqual(scenario.playerSetup.runtimePermissions, []);
  }
});

test("selects explicit opening identity and active talents for each world", () => {
  const gu = buildTestScenario({ world: "gu" }), minecraft = buildTestScenario({ world: "minecraft" });
  assert.equal(gu.sceneRef, "scene.rpg04.gu");
  assert.equal(gu.playerSetup.currentIdentity.resourceRef, "identity.gu.outer-disciple");
  assert.equal(gu.playerSetup.talents.length, 5);
  assert.equal(gu.playerSetup.talents.every((talent) => talent.owned && talent.active), true);
  assert.equal(minecraft.sceneRef, "scene.rpg04.minecraft");
  assert.equal(minecraft.playerSetup.opening.openingRef, "opening.rpg04-approved.minecraft");
  assert.equal(minecraft.playerSetup.currentIdentity.resourceRef, "identity.minecraft.first-night");
  assert.deepEqual(minecraft.playerSetup.talents.map((talent) => talent.resource.resourceRef), ["talent.minecraft.auto-fishing", "talent.minecraft.shaders", "talent.minecraft.steve-hand"]);
  assert.equal(minecraft.playerSetup.talents.every((talent) => talent.owned && talent.active), true);
});

test("creates three fixed replayable neutral context inputs without mutating the source fixture", () => {
  const original = loadRpg04ApprovedContextCardFixture(), before = canonicalJson(original).value;
  for (const world of ["gu", "minecraft"]) {
    const scenario = buildTestScenario({ world }), session = sessionFor(scenario);
    const inputs = [0, 1, 2].map((turnIndex) => createTestContextInput({ scenario, session, turnIndex, modelId: "provider/model" }));
    assert.deepEqual(inputs.map(({ input }) => input.kind), ["action", "speech", "query"]);
    assert.equal(inputs.every((input) => validateContextInput(input, { hash }).valid), true);
    assert.equal(inputs.every((input) => input.input.text.includes("权限变更") === false || input.input.kind === "query"), true);
    assert.deepEqual(inputs, [0, 1, 2].map((turnIndex) => createTestContextInput({ scenario, session, turnIndex, modelId: "provider/model" })));
  }
  assert.equal(canonicalJson(loadRpg04ApprovedContextCardFixture()).value, before);
});

test("rejects unknown worlds and caller-defined turn positions", () => {
  assert.throws(() => buildTestScenario({ world: "other" }), /RPG04_TEST_WORLD_INVALID/u);
  const scenario = buildTestScenario({ world: "gu" }), session = sessionFor(scenario);
  assert.throws(() => createTestContextInput({ scenario, session, turnIndex: 3, modelId: "provider/model" }), /RPG04_TEST_INPUT_ARGUMENT_INVALID/u);
});
