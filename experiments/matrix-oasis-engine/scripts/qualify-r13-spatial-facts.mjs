import {
  parseSpatialAnalysisArguments,
  publishSpatialEnvironmentAnalysis,
} from "./lib/spatial-analysis-core.mjs";
import { resolveGodotBinary } from "./lib/godot-core.mjs";

function qualificationArguments(args) {
  const translated = [];
  for (let index = 0; index < args.length; index += 2) {
    const name = args[index] === "--spatial-run" ? "--spatial-environment-dir" : args[index];
    translated.push(name, args[index + 1]);
  }
  return parseSpatialAnalysisArguments(translated);
}

async function main() {
  const options = qualificationArguments(process.argv.slice(2));
  const godot = resolveGodotBinary();
  const result = await publishSpatialEnvironmentAnalysis({
    sourceDirectory: options.sourceDirectory,
    outputDirectory: options.outputDirectory,
    godotBin: godot.command,
  });
  console.log(`R13_SPATIAL_FACTS_QUALIFIED facts=${result.factsSha256}`);
}

main().catch((error) => {
  const code = typeof error?.code === "string" ? error.code : "SPATIAL_ANALYSIS_CLI_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = 2;
});
