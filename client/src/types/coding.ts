export type CodingEventType =
  | "session_started"
  | "turn_started"
  | "plan"
  | "answer_delta"
  | "tool_status"
  | "command_requested"
  | "command_resolved"
  | "turn_completed"
  | "failed"
  | "cancelled"
  | "heartbeat";

export type CodingProjectKind = "builtin" | "local_clone" | "host_git";

export interface CodingProjectHostCapability {
  available: boolean;
  direct_writeback: false;
  enabled: boolean;
  paired: boolean;
  platform: "windows";
  reason?: string;
  remembers_projects: true;
  selection: true;
}

export interface CodingProjectHostStatus {
  available: boolean;
  host_id: string | null;
  name: string | null;
  paired: boolean;
  platform: "windows";
  version: string | null;
}

export interface CodingProjectHostPairing {
  expires_at: number;
  pairing_code: string;
  pairing_id: string;
  single_use: true;
}

export interface CodingProjectSelection {
  error: string | null;
  expires_at: number;
  project_id: string | null;
  request_id: string;
  status: "pending" | "dispatched" | "completed" | "failed" | "expired";
}

export interface CodingProjectFeatures {
  apply: boolean;
  chat: boolean;
  commit: boolean;
  diff: boolean;
  download: boolean;
  draft: boolean;
  publish: boolean;
  recovery: boolean;
  verification: boolean;
  commands?: boolean;
}

export interface CodingProjectSummary {
  branch: string | null;
  features: CodingProjectFeatures;
  head: string | null;
  id: string;
  kind: CodingProjectKind;
  name: string;
  reason: string | null;
  state: "available" | "unavailable";
  writeback_reason?: string | null;
}

export interface CodingProjectsStatus {
  available: boolean;
  configured: boolean;
  default_project_id: "modelmirror";
  enabled: boolean;
  max_projects: number;
  reason?: string;
  selection: true;
}

export interface CodingProjectsResponse extends CodingProjectsStatus {
  projects: CodingProjectSummary[];
}

export interface CodingCapabilities {
  available: boolean;
  enabled: boolean;
  limits: {
    max_concurrency: number;
    max_prompt_chars: number;
    session_ttl_seconds: number;
    max_changed_files?: number;
    max_file_bytes?: number;
    max_patch_bytes?: number;
  };
  mode: "readonly" | "draft";
  project_host?: CodingProjectHostCapability;
  projects: CodingProjectsStatus;
  project_writeback?: {
    available: boolean;
    configured: boolean;
    enabled: boolean;
    reason?: string;
    remote_operations: false;
    supports_commit: true;
    supports_delete: true;
    supports_move: true;
    supports_revert: true;
    target: "selected_local_repository";
  };
  host_apply?: boolean;
  apply?: {
    allows_not_applicable: true;
    allows_quality_risk_confirmation: true;
    available: boolean;
    configured: boolean;
    reason?: string;
    requires_verification: false;
    supports_revert: true;
    target: "dedicated_worktree" | "selected_local_repository";
  };
  commit?: {
    available: boolean;
    configured: boolean;
    max_message_chars: 2000;
    reason?: string;
    remote_operations: false;
    requires_apply: true;
    supports_undo: true;
    target: "isolated_local_repository" | "selected_local_repository";
  };
  commands: {
    available: boolean;
    confirmation: "always";
    enabled: boolean;
    execution: "isolated_copy";
    max_commands_per_turn: number;
    max_duration_seconds: number;
    network: false;
    persists_output: false;
    reason?: string;
  };
  incremental?: {
    available: boolean;
    commit_strategy: "linear";
    enabled: boolean;
    max_cycles: number;
    reason?: string;
    requires_recovery: true;
    undo_scope: "latest";
  };
  publish?: {
    available: boolean;
    configured: boolean;
    default_pr_state: "draft";
    enabled: boolean;
    provider: "github";
    reason?: string;
    remote_merge: false;
    requires_exact_base: true;
    supports_mark_ready: true;
    target: "fixed_repository";
  };
  reason?: "disabled" | "not_configured" | "worker_unavailable" | string;
  recovery: {
    available: boolean;
    enabled: boolean;
    pending: boolean;
    reason?: string;
    restores_conversation: false;
    retention_seconds: number;
  };
  verification: {
    available: boolean;
    max_duration_seconds: number;
    reason?: string;
    required_for_patch: false;
    strategy: "adaptive";
  };
  workspace: string;
}

export interface CodingPlanEntry {
  content: string;
  priority: string;
  status: string;
}

export interface CodingEventData {
  code?: string;
  entries?: CodingPlanEntry[];
  kind?: string;
  project?: CodingProjectSummary;
  status?: string;
  state?: string;
  stop_reason?: string;
  text?: string;
  title?: string;
  tool_call_id?: string;
  request_id?: string;
  command?: CodingProjectCommand;
  expires_at?: number | null;
  result?: CodingCommandResult | null;
}

export interface CodingEvent {
  created_at: number;
  data: CodingEventData;
  seq: number;
  session_id: string;
  turn_id: string | null;
  type: CodingEventType;
}

export interface CodingSessionResponse {
  id: string;
  project: CodingProjectSummary;
  status: string;
}

export type CodingRecoveryState =
  | "draft"
  | "applied"
  | "reverted"
  | "committed"
  | "undone"
  | "conflict";

export interface CodingRecoveryStatus {
  available: boolean;
  can_download?: boolean;
  can_resume?: boolean;
  enabled: boolean;
  expires_at?: number;
  file_count?: number;
  pending: boolean;
  project?: CodingProjectSummary | null;
  reason?: string | null;
  restores_conversation: false;
  retention_seconds: number;
  revision?: number;
  state?: CodingRecoveryState;
  updated_at?: number;
}

export interface CodingRecoveryResumeResponse {
  conflict: string | null;
  conversation_restored: false;
  id: string;
  project: CodingProjectSummary;
  status: string;
}

export interface CodingTurnResponse {
  accepted: boolean;
  status: string;
}

export interface CodingCancelResponse {
  accepted: boolean;
}

export type CodingDraftFileStatus = "added" | "modified" | "deleted";
export type CodingDraftCheckStatus = "passed" | "failed";

export interface CodingDraftFile {
  additions: number;
  deletions: number;
  path: string;
  status: CodingDraftFileStatus;
}

export interface CodingDraftCheck {
  id: string;
  label: string;
  message: string;
  status: CodingDraftCheckStatus;
}

export interface CodingDraftChanges {
  additions: number;
  can_download: boolean;
  checks: CodingDraftCheck[];
  deletions: number;
  file_count: number;
  files: CodingDraftFile[];
  patch_bytes: number;
  revision: number;
  validation_status: CodingDraftCheckStatus;
}

export interface CodingPatchDownload {
  blob: Blob;
  filename: string;
}

export type CodingVerificationState =
  | "not_started"
  | "awaiting_confirmation"
  | "running"
  | "completed"
  | "cancelled";

export type CodingVerificationResult =
  | "not_run"
  | "passed"
  | "failed"
  | "not_applicable";

export interface CodingVerificationStep {
  command?: CodingProjectCommand | null;
  details: string;
  duration_ms: number | null;
  id: string;
  label: string;
  result: CodingVerificationResult;
  state: CodingVerificationState;
  summary: string;
  truncated: boolean;
}

export interface CodingVerification {
  confirmation_id?: string | null;
  finished_at: number | null;
  reason: string | null;
  result: CodingVerificationResult;
  revision: number;
  stale: boolean;
  started_at: number | null;
  state: CodingVerificationState;
  steps: CodingVerificationStep[];
  plan_fingerprint?: string | null;
}

export interface CodingVerificationCancelResponse
  extends CodingVerification {
  accepted: boolean;
}

export interface CodingProjectCommand {
  argv: string[];
  cwd: string;
  id: string;
  kind: "test" | "build" | "lint" | "typecheck" | "custom";
  name: string;
  timeout_seconds: number;
}

export interface CodingCommandResult {
  duration_seconds: number;
  exit_code: number | null;
  output: string;
  status: string;
}

export interface CodingCommandRequest {
  command: CodingProjectCommand;
  created_at: number | null;
  expires_at: number | null;
  request_id: string;
  result: CodingCommandResult | null;
  state:
    | "awaiting_confirmation"
    | "running"
    | "completed"
    | "rejected"
    | "timed_out"
    | "cancelled"
    | "failed";
}

export type CodingApplyState =
  | "not_applied"
  | "applying"
  | "applied"
  | "reverting"
  | "reverted"
  | "failed";

export interface CodingApplyResult {
  applied_at: number | null;
  apply_id: string | null;
  can_revert: boolean;
  file_count: number;
  finished_at: number | null;
  reason: string | null;
  revision: number;
  started_at: number | null;
  state: CodingApplyState;
}

export type CodingCommitState =
  | "not_committed"
  | "committing"
  | "committed"
  | "undoing"
  | "undone"
  | "failed";

export interface CodingCommitResult {
  branch: "coding/local-draft";
  can_undo: boolean;
  commit_id: string | null;
  commit_sha: string | null;
  committed_at: number | null;
  file_count: number;
  finished_at: number | null;
  message: string | null;
  reason: string | null;
  revision: number;
  short_sha: string | null;
  started_at: number | null;
  state: CodingCommitState;
  suggested_message: string;
}

export interface CodingCycleSummary {
  additions: number;
  can_undo: boolean;
  commit_sha: string | null;
  created_at: number;
  deletions: number;
  file_count: number;
  message: string | null;
  number: number;
  revision: number;
  short_sha: string | null;
  state: "committed" | "undone" | "reverted" | "conflict";
  updated_at: number;
  verification_result: CodingVerificationResult;
}

export interface CodingCycleHistory {
  active_cycle: number;
  can_continue: boolean;
  completed_count: number;
  current_commit: CodingCommitResult | null;
  cycles: CodingCycleSummary[];
  max_cycles: number;
}

export type CodingPublishState =
  | "not_published"
  | "publishing"
  | "draft"
  | "marking_ready"
  | "ready"
  | "failed"
  | "conflict";

export interface CodingPublishResult {
  body: string;
  can_mark_ready: boolean;
  commit_count: number;
  file_count: number;
  finished_at: number | null;
  pr_number: number | null;
  pr_url: string | null;
  publish_id: string | null;
  reason: string | null;
  revision: number;
  started_at: number | null;
  state: CodingPublishState;
  title: string;
}
