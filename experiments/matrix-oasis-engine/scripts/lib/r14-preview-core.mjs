import { spawn } from "node:child_process";
import { lstat, mkdir, mkdtemp, open, readdir, realpath, rename, rm, rmdir } from "node:fs/promises";
import path from "node:path";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import { assemblePrototypeSpatialScene } from "@matrix-oasis/prototype-spatial-assembler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { recoverPrototypeRuns } from "./prototype-cache-core.mjs";
import {
  findVerifiedSolvedSpatialPrototypeRun,
  loadVerifiedSolvedSpatialPrototypeRun,
  recoverSolvedSpatialPrototypeRuns,
} from "./solved-spatial-cache-core.mjs";
import { loadVerifiedSpatialPrototypeRun } from "./spatial-cache-core.mjs";
import { assertGodotOutputClean, runGodotCommand } from "./godot-core.mjs";
import { createRuntimePreviewProject, removeRuntimePreviewProject } from "../prepare-godot-runtime.mjs";
import { configureGdgsProject } from "../verify-godot-splat.mjs";
import { copySpatialPreviewFiles } from "../preview-spatial-prototype.mjs";

export const R14_PREVIEW_READY_MARKER = "MATRIX_OASIS_R14_SOLVED_SPATIAL_READY";
export const R14_PREVIEW_TRACE_MARKER = "MATRIX_OASIS_R14_SPATIAL_TRACE_JSON:";
const RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;
const defaultServices = Object.freeze({ lstat, mkdir, mkdtemp, openFile: open, readdir, realpath, rename, rm, rmdir });

function validAbsolute(value) { return typeof value === "string" && path.isAbsolute(value) && !value.includes("\0"); }

export function r14GodotArguments({ projectRoot, runDirectory, smoke = false }) {
  if (!validAbsolute(projectRoot) || !validAbsolute(runDirectory) || typeof smoke !== "boolean") {
    throw new Error("R14_PREVIEW_GODOT_ARGUMENT_INVALID");
  }
  return Object.freeze([
    ...(smoke ? ["--headless"] : []), "--path", projectRoot,
    "res://solved_spatial_prototype/solved_spatial_lab.tscn", "--",
    `--matrix-oasis-runtime-pack=${path.join(runDirectory, "runtime-game-pack.json")}`,
    `--matrix-oasis-runtime-receipt=${path.join(runDirectory, "runtime-receipt.json")}`,
    `--matrix-oasis-scene-pack=${path.join(runDirectory, "scene-pack.json")}`,
    `--matrix-oasis-spatial-assembly=${path.join(runDirectory, "spatial-assembly.json")}`,
    "--matrix-oasis-spatial-resource=res://spatial_run/assets/environment.compressed.ply",
    `--matrix-oasis-spatial-solution=${path.join(runDirectory, "spatial-solution.json")}`,
    `--matrix-oasis-spatial-verification=${path.join(runDirectory, "spatial-verification-report.json")}`,
    ...(smoke ? ["--matrix-oasis-r14-smoke"] : []),
  ]);
}

function diagnostic(code) {
  return Object.freeze({ ok: false, diagnostics: Object.freeze([Object.freeze({ code, path: "" })]) });
}

export function createR14PreviewOperations({
  prototypeRunRoot,
  spatialRunRoot,
  solvedRunRoot,
  godot,
  moduleRoot,
  temporaryRoot,
  services = defaultServices,
  cache = {},
  godotTools = {},
  spawnProcess = spawn,
  previewFiles = { mkdir, openFile: open, lstat, realpath },
}) {
  if (![prototypeRunRoot, spatialRunRoot, solvedRunRoot, moduleRoot, temporaryRoot].every(validAbsolute)) {
    throw new Error("R14_PREVIEW_ARGUMENT_INVALID");
  }
  const findSolved = cache.findVerifiedSolvedSpatialPrototypeRun ?? findVerifiedSolvedSpatialPrototypeRun;
  const recoverSolved = cache.recoverSolvedSpatialPrototypeRuns ?? recoverSolvedSpatialPrototypeRuns;
  const loadSolved = cache.loadVerifiedSolvedSpatialPrototypeRun ?? loadVerifiedSolvedSpatialPrototypeRun;
  const createProject = godotTools.createRuntimePreviewProject ?? createRuntimePreviewProject;
  const removeProject = godotTools.removeRuntimePreviewProject ?? removeRuntimePreviewProject;
  const configureProject = godotTools.configureGdgsProject ?? configureGdgsProject;
  const runGodot = godotTools.runGodotCommand ?? runGodotCommand;
  const assertClean = godotTools.assertGodotOutputClean ?? assertGodotOutputClean;
  const sourceOptions = Object.freeze({
    loadVerifiedSpatialPrototypeRun: cache.loadVerifiedSpatialPrototypeRun ?? loadVerifiedSpatialPrototypeRun,
    cacheOptions: Object.freeze({
      runRoot: spatialRunRoot, prototypeRunRoot, temporaryRoot, services,
      recoverPrototypeRuns, assemblePrototypeScene, assemblePrototypeSpatialScene, canonicalizeJsonValue,
    }),
  });
  const common = Object.freeze({ runRoot: solvedRunRoot, temporaryRoot, sourceOptions, services, canonicalizeJsonValue });
  let activePreview = null;

  async function cleanup(preview) {
    if (!preview) return;
    try { await removeProject(preview.project.temporaryRoot, { moduleRoot, identity: preview.project.identity }); }
    catch { /* a raced temporary project is retained */ }
  }

  async function stopLaunch() {
    const preview = activePreview; activePreview = null;
    if (!preview) return;
    if (preview.child.exitCode === null && preview.child.signalCode === null) {
      const exited = new Promise((resolve) => preview.child.once("exit", resolve));
      preview.child.kill();
      await Promise.race([exited, new Promise((resolve) => setTimeout(resolve, 5_000))]);
    }
    await cleanup(preview);
  }

  async function launch(runId) {
    if (!godot || !RUN_ID.test(runId)) return Object.freeze({ ok: false });
    let verified;
    try { verified = await loadSolved({ runId, ...common }); }
    catch { return Object.freeze({ ok: false }); }
    await stopLaunch();
    const project = createProject({ moduleRoot });
    try {
      configureProject(project.projectRoot);
      const runDirectory = await copySpatialPreviewFiles(project.projectRoot, verified.previewFiles, previewFiles);
      const imported = runGodot({ command: godot.command,
        args: ["--headless", "--editor", "--path", project.projectRoot, "--quit"], cwd: moduleRoot, timeout: 120_000 });
      assertClean(imported);
      const child = spawnProcess(godot.command, r14GodotArguments({ projectRoot: project.projectRoot, runDirectory }), {
        cwd: moduleRoot, shell: false, windowsHide: false, stdio: ["ignore", "pipe", "pipe"],
      });
      const preview = { child, project }; activePreview = preview; let output = ""; let settled = false;
      const started = await new Promise((resolve) => {
        const finish = (value) => { if (!settled) { settled = true; clearTimeout(timer); resolve(value); } };
        const collect = (chunk) => {
          if (output.length > 8 * 1024 * 1024) { child.kill(); finish(false); return; }
          output += chunk.toString("utf8");
          if (output.includes(R14_PREVIEW_READY_MARKER)) finish(!/\b(?:SCRIPT ERROR|ERROR:)\b/u.test(output));
        };
        child.stdout.on("data", collect); child.stderr.on("data", collect);
        child.once("error", () => finish(false)); child.once("exit", () => finish(false));
        const timer = setTimeout(() => { child.kill(); finish(false); }, 30_000);
      });
      child.once("exit", () => {
        if (activePreview === preview) { activePreview = null; void cleanup(preview); }
      });
      if (!started) { await stopLaunch(); return Object.freeze({ ok: false }); }
      return Object.freeze({ ok: true });
    } catch {
      await cleanup({ project, child: { exitCode: 0, signalCode: null } });
      return Object.freeze({ ok: false });
    }
  }

  return Object.freeze({
    async findCache({ promptSha256, model }) { return findSolved({ promptSha256, model, ...common }); },
    async generate() { return diagnostic("R14_PREVIEW_OFFLINE_CACHE_ONLY"); },
    async describeAssets() { return diagnostic("R14_PREVIEW_OFFLINE_CACHE_ONLY"); },
    async acquire() { return diagnostic("R14_PREVIEW_OFFLINE_CACHE_ONLY"); },
    async publish() { return diagnostic("R14_PREVIEW_OFFLINE_CACHE_ONLY"); },
    async launch({ runId }) { return launch(runId); },
    async recover() { return recoverSolved(common); },
    stopLaunch,
  });
}
