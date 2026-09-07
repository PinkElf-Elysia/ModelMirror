import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import {
  analyzeSourceText,
  FIXED_BASE,
  maskNonCode,
  REQUIRED_BRANCH,
  validateChangedPaths,
  validateLinkTarget,
} from "../scripts/check-boundary-rpg02.mjs";
import { verifyRpg02Revision } from "../scripts/verify-rpg02.mjs";
import policy from "../module-boundary.json" with { type: "json" };

test("02A1 policy fixes the accepted base, branch, layers, and bootstrap boundary", () => {
  assert.equal(FIXED_BASE, "a43cfa389e1785a95f04a006ba26550a5a36965e");
  assert.equal(REQUIRED_BRANCH, "codex/ai-rpg-rpg02-content");
  const descendantHead = "b".repeat(40);
  assert.notEqual(descendantHead, FIXED_BASE);
  assert.deepEqual(verifyRpg02Revision(FIXED_BASE, descendantHead), {
    base: FIXED_BASE,
    candidateHead: descendantHead,
    diagnostics: [],
  });
  assert.equal(verifyRpg02Revision("0".repeat(40), descendantHead).diagnostics[0].code, "RPG02_VERIFY_BASE_ARGUMENT");
  assert.deepEqual(policy.sourceLayers.contentPrefixes, ["content/"]);
  assert.deepEqual(validateChangedPaths([
    "docs/ai-rpg-experiment/RPG02_PLAN.md",
    "experiments/ai-rpg-engine/scripts/check-boundary-rpg02.mjs",
  ]), []);
});

test("change allowlist rejects parent repository and generated paths independently", () => {
  const diagnostics = validateChangedPaths([
    "client/src/App.tsx",
    "experiments/ai-rpg-engine-evil/file.mjs",
    "experiments/ai-rpg-engine/.rpg02-work/output.json",
  ]);
  assert.deepEqual(diagnostics.map(({ code }) => code), [
    "RPG02_CHANGE_OUTSIDE_ALLOWLIST",
    "RPG02_CHANGE_OUTSIDE_ALLOWLIST",
    "RPG02_GENERATED_PATH_CHANGED",
  ]);
});

test("content layer rejects parent, absolute, and escaping imports", () => {
  for (const [source, code] of [
    ['import value from "../tooling/io.mjs";', "RPG02_CONTENT_LAYER_IMPORT"],
    ['import value from "C:/private/value.mjs";', "RPG02_ABSOLUTE_IMPORT"],
    ['import value from "../../outside.mjs";', "RPG02_PARENT_IMPORT"],
  ]) assert.equal(analyzeSourceText("experiments/ai-rpg-engine/content/example.mjs", source, policy).some((entry) => entry.code === code), true);
});

test("content and tooling reject network, source execution, dynamic loading, and stray subprocesses", () => {
  const cases = [
    ["content/network.mjs", 'fetch("https://invalid.example");', "RPG02_NETWORK_GLOBAL"],
    ["content/eval.mjs", 'eval("1 + 1");', "RPG02_SOURCE_EXECUTION"],
    ["content/dynamic.mjs", "const p = './x.mjs'; import(p);", "RPG02_DYNAMIC_LOAD"],
    ["tooling/spawn.mjs", 'spawnSync("node", []);', "RPG02_SUBPROCESS_OUTSIDE_GATE"],
  ];
  for (const [relative, source, expected] of cases) {
    const diagnostics = analyzeSourceText(`experiments/ai-rpg-engine/${relative}`, source, policy);
    assert.equal(diagnostics.some(({ code }) => code === expected), true, `${relative}: ${expected}`);
  }
});

test("schema enum text is masked instead of treated as executable source", () => {
  const source = `export const schema = { enum: ["fetch('x')", "eval('x')", "import('../server/x.mjs')"] };`;
  assert.equal(maskNonCode(source).includes("fetch"), false);
  assert.deepEqual(analyzeSourceText("experiments/ai-rpg-engine/content/schema.mjs", source, policy), []);
});

test("content rejects executable template interpolation conservatively", () => {
  const source = "const value = `prefix ${dangerous()} suffix`;";
  assert.equal(analyzeSourceText("experiments/ai-rpg-engine/content/template.mjs", source, policy).some(({ code }) => code === "RPG02_TEMPLATE_INTERPOLATION"), true);
});

test("link validation rejects broken and external links", () => {
  const root = path.resolve("C:/module");
  const link = path.join(root, "content", "link.mjs");
  assert.equal(validateLinkTarget(root, link, null)[0].code, "RPG02_BROKEN_SYMLINK");
  assert.equal(validateLinkTarget(root, link, path.resolve("C:/outside/value.mjs"))[0].code, "RPG02_EXTERNAL_SYMLINK");
});
