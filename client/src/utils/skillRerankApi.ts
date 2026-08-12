export type SkillRerankStatus =
  | "lexical"
  | "semantic"
  | "lexical_fallback"
  | "shadow";

export interface SkillRankingResult {
  candidateId: string;
  candidateFingerprint: string;
  name: string;
  summary: string;
  category: string;
  kind: "skill" | "skillset";
  installStatus: string;
  score: number;
  reasons: Array<{
    type: string;
    label: string;
    origin: "direct" | "expanded";
    matchedTerms: string[];
  }>;
  lexicalRank?: number;
  semanticRank?: number | null;
  rankDelta?: number;
}

export interface SkillRankingReceipt {
  queryHash: string;
  candidateSetFingerprint: string;
  candidateFingerprints: Array<{
    candidateId: string;
    candidateFingerprint: string;
  }>;
  lexicalRanks: string[];
  semanticRanks: string[];
  proposedRanks: string[];
  finalRanks: string[];
  rankChanges: Array<{
    candidateId: string;
    lexicalRank: number;
    finalRank: number;
  }>;
  provider: string;
  model: string | null;
  strategyVersion: string;
  durationMs: number;
  fallbackReason: string | null;
}

export interface SkillSearchOutcome {
  lexicalResults: SkillRankingResult[];
  finalResults: SkillRankingResult[];
  status: SkillRerankStatus;
  warnings: string[];
  receipt: SkillRankingReceipt;
  governanceRevision: number;
}

export interface SkillRerankPolicyStatus {
  provider: string;
  providerAvailable: boolean;
  apiAvailable: boolean;
  llmAvailable: boolean;
  routerMode: "off" | "shadow" | "on";
  effectiveRouterMode: "off" | "shadow" | "on";
  searchIndexFingerprint: string | null;
  governanceAvailable: boolean;
  governanceRevision: number;
  feedbackCount: number;
  evaluationCount: number;
  evaluations: SkillRerankEvaluation[];
  policyReasons: string[];
  warnings: string[];
  policy: {
    revision: number;
    mode: "shadow" | "on";
    promotion: Record<string, unknown> | null;
    updatedAt: number;
  };
  shadow: {
    sampleCount: number;
    changedCount: number;
    fallbackCount: number;
    fallbackRate: number;
    p95DurationMs: number;
    fallbackReasons: Record<string, number>;
  } | null;
}

export interface SkillRerankEvaluation {
  evaluationId: string;
  revision: number;
  status: "queued" | "running" | "completed" | "failed";
  createdAt: number;
  completedAt: number | null;
  errorCode: string | null;
  provider: string;
  model: string | null;
  baseline: Record<string, number> | null;
  semantic: Record<string, number | unknown[]> | null;
  feedbackSummary: Record<string, number> | null;
  caseReports?: Array<{
    caseId: string;
    kind: "positive" | "near_miss";
    scope: "market" | "router";
    status: SkillRerankStatus;
    fallbackReason: string | null;
    durationMs: number;
    rankChanges: number;
  }>;
  gates: Array<{ code: string; passed: boolean; details: Record<string, number> }>;
  eligibleForPromotion: boolean;
}

export class SkillRerankApiError extends Error {
  code: string;
  status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.name = "SkillRerankApiError";
    this.code = code;
    this.status = status;
  }
}

async function readResponse<T>(response: Response): Promise<T> {
  if (response.ok) return (await response.json()) as T;
  let code = "skill_rerank_request_failed";
  let message = `Skill 语义重排请求失败（HTTP ${response.status}）。`;
  try {
    const body = (await response.json()) as {
      detail?: string | { code?: string; message?: string };
    };
    if (typeof body.detail === "string") message = body.detail;
    else if (body.detail) {
      code = body.detail.code || code;
      message = body.detail.message || message;
    }
  } catch {
    // Keep the bounded generic error; do not surface raw provider responses.
  }
  throw new SkillRerankApiError(message, code, response.status);
}

export async function searchSkills(
  query: string,
  semantic: boolean,
): Promise<SkillSearchOutcome> {
  return readResponse(
    await fetch("/api/skills/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, limit: 6, semantic }),
    }),
  );
}

export async function saveSkillRerankFeedback(input: {
  expectedRevision: number;
  query: string;
  candidateId: string;
  candidateFingerprint: string;
  judgment: "relevant" | "not_relevant";
  receipt: SkillRankingReceipt;
}): Promise<{ feedback: { feedbackId: string }; governanceRevision: number }> {
  return readResponse(
    await fetch("/api/skills/rerank/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: input.expectedRevision,
        query: input.query,
        candidate_id: input.candidateId,
        candidate_fingerprint: input.candidateFingerprint,
        judgment: input.judgment,
        receipt: input.receipt,
      }),
    }),
  );
}

export async function readSkillRerankPolicy(): Promise<SkillRerankPolicyStatus> {
  return readResponse(await fetch("/api/skills/rerank/policy"));
}

export async function startSkillRerankEvaluation(
  expectedRevision: number,
): Promise<SkillRerankEvaluation> {
  return readResponse(
    await fetch("/api/skills/rerank/evaluations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: expectedRevision }),
    }),
  );
}

export async function readSkillRerankEvaluation(
  evaluationId: string,
): Promise<SkillRerankEvaluation> {
  return readResponse(
    await fetch(`/api/skills/rerank/evaluations/${encodeURIComponent(evaluationId)}`),
  );
}

export async function promoteSkillRerankPolicy(input: {
  expectedRevision: number;
  evaluationId: string;
}): Promise<{ status: SkillRerankPolicyStatus }> {
  return readResponse(
    await fetch("/api/skills/rerank/policy/promote", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: input.expectedRevision,
        evaluation_id: input.evaluationId,
        confirmed: true,
      }),
    }),
  );
}

export async function rollbackSkillRerankPolicy(
  expectedRevision: number,
): Promise<{ status: SkillRerankPolicyStatus }> {
  return readResponse(
    await fetch("/api/skills/rerank/policy/rollback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: expectedRevision, confirmed: true }),
    }),
  );
}

export async function clearSkillRerankFeedback(
  expectedRevision: number,
): Promise<{ revision: number; feedbackCount: number }> {
  return readResponse(
    await fetch(
      `/api/skills/rerank/feedback?expected_revision=${expectedRevision}`,
      { method: "DELETE" },
    ),
  );
}
