import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import { lstat, mkdir, mkdtemp, open, readFile, readdir, realpath, rename, rm, rmdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { PassThrough } from "node:stream";
import test from "node:test";
import { solvePrototypeSpatialLayout } from "@matrix-oasis/prototype-spatial-solver";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { buildSpatialVerificationFixture } from "../packages/prototype-spatial-verifier/tests/fixture.mjs";
import {
  findVerifiedSolvedSpatialPrototypeRun,
  loadVerifiedSolvedSpatialPrototypeRun,
  publishSolvedSpatialPrototypeRun,
  recoverSolvedSpatialPrototypeRuns,
} from "../scripts/lib/solved-spatial-cache-core.mjs";
import {
  R14_PREVIEW_READY_MARKER,
  R14_PREVIEW_TRACE_MARKER,
  createR14PreviewOperations,
  r14GodotArguments,
} from "../scripts/lib/r14-preview-core.mjs";
import { parseR14PreviewArguments, R14_PREVIEW_HOST_MARKER } from "../scripts/preview-r14.mjs";

const RUN_ID = `${"a".repeat(64)}-${"b".repeat(64)}`;
const PROMPT_HASH = `sha256:${"c".repeat(64)}`;
const MODEL = "fixture-model";
const encoder = new TextEncoder();
const services = Object.freeze({ lstat, mkdir, mkdtemp, openFile: open, readdir, realpath, rename, rm, rmdir });

function hash(value) { return `sha256:${createHash("sha256").update(value).digest("hex")}`; }

async function solvedFixture() {
  const root = await mkdtemp(path.join(os.tmpdir(), "r14-"));
  const prototypeRunRoot = path.join(root, "p");
  const spatialRunRoot = path.join(root, "x");
  const solvedRunRoot = path.join(root, "s");
  const prototypeRun = path.join(prototypeRunRoot, "runs", RUN_ID);
  await mkdir(prototypeRun, { recursive: true });
  await mkdir(spatialRunRoot);
  const fixture = await buildSpatialVerificationFixture();
  await writeFile(path.join(prototypeRun, "prototype-asset-bundle.json"), fixture.assetBundleJson);
  const spatialAssemblyJson = canonicalizeJsonValue({
    canonicalization: "matrix-oasis.canonical-json/1", format: "matrix-oasis.prototype-spatial-assembly", formatVersion: "0.1.0",
  });
  const facts = JSON.parse(fixture.environmentFactsJson);
  facts.source.analysisTransform = {
    profile: "spatial-assembly-collider-v1", sourceCanonicalSha256: hash(spatialAssemblyJson), eulerOrder: "YXZ",
    root: { translationMm: [0, 0, 0], rotationMilliDegrees: [0, 0, 0] },
    collider: { localTranslationMm: [0, 0, 0], scaleMicros: 1_000_000 },
  };
  const environmentFactsJson = canonicalizeJsonValue(facts);
  const solved = await solvePrototypeSpatialLayout({
    spatialIntentJson: fixture.spatialIntentJson, environmentFactsJson, assetBundleJson: fixture.assetBundleJson,
    runtimeGamePackJson: fixture.runtimeGamePackJson, runtimeReceiptJson: fixture.runtimeReceiptJson,
  });
  assert.equal(solved.ok, true, JSON.stringify(solved));
  const solution = solved.spatialSolution;
  const verification = canonicalizeJsonValue({
    format: "matrix-oasis.prototype-spatial-verification-report", formatVersion: "0.1.0",
    solutionSha256: hash(solved.canonicalSpatialSolutionJson), evidenceSha256: `sha256:${"d".repeat(64)}`,
    verifier: { id: "godot-spatial-solution-verifier", version: "0.1.0-r14", godotVersion: "4.6.3" },
    checks: { placementCount: solution.placements.length, nodeContextCount: solution.nodeContexts.length,
      pathCount: solution.nodeContexts.length, terminalCount: solution.nodeContexts.reduce((sum, item) => sum + item.actionTerminal.actionCount, 0) },
  });
  const sourcePreviewFiles = new Map([
    ["runtime-game-pack.json", encoder.encode(fixture.runtimeGamePackJson)],
    ["runtime-receipt.json", encoder.encode(fixture.runtimeReceiptJson)],
    ["scene-pack.json", encoder.encode("{}")],
    ["spatial-assembly.json", encoder.encode(spatialAssemblyJson)],
    ["assets/environment.compressed.ply", Uint8Array.of(1)],
    ["assets/environment-collider.glb", fixture.environmentColliderBytes],
    ...fixture.assetFiles,
  ]);
  const loadVerifiedSpatialPrototypeRun = async ({ runId }) => {
    assert.equal(runId, RUN_ID);
    return Object.freeze({ runId, promptSha256: PROMPT_HASH, model: MODEL, previewFiles: sourcePreviewFiles });
  };
  const sourceOptions = Object.freeze({ loadVerifiedSpatialPrototypeRun, cacheOptions: Object.freeze({ prototypeRunRoot, temporaryRoot: root }) });
  const common = { runRoot: solvedRunRoot, temporaryRoot: root, sourceOptions, services, canonicalizeJsonValue };
  const artifacts = {
    spatialIntentJson: fixture.spatialIntentJson,
    environmentFactsJson,
    spatialSolutionJson: solved.canonicalSpatialSolutionJson,
    spatialSolutionReportJson: solved.canonicalSpatialSolutionReportJson,
    spatialVerificationReportJson: verification,
  };
  return { root, prototypeRunRoot, spatialRunRoot, solvedRunRoot, common, artifacts, sourcePreviewFiles };
}

test("R14 preview arguments and markers are exact while the old default remains separate", () => {
  const root = path.resolve(os.tmpdir());
  const parsed = parseR14PreviewArguments([
    "--prototype-run-root", path.join(root, "prototype"), "--spatial-run-root", path.join(root, "spatial"),
    "--solved-run-root", path.join(root, "solved"),
  ], root);
  assert.deepEqual({ ...parsed }, {
    prototypeRunRoot: path.join(root, "prototype"), spatialRunRoot: path.join(root, "spatial"), solvedRunRoot: path.join(root, "solved"),
  });
  const args = r14GodotArguments({ projectRoot: path.join(root, "project"), runDirectory: path.join(root, "run"), smoke: true });
  assert.equal(args.includes("res://solved_spatial_prototype/solved_spatial_lab.tscn"), true);
  assert.equal(args.some((item) => item.includes("spatial-solution.json")), true);
  assert.equal(args.some((item) => item.includes("spatial-verification-report.json")), true);
  assert.equal(args.at(-1), "--matrix-oasis-r14-smoke");
  assert.equal(R14_PREVIEW_READY_MARKER, "MATRIX_OASIS_R14_SOLVED_SPATIAL_READY");
  assert.equal(R14_PREVIEW_TRACE_MARKER, "MATRIX_OASIS_R14_SPATIAL_TRACE_JSON:");
  assert.equal(R14_PREVIEW_HOST_MARKER, "MATRIX_OASIS_R14_SOLVED_SPATIAL_HOST");
});

test("publishes, recovers, and loads an isolated solved overlay with full identity revalidation", async () => {
  const value = await solvedFixture();
  try {
    const badVerification = JSON.parse(value.artifacts.spatialVerificationReportJson);
    badVerification.checks.pathCount += 1;
    await assert.rejects(
      publishSolvedSpatialPrototypeRun({ runId: RUN_ID, artifacts: { ...value.artifacts,
        spatialVerificationReportJson: canonicalizeJsonValue(badVerification) }, ...value.common }),
      { code: "SOLVED_SPATIAL_CACHE_INPUT_INVALID" },
    );
    const published = await publishSolvedSpatialPrototypeRun({ runId: RUN_ID, artifacts: value.artifacts, ...value.common });
    assert.equal(published.runId, RUN_ID);
    assert.match(published.solutionSha256, /^sha256:[0-9a-f]{64}$/u);
    assert.equal(published.files, 6);
    const solutionHex = published.solutionSha256.slice(7);
    assert.deepEqual((await readdir(path.join(value.solvedRunRoot, "solved-runs", RUN_ID, solutionHex))).sort(), [
      "environment-facts.json", "run-report.json", "spatial-intent.json", "spatial-solution-report.json",
      "spatial-solution.json", "spatial-verification-report.json",
    ]);
    const recovered = await recoverSolvedSpatialPrototypeRuns(value.common);
    assert.equal(recovered.currentRunId, RUN_ID);
    assert.equal(recovered.currentSolutionSha256, published.solutionSha256);
    assert.deepEqual(await findVerifiedSolvedSpatialPrototypeRun({ promptSha256: PROMPT_HASH, model: MODEL, ...value.common }), { ok: true, runId: RUN_ID });
    const loaded = await loadVerifiedSolvedSpatialPrototypeRun({ runId: RUN_ID, ...value.common });
    assert.equal(loaded.previewFiles.has("spatial-solution.json"), true);
    assert.equal(loaded.previewFiles.has("spatial-verification-report.json"), true);
    assert.equal(loaded.previewFiles.has("assets/environment.compressed.ply"), true);
    const reportPath = path.join(value.solvedRunRoot, "solved-runs", RUN_ID, solutionHex, "spatial-verification-report.json");
    const report = JSON.parse(await readFile(reportPath, "utf8"));
    await writeFile(reportPath, canonicalizeJsonValue({ ...report, solutionSha256: `sha256:${"e".repeat(64)}` }));
    assert.deepEqual(await recoverSolvedSpatialPrototypeRuns(value.common), { currentRunId: null, runs: [], currentSolutionSha256: null });
  } finally { await rm(value.root, { recursive: true, force: true }); }
});

test("R14 operations are offline cache-only and launch only verified copied files", async () => {
  const projectRoot = await mkdtemp(path.join(os.tmpdir(), "matrix-oasis-r14-project-"));
  const temporaryProjectRoot = await mkdtemp(path.join(os.tmpdir(), "matrix-oasis-r14-project-owner-"));
  const child = new EventEmitter(); child.stdout = new PassThrough(); child.stderr = new PassThrough();
  child.exitCode = null; child.signalCode = null; child.kill = () => { child.exitCode = 0; child.emit("exit", 0); };
  const files = new Map([
    ["runtime-game-pack.json", encoder.encode("runtime")], ["runtime-receipt.json", encoder.encode("receipt")],
    ["scene-pack.json", encoder.encode("scene")], ["spatial-assembly.json", encoder.encode("assembly")],
    ["spatial-solution.json", encoder.encode("solution")], ["spatial-verification-report.json", encoder.encode("verification")],
    ["assets/environment-collider.glb", Uint8Array.of(1)], ["assets/environment.compressed.ply", Uint8Array.of(2)],
  ]);
  const calls = { configured: 0, imported: 0, removed: 0 };
  const operations = createR14PreviewOperations({
    prototypeRunRoot: path.join(os.tmpdir(), "prototype"), spatialRunRoot: path.join(os.tmpdir(), "spatial"),
    solvedRunRoot: path.join(os.tmpdir(), "solved"), godot: { command: "godot" }, moduleRoot: process.cwd(), temporaryRoot: os.tmpdir(),
    cache: {
      findVerifiedSolvedSpatialPrototypeRun: async () => ({ ok: true, runId: RUN_ID }),
      recoverSolvedSpatialPrototypeRuns: async () => ({ currentRunId: RUN_ID, runs: [{ runId: RUN_ID, promptSha256: PROMPT_HASH, model: MODEL }] }),
      loadVerifiedSolvedSpatialPrototypeRun: async () => ({ runId: RUN_ID, previewFiles: files }),
      loadVerifiedSpatialPrototypeRun: async () => ({ ok: false }),
    },
    godotTools: {
      createRuntimePreviewProject: () => ({ projectRoot, temporaryRoot: temporaryProjectRoot, identity: { dev: 1n, ino: 1n } }),
      removeRuntimePreviewProject: () => { calls.removed += 1; }, configureGdgsProject: () => { calls.configured += 1; },
      runGodotCommand: () => { calls.imported += 1; return ""; }, assertGodotOutputClean: () => {},
    },
    spawnProcess: () => { queueMicrotask(() => child.stdout.write(`${R14_PREVIEW_READY_MARKER}\n`)); return child; },
  });
  try {
    assert.deepEqual(await operations.findCache({ promptSha256: PROMPT_HASH, model: MODEL }), { ok: true, runId: RUN_ID });
    assert.equal((await operations.generate()).diagnostics[0].code, "R14_PREVIEW_OFFLINE_CACHE_ONLY");
    assert.deepEqual(await operations.launch({ runId: RUN_ID }), { ok: true });
    assert.deepEqual({ configured: calls.configured, imported: calls.imported }, { configured: 1, imported: 1 });
    assert.equal(await readFile(path.join(projectRoot, "spatial_run", "spatial-solution.json"), "utf8"), "solution");
    await operations.stopLaunch(); assert.equal(calls.removed, 1);
  } finally {
    await rm(projectRoot, { recursive: true, force: true });
    await rm(temporaryProjectRoot, { recursive: true, force: true });
  }
});

test("Godot solved wrapper uses solution coordinates and preserves ordinary transition position", async () => {
  const root = new URL("../apps/runtime-godot/solved_spatial_prototype/", import.meta.url);
  const loader = await readFile(new URL("spatial_solution_loader.gd", root), "utf8");
  const scene = await readFile(new URL("solved_scene_lab.gd", root), "utf8");
  const lab = await readFile(new URL("solved_spatial_lab.gd", root), "utf8");
  const source = `${loader}\n${scene}\n${lab}`;
  for (const required of ["MATRIX_OASIS_R14_SOLVED_SPATIAL_READY", "MATRIX_OASIS_R14_SPATIAL_TRACE_JSON:",
    "matrix-oasis.prototype-spatial-solution", "spatial-assembly-collider-v1", "EULER_ORDER_YXZ",
    "_new_visibility_overlaps_player", "_force_spawn", "world._apply_node_for_runtime", "SolvedSpatialSplat"])
    assert.equal(source.includes(required), true, required);
  assert.match(scene, /if relocate:\s*\n\s*player\.set_start_transform/u);
  assert.equal(scene.includes("super._apply_world_candidate"), false);
  for (const forbidden of ["PanoramaSkyMaterial", "environment-panorama.png", "groundTarget", "placementGroundTargetMm",
    "node-carriage", "last-train", "OpenAI", "Meshy", "Marble", "HTTPClient", "OS.execute"])
    assert.equal(source.includes(forbidden), false, forbidden);
});
