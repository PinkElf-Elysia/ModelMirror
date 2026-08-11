import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {compileAuthoringGamePackJson} from "@matrix-oasis/game-pack-compiler";
import {canonicalizeJsonValue} from "@matrix-oasis/runtime-pack-contracts";
import {assertGodotOutputClean, resolveGodotBinary, runGodotCommand} from "./lib/godot-core.mjs";
import {SCENE_READY_MARKER} from "./lib/godot-scene-core.mjs";
import {
  configureSceneViewport,
  GodotSceneVerificationError,
  inspectSceneCapture,
  parseSceneCaptureArguments,
  SCENE_CAPTURE_FPS,
  SCENE_CAPTURE_FRAMES,
  SCENE_CAPTURE_HEIGHT,
  SCENE_CAPTURE_PREFIX,
  validateSceneCaptureOutput,
} from "./lib/godot-scene-verification-core.mjs";
import {createRuntimePreviewProject, removeRuntimePreviewProject} from "./prepare-godot-runtime.mjs";
import {createScenePreviewArtifacts, removeScenePreviewArtifacts} from "./prepare-godot-scene.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
let artifacts = null;
let project = null;

try {
  const request = parseSceneCaptureArguments(process.argv.slice(2));
  const output = validateSceneCaptureOutput(request.output);
  fs.mkdirSync(output);
  artifacts = await createScenePreviewArtifacts({
    moduleRoot,
    example: request.example,
    compileAuthoringGamePackJson,
    canonicalizeRuntimeJsonValue: canonicalizeJsonValue,
  });
  project = createRuntimePreviewProject({moduleRoot});
  configureSceneViewport(project.projectRoot, request.width);
  const godot = resolveGodotBinary();
  const imported = runGodotCommand({
    command: godot.command,
    args: ["--headless", "--editor", "--path", project.projectRoot, "--quit"],
    cwd: moduleRoot,
    timeout: 120_000,
  });
  assertGodotOutputClean(imported);
  const captured = runGodotCommand({
    command: godot.command,
    args: [
      "--path",
      project.projectRoot,
      "--write-movie",
      path.join(output, `${SCENE_CAPTURE_PREFIX}.png`),
      "--fixed-fps",
      String(SCENE_CAPTURE_FPS),
      "--resolution",
      `${request.width}x${SCENE_CAPTURE_HEIGHT}`,
      "--quit-after",
      String(SCENE_CAPTURE_FRAMES),
      "res://scene_binding/scene_lab.tscn",
      "--",
      `--matrix-oasis-runtime-pack=${artifacts.runtimePath}`,
      `--matrix-oasis-runtime-receipt=${artifacts.receiptPath}`,
      `--matrix-oasis-scene-pack=${artifacts.scenePath}`,
    ],
    cwd: moduleRoot,
    timeout: 120_000,
  });
  assertGodotOutputClean(captured);
  if (captured.split(SCENE_READY_MARKER).length - 1 !== 1) {
    throw new GodotSceneVerificationError("GODOT_SCENE_READY_MARKER_INVALID");
  }
  const report = Object.freeze({...inspectSceneCapture(output, request.width), example: request.example});
  fs.writeFileSync(path.join(output, "capture-manifest.json"), `${JSON.stringify(report, null, 2)}\n`, {
    encoding: "utf8",
    flag: "wx",
  });
  removeRuntimePreviewProject(project.temporaryRoot, {moduleRoot, identity: project.identity});
  project = null;
  removeScenePreviewArtifacts(artifacts.temporaryRoot, {moduleRoot, identity: artifacts.identity});
  artifacts = null;
  console.log(`GODOT_SCENE_CAPTURE_OK frames=${report.frameCount} size=${report.width}x${report.height}`);
} catch (error) {
  if (project !== null) {
    try {
      removeRuntimePreviewProject(project.temporaryRoot, {moduleRoot, identity: project.identity});
    } catch {
      // Preserve ambiguous state instead of broad cleanup.
    }
  }
  if (artifacts !== null) {
    try {
      removeScenePreviewArtifacts(artifacts.temporaryRoot, {moduleRoot, identity: artifacts.identity});
    } catch {
      // Preserve ambiguous state instead of broad cleanup.
    }
  }
  const code = error instanceof GodotSceneVerificationError && /^[A-Z][A-Z0-9_]+$/u.test(error.code)
    ? error.code
    : "GODOT_SCENE_CAPTURE_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = code === "GODOT_SCENE_CAPTURE_ARGUMENT_ERROR" ? 2 : 1;
}
