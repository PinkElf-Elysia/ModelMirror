import { spawn } from "node:child_process";
import { lstat, mkdir, open, realpath } from "node:fs/promises";
import path from "node:path";
import { loadVerifiedRuntimeEvidenceRun, recoverRuntimeEvidenceRuns } from "./runtime-evidence-cache-core.mjs";
import { assertGodotOutputClean, runGodotCommand } from "./godot-core.mjs";
import { r14GodotArguments, R14_PREVIEW_READY_MARKER } from "./r14-preview-core.mjs";
import { createRuntimePreviewProject, removeRuntimePreviewProject } from "../prepare-godot-runtime.mjs";
import { configureGdgsProject } from "../verify-godot-splat.mjs";
import { copySpatialPreviewFiles } from "../preview-spatial-prototype.mjs";

export const R15_PREVIEW_READY_MARKER = "MATRIX_OASIS_R15_RUNTIME_EVIDENCE_PREVIEW_READY";
const RUN_ID = /^[0-9a-f]{64}$/u;

export function parseR15PreviewArguments(args, temporaryRoot) {
  if (!Array.isArray(args) || args.length !== 2 || args[0] !== "--evidence-run-root" ||
      typeof args[1] !== "string" || !path.isAbsolute(args[1]) || args[1].includes("\0")) {
    throw new Error("R15_PREVIEW_ARGUMENT_INVALID");
  }
  const evidenceRunRoot = path.resolve(args[1]);
  if (path.dirname(evidenceRunRoot) !== path.resolve(temporaryRoot)) throw new Error("R15_PREVIEW_ARGUMENT_INVALID");
  return Object.freeze({ evidenceRunRoot });
}

export async function selectR15EvidenceRun({ evidenceRunRoot, temporaryRoot, runId = null }, cache = {}) {
  const recover = cache.recoverRuntimeEvidenceRuns ?? recoverRuntimeEvidenceRuns;
  const load = cache.loadVerifiedRuntimeEvidenceRun ?? loadVerifiedRuntimeEvidenceRun;
  const recovered = await recover({ runRoot: evidenceRunRoot, temporaryRoot });
  const selectedId = runId ?? recovered.currentRunId;
  if (!RUN_ID.test(selectedId ?? "") || !recovered.runs.some((item) => item.runId === selectedId)) {
    throw new Error("R15_PREVIEW_CACHE_INVALID");
  }
  return await load({ runRoot: evidenceRunRoot, temporaryRoot, runId: selectedId, includeFiles: true });
}

export async function launchR15EvidencePreview({ selected, godot, moduleRoot }, tools = {}) {
  const createProject = tools.createRuntimePreviewProject ?? createRuntimePreviewProject;
  const removeProject = tools.removeRuntimePreviewProject ?? removeRuntimePreviewProject;
  const configureProject = tools.configureGdgsProject ?? configureGdgsProject;
  const copyFiles = tools.copySpatialPreviewFiles ?? copySpatialPreviewFiles;
  const importProject = tools.runGodotCommand ?? runGodotCommand;
  const assertClean = tools.assertGodotOutputClean ?? assertGodotOutputClean;
  const spawnProcess = tools.spawnProcess ?? spawn;
  const previewFiles = tools.previewFiles ?? { lstat, mkdir, openFile: open, realpath };
  const project = createProject({ moduleRoot });
  let child = null;
  try {
    configureProject(project.projectRoot);
    const runDirectory = await copyFiles(project.projectRoot, selected.previewFiles, previewFiles);
    const imported = importProject({ command: godot.command,
      args: ["--headless", "--editor", "--path", project.projectRoot, "--quit"], cwd: moduleRoot, timeout: 120_000 });
    assertClean(imported);
    child = spawnProcess(godot.command, r14GodotArguments({ projectRoot: project.projectRoot, runDirectory }), {
      cwd: moduleRoot, shell: false, windowsHide: false, stdio: ["ignore", "pipe", "pipe"],
    });
    let output = "";
    const started = await new Promise((resolve) => {
      let settled = false;
      const finish = (value) => { if (!settled) { settled = true; clearTimeout(timer); resolve(value); } };
      const collect = (chunk) => {
        output += chunk.toString("utf8");
        if (output.length > 8 * 1024 * 1024) { child.kill(); finish(false); return; }
        if (output.includes(R14_PREVIEW_READY_MARKER)) finish(!/(?:SCRIPT ERROR:|(?:^|\n)ERROR:)/u.test(output));
      };
      child.stdout.on("data", collect); child.stderr.on("data", collect);
      child.once("error", () => finish(false)); child.once("exit", () => finish(false));
      const timer = setTimeout(() => { child.kill(); finish(false); }, 120_000);
    });
    if (!started) throw new Error("R15_PREVIEW_GODOT_FAILED");
    return Object.freeze({ child, project, cleanup: async () => {
      if (child.exitCode === null && child.signalCode === null) child.kill();
      removeProject(project.temporaryRoot, { moduleRoot, identity: project.identity });
    } });
  } catch (error) {
    if (child?.exitCode === null && child?.signalCode === null) child.kill();
    try { removeProject(project.temporaryRoot, { moduleRoot, identity: project.identity }); } catch { /* retain raced temp */ }
    throw error;
  }
}
