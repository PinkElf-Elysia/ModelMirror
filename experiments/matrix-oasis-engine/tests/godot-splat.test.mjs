import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  GDGS_COMMIT,
  GDGS_IMPORT_MARKER,
  GDGS_TAG_OBJECT,
  GDGS_TREE_SHA256,
  GdgsVerificationError,
  configureGdgsProject,
  parseGdgsImportProbe,
  verifyGdgsVendor,
} from "../scripts/verify-godot-splat.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

test("the original gdgs v3.3.0 tree and MIT license match the exact dual Git identity", async () => {
  const result = await verifyGdgsVendor(moduleRoot);
  assert.equal(result.tagObject, GDGS_TAG_OBJECT);
  assert.equal(result.commit, GDGS_COMMIT);
  assert.equal(result.sha256, GDGS_TREE_SHA256);
  assert.equal(result.fileCount, 73);
  assert.equal(result.byteLength, 429070);
});

test("a disposable project enables only the locked addon and fixes Compute explicitly", async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "matrix-oasis-gdgs-project-"));
  try {
    await fs.writeFile(path.join(root, "project.godot"), '[editor_plugins]\n\nenabled=PackedStringArray("res://addons/gdUnit4/plugin.cfg")\n', "utf8");
    configureGdgsProject(root);
    const configured = await fs.readFile(path.join(root, "project.godot"), "utf8");
    assert.match(configured, /gdUnit4\/plugin\.cfg", "res:\/\/addons\/gdgs\/plugin\.cfg/u);
    assert.match(configured, /\[gdgs\]\n\nrendering\/backend="Compute"\n$/u);
    assert.equal(configured.includes('rendering/backend="Raster"'), false);
  } finally {
    await fs.rm(root, { recursive: true });
  }
});

test("the import marker has one exact frozen public report", () => {
  const report = parseGdgsImportProbe(`${GDGS_IMPORT_MARKER}{"configuredBackend":"Compute","format":"compressed-ply","pointCount":3,"probeVersion":1}\n`);
  assert.deepEqual(report, { configuredBackend: "Compute", format: "compressed-ply", pointCount: 3, probeVersion: 1 });
  assert.throws(
    () => parseGdgsImportProbe(`${GDGS_IMPORT_MARKER}{"configuredBackend":"Raster","format":"compressed-ply","pointCount":3,"probeVersion":1}\n`),
    (error) => error instanceof GdgsVerificationError && error.code === "GDGS_IMPORT_REPORT_INVALID",
  );
});

test("the first-party guard rejects headless, missing, and fallback renderers", async () => {
  const guard = await fs.readFile(path.join(moduleRoot, "apps", "runtime-godot", "spatial_prototype", "spatial_splat_guard.gd"), "utf8");
  assert.match(guard, /DisplayServer\.get_name\(\) == "headless"/u);
  assert.match(guard, /str\(ProjectSettings\.get_setting\("gdgs\/rendering\/backend", ""\)\) != REQUIRED_BACKEND/u);
  assert.match(guard, /get_display_name/u);
  assert.match(guard, /!= REQUIRED_BACKEND/u);
  assert.equal(guard.includes("Raster"), false);
});

test("vendored bytes stay opaque while first-party spatial source stays offline", async () => {
  const attributes = await fs.readFile(path.join(moduleRoot, ".gitattributes"), "utf8");
  assert.match(attributes, /^apps\/runtime-godot\/addons\/gdgs\/\*\* -text -whitespace$/mu);
  const root = path.join(moduleRoot, "apps", "runtime-godot", "spatial_prototype");
  const sources = (await fs.readdir(root)).filter((name) => name.endsWith(".gd"));
  const text = (await Promise.all(sources.map((name) => fs.readFile(path.join(root, name), "utf8")))).join("\n");
  for (const forbidden of ["HTTPClient", "PacketPeer", "OS.execute", "get_environment", "FileAccess.open", "ResourceSaver.save"]) {
    assert.equal(text.includes(forbidden), false);
  }
  assert.equal(text.includes("environment-panorama.png"), false);
});
