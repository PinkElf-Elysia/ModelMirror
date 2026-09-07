import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { extractSourceRecords } from "../content/index.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixture = fs.readFileSync(path.join(root, "fixtures", "rpg02", "synthetic-source.txt"), "utf8");
const selection = { format: "modelmirror.ai-rpg.source-selection", formatVersion: "0.1.0", worlds: [{ name: "世界一", identityNames: ["旅者"], talentNames: ["逆价"] }], commonTalentNames: ["坚韧"] };

test("extracts ordered records and never executes UI scripts", () => {
  delete globalThis.syntheticUiExecuted; const before = structuredClone(selection), value = extractSourceRecords(fixture, selection);
  assert.equal(value.valid, true);
  assert.deepEqual(value.value.records.map((entry) => [entry.kind, entry.worldName, entry.locator]), [["world", "世界一", "/worldDB/world~1~0alpha"], ["identity", "世界一", "/worldDB/world~1~0alpha/identities/0"], ["talent", "世界一", "/worldDB/world~1~0alpha/talents/0"], ["talent", null, "/commonTalents/0"]]);
  assert.equal(value.value.records[2].data.cost, -3); assert.equal(value.value.records[0].data.desc, "<b>仅文本</b> 😀");
  assert.equal(globalThis.syntheticUiExecuted, undefined); assert.deepEqual(selection, before); assert.equal(value.value.source.utf8Bytes > value.value.source.utf16Units, true);
});

test("source ranges slice exact original values and objects", () => {
  const value = extractSourceRecords(fixture, selection).value;
  for (const record of value.records) for (const range of record.sourceRanges) {
    const source = fixture.slice(range.start, range.end);
    if (record.kind === "world") assert.equal(source.includes(record.data[range.path.slice(1)]), true); else assert.equal(source.startsWith("{"), true);
  }
});

test("zero and ambiguous exact matches fail stably without value", () => {
  const missing = { ...selection, worlds: [{ ...selection.worlds[0], name: "不存在" }] };
  const first = extractSourceRecords(fixture, missing), second = extractSourceRecords(fixture, missing);
  assert.equal(first.valid, false); assert.equal("value" in first, false); assert.deepEqual(first, second);
  const talentDup = fixture.replace("const commonTalents = [", "const commonTalents = [{name:'坚韧',color:'红',cost:1,desc:'重复',type:'被动'},");
  assert.equal(extractSourceRecords(talentDup, selection).diagnostics.some((entry) => entry.code === "NAME_AMBIGUOUS"), true);
  const worldDup = fixture.replace("};\nconst commonTalents", ", other:{name:'世界一',desc:'d',boss:'b',identities:[],talents:[]}};\nconst commonTalents");
  assert.equal(extractSourceRecords(worldDup, selection).diagnostics.some((entry) => entry.code === "NAME_AMBIGUOUS"), true);
});

test("selection rejects duplicates, versions, unknowns, accessors, and cycles", () => {
  const duplicate = structuredClone(selection); duplicate.commonTalentNames.push("坚韧");
  assert.equal(extractSourceRecords(fixture, duplicate).diagnostics.some((entry) => entry.code === "SELECTION_DUPLICATE_NAME"), true);
  assert.equal(extractSourceRecords(fixture, { ...selection, formatVersion: "9" }).valid, false);
  assert.equal(extractSourceRecords(fixture, { ...selection, extra: true }).valid, false);
  const getter = structuredClone(selection); Object.defineProperty(getter, "extra", { get() { throw new Error("must not run"); }, enumerable: true });
  assert.equal(extractSourceRecords(fixture, getter).diagnostics.some((entry) => entry.code === "SELECTION_ACCESSOR"), true);
  const cycle = structuredClone(selection); cycle.self = cycle;
  assert.equal(extractSourceRecords(fixture, cycle).diagnostics.some((entry) => entry.code === "SELECTION_CYCLE"), true);
  assert.equal(extractSourceRecords(fixture, { ...selection, worlds: "invalid" }).valid, false);
  let reads = 0;
  const names = []; Object.defineProperty(names, "0", { get() { reads++; throw new Error("must not run"); }, enumerable: true }); names.length = 1;
  const indexedGetter = { ...selection, commonTalentNames: names };
  const getterReport = extractSourceRecords(fixture, indexedGetter);
  assert.equal(getterReport.diagnostics.some((entry) => entry.code === "SELECTION_ACCESSOR"), true); assert.equal(reads, 0);
  const extraArray = structuredClone(selection); extraArray.commonTalentNames.extra = true;
  assert.equal(extractSourceRecords(fixture, extraArray).diagnostics.some((entry) => entry.code === "SELECTION_ARRAY_EXTRA_PROPERTY"), true);
  const symbolObject = structuredClone(selection); symbolObject[Symbol("hidden")] = true;
  assert.equal(extractSourceRecords(fixture, symbolObject).diagnostics.some((entry) => entry.code === "SELECTION_SYMBOL_PROPERTY"), true);
});

test("selected field drift is rejected", () => {
  const changed = fixture.replace('name:"旅者",items:', 'name:"旅者",unknown:true,items:');
  assert.equal(extractSourceRecords(changed, selection).diagnostics.some((entry) => entry.code === "RECORD_UNKNOWN_FIELD"), true);
});

test("all forbidden initializer categories fail without evaluation", () => {
  const expressions = ["danger()", "other.value", "`value`", "[...other]", "{get name(){return 'x'}}", "{name}", "{['name']:'x'}", "{name:'x',name:'y'}", "{__proto__:{}}", "/x/", "1n", "[,,]"];
  for (const expression of expressions) {
    const html = "<script>const worldDB=" + expression + ";const commonTalents=[];</script>";
    assert.equal(extractSourceRecords(html, { ...selection, worlds: [], commonTalentNames: [] }).valid, false, expression);
  }
  const assignment = "<script>let worldDB=[];worldDB=[];const commonTalents=[];</script>";
  assert.equal(extractSourceRecords(assignment, { ...selection, worlds: [], commonTalentNames: [] }).valid, false);
});

test("UTF validity, byte cap, and depth cap fail closed", () => {
  assert.equal(extractSourceRecords("\ud800", selection).diagnostics[0].code, "HTML_INVALID_UTF16");
  assert.equal(extractSourceRecords("😀".repeat(4 * 1024 * 1024 + 1), selection).diagnostics.some((entry) => entry.code === "HTML_UTF8_LIMIT"), true);
  const nested = "[".repeat(70) + "]".repeat(70), html = "<script>const worldDB=" + nested + ";const commonTalents=[];</script>";
  assert.equal(extractSourceRecords(html, { ...selection, worlds: [], commonTalentNames: [] }).diagnostics.some((entry) => entry.code === "AST_TOO_DEEP"), true);
});
