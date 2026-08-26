import path from "node:path";
import { fileURLToPath } from "node:url";
import { verifyR17References } from "./lib/r17-reference-core.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

try {
  if (process.argv.length !== 2) throw Object.assign(new Error("R17_REFERENCE_ARGUMENT_ERROR"), { code: "R17_REFERENCE_ARGUMENT_ERROR" });
  const result = verifyR17References(moduleRoot);
  console.log(`R17_REFERENCES_OK executable=${result.executableCandidates} architecture=${result.architectureReferences} files=${result.files}`);
} catch (error) {
  console.error(error?.code ?? "R17_REFERENCE_INTERNAL_ERROR");
  process.exitCode = 1;
}
