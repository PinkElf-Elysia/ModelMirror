import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import {
  validateGlbBuffer,
  validateSceneBundle,
} from "../scripts/lib/scene-pack-bundle-core.mjs";

const temporaryPrefix = "matrix-oasis-scene-bundle-";

function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function makeGlb(document) {
  const source = new TextEncoder().encode(JSON.stringify(document));
  const paddedLength = Math.ceil(source.length / 4) * 4;
  const bytes = new Uint8Array(20 + paddedLength);
  const view = new DataView(bytes.buffer);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, bytes.length, true);
  view.setUint32(12, paddedLength, true);
  view.setUint32(16, 0x4e4f534a, true);
  bytes.fill(0x20, 20);
  bytes.set(source, 20);
  return bytes;
}

function validGlb(overrides = {}) {
  return makeGlb({
    asset: { version: "2.0" },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
    accessors: [{ count: 3, type: "VEC3", componentType: 5126 }],
    ...overrides,
  });
}

async function createFixture() {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), temporaryPrefix));
  const source = await fs.readFile(
    new URL("../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url),
    "utf8",
  );
  const compiled = await compileAuthoringGamePackJson(source);
  assert.equal(compiled.ok, true);
  const runtimeText = compiled.canonicalJson;
  const receiptText = canonicalizeJsonValue(compiled.receipt);
  const assetBytes = validGlb();
  const scene = {
    format: "matrix-oasis.scene-pack",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    scene: { id: "bundle-fixture", contentVersion: "1.0.0", title: "Bundle fixture" },
    runtimeIdentity: {
      runtimeFormat: compiled.runtimePack.format,
      runtimeFormatVersion: compiled.runtimePack.formatVersion,
      packId: compiled.runtimePack.source.id,
      packContentVersion: compiled.runtimePack.source.contentVersion,
      sourceCanonicalSha256: compiled.runtimePack.source.canonicalSha256,
      artifactSha256: compiled.receipt.artifact.sha256,
    },
    assets: [{
      id: "fixture-asset",
      roles: ["visual", "collider"],
      path: "assets/fixture.glb",
      format: "glb",
      byteLength: assetBytes.length,
      sha256: sha256(assetBytes),
    }],
    placements: [{
      id: "fixture-placement",
      visualAssetId: "fixture-asset",
      colliderAssetId: "fixture-asset",
      entityId: null,
      transform: {
        positionMm: [0, 0, 0],
        rotationMilliDegrees: [0, 0, 0],
        scalePermille: [1000, 1000, 1000],
      },
    }],
    nodeBindings: compiled.runtimePack.nodes.map((node) => ({
      nodeId: node.id,
      playerSpawn: { positionMm: [0, 1000, 0], yawMilliDegrees: 0 },
      actionAnchor: { positionMm: [0, 0, -2000], yawMilliDegrees: 0 },
      visiblePlacementIds: ["fixture-placement"],
    })),
  };
  await fs.mkdir(path.join(root, "bundle", "assets"), { recursive: true });
  await Promise.all([
    fs.writeFile(path.join(root, "bundle", "scene.json"), canonicalizeJsonValue(scene)),
    fs.writeFile(path.join(root, "runtime.json"), runtimeText),
    fs.writeFile(path.join(root, "receipt.json"), receiptText),
    fs.writeFile(path.join(root, "bundle", "assets", "fixture.glb"), assetBytes),
  ]);
  return { root, scene, assetBytes };
}

async function removeFixture(root) {
  const temporaryRoot = path.resolve(os.tmpdir());
  const resolved = path.resolve(root);
  assert.equal(path.basename(resolved).startsWith(temporaryPrefix), true);
  assert.equal(resolved.startsWith(`${temporaryRoot}${path.sep}`), true);
  await fs.rm(resolved, { recursive: true, force: true });
}

function validate(root, overrides = {}) {
  return validateSceneBundle({
    moduleRoot: root,
    scenePath: "bundle/scene.json",
    runtimePackPath: "runtime.json",
    runtimeReceiptPath: "receipt.json",
    ...overrides,
  });
}

test("validates one contained canonical Scene bundle", async () => {
  const fixture = await createFixture();
  try {
    const result = await validate(fixture.root);
    assert.deepEqual(result, { reportVersion: 1, valid: true, diagnostics: [] });
    assert.equal(Object.isFrozen(result), true);
    assert.equal(Object.isFrozen(result.diagnostics), true);
  } finally {
    await removeFixture(fixture.root);
  }
});

test("rejects absolute, traversal and missing input paths as static content diagnostics", async () => {
  const fixture = await createFixture();
  try {
    const absolute = await validate(fixture.root, {
      scenePath: path.join(fixture.root, "bundle", "scene.json"),
    });
    assert.equal(absolute.diagnostics[0].code, "SCENE_PACK_INPUT_PATH_INVALID");
    const traversal = await validate(fixture.root, { scenePath: "../outside.json" });
    assert.equal(traversal.diagnostics[0].code, "SCENE_PACK_INPUT_PATH_INVALID");
    const missing = await validate(fixture.root, { scenePath: "missing.json" });
    assert.equal(missing.diagnostics[0].code, "SCENE_PACK_INPUT_FILE_INVALID");
  } finally {
    await removeFixture(fixture.root);
  }
});

test("rejects asset byte length, hash and missing-file failures without path disclosure", async () => {
  const fixture = await createFixture();
  try {
    const changed = structuredClone(fixture.scene);
    changed.assets[0].byteLength += 1;
    changed.assets[0].sha256 = "f".repeat(64);
    await fs.writeFile(path.join(fixture.root, "bundle", "scene.json"), canonicalizeJsonValue(changed));
    let result = await validate(fixture.root);
    assert.deepEqual(new Set(result.diagnostics.map(({ code }) => code)), new Set([
      "SCENE_PACK_ASSET_BYTE_LENGTH_MISMATCH",
      "SCENE_PACK_ASSET_SHA256_MISMATCH",
    ]));
    assert.equal(JSON.stringify(result).includes(fixture.root), false);

    await fs.rm(path.join(fixture.root, "bundle", "assets", "fixture.glb"));
    result = await validate(fixture.root);
    assert.equal(result.diagnostics.some(({ code }) => code === "SCENE_PACK_ASSET_FILE_INVALID"), true);
  } finally {
    await removeFixture(fixture.root);
  }
});

test("GLB gate accepts version 2, permits only the approved Kenney texture edge, and rejects other external URIs", async () => {
  assert.equal(validateGlbBuffer(validGlb()).ok, true);
  const invalidHeader = validGlb();
  invalidHeader[0] = 0;
  assert.equal(validateGlbBuffer(invalidHeader).code, "SCENE_PACK_GLB_INVALID");
  assert.equal(
    validateGlbBuffer(validGlb({ buffers: [{ uri: "outside.bin", byteLength: 0 }] })).code,
    "SCENE_PACK_GLB_EXTERNAL_URI",
  );
  const kenney = await fs.readFile(
    new URL("../examples/scene-bundles/kenney-prototype/assets/crate.glb", import.meta.url),
  );
  assert.equal(validateGlbBuffer(kenney).ok, true);
  const figurine = await fs.readFile(
    new URL("../examples/scene-bundles/kenney-prototype/assets/figurine.glb", import.meta.url),
  );
  assert.equal(validateGlbBuffer(figurine).ok, true);
  assert.equal(
    validateGlbBuffer(validGlb({images: [{uri: "Textures/colormap.png"}]})).code,
    "SCENE_PACK_GLB_EXTERNAL_URI",
  );
  assert.equal(
    validateGlbBuffer(validGlb({images: [{uri: "data:image/png;base64,AA=="}]})).code,
    "SCENE_PACK_GLB_EXTERNAL_URI",
  );
  assert.equal(
    validateGlbBuffer(validGlb({buffers: [{uri: "Textures/colormap.png", byteLength: 0}]})).code,
    "SCENE_PACK_GLB_EXTERNAL_URI",
  );
  assert.equal(
    validateGlbBuffer(validGlb({ animations: [{}] })).code,
    "SCENE_PACK_GLB_FEATURE_UNSUPPORTED",
  );
  assert.equal(
    validateGlbBuffer(validGlb({ cameras: [{ type: "perspective" }] })).code,
    "SCENE_PACK_GLB_FEATURE_UNSUPPORTED",
  );
  assert.equal(
    validateGlbBuffer(validGlb({ buffers: [{ byteLength: 4 }] })).code,
    "SCENE_PACK_GLB_INVALID",
  );
  assert.equal(
    validateGlbBuffer(validGlb({ extensions: { KHR_lights_punctual: { lights: [] } } })).code,
    "SCENE_PACK_GLB_FEATURE_UNSUPPORTED",
  );
});

test("external asset junction is rejected before its bytes are accepted", async () => {
  const fixture = await createFixture();
  const outside = path.join(fixture.root, "outside");
  try {
    await fs.mkdir(outside);
    await fs.writeFile(path.join(outside, "fixture.glb"), fixture.assetBytes);
    await fs.symlink(
      outside,
      path.join(fixture.root, "bundle", "linked"),
      process.platform === "win32" ? "junction" : "dir",
    );
    const changed = structuredClone(fixture.scene);
    changed.assets[0].path = "linked/fixture.glb";
    await fs.writeFile(path.join(fixture.root, "bundle", "scene.json"), canonicalizeJsonValue(changed));
    const result = await validate(fixture.root);
    assert.equal(result.diagnostics[0].code, "SCENE_PACK_ASSET_PATH_INVALID");
  } finally {
    await removeFixture(fixture.root);
  }
});
