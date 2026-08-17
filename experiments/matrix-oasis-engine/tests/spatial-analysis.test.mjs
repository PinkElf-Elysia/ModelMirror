import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

test("the isolated Godot analyzer uses navigation and physics evidence without product coupling", async () => {
  const source = await readFile(
    path.join(moduleRoot, "apps", "runtime-godot", "spatial_analysis", "environment_analyzer.gd"),
    "utf8",
  );
  for (const forbidden of [
    ["HTTP", "Request"].join(""), ["HTTP", "Client"].join(""),
    ["OS.", "execute"].join(""), ["get_", "environment"].join(""),
    "openai", "meshy", "marble", "last-train", "subway", "carriage", "platform",
  ]) {
    assert.equal(source.toLowerCase().includes(forbidden.toLowerCase()), false, forbidden);
  }
  assert.match(source, /bake_from_source_geometry_data_async/u);
  assert.match(source, /intersect_shape/u);
  assert.match(source, /intersect_ray/u);
  assert.match(source, /EULER_ORDER_YXZ/u);
  assert.match(source, /FileAccess\.open\(paths\["output"\], FileAccess\.WRITE\)/u);
});

test("the analyzer scene is independent from every product preview", async () => {
  const scene = await readFile(
    path.join(moduleRoot, "apps", "runtime-godot", "spatial_analysis", "environment_analyzer.tscn"),
    "utf8",
  );
  assert.equal(scene.includes("prototype_builder"), false);
  assert.equal(scene.includes("spatial_prototype"), false);
  assert.equal(scene.includes("scene_binding"), false);
  assert.match(scene, /spatial_analysis\/environment_analyzer\.gd/u);
});
