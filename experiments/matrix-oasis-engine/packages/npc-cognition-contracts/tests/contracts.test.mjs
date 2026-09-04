import assert from "node:assert/strict";
import test from "node:test";
import * as api from "../src/index.mjs";
import {
  callPlan,
  canonical,
  choice,
  dialogueOnlyReceipt,
  dialogueProposal,
  policy,
  preDispatchFallbackReceipt,
  qualificationReport,
  sha,
  trace,
  turnReceipt,
  turnRequest,
} from "./fixtures.mjs";

const cases = [
  [api.validateNpcCognitionPolicyJson, policy],
  [api.validateNpcCognitionTurnRequestJson, turnRequest],
  [api.validateNpcCognitionCallPlanJson, callPlan],
  [api.validateNpcDialogueProposalJson, dialogueProposal],
  [api.validateNpcCognitionTurnReceiptJson, turnReceipt],
  [api.validateNpcCognitionTraceJson, trace],
  [api.validateNpcCognitionQualificationReportJson, qualificationReport],
];

const hasCode = (report, code) => report.diagnostics.some((item) => item.code === code);

test("R22 exports exactly seven closed frozen contract schemas", () => {
  assert.equal(api.NPC_COGNITION_FORMAT_VERSION, "0.1.0");
  assert.equal(api.NPC_COGNITION_ENDPOINT, "https://api.openai.com/v1/responses");
  assert.equal(api.NPC_COGNITION_MODEL, "gpt-5.6-luna");
  assert.equal(api.NPC_COGNITION_PROFILE, "matrix-oasis.bounded-npc-cognition/1");
  const schemas = [
    api.NPC_COGNITION_POLICY_SCHEMA,
    api.NPC_COGNITION_TURN_REQUEST_SCHEMA,
    api.NPC_COGNITION_CALL_PLAN_SCHEMA,
    api.NPC_DIALOGUE_PROPOSAL_SCHEMA,
    api.NPC_COGNITION_TURN_RECEIPT_SCHEMA,
    api.NPC_COGNITION_TRACE_SCHEMA,
    api.NPC_COGNITION_QUALIFICATION_REPORT_SCHEMA,
  ];
  assert.equal(schemas.length, 7);
  for (const schema of schemas) {
    assert.equal(schema.additionalProperties, false);
    assert.equal(Object.isFrozen(schema), true);
    assert.equal(Object.isFrozen(schema.properties), true);
    const stack = [schema];
    while (stack.length) {
      const current = stack.pop();
      if (!current || typeof current !== "object") continue;
      if (current.type === "object") assert.equal(current.additionalProperties, false, current.$id ?? JSON.stringify(current));
      for (const child of Object.values(current)) {
        if (child && typeof child === "object") stack.push(child);
      }
    }
  }
});

test("canonical fixtures validate and reports are deeply frozen", () => {
  for (const [validate, fixture] of cases) {
    const report = validate(canonical(fixture()));
    assert.equal(report.valid, true, canonical(report));
    assert.equal(Object.isFrozen(report), true);
    assert.equal(Object.isFrozen(report.diagnostics), true);
  }
  const invalid = api.validateNpcCognitionPolicyJson("{}");
  assert.equal(Object.isFrozen(invalid.diagnostics[0]), true);
});

test("unknown fields, duplicate keys, floats and noncanonical bytes fail closed", () => {
  for (const [validate, fixture] of cases) {
    const unknown = fixture();
    unknown.rawPrompt = "must-not-fit";
    assert.equal(validate(canonical(unknown)).valid, false);
  }
  assert.match(api.validateNpcCognitionPolicyJson('{"format":"a","format":"b"}').diagnostics[0].code, /DUPLICATE_KEY/);
  assert.match(api.validateNpcCognitionPolicyJson(`${"[".repeat(257)}0${"]".repeat(257)}`).diagnostics[0].code, /DEPTH_EXCEEDED/);
  assert.match(api.validateNpcCognitionPolicyJson(`{"x":"${"a".repeat(api.NPC_COGNITION_LIMITS.policyBytes)}"}`).diagnostics[0].code, /SIZE_EXCEEDED/);
  assert.match(api.validateNpcCognitionPolicyJson(JSON.stringify(policy(), null, 2)).diagnostics[0].code, /NON_CANONICAL/);

  const floating = [
    [api.validateNpcCognitionPolicyJson, policy, (value) => { value.limits.callsPerActor = 8.5; }],
    [api.validateNpcCognitionTurnRequestJson, turnRequest, (value) => { value.sequence = 1.5; }],
    [api.validateNpcCognitionCallPlanJson, callPlan, (value) => { value.requestBytes = 1.5; }],
    [api.validateNpcCognitionTurnReceiptJson, turnReceipt, (value) => { value.usage.inputTokens = 1.5; }],
    [api.validateNpcCognitionTraceJson, trace, (value) => { value.totals.turns = 1.5; }],
    [api.validateNpcCognitionQualificationReportJson, qualificationReport, (value) => { value.providerCalls.fake = 1.5; }],
  ];
  for (const [validate, fixture, mutate] of floating) {
    const value = fixture();
    mutate(value);
    assert.equal(validate(JSON.stringify(value)).valid, false);
  }
});

test("parse tree rejects prototype-sensitive property names as ordinary invalid input", () => {
  for (const [validate] of cases) {
    for (const propertyName of ["__proto__", "constructor", "prototype", "\\u005f\\u005fproto__"]) {
      const report = validate(`{"${propertyName}":{}}`);
      assert.equal(report.valid, false);
      assert(hasCode(report, `${report.diagnostics[0].code.split("_JSON_")[0]}_JSON_FORBIDDEN_PROPERTY_NAME`));
      assert.equal(report.diagnostics.some((item) => item.code === "NPC_COGNITION_CONTRACT_INTERNAL_ERROR"), false);
    }
  }
});

test("player and dialogue strings require well-formed NFC text without forbidden controls or bidi", () => {
  const allowedLf = turnRequest();
  allowedLf.playerText = "first\nsecond";
  assert.equal(api.validateNpcCognitionTurnRequestJson(canonical(allowedLf)).valid, true);

  for (const text of ["e\u0301", "before\u0000after", "before\rafter", "before\u0085after", "before\u2028after", "before\u2029after", "before\u202eafter", "\ud800"]) {
    const value = turnRequest();
    value.playerText = text;
    assert.equal(api.validateNpcCognitionTurnRequestJson(canonical(value)).valid, false, JSON.stringify(text));
  }
  const oversized = turnRequest();
  oversized.playerText = "界".repeat(1366);
  assert(hasCode(api.validateNpcCognitionTurnRequestJson(canonical(oversized)), "NPC_COGNITION_TURN_REQUEST_PLAYER_TEXT_BYTES_EXCEEDED"));
  const blank = turnRequest();
  blank.playerText = "  \n  ";
  assert(hasCode(api.validateNpcCognitionTurnRequestJson(canonical(blank)), "NPC_COGNITION_TURN_REQUEST_PLAYER_TEXT_EMPTY"));
});

test("policy actors and safe actions are bounded, unique and deterministically ordered", () => {
  assert.equal(policy().limits.turnsPerTimeline, 64);
  assert.equal(policy().limits.turnsPerActor, 32);
  for (const mutate of [
    (value) => value.actors.reverse(),
    (value) => value.actors.push(structuredClone(value.actors[0])),
    (value) => value.actors[0].safeActions.reverse(),
    (value) => value.actors[0].safeActions.push(structuredClone(value.actors[0].safeActions[0])),
  ]) {
    const value = policy();
    mutate(value);
    assert.equal(api.validateNpcCognitionPolicyJson(canonical(value)).valid, false);
  }
  const tooManyActors = policy();
  tooManyActors.actors = Array.from({ length: 7 }, (_, index) => ({
    actorEntityId: `actor-${String(index).padStart(2, "0")}`,
    safeActions: [],
  }));
  assert.equal(api.validateNpcCognitionPolicyJson(canonical(tooManyActors)).valid, false);
  const noActors = policy();
  noActors.actors = [];
  assert.equal(api.validateNpcCognitionPolicyJson(canonical(noActors)).valid, false);
  const dialogueOnlyActor = policy();
  dialogueOnlyActor.actors = [{ actorEntityId: "actor-one", safeActions: [] }];
  assert.equal(api.validateNpcCognitionPolicyJson(canonical(dialogueOnlyActor)).valid, true);
  const tooManyActions = policy();
  tooManyActions.actors = [{
    actorEntityId: "actor-one",
    safeActions: Array.from({ length: 65 }, (_, index) => ({
      nodeId: `node-${String(index).padStart(2, "0")}`,
      actionId: "action-one",
    })),
  }];
  assert.equal(api.validateNpcCognitionPolicyJson(canonical(tooManyActions)).valid, false);

  const lastTurn = turnRequest();
  lastTurn.sequence = 64;
  assert.equal(api.validateNpcCognitionTurnRequestJson(canonical(lastTurn)).valid, true);
  lastTurn.sequence = 65;
  assert.equal(api.validateNpcCognitionTurnRequestJson(canonical(lastTurn)).valid, false);
});

test("approval hash binds every call-plan field except its own hash", () => {
  const value = callPlan();
  const unchangedInput = canonical(value);
  const original = value.approval.hash;
  value.approval.hash = sha("0");
  assert.equal(api.computeNpcCognitionApprovalHash(value), original);
  value.approval.hash = original;
  assert.equal(api.validateNpcCognitionCallPlanJson(canonical(value)).valid, true);
  assert.equal(canonical(value), unchangedInput);

  for (const [mutate, schemaStillValid] of [
    [(plan) => { plan.contextSha256 = sha("1"); }, true],
    [(plan) => { plan.providerPayloadSha256 = sha("2"); }, true],
    [(plan) => { plan.responseSchemaSha256 = sha("3"); }, true],
    [(plan) => { plan.priceLock.cachedInputMicrousdPerMillionTokens += 1; }, false],
    [(plan) => { plan.requestBytes += 1; }, true],
    [(plan) => { plan.retention.promptCachingPossible = false; }, false],
  ]) {
    const changed = callPlan();
    mutate(changed);
    const report = api.validateNpcCognitionCallPlanJson(canonical(changed));
    assert.equal(report.valid, false);
    if (schemaStillValid) assert(hasCode(report, "NPC_COGNITION_CALL_PLAN_APPROVAL_HASH_MISMATCH"));
  }
  assert.throws(
    () => api.computeNpcCognitionApprovalHash(null),
    (error) => error?.code === "NPC_COGNITION_CONTRACT_INTERNAL_ERROR" && error.message === "NPC_COGNITION_CONTRACT_INTERNAL_ERROR",
  );
});

test("dialogue is byte/line bounded while markup remains inert text", () => {
  const inert = dialogueProposal();
  inert.dialogueText = "[url=file:///secret][b]text[/b][/url] <script>alert(1)</script>";
  assert.equal(api.validateNpcDialogueProposalJson(canonical(inert)).valid, true);
  const nineLines = dialogueProposal();
  nineLines.dialogueText = Array.from({ length: 9 }, (_, index) => `line ${index}`).join("\n");
  assert(hasCode(api.validateNpcDialogueProposalJson(canonical(nineLines)), "NPC_DIALOGUE_PROPOSAL_DIALOGUE_LINES_EXCEEDED"));
  const multibyte = dialogueProposal();
  multibyte.dialogueText = "界".repeat(683);
  assert(hasCode(api.validateNpcDialogueProposalJson(canonical(multibyte)), "NPC_DIALOGUE_PROPOSAL_DIALOGUE_BYTES_EXCEEDED"));
  const badChoice = dialogueProposal();
  badChoice.actionChoiceId = "choice-action-one";
  assert.equal(api.validateNpcDialogueProposalJson(canonical(badChoice)).valid, false);
  const lineSeparator = dialogueProposal();
  lineSeparator.dialogueText = "first\u2028second";
  assert(hasCode(api.validateNpcDialogueProposalJson(canonical(lineSeparator)), "NPC_DIALOGUE_PROPOSAL_TEXT_FORBIDDEN_LINE_SEPARATOR"));
});

test("receipt state, request, proposal, budget and Ledger evidence stay coherent", () => {
  assert.equal(api.validateNpcCognitionTurnReceiptJson(canonical(turnReceipt())).valid, true);
  assert.equal(api.validateNpcCognitionTurnReceiptJson(canonical(dialogueOnlyReceipt())).valid, true);
  assert.equal(api.validateNpcCognitionTurnReceiptJson(canonical(preDispatchFallbackReceipt())).valid, true);

  const skipped = turnReceipt();
  skipped.statusHistory = ["planned", "reserved", "dispatching", "validated", "queued_for_r20", "adjudicated", "finalized"];
  assert(hasCode(api.validateNpcCognitionTurnReceiptJson(canonical(skipped)), "NPC_COGNITION_TURN_RECEIPT_STATUS_TRANSITION"));

  const requestMismatch = turnReceipt();
  requestMismatch.requestCount = 0;
  assert(hasCode(api.validateNpcCognitionTurnReceiptJson(canonical(requestMismatch)), "NPC_COGNITION_TURN_RECEIPT_REQUEST_COUNT_MISMATCH"));

  const proposalWithoutValidation = preDispatchFallbackReceipt();
  proposalWithoutValidation.proposalSha256 = sha("a");
  assert(hasCode(api.validateNpcCognitionTurnReceiptJson(canonical(proposalWithoutValidation)), "NPC_COGNITION_TURN_RECEIPT_PROPOSAL_WITHOUT_VALIDATION"));

  const badUsage = turnReceipt();
  badUsage.usage.totalTokens += 1;
  assert(hasCode(api.validateNpcCognitionTurnReceiptJson(canonical(badUsage)), "NPC_COGNITION_TURN_RECEIPT_USAGE_TOTAL_MISMATCH"));
  const badCost = turnReceipt();
  badCost.budget.actualMicrousd = 43;
  assert(hasCode(api.validateNpcCognitionTurnReceiptJson(canonical(badCost)), "NPC_COGNITION_TURN_RECEIPT_ACTUAL_COST_MISMATCH"));
  const cachedAndWritten = turnReceipt();
  cachedAndWritten.usage = { inputTokens: 100, cachedInputTokens: 20, cacheWriteInputTokens: 10, outputTokens: 20, totalTokens: 120 };
  cachedAndWritten.budget.actualMicrousd = 41;
  assert.equal(api.validateNpcCognitionTurnReceiptJson(canonical(cachedAndWritten)).valid, true);
  cachedAndWritten.usage.cacheWriteInputTokens = 81;
  assert(hasCode(api.validateNpcCognitionTurnReceiptJson(canonical(cachedAndWritten)), "NPC_COGNITION_TURN_RECEIPT_INPUT_DETAILS_EXCEED_TOTAL"));
  const tooManyOutputTokens = turnReceipt();
  tooManyOutputTokens.usage = { inputTokens: 100, cachedInputTokens: 0, cacheWriteInputTokens: 0, outputTokens: 513, totalTokens: 613 };
  assert.equal(api.validateNpcCognitionTurnReceiptJson(canonical(tooManyOutputTokens)).valid, false);

  const ambiguous = turnReceipt();
  ambiguous.proposalSha256 = null;
  ambiguous.statusHistory = ["planned", "approved", "reserved", "dispatching", "fallback", "finalized"];
  ambiguous.returnedModel = null;
  ambiguous.usage = { inputTokens: 0, cachedInputTokens: 0, cacheWriteInputTokens: 0, outputTokens: 0, totalTokens: 0 };
  ambiguous.budget.actualMicrousd = 10000;
  ambiguous.fallbackReason = "NPC_COGNITION_FALLBACK_PROVIDER_NETWORK_AMBIGUOUS";
  ambiguous.actionChoiceId = null;
  ambiguous.mappedIntentSha256 = null;
  ambiguous.adjudicationResultSha256 = null;
  ambiguous.ledger.after = structuredClone(ambiguous.ledger.before);
  assert.equal(api.validateNpcCognitionTurnReceiptJson(canonical(ambiguous)).valid, true);
  ambiguous.budget.actualMicrousd = 0;
  assert(hasCode(api.validateNpcCognitionTurnReceiptJson(canonical(ambiguous)), "NPC_COGNITION_TURN_RECEIPT_DISPATCH_FALLBACK_NOT_FULLY_CHARGED"));
  ambiguous.fallbackReason = "NPC_COGNITION_FALLBACK_DISPATCH_CRASH_UNCERTAIN";
  assert(hasCode(api.validateNpcCognitionTurnReceiptJson(canonical(ambiguous)), "NPC_COGNITION_TURN_RECEIPT_DISPATCH_FALLBACK_NOT_FULLY_CHARGED"));

  const invalidResponseWithUsage = turnReceipt();
  invalidResponseWithUsage.proposalSha256 = null;
  invalidResponseWithUsage.statusHistory = ["planned", "approved", "reserved", "dispatching", "fallback", "finalized"];
  invalidResponseWithUsage.fallbackReason = "NPC_COGNITION_FALLBACK_PROVIDER_RESPONSE_INVALID";
  invalidResponseWithUsage.actionChoiceId = null;
  invalidResponseWithUsage.mappedIntentSha256 = null;
  invalidResponseWithUsage.adjudicationResultSha256 = null;
  invalidResponseWithUsage.ledger.after = structuredClone(invalidResponseWithUsage.ledger.before);
  invalidResponseWithUsage.budget.actualMicrousd = 10000;
  assert.equal(api.validateNpcCognitionTurnReceiptJson(canonical(invalidResponseWithUsage)).valid, true);

  const impossibleReason = turnReceipt();
  impossibleReason.statusHistory = ["planned", "approved", "reserved", "dispatching", "fallback", "finalized"];
  impossibleReason.proposalSha256 = null;
  impossibleReason.returnedModel = null;
  impossibleReason.usage = { inputTokens: 0, cachedInputTokens: 0, cacheWriteInputTokens: 0, outputTokens: 0, totalTokens: 0 };
  impossibleReason.budget.actualMicrousd = 0;
  impossibleReason.fallbackReason = "NPC_COGNITION_FALLBACK_APPROVAL_DECLINED";
  impossibleReason.actionChoiceId = null;
  impossibleReason.mappedIntentSha256 = null;
  impossibleReason.adjudicationResultSha256 = null;
  impossibleReason.ledger.after = structuredClone(impossibleReason.ledger.before);
  assert(hasCode(api.validateNpcCognitionTurnReceiptJson(canonical(impossibleReason)), "NPC_COGNITION_TURN_RECEIPT_FALLBACK_STAGE_MISMATCH"));

  const unchangedHead = turnReceipt();
  unchangedHead.ledger.after.headSha256 = unchangedHead.ledger.before.headSha256;
  assert(hasCode(api.validateNpcCognitionTurnReceiptJson(canonical(unchangedHead)), "NPC_COGNITION_TURN_RECEIPT_ADJUDICATION_HEAD_UNCHANGED"));

  const unfinished = turnReceipt();
  unfinished.status = "adjudicated";
  unfinished.statusHistory.pop();
  assert.equal(api.validateNpcCognitionTurnReceiptJson(canonical(unfinished)).valid, false);
});

test("every fallback reason is bound to exactly one preceding stage", () => {
  const stages = {
    NPC_COGNITION_FALLBACK_APPROVAL_DECLINED: "planned",
    NPC_COGNITION_FALLBACK_APPROVAL_EXPIRED: "planned",
    NPC_COGNITION_FALLBACK_BUDGET_EXHAUSTED: "approved",
    NPC_COGNITION_FALLBACK_PROVIDER_CREDENTIAL_UNAVAILABLE: "reserved",
    NPC_COGNITION_FALLBACK_CALL_IN_FLIGHT: "planned",
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
    NPC_COGNITION_FALLBACK_UNTRUSTED_OUTPUT_REJECTED: "validated",
    NPC_COGNITION_FALLBACK_CONTEXT_STALE: "validated",
    NPC_COGNITION_FALLBACK_CHOICE_INVALID: "validated",
    NPC_COGNITION_FALLBACK_ACTION_CHOICE_UNKNOWN: "validated",
    NPC_COGNITION_FALLBACK_R20_UNAVAILABLE: "queued_for_r20",
    NPC_COGNITION_FALLBACK_R19_FAILURE: "queued_for_r20",
  };
  assert.deepEqual(
    Object.keys(stages).sort(),
    api.NPC_COGNITION_FALLBACK_REASONS.filter((reason) => reason !== "NPC_COGNITION_FALLBACK_NONE").sort(),
  );
  const histories = {
    planned: ["planned", "fallback", "finalized"],
    approved: ["planned", "approved", "fallback", "finalized"],
    reserved: ["planned", "approved", "reserved", "fallback", "finalized"],
    dispatching: ["planned", "approved", "reserved", "dispatching", "fallback", "finalized"],
    validated: ["planned", "approved", "reserved", "dispatching", "validated", "fallback", "finalized"],
    queued_for_r20: ["planned", "approved", "reserved", "dispatching", "validated", "queued_for_r20", "fallback", "finalized"],
  };
  const makeFallback = (reason, stage) => {
    const value = turnReceipt();
    value.statusHistory = histories[stage];
    value.fallbackReason = reason;
    value.requestCount = Number(["dispatching", "validated", "queued_for_r20"].includes(stage));
    value.budget.reservedMicrousd = Number(["reserved", "dispatching", "validated", "queued_for_r20"].includes(stage)) * 10000;
    value.budget.actualMicrousd = value.requestCount * value.budget.reservedMicrousd;
    value.returnedModel = ["validated", "queued_for_r20"].includes(stage) ? "gpt-5.6-luna" : null;
    value.usage = ["validated", "queued_for_r20"].includes(stage)
      ? { inputTokens: 100, cachedInputTokens: 0, cacheWriteInputTokens: 0, outputTokens: 20, totalTokens: 120 }
      : { inputTokens: 0, cachedInputTokens: 0, cacheWriteInputTokens: 0, outputTokens: 0, totalTokens: 0 };
    value.proposalSha256 = ["validated", "queued_for_r20"].includes(stage) ? sha("e") : null;
    value.actionChoiceId = stage === "queued_for_r20" ? choice("e") : null;
    value.mappedIntentSha256 = stage === "queued_for_r20" ? sha("1") : null;
    value.adjudicationResultSha256 = null;
    value.ledger.after = structuredClone(value.ledger.before);
    return value;
  };
  for (const [reason, stage] of Object.entries(stages)) {
    const valid = makeFallback(reason, stage);
    assert.equal(api.validateNpcCognitionTurnReceiptJson(canonical(valid)).valid, true, reason);
    const wrong = makeFallback(reason, stage === "planned" ? "approved" : "planned");
    assert(hasCode(api.validateNpcCognitionTurnReceiptJson(canonical(wrong)), "NPC_COGNITION_TURN_RECEIPT_FALLBACK_STAGE_MISMATCH"), reason);
    if (valid.requestCount === 1) {
      valid.budget.actualMicrousd = 0;
      assert(hasCode(api.validateNpcCognitionTurnReceiptJson(canonical(valid)), "NPC_COGNITION_TURN_RECEIPT_DISPATCH_FALLBACK_NOT_FULLY_CHARGED"), reason);
    }
  }
});

test("trace enforces stable sequence, unique identities, limits, totals and Ledger continuity", () => {
  assert.equal(api.validateNpcCognitionTraceJson(canonical(trace())).valid, true);
  const badTotals = trace();
  badTotals.totals.providerRequests = 1;
  assert(hasCode(api.validateNpcCognitionTraceJson(canonical(badTotals)), "NPC_COGNITION_TRACE_TOTALS_MISMATCH"));
  const duplicate = trace();
  duplicate.receipts[1].turnSha256 = duplicate.receipts[0].turnSha256;
  assert(hasCode(api.validateNpcCognitionTraceJson(canonical(duplicate)), "NPC_COGNITION_TRACE_TURN_DUPLICATE"));
  const jumped = trace();
  jumped.receipts[1].interveningAuthorityEntrySha256 = [];
  const jumpReport = api.validateNpcCognitionTraceJson(canonical(jumped));
  assert(hasCode(jumpReport, "NPC_COGNITION_TRACE_INTERVENING_AUTHORITY_COUNT"));
  const reusedLedgerHash = trace();
  reusedLedgerHash.receipts[1].interveningAuthorityEntrySha256[0] = reusedLedgerHash.initialLedger.headSha256;
  assert(hasCode(api.validateNpcCognitionTraceJson(canonical(reusedLedgerHash)), "NPC_COGNITION_TRACE_LEDGER_ENTRY_HASH_REUSED"));

  const actorLimit = trace();
  actorLimit.initialLedger = { ...actorLimit.initialLedger };
  actorLimit.receipts = Array.from({ length: 33 }, (_, index) => ({
    sequence: index + 1,
    actorEntityId: "actor-one",
    turnSha256: sha(index.toString(16)),
    callPlanSha256: sha((index + 1).toString(16)),
    turnReceiptSha256: sha((index + 2).toString(16)),
    requestCount: 0,
    actualMicrousd: 0,
    outcome: "fallback",
    actionChoiceId: null,
    beforeLedger: structuredClone(actorLimit.initialLedger),
    afterLedger: structuredClone(actorLimit.initialLedger),
    interveningAuthorityEntrySha256: [],
  }));
  actorLimit.totals = { turns: 33, providerRequests: 0, actualMicrousd: 0, fallbackTurns: 33, actionChoiceTurns: 0, adjudicatedTurns: 0 };
  actorLimit.throughRevision = actorLimit.initialLedger.revision;
  actorLimit.throughHeadSha256 = actorLimit.initialLedger.headSha256;
  assert(hasCode(api.validateNpcCognitionTraceJson(canonical(actorLimit)), "NPC_COGNITION_TRACE_ACTOR_TURN_LIMIT"));

  const callLimit = trace();
  callLimit.receipts = Array.from({ length: 17 }, (_, index) => ({
    sequence: index + 1,
    actorEntityId: `actor-${String((index % 3) + 1).padStart(2, "0")}`,
    turnSha256: sha(index.toString(16)),
    callPlanSha256: sha((index + 20).toString(16)),
    turnReceiptSha256: sha((index + 40).toString(16)),
    requestCount: 1,
    actualMicrousd: 1,
    outcome: "dialogue_only",
    actionChoiceId: null,
    beforeLedger: structuredClone(callLimit.initialLedger),
    afterLedger: structuredClone(callLimit.initialLedger),
    interveningAuthorityEntrySha256: [],
  }));
  callLimit.totals = { turns: 17, providerRequests: 17, actualMicrousd: 17, fallbackTurns: 0, actionChoiceTurns: 0, adjudicatedTurns: 0 };
  callLimit.throughRevision = callLimit.initialLedger.revision;
  callLimit.throughHeadSha256 = callLimit.initialLedger.headSha256;
  assert(hasCode(api.validateNpcCognitionTraceJson(canonical(callLimit)), "NPC_COGNITION_TRACE_TIMELINE_CALL_LIMIT"));

  const actorCallLimit = structuredClone(callLimit);
  actorCallLimit.receipts = actorCallLimit.receipts.slice(0, 9).map((entry) => ({ ...entry, actorEntityId: "actor-one" }));
  actorCallLimit.totals = { turns: 9, providerRequests: 9, actualMicrousd: 9, fallbackTurns: 0, actionChoiceTurns: 0, adjudicatedTurns: 0 };
  assert(hasCode(api.validateNpcCognitionTraceJson(canonical(actorCallLimit)), "NPC_COGNITION_TRACE_ACTOR_CALL_LIMIT"));
});

test("qualification states the non-replayable and non-retained provider boundary", () => {
  assert.equal(api.validateNpcCognitionQualificationReportJson(canonical(qualificationReport())).valid, true);
  const missingReplay = qualificationReport();
  delete missingReplay.replay;
  assert.equal(api.validateNpcCognitionQualificationReportJson(canonical(missingReplay)).valid, false);
  const reproducible = qualificationReport();
  reproducible.replay.modelOutputReproducible = true;
  assert.equal(api.validateNpcCognitionQualificationReportJson(canonical(reproducible)).valid, false);
  const noEvidence = qualificationReport();
  noEvidence.providerCalls.fake = 0;
  assert(hasCode(api.validateNpcCognitionQualificationReportJson(canonical(noEvidence)), "NPC_COGNITION_QUALIFICATION_OFFLINE_PROVIDER_EVIDENCE_INSUFFICIENT"));
  const realInOffline = qualificationReport();
  realInOffline.providerCalls.real = 1;
  assert(hasCode(api.validateNpcCognitionQualificationReportJson(canonical(realInOffline)), "NPC_COGNITION_QUALIFICATION_OFFLINE_REAL_CALL"));
  const final = qualificationReport();
  final.stage = "final";
  final.providerCalls.real = 1;
  assert.equal(api.validateNpcCognitionQualificationReportJson(canonical(final)).valid, true);
  final.providerCalls.real = 0;
  assert(hasCode(api.validateNpcCognitionQualificationReportJson(canonical(final)), "NPC_COGNITION_QUALIFICATION_FINAL_REAL_CALL_REQUIRED"));
  const duplicateEvidence = qualificationReport();
  duplicateEvidence.offlineCases[1].evidenceSha256 = duplicateEvidence.offlineCases[0].evidenceSha256;
  assert(hasCode(api.validateNpcCognitionQualificationReportJson(canonical(duplicateEvidence)), "NPC_COGNITION_QUALIFICATION_OFFLINE_EVIDENCE_DUPLICATE"));
  const duplicateTrace = qualificationReport();
  duplicateTrace.offlineCases[1].traceSha256 = duplicateTrace.offlineCases[0].traceSha256;
  assert(hasCode(api.validateNpcCognitionQualificationReportJson(canonical(duplicateTrace)), "NPC_COGNITION_QUALIFICATION_OFFLINE_TRACE_DUPLICATE"));
  const unorderedCases = qualificationReport();
  unorderedCases.offlineCases.reverse();
  assert(hasCode(api.validateNpcCognitionQualificationReportJson(canonical(unorderedCases)), "NPC_COGNITION_QUALIFICATION_OFFLINE_CASE_ORDER"));
  const missingGodotEvidence = qualificationReport();
  delete missingGodotEvidence.godotEvidenceSha256;
  assert.equal(api.validateNpcCognitionQualificationReportJson(canonical(missingGodotEvidence)).valid, false);
  const markerOrder = qualificationReport();
  markerOrder.markers.reverse();
  assert(hasCode(api.validateNpcCognitionQualificationReportJson(canonical(markerOrder)), "NPC_COGNITION_QUALIFICATION_MARKER_ORDER"));
});

test("all seven validator reports are byte deterministic across 20 runs", () => {
  for (const [validate, fixture] of cases) {
    const text = canonical(fixture());
    const reports = Array.from({ length: 20 }, () => canonical(validate(text)));
    assert.equal(new Set(reports).size, 1);
  }
});
