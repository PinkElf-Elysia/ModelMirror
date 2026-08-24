export const MODULE_PREFIX = "experiments/matrix-oasis-engine";
export const ACTIVE_ROUND = "R16";
export const ACTIVE_ROUND_BASELINE_SHA =
  "7c837fe3908a4a5b60551778313624f53bcd0d1b";

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
  "docs/R16_CREATOR_QUALIFICATION_THREAT_MODEL.md",
  "docs/R16_TASK_CARD.md",
  "docs/adr/0017-r16-creator-qualification-governance.md",
  "docs/rounds/R16_ACCEPTANCE.md",
  "scripts/check-round-scope.mjs",
  "scripts/check-mvp-claim.mjs",
  "scripts/check-godot-boundary.mjs",
  "scripts/qualify-r16-creator.mjs",
  "scripts/preview-r16.mjs",
  "scripts/capture-r16.mjs",
  "scripts/verify-r16.mjs",
  "scripts/preview-r12.mjs",
  "scripts/lib/boundary-core.mjs",
  "scripts/lib/mvp-claim-core.mjs",
  "scripts/lib/parent-scope-core.mjs",
  "scripts/lib/prototype-host-core.mjs",
  "scripts/lib/r12-host-core.mjs",
  "scripts/lib/creator-qualification-cache-core.mjs",
  "scripts/lib/r16-creator-core.mjs",
  "scripts/lib/r16-preview-core.mjs",
  "scripts/lib/scope-policy.mjs",
  "scripts/run-verify.mjs",
  "scripts/verify-extraction.mjs",
  "tests/boundary.test.mjs",
  "tests/godot-boundary.test.mjs",
  "tests/extraction-contract.test.mjs",
  "tests/mvp-claim.test.mjs",
  "tests/prototype-host.test.mjs",
  "tests/prototype-builder.test.mjs",
  "tests/r16-creator-qualification-cache.test.mjs",
  "tests/r16-creator-qualification.test.mjs",
  "tests/r16-host.test.mjs",
  "tests/r16-preview.test.mjs",
  "tests/r16-generalization.test.mjs",
  "tests/r14-preview.test.mjs",
  "tests/round-scope.test.mjs",
  "apps/creator-web/src/App.tsx",
  "apps/creator-web/src/prototype-builder.ts",
  "apps/creator-web/src/styles.css",
]);

export const ROUND_ALLOWED_MODULE_PREFIXES = Object.freeze([
  "packages/prototype-creator-qualification-contracts",
  "packages/prototype-creator-qualification",
]);

// Exact allowlist entries above intentionally override these broad frozen roots.
// Everything inherited from R1-R15 therefore remains frozen unless R16 names it.
export const ROUND_FROZEN_MODULE_PATHS = Object.freeze([
  "apps",
  "docs",
  "examples",
  "packages",
  "scripts",
  "tests",
  "third-party",
]);
