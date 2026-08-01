import {
  type ConversationGoal,
  type CreateGoalPayload,
  type GoalListResponse,
  type GoalStatus,
  type GoalStep,
} from "../types/goal";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let message = `请求失败：${response.status}`;
    try {
      const payload = (await response.json()) as { detail?: string; error?: string };
      message = payload.detail || payload.error || message;
    } catch {
      // Keep the status-based fallback for non-JSON responses.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

function jsonRequest(method: "POST" | "PATCH", body?: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

export function createGoal(payload: CreateGoalPayload) {
  return requestJson<ConversationGoal>(
    "/api/runtime/goals",
    jsonRequest("POST", payload),
  );
}

export function listGoals(options?: {
  status?: GoalStatus | "all";
  search?: string;
  limit?: number;
}) {
  const query = new URLSearchParams();
  if (options?.status && options.status !== "all") query.set("status", options.status);
  if (options?.search) query.set("search", options.search);
  query.set("limit", String(options?.limit ?? 100));
  return requestJson<GoalListResponse>(`/api/runtime/goals?${query.toString()}`);
}

export function getGoal(goalId: string) {
  return requestJson<ConversationGoal>(`/api/runtime/goals/${goalId}`);
}

export function replanGoal(goalId: string) {
  return requestJson<ConversationGoal>(
    `/api/runtime/goals/${goalId}/plan`,
    jsonRequest("POST"),
  );
}

export function saveGoalPlan(
  goalId: string,
  payload: {
    plan_revision: number;
    summary: string;
    final_step_id: string;
    steps: Array<Pick<GoalStep, "step_id" | "title" | "instruction" | "target_xpert_id" | "depends_on">>;
  },
) {
  return requestJson<ConversationGoal>(
    `/api/runtime/goals/${goalId}/plan`,
    jsonRequest("PATCH", payload),
  );
}

export function goalAction(goalId: string, action: "start" | "pause" | "resume" | "cancel") {
  return requestJson<ConversationGoal>(
    `/api/runtime/goals/${goalId}/${action}`,
    jsonRequest("POST"),
  );
}

export function retryGoalStep(goalId: string, stepId: string) {
  return requestJson<ConversationGoal>(
    `/api/runtime/goals/${goalId}/steps/${stepId}/retry`,
    jsonRequest("POST"),
  );
}

export function skipGoalStep(goalId: string, stepId: string) {
  return requestJson<ConversationGoal>(
    `/api/runtime/goals/${goalId}/steps/${stepId}/skip`,
    jsonRequest("POST"),
  );
}

export function reassignGoalStep(
  goalId: string,
  stepId: string,
  payload: { target_xpert_id: string; instruction?: string },
) {
  return requestJson<ConversationGoal>(
    `/api/runtime/goals/${goalId}/steps/${stepId}`,
    jsonRequest("PATCH", payload),
  );
}
