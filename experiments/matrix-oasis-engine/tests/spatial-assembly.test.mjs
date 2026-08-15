import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE,
} from "@matrix-oasis/prototype-spatial-assembler";

const packageRoot = new URL("../packages/prototype-spatial-assembler/", import.meta.url);

test("spatial assembly package is private and pins its audited contracts and GLB reader", async () => {
  const manifest = JSON.parse(await readFile(new URL("package.json", packageRoot), "utf8"));
  assert.equal(manifest.private, true);
  assert.equal(manifest.license, "UNLICENSED");
  assert.deepEqual(manifest.dependencies, {
    "@gltf-transform/core": "4.4.2",
    "@matrix-oasis/prototype-spatial-environment": "0.1.0-r11",
    "@matrix-oasis/runtime-pack-contracts": "0.1.0-r3",
    "@matrix-oasis/scene-pack-validator": "0.1.0-r7",
    "@playcanvas/splat-transform": "3.3.0",
  });
  assert.deepEqual(PROTOTYPE_SPATIAL_ASSEMBLY_PROFILE, {
    id: "matrix-oasis.prototype-spatial-assembly/1",
    panoramaVisible: false,
  });
});

test("spatial assembly source is offline and cannot hide calibration or a panorama fallback", async () => {
  const source = await readFile(new URL("src/index.mjs", packageRoot), "utf8");
  for (const forbidden of [
    "fetch(",
    ["node", ":http"].join(""),
    ["node", ":https"].join(""),
    ["process", ".env"].join(""),
    "environment-panorama.png",
    "last-train",
  ]) {
    assert.equal(source.includes(forbidden), false, forbidden);
  }
  for (const required of [
    "groundPlaneOffsetMm",
    "godotRotationMilliDegrees",
    "rendererCenterCompensationMm",
    "metricScaleMicros",
    "localRotationMilliDegrees",
    "eulerOrder",
    "panoramaVisible: false",
  ]) {
    assert.equal(source.includes(required), true, required);
  }
});
