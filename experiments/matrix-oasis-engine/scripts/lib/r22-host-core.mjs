import { createHash, randomBytes, timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";
import {
  NPC_COGNITION_LIMITS,
  validateNpcCognitionCallPlanJson,
  validateNpcCognitionTurnReceiptJson,
  validateNpcDialogueProposalJson,
} from "@matrix-oasis/npc-cognition-contracts";
import {
  validateNpcAdjudicationResultJson,
  validateWorldEventLedgerJson,
} from "@matrix-oasis/npc-authority-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  computeR22DisplayAckHash,
  getR22CallStoreIdentity,
  inspectR22CallStore,
  markR22CallDispatching,
  publishR22TurnReceipt,
  readR22ActiveCallArtifacts,
  readR22FinalizedTurnReceipt,
  recordR22DisplayAcknowledged,
  recordR22PlannedCall,
  recordR22ValidatedProposal,
  reserveR22CallBudget,
} from "./r22-call-store.mjs";

const SHA256 = /^sha256:[0-9a-f]{64}$/u;
const CHOICE = /^choice-[0-9a-f]{64}$/u;
const IDENTIFIER = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/u;
const CANONICALIZATION = "matrix-oasis.canonical-json/1";
const ZERO_USAGE = Object.freeze({ inputTokens: 0, cachedInputTokens: 0, cacheWriteInputTokens: 0, outputTokens: 0, totalTokens: 0 });
const REDACTION = Object.freeze({
  playerTextStored: false,
  contextStored: false,
  providerPayloadStored: false,
  dialogueTextStored: false,
  responseIdStored: false,
  credentialStored: false,
  rawErrorStored: false,
});
const VALIDATED_FALLBACK_CODES = new Set(["R22_CONTEXT_STALE", "R22_CHOICE_INVALID"]);
const QUEUED_FALLBACK_CODES = new Set(["R22_R20_UNAVAILABLE", "R22_R19_FAILURE", "R22_R20_SELECTION_STALE", "R22_DISPLAY_UNCONFIRMED"]);
const R20_ROUTES = new Map([
  ["GET\0/v1/command", true],
  ["POST\0/v1/arrived", true],
  ["POST\0/v1/mirror", true],
  ["POST\0/v1/reset", true],
  ["POST\0/v1/verify", true],
]);

export const R22_COGNITION_HOST = "127.0.0.1";
export const R22_COGNITION_HOST_PORT = 43122;
export const R22_COGNITION_MAX_BODY_BYTES = 65_536;
export const R22_COGNITION_CLOSE_TIMEOUT_MS = 5_000;
export const R22_DIALOGUE_DISPLAY_LIFETIME_MS = 60_000;
export const R22_TURN_PREPARATION_LIFETIME_MS = 60_000;

const FALLBACK_BY_PROVIDER_CODE = Object.freeze({
  R22_PROVIDER_CREDENTIAL_UNAVAILABLE: "NPC_COGNITION_FALLBACK_PROVIDER_CREDENTIAL_UNAVAILABLE",
  R22_PROVIDER_TIMEOUT: "NPC_COGNITION_FALLBACK_PROVIDER_TIMEOUT",
  R22_PROVIDER_NETWORK_AMBIGUOUS: "NPC_COGNITION_FALLBACK_PROVIDER_NETWORK_AMBIGUOUS",
  R22_PROVIDER_REFUSED: "NPC_COGNITION_FALLBACK_PROVIDER_REFUSED",
  R22_PROVIDER_RESPONSE_INVALID: "NPC_COGNITION_FALLBACK_PROVIDER_RESPONSE_INVALID",
  R22_PROVIDER_RESPONSE_LIMIT_EXCEEDED: "NPC_COGNITION_FALLBACK_PROVIDER_RESPONSE_LIMIT_EXCEEDED",
  R22_PROVIDER_MODEL_MISMATCH: "NPC_COGNITION_FALLBACK_MODEL_MISMATCH",
  R22_PROVIDER_USAGE_INVALID: "NPC_COGNITION_FALLBACK_USAGE_INVALID",
  R22_APPROVAL_EXPIRED_PRE_REQUEST: "NPC_COGNITION_FALLBACK_APPROVAL_EXPIRED_PRE_REQUEST",
  R22_UNTRUSTED_OUTPUT_REJECTED: "NPC_COGNITION_FALLBACK_UNTRUSTED_OUTPUT_REJECTED",
  R22_ACTION_CHOICE_UNKNOWN: "NPC_COGNITION_FALLBACK_ACTION_CHOICE_UNKNOWN",
  R22_CONTEXT_STALE: "NPC_COGNITION_FALLBACK_CONTEXT_STALE",
  R22_APPROVAL_MISMATCH: "NPC_COGNITION_FALLBACK_PROVIDER_FAILURE",
});

const FALLBACK_BY_LOCAL_CODE = Object.freeze({
  R22_CONTEXT_STALE: "NPC_COGNITION_FALLBACK_CONTEXT_STALE",
  R22_UNTRUSTED_OUTPUT_REJECTED: "NPC_COGNITION_FALLBACK_UNTRUSTED_OUTPUT_REJECTED",
  R22_ACTION_CHOICE_UNKNOWN: "NPC_COGNITION_FALLBACK_ACTION_CHOICE_UNKNOWN",
  R22_CHOICE_INVALID: "NPC_COGNITION_FALLBACK_CHOICE_INVALID",
  R22_R20_UNAVAILABLE: "NPC_COGNITION_FALLBACK_R20_UNAVAILABLE",
  R22_R19_FAILURE: "NPC_COGNITION_FALLBACK_R19_FAILURE",
  R22_R20_SELECTION_STALE: "NPC_COGNITION_FALLBACK_R20_SELECTION_STALE",
  R22_DISPLAY_UNCONFIRMED: "NPC_COGNITION_FALLBACK_DISPLAY_UNCONFIRMED",
});

const hostStates = new WeakMap();
const selectorGateStates = new WeakMap();
const loopbackStates = new WeakMap();

function defaultDisplayScheduler(callback, delayMs) {
  const timer = setTimeout(() => { void callback(); }, delayMs);
  timer.unref?.();
  return () => clearTimeout(timer);
}

export class R22HostOperationalError extends Error {
  constructor() {
    super("NPC_COGNITION_INTERNAL_ERROR");
    this.name = "R22HostOperationalError";
    this.code = "NPC_COGNITION_INTERNAL_ERROR";
  }
}

function operational() {
  throw new R22HostOperationalError();
}

function sha256Text(text) {
  return `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`;
}

function deepFreeze(value, seen = new Set()) {
  if (value === null || typeof value !== "object" || seen.has(value)) return value;
  seen.add(value);
  for (const key of Reflect.ownKeys(value)) deepFreeze(value[key], seen);
  return Object.freeze(value);
}

function failure(code) {
  return deepFreeze({ ok: false, diagnostics: [{ code, path: "" }] });
}

function exactObject(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype &&
    Reflect.ownKeys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function constantTimeEqual(left, right) {
  if (typeof left !== "string" || typeof right !== "string") return false;
  const leftBytes = Buffer.from(left, "utf8");
  const rightBytes = Buffer.from(right, "utf8");
  return leftBytes.length === rightBytes.length && timingSafeEqual(leftBytes, rightBytes);
}

function canonicalCallPlan(text) {
  if (typeof text !== "string") return null;
  let value;
  try {
    value = JSON.parse(text);
    const report = validateNpcCognitionCallPlanJson(text);
    if (canonicalizeJsonValue(value) !== text || report?.valid !== true || report.diagnostics?.length !== 0) return null;
  } catch {
    return null;
  }
  return value;
}

function canonicalDialogueProposal(text) {
  if (typeof text !== "string") return null;
  try {
    const providerValue = JSON.parse(text);
    if (canonicalizeJsonValue(providerValue) !== text || !providerValue || Array.isArray(providerValue)) return null;
    const rawKeys = ["actionChoiceId", "contextSha256", "dialogueText"];
    const contractKeys = ["actionChoiceId", "canonicalization", "contextSha256", "dialogueText", "format", "formatVersion"];
    const keys = Object.keys(providerValue).sort();
    const isRaw = keys.length === rawKeys.length && keys.every((key, index) => key === rawKeys[index]);
    const isContract = keys.length === contractKeys.length && keys.every((key, index) => key === contractKeys[index]);
    if (!isRaw && !isContract) return null;
    const value = isRaw
      ? {
        format: "matrix-oasis.npc-dialogue-proposal",
        formatVersion: "0.1.0",
        canonicalization: CANONICALIZATION,
        ...providerValue,
      }
      : providerValue;
    const canonicalJson = canonicalizeJsonValue(value);
    const report = validateNpcDialogueProposalJson(canonicalJson);
    if (report?.valid !== true || report.diagnostics?.length !== 0) return null;
    return { value, canonicalJson, sha256: sha256Text(canonicalJson) };
  } catch {
    return null;
  }
}

function validLedgerPoint(value) {
  return exactObject(value, ["revision", "headSha256", "runtimeSnapshotSha256"]) &&
    Number.isSafeInteger(value.revision) && value.revision >= 0 && value.revision <= 10_000 &&
    (value.revision === 0 ? value.headSha256 === null : SHA256.test(value.headSha256 ?? "")) &&
    SHA256.test(value.runtimeSnapshotSha256 ?? "");
}

function captureUsage(value) {
  const keys = ["inputTokens", "cachedInputTokens", "cacheWriteInputTokens", "outputTokens", "totalTokens"];
  if (!exactObject(value, keys) || keys.some((key) => !Number.isSafeInteger(value[key]) || value[key] < 0) ||
      value.inputTokens + value.outputTokens !== value.totalTokens ||
      value.cachedInputTokens + value.cacheWriteInputTokens > value.inputTokens) return null;
  return structuredClone(value);
}

function receiptCostFromUsage(usage, plan) {
  const ordinary = usage.inputTokens - usage.cachedInputTokens - usage.cacheWriteInputTokens;
  const numerator = BigInt(ordinary) * BigInt(plan.priceLock.inputMicrousdPerMillionTokens) +
    BigInt(usage.cachedInputTokens) * BigInt(plan.priceLock.cachedInputMicrousdPerMillionTokens) +
    BigInt(usage.cacheWriteInputTokens) * BigInt(plan.priceLock.cacheWriteInputMicrousdPerMillionTokens) +
    BigInt(usage.outputTokens) * BigInt(plan.priceLock.outputMicrousdPerMillionTokens);
  return Number((numerator + 999_999n) / 1_000_000n);
}

function stateFor(host) {
  const state = hostStates.get(host);
  if (!state) operational();
  return state;
}

function selectorStateFor(gate) {
  const state = selectorGateStates.get(gate);
  if (!state) operational();
  return state;
}

function heldSelection() {
  return deepFreeze({ ok: true, status: "quiescent" });
}

export function createR22CognitionSelectorGate({ commandSelector, queuedCommandSelector }) {
  if (typeof commandSelector !== "function" || typeof queuedCommandSelector !== "function") operational();
  const handle = Object.create(null);
  const state = {
    commandSelector,
    queuedCommandSelector,
    mode: "open",
    actorEntityId: null,
    mappedIntentId: null,
    mappedIntentSha256: null,
    mismatch: false,
  };
  const gatedSelector = (input) => {
    if (state.mode === "open") return state.commandSelector(input);
    if (state.mode !== "queued") return heldSelection();
    let selection;
    try {
      selection = state.queuedCommandSelector(Object.freeze({
        ...input,
        actorEntityId: state.actorEntityId,
        expectedIntentId: state.mappedIntentId,
        expectedNpcIntentSha256: state.mappedIntentSha256,
      }));
    } catch {
      state.mismatch = true;
      return heldSelection();
    }
    const command = selection?.status === "command" ? selection.command : null;
    if (selection?.ok !== true || !command || command.actorEntityId !== state.actorEntityId || command.intentId !== state.mappedIntentId ||
        typeof command.npcIntentJson !== "string" || sha256Text(command.npcIntentJson) !== state.mappedIntentSha256) {
      state.mismatch = true;
      return heldSelection();
    }
    state.mode = "issued";
    return selection;
  };
  Object.defineProperty(handle, "commandSelector", { value: gatedSelector, enumerable: true });
  Object.freeze(handle);
  selectorGateStates.set(handle, state);
  return handle;
}

function acquireSelectorGate(gate) {
  const state = selectorStateFor(gate);
  if (state.mode !== "open") return false;
  state.mode = "held";
  state.actorEntityId = null;
  state.mappedIntentId = null;
  state.mappedIntentSha256 = null;
  state.mismatch = false;
  return true;
}

function queueSelectorGate(gate, actorEntityId, mappedIntentId, mappedIntentSha256) {
  const state = selectorStateFor(gate);
  if (state.mode !== "held" || !IDENTIFIER.test(actorEntityId ?? "") || !IDENTIFIER.test(mappedIntentId ?? "") || !SHA256.test(mappedIntentSha256 ?? "")) return false;
  state.mode = "queued";
  state.actorEntityId = actorEntityId;
  state.mappedIntentId = mappedIntentId;
  state.mappedIntentSha256 = mappedIntentSha256;
  state.mismatch = false;
  return true;
}

function releaseSelectorGate(gate) {
  const state = selectorStateFor(gate);
  state.mode = "open";
  state.actorEntityId = null;
  state.mappedIntentId = null;
  state.mappedIntentSha256 = null;
  state.mismatch = false;
}

export function restoreR22CognitionSelectorGate(gate, input) {
  const state = selectorStateFor(gate);
  if (!exactObject(input, ["actorEntityId", "mappedIntentId", "mappedIntentSha256"]) ||
      !IDENTIFIER.test(input.actorEntityId ?? "") || !IDENTIFIER.test(input.mappedIntentId ?? "") || !SHA256.test(input.mappedIntentSha256 ?? "")) {
    return failure("R22_CONTEXT_STALE");
  }
  if (state.mode !== "open" || !acquireSelectorGate(gate)) return failure("R22_CALL_IN_FLIGHT");
  if (!queueSelectorGate(gate, input.actorEntityId, input.mappedIntentId, input.mappedIntentSha256)) {
    releaseSelectorGate(gate);
    return failure("R22_CONTEXT_STALE");
  }
  return deepFreeze({ ok: true });
}

function disclosureSha256(callPlanSha256, providerPayloadSha256) {
  return sha256Text(canonicalizeJsonValue({ callPlanSha256, providerPayloadSha256 }));
}

export function computeR22ApprovalDisclosureSha256(input) {
  if (!exactObject(input, ["callPlanSha256", "providerPayloadSha256"]) ||
      !SHA256.test(input.callPlanSha256 ?? "") || !SHA256.test(input.providerPayloadSha256 ?? "")) operational();
  return disclosureSha256(input.callPlanSha256, input.providerPayloadSha256);
}

function parseTurnInput(input) {
  const keys = ["turnId", "sequence", "actorEntityId", "callPlanJson", "providerRequestJson", "beforeLedgerPoint"];
  if (!exactObject(input, keys) || typeof input.turnId !== "string" || input.turnId.length > 96 || !IDENTIFIER.test(input.turnId) ||
      !Number.isSafeInteger(input.sequence) || input.sequence < 1 || !IDENTIFIER.test(input.actorEntityId ?? "") ||
      typeof input.providerRequestJson !== "string" || Buffer.byteLength(input.providerRequestJson, "utf8") > NPC_COGNITION_LIMITS.providerRequestBytes ||
      !validLedgerPoint(input.beforeLedgerPoint)) return null;
  const plan = canonicalCallPlan(input.callPlanJson);
  if (!plan || plan.turnId !== input.turnId || plan.requestBytes !== Buffer.byteLength(input.providerRequestJson, "utf8") ||
      plan.providerPayloadSha256 !== sha256Text(input.providerRequestJson)) return null;
  return { ...input, plan, callPlanSha256: sha256Text(input.callPlanJson) };
}

function captureHostOperations(operations) {
  const keys = ["keyReader", "providerExecutor", "proposalValidator", "adjudicationLookup"];
  if (!exactObject(operations, keys) || keys.some((key) => typeof operations[key] !== "function")) operational();
  return Object.freeze(Object.fromEntries(keys.map((key) => [key, operations[key]])));
}

export function createR22TransactionalHost({ store, operations, clock = () => Date.now(), randomBytesImplementation = randomBytes }) {
  try {
    const storeIdentity = getR22CallStoreIdentity(store);
    const capturedOperations = captureHostOperations(operations);
    if (typeof clock !== "function" || typeof randomBytesImplementation !== "function") operational();
    const handle = Object.freeze(Object.create(null));
    hostStates.set(handle, {
      store,
      storeIdentity,
      operations: capturedOperations,
      clock,
      randomBytes: randomBytesImplementation,
      active: null,
      executionPromise: null,
    });
    return handle;
  } catch (error) {
    if (error instanceof R22HostOperationalError) throw error;
    operational();
  }
}

export async function readFinalizedR22CognitionTurn(host, input) {
  const state = stateFor(host);
  if (!exactObject(input, ["timelineId", "turnId", "callPlanSha256"]) ||
      input.timelineId !== state.storeIdentity.timelineId || !IDENTIFIER.test(input.turnId ?? "") ||
      !SHA256.test(input.callPlanSha256 ?? "")) return failure("R22_CONTEXT_STALE");
  try {
    const result = await readR22FinalizedTurnReceipt(state.store, input);
    return result === null ? failure("R22_CONTEXT_STALE") : deepFreeze({ ok: true, ...result });
  } catch {
    return failure("R22_CONTEXT_STALE");
  }
}

export async function registerR22CognitionTurn(host, input) {
  const state = stateFor(host);
  if (state.active !== null || state.executionPromise !== null) return failure("R22_CALL_IN_FLIGHT");
  const captured = parseTurnInput(input);
  if (!captured) return failure("R22_PROVIDER_RESPONSE_INVALID");
  const now = state.clock();
  if (!Number.isSafeInteger(now) || now < 0 || now > Number.MAX_SAFE_INTEGER - NPC_COGNITION_LIMITS.approvalLifetimeMs) operational();
  try {
    const persisted = await recordR22PlannedCall(state.store, {
      sequence: captured.sequence,
      turnId: captured.turnId,
      actorEntityId: captured.actorEntityId,
      callPlanJson: captured.callPlanJson,
      beforeLedgerPoint: captured.beforeLedgerPoint,
    });
    const disclosure = disclosureSha256(persisted.callPlanSha256, persisted.providerPayloadSha256);
    state.active = {
      ...captured,
      timelineId: state.storeIdentity.timelineId,
      callPlanSha256: persisted.callPlanSha256,
      disclosureSha256: disclosure,
      leaseExpiresAtMs: now + NPC_COGNITION_LIMITS.approvalLifetimeMs,
      approval: null,
      validated: null,
    };
    return deepFreeze({
      ok: true,
      turnId: captured.turnId,
      callPlanSha256: persisted.callPlanSha256,
      approvalContentSha256: persisted.approvalContentSha256,
      disclosureSha256: disclosure,
      providerRequestJson: captured.providerRequestJson,
      leaseExpiresAtMs: state.active.leaseExpiresAtMs,
    });
  } catch (error) {
    if (error?.code === "R22_CALL_IN_FLIGHT") return failure("R22_CALL_IN_FLIGHT");
    operational();
  }
}

export function issueR22CognitionApproval(host, input) {
  const state = stateFor(host);
  const active = state.active;
  if (!active) return failure("R22_APPROVAL_REQUIRED");
  if (!exactObject(input, ["turnId", "disclosureSha256"]) || input.turnId !== active.turnId ||
      !constantTimeEqual(input.disclosureSha256, active.disclosureSha256)) return failure("R22_APPROVAL_MISMATCH");
  const now = state.clock();
  if (!Number.isSafeInteger(now) || now < 0 || now > Number.MAX_SAFE_INTEGER - NPC_COGNITION_LIMITS.approvalLifetimeMs) operational();
  if (now >= active.leaseExpiresAtMs) return failure("R22_APPROVAL_EXPIRED");
  if (active.approval !== null) {
    return active.approval.consumed ? failure("R22_APPROVAL_MISMATCH") : deepFreeze({ ok: true, approvalHash: active.approval.hash, expiresAtMs: active.approval.expiresAtMs });
  }
  const nonce = state.randomBytes(32);
  if (!(nonce instanceof Uint8Array) || nonce.byteLength !== 32) operational();
  const expiresAtMs = Math.min(active.leaseExpiresAtMs, now + NPC_COGNITION_LIMITS.approvalLifetimeMs);
  const hash = sha256Text(canonicalizeJsonValue({
    processEpochSha256: state.storeIdentity.processEpochSha256,
    nonceSha256: sha256Text(Buffer.from(nonce)),
    turnId: active.turnId,
    callPlanSha256: active.callPlanSha256,
    providerPayloadSha256: active.plan.providerPayloadSha256,
    disclosureSha256: active.disclosureSha256,
    expiresAtMs,
  }));
  active.approval = { hash, expiresAtMs, consumed: false };
  return deepFreeze({ ok: true, approvalHash: hash, expiresAtMs });
}

function buildReceipt(active, {
  history,
  requestCount,
  returnedModel = null,
  usage = ZERO_USAGE,
  actualMicrousd = 0,
  fallbackReason,
  proposalSha256 = null,
  actionChoiceId = null,
  mappedIntentSha256 = null,
  adjudicationResultSha256 = null,
  afterLedgerPoint = active.beforeLedgerPoint,
}) {
  const document = {
    format: "matrix-oasis.npc-cognition-turn-receipt",
    formatVersion: "0.1.0",
    canonicalization: CANONICALIZATION,
    turnSha256: active.plan.turnSha256,
    callPlanSha256: active.callPlanSha256,
    approvalSha256: active.plan.approval.hash,
    proposalSha256,
    requestCount,
    status: "finalized",
    statusHistory: [...history, "finalized"],
    requestedModel: active.plan.model,
    returnedModel,
    usage: structuredClone(usage),
    budget: {
      reservedMicrousd: history.includes("reserved") ? NPC_COGNITION_LIMITS.perCallMicrousd : 0,
      actualMicrousd,
    },
    fallbackReason,
    actionChoiceId,
    mappedIntentSha256,
    adjudicationResultSha256,
    ledger: { before: structuredClone(active.beforeLedgerPoint), after: structuredClone(afterLedgerPoint) },
    redaction: structuredClone(REDACTION),
  };
  const text = canonicalizeJsonValue(document);
  const report = validateNpcCognitionTurnReceiptJson(text);
  if (report?.valid !== true || report.diagnostics?.length !== 0) operational();
  return text;
}

async function finalize(host, active, receiptJson) {
  const state = stateFor(host);
  try {
    await publishR22TurnReceipt(state.store, receiptJson);
  } catch (error) {
    if (error instanceof R22HostOperationalError) throw error;
    operational();
  }
  const value = JSON.parse(receiptJson);
  state.active = null;
  return deepFreeze({ ok: true, status: value.statusHistory.at(-2), turnReceiptJson: receiptJson, turnReceiptSha256: sha256Text(receiptJson) });
}

export async function declineR22CognitionTurn(host, input) {
  const state = stateFor(host);
  const active = state.active;
  if (!active || !exactObject(input, ["turnId", "disclosureSha256"]) || input.turnId !== active.turnId ||
      !constantTimeEqual(input.disclosureSha256, active.disclosureSha256)) return failure("R22_APPROVAL_MISMATCH");
  if (state.executionPromise !== null) return failure("R22_CALL_IN_FLIGHT");
  if (active.approval) active.approval.consumed = true;
  try {
    const receipt = buildReceipt(active, {
      history: ["planned", "fallback"],
      requestCount: 0,
      fallbackReason: "NPC_COGNITION_FALLBACK_APPROVAL_DECLINED",
    });
    return await finalize(host, active, receipt);
  } catch {
    operational();
  }
}

function providerFallbackReason(code) {
  return FALLBACK_BY_PROVIDER_CODE[code] ?? "NPC_COGNITION_FALLBACK_PROVIDER_FAILURE";
}

function localFallbackReason(code) {
  return FALLBACK_BY_LOCAL_CODE[code] ?? "NPC_COGNITION_FALLBACK_UNTRUSTED_OUTPUT_REJECTED";
}

async function finalizeDispatchedFallback(host, active, diagnosticCode, providerResult, { requestCount = 1 } = {}) {
  const usage = captureUsage(providerResult?.usage) ?? structuredClone(ZERO_USAGE);
  const returnedModel = typeof providerResult?.returnedModel === "string" && providerResult.returnedModel.length <= 128
    ? providerResult.returnedModel
    : null;
  const expectedCost = requestCount === 1 ? receiptCostFromUsage(usage, active.plan) : null;
  const knownCost = requestCount === 1 && providerResult?.costUncertain === false &&
    Number.isSafeInteger(providerResult?.actualCostMicrousd) && providerResult.actualCostMicrousd === expectedCost &&
    expectedCost >= 0 && expectedCost <= NPC_COGNITION_LIMITS.perCallMicrousd;
  const receipt = buildReceipt(active, {
    history: ["planned", "approved", "reserved", "dispatching", "fallback"],
    requestCount,
    returnedModel: requestCount === 0 ? null : returnedModel,
    usage: requestCount === 0 ? ZERO_USAGE : usage,
    actualMicrousd: requestCount === 0 ? 0 : knownCost ? expectedCost : NPC_COGNITION_LIMITS.perCallMicrousd,
    fallbackReason: providerFallbackReason(diagnosticCode),
  });
  return finalize(host, active, receipt);
}

async function finalizeValidatedFallback(host, active, diagnosticCode, providerResult) {
  const proposal = canonicalDialogueProposal(providerResult?.proposalJson);
  const usage = captureUsage(providerResult.usage);
  const expectedCost = usage ? receiptCostFromUsage(usage, active.plan) : null;
  if (!proposal || !usage || providerResult.returnedModel !== active.plan.model || providerResult.requestCount !== 1 ||
      providerResult.costUncertain === true || !Number.isSafeInteger(providerResult.actualCostMicrousd) ||
      providerResult.actualCostMicrousd !== expectedCost || expectedCost < 0 || expectedCost > NPC_COGNITION_LIMITS.perCallMicrousd) {
    return finalizeDispatchedFallback(host, active, "R22_UNTRUSTED_OUTPUT_REJECTED", providerResult);
  }
  const state = stateFor(host);
  await recordR22ValidatedProposal(state.store, {
    callPlanSha256: active.callPlanSha256,
    proposalSha256: proposal.sha256,
    returnedModel: providerResult.returnedModel,
    usage,
    actualMicrousd: providerResult.actualCostMicrousd,
    actionChoiceId: proposal.value.actionChoiceId,
    mappedIntentId: null,
    mappedIntentSha256: null,
  });
  const receipt = buildReceipt(active, {
    history: ["planned", "approved", "reserved", "dispatching", "validated", "fallback"],
    requestCount: 1,
    returnedModel: providerResult.returnedModel,
    usage,
    actualMicrousd: providerResult.actualCostMicrousd,
    fallbackReason: localFallbackReason(diagnosticCode),
    proposalSha256: proposal.sha256,
    actionChoiceId: proposal.value.actionChoiceId,
  });
  return finalize(host, active, receipt);
}

async function executeOnce(host, active) {
  const state = stateFor(host);
  const now = state.clock();
  if (!Number.isSafeInteger(now) || now < 0) operational();
  if (now >= active.approval.expiresAtMs || now >= active.leaseExpiresAtMs) {
    const receipt = buildReceipt(active, {
      history: ["planned", "fallback"],
      requestCount: 0,
      fallbackReason: "NPC_COGNITION_FALLBACK_APPROVAL_EXPIRED",
    });
    return finalize(host, active, receipt);
  }
  const reservation = await reserveR22CallBudget(state.store, active.callPlanSha256);
  if (!reservation.ok) {
    const receipt = buildReceipt(active, {
      history: ["planned", "approved", "fallback"],
      requestCount: 0,
      fallbackReason: reservation.diagnosticCode === "R22_CALL_IN_FLIGHT"
        ? "NPC_COGNITION_FALLBACK_CALL_IN_FLIGHT"
        : "NPC_COGNITION_FALLBACK_BUDGET_EXHAUSTED",
    });
    return finalize(host, active, receipt);
  }
  await markR22CallDispatching(state.store, {
    callPlanSha256: active.callPlanSha256,
    approvalTokenSha256: active.approval.hash,
    approvalContentSha256: active.plan.approval.hash,
  });
  const stillLive = () => {
    const checkedAt = state.clock();
    if (!Number.isSafeInteger(checkedAt) || checkedAt < 0) operational();
    return checkedAt < active.approval.expiresAtMs && checkedAt < active.leaseExpiresAtMs;
  };
  if (!stillLive()) {
    return finalizeDispatchedFallback(host, active, "R22_APPROVAL_EXPIRED_PRE_REQUEST", null, { requestCount: 0 });
  }
  let apiKey;
  try {
    apiKey = await state.operations.keyReader();
  } catch {
    return finalizeDispatchedFallback(host, active, "R22_PROVIDER_CREDENTIAL_UNAVAILABLE", null, { requestCount: 0 });
  }
  if (typeof apiKey !== "string" || !/^[\u0021-\u007e]{1,8192}$/u.test(apiKey)) {
    return finalizeDispatchedFallback(host, active, "R22_PROVIDER_CREDENTIAL_UNAVAILABLE", null, { requestCount: 0 });
  }
  if (!stillLive()) {
    apiKey = undefined;
    return finalizeDispatchedFallback(host, active, "R22_APPROVAL_EXPIRED_PRE_REQUEST", null, { requestCount: 0 });
  }
  let providerResult;
  try {
    providerResult = await state.operations.providerExecutor({
      apiKey,
      callPlanJson: active.callPlanJson,
      providerRequestJson: active.providerRequestJson,
      approvalHash: active.plan.approval.hash,
    });
  } catch {
    return finalizeDispatchedFallback(host, active, "R22_PROVIDER_NETWORK_AMBIGUOUS", null);
  } finally {
    apiKey = undefined;
  }
  if (!providerResult || typeof providerResult !== "object" || ![0, 1].includes(providerResult.requestCount)) {
    return finalizeDispatchedFallback(host, active, "R22_PROVIDER_NETWORK_AMBIGUOUS", null);
  }
  if (providerResult.ok !== true) {
    const requestCount = providerResult.requestCount === 0 && providerResult.diagnosticCode === "R22_PROVIDER_CREDENTIAL_UNAVAILABLE" ? 0 : 1;
    return finalizeDispatchedFallback(host, active, providerResult.diagnosticCode, providerResult, { requestCount });
  }
  const usage = captureUsage(providerResult.usage);
  const expectedCost = usage ? receiptCostFromUsage(usage, active.plan) : null;
  if (providerResult.requestCount !== 1 || providerResult.costUncertain !== false || providerResult.returnedModel !== active.plan.model || !usage ||
      !Number.isSafeInteger(providerResult.actualCostMicrousd) || providerResult.actualCostMicrousd !== expectedCost ||
      expectedCost < 0 || expectedCost > NPC_COGNITION_LIMITS.perCallMicrousd || typeof providerResult.proposalJson !== "string") {
    return finalizeDispatchedFallback(host, active, "R22_PROVIDER_RESPONSE_INVALID", providerResult);
  }
  const proposal = canonicalDialogueProposal(providerResult.proposalJson);
  if (!proposal) return finalizeDispatchedFallback(host, active, "R22_UNTRUSTED_OUTPUT_REJECTED", providerResult);
  let validation;
  try {
    validation = await state.operations.proposalValidator({
      proposalJson: providerResult.proposalJson,
      callPlanJson: active.callPlanJson,
      turnId: active.turnId,
    });
  } catch {
    validation = null;
  }
  if (!validation || validation.ok !== true) {
    const diagnosticCode = validation?.diagnosticCode ?? validation?.diagnostics?.[0]?.code;
    return VALIDATED_FALLBACK_CODES.has(diagnosticCode)
      ? finalizeValidatedFallback(host, active, diagnosticCode, providerResult)
      : finalizeDispatchedFallback(host, active, diagnosticCode, providerResult);
  }
  const canonicalProposalJson = validation.canonicalNpcDialogueProposalJson ?? validation.canonicalProposalJson;
  if (canonicalProposalJson !== proposal.canonicalJson || validation.actionChoiceId !== proposal.value.actionChoiceId) {
    return finalizeDispatchedFallback(host, active, "R22_UNTRUSTED_OUTPUT_REJECTED", providerResult);
  }
  const proposalSha256 = proposal.sha256;
  const actionChoiceId = proposal.value.actionChoiceId;
  const mappedIntentSha256 = validation.mappedIntentSha256 ?? null;
  const mappedIntentId = actionChoiceId === null ? null : validation.command?.intentId ?? null;
  const plannedCandidate = actionChoiceId === null
    ? null
    : active.plan.candidateChoices.find((candidate) => candidate.choiceId === actionChoiceId) ?? null;
  if (!SHA256.test(proposalSha256 ?? "") ||
      (actionChoiceId === null
        ? mappedIntentSha256 !== null || mappedIntentId !== null
        : !CHOICE.test(actionChoiceId) || !IDENTIFIER.test(mappedIntentId ?? "") || !SHA256.test(mappedIntentSha256 ?? "") ||
          plannedCandidate?.intentSha256 !== mappedIntentSha256 || actionChoiceId !== `choice-${mappedIntentSha256.slice(7)}`)) {
    return finalizeValidatedFallback(host, active, "R22_CHOICE_INVALID", providerResult);
  }
  await recordR22ValidatedProposal(state.store, {
    callPlanSha256: active.callPlanSha256,
    proposalSha256,
    returnedModel: providerResult.returnedModel,
    usage,
    actualMicrousd: providerResult.actualCostMicrousd,
    actionChoiceId,
    mappedIntentId,
    mappedIntentSha256,
  });
  active.validated = {
    providerResult,
    proposalSha256,
    actionChoiceId,
    mappedIntentId,
    mappedIntentSha256,
    command: validation.command ?? null,
    dialogueText: proposal.value.dialogueText,
  };
  if (actionChoiceId === null) {
    return deepFreeze({
      ok: true,
      status: "dialogue_only",
      turnId: active.turnId,
      dialogueText: proposal.value.dialogueText,
      actionChoiceId: null,
      proposalSha256,
    });
  }
  return deepFreeze({
    ok: true,
    status: "queued_for_r20",
    turnId: active.turnId,
    dialogueText: proposal.value.dialogueText,
    actionChoiceId,
    mappedIntentId,
    mappedIntentSha256,
    proposalSha256,
    command: validation.command ?? null,
  });
}

export function executeApprovedR22CognitionTurn(host, input) {
  const state = stateFor(host);
  const active = state.active;
  if (!active || !exactObject(input, ["turnId", "approvalHash"]) || input.turnId !== active.turnId || active.approval === null) {
    return Promise.resolve(failure("R22_APPROVAL_REQUIRED"));
  }
  if (!constantTimeEqual(input.approvalHash, active.approval.hash)) return Promise.resolve(failure("R22_APPROVAL_MISMATCH"));
  if (state.executionPromise !== null) return state.executionPromise;
  if (active.approval.consumed) return Promise.resolve(failure("R22_APPROVAL_MISMATCH"));
  active.approval.consumed = true;
  const promise = executeOnce(host, active).catch((error) => {
    if (error instanceof R22HostOperationalError) throw error;
    operational();
  }).finally(() => {
    state.executionPromise = null;
  });
  state.executionPromise = promise;
  return promise;
}

function canonicalAdjudicationResult(text) {
  if (typeof text !== "string" || Buffer.byteLength(text, "utf8") > 1024 * 1024) return null;
  try {
    const value = JSON.parse(text);
    const report = validateNpcAdjudicationResultJson(text);
    return canonicalizeJsonValue(value) === text && report?.valid === true && report.diagnostics?.length === 0 ? value : null;
  } catch {
    return null;
  }
}

export async function acknowledgeR22CognitionDisplay(host, input) {
  const state = stateFor(host);
  const active = state.active;
  if (!active?.validated || !exactObject(input, ["turnId", "displayAckHash"]) || input.turnId !== active.turnId ||
      !SHA256.test(input.displayAckHash ?? "") || !SHA256.test(active.approval?.hash ?? "")) {
    return failure("R22_CONTEXT_STALE");
  }
  const expected = computeR22DisplayAckHash({
    approvalTokenSha256: active.approval.hash,
    turnSha256: active.plan.turnSha256,
    callPlanSha256: active.callPlanSha256,
    proposalSha256: active.validated.proposalSha256,
    actionChoiceId: active.validated.actionChoiceId,
  });
  if (!constantTimeEqual(input.displayAckHash, expected)) return failure("R22_CONTEXT_STALE");
  if (active.displayAckSha256 !== undefined && active.displayAckSha256 !== null) {
    if (!constantTimeEqual(active.displayAckSha256, expected)) return failure("R22_CONTEXT_STALE");
    if (active.validated.actionChoiceId !== null) {
      return deepFreeze({ ok: true, status: "queued_for_r20", displayAckSha256: expected });
    }
  }
  if (active.displayAckSha256 === undefined || active.displayAckSha256 === null) {
    try {
      await recordR22DisplayAcknowledged(state.store, {
        callPlanSha256: active.callPlanSha256,
        proposalSha256: active.validated.proposalSha256,
        displayAckSha256: expected,
      });
    } catch {
      return failure("R22_CONTEXT_STALE");
    }
    active.displayAckSha256 = expected;
  }
  if (active.validated.actionChoiceId !== null) {
    return deepFreeze({ ok: true, status: "queued_for_r20", displayAckSha256: expected });
  }
  const validated = active.validated;
  const receipt = buildReceipt(active, {
    history: ["planned", "approved", "reserved", "dispatching", "validated", "dialogue_only"],
    requestCount: 1,
    returnedModel: validated.providerResult.returnedModel,
    usage: validated.providerResult.usage,
    actualMicrousd: validated.providerResult.actualCostMicrousd,
    fallbackReason: "NPC_COGNITION_FALLBACK_NONE",
    proposalSha256: validated.proposalSha256,
  });
  return finalize(host, active, receipt);
}

export async function completeQueuedR22CognitionTurn(host, input) {
  const state = stateFor(host);
  const active = state.active;
  if (!active?.validated || active.validated.actionChoiceId === null || !SHA256.test(active.displayAckSha256 ?? "")) {
    return failure("R22_R20_UNAVAILABLE");
  }
  if (!exactObject(input, ["turnId", "status", "afterLedgerPoint", "adjudicationResultJson", "diagnosticCode"]) || input.turnId !== active.turnId) {
    return failure("R22_R20_UNAVAILABLE");
  }
  if (input.status === "adjudicated") {
    const adjudication = canonicalAdjudicationResult(input.adjudicationResultJson);
    if (!validLedgerPoint(input.afterLedgerPoint) || input.afterLedgerPoint.revision !== active.beforeLedgerPoint.revision + 1 ||
        !adjudication || input.diagnosticCode !== null || adjudication.replayed !== false ||
        adjudication.intentId !== active.validated.mappedIntentId || adjudication.revision !== input.afterLedgerPoint.revision ||
        adjudication.headSha256 !== input.afterLedgerPoint.headSha256 ||
        adjudication.beforeSnapshotSha256 !== active.beforeLedgerPoint.runtimeSnapshotSha256 ||
        adjudication.afterSnapshotSha256 !== input.afterLedgerPoint.runtimeSnapshotSha256) {
      return failure("R22_R19_FAILURE");
    }
    const validated = active.validated;
    const receipt = buildReceipt(active, {
      history: ["planned", "approved", "reserved", "dispatching", "validated", "queued_for_r20", "adjudicated"],
      requestCount: 1,
      returnedModel: validated.providerResult.returnedModel,
      usage: validated.providerResult.usage,
      actualMicrousd: validated.providerResult.actualCostMicrousd,
      fallbackReason: "NPC_COGNITION_FALLBACK_NONE",
      proposalSha256: validated.proposalSha256,
      actionChoiceId: validated.actionChoiceId,
      mappedIntentSha256: validated.mappedIntentSha256,
      adjudicationResultSha256: sha256Text(input.adjudicationResultJson),
      afterLedgerPoint: input.afterLedgerPoint,
    });
    return finalize(host, active, receipt);
  }
  if (input.status === "fallback" && QUEUED_FALLBACK_CODES.has(input.diagnosticCode) && input.afterLedgerPoint !== null && validLedgerPoint(input.afterLedgerPoint) &&
      canonicalizeJsonValue(input.afterLedgerPoint) === canonicalizeJsonValue(active.beforeLedgerPoint) && input.adjudicationResultJson === null) {
    const validated = active.validated;
    const reason = localFallbackReason(input.diagnosticCode);
    const receipt = buildReceipt(active, {
      history: ["planned", "approved", "reserved", "dispatching", "validated", "queued_for_r20", "fallback"],
      requestCount: 1,
      returnedModel: validated.providerResult.returnedModel,
      usage: validated.providerResult.usage,
      actualMicrousd: validated.providerResult.actualCostMicrousd,
      fallbackReason: reason,
      proposalSha256: validated.proposalSha256,
      actionChoiceId: validated.actionChoiceId,
      mappedIntentSha256: validated.mappedIntentSha256,
    });
    return finalize(host, active, receipt);
  }
  return failure("R22_R20_UNAVAILABLE");
}

function parseValidatedRecord(text, active) {
  if (typeof text !== "string") return null;
  try {
    const value = JSON.parse(text);
    const plan = active.plan;
    const keys = ["format", "formatVersion", "canonicalization", "callPlanSha256", "proposalSha256", "returnedModel", "usage", "actualMicrousd", "actionChoiceId", "mappedIntentId", "mappedIntentSha256"];
    const mappingInvalid = value.actionChoiceId === null
      ? value.mappedIntentId !== null || value.mappedIntentSha256 !== null
      : !CHOICE.test(value.actionChoiceId) ||
        ((value.mappedIntentId === null) !== (value.mappedIntentSha256 === null)) ||
        (value.mappedIntentId !== null && (
          !IDENTIFIER.test(value.mappedIntentId) ||
          !SHA256.test(value.mappedIntentSha256 ?? "") ||
          value.actionChoiceId !== `choice-${value.mappedIntentSha256.slice(7)}` ||
          plan?.candidateChoices?.find((candidate) => candidate.choiceId === value.actionChoiceId)?.intentSha256 !== value.mappedIntentSha256
        ));
    if (canonicalizeJsonValue(value) !== text || !exactObject(value, keys) || value.format !== "matrix-oasis.r22-validated-proposal-record" ||
        value.formatVersion !== "0.1.0" || value.canonicalization !== CANONICALIZATION || value.callPlanSha256 !== active.callPlanSha256 ||
        !SHA256.test(value.proposalSha256 ?? "") || !captureUsage(value.usage) || !Number.isSafeInteger(value.actualMicrousd) ||
        value.actualMicrousd < 0 || value.actualMicrousd > NPC_COGNITION_LIMITS.perCallMicrousd || mappingInvalid) return null;
    return value;
  } catch {
    return null;
  }
}

function parseDispatchRecord(text, active) {
  if (typeof text !== "string") return null;
  try {
    const value = JSON.parse(text);
    const keys = ["format", "formatVersion", "canonicalization", "callPlanSha256", "turnSha256", "approvalTokenSha256", "approvalContentSha256", "reservationMicrousd", "providerRequestLimit", "providerRetryLimit"];
    if (canonicalizeJsonValue(value) !== text || !exactObject(value, keys) ||
        value.format !== "matrix-oasis.r22-dispatch-record" || value.formatVersion !== "0.1.0" ||
        value.canonicalization !== CANONICALIZATION || value.callPlanSha256 !== active.callPlanSha256 ||
        value.turnSha256 !== active.plan.turnSha256 || !SHA256.test(value.approvalTokenSha256 ?? "") ||
        value.approvalContentSha256 !== active.plan.approval.hash ||
        value.reservationMicrousd !== NPC_COGNITION_LIMITS.perCallMicrousd || value.providerRequestLimit !== 1 || value.providerRetryLimit !== 0) return null;
    return value;
  } catch {
    return null;
  }
}

function parseDisplayAckRecord(text, validated, active, dispatch) {
  if (typeof text !== "string" || !validated || !dispatch) return null;
  try {
    const value = JSON.parse(text);
    const keys = ["format", "formatVersion", "canonicalization", "callPlanSha256", "proposalSha256", "displayAckSha256", "state"];
    if (canonicalizeJsonValue(value) !== text || !exactObject(value, keys) ||
        value.format !== "matrix-oasis.r22-display-ack-record" || value.formatVersion !== "0.1.0" ||
        value.canonicalization !== CANONICALIZATION || value.state !== "displayed" ||
        value.callPlanSha256 !== validated.callPlanSha256 || value.proposalSha256 !== validated.proposalSha256 ||
        !SHA256.test(value.displayAckSha256 ?? "") ||
        value.displayAckSha256 !== computeR22DisplayAckHash({
          approvalTokenSha256: dispatch.approvalTokenSha256,
          turnSha256: active.plan.turnSha256,
          callPlanSha256: active.callPlanSha256,
          proposalSha256: validated.proposalSha256,
          actionChoiceId: validated.actionChoiceId,
        })) return null;
    return value;
  } catch {
    return null;
  }
}

function activeFromRecovery(artifacts, storeIdentity) {
  const plan = canonicalCallPlan(artifacts.callPlanJson);
  if (!plan || plan.turnId !== artifacts.active.turnId || sha256Text(artifacts.callPlanJson) !== artifacts.active.callPlanSha256) operational();
  return {
    turnId: plan.turnId,
    sequence: artifacts.active.sequence,
    actorEntityId: artifacts.active.actorEntityId,
    timelineId: storeIdentity.timelineId,
    plan,
    callPlanJson: artifacts.callPlanJson,
    callPlanSha256: artifacts.active.callPlanSha256,
    beforeLedgerPoint: artifacts.active.beforeLedger,
    approval: { hash: null, expiresAtMs: 0, consumed: true },
    validated: null,
  };
}

export async function recoverR22TransactionalHost(host) {
  const state = stateFor(host);
  if (state.active !== null || state.executionPromise !== null) return failure("R22_CALL_IN_FLIGHT");
  const artifacts = await readR22ActiveCallArtifacts(state.store);
  if (artifacts === null) return deepFreeze({ ok: true, status: "idle", providerReplayRequests: 0 });
  const active = activeFromRecovery(artifacts, state.storeIdentity);
  if (artifacts.turnReceiptJson !== null) {
    await publishR22TurnReceipt(state.store, artifacts.turnReceiptJson);
    return deepFreeze({ ok: true, status: "finalized", providerReplayRequests: 0, turnReceiptSha256: sha256Text(artifacts.turnReceiptJson) });
  }
  const validated = parseValidatedRecord(artifacts.validatedProposalRecordJson, active);
  if (artifacts.validatedProposalRecordJson !== null && validated === null) operational();
  const dispatch = artifacts.dispatchRecordJson === null ? null : parseDispatchRecord(artifacts.dispatchRecordJson, active);
  if (artifacts.dispatchRecordJson !== null && dispatch === null) operational();
  const displayAck = parseDisplayAckRecord(artifacts.displayAckRecordJson, validated, active, dispatch);
  if (artifacts.displayAckRecordJson !== null && displayAck === null) operational();
  if (validated !== null) {
    const recoveredProvider = {
      returnedModel: validated.returnedModel,
      usage: validated.usage,
      actualCostMicrousd: validated.actualMicrousd,
    };
    active.validated = { providerResult: recoveredProvider, proposalSha256: validated.proposalSha256, actionChoiceId: validated.actionChoiceId, mappedIntentId: validated.mappedIntentId, mappedIntentSha256: validated.mappedIntentSha256, command: null };
    active.displayAckSha256 = displayAck?.displayAckSha256 ?? null;
    state.active = active;
    if (validated.mappedIntentSha256 === null) {
      if (displayAck === null) {
        const receipt = buildReceipt(active, {
          history: ["planned", "approved", "reserved", "dispatching", "validated", "fallback"],
          requestCount: 1,
          returnedModel: validated.returnedModel,
          usage: validated.usage,
          actualMicrousd: validated.actualMicrousd,
          fallbackReason: "NPC_COGNITION_FALLBACK_DISPLAY_UNCONFIRMED",
          proposalSha256: validated.proposalSha256,
        });
        const finalized = await finalize(host, active, receipt);
        return deepFreeze({ ...finalized, recovered: true, providerReplayRequests: 0 });
      }
      const receipt = buildReceipt(active, {
        history: ["planned", "approved", "reserved", "dispatching", "validated", "dialogue_only"],
        requestCount: 1,
        returnedModel: validated.returnedModel,
        usage: validated.usage,
        actualMicrousd: validated.actualMicrousd,
        fallbackReason: "NPC_COGNITION_FALLBACK_NONE",
        proposalSha256: validated.proposalSha256,
      });
      const finalized = await finalize(host, active, receipt);
      return deepFreeze({ ...finalized, recovered: true, providerReplayRequests: 0 });
    }
    if (displayAck === null) {
      const receipt = buildReceipt(active, {
        history: ["planned", "approved", "reserved", "dispatching", "validated", "fallback"],
        requestCount: 1,
        returnedModel: validated.returnedModel,
        usage: validated.usage,
        actualMicrousd: validated.actualMicrousd,
        fallbackReason: "NPC_COGNITION_FALLBACK_DISPLAY_UNCONFIRMED",
        proposalSha256: validated.proposalSha256,
        actionChoiceId: validated.actionChoiceId,
      });
      const finalized = await finalize(host, active, receipt);
      return deepFreeze({ ...finalized, recovered: true, providerReplayRequests: 0 });
    }
    let lookup;
    try {
      lookup = await state.operations.adjudicationLookup({
        intentId: validated.mappedIntentId,
        mappedIntentSha256: validated.mappedIntentSha256,
        beforeLedgerPoint: active.beforeLedgerPoint,
        callPlanSha256: active.callPlanSha256,
      });
    } catch {
      lookup = null;
    }
    if (lookup?.found === true) {
      const evidence = deriveAdjudicationFromLedger(lookup.canonicalWorldEventLedgerJson, active);
      if (!evidence) operational();
      const receipt = buildReceipt(active, {
        history: ["planned", "approved", "reserved", "dispatching", "validated", "queued_for_r20", "adjudicated"],
        requestCount: 1,
        returnedModel: validated.returnedModel,
        usage: validated.usage,
        actualMicrousd: validated.actualMicrousd,
        fallbackReason: "NPC_COGNITION_FALLBACK_NONE",
        proposalSha256: validated.proposalSha256,
        actionChoiceId: validated.actionChoiceId,
        mappedIntentSha256: validated.mappedIntentSha256,
        adjudicationResultSha256: evidence.adjudicationResultSha256,
        afterLedgerPoint: evidence.afterLedgerPoint,
      });
      const finalized = await finalize(host, active, receipt);
      return deepFreeze({ ...finalized, recovered: true, providerReplayRequests: 0 });
    }
    return deepFreeze({
      ok: true,
      status: "queued_for_r20",
      turnId: active.turnId,
      sequence: active.sequence,
      actorEntityId: active.actorEntityId,
      recovered: true,
      providerReplayRequests: 0,
      actionChoiceId: validated.actionChoiceId,
      mappedIntentId: validated.mappedIntentId,
      mappedIntentSha256: validated.mappedIntentSha256,
      displayAckSha256: displayAck.displayAckSha256,
    });
  }
  state.active = active;
  if (artifacts.dispatchRecordJson !== null) {
    const receipt = buildReceipt(active, {
      history: ["planned", "approved", "reserved", "dispatching", "fallback"],
      requestCount: 1,
      actualMicrousd: NPC_COGNITION_LIMITS.perCallMicrousd,
      fallbackReason: "NPC_COGNITION_FALLBACK_DISPATCH_CRASH_UNCERTAIN",
    });
    const finalized = await finalize(host, active, receipt);
    return deepFreeze({ ...finalized, recovered: true, providerReplayRequests: 0 });
  }
  const budget = inspectR22CallStore(state.store).hostBudget;
  if (budget.reservedMicrousd > 0) {
    const receipt = buildReceipt(active, {
      history: ["planned", "approved", "reserved", "fallback"],
      requestCount: 0,
      fallbackReason: "NPC_COGNITION_FALLBACK_RESERVED_CRASH_RECOVERED",
    });
    const finalized = await finalize(host, active, receipt);
    return deepFreeze({ ...finalized, recovered: true, providerReplayRequests: 0 });
  }
  const receipt = buildReceipt(active, {
    history: ["planned", "fallback"],
    requestCount: 0,
    fallbackReason: "NPC_COGNITION_FALLBACK_APPROVAL_EXPIRED",
  });
  const finalized = await finalize(host, active, receipt);
  return deepFreeze({ ...finalized, recovered: true, providerReplayRequests: 0 });
}

function loopbackStateFor(controller) {
  const state = loopbackStates.get(controller);
  if (!state) operational();
  return state;
}

function jsonResponse(statusCode, value) {
  return deepFreeze({
    statusCode,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
    body: canonicalizeJsonValue(value),
  });
}

function errorResponse(statusCode, code) {
  return jsonResponse(statusCode, { code });
}

function diagnosticFromResult(result, fallback = "R22_INTERNAL_ERROR") {
  const code = result?.diagnostics?.[0]?.code;
  return typeof code === "string" && /^R22_[A-Z0-9_]{1,92}$/u.test(code) ? code : fallback;
}

function diagnosticFromReceipt(turnReceiptJson) {
  try {
    const receipt = JSON.parse(turnReceiptJson);
    const prefix = "NPC_COGNITION_FALLBACK_";
    if (typeof receipt.fallbackReason !== "string" || !receipt.fallbackReason.startsWith(prefix)) return "R22_INTERNAL_ERROR";
    if (receipt.fallbackReason === "NPC_COGNITION_FALLBACK_R20_SELECTION_STALE") return "R22_CONTEXT_STALE";
    const suffix = receipt.fallbackReason.slice(prefix.length);
    return /^[A-Z0-9_]{1,92}$/u.test(suffix) && suffix !== "NONE" ? `R22_${suffix}` : "R22_INTERNAL_ERROR";
  } catch {
    return "R22_INTERNAL_ERROR";
  }
}

function validPlainText(value, maximumBytes) {
  if (typeof value !== "string" || value.length < 1 || value !== value.normalize("NFC") ||
      value.trim().length < 1 || Buffer.byteLength(value, "utf8") > maximumBytes) return false;
  for (const character of value) {
    const code = character.codePointAt(0);
    if (code === 0 || (code < 32 && code !== 10) || (code >= 127 && code <= 159) ||
        [0x2028, 0x2029, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x2066, 0x2067, 0x2068, 0x2069].includes(code) ||
        (code >= 0xd800 && code <= 0xdfff)) return false;
  }
  return true;
}

function parseRequestBody(request, keys) {
  if (request.headers?.["content-type"] !== "application/json" || typeof request.body !== "string" ||
      Buffer.byteLength(request.body, "utf8") > R22_COGNITION_MAX_BODY_BYTES) return null;
  try {
    const value = JSON.parse(request.body);
    return exactObject(value, keys) ? value : null;
  } catch {
    return null;
  }
}

function validSessionToken(value) {
  return typeof value === "string" && /^[\x21-\x7e]{32,256}$/u.test(value);
}

function sameBearerToken(value, expected) {
  return typeof value === "string" && value.startsWith("Bearer ") && constantTimeEqual(value.slice(7), expected);
}

function rawHeaderCount(request, name) {
  if (!Array.isArray(request.rawHeaders)) return 0;
  let count = 0;
  for (let index = 0; index < request.rawHeaders.length; index += 2) {
    if (String(request.rawHeaders[index]).toLowerCase() === name) count += 1;
  }
  return count;
}

function normalizeLoopbackAddress(value) {
  return value === "::ffff:127.0.0.1" ? R22_COGNITION_HOST : value;
}

function publicFallback(diagnostic) {
  return deepFreeze({ status: "fallback", diagnostic: /^R22_[A-Z0-9_]{1,92}$/u.test(diagnostic ?? "") ? diagnostic : "R22_INTERNAL_ERROR" });
}

function publicDialogue(result, displayAckHash) {
  const dialogueText = result?.dialogueText;
  if (!SHA256.test(displayAckHash ?? "") || !validPlainText(dialogueText, NPC_COGNITION_LIMITS.dialogueBytes) ||
      dialogueText.split("\n").length > NPC_COGNITION_LIMITS.dialogueLines) {
    return publicFallback("R22_UNTRUSTED_OUTPUT_REJECTED");
  }
  if (result.status === "dialogue_only") return deepFreeze({ status: "dialogue_only", dialogueText, actionChoiceId: null, displayAckHash });
  if (result.status === "queued_for_r20" && CHOICE.test(result.actionChoiceId ?? "")) {
    return deepFreeze({ status: "queued_for_r20", dialogueText, actionChoiceId: result.actionChoiceId, displayAckHash });
  }
  return publicFallback("R22_INTERNAL_ERROR");
}

async function finalizeUnconfirmedDisplay(host, turnId) {
  const state = stateFor(host);
  const active = state.active;
  const validated = active?.validated;
  if (!active || active.turnId !== turnId || !validated) {
    return failure("R22_CONTEXT_STALE");
  }
  const receipt = buildReceipt(active, {
    history: ["planned", "approved", "reserved", "dispatching", "validated", "fallback"],
    requestCount: 1,
    returnedModel: validated.providerResult.returnedModel,
    usage: validated.providerResult.usage,
    actualMicrousd: validated.providerResult.actualCostMicrousd,
    fallbackReason: "NPC_COGNITION_FALLBACK_DISPLAY_UNCONFIRMED",
    proposalSha256: validated.proposalSha256,
    actionChoiceId: validated.actionChoiceId,
  });
  return finalize(host, active, receipt);
}

function transientDialogueSnapshot(state, actorEntityId) {
  return deepFreeze(structuredClone(state.transientDialogueByActor.get(actorEntityId) ?? []));
}

function appendTransientDialogue(state, record, outcome) {
  if (!record || !validPlainText(record.playerText, NPC_COGNITION_LIMITS.playerTextBytes) ||
      !["dialogue_only", "queued_for_r20"].includes(outcome?.status) ||
      !validPlainText(outcome.dialogueText, NPC_COGNITION_LIMITS.dialogueBytes) ||
      !Number.isSafeInteger(record.sequence) || record.sequence < 1) return false;
  const exchanges = state.transientDialogueByActor.get(record.actorEntityId) ?? [];
  const previous = exchanges.at(-1);
  if (previous && previous.sequence >= record.sequence) return false;
  exchanges.push({
    sequence: record.sequence,
    playerText: record.playerText,
    dialogueText: outcome.dialogueText,
  });
  while (exchanges.length > NPC_COGNITION_LIMITS.transientDialogueExchanges) exchanges.shift();
  while (exchanges.length &&
      Buffer.byteLength(canonicalizeJsonValue(exchanges), "utf8") > NPC_COGNITION_LIMITS.transientDialogueBytes) {
    exchanges.shift();
  }
  state.transientDialogueByActor.set(record.actorEntityId, exchanges);
  record.playerText = null;
  return true;
}

function discardTransientTurnInput(record) {
  if (record) record.playerText = null;
}

function cancelDisplayDeadline(state) {
  if (typeof state.cancelDisplayDeadline === "function") state.cancelDisplayDeadline();
  state.cancelDisplayDeadline = null;
}

function cancelApprovalDeadline(state) {
  if (typeof state.cancelApprovalDeadline === "function") state.cancelApprovalDeadline();
  state.cancelApprovalDeadline = null;
}

async function expirePendingApproval(state, expectedRecord = state.turn) {
  const record = state.turn;
  if (record !== expectedRecord || record?.phase !== "approval_required" || !Number.isSafeInteger(record.approvalExpiresAtMs)) return;
  if (record.decisionClaim === "declining") {
    await record.decisionPromise?.catch(() => false);
    return;
  }
  if (record.decisionClaim !== null) return;
  const now = stateFor(state.host).clock();
  if (!Number.isSafeInteger(now) || now < 0) operational();
  if (now < record.approvalExpiresAtMs) return;
  record.decisionClaim = "expiring";
  cancelApprovalDeadline(state);
  const expiration = (async () => {
    const hostState = stateFor(state.host);
    const active = hostState.active;
    if (!active || active.turnId !== record.turnId || active.approval !== null) operational();
    const receipt = buildReceipt(active, {
      history: ["planned", "fallback"],
      requestCount: 0,
      fallbackReason: "NPC_COGNITION_FALLBACK_APPROVAL_EXPIRED",
    });
    const completed = await finalize(state.host, active, receipt);
    record.phase = "fallback";
    record.publicOutcome = publicFallback("R22_APPROVAL_EXPIRED");
    record.disclosure = null;
    discardTransientTurnInput(record);
    releaseSelectorGate(state.selectorGate);
    return completed;
  })();
  record.decisionPromise = expiration;
  let succeeded = false;
  try {
    await expiration;
    succeeded = true;
  } finally {
    if (record.decisionPromise === expiration) record.decisionPromise = null;
    if (!succeeded && record.phase === "approval_required") {
      record.decisionClaim = null;
      if (!state.closed && !state.closing && state.turn === record) scheduleApprovalExpiration(state, record);
    }
  }
}

function scheduleApprovalExpiration(state, record) {
  const now = stateFor(state.host).clock();
  if (!Number.isSafeInteger(now) || now < 0 || !Number.isSafeInteger(record.approvalExpiresAtMs)) operational();
  cancelApprovalDeadline(state);
  const cancel = state.approvalScheduler(async () => {
    if (state.closed || state.closing || state.turn !== record) return;
    try {
      await expirePendingApproval(state, record);
    } catch {}
  }, Math.max(1, record.approvalExpiresAtMs - now));
  if (typeof cancel !== "function") operational();
  state.cancelApprovalDeadline = cancel;
}

function armApprovalExpiration(state, record, expiresAtMs) {
  const now = stateFor(state.host).clock();
  if (!Number.isSafeInteger(now) || now < 0 || !Number.isSafeInteger(expiresAtMs) || expiresAtMs <= now) operational();
  record.approvalExpiresAtMs = expiresAtMs;
  scheduleApprovalExpiration(state, record);
}

function scheduleDisplayExpiration(state, record) {
  const now = stateFor(state.host).clock();
  if (!Number.isSafeInteger(now) || now < 0 || !Number.isSafeInteger(record.displayExpiresAtMs)) operational();
  cancelDisplayDeadline(state);
  const cancel = state.displayScheduler(async () => {
    if (state.closed || state.closing || state.turn !== record || record.displayed) return;
    try {
      await expirePendingDisplay(state, record);
    } catch {}
  }, Math.max(1, record.displayExpiresAtMs - now));
  if (typeof cancel !== "function") operational();
  state.cancelDisplayDeadline = cancel;
}

function armDisplayConfirmation(state, record, result, approvalTokenSha256) {
  const hostState = stateFor(state.host);
  const now = hostState.clock();
  if (!Number.isSafeInteger(now) || now < 0 || now > Number.MAX_SAFE_INTEGER - R22_DIALOGUE_DISPLAY_LIFETIME_MS) operational();
  const dialogueSha256 = validPlainText(result?.dialogueText, NPC_COGNITION_LIMITS.dialogueBytes)
    ? sha256Text(result.dialogueText)
    : null;
  if (!SHA256.test(dialogueSha256 ?? "")) operational();
  const proposalSha256 = result.proposalSha256;
  if (!SHA256.test(approvalTokenSha256 ?? "") || !SHA256.test(proposalSha256 ?? "")) operational();
  let receipt = null;
  if (typeof result.turnReceiptJson === "string") {
    try { receipt = JSON.parse(result.turnReceiptJson); } catch { operational(); }
  }
  const turnSha256 = hostState.active?.plan?.turnSha256 ?? receipt?.turnSha256;
  const callPlanSha256 = hostState.active?.callPlanSha256 ?? receipt?.callPlanSha256;
  record.displayAckHash = computeR22DisplayAckHash({
    approvalTokenSha256,
    turnSha256,
    callPlanSha256,
    proposalSha256,
    actionChoiceId: result.actionChoiceId ?? null,
  });
  record.proposalSha256 = proposalSha256;
  record.displayExpiresAtMs = now + R22_DIALOGUE_DISPLAY_LIFETIME_MS;
  record.displayed = false;
  record.displayClaim = null;
  record.acknowledgementPromise = null;
  scheduleDisplayExpiration(state, record);
}

function captureLoopbackOperations(input) {
  const keys = ["turnFactory", "authorityRequestHandler", "authorityStateReader"];
  if (!exactObject(input, keys) || keys.some((key) => typeof input[key] !== "function")) operational();
  return Object.freeze(Object.fromEntries(keys.map((key) => [key, input[key]])));
}

export function createR22LoopbackController({ host, selectorGate, sessionToken, turnFactory, authorityRequestHandler, authorityStateReader, displayScheduler = defaultDisplayScheduler, approvalScheduler = defaultDisplayScheduler, planningScheduler = defaultDisplayScheduler }) {
  stateFor(host);
  selectorStateFor(selectorGate);
  if (!validSessionToken(sessionToken) || typeof displayScheduler !== "function" || typeof approvalScheduler !== "function" || typeof planningScheduler !== "function") operational();
  const operations = captureLoopbackOperations({ turnFactory, authorityRequestHandler, authorityStateReader });
  const handle = Object.freeze(Object.create(null));
  loopbackStates.set(handle, {
    host,
    selectorGate,
    sessionToken,
    operations,
    turn: null,
    creationPromise: null,
    cancelPlanning: null,
    executionPromise: null,
    authorityInFlight: false,
    transientDialogueByActor: new Map(),
    latestSequence: inspectR22CallStore(stateFor(host).store).checkpoint.latestSequence,
    displayScheduler,
    approvalScheduler,
    planningScheduler,
    cancelDisplayDeadline: null,
    cancelApprovalDeadline: null,
    started: false,
    closing: false,
    closePromise: null,
    closed: false,
  });
  return handle;
}

export function restoreR22LoopbackController(controller, input) {
  const state = loopbackStateFor(controller);
  if (state.closed || state.turn !== null || state.creationPromise !== null || state.executionPromise !== null || state.authorityInFlight ||
      !exactObject(input, ["turnId", "actorEntityId", "mappedIntentId", "mappedIntentSha256", "displayAckSha256"]) ||
      !IDENTIFIER.test(input.turnId ?? "") || !IDENTIFIER.test(input.actorEntityId ?? "") || !IDENTIFIER.test(input.mappedIntentId ?? "") ||
      !SHA256.test(input.mappedIntentSha256 ?? "") || !SHA256.test(input.displayAckSha256 ?? "")) {
    return failure("R22_CONTEXT_STALE");
  }
  const active = stateFor(state.host).active;
  if (!active?.validated || active.turnId !== input.turnId || active.actorEntityId !== input.actorEntityId ||
      active.validated.mappedIntentId !== input.mappedIntentId || active.validated.mappedIntentSha256 !== input.mappedIntentSha256 ||
      active.displayAckSha256 !== input.displayAckSha256) return failure("R22_CONTEXT_STALE");
  const gateResult = restoreR22CognitionSelectorGate(state.selectorGate, {
    actorEntityId: input.actorEntityId,
    mappedIntentId: input.mappedIntentId,
    mappedIntentSha256: input.mappedIntentSha256,
  });
  if (gateResult.ok !== true) return gateResult;
  state.turn = {
    requestSha256: null,
    turnId: input.turnId,
    sequence: active.sequence,
    actorEntityId: input.actorEntityId,
    playerText: null,
    approvalHash: null,
    disclosure: null,
    beforeLedgerPoint: structuredClone(active.beforeLedgerPoint),
    phase: "queued",
    publicOutcome: null,
    mappedIntentId: input.mappedIntentId,
    mappedIntentSha256: input.mappedIntentSha256,
    displayExpiresAtMs: null,
    displayAckHash: input.displayAckSha256,
    proposalSha256: active.validated.proposalSha256,
    displayed: true,
    displayClaim: "acknowledged",
    acknowledgementPromise: null,
    decisionClaim: "approving",
    decisionPromise: null,
    expirationPromise: null,
  };
  state.latestSequence = Math.max(state.latestSequence, active.sequence);
  return deepFreeze({ ok: true, status: "queued_for_r20", providerReplayRequests: 0 });
}

function turnResponse(record) {
  return deepFreeze({ status: "approval_required", turnId: record.turnId, approvalHash: record.approvalHash, disclosure: record.disclosure });
}

async function createLoopbackTurn(state, body) {
  const requestSha256 = sha256Text(canonicalizeJsonValue(body));
  if (state.creationPromise !== null) {
    await state.creationPromise;
    if (state.turn?.phase === "approval_required" && state.turn.requestSha256 === requestSha256) return turnResponse(state.turn);
    return null;
  }
  if (state.turn?.phase === "approval_required" && state.turn.requestSha256 === requestSha256) return turnResponse(state.turn);
  if (state.turn && ["approval_required", "dispatching", "awaiting_display", "queued", "issued"].includes(state.turn.phase)) return null;
  if (state.authorityInFlight || !acquireSelectorGate(state.selectorGate)) return null;
  const sequence = state.latestSequence + 1;
  if (!Number.isSafeInteger(sequence) || sequence < 1 || sequence > NPC_COGNITION_LIMITS.turnsPerTimeline) {
    releaseSelectorGate(state.selectorGate);
    return null;
  }
  const abortController = new AbortController();
  let resolveDeadline;
  const deadline = new Promise((resolve) => { resolveDeadline = resolve; });
  const cancelDeadline = state.planningScheduler(() => resolveDeadline({ kind: "timeout" }), R22_TURN_PREPARATION_LIFETIME_MS);
  if (typeof cancelDeadline !== "function") {
    releaseSelectorGate(state.selectorGate);
    operational();
  }
  state.cancelPlanning = () => {
    abortController.abort();
    resolveDeadline({ kind: "cancelled" });
  };
  const factory = Promise.resolve().then(() => state.operations.turnFactory(Object.freeze({
    actorEntityId: body.actorEntityId,
    sequence,
    playerText: body.playerText,
    transientDialogue: transientDialogueSnapshot(state, body.actorEntityId),
    abortSignal: abortController.signal,
  }))).then((value) => ({ kind: "factory", value }), () => ({ kind: "factory", value: null }));
  const promise = (async () => {
    const outcome = await Promise.race([factory, deadline]);
    cancelDeadline();
    state.cancelPlanning = null;
    if (outcome.kind !== "factory") {
      abortController.abort();
      releaseSelectorGate(state.selectorGate);
      return { preparationTimedOut: true };
    }
    const factoryResult = outcome.value;
    const keys = ["turnId", "sequence", "actorEntityId", "callPlanJson", "providerRequestJson", "beforeLedgerPoint"];
    const previousExchange = state.transientDialogueByActor.get(body.actorEntityId)?.at(-1);
    if (!exactObject(factoryResult, keys) || factoryResult.actorEntityId !== body.actorEntityId || factoryResult.sequence !== sequence ||
        (previousExchange && factoryResult.sequence <= previousExchange.sequence)) {
      releaseSelectorGate(state.selectorGate);
      return null;
    }
    const registered = await registerR22CognitionTurn(state.host, factoryResult).catch(() => null);
    if (registered?.ok !== true) {
      releaseSelectorGate(state.selectorGate);
      return null;
    }
    const plan = canonicalCallPlan(factoryResult.callPlanJson);
    if (!plan || registered.turnId !== factoryResult.turnId || registered.providerRequestJson !== factoryResult.providerRequestJson) {
      releaseSelectorGate(state.selectorGate);
      return null;
    }
    const disclosure = deepFreeze({
      providerRequestJson: registered.providerRequestJson,
      model: plan.model,
      endpoint: plan.endpoint,
      maxCostMicrousd: plan.maxCostMicrousd,
      retention: structuredClone(plan.retention),
      priceLock: structuredClone(plan.priceLock),
      maxOutputTokens: plan.maxOutputTokens,
      requestLimit: plan.requestLimit,
      retryLimit: plan.retryLimit,
    });
    const record = {
      requestSha256,
      turnId: registered.turnId,
      sequence: factoryResult.sequence,
      actorEntityId: body.actorEntityId,
      playerText: body.playerText,
      approvalHash: registered.disclosureSha256,
      disclosure,
      beforeLedgerPoint: structuredClone(factoryResult.beforeLedgerPoint),
      phase: "approval_required",
      publicOutcome: null,
      mappedIntentId: null,
      mappedIntentSha256: null,
      displayExpiresAtMs: null,
      displayAckHash: null,
      proposalSha256: null,
      displayed: false,
      displayClaim: null,
      acknowledgementPromise: null,
      decisionClaim: null,
      decisionPromise: null,
      expirationPromise: null,
      approvalExpiresAtMs: registered.leaseExpiresAtMs,
    };
    state.turn = record;
    state.latestSequence = sequence;
    armApprovalExpiration(state, record, registered.leaseExpiresAtMs);
    return turnResponse(record);
  })();
  state.creationPromise = promise;
  try {
    return await promise;
  } finally {
    cancelDeadline();
    state.cancelPlanning = null;
    state.creationPromise = null;
  }
}

async function settleLoopbackExecution(state, record, internalApprovalHash) {
  let result;
  try {
    result = await executeApprovedR22CognitionTurn(state.host, { turnId: record.turnId, approvalHash: internalApprovalHash });
  } catch {
    result = null;
  }
  if (state.turn !== record) return;
  if (result?.ok !== true) {
    record.phase = "fallback";
    record.publicOutcome = publicFallback(diagnosticFromResult(result));
    record.disclosure = null;
    discardTransientTurnInput(record);
    releaseSelectorGate(state.selectorGate);
    return;
  }
  if (result.status === "queued_for_r20") {
    record.phase = "awaiting_display";
    record.mappedIntentId = result.mappedIntentId;
    record.mappedIntentSha256 = result.mappedIntentSha256;
    record.disclosure = null;
    armDisplayConfirmation(state, record, result, internalApprovalHash);
    record.publicOutcome = publicDialogue(result, record.displayAckHash);
    return;
  }
  if (result.status === "dialogue_only") {
    record.phase = "awaiting_display";
    armDisplayConfirmation(state, record, result, internalApprovalHash);
    record.publicOutcome = publicDialogue(result, record.displayAckHash);
    record.disclosure = null;
    return;
  } else {
    record.phase = "fallback";
    record.publicOutcome = publicFallback(diagnosticFromReceipt(result.turnReceiptJson));
    discardTransientTurnInput(record);
  }
  record.disclosure = null;
  releaseSelectorGate(state.selectorGate);
}

async function declineLoopbackRecord(state, record) {
  if (record.decisionClaim === "approving") return false;
  if (record.decisionClaim === "declining") return record.decisionPromise;
  if (record.decisionClaim !== null || record.phase !== "approval_required") return record.phase === "fallback";
  record.decisionClaim = "declining";
  cancelApprovalDeadline(state);
  const decision = (async () => {
    const declined = await declineR22CognitionTurn(state.host, {
      turnId: record.turnId,
      disclosureSha256: record.approvalHash,
    }).catch(() => null);
    if (declined?.ok !== true) return false;
    record.phase = "fallback";
    record.publicOutcome = publicFallback("R22_APPROVAL_DECLINED");
    record.disclosure = null;
    discardTransientTurnInput(record);
    releaseSelectorGate(state.selectorGate);
    return true;
  })();
  record.decisionPromise = decision;
  let succeeded = false;
  try {
    succeeded = await decision;
    return succeeded;
  } finally {
    record.decisionPromise = null;
    if (!succeeded && record.phase === "approval_required") {
      record.decisionClaim = null;
      if (!state.closed && !state.closing && state.turn === record) scheduleApprovalExpiration(state, record);
    }
  }
}

async function expirePendingDisplay(state, expectedRecord = state.turn) {
  const record = state.turn;
  if (record !== expectedRecord) return;
  if (record?.phase !== "awaiting_display" || record.displayed || !Number.isSafeInteger(record.displayExpiresAtMs)) return;
  if (record.displayClaim === "acknowledging") {
    await record.acknowledgementPromise?.catch(() => false);
    if (record.displayed || record.phase !== "awaiting_display") return;
    return expirePendingDisplay(state, expectedRecord);
  }
  if (record.displayClaim === "acknowledged") return;
  if (record.expirationPromise) return record.expirationPromise;
  const now = stateFor(state.host).clock();
  if (!Number.isSafeInteger(now) || now < 0) operational();
  if (now < record.displayExpiresAtMs) return;
  if (record.displayClaim !== null) return;
  record.displayClaim = "expiring";
  const expiration = (async () => {
    cancelDisplayDeadline(state);
    const completed = await finalizeUnconfirmedDisplay(state.host, record.turnId).catch(() => null);
    if (completed?.ok !== true) operational();
    record.phase = "fallback";
    record.publicOutcome = publicFallback("R22_DISPLAY_UNCONFIRMED");
    record.disclosure = null;
    discardTransientTurnInput(record);
    releaseSelectorGate(state.selectorGate);
    record.displayExpiresAtMs = null;
  })();
  record.expirationPromise = expiration;
  let succeeded = false;
  try {
    await expiration;
    succeeded = true;
  } finally {
    if (record.expirationPromise === expiration) record.expirationPromise = null;
    if (!succeeded && record.phase === "awaiting_display") {
      record.displayClaim = null;
      if (!state.closed && !state.closing && state.turn === record) scheduleDisplayExpiration(state, record);
    }
  }
}

async function acknowledgeDisplayedDialogue(state, record) {
  if (record.displayed) return true;
  if (record.phase !== "awaiting_display") return false;
  if (!SHA256.test(record.proposalSha256 ?? "") || !SHA256.test(record.displayAckHash ?? "")) return false;
  if (state.closing || record.displayClaim === "expiring" || record.displayClaim === "closing") return false;
  if (record.displayClaim === "acknowledging") return record.acknowledgementPromise;
  if (record.displayClaim !== null) return record.displayClaim === "acknowledged";
  record.displayClaim = "acknowledging";
  cancelDisplayDeadline(state);
  const acknowledgement = (async () => {
    const acknowledged = await acknowledgeR22CognitionDisplay(state.host, {
      turnId: record.turnId,
      displayAckHash: record.displayAckHash,
    }).catch(() => null);
    if (acknowledged?.ok !== true) return false;
    if (!appendTransientDialogue(state, record, record.publicOutcome)) operational();
    record.displayed = true;
    record.displayClaim = "acknowledged";
    record.displayExpiresAtMs = null;
    if (record.mappedIntentId === null) {
      if (acknowledged.status !== "dialogue_only" || typeof acknowledged.turnReceiptJson !== "string") operational();
      record.phase = "dialogue_only";
      releaseSelectorGate(state.selectorGate);
      return true;
    }
    if (!queueSelectorGate(state.selectorGate, record.actorEntityId, record.mappedIntentId, record.mappedIntentSha256)) {
      return finalizeQueuedFallback(state, record, "R22_R20_SELECTION_STALE");
    }
    record.phase = "queued";
    return true;
  })();
  let settled;
  let succeeded = false;
  settled = acknowledgement.then((value) => {
    succeeded = value === true;
    return value;
  }).finally(() => {
    if (record.acknowledgementPromise === settled) record.acknowledgementPromise = null;
    if (record.displayClaim === "acknowledging") record.displayClaim = null;
    if (!succeeded && record.phase === "awaiting_display" && !state.closed && !state.closing && state.turn === record) {
      scheduleDisplayExpiration(state, record);
    }
  });
  record.acknowledgementPromise = settled;
  return settled;
}

async function finalizeQueuedFallback(state, record, diagnosticCode) {
  const completed = await completeQueuedR22CognitionTurn(state.host, {
    turnId: record.turnId,
    status: "fallback",
    afterLedgerPoint: structuredClone(record.beforeLedgerPoint),
    adjudicationResultJson: null,
    diagnosticCode,
  }).catch(() => null);
  if (completed?.ok !== true) return false;
  record.phase = "fallback";
  record.publicOutcome = publicFallback(diagnosticFromReceipt(completed.turnReceiptJson));
  record.disclosure = null;
  discardTransientTurnInput(record);
  releaseSelectorGate(state.selectorGate);
  return true;
}

function deriveAdjudicationFromLedger(text, identity) {
  if (typeof text !== "string") return null;
  try {
    const ledger = JSON.parse(text);
    const report = validateWorldEventLedgerJson(text);
    if (canonicalizeJsonValue(ledger) !== text || report?.valid !== true || report.diagnostics?.length !== 0 ||
        ledger.timeline?.id !== identity.timelineId || ledger.revision !== identity.beforeLedgerPoint.revision + 1 ||
        ledger.entries.length !== ledger.revision) return null;
    const priorHeadSha256 = identity.beforeLedgerPoint.revision === 0
      ? null
      : ledger.entries[identity.beforeLedgerPoint.revision - 1]?.entrySha256 ?? null;
    if (priorHeadSha256 !== identity.beforeLedgerPoint.headSha256) return null;
    const entry = ledger.entries.at(-1);
    if (!entry || entry.revision !== ledger.revision || entry.previousEntrySha256 !== identity.beforeLedgerPoint.headSha256 ||
        entry.intent?.id !== identity.validated.mappedIntentId || entry.intent.timelineId !== identity.timelineId ||
        sha256Text(canonicalizeJsonValue(entry.intent)) !== identity.validated.mappedIntentSha256 ||
        entry.entrySha256 !== ledger.headSha256 || entry.beforeSnapshotSha256 !== identity.beforeLedgerPoint.runtimeSnapshotSha256) return null;
    const adjudicationResultJson = canonicalizeJsonValue({
      format: "matrix-oasis.npc-adjudication-result",
      formatVersion: "0.1.0",
      canonicalization: CANONICALIZATION,
      timelineId: ledger.timeline.id,
      intentId: entry.intent.id,
      replayed: false,
      revision: entry.revision,
      headSha256: entry.entrySha256,
      decision: entry.decision,
      beforeSnapshotSha256: entry.beforeSnapshotSha256,
      afterSnapshotSha256: entry.afterSnapshotSha256,
      transition: entry.transition,
    });
    const adjudicationReport = validateNpcAdjudicationResultJson(adjudicationResultJson);
    if (adjudicationReport?.valid !== true || adjudicationReport.diagnostics?.length !== 0) return null;
    return {
      afterLedgerPoint: {
        revision: ledger.revision,
        headSha256: ledger.headSha256,
        runtimeSnapshotSha256: entry.afterSnapshotSha256,
      },
      adjudicationResultJson,
      adjudicationResultSha256: sha256Text(adjudicationResultJson),
    };
  } catch {
    return null;
  }
}

async function completeQueuedFromAuthority(state, record) {
  let snapshot;
  try {
    snapshot = await state.operations.authorityStateReader();
  } catch {
    return false;
  }
  const ledgerJson = snapshot?.authority?.canonicalWorldEventLedgerJson ?? snapshot?.canonicalWorldEventLedgerJson;
  const active = stateFor(state.host).active;
  if (!active || active.turnId !== record.turnId) return false;
  const evidence = deriveAdjudicationFromLedger(ledgerJson, active);
  if (!evidence) return false;
  const completed = await completeQueuedR22CognitionTurn(state.host, {
    turnId: record.turnId,
    status: "adjudicated",
    afterLedgerPoint: evidence.afterLedgerPoint,
    adjudicationResultJson: evidence.adjudicationResultJson,
    diagnosticCode: null,
  }).catch(() => null);
  if (completed?.ok !== true) return false;
  record.phase = "adjudicated";
  record.disclosure = null;
  releaseSelectorGate(state.selectorGate);
  return true;
}

function parseDelegatedResponse(value) {
  if (!value || !Number.isSafeInteger(value.statusCode) || value.statusCode < 100 || value.statusCode > 599 ||
      typeof value.body !== "string" || Buffer.byteLength(value.body, "utf8") > R22_COGNITION_MAX_BODY_BYTES) return null;
  try {
    const body = JSON.parse(value.body);
    return body !== null && typeof body === "object" && !Array.isArray(body) ? { statusCode: value.statusCode, body } : null;
  } catch {
    return null;
  }
}

async function delegateAuthority(state, request) {
  let result;
  try {
    result = await state.operations.authorityRequestHandler(deepFreeze({
      remoteAddress: R22_COGNITION_HOST,
      method: request.method,
      url: request.url,
      headers: { authorization: request.headers.authorization, ...(request.method === "POST" ? { "content-type": "application/json" } : {}) },
      body: request.body,
    }));
  } catch {
    return errorResponse(500, "R22_R20_UNAVAILABLE");
  }
  const parsed = parseDelegatedResponse(result);
  return parsed ? jsonResponse(parsed.statusCode, parsed.body) : errorResponse(500, "R22_R20_UNAVAILABLE");
}

async function handleAuthorityRoute(state, request) {
  const routeKey = `${request.method}\0${request.url}`;
  if (!R20_ROUTES.has(routeKey)) return errorResponse(404, "R22_ROUTE_NOT_FOUND");
  if (state.creationPromise !== null) {
    if (request.method === "GET" && request.url === "/v1/command") return jsonResponse(200, { status: "quiescent" });
    if (request.method === "POST" && request.url === "/v1/reset") {
      state.cancelPlanning?.();
      await state.creationPromise;
    } else {
      return errorResponse(409, "R22_CALL_IN_FLIGHT");
    }
  }
  const gate = selectorStateFor(state.selectorGate);
  const record = state.turn;
  if (request.method === "POST" && request.url === "/v1/reset" && record?.phase === "approval_required") {
    if (!await declineLoopbackRecord(state, record)) return errorResponse(409, "R22_CALL_IN_FLIGHT");
  }
  if (request.method === "GET" && request.url === "/v1/command") {
    if (record?.phase === "approval_required") {
      if (!await declineLoopbackRecord(state, record)) return errorResponse(409, "R22_CALL_IN_FLIGHT");
    } else if (record?.phase === "dispatching" || record?.phase === "awaiting_display" || gate.mode === "held") {
      return jsonResponse(200, { status: "quiescent" });
    }
    let delegated = await delegateAuthority(state, request);
    if (gate.mismatch && record?.phase === "queued") {
      if (!await finalizeQueuedFallback(state, record, "R22_R20_SELECTION_STALE")) return errorResponse(500, "R22_INTERNAL_ERROR");
      return jsonResponse(200, { status: "quiescent" });
    }
    const parsed = JSON.parse(delegated.body);
    if (delegated.statusCode === 200 && parsed.status === "command") state.authorityInFlight = true;
    if (record?.phase === "queued" && selectorStateFor(state.selectorGate).mode === "issued") record.phase = "issued";
    return delegated;
  }
  if (["approval_required", "dispatching", "awaiting_display", "queued"].includes(record?.phase) && ["/v1/reset", "/v1/verify"].includes(request.url)) {
    return errorResponse(409, "R22_CALL_IN_FLIGHT");
  }
  const delegated = await delegateAuthority(state, request);
  const body = JSON.parse(delegated.body);
  if (request.url === "/v1/mirror" && delegated.statusCode === 200 && body.status === "committed") {
    state.authorityInFlight = false;
    if (record?.phase === "issued" && !await completeQueuedFromAuthority(state, record)) return errorResponse(500, "R22_R19_FAILURE");
  } else if (request.url === "/v1/reset" && delegated.statusCode === 200 && body.status === "reset") {
    state.authorityInFlight = false;
    state.transientDialogueByActor.clear();
    cancelDisplayDeadline(state);
    state.turn = null;
  } else if (["/v1/arrived", "/v1/mirror"].includes(request.url) && record?.phase === "issued" && delegated.statusCode !== 200) {
    const diagnostic = body.code?.includes("AUTHORITY") || body.code?.includes("RUNTIME") ? "R22_R19_FAILURE" : "R22_R20_UNAVAILABLE";
    if (!await finalizeQueuedFallback(state, record, diagnostic)) return errorResponse(500, "R22_INTERNAL_ERROR");
    state.authorityInFlight = false;
  }
  return delegated;
}

export async function handleR22LoopbackRequestAsync(controller, request) {
  const state = loopbackStateFor(controller);
  if (state.closed || state.closing) return errorResponse(503, "R22_HOST_CLOSED");
  if (!request || normalizeLoopbackAddress(request.remoteAddress) !== R22_COGNITION_HOST) return errorResponse(403, "R22_LOOPBACK_REQUIRED");
  if (rawHeaderCount(request, "authorization") > 1 || rawHeaderCount(request, "content-type") > 1) return errorResponse(400, "R22_REQUEST_HEADERS_INVALID");
  if (!sameBearerToken(request.headers?.authorization, state.sessionToken)) return errorResponse(401, "R22_SESSION_TOKEN_INVALID");
  if (typeof request.url !== "string" || request.url.includes("?")) return errorResponse(404, "R22_ROUTE_NOT_FOUND");
  if (request.method === "GET") {
    if ((request.body ?? "") !== "" || request.headers?.["content-type"] !== undefined) return errorResponse(400, "R22_REQUEST_BODY_INVALID");
  } else if (request.method === "POST") {
    if (request.headers?.["content-type"] !== "application/json") return errorResponse(415, "R22_CONTENT_TYPE_INVALID");
    if (typeof request.body !== "string" || Buffer.byteLength(request.body, "utf8") > R22_COGNITION_MAX_BODY_BYTES) return errorResponse(413, "R22_REQUEST_LIMIT_EXCEEDED");
  } else {
    return errorResponse(405, "R22_METHOD_NOT_ALLOWED");
  }
  await expirePendingDisplay(state);
  await expirePendingApproval(state);

  if (request.method === "POST" && request.url === "/v1/cognition/turn") {
    const body = parseRequestBody(request, ["actorEntityId", "playerText"]);
    if (!body || !IDENTIFIER.test(body.actorEntityId ?? "") || !validPlainText(body.playerText, NPC_COGNITION_LIMITS.playerTextBytes)) {
      return errorResponse(400, "R22_TURN_REQUEST_INVALID");
    }
    const result = await createLoopbackTurn(state, body);
    if (result?.preparationTimedOut) return errorResponse(408, "R22_CONTEXT_STALE");
    return result ? jsonResponse(200, result) : errorResponse(409, "R22_CALL_IN_FLIGHT");
  }

  if (request.method === "POST" && ["/v1/cognition/approve", "/v1/cognition/decline"].includes(request.url)) {
    const body = parseRequestBody(request, ["turnId", "approvalHash"]);
    const record = state.turn;
    if (!body || !IDENTIFIER.test(body.turnId ?? "") || !SHA256.test(body.approvalHash ?? "") || !record ||
        body.turnId !== record.turnId || !constantTimeEqual(body.approvalHash, record.approvalHash)) {
      return errorResponse(400, "R22_APPROVAL_MISMATCH");
    }
    if (request.url.endsWith("/decline")) {
      if (record.phase === "fallback") return jsonResponse(200, record.publicOutcome);
      if (!await declineLoopbackRecord(state, record)) return errorResponse(409, "R22_CALL_IN_FLIGHT");
      return jsonResponse(200, record.publicOutcome);
    }
    if (["awaiting_display", "dialogue_only", "queued", "issued", "fallback", "adjudicated"].includes(record.phase)) {
      return record.publicOutcome ? jsonResponse(200, record.publicOutcome) : errorResponse(409, "R22_CALL_IN_FLIGHT");
    }
    if (record.phase === "dispatching") return jsonResponse(200, { status: "dispatching" });
    if (record.phase !== "approval_required") return errorResponse(409, "R22_CALL_IN_FLIGHT");
    if (record.decisionClaim === "declining") return errorResponse(409, "R22_CALL_IN_FLIGHT");
    if (record.decisionClaim !== null && record.decisionClaim !== "approving") return errorResponse(409, "R22_CALL_IN_FLIGHT");
    record.decisionClaim = "approving";
    cancelApprovalDeadline(state);
    const approval = issueR22CognitionApproval(state.host, { turnId: record.turnId, disclosureSha256: record.approvalHash });
    if (approval.ok !== true) return errorResponse(409, diagnosticFromResult(approval, "R22_APPROVAL_MISMATCH"));
    record.phase = "dispatching";
    record.disclosure = null;
    const execution = settleLoopbackExecution(state, record, approval.approvalHash).finally(() => {
      if (state.executionPromise === execution) state.executionPromise = null;
    });
    state.executionPromise = execution;
    return jsonResponse(200, { status: "dispatching" });
  }

  if (request.method === "POST" && request.url === "/v1/cognition/displayed") {
    const body = parseRequestBody(request, ["turnId", "displayAckHash"]);
    const record = state.turn;
    if (!body || !IDENTIFIER.test(body.turnId ?? "") || !SHA256.test(body.displayAckHash ?? "") || !record ||
        body.turnId !== record.turnId || !constantTimeEqual(body.displayAckHash, record.displayAckHash)) {
      return errorResponse(400, "R22_DISPLAY_ACK_MISMATCH");
    }
    if (record.displayed) return jsonResponse(200, { status: "acknowledged" });
    if (record.phase !== "awaiting_display" || !await acknowledgeDisplayedDialogue(state, record)) {
      return errorResponse(409, record.publicOutcome?.diagnostic ?? "R22_CONTEXT_STALE");
    }
    return jsonResponse(200, { status: "acknowledged" });
  }

  const statusMatch = request.method === "GET" ? /^\/v1\/cognition\/status\/([a-z][a-z0-9-]{0,95})$/u.exec(request.url) : null;
  if (statusMatch) {
    const record = state.turn;
    if (!record || record.turnId !== statusMatch[1]) return errorResponse(404, "R22_TURN_NOT_FOUND");
    if (record.phase === "approval_required") return errorResponse(409, "R22_APPROVAL_REQUIRED");
    if (record.phase === "dispatching") return jsonResponse(200, { status: "dispatching" });
    return record.publicOutcome ? jsonResponse(200, record.publicOutcome) : errorResponse(409, "R22_CALL_IN_FLIGHT");
  }

  if (R20_ROUTES.has(`${request.method}\0${request.url}`)) return handleAuthorityRoute(state, request);
  return errorResponse(404, "R22_ROUTE_NOT_FOUND");
}

async function readHttpBody(request) {
  const chunks = [];
  let length = 0;
  let tooLarge = false;
  for await (const chunk of request) {
    length += chunk.length;
    if (length > R22_COGNITION_MAX_BODY_BYTES) {
      tooLarge = true;
      continue;
    }
    chunks.push(chunk);
  }
  if (tooLarge) return { tooLarge: true, body: "" };
  try {
    return { tooLarge: false, body: new TextDecoder("utf-8", { fatal: true }).decode(Buffer.concat(chunks)) };
  } catch {
    return { tooLarge: false, body: null };
  }
}

export function closeR22LoopbackServer(server, { timeoutMs = R22_COGNITION_CLOSE_TIMEOUT_MS } = {}) {
  if (!server || typeof server.close !== "function" || typeof server.closeAllConnections !== "function" ||
      !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > R22_COGNITION_CLOSE_TIMEOUT_MS) {
    return Promise.reject(new Error("R22_HOST_CLOSE_INVALID"));
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error = null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error); else resolve();
    };
    const timer = setTimeout(() => {
      try {
        server.closeAllConnections();
        finish(new Error("R22_HOST_CLOSE_TIMEOUT"));
      } catch (error) {
        finish(new Error("R22_HOST_CLOSE_FAILED", { cause: error }));
      }
    }, timeoutMs);
    try {
      server.close((error) => finish(error ? new Error("R22_HOST_CLOSE_FAILED", { cause: error }) : null));
    } catch (error) {
      finish(new Error("R22_HOST_CLOSE_FAILED", { cause: error }));
    }
  });
}

export async function closeR22LoopbackController(controller) {
  const state = loopbackStateFor(controller);
  if (state.closed) return deepFreeze({ ok: true });
  if (state.closePromise !== null) return state.closePromise;
  state.closing = true;
  const closing = (async () => {
    state.cancelPlanning?.();
    if (state.creationPromise !== null) await state.creationPromise.catch(() => {});
    if (state.executionPromise !== null) await state.executionPromise.catch(() => {});
    let record = state.turn;
    cancelDisplayDeadline(state);
    cancelApprovalDeadline(state);
    if (record?.decisionPromise) await record.decisionPromise.catch(() => {});
    if (record?.acknowledgementPromise) await record.acknowledgementPromise.catch(() => {});
    if (record?.expirationPromise) await record.expirationPromise.catch(() => {});
    record = state.turn;
    if (record?.phase === "approval_required") {
      await declineLoopbackRecord(state, record).catch(() => false);
    } else if (record?.phase === "awaiting_display") {
      if (record.displayClaim === null) record.displayClaim = "closing";
      if (record.displayClaim === "closing") {
        const completed = await finalizeUnconfirmedDisplay(state.host, record.turnId).catch(() => null);
        if (completed?.ok === true) {
          record.phase = "fallback";
          record.publicOutcome = publicFallback("R22_DISPLAY_UNCONFIRMED");
        }
      }
    } else if (record?.phase === "queued") {
      await finalizeQueuedFallback(state, record, "R22_R20_UNAVAILABLE").catch(() => false);
    }
    discardTransientTurnInput(record);
    state.transientDialogueByActor.clear();
    releaseSelectorGate(state.selectorGate);
    state.closed = true;
    return deepFreeze({ ok: true });
  })();
  state.closePromise = closing;
  return closing;
}

export function startR22LoopbackServer({ controller, port = R22_COGNITION_HOST_PORT }) {
  const state = loopbackStateFor(controller);
  if (port !== R22_COGNITION_HOST_PORT || state.started || state.closed) return Promise.reject(new Error("R22_HOST_START_INVALID"));
  state.started = true;
  return new Promise((resolve, reject) => {
    const server = createServer((request, output) => {
      void readHttpBody(request).then(async (captured) => {
        const result = captured.tooLarge
          ? errorResponse(413, "R22_REQUEST_LIMIT_EXCEEDED")
          : captured.body === null
            ? errorResponse(400, "R22_REQUEST_BODY_INVALID")
            : await handleR22LoopbackRequestAsync(controller, {
              remoteAddress: normalizeLoopbackAddress(request.socket.remoteAddress),
              method: request.method,
              url: request.url,
              headers: request.headers,
              rawHeaders: request.rawHeaders,
              body: captured.body,
            }).catch(() => errorResponse(500, "R22_INTERNAL_ERROR"));
        output.writeHead(result.statusCode, result.headers);
        output.end(result.body);
      }).catch(() => {
        const result = errorResponse(500, "R22_INTERNAL_ERROR");
        output.writeHead(result.statusCode, result.headers);
        output.end(result.body);
      });
    });
    server.requestTimeout = R22_COGNITION_CLOSE_TIMEOUT_MS;
    server.headersTimeout = R22_COGNITION_CLOSE_TIMEOUT_MS;
    server.once("error", (error) => {
      state.started = false;
      reject(error);
    });
    server.listen(R22_COGNITION_HOST_PORT, R22_COGNITION_HOST, () => {
      resolve(deepFreeze({
        host: R22_COGNITION_HOST,
        port: R22_COGNITION_HOST_PORT,
        close: async () => {
          await closeR22LoopbackServer(server);
          await closeR22LoopbackController(controller);
        },
      }));
    });
  });
}
