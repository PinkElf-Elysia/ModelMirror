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
