import {
  parseSpatialAnalysisArguments,
  publishSpatialEnvironmentAnalysis,
} from "./lib/spatial-analysis-core.mjs";
import { resolveGodotBinary } from "./lib/godot-core.mjs";

async function main() {
  const options = parseSpatialAnalysisArguments(process.argv.slice(2));
  const godot = resolveGodotBinary();
  const result = await publishSpatialEnvironmentAnalysis({
    sourceDirectory: options.sourceDirectory,
    outputDirectory: options.outputDirectory,
    godotBin: godot.command,
  });
  console.log(`SPATIAL_ENVIRONMENT_ANALYSIS_OK facts=${result.factsSha256}`);
}

main().catch((error) => {
  const code = typeof error?.code === "string" ? error.code : "SPATIAL_ANALYSIS_CLI_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = 2;
});
