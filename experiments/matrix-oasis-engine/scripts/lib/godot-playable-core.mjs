import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import { isDeepStrictEqual } from "node:util";
import { assertGodotOutputClean, runGodotCommand } from "./godot-core.mjs";

export const PLAYABLE_EXAMPLES = Object.freeze([
  "mechanics-conformance",
  "last-train-r1",
]);
export const PLAYABLE_READY_MARKER = "MATRIX_OASIS_R6_PLAYABLE_3D_READY";
export const PLAYABLE_TRACE_MARKER = "MATRIX_OASIS_R6_3D_TRACE_JSON:";
export const PLAYABLE_CAPTURE_WIDTH = 960;
export const PLAYABLE_NARROW_WIDTH = 640;
export const PLAYABLE_CAPTURE_HEIGHT = 540;
export const PLAYABLE_CAPTURE_FPS = 30;
export const PLAYABLE_CAPTURE_FRAMES = 12;
export const PLAYABLE_CAPTURE_PREFIX = "playable-lab";

export class GodotPlayableHarnessError extends Error {
  constructor(code) {
    super(code);
    this.name = "GodotPlayableHarnessError";
    this.code = code;
  }
}

function fail(code) {
  throw new GodotPlayableHarnessError(code);
}

function exactKeys(value, expected) {
  if (!value || Object.getPrototypeOf(value) !== Object.prototype) {
    return false;
  }
  const keys = Object.keys(value).sort();
  return keys.length === expected.length && keys.every((key, index) => key === expected[index]);
}

function freezeJson(value) {
  if (Array.isArray(value)) {
    value.forEach(freezeJson);
    return Object.freeze(value);
  }
  if (value && typeof value === "object") {
    Object.values(value).forEach(freezeJson);
    return Object.freeze(value);
  }
  return value;
}

function temporaryBase(moduleRoot) {
  return process.platform === "win32" ? path.join(path.parse(moduleRoot).root, "tmp") : os.tmpdir();
}

function captureBase() {
  return process.platform === "win32"
    ? path.win32.join(`C:${path.win32.sep}`, "tmp")
    : os.tmpdir();
}

function isContained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function ownedIdentity(candidate) {
  const stat = fs.lstatSync(candidate, { bigint: true });
  return Object.freeze({ dev: stat.dev, ino: stat.ino });
}

function removeOwnedRoot(temporaryRoot, moduleRoot, identity, prefix) {
  const base = fs.realpathSync(temporaryBase(moduleRoot));
  const candidate = fs.realpathSync(temporaryRoot);
  const stat = fs.lstatSync(candidate, { bigint: true });
  if (!isContained(base, candidate) || stat.isSymbolicLink() ||
      stat.dev !== identity.dev || stat.ino !== identity.ino ||
      !path.basename(candidate).startsWith(prefix)) {
    fail("GODOT_3D_TEMPORARY_ROOT_INVALID");
  }
  fs.rmSync(candidate, { recursive: true });
}

export function parsePlayableExampleArguments(args) {
  if (!Array.isArray(args) || args.length !== 2 || args[0] !== "--example" ||
      !PLAYABLE_EXAMPLES.includes(args[1])) {
    fail("GODOT_3D_ARGUMENT_ERROR");
  }
  return args[1];
}

export function playableGodotArguments({ projectRoot, runtimePath, receiptPath, smoke = false }) {
  if (![projectRoot, runtimePath, receiptPath].every((value) =>
    typeof value === "string" && path.isAbsolute(value) && !value.includes("\0"))) {
    fail("GODOT_3D_PATH_INVALID");
  }
  return Object.freeze([
    ...(smoke ? ["--headless"] : []),
    "--path",
    projectRoot,
    "res://playable/playable_lab.tscn",
    "--",
    `--matrix-oasis-runtime-pack=${runtimePath}`,
    `--matrix-oasis-runtime-receipt=${receiptPath}`,
    ...(smoke ? ["--matrix-oasis-3d-smoke"] : []),
  ]);
}

export function parseGodotPlayableTrace(output, status) {
  const text = typeof output === "string" ? output : "";
  if (status !== 0 || text.split(PLAYABLE_TRACE_MARKER).length - 1 !== 1) {
    fail("GODOT_3D_TRACE_MARKER_INVALID");
  }
  const line = text.split(/\r?\n/u).find((item) => item.includes(PLAYABLE_TRACE_MARKER));
  let trace;
  try {
    trace = JSON.parse(line.slice(line.indexOf(PLAYABLE_TRACE_MARKER) + PLAYABLE_TRACE_MARKER.length));
  } catch {
    fail("GODOT_3D_TRACE_REPORT_INVALID");
  }
  if (!exactKeys(trace, ["created", "steps", "traceVersion"]) || trace.traceVersion !== 1 ||
      !trace.created || trace.created.ok !== true || !Array.isArray(trace.steps)) {
    fail("GODOT_3D_TRACE_REPORT_INVALID");
  }
  return freezeJson(trace);
}

export function runGodotPlayableCases({
  moduleRoot,
  sourceProjectRoot,
  godotCommand,
  cases,
  runTraces = true,
  spawn = spawnSync,
}) {
  if (!Array.isArray(cases) || cases.length < 1 || typeof runTraces !== "boolean") {
    fail("GODOT_3D_CASE_INPUT_INVALID");
  }
  const base = temporaryBase(moduleRoot);
  fs.mkdirSync(base, { recursive: true });
  const prefix = "matrix-oasis-r6-playable-";
  const temporaryRoot = fs.mkdtempSync(path.join(base, prefix));
  const identity = ownedIdentity(temporaryRoot);
  const projectRoot = path.join(temporaryRoot, "runtime-godot");
  const inputsRoot = path.join(temporaryRoot, "inputs");
  try {
    fs.cpSync(sourceProjectRoot, projectRoot, {
      recursive: true,
      filter: (source) => path.basename(source) !== ".godot",
    });
    fs.mkdirSync(inputsRoot);
    const imported = runGodotCommand({
      command: godotCommand,
      args: ["--headless", "--editor", "--path", projectRoot, "--quit"],
      cwd: moduleRoot,
      timeout: 120_000,
      spawn,
    });
    assertGodotOutputClean(imported);
    const caseFiles = new Map();
    const results = [];
    for (let index = 0; index < cases.length; index += 1) {
      const item = cases[index];
      const caseRoot = path.join(inputsRoot, String(index));
      fs.mkdirSync(caseRoot);
      const runtimePath = path.join(caseRoot, "runtime.json");
      const receiptPath = path.join(caseRoot, "receipt.json");
      fs.writeFileSync(runtimePath, item.runtimeText, { encoding: "utf8", flag: "wx" });
      fs.writeFileSync(receiptPath, item.receiptText, { encoding: "utf8", flag: "wx" });
      caseFiles.set(item.name, { runtimePath, receiptPath });
      const serializations = [];
      for (let repetition = 0; runTraces && repetition < item.repetitions; repetition += 1) {
        const processResult = spawn(godotCommand, [
          "--headless",
          "--path",
          projectRoot,
          "--script",
          "res://playable/playable_trace_runner.gd",
          "--",
          `--matrix-oasis-runtime-pack=${runtimePath}`,
          `--matrix-oasis-runtime-receipt=${receiptPath}`,
          `--matrix-oasis-3d-trace-step-limit=${item.stepLimit}`,
          ...item.actions.map((actionId) => `--matrix-oasis-3d-trace-action=${actionId}`),
        ], {
          cwd: moduleRoot,
          encoding: "utf8",
          maxBuffer: 8 * 1024 * 1024,
          shell: false,
          timeout: 30_000,
          windowsHide: true,
        });
        if (processResult.error || processResult.status !== 0) {
          fail("GODOT_3D_TRACE_COMMAND_FAILED");
        }
        const output = `${processResult.stdout ?? ""}${processResult.stderr ?? ""}`;
        assertGodotOutputClean(output);
        const trace = parseGodotPlayableTrace(output, processResult.status);
        if (!isDeepStrictEqual(trace, item.referenceTrace)) {
          fail("GODOT_3D_TRACE_MISMATCH");
        }
        serializations.push(JSON.stringify(trace));
      }
      if (runTraces && new Set(serializations).size !== 1) {
        fail("GODOT_3D_TRACE_NONDETERMINISTIC");
      }
      if (runTraces) {
        results.push(Object.freeze({ name: item.name, repetitions: item.repetitions }));
      }
    }
    const smokeNames = ["mechanics-complete-with-failures", "last-train-return"];
    for (const name of smokeNames) {
      const pair = caseFiles.get(name);
      if (!pair) {
        fail("GODOT_3D_SMOKE_INPUT_INVALID");
      }
      const processResult = spawn(godotCommand, playableGodotArguments({
        projectRoot,
        runtimePath: pair.runtimePath,
        receiptPath: pair.receiptPath,
        smoke: true,
      }), {
        cwd: moduleRoot,
        encoding: "utf8",
        maxBuffer: 8 * 1024 * 1024,
        shell: false,
        timeout: 30_000,
        windowsHide: true,
      });
      if (processResult.error || processResult.status !== 0) {
        fail("GODOT_3D_SMOKE_COMMAND_FAILED");
      }
      const output = `${processResult.stdout ?? ""}${processResult.stderr ?? ""}`;
      assertGodotOutputClean(output);
      if (output.split(PLAYABLE_READY_MARKER).length - 1 !== 1) {
        fail("GODOT_3D_READY_MARKER_INVALID");
      }
    }
    removeOwnedRoot(temporaryRoot, moduleRoot, identity, prefix);
    return Object.freeze({ results: Object.freeze(results), smokes: smokeNames.length });
  } catch (error) {
    if (error instanceof GodotPlayableHarnessError) {
      throw error;
    }
    fail("GODOT_3D_HARNESS_INTERNAL_ERROR");
  }
}

export function parsePlayableCaptureArguments(args) {
  if (!Array.isArray(args) || ![4, 5].includes(args.length) || args[0] !== "--example" ||
      args[2] !== "--output" || (args.length === 5 && args[4] !== "--narrow")) {
    fail("GODOT_3D_CAPTURE_ARGUMENT_ERROR");
  }
  const example = parsePlayableExampleArguments(args.slice(0, 2));
  const output = args[3];
  if (typeof output !== "string" || output.includes("\0")) {
    fail("GODOT_3D_CAPTURE_OUTPUT_INVALID");
  }
  const nativeAbsolute = process.platform === "win32"
    ? path.win32.isAbsolute(output)
    : path.posix.isAbsolute(output);
  if (!nativeAbsolute) {
    fail("GODOT_3D_CAPTURE_OUTPUT_INVALID");
  }
  return Object.freeze({
    example,
    output,
    width: args.length === 5 ? PLAYABLE_NARROW_WIDTH : PLAYABLE_CAPTURE_WIDTH,
  });
}

export function validatePlayableCaptureOutput(output, { temporaryRoot = captureBase() } = {}) {
  const trustedRoot = fs.realpathSync(temporaryRoot);
  const candidate = path.resolve(output);
  const parent = fs.realpathSync(path.dirname(candidate));
  if (!isContained(trustedRoot, candidate) || (parent !== trustedRoot && !isContained(trustedRoot, parent)) ||
      fs.existsSync(candidate)) {
    fail("GODOT_3D_CAPTURE_OUTPUT_INVALID");
  }
  return candidate;
}

export function configurePlayableViewport(projectRoot, width) {
  if (![PLAYABLE_CAPTURE_WIDTH, PLAYABLE_NARROW_WIDTH].includes(width)) {
    fail("GODOT_3D_CAPTURE_FRAME_INVALID");
  }
  if (width === PLAYABLE_CAPTURE_WIDTH) {
    return;
  }
  const projectFile = path.join(projectRoot, "project.godot");
  let source = fs.readFileSync(projectFile, "utf8");
  for (const key of ["viewport_width", "window_width_override"]) {
    const setting = `${key}=${PLAYABLE_CAPTURE_WIDTH}`;
    if (source.split(setting).length !== 2) {
      fail("GODOT_3D_CAPTURE_PROJECT_INVALID");
    }
    source = source.replace(setting, `${key}=${PLAYABLE_NARROW_WIDTH}`);
  }
  fs.writeFileSync(projectFile, source, "utf8");
}

export function inspectPlayableCapture(output, expectedWidth) {
  const pattern = new RegExp(`^${PLAYABLE_CAPTURE_PREFIX}\\d+\\.png$`, "u");
  const names = fs.readdirSync(output).filter((name) => pattern.test(name)).sort();
  if (names.length !== PLAYABLE_CAPTURE_FRAMES) {
    fail("GODOT_3D_CAPTURE_FRAME_COUNT_INVALID");
  }
  const pngHeader = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const frames = names.map((name) => {
    const bytes = fs.readFileSync(path.join(output, name));
    if (bytes.length < 24 || !bytes.subarray(0, 8).equals(pngHeader) ||
        bytes.toString("ascii", 12, 16) !== "IHDR" ||
        bytes.readUInt32BE(16) !== expectedWidth ||
        bytes.readUInt32BE(20) !== PLAYABLE_CAPTURE_HEIGHT) {
      fail("GODOT_3D_CAPTURE_FRAME_INVALID");
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
    height: PLAYABLE_CAPTURE_HEIGHT,
    fps: PLAYABLE_CAPTURE_FPS,
    frameCount: frames.length,
    frames: Object.freeze(frames),
  });
}
