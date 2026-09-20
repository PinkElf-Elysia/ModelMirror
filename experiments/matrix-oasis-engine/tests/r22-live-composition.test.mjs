import assert from "node:assert/strict";
import { access, lstat, readFile, readdir, realpath, rm, unlink, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";
import { createR22LiveComposition } from "../scripts/lib/r22-live-composition.mjs";
import { handleR22LoopbackRequestAsync } from "../scripts/lib/r22-host-core.mjs";
import { verifyR22QualifiedSourcePair } from "../scripts/lib/r22-qualification-core.mjs";
import { canonicalText, sha256 } from "../scripts/lib/r22-cli-core.mjs";

const TEMP_ROOT = path.join(path.parse(fileURLToPath(import.meta.url)).root, "tmp");
const CASE = Object.freeze({
  caseId: "neutral-cache",
  sourceKind: "qualified-cache",
  npcRunRoot: path.join(TEMP_ROOT, "matrix-oasis-r20-neutral-r10-npc"),
  derivedStateRoot: path.join(TEMP_ROOT, "matrix-oasis-r21-real-20260901-b-case-1"),
  expectedNpcCurrentSha256: "sha256:187221f904f7eee7fc97c0194fe16f075365b1b489050ac00a04be0c4da91135",
  expectedDerivedStateBundleSha256: "sha256:7cfc9b43e6318076b57ee97e34a759a9b9a52c31377a9beaaf5d8d829d1a008b",
});
const SHA_A = `sha256:${"a".repeat(64)}`;
const SHA_B = `sha256:${"b".repeat(64)}`;

function request(token, method, url, body) {
  return {
    remoteAddress: "127.0.0.1", method, url,
    headers: { authorization: `Bearer ${token}`, ...(method === "POST" ? { "content-type": "application/json" } : {}) },
    body: method === "POST" ? JSON.stringify(body ?? {}) : "",
  };
}

async function filesBelow(root) {
  const output = [];
  async function visit(directory) {
    for (const item of await readdir(directory, { withFileTypes: true })) {
      const candidate = path.join(directory, item.name);
      if (item.isDirectory()) await visit(candidate); else output.push(candidate);
    }
  }
  await visit(root);
  return output;
}

async function inspectOwnedDirectory(target, namePattern) {
  const resolved = path.resolve(target);
  assert.equal(path.dirname(resolved), path.resolve(TEMP_ROOT));
  assert.match(path.basename(resolved), namePattern);
  const stat = await lstat(resolved, { bigint: true });
  assert.equal(stat.isDirectory(), true);
  assert.equal(stat.isSymbolicLink(), false);
  assert.equal(await realpath(resolved), resolved);
  return { dev: String(stat.dev), ino: String(stat.ino) };
}

async function removeOwnedDirectory(target, namePattern, expectedIdentity = null) {
  const resolved = path.resolve(target);
  let identity;
  try {
    identity = await inspectOwnedDirectory(resolved, namePattern);
  } catch (error) {
    if (error?.code === "ENOENT") return;
    throw error;
  }
  if (expectedIdentity !== null) assert.deepEqual(identity, expectedIdentity);
  await rm(resolved, { recursive: true, force: true });
}

async function physicalOutputs() {
  return (await readdir(TEMP_ROOT, { withFileTypes: true }))
    .filter((item) => item.isDirectory() && item.name.startsWith("matrix-oasis-r22-physical-"))
    .map((item) => item.name).sort();
}

async function route(live, method, url, body, expected = 200) {
  const response = await handleR22LoopbackRequestAsync(live.controller, request(live.sessionToken, method, url, body));
  assert.equal(response.statusCode, expected, response.body);
  return JSON.parse(response.body);
}

async function settled(live, turnId, {
  readStatus = () => route(live, "GET", `/v1/cognition/status/${turnId}`),
  now = () => performance.now(),
  pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
} = {}) {
  // This is a test completion budget, not a product timeout. Observe only the
  // public status route; never close the live controller to force a result.
  const deadline = now() + 45_000;
  while (now() < deadline) {
    const response = await readStatus();
    if (now() >= deadline) break;
    if (response.status !== "dispatching") return response;
    await pause(Math.min(25, deadline - now()));
  }
  assert.fail("offline fake provider exceeded the 45-second completion deadline");
}

test("composition status wait has a monotonic total deadline and cannot promote pending or failed outcomes", async () => {
  let elapsed = 0, reads = 0;
  const outcome = Object.freeze({ status: "dialogue_only", dialogueText: "Local fixture." });
  const result = await settled(null, "turn-fixture", {
    now: () => elapsed,
    pause: async (milliseconds) => { elapsed += milliseconds; },
    readStatus: async () => { reads += 1; return reads <= 201 ? { status: "dispatching" } : outcome; },
  });
  assert.strictEqual(result, outcome);
  assert.equal(reads, 202);
  assert.equal(elapsed, 5025);

  elapsed = 0; reads = 0;
  await assert.rejects(settled(null, "turn-fixture", {
    now: () => elapsed,
    pause: async (milliseconds) => { elapsed += milliseconds; },
    readStatus: async () => { reads += 1; return { status: "dispatching" }; },
  }), /exceeded the 45-second completion deadline/u);
  assert.equal(elapsed, 45_000);
  assert.equal(reads, 1800);

  for (const completedAt of [44_999, 45_000]) {
    elapsed = 0;
    const pending = settled(null, "turn-fixture", {
      now: () => elapsed,
      readStatus: async () => { elapsed = completedAt; return outcome; },
    });
    if (completedAt === 44_999) assert.strictEqual(await pending, outcome);
    else await assert.rejects(pending, /exceeded the 45-second completion deadline/u);
  }
  const fallback = Object.freeze({ status: "fallback", fallbackCode: "local-fixture" });
  assert.strictEqual(await settled(null, "turn-fixture", { readStatus: async () => fallback }), fallback);
  const failure = new Error("local status fixture failure");
  await assert.rejects(settled(null, "turn-fixture", { readStatus: async () => { throw failure; } }),
    (error) => error === failure);
});

test("official file source stays unread before approval and never enters persisted evidence", async (t) => {
  if (process.platform !== "win32") return t.skip("fixed Windows qualification cache is unavailable");
  try { await access(path.join(CASE.npcRunRoot, "npc-current.json")); await access(path.join(CASE.derivedStateRoot, "npc-derived-state-bundle.json")); }
  catch { return t.skip("fixed Windows qualification cache is unavailable"); }
  const suffix = randomUUID(), base = path.join(TEMP_ROOT, `matrix-oasis-r22-live-${suffix}`);
  const npcRunRoot = `${base}-npc`, cognitionRunRoot = `${base}-cognition`;
  const credentialFile = path.join(TEMP_ROOT, `matrix-oasis-r22-test-credential-${suffix}.txt`);
  const fakeCredential = ["sk", "proj", "offline-fixture", suffix].join("-");
  await writeFile(credentialFile, fakeCredential, { flag: "wx" });
  const ownedCredential = await lstat(credentialFile, { bigint: true });
  t.after(async () => {
    const current = await lstat(credentialFile, { bigint: true });
    assert.equal(current.dev, ownedCredential.dev); assert.equal(current.ino, ownedCredential.ino); await unlink(credentialFile);
    const names = /^matrix-oasis-r22-live-[0-9a-f-]+-(?:npc|cognition)$/u;
    await removeOwnedDirectory(npcRunRoot, names); await removeOwnedDirectory(cognitionRunRoot, names);
  });
  const source = await verifyR22QualifiedSourcePair(CASE, TEMP_ROOT);
  const common = { source, implementationSha256: SHA_A, godotBinarySha256: SHA_B, temporaryRoot: TEMP_ROOT, npcRunRoot, cognitionRunRoot, credentialFile };
  await assert.rejects(createR22LiveComposition({ ...common, providerMode: "offline-fake" }), /R22_LIVE_CONFIGURATION_INVALID/u);
  await assert.rejects(createR22LiveComposition({ ...common, providerMode: "official-once", resume: true }), /R22_LIVE_CONFIGURATION_INVALID/u);
  let fetches = 0;
  // A local protocol fixture only: no upstream request or real key is used.
  t.mock.method(globalThis, "fetch", async (url, options) => {
    fetches += 1;
    assert.equal(url, "https://api.openai.com/v1/responses");
    assert.equal(options.headers.authorization === `Bearer ${fakeCredential}`, true);
    assert.equal(options.redirect, "error");
    const payload = JSON.parse(options.body);
    assert.equal(payload.service_tier, "default");
    // Stay well inside the fixed 30-second Provider contract, but exceed the
    // old test helper's accidental 200 x 5 ms completion budget.
    await new Promise((resolve) => setTimeout(resolve, 1_500));
    return new Response(JSON.stringify({ id: "discarded-response", object: "response", status: "completed", error: null, incomplete_details: null,
      model: payload.model, service_tier: "default", output: [{ id: "discarded-message", type: "message", status: "completed", role: "assistant", content: [{ type: "output_text", annotations: [],
        text: JSON.stringify({ contextSha256: sha256(payload.input), dialogueText: "Offline credential integration fixture.", actionChoiceId: null }) }] }],
      usage: { input_tokens: 200, input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 }, output_tokens: 50,
        output_tokens_details: { reasoning_tokens: 0 }, total_tokens: 250 } }), { status: 200, headers: { "content-type": "application/json" } });
  });
  const live = await createR22LiveComposition({ ...common, providerMode: "official-once" });
  try {
    const actorEntityId = JSON.parse(source.npcEntityBindingJson).bindings[0].actorEntityId;
    assert.equal(live.exportState().sourceCredentialReads, 0); assert.equal(fetches, 0);
    const declined = await route(live, "POST", "/v1/cognition/turn", { actorEntityId, playerText: "Decline the credential fixture." });
    await route(live, "POST", "/v1/cognition/decline", { turnId: declined.turnId, approvalHash: declined.approvalHash });
    assert.equal(live.exportState().sourceCredentialReads, 0); assert.equal(fetches, 0);
    const approved = await route(live, "POST", "/v1/cognition/turn", { actorEntityId, playerText: "Approve the credential fixture." });
    assert.equal(live.exportState().sourceCredentialReads, 0); assert.equal(fetches, 0);
    await route(live, "POST", "/v1/cognition/approve", { turnId: approved.turnId, approvalHash: approved.approvalHash });
    const outcome = await settled(live, approved.turnId);
    assert.equal(outcome.status, "dialogue_only"); assert.equal(fetches, 1); assert.equal(live.exportState().sourceCredentialReads, 1);
    for (const file of await filesBelow(cognitionRunRoot)) {
      const text = await readFile(file, "utf8");
      assert.equal(text.includes(fakeCredential), false); assert.equal(text.includes(credentialFile), false);
      assert.equal(text.includes("Offline credential integration fixture."), false);
    }
  } finally { await live.close(); }
});

test("offline manual profiles reject non-data, unknown, extra, official, and credential-bearing input before resource access", async () => {
  const base = { providerMode: "offline-fake", implementationSha256: SHA_A, godotBinarySha256: SHA_B };
  let getterReads = 0, resourceReads = 0;
  const symbolProfile = { scenario: "normal", windowSize: "960x540" };
  symbolProfile[Symbol("extra")] = true;
  const getterProfile = { windowSize: "960x540" };
  Object.defineProperty(getterProfile, "scenario", { enumerable: true, get() { getterReads += 1; return "normal"; } });
  const cases = [
    { ...base, offlineManualProfile: getterProfile },
    { ...base, offlineManualProfile: { scenario: "unknown", windowSize: "960x540" } },
    { ...base, offlineManualProfile: { scenario: "normal", windowSize: "800x600" } },
    { ...base, offlineManualProfile: { scenario: "normal", windowSize: "960x540", extra: true } },
    { ...base, offlineManualProfile: symbolProfile },
    { ...base, offlineManualProfile: new Proxy({ scenario: "normal", windowSize: "960x540" }, {}) },
    { ...base, providerMode: "official-once", offlineManualProfile: { scenario: "normal", windowSize: "960x540" } },
    { ...base, credentialFile: undefined, offlineManualProfile: { scenario: "normal", windowSize: "960x540" } },
  ];
  for (const input of cases) {
    Object.defineProperty(input, "temporaryRoot", { get() { resourceReads += 1; throw new Error("resource access"); } });
    await assert.rejects(createR22LiveComposition(input), /R22_LIVE_CONFIGURATION_INVALID/u);
  }
  assert.equal(getterReads, 0);
  assert.equal(resourceReads, 0);
});

async function createOfflineManualFixture(t, scenario, windowSize = "960x540") {
  if (process.platform !== "win32") { t.skip("fixed Windows qualification cache is unavailable"); return null; }
  try { await access(path.join(CASE.npcRunRoot, "npc-current.json")); await access(path.join(CASE.derivedStateRoot, "npc-derived-state-bundle.json")); }
  catch { t.skip("fixed Windows qualification cache is unavailable"); return null; }
  const base = path.join(TEMP_ROOT, `matrix-oasis-r22-live-${randomUUID()}`);
  const npcRunRoot = `${base}-npc`, cognitionRunRoot = `${base}-cognition`;
  const name = /^matrix-oasis-r22-live-[0-9a-f-]+-(?:npc|cognition)$/u;
  t.after(async () => {
    await removeOwnedDirectory(npcRunRoot, name); await removeOwnedDirectory(cognitionRunRoot, name);
    await rm(path.join(TEMP_ROOT, `.${path.basename(npcRunRoot)}.r20-writer-lock`), { force: true });
    await rm(path.join(TEMP_ROOT, `.${path.basename(cognitionRunRoot)}.r22-writer-lock`), { force: true });
  });
  const source = await verifyR22QualifiedSourcePair(CASE, TEMP_ROOT);
  const profile = { scenario, windowSize };
  const live = await createR22LiveComposition({ providerMode: "offline-fake", implementationSha256: SHA_A,
    godotBinarySha256: SHA_B, source, temporaryRoot: TEMP_ROOT, npcRunRoot, cognitionRunRoot,
    offlineManualProfile: profile });
  return { live, source, actorEntityId: JSON.parse(source.npcEntityBindingJson).bindings[0].actorEntityId,
    cognitionRunRoot, profile };
}

async function approveOfflineManualTurn(fixture) {
  const turn = await route(fixture.live, "POST", "/v1/cognition/turn", {
    actorEntityId: fixture.actorEntityId, playerText: "Exercise the fixed offline manual scenario.",
  });
  await route(fixture.live, "POST", "/v1/cognition/approve", { turnId: turn.turnId, approvalHash: turn.approvalHash });
  return { turn, outcome: await settled(fixture.live, turn.turnId) };
}

async function completeOfflineCommand(live, turn, outcome) {
  if (outcome.status !== "fallback") {
    await route(live, "POST", "/v1/cognition/displayed", { turnId: turn.turnId, displayAckHash: outcome.displayAckHash });
  }
  const selected = await route(live, "GET", "/v1/command");
  assert.equal(selected.status, "command");
  assert.deepEqual(await route(live, "GET", "/v1/command"), { status: "quiescent" });
  const arrived = await route(live, "POST", "/v1/arrived", { sequence: selected.command.sequence,
    pathComplete: true, floorVerified: true, capsuleVerified: true, domainVerified: true, movementTicks: 0, pathLengthMm: 0 });
  await route(live, "POST", "/v1/mirror", { sequence: selected.command.sequence,
    beforeSnapshotSha256: arrived.beforeSnapshotSha256, afterSnapshotSha256: arrived.afterSnapshotSha256 });
  return { selected, arrived };
}

function syntheticPhysicalMarker(live, source, selected, arrived) {
  const snapshot = live.exportState().authority;
  const committed = snapshot.commands.find((item) => item.sequence === selected.command.sequence);
  const ledger = JSON.parse(snapshot.authority.canonicalWorldEventLedgerJson);
  const entry = ledger.entries.find((item) => item.intent.id === selected.command.intentId);
  assert.ok(committed); assert.ok(entry);
  const identity = Object.fromEntries(["sequence", "actorEntityId", "ruleIndex", "intentId", "nodeId", "actionId"]
    .map((key) => [key, selected.command[key]]));
  const authority = { decision: arrived.decision, beforeSnapshotSha256: arrived.beforeSnapshotSha256,
    afterSnapshotSha256: arrived.afterSnapshotSha256 };
  const trace = [
    { sequence: selected.command.sequence, actorEntityId: selected.command.actorEntityId, actionId: selected.command.actionId,
      state: "arrived", arrivalEvidence: structuredClone(committed.arrivalEvidence) },
    { sequence: selected.command.sequence, actorEntityId: selected.command.actorEntityId, actionId: selected.command.actionId,
      state: "mirrored", ...authority },
  ];
  const intentJson = canonicalText(entry.intent);
  return canonicalText({ format: "matrix-oasis.r22-live-physical-evidence", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", timelineId: ledger.timeline.id,
    entityBindingSha256: sha256(source.npcEntityBindingJson),
    command: { ...identity, commandSha256: sha256(canonicalText({ ...identity, npcIntentJson: intentJson })),
      intentSha256: sha256(intentJson) },
    authority, movement: { outbound: structuredClone(committed.arrivalEvidence),
      return: { kind: "walked-home", returnedHome: true, physicsProcessing: false, positionErrorMm: 0 } },
    performance: { sampleCount: 300, frameMicros: Array(300).fill(16000), medianFrameMicros: 16000, medianFpsMilli: 62500 },
    canonicalR20Trace: canonicalText(trace) });
}

test("offline manual normal, refusal, invalid-response, and injection use one bounded fake dispatch through the real host path", async (t) => {
  const scenarios = [
    ["normal", "640x540", "queued_for_r20", null],
    ["refusal", "960x540", "fallback", "R22_PROVIDER_REFUSED"],
    ["invalid-response", "960x540", "fallback", "R22_PROVIDER_RESPONSE_INVALID"],
    ["injection", "960x540", "queued_for_r20", null],
  ];
  for (const [scenario, windowSize, status, diagnostic] of scenarios) await t.test(scenario, async (child) => {
    const fixture = await createOfflineManualFixture(child, scenario, windowSize); if (!fixture) return;
    const { live, cognitionRunRoot, profile } = fixture;
    try {
      assert.deepEqual(live.offlineManualProfile, profile); assert.equal(Object.isFrozen(live.offlineManualProfile), true);
      profile.scenario = "timeout";
      assert.equal(live.offlineManualProfile.scenario, scenario);
      const manifest = JSON.parse(await readFile(path.join(cognitionRunRoot, "cognition-session-manifest.json"), "utf8"));
      assert.equal(manifest.formatVersion, "0.2.0");
      assert.deepEqual(manifest.offlineManualProfile, { scenario, windowSize });
      assert.equal(manifest.implementationSha256, SHA_A);
      const { turn, outcome } = await approveOfflineManualTurn(fixture);
      assert.equal(outcome.status, status);
      if (diagnostic === null) {
        assert.equal(typeof outcome.displayAckHash, "string");
      } else {
        assert.deepEqual(outcome, { status: "fallback", diagnostic });
      }
      if (scenario === "injection") {
        assert.equal(outcome.dialogueText,
          "[offline link](file:///offline-qa) <b>literal only</b> [color=red]no execution[/color]");
        const callPlanFile = (await filesBelow(cognitionRunRoot)).find((file) => file.endsWith("call-plan.json"));
        assert.ok(callPlanFile);
        const callPlan = JSON.parse(await readFile(callPlanFile, "utf8"));
        assert.equal(outcome.actionChoiceId, callPlan.candidateChoices[0].choiceId);
      }
      assert.equal(live.exportState().fakeDispatches, 1);
      assert.equal(live.exportState().realProviderRequests, 0);
      assert.equal(live.exportState().sourceCredentialReads, 0);
      assert.deepEqual(await route(live, "GET", `/v1/cognition/status/${turn.turnId}`), outcome);
      assert.equal(live.exportState().fakeDispatches, 1);
      const completed = await completeOfflineCommand(live, turn, outcome);
      if (scenario === "injection") {
        const physical = await live.recordPhysicalEvidence(syntheticPhysicalMarker(live, fixture.source, completed.selected, completed.arrived));
        const physicalName = /^matrix-oasis-r22-physical-[0-9a-f]{64}$/u;
        const identity = await inspectOwnedDirectory(physical.output, physicalName);
        child.after(() => removeOwnedDirectory(physical.output, physicalName, identity));
        const report = JSON.parse(await readFile(path.join(physical.output, "observation-report.json"), "utf8"));
        assert.equal(report.formatVersion, "0.2.0");
        assert.deepEqual(report.offlineManualProfile, { scenario, windowSize });
        assert.equal(report.implementationSha256, SHA_A);
        assert.equal(report.providerMode, "offline-fake"); assert.equal(report.realProviderRequests, 0);
        assert.equal(report.sourceCredentialReads, 0); assert.equal(report.manualAcceptancePassed, false);
      }
      const persisted = await Promise.all((await filesBelow(cognitionRunRoot)).map((file) => readFile(file, "utf8")));
      assert.equal(persisted.some((text) => text.includes("offline-fake-key") || text.includes("Offline fake refusal.") ||
        text.includes("file:///offline-qa") || text.includes("Exercise the fixed offline manual scenario.")), false);
    } finally { await live.close(); }
  });
});

test("offline manual timeout waits for the real 30-second AbortSignal and never retries", { timeout: 60000 }, async (t) => {
  const fixture = await createOfflineManualFixture(t, "timeout", "640x540"); if (!fixture) return;
  const started = performance.now();
  try {
    const { turn, outcome } = await approveOfflineManualTurn(fixture);
    const elapsed = performance.now() - started;
    assert.deepEqual(outcome, { status: "fallback", diagnostic: "R22_PROVIDER_TIMEOUT" });
    assert.equal(elapsed >= 29_000, true, `timeout settled too early: ${elapsed}`);
    assert.equal(elapsed < 45_000, true, `timeout exceeded test budget: ${elapsed}`);
    const state = fixture.live.exportState();
    assert.equal(state.fakeDispatches, 1); assert.equal(state.realProviderRequests, 0); assert.equal(state.sourceCredentialReads, 0);
    assert.deepEqual(await route(fixture.live, "GET", `/v1/cognition/status/${turn.turnId}`), outcome);
    assert.equal(fixture.live.exportState().fakeDispatches, 1);
    await completeOfflineCommand(fixture.live, turn, outcome);
  } finally { await fixture.live.close(); }
});

test("live composition preserves source, rotates stores on reset, and releases exactly one command per approved turn", async (t) => {
  if (process.platform !== "win32") return t.skip("fixed Windows qualification cache is unavailable");
  try {
    await access(path.join(CASE.npcRunRoot, "npc-current.json"));
    await access(path.join(CASE.derivedStateRoot, "npc-derived-state-bundle.json"));
  } catch {
    return t.skip("fixed Windows qualification cache is unavailable");
  }
  const suffix = randomUUID();
  const base = path.join(TEMP_ROOT, `matrix-oasis-r22-live-${suffix}`);
  const npcRunRoot = `${base}-npc`;
  const cognitionRunRoot = `${base}-cognition`;
  const liveRootName = /^matrix-oasis-r22-live-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-(?:npc|cognition)$/u;
  const leasePaths = [path.join(path.dirname(npcRunRoot), `.${path.basename(npcRunRoot)}.r20-writer-lock`),
    path.join(path.dirname(cognitionRunRoot), `.${path.basename(cognitionRunRoot)}.r22-writer-lock`)];
  t.after(async () => {
    await Promise.all([removeOwnedDirectory(npcRunRoot, liveRootName), removeOwnedDirectory(cognitionRunRoot, liveRootName),
      ...leasePaths.map((item) => rm(item, { force: true }))]);
  });
  const source = await verifyR22QualifiedSourcePair(CASE, TEMP_ROOT);
  const sourceIdentity = [source.npc.record.sha256, source.derived.record.sha256];
  const actorEntityId = JSON.parse(source.npcEntityBindingJson).bindings[0].actorEntityId;
  const publishedPhysicalOutputs = new Map();
  const physicalName = /^matrix-oasis-r22-physical-[0-9a-f]{64}$/u;
  t.after(async () => { await Promise.all([...publishedPhysicalOutputs].map(([output, identity]) => removeOwnedDirectory(output, physicalName, identity))); });
  const live = await createR22LiveComposition({ providerMode: "offline-fake", implementationSha256: SHA_A,
    godotBinarySha256: SHA_B, source, temporaryRoot: TEMP_ROOT, npcRunRoot, cognitionRunRoot });
  try {
    const defaultManifest = JSON.parse(await readFile(path.join(cognitionRunRoot, "cognition-session-manifest.json"), "utf8"));
    assert.equal(defaultManifest.formatVersion, "0.1.0");
    assert.equal(Object.hasOwn(defaultManifest, "offlineManualProfile"), false);
    assert.equal(Object.hasOwn(live, "offlineManualProfile"), false);
    assert.deepEqual(await route(live, "GET", "/v1/command"), { status: "quiescent" });

    const declined = await route(live, "POST", "/v1/cognition/turn", { actorEntityId, playerText: "Decline this disclosed turn." });
    await route(live, "POST", "/v1/cognition/decline", { turnId: declined.turnId, approvalHash: declined.approvalHash });
    assert.equal(live.exportState().fakeDispatches, 0);

    const approved = await route(live, "POST", "/v1/cognition/turn", { actorEntityId, playerText: "Approve this bounded safe action." });
    await route(live, "POST", "/v1/cognition/approve", { turnId: approved.turnId, approvalHash: approved.approvalHash });
    const outcome = await settled(live, approved.turnId);
    assert.equal(outcome.status, "queued_for_r20");
    await route(live, "POST", "/v1/cognition/displayed", { turnId: approved.turnId, displayAckHash: outcome.displayAckHash });
    const selected = await route(live, "GET", "/v1/command");
    assert.equal(selected.status, "command");
    assert.deepEqual(await route(live, "GET", "/v1/command"), { status: "quiescent" });
    const arrived = await route(live, "POST", "/v1/arrived", { sequence: selected.command.sequence,
      pathComplete: true, floorVerified: true, capsuleVerified: true, domainVerified: true, movementTicks: 0, pathLengthMm: 0 });
    assert.equal(arrived.status, "adjudicated");
    await route(live, "POST", "/v1/mirror", { sequence: selected.command.sequence,
      beforeSnapshotSha256: arrived.beforeSnapshotSha256, afterSnapshotSha256: arrived.afterSnapshotSha256 });
    const authoritySnapshot = live.exportState().authority;
    const committed = authoritySnapshot.commands.find((item) => item.sequence === selected.command.sequence);
    assert.ok(committed);
    assert.deepEqual(Object.keys(committed.arrivalEvidence).sort(), ["capsuleVerified", "domainVerified", "floorVerified", "movementTicks", "pathComplete", "pathLengthMm"]);
    const ledger = JSON.parse(authoritySnapshot.authority.canonicalWorldEventLedgerJson);
    const entry = ledger.entries.find((item) => item.intent.id === selected.command.intentId);
    assert.ok(entry);
    const identity = Object.fromEntries(["sequence", "actorEntityId", "ruleIndex", "intentId", "nodeId", "actionId"].map((key) => [key, selected.command[key]]));
    const authority = { decision: arrived.decision, beforeSnapshotSha256: arrived.beforeSnapshotSha256, afterSnapshotSha256: arrived.afterSnapshotSha256 };
    const syntheticOutbound = structuredClone(committed.arrivalEvidence);
    const syntheticTrace = [
      { sequence: selected.command.sequence, actorEntityId: selected.command.actorEntityId, actionId: selected.command.actionId, state: "arrived", arrivalEvidence: syntheticOutbound },
      { sequence: selected.command.sequence, actorEntityId: selected.command.actorEntityId, actionId: selected.command.actionId, state: "mirrored", ...authority },
    ];
    const intentJson = canonicalText(entry.intent);
    // This marker and its repeated 300-frame sample are synthetic contract-test
    // input only. They are not evidence of a real runtime or qualification.
    const syntheticMarker = {
      format: "matrix-oasis.r22-live-physical-evidence", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1",
      timelineId: ledger.timeline.id, entityBindingSha256: sha256(source.npcEntityBindingJson),
      command: { ...identity, commandSha256: sha256(canonicalText({ ...identity, npcIntentJson: intentJson })), intentSha256: sha256(intentJson) },
      authority, movement: { outbound: syntheticOutbound,
        return: { kind: "walked-home", returnedHome: true, physicsProcessing: false, positionErrorMm: 0 } },
      performance: { sampleCount: 300, frameMicros: Array(300).fill(16000), medianFrameMicros: 16000, medianFpsMilli: 62500 },
      canonicalR20Trace: canonicalText(syntheticTrace),
    };
    const forgedMovement = structuredClone(syntheticMarker);
    const outputsBeforeAttacks = await physicalOutputs();
    forgedMovement.movement.outbound.movementTicks += 1;
    await assert.rejects(live.recordPhysicalEvidence(canonicalText(forgedMovement)), /R22_LIVE_PHYSICAL_EVIDENCE_INVALID/u);
    const forgedCommand = structuredClone(syntheticMarker);
    forgedCommand.command.actionId = "forged-action";
    forgedCommand.command.commandSha256 = sha256(canonicalText({ ...identity, actionId: "forged-action", npcIntentJson: intentJson }));
    await assert.rejects(live.recordPhysicalEvidence(canonicalText(forgedCommand)), /R22_LIVE_PHYSICAL_EVIDENCE_INVALID/u);
    assert.deepEqual(await physicalOutputs(), outputsBeforeAttacks);
    const physical = await live.recordPhysicalEvidence(canonicalText(syntheticMarker));
    publishedPhysicalOutputs.set(physical.output, await inspectOwnedDirectory(physical.output, physicalName));
    assert.equal(physical.performancePassed, true);
    assert.equal(physical.medianFpsMilli, 62500);
    assert.equal(physical.cognitionActionVerified, true);
    const observationReport = JSON.parse(await readFile(path.join(physical.output, "observation-report.json"), "utf8"));
    assert.equal(observationReport.qualificationStatus, "unqualified-manual-observation");
    assert.equal(observationReport.manualAcceptancePassed, false);
    assert.equal(observationReport.entrySha256, entry.entrySha256);
    assert.equal(observationReport.turnReceiptSha256 === null, false);
    assert.equal(await readFile(path.join(physical.output, "world-event-ledger.json"), "utf8"), authoritySnapshot.authority.canonicalWorldEventLedgerJson);
    const receiptFilesBeforeReset = (await filesBelow(cognitionRunRoot)).filter((item) => item.endsWith("turn-receipt.json"));
    assert.equal(receiptFilesBeforeReset.length, 2);
    const budgetPath = path.join(cognitionRunRoot, "host-budget.json");
    const budgetBeforeReset = await readFile(budgetPath, "utf8");

    const reset = await route(live, "POST", "/v1/reset", {});
    assert.deepEqual(Object.keys(reset).sort(), ["status", "timelineId"]);
    assert.equal(reset.status, "reset");
    assert.equal(await readFile(budgetPath, "utf8"), budgetBeforeReset);
    const afterReset = await route(live, "POST", "/v1/cognition/turn", { actorEntityId, playerText: "First turn on the reset timeline." });
    await route(live, "POST", "/v1/cognition/decline", { turnId: afterReset.turnId, approvalHash: afterReset.approvalHash });
    const persistedFiles = await filesBelow(cognitionRunRoot);
    const receipts = await Promise.all(persistedFiles.filter((item) => item.endsWith("turn-receipt.json")).map((item) => readFile(item, "utf8").then(JSON.parse)));
    assert.equal(receipts.length, 3);
    const checkpoints = await Promise.all(persistedFiles.filter((item) => item.endsWith("cognition-checkpoint.json")).map((item) => readFile(item, "utf8").then(JSON.parse)));
    assert.deepEqual(checkpoints.map((item) => item.latestSequence).sort((left, right) => left - right), [1, 2]);
    assert.equal(persistedFiles.filter((item) => item.endsWith("call-plan.json")).length, 3);
    assert.equal(live.exportState().fakeDispatches, 1);
    await live.revalidate();
    const sourceAfter = await verifyR22QualifiedSourcePair(CASE, TEMP_ROOT);
    assert.equal(sourceAfter.npc.record.sha256, CASE.expectedNpcCurrentSha256);
    assert.equal(sourceAfter.derived.record.sha256, CASE.expectedDerivedStateBundleSha256);
    assert.deepEqual([sourceAfter.npc.record.sha256, sourceAfter.derived.record.sha256], sourceIdentity);
  } finally {
    await live.close();
  }
  for (const leasePath of leasePaths) await assert.rejects(access(leasePath));
});
