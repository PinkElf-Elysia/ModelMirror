export const MODULE_PREFIX = "experiments/matrix-oasis-engine";
export const ACTIVE_ROUND = "R9";
export const ACTIVE_ROUND_BASELINE_SHA =
  "da5fd0fe39234807ae3c4a1d543b9fd64de66d97";

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
  "docs/PROTOTYPE_ASSET_BUNDLE.md",
  "docs/PROTOTYPE_ASSET_THREAT_MODEL.md",
  "docs/MESHY_CALL_APPROVAL.md",
  "docs/adr/0010-r9-asset-materialization-governance.md",
  "docs/rounds/R9_ACCEPTANCE.md",
  "scripts/check-round-scope.mjs",
  "scripts/plan-prototype-assets.mjs",
  "scripts/qualify-meshy-asset.mjs",
  "scripts/materialize-prototype-assets.mjs",
  "scripts/verify-prototype-assets-godot.mjs",
  "scripts/lib/boundary-core.mjs",
  "scripts/lib/parent-scope-core.mjs",
  "scripts/lib/prototype-asset-cli-core.mjs",
  "scripts/lib/scope-policy.mjs",
  "scripts/run-verify.mjs",
  "scripts/verify-extraction.mjs",
  "tests/boundary.test.mjs",
  "tests/extraction-contract.test.mjs",
  "tests/prototype-asset-cli.test.mjs",
  "tests/prototype-asset-qualification.test.mjs",
  "tests/prototype-asset-godot.test.mjs",
  "tests/round-scope.test.mjs",
]);

export const ROUND_ALLOWED_MODULE_PREFIXES = Object.freeze([
  "packages/prototype-asset-contracts",
  "packages/prototype-asset-pipeline",
]);

// Exact allowlist entries above intentionally override these broad frozen roots.
// Everything inherited from R1-R8 therefore remains frozen unless R9 names it.
export const ROUND_FROZEN_MODULE_PATHS = Object.freeze([
  "apps",
  "docs",
  "examples",
  "packages",
  "scripts",
  "tests",
  "third-party",
]);
