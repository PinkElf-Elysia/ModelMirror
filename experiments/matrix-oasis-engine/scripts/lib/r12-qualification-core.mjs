import {
  applyRuntimeGameSessionAction,
  createRuntimeGameSession,
  prepareRuntimeGamePackJson,
} from "@matrix-oasis/runtime-pack-simulator";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import { assemblePrototypeSpatialScene } from "@matrix-oasis/prototype-spatial-assembler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { lstat, mkdir, mkdtemp, open, readdir, realpath, rename, rm, rmdir } from "node:fs/promises";
import path from "node:path";
import { recoverPrototypeRuns } from "./prototype-cache-core.mjs";
import { loadVerifiedSpatialPrototypeRun, recoverSpatialPrototypeRuns } from "./spatial-cache-core.mjs";

const MAX_STATES = 16_384;
const MAX_PATH = 10_000;

export const R12_MVP_READY_MARKER = "MATRIX_OASIS_R12_MVP_READY";
export const R12_QUALIFICATION_MARKER = "MATRIX_OASIS_R12_QUALIFICATION_JSON:";
export const R12_LAST_TRAIN_ACCEPTANCE_PROFILE = deepFreeze({
  format: "matrix-oasis.prototype-acceptance-profile",
  formatVersion: "0.1.0",
  nodes: { min: 7, max: 16 },
  endings: { min: 3, max: 3 },
  actions: { min: 15, max: 64 * 16 },
  zones: { min: 2, max: 4 },
  props: { min: 3, max: 3 },
  characterPlaceholders: { min: 3, max: 3 },
  requireReachableCycle: true,
  requireAllEndingsReachable: true,
  requireAllNonEnvironmentBriefsBound: true,
});

export class R12QualificationOperationalError extends Error {
  constructor() {
    super("R12_QUALIFICATION_INTERNAL_ERROR");
    this.name = "R12QualificationOperationalError";
    this.code = "R12_QUALIFICATION_INTERNAL_ERROR";
  }
}

const CACHE_SERVICES = Object.freeze({
  lstat, mkdir, mkdtemp, openFile: open, readdir, realpath, rename, rm, rmdir,
});
const CACHE_ARGUMENTS = Object.freeze(["--prototype-run-root", "--spatial-run-root"]);

function deepFreeze(value) {
  if (value === null || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function semanticStateKey(snapshot) {
  return JSON.stringify([
    snapshot.status,
    snapshot.location.kind,
    snapshot.location.index,
    snapshot.variables,
  ]);
}

function safeRuntimeShape(text) {
  const value = JSON.parse(text);
  if (value === null || typeof value !== "object" || Array.isArray(value) ||
      !Array.isArray(value.endings) || value.endings.length < 1 || value.endings.length > 4096 ||
      value.endings.some((ending) => ending === null || typeof ending !== "object" || Array.isArray(ending) ||
        typeof ending.id !== "string")) throw new R12QualificationOperationalError();
  return value;
}

function failure(code, path = "") {
  return deepFreeze({ ok: false, diagnostics: [{
    phase: "qualification", severity: "error", code, path, message: code,
  }] });
}

export async function analyzeRuntimeReachability(runtimeGamePackJson, runtimeReceiptJson) {
  try {
    if (typeof runtimeGamePackJson !== "string" || typeof runtimeReceiptJson !== "string") {
      return failure("R12_RUNTIME_INPUT_INVALID");
    }
    const prepared = await prepareRuntimeGamePackJson(runtimeGamePackJson, runtimeReceiptJson);
    if (!prepared?.ok) return failure("R12_RUNTIME_INPUT_INVALID");
    const runtimePack = safeRuntimeShape(runtimeGamePackJson);
    const created = createRuntimeGameSession(prepared.prepared, { stepLimit: MAX_PATH });
    if (!created?.ok) return failure("R12_RUNTIME_SESSION_INVALID");

    const initialKey = semanticStateKey(created.snapshot);
    const visited = new Map([[initialKey, Object.freeze([])]]);
    const queue = [{ snapshot: created.snapshot, inspection: created.inspection, path: Object.freeze([]) }];
    const endingPaths = new Map();
    let loopPath = null;

    while (queue.length > 0) {
      if (visited.size > MAX_STATES) return failure("R12_RUNTIME_STATE_LIMIT");
      const current = queue.shift();
      if (current.inspection.status === "ended") {
        if (!endingPaths.has(current.inspection.location.id)) endingPaths.set(current.inspection.location.id, current.path);
        continue;
      }
      for (const action of current.inspection.actions) {
        if (!action.available) continue;
        const applied = applyRuntimeGameSessionAction(prepared.prepared, current.snapshot, action.id);
        if (!applied?.ok) return failure("R12_RUNTIME_TRACE_INVALID");
        const nextPath = Object.freeze([...current.path, action.id]);
        const nextKey = semanticStateKey(applied.snapshot);
        if (applied.inspection.status === "active" && visited.has(nextKey)) {
          if (loopPath === null) loopPath = nextPath;
          continue;
        }
        if (!visited.has(nextKey)) {
          visited.set(nextKey, nextPath);
          queue.push({ snapshot: applied.snapshot, inspection: applied.inspection, path: nextPath });
        }
      }
    }

    const declaredEndingIds = runtimePack.endings.map((ending) => ending.id);
    const orderedEndingPaths = declaredEndingIds
      .filter((id) => endingPaths.has(id))
      .map((id) => Object.freeze({ endingId: id, actionIds: endingPaths.get(id) }));
    return deepFreeze({
      ok: true,
      evidence: {
        declaredEndingCount: declaredEndingIds.length,
        reachableEndingCount: orderedEndingPaths.length,
        allEndingsReachable: orderedEndingPaths.length === declaredEndingIds.length,
        hasReachableLoop: loopPath !== null,
        endingPaths: orderedEndingPaths,
        loopActionIds: loopPath,
        visitedStateCount: visited.size,
      },
    });
  } catch (error) {
    if (error instanceof R12QualificationOperationalError) throw error;
    throw new R12QualificationOperationalError();
  }
}

export function parseR12CacheVerificationArguments(args) {
  if (!Array.isArray(args) || args.length !== 4) throw new R12QualificationOperationalError();
  const values = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const name = args[index]; const value = args[index + 1];
    if (!CACHE_ARGUMENTS.includes(name) || Object.hasOwn(values, name) || typeof value !== "string" || value.length < 1) {
      throw new R12QualificationOperationalError();
    }
    values[name] = path.resolve(value);
  }
  if (Object.keys(values).length !== CACHE_ARGUMENTS.length) throw new R12QualificationOperationalError();
  const temporaryRoot = path.resolve(path.parse(values["--prototype-run-root"]).root, "tmp");
  for (const value of Object.values(values)) {
    if (path.dirname(value) !== temporaryRoot) throw new R12QualificationOperationalError();
  }
  return deepFreeze({
    prototypeRunRoot: values["--prototype-run-root"],
    spatialRunRoot: values["--spatial-run-root"],
    temporaryRoot,
  });
}

export async function verifyR12NeutralSpatialCache(options) {
  try {
    if (options === null || typeof options !== "object" || Array.isArray(options) ||
        Object.getPrototypeOf(options) !== Object.prototype ||
        JSON.stringify(Object.keys(options).sort()) !== JSON.stringify(["prototypeRunRoot", "spatialRunRoot", "temporaryRoot"].sort())) {
      throw new R12QualificationOperationalError();
    }
    const dependencies = {
      services: CACHE_SERVICES,
      recoverPrototypeRuns,
      assemblePrototypeScene,
      assemblePrototypeSpatialScene,
      canonicalizeJsonValue,
    };
    const recovered = await recoverSpatialPrototypeRuns({
      runRoot: options.spatialRunRoot,
      prototypeRunRoot: options.prototypeRunRoot,
      temporaryRoot: options.temporaryRoot,
      ...dependencies,
    });
    if (typeof recovered.currentRunId !== "string") return failure("R12_NEUTRAL_CACHE_INVALID");
    const loaded = await loadVerifiedSpatialPrototypeRun({
      runId: recovered.currentRunId,
      runRoot: options.spatialRunRoot,
      prototypeRunRoot: options.prototypeRunRoot,
      temporaryRoot: options.temporaryRoot,
      ...dependencies,
    });
    const decoder = new TextDecoder("utf-8", { fatal: true });
    const runtime = decoder.decode(loaded.previewFiles.get("runtime-game-pack.json"));
    const receipt = decoder.decode(loaded.previewFiles.get("runtime-receipt.json"));
    const reachability = await analyzeRuntimeReachability(runtime, receipt);
    if (!reachability.ok || !reachability.evidence.allEndingsReachable) return failure("R12_NEUTRAL_CACHE_RUNTIME_INVALID");
    return deepFreeze({
      ok: true,
      evidence: {
        runId: loaded.runId,
        promptSha256: loaded.promptSha256,
        model: loaded.model,
        declaredEndingCount: reachability.evidence.declaredEndingCount,
        reachableEndingCount: reachability.evidence.reachableEndingCount,
        hasReachableLoop: reachability.evidence.hasReachableLoop,
      },
    });
  } catch (error) {
    if (error instanceof R12QualificationOperationalError) throw error;
    throw new R12QualificationOperationalError();
  }
}
