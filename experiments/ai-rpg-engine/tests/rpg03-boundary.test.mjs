import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { ALLOWED_EXACT_PATHS, analyzeSourceText, FIXED_BASE, REQUIRED_BRANCH, validateChangedPaths, validateLinkTarget } from "../scripts/check-boundary-rpg03.mjs";
import policy from "../module-boundary.json" with { type: "json" };

test("03A1 fixes the accepted base, branch, exact parent exceptions, and future runtime layers", () => {
  assert.equal(FIXED_BASE, "80221379cec850a2b25f5eeeb410233062f3e1ea");
  assert.equal(REQUIRED_BRANCH, "codex/ai-rpg-rpg03-runtime");
  assert.deepEqual(ALLOWED_EXACT_PATHS, ["docs/MODEL_PROVIDER_CONTROL_PLANE.md", "server/main.py", "server/tests/test_provider_chat_stable_chat.py"]);
  assert.deepEqual(policy.sourceLayers.contentPrefixes, ["content/"]);
  assert.deepEqual(policy.sourceLayers.runtimeNodePrefixes, ["runtime/node.mjs", "runtime/node/"]);
});

test("change allowlist accepts only both owned trees and the three exact parent files", () => {
  assert.deepEqual(validateChangedPaths(["experiments/ai-rpg-engine/runtime/index.mjs", "docs/ai-rpg-experiment/RPG03_PLAN.md", ...ALLOWED_EXACT_PATHS]), []);
  assert.deepEqual(validateChangedPaths(["server/other.py", "client/src/App.tsx", "experiments/ai-rpg-engine/.rpg03-work/state.json"]).map(({ code }) => code), ["RPG03_CHANGE_OUTSIDE_ALLOWLIST", "RPG03_CHANGE_OUTSIDE_ALLOWLIST", "RPG03_GENERATED_PATH_CHANGED"]);
});

test("pure runtime rejects I/O, network, environment, subprocess and bare dependencies", () => {
  const cases = [
    ['import fs from "node:fs";', "RPG03_RUNTIME_CORE_DEPENDENCY"],
    ['import sdk from "openai";', "RPG03_RUNTIME_CORE_DEPENDENCY"],
    ['fetch("http://127.0.0.1")', "RPG03_NETWORK_GLOBAL"],
    ["process.env.MODEL_URL", "RPG03_PURE_LAYER_ENV"],
    ['spawnSync("node", [])', "RPG03_SUBPROCESS_OUTSIDE_GATE"],
  ];
  for (const [source, expected] of cases) assert.equal(analyzeSourceText("experiments/ai-rpg-engine/runtime/core.mjs", source, policy).some(({ code }) => code === expected), true, expected);
});

test("node adapters allow only declared builtins and no arbitrary package", () => {
  assert.deepEqual(analyzeSourceText("experiments/ai-rpg-engine/runtime/node/http.mjs", 'import fs from "node:fs"; import https from "node:https";', policy), []);
  assert.equal(analyzeSourceText("experiments/ai-rpg-engine/runtime/node/http.mjs", 'import sdk from "openai";', policy)[0].code, "RPG03_RUNTIME_NODE_DEPENDENCY");
  assert.equal(analyzeSourceText("experiments/ai-rpg-engine/runtime/node/http.mjs", 'import cp from "node:child_process";', policy)[0].code, "RPG03_RUNTIME_NODE_BUILTIN");
});

test("lexical analysis ignores policy words in strings and comments", () => {
  const source = `// eval('x'); import('../server/x.mjs')\nexport const schema = { examples: ["fetch('x')", "process.env.KEY", "spawnSync('x')"] };`;
  assert.deepEqual(analyzeSourceText("experiments/ai-rpg-engine/runtime/schema.mjs", source, policy), []);
});

test("static import scanning catches side effects, multiline from, and pure-to-node imports", () => {
  for (const source of ['import "node:fs";', 'import { readFile }\n from "node:fs";']) {
    assert.equal(analyzeSourceText("experiments/ai-rpg-engine/runtime/core.mjs", source, policy).some(({ code }) => code === "RPG03_RUNTIME_CORE_DEPENDENCY"), true);
  }
  for (const source of ['import x from "./node/http.mjs";', 'import x from "../runtime/node/http.mjs";', 'import x from "../tooling/runtime-cli.mjs";']) {
    assert.equal(analyzeSourceText("experiments/ai-rpg-engine/runtime/core.mjs", source, policy).some(({ code }) => code === "RPG03_RUNTIME_LAYER_IMPORT"), true);
  }
  assert.deepEqual(analyzeSourceText("experiments/ai-rpg-engine/runtime/schema.mjs", 'import Ajv from "ajv/dist/2020.js";', policy), []);
});

test("pure runtime conservatively rejects template interpolation", () => {
  assert.equal(analyzeSourceText("experiments/ai-rpg-engine/runtime/core.mjs", "const x = `value ${fetch(url)}`;", policy).some(({ code }) => code === "RPG03_RUNTIME_TEMPLATE_INTERPOLATION"), true);
  assert.deepEqual(analyzeSourceText("experiments/ai-rpg-engine/runtime/core.mjs", 'const x = "literal ${fetch(url)}";', policy), []);
});

test("loopback network and subprocess builtins are limited to exact verification files", () => {
  assert.deepEqual(analyzeSourceText("experiments/ai-rpg-engine/tests/runtime-adapter.test.mjs", 'import http from "node:http";', policy), []);
  assert.equal(analyzeSourceText("experiments/ai-rpg-engine/tests/other.test.mjs", 'import http from "node:http";', policy)[0].code, "RPG03_TOOLING_BUILTIN");
  assert.deepEqual(analyzeSourceText("experiments/ai-rpg-engine/tests/runtime-store.test.mjs", 'import cp from "node:child_process"; cp.spawnSync("node", []);', policy), []);
  assert.equal(analyzeSourceText("experiments/ai-rpg-engine/tests/other.test.mjs", 'import cp from "node:child_process";', policy)[0].code, "RPG03_SUBPROCESS_IMPORT_OUTSIDE_GATE");
});

test("dynamic import and executable constructors fail in every new layer", () => {
  for (const [source, code] of [["import('./x.mjs')", "RPG03_DYNAMIC_LOAD"], ["eval('1')", "RPG03_SOURCE_EXECUTION"], ["new Function('return 1')", "RPG03_SOURCE_EXECUTION"]]) {
    assert.equal(analyzeSourceText("experiments/ai-rpg-engine/runtime/node/adapter.mjs", source, policy).some((entry) => entry.code === code), true);
  }
});

test("relative parent, absolute imports, and unsafe links fail closed", () => {
  assert.equal(analyzeSourceText("experiments/ai-rpg-engine/runtime/core.mjs", 'import x from "../../../server/main.py";', policy)[0].code, "RPG03_PARENT_IMPORT");
  assert.equal(analyzeSourceText("experiments/ai-rpg-engine/runtime/core.mjs", 'import x from "C:/outside.mjs";', policy)[0].code, "RPG03_ABSOLUTE_IMPORT");
  const root = path.resolve("C:/module"), link = path.join(root, "runtime", "link.mjs");
  assert.equal(validateLinkTarget(root, link, null)[0].code, "RPG03_BROKEN_SYMLINK");
  assert.equal(validateLinkTarget(root, link, path.resolve("C:/outside/value.mjs"))[0].code, "RPG03_EXTERNAL_SYMLINK");
});
