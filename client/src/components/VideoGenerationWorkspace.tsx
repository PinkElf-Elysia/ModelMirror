import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
} from "react";
import { Link } from "react-router-dom";
import type { Model } from "../data/models";
import BrandLogo from "./BrandLogo";
import ResourceNav from "./ResourceNav";

const MAX_PROMPT_CHARS = 4_000;
const MAX_FIRST_FRAME_BYTES = 10 * 1024 * 1024;
const POLL_DELAYS = [30_000, 60_000, 120_000] as const;
const ACTIVE_STATUSES = new Set<VideoJobStatus>(["queued", "running"]);
const FIRST_FRAME_EXTENSIONS = new Set(["jpg", "jpeg", "png", "webp"]);

type VideoJobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "expired";

interface VideoModelProfile {
  model_id: string;
  operation: "analyze_video" | "generate_video";
  supported_resolutions: string[];
  supported_aspect_ratios: string[];
  supported_durations: number[];
  supports_first_frame: boolean;
  supports_generated_audio: boolean;
  supports_seed: boolean;
  pricing_skus: Record<string, string>;
  interaction_status: "ready" | "planned" | "unsupported";
}

interface VideoCatalogResponse {
  status: "online" | "stale" | "offline" | "disabled";
  stale: boolean;
  profiles: VideoModelProfile[];
}

interface VideoJob {
  job_id: string;
  status: VideoJobStatus;
  requested_model: string;
  actual_model: string | null;
  provider: "openrouter";
  generation_id: string | null;
  parameters: {
    duration: number | null;
    resolution: string | null;
    aspect_ratio: string | null;
    generate_audio: boolean;
    has_first_frame: boolean;
  };
  usage: {
    cost_usd: number | null;
    cost_kind: "actual" | "estimated" | "unavailable";
  };
  created_at: string;
  updated_at: string;
  error: {
    code: string;
    message: string;
  } | null;
  output_count: number;
}

interface VideoJobListResponse {
  jobs: VideoJob[];
}

interface VideoGenerationWorkspaceProps {
  model: Model;
}

const statusPresentation: Record<
  VideoJobStatus,
  { label: string; className: string; description: string }
> = {
  queued: {
    label: "排队中",
    className: "border-amber-300/30 bg-amber-300/10 text-amber-100",
    description: "服务已接收任务，正在等待生成资源。",
  },
  running: {
    label: "生成中",
    className: "border-sky-300/30 bg-sky-300/10 text-sky-100",
    description: "视频正在生成，页面会每 30 秒检查一次状态。",
  },
  succeeded: {
    label: "已完成",
    className: "border-emerald-300/30 bg-emerald-300/10 text-emerald-100",
    description: "视频已生成，可以播放或下载。",
  },
  failed: {
    label: "未完成",
    className: "border-rose-300/30 bg-rose-300/10 text-rose-100",
    description: "任务没有生成可用视频，请根据提示调整后重试。",
  },
  cancelled: {
    label: "上游已停止",
    className: "border-slate-300/25 bg-slate-300/10 text-slate-200",
    description: "供应商已经停止该任务。",
  },
  expired: {
    label: "已过期",
    className: "border-slate-300/25 bg-slate-300/10 text-slate-200",
    description: "任务或生成内容已超过供应商保留时间。",
  },
};

function responseErrorMessage(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object") {
    const detail = (payload as { detail?: unknown }).detail;
    if (detail && typeof detail === "object") {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string" && message.trim()) {
        return message;
      }
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

function mergeJob(current: VideoJob[], job: VideoJob) {
  const next = current.filter((item) => item.job_id !== job.job_id);
  return [job, ...next].sort(
    (left, right) =>
      new Date(right.created_at).getTime() -
      new Date(left.created_at).getTime(),
  );
}

function fileExtension(file: File) {
  return file.name.split(".").pop()?.toLowerCase() ?? "";
}

function formatBytes(bytes: number) {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
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

function formatCost(job: VideoJob) {
  if (job.usage.cost_usd === null) return "费用待网关结算";
  const prefix = job.usage.cost_kind === "estimated" ? "约 " : "";
  return `${prefix}$${job.usage.cost_usd.toFixed(4)}`;
}

function resolutionRank(value: string) {
  const normalized = value.trim().toLowerCase();
  if (normalized === "4k") return 2160;
  const match = normalized.match(/^(\d+)p$/);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

function pricingNumber(profile: VideoModelProfile, key: string) {
  const raw = profile.pricing_skus[key];
  if (raw === undefined) return null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function estimatedCost(
  profile: VideoModelProfile,
  {
    duration,
    resolution,
    generateAudio,
    hasFirstFrame,
  }: {
    duration: number | null;
    resolution: string;
    generateAudio: boolean;
    hasFirstFrame: boolean;
  },
) {
  if (duration === null || !resolution) return null;
  const resolutionKey = resolution.toLowerCase();
  const mode = hasFirstFrame ? "image_to_video" : "text_to_video";
  const dollarKeys = [
    generateAudio
      ? `duration_seconds_with_audio_${resolutionKey}`
      : `duration_seconds_without_audio_${resolutionKey}`,
    `${mode}_duration_seconds_${resolutionKey}`,
    `duration_seconds_${resolutionKey}`,
    generateAudio
      ? "duration_seconds_with_audio"
      : "duration_seconds_without_audio",
    `${mode}_duration_seconds`,
    "duration_seconds",
  ];
  let perSecond: number | null = null;
  for (const key of dollarKeys) {
    const value = pricingNumber(profile, key);
    if (value !== null) {
      perSecond = value;
      break;
    }
  }
  if (perSecond === null) {
    const cents =
      pricingNumber(
        profile,
        `cents_per_video_output_second_${resolutionKey}`,
      ) ?? pricingNumber(profile, "cents_per_video_output_second");
    if (cents !== null) perSecond = cents / 100;
  }
  if (perSecond === null) return null;
  const imageInputCents = hasFirstFrame
    ? (pricingNumber(profile, "cents_per_image_input") ?? 0)
    : 0;
  return perSecond * duration + imageInputCents / 100;
}

function defaultResolution(
  profile: VideoModelProfile,
  duration: number | null,
) {
  const ranked = [...profile.supported_resolutions].sort(
    (left, right) => resolutionRank(left) - resolutionRank(right),
  );
  const priced = ranked
    .map((resolution) => ({
      resolution,
      cost: estimatedCost(profile, {
        duration,
        resolution,
        generateAudio: false,
        hasFirstFrame: false,
      }),
    }))
    .filter(
      (item): item is { resolution: string; cost: number } =>
        item.cost !== null,
    )
    .sort(
      (left, right) =>
        left.cost - right.cost ||
        resolutionRank(left.resolution) - resolutionRank(right.resolution),
    );
  return priced[0]?.resolution ?? ranked[0] ?? "";
}

function newIdempotencyKey() {
  const randomPart =
    typeof crypto.randomUUID === "function"
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `video-${randomPart}`;
}

function validateFirstFrame(file: File) {
  if (!FIRST_FRAME_EXTENSIONS.has(fileExtension(file))) {
    return "首帧只支持 JPEG、PNG 和 WebP 图片。";
  }
  if (file.size <= 0) return "这张图片没有可读取的内容。";
  if (file.size > MAX_FIRST_FRAME_BYTES) {
    return "首帧图片超过 10 MiB，请压缩后重试。";
  }
  return "";
}

export default function VideoGenerationWorkspace({
  model,
}: VideoGenerationWorkspaceProps) {
  const [profile, setProfile] = useState<VideoModelProfile | null>(null);
  const [catalogStatus, setCatalogStatus] =
    useState<VideoCatalogResponse["status"]>("online");
  const [catalogStale, setCatalogStale] = useState(false);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [jobs, setJobs] = useState<VideoJob[]>([]);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [prompt, setPrompt] = useState("");
  const [duration, setDuration] = useState<number | null>(null);
  const [resolution, setResolution] = useState("");
  const [aspectRatio, setAspectRatio] = useState("");
  const [generateAudio, setGenerateAudio] = useState(false);
  const [seed, setSeed] = useState("");
  const [firstFrame, setFirstFrame] = useState<File | null>(null);
  const [firstFrameUrl, setFirstFrameUrl] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [refreshingIds, setRefreshingIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [ignoredIds, setIgnoredIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [pollWarnings, setPollWarnings] = useState<Record<string, string>>({});
  const [confirmDeleteId, setConfirmDeleteId] = useState("");
  const firstFrameInputRef = useRef<HTMLInputElement>(null);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const idempotencyKeyRef = useRef("");
  const jobsRef = useRef<VideoJob[]>([]);
  const ignoredIdsRef = useRef<Set<string>>(new Set());

  const visibleJobs = useMemo(
    () => jobs.filter((job) => job.requested_model === model.id),
    [jobs, model.id],
  );

  useEffect(() => {
    jobsRef.current = jobs;
  }, [jobs]);

  useEffect(() => {
    ignoredIdsRef.current = ignoredIds;
  }, [ignoredIds]);

  useEffect(() => {
    document.title = `生成视频 · ${model.name} · 模镜`;
  }, [model.name]);

  useEffect(() => {
    if (!firstFrame) {
      setFirstFrameUrl("");
      return undefined;
    }
    const nextUrl = URL.createObjectURL(firstFrame);
    setFirstFrameUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [firstFrame]);

  const loadJobs = useCallback(async () => {
    const response = await fetch("/api/multimodal/video/jobs");
    const payload = await readJson<VideoJobListResponse>(
      response,
      "暂时无法读取视频任务，请稍后刷新。",
    );
    setJobs(payload.jobs);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setCatalogLoading(true);
    setJobsLoading(true);
    setError("");

    void fetch("/api/multimodal/video/models", {
      signal: controller.signal,
    })
      .then((response) =>
        readJson<VideoCatalogResponse>(
          response,
          "暂时无法读取视频模型能力。",
        ),
      )
      .then((payload) => {
        if (controller.signal.aborted) return;
        const nextProfile =
          payload.profiles.find(
            (item) =>
              item.model_id === model.id &&
              item.operation === "generate_video",
          ) ?? null;
        setCatalogStatus(payload.status);
        setCatalogStale(payload.stale);
        setProfile(nextProfile);
        if (nextProfile) {
          const nextDuration =
            [...nextProfile.supported_durations].sort(
              (left, right) => left - right,
            )[0] ?? null;
          setDuration(nextDuration);
          setResolution(defaultResolution(nextProfile, nextDuration));
          setAspectRatio(
            nextProfile.supported_aspect_ratios.includes("16:9")
              ? "16:9"
              : (nextProfile.supported_aspect_ratios[0] ?? ""),
          );
        }
      })
      .catch((requestError) => {
        if (controller.signal.aborted) return;
        setProfile(null);
        setCatalogStatus("offline");
        setError(
          requestError instanceof Error
            ? requestError.message
            : "暂时无法读取视频模型能力。",
        );
      })
      .finally(() => {
        if (!controller.signal.aborted) setCatalogLoading(false);
      });

    void loadJobs()
      .catch((requestError) => {
        if (!controller.signal.aborted) {
          setError(
            requestError instanceof Error
              ? requestError.message
              : "暂时无法读取视频任务。",
          );
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setJobsLoading(false);
      });

    return () => controller.abort();
  }, [loadJobs, model.id]);

  const updateJob = useCallback((job: VideoJob) => {
    setJobs((current) => mergeJob(current, job));
    setPollWarnings((current) => {
      if (!current[job.job_id]) return current;
      const next = { ...current };
      delete next[job.job_id];
      return next;
    });
  }, []);

  const refreshJob = useCallback(
    async (jobId: string, announce = false) => {
      setRefreshingIds((current) => new Set(current).add(jobId));
      try {
        const response = await fetch(
          `/api/multimodal/video/jobs/${encodeURIComponent(jobId)}/refresh`,
          { method: "POST" },
        );
        const job = await readJson<VideoJob>(
          response,
          "暂时无法刷新任务，请稍后重试。",
        );
        updateJob(job);
        if (announce) setNotice("任务状态已更新。");
        return true;
      } catch (requestError) {
        const message =
          requestError instanceof Error
            ? requestError.message
            : "暂时无法刷新任务，请稍后重试。";
        setPollWarnings((current) => ({ ...current, [jobId]: message }));
        if (announce) setError(message);
        return false;
      } finally {
        setRefreshingIds((current) => {
          const next = new Set(current);
          next.delete(jobId);
          return next;
        });
      }
    },
    [updateJob],
  );

  useEffect(() => {
    let timer: number | null = null;
    let stopped = false;
    let polling = false;
    let errorStreak = 0;

    const clearTimer = () => {
      if (timer !== null) {
        window.clearTimeout(timer);
        timer = null;
      }
    };

    const schedule = () => {
      clearTimer();
      if (stopped || document.visibilityState === "hidden") return;
      const delay = POLL_DELAYS[Math.min(errorStreak, POLL_DELAYS.length - 1)];
      timer = window.setTimeout(() => void tick(), delay);
    };

    const tick = async () => {
      if (
        stopped ||
        polling ||
        document.visibilityState === "hidden"
      ) {
        schedule();
        return;
      }
      const activeJobs = jobsRef.current.filter(
        (job) =>
          job.requested_model === model.id &&
          ACTIVE_STATUSES.has(job.status) &&
          !ignoredIdsRef.current.has(job.job_id),
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
      if (document.visibilityState === "hidden") {
        clearTimer();
      } else {
        void tick();
      }
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
    idempotencyKeyRef.current = "";
    setNotice("");
    setError("");
  }

  function chooseFirstFrame(file: File | undefined) {
    if (!file || submitting) return;
    const validationError = validateFirstFrame(file);
    if (validationError) {
      setError(validationError);
      return;
    }
    setFirstFrame(file);
    markFormChanged();
  }

  function handleFirstFrameInput(event: ChangeEvent<HTMLInputElement>) {
    chooseFirstFrame(event.target.files?.[0]);
    event.target.value = "";
  }

  const estimate = profile
    ? estimatedCost(profile, {
        duration,
        resolution,
        generateAudio,
        hasFirstFrame: Boolean(firstFrame),
      })
    : null;

  const canSubmit =
    Boolean(profile) &&
    catalogStatus !== "offline" &&
    catalogStatus !== "disabled" &&
    prompt.trim().length > 0 &&
    prompt.trim().length <= MAX_PROMPT_CHARS &&
    duration !== null &&
    Boolean(resolution) &&
    !submitting;

  async function submitJob() {
    if (!profile || !canSubmit) return;
    const cleanPrompt = prompt.trim();
    if (!cleanPrompt) {
      setError("请先描述希望生成的视频内容。");
      promptRef.current?.focus();
      return;
    }
    const key =
      idempotencyKeyRef.current ||
      (idempotencyKeyRef.current = newIdempotencyKey());
    const form = new FormData();
    form.append("model_id", model.id);
    form.append("prompt", cleanPrompt);
    form.append("idempotency_key", key);
    if (duration !== null) form.append("duration", String(duration));
    if (resolution) form.append("resolution", resolution);
    if (aspectRatio) form.append("aspect_ratio", aspectRatio);
    form.append("generate_audio", String(generateAudio));
    if (seed.trim()) form.append("seed", seed.trim());
    if (firstFrame) form.append("first_frame", firstFrame, firstFrame.name);

    setSubmitting(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/multimodal/video/jobs", {
        method: "POST",
        body: form,
      });
      const job = await readJson<VideoJob>(
        response,
        "视频任务没有提交成功，请检查连接后重试。",
      );
      updateJob(job);
      setIgnoredIds((current) => {
        const next = new Set(current);
        next.delete(job.job_id);
        return next;
      });
      setNotice(
        job.status === "failed"
          ? "这次提交未被接受，请查看任务提示并调整设置。"
          : "任务已提交，可以离开页面，稍后返回查看结果。",
      );
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "视频任务没有提交成功，请稍后重试。",
      );
      try {
        await loadJobs();
      } catch {
        // The original actionable error remains visible.
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
        `/api/multimodal/video/jobs/${encodeURIComponent(jobId)}`,
        { method: "DELETE" },
      );
      await readJson(
        response,
        "暂时无法移除任务记录，请稍后重试。",
      );
      setJobs((current) => current.filter((job) => job.job_id !== jobId));
      setIgnoredIds((current) => {
        const next = new Set(current);
        next.delete(jobId);
        return next;
      });
      setConfirmDeleteId("");
      setNotice("本地任务记录已移除，上游任务未被取消。");
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "暂时无法移除任务记录。",
      );
    }
  }

  function toggleFollowing(jobId: string) {
    setIgnoredIds((current) => {
      const next = new Set(current);
      if (next.has(jobId)) next.delete(jobId);
      else next.add(jobId);
      return next;
    });
  }

  return (
    <main className="museum-grid min-h-screen pb-28 pt-5 text-slate-100 lg:pb-12 lg:pt-24">
      <ResourceNav activeResource="models" />
      <div className="mx-auto w-full max-w-[1180px] px-4 py-5 sm:px-6 lg:px-8">
        <header className="border-y border-hire-300/20 bg-ink-950/72 py-5 backdrop-blur-xl">
          <BrandLogo className="mb-4 lg:hidden" />
          <Link
            className="inline-flex rounded-full border border-white/10 bg-white/[0.05] px-3 py-1.5 text-sm font-medium text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-200"
            to="/models"
          >
            返回招聘会现场
          </Link>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="rounded-full border border-violet-300/30 bg-violet-300/10 px-3 py-1.5 text-xs font-semibold text-violet-100">
              视频生成
            </span>
            <span className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1.5 text-xs text-slate-300">
              异步任务，可离开页面后返回
            </span>
            {catalogStale ? (
              <span className="rounded-full border border-amber-300/25 bg-amber-300/10 px-3 py-1.5 text-xs text-amber-100">
                使用缓存能力目录
              </span>
            ) : null}
          </div>
          <h1 className="mt-4 text-2xl font-semibold text-white sm:text-4xl">
            使用 {model.name} 生成视频
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
            描述画面并选择模型明确支持的参数。提交后通常需要数十秒到数分钟，任务状态会保存在本机。
          </p>
        </header>

        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-start">
          <section className="surface-panel overflow-hidden rounded-lg">
            <div className="border-b border-white/10 px-5 py-4 sm:px-6">
              <h2 className="text-lg font-semibold text-white">生成设置</h2>
              <p className="mt-1 text-sm text-slate-400">
                默认选择可用的最短时长和最低费用分辨率。
              </p>
            </div>

            {catalogLoading ? (
              <div className="space-y-4 p-5 sm:p-6" aria-label="正在加载模型能力">
                <div className="h-28 animate-pulse rounded-lg bg-white/[0.055] motion-reduce:animate-none" />
                <div className="grid gap-4 sm:grid-cols-3">
                  <div className="h-20 animate-pulse rounded-lg bg-white/[0.045] motion-reduce:animate-none" />
                  <div className="h-20 animate-pulse rounded-lg bg-white/[0.045] motion-reduce:animate-none" />
                  <div className="h-20 animate-pulse rounded-lg bg-white/[0.045] motion-reduce:animate-none" />
                </div>
              </div>
            ) : profile ? (
              <div className="space-y-6 p-5 sm:p-6">
                <div>
                  <div className="mb-2 flex items-center justify-between gap-4">
                    <label
                      className="text-sm font-semibold text-slate-200"
                      htmlFor="video-generation-prompt"
                    >
                      视频描述
                    </label>
                    <span className="text-xs tabular-nums text-slate-400">
                      {prompt.length} / {MAX_PROMPT_CHARS}
                    </span>
                  </div>
                  <textarea
                    className="min-h-36 w-full resize-y rounded-lg border border-white/15 bg-ink-950 px-3 py-3 text-sm leading-6 text-white outline-none transition placeholder:text-slate-400 focus:border-brand-300/60 focus:ring-4 focus:ring-brand-300/10 disabled:cursor-not-allowed disabled:opacity-60"
                    disabled={submitting}
                    id="video-generation-prompt"
                    maxLength={MAX_PROMPT_CHARS}
                    onChange={(event) => {
                      setPrompt(event.target.value);
                      markFormChanged();
                    }}
                    placeholder="例如：清晨的海边车站，一列复古列车缓慢进站，固定广角镜头，柔和自然光。"
                    ref={promptRef}
                    value={prompt}
                  />
                </div>

                <div className="grid gap-4 sm:grid-cols-3">
                  <label className="block text-sm font-semibold text-slate-200">
                    时长
                    <select
                      className="mt-2 w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-2.5 text-sm text-white outline-none transition focus:border-brand-300/60 focus:ring-4 focus:ring-brand-300/10"
                      disabled={submitting}
                      onChange={(event) => {
                        setDuration(Number(event.target.value));
                        markFormChanged();
                      }}
                      value={duration ?? ""}
                    >
                      {profile.supported_durations
                        .slice()
                        .sort((left, right) => left - right)
                        .map((value) => (
                          <option key={value} value={value}>
                            {value} 秒
                          </option>
                        ))}
                    </select>
                  </label>
                  <label className="block text-sm font-semibold text-slate-200">
                    分辨率
                    <select
                      className="mt-2 w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-2.5 text-sm text-white outline-none transition focus:border-brand-300/60 focus:ring-4 focus:ring-brand-300/10"
                      disabled={submitting}
                      onChange={(event) => {
                        setResolution(event.target.value);
                        markFormChanged();
                      }}
                      value={resolution}
                    >
                      {profile.supported_resolutions.map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="block text-sm font-semibold text-slate-200">
                    画面比例
                    <select
                      className="mt-2 w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-2.5 text-sm text-white outline-none transition focus:border-brand-300/60 focus:ring-4 focus:ring-brand-300/10 disabled:cursor-not-allowed disabled:opacity-55"
                      disabled={
                        submitting ||
                        profile.supported_aspect_ratios.length === 0
                      }
                      onChange={(event) => {
                        setAspectRatio(event.target.value);
                        markFormChanged();
                      }}
                      value={aspectRatio}
                    >
                      {profile.supported_aspect_ratios.length === 0 ? (
                        <option value="">由模型决定</option>
                      ) : (
                        profile.supported_aspect_ratios.map((value) => (
                          <option key={value} value={value}>
                            {value}
                          </option>
                        ))
                      )}
                    </select>
                  </label>
                </div>

                {profile.supports_first_frame ? (
                  <div>
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-slate-200">
                          首帧图片（可选）
                        </p>
                        <p className="mt-1 text-xs leading-5 text-slate-400">
                          添加后将使用图片生成视频。支持 JPEG、PNG、WebP，最大 10 MiB。
                        </p>
                      </div>
                      <input
                        accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"
                        className="hidden"
                        disabled={submitting}
                        onChange={handleFirstFrameInput}
                        ref={firstFrameInputRef}
                        type="file"
                      />
                      <button
                        className="rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/40 hover:bg-hire-300/10 hover:text-hire-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-200 disabled:cursor-not-allowed disabled:opacity-50"
                        disabled={submitting}
                        onClick={() => firstFrameInputRef.current?.click()}
                        type="button"
                      >
                        {firstFrame ? "替换首帧" : "选择首帧"}
                      </button>
                    </div>
                    {firstFrame ? (
                      <div className="mt-4 flex flex-col gap-4 rounded-lg bg-white/[0.045] p-4 sm:flex-row sm:items-center">
                        {firstFrameUrl ? (
                          <img
                            alt="首帧预览"
                            className="aspect-video w-full rounded-lg bg-black object-contain sm:w-48"
                            src={firstFrameUrl}
                          />
                        ) : null}
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-semibold text-white">
                            {firstFrame.name}
                          </p>
                          <p className="mt-1 text-xs text-slate-400">
                            {fileExtension(firstFrame).toUpperCase()} ·{" "}
                            {formatBytes(firstFrame.size)}
                          </p>
                          <button
                            className="mt-3 rounded-full border border-rose-300/25 px-3 py-1.5 text-xs font-semibold text-rose-100 transition hover:bg-rose-300/10"
                            disabled={submitting}
                            onClick={() => {
                              setFirstFrame(null);
                              markFormChanged();
                            }}
                            type="button"
                          >
                            移除首帧
                          </button>
                        </div>
                      </div>
                    ) : null}
                  </div>
                ) : null}

                <div className="flex flex-wrap gap-4">
                  {profile.supports_generated_audio ? (
                    <label className="inline-flex min-h-11 cursor-pointer items-center gap-3 rounded-lg bg-white/[0.045] px-4 py-2.5 text-sm font-medium text-slate-200">
                      <input
                        checked={generateAudio}
                        className="h-4 w-4 accent-orange-300"
                        disabled={submitting}
                        onChange={(event) => {
                          setGenerateAudio(event.target.checked);
                          markFormChanged();
                        }}
                        type="checkbox"
                      />
                      同时生成音频
                    </label>
                  ) : null}
                  {profile.supports_seed ? (
                    <label className="flex min-h-11 items-center gap-3 rounded-lg bg-white/[0.045] px-4 py-2.5 text-sm font-medium text-slate-200">
                      随机种子
                      <input
                        className="w-32 rounded-md border border-white/15 bg-ink-950 px-2.5 py-1.5 text-sm text-white outline-none placeholder:text-slate-400 focus:border-brand-300/60"
                        disabled={submitting}
                        inputMode="numeric"
                        max={2_147_483_647}
                        min={0}
                        onChange={(event) => {
                          setSeed(event.target.value.replace(/\D/g, ""));
                          markFormChanged();
                        }}
                        placeholder="可选"
                        type="text"
                        value={seed}
                      />
                    </label>
                  ) : null}
                </div>

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
                      {estimate === null
                        ? "费用以网关结算为准"
                        : `目录估算 $${estimate.toFixed(4)}`}
                    </p>
                    <p className="mt-1 text-xs text-slate-400">
                      提交会产生费用，最终金额以网关回执为准。
                    </p>
                  </div>
                  <button
                    className="rounded-full bg-hire-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-100 focus-visible:ring-offset-2 focus-visible:ring-offset-ink-950 disabled:cursor-not-allowed disabled:opacity-45"
                    disabled={!canSubmit}
                    onClick={() => void submitJob()}
                    type="button"
                  >
                    {submitting
                      ? "正在提交…"
                      : estimate === null
                        ? "提交生成 · 费用以网关结算为准"
                        : `提交生成 · 预计 $${estimate.toFixed(4)}`}
                  </button>
                </div>
              </div>
            ) : (
              <div className="p-5 sm:p-6">
                <div
                  className="rounded-lg border border-amber-300/25 bg-amber-300/[0.08] px-4 py-4 text-sm leading-6 text-amber-100"
                  role="status"
                >
                  {catalogStatus === "disabled"
                    ? "视频生成当前未启用，请在服务设置中开启后再试。"
                    : "实时目录尚未确认这个模型可生成视频，请返回招聘会选择标有“生成视频”的模型。"}
                </div>
              </div>
            )}
          </section>

          <aside className="space-y-4 lg:sticky lg:top-24">
            <div className="surface-card rounded-lg p-5">
              <h2 className="text-sm font-semibold text-white">提交前确认</h2>
              <dl className="mt-4 space-y-3 text-sm">
                <div>
                  <dt className="text-slate-400">模型</dt>
                  <dd className="mt-1 break-words font-medium text-slate-100">
                    {model.name}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-400">方式</dt>
                  <dd className="mt-1 font-medium text-slate-100">
                    {firstFrame ? "首帧图生视频" : "文字生成视频"}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-400">当前设置</dt>
                  <dd className="mt-1 leading-6 text-slate-300">
                    {[duration ? `${duration} 秒` : "", resolution, aspectRatio]
                      .filter(Boolean)
                      .join(" · ") || "等待模型能力"}
                  </dd>
                </div>
              </dl>
            </div>
            <div className="rounded-lg border border-amber-300/20 bg-amber-300/[0.07] p-5">
              <h2 className="text-sm font-semibold text-amber-100">
                内容与隐私提示
              </h2>
              <p className="mt-3 text-sm leading-6 text-slate-300">
                视频生成不支持零数据保留。提示词和首帧会交给模型供应商处理，供应商可能按自身政策临时保留内容。模镜只保存任务元数据，不保存提示词、首帧或视频正文。
              </p>
            </div>
          </aside>
        </div>

        <section className="surface-panel mt-6 overflow-hidden rounded-lg">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-5 py-4 sm:px-6">
            <div>
              <h2 className="text-lg font-semibold text-white">生成任务</h2>
              <p className="mt-1 text-sm text-slate-400">
                运行中的任务会自动刷新，隐藏页面时暂停检查。
              </p>
            </div>
            <button
              className="rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-brand-300/40 hover:bg-brand-300/10 hover:text-brand-100 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={jobsLoading}
              onClick={() => {
                setJobsLoading(true);
                void loadJobs()
                  .catch((requestError) =>
                    setError(
                      requestError instanceof Error
                        ? requestError.message
                        : "暂时无法读取视频任务。",
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
            <div className="space-y-3 p-5 sm:p-6" aria-label="正在加载视频任务">
              <div className="h-28 animate-pulse rounded-lg bg-white/[0.05] motion-reduce:animate-none" />
              <div className="h-28 animate-pulse rounded-lg bg-white/[0.04] motion-reduce:animate-none" />
            </div>
          ) : visibleJobs.length === 0 ? (
            <div className="px-5 py-12 text-center sm:px-6">
              <p className="font-semibold text-white">还没有生成任务</p>
              <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-slate-400">
                填写视频描述并提交后，排队、生成和完成状态都会显示在这里。
              </p>
            </div>
          ) : (
            <div
              aria-live="polite"
              className="divide-y divide-white/10"
            >
              {visibleJobs.map((job) => {
                const presentation = statusPresentation[job.status];
                const isActive = ACTIVE_STATUSES.has(job.status);
                const ignored = ignoredIds.has(job.job_id);
                const refreshing = refreshingIds.has(job.job_id);
                return (
                  <article
                    className="px-5 py-5 sm:px-6"
                    key={job.job_id}
                  >
                    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span
                            className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${presentation.className}`}
                          >
                            {presentation.label}
                          </span>
                          {ignored && isActive ? (
                            <span className="rounded-full border border-white/10 bg-white/[0.045] px-2.5 py-1 text-xs text-slate-300">
                              已停止自动刷新
                            </span>
                          ) : null}
                        </div>
                        <p className="mt-3 text-sm leading-6 text-slate-300">
                          {presentation.description}
                        </p>
                        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
                          <span>{formatDate(job.created_at)}</span>
                          <span>
                            {[
                              job.parameters.duration
                                ? `${job.parameters.duration} 秒`
                                : "",
                              job.parameters.resolution,
                              job.parameters.aspect_ratio,
                            ]
                              .filter(Boolean)
                              .join(" · ") || "参数由模型决定"}
                          </span>
                          <span>{formatCost(job)}</span>
                          {job.parameters.has_first_frame ? (
                            <span>使用首帧</span>
                          ) : null}
                          {job.parameters.generate_audio ? (
                            <span>包含音频</span>
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
                          <>
                            <button
                              className="rounded-full border border-white/15 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-brand-300/40 hover:bg-brand-300/10 disabled:cursor-not-allowed disabled:opacity-50"
                              disabled={refreshing}
                              onClick={() => void refreshJob(job.job_id, true)}
                              type="button"
                            >
                              {refreshing ? "刷新中…" : "刷新状态"}
                            </button>
                            <button
                              className="rounded-full border border-white/15 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:bg-white/[0.06]"
                              onClick={() => toggleFollowing(job.job_id)}
                              type="button"
                            >
                              {ignored ? "继续关注" : "停止关注"}
                            </button>
                          </>
                        ) : null}
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

                    {job.status === "succeeded" && job.output_count > 0 ? (
                      <div className="mt-5 space-y-5">
                        {Array.from(
                          { length: job.output_count },
                          (_, index) => {
                            const contentUrl = `/api/multimodal/video/jobs/${encodeURIComponent(job.job_id)}/content?index=${index}`;
                            return (
                              <div
                                className="overflow-hidden rounded-lg bg-black"
                                key={contentUrl}
                              >
                                <video
                                  aria-label={`生成视频 ${index + 1}`}
                                  className="aspect-video w-full object-contain"
                                  controls
                                  preload="metadata"
                                  src={contentUrl}
                                />
                                <div className="flex items-center justify-between gap-3 bg-white/[0.055] px-4 py-3">
                                  <span className="text-xs text-slate-400">
                                    输出 {index + 1}
                                  </span>
                                  <a
                                    className="rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-hire-200"
                                    download={`modelmirror-video-${index + 1}.mp4`}
                                    href={contentUrl}
                                  >
                                    下载视频
                                  </a>
                                </div>
                              </div>
                            );
                          },
                        )}
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
