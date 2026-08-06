import type {
  AgentPayload,
  AgentSummary,
  BuiltinSkill,
} from "../types/agentWorkspace";

export class AgentWorkspaceApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "AgentWorkspaceApiError";
    this.status = status;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? (payload as { detail?: unknown }).detail
        : null;
    throw new AgentWorkspaceApiError(
      typeof detail === "string" ? detail : `请求失败（${response.status}）`,
      response.status,
    );
  }
  return payload as T;
}

export function readAgentWorkspaceStatus() {
  return request<{
    enabled: boolean;
    version: string;
    runtime_enabled: boolean;
  }>("/api/agent-workspace/status");
}

export async function listWorkspaceAgents(): Promise<AgentSummary[]> {
  const result = await request<{ agents: AgentSummary[] }>(
    "/api/agent-workspace/agents",
  );
  return result.agents;
}

export function readWorkspaceAgent(agentId: string) {
  return request<AgentPayload>(
    `/api/agent-workspace/agents/${encodeURIComponent(agentId)}`,
  );
}

export function createWorkspaceAgent(payload: {
  agent_id: string;
  name: string;
  description: string;
}) {
  return request<AgentPayload>("/api/agent-workspace/agents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function saveWorkspaceAgent(agent: AgentPayload) {
  return request<AgentPayload>(
    `/api/agent-workspace/agents/${encodeURIComponent(agent.agent_id)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: agent.revision,
        config: agent.config,
        agents_md: agent.agents_md,
      }),
    },
  );
}

export function resetWorkspaceAgent(agent: AgentPayload) {
  return request<AgentPayload>(
    `/api/agent-workspace/agents/${encodeURIComponent(agent.agent_id)}/reset`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: agent.revision }),
    },
  );
}

export async function listBuiltinSkills(): Promise<BuiltinSkill[]> {
  const result = await request<{ skills: BuiltinSkill[]; total: number }>(
    "/api/skills/library",
  );
  return result.skills;
}
