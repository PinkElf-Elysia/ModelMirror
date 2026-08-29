import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const lock = JSON.parse(readFileSync(path.join(moduleRoot, "third-party", "npc-authority-references", "reference.lock.json"), "utf8"));
test("R19 references are fixed and never become production dependencies", () => {
  assert.equal(lock.schemaVersion, 1);
  assert.equal(lock.references.length, 5);
  assert.deepEqual(lock.references.map((entry) => entry.id), [...lock.references.map((entry) => entry.id)].sort());
  for (const entry of lock.references) {
    assert.match(entry.commit, /^[0-9a-f]{40}$/);
    assert.match(entry.fileSha256, /^[0-9a-f]{64}$/);
    assert.notEqual(entry.reuse, "production-dependency");
  }
});
