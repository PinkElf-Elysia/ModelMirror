import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import * as parity from "../src/index.mjs";

const mechanicsText = await readFile(
  new URL("../../../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url),
  "utf8",
);

function code(result) {
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics.length, 1);
  return result.diagnostics[0].code;
}

test("public surface is the five frozen parity symbols", () => {
  assert.deepEqual(Object.keys(parity).sort(), [
    "GamePackParityOperationalError",
    "applyGamePackParitySessionAction",
    "createGamePackParitySession",
    "inspectGamePackParitySession",
    "prepareGamePackParityJson",
  ]);
});

test("prepare compiles one input and exposes only canonical download text", async () => {
  const prepared = await parity.prepareGamePackParityJson(mechanicsText);
  assert.equal(prepared.ok, true);
  assert.equal(Object.getPrototypeOf(prepared.prepared), null);
  assert.equal(Object.isFrozen(prepared.prepared), true);
  assert.deepEqual(Object.keys(prepared.artifact), [
    "artifactVersion",
    "runtimePackJson",
    "runtimePackReceiptJson",
  ]);
  assert.equal(prepared.artifact.runtimePackJson.endsWith("\n"), false);
  assert.equal(prepared.artifact.runtimePackReceiptJson.endsWith("\n"), false);
  assert.equal(JSON.stringify(JSON.parse(prepared.artifact.runtimePackJson)), prepared.artifact.runtimePackJson);
  assert.equal(Object.isFrozen(prepared.artifact), true);
});

test("invalid Authoring content returns the compiler validation report", async () => {
  const source = JSON.parse(mechanicsText);
  delete source.entryNodeId;
  const result = await parity.prepareGamePackParityJson(JSON.stringify(source));
  assert.equal(result.ok, false);
  assert.equal(result.validationReport.valid, false);
  assert.equal(Object.isFrozen(result.validationReport), true);
});

test("neutral trace advances only when both simulators agree", async () => {
  const prepared = await parity.prepareGamePackParityJson(mechanicsText);
  assert.equal(prepared.ok, true);
  let current = parity.createGamePackParitySession(prepared.prepared);
  assert.equal(current.ok, true);
  assert.equal(current.inspection.location.id, "node-start");
  assert.deepEqual(current.emittedCues.map((cue) => cue.id), ["cue-entry"]);

  const actions = [
    "action-initialize",
    "action-check",
    "action-adjust",
    "action-review",
    "action-complete",
  ];
  for (const actionId of actions) {
    current = parity.applyGamePackParitySessionAction(
      prepared.prepared,
      current.snapshot,
      actionId,
    );
    assert.equal(current.ok, true, actionId);
  }
  assert.equal(current.inspection.status, "ended");
  assert.equal(current.inspection.location.id, "ending-pass");
  assert.equal(current.inspection.stepCount, 5);
  assert.deepEqual(
    current.inspection.variables.map(({ id, value }) => [id, value]),
    [["flag-active", false], ["counter-value", 1], ["mode-value", "mode-gamma"]],
  );
});

test("matching expected failures preserve the frozen runtime diagnostics", async () => {
  const prepared = await parity.prepareGamePackParityJson(mechanicsText);
  const created = parity.createGamePackParitySession(prepared.prepared, {
    stepLimit: 1,
  });
  assert.equal(created.ok, true);
  assert.equal(
    code(
      parity.applyGamePackParitySessionAction(
        prepared.prepared,
        created.snapshot,
        "missing",
      ),
    ),
    "PACK_RUNTIME_ACTION_UNKNOWN",
  );
  const advanced = parity.applyGamePackParitySessionAction(
    prepared.prepared,
    created.snapshot,
    "action-initialize",
  );
  assert.equal(advanced.ok, true);
  assert.equal(
    code(
      parity.applyGamePackParitySessionAction(
        prepared.prepared,
        advanced.snapshot,
        "action-check",
      ),
    ),
    "PACK_RUNTIME_STEP_LIMIT",
  );
  assert.equal(
    code(parity.createGamePackParitySession(prepared.prepared, { stepLimit: 0 })),
    "PACK_RUNTIME_OPTIONS_INVALID",
  );
  assert.equal(
    code(parity.createGamePackParitySession(prepared.prepared, null)),
    "PACK_RUNTIME_OPTIONS_INVALID",
  );
});

test("composite snapshots round-trip and detect one-sided tampering", async () => {
  const prepared = await parity.prepareGamePackParityJson(mechanicsText);
  const created = parity.createGamePackParitySession(prepared.prepared);
  const roundTrip = JSON.parse(JSON.stringify(created.snapshot));
  assert.equal(
    parity.inspectGamePackParitySession(prepared.prepared, roundTrip).ok,
    true,
  );

  const tampered = JSON.parse(JSON.stringify(roundTrip));
  tampered.runtime.pack.artifactSha256 = "0".repeat(64);
  assert.equal(
    code(parity.inspectGamePackParitySession(prepared.prepared, tampered)),
    "PACK_PARITY_MISMATCH",
  );
  assert.equal(
    code(
      parity.inspectGamePackParitySession(prepared.prepared, {
        ...roundTrip,
        extra: true,
      }),
    ),
    "PACK_PARITY_INVALID_SNAPSHOT",
  );
  assert.equal(
    code(parity.createGamePackParitySession(Object.freeze({}))),
    "PACK_PARITY_PREPARED_INVALID",
  );
});

test("same Authoring text produces identical artifacts and sessions twenty times", async () => {
  const outputs = [];
  for (let index = 0; index < 20; index += 1) {
    const prepared = await parity.prepareGamePackParityJson(mechanicsText);
    const created = parity.createGamePackParitySession(prepared.prepared);
    outputs.push(JSON.stringify({ artifact: prepared.artifact, created }));
  }
  assert.equal(new Set(outputs).size, 1);
});

test("harness source uses package roots and never imports simulator internals", async () => {
  const source = await readFile(new URL("../src/harness.mjs", import.meta.url), "utf8");
  assert.equal(source.includes("game-pack-simulator/src"), false);
  assert.equal(source.includes("runtime-pack-simulator/src"), false);
  const forbidden = [
    "node" + ":",
    "examples/",
    "last" + "-train",
    "mechanics-conformance",
    "fet" + "ch(",
    "process" + ".env",
    "local" + "Storage",
  ];
  for (const token of forbidden) {
    assert.equal(source.includes(token), false, token);
  }
});

test("operational error is static", () => {
  const error = new parity.GamePackParityOperationalError();
  assert.equal(error.name, "GamePackParityOperationalError");
  assert.equal(error.code, "PACK_PARITY_INTERNAL_ERROR");
  assert.equal(error.message, "PACK_PARITY_INTERNAL_ERROR");
  assert.equal("cause" in error, false);
});
