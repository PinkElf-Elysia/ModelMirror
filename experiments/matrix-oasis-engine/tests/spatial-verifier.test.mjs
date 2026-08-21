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
    /_apply_terminal_layout/u,
    /value\["actionTerminal"\]\["terminalSupports"\]/u,
    /runtime_support_height_mm/u,
    /runtime_anchor\["positionMm"\]\[1\] = request\["runtimeSupportHeightMm"\]/u,
    /_body_from_faces\(collision_faces, 1, true\)/u,
    /collision_faces\.append_array\(_steep_environment_faces\(environment_faces\)\)/u,
    /visual_safety_faces\.append_array\(_box_faces\(box\)\)/u,
    /_body_from_faces\(visual_safety_faces, 8, true\)/u,
    /query\.collision_mask = 1 \| 2 \| 8/u,
    /capsule_query\.collision_mask = 1 \| 2 \| 4 \| 8/u,
    /sight_target, 1 \| 2 \| 8/u,
    /checkedVisualSafetyBoxCount/u,
    /absf\(normal\.normalized\(\)\.y\) < MAX_WALKABLE_NORMAL_Y/u,
    /Vector2\(placement_transform\.origin\.x, placement_transform\.origin\.z\)\.distance_to/u,
    /footprint\["columns"\]/u,
    /for terminal_index in terminal_count/u,
    /_verify_terminal_access/u,
    /sight_target: Vector3 = terminal\.global_position/u,
    /for anchor_id: String in _floor_anchors/u,
    /MAX_TERMINAL_APPROACH_CANDIDATES := 256/u,
    /_candidate_overlaps_terminal_grid/u,
    /approach_id: String = value\["actionTerminal"\]\["approachFloorAnchorId"\]/u,
    /navigation_spawn: Variant = _navigation_projection/u,
    /navigation_approach: Variant = _navigation_projection/u,
    /map_get_closest_point/u,
    /horizontal > PATH_ENDPOINT_TOLERANCE/u,
    /absf\(projected\.y - position\.y\) > FLOOR_SNAP_TOLERANCE/u,
    /candidate \+ Vector3\.UP \* PLAYER_EYE_HEIGHT/u,
    /"pathCount": terminal_count/u,
    /EULER_ORDER_YXZ/u,
    /GLTFDocument/u,
  ]) assert.match(source, expression);
  assert.doesNotMatch(source, /sight_target[^\n]*terminal_grid\.global_position/u);
  assert.doesNotMatch(source, /R14_DEBUG/u);
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
