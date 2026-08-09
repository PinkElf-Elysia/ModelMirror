import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  GODOT_READINESS_MARKER,
  GODOT_REQUIRED_VERSION,
  GodotHarnessError,
  assertGodotOutputClean,
  assertSingleReadinessMarker,
  extractGodotVersion,
  resolveGodotBinary,
} from "../scripts/lib/godot-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

test("R4 project fixes Forward+ and the 960 by 540 viewport", () => {
  const project = readFileSync(
    path.join(moduleRoot, "apps", "runtime-godot", "project.godot"),
    "utf8",
  );
  assert.match(project, /renderer\/rendering_method="forward_plus"/);
  assert.match(project, /viewport_width=960/);
  assert.match(project, /viewport_height=540/);
  assert.match(project, /run\/main_scene="res:\/\/scenes\/bootstrap\.tscn"/);
});

test("Bootstrap is neutral, deterministic, and exposes the readiness marker", () => {
  const script = readFileSync(
    path.join(moduleRoot, "apps", "runtime-godot", "scripts", "bootstrap.gd"),
    "utf8",
  );
  const scene = readFileSync(
    path.join(moduleRoot, "apps", "runtime-godot", "scenes", "bootstrap.tscn"),
    "utf8",
  );
  assert.equal(script.includes(GODOT_READINESS_MARKER), true);
  for (const node of [
    "FoundationEnvironment",
    "FoundationGround",
    "FoundationMarker",
    "FoundationLight",
    "FoundationCamera",
  ]) {
    assert.equal(scene.includes(`name="${node}"`), true);
  }
  assert.doesNotMatch(`${script}\n${scene}`, /Runtime Pack|Marble|NPC|HTTP|WebSocket/);
});

test("Bootstrap tracks the Godot 4.6 source identity sidecar", () => {
  const sourceIdentity = readFileSync(
    path.join(moduleRoot, "apps", "runtime-godot", "scripts", "bootstrap.gd.uid"),
    "utf8",
  ).trim();
  assert.match(sourceIdentity, /^uid:\/\/[a-z0-9]+$/);
});

test("Godot version parsing and selection are exact", () => {
  assert.equal(
    extractGodotVersion("4.6.3.stable.official.7d41c59c4"),
    GODOT_REQUIRED_VERSION,
  );
  const probe = (command) => ({
    status: 0,
    stdout: command === "approved" ? "4.6.3.stable.official" : "4.6.2.stable",
    stderr: "",
  });
  assert.deepEqual(
    resolveGodotBinary({ environment: { GODOT_BIN: "approved" }, probe }),
    { command: "approved", version: "4.6.3" },
  );
});

test("Godot output gates reject errors and duplicate readiness", () => {
  assert.doesNotThrow(() => assertGodotOutputClean("Godot Engine 4.6.3\n"));
  assert.throws(
    () => assertGodotOutputClean("SCRIPT ERROR: fixture"),
    (error) => error instanceof GodotHarnessError && error.code === "GODOT_OUTPUT_CONTAINS_ERROR",
  );
  assert.doesNotThrow(() => assertSingleReadinessMarker(GODOT_READINESS_MARKER));
  assert.throws(
    () => assertSingleReadinessMarker(`${GODOT_READINESS_MARKER}\n${GODOT_READINESS_MARKER}`),
    (error) => error instanceof GodotHarnessError && error.code === "GODOT_READINESS_MARKER_INVALID",
  );
});
