import { readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { checkV2Claim } from "./lib/v2-claim-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

const steps = [
  ["references", ["run", "verify:r21-references"]],
  ["contracts", ["run", "verify:npc-derived-state-contracts"]],
  ["derived-state", ["run", "verify:npc-derived-state"]],
  ["round-scope", ["run", "check:round-scope"]],
  ["boundary", ["run", "check:boundary"]],
  ["v2-claim", ["run", "check:v2-claim"]],
];

function readJson(relativePath) {
  return JSON.parse(readFileSync(path.join(moduleRoot, relativePath), "utf8"));
}

function requireText(relativePath, requiredFragments) {
  const text = readFileSync(path.join(moduleRoot, relativePath), "utf8");
  for (const fragment of requiredFragments) {
    if (!text.includes(fragment)) {
      throw new Error("R21_GOVERNANCE_DOCUMENT_INVALID");
    }
  }
}

try {
  const manifest = readJson("package.json");
  const boundary = readJson("module-boundary.json");
  const claim = checkV2Claim({ moduleRoot });

  if (
    manifest.version !== "0.21.0-r21" ||
    boundary.schemaVersion !== 21 ||
    boundary.activeRound !== "R21" ||
    boundary.activeRoundBaselineSha !== "cbb50f1095a51f2c32958ab4f7dd4e34dadfc2c2" ||
    boundary.v2ClaimPolicy?.qualificationProfile !== "matrix-oasis.npc-derived-state/1" ||
    claim.status !== "r21-derived-state-qualified" ||
    claim.claimAllowed !== false ||
    claim.blockingRound !== "R25"
  ) {
    throw new Error("R21_GOVERNANCE_POLICY_INVALID");
  }

  requireText("docs/R21_TASK_CARD.md", [
    "R21_LEDGER_REBUILD_EQUIVALENT",
    "R21_MEMORY_DELETION_VERIFIED",
    "R21_RELATIONSHIP_PROJECTION_DETERMINISTIC",
  ]);
  requireText("docs/R21_MINIMUM_SEMANTICS.md", [
    "Persona是可信、版本化、闭合字段的静态seed",
    "Memory只记录actor自身已被R19接受的Action",
    "拒绝事件贡献为零",
    "跨timeline、跨reset合并、迁移和比较均不支持",
    "选择性forget、单条删除或correction",
  ]);
  requireText("docs/R21_DERIVED_STATE_THREAT_MODEL.md", [
    "权威倒置",
    "伪造投影证据",
    "跨时间线污染",
    "虚假删除",
  ]);
  requireText("docs/adr/0022-r21-derived-state-governance.md", [
    "不引入Mem0、Letta、Graphiti或其他外部索引作为生产依赖",
    "R19 Projection Manifest继续只承担身份绑定",
  ]);
  requireText("docs/R21_DERIVED_STATE.md", [
    "Runtime仍是游戏状态唯一权威",
    "不是安全字节擦除",
    "project:npc-derived-state",
  ]);
  requireText("docs/rounds/R21_FALSIFICATION_EVIDENCE.md", [
    "10,000条Ledger",
    "历史未完成时间线",
    "R20源树SHA-256",
  ]);
  requireText("docs/rounds/R21_ACCEPTANCE.md", [
    "r21-derived-state-qualified",
    "claimAllowed=false",
    "等待用户决定是否允许push和创建PR",
  ]);

  const npmExecPath = process.env.npm_execpath;
  if (!npmExecPath) throw new Error("R21_VERIFY_RUNTIME_UNAVAILABLE");
  for (const [id, args] of steps) {
    const result = spawnSync(process.execPath, [npmExecPath, ...args], {
      cwd: moduleRoot,
      stdio: "inherit",
      shell: false,
      windowsHide: true,
    });
    if (result.error || result.status !== 0) throw new Error(`R21_VERIFY_FAILED_${id}`);
  }

  console.log("R21_AUTOMATED_GATES_OK");
} catch {
  console.error("R21_AUTOMATED_GATES_INVALID");
  process.exitCode = 1;
}
