export type OpenRouterBatchStatus =
  | "submitting"
  | "validating"
  | "in_progress"
  | "finalizing"
  | "completed"
  | "failed"
  | "expired"
  | "cancelling"
  | "cancelled"
  | "uncertain";

export interface OpenRouterBatchResult {
  id: string;
  custom_id: string;
  response: {
    status_code: number;
    request_id?: string;
    body?: unknown;
  } | null;
  error: unknown;
}

export interface OpenRouterBatchJob {
  id: string;
  object: "batch";
  endpoint: "/v1/chat/completions" | "/v1/embeddings";
  model: string;
  completion_window: "24h";
  status: OpenRouterBatchStatus;
  created_at: number;
  finalized_at: number | null;
  request_counts: {
    total: number;
    completed: number;
    failed: number;
  };
  usage: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    cost?: number;
    is_byok?: boolean;
  } | null;
  results: OpenRouterBatchResult[] | null;
  error: unknown;
  billing_authoritative?: false;
}

export interface OpenRouterBatchSubmitInput {
  model_id: string;
  endpoint: "/v1/chat/completions" | "/v1/embeddings";
  requests: Array<{
    custom_id: string;
    input: string;
  }>;
  temperature?: number;
  max_tokens?: number;
}

export const TERMINAL_BATCH_STATUSES = new Set<OpenRouterBatchStatus>([
  "completed",
  "failed",
  "expired",
  "cancelled",
  "uncertain",
]);

async function responseError(response: Response) {
  const payload = (await response.json().catch(() => null)) as
    | { error?: unknown; detail?: unknown }
    | null;
  const candidate = payload?.error ?? payload?.detail;
  if (typeof candidate === "string" && candidate.trim()) return candidate;
  if (candidate && typeof candidate === "object") {
    const message = (candidate as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
  }
  return `批处理请求失败（HTTP ${response.status}）`;
}

export async function submitOpenRouterBatch(
  input: OpenRouterBatchSubmitInput,
  idempotencyKey: string,
  signal?: AbortSignal,
) {
  const response = await fetch("/api/openrouter/batches", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(input),
    signal,
  });
  if (!response.ok) throw new Error(await responseError(response));
  return (await response.json()) as OpenRouterBatchJob;
}

export async function fetchOpenRouterBatch(
  batchId: string,
  signal?: AbortSignal,
) {
  const response = await fetch(
    `/api/openrouter/batches/${encodeURIComponent(batchId)}`,
    { signal },
  );
  if (!response.ok) throw new Error(await responseError(response));
  return (await response.json()) as OpenRouterBatchJob;
}

export function batchResultText(result: OpenRouterBatchResult) {
  if (result.error) {
    if (typeof result.error === "string") return result.error;
    if (typeof result.error === "object") {
      const message = (result.error as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
    return "该请求处理失败。";
  }
  const body = result.response?.body;
  if (!body || typeof body !== "object") return "未返回可展示结果。";
  const choices = (body as { choices?: unknown }).choices;
  if (Array.isArray(choices) && choices.length > 0) {
    const first = choices[0] as {
      message?: { content?: unknown };
      text?: unknown;
    };
    const content = first.message?.content ?? first.text;
    if (typeof content === "string") return content;
  }
  const data = (body as { data?: unknown }).data;
  if (Array.isArray(data)) {
    const first = data[0] as { embedding?: unknown } | undefined;
    const dimensions = Array.isArray(first?.embedding)
      ? first.embedding.length
      : 0;
    return dimensions > 0
      ? `向量生成完成，共 ${data.length} 条，维度 ${dimensions}。`
      : `向量生成完成，共 ${data.length} 条。`;
  }
  return "处理完成，返回结果不属于当前可视化格式。";
}
