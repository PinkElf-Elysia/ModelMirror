import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, readdir, rm, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  createR22OwnedTemporaryDirectory,
  publishR22Artifacts,
  removeR22OwnedTemporaryDirectory,
  trustR22TemporaryRoot,
} from "../scripts/lib/r22-cli-core.mjs";
import { runR22Capture } from "../scripts/capture-r22.mjs";
import { runR22Preview } from "../scripts/preview-r22.mjs";

const TEMPORARY_ROOT = path.join(path.parse(fileURLToPath(import.meta.url)).root, "tmp");

async function temporary(t) { const root = await mkdtemp(path.join(tmpdir(), "r22-cli-")); t.after(() => rm(root, { recursive: true, force: true })); return root; }

test("publication uses a new direct temporary child and one atomic rename", async (t) => {
  const root = await temporary(t); const output = path.join(root, "result"); let renames = 0;
  const result = await publishR22Artifacts({ output, temporaryRoot: root, artifacts: new Map([["a.json", "{}"], ["b.json", "[]"]]) }, { async rename(from, to) { renames += 1; return (await import("node:fs/promises")).rename(from, to); } });
  assert.equal(renames, 1); assert.deepEqual(result.files, ["a.json", "b.json"]); assert.deepEqual(await readdir(output), ["a.json", "b.json"]);
});

test("nested output, junction-like realpath drift and unknown staging entries fail closed", async (t) => {
  const root = await temporary(t); const trusted = await trustR22TemporaryRoot(root);
  await assert.rejects(publishR22Artifacts({ output: path.join(root, "nested", "result"), temporaryRoot: root, artifacts: new Map([["a.json", "{}"]]) }), /R22_CLI_ARGUMENT_INVALID/u);
  let injected = false;
  await assert.rejects(publishR22Artifacts({ output: path.join(root, "injected"), temporaryRoot: root, artifacts: new Map([["a.json", "{}"]]) }, {
    async openFile(file, flags) {
      const handle = await (await import("node:fs/promises")).open(file, flags);
      return { stat: (...args) => handle.stat(...args), writeFile: (...args) => handle.writeFile(...args), async sync() { await handle.sync(); if (!injected) { injected = true; await writeFile(path.join(path.dirname(file), "unexpected.bin"), "preserve"); } }, readFile: (...args) => handle.readFile(...args), close: () => handle.close() };
    },
  }), /R22_STAGING_CLEANUP_FAILED/u);
  assert.equal(trusted.path, root); await assert.rejects(stat(path.join(root, "injected")), /ENOENT/u);
  const stage = (await readdir(root)).find((name) => name.startsWith(".injected-")); assert.equal(typeof stage, "string"); assert.equal(await readFile(path.join(root, stage, "unexpected.bin"), "utf8"), "preserve");
});

test("post-effect rename error is accepted only when exact published bytes verify", async (t) => {
  const root = await temporary(t); const output = path.join(root, "recovered");
  await publishR22Artifacts({ output, temporaryRoot: root, artifacts: new Map([["a.json", "{}"]]) }, { async rename(from, to) { await (await import("node:fs/promises")).rename(from, to); throw new Error("post-effect"); } });
  assert.equal(await readFile(path.join(output, "a.json"), "utf8"), "{}");
  const conflict = path.join(root, "conflict");
  await assert.rejects(publishR22Artifacts({ output: conflict, temporaryRoot: root, artifacts: new Map([["a.json", "{}"]]) }, { async rename(_from, to) { await mkdir(to); await writeFile(path.join(to, "sentinel.txt"), "competitor"); throw new Error("pre-effect"); } }), /R22_STAGING_CLEANUP_FAILED|pre-effect/u);
  assert.equal(await readFile(path.join(conflict, "sentinel.txt"), "utf8"), "competitor");
});

test("a post-publication source failure removes the exact just-published result", async (t) => {
  const root = await temporary(t); const output = path.join(root, "rollback");
  await assert.rejects(publishR22Artifacts({ output, temporaryRoot: root, artifacts: new Map([["a.json", "{}"]]), afterRename: async () => { throw new Error("source-drift"); } }), /source-drift/u);
  await assert.rejects(stat(output), /ENOENT/u);
});

test("owned qualification scratch directories are identity-bound and removed recursively", async (t) => {
  const root = await temporary(t);
  const owned = await createR22OwnedTemporaryDirectory(root, "r22-qualification");
  await mkdir(path.join(owned.path, "nested"));
  await writeFile(path.join(owned.path, "nested", "receipt.json"), "{}");
  await removeR22OwnedTemporaryDirectory(owned.handle);
  await assert.rejects(stat(owned.path), /ENOENT/u);
  await assert.rejects(removeR22OwnedTemporaryDirectory(owned.handle), /R22_PATH_IDENTITY_INVALID/u);
});

test("owned qualification scratch cleanup fails closed after directory identity replacement", async (t) => {
  const root = await temporary(t);
  const owned = await createR22OwnedTemporaryDirectory(root, "r22-qualification");
  await rm(owned.path, { recursive: true, force: false });
  await mkdir(owned.path);
  await writeFile(path.join(owned.path, "user-owned.txt"), "preserve");
  await assert.rejects(removeR22OwnedTemporaryDirectory(owned.handle), /R22_DIRECTORY_IDENTITY_INVALID|R22_PATH_IDENTITY_INVALID/u);
  assert.equal(await readFile(path.join(owned.path, "user-owned.txt"), "utf8"), "preserve");
});

test("preview and capture require an explicit fixed-source case spec", async () => {
  await assert.rejects(
    runR22Preview(["--qualified-root", path.join(TEMPORARY_ROOT, "r22-qualified-without-source-binding")]),
    /R22_PREVIEW_ARGUMENT_INVALID/u,
  );
  await assert.rejects(
    runR22Capture([
      "--qualified-root", path.join(TEMPORARY_ROOT, "r22-qualified-without-source-binding"),
      "--output", path.join(TEMPORARY_ROOT, "r22-capture-without-source-binding"),
    ]),
    /R22_CLI_ARGUMENT_INVALID/u,
  );
});
