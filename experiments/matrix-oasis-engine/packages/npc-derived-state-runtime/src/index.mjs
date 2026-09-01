import {
  validateDerivedProjectionManifestJson,
  validateNpcAuthorityPolicyJson,
} from "@matrix-oasis/npc-authority-contracts";
import {
  createDerivedProjectionManifest,
  hashCanonicalValue,
  prepareNpcAuthority,
  replayWorldEventLedger,
} from "@matrix-oasis/npc-authority-runtime";
import { validateNpcEntityBindingJson } from "@matrix-oasis/npc-behavior-contracts";
import {
  NPC_DERIVED_STATE_CANONICALIZATION,
  NPC_DERIVED_STATE_FORMAT_VERSION,
  NPC_DERIVED_STATE_BUNDLE_FORMAT,
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
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  createMemoryReducerState,
  finishMemoryReducerState,
  reduceMemoryLedgerEntry,
} from "./memory-reducer.mjs";
import {
  createRelationshipReducerState,
  finishRelationshipReducerState,
  reduceRelationshipLedgerEntry,
} from "./relationship-reducer.mjs";
import { NPC_DERIVED_STATE_PROFILE, NPC_DERIVED_STATE_REDUCERS } from "./reducer-registry.mjs";

const INTERNAL_CODE = "NPC_DERIVED_STATE_INTERNAL_ERROR";
const preparedData = new WeakMap();

export class NpcDerivedStateRuntimeOperationalError extends Error {
  constructor() {
    super(INTERNAL_CODE);
    this.name = "NpcDerivedStateRuntimeOperationalError";
    this.code = INTERNAL_CODE;
  }
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function diagnostic(code, path = "") {
  return deepFreeze({ phase: "runtime", severity: "error", code, path, message: code });
}

function failure(code, path = "") {
  return deepFreeze({ ok: false, diagnostics: [diagnostic(code, path)] });
}

function validationFailure(report) {
  return deepFreeze({ ok: false, diagnostics: report.diagnostics });
}

function parseValidated(text, validator) {
  const report = validator(text);
  return report.valid ? { ok: true, value: JSON.parse(text) } : { ok: false, report };
}

function compareCanonical(left, right) {
  return canonicalizeJsonValue(left) === canonicalizeJsonValue(right);
}

function compareText(left, right) {
  return left < right ? -1 : left > right ? 1 : 0;
}

function hashDocument(text) {
  return hashCanonicalValue(JSON.parse(text));
}

function artifactReference(format, text) {
  return {
    format,
    canonicalSha256: hashDocument(text),
    byteLength: new TextEncoder().encode(text).byteLength,
  };
}

function authorityIdentity({ runtimeGamePackJson, runtimeReceiptJson, authorityPolicyJson, npcEntityBindingJson }) {
  return {
    runtimePackSha256: hashDocument(runtimeGamePackJson),
    runtimeReceiptSha256: hashDocument(runtimeReceiptJson),
    authorityPolicySha256: hashDocument(authorityPolicyJson),
    npcEntityBindingSha256: hashDocument(npcEntityBindingJson),
  };
}

function semanticPreparationDiagnostics({ pack, authorityPolicy, binding, personaSeed, relationshipPolicy, authority }) {
  const output = [];
  const add = (code, path) => output.push(diagnostic(code, path));
  if (!compareCanonical(personaSeed.authority, authority)) add("NPC_DERIVED_STATE_PERSONA_AUTHORITY_MISMATCH", "/personaSeed/authority");
  if (!compareCanonical(relationshipPolicy.authority, authority)) add("NPC_DERIVED_STATE_RELATIONSHIP_POLICY_AUTHORITY_MISMATCH", "/relationshipPolicy/authority");
  if (relationshipPolicy.personaSeedSha256 !== hashCanonicalValue(personaSeed)) add("NPC_DERIVED_STATE_RELATIONSHIP_POLICY_PERSONA_MISMATCH", "/relationshipPolicy/personaSeedSha256");
  if (binding.identities.authorityPolicySha256 !== authority.authorityPolicySha256) add("NPC_DERIVED_STATE_BINDING_AUTHORITY_MISMATCH", "/npcEntityBinding/identities/authorityPolicySha256");

  const entities = new Set(pack.entities.map((entity) => entity.id));
  const nodes = new Map(pack.nodes.map((node) => [node.id, node]));
  const grants = new Map(authorityPolicy.actorGrants.map((actor) => [
    actor.actorEntityId,
    new Set(actor.grants.map((grant) => `${grant.nodeId}\0${grant.actionId}`)),
  ]));
  const boundActors = binding.bindings.map((value) => value.actorEntityId).sort(compareText);
  const personaActors = personaSeed.actors.map((value) => value.actorEntityId);
  if (boundActors.join("\0") !== personaActors.join("\0")) add("NPC_DERIVED_STATE_PERSONA_ACTOR_SET_MISMATCH", "/personaSeed/actors");
  binding.bindings.forEach((value, index) => {
    if (value.actorEntityId !== value.runtimeEntityId) add("NPC_DERIVED_STATE_BINDING_RUNTIME_ACTOR_MISMATCH", `/npcEntityBinding/bindings/${index}/runtimeEntityId`);
    if (!entities.has(value.runtimeEntityId)) add("NPC_DERIVED_STATE_BINDING_RUNTIME_ENTITY_NOT_FOUND", `/npcEntityBinding/bindings/${index}/runtimeEntityId`);
    if (!grants.has(value.actorEntityId)) add("NPC_DERIVED_STATE_BINDING_ACTOR_UNAUTHORIZED", `/npcEntityBinding/bindings/${index}/actorEntityId`);
    value.visibleNodeIds.forEach((nodeId, nodeIndex) => {
      if (!nodes.has(nodeId)) add("NPC_DERIVED_STATE_BINDING_VISIBLE_NODE_NOT_FOUND", `/npcEntityBinding/bindings/${index}/visibleNodeIds/${nodeIndex}`);
    });
  });

  const edgeBounds = new Map();
  relationshipPolicy.rules.forEach((rule, index) => {
    if (!boundActors.includes(rule.sourceActorEntityId)) add("NPC_DERIVED_STATE_RELATIONSHIP_SOURCE_UNBOUND", `/relationshipPolicy/rules/${index}/sourceActorEntityId`);
    if (!entities.has(rule.targetEntityId)) add("NPC_DERIVED_STATE_RELATIONSHIP_TARGET_NOT_FOUND", `/relationshipPolicy/rules/${index}/targetEntityId`);
    const node = nodes.get(rule.nodeId);
    if (!node) add("NPC_DERIVED_STATE_RELATIONSHIP_NODE_NOT_FOUND", `/relationshipPolicy/rules/${index}/nodeId`);
    else if (!node.actions.some((action) => action.id === rule.actionId)) add("NPC_DERIVED_STATE_RELATIONSHIP_ACTION_NOT_FOUND", `/relationshipPolicy/rules/${index}/actionId`);
    if (!grants.get(rule.sourceActorEntityId)?.has(`${rule.nodeId}\0${rule.actionId}`)) add("NPC_DERIVED_STATE_RELATIONSHIP_ACTION_UNAUTHORIZED", `/relationshipPolicy/rules/${index}/actionId`);
    const key = `${rule.sourceActorEntityId}\0${rule.targetEntityId}\0${rule.dimensionId}`;
    const bounds = edgeBounds.get(key) ?? { positive: 0, negative: 0 };
    if (rule.delta > 0) bounds.positive += rule.delta;
    else bounds.negative += rule.delta;
    edgeBounds.set(key, bounds);
  });
  if ([...edgeBounds.values()].some((value) => value.positive > 1000 || value.negative < -1000)) {
    add("NPC_DERIVED_STATE_RELATIONSHIP_THEORETICAL_RANGE_EXCEEDED", "/relationshipPolicy/rules");
  }
  return output;
}

function captureProjection(value, validator) {
  const canonicalJson = canonicalizeJsonValue(value);
  const report = validator(canonicalJson);
  return report.valid ? { ok: true, canonicalJson } : validationFailure(report);
}

function memoryManifestScope(scopeActorEntityIds, episodes) {
  return [...new Set([
    ...scopeActorEntityIds,
    ...episodes.flatMap((episode) => episode.interactionEntityIds),
  ])].sort(compareText);
}

function relationshipManifestScope(scopeActorEntityIds, relationships) {
  return [...new Set([
    ...scopeActorEntityIds,
    ...relationships.map((edge) => edge.targetEntityId),
  ])].sort(compareText);
}

function dataFor(prepared) {
  if (!prepared || (typeof prepared !== "object" && typeof prepared !== "function")) return undefined;
  return preparedData.get(prepared);
}

export async function prepareNpcDerivedState(input) {
  try {
    const {
      runtimeGamePackJson,
      runtimeReceiptJson,
      authorityPolicyJson,
      npcEntityBindingJson,
      personaSeedJson,
      relationshipPolicyJson,
    } = input ?? {};
    const authorityPolicyResult = parseValidated(authorityPolicyJson, validateNpcAuthorityPolicyJson);
    if (!authorityPolicyResult.ok) return validationFailure(authorityPolicyResult.report);
    const bindingResult = parseValidated(npcEntityBindingJson, validateNpcEntityBindingJson);
    if (!bindingResult.ok) return validationFailure(bindingResult.report);
    const personaResult = parseValidated(personaSeedJson, validateNpcPersonaSeedJson);
    if (!personaResult.ok) return validationFailure(personaResult.report);
    const relationshipPolicyResult = parseValidated(relationshipPolicyJson, validateNpcRelationshipProjectionPolicyJson);
    if (!relationshipPolicyResult.ok) return validationFailure(relationshipPolicyResult.report);
    const authorityPrepared = await prepareNpcAuthority({
      runtimeGamePackJson,
      runtimeReceiptJson,
      policyJson: authorityPolicyJson,
    });
    if (!authorityPrepared.ok) return deepFreeze(authorityPrepared);
    const pack = JSON.parse(runtimeGamePackJson);
    const receipt = JSON.parse(runtimeReceiptJson);
    const authority = authorityIdentity({ runtimeGamePackJson, runtimeReceiptJson, authorityPolicyJson, npcEntityBindingJson });
    const semanticDiagnostics = semanticPreparationDiagnostics({
      pack,
      authorityPolicy: authorityPolicyResult.value,
      binding: bindingResult.value,
      personaSeed: personaResult.value,
      relationshipPolicy: relationshipPolicyResult.value,
      authority,
    });
    if (semanticDiagnostics.length) return deepFreeze({ ok: false, diagnostics: semanticDiagnostics });
    const scopeActorEntityIds = bindingResult.value.bindings.map((value) => value.actorEntityId).sort(compareText);
    const actionEntityIdsByKey = new Map();
    for (const node of pack.nodes) {
      for (const action of node.actions) {
        const entityIds = [...new Set(action.entityIndexes.map((entityIndex) => pack.entities[entityIndex]?.id).filter(Boolean))].sort(compareText);
        actionEntityIdsByKey.set(`${node.id}\0${action.id}`, Object.freeze(entityIds));
      }
    }
    const handle = Object.freeze(Object.create(null));
    preparedData.set(handle, Object.freeze({
      authorityPrepared: authorityPrepared.prepared,
      pack,
      receipt,
      authorityPolicy: authorityPolicyResult.value,
      binding: bindingResult.value,
      personaSeed: personaResult.value,
      relationshipPolicy: relationshipPolicyResult.value,
      authority,
      personaSeedJson,
      relationshipPolicyJson,
      personaSeedSha256: hashDocument(personaSeedJson),
      relationshipPolicySha256: hashDocument(relationshipPolicyJson),
      scopeActorEntityIds: Object.freeze(scopeActorEntityIds),
      actionEntityIdsByKey,
    }));
    return deepFreeze({ ok: true, prepared: handle });
  } catch (error) {
    if (error instanceof NpcDerivedStateRuntimeOperationalError) throw error;
    throw new NpcDerivedStateRuntimeOperationalError();
  }
}

export function projectNpcDerivedState(input) {
  try {
    const { prepared, worldEventLedgerJson } = input ?? {};
    const data = dataFor(prepared);
    if (!data) return failure("NPC_DERIVED_STATE_PREPARED_INVALID");
    const replayed = replayWorldEventLedger({ prepared: data.authorityPrepared, worldEventLedgerJson });
    if (!replayed.ok) return deepFreeze(replayed);
    const ledger = JSON.parse(worldEventLedgerJson);
    const replayReport = JSON.parse(replayed.canonicalWorldEventLedgerReplayReportJson);
    const ledgerIdentity = {
      timelineId: ledger.timeline.id,
      canonicalSha256: replayReport.ledgerSha256,
      throughRevision: ledger.revision,
      throughHeadSha256: ledger.headSha256,
    };
    const memoryState = createMemoryReducerState(data.scopeActorEntityIds);
    const relationshipState = createRelationshipReducerState(data.relationshipPolicy.rules);
    for (const entry of ledger.entries) {
      reduceMemoryLedgerEntry(memoryState, entry, data.actionEntityIdsByKey);
      reduceRelationshipLedgerEntry(relationshipState, entry);
    }
    const memory = finishMemoryReducerState(memoryState);
    const relationship = finishRelationshipReducerState(relationshipState);
    if (memory.scannedEntries !== ledger.entries.length || relationship.scannedEntries !== ledger.entries.length) {
      return failure("NPC_DERIVED_STATE_LEDGER_SCAN_MISMATCH");
    }
    const memoryProjection = {
      format: NPC_MEMORY_PROJECTION_FORMAT,
      formatVersion: NPC_DERIVED_STATE_FORMAT_VERSION,
      canonicalization: NPC_DERIVED_STATE_CANONICALIZATION,
      authority: data.authority,
      personaSeedSha256: data.personaSeedSha256,
      ledger: ledgerIdentity,
      reducer: NPC_DERIVED_STATE_REDUCERS.memory,
      scopeActorEntityIds: data.scopeActorEntityIds,
      episodes: memory.episodes,
    };
    const relationshipProjection = {
      format: NPC_RELATIONSHIP_PROJECTION_FORMAT,
      formatVersion: NPC_DERIVED_STATE_FORMAT_VERSION,
      canonicalization: NPC_DERIVED_STATE_CANONICALIZATION,
      authority: data.authority,
      personaSeedSha256: data.personaSeedSha256,
      relationshipPolicySha256: data.relationshipPolicySha256,
      ledger: ledgerIdentity,
      reducer: NPC_DERIVED_STATE_REDUCERS.relationship,
      scopeActorEntityIds: data.scopeActorEntityIds,
      relationships: relationship.relationships,
    };
    const memoryCaptured = captureProjection(memoryProjection, validateNpcMemoryProjectionJson);
    if (!memoryCaptured.ok) return memoryCaptured;
    const relationshipCaptured = captureProjection(relationshipProjection, validateNpcRelationshipProjectionJson);
    if (!relationshipCaptured.ok) return relationshipCaptured;
    const memoryScopeEntityIds = memoryManifestScope(data.scopeActorEntityIds, memory.episodes);
    const relationshipScopeEntityIds = relationshipManifestScope(data.scopeActorEntityIds, relationship.relationships);
    const memoryManifest = createDerivedProjectionManifest({
      worldEventLedgerJson,
      projectionKind: "memory",
      reducer: NPC_DERIVED_STATE_REDUCERS.memory,
      scopeEntityIds: memoryScopeEntityIds,
      artifact: { format: NPC_MEMORY_PROJECTION_FORMAT, bytes: memoryCaptured.canonicalJson },
    });
    if (!memoryManifest.ok) return deepFreeze(memoryManifest);
    const relationshipManifest = createDerivedProjectionManifest({
      worldEventLedgerJson,
      projectionKind: "relationship",
      reducer: NPC_DERIVED_STATE_REDUCERS.relationship,
      scopeEntityIds: relationshipScopeEntityIds,
      artifact: { format: NPC_RELATIONSHIP_PROJECTION_FORMAT, bytes: relationshipCaptured.canonicalJson },
    });
    if (!relationshipManifest.ok) return deepFreeze(relationshipManifest);
    return deepFreeze({
      ok: true,
      canonicalWorldEventLedgerReplayReportJson: replayed.canonicalWorldEventLedgerReplayReportJson,
      canonicalNpcMemoryProjectionJson: memoryCaptured.canonicalJson,
      canonicalNpcRelationshipProjectionJson: relationshipCaptured.canonicalJson,
      canonicalMemoryDerivedProjectionManifestJson: memoryManifest.canonicalDerivedProjectionManifestJson,
      canonicalRelationshipDerivedProjectionManifestJson: relationshipManifest.canonicalDerivedProjectionManifestJson,
    });
  } catch (error) {
    if (error instanceof NpcDerivedStateRuntimeOperationalError) throw error;
    throw new NpcDerivedStateRuntimeOperationalError();
  }
}

function verifyExactDocument(actual, expected, validator, code, path) {
  const validated = validator(actual);
  if (!validated.valid) return validationFailure(validated);
  return actual === expected ? undefined : failure(code, path);
}

export function verifyNpcDerivedState(input) {
  try {
    const {
      prepared,
      worldEventLedgerJson,
      memoryProjectionJson,
      relationshipProjectionJson,
      memoryManifestJson,
      relationshipManifestJson,
      derivedStateBundleJson,
    } = input ?? {};
    const data = dataFor(prepared);
    if (!data) return failure("NPC_DERIVED_STATE_PREPARED_INVALID");
    const projected = projectNpcDerivedState({ prepared, worldEventLedgerJson });
    if (!projected.ok) return projected;
    const documents = [
      [memoryProjectionJson, projected.canonicalNpcMemoryProjectionJson, validateNpcMemoryProjectionJson, "NPC_DERIVED_STATE_MEMORY_PROJECTION_MISMATCH", "/memoryProjectionJson"],
      [relationshipProjectionJson, projected.canonicalNpcRelationshipProjectionJson, validateNpcRelationshipProjectionJson, "NPC_DERIVED_STATE_RELATIONSHIP_PROJECTION_MISMATCH", "/relationshipProjectionJson"],
      [memoryManifestJson, projected.canonicalMemoryDerivedProjectionManifestJson, validateDerivedProjectionManifestJson, "NPC_DERIVED_STATE_MEMORY_MANIFEST_MISMATCH", "/memoryManifestJson"],
      [relationshipManifestJson, projected.canonicalRelationshipDerivedProjectionManifestJson, validateDerivedProjectionManifestJson, "NPC_DERIVED_STATE_RELATIONSHIP_MANIFEST_MISMATCH", "/relationshipManifestJson"],
    ];
    for (const [actual, expected, validator, code, path] of documents) {
      const mismatch = verifyExactDocument(actual, expected, validator, code, path);
      if (mismatch) return mismatch;
    }
    const bundleResult = parseValidated(derivedStateBundleJson, validateNpcDerivedStateBundleJson);
    if (!bundleResult.ok) return validationFailure(bundleResult.report);
    const bundle = bundleResult.value;
    const memory = JSON.parse(memoryProjectionJson);
    const relationship = JSON.parse(relationshipProjectionJson);
    const replayReport = JSON.parse(projected.canonicalWorldEventLedgerReplayReportJson);
    const expectedArtifactReferences = {
      personaSeed: artifactReference(NPC_PERSONA_SEED_FORMAT, data.personaSeedJson),
      relationshipPolicy: artifactReference(NPC_RELATIONSHIP_PROJECTION_POLICY_FORMAT, data.relationshipPolicyJson),
      memoryProjection: artifactReference(NPC_MEMORY_PROJECTION_FORMAT, memoryProjectionJson),
      relationshipProjection: artifactReference(NPC_RELATIONSHIP_PROJECTION_FORMAT, relationshipProjectionJson),
      memoryManifest: artifactReference("matrix-oasis.derived-projection-manifest", memoryManifestJson),
      relationshipManifest: artifactReference("matrix-oasis.derived-projection-manifest", relationshipManifestJson),
    };
    if (!compareCanonical(bundle.authority, data.authority)) return failure("NPC_DERIVED_STATE_BUNDLE_AUTHORITY_MISMATCH", "/authority");
    if (bundle.source.npcEntityBindingSha256 !== data.authority.npcEntityBindingSha256) return failure("NPC_DERIVED_STATE_BUNDLE_BINDING_MISMATCH", "/source/npcEntityBindingSha256");
    if (!compareCanonical(bundle.ledger, memory.ledger) || !compareCanonical(bundle.ledger, relationship.ledger)) return failure("NPC_DERIVED_STATE_BUNDLE_LEDGER_MISMATCH", "/ledger");
    if (!compareCanonical(bundle.reducers, NPC_DERIVED_STATE_REDUCERS)) return failure("NPC_DERIVED_STATE_BUNDLE_REDUCER_MISMATCH", "/reducers");
    if (!compareCanonical(bundle.profile, NPC_DERIVED_STATE_PROFILE)) return failure("NPC_DERIVED_STATE_BUNDLE_PROFILE_MISMATCH", "/profile");
    if (!compareCanonical(bundle.artifacts, expectedArtifactReferences)) return failure("NPC_DERIVED_STATE_BUNDLE_ARTIFACT_MISMATCH", "/artifacts");
    const expectedReplay = {
      reportSha256: hashDocument(projected.canonicalWorldEventLedgerReplayReportJson),
      finalSnapshotSha256: replayReport.finalSnapshotSha256,
      finalInspectionSha256: replayReport.finalInspectionSha256,
    };
    if (!compareCanonical(bundle.replay, expectedReplay)) return failure("NPC_DERIVED_STATE_BUNDLE_REPLAY_MISMATCH", "/replay");
    return deepFreeze({
      ...projected,
      canonicalNpcDerivedStateBundleJson: derivedStateBundleJson,
    });
  } catch (error) {
    if (error instanceof NpcDerivedStateRuntimeOperationalError) throw error;
    throw new NpcDerivedStateRuntimeOperationalError();
  }
}

export { NPC_DERIVED_STATE_PROFILE, NPC_DERIVED_STATE_REDUCERS } from "./reducer-registry.mjs";
