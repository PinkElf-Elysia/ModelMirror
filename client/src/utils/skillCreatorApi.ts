export type SkillCreatorSessionState =
  | "defining"
  | "selecting_evidence"
  | "editing_draft"
  | "designing_tests"
  | "reviewing_results"
  | "iterating"
  | "completed"
  | "archived";

export type SkillCreatorSourceKind = "blank" | "xpert_chat" | "workflow_classic";

export type SkillQualityStatus =
  | "not_evaluated"
  | "running"
  | "accepted"
  | "eval_waived"
  | "outdated";

export interface SkillPackageIssue {
  code: string;
  message: string;
  severity: "error" | "warning";
  path?: string | null;
  field?: string | null;
  line?: number | null;
}

export interface SkillPackageValidation {
  valid: boolean;
  validator_version: string;
  issues: SkillPackageIssue[];
  creator_quality?: SkillCreatorQualityReport | null;
  content_digest?: string | null;
  file_count?: number;
  total_bytes?: number;
}

export interface SkillCreatorQualityCheck {
  code: string;
  check_id?: string;
  label?: string;
  passed: boolean;
  weight?: number;
  message?: string | null;
}

export interface SkillCreatorQualityReport {
  ready: boolean;
  version?: string;
  quality_version?: string;
  contract_version?: string | null;
  playbook_version?: string;
  score?: number | null;
  requirement_ids?: string[];
  checks?: SkillCreatorQualityCheck[];
  issues?: SkillPackageIssue[];
  summary?: string | null;
}

export interface SkillPackagePayload {
  version?: 2;
  root_name: string;
  name: string;
  description: string;
  skill_markdown: string;
  files: Record<string, string>;
  content_digest?: string;
  file_count?: number;
  total_bytes?: number;
  license?: string | null;
  compatibility?: string | null;
  metadata?: Record<string, unknown>;
  allowed_tools?: string[];
}

export interface SkillPackageFrontmatter {
  name: string;
  description: string;
  license?: string | null;
  compatibility?: string | null;
  metadata: Record<string, unknown>;
  allowed_tools: string[];
}

export interface SkillCreatorDraft extends SkillPackagePayload {
  draft_id: string;
  slug: string;
  status: "draft" | "installed" | "archived";
  revision: number;
  content_revision: number;
  content_digest: string;
  draft_state_revision?: number;
  quality_required?: boolean;
  quality_status?: SkillQualityStatus;
  frontmatter?: SkillPackageFrontmatter | null;
  validation?: SkillPackageValidation;
  source_proposal_id?: string | null;
  creator_session_id?: string | null;
  created_at?: number;
  updated_at?: number;
}

export interface SkillCreatorEvidenceCandidate {
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

export interface SkillCreatorEvidenceSelection {
  candidate_id: string;
  kind: SkillCreatorEvidenceCandidate["kind"];
  title: string;
  summary: string;
  content_hash: string;
}

export interface SkillCreatorSourcePreview {
  preview_fingerprint: string;
  source_kind: SkillCreatorSourceKind;
  source_task_id: string | null;
  source_run_id: string | null;
  candidates: SkillCreatorEvidenceCandidate[];
  generated_at?: number;
}

export interface SkillCreatorProposal {
  proposal_id: string;
  kind: "skill_create" | "skill_update";
  title: string;
  status: "pending" | "approved" | "rejected" | "cancelled" | "conflict";
  revision: number;
  creator_session_id: string;
  apply_key: string;
  payload_digest: string;
  content_digest: string;
  base_digest?: string | null;
  base_revision?: number | null;
  target_id?: string | null;
  payload: SkillPackagePayload | { skill: SkillPackagePayload };
  validation?: SkillPackageValidation;
  creator_quality?: SkillCreatorQualityReport | null;
  applied_resource_id?: string | null;
  error?: string | null;
  created_at?: number;
  updated_at?: number;
}

export interface SkillCreatorSession {
  session_id: string;
  session_revision: number;
  draft_state_revision: number;
  mode: "blank" | "run";
  assistant_agent_id: "skill-creator-assistant-v1" | string;
  intent: string;
  positive_examples: string[];
  near_miss_examples: string[];
  expected_output: string;
  success_criteria: string[];
  selected_evidence: SkillCreatorEvidenceSelection[];
  evidence_preview_fingerprint?: string | null;
  evidence_confirmed?: boolean;
  proposal_id?: string | null;
  draft_id?: string | null;
  current_revision?: number | null;
  current_digest?: string | null;
  state: SkillCreatorSessionState;
  source_kind?: SkillCreatorSourceKind | null;
  source_task_id?: string | null;
  source_run_id?: string | null;
  source_xpert_id?: string | null;
  source_conversation_id?: string | null;
  source_message_id?: string | null;
  proposal?: SkillCreatorProposal | null;
  draft?: SkillCreatorDraft | null;
  created_at: number;
  updated_at: number;
}

export interface SkillCreatorStatus {
  enabled: boolean;
  version: string;
  model_available: boolean;
  assistant_agent_id: string;
  supported_sources: SkillCreatorSourceKind[];
  disabled_reason?: string | null;
  model_unavailable_reason?: string | null;
}

export interface SkillCreatorListResponse {
  items: SkillCreatorSession[];
  total: number;
}

export class SkillCreatorApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly issues: SkillPackageIssue[];

  constructor(
    message: string,
    { status, code = "skill_creator_request_failed", issues = [] }: {
      status: number;
      code?: string;
      issues?: SkillPackageIssue[];
    },
  ) {
    super(message);
    this.name = "SkillCreatorApiError";
    this.status = status;
    this.code = code;
    this.issues = issues;
  }
}

function issueList(value: unknown): SkillPackageIssue[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const record = item as Record<string, unknown>;
    if (typeof record.message !== "string") return [];
    return [{
      code: typeof record.code === "string" ? record.code : "skill_creator_issue",
      message: record.message,
      severity: record.severity === "warning" ? "warning" as const : "error" as const,
      path: typeof record.path === "string" ? record.path : undefined,
      field: typeof record.field === "string" ? record.field : undefined,
      line: typeof record.line === "number" ? record.line : undefined,
    }];
  });
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => null) as unknown;
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? (payload as { detail?: unknown }).detail
      : payload;
    const detailRecord = detail && typeof detail === "object"
      ? detail as Record<string, unknown>
      : null;
    const message = typeof detail === "string"
      ? detail
      : typeof detailRecord?.message === "string"
        ? detailRecord.message
        : `请求失败（${response.status}）`;
    throw new SkillCreatorApiError(message, {
      status: response.status,
      code: typeof detailRecord?.code === "string"
        ? detailRecord.code
        : "skill_creator_request_failed",
      issues: issueList(detailRecord?.issues),
    });
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

function unwrapSession(
  payload:
    | SkillCreatorSession
    | {
        session: SkillCreatorSession;
        draft?: SkillCreatorDraft | null;
        proposal?: SkillCreatorProposal | null;
      },
): SkillCreatorSession {
  if (!("session" in payload)) return payload;
  return {
    ...payload.session,
    draft: payload.draft ?? payload.session.draft,
    proposal: payload.proposal ?? payload.session.proposal,
  };
}

export function readSkillCreatorStatus() {
  return request<SkillCreatorStatus>("/api/skills/creator/status");
}

export async function listSkillCreatorSessions(limit = 50) {
  const response = await request<SkillCreatorListResponse | { sessions: SkillCreatorSession[] }>(
    `/api/skills/creator/sessions?limit=${Math.min(Math.max(limit, 1), 100)}`,
  );
  if ("sessions" in response) {
    return { items: response.sessions, total: response.sessions.length };
  }
  return response;
}

export async function createSkillCreatorSession(payload: {
  mode: "blank" | "run";
  intent?: string;
  source_kind?: SkillCreatorSourceKind;
  source_task_id?: string;
  source_run_id?: string;
  source_xpert_id?: string;
  source_conversation_id?: string;
  source_message_id?: string;
}) {
  return unwrapSession(await request<SkillCreatorSession | { session: SkillCreatorSession }>(
    "/api/skills/creator/sessions",
    jsonRequest("POST", payload),
  ));
}

export async function readSkillCreatorSession(sessionId: string) {
  return unwrapSession(await request<SkillCreatorSession | { session: SkillCreatorSession }>(
    `/api/skills/creator/sessions/${encodeURIComponent(sessionId)}`,
  ));
}

export async function updateSkillCreatorSession(
  sessionId: string,
  payload: {
    expected_session_revision: number;
    intent?: string;
    positive_examples?: string[];
    near_miss_examples?: string[];
    expected_output?: string;
    success_criteria?: string[];
  },
) {
  return unwrapSession(await request<SkillCreatorSession | { session: SkillCreatorSession }>(
    `/api/skills/creator/sessions/${encodeURIComponent(sessionId)}`,
    jsonRequest("PATCH", payload),
  ));
}

export function previewSkillCreatorSource(session: SkillCreatorSession) {
  return request<SkillCreatorSourcePreview | {
    fingerprint: string;
    candidates: SkillCreatorEvidenceCandidate[];
  }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/source-preview`,
    { method: "POST" },
  ).then((preview) => "preview_fingerprint" in preview ? preview : {
    preview_fingerprint: preview.fingerprint,
    source_kind: session.source_kind ?? "blank",
    source_task_id: session.source_task_id ?? null,
    source_run_id: session.source_run_id ?? null,
    candidates: preview.candidates,
  });
}

export async function selectSkillCreatorEvidence(
  session: SkillCreatorSession,
  preview: SkillCreatorSourcePreview,
  candidateIds: string[],
) {
  return unwrapSession(await request<SkillCreatorSession | { session: SkillCreatorSession }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/evidence`,
    jsonRequest("PUT", {
      expected_session_revision: session.session_revision,
      preview_fingerprint: preview.preview_fingerprint,
      candidate_ids: candidateIds,
    }),
  ));
}

export async function generateSkillCreatorProposal(session: SkillCreatorSession) {
  const result = await request<{
    session?: SkillCreatorSession;
    proposal: SkillCreatorProposal;
  }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/generate`,
    jsonRequest("POST", {
      expected_session_revision: session.session_revision,
    }),
  );
  return {
    proposal: result.proposal,
    session: result.session ?? await readSkillCreatorSession(session.session_id),
  };
}

export async function createBlankSkillCreatorDraft(
  session: SkillCreatorSession,
  rootName: string,
  description: string,
) {
  return unwrapSession(await request<SkillCreatorSession | { session: SkillCreatorSession }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/draft`,
    jsonRequest("POST", {
      expected_session_revision: session.session_revision,
      skill_id: rootName,
      description: description.trim().slice(0, 1_024),
    }),
  ));
}

export async function saveSkillCreatorDraft(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
  packagePayload: SkillPackagePayload,
) {
  return unwrapSession(await request<SkillCreatorSession | { session: SkillCreatorSession }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/draft`,
    jsonRequest("PUT", {
      expected_session_revision: session.session_revision,
      expected_revision: draft.revision,
      expected_digest: draft.content_digest,
      name: packagePayload.name,
      slug: packagePayload.root_name,
      description: packagePayload.description,
      skill_markdown: packagePayload.skill_markdown,
      files: packagePayload.files,
    }),
  ));
}

export function readSkillCreatorProposal(proposalId: string) {
  return request<SkillCreatorProposal>(
    `/api/runtime/authoring-proposals/${encodeURIComponent(proposalId)}`,
  );
}

export function readSkillCreatorDraft(draftId: string) {
  return request<SkillCreatorDraft>(
    `/api/skills/drafts/${encodeURIComponent(draftId)}`,
  );
}

export function approveSkillCreatorProposal(proposal: SkillCreatorProposal) {
  return request<SkillCreatorProposal | { proposal: SkillCreatorProposal }>(
    `/api/runtime/authoring-proposals/${encodeURIComponent(proposal.proposal_id)}/approve`,
    jsonRequest("POST", {
      revision: proposal.revision,
      apply_key: proposal.apply_key,
      reason: "用户在 Skill Creator 工作台批准",
    }),
  ).then((response) => "proposal" in response ? response.proposal : response);
}

export function rejectSkillCreatorProposal(
  proposal: SkillCreatorProposal,
  reason: string,
) {
  return request<SkillCreatorProposal | { proposal: SkillCreatorProposal }>(
    `/api/runtime/authoring-proposals/${encodeURIComponent(proposal.proposal_id)}/reject`,
    jsonRequest("POST", {
      revision: proposal.revision,
      reason: reason.trim(),
    }),
  ).then((response) => "proposal" in response ? response.proposal : response);
}

export function proposalPackage(proposal: SkillCreatorProposal): SkillPackagePayload {
  return "skill" in proposal.payload ? proposal.payload.skill : proposal.payload;
}

export function copySkillCreatorSession(
  source: SkillCreatorSession,
  packagePayload?: SkillPackagePayload,
) {
  return createSkillCreatorSession({ mode: "blank", intent: source.intent }).then(
    async (session) => {
      const updated = await updateSkillCreatorSession(session.session_id, {
        expected_session_revision: session.session_revision,
        positive_examples: source.positive_examples,
        near_miss_examples: source.near_miss_examples,
        expected_output: source.expected_output,
        success_criteria: source.success_criteria,
      });
      const preview = await previewSkillCreatorSource(updated);
      const confirmed = await selectSkillCreatorEvidence(updated, preview, []);
      if (!packagePayload) return confirmed;
      const blank = await createBlankSkillCreatorDraft(
        confirmed,
        packagePayload.root_name,
        packagePayload.description,
      );
      if (!blank.draft) return blank;
      return saveSkillCreatorDraft(blank, blank.draft, packagePayload);
    },
  );
}
