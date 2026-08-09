import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import * as simulator from "../src/index.mjs";
import { evaluateRuntimeCondition } from "../src/session.mjs";

async function sha256(text) {
  const digest = await globalThis.crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(text),
  );
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0")).join("");
}

function runtimePack() {
  return {
    format: "matrix-oasis.runtime-game-pack",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    source: {
      format: "matrix-oasis.authoring-game-pack",
      formatVersion: "0.1.0",
      id: "runtime-simulator-unit",
      contentVersion: "1",
      canonicalSha256: "1".repeat(64),
    },
    language: "en",
    title: "Runtime simulator unit",
    summary: null,
    entryNodeIndex: 0,
    entities: [{ id: "entity-unit", label: "Unit", description: null }],
    variables: [
      { id: "flag", type: "boolean", initial: false },
      { id: "count", type: "integer", initial: 0 },
      {
        id: "mode",
        type: "enum",
        allowedValues: ["alpha", "beta"],
        initial: "alpha",
      },
    ],
    cues: [
      { id: "cue-entry", channel: "visual", intent: "Enter." },
      { id: "cue-step", channel: "ui", intent: "Step." },
      { id: "cue-end", channel: "audio", intent: "End." },
    ],
    nodes: [
      {
        id: "node-start",
        title: "Start",
        text: null,
        entityIndexes: [0],
        entryCueIndexes: [0],
        actions: [
          {
            id: "advance",
            label: "Advance",
            entityIndexes: [0],
            when: {
              op: "all",
              conditions: [
                { op: "eq", variableIndex: 0, value: false },
                { op: "gte", variableIndex: 1, value: 0 },
              ],
            },
            effects: [
              { op: "set", variableIndex: 0, value: true },
              { op: "add", variableIndex: 1, value: 1 },
              { op: "set", variableIndex: 2, value: "beta" },
              { op: "emitCue", cueIndex: 1 },
            ],
            target: { kind: "node", index: 1 },
          },
          {
            id: "blocked",
            label: "Blocked",
            entityIndexes: [],
            when: { op: "eq", variableIndex: 0, value: true },
            effects: [],
            target: { kind: "ending", index: 0 },
          },
        ],
      },
      {
        id: "node-finish",
        title: "Finish",
        text: "Finish the unit trace.",
        entityIndexes: [],
        entryCueIndexes: [1],
        actions: [
          {
            id: "finish",
            label: "Finish",
            entityIndexes: [],
            when: {
              op: "any",
              conditions: [
                { op: "ne", variableIndex: 2, value: "alpha" },
                {
                  op: "not",
                  condition: { op: "lt", variableIndex: 1, value: 1 },
                },
              ],
            },
            effects: [
              { op: "add", variableIndex: 1, value: -1 },
              { op: "emitCue", cueIndex: 1 },
            ],
            target: { kind: "ending", index: 0 },
          },
        ],
      },
    ],
    endings: [
      {
        id: "ending-done",
        title: "Done",
        text: null,
        cueIndexes: [2, 1],
      },
    ],
  };
}

async function artifact(pack = runtimePack()) {
  const runtimeText = canonicalizeJsonValue(pack);
  const receipt = {
    format: "matrix-oasis.runtime-game-pack-receipt",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    compiler: {
      id: "@matrix-oasis/game-pack-compiler",
      version: "0.1.0-r3",
    },
    artifact: {
      format: "matrix-oasis.runtime-game-pack",
      formatVersion: "0.1.0",
      sha256: await sha256(runtimeText),
      byteLength: new TextEncoder().encode(runtimeText).byteLength,
    },
  };
  return { runtimeText, receiptText: canonicalizeJsonValue(receipt) };
}

async function prepared(pack) {
  const pair = await artifact(pack);
  const result = await simulator.prepareRuntimeGamePackJson(
    pair.runtimeText,
    pair.receiptText,
  );
  assert.equal(result.ok, true);
  return result.prepared;
}

function diagnosticCode(result) {
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics.length, 1);
  return result.diagnostics[0].code;
}

test("public surface is the five frozen Runtime simulator symbols", () => {
  assert.deepEqual(Object.keys(simulator).sort(), [
    "RuntimeGamePackSimulatorOperationalError",
    "applyRuntimeGameSessionAction",
    "createRuntimeGameSession",
    "inspectRuntimeGameSession",
    "prepareRuntimeGamePackJson",
  ]);
});

test("prepare requires a canonical Runtime Pack and matching Receipt", async () => {
  const pair = await artifact();
  const valid = await simulator.prepareRuntimeGamePackJson(
    pair.runtimeText,
    pair.receiptText,
  );
  assert.equal(valid.ok, true);
  assert.equal(Object.getPrototypeOf(valid.prepared), null);
  assert.equal(Object.isFrozen(valid.prepared), true);

  const invalid = await simulator.prepareRuntimeGamePackJson(
    `${pair.runtimeText}\n`,
    pair.receiptText,
  );
  assert.equal(invalid.ok, false);
  assert.equal(invalid.validationReport.valid, false);
  assert.equal(Object.isFrozen(invalid.validationReport), true);
});

test("create binds source and artifact identity and emits entry Cues", async () => {
  const handle = await prepared();
  const created = simulator.createRuntimeGameSession(handle);
  assert.equal(created.ok, true);
  assert.deepEqual(created.snapshot.location, { kind: "node", index: 0 });
  assert.deepEqual(created.snapshot.variables, [false, 0, "alpha"]);
  assert.match(created.snapshot.pack.sourceSha256, /^[0-9a-f]{64}$/u);
  assert.match(created.snapshot.pack.artifactSha256, /^[0-9a-f]{64}$/u);
  assert.deepEqual(created.emittedCues.map((cue) => cue.id), ["cue-entry"]);
  assert.deepEqual(
    created.inspection.actions.map(({ id, available }) => [id, available]),
    [["advance", true], ["blocked", false]],
  );
  assert.equal(Object.isFrozen(created.snapshot.variables), true);
});

test("one-step actions preserve effect and target Cue order", async () => {
  const handle = await prepared();
  const created = simulator.createRuntimeGameSession(handle);
  const advanced = simulator.applyRuntimeGameSessionAction(
    handle,
    created.snapshot,
    "advance",
  );
  assert.equal(advanced.ok, true);
  assert.deepEqual(advanced.snapshot.variables, [true, 1, "beta"]);
  assert.deepEqual(advanced.transition.emittedCues.map((cue) => cue.id), [
    "cue-step",
    "cue-step",
  ]);
  assert.deepEqual(advanced.transition.to, {
    kind: "node",
    index: 1,
    id: "node-finish",
  });

  const ended = simulator.applyRuntimeGameSessionAction(
    handle,
    advanced.snapshot,
    "finish",
  );
  assert.equal(ended.ok, true);
  assert.deepEqual(ended.snapshot.variables, [true, 0, "beta"]);
  assert.deepEqual(ended.transition.emittedCues.map((cue) => cue.id), [
    "cue-step",
    "cue-end",
    "cue-step",
  ]);
  assert.equal(ended.inspection.status, "ended");
  assert.deepEqual(ended.inspection.actions, []);
  assert.equal(
    diagnosticCode(
      simulator.applyRuntimeGameSessionAction(handle, ended.snapshot, "finish"),
    ),
    "PACK_RUNTIME_SESSION_ENDED",
  );
});

test("expected runtime failures keep the R2 code and path contract", async () => {
  const handle = await prepared();
  const created = simulator.createRuntimeGameSession(handle, { stepLimit: 1 });
  assert.equal(created.ok, true);
  assert.equal(
    diagnosticCode(
      simulator.applyRuntimeGameSessionAction(handle, created.snapshot, "missing"),
    ),
    "PACK_RUNTIME_ACTION_UNKNOWN",
  );
  assert.equal(
    diagnosticCode(
      simulator.applyRuntimeGameSessionAction(handle, created.snapshot, "blocked"),
    ),
    "PACK_RUNTIME_ACTION_UNAVAILABLE",
  );
  const advanced = simulator.applyRuntimeGameSessionAction(
    handle,
    created.snapshot,
    "advance",
  );
  assert.equal(advanced.ok, true);
  assert.equal(
    diagnosticCode(
      simulator.applyRuntimeGameSessionAction(handle, advanced.snapshot, "finish"),
    ),
    "PACK_RUNTIME_STEP_LIMIT",
  );
  assert.equal(
    diagnosticCode(simulator.createRuntimeGameSession(handle, { stepLimit: 0 })),
    "PACK_RUNTIME_OPTIONS_INVALID",
  );
  for (const invalidOptions of [null, [], "default"]) {
    assert.equal(
      diagnosticCode(simulator.createRuntimeGameSession(handle, invalidOptions)),
      "PACK_RUNTIME_OPTIONS_INVALID",
    );
  }
  assert.equal(simulator.createRuntimeGameSession(handle, {}).ok, true);
  assert.equal(
    diagnosticCode(simulator.createRuntimeGameSession(Object.freeze({}))),
    "PACK_RUNTIME_PREPARED_PACK_INVALID",
  );
});

test("snapshots round-trip but reject extra, wrong-type and mismatched identity", async () => {
  const handle = await prepared();
  const created = simulator.createRuntimeGameSession(handle);
  const roundTrip = JSON.parse(JSON.stringify(created.snapshot));
  assert.equal(simulator.inspectRuntimeGameSession(handle, roundTrip).ok, true);

  assert.equal(
    diagnosticCode(
      simulator.inspectRuntimeGameSession(handle, { ...roundTrip, extra: true }),
    ),
    "PACK_RUNTIME_INVALID_SNAPSHOT",
  );
  assert.equal(
    diagnosticCode(
      simulator.inspectRuntimeGameSession(handle, {
        ...roundTrip,
        variables: ["false", 0, "alpha"],
      }),
    ),
    "PACK_RUNTIME_INVALID_SNAPSHOT",
  );
  assert.equal(
    diagnosticCode(
      simulator.inspectRuntimeGameSession(handle, {
        ...roundTrip,
        pack: { ...roundTrip.pack, artifactSha256: "0".repeat(64) },
      }),
    ),
    "PACK_RUNTIME_PACK_MISMATCH",
  );
});

test("positive and negative overflow are atomic and emit no Cue", async () => {
  for (const [initial, delta] of [
    [Number.MAX_SAFE_INTEGER, 1],
    [Number.MIN_SAFE_INTEGER, -1],
  ]) {
    const pack = runtimePack();
    pack.variables[1].initial = initial;
    pack.nodes[0].actions[0].when = null;
    pack.nodes[0].actions[0].effects = [
      { op: "emitCue", cueIndex: 1 },
      { op: "add", variableIndex: 1, value: delta },
    ];
    const handle = await prepared(pack);
    const created = simulator.createRuntimeGameSession(handle);
    const before = JSON.stringify(created.snapshot);
    const overflow = simulator.applyRuntimeGameSessionAction(
      handle,
      created.snapshot,
      "advance",
    );
    assert.equal(diagnosticCode(overflow), "PACK_RUNTIME_INTEGER_OVERFLOW");
    assert.equal(JSON.stringify(created.snapshot), before);
  }
});

test("set and add read the preceding working value in declared order", async () => {
  const pack = runtimePack();
  pack.nodes[0].actions[0].effects = [
    { op: "set", variableIndex: 1, value: 4 },
    { op: "add", variableIndex: 1, value: -1 },
  ];
  const handle = await prepared(pack);
  const created = simulator.createRuntimeGameSession(handle);
  const result = simulator.applyRuntimeGameSessionAction(
    handle,
    created.snapshot,
    "advance",
  );
  assert.equal(result.ok, true);
  assert.equal(result.snapshot.variables[1], 3);
});

test("condition operators keep strict boundaries and left-to-right short circuit", () => {
  const reads = [];
  const variables = new Proxy([1, 1], {
    get(target, property, receiver) {
      if (property === "0" || property === "1") {
        reads.push(property);
      }
      return Reflect.get(target, property, receiver);
    },
  });
  assert.equal(
    evaluateRuntimeCondition({
      op: "any",
      conditions: [
        { op: "eq", variableIndex: 0, value: 1 },
        { op: "eq", variableIndex: 1, value: 1 },
      ],
    }, variables),
    true,
  );
  assert.deepEqual(reads, ["0"]);
  assert.equal(evaluateRuntimeCondition({ op: "lt", variableIndex: 0, value: 1 }, variables), false);
  assert.equal(evaluateRuntimeCondition({ op: "lte", variableIndex: 0, value: 1 }, variables), true);
  assert.equal(evaluateRuntimeCondition({ op: "gt", variableIndex: 0, value: 1 }, variables), false);
  assert.equal(evaluateRuntimeCondition({ op: "gte", variableIndex: 0, value: 1 }, variables), true);
  assert.equal(evaluateRuntimeCondition({ op: "ne", variableIndex: 0, value: 2 }, variables), true);
  assert.equal(
    evaluateRuntimeCondition({
      op: "not",
      condition: { op: "eq", variableIndex: 0, value: 2 },
    }, variables),
    true,
  );
});

test("runtime sources are browser-only and independent from R2 and fixtures", async () => {
  const sourceNames = [
    "diagnostics.mjs",
    "prepared.mjs",
    "safety.mjs",
    "session.mjs",
    "snapshot.mjs",
  ];
  const sources = await Promise.all(
    sourceNames.map((name) =>
      readFile(new URL(`../src/${name}`, import.meta.url), "utf8")),
  );
  const forbidden = [
    "node" + ":",
    "game-pack-simulator",
    "game-pack-compiler",
    "examples/",
    "last" + "-train",
    "mechanics-conformance",
    "process" + ".env",
    "local" + "Storage",
    "fet" + "ch(",
  ];
  for (const token of forbidden) {
    assert.equal(sources.some((source) => source.includes(token)), false, token);
  }
});

test("operational error is static", () => {
  const error = new simulator.RuntimeGamePackSimulatorOperationalError();
  assert.equal(error.name, "RuntimeGamePackSimulatorOperationalError");
  assert.equal(error.code, "PACK_RUNTIME_INTERNAL_ERROR");
  assert.equal(error.message, "PACK_RUNTIME_INTERNAL_ERROR");
  assert.equal("cause" in error, false);
});
