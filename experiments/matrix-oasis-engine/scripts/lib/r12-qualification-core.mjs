import {
  applyRuntimeGameSessionAction,
  createRuntimeGameSession,
  prepareRuntimeGamePackJson,
} from "@matrix-oasis/runtime-pack-simulator";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import { assemblePrototypeSpatialScene } from "@matrix-oasis/prototype-spatial-assembler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { createHash } from "node:crypto";
import { lstat, mkdir, mkdtemp, open, readdir, realpath, rename, rm, rmdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
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
const CALL_ARGUMENTS = Object.freeze(["--prompt-file", "--profile", "--run-root"]);
const MODULE_ROOT = path.resolve(fileURLToPath(new URL("../..", import.meta.url)));
const TEMPORARY_ROOT = path.resolve(path.parse(process.cwd()).root, "tmp");

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

function exactRecord(value, keys) {
  return value !== null && typeof value === "object" && !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype && Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key));
}

function safeRuntimeShape(text) {
  const value = JSON.parse(text);
  if (value === null || typeof value !== "object" || Array.isArray(value) ||
      !Array.isArray(value.nodes) || value.nodes.length < 1 || value.nodes.length > 4096 ||
      value.nodes.some((node) => node === null || typeof node !== "object" || Array.isArray(node) ||
        !Array.isArray(node.actions) || node.actions.length < 1 || node.actions.length > 64) ||
      !Array.isArray(value.endings) || value.endings.length < 1 || value.endings.length > 4096 ||
      value.endings.some((ending) => ending === null || typeof ending !== "object" || Array.isArray(ending) ||
        typeof ending.id !== "string")) throw new R12QualificationOperationalError();
  return value;
}

function sha256Text(value) {
  return `sha256:${createHash("sha256").update(new TextEncoder().encode(value)).digest("hex")}`;
}

function qualificationProfileEvidence(sceneBlueprintJson, runtimeGamePackJson) {
  try {
    const blueprint = JSON.parse(sceneBlueprintJson);
    const runtime = safeRuntimeShape(runtimeGamePackJson);
    if (blueprint === null || typeof blueprint !== "object" || Array.isArray(blueprint) ||
        !Array.isArray(blueprint.zones) || blueprint.zones.length < 2 || blueprint.zones.length > 4 ||
        !Array.isArray(blueprint.assetBriefs) || !Array.isArray(blueprint.placements)) return null;
    const environments = blueprint.assetBriefs.filter((brief) => brief?.kind === "environment");
    const props = blueprint.assetBriefs.filter((brief) => brief?.kind === "prop");
    const characters = blueprint.assetBriefs.filter((brief) => brief?.kind === "character-placeholder");
    const nonEnvironment = [...props, ...characters];
    const placedBriefIds = new Set(blueprint.placements.map((placement) => placement?.assetBriefId));
    const allBound = nonEnvironment.every((brief) =>
      typeof brief?.id === "string" && typeof brief?.entityId === "string" && placedBriefIds.has(brief.id));
    const actionCount = runtime.nodes.reduce((total, node) => total + node.actions.length, 0);
    if (environments.length !== 1 || props.length !== 3 || characters.length !== 3 ||
        nonEnvironment.length !== 6 || !allBound || runtime.nodes.length < 7 || runtime.nodes.length > 16 ||
        runtime.endings.length !== 3 || actionCount < 15 || actionCount > 64 * 16) return null;
    return Object.freeze({
      nodeCount: runtime.nodes.length,
      endingCount: runtime.endings.length,
      actionCount,
      zoneCount: blueprint.zones.length,
      propCount: props.length,
      characterPlaceholderCount: characters.length,
      placementCount: blueprint.placements.length,
    });
  } catch {
    return null;
  }
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
    let hasReachableDeadlock = false;

    while (queue.length > 0) {
      if (visited.size > MAX_STATES) return failure("R12_RUNTIME_STATE_LIMIT");
      const current = queue.shift();
      if (current.inspection.status === "ended") {
        if (!endingPaths.has(current.inspection.location.id)) endingPaths.set(current.inspection.location.id, current.path);
        continue;
      }
      const availableActions = current.inspection.actions.filter((action) => action.available === true);
      if (availableActions.length === 0) hasReachableDeadlock = true;
      for (const action of availableActions) {
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
        hasReachableDeadlock,
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

export async function analyzeR12QualificationCandidate(
  sceneBlueprintJson,
  runtimeGamePackJson,
  runtimeReceiptJson,
  analyze = analyzeRuntimeReachability,
) {
  try {
    const profileEvidence = qualificationProfileEvidence(sceneBlueprintJson, runtimeGamePackJson);
    if (profileEvidence === null) return failure("R12_CREATOR_PROFILE_MISMATCH");
    const reachability = await analyze(runtimeGamePackJson, runtimeReceiptJson);
    if (!reachability?.ok || reachability.evidence?.declaredEndingCount !== 3 ||
        reachability.evidence.reachableEndingCount !== 3 || reachability.evidence.allEndingsReachable !== true ||
        reachability.evidence.hasReachableLoop !== true || reachability.evidence.hasReachableDeadlock !== false) {
      return failure("R12_CREATOR_RUNTIME_INVALID");
    }
    return deepFreeze({ ok: true, evidence: {
      ...profileEvidence,
      reachableEndingCount: reachability.evidence.reachableEndingCount,
      hasReachableLoop: reachability.evidence.hasReachableLoop,
      hasReachableDeadlock: false,
    } });
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

export async function verifyR12CreatorPublishedQualification(parsed, dependencies = {}) {
  try {
    if (!exactRecord(parsed, ["promptFile", "profileFile", "prototypeRunRoot", "spatialRunRoot"])) {
      throw new R12QualificationOperationalError();
    }
    const readInputs = dependencies.readInputs ?? readR12CallInputs;
    const recoverSpatial = dependencies.recoverSpatial ?? recoverSpatialPrototypeRuns;
    const loadSpatial = dependencies.loadSpatial ?? loadVerifiedSpatialPrototypeRun;
    const analyze = dependencies.analyze ?? analyzeRuntimeReachability;
    const input = await readInputs(parsed);
    if (input?.profile !== R12_LAST_TRAIN_ACCEPTANCE_PROFILE || typeof input.prompt !== "string") {
      throw new R12QualificationOperationalError();
    }
    const cacheDependencies = {
      services: dependencies.services ?? CACHE_SERVICES,
      recoverPrototypeRuns: dependencies.recoverPrototypeRuns ?? recoverPrototypeRuns,
      assemblePrototypeScene: dependencies.assemblePrototypeScene ?? assemblePrototypeScene,
      assemblePrototypeSpatialScene: dependencies.assemblePrototypeSpatialScene ?? assemblePrototypeSpatialScene,
      canonicalizeJsonValue: dependencies.canonicalizeJsonValue ?? canonicalizeJsonValue,
    };
    const recovered = await recoverSpatial({
      runRoot: parsed.spatialRunRoot,
      prototypeRunRoot: parsed.prototypeRunRoot,
      temporaryRoot: TEMPORARY_ROOT,
      ...cacheDependencies,
    });
    if (typeof recovered?.currentRunId !== "string") return failure("R12_CREATOR_RESULT_MISSING");
    const loaded = await loadSpatial({
      runId: recovered.currentRunId,
      runRoot: parsed.spatialRunRoot,
      prototypeRunRoot: parsed.prototypeRunRoot,
      temporaryRoot: TEMPORARY_ROOT,
      ...cacheDependencies,
    });
    const evidence = loaded?.qualificationEvidence;
    if (!exactRecord(evidence, ["source", "sceneBlueprintJson", "runtimeGamePackJson", "runtimeReceiptJson"]) ||
        loaded.runId !== recovered.currentRunId || typeof loaded.promptSha256 !== "string" ||
        typeof loaded.model !== "string") return failure("R12_CREATOR_RESULT_INVALID");
    if (evidence.source !== "live-provider") return failure("R12_CREATOR_RESULT_NOT_LIVE");
    if (loaded.promptSha256 !== sha256Text(input.prompt)) return failure("R12_CREATOR_PROMPT_MISMATCH");
    if (loaded.model !== "gpt-5.6-luna") return failure("R12_CREATOR_MODEL_MISMATCH");
    const qualification = await analyzeR12QualificationCandidate(
      evidence.sceneBlueprintJson,
      evidence.runtimeGamePackJson,
      evidence.runtimeReceiptJson,
      analyze,
    );
    if (!qualification.ok) return qualification;
    return deepFreeze({
      ok: true,
      evidence: {
        runId: loaded.runId,
        promptSha256: loaded.promptSha256,
        model: loaded.model,
        source: evidence.source,
        ...qualification.evidence,
      },
    });
  } catch (error) {
    if (error instanceof R12QualificationOperationalError) throw error;
    throw new R12QualificationOperationalError();
  }
}

function allowedInputFile(candidate, exactRepositoryFile) {
  return candidate === exactRepositoryFile || path.dirname(candidate) === TEMPORARY_ROOT;
}

async function readStableText(candidate, maximum) {
  let handle;
  try {
    handle = await open(candidate, "r");
    const before = await handle.stat({ bigint: true });
    const linked = await lstat(candidate, { bigint: true });
    const resolved = path.resolve(await realpath(candidate));
    if (!before.isFile() || linked.isSymbolicLink() || before.dev !== linked.dev || before.ino !== linked.ino ||
        resolved !== candidate || before.size < 1n || before.size > BigInt(maximum)) throw new R12QualificationOperationalError();
    const bytes = new Uint8Array(Number(before.size)); let offset = 0;
    while (offset < bytes.length) {
      const result = await handle.read(bytes, offset, bytes.length - offset, offset);
      if (!result || result.bytesRead < 1) throw new R12QualificationOperationalError();
      offset += result.bytesRead;
    }
    const tail = await handle.read(new Uint8Array(1), 0, 1, bytes.length);
    const after = await handle.stat({ bigint: true });
    if (tail.bytesRead !== 0 || after.dev !== before.dev || after.ino !== before.ino ||
        after.size !== before.size || after.mtimeNs !== before.mtimeNs) throw new R12QualificationOperationalError();
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch (error) {
    if (error instanceof R12QualificationOperationalError) throw error;
    throw new R12QualificationOperationalError();
  } finally { if (handle) await handle.close().catch(() => {}); }
}

export function parseR12CallArguments(args) {
  if (!Array.isArray(args) || args.length !== 6) throw new R12QualificationOperationalError();
  const values = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const name = args[index]; const value = args[index + 1];
    if (!CALL_ARGUMENTS.includes(name) || Object.hasOwn(values, name) || typeof value !== "string" ||
        !path.isAbsolute(value) || value.includes("\0")) throw new R12QualificationOperationalError();
    values[name] = path.resolve(value);
  }
  const promptRepositoryFile = path.join(MODULE_ROOT, "docs", "R12_LAST_TRAIN_PROMPT.txt");
  const profileRepositoryFile = path.join(MODULE_ROOT, "docs", "R12_LAST_TRAIN_PROFILE.json");
  if (!allowedInputFile(values["--prompt-file"], promptRepositoryFile) ||
      !allowedInputFile(values["--profile"], profileRepositoryFile) ||
      path.dirname(values["--run-root"]) !== TEMPORARY_ROOT || values["--run-root"].endsWith("-spatial")) {
    throw new R12QualificationOperationalError();
  }
  return deepFreeze({
    promptFile: values["--prompt-file"], profileFile: values["--profile"],
    prototypeRunRoot: values["--run-root"], spatialRunRoot: `${values["--run-root"]}-spatial`,
  });
}

export async function readR12CallInputs(parsed) {
  try {
    const prompt = await readStableText(parsed.promptFile, 32_768);
    if (prompt.trim().length < 1) throw new R12QualificationOperationalError();
    const profileText = await readStableText(parsed.profileFile, 16_384);
    const profile = JSON.parse(profileText);
    if (canonicalizeJsonValue(profile) !== canonicalizeJsonValue(R12_LAST_TRAIN_ACCEPTANCE_PROFILE)) {
      throw new R12QualificationOperationalError();
    }
    return deepFreeze({ prompt, profile: R12_LAST_TRAIN_ACCEPTANCE_PROFILE });
  } catch (error) {
    if (error instanceof R12QualificationOperationalError) throw error;
    throw new R12QualificationOperationalError();
  }
}
