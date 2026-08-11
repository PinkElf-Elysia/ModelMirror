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
  ACTIVE_ROUND,
  ACTIVE_ROUND_BASELINE_SHA,
  ROUND_ALLOWED_MODULE_PREFIXES,
  ROUND_ALLOWED_MODULE_FILES,
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
  write(fixture, `${MODULE_PREFIX}/packages/game-pack-simulator/src/index.mjs`);
  write(fixture, `${MODULE_PREFIX}/packages/game-pack-validator/src/index.mjs`);
  write(fixture, `${MODULE_PREFIX}/examples/neutral.json`);
  write(fixture, `${MODULE_PREFIX}/docs/AUTHORING_GAME_PACK.md`);
  write(fixture, `${MODULE_PREFIX}/docs/adr/0001-isolated-experiment-module.md`);
  write(fixture, `${MODULE_PREFIX}/docs/adr/0002-r1-active-round-governance.md`);
  write(fixture, `${MODULE_PREFIX}/docs/adr/0003-r2-reference-simulator-governance.md`);
  write(fixture, `${MODULE_PREFIX}/docs/adr/0004-r3-runtime-pack-governance.md`);
  write(fixture, `${MODULE_PREFIX}/docs/adr/0005-r4-godot-foundation-governance.md`);
  write(fixture, `${MODULE_PREFIX}/docs/adr/0006-r5-godot-runtime-adapter-governance.md`);
  write(fixture, `${MODULE_PREFIX}/docs/adr/0007-r6-playable-3d-governance.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R0_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R1_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R2_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R3_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R4_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R5_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R6_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/RUNTIME_GAME_PACK.md`);
  write(fixture, `${MODULE_PREFIX}/docs/RUNTIME_PACK_THREAT_MODEL.md`);
  write(fixture, `${MODULE_PREFIX}/docs/GODOT_FOUNDATION.md`);
  write(fixture, `${MODULE_PREFIX}/docs/GODOT_THREAT_MODEL.md`);
  write(fixture, `${MODULE_PREFIX}/docs/MCP_QUALIFICATION.md`);
  write(fixture, `${MODULE_PREFIX}/apps/runtime-godot/project.godot`);
  write(fixture, `${MODULE_PREFIX}/apps/runtime-godot/scenes/bootstrap.tscn`);
  write(fixture, `${MODULE_PREFIX}/apps/runtime-godot/scripts/bootstrap.gd`);
  write(fixture, `${MODULE_PREFIX}/apps/runtime-godot/scripts/bootstrap.gd.uid`);
  write(fixture, `${MODULE_PREFIX}/apps/runtime-godot/test/test_foundation.gd`);
  write(fixture, `${MODULE_PREFIX}/apps/runtime-godot/test/test_foundation.gd.uid`);
  write(fixture, `${MODULE_PREFIX}/apps/runtime-godot/addons/gdUnit4/plugin.cfg`);
  write(fixture, `${MODULE_PREFIX}/apps/runtime-godot/runtime/runtime_session.gd`);
  write(fixture, `${MODULE_PREFIX}/apps/runtime-godot/test/r5/test_runtime_session.gd`);
  write(fixture, `${MODULE_PREFIX}/apps/runtime-godot/playable/playable_lab.gd`);
  write(fixture, `${MODULE_PREFIX}/apps/runtime-godot/test/r6/test_playable_lab.gd`);
  write(fixture, `${MODULE_PREFIX}/third-party/gdunit4.lock.json`);
  write(fixture, `${MODULE_PREFIX}/third-party/gdunit4/LICENSE`);
  write(fixture, `${MODULE_PREFIX}/third-party/godot-demo-projects/reference.lock.json`);
  write(fixture, `${MODULE_PREFIX}/scripts/validate-pack.mjs`);
  write(fixture, `${MODULE_PREFIX}/tests/game-pack-simulator-semantics.test.mjs`);
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

test("machine boundary and code expose the same ordered R8 policy", () => {
  const policy = JSON.parse(
    readFileSync(path.join(committedModuleRoot, "module-boundary.json"), "utf8"),
  );

  assert.equal(policy.schemaVersion, 8);
  assert.equal(policy.activeRound, ACTIVE_ROUND);
  assert.equal(policy.activeRoundBaselineSha, ACTIVE_ROUND_BASELINE_SHA);
  assert.deepEqual(
    policy.activeRoundChangePolicy.allowedModuleFiles,
    ROUND_ALLOWED_MODULE_FILES,
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

test("accepts exact R8 files and prototype package prefixes in every Git status source", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/packages/prototype-generation-contracts/src/index.mjs`, "export {};\n");
  git(fixture, ["add", "."]);
  git(fixture, ["commit", "--quiet", "-m", "round change"]);
  write(fixture, `${MODULE_PREFIX}/packages/prototype-generator/src/index.mjs`, "export {};\n");
  git(fixture, ["add", `${MODULE_PREFIX}/packages/prototype-generator/src/index.mjs`]);
  write(fixture, `${MODULE_PREFIX}/scripts/run-verify.mjs`, "staged\n");
  git(fixture, ["add", `${MODULE_PREFIX}/scripts/run-verify.mjs`]);
  write(fixture, `${MODULE_PREFIX}/scripts/run-verify.mjs`, "unstaged update\n");
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R8_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/scripts/generate-prototype.mjs`, "export {};\n");

  const result = checkRoundScope({ moduleRoot, base, expectedBase: base });
  assert.equal(result.status, "ok");
  assert.equal(result.mode, "parent");
  assert.equal(result.uniqueChangedPaths, 5);
});

test("rejects a committed R1 contracts change", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/packages/game-pack-contracts/src/index.mjs`, "changed\n");
  git(fixture, ["add", "."]);
  git(fixture, ["commit", "--quiet", "-m", "contract change"]);

  expectCode(
    () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
});

test("rejects a staged R1 validator change", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/packages/game-pack-validator/src/index.mjs`, "changed\n");
  git(fixture, ["add", `${MODULE_PREFIX}/packages/game-pack-validator/src/index.mjs`]);

  expectCode(
    () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
});

test("rejects an unstaged frozen R1 example change", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/examples/mechanics-conformance.authoring-game-pack.json`, "changed\n");

  expectCode(
    () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
});

test("rejects an untracked unapproved file under examples", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/examples/new-story.json`);

  expectCode(
    () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
});

for (const acceptance of [
  "R0_ACCEPTANCE.md",
  "R1_ACCEPTANCE.md",
  "R2_ACCEPTANCE.md",
  "R3_ACCEPTANCE.md",
  "R4_ACCEPTANCE.md",
  "R5_ACCEPTANCE.md",
  "R6_ACCEPTANCE.md",
]) {
  test(`rejects byte changes to historical ${acceptance}`, (t) => {
    const { fixture, moduleRoot, base } = makeParentFixture(t);
    write(fixture, `${MODULE_PREFIX}/docs/rounds/${acceptance}`, "changed\n");

    expectCode(
      () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
      "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
    );
  });
}

for (const historicalPath of [
  "docs/AUTHORING_GAME_PACK.md",
  "docs/adr/0001-isolated-experiment-module.md",
  "docs/adr/0002-r1-active-round-governance.md",
  "docs/adr/0003-r2-reference-simulator-governance.md",
  "docs/adr/0004-r3-runtime-pack-governance.md",
  "docs/adr/0005-r4-godot-foundation-governance.md",
  "docs/adr/0006-r5-godot-runtime-adapter-governance.md",
  "docs/adr/0007-r6-playable-3d-governance.md",
  "docs/RUNTIME_GAME_PACK.md",
  "docs/RUNTIME_PACK_THREAT_MODEL.md",
  "docs/GODOT_FOUNDATION.md",
  "docs/GODOT_THREAT_MODEL.md",
  "docs/MCP_QUALIFICATION.md",
  "docs/GODOT_RUNTIME_ADAPTER.md",
  "docs/GODOT_RUNTIME_THREAT_MODEL.md",
  "docs/GODOT_PLAYABLE_3D.md",
  "docs/GODOT_PLAYABLE_3D_THREAT_MODEL.md",
]) {
  test(`rejects byte changes to frozen ${historicalPath}`, (t) => {
    const { fixture, moduleRoot, base } = makeParentFixture(t);
    write(fixture, `${MODULE_PREFIX}/${historicalPath}`, "changed\n");

    expectCode(
      () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
      "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
    );
  });
}

for (const frozenPath of [
  "apps/creator-web/src/App.tsx",
  "packages/game-pack-simulator/src/index.mjs",
  "packages/runtime-pack-simulator/src/index.mjs",
  "scripts/validate-pack.mjs",
  "tests/game-pack-simulator-semantics.test.mjs",
  "apps/runtime-godot/runtime/runtime_session.gd",
  "apps/runtime-godot/test/r5/test_runtime_session.gd",
  "apps/runtime-godot/playable/playable_lab.gd",
  "apps/runtime-godot/test/r6/test_playable_lab.gd",
  "apps/runtime-godot/scenes/bootstrap.tscn",
  "apps/runtime-godot/scripts/bootstrap.gd",
  "apps/runtime-godot/test/test_foundation.gd",
  "apps/runtime-godot/addons/gdUnit4/plugin.cfg",
  "third-party/gdunit4.lock.json",
  "third-party/gdunit4/LICENSE",
]) {
  test(`rejects byte changes to frozen R1-R3 implementation ${frozenPath}`, (t) => {
    const { fixture, moduleRoot, base } = makeParentFixture(t);
    write(fixture, `${MODULE_PREFIX}/${frozenPath}`, "changed\n");

    expectCode(
      () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
      "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
    );
  });
}

for (const unknownPath of [
  "docs/unplanned.md",
  "scripts/unplanned.mjs",
  "tests/unplanned.test.mjs",
]) {
test(`rejects unlisted path inside frozen root ${unknownPath}`, (t) => {
    const { fixture, moduleRoot, base } = makeParentFixture(t);
    write(fixture, `${MODULE_PREFIX}/${unknownPath}`);

    expectCode(
      () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
      "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
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

test("round path classifier exposes stable R8 guard categories", () => {
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/prototype-generation-contracts/src/index.mjs`),
    null,
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/prototype-generator/src/index.mjs`),
    null,
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/scripts/generate-prototype.mjs`),
    null,
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/apps/runtime-godot/playable/first_person_controller.gd`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/apps/runtime-godot/project.godot`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/apps/runtime-godot/runtime/runtime_session.gd`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/apps/runtime-godot/addons/gdUnit4/plugin.cfg`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/apps/creator-web/src/App.tsx`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/game-pack-compiler/src/index.mjs`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/scene-pack-validator/src/index.mjs`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/docs/rounds/R1_ACCEPTANCE.md`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/docs/AUTHORING_GAME_PACK.md`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/docs/unplanned.md`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
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
