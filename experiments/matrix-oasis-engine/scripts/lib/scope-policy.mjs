export const MODULE_PREFIX = "experiments/matrix-oasis-engine";
export const ACTIVE_ROUND = "R15";
export const ACTIVE_ROUND_BASELINE_SHA =
  "4be3e9483e57f792769c079d3c985a357e99a558";

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
  "docs/R15_RUNTIME_EVIDENCE_THREAT_MODEL.md",
  "docs/R15_TASK_CARD.md",
  "docs/adr/0016-r15-runtime-evidence-governance.md",
  "docs/rounds/R15_ACCEPTANCE.md",
  "scripts/check-round-scope.mjs",
  "scripts/check-mvp-claim.mjs",
  "scripts/check-godot-boundary.mjs",
  "scripts/plan-r15-replay.mjs",
  "scripts/collect-r15-runtime-evidence.mjs",
  "scripts/qualify-r15-runtime-evidence.mjs",
  "scripts/preview-r15.mjs",
  "scripts/capture-r15.mjs",
  "scripts/verify-r15.mjs",
  "scripts/lib/boundary-core.mjs",
  "scripts/lib/mvp-claim-core.mjs",
  "scripts/lib/parent-scope-core.mjs",
  "scripts/lib/runtime-evidence-core.mjs",
  "scripts/lib/runtime-evidence-cache-core.mjs",
  "scripts/lib/r15-preview-core.mjs",
  "scripts/lib/scope-policy.mjs",
  "scripts/run-verify.mjs",
  "scripts/verify-extraction.mjs",
  "tests/boundary.test.mjs",
  "tests/godot-boundary.test.mjs",
  "tests/extraction-contract.test.mjs",
  "tests/mvp-claim.test.mjs",
  "tests/prototype-asset-cli.test.mjs",
  "tests/r14-preview.test.mjs",
  "tests/r15-runtime-evidence.test.mjs",
  "tests/r15-runtime-evidence-godot.test.mjs",
  "tests/r15-runtime-evidence-cache.test.mjs",
  "tests/r15-qualification.test.mjs",
  "tests/round-scope.test.mjs",
]);

export const ROUND_ALLOWED_MODULE_PREFIXES = Object.freeze([
  "packages/prototype-runtime-evidence-contracts",
  "packages/prototype-runtime-evidence",
  "apps/runtime-godot/runtime_evidence",
]);

// Exact allowlist entries above intentionally override these broad frozen roots.
// Everything inherited from R1-R14 therefore remains frozen unless R15 names it.
export const ROUND_FROZEN_MODULE_PATHS = Object.freeze([
  "apps",
  "docs",
  "examples",
  "packages",
  "scripts",
  "tests",
  "third-party",
]);
