import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { validatePrototypeAssetBundleJson } from "@matrix-oasis/prototype-asset-contracts";
import { validatePrototypeSpatialAssemblyJson } from "@matrix-oasis/prototype-spatial-assembler";
import {
  validatePrototypeEnvironmentFactsJson,
  validatePrototypeSpatialIntentJson,
} from "@matrix-oasis/prototype-spatial-planning-contracts";
import { validatePrototypeSpatialSolutionJson } from "@matrix-oasis/prototype-spatial-solution-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { validateRuntimeGamePackJson } from "@matrix-oasis/runtime-pack-validator";
import { deriveVisualSafetyEvidence } from "./visual-safety.mjs";

const INTERNAL_CODE = "PROTOTYPE_SPATIAL_VERIFIER_INTERNAL_ERROR";
const VERIFIER_KIND = "matrix-oasis.godot-spatial-solution-verifier/1";
const READY_MARKER = "MATRIX_OASIS_R14_SPATIAL_VERIFICATION_READY";
const MAX_COLLIDER_BYTES = 32 * 1024 * 1024;
const MAX_SPLAT_BYTES = 96 * 1024 * 1024;
const MAX_ASSET_BYTES = 32 * 1024 * 1024;
const verifierState = new WeakMap();
const moduleRoot = path.resolve(fileURLToPath(new URL("../../..", import.meta.url)));
const projectRoot = path.join(moduleRoot, "apps", "runtime-godot");

const GODOT_FAILURE_CODES = new Set([
  "PROTOTYPE_SPATIAL_VERIFY_ASSET_GROUNDING_FAILED",
  "PROTOTYPE_SPATIAL_VERIFY_ASSET_OVERLAP",
  "PROTOTYPE_SPATIAL_VERIFY_ASSET_PENETRATION",
  "PROTOTYPE_SPATIAL_VERIFY_PATH_BLOCKED",
  "PROTOTYPE_SPATIAL_VERIFY_PATH_UNREACHABLE",
  "PROTOTYPE_SPATIAL_VERIFY_SPAWN_COLLISION",
  "PROTOTYPE_SPATIAL_VERIFY_TERMINAL_COLLISION",
  "PROTOTYPE_SPATIAL_VERIFY_TERMINAL_SIGHT_BLOCKED",
]);

const OPERATION_STAGES = new Set(["operation", "probe", "import", "verification", "result"]);
const PROCESS_FAILURES = new Set([
  "marker-missing", "nonzero-exit", "output-error", "output-limit", "signal", "spawn-error", "timeout", "unknown",
]);

export class PrototypeSpatialVerifierOperationalError extends Error {
  constructor(stage = "operation", processFailure = "unknown") {
    super(INTERNAL_CODE);
    this.name = "PrototypeSpatialVerifierOperationalError";
    this.code = INTERNAL_CODE;
    Object.defineProperty(this, "stage", {
      value: OPERATION_STAGES.has(stage) ? stage : "operation",
      enumerable: false,
      writable: false,
      configurable: false,
    });
    Object.defineProperty(this, "processFailure", {
      value: PROCESS_FAILURES.has(processFailure) ? processFailure : "unknown",
      enumerable: false,
      writable: false,
      configurable: false,
    });
  }
}

function operational(error) {
  return error instanceof PrototypeSpatialVerifierOperationalError
    ? error
    : new PrototypeSpatialVerifierOperationalError();
}

function classifyProcessFailure(result, output, { markerRequired = false } = {}) {
  if (!result || typeof result !== "object") return "unknown";
  if (result.error?.code === "ETIMEDOUT") return "timeout";
  if (result.error?.code === "ENOBUFS") return "output-limit";
  if (result.error) return "spawn-error";
  if (typeof result.signal === "string" && result.signal) return "signal";
  if (result.status !== 0) return "nonzero-exit";
  if (/(?:SCRIPT ERROR:|(?:^|\n)ERROR:)/u.test(output)) return "output-error";
  if (markerRequired && !hasSingleMarker(output)) return "marker-missing";
  return null;
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function exactRecord(value, keys) {
  if (!value || Object.getPrototypeOf(value) !== Object.prototype) return null;
  const descriptors = Object.getOwnPropertyDescriptors(value);
  if (Reflect.ownKeys(descriptors).some((key) => typeof key !== "string") || Object.keys(descriptors).length !== keys.length) return null;
  const output = {};
  for (const key of keys) {
    const descriptor = descriptors[key];
    if (!descriptor || !descriptor.enumerable || !("value" in descriptor)) return null;
    output[key] = descriptor.value;
  }
  return output;
}

function copyBytes(value, maximum) {
  if (!value || Object.getPrototypeOf(value) !== Uint8Array.prototype || value.byteLength < 1 || value.byteLength > maximum) return null;
  return Uint8Array.prototype.slice.call(value);
}

function copyAssetFiles(value) {
  if (!value || Object.getPrototypeOf(value) !== Map.prototype || value.size > 16) return null;
  const output = new Map();
  for (const [assetPath, bytes] of Map.prototype.entries.call(value)) {
    if (typeof assetPath !== "string" || output.has(assetPath)) return null;
    const copied = copyBytes(bytes, MAX_ASSET_BYTES);
    if (!copied) return null;
    output.set(assetPath, copied);
  }
  return output;
}

function sha256(value) {
  return `sha256:${createHash("sha256").update(value).digest("hex")}`;
}

function staticFailure(phase, code, pathValue) {
  return deepFreeze({
    ok: false,
    diagnostics: [{ phase, severity: "error", code, path: pathValue, message: code }],
  });
}

function parseCanonical(text) {
  const value = JSON.parse(text);
  if (canonicalizeJsonValue(value) !== text) throw new PrototypeSpatialVerifierOperationalError();
  return value;
}

function documentFailure(report, code, pathValue) {
  return report.valid ? null : staticFailure("input", code, pathValue);
}

function sameRuntimeIdentity(solution, runtimePack, receipt) {
  const identity = solution.source.runtime;
  return identity.format === runtimePack.format && identity.formatVersion === runtimePack.formatVersion &&
    identity.id === runtimePack.source.id && identity.contentVersion === runtimePack.source.contentVersion &&
    identity.sourceSha256 === `sha256:${runtimePack.source.canonicalSha256}` &&
    identity.artifactSha256 === `sha256:${receipt.artifact.sha256}`;
}

function collectPlacementAssets(intent, solution, assetBundle, files) {
  const intentPlacements = new Map(intent.placements.map((item) => [item.id, item]));
  const materializations = new Map(assetBundle.materializations.map((item) => [item.assetBriefId, item]));
  const declaredFiles = new Map(assetBundle.materializations.flatMap((item) => item.assets.map((asset) => [asset.path, asset])));
  for (const [assetPath, bytes] of files) {
    const asset = declaredFiles.get(assetPath);
    if (!asset || bytes.byteLength !== asset.byteLength || sha256(bytes) !== asset.sha256) return null;
  }
  const expectedPaths = new Set();
  const records = [];
  for (const placement of solution.placements) {
    const intentPlacement = intentPlacements.get(placement.placementId);
    const materialization = intentPlacement ? materializations.get(intentPlacement.assetBriefId) : null;
    if (!materialization) return null;
    const visual = materialization.assets.find((asset) => asset.roles.includes("visual"));
    const collider = materialization.assets.find((asset) => asset.roles.includes("collider")) ?? visual;
    if (!visual || !collider) return null;
    for (const asset of [visual, collider]) {
      const bytes = files.get(asset.path);
      if (!bytes || bytes.byteLength !== asset.byteLength || sha256(bytes) !== asset.sha256) return null;
      expectedPaths.add(asset.path);
    }
    records.push({
      placementId: placement.placementId,
      anchorKind: placement.anchorKind,
      anchorId: placement.anchorId,
      positionMm: [...placement.positionMm],
      rotationMilliDegrees: [...placement.rotationMilliDegrees],
      footprint: { ...placement.footprint },
      visual: { path: visual.path, byteLength: visual.byteLength, sha256: visual.sha256 },
      collider: { path: collider.path, byteLength: collider.byteLength, sha256: collider.sha256 },
    });
  }
  if ([...expectedPaths].some((item) => !files.has(item))) return null;
  return records;
}

function sourceIdentityMatches({ intentText, factsText, solution, assetText, runtimeText, receiptText, runtimePack, receipt, facts, intent }) {
  return solution.source.spatialIntent.canonicalSha256 === sha256(intentText) &&
    solution.source.environmentFacts.canonicalSha256 === sha256(factsText) &&
    solution.source.assetBundle.canonicalSha256 === sha256(assetText) &&
    solution.source.runtimeReceiptSha256 === sha256(receiptText) &&
    sameRuntimeIdentity(solution, runtimePack, receipt) &&
    intent.runtime.id === runtimePack.source.id && intent.runtime.contentVersion === runtimePack.source.contentVersion &&
    facts.source.runtime.id === runtimePack.source.id && facts.source.runtime.artifactSha256 === solution.source.runtime.artifactSha256 &&
    sha256(runtimeText) === solution.source.runtime.artifactSha256;
}

function triangleContainsXZ(position, first, second, third) {
  const cross = (left, right, point) =>
    ((right[0] - left[0]) * (point[2] - left[2])) - ((right[2] - left[2]) * (point[0] - left[0]));
  const signs = [cross(first, second, position), cross(second, third, position), cross(third, first, position)];
  return signs.every((value) => value >= -1) || signs.every((value) => value <= 1);
}

function navigationHeightAtPosition(facts, polygonIndex, position) {
  const polygon = facts.navigationMesh.polygons[polygonIndex];
  if (!polygon || polygon.vertexIndices.length < 3) return null;
  const origin = facts.navigationMesh.verticesMm[polygon.vertexIndices[0]];
  for (let index = 1; index < polygon.vertexIndices.length - 1; index += 1) {
    const left = facts.navigationMesh.verticesMm[polygon.vertexIndices[index]];
    const right = facts.navigationMesh.verticesMm[polygon.vertexIndices[index + 1]];
    if (!triangleContainsXZ(position, origin, left, right)) continue;
    const first = [left[0] - origin[0], left[1] - origin[1], left[2] - origin[2]];
    const second = [right[0] - origin[0], right[1] - origin[1], right[2] - origin[2]];
    const normalX = (first[1] * second[2]) - (first[2] * second[1]);
    const normalY = (first[2] * second[0]) - (first[0] * second[2]);
    const normalZ = (first[0] * second[1]) - (first[1] * second[0]);
    if (normalY === 0) continue;
    return Math.round(origin[1] - (((normalX * (position[0] - origin[0])) +
      (normalZ * (position[2] - origin[2]))) / normalY));
  }
  return null;
}

function navigationHeightInPolygons(facts, polygonIndexes, position) {
  for (const polygonIndex of polygonIndexes.slice().sort((left, right) => left - right)) {
    const height = navigationHeightAtPosition(facts, polygonIndex, position);
    if (height !== null) return height;
  }
  return null;
}

function terminalBasePosition(context, index) {
  const terminal = context.actionTerminal;
  const columns = terminal.footprint.columns;
  const row = Math.floor(index / columns);
  const column = index % columns;
  const rowCount = Math.min(columns, terminal.actionCount - (row * columns));
  const localX = (column - ((rowCount - 1) / 2)) * 1_700;
  const localZ = -2_400 - (row * 2_250);
  const radians = terminal.yawMilliDegrees * Math.PI / 180_000;
  return [
    Math.round(terminal.positionMm[0] + (Math.cos(radians) * localX) + (Math.sin(radians) * localZ)),
    0,
    Math.round(terminal.positionMm[2] - (Math.sin(radians) * localX) + (Math.cos(radians) * localZ)),
  ];
}

function nodeContexts(solution, runtimePack, facts) {
  const nodes = new Map(runtimePack.nodes.map((node) => [node.id, node]));
  const floorAnchors = new Map(facts.floorAnchors.map((anchor) => [anchor.id, anchor]));
  const selectedComponent = facts.navigationMesh.components.find((component) =>
    component.index === solution.navigation.componentIndex);
  if (!selectedComponent) return null;
  const output = [];
  for (const context of solution.nodeContexts) {
    const node = nodes.get(context.nodeId);
    if (!node || node.actions.length !== context.actionTerminal.actionCount) return null;
    if (context.actionTerminal.terminalSupports.some((support, supportIndex) => {
      const anchor = floorAnchors.get(support.floorAnchorId);
      const position = terminalBasePosition(context, supportIndex);
      return !anchor || navigationHeightInPolygons(facts, selectedComponent.polygonIndices, position) !==
        support.baseHeightMm;
    })) return null;
    output.push({
      nodeId: context.nodeId,
      zoneId: context.zoneId,
      visiblePlacementIds: [...context.visiblePlacementIds],
      playerSpawn: { ...context.playerSpawn, positionMm: [...context.playerSpawn.positionMm] },
      actionTerminal: {
        ...context.actionTerminal,
        positionMm: [...context.actionTerminal.positionMm],
        footprint: { ...context.actionTerminal.footprint, layoutCenterOffsetMm: [...context.actionTerminal.footprint.layoutCenterOffsetMm] },
        terminalSupports: context.actionTerminal.terminalSupports.map((support) => ({ ...support })),
      },
      approachPathFloorAnchorIds: [...context.approachPathFloorAnchorIds],
    });
  }
  return output;
}

function runtimeSupportHeightMm(solution, runtimePack) {
  const entryNode = runtimePack.nodes[runtimePack.entryNodeIndex];
  if (!entryNode) return null;
  const matches = solution.nodeContexts.filter((context) => context.nodeId === entryNode.id);
  const value = matches[0]?.playerSpawn?.positionMm?.[1];
  return matches.length === 1 && Number.isSafeInteger(value) ? value : null;
}

function runtimeSelectedPolygonIndices(facts, solution) {
  const component = facts.navigationMesh.components.find((item) =>
    item.index === solution.navigation.componentIndex);
  if (!component) return null;
  const componentPolygons = new Set(component.polygonIndices);
  const floorById = new Map(facts.floorAnchors.map((anchor) => [anchor.id, anchor]));
  const wallById = new Map(facts.wallAnchors.map((anchor) => [anchor.id, anchor]));
  const requiredFloorIds = new Set(solution.navigation.zoneSeeds.map((seed) => seed.floorAnchorId));
  for (const placement of solution.placements) {
    if (placement.anchorKind === "floor") requiredFloorIds.add(placement.anchorId);
    else {
      const wall = wallById.get(placement.anchorId);
      if (!wall) return null;
      requiredFloorIds.add(wall.nearestFloorAnchorId);
    }
  }
  for (const context of solution.nodeContexts) {
    requiredFloorIds.add(context.playerSpawn.floorAnchorId);
    requiredFloorIds.add(context.actionTerminal.floorAnchorId);
    requiredFloorIds.add(context.actionTerminal.approachFloorAnchorId);
    for (const id of context.approachPathFloorAnchorIds) requiredFloorIds.add(id);
    for (const support of context.actionTerminal.terminalSupports) requiredFloorIds.add(support.floorAnchorId);
  }
  const requiredPolygons = new Set();
  for (const id of requiredFloorIds) {
    const anchor = floorById.get(id);
    if (!anchor || anchor.componentIndex !== component.index || !componentPolygons.has(anchor.polygonIndex)) return null;
    requiredPolygons.add(anchor.polygonIndex);
  }
  if (requiredPolygons.size === 0) return null;
  const polygonsByVertex = new Map();
  const adjacency = new Map([...componentPolygons].map((index) => [index, new Set()]));
  for (const polygonIndex of componentPolygons) {
    const polygon = facts.navigationMesh.polygons[polygonIndex];
    if (!polygon || polygon.componentIndex !== component.index) return null;
    for (const vertexIndex of polygon.vertexIndices) {
      if (!polygonsByVertex.has(vertexIndex)) polygonsByVertex.set(vertexIndex, []);
      polygonsByVertex.get(vertexIndex).push(polygonIndex);
    }
  }
  for (const linked of polygonsByVertex.values()) {
    linked.sort((left, right) => left - right);
    for (const left of linked) for (const right of linked) if (left !== right) adjacency.get(left).add(right);
  }
  const targets = [...requiredPolygons].sort((left, right) => left - right);
  const selected = new Set([targets[0]]);
  for (const target of targets) {
    if (selected.has(target)) continue;
    const pending = [target];
    const parents = new Map([[target, -1]]);
    let found = -1;
    for (let offset = 0; offset < pending.length && found < 0; offset += 1) {
      const current = pending[offset];
      if (selected.has(current)) { found = current; break; }
      for (const neighbor of [...adjacency.get(current)].sort((left, right) => left - right)) {
        if (!parents.has(neighbor)) { parents.set(neighbor, current); pending.push(neighbor); }
      }
    }
    if (found < 0) return null;
    for (let cursor = found; cursor !== -1; cursor = parents.get(cursor)) selected.add(cursor);
  }
  const buffered = new Set(selected);
  for (const polygonIndex of selected) for (const neighbor of adjacency.get(polygonIndex)) buffered.add(neighbor);
  return [...buffered].sort((left, right) => left - right);
}

function exactGodotResult(value) {
  const failure = exactRecord(value, ["code", "ok", "path"]);
  if (failure && failure.ok === false && GODOT_FAILURE_CODES.has(failure.code) && typeof failure.path === "string" && /^\/(?:nodeContexts|placements)\/\d+(?:\/[A-Za-z]+)?$/u.test(failure.path)) {
    return { ok: false, code: failure.code, path: failure.path };
  }
  const success = exactRecord(value, ["allChecksPassed", "checkedPathCount", "checkedTerminalCount", "checkedVisualSafetyBoxCount", "format", "formatVersion", "nodeContextCount", "ok", "placementCount", "solutionSha256"]);
  if (!success || success.ok !== true || success.format !== "matrix-oasis.godot-spatial-solution-verification" || success.formatVersion !== "0.1.0" || success.allChecksPassed !== true || typeof success.solutionSha256 !== "string") return null;
  for (const key of ["placementCount", "nodeContextCount", "checkedPathCount", "checkedTerminalCount", "checkedVisualSafetyBoxCount"]) {
    if (!Number.isSafeInteger(success[key]) || success[key] < 0) return null;
  }
  delete success.ok;
  return { ok: true, value: success };
}

function hasSingleMarker(output) {
  return output.split(READY_MARKER).length - 1 === 1 && !/\b(?:SCRIPT ERROR|ERROR:)\b/u.test(output);
}

export function createGodotSpatialSolutionVerifier(config) {
  try {
    const captured = exactRecord(config, ["godotBin"]);
    if (!captured || typeof captured.godotBin !== "string" || !path.isAbsolute(captured.godotBin)) throw new PrototypeSpatialVerifierOperationalError();
    const probe = spawnSync(captured.godotBin, ["--version"], { encoding: "utf8", shell: false, timeout: 5_000, windowsHide: true });
    if (probe.error || probe.status !== 0 || !/(?:^|\D)4\.6\.3(?:\D|$)/u.test(`${probe.stdout ?? ""}${probe.stderr ?? ""}`)) throw new PrototypeSpatialVerifierOperationalError();
    const handle = Object.freeze({ kind: VERIFIER_KIND });
    verifierState.set(handle, Object.freeze({ godotBin: captured.godotBin }));
    return handle;
  } catch (error) {
    throw operational(error);
  }
}

export async function verifyPrototypeSpatialSolution(request, verifier) {
  let temporaryRoot = null;
  try {
    const state = verifierState.get(verifier);
    const captured = exactRecord(request, [
      "assetBundleJson", "assetFiles", "environmentColliderBytes", "environmentFactsJson", "runtimeGamePackJson",
      "runtimeReceiptJson", "spatialAssemblyJson", "spatialIntentJson", "spatialSolutionJson", "environmentSplatBytes",
    ]);
    if (!state || !captured || [captured.assetBundleJson, captured.environmentFactsJson, captured.runtimeGamePackJson,
      captured.runtimeReceiptJson, captured.spatialAssemblyJson, captured.spatialIntentJson,
      captured.spatialSolutionJson].some((item) => typeof item !== "string")) {
      return staticFailure("input", "PROTOTYPE_SPATIAL_VERIFIER_INPUT_INVALID", "");
    }
    const colliderBytes = copyBytes(captured.environmentColliderBytes, MAX_COLLIDER_BYTES);
    const splatBytes = copyBytes(captured.environmentSplatBytes, MAX_SPLAT_BYTES);
    const files = copyAssetFiles(captured.assetFiles);
    if (!colliderBytes || !splatBytes || !files) return staticFailure("input", "PROTOTYPE_SPATIAL_VERIFIER_INPUT_INVALID", "");

    const intentReport = validatePrototypeSpatialIntentJson(captured.spatialIntentJson);
    const factsReport = validatePrototypeEnvironmentFactsJson(captured.environmentFactsJson);
    const solutionReport = validatePrototypeSpatialSolutionJson(captured.spatialSolutionJson);
    const assemblyReport = validatePrototypeSpatialAssemblyJson(captured.spatialAssemblyJson);
    const assetReport = validatePrototypeAssetBundleJson(captured.assetBundleJson);
    const runtimeReport = await validateRuntimeGamePackJson(captured.runtimeGamePackJson, captured.runtimeReceiptJson);
    for (const [report, code, pathValue] of [
      [intentReport, "PROTOTYPE_SPATIAL_VERIFIER_INTENT_INVALID", "/spatialIntent"],
      [factsReport, "PROTOTYPE_SPATIAL_VERIFIER_FACTS_INVALID", "/environmentFacts"],
      [solutionReport, "PROTOTYPE_SPATIAL_VERIFIER_SOLUTION_INVALID", "/spatialSolution"],
      [assemblyReport, "PROTOTYPE_SPATIAL_VERIFIER_SPATIAL_ASSEMBLY_INVALID", "/spatialAssembly"],
      [assetReport, "PROTOTYPE_SPATIAL_VERIFIER_ASSET_BUNDLE_INVALID", "/assetBundle"],
      [runtimeReport, "PROTOTYPE_SPATIAL_VERIFIER_RUNTIME_INVALID", "/runtimePack"],
    ]) {
      const failure = documentFailure(report, code, pathValue);
      if (failure) return failure;
    }

    const intent = parseCanonical(captured.spatialIntentJson);
    const facts = parseCanonical(captured.environmentFactsJson);
    const solution = parseCanonical(captured.spatialSolutionJson);
    const assetBundle = parseCanonical(captured.assetBundleJson);
    const runtimePack = parseCanonical(captured.runtimeGamePackJson);
    const receipt = parseCanonical(captured.runtimeReceiptJson);
    const spatialAssembly = parseCanonical(captured.spatialAssemblyJson);
    if (!sourceIdentityMatches({
      intentText: captured.spatialIntentJson, factsText: captured.environmentFactsJson, solution,
      assetText: captured.assetBundleJson, runtimeText: captured.runtimeGamePackJson, receiptText: captured.runtimeReceiptJson,
      runtimePack, receipt, facts, intent,
    })) return staticFailure("integrity", "PROTOTYPE_SPATIAL_VERIFIER_IDENTITY_MISMATCH", "/spatialSolution/source");
    if (colliderBytes.byteLength !== facts.source.collider.byteLength || sha256(colliderBytes) !== facts.source.collider.sha256) {
      return staticFailure("integrity", "PROTOTYPE_SPATIAL_VERIFIER_COLLIDER_INTEGRITY_MISMATCH", "/environmentColliderBytes");
    }
    const assemblySha256 = sha256(captured.spatialAssemblyJson);
    if (spatialAssembly?.format !== "matrix-oasis.prototype-spatial-assembly" ||
        spatialAssembly?.formatVersion !== "0.1.0" ||
        spatialAssembly?.canonicalization !== "matrix-oasis.canonical-json/1" ||
        spatialAssembly?.environment?.splat?.sha256 !== sha256(splatBytes) ||
        facts.source.analysisTransform.sourceCanonicalSha256 !== assemblySha256 ||
        solution.source.analysisTransformSource.profile !== "spatial-assembly-collider-v1" ||
        solution.source.analysisTransformSource.canonicalSha256 !== assemblySha256) {
      return staticFailure("integrity", "PROTOTYPE_SPATIAL_VERIFIER_VISUAL_SAFETY_IDENTITY_MISMATCH",
        "/spatialAssembly");
    }
    const placements = collectPlacementAssets(intent, solution, assetBundle, files);
    const contexts = nodeContexts(solution, runtimePack, facts);
    const supportHeightMm = runtimeSupportHeightMm(solution, runtimePack);
    const selectedPolygonIndices = runtimeSelectedPolygonIndices(facts, solution);
    if (!placements) return staticFailure("integrity", "PROTOTYPE_SPATIAL_VERIFIER_ASSET_INTEGRITY_MISMATCH", "/assetFiles");
    if (!contexts) return staticFailure("integrity", "PROTOTYPE_SPATIAL_VERIFIER_TERMINAL_SUPPORT_MISMATCH",
      "/spatialSolution/nodeContexts");
    if (supportHeightMm === null || !selectedPolygonIndices) return staticFailure("integrity",
      "PROTOTYPE_SPATIAL_VERIFIER_SOLUTION_INVALID", "/spatialSolution/navigation");
    const derivedVisualSafety = await deriveVisualSafetyEvidence({
      spatialResourceBytes: splatBytes, spatialAssembly, environmentFacts: facts,
      selectedPolygonIndices, runtimeSupportHeightMm: supportHeightMm,
    });
    if (!derivedVisualSafety) return staticFailure("integrity",
      "PROTOTYPE_SPATIAL_VERIFIER_VISUAL_SAFETY_INVALID", "/environmentSplatBytes");
    const visualSafety = {
      ...derivedVisualSafety,
      sourceSplatSha256: sha256(splatBytes),
      spatialAssemblySha256: assemblySha256,
    };

    temporaryRoot = await mkdtemp(path.join(os.tmpdir(), "matrix-oasis-r14-verifier-"));
    const analysisProjectRoot = path.join(temporaryRoot, "godot-project");
    const runtimeDirectory = path.join(analysisProjectRoot, "runtime");
    const verifierDirectory = path.join(analysisProjectRoot, "spatial_solution_verification");
    const playableDirectory = path.join(analysisProjectRoot, "playable");
    await Promise.all([mkdir(runtimeDirectory, { recursive: true }), mkdir(verifierDirectory, { recursive: true }), mkdir(playableDirectory, { recursive: true })]);
    const projectConfiguration = [
      "; Generated isolated Matrix Oasis R14 verification project.", "config_version=5", "", "[application]",
      "config/name=\"Matrix Oasis R14 Spatial Verification\"", "", "[physics]", "3d/physics_engine=\"Jolt Physics\"", "",
      "[rendering]", "renderer/rendering_method=\"gl_compatibility\"", "",
    ].join("\n");
    await writeFile(path.join(analysisProjectRoot, "project.godot"), projectConfiguration, { encoding: "utf8", flag: "wx" });
    for (const [source, destination] of [
      [path.join(projectRoot, "runtime", "strict_json.gd"), path.join(runtimeDirectory, "strict_json.gd")],
      [path.join(projectRoot, "spatial_solution_verification", "solution_verifier.gd"), path.join(verifierDirectory, "solution_verifier.gd")],
      [path.join(projectRoot, "spatial_solution_verification", "solution_verifier.tscn"), path.join(verifierDirectory, "solution_verifier.tscn")],
      [path.join(projectRoot, "playable", "action_terminal_grid.gd"), path.join(playableDirectory, "action_terminal_grid.gd")],
      [path.join(projectRoot, "playable", "action_terminal_3d.gd"), path.join(playableDirectory, "action_terminal_3d.gd")],
      [path.join(projectRoot, "playable", "action_terminal_3d.tscn"), path.join(playableDirectory, "action_terminal_3d.tscn")],
    ]) await writeFile(destination, await readFile(source), { flag: "wx" });

    const imported = spawnSync(state.godotBin, ["--headless", "--path", analysisProjectRoot, "--import"], {
      cwd: temporaryRoot, encoding: "utf8", maxBuffer: 16 * 1024 * 1024, shell: false, timeout: 300_000, windowsHide: true,
    });
    const importOutput = `${imported.stdout ?? ""}${imported.stderr ?? ""}`;
    const importFailure = classifyProcessFailure(imported, importOutput);
    if (importFailure !== null) throw new PrototypeSpatialVerifierOperationalError("import", importFailure);

    const artifactDirectory = path.join(temporaryRoot, "artifacts");
    const assetDirectory = path.join(artifactDirectory, "assets");
    await mkdir(assetDirectory, { recursive: true });
    const environmentPath = path.join(artifactDirectory, "environment-collider.glb");
    await writeFile(environmentPath, colliderBytes, { flag: "wx" });
    const pathBySource = new Map();
    let fileIndex = 0;
    for (const [sourcePath, bytes] of [...files.entries()].sort(([left], [right]) => left < right ? -1 : left > right ? 1 : 0)) {
      const destination = path.join(assetDirectory, `${String(fileIndex).padStart(2, "0")}.glb`);
      await writeFile(destination, bytes, { flag: "wx" });
      pathBySource.set(sourcePath, destination);
      fileIndex += 1;
    }
    const requestPath = path.join(temporaryRoot, "request.json");
    const outputPath = path.join(temporaryRoot, "verification.json");
    const godotRequest = canonicalizeJsonValue({
      format: "matrix-oasis.godot-spatial-solution-verification-request", formatVersion: "0.1.0",
      solutionSha256: sha256(captured.spatialSolutionJson),
      environmentCollider: { path: environmentPath, byteLength: colliderBytes.byteLength, sha256: sha256(colliderBytes) },
      analysisTransform: facts.source.analysisTransform,
      navigationMesh: facts.navigationMesh,
      runtimeSupportHeightMm: supportHeightMm,
      selectedPolygonIndices,
      floorAnchors: facts.floorAnchors,
      visualSafety,
      placements: placements.map((item) => ({
        ...item,
        positionMm: item.anchorKind === "floor"
          ? [item.positionMm[0], supportHeightMm, item.positionMm[2]] : item.positionMm,
        visual: { ...item.visual, path: pathBySource.get(item.visual.path) },
        collider: { ...item.collider, path: pathBySource.get(item.collider.path) },
      })),
      nodeContexts: contexts.map((context) => ({
        ...context,
        playerSpawn: { ...context.playerSpawn,
          positionMm: [context.playerSpawn.positionMm[0], supportHeightMm, context.playerSpawn.positionMm[2]] },
        actionTerminal: { ...context.actionTerminal,
          positionMm: [context.actionTerminal.positionMm[0], supportHeightMm, context.actionTerminal.positionMm[2]],
          terminalSupports: context.actionTerminal.terminalSupports.map((support) =>
            ({ ...support, baseHeightMm: supportHeightMm })),
        },
      })),
    });
    await writeFile(requestPath, godotRequest, { encoding: "utf8", flag: "wx" });
    const result = spawnSync(state.godotBin, [
      "--headless", "--path", analysisProjectRoot, "res://spatial_solution_verification/solution_verifier.tscn", "--",
      `--matrix-oasis-verification-request=${requestPath}`, `--matrix-oasis-verification-output=${outputPath}`,
    ], { cwd: moduleRoot, encoding: "utf8", maxBuffer: 16 * 1024 * 1024, shell: false, timeout: 300_000, windowsHide: true });
    const output = `${result.stdout ?? ""}${result.stderr ?? ""}`;
    const verificationFailure = classifyProcessFailure(result, output, { markerRequired: true });
    if (verificationFailure !== null) throw new PrototypeSpatialVerifierOperationalError("verification", verificationFailure);
    const parsed = exactGodotResult(JSON.parse(await readFile(outputPath, "utf8")));
    if (!parsed) throw new PrototypeSpatialVerifierOperationalError("result", "unknown");
    if (!parsed.ok) return staticFailure("verification", parsed.code, parsed.path);
    const terminalCount = solution.nodeContexts.reduce((sum, context) => sum + context.actionTerminal.actionCount, 0);
    if (parsed.value.solutionSha256 !== sha256(captured.spatialSolutionJson) ||
        parsed.value.placementCount !== solution.placements.length ||
        parsed.value.nodeContextCount !== solution.nodeContexts.length ||
        parsed.value.checkedTerminalCount !== terminalCount || parsed.value.checkedPathCount !== terminalCount ||
        parsed.value.checkedVisualSafetyBoxCount !== visualSafety.boxes.length) {
      throw new PrototypeSpatialVerifierOperationalError("result", "unknown");
    }
    const canonicalVerificationReportJson = canonicalizeJsonValue({
      format: "matrix-oasis.prototype-spatial-verification-report", formatVersion: "0.1.0",
      solutionSha256: parsed.value.solutionSha256,
      evidenceSha256: sha256(canonicalizeJsonValue(parsed.value)),
      visualSafety,
      verifier: { id: "godot-spatial-solution-verifier", version: "0.1.0-r14", godotVersion: "4.6.3" },
      checks: {
        placementCount: parsed.value.placementCount, nodeContextCount: parsed.value.nodeContextCount,
        pathCount: parsed.value.checkedPathCount, terminalCount: parsed.value.checkedTerminalCount,
        visualSafetyBoxCount: parsed.value.checkedVisualSafetyBoxCount,
      },
    });
    return deepFreeze({ ok: true, spatialSolution: solution, verification: parsed.value, canonicalVerificationReportJson });
  } catch (error) {
    throw operational(error);
  } finally {
    if (temporaryRoot !== null) await rm(temporaryRoot, { recursive: true, force: true }).catch(() => {});
  }
}
