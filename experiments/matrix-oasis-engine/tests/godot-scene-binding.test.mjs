import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/scene-pack-contracts";
import { validateScenePackJson } from "@matrix-oasis/scene-pack-validator";
import { buildScenePack, SCENE_ASSETS } from "../scripts/lib/godot-scene-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

async function compiledExample(name) {
  const source = await fs.readFile(path.join(moduleRoot, "examples", `${name}.authoring-game-pack.json`), "utf8");
  const compiled = await compileAuthoringGamePackJson(source);
  assert.equal(compiled.ok, true);
  return compiled;
}

test("both frozen examples produce canonical topic-independent Scene Packs", async () => {
  for (const example of ["mechanics-conformance", "last-train-r1"]) {
    const compiled = await compiledExample(example);
    const scene = buildScenePack({example, runtimePack: compiled.runtimePack, receipt: compiled.receipt});
    const sceneText = canonicalizeJsonValue(scene);
    const receiptText = canonicalizeJsonValue(compiled.receipt);
    const report = await validateScenePackJson(sceneText, compiled.canonicalJson, receiptText);
    assert.deepEqual(report, {reportVersion: 1, valid: true, diagnostics: []});
    assert.equal(scene.assets.length, 4);
    assert.equal(scene.placements.length, 18);
    assert.equal(scene.nodeBindings.length, compiled.runtimePack.nodes.length);
    assert.deepEqual(scene.nodeBindings.map(({nodeId}) => nodeId), compiled.runtimePack.nodes.map(({id}) => id));
    assert.equal(new Set(scene.placements.map(({id}) => id)).size, 18);
    assert.equal(Object.isFrozen(scene), true);
    assert.equal(Object.isFrozen(scene.nodeBindings), true);
  }
});

test("node bindings preserve declaration order and switch only data-defined visibility", async () => {
  const compiled = await compiledExample("mechanics-conformance");
  const scene = buildScenePack({example: "mechanics-conformance", runtimePack: compiled.runtimePack, receipt: compiled.receipt});
  const environment = scene.nodeBindings[0].visiblePlacementIds.filter((id) => !["scene-crate", "scene-figurine"].includes(id));
  assert.equal(environment.length, 16);
  for (const [index, binding] of scene.nodeBindings.entries()) {
    assert.deepEqual(binding.visiblePlacementIds.slice(0, 16), environment);
    assert.equal(binding.visiblePlacementIds.at(-1), index % 2 === 0 ? "scene-crate" : "scene-figurine");
    assert.deepEqual(binding.actionAnchor, {positionMm: [0, 0, 2000], yawMilliDegrees: 0});
  }
  assert.deepEqual(scene.assets, SCENE_ASSETS);
});

test("Godot composition uses static concave collision and frozen R5/R6 public classes", async () => {
  const sources = await Promise.all([
    "apps/runtime-godot/scene_binding/scene_composer.gd",
    "apps/runtime-godot/scene_binding/scene_composed_world.gd",
    "apps/runtime-godot/scene_binding/scene_lab.gd",
    "apps/runtime-godot/scene_binding/scene_lab.tscn",
  ].map((relative) => fs.readFile(path.join(moduleRoot, relative), "utf8")));
  const joined = sources.join("\n");
  assert.match(joined, /ConcavePolygonShape3D\.new\(\)/u);
  assert.match(joined, /shape\.set_faces\(faces\)/u);
  assert.match(joined, /StaticBody3D\.new\(\)/u);
  assert.match(joined, /collision_layer = WORLD_COLLISION_LAYER if shown else 0/u);
  assert.match(joined, /res:\/\/playable\/player\.tscn/u);
  assert.match(joined, /MatrixOasisGodotRuntime\.apply_game_session_action/u);
  for (const forbidden of ["inspect-map", "last-train", "node-carriage", "mechanics-conformance"]) {
    assert.equal(joined.includes(forbidden), false);
  }
});
