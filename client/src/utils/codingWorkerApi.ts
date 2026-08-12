import type {
  CodingWorkerApproval,
  CodingWorkerArtifact,
  CodingWorkerChangeset,
  CodingWorkerChildren,
  CodingWorkerDiagnosticsSnapshot,
  CodingWorkerEntry,
  CodingWorkerEvent,
  CodingWorkerEvidence,
  CodingWorkerOperationOutputChunk,
  CodingWorkerPlan,
  CodingWorkerQuestion,
  CodingWorkerStatus,
  CodingWorkerSubtask,
  CodingWorkerTask,
  CodingWorkerTaskSpec,
  CodingWorkerTurnHistory,
} from "../types/codingWorker";

const API_ROOT = "/api/coding-worker/v1";

export interface CodingWorkerHandoffResult {
  id: string;
  status: string;
  project: { id: string; [key: string]: unknown };
  revision: number;
  task_id: string;
}

export class CodingWorkerApiError extends Error {
  status: number;
  code: string | null;

  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = "CodingWorkerApiError";
    this.status = status;
    this.code = code;
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload
      ? (payload as { detail?: unknown }).detail
      : null;
    const structured = detail && typeof detail === "object"
      ? detail as { code?: unknown; message?: unknown }
      : null;
    throw new CodingWorkerApiError(
      typeof structured?.message === "string"
        ? structured.message
        : typeof detail === "string"
          ? detail
          : `Coding Worker 请求失败（${response.status}）`,
      response.status,
      typeof structured?.code === "string" ? structured.code : null,
    );
  }
  return payload as T;
}

const taskEventTypes = [
  "task_created", "task_state", "provider_event", "steering_queued",
  "approval_requested", "approval_decided", "tool_operation",
  "artifact_created", "evidence_recorded", "evidence_invalidated",
  "checkpoint_created", "acceptance_evaluated", "acceptance_retry",
  "task_pinned", "task_unpinned", "operation_output",
  "plan_updated", "todo_updated", "question_requested", "question_resolved",
  "context_compacted", "subtask_created", "subtask_completed", "subtask_failed",
  "changeset_merge_started", "changeset_merged", "changeset_conflicted",
] as const;

export const getCodingWorkerStatus = () => request<CodingWorkerStatus>(API_ROOT);

export async function listCodingWorkerTasks() {
  return (await request<{ tasks: CodingWorkerTask[] }>(`${API_ROOT}/tasks`)).tasks;
}

export const getCodingWorkerTask = (taskId: string) =>
  request<CodingWorkerTask>(`${API_ROOT}/tasks/${encodeURIComponent(taskId)}`);

export const createCodingWorkerTask = (spec: CodingWorkerTaskSpec) =>
  request<CodingWorkerTask>(`${API_ROOT}/tasks`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec),
  });

export const handoffCodingWorkerTask = (taskId: string) =>
  request<CodingWorkerHandoffResult>(
    `/api/coding/worker-tasks/${encodeURIComponent(taskId)}/handoff`,
    { method: "POST" },
  );

export const sendCodingWorkerMessage = (taskId: string, message: string) =>
  request<CodingWorkerTask>(`${API_ROOT}/tasks/${encodeURIComponent(taskId)}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });

export const getCodingWorkerPlan = (taskId: string) =>
  request<CodingWorkerPlan | null>(
    `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/plan`,
  );

export async function listCodingWorkerQuestions(taskId: string) {
  return (await request<{ questions: CodingWorkerQuestion[] }>(
    `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/questions`,
  )).questions;
}

export const answerCodingWorkerQuestion = (
  taskId: string,
  questionId: string,
  answer: { answer: string } | { option_id: string },
) => request<CodingWorkerQuestion>(
  `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/questions/${encodeURIComponent(questionId)}`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(answer),
  },
);

export const getCodingWorkerTurnHistory = (taskId: string) =>
  request<CodingWorkerTurnHistory>(
    `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/turns`,
  );

export const navigateCodingWorkerTurn = (taskId: string, action: "undo" | "redo") =>
  request<CodingWorkerTurnHistory>(
    `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/${action}`,
    { method: "POST" },
  );

export const forkCodingWorkerTask = (taskId: string, clientForkId: string) =>
  request<CodingWorkerTask>(`${API_ROOT}/tasks/${encodeURIComponent(taskId)}/fork`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_fork_id: clientForkId }),
  });

export const listCodingWorkerChildren = (taskId: string) =>
  request<CodingWorkerChildren>(
    `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/children`,
  );

export const mergeCodingWorkerSubtask = (
  taskId: string,
  childTaskId: string,
  operationId: string,
) => request<CodingWorkerSubtask>(
  `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/subtasks/${encodeURIComponent(childTaskId)}/merge`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ operation_id: operationId }),
  },
);

export const changeCodingWorkerTask = (
  taskId: string,
  action: "pause" | "resume" | "cancel" | "pin" | "unpin",
) => request<CodingWorkerTask>(
  `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/${action === "unpin" ? "pin" : action}`,
  { method: action === "unpin" ? "DELETE" : "POST" },
);

export async function listCodingWorkerApprovals(taskId: string) {
  return (await request<{ approvals: CodingWorkerApproval[] }>(
    `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/approvals`,
  )).approvals;
}

export const decideCodingWorkerApproval = (
  taskId: string,
  approvalId: string,
  decision: "approve_once" | "approve_task" | "reject",
) => request<CodingWorkerApproval>(
  `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/approvals`,
  {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approval_id: approvalId, decision, ttl_seconds: 900 }),
  },
);

export async function listCodingWorkerEvidence(taskId: string) {
  return (await request<{ evidence: CodingWorkerEvidence[] }>(
    `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/evidence`,
  )).evidence;
}

export async function listCodingWorkerArtifacts(taskId: string) {
  return (await request<{ artifacts: CodingWorkerArtifact[] }>(
    `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/artifacts`,
  )).artifacts;
}

export async function listCodingWorkerOperationOutput(
  taskId: string,
  operationId: string,
  after = 0,
) {
  return (await request<{ chunks: CodingWorkerOperationOutputChunk[] }>(
    `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/operations/${encodeURIComponent(operationId)}/output?after=${Math.max(0, after)}`,
  )).chunks;
}

export const getCodingWorkerChangeset = (taskId: string, operationId: string) =>
  request<CodingWorkerChangeset>(
    `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/changesets/${encodeURIComponent(operationId)}`,
  );

export const getCodingWorkerDiagnostics = (taskId: string, operationId: string) =>
  request<CodingWorkerDiagnosticsSnapshot>(
    `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/diagnostics/${encodeURIComponent(operationId)}`,
  );

export async function listCodingWorkerTree(taskId: string) {
  return request<{ workspace_id: string; tree_hash: string; entries: CodingWorkerEntry[] }>(
    `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/workspace/tree`,
  );
}

async function requestText(url: string) {
  const response = await fetch(url);
  if (!response.ok) throw new CodingWorkerApiError(`内容读取失败（${response.status}）`, response.status);
  return response.text();
}

export const readCodingWorkerEntry = (taskId: string, entryId: string) =>
  requestText(`${API_ROOT}/tasks/${encodeURIComponent(taskId)}/workspace/entries/${encodeURIComponent(entryId)}`);

export const readCodingWorkerDiff = (taskId: string) =>
  requestText(`${API_ROOT}/tasks/${encodeURIComponent(taskId)}/workspace/diff`);

export const codingWorkerArtifactUrl = (taskId: string, artifactId: string) =>
  `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(artifactId)}`;

export function connectCodingWorkerEvents(
  taskId: string,
  after: number,
  handlers: { onEvent: (event: CodingWorkerEvent) => void; onTransportError: () => void },
) {
  const source = new EventSource(
    `${API_ROOT}/tasks/${encodeURIComponent(taskId)}/events?after=${Math.max(0, after)}`,
  );
  const receive = (message: MessageEvent<string>) => {
    try {
      const event = JSON.parse(message.data) as CodingWorkerEvent;
      if (!Number.isFinite(event.sequence) || typeof event.type !== "string") throw new Error("invalid event");
      handlers.onEvent(event);
    } catch {
      source.close();
      handlers.onTransportError();
    }
  };
  taskEventTypes.forEach((type) => source.addEventListener(type, receive as EventListener));
  source.onerror = handlers.onTransportError;
  return () => source.close();
}
