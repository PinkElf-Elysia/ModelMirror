import { diagnostic, sortDiagnostics } from "./schemas.mjs";

const MAX_BYTES = 1024 * 1024;
const SECTIONS = ["玩家设定", "开局模式", "世界信息", "身份设定", "携带天赋"];
const FIELDS = { 玩家设定: ["姓名", "性别", "年龄", "外貌", "性格", "XP", "其他"], 世界信息: ["名称", "简介", "S级代表"], 身份设定: ["身份", "等级", "物资"] };
function finish(values, value) { const diagnostics = sortDiagnostics(values); return diagnostics.length ? Object.freeze({ valid: false, diagnostics }) : Object.freeze({ valid: true, diagnostics, value }); }
function validateText(text) {
  if (typeof text !== "string") return [diagnostic("player", "PLAYER_TEXT_NOT_STRING", "/text")];
  const values = [];
  for (let index = 0; index < text.length; index++) {
    const unit = text.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) { const next = text.charCodeAt(index + 1); if (!(next >= 0xdc00 && next <= 0xdfff)) { values.push(diagnostic("player", "PLAYER_TEXT_INVALID_UTF16", "/text")); break; } index++; }
    else if (unit >= 0xdc00 && unit <= 0xdfff) { values.push(diagnostic("player", "PLAYER_TEXT_INVALID_UTF16", "/text")); break; }
  }
  if (new TextEncoder().encode(text).byteLength > MAX_BYTES) values.push(diagnostic("player", "PLAYER_TEXT_LIMIT", "/text"));
  return values;
}
export function parsePlayerText(text) {
  const diagnostics = validateText(text); if (diagnostics.length) return finish(diagnostics);
  const lines = text.replaceAll("\r\n", "\n").split("\n"); if (lines.at(-1) === "") lines.pop();
  const sections = new Map(), order = []; let current = null;
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const inlineOpening = line.match(/^【开局模式】：(.+)$/u);
    const heading = line.match(/^【([^】]+)】$/u);
    if (inlineOpening) {
      if (sections.has("开局模式")) diagnostics.push(diagnostic("player", "PLAYER_SECTION_DUPLICATE", "/lines/" + index));
      else { sections.set("开局模式", [{ line: inlineOpening[1], index }]); order.push("开局模式"); current = null; }
    } else if (heading) {
      const name = heading[1];
      if (!SECTIONS.includes(name) || name === "开局模式") diagnostics.push(diagnostic("player", "PLAYER_SECTION_UNKNOWN", "/lines/" + index));
      else if (sections.has(name)) diagnostics.push(diagnostic("player", "PLAYER_SECTION_DUPLICATE", "/lines/" + index));
      else { sections.set(name, []); order.push(name); current = name; }
    } else if (line === "") { current = null; }
    else if (!current) diagnostics.push(diagnostic("player", "PLAYER_TRAILING_OR_UNSCOPED_TEXT", "/lines/" + index));
    else sections.get(current).push({ line, index });
  }
  for (const name of SECTIONS) if (!sections.has(name)) diagnostics.push(diagnostic("player", "PLAYER_SECTION_MISSING", "/" + name));
  if (JSON.stringify(order) !== JSON.stringify(SECTIONS)) diagnostics.push(diagnostic("player", "PLAYER_SECTION_ORDER", ""));
  if (diagnostics.length) return finish(diagnostics);
  const parsed = {};
  for (const section of ["玩家设定", "世界信息", "身份设定"]) {
    const values = {}, allowed = FIELDS[section];
    for (const entry of sections.get(section)) {
      const match = entry.line.match(/^([^：]+)：(.+)$/u);
      if (!match || !allowed.includes(match[1])) { diagnostics.push(diagnostic("player", "PLAYER_FIELD_UNKNOWN", "/lines/" + entry.index)); continue; }
      if (Object.hasOwn(values, match[1])) diagnostics.push(diagnostic("player", "PLAYER_FIELD_DUPLICATE", "/lines/" + entry.index)); else values[match[1]] = match[2];
    }
    for (const field of allowed) if (!Object.hasOwn(values, field)) diagnostics.push(diagnostic("player", "PLAYER_FIELD_MISSING", "/" + section + "/" + field));
    parsed[section] = values;
  }
  const talents = [];
  for (const entry of sections.get("携带天赋")) {
    const match = entry.line.match(/^\[([^\]]+)\]\[([^\]]+)\] (.+) \(([^()]+)\): (.+)$/u);
    if (!match) diagnostics.push(diagnostic("player", "PLAYER_TALENT_LINE_INVALID", "/lines/" + entry.index));
    else talents.push({ sourceLabel: match[1], worldName: match[2], name: match[3], tierLabel: match[4], description: match[5], owned: true });
  }
  if (talents.length < 1 || talents.length > 16) diagnostics.push(diagnostic("player", "PLAYER_TALENT_COUNT", "/携带天赋"));
  const seen = new Set();
  talents.forEach((talent, index) => { const key = talent.worldName + "\u0000" + talent.name; if (seen.has(key)) diagnostics.push(diagnostic("player", "PLAYER_TALENT_DUPLICATE", "/携带天赋/" + index)); seen.add(key); });
  const ageText = parsed["玩家设定"].年龄, age = /^[0-9]+$/u.test(ageText ?? "") ? Number(ageText) : NaN;
  if (!Number.isInteger(age) || age < 0 || age > 1000) diagnostics.push(diagnostic("player", "PLAYER_AGE_INVALID", "/玩家设定/年龄"));
  if (diagnostics.length) return finish(diagnostics);
  return finish([], structuredClone({ rawText: text, character: { name: parsed["玩家设定"].姓名, gender: parsed["玩家设定"].性别, age, appearance: parsed["玩家设定"].外貌, personality: parsed["玩家设定"].性格, xpText: parsed["玩家设定"].XP, otherText: parsed["玩家设定"].其他 }, openingMode: sections.get("开局模式")[0].line, world: { name: parsed["世界信息"].名称, description: parsed["世界信息"].简介, boss: parsed["世界信息"]["S级代表"] }, identity: { name: parsed["身份设定"].身份, rankLabel: parsed["身份设定"].等级, items: parsed["身份设定"].物资 }, talents }));
}
