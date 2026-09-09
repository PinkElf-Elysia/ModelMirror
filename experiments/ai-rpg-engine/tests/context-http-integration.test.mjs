import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { createModelMirrorAdapter, openFileSessionStore, sha256 } from "../runtime/node.mjs";
import { createPreparedRuntime } from "../tooling/context-runtime.mjs";
import { buildTestScenario, createTestContextInput } from "../tooling/context-test-input.mjs";

const requireValid = (report) => { assert.equal(report?.valid, true, JSON.stringify(report?.diagnostics)); return report.value; };
const resourceRequest = (sessionId, scenario) => ({ sessionId, cardPackage: scenario.cardPackage, playerSetup: scenario.playerSetup });
const stateValue = (session, fieldRef) => session.state.find((field) => field.fieldRef === fieldRef)?.value;
const commit = (runtime, generated, acceptedStateFields) => runtime.commitTurn({
  format: "modelmirror.ai-rpg.turn-commit", formatVersion: "0.1.0",
  sessionId: generated.session.sessionId, generationId: generated.generation.generationId,
  exchangeId: generated.generation.exchangeId, expectedRevision: generated.session.revision, acceptedStateFields,
});

test("RPG04 loopback HTTP compiles two worlds and preserves explicit state authority, recovery and terminal failures", {
  skip: typeof process.env.RPG04_HARNESS_CONFIG !== "string" ? "RPG04_HARNESS_CONFIG not provided" : false,
  timeout: 50000,
}, async () => {
  const config = JSON.parse(await readFile(process.env.RPG04_HARNESS_CONFIG, "utf8"));
  assert.deepEqual(Object.keys(config).sort(), ["baseUrl", "evidenceKind", "fakeBaseUrl", "modelId", "port", "workDirectory"].sort());
  assert.equal(config.evidenceKind, "mock"); assert.equal(config.modelId, "rpg04/fake-text-v1");
  const url = new URL(config.baseUrl); assert.equal(url.protocol, "http:"); assert.equal(url.hostname, "127.0.0.1");
  const adapter = requireValid(createModelMirrorAdapter({ baseUrl: config.baseUrl, evidenceKind: "mock", timeoutMs: 10000, trustedOutputBudget: { maxTokens: 2048 } }));
  requireValid(await adapter.initialize());

  for (const world of ["gu", "minecraft"]) {
    const scenario = buildTestScenario({ world }), sessionId = `session.rpg04.http.${world}`;
    const storeRoot = path.join(config.workDirectory, `sessions-${world}`);
    let store = requireValid(await openFileSessionStore({ rootDirectory: storeRoot }));
    let runtime = requireValid(createPreparedRuntime({ store, modelAdapter: adapter, hash: sha256, hostTemplate: scenario.hostTemplate }));
    const resources = resourceRequest(sessionId, scenario);
    let session = requireValid(await runtime.createSession(resources));
    const initialLtm = stateValue(session, "state.rpg04.memory.ltm");

    const actionInput = createTestContextInput({ scenario, session, turnIndex: 0, modelId: config.modelId });
    const action = requireValid(await runtime.generatePreparedTurn({ contextInput: actionInput }));
    assert.equal(action.generation.status, "pending"); assert.equal(action.bridgeReceipt.evidenceKind, "mock");
    assert.equal(action.generation.exchange.proposal.suggestedActions.length, 1);
    session = requireValid(await commit(runtime, action, ["state.rpg04.memory.stm"]));
    assert.equal(stateValue(session, "state.rpg04.memory.stm"), "Explicitly accepted offline scene.");
    assert.equal(stateValue(session, "state.rpg04.memory.ltm"), initialLtm);

    requireValid(await store.close());
    store = requireValid(await openFileSessionStore({ rootDirectory: storeRoot }));
    runtime = requireValid(createPreparedRuntime({ store, modelAdapter: adapter, hash: sha256, hostTemplate: scenario.hostTemplate }));
    session = requireValid(await runtime.resumeSession(resources));
    assert.equal(stateValue(session, "state.rpg04.memory.stm"), "Explicitly accepted offline scene.");
    assert.equal(stateValue(session, "state.rpg04.memory.ltm"), initialLtm);

    const queryInput = createTestContextInput({ scenario, session, turnIndex: 2, modelId: config.modelId });
    const query = requireValid(await runtime.generatePreparedTurn({ contextInput: queryInput }));
    assert.deepEqual(query.generation.exchange.proposal.stateProposals, []);
    session = requireValid(await commit(runtime, query, []));
    assert.equal(stateValue(session, "state.rpg04.memory.stm"), "Explicitly accepted offline scene.");

    const cancelInput = createTestContextInput({ scenario, session, turnIndex: 0, modelId: config.modelId });
    cancelInput.generationId = `generation.rpg04.${world}.cancel`; cancelInput.exchangeId = `exchange.rpg04.${world}.cancel`; cancelInput.input.text = "RPG04_MOCK_CANCEL";
    let cancellation;
    const cancelled = await runtime.generatePreparedTurn({ contextInput: cancelInput }, { onEvent(event) {
      if (event.type === "draft" && !cancellation) cancellation = runtime.cancelGeneration({ sessionId, generationId: event.generationId, expectedRevision: event.revision });
    } });
    assert.ok(cancellation); assert.equal(requireValid(await cancellation).outcome, "cancel_requested"); assert.equal(cancelled.valid, false);
    session = requireValid(await runtime.readSession(resources)); assert.equal(session.pending, null);

    const invalidInput = createTestContextInput({ scenario, session, turnIndex: 0, modelId: config.modelId });
    invalidInput.generationId = `generation.rpg04.${world}.invalid`; invalidInput.exchangeId = `exchange.rpg04.${world}.invalid`; invalidInput.input.text = "RPG04_MOCK_INVALID";
    const invalid = await runtime.generatePreparedTurn({ contextInput: invalidInput }); assert.equal(invalid.valid, false);
    const repeated = await runtime.generatePreparedTurn({ contextInput: invalidInput }); assert.equal(repeated.valid, false);
    session = requireValid(await runtime.readSession(resources));
    assert.equal(session.pending, null); assert.equal(session.turns.length, 2);
    assert.equal(stateValue(session, "state.rpg04.memory.stm"), "Explicitly accepted offline scene.");
    assert.equal(stateValue(session, "state.rpg04.memory.ltm"), initialLtm);
    requireValid(await store.close());
  }
});
