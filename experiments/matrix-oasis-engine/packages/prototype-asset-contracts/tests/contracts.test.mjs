import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import * as contract from "../src/index.mjs";

const hash = (character) => `sha256:${character.repeat(64)}`;

function metrics(triangleCount = 120) {
  return {
    nodeCount: 1,
    meshCount: 1,
    surfaceCount: 1,
    triangleCount,
    maxTextureWidth: 1024,
    maxTextureHeight: 1024,
    boundsMm: { min: [-500, 0, -500], max: [500, 1000, 500] },
  };
}

function file(id, roles, normalizationProfile, triangleCount = 120) {
  return {
    id,
    path: `assets/${id}.glb`,
    format: "glb",
    roles,
    normalizationProfile,
    byteLength: 4096,
    sha256: hash("e"),
    metrics: metrics(triangleCount),
  };
}

function validBundle() {
  const briefs = [
    {
      id: "room",
      kind: "environment",
      entityId: null,
      roles: ["visual", "collider"],
    },
    {
      id: "crate",
      kind: "prop",
      entityId: "object-crate",
      roles: ["visual", "collider"],
    },
    {
      id: "guide",
      kind: "character-placeholder",
      entityId: "person-guide",
      roles: ["visual"],
    },
  ];
  return {
    format: "matrix-oasis.prototype-asset-bundle",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    scene: { id: "prototype", contentVersion: "1.0.0", title: "Prototype" },
    blueprint: {
      format: "matrix-oasis.scene-blueprint",
      formatVersion: "0.1.0",
      canonicalSha256: hash("b"),
      assetBriefs: briefs,
    },
    runtimeIdentity: {
      format: "matrix-oasis.runtime-game-pack",
      formatVersion: "0.1.0",
      id: "prototype",
      contentVersion: "1.0.0",
      authoringCanonicalSha256: hash("c"),
      artifactSha256: hash("d"),
    },
    environmentTemplate: "kenney-prototype-room-v1",
    materializations: [
      {
        assetBriefId: "room",
        source: {
          type: "builtin-template",
          template: "kenney-prototype-room-v1",
        },
        assets: [
          file(
            "room-shell",
            ["visual", "collider"],
            "kenney-prototype-room-v1",
          ),
        ],
      },
      {
        assetBriefId: "crate",
        source: {
          type: "meshy-text-to-3d",
          provider: "meshy",
          model: "meshy-6",
        },
        assets: [
          file("crate-visual", ["visual"], "matrix-oasis.glb-normalization/1"),
          file("crate-collider", ["collider"], "matrix-oasis.glb-normalization/1"),
        ],
      },
      {
        assetBriefId: "guide",
        source: {
          type: "meshy-text-to-3d",
          provider: "meshy",
          model: "meshy-6",
        },
        assets: [
          file("guide-visual", ["visual"], "matrix-oasis.glb-normalization/1"),
        ],
      },
    ],
  };
}

const clone = (value) => structuredClone(value);
const serializeForValidation = (value) => {
  try {
    return canonicalizeJsonValue(value);
  } catch {
    return JSON.stringify(value);
  }
};
const validate = (value) =>
  contract.validatePrototypeAssetBundleJson(serializeForValidation(value));
const has = (report, code, path) =>
  report.diagnostics.some((item) => item.code === code && item.path === path);

test("public surface and frozen schema are exact", () => {
  assert.deepEqual(Object.keys(contract).sort(), [
    "PROTOTYPE_ASSET_BUNDLE_FORMAT",
    "PROTOTYPE_ASSET_BUNDLE_FORMAT_VERSION",
    "PROTOTYPE_ASSET_BUNDLE_SCHEMA",
    "PROTOTYPE_ASSET_CANONICALIZATION",
    "PROTOTYPE_ASSET_ENVIRONMENT_TEMPLATE",
    "PROTOTYPE_ASSET_LIMITS",
    "PROTOTYPE_ASSET_NORMALIZATION_PROFILE",
    "PrototypeAssetContractOperationalError",
    "validatePrototypeAssetBundleJson",
  ].sort());
  const stack = [contract.PROTOTYPE_ASSET_BUNDLE_SCHEMA];
  while (stack.length > 0) {
    const value = stack.pop();
    assert.equal(Object.isFrozen(value), true);
    for (const child of Object.values(value)) {
      if (child && typeof child === "object") stack.push(child);
    }
  }
});

test("canonical valid bundle is stable, frozen and non-mutating", () => {
  const bundle = validBundle();
  const before = structuredClone(bundle);
  const text = canonicalizeJsonValue(bundle);
  const serialized = [];
  for (let index = 0; index < 20; index += 1) {
    const report = contract.validatePrototypeAssetBundleJson(text);
    assert.deepEqual(report, { reportVersion: 1, valid: true, diagnostics: [] });
    assert.equal(Object.isFrozen(report), true);
    assert.equal(Object.isFrozen(report.diagnostics), true);
    serialized.push(JSON.stringify(report));
  }
  assert.equal(new Set(serialized).size, 1);
  assert.deepEqual(bundle, before);
});

test("non-canonical text and unsafe syntax fail at their gates", () => {
  const canonical = canonicalizeJsonValue(validBundle());
  assert.equal(
    has(
      contract.validatePrototypeAssetBundleJson(`${canonical}\n`),
      "PROTOTYPE_ASSET_BUNDLE_JSON_NON_CANONICAL",
      "",
    ),
    true,
  );
  assert.equal(
    has(
      contract.validatePrototypeAssetBundleJson("{/*x*/}"),
      "PROTOTYPE_ASSET_BUNDLE_JSON_SYNTAX",
      "",
    ),
    true,
  );
});

test("duplicate and unknown secret-like keys never echo key names or values", () => {
  const secretKey = ["api", "Token", "NeverEcho"].join("");
  const duplicate = `{"${secretKey}":1,"${secretKey}":2}`;
  const duplicateReport = contract.validatePrototypeAssetBundleJson(duplicate);
  assert.equal(has(duplicateReport, "PROTOTYPE_ASSET_BUNDLE_JSON_DUPLICATE_KEY", ""), true);
  assert.equal(JSON.stringify(duplicateReport).includes(secretKey), false);

  const bundle = validBundle();
  bundle[secretKey] = "sensitive-value-never-echo";
  const unknownReport = validate(bundle);
  assert.equal(has(unknownReport, "PROTOTYPE_ASSET_BUNDLE_SCHEMA_UNKNOWN_PROPERTY", ""), true);
  assert.equal(JSON.stringify(unknownReport).includes(secretKey), false);
  assert.equal(JSON.stringify(unknownReport).includes(bundle[secretKey]), false);
});

test("schema rejects missing, float, forbidden path and supplier fields", () => {
  const cases = [
    [
      (bundle) => delete bundle.runtimeIdentity,
      "PROTOTYPE_ASSET_BUNDLE_SCHEMA_REQUIRED",
      "/runtimeIdentity",
    ],
    [
      (bundle) => { bundle.materializations[0].assets[0].byteLength = 1.5; },
      "PROTOTYPE_ASSET_BUNDLE_SCHEMA_TYPE",
      "/materializations/0/assets/0/byteLength",
    ],
    [
      (bundle) => { bundle.materializations[0].assets[0].path = "../outside.glb"; },
      "PROTOTYPE_ASSET_BUNDLE_SCHEMA_STRING_CONSTRAINT",
      "/materializations/0/assets/0/path",
    ],
    [
      (bundle) => { bundle.materializations[1].source.taskId = "never-persist"; },
      "PROTOTYPE_ASSET_BUNDLE_SCHEMA_UNKNOWN_PROPERTY",
      "/materializations/1/source",
    ],
  ];
  for (const [mutate, code, path] of cases) {
    const bundle = validBundle();
    mutate(bundle);
    assert.equal(has(validate(bundle), code, path), true, `${code} ${path}`);
  }
});

test("schema failure blocks semantic and integrity diagnostics", () => {
  const bundle = validBundle();
  delete bundle.format;
  bundle.scene.id = "different";
  const report = validate(bundle);
  assert.equal(report.diagnostics.every((item) => item.phase === "schema"), true);
});

test("unpaired surrogate text is rejected without replacement", () => {
  const bundle = validBundle();
  bundle.scene.title = String.fromCharCode(0xd800);
  const report = validate(bundle);
  assert.equal(
    has(report, "PROTOTYPE_ASSET_BUNDLE_TEXT_UNPAIRED_SURROGATE", "/scene/title"),
    true,
  );
});

test("unsafe integers are rejected by bounded numeric schema fields", () => {
  const bundle = validBundle();
  bundle.materializations[0].assets[0].byteLength = Number.MAX_SAFE_INTEGER + 1;
  const report = contract.validatePrototypeAssetBundleJson(JSON.stringify(bundle));
  assert.equal(
    has(
      report,
      "PROTOTYPE_ASSET_BUNDLE_SCHEMA_NUMBER_CONSTRAINT",
      "/materializations/0/assets/0/byteLength",
    ),
    true,
  );
  assert.equal(report.diagnostics.every((item) => item.phase === "schema"), true);
});

test("identity, brief and materialization invariants are enforced", () => {
  const cases = [
    [
      (bundle) => { bundle.runtimeIdentity.id = "other"; },
      "PROTOTYPE_ASSET_RUNTIME_IDENTITY_MISMATCH",
      "/runtimeIdentity/id",
    ],
    [
      (bundle) => { bundle.blueprint.assetBriefs[1].id = "room"; },
      "PROTOTYPE_ASSET_BRIEF_ID_DUPLICATE",
      "/blueprint/assetBriefs/1/id",
    ],
    [
      (bundle) => { bundle.blueprint.assetBriefs[0].kind = "prop"; },
      "PROTOTYPE_ASSET_ENVIRONMENT_BRIEF_COUNT",
      "/blueprint/assetBriefs",
    ],
    [
      (bundle) => { bundle.blueprint.assetBriefs[1].entityId = null; },
      "PROTOTYPE_ASSET_ENTITY_REQUIRED",
      "/blueprint/assetBriefs/1/entityId",
    ],
    [
      (bundle) => { bundle.materializations.splice(1, 1); },
      "PROTOTYPE_ASSET_MATERIALIZATION_MISSING",
      "/blueprint/assetBriefs/1/id",
    ],
    [
      (bundle) => { bundle.materializations[1].assetBriefId = "missing"; },
      "PROTOTYPE_ASSET_BRIEF_REFERENCE_NOT_FOUND",
      "/materializations/1/assetBriefId",
    ],
    [
      (bundle) => { bundle.materializations.reverse(); },
      "PROTOTYPE_ASSET_MATERIALIZATION_ORDER_INVALID",
      "/materializations/0/assetBriefId",
    ],
  ];
  for (const [mutate, code, path] of cases) {
    const bundle = validBundle();
    mutate(bundle);
    assert.equal(has(validate(bundle), code, path), true, `${code} ${path}`);
  }
});

test("source, profile, roles, bounds and collider limits are enforced", () => {
  const cases = [
    [
      (bundle) => { bundle.materializations[1].source = bundle.materializations[0].source; },
      "PROTOTYPE_ASSET_SOURCE_KIND_MISMATCH",
      "/materializations/1/source/type",
    ],
    [
      (bundle) => { bundle.materializations[1].assets[0].normalizationProfile = "kenney-prototype-room-v1"; },
      "PROTOTYPE_ASSET_NORMALIZATION_PROFILE_MISMATCH",
      "/materializations/1/assets/0/normalizationProfile",
    ],
    [
      (bundle) => { bundle.materializations[1].assets.pop(); },
      "PROTOTYPE_ASSET_ROLE_COVERAGE_MISMATCH",
      "/materializations/1/assets",
    ],
    [
      (bundle) => { bundle.materializations[1].assets[1].metrics.triangleCount = 10_001; },
      "PROTOTYPE_ASSET_COLLIDER_TRIANGLE_LIMIT",
      "/materializations/1/assets/1/metrics/triangleCount",
    ],
    [
      (bundle) => { bundle.materializations[1].assets[0].metrics.boundsMm.min[0] = 501; },
      "PROTOTYPE_ASSET_BOUNDS_INVALID",
      "/materializations/1/assets/0/metrics/boundsMm",
    ],
    [
      (bundle) => { bundle.blueprint.assetBriefs[1].roles = ["collider", "visual"]; },
      "PROTOTYPE_ASSET_ROLE_ORDER_INVALID",
      "/blueprint/assetBriefs/1/roles",
    ],
  ];
  for (const [mutate, code, path] of cases) {
    const bundle = validBundle();
    mutate(bundle);
    assert.equal(has(validate(bundle), code, path), true, `${code} ${path}`);
  }
});

test("file identifiers, paths and aggregate budgets are enforced", () => {
  const duplicateId = validBundle();
  duplicateId.materializations[1].assets[1].id = "crate-visual";
  assert.equal(
    has(validate(duplicateId), "PROTOTYPE_ASSET_FILE_ID_DUPLICATE", "/materializations/1/assets/1/id"),
    true,
  );

  const duplicatePath = validBundle();
  duplicatePath.materializations[1].assets[1].path = "assets/crate-visual.glb";
  assert.equal(
    has(validate(duplicatePath), "PROTOTYPE_ASSET_FILE_PATH_DUPLICATE", "/materializations/1/assets/1/path"),
    true,
  );

  const total = validBundle();
  total.materializations[0].assets = Array.from({ length: 5 }, (_, index) => {
    const item = file(
      `room-${index}`,
      ["visual", "collider"],
      "kenney-prototype-room-v1",
    );
    item.byteLength = 32 * 1024 * 1024;
    return item;
  });
  assert.equal(
    has(validate(total), "PROTOTYPE_ASSET_TOTAL_BYTES_EXCEEDED", "/materializations"),
    true,
  );
});

test("zero files is rejected while one and sixteen files are accepted", () => {
  const one = validBundle();
  one.blueprint.assetBriefs = [one.blueprint.assetBriefs[0]];
  one.materializations = [one.materializations[0]];
  assert.equal(validate(one).valid, true);

  const sixteen = clone(one);
  sixteen.materializations[0].assets = Array.from({ length: 16 }, (_, index) =>
    file(
      `room-${index}`,
      ["visual", "collider"],
      "kenney-prototype-room-v1",
    ));
  assert.equal(validate(sixteen).valid, true);

  const zero = clone(one);
  zero.materializations[0].assets = [];
  assert.equal(
    has(validate(zero), "PROTOTYPE_ASSET_BUNDLE_SCHEMA_MIN_ITEMS", "/materializations/0/assets"),
    true,
  );
});

test("declaration file exposes the contract without filesystem or supplier handles", async () => {
  const source = await readFile(new URL("../src/index.d.ts", import.meta.url), "utf8");
  assert.match(source, /interface PrototypeAssetBundle/);
  assert.match(source, /validatePrototypeAssetBundleJson/);
  for (const forbidden of ["taskId", "downloadUrl", "apiKey", "absolutePath"]) {
    assert.equal(source.includes(forbidden), false);
  }
});
