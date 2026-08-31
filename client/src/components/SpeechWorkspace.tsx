import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { Model } from "../data/models";
import {
  AudioRequestError,
  DEFAULT_SPEECH_VOICE,
  generateSpeechAudio,
  speechVoiceLabel,
  type AudioProviderRouteReceipt,
  type SpeechResponseFormat,
} from "../utils/speechAudio";
import BrandLogo from "./BrandLogo";
import ProviderRouteReceiptSummary from "./ProviderRouteReceiptSummary";
import ResourceNav from "./ResourceNav";

const MAX_TEXT_CHARS = 4_000;

type SpeechStatus =
  | "idle"
  | "generating"
  | "succeeded"
  | "failed"
  | "cancelled";

interface SpeechReceipt {
  requestId: string;
  actualModel: string;
  provider: string;
  costKind: "actual" | "estimated" | "unavailable";
  outputBytes: number | null;
}

interface SpeechWorkspaceProps {
  model: Model;
}

interface SpeechCatalogProfile {
  model_id: string;
  invocable: boolean;
  interaction_status: "ready" | "planned" | "disabled";
  chat_modes: string[];
  output_formats: string[];
  voices: string[];
}

interface SpeechCatalogResponse {
  profiles: SpeechCatalogProfile[];
}

interface ProviderWorkloadPublicStatus {
  feature_enabled: boolean;
  status: "legacy" | "managed_required" | "degraded_required";
  available: boolean;
  reason_code: string;
  certified_voice: string | null;
  certified_response_format: SpeechResponseFormat | null;
}

function formatBytes(bytes: number | null) {
  if (bytes === null) return "未提供";
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  }
  return `${Math.max(1, Math.round(bytes / 1024))} KiB`;
}

export default function SpeechWorkspace({ model }: SpeechWorkspaceProps) {
  const [text, setText] = useState("");
  const [voice, setVoice] = useState("");
  const [availableVoices, setAvailableVoices] = useState<string[]>([]);
  const [responseFormat, setResponseFormat] =
    useState<SpeechResponseFormat>("mp3");
  const [managedCertification, setManagedCertification] = useState(false);
  const [controlBlocked, setControlBlocked] = useState(true);
  const [isLoadingProfile, setIsLoadingProfile] = useState(true);
  const [profileError, setProfileError] = useState("");
  const [speed, setSpeed] = useState(1);
  const [status, setStatus] = useState<SpeechStatus>("idle");
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [receipt, setReceipt] = useState<SpeechReceipt | null>(null);
  const [providerRouteReceipt, setProviderRouteReceipt] =
    useState<AudioProviderRouteReceipt | null>(null);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const isGenerating = status === "generating";
  const characterCount = text.length;
  const canGenerate =
    text.trim().length > 0 &&
    characterCount <= MAX_TEXT_CHARS &&
    Boolean(voice) &&
    !isLoadingProfile &&
    !controlBlocked &&
    !isGenerating;

  useEffect(() => {
    document.title = `生成语音 · ${model.name} · 模镜`;
  }, [model.name]);

  useEffect(() => {
    const controller = new AbortController();
    setIsLoadingProfile(true);
    setProfileError("");
    setManagedCertification(false);
    setControlBlocked(true);
    const loadProfile = async () => {
      const params = new URLSearchParams({
        entry_id: "multimodal_speech",
        model_id: model.id,
        execution_shape: "audio_speech",
      });
      let response: Response;
      try {
        response = await fetch(
          `/api/models/provider-workload-control?${params}`,
          { signal: controller.signal },
        );
      } catch (loadError) {
        if (controller.signal.aborted) throw loadError;
        throw new Error(
          "无法读取 Provider 控制面状态，已安全阻断本次付费语音生成；请检查服务后重试。",
        );
      }
      if (!response.ok) {
        throw new Error(
          "无法读取 Provider 控制面状态，已安全阻断本次付费语音生成；请检查服务后重试。",
        );
      }
      const control = (await response.json()) as ProviderWorkloadPublicStatus;
      const legacyAllowed =
        control.feature_enabled === false || control.status === "legacy";

      if (!legacyAllowed && control.available) {
        const certifiedVoice = control.certified_voice?.trim() ?? "";
        const certifiedFormat = control.certified_response_format;
        setManagedCertification(true);
        if (!certifiedVoice || !certifiedFormat) {
          setAvailableVoices([]);
          setVoice("");
          setResponseFormat("mp3");
          setProfileError(
            "当前 Managed Provider 资格缺少已认证声线或外部输出格式，请先在设置页重新认证。",
          );
          return;
        }
        setAvailableVoices([certifiedVoice]);
        setVoice(certifiedVoice);
        setResponseFormat(certifiedFormat);
        setControlBlocked(false);
        return;
      }

      if (!legacyAllowed) {
        setAvailableVoices([]);
        setVoice("");
        setResponseFormat("mp3");
        setProfileError(
          `Provider 控制面处于 ${control.status}，该模型当前不可用，已在发送前阻断本次付费语音生成。原因：${control.reason_code}`,
        );
        return;
      }

      response = await fetch("/api/multimodal/audio/models", {
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error("暂时无法读取语音模型能力。");
      }
      const catalog = (await response.json()) as SpeechCatalogResponse;
      const profile = catalog.profiles.find(
        (item) =>
          item.model_id === model.id &&
          item.invocable &&
          item.interaction_status === "ready" &&
          item.chat_modes.includes("synthesize_speech") &&
          item.output_formats.some(
            (format) => format === "mp3" || format === "wav",
          ) &&
          item.voices.length > 0,
      );
      if (!profile) {
        setAvailableVoices([]);
        setVoice("");
        setResponseFormat("mp3");
        setProfileError(
          "该模型当前没有已验证的语音格式和声线，请返回模型招聘会选择可用语音模型。",
        );
        return;
      }
      setResponseFormat(profile.output_formats.includes("mp3") ? "mp3" : "wav");
      setAvailableVoices(profile.voices);
      setControlBlocked(false);
      setVoice((current) => {
        if (profile.voices.includes(current)) return current;
        if (
          model.id === "microsoft/mai-voice-2" &&
          profile.voices.includes(DEFAULT_SPEECH_VOICE)
        ) {
          return DEFAULT_SPEECH_VOICE;
        }
        return profile.voices[0];
      });
    };
    void loadProfile()
      .catch((loadError) => {
        if (
          loadError instanceof DOMException &&
          loadError.name === "AbortError"
        ) {
          return;
        }
        setAvailableVoices([]);
        setVoice("");
        setResponseFormat("mp3");
        setControlBlocked(true);
        setProfileError(
          loadError instanceof Error
            ? loadError.message
            : "暂时无法读取语音模型能力。",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoadingProfile(false);
      });
    return () => controller.abort();
  }, [model.id]);

  useEffect(() => {
    if (!audioBlob) {
      setAudioUrl("");
      return undefined;
    }
    const nextUrl = URL.createObjectURL(audioBlob);
    setAudioUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [audioBlob]);

  useEffect(
    () => () => {
      abortRef.current?.abort();
    },
    [],
  );

  async function generateSpeech() {
    if (!canGenerate) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setStatus("generating");
    setAudioBlob(null);
    setReceipt(null);
    setProviderRouteReceipt(null);
    setError("");

    try {
      const result = await generateSpeechAudio({
        modelId: model.id,
        input: text.trim(),
        voice,
        responseFormat,
        speed,
        signal: controller.signal,
      });
      setAudioBlob(result.blob);
      setReceipt({
        requestId: result.requestId,
        actualModel: result.actualModel,
        provider: result.provider,
        costKind: result.costKind,
        outputBytes: result.outputBytes,
      });
      setProviderRouteReceipt(result.providerRouteReceipt);
      setStatus("succeeded");
    } catch (requestError) {
      if (
        requestError instanceof DOMException &&
        requestError.name === "AbortError"
      ) {
        setStatus("cancelled");
        setError("");
      } else {
        setProviderRouteReceipt(
          requestError instanceof AudioRequestError
            ? requestError.providerRouteReceipt
            : null,
        );
        setStatus("failed");
        setError(
          requestError instanceof Error
            ? requestError.message
            : "语音没有生成完成，请稍后重试。",
        );
      }
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    }
  }

  function clearResult() {
    if (isGenerating) return;
    setAudioBlob(null);
    setReceipt(null);
    setProviderRouteReceipt(null);
    setStatus("idle");
    setError("");
  }

  return (
    <main className="museum-grid min-h-screen pb-28 pt-5 text-slate-100 lg:pb-12 lg:pt-24">
      <ResourceNav activeResource="models" />
      <div className="mx-auto w-full max-w-[1180px] px-4 py-5 sm:px-6 lg:px-8">
        <header className="border-y border-hire-300/20 bg-ink-950/72 py-5 backdrop-blur-xl">
          <BrandLogo className="mb-4 lg:hidden" />
          <Link
            className="inline-flex rounded-full border border-white/10 bg-white/[0.05] px-3 py-1.5 text-sm font-medium text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
            to="/models"
          >
            返回招聘会现场
          </Link>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="rounded-full border border-violet-300/30 bg-violet-300/10 px-3 py-1.5 text-xs font-semibold text-violet-100">
              文字转语音
            </span>
            <span className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1.5 text-xs text-slate-300">
              文字和音频不会保存在模镜
            </span>
          </div>
          <h1 className="mt-4 text-2xl font-semibold text-white sm:text-4xl">
            使用 {model.name} 生成语音
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
            输入需要朗读的文字，选择已验证声线和语速，生成后可直接播放或下载认证格式音频。
          </p>
        </header>

        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:items-start">
          <section className="surface-panel overflow-hidden rounded-lg">
            <div className="border-b border-white/10 px-5 py-5 sm:px-6">
              <h2 className="text-lg font-semibold text-white">准备朗读内容</h2>
              <p className="mt-1 text-sm leading-6 text-slate-400">
                单次最多 4,000 个字符。生成期间不会清空文字，失败后可直接重试。
              </p>
            </div>

            <div className="space-y-5 p-5 sm:p-6">
              <div>
                <div className="mb-2 flex items-center justify-between gap-3">
                  <label
                    className="text-sm font-semibold text-slate-200"
                    htmlFor="speech-input"
                  >
                    需要朗读的文字
                  </label>
                  <span
                    className={`text-xs tabular-nums ${
                      characterCount >= MAX_TEXT_CHARS
                        ? "text-amber-200"
                        : "text-slate-400"
                    }`}
                  >
                    {characterCount.toLocaleString("zh-CN")} / 4,000
                  </span>
                </div>
                <textarea
                  className="min-h-48 w-full resize-y rounded-lg border border-white/15 bg-ink-950 px-4 py-3 text-[15px] leading-7 text-white outline-none transition placeholder:text-slate-500 focus:border-brand-300/60 focus:ring-4 focus:ring-brand-300/10 disabled:cursor-not-allowed disabled:opacity-70"
                  disabled={isGenerating || controlBlocked}
                  id="speech-input"
                  maxLength={MAX_TEXT_CHARS}
                  onChange={(event) => {
                    setText(event.target.value);
                    if (status !== "idle") clearResult();
                  }}
                  placeholder="例如：欢迎使用模镜。这里可以把文字转换为便于播放和下载的语音。"
                  value={text}
                />
              </div>

              <div className="grid gap-5 sm:grid-cols-2">
                <div>
                  <label
                    className="mb-2 block text-sm font-semibold text-slate-200"
                    htmlFor="speech-voice"
                  >
                    声线
                  </label>
                  <select
                    className="w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-3 text-sm text-white outline-none transition focus:border-brand-300/60 focus:ring-4 focus:ring-brand-300/10 disabled:cursor-not-allowed disabled:opacity-60"
                    disabled={
                      isGenerating ||
                      isLoadingProfile ||
                      availableVoices.length === 0
                    }
                    id="speech-voice"
                    onChange={(event) => {
                      setVoice(event.target.value);
                      clearResult();
                    }}
                    value={voice}
                  >
                    {isLoadingProfile ? (
                      <option value="">正在读取声线…</option>
                    ) : null}
                    {!isLoadingProfile && availableVoices.length === 0 ? (
                      <option value="">暂无可用声线</option>
                    ) : null}
                    {availableVoices.map((item) => (
                      <option key={item} value={item}>
                        {speechVoiceLabel(item)}
                      </option>
                    ))}
                  </select>
                  <p className="mt-2 text-xs leading-5 text-slate-400">
                    {managedCertification
                      ? `Managed Provider 资格固定使用 ${speechVoiceLabel(voice)}，外部输出为 ${responseFormat.toUpperCase()}；更换参数需要重新认证。`
                      : "仅显示实时目录中仍存在且已完成行为验证的声线。"}
                  </p>
                </div>

                <div>
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <label
                      className="text-sm font-semibold text-slate-200"
                      htmlFor="speech-speed"
                    >
                      语速
                    </label>
                    <span className="text-sm font-semibold tabular-nums text-brand-100">
                      {speed.toFixed(2)}×
                    </span>
                  </div>
                  <input
                    className="mt-3 w-full accent-cyan-300"
                    disabled={isGenerating || controlBlocked}
                    id="speech-speed"
                    max="2"
                    min="0.5"
                    onChange={(event) => {
                      setSpeed(Number(event.target.value));
                      clearResult();
                    }}
                    step="0.05"
                    type="range"
                    value={speed}
                  />
                  <div className="mt-1 flex justify-between text-xs text-slate-500">
                    <span>较慢 0.5×</span>
                    <span>较快 2.0×</span>
                  </div>
                </div>
              </div>

              {isGenerating ? (
                <div
                  aria-live="polite"
                  className="rounded-lg bg-brand-300/[0.07] p-4"
                  role="status"
                >
                  <div className="flex items-center gap-3">
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-brand-200/30 border-t-brand-200" />
                    <div>
                      <p className="text-sm font-semibold text-brand-100">
                        正在生成完整 {responseFormat.toUpperCase()}
                      </p>
                      <p className="mt-1 text-xs leading-5 text-slate-400">
                        完成校验后才会显示播放器，请保持此页面打开。
                      </p>
                    </div>
                  </div>
                </div>
              ) : null}

              {error ? (
                <div
                  className="rounded-lg border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm leading-6 text-rose-100"
                  role="alert"
                >
                  {error}
                </div>
              ) : null}

              <ProviderRouteReceiptSummary
                receipts={providerRouteReceipt}
                title="语音生成控制面"
              />

              {profileError ? (
                <div
                  className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-4 py-3 text-sm leading-6 text-amber-100"
                  role="status"
                >
                  {profileError}
                </div>
              ) : null}

              {status === "cancelled" ? (
                <div
                  className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-4 py-3 text-sm text-amber-100"
                  role="status"
                >
                  已取消，文字仍保留，可以重新生成。
                </div>
              ) : null}

              <div className="flex flex-wrap items-center gap-3 border-t border-white/10 pt-5">
                <button
                  className="rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-45"
                  disabled={!canGenerate}
                  onClick={() => void generateSpeech()}
                  type="button"
                >
                  {status === "failed" || status === "cancelled"
                    ? "重新生成"
                    : "生成语音"}
                </button>
                {isGenerating ? (
                  <button
                    className="rounded-full border border-white/15 px-4 py-2.5 text-sm font-semibold text-slate-200 transition hover:border-rose-300/40 hover:bg-rose-300/10 hover:text-rose-100"
                    onClick={() => abortRef.current?.abort()}
                    type="button"
                  >
                    取消生成
                  </button>
                ) : null}
                {!text.trim() ? (
                  <span className="text-sm text-slate-400">
                    输入文字后即可生成
                  </span>
                ) : null}
              </div>
            </div>
          </section>

          <aside className="surface-card rounded-lg p-5 lg:sticky lg:top-24">
            <h2 className="font-semibold text-white">本次生成</h2>
            <dl className="mt-4 space-y-4 text-sm">
              <div>
                <dt className="text-slate-400">模型</dt>
                <dd className="mt-1 break-words font-medium text-slate-100">
                  {model.name}
                </dd>
              </div>
              <div>
                <dt className="text-slate-400">输出格式</dt>
                <dd className="mt-1 text-slate-200">
                  {responseFormat.toUpperCase()}
                </dd>
              </div>
              <div>
                <dt className="text-slate-400">隐私</dt>
                <dd className="mt-1 leading-6 text-slate-200">
                  文字和生成音频仅用于本次请求，不写入模镜数据库。
                </dd>
              </div>
            </dl>
          </aside>
        </div>

        {audioUrl && receipt ? (
          <section className="surface-panel mt-6 overflow-hidden rounded-lg">
            <div className="flex flex-col gap-3 border-b border-white/10 px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
              <div>
                <h2 className="text-lg font-semibold text-white">生成结果</h2>
                <p className="mt-1 text-xs text-slate-400">
                  {receipt.actualModel} · {receipt.provider} ·{" "}
                  {receipt.costKind === "unavailable"
                    ? "费用待网关结算"
                    : "费用已结算"}
                </p>
              </div>
              <a
                className="self-start rounded-full border border-brand-300/35 bg-brand-300/10 px-4 py-2 text-sm font-semibold text-brand-100 transition hover:border-brand-300/60 hover:bg-brand-300/15 sm:self-auto"
                download={`modelmirror-speech.${responseFormat}`}
                href={audioUrl}
              >
                下载 {responseFormat.toUpperCase()}
              </a>
            </div>
            <div className="p-5 sm:p-6">
              <audio
                aria-label="播放生成的语音"
                className="w-full"
                controls
                preload="metadata"
                src={audioUrl}
              />
              <div className="mt-4 grid gap-3 text-xs text-slate-400 sm:grid-cols-2">
                <p>
                  音频大小：{" "}
                  <span className="text-slate-200">
                    {formatBytes(receipt.outputBytes)}
                  </span>
                </p>
                <p className="break-all sm:text-right">
                  请求 ID：{" "}
                  <span className="font-mono text-slate-300">
                    {receipt.requestId || "未提供"}
                  </span>
                </p>
              </div>
            </div>
          </section>
        ) : null}
      </div>
    </main>
  );
}
