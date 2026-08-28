import { createHash } from "node:crypto";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

export const sha = (character) => `sha256:${character.repeat(64)}`;
export const canonical = (value) => canonicalizeJsonValue(value);
export const hashCanonical = (value) => `sha256:${createHash("sha256").update(canonical(value), "utf8").digest("hex")}`;
export const clone = (value) => JSON.parse(JSON.stringify(value));

export const runtimeIdentity = Object.freeze({
  format: "matrix-oasis.runtime-game-pack",
  formatVersion: "0.1.0",
  id: "neutral-runtime",
  contentVersion: "1.0.0",
  sourceSha256: sha("1"),
  artifactSha256: sha("2"),
  receiptSha256: sha("3"),
});

export function policyFixture() {
  return {
    format: "matrix-oasis.npc-authority-policy",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: "neutral-authority",
    contentVersion: "1.0.0",
    runtime: { ...runtimeIdentity },
    actorGrants: [{ actorEntityId: "actor-one", grants: [{ nodeId: "entry-node", actionId: "inspect" }] }],
  };
}

export function intentFixture(overrides = {}) {
  return {
    format: "matrix-oasis.npc-intent",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    id: "intent-one",
    actorEntityId: "actor-one",
    timelineId: "timeline-one",
    nodeId: "entry-node",
    actionId: "inspect",
    observed: { revision: 0, headSha256: null, runtimeSnapshotSha256: sha("4") },
    ...overrides,
  };
}

export function transitionFixture() {
  return {
    transitionVersion: 1,
    step: 1,
    from: { kind: "node", index: 0, id: "entry-node" },
    actionId: "inspect",
    to: { kind: "node", index: 1, id: "loop-node" },
    emittedCues: [{ id: "seen", channel: "visual", intent: "Show the neutral result." }],
  };
}

export function acceptedEntryFixture() {
  const body = {
    revision: 1,
    intent: intentFixture(),
    decision: { status: "accepted", reason: "NPC_INTENT_ACCEPTED" },
    beforeSnapshotSha256: sha("4"),
    afterSnapshotSha256: sha("5"),
    transition: transitionFixture(),
    previousEntrySha256: null,
  };
  return { ...body, entrySha256: hashCanonical(body) };
}

export function ledgerFixture() {
  const entry = acceptedEntryFixture();
  return {
    format: "matrix-oasis.world-event-ledger",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    timeline: { id: "timeline-one", stepLimit: 32 },
    authority: {
      runtime: { ...runtimeIdentity },
      policy: { id: "neutral-authority", contentVersion: "1.0.0", canonicalSha256: sha("6") },
      initialSnapshotSha256: sha("4"),
    },
    revision: 1,
    headSha256: entry.entrySha256,
    entries: [entry],
  };
}

export function emptyLedgerFixture() {
  const ledger = ledgerFixture();
  ledger.revision = 0;
  ledger.headSha256 = null;
  ledger.entries = [];
  return ledger;
}

export function resultFixture() {
  const ledger = ledgerFixture();
  return {
    format: "matrix-oasis.npc-adjudication-result",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    timelineId: "timeline-one",
    intentId: "intent-one",
    replayed: false,
    revision: 1,
    headSha256: ledger.headSha256,
    decision: { status: "accepted", reason: "NPC_INTENT_ACCEPTED" },
    beforeSnapshotSha256: sha("4"),
    afterSnapshotSha256: sha("5"),
    transition: transitionFixture(),
  };
}

export function projectionFixture() {
  const ledger = ledgerFixture();
  return {
    format: "matrix-oasis.derived-projection-manifest",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    projectionKind: "memory",
    reducer: { id: "neutral-reducer", version: "1.0.0", sourceSha256: sha("7") },
    ledger: { timelineId: "timeline-one", canonicalSha256: hashCanonical(ledger), throughRevision: 1, throughHeadSha256: ledger.headSha256 },
    scopeEntityIds: ["actor-one"],
    artifact: { format: "application.json", byteLength: 2, sha256: sha("8") },
  };
}

export function replayFixture() {
  const ledger = ledgerFixture();
  return {
    format: "matrix-oasis.world-event-ledger-replay-report",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    timelineId: "timeline-one",
    ledgerSha256: hashCanonical(ledger),
    throughRevision: 1,
    throughHeadSha256: ledger.headSha256,
    verifiedEntries: 1,
    acceptedEntries: 1,
    rejectedEntries: 0,
    finalSnapshotSha256: sha("5"),
    finalInspectionSha256: sha("9"),
  };
}
