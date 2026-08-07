export const MODULE_PREFIX = "experiments/matrix-oasis-engine";
export const ACTIVE_ROUND = "R2";
export const ACTIVE_ROUND_BASELINE_SHA =
  "a8e627e217c8c9e2cb8cca83fea8542c47edaeba";

export const ROUND_ALLOWED_MODULE_ROOT_FILES = Object.freeze([
  "AGENTS.md",
  "README.md",
  "module-boundary.json",
  "package-lock.json",
  "package.json",
]);

export const ROUND_ALLOWED_MODULE_PREFIXES = Object.freeze([
  "apps/creator-web",
  "docs",
  "packages/game-pack-simulator",
  "scripts",
  "tests",
]);

export const ROUND_FROZEN_MODULE_PATHS = Object.freeze([
  "docs/AUTHORING_GAME_PACK.md",
  "docs/adr/0001-isolated-experiment-module.md",
  "docs/adr/0002-r1-active-round-governance.md",
  "docs/rounds/R0_ACCEPTANCE.md",
  "docs/rounds/R1_ACCEPTANCE.md",
  "examples",
  "packages/game-pack-contracts",
  "packages/game-pack-validator",
]);
