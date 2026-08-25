const SCORE_KEYS = Object.freeze(["architectureCompatibility", "standaloneIntegration", "determinismTestability", "securityFailClosed", "maintenanceSourceRisk", "performanceRuntime", "functionality"]);
const GATE_KEYS = Object.freeze(["license", "reproducibleSource", "secretIsolation", "filesystemIsolation", "authorityCompatibility", "runtimeCompatibility"]);

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  Object.values(value).forEach(deepFreeze);
  return Object.freeze(value);
}

export function evaluateV2Candidate(report, policy = {}) {
  const gateValues = GATE_KEYS.map((key) => report?.hardGates?.[key]);
  const hardGateFailed = gateValues.includes("fail");
  const hardGateUnproven = gateValues.some((value) => value !== "pass" && value !== "fail");
  const total = SCORE_KEYS.reduce((sum, key) => sum + (Number.isInteger(report?.scores?.[key]) ? report.scores[key] : 0), 0);
  let conclusion;
  if (hardGateFailed) conclusion = "rejected";
  else if (hardGateUnproven || report?.execution?.status === "deferred") conclusion = "deferred";
  else if (report?.execution?.status === "failed") conclusion = "rejected";
  else if (total >= (policy.recommendedMinimum ?? 80)) conclusion = "recommended";
  else if (total >= (policy.backupMinimum ?? 65)) conclusion = "backup";
  else conclusion = "rejected";
  return deepFreeze({ candidateId: report?.candidate?.id ?? "invalid", lane: report?.candidate?.lane ?? "invalid", hardGatesPassed: !hardGateFailed && !hardGateUnproven, total, conclusion, switchConditions: Array.isArray(report?.switchConditions) ? report.switchConditions.map((condition) => ({ ...condition })) : [] });
}

export function rankV2Lane(evaluations) {
  const rank = { recommended: 0, backup: 1, deferred: 2, rejected: 3 };
  const ordered = [...evaluations].sort((left, right) => rank[left.conclusion] - rank[right.conclusion] || right.total - left.total || left.candidateId.localeCompare(right.candidateId));
  if (ordered.length >= 2 && ordered[0].hardGatesPassed && ordered[1].hardGatesPassed && Math.abs(ordered[0].total - ordered[1].total) <= 5) {
    const surfaces = evaluations.reduce((map, item) => map.set(item.candidateId, item.runtimeSurface ?? Number.MAX_SAFE_INTEGER), new Map());
    ordered.sort((left, right) => rank[left.conclusion] - rank[right.conclusion] || (Math.abs(left.total - right.total) <= 5 ? (surfaces.get(left.candidateId) - surfaces.get(right.candidateId)) : right.total - left.total) || left.candidateId.localeCompare(right.candidateId));
  }
  return deepFreeze(ordered.map((item) => ({ ...item })));
}
