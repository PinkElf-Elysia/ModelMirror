import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const lock = JSON.parse(readFileSync(path.join(root, "third-party", "npc-behavior-references", "reference.lock.json"), "utf8"));
test("R20 references remain evidence and never production dependencies", () => {
  assert.equal(lock.schemaVersion, 1);
  assert.deepEqual(lock.references.map(({ id }) => id), [...lock.references.map(({ id }) => id)].sort());
  assert.equal(lock.references.every(({ reuse }) => reuse !== "production-dependency"), true);
  assert.equal(lock.references.find(({ id }) => id === "beehave-compatibility").tag, "v2.9.3");
  assert.equal(lock.references.find(({ id }) => id === "limboai-godot-4.6.3").tag, "v1.7.1");
});
