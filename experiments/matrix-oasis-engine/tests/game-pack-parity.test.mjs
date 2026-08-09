import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  applyGamePackParitySessionAction,
  createGamePackParitySession,
  inspectGamePackParitySession,
  prepareGamePackParityJson,
} from "@matrix-oasis/game-pack-parity-harness";

const mechanicsText = await readFile(
  new URL("../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url),
  "utf8",
);
const lastTrainText = await readFile(
  new URL("../examples/last-train-r1.authoring-game-pack.json", import.meta.url),
  "utf8",
);

async function prepared(text) {
  const result = await prepareGamePackParityJson(text);
  assert.equal(result.ok, true);
  return result.prepared;
}

function stateKey(result) {
  return JSON.stringify({
    location: result.inspection.location.id,
    variables: result.inspection.variables.map(({ id, value }) => [id, value]),
    step: result.inspection.stepCount,
  });
}

function explore(preparedHandle, stepLimit) {
  const created = createGamePackParitySession(preparedHandle, { stepLimit });
  assert.equal(created.ok, true);
  const queue = [created];
  const visited = new Set();
  const endings = new Set();
  let transitions = 0;

  while (queue.length > 0) {
    const current = queue.shift();
    const key = stateKey(current);
    if (visited.has(key)) {
      continue;
    }
    visited.add(key);
    if (current.inspection.status === "ended") {
      endings.add(current.inspection.location.id);
      continue;
    }
    for (const action of current.inspection.actions) {
      if (!action.available) {
        continue;
      }
      const next = applyGamePackParitySessionAction(
        preparedHandle,
        current.snapshot,
        action.id,
      );
      assert.equal(next.ok, true, `${current.inspection.location.id}:${action.id}`);
      transitions += 1;
      queue.push(next);
    }
    assert.ok(visited.size < 10_000, "bounded parity exploration must terminate");
  }
  return { endings, states: visited.size, transitions };
}

test("bounded neutral exploration reaches its executable ending without mismatch", async () => {
  const result = explore(await prepared(mechanicsText), 8);
  assert.deepEqual([...result.endings].sort(), ["ending-pass"]);
  assert.ok(result.states >= 6);
  assert.ok(result.transitions >= 5);
});

test("bounded integration exploration reaches all three replaceable endings", async () => {
  const result = explore(await prepared(lastTrainText), 10);
  assert.deepEqual([...result.endings].sort(), [
    "ending-loop",
    "ending-return",
    "ending-stay",
  ]);
  assert.ok(result.states > 10);
  assert.ok(result.transitions > 10);
});

test("explicit integration loop remains in parity through the exact step limit", async () => {
  const handle = await prepared(lastTrainText);
  let current = createGamePackParitySession(handle, { stepLimit: 4 });
  for (const actionId of [
    "inspect-map", "return-carriage", "inspect-map", "return-carriage",
  ]) {
    current = applyGamePackParitySessionAction(handle, current.snapshot, actionId);
    assert.equal(current.ok, true);
  }
  assert.equal(current.inspection.location.id, "node-carriage");
  assert.equal(current.inspection.stepCount, 4);
  const blocked = applyGamePackParitySessionAction(
    handle,
    current.snapshot,
    "inspect-map",
  );
  assert.equal(blocked.ok, false);
  assert.equal(blocked.diagnostics[0].code, "PACK_RUNTIME_STEP_LIMIT");
});

test("expected action failures match and one-sided snapshot changes fail closed", async () => {
  const handle = await prepared(mechanicsText);
  const created = createGamePackParitySession(handle);
  const unavailable = applyGamePackParitySessionAction(
    handle,
    created.snapshot,
    "action-check-hold",
  );
  assert.equal(unavailable.ok, false);
  assert.equal(unavailable.diagnostics[0].code, "PACK_RUNTIME_ACTION_UNKNOWN");

  const altered = JSON.parse(JSON.stringify(created.snapshot));
  altered.runtime.stepCount = 1;
  const mismatch = inspectGamePackParitySession(handle, altered);
  assert.equal(mismatch.ok, false);
  assert.equal(mismatch.diagnostics[0].code, "PACK_PARITY_MISMATCH");
});

test("parity calls never modify the caller's Authoring text or snapshots", async () => {
  const originalText = mechanicsText;
  const handle = await prepared(originalText);
  const created = createGamePackParitySession(handle);
  const before = JSON.stringify(created.snapshot);
  const applied = applyGamePackParitySessionAction(
    handle,
    created.snapshot,
    "action-initialize",
  );
  assert.equal(applied.ok, true);
  assert.equal(mechanicsText, originalText);
  assert.equal(JSON.stringify(created.snapshot), before);
  assert.equal(Object.isFrozen(applied.snapshot), true);
  assert.equal(Object.isFrozen(applied.inspection), true);
  assert.equal(Object.isFrozen(applied.transition), true);
});
