import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  assertGodotOutputClean,
  resolveGodotBinary,
  runGodotCommand,
} from "./lib/godot-core.mjs";
import {
  createRuntimePreviewProject,
  removeRuntimePreviewProject,
} from "./prepare-godot-runtime.mjs";
import { configureGdgsProject } from "./verify-godot-splat.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const GODOT_ENVIRONMENT_ALLOWLIST = Object.freeze([
  "SystemRoot",
  "WINDIR",
  "TEMP",
  "TMP",
  "USERPROFILE",
  "APPDATA",
  "LOCALAPPDATA",
  "PATH",
  "COMSPEC",
  "PATHEXT",
  "SYSTEMDRIVE",
  "HOMEDRIVE",
  "HOMEPATH",
]);

function godotChildEnvironment(environment = process.env) {
  return Object.fromEntries(GODOT_ENVIRONMENT_ALLOWLIST.flatMap((name) => (
    typeof environment[name] === "string" && environment[name].length > 0
      ? [[name, environment[name]]]
      : []
  )));
}

let lastGodotResult = null;
function spawnGodot(command, args, options = {}) {
  const result = spawnSync(command, args, {
    ...options,
    env: godotChildEnvironment(),
  });
  lastGodotResult = result;
  return result;
}

const godot = resolveGodotBinary({
  environment: { GODOT_BIN: process.env.GODOT_BIN },
  probe: spawnGodot,
});
const project = createRuntimePreviewProject({ moduleRoot });
const probeMarker = "R22_NPC_COGNITION_GODOT_PROBE_OK";
let completed = false;

try {
  configureGdgsProject(project.projectRoot);
  const importOutput = runGodotCommand({
    command: godot.command,
    args: [
      "--headless",
      "--log-file",
      path.join(project.temporaryRoot, "matrix-oasis-r22-cognition-import.log"),
      "--editor",
      "--path",
      project.projectRoot,
      "--quit",
    ],
    cwd: moduleRoot,
    timeout: 120_000,
    spawn: spawnGodot,
  });
  assertGodotOutputClean(importOutput);

  const probeOutput = runGodotCommand({
    command: godot.command,
    args: [
      "--headless",
      "--log-file",
      path.join(project.temporaryRoot, "matrix-oasis-r22-cognition-probe.log"),
      "--path",
      project.projectRoot,
      "--script",
      "res://npc_cognition_prototype/npc_cognition_probe.gd",
    ],
    cwd: moduleRoot,
    timeout: 120_000,
    spawn: spawnGodot,
  });
  assertGodotOutputClean(probeOutput);
  if (probeOutput.split(probeMarker).length !== 2) {
    throw new Error("R22_NPC_COGNITION_GODOT_PROBE_INVALID");
  }
  console.log(`${probeMarker} version=${godot.version}`);
  console.log(`R22_NPC_COGNITION_GODOT_IMPORT_OK version=${godot.version}`);
  completed = true;
} finally {
  if (completed) {
    removeRuntimePreviewProject(project.temporaryRoot, {
      moduleRoot,
      identity: project.identity,
    });
  } else {
    process.stderr.write(`${lastGodotResult?.stdout ?? ""}${lastGodotResult?.stderr ?? ""}`);
    process.stderr.write(`R22_GODOT_FAILURE_ARTIFACTS_RETAINED ${project.temporaryRoot}\n`);
  }
}
