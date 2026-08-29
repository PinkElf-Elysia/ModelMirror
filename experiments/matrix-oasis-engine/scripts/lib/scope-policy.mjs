export const MODULE_PREFIX = "experiments/matrix-oasis-engine";
export const ACTIVE_ROUND = "R20";
export const ACTIVE_ROUND_BASELINE_SHA =
  "1ef7b86e4c9d5ab57b5e83fc9e0cadccff14375a";

export const ROUND_ALLOWED_MODULE_FILES = Object.freeze([
  "AGENTS.md",
  "module-boundary.json",
  "package-lock.json",
  "package.json",
  "docs/V2_STATUS.json",
  "docs/R20_NPC_BRIDGE.md",
  "docs/R20_NPC_BRIDGE_THREAT_MODEL.md",
  "docs/R20_REFERENCE_AUDIT.md",
  "docs/R20_TASK_CARD.md",
  "docs/adr/0021-r20-deterministic-npc-bridge-governance.md",
  "docs/rounds/R20_ACCEPTANCE.md",
  "scripts/check-godot-boundary.mjs",
  "scripts/check-round-scope.mjs",
  "scripts/synthesize-npc-behavior.mjs",
  "scripts/create-npc-authority-session.mjs",
  "scripts/qualify-r20-npc-bridge.mjs",
  "scripts/preview-r20.mjs",
  "scripts/capture-r20.mjs",
  "scripts/verify-r20-references.mjs",
  "scripts/verify-r20.mjs",
  "scripts/lib/boundary-core.mjs",
  "scripts/lib/v2-claim-core.mjs",
  "scripts/lib/r20-cli-core.mjs",
  "scripts/lib/r20-host-core.mjs",
  "scripts/lib/scope-policy.mjs",
  "scripts/run-verify.mjs",
  "tests/boundary.test.mjs",
  "tests/extraction-contract.test.mjs",
  "tests/v2-claim.test.mjs",
  "tests/r20-reference.test.mjs",
  "tests/r20-session.test.mjs",
  "tests/r20-scheduler.test.mjs",
  "tests/r20-godot-bridge.test.mjs",
  "tests/r20-falsification.test.mjs",
  "tests/round-scope.test.mjs",
  "packages/npc-authority-runtime/src/authority.mjs",
  "packages/npc-authority-runtime/src/index.d.ts",
  "packages/npc-authority-runtime/src/index.mjs",
  "packages/npc-authority-runtime/src/ledger.mjs",
  "packages/npc-authority-runtime/tests/authority.test.mjs",
  "packages/npc-authority-runtime/tests/ledger.test.mjs",
]);

export const ROUND_ALLOWED_MODULE_PREFIXES = Object.freeze([
  "packages/npc-behavior-contracts",
  "packages/npc-behavior-runtime",
  "packages/npc-authority-session",
  "apps/runtime-godot/npc_authority_prototype",
  "third-party/npc-behavior-references",
  "tests/fixtures/r20",
]);

// Exact entries above are the complete R19 compatibility-extraction surface.
// Everything inherited from R1-R19 remains frozen unless R20 names it.
export const ROUND_FROZEN_MODULE_PATHS = Object.freeze([
  "apps",
  "docs",
  "examples",
  "packages",
  "scripts",
  "tests",
  "third-party",
]);
