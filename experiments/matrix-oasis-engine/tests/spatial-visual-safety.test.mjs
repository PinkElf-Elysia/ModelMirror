import assert from "node:assert/strict";
import test from "node:test";
import { deriveVisualSafetyFromPositions } from "../packages/prototype-spatial-verifier/src/visual-safety.mjs";

function source({ sparse = false } = {}) {
  const values = [];
  if (sparse) return new Float32Array([0, 0, 0]);
  for (let index = 0; index < 100; index += 1) {
    values.push(((index % 20) - 10) * 0.4, 0, (Math.floor(index / 20) - 2) * 0.4);
  }
  for (const x of [0.125, 0.375, 0.625]) {
    for (const y of [-0.5, -1.0, -1.5]) {
      for (let index = 0; index < 30; index += 1) values.push(x, y, 0.125);
    }
  }
  return new Float32Array(values);
}

function assembly() {
  return {
    transforms: {
      root: { translationMm: [1000, 0, 2000], rotationMilliDegrees: [0, 0, 0] },
      splat: { localTranslationMm: [0, 0, 0], localRotationMilliDegrees: [180000, 0, 0], scaleMicros: 1000000 },
      walkableEnvelope: { minimumMm: [-10000, 0, -10000], maximumMm: [10000, 4000, 10000] },
    },
  };
}

function facts() {
  return {
    navigationMesh: {
      verticesMm: [[-5000, 0, -5000], [-5000, 0, 5000], [5000, 0, 5000], [5000, 0, -5000]],
      polygons: [{ vertexIndices: [0, 1, 2, 3] }],
    },
  };
}

test("dense vertical Gaussian evidence becomes deterministic world-space safety boxes", () => {
  const input = {
    positions: source(), spatialAssembly: assembly(), environmentFacts: facts(),
    selectedPolygonIndices: [0], runtimeSupportHeightMm: 0,
  };
  const first = deriveVisualSafetyFromPositions(input);
  const repeated = deriveVisualSafetyFromPositions(input);
  assert.deepEqual(repeated, first);
  assert.equal(first.visualRegistrationOffsetMm, 0);
  assert.equal(first.boxes.length, 1);
  assert.deepEqual(first.boxes[0], {
    centerMm: [1375, 1500, 1875],
    sizeMm: [750, 3000, 250],
  });
});

test("sparse Gaussian evidence does not invent a visual wall", () => {
  const result = deriveVisualSafetyFromPositions({
    positions: source({ sparse: true }), spatialAssembly: assembly(), environmentFacts: facts(),
    selectedPolygonIndices: [0], runtimeSupportHeightMm: 0,
  });
  assert.equal(result.visualRegistrationOffsetMm, 0);
  assert.deepEqual(result.boxes, []);
});
