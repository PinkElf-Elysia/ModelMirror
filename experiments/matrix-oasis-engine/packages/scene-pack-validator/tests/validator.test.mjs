import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { validateScenePackJson } from "../src/index.mjs";

async function fixture() {
  const source = await readFile(new URL("../../../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url), "utf8");
  const compiled = await compileAuthoringGamePackJson(source); assert.equal(compiled.ok, true);
  const runtimeText = compiled.canonicalJson; const receiptText = canonicalizeJsonValue(compiled.receipt);
  const runtime = compiled.runtimePack;
  const scene = {format: "matrix-oasis.scene-pack", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1", scene: {id: "mechanics-scene", contentVersion: "1.0.0", title: "Mechanics Scene"}, runtimeIdentity: {runtimeFormat: runtime.format, runtimeFormatVersion: runtime.formatVersion, packId: runtime.source.id, packContentVersion: runtime.source.contentVersion, sourceCanonicalSha256: runtime.source.canonicalSha256, artifactSha256: compiled.receipt.artifact.sha256}, assets: [], placements: [], nodeBindings: runtime.nodes.map((node, index) => ({nodeId: node.id, playerSpawn: {positionMm: [index * 1000, 1000, 0], yawMilliDegrees: 0}, actionAnchor: {positionMm: [index * 1000, 0, -2000], yawMilliDegrees: 0}, visiblePlacementIds: []}))};
  return {scene, sceneText: canonicalizeJsonValue(scene), runtimeText, receiptText};
}

test("validates one canonical Scene Pack against frozen Runtime identity", async () => {
  const value = await fixture(); const result = await validateScenePackJson(value.sceneText, value.runtimeText, value.receiptText);
  assert.deepEqual(result, {reportVersion: 1, valid: true, diagnostics: []}); assert.ok(Object.isFrozen(result)); assert.ok(Object.isFrozen(result.diagnostics));
});

test("rejects non-canonical, identity mismatch, references and missing node bindings", async () => {
  const value = await fixture();
  let result = await validateScenePackJson(`${value.sceneText}\n`, value.runtimeText, value.receiptText);
  assert.equal(result.valid, false); assert.equal(result.diagnostics[0].code, "SCENE_PACK_JSON_NON_CANONICAL");
  const mismatch = structuredClone(value.scene); mismatch.runtimeIdentity.artifactSha256 = "f".repeat(64);
  result = await validateScenePackJson(canonicalizeJsonValue(mismatch), value.runtimeText, value.receiptText);
  assert.equal(result.diagnostics[0].code, "SCENE_PACK_RUNTIME_IDENTITY_MISMATCH");
  const invalid = structuredClone(value.scene); invalid.assets = [{id: "visual", roles: ["visual"], path: "assets/visual.glb", format: "glb", byteLength: 12, sha256: "a".repeat(64)}]; invalid.placements = [{id: "placed", visualAssetId: "missing", colliderAssetId: "visual", entityId: "missing", transform: {positionMm: [0, 0, 0], rotationMilliDegrees: [0, 0, 0], scalePermille: [1000, 1000, 1000]}}]; invalid.nodeBindings = invalid.nodeBindings.slice(1); invalid.nodeBindings[0].visiblePlacementIds = ["missing"];
  result = await validateScenePackJson(canonicalizeJsonValue(invalid), value.runtimeText, value.receiptText);
  assert.deepEqual(new Set(result.diagnostics.map((item) => item.code)), new Set(["SCENE_PACK_VISUAL_ASSET_REFERENCE_INVALID", "SCENE_PACK_COLLIDER_ASSET_REFERENCE_INVALID", "SCENE_PACK_ENTITY_REFERENCE_NOT_FOUND", "SCENE_PACK_PLACEMENT_REFERENCE_NOT_FOUND", "SCENE_PACK_NODE_BINDING_MISSING"]));
});

test("rejects duplicate and unknown keys without exposing unknown property names", async () => {
  const value = await fixture();
  const duplicated = value.sceneText.replace('{"assets"', '{"secret-sentinel":1,"secret-sentinel":2,"assets"');
  const result = await validateScenePackJson(duplicated, value.runtimeText, value.receiptText);
  assert.equal(result.valid, false); assert.equal(result.diagnostics[0].code, "SCENE_PACK_JSON_DUPLICATE_KEY"); assert.equal(JSON.stringify(result).includes("secret-sentinel"), false);
});

test("rejects isolated surrogate text and schema expansion", async () => {
  const value = await fixture(); const bad = structuredClone(value.scene); bad.scene.title = String.fromCharCode(0xd800);
  let result = await validateScenePackJson(canonicalizeJsonValue(bad), value.runtimeText, value.receiptText);
  assert.equal(result.diagnostics[0].code, "SCENE_PACK_UNSUPPORTED_TEXT");
  result = await validateScenePackJson(JSON.stringify({...value.scene, extra: true}), value.runtimeText, value.receiptText);
  assert.equal(result.diagnostics.some((item) => item.code === "SCENE_PACK_SCHEMA_UNKNOWN_PROPERTY"), true);
});
