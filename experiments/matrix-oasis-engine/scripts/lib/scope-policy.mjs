export const MODULE_PREFIX = "experiments/matrix-oasis-engine";
export const ACTIVE_ROUND = "R11";
export const ACTIVE_ROUND_BASELINE_SHA =
  "da2a914a2ff131507750a0afb8d8881180530f62";

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
  "docs/PROTOTYPE_SPATIAL_ENVIRONMENT.md",
  "docs/PROTOTYPE_SPATIAL_THREAT_MODEL.md",
  "docs/adr/0012-r11-spatial-environment-governance.md",
  "docs/rounds/R11_ACCEPTANCE.md",
  "apps/creator-web/package.json",
  "apps/creator-web/src/App.tsx",
  "apps/creator-web/src/styles.css",
  "apps/creator-web/src/prototype-builder.ts",
  "scripts/check-round-scope.mjs",
  "scripts/check-godot-boundary.mjs",
  "scripts/qualify-spatial-environment.mjs",
  "scripts/import-spatial-prototype-cache.mjs",
  "scripts/preview-spatial-prototype.mjs",
  "scripts/verify-spatial-environment.mjs",
  "scripts/verify-spatial-assembly.mjs",
  "scripts/verify-godot-splat.mjs",
  "scripts/verify-spatial-builder.mjs",
  "scripts/verify-r11.mjs",
  "scripts/lib/boundary-core.mjs",
  "scripts/lib/parent-scope-core.mjs",
  "scripts/lib/spatial-cache-core.mjs",
  "scripts/lib/scope-policy.mjs",
  "scripts/run-verify.mjs",
  "scripts/verify-extraction.mjs",
  "tests/boundary.test.mjs",
  "tests/godot-boundary.test.mjs",
  "tests/extraction-contract.test.mjs",
  "tests/spatial-environment.test.mjs",
  "tests/spatial-assembly.test.mjs",
  "tests/godot-splat.test.mjs",
  "tests/spatial-builder.test.mjs",
  "tests/r11-qualification.test.mjs",
  "tests/round-scope.test.mjs",
  "third-party/godot-gaussian-splatting.lock.json",
  "third-party/godot-gaussian-splatting/LICENSE",
]);

export const ROUND_ALLOWED_MODULE_PREFIXES = Object.freeze([
  "packages/prototype-spatial-environment",
  "packages/prototype-spatial-assembler",
  "apps/runtime-godot/spatial_prototype",
  "apps/runtime-godot/addons/gdgs",
]);

// Exact allowlist entries above intentionally override these broad frozen roots.
// Everything inherited from R1-R10 therefore remains frozen unless R11 names it.
export const ROUND_FROZEN_MODULE_PATHS = Object.freeze([
  "apps",
  "docs",
  "examples",
  "packages",
  "scripts",
  "tests",
  "third-party",
]);
