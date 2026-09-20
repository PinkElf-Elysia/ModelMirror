import path from "node:path";
import { fileURLToPath } from "node:url";
import { R22_QUALIFICATION_MARKERS, runR22OfflineQualification } from "./lib/r22-qualification-core.mjs";

const scriptFile = fileURLToPath(import.meta.url); const temporaryRoot = path.resolve(path.parse(scriptFile).root, "tmp");
export async function main(args = process.argv.slice(2), overrides = {}) { return runR22OfflineQualification(args, { temporaryRoot, ...overrides }); }
if (scriptFile === path.resolve(process.argv[1] ?? "")) {
  try { const result = await main(); for (const marker of R22_QUALIFICATION_MARKERS) process.stdout.write(`${marker}\n`); process.stdout.write(`R22_OFFLINE_QUALIFICATION_ONLY ${JSON.stringify(result)}\n`); }
  catch { process.stderr.write("NPC_COGNITION_INTERNAL_ERROR\n"); process.exitCode = 2; }
}
