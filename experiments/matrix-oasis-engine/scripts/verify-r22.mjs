import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { checkV2Claim } from "./lib/v2-claim-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

const steps = [
  ["references", ["run", "verify:r22-references"]],
  ["contracts", ["run", "verify:npc-cognition-contracts"]],
  ["runtime", ["run", "verify:npc-cognition-runtime"]],
  ["provider", ["run", "verify:npc-cognition-provider"]],
  ["godot", ["run", "verify:npc-cognition-godot"]],
  ["round-scope", ["run", "check:round-scope"]],
  ["boundary", ["run", "check:boundary"]],
  ["v2-claim", ["run", "check:v2-claim"]],
];

function readJson(relativePath) {
  return JSON.parse(readFileSync(path.join(moduleRoot, relativePath), "utf8"));
}

function requireText(relativePath, requiredFragments) {
  const source = readFileSync(path.join(moduleRoot, relativePath), "utf8");
  for (const fragment of requiredFragments) {
    if (!source.includes(fragment)) throw new Error("R22_GOVERNANCE_DOCUMENT_INVALID");
  }
}

try {
  const manifest = readJson("package.json");
  const boundary = readJson("module-boundary.json");
  const claim = checkV2Claim({ moduleRoot });

  if (
    manifest.version !== "0.22.0-r22" ||
    boundary.schemaVersion !== 22 ||
    boundary.activeRound !== "R22" ||
    boundary.activeRoundBaselineSha !== "e927db557f71db420e07a49818c0d4ae1e0d6ce3" ||
    boundary.v2ClaimPolicy?.qualificationProfile !== "matrix-oasis.bounded-npc-cognition/1" ||
    claim.status !== "r22-bounded-cognition-in-progress" ||
    claim.claimAllowed !== false ||
    claim.blockingRound !== "R25"
  ) {
    throw new Error("R22_GOVERNANCE_POLICY_INVALID");
  }

  requireText("docs/R22_TASK_CARD.md", [
    "R22_DIALOGUE_BUDGET_ENFORCED",
    "R22_FALLBACK_PLAYABLE",
    "R22_UNTRUSTED_OUTPUT_ADJUDICATED",
    "当前计划授权不等于该次付费调用授权",
  ]);
  requireText("docs/R22_MINIMUM_SEMANTICS.md", [
    "最多发生一次非流式请求",
    "R19权威裁决",
    "不产生隐藏Action",
    "不把对白包装成长期记忆",
  ]);
  requireText("docs/R22_BOUNDED_COGNITION_THREAT_MODEL.md", [
    "提示注入",
    "重复收费",
    "审批换身",
    "权威倒置",
  ]);
  requireText("docs/adr/0023-r22-bounded-cognition-governance.md", [
    "模型只可返回纯文本对白",
    "不等于ZDR",
    "不允许模型写Persona",
  ]);

  const npmExecPath = process.env.npm_execpath;
  if (!npmExecPath) throw new Error("R22_VERIFY_RUNTIME_UNAVAILABLE");
  const scopeTests = spawnSync(process.execPath, [
    "--test",
    "tests/round-scope.test.mjs",
    "tests/v2-claim.test.mjs",
  ], {
    cwd: moduleRoot,
    stdio: "inherit",
    shell: false,
    windowsHide: true,
  });
  if (scopeTests.error || scopeTests.status !== 0) {
    throw new Error("R22_VERIFY_FAILED_scope-tests");
  }
  const liveTests = spawnSync(process.execPath, [
    "--test",
    "tests/r22-cli.test.mjs",
    "tests/r22-qualification.test.mjs",
    "tests/r22-falsification.test.mjs",
    "tests/r22-real-cache.test.mjs",
    "tests/r22-live-composition.test.mjs",
    "tests/r22-live-evidence.test.mjs",
    "tests/r22-live-preview.test.mjs",
    "tests/r22-live-process.test.mjs",
    "tests/r22-live-provider.test.mjs",
    "tests/r22-live-recovery.test.mjs",
    "tests/r22-diagnostic-budget.test.mjs",
    "tests/r22-diagnostic-transaction.test.mjs",
    "tests/r22-diagnostic-transport.test.mjs",
    "tests/r22-live-recovery-process.test.mjs",
    "tests/r22-recovered-behavior.test.mjs",
    "tests/r22-recovered-coordinator.test.mjs",
    "tests/r20-selector-injection.test.mjs",
    "tests/r20-selector-enumeration.test.mjs",
  ], { cwd: moduleRoot, stdio: "inherit", shell: false, windowsHide: true });
  if (liveTests.error || liveTests.status !== 0) throw new Error("R22_VERIFY_FAILED_live-tests");
  for (const [id, args] of steps) {
    const result = spawnSync(process.execPath, [npmExecPath, ...args], {
      cwd: moduleRoot,
      stdio: "inherit",
      shell: false,
      windowsHide: true,
    });
    if (result.error || result.status !== 0) throw new Error(`R22_VERIFY_FAILED_${id}`);
  }

  console.log("R22_GOVERNANCE_GATES_OK");
} catch {
  console.error("R22_VERIFY_FAILED");
  process.exitCode = 1;
}
