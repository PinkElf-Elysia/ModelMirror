import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

test("isolated Godot verifier uses the required real navigation and physics evidence", async () => {
  const source = await readFile(path.join(moduleRoot, "apps", "runtime-godot", "spatial_solution_verification", "solution_verifier.gd"), "utf8");
  for (const expression of [
    /NavigationServer3D\.query_path/u,
    /NavigationServer3D\.map_get_iteration_id/u,
    /intersect_shape/u,
    /cast_motion/u,
    /intersect_ray/u,
    /get_tree\(\)\.physics_frame/u,
    /MatrixOasisActionTerminalGrid/u,
    /EULER_ORDER_YXZ/u,
    /GLTFDocument/u,
  ]) assert.match(source, expression);
  for (const forbidden of [
    ["HTTP", "Request"].join(""), ["HTTP", "Client"].join(""), ["OS.", "execute"].join(""), ["get_", "environment"].join(""),
    "openai", "meshy", "marble", "last-train", "subway", "carriage", "platform", "panorama",
  ]) assert.equal(source.toLowerCase().includes(forbidden.toLowerCase()), false, forbidden);
});

test("verifier scene is independent from every product preview", async () => {
  const scene = await readFile(path.join(moduleRoot, "apps", "runtime-godot", "spatial_solution_verification", "solution_verifier.tscn"), "utf8");
  assert.match(scene, /spatial_solution_verification\/solution_verifier\.gd/u);
  for (const forbidden of ["prototype_builder", "spatial_prototype", "solved_spatial_prototype", "scene_binding"]) assert.equal(scene.includes(forbidden), false);
});

test("declaration exposes only authoritative types and the two approved functions", async () => {
  const declaration = await readFile(path.join(moduleRoot, "packages", "prototype-spatial-verifier", "src", "index.d.ts"), "utf8");
  assert.match(declaration, /import type \{ PrototypeSpatialSolution \}/u);
  assert.equal((declaration.match(/export declare function /gu) ?? []).length, 2);
  assert.equal(declaration.includes("interface PrototypeSpatialSolution {"), false);
});
