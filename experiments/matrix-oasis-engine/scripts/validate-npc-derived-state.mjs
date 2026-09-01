import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseR21ValidateArguments, validateR21Document } from "./lib/r21-cli-core.mjs";

const scriptFile = fileURLToPath(import.meta.url);
const temporaryRoot = path.resolve(path.parse(scriptFile).root, "tmp");

export async function main(args = process.argv.slice(2), overrides = {}) {
  const parsed = parseR21ValidateArguments(args);
  return validateR21Document(parsed.kind, parsed.file, temporaryRoot, overrides);
}

if (scriptFile === path.resolve(process.argv[1] ?? "")) {
  try {
    const report = await main();
    process.stdout.write(`${JSON.stringify(report)}\n`);
    process.exitCode = report.valid ? 0 : 1;
  } catch {
    process.stderr.write("NPC_DERIVED_STATE_CONTRACT_INTERNAL_ERROR\n");
    process.exitCode = 2;
  }
}
