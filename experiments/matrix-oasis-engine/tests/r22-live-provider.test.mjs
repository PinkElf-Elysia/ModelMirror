import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import fsPromises, { link, mkdir, rename, rm, symlink, unlink, writeFile } from "node:fs/promises";
import { syncBuiltinESMExports } from "node:module";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { computeNpcCognitionApprovalHash, NPC_COGNITION_LIMITS, NPC_COGNITION_TRUSTED_INSTRUCTIONS } from "@matrix-oasis/npc-cognition-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { callPlan, sha } from "../packages/npc-cognition-contracts/tests/fixtures.mjs";
import { createR22OfficialOneShotOperations, prepareR22FileCredentialReader } from "../scripts/lib/r22-live-provider.mjs";
import { canonicalText, sha256 } from "../scripts/lib/r22-cli-core.mjs";
import { createNpcCognitionToolUsageDiagnosticPlan, evaluateNpcCognitionToolUsageDiagnosticFixture } from "@matrix-oasis/npc-cognition-provider-openai";
import { observeToolUsage } from "../packages/npc-cognition-provider-openai/src/tool-usage-observer.mjs";

test("diagnostic declaration forbids ordinary-input substitution and partial failed observations", () => {
  const moduleRoot = fileURLToPath(new URL("../", import.meta.url));
  const result = spawnSync(process.execPath, [path.join(moduleRoot, "node_modules/typescript/bin/tsc"),
    "--noEmit", "--strict", "--module", "NodeNext", "--target", "ES2022",
    "packages/npc-cognition-provider-openai/tests/tool-usage-diagnostic.types.ts",
  ], { cwd: moduleRoot, encoding: "utf8", windowsHide: true, shell: false });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
});

// Ambient network/credential tripwires belong in the existing host-side suite,
// not in the pure package's no-network source surface. No boundary exception.
test("diagnostic fixture and observer are inert under global network and credential tripwires", async (t) => {
  let externalFetches = 0, credentialReads = 0;
  t.mock.method(globalThis, "fetch", () => { externalFetches += 1; assert.fail("external fetch forbidden"); });
  const originalEnv = process.env;
  process.env = new Proxy(originalEnv, { get(target, key) {
    if (key === "MATRIX_OASIS_R22_OPENAI_API_KEY") { credentialReads += 1; throw new Error("credential read forbidden"); }
    return Reflect.get(target, key);
  } });
  try {
    const input = createNpcCognitionToolUsageDiagnosticPlan();
    const binding = { callPlanSha256: sha256(input.callPlanJson), diagnosticApprovalSha256: input.diagnosticApprovalSha256 };
    const bytes = new TextEncoder().encode(JSON.stringify({ id: "resp_local", object: "response", status: "completed",
      error: null, incomplete_details: null, model: "gpt-5.6-luna", service_tier: "default",
      output: [{ id: "msg_local", type: "message", status: "completed", role: "assistant", content: [{
        type: "output_text", text: JSON.stringify({ contextSha256: JSON.parse(input.callPlanJson).contextSha256,
          dialogueText: "Neutral acknowledgement.", actionChoiceId: null }), annotations: [],
      }] }], usage: { input_tokens: 20, input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 },
        output_tokens: 10, output_tokens_details: { reasoning_tokens: 0 }, total_tokens: 30 },
      tool_usage: { image_gen: { total_tokens: 0 } },
    }));
    const results = await Promise.all(Array.from({ length: 20 }, () => evaluateNpcCognitionToolUsageDiagnosticFixture(input, bytes)));
    assert.equal(new Set(results.map(canonicalText)).size, 1);
    assert.ok(results.every((result) => result.realRequestCount === 0 && result.fixtureOnly));
    const privateUrl = "https://private.invalid/path/token";
    const observation = observeToolUsage({ [privateUrl]: { url: privateUrl } }, binding);
    assert.equal(observation.status, "observed");
    assert.equal(observation.coverage, "redacted");
    assert.equal(canonicalText(observation).includes(privateUrl), false);
    assert.equal(canonicalText(observation).includes(sha256(privateUrl)), false);
    let traps = 0;
    const proxy = new Proxy({}, { get() { traps += 1; throw new Error("private-placeholder-error"); } });
    assert.equal(observeToolUsage(proxy, binding).status, "failed_internal");
    assert.equal(traps, 0);
    assert.equal(externalFetches, 0);
    assert.equal(credentialReads, 0);
  } finally { process.env = originalEnv; }
});

function fixture(label = "one") {
  const providerRequestJson = canonicalizeJsonValue({ input: label, model: "gpt-5.6-luna" });
  const plan = callPlan();
  plan.turnId = `turn-${label}`;
  plan.providerPayloadSha256 = sha256(providerRequestJson);
  plan.requestBytes = Buffer.byteLength(providerRequestJson, "utf8");
  plan.approval.hash = computeNpcCognitionApprovalHash(plan);
  const callPlanJson = canonicalizeJsonValue(plan);
  const callPlanSha256 = sha256(callPlanJson);
  const dispatchRecordJson = canonicalizeJsonValue({
    format: "matrix-oasis.r22-dispatch-record", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", callPlanSha256,
    turnSha256: plan.turnSha256, approvalTokenSha256: sha("e"),
    approvalContentSha256: plan.approval.hash, reservationMicrousd: 10000,
    providerRequestLimit: 1, providerRetryLimit: 0,
  });
  const checkpoint = { providerRequests: 0, active: { stage: "dispatching", callPlanSha256 } };
  return { providerRequestJson, callPlanJson, dispatchRecordJson, checkpoint, plan };
}

function executorInput(f, changes = {}) {
  return { apiKey: "dummy", callPlanJson: f.callPlanJson,
    providerRequestJson: f.providerRequestJson, approvalHash: f.plan.approval.hash, ...changes };
}

function typedFixture(label) {
  const f = fixture(label), context = canonicalText({ state: "ready" });
  f.plan.contextSha256 = sha256(context);
  const schema = { type: "object", additionalProperties: false,
    required: ["contextSha256", "dialogueText", "actionChoiceId"], properties: {
      contextSha256: { type: "string", const: f.plan.contextSha256 },
      dialogueText: { type: "string", minLength: 1, maxLength: NPC_COGNITION_LIMITS.dialogueBytes },
      actionChoiceId: { type: ["string", "null"], enum: [null, ...f.plan.candidateChoices.map((item) => item.choiceId)] },
    } };
  f.providerRequestJson = canonicalText({ background: false, input: context,
    instructions: NPC_COGNITION_TRUSTED_INSTRUCTIONS, max_output_tokens: NPC_COGNITION_LIMITS.maxOutputTokens,
    model: f.plan.model, reasoning: { effort: "none" }, service_tier: "default", store: false, stream: false, truncation: "disabled",
    text: { format: { type: "json_schema", name: "matrix_oasis_npc_dialogue_proposal", strict: true, schema } },
  });
  f.plan.providerPayloadSha256 = sha256(f.providerRequestJson);
  f.plan.responseSchemaSha256 = sha256(canonicalText(schema));
  f.plan.requestBytes = Buffer.byteLength(f.providerRequestJson);
  f.plan.approval.hash = computeNpcCognitionApprovalHash(f.plan);
  f.callPlanJson = canonicalText(f.plan);
  const callPlanSha256 = sha256(f.callPlanJson);
  f.dispatchRecordJson = canonicalText({ ...JSON.parse(f.dispatchRecordJson), callPlanSha256, approvalContentSha256: f.plan.approval.hash });
  f.checkpoint.active.callPlanSha256 = callPlanSha256;
  return f;
}

const TEMP_ROOT = path.join(path.parse(fileURLToPath(import.meta.url)).root, "tmp");
const officialPrefix = () => String.fromCharCode(115, 107, 45);
const fakeOfficialCredential = () => `${officialPrefix()}${"x".repeat(20)}`;
function credentialPath(label) { return path.join(TEMP_ROOT, `r22-credential-${label}-${randomUUID()}.txt`); }
function staticCredentialError(error) {
  assert.equal(error?.message, "R22_OFFICIAL_CREDENTIAL_FILE_UNAVAILABLE");
  assert.equal(String(error).includes("r22-credential-"), false);
  assert.equal(String(error).includes("fixture"), false);
  return true;
}

test("one-shot operations remain inert until durable dispatch and consume exactly one credential and execution claim", async () => {
  const f = fixture();
  let dispatchReads = 0, credentialReads = 0, executions = 0;
  const operations = createR22OfficialOneShotOperations({
    async readDispatch() { dispatchReads += 1; return f; },
    async readCredential() { credentialReads += 1; return "dummy"; },
    async execute(input) { executions += 1; return { ok: true, input }; },
  });
  assert.deepEqual(operations.inspect(), { credentialClaimed: false, executionClaimed: false,
    sourceCredentialReads: 0, providerRequests: 0, requestLimit: 1, retryLimit: 0 });
  assert.deepEqual([dispatchReads, credentialReads, executions], [0, 0, 0]);
  assert.equal(await operations.keyReader(), "dummy");
  assert.equal((await operations.providerExecutor(executorInput(f))).ok, true);
  assert.deepEqual([dispatchReads, credentialReads, executions], [2, 1, 1]);
  assert.deepEqual(operations.inspect(), { credentialClaimed: true, executionClaimed: true,
    sourceCredentialReads: 1, providerRequests: 1, requestLimit: 1, retryLimit: 0 });
});

test("twenty concurrent callers cannot duplicate credential reads or provider execution", async () => {
  const f = fixture("concurrent");
  let credentialReads = 0, executions = 0;
  const operations = createR22OfficialOneShotOperations({ async readDispatch() { return f; },
    async readCredential() { credentialReads += 1; await new Promise((resolve) => setImmediate(resolve)); return "dummy"; },
    async execute() { executions += 1; await new Promise((resolve) => setImmediate(resolve)); return { ok: true }; } });
  const keys = await Promise.allSettled(Array.from({ length: 20 }, () => operations.keyReader()));
  assert.equal(keys.filter((item) => item.status === "fulfilled").length, 1);
  assert.equal(credentialReads, 1);
  const providers = await Promise.allSettled(Array.from({ length: 20 }, () => operations.providerExecutor(executorInput(f))));
  assert.equal(providers.filter((item) => item.status === "fulfilled").length, 1);
  assert.equal(executions, 1);
  assert.equal(operations.inspect().providerRequests, 1);
});

test("the one-shot claim survives replacement dispatch checkpoints and rejects every identity substitution", async (t) => {
  const original = fixture("original");
  const reset = fixture("reset");
  const operations = createR22OfficialOneShotOperations({ async readDispatch() { return original; },
    async readCredential() { return "dummy"; }, async execute() { return { ok: true }; } });
  await operations.keyReader();
  await operations.providerExecutor(executorInput(original));
  await assert.rejects(operations.keyReader(), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
  await assert.rejects(operations.providerExecutor(executorInput(reset)), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);

  for (const [name, mutate] of [
    ["approval", (input) => ({ ...input, approvalHash: sha("f") })],
    ["payload", (input) => ({ ...input, providerRequestJson: "{}" })],
    ["plan", (input) => ({ ...input, callPlanJson: reset.callPlanJson })],
  ]) {
    await t.test(name, async () => {
      const isolated = createR22OfficialOneShotOperations({ async readDispatch() { return original; },
        async readCredential() { return "dummy"; }, async execute() { assert.fail("invalid identity reached execute"); } });
      await isolated.keyReader();
      await assert.rejects(isolated.providerExecutor(mutate(executorInput(original))), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
      assert.equal(isolated.inspect().providerRequests, 0);
    });
  }
});

test("used checkpoints and dispatch read failures fail closed before credential access", async (t) => {
  const f = fixture("blocked");
  for (const [name, readDispatch] of [
    ["dispatch-missing", async () => null],
    ["not-dispatching", async () => ({ ...f, checkpoint: { ...f.checkpoint, active: { ...f.checkpoint.active, stage: "planned" } } })],
    ["already-used", async () => ({ ...f, checkpoint: { ...f.checkpoint, providerRequests: 1 } })],
    ["read-failure", async () => { throw new Error("source unavailable"); }],
  ]) {
    await t.test(name, async () => {
      let credentialReads = 0;
      const operations = createR22OfficialOneShotOperations({ readDispatch,
        async readCredential() { credentialReads += 1; return "dummy"; }, async execute() { assert.fail(); } });
      await assert.rejects(operations.keyReader());
      assert.equal(credentialReads, 0);
      assert.equal(operations.inspect().sourceCredentialReads, 0);
    });
  }
});

test("missing credentials and ambiguous execution are terminal and inspect never exposes raw values", async () => {
  const missing = fixture("missing");
  const noKey = createR22OfficialOneShotOperations({ async readDispatch() { return missing; },
    async readCredential() { return undefined; }, async execute() { assert.fail(); } });
  assert.equal(await noKey.keyReader(), undefined);
  await assert.rejects(noKey.keyReader(), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
  assert.equal(JSON.stringify(noKey.inspect()).includes("dummy"), false);

  const ambiguousFixture = fixture("ambiguous");
  let executions = 0;
  const ambiguous = createR22OfficialOneShotOperations({ async readDispatch() { return ambiguousFixture; },
    async readCredential() { return "dummy"; },
    async execute() { executions += 1; throw new Error("RAW_PROVIDER_FAILURE_MUST_NOT_LEAK"); } });
  await ambiguous.keyReader();
  await assert.rejects(ambiguous.providerExecutor(executorInput(ambiguousFixture)), /RAW_PROVIDER_FAILURE_MUST_NOT_LEAK/u);
  await assert.rejects(ambiguous.providerExecutor(executorInput(ambiguousFixture)), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
  assert.equal(executions, 1);
  assert.deepEqual(ambiguous.inspect(), { credentialClaimed: true, executionClaimed: true,
    sourceCredentialReads: 1, providerRequests: 1, requestLimit: 1, retryLimit: 0 });
  assert.equal(JSON.stringify(ambiguous.inspect()).includes("dummy"), false);
});

test("default official executor emits only a Call-Plan-bound HTTP diagnostic after dispatch", async (t) => {
  const f = typedFixture("http-diagnostic"), context = JSON.parse(f.providerRequestJson).input;
  const callPlanSha256 = sha256(f.callPlanJson);
  let requests = 0, keyReads = 0;
  const logs = [];
  t.mock.method(globalThis, "fetch", async () => {
    requests += 1;
    return new Response(JSON.stringify({ error: { type: "invalid_request_error", code: "invalid_json_schema",
      param: "text.format.schema", message: "PRIVATE_RAW_ERROR_AND_KEY" } }), { status: 400 });
  });
  const originalWrite = process.stdout.write.bind(process.stdout);
  t.mock.method(process.stdout, "write", (text, ...args) => {
    if (typeof text !== "string" || !text.startsWith("R22_PROVIDER_HTTP_DIAGNOSTIC_JSON:")) return originalWrite(text, ...args);
    logs.push(text);
    args.find((value) => typeof value === "function")?.();
    return true;
  });
  const operations = createR22OfficialOneShotOperations({ readDispatch: async () => f,
    readCredential: async () => { keyReads += 1; return "dummy"; } });
  assert.deepEqual([requests, keyReads, logs.length], [0, 0, 0]);
  await operations.keyReader();
  assert.deepEqual([requests, keyReads, logs.length], [0, 1, 0]);
  const result = await operations.providerExecutor(executorInput(f));
  await new Promise(setImmediate);
  t.mock.restoreAll();
  assert.equal(result.diagnosticCode, "R22_PROVIDER_REFUSED");
  assert.deepEqual([requests, keyReads, logs.length], [1, 1, 1]);
  assert.equal(logs[0], `R22_PROVIDER_HTTP_DIAGNOSTIC_JSON:${canonicalText({ callPlanSha256,
    status: 400, bodyStatus: "parsed", errorType: "invalid_request_error", errorCode: "invalid_json_schema",
    parameter: "text.format.schema" })}\n`);
  assert.equal(logs[0].includes("PRIVATE"), false);
  assert.equal(logs[0].includes("dummy"), false);
  assert.equal(logs[0].includes(context), false);
  await assert.rejects(operations.providerExecutor(executorInput(f)), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
  assert.equal(requests, 1);
  for (const mode of ["throw", "asynchronous-error", "backpressure"]) {
    await t.test(`diagnostic sink ${mode} cannot alter the provider result`, () => {
      // Use a credential-free bounded child: injecting errors into node:test's
      // own stdout would test its IPC channel, not the live adapter's sink.
      const script = `
        import assert from "node:assert/strict";
        import { createR22OfficialOneShotOperations } from ${JSON.stringify(new URL("../scripts/lib/r22-live-provider.mjs", import.meta.url).href)};
        const [f, mode, expected] = JSON.parse(process.argv[1]);
        let requests = 0;
        globalThis.fetch = async () => {
          requests += 1;
          return new Response(JSON.stringify({ error: { type: "invalid_request_error", code: "invalid_json_schema", param: "text.format.schema" } }), { status: 400 });
        };
        const stream = process.stdout, originalWrite = stream.write, listenerCount = stream.listenerCount("error");
        stream.write = (_text, callback) => {
          if (mode === "throw") throw new Error("PRIVATE_STDOUT_FAILURE");
          callback();
          if (mode === "asynchronous-error") process.nextTick(() => stream.emit("error", new Error("PRIVATE_STDOUT_FAILURE")));
          return false;
        };
        const fault = createR22OfficialOneShotOperations({ readDispatch: async () => f, readCredential: async () => "dummy" });
        const input = { apiKey: await fault.keyReader(), callPlanJson: f.callPlanJson, providerRequestJson: f.providerRequestJson, approvalHash: f.plan.approval.hash };
        const result = await fault.providerExecutor(input);
        await new Promise(setImmediate);
        stream.write = originalWrite;
        assert.equal(result.diagnosticCode, "R22_PROVIDER_REFUSED");
        assert.deepEqual(result.httpDiagnostic, expected);
        assert.equal(requests, 1);
        assert.equal(stream.listenerCount("error"), listenerCount);
        await assert.rejects(fault.providerExecutor(input), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
        assert.equal(requests, 1);
        process.stdout.write("R22_HTTP_DIAGNOSTIC_SINK_OK");
      `;
      const child = spawnSync(process.execPath, ["--input-type=module", "--eval", script,
        JSON.stringify([f, mode, result.httpDiagnostic])], {
        env: process.env.SystemRoot ? { SystemRoot: process.env.SystemRoot } : {},
        timeout: 10000, maxBuffer: 16384, encoding: "utf8", windowsHide: true,
      });
      assert.equal(child.error, undefined);
      assert.equal(child.status, 0, child.stderr);
      assert.equal(child.stdout, "R22_HTTP_DIAGNOSTIC_SINK_OK");
      assert.equal(child.stderr, "");
    });
  }
});

test("default executor binds a closed response-stage diagnostic to the single consumed Call Plan", async (t) => {
  const f = typedFixture("response-diagnostic"), logs = [];
  let requests = 0, credentialReads = 0;
  t.mock.method(globalThis, "fetch", async () => {
    requests += 1;
    return new Response('{"PRIVATE_RAW_RESPONSE":', { headers: { "content-type": "application/json" } });
  });
  const originalWrite = process.stdout.write.bind(process.stdout);
  t.mock.method(process.stdout, "write", (text, ...args) => {
    if (typeof text !== "string" || !text.startsWith("R22_PROVIDER_RESPONSE_DIAGNOSTIC_JSON:")) return originalWrite(text, ...args);
    logs.push(text);
    args.find((value) => typeof value === "function")?.();
    return true;
  });
  const operations = createR22OfficialOneShotOperations({ readDispatch: async () => f,
    readCredential: async () => { credentialReads += 1; return "dummy"; } });
  assert.deepEqual([requests, credentialReads, logs.length], [0, 0, 0]);
  await operations.keyReader();
  const result = await operations.providerExecutor(executorInput(f));
  await new Promise(setImmediate);
  t.mock.restoreAll();
  assert.equal(result.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
  assert.deepEqual(result.responseDiagnostic, { status: 200, stage: "json" });
  assert.deepEqual([requests, credentialReads, logs.length], [1, 1, 1]);
  assert.equal(logs[0], `R22_PROVIDER_RESPONSE_DIAGNOSTIC_JSON:${canonicalText({
    callPlanSha256: sha256(f.callPlanJson), status: 200, stage: "json" })}\n`);
  assert.equal(logs[0].includes("PRIVATE"), false);
  assert.equal(logs[0].includes("dummy"), false);
  assert.equal(result.httpDiagnostic, undefined);
  await assert.rejects(operations.providerExecutor(executorInput(f)), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
  assert.equal(requests, 1);
});

test("root structure diagnostics reach the live marker without retaining unsafe names omitted counts or values", async (t) => {
  const expected = { status: 200, stage: "root_fields", rootFields: {
    rootKind: "record", missingRequiredMask: 192, unknownKeysPresent: true,
    unknownFields: [], unknownFieldsOmitted: true,
  } };
  const markers = [];
  const f = typedFixture("root-structure-diagnostic");
  for (const count of [1, 32]) {
    let requests = 0, credentialReads = 0;
    const logs = [];
    const body = { id: "PRIVATE_RESPONSE_ID", object: "response", status: "completed",
      error: null, incomplete_details: null, model: "gpt-5.6-luna" };
    for (let index = 0; index < count; index += 1) body[`PRIVATE_FIELD_${index}`] = "PRIVATE_VALUE";
    t.mock.method(globalThis, "fetch", async () => {
      requests += 1;
      return new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
    });
    const originalWrite = process.stdout.write.bind(process.stdout);
    t.mock.method(process.stdout, "write", (text, ...args) => {
      if (typeof text !== "string" || !text.startsWith("R22_PROVIDER_RESPONSE_DIAGNOSTIC_JSON:")) return originalWrite(text, ...args);
      logs.push(text); args.find((value) => typeof value === "function")?.(); return true;
    });
    const operations = createR22OfficialOneShotOperations({ readDispatch: async () => f,
      readCredential: async () => { credentialReads += 1; return "dummy"; } });
    assert.deepEqual([requests, credentialReads, logs.length], [0, 0, 0]);
    await operations.keyReader();
    const result = await operations.providerExecutor(executorInput(f));
    await new Promise(setImmediate);
    t.mock.restoreAll();
    assert.equal(result.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
    assert.equal(result.costUncertain, true);
    assert.deepEqual(result.responseDiagnostic, expected);
    assert.deepEqual([requests, credentialReads, logs.length], [1, 1, 1]);
    assert.equal(logs[0], `R22_PROVIDER_RESPONSE_DIAGNOSTIC_JSON:${canonicalText({
      callPlanSha256: sha256(f.callPlanJson), ...expected })}\n`);
    assert.equal(logs[0].includes("PRIVATE"), false);
    assert.equal(logs[0].includes("dummy"), false);
    await assert.rejects(operations.providerExecutor(executorInput(f)), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
    assert.equal(requests, 1);
    markers.push(logs[0]);
  }
  assert.equal(markers[0], markers[1]);
  for (const mode of ["throw", "asynchronous-error", "backpressure"]) {
    await t.test(`root diagnostic sink ${mode} preserves fail-closed outcome`, () => {
      const script = `
        import assert from "node:assert/strict";
        import { createR22OfficialOneShotOperations } from ${JSON.stringify(new URL("../scripts/lib/r22-live-provider.mjs", import.meta.url).href)};
        const [f, mode, expected] = JSON.parse(process.argv[1]);
        let requests = 0;
        globalThis.fetch = async () => {
          requests += 1;
          return new Response(JSON.stringify({ id: "PRIVATE_ID", object: "response", status: "completed",
            error: null, incomplete_details: null, model: "gpt-5.6-luna", PRIVATE_KEY: "PRIVATE_VALUE" }),
            { headers: { "content-type": "application/json" } });
        };
        const stream = process.stdout, originalWrite = stream.write, listeners = stream.listenerCount("error");
        stream.write = (_text, callback) => {
          if (mode === "throw") throw new Error("PRIVATE_STDOUT_FAILURE");
          callback();
          if (mode === "asynchronous-error") process.nextTick(() => stream.emit("error", new Error("PRIVATE_STDOUT_FAILURE")));
          return false;
        };
        const operations = createR22OfficialOneShotOperations({ readDispatch: async () => f, readCredential: async () => "dummy" });
        await operations.keyReader();
        const input = { apiKey: "dummy", callPlanJson: f.callPlanJson, providerRequestJson: f.providerRequestJson, approvalHash: f.plan.approval.hash };
        const result = await operations.providerExecutor(input);
        await new Promise(setImmediate);
        stream.write = originalWrite;
        assert.equal(result.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
        assert.deepEqual(result.responseDiagnostic, expected);
        assert.equal(requests, 1);
        assert.equal(stream.listenerCount("error"), listeners);
        await assert.rejects(operations.providerExecutor(input), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
        assert.equal(requests, 1);
        process.stdout.write("R22_ROOT_DIAGNOSTIC_SINK_OK");
      `;
      const child = spawnSync(process.execPath, ["--input-type=module", "--eval", script, JSON.stringify([f, mode, expected])], {
        env: process.env.SystemRoot ? { SystemRoot: process.env.SystemRoot } : {},
        timeout: 10000, maxBuffer: 16384, encoding: "utf8", windowsHide: true,
      });
      assert.equal(child.error, undefined); assert.equal(child.status, 0, child.stderr);
      assert.equal(child.stdout, "R22_ROOT_DIAGNOSTIC_SINK_OK"); assert.equal(child.stderr, "");
    });
  }
});

test("live root diagnostic exposes only the bounded approved name and type metadata", async (t) => {
  const f = typedFixture("root-name-type-diagnostic"), markers = [];
  const fields = {
    future_string: "PRIVATE_DIALOGUE", future_object: { private_nested: "PRIVATE_SECRET" },
    future_number: 1024, future_null: null, future_boolean: false,
    future_array: ["PRIVATE_RESPONSE_ID"],
    ...Object.fromEntries(Array.from({ length: 11 }, (_, index) => [`z_field_${String(index).padStart(2, "0")}`, "PRIVATE_VALUE"])),
    "private@example.test": "PRIVATE_VALUE",
  };
  const expected = { status: 200, stage: "root_fields", rootFields: {
    rootKind: "record", missingRequiredMask: 192, unknownKeysPresent: true,
    unknownFields: [
      { name: "future_array", jsonType: "array" }, { name: "future_boolean", jsonType: "boolean" },
      { name: "future_null", jsonType: "null" }, { name: "future_number", jsonType: "number" },
      { name: "future_object", jsonType: "object" }, { name: "future_string", jsonType: "string" },
      ...Array.from({ length: 10 }, (_, index) => ({ name: `z_field_${String(index).padStart(2, "0")}`, jsonType: "string" })),
    ], unknownFieldsOmitted: true,
  } };
  for (const entries of [Object.entries(fields), Object.entries(fields).reverse()]) {
    let requests = 0, credentialReads = 0;
    const logs = [];
    t.mock.method(globalThis, "fetch", async () => {
      requests += 1;
      return new Response(JSON.stringify({ id: "PRIVATE_RESPONSE_ID", object: "response", status: "completed",
        error: null, incomplete_details: null, model: "gpt-5.6-luna", ...Object.fromEntries(entries) }),
      { headers: { "content-type": "application/json" } });
    });
    const originalWrite = process.stdout.write.bind(process.stdout);
    t.mock.method(process.stdout, "write", (text, ...args) => {
      if (typeof text !== "string" || !text.startsWith("R22_PROVIDER_RESPONSE_DIAGNOSTIC_JSON:")) return originalWrite(text, ...args);
      logs.push(text); args.find((value) => typeof value === "function")?.(); return true;
    });
    const operations = createR22OfficialOneShotOperations({ readDispatch: async () => f,
      readCredential: async () => { credentialReads += 1; return "dummy"; } });
    assert.deepEqual([requests, credentialReads, logs.length], [0, 0, 0]);
    await operations.keyReader();
    const result = await operations.providerExecutor(executorInput(f));
    await new Promise(setImmediate);
    t.mock.restoreAll();
    assert.equal(result.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
    assert.equal(result.costUncertain, true); assert.equal(result.usage, null);
    assert.deepEqual(result.responseDiagnostic, expected);
    assert.deepEqual([requests, credentialReads, logs.length], [1, 1, 1]);
    assert.equal(logs[0], `R22_PROVIDER_RESPONSE_DIAGNOSTIC_JSON:${canonicalText({
      callPlanSha256: sha256(f.callPlanJson), ...expected })}\n`);
    for (const forbidden of ["PRIVATE", "private_nested", "private@example.test", "dummy", "z_field_10"]) {
      assert.equal(logs[0].includes(forbidden), false);
    }
    assert.ok(Buffer.byteLength(logs[0]) < 4096);
    await assert.rejects(operations.providerExecutor(executorInput(f)), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
    assert.equal(requests, 1); markers.push(logs[0]);
  }
  assert.equal(markers[0], markers[1]);
});

test("live executor accepts only neutral root extensions without changing approval or leaking rejected values", async (t) => {
  const f = typedFixture("neutral-root-extensions");
  const proposal = { contextSha256: f.plan.contextSha256, dialogueText: "PRIVATE_DIALOGUE",
    actionChoiceId: f.plan.candidateChoices[0].choiceId };
  const base = { id: "PRIVATE_RESPONSE_ID", object: "response", status: "completed", error: null,
    incomplete_details: null, model: f.plan.model, service_tier: "default",
    output: [{ id: "msg_discarded", type: "message", status: "completed", role: "assistant",
      content: [{ type: "output_text", text: JSON.stringify(proposal), annotations: [] }] }],
    usage: { input_tokens: 200, input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 },
      output_tokens: 50, output_tokens_details: { reasoning_tokens: 0 }, total_tokens: 250 },
    frequency_penalty: 0, presence_penalty: 0, tool_usage: {},
  };
  for (const [overrides, accepted] of [
    [{}, true], [{ frequency_penalty: "PRIVATE_VALUE" }, false],
    [{ presence_penalty: null }, false], [{ tool_usage: { PRIVATE_NESTED: "PRIVATE_VALUE" } }, false],
    [{ tool_usage: { calls: 0 } }, false],
  ]) {
    let requests = 0, credentialReads = 0;
    const logs = [], originalWrite = process.stdout.write.bind(process.stdout);
    t.mock.method(globalThis, "fetch", async (_, options) => {
      requests += 1; assert.equal(options.body, f.providerRequestJson);
      return new Response(JSON.stringify({ ...base, ...overrides }), { headers: { "content-type": "application/json" } });
    });
    t.mock.method(process.stdout, "write", (text, ...args) => {
      if (typeof text !== "string" || !text.startsWith("R22_PROVIDER_RESPONSE_DIAGNOSTIC_JSON:")) return originalWrite(text, ...args);
      logs.push(text); args.find((value) => typeof value === "function")?.(); return true;
    });
    const operations = createR22OfficialOneShotOperations({ readDispatch: async () => f,
      readCredential: async () => { credentialReads += 1; return "dummy"; } });
    assert.deepEqual([requests, credentialReads, logs.length], [0, 0, 0]);
    await operations.keyReader();
    const result = await operations.providerExecutor(executorInput(f));
    await new Promise(setImmediate);
    t.mock.restoreAll();
    assert.equal(result.ok, accepted);
    assert.deepEqual([requests, credentialReads], [1, 1]);
    if (accepted) {
      assert.deepEqual(result.proposal, proposal);
      assert.equal(result.actualCostMicrousd, 100);
      assert.equal(result.usage.totalTokens, 250);
      assert.equal(result.responseDiagnostic, undefined);
      assert.deepEqual(logs, []);
    } else {
      assert.equal(result.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
      assert.equal(result.proposal, undefined);
      assert.equal(result.responseDiagnostic.status, 200);
      assert.equal(result.responseDiagnostic.stage, "root_echo");
      assert.deepEqual(result.responseDiagnostic.checks.filter((item) => item.status === "failed"),
        [{ rule: `echo_${Object.keys(overrides)[0]}`, status: "failed" }]);
      assert.equal(JSON.stringify(result).includes("PRIVATE"), false);
      assert.deepEqual(logs, [`R22_PROVIDER_RESPONSE_DIAGNOSTIC_JSON:${canonicalText({
        callPlanSha256: sha256(f.callPlanJson), ...result.responseDiagnostic })}\n`]);
    }
    await assert.rejects(operations.keyReader(), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
    await assert.rejects(operations.providerExecutor(executorInput(f)), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
    assert.equal(requests, 1);
  }
});

test("live echo details preserve both failures and never publish the unknown nested names or description", async (t) => {
  const f = typedFixture("echo-subrule-diagnostic"), logs = [];
  const body = { id: "PRIVATE_RESPONSE_ID", object: "response", status: "completed", error: null,
    incomplete_details: null, model: f.plan.model, service_tier: "default",
    tool_usage: { PRIVATE_UNKNOWN_KEY: "PRIVATE_VALUE" },
    text: { format: { ...JSON.parse(f.providerRequestJson).text.format, description: "PRIVATE_DESCRIPTION\u0000" } },
    output: [{ id: "PRIVATE_MESSAGE_ID", type: "message", status: "completed", role: "assistant",
      content: [{ type: "output_text", text: JSON.stringify({ contextSha256: f.plan.contextSha256,
        dialogueText: "PRIVATE_DIALOGUE", actionChoiceId: null }), annotations: [] }] }],
    usage: { input_tokens: 20, input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 },
      output_tokens: 10, output_tokens_details: { reasoning_tokens: 0 }, total_tokens: 30 },
  };
  let requests = 0, credentialReads = 0;
  t.mock.method(globalThis, "fetch", async (_, options) => {
    requests += 1;
    assert.equal(options.body, f.providerRequestJson);
    assert.equal(JSON.parse(options.body).service_tier, "default");
    return new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
  });
  const originalWrite = process.stdout.write.bind(process.stdout);
  t.mock.method(process.stdout, "write", (text, ...args) => {
    if (typeof text !== "string" || !text.startsWith("R22_PROVIDER_RESPONSE_DIAGNOSTIC_JSON:")) return originalWrite(text, ...args);
    logs.push(text); args.find((value) => typeof value === "function")?.(); return true;
  });
  const operations = createR22OfficialOneShotOperations({ readDispatch: async () => f,
    readCredential: async () => { credentialReads += 1; return "dummy"; } });
  assert.deepEqual([requests, credentialReads, logs.length], [0, 0, 0]);
  await operations.keyReader();
  const result = await operations.providerExecutor(executorInput(f));
  await new Promise(setImmediate);
  t.mock.restoreAll();
  assert.equal(result.ok, false);
  assert.equal(result.diagnosticCode, "R22_PROVIDER_RESPONSE_INVALID");
  assert.equal(result.responseDiagnostic.stage, "root_echo");
  assert.equal(result.costUncertain, true);
  assert.equal(result.usage, null);
  assert.equal(result.actualCostMicrousd, null);
  assert.equal(result.proposal, undefined);
  assert.deepEqual(result.responseDiagnostic.checks.filter((item) => item.status === "failed"), [
    { rule: "echo_tool_usage", status: "failed" }, { rule: "echo_text_format", status: "failed" },
  ]);
  assert.deepEqual(result.responseDiagnostic.echoDetails, {
    toolUsage: { jsonType: "object", objectRecord: "passed", emptyRecord: "failed" },
    textFormat: {
      textJsonType: "object", formatJsonType: "object", descriptionJsonType: "string",
      textRecord: "passed", textRequiredKeys: "passed", textAllowedKeys: "passed",
      formatRecord: "passed", formatRequiredKeys: "passed", formatAllowedKeys: "passed",
      typeJsonSchema: "passed", nameExact: "passed", strictTrue: "passed",
      descriptionString: "passed", descriptionSafeText: "failed", descriptionUtf8Limit: "passed",
    },
  });
  assert.deepEqual([requests, credentialReads, logs.length], [1, 1, 1]);
  assert.equal(logs[0], `R22_PROVIDER_RESPONSE_DIAGNOSTIC_JSON:${canonicalText({
    callPlanSha256: sha256(f.callPlanJson), ...result.responseDiagnostic })}\n`);
  for (const forbidden of ["PRIVATE", "dummy", f.plan.contextSha256, f.providerRequestJson]) {
    assert.equal(logs[0].includes(forbidden), false);
  }
  assert.ok(Buffer.byteLength(logs[0]) < 4096);
  await assert.rejects(operations.keyReader(), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
  await assert.rejects(operations.providerExecutor(executorInput(f)), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
  assert.equal(requests, 1);
});

test("response profile normalization and rejection propagate through the live one-shot adapter without redispatch", async (t) => {
  for (const [name, mutate, expectedField] of [
    ["normalized description", () => {}, null],
    ["unexplained tool usage", (body) => { body.tool_usage = { PRIVATE_NESTED: 0 }; }, "echo_tool_usage"],
    ["unrequested capability", (body) => { body.agent = { PRIVATE_NESTED: "PRIVATE_VALUE" }; }, "agent"],
  ]) {
    await t.test(name, async (t) => {
      const f = typedFixture("response-profile"), logs = [];
      const body = { id: "PRIVATE_RESPONSE_ID", object: "response", status: "completed", error: null,
        incomplete_details: null, model: f.plan.model, service_tier: "default",
        text: { format: { ...JSON.parse(f.providerRequestJson).text.format, description: null } },
        output: [{ id: "PRIVATE_MESSAGE_ID", type: "message", status: "completed", role: "assistant",
          content: [{ type: "output_text", text: JSON.stringify({ contextSha256: f.plan.contextSha256,
            dialogueText: "Transient dialogue.", actionChoiceId: null }), annotations: [] }] }],
        usage: { input_tokens: 20, input_tokens_details: { cached_tokens: 0, cache_write_tokens: 0 },
          output_tokens: 10, output_tokens_details: { reasoning_tokens: 0 }, total_tokens: 30 },
      };
      mutate(body);
      let requests = 0, credentialReads = 0;
      t.mock.method(globalThis, "fetch", async (_, options) => {
        requests += 1;
        assert.equal(options.body, f.providerRequestJson);
        return new Response(JSON.stringify(body), { headers: { "content-type": "application/json" } });
      });
      const originalWrite = process.stdout.write.bind(process.stdout);
      t.mock.method(process.stdout, "write", (text, ...args) => {
        if (typeof text !== "string" || !text.startsWith("R22_PROVIDER_RESPONSE_DIAGNOSTIC_JSON:")) return originalWrite(text, ...args);
        logs.push(text); args.find((value) => typeof value === "function")?.(); return true;
      });
      const operations = createR22OfficialOneShotOperations({ readDispatch: async () => f,
        readCredential: async () => { credentialReads += 1; return "dummy"; } });
      assert.deepEqual([requests, credentialReads], [0, 0]);
      await operations.keyReader();
      const result = await operations.providerExecutor(executorInput(f));
      await new Promise(setImmediate);
      assert.equal(result.ok, expectedField === null);
      assert.deepEqual([requests, credentialReads, logs.length], [1, 1, expectedField === null ? 0 : 1]);
      if (expectedField !== null) {
        assert.equal(result.proposal, undefined);
        assert.equal(result.costUncertain, true);
        if (expectedField === "agent") {
          assert.deepEqual(result.responseDiagnostic.fieldPolicyFailures, ["agent"]);
          assert.equal(result.responseDiagnostic.envelopeProfile, "matrix-oasis.responses-envelope/1");
        } else {
          assert.deepEqual(result.responseDiagnostic.checks.filter(({ status }) => status === "failed"),
            [{ rule: "echo_tool_usage", status: "failed" }]);
        }
        assert.equal(logs[0], `R22_PROVIDER_RESPONSE_DIAGNOSTIC_JSON:${canonicalText({
          callPlanSha256: sha256(f.callPlanJson), ...result.responseDiagnostic })}\n`);
        for (const forbidden of ["PRIVATE", "dummy", "Transient dialogue.", f.plan.contextSha256, f.providerRequestJson]) {
          assert.equal(logs[0].includes(forbidden), false);
        }
      }
      await assert.rejects(operations.keyReader(), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
      await assert.rejects(operations.providerExecutor(executorInput(f)), /R22_OFFICIAL_ONE_SHOT_UNAVAILABLE/u);
      assert.equal(requests, 1);
    });
  }
});

test("file credential preparation reads no content and a dispatch-time reader accepts one official key line", async (t) => {
  const file = credentialPath("valid"), value = fakeOfficialCredential();
  await writeFile(file, Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from(`${value}\r\n`)]), { flag: "wx" });
  t.after(() => unlink(file).catch(() => {}));
  const readCredential = await prepareR22FileCredentialReader({ credentialFile: file, temporaryRoot: TEMP_ROOT });
  assert.equal(typeof readCredential, "function");
  assert.equal(Object.isFrozen(readCredential), true);
  assert.equal(Object.isFrozen(readCredential.sourceBinding), true);
  assert.equal(readCredential.sourceBinding.kind, "pinned-file");
  assert.match(readCredential.sourceBinding.identitySha256, /^sha256:[0-9a-f]{64}$/u);
  assert.equal(JSON.stringify(readCredential.sourceBinding).includes(file), false);
  assert.equal(JSON.stringify(readCredential.sourceBinding).includes(value), false);
  const repeated = await prepareR22FileCredentialReader({ credentialFile: file, temporaryRoot: TEMP_ROOT });
  assert.deepEqual(repeated.sourceBinding, readCredential.sourceBinding);
  assert.equal(await readCredential(), value);

  const invalid = credentialPath("deferred-invalid");
  await writeFile(invalid, "not-an-official-key\n", { flag: "wx" });
  t.after(() => unlink(invalid).catch(() => {}));
  const deferred = await prepareR22FileCredentialReader({ credentialFile: invalid, temporaryRoot: TEMP_ROOT });
  await assert.rejects(deferred(), staticCredentialError);
});

test("file credential preparation performs no open or read before the returned reader is called", async (t) => {
  const file = credentialPath("observable-deferred"), value = fakeOfficialCredential();
  await writeFile(file, value, { flag: "wx" });
  t.after(() => unlink(file).catch(() => {}));
  const originalOpen = fsPromises.open;
  let opens = 0, reads = 0;
  t.mock.method(fsPromises, "open", async (...args) => {
    opens += 1;
    const handle = await originalOpen(...args);
    const originalRead = handle.read.bind(handle);
    handle.read = async (...readArgs) => { reads += 1; return originalRead(...readArgs); };
    return handle;
  });
  syncBuiltinESMExports();
  t.after(() => { t.mock.restoreAll(); syncBuiltinESMExports(); });
  const readCredential = await prepareR22FileCredentialReader({ credentialFile: file, temporaryRoot: TEMP_ROOT });
  assert.deepEqual([opens, reads], [0, 0]);
  assert.equal(await readCredential(), value);
  assert.equal(opens, 1); assert.ok(reads >= 1);
});

test("file credential reader rejects modification, replacement, links and paths outside the direct temporary root", async (t) => {
  const modified = credentialPath("modified"), original = fakeOfficialCredential();
  await writeFile(modified, original, { flag: "wx" });
  t.after(() => unlink(modified).catch(() => {}));
  const modifiedReader = await prepareR22FileCredentialReader({ credentialFile: modified, temporaryRoot: TEMP_ROOT });
  await writeFile(modified, `${original}x`);
  await assert.rejects(modifiedReader(), staticCredentialError);

  const replaced = credentialPath("replaced"), displaced = `${replaced}.old`;
  await writeFile(replaced, original, { flag: "wx" });
  t.after(() => unlink(replaced).catch(() => {})); t.after(() => unlink(displaced).catch(() => {}));
  const replacedReader = await prepareR22FileCredentialReader({ credentialFile: replaced, temporaryRoot: TEMP_ROOT });
  await rename(replaced, displaced); await writeFile(replaced, original, { flag: "wx" });
  const replacementReader = await prepareR22FileCredentialReader({ credentialFile: replaced, temporaryRoot: TEMP_ROOT });
  assert.notEqual(replacementReader.sourceBinding.identitySha256, replacedReader.sourceBinding.identitySha256);
  await assert.rejects(replacedReader(), staticCredentialError);

  await t.test("symbolic-link", async (child) => {
    const target = credentialPath("link-target"), link = credentialPath("link");
    await writeFile(target, original, { flag: "wx" });
    child.after(() => unlink(link).catch(() => {})); child.after(() => unlink(target).catch(() => {}));
    try { await symlink(target, link, "file"); }
    catch (error) { if (error?.code === "EPERM") { child.skip("Windows symbolic-link privilege unavailable"); return; } throw error; }
    await assert.rejects(prepareR22FileCredentialReader({ credentialFile: link, temporaryRoot: TEMP_ROOT }), staticCredentialError);
  });
  await t.test("junction-root", async (child) => {
    const junction = path.join(TEMP_ROOT, `r22-junction-${randomUUID()}`);
    await symlink(TEMP_ROOT, junction, "junction");
    child.after(() => rm(junction, { force: true }).catch(() => {}));
    await assert.rejects(prepareR22FileCredentialReader({
      credentialFile: path.join(junction, path.basename(modified)), temporaryRoot: junction,
    }), staticCredentialError);
  });
  await assert.rejects(prepareR22FileCredentialReader({ credentialFile: path.basename(modified), temporaryRoot: TEMP_ROOT }), staticCredentialError);
  await assert.rejects(prepareR22FileCredentialReader({ credentialFile: modified, temporaryRoot: "." }), staticCredentialError);
  await assert.rejects(prepareR22FileCredentialReader({ credentialFile: path.join(path.dirname(TEMP_ROOT), `outside-${randomUUID()}.txt`), temporaryRoot: TEMP_ROOT }), staticCredentialError);
});

test("file credential reader rejects hard links created before or after preparation", async (t) => {
  const owned = path.join(TEMP_ROOT, `r22-hardlink-fixtures-${randomUUID()}`);
  await mkdir(owned);
  t.after(() => rm(owned, { recursive: true, force: true }).catch(() => {}));

  const outsideSource = path.join(owned, "outside-source.txt");
  const directAlias = credentialPath("hardlink-before");
  await writeFile(outsideSource, fakeOfficialCredential(), { flag: "wx" });
  await link(outsideSource, directAlias);
  t.after(() => unlink(directAlias).catch(() => {}));
  await assert.rejects(prepareR22FileCredentialReader({ credentialFile: directAlias, temporaryRoot: TEMP_ROOT }), staticCredentialError);

  const pinned = credentialPath("hardlink-after");
  const lateAlias = path.join(owned, "late-alias.txt");
  await writeFile(pinned, fakeOfficialCredential(), { flag: "wx" });
  t.after(() => unlink(pinned).catch(() => {}));
  const reader = await prepareR22FileCredentialReader({ credentialFile: pinned, temporaryRoot: TEMP_ROOT });
  await link(pinned, lateAlias);
  await assert.rejects(reader(), staticCredentialError);
});

test("file credential reader rejects oversized, malformed, multiline and OpenRouter-like bytes without leaking them", async (t) => {
  const cases = [
    ["empty", Buffer.alloc(0)],
    ["oversized", Buffer.alloc(8193, 0x78)],
    ["utf8", Buffer.from([0x73, 0x6b, 0x2d, 0xc3, 0x28])],
    ["control", `${fakeOfficialCredential()}\u0001`],
    ["multiline", `${fakeOfficialCredential()}\nsecond-line`],
    ["openrouter", `${officialPrefix()}${["or", "v1", "x".repeat(20)].join("-")}`],
  ];
  for (const [label, contents] of cases) await t.test(label, async (child) => {
    const file = credentialPath(label);
    await writeFile(file, contents, { flag: "wx" });
    child.after(() => unlink(file).catch(() => {}));
    let reader;
    try { reader = await prepareR22FileCredentialReader({ credentialFile: file, temporaryRoot: TEMP_ROOT }); }
    catch (error) { assert.equal(["empty", "oversized"].includes(label), true); staticCredentialError(error); return; }
    await assert.rejects(reader(), staticCredentialError);
  });
});
