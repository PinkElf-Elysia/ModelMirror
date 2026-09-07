import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { runCli } from "../tooling/cli.mjs";
import { readArchive } from "../tooling/archive.mjs";
import { readBundleDirectory } from "../tooling/directory.mjs";
import { sha256 } from "../tooling/bundle.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixture = path.join(root, "fixtures", "rpg02");
const work = path.join(root, ".rpg02-work");
const talentRefs = ["talent.gu.sovereign-body", "talent.gu.perseverance", "talent.gu.spring-autumn-cicada", "talent.gu.venerable-aptitude", "talent.common.root"];
async function sandbox(t) {
  await fs.mkdir(work, { recursive: true });
  const directory = await fs.mkdtemp(path.join(work, "e2e-"));
  t.after(async () => {
    const resolved = path.resolve(directory), real = await fs.realpath(directory);
    assert.equal(path.dirname(resolved), work);
    assert.equal(path.basename(resolved).startsWith("e2e-"), true);
    assert.equal(real.toLowerCase(), resolved.toLowerCase());
    await fs.rm(resolved, { recursive: true, force: true });
  });
  return directory;
}
function baseArgs(output) {
  return ["compile", "--input", path.join(fixture, "compile-input.json"), "--html", path.join(fixture, "selected-source.txt"),
    "--selection", path.join(fixture, "source-selection.json"), "--capture", path.join(fixture, "source-capture.json"), "--out", output];
}
async function configFile(directory, activations = talentRefs.map((talentRef) => ({ talentRef, active: true }))) {
  // This is explicit golden setup configuration, never inferred from carrying.
  const config = { setupId: "setup.bai-yu-ling-yin", openingRef: "opening.gu", activations, backgroundRefs: ["background.gu.arrival"] };
  const file = path.join(directory, "player-config.json");
  await fs.writeFile(file, JSON.stringify(config), { flag: "wx" }); return file;
}
const withPlayer = (args, file) => [...args, "--player-text", path.join(fixture, "player-text.txt"), "--player-config", file];

test("real samples and full player traverse CLI compile, directory, ZIP and replay offline", async (t) => {
  const directory = await sandbox(t), config = await configFile(directory), output = path.join(directory, "compiled");
  const priorFetch = globalThis.fetch; globalThis.fetch = () => { throw new Error("network forbidden"); };
  t.after(() => { globalThis.fetch = priorFetch; });
  const compiled = await runCli(withPlayer(baseArgs(output), config));
  assert.equal(compiled.valid, true, JSON.stringify(compiled.diagnostics));
  assert.equal(compiled.value.sourceRecords, 14); assert.equal(compiled.value.playerIncluded, true);
  const read = await readBundleDirectory(output); assert.equal(read.valid, true);
  assert.equal(read.value.documents.conversionReceipt.hashVerification, "verified_selected_evidence");
  assert.equal(read.value.documents.conversionReceipt.toolingVerification.fullOriginalHtmlStored, false);
  assert.equal(read.value.documents.playerSetup.talents.length, 5);
  assert.deepEqual(read.value.documents.playerSetup.runtimePermissions, []);
  const firstZip = path.join(directory, "first.zip"), secondZip = path.join(directory, "second.zip");
  const first = await runCli(["pack", "--input", output, "--out", firstZip]);
  const second = await runCli(["pack", "--input", output, "--out", secondZip]);
  assert.equal(first.valid, true, JSON.stringify(first.diagnostics)); assert.equal(second.valid, true);
  const bytes = await fs.readFile(firstZip);
  assert.equal(first.value.archiveSha256, sha256(bytes)); assert.equal(first.value.archiveSha256, second.value.archiveSha256);
  assert.equal(bytes.equals(await fs.readFile(secondZip)), true);
  const replay = await readArchive(bytes); assert.equal(replay.valid, true);
  for (const [name, data] of read.value.files) assert.equal(replay.value.files.get(name).equals(data), true);
  const restored = path.join(directory, "restored"), unpacked = await runCli(["unpack", "--input", firstZip, "--out", restored]);
  assert.equal(unpacked.valid, true);
  for (const target of [firstZip, output, restored]) assert.equal((await runCli(["verify", "--input", target])).valid, true);
});

test("CLI permits a card-only delivery and never creates a player implicitly", async (t) => {
  const directory = await sandbox(t), output = path.join(directory, "card-only");
  const result = await runCli(baseArgs(output)); assert.equal(result.valid, true, JSON.stringify(result.diagnostics));
  assert.equal(result.value.playerIncluded, false);
  const read = await readBundleDirectory(output); assert.equal(read.value.files.has("player-setup.json"), false);
});

test("source drift and missing explicit activation produce no output or partial files", async (t) => {
  const directory = await sandbox(t), output = path.join(directory, "absent");
  const changed = path.join(directory, "changed.txt");
  await fs.writeFile(changed, (await fs.readFile(path.join(fixture, "selected-source.txt"), "utf8")).replace("蛊真人", "漂移文本"), { flag: "wx" });
  const args = baseArgs(output); args[args.indexOf("--html") + 1] = changed;
  assert.equal((await runCli(args)).valid, false); await assert.rejects(fs.lstat(output), { code: "ENOENT" });
  const config = await configFile(directory, talentRefs.slice(1).map((talentRef) => ({ talentRef, active: false })));
  assert.equal((await runCli(withPlayer(baseArgs(output), config))).valid, false);
  await assert.rejects(fs.lstat(output), { code: "ENOENT" });
});

test("existing directory and ZIP remain byte-for-byte unchanged after a refused overwrite", async (t) => {
  const directory = await sandbox(t), output = path.join(directory, "compiled");
  assert.equal((await runCli(baseArgs(output))).valid, true);
  const before = await readBundleDirectory(output);
  assert.equal((await runCli(baseArgs(output))).valid, false);
  const after = await readBundleDirectory(output);
  for (const [name, bytes] of before.value.files) assert.equal(bytes.equals(after.value.files.get(name)), true);
  const zip = path.join(directory, "existing.zip"); await fs.writeFile(zip, "user-owned-marker", { flag: "wx" });
  const result = await runCli(["pack", "--input", output, "--out", zip]);
  assert.equal(result.valid, false); assert.equal(await fs.readFile(zip, "utf8"), "user-owned-marker");
});

test("invalid arguments, duplicate JSON keys and corrupt archives fail without echo or output", async (t) => {
  const directory = await sandbox(t), output = path.join(directory, "absent"), invalid = path.join(directory, "invalid.zip");
  for (const args of [[], ["compile"], ["verify", "--input", "PRIVATE_SENTINEL", "--input", "duplicate"], ["verify", "--model", "PRIVATE_SENTINEL"]]) {
    const report = await runCli(args); assert.equal(report.valid, false); assert.equal(JSON.stringify(report).includes("PRIVATE_SENTINEL"), false);
  }
  await fs.writeFile(invalid, Buffer.from([1, 2, 3]), { flag: "wx" });
  assert.equal((await runCli(["unpack", "--input", invalid, "--out", output])).valid, false);
  await assert.rejects(fs.lstat(output), { code: "ENOENT" });
  const json = path.join(directory, "duplicate.json"); await fs.writeFile(json, '{"format":"first","format":"second"}', { flag: "wx" });
  const args = baseArgs(output); args[args.indexOf("--input") + 1] = json;
  assert.equal((await runCli(args)).valid, false); await assert.rejects(fs.lstat(output), { code: "ENOENT" });
});
