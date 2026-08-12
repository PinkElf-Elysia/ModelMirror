import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import {
  PROTOTYPE_BUILDER_MARKER,
  PrototypeBuilderClient,
  PrototypeBuilderClientError,
} from "../apps/creator-web/src/prototype-builder.ts";
import { prototypeGodotArguments } from "../scripts/preview-prototype.mjs";

const HASH_A = `sha256:${"a".repeat(64)}`;
const HASH_B = `sha256:${"b".repeat(64)}`;
const RESULT_RUN = `${"c".repeat(64)}-${"d".repeat(64)}`;

function response(value, status = 200, headers = { "content-type": "application/json" }) {
  return new Response(JSON.stringify(value), { status, headers });
}

function modelApproval() {
  return { endpointHost: "api.openai.com", model: "luna", maxRequests: 3, maxUsdCents: 100,
    prompt: "A neutral room.\nInclude one console.", promptSha256: HASH_A,
    approvalHash: HASH_B, approved: false };
}

function assetApproval() {
  return { blueprintSha256: HASH_A, marble: { model: "marble-1.1",
    environmentPrompt: "A quiet room.\nSoft indirect light.", maxCreates: 1, maxPolls: 180,
    maxDownloads: 2, creditLimit: 1600, usdLimitCents: 150 },
  meshy: { model: "meshy-6", briefs: [{ id: "asset-prop", kind: "prop", prompt: "A plain console." }],
    maxTasks: 2, creditLimit: 30 }, approvalHash: HASH_B, approved: false };
}

function run(overrides = {}) {
  return { id: "r10-run-1", status: "awaiting_model_approval", cacheHit: false, diagnostics: [],
    modelApproval: modelApproval(), assetApproval: null, resultRunId: null, ...overrides };
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
  for (const marker of ["MATRIX_OASIS_R0_ISOLATED_SHELL", "MATRIX_OASIS_R2_REFERENCE_SIMULATOR",
    "MATRIX_OASIS_R3_RUNTIME_PARITY"]) assert.equal(app.includes(marker), true);
  assert.equal(client.includes(PROTOTYPE_BUILDER_MARKER), true);
  for (const text of ["Prototype Builder", "Runtime / Parity", "当前可运行原型", "审批 1 / 2", "审批 2 / 2",
    "上一份可运行原型未改变", "已复用真实资格缓存", "aria-live=\"polite\""]) assert.equal(app.includes(text), true);
  assert.match(app, /candidate\.runs\.find\(\(item\) => !TERMINAL_RUN_STATES\.has\(item\.status\)\)/u);
  assert.match(app, /setTimeout\(poll, 1_000\)/u); assert.match(app, /clearTimeout\(timeoutId\)/u);
  assert.equal(app.includes("dangerouslySetInnerHTML"), false);
  for (const forbidden of ["API-Key", "任务 ID", "JSON 编辑", "gradient", "backdrop-filter", "animation:"])
    assert.equal(`${app}\n${client}\n${css}`.includes(forbidden), false);
  assert.match(css, /@media \(max-width: 640px\)/u); assert.match(css, /min-width: 320px/u);
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
