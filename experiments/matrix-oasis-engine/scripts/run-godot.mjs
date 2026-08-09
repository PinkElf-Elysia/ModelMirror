import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import { fileURLToPath } from "node:url";
import {
  GodotHarnessError,
  assertGdUnitSuccess,
  assertGodotOutputClean,
  assertSingleReadinessMarker,
  projectPath,
  resolveGodotBinary,
  runGodotCommand,
} from "./lib/godot-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const sourceProjectRoot = projectPath(moduleRoot);
const [mode, ...extra] = process.argv.slice(2);

function createDisposableProject() {
  const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "matrix-oasis-godot-project-"));
  const projectRoot = path.join(temporaryRoot, "runtime-godot");
  fs.cpSync(sourceProjectRoot, projectRoot, {
    recursive: true,
    filter: (source) => path.basename(source) !== ".godot",
  });
  return { temporaryRoot, projectRoot };
}

function removeDisposableProject(temporaryRoot) {
  const tempRoot = fs.realpathSync(os.tmpdir());
  const candidate = fs.realpathSync(temporaryRoot);
  const relative = path.relative(tempRoot, candidate);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new GodotHarnessError("GODOT_TEMPORARY_PROJECT_INVALID");
  }
  fs.rmSync(candidate, { recursive: true });
}

try {
  if (extra.length > 0 || !["import", "smoke", "test"].includes(mode)) {
    throw new GodotHarnessError("GODOT_HARNESS_ARGUMENT_ERROR");
  }
  const godot = resolveGodotBinary();
  const { temporaryRoot, projectRoot } = createDisposableProject();
  let reportRoot = null;
  if (mode === "test") {
    const importOutput = runGodotCommand({
      command: godot.command,
      args: ["--headless", "--editor", "--path", projectRoot, "--quit"],
      cwd: moduleRoot,
      timeout: 120_000,
    });
    assertGodotOutputClean(importOutput);
  }
  const args = mode === "import"
    ? ["--headless", "--editor", "--path", projectRoot, "--quit"]
    : mode === "smoke"
      ? ["--headless", "--path", projectRoot, "--", "--matrix-oasis-smoke"]
      : (() => {
          reportRoot = path.join(temporaryRoot, "test-reports");
          return [
            "--headless",
            "--path",
            projectRoot,
            "--script",
            "res://addons/gdUnit4/bin/GdUnitCmdTool.gd",
            "--ignoreHeadlessMode",
            "--add",
            "res://test",
            "--report-directory",
            reportRoot,
          ];
        })();
  const output = runGodotCommand({
    command: godot.command,
    args,
    cwd: moduleRoot,
    timeout: mode === "import" || mode === "test" ? 120_000 : 30_000,
  });
  assertGodotOutputClean(output);
  if (mode === "test") {
    assertGdUnitSuccess(output);
  }
  if (mode === "smoke") {
    assertSingleReadinessMarker(output);
  }
  if (mode === "test" && reportRoot) {
    const resolvedTemporary = fs.realpathSync(temporaryRoot);
    const resolvedReport = fs.realpathSync(reportRoot);
    const relative = path.relative(resolvedTemporary, resolvedReport);
    if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
      throw new GodotHarnessError("GODOT_TEST_REPORT_PATH_INVALID");
    }
  }
  removeDisposableProject(temporaryRoot);
  console.log(`GODOT_${mode.toUpperCase()}_OK version=${godot.version}`);
} catch (error) {
  const code = error instanceof GodotHarnessError
    ? error.code
    : "GODOT_HARNESS_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = code === "GODOT_HARNESS_ARGUMENT_ERROR" ? 2 : 1;
}
