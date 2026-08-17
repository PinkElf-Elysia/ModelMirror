import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const manifest = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));

for (const name of [
  "analyze:spatial-environment",
  "qualify:r13-spatial-facts",
  "capture:spatial-facts",
  "verify:spatial-references",
  "verify:spatial-contracts",
  "verify:spatial-analysis",
]) {
  assert.equal(typeof manifest.scripts[name], "string", name);
}
assert.equal(manifest.version, "0.13.0-r13");
console.log("R13_VERIFY_OK interfaces=6 mvp=pending-spatial-solver");
