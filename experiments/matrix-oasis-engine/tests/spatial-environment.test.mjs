import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../packages/prototype-spatial-environment/", import.meta.url);

test("spatial environment package remains offline and topic neutral", async () => {
  const files = ["src/index.mjs", "src/pipeline.mjs", "src/convert.mjs", "src/safety.mjs"];
  const source = (await Promise.all(files.map((file) => readFile(new URL(file, root), "utf8")))).join("\n");
  for (const forbidden of [
    "fetch(", ["node", ":http"].join(""), ["node", ":https"].join(""), ["process", ".env"].join(""),
    ["WORLD", "_LABS_API_KEY"].join(""), ["MATRIX_OASIS", "_MARBLE_API_KEY"].join(""),
    "last-train", "ending-return", "rgb_0.png",
  ]) assert.equal(source.includes(forbidden), false, forbidden);
  assert.equal(source.includes("@matrix-oasis/prototype-environment-pipeline"), true);
  assert.equal(source.includes("@playcanvas/splat-transform"), true);
});

test("package manifest pins the audited converter and decoder", async () => {
  const manifest = JSON.parse(await readFile(new URL("package.json", root), "utf8"));
  assert.equal(manifest.private, true);
  assert.equal(manifest.license, "UNLICENSED");
  assert.equal(manifest.dependencies["@playcanvas/splat-transform"], "3.3.0");
  assert.equal(manifest.dependencies["@adobe/spz"], "0.2.2");
});
