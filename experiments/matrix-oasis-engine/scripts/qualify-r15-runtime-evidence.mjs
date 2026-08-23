import { createHash } from "node:crypto";
import { lstat, mkdir, mkdtemp, open, readFile, readdir, realpath, rename, rm, rmdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import { assemblePrototypeSpatialScene } from "@matrix-oasis/prototype-spatial-assembler";
import {
  collectPrototypeRuntimeEvidence,
  createGodotRuntimeEvidenceRunner,
  planPrototypeRuntimeReplay,
} from "@matrix-oasis/prototype-runtime-evidence";
import { createGodotSpatialSolutionVerifier, verifyPrototypeSpatialSolution } from "@matrix-oasis/prototype-spatial-verifier";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { recoverPrototypeRuns } from "./lib/prototype-cache-core.mjs";
import { runRuntimeEvidenceQualification } from "./lib/runtime-evidence-core.mjs";
import { publishRuntimeEvidenceRun } from "./lib/runtime-evidence-cache-core.mjs";
import { resolveGodotBinary } from "./lib/godot-core.mjs";
import { r14PhysicalRejectionCandidate } from "./qualify-r14-spatial-solver.mjs";
import {
  loadVerifiedR14SpatialPrototypeRun,
  loadVerifiedSolvedSpatialPrototypeRun,
} from "./lib/solved-spatial-cache-core.mjs";
import {
  solvePrototypeSpatialLayoutInternal,
  spatialWalkableEnvelopeCandidateRegion,
} from "../packages/prototype-spatial-solver/src/solver.mjs";

const SOURCE_RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;
const moduleRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const temporaryRoot = path.resolve(path.parse(moduleRoot).root, "tmp");
const services = Object.freeze({ lstat, mkdir, mkdtemp, openFile: open, readFile, readdir, realpath, rename, rm, rmdir });
const TEXT = new TextDecoder("utf-8", { fatal: true });
const TOP_LEVEL = new Set([
  "runtime-game-pack.json", "runtime-receipt.json", "environment-facts.json", "spatial-intent.json",
  "prototype-asset-bundle.json", "spatial-solution.json", "spatial-verification-report.json",
  "scene-pack.json", "spatial-assembly.json",
]);

export const R15_COMPATIBILITY_VERIFICATION_LIMIT = 3;
export const R15_COMPATIBILITY_OPERATIONAL_RETRY_LIMIT = 1;

function fail(code) { const error = new Error(code); error.code = code; throw error; }
function staticFailure(code, path = "", message = code) {
  return Object.freeze({ ok: false, diagnostics: Object.freeze([
    Object.freeze({ phase: "qualification", severity: "error", code, path, message }),
  ]) });
}
function resolveFailure(message) { return staticFailure("R15_QUALIFICATION_RESOLVE_FAILED", "", message); }
export function r15PhysicalScreenFailure(result) {
  const code = result?.diagnostics?.length === 1 ? result.diagnostics[0]?.code : "";
  if (code === "R15_COMPATIBILITY_VERIFICATION_LIMIT_EXCEEDED") {
    return resolveFailure("R15_RESOLVE_PHYSICAL_SCREEN_LIMIT_EXCEEDED");
  }
  if (code === "R15_COMPATIBILITY_CANDIDATE_REPEATED") {
    return resolveFailure("R15_RESOLVE_PHYSICAL_CANDIDATE_REPEATED");
  }
  if (code === "R15_COMPATIBILITY_VERIFIER_INTERNAL_ERROR") {
    return resolveFailure("R15_RESOLVE_PHYSICAL_VERIFIER_INTERNAL_ERROR");
  }
  if (code === "R15_COMPATIBILITY_SOLVER_INTERNAL_ERROR") {
    return resolveFailure("R15_RESOLVE_PHYSICAL_SOLVER_INTERNAL_ERROR");
  }
  if (/^PROTOTYPE_SPATIAL_SOLVER_/u.test(code)) {
    return resolveFailure("R15_RESOLVE_PHYSICAL_SOLVE_FAILED");
  }
  return resolveFailure("R15_RESOLVE_PHYSICAL_REJECTION_UNMAPPED");
}
function sha256(bytes) { return createHash("sha256").update(bytes).digest("hex"); }
function canonicalText(bytes) {
  const text = TEXT.decode(bytes);
  try { if (canonicalizeJsonValue(JSON.parse(text)) !== text) fail("R15_QUALIFICATION_SOURCE_INVALID"); }
  catch (error) { if (error?.code) throw error; fail("R15_QUALIFICATION_SOURCE_INVALID"); }
  return text;
}
export function parseR15QualificationArguments(args, tempRoot = temporaryRoot) {
  if (!Array.isArray(args) || args.length !== 10) fail("R15_QUALIFICATION_ARGUMENT_INVALID");
  const names = Object.freeze({
    "--prototype-run-root": "prototypeRunRoot",
    "--spatial-run-root": "spatialRunRoot",
    "--solved-run-root": "solvedRunRoot",
    "--evidence-run-root": "evidenceRunRoot",
    "--run-id": "runId",
  });
  const values = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const key = names[args[index]]; const value = args[index + 1];
    if (!key || Object.hasOwn(values, key) || typeof value !== "string" || value.includes("\0")) {
      fail("R15_QUALIFICATION_ARGUMENT_INVALID");
    }
    values[key] = key === "runId" ? value : path.resolve(value);
  }
  const root = path.resolve(tempRoot);
  if (!SOURCE_RUN_ID.test(values.runId) ||
      [values.prototypeRunRoot, values.spatialRunRoot, values.solvedRunRoot, values.evidenceRunRoot]
        .some((value) => path.dirname(value) !== root)) fail("R15_QUALIFICATION_ARGUMENT_INVALID");
  return Object.freeze({ ...values, temporaryRoot: root });
}
function sourceOptions(options) {
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
async function safeFile(candidate, parent) {
  const absolute = path.resolve(candidate);
  const resolved = path.resolve(await realpath(absolute));
  const stat = await lstat(absolute, { bigint: true });
  if (resolved !== absolute || !stat.isFile() || stat.isSymbolicLink() || !absolute.startsWith(`${path.resolve(parent)}${path.sep}`)) {
    fail("R15_QUALIFICATION_SOURCE_INVALID");
  }
  const bytes = new Uint8Array(await readFile(absolute));
  const after = await lstat(absolute, { bigint: true });
  if (after.dev !== stat.dev || after.ino !== stat.ino || after.size !== stat.size || after.mtimeNs !== stat.mtimeNs) {
    fail("R15_QUALIFICATION_SOURCE_INVALID");
  }
  return bytes;
}
function curatedPreviewFiles(loaded, intentBytes, bundleBytes) {
  const files = new Map();
  for (const [relative, bytes] of loaded.previewFiles) {
    if (TOP_LEVEL.has(relative) || /^assets\/[A-Za-z0-9._-]+$/u.test(relative)) files.set(relative, bytes.slice());
  }
  files.set("spatial-intent.json", intentBytes.slice());
  files.set("prototype-asset-bundle.json", bundleBytes.slice());
  return files;
}
function requestFromFiles(files) {
  const text = (name) => canonicalText(files.get(name));
  return Object.freeze({
    runtimeGamePackJson: text("runtime-game-pack.json"),
    runtimeReceiptJson: text("runtime-receipt.json"),
    environmentFactsJson: text("environment-facts.json"),
    spatialIntentJson: text("spatial-intent.json"),
    assetBundleJson: text("prototype-asset-bundle.json"),
    spatialSolutionJson: text("spatial-solution.json"),
    spatialVerificationReportJson: text("spatial-verification-report.json"),
  });
}
function assetFiles(bundleJson, previewFiles) {
  const bundle = JSON.parse(bundleJson); const files = new Map();
  for (const item of bundle.materializations ?? []) {
    if (item.source?.type === "builtin-template") continue;
    for (const asset of item.assets ?? []) {
    const bytes = previewFiles.get(asset.path);
    if (!(bytes instanceof Uint8Array)) fail("R15_QUALIFICATION_SOURCE_INVALID");
    files.set(asset.path, bytes);
    }
  }
  return files;
}

export function r15PhysicalRejectionCandidate({ solved, diagnostics }) {
  return r14PhysicalRejectionCandidate({ solved, diagnostics });
}

async function solveCompatibilityCandidate(operations, rejectedPlacements, rejectedStations) {
  try {
    return await operations.solve({
      rejectedPlacements: new Set(rejectedPlacements),
      rejectedStations: new Set(rejectedStations),
    });
  } catch {
    return staticFailure("R15_COMPATIBILITY_SOLVER_INTERNAL_ERROR");
  }
}

export async function selectR15VerifiedSpatialCandidate(request, operations) {
  if (!request || typeof request.initialSolutionJson !== "string" ||
      (request.environmentFactsJson !== undefined && typeof request.environmentFactsJson !== "string") ||
      !operations || typeof operations.solve !== "function" || typeof operations.verify !== "function") {
    return staticFailure("R15_COMPATIBILITY_INPUT_INVALID");
  }
  const rejectedPlacements = new Set(request.rejectedPlacements ?? []);
  const rejectedStations = new Set(request.rejectedStations ?? []);
  const repairs = [];
  let solved;
  try {
    solved = Object.freeze({
      spatialSolution: JSON.parse(request.initialSolutionJson),
      canonicalSpatialSolutionJson: request.initialSolutionJson,
    });
  } catch {
    return staticFailure("R15_COMPATIBILITY_INPUT_INVALID");
  }
  for (let verificationCount = 1; verificationCount <= R15_COMPATIBILITY_VERIFICATION_LIMIT;
    verificationCount += 1) {
    let verified;
    for (let operationalAttempt = 0;
      operationalAttempt <= R15_COMPATIBILITY_OPERATIONAL_RETRY_LIMIT; operationalAttempt += 1) {
      try {
        verified = await operations.verify(solved.canonicalSpatialSolutionJson);
        break;
      } catch {
        if (operationalAttempt === R15_COMPATIBILITY_OPERATIONAL_RETRY_LIMIT) {
          return staticFailure("R15_COMPATIBILITY_VERIFIER_INTERNAL_ERROR");
        }
      }
    }
    if (verified?.ok && typeof verified.canonicalVerificationReportJson === "string") {
      return Object.freeze({
        ok: true,
        canonicalSpatialSolutionJson: solved.canonicalSpatialSolutionJson,
        canonicalVerificationReportJson: verified.canonicalVerificationReportJson,
        rejectedPlacements: Object.freeze([...rejectedPlacements]),
        rejectedStations: Object.freeze([...rejectedStations]),
        repairs: Object.freeze(repairs.map((item) => Object.freeze({ ...item }))),
        verificationCount,
      });
    }
    if (verificationCount === R15_COMPATIBILITY_VERIFICATION_LIMIT) {
      return staticFailure("R15_COMPATIBILITY_VERIFICATION_LIMIT_EXCEEDED");
    }
    const rejection = r15PhysicalRejectionCandidate({ solved, diagnostics: verified?.diagnostics ?? [] });
    if (!rejection) return Object.freeze({ ok: false, diagnostics: Object.freeze((verified?.diagnostics ?? []).slice()) });
    const rejected = rejection.kind === "placement" ? rejectedPlacements : rejectedStations;
    const freshKeys = [rejection.key].filter((key) => !rejected.has(key));
    if (freshKeys.length === 0 || rejected.size + freshKeys.length > rejection.maximum) {
      return staticFailure("R15_COMPATIBILITY_CANDIDATE_REPEATED");
    }
    freshKeys.forEach((key) => rejected.add(key));
    repairs.push(Object.freeze({
      round: repairs.length + 1,
      kind: rejection.kind,
      candidateKeySha256: `sha256:${sha256(Buffer.from(rejection.key, "utf8"))}`,
      diagnosticCode: rejection.kind === "placement"
        ? "R15_PLACEMENT_RUNTIME_INVALID" : "R15_STATION_RUNTIME_INVALID",
    }));
    const next = await solveCompatibilityCandidate(operations, rejectedPlacements, rejectedStations);
    if (!next?.ok || typeof next.canonicalSpatialSolutionJson !== "string" || !next.spatialSolution) {
      return Object.freeze({ ok: false, diagnostics: Object.freeze((next?.diagnostics ?? []).slice()) });
    }
    solved = next;
  }
  return staticFailure("R15_COMPATIBILITY_VERIFICATION_LIMIT_EXCEEDED");
}

export async function loadR15QualificationSource(options) {
  let loaded; let solutionHex; let historicalOverlay = false;
  let compatibilityRejections = Object.freeze({ placements: Object.freeze([]), stations: Object.freeze([]) });
  let compatibilityRepairs = Object.freeze([]);
  try {
    loaded = await loadVerifiedSolvedSpatialPrototypeRun({
      runId: options.runId,
      runRoot: options.solvedRunRoot,
      temporaryRoot: options.temporaryRoot,
      sourceOptions: sourceOptions(options),
      services,
      canonicalizeJsonValue,
    });
    solutionHex = loaded.solutionSha256.slice(7);
  } catch {
    historicalOverlay = true;
    const pointerBytes = await safeFile(path.join(options.solvedRunRoot, "solved-current.json"), options.solvedRunRoot);
    const pointer = JSON.parse(canonicalText(pointerBytes));
    if (pointer.format !== "matrix-oasis.prototype-solved-current" || pointer.formatVersion !== "0.1.0" ||
        pointer.runId !== options.runId || !/^sha256:[0-9a-f]{64}$/u.test(pointer.solutionSha256)) {
      fail("R15_QUALIFICATION_SOURCE_INVALID");
    }
    solutionHex = pointer.solutionSha256.slice(7);
    loaded = await loadVerifiedR14SpatialPrototypeRun({ runId: options.runId, ...sourceOptions(options).cacheOptions });
    if (!loaded || !(loaded.previewFiles instanceof Map)) fail("R15_QUALIFICATION_SOURCE_INVALID");
  }
  const overlay = path.join(options.solvedRunRoot, "solved-runs", options.runId, solutionHex);
  const prototype = path.join(options.prototypeRunRoot, "runs", options.runId);
  const intentBytes = await safeFile(path.join(overlay, "spatial-intent.json"), options.solvedRunRoot);
  const bundleBytes = await safeFile(path.join(prototype, "prototype-asset-bundle.json"), options.prototypeRunRoot);
  const previewFiles = curatedPreviewFiles(loaded, intentBytes, bundleBytes);
  if (historicalOverlay) {
    for (const name of ["environment-facts.json", "spatial-solution.json", "spatial-verification-report.json"]) {
      previewFiles.set(name, await safeFile(path.join(overlay, name), options.solvedRunRoot));
    }
    if (`sha256:${sha256(previewFiles.get("spatial-solution.json"))}` !== `sha256:${solutionHex}`) {
      fail("R15_QUALIFICATION_SOURCE_INVALID");
    }
  }
  if (!(previewFiles.get("spatial-solution.json") instanceof Uint8Array)) {
    const solutionBytes = await safeFile(path.join(overlay, "spatial-solution.json"), options.solvedRunRoot);
    if (`sha256:${sha256(solutionBytes)}` !== `sha256:${solutionHex}`) fail("R15_QUALIFICATION_SOURCE_INVALID");
    previewFiles.set("spatial-solution.json", solutionBytes);
  }
  if (historicalOverlay || !(previewFiles.get("spatial-verification-report.json") instanceof Uint8Array)) {
    const runtimeGamePackJson = canonicalText(previewFiles.get("runtime-game-pack.json"));
    const runtimeReceiptJson = canonicalText(previewFiles.get("runtime-receipt.json"));
    const environmentFactsJson = canonicalText(previewFiles.get("environment-facts.json") ??
      await safeFile(path.join(overlay, "environment-facts.json"), options.solvedRunRoot));
    previewFiles.set("environment-facts.json", new TextEncoder().encode(environmentFactsJson));
    const assetBundleJson = canonicalText(bundleBytes);
    const verifier = createGodotSpatialSolutionVerifier({ godotBin: options.godotBin });
    const spatialIntentJson = canonicalText(intentBytes);
    const spatialAssemblyJson = canonicalText(previewFiles.get("spatial-assembly.json"));
    const materializedFiles = assetFiles(assetBundleJson, previewFiles);
    const verifySolution = async (spatialSolutionJson) => await verifyPrototypeSpatialSolution({
      spatialIntentJson, environmentFactsJson, spatialSolutionJson, assetBundleJson, runtimeGamePackJson,
      runtimeReceiptJson, spatialAssemblyJson,
      environmentColliderBytes: previewFiles.get("assets/environment-collider.glb"),
      environmentSplatBytes: previewFiles.get("assets/environment.compressed.ply"), assetFiles: materializedFiles,
    }, verifier);
    const region = spatialWalkableEnvelopeCandidateRegion(JSON.parse(spatialAssemblyJson));
    if (!region) fail("R15_QUALIFICATION_SOURCE_INVALID");
    const selected = await selectR15VerifiedSpatialCandidate({
      initialSolutionJson: canonicalText(previewFiles.get("spatial-solution.json")),
      environmentFactsJson,
    }, {
      solve: ({ rejectedPlacements, rejectedStations }) => solvePrototypeSpatialLayoutInternal({
        spatialIntentJson, environmentFactsJson, assetBundleJson, runtimeGamePackJson, runtimeReceiptJson,
      }, rejectedPlacements, rejectedStations, region),
      verify: verifySolution,
    });
    if (!selected.ok) return selected;
    previewFiles.set("spatial-solution.json", new TextEncoder().encode(selected.canonicalSpatialSolutionJson));
    previewFiles.set("spatial-verification-report.json",
      new TextEncoder().encode(selected.canonicalVerificationReportJson));
    compatibilityRejections = Object.freeze({
      placements: selected.rejectedPlacements,
      stations: selected.rejectedStations,
    });
    compatibilityRepairs = selected.repairs;
  }
  const input = requestFromFiles(previewFiles);
  const planned = await planPrototypeRuntimeReplay(input);
  if (!planned?.ok) return Object.freeze({ ok: false, diagnostics: planned?.diagnostics ?? [] });
  const source = Object.freeze({
    ...input,
    replayPlanJson: planned.canonicalReplayPlanJson,
    previewFiles,
    spatialAssemblyJson: canonicalText(previewFiles.get("spatial-assembly.json")),
    environmentColliderBytes: previewFiles.get("assets/environment-collider.glb"),
    environmentSplatBytes: previewFiles.get("assets/environment.compressed.ply"),
    assetFiles: assetFiles(input.assetBundleJson, previewFiles),
  });
  const preflight = historicalOverlay ? await collectPrototypeRuntimeEvidence(
    { replayPlanJson: source.replayPlanJson, previewFiles: source.previewFiles },
    createGodotRuntimeEvidenceRunner({ godotBin: options.godotBin }),
  ) : null;
  return Object.freeze({ ok: true, source, preflight, compatibilityRejections, compatibilityRepairs });
}

export async function qualifyR15RuntimeEvidence(options, overrides = {}) {
  const loaded = await (overrides.loadSource ?? loadR15QualificationSource)(options);
  if (!loaded?.ok) return loaded;
  const runner = (overrides.createEvidenceRunner ?? createGodotRuntimeEvidenceRunner)({ godotBin: options.godotBin });
  const verifier = (overrides.createVerifier ?? createGodotSpatialSolutionVerifier)({ godotBin: options.godotBin });
  const rejectedPlacements = new Set(loaded.compatibilityRejections?.placements ?? []);
  const rejectedStations = new Set(loaded.compatibilityRejections?.stations ?? []);
  let preflight = loaded.preflight ?? null;
  const candidateRegion = spatialWalkableEnvelopeCandidateRegion(JSON.parse(loaded.source.spatialAssemblyJson));
  if (!candidateRegion) fail("R15_QUALIFICATION_SOURCE_INVALID");
  return await runRuntimeEvidenceQualification(loaded.source, {
    collect: async (source) => {
      if (preflight) { const result = preflight; preflight = null; return result; }
      return await (overrides.collect ?? collectPrototypeRuntimeEvidence)(
        { replayPlanJson: source.replayPlanJson, previewFiles: source.previewFiles }, runner);
    },
    resolve: async (source) => {
      const rejected = source.rejection.kind === "placement" ? rejectedPlacements : rejectedStations;
      if (rejected.has(source.rejection.key)) return resolveFailure("R15_RESOLVE_CANDIDATE_REPEATED");
      rejected.add(source.rejection.key);
      const solve = overrides.solve ?? solvePrototypeSpatialLayoutInternal;
      const verify = overrides.verify ?? verifyPrototypeSpatialSolution;
      const solved = await solve({
        spatialIntentJson: source.spatialIntentJson,
        environmentFactsJson: source.environmentFactsJson,
        assetBundleJson: source.assetBundleJson,
        runtimeGamePackJson: source.runtimeGamePackJson,
        runtimeReceiptJson: source.runtimeReceiptJson,
      }, rejectedPlacements, rejectedStations, candidateRegion);
      if (!solved?.ok) return resolveFailure("R15_RESOLVE_NO_SOLUTION");
      const selected = await selectR15VerifiedSpatialCandidate({
        initialSolutionJson: solved.canonicalSpatialSolutionJson,
        environmentFactsJson: source.environmentFactsJson,
        rejectedPlacements,
        rejectedStations,
      }, {
        solve: ({ rejectedPlacements: screenedPlacements, rejectedStations: screenedStations }) => solve({
          spatialIntentJson: source.spatialIntentJson,
          environmentFactsJson: source.environmentFactsJson,
          assetBundleJson: source.assetBundleJson,
          runtimeGamePackJson: source.runtimeGamePackJson,
          runtimeReceiptJson: source.runtimeReceiptJson,
        }, screenedPlacements, screenedStations, candidateRegion),
        verify: (spatialSolutionJson) => verify({
          spatialIntentJson: source.spatialIntentJson,
          environmentFactsJson: source.environmentFactsJson,
          spatialSolutionJson,
          assetBundleJson: source.assetBundleJson,
          runtimeGamePackJson: source.runtimeGamePackJson,
          runtimeReceiptJson: source.runtimeReceiptJson,
          spatialAssemblyJson: source.spatialAssemblyJson,
          environmentColliderBytes: source.environmentColliderBytes,
          environmentSplatBytes: source.environmentSplatBytes,
          assetFiles: source.assetFiles,
        }, verifier),
      });
      if (!selected.ok) return r15PhysicalScreenFailure(selected);
      rejectedPlacements.clear(); selected.rejectedPlacements.forEach((key) => rejectedPlacements.add(key));
      rejectedStations.clear(); selected.rejectedStations.forEach((key) => rejectedStations.add(key));
      const previewFiles = new Map(source.previewFiles);
      previewFiles.set("spatial-solution.json", new TextEncoder().encode(selected.canonicalSpatialSolutionJson));
      previewFiles.set("spatial-verification-report.json",
        new TextEncoder().encode(selected.canonicalVerificationReportJson));
      const next = requestFromFiles(previewFiles);
      const planned = await planPrototypeRuntimeReplay(next);
      if (!planned?.ok) return resolveFailure("R15_RESOLVE_REPLAY_PLAN_FAILED");
      return Object.freeze({ ok: true, source: Object.freeze({ ...source, ...next, previewFiles, replayPlanJson: planned.canonicalReplayPlanJson }) });
    },
    publish: async (source) => {
      const runId = sha256(Buffer.from(source.canonicalEvidenceJson, "utf8"));
      return await (overrides.publish ?? publishRuntimeEvidenceRun)({
        runRoot: options.evidenceRunRoot,
        temporaryRoot: options.temporaryRoot,
        runId,
        replayPlanJson: source.replayPlanJson,
        canonicalEvidenceJson: source.canonicalEvidenceJson,
        mediaFiles: source.mediaFiles,
        previewFiles: source.previewFiles,
      });
    },
  }, loaded.compatibilityRepairs);
}

async function main() {
  try {
    const parsed = parseR15QualificationArguments(process.argv.slice(2));
    const godot = resolveGodotBinary();
    const result = await qualifyR15RuntimeEvidence({ ...parsed, godotBin: godot.command });
    if (!result.ok) {
      process.stdout.write(`${JSON.stringify({ reportVersion: 1, valid: false, diagnostics: result.diagnostics })}\n`);
      process.exitCode = 1; return;
    }
    process.stdout.write(`MATRIX_OASIS_R15_RUNTIME_EVIDENCE_QUALIFIED run=${result.published.runId} attempt=${result.attempt}\n`);
  } catch (error) {
    process.stderr.write(`${error?.code ?? "R15_QUALIFICATION_INTERNAL_ERROR"}\n`);
    process.exitCode = 2;
  }
}

if (fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "")) await main();
