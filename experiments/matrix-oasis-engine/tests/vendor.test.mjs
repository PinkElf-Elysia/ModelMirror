import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  GDUNIT4_COMMIT,
  GDUNIT4_SOURCE_ARCHIVE_SHA256,
  GODOT_DEMO_REFERENCE_COMMIT,
  GODOT_DEMO_REFERENCE_SHA256,
  VendorIntegrityError,
  computeVendorTree,
  verifyGodotDemoReference,
  verifyGodotVendor,
} from "../scripts/lib/vendor-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

async function readManifest(root = moduleRoot) {
  return JSON.parse(
    await fs.readFile(path.join(root, "third-party", "gdunit4.lock.json"), "utf8"),
  );
}

async function readReferenceManifest(root = moduleRoot) {
  return JSON.parse(
    await fs.readFile(
      path.join(root, "third-party", "godot-demo-projects", "reference.lock.json"),
      "utf8",
    ),
  );
}

test("the committed GdUnit4 tree matches the exact approved lock", async () => {
  const manifest = await readManifest();
  const tree = await verifyGodotVendor({ moduleRoot, manifest });
  assert.equal(manifest.commit, GDUNIT4_COMMIT);
  assert.equal(manifest.sourceArchiveSha256, GDUNIT4_SOURCE_ARCHIVE_SHA256);
  assert.deepEqual(tree, Object.freeze({ ...manifest.tree }));
});

test("the official Godot movement reference is exact and non-executable", async () => {
  const manifest = await readReferenceManifest();
  const result = await verifyGodotDemoReference({ moduleRoot, manifest });
  assert.equal(result.commit, GODOT_DEMO_REFERENCE_COMMIT);
  assert.equal(result.referenceSha256, GODOT_DEMO_REFERENCE_SHA256);
  assert.equal(result.referenceByteLength, 2303);
  assert.equal(manifest.runtimeDependency, false);
  assert.equal(manifest.executable, false);
  assert.match(manifest.referencePath, /\.reference\.txt$/);
});

test("Godot movement reference verification rejects drift and expanded manifests", async () => {
  const manifest = await readReferenceManifest();
  await assert.rejects(
    verifyGodotDemoReference({
      moduleRoot,
      manifest: { ...manifest, unexpected: true },
    }),
    (error) => error instanceof VendorIntegrityError &&
      error.code === "GODOT_DEMO_REFERENCE_MANIFEST_INVALID",
  );
  await assert.rejects(
    verifyGodotDemoReference({
      moduleRoot,
      manifest: { ...manifest, referenceSha256: "0".repeat(64) },
    }),
    (error) => error instanceof VendorIntegrityError &&
      error.code === "GODOT_DEMO_REFERENCE_MANIFEST_INVALID",
  );
});

test("Git preserves vendor bytes and scopes whitespace exceptions exactly", async () => {
  const attributes = await fs.readFile(path.join(moduleRoot, ".gitattributes"), "utf8");
  assert.match(
    attributes,
    /^apps\/runtime-godot\/addons\/gdUnit4\/\*\* -text -whitespace$/m,
  );
  const vendorAttribute = spawnSync(
    "git",
    ["check-attr", "text", "whitespace", "--", "apps/runtime-godot/addons/gdUnit4/test/core/command/GdUnitCommandTestSessionTest.gd"],
    { cwd: moduleRoot, encoding: "utf8", shell: false, windowsHide: true },
  );
  assert.equal(vendorAttribute.status, 0);
  assert.match(vendorAttribute.stdout, /text: unset/);
  assert.match(vendorAttribute.stdout, /whitespace: unset/);
  const firstPartyAttribute = spawnSync(
    "git",
    ["check-attr", "whitespace", "--", "apps/runtime-godot/test/test_foundation.gd"],
    { cwd: moduleRoot, encoding: "utf8", shell: false, windowsHide: true },
  );
  assert.equal(firstPartyAttribute.status, 0);
  assert.match(firstPartyAttribute.stdout, /whitespace: unspecified/);
});

test("tree hashing is path, byte, and order deterministic", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "matrix-oasis-vendor-tree-"));
  try {
    await fs.mkdir(path.join(root, "nested"));
    await fs.writeFile(path.join(root, "z.txt"), "z\n");
    await fs.writeFile(path.join(root, "nested", "a.txt"), "a\n");
    const first = await computeVendorTree(root);
    const second = await computeVendorTree(root);
    assert.deepEqual(first, second);
    await fs.writeFile(path.join(root, "nested", "a.txt"), "changed\n");
    assert.notEqual((await computeVendorTree(root)).sha256, first.sha256);
    await fs.writeFile(path.join(root, "nested", "a.txt"), "a\n");
    await fs.rename(path.join(root, "z.txt"), path.join(root, "y.txt"));
    assert.notEqual((await computeVendorTree(root)).sha256, first.sha256);
  } finally {
    await fs.rm(root, { recursive: true });
  }
});

test("vendor verification rejects additions, removals, and byte drift", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "matrix-oasis-vendor-fixture-"));
  try {
    const vendor = path.join(root, "apps", "runtime-godot", "addons", "gdUnit4");
    const license = path.join(root, "third-party", "gdunit4", "LICENSE");
    await fs.mkdir(vendor, { recursive: true });
    await fs.mkdir(path.dirname(license), { recursive: true });
    await fs.writeFile(path.join(vendor, "plugin.gd"), "extends EditorPlugin\n");
    await fs.writeFile(license, "MIT fixture\n");
    const realManifest = await readManifest();
    const tree = await computeVendorTree(vendor);
    const crypto = await import("node:crypto");
    const licenseSha256 = crypto.createHash("sha256").update(await fs.readFile(license)).digest("hex");
    const manifest = { ...realManifest, licenseSha256, tree: { ...tree } };
    await verifyGodotVendor({ moduleRoot: root, manifest });

    await fs.writeFile(path.join(vendor, "extra.gd"), "extends Node\n");
    await assert.rejects(
      verifyGodotVendor({ moduleRoot: root, manifest }),
      (error) => error instanceof VendorIntegrityError && error.code === "GDUNIT4_VENDOR_TREE_MISMATCH",
    );
    await fs.rm(path.join(vendor, "extra.gd"));
    await fs.writeFile(path.join(vendor, "plugin.gd"), "changed\n");
    await assert.rejects(
      verifyGodotVendor({ moduleRoot: root, manifest }),
      (error) => error instanceof VendorIntegrityError && error.code === "GDUNIT4_VENDOR_TREE_MISMATCH",
    );
    await fs.rm(path.join(vendor, "plugin.gd"));
    await assert.rejects(
      verifyGodotVendor({ moduleRoot: root, manifest }),
      (error) => error instanceof VendorIntegrityError && error.code === "GDUNIT4_VENDOR_TREE_MISMATCH",
    );
  } finally {
    await fs.rm(root, { recursive: true });
  }
});

test("vendor trees reject symbolic links", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "matrix-oasis-vendor-link-"));
  const external = await fs.mkdtemp(path.join(os.tmpdir(), "matrix-oasis-vendor-external-"));
  const link = path.join(root, "outside");
  try {
    await fs.writeFile(path.join(external, "outside.txt"), "outside\n");
    try {
      await fs.symlink(external, link, process.platform === "win32" ? "junction" : "dir");
    } catch (error) {
      if (error?.code === "EPERM" || error?.code === "EACCES") {
        t.skip("symbolic links are unavailable in this environment");
        return;
      }
      throw error;
    }
    await assert.rejects(
      computeVendorTree(root),
      (error) => error instanceof VendorIntegrityError && error.code === "GDUNIT4_VENDOR_SYMLINK_FORBIDDEN",
    );
  } finally {
    await fs.unlink(link).catch(() => {});
    await fs.rm(root, { recursive: true });
    await fs.rm(external, { recursive: true });
  }
});
