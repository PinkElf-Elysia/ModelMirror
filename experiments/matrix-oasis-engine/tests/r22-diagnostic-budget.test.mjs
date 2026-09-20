import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  realpath,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";

import {
  computeNpcCognitionApprovalHash,
  NPC_COGNITION_LIMITS,
} from "@matrix-oasis/npc-cognition-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  closeR22CallStore,
  openR22CallStore,
  openR22DiagnosticBudgetStore,
  recordR22PlannedCall,
  reserveR22CallBudget,
} from "../scripts/lib/r22-call-store.mjs";

const CANONICALIZATION = "matrix-oasis.canonical-json/1";
const OWNED_PREFIX = "mo-r22-db-";
const TEST_TMP_PARENT = tmpdir();
const HOST_RUN_ID = "host-run-one";
const FIVE_BUDGET_FIELDS = [
  "authoritySessionSha256",
  "callPlanSha256",
  "chargedMicrousd",
  "reservedMicrousd",
  "state",
];

function sha256Text(text) {
  return `sha256:${createHash("sha256").update(text, "utf8").digest("hex")}`;
}

function fixedSha(hex) {
  return `sha256:${hex.repeat(64).slice(0, 64)}`;
}

function stableSha(label) {
  return sha256Text(`synthetic-${label}`);
}

function samePath(left, right) {
  const normalize = (value) => path.resolve(value).replaceAll("\\", "/").toLowerCase();
  return normalize(left) === normalize(right);
}

function sessionManifest(hostRunId = HOST_RUN_ID) {
  return {
    format: "matrix-oasis.r22-cognition-session-manifest",
    formatVersion: "0.1.0",
    canonicalization: CANONICALIZATION,
    hostRunId,
    initialTimelineId: "timeline-session-one",
    providerMode: "offline-fake",
    sourceCurrentSha256: stableSha("session-source-current"),
    sourceDerivedBundleSha256: stableSha("session-source-derived-bundle"),
    sourceAuthorityManifestSha256: stableSha("session-source-authority-manifest"),
    cognitionPolicySha256: stableSha("session-cognition-policy"),
    implementationSha256: stableSha("session-implementation"),
    godotBinarySha256: stableSha("session-godot-binary"),
  };
}

function budgetEntry(index, state = "charged", chargedMicrousd = NPC_COGNITION_LIMITS.perCallMicrousd) {
  return {
    callPlanSha256: stableSha(`call-plan-${index}`),
    authoritySessionSha256: stableSha(`authority-session-${index}`),
    reservedMicrousd: NPC_COGNITION_LIMITS.perCallMicrousd,
    chargedMicrousd: state === "charged" ? chargedMicrousd : 0,
    state,
  };
}

function hostBudget(entries = [], hostRunId = HOST_RUN_ID) {
  return {
    format: "matrix-oasis.r22-host-budget",
    formatVersion: "0.1.0",
    canonicalization: CANONICALIZATION,
    hostRunId,
    limitMicrousd: NPC_COGNITION_LIMITS.perHostRunMicrousd,
    entries,
  };
}

async function cleanupOwnedRoot(temporaryRoot) {
  const resolved = path.resolve(temporaryRoot);
  const expectedParent = path.resolve(TEST_TMP_PARENT);
  assert.equal(path.basename(resolved).startsWith(OWNED_PREFIX), true);
  assert.equal(samePath(path.dirname(resolved), expectedParent), true);
  const stat = await lstat(resolved);
  assert.equal(stat.isDirectory(), true);
  assert.equal(stat.isSymbolicLink(), false);
  assert.equal(samePath(await realpath(resolved), resolved), true);
  await rm(resolved, { recursive: true, force: false });
}

async function fixture(t, options = {}) {
  await mkdir(TEST_TMP_PARENT, { recursive: true });
  const temporaryRoot = await mkdtemp(path.join(TEST_TMP_PARENT, OWNED_PREFIX));
  t.after(async () => { await cleanupOwnedRoot(temporaryRoot); });
  const cognitionRunRoot = path.join(temporaryRoot, "run-cognition");
  const manifestValue = options.manifestValue ?? sessionManifest();
  const budgetValue = options.budgetValue ?? hostBudget(options.entries ?? []);
  const manifestJson = options.manifestJson ?? canonicalizeJsonValue(manifestValue);
  const budgetJson = options.budgetJson ?? canonicalizeJsonValue(budgetValue);
  if (options.createRoot !== false) {
    await mkdir(cognitionRunRoot);
    if (options.writeManifest !== false) {
      await writeFile(path.join(cognitionRunRoot, "cognition-session-manifest.json"), manifestJson, { flag: "wx" });
    }
    if (options.writeBudget !== false) {
      await writeFile(path.join(cognitionRunRoot, "host-budget.json"), budgetJson, { flag: "wx" });
    }
  }
  return {
    temporaryRoot,
    cognitionRunRoot,
    manifestJson,
    budgetJson,
    manifestPath: path.join(cognitionRunRoot, "cognition-session-manifest.json"),
    budgetPath: path.join(cognitionRunRoot, "host-budget.json"),
    config: {
      temporaryRoot,
      cognitionRunRoot,
      hostRunId: HOST_RUN_ID,
      expectedSessionManifestSha256: sha256Text(manifestJson),
      expectedHostBudgetSha256: sha256Text(budgetJson),
    },
  };
}

function ordinaryConfig(f, suffix = "one") {
  return {
    temporaryRoot: f.temporaryRoot,
    cognitionRunRoot: f.cognitionRunRoot,
    hostRunId: HOST_RUN_ID,
    timelineId: `timeline-${suffix}`,
    authoritySessionSha256: stableSha(`ordinary-authority-${suffix}`),
    cognitionPolicySha256: stableSha("ordinary-policy"),
    initialLedgerPoint: {
      revision: 0,
      headSha256: null,
      runtimeSnapshotSha256: stableSha("ordinary-runtime-snapshot"),
    },
  };
}

function writerLeasePath(f) {
  return path.join(f.temporaryRoot, `.${path.basename(f.cognitionRunRoot)}.r22-writer-lock`);
}

function providerPayload(sequence = 1) {
  return canonicalizeJsonValue({
    background: false,
    input: `Synthetic input ${sequence}`,
    instructions: "Return one bounded dialogue proposal.",
    max_output_tokens: 512,
    model: "gpt-5.6-luna",
    reasoning: { effort: "none" },
    service_tier: "default",
    store: false,
    stream: false,
    text: {
      format: {
        name: "matrix_oasis_npc_dialogue_proposal",
        schema: { additionalProperties: false, properties: {}, required: [], type: "object" },
        strict: true,
        type: "json_schema",
      },
    },
    truncation: "disabled",
  });
}

function callPlan(providerRequestJson, sequence = 1) {
  const value = {
    format: "matrix-oasis.npc-cognition-call-plan",
    formatVersion: "0.1.0",
    canonicalization: CANONICALIZATION,
    turnId: `turn-${sequence}`,
    turnSha256: stableSha(`ordinary-turn-${sequence}`),
    contextSha256: stableSha(`ordinary-context-${sequence}`),
    candidateSha256: sha256Text("[]"),
    candidateChoices: [],
    providerPayloadSha256: sha256Text(providerRequestJson),
    responseSchemaSha256: stableSha(`ordinary-schema-${sequence}`),
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
    requestBytes: Buffer.byteLength(providerRequestJson, "utf8"),
    requestLimit: 1,
    retryLimit: 0,
    retentionPolicyVersion: "openai-api-data-controls-2026-09-03",
    retention: {
      store: false,
      zeroDataRetentionClaimed: false,
      abuseMonitoringMaxDays: 30,
      promptCachingPossible: true,
    },
    approval: { hash: fixedSha("0"), expiresAfterMs: 300000 },
  };
  value.approval.hash = computeNpcCognitionApprovalHash(value);
  return canonicalizeJsonValue(value);
}

function diagnosticKey(suffix) {
  const transactionSha256 = stableSha(`diagnostic-transaction-${suffix}`);
  return {
    authoritySessionSha256: sha256Text(canonicalizeJsonValue({
      purpose: "matrix-oasis.r22-diagnostic-budget/1",
      transactionSha256,
    })),
    callPlanSha256: stableSha(`diagnostic-call-plan-${suffix}`),
    transactionSha256,
  };
}

async function assertRejectCode(action, code) {
  await assert.rejects(action, (error) => {
    assert.equal(error?.code, code);
    assert.equal(error?.message, code);
    return true;
  });
}

async function assertNoTimelineOrLease(f) {
  const names = await readdir(f.cognitionRunRoot);
  assert.equal(names.includes("timelines"), false);
  assert.equal(names.some((name) => name.includes("writer")), false);
}

async function assertInvalidManifestHasNoSideEffects(f) {
  const beforeManifest = await readFile(f.manifestPath, "utf8");
  const beforeBudget = await readFile(f.budgetPath, "utf8");
  const beforeNames = (await readdir(f.cognitionRunRoot)).sort();
  assert.equal(f.config.expectedSessionManifestSha256, sha256Text(beforeManifest));
  let opened = null;
  let failure = null;
  try {
    opened = await openR22DiagnosticBudgetStore(f.config);
  } catch (error) {
    failure = error;
  }
  if (opened !== null) await opened.close();
  assert.equal(failure?.code, "R22_DIAGNOSTIC_SOURCE_INVALID");
  assert.equal(failure?.message, "R22_DIAGNOSTIC_SOURCE_INVALID");
  assert.equal(await readFile(f.manifestPath, "utf8"), beforeManifest);
  assert.equal(await readFile(f.budgetPath, "utf8"), beforeBudget);
  assert.deepEqual((await readdir(f.cognitionRunRoot)).sort(), beforeNames);
  await assertNoTimelineOrLease(f);
}

async function closeDiagnostic(store) {
  if (store !== null) await store.close();
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

async function boundedWait(promise, label) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((resolve, reject) => {
        timer = setTimeout(() => reject(new Error(`timed out waiting for ${label}`)), 2_000);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

test("missing cognition root is rejected without creating an account, timeline, or lease", async (t) => {
  const f = await fixture(t, { createRoot: false });
  await assertRejectCode(() => openR22DiagnosticBudgetStore(f.config), "R22_DIAGNOSTIC_SOURCE_INVALID");
  await assert.rejects(() => lstat(f.cognitionRunRoot), (error) => error?.code === "ENOENT");
  assert.deepEqual(await readdir(f.temporaryRoot), []);
});

test("missing session manifest is rejected before a writer lease is created", async (t) => {
  const f = await fixture(t, { writeManifest: false });
  await assertRejectCode(() => openR22DiagnosticBudgetStore(f.config), "R22_DIAGNOSTIC_SOURCE_INVALID");
  assert.deepEqual(await readdir(f.cognitionRunRoot), ["host-budget.json"]);
  await assertNoTimelineOrLease(f);
});

test("missing host budget is rejected before a writer lease is created", async (t) => {
  const f = await fixture(t, { writeBudget: false });
  await assertRejectCode(() => openR22DiagnosticBudgetStore(f.config), "R22_DIAGNOSTIC_SOURCE_INVALID");
  assert.deepEqual(await readdir(f.cognitionRunRoot), ["cognition-session-manifest.json"]);
  await assertNoTimelineOrLease(f);
});

test("missing or malformed identity hashes fail with one fixed source error", async (t) => {
  const f = await fixture(t);
  const cases = [
    Object.fromEntries(Object.entries(f.config).filter(([key]) => key !== "expectedSessionManifestSha256")),
    Object.fromEntries(Object.entries(f.config).filter(([key]) => key !== "expectedHostBudgetSha256")),
    { ...f.config, expectedSessionManifestSha256: "sha256:not-a-hash" },
    { ...f.config, expectedHostBudgetSha256: null },
  ];
  for (const config of cases) {
    await assertRejectCode(() => openR22DiagnosticBudgetStore(config), "R22_DIAGNOSTIC_SOURCE_INVALID");
  }
  await assertNoTimelineOrLease(f);
});

test("manifest must be canonical and bound to the configured host run", async (t) => {
  const nonCanonical = await fixture(t, { manifestJson: `${JSON.stringify(sessionManifest(), null, 2)}\n` });
  await assertRejectCode(() => openR22DiagnosticBudgetStore(nonCanonical.config), "R22_DIAGNOSTIC_SOURCE_INVALID");
  await assertNoTimelineOrLease(nonCanonical);

  const wrongHost = await fixture(t, { manifestValue: sessionManifest("host-run-other") });
  await assertRejectCode(() => openR22DiagnosticBudgetStore(wrongHost.config), "R22_DIAGNOSTIC_SOURCE_INVALID");
  await assertNoTimelineOrLease(wrongHost);
});

test("a re-signed manifest cannot bypass the exact twelve-field structure gate", async (t) => {
  const requiredFields = [
    "format",
    "formatVersion",
    "canonicalization",
    "hostRunId",
    "initialTimelineId",
    "providerMode",
    "sourceCurrentSha256",
    "sourceDerivedBundleSha256",
    "sourceAuthorityManifestSha256",
    "cognitionPolicySha256",
    "implementationSha256",
    "godotBinarySha256",
  ];
  const cases = requiredFields.map((field) => {
    const value = sessionManifest();
    delete value[field];
    return [`missing ${field}`, value];
  });
  cases.push(
    ["unknown field", { ...sessionManifest(), diagnosticApprovalSha256: stableSha("unknown") }],
    ["wrong scalar type", { ...sessionManifest(), formatVersion: 1 }],
    ["wrong container type", { ...sessionManifest(), canonicalization: [] }],
    ["invalid host identifier", { ...sessionManifest(), hostRunId: "host_run_one" }],
    ["invalid timeline identifier", { ...sessionManifest(), initialTimelineId: "timeline_session_one" }],
    ["unknown provider mode", { ...sessionManifest(), providerMode: "diagnostic-only" }],
    ["wrong provider type", { ...sessionManifest(), providerMode: false }],
  );
  for (const field of requiredFields.filter((field) => field.endsWith("Sha256"))) {
    cases.push([`invalid ${field}`, { ...sessionManifest(), [field]: "sha256:not-a-hash" }]);
  }

  for (const [label, manifestValue] of cases) {
    await t.test(label, async (child) => {
      const f = await fixture(child, { manifestValue });
      await assertInvalidManifestHasNoSideEffects(f);
    });
  }
});

test("manifest and budget byte hashes are both mandatory source identities", async (t) => {
  const f = await fixture(t);
  for (const config of [
    { ...f.config, expectedSessionManifestSha256: fixedSha("e") },
    { ...f.config, expectedHostBudgetSha256: fixedSha("f") },
  ]) {
    await assertRejectCode(() => openR22DiagnosticBudgetStore(config), "R22_DIAGNOSTIC_SOURCE_INVALID");
  }
  await assertNoTimelineOrLease(f);
});

test("an invalid pre-existing five-field budget is rejected rather than repaired", async (t) => {
  const invalid = budgetEntry(1, "released");
  invalid.chargedMicrousd = 1;
  const f = await fixture(t, { entries: [invalid] });
  const before = await readFile(f.budgetPath, "utf8");
  await assertRejectCode(() => openR22DiagnosticBudgetStore(f.config), "R22_STORE_HOST_BUDGET_INVALID");
  assert.equal(await readFile(f.budgetPath, "utf8"), before);
  await assertNoTimelineOrLease(f);
});

test("open, identity, inspect, revalidate, and close are frozen and leave source bytes unchanged", async (t) => {
  const f = await fixture(t);
  const beforeManifest = await readFile(f.manifestPath, "utf8");
  const beforeBudget = await readFile(f.budgetPath, "utf8");
  const store = await openR22DiagnosticBudgetStore(f.config);
  try {
    assert.equal(Object.isFrozen(store), true);
    assert.equal(Object.isFrozen(store.identity), true);
    assert.equal(store.identity.hostRunId, HOST_RUN_ID);
    assert.equal(store.identity.providerMode, "offline-fake");
    assert.equal(store.identity.sessionManifestSha256, f.config.expectedSessionManifestSha256);
    assert.match(store.identity.processEpochSha256, /^sha256:[0-9a-f]{64}$/u);
    assert.equal(await store.revalidate(), true);
    const inspected = await store.inspect();
    assert.equal(Object.isFrozen(inspected), true);
    assert.deepEqual(inspected, { canonicalBudgetJson: beforeBudget, charged: 0, reserved: 0 });
    assert.equal(await readFile(f.manifestPath, "utf8"), beforeManifest);
    assert.equal(await readFile(f.budgetPath, "utf8"), beforeBudget);
    assert.equal((await readdir(f.cognitionRunRoot)).includes("timelines"), false);
  } finally {
    await closeDiagnostic(store);
  }
  await assertNoTimelineOrLease(f);
  assert.equal(await readFile(f.budgetPath, "utf8"), beforeBudget);
  await assertRejectCode(() => store.inspect(), "R22_DIAGNOSTIC_STORE_UNAVAILABLE");
});

test("an ordinary writer excludes a diagnostic writer on the same cognition root", async (t) => {
  const f = await fixture(t);
  const ordinary = await openR22CallStore(ordinaryConfig(f));
  try {
    await assertRejectCode(() => openR22DiagnosticBudgetStore(f.config), "R22_CALL_IN_FLIGHT");
  } finally {
    await closeR22CallStore(ordinary);
  }
});

test("a diagnostic writer excludes an ordinary writer before any timeline is created", async (t) => {
  const f = await fixture(t);
  const diagnostic = await openR22DiagnosticBudgetStore(f.config);
  try {
    await assertRejectCode(() => openR22CallStore(ordinaryConfig(f)), "R22_CALL_IN_FLIGHT");
    assert.equal((await readdir(f.cognitionRunRoot)).includes("timelines"), false);
  } finally {
    await diagnostic.close();
  }
});

test("pre-existing charged and reserved amounts and canonical bytes are preserved", async (t) => {
  const entries = [budgetEntry(1, "charged", 7_000), budgetEntry(2, "reserved")]
    .sort((left, right) => left.authoritySessionSha256.localeCompare(right.authoritySessionSha256));
  const f = await fixture(t, { entries });
  const before = await readFile(f.budgetPath, "utf8");
  const store = await openR22DiagnosticBudgetStore(f.config);
  try {
    const inspected = await store.inspect();
    assert.deepEqual(inspected, {
      canonicalBudgetJson: before,
      charged: 7_000,
      reserved: NPC_COGNITION_LIMITS.perCallMicrousd,
    });
    assert.equal(await readFile(f.budgetPath, "utf8"), before);
  } finally {
    await store.close();
  }
});

test("reserve appends one sorted historical five-field entry and never creates a timeline", async (t) => {
  const prior = budgetEntry(9, "released");
  const f = await fixture(t, { entries: [prior] });
  const store = await openR22DiagnosticBudgetStore(f.config);
  try {
    const key = diagnosticKey("reserve");
    assert.deepEqual(await store.reserve(key), { ok: true, reservedMicrousd: NPC_COGNITION_LIMITS.perCallMicrousd });
    const inspected = await store.inspect();
    const parsed = JSON.parse(inspected.canonicalBudgetJson);
    const reserved = parsed.entries.find((entry) => entry.callPlanSha256 === key.callPlanSha256);
    assert.equal(parsed.entries.length, 2);
    assert.equal(reserved.authoritySessionSha256, key.authoritySessionSha256);
    assert.notEqual(reserved.authoritySessionSha256, key.transactionSha256);
    assert.equal(inspected.charged, 0);
    assert.equal(inspected.reserved, NPC_COGNITION_LIMITS.perCallMicrousd);
    assert.deepEqual(parsed.entries.map((entry) => entry.authoritySessionSha256),
      [...parsed.entries].map((entry) => entry.authoritySessionSha256).sort());
    for (const entry of parsed.entries) assert.deepEqual(Object.keys(entry).sort(), FIVE_BUDGET_FIELDS);
    assert.equal((await readdir(f.cognitionRunRoot)).includes("timelines"), false);
  } finally {
    await store.close();
  }
});

test("an existing reservation blocks another reserve and preserves exact budget bytes", async (t) => {
  const f = await fixture(t, { entries: [budgetEntry(1, "reserved")] });
  const before = await readFile(f.budgetPath, "utf8");
  const store = await openR22DiagnosticBudgetStore(f.config);
  try {
    const result = await store.reserve(diagnosticKey("blocked"));
    assert.deepEqual(result, { ok: false, diagnosticCode: "R22_CALL_IN_FLIGHT" });
    assert.equal(canonicalizeJsonValue(result), '{"diagnosticCode":"R22_CALL_IN_FLIGHT","ok":false}');
    assert.equal(await readFile(f.budgetPath, "utf8"), before);
  } finally {
    await store.close();
  }
});

test("the 100-entry host limit precedes in-flight and leaves the full budget byte-identical", async (t) => {
  const entries = Array.from({ length: NPC_COGNITION_LIMITS.callsPerHostRun }, (_, index) =>
    budgetEntry(index, index === 0 ? "reserved" : "charged"));
  const f = await fixture(t, { entries });
  const before = await readFile(f.budgetPath, "utf8");
  const store = await openR22DiagnosticBudgetStore(f.config);
  try {
    const result = await store.reserve(diagnosticKey("limit"));
    assert.deepEqual(result, { ok: false, diagnosticCode: "R22_BUDGET_EXHAUSTED" });
    assert.equal(canonicalizeJsonValue(result), '{"diagnosticCode":"R22_BUDGET_EXHAUSTED","ok":false}');
    assert.equal(await readFile(f.budgetPath, "utf8"), before);
  } finally {
    await store.close();
  }
});

test("two concurrent reserves serialize to one durable write and one in-flight result", async (t) => {
  const f = await fixture(t);
  const store = await openR22DiagnosticBudgetStore(f.config);
  try {
    const [first, second] = await Promise.all([
      store.reserve(diagnosticKey("concurrent-a")),
      store.reserve(diagnosticKey("concurrent-b")),
    ]);
    assert.deepEqual(first, { ok: true, reservedMicrousd: NPC_COGNITION_LIMITS.perCallMicrousd });
    assert.deepEqual(second, { ok: false, diagnosticCode: "R22_CALL_IN_FLIGHT" });
    const inspected = await store.inspect();
    assert.equal(JSON.parse(inspected.canonicalBudgetJson).entries.length, 1);
    assert.equal(inspected.reserved, NPC_COGNITION_LIMITS.perCallMicrousd);
  } finally {
    await store.close();
  }
});

test("close synchronously rejects late operations without writing and releases the ordinary writer lock", async (t) => {
  const f = await fixture(t);
  const before = await readFile(f.budgetPath, "utf8");
  const store = await openR22DiagnosticBudgetStore(f.config);
  const key = diagnosticKey("late-close");
  const closing = store.close();
  await Promise.all([
    assertRejectCode(() => store.inspect(), "R22_DIAGNOSTIC_STORE_UNAVAILABLE"),
    assertRejectCode(() => store.revalidate(), "R22_DIAGNOSTIC_STORE_UNAVAILABLE"),
    assertRejectCode(() => store.reserve(key), "R22_DIAGNOSTIC_STORE_UNAVAILABLE"),
    assertRejectCode(() => store.settle(key, false), "R22_DIAGNOSTIC_STORE_UNAVAILABLE"),
  ]);
  assert.equal(await readFile(f.budgetPath, "utf8"), before);
  assert.deepEqual(await closing, { ok: true });

  const ordinary = await openR22CallStore(ordinaryConfig(f, "after-diagnostic-close"));
  await closeR22CallStore(ordinary);
});

test("close waits for an already queued reserve to persist its historical five-field entry", async (t) => {
  const f = await fixture(t);
  const before = await readFile(f.budgetPath, "utf8");
  const renameReached = deferred();
  const allowRename = deferred();
  let held = false;
  const operations = {
    async rename(source, target) {
      if (!held && samePath(target, f.budgetPath)) {
        held = true;
        renameReached.resolve();
        await allowRename.promise;
      }
      return rename(source, target);
    },
  };
  const store = await openR22DiagnosticBudgetStore(f.config, operations);
  const key = diagnosticKey("queued-before-close");
  let reservePromise = null;
  let closePromise = null;
  try {
    reservePromise = store.reserve(key);
    await boundedWait(renameReached.promise, "queued diagnostic budget rename");
    closePromise = store.close();
    await assertRejectCode(() => store.reserve(diagnosticKey("late-while-queued")), "R22_DIAGNOSTIC_STORE_UNAVAILABLE");
    assert.equal(await readFile(f.budgetPath, "utf8"), before);
    await assertRejectCode(() => openR22CallStore(ordinaryConfig(f, "while-diagnostic-closing")), "R22_CALL_IN_FLIGHT");

    allowRename.resolve();
    assert.deepEqual(await reservePromise, { ok: true, reservedMicrousd: NPC_COGNITION_LIMITS.perCallMicrousd });
    assert.deepEqual(await closePromise, { ok: true });
  } finally {
    allowRename.resolve();
    await Promise.allSettled([reservePromise, closePromise].filter((value) => value !== null));
  }

  const persisted = JSON.parse(await readFile(f.budgetPath, "utf8"));
  assert.deepEqual(Object.keys(persisted).sort(), [
    "canonicalization",
    "entries",
    "format",
    "formatVersion",
    "hostRunId",
    "limitMicrousd",
  ]);
  assert.equal(persisted.entries.length, 1);
  assert.deepEqual(Object.keys(persisted.entries[0]).sort(), FIVE_BUDGET_FIELDS);
  assert.deepEqual({
    authoritySessionSha256: persisted.entries[0].authoritySessionSha256,
    callPlanSha256: persisted.entries[0].callPlanSha256,
    state: persisted.entries[0].state,
  }, {
    authoritySessionSha256: key.authoritySessionSha256,
    callPlanSha256: key.callPlanSha256,
    state: "reserved",
  });

  const ordinary = await openR22CallStore(ordinaryConfig(f, "after-queued-close"));
  await closeR22CallStore(ordinary);
});

test("a pre-delete EPERM shares one failed close attempt and permits close-only retry", async (t) => {
  const f = await fixture(t);
  const before = await readFile(f.budgetPath, "utf8");
  const leasePath = writerLeasePath(f);
  let leaseRemoveCalls = 0;
  const operations = {
    async rm(candidate, options) {
      if (samePath(candidate, leasePath)) {
        leaseRemoveCalls += 1;
        if (leaseRemoveCalls === 1) {
          const error = new Error("synthetic pre-delete denial");
          error.code = "EPERM";
          throw error;
        }
      }
      return rm(candidate, options);
    },
  };
  const store = await openR22DiagnosticBudgetStore(f.config, operations);
  const firstClose = store.close();
  const sharedClose = store.close();
  assert.equal(sharedClose, firstClose);
  await assertRejectCode(() => firstClose, "R22_STORE_WRITE_FAILED");
  assert.equal(typeof await readFile(leasePath, "utf8"), "string");
  await Promise.all([
    assertRejectCode(() => store.inspect(), "R22_DIAGNOSTIC_STORE_UNAVAILABLE"),
    assertRejectCode(() => store.reserve(diagnosticKey("after-close-cleanup-failure")), "R22_DIAGNOSTIC_STORE_UNAVAILABLE"),
  ]);
  assert.equal(await readFile(f.budgetPath, "utf8"), before);
  assert.deepEqual(await store.close(), { ok: true });
  assert.equal(leaseRemoveCalls, 2);
  await assert.rejects(() => lstat(leasePath), (error) => error?.code === "ENOENT");

  const ordinary = await openR22CallStore(ordinaryConfig(f, "after-close-retry"));
  await closeR22CallStore(ordinary);
});

test("close reconciles an rm error after its own lease was already deleted", async (t) => {
  const f = await fixture(t);
  const before = await readFile(f.budgetPath, "utf8");
  const leasePath = writerLeasePath(f);
  let leaseRemoveCalls = 0;
  const operations = {
    async rm(candidate, options) {
      if (samePath(candidate, leasePath)) {
        leaseRemoveCalls += 1;
        await rm(candidate, options);
        const error = new Error("synthetic post-delete report failure");
        error.code = "EPERM";
        throw error;
      }
      return rm(candidate, options);
    },
  };
  const store = await openR22DiagnosticBudgetStore(f.config, operations);
  assert.deepEqual(await store.close(), { ok: true });
  assert.equal(leaseRemoveCalls, 1);
  assert.equal(await readFile(f.budgetPath, "utf8"), before);
  await assert.rejects(() => lstat(leasePath), (error) => error?.code === "ENOENT");
  await assertRejectCode(() => store.revalidate(), "R22_DIAGNOSTIC_STORE_UNAVAILABLE");
  assert.deepEqual(await store.close(), { ok: true });
  assert.equal(leaseRemoveCalls, 1);

  const ordinary = await openR22CallStore(ordinaryConfig(f, "after-reconciled-close"));
  await closeR22CallStore(ordinary);
});

test("close never deletes a replacement lease after rm reports failure", async (t) => {
  const f = await fixture(t);
  const before = await readFile(f.budgetPath, "utf8");
  const leasePath = writerLeasePath(f);
  const replacement = canonicalizeJsonValue({
    format: "matrix-oasis.r22-writer-lock",
    formatVersion: "0.1.0",
    processId: 999_999,
    processEpochSha256: stableSha("replacement-writer-epoch"),
    rootSha256: sha256Text(path.resolve(f.cognitionRunRoot).toLowerCase()),
  });
  let leaseRemoveCalls = 0;
  const operations = {
    async rm(candidate, options) {
      if (samePath(candidate, leasePath)) {
        leaseRemoveCalls += 1;
        await rm(candidate, options);
        await writeFile(candidate, replacement, { flag: "wx" });
        const error = new Error("synthetic lease replacement race");
        error.code = "EPERM";
        throw error;
      }
      return rm(candidate, options);
    },
  };
  const store = await openR22DiagnosticBudgetStore(f.config, operations);
  await assertRejectCode(() => store.close(), "R22_STORE_WRITER_LOCK_LOST");
  assert.equal(await readFile(leasePath, "utf8"), replacement);
  await assertRejectCode(() => store.inspect(), "R22_DIAGNOSTIC_STORE_UNAVAILABLE");
  await assertRejectCode(() => store.close(), "R22_STORE_WRITER_LOCK_LOST");
  assert.equal(leaseRemoveCalls, 1);
  assert.equal(await readFile(leasePath, "utf8"), replacement);
  assert.equal(await readFile(f.budgetPath, "utf8"), before);
});

test("a forged ordinary authority cannot reserve diagnostic budget and permanently poisons the facade", async (t) => {
  const f = await fixture(t);
  const store = await openR22DiagnosticBudgetStore(f.config);
  try {
    const before = await readFile(f.budgetPath, "utf8");
    const key = diagnosticKey("forged-reserve");
    const forged = { ...key, authoritySessionSha256: stableSha("ordinary-authority-forgery") };
    await assertRejectCode(() => store.reserve(forged), "R22_DIAGNOSTIC_BUDGET_KEY_INVALID");
    assert.equal(await readFile(f.budgetPath, "utf8"), before);
    await assertRejectCode(() => store.inspect(), "R22_DIAGNOSTIC_STORE_UNAVAILABLE");
  } finally {
    await store.close();
  }
});

test("a forged ordinary authority cannot settle a diagnostic reservation or alter its bytes", async (t) => {
  const f = await fixture(t);
  const store = await openR22DiagnosticBudgetStore(f.config);
  try {
    const key = diagnosticKey("forged-settle");
    await store.reserve(key);
    const before = await readFile(f.budgetPath, "utf8");
    const forged = { ...key, authoritySessionSha256: stableSha("ordinary-settle-forgery") };
    await assertRejectCode(() => store.settle(forged, true), "R22_DIAGNOSTIC_BUDGET_KEY_INVALID");
    assert.equal(await readFile(f.budgetPath, "utf8"), before);
    const entry = JSON.parse(before).entries[0];
    assert.deepEqual({ authoritySessionSha256: entry.authoritySessionSha256, state: entry.state, chargedMicrousd: entry.chargedMicrousd }, {
      authoritySessionSha256: key.authoritySessionSha256,
      state: "reserved",
      chargedMicrousd: 0,
    });
    await assertRejectCode(() => store.revalidate(), "R22_DIAGNOSTIC_STORE_UNAVAILABLE");
  } finally {
    await store.close();
  }
});

test("a released identity cannot be reserved twice or produce a sixth budget field", async (t) => {
  const f = await fixture(t);
  const store = await openR22DiagnosticBudgetStore(f.config);
  const key = diagnosticKey("duplicate");
  try {
    await store.reserve(key);
    await store.settle(key, false);
    const before = await readFile(f.budgetPath, "utf8");
    await assertRejectCode(() => store.reserve(key), "R22_STORE_BUDGET_ENTRY_CONFLICT");
    assert.equal(await readFile(f.budgetPath, "utf8"), before);
    const parsed = JSON.parse(before);
    assert.equal(parsed.entries.length, 1);
    assert.deepEqual(Object.keys(parsed.entries[0]).sort(), FIVE_BUDGET_FIELDS);
  } finally {
    await store.close();
  }
});

test("settle(false) releases an undispatched reservation without charging it", async (t) => {
  const f = await fixture(t);
  const store = await openR22DiagnosticBudgetStore(f.config);
  const key = diagnosticKey("released");
  try {
    await store.reserve(key);
    assert.deepEqual(await store.settle(key, false), { ok: true });
    const inspected = await store.inspect();
    const entry = JSON.parse(inspected.canonicalBudgetJson).entries[0];
    assert.deepEqual({ state: entry.state, chargedMicrousd: entry.chargedMicrousd },
      { state: "released", chargedMicrousd: 0 });
    assert.equal(inspected.charged, 0);
    assert.equal(inspected.reserved, 0);
  } finally {
    await store.close();
  }
});

test("a dispatched reservation charges fully, is idempotent, and can never be released", async (t) => {
  const f = await fixture(t);
  const store = await openR22DiagnosticBudgetStore(f.config);
  const key = diagnosticKey("charged");
  try {
    await store.reserve(key);
    assert.deepEqual(await store.settle(key, true), { ok: true });
    const chargedBytes = await readFile(f.budgetPath, "utf8");
    assert.deepEqual(await store.settle(key, true), { ok: true });
    assert.equal(await readFile(f.budgetPath, "utf8"), chargedBytes);
    const entry = JSON.parse(chargedBytes).entries[0];
    assert.deepEqual({ state: entry.state, chargedMicrousd: entry.chargedMicrousd }, {
      state: "charged",
      chargedMicrousd: NPC_COGNITION_LIMITS.perCallMicrousd,
    });
    await assertRejectCode(() => store.settle(key, false), "R22_STORE_BUDGET_ENTRY_CONFLICT");
    assert.equal(await readFile(f.budgetPath, "utf8"), chargedBytes);
  } finally {
    await store.close();
  }
});

test("byte-identical budget replacement is detected by file identity on every later read", async (t) => {
  const f = await fixture(t);
  const store = await openR22DiagnosticBudgetStore(f.config);
  try {
    const original = await readFile(f.budgetPath, "utf8");
    const displaced = `${f.budgetPath}.displaced`;
    await rename(f.budgetPath, displaced);
    await writeFile(f.budgetPath, original, { flag: "wx" });
    await assertRejectCode(() => store.revalidate(), "R22_DIAGNOSTIC_SOURCE_INVALID");
    await assertRejectCode(() => store.inspect(), "R22_DIAGNOSTIC_STORE_UNAVAILABLE");
    assert.equal(await readFile(f.budgetPath, "utf8"), original);
  } finally {
    await store.close();
  }
});

test("in-place budget content drift rejects every later operation without writing", async (t) => {
  const f = await fixture(t);
  const store = await openR22DiagnosticBudgetStore(f.config);
  try {
    const drifted = canonicalizeJsonValue(hostBudget([budgetEntry(88, "released")]));
    await writeFile(f.budgetPath, drifted);
    await assertRejectCode(() => store.revalidate(), "R22_DIAGNOSTIC_SOURCE_INVALID");
    await assertRejectCode(() => store.reserve(diagnosticKey("after-drift")), "R22_DIAGNOSTIC_STORE_UNAVAILABLE");
    assert.equal(await readFile(f.budgetPath, "utf8"), drifted);
  } finally {
    await store.close();
  }
});

test("ordinary reserve keeps its historical diagnostic precedence and canonical bytes", async (t) => {
  const inFlightFixture = await fixture(t, { entries: [budgetEntry(1, "reserved")] });
  const inFlightStore = await openR22CallStore(ordinaryConfig(inFlightFixture, "in-flight"));
  try {
    const payloadJson = providerPayload(1);
    const planned = await recordR22PlannedCall(inFlightStore, {
      sequence: 1,
      turnId: "turn-1",
      actorEntityId: "actor-one",
      callPlanJson: callPlan(payloadJson, 1),
      beforeLedgerPoint: ordinaryConfig(inFlightFixture, "in-flight").initialLedgerPoint,
    });
    const budgetBefore = await readFile(inFlightFixture.budgetPath, "utf8");
    const result = await reserveR22CallBudget(inFlightStore, planned.callPlanSha256);
    assert.equal(canonicalizeJsonValue(result), '{"diagnosticCode":"R22_CALL_IN_FLIGHT","ok":false}');
    assert.equal(await readFile(inFlightFixture.budgetPath, "utf8"), budgetBefore);
  } finally {
    await closeR22CallStore(inFlightStore);
  }

  const fullEntries = Array.from({ length: NPC_COGNITION_LIMITS.callsPerHostRun }, (_, index) =>
    budgetEntry(index, index === 0 ? "reserved" : "charged"));
  const exhaustedFixture = await fixture(t, { entries: fullEntries });
  const exhaustedStore = await openR22CallStore(ordinaryConfig(exhaustedFixture, "exhausted"));
  try {
    const payloadJson = providerPayload(1);
    const planned = await recordR22PlannedCall(exhaustedStore, {
      sequence: 1,
      turnId: "turn-1",
      actorEntityId: "actor-one",
      callPlanJson: callPlan(payloadJson, 1),
      beforeLedgerPoint: ordinaryConfig(exhaustedFixture, "exhausted").initialLedgerPoint,
    });
    const budgetBefore = await readFile(exhaustedFixture.budgetPath, "utf8");
    const result = await reserveR22CallBudget(exhaustedStore, planned.callPlanSha256);
    assert.equal(canonicalizeJsonValue(result), '{"diagnosticCode":"R22_BUDGET_EXHAUSTED","ok":false}');
    assert.equal(await readFile(exhaustedFixture.budgetPath, "utf8"), budgetBefore);
  } finally {
    await closeR22CallStore(exhaustedStore);
  }
});
