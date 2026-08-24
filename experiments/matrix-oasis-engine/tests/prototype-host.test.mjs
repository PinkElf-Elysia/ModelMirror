import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { request as httpRequest } from "node:http";
import path from "node:path";
import test from "node:test";
import {
  PROTOTYPE_HOST,
  PROTOTYPE_HOST_MARKER,
  PROTOTYPE_HOST_ORIGIN,
  PROTOTYPE_HOST_PORT,
  PrototypeHostOperationalError,
  createPrototypeHost,
} from "../scripts/lib/prototype-host-core.mjs";

const VALID_RUN = `${"a".repeat(64)}-${"b".repeat(64)}`;
const SECOND_RUN = `${"c".repeat(64)}-${"d".repeat(64)}`;

function configuration(overrides = {}) {
  return { endpointHost: "api.example.test", model: "qualification-model", modelReady: true,
    assetsReady: true, godotReady: true, ...overrides };
}

function operationFixture(overrides = {}) {
  const calls = { findCache: 0, generate: 0, describeAssets: 0, acquire: 0, publish: 0, launch: 0,
    persistPending: 0, recoverPending: 0, discardPending: 0, recover: 0, stopLaunch: 0 };
  const operations = {
    async findCache() { calls.findCache += 1; return { ok: false }; },
    async generate() { calls.generate += 1; return { ok: true, artifacts: Object.freeze({ sceneBlueprintJson: "{}" }) }; },
    async describeAssets() { calls.describeAssets += 1; return { ok: true, blueprintSha256: `sha256:${"e".repeat(64)}`,
      environmentPrompt: "A neutral enclosed room.", briefs: [{ id: "asset-prop", kind: "prop", prompt: "A neutral console." }] }; },
    async acquire({ onStage }) { calls.acquire += 1; onStage("normalizing"); return { ok: true, value: "acquired" }; },
    async publish() { calls.publish += 1; return { ok: true, runId: VALID_RUN }; },
    async persistPending() { calls.persistPending += 1; },
    async recoverPending() { calls.recoverPending += 1; return { runs: [] }; },
    async discardPending() { calls.discardPending += 1; },
    async launch() { calls.launch += 1; return { ok: true }; },
    async recover() { calls.recover += 1; return { currentRunId: null, runs: [] }; },
    async stopLaunch() { calls.stopLaunch += 1; },
    ...overrides,
  };
  return { calls, operations };
}

test("R12 complete offline reuse needs no asset configuration and exposes zero external budget", async (t) => {
  const port = 43_121;
  const origin = `http://127.0.0.1:${port}`;
  const fixture = operationFixture({
    async describeAssets() {
      fixture.calls.describeAssets += 1;
      return { ok: true, blueprintSha256: `sha256:${"e".repeat(64)}`,
        environmentPrompt: "A neutral enclosed room.", environmentCached: true, assetsCached: true,
        briefs: [{ id: "asset-prop", kind: "prop", prompt: "A neutral console." }] };
    },
  });
  const host = createPrototypeHost({ profile: "r12", port,
    configuration: configuration({ assetsReady: false }), operations: fixture.operations });
  await host.start(); t.after(() => host.stop());
  const bootstrap = await fetch(`${origin}/api/bootstrap`, { headers: { origin, connection: "close" } });
  const cookie = bootstrap.headers.get("set-cookie").split(";")[0];
  const created = await fetch(`${origin}/api/runs`, { method: "POST", headers: {
    origin, cookie, connection: "close", "content-type": "application/json",
  }, body: JSON.stringify({ prompt: "Build one neutral interactive prototype." }) }).then((response) => response.json());
  await fetch(`${origin}/api/runs/${created.run.id}/approve-model`, { method: "POST", headers: {
    origin, cookie, connection: "close", "content-type": "application/json",
  }, body: JSON.stringify({ approvalHash: created.run.modelApproval.approvalHash }) });
  const waiting = await waitForLocal(origin, cookie, created.run.id, "awaiting_asset_approval");
  assert.equal(waiting.assetApproval.marble.maxCreates, 0);
  assert.equal(waiting.assetApproval.meshy.maxTasks, 0);
  assert.equal(waiting.assetApproval.meshy.creditLimit, 0);
  const approved = await fetch(`${origin}/api/runs/${created.run.id}/approve-assets`, { method: "POST", headers: {
    origin, cookie, connection: "close", "content-type": "application/json",
  }, body: JSON.stringify({ approvalHash: waiting.assetApproval.approvalHash }) });
  assert.equal(approved.status, 202);
  await waitForLocal(origin, cookie, created.run.id, "ready");
  assert.equal(fixture.calls.acquire, 1);
});

test("R12 durably restores generated artifacts at asset approval without persisting the original prompt", async (t) => {
  const port = 43_117;
  const origin = `http://127.0.0.1:${port}`;
  const prompt = "Build a restart-safe neutral prototype without writing this text to disk.";
  let pending = null;
  let modelCalls = 0;
  let published = null;
  const operations = operationFixture({
    async generate() {
      modelCalls += 1;
      return { ok: true, artifacts: Object.freeze({ sceneBlueprintJson: "{}" }) };
    },
    async persistPending(input) {
      assert.deepEqual(Object.keys(input), ["promptSha256", "model", "artifacts", "approval"]);
      assert.equal(JSON.stringify(input).includes(prompt), false);
      pending = input;
    },
    async recoverPending() { return { runs: pending === null ? [] : [pending] }; },
    async discardPending(input) {
      assert.equal(input.promptSha256, pending.promptSha256);
      pending = null;
    },
    async publish(input) {
      published = input;
      return { ok: true, runId: VALID_RUN };
    },
  }).operations;
  const first = createPrototypeHost({ profile: "r12", port, configuration: configuration(), operations });
  t.after(() => first.stop());
  await first.start();
  const firstBootstrap = await fetch(`${origin}/api/bootstrap`, { headers: { origin, connection: "close" } });
  const firstCookie = firstBootstrap.headers.get("set-cookie").split(";")[0];
  const created = await fetch(`${origin}/api/runs`, { method: "POST", headers: {
    origin, cookie: firstCookie, connection: "close", "content-type": "application/json",
  }, body: JSON.stringify({ prompt }) }).then((response) => response.json());
  await fetch(`${origin}/api/runs/${created.run.id}/approve-model`, { method: "POST", headers: {
    origin, cookie: firstCookie, connection: "close", "content-type": "application/json",
  }, body: JSON.stringify({ approvalHash: created.run.modelApproval.approvalHash }) }).then((response) => response.json());
  await waitForLocal(origin, firstCookie, created.run.id, "awaiting_asset_approval");
  await first.stop();
  assert.equal(modelCalls, 1);
  assert.notEqual(pending, null);

  const second = createPrototypeHost({ profile: "r12", port, configuration: configuration({ modelReady: false }), operations });
  t.after(() => second.stop());
  await second.start();
  const secondBootstrap = await fetch(`${origin}/api/bootstrap`, { headers: { origin, connection: "close" } });
  const secondBody = await secondBootstrap.json();
  const secondCookie = secondBootstrap.headers.get("set-cookie").split(";")[0];
  const restored = secondBody.runs.find((run) => run.status === "awaiting_asset_approval");
  assert.ok(restored);
  await fetch(`${origin}/api/runs/${restored.id}/approve-assets`, { method: "POST", headers: {
    origin, cookie: secondCookie, connection: "close", "content-type": "application/json",
  }, body: JSON.stringify({ approvalHash: restored.assetApproval.approvalHash }) }).then((response) => response.json());
  await waitForLocal(origin, secondCookie, restored.id, "ready");
  await second.stop();
  assert.equal(modelCalls, 1);
  assert.equal(published.prompt, null);
  assert.match(published.promptSha256, /^sha256:[0-9a-f]{64}$/u);
  assert.equal(published.model, "qualification-model");
  assert.equal(pending, null);
});

test("R12 asset failure preserves the generated candidate and requires a fresh approval before retry", async (t) => {
  const port = 43_118;
  const origin = `http://127.0.0.1:${port}`;
  const fixture = operationFixture({
    async acquire({ onStage }) {
      fixture.calls.acquire += 1;
      if (fixture.calls.acquire === 1) {
        return { ok: false, diagnostics: [{ code: "MESHY_PROVIDER_NETWORK_ERROR", path: "" }] };
      }
      onStage("normalizing");
      return { ok: true, value: "acquired" };
    },
  });
  const host = createPrototypeHost({ profile: "r12", port, configuration: configuration(), operations: fixture.operations });
  await host.start();
  t.after(() => host.stop());
  const bootstrap = await fetch(`${origin}/api/bootstrap`, { headers: { origin, connection: "close" } });
  const cookie = bootstrap.headers.get("set-cookie").split(";")[0];
  const created = await fetch(`${origin}/api/runs`, { method: "POST", headers: {
    origin, cookie, connection: "close", "content-type": "application/json",
  }, body: JSON.stringify({ prompt: "Build a retry-safe neutral prototype." }) }).then((response) => response.json());
  await fetch(`${origin}/api/runs/${created.run.id}/approve-model`, { method: "POST", headers: {
    origin, cookie, connection: "close", "content-type": "application/json",
  }, body: JSON.stringify({ approvalHash: created.run.modelApproval.approvalHash }) });
  const waiting = await waitForLocal(origin, cookie, created.run.id, "awaiting_asset_approval");
  await fetch(`${origin}/api/runs/${created.run.id}/approve-assets`, { method: "POST", headers: {
    origin, cookie, connection: "close", "content-type": "application/json",
  }, body: JSON.stringify({ approvalHash: waiting.assetApproval.approvalHash }) });
  const retryable = await waitForLocal(origin, cookie, created.run.id, "awaiting_asset_approval");
  assert.deepEqual(retryable.diagnostics.map(({ code }) => code), ["MESHY_PROVIDER_NETWORK_ERROR"]);
  assert.equal(retryable.assetApproval.approved, false);
  assert.equal(fixture.calls.discardPending, 0);
  await fetch(`${origin}/api/runs/${created.run.id}/approve-assets`, { method: "POST", headers: {
    origin, cookie, connection: "close", "content-type": "application/json",
  }, body: JSON.stringify({ approvalHash: retryable.assetApproval.approvalHash }) });
  const ready = await waitForLocal(origin, cookie, created.run.id, "ready");
  assert.equal(ready.resultRunId, VALID_RUN);
  assert.deepEqual({ generate: fixture.calls.generate, acquire: fixture.calls.acquire,
    persistPending: fixture.calls.persistPending, discardPending: fixture.calls.discardPending },
  { generate: 1, acquire: 2, persistPending: 1, discardPending: 1 });
});

async function waitForLocal(origin, cookie, id, expected) {
  for (let index = 0; index < 100; index += 1) {
    const response = await fetch(`${origin}/api/runs/${id}`, { headers: { origin, cookie, connection: "close" } });
    const body = await response.json();
    if (body.run?.status === expected) return body.run;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail(`run did not reach ${expected}`);
}

async function startHost(overrides = {}, config = configuration()) {
  const fixture = operationFixture(overrides);
  const host = createPrototypeHost({ configuration: config, operations: fixture.operations });
  await host.start();
  return { host, ...fixture };
}

async function requestWithHost(pathname, hostHeader) {
  return new Promise((resolve, reject) => {
    const request = httpRequest({ host: PROTOTYPE_HOST, port: PROTOTYPE_HOST_PORT, path: pathname,
      method: "GET", headers: { host: hostHeader, connection: "close" } }, (response) => {
      response.resume(); response.once("end", () => resolve(response.statusCode));
    });
    request.once("error", reject); request.end();
  });
}

test("Creator assets are served from the fixed loopback origin with a closed CSP", async (t) => {
  const fixture = operationFixture();
  const original = new TextEncoder().encode("<!doctype html><title>R10</title>");
  const host = createPrototypeHost({ configuration: configuration(), operations: fixture.operations,
    webAssets: new Map([
      ["/", { contentType: "text/html; charset=utf-8", bytes: original }],
      ["/index.html", { contentType: "text/html; charset=utf-8", bytes: original }],
      ["/assets/app.js", { contentType: "text/javascript; charset=utf-8", bytes: new TextEncoder().encode("export {}") }],
    ]) });
  original.fill(0);
  await host.start(); t.after(() => host.stop());
  const response = await fetch(`${PROTOTYPE_HOST_ORIGIN}/`, { redirect: "manual" });
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "<!doctype html><title>R10</title>");
  assert.equal(response.headers.get("content-security-policy"),
    "default-src 'self'; base-uri 'none'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; img-src 'self'; object-src 'none'; script-src 'self' 'unsafe-eval'; style-src 'self'");
  assert.equal(response.headers.get("access-control-allow-origin"), null);
  assert.equal((await fetch(`${PROTOTYPE_HOST_ORIGIN}/assets/app.js`, { method: "HEAD" })).status, 200);
  assert.equal(await requestWithHost("/", "attacker.invalid"), 403);
  assert.equal((await fetch(`${PROTOTYPE_HOST_ORIGIN}/unknown`)).status, 403);
});

async function request(pathname, { method = "GET", cookie = null, body, origin = PROTOTYPE_HOST_ORIGIN, contentType = "application/json" } = {}) {
  const headers = { origin, connection: "close" };
  if (cookie) headers.cookie = cookie;
  if (body !== undefined) headers["content-type"] = contentType;
  const response = await fetch(`${PROTOTYPE_HOST_ORIGIN}${pathname}`, { method, headers,
    body: body === undefined ? undefined : typeof body === "string" ? body : JSON.stringify(body), redirect: "manual" });
  return { status: response.status, headers: response.headers, body: await response.json() };
}

async function session() {
  const response = await request("/api/bootstrap");
  assert.equal(response.status, 200); const cookie = response.headers.get("set-cookie")?.split(";", 1)[0];
  assert.match(cookie, /^matrix_oasis_r10_session=[a-f0-9]{64}$/u);
  return { cookie, bootstrap: response.body };
}

async function createRun(cookie, prompt = "Build one neutral interactive prototype.") {
  return request("/api/runs", { method: "POST", cookie, body: { prompt } });
}

async function getRun(cookie, id) { return request(`/api/runs/${id}`, { cookie }); }

async function waitFor(cookie, id, expected) {
  for (let index = 0; index < 100; index += 1) {
    const response = await getRun(cookie, id);
    if (response.body.run?.status === expected) return response.body.run;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail(`run did not reach ${expected}`);
}

test("host constants and public construction surface are fixed", () => {
  assert.equal(PROTOTYPE_HOST, "127.0.0.1"); assert.equal(PROTOTYPE_HOST_PORT, 43110);
  assert.equal(PROTOTYPE_HOST_ORIGIN, "http://127.0.0.1:43110"); assert.equal(PROTOTYPE_HOST_MARKER, "MATRIX_OASIS_R10_PROTOTYPE_HOST");
  const fixture = operationFixture();
  assert.throws(() => createPrototypeHost({ configuration: { ...configuration(), leaked: "secret" }, operations: fixture.operations }),
    PrototypeHostOperationalError);
  assert.throws(() => createPrototypeHost({ configuration: configuration(), operations: { ...fixture.operations, leaked() {} } }),
    PrototypeHostOperationalError);
});

test("R12 recovery is content-bound and runs only after its dedicated Creator approval", async (t) => {
  const fixture = operationFixture();
  let recoveries = 0;
  const port = 43_112;
  const origin = `http://127.0.0.1:${port}`;
  const host = createPrototypeHost({ profile: "r12", port, configuration: configuration(), operations: fixture.operations,
    recovery: {
      summary: { model: "marble-1.1", worldIdSha256: `sha256:${"1".repeat(64)}`,
        maxCreates: 0, maxPolls: 0, maxWorldGets: 0, maxDownloads: 0, creditLimit: 0, usdLimitCents: 0 },
      async execute() { recoveries += 1; return { ok: true }; },
    } });
  await host.start(); t.after(() => host.stop());
  const bootstrapResponse = await fetch(`${origin}/api/bootstrap`, { headers: { origin } });
  const cookie = bootstrapResponse.headers.get("set-cookie")?.split(";", 1)[0];
  const bootstrap = await bootstrapResponse.json();
  assert.equal(recoveries, 0);
  assert.equal(bootstrap.recovery.status, "awaiting_approval");
  assert.equal(bootstrap.recovery.maxWorldGets, 0);
  assert.equal(bootstrap.recovery.maxDownloads, 0);
  assert.equal(JSON.stringify(bootstrap).includes("705fd38b"), false);
  const approved = await fetch(`${origin}/api/recovery/approve`, { method: "POST",
    headers: { origin, cookie, "content-type": "application/json; charset=utf-8" },
    body: JSON.stringify({ approvalHash: bootstrap.recovery.approvalHash }) });
  assert.equal(approved.status, 202);
  for (let index = 0; index < 20 && recoveries === 0; index += 1) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(recoveries, 1);
  const duplicate = await fetch(`${origin}/api/recovery/approve`, { method: "POST",
    headers: { origin, cookie, "content-type": "application/json; charset=utf-8" },
    body: JSON.stringify({ approvalHash: bootstrap.recovery.approvalHash }) });
  assert.equal(duplicate.status, 409);
});

test("R12 worlds:list discovery is single-use, read-only, and exposes only candidate hashes", async (t) => {
  const fixture = operationFixture();
  let listRequests = 0;
  let recoveries = 0;
  const port = 43_117;
  const origin = `http://127.0.0.1:${port}`;
  const rawWorldId = "world-private-sentinel";
  const rawPrompt = "PRIVATE-PROMPT-SENTINEL";
  const host = createPrototypeHost({ profile: "r12", port, configuration: configuration(), operations: fixture.operations,
    worldDiscovery: {
      summary: { provider: "world-labs-marble", operation: "worlds:list", model: "marble-1.1", pageSize: 100,
        status: "SUCCEEDED", sortBy: "created_at", maxRequests: 1, maxCreates: 0, maxPolls: 0,
        maxWorldGets: 0, maxDownloads: 0, creditLimit: 0, usdLimitCents: 0 },
      async execute() {
        listRequests += 1;
        return { ok: true, worlds: [{ worldId: rawWorldId, createdAt: "2026-08-14T12:00:00Z",
          updatedAt: "2026-08-14T12:30:00Z", model: "marble-1.1", worldPrompt: rawPrompt,
          assets: { panorama: true, collider: true, spatialSource: true } }],
        counts: { listRequests: 1, creates: 0, polls: 0, worldGets: 0, downloads: 0 } };
      },
      async recover(worldId) {
        assert.equal(worldId, rawWorldId);
        recoveries += 1;
        return { ok: true };
      },
    } });
  await host.start(); t.after(() => host.stop());
  const bootstrapResponse = await fetch(`${origin}/api/bootstrap`, { headers: { origin } });
  const cookie = bootstrapResponse.headers.get("set-cookie")?.split(";", 1)[0];
  const bootstrap = await bootstrapResponse.json();
  assert.equal(listRequests, 0);
  assert.equal(bootstrap.worldDiscovery.statusState, "awaiting_approval");
  assert.equal(JSON.stringify(bootstrap).includes(rawWorldId), false);
  assert.equal(JSON.stringify(bootstrap).includes(rawPrompt), false);
  const approved = await fetch(`${origin}/api/world-discovery/approve`, { method: "POST",
    headers: { origin, cookie, "content-type": "application/json; charset=utf-8" },
    body: JSON.stringify({ approvalHash: bootstrap.worldDiscovery.approvalHash }) });
  assert.equal(approved.status, 202);
  for (let index = 0; index < 20 && listRequests === 0; index += 1) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(listRequests, 1);
  const completed = await fetch(`${origin}/api/bootstrap`, { headers: { origin, cookie } });
  const body = await completed.json();
  assert.equal(body.worldDiscovery.statusState, "ready");
  assert.equal(body.worldDiscovery.candidates.length, 1);
  assert.match(body.worldDiscovery.candidates[0].worldIdSha256, /^sha256:[0-9a-f]{64}$/u);
  assert.match(body.worldDiscovery.candidates[0].promptSha256, /^sha256:[0-9a-f]{64}$/u);
  assert.equal(JSON.stringify(body).includes(rawWorldId), false);
  assert.equal(JSON.stringify(body).includes(rawPrompt), false);
  const prepared = await fetch(`${origin}/api/world-discovery/prepare-recovery`, { method: "POST",
    headers: { origin, cookie, "content-type": "application/json; charset=utf-8" },
    body: JSON.stringify({ worldIdSha256: body.worldDiscovery.candidates[0].worldIdSha256 }) });
  assert.equal(prepared.status, 200);
  const preparedBody = await prepared.json();
  assert.equal(preparedBody.worldDiscovery.recovery.status, "awaiting_approval");
  assert.equal(preparedBody.worldDiscovery.recovery.maxWorldGets, 1);
  assert.equal(preparedBody.worldDiscovery.recovery.maxDownloads, 3);
  assert.equal(JSON.stringify(preparedBody).includes(rawWorldId), false);
  assert.equal(recoveries, 0);
  const recovered = await fetch(`${origin}/api/world-discovery/approve-recovery`, { method: "POST",
    headers: { origin, cookie, "content-type": "application/json; charset=utf-8" },
    body: JSON.stringify({ approvalHash: preparedBody.worldDiscovery.recovery.approvalHash }) });
  assert.equal(recovered.status, 202);
  for (let index = 0; index < 20 && recoveries === 0; index += 1) await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(recoveries, 1);
  const duplicate = await fetch(`${origin}/api/world-discovery/approve`, { method: "POST",
    headers: { origin, cookie, "content-type": "application/json; charset=utf-8" },
    body: JSON.stringify({ approvalHash: bootstrap.worldDiscovery.approvalHash }) });
  assert.equal(duplicate.status, 409);
  assert.equal(listRequests, 1);
});

test("R12 recovered environment changes the Creator asset approval to zero Marble calls", async (t) => {
  const fixture = operationFixture({
    async describeAssets() {
      fixture.calls.describeAssets += 1;
      return { ok: true, blueprintSha256: `sha256:${"e".repeat(64)}`,
        environmentPrompt: "A neutral enclosed room.", environmentCached: true,
        briefs: [{ id: "asset-prop", kind: "prop", prompt: "A neutral console." }] };
    },
  });
  const port = 43_113;
  const origin = `http://127.0.0.1:${port}`;
  const host = createPrototypeHost({ profile: "r12", port, configuration: configuration(), operations: fixture.operations });
  await host.start(); t.after(() => host.stop());
  const bootstrapResponse = await fetch(`${origin}/api/bootstrap`, { headers: { origin } });
  const cookie = bootstrapResponse.headers.get("set-cookie")?.split(";", 1)[0];
  const createdResponse = await fetch(`${origin}/api/runs`, { method: "POST",
    headers: { origin, cookie, "content-type": "application/json" },
    body: JSON.stringify({ prompt: "Build one neutral interactive prototype." }) });
  const created = (await createdResponse.json()).run;
  await fetch(`${origin}/api/runs/${created.id}/approve-model`, { method: "POST",
    headers: { origin, cookie, "content-type": "application/json" },
    body: JSON.stringify({ approvalHash: created.modelApproval.approvalHash }) });
  let waiting = null;
  for (let index = 0; index < 100; index += 1) {
    const response = await fetch(`${origin}/api/runs/${created.id}`, { headers: { origin, cookie } });
    const body = await response.json();
    if (body.run?.status === "awaiting_asset_approval") { waiting = body.run; break; }
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.equal(waiting?.assetApproval.marble.recovered, true);
  assert.deepEqual({ creates: waiting.assetApproval.marble.maxCreates, polls: waiting.assetApproval.marble.maxPolls,
    downloads: waiting.assetApproval.marble.maxDownloads, credits: waiting.assetApproval.marble.creditLimit },
  { creates: 0, polls: 0, downloads: 0, credits: 0 });
});

test("same-origin bootstrap issues one HttpOnly strict cookie and no CORS surface", async (t) => {
  const { host } = await startHost(); t.after(() => host.stop());
  const foreign = await request("/api/bootstrap", { origin: "https://attacker.invalid" });
  assert.equal(foreign.status, 403); assert.equal(foreign.headers.get("access-control-allow-origin"), null);
  const established = await request("/api/bootstrap");
  assert.equal(established.status, 200); assert.equal(established.body.marker, PROTOTYPE_HOST_MARKER);
  const setCookie = established.headers.get("set-cookie"); assert.match(setCookie, /HttpOnly/u); assert.match(setCookie, /SameSite=Strict/u);
  assert.equal(established.headers.get("access-control-allow-origin"), null);
  const missing = await request("/api/runs/current"); assert.equal(missing.status, 401);
});

test("model and asset approvals are content-bound and external operations start only after approval", async (t) => {
  const { host, calls } = await startHost(); t.after(() => host.stop()); const { cookie } = await session();
  const created = await createRun(cookie); assert.equal(created.status, 201);
  const run = created.body.run; assert.equal(run.status, "awaiting_model_approval"); assert.equal(calls.generate, 0); assert.equal(calls.acquire, 0);
  const restored = await request("/api/bootstrap");
  assert.deepEqual(restored.body.runs.map(({ id, status }) => ({ id, status })),
    [{ id: run.id, status: "awaiting_model_approval" }]);
  const staleModel = await request(`/api/runs/${run.id}/approve-model`, { method: "POST", cookie, body: { approvalHash: `sha256:${"0".repeat(64)}` } });
  assert.equal(staleModel.status, 409); assert.equal(calls.generate, 0);
  const acceptedModel = await request(`/api/runs/${run.id}/approve-model`, { method: "POST", cookie, body: { approvalHash: run.modelApproval.approvalHash } });
  assert.equal(acceptedModel.status, 202);
  const waiting = await waitFor(cookie, run.id, "awaiting_asset_approval");
  assert.equal(calls.generate, 1); assert.equal(calls.acquire, 0); assert.equal(waiting.assetApproval.marble.model, "marble-1.1");
  assert.deepEqual(waiting.assetApproval.meshy.briefs.map(({ id }) => id), ["asset-prop"]);
  const staleAsset = await request(`/api/runs/${run.id}/approve-assets`, { method: "POST", cookie, body: { approvalHash: run.modelApproval.approvalHash } });
  assert.equal(staleAsset.status, 409); assert.equal(calls.acquire, 0);
  const acceptedAsset = await request(`/api/runs/${run.id}/approve-assets`, { method: "POST", cookie, body: { approvalHash: waiting.assetApproval.approvalHash } });
  assert.equal(acceptedAsset.status, 202);
  const ready = await waitFor(cookie, run.id, "ready"); assert.equal(ready.resultRunId, VALID_RUN);
  assert.deepEqual({ generate: calls.generate, acquire: calls.acquire, publish: calls.publish }, { generate: 1, acquire: 1, publish: 1 });
  const duplicate = await request(`/api/runs/${run.id}/approve-assets`, { method: "POST", cookie, body: { approvalHash: waiting.assetApproval.approvalHash } });
  assert.equal(duplicate.status, 409); assert.equal(calls.acquire, 1);
});

test("asset approval expands to six briefs and twelve bounded Meshy tasks", async () => {
  const briefs = Array.from({ length: 6 }, (_, index) => ({
    id: `asset-${index}`,
    kind: index % 2 === 0 ? "prop" : "character-placeholder",
    prompt: `Neutral asset brief ${index}`,
  }));
  const fixture = operationFixture({
    async describeAssets() {
      fixture.calls.describeAssets += 1;
      return {
        ok: true,
        blueprintSha256: `sha256:${"e".repeat(64)}`,
        environmentPrompt: "A connected neutral environment.",
        briefs,
      };
    },
  });
  const host = createPrototypeHost({ configuration: configuration(), operations: fixture.operations });
  await host.start();
  try {
    const { cookie } = await session();
    const created = await createRun(cookie);
    await request(`/api/runs/${created.body.run.id}/approve-model`, {
      method: "POST",
      cookie,
      body: { approvalHash: created.body.run.modelApproval.approvalHash },
    });
    const waiting = await waitFor(cookie, created.body.run.id, "awaiting_asset_approval");
    assert.deepEqual(waiting.assetApproval.meshy.briefs, briefs);
    assert.equal(waiting.assetApproval.meshy.maxTasks, 12);
    assert.equal(waiting.assetApproval.meshy.creditLimit, 180);
    assert.equal(fixture.calls.acquire, 0);
  } finally {
    await host.stop();
  }

  const overLimit = operationFixture({
    async describeAssets() {
      return {
        ok: true,
        blueprintSha256: `sha256:${"e".repeat(64)}`,
        environmentPrompt: "A connected neutral environment.",
        briefs: [...briefs, { id: "asset-6", kind: "prop", prompt: "One extra brief" }],
      };
    },
  });
  const rejectedHost = createPrototypeHost({ configuration: configuration(), operations: overLimit.operations });
  await rejectedHost.start();
  try {
    const { cookie } = await session();
    const created = await createRun(cookie);
    await request(`/api/runs/${created.body.run.id}/approve-model`, {
      method: "POST",
      cookie,
      body: { approvalHash: created.body.run.modelApproval.approvalHash },
    });
    const failed = await waitFor(cookie, created.body.run.id, "failed");
    assert.equal(failed.diagnostics[0].code, "PROTOTYPE_HOST_GENERATION_FAILED");
    assert.equal(overLimit.calls.acquire, 0);
  } finally {
    await rejectedHost.stop();
  }
});

test("only one nonterminal run exists and a cache hit requires no approval or provider operation", async (t) => {
  const fixture = operationFixture({ async findCache() { fixture.calls.findCache += 1; return { ok: true, runId: SECOND_RUN }; } });
  const host = createPrototypeHost({ configuration: configuration({ modelReady: false, assetsReady: false }), operations: fixture.operations });
  await host.start(); t.after(() => host.stop()); const { cookie } = await session();
  const cached = await createRun(cookie); assert.equal(cached.body.run.status, "ready"); assert.equal(cached.body.run.cacheHit, true);
  assert.equal(cached.body.run.modelApproval, null); assert.equal(cached.body.run.assetApproval, null);
  assert.deepEqual({ generate: fixture.calls.generate, acquire: fixture.calls.acquire, publish: fixture.calls.publish }, { generate: 0, acquire: 0, publish: 0 });
  const current = await request("/api/runs/current", { cookie }); assert.equal(current.body.currentRunId, SECOND_RUN);
});

test("acquiring, normalizing, spatializing, and assembling are separately observable in the fixed order", async (t) => {
  let releaseAcquire; let releaseNormalize; let releaseSpatialize; let releasePublish;
  const acquireGate = new Promise((resolve) => { releaseAcquire = resolve; });
  const normalizeGate = new Promise((resolve) => { releaseNormalize = resolve; });
  const spatializeGate = new Promise((resolve) => { releaseSpatialize = resolve; });
  const publishGate = new Promise((resolve) => { releasePublish = resolve; });
  let normalized; const normalizedSignal = new Promise((resolve) => { normalized = resolve; });
  let assembling; const assemblingSignal = new Promise((resolve) => { assembling = resolve; });
  let spatialized; const spatializedSignal = new Promise((resolve) => { spatialized = resolve; });
  const { host } = await startHost({
    async acquire({ onStage }) {
      await acquireGate; onStage("normalizing"); normalized(); await normalizeGate;
      onStage("spatializing"); spatialized(); await spatializeGate; return { ok: true };
    },
    async publish() { assembling(); await publishGate; return { ok: true, runId: VALID_RUN }; },
  });
  t.after(async () => { releaseAcquire(); releaseNormalize(); releaseSpatialize(); releasePublish(); await host.stop(); });
  const { cookie } = await session(); const created = await createRun(cookie);
  await request(`/api/runs/${created.body.run.id}/approve-model`, { method: "POST", cookie,
    body: { approvalHash: created.body.run.modelApproval.approvalHash } });
  const waiting = await waitFor(cookie, created.body.run.id, "awaiting_asset_approval");
  await request(`/api/runs/${created.body.run.id}/approve-assets`, { method: "POST", cookie,
    body: { approvalHash: waiting.assetApproval.approvalHash } });
  assert.equal((await getRun(cookie, created.body.run.id)).body.run.status, "acquiring");
  releaseAcquire(); await normalizedSignal;
  assert.equal((await getRun(cookie, created.body.run.id)).body.run.status, "normalizing");
  releaseNormalize(); await spatializedSignal;
  assert.equal((await getRun(cookie, created.body.run.id)).body.run.status, "spatializing");
  releaseSpatialize(); await assemblingSignal;
  assert.equal((await getRun(cookie, created.body.run.id)).body.run.status, "assembling");
  releasePublish(); await waitFor(cookie, created.body.run.id, "ready");
});

test("a concurrent run is rejected until the active run reaches a terminal state", async (t) => {
  let release; const gate = new Promise((resolve) => { release = resolve; });
  const { host } = await startHost({ async generate() { await gate; return { ok: false, diagnostics: [{ code: "GENERATION_REJECTED", path: "" }] }; } });
  t.after(() => host.stop()); const { cookie } = await session(); const first = await createRun(cookie);
  await request(`/api/runs/${first.body.run.id}/approve-model`, { method: "POST", cookie, body: { approvalHash: first.body.run.modelApproval.approvalHash } });
  const blocked = await createRun(cookie, "A second prompt."); assert.equal(blocked.status, 409);
  release(); await waitFor(cookie, first.body.run.id, "failed");
  const next = await createRun(cookie, "A second prompt."); assert.equal(next.status, 201);
});

test("provider failures are statically rebuilt and do not change the previous current run", async (t) => {
  let cached = true;
  const fixture = operationFixture({
    async findCache() { fixture.calls.findCache += 1; if (cached) { cached = false; return { ok: true, runId: VALID_RUN }; } return { ok: false }; },
    async generate() { throw new Error("dynamic-secret-sentinel"); },
  });
  const host = createPrototypeHost({ configuration: configuration(), operations: fixture.operations }); await host.start(); t.after(() => host.stop());
  const { cookie } = await session(); await createRun(cookie, "Cached prompt.");
  const failed = await createRun(cookie, "New prompt.");
  await request(`/api/runs/${failed.body.run.id}/approve-model`, { method: "POST", cookie, body: { approvalHash: failed.body.run.modelApproval.approvalHash } });
  const terminal = await waitFor(cookie, failed.body.run.id, "failed");
  assert.equal(terminal.diagnostics[0].code, "PROTOTYPE_HOST_INTERNAL_ERROR");
  assert.equal(JSON.stringify(terminal).includes("sentinel"), false);
  const current = await request("/api/runs/current", { cookie }); assert.equal(current.body.currentRunId, VALID_RUN);
});

test("partial asset acquisition failure never publishes and preserves the previous current run", async (t) => {
  let cached = true;
  const fixture = operationFixture({
    async findCache() { if (cached) { cached = false; return { ok: true, runId: VALID_RUN }; } return { ok: false }; },
    async acquire() { fixture.calls.acquire += 1; return { ok: false,
      diagnostics: [{ code: "MARBLE_PROVIDER_TIMEOUT", path: "", leaked: "dynamic-secret" }] }; },
  });
  const host = createPrototypeHost({ configuration: configuration(), operations: fixture.operations }); await host.start(); t.after(() => host.stop());
  const { cookie } = await session(); await createRun(cookie, "Cached prompt."); const created = await createRun(cookie, "New prompt.");
  await request(`/api/runs/${created.body.run.id}/approve-model`, { method: "POST", cookie,
    body: { approvalHash: created.body.run.modelApproval.approvalHash } });
  const waiting = await waitFor(cookie, created.body.run.id, "awaiting_asset_approval");
  await request(`/api/runs/${created.body.run.id}/approve-assets`, { method: "POST", cookie,
    body: { approvalHash: waiting.assetApproval.approvalHash } });
  const failed = await waitFor(cookie, created.body.run.id, "failed");
  assert.equal(failed.diagnostics[0].code, "MARBLE_PROVIDER_TIMEOUT"); assert.equal(JSON.stringify(failed).includes("secret"), false);
  assert.equal(fixture.calls.publish, 0);
  assert.equal((await request("/api/runs/current", { cookie })).body.currentRunId, VALID_RUN);
});

test("request grammar rejects wrong content type, unknown fields, oversize text, and unsafe routes", async (t) => {
  const { host } = await startHost(); t.after(() => host.stop()); const { cookie } = await session();
  assert.equal((await request("/api/runs", { method: "POST", cookie, body: { prompt: "ok" }, contentType: "text/plain" })).status, 415);
  assert.equal((await request("/api/runs", { method: "POST", cookie, body: { prompt: "ok", extra: true } })).status, 400);
  assert.equal((await request("/api/runs", { method: "POST", cookie, body: { prompt: "x".repeat(32_769) } })).status, 400);
  assert.equal((await request("/api/runs?expanded=1", { method: "POST", cookie, body: { prompt: "ok" } })).status, 403);
  assert.equal((await request("/api/unknown", { cookie })).status, 404);
});

test("launch is ready-only and single-flight without altering current on failure", async (t) => {
  let release; const gate = new Promise((resolve) => { release = resolve; });
  const { host, calls } = await startHost({ async findCache() { return { ok: true, runId: VALID_RUN }; },
    async launch() { calls.launch += 1; await gate; return { ok: true }; } });
  t.after(() => host.stop()); const { cookie } = await session(); const created = await createRun(cookie);
  const first = request(`/api/runs/${created.body.run.id}/launch`, { method: "POST", cookie, body: {} });
  await new Promise((resolve) => setTimeout(resolve, 10));
  const blocked = await request(`/api/runs/${created.body.run.id}/launch`, { method: "POST", cookie, body: {} });
  assert.equal(blocked.status, 409); release(); assert.equal((await first).status, 202); assert.equal(calls.launch, 1);
});

test("successful runs recover across host restart without raw provider state", async (t) => {
  const fixture = operationFixture({ async recover() { fixture.calls.recover += 1; return { currentRunId: VALID_RUN,
    runs: [
      { runId: VALID_RUN, promptSha256: `sha256:${"e".repeat(64)}`, model: "qualification-model" },
      { runId: "invalid", promptSha256: `sha256:${"e".repeat(64)}`, model: "qualification-model" },
      { runId: SECOND_RUN, promptSha256: `sha256:${"e".repeat(64)}`, model: "qualification-model", leaked: "secret" },
    ] }; } });
  const host = createPrototypeHost({ configuration: configuration(), operations: fixture.operations }); await host.start(); t.after(() => host.stop());
  const { cookie, bootstrap } = await session(); assert.equal(bootstrap.currentRunId, VALID_RUN);
  assert.equal(bootstrap.runs.length, 1); assert.equal(bootstrap.runs[0].id, "r10-run-1");
  assert.equal(bootstrap.runs[0].resultRunId, VALID_RUN); assert.equal(bootstrap.runs[0].status, "ready");
  const current = await request("/api/runs/current", { cookie }); assert.equal(current.body.run.id, "r10-run-1");
  assert.equal((await request("/api/runs/r10-run-1/launch", { method: "POST", cookie, body: {} })).status, 202);
  assert.equal(JSON.stringify(bootstrap).includes("secret"), false);
});

test("host and preview source do not implement provider networking or expose secret values", async () => {
  const hostSource = await readFile(new URL("../scripts/lib/prototype-host-core.mjs", import.meta.url), "utf8");
  assert.equal(hostSource.includes("fetch("), false); assert.equal(hostSource.includes("process.env"), false);
  const preview = await readFile(new URL("../scripts/preview-prototype.mjs", import.meta.url), "utf8");
  assert.equal(preview.includes("console.log"), false); assert.equal(preview.includes("shell: true"), false);
  assert.equal(preview.includes('assemblyProfile: "matrix-oasis.prototype-assembly/2"'), true);
  assert.equal(preview.includes("environmentPrompt: environmentPlan.plan.environmentPrompt"), true);
  assert.equal(preview.includes("environmentPrompt: blueprint.scene.environmentPrompt"), false);
  for (const forbidden of ["LLM_GATEWAY_", "OPENROUTER_API_KEY", "WORLD_LABS_API_KEY"]) assert.equal(preview.includes(forbidden), false);
});

test("real preview entrypoint starts unconfigured without reading credentials or calling providers", async () => {
  const runRoot = await mkdtemp(path.join(path.resolve(path.parse(process.cwd()).root, "tmp"), "matrix-oasis-r10-host-entry-"));
  await rm(runRoot, { recursive: true, force: true });
  const child = spawn(process.execPath, [path.resolve("scripts/preview-prototype.mjs"), "--run-root", runRoot], {
    cwd: path.resolve("."), windowsHide: true, stdio: ["ignore", "pipe", "pipe"],
    env: Object.fromEntries(Object.entries(process.env).filter(([name]) => !name.startsWith("MATRIX_OASIS_") && name !== "GODOT_BIN")),
  });
  const exited = new Promise((resolve) => child.once("exit", resolve));
  let stdout = ""; let stderr = ""; child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => { stdout += chunk; }); child.stderr.on("data", (chunk) => { stderr += chunk; });
  try {
    for (let index = 0; index < 1_000 && !stdout.includes(PROTOTYPE_HOST_MARKER); index += 1) await new Promise((resolve) => setTimeout(resolve, 10));
    assert.match(stdout, /^MATRIX_OASIS_R10_PROTOTYPE_HOST origin=http:\/\/127\.0\.0\.1:43110\n$/u, stderr);
    const bootstrap = await request("/api/bootstrap"); assert.equal(bootstrap.status, 200);
    assert.deepEqual(bootstrap.body.readiness, { model: false, assets: false, godot: false });
    assert.equal(stderr, "");
  } finally {
    if (child.exitCode === null) child.kill("SIGTERM");
    const forced = setTimeout(() => { if (child.exitCode === null) child.kill("SIGKILL"); }, 2_000);
    await exited; clearTimeout(forced);
    await rm(runRoot, { recursive: true, force: true });
  }
});
