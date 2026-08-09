import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  GodotHarnessError,
  assertGodotOutputClean,
  assertSingleReadinessMarker,
  projectPath,
  resolveGodotBinary,
  runGodotCommand,
} from "./lib/godot-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const sourceProjectRoot = projectPath(moduleRoot);
export const CAPTURE_WIDTH = 960;
export const CAPTURE_HEIGHT = 540;
export const CAPTURE_FPS = 30;
export const CAPTURE_FRAME_COUNT = 12;

function fail(code) {
  throw new GodotHarnessError(code);
}

function isContained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

export function parseCaptureArguments(args) {
  if (args.length !== 2 || args[0] !== "--output" || typeof args[1] !== "string" || args[1].includes("\0")) {
    fail("GODOT_CAPTURE_ARGUMENT_ERROR");
  }
  const output = args[1];
  if (!path.isAbsolute(output) || path.win32.isAbsolute(output) !== (process.platform === "win32")) {
    fail("GODOT_CAPTURE_OUTPUT_INVALID");
  }
  return output;
}

function defaultCaptureRoot() {
  return process.platform === "win32"
    ? path.win32.join(`C:${path.win32.sep}`, "tmp")
    : os.tmpdir();
}

export function validateCaptureOutput(output, { temporaryRoot = defaultCaptureRoot() } = {}) {
  const trustedRoot = fs.realpathSync(temporaryRoot);
  const absolute = path.resolve(output);
  if (!isContained(trustedRoot, absolute) || fs.existsSync(absolute)) {
    fail("GODOT_CAPTURE_OUTPUT_INVALID");
  }
  const existingParent = fs.realpathSync(path.dirname(absolute));
  if (!isContained(trustedRoot, existingParent) && existingParent !== trustedRoot) {
    fail("GODOT_CAPTURE_OUTPUT_INVALID");
  }
  return absolute;
}

export function readPngDimensions(bytes) {
  if (
    !Buffer.isBuffer(bytes) ||
    bytes.length < 24 ||
    !bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])) ||
    bytes.toString("ascii", 12, 16) !== "IHDR"
  ) {
    fail("GODOT_CAPTURE_PNG_INVALID");
  }
  return Object.freeze({ width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) });
}

function createDisposableProject() {
  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "matrix-oasis-godot-capture-project-"));
  const projectRoot = path.join(temporaryRoot, "runtime-godot");
  fs.cpSync(sourceProjectRoot, projectRoot, {
    recursive: true,
    filter: (source) => path.basename(source) !== ".godot",
  });
  return { temporaryRoot, projectRoot };
}

function removeDisposableProject(temporaryRoot) {
  const trustedRoot = fs.realpathSync(os.tmpdir());
  const candidate = fs.realpathSync(temporaryRoot);
  if (!isContained(trustedRoot, candidate) || fs.lstatSync(candidate).isSymbolicLink()) {
    fail("GODOT_CAPTURE_TEMPORARY_PROJECT_INVALID");
  }
  fs.rmSync(candidate, { recursive: true });
}

export function inspectCapture(output) {
  const frameNames = fs.readdirSync(output)
    .filter((name) => /^foundation\d+\.png$/u.test(name))
    .sort();
  if (frameNames.length !== CAPTURE_FRAME_COUNT) {
    fail("GODOT_CAPTURE_FRAME_COUNT_INVALID");
  }
  const frames = frameNames.map((name) => {
    const bytes = fs.readFileSync(path.join(output, name));
    const dimensions = readPngDimensions(bytes);
    if (dimensions.width !== CAPTURE_WIDTH || dimensions.height !== CAPTURE_HEIGHT || bytes.length === 0) {
      fail("GODOT_CAPTURE_FRAME_INVALID");
    }
    return Object.freeze({
      file: name,
      bytes: bytes.length,
      sha256: crypto.createHash("sha256").update(bytes).digest("hex"),
    });
  });
  return Object.freeze({
    captureVersion: 1,
    width: CAPTURE_WIDTH,
    height: CAPTURE_HEIGHT,
    fps: CAPTURE_FPS,
    frameCount: frames.length,
    frames: Object.freeze(frames),
  });
}

function isDirectExecution() {
  return process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
}

if (isDirectExecution()) {
  let disposable = null;
  try {
    const requestedOutput = parseCaptureArguments(process.argv.slice(2));
    const output = validateCaptureOutput(requestedOutput);
    fs.mkdirSync(output);
    disposable = createDisposableProject();
    const godot = resolveGodotBinary();
    const importOutput = runGodotCommand({
      command: godot.command,
      args: ["--headless", "--editor", "--path", disposable.projectRoot, "--quit"],
      cwd: moduleRoot,
      timeout: 120_000,
    });
    assertGodotOutputClean(importOutput);
    const captureOutput = runGodotCommand({
      command: godot.command,
      args: [
        "--path", disposable.projectRoot,
        "--write-movie", path.join(output, "foundation.png"),
        "--fixed-fps", String(CAPTURE_FPS),
        "--resolution", `${CAPTURE_WIDTH}x${CAPTURE_HEIGHT}`,
        "--", "--matrix-oasis-capture",
      ],
      cwd: moduleRoot,
      timeout: 120_000,
    });
    assertGodotOutputClean(captureOutput);
    assertSingleReadinessMarker(captureOutput);
    const report = inspectCapture(output);
    fs.writeFileSync(path.join(output, "capture-manifest.json"), `${JSON.stringify(report, null, 2)}\n`, { flag: "wx" });
    removeDisposableProject(disposable.temporaryRoot);
    disposable = null;
    console.log(`GODOT_CAPTURE_OK frames=${report.frameCount} size=${report.width}x${report.height}`);
  } catch (error) {
    const code = error instanceof GodotHarnessError ? error.code : "GODOT_CAPTURE_INTERNAL_ERROR";
    console.error(code);
    process.exitCode = code === "GODOT_CAPTURE_ARGUMENT_ERROR" ? 2 : 1;
  }
}
