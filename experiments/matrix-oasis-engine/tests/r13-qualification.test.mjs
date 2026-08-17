import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  SpatialAnalysisCliOperationalError,
  SPATIAL_ANALYSIS_OUTPUT_ROOT,
  captureSpatialFacts,
  parseSpatialAnalysisArguments,
  parseSpatialFactsCaptureArguments,
  publishSpatialEnvironmentAnalysis,
} from "../scripts/lib/spatial-analysis-core.mjs";

const encoder = new TextEncoder();
const externalTemp = SPATIAL_ANALYSIS_OUTPUT_ROOT;
const fixtureGodot = path.join(externalTemp, "fixture-godot.exe");

function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function factsJson() {
  return canonicalizeJsonValue({
    analysisProfile: { floorSnapMm: 200, maxSlopeMilliDegrees: 45000, playerHeightMm: 1800, playerRadiusMm: 350 },
    canonicalization: "matrix-oasis.canonical-json/1",
    coordinateSystem: { eulerOrder: "YXZ", handedness: "right", unit: "millimeter", upAxis: "Y" },
    environmentBounds: { maximumMm: [4000, 3000, 4000], minimumMm: [-4000, 0, -4000] },
    floorAnchors: [{ capsuleClearanceVerified: true, ceilingHeightMm: 3000, clearanceHeightMm: 1800, clearanceRadiusMm: 350, componentIndex: 0, id: "floor-0000", normalMicros: [0, 1000000, 0], polygonIndex: 0, positionMm: [0, 0, 0] }],
    format: "matrix-oasis.prototype-environment-facts",
    formatVersion: "0.1.0",
    navigationMesh: {
      components: [{ bounds: { maximumMm: [4000, 0, 4000], minimumMm: [-4000, 0, -4000] }, index: 0, polygonIndices: [0] }],
      polygons: [{ componentIndex: 0, vertexIndices: [0, 1, 2, 3] }],
      verticesMm: [[-4000, 0, -4000], [-4000, 0, 4000], [4000, 0, 4000], [4000, 0, -4000]],
    },
    source: {
      blueprint: { canonicalSha256: `sha256:${"a".repeat(64)}`, format: "matrix-oasis.scene-blueprint", formatVersion: "0.1.0" },
      analysisTransform: { profile: "spatial-environment-calibration-v1", sourceCanonicalSha256: `sha256:${"f".repeat(64)}`, eulerOrder: "YXZ", root: { translationMm: [0, 0, 0], rotationMilliDegrees: [0, 0, 0] }, collider: { localTranslationMm: [0, 0, 0], scaleMicros: 1000000 } },
      calibration: { coordinateTransform: "spz-raw-ply-to-godot-v1", godotRotationMilliDegrees: [0, 0, 0], godotTranslationMm: [0, 0, 0], groundPlaneOffsetMm: 0, metricScaleMicros: 1000000 },
      collider: { byteLength: 4, format: "glb", sha256: `sha256:${"b".repeat(64)}` },
      environmentBundleSha256: `sha256:${"c".repeat(64)}`,
      runtime: { artifactSha256: `sha256:${"d".repeat(64)}`, contentVersion: "1", format: "matrix-oasis.runtime-game-pack", formatVersion: "0.1.0", id: "neutral-room", sourceSha256: `sha256:${"e".repeat(64)}` },
      scene: { contentVersion: "1", id: "neutral-room" },
      spatialEnvironmentBundle: { canonicalSha256: `sha256:${"f".repeat(64)}`, format: "matrix-oasis.prototype-spatial-environment-bundle", formatVersion: "0.1.0" },
    },
    wallAnchors: [{ availableHeightMm: 2500, availableWidthMm: 2000, id: "wall-0000", nearestFloorAnchorId: "floor-0000", normalMicros: [1000000, 0, 0], positionMm: [-4000, 1200, 0] }],
  });
}

async function fixture(t) {
  const root = await mkdtemp(path.join(os.tmpdir(), "matrix-oasis-r13-core-test-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const source = path.join(root, "source");
  await mkdir(path.join(source, "assets"), { recursive: true });
  await writeFile(path.join(source, "prototype-spatial-intent.json"), canonicalizeJsonValue({ fixture: "intent" }));
  await writeFile(path.join(source, "prototype-spatial-environment-bundle.json"), canonicalizeJsonValue({ fixture: "bundle" }));
  await writeFile(path.join(source, "assets", "environment-collider.glb"), new Uint8Array([1, 2, 3, 4]));
  await writeFile(path.join(source, "assets", "environment.compressed.ply"), new Uint8Array([5, 6, 7, 8]));
  return { root, source };
}

function analysisOverrides(extra = {}) {
  const facts = factsJson();
  return {
    validateIntent: () => Object.freeze({ valid: true, diagnostics: [] }),
    validateBundle: async () => Object.freeze({ valid: true, diagnostics: [] }),
    validateFacts: () => Object.freeze({ valid: true, diagnostics: [] }),
    createAnalyzer: () => Object.freeze({ kind: "fixture" }),
    runAnalysis: async () => Object.freeze({
      ok: true,
      canonicalFactsJson: facts,
      canonicalReportJson: canonicalizeJsonValue({
        analyzer: { godotVersion: "4.6.3", id: "godot-environment-analyzer", version: "0.1.0-r13" },
        anchors: { floorCount: 1, wallCount: 1 },
        factsSha256: sha256(encoder.encode(facts)),
        format: "matrix-oasis.prototype-environment-analysis-report",
        formatVersion: "0.1.0",
        navigation: { componentCount: 1, polygonCount: 1, vertexCount: 4 },
      }),
    }),
    ...extra,
  };
}

test("R13 CLI argument surfaces are exact", () => {
  const input = path.join(externalTemp, "r13-in");
  const output = path.join(externalTemp, "r13-out");
  const facts = path.join(externalTemp, "r13-facts");
  const capture = path.join(externalTemp, "r13-capture");
  assert.deepEqual({ ...parseSpatialAnalysisArguments(["--output", output, "--spatial-environment-dir", input]) }, {
    outputDirectory: path.resolve(output),
    sourceDirectory: path.resolve(input),
  });
  assert.deepEqual({ ...parseSpatialFactsCaptureArguments(["--facts-dir", facts, "--output", capture]) }, {
    factsDirectory: path.resolve(facts),
    outputDirectory: path.resolve(capture),
  });
  for (const args of [[], ["--output", "x"], ["--output", "x", "--output", "y"]]) {
    assert.throws(() => parseSpatialAnalysisArguments(args), SpatialAnalysisCliOperationalError);
  }
});

test("analysis publishes exactly two canonical files with a single directory rename", async (t) => {
  const value = await fixture(t);
  const output = path.join(externalTemp, `matrix-oasis-r13-analysis-${crypto.randomUUID()}`);
  t.after(() => rm(output, { recursive: true, force: true }));
  let renameCalls = 0;
  const result = await publishSpatialEnvironmentAnalysis({ sourceDirectory: value.source, outputDirectory: output, godotBin: fixtureGodot }, analysisOverrides({
    rename: async (...args) => {
      renameCalls += 1;
      return (await import("node:fs/promises")).rename(...args);
    },
  }));
  assert.equal(renameCalls, 1);
  assert.equal(result.publishedDirectory, output);
  const names = (await (await import("node:fs/promises")).readdir(output)).sort();
  assert.deepEqual(names, ["prototype-environment-analysis-report.json", "prototype-environment-facts.json"]);
  const facts = await readFile(path.join(output, "prototype-environment-facts.json"), "utf8");
  assert.equal(facts, canonicalizeJsonValue(JSON.parse(facts)));
});

test("an explicitly published spatial assembly is validated and forwarded as transform provenance", async (t) => {
  const value = await fixture(t);
  const assemblyJson = canonicalizeJsonValue({ fixture: "spatial-assembly" });
  await writeFile(path.join(value.source, "spatial-assembly.json"), assemblyJson);
  const output = path.join(externalTemp, `matrix-oasis-r13-assembly-${crypto.randomUUID()}`);
  t.after(() => rm(output, { recursive: true, force: true }));
  const overrides = analysisOverrides({ validateAssembly: () => Object.freeze({ valid: true, diagnostics: [] }) });
  const defaultRun = overrides.runAnalysis;
  let observed = null;
  overrides.runAnalysis = async (request, handle) => {
    observed = request;
    return defaultRun(request, handle);
  };
  await publishSpatialEnvironmentAnalysis({ sourceDirectory: value.source, outputDirectory: output, godotBin: fixtureGodot }, overrides);
  assert.equal(observed.spatialAssemblyJson, assemblyJson);
  assert.deepEqual(Object.keys(observed).sort(), ["spatialAssemblyJson", "spatialEnvironmentBundleJson", "spatialEnvironmentFiles", "spatialIntentJson"]);
});

test("existing output and rename failure fail closed without a published pair", async (t) => {
  const value = await fixture(t);
  const existing = path.join(externalTemp, `matrix-oasis-r13-existing-${crypto.randomUUID()}`);
  await mkdir(existing);
  t.after(() => rm(existing, { recursive: true, force: true }));
  await assert.rejects(
    publishSpatialEnvironmentAnalysis({ sourceDirectory: value.source, outputDirectory: existing, godotBin: fixtureGodot }, analysisOverrides()),
    (error) => error.code === "SPATIAL_ANALYSIS_PUBLISH_FAILED",
  );
  const failed = path.join(externalTemp, `matrix-oasis-r13-failed-${crypto.randomUUID()}`);
  await assert.rejects(
    publishSpatialEnvironmentAnalysis({ sourceDirectory: value.source, outputDirectory: failed, godotBin: fixtureGodot }, analysisOverrides({ rename: async () => { throw new Error("dynamic-sensitive"); } })),
    (error) => error.code === "SPATIAL_ANALYSIS_PUBLISH_FAILED" && !String(error).includes("sensitive"),
  );
  await assert.rejects(readFile(path.join(failed, "prototype-environment-facts.json")), /ENOENT/u);
});

test("source realpath drift is rejected before the analyzer is created", async (t) => {
  const value = await fixture(t);
  let analyzerCalls = 0;
  const overrides = analysisOverrides({
    createAnalyzer: () => { analyzerCalls += 1; return Object.freeze({ kind: "fixture" }); },
    realpath: async (candidate) => candidate === value.source ? `${candidate}-replaced` : (await import("node:fs/promises")).realpath(candidate),
  });
  await assert.rejects(
    publishSpatialEnvironmentAnalysis({ sourceDirectory: value.source, outputDirectory: path.join(externalTemp, `matrix-oasis-r13-drift-${crypto.randomUUID()}`), godotBin: fixtureGodot }, overrides),
    (error) => error.code === "SPATIAL_ANALYSIS_SOURCE_INVALID",
  );
  assert.equal(analyzerCalls, 0);
});

test("facts capture produces a deterministic offline SVG and report", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "matrix-oasis-r13-capture-test-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  await writeFile(path.join(root, "prototype-environment-facts.json"), factsJson());
  const output = path.join(externalTemp, `matrix-oasis-r13-capture-${crypto.randomUUID()}`);
  t.after(() => rm(output, { recursive: true, force: true }));
  const first = await captureSpatialFacts({ factsDirectory: root, outputDirectory: output }, { validateFacts: () => ({ valid: true, diagnostics: [] }) });
  assert.match(await readFile(path.join(output, "spatial-facts-plan.svg"), "utf8"), /^<svg[^>]+>/u);
  const report = JSON.parse(await readFile(path.join(output, "capture-report.json"), "utf8"));
  assert.equal(report.factsSha256, first.factsSha256);
  assert.deepEqual(report.navigation, { componentCount: 1, polygonCount: 1, vertexCount: 4 });
});
