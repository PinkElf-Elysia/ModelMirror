import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
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
  write(fixture, `${MODULE_PREFIX}/docs/adr/0008-r7-scene-pack-governance.md`);
  write(fixture, `${MODULE_PREFIX}/docs/adr/0009-r8-natural-language-prototype-governance.md`);
  write(fixture, `${MODULE_PREFIX}/docs/adr/0010-r9-asset-materialization-governance.md`);
  write(fixture, `${MODULE_PREFIX}/docs/adr/0011-r10-prototype-builder-governance.md`);
  write(fixture, `${MODULE_PREFIX}/docs/adr/0012-r11-spatial-environment-governance.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R0_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R1_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R2_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R3_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R4_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R5_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R6_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R7_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R8_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R9_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R10_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R11_ACCEPTANCE.md`);
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
  write(fixture, `${MODULE_PREFIX}/tests/prototype-generation-cli.test.mjs`);
  write(fixture, `${MODULE_PREFIX}/packages/prototype-generator/src/openai-compatible.mjs`);
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

test("machine boundary and code expose the same ordered R22 policy", () => {
  const policy = JSON.parse(
    readFileSync(path.join(committedModuleRoot, "module-boundary.json"), "utf8"),
  );

  assert.equal(policy.schemaVersion, 22);
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
  const cli = readFileSync(
    path.join(committedModuleRoot, "scripts", "check-round-scope.mjs"),
    "utf8",
  );
  assert.match(cli, /policy\.schemaVersion !== 22/);
  assert.doesNotMatch(cli, /policy\.schemaVersion !== 21/);
});

const R21_HISTORICAL_STEPS = Object.freeze([
  "verify:r21-references",
  "verify:npc-derived-state-contracts",
  "verify:npc-derived-state",
  "check:round-scope",
  "check:boundary",
  "check:v2-claim",
]);
const R21_HISTORICAL_DOCUMENTS = Object.freeze([
  "docs/R21_TASK_CARD.md",
  "docs/R21_MINIMUM_SEMANTICS.md",
  "docs/R21_DERIVED_STATE_THREAT_MODEL.md",
  "docs/adr/0022-r21-derived-state-governance.md",
  "docs/R21_DERIVED_STATE.md",
  "docs/rounds/R21_FALSIFICATION_EVIDENCE.md",
  "docs/rounds/R21_ACCEPTANCE.md",
]);

function runHistoricalR21Verifier(t, {
  mutateBoundary = () => {},
  mutateClaim = () => {},
  invalidDocument = null,
  failingStep = null,
} = {}) {
  const fixture = mkdtempSync(path.join(os.tmpdir(), TEMP_PREFIX));
  registerCleanup(t, fixture);
  for (const relative of [
    "scripts/verify-r21.mjs",
    "scripts/lib/v2-claim-core.mjs",
    "package.json",
    ...R21_HISTORICAL_DOCUMENTS,
  ]) {
    write(fixture, relative, readFileSync(path.join(committedModuleRoot, relative), "utf8"));
  }
  const boundary = JSON.parse(readFileSync(path.join(committedModuleRoot, "module-boundary.json"), "utf8"));
  const claim = JSON.parse(readFileSync(path.join(committedModuleRoot, "docs/V2_STATUS.json"), "utf8"));
  mutateBoundary(boundary);
  mutateClaim(claim);
  write(fixture, "module-boundary.json", JSON.stringify(boundary));
  write(fixture, "docs/V2_STATUS.json", JSON.stringify(claim));
  if (invalidDocument !== null) write(fixture, invalidDocument, "invalid historical document\n");
  // Only the subprocess boundary is stubbed here; the actual verifier, current
  // claim checker and historical document checks execute from an owned copy.
  write(fixture, "fixture-npm.mjs", [
    'if (process.argv.length !== 4 || process.argv[2] !== "run") process.exit(91);',
    'console.log("R21_TEST_STEP:" + process.argv[3]);',
    `if (process.argv[3] === ${JSON.stringify(failingStep)}) process.exit(7);`,
  ].join("\n"));
  const result = spawnSync(process.execPath, [path.join(fixture, "scripts/verify-r21.mjs")], {
    cwd: fixture,
    encoding: "utf8",
    env: { SystemRoot: process.env.SystemRoot, npm_execpath: path.join(fixture, "fixture-npm.mjs") },
    shell: false,
    windowsHide: true,
    timeout: 10_000,
    maxBuffer: 1_048_576,
  });
  assert.equal(result.error, undefined);
  const steps = [...result.stdout.matchAll(/^R21_TEST_STEP:(.+)$/gmu)].map((match) => match[1].trim());
  return { ...result, steps };
}

test("historical R21 verifier accepts active R22 governance and keeps all six steps", (t) => {
  const result = runHistoricalR21Verifier(t);
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.steps, R21_HISTORICAL_STEPS);
  assert.match(result.stdout, /^R21_AUTOMATED_GATES_OK$/mu);
});

test("historical R21 verifier rejects every changed, missing or unknown frozen policy field", (t) => {
  const policy = JSON.parse(readFileSync(path.join(committedModuleRoot, "module-boundary.json"), "utf8")).r21DerivedStatePolicy;
  const mutations = Object.entries(policy).map(([key, value]) => [key, (boundary) => {
    boundary.r21DerivedStatePolicy[key] = typeof value === "boolean" ? !value : `${value}-drift`;
  }]);
  mutations.push(
    ["missing-field", (boundary) => { delete boundary.r21DerivedStatePolicy.qualificationProfile; }],
    ["unknown-field", (boundary) => { boundary.r21DerivedStatePolicy.unapprovedCapability = false; }],
    ["missing-policy", (boundary) => { delete boundary.r21DerivedStatePolicy; }],
  );
  for (const [name, mutateBoundary] of mutations) {
    const result = runHistoricalR21Verifier(t, { mutateBoundary });
    assert.equal(result.status, 1, name);
    assert.deepEqual(result.steps, [], name);
    assert.match(result.stderr, /^R21_AUTOMATED_GATES_INVALID$/mu, name);
  }
});

test("historical R21 verifier still rejects a changed current V2 claim", (t) => {
  const result = runHistoricalR21Verifier(t, { mutateClaim: (claim) => { claim.claimAllowed = true; } });
  assert.equal(result.status, 1);
  assert.deepEqual(result.steps, []);
});

test("historical R21 verifier retains every frozen document check", (t) => {
  for (const invalidDocument of R21_HISTORICAL_DOCUMENTS) {
    const result = runHistoricalR21Verifier(t, { invalidDocument });
    assert.equal(result.status, 1, invalidDocument);
    assert.deepEqual(result.steps, [], invalidDocument);
    assert.match(result.stderr, /^R21_AUTOMATED_GATES_INVALID$/mu, invalidDocument);
  }
});

test("historical R21 verifier fails closed at each original subprocess step", (t) => {
  for (const [index, failingStep] of R21_HISTORICAL_STEPS.entries()) {
    const result = runHistoricalR21Verifier(t, { failingStep });
    assert.equal(result.status, 1, failingStep);
    assert.deepEqual(result.steps, R21_HISTORICAL_STEPS.slice(0, index + 1), failingStep);
    assert.doesNotMatch(result.stdout, /^R21_AUTOMATED_GATES_OK$/mu);
  }
});

test("only the approved historical R21 verifier is unfrozen, not its implementation or evidence", () => {
  assert.equal(classifyRoundPath(`${MODULE_PREFIX}/scripts/verify-r21.mjs`), null);
  for (const relative of [
    "scripts/lib/r21-cli-core.mjs",
    "packages/npc-derived-state-runtime/src/index.mjs",
    "packages/npc-derived-state-contracts/src/index.mjs",
    ...R21_HISTORICAL_DOCUMENTS,
  ]) {
    assert.equal(classifyRoundPath(`${MODULE_PREFIX}/${relative}`), "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED", relative);
  }
});

test("accepts exact R22 files and cognition prefixes in every Git status source", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/packages/npc-cognition-contracts/src/index.mjs`, "export {};\n");
  git(fixture, ["add", "."]);
  git(fixture, ["commit", "--quiet", "-m", "round change"]);
  write(fixture, `${MODULE_PREFIX}/packages/npc-cognition-runtime/src/index.mjs`, "export {};\n");
  git(fixture, ["add", `${MODULE_PREFIX}/packages/npc-cognition-runtime/src/index.mjs`]);
  write(fixture, `${MODULE_PREFIX}/scripts/run-verify.mjs`, "staged\n");
  git(fixture, ["add", `${MODULE_PREFIX}/scripts/run-verify.mjs`]);
  write(fixture, `${MODULE_PREFIX}/scripts/run-verify.mjs`, "unstaged update\n");
  write(fixture, `${MODULE_PREFIX}/docs/V2_STATUS.json`);
  write(fixture, `${MODULE_PREFIX}/tests/r22-falsification.test.mjs`);
  write(fixture, `${MODULE_PREFIX}/scripts/qualify-r22.mjs`, "approved R22 CLI\n");
  write(fixture, `${MODULE_PREFIX}/docs/rounds/R22_ACCEPTANCE.md`);
  write(fixture, `${MODULE_PREFIX}/third-party/npc-cognition-references/reference.lock.json`, "{}\n");

  const result = checkRoundScope({ moduleRoot, base, expectedBase: base });
  assert.equal(result.status, "ok");
  assert.equal(result.mode, "parent");
  assert.equal(result.uniqueChangedPaths, 8);
});

test("keeps prototype generation tests frozen in R22", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(fixture, `${MODULE_PREFIX}/tests/prototype-generation-orchestrator.test.mjs`);

  expectCode(
    () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
});

test("keeps the prototype provider production implementation frozen", (t) => {
  const { fixture, moduleRoot, base } = makeParentFixture(t);
  write(
    fixture,
    `${MODULE_PREFIX}/packages/prototype-generator/src/openai-compatible.mjs`,
    "changed\n",
  );

  expectCode(
    () => checkRoundScope({ moduleRoot, base, expectedBase: base }),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
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
  "R7_ACCEPTANCE.md",
  "R8_ACCEPTANCE.md",
  "R9_ACCEPTANCE.md",
  "R10_ACCEPTANCE.md",
  "R11_ACCEPTANCE.md",
  "R12_ACCEPTANCE.md",
  "R13_ACCEPTANCE.md",
  "R14_ACCEPTANCE.md",
  "R15_ACCEPTANCE.md",
  "R16_ACCEPTANCE.md",
  "R17_ACCEPTANCE.md",
  "R18_ACCEPTANCE.md",
  "R19_ACCEPTANCE.md",
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
  "docs/SCENE_PACK.md",
  "docs/SCENE_PACK_THREAT_MODEL.md",
  "docs/PROTOTYPE_GENERATION.md",
  "docs/PROTOTYPE_GENERATION_THREAT_MODEL.md",
  "docs/MODEL_CALL_APPROVAL.md",
  "docs/adr/0008-r7-scene-pack-governance.md",
  "docs/adr/0009-r8-natural-language-prototype-governance.md",
  "docs/adr/0010-r9-asset-materialization-governance.md",
  "docs/adr/0011-r10-prototype-builder-governance.md",
  "docs/adr/0012-r11-spatial-environment-governance.md",
  "docs/PROTOTYPE_ASSET_BUNDLE.md",
  "docs/PROTOTYPE_ASSET_THREAT_MODEL.md",
  "docs/PROTOTYPE_ENVIRONMENT.md",
  "docs/PROTOTYPE_BUILDER_THREAT_MODEL.md",
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
  "apps/creator-web/src/pack-loader.ts",
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
  "packages/prototype-generation-contracts/src/index.mjs",
  "packages/prototype-asset-pipeline/src/index.mjs",
  "apps/runtime-godot/addons/gdgs/plugin.cfg",
  "third-party/godot-gaussian-splatting.lock.json",
  "examples/scene-bundles/kenney-prototype/assets/crate.glb",
]) {
  test(`rejects byte changes to frozen R1-R12 implementation ${frozenPath}`, (t) => {
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

test("round path classifier exposes stable R22 guard categories", () => {
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/npc-cognition-contracts/src/index.mjs`),
    null,
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/npc-cognition-runtime/src/index.mjs`),
    null,
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/tests/r22-falsification.test.mjs`),
    null,
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/tests/prototype-generation-cli.test.mjs`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/tests/prototype-generation-orchestrator.test.mjs`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/prototype-generator/src/openai-compatible.mjs`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/third-party/npc-cognition-references/reference.lock.json`),
    null,
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/prototype-spatial-solution-contracts/src/index.mjs`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/prototype-spatial-solver/src/index.mjs`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/prototype-spatial-verifier/src/index.mjs`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/scripts/lib/godot-runtime-core.mjs`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/tests/godot-runtime-adapter.test.mjs`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/apps/runtime-godot/spatial_solution_verification/verifier.gd`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/apps/runtime-godot/solved_spatial_prototype/solved_lab.gd`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/apps/runtime-godot/spatial_analysis/analyzer.gd`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/prototype-spatial-planning-contracts/src/index.mjs`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/apps/runtime-godot/addons/gdgs/plugin.cfg`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/prototype-generator/src/index.mjs`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/apps/runtime-godot/playable/first_person_controller.gd`),
    "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/prototype-asset-pipeline/src/index.mjs`),
      "ROUND_GUARD_FROZEN_ARTIFACT_CHANGED",
  );
  assert.equal(
    classifyRoundPath(`${MODULE_PREFIX}/packages/prototype-environment-pipeline/src/index.mjs`),
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
    classifyRoundPath(`${MODULE_PREFIX}/apps/creator-web/src/pack-loader.ts`),
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
