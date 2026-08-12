export interface AgencyExecutionCapabilities {
  enabled: boolean;
  worker_available: boolean;
  protocol: string;
  max_steps: number;
  max_concurrency: number;
  max_model_calls: number;
  max_tokens_per_call: number;
  timeout_seconds: number;
  supports_replay: boolean;
  supports_cancel: boolean;
  supports_restart_resume: boolean;
}

export interface AgencyPlannerCapabilities {
  enabled: boolean;
  worker_available: boolean;
  upstream_project: string;
  upstream_revision: string;
  supported_modes: Array<"auto" | "pinned">;
  max_agents: number;
  max_steps: number;
  execution?: AgencyExecutionCapabilities | null;
}

export interface AgencyPlanTask {
  task_id: string;
  title: string;
  objective: string;
  depends_on: string[];
  input_contract: string[];
  output_contract: string;
  agent_id?: string | null;
  acceptance: string;
}

export interface AgencyValidationIssue {
  code?: string;
  message?: string;
  severity?: string;
  node_id?: string;
}

export interface AgencyWorkflow {
  id: string;
  title: string;
  version?: string;
  source?: string;
  nodes: Array<{
    id: string;
    type?: string;
    position?: { x: number; y: number };
    data: Record<string, unknown>;
  }>;
  edges: Array<{
    id: string;
    source: string;
    target: string;
    sourceHandle?: string;
    targetHandle?: string;
  }>;
}

export interface AgencyAgentSummary {
  id: string;
  name: string;
  department: string;
  expertise: string;
  scenarios: string;
  emoji?: string;
  popularity?: number;
  score?: number;
}

export interface AgencyPlanPreview {
  plan: {
    summary: string;
    assumptions: string[];
    tasks: AgencyPlanTask[];
  };
  candidate: {
    name: string;
    description: string;
    draft: { workflow: AgencyWorkflow };
  };
  workflow: AgencyWorkflow;
  validation: {
    valid: boolean;
    issues?: AgencyValidationIssue[];
    stages?: Array<{
      id: string;
      valid: boolean;
      issues: AgencyValidationIssue[];
    }>;
  };
  selected_agents: AgencyAgentSummary[];
  baseline_matches: AgencyAgentSummary[];
  warnings: string[];
  repair_used: boolean;
  capability_snapshot_version: string;
  capability_snapshot_hash: string;
  upstream_project: string;
  upstream_revision: string;
}

export type AgencyDagStatus =
  | "running"
  | "waiting"
  | "ready"
  | "completed"
  | "failed"
  | "cancelled";

export interface AgencyDagVerification {
  pass: boolean;
  failed: string[];
  reworked: boolean;
}

export interface AgencyDagUsage {
  input_tokens?: number;
  output_tokens?: number;
}

export interface AgencyDagEvent {
  event: string;
  sequence: number;
  task_id?: string;
  agent_id?: string;
  depends_on?: string[];
  acceptance?: string;
  status?: string;
  output?: string;
  error?: string;
  message?: string;
  verification?: AgencyDagVerification;
  usage?: AgencyDagUsage;
  warnings?: string[];
  model_calls?: number;
  quality_status?: string;
  final_output?: string;
}

export interface AgencyDagRun {
  task_id: string;
  run_id: string;
  model_id?: string;
  goal?: string;
  team_name?: string;
  selected_agent_ids?: string[];
  status: AgencyDagStatus;
  sequence: number;
  events: AgencyDagEvent[];
  steps: AgencyDagEvent[];
  task_definitions?: Array<{
    task_id: string;
    title: string;
    objective: string;
    depends_on: string[];
    agent_id: string;
    acceptance: string;
  }>;
  final_output?: string | null;
  quality_status?: string | null;
  warnings: string[];
  model_calls: number;
  usage: AgencyDagUsage;
  estimated_cost?: number | null;
  error?: string | null;
  error_code?: string | null;
  created_at: number;
  updated_at: number;
  status_url?: string;
  events_url?: string;
  cancel_url?: string;
}

export interface AgencyDagStartPayload {
  goal: string;
  plan: AgencyPlanPreview["plan"];
  workflow: AgencyWorkflow;
  model_id: string;
  capability_snapshot_version: string;
  capability_snapshot_hash: string;
  upstream_revision: string;
}
