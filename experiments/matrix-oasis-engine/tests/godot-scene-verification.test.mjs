import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";
import {compileAuthoringGamePackJson} from "@matrix-oasis/game-pack-compiler";
import {canonicalizeJsonValue as canonicalizeRuntimeJsonValue} from "@matrix-oasis/runtime-pack-contracts";
import {
  applyRuntimeGameSessionAction,
  createRuntimeGameSession,
  prepareRuntimeGamePackJson,
} from "@matrix-oasis/runtime-pack-simulator";
import {canonicalizeJsonValue as canonicalizeSceneJsonValue} from "@matrix-oasis/scene-pack-contracts";
import {buildGodotParityCases} from "../scripts/lib/godot-runtime-core.mjs";
import {
  buildSceneParityCases,
  inspectSceneCapture,
  parseGodotSceneTrace,
  parseSceneCaptureArguments,
  SCENE_CAPTURE_FRAMES,
  SCENE_TRACE_MARKER,
  validateSceneCaptureOutput,
} from "../scripts/lib/godot-scene-verification-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

async function cases() {
  const examples = ["mechanics-conformance", "last-train-r1"].map((name) => ({
    name,
    text: fs.readFileSync(path.join(moduleRoot, "examples", `${name}.authoring-game-pack.json`), "utf8"),
  }));
  const runtimeCases = await buildGodotParityCases({
    examples,
    compileAuthoringGamePackJson,
    canonicalizeJsonValue: canonicalizeRuntimeJsonValue,
    prepareRuntimeGamePackJson,
    createRuntimeGameSession,
    applyRuntimeGameSessionAction,
  });
  return buildSceneParityCases({runtimeCases, canonicalizeSceneJson: canonicalizeSceneJsonValue});
}

test("scene parity cases retain all runtime routes and twenty deterministic mechanics runs", async () => {
  const built = await cases();
  assert.equal(built.length, 7);
  assert.equal(built[0].repetitions, 20);
  assert.deepEqual(built.slice(1, 4).map((item) => item.referenceSceneTrace.steps.at(-1).scene.status), [
    "ended", "ended", "ended",
  ]);
  assert.equal(built[4].referenceSceneTrace.steps.at(-1).runtime.diagnostics[0].code, "PACK_RUNTIME_STEP_LIMIT");
  assert.equal(built[5].referenceSceneTrace.steps[0].runtime.diagnostics[0].code, "PACK_RUNTIME_INTEGER_OVERFLOW");
  assert.equal(built.every((item) => /^[0-9a-f]{64}$/u.test(item.referenceSceneTrace.created.scene.manifestSha256)), true);
  assert.equal(built.every((item) => item.referenceSceneTrace.created.scene.visiblePlacementIds.length === 17), true);
});

test("scene trace parser accepts exactly one frozen public marker", async () => {
  const expected = (await cases())[0].referenceSceneTrace;
  assert.deepEqual(parseGodotSceneTrace(`${SCENE_TRACE_MARKER}${JSON.stringify(expected)}\n`, 0), expected);
  assert.throws(() => parseGodotSceneTrace("", 0), /GODOT_SCENE_TRACE_MARKER_INVALID/u);
  assert.throws(
    () => parseGodotSceneTrace(`${SCENE_TRACE_MARKER}${JSON.stringify(expected)}\n${SCENE_TRACE_MARKER}{}`, 0),
    /GODOT_SCENE_TRACE_MARKER_INVALID/u,
  );
});

test("scene capture accepts only fixed examples, dimensions and new temporary output", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "matrix-oasis-scene-capture-test-"));
  try {
    const output = path.join(root, "capture");
    assert.deepEqual(parseSceneCaptureArguments([
      "--example", "mechanics-conformance", "--output", output, "--narrow",
    ]), {example: "mechanics-conformance", output, width: 640});
    assert.equal(validateSceneCaptureOutput(output, {temporaryRoot: root}), output);
    assert.throws(
      () => parseSceneCaptureArguments(["--example", "unknown", "--output", output]),
      /GODOT_SCENE_CAPTURE_ARGUMENT_ERROR/u,
    );
    fs.mkdirSync(output);
    assert.throws(() => validateSceneCaptureOutput(output, {temporaryRoot: root}), /GODOT_SCENE_CAPTURE_OUTPUT_INVALID/u);
  } finally {
    fs.rmSync(root, {recursive: true});
  }
});

test("scene capture inspection locks PNG count and dimensions", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "matrix-oasis-scene-frames-test-"));
  try {
    for (let index = 0; index < SCENE_CAPTURE_FRAMES; index += 1) {
      const bytes = Buffer.alloc(24);
      Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(bytes);
      bytes.write("IHDR", 12, "ascii");
      bytes.writeUInt32BE(960, 16);
      bytes.writeUInt32BE(540, 20);
      fs.writeFileSync(path.join(root, `scene-lab${String(index).padStart(8, "0")}.png`), bytes);
    }
    const report = inspectSceneCapture(root, 960);
    assert.equal(report.frameCount, SCENE_CAPTURE_FRAMES);
    assert.equal(report.frames.every((frame) => /^[0-9a-f]{64}$/u.test(frame.sha256)), true);
  } finally {
    fs.rmSync(root, {recursive: true});
  }
});

test("scene trace runner and verifier remain topic-independent and package-root only", () => {
  const source = [
    fs.readFileSync(path.join(moduleRoot, "apps", "runtime-godot", "scene_binding", "scene_trace_runner.gd"), "utf8"),
    fs.readFileSync(path.join(moduleRoot, "scripts", "verify-godot-scene.mjs"), "utf8"),
  ].join("\n");
  assert.doesNotMatch(source, /node-carriage|ending-return|station|subway|metro/iu);
  assert.doesNotMatch(source, /packages[/\\].*src|examples[/\\].*json/iu);
  assert.match(source, /MATRIX_OASIS_R7_SCENE_TRACE_JSON:/u);
});
