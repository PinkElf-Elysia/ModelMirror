import { createHash } from "node:crypto";
import Ajv2020 from "ajv/dist/2020.js";
import { parse, parseTree } from "jsonc-parser";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  NPC_COGNITION_CALL_PLAN_SCHEMA,
  NPC_COGNITION_FALLBACK_REASONS,
  NPC_COGNITION_LIMITS,
  NPC_COGNITION_MODEL,
  NPC_COGNITION_POLICY_SCHEMA,
  NPC_COGNITION_QUALIFICATION_MARKERS,
  NPC_COGNITION_QUALIFICATION_REPORT_SCHEMA,
  NPC_COGNITION_TRACE_SCHEMA,
  NPC_COGNITION_TURN_RECEIPT_SCHEMA,
  NPC_COGNITION_TURN_REQUEST_SCHEMA,
  NPC_DIALOGUE_PROPOSAL_SCHEMA,
} from "./schema.mjs";

const INTERNAL_CODE = "NPC_COGNITION_CONTRACT_INTERNAL_ERROR";
const PHASE = Object.freeze({ parse: 0, schema: 1, semantic: 2, integrity: 3, canonical: 4 });
const NONE = "NPC_COGNITION_FALLBACK_NONE";
const BIDI = /[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/u;
const CONTROL = /[\u0000-\u0009\u000b-\u001f\u007f-\u009f]/u;
const LINE_SEPARATOR = /[\u2028\u2029]/u;
const FORBIDDEN_PROPERTY_NAMES = new Set(["__proto__", "constructor", "prototype"]);

export class NpcCognitionContractOperationalError extends Error {
  constructor() {
    super(INTERNAL_CODE);
    this.name = "NpcCognitionContractOperationalError";
    this.code = INTERNAL_CODE;
  }
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}
function token(value) { return String(value).replaceAll("~", "~0").replaceAll("/", "~1"); }
function at(path, value) { return `${path}/${token(value)}`; }
function diagnostic(phase, code, path = "") { return { phase, severity: "error", code, path, message: code }; }
function compareText(left, right) { return left < right ? -1 : left > right ? 1 : 0; }
function report(items) {
  const compare = (left, right) => (PHASE[left.phase] - PHASE[right.phase]) || compareText(left.path, right.path) || compareText(left.code, right.code);
  const seen = new Set();
  const diagnostics = [];
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
    else if (character === "{" || character === "[") { depth += 1; if (depth > NPC_COGNITION_LIMITS.documentDepth) return true; }
    else if (character === "}" || character === "]") depth -= 1;
  }
  return false;
}
function treeKeyDiagnostics(tree, prefix) {
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
        if (FORBIDDEN_PROPERTY_NAMES.has(key.value)) {
          output.push(diagnostic("parse", `${prefix}_JSON_FORBIDDEN_PROPERTY_NAME`, at(current.path, key.value)));
        }
      }
    } else if (current.node.type === "array") {
      for (let index = 0; index < (current.node.children?.length ?? 0); index += 1) stack.push({ node: current.node.children[index], path: at(current.path, index) });
    }
  }
  return output;
}
function parseDocument(text, prefix, byteLimit) {
  if (typeof text !== "string") return { ok: false, diagnostics: [diagnostic("parse", `${prefix}_JSON_INPUT_TYPE`)] };
  if (new TextEncoder().encode(text).byteLength > byteLimit) return { ok: false, diagnostics: [diagnostic("parse", `${prefix}_JSON_SIZE_EXCEEDED`)] };
  if (tooDeep(text)) return { ok: false, diagnostics: [diagnostic("parse", `${prefix}_JSON_DEPTH_EXCEEDED`)] };
  const options = { allowTrailingComma: false, disallowComments: true, allowEmptyContent: false };
  const treeErrors = [];
  const tree = parseTree(text, treeErrors, options);
  if (treeErrors.length || !tree) return { ok: false, diagnostics: [diagnostic("parse", `${prefix}_JSON_SYNTAX`)] };
  const keyDiagnostics = treeKeyDiagnostics(tree, prefix);
  if (keyDiagnostics.length) return { ok: false, diagnostics: keyDiagnostics };
  const errors = [];
  const value = parse(text, errors, options);
  if (errors.length || value === undefined) return { ok: false, diagnostics: [diagnostic("parse", `${prefix}_JSON_SYNTAX`)] };
  return { ok: true, value };
}

const ajv = new Ajv2020({ strict: true, allErrors: true, coerceTypes: false, useDefaults: false, removeAdditional: false, ownProperties: true, validateFormats: false });
const validators = new Map([
  ["POLICY", ajv.compile(NPC_COGNITION_POLICY_SCHEMA)],
  ["TURN_REQUEST", ajv.compile(NPC_COGNITION_TURN_REQUEST_SCHEMA)],
  ["CALL_PLAN", ajv.compile(NPC_COGNITION_CALL_PLAN_SCHEMA)],
  ["DIALOGUE_PROPOSAL", ajv.compile(NPC_DIALOGUE_PROPOSAL_SCHEMA)],
  ["TURN_RECEIPT", ajv.compile(NPC_COGNITION_TURN_RECEIPT_SCHEMA)],
  ["TRACE", ajv.compile(NPC_COGNITION_TRACE_SCHEMA)],
  ["QUALIFICATION", ajv.compile(NPC_COGNITION_QUALIFICATION_REPORT_SCHEMA)],
]);
function schemaDiagnostics(validate, prefix, value) {
  if (validate(value)) return [];
  const codes = {
    required: "REQUIRED", additionalProperties: "UNKNOWN_PROPERTY", type: "TYPE", const: "CONST", enum: "ENUM",
    minItems: "MIN_ITEMS", maxItems: "MAX_ITEMS", uniqueItems: "DUPLICATE_ITEM", minimum: "NUMBER_CONSTRAINT",
    maximum: "NUMBER_CONSTRAINT", minLength: "STRING_CONSTRAINT", maxLength: "STRING_CONSTRAINT", pattern: "STRING_CONSTRAINT", oneOf: "SHAPE",
  };
  return (validate.errors ?? []).map((error) => diagnostic(
    "schema",
    `${prefix}_SCHEMA_${codes[error.keyword] ?? "INVALID"}`,
    error.keyword === "required" ? at(error.instancePath, error.params.missingProperty) : error.instancePath,
  ));
}
function wellFormed(value) {
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (!Number.isInteger(next) || next < 0xdc00 || next > 0xdfff) return false;
      index += 1;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return true;
}
function stringDiagnostics(value, prefix) {
  const output = []; const stack = [{ value, path: "" }];
  const inspect = (text, path) => {
    if (!wellFormed(text)) { output.push(diagnostic("semantic", `${prefix}_TEXT_UNPAIRED_SURROGATE`, path)); return; }
    if (text.normalize("NFC") !== text) output.push(diagnostic("semantic", `${prefix}_TEXT_NOT_NFC`, path));
    if (CONTROL.test(text)) output.push(diagnostic("semantic", `${prefix}_TEXT_FORBIDDEN_CONTROL`, path));
    if (BIDI.test(text)) output.push(diagnostic("semantic", `${prefix}_TEXT_FORBIDDEN_BIDI`, path));
    if (LINE_SEPARATOR.test(text)) output.push(diagnostic("semantic", `${prefix}_TEXT_FORBIDDEN_LINE_SEPARATOR`, path));
  };
  while (stack.length) {
    const current = stack.pop();
    if (typeof current.value === "string") inspect(current.value, current.path);
    else if (typeof current.value === "number" && !Number.isSafeInteger(current.value)) output.push(diagnostic("semantic", `${prefix}_NUMBER_NOT_SAFE_INTEGER`, current.path));
    else if (Array.isArray(current.value)) current.value.forEach((child, index) => stack.push({ value: child, path: at(current.path, index) }));
    else if (current.value && typeof current.value === "object") Object.entries(current.value).forEach(([key, child]) => {
      inspect(key, current.path); stack.push({ value: child, path: at(current.path, key) });
    });
  }
  return output;
}
function ordered(values, keyOf, code, path) {
  const output = [];
  for (let index = 1; index < values.length; index += 1) {
    if (compareText(keyOf(values[index - 1]), keyOf(values[index])) >= 0) output.push(diagnostic("semantic", code, `${path}/${index}`));
  }
  return output;
}
function same(left, right) { return canonicalizeJsonValue(left) === canonicalizeJsonValue(right); }
function hashCanonical(value) { return `sha256:${createHash("sha256").update(canonicalizeJsonValue(value), "utf8").digest("hex")}`; }

export function computeNpcCognitionApprovalHash(callPlan) {
  try {
    if (!callPlan || typeof callPlan !== "object" || Array.isArray(callPlan)) throw new Error("invalid call plan");
    if (!callPlan.approval || typeof callPlan.approval !== "object" || Array.isArray(callPlan.approval)) throw new Error("invalid approval");
    const { hash: ignoredHash, ...approvalBody } = callPlan.approval;
    return hashCanonical({ ...callPlan, approval: approvalBody });
  } catch (error) {
    if (error instanceof NpcCognitionContractOperationalError) throw error;
    throw new NpcCognitionContractOperationalError();
  }
}

function ledgerHeadDiagnostics(value, prefix, path) {
  return ((value.revision === 0) !== (value.headSha256 === null))
    ? [diagnostic("semantic", `${prefix}_LEDGER_HEAD_MISMATCH`, `${path}/headSha256`)] : [];
}

function policySemantics(value) {
  const output = [...ordered(value.actors, (actor) => actor.actorEntityId, "NPC_COGNITION_POLICY_ACTOR_ORDER", "/actors")];
  const actors = new Set();
  value.actors.forEach((actor, actorIndex) => {
    if (actors.has(actor.actorEntityId)) output.push(diagnostic("semantic", "NPC_COGNITION_POLICY_ACTOR_DUPLICATE", `/actors/${actorIndex}/actorEntityId`));
    actors.add(actor.actorEntityId);
    output.push(...ordered(actor.safeActions, (action) => `${action.nodeId}\0${action.actionId}`, "NPC_COGNITION_POLICY_SAFE_ACTION_ORDER", `/actors/${actorIndex}/safeActions`));
    const actions = new Set();
    actor.safeActions.forEach((action, actionIndex) => {
      const key = `${action.nodeId}\0${action.actionId}`;
      if (actions.has(key)) output.push(diagnostic("semantic", "NPC_COGNITION_POLICY_SAFE_ACTION_DUPLICATE", `/actors/${actorIndex}/safeActions/${actionIndex}`));
      actions.add(key);
    });
  });
  return output;
}
function turnRequestSemantics(value) {
  const output = [...ledgerHeadDiagnostics(value.observed, "NPC_COGNITION_TURN_REQUEST", "/observed")];
  if (new TextEncoder().encode(value.playerText).byteLength > NPC_COGNITION_LIMITS.playerTextBytes) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_REQUEST_PLAYER_TEXT_BYTES_EXCEEDED", "/playerText"));
  if (!/\S/u.test(value.playerText)) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_REQUEST_PLAYER_TEXT_EMPTY", "/playerText"));
  return output;
}
function callPlanSemantics(value) {
  const output = [];
  const choices = new Set();
  const intents = new Set();
  value.candidateChoices.forEach((candidate, index) => {
    if (choices.has(candidate.choiceId)) output.push(diagnostic("semantic", "NPC_COGNITION_CALL_PLAN_CHOICE_DUPLICATE", `/candidateChoices/${index}/choiceId`));
    if (intents.has(candidate.intentSha256)) output.push(diagnostic("semantic", "NPC_COGNITION_CALL_PLAN_INTENT_DUPLICATE", `/candidateChoices/${index}/intentSha256`));
    choices.add(candidate.choiceId);
    intents.add(candidate.intentSha256);
    if (candidate.choiceId !== `choice-${candidate.intentSha256.slice(7)}`) {
      output.push(diagnostic("integrity", "NPC_COGNITION_CALL_PLAN_CHOICE_INTENT_MISMATCH", `/candidateChoices/${index}`));
    }
  });
  if (value.candidateSha256 !== hashCanonical(value.candidateChoices)) {
    output.push(diagnostic("integrity", "NPC_COGNITION_CALL_PLAN_CANDIDATE_HASH_MISMATCH", "/candidateSha256"));
  }
  const expected = computeNpcCognitionApprovalHash(value);
  if (value.approval.hash !== expected) output.push(diagnostic("integrity", "NPC_COGNITION_CALL_PLAN_APPROVAL_HASH_MISMATCH", "/approval/hash"));
  return output;
}
function dialogueProposalSemantics(value) {
  const output = [];
  if (new TextEncoder().encode(value.dialogueText).byteLength > NPC_COGNITION_LIMITS.dialogueBytes) output.push(diagnostic("semantic", "NPC_DIALOGUE_PROPOSAL_DIALOGUE_BYTES_EXCEEDED", "/dialogueText"));
  if (value.dialogueText.split("\n").length > NPC_COGNITION_LIMITS.dialogueLines) output.push(diagnostic("semantic", "NPC_DIALOGUE_PROPOSAL_DIALOGUE_LINES_EXCEEDED", "/dialogueText"));
  if (!/\S/u.test(value.dialogueText)) output.push(diagnostic("semantic", "NPC_DIALOGUE_PROPOSAL_DIALOGUE_EMPTY", "/dialogueText"));
  return output;
}

const NEXT_STATUS = Object.freeze({
  planned: new Set(["approved", "fallback"]),
  approved: new Set(["reserved", "fallback"]),
  reserved: new Set(["dispatching", "fallback"]),
  dispatching: new Set(["validated", "fallback"]),
  validated: new Set(["queued_for_r20", "dialogue_only", "fallback"]),
  queued_for_r20: new Set(["adjudicated", "fallback"]),
  dialogue_only: new Set(["finalized"]),
  fallback: new Set(["finalized"]),
  adjudicated: new Set(["finalized"]),
  finalized: new Set(),
});
const FALLBACK_STAGE_BY_REASON = Object.freeze({
  NPC_COGNITION_FALLBACK_APPROVAL_DECLINED: "planned",
  NPC_COGNITION_FALLBACK_APPROVAL_EXPIRED: "planned",
  NPC_COGNITION_FALLBACK_APPROVAL_EXPIRED_PRE_REQUEST: "dispatching",
  NPC_COGNITION_FALLBACK_BUDGET_EXHAUSTED: "approved",
  NPC_COGNITION_FALLBACK_RESERVED_CRASH_RECOVERED: "reserved",
  NPC_COGNITION_FALLBACK_PROVIDER_CREDENTIAL_UNAVAILABLE: "dispatching",
  NPC_COGNITION_FALLBACK_CALL_IN_FLIGHT: "approved",
  NPC_COGNITION_FALLBACK_PROVIDER_TIMEOUT: "dispatching",
  NPC_COGNITION_FALLBACK_PROVIDER_NETWORK_AMBIGUOUS: "dispatching",
  NPC_COGNITION_FALLBACK_DISPATCH_CRASH_UNCERTAIN: "dispatching",
  NPC_COGNITION_FALLBACK_PROVIDER_FAILURE: "dispatching",
  NPC_COGNITION_FALLBACK_PROVIDER_REFUSED: "dispatching",
  NPC_COGNITION_FALLBACK_PROVIDER_RESPONSE_INVALID: "dispatching",
  NPC_COGNITION_FALLBACK_PROVIDER_RESPONSE_LIMIT_EXCEEDED: "dispatching",
  NPC_COGNITION_FALLBACK_MODEL_MISMATCH: "dispatching",
  NPC_COGNITION_FALLBACK_USAGE_INVALID: "dispatching",
  NPC_COGNITION_FALLBACK_PROPOSAL_INVALID: "dispatching",
  NPC_COGNITION_FALLBACK_UNTRUSTED_OUTPUT_REJECTED: "dispatching",
  NPC_COGNITION_FALLBACK_CONTEXT_STALE: "validated",
  NPC_COGNITION_FALLBACK_CHOICE_INVALID: "validated",
  NPC_COGNITION_FALLBACK_DISPLAY_UNCONFIRMED: "validated",
  NPC_COGNITION_FALLBACK_ACTION_CHOICE_UNKNOWN: "dispatching",
  NPC_COGNITION_FALLBACK_R20_UNAVAILABLE: "queued_for_r20",
  NPC_COGNITION_FALLBACK_R20_SELECTION_STALE: "queued_for_r20",
  NPC_COGNITION_FALLBACK_R19_FAILURE: "queued_for_r20",
});

function receiptSemantics(value) {
  const output = [
    ...ledgerHeadDiagnostics(value.ledger.before, "NPC_COGNITION_TURN_RECEIPT_BEFORE", "/ledger/before"),
    ...ledgerHeadDiagnostics(value.ledger.after, "NPC_COGNITION_TURN_RECEIPT_AFTER", "/ledger/after"),
  ];
  const history = value.statusHistory;
  if (history[0] !== "planned") output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_HISTORY_START", "/statusHistory/0"));
  if (history.at(-1) !== value.status) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_STATUS_MISMATCH", "/status"));
  for (let index = 1; index < history.length; index += 1) {
    if (!NEXT_STATUS[history[index - 1]]?.has(history[index])) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_STATUS_TRANSITION", `/statusHistory/${index}`));
  }
  const has = (status) => history.includes(status);
  const dispatched = has("dispatching");
  const reserved = has("reserved");
  const validated = has("validated");
  const queued = has("queued_for_r20");
  const dialogueOnly = has("dialogue_only");
  const fallback = has("fallback");
  const adjudicated = has("adjudicated");
  const preRequestFallback = fallback && [
    "NPC_COGNITION_FALLBACK_PROVIDER_CREDENTIAL_UNAVAILABLE",
    "NPC_COGNITION_FALLBACK_APPROVAL_EXPIRED_PRE_REQUEST",
  ].includes(value.fallbackReason);
  const expectedRequestCount = dispatched && !preRequestFallback ? 1 : 0;
  if (value.requestCount !== expectedRequestCount) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_REQUEST_COUNT_MISMATCH", "/requestCount"));
  if (value.usage.inputTokens + value.usage.outputTokens !== value.usage.totalTokens || !Number.isSafeInteger(value.usage.inputTokens + value.usage.outputTokens)) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_USAGE_TOTAL_MISMATCH", "/usage/totalTokens"));
  if (value.usage.cachedInputTokens + value.usage.cacheWriteInputTokens > value.usage.inputTokens
      || !Number.isSafeInteger(value.usage.cachedInputTokens + value.usage.cacheWriteInputTokens)) {
    output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_INPUT_DETAILS_EXCEED_TOTAL", "/usage/inputTokens"));
  }
  if (value.budget.actualMicrousd > value.budget.reservedMicrousd) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_BUDGET_EXCEEDED", "/budget/actualMicrousd"));
  if (value.budget.reservedMicrousd !== (reserved ? NPC_COGNITION_LIMITS.perCallMicrousd : 0)) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_RESERVATION_MISMATCH", "/budget/reservedMicrousd"));
  if (value.requestCount === 0 && (value.budget.actualMicrousd !== 0 || value.usage.totalTokens !== 0 || value.returnedModel !== null)) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_ZERO_REQUEST_EVIDENCE", "/requestCount"));
  if (validated && (value.proposalSha256 === null || value.returnedModel !== value.requestedModel)) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_VALIDATION_EVIDENCE", "/proposalSha256"));
  if (validated && value.usage.totalTokens === 0) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_VALIDATED_USAGE_EMPTY", "/usage/totalTokens"));
  if (!validated && value.proposalSha256 !== null) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_PROPOSAL_WITHOUT_VALIDATION", "/proposalSha256"));
  if (value.proposalSha256 === null && value.actionChoiceId !== null) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_CHOICE_WITHOUT_PROPOSAL", "/actionChoiceId"));
  if (queued && (value.actionChoiceId === null || value.mappedIntentSha256 === null)) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_QUEUE_EVIDENCE", "/mappedIntentSha256"));
  if (dialogueOnly && (value.actionChoiceId !== null || value.mappedIntentSha256 !== null || value.adjudicationResultSha256 !== null)) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_DIALOGUE_ONLY_MUTATION", "/actionChoiceId"));
  if (adjudicated && (value.actionChoiceId === null || value.mappedIntentSha256 === null || value.adjudicationResultSha256 === null)) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_ADJUDICATION_EVIDENCE", "/adjudicationResultSha256"));
  if (!queued && !adjudicated && value.mappedIntentSha256 !== null) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_ORPHAN_INTENT", "/mappedIntentSha256"));
  if (value.mappedIntentSha256 !== null && value.actionChoiceId === null) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_INTENT_WITHOUT_CHOICE", "/mappedIntentSha256"));
  if (!adjudicated && value.adjudicationResultSha256 !== null) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_ORPHAN_ADJUDICATION", "/adjudicationResultSha256"));
  if (fallback !== (value.fallbackReason !== NONE)) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_FALLBACK_REASON_MISMATCH", "/fallbackReason"));
  if (fallback) {
    const fallbackIndex = history.indexOf("fallback");
    const actualStage = history[fallbackIndex - 1] ?? null;
    const expectedStage = FALLBACK_STAGE_BY_REASON[value.fallbackReason] ?? null;
    if (expectedStage === null || actualStage !== expectedStage) {
      output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_FALLBACK_STAGE_MISMATCH", "/fallbackReason"));
    }
  } else if (!dialogueOnly && !adjudicated) {
    output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_SUCCESS_OUTCOME_MISSING", "/statusHistory"));
  }
  if (value.returnedModel !== null && value.returnedModel !== value.requestedModel && !fallback) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_MODEL_MISMATCH_NOT_FALLBACK", "/returnedModel"));
  if (!adjudicated && !same(value.ledger.before, value.ledger.after)) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_NON_ADJUDICATED_LEDGER_CHANGED", "/ledger/after"));
  if (adjudicated) {
    if (value.ledger.after.revision !== value.ledger.before.revision + 1) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_ADJUDICATION_REVISION", "/ledger/after/revision"));
    if (value.ledger.after.headSha256 === null) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_ADJUDICATION_HEAD", "/ledger/after/headSha256"));
    if (value.ledger.after.headSha256 === value.ledger.before.headSha256) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_ADJUDICATION_HEAD_UNCHANGED", "/ledger/after/headSha256"));
  }
  if (fallback && value.requestCount === 1 && value.usage.totalTokens === 0 &&
      value.budget.actualMicrousd !== value.budget.reservedMicrousd) {
    output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_DISPATCH_FALLBACK_NOT_FULLY_CHARGED", "/budget/actualMicrousd"));
  }
  if (fallback && value.requestCount === 0 && value.budget.actualMicrousd !== 0) {
    output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_PRE_DISPATCH_FALLBACK_CHARGED", "/budget/actualMicrousd"));
  }
  if (dispatched && value.requestCount === 1 && value.usage.totalTokens > 0) {
    const ordinaryInputTokens = value.usage.inputTokens - value.usage.cachedInputTokens - value.usage.cacheWriteInputTokens;
    const numerator = (BigInt(ordinaryInputTokens) * BigInt(NPC_COGNITION_LIMITS.inputMicrousdPerMillionTokens))
      + (BigInt(value.usage.cachedInputTokens) * BigInt(NPC_COGNITION_LIMITS.cachedInputMicrousdPerMillionTokens))
      + (BigInt(value.usage.cacheWriteInputTokens) * BigInt(NPC_COGNITION_LIMITS.cacheWriteInputMicrousdPerMillionTokens))
      + (BigInt(value.usage.outputTokens) * BigInt(NPC_COGNITION_LIMITS.outputMicrousdPerMillionTokens));
    const expectedCost = Number((numerator + 999999n) / 1000000n);
    if (value.budget.actualMicrousd !== expectedCost) output.push(diagnostic("semantic", "NPC_COGNITION_TURN_RECEIPT_ACTUAL_COST_MISMATCH", "/budget/actualMicrousd"));
  }
  return output;
}
function traceSemantics(value) {
  const output = [];
  const actors = new Map(); const turns = new Set(); const plans = new Set(); const receipts = new Set();
  const ledgerEntryHashes = new Set(value.initialLedger.headSha256 === null ? [] : [value.initialLedger.headSha256]);
  let requests = 0; let cost = 0; let fallbacks = 0; let choices = 0; let adjudications = 0;
  let previousLedger = value.initialLedger;
  output.push(...ledgerHeadDiagnostics(value.initialLedger, "NPC_COGNITION_TRACE_INITIAL", "/initialLedger"));
  value.receipts.forEach((entry, index) => {
    if (entry.sequence !== index + 1) output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_SEQUENCE", `/receipts/${index}/sequence`));
    for (const [set, field, code] of [[turns, "turnSha256", "TURN"], [plans, "callPlanSha256", "PLAN"], [receipts, "turnReceiptSha256", "RECEIPT"]]) {
      if (set.has(entry[field])) output.push(diagnostic("semantic", `NPC_COGNITION_TRACE_${code}_DUPLICATE`, `/receipts/${index}/${field}`));
      set.add(entry[field]);
    }
    actors.set(entry.actorEntityId, (actors.get(entry.actorEntityId) ?? 0) + 1);
    if (actors.get(entry.actorEntityId) > NPC_COGNITION_LIMITS.turnsPerActor) output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_ACTOR_TURN_LIMIT", `/receipts/${index}/actorEntityId`));
    output.push(...ledgerHeadDiagnostics(entry.beforeLedger, "NPC_COGNITION_TRACE_BEFORE", `/receipts/${index}/beforeLedger`));
    output.push(...ledgerHeadDiagnostics(entry.afterLedger, "NPC_COGNITION_TRACE_AFTER", `/receipts/${index}/afterLedger`));
    requests += entry.requestCount; cost += entry.actualMicrousd;
    fallbacks += Number(entry.outcome === "fallback"); choices += Number(entry.actionChoiceId !== null); adjudications += Number(entry.outcome === "adjudicated");
    if (entry.outcome === "dialogue_only" && entry.actionChoiceId !== null) output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_DIALOGUE_ONLY_CHOICE", `/receipts/${index}/actionChoiceId`));
    if (entry.outcome === "adjudicated" && entry.actionChoiceId === null) output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_ADJUDICATION_CHOICE", `/receipts/${index}/actionChoiceId`));
    if ((entry.outcome === "dialogue_only" || entry.outcome === "adjudicated") && entry.requestCount !== 1) output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_PROVIDER_OUTCOME_WITHOUT_REQUEST", `/receipts/${index}/requestCount`));
    const interveningDelta = entry.beforeLedger.revision - previousLedger.revision;
    if (interveningDelta < 0 || entry.interveningAuthorityEntrySha256.length !== interveningDelta) {
      output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_INTERVENING_AUTHORITY_COUNT", `/receipts/${index}/interveningAuthorityEntrySha256`));
    }
    entry.interveningAuthorityEntrySha256.forEach((entrySha256, authorityIndex) => {
      if (ledgerEntryHashes.has(entrySha256)) output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_LEDGER_ENTRY_HASH_REUSED", `/receipts/${index}/interveningAuthorityEntrySha256/${authorityIndex}`));
      ledgerEntryHashes.add(entrySha256);
    });
    if (interveningDelta === 0) {
      if (!same(entry.beforeLedger, previousLedger)) output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_BEFORE_LEDGER_MISMATCH", `/receipts/${index}/beforeLedger`));
    } else if (entry.interveningAuthorityEntrySha256.at(-1) !== entry.beforeLedger.headSha256) {
      output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_INTERVENING_AUTHORITY_HEAD", `/receipts/${index}/interveningAuthorityEntrySha256`));
    }
    const localDelta = entry.afterLedger.revision - entry.beforeLedger.revision;
    const expectedLocalDelta = Number(entry.outcome === "adjudicated");
    if (localDelta !== expectedLocalDelta) output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_LOCAL_OUTCOME_REVISION", `/receipts/${index}/afterLedger/revision`));
    if (localDelta === 0 && !same(entry.afterLedger, entry.beforeLedger)) output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_LOCAL_OUTCOME_CHANGED_LEDGER", `/receipts/${index}/afterLedger`));
    if (localDelta === 1) {
      if (entry.afterLedger.headSha256 === null || entry.afterLedger.headSha256 === entry.beforeLedger.headSha256) {
        output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_LOCAL_OUTCOME_HEAD", `/receipts/${index}/afterLedger/headSha256`));
      }
      if (ledgerEntryHashes.has(entry.afterLedger.headSha256)) output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_LEDGER_ENTRY_HASH_REUSED", `/receipts/${index}/afterLedger/headSha256`));
      ledgerEntryHashes.add(entry.afterLedger.headSha256);
    }
    previousLedger = entry.afterLedger;
  });
  const actorRequestCounts = new Map();
  value.receipts.forEach((entry, index) => {
    actorRequestCounts.set(entry.actorEntityId, (actorRequestCounts.get(entry.actorEntityId) ?? 0) + entry.requestCount);
    if (actorRequestCounts.get(entry.actorEntityId) > NPC_COGNITION_LIMITS.callsPerActor) {
      output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_ACTOR_CALL_LIMIT", `/receipts/${index}/requestCount`));
    }
  });
  if (requests > NPC_COGNITION_LIMITS.callsPerTimeline) output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_TIMELINE_CALL_LIMIT", "/totals/providerRequests"));
  const expected = { turns: value.receipts.length, providerRequests: requests, actualMicrousd: cost, fallbackTurns: fallbacks, actionChoiceTurns: choices, adjudicatedTurns: adjudications };
  if (!same(value.totals, expected)) output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_TOTALS_MISMATCH", "/totals"));
  const last = value.receipts.at(-1);
  if (last) {
    if (value.throughRevision !== last.afterLedger.revision || value.throughHeadSha256 !== last.afterLedger.headSha256) output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_LEDGER_TAIL_MISMATCH", "/throughRevision"));
  } else if (value.throughRevision !== value.initialLedger.revision || value.throughHeadSha256 !== value.initialLedger.headSha256) output.push(diagnostic("semantic", "NPC_COGNITION_TRACE_EMPTY_LEDGER_MISMATCH", "/throughRevision"));
  output.push(...ledgerHeadDiagnostics({ revision: value.throughRevision, headSha256: value.throughHeadSha256 }, "NPC_COGNITION_TRACE", ""));
  return output;
}
function qualificationSemantics(value) {
  const output = [];
  if (value.markers.join("\0") !== NPC_COGNITION_QUALIFICATION_MARKERS.join("\0")) output.push(diagnostic("semantic", "NPC_COGNITION_QUALIFICATION_MARKER_ORDER", "/markers"));
  output.push(...ordered(value.offlineCases, (item) => item.caseId, "NPC_COGNITION_QUALIFICATION_OFFLINE_CASE_ORDER", "/offlineCases"));
  const evidence = new Set(); const traces = new Set();
  value.offlineCases.forEach((item, index) => {
    if (evidence.has(item.evidenceSha256)) output.push(diagnostic("semantic", "NPC_COGNITION_QUALIFICATION_OFFLINE_EVIDENCE_DUPLICATE", `/offlineCases/${index}/evidenceSha256`));
    if (traces.has(item.traceSha256)) output.push(diagnostic("semantic", "NPC_COGNITION_QUALIFICATION_OFFLINE_TRACE_DUPLICATE", `/offlineCases/${index}/traceSha256`));
    evidence.add(item.evidenceSha256);
    traces.add(item.traceSha256);
  });
  if (value.providerCalls.fake < 3 || value.providerCalls.fake < value.offlineCases.length) output.push(diagnostic("semantic", "NPC_COGNITION_QUALIFICATION_OFFLINE_PROVIDER_EVIDENCE_INSUFFICIENT", "/providerCalls/fake"));
  if (value.stage === "offline" && value.providerCalls.real !== 0) output.push(diagnostic("semantic", "NPC_COGNITION_QUALIFICATION_OFFLINE_REAL_CALL", "/providerCalls/real"));
  if (value.stage === "final" && value.providerCalls.real !== 1) output.push(diagnostic("semantic", "NPC_COGNITION_QUALIFICATION_FINAL_REAL_CALL_REQUIRED", "/providerCalls/real"));
  if (value.providerCalls.replay !== value.replay.providerReplayRequests) output.push(diagnostic("semantic", "NPC_COGNITION_QUALIFICATION_REPLAY_REQUEST_MISMATCH", "/replay/providerReplayRequests"));
  return output;
}

function validateDocument(text, { prefix, limit, validator, semantics }) {
  try {
    const parsed = parseDocument(text, prefix, limit);
    if (!parsed.ok) return report(parsed.diagnostics);
    const schema = schemaDiagnostics(validator, prefix, parsed.value);
    if (schema.length) return report(schema);
    const semantic = [...stringDiagnostics(parsed.value, prefix), ...semantics(parsed.value)];
    if (semantic.length) return report(semantic);
    if (canonicalizeJsonValue(parsed.value) !== text) return report([diagnostic("canonical", `${prefix}_JSON_NON_CANONICAL`)]);
    return report([]);
  } catch (error) {
    if (error instanceof NpcCognitionContractOperationalError) throw error;
    throw new NpcCognitionContractOperationalError();
  }
}

export const validateNpcCognitionPolicyJson = (text) => validateDocument(text, { prefix: "NPC_COGNITION_POLICY", limit: NPC_COGNITION_LIMITS.policyBytes, validator: validators.get("POLICY"), semantics: policySemantics });
export const validateNpcCognitionTurnRequestJson = (text) => validateDocument(text, { prefix: "NPC_COGNITION_TURN_REQUEST", limit: NPC_COGNITION_LIMITS.turnRequestBytes, validator: validators.get("TURN_REQUEST"), semantics: turnRequestSemantics });
export const validateNpcCognitionCallPlanJson = (text) => validateDocument(text, { prefix: "NPC_COGNITION_CALL_PLAN", limit: NPC_COGNITION_LIMITS.callPlanBytes, validator: validators.get("CALL_PLAN"), semantics: callPlanSemantics });
export const validateNpcDialogueProposalJson = (text) => validateDocument(text, { prefix: "NPC_DIALOGUE_PROPOSAL", limit: NPC_COGNITION_LIMITS.dialogueProposalBytes, validator: validators.get("DIALOGUE_PROPOSAL"), semantics: dialogueProposalSemantics });
export const validateNpcCognitionTurnReceiptJson = (text) => validateDocument(text, { prefix: "NPC_COGNITION_TURN_RECEIPT", limit: NPC_COGNITION_LIMITS.turnReceiptBytes, validator: validators.get("TURN_RECEIPT"), semantics: receiptSemantics });
export const validateNpcCognitionTraceJson = (text) => validateDocument(text, { prefix: "NPC_COGNITION_TRACE", limit: NPC_COGNITION_LIMITS.traceBytes, validator: validators.get("TRACE"), semantics: traceSemantics });
export const validateNpcCognitionQualificationReportJson = (text) => validateDocument(text, { prefix: "NPC_COGNITION_QUALIFICATION_REPORT", limit: NPC_COGNITION_LIMITS.qualificationReportBytes, validator: validators.get("QUALIFICATION"), semantics: qualificationSemantics });
