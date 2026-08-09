import { spawnSync } from "node:child_process";

const npmExecPath = process.env.npm_execpath;
if (!npmExecPath) {
  console.error("VERIFY_RUNTIME_UNAVAILABLE: run this command through npm.");
  process.exit(2);
}

const steps = [
  ["doctor", ["run", "doctor"]],
  ["round-scope", ["run", "check:round-scope"]],
  ["boundary", ["run", "check:boundary"]],
  ["pack-examples", ["run", "validate:examples"]],
  ["runtime-pack", ["run", "verify:runtime-pack"]],
  ["compiler", ["run", "verify:compiler"]],
  ["runtime-simulator", ["run", "verify:runtime-simulator"]],
  ["parity", ["run", "verify:parity"]],
  ["tests", ["test"]],
  ["creator-build", ["run", "build:creator"]],
  ["creator-smoke", ["run", "smoke:creator"]],
];

for (const [id, args] of steps) {
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

console.log(`VERIFY_OK steps=${steps.length}`);
