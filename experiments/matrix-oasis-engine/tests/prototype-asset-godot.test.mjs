import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/scene-pack-contracts";
import {
  PrototypeAssetGodotVerificationError,
  buildFixedPrototypeAssetScenePack,
  parsePrototypeAssetGodotArguments,
} from "../scripts/verify-prototype-assets-godot.mjs";

function fixture() {
  const blueprint = {
    assetBriefs: [
      { id: "room-brief", kind: "environment", entityId: null, roles: ["visual", "collider"] },
      { id: "object-brief", kind: "prop", entityId: "object-unit", roles: ["visual", "collider"] },
      { id: "person-brief", kind: "character-placeholder", entityId: "person-unit", roles: ["visual", "collider"] },
    ],
    placements: [
      { id: "logical-room", assetBriefId: "room-brief", zoneId: "zone", entityId: null },
      { id: "logical-object", assetBriefId: "object-brief", zoneId: "zone", entityId: "object-unit" },
      { id: "logical-person", assetBriefId: "person-brief", zoneId: "zone", entityId: "person-unit" },
    ],
    nodeBindings: [
      { nodeId: "node-a", zoneId: "zone", visiblePlacementIds: ["logical-room", "logical-object", "logical-person"] },
      { nodeId: "node-b", zoneId: "zone", visiblePlacementIds: ["logical-room", "logical-person"] },
    ],
  };
  const runtimePack = {
    format: "matrix-oasis.runtime-game-pack",
    formatVersion: "0.1.0",
    source: { id: "fixture-pack", contentVersion: "0.1.0", canonicalSha256: "a".repeat(64) },
    nodes: [{ id: "node-a" }, { id: "node-b" }],
  };
  const receipt = { artifact: { sha256: "b".repeat(64) } };
  const asset = (id, roles) => ({
    id,
    roles,
    path: `assets/${id}.glb`,
    format: "glb",
    byteLength: 100,
    sha256: `sha256:${id.startsWith("room") ? "c" : id.startsWith("object") ? "d" : "e"}`.padEnd(71, id.endsWith("collider") ? "f" : "0").slice(0, 71),
  });
  const assetBundle = {
    scene: { id: "fixture-scene", contentVersion: "0.1.0", title: "Fixed Fixture" },
    materializations: [
      {
        assetBriefId: "room-brief",
        source: { type: "builtin-template", template: "kenney-prototype-room-v1" },
        assets: [asset("room-floor-square", ["visual", "collider"]), asset("room-wall", ["visual", "collider"])],
      },
      {
        assetBriefId: "object-brief",
        source: { type: "meshy-text-to-3d", provider: "meshy", model: "meshy-6" },
        assets: [asset("object-visual", ["visual"]), asset("object-collider", ["collider"])],
      },
      {
        assetBriefId: "person-brief",
        source: { type: "meshy-text-to-3d", provider: "meshy", model: "meshy-6" },
        assets: [asset("person-visual", ["visual"]), asset("person-collider", ["collider"])],
      },
    ],
  };
  return { blueprint, runtimePack, receipt, assetBundle };
}

test("fixed R9 layout maps one environment, prop, and character without topic branches", () => {
  const scene = buildFixedPrototypeAssetScenePack(fixture());
  assert.equal(scene.assets.length, 6);
  assert.equal(scene.placements.length, 18);
  assert.deepEqual(scene.nodeBindings.map(({ nodeId }) => nodeId), ["node-a", "node-b"]);
  assert.equal(scene.nodeBindings[0].visiblePlacementIds.length, 18);
  assert.equal(scene.nodeBindings[1].visiblePlacementIds.length, 17);
  const object = scene.placements.find(({ id }) => id === "r9-prop");
  const person = scene.placements.find(({ id }) => id === "r9-character");
  assert.deepEqual([object.visualAssetId, object.colliderAssetId, object.entityId], ["object-visual", "object-collider", "object-unit"]);
  assert.deepEqual([person.visualAssetId, person.colliderAssetId, person.entityId], ["person-visual", "person-collider", "person-unit"]);
  assert.equal(Object.isFrozen(scene.nodeBindings), true);
});

test("fixed layout is byte deterministic twenty times", () => {
  const values = Array.from({ length: 20 }, () => canonicalizeJsonValue(buildFixedPrototypeAssetScenePack(fixture())));
  assert.equal(new Set(values).size, 1);
});

test("fixed layout fails closed for expanded or incomplete qualification shapes", () => {
  const extra = fixture();
  extra.blueprint.assetBriefs.push({ id: "other", kind: "prop", entityId: null, roles: ["visual"] });
  assert.throws(() => buildFixedPrototypeAssetScenePack(extra), PrototypeAssetGodotVerificationError);
  const missing = fixture();
  missing.assetBundle.materializations[1].assets.pop();
  assert.throws(() => buildFixedPrototypeAssetScenePack(missing), PrototypeAssetGodotVerificationError);
  const binding = fixture();
  binding.blueprint.nodeBindings[0].visiblePlacementIds.push("unknown-placement");
  assert.throws(() => buildFixedPrototypeAssetScenePack(binding), PrototypeAssetGodotVerificationError);
});

test("qualification CLI is closed and ordinary verification has no supplier surface", async () => {
  assert.deepEqual(parsePrototypeAssetGodotArguments([]), { mode: "fixture" });
  assert.throws(() => parsePrototypeAssetGodotArguments(["--asset-bundle-dir", "x"]), PrototypeAssetGodotVerificationError);
  const source = await readFile(new URL("../scripts/verify-prototype-assets-godot.mjs", import.meta.url), "utf8");
  for (const forbidden of [
    ["create", "Meshy"].join(""),
    ["MATRIX", "OASIS", "MESHY", "API", "KEY"].join("_"),
    ["fetch", "("].join(""),
    ["Mar", "ble"].join(""),
  ]) assert.equal(source.includes(forbidden), false);
  assert.equal(source.includes("meshy-provider.mjs"), false);
});
