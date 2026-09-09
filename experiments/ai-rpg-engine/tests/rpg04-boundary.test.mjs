import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { ALLOWED_EXACT_PATHS, analyzeFileText, analyzeSourceText, FIXED_BASE, REQUIRED_BRANCH, validateChangedPaths, validateDependencySet, validateFrozenBytes, validateLinkTarget } from "../scripts/check-boundary-rpg04.mjs";
import policy from "../module-boundary.json" with { type: "json" };

test("04A1a fixes base, branch, owned trees, and no parent path exceptions", () => {
  assert.equal(FIXED_BASE, "1b280ed257a45672c4a3dc03745fcb585685faf9");
  assert.equal(REQUIRED_BRANCH, "codex/ai-rpg-rpg04-context");
  assert.deepEqual(ALLOWED_EXACT_PATHS, []);
  assert.deepEqual(policy.repositoryChangePolicy.allowedExactPaths, []);
  assert.deepEqual(policy.sourceLayers.contextPrefixes, ["context/"]);
});

test("change allowlist rejects parent files and generated work directories", () => {
  assert.deepEqual(validateChangedPaths(["experiments/ai-rpg-engine/context/index.mjs", "docs/ai-rpg-experiment/RPG04_PLAN.md"]), []);
  assert.deepEqual(validateChangedPaths(["server/main.py", "client/src/App.tsx"]).map(({ code }) => code), ["RPG04_CHANGE_OUTSIDE_ALLOWLIST", "RPG04_CHANGE_OUTSIDE_ALLOWLIST"]);
  for (const name of [".rpg02-work", ".rpg03-work", ".rpg04-work"]) assert.equal(validateChangedPaths([`experiments/ai-rpg-engine/${name}/state.json`])[0].code, "RPG04_GENERATED_PATH_CHANGED");
});

test("text scanner rejects synthetic sensitive content and forbidden paths", () => {
  const synthetic = ["sk", "synthetic", "0123456789ABCDEFGHIJ"].join("-");
  assert.equal(analyzeFileText("docs/ai-rpg-experiment/probe.txt", synthetic, policy)[0].code, "RPG04_SECRET_CONTENT");
  assert.equal(analyzeFileText("experiments/ai-rpg-engine/fixtures/page.html", synthetic, policy)[0].code, "RPG04_SECRET_CONTENT");
  assert.equal(analyzeFileText("docs/ai-rpg-experiment/.env", "placeholder", policy)[0].code, "RPG04_SECRET_OR_BINARY_PATH");
  assert.equal(policy.generatedPaths.includes(".rpg04-work"), true);
});

test("pure context permits only its own files, frozen runtime contracts, and Ajv", () => {
  assert.deepEqual(analyzeSourceText("experiments/ai-rpg-engine/context/core.mjs", 'import Ajv from "ajv/dist/2020.js"; import { validateRuntimeSession } from "../runtime/contracts.mjs"; import { validateCardPackage } from "../src/index.mjs";', policy), []);
  for (const [source, code] of [
    ['import fs from "node:fs";', "RPG04_CONTEXT_DEPENDENCY"],
    ['import x from "../runtime/index.mjs";', "RPG04_CONTEXT_LAYER_IMPORT"],
    ['import x from "../../../server/main.py";', "RPG04_PARENT_IMPORT"],
    ['fetch("https://example.test")', "RPG04_NETWORK_GLOBAL"],
    ["process.env.MODEL_URL", "RPG04_PURE_LAYER_ENV"],
    ['spawnSync("node", [])', "RPG04_SUBPROCESS_OUTSIDE_GATE"]
  ]) assert.equal(analyzeSourceText("experiments/ai-rpg-engine/context/core.mjs", source, policy).some((entry) => entry.code === code), true, code);
});

test("network and subprocess permissions are exact entrypoint declarations", () => {
  assert.deepEqual(analyzeSourceText("experiments/ai-rpg-engine/runtime/node/http.mjs", 'import https from "node:https";', policy), []);
  assert.equal(analyzeSourceText("experiments/ai-rpg-engine/runtime/node/other.mjs", 'import https from "node:https";', policy).some(({ code }) => code === "RPG04_NETWORK_IMPORT_OUTSIDE_ADAPTER"), true);
  assert.deepEqual(analyzeSourceText("experiments/ai-rpg-engine/scripts/check-boundary-rpg04.mjs", 'import cp from "node:child_process"; cp.spawnSync("git", []);', policy), []);
  assert.equal(analyzeSourceText("experiments/ai-rpg-engine/scripts/other.mjs", 'import cp from "node:child_process";', policy).some(({ code }) => code === "RPG04_SUBPROCESS_IMPORT_OUTSIDE_GATE"), true);
});

test("dynamic import and source execution fail in all new layers", () => {
  for (const [source, code] of [["import('./x.mjs')", "RPG04_DYNAMIC_LOAD"], ["eval('1')", "RPG04_SOURCE_EXECUTION"], ["new Function('return 1')", "RPG04_SOURCE_EXECUTION"], ["vm.runInNewContext('1')", "RPG04_SOURCE_EXECUTION"]]) assert.equal(analyzeSourceText("experiments/ai-rpg-engine/context/core.mjs", source, policy).some((entry) => entry.code === code), true);
});

test("frozen byte comparison catches baseline, index, workspace, and missing drift", () => {
  const original = Buffer.from("frozen");
  const expected = "FFB304816A1090313E833215C08DAE3D209CFAD1FFD1F674F0909A2AE99E1394";
  assert.deepEqual(validateFrozenBytes("fixture", expected, original, original, original), []);
  for (const values of [[Buffer.from("changed"), original, original], [original, Buffer.from("changed"), original], [original, original, Buffer.from("changed")], [original, null, original]]) assert.equal(validateFrozenBytes("fixture", expected, ...values)[0].code, "RPG04_FROZEN_HASH_DRIFT");
});

test("dependency set is pinned independently to the fixed baseline", () => {
  const baseline = { ajv: "8.20.0", parse5: "8.0.1" };
  assert.deepEqual(validateDependencySet({ parse5: "8.0.1", ajv: "8.20.0" }, baseline, baseline), []);
  assert.equal(validateDependencySet({ ...baseline, openai: "1.0.0" }, { ...baseline, openai: "1.0.0" }, baseline)[0].code, "RPG04_PACKAGE_DEPENDENCIES");
  assert.equal(validateDependencySet(baseline, { ajv: "8.21.0", parse5: "8.0.1" }, baseline)[0].code, "RPG04_PACKAGE_DEPENDENCIES");
});

test("absolute imports and unsafe links fail closed", () => {
  assert.equal(analyzeSourceText("experiments/ai-rpg-engine/context/core.mjs", 'import x from "C:/outside.mjs";', policy)[0].code, "RPG04_ABSOLUTE_IMPORT");
  const root = path.resolve("C:/module"), link = path.join(root, "context", "link.mjs");
  assert.equal(validateLinkTarget(root, link, null)[0].code, "RPG04_BROKEN_SYMLINK");
  assert.equal(validateLinkTarget(root, link, path.resolve("C:/outside/value.mjs"))[0].code, "RPG04_EXTERNAL_SYMLINK");
});
