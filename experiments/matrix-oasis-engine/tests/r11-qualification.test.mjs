import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import {
  SpatialEnvironmentQualificationOperationalError,
  parseSpatialEnvironmentQualificationArgs,
} from "../scripts/qualify-spatial-environment.mjs";
import {
  SPATIAL_QUALIFICATION_MARKER,
  SpatialQualificationError,
  parseSpatialQualificationArguments,
  parseSpatialQualificationReport,
} from "../scripts/verify-r11.mjs";

const TEMP_ROOT = path.resolve(path.parse(process.cwd()).root, "tmp");

function validArgs() {
  return [
    "--environment-dir", path.join(TEMP_ROOT, "environment"),
    "--spz-file", path.join(TEMP_ROOT, "environment.spz"),
    "--output", path.join(TEMP_ROOT, "spatial-qualified"),
    "--metric-scale-micros", "1000000",
    "--ground-plane-offset-mm", "0",
    "--translation-mm", "0,0,0",
    "--rotation-mdeg", "0,0,0",
  ];
}

test("qualification requires every explicit calibration value and one new C tmp output", () => {
  assert.deepEqual(parseSpatialEnvironmentQualificationArgs(validArgs(), TEMP_ROOT), {
    environmentDir: path.join(TEMP_ROOT, "environment"),
    spzFile: path.join(TEMP_ROOT, "environment.spz"),
    output: path.join(TEMP_ROOT, "spatial-qualified"),
    calibration: {
      coordinateTransform: "spz-raw-ply-to-godot-v1",
      metricScaleMicros: 1_000_000,
      groundPlaneOffsetMm: 0,
      godotTranslationMm: [0, 0, 0],
      godotRotationMilliDegrees: [0, 0, 0],
    },
  });
  for (const invalid of [validArgs().slice(0, -2), [...validArgs().slice(0, -1), "0,0"],
    [...validArgs().slice(0, 5), path.join(TEMP_ROOT, "nested", "output"), ...validArgs().slice(6)],
    [...validArgs().slice(0, 9), "1e6", ...validArgs().slice(10)]]) {
    assert.throws(() => parseSpatialEnvironmentQualificationArgs(invalid, TEMP_ROOT),
      (error) => error instanceof SpatialEnvironmentQualificationOperationalError &&
        error.code === "SPATIAL_ENVIRONMENT_QUALIFICATION_ARGUMENT_INVALID");
  }
});

test("qualification source is offline, credential-free, bounded, and excluded from ordinary verify", async () => {
  const source = await readFile(new URL("../scripts/qualify-spatial-environment.mjs", import.meta.url), "utf8");
  const manifest = JSON.parse(await readFile(new URL("../package.json", import.meta.url), "utf8"));
  for (const forbidden of ["fetch(", ["process", ".env"].join(""), ["MATRIX_OASIS", "_MARBLE_API_KEY"].join(""),
    ["WORLD", "_LABS_API_KEY"].join(""), "PanoramaSkyMaterial", "OS.execute"])
    assert.equal(source.includes(forbidden), false, forbidden);
  for (const required of ["64 * 1024 * 1024", "96 * 1024 * 1024", "32 * 1024 * 1024", "wx+", "bigint"])
    assert.equal(source.includes(required), true, required);
  assert.equal(manifest.scripts["qualify:spatial-environment"], "node scripts/qualify-spatial-environment.mjs");
  assert.equal(manifest.scripts.verify.includes("qualify:spatial-environment"), false);
});

test("graphical qualification has an exact C tmp surface and a strict 300-frame result gate", () => {
  const args = ["--prototype-run-root", path.join(TEMP_ROOT, "prototype-runs"),
    "--spatial-run-root", path.join(TEMP_ROOT, "spatial-runs"),
    "--output", path.join(TEMP_ROOT, "spatial-qualification")];
  assert.deepEqual({ ...parseSpatialQualificationArguments(args, TEMP_ROOT) }, {
    prototypeRunRoot: path.join(TEMP_ROOT, "prototype-runs"),
    spatialRunRoot: path.join(TEMP_ROOT, "spatial-runs"),
    output: path.join(TEMP_ROOT, "spatial-qualification"),
  });
  assert.throws(() => parseSpatialQualificationArguments([...args.slice(0, -1), path.join(TEMP_ROOT, "nested", "output")], TEMP_ROOT),
    (error) => error instanceof SpatialQualificationError && error.code === "SPATIAL_QUALIFICATION_ARGUMENT_INVALID");
  const report = { qualificationVersion: 1, width: 960, height: 540, pointCount: 640_000,
    warmupFrames: 120, sampleFrames: 300, drawnFrames: 300, medianFrameUsec: 20_000, medianFpsMilli: 50_000 };
  assert.deepEqual(parseSpatialQualificationReport(`${SPATIAL_QUALIFICATION_MARKER}${JSON.stringify(report)}\n`), report);
  assert.throws(() => parseSpatialQualificationReport(`${SPATIAL_QUALIFICATION_MARKER}${JSON.stringify({ ...report, medianFpsMilli: 29_999 })}\n`),
    (error) => error instanceof SpatialQualificationError && error.code === "SPATIAL_QUALIFICATION_PERFORMANCE_BELOW_MINIMUM");
  assert.throws(() => parseSpatialQualificationReport(`${SPATIAL_QUALIFICATION_MARKER}${JSON.stringify({ ...report, medianFpsMilli: 0 })}\n`),
    (error) => error instanceof SpatialQualificationError && error.code === "SPATIAL_QUALIFICATION_REPORT_INVALID");
});

test("graphical qualification measures real post-draw time while fixed-fps is capture-only", async () => {
  const [script, lab, manifestText] = await Promise.all([
    readFile(new URL("../scripts/verify-r11.mjs", import.meta.url), "utf8"),
    readFile(new URL("../apps/runtime-godot/spatial_prototype/spatial_lab.gd", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);
  const manifest = JSON.parse(manifestText);
  for (const required of ["RenderingServer.frame_post_draw", "Time.get_ticks_usec", "QUALIFICATION_SAMPLE_FRAMES := 300",
    "Engine.get_frames_drawn", "medianFpsMilli", "--write-movie", "--fixed-fps", "CAPTURE_WARMUP_FRAMES = 120",
    "qualification-report.json", "static-consecutive-rgb-mad-v1", "SPATIAL_QUALIFICATION_VISUAL_STABILITY_FAILED"])
    assert.equal(`${script}\n${lab}`.includes(required), true, required);
  for (const forbidden of [["process", ".env"].join(""), "PanoramaSkyMaterial", "environment-panorama.png", "fetch("])
    assert.equal(script.includes(forbidden), false, forbidden);
  assert.equal(manifest.scripts["qualify:spatial-preview"], "node scripts/verify-r11.mjs");
  assert.equal(manifest.scripts.verify.includes("qualify:spatial-preview"), false);
});
