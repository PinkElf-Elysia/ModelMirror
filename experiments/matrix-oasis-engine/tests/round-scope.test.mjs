import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  mkdtempSync,
  mkdirSync,
  readFileSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  ParentScopeError,
  checkRoundScope,
  classifyRoundPath,
} from "../scripts/lib/parent-scope-core.mjs";
import {
  ROUND_ALLOWED_MODULE_PREFIXES,
  ROUND_ALLOWED_MODULE_ROOT_FILES,
  ROUND_FROZEN_MODULE_PATHS,
} from "../scripts/lib/scope-policy.mjs";

const TEMP_PREFIX = "matrix-oasis-round-scope-";
const MODULE_PREFIX = "experiments/matrix-oasis-engine";
const committedModuleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

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
  write(fixture, `${MODULE_PREFIX}/packages/game-pack-contracts/src/index.mjs`);
  write(fixture, `${MODULE_PREFIX}/packages/game-pack-validator/src/index.mjs`);
  write(fixture, `${MODULE_PREFIX}/examples/neutral.json`);
  write(fixture, `${MODULE_PREFIX}/docs/AUTHORING_GAME_PACK.md`);
  write(fixture, `${MODULE_PREFIX}/docs/adr/0001-isolated-experiment-module.md`);
  write(fixture, `${MODULE_PREFIX}/docs/adr/0002-r1-active-round-governance.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R0_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R1_ACCEPTANCE.md`);
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

test("machine boundary and code expose the same ordered R2 allowlist", () => {
  const policy = JSON.parse(
    readFileSync(path.join(committedModuleRoot, "module-boundary.json"), "utf8"),
  );

  assert.deepEqual(
    policy.activeRoundChangePolicy.allowedModuleRootFiles,
    ROUND_ALLOWED_MODULE_ROOT_FILES,
  );
  assert.deepEqual(
    policy.activeRoundChangePolicy.allowedModulePrefixes,
    ROUND_ALLOWED_MODULE_PREFIXES,
  );
  assert.deepEqual(
    policy.activeRoundChangePolicy.frozenModulePaths,
    ROUND_FROZEN_MODULE_PATHS,
  );
});

test("accepts allowlisted R2 changes in every Git status source", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/apps/creator-web/index.html`, "R2 lab\n");
  git(fixture, ["add", "."]);
  git(fixture, ["commit", "--quiet", "-m", "round change"]);
  write(fixture, `${MODULE_PREFIX}/packages/game-pack-simulator/src/index.mjs`);
  git(fixture, ["add", `${MODULE_PREFIX}/packages/game-pack-simulator/src/index.mjs`]);
  write(fixture, `${MODULE_PREFIX}/scripts/r2.mjs`, "unstaged\n");
  git(fixture, ["add", `${MODULE_PREFIX}/scripts/r2.mjs`]);
  write(fixture, `${MODULE_PREFIX}/scripts/r2.mjs`, "unstaged update\n");
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R2_ACCEPTANCE.md`);

  const result = checkRoundScope({ moduleRoot, base, expectedBase: base });
  assert.equal(result.status, "ok");
  assert.equal(result.mode, "parent");
  assert.equal(result.uniqueChangedPaths, 4);
});

test("rejects a committed R1 contracts change", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/packages/game-pack-contracts/src/index.mjs`, "changed\n");
  git(fixture, ["add", "."]);
  git(fixture, ["commit", "--quiet", "-m", "contract change"]);

  expectCode(
    () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
    "ROUND_GUARD_R1_ARTIFACT_CHANGED",
  );
});

test("rejects a staged R1 validator change", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/packages/game-pack-validator/src/index.mjs`, "changed\n");
  git(fixture, ["add", `${MODULE_PREFIX}/packages/game-pack-validator/src/index.mjs`]);

  expectCode(
    () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
    "ROUND_GUARD_R1_ARTIFACT_CHANGED",
  );
});

test("rejects an unstaged R1 example change", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/examples/neutral.json`, "changed\n");

  expectCode(
    () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
    "ROUND_GUARD_R1_ARTIFACT_CHANGED",
  );
});

test("rejects an untracked file under frozen R1 examples", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/examples/new-story.json`);

  expectCode(
    () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
    "ROUND_GUARD_R1_ARTIFACT_CHANGED",
  );
});

for (const acceptance of ["R0_ACCEPTANCE.md", "R1_ACCEPTANCE.md"]) {
  test(`rejects byte changes to historical ${acceptance}`, (t) => {
    const { fixture, moduleRoot, base } = makeParentFixture(t);
    write(fixture, `${MODULE_PREFIX}/docs/rounds/${acceptance}`, "changed\n");

    expectCode(
      () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
      "ROUND_GUARD_R1_ARTIFACT_CHANGED",
    );
  });
}

for (const historicalPath of [
  "docs/AUTHORING_GAME_PACK.md",
  "docs/adr/0001-isolated-experiment-module.md",
  "docs/adr/0002-r1-active-round-governance.md",
]) {
  test(`rejects byte changes to frozen ${historicalPath}`, (t) => {
    const { fixture, moduleRoot, base } = makeParentFixture(t);
    write(fixture, `${MODULE_PREFIX}/${historicalPath}`, "changed\n");

    expectCode(
      () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
      "ROUND_GUARD_R1_ARTIFACT_CHANGED",
    );
  });
}

test("rejects a module path omitted from the positive allowlist", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/unexpected-root.txt`);

  expectCode(
    () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
    "ROUND_SCOPE_PATH_NOT_ALLOWLISTED",
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
  assert.equal(classifyRoundPath(`${MODULE_PREFIX}/apps/creator-web/src/App.tsx`), null);
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/game-pack-simulator/src/index.mjs`),
    null,
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/game-pack-contracts/src/index.mjs`),
    "ROUND_GUARD_R1_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/docs/rounds/R1_ACCEPTANCE.md`),
    "ROUND_GUARD_R1_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/docs/AUTHORING_GAME_PACK.md`),
    "ROUND_GUARD_R1_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/unexpected-root.txt`),
    "ROUND_SCOPE_PATH_NOT_ALLOWLISTED",
  );
  assert.equal(
    classifyRoundPath("client/src/pages/MatrixOasisPage.tsx"),
    "ROUND_SCOPE_PATH_OUTSIDE_MODULE",
  );
});
