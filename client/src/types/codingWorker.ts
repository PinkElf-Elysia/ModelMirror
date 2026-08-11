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
  kind: "command" | "diff" | "artifact" | "custom";
  label: string;
  required: true;
}

export interface CodingWorkerAcceptanceContract {
  contract_id: string;
  required_checks: CodingWorkerAcceptanceCheck[];
  required_artifacts: Array<{
    artifact_id: string;
    media_type: string;
    label: string;
    required: true;
  }>;
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
  spec: Omit<CodingWorkerTaskSpec, "origin"> & {
    origin: { module: string; object_id: string };
  };
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

export interface CodingWorkerCapabilities {
  api_version: "v1";
  task_runtime: boolean;
  professional_file_tools: boolean;
  shell: boolean;
  operation_output: boolean;
  changesets: boolean;
  code_intelligence: boolean;
}

export interface CodingWorkerStatus {
  enabled: boolean;
  available: boolean;
  version: "v1";
  max_active_tasks: number;
  retention_seconds: number;
  network_enabled: boolean;
  acceptance_checks: string[];
  reason: string | null;
  capabilities: CodingWorkerCapabilities;
}

export interface CodingWorkerApproval {
  approval_id: string;
  task_id: string;
  operation_id: string;
  capability: string;
  status: "pending" | "approved" | "rejected" | "cancelled" | "expired";
  request: Record<string, unknown>;
  lease: {
    lease_id: string;
    expires_at: number;
    operation_limit: number;
  } | null;
  created_at: number;
  decided_at: number | null;
}

export interface CodingWorkerEvidence {
  evidence_id: string;
  task_id: string;
  check_id: string;
  operation_id: string;
  workspace_tree_hash: string;
  status: "passed" | "failed" | "invalidated";
  exit_code: number;
  artifact_id: string;
  created_at: number;
}

export interface CodingWorkerArtifact {
  artifact_id: string;
  task_id: string;
  media_type: string;
  sha256: string;
  size: number;
  metadata: Record<string, unknown>;
  created_at: number;
}

export interface CodingWorkerEntry {
  entry_id: string;
  name: string;
  display_path: string;
  kind: string;
  size: number;
  sha256: string | null;
}

export interface CodingWorkerOperationOutputChunk {
  task_id: string;
  operation_id: string;
  sequence: number;
  stream: "stdout" | "stderr" | "system";
  text: string;
  created_at: number;
  truncated: boolean;
}

export interface CodingWorkerChangesetEntry {
  entry_id: string;
  kind: "add" | "modify" | "delete" | "move";
  display_path: string;
  destination_display_path: string | null;
  preimage_sha256: string | null;
  postimage_sha256: string | null;
  binary: boolean;
}

export interface CodingWorkerChangeset {
  changeset_id: string;
  task_id: string;
  operation_id: string;
  base_tree_hash: string;
  result_tree_hash: string | null;
  state: "prepared" | "applied" | "conflict" | "rejected" | "unknown";
  entries: CodingWorkerChangesetEntry[];
  artifact_id: string | null;
  created_at: number;
  updated_at: number;
}

export interface CodingWorkerCodePosition {
  line: number;
  character: number;
}

export interface CodingWorkerCodeRange {
  start: CodingWorkerCodePosition;
  end: CodingWorkerCodePosition;
}

export interface CodingWorkerDiagnostic {
  diagnostic_id: string;
  task_id: string;
  entry_id: string;
  workspace_tree_hash: string;
  range: CodingWorkerCodeRange;
  severity: "error" | "warning" | "information" | "hint";
  code: string | null;
  message: string;
  created_at: number;
}

export interface CodingWorkerDiagnosticsSnapshot {
  task_id: string;
  operation_id: string;
  entry_id: string;
  language: "python" | "typescript" | "typescriptreact" | "javascript" | "javascriptreact";
  workspace_tree_hash: string;
  current_tree_hash: string;
  stale: boolean;
  diagnostics: CodingWorkerDiagnostic[];
}
