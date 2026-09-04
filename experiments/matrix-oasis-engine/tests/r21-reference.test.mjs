import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { assertR21ReferenceDirectory, assertR21ReferenceLock } from "../scripts/verify-r21-references.mjs";

const root = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const lock = JSON.parse(readFileSync(path.join(root, "third-party", "npc-derived-state-references", "reference.lock.json"), "utf8"));
const clone = () => structuredClone(lock);

test("R21 memory references are fixed, direct-license-only evidence", () => {
  assert.equal(assertR21ReferenceLock(clone()), true);
  assert.equal(lock.implementationDecision, "internal-canonical-reducers-only");
  assert.equal(lock.references.every(({ license, reuse }) => license.closure === "direct-only-transitive-unverified" && reuse !== "production-dependency"), true);
});

test("R21 reference validation rejects source identity and license drift", () => {
  const commitDrift = clone();
  commitDrift.references[0].commit = "0".repeat(40);
  assert.throws(() => assertR21ReferenceLock(commitDrift));
  const licenseDrift = clone();
  licenseDrift.references[0].license.spdx = "NOASSERTION";
  assert.throws(() => assertR21ReferenceLock(licenseDrift));
});

test("R21 reference validation rejects reordered, duplicate, or untriggered entries", () => {
  const reordered = clone();
  reordered.references.reverse();
  assert.throws(() => assertR21ReferenceLock(reordered));
  const duplicate = clone();
  duplicate.references[1].id = duplicate.references[0].id;
  assert.throws(() => assertR21ReferenceLock(duplicate));
  const untriggered = clone();
  untriggered.references[0].reEvaluateWhen = [];
  assert.throws(() => assertR21ReferenceLock(untriggered));
});

test("R21 reference validation rejects a candidate dependency in any manifest section", () => {
  for (const section of ["dependencies", "devDependencies", "optionalDependencies", "peerDependencies"]) {
    assert.throws(() => assertR21ReferenceLock(clone(), [JSON.stringify({ [section]: { minisearch: "7.2.0" } })]));
  }
});

test("R21 reference directory cannot contain candidate source or binaries", () => {
  assert.equal(assertR21ReferenceDirectory(["reference.lock.json"]), true);
  assert.throws(() => assertR21ReferenceDirectory(["reference.lock.json", "candidate.js"]));
  assert.throws(() => assertR21ReferenceDirectory(["reference.lock.json", "candidate.bin"]));
});

test("R21 reference validation rejects production reuse or overstated license closure", () => {
  const production = clone();
  production.references[0].reuse = "production-dependency";
  assert.throws(() => assertR21ReferenceLock(production));
  const closure = clone();
  closure.references[0].license.closure = "qualified";
  assert.throws(() => assertR21ReferenceLock(closure));
});
