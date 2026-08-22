import fs from "node:fs";
import { spawnSync } from "node:child_process";
import { lstat, mkdir, mkdtemp, open, readFile, readdir, realpath, rename, rm, rmdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import { assemblePrototypeSpatialScene } from "@matrix-oasis/prototype-spatial-assembler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { recoverPrototypeRuns } from "./lib/prototype-cache-core.mjs";
import {
  loadVerifiedR14SpatialPrototypeRun,
  loadVerifiedSolvedSpatialPrototypeRun,
  recoverSolvedSpatialPrototypeRuns,
} from "./lib/solved-spatial-cache-core.mjs";
import { assertGodotOutputClean, resolveGodotBinary, runGodotCommand } from "./lib/godot-core.mjs";
import {
  configurePlayableViewport,
  inspectPlayableCapture,
  PLAYABLE_CAPTURE_FPS,
  PLAYABLE_CAPTURE_FRAMES,
  PLAYABLE_CAPTURE_HEIGHT,
  PLAYABLE_CAPTURE_PREFIX,
  PLAYABLE_CAPTURE_WIDTH,
  PLAYABLE_NARROW_WIDTH,
} from "./lib/godot-playable-core.mjs";
import { r14GodotArguments, R14_PREVIEW_READY_MARKER } from "./lib/r14-preview-core.mjs";
import { createRuntimePreviewProject, removeRuntimePreviewProject } from "./prepare-godot-runtime.mjs";
import { configureGdgsProject } from "./verify-godot-splat.mjs";
import { copySpatialPreviewFiles } from "./preview-spatial-prototype.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const temporaryRoot = path.resolve(path.parse(moduleRoot).root, "tmp");
const services = Object.freeze({ lstat, mkdir, mkdtemp, openFile: open, readFile, readdir, realpath, rename, rm, rmdir });

export function parseR14CaptureArguments(args, tempRoot = temporaryRoot) {
  if (!Array.isArray(args) || ![8, 9].includes(args.length)) throw new Error("R14_CAPTURE_ARGUMENT_INVALID");
  const narrow = args.length === 9 && args[8] === "--narrow";
  if (args.length === 9 && !narrow) throw new Error("R14_CAPTURE_ARGUMENT_INVALID");
  const names = Object.freeze({
    "--prototype-run-root": "prototypeRunRoot",
    "--spatial-run-root": "spatialRunRoot",
    "--solved-run-root": "solvedRunRoot",
    "--output": "output",
  });
  const values = Object.create(null);
  for (let index = 0; index < 8; index += 2) {
    const name = names[args[index]]; const value = args[index + 1];
    if (!name || Object.hasOwn(values, name) || typeof value !== "string" || value.includes("\0") ||
        !path.isAbsolute(value)) throw new Error("R14_CAPTURE_ARGUMENT_INVALID");
    values[name] = path.resolve(value);
  }
  const root = path.resolve(tempRoot);
  if (Object.keys(values).length !== 4 || Object.values(values).some((value) => path.dirname(value) !== root)) {
    throw new Error("R14_CAPTURE_ARGUMENT_INVALID");
  }
  return Object.freeze({ ...values, width: narrow ? PLAYABLE_NARROW_WIDTH : PLAYABLE_CAPTURE_WIDTH });
}

export function r14CaptureGodotArguments({ projectRoot, runDirectory, output, width }) {
  if (![projectRoot, runDirectory, output].every((value) => typeof value === "string" && path.isAbsolute(value)) ||
      ![PLAYABLE_CAPTURE_WIDTH, PLAYABLE_NARROW_WIDTH].includes(width)) throw new Error("R14_CAPTURE_ARGUMENT_INVALID");
  const base = r14GodotArguments({ projectRoot, runDirectory });
  return Object.freeze([
    base[0], base[1],
    "--write-movie", path.join(output, `${PLAYABLE_CAPTURE_PREFIX}.png`),
    "--fixed-fps", String(PLAYABLE_CAPTURE_FPS),
    "--resolution", `${width}x${PLAYABLE_CAPTURE_HEIGHT}`,
    "--quit-after", String(PLAYABLE_CAPTURE_FRAMES),
    ...base.slice(2),
  ]);
}

function runCaptureCommand({ command, args, output }) {
  const result = spawnSync(command, args, { cwd: moduleRoot, encoding: "utf8", maxBuffer: 16 * 1024 * 1024,
    shell: false, timeout: 120_000, windowsHide: true });
  const text = `${result.stdout ?? ""}${result.stderr ?? ""}`;
  if (result.error || result.status !== 0) {
    fs.writeFileSync(path.join(output, "capture-failure.log"), text, { encoding: "utf8", flag: "wx" });
    throw new Error("R14_CAPTURE_GODOT_FAILED");
  }
  return text;
}

async function main() {
  const parsed = parseR14CaptureArguments(process.argv.slice(2));
  if (fs.existsSync(parsed.output)) throw new Error("R14_CAPTURE_OUTPUT_EXISTS");
  const sourceOptions = Object.freeze({
    loadVerifiedSpatialPrototypeRun: loadVerifiedR14SpatialPrototypeRun,
    cacheOptions: Object.freeze({
      runRoot: parsed.spatialRunRoot, prototypeRunRoot: parsed.prototypeRunRoot, temporaryRoot, services,
      recoverPrototypeRuns, assemblePrototypeScene, assemblePrototypeSpatialScene, canonicalizeJsonValue,
    }),
  });
  const common = Object.freeze({
    runRoot: parsed.solvedRunRoot, temporaryRoot, sourceOptions, services, canonicalizeJsonValue,
  });
  const recovered = await recoverSolvedSpatialPrototypeRuns(common);
  const selected = recovered.runs.find((run) => run.runId === recovered.currentRunId) ?? recovered.runs[0];
  if (!selected) throw new Error("R14_CAPTURE_CACHE_INVALID");
  const verified = await loadVerifiedSolvedSpatialPrototypeRun({ runId: selected.runId, ...common });
  await mkdir(parsed.output, { recursive: false });
  const project = createRuntimePreviewProject({ moduleRoot });
  try {
    configureGdgsProject(project.projectRoot);
    configurePlayableViewport(project.projectRoot, parsed.width);
    const runDirectory = await copySpatialPreviewFiles(project.projectRoot, verified.previewFiles,
      { mkdir, openFile: open, lstat, realpath });
    const godot = resolveGodotBinary();
    const imported = runGodotCommand({ command: godot.command,
      args: ["--headless", "--editor", "--path", project.projectRoot, "--quit"], cwd: moduleRoot, timeout: 120_000 });
    try { assertGodotOutputClean(imported); }
    catch {
      fs.writeFileSync(path.join(parsed.output, "capture-failure.log"), imported, { encoding: "utf8", flag: "wx" });
      throw new Error("R14_CAPTURE_GODOT_IMPORT_FAILED");
    }
    const captured = runCaptureCommand({ command: godot.command,
      args: r14CaptureGodotArguments({ projectRoot: project.projectRoot, runDirectory, output: parsed.output,
        width: parsed.width }), output: parsed.output });
    try { assertGodotOutputClean(captured); }
    catch {
      fs.writeFileSync(path.join(parsed.output, "capture-failure.log"), captured, { encoding: "utf8", flag: "wx" });
      throw new Error("R14_CAPTURE_GODOT_OUTPUT_INVALID");
    }
    if (captured.split(R14_PREVIEW_READY_MARKER).length - 1 !== 1) {
      fs.writeFileSync(path.join(parsed.output, "capture-failure.log"), captured, { encoding: "utf8", flag: "wx" });
      throw new Error("R14_CAPTURE_MARKER_INVALID");
    }
    const report = Object.freeze({ ...inspectPlayableCapture(parsed.output, parsed.width), runId: selected.runId,
      solutionSha256: selected.solutionSha256 });
    fs.writeFileSync(path.join(parsed.output, "capture-manifest.json"), `${JSON.stringify(report, null, 2)}\n`,
      { encoding: "utf8", flag: "wx" });
    process.stdout.write(`R14_CAPTURE_OK run=${selected.runId} frames=${report.frameCount} size=${report.width}x${report.height}\n`);
  } finally {
    try { removeRuntimePreviewProject(project.temporaryRoot, { moduleRoot, identity: project.identity }); }
    catch { /* retain a raced preview project rather than broad cleanup */ }
  }
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) {
  main().catch((error) => {
    const candidate = typeof error?.code === "string" ? error.code : error?.message;
    const code = typeof candidate === "string" && /^(?:R14_CAPTURE|GODOT)_[A-Z0-9_]+$/u.test(candidate)
      ? candidate : "R14_CAPTURE_INTERNAL_ERROR";
    process.stderr.write(`${code}\n`); process.exitCode = 2;
  });
}
