import Ajv2020 from "ajv/dist/2020.js";
import { parse, parseTree } from "jsonc-parser";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

export const NPC_DERIVED_STATE_FORMAT_VERSION = "0.1.0";
export const NPC_DERIVED_STATE_CANONICALIZATION = "matrix-oasis.canonical-json/1";
export const NPC_PERSONA_SEED_FORMAT = "matrix-oasis.npc-persona-seed";
export const NPC_RELATIONSHIP_PROJECTION_POLICY_FORMAT = "matrix-oasis.npc-relationship-projection-policy";
export const NPC_MEMORY_PROJECTION_FORMAT = "matrix-oasis.npc-memory-projection";
export const NPC_RELATIONSHIP_PROJECTION_FORMAT = "matrix-oasis.npc-relationship-projection";
export const NPC_DERIVED_STATE_BUNDLE_FORMAT = "matrix-oasis.npc-derived-state-bundle";
export const NPC_PROJECTION_QUALIFICATION_REPORT_FORMAT = "matrix-oasis.npc-projection-qualification-report";

export const NPC_DERIVED_STATE_LIMITS = Object.freeze({
  documentDepth: 256,
  personaBytes: 1024 * 1024,
  relationshipPolicyBytes: 4 * 1024 * 1024,
  memoryProjectionBytes: 16 * 1024 * 1024,
  relationshipProjectionBytes: 16 * 1024 * 1024,
  bundleBytes: 1024 * 1024,
  qualificationReportBytes: 1024 * 1024,
  actors: 64,
  traitIds: 16,
  relationshipRules: 4096,
  ledgerEntries: 10000,
  interactionEntities: 256,
  relationshipEdges: 4096,
  relationshipContributions: 4096,
});

const JSON_SCHEMA_2020_12 = ["https:", "", "json-schema.org", "draft", "2020-12", "schema"].join("/");
const id = { type: "string", minLength: 1, maxLength: 96, pattern: "^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$" };
const version = { type: "string", minLength: 1, maxLength: 64, pattern: "^[0-9]+\\.[0-9]+\\.[0-9]+(?:-[a-z0-9.-]+)?$" };
const sha256 = { type: "string", pattern: "^sha256:[0-9a-f]{64}$" };
const safeInteger = { type: "integer", minimum: 0, maximum: Number.MAX_SAFE_INTEGER };
const positiveSafeInteger = { type: "integer", minimum: 1, maximum: Number.MAX_SAFE_INTEGER };
const revision = { type: "integer", minimum: 0, maximum: NPC_DERIVED_STATE_LIMITS.ledgerEntries };
const positiveRevision = { type: "integer", minimum: 1, maximum: NPC_DERIVED_STATE_LIMITS.ledgerEntries };
const personaValue = { type: "integer", minimum: -1000, maximum: 1000 };
const relationshipDelta = { type: "integer", minimum: -100, maximum: 100 };
const nullableSha256 = { oneOf: [{ type: "null" }, sha256] };
const authorityIdentity = {
  type: "object", additionalProperties: false,
  required: ["runtimePackSha256", "runtimeReceiptSha256", "authorityPolicySha256", "npcEntityBindingSha256"],
  properties: { runtimePackSha256: sha256, runtimeReceiptSha256: sha256, authorityPolicySha256: sha256, npcEntityBindingSha256: sha256 },
};
const reducerIdentity = {
  type: "object", additionalProperties: false,
  required: ["id", "version", "sourceSha256"], properties: { id, version, sourceSha256: sha256 },
};
const ledgerIdentity = {
  type: "object", additionalProperties: false,
  required: ["timelineId", "canonicalSha256", "throughRevision", "throughHeadSha256"],
  properties: { timelineId: id, canonicalSha256: sha256, throughRevision: revision, throughHeadSha256: nullableSha256 },
};
const profile = {
  type: "object", additionalProperties: false,
  required: ["timelineMode", "authorityMode", "personaMode", "memoryScope", "relationshipScope", "deletionMode", "selectiveForgetting", "externalModelCalls", "semanticRetrieval"],
  properties: {
    timelineMode: { const: "single" }, authorityMode: { const: "runtime-and-ledger-only" },
    personaMode: { const: "trusted-static-seed" }, memoryScope: { const: "actor-self-accepted-actions" },
    relationshipScope: { const: "accepted-explicit-policy-rules" }, deletionMode: { const: "whole-derived-state" },
    selectiveForgetting: { const: false }, externalModelCalls: { const: false }, semanticRetrieval: { const: false },
  },
};
const artifactReference = (format, maximumBytes) => ({
  type: "object", additionalProperties: false, required: ["format", "canonicalSha256", "byteLength"],
  properties: { format: { const: format }, canonicalSha256: sha256, byteLength: { type: "integer", minimum: 0, maximum: maximumBytes } },
});
function documentSchema(schemaId, format, required, properties) {
  return {
    $schema: JSON_SCHEMA_2020_12, $id: schemaId, type: "object", additionalProperties: false,
    required: ["format", "formatVersion", "canonicalization", ...required],
    properties: { format: { const: format }, formatVersion: { const: NPC_DERIVED_STATE_FORMAT_VERSION }, canonicalization: { const: NPC_DERIVED_STATE_CANONICALIZATION }, ...properties },
  };
}

export const NPC_PERSONA_SEED_SCHEMA = documentSchema(
  "urn:matrix-oasis:npc-persona-seed:0.1.0", NPC_PERSONA_SEED_FORMAT,
  ["id", "contentVersion", "authority", "traitIds", "actors"], {
    id, contentVersion: version, authority: authorityIdentity,
    traitIds: { type: "array", minItems: 1, maxItems: NPC_DERIVED_STATE_LIMITS.traitIds, uniqueItems: true, items: id },
    actors: { type: "array", minItems: 1, maxItems: NPC_DERIVED_STATE_LIMITS.actors, items: {
      type: "object", additionalProperties: false, required: ["actorEntityId", "traits"], properties: {
        actorEntityId: id,
        traits: { type: "array", minItems: 1, maxItems: NPC_DERIVED_STATE_LIMITS.traitIds, items: {
          type: "object", additionalProperties: false, required: ["traitId", "value"], properties: { traitId: id, value: personaValue },
        } },
      },
    } },
  },
);

export const NPC_RELATIONSHIP_PROJECTION_POLICY_SCHEMA = documentSchema(
  "urn:matrix-oasis:npc-relationship-projection-policy:0.1.0", NPC_RELATIONSHIP_PROJECTION_POLICY_FORMAT,
  ["id", "contentVersion", "authority", "personaSeedSha256", "repeatMode", "rules"], {
    id, contentVersion: version, authority: authorityIdentity, personaSeedSha256: sha256,
    repeatMode: { const: "first-accepted-per-rule-actor-target-timeline" },
    rules: { type: "array", maxItems: NPC_DERIVED_STATE_LIMITS.relationshipRules, items: {
      type: "object", additionalProperties: false,
      required: ["ruleId", "sourceActorEntityId", "targetEntityId", "nodeId", "actionId", "dimensionId", "delta"],
      properties: { ruleId: id, sourceActorEntityId: id, targetEntityId: id, nodeId: id, actionId: id, dimensionId: id, delta: relationshipDelta },
    } },
  },
);

const transitionLocation = {
  type: "object", additionalProperties: false, required: ["kind", "index", "id"],
  properties: { kind: { enum: ["node", "ending"] }, index: safeInteger, id },
};
const memoryTransition = {
  type: "object", additionalProperties: false,
  required: ["transitionVersion", "step", "from", "actionId", "to"],
  properties: {
    transitionVersion: { const: 1 }, step: positiveSafeInteger,
    from: { type: "object", additionalProperties: false, required: ["kind", "index", "id"], properties: { kind: { const: "node" }, index: safeInteger, id } },
    actionId: id, to: transitionLocation,
  },
};
export const NPC_MEMORY_PROJECTION_SCHEMA = documentSchema(
  "urn:matrix-oasis:npc-memory-projection:0.1.0", NPC_MEMORY_PROJECTION_FORMAT,
  ["authority", "personaSeedSha256", "ledger", "reducer", "scopeActorEntityIds", "episodes"], {
    authority: authorityIdentity, personaSeedSha256: sha256, ledger: ledgerIdentity, reducer: reducerIdentity,
    scopeActorEntityIds: { type: "array", minItems: 1, maxItems: NPC_DERIVED_STATE_LIMITS.actors, uniqueItems: true, items: id },
    episodes: { type: "array", maxItems: NPC_DERIVED_STATE_LIMITS.ledgerEntries, items: {
      type: "object", additionalProperties: false,
      required: ["episodeId", "actorEntityId", "intentId", "revision", "entrySha256", "beforeSnapshotSha256", "afterSnapshotSha256", "interactionEntityIds", "transition"],
      properties: {
        episodeId: id, actorEntityId: id, intentId: id, revision: positiveRevision, entrySha256: sha256,
        beforeSnapshotSha256: sha256, afterSnapshotSha256: sha256,
        interactionEntityIds: { type: "array", maxItems: NPC_DERIVED_STATE_LIMITS.interactionEntities, uniqueItems: true, items: id },
        transition: memoryTransition,
      },
    } },
  },
);

export const NPC_RELATIONSHIP_PROJECTION_SCHEMA = documentSchema(
  "urn:matrix-oasis:npc-relationship-projection:0.1.0", NPC_RELATIONSHIP_PROJECTION_FORMAT,
  ["authority", "personaSeedSha256", "relationshipPolicySha256", "ledger", "reducer", "scopeActorEntityIds", "relationships"], {
    authority: authorityIdentity, personaSeedSha256: sha256, relationshipPolicySha256: sha256,
    ledger: ledgerIdentity, reducer: reducerIdentity,
    scopeActorEntityIds: { type: "array", minItems: 1, maxItems: NPC_DERIVED_STATE_LIMITS.actors, uniqueItems: true, items: id },
    relationships: { type: "array", maxItems: NPC_DERIVED_STATE_LIMITS.relationshipEdges, items: {
      type: "object", additionalProperties: false,
      required: ["sourceActorEntityId", "targetEntityId", "dimensionId", "value", "contributions"],
      properties: {
        sourceActorEntityId: id, targetEntityId: id, dimensionId: id, value: personaValue,
        contributions: { type: "array", maxItems: NPC_DERIVED_STATE_LIMITS.relationshipContributions, items: {
          type: "object", additionalProperties: false, required: ["ruleId", "revision", "entrySha256", "delta"],
          properties: { ruleId: id, revision: positiveRevision, entrySha256: sha256, delta: relationshipDelta },
        } },
      },
    } },
  },
);

const projectionManifestFormat = "matrix-oasis.derived-projection-manifest";
export const NPC_DERIVED_STATE_BUNDLE_SCHEMA = documentSchema(
  "urn:matrix-oasis:npc-derived-state-bundle:0.1.0", NPC_DERIVED_STATE_BUNDLE_FORMAT,
  ["source", "authority", "ledger", "replay", "reducers", "profile", "artifacts"], {
    source: { type: "object", additionalProperties: false,
      required: ["r20CurrentSha256", "r20AuthorityManifestSha256", "r20QualificationReceiptSha256", "npcEntityBindingSha256"],
      properties: { r20CurrentSha256: sha256, r20AuthorityManifestSha256: sha256, r20QualificationReceiptSha256: sha256, npcEntityBindingSha256: sha256 },
    },
    authority: authorityIdentity, ledger: ledgerIdentity,
    replay: { type: "object", additionalProperties: false,
      required: ["reportSha256", "finalSnapshotSha256", "finalInspectionSha256"],
      properties: { reportSha256: sha256, finalSnapshotSha256: sha256, finalInspectionSha256: sha256 },
    },
    reducers: { type: "object", additionalProperties: false, required: ["memory", "relationship"], properties: { memory: reducerIdentity, relationship: reducerIdentity } },
    profile,
    artifacts: { type: "object", additionalProperties: false,
      required: ["personaSeed", "relationshipPolicy", "memoryProjection", "relationshipProjection", "memoryManifest", "relationshipManifest"],
      properties: {
        personaSeed: artifactReference(NPC_PERSONA_SEED_FORMAT, NPC_DERIVED_STATE_LIMITS.personaBytes),
        relationshipPolicy: artifactReference(NPC_RELATIONSHIP_PROJECTION_POLICY_FORMAT, NPC_DERIVED_STATE_LIMITS.relationshipPolicyBytes),
        memoryProjection: artifactReference(NPC_MEMORY_PROJECTION_FORMAT, NPC_DERIVED_STATE_LIMITS.memoryProjectionBytes),
        relationshipProjection: artifactReference(NPC_RELATIONSHIP_PROJECTION_FORMAT, NPC_DERIVED_STATE_LIMITS.relationshipProjectionBytes),
        memoryManifest: artifactReference(projectionManifestFormat, NPC_DERIVED_STATE_LIMITS.bundleBytes),
        relationshipManifest: artifactReference(projectionManifestFormat, NPC_DERIVED_STATE_LIMITS.bundleBytes),
      },
    },
  },
);

const rebuildEvidence = {
  type: "object", additionalProperties: false,
  required: ["personaSeedSha256", "relationshipPolicySha256", "replayReportSha256", "memoryProjectionSha256", "relationshipProjectionSha256", "memoryManifestSha256", "relationshipManifestSha256", "bundleSha256"],
  properties: {
    personaSeedSha256: sha256, relationshipPolicySha256: sha256, replayReportSha256: sha256,
    memoryProjectionSha256: sha256, relationshipProjectionSha256: sha256,
    memoryManifestSha256: sha256, relationshipManifestSha256: sha256, bundleSha256: sha256,
  },
};
export const NPC_PROJECTION_QUALIFICATION_REPORT_SCHEMA = documentSchema(
  "urn:matrix-oasis:npc-projection-qualification-report:0.1.0", NPC_PROJECTION_QUALIFICATION_REPORT_FORMAT,
  ["qualifiedBundleSha256", "ledger", "profile", "rebuilds", "deletion", "counts", "isolation", "markers"], {
    qualifiedBundleSha256: sha256, ledger: ledgerIdentity, profile,
    rebuilds: { type: "object", additionalProperties: false,
      required: ["initial", "repeated", "afterDeletion", "repeatedBuildCount"],
      properties: { initial: rebuildEvidence, repeated: rebuildEvidence, afterDeletion: rebuildEvidence, repeatedBuildCount: { const: 20 } },
    },
    deletion: { type: "object", additionalProperties: false,
      required: ["mode", "derivedArtifactsRemoved", "runtimeSnapshotSha256Before", "runtimeSnapshotSha256After", "ledgerSha256Before", "ledgerSha256After"],
      properties: {
        mode: { const: "whole-derived-state" }, derivedArtifactsRemoved: { const: true },
        runtimeSnapshotSha256Before: sha256, runtimeSnapshotSha256After: sha256, ledgerSha256Before: sha256, ledgerSha256After: sha256,
      },
    },
    counts: { type: "object", additionalProperties: false,
      required: ["ledgerEntries", "acceptedEntries", "rejectedEntries", "memoryEpisodes", "relationshipEdges", "relationshipContributions"],
      properties: {
        ledgerEntries: revision, acceptedEntries: revision, rejectedEntries: revision, memoryEpisodes: revision,
        relationshipEdges: { type: "integer", minimum: 0, maximum: NPC_DERIVED_STATE_LIMITS.relationshipEdges },
        relationshipContributions: { type: "integer", minimum: 0, maximum: NPC_DERIVED_STATE_LIMITS.relationshipContributions },
      },
    },
    isolation: { type: "object", additionalProperties: false,
      required: ["externalModelCalls", "networkRequests", "credentialReads"],
      properties: { externalModelCalls: { const: 0 }, networkRequests: { const: 0 }, credentialReads: { const: 0 } },
    },
    markers: { type: "array", minItems: 3, maxItems: 3,
      items: { enum: ["R21_LEDGER_REBUILD_EQUIVALENT", "R21_MEMORY_DELETION_VERIFIED", "R21_RELATIONSHIP_PROJECTION_DETERMINISTIC"] },
    },
  },
);

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}
for (const schema of [NPC_PERSONA_SEED_SCHEMA, NPC_RELATIONSHIP_PROJECTION_POLICY_SCHEMA, NPC_MEMORY_PROJECTION_SCHEMA, NPC_RELATIONSHIP_PROJECTION_SCHEMA, NPC_DERIVED_STATE_BUNDLE_SCHEMA, NPC_PROJECTION_QUALIFICATION_REPORT_SCHEMA]) deepFreeze(schema);

const INTERNAL_CODE = "NPC_DERIVED_STATE_CONTRACT_INTERNAL_ERROR";
const PHASE = Object.freeze({ parse: 0, schema: 1, semantic: 2, canonical: 3 });
export class NpcDerivedStateContractOperationalError extends Error {
  constructor() { super(INTERNAL_CODE); this.name = "NpcDerivedStateContractOperationalError"; this.code = INTERNAL_CODE; }
}
function token(value) { return String(value).replaceAll("~", "~0").replaceAll("/", "~1"); }
function at(path, value) { return `${path}/${token(value)}`; }
function diagnostic(phase, code, path = "") { return { phase, severity: "error", code, path, message: code }; }
function compareText(left, right) { return left < right ? -1 : left > right ? 1 : 0; }
function report(items) {
  const seen = new Set(); const diagnostics = [];
  const compare = (left, right) => (PHASE[left.phase] - PHASE[right.phase]) || compareText(left.path, right.path) || compareText(left.code, right.code);
  for (const value of [...items].sort(compare)) {
    const key = `${value.phase}\0${value.path}\0${value.code}`;
    if (!seen.has(key)) { seen.add(key); diagnostics.push(deepFreeze({ ...value })); }
  }
  return deepFreeze({ reportVersion: 1, valid: diagnostics.length === 0, diagnostics });
}
function tooDeep(text) {
  let depth = 0; let quoted = false; let escaped = false;
  for (const character of text) {
    if (quoted) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === '"') quoted = false;
    } else if (character === '"') quoted = true;
    else if (character === "{" || character === "[") { depth += 1; if (depth > NPC_DERIVED_STATE_LIMITS.documentDepth) return true; }
    else if (character === "}" || character === "]") depth -= 1;
  }
  return false;
}
function duplicateKeys(text, prefix) {
  const tree = parseTree(text, [], { allowTrailingComma: false, disallowComments: true, allowEmptyContent: false });
  if (!tree) return [];
  const output = []; const stack = [{ node: tree, path: "" }];
  while (stack.length) {
    const current = stack.pop();
    if (current.node.type === "object") {
      const keys = new Set();
      for (const property of current.node.children ?? []) {
        const key = property.children?.[0]; const value = property.children?.[1];
        if (!key || !value) continue;
        if (keys.has(key.value)) output.push(diagnostic("parse", `${prefix}_JSON_DUPLICATE_KEY`, current.path));
        keys.add(key.value); stack.push({ node: value, path: at(current.path, key.value) });
      }
    } else if (current.node.type === "array") (current.node.children ?? []).forEach((node, index) => stack.push({ node, path: at(current.path, index) }));
  }
  return output;
}
function parseDocument(text, prefix, byteLimit) {
  if (typeof text !== "string") return { ok: false, diagnostics: [diagnostic("parse", `${prefix}_JSON_INPUT_TYPE`)] };
  if (new TextEncoder().encode(text).byteLength > byteLimit) return { ok: false, diagnostics: [diagnostic("parse", `${prefix}_JSON_SIZE_EXCEEDED`)] };
  if (tooDeep(text)) return { ok: false, diagnostics: [diagnostic("parse", `${prefix}_JSON_DEPTH_EXCEEDED`)] };
  const errors = []; const value = parse(text, errors, { allowTrailingComma: false, disallowComments: true, allowEmptyContent: false });
  if (errors.length || value === undefined) return { ok: false, diagnostics: [diagnostic("parse", `${prefix}_JSON_SYNTAX`)] };
  const duplicates = duplicateKeys(text, prefix);
  return duplicates.length ? { ok: false, diagnostics: duplicates } : { ok: true, value };
}
const ajv = new Ajv2020({ strict: true, allErrors: true, coerceTypes: false, useDefaults: false, removeAdditional: false, ownProperties: true, validateFormats: false });
const validators = new Map([
  ["PERSONA", ajv.compile(NPC_PERSONA_SEED_SCHEMA)], ["POLICY", ajv.compile(NPC_RELATIONSHIP_PROJECTION_POLICY_SCHEMA)],
  ["MEMORY", ajv.compile(NPC_MEMORY_PROJECTION_SCHEMA)], ["RELATIONSHIP", ajv.compile(NPC_RELATIONSHIP_PROJECTION_SCHEMA)],
  ["BUNDLE", ajv.compile(NPC_DERIVED_STATE_BUNDLE_SCHEMA)], ["QUALIFICATION", ajv.compile(NPC_PROJECTION_QUALIFICATION_REPORT_SCHEMA)],
]);
function schemaDiagnostics(validate, prefix, value) {
  if (validate(value)) return [];
  const codes = { required: "REQUIRED", additionalProperties: "UNKNOWN_PROPERTY", type: "TYPE", const: "CONST", enum: "ENUM", minItems: "MIN_ITEMS", maxItems: "MAX_ITEMS", uniqueItems: "DUPLICATE_ITEM", minimum: "NUMBER_CONSTRAINT", maximum: "NUMBER_CONSTRAINT", minLength: "STRING_CONSTRAINT", maxLength: "STRING_CONSTRAINT", pattern: "STRING_CONSTRAINT", oneOf: "SHAPE" };
  return (validate.errors ?? []).map((error) => diagnostic("schema", `${prefix}_SCHEMA_${codes[error.keyword] ?? "INVALID"}`, error.keyword === "required" ? at(error.instancePath, error.params.missingProperty) : error.instancePath));
}
function wellFormed(value) {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) { const next = value.charCodeAt(index + 1); if (!Number.isInteger(next) || next < 0xdc00 || next > 0xdfff) return false; index += 1; }
    else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return true;
}
function primitiveDiagnostics(value, prefix) {
  const output = []; const stack = [{ value, path: "" }];
  while (stack.length) {
    const current = stack.pop();
    if (typeof current.value === "string") { if (!wellFormed(current.value)) output.push(diagnostic("semantic", `${prefix}_TEXT_UNPAIRED_SURROGATE`, current.path)); }
    else if (typeof current.value === "number") { if (!Number.isSafeInteger(current.value)) output.push(diagnostic("semantic", `${prefix}_NUMBER_NOT_SAFE_INTEGER`, current.path)); }
    else if (Array.isArray(current.value)) current.value.forEach((child, index) => stack.push({ value: child, path: at(current.path, index) }));
    else if (current.value && typeof current.value === "object") Object.entries(current.value).forEach(([key, child]) => { if (!wellFormed(key)) output.push(diagnostic("semantic", `${prefix}_TEXT_UNPAIRED_SURROGATE`, current.path)); stack.push({ value: child, path: at(current.path, key) }); });
  }
  return output;
}
function ordered(values, keyOf, code, path) {
  const output = [];
  for (let index = 1; index < values.length; index += 1) if (compareText(keyOf(values[index - 1]), keyOf(values[index])) >= 0) output.push(diagnostic("semantic", code, `${path}/${index}`));
  return output;
}
function ledgerHeadSemantics(value, path = "/ledger") {
  return (value.throughRevision === 0) !== (value.throughHeadSha256 === null) ? [diagnostic("semantic", "NPC_DERIVED_STATE_LEDGER_HEAD_MISMATCH", `${path}/throughHeadSha256`)] : [];
}
function personaSemantics(value) {
  const output = [
    ...ordered(value.traitIds, (traitId) => traitId, "NPC_PERSONA_SEED_TRAIT_ID_ORDER", "/traitIds"),
    ...ordered(value.actors, (actor) => actor.actorEntityId, "NPC_PERSONA_SEED_ACTOR_ORDER", "/actors"),
  ];
  const actors = new Set();
  value.actors.forEach((actor, actorIndex) => {
    if (actors.has(actor.actorEntityId)) output.push(diagnostic("semantic", "NPC_PERSONA_SEED_ACTOR_DUPLICATE", `/actors/${actorIndex}/actorEntityId`));
    actors.add(actor.actorEntityId);
    if (actor.traits.length !== value.traitIds.length) output.push(diagnostic("semantic", "NPC_PERSONA_SEED_TRAIT_VECTOR_INCOMPLETE", `/actors/${actorIndex}/traits`));
    actor.traits.forEach((trait, traitIndex) => {
      if (trait.traitId !== value.traitIds[traitIndex]) output.push(diagnostic("semantic", "NPC_PERSONA_SEED_TRAIT_VECTOR_MISMATCH", `/actors/${actorIndex}/traits/${traitIndex}/traitId`));
    });
  });
  return output;
}
function policySemantics(value) {
  const output = [...ordered(value.rules, (rule) => rule.ruleId, "NPC_RELATIONSHIP_POLICY_RULE_ORDER", "/rules")];
  const ids = new Set(); const rules = new Set(); const edgeBounds = new Map();
  value.rules.forEach((rule, index) => {
    if (ids.has(rule.ruleId)) output.push(diagnostic("semantic", "NPC_RELATIONSHIP_POLICY_RULE_DUPLICATE", `/rules/${index}/ruleId`));
    ids.add(rule.ruleId);
    const tuple = [rule.sourceActorEntityId, rule.targetEntityId, rule.nodeId, rule.actionId, rule.dimensionId].join("\0");
    if (rules.has(tuple)) output.push(diagnostic("semantic", "NPC_RELATIONSHIP_POLICY_RULE_TUPLE_DUPLICATE", `/rules/${index}`));
    rules.add(tuple);
    if (rule.sourceActorEntityId === rule.targetEntityId) output.push(diagnostic("semantic", "NPC_RELATIONSHIP_POLICY_SELF_EDGE_FORBIDDEN", `/rules/${index}/targetEntityId`));
    if (rule.delta === 0) output.push(diagnostic("semantic", "NPC_RELATIONSHIP_POLICY_ZERO_DELTA_FORBIDDEN", `/rules/${index}/delta`));
    const edge = [rule.sourceActorEntityId, rule.targetEntityId, rule.dimensionId].join("\0");
    const bounds = edgeBounds.get(edge) ?? { positive: 0, negative: 0 };
    if (rule.delta > 0) bounds.positive += rule.delta; else bounds.negative += rule.delta;
    edgeBounds.set(edge, bounds);
  });
  for (const bounds of edgeBounds.values()) if (bounds.positive > 1000 || bounds.negative < -1000) output.push(diagnostic("semantic", "NPC_RELATIONSHIP_POLICY_THEORETICAL_RANGE_EXCEEDED", "/rules"));
  return output;
}
function memorySemantics(value) {
  const output = [
    ...ledgerHeadSemantics(value.ledger),
    ...ordered(value.scopeActorEntityIds, (actor) => actor, "NPC_MEMORY_SCOPE_ORDER", "/scopeActorEntityIds"),
    ...ordered(value.episodes, (episode) => `${String(episode.revision).padStart(5, "0")}\0${episode.intentId}`, "NPC_MEMORY_EPISODE_ORDER", "/episodes"),
  ];
  const scope = new Set(value.scopeActorEntityIds); const episodes = new Set(); const intents = new Set(); const revisions = new Set();
  value.episodes.forEach((episode, index) => {
    if (episodes.has(episode.episodeId)) output.push(diagnostic("semantic", "NPC_MEMORY_EPISODE_DUPLICATE", `/episodes/${index}/episodeId`));
    if (intents.has(episode.intentId)) output.push(diagnostic("semantic", "NPC_MEMORY_INTENT_DUPLICATE", `/episodes/${index}/intentId`));
    if (revisions.has(episode.revision)) output.push(diagnostic("semantic", "NPC_MEMORY_REVISION_DUPLICATE", `/episodes/${index}/revision`));
    episodes.add(episode.episodeId); intents.add(episode.intentId); revisions.add(episode.revision);
    if (!scope.has(episode.actorEntityId)) output.push(diagnostic("semantic", "NPC_MEMORY_ACTOR_OUT_OF_SCOPE", `/episodes/${index}/actorEntityId`));
    if (episode.revision > value.ledger.throughRevision) output.push(diagnostic("semantic", "NPC_MEMORY_EPISODE_AFTER_LEDGER_HEAD", `/episodes/${index}/revision`));
    if (episode.transition.step > episode.revision) output.push(diagnostic("semantic", "NPC_MEMORY_TRANSITION_STEP_AFTER_EPISODE", `/episodes/${index}/transition/step`));
    output.push(...ordered(episode.interactionEntityIds, (entityId) => entityId, "NPC_MEMORY_INTERACTION_ENTITY_ORDER", `/episodes/${index}/interactionEntityIds`));
  });
  return output;
}
function relationshipSemantics(value) {
  const output = [
    ...ledgerHeadSemantics(value.ledger),
    ...ordered(value.scopeActorEntityIds, (actor) => actor, "NPC_RELATIONSHIP_SCOPE_ORDER", "/scopeActorEntityIds"),
    ...ordered(value.relationships, (edge) => `${edge.sourceActorEntityId}\0${edge.targetEntityId}\0${edge.dimensionId}`, "NPC_RELATIONSHIP_EDGE_ORDER", "/relationships"),
  ];
  const scope = new Set(value.scopeActorEntityIds); const edges = new Set();
  value.relationships.forEach((edge, edgeIndex) => {
    const edgeKey = `${edge.sourceActorEntityId}\0${edge.targetEntityId}\0${edge.dimensionId}`;
    if (edges.has(edgeKey)) output.push(diagnostic("semantic", "NPC_RELATIONSHIP_EDGE_DUPLICATE", `/relationships/${edgeIndex}`));
    edges.add(edgeKey);
    if (!scope.has(edge.sourceActorEntityId)) output.push(diagnostic("semantic", "NPC_RELATIONSHIP_SOURCE_ACTOR_OUT_OF_SCOPE", `/relationships/${edgeIndex}/sourceActorEntityId`));
    if (edge.sourceActorEntityId === edge.targetEntityId) output.push(diagnostic("semantic", "NPC_RELATIONSHIP_SELF_EDGE_FORBIDDEN", `/relationships/${edgeIndex}/targetEntityId`));
    output.push(...ordered(edge.contributions, (contribution) => `${String(contribution.revision).padStart(5, "0")}\0${contribution.ruleId}`, "NPC_RELATIONSHIP_CONTRIBUTION_ORDER", `/relationships/${edgeIndex}/contributions`));
    const ruleIds = new Set(); let sum = 0;
    edge.contributions.forEach((contribution, contributionIndex) => {
      if (ruleIds.has(contribution.ruleId)) output.push(diagnostic("semantic", "NPC_RELATIONSHIP_RULE_REAPPLIED", `/relationships/${edgeIndex}/contributions/${contributionIndex}/ruleId`));
      ruleIds.add(contribution.ruleId);
      if (contribution.revision > value.ledger.throughRevision) output.push(diagnostic("semantic", "NPC_RELATIONSHIP_CONTRIBUTION_AFTER_LEDGER_HEAD", `/relationships/${edgeIndex}/contributions/${contributionIndex}/revision`));
      if (contribution.delta === 0) output.push(diagnostic("semantic", "NPC_RELATIONSHIP_ZERO_DELTA_FORBIDDEN", `/relationships/${edgeIndex}/contributions/${contributionIndex}/delta`));
      sum += contribution.delta;
    });
    if (!Number.isSafeInteger(sum) || sum !== edge.value) output.push(diagnostic("semantic", "NPC_RELATIONSHIP_AGGREGATE_MISMATCH", `/relationships/${edgeIndex}/value`));
  });
  return output;
}
function bundleSemantics(value) { return ledgerHeadSemantics(value.ledger); }
function qualificationSemantics(value) {
  const output = [...ledgerHeadSemantics(value.ledger)];
  const expectedMarkers = ["R21_LEDGER_REBUILD_EQUIVALENT", "R21_MEMORY_DELETION_VERIFIED", "R21_RELATIONSHIP_PROJECTION_DETERMINISTIC"];
  if (value.markers.join("\0") !== expectedMarkers.join("\0")) output.push(diagnostic("semantic", "NPC_PROJECTION_QUALIFICATION_MARKER_ORDER", "/markers"));
  const initial = canonicalizeJsonValue(value.rebuilds.initial);
  if (canonicalizeJsonValue(value.rebuilds.repeated) !== initial) output.push(diagnostic("semantic", "NPC_PROJECTION_REPEAT_REBUILD_MISMATCH", "/rebuilds/repeated"));
  if (canonicalizeJsonValue(value.rebuilds.afterDeletion) !== initial) output.push(diagnostic("semantic", "NPC_PROJECTION_POST_DELETION_REBUILD_MISMATCH", "/rebuilds/afterDeletion"));
  if (value.qualifiedBundleSha256 !== value.rebuilds.initial.bundleSha256) output.push(diagnostic("semantic", "NPC_PROJECTION_QUALIFIED_BUNDLE_MISMATCH", "/qualifiedBundleSha256"));
  if (value.deletion.runtimeSnapshotSha256Before !== value.deletion.runtimeSnapshotSha256After) output.push(diagnostic("semantic", "NPC_PROJECTION_DELETION_CHANGED_RUNTIME", "/deletion/runtimeSnapshotSha256After"));
  if (value.deletion.ledgerSha256Before !== value.deletion.ledgerSha256After) output.push(diagnostic("semantic", "NPC_PROJECTION_DELETION_CHANGED_LEDGER", "/deletion/ledgerSha256After"));
  if (value.deletion.ledgerSha256Before !== value.ledger.canonicalSha256) output.push(diagnostic("semantic", "NPC_PROJECTION_DELETION_LEDGER_IDENTITY_MISMATCH", "/deletion/ledgerSha256Before"));
  if (value.counts.ledgerEntries !== value.ledger.throughRevision || value.counts.acceptedEntries + value.counts.rejectedEntries !== value.counts.ledgerEntries) output.push(diagnostic("semantic", "NPC_PROJECTION_QUALIFICATION_COUNT_MISMATCH", "/counts"));
  if (value.counts.memoryEpisodes > value.counts.acceptedEntries) output.push(diagnostic("semantic", "NPC_PROJECTION_MEMORY_COUNT_EXCEEDS_ACCEPTED", "/counts/memoryEpisodes"));
  return output;
}
function validateDocument(text, { prefix, limit, validator, semantics }) {
  try {
    const parsed = parseDocument(text, prefix, limit);
    if (!parsed.ok) return report(parsed.diagnostics);
    const schema = schemaDiagnostics(validator, prefix, parsed.value);
    if (schema.length) return report(schema);
    const semantic = [...primitiveDiagnostics(parsed.value, prefix), ...semantics(parsed.value)];
    if (semantic.length) return report(semantic);
    if (canonicalizeJsonValue(parsed.value) !== text) return report([diagnostic("canonical", `${prefix}_JSON_NON_CANONICAL`)]);
    return report([]);
  } catch (error) {
    if (error instanceof NpcDerivedStateContractOperationalError) throw error;
    throw new NpcDerivedStateContractOperationalError();
  }
}
export const validateNpcPersonaSeedJson = (text) => validateDocument(text, { prefix: "NPC_PERSONA_SEED", limit: NPC_DERIVED_STATE_LIMITS.personaBytes, validator: validators.get("PERSONA"), semantics: personaSemantics });
export const validateNpcRelationshipProjectionPolicyJson = (text) => validateDocument(text, { prefix: "NPC_RELATIONSHIP_PROJECTION_POLICY", limit: NPC_DERIVED_STATE_LIMITS.relationshipPolicyBytes, validator: validators.get("POLICY"), semantics: policySemantics });
export const validateNpcMemoryProjectionJson = (text) => validateDocument(text, { prefix: "NPC_MEMORY_PROJECTION", limit: NPC_DERIVED_STATE_LIMITS.memoryProjectionBytes, validator: validators.get("MEMORY"), semantics: memorySemantics });
export const validateNpcRelationshipProjectionJson = (text) => validateDocument(text, { prefix: "NPC_RELATIONSHIP_PROJECTION", limit: NPC_DERIVED_STATE_LIMITS.relationshipProjectionBytes, validator: validators.get("RELATIONSHIP"), semantics: relationshipSemantics });
export const validateNpcDerivedStateBundleJson = (text) => validateDocument(text, { prefix: "NPC_DERIVED_STATE_BUNDLE", limit: NPC_DERIVED_STATE_LIMITS.bundleBytes, validator: validators.get("BUNDLE"), semantics: bundleSemantics });
export const validateNpcProjectionQualificationReportJson = (text) => validateDocument(text, { prefix: "NPC_PROJECTION_QUALIFICATION_REPORT", limit: NPC_DERIVED_STATE_LIMITS.qualificationReportBytes, validator: validators.get("QUALIFICATION"), semantics: qualificationSemantics });
