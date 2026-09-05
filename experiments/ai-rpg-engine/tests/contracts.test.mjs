import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import {
  CARD_PACKAGE_SCHEMA,
  FORMAT_VERSION,
  FORMATS,
  PLUGIN_CAPABILITIES,
  PLUGIN_MANIFEST_SCHEMA,
  PLUGIN_PERMISSIONS,
  PLAYER_SETUP_SCHEMA,
  SCHEMAS,
  TURN_EXCHANGE_SCHEMA,
  validateCardPackage,
  evaluatePluginReadiness,
  validatePlayerSetup,
  validateTurnExchange,
} from "../src/index.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixture = JSON.parse(await fs.readFile(path.join(moduleRoot, "fixtures", "zero-plugin.card-package.json"), "utf8"));
const playerFixture = JSON.parse(await fs.readFile(path.join(moduleRoot, "fixtures", "bai-yu-ling-yin.player-setup.json"), "utf8"));
const turnFixture = JSON.parse(await fs.readFile(path.join(moduleRoot, "fixtures", "minimal.turn-exchange.json"), "utf8"));
const pluginFixtures = JSON.parse(await fs.readFile(path.join(moduleRoot, "fixtures", "plugin-manifests.json"), "utf8"));

function clone(value) {
  return structuredClone(value);
}

function freezeDeep(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) freezeDeep(child);
  return Object.freeze(value);
}

function codes(report) {
  return report.diagnostics.map((item) => item.code);
}

test("RPG-01 format constants and exported card schema are frozen and compile strictly", () => {
  assert.equal(FORMAT_VERSION, "0.1.0");
  assert.deepEqual(FORMATS, {
    cardPackage: "modelmirror.ai-rpg.card-package",
    playerSetup: "modelmirror.ai-rpg.player-setup",
    turnExchange: "modelmirror.ai-rpg.turn-exchange",
    pluginManifest: "modelmirror.ai-rpg.plugin-manifest",
  });
  assert.equal(Object.isFrozen(CARD_PACKAGE_SCHEMA), true);
  assert.equal(Object.isFrozen(PLAYER_SETUP_SCHEMA), true);
  assert.equal(Object.isFrozen(TURN_EXCHANGE_SCHEMA), true);
  assert.equal(Object.isFrozen(PLUGIN_MANIFEST_SCHEMA), true);
  assert.equal(Object.isFrozen(PLUGIN_CAPABILITIES), true);
  assert.equal(Object.isFrozen(PLUGIN_PERMISSIONS), true);
  assert.equal(Object.isFrozen(SCHEMAS), true);
  const strictAjv = new Ajv2020({ strict: true, allErrors: true, validateFormats: false });
  assert.equal(typeof strictAjv.compile(CARD_PACKAGE_SCHEMA), "function");
  assert.equal(typeof strictAjv.compile(PLAYER_SETUP_SCHEMA), "function");
  assert.equal(typeof strictAjv.compile(TURN_EXCHANGE_SCHEMA), "function");
  assert.equal(typeof strictAjv.compile(PLUGIN_MANIFEST_SCHEMA), "function");
});

test("complete virtual player fixture preserves all five talents and separates semantic axes", () => {
  assert.deepEqual(validatePlayerSetup(playerFixture, fixture), { valid: true, diagnostics: [] });
  assert.equal(playerFixture.character.name, "白羽绫音");
  assert.equal(playerFixture.character.age, 18);
  assert.deepEqual(playerFixture.character.preferences, ["百合", "SM"]);
  assert.equal(playerFixture.talents.length, 5);
  assert.equal(playerFixture.talents.every((entry) => entry.owned), true);
  assert.equal(fixture.resources.identities[0].rankLabel, "E");
  assert.deepEqual(fixture.resources.talents.map((entry) => entry.tierLabel), ["SSS", "SSS", "SSS", "SSS", "UR"]);
  assert.equal(playerFixture.characterPower.status, "unspecified");
  assert.notEqual(playerFixture.inherentBackgrounds[0].resourceRef, playerFixture.currentIdentity.resourceRef);
  assert.deepEqual(playerFixture.runtimePermissions, []);
  assert.equal(fixture.resources.talents.at(-1).displayName.includes("root"), true);
});

test("custom player resources are declarative, typed, unique, and collision checked", () => {
  const custom = clone(playerFixture);
  custom.inherentBackgrounds = [{
    source: "custom",
    resource: {
      id: "custom.background.dragon-princess",
      kind: "background",
      displayName: "龙人族公主",
      description: "玩家自定义固有背景。",
    },
  }];
  assert.equal(validatePlayerSetup(custom, fixture).valid, true);

  const wrongKind = clone(custom);
  wrongKind.inherentBackgrounds[0].resource.kind = "identity";
  assert.equal(codes(validatePlayerSetup(wrongKind, fixture)).includes("PLAYER_SETUP_CUSTOM_RESOURCE_KIND_MISMATCH"), true);

  const collision = clone(custom);
  collision.inherentBackgrounds[0].resource.id = "world.reverend-insanity";
  assert.equal(codes(validatePlayerSetup(collision, fixture)).includes("PLAYER_SETUP_CUSTOM_RESOURCE_ID_COLLISION"), true);
});

test("player setup rejects dangling refs, active unowned talents, package mismatch, and runtime permissions", () => {
  const dangling = clone(playerFixture);
  dangling.talents[0].resource.resourceRef = "talent.missing";
  assert.equal(codes(validatePlayerSetup(dangling, fixture)).includes("PLAYER_SETUP_TALENT_REF_MISSING"), true);

  const unowned = clone(playerFixture);
  unowned.talents[0].owned = false;
  assert.equal(codes(validatePlayerSetup(unowned, fixture)).includes("PLAYER_SETUP_UNOWNED_TALENT_ACTIVE"), true);

  const mismatch = clone(playerFixture);
  mismatch.cardPackageRef.version = "9.9.9";
  assert.equal(codes(validatePlayerSetup(mismatch, fixture)).includes("PLAYER_SETUP_CARD_PACKAGE_REF_MISMATCH"), true);

  const permission = clone(playerFixture);
  permission.runtimePermissions = ["system.root"];
  const permissionReport = validatePlayerSetup(permission, fixture);
  assert.equal(permissionReport.valid, false);
  assert.equal(codes(permissionReport).includes("PLAYER_SETUP_SCHEMA_ARRAY_BOUNDS"), true);
});

test("player validation accepts frozen input without mutation and returns isolated reports", () => {
  const frozenPlayer = freezeDeep(clone(playerFixture));
  const frozenCard = freezeDeep(clone(fixture));
  const before = JSON.stringify([frozenPlayer, frozenCard]);
  const first = validatePlayerSetup(frozenPlayer, frozenCard);
  const second = validatePlayerSetup(frozenPlayer, frozenCard);
  assert.deepEqual(first, second);
  assert.notEqual(first.diagnostics, second.diagnostics);
  assert.equal(JSON.stringify([frozenPlayer, frozenCard]), before);
});

test("minimal turn exchange is a structured proposal and validates without executing suggestions", () => {
  assert.deepEqual(validateTurnExchange(turnFixture, fixture), { valid: true, diagnostics: [] });
  assert.deepEqual(Object.keys(turnFixture.proposal), [
    "narrative", "suggestedActions", "informationModules", "stateProposals", "uncertainties",
  ]);
  assert.equal(turnFixture.proposal.suggestedActions.every((entry) => !("selected" in entry) && !("executed" in entry)), true);
});

test("query allows suggestions but rejects every state proposal", () => {
  const query = clone(turnFixture);
  query.input = { kind: "query", text: "这里是什么地方？" };
  query.proposal.stateProposals = [];
  assert.equal(validateTurnExchange(query, fixture).valid, true);
  assert.equal(query.proposal.suggestedActions.length > 0, true);

  query.proposal.stateProposals = [{ fieldRef: "state.scene-note", proposedValue: "不应提交" }];
  const report = validateTurnExchange(query, fixture);
  const item = report.diagnostics.find((entry) => entry.code === "TURN_EXCHANGE_QUERY_STATE_PROPOSAL_FORBIDDEN");
  assert.deepEqual(item, {
    phase: "policy",
    severity: "error",
    code: "TURN_EXCHANGE_QUERY_STATE_PROPOSAL_FORBIDDEN",
    path: "/proposal/stateProposals/0",
    relatedPath: "/input/kind",
  });
});

test("suggestions and proposal objects reject selection, execution, HTML, tool, network, and commit fields", () => {
  const cases = [
    ["suggestedActions", "selected"],
    ["suggestedActions", "executed"],
    ["suggestedActions", "autoExecute"],
    ["stateProposals", "commit"],
  ];
  for (const [collection, field] of cases) {
    const turn = clone(turnFixture);
    turn.proposal[collection][0][field] = true;
    assert.equal(validateTurnExchange(turn, fixture).valid, false, field);
  }
  for (const field of ["rawHtml", "toolCall", "network", "installPlugin"]) {
    const turn = clone(turnFixture);
    turn.proposal[field] = { hidden: true };
    assert.equal(validateTurnExchange(turn, fixture).valid, false, field);
  }
});

test("HTML-like content remains opaque text while typed information references are enforced", () => {
  const opaque = clone(turnFixture);
  opaque.proposal.narrative = "<script>alert('opaque')</script><b>叙事文本</b>";
  opaque.proposal.informationModules[0].values[0].value = "<img src=x onerror=alert(1)>";
  assert.equal(validateTurnExchange(opaque, fixture).valid, true);
  assert.equal(opaque.proposal.narrative.startsWith("<script>"), true);

  const missing = clone(turnFixture);
  missing.proposal.informationModules[0].moduleRef = "info.missing";
  assert.equal(codes(validateTurnExchange(missing, fixture)).includes("TURN_EXCHANGE_INFORMATION_MODULE_REF_MISSING"), true);

  const wrongType = clone(turnFixture);
  wrongType.proposal.informationModules[0].values[0].value = false;
  assert.equal(codes(validateTurnExchange(wrongType, fixture)).includes("TURN_EXCHANGE_INFORMATION_VALUE_TYPE"), true);
});

test("state proposals reject missing, protected, duplicate, wrong-type, range, length, and enum values", () => {
  const missing = clone(turnFixture);
  missing.proposal.stateProposals[0].fieldRef = "state.missing";
  assert.equal(codes(validateTurnExchange(missing, fixture)).includes("TURN_EXCHANGE_STATE_FIELD_REF_MISSING"), true);

  const protectedField = clone(turnFixture);
  protectedField.proposal.stateProposals[0] = { fieldRef: "state.player-alert", proposedValue: true };
  assert.equal(codes(validateTurnExchange(protectedField, fixture)).includes("TURN_EXCHANGE_STATE_FIELD_NOT_PROPOSABLE"), true);

  const duplicate = clone(turnFixture);
  duplicate.proposal.stateProposals.push(clone(duplicate.proposal.stateProposals[0]));
  const duplicateItem = validateTurnExchange(duplicate, fixture).diagnostics.find((item) => item.code === "TURN_EXCHANGE_STATE_FIELD_DUPLICATE");
  assert.equal(duplicateItem.path, "/proposal/stateProposals/1/fieldRef");
  assert.equal(duplicateItem.relatedPath, "/proposal/stateProposals/0/fieldRef");

  const wrongType = clone(turnFixture);
  wrongType.proposal.stateProposals[0].proposedValue = 4;
  assert.equal(codes(validateTurnExchange(wrongType, fixture)).includes("TURN_EXCHANGE_STATE_VALUE_TYPE"), true);

  const tooLong = clone(turnFixture);
  tooLong.proposal.stateProposals[0].proposedValue = "x".repeat(513);
  assert.equal(codes(validateTurnExchange(tooLong, fixture)).includes("TURN_EXCHANGE_STATE_VALUE_LENGTH"), true);

  const dynamicCard = clone(fixture);
  dynamicCard.stateFields.push({ id: "state.tension", displayName: "紧张度", modelMayPropose: true, valueType: "integer", initialValue: 0, minimum: 0, maximum: 10 });
  dynamicCard.stateFields.push({ id: "state.tone", displayName: "场景基调", modelMayPropose: true, valueType: "enum", initialValue: "calm", choices: ["calm", "danger"] });
  const range = clone(turnFixture);
  range.proposal.stateProposals = [{ fieldRef: "state.tension", proposedValue: 11 }];
  assert.equal(codes(validateTurnExchange(range, dynamicCard)).includes("TURN_EXCHANGE_STATE_VALUE_RANGE"), true);
  const enumValue = clone(turnFixture);
  enumValue.proposal.stateProposals = [{ fieldRef: "state.tone", proposedValue: "unknown" }];
  assert.equal(codes(validateTurnExchange(enumValue, dynamicCard)).includes("TURN_EXCHANGE_STATE_VALUE_ENUM"), true);
});

test("turn command references, package ownership, diagnostics, and input immutability are stable", () => {
  const badCommand = clone(turnFixture);
  badCommand.input = { kind: "command", commandRef: "command.missing", text: "执行声明命令" };
  assert.equal(codes(validateTurnExchange(badCommand, fixture)).includes("TURN_EXCHANGE_COMMAND_REF_MISSING"), true);

  const mismatch = clone(turnFixture);
  mismatch.cardPackageRef.id = "card.other";
  assert.equal(codes(validateTurnExchange(mismatch, fixture)).includes("TURN_EXCHANGE_CARD_PACKAGE_REF_MISMATCH"), true);

  const frozenTurn = freezeDeep(clone(turnFixture));
  const frozenCard = freezeDeep(clone(fixture));
  const before = JSON.stringify([frozenTurn, frozenCard]);
  const first = validateTurnExchange(frozenTurn, frozenCard);
  const second = validateTurnExchange(frozenTurn, frozenCard);
  assert.deepEqual(first, second);
  assert.notEqual(first.diagnostics, second.diagnostics);
  assert.equal(JSON.stringify([frozenTurn, frozenCard]), before);
});

function cardWithRequiredPlugin(pluginId = "plugin.context-basic", version = "1.0.0", capabilities = ["context.enrich"]) {
  const card = clone(fixture);
  card.requiredPlugins = [{ pluginId, version, capabilities }];
  return card;
}

function cardWithRecommendedPlugin(fallback) {
  const card = clone(fixture);
  card.recommendedPlugins = [{
    pluginId: "plugin.optional-missing",
    version: "1.0.0",
    capabilities: ["memory.augment"],
    fallback,
  }];
  return card;
}

test("zero-plugin cards are ready and matching required manifests satisfy exact contracts", () => {
  assert.deepEqual(evaluatePluginReadiness(fixture, []), { ready: true, diagnostics: [] });
  const unrelated = clone(pluginFixtures[0]);
  unrelated.plugin.id = "plugin.unrelated";
  unrelated.dependencies = [{ pluginId: "plugin.missing", version: "1.0.0", capabilities: [] }];
  assert.deepEqual(evaluatePluginReadiness(fixture, [unrelated, clone(unrelated), { malformed: true }]), {
    ready: true,
    diagnostics: [],
  });
  assert.deepEqual(evaluatePluginReadiness(cardWithRequiredPlugin(), [pluginFixtures[0]]), { ready: true, diagnostics: [] });
});

test("missing, version-mismatched, and capability-deficient required plugins block readiness", () => {
  const missing = evaluatePluginReadiness(cardWithRequiredPlugin(), []);
  assert.equal(missing.ready, false);
  assert.equal(codes(missing).includes("PLUGIN_REQUIRED_MISSING"), true);

  const version = evaluatePluginReadiness(cardWithRequiredPlugin("plugin.context-basic", "9.0.0"), [pluginFixtures[0]]);
  assert.equal(version.ready, false);
  assert.equal(codes(version).includes("PLUGIN_REQUIRED_VERSION_MISMATCH"), true);

  const capability = evaluatePluginReadiness(cardWithRequiredPlugin("plugin.context-basic", "1.0.0", ["memory.augment"]), [pluginFixtures[0]]);
  assert.equal(capability.ready, false);
  assert.equal(codes(capability).includes("PLUGIN_REQUIRED_CAPABILITY_MISSING"), true);
});

test("each recommended-plugin fallback stays ready and is explicit in a warning code", () => {
  for (const [fallback, suffix] of [["core", "CORE"], ["omit", "OMIT"], ["readOnly", "READ_ONLY"]]) {
    const report = evaluatePluginReadiness(cardWithRecommendedPlugin(fallback), []);
    assert.equal(report.ready, true, fallback);
    assert.deepEqual(Object.keys(report), ["ready", "diagnostics"]);
    assert.equal(report.diagnostics.length, 1);
    assert.equal(report.diagnostics[0].severity, "warning");
    assert.equal(report.diagnostics[0].code, `PLUGIN_RECOMMENDED_MISSING_FALLBACK_${suffix}`);
    assert.equal("action" in report.diagnostics[0], false);
  }
});

test("unknown capabilities, permissions, and executable loading fields fail and cannot satisfy requirements", () => {
  for (const mutate of [
    (manifest) => { manifest.capabilities = ["unknown.capability"]; },
    (manifest) => { manifest.permissions = ["system.root"]; },
    (manifest) => { manifest.entrypoint = "./plugin.mjs"; },
    (manifest) => { manifest.autoInstall = true; },
    (manifest) => { manifest.autoEnable = true; },
    (manifest) => { manifest.autoUpgrade = true; },
  ]) {
    const manifest = clone(pluginFixtures[0]);
    mutate(manifest);
    const report = evaluatePluginReadiness(cardWithRequiredPlugin(), [manifest]);
    assert.equal(report.ready, false);
    assert.equal(codes(report).some((code) => code.startsWith("PLUGIN_MANIFEST_SCHEMA_")), true);
  }
});

test("manifest identity, setting, dependency, and network declarations are statically checked", () => {
  const requiredCard = cardWithRequiredPlugin();
  const duplicate = evaluatePluginReadiness(requiredCard, [pluginFixtures[0], clone(pluginFixtures[0])]);
  assert.equal(duplicate.ready, false);
  assert.equal(codes(duplicate).includes("PLUGIN_MANIFEST_ID_DUPLICATE"), true);

  const setting = clone(pluginFixtures[0]);
  setting.settings.push(clone(setting.settings[0]));
  assert.equal(codes(evaluatePluginReadiness(requiredCard, [setting])).includes("PLUGIN_MANIFEST_SETTING_KEY_DUPLICATE"), true);

  const dependency = clone(pluginFixtures[0]);
  dependency.dependencies = [{ pluginId: "plugin.missing", version: "1.0.0", capabilities: [] }];
  assert.equal(codes(evaluatePluginReadiness(requiredCard, [dependency])).includes("PLUGIN_DEPENDENCY_MISSING"), true);

  const network = clone(pluginFixtures[0]);
  network.network = { mode: "modelmirror-mediated", allowedHosts: ["example.invalid"], purposes: ["fixture"] };
  assert.equal(codes(evaluatePluginReadiness(requiredCard, [network])).includes("PLUGIN_MANIFEST_NETWORK_PERMISSION_REQUIRED"), true);

  const hostVersion = clone(pluginFixtures[0]);
  hostVersion.compatibleHostContractVersions = ["9.0.0"];
  assert.equal(codes(evaluatePluginReadiness(requiredCard, [hostVersion])).includes("PLUGIN_MANIFEST_SCHEMA_VERSION_OR_FORMAT"), true);
});

test("recommended plugin failures across the reachable dependency closure use the declared fallback", () => {
  for (const [fallback, suffix] of [["core", "CORE"], ["omit", "OMIT"], ["readOnly", "READ_ONLY"]]) {
    const card = clone(fixture);
    card.recommendedPlugins = [{
      pluginId: "plugin.context-basic",
      version: "1.0.0",
      capabilities: ["context.enrich"],
      fallback,
    }];
    const manifest = clone(pluginFixtures[0]);
    manifest.dependencies = [{ pluginId: "plugin.missing", version: "1.0.0", capabilities: [] }];
    const report = evaluatePluginReadiness(card, [manifest]);
    assert.equal(report.ready, true, fallback);
    assert.equal(codes(report).includes(`PLUGIN_RECOMMENDED_DEPENDENCY_MISSING_FALLBACK_${suffix}`), true, fallback);
    assert.equal(report.diagnostics.every((item) => item.severity === "warning"), true, fallback);
  }

  const invalidCard = clone(fixture);
  invalidCard.recommendedPlugins = [{
    pluginId: "plugin.context-basic",
    version: "1.0.0",
    capabilities: ["context.enrich"],
    fallback: "omit",
  }];
  const invalidManifest = clone(pluginFixtures[0]);
  invalidManifest.permissions = ["system.root"];
  const invalidReport = evaluatePluginReadiness(invalidCard, [invalidManifest]);
  assert.equal(invalidReport.ready, true);
  assert.equal(codes(invalidReport).some((code) => code.startsWith("PLUGIN_RECOMMENDED_MANIFEST_SCHEMA_") && code.endsWith("_FALLBACK_OMIT")), true);

  const first = clone(pluginFixtures[0]);
  const second = clone(pluginFixtures[1]);
  first.dependencies = [{ pluginId: second.plugin.id, version: second.plugin.version, capabilities: [] }];
  second.dependencies = [{ pluginId: first.plugin.id, version: first.plugin.version, capabilities: [] }];
  assert.equal(codes(evaluatePluginReadiness(cardWithRequiredPlugin(), [first, second])).includes("PLUGIN_DEPENDENCY_CYCLE"), true);
  const cycleReport = evaluatePluginReadiness(invalidCard, [first, second]);
  assert.equal(cycleReport.ready, true);
  assert.equal(codes(cycleReport).includes("PLUGIN_RECOMMENDED_DEPENDENCY_CYCLE_FALLBACK_OMIT"), true);
});

test("card plugin requirements reject duplicate ids and missing fallback before readiness", () => {
  const duplicate = cardWithRequiredPlugin();
  duplicate.recommendedPlugins = [{
    pluginId: "plugin.context-basic",
    version: "1.0.0",
    capabilities: [],
    fallback: "omit",
  }];
  assert.equal(codes(validateCardPackage(duplicate)).includes("CARD_PACKAGE_PLUGIN_REQUIREMENT_DUPLICATE"), true);

  const noFallback = clone(fixture);
  noFallback.recommendedPlugins = [{ pluginId: "plugin.optional", version: "1.0.0", capabilities: [] }];
  assert.equal(validateCardPackage(noFallback).valid, false);
});

test("plugin readiness is deterministic, isolated, mutation-free, and never returns install actions", () => {
  const frozenCard = freezeDeep(cardWithRequiredPlugin());
  const frozenManifests = freezeDeep([clone(pluginFixtures[0])]);
  const before = JSON.stringify([frozenCard, frozenManifests]);
  const first = evaluatePluginReadiness(frozenCard, frozenManifests);
  const second = evaluatePluginReadiness(frozenCard, frozenManifests);
  assert.deepEqual(first, second);
  assert.notEqual(first.diagnostics, second.diagnostics);
  assert.equal(JSON.stringify([frozenCard, frozenManifests]), before);
  assert.deepEqual(Object.keys(first), ["ready", "diagnostics"]);
});

test("zero-plugin representative card is valid and keeps legacy negative price inert", () => {
  assert.deepEqual(validateCardPackage(fixture), { valid: true, diagnostics: [] });
  assert.equal(fixture.requiredPlugins.length, 0);
  assert.equal(fixture.recommendedPlugins.length, 0);
  assert.equal(fixture.extensions["example.source-preservation"].legacyNegativePrice, -1);
});

test("display names may repeat while stable resource IDs may not", () => {
  const sameName = clone(fixture);
  sameName.resources.items[1].displayName = sameName.resources.items[0].displayName;
  assert.equal(validateCardPackage(sameName).valid, true);

  const duplicateId = clone(fixture);
  duplicateId.resources.items[1].id = duplicateId.resources.worlds[0].id;
  const report = validateCardPackage(duplicateId);
  assert.equal(report.valid, false);
  assert.equal(codes(report).includes("CARD_PACKAGE_STABLE_ID_DUPLICATE"), true);
  const duplicate = report.diagnostics.find((item) => item.code === "CARD_PACKAGE_STABLE_ID_DUPLICATE");
  assert.equal(duplicate.path, "/resources/items/1/id");
  assert.equal(duplicate.relatedPath, "/resources/worlds/0/id");
});

test("package-owned stable IDs are globally unique across provenance, resources, fields, and state", () => {
  for (const mutate of [
    (card) => { card.provenance.sources[0].id = card.provenance.rights[0].id; },
    (card) => { card.resources.informationModules[0].fields[0].id = card.package.id; },
    (card) => { card.stateFields[0].id = card.resources.worlds[0].id; },
  ]) {
    const card = clone(fixture);
    mutate(card);
    const report = validateCardPackage(card);
    assert.equal(report.valid, false);
    assert.equal(codes(report).includes("CARD_PACKAGE_STABLE_ID_DUPLICATE"), true);
  }
});

test("default opening must belong to the default world and scalar ref paths are exact", () => {
  const missing = clone(fixture);
  missing.defaults.worldRef = "world.missing";
  const missingItem = validateCardPackage(missing).diagnostics.find((item) => item.code === "CARD_PACKAGE_WORLD_REF_MISSING");
  assert.equal(missingItem.path, "/defaults/worldRef");

  const mismatch = clone(fixture);
  mismatch.resources.worlds.push({
    id: "world.other",
    displayName: "其他世界",
    description: "用于验证默认开场与世界的绑定。",
    sourceRefs: ["source.synthetic-player-card"],
  });
  mismatch.defaults.worldRef = "world.other";
  assert.equal(codes(validateCardPackage(mismatch)).includes("CARD_PACKAGE_DEFAULT_OPENING_WORLD_MISMATCH"), true);
});

test("diagnostics are sorted deterministically and safe additional-property paths are precise", () => {
  const card = clone(fixture);
  card.zUnknown = true;
  card.aUnknown = true;
  const first = validateCardPackage(card);
  const second = validateCardPackage(card);
  assert.deepEqual(first, second);
  assert.deepEqual(first.diagnostics.map((item) => item.path), ["/aUnknown", "/zUnknown"]);
});

test("dangling typed resource references fail with stable codes", () => {
  const cases = [
    ["CARD_PACKAGE_WORLD_REF_MISSING", (card) => { card.defaults.worldRef = "world.missing"; }],
    ["CARD_PACKAGE_IDENTITY_REF_MISSING", (card) => { card.resources.openings[0].identityRefs = ["identity.missing"]; }],
    ["CARD_PACKAGE_TALENT_REF_MISSING", (card) => { card.resources.openings[0].talentRefs = ["talent.missing"]; }],
    ["CARD_PACKAGE_WORLDBOOK_REF_MISSING", (card) => { card.resources.openings[0].worldbookRefs = ["worldbook.missing"]; }],
    ["CARD_PACKAGE_INFORMATION_MODULE_REF_MISSING", (card) => { card.resources.openings[0].informationModuleRefs = ["info.missing"]; }],
  ];
  for (const [expected, mutate] of cases) {
    const card = clone(fixture);
    mutate(card);
    const report = validateCardPackage(card);
    assert.equal(report.valid, false, expected);
    assert.equal(codes(report).includes(expected), true, expected);
  }
});

test("wrong format version and executable-shaped fields are rejected", () => {
  const wrongVersion = clone(fixture);
  wrongVersion.formatVersion = "0.2.0";
  assert.equal(codes(validateCardPackage(wrongVersion)).includes("CARD_PACKAGE_SCHEMA_VERSION_OR_FORMAT"), true);

  for (const field of ["script", "rawHtml", "toolCall", "network", "autoInstall", "autoEnable", "autoUpgrade"]) {
    const card = clone(fixture);
    card[field] = { payload: "must remain private" };
    const report = validateCardPackage(card);
    assert.equal(report.valid, false, field);
    assert.equal(codes(report).includes("CARD_PACKAGE_SCHEMA_UNKNOWN_PROPERTY"), true, field);
  }
  const nested = clone(fixture);
  nested.extensions["example.source-preservation"].script = "doSomething()";
  assert.equal(codes(validateCardPackage(nested)).includes("CARD_PACKAGE_EXECUTABLE_FIELD_FORBIDDEN"), true);

  for (const field of ["networkRequest", "toolCallV2", "scriptSource", "rawHtmlTemplate", "autoInstallPlugin"]) {
    const compound = clone(fixture);
    compound.extensions["example.source-preservation"][field] = { payload: "forbidden" };
    assert.equal(codes(validateCardPackage(compound)).includes("CARD_PACKAGE_EXECUTABLE_FIELD_FORBIDDEN"), true, field);
  }
  const descriptive = clone(fixture);
  descriptive.extensions["example.source-preservation"].description = "ordinary inert metadata";
  assert.equal(validateCardPackage(descriptive).valid, true);
});

test("extension preflight bounds deep or cyclic non-JSON input before structural validation", () => {
  const deep = clone(fixture);
  let cursor = {};
  deep.extensions["example.deep"] = cursor;
  for (let index = 0; index < 5000; index += 1) {
    cursor.safe = {};
    cursor = cursor.safe;
  }
  const deepReport = validateCardPackage(deep);
  assert.equal(deepReport.valid, false);
  assert.equal(codes(deepReport).includes("CARD_PACKAGE_EXTENSION_LIMIT"), true);

  const cyclic = clone(fixture);
  cyclic.extensions["example.cycle"] = {};
  cyclic.extensions["example.cycle"].self = cyclic.extensions["example.cycle"];
  const cyclicReport = validateCardPackage(cyclic);
  assert.equal(cyclicReport.valid, false);
  assert.equal(codes(cyclicReport).includes("CARD_PACKAGE_EXTENSION_NON_JSON"), true);

  const exotic = clone(fixture);
  exotic.extensions = new Date(0);
  const exoticReport = validateCardPackage(exotic);
  assert.equal(exoticReport.valid, false);
  assert.equal(codes(exoticReport).includes("CARD_PACKAGE_EXTENSION_NON_JSON"), true);
});

test("validation is deterministic, does not mutate frozen input, and never echoes content or paths", () => {
  const frozen = freezeDeep(clone(fixture));
  const before = JSON.stringify(frozen);
  const first = validateCardPackage(frozen);
  const second = validateCardPackage(frozen);
  assert.deepEqual(first, second);
  assert.notEqual(first.diagnostics, second.diagnostics);
  assert.equal(JSON.stringify(frozen), before);

  const malicious = clone(fixture);
  malicious["C:\\private\\secret.txt"] = "sk-" + "z".repeat(32);
  const serialized = JSON.stringify(validateCardPackage(malicious));
  assert.equal(serialized.includes("C:\\private"), false);
  assert.equal(serialized.includes("sk-" + "z".repeat(32)), false);
  const diagnosticKeys = Object.keys(validateCardPackage(malicious).diagnostics[0]).sort();
  assert.deepEqual(diagnosticKeys, ["code", "path", "phase", "severity"]);
});
