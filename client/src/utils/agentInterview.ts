import { type AgentProfile } from "../data/agents";

export const AGENT_INTERVIEW_STORAGE_KEY = "modelmirror-agent-interview";
export const AGENT_DEFAULT_MODEL_NOTICE_KEY =
  "modelmirror-agent-default-model-notice";

export interface AgentInterviewPayload {
  agentId: string;
  agentName: string;
  department: string;
  expertise: string;
  prompt: string;
  sourceUrl: string;
}

export function buildAgentChatPath(agent: AgentProfile, modelId: string) {
  return `/chat/${encodeURIComponent(modelId)}?agentId=${encodeURIComponent(agent.id)}`;
}

export function saveAgentInterview(agent: AgentProfile) {
  const payload: AgentInterviewPayload = {
    agentId: agent.id,
    agentName: agent.name,
    department: agent.department,
    expertise: agent.expertise,
    prompt: agent.prompt,
    sourceUrl: agent.sourceUrl,
  };

  window.sessionStorage.setItem(
    AGENT_INTERVIEW_STORAGE_KEY,
    JSON.stringify(payload),
  );
}

export function readAgentInterview(agentId: string | null) {
  const raw = window.sessionStorage.getItem(AGENT_INTERVIEW_STORAGE_KEY);
  if (!raw) return null;

  try {
    const payload = JSON.parse(raw) as AgentInterviewPayload;
    if (agentId && payload.agentId !== agentId) return null;
    return payload;
  } catch {
    window.sessionStorage.removeItem(AGENT_INTERVIEW_STORAGE_KEY);
    return null;
  }
}

export function clearAgentInterview() {
  window.sessionStorage.removeItem(AGENT_INTERVIEW_STORAGE_KEY);
  window.sessionStorage.removeItem(AGENT_DEFAULT_MODEL_NOTICE_KEY);
}
