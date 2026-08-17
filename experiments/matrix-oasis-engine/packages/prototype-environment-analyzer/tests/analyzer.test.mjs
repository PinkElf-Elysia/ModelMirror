import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import * as analyzer from "../src/index.mjs";

const packageRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

test("public surface is exact and operational errors are static", () => {
  assert.deepEqual(Object.keys(analyzer).sort(), [
    "PrototypeEnvironmentAnalyzerOperationalError",
    "analyzePrototypeEnvironment",
    "createGodotEnvironmentAnalyzer",
  ]);
  const error = new analyzer.PrototypeEnvironmentAnalyzerOperationalError();
  assert.equal(error.code, "PROTOTYPE_SPATIAL_ANALYZER_INTERNAL_ERROR");
  assert.equal(error.message, error.code);
  assert.equal("cause" in error, false);
});

test("configuration is descriptor-safe and fails closed before analysis", () => {
  let getterCalls = 0;
  const hostile = {};
  Object.defineProperty(hostile, "godotBin", {
    enumerable: true,
    get() {
      getterCalls += 1;
      throw new Error("sensitive-analyzer-value");
    },
  });
  assert.throws(
    () => analyzer.createGodotEnvironmentAnalyzer(hostile),
    (error) => error.code === "PROTOTYPE_SPATIAL_ANALYZER_INTERNAL_ERROR" && !String(error).includes("sensitive"),
  );
  assert.equal(getterCalls, 0);
});

test("the Node analyzer source is topic-neutral", async () => {
  const nodeSource = await readFile(path.join(packageRoot, "src", "index.mjs"), "utf8");
  for (const topic of ["last-train", "subway", "carriage", "platform", "student", "nurse"] ) {
    assert.equal(nodeSource.toLowerCase().includes(topic), false, topic);
  }
});
