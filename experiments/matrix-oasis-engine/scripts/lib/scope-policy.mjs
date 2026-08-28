export const MODULE_PREFIX = "experiments/matrix-oasis-engine";
export const ACTIVE_ROUND = "R19";
export const ACTIVE_ROUND_BASELINE_SHA =
  "821067a7db4811a3f3f1fd649e4fdfade9eafb22";

export const ROUND_ALLOWED_MODULE_FILES = Object.freeze([
  "AGENTS.md",
  "module-boundary.json",
  "package-lock.json",
  "package.json",
  "docs/V2_STATUS.json",
  "docs/R19_NPC_AUTHORITY.md",
  "docs/R19_NPC_AUTHORITY_THREAT_MODEL.md",
  "docs/R19_REFERENCE_AUDIT.md",
  "docs/R19_TASK_CARD.md",
  "docs/adr/0020-r19-npc-authority-governance.md",
  "docs/rounds/R19_ACCEPTANCE.md",
  "scripts/check-round-scope.mjs",
  "scripts/create-npc-authority-timeline.mjs",
  "scripts/adjudicate-npc-intent.mjs",
  "scripts/replay-world-event-ledger.mjs",
  "scripts/validate-npc-authority.mjs",
  "scripts/verify-r19-references.mjs",
  "scripts/verify-r19.mjs",
  "scripts/lib/boundary-core.mjs",
  "scripts/lib/v2-claim-core.mjs",
  "scripts/lib/r19-cli-core.mjs",
  "scripts/lib/scope-policy.mjs",
  "scripts/run-verify.mjs",
  "tests/boundary.test.mjs",
  "tests/extraction-contract.test.mjs",
  "tests/v2-claim.test.mjs",
  "tests/r19-reference.test.mjs",
  "tests/r19-cli.test.mjs",
  "tests/r19-falsification.test.mjs",
  "tests/round-scope.test.mjs",
]);

export const ROUND_ALLOWED_MODULE_PREFIXES = Object.freeze([
  "packages/npc-authority-contracts",
  "packages/npc-authority-runtime",
  "third-party/npc-authority-references",
  "tests/fixtures/r19",
]);

// Exact allowlist entries above intentionally override these broad frozen roots.
// Everything inherited from R1-R18 therefore remains frozen unless R19 names it.
export const ROUND_FROZEN_MODULE_PATHS = Object.freeze([
  "apps",
  "docs",
  "examples",
  "packages",
  "scripts",
  "tests",
  "third-party",
]);
