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
const EXPECTED_FAILURE = "PACK_GODOT_SPATIAL_COMPUTE_UNAVAILABLE";
const READY_MARKER = "MATRIX_OASIS_R11_SPATIAL_READY";

function fail(code) {
  throw new Error(code);
}

function verifyHeadlessGuard(godot, projectRoot) {
  const missing = (name) => path.join(projectRoot, "spatial_verify_missing", name);
  const result = spawnSync(godot.command, [
    "--headless", "--path", projectRoot, "res://spatial_prototype/spatial_lab.tscn", "--",
    `--matrix-oasis-runtime-pack=${missing("runtime.json")}`,
    `--matrix-oasis-runtime-receipt=${missing("receipt.json")}`,
    `--matrix-oasis-scene-pack=${missing("scene.json")}`,
    `--matrix-oasis-spatial-assembly=${missing("spatial.json")}`,
    "--matrix-oasis-spatial-resource=res://spatial_run/assets/environment.compressed.ply",
    "--matrix-oasis-spatial-smoke",
  ], {
    cwd: moduleRoot,
    encoding: "utf8",
    maxBuffer: 8 * 1024 * 1024,
    shell: false,
    timeout: 30_000,
    windowsHide: true,
  });
  const output = `${result.stdout ?? ""}${result.stderr ?? ""}`;
  if (result.error) {
    if (output.split(EXPECTED_FAILURE).length - 1 === 1 && !/\bSCRIPT ERROR\b/u.test(output)) {
      fail("SPATIAL_BUILDER_HEADLESS_GUARD_DID_NOT_EXIT");
    }
    fail("SPATIAL_BUILDER_HEADLESS_GUARD_PROCESS_FAILED");
  }
  if (result.status === 0) fail("SPATIAL_BUILDER_HEADLESS_GUARD_EXIT_FAILED");
  if (output.split(EXPECTED_FAILURE).length - 1 !== 1) fail("SPATIAL_BUILDER_HEADLESS_GUARD_CODE_FAILED");
  if (output.includes(READY_MARKER)) fail("SPATIAL_BUILDER_HEADLESS_GUARD_READY_FAILED");
  if (/\bSCRIPT ERROR\b/u.test(output)) fail("SPATIAL_BUILDER_HEADLESS_GUARD_SCRIPT_FAILED");
  if (/\bPanoramaSkyMaterial\b/u.test(output)) fail("SPATIAL_BUILDER_HEADLESS_GUARD_PANORAMA_FAILED");
}

let preview;
try {
  const godot = resolveGodotBinary();
  preview = createRuntimePreviewProject({ moduleRoot });
  configureGdgsProject(preview.projectRoot);
  const imported = runGodotCommand({
    command: godot.command,
    args: ["--headless", "--editor", "--path", preview.projectRoot, "--quit"],
    cwd: moduleRoot,
    timeout: 120_000,
  });
  assertGodotOutputClean(imported);
  verifyHeadlessGuard(godot, preview.projectRoot);
  process.stdout.write("SPATIAL_BUILDER_VERIFY_OK import=1 headless_fail_closed=1\n");
} catch (error) {
  const code = typeof error?.message === "string" && /^[A-Z][A-Z0-9_]{2,127}$/u.test(error.message)
    ? error.message : "SPATIAL_BUILDER_VERIFY_FAILED";
  process.stderr.write(`${code}\n`);
  process.exitCode = 1;
} finally {
  if (preview) {
    try {
      removeRuntimePreviewProject(preview.temporaryRoot, { moduleRoot, identity: preview.identity });
    } catch {
      process.stderr.write("SPATIAL_BUILDER_VERIFY_CLEANUP_FAILED\n");
      process.exitCode = 1;
    }
  }
}
