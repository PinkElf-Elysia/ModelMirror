export type SkillCapabilityStatus =
  | "ready"
  | "conditional"
  | "dependency_missing"
  | "reference_only";

export interface AgentToolConfig {
  name:
    | "read_file"
    | "edit_file"
    | "write_file"
    | "exec_command"
    | "input_command"
    | "run_subagent"
    | "input_subagent"
    | "read_image"
    | "describe_image";
  description: string;
  parameters: Record<string, unknown>;
  permission: "r" | "rw";
  timeoutMs: number;
  maxOutputLength: number;
  call_description: boolean;
}

export interface AgentSystemConfig {
  version: number;
  name: string;
  description: string;
  system_prompt: string;
  max_turns: number;
  model: {
    max_tokens: number;
    thinking_level: "low" | "medium" | "high" | "xhigh";
    timeoutMs: number;
  };
  compaction: {
    max_context_length: number;
    max_session_turns: number;
    mode: "summarize";
    prompt: string;
  };
  tools: { builtin: AgentToolConfig[] };
  skillset_id: string;
}

export interface AgentSkillSnapshot {
  skill_id: string;
  name: string;
  description: string;
  status: SkillCapabilityStatus;
  reason: string;
  digest: string;
  source_url: string;
  source_path: string;
  source_license: "Apache-2.0";
  adapted: boolean;
}

export interface AgentPayload {
  agent_id: string;
  builtin: boolean;
  config: AgentSystemConfig;
  agents_md: string;
  skills: AgentSkillSnapshot[];
  revision: string;
  state_path: string;
}

export interface AgentSummary {
  agent_id: string;
  name: string;
  description: string;
  version: number;
  builtin: boolean;
  skill_count: number;
  revision: string;
}

export interface BuiltinSkill extends AgentSkillSnapshot {
  available: boolean;
  availability_reason: string;
  inject_runtime: boolean;
}

export type ApprovalMode =
  | "always-ask"
  | "read-only"
  | "allow-all"
  | "deny-all";

export type AgentThinkingLevel = "low" | "medium" | "high" | "xhigh";
export type AgentSessionStatus = "idle" | "running" | "waiting_approval" | "failed";
export type AgentTaskStatus =
  | "pending"
  | "running"
  | "waiting_approval"
  | "completed"
  | "failed"
  | "stopped";

export interface AgentSession {
  session_id: string;
  agent_id: string;
  workspace_id: string;
  title: string;
  model_id: string;
  thinking_level: AgentThinkingLevel;
  approval_mode: ApprovalMode;
  skillset_id: string;
  status: AgentSessionStatus;
  parent_session_id: string | null;
  depth: number;
  created_at: number;
  updated_at: number;
}

export interface AgentMessage {
  message_id: string;
  session_id: string;
  task_id: string | null;
  sequence: number;
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  tool_call_id: string | null;
  tool_calls: Array<Record<string, unknown>>;
  created_at: number;
}

export interface AgentTask {
  task_id: string;
  session_id: string;
  kind: "chat" | "generate_agent" | "app_engine_shadow";
  prompt: string;
  model_id: string;
  thinking_level: AgentThinkingLevel;
  approval_mode: ApprovalMode;
  status: AgentTaskStatus;
  output: string;
  error: string;
  created_at: number;
  updated_at: number;
  started_at: number | null;
  finished_at: number | null;
}

export type EngineShadowStatus =
  | "pending"
  | "running"
  | "candidate_ready"
  | "blocked"
  | "budget_limited"
  | "stopped"
  | "interrupted"
  | "failed";

export interface EngineShadowRun {
  run_id: string;
  session_id: string;
  status: EngineShadowStatus;
  objective: string;
  model_base_id: string;
  resolved_model_id: string;
  thinking_level: AgentThinkingLevel;
  token_budget: number;
  max_goal_rounds: number;
  max_task_turns: number;
  goal_round: number;
  model_turns: number;
  retry_count: number;
  token_total: number;
  usage_source: "provider" | "estimated" | "none";
  tool_calls: number;
  tool_failures: number;
  candidate_sha256: string;
  error_code: string;
  public_error: string;
  upstream_revision: string;
  protocol: string;
  created_at: number;
  updated_at: number;
  started_at: number | null;
  finished_at: number | null;
}

export interface EngineShadowRunDetail {
  run: EngineShadowRun;
  last_event_sequence: number;
}

export interface EngineShadowEvent {
  sequence: number;
  run_id: string;
  type: string;
  payload: Record<string, unknown>;
  created_at: number;
}

export interface AgentApproval {
  approval_id: string;
  session_id: string;
  task_id: string;
  tool_call_id: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  status: "pending" | "approved" | "rejected" | "cancelled";
  decision_message: string;
  created_at: number;
  decided_at: number | null;
}

export interface AgentSessionDetail {
  session: AgentSession;
  messages: AgentMessage[];
  tasks: AgentTask[];
  approvals: AgentApproval[];
  last_event_sequence: number;
}

export interface AgentRuntimeEvent {
  sequence: number;
  session_id: string;
  task_id: string | null;
  type: string;
  payload: Record<string, unknown>;
  created_at: number;
}

export interface AgentWorkspaceEntry {
  name: string;
  path: string;
  kind: "file" | "directory";
  size: number;
  modified_at: number;
}

export interface AgentSkillset {
  skillset_id: string;
  name: string;
  description: string;
  builtin: boolean;
  members: Array<{ skill_id: string; digest: string }>;
  revision: string;
}
