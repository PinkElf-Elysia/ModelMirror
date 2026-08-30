import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { assertGodotOutputClean, resolveGodotBinary, runGodotCommand } from "./lib/godot-core.mjs";
import { createRuntimePreviewProject, removeRuntimePreviewProject } from "./prepare-godot-runtime.mjs";
import { configureGdgsProject } from "./verify-godot-splat.mjs";

const steps = [
  ["references", ["run", "verify:r20-references"]],
  ["behavior-contracts", ["run", "verify:npc-behavior-contracts"]],
  ["behavior-runtime", ["test", "--workspace", "@matrix-oasis/npc-behavior-runtime"]],
  ["authority-session", ["run", "verify:npc-authority-session"]],
  ["godot-bridge", ["run", "verify:npc-godot-bridge"]],
  ["falsification", ["run", "test:r20-falsification"]],
  ["gate-truthfulness", ["run", "test:r20-gate-truthfulness"]],
  ["capture", ["run", "verify:r20-capture"]],
  ["round-scope", ["run", "check:round-scope"]],
  ["boundary", ["run", "check:boundary"]],
  ["v2-claim", ["run", "check:v2-claim"]],
];
const npmExecPath = process.env.npm_execpath;
if (!npmExecPath) {
  console.error("R20_VERIFY_RUNTIME_UNAVAILABLE");
  process.exit(2);
}
for (const [id, args] of steps) {
  const result = spawnSync(process.execPath, [npmExecPath, ...args], { stdio: "inherit", shell: false, windowsHide: true });
  if (result.error || result.status !== 0) {
    console.error(`R20_VERIFY_FAILED step=${id}`);
    process.exit(result.status ?? 1);
  }
}
const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const godot = resolveGodotBinary();
const project = createRuntimePreviewProject({ moduleRoot });

try {
  configureGdgsProject(project.projectRoot);
  for (const actorCount of [2, 4, 32, 64]) {
    const output = runGodotCommand({
      command: godot.command,
      args: [
        "--headless",
        "--log-file",
        path.join(project.temporaryRoot, `npc-load-${actorCount}.log`),
        "--path",
        project.projectRoot,
        "res://npc_authority_prototype/npc_load_probe.tscn",
        "--",
        `--actors=${actorCount}`,
      ],
      cwd: moduleRoot,
      timeout: 120_000,
    });
    assertGodotOutputClean(output);
    const match = /R20_NPC_LOAD_JSON:(\{[^\r\n]+\})/u.exec(output);
    const probeMarkerCount = output.split("R20_NPC_LOAD_PROBE_OK").length - 1;
    if (!match || probeMarkerCount !== 1) {
      throw new Error("R20_NPC_LOAD_MARKER_MISSING");
    }
    const report = JSON.parse(match[1]);
    if (
      report.actorCount !== actorCount ||
      report.sampleCount !== 300 ||
      !Number.isSafeInteger(report.medianFpsMilli) ||
      report.medianFpsMilli < 30_000
    ) {
      throw new Error("R20_NPC_LOAD_QUALIFICATION_FAILED");
    }
    if (
      output.includes("R20_GODOT_ENTITY_BRIDGE_QUALIFIED") ||
      output.includes("R20_MULTI_AGENT_TRACE_DETERMINISTIC") ||
      output.includes("R20_RUNTIME_REMAINS_AUTHORITATIVE")
    ) {
      throw new Error("R20_NPC_LOAD_FORMAL_MARKER_FORBIDDEN");
    }
  }
} finally {
  removeRuntimePreviewProject(project.temporaryRoot, {
    moduleRoot,
    identity: project.identity,
  });
}

console.log("R20_AUTOMATED_GATES_OK");
