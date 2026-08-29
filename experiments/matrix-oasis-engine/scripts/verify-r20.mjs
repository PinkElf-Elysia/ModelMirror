import { spawnSync } from "node:child_process";

const steps = [
  ["references", ["run", "verify:r20-references"]],
  ["round-scope", ["run", "check:round-scope"]],
  ["boundary", ["run", "check:boundary"]],
  ["v2-claim", ["run", "check:v2-claim"]],
];
const npmExecPath = process.env.npm_execpath;
if (!npmExecPath) {
  console.error("R20_VERIFY_RUNTIME_UNAVAILABLE");
  process.exit(2);
}
for (const [id, args] of steps) {
  const result = spawnSync(process.execPath, [npmExecPath, ...args], { stdio: "inherit", shell: false, windowsHide: true });
  if (result.error || result.status !== 0) {
    console.error(`R20_VERIFY_FAILED step=${id}`);
    process.exit(result.status ?? 1);
  }
}
console.log("R20_GOVERNANCE_READY");
