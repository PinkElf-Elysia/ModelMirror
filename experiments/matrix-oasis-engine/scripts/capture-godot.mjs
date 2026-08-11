import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  GodotHarnessError,
  assertGodotOutputClean,
  assertSingleReadinessMarker,
  projectPath,
  resolveGodotBinary,
  runGodotCommand,
} from "./lib/godot-core.mjs";
import { GODOT_RUNTIME_READY_MARKER } from "./lib/godot-runtime-core.mjs";
import {
  createRuntimePreviewArtifacts,
  parseRuntimePreviewArguments,
  removeRuntimePreviewArtifacts,
} from "./prepare-godot-runtime.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const sourceProjectRoot = projectPath(moduleRoot);
export const CAPTURE_WIDTH = 960;
export const NARROW_CAPTURE_WIDTH = 640;
export const CAPTURE_HEIGHT = 540;
export const CAPTURE_FPS = 30;
export const CAPTURE_FRAME_COUNT = 12;
export const RUNTIME_CAPTURE_FRAME_PREFIX = "runtime-lab";

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

export function parseCaptureRequest(args) {
  if (!Array.isArray(args) || ![2, 4, 5].includes(args.length)) {
    fail("GODOT_CAPTURE_ARGUMENT_ERROR");
  }
  const output = parseCaptureArguments(args.slice(0, 2));
  if (args.length === 2) {
    return Object.freeze({ output, example: null, width: CAPTURE_WIDTH });
  }
  if (args.length === 5 && args[4] !== "--narrow") {
    fail("GODOT_CAPTURE_ARGUMENT_ERROR");
  }
  let example;
  try {
    example = parseRuntimePreviewArguments(args.slice(2, 4));
  } catch {
    fail("GODOT_CAPTURE_ARGUMENT_ERROR");
  }
  return Object.freeze({
    output,
    example,
    width: args.length === 5 ? NARROW_CAPTURE_WIDTH : CAPTURE_WIDTH,
  });
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

function configureDisposableViewport(projectRoot, width) {
  if (width === CAPTURE_WIDTH) {
    return;
  }
  if (width !== NARROW_CAPTURE_WIDTH) {
    fail("GODOT_CAPTURE_FRAME_INVALID");
  }
  const projectFile = path.join(projectRoot, "project.godot");
  let source = fs.readFileSync(projectFile, "utf8");
  for (const key of ["viewport_width", "window_width_override"]) {
    const setting = `${key}=${CAPTURE_WIDTH}`;
    if (source.split(setting).length !== 2) {
      fail("GODOT_CAPTURE_TEMPORARY_PROJECT_INVALID");
    }
    source = source.replace(setting, `${key}=${NARROW_CAPTURE_WIDTH}`);
  }
  fs.writeFileSync(projectFile, source, "utf8");
}

function removeDisposableProject(temporaryRoot) {
  const trustedRoot = fs.realpathSync(os.tmpdir());
  const candidate = fs.realpathSync(temporaryRoot);
  if (!isContained(trustedRoot, candidate) || fs.lstatSync(candidate).isSymbolicLink()) {
    fail("GODOT_CAPTURE_TEMPORARY_PROJECT_INVALID");
  }
  fs.rmSync(candidate, { recursive: true });
}

export function inspectCapture(output, {
  framePrefix = "foundation",
  expectedWidth = CAPTURE_WIDTH,
} = {}) {
  if (framePrefix !== "foundation" && framePrefix !== RUNTIME_CAPTURE_FRAME_PREFIX) {
    fail("GODOT_CAPTURE_FRAME_INVALID");
  }
  if (expectedWidth !== CAPTURE_WIDTH && expectedWidth !== NARROW_CAPTURE_WIDTH) {
    fail("GODOT_CAPTURE_FRAME_INVALID");
  }
  const escapedPrefix = framePrefix.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
  const framePattern = new RegExp(`^${escapedPrefix}\\d+\\.png$`, "u");
  const frameNames = fs.readdirSync(output)
    .filter((name) => framePattern.test(name))
    .sort();
  if (frameNames.length !== CAPTURE_FRAME_COUNT) {
    fail("GODOT_CAPTURE_FRAME_COUNT_INVALID");
  }
  const frames = frameNames.map((name) => {
    const bytes = fs.readFileSync(path.join(output, name));
    const dimensions = readPngDimensions(bytes);
    if (dimensions.width !== expectedWidth || dimensions.height !== CAPTURE_HEIGHT || bytes.length === 0) {
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
    width: expectedWidth,
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
  let runtimeArtifacts = null;
  try {
    const request = parseCaptureRequest(process.argv.slice(2));
    const output = validateCaptureOutput(request.output);
    fs.mkdirSync(output);
    disposable = createDisposableProject();
    configureDisposableViewport(disposable.projectRoot, request.width);
    const godot = resolveGodotBinary();
    const importOutput = runGodotCommand({
      command: godot.command,
      args: ["--headless", "--editor", "--path", disposable.projectRoot, "--quit"],
      cwd: moduleRoot,
      timeout: 120_000,
    });
    assertGodotOutputClean(importOutput);
    let captureOutput;
    let report;
    if (request.example === null) {
      captureOutput = runGodotCommand({
        command: godot.command,
        args: [
          "--path", disposable.projectRoot,
          "--write-movie", path.join(output, "foundation.png"),
          "--fixed-fps", String(CAPTURE_FPS),
          "--resolution", `${request.width}x${CAPTURE_HEIGHT}`,
          "--", "--matrix-oasis-capture",
        ],
        cwd: moduleRoot,
        timeout: 120_000,
      });
      assertGodotOutputClean(captureOutput);
      assertSingleReadinessMarker(captureOutput);
      report = inspectCapture(output);
    } else {
      runtimeArtifacts = await createRuntimePreviewArtifacts({
        moduleRoot,
        example: request.example,
        compileAuthoringGamePackJson,
        canonicalizeJsonValue,
      });
      captureOutput = runGodotCommand({
        command: godot.command,
        args: [
          "--path", disposable.projectRoot,
          "--write-movie", path.join(output, `${RUNTIME_CAPTURE_FRAME_PREFIX}.png`),
          "--fixed-fps", String(CAPTURE_FPS),
          "--resolution", `${request.width}x${CAPTURE_HEIGHT}`,
          "--quit-after", String(CAPTURE_FRAME_COUNT),
          "res://runtime/runtime_lab.tscn",
          "--",
          `--matrix-oasis-runtime-pack=${runtimeArtifacts.runtimePath}`,
          `--matrix-oasis-runtime-receipt=${runtimeArtifacts.receiptPath}`,
        ],
        cwd: moduleRoot,
        timeout: 120_000,
      });
      assertGodotOutputClean(captureOutput);
      const readinessCount = captureOutput.split(GODOT_RUNTIME_READY_MARKER).length - 1;
      if (readinessCount !== 1) {
        fail("GODOT_CAPTURE_RUNTIME_MARKER_INVALID");
      }
      report = Object.freeze({
        ...inspectCapture(output, {
          framePrefix: RUNTIME_CAPTURE_FRAME_PREFIX,
          expectedWidth: request.width,
        }),
        example: request.example,
      });
      removeRuntimePreviewArtifacts(runtimeArtifacts.temporaryRoot, {
        moduleRoot,
        identity: runtimeArtifacts.identity,
      });
      runtimeArtifacts = null;
    }
    fs.writeFileSync(path.join(output, "capture-manifest.json"), `${JSON.stringify(report, null, 2)}\n`, { flag: "wx" });
    removeDisposableProject(disposable.temporaryRoot);
    disposable = null;
    console.log(`GODOT_CAPTURE_OK frames=${report.frameCount} size=${report.width}x${report.height}`);
  } catch (error) {
    if (runtimeArtifacts !== null) {
      try {
        removeRuntimePreviewArtifacts(runtimeArtifacts.temporaryRoot, {
          moduleRoot,
          identity: runtimeArtifacts.identity,
        });
      } catch {
        // Preserve ambiguous temporary state for diagnosis instead of broad cleanup.
      }
    }
    const code = error instanceof GodotHarnessError ? error.code : "GODOT_CAPTURE_INTERNAL_ERROR";
    console.error(code);
    process.exitCode = code === "GODOT_CAPTURE_ARGUMENT_ERROR" ? 2 : 1;
  }
}
