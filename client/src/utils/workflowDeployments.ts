import { type WorkflowDefinition } from "../types/workflow";

export interface WorkflowVersionSummary {
  project_id: string;
  version: number;
  node_contract_checksum: string;
  definition_checksum: string;
  trigger_kind: "manual" | "schedule" | "http" | "failure";
  entry_node_id: string;
  published_at: number;
}

export interface WorkflowDeploymentSummary {
  deployment_id: string;
  project_id: string;
  version: number;
  trigger_kind: "manual" | "schedule" | "http" | "failure";
  active: boolean;
  hook_id?: string | null;
  webhook_key_prefix?: string | null;
  next_run_at?: number | null;
  activated_at?: number | null;
  deactivated_at?: number | null;
  webhook_key?: string;
  webhook_key_once?: boolean;
}

export interface WorkflowProjectResponse {
  project_id: string;
  title: string;
  draft: WorkflowDefinition;
  draft_revision: number;
  active_version?: number | null;
  active_deployment?: WorkflowDeploymentSummary | null;
  published_versions: WorkflowVersionSummary[];
  created_at: number;
  updated_at: number;
}

export interface WorkflowExecutionSummary {
  execution_id: string;
  project_id: string;
  version: number;
  trigger_kind: "manual" | "schedule" | "http" | "failure";
  occurrence_key: string;
  status: "pending" | "running" | "waiting" | "completed" | "failed" | "skipped" | "cancelled";
  wait_kind?: string | null;
  resume_at?: number | null;
  parent_execution_id?: string | null;
  root_execution_id?: string | null;
  source_execution_id?: string | null;
  call_node_id?: string | null;
  test_mode?: boolean;
  trigger_summary?: Record<string, unknown>;
  created_at: number;
  updated_at: number;
}

export interface WorkflowProjectSummary {
  project_id: string;
  title: string;
  active_version?: number | null;
  active_trigger_kind?: "manual" | "schedule" | "http" | "failure" | null;
  updated_at: number;
}

export interface WorkflowProjectListResponse {
  items: WorkflowProjectSummary[];
  total: number;
  limit: number;
  offset: number;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const payload = (await response.json().catch(() => ({}))) as T & {
    detail?: string | { message?: string };
  };
  if (!response.ok) {
    const detail = payload.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail?.message ?? "工作流发布服务暂时不可用。",
    );
  }
  return payload;
}

export function createWorkflowProject(workflow: WorkflowDefinition) {
  return requestJson<WorkflowProjectResponse>("/api/workflows", {
    method: "POST",
    body: JSON.stringify({ workflow }),
  });
}

export function fetchWorkflowProject(projectId: string) {
  return requestJson<WorkflowProjectResponse>(`/api/workflows/${projectId}`);
}

export function fetchWorkflowProjects({
  limit = 100,
  offset = 0,
  activeOnly = false,
  triggerKind,
}: {
  limit?: number;
  offset?: number;
  activeOnly?: boolean;
  triggerKind?: "manual" | "schedule" | "http" | "failure";
} = {}) {
  const query = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
    active_only: String(activeOnly),
  });
  if (triggerKind) query.set("trigger_kind", triggerKind);
  return requestJson<WorkflowProjectListResponse>(`/api/workflows?${query}`);
}

export function saveWorkflowProjectDraft(
  projectId: string,
  expectedRevision: number,
  workflow: WorkflowDefinition,
) {
  return requestJson<WorkflowProjectResponse>(`/api/workflows/${projectId}/draft`, {
    method: "PUT",
    body: JSON.stringify({ expected_revision: expectedRevision, workflow }),
  });
}

export function publishWorkflowProject(projectId: string) {
  return requestJson<WorkflowVersionSummary>(`/api/workflows/${projectId}/publish`, {
    method: "POST",
  });
}

export function activateWorkflowVersion(projectId: string, version: number) {
  return requestJson<WorkflowDeploymentSummary>(
    `/api/workflows/${projectId}/versions/${version}/activate`,
    { method: "POST" },
  );
}

export function deactivateWorkflowVersion(projectId: string, version: number) {
  return requestJson<WorkflowDeploymentSummary>(
    `/api/workflows/${projectId}/versions/${version}/deactivate`,
    { method: "POST" },
  );
}

export function rotateWorkflowWebhookKey(projectId: string, version: number) {
  return requestJson<WorkflowDeploymentSummary>(
    `/api/workflows/${projectId}/versions/${version}/rotate-webhook-key`,
    { method: "POST" },
  );
}

export function fetchWorkflowExecutions(projectId: string, limit = 20) {
  return requestJson<{ items: WorkflowExecutionSummary[] }>(
    `/api/workflows/${projectId}/executions?limit=${limit}`,
  );
}
