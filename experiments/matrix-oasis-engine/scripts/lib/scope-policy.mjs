export const MODULE_PREFIX = "experiments/matrix-oasis-engine";
export const ACTIVE_ROUND = "R21";
export const ACTIVE_ROUND_BASELINE_SHA =
  "cbb50f1095a51f2c32958ab4f7dd4e34dadfc2c2";

export const ROUND_ALLOWED_MODULE_FILES = Object.freeze([
  "AGENTS.md",
  "module-boundary.json",
  "package-lock.json",
  "package.json",
  "docs/V2_STATUS.json",
  "docs/R21_DERIVED_STATE.md",
  "docs/R21_DERIVED_STATE_THREAT_MODEL.md",
  "docs/R21_MINIMUM_SEMANTICS.md",
  "docs/R21_REFERENCE_AUDIT.md",
  "docs/R21_TASK_CARD.md",
  "docs/adr/0022-r21-derived-state-governance.md",
  "docs/rounds/R21_ACCEPTANCE.md",
  "docs/rounds/R21_FALSIFICATION_EVIDENCE.md",
  "scripts/check-round-scope.mjs",
  "scripts/project-npc-derived-state.mjs",
  "scripts/validate-npc-derived-state.mjs",
  "scripts/verify-npc-derived-state.mjs",
  "scripts/qualify-r21.mjs",
  "scripts/verify-r21-references.mjs",
  "scripts/verify-r21.mjs",
  "scripts/lib/boundary-core.mjs",
  "scripts/lib/v2-claim-core.mjs",
  "scripts/lib/r21-cli-core.mjs",
  "scripts/lib/r21-projection-core.mjs",
  "scripts/lib/r21-qualification-core.mjs",
  "scripts/lib/r21-reference-core.mjs",
  "scripts/lib/scope-policy.mjs",
  "scripts/run-verify.mjs",
  "tests/extraction-contract.test.mjs",
  "tests/v2-claim.test.mjs",
  "tests/round-scope.test.mjs",
  "tests/r18-sources.test.mjs",
  "tests/r20-gate-truthfulness.test.mjs",
  "tests/r21-cli.test.mjs",
  "tests/r21-falsification.test.mjs",
  "tests/r21-projection.test.mjs",
  "tests/r21-qualification.test.mjs",
  "tests/r21-real-cache.test.mjs",
  "tests/r21-reference.test.mjs",
]);

export const ROUND_ALLOWED_MODULE_PREFIXES = Object.freeze([
  "packages/npc-derived-state-contracts",
  "packages/npc-derived-state-runtime",
  "third-party/npc-derived-state-references",
  "tests/fixtures/r21",
]);

// Everything inherited from R1-R20 remains frozen unless R21 names it.
export const ROUND_FROZEN_MODULE_PATHS = Object.freeze([
  "apps",
  "docs",
  "examples",
  "packages",
  "scripts",
  "tests",
  "third-party",
]);
