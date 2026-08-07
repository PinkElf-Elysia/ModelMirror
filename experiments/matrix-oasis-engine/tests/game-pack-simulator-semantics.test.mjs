import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  applyGameSessionAction,
  createGameSession,
  inspectGameSession,
  prepareAuthoringGamePack,
  prepareAuthoringGamePackJson,
} from "@matrix-oasis/game-pack-simulator";
import * as simulatorPublicApi from "@matrix-oasis/game-pack-simulator";
import { evaluateCondition } from "../packages/game-pack-simulator/src/session.mjs";

const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const examplesRoot = path.join(moduleRoot, "examples");
const simulatorSourceRoot = path.join(
  moduleRoot,
  "packages",
  "game-pack-simulator",
  "src",
);

function exampleText(name) {
  return readFileSync(path.join(examplesRoot, name), "utf8");
}

function prepareText(text) {
  const result = prepareAuthoringGamePackJson(text);
  assert.equal(result.ok, true);
  return result.prepared;
}

function runActions(prepared, actionIds, options) {
  const created = createGameSession(prepared, options);
  assert.equal(created.ok, true);
  const steps = [];
  let snapshot = created.snapshot;
  for (const actionId of actionIds) {
    const result = applyGameSessionAction(prepared, snapshot, actionId);
    assert.equal(result.ok, true, `expected action ${actionId} to succeed`);
    steps.push(result);
    snapshot = result.snapshot;
  }
  return { created, steps, snapshot };
}

function collectConditionOps(condition, output) {
  if (!condition) {
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

function mechanicVariables(snapshot) {
  return {
    flag: snapshot.variables["flag-active"],
    counter: snapshot.variables["counter-value"],
    mode: snapshot.variables["mode-value"],
  };
}

const mechanicsText = exampleText("mechanics-conformance.authoring-game-pack.json");
const lastTrainText = exampleText("last-train-r1.authoring-game-pack.json");

test("the package root exposes only the frozen R2 public simulator API", () => {
  assert.deepEqual(Object.keys(simulatorPublicApi).sort(), [
    "GamePackSimulatorOperationalError",
    "applyGameSessionAction",
    "createGameSession",
    "inspectGameSession",
    "prepareAuthoringGamePack",
    "prepareAuthoringGamePackJson",
  ]);
});

test("the neutral authority fixture declares every R2 condition, effect, and target kind", () => {
  const pack = JSON.parse(mechanicsText);
  const conditionOps = new Set();
  const effectOps = new Set();
  const targetKinds = new Set();
  for (const node of pack.nodes) {
    for (const action of node.actions) {
      collectConditionOps(action.when, conditionOps);
      for (const effect of action.effects) {
        effectOps.add(effect.op);
      }
      targetKinds.add(action.target.kind);
    }
  }
  assert.deepEqual([...conditionOps].sort(), [
    "all",
    "any",
    "eq",
    "gt",
    "gte",
    "lt",
    "lte",
    "ne",
    "not",
  ]);
  assert.deepEqual([...effectOps].sort(), ["add", "emitCue", "set"]);
  assert.deepEqual([...targetKinds].sort(), ["ending", "node"]);
});

test("the neutral five-step trace is exact down to state, location, and Cue order", () => {
  const actions = [
    "action-initialize",
    "action-check",
    "action-adjust",
    "action-review",
    "action-complete",
  ];
  const { created, steps } = runActions(prepareText(mechanicsText), actions);
  const timeline = [
    {
      step: created.snapshot.stepCount,
      location: created.snapshot.location,
      variables: mechanicVariables(created.snapshot),
      cues: created.emittedCues.map((cue) => cue.id),
    },
    ...steps.map((result) => ({
      step: result.snapshot.stepCount,
      location: result.snapshot.location,
      variables: mechanicVariables(result.snapshot),
      cues: result.transition.emittedCues.map((cue) => cue.id),
    })),
  ];
  assert.deepEqual(timeline, [
    {
      step: 0,
      location: { kind: "node", id: "node-start" },
      variables: { flag: false, counter: 0, mode: "mode-alpha" },
      cues: ["cue-entry"],
    },
    {
      step: 1,
      location: { kind: "node", id: "node-check" },
      variables: { flag: true, counter: 1, mode: "mode-beta" },
      cues: ["cue-change"],
    },
    {
      step: 2,
      location: { kind: "node", id: "node-adjust" },
      variables: { flag: true, counter: 2, mode: "mode-beta" },
      cues: ["cue-change"],
    },
    {
      step: 3,
      location: { kind: "node", id: "node-review" },
      variables: { flag: true, counter: 1, mode: "mode-gamma" },
      cues: [],
    },
    {
      step: 4,
      location: { kind: "node", id: "node-complete" },
      variables: { flag: true, counter: 1, mode: "mode-gamma" },
      cues: ["cue-change", "cue-entry"],
    },
    {
      step: 5,
      location: { kind: "ending", id: "ending-pass" },
      variables: { flag: false, counter: 1, mode: "mode-gamma" },
      cues: ["cue-complete", "cue-complete"],
    },
  ]);
  assert.deepEqual(
    steps.map((result) => ({
      version: result.transition.transitionVersion,
      step: result.transition.step,
      actionId: result.transition.actionId,
      from: result.transition.from.id,
      to: `${result.transition.to.kind}:${result.transition.to.id}`,
    })),
    [
      { version: 1, step: 1, actionId: "action-initialize", from: "node-start", to: "node:node-check" },
      { version: 1, step: 2, actionId: "action-check", from: "node-check", to: "node:node-adjust" },
      { version: 1, step: 3, actionId: "action-adjust", from: "node-adjust", to: "node:node-review" },
      { version: 1, step: 4, actionId: "action-review", from: "node-review", to: "node:node-complete" },
      { version: 1, step: 5, actionId: "action-complete", from: "node-complete", to: "ending:ending-pass" },
    ],
  );
});

test("all nine operators produce the authored availability states along the neutral trace", () => {
  const prepared = prepareText(mechanicsText);
  const actionIds = [
    "action-initialize",
    "action-check",
    "action-adjust",
    "action-review",
  ];
  let snapshot = createGameSession(prepared).snapshot;
  for (const actionId of actionIds) {
    const applied = applyGameSessionAction(prepared, snapshot, actionId);
    assert.equal(applied.ok, true);
    assert.equal(applied.inspection.actions[0].available, true);
    assert.equal(applied.inspection.actions[1].available, false);
    snapshot = applied.snapshot;
  }
});

test("the same complete neutral input yields byte-identical results twenty times", () => {
  const serialized = [];
  const actions = [
    "action-initialize",
    "action-check",
    "action-adjust",
    "action-review",
    "action-complete",
  ];
  for (let iteration = 0; iteration < 20; iteration += 1) {
    const trace = runActions(prepareText(mechanicsText), actions);
    serialized.push(JSON.stringify({
      created: trace.created,
      steps: trace.steps,
    }));
  }
  assert.equal(new Set(serialized).size, 1);
});

test("the replaceable integration fixture reaches all three endings without special handling", () => {
  const paths = [
    {
      actions: [
        "inspect-map",
        "compare-ticket",
        "return-to-carriage",
        "ask-student",
        "trust-student",
        "compare-versions",
        "choose-return",
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
  for (const route of paths) {
    const result = runActions(prepareText(lastTrainText), route.actions);
    assert.deepEqual(result.snapshot.location, { kind: "ending", id: route.ending });
    assert.equal(result.snapshot.status, "ended");
    if (route.ending === "ending-stay") {
      assert.deepEqual(
        result.steps.at(-1).transition.emittedCues.map((cue) => cue.id),
        ["cue-memory-shift", "cue-final-choice"],
      );
    }
  }
});

test("multiple authored Cues preserve create, effect, and node-entry order", () => {
  const pack = JSON.parse(mechanicsText);
  pack.cues.push({
    id: "cue-extra",
    channel: "ui",
    intent: "Mark additional ordered output.",
  });
  pack.nodes[0].entryCueIds = ["cue-entry", "cue-extra"];
  pack.nodes[1].entryCueIds = ["cue-extra", "cue-entry"];
  pack.nodes[0].actions[0].effects.push({ op: "emitCue", cueId: "cue-extra" });
  const preparedResult = prepareAuthoringGamePack(pack);
  assert.equal(preparedResult.ok, true);
  const created = createGameSession(preparedResult.prepared);
  assert.deepEqual(created.emittedCues.map((cue) => cue.id), [
    "cue-entry",
    "cue-extra",
  ]);
  const applied = applyGameSessionAction(
    preparedResult.prepared,
    created.snapshot,
    "action-initialize",
  );
  assert.equal(applied.ok, true);
  assert.deepEqual(applied.transition.emittedCues.map((cue) => cue.id), [
    "cue-change",
    "cue-extra",
    "cue-extra",
    "cue-entry",
  ]);
});

test("the integration fixture can loop explicitly until the exact step limit", () => {
  const prepared = prepareText(lastTrainText);
  const result = runActions(
    prepared,
    ["inspect-map", "return-carriage", "inspect-map", "return-carriage"],
    { stepLimit: 4 },
  );
  assert.deepEqual(result.snapshot.location, { kind: "node", id: "node-carriage" });
  assert.equal(result.snapshot.stepCount, 4);
  assert.equal(result.steps[1].transition.emittedCues[0].id, "cue-arrival");
  assert.equal(result.steps[3].transition.emittedCues[0].id, "cue-arrival");
  const blocked = applyGameSessionAction(prepared, result.snapshot, "inspect-map");
  assert.equal(blocked.ok, false);
  assert.equal(blocked.diagnostics[0].code, "PACK_RUNTIME_STEP_LIMIT");
});

test("all, any, and not evaluate left to right with observable short circuiting", () => {
  const reads = [];
  const variables = new Proxy(
    { first: true, second: false },
    {
      get(target, property, receiver) {
        reads.push(property);
        return Reflect.get(target, property, receiver);
      },
    },
  );
  const firstTrue = { op: "eq", variableId: "first", value: true };
  const firstFalse = { op: "eq", variableId: "first", value: false };
  const second = { op: "eq", variableId: "second", value: true };

  assert.equal(
    evaluateCondition({ op: "any", conditions: [firstTrue, second] }, variables),
    true,
  );
  assert.deepEqual(reads.splice(0), ["first"]);
  assert.equal(
    evaluateCondition({ op: "all", conditions: [firstFalse, second] }, variables),
    false,
  );
  assert.deepEqual(reads.splice(0), ["first"]);
  assert.equal(evaluateCondition({ op: "not", condition: firstTrue }, variables), false);
  assert.deepEqual(reads.splice(0), ["first"]);
});

test("leaf comparisons distinguish strict and inclusive boundaries without coercion", () => {
  const variables = { number: 1, boolean: true, enum: "mode-alpha" };
  const cases = [
    [{ op: "eq", variableId: "number", value: 1 }, true],
    [{ op: "eq", variableId: "number", value: 2 }, false],
    [{ op: "ne", variableId: "boolean", value: true }, false],
    [{ op: "ne", variableId: "enum", value: "mode-beta" }, true],
    [{ op: "lt", variableId: "number", value: 1 }, false],
    [{ op: "lt", variableId: "number", value: 2 }, true],
    [{ op: "lte", variableId: "number", value: 1 }, true],
    [{ op: "lte", variableId: "number", value: 0 }, false],
    [{ op: "gt", variableId: "number", value: 1 }, false],
    [{ op: "gt", variableId: "number", value: 0 }, true],
    [{ op: "gte", variableId: "number", value: 1 }, true],
    [{ op: "gte", variableId: "number", value: 2 }, false],
  ];
  for (const [condition, expected] of cases) {
    assert.equal(evaluateCondition(condition, variables), expected);
  }
});

test("ordered set and add effects preserve both safe integer boundaries", () => {
  const cases = [
    {
      effects: [
        { op: "set", variableId: "counter-value", value: 4 },
        { op: "add", variableId: "counter-value", value: 3 },
        { op: "add", variableId: "counter-value", value: -2 },
      ],
      expected: 5,
    },
    {
      effects: [
        { op: "set", variableId: "counter-value", value: Number.MAX_SAFE_INTEGER - 1 },
        { op: "add", variableId: "counter-value", value: 1 },
      ],
      expected: Number.MAX_SAFE_INTEGER,
    },
    {
      effects: [
        { op: "set", variableId: "counter-value", value: Number.MIN_SAFE_INTEGER + 1 },
        { op: "add", variableId: "counter-value", value: -1 },
      ],
      expected: Number.MIN_SAFE_INTEGER,
    },
    {
      effects: [{ op: "set", variableId: "counter-value", value: -0 }],
      expected: 0,
    },
  ];
  for (const scenario of cases) {
    const pack = JSON.parse(mechanicsText);
    pack.nodes[0].actions[0].effects = scenario.effects;
    const preparedResult = prepareAuthoringGamePack(pack);
    assert.equal(preparedResult.ok, true);
    const created = createGameSession(preparedResult.prepared);
    const applied = applyGameSessionAction(
      preparedResult.prepared,
      created.snapshot,
      "action-initialize",
    );
    assert.equal(applied.ok, true);
    assert.equal(applied.snapshot.variables["counter-value"], scenario.expected);
    assert.equal(Object.is(applied.snapshot.variables["counter-value"], -0), false);
  }
});

test("positive and negative intermediate overflow roll back state, Cue, and step", () => {
  for (const scenario of [
    { boundary: Number.MAX_SAFE_INTEGER, delta: 1 },
    { boundary: Number.MIN_SAFE_INTEGER, delta: -1 },
  ]) {
    const pack = JSON.parse(mechanicsText);
    pack.nodes[0].actions[0].effects = [
      { op: "set", variableId: "flag-active", value: true },
      { op: "emitCue", cueId: "cue-change" },
      { op: "set", variableId: "counter-value", value: scenario.boundary },
      { op: "add", variableId: "counter-value", value: scenario.delta },
      { op: "set", variableId: "counter-value", value: 0 },
    ];
    const preparedResult = prepareAuthoringGamePack(pack);
    assert.equal(preparedResult.ok, true);
    const created = createGameSession(preparedResult.prepared);
    const before = JSON.stringify(created.snapshot);
    const result = applyGameSessionAction(
      preparedResult.prepared,
      created.snapshot,
      "action-initialize",
    );
    assert.equal(result.ok, false);
    assert.equal(result.diagnostics[0].code, "PACK_RUNTIME_INTEGER_OVERFLOW");
    assert.equal(JSON.stringify(created.snapshot), before);
    assert.deepEqual(Object.keys(result), ["ok", "diagnostics"]);
  }
});

test("an active node with no available action remains a legal stopped state", () => {
  const prepared = prepareText(mechanicsText);
  const created = createGameSession(prepared);
  const advanced = applyGameSessionAction(
    prepared,
    created.snapshot,
    "action-initialize",
  );
  assert.equal(advanced.ok, true);
  const stopped = JSON.parse(JSON.stringify(advanced.snapshot));
  stopped.variables["flag-active"] = false;
  stopped.variables["counter-value"] = 1;
  stopped.variables["mode-value"] = "mode-alpha";
  const inspected = inspectGameSession(prepared, stopped);
  assert.equal(inspected.ok, true);
  assert.equal(inspected.inspection.status, "active");
  assert.deepEqual(
    inspected.inspection.actions.map((action) => action.available),
    [false, false],
  );
  const attempted = applyGameSessionAction(prepared, stopped, "action-check");
  assert.equal(attempted.ok, false);
  assert.equal(attempted.diagnostics[0].code, "PACK_RUNTIME_ACTION_UNAVAILABLE");
  assert.equal(stopped.stepCount, 1);
});

test("snapshot gates reject missing, extra, and wrongly typed state without value leakage", () => {
  const prepared = prepareText(mechanicsText);
  const created = createGameSession(prepared);
  const { stepLimit: _omitted, ...missing } = created.snapshot;
  const extra = { ...created.snapshot, extraField: "private-sentinel" };
  const wrongVariable = {
    ...created.snapshot,
    variables: {
      ...created.snapshot.variables,
      "flag-active": "private-sentinel",
    },
  };
  for (const candidate of [missing, extra, wrongVariable]) {
    const result = inspectGameSession(prepared, candidate);
    assert.equal(result.ok, false);
    assert.equal(result.diagnostics[0].code, "PACK_RUNTIME_INVALID_SNAPSHOT");
    assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
  }
});

test("snapshot gates reject every nested identity, location, step, and variable shape class", () => {
  const prepared = prepareText(mechanicsText);
  const snapshot = createGameSession(prepared).snapshot;
  const missingVariables = { ...snapshot.variables };
  delete missingVariables["flag-active"];
  const candidates = [
    { ...snapshot, snapshotVersion: 2 },
    { ...snapshot, pack: { ...snapshot.pack, extra: "private-sentinel" } },
    { ...snapshot, location: { ...snapshot.location, extra: "private-sentinel" } },
    { ...snapshot, status: "ended" },
    { ...snapshot, location: { kind: "node", id: "node-unknown" } },
    { ...snapshot, stepCount: -1 },
    { ...snapshot, stepCount: 0.5 },
    { ...snapshot, stepCount: 2, stepLimit: 1 },
    { ...snapshot, stepLimit: 0 },
    { ...snapshot, stepLimit: 10_001 },
    { ...snapshot, variables: missingVariables },
    { ...snapshot, variables: { ...snapshot.variables, extra: false } },
    {
      ...snapshot,
      variables: {
        ...snapshot.variables,
        "counter-value": Number.MAX_SAFE_INTEGER + 1,
      },
    },
    {
      ...snapshot,
      variables: { ...snapshot.variables, "mode-value": "mode-unknown" },
    },
  ];
  for (const candidate of candidates) {
    const result = inspectGameSession(prepared, candidate);
    assert.equal(result.ok, false);
    assert.equal(result.diagnostics[0].code, "PACK_RUNTIME_INVALID_SNAPSHOT");
    assert.equal(JSON.stringify(result).includes("private-sentinel"), false);
    assert.equal(Object.isFrozen(result), true);
    assert.equal(Object.isFrozen(result.diagnostics), true);
    assert.equal(Object.isFrozen(result.diagnostics[0]), true);
  }
});

test("each Pack identity field is checked and valid JSON snapshots remain deterministic", () => {
  const prepared = prepareText(mechanicsText);
  const created = createGameSession(prepared);
  for (const [field, value] of [
    ["format", "other-format"],
    ["formatVersion", "9.9.9"],
    ["id", "other-pack"],
    ["contentVersion", "other-content"],
  ]) {
    const candidate = {
      ...created.snapshot,
      pack: { ...created.snapshot.pack, [field]: value },
    };
    const result = inspectGameSession(prepared, candidate);
    assert.equal(result.ok, false);
    assert.equal(result.diagnostics[0].code, "PACK_RUNTIME_PACK_MISMATCH");
  }

  const roundTripped = JSON.parse(JSON.stringify(created.snapshot));
  const first = inspectGameSession(prepared, created.snapshot);
  const second = inspectGameSession(prepared, roundTripped);
  assert.equal(first.ok, true);
  assert.equal(second.ok, true);
  assert.equal(JSON.stringify(first), JSON.stringify(second));
  assert.equal(Object.isFrozen(first), true);
  assert.equal(Object.isFrozen(first.inspection), true);
  assert.equal(Object.isFrozen(first.inspection.actions), true);
});

test("simulator runtime sources remain fixture- and subject-independent", () => {
  const source = readdirSync(simulatorSourceRoot)
    .filter((name) => name.endsWith(".mjs"))
    .sort()
    .map((name) => readFileSync(path.join(simulatorSourceRoot, name), "utf8"))
    .join("\n");
  assert.equal(source.includes("examples/"), false);
  assert.equal(source.includes("examples\\"), false);
  for (const subjectToken of [
    "node-carriage",
    "memory-shifts",
    "trusted-version",
    "ending-return",
    "last-train-r1",
    "回声十三站",
    "train",
    "platform",
    "station",
    "carriage",
  ]) {
    assert.equal(source.includes(subjectToken), false);
  }
});
