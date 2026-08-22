import {
  lstat,
  mkdir,
  mkdtemp,
  open,
  readFile,
  readdir,
  realpath,
  rename,
  rm,
  rmdir,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import { validatePrototypeAssetBundleJson } from "@matrix-oasis/prototype-asset-contracts";
import {
  analyzePrototypeEnvironment,
  createGodotEnvironmentAnalyzer,
} from "@matrix-oasis/prototype-environment-analyzer";
import {
  assemblePrototypeSpatialScene,
  validatePrototypeSpatialAssemblyJson,
} from "@matrix-oasis/prototype-spatial-assembler";
import {
  synthesizePrototypeSpatialIntent,
} from "@matrix-oasis/prototype-spatial-solver";
import { PROTOTYPE_SPATIAL_SOLUTION_PROFILE } from "@matrix-oasis/prototype-spatial-solution-contracts";
import {
  solvePrototypeSpatialLayoutInternal,
  spatialWalkableEnvelopeCandidateRegion,
  spatialPlacementCandidateKey,
  spatialStationCandidateKey,
  spatialTerminalCandidateKey,
} from "../packages/prototype-spatial-solver/src/solver.mjs";
import {
  createGodotSpatialSolutionVerifier,
  verifyPrototypeSpatialSolution,
} from "@matrix-oasis/prototype-spatial-verifier";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { recoverPrototypeRuns } from "./lib/prototype-cache-core.mjs";
import {
  loadVerifiedR14SpatialPrototypeRun,
  publishSolvedSpatialPrototypeRun,
} from "./lib/solved-spatial-cache-core.mjs";
import { resolveGodotBinary } from "./lib/godot-core.mjs";

const RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;
const TEXT_LIMIT = 262_144;
const COLLIDER_PATH = "assets/environment-collider.glb";
const SPLAT_PATH = "assets/environment.compressed.ply";
const MAX_PHYSICAL_ATTEMPTS =
  (PROTOTYPE_SPATIAL_SOLUTION_PROFILE.maxPlacements + PROTOTYPE_SPATIAL_SOLUTION_PROFILE.maxZones) * 2 + 1;
const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const temporaryRoot = path.resolve(path.parse(moduleRoot).root, "tmp");
const defaultServices = Object.freeze({
  lstat,
  mkdir,
  mkdtemp,
  openFile: open,
  readFile,
  readdir,
  realpath,
  rename,
  rm,
  rmdir,
});

export class R14QualificationOperationalError extends Error {
  constructor(code = "R14_QUALIFICATION_INTERNAL_ERROR") {
    super(code);
    this.name = "R14QualificationOperationalError";
    this.code = code;
  }
}

function fail(code) { throw new R14QualificationOperationalError(code); }
function exact(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype && Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key));
}
function contained(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}
function identity(stat) {
  return stat && typeof stat.dev === "bigint" && typeof stat.ino === "bigint"
    ? Object.freeze({ dev: stat.dev, ino: stat.ino }) : null;
}
function state(stat) {
  return stat && typeof stat.size === "bigint" && typeof stat.mtimeNs === "bigint" && typeof stat.ctimeNs === "bigint"
    ? Object.freeze({ size: stat.size, mtimeNs: stat.mtimeNs, ctimeNs: stat.ctimeNs }) : null;
}
function sameIdentity(stat, expected) { return expected && stat.dev === expected.dev && stat.ino === expected.ino; }
function sameState(stat, expected) {
  return expected && stat.size === expected.size && stat.mtimeNs === expected.mtimeNs && stat.ctimeNs === expected.ctimeNs;
}
function decode(bytes) {
  try { return new TextDecoder("utf-8", { fatal: true }).decode(bytes); }
  catch { fail("R14_QUALIFICATION_SOURCE_INVALID"); }
}
function canonical(text) {
  try {
    const value = JSON.parse(text);
    if (canonicalizeJsonValue(value) !== text) fail("R14_QUALIFICATION_SOURCE_INVALID");
    return value;
  } catch (error) {
    if (error instanceof R14QualificationOperationalError) throw error;
    fail("R14_QUALIFICATION_SOURCE_INVALID");
  }
}
function validReport(report) {
  return report && report.reportVersion === 1 && report.valid === true &&
    Array.isArray(report.diagnostics) && report.diagnostics.length === 0;
}
function staticFailure(stage, diagnostics) {
  return Object.freeze({ ok: false, stage, diagnostics: Object.freeze(diagnostics.slice()) });
}

async function trustedDirectory(candidate, parent, services) {
  try {
    const absolute = path.resolve(candidate);
    const real = path.resolve(await services.realpath(absolute));
    const stat = await services.lstat(absolute, { bigint: true });
    const observed = identity(stat);
    if (real !== absolute || !contained(parent, real) || !stat.isDirectory() || stat.isSymbolicLink() || !observed) {
      fail("R14_QUALIFICATION_SOURCE_INVALID");
    }
    return Object.freeze({ path: absolute, identity: observed });
  } catch (error) {
    if (error instanceof R14QualificationOperationalError) throw error;
    fail("R14_QUALIFICATION_SOURCE_INVALID");
  }
}

async function assertDirectory(directory, parent, services) {
  const current = await trustedDirectory(directory.path, parent, services);
  if (current.identity.dev !== directory.identity.dev || current.identity.ino !== directory.identity.ino) {
    fail("R14_QUALIFICATION_SOURCE_INVALID");
  }
}

async function readStableText(directory, name, maximum, services) {
  const candidate = path.join(directory.path, name);
  let handle;
  try {
    await assertDirectory(directory, path.dirname(directory.path), services);
    handle = await services.openFile(candidate, "r");
    const before = await handle.stat({ bigint: true });
    const observed = identity(before); const captured = state(before);
    const linked = await services.lstat(candidate, { bigint: true });
    const real = path.resolve(await services.realpath(candidate));
    if (!before.isFile() || before.isSymbolicLink() || !observed || !captured || before.size < 1n ||
        before.size > BigInt(maximum) || real !== candidate || linked.isSymbolicLink() || !linked.isFile() ||
        !sameIdentity(linked, observed) || !sameState(linked, captured)) fail("R14_QUALIFICATION_SOURCE_INVALID");
    const output = new Uint8Array(Number(before.size)); let offset = 0;
    while (offset < output.length) {
      const result = await handle.read(output, offset, output.length - offset, offset);
      if (!result || result.bytesRead < 1) fail("R14_QUALIFICATION_SOURCE_INVALID");
      offset += result.bytesRead;
    }
    const tail = await handle.read(new Uint8Array(1), 0, 1, output.length);
    const after = await handle.stat({ bigint: true });
    if (tail.bytesRead !== 0 || !sameIdentity(after, observed) || !sameState(after, captured)) {
      fail("R14_QUALIFICATION_SOURCE_INVALID");
    }
    const text = decode(output); canonical(text); return text;
  } catch (error) {
    if (error instanceof R14QualificationOperationalError) throw error;
    fail("R14_QUALIFICATION_SOURCE_INVALID");
  } finally {
    if (handle) try { await handle.close(); } catch { /* preserve the primary result */ }
  }
}

function sourceOptions(options, services) {
  return Object.freeze({
    loadVerifiedSpatialPrototypeRun: loadVerifiedR14SpatialPrototypeRun,
    cacheOptions: Object.freeze({
      runRoot: options.spatialRunRoot,
      prototypeRunRoot: options.prototypeRunRoot,
      temporaryRoot: options.temporaryRoot,
      services,
      recoverPrototypeRuns,
      assemblePrototypeScene,
      assemblePrototypeSpatialScene,
      canonicalizeJsonValue,
    }),
  });
}

async function loadQualificationSource(options, services) {
  const source = await loadVerifiedR14SpatialPrototypeRun({
    runId: options.runId,
    ...sourceOptions(options, services).cacheOptions,
  });
  if (!source || !(source.previewFiles instanceof Map) || !source.qualificationEvidence) {
    fail("R14_QUALIFICATION_SOURCE_INVALID");
  }
  const temp = path.resolve(await services.realpath(options.temporaryRoot));
  const prototypeRoot = await trustedDirectory(options.prototypeRunRoot, temp, services);
  const prototypeRuns = await trustedDirectory(path.join(prototypeRoot.path, "runs"), prototypeRoot.path, services);
  const prototypeRun = await trustedDirectory(path.join(prototypeRuns.path, options.runId), prototypeRuns.path, services);
  const spatialRoot = await trustedDirectory(options.spatialRunRoot, temp, services);
  const spatialRuns = await trustedDirectory(path.join(spatialRoot.path, "spatial-runs"), spatialRoot.path, services);
  const spatialRun = await trustedDirectory(path.join(spatialRuns.path, options.runId), spatialRuns.path, services);
  const assetBundleJson = await readStableText(prototypeRun, "prototype-asset-bundle.json", TEXT_LIMIT, services);
  const spatialEnvironmentBundleJson = await readStableText(spatialRun, "prototype-spatial-environment-bundle.json", TEXT_LIMIT, services);
  if (!validReport(validatePrototypeAssetBundleJson(assetBundleJson))) fail("R14_QUALIFICATION_SOURCE_INVALID");
  return Object.freeze({ source, assetBundleJson, spatialEnvironmentBundleJson });
}

export function qualificationInputs(loaded) {
  const { source, assetBundleJson, spatialEnvironmentBundleJson } = loaded;
  const evidence = source.qualificationEvidence;
  if (!exact(evidence, ["source", "sceneBlueprintJson", "runtimeGamePackJson", "runtimeReceiptJson"]) ||
      typeof evidence.sceneBlueprintJson !== "string" || typeof evidence.runtimeGamePackJson !== "string" ||
      typeof evidence.runtimeReceiptJson !== "string") fail("R14_QUALIFICATION_SOURCE_INVALID");
  const runtimeGamePackJson = decode(source.previewFiles.get("runtime-game-pack.json"));
  const runtimeReceiptJson = decode(source.previewFiles.get("runtime-receipt.json"));
  const spatialAssemblyJson = decode(source.previewFiles.get("spatial-assembly.json"));
  if (runtimeGamePackJson !== evidence.runtimeGamePackJson || runtimeReceiptJson !== evidence.runtimeReceiptJson) {
    fail("R14_QUALIFICATION_SOURCE_INVALID");
  }
  for (const text of [evidence.sceneBlueprintJson, runtimeGamePackJson, runtimeReceiptJson, spatialAssemblyJson,
    assetBundleJson, spatialEnvironmentBundleJson]) canonical(text);
  const blueprint = JSON.parse(evidence.sceneBlueprintJson);
  const materializedBriefs = new Set((blueprint.assetBriefs ?? [])
    .filter((brief) => brief?.kind !== "environment")
    .map((brief) => brief?.id));
  if ([...materializedBriefs].some((briefId) => typeof briefId !== "string")) {
    fail("R14_QUALIFICATION_SOURCE_INVALID");
  }
  const assetBundle = JSON.parse(assetBundleJson);
  const assetFiles = new Map();
  for (const materialization of assetBundle.materializations ?? []) {
    if (!materializedBriefs.has(materialization.assetBriefId)) continue;
    for (const asset of materialization.assets ?? []) {
      const bytes = source.previewFiles.get(asset.path);
      if (!(bytes instanceof Uint8Array) || assetFiles.has(asset.path)) fail("R14_QUALIFICATION_SOURCE_INVALID");
      assetFiles.set(asset.path, bytes);
    }
  }
  const environmentColliderBytes = source.previewFiles.get(COLLIDER_PATH);
  const splatBytes = source.previewFiles.get(SPLAT_PATH);
  if (!(environmentColliderBytes instanceof Uint8Array) || !(splatBytes instanceof Uint8Array)) {
    fail("R14_QUALIFICATION_SOURCE_INVALID");
  }
  return Object.freeze({
    sceneBlueprintJson: evidence.sceneBlueprintJson,
    runtimeGamePackJson,
    runtimeReceiptJson,
    spatialAssemblyJson,
    assetBundleJson,
    spatialEnvironmentBundleJson,
    assetFiles,
    environmentColliderBytes,
    environmentSplatBytes: splatBytes,
    spatialEnvironmentFiles: new Map([[COLLIDER_PATH, environmentColliderBytes], [SPLAT_PATH, splatBytes]]),
  });
}

export function parseR14QualificationArguments(args, tempRoot = temporaryRoot) {
  if (!Array.isArray(args) || args.length !== 8) fail("R14_QUALIFICATION_ARGUMENT_INVALID");
  const names = Object.freeze({
    "--prototype-run-root": "prototypeRunRoot",
    "--spatial-run-root": "spatialRunRoot",
    "--solved-run-root": "solvedRunRoot",
    "--run-id": "runId",
  });
  const values = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const name = names[args[index]]; const value = args[index + 1];
    if (!name || Object.hasOwn(values, name) || typeof value !== "string" || value.includes("\0")) {
      fail("R14_QUALIFICATION_ARGUMENT_INVALID");
    }
    values[name] = name === "runId" ? value : path.resolve(value);
  }
  const root = path.resolve(tempRoot);
  if (Object.keys(values).length !== 4 || !RUN_ID.test(values.runId) ||
      [values.prototypeRunRoot, values.spatialRunRoot, values.solvedRunRoot]
        .some((value) => !path.isAbsolute(value) || path.dirname(value) !== root)) {
    fail("R14_QUALIFICATION_ARGUMENT_INVALID");
  }
  return Object.freeze({ ...values, temporaryRoot: root });
}

export async function runR14Qualification({ runId, inputs }, operations) {
  let stage = "INPUT";
  try {
    if (!RUN_ID.test(runId) || !inputs || !operations) fail("R14_QUALIFICATION_INPUT_INVALID");
    stage = "SYNTHESIS";
    const synthesized = await operations.synthesize({
      sceneBlueprintJson: inputs.sceneBlueprintJson,
      runtimeGamePackJson: inputs.runtimeGamePackJson,
      runtimeReceiptJson: inputs.runtimeReceiptJson,
      assetBundleJson: inputs.assetBundleJson,
    });
    if (!synthesized?.ok) return staticFailure("synthesis", synthesized?.diagnostics ?? []);
    stage = "ANALYSIS";
    const analyzed = await operations.analyze({
      spatialIntentJson: synthesized.canonicalSpatialIntentJson,
      spatialEnvironmentBundleJson: inputs.spatialEnvironmentBundleJson,
      spatialEnvironmentFiles: inputs.spatialEnvironmentFiles,
      spatialAssemblyJson: inputs.spatialAssemblyJson,
    });
    if (!analyzed?.ok) return staticFailure("analysis", analyzed?.diagnostics ?? []);
    let solved; let verified;
    for (let attempt = 0; attempt < MAX_PHYSICAL_ATTEMPTS; attempt += 1) {
      stage = "SOLVER";
      solved = await operations.solve({
        spatialIntentJson: synthesized.canonicalSpatialIntentJson,
        environmentFactsJson: analyzed.canonicalFactsJson,
        assetBundleJson: inputs.assetBundleJson,
        runtimeGamePackJson: inputs.runtimeGamePackJson,
        runtimeReceiptJson: inputs.runtimeReceiptJson,
      });
      if (!solved?.ok) return staticFailure("solver", solved?.diagnostics ?? []);
      stage = "VERIFICATION";
      verified = await operations.verify({
        spatialIntentJson: synthesized.canonicalSpatialIntentJson,
        environmentFactsJson: analyzed.canonicalFactsJson,
        spatialSolutionJson: solved.canonicalSpatialSolutionJson,
        assetBundleJson: inputs.assetBundleJson,
        runtimeGamePackJson: inputs.runtimeGamePackJson,
        runtimeReceiptJson: inputs.runtimeReceiptJson,
        spatialAssemblyJson: inputs.spatialAssemblyJson,
        environmentColliderBytes: inputs.environmentColliderBytes,
        environmentSplatBytes: inputs.environmentSplatBytes,
        assetFiles: inputs.assetFiles,
      });
      if (verified?.ok) break;
      if (attempt + 1 >= MAX_PHYSICAL_ATTEMPTS) {
        return staticFailure("verification", [Object.freeze({
          phase: "verification", severity: "error", code: "R14_QUALIFICATION_PHYSICAL_RETRY_LIMIT_EXCEEDED",
          path: "", message: "R14_QUALIFICATION_PHYSICAL_RETRY_LIMIT_EXCEEDED",
        })]);
      }
      const rejectPhysicalCandidate = operations.rejectPhysicalCandidate ?? operations.rejectPlacementCandidate;
      if (!rejectPhysicalCandidate ||
          !rejectPhysicalCandidate({ solved, diagnostics: verified?.diagnostics ?? [] })) {
        return staticFailure("verification", verified?.diagnostics ?? []);
      }
    }
    stage = "PUBLISH";
    const published = await operations.publish({
      runId,
      artifacts: {
        spatialIntentJson: synthesized.canonicalSpatialIntentJson,
        environmentFactsJson: analyzed.canonicalFactsJson,
        spatialSolutionJson: solved.canonicalSpatialSolutionJson,
        spatialSolutionReportJson: solved.canonicalSpatialSolutionReportJson,
        spatialVerificationReportJson: verified.canonicalVerificationReportJson,
      },
    });
    return Object.freeze({ ok: true, runId, solutionSha256: published.solutionSha256 });
  } catch (error) {
    if (error instanceof R14QualificationOperationalError) throw error;
    fail(`R14_QUALIFICATION_${stage}_INTERNAL_ERROR`);
  }
}

export function r14PhysicalRejectionCandidate({ solved, diagnostics }) {
  if (!Array.isArray(diagnostics) || diagnostics.length !== 1) return null;
  const code = diagnostics[0]?.code;
  const placementMatch = /^\/placements\/(\d+)$/u.exec(diagnostics[0]?.path ?? "");
  if (["PROTOTYPE_SPATIAL_VERIFY_ASSET_GROUNDING_FAILED", "PROTOTYPE_SPATIAL_VERIFY_ASSET_OVERLAP",
    "PROTOTYPE_SPATIAL_VERIFY_ASSET_PENETRATION"].includes(code) && placementMatch) {
    const key = spatialPlacementCandidateKey(solved?.spatialSolution?.placements?.[Number(placementMatch[1])]);
    return key ? Object.freeze({ kind: "placement", key,
      maximum: PROTOTYPE_SPATIAL_SOLUTION_PROFILE.maxCandidatesPerItem * PROTOTYPE_SPATIAL_SOLUTION_PROFILE.maxPlacements }) : null;
  }
  const contextMatch = /^\/nodeContexts\/(\d+)(?:\/(?:actionTerminal|playerSpawn))?$/u.exec(diagnostics[0]?.path ?? "");
  if (!["PROTOTYPE_SPATIAL_VERIFY_PATH_BLOCKED", "PROTOTYPE_SPATIAL_VERIFY_PATH_UNREACHABLE",
    "PROTOTYPE_SPATIAL_VERIFY_SPAWN_COLLISION", "PROTOTYPE_SPATIAL_VERIFY_TERMINAL_COLLISION",
    "PROTOTYPE_SPATIAL_VERIFY_TERMINAL_SIGHT_BLOCKED"].includes(code) || !contextMatch) return null;
  const context = solved?.spatialSolution?.nodeContexts?.[Number(contextMatch[1])];
  const terminalSpecific = ["PROTOTYPE_SPATIAL_VERIFY_TERMINAL_COLLISION",
    "PROTOTYPE_SPATIAL_VERIFY_TERMINAL_SIGHT_BLOCKED"].includes(code);
  const key = terminalSpecific ? spatialTerminalCandidateKey(context) : spatialStationCandidateKey(context);
  return key ? Object.freeze({ kind: "station", key,
    maximum: PROTOTYPE_SPATIAL_SOLUTION_PROFILE.maxCandidatesPerItem * PROTOTYPE_SPATIAL_SOLUTION_PROFILE.maxZones }) : null;
}

export async function qualifyR14SpatialSolver(options, overrides = {}) {
  const services = Object.freeze({ ...defaultServices, ...(overrides.services ?? {}) });
  let stage = "SOURCE";
  try {
    const loaded = overrides.loadSource
      ? await overrides.loadSource(options, services)
      : await loadQualificationSource(options, services);
    stage = "INPUT";
    const inputs = overrides.inputs ? overrides.inputs(loaded) : qualificationInputs(loaded);
    stage = "GODOT";
    const analyzer = (overrides.createAnalyzer ?? createGodotEnvironmentAnalyzer)({ godotBin: options.godotBin });
    const verifier = (overrides.createVerifier ?? createGodotSpatialSolutionVerifier)({ godotBin: options.godotBin });
    const optionsForSource = sourceOptions(options, services);
    const rejectedCandidates = new Set();
    const rejectedStations = new Set();
    const candidateRegion = overrides.solve ? null :
      spatialWalkableEnvelopeCandidateRegion(canonical(inputs.spatialAssemblyJson));
    if (!overrides.solve &&
        (!validReport(validatePrototypeSpatialAssemblyJson(inputs.spatialAssemblyJson)) || !candidateRegion)) {
      fail("R14_QUALIFICATION_SOURCE_INVALID");
    }
    return await runR14Qualification({ runId: options.runId, inputs }, {
      synthesize: overrides.synthesize ?? synthesizePrototypeSpatialIntent,
      analyze: (request) => (overrides.analyze ?? analyzePrototypeEnvironment)(request, analyzer),
      solve: overrides.solve ?? ((request) => solvePrototypeSpatialLayoutInternal(
        request, rejectedCandidates, rejectedStations, candidateRegion)),
      verify: (request) => (overrides.verify ?? verifyPrototypeSpatialSolution)(request, verifier),
      rejectPhysicalCandidate: overrides.rejectPhysicalCandidate ?? overrides.rejectPlacementCandidate ?? (({ solved, diagnostics }) => {
        const candidate = r14PhysicalRejectionCandidate({ solved, diagnostics });
        if (!candidate) return false;
        const rejected = candidate.kind === "placement" ? rejectedCandidates : rejectedStations;
        if (rejected.has(candidate.key) || rejected.size >= candidate.maximum) return false;
        rejected.add(candidate.key); return true;
      }),
      publish: ({ runId, artifacts }) => (overrides.publish ?? publishSolvedSpatialPrototypeRun)({
        runId,
        runRoot: options.solvedRunRoot,
        temporaryRoot: options.temporaryRoot,
        sourceOptions: optionsForSource,
        artifacts,
        services,
        canonicalizeJsonValue,
      }),
    });
  } catch (error) {
    if (error instanceof R14QualificationOperationalError) throw error;
    fail(`R14_QUALIFICATION_${stage}_INTERNAL_ERROR`);
  }
}

async function main() {
  let options;
  try {
    options = parseR14QualificationArguments(process.argv.slice(2));
    const godot = resolveGodotBinary();
    const result = await qualifyR14SpatialSolver({ ...options, godotBin: godot.command });
    if (!result.ok) {
      const code = result.diagnostics[0]?.code ?? `R14_QUALIFICATION_${result.stage.toUpperCase()}_FAILED`;
      process.stderr.write(`${code}\n`); process.exitCode = 1; return;
    }
    process.stdout.write(`R14_SPATIAL_SOLVER_QUALIFIED run=${result.runId} solution=${result.solutionSha256}\n`);
  } catch (error) {
    const code = error instanceof R14QualificationOperationalError ? error.code : "R14_QUALIFICATION_INTERNAL_ERROR";
    process.stderr.write(`${code}\n`); process.exitCode = 2;
  }
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) await main();
