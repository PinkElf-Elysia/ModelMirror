import assert from "node:assert/strict";
import test from "node:test";
import { R12_LAST_TRAIN_ACCEPTANCE_PROFILE } from "../scripts/lib/r12-qualification-core.mjs";

test("R12 qualification profile contains only generic count and graph constraints", () => {
  assert.deepEqual(R12_LAST_TRAIN_ACCEPTANCE_PROFILE, {
    format: "matrix-oasis.prototype-acceptance-profile", formatVersion: "0.1.0",
    nodes: { min: 7, max: 16 }, endings: { min: 3, max: 3 }, actions: { min: 15, max: 1024 },
    zones: { min: 2, max: 4 }, props: { min: 3, max: 3 }, characterPlaceholders: { min: 3, max: 3 },
    requireReachableCycle: true, requireAllEndingsReachable: true, requireAllNonEnvironmentBriefsBound: true,
  });
  assert.equal(Object.isFrozen(R12_LAST_TRAIN_ACCEPTANCE_PROFILE), true);
  assert.equal(Object.isFrozen(R12_LAST_TRAIN_ACCEPTANCE_PROFILE.nodes), true);
  const serialized = JSON.stringify(R12_LAST_TRAIN_ACCEPTANCE_PROFILE).toLowerCase();
  for (const forbidden of ["train", "subway", "student", "nurse", "commuter", "ticket", "clock"]) {
    assert.equal(serialized.includes(forbidden), false);
  }
});
