export type CodingWorkerTaskState =
  | "queued"
  | "preparing"
  | "running"
  | "waiting_approval"
  | "paused"
  | "testing"
  | "interrupted"
  | "completed"
  | "blocked"
  | "failed"
  | "cancelled"
  | "budget_limited"
  | "expired";

export type CodingWorkerPolicyProfile = "inspect" | "develop" | "develop_networked";

export interface CodingWorkerSource {
  kind: "builtin" | "manifest" | "host_snapshot";
  source_id: string;
  revision: string;
}

export interface CodingWorkerAcceptanceCheck {
  check_id: string;
  kind: "command" | "artifact" | "policy";
  label: string;
}

export interface CodingWorkerAcceptanceContract {
  contract_id: string;
  required_checks: CodingWorkerAcceptanceCheck[];
  required_artifacts: Array<{ artifact_id: string; label: string }>;
}

export interface CodingWorkerTaskSpec {
  client_task_id: string;
  objective: string;
  workspace_source: CodingWorkerSource;
  acceptance: CodingWorkerAcceptanceContract;
  policy_profile: CodingWorkerPolicyProfile;
  model_route: string;
  budget: {
    max_seconds: number;
    max_turns: number;
    max_tool_calls: number;
    max_output_bytes: number;
  };
  context_refs: Array<{ ref_id: string; kind: "artifact" | "resource" | "file" | "image" }>;
  origin?: never;
}

export interface CodingWorkerTask {
  task_id: string;
  spec: CodingWorkerTaskSpec & { origin: { module: string; object_id: string } };
  state: CodingWorkerTaskState;
  workspace_id: string | null;
  created_at: number;
  updated_at: number;
  expires_at: number;
  pinned: boolean;
  last_event_sequence: number;
  reason: string | null;
}

export interface CodingWorkerEvent {
  sequence: number;
  task_id: string;
  type: string;
  payload: Record<string, unknown>;
  created_at: number;
}

export interface CodingWorkerStatus {
  enabled: boolean;
  available: boolean;
  version: "v1";
  max_active_tasks: number;
  retention_seconds: number;
  network_enabled: boolean;
  reason: string | null;
}
