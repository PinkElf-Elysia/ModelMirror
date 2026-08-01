export type CodingEventType =
  | "session_started"
  | "turn_started"
  | "plan"
  | "answer_delta"
  | "tool_status"
  | "turn_completed"
  | "failed"
  | "cancelled"
  | "heartbeat";

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
  host_apply?: boolean;
  apply?: {
    allows_not_applicable: true;
    available: boolean;
    configured: boolean;
    reason?: string;
    requires_verification: true;
    supports_revert: true;
    target: "dedicated_worktree";
  };
  commit?: {
    available: boolean;
    configured: boolean;
    max_message_chars: 2000;
    reason?: string;
    remote_operations: false;
    requires_apply: true;
    supports_undo: true;
    target: "isolated_local_repository";
  };
  reason?: "disabled" | "not_configured" | "worker_unavailable" | string;
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
  status?: string;
  stop_reason?: string;
  text?: string;
  title?: string;
  tool_call_id?: string;
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
  status: string;
}

export interface CodingTurnResponse {
  accepted: boolean;
  status: string;
}

export interface CodingCancelResponse {
  accepted: boolean;
}

export type CodingDraftFileStatus = "added" | "modified";
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
  | "running"
  | "completed"
  | "cancelled";

export type CodingVerificationResult =
  | "not_run"
  | "passed"
  | "failed"
  | "not_applicable";

export interface CodingVerificationStep {
  details: string;
  duration_ms: number | null;
  id:
    | "backend_tests"
    | "backend_baseline_tests"
    | "backend_draft_tests"
    | "frontend_build";
  label: string;
  result: CodingVerificationResult;
  state: CodingVerificationState;
  summary: string;
  truncated: boolean;
}

export interface CodingVerification {
  finished_at: number | null;
  reason: string | null;
  result: CodingVerificationResult;
  revision: number;
  stale: boolean;
  started_at: number | null;
  state: CodingVerificationState;
  steps: CodingVerificationStep[];
}

export interface CodingVerificationCancelResponse
  extends CodingVerification {
  accepted: boolean;
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
