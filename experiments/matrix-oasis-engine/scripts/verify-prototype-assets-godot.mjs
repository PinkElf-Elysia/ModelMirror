import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { compileAuthoringGamePackJson } from "@matrix-oasis/game-pack-compiler";
import { validatePrototypeAssetBundleJson } from "@matrix-oasis/prototype-asset-contracts";
import {
  materializePrototypeAssetBundle,
  planPrototypeAssets,
} from "@matrix-oasis/prototype-asset-pipeline";
import { canonicalizeJsonValue as canonicalizeRuntimeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { canonicalizeJsonValue as canonicalizeSceneJsonValue } from "@matrix-oasis/scene-pack-contracts";
import { validateScenePackJson } from "@matrix-oasis/scene-pack-validator";
import { normalizePrototypeGlb } from "../packages/prototype-asset-pipeline/src/glb-normalizer.mjs";
import {
  assertGodotOutputClean,
  projectPath,
  resolveGodotBinary,
  runGodotCommand,
} from "./lib/godot-core.mjs";
import { sceneGodotArguments } from "./lib/godot-scene-core.mjs";
import { validateSceneBundle } from "./lib/scene-pack-bundle-core.mjs";

const PROTOTYPE_FILES = Object.freeze({
  authoringGamePackJson: Object.freeze(["authoring-game-pack.json", 1024 * 1024]),
  sceneBlueprintJson: Object.freeze(["scene-blueprint.json", 256 * 1024]),
  runtimeGamePackJson: Object.freeze(["runtime-game-pack.json", 16 * 1024 * 1024]),
  runtimeReceiptJson: Object.freeze(["runtime-receipt.json", 16 * 1024]),
});
const OUTPUT_PREFIX = ".matrix-oasis-r9-godot-";
const FIXTURE_PREFIX = "matrix-oasis-r9-godot-fixture-";
const READY_MARKER = "MATRIX_OASIS_R7_SCENE_BINDING_READY";

export class PrototypeAssetGodotVerificationError extends Error {
  constructor(code) {
    super(code);
    this.name = "PrototypeAssetGodotVerificationError";
    this.code = code;
  }
}

function fail(code) {
  throw new PrototypeAssetGodotVerificationError(code);
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function hashBytes(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

function unprefixedHash(value) {
  return typeof value === "string" && /^sha256:[0-9a-f]{64}$/u.test(value)
    ? value.slice("sha256:".length)
    : null;
}

function samePath(left, right) {
  const a = path.resolve(left);
  const b = path.resolve(right);
  return process.platform === "win32" ? a.toLowerCase() === b.toLowerCase() : a === b;
}

function contained(root, candidate, allowRoot = false) {
  const relative = path.relative(root, candidate);
  return (allowRoot && relative === "") || (
    relative !== "" && relative !== ".." && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)
  );
}

function temporaryRoot(moduleRoot) {
  return process.platform === "win32" ? path.join(path.parse(moduleRoot).root, "tmp") : os.tmpdir();
}

function identity(candidate) {
  const stat = fs.lstatSync(candidate, { bigint: true });
  return Object.freeze({ dev: stat.dev, ino: stat.ino });
}

function assertOwnedDirectory(candidate, parent, expectedIdentity) {
  const stat = fs.lstatSync(candidate, { bigint: true });
  if (
    !contained(parent, candidate) || stat.isSymbolicLink() || !stat.isDirectory() ||
    stat.dev !== expectedIdentity.dev || stat.ino !== expectedIdentity.ino ||
    !samePath(fs.realpathSync(candidate), candidate)
  ) fail("PROTOTYPE_ASSET_GODOT_OUTPUT_INVALID");
}

function trustedDirectory(candidate, root, code) {
  if (typeof candidate !== "string" || !path.isAbsolute(candidate)) fail(code);
  const resolved = path.resolve(candidate);
  if (!contained(root, resolved)) fail(code);
  let current = root;
  try {
    for (const segment of path.relative(root, resolved).split(path.sep)) {
      current = path.join(current, segment);
      const stat = fs.lstatSync(current, { bigint: true });
      if (stat.isSymbolicLink() || !stat.isDirectory() || !samePath(fs.realpathSync(current), current)) fail(code);
    }
  } catch (error) {
    if (error instanceof PrototypeAssetGodotVerificationError) throw error;
    fail(code);
  }
  return resolved;
}

function safeRelative(value) {
  if (typeof value !== "string" || value.length === 0 || value.includes("\0") ||
      path.isAbsolute(value) || path.win32.isAbsolute(value) || path.posix.isAbsolute(value)) return false;
  const parts = value.replaceAll("\\", "/").split("/");
  return parts.every((part) => part.length > 0 && part !== "." && part !== "..");
}

function readStableFile(root, relative, maximum, code) {
  if (!safeRelative(relative)) fail(code);
  const candidate = path.resolve(root, ...relative.replaceAll("\\", "/").split("/"));
  if (!contained(root, candidate)) fail(code);
  let handle;
  try {
    const before = fs.lstatSync(candidate, { bigint: true });
    if (before.isSymbolicLink() || !before.isFile() || before.size < 1n || before.size > BigInt(maximum) ||
        !samePath(fs.realpathSync(candidate), candidate)) fail(code);
    handle = fs.openSync(candidate, "r");
    const opened = fs.fstatSync(handle, { bigint: true });
    if (opened.dev !== before.dev || opened.ino !== before.ino || opened.size !== before.size) fail(code);
    const bytes = fs.readFileSync(handle);
    const after = fs.fstatSync(handle, { bigint: true });
    if (after.dev !== before.dev || after.ino !== before.ino || after.size !== before.size || BigInt(bytes.length) !== after.size) fail(code);
    return bytes;
  } catch (error) {
    if (error instanceof PrototypeAssetGodotVerificationError) throw error;
    fail(code);
  } finally {
    if (handle !== undefined) fs.closeSync(handle);
  }
}

function decodeJson(bytes, code) {
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    fail(code);
  }
}

function placement({ id, visualAssetId, colliderAssetId, entityId = null, position, rotation = [0, 0, 0], scale = 1000 }) {
  return {
    id,
    visualAssetId,
    colliderAssetId,
    entityId,
    transform: {
      positionMm: position,
      rotationMilliDegrees: rotation,
      scalePermille: [scale, scale, scale],
    },
  };
}

function roleAsset(materialization, role) {
  const matches = materialization.assets.filter((asset) => asset.roles.includes(role));
  if (matches.length !== 1) fail("PROTOTYPE_ASSET_GODOT_LAYOUT_INVALID");
  return matches[0].id;
}

export function buildFixedPrototypeAssetScenePack({ blueprint, runtimePack, receipt, assetBundle }) {
  if (!blueprint || !runtimePack || !receipt || !assetBundle ||
      !Array.isArray(blueprint.assetBriefs) || !Array.isArray(blueprint.placements) ||
      !Array.isArray(blueprint.nodeBindings) || !Array.isArray(runtimePack.nodes) ||
      !Array.isArray(assetBundle.materializations)) fail("PROTOTYPE_ASSET_GODOT_INPUT_INVALID");
  const briefsByKind = new Map();
  for (const brief of blueprint.assetBriefs) {
    if (!["environment", "prop", "character-placeholder"].includes(brief.kind) || briefsByKind.has(brief.kind)) {
      fail("PROTOTYPE_ASSET_GODOT_LAYOUT_INVALID");
    }
    briefsByKind.set(brief.kind, brief);
  }
  if (briefsByKind.size !== 3 || blueprint.placements.length !== 3) fail("PROTOTYPE_ASSET_GODOT_LAYOUT_INVALID");
  const materializations = new Map(assetBundle.materializations.map((item) => [item.assetBriefId, item]));
  const logicalPlacements = new Map(blueprint.placements.map((item) => [item.id, item]));
  if (materializations.size !== 3 || logicalPlacements.size !== 3) fail("PROTOTYPE_ASSET_GODOT_LAYOUT_INVALID");
  const environment = materializations.get(briefsByKind.get("environment").id);
  const prop = materializations.get(briefsByKind.get("prop").id);
  const character = materializations.get(briefsByKind.get("character-placeholder").id);
  if (!environment || environment.source?.type !== "builtin-template" || environment.assets.length !== 2 ||
      !prop || prop.source?.type !== "meshy-text-to-3d" ||
      !character || character.source?.type !== "meshy-text-to-3d") fail("PROTOTYPE_ASSET_GODOT_LAYOUT_INVALID");
  const floor = environment.assets.find((asset) => asset.id.endsWith("-floor-square"));
  const wall = environment.assets.find((asset) => asset.id.endsWith("-wall"));
  if (!floor || !wall || !floor.roles.includes("visual") || !floor.roles.includes("collider") ||
      !wall.roles.includes("visual") || !wall.roles.includes("collider")) fail("PROTOTYPE_ASSET_GODOT_LAYOUT_INVALID");
  const propVisual = roleAsset(prop, "visual");
  const propCollider = roleAsset(prop, "collider");
  const characterVisual = roleAsset(character, "visual");
  const characterCollider = roleAsset(character, "collider");
  const environmentPlacement = blueprint.placements.find((item) => item.assetBriefId === briefsByKind.get("environment").id);
  const propPlacement = blueprint.placements.find((item) => item.assetBriefId === briefsByKind.get("prop").id);
  const characterPlacement = blueprint.placements.find((item) => item.assetBriefId === briefsByKind.get("character-placeholder").id);
  if (!environmentPlacement || !propPlacement || !characterPlacement) fail("PROTOTYPE_ASSET_GODOT_LAYOUT_INVALID");
  const placements = [
    placement({ id: "r9-floor", visualAssetId: floor.id, colliderAssetId: floor.id, position: [0, 0, -6000], scale: 30000 }),
    placement({ id: "r9-prop", visualAssetId: propVisual, colliderAssetId: propCollider, entityId: propPlacement.entityId, position: [4000, 0, -2000] }),
    placement({ id: "r9-character", visualAssetId: characterVisual, colliderAssetId: characterCollider, entityId: characterPlacement.entityId, position: [-4000, 0, -2000] }),
  ];
  for (let index = 0; index < 5; index += 1) {
    placements.push(placement({
      id: `r9-wall-north-${index}`,
      visualAssetId: wall.id,
      colliderAssetId: wall.id,
      position: [-12000 + index * 6000, 0, -20000],
      rotation: [0, 90000, 0],
      scale: 6000,
    }));
  }
  for (const side of [-1, 1]) {
    const sideName = side < 0 ? "west" : "east";
    for (let index = 0; index < 5; index += 1) {
      placements.push(placement({
        id: `r9-wall-${sideName}-${index}`,
        visualAssetId: wall.id,
        colliderAssetId: wall.id,
        position: [side * 15000, 0, -17000 + index * 6000],
        scale: 6000,
      }));
    }
  }
  const environmentIds = placements.map(({ id }) => id).filter((id) => !["r9-prop", "r9-character"].includes(id));
  const physicalByLogical = new Map([
    [environmentPlacement.id, environmentIds],
    [propPlacement.id, ["r9-prop"]],
    [characterPlacement.id, ["r9-character"]],
  ]);
  const sourceBindings = new Map(blueprint.nodeBindings.map((binding) => [binding.nodeId, binding]));
  if (sourceBindings.size !== runtimePack.nodes.length) fail("PROTOTYPE_ASSET_GODOT_LAYOUT_INVALID");
  const nodeBindings = runtimePack.nodes.map((node) => {
    const source = sourceBindings.get(node.id);
    if (!source) fail("PROTOTYPE_ASSET_GODOT_LAYOUT_INVALID");
    const visible = [];
    for (const logicalId of source.visiblePlacementIds) {
      const physical = physicalByLogical.get(logicalId);
      if (!physical) fail("PROTOTYPE_ASSET_GODOT_LAYOUT_INVALID");
      for (const id of physical) if (!visible.includes(id)) visible.push(id);
    }
    return {
      nodeId: node.id,
      playerSpawn: { positionMm: [0, 1000, 5000], yawMilliDegrees: 0 },
      actionAnchor: { positionMm: [0, 0, 2000], yawMilliDegrees: 0 },
      visiblePlacementIds: visible,
    };
  });
  const assets = assetBundle.materializations.flatMap((item) => item.assets).map((asset) => {
    const sha256 = unprefixedHash(asset.sha256);
    if (!sha256) fail("PROTOTYPE_ASSET_GODOT_INPUT_INVALID");
    return {
      id: asset.id,
      roles: [...asset.roles],
      path: asset.path,
      format: "glb",
      byteLength: asset.byteLength,
      sha256,
    };
  });
  return deepFreeze({
    format: "matrix-oasis.scene-pack",
    formatVersion: "0.1.0",
    canonicalization: "matrix-oasis.canonical-json/1",
    scene: { id: "r9-fixed-qualification-scene", contentVersion: assetBundle.scene.contentVersion, title: `${assetBundle.scene.title} · R9 Fixed Layout` },
    runtimeIdentity: {
      runtimeFormat: runtimePack.format,
      runtimeFormatVersion: runtimePack.formatVersion,
      packId: runtimePack.source.id,
      packContentVersion: runtimePack.source.contentVersion,
      sourceCanonicalSha256: runtimePack.source.canonicalSha256,
      artifactSha256: receipt.artifact.sha256,
    },
    assets,
    placements,
    nodeBindings,
  });
}

export function parsePrototypeAssetGodotArguments(args) {
  if (!Array.isArray(args)) fail("PROTOTYPE_ASSET_GODOT_ARGUMENT_INVALID");
  if (args.length === 0) return Object.freeze({ mode: "fixture" });
  if (args.length !== 6 || args[0] !== "--prototype-dir" || args[2] !== "--asset-bundle-dir" || args[4] !== "--output" ||
      args.slice(1).some((value, index) => index % 2 === 0 && (typeof value !== "string" || !path.isAbsolute(value) || value.includes("\0")))) {
    fail("PROTOTYPE_ASSET_GODOT_ARGUMENT_INVALID");
  }
  return Object.freeze({ mode: "external", prototypeDir: args[1], assetBundleDir: args[3], output: args[5] });
}

function exactValidReport(report) {
  return report?.reportVersion === 1 && report.valid === true && Array.isArray(report.diagnostics) && report.diagnostics.length === 0;
}

async function prepareExternalVerification({ moduleRoot, prototypeDir, assetBundleDir, output, godotCommand }) {
  const temp = fs.realpathSync(temporaryRoot(moduleRoot));
  const trustedPrototype = trustedDirectory(prototypeDir, temp, "PROTOTYPE_ASSET_GODOT_PROTOTYPE_INVALID");
  const trustedBundle = trustedDirectory(assetBundleDir, temp, "PROTOTYPE_ASSET_GODOT_BUNDLE_INVALID");
  if (typeof output !== "string" || !path.isAbsolute(output) || !contained(temp, path.resolve(output)) ||
      path.dirname(path.resolve(output)) !== temp || fs.existsSync(output)) fail("PROTOTYPE_ASSET_GODOT_OUTPUT_INVALID");
  const texts = Object.create(null);
  for (const [key, [name, maximum]] of Object.entries(PROTOTYPE_FILES)) {
    const bytes = readStableFile(trustedPrototype, name, maximum, "PROTOTYPE_ASSET_GODOT_PROTOTYPE_INVALID");
    try { texts[key] = new TextDecoder("utf-8", { fatal: true }).decode(bytes); } catch { fail("PROTOTYPE_ASSET_GODOT_PROTOTYPE_INVALID"); }
  }
  const plan = await planPrototypeAssets(texts);
  if (!plan?.ok) fail("PROTOTYPE_ASSET_GODOT_PROTOTYPE_INVALID");
  const bundleBytes = readStableFile(trustedBundle, "prototype-asset-bundle.json", 256 * 1024, "PROTOTYPE_ASSET_GODOT_BUNDLE_INVALID");
  let bundleText;
  try { bundleText = new TextDecoder("utf-8", { fatal: true }).decode(bundleBytes); } catch { fail("PROTOTYPE_ASSET_GODOT_BUNDLE_INVALID"); }
  const bundleReport = validatePrototypeAssetBundleJson(bundleText);
  if (!exactValidReport(bundleReport)) fail("PROTOTYPE_ASSET_GODOT_BUNDLE_INVALID");
  const blueprint = decodeJson(new TextEncoder().encode(texts.sceneBlueprintJson), "PROTOTYPE_ASSET_GODOT_PROTOTYPE_INVALID");
  const runtimePack = decodeJson(new TextEncoder().encode(texts.runtimeGamePackJson), "PROTOTYPE_ASSET_GODOT_PROTOTYPE_INVALID");
  const receipt = decodeJson(new TextEncoder().encode(texts.runtimeReceiptJson), "PROTOTYPE_ASSET_GODOT_PROTOTYPE_INVALID");
  const assetBundle = decodeJson(bundleBytes, "PROTOTYPE_ASSET_GODOT_BUNDLE_INVALID");
  const plannedBriefs = plan.plan.blueprint.assetBriefs.map(({ id, kind, entityId, roles }) => ({ id, kind, entityId, roles }));
  if (
    canonicalizeRuntimeJsonValue(assetBundle.scene) !== canonicalizeRuntimeJsonValue(plan.plan.scene) ||
    canonicalizeRuntimeJsonValue(assetBundle.blueprint.assetBriefs) !== canonicalizeRuntimeJsonValue(plannedBriefs) ||
    assetBundle.blueprint.canonicalSha256 !== plan.plan.blueprint.canonicalSha256 ||
    assetBundle.blueprint.canonicalSha256 !== `sha256:${hashBytes(texts.sceneBlueprintJson)}` ||
    assetBundle.runtimeIdentity.artifactSha256 !== `sha256:${receipt.artifact.sha256}` ||
    assetBundle.runtimeIdentity.authoringCanonicalSha256 !== `sha256:${runtimePack.source.canonicalSha256}` ||
    assetBundle.runtimeIdentity.id !== runtimePack.source.id ||
    assetBundle.runtimeIdentity.contentVersion !== runtimePack.source.contentVersion
  ) fail("PROTOTYPE_ASSET_GODOT_IDENTITY_INVALID");
  const scenePack = buildFixedPrototypeAssetScenePack({ blueprint, runtimePack, receipt, assetBundle });
  const sceneText = canonicalizeSceneJsonValue(scenePack);
  if (!exactValidReport(await validateScenePackJson(sceneText, texts.runtimeGamePackJson, texts.runtimeReceiptJson))) {
    fail("PROTOTYPE_ASSET_GODOT_SCENE_INVALID");
  }
  const staging = fs.mkdtempSync(path.join(temp, OUTPUT_PREFIX));
  const stagingIdentity = identity(staging);
  try {
    const inputsRoot = path.join(staging, "inputs");
    const projectRoot = path.join(staging, "runtime-godot");
    fs.mkdirSync(inputsRoot);
    fs.cpSync(projectPath(moduleRoot), projectRoot, { recursive: true, filter: (source) => path.basename(source) !== ".godot" });
    fs.writeFileSync(path.join(inputsRoot, "runtime.json"), texts.runtimeGamePackJson, { encoding: "utf8", flag: "wx" });
    fs.writeFileSync(path.join(inputsRoot, "receipt.json"), texts.runtimeReceiptJson, { encoding: "utf8", flag: "wx" });
    fs.writeFileSync(path.join(inputsRoot, "scene.json"), sceneText, { encoding: "utf8", flag: "wx" });
    for (const asset of scenePack.assets) {
      const bytes = readStableFile(trustedBundle, asset.path, 32 * 1024 * 1024, "PROTOTYPE_ASSET_GODOT_ASSET_INVALID");
      if (bytes.length !== asset.byteLength || hashBytes(bytes) !== asset.sha256) fail("PROTOTYPE_ASSET_GODOT_ASSET_INVALID");
      const target = path.join(inputsRoot, ...asset.path.split("/"));
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, bytes, { flag: "wx" });
    }
    const validated = await validateSceneBundle({
      moduleRoot: staging,
      scenePath: "inputs/scene.json",
      runtimePackPath: "inputs/runtime.json",
      runtimeReceiptPath: "inputs/receipt.json",
    });
    if (!exactValidReport(validated)) fail("PROTOTYPE_ASSET_GODOT_SCENE_INVALID");
    const imported = runGodotCommand({ command: godotCommand, args: ["--headless", "--editor", "--path", projectRoot, "--quit"], cwd: moduleRoot, timeout: 120_000 });
    assertGodotOutputClean(imported);
    const smoke = runGodotCommand({
      command: godotCommand,
      args: sceneGodotArguments({
        projectRoot,
        runtimePath: path.join(inputsRoot, "runtime.json"),
        receiptPath: path.join(inputsRoot, "receipt.json"),
        scenePath: path.join(inputsRoot, "scene.json"),
        smoke: true,
      }),
      cwd: moduleRoot,
      timeout: 60_000,
    });
    assertGodotOutputClean(smoke);
    if (smoke.split(READY_MARKER).length - 1 !== 1) fail("PROTOTYPE_ASSET_GODOT_SMOKE_INVALID");
    assertOwnedDirectory(staging, temp, stagingIdentity);
    fs.renameSync(staging, output);
    assertOwnedDirectory(path.resolve(output), temp, stagingIdentity);
    return Object.freeze({ output: path.resolve(output), sceneSha256: hashBytes(sceneText), assets: scenePack.assets.length, placements: scenePack.placements.length });
  } catch (error) {
    if (fs.existsSync(staging)) {
      try { assertOwnedDirectory(staging, temp, stagingIdentity); fs.rmSync(staging, { recursive: true }); } catch { /* preserve untrusted staging */ }
    }
    if (error instanceof PrototypeAssetGodotVerificationError) throw error;
    fail("PROTOTYPE_ASSET_GODOT_INTERNAL_ERROR");
  }
}

async function createFixture(moduleRoot) {
  const temp = fs.realpathSync(temporaryRoot(moduleRoot));
  const root = fs.mkdtempSync(path.join(temp, FIXTURE_PREFIX));
  const rootIdentity = identity(root);
  const prototypeDir = path.join(root, "prototype");
  const assetBundleDir = path.join(root, "bundle");
  fs.mkdirSync(prototypeDir);
  fs.mkdirSync(path.join(assetBundleDir, "assets"), { recursive: true });
  try {
    const authoringSource = fs.readFileSync(path.join(moduleRoot, "examples", "mechanics-conformance.authoring-game-pack.json"), "utf8");
    const authoring = JSON.parse(authoringSource);
    const authoringGamePackJson = canonicalizeRuntimeJsonValue(authoring);
    const compiled = await compileAuthoringGamePackJson(authoringGamePackJson);
    if (!compiled?.ok) fail("PROTOTYPE_ASSET_GODOT_FIXTURE_INVALID");
    const placementIds = ["fixture-room-placement", "fixture-prop-placement", "fixture-character-placement"];
    const sceneBlueprintJson = canonicalizeRuntimeJsonValue({
      format: "matrix-oasis.scene-blueprint",
      formatVersion: "0.1.0",
      scene: { id: authoring.id, contentVersion: authoring.contentVersion, title: authoring.title, environmentPrompt: "Neutral room.", visualStylePrompt: "Neutral validation geometry." },
      zones: [{ id: "fixture-zone", label: "Fixture", description: "Fixed offline validation zone." }],
      assetBriefs: [
        { id: "fixture-room", kind: "environment", prompt: "Neutral room.", entityId: null, roles: ["visual", "collider"] },
        { id: "fixture-prop", kind: "prop", prompt: "Neutral object.", entityId: "control-unit", roles: ["visual", "collider"] },
        { id: "fixture-character", kind: "character-placeholder", prompt: "Neutral static character.", entityId: "actor-unit", roles: ["visual", "collider"] },
      ],
      placements: [
        { id: placementIds[0], assetBriefId: "fixture-room", zoneId: "fixture-zone", entityId: null },
        { id: placementIds[1], assetBriefId: "fixture-prop", zoneId: "fixture-zone", entityId: "control-unit" },
        { id: placementIds[2], assetBriefId: "fixture-character", zoneId: "fixture-zone", entityId: "actor-unit" },
      ],
      nodeBindings: authoring.nodes.map((node) => ({ nodeId: node.id, zoneId: "fixture-zone", visiblePlacementIds: placementIds })),
    });
    const runtimeReceiptJson = canonicalizeRuntimeJsonValue(compiled.receipt);
    const inputs = { authoringGamePackJson, sceneBlueprintJson, runtimeGamePackJson: compiled.canonicalJson, runtimeReceiptJson };
    const plan = await planPrototypeAssets(inputs);
    if (!plan?.ok) fail("PROTOTYPE_ASSET_GODOT_FIXTURE_INVALID");
    const environmentRoot = path.join(moduleRoot, "examples", "scene-bundles", "kenney-prototype", "assets");
    const texture = fs.readFileSync(path.join(environmentRoot, "Textures", "colormap.png"));
    const crate = fs.readFileSync(path.join(environmentRoot, "crate.glb"));
    const embedded = await normalizePrototypeGlb(crate, { kind: "prop", role: "visual", externalResources: new Map([["Textures/colormap.png", texture]]) });
    if (!embedded.ok) fail("PROTOTYPE_ASSET_GODOT_FIXTURE_INVALID");
    const materialized = await materializePrototypeAssetBundle({
      plan,
      acquiredAssets: new Map([["fixture-prop", embedded.bytes], ["fixture-character", embedded.bytes]]),
      environmentAssets: new Map([
        ["floor-square", fs.readFileSync(path.join(environmentRoot, "floor-square.glb"))],
        ["wall", fs.readFileSync(path.join(environmentRoot, "wall.glb"))],
      ]),
      environmentTexture: new Uint8Array(texture),
    });
    if (!materialized?.ok) fail("PROTOTYPE_ASSET_GODOT_FIXTURE_INVALID");
    for (const [name, text] of Object.entries({
      "authoring-game-pack.json": authoringGamePackJson,
      "scene-blueprint.json": sceneBlueprintJson,
      "runtime-game-pack.json": compiled.canonicalJson,
      "runtime-receipt.json": runtimeReceiptJson,
    })) fs.writeFileSync(path.join(prototypeDir, name), text, { encoding: "utf8", flag: "wx" });
    fs.writeFileSync(path.join(assetBundleDir, "prototype-asset-bundle.json"), materialized.canonicalBundleJson, { encoding: "utf8", flag: "wx" });
    for (const file of materialized.files) {
      const target = path.join(assetBundleDir, ...file.path.split("/"));
      fs.writeFileSync(target, file.bytes, { flag: "wx" });
    }
    return Object.freeze({ root, rootIdentity, prototypeDir, assetBundleDir });
  } catch (error) {
    try { assertOwnedDirectory(root, temp, rootIdentity); fs.rmSync(root, { recursive: true }); } catch { /* preserve primary failure */ }
    if (error instanceof PrototypeAssetGodotVerificationError) throw error;
    fail("PROTOTYPE_ASSET_GODOT_FIXTURE_INVALID");
  }
}

function removeOwned(candidate, moduleRoot, expectedIdentity, prefix) {
  const temp = fs.realpathSync(temporaryRoot(moduleRoot));
  if (!path.basename(candidate).startsWith(prefix)) fail("PROTOTYPE_ASSET_GODOT_OUTPUT_INVALID");
  assertOwnedDirectory(candidate, temp, expectedIdentity);
  fs.rmSync(candidate, { recursive: true });
}

export async function executePrototypeAssetGodotVerification({ args, moduleRoot, godotCommand }) {
  const parsed = parsePrototypeAssetGodotArguments(args);
  if (parsed.mode === "external") {
    return prepareExternalVerification({ moduleRoot, ...parsed, godotCommand });
  }
  const fixture = await createFixture(moduleRoot);
  const output = path.join(temporaryRoot(moduleRoot), `${FIXTURE_PREFIX}output-${process.pid}-${Date.now()}`);
  try {
    const report = await prepareExternalVerification({ moduleRoot, prototypeDir: fixture.prototypeDir, assetBundleDir: fixture.assetBundleDir, output, godotCommand });
    const outputIdentity = identity(report.output);
    removeOwned(report.output, moduleRoot, outputIdentity, FIXTURE_PREFIX);
    return Object.freeze({ ...report, output: null });
  } finally {
    if (fs.existsSync(fixture.root)) removeOwned(fixture.root, moduleRoot, fixture.rootIdentity, FIXTURE_PREFIX);
  }
}

async function main() {
  const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
  try {
    const godot = resolveGodotBinary();
    const report = await executePrototypeAssetGodotVerification({ args: process.argv.slice(2), moduleRoot, godotCommand: godot.command });
    console.log(`PROTOTYPE_ASSET_GODOT_OK version=${godot.version} assets=${report.assets} placements=${report.placements} sceneSha256=${report.sceneSha256}`);
  } catch (error) {
    const code = error instanceof PrototypeAssetGodotVerificationError && /^[A-Z][A-Z0-9_]+$/u.test(error.code)
      ? error.code
      : "PROTOTYPE_ASSET_GODOT_INTERNAL_ERROR";
    console.error(code);
    process.exitCode = code === "PROTOTYPE_ASSET_GODOT_ARGUMENT_INVALID" ? 2 : 1;
  }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) await main();
