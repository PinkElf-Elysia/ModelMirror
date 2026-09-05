import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from "react";
import { Link, useNavigate } from "react-router-dom";
import { models, type Model } from "../data/models";
import {
  parseAudioProviderRouteReceipts,
  type AudioProviderRouteReceipt,
} from "../utils/speechAudio";
import BrandLogo from "./BrandLogo";
import ProviderRouteReceiptSummary from "./ProviderRouteReceiptSummary";
import ResourceNav from "./ResourceNav";

const MAX_PROMPT_CHARS = 4_000;
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const ACTIVE_STATUSES = new Set<AudioJobStatus>(["queued", "running"]);
const POLL_DELAYS = [2_000, 5_000, 10_000] as const;
const IMAGE_EXTENSIONS = new Set(["jpg", "jpeg", "png", "webp"]);

type AudioJobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "expired";

interface AudioModelProfile {
  model_id: string;
  display_name: string;
  invocable: boolean;
  interaction_status: "ready" | "planned" | "disabled";
  status_reason: string | null;
  operations: string[];
  output_formats: string[];
  supports_image_prompt: boolean;
  price_per_generation_usd: number | null;
  fixed_duration_seconds: number | null;
}

interface AudioCatalogResponse {
  status: "online" | "stale" | "offline" | "disabled";
  stale: boolean;
  profiles: AudioModelProfile[];
}

interface AudioJob {
  job_id: string;
  status: AudioJobStatus;
  requested_model: string;
  actual_model: string | null;
  provider: "openrouter";
  generation_id: string | null;
  parameters: {
    has_image: boolean;
  };
  usage: {
    cost_usd: number | null;
    cost_kind: "actual" | "unavailable";
  };
  output_bytes: number;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
  error: {
    code: string;
    message: string;
  } | null;
  execution_mode: "managed" | "legacy";
  provider_route_receipts: AudioProviderRouteReceipt[];
  provider_dispatch_state:
    | "not_dispatched"
    | "dispatched"
    | "confirmed"
    | "uncertain"
    | null;
  retry_allowed: boolean;
  fallback_reason_codes: string[];
}

interface AudioJobListResponse {
  jobs: AudioJob[];
}

interface AudioCreationWorkspaceProps {
  model: Model;
}

const statusPresentation: Record<
  AudioJobStatus,
  { label: string; className: string; description: string }
> = {
  queued: {
    label: "已提交",
    className: "border-amber-300/30 bg-amber-300/10 text-amber-100",
    description: "任务已接收，正在等待音乐模型开始生成。",
  },
  running: {
    label: "创作中",
    className: "border-sky-300/30 bg-sky-300/10 text-sky-100",
    description: "正在接收并校验完整音频，完成前不会交付损坏文件。",
  },
  succeeded: {
    label: "已完成",
    className: "border-emerald-300/30 bg-emerald-300/10 text-emerald-100",
    description: "音乐已生成，可以试听或下载。",
  },
  failed: {
    label: "未完成",
    className: "border-rose-300/30 bg-rose-300/10 text-rose-100",
    description: "任务没有生成可用音频，请按提示调整后重新提交。",
  },
  expired: {
    label: "已过期",
    className: "border-slate-300/25 bg-slate-300/10 text-slate-200",
    description: "临时音频已超过 30 分钟保留时间，请重新生成。",
  },
};

function responseErrorMessage(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object") {
    const detail = (payload as { detail?: unknown }).detail;
    if (detail && typeof detail === "object") {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string" && message.trim()) return message;
    }
  }
  return fallback;
}

async function readJson<T>(response: Response, fallback: string): Promise<T> {
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new Error(responseErrorMessage(payload, fallback));
  }
  return payload as T;
}

function parseAudioJob(payload: unknown): AudioJob {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("音乐任务响应格式无效，请刷新后重试。");
  }
  const value = payload as Record<string, unknown>;
  if (
    typeof value.job_id !== "string" ||
    !value.job_id.trim() ||
    typeof value.requested_model !== "string" ||
    !ACTIVE_STATUSES.has(value.status as AudioJobStatus) &&
      value.status !== "succeeded" &&
      value.status !== "failed" &&
      value.status !== "expired"
  ) {
    throw new Error("音乐任务响应格式无效，请刷新后重试。");
  }
  const executionMode = value.execution_mode === "managed" ? "managed" : "legacy";
  const dispatchState = new Set([
    "not_dispatched",
    "dispatched",
    "confirmed",
    "uncertain",
  ]).has(String(value.provider_dispatch_state))
    ? (value.provider_dispatch_state as AudioJob["provider_dispatch_state"])
    : null;
  return {
    ...(value as unknown as AudioJob),
    generation_id:
      executionMode === "managed"
        ? null
        : typeof value.generation_id === "string"
          ? value.generation_id
          : null,
    execution_mode: executionMode,
    provider_route_receipts: parseAudioProviderRouteReceipts(
      value.provider_route_receipts,
    ),
    provider_dispatch_state: dispatchState,
    retry_allowed:
      typeof value.retry_allowed === "boolean"
        ? value.retry_allowed
        : executionMode === "legacy",
    fallback_reason_codes: Array.isArray(value.fallback_reason_codes)
      ? value.fallback_reason_codes.filter(
          (item): item is string => typeof item === "string",
        )
      : [],
  };
}

function parseAudioJobList(payload: unknown): AudioJobListResponse {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("音乐任务列表响应格式无效，请刷新后重试。");
  }
  const jobs = (payload as { jobs?: unknown }).jobs;
  if (!Array.isArray(jobs)) {
    throw new Error("音乐任务列表响应格式无效，请刷新后重试。");
  }
  return { jobs: jobs.map(parseAudioJob) };
}

function mergeJob(current: AudioJob[], job: AudioJob) {
  return [job, ...current.filter((item) => item.job_id !== job.job_id)].sort(
    (left, right) =>
      new Date(right.created_at).getTime() -
      new Date(left.created_at).getTime(),
  );
}

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间待确认";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatBytes(bytes: number) {
  if (bytes <= 0) return "大小待确认";
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  }
  return `${Math.max(1, Math.round(bytes / 1024))} KiB`;
}

function formatCost(job: AudioJob) {
  return job.usage.cost_usd === null
    ? "费用以网关结算为准"
    : `$${job.usage.cost_usd.toFixed(4)}`;
}

function creationType(
  modelId: string,
  fixedDurationSeconds: number | null | undefined,
) {
  const durationLabel = fixedDurationSeconds
    ? `约 ${fixedDurationSeconds} 秒`
    : "时长由模型决定";
  return modelId.includes("clip")
    ? {
        label: "短音乐片段",
        description: `适合快速验证旋律、配器和整体氛围，${durationLabel}。`,
      }
    : {
        label: "完整音乐作品",
        description: `适合需要更完整段落与编排的创作，${durationLabel}。`,
      };
}

function newIdempotencyKey() {
  if (typeof crypto.randomUUID === "function") {
    return `audio-${crypto.randomUUID()}`;
  }
  return `audio-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function validateImage(file: File) {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (!IMAGE_EXTENSIONS.has(extension)) {
    return "图片提示只支持 JPEG、PNG 和 WebP。";
  }
  if (file.size > MAX_IMAGE_BYTES) {
    return "图片提示超过 10 MiB，请压缩后重试。";
  }
  const allowedTypes = new Set([
    "",
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
  ]);
  if (!allowedTypes.has(file.type.toLowerCase())) {
    return "图片扩展名与文件类型不一致，请重新导出后重试。";
  }
  return "";
}

export default function AudioCreationWorkspace({
  model,
}: AudioCreationWorkspaceProps) {
  const navigate = useNavigate();
  const [catalog, setCatalog] = useState<AudioCatalogResponse | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [jobs, setJobs] = useState<AudioJob[]>([]);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [prompt, setPrompt] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [imageUrl, setImageUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pollWarnings, setPollWarnings] = useState<Record<string, string>>({});
  const [confirmDeleteId, setConfirmDeleteId] = useState("");
  const [idempotencyAttemptRetained, setIdempotencyAttemptRetained] =
    useState(false);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const idempotencyKeyRef = useRef("");
  const idempotencyJobIdRef = useRef("");
  const jobsRef = useRef<AudioJob[]>([]);

  const clearIdempotencyAttempt = useCallback(() => {
    idempotencyKeyRef.current = "";
    idempotencyJobIdRef.current = "";
    setIdempotencyAttemptRetained(false);
  }, []);

  const profile = useMemo(
    () =>
      catalog?.profiles.find(
        (item) =>
          item.model_id === model.id &&
          item.operations.includes("generate_audio"),
      ) ?? null,
    [catalog, model.id],
  );
  const readyProfiles = useMemo(
    () =>
      (catalog?.profiles ?? []).filter(
        (item) =>
          item.invocable &&
          item.interaction_status === "ready" &&
          item.operations.includes("generate_audio") &&
          item.output_formats.includes("mp3"),
      ),
    [catalog],
  );
  const visibleJobs = useMemo(
    () => jobs.filter((job) => job.requested_model === model.id),
    [jobs, model.id],
  );
  const type = creationType(model.id, profile?.fixed_duration_seconds);
  const canSubmit =
    Boolean(profile) &&
    profile?.invocable === true &&
    profile?.interaction_status === "ready" &&
    prompt.trim().length > 0 &&
    prompt.length <= MAX_PROMPT_CHARS &&
    !submitting;

  useEffect(() => {
    document.title = `生成音乐 · ${model.name} · 模镜`;
  }, [model.name]);

  useEffect(() => {
    jobsRef.current = jobs;
  }, [jobs]);

  useEffect(() => {
    if (!image) {
      setImageUrl("");
      return undefined;
    }
    const nextUrl = URL.createObjectURL(image);
    setImageUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [image]);

  const loadCatalog = useCallback(async (signal?: AbortSignal) => {
    const response = await fetch("/api/multimodal/audio/models", { signal });
    const payload = await readJson<AudioCatalogResponse>(
      response,
      "暂时无法读取音乐模型能力。",
    );
    setCatalog(payload);
  }, []);

  const loadJobs = useCallback(async () => {
    const response = await fetch("/api/multimodal/audio/jobs");
    const payload = await readJson<unknown>(
      response,
      "暂时无法读取音乐任务，请稍后刷新。",
    );
    setJobs(parseAudioJobList(payload).jobs);
  }, []);

  const refreshJob = useCallback(async (jobId: string) => {
    try {
      const response = await fetch(
        `/api/multimodal/audio/jobs/${encodeURIComponent(jobId)}`,
      );
      const payload = await readJson<unknown>(
        response,
        "暂时无法刷新音乐任务。",
      );
      const job = parseAudioJob(payload);
      setJobs((current) => mergeJob(current, job));
      if (
        idempotencyJobIdRef.current === job.job_id &&
        !ACTIVE_STATUSES.has(job.status) &&
        job.retry_allowed
      ) {
        clearIdempotencyAttempt();
      }
      setPollWarnings((current) => {
        if (!current[jobId]) return current;
        const next = { ...current };
        delete next[jobId];
        return next;
      });
      return true;
    } catch (refreshError) {
      setPollWarnings((current) => ({
        ...current,
        [jobId]:
          refreshError instanceof Error
            ? refreshError.message
            : "暂时无法刷新音乐任务。",
      }));
      return false;
    }
  }, [clearIdempotencyAttempt]);

  useEffect(() => {
    clearIdempotencyAttempt();
  }, [clearIdempotencyAttempt, model.id]);

  useEffect(() => {
    const controller = new AbortController();
    setCatalogLoading(true);
    setJobsLoading(true);
    setError("");

    void loadCatalog(controller.signal)
      .catch((loadError) => {
        if (!controller.signal.aborted) {
          setCatalog(null);
          setError(
            loadError instanceof Error
              ? loadError.message
              : "暂时无法读取音乐模型能力。",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setCatalogLoading(false);
      });
    void loadJobs()
      .catch((loadError) => {
        if (!controller.signal.aborted) {
          setError(
            loadError instanceof Error
              ? loadError.message
              : "暂时无法读取音乐任务。",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setJobsLoading(false);
      });

    return () => controller.abort();
  }, [loadCatalog, loadJobs]);

  useEffect(() => {
    let stopped = false;
    let polling = false;
    let timer: number | null = null;
    let errorStreak = 0;

    const clearTimer = () => {
      if (timer !== null) window.clearTimeout(timer);
      timer = null;
    };
    const schedule = () => {
      clearTimer();
      if (stopped || document.visibilityState === "hidden") return;
      const delay =
        POLL_DELAYS[Math.min(errorStreak, POLL_DELAYS.length - 1)];
      timer = window.setTimeout(() => void tick(), delay);
    };
    const tick = async () => {
      if (stopped || polling || document.visibilityState === "hidden") {
        schedule();
        return;
      }
      const activeJobs = jobsRef.current.filter(
        (job) =>
          job.requested_model === model.id &&
          ACTIVE_STATUSES.has(job.status),
      );
      if (activeJobs.length === 0) {
        errorStreak = 0;
        schedule();
        return;
      }
      polling = true;
      const results = await Promise.all(
        activeJobs.map((job) => refreshJob(job.job_id)),
      );
      polling = false;
      errorStreak = results.every(Boolean)
        ? 0
        : Math.min(errorStreak + 1, POLL_DELAYS.length - 1);
      schedule();
    };
    const handleVisibility = () => {
      if (document.visibilityState === "hidden") clearTimer();
      else void tick();
    };

    document.addEventListener("visibilitychange", handleVisibility);
    schedule();
    return () => {
      stopped = true;
      clearTimer();
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [model.id, refreshJob]);

  function markFormChanged() {
    clearIdempotencyAttempt();
    setError("");
    setNotice("");
  }

  function chooseImage(file: File | undefined) {
    if (!file || submitting) return;
    const validationError = validateImage(file);
    if (validationError) {
      setError(validationError);
      return;
    }
    setImage(file);
    markFormChanged();
  }

  function handleImageInput(event: ChangeEvent<HTMLInputElement>) {
    chooseImage(event.target.files?.[0]);
    event.target.value = "";
  }

  async function submitJob() {
    if (!canSubmit || !profile) return;
    const cleanPrompt = prompt.trim();
    if (!cleanPrompt) {
      setError("请先描述希望生成的音乐。");
      promptRef.current?.focus();
      return;
    }
    let key = idempotencyKeyRef.current;
    if (!key) {
      key = newIdempotencyKey();
      idempotencyKeyRef.current = key;
      setIdempotencyAttemptRetained(true);
    }
    const form = new FormData();
    form.append("model_id", model.id);
    form.append("prompt", cleanPrompt);
    form.append("idempotency_key", key);
    if (image) form.append("image", image, image.name);

    setSubmitting(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/multimodal/audio/jobs", {
        method: "POST",
        headers: { "Idempotency-Key": key },
        body: form,
      });
      const payload = await readJson<unknown>(
        response,
        "音乐任务没有提交成功，请检查连接后重试。",
      );
      const job = parseAudioJob(payload);
      idempotencyJobIdRef.current = job.job_id;
      setIdempotencyAttemptRetained(true);
      setJobs((current) => mergeJob(current, job));
      setNotice(
        job.status === "failed"
          ? "这次提交未被接受，请查看任务提示后重试。"
          : "任务已提交，完成后可在下方试听和下载。",
      );
      if (!ACTIVE_STATUSES.has(job.status) && job.retry_allowed) {
        clearIdempotencyAttempt();
      }
    } catch (submitError) {
      setError(
        submitError instanceof Error
          ? submitError.message
          : "音乐任务没有提交成功，请稍后重试。",
      );
      try {
        await loadJobs();
      } catch {
        // Keep the original actionable error.
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function removeJob(jobId: string) {
    if (confirmDeleteId !== jobId) {
      setConfirmDeleteId(jobId);
      return;
    }
    try {
      const response = await fetch(
        `/api/multimodal/audio/jobs/${encodeURIComponent(jobId)}`,
        { method: "DELETE" },
      );
      await readJson(
        response,
        "暂时无法移除任务记录，请稍后重试。",
      );
      setJobs((current) => current.filter((job) => job.job_id !== jobId));
      setConfirmDeleteId("");
      setNotice("本地任务记录已移除，不代表取消上游调用。");
    } catch (removeError) {
      setError(
        removeError instanceof Error
          ? removeError.message
          : "暂时无法移除任务记录。",
      );
    }
  }

  return (
    <main className="museum-grid min-h-screen overflow-x-hidden pb-28 pt-5 text-slate-100 lg:pb-12 lg:pt-24">
      <ResourceNav activeResource="models" />
      <div className="mx-auto w-full max-w-[1180px] px-4 py-5 sm:px-6 lg:px-8">
        <header className="border-y border-hire-300/20 bg-ink-950/72 py-5 backdrop-blur-xl">
          <BrandLogo className="mb-4 lg:hidden" />
        <div className="flex flex-wrap items-start justify-between gap-5">
          <div className="min-w-0">
            <Link
              className="text-sm font-semibold text-slate-300 transition hover:text-brand-100"
              to="/models"
            >
              ← 返回模型招聘会
            </Link>
            <h1 className="mt-4 text-3xl font-semibold tracking-tight text-white sm:text-4xl">
              生成音乐
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300 sm:text-base">
              描述旋律、节奏、乐器与氛围。任务完成后可试听并下载 MP3，生成内容在本地临时保留 30 分钟。
            </p>
          </div>
          <span className="rounded-full border border-brand-300/30 bg-brand-300/10 px-3 py-1.5 text-xs font-semibold text-brand-100">
            {catalog?.stale ? "使用缓存能力" : "音乐创作"}
          </span>
        </div>
        </header>

        <div className="mt-8 grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px] lg:items-start">
          <section className="surface-panel overflow-hidden rounded-lg">
            <div className="border-b border-white/10 px-5 py-4 sm:px-6">
              <h2 className="text-lg font-semibold text-white">创作设置</h2>
              <p className="mt-1 text-sm text-slate-400">
                选择创作类型，图片只作为风格和氛围参考。
              </p>
            </div>

            {catalogLoading ? (
              <div
                aria-label="正在加载音乐模型能力"
                className="space-y-4 p-5 sm:p-6"
              >
                <div className="h-12 animate-pulse rounded-lg bg-white/[0.05] motion-reduce:animate-none" />
                <div className="h-44 animate-pulse rounded-lg bg-white/[0.04] motion-reduce:animate-none" />
              </div>
            ) : profile?.interaction_status === "ready" ? (
              <div className="space-y-5 p-5 sm:p-6">
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="block text-sm font-medium text-slate-200">
                    音乐模型
                    <select
                      className="mt-2 w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-2.5 text-sm text-white outline-none transition focus:border-brand-300/60 focus:ring-4 focus:ring-brand-300/10"
                      disabled={submitting || readyProfiles.length === 0}
                      onChange={(event) => {
                        const nextId = event.target.value;
                        if (nextId === model.id) return;
                        navigate(
                          `/chat/${encodeURIComponent(nextId)}?operation=generate_audio`,
                        );
                      }}
                      value={model.id}
                    >
                      {readyProfiles.map((item) => {
                        const catalogModel = models.find(
                          (candidate) => candidate.id === item.model_id,
                        );
                        return (
                          <option key={item.model_id} value={item.model_id}>
                            {catalogModel?.name ?? item.display_name}
                          </option>
                        );
                      })}
                    </select>
                  </label>
                  <div>
                    <p className="text-sm font-medium text-slate-200">
                      创作类型
                    </p>
                    <div className="mt-2 min-h-11 rounded-lg bg-white/[0.05] px-3 py-2.5">
                      <p className="text-sm font-semibold text-white">
                        {type.label}
                      </p>
                      <p className="mt-1 text-xs leading-5 text-slate-400">
                        {type.description}
                      </p>
                    </div>
                  </div>
                </div>

                <label className="block text-sm font-medium text-slate-200">
                  音乐描述
                  <textarea
                    className="mt-2 min-h-40 w-full resize-y rounded-lg border border-white/15 bg-ink-950 px-4 py-3 text-sm leading-6 text-white outline-none transition placeholder:text-slate-400 focus:border-brand-300/60 focus:ring-4 focus:ring-brand-300/10"
                    disabled={submitting}
                    maxLength={MAX_PROMPT_CHARS}
                    onChange={(event) => {
                      setPrompt(event.target.value);
                      markFormChanged();
                    }}
                    placeholder="例如：轻快的城市流行乐，女声哼唱感的主旋律，加入钢琴、清脆鼓点和温暖弦乐，适合作为清晨通勤配乐。"
                    ref={promptRef}
                    value={prompt}
                  />
                  <span
                    className={`mt-1 block text-right text-xs ${
                      prompt.length >= MAX_PROMPT_CHARS
                        ? "text-amber-100"
                        : "text-slate-400"
                    }`}
                  >
                    {prompt.length} / {MAX_PROMPT_CHARS}
                  </span>
                </label>

                {profile.supports_image_prompt ? (
                  <div className="rounded-lg bg-white/[0.045] p-4">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-white">
                          图片提示（可选）
                        </p>
                        <p className="mt-1 text-xs leading-5 text-slate-400">
                          用于传达色彩、场景和情绪，不保证复刻图片内容。支持 JPEG、PNG、WebP，最大 10 MiB。
                        </p>
                      </div>
                      <input
                        accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"
                        className="hidden"
                        disabled={submitting}
                        onChange={handleImageInput}
                        ref={imageInputRef}
                        type="file"
                      />
                      <button
                        className="rounded-full border border-white/15 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-brand-300/40 hover:bg-brand-300/10 hover:text-brand-100 disabled:cursor-not-allowed disabled:opacity-50"
                        disabled={submitting}
                        onClick={() => imageInputRef.current?.click()}
                        type="button"
                      >
                        {image ? "替换图片" : "选择图片"}
                      </button>
                    </div>
                    {image ? (
                      <div className="mt-4 flex min-w-0 items-center gap-3">
                        {imageUrl ? (
                          <img
                            alt="音乐图片提示预览"
                            className="h-24 w-32 shrink-0 rounded-md bg-black object-contain"
                            src={imageUrl}
                          />
                        ) : null}
                        <div className="min-w-0">
                          <p className="truncate text-xs font-medium text-slate-200">
                            {image.name}
                          </p>
                          <p className="mt-1 text-xs text-slate-400">
                            {formatBytes(image.size)}
                          </p>
                          <button
                            className="mt-2 text-xs font-semibold text-rose-200 transition hover:text-rose-100"
                            disabled={submitting}
                            onClick={() => {
                              setImage(null);
                              markFormChanged();
                            }}
                            type="button"
                          >
                            移除图片
                          </button>
                        </div>
                      </div>
                    ) : null}
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
                {notice ? (
                  <div
                    className="rounded-lg border border-emerald-300/20 bg-emerald-300/[0.08] px-4 py-3 text-sm leading-6 text-emerald-100"
                    role="status"
                  >
                    {notice}
                  </div>
                ) : null}

                <div className="flex flex-wrap items-center justify-between gap-4 border-t border-white/10 pt-5">
                  <div>
                    <p className="text-sm font-semibold text-white">
                      {profile.price_per_generation_usd === null
                        ? "费用以网关结算为准"
                        : `目录估算 $${profile.price_per_generation_usd.toFixed(2)}`}
                    </p>
                    <p className="mt-1 text-xs leading-5 text-slate-400">
                      提交会产生费用，未知费用不会显示为零。
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {idempotencyAttemptRetained ? (
                      <button
                        className="rounded-full border border-white/15 px-4 py-2.5 text-sm font-semibold text-slate-200 transition hover:border-brand-300/40 hover:text-brand-100 disabled:cursor-not-allowed disabled:opacity-45"
                        disabled={submitting}
                        onClick={() => {
                          clearIdempotencyAttempt();
                          setNotice("已准备一个使用新幂等键的任务。");
                        }}
                        type="button"
                      >
                        开始新任务
                      </button>
                    ) : null}
                    <button
                      className="rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-100 focus-visible:ring-offset-2 focus-visible:ring-offset-ink-950 disabled:cursor-not-allowed disabled:opacity-45"
                      disabled={!canSubmit}
                      onClick={() => void submitJob()}
                      type="button"
                    >
                      {submitting
                        ? "正在提交…"
                        : profile.price_per_generation_usd === null
                          ? "提交生成 · 费用以网关结算为准"
                          : `提交生成 · 预计 $${profile.price_per_generation_usd.toFixed(2)}`}
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              <div className="p-5 sm:p-6">
                <div
                  className="rounded-lg border border-amber-300/25 bg-amber-300/[0.08] px-4 py-4 text-sm leading-6 text-amber-100"
                  role="status"
                >
                  {catalog?.status === "disabled" ||
                  profile?.interaction_status === "disabled"
                    ? "音乐生成当前未启用，请在服务配置中开启后再试。"
                    : profile?.status_reason ??
                      "实时目录尚未确认这个模型可生成音乐，请返回模型招聘会重新选择。"}
                </div>
              </div>
            )}
          </section>

          <aside className="space-y-4 lg:sticky lg:top-24">
            <div className="surface-card rounded-lg p-5">
              <h2 className="text-sm font-semibold text-white">本次创作</h2>
              <dl className="mt-4 space-y-3 text-sm">
                <div>
                  <dt className="text-slate-400">模型</dt>
                  <dd className="mt-1 break-words font-medium text-slate-100">
                    {model.name}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-400">类型</dt>
                  <dd className="mt-1 font-medium text-slate-100">
                    {type.label}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-400">图片提示</dt>
                  <dd className="mt-1 font-medium text-slate-100">
                    {image ? "已添加" : "未添加"}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-400">输出</dt>
                  <dd className="mt-1 font-medium text-slate-100">MP3</dd>
                </div>
              </dl>
            </div>
            <div className="rounded-lg border border-amber-300/20 bg-amber-300/[0.07] p-5">
              <h2 className="text-sm font-semibold text-amber-100">
                保存与隐私
              </h2>
              <p className="mt-3 text-sm leading-6 text-slate-300">
                描述和图片会交给模型供应商处理。模镜不把描述、图片或音频写入数据库；生成音频只在本机临时保存 30 分钟，请及时下载。
              </p>
            </div>
          </aside>
        </div>

        <section className="surface-panel mt-6 overflow-hidden rounded-lg">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-5 py-4 sm:px-6">
            <div>
              <h2 className="text-lg font-semibold text-white">生成任务</h2>
              <p className="mt-1 text-sm text-slate-400">
                创作中每 2 秒检查一次状态，页面隐藏时暂停。
              </p>
            </div>
            <button
              className="rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-brand-300/40 hover:bg-brand-300/10 hover:text-brand-100 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={jobsLoading}
              onClick={() => {
                setJobsLoading(true);
                void loadJobs()
                  .catch((loadError) =>
                    setError(
                      loadError instanceof Error
                        ? loadError.message
                        : "暂时无法读取音乐任务。",
                    ),
                  )
                  .finally(() => setJobsLoading(false));
              }}
              type="button"
            >
              刷新任务
            </button>
          </div>

          {jobsLoading ? (
            <div
              aria-label="正在加载音乐任务"
              className="space-y-3 p-5 sm:p-6"
            >
              <div className="h-28 animate-pulse rounded-lg bg-white/[0.05] motion-reduce:animate-none" />
              <div className="h-28 animate-pulse rounded-lg bg-white/[0.04] motion-reduce:animate-none" />
            </div>
          ) : visibleJobs.length === 0 ? (
            <div className="px-5 py-12 text-center sm:px-6">
              <p className="font-semibold text-white">还没有生成任务</p>
              <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-slate-400">
                填写音乐描述并提交后，创作进度和试听入口会显示在这里。
              </p>
            </div>
          ) : (
            <div aria-live="polite" className="divide-y divide-white/10">
              {visibleJobs.map((job) => {
                const presentation = statusPresentation[job.status];
                const isActive = ACTIVE_STATUSES.has(job.status);
                const contentUrl = `/api/multimodal/audio/jobs/${encodeURIComponent(job.job_id)}/content`;
                return (
                  <article className="px-5 py-5 sm:px-6" key={job.job_id}>
                    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span
                            className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${presentation.className}`}
                          >
                            {presentation.label}
                          </span>
                          {job.parameters.has_image ? (
                            <span className="rounded-full bg-white/[0.06] px-2.5 py-1 text-xs text-slate-300">
                              使用图片提示
                            </span>
                          ) : null}
                          {job.execution_mode === "managed" ? (
                            <span className="rounded-full border border-cyan-300/20 bg-cyan-300/[0.07] px-2.5 py-1 text-xs text-cyan-100">
                              Provider 控制面
                            </span>
                          ) : null}
                        </div>
                        <p className="mt-3 text-sm leading-6 text-slate-300">
                          {presentation.description}
                        </p>
                        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
                          <span>{formatDate(job.created_at)}</span>
                          <span>{formatCost(job)}</span>
                          {job.output_bytes > 0 ? (
                            <span>{formatBytes(job.output_bytes)}</span>
                          ) : null}
                          {job.expires_at && job.status === "succeeded" ? (
                            <span>
                              临时保存至 {formatDate(job.expires_at)}
                            </span>
                          ) : null}
                        </div>
                        <p
                          className="mt-2 truncate font-mono text-[11px] text-slate-500"
                          title={job.job_id}
                        >
                          {job.job_id}
                        </p>
                      </div>

                      <div className="flex shrink-0 flex-wrap gap-2">
                        {isActive ? (
                          <button
                            className="rounded-full border border-white/15 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-brand-300/40 hover:bg-brand-300/10"
                            onClick={() => void refreshJob(job.job_id)}
                            type="button"
                          >
                            刷新状态
                          </button>
                        ) : null}
                        {job.execution_mode === "legacy" ? (
                          <button
                            className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
                              confirmDeleteId === job.job_id
                                ? "border-rose-300/40 bg-rose-300/10 text-rose-100"
                                : "border-white/15 text-slate-300 hover:border-rose-300/35 hover:text-rose-100"
                            }`}
                            onBlur={() => {
                              if (confirmDeleteId === job.job_id) {
                                setConfirmDeleteId("");
                              }
                            }}
                            onClick={() => void removeJob(job.job_id)}
                            type="button"
                          >
                            {confirmDeleteId === job.job_id
                              ? "确认移除记录"
                              : "移除记录"}
                          </button>
                        ) : null}
                      </div>
                    </div>

                    {pollWarnings[job.job_id] ? (
                      <p
                        className="mt-4 rounded-lg border border-amber-300/20 bg-amber-300/[0.07] px-4 py-3 text-sm leading-6 text-amber-100"
                        role="status"
                      >
                        {pollWarnings[job.job_id]} 本地任务状态未改变。
                      </p>
                    ) : null}

                    {job.error ? (
                      <p
                        className="mt-4 rounded-lg border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm leading-6 text-rose-100"
                        role="alert"
                      >
                        {job.error.message}
                      </p>
                    ) : null}

                    {job.provider_dispatch_state === "uncertain" ? (
                      <p
                        className="mt-4 rounded-lg border border-amber-300/25 bg-amber-300/10 px-4 py-3 text-sm leading-6 text-amber-100"
                        role="alert"
                      >
                        Provider 请求已派发，但结果尚未确认。系统不会自动重放同一任务；请先在供应商侧核对，再决定是否创建新任务。
                        {job.fallback_reason_codes.length
                          ? ` 原因：${job.fallback_reason_codes.join("、")}`
                          : ""}
                      </p>
                    ) : null}

                    {job.execution_mode === "managed" ? (
                      <p className="mt-3 text-xs leading-5 text-slate-400">
                        Managed 任务会保留脱敏幂等与审计记录，不能从此处移除。
                      </p>
                    ) : null}

                    <ProviderRouteReceiptSummary
                      receipts={job.provider_route_receipts}
                      title="音乐生成控制面"
                    />

                    {job.status === "succeeded" ? (
                      <div className="mt-5 rounded-lg bg-white/[0.045] p-4">
                        <audio
                          aria-label="生成的音乐"
                          className="w-full"
                          controls
                          preload="metadata"
                          src={contentUrl}
                        />
                        <div className="mt-3 flex justify-end">
                          <a
                            className="rounded-full border border-brand-300/35 px-3 py-1.5 text-xs font-semibold text-brand-100 transition hover:bg-brand-300/10"
                            download={`modelmirror-${job.job_id}.mp3`}
                            href={contentUrl}
                          >
                            下载 MP3
                          </a>
                        </div>
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
