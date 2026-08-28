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

export type SkillEvaluationSuiteRole = "normal" | "ambiguous" | "boundary" | "regression";

export interface SkillEvaluationSuiteCase extends SkillEvaluationCase {
  role: SkillEvaluationSuiteRole;
  source: "generated" | "migrated" | "user" | "run_experience";
  requirement_ids: string[];
  required_resource_paths: string[];
  workflow_step_ids: string[];
  case_fingerprint?: string;
}

export interface SkillEvaluationSuite {
  suite_id: string;
  version: string;
  suite_revision: number;
  suite_digest: string;
  session_id: string;
  draft_id: string;
  draft_revision: number;
  draft_digest: string;
  quality_mode: SkillCreatorQualityMode;
  state: "draft" | "confirmed";
  cases: SkillEvaluationSuiteCase[];
  change_reason?: string;
  based_on_revision?: number | null;
  stale?: boolean;
  created_at?: number;
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
  target: "baseline" | "previous" | "candidate";
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
  acknowledged_regression_item_ids?: string[];
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
  previous_usage?: SkillEvaluationUsage | null;
  previous_latency_ms?: number | null;
  candidate_assertion_failed_count?: number;
  eligible_for_accept?: boolean;
  application_receipt_verified_count?: number;
  model_mismatch_count?: number;
  regression_item_ids?: string[];
  comparison_reference?: "baseline" | "previous";
  comparison_counts?: Record<"regressed" | "improved" | "flat" | "inconclusive", number>;
  pairs?: Array<{
    pair_id: string;
    case_id: string;
    repetition: number;
    baseline_item_id?: string | null;
    previous_item_id?: string | null;
    candidate_item_id?: string | null;
    comparable?: boolean;
    classification: "regressed" | "improved" | "flat" | "inconclusive";
    new_failure_indices?: number[];
    fixed_failure_indices?: number[];
  }>;
}

export interface SkillEvaluationRun {
  run_id: string;
  session_id: string;
  status: SkillEvaluationRunStatus;
  revision: number;
  frozen_digest: string;
  baseline_overlay_id?: string | null;
  previous_overlay_id?: string | null;
  candidate_overlay_id?: string | null;
  model_id: string;
  repetitions: number;
  case_set_revision?: number | null;
  evaluation_suite_id?: string | null;
  evaluation_suite_revision?: number | null;
  evaluation_suite_digest?: string | null;
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

export interface SkillEvolutionQuestion {
  question_id: string;
  question: string;
  reason: string;
}

export interface SkillEvolutionDiagnosis {
  case_id: string;
  evidence_item_ids: string[];
  failure_types: string[];
  requirement_ids: string[];
  resource_ids: string[];
  sections: string[];
  summary: string;
}

export interface SkillEvolutionAction {
  action_id: string;
  action: "keep" | "update" | "create" | "delete";
  resource_id: string;
  kind: "script" | "reference" | "asset";
  path: string;
  purpose: string;
  source_ids: string[];
  used_by_steps: string[];
  depends_on: string[];
  acceptance_checks: string[];
  related_case_ids: string[];
  expected_improvement: string;
  non_regression_case_ids: string[];
}

export interface SkillEvolutionPlan {
  plan_id: string;
  version: string;
  revision: number;
  digest: string;
  state: "needs_input" | "needs_regeneration" | "ready" | "confirmed" | "stale";
  session_id: string;
  draft_id: string;
  draft_revision: number;
  draft_digest: string;
  evaluation_run_id: string;
  evaluation_run_revision: number;
  review_revision: number;
  suite_id: string;
  suite_revision: number;
  suite_digest: string;
  diagnoses: SkillEvolutionDiagnosis[];
  actions: SkillEvolutionAction[];
  expected_improvements: string[];
  acceptance_criteria: string[];
  non_goals: string[];
  overfitting_risks: string[];
  clarifications: SkillEvolutionQuestion[];
  clarification_answers: Record<string, string>;
  created_at?: number;
  updated_at?: number;
}

export interface SkillUnavailableProjection {
  available: false;
  code: string;
}

export interface SkillRegressionGovernance {
  version: string;
  enabled: boolean;
  max_items: number;
  case_count: number;
  target_count: 2 | 3;
  estimated_model_calls: number;
  max_repetitions: number;
  previous_revision?: number | null;
  previous_digest?: string | null;
  evolution_history_available?: boolean;
  revisions: Array<{
    revision: number;
    content_digest: string;
    source_proposal_id?: string | null;
    created_at?: number;
    is_current: boolean;
    is_installed: boolean;
    is_previous: boolean;
  }>;
  runs: Array<{
    run_id: string;
    draft_revision: number;
    frozen_digest: string;
    status: SkillEvaluationRunStatus;
    review_state?: string | null;
    suite_revision?: number | null;
    target_count: 2 | 3;
    item_count: number;
    comparison_counts?: Record<string, number>;
    created_at?: number;
    completed_at?: number | null;
  }>;
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

export interface SkillResourceHookPlanItem {
  hook_id: string;
  spec_digest: string;
  event: "session_start" | "pre_tool_use" | "post_tool_use" | "session_end";
  mode: "annotation" | "validation" | "guard";
  tool_names: string[];
  purpose: string;
  script_resource_id: string;
  source_ids: string[];
  used_by_steps: string[];
  acceptance_checks: string[];
  action: "keep" | "create" | "update" | "delete";
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
  /** Absent on read-only sessions created before Hook V2. */
  hooks?: SkillResourceHookPlanItem[];
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

export interface SkillHookScriptReceipt {
  receipt_id: string;
  hook_id: string;
  hook_spec_digest: string;
  script_digest: string;
  manifest_digest: string;
  profile: string;
  passed: boolean;
  results: Array<{
    case_id: string;
    passed: boolean;
    result_types: string[];
    result_digest: string;
    duration_ms: number;
    issues: string[];
  }>;
  created_at: number;
}

export interface SkillResourceBuildHook extends SkillResourceHookPlanItem {
  test_receipt?: SkillHookScriptReceipt | null;
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
  /** Absent on read-only builds created before Hook V2. */
  hooks?: SkillResourceBuildHook[];
  hook_manifest?: string | null;
  hook_manifest_digest?: string | null;
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

export type SkillTriggerCaseKind = "should_trigger" | "should_not_trigger" | "exact_name_smoke";

export interface SkillTriggerCase {
  case_id: string;
  kind: SkillTriggerCaseKind;
  text: string;
  source: "model" | "user" | "migrated";
  case_hash: string;
}

export interface SkillTriggerSuite {
  suite_id: string;
  version: string;
  suite_revision: number;
  suite_digest: string;
  session_id: string;
  session_revision: number;
  definition_digest: string;
  skill_name: string;
  state: "draft" | "confirmed";
  cases: SkillTriggerCase[];
  change_reason: string;
  based_on_revision?: number | null;
  confirmed_actor_id?: string | null;
  confirmed_at?: number | null;
  created_at: number;
}

export interface SkillTriggerMatchReason {
  reason_type: string;
  origin: string;
  matched_terms: string[];
}

export interface SkillTriggerCompetitor {
  candidate_id: string;
  candidate_fingerprint: string;
  rank: number;
}

export interface SkillTriggerDomainResult {
  rank_top_6?: number | null;
  rank_top_24?: number | null;
  in_top_6: boolean;
  in_top_24: boolean;
  score?: number | null;
  reasons: SkillTriggerMatchReason[];
  competitors: SkillTriggerCompetitor[];
}

export interface SkillTriggerCaseResult {
  case_id: string;
  case_hash: string;
  kind: SkillTriggerCaseKind;
  finder: SkillTriggerDomainResult;
  router: SkillTriggerDomainResult;
  passed: boolean;
}

export interface SkillTriggerReceipt {
  receipt_id: string;
  version: string;
  suite_id: string;
  suite_revision: number;
  suite_digest: string;
  session_id: string;
  skill_name: string;
  description_digest: string;
  ranker_version: string;
  runtime_index_fingerprint: string;
  directory_fingerprint: string;
  trust_index_fingerprint: string;
  candidate_fingerprint: string;
  candidate_set_fingerprint: string;
  passed: boolean;
  case_results: SkillTriggerCaseResult[];
  created_at: number;
}

export interface SkillTriggerDescriptionCandidate {
  description: string;
  description_digest: string;
  receipt_id: string;
  passed: boolean;
  worst_positive_rank: number;
  positive_rank_sum: number;
  negative_safety_distance: number;
}

export interface SkillTriggerDescriptionAttempt {
  attempt_id: string;
  version: string;
  revision: number;
  digest: string;
  session_id: string;
  session_revision: number;
  plan_id: string;
  plan_revision: number;
  plan_digest: string;
  suite_id: string;
  suite_revision: number;
  suite_digest: string;
  state: "evaluated" | "confirmed";
  candidates: SkillTriggerDescriptionCandidate[];
  recommended_description_digest?: string | null;
  selected_description_digest?: string | null;
  created_at: number;
  confirmed_at?: number | null;
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
  evaluation_suite?: SkillEvaluationSuite | null;
  evolution_plan?: SkillEvolutionPlan | SkillUnavailableProjection | null;
  regression_governance?: SkillRegressionGovernance | null;
  trigger_required?: boolean;
  trigger_suite?: SkillTriggerSuite | null;
  trigger_attempt?: SkillTriggerDescriptionAttempt | null;
  trigger_receipt?: SkillTriggerReceipt | null;
  trigger_stale_reason?: string | null;
  experience_candidate_id?: string | null;
  experience_decision?: "create" | "update" | null;
  update_target_skill_id?: string | null;
  predecessor_draft_id?: string | null;
  experience_baseline_version_id?: string | null;
  experience_baseline_content_digest?: string | null;
  run_experience_case?: SkillEvaluationSuiteCase | null;
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
  evaluation_suite_enabled?: boolean;
  evaluation_suite_version?: string | null;
  evaluation_suite_generator_available?: boolean;
  evaluation_suite_store_available?: boolean;
  evolution_enabled?: boolean;
  evolution_version?: string | null;
  evolution_planner_available?: boolean;
  hook_authoring_enabled?: boolean;
  hook_manifest_version?: string | null;
  hook_result_version?: string | null;
  hook_runtimes?: string[];
  trigger_optimization_enabled?: boolean;
  trigger_optimization_version?: string | null;
  trigger_optimizer_available?: boolean;
  trigger_store_available?: boolean;
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
        evaluation_suite?: SkillEvaluationSuite | null;
        evolution_plan?: SkillEvolutionPlan | SkillUnavailableProjection | null;
        regression_governance?: SkillRegressionGovernance | null;
        trigger_required?: boolean;
        trigger_suite?: SkillTriggerSuite | null;
        trigger_attempt?: SkillTriggerDescriptionAttempt | null;
        trigger_receipt?: SkillTriggerReceipt | null;
        trigger_stale_reason?: string | null;
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
    evaluation_suite: payload.evaluation_suite ?? payload.session.evaluation_suite,
    evolution_plan: payload.evolution_plan ?? payload.session.evolution_plan,
    regression_governance: payload.regression_governance ?? payload.session.regression_governance,
    trigger_required: payload.trigger_required ?? payload.session.trigger_required,
    trigger_suite: payload.trigger_suite ?? payload.session.trigger_suite,
    trigger_attempt: payload.trigger_attempt ?? payload.session.trigger_attempt,
    trigger_receipt: payload.trigger_receipt ?? payload.session.trigger_receipt,
    trigger_stale_reason: payload.trigger_stale_reason ?? payload.session.trigger_stale_reason,
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

function suiteOptimisticPayload(session: SkillCreatorSession, draft: SkillCreatorDraft) {
  return {
    expected_session_revision: session.session_revision,
    expected_draft_state_revision: session.draft_state_revision,
    expected_draft_revision: draft.content_revision,
    expected_draft_digest: draft.content_digest,
  };
}

function suiteCasePayload(evaluationCase: SkillEvaluationSuiteCase) {
  return {
    case_id: evaluationCase.case_id,
    role: evaluationCase.role,
    name: evaluationCase.name,
    prompt: evaluationCase.prompt,
    expected_behavior: evaluationCase.expected_behavior,
    fixtures: evaluationCase.fixtures,
    assertions: evaluationCase.assertions,
    requirement_ids: evaluationCase.requirement_ids,
    required_resource_paths: evaluationCase.required_resource_paths,
    workflow_step_ids: evaluationCase.workflow_step_ids,
  };
}

export async function generateSkillCreatorEvaluationSuite(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
) {
  const suite = session.evaluation_suite;
  return unwrapSession(await request<SkillCreatorSession | {
    session: SkillCreatorSession;
    draft?: SkillCreatorDraft;
    evaluation_suite?: SkillEvaluationSuite | null;
  }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/evaluation-suite/generate`,
    jsonRequest("POST", {
      ...suiteOptimisticPayload(session, draft),
      expected_suite_revision: suite?.suite_revision,
      expected_suite_digest: suite?.suite_digest,
    }),
  ));
}

export async function updateSkillCreatorEvaluationSuite(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
  cases: SkillEvaluationSuiteCase[],
  changeReason: string,
) {
  const suite = session.evaluation_suite;
  if (!suite) throw new Error("Evaluation suite is not available.");
  return unwrapSession(await request<SkillCreatorSession | {
    session: SkillCreatorSession;
    draft?: SkillCreatorDraft;
    evaluation_suite?: SkillEvaluationSuite | null;
  }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/evaluation-suite`,
    jsonRequest("PATCH", {
      ...suiteOptimisticPayload(session, draft),
      suite_id: suite.suite_id,
      expected_suite_revision: suite.suite_revision,
      expected_suite_digest: suite.suite_digest,
      cases: cases.map(suiteCasePayload),
      change_reason: changeReason.trim(),
    }),
  ));
}

export async function confirmSkillCreatorEvaluationSuite(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
) {
  const suite = session.evaluation_suite;
  if (!suite) throw new Error("Evaluation suite is not available.");
  return unwrapSession(await request<SkillCreatorSession | {
    session: SkillCreatorSession;
    draft?: SkillCreatorDraft;
    evaluation_suite?: SkillEvaluationSuite | null;
  }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/evaluation-suite/confirm`,
    jsonRequest("POST", {
      ...suiteOptimisticPayload(session, draft),
      suite_id: suite.suite_id,
      expected_suite_revision: suite.suite_revision,
      expected_suite_digest: suite.suite_digest,
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
      evaluation_suite_revision: session.evaluation_suite?.state === "confirmed"
        ? session.evaluation_suite.suite_revision
        : undefined,
      evaluation_suite_digest: session.evaluation_suite?.state === "confirmed"
        ? session.evaluation_suite.suite_digest
        : undefined,
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
  payload: {
    feedback?: string;
    reason?: string;
    confirm_failed_assertions?: boolean;
    acknowledged_regression_item_ids?: string[];
  },
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
      acknowledged_regression_item_ids: payload.acknowledged_regression_item_ids ?? [],
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

function evolutionOptimisticPayload(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
  plan: SkillEvolutionPlan,
) {
  return {
    plan_id: plan.plan_id,
    expected_session_revision: session.session_revision,
    expected_draft_state_revision: session.draft_state_revision,
    expected_draft_revision: draft.content_revision,
    expected_draft_digest: draft.content_digest,
    expected_plan_revision: plan.revision,
    expected_plan_digest: plan.digest,
  };
}

export async function generateSkillCreatorEvolutionPlan(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
  run: SkillEvaluationRun,
) {
  const resourcePlan = session.resource_plan;
  if (!resourcePlan) throw new Error("Resource plan is not available.");
  const evolution = session.evolution_plan && "plan_id" in session.evolution_plan
    ? session.evolution_plan
    : null;
  const result = await request<{ evolution_plan: SkillEvolutionPlan }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/evolution-plan/generate`,
    jsonRequest("POST", {
      evaluation_run_id: run.run_id,
      expected_session_revision: session.session_revision,
      expected_draft_state_revision: session.draft_state_revision,
      expected_draft_revision: draft.content_revision,
      expected_draft_digest: draft.content_digest,
      expected_review_revision:
        run.reviews?.at(-1)?.review_revision ?? run.review_revision ?? session.review_revision ?? 0,
      expected_run_revision: run.revision,
      expected_resource_plan_revision: resourcePlan.revision,
      expected_resource_plan_digest: resourcePlan.digest,
      expected_evolution_revision: evolution?.revision,
      expected_evolution_digest: evolution?.digest,
    }),
  );
  return result.evolution_plan;
}

export async function answerSkillCreatorEvolutionPlan(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
  plan: SkillEvolutionPlan,
  answers: Record<string, string>,
) {
  const result = await request<{ evolution_plan: SkillEvolutionPlan }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/evolution-plan/answers`,
    jsonRequest("PUT", {
      ...evolutionOptimisticPayload(session, draft, plan),
      answers,
    }),
  );
  return result.evolution_plan;
}

export async function updateSkillCreatorEvolutionPlan(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
  plan: SkillEvolutionPlan,
  changes: Record<string, unknown>,
) {
  const result = await request<{ evolution_plan: SkillEvolutionPlan }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/evolution-plan`,
    jsonRequest("PATCH", {
      ...evolutionOptimisticPayload(session, draft, plan),
      changes,
    }),
  );
  return result.evolution_plan;
}

export async function confirmSkillCreatorEvolutionPlan(
  session: SkillCreatorSession,
  draft: SkillCreatorDraft,
  plan: SkillEvolutionPlan,
) {
  return request<{ evolution_plan: SkillEvolutionPlan; resource_plan: SkillResourcePlan }>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/evolution-plan/confirm`,
    jsonRequest("POST", evolutionOptimisticPayload(session, draft, plan)),
  );
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

function triggerSessionResponse(payload: SkillCreatorSession | {
  session: SkillCreatorSession;
  resource_plan?: SkillResourcePlan | null;
  trigger_required?: boolean;
  trigger_suite?: SkillTriggerSuite | null;
  trigger_attempt?: SkillTriggerDescriptionAttempt | null;
  trigger_receipt?: SkillTriggerReceipt | null;
  trigger_stale_reason?: string | null;
}) {
  return unwrapSession(payload);
}

function triggerSuitePayload(session: SkillCreatorSession) {
  const plan = session.resource_plan;
  if (!plan) throw new Error("Resource plan is unavailable.");
  const suite = session.trigger_suite;
  return {
    ...resourcePlanWritePayload(session, plan),
    expected_suite_revision: suite?.suite_revision ?? null,
    expected_suite_digest: suite?.suite_digest ?? null,
  };
}

function confirmedTriggerSuitePayload(session: SkillCreatorSession) {
  const suite = session.trigger_suite;
  if (!suite || suite.state !== "confirmed") {
    throw new Error("Trigger suite is not confirmed.");
  }
  return {
    ...triggerSuitePayload(session),
    suite_id: suite.suite_id,
    expected_suite_revision: suite.suite_revision,
    expected_suite_digest: suite.suite_digest,
  };
}

export async function generateSkillCreatorTriggerSuite(session: SkillCreatorSession) {
  return triggerSessionResponse(await request<Parameters<typeof triggerSessionResponse>[0]>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/trigger-suite/generate`,
    jsonRequest("POST", triggerSuitePayload(session)),
  ));
}

export async function saveSkillCreatorTriggerSuite(
  session: SkillCreatorSession,
  cases: Array<Pick<SkillTriggerCase, "kind" | "text">>,
  changeReason: string,
) {
  return triggerSessionResponse(await request<Parameters<typeof triggerSessionResponse>[0]>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/trigger-suite`,
    jsonRequest("PATCH", {
      ...triggerSuitePayload(session),
      cases: cases.map((item) => ({ kind: item.kind, text: item.text.trim() })),
      change_reason: changeReason.trim(),
    }),
  ));
}

export async function confirmSkillCreatorTriggerSuite(session: SkillCreatorSession) {
  const suite = session.trigger_suite;
  if (!suite) throw new Error("Trigger suite is unavailable.");
  return triggerSessionResponse(await request<Parameters<typeof triggerSessionResponse>[0]>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/trigger-suite/confirm`,
    jsonRequest("POST", {
      ...triggerSuitePayload(session),
      suite_id: suite.suite_id,
      expected_suite_revision: suite.suite_revision,
      expected_suite_digest: suite.suite_digest,
    }),
  ));
}

export async function optimizeSkillCreatorTriggerDescriptions(session: SkillCreatorSession) {
  return triggerSessionResponse(await request<Parameters<typeof triggerSessionResponse>[0]>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/trigger-descriptions/optimize`,
    jsonRequest("POST", confirmedTriggerSuitePayload(session)),
  ));
}

export async function evaluateSkillCreatorTriggerDescription(
  session: SkillCreatorSession,
  description: string,
) {
  return triggerSessionResponse(await request<Parameters<typeof triggerSessionResponse>[0]>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/trigger-descriptions/evaluate`,
    jsonRequest("POST", {
      ...confirmedTriggerSuitePayload(session),
      description: description.trim(),
    }),
  ));
}

export async function confirmSkillCreatorTriggerDescription(
  session: SkillCreatorSession,
  descriptionDigest: string,
) {
  const attempt = session.trigger_attempt;
  if (!attempt) throw new Error("Trigger description attempt is unavailable.");
  return triggerSessionResponse(await request<Parameters<typeof triggerSessionResponse>[0]>(
    `/api/skills/creator/sessions/${encodeURIComponent(session.session_id)}/trigger-descriptions/${encodeURIComponent(attempt.attempt_id)}/confirm`,
    jsonRequest("POST", {
      ...confirmedTriggerSuitePayload(session),
      expected_attempt_revision: attempt.revision,
      expected_attempt_digest: attempt.digest,
      selected_description_digest: descriptionDigest,
    }),
  ));
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
    "output_contract" | "failure_modes" | "resources" | "hooks">>,
) {
  const plan = session.resource_plan;
  if (!plan) throw new Error("Resource plan is unavailable.");
  const pathById = new Map(
    (changes.resources ?? plan.resources).map((item) => [item.resource_id, item.path]),
  );
  const normalized = {
    ...changes,
    ...(changes.resources
      ? {
          resources: changes.resources.map((item) => ({
            ...item,
            depends_on: item.depends_on.map((value) => pathById.get(value) ?? value),
          })),
        }
      : {}),
    ...(changes.hooks
      ? {
          hooks: changes.hooks.map((item) => ({
            ...item,
            script_path: pathById.get(item.script_resource_id),
          })),
        }
      : {}),
  };
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
