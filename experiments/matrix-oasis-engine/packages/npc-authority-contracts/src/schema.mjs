export const NPC_AUTHORITY_FORMAT_VERSION = "0.1.0";
export const NPC_AUTHORITY_CANONICALIZATION = "matrix-oasis.canonical-json/1";
export const NPC_AUTHORITY_POLICY_FORMAT = "matrix-oasis.npc-authority-policy";
export const NPC_INTENT_FORMAT = "matrix-oasis.npc-intent";
export const NPC_ADJUDICATION_RESULT_FORMAT = "matrix-oasis.npc-adjudication-result";
export const WORLD_EVENT_LEDGER_FORMAT = "matrix-oasis.world-event-ledger";
export const DERIVED_PROJECTION_MANIFEST_FORMAT = "matrix-oasis.derived-projection-manifest";
export const WORLD_EVENT_LEDGER_REPLAY_REPORT_FORMAT = "matrix-oasis.world-event-ledger-replay-report";
const JSON_SCHEMA_2020_12 = ["https:", "", "json-schema.org", "draft", "2020-12", "schema"].join("/");

export const NPC_AUTHORITY_LIMITS = Object.freeze({
  documentDepth: 256,
  policyBytes: 1024 * 1024,
  intentBytes: 64 * 1024,
  ledgerBytes: 16 * 1024 * 1024,
  resultBytes: 1024 * 1024,
  projectionBytes: 1024 * 1024,
  projectionArtifactBytes: 16 * 1024 * 1024,
  replayReportBytes: 1024 * 1024,
  actors: 64,
  grantsPerActor: 256,
  ledgerEntries: 10_000,
});

const SAFE = Number.MAX_SAFE_INTEGER;
const id = { type: "string", minLength: 1, maxLength: 96, pattern: "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$" };
const version = { type: "string", minLength: 1, maxLength: 64, pattern: "\\S" };
const sha256 = { type: "string", pattern: "^sha256:[0-9a-f]{64}$" };
const index = { type: "integer", minimum: 0, maximum: SAFE };
const ledgerIndex = { type: "integer", minimum: 0, maximum: NPC_AUTHORITY_LIMITS.ledgerEntries };
const positive = { type: "integer", minimum: 1, maximum: SAFE };

const runtimeIdentity = {
  type: "object", additionalProperties: false,
  required: ["format", "formatVersion", "id", "contentVersion", "sourceSha256", "artifactSha256", "receiptSha256"],
  properties: {
    format: { const: "matrix-oasis.runtime-game-pack" },
    formatVersion: { const: "0.1.0" }, id, contentVersion: version,
    sourceSha256: sha256, artifactSha256: sha256, receiptSha256: sha256,
  },
};
const observed = {
  type: "object", additionalProperties: false,
  required: ["revision", "headSha256", "runtimeSnapshotSha256"],
  properties: { revision: ledgerIndex, headSha256: { oneOf: [{ type: "null" }, sha256] }, runtimeSnapshotSha256: sha256 },
};
const intent = {
  type: "object", additionalProperties: false,
  required: ["format", "formatVersion", "canonicalization", "id", "actorEntityId", "timelineId", "nodeId", "actionId", "observed"],
  properties: {
    format: { const: NPC_INTENT_FORMAT }, formatVersion: { const: NPC_AUTHORITY_FORMAT_VERSION },
    canonicalization: { const: NPC_AUTHORITY_CANONICALIZATION }, id,
    actorEntityId: id, timelineId: id, nodeId: id, actionId: id, observed,
  },
};
const cue = {
  type: "object", additionalProperties: false,
  required: ["id", "channel", "intent"],
  properties: { id, channel: { enum: ["visual", "audio", "ui"] }, intent: { type: "string", minLength: 1, maxLength: 4096, pattern: "\\S" } },
};
const location = {
  type: "object", additionalProperties: false,
  required: ["kind", "index", "id"],
  properties: { kind: { enum: ["node", "ending"] }, index, id },
};
const transition = {
  type: "object", additionalProperties: false,
  required: ["transitionVersion", "step", "from", "actionId", "to", "emittedCues"],
  properties: {
    transitionVersion: { const: 1 }, step: positive,
    from: { type: "object", additionalProperties: false, required: ["kind", "index", "id"], properties: { kind: { const: "node" }, index, id } },
    actionId: id, to: location,
    emittedCues: { type: "array", maxItems: 256, items: cue },
  },
};
const rejectionReason = {
  enum: [
    "NPC_INTENT_ACTOR_NOT_FOUND", "NPC_INTENT_ACTOR_UNAUTHORIZED",
    "NPC_INTENT_NODE_NOT_FOUND", "NPC_INTENT_ACTION_NOT_FOUND",
    "NPC_INTENT_NODE_MISMATCH", "NPC_INTENT_ACTION_UNAVAILABLE",
    "NPC_INTENT_SESSION_ENDED", "NPC_INTENT_STEP_LIMIT",
    "NPC_INTENT_INTEGER_OVERFLOW",
  ],
};
const decision = {
  oneOf: [
    { type: "object", additionalProperties: false, required: ["status", "reason"], properties: { status: { const: "accepted" }, reason: { const: "NPC_INTENT_ACCEPTED" } } },
    { type: "object", additionalProperties: false, required: ["status", "reason"], properties: { status: { const: "rejected" }, reason: rejectionReason } },
  ],
};
const ledgerEntry = {
  type: "object", additionalProperties: false,
  required: ["revision", "intent", "decision", "beforeSnapshotSha256", "afterSnapshotSha256", "transition", "previousEntrySha256", "entrySha256"],
  properties: {
    revision: positive, intent, decision,
    beforeSnapshotSha256: sha256, afterSnapshotSha256: sha256,
    transition: { oneOf: [{ type: "null" }, transition] },
    previousEntrySha256: { oneOf: [{ type: "null" }, sha256] }, entrySha256: sha256,
  },
};

function documentSchema(idValue, format, required, properties) {
  return {
    $schema: JSON_SCHEMA_2020_12, $id: idValue,
    type: "object", additionalProperties: false,
    required: ["format", "formatVersion", "canonicalization", ...required],
    properties: {
      format: { const: format }, formatVersion: { const: NPC_AUTHORITY_FORMAT_VERSION },
      canonicalization: { const: NPC_AUTHORITY_CANONICALIZATION }, ...properties,
    },
  };
}

export const NPC_AUTHORITY_POLICY_SCHEMA = documentSchema(
  "urn:matrix-oasis:npc-authority-policy:0.1.0", NPC_AUTHORITY_POLICY_FORMAT,
  ["id", "contentVersion", "runtime", "actorGrants"], {
    id, contentVersion: version, runtime: runtimeIdentity,
    actorGrants: { type: "array", maxItems: NPC_AUTHORITY_LIMITS.actors, items: {
      type: "object", additionalProperties: false, required: ["actorEntityId", "grants"],
      properties: { actorEntityId: id, grants: { type: "array", minItems: 1, maxItems: NPC_AUTHORITY_LIMITS.grantsPerActor, items: {
        type: "object", additionalProperties: false, required: ["nodeId", "actionId"], properties: { nodeId: id, actionId: id },
      } } },
    } },
  },
);
export const NPC_INTENT_SCHEMA = {
  ...intent, $schema: JSON_SCHEMA_2020_12, $id: "urn:matrix-oasis:npc-intent:0.1.0",
};
export const NPC_ADJUDICATION_RESULT_SCHEMA = documentSchema(
  "urn:matrix-oasis:npc-adjudication-result:0.1.0", NPC_ADJUDICATION_RESULT_FORMAT,
  ["timelineId", "intentId", "replayed", "revision", "headSha256", "decision", "beforeSnapshotSha256", "afterSnapshotSha256", "transition"], {
    timelineId: id, intentId: id, replayed: { type: "boolean" }, revision: ledgerIndex,
    headSha256: { oneOf: [{ type: "null" }, sha256] }, decision,
    beforeSnapshotSha256: sha256, afterSnapshotSha256: sha256,
    transition: { oneOf: [{ type: "null" }, transition] },
  },
);
export const WORLD_EVENT_LEDGER_SCHEMA = documentSchema(
  "urn:matrix-oasis:world-event-ledger:0.1.0", WORLD_EVENT_LEDGER_FORMAT,
  ["timeline", "authority", "revision", "headSha256", "entries"], {
    timeline: { type: "object", additionalProperties: false, required: ["id", "stepLimit"], properties: { id, stepLimit: { type: "integer", minimum: 1, maximum: NPC_AUTHORITY_LIMITS.ledgerEntries } } },
    authority: { type: "object", additionalProperties: false, required: ["runtime", "policy", "initialSnapshotSha256"], properties: {
      runtime: runtimeIdentity,
      policy: { type: "object", additionalProperties: false, required: ["id", "contentVersion", "canonicalSha256"], properties: { id, contentVersion: version, canonicalSha256: sha256 } },
      initialSnapshotSha256: sha256,
    } },
    revision: ledgerIndex, headSha256: { oneOf: [{ type: "null" }, sha256] },
    entries: { type: "array", maxItems: NPC_AUTHORITY_LIMITS.ledgerEntries, items: ledgerEntry },
  },
);
export const DERIVED_PROJECTION_MANIFEST_SCHEMA = documentSchema(
  "urn:matrix-oasis:derived-projection-manifest:0.1.0", DERIVED_PROJECTION_MANIFEST_FORMAT,
  ["projectionKind", "reducer", "ledger", "scopeEntityIds", "artifact"], {
    projectionKind: { enum: ["memory", "relationship"] },
    reducer: { type: "object", additionalProperties: false, required: ["id", "version", "sourceSha256"], properties: { id, version, sourceSha256: sha256 } },
    ledger: { type: "object", additionalProperties: false, required: ["timelineId", "canonicalSha256", "throughRevision", "throughHeadSha256"], properties: {
      timelineId: id, canonicalSha256: sha256, throughRevision: ledgerIndex,
      throughHeadSha256: { oneOf: [{ type: "null" }, sha256] },
    } },
    scopeEntityIds: { type: "array", maxItems: 4096, uniqueItems: true, items: id },
    artifact: { type: "object", additionalProperties: false, required: ["format", "byteLength", "sha256"], properties: {
      format: { type: "string", minLength: 1, maxLength: 96, pattern: "^[a-z][a-z0-9.+-]*$" },
      byteLength: { type: "integer", minimum: 0, maximum: NPC_AUTHORITY_LIMITS.projectionArtifactBytes }, sha256,
    } },
  },
);
export const WORLD_EVENT_LEDGER_REPLAY_REPORT_SCHEMA = documentSchema(
  "urn:matrix-oasis:world-event-ledger-replay-report:0.1.0", WORLD_EVENT_LEDGER_REPLAY_REPORT_FORMAT,
  ["timelineId", "ledgerSha256", "throughRevision", "throughHeadSha256", "verifiedEntries", "acceptedEntries", "rejectedEntries", "finalSnapshotSha256", "finalInspectionSha256"], {
    timelineId: id, ledgerSha256: sha256, throughRevision: ledgerIndex,
    throughHeadSha256: { oneOf: [{ type: "null" }, sha256] },
    verifiedEntries: ledgerIndex, acceptedEntries: ledgerIndex, rejectedEntries: ledgerIndex,
    finalSnapshotSha256: sha256, finalInspectionSha256: sha256,
  },
);

function freezeTree(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) freezeTree(child);
  return Object.freeze(value);
}
for (const schema of [NPC_AUTHORITY_POLICY_SCHEMA, NPC_INTENT_SCHEMA, NPC_ADJUDICATION_RESULT_SCHEMA, WORLD_EVENT_LEDGER_SCHEMA, DERIVED_PROJECTION_MANIFEST_SCHEMA, WORLD_EVENT_LEDGER_REPLAY_REPORT_SCHEMA]) freezeTree(schema);
