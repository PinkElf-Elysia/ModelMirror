import assert from "node:assert/strict";
import test from "node:test";
import Ajv2020 from "ajv/dist/2020.js";
import * as api from "../src/index.mjs";

test("exports the frozen R7 Scene Pack contract", () => {
  assert.deepEqual(Object.keys(api).sort(), ["SCENE_PACK_CANONICALIZATION", "SCENE_PACK_FORMAT", "SCENE_PACK_FORMAT_VERSION", "SCENE_PACK_LIMITS", "SCENE_PACK_SCHEMA", "SCENE_PACK_SCHEMA_ID", "canonicalizeJsonValue"].sort());
  assert.equal(api.SCENE_PACK_FORMAT, "matrix-oasis.scene-pack");
  assert.equal(api.SCENE_PACK_FORMAT_VERSION, "0.1.0");
  assert.equal(api.SCENE_PACK_CANONICALIZATION, "matrix-oasis.canonical-json/1");
  assert.ok(Object.isFrozen(api.SCENE_PACK_SCHEMA));
  assert.ok(Object.isFrozen(api.SCENE_PACK_LIMITS));
});

test("schema is strict and accepts integer transforms only", () => {
  const ajv = new Ajv2020({strict: true, allErrors: true});
  const validate = ajv.compile(structuredClone(api.SCENE_PACK_SCHEMA));
  const base = {format: "matrix-oasis.scene-pack", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1", scene: {id: "fixture-scene", contentVersion: "1.0.0", title: "Fixture"}, runtimeIdentity: {runtimeFormat: "matrix-oasis.runtime-game-pack", runtimeFormatVersion: "0.1.0", packId: "fixture-pack", packContentVersion: "1", sourceCanonicalSha256: "0".repeat(64), artifactSha256: "1".repeat(64)}, assets: [], placements: [], nodeBindings: []};
  assert.equal(validate(base), true);
  const asset = {id: "floor", roles: ["visual", "collider"], path: "assets/floor.glb", format: "glb", byteLength: 1, sha256: "2".repeat(64)};
  const placement = {id: "floor-placement", visualAssetId: "floor", colliderAssetId: "floor", entityId: null, transform: {positionMm: [0, 0, 0], rotationMilliDegrees: [0, 0, 0], scalePermille: [1000, 1000, 1000]}};
  const binding = {nodeId: "node-start", playerSpawn: {positionMm: [0, 1000, 0], yawMilliDegrees: 0}, actionAnchor: {positionMm: [0, 0, -2000], yawMilliDegrees: 0}, visiblePlacementIds: ["floor-placement"]};
  assert.equal(validate({...base, assets: [asset], placements: [placement], nodeBindings: [binding]}), true);
  assert.equal(validate({...base, assets: [asset], placements: [{...placement, transform: {...placement.transform, positionMm: [0.5, 0, 0]}}]}), false);
  assert.equal(validate({...base, unexpected: true}), false);
});

test("schema locks the exact asset, placement and node-binding limits", () => {
  const ajv = new Ajv2020({strict: true, allErrors: true});
  const validate = ajv.compile(structuredClone(api.SCENE_PACK_SCHEMA));
  const base = {format: "matrix-oasis.scene-pack", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1", scene: {id: "limit-scene", contentVersion: "1", title: "Limits"}, runtimeIdentity: {runtimeFormat: "matrix-oasis.runtime-game-pack", runtimeFormatVersion: "0.1.0", packId: "limit-pack", packContentVersion: "1", sourceCanonicalSha256: "0".repeat(64), artifactSha256: "1".repeat(64)}, assets: [], placements: [], nodeBindings: []};
  const asset = {id: "asset", roles: ["visual"], path: "asset.glb", format: "glb", byteLength: 1, sha256: "2".repeat(64)};
  const placement = {id: "placement", visualAssetId: "asset", colliderAssetId: null, entityId: null, transform: {positionMm: [0, 0, 0], rotationMilliDegrees: [0, 0, 0], scalePermille: [1000, 1000, 1000]}};
  const binding = {nodeId: "node", playerSpawn: {positionMm: [0, 0, 0], yawMilliDegrees: 0}, actionAnchor: {positionMm: [0, 0, 0], yawMilliDegrees: 0}, visiblePlacementIds: []};
  assert.equal(validate({...base, assets: Array(16).fill(asset)}), true);
  assert.equal(validate({...base, assets: Array(17).fill(asset)}), false);
  assert.equal(validate({...base, placements: Array(128).fill(placement)}), true);
  assert.equal(validate({...base, placements: Array(129).fill(placement)}), false);
  assert.equal(validate({...base, nodeBindings: Array(4096).fill(binding)}), true);
  assert.equal(validate({...base, nodeBindings: Array(4097).fill(binding)}), false);
});
