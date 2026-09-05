import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { validateConversionReceiptSchema } from "../content/schemas.mjs";
import { compileVerifiedContent, parseStrictJson } from "../tooling/source-input.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixture = (name) => fs.readFile(path.join(root, "fixtures", "rpg02", name), "utf8");
async function evidence() {
  const [inputText, htmlText, selectionText, captureText] = await Promise.all([fixture("compile-input.json"), fixture("selected-source.txt"), fixture("source-selection.json"), fixture("source-capture.json")]);
  return { input: JSON.parse(inputText), htmlText, selectionText, captureText };
}

test("strict JSON accepts noncanonical CRLF but rejects duplicate, dangerous, surrogate, deep, and executable syntax", () => {
  assert.equal(parseStrictJson('{\r\n "b": 2, "a": 1\r\n}').valid, true);
  for (const text of ['{"a":1,"a":2}', '{"__proto__":1}', '{"text":"\\ud800"}', '{"x":()=>1}', '{"x":`value`}', '{"x":{...y}}']) assert.equal(parseStrictJson(text).valid, false, text);
  let deep = "null"; for (let index = 0; index < 66; index++) deep = '{"x":' + deep + "}"; assert.equal(parseStrictJson(deep).valid, false);
});

test("real selected evidence verifies exact records, fragments, carrier, authored bytes, and receipt", async () => {
  const value = await evidence(), before = structuredClone(value.input), report = compileVerifiedContent(value.input, value);
  assert.equal(report.valid, true); assert.deepEqual(value.input, before);
  assert.equal(report.value.verification.sourceRecordCount, 14); assert.equal(report.value.verification.fragmentCount, 18);
  assert.equal(report.value.verification.fullOriginalHtmlStored, false); assert.equal(report.value.compiled.conversionReceipt.hashVerification, "verified_selected_evidence");
  assert.equal(validateConversionReceiptSchema(report.value.compiled.conversionReceipt), true);
  assert.deepEqual([...report.value.sourceFiles.keys()], ["sources/source.real-card.txt", "sources/source.authored-rpg02.txt"]);
  assert.equal(report.value.sourceFiles.get("sources/source.real-card.txt").toString("utf8"), value.htmlText);
});

test("record data, stable mapping, locator and carrier drift fail closed", async () => {
  const original = await evidence(), cases = [];
  const data = structuredClone(original.input); data.records[0].data.desc += "changed"; cases.push([data, original.captureText, original.htmlText]);
  const mapping = structuredClone(original.input); mapping.stableIdMap[0].sourceLocator = "/worldDB/1"; cases.push([mapping, original.captureText, original.htmlText]);
  const capture = JSON.parse(original.captureText); capture.selectedRecords[0].locator = "/worldDB/1"; cases.push([original.input, JSON.stringify(capture), original.htmlText]);
  cases.push([original.input, original.captureText, original.htmlText + " "]);
  for (const [input, captureText, htmlText] of cases) assert.equal(compileVerifiedContent(input, { ...original, input, captureText, htmlText }).valid, false);
});

test("fragment spelling, hash, path, capture data, and evidence cardinality are independently bound", async () => {
  const original = await evidence();
  for (const mutate of [
    (capture) => { capture.selectedRecords[0].fragments[0].text += " "; },
    (capture) => { capture.selectedRecords[0].fragments[0].path = "/desc"; },
    (capture) => { capture.selectedRecords[0].data.name += "x"; },
    (capture) => { capture.selectedRecords.pop(); }
  ]) { const capture = JSON.parse(original.captureText); mutate(capture); assert.equal(compileVerifiedContent(original.input, { ...original, captureText: JSON.stringify(capture) }).valid, false); }
  const injected = JSON.parse(original.captureText), fragment = injected.selectedRecords[0].fragments[0], literal = fragment.text;
  fragment.text = literal + ");unexpectedCall();(" + literal; fragment.end = fragment.start + fragment.text.length; fragment.utf8Bytes = Buffer.byteLength(fragment.text, "utf8"); fragment.sha256 = createHash("sha256").update(fragment.text).digest("hex");
  injected.selectedFragmentUtf8Bytes = injected.selectedRecords.flatMap((record) => record.fragments).reduce((total, entry) => total + entry.utf8Bytes, 0);
  assert.equal(compileVerifiedContent(original.input, { ...original, captureText: JSON.stringify(injected) }).diagnostics[0].code, "SOURCE_FRAGMENT_LITERAL");
  const negative = JSON.parse(original.captureText); negative.selectedRecords[0].fragments[0].start = -1; assert.equal(compileVerifiedContent(original.input, { ...original, captureText: JSON.stringify(negative) }).valid, false);
});

test("authored-byte drift and non-plain metadata fail without getter execution or echoed input", async () => {
  const original = await evidence(), changed = structuredClone(original.input); changed.authored.styles[0].instruction += "changed";
  assert.equal(compileVerifiedContent(changed, original).diagnostics[0].code, "SOURCE_AUTHORED_DRIFT");
  let reads = 0; const hostile = structuredClone(original.input); Object.defineProperty(hostile, "records", { enumerable: true, get() { reads++; throw new Error("secret raw input"); } });
  const first = compileVerifiedContent(hostile, original), second = compileVerifiedContent(hostile, original); assert.equal(reads, 0); assert.deepEqual(first, second); assert.equal(JSON.stringify(first).includes("secret raw input"), false);
  let textReads = 0; const hostileTexts = { selectionText: original.selectionText, captureText: original.captureText }; Object.defineProperty(hostileTexts, "htmlText", { enumerable: true, get() { textReads++; throw new Error("secret source"); } });
  assert.equal(compileVerifiedContent(original.input, hostileTexts).valid, false); assert.equal(textReads, 0);
});

test("source verifier contains no network, model, process, or dynamic execution primitive", async () => {
  const source = await fs.readFile(path.join(root, "tooling", "source-input.mjs"), "utf8");
  for (const forbidden of ["node:http", "node:https", "node:net", "node:child_process", "fetch(", "eval(", "new Function", "modelCalls("]) assert.equal(source.includes(forbidden), false, forbidden);
});
