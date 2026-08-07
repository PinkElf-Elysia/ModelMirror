import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  ParentScopeError,
  checkParentScope,
  classifyParentPath,
  parseParentScopeArgs,
} from "../scripts/lib/parent-scope-core.mjs";

const TEMP_PREFIX = "matrix-oasis-parent-scope-";
const MODULE_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function git(cwd, args) {
  return execFileSync("git", args, {
    cwd,
    encoding: "utf8",
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  }).trim();
}

function write(root, relative, contents = "fixture\n") {
  const target = path.join(root, ...relative.split("/"));
  mkdirSync(path.dirname(target), { recursive: true });
  writeFileSync(target, contents, "utf8");
}

function makeFixture(t) {
  const fixture = mkdtempSync(path.join(os.tmpdir(), TEMP_PREFIX));
  const moduleRoot = path.join(fixture, "experiments", "matrix-oasis-engine");

  git(fixture, ["init", "--quiet"]);
  git(fixture, ["config", "user.name", "Matrix Oasis Test"]);
  git(fixture, ["config", "user.email", "matrix-oasis-test@example.invalid"]);
  write(fixture, "client/src/pages/MatrixOasisPage.tsx", "export default null;\n");
  write(fixture, "server/main.py", "# fixture\n");
  write(fixture, "experiments/matrix-oasis-engine/package.json", "{}\n");
  git(fixture, ["add", "."]);
  git(fixture, ["commit", "--quiet", "-m", "fixture base"]);
  const base = git(fixture, ["rev-parse", "HEAD"]);

  t.after(() => {
    const resolvedFixture = realpathSync(fixture);
    const resolvedTemp = realpathSync(os.tmpdir());
    assert.equal(path.dirname(resolvedFixture), resolvedTemp);
    assert.match(path.basename(resolvedFixture), new RegExp(`^${TEMP_PREFIX}`));
    rmSync(resolvedFixture, { recursive: true, force: true });
  });

  return { fixture, moduleRoot, base };
}

function expectCode(fn, expected) {
  assert.throws(fn, (error) => {
    assert.ok(error instanceof ParentScopeError);
    assert.equal(error.code, expected);
    return true;
  });
}

test("requires an explicit 40-character base SHA", () => {
  expectCode(() => parseParentScopeArgs([]), "PARENT_SCOPE_BASE_REQUIRED");
  expectCode(
    () => parseParentScopeArgs(["--base", "not-a-commit"]),
    "PARENT_SCOPE_BASE_INVALID",
  );
  expectCode(
    () => parseParentScopeArgs(["--base", "a".repeat(40), "extra"]),
    "PARENT_SCOPE_ARGUMENT_ERROR",
  );
});

test("CLI fails with a stable code and does not echo invalid input", () => {
  const sensitiveLookingArgument = `not-a-commit-${"secret"}`;
  const result = spawnSync(
    process.execPath,
    ["scripts/check-parent-scope.mjs", "--base", sensitiveLookingArgument],
    { cwd: MODULE_ROOT, encoding: "utf8", windowsHide: true },
  );

  assert.equal(result.status, 1);
  assert.equal(result.stdout, "");
  assert.equal(result.stderr.trim(), "PARENT_SCOPE_BASE_INVALID");
  assert.doesNotMatch(result.stderr, new RegExp(sensitiveLookingArgument));
});

test("accepts committed and untracked changes confined to the module", (t) => {
  const { fixture, moduleRoot, base } = makeFixture(t);
  write(fixture, "experiments/matrix-oasis-engine/src/inside.ts");
  git(fixture, ["add", "."]);
  git(fixture, ["commit", "--quiet", "-m", "module change"]);
  write(fixture, "experiments/matrix-oasis-engine/tmp/untracked file.txt");

  const result = checkParentScope({ moduleRoot, base, expectedBase: base });
  assert.equal(result.status, "ok");
  assert.equal(result.uniqueChangedPaths, 2);
});

test("rejects a committed parent client change", (t) => {
  const { fixture, moduleRoot, base } = makeFixture(t);
  write(fixture, "client/src/parent-change.ts");
  git(fixture, ["add", "."]);
  git(fixture, ["commit", "--quiet", "-m", "parent client change"]);

  expectCode(
    () => checkParentScope({ moduleRoot, base, expectedBase: base }),
    "PARENT_GUARD_CLIENT_CHANGED",
  );
});

test("rejects an untracked parent file", (t) => {
  const { fixture, moduleRoot, base } = makeFixture(t);
  write(fixture, "outside-parent.txt");

  expectCode(
    () => checkParentScope({ moduleRoot, base, expectedBase: base }),
    "PARENT_SCOPE_PATH_OUTSIDE_MODULE",
  );
});

test("rejects a well-formed SHA that is not a commit in the repository", (t) => {
  const { moduleRoot } = makeFixture(t);
  expectCode(
    () => checkParentScope({
      moduleRoot,
      base: "0".repeat(40),
      expectedBase: "0".repeat(40),
    }),
    "PARENT_SCOPE_BASE_NOT_FOUND",
  );
});

test("rejects standalone extraction use explicitly", (t) => {
  const { fixture, base } = makeFixture(t);
  expectCode(
    () => checkParentScope({ moduleRoot: fixture, base, expectedBase: base }),
    "PARENT_SCOPE_STANDALONE_UNSUPPORTED",
  );
});

test("rejects a caller-selected base that differs from the fixed active-round baseline", (t) => {
  const { moduleRoot, base } = makeFixture(t);
  expectCode(
    () => checkParentScope({
      moduleRoot,
      base,
      expectedBase: "f".repeat(40),
    }),
    "PARENT_SCOPE_BASE_MISMATCH",
  );
});

test("provides stable explicit parent guard categories", () => {
  assert.equal(
    classifyParentPath("client/src/pages/MatrixOasisPage.tsx"),
    "PARENT_GUARD_MATRIX_OASIS_CHANGED",
  );
  assert.equal(classifyParentPath("client/Dockerfile"), "PARENT_GUARD_DOCKER_CHANGED");
  assert.equal(classifyParentPath(".github/workflows/ci.yml"), "PARENT_GUARD_GITHUB_CHANGED");
  assert.equal(classifyParentPath("server/main.py"), "PARENT_GUARD_SERVER_CHANGED");
  assert.equal(classifyParentPath("package-lock.json"), "PARENT_GUARD_ROOT_CONFIG_CHANGED");
  assert.equal(classifyParentPath("notes/review.md"), "PARENT_SCOPE_PATH_OUTSIDE_MODULE");
  assert.equal(classifyParentPath("experiments/matrix-oasis-engine/README.md"), null);
});
