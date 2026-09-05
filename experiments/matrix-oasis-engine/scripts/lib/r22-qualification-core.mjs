import { lstat, mkdir, mkdtemp, open, readdir, realpath, rename, rm, rmdir, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import {
  NPC_COGNITION_CANONICALIZATION, NPC_COGNITION_FORMAT_VERSION, NPC_COGNITION_LIMITS,
  NPC_COGNITION_POLICY_FORMAT, NPC_COGNITION_PROFILE, NPC_COGNITION_QUALIFICATION_MARKERS,
  validateNpcCognitionCallPlanJson, validateNpcCognitionPolicyJson,
  validateNpcCognitionQualificationReportJson, validateNpcCognitionTraceJson,
  validateNpcCognitionTurnReceiptJson,
} from "@matrix-oasis/npc-cognition-contracts";
import {
  validateNpcAdjudicationResultJson, validateNpcAuthorityPolicyJson,
  validateNpcIntentJson, validateWorldEventLedgerJson, validateWorldEventLedgerReplayReportJson,
} from "@matrix-oasis/npc-authority-contracts";
import {
  createNpcAuthoritySession,
  restoreNpcAuthoritySession,
  verifyNpcAuthoritySession,
} from "@matrix-oasis/npc-authority-session";
import { validateNpcBehaviorPolicyJson, validateNpcEntityBindingJson } from "@matrix-oasis/npc-behavior-contracts";
import {
  prepareDeterministicNpcBehavior,
  selectEligibleNpcBehaviorCommand,
  selectNextNpcBehaviorCommand,
} from "@matrix-oasis/npc-behavior-runtime";
import {
  validateNpcDerivedStateBundleJson, validateNpcMemoryProjectionJson, validateNpcPersonaSeedJson,
  validateNpcProjectionQualificationReportJson, validateNpcRelationshipProjectionJson,
  validateNpcRelationshipProjectionPolicyJson,
} from "@matrix-oasis/npc-derived-state-contracts";
import { validateDerivedProjectionManifestJson } from "@matrix-oasis/npc-authority-contracts";
import { prepareNpcDerivedState, projectNpcDerivedState, verifyNpcDerivedState } from "@matrix-oasis/npc-derived-state-runtime";
import {
  createNpcCognitionTurn, mapNpcDialogueProposalToIntent, planNpcCognitionCall,
  prepareNpcCognition, replayNpcCognitionEvidence, validateNpcDialogueProposal,
} from "@matrix-oasis/npc-cognition-runtime";
import { createOpenAiNpcCognitionProvider, executeApprovedNpcCognitionTurn } from "@matrix-oasis/npc-cognition-provider-openai";
import { auditR20TimelineStore } from "./r20-cli-core.mjs";
import { closeR22CallStore, computeR22DisplayAckHash, openR22CallStore } from "./r22-call-store.mjs";
import {
  closeR22LoopbackController,
  createR22CognitionSelectorGate, createR22LoopbackController, createR22TransactionalHost,
  handleR22LoopbackRequestAsync, readFinalizedR22CognitionTurn,
} from "./r22-host-core.mjs";
import {
  createR20Coordinator,
  exportR20Coordinator,
  handleR20CoordinatorRequestAsync,
} from "./r20-host-core.mjs";
import {
  canonicalText, assertExactR22DirectoryFiles, assertMissingR22Path,
  createR22OwnedTemporaryDirectory, decodeCanonicalR22Record, directR22TemporaryChild,
  observeR22SourceDirectory, parseR22Pairs, publishR22Artifacts, readStableR22File,
  removeR22OwnedTemporaryDirectory, revalidateR22FileRecord, revalidateR22SourceDirectory,
  sha256, trustR22TemporaryRoot,
} from "./r22-cli-core.mjs";

const SHA = /^sha256:[0-9a-f]{64}$/u;
const ID = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/u;
const CASE_SPEC_FORMAT = "matrix-oasis.r22-offline-case-spec";
const CASE_EVIDENCE_FORMAT = "matrix-oasis.r22-offline-case-evidence-set";
const R21_FILES = Object.freeze([
  "memory-derived-projection-manifest.json", "npc-derived-state-bundle.json",
  "npc-memory-projection.json", "npc-persona-seed.json",
  "npc-projection-qualification-report.json", "npc-relationship-projection-policy.json",
  "npc-relationship-projection.json", "relationship-derived-projection-manifest.json",
  "world-event-ledger-replay-report.json",
].sort());
const REDACTION = Object.freeze({
  playerTextStored: false, contextStored: false, providerPayloadStored: false,
  dialogueTextStored: false, responseIdStored: false, credentialStored: false,
  rawErrorStored: false,
});

export const R22_QUALIFICATION_FILES = Object.freeze([
  "godot-evidence.json", "npc-cognition-policy.json",
  "npc-cognition-qualification-report.json", "npc-cognition-trace.json",
  "npc-projection-qualification-report.json", "offline-case-evidence.json",
  "performance-evidence.json", "world-event-ledger-replay-report.json",
].sort());
export const R22_QUALIFICATION_MARKERS = NPC_COGNITION_QUALIFICATION_MARKERS;

export class R22QualificationOperationalError extends Error {
  constructor(code, cause = undefined) {
    super(code); this.name = "R22QualificationOperationalError"; this.code = code;
    if (cause !== undefined) this.cause = cause;
  }
}
function fail(code, cause) { throw new R22QualificationOperationalError(code, cause); }
function exact(value, keys) {
  return value && typeof value === "object" && !Array.isArray(value) &&
    Object.keys(value).sort().join("\0") === [...keys].sort().join("\0");
}
function same(left, right) { return canonicalText(left) === canonicalText(right); }
function valid(report) { return report?.valid === true && Array.isArray(report.diagnostics) && report.diagnostics.length === 0; }
function parseCanonical(text, code) {
  let value; try { value = JSON.parse(text); } catch { fail(code); }
  if (canonicalText(value) !== text) fail(code); return value;
}
function requireCanonicalArtifact(text, code, format = null) {
  if (typeof text !== "string" || Buffer.byteLength(text, "utf8") < 1 || Buffer.byteLength(text, "utf8") > 16 * 1024 * 1024) fail(code);
  const value = parseCanonical(text, code); if (format !== null && value?.format !== format) fail(code); return value;
}
function safeHash(value, code) { if (!SHA.test(value ?? "")) fail(code); return value; }
function requireOk(value, code) { if (value?.ok !== true) fail(code); return value; }
function ledgerPoint(runtimeSnapshot, ledger) {
  return Object.freeze({ revision: ledger.revision, headSha256: ledger.headSha256, runtimeSnapshotSha256: sha256(canonicalText(runtimeSnapshot)) });
}
function receiptOutcome(receipt) {
  if (receipt.statusHistory.includes("adjudicated")) return "adjudicated";
  if (receipt.statusHistory.includes("dialogue_only")) return "dialogue_only";
  return "fallback";
}
function artifactHashes(documents) {
  return Object.freeze({
    personaSeedSha256: sha256(documents.personaSeedJson),
    relationshipPolicySha256: sha256(documents.relationshipPolicyJson),
    replayReportSha256: sha256(documents.replayReportJson),
    bundleSha256: sha256(documents.bundleJson),
    memoryProjectionSha256: sha256(documents.memoryProjectionJson),
    relationshipProjectionSha256: sha256(documents.relationshipProjectionJson),
    memoryManifestSha256: sha256(documents.memoryManifestJson),
    relationshipManifestSha256: sha256(documents.relationshipManifestJson),
  });
}

export function validateR22OfflineCaseSpecJson(text) {
  try {
    const value = parseCanonical(text, "R22_CASE_SPEC_INVALID");
    if (!exact(value, ["format", "formatVersion", "canonicalization", "primaryCaseId", "cases"]) ||
        value.format !== CASE_SPEC_FORMAT || value.formatVersion !== "0.1.0" ||
        value.canonicalization !== NPC_COGNITION_CANONICALIZATION || !ID.test(value.primaryCaseId ?? "") ||
        !Array.isArray(value.cases) || value.cases.length !== 3) fail("R22_CASE_SPEC_INVALID");
    const ids = new Set(); let previous = "";
    for (const item of value.cases) {
      if (!exact(item, ["caseId", "sourceKind", "npcRunRoot", "derivedStateRoot", "expectedNpcCurrentSha256", "expectedDerivedStateBundleSha256"]) ||
          !["qualified-cache", "synthetic-fixture"].includes(item.sourceKind) || !ID.test(item.caseId ?? "") ||
          ids.has(item.caseId) || (previous && item.caseId.localeCompare(previous) <= 0) ||
          typeof item.npcRunRoot !== "string" || !path.isAbsolute(item.npcRunRoot) ||
          typeof item.derivedStateRoot !== "string" || !path.isAbsolute(item.derivedStateRoot) ||
          item.npcRunRoot.includes("\0") || item.derivedStateRoot.includes("\0")) fail("R22_CASE_SPEC_INVALID");
      safeHash(item.expectedNpcCurrentSha256, "R22_CASE_SPEC_INVALID");
      safeHash(item.expectedDerivedStateBundleSha256, "R22_CASE_SPEC_INVALID");
      ids.add(item.caseId); previous = item.caseId;
    }
    if (value.cases.filter(({ sourceKind }) => sourceKind === "qualified-cache").length !== 2 ||
        value.cases.filter(({ sourceKind }) => sourceKind === "synthetic-fixture").length !== 1 ||
        !ids.has(value.primaryCaseId)) fail("R22_CASE_SPEC_INVALID");
    return Object.freeze(value);
  } catch (error) {
    if (error instanceof R22QualificationOperationalError) throw error;
    fail("R22_CASE_SPEC_INVALID", error);
  }
}

const policyLimits = () => ({
  turnsPerTimeline: NPC_COGNITION_LIMITS.turnsPerTimeline,
  turnsPerActor: NPC_COGNITION_LIMITS.turnsPerActor,
  callsPerTimeline: NPC_COGNITION_LIMITS.callsPerTimeline,
  callsPerActor: NPC_COGNITION_LIMITS.callsPerActor,
  concurrentCalls: NPC_COGNITION_LIMITS.concurrentCalls,
  candidateActionsPerTurn: NPC_COGNITION_LIMITS.candidateActionsPerTurn,
  memoryEpisodesPerActor: NPC_COGNITION_LIMITS.memoryEpisodesPerActor,
  relationshipEdgesPerActor: NPC_COGNITION_LIMITS.relationshipEdgesPerActor,
  transientDialogueExchanges: NPC_COGNITION_LIMITS.transientDialogueExchanges,
  transientDialogueBytes: NPC_COGNITION_LIMITS.transientDialogueBytes,
  playerTextBytes: NPC_COGNITION_LIMITS.playerTextBytes,
  derivedContextBytes: NPC_COGNITION_LIMITS.derivedContextBytes,
  providerRequestBytes: NPC_COGNITION_LIMITS.providerRequestBytes,
  providerResponseBytes: NPC_COGNITION_LIMITS.providerResponseBytes,
  dialogueBytes: NPC_COGNITION_LIMITS.dialogueBytes,
  dialogueLines: NPC_COGNITION_LIMITS.dialogueLines,
  maxOutputTokens: NPC_COGNITION_LIMITS.maxOutputTokens,
  timeoutMs: NPC_COGNITION_LIMITS.timeoutMs,
  approvalLifetimeMs: NPC_COGNITION_LIMITS.approvalLifetimeMs,
});
const budgets = () => ({
  perCallMicrousd: NPC_COGNITION_LIMITS.perCallMicrousd,
  perTimelineMicrousd: NPC_COGNITION_LIMITS.perTimelineMicrousd,
  perHostRunMicrousd: NPC_COGNITION_LIMITS.perHostRunMicrousd,
});

export function synthesizeNpcCognitionPolicyDocument(input) {
  try {
    const keys = ["runtimeGamePackJson", "runtimeReceiptJson", "authorityPolicyJson", "behaviorPolicyJson", "npcEntityBindingJson", "derivedStateBundleJson"];
    if (!exact(input, keys) || keys.some((key) => typeof input[key] !== "string")) fail("R22_POLICY_SYNTHESIS_INPUT_INVALID");
    const runtimePack = parseCanonical(input.runtimeGamePackJson, "R22_POLICY_SYNTHESIS_INPUT_INVALID");
    const runtimeReceipt = parseCanonical(input.runtimeReceiptJson, "R22_POLICY_SYNTHESIS_INPUT_INVALID");
    const authority = parseCanonical(input.authorityPolicyJson, "R22_POLICY_SYNTHESIS_INPUT_INVALID");
    const behavior = parseCanonical(input.behaviorPolicyJson, "R22_POLICY_SYNTHESIS_INPUT_INVALID");
    const binding = parseCanonical(input.npcEntityBindingJson, "R22_POLICY_SYNTHESIS_INPUT_INVALID");
    parseCanonical(input.derivedStateBundleJson, "R22_POLICY_SYNTHESIS_INPUT_INVALID");
    if (!valid(validateNpcAuthorityPolicyJson(input.authorityPolicyJson)) ||
        !valid(validateNpcBehaviorPolicyJson(input.behaviorPolicyJson)) ||
        !valid(validateNpcEntityBindingJson(input.npcEntityBindingJson)) ||
        !valid(validateNpcDerivedStateBundleJson(input.derivedStateBundleJson))) fail("R22_POLICY_SYNTHESIS_INPUT_INVALID");
    const grants = new Map(authority.actorGrants.map((actor) => [actor.actorEntityId, new Set(actor.grants.map((grant) => `${grant.nodeId}\0${grant.actionId}`))]));
    const behaviorActors = new Map(behavior.actors.map((actor) => [actor.actorEntityId, actor]));
    const actors = binding.bindings.map((item) => item.actorEntityId).sort().map((actorEntityId) => {
      const actor = behaviorActors.get(actorEntityId); const allowed = grants.get(actorEntityId) ?? new Set();
      const safeActions = (actor?.rules ?? []).filter((rule) => allowed.has(`${rule.nodeId}\0${rule.actionId}`))
        .map(({ nodeId, actionId }) => ({ nodeId, actionId }))
        .sort((a, b) => a.nodeId.localeCompare(b.nodeId) || a.actionId.localeCompare(b.actionId));
      return { actorEntityId, safeActions };
    });
    if (actors.length < 1 || actors.length > NPC_COGNITION_LIMITS.actors) fail("R22_POLICY_SYNTHESIS_ACTOR_LIMIT");
    const identitySeed = canonicalText({ runtimePack: runtimePack.format, runtimeReceipt: runtimeReceipt.format, identities: actors });
    const policy = {
      format: NPC_COGNITION_POLICY_FORMAT, formatVersion: NPC_COGNITION_FORMAT_VERSION,
      canonicalization: NPC_COGNITION_CANONICALIZATION,
      id: `cognition-policy-${sha256(identitySeed).slice(7, 23)}`, contentVersion: "0.1.0-r22",
      identities: {
        runtimePackSha256: sha256(input.runtimeGamePackJson), runtimeReceiptSha256: sha256(input.runtimeReceiptJson),
        authorityPolicySha256: sha256(input.authorityPolicyJson), behaviorPolicySha256: sha256(input.behaviorPolicyJson),
        entityBindingSha256: sha256(input.npcEntityBindingJson), derivedStateBundleSha256: sha256(input.derivedStateBundleJson),
      },
      actors, limits: policyLimits(), budgets: budgets(),
    };
    const canonicalNpcCognitionPolicyJson = canonicalText(policy);
    if (!valid(validateNpcCognitionPolicyJson(canonicalNpcCognitionPolicyJson))) fail("R22_POLICY_SYNTHESIS_OUTPUT_INVALID");
    return Object.freeze({ ok: true, npcCognitionPolicy: Object.freeze(policy), canonicalNpcCognitionPolicyJson });
  } catch (error) {
    if (error instanceof R22QualificationOperationalError) throw error;
    fail("R22_POLICY_SYNTHESIS_INTERNAL_ERROR", error);
  }
}

async function readTrackedDocument(file, maximumBytes, trustedRoot, records, code, overrides) {
  const record = await readStableR22File(file, maximumBytes, trustedRoot, overrides);
  const document = decodeCanonicalR22Record(record, code); records.push(record); return document;
}

async function assertR20WriterInactive(npcRunRoot) {
  const writerLock = path.join(path.dirname(npcRunRoot), `.${path.basename(npcRunRoot)}.writer-lock`);
  try {
    await lstat(writerLock, { bigint: true });
  } catch (error) {
    if (error?.code === "ENOENT") return;
    fail("R22_R20_SOURCE_WRITER_STATE_UNKNOWN", error);
  }
  fail("R22_R20_SOURCE_WRITER_ACTIVE");
}

function readOnlyR20AuditOperations(sourceRoot, virtualDirectory, targetManifestId) {
  const sourceLockPrefix = path.join(path.dirname(sourceRoot), `.${path.basename(sourceRoot)}.writer-lock`);
  const virtualLockPrefix = path.join(virtualDirectory, "writer-lock");
  const timelineContainer = path.join(sourceRoot, "timelines");
  const mapped = (candidate) => {
    const absolute = path.resolve(candidate);
    return absolute.startsWith(sourceLockPrefix) ? `${virtualLockPrefix}${absolute.slice(sourceLockPrefix.length)}` : absolute;
  };
  return Object.freeze({
    lstat: (candidate, options) => lstat(mapped(candidate), options),
    mkdir: (candidate, options) => mkdir(mapped(candidate), options),
    mkdtemp: (candidate, options) => mkdtemp(mapped(candidate), options),
    openFile: (candidate, flags, mode) => open(mapped(candidate), flags, mode),
    async readdir(candidate, options) {
      if (path.resolve(candidate) === timelineContainer) {
        const found = await readdir(candidate, options);
        if (options?.withFileTypes || !found.includes(targetManifestId)) fail("R22_R20_CURRENT_TIMELINE_MISSING");
        return [targetManifestId];
      }
      return readdir(mapped(candidate), options);
    },
    realpath: async (candidate) => path.resolve(candidate).startsWith(sourceLockPrefix) ? path.resolve(candidate) : realpath(candidate),
    rename: (from, to) => rename(mapped(from), mapped(to)),
    rm: (candidate, options) => rm(mapped(candidate), options),
    rmdir: (candidate, options) => rmdir(mapped(candidate), options),
    stat: (candidate, options) => stat(mapped(candidate), options),
    writeFile: (candidate, data, options) => writeFile(mapped(candidate), data, options),
    isProcessAlive(pid) {
      try { process.kill(pid, 0); return true; }
      catch (error) { if (error?.code === "ESRCH") return false; return true; }
    },
  });
}

async function auditQualifiedR20Source(item, trustedRoot, overrides, targetManifestId) {
  let owned = null; let primaryError = null;
  try {
    await assertR20WriterInactive(item.npcRunRoot);
    owned = await createR22OwnedTemporaryDirectory(trustedRoot, "r22-r20-audit", overrides);
    const audit = await auditR20TimelineStore(
      { npcRunRoot: item.npcRunRoot, temporaryRoot: trustedRoot.path },
      readOnlyR20AuditOperations(item.npcRunRoot, owned.path, targetManifestId),
    );
    await assertR20WriterInactive(item.npcRunRoot);
    return audit;
  } catch (error) {
    primaryError = error;
    throw error;
  } finally {
    if (owned !== null) {
      try { await removeR22OwnedTemporaryDirectory(owned.handle); }
      catch (error) { if (primaryError === null) throw error; }
    }
  }
}

function verifyR21QualificationReport(report, documents, source) {
  if (!valid(validateNpcProjectionQualificationReportJson(documents.qualificationReportJson))) fail("R22_R21_QUALIFICATION_INVALID");
  const hashes = artifactHashes(documents); const ledger = JSON.parse(source.worldEventLedgerJson);
  const replay = JSON.parse(documents.replayReportJson); const memory = JSON.parse(documents.memoryProjectionJson);
  const relationship = JSON.parse(documents.relationshipProjectionJson); const bundle = JSON.parse(documents.bundleJson);
  const acceptedEntries = ledger.entries.filter((entry) => entry.decision.status === "accepted").length;
  const counts = {
    ledgerEntries: ledger.entries.length, acceptedEntries, rejectedEntries: ledger.entries.length - acceptedEntries,
    memoryEpisodes: memory.episodes.length, relationshipEdges: relationship.relationships.length,
    relationshipContributions: relationship.relationships.reduce((sum, edge) => sum + edge.contributions.length, 0),
  };
  const deletion = {
    mode: "whole-derived-state", derivedArtifactsRemoved: true,
    runtimeSnapshotSha256Before: replay.finalSnapshotSha256, runtimeSnapshotSha256After: replay.finalSnapshotSha256,
    ledgerSha256Before: sha256(source.worldEventLedgerJson), ledgerSha256After: sha256(source.worldEventLedgerJson),
  };
  if (report.qualifiedBundleSha256 !== hashes.bundleSha256 || !same(report.ledger, bundle.ledger) ||
      !same(report.profile, bundle.profile) || !same(report.rebuilds.initial, hashes) ||
      !same(report.rebuilds.repeated, hashes) || !same(report.rebuilds.afterDeletion, hashes) ||
      report.rebuilds.repeatedBuildCount !== 20 || !same(report.counts, counts) || !same(report.deletion, deletion) ||
      report.isolation.externalModelCalls !== 0 || report.isolation.networkRequests !== 0 ||
      report.isolation.credentialReads !== 0 || bundle.replay.reportSha256 !== hashes.replayReportSha256 ||
      bundle.replay.finalSnapshotSha256 !== replay.finalSnapshotSha256 ||
      bundle.replay.finalInspectionSha256 !== replay.finalInspectionSha256) fail("R22_R21_QUALIFICATION_IDENTITY_MISMATCH");
}

export async function verifyR22QualifiedSourcePair(item, trustedRootInput, overrides = {}) {
  const trustedRoot = typeof trustedRootInput === "string" ? await trustR22TemporaryRoot(trustedRootInput, overrides) : trustedRootInput;
  const npc = await observeR22SourceDirectory(item.npcRunRoot, "npc-current.json", item.expectedNpcCurrentSha256, trustedRoot, overrides);
  const derived = await observeR22SourceDirectory(item.derivedStateRoot, "npc-derived-state-bundle.json", item.expectedDerivedStateBundleSha256, trustedRoot, overrides);
  const current = decodeCanonicalR22Record(npc.record, "R22_R20_CURRENT_INVALID").value;
  const sourceBundle = decodeCanonicalR22Record(derived.record, "R22_R21_BUNDLE_INVALID").value;
  if (!exact(current, ["format", "formatVersion", "manifestSha256", "timelineId", "revision", "headSha256", "qualificationReceiptSha256"]) ||
      current.format !== "matrix-oasis.npc-current" || current.formatVersion !== "0.1.0" ||
      !SHA.test(current.manifestSha256 ?? "") || !SHA.test(current.qualificationReceiptSha256 ?? "")) fail("R22_R20_CURRENT_INVALID");
  const audit = await auditQualifiedR20Source(item, trustedRoot, overrides, current.manifestSha256.slice(7));
  const currentTimeline = audit.timelines.filter((entry) => entry.status === "qualified" && entry.qualified === true &&
    entry.manifestId === current.manifestSha256.slice(7) && entry.timelineId === current.timelineId &&
    entry.revision === current.revision && entry.headSha256 === current.headSha256 &&
    entry.qualificationReceiptSha256 === current.qualificationReceiptSha256);
  if (!same(audit.current, current) || audit.pendingCurrent !== null || currentTimeline.length !== 1) fail("R22_R20_SOURCE_NOT_QUALIFIED");
  const timelineRoot = path.join(item.npcRunRoot, "timelines", current.manifestSha256.slice(7)); const records = [];
  const authorityManifest = await readTrackedDocument(path.join(timelineRoot, "authority-manifest.json"), 16 * 1024 * 1024, trustedRoot, records, "R22_R20_MANIFEST_INVALID", overrides);
  const behaviorPolicy = await readTrackedDocument(path.join(timelineRoot, "behavior-policy.json"), 16 * 1024 * 1024, trustedRoot, records, "R22_R20_BEHAVIOR_POLICY_INVALID", overrides);
  const entityBinding = await readTrackedDocument(path.join(timelineRoot, "entity-bindings.json"), 16 * 1024 * 1024, trustedRoot, records, "R22_R20_ENTITY_BINDING_INVALID", overrides);
  const ledger = await readTrackedDocument(path.join(timelineRoot, "world-event-ledger.json"), 16 * 1024 * 1024, trustedRoot, records, "R22_R20_LEDGER_INVALID", overrides);
  const qualificationEvidence = await readTrackedDocument(path.join(timelineRoot, "qualification-evidence.json"), 32 * 1024 * 1024, trustedRoot, records, "R22_R20_QUALIFICATION_INVALID", overrides);
  const godotTrace = await readTrackedDocument(path.join(timelineRoot, "godot-trace.json"), 16 * 1024 * 1024, trustedRoot, records, "R22_R20_GODOT_EVIDENCE_INVALID", overrides);
  const bridgeReport = await readTrackedDocument(path.join(timelineRoot, "bridge-report.json"), 16 * 1024 * 1024, trustedRoot, records, "R22_R20_GODOT_EVIDENCE_INVALID", overrides);
  if (sha256(authorityManifest.text) !== current.manifestSha256 || authorityManifest.value.timelineId !== current.timelineId ||
      ledger.value.timeline.id !== current.timelineId || ledger.value.revision !== current.revision || ledger.value.headSha256 !== current.headSha256 ||
      sha256(behaviorPolicy.text) !== authorityManifest.value.identities.behaviorPolicySha256 ||
      sha256(entityBinding.text) !== authorityManifest.value.identities.entityBindingSha256 ||
      qualificationEvidence.value.qualificationReceiptSha256 !== current.qualificationReceiptSha256 ||
      qualificationEvidence.value.runtimeGamePackSha256 !== sha256(qualificationEvidence.value.runtimeGamePackJson) ||
      qualificationEvidence.value.runtimeReceiptSha256 !== sha256(qualificationEvidence.value.runtimeReceiptJson) ||
      qualificationEvidence.value.authorityPolicySha256 !== sha256(qualificationEvidence.value.authorityPolicyJson)) fail("R22_R20_SOURCE_IDENTITY_MISMATCH");
  if (!valid(validateNpcBehaviorPolicyJson(behaviorPolicy.text)) || !valid(validateNpcEntityBindingJson(entityBinding.text)) ||
      !valid(validateWorldEventLedgerJson(ledger.text)) || !valid(validateNpcAuthorityPolicyJson(qualificationEvidence.value.authorityPolicyJson))) fail("R22_R20_SOURCE_CONTRACT_INVALID");

  await assertExactR22DirectoryFiles(item.derivedStateRoot, R21_FILES, trustedRoot, overrides);
  const r21 = Object.create(null);
  for (const name of R21_FILES) {
    const document = await readTrackedDocument(path.join(item.derivedStateRoot, name), 16 * 1024 * 1024, trustedRoot, records, "R22_R21_ARTIFACT_INVALID", overrides);
    r21[name] = document.text;
  }
  const documents = Object.freeze({
    personaSeedJson: r21["npc-persona-seed.json"], relationshipPolicyJson: r21["npc-relationship-projection-policy.json"],
    replayReportJson: r21["world-event-ledger-replay-report.json"], memoryProjectionJson: r21["npc-memory-projection.json"],
    relationshipProjectionJson: r21["npc-relationship-projection.json"], memoryManifestJson: r21["memory-derived-projection-manifest.json"],
    relationshipManifestJson: r21["relationship-derived-projection-manifest.json"], bundleJson: r21["npc-derived-state-bundle.json"],
    qualificationReportJson: r21["npc-projection-qualification-report.json"],
  });
  if (!valid(validateNpcPersonaSeedJson(documents.personaSeedJson)) ||
      !valid(validateNpcRelationshipProjectionPolicyJson(documents.relationshipPolicyJson)) ||
      !valid(validateNpcMemoryProjectionJson(documents.memoryProjectionJson)) ||
      !valid(validateNpcRelationshipProjectionJson(documents.relationshipProjectionJson)) ||
      !valid(validateDerivedProjectionManifestJson(documents.memoryManifestJson)) ||
      !valid(validateDerivedProjectionManifestJson(documents.relationshipManifestJson)) ||
      !valid(validateNpcDerivedStateBundleJson(documents.bundleJson)) ||
      !valid(validateWorldEventLedgerReplayReportJson(documents.replayReportJson))) fail("R22_R21_ARTIFACT_INVALID");
  const runtimeGamePackJson = qualificationEvidence.value.runtimeGamePackJson;
  const runtimeReceiptJson = qualificationEvidence.value.runtimeReceiptJson;
  const authorityPolicyJson = qualificationEvidence.value.authorityPolicyJson;
  const derivedPrepared = requireOk(await prepareNpcDerivedState({ runtimeGamePackJson, runtimeReceiptJson, authorityPolicyJson, npcEntityBindingJson: entityBinding.text, personaSeedJson: documents.personaSeedJson, relationshipPolicyJson: documents.relationshipPolicyJson }), "R22_R21_PREPARE_FAILED");
  requireOk(verifyNpcDerivedState({ prepared: derivedPrepared.prepared, worldEventLedgerJson: ledger.text,
    memoryProjectionJson: documents.memoryProjectionJson, relationshipProjectionJson: documents.relationshipProjectionJson,
    memoryManifestJson: documents.memoryManifestJson, relationshipManifestJson: documents.relationshipManifestJson,
    derivedStateBundleJson: documents.bundleJson }), "R22_R21_VERIFY_FAILED");
  if (sourceBundle.source.r20CurrentSha256 !== npc.record.sha256 ||
      sourceBundle.source.r20AuthorityManifestSha256 !== current.manifestSha256 ||
      sourceBundle.source.r20QualificationReceiptSha256 !== current.qualificationReceiptSha256 ||
      sourceBundle.source.npcEntityBindingSha256 !== sha256(entityBinding.text) ||
      sourceBundle.ledger.canonicalSha256 !== sha256(ledger.text) || sourceBundle.ledger.timelineId !== current.timelineId ||
      sourceBundle.ledger.throughRevision !== current.revision || sourceBundle.ledger.throughHeadSha256 !== current.headSha256) fail("R22_R20_R21_PAIR_MISMATCH");
  verifyR21QualificationReport(JSON.parse(documents.qualificationReportJson), documents, { worldEventLedgerJson: ledger.text });
  if (item.sourceKind === "synthetic-fixture" && entityBinding.value.bindings.length !== 2) fail("R22_SYNTHETIC_DUAL_ACTOR_REQUIRED");
  return Object.freeze({
    kind: item.sourceKind, npc, derived, records: Object.freeze(records), current,
    sourceIdentity: sourceBundle.source, authorityManifestJson: authorityManifest.text,
    runtimeGamePackJson, runtimeReceiptJson, authorityPolicyJson,
    behaviorPolicyJson: behaviorPolicy.text, npcEntityBindingJson: entityBinding.text,
    originalWorldEventLedgerJson: ledger.text, r21: documents,
    r20Godot: Object.freeze({ godotTraceJson: godotTrace.text, bridgeReportJson: bridgeReport.text }),
  });
}

export async function revalidateQualifiedSource(source, trustedRoot, overrides = {}) {
  await revalidateR22SourceDirectory(source.npc, trustedRoot, overrides);
  await revalidateR22SourceDirectory(source.derived, trustedRoot, overrides);
  for (const record of source.records) {
    await revalidateR22FileRecord(record, record.path.endsWith("qualification-evidence.json") ? 32 * 1024 * 1024 : 16 * 1024 * 1024, trustedRoot, overrides);
  }
  const audit = await auditQualifiedR20Source(
    { caseId: "source-revalidation", npcRunRoot: source.npc.directory.path },
    trustedRoot,
    overrides,
    source.current.manifestSha256.slice(7),
  );
  if (!same(audit.current, source.current) || audit.pendingCurrent !== null) fail("R22_SOURCE_CHANGED");
}

function ephemeralProjectionDocuments(projected) {
  return Object.freeze({
    replayReportJson: projected.canonicalWorldEventLedgerReplayReportJson,
    memoryProjectionJson: projected.canonicalNpcMemoryProjectionJson,
    relationshipProjectionJson: projected.canonicalNpcRelationshipProjectionJson,
    memoryManifestJson: projected.canonicalMemoryDerivedProjectionManifestJson,
    relationshipManifestJson: projected.canonicalRelationshipDerivedProjectionManifestJson,
  });
}

function validateEphemeralProjectionDocuments(documents) {
  return valid(validateWorldEventLedgerReplayReportJson(documents.replayReportJson)) &&
    valid(validateNpcMemoryProjectionJson(documents.memoryProjectionJson)) &&
    valid(validateNpcRelationshipProjectionJson(documents.relationshipProjectionJson)) &&
    valid(validateDerivedProjectionManifestJson(documents.memoryManifestJson)) &&
    valid(validateDerivedProjectionManifestJson(documents.relationshipManifestJson));
}

function ephemeralProjectionHashes(documents) {
  return Object.freeze({
    replayReportSha256: sha256(documents.replayReportJson),
    memoryProjectionSha256: sha256(documents.memoryProjectionJson),
    relationshipProjectionSha256: sha256(documents.relationshipProjectionJson),
    memoryManifestSha256: sha256(documents.memoryManifestJson),
    relationshipManifestSha256: sha256(documents.relationshipManifestJson),
  });
}

function ephemeralProjectionArtifacts(documents) {
  return Object.freeze({
    worldEventLedgerReplayReportJson: documents.replayReportJson,
    npcMemoryProjectionJson: documents.memoryProjectionJson,
    npcRelationshipProjectionJson: documents.relationshipProjectionJson,
    memoryDerivedProjectionManifestJson: documents.memoryManifestJson,
    relationshipDerivedProjectionManifestJson: documents.relationshipManifestJson,
  });
}

export async function projectEphemeralDerivedContext(source, worldEventLedgerJson) {
  const prepared = requireOk(await prepareNpcDerivedState({
    runtimeGamePackJson: source.runtimeGamePackJson, runtimeReceiptJson: source.runtimeReceiptJson,
    authorityPolicyJson: source.authorityPolicyJson, npcEntityBindingJson: source.npcEntityBindingJson,
    personaSeedJson: source.r21.personaSeedJson, relationshipPolicyJson: source.r21.relationshipPolicyJson,
  }), "R22_DERIVED_STATE_PREPARE_FAILED");
  const first = requireOk(projectNpcDerivedState({ prepared: prepared.prepared, worldEventLedgerJson }), "R22_DERIVED_STATE_PROJECT_FAILED");
  const repeated = requireOk(projectNpcDerivedState({ prepared: prepared.prepared, worldEventLedgerJson }), "R22_DERIVED_STATE_PROJECT_FAILED");
  const documents = ephemeralProjectionDocuments(first);
  const repeatedDocuments = ephemeralProjectionDocuments(repeated);
  if (!validateEphemeralProjectionDocuments(documents) || !same(documents, repeatedDocuments)) {
    fail("R22_DERIVED_STATE_REBUILD_MISMATCH");
  }
  const artifacts = ephemeralProjectionArtifacts(documents);
  const projectionDocumentJson = canonicalText(artifacts);
  return Object.freeze({
    documents,
    artifacts,
    hashes: ephemeralProjectionHashes(documents),
    ephemeralProjectionSha256: sha256(projectionDocumentJson),
  });
}

function createEphemeralAuthoritySessionManifest(source, authority) {
  const ledger = JSON.parse(authority.canonicalWorldEventLedgerJson);
  const manifestJson = canonicalText({
    format: "matrix-oasis.r22-ephemeral-authority-session-manifest",
    formatVersion: "0.1.0",
    canonicalization: NPC_COGNITION_CANONICALIZATION,
    source: {
      baseQualifiedR20CurrentSha256: source.npc.record.sha256,
      baseQualifiedR20AuthorityManifestSha256: source.current.manifestSha256,
      baseQualifiedR21BundleSha256: source.derived.record.sha256,
    },
    identities: {
      runtimeGamePackSha256: sha256(source.runtimeGamePackJson),
      runtimeReceiptSha256: sha256(source.runtimeReceiptJson),
      authorityPolicySha256: sha256(source.authorityPolicyJson),
      behaviorPolicySha256: sha256(source.behaviorPolicyJson),
      npcEntityBindingSha256: sha256(source.npcEntityBindingJson),
    },
    timeline: {
      id: ledger.timeline.id,
      revision: ledger.revision,
      headSha256: ledger.headSha256,
      ledgerSha256: sha256(authority.canonicalWorldEventLedgerJson),
      runtimeSnapshotSha256: sha256(canonicalText(authority.runtimeSnapshot)),
    },
  });
  return Object.freeze({ manifestJson, manifestSha256: sha256(manifestJson) });
}

function validateEphemeralAuthoritySessionManifest(manifestJson, source, finalLedger) {
  const manifest = requireCanonicalArtifact(
    manifestJson,
    "R22_EPHEMERAL_AUTHORITY_SESSION_INVALID",
    "matrix-oasis.r22-ephemeral-authority-session-manifest",
  );
  const initialLedger = canonicalText({
    authority: finalLedger.authority,
    canonicalization: finalLedger.canonicalization,
    entries: [],
    format: finalLedger.format,
    formatVersion: finalLedger.formatVersion,
    headSha256: null,
    revision: 0,
    timeline: finalLedger.timeline,
  });
  if (!exact(manifest, ["format", "formatVersion", "canonicalization", "source", "identities", "timeline"]) ||
      manifest.formatVersion !== "0.1.0" || manifest.canonicalization !== NPC_COGNITION_CANONICALIZATION ||
      !exact(manifest.source, ["baseQualifiedR20CurrentSha256", "baseQualifiedR20AuthorityManifestSha256", "baseQualifiedR21BundleSha256"]) ||
      manifest.source.baseQualifiedR20CurrentSha256 !== source.npc.record.sha256 ||
      manifest.source.baseQualifiedR20AuthorityManifestSha256 !== source.current.manifestSha256 ||
      manifest.source.baseQualifiedR21BundleSha256 !== source.derived.record.sha256 ||
      !exact(manifest.identities, ["runtimeGamePackSha256", "runtimeReceiptSha256", "authorityPolicySha256", "behaviorPolicySha256", "npcEntityBindingSha256"]) ||
      manifest.identities.runtimeGamePackSha256 !== sha256(source.runtimeGamePackJson) ||
      manifest.identities.runtimeReceiptSha256 !== sha256(source.runtimeReceiptJson) ||
      manifest.identities.authorityPolicySha256 !== sha256(source.authorityPolicyJson) ||
      manifest.identities.behaviorPolicySha256 !== sha256(source.behaviorPolicyJson) ||
      manifest.identities.npcEntityBindingSha256 !== sha256(source.npcEntityBindingJson) ||
      !exact(manifest.timeline, ["id", "revision", "headSha256", "ledgerSha256", "runtimeSnapshotSha256"]) ||
      manifest.timeline.id !== finalLedger.timeline.id || manifest.timeline.revision !== 0 || manifest.timeline.headSha256 !== null ||
      manifest.timeline.ledgerSha256 !== sha256(initialLedger) ||
      manifest.timeline.runtimeSnapshotSha256 !== finalLedger.authority.initialSnapshotSha256) {
    fail("R22_EPHEMERAL_AUTHORITY_SESSION_INVALID");
  }
  return Object.freeze({ manifest, manifestSha256: sha256(manifestJson) });
}

function validateEphemeralProjectionEvidence(projection, { baseQualifiedR21BundleSha256,
  baseQualifiedR21LedgerSha256 = null, authoritySessionManifestSha256, finalWorldEventLedgerJson }) {
  const keys = ["format", "formatVersion", "canonicalization", "baseQualifiedR21BundleSha256",
    "baseQualifiedR21LedgerSha256", "ephemeralAuthoritySessionManifestSha256", "freshTimelineId",
    "freshRevision", "freshHeadSha256", "freshLedgerSha256", "initialEphemeralProjectionSha256",
    "ephemeralProjectionSha256", "artifacts", "artifactSha256", "rebuildCount", "byteIdentical", "r21Qualified"];
  const artifactKeys = ["worldEventLedgerReplayReportJson", "npcMemoryProjectionJson",
    "npcRelationshipProjectionJson", "memoryDerivedProjectionManifestJson", "relationshipDerivedProjectionManifestJson"];
  const hashKeys = ["replayReportSha256", "memoryProjectionSha256", "relationshipProjectionSha256",
    "memoryManifestSha256", "relationshipManifestSha256"];
  const ledger = JSON.parse(finalWorldEventLedgerJson);
  if (!exact(projection, keys) || projection.formatVersion !== "0.1.0" ||
      projection.canonicalization !== NPC_COGNITION_CANONICALIZATION ||
      projection.baseQualifiedR21BundleSha256 !== baseQualifiedR21BundleSha256 ||
      (baseQualifiedR21LedgerSha256 !== null && projection.baseQualifiedR21LedgerSha256 !== baseQualifiedR21LedgerSha256) ||
      projection.ephemeralAuthoritySessionManifestSha256 !== authoritySessionManifestSha256 ||
      projection.freshTimelineId !== ledger.timeline.id || projection.freshRevision !== ledger.revision ||
      projection.freshHeadSha256 !== ledger.headSha256 || projection.freshLedgerSha256 !== sha256(finalWorldEventLedgerJson) ||
      !SHA.test(projection.initialEphemeralProjectionSha256 ?? "") || !SHA.test(projection.ephemeralProjectionSha256 ?? "") ||
      projection.rebuildCount !== 2 || projection.byteIdentical !== true || projection.r21Qualified !== false ||
      !exact(projection.artifacts, artifactKeys) || !exact(projection.artifactSha256, hashKeys)) {
    fail("R22_EPHEMERAL_DERIVED_CONTEXT_INVALID");
  }
  const documents = {
    replayReportJson: projection.artifacts.worldEventLedgerReplayReportJson,
    memoryProjectionJson: projection.artifacts.npcMemoryProjectionJson,
    relationshipProjectionJson: projection.artifacts.npcRelationshipProjectionJson,
    memoryManifestJson: projection.artifacts.memoryDerivedProjectionManifestJson,
    relationshipManifestJson: projection.artifacts.relationshipDerivedProjectionManifestJson,
  };
  if (!validateEphemeralProjectionDocuments(documents) ||
      !same(projection.artifactSha256, ephemeralProjectionHashes(documents)) ||
      projection.ephemeralProjectionSha256 !== sha256(canonicalText(projection.artifacts))) {
    fail("R22_EPHEMERAL_DERIVED_CONTEXT_INVALID");
  }
  const replay = JSON.parse(documents.replayReportJson);
  const memory = JSON.parse(documents.memoryProjectionJson);
  const relationship = JSON.parse(documents.relationshipProjectionJson);
  const memoryManifest = JSON.parse(documents.memoryManifestJson);
  const relationshipManifest = JSON.parse(documents.relationshipManifestJson);
  const expectedLedgerIdentity = {
    canonicalSha256: projection.freshLedgerSha256,
    throughHeadSha256: projection.freshHeadSha256,
    throughRevision: projection.freshRevision,
    timelineId: projection.freshTimelineId,
  };
  if (replay.ledgerSha256 !== projection.freshLedgerSha256 || replay.timelineId !== projection.freshTimelineId ||
      replay.throughRevision !== projection.freshRevision || replay.throughHeadSha256 !== projection.freshHeadSha256 ||
      !same(memory.ledger, expectedLedgerIdentity) || !same(relationship.ledger, expectedLedgerIdentity) ||
      !same(memoryManifest.ledger, expectedLedgerIdentity) || !same(relationshipManifest.ledger, expectedLedgerIdentity) ||
      memoryManifest.artifact.sha256 !== projection.artifactSha256.memoryProjectionSha256 ||
      relationshipManifest.artifact.sha256 !== projection.artifactSha256.relationshipProjectionSha256) {
    fail("R22_EPHEMERAL_DERIVED_CONTEXT_INVALID");
  }
  return Object.freeze({ documents, ledger });
}

function requireControllerRecord(text, format, keys) {
  const value = requireCanonicalArtifact(text, "R22_OFFLINE_GODOT_BOUNDARY_INVALID", format);
  if (!exact(value, keys) || value.formatVersion !== "0.1.0" ||
      value.canonicalization !== NPC_COGNITION_CANONICALIZATION) {
    fail("R22_OFFLINE_GODOT_BOUNDARY_INVALID");
  }
  return value;
}

function validateOfflineGodotBoundary(godot, {
  authoritySessionManifestSha256,
  source = null,
  turnRecords = null,
  finalWorldEventLedgerJson = null,
}) {
  const keys = ["format", "formatVersion", "canonicalization", "r22GodotExecuted", "r22GodotQualified",
    "r20ControllerRoutesExecuted", "displayedAckPersisted", "arrivalEvidenceKind", "physicalMovementVerified",
    "ephemeralAuthoritySessionManifestSha256", "executedAuthorityRoutes", "commandSha256", "displayAckSha256",
    "executedCommand", "dispatchRecordJson", "validatedProposalRecordJson", "displayAckRecordJson",
    "finalizedTurnReceiptSha256", "sourceR20QualificationVerified", "sourceR20GodotTraceSha256", "sourceR20BridgeReportSha256"];
  if (!exact(godot, keys) || godot.formatVersion !== "0.1.0" ||
      godot.canonicalization !== NPC_COGNITION_CANONICALIZATION || godot.r22GodotExecuted !== false ||
      godot.r22GodotQualified !== false || godot.r20ControllerRoutesExecuted !== true ||
      godot.displayedAckPersisted !== true || godot.arrivalEvidenceKind !== "offline-protocol-fixture" ||
      godot.physicalMovementVerified !== false || godot.ephemeralAuthoritySessionManifestSha256 !== authoritySessionManifestSha256 ||
      !same(godot.executedAuthorityRoutes, ["GET /v1/command", "POST /v1/arrived", "POST /v1/mirror"]) ||
      !SHA.test(godot.commandSha256 ?? "") || !SHA.test(godot.displayAckSha256 ?? "") ||
      !SHA.test(godot.finalizedTurnReceiptSha256 ?? "") || godot.sourceR20QualificationVerified !== true ||
      !SHA.test(godot.sourceR20GodotTraceSha256 ?? "") || !SHA.test(godot.sourceR20BridgeReportSha256 ?? "") ||
      (source !== null && (godot.sourceR20GodotTraceSha256 !== sha256(source.r20Godot.godotTraceJson) ||
        godot.sourceR20BridgeReportSha256 !== sha256(source.r20Godot.bridgeReportJson)))) {
    fail("R22_OFFLINE_GODOT_BOUNDARY_INVALID");
  }
  const command = godot.executedCommand;
  if (!exact(command, ["sequence", "actorEntityId", "ruleIndex", "nodeId", "actionId", "intentId", "npcIntentJson"]) ||
      !Number.isSafeInteger(command.sequence) || command.sequence < 1 ||
      !Number.isSafeInteger(command.ruleIndex) || command.ruleIndex < 0 ||
      ![command.actorEntityId, command.nodeId, command.actionId, command.intentId].every((value) => ID.test(value ?? "")) ||
      !valid(validateNpcIntentJson(command.npcIntentJson)) || godot.commandSha256 !== sha256(canonicalText(command))) {
    fail("R22_OFFLINE_GODOT_BOUNDARY_INVALID");
  }
  const intent = JSON.parse(command.npcIntentJson);
  if (intent.id !== command.intentId || intent.actorEntityId !== command.actorEntityId ||
      intent.nodeId !== command.nodeId || intent.actionId !== command.actionId) {
    fail("R22_OFFLINE_GODOT_BOUNDARY_INVALID");
  }
  const dispatch = requireControllerRecord(godot.dispatchRecordJson, "matrix-oasis.r22-dispatch-record",
    ["format", "formatVersion", "canonicalization", "callPlanSha256", "turnSha256", "approvalTokenSha256",
      "approvalContentSha256", "reservationMicrousd", "providerRequestLimit", "providerRetryLimit"]);
  const validated = requireControllerRecord(godot.validatedProposalRecordJson, "matrix-oasis.r22-validated-proposal-record",
    ["format", "formatVersion", "canonicalization", "callPlanSha256", "proposalSha256", "returnedModel", "usage",
      "actualMicrousd", "actionChoiceId", "mappedIntentId", "mappedIntentSha256"]);
  const displayAck = requireControllerRecord(godot.displayAckRecordJson, "matrix-oasis.r22-display-ack-record",
    ["format", "formatVersion", "canonicalization", "callPlanSha256", "proposalSha256", "displayAckSha256", "state"]);
  if (!SHA.test(dispatch.callPlanSha256 ?? "") || !SHA.test(dispatch.turnSha256 ?? "") ||
      !SHA.test(dispatch.approvalTokenSha256 ?? "") || !SHA.test(dispatch.approvalContentSha256 ?? "") ||
      dispatch.reservationMicrousd !== NPC_COGNITION_LIMITS.perCallMicrousd ||
      dispatch.providerRequestLimit !== 1 || dispatch.providerRetryLimit !== 0 ||
      validated.callPlanSha256 !== dispatch.callPlanSha256 || !SHA.test(validated.proposalSha256 ?? "") ||
      validated.mappedIntentId !== command.intentId || validated.mappedIntentSha256 !== sha256(command.npcIntentJson) ||
      displayAck.state !== "displayed" || displayAck.callPlanSha256 !== dispatch.callPlanSha256 ||
      displayAck.proposalSha256 !== validated.proposalSha256 || displayAck.displayAckSha256 !== godot.displayAckSha256 ||
      displayAck.displayAckSha256 !== computeR22DisplayAckHash({
        approvalTokenSha256: dispatch.approvalTokenSha256,
        turnSha256: dispatch.turnSha256,
        callPlanSha256: dispatch.callPlanSha256,
        proposalSha256: validated.proposalSha256,
        actionChoiceId: validated.actionChoiceId,
      })) {
    fail("R22_OFFLINE_GODOT_BOUNDARY_INVALID");
  }
  if (turnRecords !== null && finalWorldEventLedgerJson !== null) {
    const adjudicated = turnRecords.filter(({ turnReceiptJson }) => JSON.parse(turnReceiptJson).statusHistory.includes("adjudicated"));
    if (adjudicated.length !== 1) fail("R22_OFFLINE_GODOT_BOUNDARY_INVALID");
    const record = adjudicated[0];
    const plan = JSON.parse(record.callPlanJson);
    const receipt = JSON.parse(record.turnReceiptJson);
    const ledger = JSON.parse(finalWorldEventLedgerJson);
    const matchingChoice = plan.candidateChoices.find(({ choiceId }) => choiceId === receipt.actionChoiceId);
    if (record.actorEntityId !== command.actorEntityId || sha256(record.callPlanJson) !== dispatch.callPlanSha256 ||
        plan.turnSha256 !== dispatch.turnSha256 || plan.approval.hash !== dispatch.approvalContentSha256 ||
        receipt.callPlanSha256 !== dispatch.callPlanSha256 || receipt.turnSha256 !== dispatch.turnSha256 ||
        receipt.proposalSha256 !== validated.proposalSha256 || receipt.returnedModel !== validated.returnedModel ||
        receipt.budget.actualMicrousd !== validated.actualMicrousd || !same(receipt.usage, validated.usage) ||
        receipt.actionChoiceId !== validated.actionChoiceId || receipt.mappedIntentSha256 !== validated.mappedIntentSha256 ||
        matchingChoice?.intentSha256 !== sha256(command.npcIntentJson) ||
        godot.finalizedTurnReceiptSha256 !== sha256(record.turnReceiptJson) || ledger.entries.length !== 1 ||
        ledger.entries[0].decision.status !== "accepted" || !same(ledger.entries[0].intent, intent)) {
      fail("R22_OFFLINE_GODOT_BOUNDARY_INVALID");
    }
  }
  return Object.freeze({ command });
}

function validateOfflinePerformanceBoundary(performance) {
  if (!exact(performance, ["format", "formatVersion", "canonicalization", "r22PerformanceMeasured",
    "r22PerformanceQualified", "sourceR20PerformanceOnly"]) || performance.formatVersion !== "0.1.0" ||
    performance.canonicalization !== NPC_COGNITION_CANONICALIZATION || performance.r22PerformanceMeasured !== false ||
    performance.r22PerformanceQualified !== false || !performance.sourceR20PerformanceOnly ||
    typeof performance.sourceR20PerformanceOnly !== "object" || Array.isArray(performance.sourceR20PerformanceOnly)) {
    fail("R22_OFFLINE_PERFORMANCE_BOUNDARY_INVALID");
  }
}

function fakeResponseEnvelope(callPlan, providerBody, actionChoiceId) {
  const proposal = { contextSha256: callPlan.contextSha256, dialogueText: "Offline bounded dialogue.", actionChoiceId };
  return {
    id: "resp_discarded", object: "response", created_at: 1, completed_at: 2, status: "completed",
    background: false, error: null, incomplete_details: null, instructions: providerBody.instructions,
    max_output_tokens: callPlan.maxOutputTokens, max_tool_calls: null, metadata: {}, model: callPlan.model,
    output: [{ id: "msg_discarded", type: "message", status: "completed", role: "assistant",
      content: [{ type: "output_text", text: JSON.stringify(proposal), annotations: [] }] }],
    usage: { input_tokens: 200, input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 },
      output_tokens: 50, output_tokens_details: { reasoning_tokens: 0 }, total_tokens: 250 },
    parallel_tool_calls: true, previous_response_id: null, store: false, tool_choice: "auto", tools: [], truncation: "disabled",
  };
}

function traceFromRecords({ timelineId, policyJson, records }) {
  const parsed = records.map((record) => ({ plan: JSON.parse(record.callPlanJson), receipt: JSON.parse(record.turnReceiptJson) }));
  const first = parsed[0]?.receipt; if (!first) fail("R22_OFFLINE_TRACE_EMPTY");
  const receipts = parsed.map(({ plan, receipt }, index) => ({
    sequence: index + 1, actorEntityId: records[index].actorEntityId, turnSha256: plan.turnSha256,
    callPlanSha256: sha256(records[index].callPlanJson), turnReceiptSha256: sha256(records[index].turnReceiptJson),
    requestCount: receipt.requestCount, actualMicrousd: receipt.budget.actualMicrousd,
    outcome: receiptOutcome(receipt), actionChoiceId: receipt.actionChoiceId,
    beforeLedger: receipt.ledger.before, afterLedger: receipt.ledger.after, interveningAuthorityEntrySha256: [],
  }));
  const trace = {
    format: "matrix-oasis.npc-cognition-trace", formatVersion: "0.1.0",
    canonicalization: NPC_COGNITION_CANONICALIZATION, timelineId, policySha256: sha256(policyJson),
    initialLedger: first.ledger.before, receipts,
    totals: {
      turns: receipts.length, providerRequests: receipts.reduce((sum, item) => sum + item.requestCount, 0),
      actualMicrousd: receipts.reduce((sum, item) => sum + item.actualMicrousd, 0),
      fallbackTurns: receipts.filter((item) => item.outcome === "fallback").length,
      actionChoiceTurns: receipts.filter((item) => item.actionChoiceId !== null).length,
      adjudicatedTurns: receipts.filter((item) => item.outcome === "adjudicated").length,
    },
    throughRevision: receipts.at(-1).afterLedger.revision, throughHeadSha256: receipts.at(-1).afterLedger.headSha256,
  };
  const text = canonicalText(trace); if (!valid(validateNpcCognitionTraceJson(text))) fail("R22_OFFLINE_TRACE_INVALID"); return text;
}

function loopbackRequest(sessionToken, method, url, body = null) {
  return Object.freeze({ remoteAddress: "127.0.0.1", method, url,
    headers: Object.freeze({ authorization: `Bearer ${sessionToken}`,
      ...(method === "POST" ? { "content-type": "application/json" } : {}) }),
    body: method === "POST" ? canonicalText(body ?? {}) : "" });
}

async function awaitLoopbackOutcome(controller, sessionToken, turnId) {
  for (let attempt = 0; attempt < 1_000; attempt += 1) {
    const response = await handleR22LoopbackRequestAsync(controller, loopbackRequest(sessionToken, "GET", `/v1/cognition/status/${turnId}`));
    const body = JSON.parse(response.body);
    if (response.statusCode === 200 && body.status !== "dispatching") return body;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  fail("R22_FAKE_PROVIDER_TURN_TIMEOUT");
}

async function readOfflineControllerClosure({ ownedPath, authoritySessionManifestSha256, sequence,
  callPlanJson, temporaryRoot, overrides }) {
  const callPlanSha256 = sha256(callPlanJson);
  const turnRoot = path.join(ownedPath, "run-cognition", "timelines",
    authoritySessionManifestSha256.slice(7), "turns",
    `${String(sequence).padStart(6, "0")}-${callPlanSha256.slice(7)}`);
  const readRecord = async (name) => decodeCanonicalR22Record(
    await readStableR22File(path.join(turnRoot, name), 1024 * 1024, temporaryRoot, overrides),
    "R22_OFFLINE_CONTROLLER_EVIDENCE_INVALID",
  ).text;
  return Object.freeze({
    dispatchRecordJson: await readRecord("dispatch-record.json"),
    validatedProposalRecordJson: await readRecord("validated-proposal-record.json"),
    displayAckRecordJson: await readRecord("display-ack-record.json"),
  });
}

export async function qualifyR22OfflineCase({ caseId, source, temporaryRoot, overrides = {} }) {
  let owned = null; let store = null; let controller = null; let primaryError = null;
  try {
    const trustedRoot = typeof temporaryRoot === "string" ? await trustR22TemporaryRoot(temporaryRoot, overrides) : temporaryRoot;
    const timelineId = `r22-${caseId}-${source.npc.record.sha256.slice(7, 23)}`;
    const authority = requireOk(await createNpcAuthoritySession({ runtimeGamePackJson: source.runtimeGamePackJson,
      runtimeReceiptJson: source.runtimeReceiptJson, policyJson: source.authorityPolicyJson, timelineId }), "R22_FRESH_TIMELINE_FAILED");
    const behavior = requireOk(prepareDeterministicNpcBehavior({ behaviorPolicyJson: source.behaviorPolicyJson,
      entityBindingJson: source.npcEntityBindingJson, authorityPolicyJson: source.authorityPolicyJson }), "R22_BEHAVIOR_PREPARE_FAILED");
    const initialEphemeralProjection = await projectEphemeralDerivedContext(source, authority.canonicalWorldEventLedgerJson);
    const authoritySession = createEphemeralAuthoritySessionManifest(source, authority);
    const synthesized = synthesizeNpcCognitionPolicyDocument({ runtimeGamePackJson: source.runtimeGamePackJson,
      runtimeReceiptJson: source.runtimeReceiptJson, authorityPolicyJson: source.authorityPolicyJson,
      behaviorPolicyJson: source.behaviorPolicyJson, npcEntityBindingJson: source.npcEntityBindingJson,
      derivedStateBundleJson: source.r21.bundleJson });
    const cognitionPolicyJson = synthesized.canonicalNpcCognitionPolicyJson;
    const cognition = requireOk(await prepareNpcCognition({ runtimeGamePackJson: source.runtimeGamePackJson,
      runtimeReceiptJson: source.runtimeReceiptJson, authorityPolicyJson: source.authorityPolicyJson,
      behaviorPolicyJson: source.behaviorPolicyJson, npcEntityBindingJson: source.npcEntityBindingJson,
      personaSeedJson: source.r21.personaSeedJson, relationshipPolicyJson: source.r21.relationshipPolicyJson,
      qualifiedWorldEventLedgerJson: source.originalWorldEventLedgerJson,
      memoryProjectionJson: source.r21.memoryProjectionJson,
      relationshipProjectionJson: source.r21.relationshipProjectionJson,
      memoryManifestJson: source.r21.memoryManifestJson,
      relationshipManifestJson: source.r21.relationshipManifestJson,
      derivedStateBundleJson: source.r21.bundleJson, cognitionPolicyJson }), "R22_COGNITION_PREPARE_FAILED");
    let actorEntityId = null;
    for (const actor of synthesized.npcCognitionPolicy.actors) {
      const candidate = createNpcCognitionTurn({ prepared: cognition.prepared, timelineId,
        actorEntityId: actor.actorEntityId, sequence: 1, runtimeSnapshot: authority.runtimeSnapshot,
        runtimeInspection: authority.inspection, worldEventLedgerJson: authority.canonicalWorldEventLedgerJson,
        behaviorState: behavior.initialState, playerText: "Offline qualification input.", transientDialogue: [] });
      if (candidate.ok && candidate.candidateActions.length > 0) { actorEntityId = actor.actorEntityId; break; }
    }
    if (actorEntityId === null) fail("R22_NO_EXECUTABLE_COGNITION_ACTION");
    owned = await createR22OwnedTemporaryDirectory(trustedRoot, "r22-offline-case", overrides);
    const sessionToken = "r22-offline-loopback-session-token-0000000000000000000000000001";
    const selectorGate = createR22CognitionSelectorGate({ commandSelector: selectNextNpcBehaviorCommand,
      queuedCommandSelector: selectEligibleNpcBehaviorCommand });
    const coordinator = createR20Coordinator({ authoritySession: authority.session, preparedBehavior: behavior.prepared,
      initialBehaviorState: behavior.initialState, entityBindingSha256: sha256(source.npcEntityBindingJson),
      sessionToken, commandSelector: selectorGate.commandSelector });
    if (coordinator === null) fail("R22_R20_COORDINATOR_FAILED");
    const initialCoordinator = exportR20Coordinator(coordinator); const initialLedger = JSON.parse(initialCoordinator.authority.canonicalWorldEventLedgerJson);
    let adapterDispatches = 0; let activeRuntime = null; let sequence = 0; const plannedTurns = new Map();
    store = await openR22CallStore({ temporaryRoot: owned.path, cognitionRunRoot: path.join(owned.path, "run-cognition"),
      hostRunId: `host-${caseId}`, timelineId, authoritySessionSha256: authoritySession.manifestSha256,
      cognitionPolicySha256: sha256(cognitionPolicyJson), initialLedgerPoint: ledgerPoint(initialCoordinator.authority.runtimeSnapshot, initialLedger) }, {
      randomBytes: () => new Uint8Array(32).fill(6),
    });
    const host = createR22TransactionalHost({ store, operations: {
      async keyReader() { return "offline-fake-key"; },
      async providerExecutor({ apiKey, callPlanJson, providerRequestJson, approvalHash }) {
        const plan = JSON.parse(callPlanJson); const body = JSON.parse(providerRequestJson);
        const selected = plan.candidateChoices[0]?.choiceId ?? null;
        const provider = createOpenAiNpcCognitionProvider({ apiKey, fetchImplementation: async (url, init) => {
          if (url !== plan.endpoint || init?.method !== "POST" || init?.body !== providerRequestJson) throw new Error("offline provider input mismatch");
          adapterDispatches += 1;
          return new Response(JSON.stringify(fakeResponseEnvelope(plan, body, selected)), { status: 200, headers: { "content-type": "application/json" } });
        } });
        return executeApprovedNpcCognitionTurn({ callPlanJson, providerRequestJson, approvalHash }, provider);
      },
      async proposalValidator({ proposalJson }) {
        if (!activeRuntime) return { ok: false, diagnosticCode: "R22_CONTEXT_STALE" };
        const snapshot = exportR20Coordinator(coordinator);
        const validated = validateNpcDialogueProposal({ prepared: cognition.prepared, turn: activeRuntime.turn.turn,
          callPlan: activeRuntime.plan.callPlan, npcDialogueProposalJson: proposalJson,
          runtimeSnapshot: snapshot.authority.runtimeSnapshot, runtimeInspection: snapshot.authority.inspection,
          worldEventLedgerJson: snapshot.authority.canonicalWorldEventLedgerJson, behaviorState: snapshot.behaviorState });
        if (!validated.ok) return validated;
        const mapped = mapNpcDialogueProposalToIntent({ prepared: cognition.prepared, turn: activeRuntime.turn.turn,
          callPlan: activeRuntime.plan.callPlan, validatedProposal: validated.validatedProposal,
          runtimeSnapshot: snapshot.authority.runtimeSnapshot, runtimeInspection: snapshot.authority.inspection,
          worldEventLedgerJson: snapshot.authority.canonicalWorldEventLedgerJson, behaviorState: snapshot.behaviorState });
        if (!mapped.ok) return mapped; activeRuntime.mapped = mapped;
        return { ok: true, canonicalNpcDialogueProposalJson: validated.canonicalNpcDialogueProposalJson,
          dialogueText: validated.dialogueText, actionChoiceId: validated.actionChoiceId,
          mappedIntentSha256: mapped.npcIntentJson === null ? null : sha256(mapped.npcIntentJson), command: mapped.command };
      },
      async adjudicationLookup() { return { found: false }; },
    }, clock: () => 1_000_000, randomBytesImplementation: () => new Uint8Array(32).fill(7) });
    const turnFactory = async ({ actorEntityId: requestedActor, playerText, transientDialogue }) => {
      const snapshot = exportR20Coordinator(coordinator); sequence += 1;
      const turn = requireOk(createNpcCognitionTurn({ prepared: cognition.prepared, timelineId,
        actorEntityId: requestedActor, sequence, runtimeSnapshot: snapshot.authority.runtimeSnapshot,
        runtimeInspection: snapshot.authority.inspection, worldEventLedgerJson: snapshot.authority.canonicalWorldEventLedgerJson,
        behaviorState: snapshot.behaviorState, playerText, transientDialogue }), "R22_ACTION_TURN_FAILED");
      if (turn.candidateActions.length < 1) fail("R22_NO_EXECUTABLE_COGNITION_ACTION");
      const plan = requireOk(planNpcCognitionCall({ prepared: cognition.prepared, turn: turn.turn }), "R22_CALL_PLAN_FAILED");
      activeRuntime = { turn, plan, mapped: null }; plannedTurns.set(turn.npcCognitionTurnRequest.id, { turn, plan });
      return Object.freeze({ turnId: turn.npcCognitionTurnRequest.id, sequence,
        actorEntityId: requestedActor, callPlanJson: plan.canonicalNpcCognitionCallPlanJson,
        providerRequestJson: plan.providerPayloadJson,
        beforeLedgerPoint: ledgerPoint(snapshot.authority.runtimeSnapshot, JSON.parse(snapshot.authority.canonicalWorldEventLedgerJson)) });
    };
    const inertScheduler = () => () => {};
    controller = createR22LoopbackController({ host, selectorGate, sessionToken, turnFactory,
      authorityRequestHandler: (request) => handleR20CoordinatorRequestAsync(coordinator, request),
      authorityStateReader: async () => exportR20Coordinator(coordinator),
      displayScheduler: inertScheduler, planningScheduler: inertScheduler });
    const route = async (method, url, body = null, expectedStatus = 200) => {
      const response = await handleR22LoopbackRequestAsync(controller, loopbackRequest(sessionToken, method, url, body));
      if (response.statusCode !== expectedStatus) fail("R22_LOOPBACK_ROUTE_FAILED");
      return JSON.parse(response.body);
    };
    const declinedPlan = await route("POST", "/v1/cognition/turn", { actorEntityId, playerText: "Offline qualification input." });
    await route("POST", "/v1/cognition/decline", { turnId: declinedPlan.turnId, approvalHash: declinedPlan.approvalHash });
    const declined = plannedTurns.get(declinedPlan.turnId);
    if (!declined) fail("R22_CALL_PLAN_FAILED");
    const declinedArtifacts = requireOk(await readFinalizedR22CognitionTurn(host, {
      timelineId, turnId: declinedPlan.turnId,
      callPlanSha256: sha256(declined.plan.canonicalNpcCognitionCallPlanJson),
    }), "R22_DECLINE_FAILED");
    const actionRegistration = await route("POST", "/v1/cognition/turn", { actorEntityId, playerText: "Offline qualification input." });
    await route("POST", "/v1/cognition/approve", { turnId: actionRegistration.turnId, approvalHash: actionRegistration.approvalHash });
    const displayedOutcome = await awaitLoopbackOutcome(controller, sessionToken, actionRegistration.turnId);
    if (displayedOutcome.status !== "queued_for_r20" || !SHA.test(displayedOutcome.displayAckHash ?? "")) fail("R22_R20_COMMAND_MAPPING_FAILED");
    await route("POST", "/v1/cognition/displayed", { turnId: actionRegistration.turnId, displayAckHash: displayedOutcome.displayAckHash });
    const selected = await route("GET", "/v1/command");
    if (selected.status !== "command" || !activeRuntime?.mapped?.ok ||
        selected.command.intentId !== activeRuntime.mapped.command.intentId) fail("R22_R20_COMMAND_MAPPING_FAILED");
    const arrived = await route("POST", "/v1/arrived", { sequence: selected.command.sequence,
      pathComplete: true, floorVerified: true, capsuleVerified: true, domainVerified: true,
      movementTicks: 0, pathLengthMm: 0 });
    if (arrived.status !== "adjudicated" || arrived.decision !== "accepted") fail("R22_R19_ADJUDICATION_FAILED");
    const mirrored = await route("POST", "/v1/mirror", { sequence: selected.command.sequence,
      beforeSnapshotSha256: arrived.beforeSnapshotSha256, afterSnapshotSha256: arrived.afterSnapshotSha256 });
    if (mirrored.status !== "committed") fail("R22_R20_MIRROR_FAILED");
    const action = plannedTurns.get(actionRegistration.turnId);
    if (!action) fail("R22_CALL_PLAN_FAILED");
    const completedArtifacts = requireOk(await readFinalizedR22CognitionTurn(host, {
      timelineId, turnId: actionRegistration.turnId,
      callPlanSha256: sha256(action.plan.canonicalNpcCognitionCallPlanJson),
    }), "R22_RECEIPT_FINALIZATION_FAILED");
    const controllerClosure = await readOfflineControllerClosure({
      ownedPath: owned.path,
      authoritySessionManifestSha256: authoritySession.manifestSha256,
      sequence: action.turn.npcCognitionTurnRequest.sequence,
      callPlanJson: action.plan.canonicalNpcCognitionCallPlanJson,
      temporaryRoot: trustedRoot,
      overrides,
    });
    requireOk(verifyNpcAuthoritySession(authority.session), "R22_R19_REPLAY_FAILED");
    const finalCoordinator = exportR20Coordinator(coordinator);
    const runtimeSnapshot = finalCoordinator.authority.runtimeSnapshot;
    const worldEventLedgerJson = finalCoordinator.authority.canonicalWorldEventLedgerJson;
    const finalLedger = JSON.parse(worldEventLedgerJson);
    const turnRecords = Object.freeze([
      Object.freeze({ actorEntityId, callPlanJson: declined.plan.canonicalNpcCognitionCallPlanJson, turnReceiptJson: declinedArtifacts.turnReceiptJson }),
      Object.freeze({ actorEntityId, callPlanJson: action.plan.canonicalNpcCognitionCallPlanJson, turnReceiptJson: completedArtifacts.turnReceiptJson }),
    ]);
    const traceJson = traceFromRecords({ timelineId, policyJson: cognitionPolicyJson, records: turnRecords });
    const replay = requireOk(replayNpcCognitionEvidence({ prepared: cognition.prepared, worldEventLedgerJson,
      cognitionTraceJson: traceJson,
      turnRecords: turnRecords.map(({ callPlanJson, turnReceiptJson }) => ({ callPlanJson, turnReceiptJson })) }), "R22_COGNITION_REPLAY_FAILED");
    const postDerived = await projectEphemeralDerivedContext(source, worldEventLedgerJson);
    const repeatedPostDerived = await projectEphemeralDerivedContext(source, worldEventLedgerJson);
    if (!same(postDerived, repeatedPostDerived)) fail("R22_DERIVED_STATE_REBUILD_MISMATCH");
    const rebuildEvidenceJson = canonicalText({
      format: "matrix-oasis.r22-ephemeral-derived-context-evidence", formatVersion: "0.1.0",
      canonicalization: NPC_COGNITION_CANONICALIZATION,
      baseQualifiedR21BundleSha256: source.derived.record.sha256,
      baseQualifiedR21LedgerSha256: sha256(source.originalWorldEventLedgerJson),
      ephemeralAuthoritySessionManifestSha256: authoritySession.manifestSha256,
      freshTimelineId: finalLedger.timeline.id,
      freshRevision: finalLedger.revision,
      freshHeadSha256: finalLedger.headSha256,
      freshLedgerSha256: sha256(worldEventLedgerJson),
      initialEphemeralProjectionSha256: initialEphemeralProjection.ephemeralProjectionSha256,
      ephemeralProjectionSha256: postDerived.ephemeralProjectionSha256,
      artifacts: postDerived.artifacts,
      artifactSha256: postDerived.hashes,
      rebuildCount: 2,
      byteIdentical: true,
      r21Qualified: false,
    });
    const sourceGodot = JSON.parse(source.r20Godot.godotTraceJson);
    const godotEvidenceJson = canonicalText({
      format: "matrix-oasis.r22-offline-godot-evidence-boundary", formatVersion: "0.1.0",
      canonicalization: NPC_COGNITION_CANONICALIZATION, r22GodotExecuted: false, r22GodotQualified: false,
      r20ControllerRoutesExecuted: true, displayedAckPersisted: true,
      arrivalEvidenceKind: "offline-protocol-fixture", physicalMovementVerified: false,
      ephemeralAuthoritySessionManifestSha256: authoritySession.manifestSha256,
      executedAuthorityRoutes: ["GET /v1/command", "POST /v1/arrived", "POST /v1/mirror"],
      commandSha256: sha256(canonicalText(selected.command)), displayAckSha256: displayedOutcome.displayAckHash,
      executedCommand: selected.command,
      dispatchRecordJson: controllerClosure.dispatchRecordJson,
      validatedProposalRecordJson: controllerClosure.validatedProposalRecordJson,
      displayAckRecordJson: controllerClosure.displayAckRecordJson,
      finalizedTurnReceiptSha256: completedArtifacts.turnReceiptSha256,
      sourceR20QualificationVerified: true, sourceR20GodotTraceSha256: sha256(source.r20Godot.godotTraceJson),
      sourceR20BridgeReportSha256: sha256(source.r20Godot.bridgeReportJson),
    });
    const performanceEvidenceJson = canonicalText({
      format: "matrix-oasis.r22-offline-performance-evidence-boundary", formatVersion: "0.1.0",
      canonicalization: NPC_COGNITION_CANONICALIZATION, r22PerformanceMeasured: false,
      r22PerformanceQualified: false, sourceR20PerformanceOnly: sourceGodot.performance,
    });
    const result = Object.freeze({ caseId, policyJson: cognitionPolicyJson, traceJson,
      ephemeralAuthoritySessionManifestJson: authoritySession.manifestJson,
      worldEventLedgerReplayReportJson: replay.canonicalWorldEventLedgerReplayReportJson,
      projectionQualificationReportJson: rebuildEvidenceJson, godotEvidenceJson, performanceEvidenceJson,
      finalWorldEventLedgerJson: worldEventLedgerJson, turnRecords, adapterDispatches });
    return validateCaseResult(result, caseId, source);
  } catch (error) {
    primaryError = error; throw error;
  } finally {
    if (controller !== null) {
      try { await closeR22LoopbackController(controller); } catch (error) { if (primaryError === null) throw error; }
    }
    if (store !== null) {
      try { await closeR22CallStore(store); } catch (error) { if (primaryError === null) throw error; }
    }
    if (owned !== null) {
      try { await removeR22OwnedTemporaryDirectory(owned.handle); } catch (error) { if (primaryError === null) throw error; }
    }
  }
}

function validateTurnRecords(turnRecords, trace) {
  if (!Array.isArray(turnRecords) || turnRecords.length !== trace.receipts.length || turnRecords.length < 2) fail("R22_OFFLINE_CASE_RESULT_INVALID");
  let requests = 0; let cost = 0;
  for (let index = 0; index < turnRecords.length; index += 1) {
    const record = turnRecords[index];
    if (!exact(record, ["actorEntityId", "callPlanJson", "turnReceiptJson"]) || typeof record.actorEntityId !== "string" ||
        !valid(validateNpcCognitionCallPlanJson(record.callPlanJson)) || !valid(validateNpcCognitionTurnReceiptJson(record.turnReceiptJson))) fail("R22_OFFLINE_CASE_RESULT_INVALID");
    const plan = JSON.parse(record.callPlanJson); const receipt = JSON.parse(record.turnReceiptJson); const entry = trace.receipts[index];
    if (entry.actorEntityId !== record.actorEntityId || entry.turnSha256 !== plan.turnSha256 ||
        entry.callPlanSha256 !== sha256(record.callPlanJson) || entry.turnReceiptSha256 !== sha256(record.turnReceiptJson) ||
        entry.requestCount !== receipt.requestCount || entry.actualMicrousd !== receipt.budget.actualMicrousd ||
        entry.outcome !== receiptOutcome(receipt) || entry.actionChoiceId !== receipt.actionChoiceId ||
        !same(entry.beforeLedger, receipt.ledger.before) || !same(entry.afterLedger, receipt.ledger.after)) fail("R22_OFFLINE_CASE_RESULT_INVALID");
    requests += receipt.requestCount; cost += receipt.budget.actualMicrousd;
  }
  if (requests !== trace.totals.providerRequests || cost !== trace.totals.actualMicrousd) fail("R22_OFFLINE_CASE_RESULT_INVALID");
  return Object.freeze({ requests, cost });
}

function validateCaseResult(result, caseId, source) {
  const keys = ["caseId", "policyJson", "traceJson", "worldEventLedgerReplayReportJson",
    "projectionQualificationReportJson", "godotEvidenceJson", "performanceEvidenceJson",
    "ephemeralAuthoritySessionManifestJson", "finalWorldEventLedgerJson", "turnRecords", "adapterDispatches"];
  if (!exact(result, keys) || result.caseId !== caseId || !Number.isSafeInteger(result.adapterDispatches) || result.adapterDispatches < 1) fail("R22_OFFLINE_CASE_RESULT_INVALID");
  if (!valid(validateNpcCognitionPolicyJson(result.policyJson)) || !valid(validateNpcCognitionTraceJson(result.traceJson)) ||
      !valid(validateWorldEventLedgerReplayReportJson(result.worldEventLedgerReplayReportJson)) ||
      !valid(validateWorldEventLedgerJson(result.finalWorldEventLedgerJson))) fail("R22_OFFLINE_CASE_RESULT_INVALID");
  const policy = JSON.parse(result.policyJson); const trace = JSON.parse(result.traceJson); const ledger = JSON.parse(result.finalWorldEventLedgerJson);
  const totals = validateTurnRecords(result.turnRecords, trace);
  const projection = requireCanonicalArtifact(result.projectionQualificationReportJson, "R22_OFFLINE_CASE_RESULT_INVALID", "matrix-oasis.r22-ephemeral-derived-context-evidence");
  const godot = requireCanonicalArtifact(result.godotEvidenceJson, "R22_OFFLINE_CASE_RESULT_INVALID", "matrix-oasis.r22-offline-godot-evidence-boundary");
  const performance = requireCanonicalArtifact(result.performanceEvidenceJson, "R22_OFFLINE_CASE_RESULT_INVALID", "matrix-oasis.r22-offline-performance-evidence-boundary");
  const authoritySession = validateEphemeralAuthoritySessionManifest(result.ephemeralAuthoritySessionManifestJson, source, ledger);
  validateEphemeralProjectionEvidence(projection, {
    baseQualifiedR21BundleSha256: source.derived.record.sha256,
    baseQualifiedR21LedgerSha256: sha256(source.originalWorldEventLedgerJson),
    authoritySessionManifestSha256: authoritySession.manifestSha256,
    finalWorldEventLedgerJson: result.finalWorldEventLedgerJson,
  });
  validateOfflineGodotBoundary(godot, {
    authoritySessionManifestSha256: authoritySession.manifestSha256,
    source,
    turnRecords: result.turnRecords,
    finalWorldEventLedgerJson: result.finalWorldEventLedgerJson,
  });
  validateOfflinePerformanceBoundary(performance);
  const sourceR20Godot = JSON.parse(source.r20Godot.godotTraceJson);
  if (policy.identities.derivedStateBundleSha256 !== projection.baseQualifiedR21BundleSha256 ||
      trace.policySha256 !== sha256(result.policyJson) || trace.timelineId !== ledger.timeline.id ||
      trace.throughRevision !== ledger.revision || trace.throughHeadSha256 !== ledger.headSha256 ||
      projection.baseQualifiedR21BundleSha256 !== source.derived.record.sha256 ||
      projection.baseQualifiedR21LedgerSha256 !== sha256(source.originalWorldEventLedgerJson) ||
      projection.ephemeralAuthoritySessionManifestSha256 !== authoritySession.manifestSha256 ||
      projection.freshLedgerSha256 !== sha256(result.finalWorldEventLedgerJson) ||
      projection.freshTimelineId !== ledger.timeline.id ||
      projection.freshRevision !== ledger.revision || projection.freshHeadSha256 !== ledger.headSha256 ||
      projection.rebuildCount !== 2 || projection.byteIdentical !== true ||
      projection.r21Qualified !== false || !SHA.test(projection.ephemeralProjectionSha256 ?? "") ||
      godot.r22GodotExecuted !== false || godot.r22GodotQualified !== false ||
      godot.r20ControllerRoutesExecuted !== true || godot.displayedAckPersisted !== true ||
      godot.arrivalEvidenceKind !== "offline-protocol-fixture" || godot.physicalMovementVerified !== false ||
      !same(godot.executedAuthorityRoutes, ["GET /v1/command", "POST /v1/arrived", "POST /v1/mirror"]) ||
      godot.ephemeralAuthoritySessionManifestSha256 !== authoritySession.manifestSha256 ||
      performance.r22PerformanceMeasured !== false || performance.r22PerformanceQualified !== false ||
      !same(performance.sourceR20PerformanceOnly, sourceR20Godot.performance) ||
      totals.requests !== result.adapterDispatches || trace.totals.fallbackTurns < 1 || trace.totals.adjudicatedTurns !== 1) fail("R22_OFFLINE_CASE_IDENTITY_MISMATCH");
  return Object.freeze(result);
}

function evidenceFor(caseResult, source) {
  const trace = JSON.parse(caseResult.traceJson);
  return {
    format: "matrix-oasis.r22-offline-case-evidence", formatVersion: "0.2.0",
    canonicalization: NPC_COGNITION_CANONICALIZATION, caseId: caseResult.caseId,
    source: { kind: source.kind, npcCurrentSha256: source.npc.record.sha256, derivedStateBundleSha256: source.derived.record.sha256 },
    policyJson: caseResult.policyJson, traceJson: caseResult.traceJson,
    ephemeralAuthoritySessionManifestJson: caseResult.ephemeralAuthoritySessionManifestJson,
    worldEventLedgerReplayReportJson: caseResult.worldEventLedgerReplayReportJson,
    projectionQualificationReportJson: caseResult.projectionQualificationReportJson,
    godotEvidenceJson: caseResult.godotEvidenceJson, performanceEvidenceJson: caseResult.performanceEvidenceJson,
    finalWorldEventLedgerJson: caseResult.finalWorldEventLedgerJson, turnRecords: caseResult.turnRecords,
    provider: { fakeAdapterDispatches: trace.totals.providerRequests, actualMicrousd: trace.totals.actualMicrousd, realRequests: 0, replayRequests: 0 },
    sideEffects: { externalNetworkRequests: 0, environmentCredentialReads: 0 }, redaction: { ...REDACTION },
  };
}

export function buildR22OfflineQualificationArtifacts({ spec, cases }) {
  if (!spec || !Array.isArray(cases) || cases.length !== spec.cases.length) fail("R22_OFFLINE_CASE_SET_INVALID");
  const normalized = cases.map((entry) => {
    if (!entry?.source?.npc?.record || !entry?.source?.derived?.record ||
        !SHA.test(entry.source.npc.record.sha256 ?? "") || !SHA.test(entry.source.derived.record.sha256 ?? "")) fail("R22_OFFLINE_CASE_SET_INVALID");
    return Object.freeze({ source: entry.source, result: validateCaseResult(entry.result, entry.result?.caseId, entry.source) });
  });
  const byId = new Map(normalized.map((entry) => [entry.result.caseId, entry]));
  if (byId.size !== cases.length || spec.cases.some(({ caseId }) => !byId.has(caseId))) fail("R22_OFFLINE_CASE_SET_INVALID");
  const ordered = spec.cases.map(({ caseId }) => byId.get(caseId));
  const evidenceDocuments = ordered.map(({ result, source }) => canonicalText(evidenceFor(result, source)));
  const primary = byId.get(spec.primaryCaseId).result;
  const evidenceSetJson = canonicalText({ format: CASE_EVIDENCE_FORMAT, formatVersion: "0.2.0",
    canonicalization: NPC_COGNITION_CANONICALIZATION, primaryCaseId: spec.primaryCaseId,
    cases: evidenceDocuments.map((text) => JSON.parse(text)) });
  const report = {
    format: "matrix-oasis.npc-cognition-qualification-report", formatVersion: NPC_COGNITION_FORMAT_VERSION,
    canonicalization: NPC_COGNITION_CANONICALIZATION, profile: NPC_COGNITION_PROFILE, stage: "offline",
    policySha256: sha256(primary.policyJson), traceSha256: sha256(primary.traceJson),
    worldEventLedgerReplayReportSha256: sha256(primary.worldEventLedgerReplayReportJson),
    projectionQualificationReportSha256: sha256(primary.projectionQualificationReportJson),
    offlineCases: ordered.map(({ result }, index) => ({ caseId: result.caseId,
      evidenceSha256: sha256(evidenceDocuments[index]), traceSha256: sha256(result.traceJson) })),
    godotEvidenceSha256: sha256(primary.godotEvidenceJson), performanceEvidenceSha256: sha256(primary.performanceEvidenceJson),
    providerCalls: { fake: ordered.reduce((sum, item) => sum + JSON.parse(item.result.traceJson).totals.providerRequests, 0), real: 0, replay: 0 },
    replay: { modelOutputReproducible: false, dialogueContentRetained: false, providerReplayRequests: 0 },
    markers: [...NPC_COGNITION_QUALIFICATION_MARKERS],
  };
  const reportJson = canonicalText(report); if (!valid(validateNpcCognitionQualificationReportJson(reportJson))) fail("R22_QUALIFICATION_REPORT_INVALID");
  return new Map([
    ["godot-evidence.json", primary.godotEvidenceJson], ["npc-cognition-policy.json", primary.policyJson],
    ["npc-cognition-qualification-report.json", reportJson], ["npc-cognition-trace.json", primary.traceJson],
    ["npc-projection-qualification-report.json", primary.projectionQualificationReportJson],
    ["offline-case-evidence.json", evidenceSetJson], ["performance-evidence.json", primary.performanceEvidenceJson],
    ["world-event-ledger-replay-report.json", primary.worldEventLedgerReplayReportJson],
  ]);
}

export function parseR22QualificationArguments(args, trustedRoot) {
  const values = parseR22Pairs(args, { "--case-spec": "caseSpec", "--output": "output" }, ["caseSpec", "output"]);
  return Object.freeze({ caseSpec: directR22TemporaryChild(values.caseSpec, trustedRoot), output: directR22TemporaryChild(values.output, trustedRoot) });
}

function validatePersistedCaseEvidence(evidence) {
  const keys = ["format", "formatVersion", "canonicalization", "caseId", "source", "policyJson", "traceJson",
    "worldEventLedgerReplayReportJson", "projectionQualificationReportJson", "godotEvidenceJson",
    "performanceEvidenceJson", "ephemeralAuthoritySessionManifestJson", "finalWorldEventLedgerJson", "turnRecords", "provider", "sideEffects", "redaction"];
  if (!exact(evidence, keys) || evidence.format !== "matrix-oasis.r22-offline-case-evidence" || evidence.formatVersion !== "0.2.0" ||
      evidence.canonicalization !== NPC_COGNITION_CANONICALIZATION || !ID.test(evidence.caseId ?? "") ||
      !exact(evidence.source, ["kind", "npcCurrentSha256", "derivedStateBundleSha256"]) ||
      !["qualified-cache", "synthetic-fixture"].includes(evidence.source.kind) ||
      !SHA.test(evidence.source.npcCurrentSha256 ?? "") || !SHA.test(evidence.source.derivedStateBundleSha256 ?? "") ||
      !valid(validateNpcCognitionPolicyJson(evidence.policyJson)) || !valid(validateNpcCognitionTraceJson(evidence.traceJson)) ||
      !valid(validateWorldEventLedgerReplayReportJson(evidence.worldEventLedgerReplayReportJson)) ||
      !valid(validateWorldEventLedgerJson(evidence.finalWorldEventLedgerJson))) fail("R22_OFFLINE_CASE_EVIDENCE_INVALID");
  const trace = JSON.parse(evidence.traceJson); const totals = validateTurnRecords(evidence.turnRecords, trace);
  const projection = requireCanonicalArtifact(evidence.projectionQualificationReportJson, "R22_OFFLINE_CASE_EVIDENCE_INVALID", "matrix-oasis.r22-ephemeral-derived-context-evidence");
  const godot = requireCanonicalArtifact(evidence.godotEvidenceJson, "R22_OFFLINE_CASE_EVIDENCE_INVALID", "matrix-oasis.r22-offline-godot-evidence-boundary");
  const performance = requireCanonicalArtifact(evidence.performanceEvidenceJson, "R22_OFFLINE_CASE_EVIDENCE_INVALID", "matrix-oasis.r22-offline-performance-evidence-boundary");
  const authoritySession = requireCanonicalArtifact(evidence.ephemeralAuthoritySessionManifestJson,
    "R22_OFFLINE_CASE_EVIDENCE_INVALID", "matrix-oasis.r22-ephemeral-authority-session-manifest");
  const finalLedger = JSON.parse(evidence.finalWorldEventLedgerJson);
  if (authoritySession.source?.baseQualifiedR20CurrentSha256 !== evidence.source.npcCurrentSha256 ||
      authoritySession.source?.baseQualifiedR21BundleSha256 !== evidence.source.derivedStateBundleSha256 ||
      authoritySession.timeline?.id !== finalLedger.timeline.id || authoritySession.timeline?.revision !== 0 ||
      authoritySession.timeline?.headSha256 !== null || authoritySession.timeline?.runtimeSnapshotSha256 !== finalLedger.authority.initialSnapshotSha256) {
    fail("R22_OFFLINE_CASE_EVIDENCE_INVALID");
  }
  validateEphemeralProjectionEvidence(projection, {
    baseQualifiedR21BundleSha256: evidence.source.derivedStateBundleSha256,
    authoritySessionManifestSha256: sha256(evidence.ephemeralAuthoritySessionManifestJson),
    finalWorldEventLedgerJson: evidence.finalWorldEventLedgerJson,
  });
  validateOfflineGodotBoundary(godot, {
    authoritySessionManifestSha256: sha256(evidence.ephemeralAuthoritySessionManifestJson),
    turnRecords: evidence.turnRecords,
    finalWorldEventLedgerJson: evidence.finalWorldEventLedgerJson,
  });
  validateOfflinePerformanceBoundary(performance);
  if (!exact(evidence.provider, ["fakeAdapterDispatches", "actualMicrousd", "realRequests", "replayRequests"]) ||
      evidence.provider.fakeAdapterDispatches !== totals.requests || evidence.provider.actualMicrousd !== totals.cost ||
      evidence.provider.realRequests !== 0 || evidence.provider.replayRequests !== 0 ||
      !exact(evidence.sideEffects, ["externalNetworkRequests", "environmentCredentialReads"]) ||
      evidence.sideEffects.externalNetworkRequests !== 0 || evidence.sideEffects.environmentCredentialReads !== 0 ||
      !same(evidence.redaction, REDACTION) || projection.baseQualifiedR21BundleSha256 !== evidence.source.derivedStateBundleSha256 ||
      projection.freshLedgerSha256 !== sha256(evidence.finalWorldEventLedgerJson) ||
      projection.ephemeralAuthoritySessionManifestSha256 !== sha256(evidence.ephemeralAuthoritySessionManifestJson) ||
      projection.r21Qualified !== false || godot.r22GodotExecuted !== false ||
      godot.r22GodotQualified !== false || performance.r22PerformanceMeasured !== false ||
      performance.r22PerformanceQualified !== false) fail("R22_OFFLINE_CASE_EVIDENCE_INVALID");
  return Object.freeze({ trace, totals });
}

function persistedEvidenceAsCaseResult(evidence) {
  return Object.freeze({
    caseId: evidence.caseId,
    policyJson: evidence.policyJson,
    traceJson: evidence.traceJson,
    worldEventLedgerReplayReportJson: evidence.worldEventLedgerReplayReportJson,
    projectionQualificationReportJson: evidence.projectionQualificationReportJson,
    godotEvidenceJson: evidence.godotEvidenceJson,
    performanceEvidenceJson: evidence.performanceEvidenceJson,
    ephemeralAuthoritySessionManifestJson: evidence.ephemeralAuthoritySessionManifestJson,
    finalWorldEventLedgerJson: evidence.finalWorldEventLedgerJson,
    turnRecords: evidence.turnRecords,
    adapterDispatches: evidence.provider.fakeAdapterDispatches,
  });
}

async function verifyPersistedCaseAgainstQualifiedSource({ evidence, item, trustedRoot, overrides }) {
  if (evidence.caseId !== item.caseId || evidence.source.kind !== item.sourceKind ||
      evidence.source.npcCurrentSha256 !== item.expectedNpcCurrentSha256 ||
      evidence.source.derivedStateBundleSha256 !== item.expectedDerivedStateBundleSha256) {
    fail("R22_OFFLINE_CASE_SOURCE_BINDING_MISMATCH");
  }
  const source = await verifyR22QualifiedSourcePair(item, trustedRoot, overrides);
  const structural = validatePersistedCaseEvidence(evidence);
  const result = validateCaseResult(persistedEvidenceAsCaseResult(evidence), item.caseId, source);
  const finalLedger = JSON.parse(result.finalWorldEventLedgerJson);

  const fresh = requireOk(await createNpcAuthoritySession({
    runtimeGamePackJson: source.runtimeGamePackJson,
    runtimeReceiptJson: source.runtimeReceiptJson,
    policyJson: source.authorityPolicyJson,
    timelineId: finalLedger.timeline.id,
    stepLimit: finalLedger.timeline.stepLimit,
  }), "R22_PERSISTED_R19_REPLAY_FAILED");
  const preparedBehavior = requireOk(prepareDeterministicNpcBehavior({
    behaviorPolicyJson: source.behaviorPolicyJson,
    entityBindingJson: source.npcEntityBindingJson,
    authorityPolicyJson: source.authorityPolicyJson,
  }), "R22_PERSISTED_BEHAVIOR_PREPARE_FAILED");
  const persistedGodotBoundary = JSON.parse(result.godotEvidenceJson);
  const executedCommand = persistedGodotBoundary.executedCommand;
  const reconstructedCommand = requireOk(selectEligibleNpcBehaviorCommand({
    prepared: preparedBehavior.prepared,
    runtimeSnapshot: fresh.runtimeSnapshot,
    runtimeInspection: fresh.inspection,
    worldEventLedgerJson: fresh.canonicalWorldEventLedgerJson,
    behaviorState: preparedBehavior.initialState,
    actorEntityId: executedCommand.actorEntityId,
    expectedIntentId: executedCommand.intentId,
    expectedNpcIntentSha256: sha256(executedCommand.npcIntentJson),
  }), "R22_PERSISTED_COMMAND_REBUILD_FAILED");
  if (reconstructedCommand.status !== "command" || !same(reconstructedCommand.command, executedCommand)) {
    fail("R22_PERSISTED_COMMAND_REBUILD_MISMATCH");
  }
  const expectedAuthoritySession = createEphemeralAuthoritySessionManifest(source, fresh);
  if (expectedAuthoritySession.manifestJson !== result.ephemeralAuthoritySessionManifestJson) {
    fail("R22_OFFLINE_AUTHORITY_SESSION_REBUILD_MISMATCH");
  }
  const restored = requireOk(await restoreNpcAuthoritySession({
    runtimeGamePackJson: source.runtimeGamePackJson,
    runtimeReceiptJson: source.runtimeReceiptJson,
    policyJson: source.authorityPolicyJson,
    worldEventLedgerJson: result.finalWorldEventLedgerJson,
  }), "R22_PERSISTED_R19_REPLAY_FAILED");
  const authorityReplay = requireOk(verifyNpcAuthoritySession(restored.session), "R22_PERSISTED_R19_REPLAY_FAILED");
  if (restored.canonicalWorldEventLedgerJson !== result.finalWorldEventLedgerJson ||
      authorityReplay.canonicalWorldEventLedgerJson !== result.finalWorldEventLedgerJson ||
      authorityReplay.canonicalWorldEventLedgerReplayReportJson !== result.worldEventLedgerReplayReportJson) {
    fail("R22_PERSISTED_R19_REPLAY_MISMATCH");
  }

  const synthesized = synthesizeNpcCognitionPolicyDocument({
    runtimeGamePackJson: source.runtimeGamePackJson,
    runtimeReceiptJson: source.runtimeReceiptJson,
    authorityPolicyJson: source.authorityPolicyJson,
    behaviorPolicyJson: source.behaviorPolicyJson,
    npcEntityBindingJson: source.npcEntityBindingJson,
    derivedStateBundleJson: source.r21.bundleJson,
  });
  if (synthesized.canonicalNpcCognitionPolicyJson !== result.policyJson) {
    fail("R22_PERSISTED_POLICY_REBUILD_MISMATCH");
  }
  const cognition = requireOk(await prepareNpcCognition({
    runtimeGamePackJson: source.runtimeGamePackJson,
    runtimeReceiptJson: source.runtimeReceiptJson,
    authorityPolicyJson: source.authorityPolicyJson,
    behaviorPolicyJson: source.behaviorPolicyJson,
    npcEntityBindingJson: source.npcEntityBindingJson,
    personaSeedJson: source.r21.personaSeedJson,
    relationshipPolicyJson: source.r21.relationshipPolicyJson,
    qualifiedWorldEventLedgerJson: source.originalWorldEventLedgerJson,
    memoryProjectionJson: source.r21.memoryProjectionJson,
    relationshipProjectionJson: source.r21.relationshipProjectionJson,
    memoryManifestJson: source.r21.memoryManifestJson,
    relationshipManifestJson: source.r21.relationshipManifestJson,
    derivedStateBundleJson: source.r21.bundleJson,
    cognitionPolicyJson: result.policyJson,
  }), "R22_PERSISTED_COGNITION_PREPARE_FAILED");
  const cognitionReplay = requireOk(replayNpcCognitionEvidence({
    prepared: cognition.prepared,
    worldEventLedgerJson: result.finalWorldEventLedgerJson,
    cognitionTraceJson: result.traceJson,
    turnRecords: result.turnRecords.map(({ callPlanJson, turnReceiptJson }) => ({ callPlanJson, turnReceiptJson })),
  }), "R22_PERSISTED_COGNITION_REPLAY_FAILED");
  if (cognitionReplay.canonicalWorldEventLedgerReplayReportJson !== result.worldEventLedgerReplayReportJson ||
      cognitionReplay.providerReplayRequests !== 0 || cognitionReplay.modelOutputReproducible !== false ||
      cognitionReplay.dialogueContentRetained !== false) {
    fail("R22_PERSISTED_COGNITION_REPLAY_MISMATCH");
  }

  const initialProjection = await projectEphemeralDerivedContext(source, fresh.canonicalWorldEventLedgerJson);
  const finalProjection = await projectEphemeralDerivedContext(source, result.finalWorldEventLedgerJson);
  const projectionEvidence = JSON.parse(result.projectionQualificationReportJson);
  if (projectionEvidence.initialEphemeralProjectionSha256 !== initialProjection.ephemeralProjectionSha256 ||
      projectionEvidence.ephemeralProjectionSha256 !== finalProjection.ephemeralProjectionSha256 ||
      !same(projectionEvidence.artifacts, finalProjection.artifacts) ||
      !same(projectionEvidence.artifactSha256, finalProjection.hashes)) {
    fail("R22_PERSISTED_EPHEMERAL_PROJECTION_REBUILD_MISMATCH");
  }
  const adjudicatedRecords = result.turnRecords.filter(({ turnReceiptJson }) =>
    JSON.parse(turnReceiptJson).statusHistory.includes("adjudicated"));
  const godotBoundary = persistedGodotBoundary;
  if (adjudicatedRecords.length !== 1 ||
      godotBoundary.finalizedTurnReceiptSha256 !== sha256(adjudicatedRecords[0].turnReceiptJson)) {
    fail("R22_PERSISTED_CONTROLLER_EVIDENCE_MISMATCH");
  }
  await revalidateQualifiedSource(source, trustedRoot, overrides);
  return Object.freeze({ totals: structural.totals });
}

export async function readBoundQualificationCaseSpec(caseSpecPath, trustedRoot, overrides = {}) {
  if (typeof caseSpecPath !== "string") fail("R22_QUALIFICATION_SOURCE_BINDING_REQUIRED");
  const resolved = directR22TemporaryChild(caseSpecPath, trustedRoot);
  const record = await readStableR22File(resolved, 1024 * 1024, trustedRoot, overrides);
  return Object.freeze({
    record,
    spec: validateR22OfflineCaseSpecJson(decodeCanonicalR22Record(record, "R22_CASE_SPEC_INVALID").text),
  });
}

export async function verifyR22OfflineQualification(directory, temporaryRoot, overrides = {}) {
  const trustedRoot = await trustR22TemporaryRoot(temporaryRoot, overrides); const root = path.resolve(directory);
  const boundSpec = await readBoundQualificationCaseSpec(overrides.caseSpecPath, trustedRoot, overrides);
  await assertExactR22DirectoryFiles(root, R22_QUALIFICATION_FILES, trustedRoot, overrides);
  const files = new Map(); const fileRecords = [];
  for (const name of R22_QUALIFICATION_FILES) {
    const record = await readStableR22File(path.join(root, name), 16 * 1024 * 1024, trustedRoot, overrides);
    fileRecords.push(record); files.set(name, decodeCanonicalR22Record(record).text);
  }
  const reportJson = files.get("npc-cognition-qualification-report.json");
  if (!valid(validateNpcCognitionQualificationReportJson(reportJson)) ||
      !valid(validateNpcCognitionPolicyJson(files.get("npc-cognition-policy.json"))) ||
      !valid(validateNpcCognitionTraceJson(files.get("npc-cognition-trace.json")))) fail("R22_QUALIFICATION_ARTIFACT_INVALID");
  const report = JSON.parse(reportJson); const evidenceSet = parseCanonical(files.get("offline-case-evidence.json"), "R22_OFFLINE_CASE_EVIDENCE_INVALID");
  if (!exact(evidenceSet, ["format", "formatVersion", "canonicalization", "primaryCaseId", "cases"]) ||
      evidenceSet.format !== CASE_EVIDENCE_FORMAT || evidenceSet.formatVersion !== "0.2.0" ||
      evidenceSet.primaryCaseId !== boundSpec.spec.primaryCaseId ||
      !Array.isArray(evidenceSet.cases) || evidenceSet.cases.length !== report.offlineCases.length ||
      !same(report.offlineCases.map(({ caseId }) => caseId), boundSpec.spec.cases.map(({ caseId }) => caseId)) ||
      !same(evidenceSet.cases.map(({ caseId }) => caseId), boundSpec.spec.cases.map(({ caseId }) => caseId))) {
    fail("R22_OFFLINE_CASE_EVIDENCE_INVALID");
  }
  const primary = evidenceSet.cases.find((item) => item.caseId === evidenceSet.primaryCaseId);
  if (!primary || report.policySha256 !== sha256(files.get("npc-cognition-policy.json")) ||
      report.traceSha256 !== sha256(files.get("npc-cognition-trace.json")) ||
      report.worldEventLedgerReplayReportSha256 !== sha256(files.get("world-event-ledger-replay-report.json")) ||
      report.projectionQualificationReportSha256 !== sha256(files.get("npc-projection-qualification-report.json")) ||
      report.godotEvidenceSha256 !== sha256(files.get("godot-evidence.json")) ||
      report.performanceEvidenceSha256 !== sha256(files.get("performance-evidence.json"))) fail("R22_QUALIFICATION_ARTIFACT_IDENTITY_MISMATCH");
  let totalRequests = 0;
  for (let index = 0; index < report.offlineCases.length; index += 1) {
    const item = report.offlineCases[index];
    const evidence = evidenceSet.cases.find((candidate) => candidate.caseId === item.caseId);
    if (!evidence || item.evidenceSha256 !== sha256(canonicalText(evidence)) ||
        item.traceSha256 !== sha256(evidence.traceJson)) fail("R22_OFFLINE_CASE_EVIDENCE_INVALID");
    const verified = await verifyPersistedCaseAgainstQualifiedSource({
      evidence,
      item: boundSpec.spec.cases[index],
      trustedRoot,
      overrides,
    });
    totalRequests += verified.totals.requests;
  }
  if (report.providerCalls.fake !== totalRequests || primary.policyJson !== files.get("npc-cognition-policy.json") ||
      primary.traceJson !== files.get("npc-cognition-trace.json") ||
      primary.worldEventLedgerReplayReportJson !== files.get("world-event-ledger-replay-report.json") ||
      primary.projectionQualificationReportJson !== files.get("npc-projection-qualification-report.json") ||
      primary.godotEvidenceJson !== files.get("godot-evidence.json") ||
      primary.performanceEvidenceJson !== files.get("performance-evidence.json")) fail("R22_QUALIFICATION_ARTIFACT_IDENTITY_MISMATCH");
  await revalidateR22FileRecord(boundSpec.record, 1024 * 1024, trustedRoot, overrides);
  for (const record of fileRecords) await revalidateR22FileRecord(record, 16 * 1024 * 1024, trustedRoot, overrides);
  await assertExactR22DirectoryFiles(root, R22_QUALIFICATION_FILES, trustedRoot, overrides);
  return Object.freeze({ ok: true, reportSha256: sha256(reportJson), policySha256: report.policySha256,
    traceSha256: report.traceSha256, markers: Object.freeze([...report.markers]), r22GodotQualified: false,
    r22PerformanceQualified: false, readyForPreview: false,
    sourceBound: true, caseSpecSha256: boundSpec.record.sha256 });
}

export async function runR22OfflineQualification(args, injected = {}) {
  const temporaryRoot = injected.temporaryRoot;
  const trustedRoot = await trustR22TemporaryRoot(temporaryRoot, injected);
  const parsed = parseR22QualificationArguments(args, trustedRoot); await assertMissingR22Path(parsed.output, injected);
  const specRecord = await readStableR22File(parsed.caseSpec, 1024 * 1024, trustedRoot, injected);
  const spec = validateR22OfflineCaseSpecJson(decodeCanonicalR22Record(specRecord, "R22_CASE_SPEC_INVALID").text);
  const completed = [];
  for (const item of spec.cases) {
    const source = await verifyR22QualifiedSourcePair(item, trustedRoot, injected);
    const result = typeof injected.qualifyCase === "function"
      ? validateCaseResult(await injected.qualifyCase(Object.freeze({ caseId: item.caseId, source })), item.caseId, source)
      : await qualifyR22OfflineCase({ caseId: item.caseId, source, temporaryRoot: trustedRoot, overrides: injected });
    completed.push(Object.freeze({ result, source })); await revalidateQualifiedSource(source, trustedRoot, injected);
  }
  const artifacts = buildR22OfflineQualificationArtifacts({ spec, cases: completed });
  const revalidate = async () => {
    for (const entry of completed) await revalidateQualifiedSource(entry.source, trustedRoot, injected);
    const currentSpec = await readStableR22File(parsed.caseSpec, 1024 * 1024, trustedRoot, injected);
    if (currentSpec.sha256 !== specRecord.sha256 || !Buffer.from(currentSpec.bytes).equals(Buffer.from(specRecord.bytes))) fail("R22_SOURCE_CHANGED");
  };
  const verificationOverrides = { ...injected, caseSpecPath: parsed.caseSpec };
  const published = await publishR22Artifacts({ output: parsed.output, temporaryRoot: trustedRoot.path, artifacts,
    beforeRename: revalidate, afterRename: revalidate,
    verifyPublished: (root) => verifyR22OfflineQualification(root, trustedRoot.path, verificationOverrides) }, injected);
  const verified = await verifyR22OfflineQualification(parsed.output, trustedRoot.path, verificationOverrides);
  return Object.freeze({ ok: true, output: published.output, reportSha256: verified.reportSha256,
    markers: verified.markers, readyForPreview: false });
}
