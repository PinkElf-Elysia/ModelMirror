import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  applyRuntimeGameSessionAction,
  createRuntimeGameSession,
  inspectRuntimeGameSession,
  prepareRuntimeGamePackJson,
} from "@matrix-oasis/runtime-pack-simulator";

const mechanicsText = await readFile(
  new URL("../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url),
  "utf8",
);
const lastTrainText = await readFile(
  new URL("../examples/last-train-r1.authoring-game-pack.json", import.meta.url),
  "utf8",
);

async function compileAndPrepare(text) {
  const compiled = await compileAuthoringGamePackJson(text);
  assert.equal(compiled.ok, true);
  const receiptText = canonicalizeJsonValue(compiled.receipt);
  const prepared = await prepareRuntimeGamePackJson(
    compiled.canonicalJson,
    receiptText,
  );
  assert.equal(prepared.ok, true);
  return { compiled, prepared: prepared.prepared, receiptText };
}

function run(prepared, actions, options) {
  const created = createRuntimeGameSession(prepared, options);
  assert.equal(created.ok, true);
  const steps = [];
  let snapshot = created.snapshot;
  for (const actionId of actions) {
    const next = applyRuntimeGameSessionAction(prepared, snapshot, actionId);
    assert.equal(next.ok, true, actionId);
    steps.push(next);
    snapshot = next.snapshot;
  }
  return { created, steps, snapshot };
}

function collectConditionOps(condition, output) {
  if (condition === null) {
    return;
  }
  output.add(condition.op);
  if (condition.op === "all" || condition.op === "any") {
    for (const child of condition.conditions) {
      collectConditionOps(child, output);
    }
  } else if (condition.op === "not") {
    collectConditionOps(condition.condition, output);
  }
}

test("compiled neutral artifact retains all nine conditions, three effects, and two targets", async () => {
  const { compiled } = await compileAndPrepare(mechanicsText);
  const pack = compiled.runtimePack;
  const conditions = new Set();
  const effects = new Set();
  const targets = new Set();
  for (const node of pack.nodes) {
    for (const action of node.actions) {
      collectConditionOps(action.when, conditions);
      for (const effect of action.effects) {
        effects.add(effect.op);
      }
      targets.add(action.target.kind);
    }
  }
  assert.deepEqual([...conditions].sort(), [
    "all", "any", "eq", "gt", "gte", "lt", "lte", "ne", "not",
  ]);
  assert.deepEqual([...effects].sort(), ["add", "emitCue", "set"]);
  assert.deepEqual([...targets].sort(), ["ending", "node"]);
  assert.equal(
    JSON.stringify(pack).includes("variableId"),
    false,
  );
});

test("neutral five-step Runtime trace is exact down to variables, Cues, and ending", async () => {
  const { prepared } = await compileAndPrepare(mechanicsText);
  const trace = run(prepared, [
    "action-initialize",
    "action-check",
    "action-adjust",
    "action-review",
    "action-complete",
  ]);
  assert.deepEqual(
    trace.steps.map((step) => step.inspection.location.id),
    ["node-check", "node-adjust", "node-review", "node-complete", "ending-pass"],
  );
  assert.deepEqual(trace.snapshot.variables, [false, 1, "mode-gamma"]);
  assert.deepEqual(
    trace.steps.map((step) => step.transition.emittedCues.map((cue) => cue.id)),
    [["cue-change"], ["cue-change"], [], ["cue-change", "cue-entry"], ["cue-complete", "cue-complete"]],
  );
  assert.equal(trace.snapshot.status, "ended");
});

test("integration artifact reaches all three endings and retains explicit loops", async () => {
  const { prepared } = await compileAndPrepare(lastTrainText);
  const routes = [
    {
      actions: [
        "inspect-map", "compare-ticket", "return-to-carriage", "ask-student",
        "trust-student", "compare-versions", "choose-return",
      ],
      ending: "ending-return",
    },
    {
      actions: ["step-out", "follow-platform-light", "choose-stay"],
      ending: "ending-stay",
    },
    {
      actions: ["ask-student", "trust-student", "trust-announcement"],
      ending: "ending-loop",
    },
  ];
  for (const route of routes) {
    const trace = run(prepared, route.actions);
    assert.equal(trace.steps.at(-1).inspection.location.id, route.ending);
    assert.equal(trace.snapshot.status, "ended");
  }

  const loop = run(
    prepared,
    ["inspect-map", "return-carriage", "inspect-map", "return-carriage"],
    { stepLimit: 4 },
  );
  assert.equal(loop.steps.at(-1).inspection.location.id, "node-carriage");
  const blocked = applyRuntimeGameSessionAction(
    prepared,
    loop.snapshot,
    "inspect-map",
  );
  assert.equal(blocked.ok, false);
  assert.equal(blocked.diagnostics[0].code, "PACK_RUNTIME_STEP_LIMIT");
});

test("Runtime snapshots bind both source and artifact hashes", async () => {
  const { prepared } = await compileAndPrepare(mechanicsText);
  const created = createRuntimeGameSession(prepared);
  assert.equal(created.ok, true);
  assert.match(created.snapshot.pack.sourceSha256, /^[0-9a-f]{64}$/u);
  assert.match(created.snapshot.pack.artifactSha256, /^[0-9a-f]{64}$/u);
  const roundTrip = JSON.parse(JSON.stringify(created.snapshot));
  assert.equal(inspectRuntimeGameSession(prepared, roundTrip).ok, true);
  roundTrip.pack.sourceSha256 = "0".repeat(64);
  const mismatch = inspectRuntimeGameSession(prepared, roundTrip);
  assert.equal(mismatch.ok, false);
  assert.equal(mismatch.diagnostics[0].code, "PACK_RUNTIME_PACK_MISMATCH");
});

test("same compiled input yields byte-identical Runtime results twenty times", async () => {
  const serializations = [];
  for (let index = 0; index < 20; index += 1) {
    const { compiled, prepared, receiptText } = await compileAndPrepare(mechanicsText);
    const trace = run(prepared, [
      "action-initialize",
      "action-check",
      "action-adjust",
      "action-review",
      "action-complete",
    ]);
    serializations.push(JSON.stringify({
      canonicalJson: compiled.canonicalJson,
      receiptText,
      created: trace.created,
      steps: trace.steps,
    }));
  }
  assert.equal(new Set(serializations).size, 1);
});
