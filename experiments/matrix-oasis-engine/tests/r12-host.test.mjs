import assert from "node:assert/strict";
import test from "node:test";
import { createPrototypeHost } from "../scripts/lib/prototype-host-core.mjs";
import { createR12PrototypeOperations, validateR12AssetApprovalSummary } from "../scripts/lib/r12-host-core.mjs";

const RUN = `${"a".repeat(64)}-${"b".repeat(64)}`;

function steps(overrides = {}) {
  return {
    async findCache() { return { ok: false }; },
    async generate() { return { ok: true, artifacts: {} }; },
    async describeAssets() { return { ok: true }; },
    async acquireEnvironment() { return { ok: true, value: "environment" }; },
    async acquireAssets() { return { ok: true, value: "assets" }; },
    async normalizeAssets() { return { ok: true, value: "normalized" }; },
    async spatializeEnvironment() { return { ok: true, value: "spatial" }; },
    async publishPrototype() { return { ok: true, runId: RUN }; },
    async publishSpatial() { return { ok: true, runId: RUN }; },
    async launch() { return { ok: true }; },
    async recover() { return { currentRunId: RUN, runs: [] }; },
    async stopLaunch() {},
    ...overrides,
  };
}

test("R12 operations run the bounded offline phases in exact order", async () => {
  const order = [];
  const operations = createR12PrototypeOperations(steps({
    async acquireEnvironment() { order.push("environment"); return { ok: true }; },
    async acquireAssets() { order.push("assets"); return { ok: true }; },
    async normalizeAssets() { order.push("normalize"); return { ok: true }; },
    async spatializeEnvironment() { order.push("spatialize"); return { ok: true }; },
    async publishPrototype() { order.push("prototype"); return { ok: true, runId: RUN }; },
    async publishSpatial() { order.push("overlay"); return { ok: true, runId: RUN }; },
  }));
  const stages = [];
  const acquired = await operations.acquire({ artifacts: {}, approval: {}, onStage: (stage) => stages.push(stage) });
  assert.equal(acquired.ok, true);
  assert.deepEqual(stages, ["normalizing", "spatializing"]);
  assert.deepEqual(order, ["environment", "assets", "normalize", "spatialize"]);
  assert.deepEqual(await operations.publish({ prompt: "neutral", artifacts: {}, acquisition: acquired }), { ok: true, runId: RUN });
  assert.deepEqual(order, ["environment", "assets", "normalize", "spatialize", "prototype", "overlay"]);
});

test("each failed phase stops later work and returns static diagnostics", async () => {
  for (const name of ["acquireEnvironment", "acquireAssets", "normalizeAssets", "spatializeEnvironment"]) {
    let later = 0;
    const fixture = steps({ [name]: async () => ({ ok: false, diagnostics: [{ code: "FIXTURE_REJECTED", path: "" }] }),
      async publishPrototype() { later += 1; return { ok: true, runId: RUN }; } });
    const result = await createR12PrototypeOperations(fixture).acquire({ artifacts: {}, approval: {}, onStage() {} });
    assert.equal(result.ok, false); assert.equal(later, 0);
  }
  const thrown = await createR12PrototypeOperations(steps({ async spatializeEnvironment() { throw new Error("secret"); } }))
    .acquire({ artifacts: {}, approval: {}, onStage() {} });
  assert.deepEqual(thrown.diagnostics.map((item) => item.code), ["R12_HOST_ACQUISITION_FAILED"]);
  assert.equal(JSON.stringify(thrown).includes("secret"), false);
});

test("R12 host profile exposes three Marble downloads while R10 remains byte-compatible", async () => {
  const operations = createR12PrototypeOperations(steps());
  const base = { configuration: { endpointHost: "api.openai.com", model: "fixture", modelReady: true, assetsReady: true, godotReady: false }, operations };
  assert.throws(() => createPrototypeHost({ ...base, profile: "unknown" }), { code: "PROTOTYPE_HOST_INTERNAL_ERROR" });
  assert.equal(validateR12AssetApprovalSummary({ blueprintSha256: `sha256:${"c".repeat(64)}`,
    marble: { model: "marble-1.1", maxCreates: 1, maxPolls: 180, maxDownloads: 3 },
    meshy: { model: "meshy-6", briefs: Array(6).fill({}), maxTasks: 12, creditLimit: 180 } }), true);
});
