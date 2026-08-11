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

export type SkillCreatorQualityMode = "objective" | "subjective";

export type SkillEvaluationRunStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "stale";

export type SkillEvaluationItemStatus =
  | "pending"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "skill_not_read";

export type SkillEvaluationAssertionKind =
  | "exact_match"
  | "contains"
  | "not_contains"
  | "json_schema"
  | "file_exists"
  | "file_sha256";

export interface SkillEvaluationFixture {
  path: string;
  content: string;
}

export interface SkillEvaluationAssertion {
  kind: SkillEvaluationAssertionKind;
  value?: string | null;
  path?: string | null;
  schema?: Record<string, unknown> | null;
  sha256?: string | null;
  passed?: boolean | null;
  message?: string | null;
  reason?: string | null;
  score?: number | null;
}

export interface SkillEvaluationCase {
  case_id: string;
  name: string;
  prompt: string;
  expected_behavior: string;
  fixtures: SkillEvaluationFixture[];
  assertions: SkillEvaluationAssertion[];
}

export interface SkillEvaluationUsage {
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  estimated_tokens?: number | null;
  model_calls?: number | null;
  tool_calls?: number | null;
}

export interface SkillEvaluationWorkFile {
  path: string;
  size?: number | null;
  size_bytes?: number | null;
  sha256?: string | null;
  preview?: string | null;
  text_preview?: string | null;
  preview_truncated?: boolean;
}

export interface SkillEvaluationAttempt {
  attempt?: number;
  status?: SkillEvaluationItemStatus;
  output?: string | null;
  error_code?: string | null;
  error?: string | null;
  created_at?: number | null;
}

export interface SkillEvaluationItem {
  item_id: string;
  pair_id?: string | null;
  case_id: string;
  target: "baseline" | "candidate";
  repetition: number;
  status: SkillEvaluationItemStatus;
  attempts?: number;
  output?: string | null;
  actual_model?: string | null;
  skill_read?: boolean;
  work_manifest?: SkillEvaluationWorkFile[];
  assertions?: SkillEvaluationAssertion[];
  assertion_results?: SkillEvaluationAssertion[];
  score?: number | null;
  usage?: SkillEvaluationUsage | null;
  latency_ms?: number | null;
  error_code?: string | null;
  error?: string | null;
  attempt_history?: SkillEvaluationAttempt[];
}

export interface SkillEvaluationReview {
  review_revision?: number;
  decision: "accept" | "revise";
  feedback?: string | null;
  reason?: string | null;
  actor_kind?: string;
  created_at?: number;
}

export interface SkillEvaluationReport {
  completed_items?: number;
  total_items?: number;
  passed_assertions?: number;
  total_assertions?: number;
  baseline_usage?: SkillEvaluationUsage | null;
  candidate_usage?: SkillEvaluationUsage | null;
  baseline_latency_ms?: number | null;
  candidate_latency_ms?: number | null;
}

export interface SkillEvaluationRun {
  run_id: string;
  session_id: string;
  status: SkillEvaluationRunStatus;
  revision: number;
  frozen_digest: string;
  baseline_overlay_id?: string | null;
  candidate_overlay_id?: string | null;
  model_id: string;
  repetitions: number;
  case_set_revision?: number | null;
  cases: SkillEvaluationCase[];
  items: SkillEvaluationItem[];
  report?: SkillEvaluationReport | null;
  reviews?: SkillEvaluationReview[];
  review_state?: "pending" | "accepted" | "revise" | null;
  review_revision?: number;
  feedback?: string;
  feedback_revision?: number;
  cancel_requested?: boolean;
  error_code?: string | null;
  error?: string | null;
  created_at?: number;
  updated_at?: number;
}

export interface SkillCreatorQualityDecision {
  status: "accepted" | "eval_waived" | "outdated";
  revision?: number | null;
  content_digest?: string | null;
  evaluation_run_id?: string | null;
  reason?: string | null;
  actor_kind?: string | null;
  decided_at?: number | null;
}

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
  quality_decision?: SkillCreatorQualityDecision | null;
  install_state?: "not_installed" | "current" | "outdated";
  installed_skill_id?: string | null;
  installed_content_digest?: string | null;
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

export type SkillResourcePlanState =
  | "needs_input"
  | "needs_regeneration"
  | "ready"
  | "confirmed";

export interface SkillResourcePlanStep {
  step_id: string;
  instruction: string;
}

export interface SkillResourcePlanQuestion {
  question_id: string;
  question: string;
  reason: string;
}

export interface SkillResourcePlanItem {
  resource_id: string;
  spec_digest: string;
  kind: "script" | "reference" | "asset";
  action: "keep" | "create" | "update" | "delete";
  generation_cost: "low" | "medium" | "high";
  path: string;
  purpose: string;
  source_ids: string[];
  used_by_steps: string[];
  depends_on: string[];
  acceptance_checks: string[];
}

export interface SkillResourcePlan {
  plan_id: string;
  session_id: string;
  revision: number;
  digest: string;
  state: SkillResourcePlanState;
  session_revision: number;
  draft_id?: string | null;
  draft_revision?: number | null;
  draft_digest?: string | null;
  skill_name: string;
  skill_description: string;
  workflow_steps: SkillResourcePlanStep[];
  output_contract: string[];
  failure_modes: string[];
  resources: SkillResourcePlanItem[];
  clarifications: SkillResourcePlanQuestion[];
  clarification_answers: Record<string, string>;
  stale?: boolean;
  created_at: number;
  updated_at: number;
}

export type SkillResourceBuildState =
  | "planned"
  | "generating"
  | "awaiting_review"
  | "accepted"
  | "revision_requested"
  | "failed"
  | "stale";

export interface SkillResourceScriptTestResult {
  test_id: string;
  passed: boolean;
  exit_code: number;
  stdout_sha256: string;
  stderr_sha256: string;
  duration_ms: number;
  issues: string[];
}

export interface SkillResourceScriptReceipt {
  receipt_id: string;
  script_digest: string;
  profile: string;
  passed: boolean;
  results: SkillResourceScriptTestResult[];
  created_at: number;
}

export interface SkillResourceBuildItem {
  resource_id: string;
  spec_digest: string;
  kind: "script" | "reference" | "asset";
  action: "keep" | "create" | "update" | "delete";
  path: string;
  purpose: string;
  source_ids: string[];
  used_by_steps: string[];
  depends_on: string[];
  acceptance_checks: string[];
  state: SkillResourceBuildState;
  attempt: number;
  repair_count: number;
  chunks: string[];
  content?: string | null;
  content_digest?: string | null;
  base_content?: string | null;
  base_digest?: string | null;
  script_tests: Array<{
    test_id: string;
    args: string[];
    fixtures: Array<{ path: string; content: string }>;
    expected_exit_code: number;
    stdout_contains: string[];
    stderr_contains: string[];
  }>;
  script_receipt?: SkillResourceScriptReceipt | null;
  validation_issues: SkillPackageIssue[];
  feedback: string;
}

export interface SkillResourceBuild {
  build_id: string;
  session_id: string;
  revision: number;
  digest: string;
  state: SkillResourceBuildState;
  phase: "resources" | "skill_markdown" | "proposal";
  session_revision: number;
  plan_id: string;
  plan_revision: number;
  plan_digest: string;
  draft_id?: string | null;
  draft_revision?: number | null;
  draft_digest?: string | null;
  skill_name: string;
  skill_description: string;
  workflow_steps: SkillResourcePlanStep[];
  output_contract: string[];
  failure_modes: string[];
  resources: SkillResourceBuildItem[];
  current_resource_id?: string | null;
  skill_chunks: string[];
  skill_markdown?: string | null;
  skill_markdown_digest?: string | null;
  skill_attempt: number;
  skill_repair_count: number;
  skill_validation_issues: SkillPackageIssue[];
  skill_feedback: string;
  requirement_coverage: Array<Record<string, unknown>>;
  proposal_id?: string | null;
  stale?: boolean;
  created_at: number;
  updated_at: number;
}

export interface SkillCreatorSession {
  session_id: string;
  session_revision: number;
  draft_state_revision: number;
  mode: "blank" | "run";
  assistant_agent_id: "skill-creator-assistant-v1" | string;
  authoring_flow?: "legacy" | "resource";
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
  quality_mode?: SkillCreatorQualityMode;
  cases_revision?: number;
  evaluation_cases?: SkillEvaluationCase[];
  evaluation_repetitions?: number;
  active_evaluation_run_id?: string | null;
  latest_evaluation_run_id?: string | null;
  evaluation_run?: SkillEvaluationRun | null;
  baseline_content_revision?: number | null;
  baseline_content_digest?: string | null;
  review_state?: "none" | "pending" | "accepted" | "revise" | "waived" | null;
  review_revision?: number;
  review_feedback?: string | null;
  quality_status?: SkillQualityStatus;
  quality_run_id?: string | null;
  quality_reason?: string | null;
  quality_decision?: SkillCreatorQualityDecision | null;
  install_state?: "not_installed" | "current" | "outdated";
  installed_skill_id?: string | null;
  state: SkillCreatorSessionState;
  source_kind?: SkillCreatorSourceKind | null;
  source_task_id?: string | null;
  source_run_id?: string | null;
  source_xpert_id?: string | null;
  source_conversation_id?: string | null;
  source_message_id?: string | null;
  proposal?: SkillCreatorProposal | null;
  draft?: SkillCreatorDraft | null;
  resource_plan?: SkillResourcePlan | null;
  resource_build?: SkillResourceBuild | null;
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
  resource_authoring_enabled?: boolean;
  resource_authoring_version?: string | null;
  resource_planner_available?: boolean;
  resource_build_enabled?: boolean;
  resource_build_version?: string | null;
  resource_builder_available?: boolean;
  script_sandbox_configured?: boolean;
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
        cases?: SkillEvaluationCase[];
        cases_revision?: number;
        evaluation_run?: SkillEvaluationRun | null;
        resource_plan?: SkillResourcePlan | null;
        resource_build?: SkillResourceBuild | null;
      },
): SkillCreatorSession {
  if (!("session" in payload)) return payload;
  return {
    ...payload.session,
    draft: payload.draft ?? payload.session.draft,
    proposal: payload.proposal ?? payload.session.proposal,
    evaluation_cases: payload.cases ?? payload.session.evaluation_cases,
    cases_revision: payload.cases_revision ?? payload.session.cases_revision,
    evaluation_run: payload.evaluation_run ?? payload.session.evaluation_run,
    resource_plan: payload.resource_plan ?? payload.session.resource_plan,
    resource_build: payload.resource_build ?? payload.session.resource_build,
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
    quality_mode?: SkillCreatorQualityMode;
  },
) {
  return unwrapSession(await request<SkillCreatorSession | { session: SkillCreatorSession }>(
    `/api/skills/creator/sessions/${encodeURIComponent(sessionId)}`,
    jsonRequest("PATCH", payload),
  ));
}

function optimisticPayload(session: SkillCreatorSession, draft: SkillCreatorDraft) {
  return {
    expected_session_revision: session.session_revision,
    expected_revision: draft.revision,
    expected_digest: draft.content_digest,
  };
}

function unwrapEvaluationRun(
  payload: SkillEvaluationRun | { run: SkillEvaluationRun } | { evaluation_run: SkillEvaluationRun },
) {
  if ("run" in payload) return payload.run;
  if ("evaluation_run" in payload) return payload.evaluation_run;
  return payload;
}

function evaluationReviewRevision(run: SkillEvaluationRun, session?: SkillCreatorSession) {
  return run.feedback_revision ?? run.review_revision ?? session?.review_revision ?? 0;
}

export async function saveSkillCreatorEvaluationCases(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
  cases: SkillEvaluationCase[],
) {
  return unwrapSession(await request<SkillCreatorSession | {
    session: SkillCreatorSession;
    draft?: SkillCreatorDraft;
    cases?: SkillEvaluationCase[];
    cases_revision?: number;
  }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/cases`,
    jsonRequest("PUT", {
      ...optimisticPayload(session, draft),
      quality_mode: session.quality_mode ?? "objective",
      cases: cases.map((evaluationCase) => ({
        name: evaluationCase.name,
        prompt: evaluationCase.prompt,
        expected_behavior: evaluationCase.expected_behavior,
        fixtures: evaluationCase.fixtures,
        assertions: evaluationCase.assertions,
      })),
    }),
  ));
}

export async function startSkillCreatorEvaluation(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
  repetitions = 1,
) {
  const result = await request<
    SkillEvaluationRun |
    { run: SkillEvaluationRun; session?: SkillCreatorSession } |
    { evaluation_run: SkillEvaluationRun; session?: SkillCreatorSession }
  >(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/evaluations`,
    jsonRequest("POST", {
      ...optimisticPayload(session, draft),
      repetitions,
    }),
  );
  return {
    run: unwrapEvaluationRun(result),
    session: "session" in result ? result.session : undefined,
  };
}

export async function readSkillCreatorEvaluation(runId: string) {
  return unwrapEvaluationRun(await request<SkillEvaluationRun | { run: SkillEvaluationRun } | { evaluation_run: SkillEvaluationRun }>(
    `/api/skills/creator/evaluations/${encodeURIComponent(runId)}`,
  ));
}

export async function cancelSkillCreatorEvaluation(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
  run: SkillEvaluationRun,
) {
  const result = await request<SkillEvaluationRun | { run: SkillEvaluationRun; session?: SkillCreatorSession; draft?: SkillCreatorDraft } | { evaluation_run: SkillEvaluationRun; session?: SkillCreatorSession; draft?: SkillCreatorDraft }>(
    `/api/skills/creator/evaluations/${encodeURIComponent(run.run_id)}/cancel`,
    jsonRequest("POST", {
      ...optimisticPayload(session, draft),
      expected_run_revision: run.revision,
    }),
  );
  return {
    run: unwrapEvaluationRun(result),
    session: "session" in result ? result.session : undefined,
    draft: "draft" in result ? result.draft : undefined,
  };
}

export async function retrySkillCreatorEvaluation(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
  run: SkillEvaluationRun,
  caseIds?: string[],
) {
  const result = await request<SkillEvaluationRun | { run: SkillEvaluationRun; session?: SkillCreatorSession; draft?: SkillCreatorDraft } | { evaluation_run: SkillEvaluationRun; session?: SkillCreatorSession; draft?: SkillCreatorDraft }>(
    `/api/skills/creator/evaluations/${encodeURIComponent(run.run_id)}/retry`,
    jsonRequest("POST", {
      ...optimisticPayload(session, draft),
      expected_run_revision: run.revision,
      case_ids: caseIds?.length ? caseIds : undefined,
    }),
  );
  return {
    run: unwrapEvaluationRun(result),
    session: "session" in result ? result.session : undefined,
    draft: "draft" in result ? result.draft : undefined,
  };
}

export async function saveSkillCreatorEvaluationFeedback(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
  run: SkillEvaluationRun,
  feedback: string,
) {
  const result = await request<SkillEvaluationRun | { run: SkillEvaluationRun; session?: SkillCreatorSession; draft?: SkillCreatorDraft } | { evaluation_run: SkillEvaluationRun; session?: SkillCreatorSession; draft?: SkillCreatorDraft }>(
    `/api/skills/creator/evaluations/${encodeURIComponent(run.run_id)}/review`,
    jsonRequest("PATCH", {
      ...optimisticPayload(session, draft),
      expected_run_revision: run.revision,
      expected_review_revision: evaluationReviewRevision(run, session),
      feedback: feedback.trim(),
    }),
  );
  return {
    run: unwrapEvaluationRun(result),
    session: "session" in result ? result.session : undefined,
    draft: "draft" in result ? result.draft : undefined,
  };
}

export async function submitSkillCreatorEvaluationReview(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
  run: SkillEvaluationRun,
  decision: "accept" | "revise",
  payload: { feedback?: string; reason?: string; confirm_failed_assertions?: boolean },
) {
  const result = await request<
    SkillEvaluationRun |
    { run: SkillEvaluationRun; session?: SkillCreatorSession; draft?: SkillCreatorDraft } |
    { evaluation_run: SkillEvaluationRun; session?: SkillCreatorSession; draft?: SkillCreatorDraft }
  >(
    `/api/skills/creator/evaluations/${encodeURIComponent(run.run_id)}/review`,
    jsonRequest("POST", {
      ...optimisticPayload(session, draft),
      expected_run_revision: run.revision,
      expected_review_revision: evaluationReviewRevision(run, session),
      decision,
      reason: payload.reason?.trim() || undefined,
      acknowledge_failed_assertions: payload.confirm_failed_assertions ?? false,
    }),
  );
  return {
    run: unwrapEvaluationRun(result),
    session: "session" in result ? result.session : undefined,
    draft: "draft" in result ? result.draft : undefined,
  };
}

export async function iterateSkillCreatorDraft(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
  run: SkillEvaluationRun,
) {
  const result = await request<{
    session?: SkillCreatorSession;
    proposal: SkillCreatorProposal;
  }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/iterate`,
    jsonRequest("POST", {
      ...optimisticPayload(session, draft),
      evaluation_run_id: run.run_id,
      expected_review_revision: evaluationReviewRevision(run, session),
    }),
  );
  return {
    proposal: result.proposal,
    session: result.session ?? await readSkillCreatorSession(session.session_id),
  };
}

export async function waiveSkillCreatorEvaluation(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
  reason: string,
) {
  return unwrapSession(await request<SkillCreatorSession | { session: SkillCreatorSession; draft?: SkillCreatorDraft }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/waive-evaluation`,
    jsonRequest("POST", {
      ...optimisticPayload(session, draft),
      reason: reason.trim(),
      confirmed: true,
    }),
  ));
}

export async function installSkillCreatorDraft(draft: SkillCreatorDraft) {
  return request<{ draft: SkillCreatorDraft; installed: { skill_id?: string } }>(
    `/api/skills/drafts/${encodeURIComponent(draft.draft_id)}/install`,
    jsonRequest("POST", {
      expected_revision: draft.revision,
      expected_digest: draft.content_digest,
    }),
  );
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

export async function generateSkillCreatorResourcePlan(session: SkillCreatorSession) {
  const plan = session.resource_plan;
  return unwrapSession(await request<SkillCreatorSession | {
    session: SkillCreatorSession;
    resource_plan?: SkillResourcePlan | null;
  }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/resource-plan/generate`,
    jsonRequest("POST", {
      expected_session_revision: session.session_revision,
      expected_plan_revision: plan?.revision ?? null,
      expected_plan_digest: plan?.digest ?? null,
    }),
  ));
}

function resourcePlanWritePayload(session: SkillCreatorSession, plan: SkillResourcePlan) {
  return {
    plan_id: plan.plan_id,
    expected_session_revision: session.session_revision,
    expected_plan_revision: plan.revision,
    expected_plan_digest: plan.digest,
  };
}

export async function answerSkillCreatorResourcePlan(
  session: SkillCreatorSession,
  answers: Record<string, string>,
) {
  const plan = session.resource_plan;
  if (!plan) throw new Error("Resource plan is unavailable.");
  return unwrapSession(await request<SkillCreatorSession | {
    session: SkillCreatorSession;
    resource_plan?: SkillResourcePlan | null;
  }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/resource-plan/answers`,
    jsonRequest("PUT", { ...resourcePlanWritePayload(session, plan), answers }),
  ));
}

export async function patchSkillCreatorResourcePlan(
  session: SkillCreatorSession,
  changes: Partial<Pick<SkillResourcePlan,
    "skill_name" | "skill_description" | "workflow_steps" |
    "output_contract" | "failure_modes" | "resources">>,
) {
  const plan = session.resource_plan;
  if (!plan) throw new Error("Resource plan is unavailable.");
  const pathById = new Map(plan.resources.map((item) => [item.resource_id, item.path]));
  const normalized = changes.resources
    ? {
        ...changes,
        resources: changes.resources.map((item) => ({
          ...item,
          depends_on: item.depends_on.map((value) => pathById.get(value) ?? value),
        })),
      }
    : changes;
  return unwrapSession(await request<SkillCreatorSession | {
    session: SkillCreatorSession;
    resource_plan?: SkillResourcePlan | null;
  }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/resource-plan`,
    jsonRequest("PATCH", { ...resourcePlanWritePayload(session, plan), ...normalized }),
  ));
}

export async function confirmSkillCreatorResourcePlan(session: SkillCreatorSession) {
  const plan = session.resource_plan;
  if (!plan) throw new Error("Resource plan is unavailable.");
  return unwrapSession(await request<SkillCreatorSession | {
    session: SkillCreatorSession;
    resource_plan?: SkillResourcePlan | null;
  }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/resource-plan/confirm`,
    jsonRequest("POST", resourcePlanWritePayload(session, plan)),
  ));
}

function resourceBuildMutationPayload(
  session: SkillCreatorSession,
  build: SkillResourceBuild,
) {
  return {
    expected_session_revision: session.session_revision,
    expected_revision: build.revision,
    expected_digest: build.digest,
  };
}

function unwrapResourceBuild(
  payload: SkillResourceBuild | { resource_build: SkillResourceBuild },
) {
  return "resource_build" in payload ? payload.resource_build : payload;
}

export async function startSkillCreatorResourceBuild(session: SkillCreatorSession) {
  const plan = session.resource_plan;
  if (!plan) throw new Error("Resource plan is unavailable.");
  return unwrapResourceBuild(await request<{ resource_build: SkillResourceBuild }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/resource-build`,
    jsonRequest("POST", resourcePlanWritePayload(session, plan)),
  ));
}

export async function readSkillCreatorResourceBuild(buildId: string) {
  return unwrapResourceBuild(await request<{ resource_build: SkillResourceBuild }>(
    `/api/skills/creator/resource-builds/${encodeURIComponent(buildId)}`,
  ));
}

export async function advanceSkillCreatorResourceBuild(
  session: SkillCreatorSession,
  build: SkillResourceBuild,
) {
  return unwrapResourceBuild(await request<{ resource_build: SkillResourceBuild }>(
    `/api/skills/creator/resource-builds/${encodeURIComponent(build.build_id)}/next`,
    jsonRequest("POST", resourceBuildMutationPayload(session, build)),
  ));
}

export async function reviewSkillCreatorResource(
  session: SkillCreatorSession,
  build: SkillResourceBuild,
  resourceId: string,
  decision: "accept" | "revise",
  feedback = "",
) {
  return unwrapResourceBuild(await request<{ resource_build: SkillResourceBuild }>(
    `/api/skills/creator/resource-builds/${encodeURIComponent(build.build_id)}/resources/${encodeURIComponent(resourceId)}/review`,
    jsonRequest("POST", {
      ...resourceBuildMutationPayload(session, build),
      decision,
      feedback,
    }),
  ));
}

export async function editSkillCreatorResource(
  session: SkillCreatorSession,
  build: SkillResourceBuild,
  resourceId: string,
  content: string,
) {
  return unwrapResourceBuild(await request<{ resource_build: SkillResourceBuild }>(
    `/api/skills/creator/resource-builds/${encodeURIComponent(build.build_id)}/resources/${encodeURIComponent(resourceId)}`,
    jsonRequest("PUT", {
      ...resourceBuildMutationPayload(session, build),
      content,
    }),
  ));
}

export async function finalizeSkillCreatorResourceBuild(
  session: SkillCreatorSession,
  build: SkillResourceBuild,
  decision: "accept" | "revise",
  feedback = "",
) {
  const result = await request<{
    resource_build: SkillResourceBuild;
    proposal?: SkillCreatorProposal | null;
  }>(
    `/api/skills/creator/resource-builds/${encodeURIComponent(build.build_id)}/finalize`,
    jsonRequest("POST", {
      ...resourceBuildMutationPayload(session, build),
      decision,
      feedback,
    }),
  );
  return { build: result.resource_build, proposal: result.proposal ?? null };
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
