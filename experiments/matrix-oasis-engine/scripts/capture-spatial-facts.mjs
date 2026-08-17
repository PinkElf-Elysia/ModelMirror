import {
  captureSpatialFacts,
  parseSpatialFactsCaptureArguments,
} from "./lib/spatial-analysis-core.mjs";

async function main() {
  const options = parseSpatialFactsCaptureArguments(process.argv.slice(2));
  const result = await captureSpatialFacts(options);
  console.log(`SPATIAL_FACTS_CAPTURE_OK facts=${result.factsSha256}`);
}

main().catch((error) => {
  const code = typeof error?.code === "string" ? error.code : "SPATIAL_ANALYSIS_CLI_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = 2;
});
