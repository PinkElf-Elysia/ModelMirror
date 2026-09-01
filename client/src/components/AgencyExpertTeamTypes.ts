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
  supports_retry: boolean;
  supports_restart_resume: boolean;
  revision: {
    enabled: boolean;
    supports_feedback: boolean;
    supports_intermediate_steps: boolean;
    max_feedback_chars: number;
    max_model_calls: number;
    budget_mode: "fresh";
  };
  hitl?: {
    enabled: boolean;
    protocol: string;
    supports_human_input: boolean;
    supports_approval: boolean;
    max_interactions: number;
    max_input_chars: number;
    wait_timeout_seconds: number;
    supports_reopen: boolean;
    supports_restart_wait: boolean;
    auto_insert_policy: "conservative";
  };
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
  method_skill_ids?: string[];
  task_type?: "expert" | "human_input" | "approval";
  interaction_prompt?: string;
  output_variable?: string | null;
}

export interface AgencyMethodSkill {
  skill_id: string;
  name: string;
  description: string;
  digest: string;
}

export interface AgencyTeamAsset {
  ref: string;
  kind: "team";
  name: string;
  description?: string;
  roles: Array<{
    role: string;
    name?: string;
    emoji?: string;
    note?: string;
  }>;
  created?: string;
  source?: string;
}

export interface AgencyTaskTemplate {
  ref: string;
  kind: "prompt";
  name: string;
  mode: "user" | "system";
  content: string;
  note?: string;
  version_count: number;
  created: string;
  updated: string;
}

export interface AgencyPromptGardenSeed {
  id: string;
  name: string;
  mode: "user" | "system";
  lang: "zh" | "en";
  tags: string[];
  content: string;
}

export interface AgencyAssets {
  teams: AgencyTeamAsset[];
  templates: AgencyTaskTemplate[];
  garden: AgencyPromptGardenSeed[];
  method_skills: AgencyMethodSkill[];
  upstream_project: string;
  upstream_revision: string;
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

export interface ProviderRouteCallReceipt {
  call_sequence: number;
  model_id: string;
  actual_model?: string | null;
  dispatched: boolean;
  status: "running" | "passed" | "failed" | "uncertain" | "cancelled";
  error_code?: string | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_tokens?: number | null;
}

export interface ProviderRouteReceipt {
  contract_version: "modelmirror-provider-workload-routing-v1";
  entry_id:
    | "expert_team_planner"
    | "expert_team_dag"
    | "fusion"
    | "route_agent"
    | "team_chat"
    | "chat_audio_input"
    | "chat_audio_output"
    | "audio_generation";
  routing_mode: "managed_required";
  run_reference: string;
  status: "running" | "passed" | "failed" | "uncertain" | "cancelled";
  call_count: number;
  reason_codes: string[];
  calls: ProviderRouteCallReceipt[];
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
  method_skill?: AgencyMethodSkill | null;
  warnings: string[];
  repair_used: boolean;
  model_calls: number;
  usage: AgencyDagUsage;
  provider_route_receipts?: ProviderRouteReceipt | null;
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
  | "cancelled"
  | "rejected";

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
  cumulative_usage?: AgencyDagUsage;
  reused?: boolean;
  revision_parent_task_id?: string;
  revision_target_task_id?: string;
  warnings?: string[];
  model_calls?: number;
  quality_status?: string;
  final_output?: string;
  provider_route_receipts?: ProviderRouteReceipt;
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
    method_skill_ids?: string[];
    task_type?: "expert" | "human_input" | "approval";
    interaction_prompt?: string;
    output_variable?: string;
  }>;
  final_output?: string | null;
  quality_status?: string | null;
  warnings: string[];
  model_calls: number;
  usage: AgencyDagUsage;
  provider_route_receipts?: ProviderRouteReceipt[];
  estimated_cost?: number | null;
  error?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  retryable?: boolean;
  revisable?: boolean;
  resumed_from_task_id?: string | null;
  revision?: {
    parent_task_id: string;
    root_task_id: string;
    revision_index: number;
    target_task_id: string;
    feedback?: string;
    feedback_preview?: string;
    affected_task_ids: string[];
  } | null;
  lineage_model_calls?: number;
  lineage_usage?: AgencyDagUsage;
  created_at: number;
  updated_at: number;
  status_url?: string;
  events_url?: string;
  cancel_url?: string;
  retry_url?: string;
  revise_url?: string;
  pending_interaction?: AgencyInteraction | null;
  interaction_history?: AgencyInteraction[];
}

export interface AgencyInteraction {
  approval_id: string;
  step_id: string;
  kind: "human_input" | "approval";
  prompt: string;
  content_preview?: string;
  allowed_decisions: Array<"replace" | "approve" | "reject">;
  revision: number;
  status: "pending" | "decided" | "expired" | "cancelled";
  decision?: "replace" | "approve" | "reject" | null;
  input?: string | null;
  message?: string | null;
  created_at: number;
  updated_at: number;
  expires_at: number;
}

export interface AgencyInteractionDecisionPayload {
  approval_id: string;
  revision: number;
  decision: "replace" | "approve" | "reject";
  replacement_text?: string;
  message?: string;
}

export interface AgencyDagRevisionPayload {
  target_task_id: string;
  feedback: string;
}

export interface AgencyDagStartPayload {
  goal: string;
  plan: AgencyPlanPreview["plan"];
  workflow: AgencyWorkflow;
  model_id: string;
  capability_snapshot_version: string;
  capability_snapshot_hash: string;
  upstream_revision: string;
  method_skill_digests: Record<string, string>;
}
