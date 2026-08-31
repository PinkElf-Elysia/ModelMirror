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
import {
  type ProviderRouteCallReceipt,
  type WorkflowDefinition,
} from "../types/workflow";

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

export interface XpertAudioProviderRouteReceipt {
  contract_version: string;
  entry_id: "xpert_transcription" | "xpert_speech";
  routing_mode: "managed_required";
  run_reference: string;
  status: "running" | "passed" | "failed" | "uncertain" | "cancelled";
  call_count: number;
  reason_codes: string[];
  calls: ProviderRouteCallReceipt[];
}

export class XpertAudioRequestError extends Error {
  readonly providerRouteReceipt: XpertAudioProviderRouteReceipt | null;

  constructor(
    message: string,
    providerRouteReceipt: XpertAudioProviderRouteReceipt | null,
  ) {
    super(message);
    this.name = "XpertAudioRequestError";
    this.providerRouteReceipt = providerRouteReceipt;
  }
}

function parseXpertAudioProviderRouteReceipt(
  value: unknown,
): XpertAudioProviderRouteReceipt | null {
  if (!value || typeof value !== "object") return null;
  const candidate = value as Record<string, unknown>;
  const statuses = new Set([
    "running",
    "passed",
    "failed",
    "uncertain",
    "cancelled",
  ]);
  if (
    typeof candidate.contract_version !== "string" ||
    !["xpert_transcription", "xpert_speech"].includes(
      String(candidate.entry_id),
    ) ||
    candidate.routing_mode !== "managed_required" ||
    typeof candidate.run_reference !== "string" ||
    typeof candidate.status !== "string" ||
    !statuses.has(candidate.status) ||
    typeof candidate.call_count !== "number" ||
    !Array.isArray(candidate.reason_codes) ||
    !candidate.reason_codes.every((item) => typeof item === "string") ||
    !Array.isArray(candidate.calls)
  ) {
    return null;
  }
  const calls: ProviderRouteCallReceipt[] = [];
  for (const value of candidate.calls) {
    if (!value || typeof value !== "object") return null;
    const call = value as Record<string, unknown>;
    if (
      typeof call.call_sequence !== "number" ||
      typeof call.model_id !== "string" ||
      typeof call.status !== "string" ||
      !["passed", "failed", "uncertain", "cancelled"].includes(call.status)
    ) {
      return null;
    }
    calls.push({
      call_sequence: call.call_sequence,
      model_id: call.model_id,
      ...(typeof call.actual_model === "string" || call.actual_model === null
        ? { actual_model: call.actual_model }
        : {}),
      ...(typeof call.dispatched === "boolean"
        ? { dispatched: call.dispatched }
        : {}),
      status: call.status as ProviderRouteCallReceipt["status"],
      ...(typeof call.error_code === "string" || call.error_code === null
        ? { error_code: call.error_code }
        : {}),
      ...(typeof call.prompt_tokens === "number" || call.prompt_tokens === null
        ? { prompt_tokens: call.prompt_tokens }
        : {}),
      ...(typeof call.completion_tokens === "number" ||
      call.completion_tokens === null
        ? { completion_tokens: call.completion_tokens }
        : {}),
      ...(typeof call.total_tokens === "number" || call.total_tokens === null
        ? { total_tokens: call.total_tokens }
        : {}),
    });
  }
  return {
    contract_version: candidate.contract_version,
    entry_id: candidate.entry_id as XpertAudioProviderRouteReceipt["entry_id"],
    routing_mode: "managed_required",
    run_reference: candidate.run_reference,
    status: candidate.status as XpertAudioProviderRouteReceipt["status"],
    call_count: candidate.call_count,
    reason_codes: [...candidate.reason_codes] as string[],
    calls,
  };
}

async function readXpertAudioError(
  response: Response,
): Promise<XpertAudioRequestError> {
  let receipt: XpertAudioProviderRouteReceipt | null = null;
  try {
    const payload = await response.clone().json() as { detail?: unknown };
    const detail = payload.detail;
    if (detail && typeof detail === "object" && "route_receipt" in detail) {
      receipt = parseXpertAudioProviderRouteReceipt(
        (detail as { route_receipt?: unknown }).route_receipt,
      );
    }
  } catch {
    // Error text still follows the existing sanitized response parser.
  }
  return new XpertAudioRequestError(await readResponseError(response), receipt);
}

function readXpertAudioReceiptHeader(response: Response) {
  const raw = response.headers.get("X-ModelMirror-Provider-Route-Receipt");
  if (!raw) return null;
  try {
    return parseXpertAudioProviderRouteReceipt(JSON.parse(raw));
  } catch {
    return null;
  }
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

export function deleteXpertFile(
  xpertId: string,
  conversationId: string,
  assetId: string,
) {
  return requestJson<{ asset_id: string; deleted: true }>(
    `/api/xperts/${xpertId}/conversations/${conversationId}/files/${assetId}/purge`,
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
    available: boolean;
    routing_mode: "legacy" | "managed_required" | "degraded_required";
    reason_code: string;
  };
  speech_to_text: {
    enabled: boolean;
    model_id: string;
    max_file_bytes: number;
    available: boolean;
    routing_mode: "legacy" | "managed_required" | "degraded_required";
    reason_code: string;
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
  const response = await fetch(`/api/xperts/${xpertId}/audio/transcriptions`, {
    method: "POST",
    headers: { "Idempotency-Key": window.crypto.randomUUID() },
    body,
  });
  if (!response.ok) throw await readXpertAudioError(response);
  const payload = await response.json() as {
    text: string;
    model_id: string;
    xpert_version: number;
    execution_mode?: "managed" | "legacy";
    provider_route_receipts?: unknown;
  };
  const receipts = Array.isArray(payload.provider_route_receipts)
    ? payload.provider_route_receipts
      .map(parseXpertAudioProviderRouteReceipt)
      .filter((item): item is XpertAudioProviderRouteReceipt => item !== null)
    : [];
  return { ...payload, provider_route_receipts: receipts };
}

export async function synthesizeXpertSpeech(
  xpertId: string,
  version: number,
  text: string,
) {
  const request = jsonRequest("POST", { text, version });
  const headers = new Headers(request.headers);
  headers.set("Idempotency-Key", window.crypto.randomUUID());
  const response = await fetch(`/api/xperts/${xpertId}/audio/speech`, {
    ...request,
    headers,
  });
  if (!response.ok) throw await readXpertAudioError(response);
  return {
    blob: await response.blob(),
    executionMode:
      response.headers.get("X-ModelMirror-Execution-Mode") === "managed"
        ? "managed" as const
        : "legacy" as const,
    providerRouteReceipt: readXpertAudioReceiptHeader(response),
  };
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
    variables: xpert.draft.workflow.variables?.map((variable) => ({
      ...variable,
      defaultValue:
        variable.defaultValue === undefined
          ? undefined
          : structuredClone(variable.defaultValue),
    })),
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
    variables: definition.variables?.map((variable) => ({
      ...variable,
      defaultValue:
        variable.defaultValue === undefined
          ? undefined
          : structuredClone(variable.defaultValue),
    })),
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
