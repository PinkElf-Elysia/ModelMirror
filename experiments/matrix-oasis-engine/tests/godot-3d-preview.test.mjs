import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  GodotPlayableHarnessError,
  inspectPlayableCapture,
  parsePlayableCaptureArguments,
  parsePlayableExampleArguments,
  PLAYABLE_CAPTURE_FRAMES,
  PLAYABLE_CAPTURE_HEIGHT,
  PLAYABLE_CAPTURE_PREFIX,
  PLAYABLE_CAPTURE_WIDTH,
  PLAYABLE_NARROW_WIDTH,
  validatePlayableCaptureOutput,
} from "../scripts/lib/godot-playable-core.mjs";

test("preview accepts only the two frozen replaceable example selectors", () => {
  assert.equal(parsePlayableExampleArguments(["--example", "mechanics-conformance"]), "mechanics-conformance");
  assert.equal(parsePlayableExampleArguments(["--example", "last-train-r1"]), "last-train-r1");
  for (const args of [[], ["--example"], ["--example", "unknown"], ["--other", "last-train-r1"]]) {
    assert.throws(() => parsePlayableExampleArguments(args), GodotPlayableHarnessError);
  }
});

test("capture fixes argument order, C tmp output, and the two approved widths", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "matrix-oasis-r6-capture-test-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const wide = path.join(root, "wide");
  const narrow = path.join(root, "narrow");
  assert.deepEqual(parsePlayableCaptureArguments([
    "--example", "mechanics-conformance", "--output", wide,
  ]), { example: "mechanics-conformance", output: wide, width: PLAYABLE_CAPTURE_WIDTH });
  assert.deepEqual(parsePlayableCaptureArguments([
    "--example", "last-train-r1", "--output", narrow, "--narrow",
  ]), { example: "last-train-r1", output: narrow, width: PLAYABLE_NARROW_WIDTH });
  for (const args of [
    ["--output", wide, "--example", "mechanics-conformance"],
    ["--example", "unknown", "--output", wide],
    ["--example", "mechanics-conformance", "--output", "relative"],
    ["--example", "mechanics-conformance", "--output", wide, "--wide"],
  ]) {
    assert.throws(() => parsePlayableCaptureArguments(args), GodotPlayableHarnessError);
  }
  assert.equal(validatePlayableCaptureOutput(wide, { temporaryRoot: root }), wide);
  fs.mkdirSync(wide);
  assert.throws(
    () => validatePlayableCaptureOutput(wide, { temporaryRoot: root }),
    GodotPlayableHarnessError,
  );
});

test("capture inspection requires twelve nonempty PNG headers at the requested size", (context) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "matrix-oasis-r6-frames-"));
  context.after(() => fs.rmSync(root, { recursive: true, force: true }));
  for (let index = 0; index < PLAYABLE_CAPTURE_FRAMES; index += 1) {
    const bytes = Buffer.alloc(24);
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(bytes, 0);
    bytes.write("IHDR", 12, "ascii");
    bytes.writeUInt32BE(PLAYABLE_CAPTURE_WIDTH, 16);
    bytes.writeUInt32BE(PLAYABLE_CAPTURE_HEIGHT, 20);
    fs.writeFileSync(path.join(root, `${PLAYABLE_CAPTURE_PREFIX}${String(index).padStart(8, "0")}.png`), bytes);
  }
  const report = inspectPlayableCapture(root, PLAYABLE_CAPTURE_WIDTH);
  assert.equal(report.frameCount, PLAYABLE_CAPTURE_FRAMES);
  assert.equal(report.frames.every((frame) => /^[0-9a-f]{64}$/u.test(frame.sha256)), true);
});

test("preview and capture wrappers use disposable R5 project/artifact utilities", () => {
  const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
  const preview = fs.readFileSync(path.join(moduleRoot, "scripts", "preview-godot-3d.mjs"), "utf8");
  const capture = fs.readFileSync(path.join(moduleRoot, "scripts", "capture-godot-3d.mjs"), "utf8");
  for (const source of [preview, capture]) {
    assert.equal(source.includes("createRuntimePreviewArtifacts"), true);
    assert.equal(source.includes("createRuntimePreviewProject"), true);
    assert.equal(source.includes("removeRuntimePreviewArtifacts"), true);
    assert.equal(source.includes("removeRuntimePreviewProject"), true);
  }
});
