import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import path from "node:path";
import test from "node:test";
import {
  R14QualificationOperationalError,
  parseR14QualificationArguments,
  qualificationInputs,
  r14PhysicalRejectionCandidate,
  runR14Qualification,
} from "../scripts/qualify-r14-spatial-solver.mjs";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { validatePrototypeSpatialAssemblyJson } from "@matrix-oasis/prototype-spatial-assembler";
import {
  normalizeR14SpatialAssemblyForQualification,
  spatialAssemblyMatchesSource,
} from "../scripts/lib/solved-spatial-cache-core.mjs";
import { parseR14CaptureArguments, r14CaptureGodotArguments } from "../scripts/capture-r14.mjs";

const RUN_ID = `${"a".repeat(64)}-${"b".repeat(64)}`;
const bytes = (value) => new TextEncoder().encode(value);
const sha256 = (value) => `sha256:${createHash("sha256").update(value).digest("hex")}`;

function inputs() {
  return Object.freeze({
    sceneBlueprintJson: "blueprint",
    runtimeGamePackJson: "runtime",
    runtimeReceiptJson: "receipt",
    spatialAssemblyJson: "assembly",
    assetBundleJson: "assets",
    spatialEnvironmentBundleJson: "environment",
    assetFiles: new Map([["assets/prop.glb", bytes("prop")]]),
    environmentColliderBytes: bytes("collider"),
    environmentSplatBytes: bytes("splat"),
    spatialEnvironmentFiles: new Map([
      ["assets/environment-collider.glb", bytes("collider")],
      ["assets/environment.compressed.ply", bytes("splat")],
    ]),
  });
}

function successOperations(observed) {
  return Object.freeze({
    async synthesize(value) {
      observed.push(["synthesize", value]);
      return { ok: true, canonicalSpatialIntentJson: "intent" };
    },
    async analyze(value) {
      observed.push(["analyze", value]);
      return { ok: true, canonicalFactsJson: "facts", canonicalReportJson: "analysis-report" };
    },
    async solve(value) {
      observed.push(["solve", value]);
      return { ok: true, canonicalSpatialSolutionJson: "solution", canonicalSpatialSolutionReportJson: "solver-report" };
    },
    async verify(value) {
      observed.push(["verify", value]);
      return { ok: true, canonicalVerificationReportJson: "verification-report" };
    },
    async publish(value) {
      observed.push(["publish", value]);
      return { solutionSha256: `sha256:${"c".repeat(64)}` };
    },
  });
}

test("qualification arguments require three direct temporary roots and one exact run id", () => {
  const root = path.resolve(path.parse(process.cwd()).root, "tmp");
  const parsed = parseR14QualificationArguments([
    "--spatial-run-root", path.join(root, "spatial"),
    "--run-id", RUN_ID,
    "--solved-run-root", path.join(root, "solved"),
    "--prototype-run-root", path.join(root, "prototype"),
  ], root);
  assert.equal(parsed.runId, RUN_ID);
  assert.equal(parsed.temporaryRoot, root);
  for (const args of [
    [],
    ["--prototype-run-root", path.join(root, "nested", "prototype"), "--spatial-run-root", path.join(root, "spatial"),
      "--solved-run-root", path.join(root, "solved"), "--run-id", RUN_ID],
    ["--prototype-run-root", path.join(root, "prototype"), "--spatial-run-root", path.join(root, "spatial"),
      "--solved-run-root", path.join(root, "solved"), "--run-id", "unsafe"],
  ]) {
    assert.throws(() => parseR14QualificationArguments(args, root), (error) =>
      error instanceof R14QualificationOperationalError && error.code === "R14_QUALIFICATION_ARGUMENT_INVALID");
  }
});

test("capture arguments remain temporary-only and preserve the solved preview argument boundary", () => {
  const root = path.resolve(path.parse(process.cwd()).root, "tmp");
  const args = [
    "--prototype-run-root", path.join(root, "prototype"), "--spatial-run-root", path.join(root, "spatial"),
    "--solved-run-root", path.join(root, "solved"), "--output", path.join(root, "capture"), "--narrow",
  ];
  const parsed = parseR14CaptureArguments(args, root);
  assert.equal(parsed.width, 640);
  assert.throws(() => parseR14CaptureArguments(args.slice(0, 8).concat("--wide"), root), /R14_CAPTURE_ARGUMENT_INVALID/u);
  const projectRoot = path.join(root, "project"); const runDirectory = path.join(root, "run");
  const godotArgs = r14CaptureGodotArguments({ projectRoot, runDirectory, output: parsed.output, width: parsed.width });
  assert.equal(godotArgs.includes("--write-movie"), true);
  assert.equal(godotArgs.includes("640x540"), true);
  assert.equal(godotArgs.includes("res://solved_spatial_prototype/solved_spatial_lab.tscn"), true);
  assert.equal(godotArgs.filter((value) => value === "--").length, 1);
  assert.equal(godotArgs.some((value) => value === `--matrix-oasis-spatial-solution=${path.join(runDirectory,
    "spatial-solution.json")}`), true);
});

test("one offline path synthesizes, analyzes, solves, verifies and publishes exact artifacts", async () => {
  const observed = [];
  const result = await runR14Qualification({ runId: RUN_ID, inputs: inputs() }, successOperations(observed));
  assert.deepEqual(result, { ok: true, runId: RUN_ID, solutionSha256: `sha256:${"c".repeat(64)}` });
  assert.deepEqual(observed.map(([name]) => name), ["synthesize", "analyze", "solve", "verify", "publish"]);
  assert.deepEqual(observed[0][1], {
    sceneBlueprintJson: "blueprint", runtimeGamePackJson: "runtime", runtimeReceiptJson: "receipt", assetBundleJson: "assets",
  });
  assert.deepEqual(observed[1][1], {
    spatialIntentJson: "intent", spatialEnvironmentBundleJson: "environment",
    spatialEnvironmentFiles: inputs().spatialEnvironmentFiles, spatialAssemblyJson: "assembly",
  });
  assert.deepEqual(Object.keys(observed[4][1].artifacts).sort(), [
    "environmentFactsJson", "spatialIntentJson", "spatialSolutionJson", "spatialSolutionReportJson", "spatialVerificationReportJson",
  ]);
  assert.equal(Object.isFrozen(result), true);
});

test("qualification excludes replaced environment template files but requires non-environment assets", () => {
  const sceneBlueprintJson = canonicalizeJsonValue({ assetBriefs: [
    { id: "environment", kind: "environment" },
    { id: "prop", kind: "prop" },
  ] });
  const assetBundleJson = canonicalizeJsonValue({ materializations: [
    { assetBriefId: "environment", assets: [{ path: "assets/floor.glb" }] },
    { assetBriefId: "prop", assets: [{ path: "assets/prop.glb" }] },
  ] });
  const source = {
    qualificationEvidence: { source: "verified-r14-spatial-source", sceneBlueprintJson,
      runtimeGamePackJson: "{}", runtimeReceiptJson: "{}" },
    previewFiles: new Map([
      ["runtime-game-pack.json", bytes("{}")], ["runtime-receipt.json", bytes("{}")],
      ["spatial-assembly.json", bytes("{}")], ["assets/prop.glb", bytes("prop")],
      ["assets/environment-collider.glb", bytes("collider")],
      ["assets/environment.compressed.ply", bytes("splat")],
    ]),
  };
  const result = qualificationInputs({ source, assetBundleJson, spatialEnvironmentBundleJson: "{}" });
  assert.deepEqual([...result.assetFiles.keys()], ["assets/prop.glb"]);
  source.previewFiles.delete("assets/prop.glb");
  assert.throws(() => qualificationInputs({ source, assetBundleJson, spatialEnvironmentBundleJson: "{}" }), (error) =>
    error instanceof R14QualificationOperationalError && error.code === "R14_QUALIFICATION_SOURCE_INVALID");
});

test("physical placement rejection deterministically retries before publishing", async () => {
  const observed = [];
  let solveCount = 0;
  let verifyCount = 0;
  const operations = {
    ...successOperations(observed),
    async solve(value) {
      solveCount += 1; observed.push(["solve", value]);
      return { ok: true, spatialSolution: { placements: [{ placementId: "prop", anchorKind: "floor",
        anchorId: `floor-${solveCount}` }] }, canonicalSpatialSolutionJson: `solution-${solveCount}`,
      canonicalSpatialSolutionReportJson: `solver-report-${solveCount}` };
    },
    async verify(value) {
      verifyCount += 1; observed.push(["verify", value]);
      return verifyCount === 1 ? { ok: false, diagnostics: [{ code: "PROTOTYPE_SPATIAL_VERIFY_ASSET_PENETRATION",
        path: "/placements/0" }] } : { ok: true, canonicalVerificationReportJson: "verification-report" };
    },
    rejectPlacementCandidate({ solved, diagnostics }) {
      observed.push(["reject", solved.spatialSolution.placements[0].anchorId, diagnostics[0].code]);
      return true;
    },
  };
  const result = await runR14Qualification({ runId: RUN_ID, inputs: inputs() }, operations);
  assert.equal(result.ok, true);
  assert.equal(solveCount, 2);
  assert.equal(verifyCount, 2);
  assert.deepEqual(observed.map((item) => item[0]), ["synthesize", "analyze", "solve", "verify", "reject", "solve", "verify", "publish"]);
  const published = observed.find((item) => item[0] === "publish")[1];
  assert.equal(published.artifacts.spatialSolutionJson, "solution-2");
});

test("terminal collision and sight rejection bind the exact adaptive terminal layout", () => {
  const context = {
    zoneId: "zone-a",
    playerSpawn: { floorAnchorId: "spawn-a" },
    actionTerminal: { floorAnchorId: "terminal-a", approachFloorAnchorId: "approach-a",
      yawMilliDegrees: 0, actionCount: 3, footprint: { columns: 2 } },
  };
  const solved = { spatialSolution: { nodeContexts: [context] } };
  for (const code of ["PROTOTYPE_SPATIAL_VERIFY_TERMINAL_COLLISION", "PROTOTYPE_SPATIAL_VERIFY_TERMINAL_SIGHT_BLOCKED"]) {
    const candidate = r14PhysicalRejectionCandidate({ solved, diagnostics: [{ code, path: "/nodeContexts/0/actionTerminal" }] });
    assert.equal(candidate.kind, "station");
    assert.match(candidate.key, /^terminal\0/u);
    assert.deepEqual(candidate.key.split("\0").slice(-2), ["3", "2"]);
  }
  const pathCandidate = r14PhysicalRejectionCandidate({ solved,
    diagnostics: [{ code: "PROTOTYPE_SPATIAL_VERIFY_PATH_BLOCKED", path: "/nodeContexts/0" }] });
  assert.equal(pathCandidate.kind, "station");
  assert.equal(pathCandidate.key.startsWith("terminal\0"), false);
  const differentColumns = structuredClone(solved);
  differentColumns.spatialSolution.nodeContexts[0].actionTerminal.footprint.columns = 1;
  const repeatedPathCandidate = r14PhysicalRejectionCandidate({ solved: differentColumns,
    diagnostics: [{ code: "PROTOTYPE_SPATIAL_VERIFY_PATH_BLOCKED", path: "/nodeContexts/0" }] });
  assert.equal(repeatedPathCandidate.key, pathCandidate.key);
});

test("physical verification retries stop at the fixed qualification limit", async () => {
  const observed = [];
  let solveCount = 0;
  let verifyCount = 0;
  let rejectCount = 0;
  const operations = {
    ...successOperations(observed),
    async solve() {
      solveCount += 1;
      return { ok: true, canonicalSpatialSolutionJson: `solution-${solveCount}`,
        canonicalSpatialSolutionReportJson: `report-${solveCount}` };
    },
    async verify() {
      verifyCount += 1;
      return { ok: false, diagnostics: [{ code: "PROTOTYPE_SPATIAL_VERIFY_PATH_BLOCKED", path: "/nodeContexts/0" }] };
    },
    rejectPhysicalCandidate() { rejectCount += 1; return true; },
  };
  const result = await runR14Qualification({ runId: RUN_ID, inputs: inputs() }, operations);
  assert.equal(result.ok, false);
  assert.equal(result.stage, "verification");
  assert.equal(result.diagnostics[0].code, "R14_QUALIFICATION_PHYSICAL_RETRY_LIMIT_EXCEEDED");
  assert.equal(solveCount, 21);
  assert.equal(verifyCount, 21);
  assert.equal(rejectCount, 20);
  assert.equal(observed.some(([name]) => name === "publish"), false);
});

test("every failed stage stops before publication and preserves static diagnostics", async () => {
  const stages = ["synthesize", "analyze", "solve", "verify"];
  for (const failed of stages) {
    const observed = [];
    const operations = { ...successOperations(observed), [failed]: async () => ({
      ok: false,
      diagnostics: Object.freeze([Object.freeze({ code: `STATIC_${failed.toUpperCase()}`, path: "/safe" })]),
    }) };
    const result = await runR14Qualification({ runId: RUN_ID, inputs: inputs() }, operations);
    assert.equal(result.ok, false);
    assert.equal(result.stage, failed === "synthesize" ? "synthesis" : failed === "analyze" ? "analysis" :
      failed === "solve" ? "solver" : failed === "verify" ? "verification" : failed);
    assert.equal(result.diagnostics[0].code, `STATIC_${failed.toUpperCase()}`);
    assert.equal(observed.some(([name]) => name === "publish"), false);
    assert.equal(Object.isFrozen(result), true);
  }
});

test("qualification never introduces provider or network operations", async () => {
  const source = await (await import("node:fs/promises")).readFile(
    new URL("../scripts/qualify-r14-spatial-solver.mjs", import.meta.url), "utf8");
  for (const forbidden of ["fetch(", "OpenAI", "Marble", "Meshy", "MATRIX_OASIS_MODEL", "MATRIX_OASIS_MARBLE", "MATRIX_OASIS_MESHY"]) {
    assert.equal(source.includes(forbidden), false, forbidden);
  }
});

test("operational failures expose only a static stage code", async () => {
  const operations = { ...successOperations([]), analyze: async () => { throw new Error("dynamic-sensitive-value"); } };
  await assert.rejects(runR14Qualification({ runId: RUN_ID, inputs: inputs() }, operations), (error) =>
    error instanceof R14QualificationOperationalError && error.code === "R14_QUALIFICATION_ANALYSIS_INTERNAL_ERROR" &&
    !String(error).includes("dynamic-sensitive-value"));
});

test("legacy official-metric source normalization changes only its contract span", () => {
  const valid = {
    format: "matrix-oasis.prototype-spatial-assembly", formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1", scene: { id: "neutral-space", contentVersion: "1", title: "Neutral" },
    runtimeIdentity: { runtimeFormat: "matrix-oasis.runtime-game-pack", runtimeFormatVersion: "0.1.0",
      packId: "neutral-space", packContentVersion: "1", sourceCanonicalSha256: `sha256:${"a".repeat(64)}`,
      artifactSha256: `sha256:${"b".repeat(64)}` },
    sources: { scenePackSha256: `sha256:${"c".repeat(64)}`, prototypeAssemblyReportSha256: `sha256:${"d".repeat(64)}`,
      spatialEnvironmentBundleSha256: `sha256:${"e".repeat(64)}`, sceneBlueprintSha256: `sha256:${"f".repeat(64)}` },
    environment: { panoramaVisible: false, renderer: { profile: "opaque-depth-compose-v1", depthBiasMicros: 0,
      depthTestMinAlphaPermille: 50, depthCaptureAlphaPermille: 500 }, splat: { path: "assets/environment.compressed.ply",
      sha256: `sha256:${"1".repeat(64)}`, numGaussians: 640000, derivation: { profile: "mpmm-uniform-v1",
        targetNumGaussians: 640000, sourceNumGaussians: 1920000, fullResolutionCompressedPly: {
          byteLength: 100, sha256: `sha256:${"2".repeat(64)}`, numGaussians: 1920000 } } },
      collider: { assetId: "environment-collider", placementId: "environment", path: "assets/environment-collider.glb",
        sha256: `sha256:${"3".repeat(64)}` } },
    transforms: { coordinateTransform: "spz-raw-ply-to-godot-v1", eulerOrder: "YXZ", alignment: {
      profile: "collider-official-metric-frame-v4", targetFloorSpanMm: 0, maximumHorizontalSpanMm: 128000,
      colliderBoundsMm: { minimumMm: [-1000, -1000, -1000], maximumMm: [1000, 1000, 1000] },
      centerFloorSampleSourceMm: [0, 0, 0], splatProfile: "splat-opencv-to-godot-official-metric-v4",
      splatBoundsProfile: "source-position-percentile-1-99-v1",
      splatBoundsMm: { minimumMm: [-1000, -1000, -1000], maximumMm: [1000, 1000, 1000] } },
      root: { translationMm: [0, 0, 0], rotationMilliDegrees: [0, 0, 0] },
      splat: { localTranslationMm: [0, 0, 0], localRotationMilliDegrees: [180000, 0, 0], scaleMicros: 1000000 },
      collider: { localTranslationMm: [0, 0, 0], scaleMicros: 1000000 },
      walkableEnvelope: { profile: "source-density-first-surface-v1", minimumMm: [-5000, 0, -5000],
        maximumMm: [5000, 3000, 5000], wallThicknessMm: 700, floorThicknessMm: 200,
        verticalBandMm: [350, 3000], lateralBandMm: 4000, binSizeMm: 250, minimumBinCount: 64,
        peakThresholdPermille: 5, adjacentBins: 2 }, placementGroundTargetMm: 150 },
  };
  const canonical = canonicalizeJsonValue(valid);
  assert.equal(validatePrototypeSpatialAssemblyJson(canonical).valid, true);
  const legacy = structuredClone(valid); legacy.transforms.alignment.maximumHorizontalSpanMm = 90000;
  const normalized = normalizeR14SpatialAssemblyForQualification(canonicalizeJsonValue(legacy), canonicalizeJsonValue);
  assert.equal(validatePrototypeSpatialAssemblyJson(normalized).valid, true);
  assert.deepEqual(JSON.parse(normalized), valid);
  const unrelatedInvalid = structuredClone(legacy); unrelatedInvalid.transforms.eulerOrder = "XYZ";
  assert.equal(normalizeR14SpatialAssemblyForQualification(canonicalizeJsonValue(unrelatedInvalid), canonicalizeJsonValue), null);
});

test("verified spatial source comparison receives its canonicalizer explicitly", () => {
  const blueprintText = "blueprint";
  const scenePackText = "scene-pack";
  const prototypeAssemblyReportText = "prototype-report";
  const spatialBundleText = "spatial-bundle";
  const assemblyText = "spatial-assembly";
  const sources = {
    scenePackSha256: sha256(scenePackText),
    prototypeAssemblyReportSha256: sha256(prototypeAssemblyReportText),
    spatialEnvironmentBundleSha256: sha256(spatialBundleText),
    sceneBlueprintSha256: sha256(blueprintText),
  };
  const spatialBundle = {
    assets: {
      splat: { path: "assets/environment.compressed.ply", sha256: `sha256:${"1".repeat(64)}`, numGaussians: 640000 },
      collider: { path: "assets/environment-collider.glb", sha256: `sha256:${"2".repeat(64)}` },
    },
    calibration: { coordinateTransform: "spz-raw-ply-to-godot-v1", godotRotationMilliDegrees: [0, 0, 0] },
  };
  const runtime = { format: "matrix-oasis.runtime-game-pack", formatVersion: "0.1.0",
    source: { id: "neutral-space", contentVersion: "1", canonicalSha256: "a".repeat(64) } };
  const receipt = { artifact: { sha256: "b".repeat(64) } };
  const assembly = {
    sources,
    scene: { id: runtime.source.id, contentVersion: runtime.source.contentVersion },
    runtimeIdentity: { runtimeFormat: runtime.format, runtimeFormatVersion: runtime.formatVersion,
      packId: runtime.source.id, packContentVersion: runtime.source.contentVersion,
      sourceCanonicalSha256: `sha256:${runtime.source.canonicalSha256}`,
      artifactSha256: `sha256:${receipt.artifact.sha256}` },
    environment: { panoramaVisible: false, splat: spatialBundle.assets.splat, collider: spatialBundle.assets.collider },
    transforms: { coordinateTransform: spatialBundle.calibration.coordinateTransform, eulerOrder: "YXZ",
      root: { rotationMilliDegrees: spatialBundle.calibration.godotRotationMilliDegrees } },
  };
  const input = { assembly, assemblyText, assemblyReport: { reportVersion: 1,
    profile: "matrix-oasis.prototype-spatial-assembly/2", inputs: structuredClone(sources),
    output: { spatialAssemblySha256: sha256(assemblyText), referencedFiles: 2 } },
  blueprintText, prototypeAssemblyReportText, runtime, receipt, scenePackText, spatialBundleText, spatialBundle };
  assert.equal(spatialAssemblyMatchesSource({ ...input, canonicalizeJsonValue }), true);
  input.assemblyReport.profile = "matrix-oasis.prototype-spatial-assembly/1";
  assert.equal(spatialAssemblyMatchesSource({ ...input, canonicalizeJsonValue }), true);
  assert.equal(spatialAssemblyMatchesSource(input), false);
});
