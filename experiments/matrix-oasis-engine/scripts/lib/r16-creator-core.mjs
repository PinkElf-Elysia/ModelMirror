import { createHash } from "node:crypto";
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
  unlink,
  writeFile,
} from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { assemblePrototypeScene } from "@matrix-oasis/prototype-assembler";
import {
  findVerifiedQualifiedCreatorRun,
  publishQualifiedCreatorRun,
  qualifyPrototypeForCreator,
  recoverQualifiedCreatorRuns,
} from "@matrix-oasis/prototype-creator-qualification";
import { assemblePrototypeSpatialScene } from "@matrix-oasis/prototype-spatial-assembler";
import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";
import { qualifyR14SpatialSolver } from "../qualify-r14-spatial-solver.mjs";
import { qualifyR15RuntimeEvidence } from "../qualify-r15-runtime-evidence.mjs";
import {
  buildCreatorQualificationReferences,
  createCreatorQualificationReferenceVerifier,
} from "./creator-qualification-cache-core.mjs";
import { recoverPrototypeRuns } from "./prototype-cache-core.mjs";
import {
  loadVerifiedRuntimeEvidenceRun,
  recoverRuntimeEvidenceRuns,
} from "./runtime-evidence-cache-core.mjs";
import {
  loadVerifiedR14SpatialPrototypeRun,
  loadVerifiedSolvedSpatialPrototypeRun,
} from "./solved-spatial-cache-core.mjs";

const SOURCE_RUN_ID = /^[0-9a-f]{64}-[0-9a-f]{64}$/u;
const SHA_256 = /^sha256:[0-9a-f]{64}$/u;
const moduleRoot = path.resolve(fileURLToPath(new URL("../..", import.meta.url)));
const defaultTemporaryRoot = path.resolve(path.parse(moduleRoot).root, "tmp");

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
  unlink,
  writeFile,
});

const defaultDependencies = Object.freeze({
  assemblePrototypeScene,
  assemblePrototypeSpatialScene,
  buildCreatorQualificationReferences,
  canonicalizeJsonValue,
  createCreatorQualificationReferenceVerifier,
  findVerifiedQualifiedCreatorRun,
  loadVerifiedR14SpatialPrototypeRun,
  loadVerifiedRuntimeEvidenceRun,
  loadVerifiedSolvedSpatialPrototypeRun,
  publishQualifiedCreatorRun,
  qualifyPrototypeForCreator,
  qualifyR14SpatialSolver,
  qualifyR15RuntimeEvidence,
  recoverPrototypeRuns,
  recoverQualifiedCreatorRuns,
  recoverRuntimeEvidenceRuns,
  services: defaultServices,
});

export class R16CreatorQualificationOperationalError extends Error {
  constructor(code = "R16_CREATOR_QUALIFICATION_INTERNAL_ERROR") {
    super(code);
    this.name = "R16CreatorQualificationOperationalError";
    this.code = code;
  }
}

function fail(code) {
  throw new R16CreatorQualificationOperationalError(code);
}

function diagnostic(code) {
  return Object.freeze({
    phase: "qualification",
    severity: "error",
    code,
    path: "",
    message: code,
  });
}

function failure(code) {
  return Object.freeze({ ok: false, diagnostics: Object.freeze([diagnostic(code)]) });
}

function digest(bytes) {
  return `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
}

function rawDigest(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function encode(text) {
  return new TextEncoder().encode(text);
}

function canonicalText(bytes, code = "R16_CREATOR_QUALIFICATION_REFERENCE_INVALID") {
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    if (canonicalizeJsonValue(JSON.parse(text)) !== text) fail(code);
    return text;
  } catch (error) {
    if (error instanceof R16CreatorQualificationOperationalError) throw error;
    fail(code);
  }
}

function exactDirectChildren(temporaryRoot, values) {
  return values.every((value) => path.isAbsolute(value) && path.dirname(value) === temporaryRoot) &&
    new Set(values).size === values.length;
}

export function parseR16CreatorQualificationArguments(args, temporaryRoot = defaultTemporaryRoot) {
  if (!Array.isArray(args) || ![10, 12].includes(args.length)) {
    fail("R16_CREATOR_QUALIFICATION_ARGUMENT_INVALID");
  }
  const names = Object.freeze({
    "--prototype-run-root": "prototypeRunRoot",
    "--spatial-run-root": "spatialRunRoot",
    "--solved-run-root": "solvedRunRoot",
    "--evidence-run-root": "evidenceRunRoot",
    "--qualified-run-root": "qualifiedRunRoot",
    "--source-run-id": "sourceRunId",
  });
  const values = Object.create(null);
  for (let index = 0; index < args.length; index += 2) {
    const key = names[args[index]];
    const value = args[index + 1];
    if (!key || Object.hasOwn(values, key) || typeof value !== "string" ||
        value.length === 0 || value.includes("\0")) {
      fail("R16_CREATOR_QUALIFICATION_ARGUMENT_INVALID");
    }
    values[key] = key === "sourceRunId" ? value : path.resolve(value);
  }
  const root = path.resolve(temporaryRoot);
  const roots = [values.prototypeRunRoot, values.spatialRunRoot, values.solvedRunRoot,
    values.evidenceRunRoot, values.qualifiedRunRoot];
  if (roots.some((value) => typeof value !== "string") || !exactDirectChildren(root, roots) ||
      (values.sourceRunId !== undefined && !SOURCE_RUN_ID.test(values.sourceRunId))) {
    fail("R16_CREATOR_QUALIFICATION_ARGUMENT_INVALID");
  }
  return Object.freeze({ ...values, temporaryRoot: root });
}

function sourceCacheOptions(options, dependencies) {
  return Object.freeze({
    runRoot: options.prototypeRunRoot,
    temporaryRoot: options.temporaryRoot,
    services: dependencies.services,
    assemblePrototypeScene: dependencies.assemblePrototypeScene,
    canonicalizeJsonValue: dependencies.canonicalizeJsonValue,
  });
}

function spatialSourceOptions(options, dependencies) {
  return Object.freeze({
    loadVerifiedSpatialPrototypeRun: dependencies.loadVerifiedR14SpatialPrototypeRun,
    cacheOptions: Object.freeze({
      runRoot: options.spatialRunRoot,
      prototypeRunRoot: options.prototypeRunRoot,
      temporaryRoot: options.temporaryRoot,
      services: dependencies.services,
      recoverPrototypeRuns: dependencies.recoverPrototypeRuns,
      assemblePrototypeScene: dependencies.assemblePrototypeScene,
      assemblePrototypeSpatialScene: dependencies.assemblePrototypeSpatialScene,
      canonicalizeJsonValue: dependencies.canonicalizeJsonValue,
    }),
  });
}

async function recoverSources(options, dependencies) {
  const recovered = await dependencies.recoverPrototypeRuns(sourceCacheOptions(options, dependencies));
  if (!recovered || !Array.isArray(recovered.runs)) fail("R16_CREATOR_QUALIFICATION_SOURCE_INVALID");
  return recovered;
}

export async function recoverR16CreatorSourceRuns(options, overrides = {}) {
  const dependencies = Object.freeze({ ...defaultDependencies, ...overrides });
  if (!options || typeof options.prototypeRunRoot !== "string" || typeof options.temporaryRoot !== "string" ||
      path.dirname(path.resolve(options.prototypeRunRoot)) !== path.resolve(options.temporaryRoot)) {
    fail("R16_CREATOR_QUALIFICATION_SOURCE_INVALID");
  }
  return await recoverSources(options, dependencies);
}

async function sourceByRunId(options, dependencies, runId) {
  if (!SOURCE_RUN_ID.test(runId)) return null;
  const recovered = await recoverSources(options, dependencies);
  return recovered.runs.find((item) => item.runId === runId) ?? null;
}

function qualificationRootRequest(options, verifyReferences) {
  return Object.freeze({
    qualifiedRunRoot: options.qualifiedRunRoot,
    temporaryRoot: options.temporaryRoot,
    verifyReferences,
  });
}

function evidencePayload(loaded) {
  try {
    const evidence = JSON.parse(loaded.canonicalEvidenceJson);
    const replayPlan = JSON.parse(loaded.replayPlanJson);
    if (evidence.status !== "passed" || evidence.replayPlanSha256 !== digest(encode(loaded.replayPlanJson)) ||
        canonicalizeJsonValue(evidence.identity) !== canonicalizeJsonValue(replayPlan.identity) ||
        !SHA_256.test(evidence.identity?.spatialSolutionSha256)) {
      fail("R16_CREATOR_QUALIFICATION_EVIDENCE_INVALID");
    }
    return Object.freeze({
      runId: loaded.runId,
      replayPlanJson: loaded.replayPlanJson,
      canonicalEvidenceJson: loaded.canonicalEvidenceJson,
      evidence,
      replayPlan,
      previewFiles: loaded.previewFiles,
      mediaFiles: loaded.mediaFiles,
      solutionSha256: evidence.identity.spatialSolutionSha256,
      attempt: evidence.attempt,
    });
  } catch (error) {
    if (error instanceof R16CreatorQualificationOperationalError) throw error;
    fail("R16_CREATOR_QUALIFICATION_EVIDENCE_INVALID");
  }
}

function solvedPayload(loaded) {
  return Object.freeze({
    runId: loaded.runId,
    promptSha256: loaded.promptSha256,
    model: loaded.model,
    solutionSha256: loaded.solutionSha256,
    previewFiles: loaded.previewFiles,
  });
}

function finalArtifactsFromEvidence(evidence) {
  const solutionBytes = evidence.previewFiles?.get("spatial-solution.json");
  const verificationBytes = evidence.previewFiles?.get("spatial-verification-report.json");
  if (!(solutionBytes instanceof Uint8Array) || !(verificationBytes instanceof Uint8Array) ||
      digest(solutionBytes) !== evidence.solutionSha256 ||
      digest(verificationBytes) !== evidence.evidence.identity.spatialVerificationSha256) {
    fail("R16_CREATOR_QUALIFICATION_EVIDENCE_INVALID");
  }
  return Object.freeze({
    solved: Object.freeze({
      solutionSha256: evidence.solutionSha256,
      spatialSolutionJson: canonicalText(solutionBytes),
    }),
    verification: Object.freeze({
      solutionSha256: evidence.solutionSha256,
      spatialVerificationSha256: evidence.evidence.identity.spatialVerificationSha256,
      spatialVerificationReportJson: canonicalText(verificationBytes),
    }),
  });
}

async function loadEvidence(options, dependencies, runId) {
  const loaded = await dependencies.loadVerifiedRuntimeEvidenceRun({
    runRoot: options.evidenceRunRoot,
    temporaryRoot: options.temporaryRoot,
    runId,
    includeFiles: true,
  }, dependencies.services);
  return evidencePayload(loaded);
}

async function sourcePreview(options, dependencies, source) {
  const loaded = await dependencies.loadVerifiedR14SpatialPrototypeRun({
    runId: source.runId,
    ...spatialSourceOptions(options, dependencies).cacheOptions,
  });
  if (!loaded || !(loaded.previewFiles instanceof Map)) {
    fail("R16_CREATOR_QUALIFICATION_SOURCE_INVALID");
  }
  return loaded.previewFiles;
}

async function readStableAssetBundle(options, dependencies, runId) {
  const candidate = path.resolve(options.prototypeRunRoot, "runs", runId, "prototype-asset-bundle.json");
  const expectedParent = path.resolve(options.prototypeRunRoot, "runs", runId);
  if (path.dirname(candidate) !== expectedParent) fail("R16_CREATOR_QUALIFICATION_SOURCE_INVALID");
  let handle;
  try {
    handle = await dependencies.services.openFile(candidate, "r");
    const opened = await handle.stat({ bigint: true });
    const linked = await dependencies.services.lstat(candidate, { bigint: true });
    const resolved = path.resolve(await dependencies.services.realpath(candidate));
    if (!opened.isFile() || linked.isSymbolicLink() || resolved !== candidate ||
        opened.dev !== linked.dev || opened.ino !== linked.ino || opened.size !== linked.size ||
        opened.mtimeNs !== linked.mtimeNs || opened.ctimeNs !== linked.ctimeNs ||
        opened.size < 1n || opened.size > 262_144n) {
      fail("R16_CREATOR_QUALIFICATION_SOURCE_INVALID");
    }
    const bytes = new Uint8Array(await handle.readFile());
    const after = await handle.stat({ bigint: true });
    const linkedAfter = await dependencies.services.lstat(candidate, { bigint: true });
    const resolvedAfter = path.resolve(await dependencies.services.realpath(candidate));
    if (after.dev !== opened.dev || after.ino !== opened.ino || after.size !== opened.size ||
        after.mtimeNs !== opened.mtimeNs || after.ctimeNs !== opened.ctimeNs ||
        linkedAfter.isSymbolicLink() || resolvedAfter !== candidate ||
        linkedAfter.dev !== opened.dev || linkedAfter.ino !== opened.ino ||
        linkedAfter.size !== opened.size || linkedAfter.mtimeNs !== opened.mtimeNs ||
        linkedAfter.ctimeNs !== opened.ctimeNs || bytes.byteLength !== Number(opened.size)) {
      fail("R16_CREATOR_QUALIFICATION_SOURCE_INVALID");
    }
    canonicalText(bytes, "R16_CREATOR_QUALIFICATION_SOURCE_INVALID");
    return bytes;
  } catch (error) {
    if (error instanceof R16CreatorQualificationOperationalError) throw error;
    fail("R16_CREATOR_QUALIFICATION_SOURCE_INVALID");
  } finally {
    await handle?.close().catch(() => {});
  }
}

function createReferenceSourceLoader(options, dependencies) {
  return async ({ sourceRunId }) => {
    const source = await sourceByRunId(options, dependencies, sourceRunId);
    if (!source) return null;
    const previewFiles = new Map(await sourcePreview(options, dependencies, source));
    previewFiles.set("prototype-asset-bundle.json",
      await readStableAssetBundle(options, dependencies, sourceRunId));
    return Object.freeze({ ...source, previewFiles });
  };
}

export function createR16QualificationReferenceVerifier(options, dependencies = defaultDependencies) {
  return dependencies.createCreatorQualificationReferenceVerifier({
    evidenceRunRoot: options.evidenceRunRoot,
    sourceRunRoot: options.prototypeRunRoot,
    temporaryRoot: options.temporaryRoot,
    loadSource: createReferenceSourceLoader(options, dependencies),
  }, {
    loadEvidence: (request) => dependencies.loadVerifiedRuntimeEvidenceRun(
      request,
      dependencies.services,
    ),
  });
}

async function buildStrongQualificationReference(options, dependencies, sourceRunId, evidenceRunId) {
  return await dependencies.buildCreatorQualificationReferences({
    sourceRunId,
    evidenceRunId,
    evidenceRunRoot: options.evidenceRunRoot,
    sourceRunRoot: options.prototypeRunRoot,
    temporaryRoot: options.temporaryRoot,
  }, {
    loadEvidence: (request) => dependencies.loadVerifiedRuntimeEvidenceRun(
      request,
      dependencies.services,
    ),
    loadSource: createReferenceSourceLoader(options, dependencies),
  });
}

async function selectSource(options, dependencies, verifyReferences) {
  const recovered = await recoverSources(options, dependencies);
  if (options.sourceRunId !== undefined) {
    if (!SOURCE_RUN_ID.test(options.sourceRunId)) fail("R16_CREATOR_QUALIFICATION_SOURCE_INVALID");
    const explicit = recovered.runs.find((run) => run.runId === options.sourceRunId);
    if (!explicit) fail("R16_CREATOR_QUALIFICATION_SOURCE_INVALID");
    return explicit;
  }
  if (recovered.currentRunId) {
    const current = recovered.runs.find((run) => run.runId === recovered.currentRunId);
    if (current) return current;
  }
  if (recovered.runs.length === 1) return recovered.runs[0];
  if (recovered.runs.length > 1) {
    const qualified = await dependencies.recoverQualifiedCreatorRuns(
      qualificationRootRequest(options, verifyReferences),
    );
    const current = qualified.runs.find((run) =>
      run.qualificationRunId === qualified.currentQualificationRunId);
    if (current) {
      const source = recovered.runs.find((run) => run.runId === current.qualification.sourceRunId);
      if (source) return source;
    }
  }
  fail(recovered.runs.length === 0
    ? "R16_CREATOR_QUALIFICATION_SOURCE_MISSING"
    : "R16_CREATOR_QUALIFICATION_SOURCE_AMBIGUOUS");
}

async function loadSolved(options, dependencies, source) {
  try {
    const loaded = await dependencies.loadVerifiedSolvedSpatialPrototypeRun({
      runId: source.runId,
      runRoot: options.solvedRunRoot,
      temporaryRoot: options.temporaryRoot,
      sourceOptions: spatialSourceOptions(options, dependencies),
      services: dependencies.services,
      canonicalizeJsonValue: dependencies.canonicalizeJsonValue,
    });
    return solvedPayload(loaded);
  } catch {
    return null;
  }
}

async function selectEvidence(options, dependencies, source) {
  let recovered;
  try {
    recovered = await dependencies.recoverRuntimeEvidenceRuns({
      runRoot: options.evidenceRunRoot,
      temporaryRoot: options.temporaryRoot,
    }, dependencies.services);
  } catch {
    return null;
  }
  const matches = [];
  for (const summary of recovered.runs) {
    try {
      const candidate = await loadEvidence(options, dependencies, summary.runId);
      if (options.expectedSolutionSha256 && candidate.solutionSha256 !== options.expectedSolutionSha256) continue;
      const built = await buildStrongQualificationReference(
        options,
        dependencies,
        source.runId,
        candidate.runId,
      );
      if (built?.ok && built.qualification.hashes.spatialSolutionSha256 === candidate.solutionSha256) {
        matches.push(candidate);
      }
    } catch {
      // Invalid evidence is ineligible, never a partial qualification.
    }
  }
  const current = matches.find((item) => item.runId === recovered.currentRunId);
  if (current) return current;
  if (matches.length === 1) return matches[0];
  if (matches.length > 1) fail("R16_CREATOR_QUALIFICATION_EVIDENCE_AMBIGUOUS");
  return null;
}

export async function discoverR16CreatorQualificationCache(options, dependencies = defaultDependencies) {
  const verifyReferences = createR16QualificationReferenceVerifier(options, dependencies);
  const source = await selectSource(options, dependencies, verifyReferences);
  const qualified = await dependencies.findVerifiedQualifiedCreatorRun({
    ...qualificationRootRequest(options, verifyReferences),
    promptSha256: source.promptSha256,
    model: source.model,
  });
  if (qualified && qualified.qualification.sourceRunId === source.runId &&
      (!options.expectedSolutionSha256 ||
      qualified.qualification.hashes.spatialSolutionSha256 === options.expectedSolutionSha256)) {
    return Object.freeze({
      cacheLevel: "qualified",
      source,
      qualified: qualified.qualification,
      qualificationRunId: qualified.qualificationRunId,
      expectedSolutionSha256: options.expectedSolutionSha256,
      verifyReferences,
    });
  }

  const evidence = await selectEvidence(options, dependencies, source);
  if (evidence) {
    const final = finalArtifactsFromEvidence(evidence);
    return Object.freeze({
      cacheLevel: "evidence-only",
      source,
      evidence,
      ...final,
      expectedSolutionSha256: options.expectedSolutionSha256,
      verifyReferences,
    });
  }

  const solved = await loadSolved(options, dependencies, source);
  if (solved) {
    return Object.freeze({
      cacheLevel: "solved-only",
      source,
      solved,
      verification: Object.freeze({ solutionSha256: solved.solutionSha256 }),
      expectedSolutionSha256: options.expectedSolutionSha256,
      verifyReferences,
    });
  }
  return Object.freeze({
    cacheLevel: "source-only",
    source,
    expectedSolutionSha256: options.expectedSolutionSha256,
    verifyReferences,
  });
}

function operationFailure(code) {
  return Object.freeze({ ok: false, diagnostics: Object.freeze([diagnostic(code)]) });
}

export function createR16CreatorQualificationOperations(options, discovered, dependencies = defaultDependencies) {
  const loadCurrentSolved = async () => {
    const loaded = await loadSolved(options, dependencies, discovered.source);
    if (!loaded) fail("R16_CREATOR_QUALIFICATION_SOLUTION_INVALID");
    return loaded;
  };
  const verifyEvidence = async (candidate, expected) => {
    const loaded = await loadEvidence(options, dependencies, candidate.runId);
    if (loaded.canonicalEvidenceJson !== candidate.canonicalEvidenceJson ||
        loaded.replayPlanJson !== candidate.replayPlanJson || loaded.solutionSha256 !== expected) return null;
    const built = await buildStrongQualificationReference(
      options,
      dependencies,
      discovered.source.runId,
      candidate.runId,
    );
    if (!built?.ok || built.qualification.hashes.spatialSolutionSha256 !== expected) return null;
    return loaded;
  };

  return Object.freeze({
    analyze: async () => Object.freeze({ ok: true, analysis: Object.freeze({ sourceRunId: discovered.source.runId }) }),
    solve: async () => {
      const result = await dependencies.qualifyR14SpatialSolver({
        ...options,
        runId: discovered.source.runId,
      });
      if (!result?.ok) return result ?? operationFailure("R16_CREATOR_QUALIFICATION_SOLVE_FAILED");
      const solved = await loadCurrentSolved();
      return Object.freeze({ ok: true, solved });
    },
    verify: async ({ solved, expectedSolutionSha256 }) => {
      const loaded = await loadCurrentSolved();
      return loaded.solutionSha256 === solved.solutionSha256 && loaded.solutionSha256 === expectedSolutionSha256
        ? Object.freeze({ ok: true, verification: Object.freeze({ solutionSha256: loaded.solutionSha256 }) })
        : operationFailure("R16_CREATOR_QUALIFICATION_SOLUTION_INVALID");
    },
    verifySolved: async ({ solved, expectedSolutionSha256 }) => {
      const loaded = await loadCurrentSolved();
      return loaded.solutionSha256 === solved.solutionSha256 && loaded.solutionSha256 === expectedSolutionSha256
        ? Object.freeze({ ok: true })
        : operationFailure("R16_CREATOR_QUALIFICATION_SOLUTION_INVALID");
    },
    verifyEvidence: async ({ evidence, expectedSolutionSha256 }) =>
      (await verifyEvidence(evidence, expectedSolutionSha256))
        ? Object.freeze({ ok: true })
        : operationFailure("R16_CREATOR_QUALIFICATION_EVIDENCE_INVALID"),
    verifyQualified: async ({ qualified, expectedSolutionSha256 }) => {
      if (qualified.hashes?.spatialSolutionSha256 !== expectedSolutionSha256) {
        return operationFailure("R16_CREATOR_QUALIFICATION_SOLUTION_IDENTITY_MISMATCH");
      }
      const text = dependencies.canonicalizeJsonValue(qualified);
      const valid = await discovered.verifyReferences({
        qualification: qualified,
        qualificationJson: text,
        qualificationRunId: rawDigest(encode(text)),
      });
      return valid ? Object.freeze({ ok: true }) : operationFailure("R16_CREATOR_QUALIFICATION_REFERENCE_INVALID");
    },
    collectEvidence: async ({ initialSolutionSha256, expectedSolutionSha256, onAttempt }) => {
      const result = await dependencies.qualifyR15RuntimeEvidence({
        ...options,
        runId: discovered.source.runId,
      });
      if (!result?.ok || !result.published?.runId) {
        return result ?? operationFailure("R16_CREATOR_QUALIFICATION_EVIDENCE_FAILED");
      }
      if (Number.isInteger(result.attempt)) {
        for (let attempt = 1; attempt <= result.attempt; attempt += 1) {
          await onAttempt(attempt);
        }
      }
      const evidence = await loadEvidence(options, dependencies, result.published.runId);
      if (expectedSolutionSha256 && evidence.solutionSha256 !== expectedSolutionSha256) {
        return operationFailure("R16_CREATOR_QUALIFICATION_SOLUTION_IDENTITY_MISMATCH");
      }
      const final = finalArtifactsFromEvidence(evidence);
      if (!expectedSolutionSha256 && initialSolutionSha256 !== evidence.solutionSha256) {
        // R15 may select and fully re-evidence a different bounded candidate.
      }
      return Object.freeze({
        ok: true,
        evidence,
        attempt: evidence.attempt,
        finalSolutionSha256: evidence.solutionSha256,
        solved: final.solved,
        verification: final.verification,
        source: discovered.source,
      });
    },
    publishQualification: async ({ source, evidence, expectedSolutionSha256 }) => {
      if (!source || !evidence || evidence.solutionSha256 !== expectedSolutionSha256) {
        return operationFailure("R16_CREATOR_QUALIFICATION_PUBLISH_FAILED");
      }
      const built = await buildStrongQualificationReference(
        options,
        dependencies,
        source.runId,
        evidence.runId,
      );
      if (!built?.ok || built.qualification.hashes.spatialSolutionSha256 !== expectedSolutionSha256) {
        return operationFailure("R16_CREATOR_QUALIFICATION_REFERENCE_INVALID");
      }
      await dependencies.publishQualifiedCreatorRun({
        ...qualificationRootRequest(options, discovered.verifyReferences),
        canonicalQualificationJson: built.canonicalQualificationJson,
      });
      return Object.freeze({ ok: true, qualification: built.qualification });
    },
  });
}

export async function qualifyR16Creator(options, overrides = {}) {
  const dependencies = Object.freeze({ ...defaultDependencies, ...(overrides.dependencies ?? {}) });
  if (!options || typeof options.godotBin !== "string" || options.godotBin.length === 0 ||
      options.godotVersion !== "4.6.3" ||
      !exactDirectChildren(path.resolve(options.temporaryRoot), [
        options.prototypeRunRoot,
        options.spatialRunRoot,
        options.solvedRunRoot,
        options.evidenceRunRoot,
        options.qualifiedRunRoot,
      ].map((value) => path.resolve(value)))) {
    return failure("R16_CREATOR_QUALIFICATION_INPUT_INVALID");
  }
  try {
    const discovered = await (overrides.discoverCache ?? discoverR16CreatorQualificationCache)(
      options,
      dependencies,
    );
    const operations = overrides.operations ??
      createR16CreatorQualificationOperations(options, discovered, dependencies);
    return await dependencies.qualifyPrototypeForCreator({
      ...discovered,
      expectedSolutionSha256: options.expectedSolutionSha256 ?? discovered.expectedSolutionSha256,
      onStage: options.onStage,
    }, operations);
  } catch (error) {
    if (error instanceof R16CreatorQualificationOperationalError) return failure(error.code);
    return failure("R16_CREATOR_QUALIFICATION_INTERNAL_ERROR");
  }
}
