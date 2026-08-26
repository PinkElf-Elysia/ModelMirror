import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  ParentScopeError,
  checkRoundScope,
} from "./lib/parent-scope-core.mjs";
import {
  ACTIVE_ROUND,
  ACTIVE_ROUND_BASELINE_SHA,
  ROUND_ALLOWED_MODULE_PREFIXES,
  ROUND_ALLOWED_MODULE_FILES,
  ROUND_FROZEN_MODULE_PATHS,
} from "./lib/scope-policy.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function sameStringArray(actual, expected) {
  return Array.isArray(actual) &&
    actual.length === expected.length &&
    actual.every((value, index) => value === expected[index]);
}

try {
  if (process.argv.length !== 2) {
    throw new ParentScopeError("ROUND_SCOPE_ARGUMENT_ERROR");
  }

  const policy = JSON.parse(
    readFileSync(path.join(moduleRoot, "module-boundary.json"), "utf8"),
  );
  if (
    policy.schemaVersion !== 18 ||
    policy.activeRound !== ACTIVE_ROUND ||
    policy.activeRoundBaselineSha !== ACTIVE_ROUND_BASELINE_SHA ||
    !sameStringArray(
      policy.activeRoundChangePolicy?.allowedModuleFiles,
      ROUND_ALLOWED_MODULE_FILES,
    ) ||
    !sameStringArray(
      policy.activeRoundChangePolicy?.allowedModulePrefixes,
      ROUND_ALLOWED_MODULE_PREFIXES,
    ) ||
    !sameStringArray(
      policy.activeRoundChangePolicy?.frozenModulePaths,
      ROUND_FROZEN_MODULE_PATHS,
    )
  ) {
    throw new ParentScopeError("ROUND_SCOPE_POLICY_INVALID");
  }

  const result = checkRoundScope({
    moduleRoot,
    base: policy.activeRoundBaselineSha,
    expectedBase: ACTIVE_ROUND_BASELINE_SHA,
  });
  if (result.status === "not_applicable") {
    console.log("ROUND_SCOPE_NOT_APPLICABLE mode=standalone");
  } else {
    console.log(
      `ROUND_SCOPE_OK checked=${result.checkedEntries} changed=${result.uniqueChangedPaths}`,
    );
  }
} catch (error) {
  const code = error instanceof ParentScopeError
    ? error.code
    : "ROUND_SCOPE_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = 1;
}
