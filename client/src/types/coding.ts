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
  };
  mode: "readonly";
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
