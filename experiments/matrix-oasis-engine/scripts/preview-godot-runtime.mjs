import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { projectPath, resolveGodotBinary } from "./lib/godot-core.mjs";
import {
  createRuntimePreviewArtifacts,
  GodotRuntimePreviewError,
  parseRuntimePreviewArguments,
  removeRuntimePreviewArtifacts,
  runtimePreviewGodotArguments,
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
        reject(new GodotRuntimePreviewError("GODOT_RUNTIME_PREVIEW_PROCESS_FAILED"));
        return;
      }
      resolve();
    });
  });
}

let artifacts = null;
try {
  const example = parseRuntimePreviewArguments(process.argv.slice(2));
  artifacts = await createRuntimePreviewArtifacts({
    moduleRoot,
    example,
    compileAuthoringGamePackJson,
    canonicalizeJsonValue,
  });
  const godot = resolveGodotBinary();
  await runInteractive(godot.command, runtimePreviewGodotArguments({
    projectRoot: projectPath(moduleRoot),
    runtimePath: artifacts.runtimePath,
    receiptPath: artifacts.receiptPath,
  }));
  removeRuntimePreviewArtifacts(artifacts.temporaryRoot, {
    moduleRoot,
    identity: artifacts.identity,
  });
  artifacts = null;
  console.log(`GODOT_RUNTIME_PREVIEW_CLOSED version=${godot.version} example=${example}`);
} catch (error) {
  let finalError = error;
  if (artifacts) {
    try {
      removeRuntimePreviewArtifacts(artifacts.temporaryRoot, {
        moduleRoot,
        identity: artifacts.identity,
      });
      artifacts = null;
    } catch (cleanupError) {
      finalError = cleanupError;
    }
  }
  const code = finalError instanceof GodotRuntimePreviewError && /^[A-Z][A-Z0-9_]+$/u.test(finalError.code)
    ? finalError.code
    : "GODOT_RUNTIME_PREVIEW_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = code === "GODOT_RUNTIME_PREVIEW_ARGUMENT_ERROR" ? 2 : 1;
}
