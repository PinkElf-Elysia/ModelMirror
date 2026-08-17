import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { deflateSync } from "node:zlib";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  materializeRecoveredPrototypeEnvironmentWithSpatialSource,
  planPrototypeEnvironment,
} from "../packages/prototype-environment-pipeline/src/index.mjs";
import {
  acquireMarbleEnvironmentWithSpatialSource,
  createMarbleWorldProvider,
} from "../packages/prototype-environment-pipeline/src/marble-provider.mjs";
import {
  R12_LAST_TRAIN_ACCEPTANCE_PROFILE,
  parseR12CallArguments,
  readR12CallInputs,
  verifyR12CreatorPublishedQualification,
} from "../scripts/lib/r12-qualification-core.mjs";
import { createR12PrototypeOperations } from "../scripts/lib/r12-host-core.mjs";
import { createPrototypeHost } from "../scripts/lib/prototype-host-core.mjs";
import {
  createR12LiveSteps,
  createR12CachedRecoveryConfiguration,
  createR12RecoveryConfiguration,
  createR12WorldDiscoveryConfiguration,
  matchReusablePrototypeAssets,
  parseR12PreviewArguments,
} from "../scripts/preview-r12.mjs";
import {
  parseR12QualificationArguments,
  runR12QualificationVerification,
} from "../scripts/qualify-r12.mjs";

const moduleRoot = path.resolve(new URL("..", import.meta.url).pathname.slice(1));
const temporaryRoot = path.resolve(path.parse(process.cwd()).root, "tmp");

function qualificationArgs() {
  return [
    "--prompt-file", path.join(moduleRoot, "docs", "R12_LAST_TRAIN_PROMPT.txt"),
    "--profile", path.join(moduleRoot, "docs", "R12_LAST_TRAIN_PROFILE.json"),
    "--run-root", path.join(temporaryRoot, "matrix-oasis-r12-qualification"),
  ];
}

function promptHash(value) {
  return `sha256:${createHash("sha256").update(new TextEncoder().encode(value)).digest("hex")}`;
}

test("historical asset reuse is a complete deterministic one-to-one semantic match", () => {
  const roles = Object.freeze(["visual"]);
  const targets = Object.freeze([
    { id: "new-student", entityId: "student-backpack", kind: "character-placeholder",
      prompt: "A full body student with a worn backpack and practical shoes.", roles },
    { id: "new-nurse", entityId: "night-shift-nurse", kind: "character-placeholder",
      prompt: "A full body night shift nurse in practical scrubs and a weathered coat.", roles },
    { id: "new-map", entityId: "route-map", kind: "prop",
      prompt: "A scuffed subway route map with colored lines and station labels.", roles },
  ]);
  const source = Object.freeze({ runId: `${"1".repeat(64)}-${"2".repeat(64)}`, briefs: Object.freeze([
    { id: "old-map", entityId: "route-map", kind: "prop",
      prompt: "A subway route map with colored transit lines and station labels.", roles, bytes: new Uint8Array([3]) },
    { id: "old-nurse", entityId: "night-nurse", kind: "character-placeholder",
      prompt: "A night shift nurse in practical scrubs with a dark weathered coat.", roles, bytes: new Uint8Array([2]) },
    { id: "old-student", entityId: "student-backpack", kind: "character-placeholder",
      prompt: "A young adult student wearing a worn backpack and practical shoes.", roles, bytes: new Uint8Array([1]) },
  ]) });
  const first = matchReusablePrototypeAssets(targets, [source]);
  const second = matchReusablePrototypeAssets(targets, [source]);
  assert.deepEqual(first?.matches.map(({ targetBriefId, sourceBriefId, bytes }) =>
    [targetBriefId, sourceBriefId, [...bytes]]), [
    ["new-student", "old-student", [1]], ["new-nurse", "old-nurse", [2]], ["new-map", "old-map", [3]],
  ]);
  assert.deepEqual(second, first);
  source.briefs[0].bytes[0] = 9;
  assert.equal(first.matches[2].bytes[0], 3);
});

test("historical asset reuse deduplicates identical verified bytes but rejects ambiguous content", () => {
  const target = [{ id: "new-console", entityId: "control-console", kind: "prop",
    prompt: "A metal control console with colored buttons.", roles: ["visual"] }];
  const run = (digit, overrides = {}) => ({ runId: `${digit.repeat(64)}-${digit.repeat(64)}`, briefs: [{
    id: "old-console", entityId: "control-console", kind: "prop",
    prompt: "A metal control console with colored buttons.", roles: ["visual"], bytes: new Uint8Array([1]), ...overrides,
  }] });
  assert.equal(matchReusablePrototypeAssets(target, [run("1"), run("2")]).sourceRunId,
    `${"1".repeat(64)}-${"1".repeat(64)}`);
  assert.equal(matchReusablePrototypeAssets(target, [run("1"), run("2", { bytes: new Uint8Array([2]) })]), null);
  assert.equal(matchReusablePrototypeAssets(target, [run("1", { kind: "character-placeholder" })]), null);
  assert.equal(matchReusablePrototypeAssets([...target, { ...target[0], id: "second-console" }], [run("1")]), null);
});

test("Creator discovery configuration delegates exactly one read-only Marble list operation", async () => {
  let providers = 0;
  let requests = 0;
  const configuration = createR12WorldDiscoveryConfiguration({ dependencies: {
    createProvider() { providers += 1; return Object.freeze({ provider: "marble", model: "marble-1.1" }); },
    async listWorlds() {
      requests += 1;
      return { ok: true, worlds: [], counts: { listRequests: 1, creates: 0, polls: 0, worldGets: 0, downloads: 0 } };
    },
  } });
  assert.deepEqual(configuration.summary, {
    provider: "world-labs-marble", operation: "worlds:list", model: "marble-1.1", pageSize: 100,
    status: "SUCCEEDED", sortBy: "created_at", maxRequests: 1, maxCreates: 0, maxPolls: 0,
    maxWorldGets: 0, maxDownloads: 0, creditLimit: 0, usdLimitCents: 0,
  });
  assert.equal(providers, 0);
  const result = await configuration.execute();
  assert.equal(result.ok, true);
  assert.equal(providers, 1);
  assert.equal(requests, 1);
});

function checkpointPng() {
  const crc32 = (bytes) => {
    let crc = 0xffffffff;
    for (const byte of bytes) {
      crc ^= byte;
      for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
    return (crc ^ 0xffffffff) >>> 0;
  };
  const chunk = (type, data) => {
    const typeBytes = new TextEncoder().encode(type);
    const output = new Uint8Array(12 + data.length);
    const view = new DataView(output.buffer);
    view.setUint32(0, data.length, false);
    output.set(typeBytes, 4);
    output.set(data, 8);
    const checked = new Uint8Array(4 + data.length);
    checked.set(typeBytes);
    checked.set(data, 4);
    view.setUint32(8 + data.length, crc32(checked), false);
    return output;
  };
  const header = new Uint8Array(13);
  const headerView = new DataView(header.buffer);
  headerView.setUint32(0, 2, false);
  headerView.setUint32(4, 1, false);
  header.set([8, 2, 0, 0, 0], 8);
  const chunks = [Uint8Array.of(137, 80, 78, 71, 13, 10, 26, 10), chunk("IHDR", header),
    chunk("IDAT", new Uint8Array(deflateSync(new Uint8Array(7)))), chunk("IEND", new Uint8Array())];
  const output = new Uint8Array(chunks.reduce((sum, value) => sum + value.length, 0));
  let offset = 0;
  for (const value of chunks) { output.set(value, offset); offset += value.length; }
  return output;
}

function checkpointGlb() {
  const encoded = new TextEncoder().encode(JSON.stringify({ asset: { version: "2.0" }, scene: 0,
    scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }], meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1 }] }],
    accessors: [{ count: 3 }, { count: 3 }], buffers: [{ byteLength: 4 }] }));
  const jsonLength = Math.ceil(encoded.length / 4) * 4;
  const output = new Uint8Array(12 + 8 + jsonLength + 8 + 4);
  const view = new DataView(output.buffer);
  view.setUint32(0, 0x46546c67, true);
  view.setUint32(4, 2, true);
  view.setUint32(8, output.length, true);
  view.setUint32(12, jsonLength, true);
  view.setUint32(16, 0x4e4f534a, true);
  output.fill(32, 20, 20 + jsonLength);
  output.set(encoded, 20);
  view.setUint32(20 + jsonLength, 4, true);
  view.setUint32(24 + jsonLength, 0x004e4942, true);
  return output;
}

function checkpointBlueprint() {
  return { format: "matrix-oasis.scene-blueprint", formatVersion: "0.1.0",
    scene: { id: "checkpoint-room", contentVersion: "1", title: "Checkpoint Room",
      environmentPrompt: "A bounded neutral room.", visualStylePrompt: "Readable prototype materials." },
    zones: [{ id: "zone-main", label: "Main", description: "Central zone" }],
    assetBriefs: [{ id: "asset-environment", kind: "environment", prompt: "Neutral room", entityId: null,
      roles: ["visual", "collider"] }], placements: [],
    nodeBindings: [{ nodeId: "node-entry", zoneId: "zone-main", visiblePlacementIds: [] }] };
}

test("Creator recovery transaction publishes and reuses one verified three-asset Marble cache", async (t) => {
  const reserved = await mkdtemp(path.join(temporaryRoot, "matrix-oasis-r12-recovery-test-"));
  await rm(reserved, { recursive: true, force: true });
  t.after(() => rm(reserved, { recursive: true, force: true }));
  let recoveries = 0;
  const recovered = Object.freeze({
    ok: true,
    worldPrompt: "A generic connected interior.",
    panoramaBytes: new Uint8Array([1, 2, 3]),
    colliderBytes: new Uint8Array([4, 5, 6]),
    spzBytes: new Uint8Array([7, 8, 9]),
    metricScaleFactor: 1.25,
    groundPlaneOffset: -0.5,
    worldSource: "get-world-recovery",
    counts: Object.freeze({ creates: 0, polls: 0, worldGets: 1, downloads: 3 }),
  });
  const dependencies = {
    createProvider() { return Object.freeze({ marker: true }); },
    async recover(provider, worldId) {
      recoveries += 1;
      assert.equal(provider.marker, true);
      assert.equal(worldId, "world-recovery-test");
      return recovered;
    },
    async materialize({ plan, recovered: cached }) {
      assert.equal(plan.marker, "plan");
      assert.deepEqual([...cached.panoramaBytes], [1, 2, 3]);
      assert.deepEqual([...cached.colliderBytes], [4, 5, 6]);
      assert.deepEqual([...cached.spzBytes], [7, 8, 9]);
      assert.equal(cached.metricScaleFactor, 1.25);
      assert.equal(cached.groundPlaneOffset, -0.5);
      assert.equal(cached.worldIdSha256, promptHash("world-recovery-test"));
      assert.equal(cached.worldPromptSha256, promptHash(recovered.worldPrompt));
      return Object.freeze({ ok: true, marker: "materialized" });
    },
  };
  const first = await createR12RecoveryConfiguration({
    worldId: "world-recovery-test", rootPath: reserved, dependencies,
  });
  assert.equal((await first.execute()).ok, true);
  assert.equal(first.isReady(), true);
  assert.equal((await first.materialize({ marker: "plan" })).marker, "materialized");
  assert.equal(recoveries, 1);

  const second = await createR12RecoveryConfiguration({
    worldId: "world-recovery-test", rootPath: reserved,
    dependencies: { ...dependencies, async recover() { assert.fail("verified cache must avoid another recovery request"); } },
  });
  assert.equal((await second.execute()).ok, true);
  assert.equal(second.isReady(), true);
  assert.equal(recoveries, 1);
  assert.deepEqual((await readdir(reserved)).sort(), ["assets", "recovery-cache.json"]);

  const cacheOnly = await createR12CachedRecoveryConfiguration({
    rootPath: reserved,
    dependencies: { materialize: dependencies.materialize },
  });
  assert.equal(cacheOnly.summary.worldIdSha256, promptHash("world-recovery-test"));
  assert.deepEqual({ ...cacheOnly.summary, worldIdSha256: undefined }, {
    model: "marble-1.1", worldIdSha256: undefined,
    maxCreates: 0, maxPolls: 0, maxWorldGets: 0, maxDownloads: 0, creditLimit: 0, usdLimitCents: 0,
  });
  assert.equal(cacheOnly.isReady(), false);
  assert.equal((await cacheOnly.execute()).ok, true);
  assert.equal(cacheOnly.isReady(), true);
  assert.equal(await cacheOnly.matches({ environmentPromptSha256: promptHash(recovered.worldPrompt) }), true);
  assert.equal(await cacheOnly.matches({ environmentPromptSha256: `sha256:${"0".repeat(64)}` }), false);
  assert.equal((await cacheOnly.materialize({ marker: "plan" })).marker, "materialized");
  assert.equal(recoveries, 1);
});

async function unusedLoopbackPort() {
  const server = http.createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return port;
}

async function waitForRun(origin, cookie, runId, status) {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const response = await fetch(`${origin}/api/runs/${runId}`, { headers: { origin, cookie } });
    const body = await response.json();
    if (body.run?.status === status) return body.run;
    if (body.run?.status === "failed") assert.fail(JSON.stringify(body.run.diagnostics));
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail(`run did not reach ${status}`);
}

function qualificationBlueprint() {
  const briefs = [
    { id: "environment", kind: "environment", entityId: null },
    ...Array.from({ length: 3 }, (_, index) => ({ id: `prop-${index}`, kind: "prop", entityId: `entity-prop-${index}` })),
    ...Array.from({ length: 3 }, (_, index) => ({ id: `character-${index}`, kind: "character-placeholder", entityId: `entity-character-${index}` })),
  ];
  return JSON.stringify({
    zones: [{ id: "zone-a" }, { id: "zone-b" }],
    assetBriefs: briefs,
    placements: briefs.map((brief, index) => ({ id: `placement-${index}`, assetBriefId: brief.id })),
  });
}

function qualificationRuntime() {
  return JSON.stringify({
    nodes: Array.from({ length: 7 }, (_, nodeIndex) => ({
      actions: Array.from({ length: nodeIndex === 0 ? 9 : 1 }, (_, actionIndex) => ({ id: `action-${nodeIndex}-${actionIndex}` })),
    })),
    endings: Array.from({ length: 3 }, (_, index) => ({ id: `ending-${index}` })),
  });
}

test("qualification inputs are exact committed natural language and the generic profile", async () => {
  const parsed = parseR12CallArguments(qualificationArgs());
  const input = await readR12CallInputs(parsed);
  assert.equal(input.profile, R12_LAST_TRAIN_ACCEPTANCE_PROFILE);
  assert.equal(input.prompt.trim().startsWith("{"), false);
  for (const forbidden of ["matrix-oasis.", "entryNodeId", "formatVersion", "sha256:"]) {
    assert.equal(input.prompt.includes(forbidden), false);
  }
  assert.match(input.prompt, /environment placement must remain visible in every story state/u);
  assert.match(input.prompt, /actual variable conditions and ordered effects allow all three endings/u);
  assert.equal(parsed.spatialRunRoot, `${parsed.prototypeRunRoot}-spatial`);
});

test("call and preview arguments reject traversal, wrong roots, duplicates and expanded forms", () => {
  const valid = qualificationArgs();
  for (const args of [[], valid.slice(0, 4), [...valid, "--extra", "value"],
    ["--prompt-file", valid[1], "--prompt-file", valid[1], "--run-root", valid[5]],
    ["--prompt-file", valid[1], "--profile", valid[3], "--run-root", path.join(path.parse(temporaryRoot).root, "outside", "run")]]) {
    assert.throws(() => parseR12CallArguments(args), { code: "R12_QUALIFICATION_INTERNAL_ERROR" });
  }
  assert.deepEqual(parseR12PreviewArguments(["--run-root", valid[5]]), {
    prototypeRunRoot: valid[5], spatialRunRoot: `${valid[5]}-spatial`,
  });
  assert.throws(() => parseR12PreviewArguments(["--run-root", `${valid[5]}-spatial`]), /R12_HOST_ARGUMENT_INVALID/u);
});

test("qualification command only verifies an already Creator-published run", async () => {
  const args = qualificationArgs();
  assert.deepEqual(parseR12QualificationArguments(args), parseR12CallArguments(args));
  assert.throws(() => parseR12QualificationArguments(["--phase", "model", ...args]),
    /R12_QUALIFICATION_INTERNAL_ERROR/u);
  const calls = [];
  const result = await runR12QualificationVerification({ args, dependencies: {
    async verify(parsed) {
      calls.push("verify");
      assert.equal(parsed.prototypeRunRoot, qualificationArgs()[5]);
      return { ok: true, evidence: { source: "live-provider" } };
    },
  } });
  assert.deepEqual(result, { ok: true, evidence: { source: "live-provider" } });
  assert.deepEqual(calls, ["verify"]);
});

test("qualification accepts only the current live Creator result with the exact prompt and profile", async () => {
  const prompt = "Natural language";
  const runId = `${"a".repeat(64)}-${"b".repeat(64)}`;
  const sceneBlueprintJson = qualificationBlueprint();
  const runtimeGamePackJson = qualificationRuntime();
  const loaded = {
    runId,
    promptSha256: promptHash(prompt),
    model: "gpt-5.6-luna",
    qualificationEvidence: {
      source: "live-provider",
      sceneBlueprintJson,
      runtimeGamePackJson,
      runtimeReceiptJson: "receipt",
    },
  };
  const dependencies = {
    async readInputs() { return { prompt, profile: R12_LAST_TRAIN_ACCEPTANCE_PROFILE }; },
    async recoverSpatial() { return { currentRunId: runId, runs: [{ runId }] }; },
    async loadSpatial() { return loaded; },
    async analyze() { return { ok: true, evidence: { declaredEndingCount: 3, reachableEndingCount: 3,
      allEndingsReachable: true, hasReachableLoop: true, hasReachableDeadlock: false } }; },
    services: {}, recoverPrototypeRuns() {}, assemblePrototypeScene() {},
    assemblePrototypeSpatialScene() {}, canonicalizeJsonValue() {},
  };
  const result = await verifyR12CreatorPublishedQualification(parseR12CallArguments(qualificationArgs()), dependencies);
  assert.equal(result.ok, true);
  assert.deepEqual({ source: result.evidence.source, nodes: result.evidence.nodeCount,
    actions: result.evidence.actionCount, props: result.evidence.propCount,
    characters: result.evidence.characterPlaceholderCount, endings: result.evidence.reachableEndingCount,
    loop: result.evidence.hasReachableLoop },
  { source: "live-provider", nodes: 7, actions: 15, props: 3, characters: 3, endings: 3, loop: true });

  const cached = await verifyR12CreatorPublishedQualification(parseR12CallArguments(qualificationArgs()), {
    ...dependencies, async loadSpatial() { return { ...loaded,
      qualificationEvidence: { ...loaded.qualificationEvidence, source: "verified-cache" } }; },
  });
  assert.deepEqual(cached.diagnostics.map((item) => item.code), ["R12_CREATOR_RESULT_NOT_LIVE"]);
  const drifted = await verifyR12CreatorPublishedQualification(parseR12CallArguments(qualificationArgs()), {
    ...dependencies, async loadSpatial() { return { ...loaded, promptSha256: `sha256:${"c".repeat(64)}` }; },
  });
  assert.deepEqual(drifted.diagnostics.map((item) => item.code), ["R12_CREATOR_PROMPT_MISMATCH"]);
});

test("qualification source has no provider, credential, generation or publication capability", async () => {
  const source = await readFile(new URL("../scripts/qualify-r12.mjs", import.meta.url), "utf8");
  for (const forbidden of ["createR12LiveSteps", "generatePrototype", "createMarbleWorldProvider",
    "createMeshyTextTo3DProvider", "publishPrototype", "publishSpatial", "process.env", "fetch("]) {
    assert.equal(source.includes(forbidden), false, forbidden);
  }
  assert.equal(source.includes("verifyR12CreatorPublishedQualification"), true);
});

test("live R12 entrypoint is bounded, spatial-first and has no case-specific runtime branch", async () => {
  const source = await readFile(new URL("../scripts/preview-r12.mjs", import.meta.url), "utf8");
  for (const required of ["materializePrototypeEnvironmentWithSpatialSource", "maxDownloads: 3",
    "maxPollAttempts: 180", "materializePrototypeSpatialEnvironmentFromSource",
    "matrix-oasis.prototype-assembly/2", "matrix-oasis.prototype-environment/2",
    "R12_ENVIRONMENT_PLAN_OPTIONS", "publishSpatialPrototypeRun", "profile: \"r12\""]) {
    assert.equal(source.includes(required), true, required);
  }
  for (const forbidden of ["last-train", "student", "nurse", "commuter", "ticket", "stopped station clock"]) {
    assert.equal(source.toLowerCase().includes(forbidden), false, forbidden);
  }
});

test("fake providers execute the real R12 live-step composition without network or credentials", async () => {
  const calls = [];
  const briefs = Array.from({ length: 6 }, (_, index) => ({
    id: `brief-${index}`, kind: index < 3 ? "character-placeholder" : "prop", prompt: `Neutral asset ${index}`,
  }));
  const artifacts = {
    authoringGamePackJson: "authoring",
    sceneBlueprintJson: JSON.stringify({ assetBriefs: [{ id: "environment", kind: "environment" }, ...briefs] }),
    runtimeGamePackJson: "runtime",
    runtimeReceiptJson: "receipt",
    generationReportJson: "generation-report",
  };
  const prototypeRunRoot = path.join(temporaryRoot, "r12-fake-prototype");
  const spatialRunRoot = path.join(temporaryRoot, "r12-fake-spatial");
  const runId = `${"a".repeat(64)}-${"b".repeat(64)}`;
  const steps = createR12LiveSteps({ prototypeRunRoot, spatialRunRoot, godot: null, recovery: {
    isReady: () => true,
    async matches() { return true; },
    async materialize() {
      calls.push("recovered-environment");
      return { ok: true,
        environment: { canonicalBundleJson: "environment", canonicalReportJson: "environment-report", files: [] },
        spatialSource: { canonicalBundleJson: "source", canonicalReportJson: "source-report", files: [] } };
    },
  }, dependencies: {
    createSpatialPrototypeOperations: () => ({ async findCache() { return { ok: false }; }, async launch() { return { ok: true }; },
      async recover() { return { currentRunId: null, runs: [] }; }, async stopLaunch() {} }),
    modelConfiguration: () => ({}), createModelProvider: () => ({}), secret: () => "fixture",
    async generatePrototype(_request, _provider, options) {
      calls.push("generate"); assert.equal(options.acceptanceProfile, R12_LAST_TRAIN_ACCEPTANCE_PROFILE);
      return { ok: true, artifacts };
    },
    planPrototypeEnvironment() { return { ok: true, plan: { blueprint: { canonicalSha256: `sha256:${"c".repeat(64)}` }, environmentPrompt: "Neutral environment" } }; },
    createMarbleWorldProvider: () => ({}),
    async materializePrototypeEnvironmentWithSpatialSource() { assert.fail("verified recovery must bypass Marble generation"); },
    async planPrototypeAssets(input) {
      calls.push("plan-assets");
      assert.deepEqual(Object.keys(input), [
        "authoringGamePackJson", "sceneBlueprintJson", "runtimeGamePackJson", "runtimeReceiptJson",
      ]);
      assert.equal(input.generationReportJson, undefined);
      return { ok: true, plan: { blueprint: { assetBriefs: briefs } } };
    },
    createMeshyTextTo3DProvider: () => ({}),
    async acquireMeshy() { calls.push("assets"); return { ok: true, acquired: new Map() }; },
    readEnvironmentAssets: async () => ({ environmentAssets: new Map(), environmentTexture: new Uint8Array() }),
    async materializePrototypeAssetBundle() { calls.push("normalize"); return { ok: true, canonicalBundleJson: "assets", files: [] }; },
    async materializePrototypeSpatialEnvironmentFromSource() {
      calls.push("spatialize"); return { ok: true, canonicalBundleJson: "spatial", canonicalReportJson: "spatial-report", files: [] };
    },
    async loadAcquisitionCheckpoint() { return null; },
    async saveAcquisitionCheckpoint() { calls.push("checkpoint"); return { key: "fixture" }; },
    async publishPrototypeRun() { calls.push("prototype"); return { runId }; },
    async publishSpatialPrototypeRun() { calls.push("overlay"); return { runId }; },
  } });
  const operations = createR12PrototypeOperations(steps);
  const generated = await operations.generate({ prompt: "Neutral" });
  const description = await operations.describeAssets({ artifacts: generated.artifacts });
  assert.equal(description.briefs.length, 6);
  const acquired = await operations.acquire({ artifacts: generated.artifacts,
    approval: { blueprintSha256: description.blueprintSha256 }, onStage: (stage) => calls.push(stage) });
  assert.equal(acquired.ok, true);
  assert.deepEqual(await operations.publish({ prompt: "Neutral", artifacts: generated.artifacts, acquisition: acquired }), { ok: true, runId });
  assert.deepEqual(calls, ["generate", "plan-assets", "recovered-environment", "plan-assets", "assets", "normalizing", "normalize", "spatializing", "spatialize", "checkpoint", "prototype", "overlay"]);
});

test("Creator checkpoints a paid Marble materialization before Meshy and reuses it without another provider call", async () => {
  const blueprintSha256 = `sha256:${"9".repeat(64)}`;
  const artifacts = { sceneBlueprintJson: "blueprint" };
  const approval = { blueprintSha256, marble: { recovered: false } };
  const materialization = Object.freeze({ ok: true,
    environment: Object.freeze({ canonicalBundleJson: "environment", canonicalReportJson: "environment-report", files: Object.freeze([]) }),
    spatialSource: Object.freeze({ canonicalBundleJson: "source", canonicalReportJson: "source-report", files: Object.freeze([]) }) });
  let cached = null;
  let providerCalls = 0;
  let saves = 0;
  const steps = createR12LiveSteps({
    prototypeRunRoot: path.join(temporaryRoot, "r12-environment-stage-prototype"),
    spatialRunRoot: path.join(temporaryRoot, "r12-environment-stage-spatial"),
    godot: null,
    dependencies: {
      createSpatialPrototypeOperations: () => ({ async findCache() { return { ok: false }; }, async launch() { return { ok: true }; },
        async recover() { return { currentRunId: null, runs: [] }; }, async stopLaunch() {} }),
      planPrototypeEnvironment() { return { ok: true, plan: { blueprint: { canonicalSha256: blueprintSha256 } } }; },
      async loadEnvironmentCheckpoint({ blueprintSha256: supplied }) {
        assert.equal(supplied, blueprintSha256);
        return cached;
      },
      async saveEnvironmentCheckpoint({ blueprintSha256: supplied, materialization: suppliedMaterialization }) {
        assert.equal(supplied, blueprintSha256);
        assert.equal(suppliedMaterialization, materialization);
        saves += 1;
        cached = suppliedMaterialization;
        return { key: "environment-stage" };
      },
      createMarbleWorldProvider() { providerCalls += 1; return {}; },
      async materializePrototypeEnvironmentWithSpatialSource() { return materialization; },
      secret() { return "fixture"; },
    },
  });
  assert.equal((await steps.acquireEnvironment({ artifacts, approval })).ok, true);
  assert.equal((await steps.acquireEnvironment({ artifacts, approval: {
    blueprintSha256, marble: { recovered: true },
  } })).ok, true);
  assert.deepEqual({ providerCalls, saves }, { providerCalls: 1, saves: 1 });
});

test("Creator environment checkpoint is transactionally revalidated and rejects byte drift", async (t) => {
  const prototypeRunRoot = await mkdtemp(path.join(temporaryRoot, "matrix-oasis-r12-environment-checkpoint-"));
  const spatialRunRoot = `${prototypeRunRoot}-spatial`;
  t.after(async () => {
    await rm(prototypeRunRoot, { recursive: true, force: true });
    await rm(spatialRunRoot, { recursive: true, force: true });
  });
  const sceneBlueprintJson = canonicalizeJsonValue(checkpointBlueprint());
  const planned = planPrototypeEnvironment(sceneBlueprintJson, { profile: "matrix-oasis.prototype-environment/2" });
  assert.equal(planned.ok, true);
  const materialization = materializeRecoveredPrototypeEnvironmentWithSpatialSource({ plan: planned, recovered: {
    panoramaBytes: checkpointPng(), colliderBytes: checkpointGlb(),
    spzBytes: Uint8Array.of(0x53, 0x50, 0x5a, 0x01), metricScaleFactor: 1.25,
    groundPlaneOffset: -0.1, worldSource: "get-world-recovery",
    worldPromptSha256: planned.plan.environmentPromptSha256,
    counts: { creates: 0, polls: 0, worldGets: 1, downloads: 3 },
  } });
  assert.equal(materialization.ok, true);
  let providerCalls = 0;
  const dependencies = {
    createSpatialPrototypeOperations: () => ({ async findCache() { return { ok: false }; }, async launch() { return { ok: true }; },
      async recover() { return { currentRunId: null, runs: [] }; }, async stopLaunch() {} }),
    planPrototypeEnvironment() { return planned; },
    createMarbleWorldProvider() { providerCalls += 1; return {}; },
    async materializePrototypeEnvironmentWithSpatialSource() { return materialization; },
    secret() { return "fixture"; },
  };
  const artifacts = { sceneBlueprintJson };
  const approval = { blueprintSha256: planned.plan.blueprint.canonicalSha256, marble: { recovered: false } };
  const first = createR12LiveSteps({ prototypeRunRoot, spatialRunRoot, godot: null, dependencies });
  const acquired = await first.acquireEnvironment({ artifacts, approval });
  assert.equal(acquired.ok, true);
  assert.equal(providerCalls, 1);

  const second = createR12LiveSteps({ prototypeRunRoot, spatialRunRoot, godot: null, dependencies: {
    ...dependencies,
    createMarbleWorldProvider() { assert.fail("verified checkpoint must bypass Marble"); },
    secret() { assert.fail("verified checkpoint must not read Marble credentials"); },
  } });
  const reused = await second.acquireEnvironment({ artifacts,
    approval: { blueprintSha256: planned.plan.blueprint.canonicalSha256, marble: { recovered: true } } });
  assert.equal(reused.ok, true);
  assert.equal(reused.environment.canonicalBundleJson, materialization.environment.canonicalBundleJson);
  const checkpointRoot = path.join(prototypeRunRoot, "environment-checkpoints");
  const [key] = await readdir(checkpointRoot);
  await writeFile(path.join(checkpointRoot, key, "spatial-source", "files", "assets", "environment.spz"),
    Uint8Array.of(0x53, 0x50, 0x5a, 0x02));
  await assert.rejects(() => second.acquireEnvironment({ artifacts,
    approval: { blueprintSha256: planned.plan.blueprint.canonicalSha256, marble: { recovered: true } } }),
  { message: "R12_ENVIRONMENT_CHECKPOINT_INVALID" });
});

test("Creator cache recovery rejects semantically deadlocked R12 artifacts before reuse", async () => {
  const runId = `${"1".repeat(64)}-${"2".repeat(64)}`;
  let accepted = false;
  const steps = createR12LiveSteps({
    prototypeRunRoot: path.join(temporaryRoot, "r12-cache-gate-prototype"),
    spatialRunRoot: path.join(temporaryRoot, "r12-cache-gate-spatial"),
    godot: null,
    dependencies: {
      createSpatialPrototypeOperations: () => ({
        async findCache() { return { ok: true, runId }; },
        async launch() { return { ok: true }; },
        async recover() { return { currentRunId: runId, runs: [{ runId, model: "fixture" }] }; },
        async stopLaunch() {},
      }),
      async loadVerifiedSpatialPrototypeRun() {
        return { qualificationEvidence: {
          source: "live-provider",
          sceneBlueprintJson: "blueprint",
          runtimeGamePackJson: "runtime",
          runtimeReceiptJson: "receipt",
        } };
      },
      async analyzeR12QualificationCandidate() { return { ok: accepted }; },
      async loadAcquisitionCheckpoint() { return null; },
    },
  });
  const input = { prompt: "Neutral prompt", promptSha256: `sha256:${"3".repeat(64)}`, model: "fixture" };
  assert.deepEqual(await steps.findCache(input), { ok: false });
  assert.deepEqual(await steps.recover(), { currentRunId: null, runs: [] });
  accepted = true;
  assert.deepEqual(await steps.findCache(input), { ok: true, runId });
  assert.deepEqual(await steps.recover(), { currentRunId: runId, runs: [{ runId, model: "fixture" }] });
});

test("Creator rejects an incompatible acquisition checkpoint before scanning an obsolete spatial cache", async () => {
  let spatialLookups = 0;
  const steps = createR12LiveSteps({
    prototypeRunRoot: path.join(temporaryRoot, "r12-incompatible-checkpoint-prototype"),
    spatialRunRoot: path.join(temporaryRoot, "r12-incompatible-checkpoint-spatial"),
    godot: null,
    dependencies: {
      createSpatialPrototypeOperations: () => ({
        async findCache() { spatialLookups += 1; throw new Error("obsolete-spatial-cache-must-not-be-read"); },
        async launch() { return { ok: true }; },
        async recover() { return { currentRunId: null, runs: [] }; },
        async stopLaunch() {},
      }),
      async loadAcquisitionCheckpoint() {
        return { artifacts: {
          sceneBlueprintJson: "obsolete-blueprint",
          runtimeGamePackJson: "obsolete-runtime",
          runtimeReceiptJson: "obsolete-receipt",
        } };
      },
      async analyzeR12QualificationCandidate() { return { ok: false }; },
    },
  });
  const input = { prompt: "Neutral prompt", promptSha256: `sha256:${"4".repeat(64)}`, model: "fixture" };
  assert.deepEqual(await steps.findCache(input), { ok: false });
  assert.equal(spatialLookups, 0);
});

test("Creator HTTP approvals drive the same live Marble path through operation-snapshot fallback", async (t) => {
  const marbleCalls = [];
  let marbleOrigin;
  const marbleServer = http.createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    marbleCalls.push({ method: request.method, url: request.url, body: Buffer.concat(chunks).toString("utf8") });
    response.setHeader("content-type", "application/json");
    if (request.url === "/marble/v1/worlds:generate") {
      response.end(JSON.stringify({ done: false, operation_id: "operation-creator", error: null, metadata: null, response: null }));
    } else if (request.url === "/marble/v1/operations/operation-creator") {
      response.end(JSON.stringify({ done: true, operation_id: "operation-creator", error: null,
        metadata: { world_id: "world-creator" }, response: { id: "world-creator", model: null,
          assets: { imagery: { pano_url: `${marbleOrigin}/assets/panorama.png` },
            mesh: { collider_mesh_url: `${marbleOrigin}/assets/collider.glb` },
            splats: { spz_urls: { full_res: `${marbleOrigin}/assets/environment.spz` },
              semantics_metadata: { metric_scale_factor: 1.5, ground_plane_offset: -0.25 } } } } }));
    } else if (request.url === "/marble/v1/worlds/world-creator") {
      response.writeHead(404); response.end();
    } else if (request.url === "/assets/panorama.png") {
      response.setHeader("content-type", "image/png"); response.end(new Uint8Array([1, 2, 3]));
    } else if (request.url === "/assets/collider.glb") {
      response.setHeader("content-type", "model/gltf-binary"); response.end(new Uint8Array([4, 5, 6]));
    } else if (request.url === "/assets/environment.spz") {
      response.setHeader("content-type", "application/octet-stream"); response.end(new Uint8Array([7, 8, 9]));
    } else { response.writeHead(404); response.end(); }
  });
  await new Promise((resolve) => marbleServer.listen(0, "127.0.0.1", resolve));
  marbleOrigin = `http://127.0.0.1:${marbleServer.address().port}`;
  t.after(() => new Promise((resolve, reject) => marbleServer.close((error) => error ? reject(error) : resolve())));

  const environmentPrompt = "A coherent bounded two-space transit prototype with continuous walkable floors and clear placement capacity.";
  const briefs = Array.from({ length: 6 }, (_, index) => ({
    id: `brief-${index}`, kind: index < 3 ? "character-placeholder" : "prop", prompt: `Neutral asset ${index}`,
  }));
  const artifacts = { authoringGamePackJson: "authoring", sceneBlueprintJson: "blueprint",
    runtimeGamePackJson: "runtime", runtimeReceiptJson: "receipt", generationReportJson: "generation" };
  let acquiredWorldSource = null;
  const runId = `${"d".repeat(64)}-${"e".repeat(64)}`;
  const steps = createR12LiveSteps({ prototypeRunRoot: path.join(temporaryRoot, "r12-creator-http"),
    spatialRunRoot: path.join(temporaryRoot, "r12-creator-http-spatial"), godot: null, dependencies: {
      createSpatialPrototypeOperations: () => ({ async findCache() { return { ok: false }; }, async launch() { return { ok: true }; },
        async recover() { return { currentRunId: null, runs: [] }; }, async stopLaunch() {} }),
      modelConfiguration: () => ({}), createModelProvider: () => ({}), secret: () => "example-credential",
      async generatePrototype() { return { ok: true, artifacts }; },
      async planPrototypeAssets() { return { ok: true, plan: { blueprint: { assetBriefs: briefs } } }; },
      planPrototypeEnvironment() { return { ok: true, plan: { blueprint: { canonicalSha256: `sha256:${"f".repeat(64)}` }, environmentPrompt } }; },
      createMarbleWorldProvider: () => createMarbleWorldProvider({ endpoint: `${marbleOrigin}/marble/v1`, apiKey: "example-credential",
        allowedAssetHosts: ["127.0.0.1"], timeoutMs: 1000, pollIntervalMs: 0 }),
      async materializePrototypeEnvironmentWithSpatialSource({ plan }, provider) {
        const acquired = await acquireMarbleEnvironmentWithSpatialSource(provider, plan.plan.environmentPrompt);
        if (!acquired.ok) return acquired;
        acquiredWorldSource = acquired.worldSource;
        return { ok: true, environment: { canonicalBundleJson: "environment", canonicalReportJson: "environment-report", files: [] },
          spatialSource: { canonicalBundleJson: "source", canonicalReportJson: "source-report", files: [] } };
      },
      createMeshyTextTo3DProvider: () => ({}), async acquireMeshy() { return { ok: true, acquired: new Map() }; },
      readEnvironmentAssets: async () => ({ environmentAssets: new Map(), environmentTexture: new Uint8Array() }),
      async materializePrototypeAssetBundle() { return { ok: true, canonicalBundleJson: "assets", files: [] }; },
      async materializePrototypeSpatialEnvironmentFromSource() { return { ok: true, canonicalBundleJson: "spatial", canonicalReportJson: "spatial-report", files: [] }; },
      async loadAcquisitionCheckpoint(input) {
        assert.equal(input.prompt, "Build a generic bounded multi-space prototype.");
        return null;
      },
      async loadEnvironmentCheckpoint() { return null; },
      async saveEnvironmentCheckpoint({ blueprintSha256, materialization }) {
        assert.equal(blueprintSha256, `sha256:${"f".repeat(64)}`);
        assert.equal(materialization.ok, true);
        return { key: "environment-fixture" };
      },
      async savePendingGeneration() { return { key: "pending-fixture" }; },
      async recoverPendingGenerations() { return []; },
      async discardPendingGeneration() {},
      async saveAcquisitionCheckpoint() { return { key: "fixture" }; },
      async publishPrototypeRun() { return { runId }; }, async publishSpatialPrototypeRun() { return { runId }; },
    } });
  const port = await unusedLoopbackPort();
  const host = createPrototypeHost({ profile: "r12", port, configuration: { endpointHost: "api.openai.com",
    model: "gpt-5.6-luna", modelReady: true, assetsReady: true, godotReady: false },
  operations: createR12PrototypeOperations(steps) });
  await host.start(); t.after(() => host.stop());
  const origin = `http://127.0.0.1:${port}`;
  const bootstrapResponse = await fetch(`${origin}/api/bootstrap`);
  const cookie = bootstrapResponse.headers.get("set-cookie").split(";")[0];
  const request = async (route, body) => {
    const response = await fetch(`${origin}${route}`, { method: "POST", headers: { origin, cookie, "content-type": "application/json" },
      body: JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  };
  const created = await request("/api/runs", { prompt: "Build a generic bounded multi-space prototype." });
  assert.equal(created.status, 201);
  await request(`/api/runs/${created.body.run.id}/approve-model`, { approvalHash: created.body.run.modelApproval.approvalHash });
  const assetApproval = await waitForRun(origin, cookie, created.body.run.id, "awaiting_asset_approval");
  assert.equal(assetApproval.assetApproval.marble.environmentPrompt, environmentPrompt);
  assert.deepEqual({ creates: assetApproval.assetApproval.marble.maxCreates, polls: assetApproval.assetApproval.marble.maxPolls,
    downloads: assetApproval.assetApproval.marble.maxDownloads }, { creates: 1, polls: 180, downloads: 3 });
  await request(`/api/runs/${created.body.run.id}/approve-assets`, { approvalHash: assetApproval.assetApproval.approvalHash });
  const ready = await waitForRun(origin, cookie, created.body.run.id, "ready");
  assert.equal(ready.resultRunId, runId);
  assert.equal(acquiredWorldSource, "operation-response");
  assert.deepEqual(marbleCalls.map(({ method, url }) => [method, url]), [
    ["POST", "/marble/v1/worlds:generate"], ["GET", "/marble/v1/operations/operation-creator"],
    ["GET", "/marble/v1/worlds/world-creator"], ["GET", "/assets/panorama.png"],
    ["GET", "/assets/collider.glb"], ["GET", "/assets/environment.spz"],
  ]);
  assert.equal(JSON.parse(marbleCalls[0].body).world_prompt.text_prompt, environmentPrompt);
});

test("Creator resumes a verified post-acquisition checkpoint without provider credentials or another paid call", async (t) => {
  const prototypeRunRoot = await mkdtemp(path.join(temporaryRoot, "matrix-oasis-r12-checkpoint-"));
  const spatialRunRoot = `${prototypeRunRoot}-spatial`;
  t.after(async () => {
    await rm(prototypeRunRoot, { recursive: true, force: true });
    await rm(spatialRunRoot, { recursive: true, force: true });
  });
  const prompt = "Build a bounded neutral multi-space prototype without writing this prompt to disk.";
  const model = "gpt-5.6-luna";
  const artifacts = Object.freeze({
    authoringGamePackJson: "{\"authoring\":true}",
    sceneBlueprintJson: "{\"blueprint\":true}",
    runtimeGamePackJson: "{\"runtime\":true}",
    runtimeReceiptJson: "{\"receipt\":true}",
    generationReportJson: JSON.stringify({ model }),
  });
  const output = (pathValue, values) => Object.freeze({ path: pathValue, bytes: Uint8Array.from(values) });
  const acquisition = Object.freeze({
    ok: true,
    normalized: Object.freeze({ ok: true, materialization: Object.freeze({
      canonicalBundleJson: "{\"assetBundle\":true}",
      files: Object.freeze([output("assets/prop.glb", [1, 2, 3])]),
    }) }),
    environment: Object.freeze({ ok: true, environment: Object.freeze({
      canonicalBundleJson: "{\"environmentBundle\":true}",
      canonicalReportJson: "{\"environmentReport\":true}",
      files: Object.freeze([
        output("assets/environment-panorama.png", [4, 5, 6]),
        output("assets/environment-collider.glb", [7, 8, 9]),
      ]),
    }) }),
    spatial: Object.freeze({ ok: true, materialization: Object.freeze({
      canonicalBundleJson: "{\"spatialBundle\":true}",
      canonicalReportJson: "{\"spatialReport\":true}",
      files: Object.freeze([output("assets/environment.compressed.ply", [10, 11, 12])]),
    }) }),
  });
  const spatialOperations = () => ({
    async findCache() { return { ok: false }; },
    async launch() { return { ok: true }; },
    async recover() { return { currentRunId: null, runs: [] }; },
    async stopLaunch() {},
  });
  const failedSteps = createR12LiveSteps({ prototypeRunRoot, spatialRunRoot, godot: null, dependencies: {
    createSpatialPrototypeOperations: spatialOperations,
    async publishPrototypeRun() { throw new Error("dynamic-local-publish-failure"); },
    async publishSpatialPrototypeRun() { assert.fail("spatial publication must not run after prototype failure"); },
  } });
  const failed = await createR12PrototypeOperations(failedSteps).publish({ prompt, artifacts, acquisition });
  assert.deepEqual(failed.diagnostics.map(({ code }) => code), ["R12_HOST_ASSEMBLY_FAILED"]);
  assert.equal(JSON.stringify(failed).includes("dynamic-local-publish-failure"), false);

  const checkpointKeys = await readdir(path.join(prototypeRunRoot, "checkpoints"));
  assert.equal(checkpointKeys.length, 1);
  const checkpointRoot = path.join(prototypeRunRoot, "checkpoints", checkpointKeys[0]);
  const manifest = JSON.parse(await readFile(path.join(checkpointRoot, "checkpoint.json"), "utf8"));
  assert.equal(manifest.promptSha256, promptHash(prompt));
  assert.equal(manifest.model, model);
  assert.equal(manifest.blueprintSha256, promptHash(artifacts.sceneBlueprintJson));
  const stored = await Promise.all(manifest.files.map(({ path: relative }) =>
    readFile(path.join(checkpointRoot, ...relative.split("/")))));
  assert.equal(Buffer.concat(stored).includes(Buffer.from(prompt, "utf8")), false);
  assert.equal(Buffer.concat(stored).includes(Buffer.from("dynamic-local-publish-failure", "utf8")), false);

  const runId = `${"9".repeat(64)}-${"8".repeat(64)}`;
  const localCalls = [];
  let credentialReads = 0;
  const resumedSteps = createR12LiveSteps({ prototypeRunRoot, spatialRunRoot, godot: null, dependencies: {
    createSpatialPrototypeOperations: spatialOperations,
    async analyzeR12QualificationCandidate() { return { ok: true }; },
    secret() { credentialReads += 1; throw new Error("credential-must-not-be-read"); },
    async publishPrototypeRun(input) {
      localCalls.push("prototype");
      assert.equal(input.source, "verified-cache");
      assert.equal(input.prompt, prompt);
      assert.equal(input.activateCurrent, false);
      assert.equal(input.reuseExisting, true);
      assert.equal(input.environmentMaterialization.canonicalReportJson, "{\"environmentReport\":true}");
      assert.deepEqual([...input.assetMaterialization.files[0].bytes], [1, 2, 3]);
      return { runId };
    },
    async publishSpatialPrototypeRun(input) {
      localCalls.push("spatial");
      assert.equal(input.prototypeRunId, runId);
      assert.deepEqual([...input.spatialMaterialization.files[0].bytes], [10, 11, 12]);
      return { runId };
    },
  } });
  const port = await unusedLoopbackPort();
  const origin = `http://127.0.0.1:${port}`;
  const host = createPrototypeHost({ profile: "r12", port, configuration: {
    endpointHost: "api.openai.com", model, modelReady: false, assetsReady: false, godotReady: false,
  }, operations: createR12PrototypeOperations(resumedSteps) });
  await host.start();
  const bootstrapResponse = await fetch(`${origin}/api/bootstrap`, { headers: { origin } });
  const cookie = bootstrapResponse.headers.get("set-cookie").split(";")[0];
  const response = await fetch(`${origin}/api/runs`, { method: "POST", headers: {
    origin, cookie, "content-type": "application/json",
  }, body: JSON.stringify({ prompt }) });
  const body = await response.json();
  await host.stop();
  assert.equal(response.status, 201);
  assert.equal(body.run.status, "ready");
  assert.equal(body.run.cacheHit, true);
  assert.equal(body.run.resultRunId, runId);
  assert.deepEqual(localCalls, ["prototype", "spatial"]);
  assert.equal(credentialReads, 0);

  const resumedAfterPrototypePublication = createR12LiveSteps({
    prototypeRunRoot, spatialRunRoot, godot: null, dependencies: {
      createSpatialPrototypeOperations: spatialOperations,
      async analyzeR12QualificationCandidate() { return { ok: true }; },
      secret() { credentialReads += 1; throw new Error("credential-must-not-be-read"); },
      async findVerifiedPrototypeRun() { return { ok: true, runId }; },
      async publishPrototypeRun() { assert.fail("verified prototype must not be republished"); },
      async publishSpatialPrototypeRun(input) {
        assert.equal(input.prototypeRunId, runId);
        return { runId };
      },
    },
  });
  assert.deepEqual(await resumedAfterPrototypePublication.findCache({
    prompt, promptSha256: promptHash(prompt), model,
  }), { ok: true, runId });
  assert.equal(credentialReads, 0);

  const incompatibleSpatialSteps = createR12LiveSteps({
    prototypeRunRoot, spatialRunRoot, godot: null, dependencies: {
      createSpatialPrototypeOperations: spatialOperations,
      async analyzeR12QualificationCandidate() { return { ok: true }; },
      async findVerifiedPrototypeRun() { return { ok: true, runId }; },
      async publishPrototypeRun() { assert.fail("verified prototype must not be republished"); },
      async publishSpatialPrototypeRun() {
        const error = new Error("SPATIAL_CACHE_ASSEMBLY_REJECTED");
        error.code = "SPATIAL_CACHE_ASSEMBLY_REJECTED";
        throw error;
      },
    },
  });
  assert.deepEqual(await incompatibleSpatialSteps.findCache({
    prompt, promptSha256: promptHash(prompt), model,
  }), { ok: false });
  assert.equal(credentialReads, 0);

  const tamperedPath = path.join(checkpointRoot, "environment", "prototype-environment-report.json");
  await writeFile(tamperedPath, "{\"tampered\":true}", "utf8");
  const guardedSteps = createR12LiveSteps({ prototypeRunRoot, spatialRunRoot, godot: null, dependencies: {
    createSpatialPrototypeOperations: spatialOperations,
    secret() { credentialReads += 1; throw new Error("credential-must-not-be-read"); },
    async publishPrototypeRun() { assert.fail("tampered checkpoint must not publish"); },
    async publishSpatialPrototypeRun() { assert.fail("tampered checkpoint must not publish"); },
  } });
  await assert.rejects(() => guardedSteps.findCache({ prompt, promptSha256: promptHash(prompt), model }), (error) => {
    assert.equal(error.message, "R12_CHECKPOINT_INVALID");
    assert.equal(String(error).includes("tampered"), false);
    return true;
  });
  assert.equal(credentialReads, 0);
});

test("Creator persists and restores the asset-approval checkpoint without the original prompt", async (t) => {
  const prototypeRunRoot = await mkdtemp(path.join(temporaryRoot, "matrix-oasis-r12-pending-parent-"));
  await rm(prototypeRunRoot, { recursive: true, force: true });
  const spatialRunRoot = `${prototypeRunRoot}-spatial`;
  t.after(() => rm(prototypeRunRoot, { recursive: true, force: true }));
  t.after(() => rm(spatialRunRoot, { recursive: true, force: true }));
  const prompt = "This original qualification prompt must remain memory-only.";
  const model = "gpt-5.6-luna";
  const artifacts = Object.freeze({
    authoringGamePackJson: "{\"authoring\":true}",
    sceneBlueprintJson: "{\"blueprint\":true}",
    runtimeGamePackJson: "{\"runtime\":true}",
    runtimeReceiptJson: "{\"receipt\":true}",
    generationReportJson: JSON.stringify({ model }),
  });
  const approval = Object.freeze({
    blueprintSha256: `sha256:${"b".repeat(64)}`,
    marble: Object.freeze({ model: "marble-1.1", environmentPrompt: "A bounded connected interior.", recovered: true,
      maxCreates: 0, maxPolls: 0, maxDownloads: 0, creditLimit: 0, usdLimitCents: 0 }),
    meshy: Object.freeze({ model: "meshy-6", briefs: Object.freeze([
      Object.freeze({ id: "asset-prop", kind: "prop", prompt: "A neutral inspection prop." }),
    ]), maxTasks: 2, creditLimit: 30 }),
  });
  const spatialOperations = () => ({
    async findCache() { return { ok: false }; },
    async launch() { return { ok: true }; },
    async recover() { return { currentRunId: null, runs: [] }; },
    async stopLaunch() {},
  });
  const dependencies = {
    createSpatialPrototypeOperations: spatialOperations,
    async analyzeR12QualificationCandidate() { return { ok: true }; },
    async planPrototypeAssets() { return { ok: true, plan: { blueprint: { assetBriefs: [
      { id: "asset-environment", kind: "environment", prompt: "A bounded connected interior.", entityId: null,
        roles: ["visual", "collider"] },
      { id: "asset-prop", kind: "prop", prompt: "A neutral inspection prop.", entityId: "entity-prop", roles: ["visual"] },
    ] } } }; },
    planPrototypeEnvironment() { return { ok: true, plan: { blueprint: { canonicalSha256: approval.blueprintSha256 },
      environmentPrompt: approval.marble.environmentPrompt } }; },
    async loadEnvironmentCheckpoint() { return { ok: true }; },
    async findReusableAssets() { return { ok: false }; },
    secret() { assert.fail("pending recovery must not read provider credentials"); },
  };
  const first = createR12LiveSteps({ prototypeRunRoot, spatialRunRoot, godot: null, dependencies });
  await first.persistPending({ promptSha256: promptHash(prompt), model, artifacts, approval });
  const pendingRoot = path.join(prototypeRunRoot, "pending-generations");
  const keys = await readdir(pendingRoot);
  assert.equal(keys.length, 1);
  const storedNames = ["pending.json", "asset-approval.json", "prototype/authoring-game-pack.json",
    "prototype/scene-blueprint.json", "prototype/runtime-game-pack.json", "prototype/runtime-receipt.json",
    "prototype/generation-report.json"];
  const stored = await Promise.all(storedNames.map((name) => readFile(path.join(pendingRoot, keys[0], ...name.split("/")))));
  assert.equal(Buffer.concat(stored).includes(Buffer.from(prompt, "utf8")), false);

  const second = createR12LiveSteps({ prototypeRunRoot, spatialRunRoot, godot: null, dependencies });
  const recovered = await second.recoverPending();
  assert.equal(recovered.runs.length, 1);
  assert.equal(recovered.runs[0].promptSha256, promptHash(prompt));
  assert.equal(recovered.runs[0].model, model);
  assert.deepEqual(recovered.runs[0].approval, approval);
  assert.deepEqual(recovered.runs[0].artifacts, artifacts);

  await writeFile(path.join(pendingRoot, keys[0], "prototype", "scene-blueprint.json"), "{\"tampered\":true}", "utf8");
  await assert.rejects(() => second.recoverPending(), { message: "R12_PENDING_GENERATION_INVALID" });
  await second.discardPending({ promptSha256: promptHash(prompt), model });
  assert.deepEqual(await readdir(pendingRoot), []);
});

test("asset planning failure is reported before any Marble or Meshy provider call", async () => {
  const calls = [];
  const artifacts = {
    authoringGamePackJson: "authoring", sceneBlueprintJson: "blueprint",
    runtimeGamePackJson: "runtime", runtimeReceiptJson: "receipt", generationReportJson: "report",
  };
  const steps = createR12LiveSteps({
    prototypeRunRoot: path.join(temporaryRoot, "r12-preflight-prototype"),
    spatialRunRoot: path.join(temporaryRoot, "r12-preflight-spatial"), godot: null,
    dependencies: {
      createSpatialPrototypeOperations: () => ({ async findCache() { return { ok: false }; }, async launch() { return { ok: true }; },
        async recover() { return { currentRunId: null, runs: [] }; }, async stopLaunch() {} }),
      async planPrototypeAssets(input) {
        calls.push("plan-assets");
        assert.deepEqual(Object.keys(input), [
          "authoringGamePackJson", "sceneBlueprintJson", "runtimeGamePackJson", "runtimeReceiptJson",
        ]);
        return { ok: false, diagnostics: [{ code: "PROTOTYPE_ASSET_PLAN_INPUT_INVALID", path: "" }] };
      },
      planPrototypeEnvironment() { calls.push("environment-plan"); throw new Error("must not run"); },
      createMarbleWorldProvider() { calls.push("marble"); throw new Error("must not run"); },
      createMeshyTextTo3DProvider() { calls.push("meshy"); throw new Error("must not run"); },
    },
  });
  const result = await steps.describeAssets({ artifacts });
  assert.equal(result.ok, false);
  assert.deepEqual(result.diagnostics.map(({ code }) => code), ["PROTOTYPE_ASSET_PLAN_INPUT_INVALID"]);
  assert.deepEqual(calls, ["plan-assets"]);
});

test("Creator asset reuse reads no Meshy credential and constructs no provider", async () => {
  const calls = [];
  const materialization = Object.freeze({
    canonicalBundleJson: "{\"bundle\":true}",
    files: Object.freeze([
      { path: "assets/environment-template.glb", bytes: new Uint8Array([4, 5]) },
      { path: "assets/asset-prop-visual.glb", bytes: new Uint8Array([1, 2, 3]) },
    ]),
  });
  const plan = { blueprint: { assetBriefs: [{ id: "asset-prop", entityId: "entity-prop", kind: "prop",
    prompt: "A neutral prop", roles: ["visual"] }] } };
  const artifacts = { authoringGamePackJson: "a", sceneBlueprintJson: "b",
    runtimeGamePackJson: "c", runtimeReceiptJson: "d", generationReportJson: "e" };
  const steps = createR12LiveSteps({
    prototypeRunRoot: path.join(temporaryRoot, "r12-reused-assets-prototype"),
    spatialRunRoot: path.join(temporaryRoot, "r12-reused-assets-spatial"), godot: null,
    dependencies: {
      createSpatialPrototypeOperations: () => ({ async findCache() { return { ok: false }; }, async launch() { return { ok: true }; },
        async recover() { return { currentRunId: null, runs: [] }; }, async stopLaunch() {} }),
      async planPrototypeAssets() { calls.push("plan"); return { ok: true, plan }; },
      async findReusableAssets({ plan: supplied }) {
        calls.push("reuse"); assert.equal(supplied, plan);
        return { ok: true, acquired: new Map([["asset-prop", new Uint8Array([1, 2, 3])]]), materialization };
      },
      createMeshyTextTo3DProvider() { calls.push("provider"); throw new Error("must not run"); },
      secret() { calls.push("secret"); throw new Error("must not run"); },
      readEnvironmentAssets() { calls.push("environment-assets"); throw new Error("must not run"); },
      materializePrototypeAssetBundle() { calls.push("normalize"); throw new Error("must not run"); },
    },
  });
  const result = await steps.acquireAssets({ artifacts,
    approval: { meshy: { maxTasks: 0, creditLimit: 0 } } });
  assert.equal(result.ok, true);
  assert.equal(result.reused, true);
  assert.deepEqual([...result.acquired.get("asset-prop")], [1, 2, 3]);
  const normalized = await steps.normalizeAssets({ assets: result });
  assert.equal(normalized.materialization, materialization);
  assert.deepEqual(normalized.materialization.files.map(({ path: filePath }) => filePath), [
    "assets/environment-template.glb", "assets/asset-prop-visual.glb",
  ]);
  assert.deepEqual(calls, ["plan", "reuse"]);
});
