import { canonicalizeJsonValue } from "@matrix-oasis/runtime-pack-contracts";

const SHA_256 = /^sha256:[0-9a-f]{64}$/u;
const CACHE_LEVELS = new Set([
  "qualified",
  "evidence-only",
  "solved-only",
  "source-only",
]);
const SUBPHASES = new Set([
  "analyzing",
  "solving",
  "verifying",
  "evidencing",
]);

function deepFreeze(value) {
  if (value && typeof value === "object" && !Object.isFrozen(value)) {
    for (const child of Object.values(value)) deepFreeze(child);
    Object.freeze(value);
  }
  return value;
}

function clone(value) {
  return value === undefined ? undefined : structuredClone(value);
}

function frozenOperationRequest(value, callbacks = {}) {
  return deepFreeze({ ...clone(value), ...callbacks });
}

function diagnostic(code) {
  return deepFreeze({
    phase: "qualification",
    severity: "error",
    code,
    path: "",
    message: code,
  });
}

function failure(code) {
  return deepFreeze({ ok: false, diagnostics: [diagnostic(code)] });
}

function success(cacheLevel, qualification, reusedQualification) {
  return deepFreeze({
    ok: true,
    cacheLevel,
    reusedQualification,
    qualification: clone(qualification),
  });
}

function operationSucceeded(result) {
  return result === true || result?.ok === true || result?.valid === true;
}

function payload(result, key) {
  if (!result || typeof result !== "object") return undefined;
  return result[key] ?? result.value ?? result;
}

function strictCanonicalEvidence(value) {
  if (typeof value?.canonicalEvidenceJson !== "string") return null;
  try {
    const evidence = JSON.parse(value.canonicalEvidenceJson);
    const topLevel = [
      "attempt", "canonicalization", "format", "formatVersion", "identity", "media",
      "observations", "performance", "repairs", "replayPlanSha256", "status",
    ];
    const identity = [
      "assetBundleSha256", "environmentFactsSha256", "runtimePackSha256",
      "runtimeReceiptSha256", "spatialIntentSha256", "spatialSolutionSha256",
      "spatialVerificationSha256",
    ];
    if (canonicalizeJsonValue(evidence) !== value.canonicalEvidenceJson ||
        evidence?.format !== "matrix-oasis.prototype-runtime-evidence" ||
        evidence?.formatVersion !== "0.1.0" ||
        evidence?.canonicalization !== "matrix-oasis.canonical-json/1" ||
        evidence?.status !== "passed" ||
        Object.keys(evidence).sort().join("\0") !== topLevel.sort().join("\0") ||
        !evidence.identity || Object.keys(evidence.identity).sort().join("\0") !== identity.sort().join("\0") ||
        !identity.every((key) => SHA_256.test(evidence.identity[key]))) return null;
    return evidence;
  } catch {
    return null;
  }
}

function solutionSha256(value) {
  const parsedEvidence = value?.canonicalEvidenceJson === undefined ? null : strictCanonicalEvidence(value);
  if (value?.canonicalEvidenceJson !== undefined && parsedEvidence === null) return null;
  const candidates = [
    parsedEvidence?.identity?.spatialSolutionSha256,
    value?.spatialSolutionSha256,
    value?.solutionSha256,
    value?.identity?.spatialSolutionSha256,
    value?.hashes?.spatialSolutionSha256,
    value?.qualification?.hashes?.spatialSolutionSha256,
    value?.manifest?.hashes?.spatialSolutionSha256,
  ].filter((candidate) => SHA_256.test(candidate));
  return candidates.length > 0 && candidates.every((candidate) => candidate === candidates[0])
    ? candidates[0] : null;
}

function evidenceAttempt(value) {
  const parsedEvidence = value?.canonicalEvidenceJson === undefined ? null : strictCanonicalEvidence(value);
  if (value?.canonicalEvidenceJson !== undefined && parsedEvidence === null) return null;
  const candidates = [
    parsedEvidence?.attempt,
    value?.attempt,
    value?.evidence?.attempt,
    value?.qualification?.evidence?.attempt,
    value?.manifest?.evidence?.attempt,
  ].filter((candidate) => Number.isInteger(candidate) && candidate >= 0 && candidate <= 2);
  return candidates.length > 0 && candidates.every((candidate) => candidate === candidates[0])
    ? candidates[0] : null;
}

function requiredOperations(cacheLevel, operations) {
  const required = cacheLevel === "qualified"
    ? ["verifyQualified"]
    : cacheLevel === "evidence-only"
      ? ["verifyEvidence", "publishQualification"]
      : cacheLevel === "solved-only"
        ? ["verifySolved", "collectEvidence", "publishQualification"]
        : ["analyze", "solve", "verify", "collectEvidence", "publishQualification"];
  return required.every((name) => typeof operations?.[name] === "function");
}

function requiredArtifactPresent(cacheLevel, request) {
  if (cacheLevel === "qualified") return request?.qualified && typeof request.qualified === "object";
  if (cacheLevel === "evidence-only") return request?.evidence && typeof request.evidence === "object";
  if (cacheLevel === "solved-only") return request?.solved && typeof request.solved === "object";
  return request?.source && typeof request.source === "object";
}

async function stage(request, subphase, attempt) {
  if (!SUBPHASES.has(subphase) || !Number.isInteger(attempt) || attempt < 0 || attempt > 2) {
    throw new Error("PROTOTYPE_CREATOR_QUALIFICATION_STAGE_INVALID");
  }
  if (typeof request.onStage === "function") {
    await request.onStage(deepFreeze({ stage: "qualifying", subphase, attempt }));
  }
}

async function collectEvidence(request, operations, context, initialSolutionSha256, expectedSolutionSha256) {
  let highestAttempt = 0;
  const emitOperationAttempt = async (attempt) => {
    if (!Number.isInteger(attempt) || attempt < 1 || attempt > 2 || attempt !== highestAttempt + 1) {
      throw new Error("PROTOTYPE_CREATOR_QUALIFICATION_ATTEMPT_INVALID");
    }
    highestAttempt = attempt;
    await stage(request, "evidencing", attempt);
  };
  await stage(request, "evidencing", 0);
  const result = await operations.collectEvidence(frozenOperationRequest({
    ...context,
    initialSolutionSha256,
    expectedSolutionSha256,
  }, { onAttempt: emitOperationAttempt }));
  if (!operationSucceeded(result)) return null;
  const evidence = payload(result, "evidence");
  const attempt = evidenceAttempt(result) ?? evidenceAttempt(evidence);
  if (attempt === null) return null;
  if (attempt < highestAttempt) return null;
  while (highestAttempt < attempt) {
    highestAttempt += 1;
    await stage(request, "evidencing", highestAttempt);
  }
  if (attempt !== highestAttempt) return null;
  const finalSolutionSha256 = SHA_256.test(result?.finalSolutionSha256)
    ? result.finalSolutionSha256
    : solutionSha256(evidence);
  if (!finalSolutionSha256 || solutionSha256(evidence) !== finalSolutionSha256 ||
      (expectedSolutionSha256 && finalSolutionSha256 !== expectedSolutionSha256)) return null;
  const finalSolved = result?.solved ?? context.solved;
  const finalVerification = result?.verification ?? context.verification;
  if (solutionSha256(finalSolved) !== finalSolutionSha256 ||
      solutionSha256(finalVerification) !== finalSolutionSha256) return null;
  return deepFreeze({
    source: clone(result?.source ?? context.source),
    analysis: clone(result?.analysis ?? context.analysis),
    solved: clone(finalSolved),
    verification: clone(finalVerification),
    evidence: clone(evidence),
    attempt,
    finalSolutionSha256,
  });
}

async function publish(request, operations, context, expectedSolutionSha256) {
  const result = await operations.publishQualification(frozenOperationRequest({
    ...context,
    expectedSolutionSha256,
  }));
  if (!operationSucceeded(result)) return null;
  const qualification = payload(result, "qualification");
  if (solutionSha256(qualification) !== expectedSolutionSha256) return null;
  return qualification;
}

/**
 * Pure R16 local-qualification orchestration. All filesystem, Godot, and cache
 * effects are injected through operations; no effect is attempted after a
 * failed prerequisite.
 */
export async function qualifyPrototypeForCreator(request, operations) {
  const cacheLevel = request?.cacheLevel;
  if (!CACHE_LEVELS.has(cacheLevel) || !requiredArtifactPresent(cacheLevel, request) ||
      !requiredOperations(cacheLevel, operations) ||
      (request?.expectedSolutionSha256 !== undefined && !SHA_256.test(request.expectedSolutionSha256)) ||
      (request?.onStage !== undefined && typeof request.onStage !== "function")) {
    return failure("PROTOTYPE_CREATOR_QUALIFICATION_INPUT_INVALID");
  }

  try {
    if (cacheLevel === "qualified") {
      const expected = request.expectedSolutionSha256 ?? solutionSha256(request.qualified);
      if (!expected || solutionSha256(request.qualified) !== expected) {
        return failure("PROTOTYPE_CREATOR_QUALIFICATION_SOLUTION_IDENTITY_MISMATCH");
      }
      await stage(request, "verifying", evidenceAttempt(request.qualified) ?? 0);
      const verified = await operations.verifyQualified(frozenOperationRequest({
        qualified: request.qualified,
        expectedSolutionSha256: expected,
      }));
      if (!operationSucceeded(verified)) {
        return failure("PROTOTYPE_CREATOR_QUALIFICATION_REFERENCE_INVALID");
      }
      return success(cacheLevel, request.qualified, true);
    }

    if (cacheLevel === "evidence-only") {
      const expected = request.expectedSolutionSha256 ?? solutionSha256(request.evidence);
      if (!expected || solutionSha256(request.evidence) !== expected) {
        return failure("PROTOTYPE_CREATOR_QUALIFICATION_SOLUTION_IDENTITY_MISMATCH");
      }
      await stage(request, "verifying", evidenceAttempt(request.evidence) ?? 0);
      const verified = await operations.verifyEvidence(frozenOperationRequest({
        evidence: request.evidence,
        expectedSolutionSha256: expected,
      }));
      if (!operationSucceeded(verified)) {
        return failure("PROTOTYPE_CREATOR_QUALIFICATION_EVIDENCE_INVALID");
      }
      const qualification = await publish(request, operations, {
        source: request.source,
        solved: request.solved,
        verification: request.verification,
        evidence: request.evidence,
      }, expected);
      return qualification
        ? success(cacheLevel, qualification, false)
        : failure("PROTOTYPE_CREATOR_QUALIFICATION_PUBLISH_FAILED");
    }

    if (cacheLevel === "solved-only") {
      const initialSolutionSha256 = solutionSha256(request.solved);
      const expected = request.expectedSolutionSha256;
      if (!initialSolutionSha256 || (expected && initialSolutionSha256 !== expected)) {
        return failure("PROTOTYPE_CREATOR_QUALIFICATION_SOLUTION_IDENTITY_MISMATCH");
      }
      await stage(request, "verifying", 0);
      const verified = await operations.verifySolved(frozenOperationRequest({
        solved: request.solved,
        verification: request.verification,
        expectedSolutionSha256: expected ?? initialSolutionSha256,
      }));
      if (!operationSucceeded(verified)) {
        return failure("PROTOTYPE_CREATOR_QUALIFICATION_SOLUTION_INVALID");
      }
      const collected = await collectEvidence(request, operations, {
        source: request.source,
        solved: request.solved,
        verification: request.verification,
      }, initialSolutionSha256, expected);
      if (!collected) return failure("PROTOTYPE_CREATOR_QUALIFICATION_EVIDENCE_FAILED");
      const qualification = await publish(request, operations, {
        source: collected.source,
        analysis: collected.analysis,
        solved: collected.solved,
        verification: collected.verification,
        evidence: collected.evidence,
      }, collected.finalSolutionSha256);
      return qualification
        ? success(cacheLevel, qualification, false)
        : failure("PROTOTYPE_CREATOR_QUALIFICATION_PUBLISH_FAILED");
    }

    await stage(request, "analyzing", 0);
    const analyzedResult = await operations.analyze(frozenOperationRequest({ source: request.source }));
    if (!operationSucceeded(analyzedResult)) {
      return failure("PROTOTYPE_CREATOR_QUALIFICATION_ANALYSIS_FAILED");
    }
    const analysis = payload(analyzedResult, "analysis");

    await stage(request, "solving", 0);
    const solvedResult = await operations.solve(frozenOperationRequest({ source: request.source, analysis }));
    if (!operationSucceeded(solvedResult)) {
      return failure("PROTOTYPE_CREATOR_QUALIFICATION_SOLVE_FAILED");
    }
    const solved = payload(solvedResult, "solved");
    const initialSolutionSha256 = solutionSha256(solved);
    const expected = request.expectedSolutionSha256;
    if (!initialSolutionSha256 || (expected && initialSolutionSha256 !== expected)) {
      return failure("PROTOTYPE_CREATOR_QUALIFICATION_SOLUTION_IDENTITY_MISMATCH");
    }

    await stage(request, "verifying", 0);
    const verifiedResult = await operations.verify(frozenOperationRequest({
      source: request.source,
      analysis,
      solved,
      expectedSolutionSha256: expected ?? initialSolutionSha256,
    }));
    if (!operationSucceeded(verifiedResult)) {
      return failure("PROTOTYPE_CREATOR_QUALIFICATION_VERIFICATION_FAILED");
    }
    const verification = payload(verifiedResult, "verification");
    if (solutionSha256(verification) !== initialSolutionSha256) {
      return failure("PROTOTYPE_CREATOR_QUALIFICATION_SOLUTION_IDENTITY_MISMATCH");
    }

    const collected = await collectEvidence(request, operations, {
      source: request.source,
      analysis,
      solved,
      verification,
    }, initialSolutionSha256, expected);
    if (!collected) return failure("PROTOTYPE_CREATOR_QUALIFICATION_EVIDENCE_FAILED");
    const qualification = await publish(request, operations, {
      source: collected.source,
      analysis: collected.analysis,
      solved: collected.solved,
      verification: collected.verification,
      evidence: collected.evidence,
    }, collected.finalSolutionSha256);
    return qualification
      ? success(cacheLevel, qualification, false)
      : failure("PROTOTYPE_CREATOR_QUALIFICATION_PUBLISH_FAILED");
  } catch {
    return failure("PROTOTYPE_CREATOR_QUALIFICATION_INTERNAL_ERROR");
  }
}
