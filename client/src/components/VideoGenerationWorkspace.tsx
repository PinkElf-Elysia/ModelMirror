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
import {
  estimateVideoCost,
  estimateVideoUpscaleCost,
  supportedAspectRatiosForResolution,
  videoUpscaleUnitRate,
} from "../utils/videoCostEstimate";
import BrandLogo from "./BrandLogo";
import ResourceNav from "./ResourceNav";

const MAX_PROMPT_CHARS = 4_000;
const MAX_FIRST_FRAME_BYTES = 10 * 1024 * 1024;
const MAX_REFERENCE_IMAGE_BYTES = 30 * 1024 * 1024;
const MAX_REFERENCE_IMAGE_COUNT = 3;
const MAX_SOURCE_VIDEO_BYTES = 20 * 1024 * 1024;
const POLL_DELAYS = [30_000, 60_000, 120_000] as const;
const ACTIVE_STATUSES = new Set<VideoJobStatus>(["queued", "running"]);
const FIRST_FRAME_EXTENSIONS = new Set(["jpg", "jpeg", "png", "webp"]);
const SOURCE_VIDEO_EXTENSIONS = new Set(["mp4", "mpeg", "mpg", "mov", "webm"]);

type VideoJobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "expired";

type ProviderOptionValue = string | number | boolean;

interface VideoProviderOption {
  key: string;
  label: string;
  type: "text" | "number" | "boolean" | "select";
  options: string[];
  min: number | null;
  max: number | null;
  default: ProviderOptionValue | null;
}

interface VideoModelProfile {
  model_id: string;
  operation: "analyze_video" | "generate_video";
  supported_input_sources: ("file" | "url")[];
  supported_resolutions: string[];
  supported_aspect_ratios: string[];
  supported_sizes: string[];
  supported_durations: number[];
  supported_frame_types: ("first_frame" | "last_frame")[];
  supports_first_frame: boolean;
  supports_reference_images: boolean;
  max_reference_images: number | null;
  supports_generated_audio: boolean;
  supports_seed: boolean;
  requires_source_video: boolean;
  upscale_factor: { min: number; max: number } | null;
  creativity: number[];
  provider_options: VideoProviderOption[];
  pricing_skus: Record<string, string>;
  interaction_status: "ready" | "planned" | "unsupported";
  status_reason?: string | null;
  verification_entry_enabled?: boolean;
  verification_requires_cost_estimate?: boolean;
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
    task_type: "generate" | "upscale";
    duration: number | null;
    resolution: string | null;
    aspect_ratio: string | null;
    generate_audio: boolean;
    has_first_frame: boolean;
    has_last_frame: boolean;
    reference_image_count: number;
    has_source_video: boolean;
    upscale_factor: number | null;
    creativity: number | null;
    provider_option_keys: string[];
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

interface SelectedReferenceImage {
  id: string;
  file: File;
  previewUrl: string;
}

interface SourceVideoMetadata {
  durationSeconds: number;
  width: number;
  height: number;
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

function defaultResolution(
  profile: VideoModelProfile,
  duration: number | null,
  aspectRatio: string,
) {
  const ranked = [...profile.supported_resolutions].sort(
    (left, right) => resolutionRank(left) - resolutionRank(right),
  );
  const priced = ranked
    .map((resolution) => ({
      resolution,
      cost: estimateVideoCost(profile, {
        duration,
        resolution,
        aspectRatio,
        generateAudio: false,
        imageInputCount: 0,
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

function validateGenerationImage(file: File, label: string) {
  if (!FIRST_FRAME_EXTENSIONS.has(fileExtension(file))) {
    return `${label}只支持 JPEG、PNG 和 WebP 图片。`;
  }
  if (file.size <= 0) return "这张图片没有可读取的内容。";
  if (file.size > MAX_FIRST_FRAME_BYTES) {
    return `${label}超过 10 MiB，请压缩后重试。`;
  }
  return "";
}

function validateSourceVideo(file: File) {
  if (!SOURCE_VIDEO_EXTENSIONS.has(fileExtension(file))) {
    return "源视频只支持 MP4、MPEG、MOV 和 WebM。";
  }
  if (file.size <= 0) return "源视频没有可读取的内容。";
  if (file.size > MAX_SOURCE_VIDEO_BYTES) {
    return "源视频超过 20 MiB，请压缩或缩短后重试。";
  }
  return "";
}

function readSourceVideoMetadata(file: File) {
  return new Promise<SourceVideoMetadata | null>((resolve) => {
    const video = document.createElement("video");
    const url = URL.createObjectURL(file);
    const cleanup = () => {
      URL.revokeObjectURL(url);
      video.removeAttribute("src");
      video.load();
    };
    video.preload = "metadata";
    video.onloadedmetadata = () => {
      const metadata = {
        durationSeconds: video.duration,
        width: video.videoWidth,
        height: video.videoHeight,
      };
      cleanup();
      resolve(
        Number.isFinite(metadata.durationSeconds) &&
          metadata.durationSeconds > 0 &&
          metadata.width > 0 &&
          metadata.height > 0
          ? metadata
          : null,
      );
    };
    video.onerror = () => {
      cleanup();
      resolve(null);
    };
    video.src = url;
  });
}

function generationModeLabel({
  hasFirstFrame,
  hasLastFrame,
  referenceImageCount,
}: {
  hasFirstFrame: boolean;
  hasLastFrame: boolean;
  referenceImageCount: number;
}) {
  if (referenceImageCount > 0) return "参考图生成";
  if (hasFirstFrame && hasLastFrame) return "首尾帧生成";
  if (hasFirstFrame) return "首帧图生视频";
  if (hasLastFrame) return "尾帧引导生成";
  return "文字生成视频";
}

export default function VideoGenerationWorkspace({
  model,
}: VideoGenerationWorkspaceProps) {
  const [profile, setProfile] = useState<VideoModelProfile | null>(null);
  const [catalogStatus, setCatalogStatus] =
    useState<VideoCatalogResponse["status"]>("online");
  const [catalogStale, setCatalogStale] = useState(false);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogRefreshing, setCatalogRefreshing] = useState(false);
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
  const [lastFrame, setLastFrame] = useState<File | null>(null);
  const [lastFrameUrl, setLastFrameUrl] = useState("");
  const [referenceImages, setReferenceImages] = useState<
    SelectedReferenceImage[]
  >([]);
  const [sourceType, setSourceType] = useState<"file" | "url">("file");
  const [sourceVideo, setSourceVideo] = useState<File | null>(null);
  const [sourceVideoUrl, setSourceVideoUrl] = useState("");
  const [sourceVideoMetadata, setSourceVideoMetadata] =
    useState<SourceVideoMetadata | null>(null);
  const [upscaleFactor, setUpscaleFactor] = useState(2);
  const [creativity, setCreativity] = useState(0);
  const [providerOptionValues, setProviderOptionValues] = useState<
    Record<string, ProviderOptionValue>
  >({});
  const [providerOptionKeys, setProviderOptionKeys] = useState<Set<string>>(
    () => new Set(),
  );
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [manualVerificationConfirmed, setManualVerificationConfirmed] =
    useState(false);
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
  const lastFrameInputRef = useRef<HTMLInputElement>(null);
  const referenceInputRef = useRef<HTMLInputElement>(null);
  const sourceVideoInputRef = useRef<HTMLInputElement>(null);
  const referenceReplaceIndexRef = useRef<number | null>(null);
  const referenceImagesRef = useRef<SelectedReferenceImage[]>([]);
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
    referenceImagesRef.current = referenceImages;
  }, [referenceImages]);

  useEffect(
    () => () => {
      referenceImagesRef.current.forEach((item) =>
        URL.revokeObjectURL(item.previewUrl),
      );
    },
    [],
  );

  useEffect(() => {
    document.title = `${profile?.requires_source_video ? "增强视频" : "生成视频"} · ${model.name} · 模镜`;
  }, [model.name, profile?.requires_source_video]);

  useEffect(() => {
    if (!firstFrame) {
      setFirstFrameUrl("");
      return undefined;
    }
    const nextUrl = URL.createObjectURL(firstFrame);
    setFirstFrameUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [firstFrame]);

  useEffect(() => {
    if (!lastFrame) {
      setLastFrameUrl("");
      return undefined;
    }
    const nextUrl = URL.createObjectURL(lastFrame);
    setLastFrameUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [lastFrame]);

  const loadJobs = useCallback(async () => {
    const response = await fetch("/api/multimodal/video/jobs");
    const payload = await readJson<VideoJobListResponse>(
      response,
      "暂时无法读取视频任务，请稍后刷新。",
    );
    setJobs(payload.jobs);
  }, []);

  const loadCatalog = useCallback(
    async (signal?: AbortSignal, refresh = false) => {
      const response = await fetch(
        `/api/multimodal/video/models${refresh ? "?refresh=true" : ""}`,
        { signal },
      );
      const payload = await readJson<VideoCatalogResponse>(
        response,
        "暂时无法读取视频模型能力。",
      );
      const nextProfile =
        payload.profiles.find(
          (item) =>
            item.model_id === model.id &&
            item.operation === "generate_video",
        ) ?? null;
      setCatalogStatus(payload.status);
      setCatalogStale(payload.stale);
      setProfile(nextProfile);
      setManualVerificationConfirmed(false);
      if (!nextProfile) return payload;

      if (nextProfile.requires_source_video) {
        const factorRange = nextProfile.upscale_factor;
        setUpscaleFactor((current) =>
          factorRange &&
          current >= factorRange.min &&
          current <= factorRange.max
            ? current
            : (factorRange?.min ?? 2),
        );
        setCreativity((current) =>
          nextProfile.creativity.includes(current)
            ? current
            : (nextProfile.creativity[0] ?? 0),
        );
        setFirstFrame(null);
        setLastFrame(null);
        setReferenceImages((current) => {
          current.forEach((item) => URL.revokeObjectURL(item.previewUrl));
          return [];
        });
        setGenerateAudio(false);
        setSeed("");
      }

      const sortedDurations = [...nextProfile.supported_durations].sort(
        (left, right) => left - right,
      );
      const fallbackDuration = sortedDurations[0] ?? null;
      const fallbackAspectRatio = nextProfile.supported_aspect_ratios.includes(
        "16:9",
      )
        ? "16:9"
        : (nextProfile.supported_aspect_ratios[0] ?? "");
      setDuration((current) =>
        current !== null &&
        nextProfile.supported_durations.includes(current)
          ? current
          : fallbackDuration,
      );
      setResolution((current) =>
        nextProfile.supported_resolutions.includes(current)
          ? current
          : defaultResolution(
              nextProfile,
              fallbackDuration,
              fallbackAspectRatio,
            ),
      );
      setAspectRatio((current) =>
        nextProfile.supported_aspect_ratios.includes(current)
          ? current
          : fallbackAspectRatio,
      );

      const supportsFirst =
        nextProfile.supports_first_frame ||
        nextProfile.supported_frame_types.includes("first_frame");
      const supportsLast =
        nextProfile.supported_frame_types.includes("last_frame");
      if (!supportsFirst) setFirstFrame(null);
      if (!supportsLast) setLastFrame(null);
      setReferenceImages((current) => {
        const limit = Math.min(
          MAX_REFERENCE_IMAGE_COUNT,
          nextProfile.max_reference_images ?? 0,
        );
        const keep = nextProfile.supports_reference_images
          ? current.slice(0, limit)
          : [];
        current.slice(keep.length).forEach((item) => {
          URL.revokeObjectURL(item.previewUrl);
        });
        return keep;
      });

      const definitions = new Map(
        nextProfile.provider_options.map((option) => [option.key, option]),
      );
      setProviderOptionValues((current) =>
        Object.fromEntries(
          nextProfile.provider_options.map((option) => [
            option.key,
            current[option.key] ?? "",
          ]),
        ),
      );
      setProviderOptionKeys(
        (current) =>
          new Set([...current].filter((key) => definitions.has(key))),
      );
      return payload;
    },
    [model.id],
  );

  useEffect(() => {
    const controller = new AbortController();
    setCatalogLoading(true);
    setJobsLoading(true);
    setError("");

    void loadCatalog(controller.signal)
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
  }, [loadCatalog, loadJobs]);

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

  async function chooseSourceVideo(file: File | undefined) {
    if (!file || submitting) return;
    const validationError = validateSourceVideo(file);
    if (validationError) {
      setError(validationError);
      return;
    }
    setSourceVideo(file);
    setSourceVideoMetadata(null);
    markFormChanged();
    setSourceVideoMetadata(await readSourceVideoMetadata(file));
  }

  function handleSourceVideoInput(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    void chooseSourceVideo(file);
  }

  function chooseFirstFrame(file: File | undefined) {
    if (!file || submitting) return;
    const validationError = validateGenerationImage(file, "首帧图片");
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

  function chooseLastFrame(file: File | undefined) {
    if (!file || submitting) return;
    const validationError = validateGenerationImage(file, "尾帧图片");
    if (validationError) {
      setError(validationError);
      return;
    }
    setLastFrame(file);
    markFormChanged();
  }

  function handleLastFrameInput(event: ChangeEvent<HTMLInputElement>) {
    chooseLastFrame(event.target.files?.[0]);
    event.target.value = "";
  }

  function handleReferenceInput(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || submitting) return;
    const validationError = validateGenerationImage(file, "参考图");
    if (validationError) {
      setError(validationError);
      return;
    }
    const replaceIndex = referenceReplaceIndexRef.current;
    referenceReplaceIndexRef.current = null;
    setReferenceImages((current) => {
      const nextBytes =
        current.reduce((total, item) => total + item.file.size, 0) -
        (replaceIndex === null
          ? 0
          : (current[replaceIndex]?.file.size ?? 0)) +
        file.size;
      if (nextBytes > MAX_REFERENCE_IMAGE_BYTES) {
        setError("参考图合计超过 30 MiB，请压缩或移除图片后重试。");
        return current;
      }
      const nextItem: SelectedReferenceImage = {
        id:
          typeof crypto.randomUUID === "function"
            ? crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(16).slice(2)}`,
        file,
        previewUrl: URL.createObjectURL(file),
      };
      if (replaceIndex !== null && current[replaceIndex]) {
        const next = [...current];
        URL.revokeObjectURL(next[replaceIndex].previewUrl);
        next[replaceIndex] = nextItem;
        return next;
      }
      const limit = Math.min(
        MAX_REFERENCE_IMAGE_COUNT,
        profile?.max_reference_images ?? 0,
      );
      if (current.length >= limit) {
        URL.revokeObjectURL(nextItem.previewUrl);
        setError(`当前模型最多使用 ${limit} 张参考图。`);
        return current;
      }
      return [...current, nextItem];
    });
    markFormChanged();
  }

  function removeReferenceImage(index: number) {
    setReferenceImages((current) => {
      const item = current[index];
      if (!item) return current;
      URL.revokeObjectURL(item.previewUrl);
      return current.filter((_, itemIndex) => itemIndex !== index);
    });
    markFormChanged();
  }

  function moveReferenceImage(index: number, direction: -1 | 1) {
    setReferenceImages((current) => {
      const target = index + direction;
      if (target < 0 || target >= current.length) return current;
      const next = [...current];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    markFormChanged();
  }

  function updateProviderOption(
    key: string,
    value: ProviderOptionValue,
    enabled = true,
  ) {
    setProviderOptionValues((current) => ({ ...current, [key]: value }));
    setProviderOptionKeys((current) => {
      const next = new Set(current);
      if (enabled && !(typeof value === "string" && !value.trim())) {
        next.add(key);
      } else {
        next.delete(key);
      }
      return next;
    });
    markFormChanged();
  }

  const supportsFirstFrame = Boolean(
    profile?.supports_first_frame ||
      profile?.supported_frame_types.includes("first_frame"),
  );
  const isUpscaler = Boolean(profile?.requires_source_video);
  const supportsLastFrame = Boolean(
    profile?.supported_frame_types.includes("last_frame"),
  );
  const referenceLimit = Math.min(
    MAX_REFERENCE_IMAGE_COUNT,
    profile?.max_reference_images ?? 0,
  );
  const providerPayload = useMemo(() => {
    const payload: Record<string, ProviderOptionValue> = {};
    for (const key of providerOptionKeys) {
      const value = providerOptionValues[key];
      if (value !== undefined && !(typeof value === "string" && !value.trim())) {
        payload[key] = value;
      }
    }
    return payload;
  }, [providerOptionKeys, providerOptionValues]);
  const providerOptionCount = Object.keys(providerPayload).length;
  const imageInputCount =
    Number(Boolean(firstFrame)) +
    Number(Boolean(lastFrame)) +
    referenceImages.length;
  const enhancedInputsSelected =
    Boolean(lastFrame) ||
    referenceImages.length > 0 ||
    providerOptionCount > 0;
  const capabilityRefreshRequired =
    catalogStale && (enhancedInputsSelected || isUpscaler);
  const selectableAspectRatios = profile
    ? supportedAspectRatiosForResolution(profile, resolution)
    : [];

  const estimate =
    profile && isUpscaler && sourceVideoMetadata
      ? estimateVideoUpscaleCost(profile, {
          ...sourceVideoMetadata,
          upscaleFactor,
          creativity,
        })
      : profile && !isUpscaler
        ? estimateVideoCost(profile, {
            duration,
            resolution,
            aspectRatio,
            generateAudio,
            imageInputCount,
          })
        : null;
  const upscaleUnitRate =
    profile && isUpscaler
      ? videoUpscaleUnitRate(profile, creativity)
      : null;
  const sourceReady =
    sourceType === "file"
      ? Boolean(sourceVideo)
      : /^https:\/\/[^\s]+$/i.test(sourceVideoUrl.trim());
  const factorRange = profile?.upscale_factor ?? null;
  const upscalerSelectionValid =
    sourceReady &&
    factorRange !== null &&
    upscaleFactor >= factorRange.min &&
    upscaleFactor <= factorRange.max &&
    Boolean(profile?.creativity.includes(creativity));

  const canSubmit =
    Boolean(profile) &&
    catalogStatus !== "offline" &&
    catalogStatus !== "disabled" &&
    prompt.trim().length <= MAX_PROMPT_CHARS &&
    (isUpscaler
      ? upscalerSelectionValid
      : prompt.trim().length > 0 && duration !== null && Boolean(resolution)) &&
    (!profile?.verification_entry_enabled || manualVerificationConfirmed) &&
    (!profile?.verification_requires_cost_estimate || estimate !== null) &&
    !capabilityRefreshRequired &&
    !submitting;

  async function refreshCapabilities() {
    setCatalogRefreshing(true);
    setError("");
    setNotice("");
    try {
      const payload = await loadCatalog(undefined, true);
      markFormChanged();
      if (payload.stale) {
        setError(
          "仍无法取得最新模型能力。请关闭尾帧、参考图或高级设置后提交，或稍后重试。",
        );
      } else {
        setNotice("模型能力已刷新，已移除当前不再支持的设置。");
      }
    } catch (requestError) {
      setError(
        requestError instanceof Error
          ? requestError.message
          : "模型能力刷新失败，请稍后重试。",
      );
    } finally {
      setCatalogRefreshing(false);
    }
  }

  async function submitJob() {
    if (!profile || !canSubmit) return;
    const cleanPrompt = prompt.trim();
    if (!isUpscaler && !cleanPrompt) {
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
    if (isUpscaler) {
      form.append("source_type", sourceType);
      if (sourceType === "file" && sourceVideo) {
        form.append("source_video", sourceVideo, sourceVideo.name);
      } else if (sourceType === "url") {
        form.append("source_video_url", sourceVideoUrl.trim());
      }
      form.append("upscale_factor", String(upscaleFactor));
      form.append("creativity", String(creativity));
    } else {
      if (duration !== null) form.append("duration", String(duration));
      if (resolution) form.append("resolution", resolution);
      if (aspectRatio) form.append("aspect_ratio", aspectRatio);
      form.append("generate_audio", String(generateAudio));
      if (seed.trim()) form.append("seed", seed.trim());
      if (firstFrame) form.append("first_frame", firstFrame, firstFrame.name);
      if (lastFrame) form.append("last_frame", lastFrame, lastFrame.name);
      referenceImages.forEach((item) => {
        form.append("reference_images", item.file, item.file.name);
      });
      if (providerOptionCount > 0) {
        form.append("provider_options", JSON.stringify(providerPayload));
      }
    }

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
              {isUpscaler ? "视频增强" : "视频生成"}
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
            使用 {model.name} {isUpscaler ? "增强视频" : "生成视频"}
          </h1>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
            {isUpscaler
              ? "提供一个源视频并选择放大倍数与增强模式。提交后按异步任务处理，状态会保存在本机。"
              : "描述画面并选择模型明确支持的参数。提交后通常需要数十秒到数分钟，任务状态会保存在本机。"}
          </p>
        </header>

        <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-start">
          <section className="surface-panel overflow-hidden rounded-lg">
            <div className="border-b border-white/10 px-5 py-4 sm:px-6">
              <h2 className="text-lg font-semibold text-white">
                {isUpscaler ? "增强设置" : "生成设置"}
              </h2>
              <p className="mt-1 text-sm text-slate-400">
                {isUpscaler
                  ? "源视频不会保存在模镜；费用按输出百万像素秒估算。"
                  : "默认选择可用的最短时长和最低费用分辨率。"}
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
                {profile.verification_entry_enabled ? (
                  <div
                    className="rounded-lg border border-amber-300/30 bg-amber-300/10 px-4 py-3 text-sm leading-6 text-amber-50"
                    role="status"
                  >
                    <p className="font-semibold">人工行为核验</p>
                    <p className="mt-1 text-amber-100/90">
                      该模型的实时参数契约已确认，但生成结果尚未验收。页面已默认选择目录中的最短时长和最低费用分辨率；提交会产生实际费用，完成后请检查视频能否完整播放和下载。
                    </p>
                  </div>
                ) : null}
                {profile.verification_requires_cost_estimate && estimate === null ? (
                  <div
                    className="rounded-lg border border-rose-300/30 bg-rose-300/10 px-4 py-3 text-sm leading-6 text-rose-100"
                    role="alert"
                  >
                    当前实时目录无法可靠估算该高费用模型，本轮禁止提交。请刷新模型能力；价格恢复前仅保留核验步骤。
                  </div>
                ) : null}
                <div>
                  <div className="mb-2 flex items-center justify-between gap-4">
                    <label
                      className="text-sm font-semibold text-slate-200"
                      htmlFor="video-generation-prompt"
                    >
                      {isUpscaler ? "增强说明（可选）" : "视频描述"}
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
                    placeholder={
                      isUpscaler
                        ? "可选：例如保留人物面部与字幕边缘，减少压缩噪点。"
                        : "例如：清晨的海边车站，一列复古列车缓慢进站，固定广角镜头，柔和自然光。"
                    }
                    ref={promptRef}
                    value={prompt}
                  />
                </div>

                {isUpscaler ? (
                  <div className="space-y-5 rounded-lg bg-white/[0.04] p-4">
                    <div>
                      <h3 className="text-sm font-semibold text-white">源视频</h3>
                      <p className="mt-1 text-xs leading-5 text-slate-400">
                        必须提供一个 MP4、MPEG、MOV 或 WebM 视频；本地文件最大 20 MiB。
                      </p>
                    </div>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {profile.supported_input_sources.map((value) => (
                        <button
                          className={`rounded-lg border px-3 py-2 text-sm font-semibold transition ${
                            sourceType === value
                              ? "border-hire-300/50 bg-hire-300/10 text-hire-100"
                              : "border-white/10 bg-ink-950/60 text-slate-300 hover:border-white/20"
                          }`}
                          disabled={submitting}
                          key={value}
                          onClick={() => {
                            setSourceType(value);
                            markFormChanged();
                          }}
                          type="button"
                        >
                          {value === "file" ? "上传本地视频" : "使用 HTTPS 网址"}
                        </button>
                      ))}
                    </div>
                    {sourceType === "file" ? (
                      <div className="flex flex-wrap items-center gap-3">
                        <input
                          accept=".mp4,.mpeg,.mpg,.mov,.webm,video/mp4,video/mpeg,video/quicktime,video/webm"
                          className="hidden"
                          disabled={submitting}
                          onChange={handleSourceVideoInput}
                          ref={sourceVideoInputRef}
                          type="file"
                        />
                        <button
                          className="rounded-full border border-white/15 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/40 hover:bg-hire-300/10 hover:text-hire-100"
                          disabled={submitting}
                          onClick={() => sourceVideoInputRef.current?.click()}
                          type="button"
                        >
                          {sourceVideo ? "替换源视频" : "选择源视频"}
                        </button>
                        {sourceVideo ? (
                          <span className="min-w-0 truncate text-xs text-slate-300">
                            {sourceVideo.name} · {formatBytes(sourceVideo.size)}
                            {sourceVideoMetadata
                              ? ` · ${sourceVideoMetadata.width}×${sourceVideoMetadata.height} · ${sourceVideoMetadata.durationSeconds.toFixed(1)} 秒`
                              : " · 元数据不可用"}
                          </span>
                        ) : null}
                      </div>
                    ) : (
                      <label className="block text-sm font-semibold text-slate-200">
                        HTTPS 视频直链
                        <input
                          className="mt-2 w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-2.5 text-sm text-white outline-none placeholder:text-slate-400 focus:border-brand-300/60"
                          disabled={submitting}
                          onChange={(event) => {
                            setSourceVideoUrl(event.target.value);
                            markFormChanged();
                          }}
                          placeholder="https://example.com/source.mp4"
                          type="url"
                          value={sourceVideoUrl}
                        />
                      </label>
                    )}
                    <div className="grid gap-4 sm:grid-cols-2">
                      <label className="block text-sm font-semibold text-slate-200">
                        放大倍数
                        <input
                          className="mt-2 w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-2.5 text-sm text-white outline-none focus:border-brand-300/60"
                          disabled={submitting}
                          max={factorRange?.max}
                          min={factorRange?.min}
                          onChange={(event) => {
                            setUpscaleFactor(Number(event.target.value));
                            markFormChanged();
                          }}
                          step="0.1"
                          type="number"
                          value={upscaleFactor}
                        />
                        <span className="mt-1 block text-xs font-normal text-slate-400">
                          支持 {factorRange?.min ?? "—"}–{factorRange?.max ?? "—"} 倍
                        </span>
                      </label>
                      <label className="block text-sm font-semibold text-slate-200">
                        增强模式
                        <select
                          className="mt-2 w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-2.5 text-sm text-white outline-none focus:border-brand-300/60"
                          disabled={submitting}
                          onChange={(event) => {
                            setCreativity(Number(event.target.value));
                            markFormChanged();
                          }}
                          value={creativity}
                        >
                          {profile.creativity.map((value) => (
                            <option key={value} value={value}>
                              {value === 1 ? "创意增强" : "精确保真"}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  </div>
                ) : (
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
                        const nextResolution = event.target.value;
                        setResolution(nextResolution);
                        const nextAspectRatios =
                          supportedAspectRatiosForResolution(
                            profile,
                            nextResolution,
                          );
                        if (!nextAspectRatios.includes(aspectRatio)) {
                          setAspectRatio(
                            nextAspectRatios.includes("16:9")
                              ? "16:9"
                              : (nextAspectRatios[0] ?? ""),
                          );
                        }
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
                        selectableAspectRatios.length === 0
                      }
                      onChange={(event) => {
                        setAspectRatio(event.target.value);
                        markFormChanged();
                      }}
                      value={aspectRatio}
                    >
                      {selectableAspectRatios.length === 0 ? (
                        <option value="">由模型决定</option>
                      ) : (
                        selectableAspectRatios.map((value) => (
                          <option key={value} value={value}>
                            {value}
                          </option>
                        ))
                      )}
                    </select>
                  </label>
                </div>
                )}

                {supportsFirstFrame ||
                supportsLastFrame ||
                (profile.supports_reference_images && referenceLimit > 0) ? (
                  <div className="space-y-4">
                    <div>
                      <h3 className="text-sm font-semibold text-slate-200">
                        画面引导（可选）
                      </h3>
                      <p className="mt-1 text-xs leading-5 text-slate-400">
                        只显示当前模型已确认支持的图片用途。单张最大 10 MiB，支持 JPEG、PNG、WebP。
                      </p>
                    </div>

                    <div className="grid gap-4 md:grid-cols-2">
                      {supportsFirstFrame ? (
                        <div className="rounded-lg bg-white/[0.045] p-4">
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                              <p className="text-sm font-semibold text-white">
                                首帧
                              </p>
                              <p className="mt-1 text-xs leading-5 text-slate-400">
                                决定视频开始时的画面。
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
                              className="rounded-full border border-white/15 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-hire-300/40 hover:bg-hire-300/10 hover:text-hire-100 disabled:cursor-not-allowed disabled:opacity-50"
                              disabled={submitting}
                              onClick={() => firstFrameInputRef.current?.click()}
                              type="button"
                            >
                              {firstFrame ? "替换首帧" : "选择首帧"}
                            </button>
                          </div>
                          {firstFrame ? (
                            <div className="mt-3 flex min-w-0 items-center gap-3">
                              {firstFrameUrl ? (
                                <img
                                  alt="首帧预览"
                                  className="h-20 w-28 shrink-0 rounded-md bg-black object-contain"
                                  src={firstFrameUrl}
                                />
                              ) : null}
                              <div className="min-w-0 flex-1">
                                <p className="truncate text-xs font-medium text-slate-200">
                                  {firstFrame.name}
                                </p>
                                <p className="mt-1 text-xs text-slate-400">
                                  {formatBytes(firstFrame.size)}
                                </p>
                                <button
                                  className="mt-2 text-xs font-semibold text-rose-200 hover:text-rose-100"
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

                      {supportsLastFrame ? (
                        <div className="rounded-lg bg-white/[0.045] p-4">
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                              <p className="text-sm font-semibold text-white">
                                尾帧
                              </p>
                              <p className="mt-1 text-xs leading-5 text-slate-400">
                                约束视频结束时的画面。
                              </p>
                            </div>
                            <input
                              accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"
                              className="hidden"
                              disabled={submitting}
                              onChange={handleLastFrameInput}
                              ref={lastFrameInputRef}
                              type="file"
                            />
                            <button
                              className="rounded-full border border-white/15 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-hire-300/40 hover:bg-hire-300/10 hover:text-hire-100 disabled:cursor-not-allowed disabled:opacity-50"
                              disabled={submitting}
                              onClick={() => lastFrameInputRef.current?.click()}
                              type="button"
                            >
                              {lastFrame ? "替换尾帧" : "选择尾帧"}
                            </button>
                          </div>
                          {lastFrame ? (
                            <div className="mt-3 flex min-w-0 items-center gap-3">
                              {lastFrameUrl ? (
                                <img
                                  alt="尾帧预览"
                                  className="h-20 w-28 shrink-0 rounded-md bg-black object-contain"
                                  src={lastFrameUrl}
                                />
                              ) : null}
                              <div className="min-w-0 flex-1">
                                <p className="truncate text-xs font-medium text-slate-200">
                                  {lastFrame.name}
                                </p>
                                <p className="mt-1 text-xs text-slate-400">
                                  {formatBytes(lastFrame.size)}
                                </p>
                                <button
                                  className="mt-2 text-xs font-semibold text-rose-200 hover:text-rose-100"
                                  disabled={submitting}
                                  onClick={() => {
                                    setLastFrame(null);
                                    markFormChanged();
                                  }}
                                  type="button"
                                >
                                  移除尾帧
                                </button>
                              </div>
                            </div>
                          ) : null}
                        </div>
                      ) : null}
                    </div>

                    {profile.supports_reference_images &&
                    referenceLimit > 0 ? (
                      <div className="rounded-lg bg-white/[0.045] p-4">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <p className="text-sm font-semibold text-white">
                              风格／角色参考图
                            </p>
                            <p className="mt-1 text-xs leading-5 text-slate-400">
                              用于保持人物、物体或风格一致，可排序，最多 {referenceLimit} 张。
                            </p>
                          </div>
                          <input
                            accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"
                            className="hidden"
                            disabled={submitting}
                            onChange={handleReferenceInput}
                            ref={referenceInputRef}
                            type="file"
                          />
                          <button
                            className="rounded-full border border-white/15 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-hire-300/40 hover:bg-hire-300/10 hover:text-hire-100 disabled:cursor-not-allowed disabled:opacity-50"
                            disabled={
                              submitting ||
                              referenceImages.length >= referenceLimit
                            }
                            onClick={() => {
                              referenceReplaceIndexRef.current = null;
                              referenceInputRef.current?.click();
                            }}
                            type="button"
                          >
                            添加参考图 {referenceImages.length}/{referenceLimit}
                          </button>
                        </div>
                        {referenceImages.length > 0 ? (
                          <ol className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                            {referenceImages.map((item, index) => (
                              <li
                                className="min-w-0 rounded-lg bg-ink-950/65 p-3"
                                key={item.id}
                              >
                                <img
                                  alt={`参考图 ${index + 1}`}
                                  className="aspect-video w-full rounded-md bg-black object-contain"
                                  src={item.previewUrl}
                                />
                                <div className="mt-2 flex items-center justify-between gap-2">
                                  <span className="truncate text-xs text-slate-300">
                                    {index + 1}/{referenceLimit} ·{" "}
                                    {formatBytes(item.file.size)}
                                  </span>
                                  <div className="flex shrink-0 items-center gap-1">
                                    <button
                                      aria-label={`将参考图 ${index + 1} 前移`}
                                      className="rounded-md px-2 py-1 text-xs text-slate-300 hover:bg-white/[0.08] disabled:opacity-30"
                                      disabled={submitting || index === 0}
                                      onClick={() =>
                                        moveReferenceImage(index, -1)
                                      }
                                      type="button"
                                    >
                                      ←
                                    </button>
                                    <button
                                      aria-label={`将参考图 ${index + 1} 后移`}
                                      className="rounded-md px-2 py-1 text-xs text-slate-300 hover:bg-white/[0.08] disabled:opacity-30"
                                      disabled={
                                        submitting ||
                                        index === referenceImages.length - 1
                                      }
                                      onClick={() =>
                                        moveReferenceImage(index, 1)
                                      }
                                      type="button"
                                    >
                                      →
                                    </button>
                                  </div>
                                </div>
                                <div className="mt-2 flex gap-3 text-xs font-semibold">
                                  <button
                                    className="text-brand-200 hover:text-brand-100"
                                    disabled={submitting}
                                    onClick={() => {
                                      referenceReplaceIndexRef.current = index;
                                      referenceInputRef.current?.click();
                                    }}
                                    type="button"
                                  >
                                    替换
                                  </button>
                                  <button
                                    className="text-rose-200 hover:text-rose-100"
                                    disabled={submitting}
                                    onClick={() => removeReferenceImage(index)}
                                    type="button"
                                  >
                                    移除
                                  </button>
                                </div>
                              </li>
                            ))}
                          </ol>
                        ) : null}
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

                {profile.provider_options.length > 0 ? (
                  <details
                    className="rounded-lg bg-white/[0.04]"
                    onToggle={(event) =>
                      setAdvancedOpen(event.currentTarget.open)
                    }
                    open={advancedOpen}
                  >
                    <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 text-sm font-semibold text-slate-200 marker:hidden">
                      <span>
                        高级设置
                        {providerOptionCount > 0 ? (
                          <span className="ml-2 rounded-full bg-brand-300/15 px-2 py-0.5 text-xs text-brand-100">
                            已启用 {providerOptionCount} 项
                          </span>
                        ) : null}
                      </span>
                      <span aria-hidden="true" className="text-slate-400">
                        {advancedOpen ? "收起" : "展开"}
                      </span>
                    </summary>
                    <div className="space-y-4 border-t border-white/10 px-4 py-4">
                      <p className="text-xs leading-5 text-slate-400">
                        这里只显示实时目录和本地安全定义共同确认的参数。未修改的选项不会发送给供应商。
                      </p>
                      {profile.provider_options.map((option) => {
                        const value =
                          providerOptionValues[option.key] ?? "";
                        const enabled = providerOptionKeys.has(option.key);
                        if (option.type === "boolean") {
                          return (
                            <label
                              className="block text-sm font-medium text-slate-200"
                              key={option.key}
                            >
                              {option.label}
                              <select
                                className="mt-2 w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-2.5 text-sm text-white outline-none transition focus:border-brand-300/60 focus:ring-4 focus:ring-brand-300/10"
                                disabled={submitting}
                                onChange={(event) => {
                                  if (!event.target.value) {
                                    updateProviderOption(
                                      option.key,
                                      "",
                                      false,
                                    );
                                    return;
                                  }
                                  updateProviderOption(
                                    option.key,
                                    event.target.value === "true",
                                  );
                                }}
                                value={enabled ? String(Boolean(value)) : ""}
                              >
                                <option value="">
                                  使用模型默认值
                                  {option.default === true
                                    ? "（开启）"
                                    : option.default === false
                                      ? "（关闭）"
                                      : ""}
                                </option>
                                <option value="true">开启</option>
                                <option value="false">关闭</option>
                              </select>
                            </label>
                          );
                        }
                        if (option.type === "select") {
                          return (
                            <label
                              className="block text-sm font-medium text-slate-200"
                              key={option.key}
                            >
                              {option.label}
                              <select
                                className="mt-2 w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-2.5 text-sm text-white outline-none transition focus:border-brand-300/60 focus:ring-4 focus:ring-brand-300/10"
                                disabled={submitting}
                                onChange={(event) =>
                                  event.target.value
                                    ? updateProviderOption(
                                        option.key,
                                        event.target.value,
                                      )
                                    : updateProviderOption(
                                        option.key,
                                        "",
                                        false,
                                      )
                                }
                                value={enabled ? String(value) : ""}
                              >
                                <option value="">使用模型默认值</option>
                                {option.options.map((item) => (
                                  <option key={item} value={item}>
                                    {item}
                                  </option>
                                ))}
                              </select>
                            </label>
                          );
                        }
                        return (
                          <label
                            className="block text-sm font-medium text-slate-200"
                            key={option.key}
                          >
                            {option.label}
                            <input
                              className="mt-2 w-full rounded-lg border border-white/15 bg-ink-950 px-3 py-2.5 text-sm text-white outline-none transition placeholder:text-slate-400 focus:border-brand-300/60 focus:ring-4 focus:ring-brand-300/10"
                              disabled={submitting}
                              max={option.max ?? undefined}
                              maxLength={
                                option.type === "text" ? 2_000 : undefined
                              }
                              min={option.min ?? undefined}
                              onChange={(event) => {
                                const nextValue =
                                  option.type === "number"
                                    ? Number(event.target.value)
                                    : event.target.value;
                                updateProviderOption(
                                  option.key,
                                  nextValue,
                                  event.target.value.length > 0,
                                );
                              }}
                              placeholder={
                                option.type === "text"
                                  ? option.default
                                    ? `可选，模型默认：${String(option.default)}`
                                    : "可选，留空则不发送"
                                  : option.default !== null
                                    ? `模型默认：${String(option.default)}`
                                    : "输入数值"
                              }
                              type={
                                option.type === "number" ? "number" : "text"
                              }
                              value={String(value)}
                            />
                          </label>
                        );
                      })}
                      {providerOptionCount > 0 ? (
                        <button
                          className="text-xs font-semibold text-slate-300 hover:text-white"
                          disabled={submitting}
                          onClick={() => {
                            setProviderOptionKeys(new Set());
                            setProviderOptionValues(
                              Object.fromEntries(
                                profile.provider_options.map((option) => [
                                  option.key,
                                  "",
                                ]),
                              ),
                            );
                            markFormChanged();
                          }}
                          type="button"
                        >
                          恢复默认设置
                        </button>
                      ) : null}
                    </div>
                  </details>
                ) : null}

                {capabilityRefreshRequired ? (
                  <div
                    className="flex flex-col gap-3 rounded-lg border border-amber-300/25 bg-amber-300/[0.08] px-4 py-3 text-sm leading-6 text-amber-100 sm:flex-row sm:items-center sm:justify-between"
                    role="status"
                  >
                    <span>
                      尾帧、参考图和高级设置需要最新能力目录确认，请刷新后再提交。
                    </span>
                    <button
                      className="shrink-0 rounded-full border border-amber-200/30 px-3 py-1.5 text-xs font-semibold hover:bg-amber-200/10 disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={catalogRefreshing}
                      onClick={() => void refreshCapabilities()}
                      type="button"
                    >
                      {catalogRefreshing ? "正在刷新…" : "刷新模型能力"}
                    </button>
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
                      {estimate === null
                        ? isUpscaler && upscaleUnitRate !== null
                          ? `单价 $${upscaleUnitRate.toFixed(3)}/百万像素秒`
                          : "费用以网关结算为准"
                        : `目录估算 $${estimate.toFixed(4)}`}
                    </p>
                    <p className="mt-1 text-xs text-slate-400">
                      {isUpscaler
                        ? "本地文件可按输出尺寸与时长预估；网址来源或无法读取元数据时仅显示单价。最终金额以网关回执为准。"
                        : "提交会产生费用，最终金额以网关回执为准。"}
                    </p>
                    {profile.verification_entry_enabled ? (
                      <label className="mt-3 flex max-w-xl items-start gap-2 text-xs leading-5 text-amber-100">
                        <input
                          checked={manualVerificationConfirmed}
                          className="mt-0.5 h-4 w-4 rounded border-white/20 bg-ink-950 text-hire-300 focus:ring-hire-200"
                          onChange={(event) =>
                            setManualVerificationConfirmed(event.target.checked)
                          }
                          type="checkbox"
                        />
                        <span>
                          我已确认这是未完成行为验证的最低规格测试，并了解会产生实际费用。
                        </span>
                      </label>
                    ) : null}
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
                        ? `${isUpscaler ? "提交增强" : "提交生成"} · 费用以网关结算为准`
                        : `${isUpscaler ? "提交增强" : "提交生成"} · 预计 $${estimate.toFixed(4)}`}
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
                    {isUpscaler
                      ? "源视频增强"
                      : generationModeLabel({
                          hasFirstFrame: Boolean(firstFrame),
                          hasLastFrame: Boolean(lastFrame),
                          referenceImageCount: referenceImages.length,
                        })}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-400">当前设置</dt>
                  <dd className="mt-1 leading-6 text-slate-300">
                    {isUpscaler
                      ? `${upscaleFactor} 倍 · ${creativity === 1 ? "创意增强" : "精确保真"}`
                      : [duration ? `${duration} 秒` : "", resolution, aspectRatio]
                          .filter(Boolean)
                          .join(" · ") || "等待模型能力"}
                  </dd>
                </div>
                {lastFrame ||
                referenceImages.length > 0 ||
                providerOptionCount > 0 ? (
                  <div>
                    <dt className="text-slate-400">增强设置</dt>
                    <dd className="mt-1 leading-6 text-slate-300">
                      {[
                        lastFrame ? "使用尾帧" : "",
                        referenceImages.length
                          ? `${referenceImages.length} 张参考图`
                          : "",
                        providerOptionCount
                          ? `${providerOptionCount} 项高级设置`
                          : "",
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </dd>
                  </div>
                ) : null}
              </dl>
            </div>
            <div className="rounded-lg border border-amber-300/20 bg-amber-300/[0.07] p-5">
              <h2 className="text-sm font-semibold text-amber-100">
                内容与隐私提示
              </h2>
              <p className="mt-3 text-sm leading-6 text-slate-300">
                视频任务不支持零数据保留。提示词、源视频、引导帧、参考图和高级参数值会交给模型供应商处理，供应商可能按自身政策临时保留内容。模镜只保存任务元数据、媒体数量和参数名称，不保存这些内容或视频正文。
              </p>
            </div>
          </aside>
        </div>

        <section className="surface-panel mt-6 overflow-hidden rounded-lg">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-5 py-4 sm:px-6">
            <div>
              <h2 className="text-lg font-semibold text-white">
                {isUpscaler ? "增强任务" : "生成任务"}
              </h2>
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
              <p className="font-semibold text-white">
                还没有{isUpscaler ? "增强" : "生成"}任务
              </p>
              <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-slate-400">
                {isUpscaler
                  ? "提供源视频并提交后，排队、增强和完成状态都会显示在这里。"
                  : "填写视频描述并提交后，排队、生成和完成状态都会显示在这里。"}
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
                            {job.parameters.task_type === "upscale"
                              ? `${job.parameters.upscale_factor ?? "—"} 倍 · ${job.parameters.creativity === 1 ? "创意增强" : "精确保真"}`
                              : [
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
                          {job.parameters.has_last_frame ? (
                            <span>使用尾帧</span>
                          ) : null}
                          {job.parameters.reference_image_count > 0 ? (
                            <span>
                              {job.parameters.reference_image_count} 张参考图
                            </span>
                          ) : null}
                          {job.parameters.has_source_video ? (
                            <span>使用源视频</span>
                          ) : null}
                          {job.parameters.provider_option_keys.length > 0 ? (
                            <span>
                              {job.parameters.provider_option_keys.length}{" "}
                              项高级设置
                            </span>
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
