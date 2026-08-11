import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  applyRuntimeGameSessionAction,
  createRuntimeGameSession,
  prepareRuntimeGamePackJson,
} from "@matrix-oasis/runtime-pack-simulator";
import {
  buildGodotParityCases,
  GODOT_TRACE_MARKER,
  GodotRuntimeHarnessError,
  parseGodotTraceOutput,
} from "../scripts/lib/godot-runtime-core.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const examplesRoot = path.join(moduleRoot, "examples");
const examples = Object.freeze([
  Object.freeze({
    name: "mechanics-conformance",
    text: fs.readFileSync(
      path.join(examplesRoot, "mechanics-conformance.authoring-game-pack.json"),
      "utf8",
    ),
  }),
  Object.freeze({
    name: "last-train-r1",
    text: fs.readFileSync(
      path.join(examplesRoot, "last-train-r1.authoring-game-pack.json"),
      "utf8",
    ),
  }),
]);

async function cases() {
  return buildGodotParityCases({
    examples,
    compileAuthoringGamePackJson,
    canonicalizeJsonValue,
    prepareRuntimeGamePackJson,
    createRuntimeGameSession,
    applyRuntimeGameSessionAction,
  });
}

function diagnosticCode(result) {
  return result.ok ? null : result.diagnostics[0].code;
}

function collectConditionOps(condition, output) {
  if (condition === null) {
    return;
  }
  output.add(condition.op);
  if (condition.op === "all" || condition.op === "any") {
    condition.conditions.forEach((child) => collectConditionOps(child, output));
  } else if (condition.op === "not") {
    collectConditionOps(condition.condition, output);
  }
}

test("parity cases freeze seven fixture-independent traces and twenty deterministic runs", async () => {
  const built = await cases();
  assert.equal(Object.isFrozen(built), true);
  assert.deepEqual(built.map((item) => [item.name, item.repetitions]), [
    ["mechanics-complete-with-failures", 20],
    ["last-train-return", 1],
    ["last-train-stay", 1],
    ["last-train-loop-ending", 1],
    ["last-train-explicit-loop-limit", 1],
    ["positive-overflow", 1],
    ["negative-overflow", 1],
  ]);
  assert.equal(built.reduce((sum, item) => sum + item.repetitions, 0), 26);
  assert.equal(built.every((item) => Object.isFrozen(item) && Object.isFrozen(item.referenceTrace)), true);
});

test("mechanics trace observes all condition, effect, target, Cue, and failure classes", async () => {
  const built = await cases();
  const mechanics = built[0];
  const runtimePack = JSON.parse(mechanics.runtimeText);
  const conditions = new Set();
  const effects = new Set();
  const targets = new Set();
  for (const node of runtimePack.nodes) {
    for (const action of node.actions) {
      collectConditionOps(action.when, conditions);
      action.effects.forEach((effect) => effects.add(effect.op));
      targets.add(action.target.kind);
    }
  }
  assert.deepEqual([...conditions].sort(), ["all", "any", "eq", "gt", "gte", "lt", "lte", "ne", "not"]);
  assert.deepEqual([...effects].sort(), ["add", "emitCue", "set"]);
  assert.deepEqual([...targets].sort(), ["ending", "node"]);
  assert.deepEqual(mechanics.referenceTrace.steps.map(diagnosticCode), [
    "PACK_RUNTIME_ACTION_UNKNOWN",
    null,
    "PACK_RUNTIME_ACTION_UNAVAILABLE",
    null,
    null,
    null,
    null,
    "PACK_RUNTIME_SESSION_ENDED",
  ]);
  assert.deepEqual(
    mechanics.referenceTrace.steps.filter((step) => step.ok).map((step) =>
      step.transition.emittedCues.map((cue) => cue.id)),
    [["cue-change"], ["cue-change"], [], ["cue-change", "cue-entry"], ["cue-complete", "cue-complete"]],
  );
});

test("integration, loop limit, and both overflow directions have exact reference outcomes", async () => {
  const built = await cases();
  assert.deepEqual(
    built.slice(1, 4).map((item) => item.referenceTrace.steps.at(-1).inspection.location.id),
    ["ending-return", "ending-stay", "ending-loop"],
  );
  assert.equal(
    diagnosticCode(built[4].referenceTrace.steps.at(-1)),
    "PACK_RUNTIME_STEP_LIMIT",
  );
  for (const item of built.slice(5)) {
    assert.equal(diagnosticCode(item.referenceTrace.steps[0]), "PACK_RUNTIME_INTEGER_OVERFLOW");
    assert.equal(item.referenceTrace.created.snapshot.stepCount, 0);
  }
});

test("reference traces are byte-stable across independent builds", async () => {
  const first = await cases();
  const second = await cases();
  assert.equal(
    JSON.stringify(first.map((item) => item.referenceTrace)),
    JSON.stringify(second.map((item) => item.referenceTrace)),
  );
});

test("trace marker parser accepts one exact successful trace and deep-freezes it", () => {
  const trace = {
    traceVersion: 1,
    created: { ok: true, snapshot: {}, inspection: {}, emittedCues: [] },
    steps: [],
  };
  const parsed = parseGodotTraceOutput(`${GODOT_TRACE_MARKER}${JSON.stringify(trace)}\n`, 0);
  assert.deepEqual(parsed, trace);
  assert.equal(Object.isFrozen(parsed), true);
  assert.equal(Object.isFrozen(parsed.created), true);
  assert.equal(Object.isFrozen(parsed.steps), true);
});

test("trace marker parser rejects nonzero, duplicate, malformed, and expanded roots", () => {
  const inputs = [
    [`${GODOT_TRACE_MARKER}{"traceVersion":1,"created":{"ok":true},"steps":[]}\n`, 1],
    [`${GODOT_TRACE_MARKER}{bad}\n`, 0],
    [`${GODOT_TRACE_MARKER}{"traceVersion":1,"created":{"ok":true},"steps":[],"extra":true}\n`, 0],
    [`${GODOT_TRACE_MARKER}{"traceVersion":1,"created":{"ok":true},"steps":[]}\n${GODOT_TRACE_MARKER}{}\n`, 0],
  ];
  for (const [output, status] of inputs) {
    assert.throws(
      () => parseGodotTraceOutput(output, status),
      (error) => error instanceof GodotRuntimeHarnessError &&
        ["GODOT_TRACE_MARKER_INVALID", "GODOT_TRACE_REPORT_INVALID"].includes(error.code),
    );
  }
});

test("Godot trace runner contains no fixture IDs or JavaScript evaluator imports", () => {
  const source = fs.readFileSync(
    path.join(moduleRoot, "apps", "runtime-godot", "runtime", "runtime_trace_runner.gd"),
    "utf8",
  );
  assert.equal(source.includes(GODOT_TRACE_MARKER), true);
  for (const forbidden of [
    "last-train",
    "ending-return",
    "ending-stay",
    "ending-loop",
    "game-pack-simulator",
    "runtime-pack-simulator",
    "game-pack-parity-harness",
  ]) {
    assert.equal(source.includes(forbidden), false);
  }
});
