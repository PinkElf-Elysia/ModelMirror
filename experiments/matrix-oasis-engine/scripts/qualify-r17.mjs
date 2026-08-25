import path from "node:path";
import { fileURLToPath } from "node:url";
import { qualifyR17CandidateRecorded, qualifyR17CandidateSourceOnly } from "./lib/r17-qualification-core.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
function args(argv) {
  if (![6, 8, 10].includes(argv.length)) throw Object.assign(new Error("R17_QUALIFY_ARGUMENT_ERROR"), { code: "R17_QUALIFY_ARGUMENT_ERROR" });
  const values = new Map();
  for (let index = 0; index < argv.length; index += 2) values.set(argv[index], argv[index + 1]);
  if (!values.has("--candidate") || !values.has("--source") || !values.has("--output") || [...values.keys()].some((key) => !["--candidate", "--source", "--output", "--runtime", "--godot-bin"].includes(key))) throw Object.assign(new Error("R17_QUALIFY_ARGUMENT_ERROR"), { code: "R17_QUALIFY_ARGUMENT_ERROR" });
  return { candidateId: values.get("--candidate"), sourceDir: values.get("--source"), outputDir: values.get("--output"), runtimeDir: values.get("--runtime"), godotBin: values.get("--godot-bin") };
}
try {
  const request = args(process.argv.slice(2));
  const result = request.runtimeDir ? await qualifyR17CandidateRecorded({ moduleRoot, ...request }) : qualifyR17CandidateSourceOnly({ moduleRoot, ...request });
  console.log(`R17_QUALIFICATION_PUBLISHED candidate=${request.candidateId} conclusion=${result.evaluation.conclusion}`);
} catch (error) { console.error(error?.code ?? "R17_QUALIFY_INTERNAL_ERROR"); process.exitCode = 1; }
