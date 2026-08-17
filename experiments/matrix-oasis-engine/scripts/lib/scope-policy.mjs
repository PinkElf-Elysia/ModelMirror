export const MODULE_PREFIX = "experiments/matrix-oasis-engine";
export const ACTIVE_ROUND = "R13";
export const ACTIVE_ROUND_BASELINE_SHA =
  "77ec8c4eace9f8dbd1dd119cd70727570bd99e9a";

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
  "docs/R13_SPATIAL_FACTS_THREAT_MODEL.md",
  "docs/R13_TASK_CARD.md",
  "docs/adr/0014-r13-spatial-facts-governance.md",
  "docs/rounds/R13_ACCEPTANCE.md",
  "scripts/check-round-scope.mjs",
  "scripts/check-mvp-claim.mjs",
  "scripts/check-godot-boundary.mjs",
  "scripts/analyze-spatial-environment.mjs",
  "scripts/qualify-r13-spatial-facts.mjs",
  "scripts/capture-spatial-facts.mjs",
  "scripts/verify-spatial-references.mjs",
  "scripts/verify-spatial-analysis.mjs",
  "scripts/verify-r13.mjs",
  "scripts/lib/boundary-core.mjs",
  "scripts/lib/mvp-claim-core.mjs",
  "scripts/lib/parent-scope-core.mjs",
  "scripts/lib/spatial-analysis-core.mjs",
  "scripts/lib/spatial-reference-core.mjs",
  "scripts/lib/scope-policy.mjs",
  "scripts/run-verify.mjs",
  "scripts/verify-extraction.mjs",
  "tests/boundary.test.mjs",
  "tests/godot-boundary.test.mjs",
  "tests/extraction-contract.test.mjs",
  "tests/mvp-claim.test.mjs",
  "tests/spatial-analysis.test.mjs",
  "tests/spatial-reference.test.mjs",
  "tests/r13-qualification.test.mjs",
  "tests/round-scope.test.mjs",
]);

export const ROUND_ALLOWED_MODULE_PREFIXES = Object.freeze([
  "packages/prototype-spatial-planning-contracts",
  "packages/prototype-environment-analyzer",
  "apps/runtime-godot/spatial_analysis",
  "third-party/spatial-layout-references",
]);

// Exact allowlist entries above intentionally override these broad frozen roots.
// Everything inherited from R1-R12 therefore remains frozen unless R13 names it.
export const ROUND_FROZEN_MODULE_PATHS = Object.freeze([
  "apps",
  "docs",
  "examples",
  "packages",
  "scripts",
  "tests",
  "third-party",
]);
