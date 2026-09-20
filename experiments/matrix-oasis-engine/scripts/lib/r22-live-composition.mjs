import { randomBytes } from "node:crypto";
import path from "node:path";
import { types } from "node:util";
import { createNpcAuthoritySession, restoreNpcAuthoritySession } from "@matrix-oasis/npc-authority-session";
import { prepareDeterministicNpcBehavior, selectNextNpcBehaviorCommand, selectEligibleNpcBehaviorCommand } from "@matrix-oasis/npc-behavior-runtime";
import { createNpcCognitionTurn, planNpcCognitionCall, prepareNpcCognition, validateNpcDialogueProposal, mapNpcDialogueProposalToIntent } from "@matrix-oasis/npc-cognition-runtime";
import { createOpenAiNpcCognitionProvider, executeApprovedNpcCognitionTurn } from "@matrix-oasis/npc-cognition-provider-openai";
import { recoverQualifiedCreatorRuns } from "@matrix-oasis/prototype-creator-qualification";
import { acquireR20WriterLease, releaseR20WriterLease, createR20TimelineStore,
  recoverR20UnfinishedTimeline, resumeR20TimelineStore } from "./r20-cli-core.mjs";
import { createR20Coordinator, exportR20Coordinator, handleR20CoordinatorRequestAsync } from "./r20-host-core.mjs";
import { closeR22CallStore, inspectR22CallStore, openR22CallStore, readR22ActiveCallArtifacts, readR22FinalizedTurnReceipt } from "./r22-call-store.mjs";
import { createR22OfficialOneShotOperations, prepareR22FileCredentialReader } from "./r22-live-provider.mjs";
import { validateR22LivePhysicalEvidence } from "./r22-live-evidence.mjs";
import { rebuildR22RecoveredCoordinator } from "./r22-recovered-coordinator.mjs";
import { loadR22LiveRecoveryHistory } from "./r22-live-recovery.mjs";
import { createR22CognitionSelectorGate, createR22TransactionalHost, createR22LoopbackController,
  closeR22LoopbackController, recoverR22TransactionalHost, restoreR22LoopbackController } from "./r22-host-core.mjs";
import { assertMissingR22Path, canonicalText, directR22TemporaryChild, sha256, trustR22TemporaryRoot,
  readStableR22File, decodeCanonicalR22Record, revalidateR22FileRecord, publishR22Artifacts } from "./r22-cli-core.mjs";
import { synthesizeNpcCognitionPolicyDocument, revalidateQualifiedSource, projectEphemeralDerivedContext,
  readBoundQualificationCaseSpec, verifyR22OfflineQualification, verifyR22QualifiedSourcePair } from "./r22-qualification-core.mjs";
import { calculateR20ImplementationIdentity } from "../qualify-r20-npc-bridge.mjs";
import { createR16QualificationReferenceVerifier } from "./r16-creator-core.mjs";
import { selectR15EvidenceRun } from "./r15-preview-core.mjs";

const SHA = /^sha256:[0-9a-f]{64}$/u;
const OFFLINE_MANUAL_SCENARIOS = new Set(["normal", "timeout", "refusal", "invalid-response", "injection"]);
const OFFLINE_MANUAL_WINDOWS = new Set(["960x540", "640x540"]);
function fail(code) { throw new Error(code); }
function requireOk(result, code) { if (result?.ok !== true) fail(code); return result; }
function captureOfflineManualProfile(input) {
  if (!input || typeof input !== "object" || Array.isArray(input) || types.isProxy(input)) fail("R22_LIVE_CONFIGURATION_INVALID");
  const descriptor = Object.getOwnPropertyDescriptor(input, "offlineManualProfile");
  if (descriptor === undefined) return null;
  if (!Object.hasOwn(descriptor, "value")) fail("R22_LIVE_CONFIGURATION_INVALID");
  const profile = descriptor.value;
  if (!profile || typeof profile !== "object" || Array.isArray(profile) || types.isProxy(profile) ||
      Object.getPrototypeOf(profile) !== Object.prototype) fail("R22_LIVE_CONFIGURATION_INVALID");
  const descriptors = Object.getOwnPropertyDescriptors(profile);
  const keys = Reflect.ownKeys(descriptors);
  if (keys.some((key) => typeof key !== "string") || keys.sort().join("\0") !== "scenario\0windowSize" ||
      !Object.hasOwn(descriptors.scenario, "value") || !Object.hasOwn(descriptors.windowSize, "value") ||
      !OFFLINE_MANUAL_SCENARIOS.has(descriptors.scenario.value) || !OFFLINE_MANUAL_WINDOWS.has(descriptors.windowSize.value)) {
    fail("R22_LIVE_CONFIGURATION_INVALID");
  }
  return Object.freeze({ scenario: descriptors.scenario.value, windowSize: descriptors.windowSize.value });
}
function validateOfflineManualMode(input, profile) {
  if (profile === null) return;
  const provider = Object.getOwnPropertyDescriptor(input, "providerMode");
  if (!provider || !Object.hasOwn(provider, "value") || provider.value !== "offline-fake" || Object.hasOwn(input, "credentialFile")) {
    fail("R22_LIVE_CONFIGURATION_INVALID");
  }
}
function point(authority) {
  const ledger = JSON.parse(authority.canonicalWorldEventLedgerJson);
  return { revision: ledger.revision, headSha256: ledger.headSha256, runtimeSnapshotSha256: sha256(canonicalText(authority.runtimeSnapshot)) };
}

export async function prepareR22LivePreview(options) {
  const offlineManualProfile = captureOfflineManualProfile(options);
  validateOfflineManualMode(options, offlineManualProfile);
  const temporaryRoot = await trustR22TemporaryRoot(options.temporaryRoot);
  const qualification = await verifyR22OfflineQualification(options.qualifiedRoot, temporaryRoot.path,
    { caseSpecPath: options.caseSpecPath });
  const boundSpec = await readBoundQualificationCaseSpec(options.caseSpecPath, temporaryRoot);
  if (boundSpec.record.sha256 !== qualification.caseSpecSha256) fail("R22_SOURCE_CHANGED");
  const item = boundSpec.spec.cases.find((entry) => entry.caseId === boundSpec.spec.primaryCaseId);
  const source = await verifyR22QualifiedSourcePair(item, temporaryRoot);
  const sourceManifest = JSON.parse(source.authorityManifestJson);
  const previewIdentity = sourceManifest.identities;
  const roots = { prototypeRunRoot: options.prototypeRunRoot, spatialRunRoot: options.spatialRunRoot,
    solvedRunRoot: options.solvedRunRoot, evidenceRunRoot: options.evidenceRunRoot,
    qualifiedRunRoot: options.creatorQualifiedRoot, temporaryRoot: temporaryRoot.path };
  const recovered = await recoverQualifiedCreatorRuns({ qualifiedRunRoot: roots.qualifiedRunRoot,
    temporaryRoot: roots.temporaryRoot, verifyReferences: createR16QualificationReferenceVerifier(roots) });
  const selectedQualification = recovered.runs.find((entry) => entry.qualificationRunId === sourceManifest.qualificationRunId);
  if (!selectedQualification || selectedQualification.qualification.sourceRunId !== sourceManifest.sourceRunId) {
    fail("R22_PREVIEW_SOURCE_IDENTITY_MISMATCH");
  }
  const evidence = await selectR15EvidenceRun({ evidenceRunRoot: roots.evidenceRunRoot,
    temporaryRoot: roots.temporaryRoot, runId: selectedQualification.qualification.evidence.runId });
  const selectedCreatorEvidence = Object.freeze({ qualificationRunId: selectedQualification.qualificationRunId,
    qualification: selectedQualification.qualification, evidence });
  const previewFiles = evidence.previewFiles;
  const identities = { runtimePackSha256: "runtime-game-pack.json", runtimeReceiptSha256: "runtime-receipt.json",
    spatialSolutionSha256: "spatial-solution.json", spatialVerificationSha256: "spatial-verification-report.json" };
  if (!(previewFiles instanceof Map) || Object.entries(identities).some(([key, file]) =>
    !(previewFiles.get(file) instanceof Uint8Array) || sha256(previewFiles.get(file)) !== previewIdentity[key])) {
    fail("R22_PREVIEW_SOURCE_IDENTITY_MISMATCH");
  }
  const calculateImplementation = () => calculateR20ImplementationIdentity(options.moduleRoot,
    { entryFiles: [offlineManualProfile === null ? "scripts/preview-r22.mjs" : "scripts/preview-r22-offline.mjs"],
      resourceTrees: ["apps/runtime-godot"] });
  const implementation = await calculateImplementation();
  const binary = await readStableR22File(options.godotCommand, 256 * 1024 * 1024, temporaryRoot);
  const composition = await createR22LiveComposition({ source, npcRunRoot: options.npcRunRoot,
    cognitionRunRoot: options.cognitionRunRoot, temporaryRoot: temporaryRoot.path,
    implementationSha256: implementation.sha256, godotBinarySha256: binary.sha256, providerMode: options.providerMode,
    resume: options.resume === true, ...(Object.hasOwn(options, "credentialFile") ? { credentialFile: options.credentialFile } : {}),
    ...(offlineManualProfile === null ? {} : { offlineManualProfile }) });
  return Object.freeze({ ...composition, source, previewIdentity, previewFiles, qualification, selectedCreatorEvidence,
    revalidate: async () => {
      await revalidateR22FileRecord(boundSpec.record, 1024 * 1024, temporaryRoot);
      await composition.revalidate();
      await revalidateR22FileRecord(binary, 256 * 1024 * 1024, temporaryRoot);
      if ((await calculateImplementation()).sha256 !== implementation.sha256) fail("R22_IMPLEMENTATION_CHANGED");
    },
  });
}

// This execution mode never reaches an external network or credential source. Its
// provider-shaped response still passes the real bounded Responses validator.
function offlineResponse(plan, request) {
  return {
    id: "resp_discarded", object: "response", created_at: 1, completed_at: 2, status: "completed", service_tier: "default",
    background: false, error: null, incomplete_details: null, instructions: request.instructions,
    max_output_tokens: plan.maxOutputTokens, max_tool_calls: null, metadata: {}, model: plan.model,
    output: [{ id: "msg_discarded", type: "message", status: "completed", role: "assistant", content: [{
      type: "output_text", annotations: [], text: JSON.stringify({ contextSha256: plan.contextSha256,
        dialogueText: "Offline test dialogue. I can carry out the disclosed safe action.",
        actionChoiceId: plan.candidateChoices[0]?.choiceId ?? null }),
    }] }],
    usage: { input_tokens: 200, input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 },
      output_tokens: 50, output_tokens_details: { reasoning_tokens: 0 }, total_tokens: 250 },
    parallel_tool_calls: true, previous_response_id: null, store: false, tool_choice: "auto", tools: [], truncation: "disabled",
  };
}

function offlineScenarioResponse(scenario, plan, request) {
  const response = offlineResponse(plan, request);
  if (scenario === "normal") return response;
  if (scenario === "refusal") {
    response.output[0].content = [{ type: "refusal", refusal: "Offline fake refusal." }];
    return response;
  }
  if (scenario === "injection") {
    response.output[0].content[0].text = JSON.stringify({ contextSha256: plan.contextSha256,
      dialogueText: "[offline link](file:///offline-qa) <b>literal only</b> [color=red]no execution[/color]",
      actionChoiceId: plan.candidateChoices[0]?.choiceId ?? null });
    return response;
  }
  fail("R22_LIVE_CONFIGURATION_INVALID");
}

function awaitOfflineTimeout(signal) {
  if (!signal || typeof signal.addEventListener !== "function" || typeof signal.aborted !== "boolean") {
    fail("R22_FAKE_REQUEST_INVALID");
  }
  return new Promise((_resolve, reject) => {
    const abort = () => { const error = new Error("R22_OFFLINE_FAKE_TIMEOUT"); error.name = "AbortError"; reject(error); };
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}

export async function createR22LiveComposition(input) {
  const offlineManualProfile = captureOfflineManualProfile(input);
  validateOfflineManualMode(input, offlineManualProfile);
  if (!["offline-fake", "official-once"].includes(input?.providerMode) || !SHA.test(input.implementationSha256 ?? "") ||
      !SHA.test(input.godotBinarySha256 ?? "") || (input.resume !== undefined && typeof input.resume !== "boolean")) fail("R22_LIVE_CONFIGURATION_INVALID");
  const resume = input.resume === true;
  if (input.credentialFile !== undefined && (input.providerMode !== "official-once" || resume)) fail("R22_LIVE_CONFIGURATION_INVALID");
  const { source } = input;
  const trustedRoot = await trustR22TemporaryRoot(input.temporaryRoot);
  // Pin metadata only. The returned reader cannot consume credential bytes until
  // the one-shot operations have verified the approved, persisted dispatch.
  const readCredential = input.credentialFile === undefined ? undefined : await prepareR22FileCredentialReader({
    credentialFile: input.credentialFile, temporaryRoot: trustedRoot.path });
  const npcRunRoot = directR22TemporaryChild(input.npcRunRoot, trustedRoot);
  const cognitionRunRoot = directR22TemporaryChild(input.cognitionRunRoot, trustedRoot);
  if (!npcRunRoot.endsWith("-npc") || !cognitionRunRoot.endsWith("-cognition") ||
      npcRunRoot.slice(0, -4) !== cognitionRunRoot.slice(0, -10) ||
      npcRunRoot === source?.npc?.directory?.path || cognitionRunRoot === source?.derived?.directory?.path) {
    fail("R22_LIVE_CONFIGURATION_INVALID");
  }
  if (resume) {
    await trustR22TemporaryRoot(npcRunRoot);
    await trustR22TemporaryRoot(cognitionRunRoot);
  } else {
    await assertMissingR22Path(npcRunRoot);
    await assertMissingR22Path(cognitionRunRoot);
  }
  await revalidateQualifiedSource(source, trustedRoot);
  const baseManifest = JSON.parse(source.authorityManifestJson);
  const behavior = requireOk(prepareDeterministicNpcBehavior({ behaviorPolicyJson: source.behaviorPolicyJson,
    entityBindingJson: source.npcEntityBindingJson, authorityPolicyJson: source.authorityPolicyJson }), "R22_BEHAVIOR_PREPARE_FAILED");
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
    memoryProjectionJson: source.r21.memoryProjectionJson, relationshipProjectionJson: source.r21.relationshipProjectionJson,
    memoryManifestJson: source.r21.memoryManifestJson, relationshipManifestJson: source.r21.relationshipManifestJson,
    derivedStateBundleJson: source.r21.bundleJson, cognitionPolicyJson }), "R22_COGNITION_PREPARE_FAILED");
  const hostRunId = `host-${sha256(path.basename(cognitionRunRoot)).slice(7, 31)}`;
  const initialTimelineId = `timeline-${sha256(canonicalText({ hostRunId, source: source.current.manifestSha256,
    implementationSha256: input.implementationSha256 })).slice(7, 31)}`;
  const manifestFor = (timelineId) => canonicalText({ ...baseManifest, timelineId,
    identities: { ...baseManifest.identities, implementationSha256: input.implementationSha256,
      godotBinarySha256: input.godotBinarySha256 } });
  const sessionManifestJson = canonicalText({ format: "matrix-oasis.r22-cognition-session-manifest",
    formatVersion: offlineManualProfile === null ? "0.1.0" : "0.2.0",
    canonicalization: "matrix-oasis.canonical-json/1", hostRunId, initialTimelineId, providerMode: input.providerMode,
    sourceCurrentSha256: source.npc.record.sha256, sourceDerivedBundleSha256: source.derived.record.sha256,
    sourceAuthorityManifestSha256: sha256(source.authorityManifestJson), cognitionPolicySha256: sha256(cognitionPolicyJson),
    implementationSha256: input.implementationSha256, godotBinarySha256: input.godotBinarySha256,
    ...(offlineManualProfile === null ? {} : { offlineManualProfile }) });
  let sessionManifestRecord = null;
  const readBoundSessionManifest = async () => {
    const record = await readStableR22File(path.join(cognitionRunRoot, "cognition-session-manifest.json"), 16 * 1024, trustedRoot);
    if (decodeCanonicalR22Record(record).text !== sessionManifestJson) fail("R22_RECOVERY_SESSION_IDENTITY_MISMATCH");
    return record;
  };
  if (resume) sessionManifestRecord = await readBoundSessionManifest();
  const authority = requireOk(await createNpcAuthoritySession({ runtimeGamePackJson: source.runtimeGamePackJson,
    runtimeReceiptJson: source.runtimeReceiptJson, policyJson: source.authorityPolicyJson,
    timelineId: initialTimelineId }), "R22_FRESH_TIMELINE_FAILED");
  const sessionToken = randomBytes(32).toString("hex");
  const gate = createR22CognitionSelectorGate({ commandSelector: selectNextNpcBehaviorCommand,
    queuedCommandSelector: selectEligibleNpcBehaviorCommand });
  let writerLease = null, authorityStore = null, callStore = null, controller = null, coordinator = null;
  let sequence = 0, activeRuntime = null, finalizedAuthority = false, closed = false, closePromise = null, compositionReady = false;
  let recoveryCommands = Object.freeze([]), recoveryResult = null;
  let fakeDispatches = 0;
  const stores = new Set();
  const physicalCommands = new Set();
  const officialOperations = input.providerMode === "official-once" ? createR22OfficialOneShotOperations({
    ...(readCredential === undefined ? {} : { readCredential }),
    readDispatch: async () => {
      if (closed || callStore === null) fail("R22_OFFICIAL_ONE_SHOT_UNAVAILABLE");
      await revalidateQualifiedSource(source, trustedRoot);
      const { checkpoint } = inspectR22CallStore(callStore);
      const artifacts = await readR22ActiveCallArtifacts(callStore);
      return { checkpoint, callPlanJson: artifacts.callPlanJson, dispatchRecordJson: artifacts.dispatchRecordJson };
    },
  }) : null;
  const createAuthorityStore = async (timelineId) => createR20TimelineStore({ npcRunRoot, temporaryRoot: trustedRoot.path,
    authorityManifestJson: manifestFor(timelineId), behaviorPolicyJson: source.behaviorPolicyJson,
    entityBindingJson: source.npcEntityBindingJson, writerLease });
  const verifyAuthority = async () => {
    const snapshot = exportR20Coordinator(coordinator);
    if (!snapshot) fail("R22_AUTHORITY_EXPORT_FAILED");
    const restored = requireOk(await restoreNpcAuthoritySession({ runtimeGamePackJson: source.runtimeGamePackJson,
      runtimeReceiptJson: source.runtimeReceiptJson, policyJson: source.authorityPolicyJson,
      worldEventLedgerJson: snapshot.authority.canonicalWorldEventLedgerJson }), "R22_R19_REPLAY_FAILED");
    if (canonicalText(restored.runtimeSnapshot) !== canonicalText(snapshot.authority.runtimeSnapshot)) fail("R22_R19_REPLAY_FAILED");
    return snapshot;
  };
  const close = async () => {
    if (closed) return;
    if (closePromise !== null) return closePromise;
    closePromise = (async () => {
      let first = null;
      const attempt = async (operation) => { try { await operation(); } catch (error) { first ??= error; } };
      if (controller) await attempt(() => closeR22LoopbackController(controller));
      if (compositionReady && authorityStore && coordinator && !finalizedAuthority) await attempt(async () => {
        await authorityStore.seal(await verifyAuthority()); finalizedAuthority = true;
      });
      for (const store of stores) await attempt(async () => { await closeR22CallStore(store); stores.delete(store); });
      if (writerLease) await attempt(async () => { await releaseR20WriterLease(writerLease); writerLease = null; });
      activeRuntime = null;
      if (first) throw first;
      closed = true;
    })();
    try { return await closePromise; } finally { if (!closed) closePromise = null; }
  };
  const hostOperations = {
    async keyReader() {
      // A crash-recovery invocation is never a fresh authorization for another
      // paid request, including when the last dispatch had an uncertain cost.
      if (resume && officialOperations !== null) fail("R22_OFFICIAL_ONE_SHOT_UNAVAILABLE");
      return officialOperations === null ? "offline-fake-key" : officialOperations.keyReader();
    },
    async providerExecutor({ apiKey, callPlanJson, providerRequestJson, approvalHash }) {
      if (resume && officialOperations !== null) fail("R22_OFFICIAL_ONE_SHOT_UNAVAILABLE");
      if (officialOperations !== null) return officialOperations.providerExecutor({ apiKey, callPlanJson, providerRequestJson, approvalHash });
      const plan = JSON.parse(callPlanJson), request = JSON.parse(providerRequestJson);
      const provider = createOpenAiNpcCognitionProvider({ apiKey, fetchImplementation: async (url, options) => {
        if (url !== plan.endpoint || options?.method !== "POST" || options?.body !== providerRequestJson) fail("R22_FAKE_REQUEST_INVALID");
        fakeDispatches += 1;
        const scenario = offlineManualProfile?.scenario ?? "normal";
        if (scenario === "timeout") return awaitOfflineTimeout(options.signal);
        const body = scenario === "invalid-response" ? JSON.stringify({ object: "response", status: "completed" }) :
          JSON.stringify(offlineScenarioResponse(scenario, plan, request));
        return new Response(body, { status: 200, headers: { "content-type": "application/json" } });
      } });
      return executeApprovedNpcCognitionTurn({ callPlanJson, providerRequestJson, approvalHash }, provider);
    },
    async proposalValidator({ proposalJson }) {
      if (!activeRuntime || closed) return { ok: false, diagnosticCode: "R22_CONTEXT_STALE" };
      await revalidateQualifiedSource(source, trustedRoot);
      const snapshot = exportR20Coordinator(coordinator);
      const state = { runtimeSnapshot: snapshot.authority.runtimeSnapshot, runtimeInspection: snapshot.authority.inspection,
        worldEventLedgerJson: snapshot.authority.canonicalWorldEventLedgerJson, behaviorState: snapshot.behaviorState };
      const validated = validateNpcDialogueProposal({ prepared: cognition.prepared, turn: activeRuntime.turn.turn,
        callPlan: activeRuntime.plan.callPlan, npcDialogueProposalJson: proposalJson, ...state });
      if (!validated.ok) return validated;
      const mapped = mapNpcDialogueProposalToIntent({ prepared: cognition.prepared, turn: activeRuntime.turn.turn,
        callPlan: activeRuntime.plan.callPlan, validatedProposal: validated.validatedProposal, ...state });
      if (!mapped.ok) return mapped;
      return { ok: true, canonicalNpcDialogueProposalJson: validated.canonicalNpcDialogueProposalJson,
        dialogueText: validated.dialogueText, actionChoiceId: validated.actionChoiceId,
        mappedIntentSha256: mapped.npcIntentJson === null ? null : sha256(mapped.npcIntentJson), command: mapped.command };
    },
    async adjudicationLookup({ intentId, mappedIntentSha256, beforeLedgerPoint }) {
      const snapshot = await verifyAuthority();
      const ledger = JSON.parse(snapshot.authority.canonicalWorldEventLedgerJson);
      const found = ledger.entries.filter((entry) => entry.intent.id === intentId);
      if (found.length === 0) {
        if (canonicalText(point(snapshot.authority)) !== canonicalText(beforeLedgerPoint)) fail("R22_CONTEXT_STALE");
        return { found: false };
      }
      if (found.length !== 1 || sha256(canonicalText(found[0].intent)) !== mappedIntentSha256 ||
          found[0].revision !== beforeLedgerPoint.revision + 1 ||
          found[0].previousEntrySha256 !== beforeLedgerPoint.headSha256 ||
          found[0].beforeSnapshotSha256 !== beforeLedgerPoint.runtimeSnapshotSha256) fail("R22_RECOVERY_ADJUDICATION_INVALID");
      return { found: true, canonicalWorldEventLedgerJson: snapshot.authority.canonicalWorldEventLedgerJson };
    },
  };
  const openTimelineHost = async (timelineId, recovering = false) => {
    const snapshot = await verifyAuthority();
    const ledger = JSON.parse(snapshot.authority.canonicalWorldEventLedgerJson);
    if (ledger.timeline.id !== timelineId || (!recovering && (ledger.revision !== 0 || ledger.headSha256 !== null))) fail("R22_HOST_ROTATION_FAILED");
    const genesis = recovering ? requireOk(await createNpcAuthoritySession({ runtimeGamePackJson: source.runtimeGamePackJson,
      runtimeReceiptJson: source.runtimeReceiptJson, policyJson: source.authorityPolicyJson,
      timelineId, stepLimit: ledger.timeline.stepLimit }), "R22_RECOVERY_GENESIS_INVALID") : snapshot.authority;
    if (recovering) {
      // openR22CallStore also supports fresh stores. Recovery must not interpret
      // a deleted checkpoint or host budget as permission to initialize either.
      await readStableR22File(path.join(cognitionRunRoot, "host-budget.json"), 1024 * 1024, trustedRoot);
      await readStableR22File(path.join(cognitionRunRoot, "timelines", sha256(manifestFor(timelineId)).slice(7),
        "cognition-checkpoint.json"), 1024 * 1024, trustedRoot);
    }
    callStore = await openR22CallStore({ temporaryRoot: trustedRoot.path, cognitionRunRoot, hostRunId, timelineId,
      authoritySessionSha256: sha256(manifestFor(timelineId)), cognitionPolicySha256: sha256(cognitionPolicyJson),
      initialLedgerPoint: point(genesis) });
    stores.add(callStore);
    return createR22TransactionalHost({ store: callStore, operations: hostOperations });
  };
  const recordPhysicalEvidence = async (text) => {
    if (closed || callStore === null) fail("R22_LIVE_PHYSICAL_EVIDENCE_INVALID");
    const store = callStore;
    const snapshot = await verifyAuthority();
    const checked = validateR22LivePhysicalEvidence(text, snapshot, sha256(source.npcEntityBindingJson));
    const key = `${checked.observation.timelineId}:${checked.observation.command.sequence}`;
    if (physicalCommands.has(key)) fail("R22_LIVE_PHYSICAL_EVIDENCE_DUPLICATE");
    const { checkpoint } = inspectR22CallStore(store);
    if (checkpoint.timelineId !== checked.observation.timelineId) fail("R22_CONTEXT_STALE");
    const receipts = [];
    for (const turn of checkpoint.finalized) {
      const result = await readR22FinalizedTurnReceipt(store, { timelineId: checkpoint.timelineId,
        turnId: turn.turnId, callPlanSha256: turn.callPlanSha256 });
      if (!result) fail("R22_LIVE_PHYSICAL_RECEIPT_INVALID");
      const receipt = JSON.parse(result.turnReceiptJson);
      if (receipt.mappedIntentSha256 === checked.observation.command.intentSha256) {
        if (receipt.status !== "finalized" || !receipt.statusHistory.includes("adjudicated") ||
            receipt.ledger.after.headSha256 !== checked.entrySha256 || receipt.ledger.after.revision !== checked.revision ||
            receipt.ledger.before.runtimeSnapshotSha256 !== checked.observation.authority.beforeSnapshotSha256 ||
            receipt.ledger.after.runtimeSnapshotSha256 !== checked.observation.authority.afterSnapshotSha256) fail("R22_LIVE_PHYSICAL_RECEIPT_INVALID");
        receipts.push(result);
      }
    }
    if (receipts.length > 1) fail("R22_LIVE_PHYSICAL_RECEIPT_INVALID");
    const report = { format: "matrix-oasis.r22-live-observation",
      formatVersion: offlineManualProfile === null ? "0.1.0" : "0.2.0", canonicalization: "matrix-oasis.canonical-json/1",
      implementationSha256: input.implementationSha256, godotBinarySha256: input.godotBinarySha256,
      sourceCurrentSha256: source.npc.record.sha256, sourceDerivedBundleSha256: source.derived.record.sha256,
      authorityManifestSha256: sha256(manifestFor(checkpoint.timelineId)), observationSha256: checked.observationSha256,
      ledgerSha256: checked.ledgerSha256, entrySha256: checked.entrySha256, revision: checked.revision,
      turnReceiptSha256: receipts[0]?.turnReceiptSha256 ?? null, cognitionActionVerified: receipts.length === 1,
      performancePassed: checked.performancePassed, providerMode: input.providerMode,
      realProviderRequests: officialOperations?.inspect().providerRequests ?? 0,
      sourceCredentialReads: officialOperations?.inspect().sourceCredentialReads ?? 0,
      qualificationStatus: "unqualified-manual-observation", manualAcceptancePassed: false,
      ...(offlineManualProfile === null ? {} : { offlineManualProfile }) };
    const reportJson = canonicalText(report);
    const artifacts = new Map([["physical-observation.json", text], ["observation-report.json", reportJson],
      ["world-event-ledger.json", snapshot.authority.canonicalWorldEventLedgerJson]]);
    if (receipts.length === 1) artifacts.set("turn-receipt.json", receipts[0].turnReceiptJson);
    const published = await publishR22Artifacts({ temporaryRoot: trustedRoot.path,
      output: path.join(trustedRoot.path, `matrix-oasis-r22-physical-${sha256(reportJson).slice(7)}`), artifacts,
      beforeRename: async () => {
        await revalidateQualifiedSource(source, trustedRoot);
        if (callStore !== store || exportR20Coordinator(coordinator).authority.canonicalWorldEventLedgerJson !== snapshot.authority.canonicalWorldEventLedgerJson) fail("R22_CONTEXT_STALE");
      } });
    physicalCommands.add(key);
    return Object.freeze({ ...published, observationSha256: checked.observationSha256,
      medianFpsMilli: checked.observation.performance.medianFpsMilli, performancePassed: checked.performancePassed,
      cognitionActionVerified: receipts.length === 1 });
  };
  try {
    writerLease = await acquireR20WriterLease({ npcRunRoot, temporaryRoot: trustedRoot.path });
    const coordinatorHooks = {
      onCommit: async (snapshot) => authorityStore.append(snapshot),
      onReset: async (snapshot, { previousSnapshot }) => {
        if (!previousSnapshot) fail("R22_RESET_SNAPSHOT_MISSING");
        if (!finalizedAuthority) await authorityStore.seal(previousSnapshot);
        const timelineId = JSON.parse(snapshot.authority.canonicalWorldEventLedgerJson).timeline.id;
        authorityStore = await createAuthorityStore(timelineId);
        finalizedAuthority = false;
        await authorityStore.append(snapshot);
      },
      onVerify: async (snapshot, { godotTraceJson }) => {
        if (snapshot.commands.length < 1 || typeof godotTraceJson !== "string") fail("R22_REAL_MOVEMENT_EVIDENCE_REQUIRED");
        await authorityStore.finalize(snapshot, { godotTraceJson }); finalizedAuthority = true;
      },
    };
    let activeTimelineId = initialTimelineId;
    if (resume) {
      const recovered = await recoverR20UnfinishedTimeline({ npcRunRoot, temporaryRoot: trustedRoot.path, writerLease });
      if (!recovered.recovered || recovered.emptyTimeline !== null || recovered.evidencePending.length !== 0 ||
          recovered.qualificationPending.length !== 0) fail("R22_RECOVERY_TIMELINE_INVALID");
      const recovery = recovered.recovered;
      activeTimelineId = JSON.parse(recovery.authorityManifestJson).timelineId;
      if (recovery.authorityManifestJson !== manifestFor(activeTimelineId) || recovery.behaviorPolicyJson !== source.behaviorPolicyJson ||
          recovery.entityBindingJson !== source.npcEntityBindingJson) fail("R22_RECOVERY_SESSION_IDENTITY_MISMATCH");
      const history = await loadR22LiveRecoveryHistory({ npcRunRoot, cognitionRunRoot, temporaryRoot: trustedRoot.path,
        source, manifestFor, hostRunId, cognitionPolicyJson, preparedBehavior: behavior.prepared,
        initialBehaviorState: behavior.initialState, recovery });
      coordinator = await rebuildR22RecoveredCoordinator({ source, preparedBehavior: behavior.prepared,
        initialBehaviorState: behavior.initialState, initialTimelineId, auditedTimelineIds: history.auditedTimelineIds,
        recovery, sessionToken, commandSelector: gate.commandSelector, ...coordinatorHooks });
      await history.revalidate();
      await revalidateR22FileRecord(sessionManifestRecord, 16 * 1024, trustedRoot);
      authorityStore = await resumeR20TimelineStore({ npcRunRoot, temporaryRoot: trustedRoot.path, recovery,
        behaviorPolicyJson: source.behaviorPolicyJson, entityBindingJson: source.npcEntityBindingJson, writerLease });
      recoveryCommands = exportR20Coordinator(coordinator).commands;
    } else {
      await publishR22Artifacts({ temporaryRoot: trustedRoot.path, output: cognitionRunRoot,
        artifacts: new Map([["cognition-session-manifest.json", sessionManifestJson]]),
        beforeRename: () => revalidateQualifiedSource(source, trustedRoot) });
      sessionManifestRecord = await readBoundSessionManifest();
      authorityStore = await createAuthorityStore(initialTimelineId);
      coordinator = createR20Coordinator({ authoritySession: authority.session, preparedBehavior: behavior.prepared,
        initialBehaviorState: behavior.initialState, entityBindingSha256: sha256(source.npcEntityBindingJson),
        sessionToken, commandSelector: gate.commandSelector, ...coordinatorHooks });
    }
    if (!coordinator) fail("R22_R20_COORDINATOR_FAILED");
    if (!resume) await authorityStore.append(exportR20Coordinator(coordinator));
    const host = await openTimelineHost(activeTimelineId, resume);
    if (resume) {
      // The durable result is rebuilt locally before the listener or Godot is
      // started. The provider is never involved in this recovery path.
      recoveryResult = requireOk(await recoverR22TransactionalHost(host), "R22_RECOVERY_HOST_INVALID");
      if (recoveryResult.providerReplayRequests !== 0) fail("R22_RECOVERY_HOST_INVALID");
      sequence = inspectR22CallStore(callStore).checkpoint.latestSequence;
      if (recoveryResult.status === "queued_for_r20") {
        const state = exportR20Coordinator(coordinator);
        const selected = selectEligibleNpcBehaviorCommand({ prepared: behavior.prepared, runtimeSnapshot: state.authority.runtimeSnapshot,
          runtimeInspection: state.authority.inspection, worldEventLedgerJson: state.authority.canonicalWorldEventLedgerJson,
          behaviorState: state.behaviorState, actorEntityId: recoveryResult.actorEntityId,
          expectedIntentId: recoveryResult.mappedIntentId, expectedNpcIntentSha256: recoveryResult.mappedIntentSha256 });
        if (!selected?.ok || selected.status !== "command" || sha256(selected.command.npcIntentJson) !== recoveryResult.mappedIntentSha256) {
          fail("R22_RECOVERY_COMMAND_INVALID");
        }
      }
    }
    controller = createR22LoopbackController({ host, selectorGate: gate, sessionToken, singleStep: true,
      authorityRequestHandler: (request) => handleR20CoordinatorRequestAsync(coordinator, request),
      authorityStateReader: async () => exportR20Coordinator(coordinator),
      rotateTimelineHost: async ({ timelineId, releasePreviousHost }) => {
        await releasePreviousHost();
        stores.delete(callStore);
        callStore = null;
        activeRuntime = null; sequence = 0;
        return openTimelineHost(timelineId);
      },
      turnFactory: async ({ actorEntityId, playerText, transientDialogue }) => {
        if (officialOperations !== null && (resume || officialOperations.inspect().credentialClaimed)) fail("R22_OFFICIAL_ONE_SHOT_UNAVAILABLE");
        await revalidateQualifiedSource(source, trustedRoot);
        const snapshot = await verifyAuthority();
        await projectEphemeralDerivedContext(source, snapshot.authority.canonicalWorldEventLedgerJson);
        const timelineId = JSON.parse(snapshot.authority.canonicalWorldEventLedgerJson).timeline.id;
        const turn = requireOk(createNpcCognitionTurn({ prepared: cognition.prepared, timelineId, actorEntityId,
          sequence: sequence + 1, runtimeSnapshot: snapshot.authority.runtimeSnapshot,
          runtimeInspection: snapshot.authority.inspection, worldEventLedgerJson: snapshot.authority.canonicalWorldEventLedgerJson,
          behaviorState: snapshot.behaviorState, playerText, transientDialogue }), "R22_LIVE_TURN_INVALID");
        const plan = requireOk(planNpcCognitionCall({ prepared: cognition.prepared, turn: turn.turn }), "R22_LIVE_PLAN_INVALID");
        sequence += 1; activeRuntime = { turn, plan };
        return { turnId: turn.npcCognitionTurnRequest.id, sequence, actorEntityId,
          callPlanJson: plan.canonicalNpcCognitionCallPlanJson, providerRequestJson: plan.providerPayloadJson,
          beforeLedgerPoint: point(snapshot.authority) };
      },
    });
    if (recoveryResult?.status === "queued_for_r20") {
      requireOk(restoreR22LoopbackController(controller, Object.fromEntries(
        ["turnId", "actorEntityId", "mappedIntentId", "mappedIntentSha256", "displayAckSha256"].map((key) => [key, recoveryResult[key]])
      )), "R22_RECOVERY_COMMAND_INVALID");
    }
    await revalidateQualifiedSource(source, trustedRoot);
    await revalidateR22FileRecord(sessionManifestRecord, 16 * 1024, trustedRoot);
    compositionReady = true;
    return Object.freeze({ controller, sessionToken, close, recordPhysicalEvidence, recoveryCommands,
      resumeQueuedAction: recoveryResult?.status === "queued_for_r20",
      processIdentity: Object.freeze({ sourceSha256: source.npc.record.sha256,
        implementationSha256: input.implementationSha256, godotBinarySha256: input.godotBinarySha256 }),
      revalidate: async () => {
        await revalidateQualifiedSource(source, trustedRoot);
        await revalidateR22FileRecord(sessionManifestRecord, 16 * 1024, trustedRoot);
      },
      ...(offlineManualProfile === null ? {} : { offlineManualProfile }),
      exportState: () => Object.freeze({ authority: exportR20Coordinator(coordinator), fakeDispatches,
        recovery: recoveryResult, callStore: inspectR22CallStore(callStore),
        providerMode: input.providerMode, realProviderRequests: officialOperations?.inspect().providerRequests ?? 0,
        sourceCredentialReads: officialOperations?.inspect().sourceCredentialReads ?? 0,
        ...(offlineManualProfile === null ? {} : { offlineManualProfile }) }),
    });
  } catch (error) {
    try { await close(); } catch { fail("R22_LIVE_COMPOSITION_CLEANUP_FAILED"); }
    throw error;
  }
}
