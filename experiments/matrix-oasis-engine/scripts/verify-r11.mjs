import { createHash } from "node:crypto";
import fs from "node:fs";
import { lstat, mkdir, mkdtemp, open, readdir, realpath, rename, rm, rmdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import { assemblePrototypeSpatialScene } from "@matrix-oasis/prototype-spatial-assembler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { recoverPrototypeRuns } from "./lib/prototype-cache-core.mjs";
import { loadVerifiedSpatialPrototypeRun, recoverSpatialPrototypeRuns } from "./lib/spatial-cache-core.mjs";
import { assertGodotOutputClean, resolveGodotBinary, runGodotCommand } from "./lib/godot-core.mjs";
import { createRuntimePreviewProject, removeRuntimePreviewProject } from "./prepare-godot-runtime.mjs";
import { copySpatialPreviewFiles, spatialPrototypeGodotArguments } from "./preview-spatial-prototype.mjs";
import { configureGdgsProject } from "./verify-godot-splat.mjs";

export const SPATIAL_QUALIFICATION_MARKER = "MATRIX_OASIS_R11_SPATIAL_QUALIFICATION_JSON:";
export const SPATIAL_CAPTURE_PREFIX = "spatial-lab";
export const SPATIAL_QUALIFICATION_WIDTH = 960;
export const SPATIAL_QUALIFICATION_HEIGHT = 540;
export const SPATIAL_QUALIFICATION_POINT_COUNT = 640_000;
export const SPATIAL_QUALIFICATION_SAMPLE_FRAMES = 300;
export const SPATIAL_QUALIFICATION_MINIMUM_FPS_MILLI = 30_000;
const CAPTURE_FRAMES = 12;
const CAPTURE_WARMUP_FRAMES = 120;
const CAPTURE_FPS = 30;
const RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;
const ARGUMENTS = Object.freeze({
  "--prototype-run-root": "prototypeRunRoot",
  "--spatial-run-root": "spatialRunRoot",
  "--output": "output",
});
const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const temporaryRoot = path.resolve(path.parse(moduleRoot).root, "tmp");
const services = Object.freeze({ lstat, mkdir, mkdtemp, openFile: open, readdir, realpath, rename, rm, rmdir });

export class SpatialQualificationError extends Error {
  constructor(code = "SPATIAL_QUALIFICATION_INTERNAL_ERROR") {
    super(code);
    this.name = "SpatialQualificationError";
    this.code = code;
  }
}

function fail(code) { throw new SpatialQualificationError(code); }
function contained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative !== "" && !relative.startsWith("..") && !path.isAbsolute(relative);
}

export function parseSpatialQualificationArguments(args, tempRoot = temporaryRoot) {
  if (!Array.isArray(args) || args.length !== 6 || !path.isAbsolute(tempRoot)) fail("SPATIAL_QUALIFICATION_ARGUMENT_INVALID");
  const values = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const name = ARGUMENTS[args[index]]; const value = args[index + 1];
    if (!name || name in values || typeof value !== "string" || value.length === 0 || value.includes("\0") || !path.isAbsolute(value)) {
      fail("SPATIAL_QUALIFICATION_ARGUMENT_INVALID");
    }
    values[name] = path.resolve(value);
  }
  const root = path.resolve(tempRoot);
  if (Object.keys(values).length !== 3 || Object.values(values).some((value) => path.dirname(value) !== root) ||
      !/^[a-z0-9][a-z0-9._-]{0,127}$/u.test(path.basename(values.output))) fail("SPATIAL_QUALIFICATION_ARGUMENT_INVALID");
  return Object.freeze(values);
}

function exactKeys(value, keys) {
  return value && Object.getPrototypeOf(value) === Object.prototype && Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key));
}

export function parseSpatialQualificationReport(output, status = 0) {
  const text = typeof output === "string" ? output : "";
  if (status !== 0 || text.split(SPATIAL_QUALIFICATION_MARKER).length - 1 !== 1) fail("SPATIAL_QUALIFICATION_MARKER_INVALID");
  const line = text.split(/\r?\n/u).find((item) => item.startsWith(SPATIAL_QUALIFICATION_MARKER));
  let report;
  try { report = JSON.parse(line.slice(SPATIAL_QUALIFICATION_MARKER.length)); }
  catch { fail("SPATIAL_QUALIFICATION_REPORT_INVALID"); }
  const keys = ["qualificationVersion", "width", "height", "pointCount", "warmupFrames", "sampleFrames",
    "drawnFrames", "medianFrameUsec", "medianFpsMilli"];
  if (!exactKeys(report, keys) || report.qualificationVersion !== 1 || report.width !== SPATIAL_QUALIFICATION_WIDTH ||
      report.height !== SPATIAL_QUALIFICATION_HEIGHT || report.pointCount !== SPATIAL_QUALIFICATION_POINT_COUNT ||
      report.warmupFrames !== 120 || report.sampleFrames !== SPATIAL_QUALIFICATION_SAMPLE_FRAMES ||
      report.drawnFrames < SPATIAL_QUALIFICATION_SAMPLE_FRAMES || report.drawnFrames > SPATIAL_QUALIFICATION_SAMPLE_FRAMES + 2 ||
      !Number.isSafeInteger(report.medianFrameUsec) || report.medianFrameUsec < 1 ||
      !Number.isSafeInteger(report.medianFpsMilli) || report.medianFpsMilli < 1) {
    fail("SPATIAL_QUALIFICATION_REPORT_INVALID");
  }
  if (report.medianFpsMilli < SPATIAL_QUALIFICATION_MINIMUM_FPS_MILLI) {
    fail("SPATIAL_QUALIFICATION_PERFORMANCE_BELOW_MINIMUM");
  }
  return Object.freeze({ ...report });
}

async function inspectFrames(output) {
  const pattern = new RegExp(`^${SPATIAL_CAPTURE_PREFIX}\\d+\\.png$`, "u");
  const names = fs.readdirSync(output).filter((name) => pattern.test(name)).sort();
  if (names.length !== CAPTURE_WARMUP_FRAMES + CAPTURE_FRAMES) fail("SPATIAL_QUALIFICATION_CAPTURE_INVALID");
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const inspected = names.map((name) => {
    const bytes = fs.readFileSync(path.join(output, name));
    if (bytes.length < 24 || !bytes.subarray(0, 8).equals(signature) || bytes.toString("ascii", 12, 16) !== "IHDR" ||
        bytes.readUInt32BE(16) !== SPATIAL_QUALIFICATION_WIDTH || bytes.readUInt32BE(20) !== SPATIAL_QUALIFICATION_HEIGHT) {
      fail("SPATIAL_QUALIFICATION_CAPTURE_INVALID");
    }
    return Object.freeze({ file: name, byteLength: bytes.length,
      sha256: `sha256:${createHash("sha256").update(bytes).digest("hex")}` });
  });
  for (const frame of inspected.slice(0, CAPTURE_WARMUP_FRAMES)) fs.unlinkSync(path.join(output, frame.file));
  const retained = inspected.slice(CAPTURE_WARMUP_FRAMES);
  const pixels = [];
  for (const frame of retained) {
    const decoded = await sharp(path.join(output, frame.file)).removeAlpha().raw().toBuffer();
    if (decoded.length !== SPATIAL_QUALIFICATION_WIDTH * SPATIAL_QUALIFICATION_HEIGHT * 3) {
      fail("SPATIAL_QUALIFICATION_CAPTURE_INVALID");
    }
    pixels.push(decoded);
  }
  let maximumMeanAbsoluteDifferenceMilli = 0;
  let minimumMeanLumaMilli = Number.POSITIVE_INFINITY;
  for (let frame = 0; frame < pixels.length; frame += 1) {
    let luma = 0;
    let difference = 0;
    for (let index = 0; index < pixels[frame].length; index += 1) {
      luma += pixels[frame][index];
      if (frame > 0) difference += Math.abs(pixels[frame][index] - pixels[frame - 1][index]);
    }
    minimumMeanLumaMilli = Math.min(
      minimumMeanLumaMilli,
      Math.round(luma * 1000 / pixels[frame].length),
    );
    if (frame > 0) {
      maximumMeanAbsoluteDifferenceMilli = Math.max(
        maximumMeanAbsoluteDifferenceMilli,
        Math.round(difference * 1000 / pixels[frame].length),
      );
    }
  }
  if (minimumMeanLumaMilli < 1000 || maximumMeanAbsoluteDifferenceMilli > 1500) {
    fail("SPATIAL_QUALIFICATION_VISUAL_STABILITY_FAILED");
  }
  return Object.freeze({
    frames: Object.freeze(retained),
    stability: Object.freeze({
      profile: "static-consecutive-rgb-mad-v1",
      minimumMeanLumaMilli,
      maximumMeanAbsoluteDifferenceMilli,
      maximumAllowedDifferenceMilli: 1500,
    }),
  });
}

async function main() {
  let previewProject = null; let stageCode = "SPATIAL_QUALIFICATION_ARGUMENT_INVALID";
  try {
    const parsed = parseSpatialQualificationArguments(process.argv.slice(2));
    stageCode = "SPATIAL_QUALIFICATION_INPUT_INVALID";
    const tempReal = path.resolve(await realpath(temporaryRoot));
    for (const rootPath of [parsed.prototypeRunRoot, parsed.spatialRunRoot]) {
      const resolved = path.resolve(await realpath(rootPath)); const stat = await lstat(rootPath, { bigint: true });
      if (resolved !== rootPath || path.dirname(resolved) !== tempReal || !stat.isDirectory() || stat.isSymbolicLink()) {
        fail("SPATIAL_QUALIFICATION_INPUT_INVALID");
      }
    }
    if (fs.existsSync(parsed.output) || path.dirname(parsed.output) !== tempReal || !contained(tempReal, parsed.output)) {
      fail("SPATIAL_QUALIFICATION_OUTPUT_INVALID");
    }
    stageCode = "SPATIAL_QUALIFICATION_CACHE_INVALID";
    const common = { runRoot: parsed.spatialRunRoot, prototypeRunRoot: parsed.prototypeRunRoot,
      temporaryRoot: tempReal, services, recoverPrototypeRuns, assemblePrototypeScene,
      assemblePrototypeSpatialScene, canonicalizeJsonValue };
    const recovered = await recoverSpatialPrototypeRuns(common);
    const runId = recovered.currentRunId;
    if (!RUN_ID.test(runId ?? "")) fail("SPATIAL_QUALIFICATION_INPUT_INVALID");
    const verified = await loadVerifiedSpatialPrototypeRun({ runId, ...common });
    stageCode = "SPATIAL_QUALIFICATION_GODOT_UNAVAILABLE";
    const godot = resolveGodotBinary();
    stageCode = "SPATIAL_QUALIFICATION_PROJECT_FAILED";
    previewProject = createRuntimePreviewProject({ moduleRoot });
    configureGdgsProject(previewProject.projectRoot);
    const runDirectory = await copySpatialPreviewFiles(previewProject.projectRoot, verified.previewFiles,
      { mkdir, openFile: open, lstat, realpath });
    const imported = runGodotCommand({ command: godot.command,
      args: ["--headless", "--editor", "--path", previewProject.projectRoot, "--quit"], cwd: moduleRoot, timeout: 300_000 });
    assertGodotOutputClean(imported);
    stageCode = "SPATIAL_QUALIFICATION_PERFORMANCE_FAILED";
    const performance = runGodotCommand({ command: godot.command,
      args: ["--resolution", `${SPATIAL_QUALIFICATION_WIDTH}x${SPATIAL_QUALIFICATION_HEIGHT}`,
        ...spatialPrototypeGodotArguments({ projectRoot: previewProject.projectRoot, runDirectory, qualification: true })],
      cwd: moduleRoot, timeout: 300_000 });
    assertGodotOutputClean(performance);
    const performanceReport = parseSpatialQualificationReport(performance);
    stageCode = "SPATIAL_QUALIFICATION_CAPTURE_FAILED";
    fs.mkdirSync(parsed.output);
    const captured = runGodotCommand({ command: godot.command,
      args: ["--write-movie", path.join(parsed.output, `${SPATIAL_CAPTURE_PREFIX}.png`), "--fixed-fps", String(CAPTURE_FPS),
        "--resolution", `${SPATIAL_QUALIFICATION_WIDTH}x${SPATIAL_QUALIFICATION_HEIGHT}`, "--quit-after",
        String(CAPTURE_WARMUP_FRAMES + CAPTURE_FRAMES),
        ...spatialPrototypeGodotArguments({ projectRoot: previewProject.projectRoot, runDirectory, capture: true })],
      cwd: moduleRoot, timeout: 300_000 });
    assertGodotOutputClean(captured);
    if (captured.split("MATRIX_OASIS_R11_SPATIAL_READY").length - 1 !== 1) fail("SPATIAL_QUALIFICATION_READY_INVALID");
    const inspected = await inspectFrames(parsed.output);
    const report = Object.freeze({ reportVersion: 1, runId, godotVersion: godot.version,
      performance: performanceReport, capture: Object.freeze({ width: SPATIAL_QUALIFICATION_WIDTH,
        height: SPATIAL_QUALIFICATION_HEIGHT, fixedFps: CAPTURE_FPS, discardedWarmupFrames: CAPTURE_WARMUP_FRAMES,
        frameCount: inspected.frames.length, frames: inspected.frames,
        stability: inspected.stability }) });
    fs.writeFileSync(path.join(parsed.output, "qualification-report.json"), `${JSON.stringify(report, null, 2)}\n`, { flag: "wx" });
    removeRuntimePreviewProject(previewProject.temporaryRoot, { moduleRoot, identity: previewProject.identity });
    previewProject = null;
    process.stdout.write(`SPATIAL_QUALIFICATION_OK frames=${performanceReport.sampleFrames} medianFpsMilli=${performanceReport.medianFpsMilli} captures=${inspected.frames.length} visualMadMilli=${inspected.stability.maximumMeanAbsoluteDifferenceMilli}\n`);
  } catch (error) {
    const code = error instanceof SpatialQualificationError ? error.code : stageCode;
    process.stderr.write(`${code}\n`); process.exitCode = code === "SPATIAL_QUALIFICATION_ARGUMENT_INVALID" ? 2 : 1;
  } finally {
    if (previewProject) {
      try { removeRuntimePreviewProject(previewProject.temporaryRoot, { moduleRoot, identity: previewProject.identity }); }
      catch { /* preserve ambiguous one-time project */ }
    }
  }
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) await main();
