import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { verifyR17References } from "../scripts/lib/r17-reference-core.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "matrix-oasis-r17-reference-"));
  for (const relative of ["third-party/v2-qualification-references", "third-party/spatial-layout-references/LICENSES"]) {
    fs.mkdirSync(path.join(root, relative), { recursive: true });
  }
  fs.cpSync(path.join(moduleRoot, "third-party/v2-qualification-references"), path.join(root, "third-party/v2-qualification-references"), { recursive: true, force: true, verbatimSymlinks: true });
  fs.copyFileSync(path.join(moduleRoot, "third-party/spatial-layout-references/reference.lock.json"), path.join(root, "third-party/spatial-layout-references/reference.lock.json"));
  fs.copyFileSync(path.join(moduleRoot, "third-party/spatial-layout-references/LICENSES/Apache-2.0.txt"), path.join(root, "third-party/spatial-layout-references/LICENSES/Apache-2.0.txt"));
  return root;
}

function expectCode(fn, code) {
  assert.throws(fn, (error) => error?.code === code && error.message === code);
}

test("R17 source, license, archive and note locks pass the exact reference gate", () => {
  assert.deepEqual(verifyR17References(moduleRoot), {
    ok: true,
    profile: "matrix-oasis.v2-qualification-references/1",
    executableCandidates: 5,
    architectureReferences: 8,
    animationFixtures: 1,
    localPayloadsChecked: 17,
    files: 17,
  });
});

test("unknown executable payload is rejected", () => {
  const root = fixture();
  fs.writeFileSync(path.join(root, "third-party/v2-qualification-references/runner.py"), "raise SystemExit\n");
  expectCode(() => verifyR17References(root), "R17_REFERENCE_FILE_SET_DRIFT");
});

test("an adaptation note byte drift is rejected", () => {
  const root = fixture();
  fs.appendFileSync(path.join(root, "third-party/v2-qualification-references/worldx.reference.txt"), "drift\n");
  expectCode(() => verifyR17References(root), "R17_REFERENCE_BYTE_DRIFT");
});

test("commit or license lock drift is rejected before it can become evidence", () => {
  const root = fixture();
  const lockPath = path.join(root, "third-party/v2-qualification-references/reference.lock.json");
  const lock = JSON.parse(fs.readFileSync(lockPath, "utf8"));
  lock.executableCandidates[0].commit = "0".repeat(40);
  fs.writeFileSync(lockPath, `${JSON.stringify(lock, null, 2)}\n`);
  expectCode(() => verifyR17References(root), "R17_REFERENCE_LOCK_DRIFT");
});

test("the Kenney page/archive version and clip mismatch remains fail-closed", () => {
  const lock = JSON.parse(fs.readFileSync(path.join(moduleRoot, "third-party/v2-qualification-references/reference.lock.json"), "utf8"));
  const fixtureLock = lock.animationFixtures[0];
  assert.equal(fixtureLock.expectedVersion, "1.0");
  assert.equal(fixtureLock.downloadedArchiveReportedVersion, "1.1");
  assert.equal(fixtureLock.sourceStatus, "deferred-version-and-clip-drift");
  assert.deepEqual(fixtureLock.missingRequiredClips, ["walk", "turn"]);
});

test("Godogen and GameCraft-Bench are reused by frozen R13 lock identity", () => {
  const lock = JSON.parse(fs.readFileSync(path.join(moduleRoot, "third-party/v2-qualification-references/reference.lock.json"), "utf8"));
  assert.deepEqual(lock.r13ReusedReferences.names, ["Godogen", "GameCraft-Bench"]);
  assert.match(lock.r13ReusedReferences.lockSha256, /^[0-9a-f]{64}$/u);
});
