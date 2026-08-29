import assert from "node:assert/strict";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { NPC_AUTHORITY_LIMITS, validateDerivedProjectionManifestJson, validateWorldEventLedgerJson } from "@matrix-oasis/npc-authority-contracts";
import {
  appendWorldEventLedgerEntryCore,
  createDerivedProjectionManifest,
  createWorldEventLedgerCore,
  resolveWorldEventLedgerIntent,
} from "../src/index.mjs";
import { canonical, clone, intentFixture, policyFixture, sha, transitionFixture } from "../../npc-authority-contracts/tests/fixtures.mjs";

function emptyLedger() {
  return createWorldEventLedgerCore({
    policyJson: canonical(policyFixture()),
    timelineId: "timeline-one",
    stepLimit: 32,
    initialSnapshotSha256: sha("4"),
  });
}

function appendAccepted(worldEventLedgerJson, npcIntentJson = canonical(intentFixture())) {
  return appendWorldEventLedgerEntryCore({
    worldEventLedgerJson,
    npcIntentJson,
    decision: { status: "accepted", reason: "NPC_INTENT_ACCEPTED" },
    beforeSnapshotSha256: sha("4"),
    afterSnapshotSha256: sha("5"),
    transition: transitionFixture(),
  });
}

test("creates a canonical empty append-only ledger without mutating policy", () => {
  const policy = policyFixture();
  const before = clone(policy);
  const result = createWorldEventLedgerCore({ policyJson: canonical(policy), timelineId: "timeline-one", stepLimit: 32, initialSnapshotSha256: sha("4") });
  assert.equal(result.ok, true);
  assert.equal(validateWorldEventLedgerJson(result.canonicalWorldEventLedgerJson).valid, true);
  assert.equal(result.canonicalWorldEventLedgerJson, canonicalizeJsonValue(JSON.parse(result.canonicalWorldEventLedgerJson)));
  assert.deepEqual(policy, before);
  assert.equal(Object.isFrozen(result), true);
});

test("public Ledger operations reject absent arguments without leaking argument exceptions", () => {
  for (const operation of [createWorldEventLedgerCore, resolveWorldEventLedgerIntent, appendWorldEventLedgerEntryCore, createDerivedProjectionManifest]) {
    const result = operation();
    assert.equal(result.ok, false);
    assert.equal(typeof result.diagnostics[0].code, "string");
  }
});

test("appends accepted and rejected entries with a continuous hash and snapshot chain", () => {
  const created = emptyLedger();
  const accepted = appendAccepted(created.canonicalWorldEventLedgerJson);
  assert.equal(accepted.ok, true);
  assert.equal(accepted.kind, "appended");
  const acceptedLedger = JSON.parse(accepted.canonicalWorldEventLedgerJson);
  const rejectedIntent = intentFixture({
    id: "intent-two",
    observed: { revision: 1, headSha256: acceptedLedger.headSha256, runtimeSnapshotSha256: sha("5") },
  });
  const rejected = appendWorldEventLedgerEntryCore({
    worldEventLedgerJson: accepted.canonicalWorldEventLedgerJson,
    npcIntentJson: canonical(rejectedIntent),
    decision: { status: "rejected", reason: "NPC_INTENT_ACTOR_UNAUTHORIZED" },
    beforeSnapshotSha256: sha("5"),
    afterSnapshotSha256: sha("5"),
    transition: null,
  });
  assert.equal(rejected.ok, true);
  const ledger = JSON.parse(rejected.canonicalWorldEventLedgerJson);
  assert.equal(ledger.revision, 2);
  assert.equal(ledger.entries[1].previousEntrySha256, ledger.entries[0].entrySha256);
  assert.equal(validateWorldEventLedgerJson(rejected.canonicalWorldEventLedgerJson).valid, true);
});

test("CAS rejects stale revision, head and snapshot without returning a candidate ledger", () => {
  const created = emptyLedger();
  const cases = [
    ["NPC_INTENT_STALE_REVISION", { revision: 1, headSha256: null, runtimeSnapshotSha256: sha("4") }],
    ["NPC_INTENT_STALE_HEAD", { revision: 0, headSha256: sha("9"), runtimeSnapshotSha256: sha("4") }],
    ["NPC_INTENT_STALE_SNAPSHOT", { revision: 0, headSha256: null, runtimeSnapshotSha256: sha("9") }],
  ];
  for (const [code, observed] of cases) {
    const result = appendAccepted(created.canonicalWorldEventLedgerJson, canonical(intentFixture({ observed })));
    assert.equal(result.ok, false);
    assert.equal(result.diagnostics[0].code, code);
    assert.equal("canonicalWorldEventLedgerJson" in result, false);
  }
});

test("exact duplicate resolves to replay and id collision fails closed", () => {
  const appended = appendAccepted(emptyLedger().canonicalWorldEventLedgerJson);
  const duplicate = resolveWorldEventLedgerIntent({ worldEventLedgerJson: appended.canonicalWorldEventLedgerJson, npcIntentJson: canonical(intentFixture()) });
  assert.equal(duplicate.ok, true);
  assert.equal(duplicate.kind, "replay");
  const replayAppend = appendAccepted(appended.canonicalWorldEventLedgerJson);
  assert.equal(replayAppend.kind, "replay");
  assert.equal("canonicalWorldEventLedgerJson" in replayAppend, false);
  const collision = resolveWorldEventLedgerIntent({ worldEventLedgerJson: appended.canonicalWorldEventLedgerJson, npcIntentJson: canonical(intentFixture({ actionId: "different-action" })) });
  assert.equal(collision.ok, false);
  assert.equal(collision.diagnostics[0].code, "NPC_INTENT_ID_COLLISION");
});

test("invalid append material is rejected by the closed ledger contract", () => {
  const result = appendWorldEventLedgerEntryCore({
    worldEventLedgerJson: emptyLedger().canonicalWorldEventLedgerJson,
    npcIntentJson: canonical(intentFixture()),
    decision: { status: "accepted", reason: "NPC_INTENT_ACCEPTED" },
    beforeSnapshotSha256: sha("4"),
    afterSnapshotSha256: sha("5"),
    transition: null,
  });
  assert.equal(result.ok, false);
  assert(result.diagnostics.some((value) => value.code === "NPC_AUTHORITY_ACCEPTED_TRANSITION_REQUIRED"));
});

test("projection manifests contain only artifact identity and bind the exact ledger head", () => {
  const appended = appendAccepted(emptyLedger().canonicalWorldEventLedgerJson);
  const request = {
    worldEventLedgerJson: appended.canonicalWorldEventLedgerJson,
    projectionKind: "memory",
    reducer: { id: "memory-reducer", version: "1.0.0", sourceSha256: sha("6") },
    scopeEntityIds: ["entity-z", "actor-one"],
    artifact: { format: "application.json", bytes: "{}" },
  };
  const result = createDerivedProjectionManifest(request);
  assert.equal(result.ok, true);
  assert.equal(validateDerivedProjectionManifestJson(result.canonicalDerivedProjectionManifestJson).valid, true);
  const manifest = JSON.parse(result.canonicalDerivedProjectionManifestJson);
  assert.deepEqual(manifest.scopeEntityIds, ["actor-one", "entity-z"]);
  assert.equal(manifest.ledger.throughHeadSha256, JSON.parse(appended.canonicalWorldEventLedgerJson).headSha256);
  assert.deepEqual(Object.keys(manifest.artifact), ["byteLength", "format", "sha256"]);
  assert.equal(result.canonicalDerivedProjectionManifestJson.includes("{}"), false);
});

test("projection identity changes on reducer, artifact or ledger drift", () => {
  const firstLedger = emptyLedger().canonicalWorldEventLedgerJson;
  const secondLedger = appendAccepted(firstLedger).canonicalWorldEventLedgerJson;
  const create = (ledger, source, bytes) => createDerivedProjectionManifest({
    worldEventLedgerJson: ledger,
    projectionKind: "relationship",
    reducer: { id: "relationship-reducer", version: "1.0.0", sourceSha256: source },
    scopeEntityIds: ["actor-one"],
    artifact: { format: "application.json", bytes },
  }).canonicalDerivedProjectionManifestJson;
  const baseline = create(firstLedger, sha("6"), "{}");
  assert.notEqual(create(secondLedger, sha("6"), "{}"), baseline);
  assert.notEqual(create(firstLedger, sha("7"), "{}"), baseline);
  assert.notEqual(create(firstLedger, sha("6"), "{\"changed\":true}"), baseline);
});

test("hostile projection byte accessors fail with the static operational error", () => {
  const hostileArtifact = new Proxy({}, {
    get(_target, property) {
      if (property === "bytes") throw new Error("secret-projection-path");
      return undefined;
    },
  });
  assert.throws(
    () => createDerivedProjectionManifest({
      worldEventLedgerJson: emptyLedger().canonicalWorldEventLedgerJson,
      projectionKind: "memory",
      reducer: { id: "memory-reducer", version: "1.0.0", sourceSha256: sha("6") },
      scopeEntityIds: ["actor-one"],
      artifact: hostileArtifact,
    }),
    (error) => error?.code === "NPC_AUTHORITY_INTERNAL_ERROR" && error.message === "NPC_AUTHORITY_INTERNAL_ERROR" && !String(error.stack).includes("secret-projection-path"),
  );
});

test("projection artifact bytes are rejected before an oversized copy or hash", () => {
  const bytes = new Uint8Array(NPC_AUTHORITY_LIMITS.projectionArtifactBytes + 1);
  const result = createDerivedProjectionManifest({
    worldEventLedgerJson: emptyLedger().canonicalWorldEventLedgerJson,
    projectionKind: "memory",
    reducer: { id: "memory-reducer", version: "1.0.0", sourceSha256: sha("6") },
    scopeEntityIds: ["actor-one"],
    artifact: { format: "application.json", bytes },
  });
  assert.equal(result.ok, false);
  assert.equal(result.diagnostics[0].code, "DERIVED_PROJECTION_ARTIFACT_SIZE_EXCEEDED");
  assert.equal("canonicalDerivedProjectionManifestJson" in result, false);
});

test("ledger and projection operations are byte deterministic for 20 runs", () => {
  const ledger = emptyLedger().canonicalWorldEventLedgerJson;
  const expectedAppend = canonicalizeJsonValue(appendAccepted(ledger));
  const projectionRequest = {
    worldEventLedgerJson: ledger,
    projectionKind: "memory",
    reducer: { id: "memory-reducer", version: "1.0.0", sourceSha256: sha("6") },
    scopeEntityIds: ["actor-one"],
    artifact: { format: "application.json", bytes: new Uint8Array([123, 125]) },
  };
  const expectedProjection = canonicalizeJsonValue(createDerivedProjectionManifest(projectionRequest));
  for (let index = 0; index < 20; index += 1) {
    assert.equal(canonicalizeJsonValue(appendAccepted(ledger)), expectedAppend);
    assert.equal(canonicalizeJsonValue(createDerivedProjectionManifest(projectionRequest)), expectedProjection);
  }
});
