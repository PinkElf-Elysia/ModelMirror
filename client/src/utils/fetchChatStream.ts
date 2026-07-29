export type ChatRole = "system" | "user" | "assistant";

export interface ChatTextPart {
  type: "text";
  text: string;
}

export interface ChatImagePart {
  type: "image_url";
  image_url: {
    url: string;
  };
}

export interface ChatInputAudioPart {
  type: "input_audio";
  attachment_id: string;
}

export type ChatMessageContent =
  | string
  | Array<ChatTextPart | ChatImagePart | ChatInputAudioPart>;

export interface ChatApiMessage {
  role: ChatRole;
  content: ChatMessageContent;
}

export interface ChatRuntimeMeta {
  runId: string;
  taskId: string;
  toolMode: string;
}

export type ChatGateway = "default" | "auto" | "omniroute";

export interface ChatRoutingOptions {
  session_id?: string;
  mode?: "fast" | "balanced" | "quality" | "cheap" | "reliable" | "offline";
  budget_usd?: number;
  budget_fallback?: "strict" | "cheapest";
}

export interface ChatCompressionOptions {
  mode?: "auto" | "off" | "standard" | "strong";
}

export interface ChatResponseAudioOptions {
  enabled: true;
  voice: string;
  format: "mp3";
}

export interface ChatAudioDelta {
  data?: string;
  transcript?: string;
}

export interface RouteReceipt {
  requested_model: string;
  actual_model?: string | null;
  provider?: string | null;
  strategy?: string | null;
  engine?: "sidecar" | "shadow" | "native_canary" | "native" | string | null;
  reason_codes?: string[];
  latency_ms?: number | null;
  tokens?: {
    input?: number | null;
    output?: number | null;
    total?: number | null;
  };
  response_cost_usd?: number | null;
  cost_kind: "actual" | "estimated" | "unavailable";
  fallback_attempts?: number;
  cache_hit?: boolean | null;
  request_id?: string | null;
  budget?: {
    limit_usd?: number | null;
    mode?: "strict" | "cheapest" | null;
    status?: string | null;
  };
  compression?: {
    applied?: boolean;
    profile?: "auto" | "off" | "standard" | "strong" | string;
    original_tokens?: number | null;
    final_tokens?: number | null;
    saved_tokens?: number | null;
    saved_ratio?: number | null;
    fidelity_status?: string | null;
    fallback_reason?: string | null;
    stages?: string[];
  };
  media?: {
    input_kind?: "audio" | null;
    output_kind?: "audio" | null;
    processing?: "direct" | "native_stream" | string | null;
    audio_status?: "completed" | "failed" | string | null;
    format?: string | null;
    raw_retained?: boolean;
  };
  version?: string | null;
}

interface FetchChatStreamOptions {
  modelId: string;
  messages: ChatApiMessage[];
  gateway?: ChatGateway;
  routing?: ChatRoutingOptions;
  compression?: ChatCompressionOptions;
  responseAudio?: ChatResponseAudioOptions;
  temperature?: number;
  topP?: number;
  maxTokens?: number;
  seed?: number;
  stop?: string[];
  toolMode?: "none" | "mcp_tools";
  toolNames?: string;
  maxToolIterations?: number;
  promptSuffix?: string;
  signal?: AbortSignal;
  onRuntimeMeta?: (meta: ChatRuntimeMeta) => void;
  onRouteReceipt?: (receipt: RouteReceipt) => void;
  onAudioDelta?: (audio: ChatAudioDelta) => void;
  onMessageEnd?: () => void;
  onDelta: (text: string) => void;
}

const fallbackErrorMessage = "抱歉，模型暂时无法响应，请稍后重试。";

function parseErrorMessage(value: unknown) {
  if (
    value &&
    typeof value === "object" &&
    "error" in value &&
    typeof value.error === "string"
  ) {
    return value.error;
  }

  if (
    value &&
    typeof value === "object" &&
    "error" in value &&
    value.error &&
    typeof value.error === "object" &&
    "message" in value.error &&
    typeof value.error.message === "string"
  ) {
    return value.error.message;
  }

  if (
    value &&
    typeof value === "object" &&
    "detail" in value &&
    typeof value.detail === "string"
  ) {
    return value.detail;
  }

  if (
    value &&
    typeof value === "object" &&
    "detail" in value &&
    Array.isArray(value.detail)
  ) {
    const messages = value.detail
      .map((item: unknown) =>
        item &&
        typeof item === "object" &&
        "msg" in item &&
        typeof item.msg === "string"
          ? item.msg
          : "",
      )
      .filter(Boolean);
    if (messages.length > 0) return messages.join("；");
  }

  return fallbackErrorMessage;
}

function imageUrlAsMarkdown(url: string) {
  return `\n![图片](${url})\n`;
}

function readContentPart(part: unknown): string {
  if (typeof part === "string") return part;
  if (!part || typeof part !== "object") return "";

  const record = part as Record<string, unknown>;
  if (record.type === "text" && typeof record.text === "string") {
    return record.text;
  }

  const imageUrl = record.image_url;
  if (record.type === "image_url" || (imageUrl && typeof imageUrl === "object")) {
    if (imageUrl && typeof imageUrl === "object") {
      const imageRecord = imageUrl as Record<string, unknown>;
      if (typeof imageRecord.url === "string") {
        return imageUrlAsMarkdown(imageRecord.url);
      }
    }
  }

  return "";
}

function readContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map(readContentPart).join("");
  }
  return readContentPart(content);
}

function readDelta(payload: unknown) {
  if (!payload || typeof payload !== "object" || !("choices" in payload)) {
    return "";
  }

  const choices = payload.choices;
  if (!Array.isArray(choices)) return "";

  const firstChoice = choices[0] as
    | {
        delta?: { content?: unknown; images?: unknown };
        message?: { content?: unknown; images?: unknown };
      }
    | undefined;

  const content =
    firstChoice?.delta?.content ?? firstChoice?.message?.content ?? "";
  const images =
    firstChoice?.delta?.images ?? firstChoice?.message?.images ?? "";

  return `${readContent(content)}${readContent(images)}`;
}

function readAudioDelta(payload: unknown): ChatAudioDelta | null {
  if (!payload || typeof payload !== "object" || !("choices" in payload)) {
    return null;
  }
  const choices = payload.choices;
  if (!Array.isArray(choices)) return null;
  const firstChoice = choices[0] as
    | {
        delta?: { audio?: unknown };
        message?: { audio?: unknown };
      }
    | undefined;
  const audio = firstChoice?.delta?.audio ?? firstChoice?.message?.audio;
  if (!audio || typeof audio !== "object") return null;
  const record = audio as Record<string, unknown>;
  const data = typeof record.data === "string" ? record.data : undefined;
  const transcript =
    typeof record.transcript === "string" ? record.transcript : undefined;
  return data || transcript ? { data, transcript } : null;
}

function readStreamError(payload: unknown) {
  if (!payload || typeof payload !== "object" || !("error" in payload)) {
    return "";
  }

  const error = payload.error;
  if (typeof error === "string") return error;
  if (
    error &&
    typeof error === "object" &&
    "message" in error &&
    typeof error.message === "string"
  ) {
    return error.message;
  }

  return "";
}

function readFinishReason(payload: unknown) {
  if (!payload || typeof payload !== "object" || !("choices" in payload)) {
    return "";
  }
  const choices = payload.choices;
  if (!Array.isArray(choices)) return "";
  for (const choice of choices) {
    if (
      choice &&
      typeof choice === "object" &&
      "finish_reason" in choice &&
      typeof choice.finish_reason === "string" &&
      choice.finish_reason
    ) {
      return choice.finish_reason;
    }
  }
  return "";
}

function handleSseEvent(
  eventText: string,
  onDelta: (text: string) => void,
  onRouteReceipt?: (receipt: RouteReceipt) => void,
  onAudioDelta?: (audio: ChatAudioDelta) => void,
  onMessageEnd?: () => void,
) {
  const eventName = eventText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find((line) => line.startsWith("event:"))
    ?.slice(6)
    .trim();
  const dataLines = eventText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim());

  if (eventName === "message_end") {
    onMessageEnd?.();
    return;
  }

  for (const data of dataLines) {
    if (!data) continue;
    if (data === "[DONE]") {
      onMessageEnd?.();
      continue;
    }

    let payload: unknown;
    try {
      payload = JSON.parse(data) as unknown;
    } catch {
      continue;
    }

    if (eventName === "route_receipt") {
      if (onRouteReceipt && payload && typeof payload === "object") {
        onRouteReceipt(payload as RouteReceipt);
      }
      continue;
    }

    const streamError = readStreamError(payload);
    if (streamError) {
      throw new Error(streamError);
    }

    const delta = readDelta(payload);
    if (delta) onDelta(delta);
    const audio = readAudioDelta(payload);
    if (audio) onAudioDelta?.(audio);
    if (readFinishReason(payload)) onMessageEnd?.();
  }
}

export async function fetchChatStream({
  modelId,
  messages,
  gateway = "default",
  routing,
  compression,
  responseAudio,
  temperature = 0.7,
  topP,
  maxTokens = 2048,
  seed,
  stop,
  toolMode = "none",
  toolNames = "",
  maxToolIterations = 5,
  promptSuffix = "",
  signal,
  onRuntimeMeta,
  onRouteReceipt,
  onAudioDelta,
  onMessageEnd,
  onDelta,
}: FetchChatStreamOptions) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model_id: modelId,
      messages,
      gateway,
      routing,
      compression,
      response_audio: responseAudio,
      temperature,
      top_p: topP,
      max_tokens: maxTokens,
      seed,
      stop,
      tool_mode: toolMode,
      tool_names: toolNames,
      max_tool_iterations: maxToolIterations,
      prompt_suffix: promptSuffix,
    }),
    signal,
  });

  if (!response.ok) {
    let message = fallbackErrorMessage;
    let errorPayload: unknown = null;
    try {
      errorPayload = (await response.json()) as unknown;
      message = parseErrorMessage(errorPayload);
    } catch {
      message = response.statusText || message;
    }
    console.error("ModelMirror chat request failed", {
      status: response.status,
      statusText: response.statusText,
      error: errorPayload,
    });
    throw new Error(message);
  }

  const runtimeRunId = response.headers.get("X-ModelMirror-Runtime-Run-Id") ?? "";
  const runtimeTaskId = response.headers.get("X-ModelMirror-Runtime-Task-Id") ?? "";
  const runtimeToolMode = response.headers.get("X-ModelMirror-Tool-Mode") ?? "";
  if (onRuntimeMeta && (runtimeRunId || runtimeTaskId)) {
    onRuntimeMeta({
      runId: runtimeRunId,
      taskId: runtimeTaskId,
      toolMode: runtimeToolMode,
    });
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("当前浏览器不支持流式响应。");
  }

  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let messageEnded = false;
  const emitMessageEnd = () => {
    if (messageEnded) return;
    messageEnded = true;
    onMessageEnd?.();
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split(/\r?\n\r?\n/);
    buffer = events.pop() ?? "";

    for (const eventText of events) {
      try {
        handleSseEvent(
          eventText,
          onDelta,
          onRouteReceipt,
          onAudioDelta,
          emitMessageEnd,
        );
      } catch (error) {
        console.error("ModelMirror chat stream event failed", error);
        throw error;
      }
    }
  }

  buffer += decoder.decode();
  if (buffer.trim()) {
    try {
      handleSseEvent(
        buffer,
        onDelta,
        onRouteReceipt,
        onAudioDelta,
        emitMessageEnd,
      );
    } catch (error) {
      console.error("ModelMirror chat stream tail failed", error);
      throw error;
    }
  }
  if (!messageEnded) {
    throw new Error(
      "流式响应在完成标记到达前中断。已保留收到的内容，请检查模型服务连接后重试。",
    );
  }
}
