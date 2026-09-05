import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { crc32 } from "node:zlib";
import { deflateSync } from "fflate";
import { compileContent } from "../content/index.mjs";
import { createBundle, LIMITS } from "../tooling/bundle.mjs";
import { readArchive, writeArchive } from "../tooling/archive.mjs";

function golden() {
  const input = JSON.parse(fs.readFileSync(new URL("../fixtures/rpg02/compile-input.json", import.meta.url), "utf8"));
  const sources = new Map([
    ["sources/source.real-card.txt", fs.readFileSync(new URL("../fixtures/rpg02/selected-source.txt", import.meta.url))],
    ["sources/source.authored-rpg02.txt", Buffer.from(JSON.stringify(input.authored))]
  ]);
  const bundle = createBundle(compileContent(input).value, sources);
  assert.equal(bundle.valid, true, JSON.stringify(bundle.diagnostics));
  return bundle.value.files;
}

// Independent minimal ZIP constructor lets attacks change both central and local
// metadata without depending on the implementation under test.
function rawZip(items) {
  const locals = [], central = []; let offset = 0;
  for (const item of items) {
    const name = Buffer.from(item.name), localName = Buffer.from(item.localName ?? item.name);
    const data = Buffer.from(item.data ?? "x"), method = item.method ?? 0;
    const payload = method === 8 ? Buffer.from(deflateSync(data)) : data;
    const checksum = item.crc ?? crc32(data), compressed = item.compressed ?? payload.length, size = item.size ?? data.length;
    const local = Buffer.alloc(30);
    local.writeUInt32LE(0x04034b50, 0); local.writeUInt16LE(20, 4);
    local.writeUInt16LE(item.flags ?? 0, 6); local.writeUInt16LE(method, 8);
    local.writeUInt32LE(checksum, 14); local.writeUInt32LE(compressed, 18); local.writeUInt32LE(size, 22); local.writeUInt16LE(localName.length, 26);
    const header = Buffer.alloc(46);
    header.writeUInt32LE(0x02014b50, 0); header.writeUInt16LE(0x0314, 4); header.writeUInt16LE(20, 6);
    header.writeUInt16LE(item.flags ?? 0, 8); header.writeUInt16LE(method, 10);
    header.writeUInt32LE(checksum, 16); header.writeUInt32LE(compressed, 20); header.writeUInt32LE(size, 24); header.writeUInt16LE(name.length, 28);
    header.writeUInt32LE(item.attrs ?? 0, 38); header.writeUInt32LE(offset, 42);
    locals.push(local, localName, payload); central.push(header, name);
    offset += local.length + localName.length + payload.length;
  }
  const cd = Buffer.concat(central), end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0); end.writeUInt16LE(items.length, 8); end.writeUInt16LE(items.length, 10); end.writeUInt32LE(cd.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...locals, cd, end]);
}
const members = (files, extra = {}) => [...files].map(([name, data]) => ({ name, data, ...extra }));
async function rejected(bytes, code) {
  const before = Buffer.from(bytes), one = await readArchive(bytes), two = await readArchive(bytes);
  assert.equal(one.valid, false); assert.equal("value" in one, false); assert.deepEqual(one, two); assert.deepEqual(bytes, before);
  if (code) assert.equal(one.diagnostics[0].code, code);
  return one;
}

test("ZIP export is sorted, stored, deterministic and mandatory readback preserves all bytes", async () => {
  const files = golden(), first = await writeArchive(files), second = await writeArchive(new Map([...files].reverse()));
  assert.equal(first.valid, true, JSON.stringify(first.diagnostics)); assert.equal(second.valid, true);
  assert.equal(first.value.bytes.equals(second.value.bytes), true); assert.equal(first.value.archiveSha256, second.value.archiveSha256);
  const replay = await readArchive(first.value.bytes); assert.equal(replay.valid, true);
  assert.deepEqual([...replay.value.files.keys()], [...files.keys()].sort());
  for (const [name, data] of files) assert.equal(replay.value.files.get(name).equals(data), true);
});

test("independent stored and deflated ZIPs pass the same content validator", async () => {
  for (const method of [0, 8]) {
    const result = await readArchive(rawZip(members(golden(), { method })));
    assert.equal(result.valid, true, JSON.stringify(result.diagnostics)); assert.equal(result.value.documents.cardPackage.resources.worlds.length, 2);
  }
});

test("unsafe paths, Windows names, undeclared files and case duplicates are rejected", async () => {
  for (const name of ["../card-package.json", "sources/../bad.txt", "C:/bad.txt", "\\\\server\\x.txt", "sources/con.txt", "sources/LPT1.txt", "sources/a:b.txt", "sources/a.txt.", "sources/a.txt ", "evil.js", "sources/a.zip"]) {
    await rejected(rawZip([{ name, data: "x" }]), "ZIP_MEMBER_NAME");
  }
  await rejected(rawZip([{ name: "card-package.json" }, { name: "card-package.json" }]), "ZIP_DUPLICATE_MEMBER");
  await rejected(rawZip([...members(golden()), { name: "sources/extra.txt" }]));
  await rejected(rawZip([{ name: "card-package.json" }, { name: "CARD-PACKAGE.JSON" }]));
});

test("links, device-like attributes, encryption and unsupported methods fail before extraction", async () => {
  await rejected(rawZip([{ name: "card-package.json", attrs: 0xa1ff0000 }]), "ZIP_LINK_OR_SPECIAL_FILE");
  await rejected(rawZip([{ name: "card-package.json", attrs: 0x10 }]), "ZIP_LINK_OR_SPECIAL_FILE");
  await rejected(rawZip([{ name: "card-package.json", flags: 1 }]), "ZIP_ENCRYPTED");
  await rejected(rawZip([{ name: "card-package.json", method: 99 }]), "ZIP_COMPRESSION_METHOD");
  await rejected(rawZip([{ name: "card-package.json", flags: 8 }]), "ZIP_UNSUPPORTED_FLAGS");
});

test("ZIP input, count, per-file, total and ratio limits reject without output", async () => {
  await rejected(Buffer.alloc(LIMITS.zipBytes + 1), "ZIP_INPUT_LIMIT");
  await rejected(rawZip(Array.from({ length: 65 }, (_, index) => ({ name: "sources/s" + index + ".txt" }))), "ZIP_FILE_LIMIT");
  await rejected(rawZip([{ name: "card-package.json", size: LIMITS.fileBytes + 1 }]), "ZIP_ENTRY_LIMIT");
  await rejected(rawZip(Array.from({ length: 9 }, (_, index) => ({ name: "sources/s" + index + ".txt", size: LIMITS.fileBytes, compressed: LIMITS.fileBytes }))), "ZIP_TOTAL_LIMIT");
  await rejected(rawZip([{ name: "card-package.json", data: "A".repeat(20000), method: 8 }]), "ZIP_RATIO_LIMIT");
});

test("CRC, compressed actual size and local/central mismatch are checked independently", async () => {
  const items = members(golden()), corrupt = rawZip(items);
  corrupt[30 + Buffer.byteLength(items[0].name)] ^= 1;
  await rejected(corrupt, "ZIP_CRC_MISMATCH");
  await rejected(rawZip([{ name: "card-package.json", localName: "evil-payload.json" }]), "ZIP_LOCAL_NAME_MISMATCH");
  const mismatch = rawZip(items); mismatch.writeUInt32LE(0, 14);
  await rejected(mismatch, "ZIP_LOCAL_METADATA_MISMATCH");
  await rejected(rawZip([{ name: "card-package.json", data: "small", size: 4, method: 8 }]));
});

test("truncation, trailing bytes, preambles and fake end records are refused", async () => {
  const valid = rawZip(members(golden()));
  await rejected(valid.subarray(0, valid.length - 1));
  await rejected(Buffer.concat([valid, Buffer.from("tail")]));
  await rejected(Buffer.concat([Buffer.from("MZ"), valid]));
  await rejected(Buffer.alloc(22));
  assert.equal((await readArchive("not binary")).valid, false);
});

test("archive reports never echo unsafe filenames, data or parser stacks", async () => {
  const report = await rejected(rawZip([{ name: "C:/PRIVATE_SENTINEL.txt", data: "PRIVATE_CONTENT" }]));
  const serialized = JSON.stringify(report);
  assert.equal(serialized.includes("PRIVATE"), false); assert.equal(serialized.includes("stack"), false);
});

test("export rejects invalid content without returning a ZIP", async () => {
  const files = golden(); files.delete("content-index.json");
  const result = await writeArchive(files); assert.equal(result.valid, false); assert.equal("value" in result, false);
});
