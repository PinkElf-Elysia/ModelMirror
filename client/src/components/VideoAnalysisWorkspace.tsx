import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from "react";
import ReactMarkdown from "react-markdown";
import { Link } from "react-router-dom";
import remarkGfm from "remark-gfm";
import type { Model } from "../data/models";
import BrandLogo from "./BrandLogo";
import ResourceNav from "./ResourceNav";

const MAX_VIDEO_BYTES = 20 * 1024 * 1024;
const MAX_PROMPT_CHARS = 4_000;
const ACCEPTED_EXTENSIONS = new Set(["mp4", "mpeg", "mpg", "mov", "webm"]);
const VIDEO_ACCEPT = [
  ".mp4",
  ".mpeg",
  ".mpg",
  ".mov",
  ".webm",
  "video/mp4",
  "video/mpeg",
  "video/quicktime",
  "video/webm",
].join(",");

type VideoSourceMode = "file" | "url";
type AnalysisStatus =
  | "idle"
  | "uploading"
  | "analyzing"
  | "succeeded"
  | "failed"
  | "cancelled";

interface VideoAnalysisUsage {
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  cost_usd: number | null;
  cost_kind: "actual" | "estimated" | "unavailable";
}

interface VideoAnalysisResponse {
  text: string;
  requested_model: string;
  actual_model: string;
  provider: string;
  request_id: string;
  source_kind: VideoSourceMode;
  usage: VideoAnalysisUsage;
}

interface VideoAnalysisWorkspaceProps {
  model: Model;
}

interface ProgressUpdate {
  phase: "uploading" | "analyzing";
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

function validateVideoFile(file: File) {
  if (!ACCEPTED_EXTENSIONS.has(fileExtension(file))) {
    return "请选择 MP4、MPEG、MOV 或 WebM 视频。";
  }
  if (file.size <= 0) {
    return "这个视频文件没有可读取的内容，请重新选择。";
  }
  if (file.size > MAX_VIDEO_BYTES) {
    return "视频超过 20 MiB，请压缩或裁剪后重新上传。";
  }
  return "";
}

function validateVideoUrl(value: string) {
  const cleanUrl = value.trim();
  if (!cleanUrl) return "请粘贴一个 HTTPS 视频网址。";
  if (cleanUrl.length > 2_048) return "视频网址过长，请更换有效链接。";
  try {
    const parsed = new URL(cleanUrl);
    if (
      parsed.protocol !== "https:" ||
      !parsed.hostname ||
      parsed.username ||
      parsed.password ||
      parsed.hash
    ) {
      return "仅支持不含账号信息或片段标记的 HTTPS 视频网址。";
    }
  } catch {
    return "视频网址格式不正确，请检查后重试。";
  }
  return "";
}

function youtubeEmbedUrl(value: string) {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/^www\./, "");
    let videoId = "";
    if (host === "youtu.be") {
      videoId = url.pathname.split("/").filter(Boolean)[0] ?? "";
    } else if (host === "youtube.com" || host === "m.youtube.com") {
      if (url.pathname === "/watch") {
        videoId = url.searchParams.get("v") ?? "";
      } else {
        const parts = url.pathname.split("/").filter(Boolean);
        if (parts[0] === "shorts" || parts[0] === "embed") {
          videoId = parts[1] ?? "";
        }
      }
    }
    if (/^[A-Za-z0-9_-]{6,32}$/.test(videoId)) {
      return `https://www.youtube-nocookie.com/embed/${videoId}`;
    }
  } catch {
    return "";
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
  if (status === 413) return "视频超过服务限制，请压缩或裁剪后重试。";
  if (status === 429) return "模型服务当前请求较多，请稍后重试。";
  if (status === 402) return "模型服务余额不足，请检查 OpenRouter 账户。";
  return "视频分析没有完成，请检查连接后重试。";
}

function requestVideoAnalysis({
  file,
  modelId,
  prompt,
  signal,
  sourceMode,
  videoUrl,
  onProgress,
}: {
  file: File | null;
  modelId: string;
  prompt: string;
  signal: AbortSignal;
  sourceMode: VideoSourceMode;
  videoUrl: string;
  onProgress: (update: ProgressUpdate) => void;
}) {
  return new Promise<VideoAnalysisResponse>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Request aborted", "AbortError"));
      return;
    }

    const xhr = new XMLHttpRequest();
    const form = new FormData();
    form.append("model_id", modelId);
    form.append("prompt", prompt);
    form.append("source_type", sourceMode);
    if (sourceMode === "file" && file) {
      form.append("file", file, file.name);
    } else {
      form.append("video_url", videoUrl.trim());
    }

    const cleanup = () => signal.removeEventListener("abort", handleAbort);
    const handleAbort = () => xhr.abort();

    xhr.open("POST", "/api/multimodal/video/analysis");
    xhr.responseType = "json";
    xhr.upload.onprogress = (event) => {
      const percent = event.lengthComputable
        ? Math.min(88, Math.max(2, Math.round((event.loaded / event.total) * 88)))
        : 24;
      onProgress({ phase: "uploading", percent });
    };
    xhr.upload.onload = () => {
      onProgress({ phase: "analyzing", percent: 92 });
    };
    xhr.onload = () => {
      cleanup();
      const payload = responsePayload(xhr);
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload as VideoAnalysisResponse);
        return;
      }
      reject(new Error(responseErrorMessage(payload, xhr.status)));
    };
    xhr.onerror = () => {
      cleanup();
      reject(new Error("无法连接视频分析服务，请确认后端正在运行。"));
    };
    xhr.onabort = () => {
      cleanup();
      reject(new DOMException("Request aborted", "AbortError"));
    };
    signal.addEventListener("abort", handleAbort, { once: true });
    onProgress({
      phase: sourceMode === "file" ? "uploading" : "analyzing",
      percent: sourceMode === "file" ? 2 : 12,
    });
    xhr.send(form);
  });
}

function costLabel(usage: VideoAnalysisUsage) {
  if (usage.cost_kind === "actual" && usage.cost_usd !== null) {
    return `$${usage.cost_usd.toFixed(6)}`;
  }
  if (usage.cost_kind === "estimated" && usage.cost_usd !== null) {
    return `约 $${usage.cost_usd.toFixed(6)}`;
  }
  return "费用待网关结算";
}

export default function VideoAnalysisWorkspace({
  model,
}: VideoAnalysisWorkspaceProps) {
  const [sourceMode, setSourceMode] = useState<VideoSourceMode>("file");
  const [file, setFile] = useState<File | null>(null);
  const [fileUrl, setFileUrl] = useState("");
  const [videoUrl, setVideoUrl] = useState("");
  const [prompt, setPrompt] = useState("");
  const [status, setStatus] = useState<AnalysisStatus>("idle");
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<VideoAnalysisResponse | null>(null);
  const [error, setError] = useState("");
  const [isDragging, setIsDragging] = useState(false);
  const [copied, setCopied] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const selectButtonRef = useRef<HTMLButtonElement>(null);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const copyTimerRef = useRef<number | null>(null);
  const isRunning = status === "uploading" || status === "analyzing";

  useEffect(() => {
    document.title = `分析视频 · ${model.name} · 模镜`;
  }, [model.name]);

  useEffect(() => {
    if (!file) {
      setFileUrl("");
      return undefined;
    }
    const nextUrl = URL.createObjectURL(file);
    setFileUrl(nextUrl);
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
      const validationError = validateVideoFile(nextFile);
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

  const cleanVideoUrl = videoUrl.trim();
  const videoUrlError = cleanVideoUrl ? validateVideoUrl(cleanVideoUrl) : "";
  const youtubePreview = useMemo(
    () => (videoUrlError ? "" : youtubeEmbedUrl(cleanVideoUrl)),
    [cleanVideoUrl, videoUrlError],
  );

  function switchSourceMode(nextMode: VideoSourceMode) {
    if (nextMode === sourceMode || isRunning) return;
    setSourceMode(nextMode);
    resetOutcome();
  }

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

  function updateVideoUrl(value: string) {
    setVideoUrl(value);
    resetOutcome();
  }

  async function runAnalysis() {
    if (isRunning) return;
    const sourceError =
      sourceMode === "file"
        ? file
          ? validateVideoFile(file)
          : "请先选择一个视频文件。"
        : validateVideoUrl(videoUrl);
    const cleanPrompt = prompt.trim();
    if (sourceError) {
      setError(sourceError);
      setStatus("failed");
      return;
    }
    if (!cleanPrompt || cleanPrompt.length > MAX_PROMPT_CHARS) {
      setError("请输入 1–4000 个字符的视频分析问题。");
      setStatus("failed");
      promptRef.current?.focus();
      return;
    }

    const controller = new AbortController();
    abortRef.current = controller;
    setResult(null);
    setError("");
    setCopied(false);

    try {
      const nextResult = await requestVideoAnalysis({
        file,
        modelId: model.id,
        prompt: cleanPrompt,
        signal: controller.signal,
        sourceMode,
        videoUrl,
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
            : "视频分析没有完成，请稍后重试。",
        );
      }
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
      }
    }
  }

  async function copyResult() {
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

  const sourceReady = sourceMode === "file" ? Boolean(file) : Boolean(cleanVideoUrl);
  const canSubmit =
    sourceReady &&
    !videoUrlError &&
    prompt.trim().length > 0 &&
    prompt.trim().length <= MAX_PROMPT_CHARS &&
    !isRunning;
  const progressLabel =
    status === "uploading" ? `正在上传 ${progress}%` : "模型正在分析视频";

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
            <span className="rounded-full border border-sky-300/30 bg-sky-300/10 px-3 py-1.5 text-xs font-semibold text-sky-100">
              视频理解
            </span>
            <span className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1.5 text-xs text-slate-300">
              本地视频不会保存在模镜
            </span>
          </div>
          <h1 className="mt-4 text-2xl font-semibold text-white sm:text-4xl">
            使用 {model.name} 分析视频
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
            上传本地视频或粘贴公开视频网址，再告诉模型你想了解什么。分析能力取决于所选模型，本轮不单独保证音轨识别。
          </p>
        </header>

        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:items-start">
          <section className="surface-panel overflow-hidden rounded-lg">
            <div className="border-b border-white/10 px-5 py-5 sm:px-6">
              <div
                aria-label="视频来源"
                className="inline-flex rounded-lg bg-white/[0.055] p-1"
                role="group"
              >
                <button
                  aria-pressed={sourceMode === "file"}
                  className={`rounded-md px-4 py-2 text-sm font-semibold transition ${
                    sourceMode === "file"
                      ? "bg-hire-300 text-ink-950"
                      : "text-slate-300 hover:bg-white/[0.06] hover:text-white"
                  }`}
                  disabled={isRunning}
                  onClick={() => switchSourceMode("file")}
                  type="button"
                >
                  上传本地视频
                </button>
                <button
                  aria-pressed={sourceMode === "url"}
                  className={`rounded-md px-4 py-2 text-sm font-semibold transition ${
                    sourceMode === "url"
                      ? "bg-hire-300 text-ink-950"
                      : "text-slate-300 hover:bg-white/[0.06] hover:text-white"
                  }`}
                  disabled={isRunning}
                  onClick={() => switchSourceMode("url")}
                  type="button"
                >
                  粘贴视频网址
                </button>
              </div>
            </div>

            <div className="space-y-5 p-5 sm:p-6">
              {sourceMode === "file" ? (
                <>
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
                      accept={VIDEO_ACCEPT}
                      className="hidden"
                      disabled={isRunning}
                      id="video-analysis-file"
                      onChange={handleFileInput}
                      ref={fileInputRef}
                      type="file"
                    />
                    <p className="text-base font-semibold text-white">
                      {file ? "需要更换视频？" : "拖放视频到这里"}
                    </p>
                    <p className="mt-2 text-sm text-slate-400">
                      MP4、MPEG、MOV、WebM，单个文件不超过 20 MiB
                    </p>
                    <button
                      className="mt-5 inline-flex rounded-full bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-45"
                      disabled={isRunning}
                      onClick={() => fileInputRef.current?.click()}
                      ref={selectButtonRef}
                      type="button"
                    >
                      {file ? "替换视频" : "选择视频"}
                    </button>
                  </div>

                  {file ? (
                    <div className="space-y-4 rounded-lg bg-white/[0.05] p-4">
                      <div className="flex items-center justify-between gap-4">
                        <div className="min-w-0">
                          <p className="truncate font-semibold text-white">
                            {file.name}
                          </p>
                          <p className="mt-1 text-xs text-slate-400">
                            {fileExtension(file).toUpperCase()} ·{" "}
                            {formatBytes(file.size)}
                          </p>
                        </div>
                        <button
                          className="shrink-0 rounded-full border border-white/15 px-3 py-1.5 text-sm font-semibold text-slate-200 transition hover:border-rose-300/40 hover:bg-rose-300/10 hover:text-rose-100 disabled:cursor-not-allowed disabled:opacity-50"
                          disabled={isRunning}
                          onClick={removeFile}
                          type="button"
                        >
                          移除视频
                        </button>
                      </div>
                      {fileUrl ? (
                        <video
                          aria-label={`预览 ${file.name}`}
                          className="aspect-video w-full rounded-lg bg-black object-contain"
                          controls
                          preload="metadata"
                          src={fileUrl}
                        />
                      ) : null}
                    </div>
                  ) : null}
                </>
              ) : (
                <div>
                  <label
                    className="mb-2 block text-sm font-semibold text-slate-200"
                    htmlFor="video-analysis-url"
                  >
                    视频网址
                  </label>
                  <input
                    className="w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-3 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-brand-300/60 focus:ring-4 focus:ring-brand-300/10 disabled:cursor-not-allowed disabled:opacity-60"
                    disabled={isRunning}
                    id="video-analysis-url"
                    onChange={(event) => updateVideoUrl(event.target.value)}
                    placeholder="https://example.com/video.mp4 或 YouTube 链接"
                    type="url"
                    value={videoUrl}
                  />
                  <p className="mt-2 text-xs leading-5 text-slate-400">
                    网址会直接交给模型服务，模镜不会主动下载视频。
                  </p>
                  {cleanVideoUrl && videoUrlError ? (
                    <p className="mt-2 text-sm text-rose-200" role="alert">
                      {videoUrlError}
                    </p>
                  ) : null}
                  {cleanVideoUrl && !videoUrlError ? (
                    <div className="mt-4 overflow-hidden rounded-lg bg-black">
                      {youtubePreview ? (
                        <iframe
                          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                          allowFullScreen
                          className="aspect-video w-full"
                          referrerPolicy="strict-origin-when-cross-origin"
                          src={youtubePreview}
                          title="YouTube 视频预览"
                        />
                      ) : (
                        <video
                          aria-label="视频网址预览"
                          className="aspect-video w-full object-contain"
                          controls
                          preload="metadata"
                          src={cleanVideoUrl}
                        />
                      )}
                    </div>
                  ) : null}
                </div>
              )}

              <div>
                <div className="mb-2 flex items-center justify-between gap-4">
                  <label
                    className="text-sm font-semibold text-slate-200"
                    htmlFor="video-analysis-prompt"
                  >
                    想了解什么？
                  </label>
                  <span className="text-xs tabular-nums text-slate-400">
                    {prompt.length} / {MAX_PROMPT_CHARS}
                  </span>
                </div>
                <textarea
                  className="min-h-32 w-full resize-y rounded-lg border border-white/15 bg-ink-950 px-3 py-3 text-sm leading-6 text-white outline-none transition placeholder:text-slate-500 focus:border-brand-300/60 focus:ring-4 focus:ring-brand-300/10 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={isRunning}
                  id="video-analysis-prompt"
                  maxLength={MAX_PROMPT_CHARS}
                  onChange={(event) => {
                    setPrompt(event.target.value);
                    resetOutcome();
                  }}
                  placeholder="例如：按时间顺序概括关键事件，并指出画面中的文字和异常情况。"
                  ref={promptRef}
                  value={prompt}
                />
              </div>

              {isRunning ? (
                <div
                  aria-live="polite"
                  className="rounded-lg bg-brand-300/[0.07] p-4"
                >
                  <div className="flex items-center justify-between gap-3 text-sm">
                    <span className="font-semibold text-brand-100">
                      {progressLabel}
                    </span>
                    <span className="tabular-nums text-slate-300">
                      {progress}%
                    </span>
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
                  已取消。本地视频和问题仍保留，可以重新分析。
                </div>
              ) : null}

              <div className="flex flex-wrap items-center gap-3 border-t border-white/10 pt-5">
                <button
                  className="rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-45"
                  disabled={!canSubmit}
                  onClick={() => void runAnalysis()}
                  type="button"
                >
                  {status === "failed" || status === "cancelled"
                    ? "重新分析"
                    : "开始分析"}
                </button>
                {isRunning ? (
                  <button
                    className="rounded-full border border-white/15 px-4 py-2.5 text-sm font-semibold text-slate-200 transition hover:border-rose-300/40 hover:bg-rose-300/10 hover:text-rose-100"
                    onClick={() => abortRef.current?.abort()}
                    type="button"
                  >
                    取消分析
                  </button>
                ) : null}
              </div>
            </div>
          </section>

          <aside className="space-y-4 lg:sticky lg:top-24">
            <div className="surface-card rounded-lg p-5">
              <p className="text-sm font-semibold text-white">本次分析</p>
              <dl className="mt-4 space-y-3 text-sm">
                <div>
                  <dt className="text-slate-400">模型</dt>
                  <dd className="mt-1 break-words font-medium text-slate-100">
                    {model.name}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-400">来源</dt>
                  <dd className="mt-1 font-medium text-slate-100">
                    {sourceMode === "file" ? "本地视频" : "公开视频网址"}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-400">保存策略</dt>
                  <dd className="mt-1 leading-6 text-slate-300">
                    模镜不保存视频和问题正文，供应商可能按自身策略处理内容。
                  </dd>
                </div>
              </dl>
            </div>
            <div className="rounded-lg border border-amber-300/20 bg-amber-300/[0.07] p-5">
              <p className="text-sm font-semibold text-amber-100">使用提示</p>
              <ul className="mt-3 space-y-2 text-sm leading-6 text-slate-300">
                <li>描述希望关注的时间段、对象或事件。</li>
                <li>较短、画面清晰的视频通常更容易分析。</li>
                <li>本轮为一次性分析，结果不会形成多轮视频会话。</li>
              </ul>
            </div>
          </aside>
        </div>

        {result ? (
          <section
            aria-live="polite"
            className="surface-panel mt-6 overflow-hidden rounded-lg"
          >
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-5 py-4 sm:px-6">
              <div>
                <h2 className="text-lg font-semibold text-white">分析结果</h2>
                <p className="mt-1 text-xs text-slate-400">
                  实际模型：{result.actual_model}
                </p>
              </div>
              <button
                className="rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-brand-300/40 hover:bg-brand-300/10 hover:text-brand-100"
                onClick={() => void copyResult()}
                type="button"
              >
                {copied ? "已复制" : "复制结果"}
              </button>
            </div>
            <div className="px-5 py-6 sm:px-6">
              <div className="prose prose-invert max-w-none prose-p:leading-7 prose-a:text-brand-200 prose-pre:overflow-x-auto">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {result.text}
                </ReactMarkdown>
              </div>
              <dl className="mt-6 grid gap-3 border-t border-white/10 pt-5 text-sm sm:grid-cols-2 lg:grid-cols-4">
                <div>
                  <dt className="text-slate-400">费用</dt>
                  <dd className="mt-1 font-semibold text-slate-100">
                    {costLabel(result.usage)}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-400">Token</dt>
                  <dd className="mt-1 font-semibold text-slate-100">
                    {result.usage.total_tokens?.toLocaleString("zh-CN") ??
                      "待网关结算"}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-400">供应商</dt>
                  <dd className="mt-1 font-semibold text-slate-100">
                    {result.provider}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-400">请求 ID</dt>
                  <dd
                    className="mt-1 truncate font-mono text-xs text-slate-200"
                    title={result.request_id}
                  >
                    {result.request_id}
                  </dd>
                </div>
              </dl>
            </div>
          </section>
        ) : null}
      </div>
    </main>
  );
}
