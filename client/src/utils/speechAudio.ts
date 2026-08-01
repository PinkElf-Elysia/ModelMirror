export const DEFAULT_SPEECH_MODEL_ID = "microsoft/mai-voice-2";
export const DEFAULT_SPEECH_VOICE = "en-US-Harper:MAI-Voice-2";
export type SpeechResponseFormat = "mp3" | "wav";

const SPEECH_VOICE_LABELS: Record<string, string> = {
  "8ef4a238714b45718ce04243307c57a7": "轻快女声",
  "802e3bc2b27e49c2995d23ef70e6ac89": "活力男声",
};

export function speechVoiceLabel(voice: string) {
  return SPEECH_VOICE_LABELS[voice] ?? voice;
}

export interface SpeechAudioResult {
  blob: Blob;
  requestId: string;
  actualModel: string;
  provider: string;
  costKind: "actual" | "estimated" | "unavailable";
  outputBytes: number;
  responseFormat: SpeechResponseFormat;
}

interface GenerateSpeechAudioOptions {
  input: string;
  modelId?: string;
  voice?: string;
  responseFormat?: SpeechResponseFormat;
  speed?: number;
  signal?: AbortSignal;
}

async function responseErrorMessage(response: Response) {
  try {
    const payload = (await response.json()) as {
      error?: unknown;
      detail?: unknown;
    };
    if (typeof payload.error === "string" && payload.error.trim()) {
      return payload.error;
    }
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail;
    }
    if (
      payload.detail &&
      typeof payload.detail === "object" &&
      "message" in payload.detail &&
      typeof payload.detail.message === "string" &&
      payload.detail.message.trim()
    ) {
      return payload.detail.message;
    }
  } catch {
    // The backend intentionally hides provider response bodies.
  }
  if (response.status === 402) {
    return "模型服务余额不足，请检查 OpenRouter 账户。";
  }
  if (response.status === 429) {
    return "语音生成服务当前请求较多，请稍后重试。";
  }
  return "语音没有生成完成，请检查连接后重试。";
}

function headerValue(response: Response, name: string) {
  return response.headers.get(name)?.trim() ?? "";
}

export async function generateSpeechAudio({
  input,
  modelId = DEFAULT_SPEECH_MODEL_ID,
  voice = DEFAULT_SPEECH_VOICE,
  responseFormat = "mp3",
  speed = 1,
  signal,
}: GenerateSpeechAudioOptions): Promise<SpeechAudioResult> {
  const response = await fetch("/api/multimodal/speech", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model_id: modelId,
      input,
      voice,
      response_format: responseFormat,
      speed,
    }),
    signal,
  });
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response));
  }
  const contentType = headerValue(response, "content-type")
    .split(";", 1)[0]
    .toLowerCase();
  const expectedContentType =
    responseFormat === "wav" ? "audio/wav" : "audio/mpeg";
  if (contentType !== expectedContentType) {
    throw new Error(
      `语音服务没有返回标准 ${responseFormat.toUpperCase()}，请稍后重试。`,
    );
  }
  const blob = await response.blob();
  if (blob.size <= 0) {
    throw new Error("语音服务没有返回可播放的内容，请稍后重试。");
  }
  const outputBytesHeader = Number(
    headerValue(response, "x-modelmirror-output-bytes"),
  );
  const rawCostKind = headerValue(response, "x-modelmirror-cost-kind");
  const costKind: SpeechAudioResult["costKind"] =
    rawCostKind === "actual" || rawCostKind === "estimated"
      ? rawCostKind
      : "unavailable";
  return {
    blob,
    requestId: headerValue(response, "x-modelmirror-request-id"),
    actualModel:
      headerValue(response, "x-modelmirror-actual-model") || modelId,
    provider:
      headerValue(response, "x-modelmirror-provider") || "openrouter",
    costKind,
    outputBytes:
      Number.isFinite(outputBytesHeader) && outputBytesHeader > 0
        ? outputBytesHeader
        : blob.size,
    responseFormat,
  };
}
