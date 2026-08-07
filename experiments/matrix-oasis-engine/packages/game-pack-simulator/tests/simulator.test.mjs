import assert from "node:assert/strict";
import test from "node:test";
import {
  applyGameSessionAction,
  createGameSession,
  GamePackSimulatorOperationalError,
  inspectGameSession,
  prepareAuthoringGamePack,
  prepareAuthoringGamePackJson,
} from "../src/index.mjs";
import { captureJsonValue } from "../src/safety.mjs";

function makePack() {
  return {
    format: "matrix-oasis.authoring-game-pack",
    formatVersion: "0.1.0",
    id: "simulator-core",
    contentVersion: "1",
    language: "en",
    title: "Simulator Core",
    summary: "Neutral simulator fixture.",
    entryNodeId: "node-start",
    entities: [{ id: "actor-unit", label: "Actor" }],
    variables: [
      { id: "flag-value", type: "boolean", initial: false },
      { id: "count-value", type: "integer", initial: 0 },
      {
        id: "mode-value",
        type: "enum",
        allowedValues: ["mode-alpha", "mode-beta"],
        initial: "mode-alpha",
      },
    ],
    cues: [
      { id: "cue-entry", channel: "visual", intent: "Enter a node." },
      { id: "cue-effect", channel: "ui", intent: "Apply an effect." },
      { id: "cue-end", channel: "audio", intent: "Enter an ending." },
    ],
    nodes: [
      {
        id: "node-start",
        title: "Start",
        text: "Start state.",
        entityIds: ["actor-unit"],
        entryCueIds: ["cue-entry"],
        actions: [
          {
            id: "action-go",
            label: "Go",
            when: {
              op: "all",
              conditions: [
                { op: "eq", variableId: "flag-value", value: false },
                { op: "gte", variableId: "count-value", value: 0 },
              ],
            },
            effects: [
              { op: "set", variableId: "flag-value", value: true },
              { op: "add", variableId: "count-value", value: 1 },
              { op: "set", variableId: "mode-value", value: "mode-beta" },
              { op: "emitCue", cueId: "cue-effect" },
            ],
            target: { kind: "node", id: "node-finish" },
          },
          {
            id: "action-blocked",
            label: "Blocked",
            when: { op: "eq", variableId: "flag-value", value: true },
            effects: [],
            target: { kind: "ending", id: "ending-done" },
          },
        ],
      },
      {
        id: "node-finish",
        title: "Finish",
        entityIds: [],
        entryCueIds: ["cue-entry"],
        actions: [
          {
            id: "action-finish",
            label: "Finish",
            effects: [
              { op: "add", variableId: "count-value", value: 1 },
              { op: "emitCue", cueId: "cue-end" },
            ],
            target: { kind: "ending", id: "ending-done" },
          },
        ],
      },
    ],
    endings: [
      {
        id: "ending-done",
        title: "Done",
        text: "Terminal state.",
        cueIds: ["cue-end"],
      },
    ],
  };
}

function assertDeepFrozen(value, seen = new WeakSet()) {
  if (value === null || typeof value !== "object" || seen.has(value)) {
    return;
  }
  seen.add(value);
  assert.equal(Object.isFrozen(value), true);
  for (const key of Reflect.ownKeys(value)) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (descriptor && "value" in descriptor) {
      assertDeepFrozen(descriptor.value, seen);
    }
  }
}

function prepare(pack = makePack()) {
  const result = prepareAuthoringGamePack(pack);
  assert.equal(result.ok, true);
  return result.prepared;
}

test("captures arrays through descriptors without executing a get trap", () => {
  let getCount = 0;
  const proxied = new Proxy([1, 2], {
    get(target, property, receiver) {
      getCount += 1;
      return Reflect.get(target, property, receiver);
    },
  });
  const result = captureJsonValue(proxied);
  assert.equal(result.ok, true);
  assert.deepEqual(result.value, [1, 2]);
  assert.equal(getCount, 0);
});

test("prepares value and JSON inputs without retaining caller data", () => {
  const pack = makePack();
  const fromValue = prepareAuthoringGamePack(pack);
  const fromJson = prepareAuthoringGamePackJson(JSON.stringify(pack));
  assert.equal(fromValue.ok, true);
  assert.equal(fromJson.ok, true);
  assert.deepEqual(Object.keys(fromValue.prepared), []);
  assert.equal(Object.isFrozen(fromValue.prepared), true);

  pack.title = "Changed after prepare";
  const created = createGameSession(fromValue.prepared);
  assert.equal(created.ok, true);
  assert.equal(created.inspection.pack.title, "Simulator Core");
  assert.deepEqual(created, createGameSession(fromJson.prepared));
  assertDeepFrozen(fromValue);
  assertDeepFrozen(created);
});

test("returns an immutable R1 validation report for invalid content", () => {
  const pack = makePack();
  delete pack.title;
  const result = prepareAuthoringGamePack(pack);
  assert.equal(result.ok, false);
  assert.equal(result.validationReport.valid, false);
  assert.equal(result.validationReport.diagnostics[0].phase, "schema");
  assertDeepFrozen(result);
});

test("maps unrecoverable prepare failures to the simulator operational error", () => {
  const proxied = new Proxy(makePack(), {
    ownKeys() {
      throw new Error("dynamic input must not escape");
    },
  });
  assert.throws(
    () => prepareAuthoringGamePack(proxied),
    (error) => error instanceof GamePackSimulatorOperationalError &&
      error.code === "PACK_RUNTIME_INTERNAL_ERROR" &&
      !error.message.includes("dynamic"),
  );
});

test("creates the deterministic initial snapshot, inspection, and entry cues", () => {
  const result = createGameSession(prepare());
  assert.equal(result.ok, true);
  assert.equal(result.snapshot.snapshotVersion, 1);
  assert.equal(result.snapshot.status, "active");
  assert.deepEqual(result.snapshot.location, { kind: "node", id: "node-start" });
  assert.equal(Object.getPrototypeOf(result.snapshot.variables), null);
  assert.deepEqual({ ...result.snapshot.variables }, {
    "flag-value": false,
    "count-value": 0,
    "mode-value": "mode-alpha",
  });
  assert.equal(result.snapshot.stepCount, 0);
  assert.equal(result.snapshot.stepLimit, 256);
  assert.deepEqual(result.emittedCues.map((cue) => cue.id), ["cue-entry"]);
  assert.deepEqual(
    result.inspection.actions.map(({ id, available }) => ({ id, available })),
    [
      { id: "action-go", available: true },
      { id: "action-blocked", available: false },
    ],
  );
});

test("validates options and opaque prepared handles without echoing input", () => {
  const prepared = prepare();
  assert.equal(createGameSession(prepared, { stepLimit: 1 }).snapshot.stepLimit, 1);
  for (const options of [
    { stepLimit: 0 },
    { stepLimit: 10_001 },
    { stepLimit: 1.5 },
    { stepLimit: 2, extra: "private-value" },
  ]) {
    const result = createGameSession(prepared, options);
    assert.equal(result.ok, false);
    assert.equal(result.diagnostics[0].code, "PACK_RUNTIME_OPTIONS_INVALID");
    assert.equal(JSON.stringify(result).includes("private-value"), false);
  }
  const invalidPrepared = createGameSession(Object.freeze({}));
  assert.equal(invalidPrepared.diagnostics[0].code, "PACK_RUNTIME_PREPARED_PACK_INVALID");
});

test("applies one atomic action with ordered effects and target cues", () => {
  const prepared = prepare();
  const created = createGameSession(prepared);
  const original = JSON.stringify(created.snapshot);
  const result = applyGameSessionAction(prepared, created.snapshot, "action-go");
  assert.equal(result.ok, true);
  assert.equal(JSON.stringify(created.snapshot), original);
  assert.equal(result.snapshot.stepCount, 1);
  assert.deepEqual(result.snapshot.location, { kind: "node", id: "node-finish" });
  assert.deepEqual({ ...result.snapshot.variables }, {
    "flag-value": true,
    "count-value": 1,
    "mode-value": "mode-beta",
  });
  assert.deepEqual(result.transition.emittedCues.map((cue) => cue.id), [
    "cue-effect",
    "cue-entry",
  ]);
  assert.equal(result.transition.step, 1);
  assertDeepFrozen(result);
});

test("round-tripped snapshots continue with the same prepared handle", () => {
  const prepared = prepare();
  const created = createGameSession(prepared);
  const first = applyGameSessionAction(prepared, created.snapshot, "action-go");
  const roundTripped = JSON.parse(JSON.stringify(first.snapshot));
  const inspected = inspectGameSession(prepared, roundTripped);
  const finished = applyGameSessionAction(prepared, roundTripped, "action-finish");
  assert.equal(inspected.ok, true);
  assert.equal(finished.ok, true);
  assert.equal(finished.snapshot.status, "ended");
  assert.deepEqual(finished.transition.emittedCues.map((cue) => cue.id), [
    "cue-end",
    "cue-end",
  ]);
  assert.equal(finished.inspection.actions.length, 0);
});

test("uses stable runtime failures for invalid calls and snapshots", () => {
  const prepared = prepare();
  const created = createGameSession(prepared);
  const unavailable = applyGameSessionAction(
    prepared,
    created.snapshot,
    "action-blocked",
  );
  assert.equal(unavailable.diagnostics[0].code, "PACK_RUNTIME_ACTION_UNAVAILABLE");
  const unknown = applyGameSessionAction(prepared, created.snapshot, "action-missing");
  assert.equal(unknown.diagnostics[0].code, "PACK_RUNTIME_ACTION_UNKNOWN");

  const extraSnapshot = { ...created.snapshot, extra: "private-value" };
  const invalid = inspectGameSession(prepared, extraSnapshot);
  assert.equal(invalid.diagnostics[0].code, "PACK_RUNTIME_INVALID_SNAPSHOT");
  assert.equal(JSON.stringify(invalid).includes("private-value"), false);

  const mismatched = {
    ...created.snapshot,
    pack: { ...created.snapshot.pack, id: "different-pack" },
  };
  assert.equal(
    inspectGameSession(prepared, mismatched).diagnostics[0].code,
    "PACK_RUNTIME_PACK_MISMATCH",
  );
});

test("enforces step limits and ended sessions", () => {
  const prepared = prepare();
  const created = createGameSession(prepared, { stepLimit: 1 });
  const first = applyGameSessionAction(prepared, created.snapshot, "action-go");
  assert.equal(first.ok, true);
  assert.equal(first.inspection.actions[0].available, false);
  assert.equal(
    applyGameSessionAction(prepared, first.snapshot, "action-finish").diagnostics[0].code,
    "PACK_RUNTIME_STEP_LIMIT",
  );

  const normal = createGameSession(prepared);
  const middle = applyGameSessionAction(prepared, normal.snapshot, "action-go");
  const ended = applyGameSessionAction(prepared, middle.snapshot, "action-finish");
  assert.equal(
    applyGameSessionAction(prepared, ended.snapshot, "action-finish").diagnostics[0].code,
    "PACK_RUNTIME_SESSION_ENDED",
  );
});

test("rolls back the whole step on integer overflow", () => {
  const pack = makePack();
  pack.variables[1].initial = Number.MAX_SAFE_INTEGER;
  pack.nodes[0].actions[0].effects = [
    { op: "set", variableId: "flag-value", value: true },
    { op: "emitCue", cueId: "cue-effect" },
    { op: "add", variableId: "count-value", value: 1 },
    { op: "set", variableId: "count-value", value: 0 },
  ];
  const prepared = prepare(pack);
  const created = createGameSession(prepared);
  const before = JSON.stringify(created.snapshot);
  const result = applyGameSessionAction(prepared, created.snapshot, "action-go");
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].code, "PACK_RUNTIME_INTEGER_OVERFLOW");
  assert.equal(JSON.stringify(created.snapshot), before);
  assert.equal(Object.hasOwn(result, "transition"), false);
});

test("canonicalizes negative zero and safely supports prototype-like variable IDs", () => {
  const pack = makePack();
  pack.variables[1].id = "constructor";
  pack.variables[1].initial = -0;
  for (const node of pack.nodes) {
    for (const action of node.actions) {
      if (action.when?.variableId === "count-value") {
        action.when.variableId = "constructor";
      }
      for (const condition of action.when?.conditions ?? []) {
        if (condition.variableId === "count-value") {
          condition.variableId = "constructor";
        }
      }
      for (const effect of action.effects) {
        if (effect.variableId === "count-value") {
          effect.variableId = "constructor";
        }
      }
    }
  }
  const created = createGameSession(prepare(pack));
  assert.equal(created.ok, true);
  assert.equal(Object.getPrototypeOf(created.snapshot.variables), null);
  assert.equal(Object.is(created.snapshot.variables.constructor, -0), false);
  assert.equal(created.snapshot.variables.constructor, 0);
});
