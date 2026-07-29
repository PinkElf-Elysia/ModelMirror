import { useEffect, useMemo, useRef, useState } from "react";
import { models } from "../data/models";

const MAX_VIDEO_BYTES = 20 * 1024 * 1024;
const VIDEO_MODEL_STORAGE_KEY = "modelmirror-chat-video-helper-model";
const VIDEO_ACCEPT =
  "video/mp4,video/mpeg,video/quicktime,video/webm,.mp4,.mpeg,.mpg,.mov,.webm";

interface VideoModelProfile {
  model_id: string;
  operation: "analyze_video" | "generate_video";
  supported_input_sources: Array<"file" | "url">;
  interaction_status: "ready" | "planned" | "unsupported";
}

interface VideoModelCatalog {
  status: "online" | "stale" | "offline" | "disabled";
  stale: boolean;
  profiles: VideoModelProfile[];
}

export interface ChatVideoSelection {
  file: File;
  fileName: string;
  mode: "direct" | "assist";
  helperModelId: string;
}

export interface ChatAttachmentUpload {
  attachment_id: string;
  kind: "video";
  mime_type: string;
  format: string;
  bytes: number;
  expires_at: string;
}

export interface ChatVideoAnalysisResult {
  text: string;
  requested_model: string;
  actual_model: string;
  provider: string;
  request_id: string;
}

function apiErrorMessage(payload: unknown, fallback: string) {
  if (!payload || typeof payload !== "object") return fallback;
  const record = payload as Record<string, unknown>;
  if (typeof record.error === "string" && record.error.trim()) {
    return record.error;
  }
  if (record.detail && typeof record.detail === "object") {
    const detail = record.detail as Record<string, unknown>;
    if (typeof detail.message === "string" && detail.message.trim()) {
      return detail.message;
    }
  }
  return fallback;
}

async function responsePayload(response: Response) {
  try {
    return (await response.json()) as unknown;
  } catch {
    return null;
  }
}

export async function uploadChatVideoAttachment(
  file: File,
  signal?: AbortSignal,
) {
  const form = new FormData();
  form.append("kind", "video");
  form.append("file", file, file.name);
  const response = await fetch("/api/multimodal/chat/attachments", {
    method: "POST",
    body: form,
    signal,
  });
  const payload = await responsePayload(response);
  if (!response.ok) {
    throw new Error(
      apiErrorMessage(payload, "视频上传没有完成，请检查文件后重试。"),
    );
  }
  return payload as ChatAttachmentUpload;
}

export async function deleteChatVideoAttachment(attachmentId: string) {
  if (!attachmentId) return;
  await fetch(
    `/api/multimodal/chat/attachments/${encodeURIComponent(attachmentId)}`,
    { method: "DELETE" },
  ).catch(() => undefined);
}

export async function analyzeChatVideo(
  file: File,
  modelId: string,
  prompt: string,
  signal?: AbortSignal,
) {
  const form = new FormData();
  form.append("model_id", modelId);
  form.append("prompt", prompt);
  form.append("source_type", "file");
  form.append("file", file, file.name);
  const response = await fetch("/api/multimodal/video/analysis", {
    method: "POST",
    body: form,
    signal,
  });
  const payload = await responsePayload(response);
  if (!response.ok) {
    throw new Error(
      apiErrorMessage(payload, "视频理解没有完成，请稍后重试。"),
    );
  }
  return payload as ChatVideoAnalysisResult;
}

function displayModelName(modelId: string) {
  return models.find((item) => item.id === modelId)?.name ?? modelId;
}

function formatBytes(bytes: number) {
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  }
  return `${Math.max(1, Math.round(bytes / 1024))} KiB`;
}

export default function ChatVideoComposer({
  currentModelId,
  disabled,
  isAutoRoute,
  onClose,
  onError,
  onSelectionChange,
  resetVersion,
}: {
  currentModelId: string;
  disabled: boolean;
  isAutoRoute: boolean;
  onClose: () => void;
  onError: (message: string) => void;
  onSelectionChange: (selection: ChatVideoSelection | null) => void;
  resetVersion: number;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const previewUrlRef = useRef("");
  const [catalog, setCatalog] = useState<VideoModelCatalog | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [mode, setMode] = useState<"direct" | "assist">("assist");
  const [helperModelId, setHelperModelId] = useState(
    () => window.sessionStorage.getItem(VIDEO_MODEL_STORAGE_KEY) ?? "",
  );

  const helperProfiles = useMemo(
    () =>
      (catalog?.profiles ?? []).filter(
        (profile) =>
          profile.operation === "analyze_video" &&
          profile.interaction_status === "ready" &&
          profile.supported_input_sources.includes("file"),
      ),
    [catalog],
  );
  const directAvailable =
    !isAutoRoute &&
    helperProfiles.some((profile) => profile.model_id === currentModelId);

  function releasePreview() {
    if (!previewUrlRef.current) return;
    URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = "";
  }

  function clearFile() {
    releasePreview();
    setFile(null);
    setPreviewUrl("");
    onSelectionChange(null);
    if (inputRef.current) inputRef.current.value = "";
  }

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/multimodal/video/models", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) return null;
        return (await response.json()) as VideoModelCatalog;
      })
      .then((nextCatalog) => {
        if (nextCatalog) setCatalog(nextCatalog);
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (directAvailable) {
      setMode("direct");
    } else {
      setMode("assist");
    }
  }, [currentModelId, directAvailable, isAutoRoute]);

  useEffect(() => {
    if (helperProfiles.length === 0) {
      setHelperModelId("");
      return;
    }
    setHelperModelId((current) => {
      const next = helperProfiles.some(
        (profile) => profile.model_id === current,
      )
        ? current
        : helperProfiles[0].model_id;
      window.sessionStorage.setItem(VIDEO_MODEL_STORAGE_KEY, next);
      return next;
    });
  }, [helperProfiles]);

  useEffect(() => {
    if (!file) {
      onSelectionChange(null);
      return;
    }
    onSelectionChange({
      file,
      fileName: file.name,
      mode,
      helperModelId: mode === "assist" ? helperModelId : "",
    });
  }, [file, helperModelId, mode, onSelectionChange]);

  useEffect(() => {
    clearFile();
  }, [resetVersion]);

  useEffect(
    () => () => {
      releasePreview();
      onSelectionChange(null);
    },
    [],
  );

  function selectFile(nextFile: File | null) {
    if (!nextFile) return;
    const extension = nextFile.name.split(".").pop()?.toLowerCase() ?? "";
    if (!["mp4", "mpeg", "mpg", "mov", "webm"].includes(extension)) {
      onError("仅支持 MP4、MPEG、MOV 和 WebM 视频。");
      return;
    }
    if (nextFile.size > MAX_VIDEO_BYTES) {
      onError("视频不能超过 20 MiB，请压缩或截短后重试。");
      return;
    }
    releasePreview();
    const nextUrl = URL.createObjectURL(nextFile);
    previewUrlRef.current = nextUrl;
    setPreviewUrl(nextUrl);
    setFile(nextFile);
    onError("");
  }

  const catalogHint =
    catalog?.status === "offline"
      ? "暂时无法读取视频模型，请检查 OpenRouter 连接。"
      : catalog?.status === "disabled"
        ? "视频能力当前未启用。"
        : catalog?.stale
          ? "正在使用最近一次可用的视频模型列表。"
          : "";

  return (
    <section className="border-b border-white/10 px-3 py-3" aria-label="视频附件">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-white">视频附件</p>
          <p className="mt-1 text-xs leading-5 text-slate-400">
            本地视频仅用于本轮，完成后立即清理。
          </p>
        </div>
        <button
          className="rounded-md px-2 py-1 text-xs font-semibold text-slate-400 transition hover:bg-white/[0.06] hover:text-white"
          disabled={disabled}
          onClick={onClose}
          type="button"
        >
          关闭
        </button>
      </div>

      <input
        accept={VIDEO_ACCEPT}
        className="hidden"
        onChange={(event) => {
          selectFile(event.target.files?.[0] ?? null);
          event.target.value = "";
        }}
        ref={inputRef}
        type="file"
      />

      {file && previewUrl ? (
        <div className="mt-3 flex flex-col gap-3 rounded-md border border-white/10 bg-black/20 p-3 sm:flex-row sm:items-center">
          <video
            className="h-24 w-full rounded-md bg-black object-contain sm:w-40"
            controls
            preload="metadata"
            src={previewUrl}
          />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-semibold text-slate-100">
              {file.name}
            </p>
            <p className="mt-1 text-xs text-slate-400">
              {formatBytes(file.size)} · 最多 20 MiB
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                className="rounded-md border border-white/10 px-2.5 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-cyan-300/35 hover:text-cyan-100"
                disabled={disabled}
                onClick={() => inputRef.current?.click()}
                type="button"
              >
                替换
              </button>
              <button
                className="rounded-md border border-rose-300/20 px-2.5 py-1.5 text-xs font-semibold text-rose-100 transition hover:border-rose-300/45"
                disabled={disabled}
                onClick={clearFile}
                type="button"
              >
                移除
              </button>
            </div>
          </div>
        </div>
      ) : (
        <button
          className="mt-3 w-full rounded-md border border-dashed border-white/15 bg-white/[0.035] px-4 py-4 text-left transition hover:border-cyan-300/35 hover:bg-cyan-300/[0.05]"
          disabled={disabled || catalog?.status === "disabled"}
          onClick={() => inputRef.current?.click()}
          type="button"
        >
          <span className="block text-sm font-semibold text-slate-100">
            选择本地视频
          </span>
          <span className="mt-1 block text-xs text-slate-400">
            MP4、MPEG、MOV、WebM，最大 20 MiB
          </span>
        </button>
      )}

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="text-xs font-semibold text-slate-300">
          处理方式
          <select
            className="mt-1.5 w-full rounded-md border border-white/10 bg-ink-950/80 px-3 py-2 text-sm text-white outline-none focus:border-cyan-300/45"
            disabled={disabled}
            onChange={(event) =>
              setMode(event.target.value as "direct" | "assist")
            }
            value={mode}
          >
            {directAvailable ? (
              <option value="direct">由当前模型直接理解</option>
            ) : null}
            <option value="assist">先生成视频理解摘要</option>
          </select>
        </label>
        {mode === "assist" ? (
          <label className="text-xs font-semibold text-slate-300">
            视频理解模型
            <select
              className="mt-1.5 w-full rounded-md border border-white/10 bg-ink-950/80 px-3 py-2 text-sm text-white outline-none focus:border-cyan-300/45"
              disabled={disabled || helperProfiles.length === 0}
              onChange={(event) => {
                setHelperModelId(event.target.value);
                window.sessionStorage.setItem(
                  VIDEO_MODEL_STORAGE_KEY,
                  event.target.value,
                );
              }}
              value={helperModelId}
            >
              {helperProfiles.map((profile) => (
                <option key={profile.model_id} value={profile.model_id}>
                  {displayModelName(profile.model_id)}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <div className="self-end rounded-md bg-cyan-300/[0.06] px-3 py-2 text-xs leading-5 text-cyan-100">
            当前模型已通过视频输入能力校验。
          </div>
        )}
      </div>

      {catalogHint ? (
        <p className="mt-2 text-xs leading-5 text-amber-100">{catalogHint}</p>
      ) : mode === "assist" ? (
        <p className="mt-2 text-xs leading-5 text-slate-400">
          摘要会作为普通参考资料交给当前模型；知识库、Skill 和工具可继续组合。
        </p>
      ) : (
        <p className="mt-2 text-xs leading-5 text-slate-400">
          直接理解不与知识库、Skill 或 MCP 工具同时使用。
        </p>
      )}
    </section>
  );
}
