import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { compileContent, parsePlayerText } from "../content/index.mjs";
import { validatePlayerSetup } from "../src/index.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const text = fs.readFileSync(path.join(root, "fixtures", "rpg02", "player-text.txt"), "utf8");
const compileInput = JSON.parse(fs.readFileSync(path.join(root, "fixtures", "rpg02", "compile-input.json"), "utf8"));
const talentIds = ["talent.gu.sovereign-body", "talent.gu.perseverance", "talent.gu.spring-autumn-cicada", "talent.gu.venerable-aptitude", "talent.common.root"];
function configured(active = true) { const value = structuredClone(compileInput); value.player = { text, setupId: "setup.bai-yu-ling-yin", openingRef: "opening.gu", activations: talentIds.map((talentRef) => ({ talentRef, active })), backgroundRefs: ["background.gu.arrival"] }; return value; }

test("parses the exact user section format and preserves every field and five scoped talents", () => {
  const result = parsePlayerText(text); assert.equal(result.valid, true); assert.equal(result.value.rawText, text);
  assert.deepEqual(result.value.character, { name: "白羽绫音", gender: "女", age: 18, appearance: "审美中近乎完美的化身，非人的绝世容颜，银发晶莹，宛若流苏，垂至腰际。欺霜赛雪，一双淡蓝的竖眸龙瞳，容貌完美无瑕，五官极其精致，皮肤白皙晶莹，身材比例完美，气质清冷而尊贵，美得不似人间之物，令一切形容美的辞藻黯然失色，十分特别的是，额头生长着一对红珊瑚般的小巧龙角", personality: "温柔善良、单纯活泼、精灵古怪", xpText: "百合、SM", otherText: "龙人族公主" });
  assert.equal(result.value.talents.length, 5); assert.equal(result.value.talents[2].name, "春秋蝉(重生)"); assert.equal(result.value.talents[4].tierLabel, "UR"); assert.equal(result.value.talents.every((entry) => entry.owned && !("active" in entry)), true);
});

test("parser accepts a different bounded talent count without imposing a two-item quota", () => {
  const three = text.split("\n").filter((line) => !line.includes("至尊仙胎蛊") && !line.includes("坚持 (SSS)")).join("\n");
  const result = parsePlayerText(three); assert.equal(result.valid, true); assert.equal(result.value.talents.length, 3);
});

test("parser rejects missing duplicate unknown or trailing sections, invalid age and malformed talent lines", () => {
  const variants = [text.replace("姓名：白羽绫音\n", ""), text.replace("性别：女", "姓名：重复\n性别：女"), text.replace("XP：百合、SM", "未知：值"), text + "尾文\n", text.replace("年龄：18", "年龄：18.5"), text.replace(" (UR): ", " (UR)：")];
  for (const value of variants) { const result = parsePlayerText(value); assert.equal(result.valid, false); assert.equal("value" in result, false); assert.deepEqual(result, parsePlayerText(value)); assert.equal(JSON.stringify(result).includes("白羽绫音"), false); }
});

test("parser rejects invalid UTF and byte limits and treats HTML-like appearance as inert text", () => {
  assert.equal(parsePlayerText("\ud800").diagnostics.some((entry) => entry.code === "PLAYER_TEXT_INVALID_UTF16"), true);
  assert.equal(parsePlayerText("界".repeat(400000)).diagnostics.some((entry) => entry.code === "PLAYER_TEXT_LIMIT"), true);
  const html = text.replace("姓名：白羽绫音", '姓名：<img src=x onerror="globalThis.bad=true">'); delete globalThis.bad;
  assert.equal(parsePlayerText(html).value.character.name.startsWith("<img"), true); assert.equal(globalThis.bad, undefined);
});

test("compiles five explicitly active owned talents into a valid isolated player setup", () => {
  const input = configured(true), before = structuredClone(input), result = compileContent(input); assert.equal(result.valid, true); assert.deepEqual(input, before);
  const setup = result.value.playerSetup; assert.equal(validatePlayerSetup(setup, result.value.cardPackage).valid, true);
  assert.equal(setup.talents.length, 5); assert.equal(setup.talents.every((entry) => entry.owned && entry.active), true); assert.deepEqual(setup.runtimePermissions, []); assert.deepEqual(setup.characterPower, { status: "unspecified" });
  assert.equal(setup.inherentBackgrounds[0].resource.id, "background.player-inherent"); assert.equal(setup.inherentBackgrounds[0].resource.description, "龙人族公主"); assert.equal(setup.character.notes.includes("XP：百合、SM"), true);
  assert.deepEqual(setup.possessions, [{ resource: { source: "package", resourceRef: "item.gu.outer-disciple-kit" }, quantity: 1 }]);
});

test("all five talents may be explicitly inactive while remaining owned", () => {
  const setup = compileContent(configured(false)).value.playerSetup;
  assert.equal(setup.talents.every((entry) => entry.owned && !entry.active), true);
});

test("activation omissions duplicates extras and unknowns all block without value", () => {
  const inputs = [configured(), configured(), configured(), configured()];
  inputs[0].player.activations.pop();
  inputs[1].player.activations[1].talentRef = inputs[1].player.activations[0].talentRef;
  inputs[2].player.activations.push({ talentRef: "talent.minecraft.shaders", active: true });
  inputs[3].player.activations[0].talentRef = "talent.unknown";
  for (const input of inputs) { const result = compileContent(input); assert.equal(result.valid, false); assert.equal("value" in result, false); }
});

test("world identity talent rank items and opening conflicts fail closed", () => {
  const replacements = [["简介：养蛊", "简介：漂移养蛊"], ["等级：E", "等级：S"], ["物资：一块门派令牌", "物资：另一件物资"], ["至尊仙胎蛊 (SSS)", "至尊仙胎蛊 (UR)"], ["名称：470.蛊真人 (Reverend Insanity)", "名称：417.我的世界 (Minecraft)"]];
  for (const [from, to] of replacements) { const input = configured(); input.player.text = input.player.text.replace(from, to); const result = compileContent(input); assert.equal(result.valid, false); assert.equal("value" in result, false); }
  const opening = configured(); opening.player.openingRef = "opening.minecraft"; assert.equal(compileContent(opening).valid, false);
});

test("talent source labels must agree with common versus world-scoped records", () => {
  const worldAsCommon = configured(); worldAsCommon.player.text = worldAsCommon.player.text.replace("[抽取][470.蛊真人 (Reverend Insanity)] 至尊仙胎蛊", "[抽取][通用] 至尊仙胎蛊");
  assert.equal(compileContent(worldAsCommon).diagnostics.some((entry) => entry.code === "PLAYER_TALENT_SCOPE_LABEL_CONFLICT"), true);
  const commonAsWorld = configured(); commonAsWorld.player.text = commonAsWorld.player.text.replace("[抽取][通用] 系统核心权限·root", "[抽取][470.蛊真人 (Reverend Insanity)] 系统核心权限·root");
  assert.equal(compileContent(commonAsWorld).diagnostics.some((entry) => entry.code === "PLAYER_TALENT_SCOPE_LABEL_CONFLICT"), true);
});

test("different aliases resolving to one talent ID are rejected after binding", () => {
  const input = configured(), mapping = input.stableIdMap.find((entry) => entry.id === "talent.gu.sovereign-body"), record = input.records.find((entry) => entry.stableId === "talent.gu.sovereign-body");
  mapping.aliases.push("至尊仙胎蛊别名"); record.aliases.push("至尊仙胎蛊别名");
  const originalLine = input.player.text.split("\n").find((line) => line.includes("至尊仙胎蛊 (SSS)"));
  const aliasLine = originalLine.replace("至尊仙胎蛊 (SSS)", "至尊仙胎蛊别名 (SSS)");
  input.player.text = input.player.text.replace(input.player.text.split("\n").find((line) => line.includes("] 坚持 (SSS)")), aliasLine);
  assert.equal(compileContent(input).diagnostics.some((entry) => entry.code === "PLAYER_TALENT_BINDING_DUPLICATE"), true);
});

test("identity kit ambiguity fails and array supplies use the fixed comma-space join", () => {
  const ambiguous = configured(); const otherKit = ambiguous.items.find((entry) => entry.id === "item.gu.mortal-servant-kit"); otherKit.identityRef = "identity.gu.outer-disciple"; otherKit.description = "一块门派令牌, 一只一转纸鹤蛊";
  assert.equal(compileContent(ambiguous).diagnostics.some((entry) => entry.code === "PLAYER_IDENTITY_KIT_AMBIGUOUS"), true);
  const arrayItems = configured(), identity = arrayItems.records.find((entry) => entry.stableId === "identity.gu.outer-disciple"); identity.data.items = ["一块门派令牌", "一只一转纸鹤蛊"];
  assert.equal(compileContent(arrayItems).valid, true);
});
