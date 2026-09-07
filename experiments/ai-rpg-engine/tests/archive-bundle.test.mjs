import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { compileContent } from "../content/index.mjs";
import { canonicalJson, createBundle, LIMITS, safeMemberName, sha256, validateBundleFiles } from "../tooling/bundle.mjs";
import { crc32 } from "node:zlib";
import { readBundleDirectory, writeBundleDirectory } from "../tooling/directory.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const work = path.join(root, ".rpg02-work");
const readJson = async (name) => JSON.parse(await fs.readFile(path.join(root, "fixtures", "rpg02", name), "utf8"));
function copyMap(input) { return new Map([...input].map(([name, bytes]) => [name, Buffer.from(bytes)])); }
function refreshManifest(files, manifest) {
  const next = structuredClone(manifest);
  next.files = [...files].filter(([name]) => name !== "bundle-manifest.json").sort(([a], [b]) => a.localeCompare(b)).map(([memberPath, bytes]) => ({ path: memberPath, bytes: bytes.length, sha256: sha256(bytes), crc32: crc32(bytes) }));
  files.set("bundle-manifest.json", Buffer.from(canonicalJson(next)));
}
function setDocument(files, name, value, manifest) { files.set(name, Buffer.from(canonicalJson(value))); refreshManifest(files, manifest); }
async function removeOwnedSandbox(sandbox, prefix) {
  const resolvedWork = path.resolve(work), resolved = path.resolve(sandbox);
  if (path.dirname(resolved) !== resolvedWork || !path.basename(resolved).startsWith(prefix)) throw new Error("unsafe test cleanup target");
  await fs.rm(resolved, { recursive: true, force: true });
}
async function compiledBundle() {
  const input = await readJson("compile-input.json");
  const compiled = compileContent(input); assert.equal(compiled.valid, true);
  const sources = new Map([
    ["sources/source.real-card.txt", await fs.readFile(path.join(root, "fixtures", "rpg02", "selected-source.txt"))],
    ["sources/source.authored-rpg02.txt", Buffer.from(JSON.stringify(input.authored), "utf8")]
  ]);
  return createBundle(compiled.value, sources);
}

test("canonical JSON sorts recursive object keys without changing arrays", () => {
  assert.equal(canonicalJson({ z: 1, a: { y: 2, x: [3, { b: 1, a: 2 }] } }), '{\n  "a": {\n    "x": [\n      3,\n      {\n        "a": 2,\n        "b": 1\n      }\n    ],\n    "y": 2\n  },\n  "z": 1\n}\n');
  const getter = {}; Object.defineProperty(getter, "secret", { enumerable: true, get() { throw new Error("executed"); } });
  assert.throws(() => canonicalJson(getter), { code: "BUNDLE_NON_JSON" });
  assert.throws(() => canonicalJson(new Array(1)), { code: "BUNDLE_NON_JSON" });
  assert.throws(() => canonicalJson({ text: "\ud800" }), { code: "BUNDLE_NON_JSON" });
  let deep = null; for (let index = 0; index < 66; index++) deep = { child: deep }; assert.throws(() => canonicalJson(deep), { code: "BUNDLE_NON_JSON" });
});

test("safe member names reject traversal, aliases, reserved names, and Windows forms", () => {
  for (const name of ["../x", "/x", "C:/x", "sources\\x.txt", "sources//x.txt", "sources/CON.txt", "sources/x. ", "sources/./x"]) assert.equal(safeMemberName(name), false, name);
  assert.equal(safeMemberName("sources/source.real-card.txt"), true);
  assert.deepEqual(LIMITS, { files: 64, fileBytes: 2097152, totalBytes: 16777216, zipBytes: 16777216, ratio: 100 });
  assert.equal(createBundle({}, new Map()).valid, false);
});

test("creates and validates a canonical bundle with cloned buffers and strict documents", async () => {
  const result = await compiledBundle(); assert.equal(result.valid, true);
  assert.equal(result.value.manifest.files.some((entry) => entry.path === "bundle-manifest.json"), false);
  assert.equal(result.value.documents.cardPackage.resources.worlds.length, 2); assert.equal(result.value.documents.playerSetup, undefined);
  const original = result.value.files.get("card-package.json"), validated = validateBundleFiles(result.value.files); original[0] = 0;
  assert.notEqual(validated.value.files.get("card-package.json")[0], 0);
});

test("integrity, canonical JSON, UTF-8, nested archives and file limits fail closed", async () => {
  const valid = await compiledBundle();
  const tampered = copyMap(valid.value.files); tampered.get("card-package.json")[20] ^= 1; assert.equal(validateBundleFiles(tampered).valid, false);
  const noncanonical = copyMap(valid.value.files); noncanonical.set("content-index.json", Buffer.from(JSON.stringify(valid.value.documents.contentIndex))); assert.equal(validateBundleFiles(noncanonical).valid, false);
  const invalidUtf = copyMap(valid.value.files); invalidUtf.set("sources/source.real-card.txt", Buffer.from([0xc3, 0x28])); refreshManifest(invalidUtf, valid.value.manifest); assert.equal(validateBundleFiles(invalidUtf).diagnostics[0].code, "BUNDLE_UTF8_INVALID");
  const nested = copyMap(valid.value.files); nested.set("sources/source.real-card.txt", Buffer.from([0x50, 0x4b, 0x03, 0x04])); assert.equal(validateBundleFiles(nested).diagnostics[0].code, "BUNDLE_NESTED_ARCHIVE");
  const huge = copyMap(valid.value.files); huge.set("sources/source.real-card.txt", Buffer.alloc(LIMITS.fileBytes + 1, 65)); assert.equal(validateBundleFiles(huge).diagnostics[0].code, "BUNDLE_FILE_LIMIT");
});

test("source count, source hash, index, receipt, schema and CRC drift are rejected", async () => {
  const valid = await compiledBundle();
  const cases = [];
  const noSource = copyMap(valid.value.files); noSource.delete("sources/source.real-card.txt"); refreshManifest(noSource, valid.value.manifest); cases.push([noSource, "BUNDLE_SOURCE_FILE_COUNT"]);
  const sourceHash = copyMap(valid.value.files); sourceHash.get("sources/source.real-card.txt")[10] ^= 1; refreshManifest(sourceHash, valid.value.manifest); cases.push([sourceHash, "BUNDLE_SOURCE_HASH"]);
  const index = copyMap(valid.value.files); const indexDoc = structuredClone(valid.value.documents.contentIndex); indexDoc.entries[0].kind = "talent"; setDocument(index, "content-index.json", indexDoc, valid.value.manifest); cases.push([index, "BUNDLE_INDEX_DRIFT"]);
  const receipt = copyMap(valid.value.files); const receiptDoc = structuredClone(valid.value.documents.conversionReceipt); receiptDoc.resourceCount++; setDocument(receipt, "conversion-receipt.json", receiptDoc, valid.value.manifest); cases.push([receipt, "BUNDLE_RECEIPT_DRIFT"]);
  const recordCount = copyMap(valid.value.files); const countDoc = structuredClone(valid.value.documents.conversionReceipt); countDoc.sourceRecordCount++; setDocument(recordCount, "conversion-receipt.json", countDoc, valid.value.manifest); cases.push([recordCount, "BUNDLE_RECEIPT_DRIFT"]);
  const duplicateEvidence = copyMap(valid.value.files); const duplicateDoc = structuredClone(valid.value.documents.conversionReceipt); duplicateDoc.sourceEvidence[1] = structuredClone(duplicateDoc.sourceEvidence[0]); setDocument(duplicateEvidence, "conversion-receipt.json", duplicateDoc, valid.value.manifest); cases.push([duplicateEvidence, "BUNDLE_RECEIPT_SOURCE_DRIFT"]);
  const crc = copyMap(valid.value.files); const manifest = structuredClone(valid.value.manifest); manifest.files[0].crc32 ^= 1; crc.set("bundle-manifest.json", Buffer.from(canonicalJson(manifest))); cases.push(crc);
  const schema = copyMap(valid.value.files); const card = structuredClone(valid.value.documents.cardPackage); card.execute = true; setDocument(schema, "card-package.json", card, valid.value.manifest); cases.push([schema, "BUNDLE_CARD_PACKAGE"]);
  for (const candidate of cases) { const [files, code] = Array.isArray(candidate) ? candidate : [candidate]; const report = validateBundleFiles(files); assert.equal(report.valid, false); if (code) assert.equal(report.diagnostics[0].code, code); }
});

test("canonical source-document envelopes validate and reject schema or duplicate-key drift", async () => {
  const valid = await compiledBundle(), sourceId = "source.authored-rpg02";
  function jsonSource(bytes) {
    const files = copyMap(valid.value.files); files.delete("sources/" + sourceId + ".txt"); files.set("sources/" + sourceId + ".json", bytes);
    const card = structuredClone(valid.value.documents.cardPackage), receipt = structuredClone(valid.value.documents.conversionReceipt), digest = sha256(bytes);
    card.provenance.sources.find((source) => source.id === sourceId).sha256 = digest;
    receipt.sourceEvidence.find((entry) => entry.sourceRef === sourceId).sha256 = digest;
    files.set("card-package.json", Buffer.from(canonicalJson(card))); files.set("conversion-receipt.json", Buffer.from(canonicalJson(receipt))); refreshManifest(files, valid.value.manifest); return files;
  }
  const envelope = { format: "modelmirror.ai-rpg.source-document", formatVersion: "0.1.0", sourceId, text: "untrusted source text" };
  assert.equal(validateBundleFiles(jsonSource(Buffer.from(canonicalJson(envelope)))).valid, true);
  assert.equal(validateBundleFiles(jsonSource(Buffer.from(canonicalJson({ ...envelope, execute: true })))).diagnostics[0].code, "BUNDLE_SOURCE_DOCUMENT");
  const duplicate = Buffer.from('{\n  "format": "modelmirror.ai-rpg.source-document",\n  "formatVersion": "0.1.0",\n  "sourceId": "' + sourceId + '",\n  "text": "a",\n  "text": "b"\n}\n');
  assert.equal(validateBundleFiles(jsonSource(duplicate)).diagnostics[0].code, "BUNDLE_SOURCE_DOCUMENT");
});

test("writes a new directory exclusively and reads it through the common validator", async (t) => {
  await fs.mkdir(work, { recursive: true }); const sandbox = await fs.mkdtemp(path.join(work, "bundle-dir-")); t.after(() => removeOwnedSandbox(sandbox, "bundle-dir-"));
  const bundle = await compiledBundle(), destination = path.join(sandbox, "output");
  const written = await writeBundleDirectory(bundle.value.files, destination); assert.equal(written.valid, true);
  const read = await readBundleDirectory(destination); assert.equal(read.valid, true); assert.equal(read.value.documents.cardPackage.package.id, "card.representative-worlds");
  assert.equal((await writeBundleDirectory(bundle.value.files, destination)).diagnostics[0].code, "DIRECTORY_DESTINATION_EXISTS");
});

test("invalid input creates no directory and directory links are rejected", async (t) => {
  await fs.mkdir(work, { recursive: true }); const sandbox = await fs.mkdtemp(path.join(work, "bundle-fail-")); t.after(() => removeOwnedSandbox(sandbox, "bundle-fail-"));
  const bundle = await compiledBundle(), invalid = copyMap(bundle.value.files); invalid.delete("card-package.json"); const destination = path.join(sandbox, "absent");
  assert.equal((await writeBundleDirectory(invalid, destination)).valid, false); await assert.rejects(fs.lstat(destination), { code: "ENOENT" });
  const outside = path.join(sandbox, "outside"); await fs.mkdir(outside); const linked = path.join(sandbox, "linked");
  try { await fs.symlink(outside, linked, process.platform === "win32" ? "junction" : "dir"); } catch (error) { if (error?.code === "EPERM") return t.skip("host does not permit links"); throw error; }
  assert.equal((await readBundleDirectory(linked)).diagnostics[0].code, "DIRECTORY_ROOT_UNSAFE");
});

test("a mid-write failure rolls back only the new destination and preserves its parent", async (t) => {
  await fs.mkdir(work, { recursive: true }); const sandbox = await fs.mkdtemp(path.join(work, "bundle-rollback-")); t.after(() => removeOwnedSandbox(sandbox, "bundle-rollback-"));
  const bundle = await compiledBundle(), destination = path.join(sandbox, "output"), originalOpen = fs.open; let writes = 0;
  fs.open = async (...args) => { if (args[1] === "wx" && ++writes === 2) throw Object.assign(new Error("full"), { code: "ENOSPC" }); return originalOpen(...args); };
  try { const report = await writeBundleDirectory(bundle.value.files, destination); assert.equal(report.valid, false); assert.equal(report.diagnostics[0].code, "ENOSPC"); }
  finally { fs.open = originalOpen; }
  await assert.rejects(fs.lstat(destination), { code: "ENOENT" }); assert.equal((await fs.lstat(sandbox)).isDirectory(), true);
});
