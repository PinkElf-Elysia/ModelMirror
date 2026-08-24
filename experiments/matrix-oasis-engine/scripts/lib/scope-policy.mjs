export const MODULE_PREFIX = "experiments/matrix-oasis-engine";
export const ACTIVE_ROUND = "R17";
export const ACTIVE_ROUND_BASELINE_SHA =
  "bb94ab37148d4278936641f6fffc9adeff595e7c";

export const ROUND_ALLOWED_MODULE_FILES = Object.freeze([
  "AGENTS.md",
  "README.md",
  ".gitattributes",
  ".gitignore",
  "module-boundary.json",
  "package-lock.json",
  "package.json",
  "docs/ARCHITECTURE.md",
  "docs/BOUNDARIES.md",
  "docs/DEPENDENCIES_AND_LICENSES.md",
  "docs/KNOWN_LIMITATIONS.md",
  "docs/PRODUCT.md",
  "docs/V1_CRITICAL_PATH.md",
  "docs/MVP_STATUS.json",
  "docs/V2_STATUS.json",
  "docs/R17_V2_QUALIFICATION_THREAT_MODEL.md",
  "docs/R17_TASK_CARD.md",
  "docs/R17_CANDIDATE_EXECUTION_APPROVAL.md",
  "docs/R17_V2_ARCHITECTURE_AUDIT.md",
  "docs/R17_V2_SELECTION_MATRIX.md",
  "docs/R17_QUALIFICATION_SUMMARY.json",
  "docs/adr/0018-r17-v2-qualification-governance.md",
  "docs/rounds/R17_ACCEPTANCE.md",
  "scripts/check-round-scope.mjs",
  "scripts/check-mvp-claim.mjs",
  "scripts/check-v2-claim.mjs",
  "scripts/check-godot-boundary.mjs",
  "scripts/plan-r17-qualification.mjs",
  "scripts/qualify-r17.mjs",
  "scripts/verify-r17-evidence.mjs",
  "scripts/verify-r17-references.mjs",
  "scripts/verify-r17.mjs",
  "scripts/lib/boundary-core.mjs",
  "scripts/lib/mvp-claim-core.mjs",
  "scripts/lib/v2-claim-core.mjs",
  "scripts/lib/r17-reference-core.mjs",
  "scripts/lib/r17-qualification-core.mjs",
  "scripts/lib/r17-evidence-core.mjs",
  "scripts/lib/r17-godot-qualification-core.mjs",
  "scripts/lib/parent-scope-core.mjs",
  "scripts/lib/scope-policy.mjs",
  "scripts/run-verify.mjs",
  "scripts/verify-extraction.mjs",
  "tests/boundary.test.mjs",
  "tests/godot-boundary.test.mjs",
  "tests/extraction-contract.test.mjs",
  "tests/mvp-claim.test.mjs",
  "tests/v2-claim.test.mjs",
  "tests/r17-reference.test.mjs",
  "tests/r17-qualification.test.mjs",
  "tests/r17-evidence.test.mjs",
  "tests/r17-godot-qualification.test.mjs",
  "tests/round-scope.test.mjs",
]);

export const ROUND_ALLOWED_MODULE_PREFIXES = Object.freeze([
  "packages/v2-qualification-contracts",
  "packages/v2-qualification-harness",
  "third-party/v2-qualification-references",
]);

// Exact allowlist entries above intentionally override these broad frozen roots.
// Everything inherited from R1-R16 therefore remains frozen unless R17 names it.
export const ROUND_FROZEN_MODULE_PATHS = Object.freeze([
  "apps",
  "docs",
  "examples",
  "packages",
  "scripts",
  "tests",
  "third-party",
]);
