export interface JsonStreamEvent {
  event?: string;
  [key: string]: unknown;
}

interface FetchJsonEventStreamOptions {
  url: string;
  payload: unknown;
  signal?: AbortSignal;
  onEvent: (event: JsonStreamEvent) => void;
}

function readErrorMessage(payload: unknown) {
  if (
    payload &&
    typeof payload === "object" &&
    "error" in payload &&
    typeof payload.error === "string"
  ) {
    return payload.error;
  }

  if (
    payload &&
    typeof payload === "object" &&
    "error" in payload &&
    payload.error &&
    typeof payload.error === "object" &&
    "message" in payload.error &&
    typeof payload.error.message === "string"
  ) {
    return payload.error.message;
  }

  return "请求失败，请稍后重试。";
}

function parseEventBlock(block: string, onEvent: (event: JsonStreamEvent) => void) {
  const data = block
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim())
    .join("\n");

  if (!data || data === "[DONE]") return;

  let event: JsonStreamEvent;
  try {
    event = JSON.parse(data) as JsonStreamEvent;
  } catch (error) {
    console.warn("ModelMirror JSON event parse failed", error, data);
    return;
  }
  onEvent(event);
}

export async function fetchJsonEventStream({
  url,
  payload,
  signal,
  onEvent,
}: FetchJsonEventStreamOptions) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });

  if (!response.ok) {
    let message = response.statusText || "请求失败，请稍后重试。";
    try {
      message = readErrorMessage((await response.json()) as unknown);
    } catch {
      // Keep status text.
    }
    throw new Error(message);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    throw new Error("当前浏览器不支持流式响应。");
  }

  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";

    for (const block of blocks) {
      parseEventBlock(block, onEvent);
    }
  }

  if (buffer.trim()) {
    parseEventBlock(buffer, onEvent);
  }
}
