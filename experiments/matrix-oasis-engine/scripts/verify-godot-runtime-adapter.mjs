import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { projectPath, resolveGodotBinary } from "./lib/godot-core.mjs";
import {
  buildGodotAdapterCases,
  GodotRuntimeHarnessError,
  runGodotAdapterCases,
} from "./lib/godot-runtime-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const examplesRoot = path.join(moduleRoot, "examples");

try {
  if (process.argv.length !== 2) {
    throw new GodotRuntimeHarnessError("GODOT_ADAPTER_ARGUMENT_ERROR");
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
  const cases = await buildGodotAdapterCases({
    examples,
    compileAuthoringGamePackJson,
    canonicalizeJsonValue,
  });
  const godot = resolveGodotBinary();
  const results = runGodotAdapterCases({
    moduleRoot,
    sourceProjectRoot: projectPath(moduleRoot),
    godotCommand: godot.command,
    cases,
  });
  console.log(`GODOT_ADAPTER_OK version=${godot.version} cases=${results.length}`);
} catch (error) {
  const code = error instanceof GodotRuntimeHarnessError &&
      /^[A-Z][A-Z0-9_]+$/u.test(error.code)
    ? error.code
    : "GODOT_ADAPTER_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = code === "GODOT_ADAPTER_ARGUMENT_ERROR" ? 2 : 1;
}
