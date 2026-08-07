import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  ParentScopeError,
  checkRoundScope,
  classifyRoundPath,
} from "../scripts/lib/parent-scope-core.mjs";

const TEMP_PREFIX = "matrix-oasis-round-scope-";
const MODULE_PREFIX = "experiments/matrix-oasis-engine";

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

function initializeGit(root) {
  git(root, ["init", "--quiet"]);
  git(root, ["config", "user.name", "Matrix Oasis Test"]);
  git(root, ["config", "user.email", "matrix-oasis-test@example.invalid"]);
  git(root, ["add", "."]);
  git(root, ["commit", "--quiet", "-m", "fixture base"]);
  return git(root, ["rev-parse", "HEAD"]);
}

function registerCleanup(t, fixture) {
  t.after(() => {
    const resolvedFixture = realpathSync(fixture);
    const resolvedTemp = realpathSync(os.tmpdir());
    assert.equal(path.dirname(resolvedFixture), resolvedTemp);
    assert.match(path.basename(resolvedFixture), new RegExp(`^${TEMP_PREFIX}`));
    rmSync(resolvedFixture, { recursive: true, force: true });
  });
}

function makeParentFixture(t) {
  const fixture = mkdtempSync(path.join(os.tmpdir(), TEMP_PREFIX));
  const moduleRoot = path.join(fixture, ...MODULE_PREFIX.split("/"));
  write(fixture, `${MODULE_PREFIX}/package.json`, "{}\n");
  write(fixture, `${MODULE_PREFIX}/apps/creator-web/index.html`, "fixture\n");
  write(fixture, "client/fixture.txt", "parent fixture\n");
  const base = initializeGit(fixture);
  registerCleanup(t, fixture);
  return { fixture, moduleRoot, base };
}

function expectCode(fn, expected) {
  assert.throws(fn, (error) => {
    assert.ok(error instanceof ParentScopeError);
    assert.equal(error.code, expected);
    return true;
  });
}

test("accepts active-round changes outside the frozen Creator", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/docs/R1.md`);
  git(fixture, ["add", "."]);
  git(fixture, ["commit", "--quiet", "-m", "round change"]);
  write(fixture, `${MODULE_PREFIX}/tests/untracked.mjs`);

  const result = checkRoundScope({ moduleRoot, base, expectedBase: base });
  assert.equal(result.status, "ok");
  assert.equal(result.mode, "parent");
  assert.equal(result.uniqueChangedPaths, 2);
});

test("rejects a committed Creator change", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/apps/creator-web/index.html`, "changed\n");
  git(fixture, ["add", "."]);
  git(fixture, ["commit", "--quiet", "-m", "creator change"]);

  expectCode(
    () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
    "ROUND_GUARD_CREATOR_CHANGED",
  );
});

test("rejects an untracked Creator file", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/apps/creator-web/src/untracked.ts`);

  expectCode(
    () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
    "ROUND_GUARD_CREATOR_CHANGED",
  );
});

test("rejects any parent-repository change", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, "server/untracked.py");

  expectCode(
    () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
    "ROUND_SCOPE_PATH_OUTSIDE_MODULE",
  );
});

test("standalone extraction is explicitly not applicable", (t) => {
  const fixture = mkdtempSync(path.join(os.tmpdir(), TEMP_PREFIX));
  write(fixture, "package.json", "{}\n");
  const base = initializeGit(fixture);
  registerCleanup(t, fixture);

  const result = checkRoundScope({
    moduleRoot: fixture,
    base,
    expectedBase: base,
  });
  assert.deepEqual(result, {
    status: "not_applicable",
    mode: "standalone",
    checkedEntries: 0,
    uniqueChangedPaths: 0,
  });
});

test("rejects a caller-selected base", (t) => {
  const { moduleRoot, base } = makeParentFixture(t);
  expectCode(
    () => checkRoundScope({
      moduleRoot,
      base,
      expectedBase: "f".repeat(40),
    }),
    "ROUND_SCOPE_BASE_MISMATCH",
  );
});

test("round path classifier exposes stable guard categories", () => {
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/apps/creator-web/src/App.tsx`),
    "ROUND_GUARD_CREATOR_CHANGED",
  );
  assert.equal(classifyRoundPath(`${MODULE_PREFIX}/docs/R1.md`), null);
  assert.equal(
    classifyRoundPath("client/src/pages/MatrixOasisPage.tsx"),
    "ROUND_SCOPE_PATH_OUTSIDE_MODULE",
  );
});
