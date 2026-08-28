import { createHash } from "node:crypto";
import {
  DERIVED_PROJECTION_MANIFEST_FORMAT,
  NPC_AUTHORITY_CANONICALIZATION,
  NPC_AUTHORITY_FORMAT_VERSION,
  NPC_AUTHORITY_LIMITS,
  WORLD_EVENT_LEDGER_FORMAT,
  validateDerivedProjectionManifestJson,
  validateNpcAuthorityPolicyJson,
  validateNpcIntentJson,
  validateWorldEventLedgerJson,
} from "@matrix-oasis/npc-authority-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const INTERNAL_CODE = "NPC_AUTHORITY_INTERNAL_ERROR";
const SHA_PATTERN = /^sha256:[0-9a-f]{64}$/u;
const ID_PATTERN = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/u;

export class NpcAuthorityRuntimeOperationalError extends Error {
  constructor() {
    super(INTERNAL_CODE);
    this.name = "NpcAuthorityRuntimeOperationalError";
    this.code = INTERNAL_CODE;
  }
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function diagnostic(code, path = "") {
  return deepFreeze({ phase: "semantic", severity: "error", code, path, message: code });
}

function failure(code, path = "") {
  return deepFreeze({ ok: false, diagnostics: [diagnostic(code, path)] });
}

function validationFailure(report) {
  return deepFreeze({ ok: false, diagnostics: report.diagnostics });
}

export function hashCanonicalValue(value) {
  return `sha256:${createHash("sha256").update(canonicalizeJsonValue(value), "utf8").digest("hex")}`;
}

function hashBytes(bytes) {
  return `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
}

function parseValidated(text, validator) {
  const report = validator(text);
  return report.valid ? { ok: true, value: JSON.parse(text) } : { ok: false, report };
}

export function createWorldEventLedgerCore(input) {
  try {
    const { policyJson, timelineId, stepLimit, initialSnapshotSha256 } = input ?? {};
    const policy = parseValidated(policyJson, validateNpcAuthorityPolicyJson);
    if (!policy.ok) return validationFailure(policy.report);
    const ledger = {
      format: WORLD_EVENT_LEDGER_FORMAT,
      formatVersion: NPC_AUTHORITY_FORMAT_VERSION,
      canonicalization: NPC_AUTHORITY_CANONICALIZATION,
      timeline: { id: timelineId, stepLimit },
      authority: {
        runtime: policy.value.runtime,
        policy: {
          id: policy.value.id,
          contentVersion: policy.value.contentVersion,
          canonicalSha256: hashCanonicalValue(policy.value),
        },
        initialSnapshotSha256,
      },
      revision: 0,
      headSha256: null,
      entries: [],
    };
    const canonicalWorldEventLedgerJson = canonicalizeJsonValue(ledger);
    const report = validateWorldEventLedgerJson(canonicalWorldEventLedgerJson);
    if (!report.valid) return validationFailure(report);
    return deepFreeze({ ok: true, canonicalWorldEventLedgerJson });
  } catch (error) {
    if (error instanceof NpcAuthorityRuntimeOperationalError) throw error;
    throw new NpcAuthorityRuntimeOperationalError();
  }
}

export function resolveWorldEventLedgerIntent(input) {
  try {
    const { worldEventLedgerJson, npcIntentJson } = input ?? {};
    const ledger = parseValidated(worldEventLedgerJson, validateWorldEventLedgerJson);
    if (!ledger.ok) return validationFailure(ledger.report);
    const intent = parseValidated(npcIntentJson, validateNpcIntentJson);
    if (!intent.ok) return validationFailure(intent.report);
    const existing = ledger.value.entries.find((entry) => entry.intent.id === intent.value.id);
    if (!existing) return deepFreeze({ ok: true, kind: "missing" });
    if (canonicalizeJsonValue(existing.intent) !== npcIntentJson) return failure("NPC_INTENT_ID_COLLISION", "/id");
    return deepFreeze({ ok: true, kind: "replay", entry: existing });
  } catch (error) {
    if (error instanceof NpcAuthorityRuntimeOperationalError) throw error;
    throw new NpcAuthorityRuntimeOperationalError();
  }
}

export function appendWorldEventLedgerEntryCore(input) {
  try {
    const {
      worldEventLedgerJson,
      npcIntentJson,
      decision,
      beforeSnapshotSha256,
      afterSnapshotSha256,
      transition,
    } = input ?? {};
    const ledger = parseValidated(worldEventLedgerJson, validateWorldEventLedgerJson);
    if (!ledger.ok) return validationFailure(ledger.report);
    const intent = parseValidated(npcIntentJson, validateNpcIntentJson);
    if (!intent.ok) return validationFailure(intent.report);
    const duplicate = resolveWorldEventLedgerIntent({ worldEventLedgerJson, npcIntentJson });
    if (!duplicate.ok || duplicate.kind === "replay") return duplicate;
    if (intent.value.timelineId !== ledger.value.timeline.id) return failure("NPC_INTENT_TIMELINE_MISMATCH", "/timelineId");
    if (intent.value.observed.revision !== ledger.value.revision) return failure("NPC_INTENT_STALE_REVISION", "/observed/revision");
    if (intent.value.observed.headSha256 !== ledger.value.headSha256) return failure("NPC_INTENT_STALE_HEAD", "/observed/headSha256");
    if (intent.value.observed.runtimeSnapshotSha256 !== beforeSnapshotSha256) return failure("NPC_INTENT_STALE_SNAPSHOT", "/observed/runtimeSnapshotSha256");
    if (ledger.value.revision >= 10_000) return failure("WORLD_EVENT_LEDGER_CAPACITY_EXCEEDED", "/revision");
    const body = {
      revision: ledger.value.revision + 1,
      intent: intent.value,
      decision,
      beforeSnapshotSha256,
      afterSnapshotSha256,
      transition,
      previousEntrySha256: ledger.value.headSha256,
    };
    const entry = { ...body, entrySha256: hashCanonicalValue(body) };
    const nextLedger = {
      ...ledger.value,
      revision: entry.revision,
      headSha256: entry.entrySha256,
      entries: [...ledger.value.entries, entry],
    };
    const canonicalWorldEventLedgerJson = canonicalizeJsonValue(nextLedger);
    const report = validateWorldEventLedgerJson(canonicalWorldEventLedgerJson);
    if (!report.valid) return validationFailure(report);
    return deepFreeze({ ok: true, kind: "appended", entry, canonicalWorldEventLedgerJson });
  } catch (error) {
    if (error instanceof NpcAuthorityRuntimeOperationalError) throw error;
    throw new NpcAuthorityRuntimeOperationalError();
  }
}

export function createDerivedProjectionManifest(input) {
  try {
    const { worldEventLedgerJson, projectionKind, reducer, scopeEntityIds, artifact } = input ?? {};
    const ledger = parseValidated(worldEventLedgerJson, validateWorldEventLedgerJson);
    if (!ledger.ok) return validationFailure(ledger.report);
    if (!artifact || typeof artifact !== "object" || !(typeof artifact.bytes === "string" || artifact.bytes instanceof Uint8Array)) {
      return failure("DERIVED_PROJECTION_ARTIFACT_BYTES_INVALID", "/artifact");
    }
    if (!Array.isArray(scopeEntityIds)) return failure("DERIVED_PROJECTION_SCOPE_INVALID", "/scopeEntityIds");
    if ((typeof artifact.bytes === "string" && artifact.bytes.length > NPC_AUTHORITY_LIMITS.projectionArtifactBytes) ||
        (artifact.bytes instanceof Uint8Array && artifact.bytes.byteLength > NPC_AUTHORITY_LIMITS.projectionArtifactBytes)) {
      return failure("DERIVED_PROJECTION_ARTIFACT_SIZE_EXCEEDED", "/artifact/bytes");
    }
    const bytes = typeof artifact.bytes === "string" ? new TextEncoder().encode(artifact.bytes) : new Uint8Array(artifact.bytes);
    if (bytes.byteLength > NPC_AUTHORITY_LIMITS.projectionArtifactBytes) return failure("DERIVED_PROJECTION_ARTIFACT_SIZE_EXCEEDED", "/artifact/bytes");
    const manifest = {
      format: DERIVED_PROJECTION_MANIFEST_FORMAT,
      formatVersion: NPC_AUTHORITY_FORMAT_VERSION,
      canonicalization: NPC_AUTHORITY_CANONICALIZATION,
      projectionKind,
      reducer,
      ledger: {
        timelineId: ledger.value.timeline.id,
        canonicalSha256: hashCanonicalValue(ledger.value),
        throughRevision: ledger.value.revision,
        throughHeadSha256: ledger.value.headSha256,
      },
      scopeEntityIds: [...scopeEntityIds].sort((left, right) => left < right ? -1 : left > right ? 1 : 0),
      artifact: { format: artifact.format, byteLength: bytes.byteLength, sha256: hashBytes(bytes) },
    };
    const canonicalDerivedProjectionManifestJson = canonicalizeJsonValue(manifest);
    const report = validateDerivedProjectionManifestJson(canonicalDerivedProjectionManifestJson);
    if (!report.valid) return validationFailure(report);
    return deepFreeze({ ok: true, canonicalDerivedProjectionManifestJson });
  } catch (error) {
    if (error instanceof NpcAuthorityRuntimeOperationalError) throw error;
    throw new NpcAuthorityRuntimeOperationalError();
  }
}

export function isNpcAuthoritySha256(value) {
  return typeof value === "string" && SHA_PATTERN.test(value);
}

export function isNpcAuthorityId(value) {
  return typeof value === "string" && ID_PATTERN.test(value);
}
