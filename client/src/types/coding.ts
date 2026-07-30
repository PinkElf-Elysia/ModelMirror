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
  host_apply?: false;
  reason?: "disabled" | "not_configured" | "worker_unavailable" | string;
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
