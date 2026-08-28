export type SkillExperienceSource =
  | {
      sourceKind: "workflow_classic";
      taskId: string;
      runId: string;
    }
  | {
      sourceKind: "xpert_chat";
      taskId: string;
      runId: string;
      xpertId: string;
      conversationId: string;
      messageId: string;
    };

export type SkillExperienceCandidateState =
  | "captured"
  | "analyzing"
  | "awaiting_review"
  | "promotion_ready"
  | "promoted"
  | "dismissed"
  | "failed"
  | "stale"
  | "archived";

export interface SkillExperienceEvidenceCandidate {
  candidate_id: string;
  kind:
    | "intent_summary"
    | "successful_steps"
    | "tool_names"
    | "user_correction"
    | "io_shape"
    | "final_output_excerpt";
  title: string;
  summary: string;
  content_hash: string;
  default_selected: boolean;
}

export interface SkillExperienceEvidencePreview {
  version: string;
  source_kind: "workflow_classic" | "xpert_chat";
  source_task_id: string;
  source_run_id: string;
  source_title: string;
  preview_fingerprint: string;
  candidates: SkillExperienceEvidenceCandidate[];
}

export interface DistilledSkillBrief {
  version: string;
  revision: number;
  digest: string;
  suggestion: "create" | "update" | "no_skill";
  recommendation_reason: string;
  no_skill_reason?:
    | "one_off_task"
    | "preference_or_environment_fact"
    | "insufficient_evidence"
    | "already_covered"
    | "cannot_generalize"
    | null;
  intent: string;
  positive_examples: string[];
  negative_examples: string[];
  expected_output: string;
  success_criteria: string[];
  reusable_steps: string[];
  failure_boundaries: string[];
  resource_clues: string[];
  overfitting_risk: string;
  source: "model" | "manual" | "user" | "trusted_handoff";
  complete: boolean;
}

export interface SkillExperienceOverlap {
  candidate_id: string;
  candidate_fingerprint: string;
  name: string;
  source_type: string;
  source_kind: string;
  installed_skill_id?: string | null;
  creator_draft_id?: string | null;
  update_target_eligible: boolean;
  best_rank: number;
  major_overlap: boolean;
}

export interface SkillExperienceCandidate {
  candidate_id: string;
  version: string;
  revision: number;
  digest: string;
  state: SkillExperienceCandidateState;
  source_kind: "workflow_classic" | "xpert_chat";
  source_task_id: string;
  source_run_id: string;
  source_xpert_id?: string | null;
  source_conversation_id?: string | null;
  source_message_id?: string | null;
  selected_evidence: Array<{
    evidence_id: string;
    kind: SkillExperienceEvidenceCandidate["kind"];
    title: string;
    summary: string;
    content_hash: string;
  }>;
  analysis_attempt?: {
    status: "running" | "succeeded" | "manual_required" | "failed";
    executor_mode: "model" | "manual" | "trusted_handoff";
    error_code?: string | null;
  } | null;
  brief?: DistilledSkillBrief | null;
  overlaps: SkillExperienceOverlap[];
  decision?: {
    decision: "create" | "update" | "dismiss";
    target_skill_id?: string | null;
  } | null;
  promotion?: {
    session_id: string;
    route: string;
    decision: "create" | "update";
  } | null;
  updated_at: number;
}

export interface SkillExperienceStatus {
  version: string;
  enabled: boolean;
  available: boolean;
  model_calls_enabled: boolean;
  error_code?: string | null;
}

export class SkillExperienceApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(message: string, status: number, code: string) {
    super(message);
    this.name = "SkillExperienceApiError";
    this.status = status;
    this.code = code;
  }
}

const ERROR_COPY: Record<string, string> = {
  skill_experience_disabled: "运行经验沉淀尚未启用，将改用原 Creator 入口。",
  skill_experience_source_invalid: "这次运行的可信来源已失效，无法继续沉淀。",
  skill_experience_source_not_completed: "只有已成功完成的运行才能沉淀为 Skill。",
  skill_experience_evidence_stale: "脱敏素材已变化，请重新加载后确认。",
  skill_experience_store_unavailable: "运行经验或已安装 Skill 暂时无法读取。请恢复服务端存储后重试。",
  skill_experience_analysis_unconfigured: "当前未配置模型，请手工补全提炼结果。",
  skill_experience_analysis_invalid: "AI 返回的分析不可用，已切换为手工补全。",
  skill_experience_candidate_conflict: "运行经验已在其他页面更新，请重新加载。",
  skill_experience_decision_required: "请先补全并确认沉淀方案。",
  skill_experience_update_target_invalid: "只能更新当前可编辑的 Creator Skill。",
  skill_experience_promotion_stale: "来源或目标版本已变化，请重新加载后检查。",
  skill_experience_already_promoted: "这次经验已经进入 Creator。",
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => null) as unknown;
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? (payload as { detail?: unknown }).detail
      : payload;
    const record = detail && typeof detail === "object"
      ? detail as Record<string, unknown>
      : null;
    const code = typeof record?.code === "string"
      ? record.code
      : "skill_experience_request_failed";
    const fallback = typeof detail === "string"
      ? detail
      : typeof record?.message === "string"
        ? record.message
        : `请求失败（${response.status}）`;
    throw new SkillExperienceApiError(ERROR_COPY[code] || fallback, response.status, code);
  }
  return payload as T;
}

function jsonRequest(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

function sourcePayload(source: SkillExperienceSource) {
  return source.sourceKind === "xpert_chat"
    ? {
        source_kind: source.sourceKind,
        source_task_id: source.taskId,
        source_run_id: source.runId,
        source_xpert_id: source.xpertId,
        source_conversation_id: source.conversationId,
        source_message_id: source.messageId,
      }
    : {
        source_kind: source.sourceKind,
        source_task_id: source.taskId,
        source_run_id: source.runId,
      };
}

function mutationPayload(candidate: SkillExperienceCandidate) {
  return {
    expected_revision: candidate.revision,
    expected_digest: candidate.digest,
  };
}

let candidateListRequest: Promise<SkillExperienceCandidate[]> | null = null;
let statusRequest: Promise<SkillExperienceStatus> | null = null;

export function clearSkillExperienceCandidateCache() {
  candidateListRequest = null;
}

export function clearSkillExperienceApiCache() {
  candidateListRequest = null;
  statusRequest = null;
}

export function readSkillExperienceStatus() {
  if (!statusRequest) {
    statusRequest = request<SkillExperienceStatus>("/api/skills/experience/status")
      .catch((error) => {
        statusRequest = null;
        throw error;
      });
  }
  return statusRequest;
}

export async function listSkillExperienceCandidates({ cached = false } = {}) {
  if (cached && candidateListRequest) return candidateListRequest;
  const pending = request<{ candidates: SkillExperienceCandidate[] }>(
    "/api/skills/experience/candidates?limit=500",
  ).then((response) => response.candidates);
  if (cached) candidateListRequest = pending;
  try {
    return await pending;
  } catch (error) {
    if (candidateListRequest === pending) candidateListRequest = null;
    throw error;
  }
}

export function sourceMatchesCandidate(
  source: SkillExperienceSource,
  candidate: SkillExperienceCandidate,
) {
  if (
    candidate.source_kind !== source.sourceKind
    || candidate.source_task_id !== source.taskId
  ) return false;
  if (source.sourceKind === "workflow_classic") return true;
  return candidate.source_xpert_id === source.xpertId
    && candidate.source_conversation_id === source.conversationId
    && candidate.source_message_id === source.messageId;
}

export async function findSkillExperienceCandidate(source: SkillExperienceSource) {
  const candidates = await listSkillExperienceCandidates({ cached: true });
  return candidates.find((candidate) => sourceMatchesCandidate(source, candidate)) ?? null;
}

export async function createSkillExperienceCandidate(source: SkillExperienceSource) {
  const response = await request<{
    candidate: SkillExperienceCandidate;
    evidence_preview: SkillExperienceEvidencePreview;
  }>("/api/skills/experience/candidates", jsonRequest("POST", sourcePayload(source)));
  clearSkillExperienceCandidateCache();
  return response;
}

export function readSkillExperienceCandidate(candidateId: string) {
  return request<{
    candidate: SkillExperienceCandidate;
    evidence_preview: SkillExperienceEvidencePreview | null;
  }>(`/api/skills/experience/candidates/${encodeURIComponent(candidateId)}`);
}

export async function selectSkillExperienceEvidence(
  candidate: SkillExperienceCandidate,
  preview: SkillExperienceEvidencePreview,
  evidenceIds: string[],
) {
  const response = await request<{ candidate: SkillExperienceCandidate }>(
    `/api/skills/experience/candidates/${encodeURIComponent(candidate.candidate_id)}/evidence`,
    jsonRequest("PUT", {
      ...mutationPayload(candidate),
      preview_fingerprint: preview.preview_fingerprint,
      evidence_ids: evidenceIds,
    }),
  );
  clearSkillExperienceCandidateCache();
  return response.candidate;
}

export async function analyzeSkillExperienceCandidate(candidate: SkillExperienceCandidate) {
  const response = await request<{ candidate: SkillExperienceCandidate }>(
    `/api/skills/experience/candidates/${encodeURIComponent(candidate.candidate_id)}/analyze`,
    jsonRequest("POST", mutationPayload(candidate)),
  );
  clearSkillExperienceCandidateCache();
  return response.candidate;
}

export async function updateSkillExperienceBrief(
  candidate: SkillExperienceCandidate,
  brief: Omit<DistilledSkillBrief, "version" | "revision" | "digest" | "source" | "complete">,
) {
  const response = await request<{ candidate: SkillExperienceCandidate }>(
    `/api/skills/experience/candidates/${encodeURIComponent(candidate.candidate_id)}/brief`,
    jsonRequest("PATCH", { ...mutationPayload(candidate), ...brief }),
  );
  clearSkillExperienceCandidateCache();
  return response.candidate;
}

export async function decideSkillExperienceCandidate(
  candidate: SkillExperienceCandidate,
  payload: {
    decision: "create" | "update" | "dismiss";
    target_skill_id?: string;
    override_reason?: string;
    new_boundary?: string;
  },
) {
  const response = await request<{ candidate: SkillExperienceCandidate }>(
    `/api/skills/experience/candidates/${encodeURIComponent(candidate.candidate_id)}/decision`,
    jsonRequest("POST", { ...mutationPayload(candidate), ...payload }),
  );
  clearSkillExperienceCandidateCache();
  return response.candidate;
}

export async function dismissSkillExperienceCandidate(
  candidate: SkillExperienceCandidate,
  reason = "用户暂不处理",
) {
  const response = await request<{ candidate: SkillExperienceCandidate }>(
    `/api/skills/experience/candidates/${encodeURIComponent(candidate.candidate_id)}/dismiss`,
    jsonRequest("POST", { ...mutationPayload(candidate), reason }),
  );
  clearSkillExperienceCandidateCache();
  return response.candidate;
}

export async function promoteSkillExperienceCandidate(candidate: SkillExperienceCandidate) {
  const response = await request<{
    candidate: SkillExperienceCandidate;
    creator_session_id: string;
    route: string;
  }>(
    `/api/skills/experience/candidates/${encodeURIComponent(candidate.candidate_id)}/promote`,
    jsonRequest("POST", mutationPayload(candidate)),
  );
  clearSkillExperienceCandidateCache();
  return response;
}
