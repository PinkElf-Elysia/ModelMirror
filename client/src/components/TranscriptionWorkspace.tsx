import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from "react";
import { Link } from "react-router-dom";
import type { Model } from "../data/models";
import BrandLogo from "./BrandLogo";
import ResourceNav from "./ResourceNav";

const MAX_AUDIO_BYTES = 25 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = new Set([
  "wav",
  "mp3",
  "flac",
  "m4a",
  "ogg",
  "webm",
  "aac",
]);
const AUDIO_ACCEPT = [
  ".wav",
  ".mp3",
  ".flac",
  ".m4a",
  ".ogg",
  ".webm",
  ".aac",
  "audio/wav",
  "audio/mpeg",
  "audio/flac",
  "audio/mp4",
  "audio/ogg",
  "audio/webm",
  "audio/aac",
].join(",");

const LANGUAGE_OPTIONS = [
  { value: "auto", label: "自动识别" },
  { value: "zh", label: "中文" },
  { value: "en", label: "英语" },
  { value: "ja", label: "日语" },
  { value: "ko", label: "韩语" },
  { value: "fr", label: "法语" },
  { value: "de", label: "德语" },
  { value: "es", label: "西班牙语" },
];

type TranscriptionStatus =
  | "idle"
  | "uploading"
  | "processing"
  | "succeeded"
  | "failed"
  | "cancelled";

interface TranscriptionUsage {
  audio_seconds: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  cost_usd: number | null;
  cost_kind: "actual" | "estimated" | "unavailable";
}

interface TranscriptionResponse {
  text: string;
  requested_model: string;
  actual_model: string;
  provider: string;
  request_id: string;
  usage: TranscriptionUsage;
}

interface TranscriptionWorkspaceProps {
  model: Model;
}

interface ProgressUpdate {
  phase: "uploading" | "processing";
  percent: number;
}

function formatBytes(bytes: number) {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  }
  return `${Math.max(1, Math.round(bytes / 1024))} KiB`;
}

function fileExtension(file: File) {
  return file.name.split(".").pop()?.toLowerCase() ?? "";
}

function validateAudioFile(file: File) {
  if (!ACCEPTED_EXTENSIONS.has(fileExtension(file))) {
    return "请选择 WAV、MP3、FLAC、M4A、OGG、WebM 或 AAC 音频。";
  }
  if (file.size <= 0) {
    return "这个音频文件没有可读取的内容，请重新选择。";
  }
  if (file.size > MAX_AUDIO_BYTES) {
    return "音频超过 25 MiB，请压缩或裁剪后重新上传。";
  }
  return "";
}

function responsePayload(xhr: XMLHttpRequest): unknown {
  if (xhr.response && typeof xhr.response === "object") {
    return xhr.response;
  }
  if (!xhr.responseText) return null;
  try {
    return JSON.parse(xhr.responseText);
  } catch {
    return null;
  }
}

function responseErrorMessage(payload: unknown, status: number) {
  if (payload && typeof payload === "object") {
    const detail = (payload as { detail?: unknown }).detail;
    if (detail && typeof detail === "object") {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string" && message.trim()) return message;
    }
  }
  if (status === 413) return "音频超过 25 MiB，请压缩或裁剪后重试。";
  if (status === 429) return "模型服务当前请求较多，请稍后重试。";
  if (status === 402) return "模型服务余额不足，请检查 OpenRouter 账户。";
  return "转录没有完成，请检查连接后重试。";
}

function requestTranscription({
  file,
  language,
  modelId,
  signal,
  onProgress,
}: {
  file: File;
  language: string;
  modelId: string;
  signal: AbortSignal;
  onProgress: (update: ProgressUpdate) => void;
}) {
  return new Promise<TranscriptionResponse>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Request aborted", "AbortError"));
      return;
    }

    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("model_id", modelId);
    form.append("language", language);
    form.append("file", file, file.name);

    const cleanup = () => signal.removeEventListener("abort", handleAbort);
    const handleAbort = () => xhr.abort();

    xhr.open("POST", "/api/multimodal/transcriptions");
    xhr.responseType = "json";
    xhr.upload.onprogress = (event) => {
      const percent = event.lengthComputable
        ? Math.min(88, Math.max(2, Math.round((event.loaded / event.total) * 88)))
        : 24;
      onProgress({ phase: "uploading", percent });
    };
    xhr.upload.onload = () => {
      onProgress({ phase: "processing", percent: 92 });
    };
    xhr.onload = () => {
      cleanup();
      const payload = responsePayload(xhr);
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload as TranscriptionResponse);
        return;
      }
      reject(new Error(responseErrorMessage(payload, xhr.status)));
    };
    xhr.onerror = () => {
      cleanup();
      reject(new Error("无法连接转录服务，请确认后端正在运行。"));
    };
    xhr.onabort = () => {
      cleanup();
      reject(new DOMException("Request aborted", "AbortError"));
    };
    signal.addEventListener("abort", handleAbort, { once: true });
    onProgress({ phase: "uploading", percent: 2 });
    xhr.send(form);
  });
}

function costLabel(usage: TranscriptionUsage) {
  if (usage.cost_kind === "actual" && usage.cost_usd !== null) {
    return `$${usage.cost_usd.toFixed(6)}`;
  }
  return "费用待网关结算";
}

export default function TranscriptionWorkspace({
  model,
}: TranscriptionWorkspaceProps) {
  const [file, setFile] = useState<File | null>(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [language, setLanguage] = useState("auto");
  const [status, setStatus] = useState<TranscriptionStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<TranscriptionResponse | null>(null);
  const [error, setError] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [copied, setCopied] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const selectButtonRef = useRef<HTMLButtonElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const copyTimerRef = useRef<number | null>(null);
  const isRunning = status === "uploading" || status === "processing";

  useEffect(() => {
    document.title = `转录音频 · ${model.name} · 模镜`;
  }, [model.name]);

  useEffect(() => {
    if (!file) {
      setAudioUrl("");
      return undefined;
    }
    const nextUrl = URL.createObjectURL(file);
    setAudioUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [file]);

  useEffect(
    () => () => {
      abortRef.current?.abort();
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
    },
    [],
  );

  const resetOutcome = useCallback(() => {
    setResult(null);
    setError("");
    setProgress(0);
    setStatus("idle");
    setCopied(false);
  }, []);

  const chooseFile = useCallback(
    (nextFile: File | undefined) => {
      if (!nextFile || isRunning) return;
      const validationError = validateAudioFile(nextFile);
      if (validationError) {
        setError(validationError);
        setStatus("failed");
        setResult(null);
        return;
      }
      setFile(nextFile);
      resetOutcome();
    },
    [isRunning, resetOutcome],
  );

  function handleFileInput(event: ChangeEvent<HTMLInputElement>) {
    chooseFile(event.target.files?.[0]);
    event.target.value = "";
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setIsDragging(false);
    chooseFile(event.dataTransfer.files?.[0]);
  }

  function removeFile() {
    if (isRunning) return;
    setFile(null);
    resetOutcome();
    selectButtonRef.current?.focus();
  }

  async function runTranscription() {
    if (!file || isRunning) return;
    const validationError = validateAudioFile(file);
    if (validationError) {
      setError(validationError);
      setStatus("failed");
      return;
    }

    const controller = new AbortController();
    abortRef.current = controller;
    setResult(null);
    setError("");
    setCopied(false);

    try {
      const nextResult = await requestTranscription({
        file,
        language,
        modelId: model.id,
        signal: controller.signal,
        onProgress: ({ phase, percent }) => {
          setStatus(phase);
          setProgress(percent);
        },
      });
      setResult(nextResult);
      setProgress(100);
      setStatus("succeeded");
    } catch (requestError) {
      setProgress(0);
      if (
        requestError instanceof DOMException &&
        requestError.name === "AbortError"
      ) {
        setStatus("cancelled");
        setError("");
      } else {
        setStatus("failed");
        setError(
          requestError instanceof Error
            ? requestError.message
            : "转录没有完成，请稍后重试。",
        );
      }
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    }
  }

  async function copyTranscript() {
    if (!result?.text) return;
    try {
      await navigator.clipboard.writeText(result.text);
      setCopied(true);
      if (copyTimerRef.current !== null) {
        window.clearTimeout(copyTimerRef.current);
      }
      copyTimerRef.current = window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setError("浏览器未允许复制，请选中文本后手动复制。");
    }
  }

  const progressLabel =
    status === "uploading"
      ? `正在上传 ${progress}%`
      : status === "processing"
        ? "模型正在识别语音"
        : "";

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
            <span className="rounded-full border border-emerald-300/30 bg-emerald-300/10 px-3 py-1.5 text-xs font-semibold text-emerald-100">
              音频转文字
            </span>
            <span className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1.5 text-xs text-slate-300">
              文件不会保存在模镜
            </span>
          </div>
          <h1 className="mt-4 text-2xl font-semibold text-white sm:text-4xl">
            使用 {model.name} 转录音频
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
            上传一段录音，模型会返回可复制的文字。首批支持单文件转录，不会申请麦克风权限。
          </p>
        </header>

        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:items-start">
          <section className="surface-panel overflow-hidden rounded-lg">
            <div className="border-b border-white/10 px-5 py-5 sm:px-6">
              <h2 className="text-lg font-semibold text-white">选择音频</h2>
              <p className="mt-1 text-sm leading-6 text-slate-400">
                WAV、MP3、FLAC、M4A、OGG、WebM、AAC，单个文件不超过 25 MiB。
              </p>
            </div>

            <div className="space-y-5 p-5 sm:p-6">
              <div
                className={`rounded-lg border border-dashed px-5 py-8 text-center transition ${
                  isDragging
                    ? "border-hire-200 bg-hire-300/12"
                    : "border-white/20 bg-white/[0.035] hover:border-hire-300/45 hover:bg-hire-300/[0.06]"
                } ${isRunning ? "cursor-not-allowed opacity-60" : ""}`}
                onDragEnter={(event) => {
                  event.preventDefault();
                  if (!isRunning) setIsDragging(true);
                }}
                onDragLeave={() => setIsDragging(false)}
                onDragOver={(event) => event.preventDefault()}
                onDrop={handleDrop}
              >
                <input
                  accept={AUDIO_ACCEPT}
                  className="hidden"
                  disabled={isRunning}
                  id="transcription-audio"
                  onChange={handleFileInput}
                  ref={inputRef}
                  type="file"
                />
                <p className="text-base font-semibold text-white">
                  {file ? "需要更换音频？" : "拖放音频到这里"}
                </p>
                <p className="mt-2 text-sm text-slate-400">
                  也可以从设备中选择一个文件
                </p>
                <button
                  className="mt-5 inline-flex rounded-full bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-45"
                  disabled={isRunning}
                  onClick={() => inputRef.current?.click()}
                  ref={selectButtonRef}
                  type="button"
                >
                  {file ? "替换音频" : "选择音频"}
                </button>
              </div>

              {file ? (
                <div className="flex flex-col gap-4 rounded-lg bg-white/[0.05] p-4 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <p className="truncate font-semibold text-white">{file.name}</p>
                    <p className="mt-1 text-xs text-slate-400">
                      {fileExtension(file).toUpperCase()} · {formatBytes(file.size)}
                    </p>
                  </div>
                  <button
                    className="self-start rounded-full border border-white/15 px-3 py-1.5 text-sm font-semibold text-slate-200 transition hover:border-rose-300/40 hover:bg-rose-300/10 hover:text-rose-100 disabled:cursor-not-allowed disabled:opacity-50 sm:self-auto"
                    disabled={isRunning}
                    onClick={removeFile}
                    type="button"
                  >
                    移除音频
                  </button>
                  {audioUrl ? (
                    <audio
                      aria-label={`预听 ${file.name}`}
                      className="w-full sm:max-w-[270px]"
                      controls
                      preload="metadata"
                      src={audioUrl}
                    />
                  ) : null}
                </div>
              ) : null}

              <div>
                <label
                  className="mb-2 block text-sm font-semibold text-slate-200"
                  htmlFor="transcription-language"
                >
                  音频语言
                </label>
                <select
                  className="w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-3 text-sm text-white outline-none transition focus:border-brand-300/60 focus:ring-4 focus:ring-brand-300/10 disabled:cursor-not-allowed disabled:opacity-60 sm:max-w-xs"
                  disabled={isRunning}
                  id="transcription-language"
                  onChange={(event) => setLanguage(event.target.value)}
                  value={language}
                >
                  {LANGUAGE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <p className="mt-2 text-xs leading-5 text-slate-400">
                  不确定时保持“自动识别”；明确语言通常能减少识别歧义。
                </p>
              </div>

              {isRunning ? (
                <div aria-live="polite" className="rounded-lg bg-brand-300/[0.07] p-4">
                  <div className="flex items-center justify-between gap-3 text-sm">
                    <span className="font-semibold text-brand-100">{progressLabel}</span>
                    <span className="tabular-nums text-slate-300">{progress}%</span>
                  </div>
                  <div
                    aria-label={progressLabel}
                    aria-valuemax={100}
                    aria-valuemin={0}
                    aria-valuenow={progress}
                    className="mt-3 h-2 overflow-hidden rounded-full bg-white/10"
                    role="progressbar"
                  >
                    <div
                      className="h-full rounded-full bg-brand-300 transition-[width] duration-200"
                      style={{ width: `${progress}%` }}
                    />
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

              {status === "cancelled" ? (
                <div
                  className="rounded-lg border border-amber-300/25 bg-amber-300/10 px-4 py-3 text-sm text-amber-100"
                  role="status"
                >
                  已取消，本地音频仍保留，可以重新开始。
                </div>
              ) : null}

              <div className="flex flex-wrap items-center gap-3 border-t border-white/10 pt-5">
                <button
                  className="rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-45"
                  disabled={!file || isRunning}
                  onClick={() => void runTranscription()}
                  type="button"
                >
                  {file && (status === "failed" || status === "cancelled")
                    ? "重新转录"
                    : "开始转录"}
                </button>
                {isRunning ? (
                  <button
                    className="rounded-full border border-white/15 px-4 py-2.5 text-sm font-semibold text-slate-200 transition hover:border-rose-300/40 hover:bg-rose-300/10 hover:text-rose-100"
                    onClick={() => abortRef.current?.abort()}
                    type="button"
                  >
                    取消转录
                  </button>
                ) : null}
                {!file ? (
                  <span className="text-sm text-slate-400">选择音频后即可开始</span>
                ) : null}
              </div>
            </div>
          </section>

          <aside className="surface-card rounded-lg p-5 lg:sticky lg:top-24">
            <h2 className="font-semibold text-white">本次转录</h2>
            <dl className="mt-4 space-y-4 text-sm">
              <div>
                <dt className="text-slate-400">模型</dt>
                <dd className="mt-1 break-words font-medium text-slate-100">
                  {model.name}
                </dd>
              </div>
              <div>
                <dt className="text-slate-400">调用 ID</dt>
                <dd className="mt-1 break-all font-mono text-xs text-slate-300">
                  {model.id}
                </dd>
              </div>
              <div>
                <dt className="text-slate-400">处理方式</dt>
                <dd className="mt-1 leading-6 text-slate-200">
                  音频仅用于本次请求，不写入模镜资料库。
                </dd>
              </div>
            </dl>
          </aside>
        </div>

        {result ? (
          <section className="surface-panel mt-6 overflow-hidden rounded-lg">
            <div className="flex flex-col gap-3 border-b border-white/10 px-5 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
              <div>
                <h2 className="text-lg font-semibold text-white">转录结果</h2>
                <p className="mt-1 text-xs text-slate-400">
                  {result.actual_model} · {result.provider} · {costLabel(result.usage)}
                </p>
              </div>
              <button
                className="self-start rounded-full border border-brand-300/35 bg-brand-300/10 px-4 py-2 text-sm font-semibold text-brand-100 transition hover:border-brand-300/60 hover:bg-brand-300/15 sm:self-auto"
                onClick={() => void copyTranscript()}
                type="button"
              >
                {copied ? "已复制" : "复制文字"}
              </button>
            </div>
            <div
              aria-live="polite"
              className="whitespace-pre-wrap break-words px-5 py-6 text-[15px] leading-7 text-slate-100 sm:px-6"
            >
              {result.text || "模型没有返回可显示的文字。"}
            </div>
            <div className="border-t border-white/10 px-5 py-3 text-xs text-slate-500 sm:px-6">
              请求 ID：<span className="break-all font-mono">{result.request_id}</span>
            </div>
          </section>
        ) : null}
      </div>
    </main>
  );
}
