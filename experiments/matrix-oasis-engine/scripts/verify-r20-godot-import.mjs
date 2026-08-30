import path from "node:path";
import { fileURLToPath } from "node:url";
import { assertGodotOutputClean, resolveGodotBinary, runGodotCommand } from "./lib/godot-core.mjs";
import { createRuntimePreviewProject, removeRuntimePreviewProject } from "./prepare-godot-runtime.mjs";
import { configureGdgsProject } from "./verify-godot-splat.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const godot = resolveGodotBinary();
const project = createRuntimePreviewProject({ moduleRoot });
const p1ProbeMarker = "R20_NPC_P1_PROBE_OK";

try {
  configureGdgsProject(project.projectRoot);
  const output = runGodotCommand({
    command: godot.command,
    args: [
      "--headless",
      "--log-file",
      path.join(project.temporaryRoot, "matrix-oasis-r20-isolated-import.log"),
      "--editor",
      "--path",
      project.projectRoot,
      "--quit",
    ],
    cwd: moduleRoot,
    timeout: 120_000,
  });
  assertGodotOutputClean(output);
  const p1ProbeOutput = runGodotCommand({
    command: godot.command,
    args: [
      "--headless",
      "--log-file",
      path.join(project.temporaryRoot, "matrix-oasis-r20-p1-probe.log"),
      "--path",
      project.projectRoot,
      "--script",
      "res://npc_authority_prototype/npc_p1_probe.gd",
    ],
    cwd: moduleRoot,
    timeout: 120_000,
  });
  assertGodotOutputClean(p1ProbeOutput);
  if (p1ProbeOutput.split(p1ProbeMarker).length !== 2) {
    throw new Error("R20_GODOT_P1_PROBE_INVALID");
  }
  console.log(`${p1ProbeMarker} version=${godot.version}`);
  console.log(`R20_GODOT_IMPORT_OK version=${godot.version}`);
} finally {
  removeRuntimePreviewProject(project.temporaryRoot, { moduleRoot, identity: project.identity });
}
