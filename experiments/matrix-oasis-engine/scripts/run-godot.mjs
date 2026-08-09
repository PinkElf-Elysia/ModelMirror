import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  GodotHarnessError,
  assertGodotOutputClean,
  assertSingleReadinessMarker,
  projectPath,
  resolveGodotBinary,
  runGodotCommand,
} from "./lib/godot-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const projectRoot = projectPath(moduleRoot);
const [mode, ...extra] = process.argv.slice(2);

try {
  if (extra.length > 0 || !["import", "smoke"].includes(mode)) {
    throw new GodotHarnessError("GODOT_HARNESS_ARGUMENT_ERROR");
  }
  const godot = resolveGodotBinary();
  const args = mode === "import"
    ? ["--headless", "--editor", "--path", projectRoot, "--quit"]
    : ["--headless", "--path", projectRoot, "--", "--matrix-oasis-smoke"];
  const output = runGodotCommand({
    command: godot.command,
    args,
    cwd: moduleRoot,
    timeout: mode === "import" ? 120_000 : 30_000,
  });
  assertGodotOutputClean(output);
  if (mode === "smoke") {
    assertSingleReadinessMarker(output);
  }
  console.log(`GODOT_${mode.toUpperCase()}_OK version=${godot.version}`);
} catch (error) {
  const code = error instanceof GodotHarnessError
    ? error.code
    : "GODOT_HARNESS_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = code === "GODOT_HARNESS_ARGUMENT_ERROR" ? 2 : 1;
}
