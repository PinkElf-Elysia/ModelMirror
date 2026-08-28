import { spawnSync } from "node:child_process";

const steps = [
  ["scripts/verify-r19-references.mjs"],
  ["--test", "tests/r19-reference.test.mjs"],
  ["--test", "packages/npc-authority-contracts/tests/contracts.test.mjs"],
  ["--test", "packages/npc-authority-runtime/tests/ledger.test.mjs"],
  ["--test", "packages/npc-authority-runtime/tests/authority.test.mjs"],
  ["--test", "tests/r19-cli.test.mjs"],
];
for (const args of steps) {
  const result = spawnSync(process.execPath, args, {
    stdio: "inherit",
    shell: false,
    windowsHide: true,
  });
  if (result.error || result.status !== 0) process.exit(result.status ?? 1);
}
console.log("R19_CONTRACTS_CANONICAL");
