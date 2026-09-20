import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { createNpcAuthoritySession } from "@matrix-oasis/npc-authority-session";
import { hashCanonicalValue } from "@matrix-oasis/npc-authority-runtime";
import {
  prepareDeterministicNpcBehavior,
  selectNextNpcBehaviorCommand,
  synthesizeNpcBehaviorPolicy,
} from "@matrix-oasis/npc-behavior-runtime";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  createR20Coordinator,
  exportR20Coordinator,
  handleR20CoordinatorRequest,
} from "../scripts/lib/r20-host-core.mjs";

const sha = (character) => `sha256:${character.repeat(64)}`;
const authoring = await readFile(
  new URL("../examples/mechanics-conformance.authoring-game-pack.json", import.meta.url),
  "utf8",
);
const compiled = await compileAuthoringGamePackJson(authoring);
assert.equal(compiled.ok, true);
const runtimeGamePackJson = compiled.canonicalJson;
const runtimeReceiptJson = canonicalizeJsonValue(compiled.receipt);
const authorityPolicyJson = canonicalizeJsonValue({
  format: "matrix-oasis.npc-authority-policy",
  formatVersion: "0.1.0",
  canonicalization: "matrix-oasis.canonical-json/1",
  id: "selector-seam-policy",
  contentVersion: "1",
  runtime: {
    format: compiled.runtimePack.format,
    formatVersion: compiled.runtimePack.formatVersion,
    id: compiled.runtimePack.source.id,
    contentVersion: compiled.runtimePack.source.contentVersion,
    sourceSha256: `sha256:${compiled.runtimePack.source.canonicalSha256}`,
    artifactSha256: `sha256:${compiled.receipt.artifact.sha256}`,
    receiptSha256: hashCanonicalValue(compiled.receipt),
  },
  actorGrants: [{
    actorEntityId: "actor-unit",
    grants: [{ nodeId: "node-start", actionId: "action-initialize" }],
  }],
});
const behavior = synthesizeNpcBehaviorPolicy({ authorityPolicyJson });
assert.equal(behavior.ok, true);
const entityBindingJson = canonicalizeJsonValue({
  format: "matrix-oasis.npc-entity-binding",
  formatVersion: "0.1.0",
  canonicalization: "matrix-oasis.canonical-json/1",
  identities: {
    sceneBlueprintSha256: sha("a"),
    scenePackSha256: sha("b"),
    assetBundleSha256: sha("c"),
    spatialSolutionSha256: sha("d"),
    spatialVerificationSha256: sha("e"),
    authorityPolicySha256: behavior.npcBehaviorPolicy.authorityPolicySha256,
  },
  bindings: [{
    actorEntityId: "actor-unit",
    assetBriefId: "brief-one",
    placementId: "placement-one",
    runtimeEntityId: "actor-unit",
    homeFloorAnchorId: "floor-one",
    homePositionMm: { x: 0, y: 0, z: 0 },
    visibleNodeIds: ["node-start"],
  }],
});
const entityBindingSha256 = hashCanonicalValue(JSON.parse(entityBindingJson));
const prepared = prepareDeterministicNpcBehavior({
  behaviorPolicyJson: behavior.canonicalNpcBehaviorPolicyJson,
  entityBindingJson,
  authorityPolicyJson,
});
assert.equal(prepared.ok, true);
const token = "s".repeat(64);

function request(method, url, body = null) {
  return {
    remoteAddress: "127.0.0.1",
    method,
    url,
    headers: {
      authorization: `Bearer ${token}`,
      ...(method === "POST" ? { "content-type": "application/json" } : {}),
    },
    body: body === null ? undefined : canonicalizeJsonValue(body),
  };
}

async function coordinator(commandSelector) {
  const authority = await createNpcAuthoritySession({
    runtimeGamePackJson,
    runtimeReceiptJson,
    policyJson: authorityPolicyJson,
    timelineId: "timeline-selector-seam",
  });
  assert.equal(authority.ok, true);
  const value = createR20Coordinator({
    authoritySession: authority.session,
    preparedBehavior: prepared.prepared,
    initialBehaviorState: prepared.initialState,
    entityBindingSha256,
    sessionToken: token,
    ...(commandSelector ? { commandSelector } : {}),
  });
  assert.ok(value);
  return value;
}

test("the default selector seam remains byte-identical to the frozen R20 path", async () => {
  let calls = 0;
  const wrapped = (input) => {
    calls += 1;
    return selectNextNpcBehaviorCommand(input);
  };
  const original = await coordinator();
  const injected = await coordinator(wrapped);
  const originalCommand = handleR20CoordinatorRequest(original, request("GET", "/v1/command"));
  const injectedCommand = handleR20CoordinatorRequest(injected, request("GET", "/v1/command"));
  assert.deepEqual(injectedCommand, originalCommand);
  assert.equal(calls, 1);

  const command = JSON.parse(originalCommand.body).command;
  const arrival = {
    sequence: command.sequence,
    pathComplete: true,
    floorVerified: true,
    capsuleVerified: true,
    domainVerified: true,
    movementTicks: 1,
    pathLengthMm: 50,
  };
  const originalAdjudication = handleR20CoordinatorRequest(original, request("POST", "/v1/arrived", arrival));
  const injectedAdjudication = handleR20CoordinatorRequest(injected, request("POST", "/v1/arrived", arrival));
  assert.deepEqual(injectedAdjudication, originalAdjudication);
  const result = JSON.parse(originalAdjudication.body);
  const mirror = {
    sequence: command.sequence,
    beforeSnapshotSha256: result.beforeSnapshotSha256,
    afterSnapshotSha256: result.afterSnapshotSha256,
  };
  assert.deepEqual(
    handleR20CoordinatorRequest(injected, request("POST", "/v1/mirror", mirror)),
    handleR20CoordinatorRequest(original, request("POST", "/v1/mirror", mirror)),
  );
  assert.deepEqual(exportR20Coordinator(injected), exportR20Coordinator(original));
});

test("an injected selector can hold a clean R20 timeline without mutating it", async () => {
  let released = false;
  const selector = (input) => released
    ? selectNextNpcBehaviorCommand(input)
    : Object.freeze({ ok: true, status: "quiescent" });
  const value = await coordinator(selector);
  const before = exportR20Coordinator(value);
  assert.deepEqual(
    JSON.parse(handleR20CoordinatorRequest(value, request("GET", "/v1/command")).body),
    { status: "quiescent" },
  );
  assert.deepEqual(exportR20Coordinator(value), before);
  released = true;
  assert.equal(
    JSON.parse(handleR20CoordinatorRequest(value, request("GET", "/v1/command")).body).status,
    "command",
  );
});

test("an injected selector cannot replay a rule after its R20 execution limit", async () => {
  let captured = null;
  const selector = (input) => {
    if (captured === null) captured = selectNextNpcBehaviorCommand(input);
    return captured;
  };
  const value = await coordinator(selector);
  const commandResponse = handleR20CoordinatorRequest(value, request("GET", "/v1/command"));
  assert.equal(commandResponse.statusCode, 200);
  const command = JSON.parse(commandResponse.body).command;
  const arrived = handleR20CoordinatorRequest(value, request("POST", "/v1/arrived", {
    sequence: command.sequence,
    pathComplete: true,
    floorVerified: true,
    capsuleVerified: true,
    domainVerified: true,
    movementTicks: 1,
    pathLengthMm: 50,
  }));
  const adjudication = JSON.parse(arrived.body);
  assert.equal(handleR20CoordinatorRequest(value, request("POST", "/v1/mirror", {
    sequence: command.sequence,
    beforeSnapshotSha256: adjudication.beforeSnapshotSha256,
    afterSnapshotSha256: adjudication.afterSnapshotSha256,
  })).statusCode, 200);

  const stale = handleR20CoordinatorRequest(value, request("GET", "/v1/command"));
  assert.equal(stale.statusCode, 409);
  assert.deepEqual(JSON.parse(stale.body), { code: "R20_COMMAND_SELECTOR_INVALID" });
  assert.deepEqual(JSON.parse(handleR20CoordinatorRequest(value, request("GET", "/v1/command")).body), {
    code: "R20_TIMELINE_FROZEN",
  });
});

test("an injected selector cannot forge quiescence for R20 verification", async () => {
  const value = await coordinator(() => Object.freeze({ ok: true, status: "quiescent" }));
  const verified = handleR20CoordinatorRequest(value, request("POST", "/v1/verify", {}));
  assert.equal(verified.statusCode, 409);
  assert.deepEqual(JSON.parse(verified.body), { code: "R20_TIMELINE_NOT_TERMINAL" });
});

test("invalid selector injection is rejected before a coordinator exists", async () => {
  const authority = await createNpcAuthoritySession({
    runtimeGamePackJson,
    runtimeReceiptJson,
    policyJson: authorityPolicyJson,
    timelineId: "timeline-selector-invalid",
  });
  assert.equal(createR20Coordinator({
    authoritySession: authority.session,
    preparedBehavior: prepared.prepared,
    initialBehaviorState: prepared.initialState,
    entityBindingSha256,
    sessionToken: token,
    commandSelector: null,
  }), null);
});
