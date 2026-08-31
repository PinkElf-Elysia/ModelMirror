import type {
  ProviderRouteCallReceipt,
  ProviderRouteReceipt,
} from "../components/AgencyExpertTeamTypes";

export const DEFAULT_SPEECH_MODEL_ID = "microsoft/mai-voice-2";
export const DEFAULT_SPEECH_VOICE = "en-US-Harper:MAI-Voice-2";
export type SpeechResponseFormat = "mp3" | "wav";

export type AudioProviderRouteReceipt = ProviderRouteReceipt;

const PROVIDER_ROUTE_STATUSES = new Set([
  "running",
  "passed",
  "failed",
  "uncertain",
  "cancelled",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function optionalString(value: unknown) {
  return typeof value === "string" || value === null ? value : undefined;
}

function optionalNumber(value: unknown) {
  return (typeof value === "number" && Number.isFinite(value)) || value === null
    ? value
    : undefined;
}

function parseProviderRouteCall(
  value: unknown,
): ProviderRouteCallReceipt | null {
  if (!isRecord(value)) return null;
  if (
    typeof value.call_sequence !== "number" ||
    !Number.isInteger(value.call_sequence) ||
    value.call_sequence < 0 ||
    typeof value.model_id !== "string" ||
    !value.model_id.trim() ||
    typeof value.dispatched !== "boolean" ||
    typeof value.status !== "string" ||
    !PROVIDER_ROUTE_STATUSES.has(value.status)
  ) {
    return null;
  }
  const call: ProviderRouteCallReceipt = {
    call_sequence: value.call_sequence,
    model_id: value.model_id,
    dispatched: value.dispatched,
    status: value.status as ProviderRouteCallReceipt["status"],
  };
  const actualModel = optionalString(value.actual_model);
  const errorCode = optionalString(value.error_code);
  const promptTokens = optionalNumber(value.prompt_tokens);
  const completionTokens = optionalNumber(value.completion_tokens);
  const totalTokens = optionalNumber(value.total_tokens);
  if (actualModel !== undefined) call.actual_model = actualModel;
  if (errorCode !== undefined) call.error_code = errorCode;
  if (promptTokens !== undefined) call.prompt_tokens = promptTokens;
  if (completionTokens !== undefined) {
    call.completion_tokens = completionTokens;
  }
  if (totalTokens !== undefined) call.total_tokens = totalTokens;
  return call;
}

export function parseAudioProviderRouteReceipt(
  value: unknown,
): AudioProviderRouteReceipt | null {
  if (!isRecord(value)) return null;
  const calls = Array.isArray(value.calls)
    ? value.calls.map(parseProviderRouteCall)
    : [];
  if (
    value.contract_version !== "modelmirror-provider-workload-routing-v1" ||
    typeof value.entry_id !== "string" ||
    !value.entry_id.trim() ||
    value.routing_mode !== "managed_required" ||
    typeof value.run_reference !== "string" ||
    typeof value.status !== "string" ||
    !PROVIDER_ROUTE_STATUSES.has(value.status) ||
    typeof value.call_count !== "number" ||
    !Number.isInteger(value.call_count) ||
    value.call_count < 0 ||
    !Array.isArray(value.reason_codes) ||
    !value.reason_codes.every((item) => typeof item === "string") ||
    !Array.isArray(value.calls) ||
    calls.some((call) => call === null)
  ) {
    return null;
  }
  return {
    contract_version: value.contract_version,
    entry_id: value.entry_id,
    routing_mode: value.routing_mode,
    run_reference: value.run_reference,
    status: value.status,
    call_count: value.call_count,
    reason_codes: [...value.reason_codes],
    calls: calls as ProviderRouteCallReceipt[],
  } as AudioProviderRouteReceipt;
}

export function parseAudioProviderRouteReceipts(
  value: unknown,
): AudioProviderRouteReceipt[] {
  if (!Array.isArray(value)) return [];
  return value
    .map(parseAudioProviderRouteReceipt)
    .filter((receipt): receipt is AudioProviderRouteReceipt => receipt !== null);
}

export class AudioRequestError extends Error {
  readonly providerRouteReceipt: AudioProviderRouteReceipt | null;

  constructor(
    message: string,
    providerRouteReceipt: AudioProviderRouteReceipt | null,
  ) {
    super(message);
    this.name = "AudioRequestError";
    this.providerRouteReceipt = providerRouteReceipt;
  }
}

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
  providerRouteReceipt: AudioProviderRouteReceipt | null;
}

interface GenerateSpeechAudioOptions {
  input: string;
  modelId?: string;
  voice?: string;
  responseFormat?: SpeechResponseFormat;
  speed?: number;
  signal?: AbortSignal;
}

function statusErrorMessage(status: number) {
  if (status === 402) {
    return "模型服务余额不足，请检查 OpenRouter 账户。";
  }
  if (status === 429) {
    return "语音生成服务当前请求较多，请稍后重试。";
  }
  return "语音没有生成完成，请检查连接后重试。";
}

async function responseError(response: Response) {
  let providerRouteReceipt: AudioProviderRouteReceipt | null = null;
  try {
    const payload = (await response.json()) as {
      error?: unknown;
      detail?: unknown;
    };
    if (isRecord(payload.detail)) {
      providerRouteReceipt = parseAudioProviderRouteReceipt(
        payload.detail.route_receipt,
      );
    }
    if (typeof payload.error === "string" && payload.error.trim()) {
      return new AudioRequestError(payload.error, providerRouteReceipt);
    }
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return new AudioRequestError(payload.detail, providerRouteReceipt);
    }
    if (
      payload.detail &&
      typeof payload.detail === "object" &&
      "message" in payload.detail &&
      typeof payload.detail.message === "string" &&
      payload.detail.message.trim()
    ) {
      return new AudioRequestError(
        payload.detail.message,
        providerRouteReceipt,
      );
    }
  } catch {
    // The backend intentionally hides provider response bodies.
  }
  return new AudioRequestError(
    statusErrorMessage(response.status),
    providerRouteReceipt,
  );
}

function headerValue(response: Response, name: string) {
  return response.headers.get(name)?.trim() ?? "";
}

function responseProviderRouteReceipt(response: Response) {
  const raw = headerValue(
    response,
    "x-modelmirror-provider-route-receipt",
  );
  if (!raw) return null;
  try {
    return parseAudioProviderRouteReceipt(JSON.parse(raw));
  } catch {
    return null;
  }
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
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": window.crypto.randomUUID(),
    },
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
    throw await responseError(response);
  }
  const providerRouteReceipt = responseProviderRouteReceipt(response);
  const contentType = headerValue(response, "content-type")
    .split(";", 1)[0]
    .toLowerCase();
  const expectedContentType =
    responseFormat === "wav" ? "audio/wav" : "audio/mpeg";
  if (contentType !== expectedContentType) {
    throw new AudioRequestError(
      `语音服务没有返回标准 ${responseFormat.toUpperCase()}，请稍后重试。`,
      providerRouteReceipt,
    );
  }
  const blob = await response.blob();
  if (blob.size <= 0) {
    throw new AudioRequestError(
      "语音服务没有返回可播放的内容，请稍后重试。",
      providerRouteReceipt,
    );
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
    providerRouteReceipt,
  };
}
