import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  GodotPlayableHarnessError,
  parseGodotPlayableTrace,
  playableGodotArguments,
  PLAYABLE_READY_MARKER,
  PLAYABLE_TRACE_MARKER,
} from "../scripts/lib/godot-playable-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const projectRoot = path.join(moduleRoot, "apps", "runtime-godot");

test("playable Godot arguments select only the independent R6 scene and paired files", () => {
  const root = path.parse(moduleRoot).root;
  const runtimePath = path.join(root, "tmp", "runtime.json");
  const receiptPath = path.join(root, "tmp", "receipt.json");
  assert.deepEqual(playableGodotArguments({ projectRoot, runtimePath, receiptPath }), [
    "--path", projectRoot, "res://playable/playable_lab.tscn", "--",
    `--matrix-oasis-runtime-pack=${runtimePath}`,
    `--matrix-oasis-runtime-receipt=${receiptPath}`,
  ]);
  assert.deepEqual(playableGodotArguments({ projectRoot, runtimePath, receiptPath, smoke: true }), [
    "--headless", "--path", projectRoot, "res://playable/playable_lab.tscn", "--",
    `--matrix-oasis-runtime-pack=${runtimePath}`,
    `--matrix-oasis-runtime-receipt=${receiptPath}`,
    "--matrix-oasis-3d-smoke",
  ]);
});

test("R6 trace parser accepts one exact frozen trace and rejects expanded output", () => {
  const trace = { traceVersion: 1, created: { ok: true }, steps: [] };
  const output = `${PLAYABLE_READY_MARKER}\n${PLAYABLE_TRACE_MARKER}${JSON.stringify(trace)}\n`;
  const parsed = parseGodotPlayableTrace(output, 0);
  assert.deepEqual(parsed, trace);
  assert.equal(Object.isFrozen(parsed), true);
  for (const invalid of [
    ["", 0],
    [`${output}${PLAYABLE_TRACE_MARKER}${JSON.stringify(trace)}\n`, 0],
    [`${PLAYABLE_TRACE_MARKER}{}`, 0],
    [output, 1],
  ]) {
    assert.throws(
      () => parseGodotPlayableTrace(invalid[0], invalid[1]),
      (error) => error instanceof GodotPlayableHarnessError,
    );
  }
});

test("playable scene routes successful terminal requests through the frozen R5 runtime", () => {
  const lab = fs.readFileSync(path.join(projectRoot, "playable", "playable_lab.gd"), "utf8");
  const runner = fs.readFileSync(path.join(projectRoot, "playable", "playable_trace_runner.gd"), "utf8");
  assert.equal(lab.includes("MatrixOasisRuntimeArtifactLoader.load_from_arguments"), true);
  assert.equal(lab.includes("MatrixOasisGodotRuntime.create_game_session"), true);
  assert.equal(lab.includes("MatrixOasisGodotRuntime.apply_game_session_action"), true);
  assert.equal(lab.includes("interaction_ray.action_requested.emit(action_id)"), true);
  assert.equal(runner.includes("lab.apply_terminal_action_for_trace(action_id)"), true);
  assert.equal(runner.includes("MatrixOasisGodotRuntime.apply_game_session_action"), false);
});

test("playable first-party source remains topic-independent and offline", () => {
  const root = path.join(projectRoot, "playable");
  const source = fs.readdirSync(root)
    .filter((name) => name.endsWith(".gd") || name.endsWith(".tscn"))
    .map((name) => fs.readFileSync(path.join(root, name), "utf8"))
    .join("\n");
  const forbidden = [
    ["last", "train"].join("-"),
    ["ending", "return"].join("-"),
    ["ending", "stay"].join("-"),
    ["ending", "loop"].join("-"),
    ["HTTP", "Client"].join(""),
    ["Stream", "PeerTCP"].join(""),
    ["OS", "execute"].join("."),
    ["FileAccess", "WRITE"].join("."),
  ];
  forbidden.forEach((item) => assert.equal(source.includes(item), false, item));
  assert.equal(source.includes(PLAYABLE_READY_MARKER), true);
  assert.equal(source.includes(PLAYABLE_TRACE_MARKER), true);
});

test("terminal grid and center ray lock max count, order, distance, and interaction layer", () => {
  const grid = fs.readFileSync(path.join(projectRoot, "playable", "action_terminal_grid.gd"), "utf8");
  const ray = fs.readFileSync(path.join(projectRoot, "playable", "interaction_raycast.gd"), "utf8");
  const terminal = fs.readFileSync(path.join(projectRoot, "playable", "action_terminal_3d.tscn"), "utf8");
  assert.equal(grid.includes("MAX_TERMINALS := 64"), true);
  assert.equal(grid.includes("COLUMN_COUNT := 8"), true);
  assert.equal(ray.includes("INTERACTION_DISTANCE := 3.0"), true);
  assert.equal(ray.includes("INTERACTION_LAYER_MASK := 4"), true);
  assert.match(terminal, /collision_layer = 4\r?\ncollision_mask = 0/u);
});
