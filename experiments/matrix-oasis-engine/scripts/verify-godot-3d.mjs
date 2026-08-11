import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  applyRuntimeGameSessionAction,
  createRuntimeGameSession,
  prepareRuntimeGamePackJson,
} from "@matrix-oasis/runtime-pack-simulator";
import { projectPath, resolveGodotBinary } from "./lib/godot-core.mjs";
import { GodotPlayableHarnessError, runGodotPlayableCases } from "./lib/godot-playable-core.mjs";
import { buildGodotParityCases } from "./lib/godot-runtime-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const examplesRoot = path.join(moduleRoot, "examples");

try {
  const args = process.argv.slice(2);
  if (args.length > 1 || (args.length === 1 && args[0] !== "--smoke-only")) {
    throw new GodotPlayableHarnessError("GODOT_3D_ARGUMENT_ERROR");
  }
  const smokeOnly = args.length === 1;
  const examples = ["mechanics-conformance", "last-train-r1"].map((name) => ({
    name,
    text: fs.readFileSync(path.join(examplesRoot, `${name}.authoring-game-pack.json`), "utf8"),
  }));
  const cases = await buildGodotParityCases({
    examples,
    compileAuthoringGamePackJson,
    canonicalizeJsonValue,
    prepareRuntimeGamePackJson,
    createRuntimeGameSession,
    applyRuntimeGameSessionAction,
  });
  const godot = resolveGodotBinary();
  const report = runGodotPlayableCases({
    moduleRoot,
    sourceProjectRoot: projectPath(moduleRoot),
    godotCommand: godot.command,
    cases,
    runTraces: !smokeOnly,
  });
  const runs = report.results.reduce((total, item) => total + item.repetitions, 0);
  console.log(smokeOnly
    ? `GODOT_3D_SMOKE_OK version=${godot.version} smokes=${report.smokes}`
    : `GODOT_3D_OK version=${godot.version} cases=${report.results.length} runs=${runs} smokes=${report.smokes}`);
} catch (error) {
  const code = error instanceof GodotPlayableHarnessError && /^[A-Z][A-Z0-9_]+$/u.test(error.code)
    ? error.code
    : "GODOT_3D_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = code === "GODOT_3D_ARGUMENT_ERROR" ? 2 : 1;
}
