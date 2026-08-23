import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { EventEmitter } from "node:events";
import { existsSync } from "node:fs";
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
  R14_PREVIEW_STARTUP_TIMEOUT_MS,
  R14_PREVIEW_TRACE_MARKER,
  createR14PreviewOperations,
  r14GodotArguments,
} from "../scripts/lib/r14-preview-core.mjs";
import { parseR14PreviewArguments, R14_PREVIEW_HOST_MARKER } from "../scripts/preview-r14.mjs";

assert.equal(R14_PREVIEW_STARTUP_TIMEOUT_MS, 120_000,
  "the first visible load of a verified splat must have the full bounded import window");

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
    visualSafety: {
      profile: "gaussian-vertical-occupancy-v1", cellSizeMm: 250, verticalCellSizeMm: 500,
      verticalBandMm: [350, 3000], minimumCellPoints: 16, peakThresholdPermille: 25,
      minimumVerticalBins: 3, minimumComponentCells: 3, visualRegistrationOffsetMm: 0,
      sampledPointCount: 1, acceptedPointCount: 0, cellPointThreshold: 16, occupiedCellCount: 0,
      boxes: [], sourceSplatSha256: hash(Uint8Array.of(1)), spatialAssemblySha256: hash(spatialAssemblyJson),
    },
    verifier: { id: "godot-spatial-solution-verifier", version: "0.1.0-r14", godotVersion: "4.6.3" },
    checks: { placementCount: solution.placements.length, nodeContextCount: solution.nodeContexts.length,
      pathCount: solution.nodeContexts.reduce((sum, item) => sum + item.actionTerminal.actionCount, 0),
      terminalCount: solution.nodeContexts.reduce((sum, item) => sum + item.actionTerminal.actionCount, 0),
      visualSafetyBoxCount: 0 },
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
  assert.equal(args.some((item) => item.includes("environment-facts.json")), true);
  assert.equal(args.some((item) => item.includes("spatial-verification-report.json")), true);
  assert.equal(args.at(-1), "--matrix-oasis-r14-smoke");
  assert.equal(R14_PREVIEW_READY_MARKER, "MATRIX_OASIS_R14_SOLVED_SPATIAL_READY");
  assert.equal(R14_PREVIEW_TRACE_MARKER, "MATRIX_OASIS_R14_SPATIAL_TRACE_JSON:");
  assert.equal(R14_PREVIEW_HOST_MARKER, "MATRIX_OASIS_R14_SOLVED_SPATIAL_HOST");
});

test("PR boundary excludes one-off qualification scripts and preserves truthful product claims", async () => {
  for (const relativePath of [
    "scripts/tmp-r14-refresh-preview.mjs",
    "scripts/tmp-r14-solve.mjs",
    "scripts/tmp-r14-splat-parity.mjs",
    "scripts/tmp-r14-visual-safety.mjs",
  ]) assert.equal(existsSync(new URL(`../${relativePath}`, import.meta.url)), false, relativePath);

  const productSources = await Promise.all([
    "packages/prototype-spatial-solver/src/solver.mjs",
    "packages/prototype-spatial-verifier/src/index.mjs",
    "packages/prototype-spatial-verifier/src/visual-safety.mjs",
    "scripts/qualify-r14-spatial-solver.mjs",
    "scripts/capture-r14.mjs",
    "scripts/preview-r14.mjs",
    "scripts/lib/r14-preview-core.mjs",
    "scripts/lib/solved-spatial-cache-core.mjs",
    "apps/runtime-godot/solved_spatial_prototype/solved_scene_lab.gd",
    "apps/runtime-godot/solved_spatial_prototype/solved_spatial_lab.gd",
    "apps/runtime-godot/solved_spatial_prototype/spatial_solution_loader.gd",
    "apps/runtime-godot/spatial_solution_verification/solution_verifier.gd",
  ].map((relativePath) => readFile(new URL(`../${relativePath}`, import.meta.url), "utf8")));
  const productSource = productSources.join("\n").toLowerCase();
  for (const forbidden of ["last-train", "last train", "subway", "metro", "node-carriage", "node-platform",
    "matrix-oasis-r12", "15ea379b", "bde11ce", "81f122d9", "705fd38b"])
    assert.equal(productSource.includes(forbidden), false, forbidden);

  const [acceptance, limitations, criticalPath, mvpStatusText, packageText] = await Promise.all([
    readFile(new URL("../docs/rounds/R14_ACCEPTANCE.md", import.meta.url), "utf8"),
    readFile(new URL("../docs/KNOWN_LIMITATIONS.md", import.meta.url), "utf8"),
    readFile(new URL("../docs/V1_CRITICAL_PATH.md", import.meta.url), "utf8"),
    readFile(new URL("../docs/MVP_STATUS.json", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);
  for (const text of [acceptance, limitations, criticalPath]) {
    assert.match(text, /Creator[^\n]*不能[^\n]*复现/u);
    assert.match(text, /第二[^\n]*真实环境[^\n]*未/u);
  }
  assert.match(acceptance, /显式离线[^\n]*直接R14预览/u);
  const mvpStatus = JSON.parse(mvpStatusText);
  assert.deepEqual({ status: mvpStatus.status, claimAllowed: mvpStatus.claimAllowed, blockingRound: mvpStatus.blockingRound },
    { status: "pending-creator-migration", claimAllowed: false, blockingRound: "R16" });
  const scripts = JSON.parse(packageText).scripts;
  assert.match(scripts["preview:r14"], /scripts\/preview-r14\.mjs/u);
  assert.doesNotMatch(scripts["preview:prototype"], /preview-r14/u);
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
    assert.equal(loaded.previewFiles.has("environment-facts.json"), true);
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
    ["environment-facts.json", encoder.encode("facts")],
    ["spatial-solution.json", encoder.encode("solution")], ["spatial-verification-report.json", encoder.encode("verification")],
    ["assets/environment-collider.glb", Uint8Array.of(1)], ["assets/environment.compressed.ply", Uint8Array.of(2)],
  ]);
  const calls = { configured: 0, imported: 0, removed: 0 };
  const operations = createR14PreviewOperations({
    prototypeRunRoot: path.join(os.tmpdir(), "prototype"), spatialRunRoot: path.join(os.tmpdir(), "spatial"),
    solvedRunRoot: path.join(os.tmpdir(), "solved"), godot: { command: "godot" }, moduleRoot: process.cwd(), temporaryRoot: os.tmpdir(),
    cache: {
      findVerifiedSolvedSpatialPrototypeRun: async () => ({ ok: true, runId: RUN_ID }),
      recoverSolvedSpatialPrototypeRuns: async () => ({ currentRunId: RUN_ID, currentSolutionSha256: `sha256:${"c".repeat(64)}`,
        runs: [{ runId: RUN_ID, promptSha256: PROMPT_HASH, model: MODEL, solutionSha256: `sha256:${"c".repeat(64)}` }] }),
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
    assert.deepEqual(await operations.recover(), { currentRunId: RUN_ID,
      runs: [{ runId: RUN_ID, promptSha256: PROMPT_HASH, model: MODEL }] });
    assert.deepEqual(await operations.launch({ runId: RUN_ID }), { ok: true });
    assert.deepEqual({ configured: calls.configured, imported: calls.imported }, { configured: 1, imported: 1 });
    assert.equal(await readFile(path.join(projectRoot, "spatial_run", "spatial-solution.json"), "utf8"), "solution");
    assert.equal(await readFile(path.join(projectRoot, "spatial_run", "environment-facts.json"), "utf8"), "facts");
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
    "_new_visibility_overlaps_player", "_force_spawn", "_apply_solution_visibility", "SolvedSpatialSplat",
    "--matrix-oasis-environment-facts=", "R14VerifiedNavigationCollision", "ConcavePolygonShape3D"])
    assert.equal(source.includes(required), true, required);
  assert.match(loader, /\["clearanceMm", "id", "limits", "player", "terminal", "tolerances"\]/u);
  assert.match(loader, /player == \{"eyeHeightMm": 1475, "floorSnapMm": 200, "heightMm": 1800, "radiusMm": 350\}/u);
  assert.match(loader, /terminal == \{"centerHeightMm": 850, "columnSpacingMm": 1700, "columns": 8/u);
  assert.match(loader, /_placement_id_map\(solution\.get\("placements"\), scene, environment_placement_id\)/u);
  assert.match(scene, /root\.get_node_or_null\(NodePath\(_scene_placement_ids\[placement_id\]\)\)/u);
  assert.match(scene, /PLAYER_SPAWN_CLEARANCE_MM := 25/u);
  assert.match(loader, /\["columns", "depthMm", "layoutCenterOffsetMm", "layoutDepthMm", "layoutWidthMm", "widthMm"\]/u);
  assert.match(scene, /_apply_terminal_layout\(context\["actionTerminal"\]\["footprint"\],\s*context\["actionTerminal"\]\["terminalSupports"\]/u);
  assert.match(scene, /terminal\.position = Vector3\(centered_column \* TERMINAL_COLUMN_SPACING, 0\.85,/u);
  assert.match(scene, /var columns: int = footprint\["columns"\]/u);
  assert.match(scene, /TERMINAL_LABEL_VISIBILITY_RANGE := 3\.0/u);
  assert.match(scene, /label_3d\.visibility_range_end = TERMINAL_LABEL_VISIBILITY_RANGE/u);
  assert.match(scene, /if relocate:\s*\n\s*var player_transform := _player_transform/u);
  assert.match(scene, /player\.set_start_transform\(player_transform\)/u);
  assert.match(scene, /body_position_mm\[1\] \+= PLAYER_HEIGHT_MM \/ 2 \+ PLAYER_SPAWN_CLEARANCE_MM/u);
  assert.match(loader, /_entry_support_height_mm\(solution, runtime\)/u);
  assert.match(loader, /Vector3\(float\(vertex\[0\]\), float\(runtime_support_height_mm\), float\(vertex\[2\]\)\)/u);
  assert.match(scene, /runtime_position = _with_runtime_support\(runtime_position\)/u);
  assert.match(scene, /placement\["anchorKind"\] == "floor" and not _ground_visual_instance\(node\)/u);
  assert.match(scene, /mesh_instance\.global_transform \* bounds\.get_endpoint\(endpoint_index\)/u);
  assert.match(scene, /grounded_position\.y \+= float\(_runtime_support_height_mm\) \/ 1000\.0 - minimum_y/u);
  assert.match(scene, /_runtime_support_consistent_for_test\(\)/u);
  assert.match(scene, /func set_spatial_boundary\(/u);
  assert.match(scene, /func set_navigation_domain\(faces: PackedVector3Array\)/u);
  assert.match(scene, /func activate_spatial_boundary\(\)/u);
  assert.match(scene, /player\.global_transform = _last_safe_player_transform/u);
  assert.match(scene, /var inside := _inside_runtime_domain\(player\.global_position\)/u);
  assert.match(scene, /func _inside_runtime_domain\(global_position: Vector3\)/u);
  assert.match(scene, /Geometry2D\.get_closest_point_to_segment/u);
  assert.match(loader, /_playable_polygon_indices\(facts, solution_navigation, placements,/u);
  assert.match(loader, /required_floor_ids\[placement\["anchorId"\]\] = true/u);
  assert.match(loader, /context\.get\("approachPathFloorAnchorIds", \[\]\)/u);
  assert.match(loader, /context\.get\("actionTerminal", \{\}\)\.get\("terminalSupports", \[\]\)/u);
  assert.match(loader, /var buffered := selected\.duplicate\(\)/u);
  assert.match(loader, /for polygon_index: int in selected_polygon_indices:/u);
  assert.match(lab, /scene_lab\.set_spatial_boundary\(spatial\["assembly"\]\["transforms"\]\["walkableEnvelope"\]/u);
  assert.match(lab, /scene_lab\.set_navigation_domain\(solved\["navigationFaces"\]\)/u);
  assert.match(lab, /scene_lab\.activate_spatial_boundary\(\)/u);
  assert.equal(scene.includes("super._apply_world_candidate"), false);
  assert.equal(scene.includes("world._apply_node_for_runtime"), false);
  assert.match(lab, /shape\.backface_collision = true/u);
  assert.match(lab, /\(collision_object as CollisionObject3D\)\.collision_layer = 0/u);
  assert.match(lab, /_collect_faces\(visual, placement\.global_transform\)/u);
  assert.match(lab, /faces\.append_array\(obstruction_faces\)/u);
  assert.match(lab, /faces\.append_array\(visual_safety_faces\)/u);
  assert.match(lab, /_visual_registration_offset_mm != visual_safety\["visualRegistrationOffsetMm"\]/u);
  assert.match(lab, /absf\(normal\.normalized\(\)\.y\) < MAX_WALKABLE_NORMAL_Y/u);
  assert.match(lab, /func _register_visual_support\(/u);
  assert.match(lab, /gaussian\.get\("xyz"\)/u);
  assert.match(lab, /not scene_lab\._inside_spatial_boundary\(global_point\)/u);
  assert.match(lab, /_visual_registration_offset_mm = runtime_support_height_mm - _visual_support_height_mm/u);
  assert.match(lab, /splat\.global_position \+= Vector3\.UP \* \(float\(_visual_registration_offset_mm\) \/ 1000\.0\)/u);
  assert.match(lab, /"sceneDepthPriorityMarginMicros": SCENE_DEPTH_PRIORITY_MARGIN_MICROS/u);
  assert.match(lab, /"visualRegistrationOffsetMm": _visual_registration_offset_mm/u);
  assert.match(lab, /"visualSafetyBoxCount": _visual_safety_box_count/u);
  assert.match(lab, /SCENE_DEPTH_PRIORITY_MARGIN_MICROS := 200000/u);
  assert.match(lab, /_scene_depth_bias = float\(assembly\["environment"\]\["renderer"\]\["depthBiasMicros"\]\) \/ 1000000\.0 - SCENE_DEPTH_PRIORITY_MARGIN_METERS/u);
  assert.match(lab, /effect\.set\("depth_bias", _scene_depth_bias\)/u);
  assert.match(lab, /not _scene_lab\._runtime_support_consistent_for_test\(\)/u);
  assert.match(lab, /_visible_solution_placement_count_for_test\(\) < 1/u);
  for (const forbidden of ["PanoramaSkyMaterial", "environment-panorama.png", "groundTarget", "placementGroundTargetMm",
    "node-carriage", "last-train", "OpenAI", "Meshy", "Marble", "HTTPClient", "OS.execute"])
    assert.equal(source.includes(forbidden), false, forbidden);
});
