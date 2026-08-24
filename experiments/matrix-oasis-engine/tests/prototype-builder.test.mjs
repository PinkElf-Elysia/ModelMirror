import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import {
  PROTOTYPE_BUILDER_MARKER,
  R16_PROTOTYPE_BUILDER_MARKER,
  PrototypeBuilderClient,
  PrototypeBuilderClientError,
} from "../apps/creator-web/src/prototype-builder.ts";
import { prototypeGodotArguments } from "../scripts/preview-prototype.mjs";

const HASH_A = `sha256:${"a".repeat(64)}`;
const HASH_B = `sha256:${"b".repeat(64)}`;
const RESULT_RUN = `${"c".repeat(64)}-${"d".repeat(64)}`;
const QUALIFIED_RUN = "f".repeat(64);

function response(value, status = 200, headers = { "content-type": "application/json" }) {
  return new Response(JSON.stringify(value), { status, headers });
}

function modelApproval() {
  return { endpointHost: "api.openai.com", model: "luna", maxRequests: 3, maxUsdCents: 100,
    prompt: "A neutral room.\nInclude one console.", promptSha256: HASH_A,
    approvalHash: HASH_B, approved: false };
}

function assetApproval(count = 1, maxDownloads = 2, recovered = false, assetsRecovered = false) {
  const briefs = Array.from({ length: count }, (_, index) => ({
    id: `asset-${index}`,
    kind: index % 2 === 0 ? "prop" : "character-placeholder",
    prompt: `A neutral generated asset ${index}.`,
  }));
  return { blueprintSha256: HASH_A, marble: { model: "marble-1.1",
    environmentPrompt: "A quiet room.\nSoft indirect light.", recovered,
    maxCreates: recovered ? 0 : 1, maxPolls: recovered ? 0 : 180,
    maxDownloads: recovered ? 0 : maxDownloads, creditLimit: recovered ? 0 : 1600,
    usdLimitCents: recovered ? 0 : 150 },
  meshy: { model: "meshy-6", briefs,
    maxTasks: assetsRecovered ? 0 : count * 2, creditLimit: assetsRecovered ? 0 : count * 30 },
  approvalHash: HASH_B, approved: false };
}

function recoveryApproval(overrides = {}) {
  return { model: "marble-1.1", worldIdSha256: HASH_A, maxCreates: 0, maxPolls: 0, maxWorldGets: 0,
    maxDownloads: 0, creditLimit: 0, usdLimitCents: 0, status: "awaiting_approval",
    diagnostics: [], approvalHash: `sha256:${"f".repeat(64)}`, approved: false, ...overrides };
}

function worldDiscovery(overrides = {}) {
  return { provider: "world-labs-marble", operation: "worlds:list", model: "marble-1.1", pageSize: 100,
    status: "SUCCEEDED", sortBy: "created_at", maxRequests: 1, maxCreates: 0, maxPolls: 0, maxWorldGets: 0,
    maxDownloads: 0, creditLimit: 0, usdLimitCents: 0, statusState: "awaiting_approval", diagnostics: [], candidates: [], recovery: null,
    approvalHash: `sha256:${"e".repeat(64)}`, approved: false, ...overrides };
}

function run(overrides = {}) {
  return { id: "r10-run-1", status: "awaiting_model_approval", cacheHit: false, diagnostics: [],
    modelApproval: modelApproval(), assetApproval: null, resultRunId: null, ...overrides };
}

function qualification(overrides = {}) {
  return { profile: "matrix-oasis.creator-solved-evidence/1", cacheLevel: "qualified", subphase: null,
    attempt: 1, reusedQualification: true, solutionSha256: HASH_B,
    evidence: { runId: "e".repeat(64), attempt: 1, replayCount: 4, screenshotCount: 8,
      videoCount: 1, sampleCount: 300, medianFrameMicros: 20_000, medianFpsMilli: 50_000 }, ...overrides };
}

function r16Run(overrides = {}) {
  return { ...run({ status: "ready", cacheHit: true, modelApproval: null, assetApproval: null,
    resultRunId: QUALIFIED_RUN }), qualification: qualification(), ...overrides };
}

test("client uses only relative same-origin requests and rebuilds the fixed public surface", async () => {
  const calls = [];
  const client = new PrototypeBuilderClient(async (input, init) => {
    calls.push({ input, init });
    if (input === "/api/bootstrap") return response({ marker: "MATRIX_OASIS_R10_PROTOTYPE_HOST",
      readiness: { model: true, assets: true, godot: true }, currentRunId: null, runs: [] });
    if (input === "/api/runs") return response({ ok: true, run: run() }, 201);
    if (String(input).endsWith("/approve-model")) return response({ ok: true,
      run: run({ status: "awaiting_asset_approval", modelApproval: null, assetApproval: assetApproval() }) }, 202);
    if (String(input).endsWith("/approve-assets")) return response({ ok: true,
      run: run({ status: "ready", cacheHit: false, modelApproval: null, assetApproval: null, resultRunId: RESULT_RUN }) }, 202);
    if (String(input).endsWith("/launch")) return response({ ok: true, runId: RESULT_RUN }, 202);
    return response({ ok: true, run: run() });
  });
  const bootstrap = await client.bootstrap(); assert.equal(Object.isFrozen(bootstrap), true);
  const created = await client.createRun("A neutral room.");
  assert.equal(created.modelApproval.prompt.includes("\n"), true);
  const waiting = await client.approveModel(created); assert.equal(waiting.assetApproval.marble.environmentPrompt.includes("\n"), true);
  const ready = await client.approveAssets(waiting); assert.equal(await client.launch(ready), RESULT_RUN);
  for (const call of calls) {
    assert.match(String(call.input), /^\/api\//u);
    assert.equal(call.init.credentials, "same-origin");
    assert.equal(call.init.redirect, "error");
    assert.equal(call.init.cache, "no-store");
  }
  assert.deepEqual(Object.keys(ready), ["id", "status", "cacheHit", "diagnostics", "modelApproval", "assetApproval", "resultRunId"]);
});

test("client accepts the exact R16 qualification profile and launches only its 64-hex qualification id", async () => {
  const ready = r16Run();
  const client = new PrototypeBuilderClient(async (input) => {
    if (input === "/api/bootstrap") return response({ marker: "MATRIX_OASIS_R16_PROTOTYPE_HOST",
      readiness: { model: false, assets: false, godot: true }, currentRunId: QUALIFIED_RUN,
      runs: [ready], qualificationProfile: "matrix-oasis.creator-solved-evidence/1" });
    if (String(input).endsWith("/launch")) return response({ ok: true, runId: QUALIFIED_RUN }, 202);
    return response({ ok: true, run: ready });
  });
  const bootstrap = await client.bootstrap();
  assert.equal(bootstrap.marker, "MATRIX_OASIS_R16_PROTOTYPE_HOST");
  assert.equal(bootstrap.qualificationProfile, "matrix-oasis.creator-solved-evidence/1");
  assert.equal(bootstrap.runs[0].qualification?.evidence?.sampleCount, 300);
  assert.equal(await client.launch(bootstrap.runs[0]), QUALIFIED_RUN);
  assert.equal(R16_PROTOTYPE_BUILDER_MARKER, "MATRIX_OASIS_R16_CREATOR_MVP_READY");
});

test("client rejects profile confusion, malformed performance evidence, and old-result ids in R16", async () => {
  const cases = [
    { marker: "MATRIX_OASIS_R10_PROTOTYPE_HOST", readiness: { model: false, assets: false, godot: true },
      currentRunId: QUALIFIED_RUN, runs: [r16Run()], qualificationProfile: "matrix-oasis.creator-solved-evidence/1" },
    { marker: "MATRIX_OASIS_R16_PROTOTYPE_HOST", readiness: { model: false, assets: false, godot: true },
      currentRunId: QUALIFIED_RUN, runs: [r16Run({ qualification: qualification({
        evidence: { ...qualification().evidence, medianFpsMilli: 49_999 },
      }) })], qualificationProfile: "matrix-oasis.creator-solved-evidence/1" },
    { marker: "MATRIX_OASIS_R16_PROTOTYPE_HOST", readiness: { model: false, assets: false, godot: true },
      currentRunId: RESULT_RUN, runs: [r16Run({ resultRunId: RESULT_RUN })],
      qualificationProfile: "matrix-oasis.creator-solved-evidence/1" },
    { marker: "MATRIX_OASIS_R16_PROTOTYPE_HOST", readiness: { model: false, assets: false, godot: true },
      currentRunId: QUALIFIED_RUN, runs: [r16Run({ qualification: null })],
      qualificationProfile: "matrix-oasis.creator-solved-evidence/1" },
  ];
  for (const value of cases) {
    const client = new PrototypeBuilderClient(async () => response(value));
    await assert.rejects(() => client.bootstrap(), PrototypeBuilderClientError);
  }
});

test("client exposes the exact R12 recovery approval without a raw world identifier", async () => {
  const expected = recoveryApproval();
  const client = new PrototypeBuilderClient(async (input) => {
    if (input === "/api/bootstrap") return response({ marker: "MATRIX_OASIS_R10_PROTOTYPE_HOST",
      readiness: { model: true, assets: true, godot: false }, currentRunId: null, runs: [], recovery: expected });
    if (input === "/api/recovery/approve") return response({ ok: true,
      recovery: recoveryApproval({ status: "recovering", approved: true }) }, 202);
    return response({ ok: false, diagnostics: [] }, 404);
  });
  const bootstrap = await client.bootstrap();
  assert.equal(bootstrap.recovery?.worldIdSha256, HASH_A);
  assert.equal(bootstrap.recovery?.maxCreates, 0);
  assert.equal(bootstrap.recovery?.maxWorldGets, 0);
  assert.equal(bootstrap.recovery?.maxDownloads, 0);
  const approved = await client.approveRecovery(bootstrap.recovery);
  assert.equal(approved.status, "recovering");
  assert.equal(approved.approved, true);
  assert.equal(JSON.stringify(bootstrap).includes("705fd38b"), false);
});

test("client exposes one exact read-only worlds:list approval and only hashed candidates", async () => {
  const candidate = { worldIdSha256: HASH_A, promptSha256: HASH_B, createdAt: "2026-08-14T12:00:00Z",
    updatedAt: "2026-08-14T12:30:00Z", model: "marble-1.1",
    assets: { panorama: true, collider: true, spatialSource: true } };
  const client = new PrototypeBuilderClient(async (input) => {
    if (input === "/api/bootstrap") return response({ marker: "MATRIX_OASIS_R10_PROTOTYPE_HOST",
      readiness: { model: true, assets: true, godot: false }, currentRunId: null, runs: [], recovery: null,
      worldDiscovery: worldDiscovery() });
    if (input === "/api/world-discovery/approve") return response({ ok: true,
      worldDiscovery: worldDiscovery({ statusState: "querying", approved: true }) }, 202);
    return response({ ok: false, diagnostics: [] }, 404);
  });
  const bootstrap = await client.bootstrap();
  assert.equal(bootstrap.worldDiscovery?.maxRequests, 1);
  assert.equal(bootstrap.worldDiscovery?.maxCreates, 0);
  const approved = await client.approveWorldDiscovery(bootstrap.worldDiscovery);
  assert.equal(approved.statusState, "querying");
  const completedClient = new PrototypeBuilderClient(async () => response({ marker: "MATRIX_OASIS_R10_PROTOTYPE_HOST",
    readiness: { model: true, assets: true, godot: false }, currentRunId: null, runs: [], recovery: null,
    worldDiscovery: worldDiscovery({ statusState: "ready", approved: true, candidates: [candidate] }) }));
  const completed = await completedClient.bootstrap();
  assert.deepEqual(completed.worldDiscovery?.candidates[0], candidate);
  assert.equal(JSON.stringify(completed).includes("world-private"), false);
});

test("client binds a separate Get World approval to one hashed discovery candidate", async () => {
  const candidate = { worldIdSha256: HASH_A, promptSha256: HASH_B, createdAt: "2026-08-14T12:00:00Z",
    updatedAt: "2026-08-14T12:30:00Z", model: "marble-1.1",
    assets: { panorama: true, collider: true, spatialSource: true } };
  const recovery = { worldIdSha256: HASH_A, maxCreates: 0, maxPolls: 0, maxWorldGets: 1, maxDownloads: 3,
    creditLimit: 0, usdLimitCents: 0, status: "awaiting_approval", diagnostics: [], approvalHash: HASH_B, approved: false };
  const client = new PrototypeBuilderClient(async (input) => {
    if (input === "/api/world-discovery/prepare-recovery") return response({ ok: true,
      worldDiscovery: worldDiscovery({ statusState: "ready", approved: true, candidates: [candidate], recovery }) });
    if (input === "/api/world-discovery/approve-recovery") return response({ ok: true,
      worldDiscovery: worldDiscovery({ statusState: "ready", approved: true, candidates: [candidate],
        recovery: { ...recovery, status: "recovering", approved: true } }) }, 202);
    return response({ ok: false, diagnostics: [] }, 404);
  });
  const prepared = await client.prepareWorldRecovery(candidate);
  assert.equal(prepared.recovery?.maxWorldGets, 1);
  assert.equal(prepared.recovery?.maxDownloads, 3);
  const approved = await client.approveWorldRecovery(prepared);
  assert.equal(approved.recovery?.status, "recovering");
});

test("malformed, oversized, and dynamic failure responses collapse to static client errors", async () => {
  const sentinel = ["PRIVATE", "RESPONSE", "SENTINEL"].join("-");
  const cases = [
    response({ marker: "MATRIX_OASIS_R10_PROTOTYPE_HOST", readiness: { model: true, assets: true, godot: true }, currentRunId: null, runs: [], [sentinel]: true }),
    response({ ok: false, diagnostics: [{ phase: "host", severity: "error", code: "HOST_FAILED", path: "", message: sentinel }] }, 400),
    new Response("x".repeat(128 * 1024 + 1), { headers: { "content-type": "application/json" } }),
    new Response("{}", { headers: { "content-type": "text/plain" } }),
  ];
  for (const candidate of cases) {
    const client = new PrototypeBuilderClient(async () => candidate);
    await assert.rejects(() => client.bootstrap(), (error) => {
      assert.equal(error instanceof PrototypeBuilderClientError, true);
      assert.equal(error.code, "PROTOTYPE_BUILDER_CLIENT_ERROR");
      assert.equal(String(error).includes(sentinel), false);
      return true;
    });
  }
});

test("client accepts six approval briefs but rejects seven", async () => {
  for (const [count, accepted] of [[6, true], [7, false]]) {
    const client = new PrototypeBuilderClient(async (input) => {
      if (String(input).endsWith("/approve-model")) {
        return response({ ok: true, run: run({ status: "awaiting_asset_approval",
          modelApproval: null, assetApproval: assetApproval(count) }) }, 202);
      }
      return response({ ok: true, run: run() });
    });
    const created = run();
    if (accepted) {
      const waiting = await client.approveModel(created);
      assert.equal(waiting.assetApproval?.meshy.briefs.length, 6);
      assert.equal(waiting.assetApproval?.meshy.maxTasks, 12);
      assert.equal(waiting.assetApproval?.meshy.creditLimit, 180);
    } else {
      await assert.rejects(() => client.approveModel(created), PrototypeBuilderClientError);
    }
  }
});

test("client accepts the exact R10 and R12 Marble download budgets only", async () => {
  for (const [maxDownloads, accepted] of [[2, true], [3, true], [1, false], [4, false]]) {
    const client = new PrototypeBuilderClient(async () => response({ ok: true,
      run: run({ status: "awaiting_asset_approval", modelApproval: null,
        assetApproval: assetApproval(6, maxDownloads) }) }, 202));
    if (accepted) {
      const waiting = await client.approveModel(run());
      assert.equal(waiting.assetApproval?.marble.maxDownloads, maxDownloads);
    } else {
      await assert.rejects(() => client.approveModel(run()), PrototypeBuilderClientError);
    }
  }
});

test("client accepts only the exact zero-cost Marble recovery approval", async () => {
  const client = new PrototypeBuilderClient(async () => response({ ok: true,
    run: run({ status: "awaiting_asset_approval", modelApproval: null,
      assetApproval: assetApproval(6, 3, true) }) }, 202));
  const waiting = await client.approveModel(run());
  assert.equal(waiting.assetApproval?.marble.recovered, true);
  assert.equal(waiting.assetApproval?.marble.maxCreates, 0);
  assert.equal(waiting.assetApproval?.marble.maxDownloads, 0);

  for (const invalid of [
    { ...assetApproval(6, 3, true), marble: { ...assetApproval(6, 3, true).marble, maxDownloads: 3 } },
    { ...assetApproval(6, 3, false), marble: { ...assetApproval(6, 3, false).marble, recovered: true } },
  ]) {
    const rejected = new PrototypeBuilderClient(async () => response({ ok: true,
      run: run({ status: "awaiting_asset_approval", modelApproval: null, assetApproval: invalid }) }, 202));
    await assert.rejects(() => rejected.approveModel(run()), PrototypeBuilderClientError);
  }
});

test("client accepts only the paired zero-task historical Meshy reuse budget", async () => {
  const client = new PrototypeBuilderClient(async () => response({ ok: true,
    run: run({ status: "awaiting_asset_approval", modelApproval: null,
      assetApproval: assetApproval(6, 3, false, true) }) }, 202));
  const waiting = await client.approveModel(run());
  assert.equal(waiting.assetApproval?.meshy.briefs.length, 6);
  assert.equal(waiting.assetApproval?.meshy.maxTasks, 0);
  assert.equal(waiting.assetApproval?.meshy.creditLimit, 0);
  for (const invalid of [
    { ...assetApproval(6, 3, false, true), meshy: { ...assetApproval(6, 3, false, true).meshy, creditLimit: 180 } },
    { ...assetApproval(6), meshy: { ...assetApproval(6).meshy, maxTasks: 0 } },
  ]) {
    const rejected = new PrototypeBuilderClient(async () => response({ ok: true,
      run: run({ status: "awaiting_asset_approval", modelApproval: null, assetApproval: invalid }) }, 202));
    await assert.rejects(() => rejected.approveModel(run()), PrototypeBuilderClientError);
  }
});

test("Godot launch arguments bind exactly one verified run to the R10 wrapper", () => {
  const fixtureRoot = path.resolve(path.parse(process.cwd()).root, "tmp");
  const projectRoot = path.join(fixtureRoot, "r10-project");
  const runDirectory = path.join(fixtureRoot, "r10-run");
  const args = prototypeGodotArguments({ projectRoot, runDirectory, smoke: true });
  assert.deepEqual(args, ["--headless", "--path", projectRoot, "res://prototype_builder/prototype_lab.tscn", "--",
    `--matrix-oasis-runtime-pack=${path.join(runDirectory, "runtime-game-pack.json")}`,
    `--matrix-oasis-runtime-receipt=${path.join(runDirectory, "runtime-receipt.json")}`,
    `--matrix-oasis-scene-pack=${path.join(runDirectory, "scene-pack.json")}`,
    `--matrix-oasis-environment-bundle=${path.join(runDirectory, "prototype-environment-bundle.json")}`,
    "--matrix-oasis-prototype-smoke"]);
  assert.equal(Object.isFrozen(args), true);
  assert.throws(() => prototypeGodotArguments({ projectRoot: "relative", runDirectory }), /PROTOTYPE_HOST_GODOT_ARGUMENT_INVALID/u);
});

test("Creator and Godot wrapper preserve old modes and expose the bounded R10 UX", async () => {
  const app = await readFile(new URL("../apps/creator-web/src/App.tsx", import.meta.url), "utf8");
  const client = await readFile(new URL("../apps/creator-web/src/prototype-builder.ts", import.meta.url), "utf8");
  const css = await readFile(new URL("../apps/creator-web/src/styles.css", import.meta.url), "utf8");
  const lab = await readFile(new URL("../apps/runtime-godot/prototype_builder/prototype_lab.gd", import.meta.url), "utf8");
  const loader = await readFile(new URL("../apps/runtime-godot/prototype_builder/environment_bundle_loader.gd", import.meta.url), "utf8");
  assert.equal(PROTOTYPE_BUILDER_MARKER, "MATRIX_OASIS_R10_PROTOTYPE_BUILDER");
  assert.equal(R16_PROTOTYPE_BUILDER_MARKER, "MATRIX_OASIS_R16_CREATOR_MVP_READY");
  for (const marker of ["MATRIX_OASIS_R0_ISOLATED_SHELL", "MATRIX_OASIS_R2_REFERENCE_SIMULATOR",
    "MATRIX_OASIS_R3_RUNTIME_PARITY"]) assert.equal(app.includes(marker), true);
  assert.equal(client.includes(PROTOTYPE_BUILDER_MARKER), true);
  assert.match(app, /run\.assetApproval\.marble\.maxDownloads/u);
  for (const text of ["Prototype Builder", "Runtime / Parity", "当前可运行原型", "审批 1 / 2", "审批 2 / 2",
    "上一份可运行原型未改变", "已验证缓存复用", "首次完整资格", "旧缓存待资格",
    "空间分析、求解、Godot 物理复验", "R16 初版资格", "aria-live=\"polite\""]) assert.equal(app.includes(text), true);
  assert.match(app, /candidate\.runs\.find\(\(item\) => !TERMINAL_RUN_STATES\.has\(item\.status\)\)/u);
  assert.match(app, /setTimeout\(poll, 1_000\)/u); assert.match(app, /clearTimeout\(timeoutId\)/u);
  assert.equal(app.includes("dangerouslySetInnerHTML"), false);
  for (const forbidden of ["API-Key", "任务 ID", "JSON 编辑", "gradient", "backdrop-filter", "animation:"])
    assert.equal(`${app}\n${client}\n${css}`.includes(forbidden), false);
  assert.match(css, /@media \(max-width: 640px\)/u); assert.match(css, /min-width: 320px/u);
  assert.match(css, /\.qualification-summary[\s\S]*max-height: 18rem[\s\S]*overflow-y: auto/u);
  assert.match(css, /\.upload-summary ul[\s\S]*max-height: 18rem[\s\S]*overflow-y: auto/u);
  assert.equal(lab.includes("PanoramaSkyMaterial"), true); assert.equal(lab.includes('get_node_or_null("Visual")'), true);
  assert.equal(lab.includes(".visible = false"), true); assert.equal(lab.includes("MATRIX_OASIS_R10_PROTOTYPE_READY"), true);
  assert.equal(lab.includes("TARGET_FLOOR_SPAN_METERS := 30.0"), true);
  assert.equal(lab.includes("_align_environment_collider"), true);
  assert.equal(lab.includes("R10SafetyFloor"), true);
  assert.equal(lab.includes("set_synthetic_move_input(Vector2.RIGHT)"), true);
  assert.equal(lab.includes("PACK_GODOT_PROTOTYPE_MOVEMENT_SMOKE_FAILED"), true);
  for (const forbidden of ["HTTPClient", "HTTPRequest", "OS.execute", "FileAccess.WRITE", "store_string"])
    assert.equal(`${lab}\n${loader}`.includes(forbidden), false);
});
