import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import { createHash } from "node:crypto";
import { validateCardPackage, validatePlayerSetup } from "../src/index.mjs";
import { validateContextProfile } from "../context/index.mjs";
import { selectContextLore } from "../context/selection.mjs";
import { canonicalJson } from "../runtime/index.mjs";
import { loadRpg04ContextCardFixture } from "../tooling/context-card.mjs";
import { buildRpg04ApprovedContextCard, loadRpg04ApprovedContextCardFixture, validateRpg04ApprovedContent, RPG04_APPROVED_CONTENT_SHA256 } from "../tooling/context-approved-card.mjs";

const approvedText = fs.readFileSync(new URL("../fixtures/rpg04/approved-content.json", import.meta.url), "utf8");
const sha = (text) => createHash("sha256").update(text, "utf8").digest("hex");
const old = () => loadRpg04ContextCardFixture();

test("builds a separately versioned approved card, player binding and valid context profile", () => {
  const report = loadRpg04ApprovedContextCardFixture();
  assert.equal(report.valid, true, JSON.stringify(report.diagnostics));
  const { cardPackage, playerSetup, contextProfile } = report.value;
  assert.deepEqual(playerSetup.cardPackageRef, { id: "card.rpg04-approved", version: "0.2.0" });
  assert.equal(validateCardPackage(cardPackage).valid, true);
  assert.equal(validatePlayerSetup(playerSetup, cardPackage).valid, true);
  assert.equal(validateContextProfile(contextProfile, cardPackage).valid, true);
  assert.equal(playerSetup.talents.length, 5);
  assert.equal(playerSetup.talents.every(({ owned, active }) => owned && active), true);
  assert.deepEqual(playerSetup.runtimePermissions, []);
});

test("preserves the old fixture and all prior resources without mutation", () => {
  const baseline = old(), before = structuredClone(baseline);
  const report = buildRpg04ApprovedContextCard(baseline, approvedText);
  assert.equal(report.valid, true, JSON.stringify(report.diagnostics));
  assert.deepEqual(baseline, before);
  for (const [key, resources] of Object.entries(before.value.cardPackage.resources)) {
    assert.deepEqual(report.value.cardPackage.resources[key].slice(0, resources.length), resources);
  }
  assert.equal(canonicalJson(old()).value, canonicalJson(before).value);
});

test("applies exact S02 and L02 text with authored provenance while retaining the Minecraft S01-form sample", () => {
  const { value } = loadRpg04ApprovedContextCardFixture();
  const fixture = JSON.parse(approvedText);
  const style = value.cardPackage.resources.styles.find(({ id }) => id === "style.rpg04.gu.approved");
  const lore = value.cardPackage.resources.worldbookEntries.find(({ id }) => id === "worldbook.rpg04.gu.chores-court-luhe");
  assert.equal(style.instruction, fixture.resources.styles[0].instruction);
  assert.equal(lore.content, fixture.resources.worldbookEntries[0].content);
  assert.equal(value.cardPackage.resources.styles.some(({ id }) => id === "style.rpg04.minecraft"), true);
  assert.deepEqual(value.provenanceReceipt.sourceKinds, { "source.rpg04-approved-authored": "authored", "source.rpg04-approved-derived": "derived" });
  assert.equal(value.contextProfile.aliases.filter(({ resourceRef }) => resourceRef === lore.id).length, 2);
  assert.deepEqual(value.contextProfile.loreRules[0].trigger.anyKeywords, ["杂务院", "陆禾"]);
});

test("maps full approved panel needs to stable text/list fields and persistent memory state declarations", () => {
  const { cardPackage } = loadRpg04ApprovedContextCardFixture().value;
  const ids = cardPackage.resources.informationModules.map(({ id }) => id);
  for (const id of ["info.rpg04.player", "info.rpg04.npcs", "info.rpg04.assets", "info.rpg04.quests", "info.rpg04.shop", "info.rpg04.world", "info.rpg04.memory"]) assert.equal(ids.includes(id), true, id);
  for (const module of cardPackage.resources.informationModules.filter(({ id }) => id.startsWith("info.rpg04."))) {
    assert.equal(module.fields.every(({ valueType }) => ["text", "list"].includes(valueType)), true, module.id);
  }
  assert.deepEqual(cardPackage.stateFields.slice(-3).map(({ id, valueType, maxLength, modelMayPropose }) => ({ id, valueType, maxLength, modelMayPropose })), [
    { id: "state.rpg04.memory.stm", valueType: "shortText", maxLength: 4096, modelMayPropose: true },
    { id: "state.rpg04.memory.ltm", valueType: "shortText", maxLength: 4096, modelMayPropose: true },
    { id: "state.rpg04.memory.saves", valueType: "shortText", maxLength: 4096, modelMayPropose: true },
  ]);
  assert.match(loadRpg04ApprovedContextCardFixture().value.provenanceReceipt.schemaDifference, /拒绝且不截断/u);
});

test("retains P14 gameplay and approved P10 P16 P20 P22 card guidance for both worlds", () => {
  const { cardPackage } = loadRpg04ApprovedContextCardFixture().value;
  const gameplay = cardPackage.resources.worldbookEntries.find(({ id }) => id === "worldbook.rpg04.shared.original-gameplay");
  const guidance = cardPackage.resources.worldbookEntries.find(({ id }) => id === "worldbook.rpg04.shared.approved-card-guidance");
  for (const text of ["等级划分：E -> D -> C -> B -> A -> S -> SS -> SSS", "彩色 (UR)", "[存档]：玩家可在非战斗状态随时存档。", "[天赋商店]"]) assert.equal(gameplay.content.includes(text), true, text);
  for (const text of ["A. {激进/冒险的选项}", "E. {色情/堕落/欲望的选项}", "查询只展示已有商店信息，不触发刷新", "不输出着色用的 HTML 标签或 CSS class", "800–1200字为默认参考"]) assert.equal(guidance.content.includes(text), true, text);
  for (const opening of cardPackage.resources.openings.filter(({ id }) => id.startsWith("opening.rpg04-approved."))) {
    assert.equal(opening.worldbookRefs.includes(gameplay.id), true);
    assert.equal(opening.worldbookRefs.includes(guidance.id), true);
  }
});

test("selects all required shared and scene-authored lore in both declared scenes", () => {
  const { cardPackage, playerSetup, contextProfile } = loadRpg04ApprovedContextCardFixture().value;
  for (const [sceneRef, openingRef, worldRef, identityRef, expected] of [
    ["scene.rpg04.gu", "opening.rpg04-approved.gu", "world.gu", "identity.gu.outer-disciple", ["worldbook.rpg04.gu.courtyard", "worldbook.rpg04.gu.steward", "worldbook.rpg04.gu.basics"]],
    ["scene.rpg04.minecraft", "opening.rpg04-approved.minecraft", "world.minecraft", "identity.minecraft.first-night", ["worldbook.rpg04.minecraft.shelter", "worldbook.rpg04.minecraft.villager", "worldbook.rpg04.minecraft.basics"]],
  ]) {
    const setup = structuredClone(playerSetup);
    setup.opening.openingRef = openingRef;
    setup.world = { source: "package", resourceRef: worldRef };
    setup.currentIdentity = { source: "package", resourceRef: identityRef };
    const input = {
      format: "modelmirror.ai-rpg.context-input", formatVersion: "0.1.0", cardPackage, playerSetup: setup,
      session: { format: "modelmirror.ai-rpg.runtime-session", formatVersion: "0.1.0", sessionId: "session.selector", resources: { cardPackage: { id: cardPackage.package.id, version: cardPackage.package.version, sha256: "a".repeat(64) }, playerSetup: { setupId: setup.setupId, sha256: "b".repeat(64) } }, revision: 0, state: cardPackage.stateFields.map((field) => ({ fieldRef: field.id, value: field.initialValue })), turns: [], generations: [], pending: null, pluginAuthorizations: [] },
      profile: contextProfile, sceneRef, resourceRefs: [], generationId: "generation.selector", exchangeId: "exchange.selector", expectedRevision: 0,
      input: { kind: "query", text: "查看当前资料" }, modelId: "offline/selector", settings: { temperature: 0, maxTokens: 512 },
    };
    const selected = selectContextLore(input);
    assert.equal(selected.valid, true, JSON.stringify(selected.diagnostics));
    const refs = selected.value.selectedEntries.map(({ id }) => id);
    for (const id of ["worldbook.rpg04.shared.original-gameplay", "worldbook.rpg04.shared.approved-card-guidance", "worldbook.rpg04.shared.memory-and-shop-verbatim", ...expected]) assert.equal(refs.includes(id), true, `${sceneRef}:${id}`);
  }
});

test("binds exact fixture bytes and emits reproducible card/profile approval receipts", () => {
  const first = loadRpg04ApprovedContextCardFixture(), second = loadRpg04ApprovedContextCardFixture();
  assert.equal(RPG04_APPROVED_CONTENT_SHA256, sha(approvedText));
  assert.deepEqual(first, second);
  assert.equal(first.value.provenanceReceipt.fixture.sha256, RPG04_APPROVED_CONTENT_SHA256);
  assert.equal(first.value.provenanceReceipt.cardPackageSha256, sha(canonicalJson(first.value.cardPackage).value));
  assert.equal(first.value.provenanceReceipt.contextProfileSha256, sha(canonicalJson(first.value.contextProfile).value));
  assert.deepEqual(first.value.provenanceReceipt.approvalMapping.appliedItemIds, ["S01", "S02", "L01", "L02", "P08", "P09", "P10", "P14", "P16", "P18", "P20", "P22"]);
});

test("rejects byte drift and invalid mapped resources without exposing source text", () => {
  const drift = buildRpg04ApprovedContextCard(old(), approvedText + " ");
  assert.equal(drift.valid, false);
  assert.equal(drift.diagnostics[0].code, "RPG04_APPROVED_CONTENT_HASH_MISMATCH");
  const bad = JSON.parse(approvedText);
  bad.resources.informationModules[0].fields[0].valueType = "record";
  const report = validateRpg04ApprovedContent(old(), JSON.stringify(bad));
  assert.equal(report.valid, false);
  assert.equal(report.diagnostics[0].code, "RPG04_APPROVED_CONTENT_ENVELOPE_INVALID");
  assert.equal(JSON.stringify(report).includes("record"), false);
  assert.equal("value" in report, false);
  const open = JSON.parse(approvedText); open.unknown = true;
  const openReport = validateRpg04ApprovedContent(old(), JSON.stringify(open));
  assert.equal(openReport.diagnostics[0].code, "RPG04_APPROVED_CONTENT_ENVELOPE_INVALID");
  assert.equal("value" in openReport, false);
  const forged = old(); forged.value.cardPackage.package.displayName += " forged";
  assert.equal(buildRpg04ApprovedContextCard(forged, approvedText).diagnostics[0].code, "RPG04_APPROVED_BASE_CARD_HASH_MISMATCH");
});

test("retains exact STM LTM compression and player-only save semantics as card content", () => {
  const { cardPackage } = loadRpg04ApprovedContextCardFixture().value;
  const memory = cardPackage.resources.worldbookEntries.find(({ id }) => id === "worldbook.rpg04.shared.memory-and-shop-verbatim").content;
  for (const text of ["每次回复生成一条短期记忆，若满7条则进行总结", "每轮仅追加一行新S，旧S逐字保留", "生成S7后，下一轮将S1~S7压缩为一条新L并清空STM重新从S1计数", "禁止你自动生成或修改，只允许玩家手动指令"]) assert.equal(memory.includes(text), true, text);
});

test("retains the three separately approved residual passages verbatim in card content", () => {
  const card = loadRpg04ApprovedContextCardFixture().value.cardPackage;
  const content = card.resources.worldbookEntries.find(x => x.id === "worldbook.rpg04.shared.approved-card-guidance").content;
  for (const text of ["【天赋词条】（绝对禁止修改）","- [出场NPC姓名]-[在其第一视角观测到的内容]-[NPC的人设] -&gt [该NPC的反应（严格遵守防神化、防绝望、防机械化限定及上述有限设定例外）]-[生成交互对象1状态栏]（100字以内）","【绝对禁令】：绝对禁止将玩家的行为恶意揣测为别有用心，别有意图的目的，绝对禁止发散玩家的想法和行为，绝对禁止将玩家阴谋化，阴冷化，反派化，占有欲爆棚，冷漠强大，玩弄人心，玩家并没有那些意图，也必须杜绝一切NPC对玩家的莫名臣服，渴望和绝望神化，必须绝对禁止此类现象出现"]) assert.equal(content.includes(text), true);
});
