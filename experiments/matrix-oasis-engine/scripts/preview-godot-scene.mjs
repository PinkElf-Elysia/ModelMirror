import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { assertGodotOutputClean, resolveGodotBinary, runGodotCommand } from "./lib/godot-core.mjs";
import { GodotSceneHarnessError, parseSceneExampleArguments, sceneGodotArguments } from "./lib/godot-scene-core.mjs";
import { createRuntimePreviewProject, removeRuntimePreviewProject } from "./prepare-godot-runtime.mjs";
import { createScenePreviewArtifacts, removeScenePreviewArtifacts } from "./prepare-godot-scene.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

function runInteractive(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {cwd: moduleRoot, shell: false, stdio: "inherit", windowsHide: false});
    child.once("error", reject);
    child.once("exit", (code, signal) => signal || code !== 0
      ? reject(new GodotSceneHarnessError("GODOT_SCENE_PREVIEW_PROCESS_FAILED"))
      : resolve());
  });
}

let artifacts = null;
let project = null;
try {
  const example = parseSceneExampleArguments(process.argv.slice(2));
  artifacts = await createScenePreviewArtifacts({
    moduleRoot,
    example,
    compileAuthoringGamePackJson,
    canonicalizeRuntimeJsonValue: canonicalizeJsonValue,
  });
  const godot = resolveGodotBinary();
  project = createRuntimePreviewProject({moduleRoot});
  const imported = runGodotCommand({
    command: godot.command,
    args: ["--headless", "--editor", "--path", project.projectRoot, "--quit"],
    cwd: moduleRoot,
    timeout: 120_000,
  });
  assertGodotOutputClean(imported);
  await runInteractive(godot.command, sceneGodotArguments({
    projectRoot: project.projectRoot,
    runtimePath: artifacts.runtimePath,
    receiptPath: artifacts.receiptPath,
    scenePath: artifacts.scenePath,
  }));
  removeRuntimePreviewProject(project.temporaryRoot, {moduleRoot, identity: project.identity});
  project = null;
  removeScenePreviewArtifacts(artifacts.temporaryRoot, {moduleRoot, identity: artifacts.identity});
  artifacts = null;
  console.log(`GODOT_SCENE_PREVIEW_CLOSED version=${godot.version} example=${example}`);
} catch (error) {
  let finalError = error;
  if (project !== null) {
    try { removeRuntimePreviewProject(project.temporaryRoot, {moduleRoot, identity: project.identity}); } catch (cleanupError) { finalError = cleanupError; }
  }
  if (artifacts !== null) {
    try { removeScenePreviewArtifacts(artifacts.temporaryRoot, {moduleRoot, identity: artifacts.identity}); } catch (cleanupError) { finalError = cleanupError; }
  }
  const code = finalError instanceof GodotSceneHarnessError && /^[A-Z][A-Z0-9_]+$/u.test(finalError.code)
    ? finalError.code
    : "GODOT_SCENE_PREVIEW_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = code === "GODOT_SCENE_ARGUMENT_ERROR" ? 2 : 1;
}
