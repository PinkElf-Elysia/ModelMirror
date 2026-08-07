import type {
  AgentApproval,
  AgentRuntimeEvent,
  AgentSession,
  AgentSessionDetail,
  AgentSkillset,
  AgentTask,
  AgentPayload,
  AgentSummary,
  AgentThinkingLevel,
  AgentWorkspaceEntry,
  ApprovalMode,
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

export async function listAgentSkillsets(): Promise<AgentSkillset[]> {
  const result = await request<{ skillsets: AgentSkillset[] }>(
    "/api/skills/skillsets",
  );
  return result.skillsets;
}

export async function listAgentSessions(): Promise<AgentSession[]> {
  const result = await request<{ sessions: AgentSession[] }>(
    "/api/agent-workspace/sessions",
  );
  return result.sessions;
}

export function createAgentSession(payload: {
  agent_id: string;
  model_id: string;
  thinking_level: AgentThinkingLevel;
  approval_mode: ApprovalMode;
  skillset_id?: string;
  title?: string;
}) {
  return request<AgentSession>("/api/agent-workspace/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function readAgentSession(sessionId: string) {
  return request<AgentSessionDetail>(
    `/api/agent-workspace/sessions/${encodeURIComponent(sessionId)}`,
  );
}

export function renameAgentSession(sessionId: string, title: string) {
  return request<AgentSession>(
    `/api/agent-workspace/sessions/${encodeURIComponent(sessionId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    },
  );
}

export function updateAgentSessionApprovalMode(
  sessionId: string,
  approvalMode: ApprovalMode,
) {
  return request<AgentSession>(
    `/api/agent-workspace/sessions/${encodeURIComponent(sessionId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approval_mode: approvalMode }),
    },
  );
}

export function deleteAgentSession(sessionId: string) {
  return request<{ ok: true }>(
    `/api/agent-workspace/sessions/${encodeURIComponent(sessionId)}`,
    { method: "DELETE" },
  );
}

export async function listAgentSubagents(sessionId: string) {
  const result = await request<{ subagents: AgentSession[] }>(
    `/api/agent-workspace/sessions/${encodeURIComponent(sessionId)}/subagents`,
  );
  return result.subagents;
}

export function createAgentTask(
  sessionId: string,
  payload: {
    prompt: string;
    model_id?: string;
    thinking_level?: AgentThinkingLevel;
    approval_mode?: ApprovalMode;
  },
) {
  return request<AgentTask>(
    `/api/agent-workspace/sessions/${encodeURIComponent(sessionId)}/tasks`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export function readAgentTask(taskId: string) {
  return request<AgentTask>(
    `/api/agent-workspace/tasks/${encodeURIComponent(taskId)}`,
  );
}

export function stopAgentTask(taskId: string) {
  return request<AgentTask>(
    `/api/agent-workspace/tasks/${encodeURIComponent(taskId)}/stop`,
    { method: "POST" },
  );
}

export function retryWorkspaceAgentGeneration(taskId: string) {
  return request<AgentTask>(
    `/api/agent-workspace/tasks/${encodeURIComponent(taskId)}/retry-generation`,
    { method: "POST" },
  );
}

export function generateWorkspaceAgent(payload: {
  prompt: string;
  model_id: string;
  thinking_level: AgentThinkingLevel;
  approval_mode: ApprovalMode;
}) {
  return request<{ session: AgentSession; task: AgentTask }>(
    "/api/agent-workspace/agents/generate",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export async function decideAgentApproval(
  approvalId: string,
  decision: "approve" | "reject",
  message = "",
) {
  const result = await request<{ approval: AgentApproval }>(
    `/api/agent-workspace/approvals/${encodeURIComponent(approvalId)}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, message }),
    },
  );
  return result.approval;
}

export async function listAgentWorkspace(
  sessionId: string,
  path = "",
): Promise<AgentWorkspaceEntry[]> {
  const search = new URLSearchParams({ path });
  const result = await request<{ path: string; entries: AgentWorkspaceEntry[] }>(
    `/api/agent-workspace/sessions/${encodeURIComponent(sessionId)}/workspace?${search}`,
  );
  return result.entries;
}

export function readAgentWorkspaceFile(sessionId: string, path: string) {
  const search = new URLSearchParams({ path });
  return request<{ path: string; content: string; size: number }>(
    `/api/agent-workspace/sessions/${encodeURIComponent(sessionId)}/workspace/file?${search}`,
  );
}

export function agentWorkspaceDownloadUrl(sessionId: string, path: string) {
  const search = new URLSearchParams({ path });
  return `/api/agent-workspace/sessions/${encodeURIComponent(sessionId)}/workspace/download?${search}`;
}

const runtimeEventTypes = [
  "session_created",
  "session_updated",
  "task_created",
  "task_running",
  "task_waiting_approval",
  "task_completed",
  "task_failed",
  "task_stopped",
  "text_delta",
  "thinking_delta",
  "tool_call_delta",
  "tool_call",
  "tool_output",
  "approval_waiting",
  "approval_decided",
  "subagent_status",
  "generation_validation_failed",
  "generation_quality_review_started",
  "generation_config_normalized",
  "approval_mode_changed",
  "agent_generated",
  "completed",
  "failed",
  "stopped",
] as const;

export function connectAgentWorkspaceEvents(
  sessionId: string,
  after: number,
  handlers: {
    onEvent: (event: AgentRuntimeEvent) => void;
    onTransportError: () => void;
  },
) {
  const source = new EventSource(
    `/api/agent-workspace/sessions/${encodeURIComponent(sessionId)}/events?after=${Math.max(0, after)}`,
  );
  const receive = (message: MessageEvent<string>) => {
    try {
      const event = JSON.parse(message.data) as AgentRuntimeEvent;
      if (
        !Number.isFinite(event.sequence) ||
        typeof event.type !== "string" ||
        !event.payload ||
        typeof event.payload !== "object"
      ) {
        throw new Error("Invalid AgentRuntimeEvent");
      }
      handlers.onEvent(event);
    } catch {
      source.close();
      handlers.onTransportError();
    }
  };
  runtimeEventTypes.forEach((eventType) => {
    source.addEventListener(eventType, receive as EventListener);
  });
  source.onerror = () => handlers.onTransportError();
  return () => source.close();
}
