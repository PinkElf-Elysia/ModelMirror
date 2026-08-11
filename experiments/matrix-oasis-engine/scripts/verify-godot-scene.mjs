import fs from "node:fs";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {compileAuthoringGamePackJson} from "@matrix-oasis/game-pack-compiler";
import {canonicalizeJsonValue as canonicalizeRuntimeJsonValue} from "@matrix-oasis/runtime-pack-contracts";
import {
  applyRuntimeGameSessionAction,
  createRuntimeGameSession,
  prepareRuntimeGamePackJson,
} from "@matrix-oasis/runtime-pack-simulator";
import {canonicalizeJsonValue as canonicalizeSceneJsonValue} from "@matrix-oasis/scene-pack-contracts";
import {projectPath, resolveGodotBinary} from "./lib/godot-core.mjs";
import {buildGodotParityCases} from "./lib/godot-runtime-core.mjs";
import {
  buildSceneParityCases,
  GodotSceneVerificationError,
  runGodotSceneCases,
} from "./lib/godot-scene-verification-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const examplesRoot = path.join(moduleRoot, "examples");

try {
  const args = process.argv.slice(2);
  if (args.length > 1 || (args.length === 1 && args[0] !== "--smoke-only")) {
    throw new GodotSceneVerificationError("GODOT_SCENE_VERIFY_ARGUMENT_ERROR");
  }
  const smokeOnly = args.length === 1;
  const examples = ["mechanics-conformance", "last-train-r1"].map((name) => ({
    name,
    text: fs.readFileSync(path.join(examplesRoot, `${name}.authoring-game-pack.json`), "utf8"),
  }));
  const runtimeCases = await buildGodotParityCases({
    examples,
    compileAuthoringGamePackJson,
    canonicalizeJsonValue: canonicalizeRuntimeJsonValue,
    prepareRuntimeGamePackJson,
    createRuntimeGameSession,
    applyRuntimeGameSessionAction,
  });
  const cases = buildSceneParityCases({runtimeCases, canonicalizeSceneJson: canonicalizeSceneJsonValue});
  const godot = resolveGodotBinary();
  const report = runGodotSceneCases({
    moduleRoot,
    sourceProjectRoot: projectPath(moduleRoot),
    godotCommand: godot.command,
    cases,
    runTraces: !smokeOnly,
  });
  const runs = report.results.reduce((total, item) => total + item.repetitions, 0);
  console.log(smokeOnly
    ? `GODOT_SCENE_SMOKE_OK version=${godot.version} smokes=${report.smokes}`
    : `GODOT_SCENE_OK version=${godot.version} cases=${report.results.length} runs=${runs} smokes=${report.smokes}`);
} catch (error) {
  const code = error instanceof GodotSceneVerificationError && /^[A-Z][A-Z0-9_]+$/u.test(error.code)
    ? error.code
    : "GODOT_SCENE_VERIFY_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = code === "GODOT_SCENE_VERIFY_ARGUMENT_ERROR" ? 2 : 1;
}
