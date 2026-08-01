import {
  type XpertDefinition,
  type XpertAppApiKey,
  type XpertAppDefinition,
  type XpertAppLimits,
  type XpertAppPolicy,
  type XpertDraft,
  type XpertConversation,
  type XpertFileAsset,
  type XpertFileMemoryIndex,
  type XpertFileMemorySignal,
  type XpertFileMemoryType,
  type XpertListResponse,
  type XpertMemoryCandidate,
  type XpertMemoryRecord,
  type XpertStatus,
  type XpertValidationResult,
  type XpertVersion,
  type XpertWorkflowDefinition,
} from "../types/xpert";
import { type WorkflowDefinition } from "../types/workflow";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw new Error(await readResponseError(response));
  }
  return response.json() as Promise<T>;
}

function jsonRequest(method: "POST" | "PATCH", body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export function listXperts(options?: {
  status?: XpertStatus | "all";
  search?: string;
  limit?: number;
}) {
  const query = new URLSearchParams();
  if (options?.status && options.status !== "all") {
    query.set("status", options.status);
  }
  if (options?.search) query.set("search", options.search);
  query.set("limit", String(options?.limit ?? 100));
  return requestJson<XpertListResponse>(`/api/xperts?${query.toString()}`);
}

export function createXpert(payload: {
  name: string;
  slug?: string;
  description?: string;
  tags?: string[];
  starters?: string[];
}) {
  return requestJson<XpertDefinition>(
    "/api/xperts",
    jsonRequest("POST", payload),
  );
}

export function getXpert(xpertId: string) {
  return requestJson<XpertDefinition>(`/api/xperts/${xpertId}`);
}

export function updateXpert(
  xpertId: string,
  payload: Partial<
    Pick<XpertDefinition, "name" | "description" | "tags" | "starters" | "status">
  > & { draft?: XpertDraft },
) {
  return requestJson<XpertDefinition>(
    `/api/xperts/${xpertId}`,
    jsonRequest("PATCH", payload),
  );
}

export function validateXpert(xpertId: string) {
  return requestJson<XpertValidationResult>(
    `/api/xperts/${xpertId}/validate`,
    jsonRequest("POST", {}),
  );
}

export function publishXpert(xpertId: string, releaseNotes: string) {
  return requestJson<XpertVersion>(
    `/api/xperts/${xpertId}/publish`,
    jsonRequest("POST", { release_notes: releaseNotes }),
  );
}

export function listXpertVersions(xpertId: string) {
  return requestJson<XpertVersion[]>(`/api/xperts/${xpertId}/versions`);
}

export async function getXpertApp(xpertId: string) {
  const response = await fetch(`/api/xperts/${xpertId}/app`);
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(await readResponseError(response));
  const payload = await response.json() as { app: XpertAppDefinition };
  return payload.app;
}

export function createXpertApp(
  xpertId: string,
  payload: { slug?: string; name?: string; description?: string; starters?: string[] },
) {
  return requestJson<{ app: XpertAppDefinition; share_token: string; share_url: string }>(
    `/api/xperts/${xpertId}/app`,
    jsonRequest("POST", payload),
  );
}

export function updateXpertApp(
  appId: string,
  payload: {
    name?: string;
    description?: string;
    starters?: string[];
    policy?: XpertAppPolicy;
    limits?: XpertAppLimits;
  },
) {
  return requestJson<{ app: XpertAppDefinition }>(
    `/api/xpert-apps/${appId}`,
    jsonRequest("PATCH", payload),
  );
}

export function deployXpertApp(
  appId: string,
  payload: { version: number; release_notes?: string },
) {
  return requestJson<{ app: XpertAppDefinition; preflight: { warnings: Array<{ code: string; message: string }> } }>(
    `/api/xpert-apps/${appId}/deploy`,
    jsonRequest("POST", payload),
  );
}

export function disableXpertApp(appId: string) {
  return requestJson<{ app: XpertAppDefinition }>(
    `/api/xpert-apps/${appId}/disable`,
    jsonRequest("POST", {}),
  );
}

export function rotateXpertAppShareToken(appId: string) {
  return requestJson<{ app: XpertAppDefinition; share_token: string; share_url: string }>(
    `/api/xpert-apps/${appId}/share-token/rotate`,
    jsonRequest("POST", {}),
  );
}

export function createXpertAppApiKey(
  appId: string,
  payload: { name: string; limits?: XpertAppLimits; expires_at?: number },
) {
  return requestJson<{ app: XpertAppDefinition; key: XpertAppApiKey; api_key: string }>(
    `/api/xpert-apps/${appId}/keys`,
    jsonRequest("POST", payload),
  );
}

export function revokeXpertAppApiKey(appId: string, keyId: string) {
  return requestJson<{ key: XpertAppApiKey }>(
    `/api/xpert-apps/${appId}/keys/${keyId}`,
    { method: "DELETE" },
  );
}

async function readResponseError(response: Response) {
  try {
    const payload = await response.json() as {
      detail?: string | { message?: string; issues?: Array<{ message: string }> };
      error?: string | { message?: string };
    };
    if (typeof payload.detail === "string") return payload.detail;
    if (payload.detail?.issues?.length) {
      return payload.detail.issues.map((item) => item.message).join("；");
    }
    if (payload.detail?.message) return payload.detail.message;
    if (typeof payload.error === "string") return payload.error;
    if (payload.error?.message) return payload.error.message;
  } catch {
    // Fall through to the status-based message.
  }
  return `请求失败：${response.status}`;
}

export function createXpertConversation(xpertId: string, title = "") {
  return requestJson<XpertConversation>(
    `/api/xperts/${xpertId}/conversations`,
    jsonRequest("POST", { title }),
  );
}

export function listXpertConversations(xpertId: string) {
  return requestJson<{ items: XpertConversation[]; total: number }>(
    `/api/xperts/${xpertId}/conversations?limit=50`,
  );
}

export function getXpertConversation(xpertId: string, conversationId: string) {
  return requestJson<XpertConversation>(
    `/api/xperts/${xpertId}/conversations/${conversationId}`,
  );
}

export async function uploadXpertFile(
  xpertId: string,
  conversationId: string,
  file: File,
) {
  const body = new FormData();
  body.append("file", file);
  return requestJson<XpertFileAsset>(
    `/api/xperts/${xpertId}/conversations/${conversationId}/files`,
    { method: "POST", body },
  );
}

export function listXpertFiles(
  xpertId: string,
  conversationId: string,
  includeArchived = false,
) {
  return requestJson<{ items: XpertFileAsset[]; total: number }>(
    `/api/xperts/${xpertId}/conversations/${conversationId}/files?include_archived=${includeArchived}`,
  );
}

export function archiveXpertFile(
  xpertId: string,
  conversationId: string,
  assetId: string,
) {
  return requestJson<XpertFileAsset>(
    `/api/xperts/${xpertId}/conversations/${conversationId}/files/${assetId}`,
    { method: "DELETE" },
  );
}

export interface XpertAudioCapabilities {
  version: number;
  text_to_speech: {
    enabled: boolean;
    model_id: string;
    voice: string;
    max_text_chars: number;
  };
  speech_to_text: {
    enabled: boolean;
    model_id: string;
    max_file_bytes: number;
  };
  gateway_configured: boolean;
}

export function getXpertAudioCapabilities(
  xpertId: string,
  version?: number,
) {
  const query = version ? `?version=${version}` : "";
  return requestJson<XpertAudioCapabilities>(
    `/api/xperts/${xpertId}/audio-capabilities${query}`,
  );
}

export async function transcribeXpertAudio(
  xpertId: string,
  version: number,
  file: File,
) {
  const body = new FormData();
  body.append("version", String(version));
  body.append("file", file);
  return requestJson<{ text: string; model_id: string; xpert_version: number }>(
    `/api/xperts/${xpertId}/audio/transcriptions`,
    { method: "POST", body },
  );
}

export async function synthesizeXpertSpeech(
  xpertId: string,
  version: number,
  text: string,
) {
  const response = await fetch(`/api/xperts/${xpertId}/audio/speech`, {
    ...jsonRequest("POST", { text, version }),
  });
  if (!response.ok) throw new Error(await readResponseError(response));
  return response.blob();
}

export function listXpertMemories(
  xpertId: string,
  conversationId?: string,
  options?: {
    search?: string;
    type?: XpertFileMemoryType | "all";
    status?: "active" | "archived";
  },
) {
  const query = new URLSearchParams({ scope: "both", limit: "100" });
  if (conversationId) query.set("conversation_id", conversationId);
  if (options?.search) query.set("search", options.search);
  if (options?.type && options.type !== "all") query.set("type", options.type);
  if (options?.status) query.set("status", options.status);
  return requestJson<{ items: XpertMemoryRecord[]; total: number }>(
    `/api/xperts/${xpertId}/memories?${query.toString()}`,
  );
}

export function createXpertMemory(
  xpertId: string,
  payload: {
    content: string;
    scope: "conversation" | "xpert";
    conversation_id?: string;
    source_type?: string;
    source_id?: string;
    type?: XpertFileMemoryType;
    title?: string;
    summary?: string;
    tags?: string[];
  },
) {
  return requestJson<XpertMemoryRecord>(
    `/api/xperts/${xpertId}/memories`,
    jsonRequest("POST", payload),
  );
}

export function archiveXpertMemory(xpertId: string, memoryId: string, revision?: number) {
  const query = revision ? `?revision=${revision}` : "";
  return requestJson<XpertMemoryRecord>(
    `/api/xperts/${xpertId}/memories/${memoryId}${query}`,
    { method: "DELETE" },
  );
}

export function listXpertMemoryCandidates(
  xpertId: string,
  conversationId?: string,
) {
  const query = new URLSearchParams({ limit: "100" });
  if (conversationId) query.set("conversation_id", conversationId);
  return requestJson<{ items: XpertMemoryCandidate[]; total: number }>(
    `/api/xperts/${xpertId}/memory-candidates?${query.toString()}`,
  );
}

export function decideXpertMemoryCandidate(
  xpertId: string,
  candidateId: string,
  action: "approve" | "reject",
  revision?: number,
) {
  return requestJson<XpertMemoryCandidate>(
    `/api/xperts/${xpertId}/memory-candidates/${candidateId}/${action}`,
    jsonRequest("POST", revision ? { revision } : {}),
  );
}

export function getXpertFileMemoryIndex(xpertId: string) {
  return requestJson<XpertFileMemoryIndex>(`/api/xperts/${xpertId}/file-memory/index`);
}

export function getXpertFileMemory(xpertId: string, memoryId: string) {
  return requestJson<XpertMemoryRecord>(
    `/api/xperts/${xpertId}/file-memory/${memoryId}`,
  );
}

export function listXpertFileMemorySignals(xpertId: string, memoryId?: string) {
  const query = new URLSearchParams({ limit: "100" });
  if (memoryId) query.set("memory_id", memoryId);
  return requestJson<{ items: XpertFileMemorySignal[]; total: number }>(
    `/api/xperts/${xpertId}/file-memory/signals?${query.toString()}`,
  );
}

export function updateXpertFileMemory(
  xpertId: string,
  memoryId: string,
  payload: {
    revision: number;
    type?: XpertFileMemoryType;
    title?: string;
    summary?: string;
    content?: string;
    tags?: string[];
  },
) {
  return requestJson<XpertMemoryRecord>(
    `/api/xperts/${xpertId}/file-memory/${memoryId}`,
    jsonRequest("PATCH", payload),
  );
}

export function updateXpertMemoryCandidate(
  xpertId: string,
  candidateId: string,
  payload: {
    revision: number;
    type?: XpertFileMemoryType;
    title?: string;
    summary?: string;
    content?: string;
    tags?: string[];
    action?: "create" | "update";
    target_memory_id?: string | null;
    base_revision?: number | null;
  },
) {
  return requestJson<XpertMemoryCandidate>(
    `/api/xperts/${xpertId}/memory-candidates/${candidateId}`,
    jsonRequest("PATCH", payload),
  );
}

export function runXpertFileMemoryWriteback(
  xpertId: string,
  conversationId: string,
) {
  return requestJson<{ items: XpertMemoryCandidate[]; total: number }>(
    `/api/xperts/${xpertId}/file-memory/writeback`,
    jsonRequest("POST", { conversation_id: conversationId, scope: "xpert" }),
  );
}

export function toWorkflowDefinition(
  xpert: XpertDefinition,
): WorkflowDefinition {
  return {
    id: xpert.draft.workflow.id,
    title: xpert.draft.workflow.title,
    nodes: xpert.draft.workflow.nodes.map((node) => ({
      id: node.id,
      type: "workflowNode" as const,
      position: node.position ?? { x: 0, y: 0 },
      data: node.data,
    })),
    edges: xpert.draft.workflow.edges,
    updatedAt: new Date(xpert.updated_at * 1000).toISOString(),
  };
}

export function toXpertDraftWorkflow(definition: WorkflowDefinition) {
  return {
    id: definition.id,
    title: definition.title,
    version: "xpert-draft-v1",
    source: "classic",
    nodes: definition.nodes.map((node) => ({
      id: node.id,
      type: node.data.kind,
      position: node.position,
      data: node.data,
    })),
    edges: definition.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle,
      targetHandle: edge.targetHandle,
    })),
  } satisfies XpertWorkflowDefinition;
}
