import { spawnSync } from "node:child_process";

const npmExecPath = process.env.npm_execpath;
if (!npmExecPath) {
  console.error("R17_VERIFY_RUNTIME_UNAVAILABLE");
  process.exit(2);
}

const steps = [
  ["v2-claim", ["run", "check:v2-claim"]],
  ["references", ["run", "verify:r17-references"]],
  ["contracts", ["run", "verify:r17-contracts"]],
  ["godot-candidates", ["run", "verify:r17-godot"]],
  ["agent-candidates", ["run", "verify:r17-agent"]],
  ["summary", ["run", "verify:r17-summary"]],
];

for (const [id, args] of steps) {
  console.log(`R17_VERIFY_STEP_START ${id}`);
  const result = spawnSync(process.execPath, [npmExecPath, ...args], { stdio: "inherit", shell: false, windowsHide: true });
  if (result.error || result.status !== 0) {
    console.error(`R17_VERIFY_STEP_FAILED ${id}`);
    process.exit(result.status ?? 1);
  }
  console.log(`R17_VERIFY_STEP_OK ${id}`);
}

console.log(`R17_VERIFY_OK steps=${steps.length}`);
