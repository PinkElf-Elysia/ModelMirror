import { spawnSync } from "node:child_process";

const npmExecPath = process.env.npm_execpath;
if (!npmExecPath) {
  console.error("R18_VERIFY_RUNTIME_UNAVAILABLE");
  process.exit(2);
}

const steps = [
  ["r17-references", ["run", "verify:r17-references"]],
  ["r17-contracts", ["run", "verify:r17-contracts"]],
  ["r17-godot-candidates", ["run", "verify:r17-godot"]],
  ["r17-agent-candidates", ["run", "verify:r17-agent"]],
  ["r17-frozen-evidence", ["run", "test:r18-sources"]],
];

for (const [id, args] of steps) {
  console.log(`R18_VERIFY_STEP_START ${id}`);
  const result = spawnSync(process.execPath, [npmExecPath, ...args], {
    stdio: "inherit",
    shell: false,
    windowsHide: true,
  });
  if (result.error || result.status !== 0) {
    console.error(`R18_VERIFY_STEP_FAILED ${id}`);
    process.exit(result.status ?? 1);
  }
  console.log(`R18_VERIFY_STEP_OK ${id}`);
}

console.log(`R18_VERIFY_OK steps=${steps.length}`);
