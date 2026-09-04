import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import {
  prepareNpcDerivedState,
  projectNpcDerivedState,
  verifyNpcDerivedState,
} from "@matrix-oasis/npc-derived-state-runtime";
import { bindNpcDerivedStateSource } from "./lib/r21-projection-core.mjs";
import { runR21Verify } from "./lib/r21-cli-core.mjs";

const scriptFile = fileURLToPath(import.meta.url);
const temporaryRoot = path.resolve(path.parse(scriptFile).root, "tmp");
const runtime = Object.freeze({ prepareNpcDerivedState, projectNpcDerivedState, verifyNpcDerivedState, bindNpcDerivedStateSource });

export async function main(args = process.argv.slice(2), overrides = {}) {
  return runR21Verify(args, runtime, { temporaryRoot, ...overrides });
}

function runAutomatedVerification() {
  const tests = [
    "packages/npc-derived-state-contracts/tests/contracts.test.mjs",
    "packages/npc-derived-state-runtime/tests/runtime.test.mjs",
    "tests/r21-cli.test.mjs",
    "tests/r21-falsification.test.mjs",
    "tests/r21-projection.test.mjs",
    "tests/r21-qualification.test.mjs",
    "tests/r21-real-cache.test.mjs",
  ];
  const result = spawnSync(process.execPath, ["--test", ...tests], {
    cwd: path.resolve(fileURLToPath(new URL("..", import.meta.url))),
    stdio: "inherit",
    shell: false,
    windowsHide: true,
  });
  if (result.error || result.status !== 0) throw new Error("NPC_DERIVED_STATE_AUTOMATED_VERIFICATION_FAILED");
  process.stdout.write("NPC_DERIVED_STATE_AUTOMATED_VERIFIED\n");
}

if (scriptFile === path.resolve(process.argv[1] ?? "")) {
  try {
    if (process.argv.length === 2) runAutomatedVerification();
    else {
      const result = await main();
      process.stdout.write(`${JSON.stringify(result)}\n`);
    }
  } catch {
    process.stderr.write("NPC_DERIVED_STATE_INTERNAL_ERROR\n");
    process.exitCode = 2;
  }
}
