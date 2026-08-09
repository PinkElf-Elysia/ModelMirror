import assert from "node:assert/strict";
import fs, { readFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  GODOT_READINESS_MARKER,
  GODOT_REQUIRED_VERSION,
  GodotHarnessError,
  assertGdUnitSuccess,
  assertGodotOutputClean,
  assertSingleReadinessMarker,
  extractGodotVersion,
  resolveGodotBinary,
} from "../scripts/lib/godot-core.mjs";
import {
  CAPTURE_FRAME_COUNT,
  CAPTURE_HEIGHT,
  CAPTURE_WIDTH,
  parseCaptureArguments,
  readPngDimensions,
  validateCaptureOutput,
} from "../scripts/capture-godot.mjs";
import {
  parseQualificationArguments,
  sanitizedMcpEnvironment,
  validateQualificationOutput,
} from "../scripts/qualify-godot-mcp.mjs";

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

test("GdUnit output gate requires the exact four-test clean summary", () => {
  const success = [
    "Overall Summary:",
    "4 test cases | 0 errors | 0 failures | 0 flaky | 0 skipped | 0 orphans",
    "Executed test suites: (1/1)",
    "Executed test cases : (4/4)",
  ].join("\n");
  assert.doesNotThrow(() => assertGdUnitSuccess(success));
  for (const invalid of [
    "No test cases found",
    success.replace("4 test cases", "3 test cases"),
    success.replace("0 failures", "1 failures"),
  ]) {
    assert.throws(
      () => assertGdUnitSuccess(invalid),
      (error) => error instanceof GodotHarnessError && error.code === "GDUNIT4_RESULT_INVALID",
    );
  }
});

test("Godot verification uses a disposable project instead of mutating the vendor", () => {
  const harness = readFileSync(path.join(moduleRoot, "scripts", "run-godot.mjs"), "utf8");
  assert.match(harness, /matrix-oasis-godot-project-/);
  assert.match(harness, /fs\.cpSync\(sourceProjectRoot, projectRoot/);
  assert.match(harness, /path\.basename\(source\) !== "\.godot"/);
});

test("capture arguments require a new absolute child of the temporary root", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "matrix-oasis-capture-contract-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const output = path.join(root, "frames");
  assert.equal(parseCaptureArguments(["--output", output]), output);
  assert.equal(validateCaptureOutput(output, { temporaryRoot: root }), output);
  fs.mkdirSync(output);
  assert.throws(
    () => validateCaptureOutput(output, { temporaryRoot: root }),
    (error) => error instanceof GodotHarnessError && error.code === "GODOT_CAPTURE_OUTPUT_INVALID",
  );
  assert.throws(
    () => parseCaptureArguments(["--output", "relative"]),
    (error) => error instanceof GodotHarnessError && error.code === "GODOT_CAPTURE_OUTPUT_INVALID",
  );
});

test("capture contract fixes PNG dimensions and twelve frames", () => {
  const png = Buffer.alloc(24);
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(png, 0);
  png.write("IHDR", 12, "ascii");
  png.writeUInt32BE(CAPTURE_WIDTH, 16);
  png.writeUInt32BE(CAPTURE_HEIGHT, 20);
  assert.deepEqual(readPngDimensions(png), { width: 960, height: 540 });
  assert.equal(CAPTURE_FRAME_COUNT, 12);
  assert.throws(
    () => readPngDimensions(Buffer.from("not-png")),
    (error) => error instanceof GodotHarnessError && error.code === "GODOT_CAPTURE_PNG_INVALID",
  );
});

test("MCP qualification requires a new external output and strips credentials", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "matrix-oasis-mcp-contract-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const output = path.join(root, "qualification");
  assert.equal(parseQualificationArguments(["--output", output]), output);
  assert.equal(validateQualificationOutput(output, { temporaryRoot: root }), output);
  const secretName = ["API", "KEY"].join("_");
  const environment = sanitizedMcpEnvironment({ Path: "fixture", TEMP: root, [secretName]: "sentinel" });
  assert.equal(environment.Path, "fixture");
  assert.equal(environment.TEMP, root);
  assert.equal(Reflect.has(environment, secretName), false);
  assert.equal(JSON.stringify(environment).includes("sentinel"), false);
});
