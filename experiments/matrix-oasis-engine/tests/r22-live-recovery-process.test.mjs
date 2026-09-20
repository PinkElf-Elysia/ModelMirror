import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { writeSync } from "node:fs";
import * as fs from "node:fs/promises";
import { syncBuiltinESMExports } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const FILE = fileURLToPath(import.meta.url);
const MODULE_ROOT = path.dirname(path.dirname(FILE));
const TEMP_ROOT = path.join(path.parse(FILE).root, "tmp");
const CASE = Object.freeze({ caseId: "neutral-cache", sourceKind: "qualified-cache",
  npcRunRoot: path.join(TEMP_ROOT, "matrix-oasis-r20-neutral-r10-npc"),
  derivedStateRoot: path.join(TEMP_ROOT, "matrix-oasis-r21-real-20260901-b-case-1"),
  expectedNpcCurrentSha256: "sha256:187221f904f7eee7fc97c0194fe16f075365b1b489050ac00a04be0c4da91135",
  expectedDerivedStateBundleSha256: "sha256:7cfc9b43e6318076b57ee97e34a759a9b9a52c31377a9beaaf5d8d829d1a008b" });
const SHA_A = `sha256:${"a".repeat(64)}`, SHA_B = `sha256:${"b".repeat(64)}`;
const MANUAL_PROFILE = Object.freeze({ scenario: "invalid-response", windowSize: "640x540" });

async function modules() {
  const [composition, host, qualification] = await Promise.all([
    import("../scripts/lib/r22-live-composition.mjs"), import("../scripts/lib/r22-host-core.mjs"),
    import("../scripts/lib/r22-qualification-core.mjs"),
  ]);
  return { ...composition, ...host, ...qualification };
}
function configuration(base, source, mode = "offline-fake", resume = false, offlineManualProfile) {
  return { source, temporaryRoot: TEMP_ROOT, npcRunRoot: `${base}-npc`, cognitionRunRoot: `${base}-cognition`,
    implementationSha256: SHA_A, godotBinarySha256: SHA_B, providerMode: mode, resume,
    ...(offlineManualProfile === undefined ? {} : { offlineManualProfile }) };
}
async function route(api, live, method, url, body = {}, expected = 200) {
  const response = await api.handleR22LoopbackRequestAsync(live.controller, { remoteAddress: "127.0.0.1", method, url,
    headers: { authorization: `Bearer ${live.sessionToken}`, ...(method === "POST" ? { "content-type": "application/json" } : {}) },
    body: method === "POST" ? JSON.stringify(body) : "" });
  assert.equal(response.statusCode, expected, response.body);
  return JSON.parse(response.body);
}
async function settled(api, live, turnId) {
  for (let attempt = 0; attempt < 400; attempt += 1) {
    const result = await route(api, live, "GET", `/v1/cognition/status/${turnId}`);
    if (result.status !== "dispatching") return result;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail("fake provider failed to settle");
}
async function commit(api, live, command) {
  // Protocol-only fixture: these values are not physical Godot evidence.
  const arrived = await route(api, live, "POST", "/v1/arrived", { sequence: command.sequence,
    pathComplete: true, floorVerified: true, capsuleVerified: true, domainVerified: true, movementTicks: 0, pathLengthMm: 0 });
  assert.equal(arrived.status, "adjudicated");
  await route(api, live, "POST", "/v1/mirror", { sequence: command.sequence,
    beforeSnapshotSha256: arrived.beforeSnapshotSha256, afterSnapshotSha256: arrived.afterSnapshotSha256 });
}

if (process.argv[2] === "--r22-crash-worker") {
  const scenario = process.argv[3], base = process.argv[4];
  const mode = scenario === "official-planned" ? "official-once" : "offline-fake";
  let manualCredentialReads = 0, manualExternalFetches = 0;
  if (scenario === "manual-finalized") {
    // Install before any R22 module is loaded. This fixture may exercise the
    // real parser and disk transaction, but never a real transport or key.
    globalThis.fetch = () => { manualExternalFetches += 1; throw new Error("R22_TEST_EXTERNAL_FETCH_FORBIDDEN"); };
    process.env = new Proxy(process.env, {
      get(target, key) {
        if (/(?:OPENAI|OPENROUTER|MESHY|MARBLE|API_KEY|CREDENTIAL)/u.test(String(key))) {
          manualCredentialReads += 1;
          throw new Error("R22_TEST_CREDENTIAL_READ_FORBIDDEN");
        }
        return Reflect.get(target, key);
      },
    });
  }
  function crash(phase) {
    writeSync(1, JSON.stringify({ phase, sourceCredentialReads: manualCredentialReads, realProviderRequests: manualExternalFetches }));
    process.exit(81);
  }
  if (scenario === "dispatch") {
    // Intercept the offline response only, after the production host has durably
    // recorded dispatch. No alternate production recovery path is installed.
    globalThis.Response = class { constructor() { crash("dispatch"); } };
  }
  if (scenario === "after-authority-commit" || scenario === "receipt-renamed") {
    // The persisted R20 commit is complete before the R22 receipt rename. Kill
    // this separate process at that exact real filesystem boundary.
    const promises = (await import("node:fs/promises")).default;
    const original = promises.rename;
    promises.rename = async (from, to) => {
      const receipt = path.basename(to) === "turn-receipt.json";
      if (receipt && scenario === "after-authority-commit") crash("after-authority-commit");
      const result = await original(from, to);
      if (receipt && scenario === "receipt-renamed") crash("receipt-renamed");
      return result;
    };
    syncBuiltinESMExports();
  }
  const api = await modules();
  const source = await api.verifyR22QualifiedSourcePair(CASE, TEMP_ROOT);
  const live = await api.createR22LiveComposition(configuration(base, source, mode, false,
    scenario === "manual-finalized" ? MANUAL_PROFILE : undefined));
  const actorEntityId = JSON.parse(source.npcEntityBindingJson).bindings[0].actorEntityId;
  if (scenario === "reset-queued") {
    const first = await route(api, live, "POST", "/v1/cognition/turn", { actorEntityId, playerText: "First timeline bounded action." });
    await route(api, live, "POST", "/v1/cognition/approve", { turnId: first.turnId, approvalHash: first.approvalHash });
    const firstOutcome = await settled(api, live, first.turnId);
    await route(api, live, "POST", "/v1/cognition/displayed", { turnId: first.turnId, displayAckHash: firstOutcome.displayAckHash });
    const selected = await route(api, live, "GET", "/v1/command");
    await commit(api, live, selected.command);
    await route(api, live, "POST", "/v1/reset");
    await route(api, live, "POST", "/v1/reset");
  }
  const turn = await route(api, live, "POST", "/v1/cognition/turn", { actorEntityId, playerText: "Test the disclosed safe action." });
  if (scenario === "planned" || scenario === "official-planned") crash("planned");
  await route(api, live, "POST", "/v1/cognition/approve", { turnId: turn.turnId, approvalHash: turn.approvalHash });
  const outcome = await settled(api, live, turn.turnId);
  if (scenario === "manual-finalized") {
    assert.equal(outcome.status, "fallback");
    const state = live.exportState();
    assert.equal(state.fakeDispatches, 1);
    assert.equal(state.realProviderRequests, 0);
    assert.equal(state.sourceCredentialReads, 0);
    assert.equal(state.callStore.checkpoint.active, null);
    assert.equal(state.callStore.checkpoint.finalized.length, 1);
    crash("manual-finalized");
  }
  assert.equal(outcome.status, "queued_for_r20");
  await route(api, live, "POST", "/v1/cognition/displayed", { turnId: turn.turnId, displayAckHash: outcome.displayAckHash });
  if (scenario === "queued" || scenario === "reset-queued") crash("queued");
  const selected = await route(api, live, "GET", "/v1/command");
  assert.equal(selected.status, "command");
  await commit(api, live, selected.command);
  crash("finalized");
} else {
  async function supported(t) {
    if (process.platform !== "win32") { t.skip("fixed Windows cache unavailable"); return false; }
    try { await fs.access(path.join(CASE.npcRunRoot, "npc-current.json")); await fs.access(path.join(CASE.derivedStateRoot, "npc-derived-state-bundle.json")); }
    catch { t.skip("fixed Windows cache unavailable"); return false; }
    return true;
  }
  async function ownRoot(t) {
    const base = path.join(TEMP_ROOT, `matrix-oasis-r22-recovery-${randomUUID()}`);
    t.after(async () => {
      for (const suffix of ["-npc", "-cognition"]) {
        const target = `${base}${suffix}`;
        assert.equal(path.dirname(path.resolve(target)), path.resolve(TEMP_ROOT));
        assert.match(path.basename(target), /^matrix-oasis-r22-recovery-[0-9a-f-]{36}-(npc|cognition)$/u);
        const stat = await fs.lstat(target, { bigint: true }).catch((error) => { if (error.code !== "ENOENT") throw error; return null; });
        if (stat) {
          assert.equal(stat.isDirectory() && !stat.isSymbolicLink(), true);
          assert.equal(await fs.realpath(target), target);
          const repeated = await fs.lstat(target, { bigint: true });
          assert.equal(`${stat.dev}:${stat.ino}`, `${repeated.dev}:${repeated.ino}`);
          await fs.rm(target, { recursive: true, force: false });
        }
        const lease = path.join(TEMP_ROOT, `.${path.basename(target)}.${suffix === "-npc" ? "r20" : "r22"}-writer-lock`);
        assert.equal(path.dirname(lease), TEMP_ROOT);
        await fs.rm(lease, { force: true });
      }
    });
    return base;
  }
  async function crashWorker(base, scenario) {
    const env = Object.fromEntries(["SystemRoot", "WINDIR", "TEMP", "TMP"].filter((name) => process.env[name] !== undefined)
      .map((name) => [name, process.env[name]]));
    return new Promise((resolve, reject) => {
      const child = spawn(process.execPath, [FILE, "--r22-crash-worker", scenario, base],
        { cwd: MODULE_ROOT, env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
      let stdout = "", stderr = "";
      const timer = setTimeout(() => { child.kill(); reject(new Error("R22_CRASH_WORKER_TIMEOUT")); }, 60000);
      child.stdout.on("data", (bytes) => { stdout += bytes; if (stdout.length > 65536) child.kill(); });
      child.stderr.on("data", (bytes) => { stderr += bytes; if (stderr.length > 65536) child.kill(); });
      child.once("error", (error) => { clearTimeout(timer); reject(error); });
      child.once("close", (code) => {
        clearTimeout(timer);
        try { assert.equal(code, 81, stderr); const value = JSON.parse(stdout); assert.equal(value.realProviderRequests, 0); resolve(value); }
        catch (error) { reject(error); }
      });
    });
  }

  for (const scenario of ["planned", "dispatch", "queued", "after-authority-commit", "receipt-renamed", "finalized", "reset-queued"]) {
    test(`live composition resumes a separate crashed process: ${scenario}`, { timeout: 90000 }, async (t) => {
      if (!await supported(t)) return;
      const base = await ownRoot(t), api = await modules();
      await crashWorker(base, scenario);
      const source = await api.verifyR22QualifiedSourcePair(CASE, TEMP_ROOT);
      const live = await api.createR22LiveComposition(configuration(base, source, "offline-fake", true));
      try {
        const state = live.exportState();
        assert.equal(state.fakeDispatches, 0); assert.equal(state.realProviderRequests, 0); assert.equal(state.sourceCredentialReads, 0);
        assert.equal(state.recovery.providerReplayRequests, 0);
        if (scenario === "dispatch") {
          assert.equal(state.callStore.hostBudget.chargedMicrousd, 10000);
          assert.equal(state.callStore.checkpoint.providerRequests, 1);
          assert.equal(state.callStore.checkpoint.active, null);
        }
        if (scenario === "planned") assert.equal(state.callStore.hostBudget.chargedMicrousd, 0);
        if (scenario === "queued" || scenario === "reset-queued") {
          assert.equal(live.resumeQueuedAction, true);
          assert.equal(state.authority.resetCount, scenario === "reset-queued" ? 2 : 0);
          // Both fake responses have known usage (100 microusd each), already
          // charged at validation. Committing the second Action must not charge
          // it again or erase the earlier sealed timeline's charge.
          if (scenario === "reset-queued") assert.equal(state.callStore.hostBudget.chargedMicrousd, 200);
          const selected = await route(api, live, "GET", "/v1/command");
          assert.equal(selected.status, "command");
          assert.equal(selected.command.intentId, state.recovery.mappedIntentId);
          assert.deepEqual(await route(api, live, "GET", "/v1/command"), { status: "quiescent" });
          await commit(api, live, selected.command);
          assert.equal(live.exportState().authority.commands.length, 1);
          if (scenario === "reset-queued") assert.equal(live.exportState().callStore.hostBudget.chargedMicrousd, 200);
          assert.deepEqual(await route(api, live, "GET", "/v1/command"), { status: "quiescent" });
          if (scenario === "reset-queued") {
            const charged = live.exportState().callStore.hostBudget.chargedMicrousd;
            await route(api, live, "POST", "/v1/reset");
            assert.equal(live.exportState().authority.resetCount, 3);
            assert.equal(live.exportState().callStore.hostBudget.chargedMicrousd, charged);
          }
        } else {
          assert.equal(live.resumeQueuedAction, false);
          assert.deepEqual(await route(api, live, "GET", "/v1/command"), { status: "quiescent" });
        }
        if (scenario === "after-authority-commit" || scenario === "receipt-renamed" || scenario === "finalized") {
          assert.equal(state.authority.commands.length, 1);
          assert.equal(live.recoveryCommands.length, 1);
          assert.equal(state.callStore.checkpoint.active, null);
          assert.equal(state.callStore.checkpoint.finalized.length, 1);
          assert.equal(state.callStore.checkpoint.providerRequests, 1);
        }
        await live.revalidate();
      } finally { await live.close(); }
      const again = await api.verifyR22QualifiedSourcePair(CASE, TEMP_ROOT);
      assert.equal(again.npc.record.sha256, CASE.expectedNpcCurrentSha256);
      assert.equal(again.derived.record.sha256, CASE.expectedDerivedStateBundleSha256);
    });
  }

  test("resume refuses changed mode or missing budget and cannot rearm a paid call", { timeout: 90000 }, async (t) => {
    if (!await supported(t)) return;
    const base = await ownRoot(t), api = await modules();
    await crashWorker(base, "official-planned");
    const source = await api.verifyR22QualifiedSourcePair(CASE, TEMP_ROOT);
    await assert.rejects(api.createR22LiveComposition(configuration(base, source, "offline-fake", true)), /R22_RECOVERY_SESSION_IDENTITY_MISMATCH/u);
    const budgetFile = path.join(`${base}-cognition`, "host-budget.json");
    const bytes = await fs.readFile(budgetFile);
    await fs.unlink(budgetFile);
    await assert.rejects(api.createR22LiveComposition(configuration(base, source, "official-once", true)), /R22_(LIVE_RECOVERY|FILE_IDENTITY)_INVALID/u);
    await assert.rejects(fs.access(budgetFile));
    await fs.writeFile(budgetFile, bytes, { flag: "wx" });
    const live = await api.createR22LiveComposition(configuration(base, source, "official-once", true));
    try {
      const actorEntityId = JSON.parse(source.npcEntityBindingJson).bindings[0].actorEntityId;
      const denied = await route(api, live, "POST", "/v1/cognition/turn", { actorEntityId, playerText: "This must never dispatch." }, 409);
      assert.equal(typeof denied.code, "string");
      assert.equal(live.exportState().sourceCredentialReads, 0);
      assert.equal(live.exportState().realProviderRequests, 0);
      assert.equal(live.exportState().callStore.hostBudget.chargedMicrousd, 0);
    } finally { await live.close(); }
  });

  test("offline manual process recovery binds scenario and viewport and never replays its fake request", { timeout: 90000 }, async (t) => {
    if (!await supported(t)) return;
    const base = await ownRoot(t), api = await modules();
    const crashed = await crashWorker(base, "manual-finalized");
    assert.equal(crashed.sourceCredentialReads, 0);
    assert.equal(crashed.realProviderRequests, 0);
    const source = await api.verifyR22QualifiedSourcePair(CASE, TEMP_ROOT);
    const manifestFile = path.join(`${base}-cognition`, "cognition-session-manifest.json");
    const budgetFile = path.join(`${base}-cognition`, "host-budget.json");
    const manifestBefore = await fs.readFile(manifestFile);
    const budgetBefore = await fs.readFile(budgetFile);
    const manifest = JSON.parse(manifestBefore);
    assert.equal(manifest.formatVersion, "0.2.0");
    assert.deepEqual(manifest.offlineManualProfile, MANUAL_PROFILE);
    for (const drift of [undefined, { ...MANUAL_PROFILE, scenario: "normal" },
      { ...MANUAL_PROFILE, windowSize: "960x540" }]) {
      await assert.rejects(api.createR22LiveComposition(configuration(base, source, "offline-fake", true, drift)),
        /R22_RECOVERY_SESSION_IDENTITY_MISMATCH/u);
      assert.deepEqual(await fs.readFile(manifestFile), manifestBefore);
      assert.deepEqual(await fs.readFile(budgetFile), budgetBefore);
    }
    const live = await api.createR22LiveComposition(configuration(base, source, "offline-fake", true, MANUAL_PROFILE));
    try {
      const state = live.exportState();
      assert.equal(state.fakeDispatches, 0);
      assert.equal(state.realProviderRequests, 0);
      assert.equal(state.sourceCredentialReads, 0);
      assert.equal(state.recovery.providerReplayRequests, 0);
      assert.equal(state.callStore.checkpoint.providerRequests, 1);
      assert.equal(state.callStore.checkpoint.finalized.length, 1);
      assert.equal(state.callStore.checkpoint.active, null);
      assert.equal(state.authority.commands.length, 0);
      assert.equal(JSON.parse(state.authority.authority.canonicalWorldEventLedgerJson).revision, 0);
      assert.equal(live.resumeQueuedAction, false);
      assert.deepEqual(await fs.readFile(manifestFile), manifestBefore);
      assert.deepEqual(await fs.readFile(budgetFile), budgetBefore);
      await live.revalidate();
    } finally { await live.close(); }
  });
}
