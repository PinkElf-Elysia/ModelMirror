import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { COMPILE_INPUT_SCHEMA, SOURCE_SELECTION_SCHEMA, compileContent } from "../content/index.mjs";
import { validateCardPackage } from "../src/index.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const golden = JSON.parse(fs.readFileSync(path.join(root, "fixtures", "rpg02", "compile-input.json"), "utf8"));
const clone = () => structuredClone(golden);

test("compiles the real selected records into a valid zero-plugin card package", () => {
  const before = clone(), result = compileContent(golden);
  assert.equal(result.valid, true); assert.deepEqual(golden, before);
  const card = result.value.cardPackage;
  assert.equal(validateCardPackage(card).valid, true);
  assert.deepEqual([card.resources.worlds.length, card.resources.identities.length, card.resources.talents.length, card.resources.items.length, card.resources.openings.length], [2, 4, 8, 4, 2]);
  assert.deepEqual(card.requiredPlugins, []); assert.deepEqual(card.recommendedPlugins, []);
  assert.equal(result.value.contentIndex.entries.length, result.value.conversionReceipt.resourceCount);
  assert.equal(result.value.conversionReceipt.hashVerification, "tooling_required");
});

test("stable IDs are explicit and source metadata drift blocks compilation", () => {
  const result = compileContent(golden), ids = result.value.contentIndex.entries.map((entry) => entry.id);
  assert.equal(ids.includes("world.gu"), true); assert.equal(ids.includes("talent.common.root"), true);
  const drift = clone(); drift.stableIdMap[0].expectedDataSha256 = "0".repeat(64);
  assert.equal(compileContent(drift).diagnostics.some((entry) => entry.code === "SOURCE_MAPPING_DRIFT"), true);
  const locator = clone(); locator.records[0].locator = "/worldDB/999";
  assert.equal(compileContent(locator).diagnostics.some((entry) => entry.code === "SOURCE_MAPPING_DRIFT"), true);
});

test("aliases resolve by kind and world scope and fail on missing, ambiguity, or wrong scope", () => {
  const valid = clone(); valid.authored.openings[0].identityRefs[0] = "中洲门派的外门弟子"; valid.authored.openings[0].talentRefs[0] = "至尊仙胎蛊";
  assert.equal(compileContent(valid).valid, true);
  const missing = clone(); missing.authored.openings[0].talentRefs[0] = "missing.alias";
  assert.equal(compileContent(missing).diagnostics.some((entry) => entry.code === "ALIAS_MISSING"), true);
  const wrong = clone(); wrong.authored.openings[1].talentRefs[0] = "至尊仙胎蛊";
  assert.equal(compileContent(wrong).diagnostics.some((entry) => entry.code === "ALIAS_SCOPE_MISMATCH"), true);
  const ambiguous = clone(); ambiguous.stableIdMap.find((entry) => entry.id === "talent.gu.perseverance").aliases.push("至尊仙胎蛊"); ambiguous.records.find((entry) => entry.stableId === "talent.gu.perseverance").aliases.push("至尊仙胎蛊"); ambiguous.authored.openings[0].talentRefs[0] = "至尊仙胎蛊";
  assert.equal(compileContent(ambiguous).diagnostics.some((entry) => entry.code === "ALIAS_AMBIGUOUS"), true);
});

test("duplicate display names remain expressible through distinct stable IDs", () => {
  const input = clone(), second = input.records.find((entry) => entry.stableId === "identity.gu.mortal-servant");
  second.data.name = "中洲门派的外门弟子";
  const result = compileContent(input);
  assert.equal(result.valid, true);
  assert.equal(result.value.cardPackage.resources.identities.filter((entry) => entry.displayName === "中洲门派的外门弟子").length, 2);
  const worlds = clone(), minecraft = worlds.records.find((entry) => entry.stableId === "world.minecraft"), guName = worlds.records.find((entry) => entry.stableId === "world.gu").data.name;
  minecraft.data.name = guName; minecraft.worldName = guName;
  for (const record of worlds.records.filter((entry) => entry.worldName === "417.我的世界 (Minecraft)")) record.worldName = guName;
  const worldResult = compileContent(worlds);
  assert.equal(worldResult.valid, true); assert.equal(worldResult.value.cardPackage.resources.worlds.filter((entry) => entry.displayName === guName).length, 2);
  for (const talent of worldResult.value.cardPackage.resources.talents.filter((entry) => entry.id.startsWith("talent.minecraft."))) assert.deepEqual(talent.worldRefs, ["world.minecraft"]);
});

test("source talent labels and synthetic negative cost stay namespaced without economics or permissions", () => {
  const input = clone(), record = input.records.find((entry) => entry.stableId === "talent.minecraft.auto-fishing"); record.data.cost = -7;
  const result = compileContent(input), card = result.value.cardPackage, extension = card.extensions["modelmirror.source"][record.stableId];
  assert.equal(extension.cost, -7); assert.equal(extension.color, "C"); assert.equal(extension.sourceType, "exclusive");
  assert.equal(JSON.stringify(card).includes("runtimePermissions"), false);
  assert.equal(card.resources.talents.find((entry) => entry.id === record.stableId).tierLabel, "C");
});

test("configured ranks and identity kits preserve provenance without granting both kits", () => {
  const card = compileContent(golden).value.cardPackage;
  assert.equal(card.resources.identities.every((entry) => entry.rankLabel === "E"), true);
  assert.equal(card.extensions["modelmirror.rank-provenance"], "configured");
  assert.equal(card.resources.openings.every((entry) => entry.itemRefs.length === 0), true);
  assert.equal(card.resources.items.length, 4);
  const duplicateRank = clone(); duplicateRank.configuredRanks[1].identityRef = duplicateRank.configuredRanks[0].identityRef;
  assert.equal(compileContent(duplicateRank).diagnostics.some((entry) => entry.code === "CONFIGURED_RANK_DUPLICATE"), true);
  const wrongRank = clone(); wrongRank.configuredRanks[0].identityRef = "world.gu";
  assert.equal(compileContent(wrongRank).diagnostics.some((entry) => entry.code === "CONFIGURED_RANK_KIND_MISMATCH"), true);
  const itemDrift = clone(); itemDrift.items[0].description = "改写后仍冒充来源";
  assert.equal(compileContent(itemDrift).diagnostics.some((entry) => entry.code === "ITEM_SOURCE_BINDING_DRIFT"), true);
  const itemKind = clone(); itemKind.items[0].identityRef = "world.gu";
  assert.equal(compileContent(itemKind).diagnostics.some((entry) => entry.code === "ITEM_IDENTITY_KIND_MISMATCH"), true);
});

test("schemas are deeply frozen and outputs do not retain authored input arrays", () => {
  assert.equal(Object.isFrozen(COMPILE_INPUT_SCHEMA.properties.authored.properties.openings.items.properties.identityRefs), true);
  assert.equal(Object.isFrozen(SOURCE_SELECTION_SCHEMA.properties.worlds.items.properties.identityNames), true);
  const input = clone(), result = compileContent(input), output = result.value.cardPackage;
  output.resources.openings[0].backgroundRefs.push("background.synthetic");
  output.resources.worldbookEntries[0].tags.push("synthetic");
  assert.equal(input.authored.openings[0].backgroundRefs.includes("background.synthetic"), false);
  assert.equal(input.authored.worldbookEntries[0].tags.includes("synthetic"), false);
});

test("carrier and authored hashes keep distinct provenance conventions", () => {
  const receipt = compileContent(golden).value.conversionReceipt;
  const carrier = receipt.sourceEvidence.find((entry) => entry.sourceRef === "source.real-card"), authored = receipt.sourceEvidence.find((entry) => entry.sourceRef === "source.authored-rpg02");
  assert.equal(golden.sources[0].kind, "derived"); assert.equal(carrier.hashConvention, "exact-file-bytes");
  assert.equal(authored.hashConvention, "JSON.stringify(input.authored) UTF-8 no trailing LF");
  assert.equal(receipt.recordDataHashConvention, "JSON.stringify(record.data) UTF-8 no trailing LF");
  const missing = clone(); missing.sources.find((entry) => entry.id === "source.authored-rpg02").id = "source.authored-missing";
  assert.equal(compileContent(missing).diagnostics.some((entry) => entry.code === "AUTHORED_SOURCE_MISSING"), true);
});

test("unknown execution fields and pending player input fail closed with stable non-echoing diagnostics", () => {
  const unsafe = clone(); unsafe.execute = "SECRET_RAW_TEXT";
  const first = compileContent(unsafe), second = compileContent(unsafe);
  assert.equal(first.valid, false); assert.equal("value" in first, false); assert.deepEqual(first, second); assert.equal(JSON.stringify(first).includes("SECRET_RAW_TEXT"), false);
  const player = clone(); player.player = { text: "无章节结构的玩家原文", setupId: "setup.one", openingRef: "opening.gu", activations: [], backgroundRefs: [] };
  const pendingFirst = compileContent(player), pendingSecond = compileContent(player);
  assert.equal(pendingFirst.valid, false); assert.equal("value" in pendingFirst, false); assert.deepEqual(pendingFirst, pendingSecond);
});
