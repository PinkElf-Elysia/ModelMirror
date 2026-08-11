import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { assertGodotOutputClean, resolveGodotBinary, runGodotCommand } from "./lib/godot-core.mjs";
import {
  configurePlayableViewport,
  GodotPlayableHarnessError,
  inspectPlayableCapture,
  parsePlayableCaptureArguments,
  PLAYABLE_CAPTURE_FPS,
  PLAYABLE_CAPTURE_FRAMES,
  PLAYABLE_CAPTURE_HEIGHT,
  PLAYABLE_CAPTURE_PREFIX,
  PLAYABLE_READY_MARKER,
  validatePlayableCaptureOutput,
} from "./lib/godot-playable-core.mjs";
import {
  createRuntimePreviewArtifacts,
  createRuntimePreviewProject,
  removeRuntimePreviewArtifacts,
  removeRuntimePreviewProject,
} from "./prepare-godot-runtime.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
let artifacts = null;
let previewProject = null;

try {
  const request = parsePlayableCaptureArguments(process.argv.slice(2));
  const output = validatePlayableCaptureOutput(request.output);
  fs.mkdirSync(output);
  artifacts = await createRuntimePreviewArtifacts({
    moduleRoot,
    example: request.example,
    compileAuthoringGamePackJson,
    canonicalizeJsonValue,
  });
  previewProject = createRuntimePreviewProject({ moduleRoot });
  configurePlayableViewport(previewProject.projectRoot, request.width);
  const godot = resolveGodotBinary();
  const imported = runGodotCommand({
    command: godot.command,
    args: ["--headless", "--editor", "--path", previewProject.projectRoot, "--quit"],
    cwd: moduleRoot,
    timeout: 120_000,
  });
  assertGodotOutputClean(imported);
  const captured = runGodotCommand({
    command: godot.command,
    args: [
      "--path",
      previewProject.projectRoot,
      "--write-movie",
      path.join(output, `${PLAYABLE_CAPTURE_PREFIX}.png`),
      "--fixed-fps",
      String(PLAYABLE_CAPTURE_FPS),
      "--resolution",
      `${request.width}x${PLAYABLE_CAPTURE_HEIGHT}`,
      "--quit-after",
      String(PLAYABLE_CAPTURE_FRAMES),
      "res://playable/playable_lab.tscn",
      "--",
      `--matrix-oasis-runtime-pack=${artifacts.runtimePath}`,
      `--matrix-oasis-runtime-receipt=${artifacts.receiptPath}`,
    ],
    cwd: moduleRoot,
    timeout: 120_000,
  });
  assertGodotOutputClean(captured);
  if (captured.split(PLAYABLE_READY_MARKER).length - 1 !== 1) {
    throw new GodotPlayableHarnessError("GODOT_3D_READY_MARKER_INVALID");
  }
  const report = Object.freeze({
    ...inspectPlayableCapture(output, request.width),
    example: request.example,
  });
  fs.writeFileSync(
    path.join(output, "capture-manifest.json"),
    `${JSON.stringify(report, null, 2)}\n`,
    { encoding: "utf8", flag: "wx" },
  );
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
  console.log(`GODOT_3D_CAPTURE_OK frames=${report.frameCount} size=${report.width}x${report.height}`);
} catch (error) {
  if (previewProject !== null) {
    try {
      removeRuntimePreviewProject(previewProject.temporaryRoot, {
        moduleRoot,
        identity: previewProject.identity,
      });
    } catch {
      // Preserve ambiguous state instead of broad cleanup.
    }
  }
  if (artifacts !== null) {
    try {
      removeRuntimePreviewArtifacts(artifacts.temporaryRoot, {
        moduleRoot,
        identity: artifacts.identity,
      });
    } catch {
      // Preserve ambiguous state instead of broad cleanup.
    }
  }
  const code = error instanceof GodotPlayableHarnessError && /^[A-Z][A-Z0-9_]+$/u.test(error.code)
    ? error.code
    : "GODOT_3D_CAPTURE_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = code === "GODOT_3D_CAPTURE_ARGUMENT_ERROR" ? 2 : 1;
}
