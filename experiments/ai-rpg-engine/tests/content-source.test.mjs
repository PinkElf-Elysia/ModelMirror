import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { parseExpressionAt } from "acorn";
import { extractSourceRecords } from "../content/index.mjs";

const base = new URL("../fixtures/rpg02/", import.meta.url);
const receipt = JSON.parse(readFileSync(new URL("source-capture.json", base), "utf8"));
const selection = JSON.parse(readFileSync(new URL("source-selection.json", base), "utf8"));
const carrier = readFileSync(new URL("selected-source.txt", base), "utf8");
const sha = (text) => createHash("sha256").update(text, "utf8").digest("hex");

function literal(node) {
  if (node.type === "Literal" && !node.regex && (node.value === null || ["string", "number", "boolean"].includes(typeof node.value))) return node.value;
  if (node.type === "UnaryExpression" && node.operator === "-" && node.argument.type === "Literal" && typeof node.argument.value === "number") return -node.argument.value;
  if (node.type === "ArrayExpression" && node.elements.every(Boolean)) return node.elements.map(literal);
  if (node.type === "ObjectExpression") {
    const output = {};
    for (const property of node.properties) {
      assert.equal(property.type, "Property");
      assert.equal(property.kind, "init");
      assert.equal(property.computed || property.method || property.shorthand, false);
      const key = property.key.type === "Identifier" ? property.key.name : property.key.value;
      assert.equal(typeof key, "string");
      assert.ok(!["__proto__", "prototype", "constructor"].includes(key));
      assert.ok(!Object.hasOwn(output, key));
      output[key] = literal(property.value);
    }
    return output;
  }
  throw new Error("SOURCE_LITERAL_REJECTED");
}

test("real sample has exactly the approved two worlds, four identities and eight talents", () => {
  assert.equal(receipt.sourceUrl, "https://afengy.cash/zh/explore/installed/e23bbc64-4fdd-46d8-92c0-64923961e5d8");
  assert.deepEqual(["world", "identity", "talent"].map((kind) => receipt.selectedRecords.filter((r) => r.kind === kind).length), [2, 4, 8]);
  assert.equal(receipt.liveDomFragmentsMatched, 18);
  assert.equal(receipt.selectedFragmentUtf8Bytes, 2801);
  assert.equal(receipt.recordPolicy.fullOriginalHtmlStored, false);
  assert.equal(receipt.recordPolicy.rawRecords, "extracted");
  assert.equal(receipt.derivedCarrier.kind, "derived");
  assert.equal(receipt.websiteMessageProbes, 0);
  assert.equal(receipt.modelCalls, 0);
  assert.equal(selection.worlds[0].identityNames[0], "中洲门派的外门弟子");
  assert.equal(selection.commonTalentNames[0], "系统核心权限·root");
});

test("selected literal spelling, offsets and decoded values match independently observed hashes", () => {
  let bytes = 0;
  for (const record of receipt.selectedRecords) {
    const restored = {};
    for (const fragment of record.fragments) {
      assert.equal(fragment.end - fragment.start, fragment.text.length);
      assert.equal(Buffer.byteLength(fragment.text), fragment.utf8Bytes);
      assert.equal(sha(fragment.text), fragment.sha256);
      const ast = parseExpressionAt(fragment.text, 0, { ecmaVersion: 2022 });
      assert.equal(ast.end, fragment.text.length);
      const value = literal(ast);
      if (fragment.path === "") Object.assign(restored, value);
      else restored[fragment.path.slice(1)] = value;
      bytes += fragment.utf8Bytes;
    }
    assert.deepEqual(restored, record.data);
    assert.equal(Buffer.byteLength(JSON.stringify(record.data)), record.dataUtf8Bytes);
    assert.equal(sha(JSON.stringify(record.data)), record.dataSha256);
  }
  assert.equal(bytes, 2801);
});

test("authored minimal carrier re-extracts every real selected value without source UI code", () => {
  assert.equal(Buffer.byteLength(carrier), receipt.derivedCarrier.utf8Bytes);
  assert.equal(sha(carrier), receipt.derivedCarrier.sha256);
  assert.equal(carrier.includes("function "), false);
  const result = extractSourceRecords(carrier, selection);
  assert.equal(result.valid, true);
  assert.equal(result.value.records.length, 14);
  result.value.records.forEach((record, index) => {
    const original = receipt.selectedRecords[index];
    assert.equal(record.kind, original.kind);
    assert.equal(record.worldName, original.worldName);
    assert.deepEqual(record.data, original.data);
    assert.notEqual(record.locator, original.locator);
  });
});

test("explicit stable ID registration preserves source meaning and all five user talents", () => {
  assert.equal(new Set(receipt.stableIdMap.map((entry) => entry.id)).size, 14);
  for (const registration of receipt.stableIdMap) {
    const matches = receipt.selectedRecords.filter((record) => record.locator === registration.sourceLocator);
    assert.equal(matches.length, 1);
    assert.equal(registration.expectedDataSha256, matches[0].dataSha256);
    assert.deepEqual(registration.aliases, [matches[0].data.name]);
  }
  const names = ["至尊仙胎蛊", "坚持", "春秋蝉(重生)", "九转尊者资质", "系统核心权限·root"];
  const talents = receipt.selectedRecords.filter((record) => names.includes(record.data.name));
  assert.equal(talents.length, 5);
  assert.deepEqual(talents.map((record) => record.data.color), ["SSS", "SSS", "SSS", "SSS", "UR"]);
  assert.equal(talents.at(-1).data.cost, 250000);
  assert.equal(receipt.selectedRecords.find((record) => record.locator === "/worldDB/469/identities/4").data.items, "一块门派令牌, 一只一转纸鹤蛊");
  assert.ok(receipt.stableIdMap.every((entry) => !Object.hasOwn(entry, "permissions")));
});
