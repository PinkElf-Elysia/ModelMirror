import { spawnSync } from "node:child_process";

const steps = [
  ["scripts/verify-r19-references.mjs"],
  ["--test", "tests/r19-reference.test.mjs"],
];
for (const args of steps) {
  const result = spawnSync(process.execPath, args, {
    stdio: "inherit",
    shell: false,
    windowsHide: true,
  });
  if (result.error || result.status !== 0) process.exit(result.status ?? 1);
}
console.log("R19_GOVERNANCE_CHECKS_OK");
