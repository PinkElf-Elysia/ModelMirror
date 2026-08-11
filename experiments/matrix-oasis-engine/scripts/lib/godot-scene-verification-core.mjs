import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {spawnSync} from "node:child_process";
import {isDeepStrictEqual} from "node:util";
import {assertGodotOutputClean, runGodotCommand} from "./godot-core.mjs";
import {buildScenePack, sceneGodotArguments} from "./godot-scene-core.mjs";

export const SCENE_TRACE_MARKER = "MATRIX_OASIS_R7_SCENE_TRACE_JSON:";
export const SCENE_CAPTURE_WIDTH = 960;
export const SCENE_NARROW_WIDTH = 640;
export const SCENE_CAPTURE_HEIGHT = 540;
export const SCENE_CAPTURE_FPS = 30;
export const SCENE_CAPTURE_FRAMES = 12;
export const SCENE_CAPTURE_PREFIX = "scene-lab";

export class GodotSceneVerificationError extends Error {
  constructor(code) {
    super(code);
    this.name = "GodotSceneVerificationError";
    this.code = code;
  }
}

function fail(code) {
  throw new GodotSceneVerificationError(code);
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

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function temporaryBase(moduleRoot) {
  return process.platform === "win32" ? path.join(path.parse(moduleRoot).root, "tmp") : os.tmpdir();
}

function isContained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

function ownedIdentity(candidate) {
  const stat = fs.lstatSync(candidate, {bigint: true});
  return Object.freeze({dev: stat.dev, ino: stat.ino});
}

function removeOwnedRoot(temporaryRoot, moduleRoot, identity, prefix) {
  const base = fs.realpathSync(temporaryBase(moduleRoot));
  const candidate = fs.realpathSync(temporaryRoot);
  const stat = fs.lstatSync(candidate, {bigint: true});
  if (!isContained(base, candidate) || stat.isSymbolicLink() || stat.dev !== identity.dev ||
      stat.ino !== identity.ino || !path.basename(candidate).startsWith(prefix)) {
    fail("GODOT_SCENE_TEMPORARY_ROOT_INVALID");
  }
  fs.rmSync(candidate, {recursive: true});
}

function bindingFor(scenePack, nodeId) {
  return scenePack.nodeBindings.find((binding) => binding.nodeId === nodeId) ?? null;
}

function sceneProjection({scenePack, manifestSha256, artifactSha256, inspection, activeNodeId}) {
  const nextActiveNodeId = inspection.status === "active" ? inspection.location.id : activeNodeId;
  const binding = bindingFor(scenePack, nextActiveNodeId);
  if (!binding) {
    fail("GODOT_SCENE_REFERENCE_BINDING_INVALID");
  }
  const visible = new Set(binding.visiblePlacementIds);
  return Object.freeze({
    activeNodeId: nextActiveNodeId,
    state: Object.freeze({
      manifestSha256,
      runtimeArtifactSha256: artifactSha256,
      status: inspection.status,
      locationId: inspection.location.id,
      visiblePlacementIds: Object.freeze(scenePack.placements.map(({id}) => id).filter((id) => visible.has(id))),
      playerSpawn: freezeJson(JSON.parse(JSON.stringify(binding.playerSpawn))),
      actionAnchor: freezeJson(JSON.parse(JSON.stringify(binding.actionAnchor))),
      terminalCount: inspection.actions.length,
    }),
  });
}

function referenceSceneTrace(runtimeTrace, scenePack, sceneText, artifactSha256) {
  let inspection = runtimeTrace.created.inspection;
  let projection = sceneProjection({
    scenePack,
    manifestSha256: sha256(Buffer.from(sceneText, "utf8")),
    artifactSha256,
    inspection,
    activeNodeId: "",
  });
  const created = Object.freeze({runtime: runtimeTrace.created, scene: projection.state});
  const steps = [];
  for (const runtime of runtimeTrace.steps) {
    if (runtime.ok) {
      inspection = runtime.inspection;
    }
    projection = sceneProjection({
      scenePack,
      manifestSha256: created.scene.manifestSha256,
      artifactSha256,
      inspection,
      activeNodeId: projection.activeNodeId,
    });
    steps.push(Object.freeze({runtime, scene: projection.state}));
  }
  return freezeJson({traceVersion: 1, created, steps});
}

export function buildSceneParityCases({runtimeCases, canonicalizeSceneJson}) {
  if (!Array.isArray(runtimeCases) || runtimeCases.length < 1 || typeof canonicalizeSceneJson !== "function") {
    fail("GODOT_SCENE_CASE_INPUT_INVALID");
  }
  return Object.freeze(runtimeCases.map((item) => {
    let runtimePack;
    let receipt;
    try {
      runtimePack = JSON.parse(item.runtimeText);
      receipt = JSON.parse(item.receiptText);
    } catch {
      fail("GODOT_SCENE_CASE_INPUT_INVALID");
    }
    const example = item.name.startsWith("last-train-") ? "last-train-r1" : "mechanics-conformance";
    const scenePack = buildScenePack({example, runtimePack, receipt});
    const sceneText = canonicalizeSceneJson(scenePack);
    return Object.freeze({
      ...item,
      sceneText,
      referenceSceneTrace: referenceSceneTrace(
        item.referenceTrace,
        scenePack,
        sceneText,
        receipt.artifact.sha256,
      ),
    });
  }));
}

export function parseGodotSceneTrace(output, status) {
  const text = typeof output === "string" ? output : "";
  if (status !== 0 || text.split(SCENE_TRACE_MARKER).length - 1 !== 1) {
    fail("GODOT_SCENE_TRACE_MARKER_INVALID");
  }
  const line = text.split(/\r?\n/u).find((item) => item.includes(SCENE_TRACE_MARKER));
  let trace;
  try {
    trace = JSON.parse(line.slice(line.indexOf(SCENE_TRACE_MARKER) + SCENE_TRACE_MARKER.length));
  } catch {
    fail("GODOT_SCENE_TRACE_REPORT_INVALID");
  }
  if (!exactKeys(trace, ["created", "steps", "traceVersion"]) || trace.traceVersion !== 1 ||
      !exactKeys(trace.created, ["runtime", "scene"]) || trace.created.runtime?.ok !== true ||
      !Array.isArray(trace.steps) || trace.steps.some((step) => !exactKeys(step, ["runtime", "scene"]))) {
    fail("GODOT_SCENE_TRACE_REPORT_INVALID");
  }
  return freezeJson(trace);
}

export function runGodotSceneCases({
  moduleRoot,
  sourceProjectRoot,
  godotCommand,
  cases,
  runTraces = true,
  spawn = spawnSync,
}) {
  if (!Array.isArray(cases) || cases.length < 1 || typeof runTraces !== "boolean") {
    fail("GODOT_SCENE_CASE_INPUT_INVALID");
  }
  const base = temporaryBase(moduleRoot);
  fs.mkdirSync(base, {recursive: true});
  const prefix = "matrix-oasis-r7-scene-parity-";
  const temporaryRoot = fs.mkdtempSync(path.join(base, prefix));
  const identity = ownedIdentity(temporaryRoot);
  const projectRoot = path.join(temporaryRoot, "runtime-godot");
  const inputsRoot = path.join(temporaryRoot, "inputs");
  const assetsSource = path.join(moduleRoot, "examples", "scene-bundles", "kenney-prototype", "assets");
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
      const assetsRoot = path.join(caseRoot, "assets");
      fs.mkdirSync(caseRoot);
      fs.cpSync(assetsSource, assetsRoot, {recursive: true});
      const runtimePath = path.join(caseRoot, "runtime.json");
      const receiptPath = path.join(caseRoot, "receipt.json");
      const scenePath = path.join(caseRoot, "scene.json");
      fs.writeFileSync(runtimePath, item.runtimeText, {encoding: "utf8", flag: "wx"});
      fs.writeFileSync(receiptPath, item.receiptText, {encoding: "utf8", flag: "wx"});
      fs.writeFileSync(scenePath, item.sceneText, {encoding: "utf8", flag: "wx"});
      caseFiles.set(item.name, {runtimePath, receiptPath, scenePath});
      const serializations = [];
      for (let repetition = 0; runTraces && repetition < item.repetitions; repetition += 1) {
        const processResult = spawn(godotCommand, [
          "--headless",
          "--path",
          projectRoot,
          "--script",
          "res://scene_binding/scene_trace_runner.gd",
          "--",
          `--matrix-oasis-runtime-pack=${runtimePath}`,
          `--matrix-oasis-runtime-receipt=${receiptPath}`,
          `--matrix-oasis-scene-pack=${scenePath}`,
          `--matrix-oasis-scene-trace-step-limit=${item.stepLimit}`,
          ...item.actions.map((actionId) => `--matrix-oasis-scene-trace-action=${actionId}`),
        ], {
          cwd: moduleRoot,
          encoding: "utf8",
          maxBuffer: 16 * 1024 * 1024,
          shell: false,
          timeout: 45_000,
          windowsHide: true,
        });
        if (processResult.error || processResult.status !== 0) {
          fail("GODOT_SCENE_TRACE_COMMAND_FAILED");
        }
        const output = `${processResult.stdout ?? ""}${processResult.stderr ?? ""}`;
        assertGodotOutputClean(output);
        const trace = parseGodotSceneTrace(output, processResult.status);
        if (!isDeepStrictEqual(trace, item.referenceSceneTrace)) {
          fail("GODOT_SCENE_TRACE_MISMATCH");
        }
        serializations.push(JSON.stringify(trace));
      }
      if (runTraces && new Set(serializations).size !== 1) {
        fail("GODOT_SCENE_TRACE_NONDETERMINISTIC");
      }
      if (runTraces) {
        results.push(Object.freeze({name: item.name, repetitions: item.repetitions}));
      }
    }
    for (const name of ["mechanics-complete-with-failures", "last-train-return"]) {
      const pair = caseFiles.get(name);
      if (!pair) {
        fail("GODOT_SCENE_SMOKE_INPUT_INVALID");
      }
      const processResult = spawn(godotCommand, sceneGodotArguments({
        projectRoot,
        runtimePath: pair.runtimePath,
        receiptPath: pair.receiptPath,
        scenePath: pair.scenePath,
        smoke: true,
      }), {
        cwd: moduleRoot,
        encoding: "utf8",
        maxBuffer: 8 * 1024 * 1024,
        shell: false,
        timeout: 45_000,
        windowsHide: true,
      });
      if (processResult.error || processResult.status !== 0 ||
          `${processResult.stdout ?? ""}${processResult.stderr ?? ""}`.split("MATRIX_OASIS_R7_SCENE_BINDING_READY").length - 1 !== 1) {
        fail("GODOT_SCENE_SMOKE_FAILED");
      }
      assertGodotOutputClean(`${processResult.stdout ?? ""}${processResult.stderr ?? ""}`);
    }
    removeOwnedRoot(temporaryRoot, moduleRoot, identity, prefix);
    return Object.freeze({results: Object.freeze(results), smokes: 2});
  } catch (error) {
    if (error instanceof GodotSceneVerificationError) {
      throw error;
    }
    fail("GODOT_SCENE_HARNESS_INTERNAL_ERROR");
  }
}

export function parseSceneCaptureArguments(args) {
  if (!Array.isArray(args) || ![4, 5].includes(args.length) || args[0] !== "--example" ||
      args[2] !== "--output" || (args.length === 5 && args[4] !== "--narrow") ||
      !["mechanics-conformance", "last-train-r1"].includes(args[1]) ||
      typeof args[3] !== "string" || args[3].includes("\0")) {
    fail("GODOT_SCENE_CAPTURE_ARGUMENT_ERROR");
  }
  const absolute = process.platform === "win32" ? path.win32.isAbsolute(args[3]) : path.posix.isAbsolute(args[3]);
  if (!absolute) {
    fail("GODOT_SCENE_CAPTURE_OUTPUT_INVALID");
  }
  return Object.freeze({
    example: args[1],
    output: args[3],
    width: args.length === 5 ? SCENE_NARROW_WIDTH : SCENE_CAPTURE_WIDTH,
  });
}

export function validateSceneCaptureOutput(output, {temporaryRoot = temporaryBase(path.parse(output).root)} = {}) {
  const trustedRoot = fs.realpathSync(temporaryRoot);
  const candidate = path.resolve(output);
  const parent = fs.realpathSync(path.dirname(candidate));
  if (!isContained(trustedRoot, candidate) || (parent !== trustedRoot && !isContained(trustedRoot, parent)) || fs.existsSync(candidate)) {
    fail("GODOT_SCENE_CAPTURE_OUTPUT_INVALID");
  }
  return candidate;
}

export function configureSceneViewport(projectRoot, width) {
  if (![SCENE_CAPTURE_WIDTH, SCENE_NARROW_WIDTH].includes(width)) {
    fail("GODOT_SCENE_CAPTURE_FRAME_INVALID");
  }
  if (width === SCENE_CAPTURE_WIDTH) {
    return;
  }
  const projectFile = path.join(projectRoot, "project.godot");
  let source = fs.readFileSync(projectFile, "utf8");
  for (const key of ["viewport_width", "window_width_override"]) {
    const setting = `${key}=${SCENE_CAPTURE_WIDTH}`;
    if (source.split(setting).length !== 2) {
      fail("GODOT_SCENE_CAPTURE_PROJECT_INVALID");
    }
    source = source.replace(setting, `${key}=${SCENE_NARROW_WIDTH}`);
  }
  fs.writeFileSync(projectFile, source, "utf8");
}

export function inspectSceneCapture(output, expectedWidth) {
  const pattern = new RegExp(`^${SCENE_CAPTURE_PREFIX}\\d+\\.png$`, "u");
  const names = fs.readdirSync(output).filter((name) => pattern.test(name)).sort();
  if (names.length !== SCENE_CAPTURE_FRAMES) {
    fail("GODOT_SCENE_CAPTURE_FRAME_COUNT_INVALID");
  }
  const header = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const frames = names.map((name) => {
    const bytes = fs.readFileSync(path.join(output, name));
    if (bytes.length < 24 || !bytes.subarray(0, 8).equals(header) || bytes.toString("ascii", 12, 16) !== "IHDR" ||
        bytes.readUInt32BE(16) !== expectedWidth || bytes.readUInt32BE(20) !== SCENE_CAPTURE_HEIGHT) {
      fail("GODOT_SCENE_CAPTURE_FRAME_INVALID");
    }
    return Object.freeze({file: name, bytes: bytes.length, sha256: sha256(bytes)});
  });
  return Object.freeze({
    captureVersion: 1,
    width: expectedWidth,
    height: SCENE_CAPTURE_HEIGHT,
    fps: SCENE_CAPTURE_FPS,
    frameCount: frames.length,
    frames: Object.freeze(frames),
  });
}
