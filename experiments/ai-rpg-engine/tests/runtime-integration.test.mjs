import assert from "node:assert/strict";
import test from "node:test";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { createRuntime, validateRuntimeSession } from "../runtime/index.mjs";
import { createModelMirrorAdapter, openFileSessionStore, sha256 } from "../runtime/node.mjs";
import { compileVerifiedContent } from "./runtime-fixtures.mjs";

const requireValid = (report) => { assert.equal(report?.valid, true, JSON.stringify(report?.diagnostics)); return report.value; };
const save = (file, value) => writeFile(file, JSON.stringify(value, null, 2) + "\n", { encoding: "utf8", flag: "wx" });

test("isolated ModelMirror HTTP binds two committed replies, recovery and observed cancellation to real local checkpoints", { timeout: 35000 }, async () => {
  assert.equal(typeof process.env.RPG03_HARNESS_CONFIG, "string", "RPG03_HARNESS_CONFIG_REQUIRED");
  const config = JSON.parse(await readFile(process.env.RPG03_HARNESS_CONFIG, "utf8"));
  assert.equal(config.evidenceKind, "mock");
  assert.equal(config.modelId, "rpg03/fake-text-v1");
  const url = new URL(config.baseUrl);
  assert.equal(url.hostname, "127.0.0.1"); assert.equal(url.protocol, "http:");
  const { cardPackage, playerSetup } = compileVerifiedContent();
  assert.equal(playerSetup.talents.length, 5);
  const resourceInput = { sessionId: "session.http", cardPackage, playerSetup };
  const adapter = requireValid(createModelMirrorAdapter({ baseUrl: config.baseUrl, evidenceKind: "mock", maxOutputTokens: 512, timeoutMs: 10000 }));
  requireValid(await adapter.initialize());
  let store = requireValid(await openFileSessionStore({ rootDirectory: config.sessionDirectory }));
  let runtime = requireValid(createRuntime({ store, modelAdapter: adapter, hash: sha256 }));
  const receipts = [], events = [];
  function request(id, revision, messages, kind = "action") {
    return { sessionId: resourceInput.sessionId, generationId: "generation." + id, exchangeId: "exchange." + id, expectedRevision: revision, input: { kind, text: kind === "query" ? "inspect" : "wait" }, messages, modelId: config.modelId, settings: { temperature: 0, maxTokens: 512 } };
  }
  function commit(generated) {
    return runtime.commitTurn({ format: "modelmirror.ai-rpg.turn-commit", formatVersion: "0.1.0", sessionId: resourceInput.sessionId, generationId: generated.generation.generationId, exchangeId: generated.generation.exchangeId, expectedRevision: generated.session.revision, acceptedStateFields: [] });
  }
  try {
    let session = requireValid(await runtime.createSession(resourceInput));
    const firstMessages = [{ role: "user", content: "RPG03_MOCK_NORMAL_ONE" }];
    const firstRequest = request("one", session.revision, firstMessages);
    const first = requireValid(await runtime.generateTurn(firstRequest, { onEvent: (event) => events.push(event) }));
    assert.equal(first.generation.status, "pending"); assert.equal(first.session.turns.length, 0);
    assert.equal(first.generation.receipt.observedModel, config.modelId);
    assert.equal(first.generation.receipt.serverReceipt.request_id, "rpg03-mock-1");
    session = requireValid(await commit(first));
    assert.equal(session.turns.length, 1); assert.equal(session.turns[0].exchange.proposal.suggestedActions.length, 1);
    receipts.push(first.generation.receipt);

    // Neutral test context prepared explicitly by this test, not RPG-04 orchestration.
    const secondMessages = [...firstMessages, { role: "assistant", content: JSON.stringify(session.turns[0].exchange.proposal) }, { role: "user", content: "RPG03_MOCK_NORMAL_TWO" }];
    const second = requireValid(await runtime.generateTurn(request("two", session.revision, secondMessages, "query")));
    assert.equal(second.generation.receipt.serverReceipt.request_id, "rpg03-mock-2");
    session = requireValid(await commit(second));
    assert.equal(session.turns.length, 2); assert.deepEqual(session.turns[1].acceptedStateFields, []);
    receipts.push(second.generation.receipt);
    const duplicate = requireValid(await runtime.generateTurn(firstRequest));
    assert.equal(duplicate.generation.status, "committed");
    const beforeRecovery = structuredClone(session);
    requireValid(await store.close());
    store = requireValid(await openFileSessionStore({ rootDirectory: config.sessionDirectory }));
    runtime = requireValid(createRuntime({ store, modelAdapter: adapter, hash: sha256 }));
    session = requireValid(await runtime.resumeSession(resourceInput));
    assert.equal(session.revision, beforeRecovery.revision + 1);
    assert.deepEqual(session.turns, beforeRecovery.turns); assert.deepEqual(session.state, beforeRecovery.state);

    let cancellation;
    const cancelled = await runtime.generateTurn(request("cancel", session.revision, [{ role: "user", content: "RPG03_MOCK_CANCEL" }]), {
      onEvent(event) {
        if (event.type === "draft" && !cancellation) cancellation = runtime.cancelGeneration({ sessionId: event.sessionId, generationId: event.generationId, expectedRevision: event.revision });
      },
    });
    assert.ok(cancellation, "RPG03_CANCEL_EVENT_REQUIRED");
    const requested = requireValid(await cancellation);
    assert.equal(requested.outcome, "cancel_requested");
    assert.equal(cancelled.valid, false);
    const final = requireValid(await runtime.readSession(resourceInput));
    const cancelledGeneration = final.generations.find((item) => item.generationId === "generation.cancel");
    assert.equal(cancelledGeneration.status, "cancelled");
    assert.equal(cancelledGeneration.receipt.cancellation.requested, true);
    assert.equal(cancelledGeneration.receipt.cancellation.clientAborted, true);
    assert.equal(cancelledGeneration.receipt.cancellation.upstreamConfirmed, null);
    assert.equal(final.pending, null); assert.deepEqual(final.turns, beforeRecovery.turns); assert.deepEqual(final.state, beforeRecovery.state);
    assert.equal(validateRuntimeSession(final, cardPackage, playerSetup, sha256).valid, true);
    assert.ok(events.some((event) => event.type === "draft"));
    await save(path.join(config.outputDirectory, "integration-receipt.json"), { format: "modelmirror.ai-rpg.offline-integration-receipt", formatVersion: "0.1.0", evidenceKind: "mock", candidateTreeSha256: config.candidateTreeSha256, moduleSourceSha256: config.moduleSourceSha256, sessionId: final.sessionId, revision: final.revision, resources: final.resources, formalTurns: final.turns.length, receipts, cancellation: cancelledGeneration.receipt, realProviderDispatches: 0 });
    await save(path.join(config.outputDirectory, "card-package.json"), cardPackage);
    await save(path.join(config.outputDirectory, "player-setup.json"), playerSetup);
    await save(path.join(config.outputDirectory, "cli-config.json"), { baseUrl: config.baseUrl, evidenceKind: "mock", sessionDirectory: path.join(config.outputDirectory, "cli-sessions"), cardPackagePath: path.join(config.outputDirectory, "card-package.json"), playerSetupPath: path.join(config.outputDirectory, "player-setup.json") });
  } finally {
    requireValid(await store.close());
  }
});
