import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { computeNpcCognitionApprovalHash } from "../src/index.mjs";

export const sha = (digit) => `sha256:${String(digit).repeat(64).slice(0, 64)}`;
export const choice = (digit) => `choice-${String(digit).repeat(64).slice(0, 64)}`;
export const canonical = (value) => canonicalizeJsonValue(value);

export const cognitionLimits = () => ({
  turnsPerTimeline: 64,
  turnsPerActor: 32,
  callsPerTimeline: 16,
  callsPerActor: 8,
  concurrentCalls: 1,
  candidateActionsPerTurn: 64,
  memoryEpisodesPerActor: 16,
  relationshipEdgesPerActor: 64,
  transientDialogueExchanges: 4,
  transientDialogueBytes: 8192,
  playerTextBytes: 4096,
  derivedContextBytes: 16384,
  providerRequestBytes: 32768,
  providerResponseBytes: 65536,
  dialogueBytes: 2048,
  dialogueLines: 8,
  maxOutputTokens: 512,
  timeoutMs: 30000,
  approvalLifetimeMs: 300000,
});

export const cognitionBudgets = () => ({
  perCallMicrousd: 10000,
  perTimelineMicrousd: 160000,
  perHostRunMicrousd: 1000000,
});

export const policy = () => ({
  format: "matrix-oasis.npc-cognition-policy",
  formatVersion: "0.1.0",
  canonicalization: "matrix-oasis.canonical-json/1",
  id: "cognition-policy-one",
  contentVersion: "1.0.0",
  identities: {
    runtimePackSha256: sha("1"),
    runtimeReceiptSha256: sha("2"),
    authorityPolicySha256: sha("3"),
    behaviorPolicySha256: sha("4"),
    entityBindingSha256: sha("5"),
    derivedStateBundleSha256: sha("6"),
  },
  actors: [
    {
      actorEntityId: "actor-one",
      safeActions: [
        { nodeId: "node-one", actionId: "action-one" },
        { nodeId: "node-one", actionId: "action-two" },
      ],
    },
    {
      actorEntityId: "actor-two",
      safeActions: [{ nodeId: "node-two", actionId: "action-three" }],
    },
  ],
  limits: cognitionLimits(),
  budgets: cognitionBudgets(),
});

export const turnRequest = () => ({
  format: "matrix-oasis.npc-cognition-turn-request",
  formatVersion: "0.1.0",
  canonicalization: "matrix-oasis.canonical-json/1",
  id: "turn-one",
  timelineId: "timeline-one",
  actorEntityId: "actor-one",
  sequence: 1,
  observed: {
    revision: 2,
    headSha256: sha("7"),
    runtimeSnapshotSha256: sha("8"),
  },
  derivedStateBundleSha256: sha("6"),
  playerText: "Hello.\nCan you help?",
});

export const callPlan = () => {
  const value = {
    format: "matrix-oasis.npc-cognition-call-plan",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    turnSha256: sha("9"),
    contextSha256: sha("a"),
    candidateSha256: sha("b"),
    providerPayloadSha256: sha("c"),
    responseSchemaSha256: sha("d"),
    endpoint: "https://api.openai.com/v1/responses",
    model: "gpt-5.6-luna",
    reasoningEffort: "none",
    priceLock: {
      inputMicrousdPerMillionTokens: 200000,
      cachedInputMicrousdPerMillionTokens: 20000,
      cacheWriteInputMicrousdPerMillionTokens: 250000,
      outputMicrousdPerMillionTokens: 1200000,
    },
    maxOutputTokens: 512,
    timeoutMs: 30000,
    maxCostMicrousd: 10000,
    requestBytes: 2048,
    requestLimit: 1,
    retryLimit: 0,
    retentionPolicyVersion: "openai-api-data-controls-2026-09-03",
    retention: {
      store: false,
      zeroDataRetentionClaimed: false,
      abuseMonitoringMaxDays: 30,
      promptCachingPossible: true,
    },
    approval: { hash: sha("0"), expiresAfterMs: 300000 },
  };
  value.approval.hash = computeNpcCognitionApprovalHash(value);
  return value;
};

export const dialogueProposal = () => ({
  format: "matrix-oasis.npc-dialogue-proposal",
  formatVersion: "0.1.0",
  canonicalization: "matrix-oasis.canonical-json/1",
  contextSha256: sha("a"),
  dialogueText: "I can try that.",
  actionChoiceId: choice("e"),
});

export const ledgerPoint = (revision, headDigit, snapshotDigit) => ({
  revision,
  headSha256: revision === 0 ? null : sha(headDigit),
  runtimeSnapshotSha256: sha(snapshotDigit),
});

export const turnReceipt = () => ({
  format: "matrix-oasis.npc-cognition-turn-receipt",
  formatVersion: "0.1.0",
  canonicalization: "matrix-oasis.canonical-json/1",
  turnSha256: sha("9"),
  callPlanSha256: sha("f"),
  approvalSha256: callPlan().approval.hash,
  proposalSha256: sha("e"),
  requestCount: 1,
  status: "finalized",
  statusHistory: ["planned", "approved", "reserved", "dispatching", "validated", "queued_for_r20", "adjudicated", "finalized"],
  requestedModel: "gpt-5.6-luna",
  returnedModel: "gpt-5.6-luna",
  usage: { inputTokens: 100, cachedInputTokens: 0, cacheWriteInputTokens: 0, outputTokens: 20, totalTokens: 120 },
  budget: { reservedMicrousd: 10000, actualMicrousd: 44 },
  fallbackReason: "NPC_COGNITION_FALLBACK_NONE",
  actionChoiceId: choice("e"),
  mappedIntentSha256: sha("1"),
  adjudicationResultSha256: sha("2"),
  ledger: {
    before: ledgerPoint(2, "7", "8"),
    after: ledgerPoint(3, "3", "4"),
  },
  redaction: {
    playerTextStored: false,
    contextStored: false,
    providerPayloadStored: false,
    dialogueTextStored: false,
    responseIdStored: false,
    credentialStored: false,
    rawErrorStored: false,
  },
});

export const dialogueOnlyReceipt = () => {
  const value = turnReceipt();
  value.statusHistory = ["planned", "approved", "reserved", "dispatching", "validated", "dialogue_only", "finalized"];
  value.actionChoiceId = null;
  value.mappedIntentSha256 = null;
  value.adjudicationResultSha256 = null;
  value.ledger.after = structuredClone(value.ledger.before);
  return value;
};

export const preDispatchFallbackReceipt = () => {
  const value = turnReceipt();
  value.proposalSha256 = null;
  value.requestCount = 0;
  value.statusHistory = ["planned", "fallback", "finalized"];
  value.returnedModel = null;
  value.usage = { inputTokens: 0, cachedInputTokens: 0, cacheWriteInputTokens: 0, outputTokens: 0, totalTokens: 0 };
  value.budget = { reservedMicrousd: 0, actualMicrousd: 0 };
  value.fallbackReason = "NPC_COGNITION_FALLBACK_APPROVAL_DECLINED";
  value.actionChoiceId = null;
  value.mappedIntentSha256 = null;
  value.adjudicationResultSha256 = null;
  value.ledger.after = structuredClone(value.ledger.before);
  return value;
};

export const trace = () => ({
  format: "matrix-oasis.npc-cognition-trace",
  formatVersion: "0.1.0",
  canonicalization: "matrix-oasis.canonical-json/1",
  timelineId: "timeline-one",
  policySha256: sha("5"),
  initialLedger: ledgerPoint(2, "7", "8"),
  receipts: [
    {
      sequence: 1,
      actorEntityId: "actor-one",
      turnSha256: sha("9"),
      callPlanSha256: sha("f"),
      turnReceiptSha256: sha("1"),
      requestCount: 1,
      actualMicrousd: 44,
      outcome: "adjudicated",
      actionChoiceId: choice("e"),
      beforeLedger: ledgerPoint(2, "7", "8"),
      afterLedger: ledgerPoint(3, "3", "4"),
      interveningAuthorityEntrySha256: [],
    },
    {
      sequence: 2,
      actorEntityId: "actor-two",
      turnSha256: sha("a"),
      callPlanSha256: sha("b"),
      turnReceiptSha256: sha("c"),
      requestCount: 1,
      actualMicrousd: 20,
      outcome: "dialogue_only",
      actionChoiceId: null,
      beforeLedger: ledgerPoint(5, "5", "6"),
      afterLedger: ledgerPoint(5, "5", "6"),
      interveningAuthorityEntrySha256: [sha("4"), sha("5")],
    },
  ],
  totals: {
    turns: 2,
    providerRequests: 2,
    actualMicrousd: 64,
    fallbackTurns: 0,
    actionChoiceTurns: 1,
    adjudicatedTurns: 1,
  },
  throughRevision: 5,
  throughHeadSha256: sha("5"),
});

export const qualificationReport = () => ({
  format: "matrix-oasis.npc-cognition-qualification-report",
  formatVersion: "0.1.0",
  canonicalization: "matrix-oasis.canonical-json/1",
  profile: "matrix-oasis.bounded-npc-cognition/1",
  stage: "offline",
  policySha256: sha("1"),
  traceSha256: sha("2"),
  worldEventLedgerReplayReportSha256: sha("3"),
  projectionQualificationReportSha256: sha("4"),
  offlineCases: [
    { caseId: "case-alpha", evidenceSha256: sha("5"), traceSha256: sha("6") },
    { caseId: "case-beta", evidenceSha256: sha("7"), traceSha256: sha("8") },
    { caseId: "case-gamma", evidenceSha256: sha("9"), traceSha256: sha("a") },
  ],
  godotEvidenceSha256: sha("b"),
  performanceEvidenceSha256: sha("c"),
  providerCalls: { fake: 3, real: 0, replay: 0 },
  replay: {
    modelOutputReproducible: false,
    dialogueContentRetained: false,
    providerReplayRequests: 0,
  },
  markers: [
    "R22_DIALOGUE_BUDGET_ENFORCED",
    "R22_FALLBACK_PLAYABLE",
    "R22_UNTRUSTED_OUTPUT_ADJUDICATED",
  ],
});
