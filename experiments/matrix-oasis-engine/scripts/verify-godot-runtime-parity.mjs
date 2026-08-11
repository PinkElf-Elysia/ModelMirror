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
import {
  buildGodotParityCases,
  GodotRuntimeHarnessError,
  runGodotParityCases,
  runGodotRuntimeLabSmokes,
} from "./lib/godot-runtime-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const examplesRoot = path.join(moduleRoot, "examples");

try {
  if (process.argv.length !== 2) {
    throw new GodotRuntimeHarnessError("GODOT_PARITY_ARGUMENT_ERROR");
  }
  const examples = [
    {
      name: "mechanics-conformance",
      text: fs.readFileSync(
        path.join(examplesRoot, "mechanics-conformance.authoring-game-pack.json"),
        "utf8",
      ),
    },
    {
      name: "last-train-r1",
      text: fs.readFileSync(
        path.join(examplesRoot, "last-train-r1.authoring-game-pack.json"),
        "utf8",
      ),
    },
  ];
  const cases = await buildGodotParityCases({
    examples,
    compileAuthoringGamePackJson,
    canonicalizeJsonValue,
    prepareRuntimeGamePackJson,
    createRuntimeGameSession,
    applyRuntimeGameSessionAction,
  });
  const godot = resolveGodotBinary();
  const results = runGodotParityCases({
    moduleRoot,
    sourceProjectRoot: projectPath(moduleRoot),
    godotCommand: godot.command,
    cases,
  });
  const labSmokes = runGodotRuntimeLabSmokes({
    moduleRoot,
    sourceProjectRoot: projectPath(moduleRoot),
    godotCommand: godot.command,
    cases,
  });
  const runs = results.reduce((total, item) => total + item.repetitions, 0);
  console.log(
    `GODOT_PARITY_OK version=${godot.version} cases=${results.length} runs=${runs} labs=${labSmokes}`,
  );
} catch (error) {
  const code = error instanceof GodotRuntimeHarnessError &&
      /^[A-Z][A-Z0-9_]+$/u.test(error.code)
    ? error.code
    : "GODOT_PARITY_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = code === "GODOT_PARITY_ARGUMENT_ERROR" ? 2 : 1;
}
