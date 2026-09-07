import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { compileVerifiedContent } from "../tooling/source-input.mjs";
import { createBundle, sha256 } from "../tooling/bundle.mjs";
import { readArchive, writeArchive } from "../tooling/archive.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixtureRoot = path.join(root, "fixtures", "rpg02");
const names = ["compile-input.json", "selected-source.txt", "source-selection.json", "source-capture.json", "player-text.txt", "player-config.json"];
async function replay() {
  const bytes = Object.fromEntries(await Promise.all(names.map(async (name) => [name, await fs.readFile(path.join(fixtureRoot, name))])));
  const input = JSON.parse(bytes["compile-input.json"]); input.player = { ...JSON.parse(bytes["player-config.json"]), text: bytes["player-text.txt"].toString("utf8") };
  const verified = compileVerifiedContent(input, { htmlText: bytes["selected-source.txt"].toString("utf8"), selectionText: bytes["source-selection.json"].toString("utf8"), captureText: bytes["source-capture.json"].toString("utf8") });
  assert.equal(verified.valid, true, JSON.stringify(verified.diagnostics));
  const bundle = createBundle(verified.value.compiled, verified.value.sourceFiles); assert.equal(bundle.valid, true, JSON.stringify(bundle.diagnostics));
  const archive = await writeArchive(bundle.value.files); assert.equal(archive.valid, true, JSON.stringify(archive.diagnostics));
  return { bytes, verified, bundle, archive };
}

test("the checked-in golden receipt matches two deterministic full offline replays", async () => {
  const golden = JSON.parse(await fs.readFile(path.join(root, "docs", "RPG02_GOLDEN.json"), "utf8")), first = await replay(), second = await replay();
  assert.equal(golden.status, "automated_golden_verified"); assert.equal(golden.manualAcceptance, false); assert.equal(golden.claimAllowed, false);
  assert.equal(golden.fullOriginalHtmlStored, false); assert.equal(golden.websiteProbes, 0); assert.equal(golden.externalModelCalls, 0);
  assert.equal(first.verified.value.compiled.playerSetup.setupId, golden.setupId);
  assert.equal(first.verified.value.verification.sourceRecordCount, golden.counts.sourceRecords); assert.equal(first.verified.value.verification.fragmentCount, golden.counts.fragments);
  assert.equal(first.verified.value.compiled.contentIndex.entries.length, golden.counts.resources);
  for (const name of names) assert.deepEqual({ bytes: first.bytes[name].length, sha256: sha256(first.bytes[name]) }, golden.inputs[name]);
  assert.deepEqual([...first.bundle.value.files.keys()].sort(), Object.keys(golden.members).sort());
  for (const [name, bytes] of first.bundle.value.files) assert.deepEqual({ bytes: bytes.length, sha256: sha256(bytes) }, golden.members[name]);
  assert.deepEqual({ bytes: first.archive.value.bytes.length, sha256: sha256(first.archive.value.bytes) }, golden.archive);
  assert.equal(first.archive.value.bytes.equals(second.archive.value.bytes), true);
  const readback = await readArchive(first.archive.value.bytes); assert.equal(readback.valid, true);
  for (const [name, bytes] of first.bundle.value.files) assert.equal(readback.value.files.get(name).equals(bytes), true);
});
