import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  prepareNpcDerivedState,
  projectNpcDerivedState,
  verifyNpcDerivedState,
} from "@matrix-oasis/npc-derived-state-runtime";
import { bindNpcDerivedStateSource } from "./lib/r21-projection-core.mjs";
import { R21_QUALIFICATION_MARKERS, runR21Qualification } from "./lib/r21-cli-core.mjs";

const scriptFile = fileURLToPath(import.meta.url);
const temporaryRoot = path.resolve(path.parse(scriptFile).root, "tmp");
const runtime = Object.freeze({ prepareNpcDerivedState, projectNpcDerivedState, verifyNpcDerivedState, bindNpcDerivedStateSource });

export async function main(args = process.argv.slice(2), overrides = {}) {
  return runR21Qualification(args, runtime, { temporaryRoot, ...overrides });
}

if (scriptFile === path.resolve(process.argv[1] ?? "")) {
  try {
    const result = await main();
    for (const marker of R21_QUALIFICATION_MARKERS) process.stdout.write(`${marker}\n`);
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch {
    process.stderr.write("NPC_DERIVED_STATE_INTERNAL_ERROR\n");
    process.exitCode = 2;
  }
}
