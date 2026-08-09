import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { prepareCreatorSession } from "../apps/creator-web/src/pack-loader.ts";
import {
  applySessionActionCandidate,
  resetSessionCandidate,
  selectSessionCandidate,
} from "../apps/creator-web/src/session-transaction.ts";

const mechanicsText = await readFile(
  new URL(
    "../examples/mechanics-conformance.authoring-game-pack.json",
    import.meta.url,
  ),
  "utf8",
);

async function session() {
  const result = await prepareCreatorSession(mechanicsText, {
    kind: "builtin",
    id: "neutral",
  });
  assert.equal(result.ok, true);
  return result.candidate;
}

test("reset keeps one prepared handle and artifact while replacing both snapshots", async () => {
  const original = await session();
  const advanced = applySessionActionCandidate(original, "action-initialize");
  assert.equal(advanced.ok, true);
  const reset = resetSessionCandidate(advanced.candidate);
  assert.equal(reset.ok, true);

  assert.strictEqual(reset.candidate.prepared, original.prepared);
  assert.strictEqual(reset.candidate.artifact, original.artifact);
  assert.strictEqual(reset.candidate.source, original.source);
  assert.equal(reset.candidate.snapshot.authoring.stepCount, 0);
  assert.equal(reset.candidate.snapshot.runtime.stepCount, 0);
  assert.equal(reset.candidate.transition, null);
});

test("one action commits only a matched dual snapshot and transition", async () => {
  const original = await session();
  const applied = applySessionActionCandidate(original, "action-initialize");
  assert.equal(applied.ok, true);

  assert.equal(applied.candidate.snapshot.authoring.stepCount, 1);
  assert.equal(applied.candidate.snapshot.runtime.stepCount, 1);
  assert.equal(applied.candidate.transition.actionId, "action-initialize");
  assert.deepEqual(
    applied.candidate.emittedCues,
    applied.candidate.transition.emittedCues,
  );
  assert.strictEqual(applied.candidate.artifact, original.artifact);
});

test("a stale operation candidate cannot overwrite a newer session", async () => {
  const original = await session();
  const replacement = await session();
  const late = resetSessionCandidate(original);
  assert.equal(late.ok, true);
  const decision = selectSessionCandidate(replacement, original, late.candidate);

  assert.equal(decision.committed, false);
  assert.strictEqual(decision.session, replacement);
  assert.strictEqual(decision.session.prepared, replacement.prepared);
  assert.strictEqual(decision.session.artifact, replacement.artifact);
  assert.strictEqual(decision.session.snapshot, replacement.snapshot);
});

test("parity failures and operational throws retain static diagnostics", async () => {
  const original = await session();
  const mismatch = () =>
    Object.freeze({
      ok: false,
      diagnostics: Object.freeze([
        Object.freeze({
          phase: "parity",
          severity: "error",
          code: "PACK_PARITY_MISMATCH",
          path: "/parity",
          message: "The Authoring and Runtime simulators produced different results.",
        }),
      ]),
    });
  const sentinel = ["PRIVATE", "PARITY", "THROW"].join("-");
  const throwing = () => {
    throw new Error(sentinel);
  };

  const mismatchResult = applySessionActionCandidate(original, "action", mismatch);
  const resetFailure = resetSessionCandidate(original, throwing);
  const actionFailure = applySessionActionCandidate(original, "action", throwing);

  assert.equal(mismatchResult.ok, false);
  assert.equal(mismatchResult.diagnostics[0].code, "PACK_PARITY_MISMATCH");
  for (const result of [resetFailure, actionFailure]) {
    assert.equal(result.ok, false);
    assert.equal(result.diagnostics[0].code, "PACK_PARITY_INTERNAL_ERROR");
    assert.equal(JSON.stringify(result).includes(sentinel), false);
    assert.equal(Object.isFrozen(result.diagnostics), true);
  }
});
