import { V2_CLASS_GATES, V2_DESKTOP_GATES, V2_LANES, V2_SCORE_LIMITS } from "./schema.mjs";

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.values(value).forEach(deepFreeze);
  return Object.freeze(value);
}

function capture(value, depth = 0) {
  if (depth > 64) throw new TypeError("INVALID_INPUT");
  if (value === null || ["string", "boolean"].includes(typeof value)) return value;
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) throw new TypeError("INVALID_INPUT");
    return value;
  }
  if (!value || typeof value !== "object") throw new TypeError("INVALID_INPUT");
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== Array.prototype && prototype !== null) throw new TypeError("INVALID_INPUT");
  const descriptors = Object.getOwnPropertyDescriptors(value);
  if (Reflect.ownKeys(descriptors).some((key) =>
    typeof key !== "string" ||
    descriptors[key].get ||
    descriptors[key].set ||
    (!descriptors[key].enumerable && !(Array.isArray(value) && key === "length"))
  )) {
    throw new TypeError("INVALID_INPUT");
  }
  if (Array.isArray(value)) {
    if (Object.keys(descriptors).filter((key) => key !== "length").length !== value.length) throw new TypeError("INVALID_INPUT");
    return Object.keys(value).map((key) => capture(descriptors[key].value, depth + 1));
  }
  return Object.fromEntries(Object.keys(descriptors).map((key) => [key, capture(descriptors[key].value, depth + 1)]));
}

function total(scores) {
  return Object.keys(V2_SCORE_LIMITS).reduce((sum, key) => sum + (Number.isInteger(scores?.[key]) ? scores[key] : 0), 0);
}

function scoresValid(scores) {
  const expected = Object.keys(V2_SCORE_LIMITS);
  return Boolean(
    scores &&
    typeof scores === "object" &&
    Object.keys(scores).length === expected.length &&
    expected.every((key) => Object.hasOwn(scores, key)) &&
    Object.entries(V2_SCORE_LIMITS).every(([key, maximum]) => Number.isInteger(scores[key]) && scores[key] >= 0 && scores[key] <= maximum),
  );
}

function evaluationPolicyValid(policy) {
  return Object.keys(policy).every((key) => ["shortlistMinimumScore", "integrationMinimumScore", "maximumPerLane", "nearTieScoreDelta"].includes(key)) &&
    (policy.shortlistMinimumScore === undefined || policy.shortlistMinimumScore === 70) &&
    (policy.integrationMinimumScore === undefined || policy.integrationMinimumScore === 80) &&
    (policy.maximumPerLane === undefined || [2, 3].includes(policy.maximumPerLane)) &&
    (policy.nearTieScoreDelta === undefined || policy.nearTieScoreDelta === 5);
}

function surface(candidate, evidence) {
  return {
    services: Number.isInteger(evidence?.runtimeSurface?.services) ? evidence.runtimeSurface.services : candidate?.surface?.externalServices ?? 65535,
    nativeBinaries: Number.isInteger(evidence?.runtimeSurface?.nativeBinaries) ? evidence.runtimeSurface.nativeBinaries : candidate?.surface?.nativeBinaries ?? 65535,
    dependencies: Number.isInteger(evidence?.runtimeSurface?.dependencies) ? evidence.runtimeSurface.dependencies : candidate?.surface?.dependencyCount ?? 65535,
  };
}

function invalidEvaluation() {
  return deepFreeze({
    candidateId: "invalid",
    laneId: "invalid",
    tier: "architecture-reference",
    conclusion: "deferred",
    total: 0,
    evidenceGap: true,
    productionGatesPassed: false,
    runtimeSurface: { services: 65535, nativeBinaries: 65535, dependencies: 65535 },
    switchConditions: [],
  });
}

export function evaluateV2CandidateForTier(candidateInput, evidenceInput, policyInput = {}) {
  let candidate;
  let evidence;
  let policy;
  try {
    candidate = capture(candidateInput);
    evidence = capture(evidenceInput);
    policy = capture(policyInput);
  } catch {
    return invalidEvaluation();
  }
  if (!evaluationPolicyValid(policy)) return invalidEvaluation();
  const candidateId = typeof candidate?.id === "string" ? candidate.id : "invalid";
  const laneId = V2_LANES.includes(evidence?.laneId) ? evidence.laneId : "invalid";
  const qualificationClass = evidence?.qualificationClass;
  const requiredGates = V2_CLASS_GATES[qualificationClass];
  const desktopGates = V2_DESKTOP_GATES[qualificationClass];
  if (
    candidateId === "invalid" ||
    laneId === "invalid" ||
    evidence?.candidateId !== candidateId ||
    !Array.isArray(candidate?.laneIds) ||
    !candidate.laneIds.includes(laneId) ||
    !requiredGates ||
    !desktopGates ||
    !scoresValid(evidence?.scores)
  ) return invalidEvaluation();
  const score = total(evidence?.scores);
  const shortlistMinimum = Number.isInteger(policy.shortlistMinimumScore) ? policy.shortlistMinimumScore : 70;
  const integrationMinimum = Number.isInteger(policy.integrationMinimumScore) ? policy.integrationMinimumScore : 80;
  const gates = Array.isArray(evidence?.hardGates) ? evidence.hardGates : [];
  const gateMap = new Map(gates.map((gate) => [gate.id, gate.status]));
  if (
    gateMap.size !== gates.length ||
    gateMap.size !== requiredGates.length ||
    requiredGates.some((gate) => !gateMap.has(gate)) ||
    gates.some((gate) => !["pass", "fail", "not-proven", "not-applicable"].includes(gate.status))
  ) return invalidEvaluation();
  const explicitFailure = [...gateMap.values()].includes("fail");
  const productionGatesPassed = requiredGates.length > 0 && requiredGates.every((gate) => gateMap.get(gate) === "pass");
  const desktopGatesPassed = desktopGates.every((gate) => gateMap.get(gate) === "pass");
  const unresolvedFailure = evidence?.executionStatus === "failed" && evidence?.harnessAttribution === "unresolved";
  const evidenceGap = unresolvedFailure || evidence?.executionStatus === "evidence-gap" || requiredGates.some((gate) => !gateMap.has(gate) || gateMap.get(gate) === "not-proven");
  const reusable = candidate?.license?.reuseAllowed === true && candidate?.license?.closureStatus === "approved";
  const qualifiable = candidate?.license?.qualificationAllowed === true && ["approved", "direct-approved"].includes(candidate?.license?.closureStatus);
  const commercial = candidate?.candidateType === "commercial-benchmark" || evidence?.qualificationClass === "commercial";
  const candidateFailure = evidence?.executionStatus === "failed" && evidence?.harnessAttribution === "candidate";
  let tier = "architecture-reference";
  let conclusion = "deferred";
  if (candidate?.staticExclusion?.excluded === true || candidateFailure || (explicitFailure && !unresolvedFailure)) conclusion = "rejected";
  else if (commercial) conclusion = desktopGatesPassed ? "backup" : "deferred";
  else if (productionGatesPassed && reusable && score >= integrationMinimum && evidence?.executionStatus === "executed") {
    tier = "integration-recommended";
    conclusion = "recommended";
  } else if (desktopGatesPassed && qualifiable && score >= shortlistMinimum && ["planned", "executed"].includes(evidence?.executionStatus)) {
    tier = "executable-shortlist";
    conclusion = "backup";
  } else if (score < shortlistMinimum && !evidenceGap) conclusion = "rejected";
  else conclusion = "deferred";
  return deepFreeze({
    candidateId,
    laneId,
    tier,
    conclusion,
    total: score,
    evidenceGap,
    productionGatesPassed,
    desktopGatesPassed,
    runtimeSurface: surface(candidate, evidence),
    switchConditions: Array.isArray(evidence?.switchConditions) ? evidence.switchConditions.map((condition) => ({ ...condition })) : [],
  });
}

function surfaceTotal(evaluation) {
  return evaluation.runtimeSurface.services + evaluation.runtimeSurface.nativeBinaries + evaluation.runtimeSurface.dependencies;
}

export function selectV2LaneShortlist(catalogInput, evidenceInput, policyInput = {}) {
  let catalog;
  let evidence;
  let policy;
  try {
    catalog = capture(catalogInput);
    evidence = capture(evidenceInput);
    policy = capture(policyInput);
  } catch {
    return deepFreeze([]);
  }
  if (!evaluationPolicyValid(policy)) return deepFreeze([]);
  const maximum = Number.isInteger(policy.maximumPerLane) ? Math.min(3, Math.max(2, policy.maximumPerLane)) : 3;
  const nearTie = Number.isInteger(policy.nearTieScoreDelta) ? policy.nearTieScoreDelta : 5;
  const candidateById = new Map((catalog?.catalog?.candidates ?? []).map((candidate) => [candidate.id, candidate]));
  const evidencePairs = new Set();
  for (const item of evidence) {
    const pair = `${item?.laneId ?? ""}\0${item?.candidateId ?? ""}`;
    if (evidencePairs.has(pair)) return deepFreeze([]);
    evidencePairs.add(pair);
  }
  const results = [];
  for (const laneId of V2_LANES) {
    const lane = catalog?.catalog?.lanes?.find((item) => item.id === laneId);
    if (!lane?.executable) {
      results.push({ laneId, candidateIds: [] });
      continue;
    }
    const evaluations = evidence
      .filter((item) => item?.laneId === laneId)
      .map((item) => evaluateV2CandidateForTier(candidateById.get(item.candidateId), item, policy))
      .filter((item) => item.tier !== "architecture-reference" && item.conclusion !== "rejected" && item.conclusion !== "deferred");
    evaluations.sort((left, right) => {
      const difference = right.total - left.total;
      if (Math.abs(difference) > nearTie) return difference;
      return surfaceTotal(left) - surfaceTotal(right) || difference || left.candidateId.localeCompare(right.candidateId);
    });
    results.push({ laneId, candidateIds: evaluations.slice(0, maximum).map((item) => item.candidateId) });
  }
  return deepFreeze(results);
}
