import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import {
  materializePrototypeAssetBundle,
  planPrototypeAssets,
  validatePrototypeAssetBundleJson,
} from "../src/index.mjs";
import {
  inspectPrototypeGlb,
  normalizePrototypeGlb,
} from "../src/glb-normalizer.mjs";

const fixtureRoot = new URL("../../../", import.meta.url);

async function fixtureBytes(name) {
  return readFile(new URL(`examples/scene-bundles/kenney-prototype/assets/${name}`, fixtureRoot));
}

async function prototypeInputs() {
  const authoringValue = JSON.parse(await readFile(
    new URL("examples/mechanics-conformance.authoring-game-pack.json", fixtureRoot),
    "utf8",
  ));
  const authoringGamePackJson = canonicalizeJsonValue(authoringValue);
  const compiled = await compileAuthoringGamePackJson(authoringGamePackJson);
  assert.equal(compiled.ok, true);
  const placementIds = ["place-room", "place-crate", "place-guide"];
  const sceneBlueprint = {
    format: "matrix-oasis.scene-blueprint",
    formatVersion: "0.1.0",
    scene: {
      id: authoringValue.id,
      contentVersion: authoringValue.contentVersion,
      title: authoringValue.title,
      environmentPrompt: "A neutral enclosed prototype room.",
      visualStylePrompt: "Low-detail neutral gray validation geometry.",
    },
    zones: [{ id: "zone-main", label: "Main", description: "Validation zone." }],
    assetBriefs: [
      { id: "room", kind: "environment", prompt: "A neutral enclosed prototype room.", entityId: null, roles: ["visual", "collider"] },
      { id: "crate", kind: "prop", prompt: "A simple wooden crate with no text.", entityId: "control-unit", roles: ["visual", "collider"] },
      { id: "guide", kind: "character-placeholder", prompt: "A neutral static human-shaped placeholder with no accessories.", entityId: "actor-unit", roles: ["visual"] },
    ],
    placements: [
      { id: placementIds[0], assetBriefId: "room", zoneId: "zone-main", entityId: null },
      { id: placementIds[1], assetBriefId: "crate", zoneId: "zone-main", entityId: "control-unit" },
      { id: placementIds[2], assetBriefId: "guide", zoneId: "zone-main", entityId: "actor-unit" },
    ],
    nodeBindings: authoringValue.nodes.map((node) => ({ nodeId: node.id, zoneId: "zone-main", visiblePlacementIds: placementIds })),
  };
  return {
    authoringGamePackJson,
    sceneBlueprintJson: canonicalizeJsonValue(sceneBlueprint),
    runtimeGamePackJson: compiled.canonicalJson,
    runtimeReceiptJson: canonicalizeJsonValue(compiled.receipt),
  };
}

async function materializationInputs() {
  const plan = await planPrototypeAssets(await prototypeInputs());
  assert.equal(plan.ok, true, JSON.stringify(plan));
  const sourceCrate = await fixtureBytes("crate.glb");
  const texture = await fixtureBytes("Textures/colormap.png");
  const embedded = await normalizePrototypeGlb(sourceCrate, {
    kind: "prop",
    role: "visual",
    externalResources: new Map([["Textures/colormap.png", texture]]),
  });
  assert.equal(embedded.ok, true);
  return {
    plan,
    acquiredAssets: new Map([["crate", embedded.bytes], ["guide", embedded.bytes]]),
    environmentAssets: new Map([
      ["floor-square", await fixtureBytes("floor-square.glb")],
      ["wall", await fixtureBytes("wall.glb")],
    ]),
    environmentTexture: await fixtureBytes("Textures/colormap.png"),
  };
}

function rewriteGlbJson(bytes, mutate) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const jsonLength = view.getUint32(12, true);
  const json = JSON.parse(new TextDecoder().decode(bytes.subarray(20, 20 + jsonLength)).trim());
  mutate(json);
  const encoded = new TextEncoder().encode(JSON.stringify(json));
  const paddedLength = Math.ceil(encoded.length / 4) * 4;
  const oldBinHeader = 20 + jsonLength;
  const binLength = view.getUint32(oldBinHeader, true);
  const bin = bytes.subarray(oldBinHeader + 8, oldBinHeader + 8 + binLength);
  const output = new Uint8Array(20 + paddedLength + 8 + binLength);
  const target = new DataView(output.buffer);
  target.setUint32(0, 0x46546c67, true);
  target.setUint32(4, 2, true);
  target.setUint32(8, output.length, true);
  target.setUint32(12, paddedLength, true);
  target.setUint32(16, 0x4e4f534a, true);
  output.fill(0x20, 20, 20 + paddedLength);
  output.set(encoded, 20);
  const targetBinHeader = 20 + paddedLength;
  target.setUint32(targetBinHeader, binLength, true);
  target.setUint32(targetBinHeader + 4, 0x004e4942, true);
  output.set(bin, targetBinHeader + 8);
  return output;
}

test("R9 pipeline exports the approved interfaces and provider guard surface", async () => {
  const api = await import("../src/index.mjs");
  assert.deepEqual(Object.keys(api).sort(), [
    "MESHY_PROVIDER_ENDPOINT", "MESHY_PROVIDER_LIMITS", "MESHY_PROVIDER_MODEL",
    "PrototypeAssetPipelineOperationalError", "createMeshyTextTo3DProvider",
    "materializePrototypeAssetBundle", "planPrototypeAssets",
    "validatePrototypeAssetBundleJson",
  ].sort());
});

test("asset planning validates canonical R8 and Runtime identities", async () => {
  const inputs = await prototypeInputs();
  const result = await planPrototypeAssets(inputs);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(Object.isFrozen(result), true);
  assert.deepEqual(result.plan.blueprint.assetBriefs.map((brief) => brief.id), ["room", "crate", "guide"]);
  assert.equal(result.plan.runtimeIdentity.id, "mechanics-conformance");
  const changed = { ...inputs, sceneBlueprintJson: `${inputs.sceneBlueprintJson}\n` };
  assert.equal((await planPrototypeAssets(changed)).diagnostics[0].code, "PROTOTYPE_ASSET_PLAN_INPUT_INVALID");
  const changedAuthoring = JSON.parse(inputs.authoringGamePackJson);
  changedAuthoring.title = "Changed without recompiling";
  assert.equal(
    (await planPrototypeAssets({
      ...inputs,
      authoringGamePackJson: canonicalizeJsonValue(changedAuthoring),
    })).diagnostics[0].code,
    "PROTOTYPE_ASSET_PLAN_IDENTITY_MISMATCH",
  );
  const runtime = JSON.parse(inputs.runtimeGamePackJson);
  runtime.source.id = "mismatched";
  assert.equal((await planPrototypeAssets({ ...inputs, runtimeGamePackJson: canonicalizeJsonValue(runtime) })).ok, false);
});

test("GLB gate rejects unsupported features, resources, and malformed containers", async () => {
  const crate = await fixtureBytes("crate.glb");
  const texture = await fixtureBytes("Textures/colormap.png");
  const external = new Map([["Textures/colormap.png", texture]]);
  assert.deepEqual(inspectPrototypeGlb(crate, external), { ok: true, triangles: 204, surfaces: 1 });
  const invalidHeader = new Uint8Array(crate);
  invalidHeader[0] = 0;
  assert.equal(inspectPrototypeGlb(invalidHeader, external).code, "PROTOTYPE_ASSET_GLB_INVALID");
  for (const [mutate, code] of [
    [(json) => { json.animations = [{}]; }, "PROTOTYPE_ASSET_GLB_FEATURE_UNSUPPORTED"],
    [(json) => { json.skins = [{}]; }, "PROTOTYPE_ASSET_GLB_FEATURE_UNSUPPORTED"],
    [(json) => { json.cameras = [{}]; }, "PROTOTYPE_ASSET_GLB_FEATURE_UNSUPPORTED"],
    [(json) => { json.extensionsRequired = ["KHR_draco_mesh_compression"]; }, "PROTOTYPE_ASSET_GLB_FEATURE_UNSUPPORTED"],
    [(json) => { json.extensionsUsed = ["EXT_meshopt_compression"]; }, "PROTOTYPE_ASSET_GLB_EXTENSION_UNSUPPORTED"],
    [(json) => { json.images[0].uri = "outside.png"; }, "PROTOTYPE_ASSET_GLB_EXTERNAL_URI"],
  ]) assert.equal(inspectPrototypeGlb(rewriteGlbJson(crate, mutate), external).code, code);
});

test("normalization embeds textures, strips collider appearance, and is byte deterministic", async () => {
  const crate = await fixtureBytes("crate.glb");
  const texture = await fixtureBytes("Textures/colormap.png");
  const externalResources = new Map([["Textures/colormap.png", texture]]);
  const visualRuns = [];
  for (let index = 0; index < 20; index += 1) {
    const result = await normalizePrototypeGlb(crate, { kind: "prop", role: "visual", externalResources });
    assert.equal(result.ok, true);
    visualRuns.push(Buffer.from(result.bytes).toString("base64"));
    assert.deepEqual(result.metrics.boundsMm, { min: [-500, 0, -500], max: [500, 1000, 500] });
    assert.equal(result.metrics.maxTextureWidth, 512);
    assert.equal(result.metrics.maxTextureHeight, 512);
    assert.equal(inspectPrototypeGlb(result.bytes).ok, true);
  }
  assert.equal(new Set(visualRuns).size, 1);
  const collider = await normalizePrototypeGlb(crate, { kind: "prop", role: "collider", externalResources });
  assert.equal(collider.ok, true);
  assert.equal(collider.metrics.maxTextureWidth, 0);
  assert.equal(collider.metrics.triangleCount <= 10_000, true);
  const colliderDocument = await new NodeIO().registerExtensions(ALL_EXTENSIONS).readBinary(collider.bytes);
  for (const mesh of colliderDocument.getRoot().listMeshes()) {
    for (const primitive of mesh.listPrimitives()) {
      assert.deepEqual(primitive.listSemantics(), ["POSITION"]);
    }
  }
  assert.deepEqual([...crate], [...await fixtureBytes("crate.glb")]);
});

test("materialization produces only canonical manifest, report, and embedded GLBs", async () => {
  const result = await materializePrototypeAssetBundle(await materializationInputs());
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(validatePrototypeAssetBundleJson(result.canonicalBundleJson).valid, true);
  assert.equal(result.files.length, 5);
  assert.deepEqual(result.files.map((file) => file.path), [
    "assets/room-floor-square.glb", "assets/room-wall.glb", "assets/crate-visual.glb",
    "assets/crate-collider.glb", "assets/guide-visual.glb",
  ]);
  assert.equal(result.canonicalBundleJson.includes("prompt"), false);
  assert.equal(result.canonicalReportJson.includes("task"), false);
  assert.equal(result.canonicalReportJson.includes("url"), false);
  for (const file of result.files) assert.equal(inspectPrototypeGlb(file.bytes).ok, true);
  const second = await materializePrototypeAssetBundle(await materializationInputs());
  assert.equal(second.ok, true);
  assert.equal(second.canonicalBundleJson, result.canonicalBundleJson);
  assert.equal(second.canonicalReportJson, result.canonicalReportJson);
  assert.deepEqual(second.files.map((file) => Buffer.from(file.bytes).toString("base64")), result.files.map((file) => Buffer.from(file.bytes).toString("base64")));
});

test("materialization rejects missing, extra, or malformed acquired assets atomically", async () => {
  const request = await materializationInputs();
  for (const acquiredAssets of [
    new Map([["crate", request.acquiredAssets.get("crate")]]),
    new Map([...request.acquiredAssets, ["extra", request.acquiredAssets.get("crate")]]),
    new Map([...request.acquiredAssets].map(([id]) => [id, new Uint8Array([1, 2, 3])])),
  ]) {
    const result = await materializePrototypeAssetBundle({ ...request, acquiredAssets });
    assert.equal(result.ok, false);
    assert.equal(Object.isFrozen(result), true);
  }
});

test("materialization accepts only an issued plan and bypasses overridden Map iteration", async () => {
  const request = await materializationInputs();
  const forged = await materializePrototypeAssetBundle({
    ...request,
    plan: { ok: true, plan: request.plan.plan },
  });
  assert.equal(forged.ok, false);
  assert.equal(forged.diagnostics[0].code, "PROTOTYPE_ASSET_MATERIALIZATION_REQUEST_INVALID");

  let iteratorCalls = 0;
  Object.defineProperty(request.acquiredAssets, Symbol.iterator, {
    configurable: true,
    get() {
      iteratorCalls += 1;
      throw new Error("untrusted iterator");
    },
  });
  const materialized = await materializePrototypeAssetBundle(request);
  assert.equal(materialized.ok, true);
  assert.equal(iteratorCalls, 0);
});
