import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { lstat, mkdir, mkdtemp, readFile, readdir, realpath, rename, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test, { describe } from "node:test";
import { canonicalizeJsonValue as canonical } from "@matrix-oasis/runtime-pack-contracts";
import { createNpcCognitionToolUsageDiagnosticPlan as legacyPlan, createNpcCognitionBillingDiagnosticPlan } from "@matrix-oasis/npc-cognition-provider-openai";
import { createR22ToolUsageDiagnosticTransaction, recoverR22ToolUsageDiagnosticTransaction, auditR22DiagnosticBudgetHistory } from "../scripts/lib/r22-diagnostic-transaction.mjs";

for (const captureProfile of ["tool-usage", "billing"]) describe(`transaction profile ${captureProfile}`, () => {
const createNpcCognitionToolUsageDiagnosticPlan = captureProfile === "billing" ? createNpcCognitionBillingDiagnosticPlan : legacyPlan;
const sha = (value) => `sha256:${createHash("sha256").update(value).digest("hex")}`;
const h = (digit) => `sha256:${digit.repeat(64)}`;
async function setup(t, { providerMode = "offline-fake", oldCharge = 123, hostRunId = "host-fixture" } = {}) {
  const temporaryRoot = await mkdtemp(path.join(tmpdir(), "rd-"));
  const initial = await lstat(temporaryRoot, { bigint: true });
  t.after(async () => {
    const current = await lstat(temporaryRoot, { bigint: true });
    assert.equal(current.isSymbolicLink(), false); assert.equal(await realpath(temporaryRoot), temporaryRoot);
    assert.equal(current.dev, initial.dev); assert.equal(current.ino, initial.ino);
    assert.equal(path.dirname(temporaryRoot), path.resolve(tmpdir()));
    await rm(temporaryRoot, { recursive: true, force: false });
  });
  const cognitionRunRoot = path.join(temporaryRoot, "run-cognition"), output = path.join(temporaryRoot, "observation");
  await mkdir(cognitionRunRoot);
  const manifest = { format: "matrix-oasis.r22-cognition-session-manifest", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1", hostRunId, providerMode,
    initialTimelineId: "timeline-fixture", sourceCurrentSha256: h("a"), sourceDerivedBundleSha256: h("b"), sourceAuthorityManifestSha256: h("c"),
    cognitionPolicySha256: h("d"), implementationSha256: h("e"), godotBinarySha256: h("f") };
  const budget = { format: "matrix-oasis.r22-host-budget", formatVersion: "0.1.0", canonicalization: "matrix-oasis.canonical-json/1", hostRunId,
    limitMicrousd: 1000000, entries: oldCharge === null ? [] : [{ authoritySessionSha256: h("1"), callPlanSha256: h("2"), reservedMicrousd: 10000, chargedMicrousd: oldCharge, state: "charged" }] };
  await writeFile(path.join(cognitionRunRoot, "cognition-session-manifest.json"), canonical(manifest));
  await writeFile(path.join(cognitionRunRoot, "host-budget.json"), canonical(budget));
  const config = { temporaryRoot, cognitionRunRoot, hostRunId, expectedSessionManifestSha256: sha(canonical(manifest)), expectedHostBudgetSha256: sha(canonical(budget)), output,
    ...(captureProfile === "billing" ? { captureProfile } : {}) };
  return { config, budget, manifest };
}
async function currentConfig(f) { return { ...f.config, expectedHostBudgetSha256: sha(await readFile(path.join(f.config.cognitionRunRoot, "host-budget.json"))) }; }
function response(toolUsage = { image_gen: { total_tokens: 0 } }, modify = () => {}) {
  const plan = createNpcCognitionToolUsageDiagnosticPlan();
  const value = { id: "resp_fixture", object: "response", status: "completed", error: null, incomplete_details: null,
    model: "gpt-5.6-luna", service_tier: "default", output: [{ id: "msg_fixture", type: "message", status: "completed", role: "assistant",
      content: [{ type: "output_text", text: JSON.stringify({ contextSha256: JSON.parse(plan.callPlanJson).contextSha256, dialogueText: "PRIVATE_DIALOGUE_BAIT", actionChoiceId: null }), annotations: [] }] }],
    usage: { input_tokens: 20, input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 }, output_tokens: 10, output_tokens_details: { reasoning_tokens: 0 }, total_tokens: 30 }, tool_usage: toolUsage };
  if (captureProfile === "billing") value.billing = { payer: "developer", amount: 998877.25, currency: "usd",
    tool_costs: { total: 998877.25 }, PRIVATE_BILLING_NAME: "PRIVATE_BILLING_VALUE" };
  modify(value); return new TextEncoder().encode(JSON.stringify(value));
}
async function create(f, overrides) { return createR22ToolUsageDiagnosticTransaction(f.config, { clock: () => 1000, randomBytes: () => new Uint8Array(32).fill(7), ...overrides }); }
async function execute(tx, bytes = response()) { const approval = await tx.approve({ disclosureSha256: tx.disclosure.transactionSha256 }); return tx.execute({ ...approval, responseBytes: bytes }); }
async function savedBudget(f) { return JSON.parse(await readFile(path.join(f.config.cognitionRunRoot, "host-budget.json"), "utf8")); }
async function allText(root) { let result = ""; for (const name of await readdir(root, { withFileTypes: true })) { const file = path.join(root, name.name); result += name.isDirectory() ? await allText(file) : await readFile(file, "utf8"); } return result; }
function transactionRoot(f, id) { return path.join(f.config.cognitionRunRoot, "diagnostics", id.slice(7)); }
async function audit(f) {
  const records = [], directories = [];
  const result = await auditR22DiagnosticBudgetHistory({ cognitionRunRoot: f.config.cognitionRunRoot, hostRunId: f.config.hostRunId, budget: await savedBudget(f),
    remember: async (file) => { records.push(file); return readFile(file, "utf8"); },
    rememberDirectory: async (file) => { directories.push(file); return (await readdir(file)).sort(); } });
  return { result, records, directories };
}

test("shared-account diagnostic publishes only redacted evidence and keeps every ordinary byte outside budget", async (t) => {
  const f = await setup(t), beforeManifest = await readFile(path.join(f.config.cognitionRunRoot, "cognition-session-manifest.json"), "utf8");
  const tx = await create(f); const result = await execute(tx, response({ image_gen: { total_tokens: 0 }, PRIVATE_KEY_NAME: "PRIVATE_VALUE_BAIT" })); await tx.close();
  assert.equal(result.terminal.realRequestCount, 0); assert.equal(result.terminal.dispatchCount, 1); assert.equal(result.terminal.state, "observed");
  assert.equal(result.terminal.chargedMicrousd, 10000); assert.equal(result.terminal.qualificationEligible, false);
  const budget = await savedBudget(f); assert.equal(budget.entries.length, 2); assert.deepEqual(budget.entries.find((entry) => entry.authoritySessionSha256 === h("1")), f.budget.entries[0]);
  assert.equal(await readFile(path.join(f.config.cognitionRunRoot, "cognition-session-manifest.json"), "utf8"), beforeManifest);
  assert.equal((await readdir(f.config.cognitionRunRoot)).includes("timelines"), false);
  const text = await allText(f.config.cognitionRunRoot) + await allText(f.config.output);
  for (const bait of ["PRIVATE_KEY_NAME", "PRIVATE_VALUE_BAIT", "PRIVATE_DIALOGUE_BAIT", sha("PRIVATE_VALUE_BAIT")]) assert.equal(text.includes(bait), false);
  const checked = await audit(f); assert.equal(checked.result.size, 1);
});

test("official source, missing history and a new output root cannot create a paid diagnostic account", async (t) => {
  const f = await setup(t, { providerMode: "official-once" });
  await assert.rejects(create(f), /R22_DIAGNOSTIC_LIVE_DISABLED/u);
  assert.deepEqual((await readdir(f.config.cognitionRunRoot)).sort(), ["cognition-session-manifest.json", "host-budget.json"]);
  assert.deepEqual(await savedBudget(f), f.budget);
  const missing = await setup(t); await rm(path.join(missing.config.cognitionRunRoot, "host-budget.json"));
  await assert.rejects(create(missing), /^Error: R22_/u); assert.equal((await readdir(missing.config.cognitionRunRoot)).includes("host-budget.json"), false);
});

test("ordinary, capture-only and changed-output approval hashes all fail before reservation", async (t) => {
  for (const kind of ["ordinary", "capture", "output"]) {
    const f = await setup(t), tx = await create(f), fixed = createNpcCognitionToolUsageDiagnosticPlan();
    const hash = kind === "ordinary" ? fixed.approvalHash : kind === "capture" ? fixed.diagnosticApprovalSha256 : h("9");
    await assert.rejects(tx.approve({ disclosureSha256: hash }), /R22_APPROVAL_MISMATCH/u);
    await assert.rejects(tx.execute({ approvalTokenSha256: hash, responseBytes: response() }), /R22_APPROVAL_MISMATCH/u);
    await tx.close(); assert.deepEqual(await savedBudget(f), f.budget);
  }
});

test("20 concurrent identical turns execute and publish a single fixture; different content conflicts", async (t) => {
  const f = await setup(t); let count = 0;
  const tx = await create(f, { phase: async (phase) => { if (phase === "fixture:before_execute") count += 1; } });
  const approval = await tx.approve({ disclosureSha256: tx.disclosure.transactionSha256 }), input = { ...approval, responseBytes: response() };
  const results = await Promise.all(Array.from({ length: 20 }, () => tx.execute(input)));
  assert.equal(new Set(results.map(canonical)).size, 1); assert.equal(count, 1);
  await assert.rejects(tx.execute({ ...input, responseBytes: response({ image_gen: { total_tokens: 1 } }) }), /R22_APPROVAL_MISMATCH/u);
  await tx.close(); assert.equal((await savedBudget(f)).entries.length, 2);
});

for (const offset of [300000, 300001, -1]) test(`approval time boundary ${offset} permits no reservation or execution`, async (t) => {
  const f = await setup(t); let clock = 1000, calls = 0;
  const tx = await create(f, { clock: () => clock, phase: async (phase) => { if (phase === "fixture:before_execute") calls += 1; } });
  const approval = await tx.approve({ disclosureSha256: tx.disclosure.transactionSha256 }); clock += offset;
  await assert.rejects(tx.execute({ ...approval, responseBytes: response() }), /^Error: R22_/u);
  await tx.close(); assert.equal(calls, 0); assert.deepEqual(await savedBudget(f), f.budget);
});

test("explicit cancel and restart before reservation are zero-dispatch terminal evidence, not reusable approvals", async (t) => {
  for (const restart of [false, true]) {
    const f = await setup(t), tx = await create(f); await tx.approve({ disclosureSha256: tx.disclosure.transactionSha256 });
    const id = tx.disclosure.transactionSha256;
    let result;
    if (restart) { await tx.close(); result = await recoverR22ToolUsageDiagnosticTransaction(await currentConfig(f), id); }
    else { result = await tx.cancel(); await tx.close(); }
    assert.equal(result.terminal.state, "cancelled"); assert.equal(result.terminal.dispatchCount, 0); assert.equal(result.terminal.chargedMicrousd, 0);
    assert.deepEqual(await savedBudget(f), f.budget); assert.equal((await audit(f)).result.size, 0);
    await assert.rejects(createR22ToolUsageDiagnosticTransaction(await currentConfig(f)), /R22_DIAGNOSTIC_OUTPUT_EXISTS/u);
  }
});

const crashCases = [
  ["budget:before_reserve", "cancelled", 0], ["budget:reserved", "cancelled", 0],
  ["dispatch-record.json:published", "dispatch_uncertain", 10000], ["fixture:before_execute", "dispatch_uncertain", 10000],
  ["fixture:received", "dispatch_uncertain", 10000], ["observation-record.json:published", "observed", 10000],
  ["budget:settled", "observed", 10000], ["output:staged", "observed", 10000],
  ["output:published", "observed", 10000], ["terminal-record.json:published", "observed", 10000],
];
for (const [fault, expected, charge] of crashCases) test(`crash at ${fault} recovers conservatively without replay`, async (t) => {
  const f = await setup(t); let injected = false, executions = 0;
  const tx = await create(f, { phase: async (phase) => { if (phase === "fixture:before_execute") executions += 1; if (!injected && phase === fault) { injected = true; throw new Error("PRIVATE_CRASH_BAIT"); } } });
  const id = tx.disclosure.transactionSha256; await assert.rejects(execute(tx), /^Error: R22_/u); await tx.close();
  assert.equal(injected, true);
  const countBefore = executions, result = await recoverR22ToolUsageDiagnosticTransaction(await currentConfig(f), id, { phase: async (phase) => { if (phase === "fixture:before_execute") executions += 1; } });
  assert.equal(result.terminal.state, expected); assert.equal(result.terminal.chargedMicrousd, charge); assert.equal(result.terminal.providerReplayRequests, 0); assert.equal(executions, countBefore);
  const budget = await savedBudget(f); assert.deepEqual(budget.entries.find((entry) => entry.authoritySessionSha256 === h("1")), f.budget.entries[0]);
  assert.equal(budget.entries.reduce((sum, entry) => sum + entry.chargedMicrousd, 0), 123 + charge);
  const second = await recoverR22ToolUsageDiagnosticTransaction(await currentConfig(f), id); assert.equal(canonical(second), canonical(result));
  assert.equal((await allText(f.config.cognitionRunRoot)).includes("PRIVATE_CRASH_BAIT"), false);
  await audit(f);
});

for (const fault of ["approval-record.json:staged", "dispatch-record.json:staged", "observation-record.json:staged", "terminal-record.json:staged", "output:approval-record.json:written"]) test(`incomplete stage ${fault} blocks recovery, never resends or releases uncertain charge`, async (t) => {
  const f = await setup(t), tx = await create(f, { phase: async (phase) => { if (phase === fault) throw new Error("fixture fault"); } });
  const id = tx.disclosure.transactionSha256; await assert.rejects(execute(tx), /^Error: R22_/u); await tx.close();
  const budget = canonical(await savedBudget(f));
  await assert.rejects(recoverR22ToolUsageDiagnosticTransaction(await currentConfig(f), id), /^Error: R22_/u);
  assert.equal(canonical(await savedBudget(f)), budget);
});

test("response refusal, wrong model/tier, malformed bytes and tool usage never change business state or reduce reservation", async (t) => {
  const fixtures = [response({}, (r) => { r.model = "other-model"; }), response({}, (r) => { r.service_tier = "priority"; }),
    response({}, (r) => { r.output[0].content = [{ type: "refusal", refusal: "PRIVATE_REFUSAL_BAIT" }]; }),
    new Uint8Array([255]), new TextEncoder().encode('{"tool_usage":'), response({ unknown: { total_tokens: 99999 } })];
  for (const bytes of fixtures) {
    const f = await setup(t), tx = await create(f); const result = await execute(tx, bytes); await tx.close();
    assert.equal(result.terminal.chargedMicrousd, 10000); assert.equal(result.terminal.realRequestCount, 0);
    assert.equal((await allText(f.config.output)).includes("PRIVATE_REFUSAL_BAIT"), false);
    assert.equal((await readdir(f.config.cognitionRunRoot)).includes("timelines"), false);
  }
});

for (const name of ["transaction-plan.json", "approval-record.json", "dispatch-record.json", "observation-record.json"]) test(`tampering with ${name} cannot become valid evidence`, async (t) => {
  const f = await setup(t), tx = await create(f); const id = tx.disclosure.transactionSha256;
  await execute(tx); await tx.close(); const target = path.join(transactionRoot(f, id), name);
  const value = JSON.parse(await readFile(target, "utf8")); value.PRIVATE_UNKNOWN_FIELD = 0; await writeFile(target, canonical(value));
  const budget = canonical(await savedBudget(f));
  await assert.rejects(recoverR22ToolUsageDiagnosticTransaction(await currentConfig(f), id), /^Error: R22_/u);
  await assert.rejects(audit(f), /^Error: R22_/u); assert.equal(canonical(await savedBudget(f)), budget);
});

test("dispatch deletion or budget deletion is not interpreted as a zero-request diagnostic", async (t) => {
  for (const erase of ["dispatch", "budget-entry"]) {
    const f = await setup(t), tx = await create(f); const id = tx.disclosure.transactionSha256; await execute(tx); await tx.close();
    if (erase === "dispatch") await rm(path.join(transactionRoot(f, id), "dispatch-record.json"));
    else { const budget = await savedBudget(f); budget.entries = budget.entries.filter((entry) => entry.authoritySessionSha256 === h("1")); await writeFile(path.join(f.config.cognitionRunRoot, "host-budget.json"), canonical(budget)); }
    await assert.rejects(audit(f), /^Error: R22_/u);
    await assert.rejects(recoverR22ToolUsageDiagnosticTransaction(await currentConfig(f), id), /^Error: R22_/u);
  }
});

test("a completed dispatch cannot release a precharged account after observation loss", async (t) => {
  const f = await setup(t), tx = await create(f, { phase: async (phase) => { if (phase === "fixture:before_execute") throw new Error("stop"); } });
  const id = tx.disclosure.transactionSha256; await assert.rejects(execute(tx)); await tx.close();
  const result = await recoverR22ToolUsageDiagnosticTransaction(await currentConfig(f), id);
  assert.equal(result.terminal.state, "dispatch_uncertain"); assert.equal(result.terminal.chargedMicrousd, 10000);
  assert.equal(result.terminal.observationRecordSha256, null);
});

test("live in-memory source identity swap blocks fixture execution", async (t) => {
  const f = await setup(t); let tx, calls = 0;
  tx = await create(f, { phase: async (phase) => {
    if (phase === "budget:reserved") {
      const root = transactionRoot(f, tx.disclosure.transactionSha256); await rename(root, `${root}-old`); await mkdir(root);
      for (const name of await readdir(`${root}-old`)) await writeFile(path.join(root, name), await readFile(path.join(`${root}-old`, name)));
    }
    if (phase === "fixture:before_execute") calls += 1;
  } });
  await assert.rejects(execute(tx), /R22_DIAGNOSTIC_PATH_CHANGED/u); await tx.close(); assert.equal(calls, 0);
});

test("fixed fake input yields 20 byte-identical terminal and observation records without wall clock text", async (t) => {
  const f = await setup(t), tx = await create(f); const result = await execute(tx); await tx.close();
  const expected = await readFile(path.join(f.config.output, "diagnostic-report.json"), "utf8");
  for (let n = 0; n < 20; n += 1) {
    const current = await recoverR22ToolUsageDiagnosticTransaction(await currentConfig(f), result.transactionSha256);
    assert.equal(canonical(current.terminal), expected);
    assert.equal(current.terminal.providerReplayRequests, 0);
  }
});

test("fixture path never calls ambient network or reads supplier credentials", async (t) => {
  let calls = 0, reads = 0;
  t.mock.method(globalThis, "fetch", () => { calls += 1; assert.fail("network forbidden"); });
  const original = process.env;
  process.env = new Proxy(original, { get(target, key) { if (key === "MATRIX_OASIS_R22_OPENAI_API_KEY") { reads += 1; throw new Error("forbidden"); } return Reflect.get(target, key); } });
  try { const f = await setup(t), tx = await create(f); await execute(tx); await tx.close(); assert.equal(calls, 0); assert.equal(reads, 0); }
  finally { process.env = original; }
});

for (const fault of ["transaction:created", "transaction-plan.json:staged", "transaction-plan.json:published"]) test(`pre-disclosure ${fault} recovers from original configuration without an id or request`, async (t) => {
  const f = await setup(t); let injected = false;
  await assert.rejects(create(f, { phase: async (phase) => { if (phase === fault) { injected = true; throw new Error("stop before disclosure"); } } }), /^Error: R22_/u);
  assert.equal(injected, true); assert.deepEqual(await savedBudget(f), f.budget);
  const recovered = await recoverR22ToolUsageDiagnosticTransaction(await currentConfig(f));
  assert.equal(recovered.terminal.state, "cancelled"); assert.equal(recovered.terminal.dispatchCount, 0);
  assert.equal(recovered.terminal.chargedMicrousd, 0); assert.equal(recovered.terminal.providerReplayRequests, 0);
  assert.deepEqual(await savedBudget(f), f.budget); await audit(f);
});

test("cancelled or closing transactions cannot accept a late approval or mutate their evidence", async (t) => {
  for (const status of ["cancelled", "closing"]) {
    const f = await setup(t), tx = await create(f), id = tx.disclosure.transactionSha256;
    if (status === "cancelled") await tx.cancel();
    const closing = status === "closing" ? tx.close() : null;
    const rejected = assert.rejects(tx.approve({ disclosureSha256: id }), /^Error: R22_/u);
    if (closing) await closing;
    await rejected;
    if (!closing) await tx.close();
    const records = await readdir(transactionRoot(f, id));
    assert.equal(records.includes("approval-record.json"), false); assert.deepEqual(await savedBudget(f), f.budget);
    const result = await recoverR22ToolUsageDiagnosticTransaction(await currentConfig(f), id);
    assert.equal(result.terminal.state, "cancelled"); await audit(f);
  }
});

test("transient writer cleanup failure allows close-only retry without reopening transaction operations", async (t) => {
  const f = await setup(t); let attempts = 0;
  const tx = await create(f, { rm: async (target, options) => {
    if (target.endsWith(".r22-writer-lock") && attempts++ === 0) throw Object.assign(new Error("PRIVATE_FILESYSTEM_BAIT"), { code: "EPERM" });
    return rm(target, options);
  } });
  const id = tx.disclosure.transactionSha256;
  await assert.rejects(tx.close(), /R22_STORE_WRITE_FAILED/u);
  await assert.rejects(tx.approve({ disclosureSha256: id }), /R22_DIAGNOSTIC_STORE_UNAVAILABLE/u);
  await assert.rejects(tx.cancel(), /R22_DIAGNOSTIC_STORE_UNAVAILABLE/u);
  const a = tx.close(), b = tx.close(); assert.equal(a, b); await a;
  assert.deepEqual(await tx.close(), { ok: true });
  assert.equal(attempts, 2); assert.deepEqual(await savedBudget(f), f.budget);
  const result = await recoverR22ToolUsageDiagnosticTransaction(await currentConfig(f), id);
  assert.equal(result.terminal.state, "cancelled"); assert.equal((await allText(f.config.output)).includes("PRIVATE_FILESYSTEM_BAIT"), false);
});

test("pre-disclosure recovery refuses a changed output, a damaged pending plan or a budget key", async (t) => {
  for (const attack of ["output", "bytes", "budget"]) {
    const f = await setup(t);
    await assert.rejects(create(f, { phase: async (phase) => { if (phase === "transaction-plan.json:staged") throw new Error("stop"); } }));
    const [hex] = await readdir(path.join(f.config.cognitionRunRoot, "diagnostics")), id = `sha256:${hex}`;
    const pending = path.join(transactionRoot(f, id), ".transaction-plan.json.pending");
    if (attack === "bytes") await writeFile(pending, "{");
    if (attack === "budget") {
      const budget = await savedBudget(f), plan = JSON.parse(await readFile(pending, "utf8"));
      budget.entries.push({ authoritySessionSha256: sha(canonical({ purpose: "matrix-oasis.r22-diagnostic-budget/1", transactionSha256: id })),
        callPlanSha256: plan.callPlanSha256, reservedMicrousd: 10000, chargedMicrousd: 0, state: "reserved" });
      budget.entries.sort((a, b) => `${a.authoritySessionSha256}:${a.callPlanSha256}`.localeCompare(`${b.authoritySessionSha256}:${b.callPlanSha256}`));
      await writeFile(path.join(f.config.cognitionRunRoot, "host-budget.json"), canonical(budget));
    }
    const config = await currentConfig(f); if (attack === "output") config.output = path.join(config.temporaryRoot, "other-output");
    const before = canonical(await savedBudget(f)), text = await readFile(pending, "utf8");
    await assert.rejects(recoverR22ToolUsageDiagnosticTransaction(config, attack === "output" ? undefined : id), /^Error: R22_/u);
    assert.equal(await readFile(pending, "utf8"), text); assert.equal(canonical(await savedBudget(f)), before);
  }
});

test("a process killed after the first synced plan is explicitly recoverable with zero fixture replay", { timeout: 20000 }, async (t) => {
  const f = await setup(t), moduleUrl = new URL("../scripts/lib/r22-diagnostic-transaction.mjs", import.meta.url).href;
  const script = `import { createR22ToolUsageDiagnosticTransaction } from ${JSON.stringify(moduleUrl)};
    await createR22ToolUsageDiagnosticTransaction(${JSON.stringify(f.config)}, { phase: async phase => {
      if (phase === "transaction-plan.json:staged") { process.stdout.write("PLAN_SYNCED\\n"); await new Promise(() => { setInterval(() => {}, 1000); }); }
    } });`;
  const env = {};
  for (const key of ["SystemRoot", "TEMP", "TMP"]) if (process.env[key]) env[key] = process.env[key];
  const child = spawn(process.execPath, ["--input-type=module", "--eval", script], { env, windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
  let ready = false;
  const closed = new Promise((resolve) => child.once("close", resolve));
  t.after(async () => { if (child.exitCode === null && child.signalCode === null) child.kill(); await closed; });
  let timer;
  try {
    await new Promise((resolve, reject) => {
      timer = setTimeout(() => reject(new Error("fixture child timeout")), 10000);
      child.once("error", reject); child.once("close", () => { if (!ready) reject(new Error("fixture child exited before plan")); });
      let output = "";
      child.stdout.on("data", (data) => { output += data.toString(); if (output.length > 128) reject(new Error("fixture child output limit"));
        if (output === "PLAN_SYNCED\n") { ready = true; resolve(); } });
      child.stderr.on("data", () => reject(new Error("fixture child unexpected stderr")));
    });
  } finally { clearTimeout(timer); }
  assert.equal(child.kill(), true); await closed;
  const recovered = await recoverR22ToolUsageDiagnosticTransaction(await currentConfig(f));
  assert.equal(recovered.terminal.state, "cancelled"); assert.equal(recovered.terminal.providerReplayRequests, 0);
  assert.equal(recovered.terminal.realRequestCount, 0); assert.deepEqual(await savedBudget(f), f.budget);
  assert.equal((await readdir(f.config.temporaryRoot)).includes(`.${path.basename(f.config.cognitionRunRoot)}.r22-writer-lock`), false); await audit(f);
});

test("changing the capture profile cannot reuse a disclosure or recover a different vocabulary", async (t) => {
  const f = await setup(t), otherProfile = captureProfile === "billing" ? "tool-usage" : "billing";
  const tx = await create(f), disclosure = tx.disclosure;
  await assert.rejects(tx.approve({ disclosureSha256: otherProfile === "billing"
    ? createNpcCognitionBillingDiagnosticPlan().diagnosticApprovalSha256 : legacyPlan().diagnosticApprovalSha256 }), /R22_APPROVAL_MISMATCH/u);
  await execute(tx); await tx.close();
  const before = canonical(await savedBudget(f));
  await assert.rejects(recoverR22ToolUsageDiagnosticTransaction({ ...await currentConfig(f), captureProfile: otherProfile }, disclosure.transactionSha256), /R22_DIAGNOSTIC_SOURCE_INVALID/u);
  assert.equal(canonical(await savedBudget(f)), before);
  const text = await allText(f.config.output);
  assert.doesNotMatch(text, /PRIVATE_BILLING_|998877/u);
  const record = JSON.parse(await readFile(path.join(f.config.output, "observation-record.json"), "utf8"));
  assert.equal(record.observation.profile, createNpcCognitionToolUsageDiagnosticPlan().diagnosticProfile);
});

if (captureProfile === "billing") {
test("billing records reject fully rehashed unknown values, category smuggling and contradictory shapes", async (t) => {
  const attacks = {
    value: (o) => { o.paths[1].value = 12345; },
    privateCategory: (o) => { o.paths[0].category = "PRIVATE_CATEGORY"; },
    wrongLiteralPath: (o) => { o.paths[2].category = "literal_developer"; },
    categoryType: (o) => { o.paths[1].category = "literal_usd"; },
    unknownPath: (o) => { o.paths[0].path = "private.payer"; },
    swappedPath: (o) => { [o.paths[0], o.paths[1]] = [o.paths[1], o.paths[0]]; },
    partialFailure: (o) => { o.status = "failed_limit"; },
    negativeCount: (o) => { o.summary.nodeCount = -1; },
    impossibleCount: (o) => { o.summary.typeCounts.string = 0; },
    impossibleAbsent: (o) => { o.rootType = "absent"; },
    impossibleDepth: (o) => { o.summary.maxDepth = 0; },
    shallowNestedPath: (o) => { o.summary.maxDepth = 1; },
    unreachableRootPath: (o) => { o.paths[0].jsonType = "not-captured"; o.paths[0].category = "not_captured"; },
    impossibleParent: (o) => { o.paths[4].jsonType = "absent"; o.paths[4].category = "absent"; },
    qualified: (o) => { o.qualificationEligible = true; },
    oldObservation: (o) => { o.profile = legacyPlan().diagnosticProfile; o.capturePolicySha256 = legacyPlan().capturePolicySha256; },
  };
  for (const [name, attack] of Object.entries(attacks)) await t.test(name, async (child) => {
    const f = await setup(child), tx = await create(f); const { transactionSha256: id } = await execute(tx); await tx.close();
    const root = transactionRoot(f, id), observationPath = path.join(root, "observation-record.json");
    const record = JSON.parse(await readFile(observationPath, "utf8")); attack(record.observation);
    const text = canonical(record), terminalPath = path.join(root, "terminal-record.json");
    const terminal = JSON.parse(await readFile(terminalPath, "utf8")); terminal.observationRecordSha256 = sha(text);
    await writeFile(observationPath, text); await writeFile(terminalPath, canonical(terminal));
    await writeFile(path.join(f.config.output, "observation-record.json"), text);
    await writeFile(path.join(f.config.output, "diagnostic-report.json"), canonical(terminal));
    const before = canonical(await savedBudget(f));
    await assert.rejects(audit(f), /^Error: R22_/u);
    await assert.rejects(recoverR22ToolUsageDiagnosticTransaction(await currentConfig(f), id), /^Error: R22_/u);
    assert.equal(canonical(await savedBudget(f)), before);
  });
});
}
});
