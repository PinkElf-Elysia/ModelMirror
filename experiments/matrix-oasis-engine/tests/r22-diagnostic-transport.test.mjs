import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { lstat, mkdir, mkdtemp, readFile, readdir, realpath, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import fsPromises from "node:fs/promises";
import { syncBuiltinESMExports } from "node:module";

import { canonicalizeJsonValue as canonical } from "@matrix-oasis/runtime-pack-contracts";
import { createNpcCognitionToolUsageDiagnosticPlan, createNpcCognitionBillingDiagnosticPlan } from "@matrix-oasis/npc-cognition-provider-openai";
import {
  createR22InjectedToolUsageDiagnosticTransaction,
  createR22OfficialToolUsageDiagnosticTransaction,
  createR22ToolUsageDiagnosticTransaction,
  describeR22OfficialToolUsageDiagnosticTransaction,
  auditR22DiagnosticBudgetHistory,
  recoverR22InjectedToolUsageDiagnosticTransaction,
  recoverR22OfficialToolUsageDiagnosticTransaction,
  recoverR22ToolUsageDiagnosticTransaction,
} from "../scripts/lib/r22-diagnostic-transaction.mjs";
import { auditBoundary } from "../scripts/lib/boundary-core.mjs";
import { openR22DiagnosticBudgetStore } from "../scripts/lib/r22-call-store.mjs";
import { runR22ToolUsageDiagnostic } from "../scripts/r22-tool-usage-diagnostic.mjs";

const OWNED_PREFIX = "r22dt-";
const TEMPORARY_PARENT = tmpdir();
const HOST_RUN_ID = "host-transport-fixture";
const HASH = /^sha256:[0-9a-f]{64}$/u;
const MODULE_ROOT = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const CHILD_OUTPUT_LIMIT_BYTES = 32 * 1024;
const CHILD_ENVIRONMENT_KEYS = ["SystemRoot", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT", "ComSpec"];

function sha(text) {
  return `sha256:${createHash("sha256").update(text).digest("hex")}`;
}

function h(digit) {
  return `sha256:${digit.repeat(64)}`;
}

function sessionManifest(providerMode = "offline-fake") {
  return {
    format: "matrix-oasis.r22-cognition-session-manifest",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    hostRunId: HOST_RUN_ID,
    initialTimelineId: "timeline-transport-fixture",
    providerMode,
    sourceCurrentSha256: h("a"),
    sourceDerivedBundleSha256: h("b"),
    sourceAuthorityManifestSha256: h("c"),
    cognitionPolicySha256: h("d"),
    implementationSha256: h("e"),
    godotBinarySha256: h("f"),
  };
}

function historicalEntry(index, state = "charged", chargedMicrousd = 123) {
  return {
    authoritySessionSha256: sha(`historical-authority-${index}`),
    callPlanSha256: sha(`historical-plan-${index}`),
    reservedMicrousd: 10_000,
    chargedMicrousd: state === "charged" ? chargedMicrousd : 0,
    state,
  };
}

function hostBudget(entries = [historicalEntry(0)]) {
  return {
    format: "matrix-oasis.r22-host-budget",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    hostRunId: HOST_RUN_ID,
    limitMicrousd: 1_000_000,
    entries,
  };
}

async function setup(t, { providerMode = "offline-fake", entries, outputName = "observation" } = {}) {
  const temporaryRoot = await mkdtemp(path.join(TEMPORARY_PARENT, OWNED_PREFIX));
  const initial = await lstat(temporaryRoot, { bigint: true });
  t.after(async () => {
    const current = await lstat(temporaryRoot, { bigint: true });
    assert.equal(path.basename(temporaryRoot).startsWith(OWNED_PREFIX), true);
    assert.equal(path.dirname(temporaryRoot), path.resolve(TEMPORARY_PARENT));
    assert.equal(current.isDirectory(), true);
    assert.equal(current.isSymbolicLink(), false);
    assert.equal(current.dev, initial.dev);
    assert.equal(current.ino, initial.ino);
    assert.equal(await realpath(temporaryRoot), temporaryRoot);
    await rm(temporaryRoot, { recursive: true, force: false });
  });
  const cognitionRunRoot = path.join(temporaryRoot, "run-cognition");
  await mkdir(cognitionRunRoot);
  const manifestJson = canonical(sessionManifest(providerMode));
  const budgetJson = canonical(hostBudget(entries));
  await writeFile(path.join(cognitionRunRoot, "cognition-session-manifest.json"), manifestJson, { flag: "wx" });
  await writeFile(path.join(cognitionRunRoot, "host-budget.json"), budgetJson, { flag: "wx" });
  return {
    initialBudgetJson: budgetJson,
    config: {
      temporaryRoot,
      cognitionRunRoot,
      hostRunId: HOST_RUN_ID,
      expectedSessionManifestSha256: sha(manifestJson),
      expectedHostBudgetSha256: sha(budgetJson),
      output: path.join(temporaryRoot, outputName),
    },
  };
}

async function currentConfig(f, outputName) {
  const budgetJson = await readFile(path.join(f.config.cognitionRunRoot, "host-budget.json"), "utf8");
  return {
    ...f.config,
    ...(outputName ? { output: path.join(f.config.temporaryRoot, outputName) } : {}),
    expectedHostBudgetSha256: sha(budgetJson),
  };
}

function validResponseBytes({ secret = "fixture-dialogue-placeholder", toolUsage = { image_gen: { total_tokens: 0 } } } = {}) {
  const plan = createNpcCognitionToolUsageDiagnosticPlan();
  const callPlan = JSON.parse(plan.callPlanJson);
  return new TextEncoder().encode(JSON.stringify({
    id: "resp_transport_fixture",
    object: "response",
    status: "completed",
    error: null,
    incomplete_details: null,
    model: callPlan.model,
    service_tier: "default",
    output: [{
      id: "msg_transport_fixture",
      type: "message",
      status: "completed",
      role: "assistant",
      content: [{
        type: "output_text",
        text: JSON.stringify({ contextSha256: callPlan.contextSha256, dialogueText: secret, actionChoiceId: null }),
        annotations: [],
      }],
    }],
    usage: {
      input_tokens: 20,
      input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 },
      output_tokens: 10,
      output_tokens_details: { reasoning_tokens: 0 },
      total_tokens: 30,
    },
    tool_usage: toolUsage,
  }));
}

function transportFixture(overrides = {}) {
  return {
    scenario: "response",
    responseBytes: validResponseBytes(),
    status: 200,
    contentType: "application/json",
    contentLength: null,
    redirected: false,
    responseUrl: "locked",
    chunkSize: 1024,
    timeoutMs: 30_000,
    ...overrides,
  };
}

function billingResponseBytes(billing = { payer: "developer", amount: 998877.25, currency: "usd",
  service_tier: "default", tool_costs: { total: 998877.25 }, PRIVATE_BILLING_KEY: "PRIVATE_BILLING_VALUE" }) {
  const value = JSON.parse(new TextDecoder().decode(validResponseBytes()));
  value.billing = billing;
  return new TextEncoder().encode(JSON.stringify(value));
}

function testOverrides(overrides = {}) {
  return {
    clock: () => 1_000,
    randomBytes: () => new Uint8Array(32).fill(7),
    ...overrides,
  };
}

const DIAGNOSTIC_NETWORK_GUARD_SOURCE = String.raw`
let networkTripwireHits = 0;
let blockedBeforeConnect = false;
const blockNetwork = (surface) => {
  networkTripwireHits += 1;
  blockedBeforeConnect = true;
  throw new Error("diagnostic-child-network-forbidden:" + surface);
};
net.Socket.prototype.connect = function () { return blockNetwork("socket.connect"); };
net.connect = function () { return blockNetwork("net.connect"); };
net.createConnection = function () { return blockNetwork("net.createConnection"); };
tls.connect = function () { return blockNetwork("tls.connect"); };
http.request = function () { return blockNetwork("http.request"); };
http.get = function () { return blockNetwork("http.get"); };
https.request = function () { return blockNetwork("https.request"); };
https.get = function () { return blockNetwork("https.get"); };
dns.lookup = function () { return blockNetwork("dns.lookup"); };
`;

const DIAGNOSTIC_CRASH_CHILD = String.raw`
import fsPromises from "node:fs/promises";
import dns from "node:dns";
import http from "node:http";
import https from "node:https";
import net from "node:net";
import path from "node:path";
import tls from "node:tls";
import { syncBuiltinESMExports } from "node:module";

${DIAGNOSTIC_NETWORK_GUARD_SOURCE}

const input = JSON.parse(Buffer.from(process.argv[1], "base64url").toString("utf8"));
const credentialPath = path.resolve(input.credentialFile);
let credentialOpens = 0;
let environmentReads = 0;
let fetchCalls = 0;
const originalEnvironment = process.env;
process.env = new Proxy(originalEnvironment, {
  get(target, name, receiver) {
    if (name === "MATRIX_OASIS_R22_OPENAI_API_KEY") {
      environmentReads += 1;
      throw new Error("child-environment-credential-read-forbidden");
    }
    return Reflect.get(target, name, receiver);
  },
  getOwnPropertyDescriptor(target, name) {
    if (name === "MATRIX_OASIS_R22_OPENAI_API_KEY") return undefined;
    return Reflect.getOwnPropertyDescriptor(target, name);
  },
});
const originalOpen = fsPromises.open;
const originalRename = fsPromises.rename;
fsPromises.open = async (target, ...args) => {
  if (path.resolve(String(target)) === credentialPath) credentialOpens += 1;
  return originalOpen(target, ...args);
};
let transactionSha256 = null;
let formatVersion = null;
const marker = (phase) => process.send?.({ kind: "crash-marker", phase, transactionSha256,
  formatVersion, credentialOpens, environmentReads, fetchCalls, networkTripwireHits });
fsPromises.rename = async (from, to, ...args) => {
  const result = await originalRename(from, to, ...args);
  if (input.point === "durable-dispatch" && path.basename(String(to)) === "dispatch-record.json") {
    marker("durable-dispatch");
    await new Promise(() => {});
  }
  return result;
};
Object.defineProperty(globalThis, "fetch", { configurable: false, enumerable: true, writable: false,
  value: async () => {
    fetchCalls += 1;
    marker("fetch-entered");
    await new Promise(() => {});
  } });
syncBuiltinESMExports();

// An unresolved promise alone does not keep Node alive at the durable-dispatch
// window. The parent owns the bounded timeout and kills this child at the marker.
const crashKeepAlive = setInterval(() => {}, 1_000);
try {
  const api = await import("./scripts/lib/r22-diagnostic-transaction.mjs?fixture=billing-child-process-crash-child");
  const disclosure = await api.describeR22OfficialToolUsageDiagnosticTransaction(input.config);
  transactionSha256 = disclosure.transactionSha256;
  formatVersion = disclosure.transactionPlan.formatVersion;
  const transaction = await api.createR22OfficialToolUsageDiagnosticTransaction({ ...input.config,
    expectedDisclosureSha256: disclosure.transactionSha256 });
  const approval = await transaction.approve({ disclosureSha256: disclosure.transactionSha256 });
  await transaction.execute(approval);
  throw new Error("child-crash-window-not-reached");
} catch {
  process.stderr.write("diagnostic-crash-child-failed");
  process.exitCode = 1;
} finally {
  clearInterval(crashKeepAlive);
}
`;

const DIAGNOSTIC_RECOVERY_CHILD = String.raw`
import { createHash } from "node:crypto";
import fsPromises from "node:fs/promises";
import dns from "node:dns";
import http from "node:http";
import https from "node:https";
import net from "node:net";
import path from "node:path";
import tls from "node:tls";
import { syncBuiltinESMExports } from "node:module";

${DIAGNOSTIC_NETWORK_GUARD_SOURCE}

const input = JSON.parse(Buffer.from(process.argv[1], "base64url").toString("utf8"));
const credentialPath = path.resolve(input.credentialFile);
let credentialOpens = 0;
let environmentReads = 0;
let fetchCalls = 0;
const originalEnvironment = process.env;
process.env = new Proxy(originalEnvironment, {
  get(target, name, receiver) {
    if (name === "MATRIX_OASIS_R22_OPENAI_API_KEY") {
      environmentReads += 1;
      throw new Error("recovery-environment-credential-read-forbidden");
    }
    return Reflect.get(target, name, receiver);
  },
  getOwnPropertyDescriptor(target, name) {
    if (name === "MATRIX_OASIS_R22_OPENAI_API_KEY") return undefined;
    return Reflect.getOwnPropertyDescriptor(target, name);
  },
});
const originalOpen = fsPromises.open;
fsPromises.open = async (target, ...args) => {
  if (path.resolve(String(target)) === credentialPath) {
    credentialOpens += 1;
    throw new Error("recovery-credential-read-forbidden");
  }
  return originalOpen(target, ...args);
};
Object.defineProperty(globalThis, "fetch", { configurable: false, enumerable: true, writable: false,
  value: async () => {
    fetchCalls += 1;
    throw new Error("recovery-fetch-forbidden");
  } });
syncBuiltinESMExports();

try {
  const api = await import("./scripts/lib/r22-diagnostic-transaction.mjs?fixture=billing-child-process-recovery-child");
  const recovered = await api.recoverR22OfficialToolUsageDiagnosticTransaction(input.recoveryConfig, input.transactionSha256);
  const budgetJson = await fsPromises.readFile(path.join(input.fourthConfig.cognitionRunRoot, "host-budget.json"), "utf8");
  const fourthConfig = { ...input.fourthConfig,
    expectedHostBudgetSha256: "sha256:" + createHash("sha256").update(budgetJson).digest("hex") };
  const errors = [];
  for (const operation of [
    () => api.describeR22OfficialToolUsageDiagnosticTransaction(fourthConfig),
    () => api.createR22OfficialToolUsageDiagnosticTransaction({ ...fourthConfig,
      expectedDisclosureSha256: input.transactionSha256 }),
  ]) {
    try { await operation(); errors.push(null); }
    catch (error) { errors.push(/^R22_[A-Z_]+$/u.test(error?.message ?? "") ? error.message : "R22_DIAGNOSTIC_CHILD_REJECTED"); }
  }
  process.send?.({ kind: "recovery-complete", terminal: recovered.terminal, errors,
    credentialOpens, environmentReads, fetchCalls, networkTripwireHits }, () => process.exit(0));
} catch {
  process.stderr.write("diagnostic-recovery-child-failed");
  process.exitCode = 1;
}
`;

const DIAGNOSTIC_NETWORK_GUARD_CHILD = String.raw`
import dns from "node:dns";
import http from "node:http";
import https from "node:https";
import net from "node:net";
import tls from "node:tls";
import { syncBuiltinESMExports } from "node:module";

${DIAGNOSTIC_NETWORK_GUARD_SOURCE}
syncBuiltinESMExports();

let rejected = false;
try {
  await globalThis.fetch("http://127.0.0.1:49199/r22-public-invalid");
} catch {
  rejected = true;
}
process.send?.({ kind: "network-guard-complete", rejected, blockedBeforeConnect,
  networkTripwireHits }, () => process.exit(0));
`;

function spawnDiagnosticChild(source, input) {
  const environment = {};
  for (const key of CHILD_ENVIRONMENT_KEYS) {
    const value = process.env[key];
    if (typeof value === "string" && value.length > 0) environment[key] = value;
  }
  const stdout = [];
  const stderr = [];
  let outputBytes = 0;
  let outputLimitError = null;
  const child = spawn(process.execPath, ["--input-type=module", "--eval", source,
    Buffer.from(JSON.stringify(input), "utf8").toString("base64url")], {
    cwd: MODULE_ROOT,
    env: environment,
    stdio: ["ignore", "pipe", "pipe", "ipc"],
    windowsHide: true,
  });
  const capture = (chunks) => (chunk) => {
    const bytes = Buffer.from(chunk);
    outputBytes += bytes.length;
    if (outputBytes > CHILD_OUTPUT_LIMIT_BYTES) {
      outputLimitError ??= new Error(`diagnostic child output exceeded ${CHILD_OUTPUT_LIMIT_BYTES} bytes`);
      if (child.exitCode === null && child.signalCode === null) child.kill();
      return;
    }
    chunks.push(bytes);
  };
  child.stdout.on("data", capture(stdout));
  child.stderr.on("data", capture(stderr));
  const closed = new Promise((resolve) => {
    child.once("error", (error) => resolve({ code: null, signal: null, error }));
    child.once("close", (code, signal) => resolve({ code, signal, error: null }));
  });
  return { child, closed, outputLimitError: () => outputLimitError,
    output: () => ({ stdout: Buffer.concat(stdout).toString("utf8"), stderr: Buffer.concat(stderr).toString("utf8") }) };
}

async function stopDiagnosticChild(runner) {
  if (runner.child.exitCode === null && runner.child.signalCode === null) runner.child.kill();
  return runner.closed;
}

function registerDiagnosticChildCleanup(t, runner) {
  t.after(() => stopDiagnosticChild(runner));
}

function diagnosticChildFailure(runner, prefix, closed = {}) {
  return runner.outputLimitError() ?? new Error(
    `${prefix}: code=${closed.code ?? runner.child.exitCode} signal=${closed.signal ?? runner.child.signalCode} output=${JSON.stringify(runner.output())}`);
}

async function waitForDiagnosticChildMessage(runner, kind, timeoutMs = 30_000) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (action) => (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      runner.child.off("message", onMessage);
      action(value);
    };
    const onMessage = (message) => {
      if (message?.kind === kind) finish(resolve)(message);
    };
    const timer = setTimeout(async () => {
      if (settled) return;
      settled = true;
      runner.child.off("message", onMessage);
      const closed = await stopDiagnosticChild(runner);
      reject(diagnosticChildFailure(runner, `diagnostic child timed out before ${kind}`, closed));
    }, timeoutMs);
    runner.child.on("message", onMessage);
    runner.closed.then((closed) => {
      if (!settled) finish(() => reject(diagnosticChildFailure(runner, `diagnostic child exited before ${kind}`, closed)))();
    });
  });
}

async function waitForDiagnosticChildExit(runner, timeoutMs = 30_000) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(async () => {
      if (settled) return;
      settled = true;
      const closed = await stopDiagnosticChild(runner);
      reject(diagnosticChildFailure(runner, "diagnostic child did not exit", closed));
    }, timeoutMs);
    runner.closed.then((closed) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (closed.error || runner.outputLimitError()) reject(diagnosticChildFailure(runner, "diagnostic child failed", closed));
      else resolve({ code: closed.code, signal: closed.signal });
    });
  });
}

async function createInjected(f, fixture = transportFixture(), overrides = {}) {
  return createR22InjectedToolUsageDiagnosticTransaction(f.config, fixture, testOverrides(overrides));
}

async function execute(tx) {
  const approval = await tx.approve({ disclosureSha256: tx.disclosure.transactionSha256 });
  return tx.execute(approval);
}

function transactionRoot(f, transactionSha256) {
  return path.join(f.config.cognitionRunRoot, "diagnostics", transactionSha256.slice(7));
}

async function savedBudget(f) {
  return JSON.parse(await readFile(path.join(f.config.cognitionRunRoot, "host-budget.json"), "utf8"));
}

async function transportRecord(f, transactionSha256) {
  return JSON.parse(await readFile(path.join(transactionRoot(f, transactionSha256), "transport-record.json"), "utf8"));
}

async function allText(root) {
  let text = "";
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const target = path.join(root, entry.name);
    text += entry.isDirectory() ? await allText(target) : await readFile(target, "utf8");
  }
  return text;
}

async function treeSnapshot(root) {
  const result = {};
  const visit = async (directory, prefix = "") => {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
      const target = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        result[`${relative}/`] = null;
        await visit(target, relative);
      } else {
        result[relative] = await readFile(target, "utf8");
      }
    }
  };
  await visit(root);
  return result;
}

async function setupCliRoot(t) {
  const actualTemporaryRoot = path.resolve(path.parse(MODULE_ROOT).root, "tmp");
  const suffix = sha(`${process.pid}:${Date.now()}:${Math.random()}`).slice(7, 23);
  const cognitionRunRoot = path.join(actualTemporaryRoot, `r22dt-${suffix}-cognition`);
  const output = path.join(actualTemporaryRoot, `r22dt-${suffix}-output`);
  await mkdir(cognitionRunRoot);
  t.after(async () => {
    for (const target of [output, cognitionRunRoot]) {
      const found = await lstat(target).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
      if (found === null) continue;
      assert.equal(path.dirname(path.resolve(target)), actualTemporaryRoot);
      assert.equal(path.basename(target).startsWith(`r22dt-${suffix}-`), true);
      assert.equal(found.isDirectory(), true);
      assert.equal(found.isSymbolicLink(), false);
      assert.equal(await realpath(target), path.resolve(target));
      await rm(target, { recursive: true, force: false });
    }
  });
  const manifestJson = canonical(sessionManifest("official-once"));
  const budgetJson = canonical(hostBudget());
  await writeFile(path.join(cognitionRunRoot, "cognition-session-manifest.json"), manifestJson, { flag: "wx" });
  await writeFile(path.join(cognitionRunRoot, "host-budget.json"), budgetJson, { flag: "wx" });
  const args = [
    "--cognition-run-root", cognitionRunRoot,
    "--host-run-id", HOST_RUN_ID,
    "--session-manifest-sha256", sha(manifestJson),
    "--host-budget-sha256", sha(budgetJson),
    "--output", output,
  ];
  return { cognitionRunRoot, output, args };
}

async function assertRejectCode(action, code) {
  await assert.rejects(action, (error) => error?.message === code);
}

async function setupCredentialCli(t, contents = "deliberately-invalid-fixture") {
  const cli = await setupCliRoot(t);
  const credentialFile = `${cli.cognitionRunRoot}-credential.txt`;
  await writeFile(credentialFile, contents, { flag: "wx" });
  t.after(async () => {
    assert.equal(path.dirname(credentialFile), path.resolve(path.parse(MODULE_ROOT).root, "tmp"));
    assert.equal(path.basename(credentialFile).startsWith("r22dt-"), true);
    await rm(credentialFile, { force: true }); // Only the exact fixture file, never recursively.
  });
  const config = { temporaryRoot: path.dirname(cli.cognitionRunRoot), cognitionRunRoot: cli.cognitionRunRoot,
    hostRunId: HOST_RUN_ID, expectedSessionManifestSha256: sha(canonical(sessionManifest("official-once"))),
    expectedHostBudgetSha256: sha(canonical(hostBudget())), output: cli.output, credentialFile };
  return { ...cli, credentialFile, config, fileArgs: [...cli.args, "--credential-file", credentialFile] };
}

function forbidEnvironmentCredential(t) {
  const original = process.env;
  let reads = 0;
  const key = "MATRIX_OASIS_R22_OPENAI_API_KEY";
  process.env = new Proxy(original, {
    get(target, name, receiver) { if (name === key) { reads += 1; throw new Error("fixture-env-read-forbidden"); } return Reflect.get(target, name, receiver); },
    getOwnPropertyDescriptor(target, name) { if (name === key) return undefined; return Reflect.getOwnPropertyDescriptor(target, name); },
  });
  t.after(() => { process.env = original; });
  return () => reads;
}

function observeCredentialOpens(t, credentialFile, beforeOpen = async () => {}) {
  const originalOpen = fsPromises.open;
  let opens = 0;
  t.mock.method(fsPromises, "open", async (file, ...args) => {
    if (file === credentialFile) { opens += 1; await beforeOpen(); }
    return originalOpen(file, ...args);
  });
  syncBuiltinESMExports();
  t.after(() => { t.mock.restoreAll(); syncBuiltinESMExports(); });
  return () => opens;
}

test("credential wiring: file disclosure is stable, metadata-only and bound without secret or path", async (t) => {
  const f = await setupCredentialCli(t);
  const reads = forbidEnvironmentCredential(t), opens = observeCredentialOpens(t, f.credentialFile);
  const before = await treeSnapshot(f.cognitionRunRoot);
  const first = await runR22ToolUsageDiagnostic(["plan", ...f.fileArgs]);
  assert.equal(first.transactionPlan.formatVersion, "0.3.0");
  assert.deepEqual(first.transactionPlan.credentialSource, { kind: "pinned-file", identitySha256: first.credentialConfiguration.identitySha256 });
  assert.equal(first.credentialConfiguration.configured, true);
  for (let i = 0; i < 20; i += 1) {
    assert.equal(canonical(await runR22ToolUsageDiagnostic(["plan", ...f.fileArgs])), canonical(first));
  }
  for (const bait of [f.credentialFile, path.basename(f.credentialFile), "deliberately-invalid-fixture", sha("deliberately-invalid-fixture")]) {
    assert.equal(canonical(first).includes(bait), false);
  }
  assert.deepEqual([opens(), reads()], [0, 0]);
  assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
});

test("credential wiring: missing environment configuration fails before claim and budget", async (t) => {
  const f = await setupCliRoot(t), reads = forbidEnvironmentCredential(t);
  const before = await treeSnapshot(f.cognitionRunRoot);
  const disclosure = await runR22ToolUsageDiagnostic(["plan", ...f.args]);
  assert.equal(disclosure.transactionPlan.formatVersion, "0.2.0");
  assert.equal(disclosure.credentialConfiguration.configured, false);
  await assertRejectCode(() => runR22ToolUsageDiagnostic(["execute", ...f.args, "--approve-disclosure", disclosure.transactionSha256]), "R22_DIAGNOSTIC_CREDENTIAL_NOT_CONFIGURED");
  assert.equal(reads(), 0);
  assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
  await assert.rejects(lstat(f.output), { code: "ENOENT" });
});

test("credential wiring: wrong, legacy and changed-file approvals cannot consume the claim", async (t) => {
  const f = await setupCredentialCli(t), opens = observeCredentialOpens(t, f.credentialFile);
  const before = await treeSnapshot(f.cognitionRunRoot);
  const legacy = await runR22ToolUsageDiagnostic(["plan", ...f.args]);
  const disclosure = await runR22ToolUsageDiagnostic(["plan", ...f.fileArgs]);
  assert.notEqual(disclosure.transactionSha256, legacy.transactionSha256);
  for (const approval of [h("9"), legacy.transactionSha256]) {
    await assertRejectCode(() => runR22ToolUsageDiagnostic(["execute", ...f.fileArgs, "--approve-disclosure", approval]), "R22_APPROVAL_MISMATCH");
  }
  await assertRejectCode(() => createR22OfficialToolUsageDiagnosticTransaction(f.config), "R22_APPROVAL_MISMATCH");
  await writeFile(f.credentialFile, "changed-fixture-contents");
  const changed = await runR22ToolUsageDiagnostic(["plan", ...f.fileArgs]);
  assert.notEqual(changed.transactionSha256, disclosure.transactionSha256);
  await assertRejectCode(() => createR22OfficialToolUsageDiagnosticTransaction({ ...f.config, expectedDisclosureSha256: disclosure.transactionSha256 }), "R22_APPROVAL_MISMATCH");
  assert.equal(opens(), 0);
  assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
});

test("credential wiring: invalid file selection stops before directory or budget publication", async (t) => {
  const f = await setupCredentialCli(t), opens = observeCredentialOpens(t, f.credentialFile);
  const before = await treeSnapshot(f.cognitionRunRoot);
  const invalid = ["relative-key", f.cognitionRunRoot, `${f.credentialFile}.missing`, path.join(f.cognitionRunRoot, "nested-key")];
  for (const credentialFile of invalid) {
    await assertRejectCode(() => describeR22OfficialToolUsageDiagnosticTransaction({ ...f.config, credentialFile }), "R22_OFFICIAL_CREDENTIAL_FILE_UNAVAILABLE");
  }
  assert.equal(opens(), 0);
  assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
});

test("credential wiring: invalid contents are read only after dispatch, then recovery needs no credential", async (t) => {
  const f = await setupCredentialCli(t), reads = forbidEnvironmentCredential(t);
  let txRoot;
  const opens = observeCredentialOpens(t, f.credentialFile, async () => {
    assert.ok(txRoot);
    assert.equal(JSON.parse(await readFile(path.join(txRoot, "dispatch-record.json"), "utf8")).retryLimit, 0);
    const budget = JSON.parse(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8"));
    assert.equal(budget.entries.at(-1).state, "charged");
    assert.equal(budget.entries.at(-1).chargedMicrousd, 10_000);
  });
  const disclosure = await runR22ToolUsageDiagnostic(["plan", ...f.fileArgs]);
  const tx = await createR22OfficialToolUsageDiagnosticTransaction({ ...f.config, expectedDisclosureSha256: disclosure.transactionSha256 });
  txRoot = path.join(f.cognitionRunRoot, "diagnostics", disclosure.transactionSha256.slice(7));
  assert.equal(opens(), 0);
  const result = await execute(tx); await tx.close();
  assert.equal(result.terminal.formatVersion, "0.3.0");
  assert.equal(result.terminal.transportRequestCount, 0);
  assert.equal(result.terminal.chargedMicrousd, 10_000);
  assert.equal(result.terminal.qualificationEligible, false);
  assert.deepEqual([opens(), reads()], [1, 0]);
  await rm(f.credentialFile);
  const { credentialFile: _file, ...baseConfig } = f.config;
  const budgetJson = await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8");
  const recoveryConfig = { ...baseConfig, expectedHostBudgetSha256: sha(budgetJson) };
  for (let i = 0; i < 20; i += 1) {
    assert.equal(canonical(await recoverR22OfficialToolUsageDiagnosticTransaction(recoveryConfig, result.transactionSha256)), canonical(result));
  }
  assert.equal(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8"), budgetJson);
  assert.deepEqual([opens(), reads()], [1, 0]);
  const blockedOutput = `${f.output}-blocked`;
  await assertRejectCode(() => createR22OfficialToolUsageDiagnosticTransaction({ ...recoveryConfig, output: blockedOutput }), "R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
  await assert.rejects(lstat(blockedOutput), { code: "ENOENT" });
});

test("credential wiring: file replacement after creation fails without a request and never falls back to env", async (t) => {
  const f = await setupCredentialCli(t), reads = forbidEnvironmentCredential(t);
  const disclosure = await runR22ToolUsageDiagnostic(["plan", ...f.fileArgs]);
  const tx = await createR22OfficialToolUsageDiagnosticTransaction({ ...f.config, expectedDisclosureSha256: disclosure.transactionSha256 });
  await rm(f.credentialFile); await writeFile(f.credentialFile, "new-owned-fixture", { flag: "wx" });
  const result = await execute(tx); await tx.close();
  const transport = JSON.parse(await readFile(path.join(f.output, "transport-record.json"), "utf8"));
  assert.equal(transport.status, "credential_unavailable");
  assert.equal(result.terminal.realRequestCount, 0);
  assert.equal(reads(), 0);
});

test("credential wiring: official factory refuses caller credential capabilities", async (t) => {
  const f = await setupCredentialCli(t);
  let invoked = 0;
  const before = await treeSnapshot(f.cognitionRunRoot);
  for (const [key, value] of [["readCredential", () => { invoked += 1; }], ["apiKey", "fixture"], ["sender", () => { invoked += 1; }], ["credentialSource", { kind: "pinned-file", identitySha256: h("a") }]]) {
    await assert.rejects(createR22OfficialToolUsageDiagnosticTransaction({ ...f.config, [key]: value }));
  }
  assert.equal(invoked, 0);
  assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
});

test("credential wiring: local intercepted native transport reads the pinned fixture once after dispatch", async (t) => {
  // This is an offline unit interception before module load, not a real request
  // or qualification. No production sender accepts this test replacement.
  const secret = ["sk", "fixture".repeat(5)].join("-");
  const f = await setupCredentialCli(t, secret), envReads = forbidEnvironmentCredential(t);
  let sends = 0, root;
  const opens = observeCredentialOpens(t, f.credentialFile, async () => {
    assert.ok(root);
    assert.equal(JSON.parse(await readFile(path.join(root, "dispatch-record.json"), "utf8")).requestLimit, 1);
    assert.equal(JSON.parse(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8")).entries.at(-1).state, "charged");
  });
  const fixed = createNpcCognitionToolUsageDiagnosticPlan();
  t.mock.method(globalThis, "fetch", async (endpoint, options) => {
    sends += 1;
    assert.equal(endpoint, JSON.parse(fixed.callPlanJson).endpoint);
    assert.equal(options.headers.authorization, `Bearer ${secret}`);
    assert.equal(options.body, fixed.providerRequestJson);
    assert.equal(options.redirect, "error");
    assert.equal(opens(), 1);
    return new Response(validResponseBytes(), { status: 200, headers: { "content-type": "application/json" } });
  });
  const localOnly = await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=credential-transport");
  const disclosure = await localOnly.describeR22OfficialToolUsageDiagnosticTransaction(f.config);
  const tx = await localOnly.createR22OfficialToolUsageDiagnosticTransaction({ ...f.config, expectedDisclosureSha256: disclosure.transactionSha256 });
  root = path.join(f.cognitionRunRoot, "diagnostics", disclosure.transactionSha256.slice(7));
  const approval = await tx.approve({ disclosureSha256: disclosure.transactionSha256 });
  assert.deepEqual([opens(), sends, envReads()], [0, 0, 0]);
  const results = await Promise.all(Array.from({ length: 20 }, () => tx.execute(approval)));
  await tx.close();
  assert.deepEqual([opens(), sends, envReads()], [1, 1, 0]);
  assert.equal(results[0].terminal.state, "observed");
  assert.equal(results[0].terminal.formatVersion, "0.3.0");
  assert.equal(results[0].terminal.qualificationEligible, false);
  for (const result of results) assert.equal(canonical(result), canonical(results[0]));
  const persisted = canonical(await treeSnapshot(f.cognitionRunRoot)) + canonical(await treeSnapshot(f.output));
  for (const bait of [secret, sha(secret), f.credentialFile, path.basename(f.credentialFile), "fixture-dialogue-placeholder"]) assert.equal(persisted.includes(bait), false);
  const { credentialFile: _credential, ...config } = f.config;
  config.expectedHostBudgetSha256 = sha(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8"));
  await rm(f.credentialFile);
  assert.equal(canonical(await localOnly.recoverR22OfficialToolUsageDiagnosticTransaction(config, results[0].transactionSha256)), canonical(results[0]));
  assert.deepEqual([opens(), sends, envReads()], [1, 1, 0]);
});

test("credential wiring: cancelled v0.3 recovers without opening a key, and source tampering fails closed", async (t) => {
  const f = await setupCredentialCli(t), opens = observeCredentialOpens(t, f.credentialFile), envReads = forbidEnvironmentCredential(t);
  const disclosure = await runR22ToolUsageDiagnostic(["plan", ...f.fileArgs]);
  const tx = await createR22OfficialToolUsageDiagnosticTransaction({ ...f.config, expectedDisclosureSha256: disclosure.transactionSha256 });
  const cancelled = await tx.cancel(); await tx.close();
  const { credentialFile: _file, ...config } = f.config;
  await rm(f.credentialFile);
  const recovered = await recoverR22OfficialToolUsageDiagnosticTransaction(config, cancelled.transactionSha256);
  assert.equal(canonical(recovered), canonical(cancelled));
  assert.equal(cancelled.terminal.chargedMicrousd, 0);
  assert.deepEqual([opens(), envReads()], [0, 0]);
  const planPath = path.join(f.cognitionRunRoot, "diagnostics", cancelled.transactionSha256.slice(7), "transaction-plan.json");
  const original = await readFile(planPath, "utf8"), forged = JSON.parse(original);
  forged.credentialSource.identitySha256 = h("a");
  await writeFile(planPath, canonical(forged));
  await assert.rejects(recoverR22OfficialToolUsageDiagnosticTransaction(config, cancelled.transactionSha256));
  assert.equal(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8"), canonical(hostBudget()));
  assert.deepEqual([opens(), envReads()], [0, 0]);
});

test("credential wiring: recovery rejects file-selection arguments instead of reading a credential", async (t) => {
  const f = await setupCredentialCli(t), opens = observeCredentialOpens(t, f.credentialFile);
  await assertRejectCode(() => runR22ToolUsageDiagnostic(["recover", ...f.fileArgs, "--transaction-sha256", h("a")]), "R22_CLI_ARGUMENT_INVALID");
  assert.equal(opens(), 0);
});

test("credential wiring: v0.3 plan crash windows never require secrets or reopen a claim", async (t) => {
  for (const point of ["empty-root", "plan-staged", "plan-published"]) await t.test(point, async (child) => {
    const f = await setupCredentialCli(child), opens = observeCredentialOpens(child, f.credentialFile), envReads = forbidEnvironmentCredential(child);
    const disclosure = await runR22ToolUsageDiagnostic(["plan", ...f.fileArgs]);
    const root = path.join(f.cognitionRunRoot, "diagnostics", disclosure.transactionSha256.slice(7));
    const originalMkdir = fsPromises.mkdir, originalRename = fsPromises.rename;
    let stopped = false;
    child.mock.method(fsPromises, "mkdir", async (target, ...args) => {
      const result = await originalMkdir(target, ...args);
      if (!stopped && point === "empty-root" && target === root) { stopped = true; throw new Error("fixture-stop"); }
      return result;
    });
    child.mock.method(fsPromises, "rename", async (from, to, ...args) => {
      const match = !stopped && to === path.join(root, "transaction-plan.json");
      if (match && point === "plan-staged") { stopped = true; throw new Error("fixture-stop"); }
      const result = await originalRename(from, to, ...args);
      if (match && point === "plan-published") { stopped = true; throw new Error("fixture-stop"); }
      return result;
    });
    syncBuiltinESMExports();
    child.after(() => { child.mock.restoreAll(); syncBuiltinESMExports(); });
    let sends = 0;
    child.mock.method(globalThis, "fetch", async () => { sends += 1; throw new Error("fixture-network-forbidden"); });
    // The sealed adapter captures its filesystem at module load. Import after
    // interception so the intended crash actually reaches that private path.
    const localOnly = point === "empty-root"
      ? await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=credential-empty-root")
      : point === "plan-staged"
        ? await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=credential-plan-staged")
        : await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=credential-plan-published");
    let unexpectedTransaction;
    try {
      await assert.rejects(async () => {
        unexpectedTransaction = await localOnly.createR22OfficialToolUsageDiagnosticTransaction({ ...f.config, expectedDisclosureSha256: disclosure.transactionSha256 });
      });
    } finally { await unexpectedTransaction?.close(); }
    assert.equal(stopped, true);
    const { credentialFile: _file, ...config } = f.config;
    await rm(f.credentialFile);
    if (point === "empty-root") {
      // No source identity was ever persisted: do not guess it from a missing
      // key or manufacture a cancellation. Keep the unusable claim closed.
      await assert.rejects(recoverR22OfficialToolUsageDiagnosticTransaction(config, disclosure.transactionSha256));
      assert.deepEqual(await readdir(root), []);
      await assert.rejects(lstat(f.output), { code: "ENOENT" });
    } else {
      const recovered = await recoverR22OfficialToolUsageDiagnosticTransaction(config, disclosure.transactionSha256);
      assert.equal(recovered.terminal.formatVersion, "0.3.0");
      assert.equal(recovered.terminal.state, "cancelled");
      assert.equal(recovered.terminal.realRequestCount, 0);
      assert.equal(recovered.terminal.chargedMicrousd, 0);
      assert.equal(canonical(await recoverR22OfficialToolUsageDiagnosticTransaction(config, disclosure.transactionSha256)), canonical(recovered));
    }
    assert.equal(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8"), canonical(hostBudget()));
    assert.deepEqual([opens(), envReads(), sends], [0, 0, 0]);
    await assertRejectCode(() => createR22OfficialToolUsageDiagnosticTransaction({ ...config, output: `${f.output}-blocked` }), "R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
    assert.equal((await readdir(path.join(f.cognitionRunRoot, "diagnostics"))).length, 1);
  });
});

async function setupAdditionalDiagnostic(t, { legacy = false } = {}) {
  const f = await setupCredentialCli(t);
  const { credentialFile: _file, ...legacyConfig } = f.config;
  const firstConfig = legacy ? legacyConfig : f.config;
  const first = await describeR22OfficialToolUsageDiagnosticTransaction(firstConfig);
  const tx = await createR22OfficialToolUsageDiagnosticTransaction({ ...firstConfig, ...(legacy ? {} : { expectedDisclosureSha256: first.transactionSha256 }) });
  let previous;
  try { previous = await execute(tx); } finally { await tx.close(); }
  assert.equal(previous.terminal.realRequestCount, 0);
  assert.equal(previous.terminal.state, "transport_failed");
  const output = `${f.output}-additional`;
  t.after(async () => {
    const found = await lstat(output).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
    if (!found) return;
    assert.equal(path.dirname(output), f.config.temporaryRoot);
    assert.equal(path.basename(output).startsWith(OWNED_PREFIX), true);
    assert.equal(found.isSymbolicLink(), false);
    assert.equal(found.isDirectory(), true);
    assert.equal(await realpath(output), output);
    await rm(output, { recursive: true, force: false });
  });
  const previousRoot = path.join(f.cognitionRunRoot, "diagnostics", first.transactionSha256.slice(7));
  const budgetJson = await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8");
  const next = { ...f.config, output, expectedHostBudgetSha256: sha(budgetJson), previousTransactionSha256: first.transactionSha256 };
  const previousTree = await treeSnapshot(previousRoot);
  const previousBudgetEntry = JSON.parse(budgetJson).entries.find((entry) => entry.authoritySessionSha256 === JSON.parse(previousTree["dispatch-record.json"]).budgetAuthoritySha256);
  assert.equal(previousBudgetEntry.chargedMicrousd, 10_000);
  return { ...f, previous, previousRoot, previousTree, previousBudgetEntry, budgetJson, next };
}

function assertBudgetPrefixPreserved(budget, previousBudgetJson) {
  const previous = JSON.parse(previousBudgetJson);
  for (const entry of previous.entries) assert.deepEqual(budget.entries.find((item) => item.authoritySessionSha256 === entry.authoritySessionSha256), entry);
  return budget.entries.filter((item) => !previous.entries.some((entry) => entry.authoritySessionSha256 === item.authoritySessionSha256));
}

async function additionalRecoveryConfig(config) {
  const { credentialFile: _file, previousTransactionSha256: _previous, billingAfterTransactionSha256: _billing, expectedDisclosureSha256: _approval, ...source } = config;
  return { ...source, expectedHostBudgetSha256: sha(await readFile(path.join(source.cognitionRunRoot, "host-budget.json"), "utf8")) };
}

async function setupBillingAdditional(t, localOnly, { previousProfile = "tool-usage", expectedState = "observed", legacyFirst = false } = {}) {
  const f = await setupAdditionalDiagnostic(t, { legacy: legacyFirst });
  const secret = ["sk", "billing-grant-fixture".repeat(3)].join("-");
  await writeFile(f.credentialFile, secret);
  const previousConfig = previousProfile === "tool-usage" ? f.next : { ...f.next, captureProfile: previousProfile };
  const disclosure = await localOnly.describeR22OfficialToolUsageDiagnosticTransaction(previousConfig);
  const tx = await localOnly.createR22OfficialToolUsageDiagnosticTransaction({ ...previousConfig, expectedDisclosureSha256: disclosure.transactionSha256 });
  let second;
  try { second = await execute(tx); } finally { await tx.close(); }
  assert.equal(second.terminal.state, expectedState);
  const secondRoot = path.join(f.cognitionRunRoot, "diagnostics", second.transactionSha256.slice(7));
  const secondTree = await treeSnapshot(secondRoot);
  const billingBudgetJson = await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8");
  const output = `${f.output}-billing`;
  t.after(async () => {
    const found = await lstat(output).catch((error) => error?.code === "ENOENT" ? null : Promise.reject(error));
    if (!found) return;
    assert.equal(path.dirname(output), f.config.temporaryRoot);
    assert.equal(path.basename(output).startsWith(OWNED_PREFIX), true);
    assert.equal(found.isSymbolicLink(), false);
    assert.equal(found.isDirectory(), true);
    assert.equal(await realpath(output), output);
    await rm(output, { recursive: true, force: false });
  });
  return { ...f, second, secondRoot, secondTree, billingBudgetJson, secret,
    billingConfig: { ...f.config, output, expectedHostBudgetSha256: sha(billingBudgetJson), captureProfile: "billing",
      billingAfterTransactionSha256: second.transactionSha256 } };
}

test("billing additional authorization: unique v0.5 plan is metadata-only and old approval cannot consume it", async (t) => {
  let sends = 0;
  t.mock.method(globalThis, "fetch", async () => {
    sends += 1;
    return new Response(validResponseBytes(), { headers: { "content-type": "application/json" } });
  });
  const localOnly = await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=billing-additional-plan");
  const f = await setupBillingAdditional(t, localOnly);
  const opens = observeCredentialOpens(t, f.credentialFile), envReads = forbidEnvironmentCredential(t);
  const before = await treeSnapshot(f.cognitionRunRoot);
  const args = [...f.fileArgs];
  args[args.indexOf("--output") + 1] = f.billingConfig.output;
  args[args.indexOf("--host-budget-sha256") + 1] = f.billingConfig.expectedHostBudgetSha256;
  args.push("--capture-profile", "billing", "--billing-after-transaction", f.second.transactionSha256);
  const disclosure = await runR22ToolUsageDiagnostic(["plan", ...args]);
  assert.equal(disclosure.transactionPlan.formatVersion, "0.5.0");
  assert.equal(disclosure.transactionPlan.billingAuthorization.kind, "single-billing-observation");
  assert.equal(disclosure.transactionPlan.billingAuthorization.officialDiagnosticLimit, 3);
  assert.equal(disclosure.transactionPlan.billingAuthorization.previousObservationSha256, sha(f.secondTree["observation-record.json"]));
  assert.equal(disclosure.transactionPlan.billingAuthorization.previousTransactionSha256, f.second.transactionSha256);
  assert.equal(disclosure.diagnosticPlan.providerRequestJson, createNpcCognitionToolUsageDiagnosticPlan().providerRequestJson);
  for (let i = 0; i < 20; i += 1) assert.equal(canonical(await runR22ToolUsageDiagnostic(["plan", ...args])), canonical(disclosure));
  for (const approval of [undefined, f.previous.transactionSha256, f.second.transactionSha256, disclosure.diagnosticPlan.diagnosticApprovalSha256]) {
    await assertRejectCode(() => localOnly.createR22OfficialToolUsageDiagnosticTransaction({ ...f.billingConfig,
      ...(approval ? { expectedDisclosureSha256: approval } : {}) }), "R22_APPROVAL_MISMATCH");
  }
  await assertRejectCode(() => runR22ToolUsageDiagnostic(["recover", ...args, "--transaction-sha256", disclosure.transactionSha256]), "R22_CLI_ARGUMENT_INVALID");
  assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
  assert.deepEqual([sends, opens(), envReads()], [1, 0, 0]); // One intercepted predecessor only.
});

test("billing additional authorization: 20 concurrent callers, immutable prefix, offline recovery and no fourth claim", async (t) => {
  let sends = 0;
  t.mock.method(globalThis, "fetch", async (_endpoint, options) => {
    sends += 1;
    assert.equal(options.body, createNpcCognitionBillingDiagnosticPlan().providerRequestJson);
    return new Response(sends === 1 ? validResponseBytes() : billingResponseBytes(), { headers: { "content-type": "application/json" } });
  });
  const localOnly = await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=billing-additional-execute");
  const f = await setupBillingAdditional(t, localOnly);
  const opens = observeCredentialOpens(t, f.credentialFile), envReads = forbidEnvironmentCredential(t);
  const disclosure = await localOnly.describeR22OfficialToolUsageDiagnosticTransaction(f.billingConfig);
  const competitors = await Promise.allSettled(Array.from({ length: 20 }, () => localOnly.createR22OfficialToolUsageDiagnosticTransaction({
    ...f.billingConfig, expectedDisclosureSha256: disclosure.transactionSha256 })));
  const winners = competitors.filter((item) => item.status === "fulfilled");
  assert.equal(winners.length, 1);
  const tx = winners[0].value;
  await assertRejectCode(() => tx.approve({ disclosureSha256: f.second.transactionSha256 }), "R22_APPROVAL_MISMATCH");
  const approval = await tx.approve({ disclosureSha256: disclosure.transactionSha256 });
  assert.deepEqual([sends, opens(), envReads()], [1, 0, 0]);
  const results = await Promise.all(Array.from({ length: 20 }, () => tx.execute(approval)));
  await tx.close();
  for (const result of results) assert.equal(canonical(result), canonical(results[0]));
  assert.equal(results[0].terminal.state, "observed");
  assert.equal(results[0].terminal.formatVersion, "0.5.0");
  assert.equal(results[0].terminal.qualificationEligible, false);
  assert.deepEqual([sends, opens(), envReads()], [2, 1, 0]); // All requests intercepted; no provider traffic.
  const budgetJson = await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8");
  assert.deepEqual(assertBudgetPrefixPreserved(JSON.parse(budgetJson), f.billingBudgetJson).map((entry) => [entry.state, entry.chargedMicrousd]), [["charged", 10000]]);
  assert.deepEqual(await treeSnapshot(f.previousRoot), f.previousTree);
  assert.deepEqual(await treeSnapshot(f.secondRoot), f.secondTree);
  assert.equal((await auditOfficialFixture(f)).size, 3);
  await rm(f.credentialFile);
  const recovery = await additionalRecoveryConfig(f.billingConfig);
  for (let i = 0; i < 20; i += 1) assert.equal(canonical(await localOnly.recoverR22OfficialToolUsageDiagnosticTransaction(recovery, results[0].transactionSha256)), canonical(results[0]));
  for (const [config, expected] of [[f.config, f.previous], [f.next, f.second]]) {
    assert.equal(canonical(await localOnly.recoverR22OfficialToolUsageDiagnosticTransaction(await additionalRecoveryConfig(config), expected.transactionSha256)), canonical(expected));
  }
  for (const captureProfile of ["billing", "tool-usage"]) {
    const fourth = { ...f.billingConfig, expectedHostBudgetSha256: sha(budgetJson), output: `${f.billingConfig.output}-fourth`, captureProfile };
    await assert.rejects(localOnly.describeR22OfficialToolUsageDiagnosticTransaction(fourth), /^Error: R22_/u);
    await assert.rejects(localOnly.createR22OfficialToolUsageDiagnosticTransaction({ ...fourth, expectedDisclosureSha256: disclosure.transactionSha256 }), /^Error: R22_/u);
  }
  assert.deepEqual([sends, opens(), envReads()], [2, 1, 0]);
  assert.equal(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8"), budgetJson);
  assert.equal((await readdir(path.join(f.cognitionRunRoot, "diagnostics"))).length, 3);
  const persisted = canonical(await treeSnapshot(f.cognitionRunRoot)) + canonical(await treeSnapshot(f.billingConfig.output));
  for (const bait of [f.secret, sha(f.secret), f.credentialFile, "fixture-dialogue-placeholder", "PRIVATE_BILLING_KEY", "PRIVATE_BILLING_VALUE", "998877.25"]) assert.equal(persisted.includes(bait), false);
});

test("billing additional authorization: eligibility, capacity and exact source fail closed before claim", async (t) => {
  let sends = 0;
  t.mock.method(globalThis, "fetch", async () => { sends += 1; return new Response(validResponseBytes(), { headers: { "content-type": "application/json" } }); });
  const localOnly = await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=billing-additional-rejection");
  for (const attack of ["missing-profile", "wrong-profile", "missing-file", "old-flag-also", "first-predecessor", "wrong-predecessor", "changed-manifest",
    "refunded-first", "refunded-second", "missing-observation", "missing-first", "entry-limit", "total-limit", "active-reservation", "fourth-directory"]) {
    await t.test(attack, async (child) => {
      const f = await setupBillingAdditional(child, localOnly);
      const opens = observeCredentialOpens(child, f.credentialFile), envReads = forbidEnvironmentCredential(child), startSends = sends;
      const config = { ...f.billingConfig };
      if (attack === "missing-profile") delete config.captureProfile;
      else if (attack === "wrong-profile") config.captureProfile = "tool-usage";
      else if (attack === "missing-file") delete config.credentialFile;
      else if (attack === "old-flag-also") config.previousTransactionSha256 = f.previous.transactionSha256;
      else if (attack === "first-predecessor") config.billingAfterTransactionSha256 = f.previous.transactionSha256;
      else if (attack === "wrong-predecessor") config.billingAfterTransactionSha256 = h("a");
      else if (attack === "changed-manifest") config.expectedSessionManifestSha256 = h("a");
      else if (attack === "missing-observation") await rm(path.join(f.secondRoot, "observation-record.json"));
      else if (attack === "missing-first") await rm(path.join(f.previousRoot, "transaction-plan.json"));
      else if (attack === "fourth-directory") await mkdir(path.join(f.cognitionRunRoot, "diagnostics", "9".repeat(64)));
      else {
        const budget = JSON.parse(f.billingBudgetJson);
        if (attack.startsWith("refunded-")) {
          const dispatch = JSON.parse((attack === "refunded-first" ? f.previousTree : f.secondTree)["dispatch-record.json"]);
          Object.assign(budget.entries.find((entry) => entry.authoritySessionSha256 === dispatch.budgetAuthoritySha256), { state: "released", chargedMicrousd: 0 });
        } else if (attack === "entry-limit") for (let i = 1; i <= 97; i += 1) budget.entries.push(historicalEntry(i, "charged", 0));
        else if (attack === "total-limit") {
          budget.entries.find((entry) => entry.authoritySessionSha256 === historicalEntry(0).authoritySessionSha256).chargedMicrousd = 10000;
          for (let i = 1; i <= 97; i += 1) budget.entries.push(historicalEntry(i, "charged", 10000));
          assert.equal(budget.entries.reduce((sum, entry) => sum + entry.chargedMicrousd, 0), 1000000);
        }
        else budget.entries.push(historicalEntry(1, "reserved", 0));
        budget.entries.sort((a, b) => a.authoritySessionSha256 < b.authoritySessionSha256 ? -1 : 1);
        await writeFile(path.join(f.cognitionRunRoot, "host-budget.json"), canonical(budget));
        config.expectedHostBudgetSha256 = sha(canonical(budget));
      }
      const before = await treeSnapshot(f.cognitionRunRoot);
      if (["entry-limit", "total-limit"].includes(attack)) await assertRejectCode(() => localOnly.describeR22OfficialToolUsageDiagnosticTransaction(config), "R22_BUDGET_EXHAUSTED");
      else await assert.rejects(localOnly.describeR22OfficialToolUsageDiagnosticTransaction(config), /^Error: R22_/u);
      await assert.rejects(localOnly.createR22OfficialToolUsageDiagnosticTransaction({ ...config, expectedDisclosureSha256: h("a") }), /^Error: R22_/u);
      assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
      assert.deepEqual([sends - startSends, opens(), envReads()], [0, 0, 0]);
      await assert.rejects(lstat(config.output), { code: "ENOENT" });
    });
  }
});

test("billing additional authorization: predecessor credential source cannot change under a fresh disclosure", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(validResponseBytes(), { headers: { "content-type": "application/json" } }));
  const localOnly = await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=billing-additional-credential-source");
  for (const change of ["different-file", "same-file-changed"]) await t.test(change, async (child) => {
    const f = await setupBillingAdditional(child, localOnly), config = { ...f.billingConfig };
    if (change === "different-file") {
      config.credentialFile = `${f.credentialFile}.alternate`;
      await writeFile(config.credentialFile, f.secret, { flag: "wx" });
      child.after(async () => {
        assert.equal(path.dirname(config.credentialFile), f.config.temporaryRoot);
        assert.equal(path.basename(config.credentialFile).startsWith(OWNED_PREFIX), true);
        await rm(config.credentialFile, { force: true });
      });
    } else await writeFile(config.credentialFile, `${f.secret}-changed`);
    const opens = observeCredentialOpens(child, config.credentialFile), envReads = forbidEnvironmentCredential(child);
    const before = await treeSnapshot(f.cognitionRunRoot);
    await assertRejectCode(() => localOnly.describeR22OfficialToolUsageDiagnosticTransaction(config), "R22_DIAGNOSTIC_BILLING_AUTHORIZATION_INVALID");
    await assertRejectCode(() => localOnly.createR22OfficialToolUsageDiagnosticTransaction({ ...config, expectedDisclosureSha256: h("a") }), "R22_DIAGNOSTIC_BILLING_AUTHORIZATION_INVALID");
    assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
    assert.deepEqual([opens(), envReads()], [0, 0]);
    await assert.rejects(lstat(config.output), { code: "ENOENT" });
  });
});

test("billing additional authorization: cancellation, zero request and response failure consume the final claim", async (t) => {
  let currentResponse, sends = 0;
  t.mock.method(globalThis, "fetch", async () => { sends += 1; return currentResponse(); });
  const localOnly = await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=billing-additional-terminal");
  for (const outcome of ["cancelled", "credential_unavailable", "http_error", "network_ambiguous"]) await t.test(outcome, async (child) => {
    currentResponse = () => new Response(validResponseBytes(), { headers: { "content-type": "application/json" } });
    const f = await setupBillingAdditional(child, localOnly), startSends = sends;
    currentResponse = () => { if (outcome === "network_ambiguous") throw new Error("fixture-only"); return new Response(null, { status: 500 }); };
    const opens = observeCredentialOpens(child, f.credentialFile, async () => { if (outcome === "credential_unavailable") throw new Error("fixture-read-unavailable"); }),
      envReads = forbidEnvironmentCredential(child);
    const disclosure = await localOnly.describeR22OfficialToolUsageDiagnosticTransaction(f.billingConfig);
    const tx = await localOnly.createR22OfficialToolUsageDiagnosticTransaction({ ...f.billingConfig, expectedDisclosureSha256: disclosure.transactionSha256 });
    const result = outcome === "cancelled" ? await tx.cancel() : await execute(tx);
    await tx.close();
    assert.equal(result.terminal.state, outcome === "cancelled" ? "cancelled" : "transport_failed");
    assert.equal(result.terminal.chargedMicrousd, outcome === "cancelled" ? 0 : 10000);
    if (outcome !== "cancelled") assert.equal((await transportRecord({ config: f.config }, result.transactionSha256)).status, outcome);
    const before = await treeSnapshot(f.cognitionRunRoot), recovery = await additionalRecoveryConfig(f.billingConfig);
    await rm(f.credentialFile);
    assert.equal(canonical(await localOnly.recoverR22OfficialToolUsageDiagnosticTransaction(recovery, result.transactionSha256)), canonical(result));
    assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
    assert.equal((await auditOfficialFixture(f)).size, outcome === "cancelled" ? 2 : 3);
    await assertRejectCode(() => localOnly.describeR22OfficialToolUsageDiagnosticTransaction({ ...f.billingConfig, expectedHostBudgetSha256: recovery.expectedHostBudgetSha256,
      output: `${f.billingConfig.output}-fourth` }), "R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
    assert.deepEqual([sends - startSends, opens(), envReads()], [outcome === "cancelled" || outcome === "credential_unavailable" ? 0 : 1, outcome === "cancelled" ? 0 : 1, 0]);
  });
});

test("billing additional authorization: replaced or fully re-signed predecessor cannot use stale approval", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(validResponseBytes(), { headers: { "content-type": "application/json" } }));
  const localOnly = await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=billing-additional-drift");
  for (const attack of ["replaced-identical", "re-signed-after-claim", "re-signed-before-claim"]) await t.test(attack, async (child) => {
    const f = await setupBillingAdditional(child, localOnly);
    const opens = observeCredentialOpens(child, f.credentialFile), envReads = forbidEnvironmentCredential(child);
    const disclosure = await localOnly.describeR22OfficialToolUsageDiagnosticTransaction(f.billingConfig);
    const tx = attack === "re-signed-before-claim" ? null : await localOnly.createR22OfficialToolUsageDiagnosticTransaction({ ...f.billingConfig, expectedDisclosureSha256: disclosure.transactionSha256 });
    const token = tx ? await tx.approve({ disclosureSha256: disclosure.transactionSha256 }) : null;
    if (attack === "replaced-identical") {
      const file = path.join(f.secondRoot, "observation-record.json"), value = await readFile(file, "utf8");
      await rename(file, `${file}.displaced`); await writeFile(file, value, { flag: "wx" }); await rm(`${file}.displaced`);
    } else {
      const observation = JSON.parse(f.secondTree["observation-record.json"]);
      const counter = observation.observation.paths.find((item) => item.counterStatus === "captured");
      assert.equal(counter?.path, "image_gen.total_tokens");
      counter.value += 1;
      await writeFile(path.join(f.secondRoot, "observation-record.json"), canonical(observation));
      const terminal = JSON.parse(f.secondTree["terminal-record.json"]);
      terminal.observationRecordSha256 = sha(canonical(observation));
      await writeFile(path.join(f.secondRoot, "terminal-record.json"), canonical(terminal));
    }
    if (!tx) {
      await assertRejectCode(() => localOnly.createR22OfficialToolUsageDiagnosticTransaction({ ...f.billingConfig, expectedDisclosureSha256: disclosure.transactionSha256 }), "R22_APPROVAL_MISMATCH");
      assert.notEqual((await localOnly.describeR22OfficialToolUsageDiagnosticTransaction(f.billingConfig)).transactionSha256, disclosure.transactionSha256);
    } else {
      await assert.rejects(tx.execute(token), /^Error: R22_/u); await tx.close();
      if (attack === "re-signed-after-claim") await assert.rejects(localOnly.recoverR22OfficialToolUsageDiagnosticTransaction(await additionalRecoveryConfig(f.billingConfig), disclosure.transactionSha256), /^Error: R22_/u);
    }
    assert.equal(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8"), f.billingBudgetJson);
    assert.deepEqual([opens(), envReads()], [0, 0]);
    await assert.rejects(lstat(f.billingConfig.output), { code: "ENOENT" });
  });
});

test("billing additional authorization: durable crash windows preserve the prefix and never replay a request", async (t) => {
  const originalMkdir = fsPromises.mkdir, originalRename = fsPromises.rename, originalOpen = fsPromises.open;
  let fault = null, root = null, output = null, account = null, stopped = false, sends = 0, billingResponse = false;
  t.mock.method(globalThis, "fetch", async () => {
    sends += 1;
    return new Response(billingResponse ? billingResponseBytes() : validResponseBytes(), { headers: { "content-type": "application/json" } });
  });
  t.mock.method(fsPromises, "mkdir", async (target, ...args) => {
    const result = await originalMkdir(target, ...args);
    if (!stopped && fault === "empty-root" && target === root) { stopped = true; throw new Error("fixture-stop"); }
    return result;
  });
  t.mock.method(fsPromises, "open", async (target, ...args) => {
    // The budget store captures its own operations before this module is
    // imported. Stop at the next transaction write, and prove reserve already
    // reached disk, instead of pretending its internal rename was intercepted.
    if (!stopped && fault === "budget-reserved" && target === path.join(root, ".dispatch-record.json.pending")) {
      const budget = JSON.parse(await readFile(path.join(account, "host-budget.json"), "utf8"));
      assert.equal(budget.entries.filter((entry) => entry.state === "reserved").length, 1);
      stopped = true; throw new Error("fixture-stop");
    }
    return originalOpen(target, ...args);
  });
  t.mock.method(fsPromises, "rename", async (from, to, ...args) => {
    const expected = fault === "output-published" ? output
      : ["plan-staged", "plan-published"].includes(fault) ? path.join(root, "transaction-plan.json")
        : fault === "approval-published" ? path.join(root, "approval-record.json")
          : fault === "dispatch-published" ? path.join(root, "dispatch-record.json")
            : ["transport-staged", "transport-published"].includes(fault) ? path.join(root, "transport-record.json")
              : fault === "observation-published" ? path.join(root, "observation-record.json") : null;
    const match = fault !== null && !stopped && to === expected;
    if (match && fault.endsWith("-staged")) { stopped = true; throw new Error("fixture-stop"); }
    const result = await originalRename(from, to, ...args);
    if (match) { stopped = true; throw new Error("fixture-stop"); }
    return result;
  });
  syncBuiltinESMExports();
  t.after(() => { t.mock.restoreAll(); syncBuiltinESMExports(); });
  const localOnly = await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=billing-additional-crash");
  for (const point of ["empty-root", "plan-staged", "plan-published", "approval-published", "budget-reserved",
    "dispatch-published", "transport-staged", "transport-published", "observation-published", "output-published"]) await t.test(point, async (child) => {
    fault = null; stopped = false; billingResponse = false;
    const f = await setupBillingAdditional(child, localOnly), startSends = sends;
    const opens = observeCredentialOpens(child, f.credentialFile), envReads = forbidEnvironmentCredential(child);
    const disclosure = await localOnly.describeR22OfficialToolUsageDiagnosticTransaction(f.billingConfig);
    root = path.join(f.cognitionRunRoot, "diagnostics", disclosure.transactionSha256.slice(7));
    output = f.billingConfig.output; account = f.cognitionRunRoot; fault = point; billingResponse = true;
    let tx;
    try {
      await assert.rejects(async () => {
        tx = await localOnly.createR22OfficialToolUsageDiagnosticTransaction({ ...f.billingConfig, expectedDisclosureSha256: disclosure.transactionSha256 });
        await execute(tx);
      }, /^Error: R22_/u);
    } finally { await tx?.close(); fault = null; }
    assert.equal(stopped, true);
    const dispatched = ["dispatch-published", "transport-staged", "transport-published", "observation-published", "output-published"].includes(point);
    const invoked = ["transport-staged", "transport-published", "observation-published", "output-published"].includes(point);
    const complete = ["observation-published", "output-published"].includes(point);
    assert.deepEqual([sends - startSends, opens(), envReads()], [invoked ? 1 : 0, invoked ? 1 : 0, 0]);
    await rm(f.credentialFile);
    let recovery = await additionalRecoveryConfig(f.billingConfig);
    if (["empty-root", "transport-staged"].includes(point)) {
      // There is no complete identity, or an incomplete transport record. Do
      // not invent a cancellation or promote an unverified response fragment.
      const before = await treeSnapshot(f.cognitionRunRoot);
      await assert.rejects(localOnly.recoverR22OfficialToolUsageDiagnosticTransaction(recovery, disclosure.transactionSha256), /^Error: R22_/u);
      await assert.rejects(auditOfficialFixture(f), /^Error: R22_/u);
      assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
    } else {
      const recovered = await localOnly.recoverR22OfficialToolUsageDiagnosticTransaction(recovery, disclosure.transactionSha256);
      assert.equal(recovered.terminal.formatVersion, "0.5.0");
      assert.equal(recovered.terminal.state, complete ? "observed" : dispatched ? "dispatch_uncertain" : "cancelled");
      assert.equal(recovered.terminal.chargedMicrousd, dispatched ? 10000 : 0);
      assert.equal(recovered.terminal.realRequestCount, complete || point === "transport-published" ? 1 : dispatched ? null : 0);
      assert.equal(recovered.terminal.providerReplayRequests, 0);
      recovery = await additionalRecoveryConfig(f.billingConfig);
      for (let i = 0; i < 2; i += 1) assert.equal(canonical(await localOnly.recoverR22OfficialToolUsageDiagnosticTransaction(recovery, disclosure.transactionSha256)), canonical(recovered));
      await auditOfficialFixture(f);
    }
    assert.deepEqual(await treeSnapshot(f.previousRoot), f.previousTree);
    assert.deepEqual(await treeSnapshot(f.secondRoot), f.secondTree);
    const budget = await savedBudget({ config: f.config }), extra = assertBudgetPrefixPreserved(budget, f.billingBudgetJson);
    assert.equal(extra.length, ["empty-root", "plan-staged", "plan-published", "approval-published"].includes(point) ? 0 : 1);
    if (extra.length) assert.equal(extra[0].chargedMicrousd, dispatched ? 10000 : 0);
    assert.equal((await readdir(path.join(f.cognitionRunRoot, "diagnostics"))).length, 3);
    assert.deepEqual([sends - startSends, opens(), envReads()], [invoked ? 1 : 0, invoked ? 1 : 0, 0]);
    await assertRejectCode(() => localOnly.describeR22OfficialToolUsageDiagnosticTransaction({ ...f.billingConfig,
      expectedHostBudgetSha256: sha(canonical(budget)), output: `${output}-fourth` }), "R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
  });
});

test("diagnostic child runner blocks native fetch at the loopback connect tripwire without raw output", async (t) => {
  const runner = spawnDiagnosticChild(DIAGNOSTIC_NETWORK_GUARD_CHILD, {});
  registerDiagnosticChildCleanup(t, runner);
  const result = await waitForDiagnosticChildMessage(runner, "network-guard-complete");
  const closed = await waitForDiagnosticChildExit(runner);
  assert.deepEqual(closed, { code: 0, signal: null });
  assert.deepEqual(result, { kind: "network-guard-complete", rejected: true,
    blockedBeforeConnect: true, networkTripwireHits: 1 });
  assert.deepEqual(runner.output(), { stdout: "", stderr: "" });
});

test("billing additional authorization: real child-process dispatch and fetch crashes recover without replay", { timeout: 120_000 }, async (t) => {
  let predecessorSends = 0;
  t.mock.method(globalThis, "fetch", async () => {
    predecessorSends += 1;
    return new Response(validResponseBytes(), { headers: { "content-type": "application/json" } });
  });
  const environmentReads = forbidEnvironmentCredential(t);
  const localOnly = await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=billing-child-process-crash");
  for (const point of ["durable-dispatch", "fetch-entered"]) await t.test(point, async (child) => {
    const readsBefore = environmentReads();
    const f = await setupBillingAdditional(child, localOnly, { legacyFirst: true });
    assert.equal(environmentReads() - readsBefore, 1); // The legacy v0.2 lane sees only the throwing test trap.
    assert.equal(JSON.parse(f.previousTree["transaction-plan.json"]).formatVersion, "0.2.0");
    assert.equal(JSON.parse(f.secondTree["transaction-plan.json"]).formatVersion, "0.4.0");
    const firstTree = structuredClone(f.previousTree);
    const secondTree = structuredClone(f.secondTree);
    const prefixBudgetJson = f.billingBudgetJson;

    const crashRunner = spawnDiagnosticChild(DIAGNOSTIC_CRASH_CHILD, {
      point,
      credentialFile: f.credentialFile,
      config: f.billingConfig,
    });
    registerDiagnosticChildCleanup(child, crashRunner);
    const marker = await waitForDiagnosticChildMessage(crashRunner, "crash-marker");
    assert.equal(marker.phase, point);
    assert.equal(marker.formatVersion, "0.5.0");
    assert.match(marker.transactionSha256, HASH);
    assert.equal(marker.environmentReads, 0);
    assert.equal(marker.credentialOpens, point === "durable-dispatch" ? 0 : 1);
    assert.equal(marker.fetchCalls, point === "durable-dispatch" ? 0 : 1);
    assert.equal(marker.networkTripwireHits, 0);
    assert.equal(crashRunner.child.kill(), true);
    const crashed = await waitForDiagnosticChildExit(crashRunner);
    assert.notEqual(crashed.code, 0);
    assert.deepEqual(crashRunner.output(), { stdout: "", stderr: "" });

    const recoveryConfig = await additionalRecoveryConfig(f.billingConfig);
    const fourthConfig = { ...f.billingConfig,
      expectedHostBudgetSha256: recoveryConfig.expectedHostBudgetSha256,
      output: `${f.billingConfig.output}-fourth-${point}` };
    const recoveryRunner = spawnDiagnosticChild(DIAGNOSTIC_RECOVERY_CHILD, {
      point,
      credentialFile: f.credentialFile,
      transactionSha256: marker.transactionSha256,
      recoveryConfig,
      fourthConfig,
    });
    registerDiagnosticChildCleanup(child, recoveryRunner);
    const recovered = await waitForDiagnosticChildMessage(recoveryRunner, "recovery-complete");
    const recoveryExit = await waitForDiagnosticChildExit(recoveryRunner);
    assert.deepEqual(recoveryExit, { code: 0, signal: null });
    assert.equal(recovered.terminal.formatVersion, "0.5.0");
    assert.equal(recovered.terminal.state, "dispatch_uncertain");
    assert.equal(recovered.terminal.chargedMicrousd, 10_000);
    assert.equal(recovered.terminal.providerReplayRequests, 0);
    assert.equal(recovered.terminal.realRequestCount, null);
    assert.deepEqual([recovered.fetchCalls, recovered.credentialOpens, recovered.environmentReads], [0, 0, 0]);
    assert.equal(recovered.networkTripwireHits, 0);
    assert.deepEqual(recovered.errors, ["R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED", "R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED"]);
    assert.deepEqual(recoveryRunner.output(), { stdout: "", stderr: "" });

    assert.deepEqual(await treeSnapshot(f.previousRoot), firstTree);
    assert.deepEqual(await treeSnapshot(f.secondRoot), secondTree);
    const finalBudgetJson = await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8");
    const additions = assertBudgetPrefixPreserved(JSON.parse(finalBudgetJson), prefixBudgetJson);
    assert.deepEqual(additions.map((entry) => [entry.state, entry.chargedMicrousd]), [["charged", 10_000]]);
    assert.equal((await auditOfficialFixture(f)).size, 3);
    assert.equal((await readdir(path.join(f.cognitionRunRoot, "diagnostics"))).length, 3);
    await assert.rejects(lstat(fourthConfig.output), { code: "ENOENT" });
  });
  assert.equal(predecessorSends, 2); // One intercepted v0.4 request per subtest; v0.2 and v0.5 use no external transport.
});

test("billing additional authorization: completed but ineligible v0.4 predecessors do not grant a new slot", async (t) => {
  let response, sends = 0;
  t.mock.method(globalThis, "fetch", async () => { sends += 1; return response(); });
  const localOnly = await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=billing-additional-ineligible");
  for (const kind of ["wrong-profile", "not-captured", "failed-limit", "transport-failed"]) await t.test(kind, async (child) => {
    let nested = 0;
    for (let i = 0; i < 10; i += 1) nested = { child: nested };
    response = () => kind === "transport-failed" ? new Response(null, { status: 500 }) : new Response(
      kind === "wrong-profile" ? billingResponseBytes() : kind === "not-captured" ? "{" : validResponseBytes({ toolUsage: nested }),
      { headers: { "content-type": "application/json" } });
    const f = await setupBillingAdditional(child, localOnly, { previousProfile: kind === "wrong-profile" ? "billing" : "tool-usage",
      expectedState: kind === "transport-failed" ? "transport_failed" : "observed" });
    if (kind === "not-captured" || kind === "failed-limit") assert.equal(JSON.parse(f.secondTree["observation-record.json"]).observation.status,
      kind === "not-captured" ? "not_captured" : "failed_limit");
    const before = await treeSnapshot(f.cognitionRunRoot), startSends = sends;
    const opens = observeCredentialOpens(child, f.credentialFile), envReads = forbidEnvironmentCredential(child);
    await assertRejectCode(() => localOnly.describeR22OfficialToolUsageDiagnosticTransaction(f.billingConfig), "R22_DIAGNOSTIC_BILLING_AUTHORIZATION_INVALID");
    await assertRejectCode(() => localOnly.createR22OfficialToolUsageDiagnosticTransaction({ ...f.billingConfig, expectedDisclosureSha256: h("a") }), "R22_DIAGNOSTIC_BILLING_AUTHORIZATION_INVALID");
    assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
    assert.deepEqual([sends - startSends, opens(), envReads()], [0, 0, 0]);
    assert.equal((await auditOfficialFixture(f)).size, 2);
  });
});

test("billing additional authorization: re-hashed forged grants still fail semantic history checks", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(validResponseBytes(), { headers: { "content-type": "application/json" } }));
  const localOnly = await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=billing-additional-forged-grant");
  for (const field of ["kind", "officialDiagnosticLimit", "previousFormatVersion", "previousTransactionSha256", "previousTerminalSha256",
    "previousTransportSha256", "previousObservationSha256", "previousBudgetEntrySha256", "extra", "removed", "capture", "credential-source"]) await t.test(field, async (child) => {
    const f = await setupBillingAdditional(child, localOnly), beforeBudget = f.billingBudgetJson;
    const opens = observeCredentialOpens(child, f.credentialFile), envReads = forbidEnvironmentCredential(child);
    const disclosure = await localOnly.describeR22OfficialToolUsageDiagnosticTransaction(f.billingConfig);
    const plan = JSON.parse(canonical(disclosure.transactionPlan));
    if (field === "kind") plan.billingAuthorization.kind = "retry";
    else if (field === "officialDiagnosticLimit") plan.billingAuthorization.officialDiagnosticLimit = 4;
    else if (field === "previousFormatVersion") plan.billingAuthorization.previousFormatVersion = "0.3.0";
    else if (field === "removed") delete plan.billingAuthorization.previousObservationSha256;
    else if (field === "extra") plan.billingAuthorization.extra = true;
    else if (field === "credential-source") plan.credentialSource.identitySha256 = h("9");
    else if (field === "capture") {
      const fixed = createNpcCognitionToolUsageDiagnosticPlan();
      Object.assign(plan, { callPlan: JSON.parse(fixed.callPlanJson), callPlanSha256: sha(fixed.callPlanJson),
        diagnosticApprovalSha256: fixed.diagnosticApprovalSha256, capturePolicySha256: fixed.capturePolicySha256 });
    } else plan.billingAuthorization[field] = h("9");
    const forgedId = sha(canonical(plan)), root = path.join(f.cognitionRunRoot, "diagnostics", forgedId.slice(7));
    await mkdir(root); await writeFile(path.join(root, "transaction-plan.json"), canonical(plan), { flag: "wx" });
    const before = await treeSnapshot(f.cognitionRunRoot);
    await assert.rejects(localOnly.recoverR22OfficialToolUsageDiagnosticTransaction(await additionalRecoveryConfig(f.billingConfig), forgedId), /^Error: R22_/u);
    assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
    assert.equal(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8"), beforeBudget);
    assert.deepEqual([opens(), envReads()], [0, 0]);
    await assert.rejects(lstat(f.billingConfig.output), { code: "ENOENT" });
  });
});

async function auditOfficialFixture(f) {
  return auditR22DiagnosticBudgetHistory({ cognitionRunRoot: f.cognitionRunRoot, hostRunId: HOST_RUN_ID,
    budget: JSON.parse(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8")),
    remember: (file) => readFile(file, "utf8"), rememberDirectory: (directory) => readdir(directory) });
}

test("additional authorization: metadata-only v0.4 requires a fresh approval and exact predecessor", async (t) => {
  const f = await setupAdditionalDiagnostic(t), envReads = forbidEnvironmentCredential(t), opens = observeCredentialOpens(t, f.credentialFile);
  const before = await treeSnapshot(f.cognitionRunRoot);
  const args = [...f.fileArgs];
  args[args.indexOf("--output") + 1] = f.next.output;
  args[args.indexOf("--host-budget-sha256") + 1] = f.next.expectedHostBudgetSha256;
  args.push("--continue-from-transaction", f.previous.transactionSha256);
  const disclosure = await runR22ToolUsageDiagnostic(["plan", ...args]);
  assert.equal(disclosure.transactionPlan.formatVersion, "0.4.0");
  assert.deepEqual(disclosure.transactionPlan.additionalAuthorization, {
    kind: "single-zero-request-credential-repair", previousTransactionSha256: f.previous.transactionSha256,
    previousFormatVersion: "0.3.0", previousTerminalSha256: sha(canonical(f.previous.terminal)),
    previousTransportSha256: sha(f.previousTree["transport-record.json"]),
    previousBudgetEntrySha256: sha(canonical(f.previousBudgetEntry)), officialDiagnosticLimit: 2,
  });
  for (let i = 0; i < 20; i += 1) assert.equal(canonical(await runR22ToolUsageDiagnostic(["plan", ...args])), canonical(disclosure));
  for (const approval of [undefined, f.previous.transactionSha256, h("a")]) {
    await assertRejectCode(() => createR22OfficialToolUsageDiagnosticTransaction({ ...f.next,
      ...(approval ? { expectedDisclosureSha256: approval } : {}) }), "R22_APPROVAL_MISMATCH");
  }
  const { credentialFile: _file, ...withoutFile } = f.next;
  await assert.rejects(describeR22OfficialToolUsageDiagnosticTransaction(withoutFile));
  await assert.rejects(describeR22OfficialToolUsageDiagnosticTransaction({ ...f.next, previousTransactionSha256: h("a") }));
  await assertRejectCode(() => runR22ToolUsageDiagnostic(["recover", ...args, "--transaction-sha256", disclosure.transactionSha256]), "R22_CLI_ARGUMENT_INVALID");
  assert.deepEqual([opens(), envReads()], [0, 0]);
  assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
  await assert.rejects(lstat(f.next.output), { code: "ENOENT" });
});

test("additional authorization: one intercepted request across 20 callers preserves history and budget", async (t) => {
  const f = await setupAdditionalDiagnostic(t), secret = ["sk", "local-fixture".repeat(4)].join("-");
  await writeFile(f.credentialFile, secret);
  const envReads = forbidEnvironmentCredential(t), opens = observeCredentialOpens(t, f.credentialFile);
  let sends = 0;
  t.mock.method(globalThis, "fetch", async (_endpoint, options) => {
    sends += 1;
    assert.equal(options.headers.authorization, `Bearer ${secret}`);
    assert.equal(options.body, createNpcCognitionToolUsageDiagnosticPlan().providerRequestJson);
    const budget = JSON.parse(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8"));
    const added = assertBudgetPrefixPreserved(budget, f.budgetJson);
    assert.equal(added.length, 1);
    assert.equal(added[0].state, "charged");
    assert.equal(added[0].chargedMicrousd, 10_000);
    return new Response(validResponseBytes(), { status: 200, headers: { "content-type": "application/json" } });
  });
  const localOnly = await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=additional-transport");
  const disclosure = await localOnly.describeR22OfficialToolUsageDiagnosticTransaction(f.next);
  const config = { ...f.next, expectedDisclosureSha256: disclosure.transactionSha256 };
  const competitors = await Promise.allSettled(Array.from({ length: 20 }, () => localOnly.createR22OfficialToolUsageDiagnosticTransaction(config)));
  const winners = competitors.filter((item) => item.status === "fulfilled");
  assert.equal(winners.length, 1);
  const tx = winners[0].value;
  await assertRejectCode(() => tx.approve({ disclosureSha256: f.previous.transactionSha256 }), "R22_APPROVAL_MISMATCH");
  const approval = await tx.approve({ disclosureSha256: disclosure.transactionSha256 });
  assert.deepEqual([sends, opens(), envReads()], [0, 0, 0]);
  const results = await Promise.all(Array.from({ length: 20 }, () => tx.execute(approval)));
  await tx.close();
  for (const result of results) assert.equal(canonical(result), canonical(results[0]));
  assert.equal(results[0].terminal.formatVersion, "0.4.0");
  assert.equal(results[0].terminal.state, "observed");
  assert.equal(results[0].terminal.realRequestCount, 1); // Unit interception only, not a real provider request.
  assert.equal(results[0].terminal.qualificationEligible, false);
  assert.deepEqual([sends, opens(), envReads()], [1, 1, 0]);
  assert.deepEqual(await treeSnapshot(f.previousRoot), f.previousTree);
  const budgetJson = await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8");
  assert.equal(assertBudgetPrefixPreserved(JSON.parse(budgetJson), f.budgetJson).length, 1);
  assert.equal((await auditOfficialFixture(f)).size, 2);
  const recovery = await additionalRecoveryConfig(f.next);
  await rm(f.credentialFile);
  for (let i = 0; i < 20; i += 1) {
    assert.equal(canonical(await localOnly.recoverR22OfficialToolUsageDiagnosticTransaction(recovery, results[0].transactionSha256)), canonical(results[0]));
  }
  assert.equal(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8"), budgetJson);
  const persisted = canonical(await treeSnapshot(f.cognitionRunRoot)) + canonical(await treeSnapshot(f.next.output));
  for (const bait of [secret, sha(secret), f.credentialFile, "fixture-dialogue-placeholder"]) assert.equal(persisted.includes(bait), false);
  // Directory count is checked before credentials, so a missing key cannot hide a third attempt.
  for (const captureProfile of ["tool-usage", "billing"]) for (const previousTransactionSha256 of [f.previous.transactionSha256, results[0].transactionSha256]) {
    await assertRejectCode(() => localOnly.describeR22OfficialToolUsageDiagnosticTransaction({ ...f.next, output: `${f.next.output}-third`,
      expectedHostBudgetSha256: sha(budgetJson), previousTransactionSha256, captureProfile }), "R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
  }
  assert.equal((await readdir(path.join(f.cognitionRunRoot, "diagnostics"))).length, 2);
  assert.deepEqual([sends, opens(), envReads()], [1, 1, 0]);
});

test("additional authorization: cancelled or zero-request successor consumes the last claim", async (t) => {
  for (const outcome of ["cancelled", "credential_unavailable"]) await t.test(outcome, async (child) => {
    const f = await setupAdditionalDiagnostic(child), envReads = forbidEnvironmentCredential(child);
    const disclosure = await describeR22OfficialToolUsageDiagnosticTransaction(f.next);
    const tx = await createR22OfficialToolUsageDiagnosticTransaction({ ...f.next, expectedDisclosureSha256: disclosure.transactionSha256 });
    const result = outcome === "cancelled" ? await tx.cancel() : await execute(tx);
    await tx.close();
    const recovery = await additionalRecoveryConfig(f.next);
    const before = await treeSnapshot(f.cognitionRunRoot);
    assert.equal(result.terminal.realRequestCount, 0);
    assert.equal(result.terminal.chargedMicrousd, outcome === "cancelled" ? 0 : 10_000);
    for (const previousTransactionSha256 of [f.previous.transactionSha256, result.transactionSha256]) {
      await assertRejectCode(() => describeR22OfficialToolUsageDiagnosticTransaction({ ...f.next, ...recovery,
        output: `${f.next.output}-third`, previousTransactionSha256 }), "R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
    }
    assert.equal(canonical(await recoverR22OfficialToolUsageDiagnosticTransaction(recovery, result.transactionSha256)), canonical(result));
    assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
    assert.equal((await auditOfficialFixture(f)).size, outcome === "cancelled" ? 1 : 2);
    assert.equal(envReads(), 0);
  });
});

test("additional authorization: legacy v0.2 zero-request predecessor and new cancellation reconcile", async (t) => {
  // The legacy convenience factory is exercised with a throwing property trap,
  // never an actual environment value. The new file lane cannot fall back to it.
  const envReads = forbidEnvironmentCredential(t);
  const f = await setupAdditionalDiagnostic(t, { legacy: true });
  const beforeReads = envReads(), opens = observeCredentialOpens(t, f.credentialFile);
  assert.equal(beforeReads, 1);
  const disclosure = await describeR22OfficialToolUsageDiagnosticTransaction(f.next);
  assert.equal(disclosure.transactionPlan.additionalAuthorization.previousFormatVersion, "0.2.0");
  const tx = await createR22OfficialToolUsageDiagnosticTransaction({ ...f.next, expectedDisclosureSha256: disclosure.transactionSha256 });
  const result = await tx.cancel(); await tx.close();
  assert.equal((await auditOfficialFixture(f)).size, 1);
  assert.equal(canonical(await recoverR22OfficialToolUsageDiagnosticTransaction(await additionalRecoveryConfig(f.next), result.transactionSha256)), canonical(result));
  assert.equal(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8"), f.budgetJson);
  assert.deepEqual(await treeSnapshot(f.previousRoot), f.previousTree);
  assert.deepEqual([opens(), envReads() - beforeReads], [0, 0]);
});

test("additional authorization: exhausted capacity or active reservation stops before claim and credential", async (t) => {
  for (const attack of ["entry-limit", "active-reservation"]) await t.test(attack, async (child) => {
    const f = await setupAdditionalDiagnostic(child), opens = observeCredentialOpens(child, f.credentialFile), envReads = forbidEnvironmentCredential(child);
    const budget = JSON.parse(f.budgetJson);
    if (attack === "entry-limit") for (let i = 1; i <= 98; i += 1) budget.entries.push(historicalEntry(i, "charged", 0));
    else budget.entries.push(historicalEntry(1, "reserved", 0));
    budget.entries.sort((a, b) => a.authoritySessionSha256 < b.authoritySessionSha256 ? -1 : a.authoritySessionSha256 > b.authoritySessionSha256 ? 1 : 0);
    await writeFile(path.join(f.cognitionRunRoot, "host-budget.json"), canonical(budget));
    const config = { ...f.next, expectedHostBudgetSha256: sha(canonical(budget)) }, before = await treeSnapshot(f.cognitionRunRoot);
    const code = attack === "entry-limit" ? "R22_BUDGET_EXHAUSTED" : "R22_CALL_IN_FLIGHT";
    await assertRejectCode(() => describeR22OfficialToolUsageDiagnosticTransaction(config), code);
    await assertRejectCode(() => createR22OfficialToolUsageDiagnosticTransaction({ ...config, expectedDisclosureSha256: h("a") }), code);
    assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
    assert.deepEqual([opens(), envReads()], [0, 0]);
  });
});

test("additional authorization: predecessor deletion or a third directory closes recovery and ordinary audit", async (t) => {
  for (const attack of ["deleted-plan", "third-directory"]) await t.test(attack, async (child) => {
    const f = await setupAdditionalDiagnostic(child), envReads = forbidEnvironmentCredential(child), opens = observeCredentialOpens(child, f.credentialFile);
    const disclosure = await describeR22OfficialToolUsageDiagnosticTransaction(f.next);
    const tx = await createR22OfficialToolUsageDiagnosticTransaction({ ...f.next, expectedDisclosureSha256: disclosure.transactionSha256 });
    await tx.close();
    const planPath = path.join(f.previousRoot, "transaction-plan.json");
    const third = path.join(f.cognitionRunRoot, "diagnostics", "9".repeat(64));
    if (attack === "deleted-plan") await rm(planPath);
    else await mkdir(third);
    const before = await treeSnapshot(f.cognitionRunRoot);
    const recovery = await additionalRecoveryConfig(f.next);
    await assert.rejects(recoverR22OfficialToolUsageDiagnosticTransaction(recovery, disclosure.transactionSha256));
    await assert.rejects(auditOfficialFixture(f));
    assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
    await assert.rejects(lstat(f.next.output), { code: "ENOENT" });
    if (attack === "deleted-plan") await writeFile(planPath, f.previousTree["transaction-plan.json"], { flag: "wx" });
    else { assert.equal(path.dirname(third), path.join(f.cognitionRunRoot, "diagnostics")); await rm(third, { recursive: true, force: false }); }
    assert.equal((await recoverR22OfficialToolUsageDiagnosticTransaction(recovery, disclosure.transactionSha256)).terminal.state, "cancelled");
    assert.equal((await auditOfficialFixture(f)).size, 1);
    assert.deepEqual([opens(), envReads()], [0, 0]);
  });
});

test("additional authorization: unsupported predecessors and refunded charges never grant another request", async (t) => {
  for (const attack of ["requested", "uncertain", "cancelled", "refunded", "cross-account"]) await t.test(attack, async (child) => {
    const f = await setupAdditionalDiagnostic(child), opens = observeCredentialOpens(child, f.credentialFile), envReads = forbidEnvironmentCredential(child);
    const transportPath = path.join(f.previousRoot, "transport-record.json"), terminalPath = path.join(f.previousRoot, "terminal-record.json");
    const terminal = JSON.parse(await readFile(terminalPath, "utf8"));
    if (attack === "requested") {
      const transport = JSON.parse(await readFile(transportPath, "utf8"));
      transport.status = "http_error"; transport.requestCount = 1;
      await writeFile(transportPath, canonical(transport));
      terminal.transportRecordSha256 = sha(canonical(transport)); terminal.transportRequestCount = 1; terminal.realRequestCount = 1;
      await writeFile(terminalPath, canonical(terminal));
    } else if (attack === "uncertain") {
      await rm(transportPath);
      Object.assign(terminal, { state: "dispatch_uncertain", transportRecordSha256: null, transportRequestCount: null, realRequestCount: null });
      await writeFile(terminalPath, canonical(terminal));
    } else if (attack === "cancelled") {
      terminal.state = "cancelled"; await writeFile(terminalPath, canonical(terminal));
    } else if (attack === "refunded") {
      const budget = JSON.parse(f.budgetJson), entry = budget.entries.find((item) => item.authoritySessionSha256 === f.previousBudgetEntry.authoritySessionSha256);
      entry.state = "released"; entry.chargedMicrousd = 0;
      await writeFile(path.join(f.cognitionRunRoot, "host-budget.json"), canonical(budget));
      f.next.expectedHostBudgetSha256 = sha(canonical(budget));
    } else {
      const unrelated = await setupCredentialCli(child);
      f.next.cognitionRunRoot = unrelated.cognitionRunRoot;
      f.next.expectedHostBudgetSha256 = unrelated.config.expectedHostBudgetSha256;
    }
    const before = await treeSnapshot(f.next.cognitionRunRoot);
    await assert.rejects(describeR22OfficialToolUsageDiagnosticTransaction(f.next), /^Error: R22_/u);
    assert.deepEqual(await treeSnapshot(f.next.cognitionRunRoot), before);
    assert.deepEqual([opens(), envReads()], [0, 0]);
  });
});

test("additional authorization: predecessor replacement and fully re-signed records invalidate dispatch and recovery", async (t) => {
  for (const attack of ["replace-identical", "resign-chain", "resign-before-create"]) await t.test(attack, async (child) => {
    const f = await setupAdditionalDiagnostic(child), opens = observeCredentialOpens(child, f.credentialFile), envReads = forbidEnvironmentCredential(child);
    const disclosure = await describeR22OfficialToolUsageDiagnosticTransaction(f.next);
    const tx = attack === "resign-before-create" ? null : await createR22OfficialToolUsageDiagnosticTransaction({ ...f.next, expectedDisclosureSha256: disclosure.transactionSha256 });
    const token = tx ? await tx.approve({ disclosureSha256: disclosure.transactionSha256 }) : null;
    if (attack === "replace-identical") {
      const file = path.join(f.previousRoot, "terminal-record.json"), text = await readFile(file, "utf8");
      await rename(file, `${file}.displaced`); await writeFile(file, text, { flag: "wx" }); await rm(`${file}.displaced`);
    } else {
      const records = Object.fromEntries(Object.entries(f.previousTree).map(([name, text]) => [name, JSON.parse(text)]));
      records["approval-record.json"].processEpochSha256 = h("9");
      records["dispatch-record.json"].approvalRecordSha256 = sha(canonical(records["approval-record.json"]));
      records["transport-record.json"].dispatchRecordSha256 = sha(canonical(records["dispatch-record.json"]));
      Object.assign(records["terminal-record.json"], { dispatchRecordSha256: sha(canonical(records["dispatch-record.json"])),
        transportRecordSha256: sha(canonical(records["transport-record.json"])) });
      for (const [name, value] of Object.entries(records)) await writeFile(path.join(f.previousRoot, name), canonical(value));
    }
    if (attack === "resign-before-create") {
      await assertRejectCode(() => createR22OfficialToolUsageDiagnosticTransaction({ ...f.next, expectedDisclosureSha256: disclosure.transactionSha256 }), "R22_APPROVAL_MISMATCH");
      assert.notEqual((await describeR22OfficialToolUsageDiagnosticTransaction(f.next)).transactionSha256, disclosure.transactionSha256);
      assert.equal((await readdir(path.join(f.cognitionRunRoot, "diagnostics"))).length, 1);
      assert.equal(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8"), f.budgetJson);
      assert.deepEqual([opens(), envReads()], [0, 0]);
      return;
    }
    await assert.rejects(tx.execute(token), /^Error: R22_/u); await tx.close();
    assert.equal(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8"), f.budgetJson);
    assert.deepEqual([opens(), envReads()], [0, 0]);
    await assert.rejects(lstat(f.next.output), { code: "ENOENT" });
    const recovery = await additionalRecoveryConfig(f.next);
    if (attack === "resign-chain") await assert.rejects(recoverR22OfficialToolUsageDiagnosticTransaction(recovery, disclosure.transactionSha256));
    else assert.equal((await recoverR22OfficialToolUsageDiagnosticTransaction(recovery, disclosure.transactionSha256)).terminal.state, "cancelled");
  });
});

test("additional authorization: pending plan and dispatch crashes recover offline without reopening the claim", async (t) => {
  for (const point of ["empty-root", "plan-staged", "plan-published", "dispatch-published"]) await t.test(point, async (child) => {
    const f = await setupAdditionalDiagnostic(child), opens = observeCredentialOpens(child, f.credentialFile), envReads = forbidEnvironmentCredential(child);
    const disclosure = await describeR22OfficialToolUsageDiagnosticTransaction(f.next);
    const root = path.join(f.cognitionRunRoot, "diagnostics", disclosure.transactionSha256.slice(7));
    const originalMkdir = fsPromises.mkdir, originalRename = fsPromises.rename;
    let stopped = false, sends = 0;
    child.mock.method(fsPromises, "mkdir", async (target, ...args) => {
      const result = await originalMkdir(target, ...args);
      if (!stopped && point === "empty-root" && target === root) { stopped = true; throw new Error("fixture-stop"); }
      return result;
    });
    child.mock.method(fsPromises, "rename", async (from, to, ...args) => {
      const match = !stopped && ((point === "dispatch-published" && to === path.join(root, "dispatch-record.json")) ||
        (["plan-staged", "plan-published"].includes(point) && to === path.join(root, "transaction-plan.json")));
      if (match && point === "plan-staged") { stopped = true; throw new Error("fixture-stop"); }
      const result = await originalRename(from, to, ...args);
      if (match) { stopped = true; throw new Error("fixture-stop"); }
      return result;
    });
    syncBuiltinESMExports();
    child.after(() => { child.mock.restoreAll(); syncBuiltinESMExports(); });
    child.mock.method(globalThis, "fetch", async () => { sends += 1; throw new Error("fixture-network-forbidden"); });
    const localOnly = point === "empty-root" ? await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=additional-empty-root")
      : point === "plan-staged" ? await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=additional-plan-staged")
        : point === "plan-published" ? await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=additional-plan-published")
          : await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=additional-dispatch-published");
    let tx;
    try {
      if (point === "dispatch-published") {
        tx = await localOnly.createR22OfficialToolUsageDiagnosticTransaction({ ...f.next, expectedDisclosureSha256: disclosure.transactionSha256 });
        await assert.rejects(execute(tx));
      } else await assert.rejects(async () => {
        tx = await localOnly.createR22OfficialToolUsageDiagnosticTransaction({ ...f.next, expectedDisclosureSha256: disclosure.transactionSha256 });
      });
    } finally { await tx?.close(); }
    assert.equal(stopped, true);
    const recovery = await additionalRecoveryConfig(f.next);
    await rm(f.credentialFile);
    if (point === "empty-root") {
      await assert.rejects(recoverR22OfficialToolUsageDiagnosticTransaction(recovery, disclosure.transactionSha256));
      await assert.rejects(auditOfficialFixture(f));
    } else {
      const result = await recoverR22OfficialToolUsageDiagnosticTransaction(recovery, disclosure.transactionSha256);
      assert.equal(result.terminal.state, point === "dispatch-published" ? "dispatch_uncertain" : "cancelled");
      assert.equal(result.terminal.chargedMicrousd, point === "dispatch-published" ? 10_000 : 0);
      assert.equal(result.terminal.providerReplayRequests, 0);
      assert.equal(canonical(await recoverR22OfficialToolUsageDiagnosticTransaction(await additionalRecoveryConfig(f.next), disclosure.transactionSha256)), canonical(result));
      await auditOfficialFixture(f);
    }
    assert.deepEqual(await treeSnapshot(f.previousRoot), f.previousTree);
    assert.deepEqual([sends, opens(), envReads()], [0, 0, 0]);
    assert.equal((await readdir(path.join(f.cognitionRunRoot, "diagnostics"))).length, 2);
  });
});

function terminalSummary(result) {
  return {
    state: result.terminal.state,
    transportRequestCount: result.terminal.transportRequestCount,
    realRequestCount: result.terminal.realRequestCount,
    requestCountUpperBound: result.terminal.requestCountUpperBound,
    dispatchCount: result.terminal.dispatchCount,
    chargedMicrousd: result.terminal.chargedMicrousd,
    providerReplayRequests: result.terminal.providerReplayRequests,
  };
}

test("injected response observes once only after durable dispatch and charge, without persisting private text", async (t) => {
  const f = await setup(t);
  const phases = [];
  let tx;
  const phase = async (name) => {
    phases.push(name);
    if (["credential:before_read", "credential:read", "transport:invoked"].includes(name)) {
      const root = transactionRoot(f, tx.disclosure.transactionSha256);
      const names = (await readdir(root)).sort();
      assert.equal(names.includes("dispatch-record.json"), true);
      assert.equal(names.includes("transport-record.json"), false);
      const entry = (await savedBudget(f)).entries.find((item) => item.chargedMicrousd === 10_000);
      assert.deepEqual({ state: entry?.state, chargedMicrousd: entry?.chargedMicrousd }, { state: "charged", chargedMicrousd: 10_000 });
    }
  };
  tx = await createInjected(f, transportFixture({
    responseBytes: validResponseBytes({
      secret: "fixture-dialogue-placeholder",
      toolUsage: { image_gen: { total_tokens: 0 }, fixture_unknown_field: "fixture-value-placeholder" },
    }),
  }), { phase });
  const result = await execute(tx);
  await tx.close();
  assert.deepEqual(terminalSummary(result), {
    state: "observed",
    transportRequestCount: 1,
    realRequestCount: 0,
    requestCountUpperBound: 1,
    dispatchCount: 1,
    chargedMicrousd: 10_000,
    providerReplayRequests: 0,
  });
  assert.equal(phases.filter((name) => name === "credential:read").length, 1);
  assert.equal(phases.filter((name) => name === "transport:invoked").length, 1);
  assert.equal(tx.disclosure.transactionPlan.sourceProviderMode, "offline-fake");
  assert.ok(phases.indexOf("dispatch-record.json:published") < phases.indexOf("credential:before_read"));
  assert.ok(phases.indexOf("credential:read") < phases.indexOf("transport:invoked"));
  const durable = await transportRecord(f, result.transactionSha256);
  assert.deepEqual({ status: durable.status, requestCount: durable.requestCount }, { status: "response", requestCount: 1 });
  const observation = JSON.parse(await readFile(path.join(transactionRoot(f, result.transactionSha256), "observation-record.json"), "utf8"));
  assert.equal(observation.transportRecordSha256, sha(canonical(durable)));
  const persisted = await allText(f.config.temporaryRoot);
  for (const bait of ["fixture-dialogue-placeholder", "fixture_unknown_field", "fixture-value-placeholder"]) assert.equal(persisted.includes(bait), false);
});

test("factories and recovery keep official and injected account modes disjoint", async (t) => {
  const offline = await setup(t);
  const official = await setup(t, { providerMode: "official-once" });
  await assertRejectCode(() => createR22OfficialToolUsageDiagnosticTransaction(offline.config), "R22_DIAGNOSTIC_LIVE_DISABLED");
  await assertRejectCode(() => createR22InjectedToolUsageDiagnosticTransaction(official.config, transportFixture()), "R22_DIAGNOSTIC_LIVE_DISABLED");
  await assertRejectCode(() => createR22OfficialToolUsageDiagnosticTransaction(official.config, {}), "R22_APPROVAL_MISMATCH");

  const injected = await createInjected(offline);
  const injectedResult = await execute(injected);
  await injected.close();
  const currentOffline = await currentConfig(offline);
  await assertRejectCode(() => recoverR22OfficialToolUsageDiagnosticTransaction(currentOffline, injectedResult.transactionSha256), "R22_DIAGNOSTIC_LIVE_DISABLED");

  const publicTx = await createR22OfficialToolUsageDiagnosticTransaction(official.config);
  const cancelled = await publicTx.cancel();
  await publicTx.close();
  assert.deepEqual(terminalSummary(cancelled), {
    state: "cancelled", transportRequestCount: 0, realRequestCount: 0, requestCountUpperBound: 0,
    dispatchCount: 0, chargedMicrousd: 0, providerReplayRequests: 0,
  });
  const currentOfficial = await currentConfig(official);
  await assertRejectCode(() => recoverR22InjectedToolUsageDiagnosticTransaction(currentOfficial, cancelled.transactionSha256), "R22_DIAGNOSTIC_LIVE_DISABLED");
});

test("an official source rejects a legacy v0.1 budget authority without changing budget bytes", async (t) => {
  const f = await setup(t, { providerMode: "official-once" });
  const { output: _output, ...accountConfig } = f.config;
  const store = await openR22DiagnosticBudgetStore(accountConfig);
  const before = await readFile(path.join(f.config.cognitionRunRoot, "host-budget.json"), "utf8");
  const transactionSha256 = sha("legacy-authority-on-official-source");
  const legacyKey = {
    authoritySessionSha256: sha(canonical({ purpose: "matrix-oasis.r22-diagnostic-budget/1", transactionSha256 })),
    callPlanSha256: sha("legacy-call-plan"),
    transactionSha256,
  };
  try {
    await assertRejectCode(() => store.reserve(legacyKey), "R22_DIAGNOSTIC_BUDGET_KEY_INVALID");
    assert.equal(await readFile(path.join(f.config.cognitionRunRoot, "host-budget.json"), "utf8"), before);
  } finally {
    await store.close();
  }
});

test("legacy offline and injected histories coexist but their recovery modes cannot be mixed", async (t) => {
  const f = await setup(t);
  const legacy = await createR22ToolUsageDiagnosticTransaction(f.config, testOverrides());
  const legacyResult = await legacy.cancel();
  await legacy.close();

  const injectedConfig = await currentConfig(f, "injected-after-legacy");
  const injectedFixture = { ...f, config: injectedConfig };
  const injected = await createInjected(injectedFixture);
  const injectedResult = await execute(injected);
  await injected.close();
  assert.equal((await readdir(path.join(f.config.cognitionRunRoot, "diagnostics"))).length, 2);

  const currentLegacyConfig = await currentConfig(f);
  const currentInjectedConfig = await currentConfig(f, "injected-after-legacy");
  assert.equal((await recoverR22ToolUsageDiagnosticTransaction(currentLegacyConfig, legacyResult.transactionSha256, testOverrides())).terminal.executionKind,
    "offline-fixture");
  assert.equal((await recoverR22InjectedToolUsageDiagnosticTransaction(currentInjectedConfig, injectedResult.transactionSha256, testOverrides())).terminal.executionKind,
    "injected-transport");
  await assert.rejects(() => recoverR22InjectedToolUsageDiagnosticTransaction(currentLegacyConfig, legacyResult.transactionSha256, testOverrides()), /^Error: R22_/u);
  await assert.rejects(() => recoverR22ToolUsageDiagnosticTransaction(currentInjectedConfig, injectedResult.transactionSha256, testOverrides()), /^Error: R22_/u);
});

test("data fixture is exact, inert and bounded", async (t) => {
  const invalid = [
    { ...transportFixture(), sender: "forbidden" },
    { ...transportFixture(), scenario: "unknown" },
    { ...transportFixture(), responseBytes: new Uint8Array(65_538) },
    { ...transportFixture(), status: 99 },
    { ...transportFixture(), contentType: "x".repeat(129) },
    { ...transportFixture(), contentLength: "1".repeat(33) },
    { ...transportFixture(), responseUrl: "unlocked" },
    { ...transportFixture(), chunkSize: 0 },
    { ...transportFixture(), timeoutMs: 0 },
  ];
  for (const value of invalid) {
    const f = await setup(t);
    await assertRejectCode(() => createR22InjectedToolUsageDiagnosticTransaction(f.config, value), "R22_DIAGNOSTIC_TRANSACTION_INVALID");
    assert.equal(await readFile(path.join(f.config.cognitionRunRoot, "host-budget.json"), "utf8"), f.initialBudgetJson);
  }
  const f = await setup(t);
  let getterCalls = 0;
  const getterFixture = { ...transportFixture() };
  Object.defineProperty(getterFixture, "scenario", { enumerable: true, get() { getterCalls += 1; return "response"; } });
  await assertRejectCode(() => createR22InjectedToolUsageDiagnosticTransaction(f.config, getterFixture), "R22_DIAGNOSTIC_TRANSACTION_INVALID");
  assert.equal(getterCalls, 0);
});

test("wrong or absent approval never reserves, reads a credential or invokes transport", async (t) => {
  for (const kind of ["absent", "wrong"]) {
    await t.test(kind, async (child) => {
      const f = await setup(child);
      const phases = [];
      const tx = await createInjected(f, transportFixture(), { phase: async (name) => { phases.push(name); } });
      if (kind === "absent") await assertRejectCode(() => tx.execute({ approvalTokenSha256: h("9") }), "R22_APPROVAL_MISMATCH");
      else await assertRejectCode(() => tx.approve({ disclosureSha256: h("8") }), "R22_APPROVAL_MISMATCH");
      await tx.close();
      assert.equal(phases.includes("credential:read"), false);
      assert.equal(phases.includes("transport:invoked"), false);
      assert.equal(await readFile(path.join(f.config.cognitionRunRoot, "host-budget.json"), "utf8"), f.initialBudgetJson);
    });
  }
});

test("existing reservation and exhausted history prevent transport before credential access", async (t) => {
  const cases = [
    ["reserved", [historicalEntry(1, "reserved")], "R22_CALL_IN_FLIGHT"],
    ["limit", Array.from({ length: 100 }, (_, index) => historicalEntry(index, "charged", 10_000)), "R22_BUDGET_EXHAUSTED"],
  ];
  for (const [name, entries, code] of cases) {
    await t.test(name, async (child) => {
      const f = await setup(child, { entries });
      const phases = [];
      const tx = await createInjected(f, transportFixture(), { phase: async (value) => { phases.push(value); } }).catch((error) => error);
      if (name === "reserved") {
        assert.equal(tx.message, code);
      } else {
        const approval = await tx.approve({ disclosureSha256: tx.disclosure.transactionSha256 });
        await assertRejectCode(() => tx.execute(approval), code);
        await tx.close();
      }
      assert.equal(phases.includes("credential:read"), false);
      assert.equal(phases.includes("transport:invoked"), false);
      assert.deepEqual(await savedBudget(f), hostBudget(entries));
    });
  }
});

test("twenty concurrent executes share one transport and one canonical result", async (t) => {
  const f = await setup(t);
  let invoked = 0;
  const tx = await createInjected(f, transportFixture(), {
    phase: async (name) => { if (name === "transport:invoked") invoked += 1; },
  });
  const approval = await tx.approve({ disclosureSha256: tx.disclosure.transactionSha256 });
  const results = await Promise.all(Array.from({ length: 20 }, () => tx.execute(approval)));
  await tx.close();
  assert.equal(invoked, 1);
  assert.equal(new Set(results.map(canonical)).size, 1);
  assert.equal(results[0].terminal.transportRequestCount, 1);
});

test("approval expires before request, but crossing five minutes after receipt does not discard the response", async (t) => {
  await t.test("expired before request", async (child) => {
    const f = await setup(child);
    let clock = 1_000;
    let invoked = 0;
    const tx = await createR22InjectedToolUsageDiagnosticTransaction(f.config, transportFixture(), {
      clock: () => clock,
      randomBytes: () => new Uint8Array(32).fill(7),
      phase: async (name) => {
        if (name === "transport:before_execute") clock = 301_000;
        if (name === "transport:invoked") invoked += 1;
      },
    });
    const approval = await tx.approve({ disclosureSha256: tx.disclosure.transactionSha256 });
    await assertRejectCode(() => tx.execute(approval), "R22_DIAGNOSTIC_APPROVAL_EXPIRED");
    await tx.close();
    assert.equal(invoked, 0);
    assert.equal((await savedBudget(f)).entries.reduce((sum, entry) => sum + entry.chargedMicrousd, 0), 10_123);
  });

  await t.test("expired after receipt", async (child) => {
    const f = await setup(child);
    let clock = 1_000;
    let invoked = 0;
    const tx = await createR22InjectedToolUsageDiagnosticTransaction(f.config, transportFixture(), {
      clock: () => clock,
      randomBytes: () => new Uint8Array(32).fill(7),
      phase: async (name) => {
        if (name === "transport:invoked") invoked += 1;
        if (name === "transport:received") clock = 301_000;
      },
    });
    const result = await execute(tx);
    await tx.close();
    assert.equal(invoked, 1);
    assert.equal(result.terminal.state, "observed");
    const durable = await transportRecord(f, result.transactionSha256);
    const observation = JSON.parse(await readFile(path.join(transactionRoot(f, result.transactionSha256), "observation-record.json"), "utf8"));
    assert.equal(durable.status, "response");
    assert.equal(observation.transportRecordSha256, sha(canonical(durable)));
  });
});

test("output or deterministic stage capture at credential read blocks transport and recovery replay", async (t) => {
  for (const capture of ["output", "stage"]) {
    await t.test(capture, async (child) => {
      const f = await setup(child);
      let tx;
      let invoked = 0;
      let capturedPath = null;
      tx = await createInjected(f, transportFixture(), {
        phase: async (name) => {
          if (name === "transport:invoked") invoked += 1;
          if (capturedPath === null && name === "credential:read") {
            const id = tx.disclosure.transactionSha256;
            capturedPath = capture === "output"
              ? f.config.output
              : path.join(path.dirname(f.config.output), `.r22d-${id.slice(7)}.pending`);
            await mkdir(capturedPath);
          }
        },
      });
      const id = tx.disclosure.transactionSha256;
      await assert.rejects(() => execute(tx), /^Error: R22_/u);
      await tx.close();
      assert.equal(invoked, 0);
      assert.equal((await readdir(transactionRoot(f, id))).includes("dispatch-record.json"), true);
      assert.equal((await readdir(transactionRoot(f, id))).includes("transport-record.json"), false);
      assert.equal((await savedBudget(f)).entries.reduce((sum, entry) => sum + entry.chargedMicrousd, 0), 10_123);

      const config = await currentConfig(f);
      await assert.rejects(() => recoverR22InjectedToolUsageDiagnosticTransaction(config, id, testOverrides({
        phase: async (name) => { if (name === "transport:invoked") invoked += 1; },
      })), /^Error: R22_/u);
      assert.equal(invoked, 0);
      await rm(capturedPath, { recursive: true, force: false });
      const recovered = await recoverR22InjectedToolUsageDiagnosticTransaction(config, id, testOverrides({
        phase: async (name) => { if (name === "transport:invoked") invoked += 1; },
      }));
      assert.deepEqual(terminalSummary(recovered), {
        state: "dispatch_uncertain", transportRequestCount: null, realRequestCount: 0, requestCountUpperBound: 1,
        dispatchCount: 1, chargedMicrousd: 10_000, providerReplayRequests: 0,
      });
      assert.equal(invoked, 0);
    });
  }
});

test("credential scenarios produce a zero-request durable failure and retain charge", async (t) => {
  for (const scenario of ["credential-missing", "credential-error", "credential-timeout"]) {
    await t.test(scenario, async (child) => {
      const f = await setup(child);
      const phases = [];
      const tx = await createInjected(f, transportFixture({ scenario, timeoutMs: 1_000 }), {
        phase: async (name) => { phases.push(name); },
      });
      const result = await execute(tx);
      await tx.close();
      assert.deepEqual(terminalSummary(result), {
        state: "transport_failed", transportRequestCount: 0, realRequestCount: 0, requestCountUpperBound: 1,
        dispatchCount: 1, chargedMicrousd: 10_000, providerReplayRequests: 0,
      });
      assert.deepEqual({ status: (await transportRecord(f, result.transactionSha256)).status, invoked: phases.includes("transport:invoked") },
        { status: "credential_unavailable", invoked: false });
    });
  }
});

test("network and deadline scenarios record one request without retry", async (t) => {
  const cases = [
    ["network-throw", "network_ambiguous", false],
    ["network-reject", "network_ambiguous", true],
    ["request-timeout", "timeout", true],
    ["body-timeout", "timeout", true],
    ["late-reject", "timeout", true],
  ];
  for (const [scenario, status, hasInvokedPhase] of cases) {
    await t.test(scenario, async (child) => {
      const f = await setup(child);
      let invoked = 0;
      const tx = await createInjected(f, transportFixture({ scenario, timeoutMs: 1_000 }), {
        phase: async (name) => { if (name === "transport:invoked") invoked += 1; },
      });
      const result = await execute(tx);
      await tx.close();
      const durable = await transportRecord(f, result.transactionSha256);
      assert.deepEqual({ status: durable.status, requestCount: durable.requestCount }, { status, requestCount: 1 });
      assert.equal(invoked, hasInvokedPhase ? 1 : 0);
      assert.deepEqual(terminalSummary(result), {
        state: "transport_failed", transportRequestCount: 1, realRequestCount: 0, requestCountUpperBound: 1,
        dispatchCount: 1, chargedMicrousd: 10_000, providerReplayRequests: 0,
      });
    });
  }
});

test("HTTP status, redirect and address failures collapse to fixed transport status", async (t) => {
  const cases = [
    ["status-201", { status: 201 }, "response_invalid"],
    ["redirect-301", { status: 301 }, "network_ambiguous"],
    ["redirected", { redirected: true }, "network_ambiguous"],
    ["other-address", { responseUrl: "other" }, "network_ambiguous"],
    ["client-error", { status: 429, responseBytes: new TextEncoder().encode("fixture-http-body-placeholder") }, "http_error"],
    ["server-error", { status: 503, responseBytes: new TextEncoder().encode("fixture-server-body-placeholder") }, "http_error"],
  ];
  for (const [name, overrides, status] of cases) {
    await t.test(name, async (child) => {
      const f = await setup(child);
      const tx = await createInjected(f, transportFixture(overrides));
      const result = await execute(tx);
      await tx.close();
      assert.deepEqual({ status: (await transportRecord(f, result.transactionSha256)).status, state: result.terminal.state },
        { status, state: "transport_failed" });
      const text = await allText(f.config.temporaryRoot);
      assert.equal(text.includes("fixture-http-body-placeholder"), false);
      assert.equal(text.includes("fixture-server-body-placeholder"), false);
    });
  }
});

test("content metadata and body limits fail closed at 65536 bytes and 4096 chunks", async (t) => {
  const cases = [
    ["content-type", { contentType: "text/plain" }, "response_invalid"],
    ["declared-limit", { contentLength: "65537" }, "response_limit"],
    ["declared-syntax", { contentLength: "065536" }, "response_limit"],
    ["declared-mismatch", { contentLength: "1" }, "response_invalid"],
    ["actual-limit", { responseBytes: new Uint8Array(65_537), chunkSize: 65_537 }, "response_limit"],
    ["chunk-limit", { responseBytes: new Uint8Array(4_097), chunkSize: 1 }, "response_limit"],
  ];
  for (const [name, overrides, status] of cases) {
    await t.test(name, async (child) => {
      const f = await setup(child);
      const tx = await createInjected(f, transportFixture(overrides));
      const result = await execute(tx);
      await tx.close();
      const durable = await transportRecord(f, result.transactionSha256);
      assert.deepEqual({ status: durable.status, requestCount: durable.requestCount, state: result.terminal.state },
        { status, requestCount: 1, state: "transport_failed" });
    });
  }
});

test("transport phase crashes recover without replaying the private sender", async (t) => {
  const cases = [
    ["transport:before_execute", 0, null],
    ["credential:before_read", 0, null],
    ["credential:read", 0, null],
    ["transport:invoked", 1, null],
    ["transport:received", 1, null],
    ["transport-record.json:published", 1, 1],
    ["transport:parsed", 1, 1],
  ];
  for (const captureProfile of ["tool-usage", "billing"]) for (const [fault, expectedInvoked, expectedDurableCount] of cases) {
    await t.test(`${captureProfile}: ${fault}`, async (child) => {
      const f = await setup(child);
      if (captureProfile === "billing") f.config.captureProfile = captureProfile;
      let failed = false;
      let invoked = 0;
      const tx = await createInjected(f, transportFixture(captureProfile === "billing" ? { responseBytes: billingResponseBytes() } : {}), {
        phase: async (name) => {
          if (name === "transport:invoked") invoked += 1;
          if (!failed && name === fault) { failed = true; throw new Error("fixture phase failure"); }
        },
      });
      const id = tx.disclosure.transactionSha256;
      await assert.rejects(() => execute(tx), /^Error: R22_/u);
      await tx.close();
      assert.equal(failed, true);
      assert.equal(invoked, expectedInvoked);
      const result = await recoverR22InjectedToolUsageDiagnosticTransaction(await currentConfig(f), id, testOverrides());
      assert.equal(result.terminal.state, "dispatch_uncertain");
      assert.equal(result.terminal.transportRequestCount, expectedDurableCount);
      assert.equal(result.terminal.realRequestCount, 0);
      assert.equal(result.terminal.providerReplayRequests, 0);
      assert.equal(invoked, expectedInvoked);
      const second = await recoverR22InjectedToolUsageDiagnosticTransaction(await currentConfig(f), id, testOverrides());
      assert.equal(canonical(second), canonical(result));
    });
  }
});

test("byte-identical plan, approval or dispatch replacement after receipt is rejected", async (t) => {
  for (const name of ["transaction-plan.json", "approval-record.json", "dispatch-record.json"]) {
    await t.test(name, async (child) => {
      const f = await setup(child);
      let tx;
      let swapped = false;
      let invoked = 0;
      tx = await createInjected(f, transportFixture(), {
        phase: async (phase) => {
          if (phase === "transport:invoked") invoked += 1;
          if (!swapped && phase === "transport:received") {
            swapped = true;
            const target = path.join(transactionRoot(f, tx.disclosure.transactionSha256), name);
            const bytes = await readFile(target);
            const displaced = path.join(f.config.temporaryRoot, `displaced-${name}`);
            await rename(target, displaced);
            await writeFile(target, bytes, { flag: "wx" });
          }
        },
      });
      await assert.rejects(() => execute(tx), /R22_DIAGNOSTIC_PATH_CHANGED/u);
      await tx.close();
      assert.equal(swapped, true);
      assert.equal(invoked, 1);
      const budget = await savedBudget(f);
      assert.equal(budget.entries.reduce((sum, entry) => sum + entry.chargedMicrousd, 0), 10_123);
      await assert.rejects(() => lstat(f.config.output), (error) => error?.code === "ENOENT");
    });
  }
});

test("transport record tampering or deletion cannot become a zero-request recovery", async (t) => {
  for (const mutation of ["status", "dispatch-hash", "delete"]) {
    await t.test(mutation, async (child) => {
      const f = await setup(child);
      const tx = await createInjected(f);
      const result = await execute(tx);
      await tx.close();
      const target = path.join(transactionRoot(f, result.transactionSha256), "transport-record.json");
      if (mutation === "delete") await rm(target);
      else {
        const value = JSON.parse(await readFile(target, "utf8"));
        if (mutation === "status") value.status = "credential_unavailable";
        else value.dispatchRecordSha256 = h("9");
        await writeFile(target, canonical(value));
      }
      const budgetBefore = canonical(await savedBudget(f));
      const current = await currentConfig(f);
      await assert.rejects(() => recoverR22InjectedToolUsageDiagnosticTransaction(current, result.transactionSha256, testOverrides()), /^Error: R22_/u);
      assert.equal(canonical(await savedBudget(f)), budgetBefore);
    });
  }
});

test("an official root permanently consumes its single diagnostic claim without a request", async (t) => {
  const f = await setup(t, { providerMode: "official-once", outputName: "official-one" });
  const tx = await createR22OfficialToolUsageDiagnosticTransaction(f.config);
  const result = await tx.cancel();
  await tx.close();
  assert.equal(result.terminal.transportRequestCount, 0);
  const recovered = await recoverR22OfficialToolUsageDiagnosticTransaction(await currentConfig(f), result.transactionSha256);
  assert.equal(canonical(recovered), canonical(result));
  const next = await currentConfig(f, "official-two");
  await assertRejectCode(() => createR22OfficialToolUsageDiagnosticTransaction(next), "R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
  assert.equal((await readdir(path.join(f.config.cognitionRunRoot, "diagnostics"))).length, 1);
});

test("official recovery and history audit both reject a root containing two diagnostic directories", async (t) => {
  const f = await setup(t, { providerMode: "official-once" });
  const tx = await createR22OfficialToolUsageDiagnosticTransaction(f.config);
  const result = await tx.cancel();
  await tx.close();
  const parent = path.join(f.config.cognitionRunRoot, "diagnostics");
  await mkdir(path.join(parent, "8".repeat(64)));
  const before = canonical(await savedBudget(f));
  const config = await currentConfig(f);
  const budget = await savedBudget(f);
  await assertRejectCode(() => recoverR22OfficialToolUsageDiagnosticTransaction(config, result.transactionSha256), "R22_DIAGNOSTIC_OFFICIAL_ALREADY_CLAIMED");
  await assert.rejects(() => auditR22DiagnosticBudgetHistory({
    cognitionRunRoot: f.config.cognitionRunRoot,
    hostRunId: HOST_RUN_ID,
    budget,
    remember: (target) => readFile(target, "utf8"),
    rememberDirectory: async (target) => (await readdir(target)).sort(),
  }), /R22_DIAGNOSTIC_TRANSACTION_INVALID/u);
  assert.equal(canonical(await savedBudget(f)), before);
});

test("official describe and CLI plan are byte-preserving, and wrong approval never executes", async (t) => {
  const f = await setup(t, { providerMode: "official-once" });
  const before = await treeSnapshot(f.config.cognitionRunRoot);
  const disclosure = await describeR22OfficialToolUsageDiagnosticTransaction(f.config);
  assert.deepEqual(disclosure.transactionPlan.accountSummary, {
    entryCount: 1,
    chargedMicrousd: 123,
    reservedMicrousd: 0,
    limitMicrousd: 1_000_000,
    additionalRequestLimit: 1,
    additionalCeilingMicrousd: 10_000,
  });
  assert.deepEqual(await treeSnapshot(f.config.cognitionRunRoot), before);

  const cli = await setupCliRoot(t);
  const cliBefore = await treeSnapshot(cli.cognitionRunRoot);
  const planned = await runR22ToolUsageDiagnostic(["plan", ...cli.args]);
  assert.equal(HASH.test(planned.transactionSha256), true);
  assert.equal(planned.transactionPlan.executionKind, "official-once");
  assert.equal(planned.transactionPlan.sourceProviderMode, "official-once");
  assert.deepEqual(planned.transactionPlan.accountSummary, disclosure.transactionPlan.accountSummary);
  assert.deepEqual(await treeSnapshot(cli.cognitionRunRoot), cliBefore);
  await assertRejectCode(() => runR22ToolUsageDiagnostic(["execute", ...cli.args, "--approve-disclosure", h("9")]), "R22_APPROVAL_MISMATCH");
  assert.deepEqual(await treeSnapshot(cli.cognitionRunRoot), cliBefore);
  await assert.rejects(() => lstat(cli.output), (error) => error?.code === "ENOENT");
});

test("boundary audit permits the actual adapter only at its exact path", async (t) => {
  const f = await setup(t);
  const policy = JSON.parse(await readFile(path.join(MODULE_ROOT, "module-boundary.json"), "utf8"));
  const source = await readFile(path.join(MODULE_ROOT, "scripts", "lib", "r22-diagnostic-transaction.mjs"), "utf8");

  const exactRoot = path.join(f.config.temporaryRoot, "boundary-exact");
  const exactRelative = "scripts/lib/r22-diagnostic-transaction.mjs";
  await mkdir(path.join(exactRoot, "scripts", "lib"), { recursive: true });
  await writeFile(path.join(exactRoot, ...exactRelative.split("/")), source, { flag: "wx" });
  const exactReport = await auditBoundary({ moduleRoot: exactRoot, policy, trackedFiles: [exactRelative] });
  assert.equal(exactReport.ok, true, canonical(exactReport));

  const copiedRoot = path.join(f.config.temporaryRoot, "boundary-copy");
  const copiedRelative = "scripts/lib/r22-diagnostic-copy.mjs";
  await mkdir(path.join(copiedRoot, "scripts", "lib"), { recursive: true });
  await writeFile(path.join(copiedRoot, ...copiedRelative.split("/")), source, { flag: "wx" });
  const copiedReport = await auditBoundary({ moduleRoot: copiedRoot, policy, trackedFiles: [copiedRelative] });
  assert.equal(copiedReport.ok, false);
  assert.equal(copiedReport.violations.some((violation) => violation.rule === "script-network-forbidden" && violation.path === copiedRelative), true);

  const leaks = [
    ["export-private-sender", source.replace("async function sendDiagnosticRequest(", "export async function sendDiagnosticRequest(")],
    ["export-native-handle", `${source}\nexport const leak = nativeRequest;\n`],
    ["export-create-transaction", source.replace("async function createTransaction(", "export async function createTransaction(")],
    ["duplicate-credential-preparer", `${source}\nvoid prepareR22FileCredentialReader({});\n`],
    ["duplicate-credential-read", `${source}\nvoid operations.readCredential();\n`],
    ["direct-credential-read", source.replace("const sender = state.credentialReader ?", "const sender = state.credentialReader() ?")],
    ...["call", "apply", "bind"].map((method) => [`credential-${method}`, source.replace("const sender = state.credentialReader ?", `const sender = state.credentialReader.${method}(null) ?`)]),
    ["aliased-credential-read", `${source}\nconst extraReader = state.credentialReader;\nvoid extraReader();\n`],
    ["removed-credential-presence", source.replace('Object.hasOwn(process.env, "MATRIX_OASIS_R22_OPENAI_API_KEY")', "true")],
    ["presence-becomes-value-read", source.replace('Object.hasOwn(process.env, "MATRIX_OASIS_R22_OPENAI_API_KEY")', "Boolean(process.env.MATRIX_OASIS_R22_OPENAI_API_KEY)")],
    ["removed-credential-preclaim-guard", source.replace('!credentialConfiguration(state).configured) fail("R22_DIAGNOSTIC_CREDENTIAL_NOT_CONFIGURED")', 'false) fail("R22_DIAGNOSTIC_CREDENTIAL_NOT_CONFIGURED")')],
    ["removed-additional-zero-request-gate", source.replace('transport.status !== "credential_unavailable" || transport.requestCount !== 0', 'transport.status !== "credential_unavailable"')],
    ["expanded-additional-limit", source.replace('grant.officialDiagnosticLimit !== 2', 'grant.officialDiagnosticLimit !== 3')],
    ["removed-additional-history-link", source.replace('validateAdditionalLink(additional[0].plan, official.find((item) => item !== additional[0]));', '')],
    ["expanded-billing-limit", source.replace('grant.officialDiagnosticLimit !== 3', 'grant.officialDiagnosticLimit !== 4')],
    ["removed-billing-profile", source.replace('config.captureProfile !== "billing" || typeof config.billingAfterTransactionSha256 !== "string"', 'typeof config.billingAfterTransactionSha256 !== "string"')],
    ["removed-billing-observed-gate", source.replace('terminal.state !== "observed" || terminal.realRequestCount !== 1', 'terminal.realRequestCount !== 1')],
    ["removed-billing-history-link", source.replace('validateBillingLink(billing[0].plan, previous);', '')],
    ["removed-billing-prefix-link", source.replace('validateOfficialHistory(prefix);', '')],
    ["removed-billing-credential-link", source.replace('canonicalText(plan.credentialSource) !== canonicalText(previous.plan.credentialSource) ||', '')],
    ["removed-billing-preclaim-credential-link", source.replace('credentialConfiguration(state).identitySha256 !== previous.plan.credentialSource.identitySha256', 'false')],
  ];
  const globalCapability = ["globalThis", ["fet", "ch"].join("")].join(".");
  leaks.push(["global-capability-typeguard", `${source.replace(
    'if (typeof nativeRequest !== "function")',
    `if (typeof ${globalCapability} !== "function")`,
  )}\nvoid nativeRequest;\n`]);
  for (const [name, content] of leaks) {
    const leakRoot = path.join(f.config.temporaryRoot, `boundary-${name}`);
    await mkdir(path.join(leakRoot, "scripts", "lib"), { recursive: true });
    await writeFile(path.join(leakRoot, ...exactRelative.split("/")), content, { flag: "wx" });
    const report = await auditBoundary({ moduleRoot: leakRoot, policy, trackedFiles: [exactRelative] });
    assert.equal(report.ok, false);
    assert.equal(report.violations.some((violation) => violation.rule === "r22-diagnostic-network-invalid" && violation.path === exactRelative), true);
  }
});

test("legacy offline fixture keeps its version and never emits a transport record", async (t) => {
  const f = await setup(t);
  const tx = await createR22ToolUsageDiagnosticTransaction(f.config, testOverrides());
  const approval = await tx.approve({ disclosureSha256: tx.disclosure.transactionSha256 });
  const result = await tx.execute({ ...approval, responseBytes: validResponseBytes() });
  await tx.close();
  assert.equal(result.terminal.formatVersion, "0.1.0");
  assert.equal(result.terminal.executionKind, "offline-fixture");
  assert.equal(result.terminal.realRequestCount, 0);
  assert.equal(Object.hasOwn(result.terminal, "transportRequestCount"), false);
  assert.equal(HASH.test(result.transactionSha256), true);
  assert.equal((await readdir(transactionRoot(f, result.transactionSha256))).includes("transport-record.json"), false);
});

test("billing CLI disclosure is explicit, content-bound and metadata-only; legacy approvals cannot execute it", async (t) => {
  const f = await setupCredentialCli(t), opens = observeCredentialOpens(t, f.credentialFile), reads = forbidEnvironmentCredential(t);
  const before = await treeSnapshot(f.cognitionRunRoot);
  const legacy = await runR22ToolUsageDiagnostic(["plan", ...f.fileArgs]);
  const explicitLegacy = await runR22ToolUsageDiagnostic(["plan", ...f.fileArgs, "--capture-profile", "tool-usage"]);
  assert.equal(canonical(explicitLegacy), canonical(legacy));
  const args = [...f.fileArgs, "--capture-profile", "billing"];
  const billing = await runR22ToolUsageDiagnostic(["plan", ...args]);
  assert.deepEqual(billing.diagnosticPlan, createNpcCognitionBillingDiagnosticPlan());
  assert.equal(sha(canonical(billing.capturePolicy)), billing.diagnosticPlan.capturePolicySha256);
  assert.equal(billing.capturePolicy.numericValues, "never_retained_or_interpreted_as_cost");
  assert.deepEqual(billing.capturePolicy.paths, ["payer", "amount", "currency", "service_tier", "tool_costs", "tool_costs.total"]);
  assert.equal(Object.hasOwn(legacy, "capturePolicy"), false);
  assert.equal(billing.diagnosticPlan.providerRequestJson, legacy.diagnosticPlan.providerRequestJson);
  assert.notEqual(billing.transactionSha256, legacy.transactionSha256);
  for (let i = 0; i < 20; i += 1) assert.equal(canonical(await runR22ToolUsageDiagnostic(["plan", ...args])), canonical(billing));
  for (const approval of [legacy.transactionSha256, legacy.diagnosticPlan.approvalHash, billing.diagnosticPlan.diagnosticApprovalSha256]) {
    await assertRejectCode(() => runR22ToolUsageDiagnostic(["execute", ...args, "--approve-disclosure", approval]), "R22_APPROVAL_MISMATCH");
  }
  for (const bad of ["both", "arbitrary", "billing/2"]) await assertRejectCode(
    () => runR22ToolUsageDiagnostic(["plan", ...f.fileArgs, "--capture-profile", bad]), "R22_CLI_ARGUMENT_INVALID");
  assert.deepEqual(await treeSnapshot(f.cognitionRunRoot), before);
  assert.deepEqual([opens(), reads()], [0, 0]);
});

test("billing sealed official path is intercepted locally: one synthetic request, no true secret and no replay", async (t) => {
  const secret = ["sk", "billing-fixture".repeat(4)].join("-");
  const f = await setupCredentialCli(t, secret), reads = forbidEnvironmentCredential(t), opens = observeCredentialOpens(t, f.credentialFile);
  let sends = 0;
  t.mock.method(globalThis, "fetch", async (_endpoint, options) => {
    sends += 1;
    assert.equal(options.body, createNpcCognitionBillingDiagnosticPlan().providerRequestJson);
    assert.equal(options.headers.authorization, `Bearer ${secret}`);
    assert.equal(options.redirect, "error");
    const budget = await savedBudget(f);
    assert.equal(budget.entries.at(-1).state, "charged");
    return new Response(billingResponseBytes(), { headers: { "content-type": "application/json" } });
  });
  const localOnly = await import("../scripts/lib/r22-diagnostic-transaction.mjs?fixture=billing-wiring");
  const config = { ...f.config, captureProfile: "billing" };
  const disclosed = await localOnly.describeR22OfficialToolUsageDiagnosticTransaction(config);
  const tx = await localOnly.createR22OfficialToolUsageDiagnosticTransaction({ ...config, expectedDisclosureSha256: disclosed.transactionSha256 });
  const approval = await tx.approve({ disclosureSha256: disclosed.transactionSha256 });
  assert.deepEqual([sends, opens(), reads()], [0, 0, 0]);
  const results = await Promise.all(Array.from({ length: 20 }, () => tx.execute(approval))); await tx.close();
  assert.equal(new Set(results.map(canonical)).size, 1);
  assert.deepEqual([sends, opens(), reads()], [1, 1, 0]);
  assert.equal(results[0].terminal.qualificationEligible, false);
  const observation = JSON.parse(await readFile(path.join(f.output, "observation-record.json"), "utf8"));
  assert.equal(observation.observation.profile, createNpcCognitionBillingDiagnosticPlan().diagnosticProfile);
  assert.equal(observation.observation.paths[1].category, "type_only");
  const budgetBefore = await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8");
  const { credentialFile: _file, ...recovery } = config;
  recovery.expectedHostBudgetSha256 = sha(budgetBefore);
  await rm(f.credentialFile);
  for (let i = 0; i < 20; i += 1) {
    assert.equal(canonical(await localOnly.recoverR22OfficialToolUsageDiagnosticTransaction(recovery, results[0].transactionSha256)), canonical(results[0]));
  }
  assert.equal(await readFile(path.join(f.cognitionRunRoot, "host-budget.json"), "utf8"), budgetBefore);
  assert.deepEqual([sends, opens(), reads()], [1, 1, 0]);
  const persisted = await allText(f.cognitionRunRoot) + await allText(f.output);
  for (const bait of [secret, sha(secret), f.credentialFile, "PRIVATE_BILLING_", "998877", "fixture-dialogue-placeholder"]) assert.equal(persisted.includes(bait), false);
});

test("billing and tool observations share one budget history without rewriting legacy bytes", async (t) => {
  const f = await setup(t), legacy = await createInjected(f);
  const old = await execute(legacy); await legacy.close();
  const before = await treeSnapshot(transactionRoot(f, old.transactionSha256));
  const oldOutput = await treeSnapshot(f.config.output);
  const billingConfig = { ...await currentConfig(f, "billing"), captureProfile: "billing" };
  const next = await createR22InjectedToolUsageDiagnosticTransaction(billingConfig, transportFixture({ responseBytes: billingResponseBytes() }), testOverrides());
  const billing = await execute(next); await next.close();
  const budget = await savedBudget(f);
  assert.equal(budget.entries.reduce((sum, entry) => sum + entry.chargedMicrousd, 0), 20123);
  const audited = await auditR22DiagnosticBudgetHistory({ cognitionRunRoot: f.config.cognitionRunRoot, hostRunId: HOST_RUN_ID, budget,
    remember: async (target) => readFile(target, "utf8"), rememberDirectory: async (target) => (await readdir(target)).sort() });
  assert.equal(audited.size, 2);
  assert.equal(canonical(await recoverR22InjectedToolUsageDiagnosticTransaction(await currentConfig(f), old.transactionSha256)), canonical(old));
  assert.equal(canonical(await recoverR22InjectedToolUsageDiagnosticTransaction({ ...billingConfig, expectedHostBudgetSha256: sha(canonical(budget)) }, billing.transactionSha256)), canonical(billing));
  assert.deepEqual(await treeSnapshot(transactionRoot(f, old.transactionSha256)), before);
  assert.deepEqual(await treeSnapshot(f.config.output), oldOutput);
});

test("billing transport failure matrix keeps redaction, ceilings and zero recovery requests", async (t) => {
  const cases = [
    ["response", { responseBytes: billingResponseBytes() }, "observed", "response"],
    ["over-limit", { responseBytes: new Uint8Array(65537) }, "transport_failed", "response_limit"],
    ["utf8", { responseBytes: Uint8Array.of(255) }, "observed", "response"],
    ["bad-json", { responseBytes: new TextEncoder().encode("{PRIVATE_TRUNCATED") }, "observed", "response"],
    ["5xx", { status: 503 }, "transport_failed", "http_error"],
    ["redirect", { redirected: true }, "transport_failed", "network_ambiguous"],
    ["timeout", { scenario: "request-timeout", timeoutMs: 1000 }, "transport_failed", "timeout"],
    ["body-timeout", { scenario: "body-timeout", timeoutMs: 1000 }, "transport_failed", "timeout"],
    ["ambiguous", { scenario: "network-reject" }, "transport_failed", "network_ambiguous"],
    ["missing-credential", { scenario: "credential-missing" }, "transport_failed", "credential_unavailable"],
  ];
  for (const [name, changes, state, status] of cases) await t.test(name, async (child) => {
    const f = await setup(child); f.config.captureProfile = "billing";
    let invokes = 0;
    const tx = await createInjected(f, transportFixture({ responseBytes: billingResponseBytes(), ...changes }), {
      phase: async (phase) => { if (phase === "transport:invoked") invokes += 1; } });
    const result = await execute(tx); await tx.close();
    assert.equal(result.terminal.state, state); assert.equal(result.terminal.chargedMicrousd, 10000);
    assert.equal((await transportRecord(f, result.transactionSha256)).status, status);
    assert.equal(invokes, name === "missing-credential" ? 0 : 1);
    if (["utf8", "bad-json"].includes(name)) {
      const record = JSON.parse(await readFile(path.join(f.config.output, "observation-record.json"), "utf8"));
      assert.equal(record.observation.status, "not_captured");
    }
    const recovered = await recoverR22InjectedToolUsageDiagnosticTransaction(await currentConfig(f), result.transactionSha256);
    assert.equal(canonical(recovered), canonical(result)); assert.equal(recovered.terminal.providerReplayRequests, 0);
    assert.doesNotMatch(await allText(f.config.output), /PRIVATE_|998877/u);
  });
});
