import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { assertGodotOutputClean, resolveGodotBinary, runGodotCommand } from "./lib/godot-core.mjs";
import {
  GodotPlayableHarnessError,
  parsePlayableExampleArguments,
  playableGodotArguments,
} from "./lib/godot-playable-core.mjs";
import {
  createRuntimePreviewArtifacts,
  createRuntimePreviewProject,
  removeRuntimePreviewArtifacts,
  removeRuntimePreviewProject,
} from "./prepare-godot-runtime.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

function runInteractive(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: moduleRoot,
      shell: false,
      stdio: "inherit",
      windowsHide: false,
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (signal || code !== 0) {
        reject(new GodotPlayableHarnessError("GODOT_3D_PREVIEW_PROCESS_FAILED"));
        return;
      }
      resolve();
    });
  });
}

let artifacts = null;
let previewProject = null;
try {
  const example = parsePlayableExampleArguments(process.argv.slice(2));
  artifacts = await createRuntimePreviewArtifacts({
    moduleRoot,
    example,
    compileAuthoringGamePackJson,
    canonicalizeJsonValue,
  });
  const godot = resolveGodotBinary();
  previewProject = createRuntimePreviewProject({ moduleRoot });
  const imported = runGodotCommand({
    command: godot.command,
    args: ["--headless", "--editor", "--path", previewProject.projectRoot, "--quit"],
    cwd: moduleRoot,
    timeout: 120_000,
  });
  assertGodotOutputClean(imported);
  await runInteractive(godot.command, playableGodotArguments({
    projectRoot: previewProject.projectRoot,
    runtimePath: artifacts.runtimePath,
    receiptPath: artifacts.receiptPath,
  }));
  removeRuntimePreviewProject(previewProject.temporaryRoot, {
    moduleRoot,
    identity: previewProject.identity,
  });
  previewProject = null;
  removeRuntimePreviewArtifacts(artifacts.temporaryRoot, {
    moduleRoot,
    identity: artifacts.identity,
  });
  artifacts = null;
  console.log(`GODOT_3D_PREVIEW_CLOSED version=${godot.version} example=${example}`);
} catch (error) {
  let finalError = error;
  if (previewProject !== null) {
    try {
      removeRuntimePreviewProject(previewProject.temporaryRoot, {
        moduleRoot,
        identity: previewProject.identity,
      });
    } catch (cleanupError) {
      finalError = cleanupError;
    }
  }
  if (artifacts !== null) {
    try {
      removeRuntimePreviewArtifacts(artifacts.temporaryRoot, {
        moduleRoot,
        identity: artifacts.identity,
      });
    } catch (cleanupError) {
      finalError = cleanupError;
    }
  }
  const code = finalError instanceof GodotPlayableHarnessError && /^[A-Z][A-Z0-9_]+$/u.test(finalError.code)
    ? finalError.code
    : "GODOT_3D_PREVIEW_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = code === "GODOT_3D_ARGUMENT_ERROR" ? 2 : 1;
}
