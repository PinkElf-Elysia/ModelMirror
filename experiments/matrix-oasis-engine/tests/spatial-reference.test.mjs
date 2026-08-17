import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { verifySpatialReferences } from "../scripts/lib/spatial-reference-core.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = path.join(moduleRoot, "third-party", "spatial-layout-references");

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "matrix-oasis-r13-reference-"));
  const destination = path.join(root, "third-party", "spatial-layout-references");
  fs.mkdirSync(path.dirname(destination), { recursive: true });
  fs.cpSync(sourceRoot, destination, { recursive: true, verbatimSymlinks: true });
  return root;
}

function expectCode(fn, code) {
  assert.throws(fn, (error) => error?.code === code && error.message === code);
}

test("pinned spatial references pass the exact non-executable source gate", () => {
  const result = verifySpatialReferences(moduleRoot);
  assert.deepEqual(result, { ok: true, profile: "matrix-oasis.spatial-layout-references/1", references: 4, files: 8, checkedPayloads: 6 });
  assert.equal(Object.isFrozen(result), true);
});

test("the reference manifest locks exact commits, source blobs, licenses and runtime policy", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(sourceRoot, "reference.lock.json"), "utf8"));
  assert.deepEqual(manifest.references.map((item) => item.name), ["Godogen", "Holodeck", "ProcTHOR", "GameCraft-Bench"]);
  assert.equal(manifest.runtimeDependency, false);
  assert.equal(manifest.executable, false);
  for (const reference of manifest.references) {
    assert.match(reference.commit, /^[0-9a-f]{40}$/u);
    assert.match(reference.notePath, /\.reference\.txt$/u);
    assert.doesNotMatch(reference.notePath, /\.(?:py|cs|gd|js|mjs)$/u);
    for (const source of reference.upstreamFiles) {
      assert.match(source.gitBlobSha1, /^[0-9a-f]{40}$/u);
      assert.match(source.sha256, /^[0-9a-f]{64}$/u);
    }
  }
});

test("local note byte drift is rejected", () => {
  const root = fixture();
  fs.appendFileSync(path.join(root, "third-party", "spatial-layout-references", "godogen.reference.txt"), "drift\n");
  expectCode(() => verifySpatialReferences(root), "SPATIAL_REFERENCE_BYTE_DRIFT");
});

test("an unknown executable reference file is rejected", () => {
  const root = fixture();
  fs.writeFileSync(path.join(root, "third-party", "spatial-layout-references", "solver.py"), "raise SystemExit\n");
  expectCode(() => verifySpatialReferences(root), "SPATIAL_REFERENCE_FILE_SET_DRIFT");
});

test("manifest commit drift is rejected even when local files are unchanged", () => {
  const root = fixture();
  const manifestPath = path.join(root, "third-party", "spatial-layout-references", "reference.lock.json");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  manifest.references[0].commit = "0".repeat(40);
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
  expectCode(() => verifySpatialReferences(root), "SPATIAL_REFERENCE_UPSTREAM_DRIFT");
});

test("a missing license is rejected before references can be accepted", () => {
  const root = fixture();
  fs.rmSync(path.join(root, "third-party", "spatial-layout-references", "LICENSES", "Apache-2.0.txt"));
  expectCode(() => verifySpatialReferences(root), "SPATIAL_REFERENCE_FILE_SET_DRIFT");
});
