import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const godotRoot = path.join(moduleRoot, "apps", "runtime-godot");

test("R6 project fixes Jolt, interpolation, 60 Hz, and the approved InputMap", () => {
  const project = fs.readFileSync(path.join(godotRoot, "project.godot"), "utf8");
  for (const setting of [
    "physics_ticks_per_second=60",
    "physics_interpolation=true",
    '3d/physics_engine="Jolt Physics"',
    "move_forward={",
    "move_backward={",
    "move_left={",
    "move_right={",
    "interact={",
    "reset_session={",
  ]) {
    assert.equal(project.includes(setting), true, setting);
  }
  assert.equal(project.match(/run\/main_scene="res:\/\/scenes\/bootstrap\.tscn"/u)?.length, 1);
});

test("first-person controller locks the approved movement and look surface", () => {
  const source = fs.readFileSync(path.join(godotRoot, "playable", "first_person_controller.gd"), "utf8");
  for (const contract of [
    "extends CharacterBody3D",
    "MOVE_SPEED := 3.5",
    "ACCELERATION := 12.0",
    "DECELERATION := 16.0",
    "deg_to_rad(85.0)",
    "move_and_slide()",
    "reset_physics_interpolation()",
    "Input.MOUSE_MODE_CAPTURED",
    "Input.MOUSE_MODE_VISIBLE",
  ]) {
    assert.equal(source.includes(contract), true, contract);
  }
  for (const deferredFeature of ["jump", "sprint", "crouch", "joypad", "NavigationAgent3D"]) {
    assert.equal(source.toLowerCase().includes(deferredFeature.toLowerCase()), false, deferredFeature);
  }
});

test("player and movement lab retain the fixed world/player collision contract", () => {
  const player = fs.readFileSync(path.join(godotRoot, "playable", "player.tscn"), "utf8");
  const lab = fs.readFileSync(path.join(godotRoot, "playable", "movement_lab.tscn"), "utf8");
  assert.match(player, /type="CharacterBody3D"/u);
  assert.match(player, /collision_layer = 2\r?\ncollision_mask = 1/u);
  assert.match(player, /path="res:\/\/playable\/interaction_raycast\.gd"/u);
  for (const node of ["Ground", "NorthWall", "WestWall", "EastWall", "Slope", "FirstPersonPlayer"]) {
    assert.equal(lab.includes(`name="${node}"`), true, node);
  }
});

test("official movement reference stays non-executable and hash-locked", () => {
  const root = path.join(moduleRoot, "third-party", "godot-demo-projects");
  const lock = JSON.parse(fs.readFileSync(path.join(root, "reference.lock.json"), "utf8"));
  assert.equal(lock.commit, "b4eff8de9d7ba5a4f1a2dea8bae60f28816b7eea");
  assert.equal(lock.referencePath, "third-party/godot-demo-projects/cubio.gd.reference.txt");
  assert.equal(lock.executable, false);
  assert.equal(fs.existsSync(path.join(root, "cubio.gd")), false);
});
