import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { validatePrototypeAssetBundleJson } from "@matrix-oasis/prototype-asset-contracts";
import {
  validatePrototypeEnvironmentFactsJson,
  validatePrototypeSpatialIntentJson,
} from "@matrix-oasis/prototype-spatial-planning-contracts";
import { validatePrototypeSpatialSolutionJson } from "@matrix-oasis/prototype-spatial-solution-contracts";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { validateRuntimeGamePackJson } from "@matrix-oasis/runtime-pack-validator";

const INTERNAL_CODE = "PROTOTYPE_SPATIAL_VERIFIER_INTERNAL_ERROR";
const VERIFIER_KIND = "matrix-oasis.godot-spatial-solution-verifier/1";
const READY_MARKER = "MATRIX_OASIS_R14_SPATIAL_VERIFICATION_READY";
const MAX_COLLIDER_BYTES = 32 * 1024 * 1024;
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

export class PrototypeSpatialVerifierOperationalError extends Error {
  constructor() {
    super(INTERNAL_CODE);
    this.name = "PrototypeSpatialVerifierOperationalError";
    this.code = INTERNAL_CODE;
  }
}

function operational(error) {
  return error instanceof PrototypeSpatialVerifierOperationalError
    ? error
    : new PrototypeSpatialVerifierOperationalError();
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

function nodeContexts(solution, runtimePack) {
  const nodes = new Map(runtimePack.nodes.map((node) => [node.id, node]));
  const output = [];
  for (const context of solution.nodeContexts) {
    const node = nodes.get(context.nodeId);
    if (!node || node.actions.length !== context.actionTerminal.actionCount) return null;
    output.push({
      nodeId: context.nodeId,
      zoneId: context.zoneId,
      visiblePlacementIds: [...context.visiblePlacementIds],
      playerSpawn: { ...context.playerSpawn, positionMm: [...context.playerSpawn.positionMm] },
      actionTerminal: {
        ...context.actionTerminal,
        positionMm: [...context.actionTerminal.positionMm],
        footprint: { ...context.actionTerminal.footprint, layoutCenterOffsetMm: [...context.actionTerminal.footprint.layoutCenterOffsetMm] },
      },
      approachPathFloorAnchorIds: [...context.approachPathFloorAnchorIds],
    });
  }
  return output;
}

function exactGodotResult(value) {
  const failure = exactRecord(value, ["code", "ok", "path"]);
  if (failure && failure.ok === false && GODOT_FAILURE_CODES.has(failure.code) && typeof failure.path === "string" && /^\/(?:nodeContexts|placements)\/\d+(?:\/[A-Za-z]+)?$/u.test(failure.path)) {
    return { ok: false, code: failure.code, path: failure.path };
  }
  const success = exactRecord(value, ["allChecksPassed", "checkedPathCount", "checkedTerminalCount", "format", "formatVersion", "nodeContextCount", "ok", "placementCount", "solutionSha256"]);
  if (!success || success.ok !== true || success.format !== "matrix-oasis.godot-spatial-solution-verification" || success.formatVersion !== "0.1.0" || success.allChecksPassed !== true || typeof success.solutionSha256 !== "string") return null;
  for (const key of ["placementCount", "nodeContextCount", "checkedPathCount", "checkedTerminalCount"]) {
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
      "runtimeReceiptJson", "spatialIntentJson", "spatialSolutionJson",
    ]);
    if (!state || !captured || [captured.assetBundleJson, captured.environmentFactsJson, captured.runtimeGamePackJson, captured.runtimeReceiptJson, captured.spatialIntentJson, captured.spatialSolutionJson].some((item) => typeof item !== "string")) {
      return staticFailure("input", "PROTOTYPE_SPATIAL_VERIFIER_INPUT_INVALID", "");
    }
    const colliderBytes = copyBytes(captured.environmentColliderBytes, MAX_COLLIDER_BYTES);
    const files = copyAssetFiles(captured.assetFiles);
    if (!colliderBytes || !files) return staticFailure("input", "PROTOTYPE_SPATIAL_VERIFIER_INPUT_INVALID", "");

    const intentReport = validatePrototypeSpatialIntentJson(captured.spatialIntentJson);
    const factsReport = validatePrototypeEnvironmentFactsJson(captured.environmentFactsJson);
    const solutionReport = validatePrototypeSpatialSolutionJson(captured.spatialSolutionJson);
    const assetReport = validatePrototypeAssetBundleJson(captured.assetBundleJson);
    const runtimeReport = await validateRuntimeGamePackJson(captured.runtimeGamePackJson, captured.runtimeReceiptJson);
    for (const [report, code, pathValue] of [
      [intentReport, "PROTOTYPE_SPATIAL_VERIFIER_INTENT_INVALID", "/spatialIntent"],
      [factsReport, "PROTOTYPE_SPATIAL_VERIFIER_FACTS_INVALID", "/environmentFacts"],
      [solutionReport, "PROTOTYPE_SPATIAL_VERIFIER_SOLUTION_INVALID", "/spatialSolution"],
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
    if (!sourceIdentityMatches({
      intentText: captured.spatialIntentJson, factsText: captured.environmentFactsJson, solution,
      assetText: captured.assetBundleJson, runtimeText: captured.runtimeGamePackJson, receiptText: captured.runtimeReceiptJson,
      runtimePack, receipt, facts, intent,
    })) return staticFailure("integrity", "PROTOTYPE_SPATIAL_VERIFIER_IDENTITY_MISMATCH", "/spatialSolution/source");
    if (colliderBytes.byteLength !== facts.source.collider.byteLength || sha256(colliderBytes) !== facts.source.collider.sha256) {
      return staticFailure("integrity", "PROTOTYPE_SPATIAL_VERIFIER_COLLIDER_INTEGRITY_MISMATCH", "/environmentColliderBytes");
    }
    const placements = collectPlacementAssets(intent, solution, assetBundle, files);
    const contexts = nodeContexts(solution, runtimePack);
    if (!placements || !contexts) return staticFailure("integrity", "PROTOTYPE_SPATIAL_VERIFIER_ASSET_INTEGRITY_MISMATCH", "/assetFiles");

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

    const imported = spawnSync(state.godotBin, ["--headless", "--editor", "--path", analysisProjectRoot, "--quit"], {
      cwd: temporaryRoot, encoding: "utf8", maxBuffer: 16 * 1024 * 1024, shell: false, timeout: 180_000, windowsHide: true,
    });
    const importOutput = `${imported.stdout ?? ""}${imported.stderr ?? ""}`;
    if (imported.error || imported.status !== 0 || /(?:SCRIPT ERROR:|(?:^|\n)ERROR:)/u.test(importOutput)) throw new PrototypeSpatialVerifierOperationalError();

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
      floorAnchors: facts.floorAnchors,
      placements: placements.map((item) => ({
        ...item,
        visual: { ...item.visual, path: pathBySource.get(item.visual.path) },
        collider: { ...item.collider, path: pathBySource.get(item.collider.path) },
      })),
      nodeContexts: contexts,
    });
    await writeFile(requestPath, godotRequest, { encoding: "utf8", flag: "wx" });
    const result = spawnSync(state.godotBin, [
      "--headless", "--path", analysisProjectRoot, "res://spatial_solution_verification/solution_verifier.tscn", "--",
      `--matrix-oasis-verification-request=${requestPath}`, `--matrix-oasis-verification-output=${outputPath}`,
    ], { cwd: moduleRoot, encoding: "utf8", maxBuffer: 16 * 1024 * 1024, shell: false, timeout: 180_000, windowsHide: true });
    const output = `${result.stdout ?? ""}${result.stderr ?? ""}`;
    if (result.error || result.status !== 0 || !hasSingleMarker(output)) throw new PrototypeSpatialVerifierOperationalError();
    const parsed = exactGodotResult(JSON.parse(await readFile(outputPath, "utf8")));
    if (!parsed) throw new PrototypeSpatialVerifierOperationalError();
    if (!parsed.ok) return staticFailure("verification", parsed.code, parsed.path);
    if (parsed.value.solutionSha256 !== sha256(captured.spatialSolutionJson) || parsed.value.placementCount !== solution.placements.length || parsed.value.nodeContextCount !== solution.nodeContexts.length) throw new PrototypeSpatialVerifierOperationalError();
    const canonicalVerificationReportJson = canonicalizeJsonValue({
      format: "matrix-oasis.prototype-spatial-verification-report", formatVersion: "0.1.0",
      solutionSha256: parsed.value.solutionSha256,
      evidenceSha256: sha256(canonicalizeJsonValue(parsed.value)),
      verifier: { id: "godot-spatial-solution-verifier", version: "0.1.0-r14", godotVersion: "4.6.3" },
      checks: {
        placementCount: parsed.value.placementCount, nodeContextCount: parsed.value.nodeContextCount,
        pathCount: parsed.value.checkedPathCount, terminalCount: parsed.value.checkedTerminalCount,
      },
    });
    return deepFreeze({ ok: true, spatialSolution: solution, verification: parsed.value, canonicalVerificationReportJson });
  } catch (error) {
    throw operational(error);
  } finally {
    if (temporaryRoot !== null) await rm(temporaryRoot, { recursive: true, force: true }).catch(() => {});
  }
}
