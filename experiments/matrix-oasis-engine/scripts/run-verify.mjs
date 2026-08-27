import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const VERIFY_STEPS = Object.freeze([
  ["doctor", ["run", "doctor"]],
  ["round-scope", ["run", "check:round-scope"]],
  ["boundary", ["run", "check:boundary"]],
  ["mvp-claim", ["run", "check:mvp-claim"]],
  ["v2-claim", ["run", "check:v2-claim"]],
  ["r18", ["run", "verify:r18"]],
  ["godot-foundation", ["run", "verify:godot"]],
  ["pack-examples", ["run", "validate:examples"]],
  ["runtime-pack", ["run", "verify:runtime-pack"]],
  ["compiler", ["run", "verify:compiler"]],
  ["runtime-simulator", ["run", "verify:runtime-simulator"]],
  ["parity", ["run", "verify:parity"]],
  ["scene-pack", ["run", "verify:scene-pack"]],
  ["prototype-generation", ["run", "verify:prototype-generation"]],
  ["prototype-assets", ["run", "verify:prototype-assets"]],
  ["spatial-environment", ["run", "verify:spatial-environment"]],
  ["spatial-assembly", ["run", "verify:spatial-assembly"]],
  ["spatial-builder", ["run", "verify:spatial-builder"]],
  ["prototype-builder", ["run", "verify:prototype-builder"]],
  ["r12", ["run", "verify:r12"]],
  ["spatial-references", ["run", "verify:spatial-references"]],
  ["spatial-contracts", ["run", "verify:spatial-contracts"]],
  ["spatial-analysis", ["run", "verify:spatial-analysis"]],
  ["r14", ["run", "verify:r14"]],
  ["tests", ["test"]],
  ["creator-build", ["run", "build:creator"]],
  ["creator-smoke", ["run", "smoke:creator"]],
].map(([id, args]) => Object.freeze([id, Object.freeze(args)])));

const isDirectExecution =
  typeof process.argv[1] === "string" &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isDirectExecution) {
  const npmExecPath = process.env.npm_execpath;
  if (!npmExecPath) {
    console.error("VERIFY_RUNTIME_UNAVAILABLE: run this command through npm.");
    process.exit(2);
  }

  for (const [id, args] of VERIFY_STEPS) {
    console.log(`VERIFY_STEP_START ${id}`);
    const result = spawnSync(process.execPath, [npmExecPath, ...args], {
      stdio: "inherit",
      shell: false,
      windowsHide: true,
    });

    if (result.error || result.status !== 0) {
      console.error(`VERIFY_STEP_FAILED ${id}`);
      process.exit(result.status ?? 1);
    }

    console.log(`VERIFY_STEP_OK ${id}`);
  }

  console.log(`VERIFY_OK steps=${VERIFY_STEPS.length}`);
}
