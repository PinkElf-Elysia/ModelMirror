import assert from "node:assert/strict";
import test from "node:test";
import {
  DERIVED_PROJECTION_MANIFEST_SCHEMA,
  NPC_ADJUDICATION_RESULT_SCHEMA,
  NPC_AUTHORITY_POLICY_SCHEMA,
  NPC_INTENT_SCHEMA,
  WORLD_EVENT_LEDGER_REPLAY_REPORT_SCHEMA,
  WORLD_EVENT_LEDGER_SCHEMA,
  validateDerivedProjectionManifestJson,
  validateNpcAdjudicationResultJson,
  validateNpcAuthorityPolicyJson,
  validateNpcIntentJson,
  validateWorldEventLedgerJson,
  validateWorldEventLedgerReplayReportJson,
} from "../src/index.mjs";
import {
  acceptedEntryFixture,
  canonical,
  clone,
  emptyLedgerFixture,
  hashCanonical,
  intentFixture,
  ledgerFixture,
  policyFixture,
  projectionFixture,
  replayFixture,
  resultFixture,
} from "./fixtures.mjs";

const validators = [
  ["policy", validateNpcAuthorityPolicyJson, policyFixture],
  ["intent", validateNpcIntentJson, intentFixture],
  ["result", validateNpcAdjudicationResultJson, resultFixture],
  ["ledger", validateWorldEventLedgerJson, ledgerFixture],
  ["projection", validateDerivedProjectionManifestJson, projectionFixture],
  ["replay", validateWorldEventLedgerReplayReportJson, replayFixture],
];

test("all R19 schemas are deeply frozen and close their roots", () => {
  for (const schema of [NPC_AUTHORITY_POLICY_SCHEMA, NPC_INTENT_SCHEMA, NPC_ADJUDICATION_RESULT_SCHEMA, WORLD_EVENT_LEDGER_SCHEMA, DERIVED_PROJECTION_MANIFEST_SCHEMA, WORLD_EVENT_LEDGER_REPLAY_REPORT_SCHEMA]) {
    assert.equal(schema.additionalProperties, false);
    assert.equal(Object.isFrozen(schema), true);
    assert.equal(Object.isFrozen(schema.properties), true);
  }
});

test("all golden documents validate without mutating input", () => {
  for (const [name, validate, fixture] of validators) {
    const value = fixture();
    const before = clone(value);
    const report = validate(canonical(value));
    assert.equal(report.valid, true, `${name}: ${JSON.stringify(report.diagnostics)}`);
    assert.deepEqual(value, before);
    assert.equal(Object.isFrozen(report), true);
    assert.equal(Object.isFrozen(report.diagnostics), true);
  }
  assert.equal(validateWorldEventLedgerJson(canonical(emptyLedgerFixture())).valid, true);
});

test("schema failures stop before semantic checks and do not echo unknown input", () => {
  const policy = policyFixture();
  policy.topSecret = "do-not-echo";
  policy.actorGrants.push({ actorEntityId: "actor-one", grants: [] });
  const report = validateNpcAuthorityPolicyJson(canonical(policy));
  assert.equal(report.valid, false);
  assert(report.diagnostics.every((diagnostic) => diagnostic.phase === "schema"));
  assert.equal(JSON.stringify(report).includes("topSecret"), false);
  assert.equal(JSON.stringify(report).includes("do-not-echo"), false);
});

test("parser rejects duplicate keys, malformed JSON, excessive size and non-string input", () => {
  const duplicate = '{"format":"matrix-oasis.npc-intent","format":"matrix-oasis.npc-intent"}';
  assert.deepEqual(validateNpcIntentJson(duplicate).diagnostics.map((value) => value.code), ["NPC_INTENT_JSON_DUPLICATE_KEY"]);
  assert.equal(validateNpcIntentJson("{").diagnostics[0].code, "NPC_INTENT_JSON_SYNTAX");
  assert.equal(validateNpcIntentJson(" ".repeat(65 * 1024)).diagnostics[0].code, "NPC_INTENT_JSON_SIZE_EXCEEDED");
  assert.equal(validateNpcIntentJson(null).diagnostics[0].code, "NPC_INTENT_JSON_INPUT_TYPE");
});

test("canonical profile rejects whitespace, floating revisions and lone surrogates", () => {
  assert.equal(validateNpcIntentJson(JSON.stringify(intentFixture(), null, 2)).diagnostics[0].code, "NPC_INTENT_JSON_NON_CANONICAL");
  const floating = intentFixture();
  floating.observed.revision = 0.5;
  assert(validateNpcIntentJson(JSON.stringify(floating)).diagnostics.some((value) => value.code === "NPC_INTENT_SCHEMA_TYPE"));
  const surrogate = policyFixture();
  surrogate.contentVersion = "1.0.0-\ud800";
  assert(validateNpcAuthorityPolicyJson(JSON.stringify(surrogate)).diagnostics.some((value) => value.code === "NPC_AUTHORITY_POLICY_TEXT_UNPAIRED_SURROGATE"));
});

test("policy rejects duplicate actors and duplicate exact grants", () => {
  const duplicateActor = policyFixture();
  duplicateActor.actorGrants.push(clone(duplicateActor.actorGrants[0]));
  assert(validateNpcAuthorityPolicyJson(canonical(duplicateActor)).diagnostics.some((value) => value.code === "NPC_AUTHORITY_POLICY_ACTOR_DUPLICATE"));
  const duplicateGrant = policyFixture();
  duplicateGrant.actorGrants[0].grants.push(clone(duplicateGrant.actorGrants[0].grants[0]));
  assert(validateNpcAuthorityPolicyJson(canonical(duplicateGrant)).diagnostics.some((value) => value.code === "NPC_AUTHORITY_POLICY_GRANT_DUPLICATE"));
});

test("decision semantics bind accepted transitions and rejected snapshot immutability", () => {
  const accepted = resultFixture();
  accepted.transition = null;
  assert(validateNpcAdjudicationResultJson(canonical(accepted)).diagnostics.some((value) => value.code === "NPC_AUTHORITY_ACCEPTED_TRANSITION_REQUIRED"));
  const rejected = resultFixture();
  rejected.decision = { status: "rejected", reason: "NPC_INTENT_ACTOR_UNAUTHORIZED" };
  assert(validateNpcAdjudicationResultJson(canonical(rejected)).diagnostics.some((value) => value.code === "NPC_AUTHORITY_REJECTED_TRANSITION_FORBIDDEN"));
  rejected.transition = null;
  assert(validateNpcAdjudicationResultJson(canonical(rejected)).diagnostics.some((value) => value.code === "NPC_AUTHORITY_REJECTED_SNAPSHOT_CHANGED"));
});

test("ledger detects revision, observation, previous hash, entry hash and head tampering", () => {
  const mutations = [
    ["WORLD_EVENT_LEDGER_REVISION_NONCONTIGUOUS", (value) => { value.entries[0].revision = 2; }],
    ["WORLD_EVENT_LEDGER_OBSERVED_STATE_MISMATCH", (value) => { value.entries[0].intent.observed.runtimeSnapshotSha256 = value.entries[0].afterSnapshotSha256; }],
    ["WORLD_EVENT_LEDGER_PREVIOUS_HASH_MISMATCH", (value) => { value.entries[0].previousEntrySha256 = value.entries[0].afterSnapshotSha256; }],
    ["WORLD_EVENT_LEDGER_ENTRY_HASH_MISMATCH", (value) => { value.entries[0].afterSnapshotSha256 = value.entries[0].beforeSnapshotSha256; }],
    ["WORLD_EVENT_LEDGER_HEAD_MISMATCH", (value) => { value.headSha256 = value.entries[0].afterSnapshotSha256; }],
  ];
  for (const [code, mutate] of mutations) {
    const ledger = ledgerFixture();
    mutate(ledger);
    assert(validateWorldEventLedgerJson(canonical(ledger)).diagnostics.some((value) => value.code === code), code);
  }
});

test("ledger binds the snapshot chain and accepted transition back to the intent", () => {
  const brokenChain = ledgerFixture();
  brokenChain.authority.initialSnapshotSha256 = brokenChain.entries[0].afterSnapshotSha256;
  assert(validateWorldEventLedgerJson(canonical(brokenChain)).diagnostics.some((value) => value.code === "WORLD_EVENT_LEDGER_SNAPSHOT_CHAIN_MISMATCH"));

  const wrongTransition = ledgerFixture();
  wrongTransition.entries[0].transition.actionId = "different-action";
  const { entrySha256: ignored, ...body } = wrongTransition.entries[0];
  wrongTransition.entries[0].entrySha256 = hashCanonical(body);
  wrongTransition.headSha256 = wrongTransition.entries[0].entrySha256;
  assert(validateWorldEventLedgerJson(canonical(wrongTransition)).diagnostics.some((value) => value.code === "WORLD_EVENT_LEDGER_TRANSITION_INTENT_MISMATCH"));

  const wrongStep = ledgerFixture();
  wrongStep.entries[0].transition.step = 2;
  const { entrySha256: ignoredAgain, ...stepBody } = wrongStep.entries[0];
  wrongStep.entries[0].entrySha256 = hashCanonical(stepBody);
  wrongStep.headSha256 = wrongStep.entries[0].entrySha256;
  assert(validateWorldEventLedgerJson(canonical(wrongStep)).diagnostics.some((value) => value.code === "WORLD_EVENT_LEDGER_TRANSITION_STEP_MISMATCH"));
});

test("ledger never permits an appended duplicate intent id", () => {
  const ledger = ledgerFixture();
  const duplicate = clone(acceptedEntryFixture());
  duplicate.revision = 2;
  duplicate.previousEntrySha256 = ledger.headSha256;
  const { entrySha256: ignored, ...body } = duplicate;
  duplicate.entrySha256 = hashCanonical(body);
  ledger.entries.push(duplicate);
  ledger.revision = 2;
  ledger.headSha256 = duplicate.entrySha256;
  assert(validateWorldEventLedgerJson(canonical(ledger)).diagnostics.some((value) => value.code === "WORLD_EVENT_LEDGER_INTENT_DUPLICATE"));

  duplicate.intent.actionId = "different-action";
  const { entrySha256: ignoredAgain, ...changedBody } = duplicate;
  duplicate.entrySha256 = hashCanonical(changedBody);
  ledger.headSha256 = duplicate.entrySha256;
  assert(validateWorldEventLedgerJson(canonical(ledger)).diagnostics.some((value) => value.code === "WORLD_EVENT_LEDGER_INTENT_ID_COLLISION"));
});

test("projection and replay reports bind zero revision to a null head and counts", () => {
  const projection = projectionFixture();
  projection.ledger.throughRevision = 0;
  assert.equal(validateDerivedProjectionManifestJson(canonical(projection)).diagnostics[0].code, "DERIVED_PROJECTION_LEDGER_HEAD_MISMATCH");
  const replay = replayFixture();
  replay.acceptedEntries = 0;
  assert(validateWorldEventLedgerReplayReportJson(canonical(replay)).diagnostics.some((value) => value.code === "WORLD_EVENT_LEDGER_REPLAY_COUNT_MISMATCH"));
});

test("adjudication result binds revision zero to a null head", () => {
  const result = resultFixture();
  result.revision = 0;
  assert(validateNpcAdjudicationResultJson(canonical(result)).diagnostics.some((value) => value.code === "NPC_ADJUDICATION_RESULT_HEAD_MISMATCH"));
});

test("canonical reports are deterministic for 20 runs", () => {
  for (const [, validate, fixture] of validators) {
    const input = canonical(fixture());
    const expected = canonical(validate(input));
    for (let index = 0; index < 20; index += 1) assert.equal(canonical(validate(input)), expected);
  }
});
