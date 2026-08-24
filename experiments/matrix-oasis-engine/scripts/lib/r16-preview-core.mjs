import { createHash } from "node:crypto";
import path from "node:path";
import {
  loadVerifiedQualifiedCreatorRun,
  recoverQualifiedCreatorRuns,
} from "@matrix-oasis/prototype-creator-qualification";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import {
  createR16QualificationReferenceVerifier,
  discoverR16CreatorQualificationCache,
  qualifyR16Creator,
  recoverR16CreatorSourceRuns,
} from "./r16-creator-core.mjs";
import {
  launchR15EvidencePreview,
  selectR15EvidenceRun,
} from "./r15-preview-core.mjs";

export const R16_PREVIEW_READY_MARKER = "MATRIX_OASIS_R16_CREATOR_MVP_READY";
const SOURCE_RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;
const QUALIFICATION_RUN_ID = /^[0-9a-f]{64}$/u;
const SHA_256 = /^sha256:[0-9a-f]{64}$/u;

function exactFunctions(value, names) {
  return value !== null && typeof value === "object" && !Array.isArray(value) &&
    Object.getPrototypeOf(value) === Object.prototype && Object.keys(value).length === names.length &&
    names.every((name) => typeof value[name] === "function");
}

function fail(code) {
  throw new Error(code);
}

function qualificationRunId(qualification) {
  return createHash("sha256").update(canonicalizeJsonValue(qualification), "utf8").digest("hex");
}

function rootsFromOptions(options) {
  return Object.freeze({
    prototypeRunRoot: options.prototypeRunRoot,
    spatialRunRoot: options.spatialRunRoot,
    solvedRunRoot: options.solvedRunRoot,
    evidenceRunRoot: options.evidenceRunRoot,
    qualifiedRunRoot: options.qualifiedRunRoot,
    temporaryRoot: options.temporaryRoot,
    godotBin: options.godot.command,
    godotVersion: options.godot.version,
  });
}

function publicCache(discovered) {
  if (discovered.cacheLevel === "qualified") {
    return Object.freeze({ ok: true, cacheLevel: "qualified",
      qualificationRunId: discovered.qualificationRunId, qualification: discovered.qualified });
  }
  return Object.freeze({ ok: true, cacheLevel: discovered.cacheLevel,
    sourceRunId: discovered.source.runId,
    expectedSolutionSha256: discovered.expectedSolutionSha256 ?? null });
}

export function parseR16PreviewArguments(args, temporaryRoot) {
  if (!Array.isArray(args) || ![2, 4].includes(args.length) || args[0] !== "--run-root" ||
      typeof args[1] !== "string" || !path.isAbsolute(args[1]) || args[1].includes("\0")) {
    fail("R16_PREVIEW_ARGUMENT_INVALID");
  }
  const prototypeRunRoot = path.resolve(args[1]);
  const root = path.resolve(temporaryRoot);
  if (path.dirname(prototypeRunRoot) !== root || /-(?:spatial|solved|evidence|qualified)$/u.test(prototypeRunRoot)) {
    fail("R16_PREVIEW_ARGUMENT_INVALID");
  }
  let port;
  if (args.length === 4) {
    if (args[2] !== "--port" || !/^[0-9]{4,5}$/u.test(args[3])) fail("R16_PREVIEW_ARGUMENT_INVALID");
    port = Number(args[3]);
    if (!Number.isSafeInteger(port) || port < 1024 || port > 65535) fail("R16_PREVIEW_ARGUMENT_INVALID");
  }
  return Object.freeze({
    prototypeRunRoot,
    spatialRunRoot: `${prototypeRunRoot}-spatial`,
    solvedRunRoot: `${prototypeRunRoot}-solved`,
    evidenceRunRoot: `${prototypeRunRoot}-evidence`,
    qualifiedRunRoot: `${prototypeRunRoot}-qualified`,
    temporaryRoot: root,
    ...(port === undefined ? {} : { port }),
  });
}

export async function selectR16QualifiedEvidence({ qualifiedRunRoot, temporaryRoot, qualificationRunId = null }, dependencies = {}) {
  const root = path.resolve(temporaryRoot);
  const qualified = path.resolve(qualifiedRunRoot);
  if (path.dirname(qualified) !== root || !qualified.endsWith("-qualified")) fail("R16_PREVIEW_ARGUMENT_INVALID");
  const prototypeRunRoot = qualified.slice(0, -"-qualified".length);
  const roots = Object.freeze({ prototypeRunRoot, spatialRunRoot: `${prototypeRunRoot}-spatial`,
    solvedRunRoot: `${prototypeRunRoot}-solved`, evidenceRunRoot: `${prototypeRunRoot}-evidence`,
    qualifiedRunRoot: qualified, temporaryRoot: root });
  const createVerifier = dependencies.createR16QualificationReferenceVerifier ?? createR16QualificationReferenceVerifier;
  const recoverQualified = dependencies.recoverQualifiedCreatorRuns ?? recoverQualifiedCreatorRuns;
  const selectEvidence = dependencies.selectR15EvidenceRun ?? selectR15EvidenceRun;
  const recovered = await recoverQualified({ qualifiedRunRoot: qualified, temporaryRoot: root,
    verifyReferences: createVerifier(roots) });
  const selectedId = qualificationRunId ?? recovered.currentQualificationRunId ??
    (recovered.runs.length === 1 ? recovered.runs[0].qualificationRunId : null);
  const selectedQualification = recovered.runs.find((run) => run.qualificationRunId === selectedId);
  if (!selectedQualification || !QUALIFICATION_RUN_ID.test(selectedId ?? "")) fail("R16_PREVIEW_CACHE_INVALID");
  const evidence = await selectEvidence({ evidenceRunRoot: roots.evidenceRunRoot, temporaryRoot: root,
    runId: selectedQualification.qualification.evidence.runId });
  if (evidence.runId !== selectedQualification.qualification.evidence.runId ||
      evidence.solutionSha256 !== selectedQualification.qualification.hashes.spatialSolutionSha256) {
    fail("R16_PREVIEW_REFERENCE_INVALID");
  }
  return Object.freeze({ qualificationRunId: selectedId, qualification: selectedQualification.qualification, evidence });
}

export function createR16PreviewOperations(options, dependencies = {}) {
  const baseNames = ["findCache", "generate", "describeAssets", "acquire", "publish", "launch", "recover",
    "stopLaunch", "persistPending", "recoverPending", "discardPending"];
  if (!options || !exactFunctions(options.r12Operations, baseNames) ||
      options.godot?.version !== "4.6.3" || typeof options.godot.command !== "string" ||
      typeof options.moduleRoot !== "string") fail("R16_PREVIEW_INPUT_INVALID");
  const discover = dependencies.discoverR16CreatorQualificationCache ?? discoverR16CreatorQualificationCache;
  const qualify = dependencies.qualifyR16Creator ?? qualifyR16Creator;
  const createVerifier = dependencies.createR16QualificationReferenceVerifier ?? createR16QualificationReferenceVerifier;
  const recoverQualified = dependencies.recoverQualifiedCreatorRuns ?? recoverQualifiedCreatorRuns;
  const recoverSources = dependencies.recoverR16CreatorSourceRuns ?? recoverR16CreatorSourceRuns;
  const loadQualified = dependencies.loadVerifiedQualifiedCreatorRun ?? loadVerifiedQualifiedCreatorRun;
  const selectEvidence = dependencies.selectR15EvidenceRun ?? selectR15EvidenceRun;
  const launchEvidence = dependencies.launchR15EvidencePreview ?? launchR15EvidencePreview;
  const roots = rootsFromOptions(options);
  let launched = null;

  const discoverSource = async (sourceRunId, expectedSolutionSha256 = undefined) => {
    if (!SOURCE_RUN_ID.test(sourceRunId) ||
        (expectedSolutionSha256 !== undefined && expectedSolutionSha256 !== null && !SHA_256.test(expectedSolutionSha256))) {
      fail("R16_PREVIEW_CACHE_INVALID");
    }
    return await discover({ ...roots, sourceRunId,
      ...(expectedSolutionSha256 ? { expectedSolutionSha256 } : {}) });
  };
  const verifier = createVerifier(roots);

  const stopLaunch = async () => {
    const current = launched;
    launched = null;
    if (current) await current.cleanup();
  };

  return Object.freeze({
    async findCache(input) {
      const recovered = await recoverSources(roots);
      const matches = recovered.runs.filter((source) =>
        source.promptSha256 === input.promptSha256 && source.model === input.model);
      const direct = matches.find((source) => source.runId === recovered.currentRunId) ??
        (matches.length === 1 ? matches[0] : null);
      if (matches.length > 1 && direct === null) fail("R16_PREVIEW_CACHE_AMBIGUOUS");
      if (direct) return publicCache(await discoverSource(direct.runId));
      const source = await options.r12Operations.findCache(input);
      if (source?.ok !== true || !SOURCE_RUN_ID.test(source.runId ?? "")) return Object.freeze({ ok: false });
      return publicCache(await discoverSource(source.runId));
    },
    async generate(input) { return options.r12Operations.generate(input); },
    async describeAssets(input) { return options.r12Operations.describeAssets(input); },
    async acquire(input) { return options.r12Operations.acquire(input); },
    async publish(input) { return options.r12Operations.publish(input); },
    async persistPending(input) { return options.r12Operations.persistPending(input); },
    async recoverPending() { return options.r12Operations.recoverPending(); },
    async discardPending(input) { return options.r12Operations.discardPending(input); },
    async qualify({ sourceRunId, expectedSolutionSha256, onStage }) {
      await stopLaunch();
      const result = await qualify({ ...roots, sourceRunId,
        ...(expectedSolutionSha256 ? { expectedSolutionSha256 } : {}), onStage });
      if (result?.ok !== true) return result;
      const runId = qualificationRunId(result.qualification);
      return Object.freeze({ ...result, qualificationRunId: runId });
    },
    async recover() {
      const sourceRecovery = await recoverSources(roots);
      const sources = Array.isArray(sourceRecovery?.runs) ? sourceRecovery.runs : [];
      const qualified = await recoverQualified({ qualifiedRunRoot: roots.qualifiedRunRoot,
        temporaryRoot: roots.temporaryRoot, verifyReferences: verifier });
      const knownSources = new Set(sources.map((source) => source.runId));
      const runs = qualified.runs.filter((run) => knownSources.has(run.qualification.sourceRunId)).map((run) => Object.freeze({
        promptSha256: run.qualification.promptSha256,
        model: run.qualification.model,
        cache: Object.freeze({ ok: true, cacheLevel: "qualified", qualificationRunId: run.qualificationRunId,
          qualification: run.qualification }),
      }));
      const qualifiedSources = new Set(runs.map((run) => run.cache.qualification.sourceRunId));
      const partialCandidates = sources.filter((source) => !qualifiedSources.has(source.runId));
      const selectedPartial = partialCandidates.find((source) => source.runId === sourceRecovery.currentRunId) ??
        (partialCandidates.length === 1 ? partialCandidates[0] : null);
      if (selectedPartial) {
        const discovered = await discoverSource(selectedPartial.runId);
        if (discovered.cacheLevel !== "qualified") runs.push(Object.freeze({
          promptSha256: selectedPartial.promptSha256,
          model: selectedPartial.model,
          cache: publicCache(discovered),
        }));
      }
      return Object.freeze({ currentRunId: qualified.currentQualificationRunId,
        runs: Object.freeze(runs) });
    },
    async launch({ runId }) {
      if (!QUALIFICATION_RUN_ID.test(runId ?? "")) return Object.freeze({ ok: false });
      await stopLaunch();
      const loaded = await loadQualified({ qualifiedRunRoot: roots.qualifiedRunRoot,
        temporaryRoot: roots.temporaryRoot, qualificationRunId: runId, verifyReferences: verifier });
      const selected = await selectEvidence({ evidenceRunRoot: roots.evidenceRunRoot,
        temporaryRoot: roots.temporaryRoot, runId: loaded.qualification.evidence.runId });
      if (selected.runId !== loaded.qualification.evidence.runId ||
          selected.solutionSha256 !== loaded.qualification.hashes.spatialSolutionSha256) {
        fail("R16_PREVIEW_REFERENCE_INVALID");
      }
      launched = await launchEvidence({ selected, godot: options.godot, moduleRoot: options.moduleRoot });
      return Object.freeze({ ok: true });
    },
    async stopLaunch() { await stopLaunch(); },
  });
}
