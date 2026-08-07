import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  ParentScopeError,
  checkParentScope,
  parseParentScopeArgs,
} from "./lib/parent-scope-core.mjs";

const moduleRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

try {
  const { base } = parseParentScopeArgs(process.argv.slice(2));
  const policy = JSON.parse(
    readFileSync(path.join(moduleRoot, "module-boundary.json"), "utf8"),
  );
  const result = checkParentScope({
    moduleRoot,
    base,
    expectedBase: policy.r0BaselineSha,
  });
  console.log(
    `PARENT_SCOPE_OK checked=${result.checkedEntries} changed=${result.uniqueChangedPaths}`,
  );
} catch (error) {
  const code = error instanceof ParentScopeError ? error.code : "PARENT_SCOPE_INTERNAL_ERROR";
  console.error(code);
  process.exitCode = 1;
}
