import type {
  CaseId,
  EventList,
  Evidence,
  ModuleInfo,
  Run,
  RunList,
  RunSummary,
  SystemStatus,
  CollectionList,
  LiteratureSession,
  ProjectList,
  ProjectReview,
  ProjectSources,
  ResearchProject,
  ZoteroStatus,
} from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) message = body.detail;
    } catch {
      // The status remains the authoritative error when no JSON body exists.
    }
    throw new ApiError(message, response.status);
  }
  return (await response.json()) as T;
}

export const api = {
  module: (signal?: AbortSignal) => request<ModuleInfo>("/api/v1/module", { signal }),
  system: (signal?: AbortSignal) => request<SystemStatus>("/api/v1/system", { signal }),
  summary: (signal?: AbortSignal) => request<RunSummary>("/api/v1/runs/summary", { signal }),
  runs: (params: URLSearchParams, signal?: AbortSignal) => {
    const query = params.toString();
    return request<RunList>(`/api/v1/runs${query ? `?${query}` : ""}`, { signal });
  },
  run: (runId: string, signal?: AbortSignal) =>
    request<Run>(`/api/v1/runs/${encodeURIComponent(runId)}`, { signal }),
  createRun: (caseId: CaseId, idempotencyKey: string, signal?: AbortSignal) =>
    request<Run>("/api/v1/runs", {
      method: "POST",
      signal,
      body: JSON.stringify({
        fixtureId: "inspect-smoke-v1",
        caseId,
        idempotencyKey,
        tenantId: "local",
        projectId: "local",
        actorId: "local",
      }),
    }),
  cancel: (runId: string, signal?: AbortSignal) =>
    request<Run>(`/api/v1/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
      signal,
    }),
  events: (runId: string, afterSeq: number, signal?: AbortSignal) =>
    request<EventList>(
      `/api/v1/runs/${encodeURIComponent(runId)}/events?afterSeq=${afterSeq}`,
      { signal },
    ),
  evidence: (runId: string, signal?: AbortSignal) =>
    request<Evidence>(`/api/v1/runs/${encodeURIComponent(runId)}/evidence`, { signal }),
  projects: (params = new URLSearchParams(), signal?: AbortSignal) => {
    const query = params.toString();
    return request<ProjectList>(`/api/v1/projects${query ? `?${query}` : ""}`, { signal });
  },
  project: (projectId: string, signal?: AbortSignal) =>
    request<ResearchProject>(`/api/v1/projects/${encodeURIComponent(projectId)}`, { signal }),
  createProject: (
    title: string,
    researchQuestion: string,
    idempotencyKey: string,
    signal?: AbortSignal,
  ) =>
    request<ResearchProject>("/api/v1/projects", {
      method: "POST",
      signal,
      body: JSON.stringify({ title, researchQuestion, idempotencyKey }),
    }),
  literatureSession: (signal?: AbortSignal) =>
    request<LiteratureSession>("/api/v1/literature/session", { signal }),
  unlockLiterature: (username: string, password: string, signal?: AbortSignal) =>
    request<LiteratureSession>("/api/v1/literature/session/unlock", {
      method: "POST",
      signal,
      body: JSON.stringify({ username, password }),
    }),
  clearLiteratureSession: (signal?: AbortSignal) =>
    request<LiteratureSession>("/api/v1/literature/session", {
      method: "DELETE",
      signal,
    }),
  startLiterature: (
    projectId: string,
    idempotencyKey: string,
    collectionId?: string,
    signal?: AbortSignal,
  ) =>
    request<ResearchProject>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/literature/runs`,
      {
        method: "POST",
        signal,
        body: JSON.stringify({ idempotencyKey, ...(collectionId ? { collectionId } : {}) }),
      },
    ),
  cancelLiterature: (projectId: string, signal?: AbortSignal) =>
    request<ResearchProject>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/literature/cancel`,
      { method: "POST", signal },
    ),
  syncLiterature: (projectId: string, signal?: AbortSignal) =>
    request<ResearchProject>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/literature/sync`,
      { method: "POST", signal },
    ),
  sources: (projectId: string, signal?: AbortSignal) =>
    request<ProjectSources>(`/api/v1/projects/${encodeURIComponent(projectId)}/sources`, { signal }),
  review: (projectId: string, signal?: AbortSignal) =>
    request<ProjectReview>(`/api/v1/projects/${encodeURIComponent(projectId)}/review`, { signal }),
  collections: (signal?: AbortSignal) =>
    request<CollectionList>("/api/v1/literature/library/collections", { signal }),
  indexCollection: (collectionId: string, signal?: AbortSignal) =>
    request<{ collectionId: string; status: "completed"; eventCount: number; terminalType: string }>(
      `/api/v1/literature/library/collections/${encodeURIComponent(collectionId)}/index`,
      { method: "POST", signal },
    ),
  zoteroStatus: (signal?: AbortSignal) =>
    request<ZoteroStatus>("/api/v1/literature/zotero/status", { signal }),
  syncZotero: (signal?: AbortSignal) =>
    request<{ success?: boolean; message?: string }>("/api/v1/literature/zotero/sync", {
      method: "POST",
      signal,
    }),
};
