import { createHash } from "node:crypto";
import {
  NPC_DERIVED_STATE_BUNDLE_FORMAT,
  NPC_DERIVED_STATE_CANONICALIZATION,
  NPC_DERIVED_STATE_FORMAT_VERSION,
  NPC_MEMORY_PROJECTION_FORMAT,
  NPC_PERSONA_SEED_FORMAT,
  NPC_RELATIONSHIP_PROJECTION_FORMAT,
  NPC_RELATIONSHIP_PROJECTION_POLICY_FORMAT,
  validateNpcDerivedStateBundleJson,
  validateNpcMemoryProjectionJson,
  validateNpcPersonaSeedJson,
  validateNpcRelationshipProjectionJson,
  validateNpcRelationshipProjectionPolicyJson,
} from "@matrix-oasis/npc-derived-state-contracts";
import {
  validateDerivedProjectionManifestJson,
  validateWorldEventLedgerReplayReportJson,
} from "@matrix-oasis/npc-authority-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const SHA256 = /^sha256:[0-9a-f]{64}$/u;
const UTF8 = new TextEncoder();
const MANIFEST_FORMAT = "matrix-oasis.derived-projection-manifest";

const PROFILE = Object.freeze({
  timelineMode: "single",
  authorityMode: "runtime-and-ledger-only",
  personaMode: "trusted-static-seed",
  memoryScope: "actor-self-accepted-actions",
  relationshipScope: "accepted-explicit-policy-rules",
  deletionMode: "whole-derived-state",
  selectiveForgetting: false,
  externalModelCalls: false,
  semanticRetrieval: false,
});

export class R21ProjectionBindingError extends Error {
  constructor(code = "R21_PROJECTION_BINDING_INVALID") {
    super(code);
    this.name = "R21ProjectionBindingError";
    this.code = code;
  }
}

function fail() {
  throw new R21ProjectionBindingError();
}

function exact(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) &&
    Reflect.ownKeys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function same(left, right) {
  return canonicalizeJsonValue(left) === canonicalizeJsonValue(right);
}

function digest(text) {
  return `sha256:${createHash("sha256").update(UTF8.encode(text)).digest("hex")}`;
}

function parseCanonical(text, validator) {
  if (typeof text !== "string") fail();
  const report = validator(text);
  if (!report || report.valid !== true || report.diagnostics?.length !== 0) fail();
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    fail();
  }
  if (canonicalizeJsonValue(value) !== text) fail();
  return value;
}

function artifact(format, text) {
  return Object.freeze({
    format,
    canonicalSha256: digest(text),
    byteLength: UTF8.encode(text).byteLength,
  });
}

function assertSourceIdentity(value) {
  const keys = [
    "r20CurrentSha256",
    "r20AuthorityManifestSha256",
    "r20QualificationReceiptSha256",
    "npcEntityBindingSha256",
  ];
  if (!exact(value, keys) || keys.some((key) => !SHA256.test(value[key] ?? ""))) fail();
}

function assertLedgerIdentity(replay, memory, relationship, memoryManifest, relationshipManifest) {
  const expected = memory.ledger;
  if (!same(relationship.ledger, expected) ||
      !same(memoryManifest.ledger, expected) ||
      !same(relationshipManifest.ledger, expected) ||
      replay.timelineId !== expected.timelineId ||
      replay.ledgerSha256 !== expected.canonicalSha256 ||
      replay.throughRevision !== expected.throughRevision ||
      replay.throughHeadSha256 !== expected.throughHeadSha256 ||
      replay.verifiedEntries !== expected.throughRevision ||
      replay.acceptedEntries + replay.rejectedEntries !== replay.verifiedEntries) fail();
}

function assertManifest(manifest, {
  kind,
  reducer,
  scope,
  artifactFormat,
  artifactText,
}) {
  if (manifest.projectionKind !== kind ||
      !same(manifest.reducer, reducer) ||
      !same(manifest.scopeEntityIds, scope) ||
      manifest.artifact.format !== artifactFormat ||
      manifest.artifact.byteLength !== UTF8.encode(artifactText).byteLength ||
      manifest.artifact.sha256 !== digest(artifactText)) fail();
}

function sortedUnique(values) {
  return [...new Set(values)].sort((left, right) => left < right ? -1 : left > right ? 1 : 0);
}

export function bindNpcDerivedStateSource(input) {
  try {
    const {
      projected,
      sourceIdentity,
      personaSeedJson,
      relationshipPolicyJson,
    } = input ?? {};
    const projectedKeys = [
      "ok",
      "canonicalWorldEventLedgerReplayReportJson",
      "canonicalNpcMemoryProjectionJson",
      "canonicalNpcRelationshipProjectionJson",
      "canonicalMemoryDerivedProjectionManifestJson",
      "canonicalRelationshipDerivedProjectionManifestJson",
    ];
    if (!exact(projected, projectedKeys) || projected.ok !== true) fail();
    assertSourceIdentity(sourceIdentity);

    const persona = parseCanonical(personaSeedJson, validateNpcPersonaSeedJson);
    const policy = parseCanonical(relationshipPolicyJson, validateNpcRelationshipProjectionPolicyJson);
    const replay = parseCanonical(
      projected.canonicalWorldEventLedgerReplayReportJson,
      validateWorldEventLedgerReplayReportJson,
    );
    const memory = parseCanonical(
      projected.canonicalNpcMemoryProjectionJson,
      validateNpcMemoryProjectionJson,
    );
    const relationship = parseCanonical(
      projected.canonicalNpcRelationshipProjectionJson,
      validateNpcRelationshipProjectionJson,
    );
    const memoryManifest = parseCanonical(
      projected.canonicalMemoryDerivedProjectionManifestJson,
      validateDerivedProjectionManifestJson,
    );
    const relationshipManifest = parseCanonical(
      projected.canonicalRelationshipDerivedProjectionManifestJson,
      validateDerivedProjectionManifestJson,
    );

    const personaSha256 = digest(personaSeedJson);
    const relationshipPolicySha256 = digest(relationshipPolicyJson);
    const actorIds = persona.actors.map((actor) => actor.actorEntityId);
    if (!same(persona.authority, policy.authority) ||
        !same(persona.authority, memory.authority) ||
        !same(persona.authority, relationship.authority) ||
        persona.authority.npcEntityBindingSha256 !== sourceIdentity.npcEntityBindingSha256 ||
        policy.personaSeedSha256 !== personaSha256 ||
        memory.personaSeedSha256 !== personaSha256 ||
        relationship.personaSeedSha256 !== personaSha256 ||
        relationship.relationshipPolicySha256 !== relationshipPolicySha256 ||
        !same(memory.scopeActorEntityIds, actorIds) ||
        !same(relationship.scopeActorEntityIds, actorIds)) fail();

    assertLedgerIdentity(replay, memory, relationship, memoryManifest, relationshipManifest);
    const memoryManifestScope = sortedUnique([
      ...memory.scopeActorEntityIds,
      ...memory.episodes.flatMap((episode) => episode.interactionEntityIds),
    ]);
    const relationshipManifestScope = sortedUnique([
      ...relationship.scopeActorEntityIds,
      ...relationship.relationships.map((edge) => edge.targetEntityId),
    ]);
    assertManifest(memoryManifest, {
      kind: "memory",
      reducer: memory.reducer,
      scope: memoryManifestScope,
      artifactFormat: NPC_MEMORY_PROJECTION_FORMAT,
      artifactText: projected.canonicalNpcMemoryProjectionJson,
    });
    assertManifest(relationshipManifest, {
      kind: "relationship",
      reducer: relationship.reducer,
      scope: relationshipManifestScope,
      artifactFormat: NPC_RELATIONSHIP_PROJECTION_FORMAT,
      artifactText: projected.canonicalNpcRelationshipProjectionJson,
    });

    const bundle = {
      format: NPC_DERIVED_STATE_BUNDLE_FORMAT,
      formatVersion: NPC_DERIVED_STATE_FORMAT_VERSION,
      canonicalization: NPC_DERIVED_STATE_CANONICALIZATION,
      source: { ...sourceIdentity },
      authority: persona.authority,
      ledger: memory.ledger,
      replay: {
        reportSha256: digest(projected.canonicalWorldEventLedgerReplayReportJson),
        finalSnapshotSha256: replay.finalSnapshotSha256,
        finalInspectionSha256: replay.finalInspectionSha256,
      },
      reducers: {
        memory: memory.reducer,
        relationship: relationship.reducer,
      },
      profile: PROFILE,
      artifacts: {
        personaSeed: artifact(NPC_PERSONA_SEED_FORMAT, personaSeedJson),
        relationshipPolicy: artifact(NPC_RELATIONSHIP_PROJECTION_POLICY_FORMAT, relationshipPolicyJson),
        memoryProjection: artifact(NPC_MEMORY_PROJECTION_FORMAT, projected.canonicalNpcMemoryProjectionJson),
        relationshipProjection: artifact(NPC_RELATIONSHIP_PROJECTION_FORMAT, projected.canonicalNpcRelationshipProjectionJson),
        memoryManifest: artifact(MANIFEST_FORMAT, projected.canonicalMemoryDerivedProjectionManifestJson),
        relationshipManifest: artifact(MANIFEST_FORMAT, projected.canonicalRelationshipDerivedProjectionManifestJson),
      },
    };
    const canonicalNpcDerivedStateBundleJson = canonicalizeJsonValue(bundle);
    const report = validateNpcDerivedStateBundleJson(canonicalNpcDerivedStateBundleJson);
    if (!report.valid || report.diagnostics.length !== 0) fail();
    return Object.freeze({ ok: true, canonicalNpcDerivedStateBundleJson });
  } catch (error) {
    if (error instanceof R21ProjectionBindingError) throw error;
    throw new R21ProjectionBindingError();
  }
}
