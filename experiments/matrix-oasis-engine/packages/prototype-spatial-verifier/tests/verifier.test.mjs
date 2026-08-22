import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import * as verifier from "../src/index.mjs";

const packageRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

test("public surface and operational error are exact", () => {
  assert.deepEqual(Object.keys(verifier).sort(), [
    "PrototypeSpatialVerifierOperationalError", "createGodotSpatialSolutionVerifier", "verifyPrototypeSpatialSolution",
  ]);
  const error = new verifier.PrototypeSpatialVerifierOperationalError();
  assert.equal(error.code, "PROTOTYPE_SPATIAL_VERIFIER_INTERNAL_ERROR");
  assert.equal(error.message, error.code);
  assert.equal("cause" in error, false);
});

test("configuration capture is descriptor-safe and redacted", () => {
  let calls = 0;
  const hostile = {};
  Object.defineProperty(hostile, "godotBin", { enumerable: true, get() { calls += 1; throw new Error("sensitive-verifier-path"); } });
  assert.throws(() => verifier.createGodotSpatialSolutionVerifier(hostile), (error) => error.code === "PROTOTYPE_SPATIAL_VERIFIER_INTERNAL_ERROR" && !String(error).includes("sensitive"));
  assert.equal(calls, 0);
});

test("invalid input is static and cannot start Godot", async () => {
  const result = await verifier.verifyPrototypeSpatialSolution({}, {});
  assert.deepEqual(result, {
    ok: false,
    diagnostics: [{ phase: "input", severity: "error", code: "PROTOTYPE_SPATIAL_VERIFIER_INPUT_INVALID", path: "", message: "PROTOTYPE_SPATIAL_VERIFIER_INPUT_INVALID" }],
  });
  assert.equal(Object.isFrozen(result), true);
  assert.equal(Object.isFrozen(result.diagnostics), true);
});

test("Node bridge is disposable, offline and topic-neutral", async () => {
  const source = await readFile(path.join(packageRoot, "src", "index.mjs"), "utf8");
  assert.match(source, /matrix-oasis-r14-verifier-/u);
  assert.match(source, /finally[\s\S]*rm\(temporaryRoot/u);
  assert.match(source, /shell: false/u);
  for (const forbidden of [
    ["fe", "tch("].join(""), ["node", ":http"].join(""), ["node", ":https"].join(""),
    ["open", "ai"].join(""), ["meshy", ".ai"].join(""), ["world", "labs"].join(""),
    ["last", "-train"].join(""), ["sub", "way"].join(""), ["car", "riage"].join(""), ["plat", "form"].join(""),
  ]) {
    assert.equal(source.toLowerCase().includes(forbidden.toLowerCase()), false, forbidden);
  }
});
