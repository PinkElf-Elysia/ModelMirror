export const MODULE_PREFIX = "experiments/matrix-oasis-engine";
export const ACTIVE_ROUND = "R8";
export const ACTIVE_ROUND_BASELINE_SHA =
  "21cbbb8b943b6f9d9799f014c44a6349e6124a63";

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
  "docs/PROTOTYPE_GENERATION.md",
  "docs/PROTOTYPE_GENERATION_THREAT_MODEL.md",
  "docs/MODEL_CALL_APPROVAL.md",
  "docs/adr/0009-r8-natural-language-prototype-governance.md",
  "docs/rounds/R8_ACCEPTANCE.md",
  "scripts/check-round-scope.mjs",
  "scripts/generate-prototype.mjs",
  "scripts/plan-prototype-call.mjs",
  "scripts/qualify-prototype-model.mjs",
  "scripts/lib/boundary-core.mjs",
  "scripts/lib/parent-scope-core.mjs",
  "scripts/lib/prototype-cli-core.mjs",
  "scripts/lib/scope-policy.mjs",
  "scripts/run-verify.mjs",
  "scripts/verify-extraction.mjs",
  "tests/boundary.test.mjs",
  "tests/extraction-contract.test.mjs",
  "tests/prototype-generation-cli.test.mjs",
  "tests/prototype-model-qualification.test.mjs",
  "tests/round-scope.test.mjs",
]);

export const ROUND_ALLOWED_MODULE_PREFIXES = Object.freeze([
  "packages/prototype-generation-contracts",
  "packages/prototype-generator",
]);

// Exact allowlist entries above intentionally override these broad frozen roots.
// Everything inherited from R1-R7 therefore remains frozen unless R8 names it.
export const ROUND_FROZEN_MODULE_PATHS = Object.freeze([
  "apps",
  "docs",
  "examples",
  "packages",
  "scripts",
  "tests",
  "third-party",
]);
