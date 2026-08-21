export const MODULE_PREFIX = "experiments/matrix-oasis-engine";
export const ACTIVE_ROUND = "R14";
export const ACTIVE_ROUND_BASELINE_SHA =
  "296e560d5197ff1367ad75455b2b9f5852560fd8";

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
  "docs/R14_SPATIAL_SOLVER_THREAT_MODEL.md",
  "docs/R14_TASK_CARD.md",
  "docs/adr/0015-r14-spatial-solver-governance.md",
  "docs/rounds/R14_ACCEPTANCE.md",
  "scripts/check-round-scope.mjs",
  "scripts/check-mvp-claim.mjs",
  "scripts/check-godot-boundary.mjs",
  "scripts/synthesize-spatial-intent.mjs",
  "scripts/solve-spatial-layout.mjs",
  "scripts/verify-spatial-solution.mjs",
  "scripts/qualify-r14-spatial-solver.mjs",
  "scripts/preview-r14.mjs",
  "scripts/capture-r14.mjs",
  "scripts/verify-r14.mjs",
  "scripts/preview-prototype.mjs",
  "scripts/lib/boundary-core.mjs",
  "scripts/lib/mvp-claim-core.mjs",
  "scripts/lib/parent-scope-core.mjs",
  "scripts/lib/spatial-solution-core.mjs",
  "scripts/lib/solved-spatial-cache-core.mjs",
  "scripts/lib/r14-preview-core.mjs",
  "scripts/lib/scope-policy.mjs",
  "scripts/run-verify.mjs",
  "scripts/verify-extraction.mjs",
  "tests/boundary.test.mjs",
  "tests/godot-boundary.test.mjs",
  "tests/extraction-contract.test.mjs",
  "tests/mvp-claim.test.mjs",
  "tests/spatial-solution.test.mjs",
  "tests/spatial-verifier.test.mjs",
  "tests/spatial-visual-safety.test.mjs",
  "tests/r14-preview.test.mjs",
  "tests/r14-qualification.test.mjs",
  "tests/round-scope.test.mjs",
]);

export const ROUND_ALLOWED_MODULE_PREFIXES = Object.freeze([
  "packages/prototype-spatial-solution-contracts",
  "packages/prototype-spatial-solver",
  "packages/prototype-spatial-verifier",
  "apps/runtime-godot/spatial_solution_verification",
  "apps/runtime-godot/solved_spatial_prototype",
]);

// Exact allowlist entries above intentionally override these broad frozen roots.
// Everything inherited from R1-R13 therefore remains frozen unless R14 names it.
export const ROUND_FROZEN_MODULE_PATHS = Object.freeze([
  "apps",
  "docs",
  "examples",
  "packages",
  "scripts",
  "tests",
  "third-party",
]);
