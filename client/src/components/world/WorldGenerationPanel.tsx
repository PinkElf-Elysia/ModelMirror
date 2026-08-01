import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FourViewer, type AssetFormat, type AssetKind } from "./FourViewer";

interface JobRecord {
  job_id: string;
  status: string;
  assets: AssetItem[];
  world_id?: string;
  caption?: string;
  error?: string;
  provider?: string;
}

interface AssetItem {
  id: string;
  kind: AssetKind;
  format: AssetFormat;
  url: string;
  size_bytes?: number | null;
}

type InputMode = "image" | "multi_image" | "video";

const MAX_FILES = 8;
const MAX_FILE_BYTES = 50 * 1024 * 1024;
const SUPPORTED_IMAGE = ["image/jpeg", "image/png", "image/webp"];
const SUPPORTED_VIDEO = ["video/mp4", "video/quicktime", "video/webm", "video/x-matroska", "video/avi"];

function friendlyError(message: string): string {
  if (message.includes("WORLD_LABS_API_KEY")) {
    return "后端尚未配置世界模型 API Key，请联系管理员配置后重试。";
  }
  if (message.includes("402")) return "世界模型额度不足，请先充值 Credits。";
  if (message.includes("429")) return "请求过于频繁，请稍后再试。";
  if (message.includes("401")) return "世界模型认证失败，请检查 API Key。";
  return message;
}

function formatBytes(bytes?: number | null): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const statusLabels: Record<string, string> = {
  created: "已创建",
  uploading: "上传中",
  submitted: "已提交",
  processing: "生成中",
  succeeded: "已完成",
  failed: "生成失败",
  cancelled: "已取消",
  expired: "已过期",
};

/**
 * World-generation panel shown inside ChatPage when the selected model
 * is a world model. Handles upload → create job → poll → show 3D assets,
 * plus a local-file 3D preview that never calls the API.
 */
export function WorldGenerationPanel() {
  const [mode, setMode] = useState<InputMode>("image");
  const [files, setFiles] = useState<File[]>([]);
  const [prompt, setPrompt] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [isExportingPly, setIsExportingPly] = useState(false);
  const [job, setJob] = useState<JobRecord | null>(null);
  const [error, setError] = useState("");
  const [viewerAsset, setViewerAsset] = useState<AssetItem | null>(null);
  const [localFile, setLocalFile] = useState<File | null>(null);
  const [localViewer, setLocalViewer] = useState<{
    url: string;
    format: AssetFormat;
    kind: AssetKind;
  } | null>(null);

  const pollTimerRef = useRef<number | null>(null);

  const isImageMode = mode === "image" || mode === "multi_image";
  const maxFiles = mode === "multi_image" ? MAX_FILES : 1;

  useEffect(() => {
    return () => {
      if (pollTimerRef.current !== null) window.clearTimeout(pollTimerRef.current);
      if (localViewer?.url.startsWith("blob:")) URL.revokeObjectURL(localViewer.url);
    };
  }, [localViewer]);

  // ------------------------------------------------------------------
  // File selection / validation
  // ------------------------------------------------------------------
  const validateFile = useCallback((file: File): string | null => {
    const allowed = isImageMode ? SUPPORTED_IMAGE : SUPPORTED_VIDEO;
    if (!allowed.includes(file.type)) {
      return `不支持的文件类型：${file.name}`;
    }
    if (file.size > MAX_FILE_BYTES) {
      return `文件过大：${file.name}（上限 50MB）`;
    }
    return null;
  }, [isImageMode]);

  const onFilesSelected = useCallback(
    (selected: FileList | File[]) => {
      const list = Array.from(selected);
      const next = [...files];
      for (const file of list) {
        if (next.length >= maxFiles) break;
        const problem = validateFile(file);
        if (problem) {
          setError(problem);
          continue;
        }
        next.push(file);
      }
      setFiles(next);
    },
    [files, maxFiles, validateFile],
  );

  const removeFile = useCallback(
    (index: number) => {
      setFiles((prev) => prev.filter((_, i) => i !== index));
    },
    [],
  );

  // ------------------------------------------------------------------
  // Create job
  // ------------------------------------------------------------------
  const createJob = useCallback(async () => {
    if (files.length === 0) {
      setError("请先选择素材文件。");
      return;
    }
    setError("");
    setIsCreating(true);
    setJob(null);
    setViewerAsset(null);

    try {
      const form = new FormData();
      for (const file of files) form.append("files", file);
      const params = new URLSearchParams({ input_type: mode });
      if (prompt.trim()) params.set("prompt", prompt.trim());
      const response = await fetch(`/api/world-generations?${params.toString()}`, {
        method: "POST",
        body: form,
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(body.detail || "创建生成任务失败。");
      }
      setJob({
        job_id: body.job_id,
        status: body.status,
        assets: [],
        provider: body.provider,
      });
      setFiles([]);
    } catch (err) {
      setError(friendlyError(err instanceof Error ? err.message : String(err)));
    } finally {
      setIsCreating(false);
    }
  }, [files, mode, prompt]);

  // ------------------------------------------------------------------
  // Poll job status
  // ------------------------------------------------------------------
  const pollJob = useCallback(async () => {
    if (!job) return;
    try {
      const response = await fetch(`/api/world-generations/${job.job_id}`);
      const body = await response.json().catch(() => ({}));
      if (response.ok) {
        setJob((prev) => ({
          job_id: body.job_id ?? prev?.job_id ?? "",
          status: body.status ?? prev?.status ?? "processing",
          assets: body.assets ?? prev?.assets ?? [],
          world_id: body.world_id,
          caption: body.caption,
          error: body.error,
          provider: body.provider ?? prev?.provider,
        }));
      }
    } catch {
      // transient network error — keep polling
    }
  }, [job]);

  useEffect(() => {
    if (!job) return;
    const status = job.status;
    if (status === "succeeded" || status === "failed" || status === "expired" || status === "cancelled") {
      return; // done — stop polling
    }
    pollTimerRef.current = window.setTimeout(pollJob, 4000);
    return () => {
      if (pollTimerRef.current !== null) window.clearTimeout(pollTimerRef.current);
    };
  }, [job, pollJob]);

  const exportPly = useCallback(async () => {
    if (!job || job.provider !== "marble") return;
    if (!window.confirm("PLY 导出可能消耗 World Labs Credits，确认继续吗？")) {
      return;
    }

    setError("");
    setIsExportingPly(true);
    try {
      const response = await fetch(
        `/api/world-generations/${job.job_id}/exports/ply`,
        { method: "POST" },
      );
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(body.detail || "PLY 导出失败。");
      }
      if (body.asset) {
        setJob((prev) =>
          prev
            ? {
                ...prev,
                assets: [
                  ...prev.assets.filter((asset) => asset.id !== body.asset.id),
                  body.asset,
                ],
              }
            : prev,
        );
      }
    } catch (err) {
      setError(friendlyError(err instanceof Error ? err.message : String(err)));
    } finally {
      setIsExportingPly(false);
    }
  }, [job]);

  // ------------------------------------------------------------------
  // Local 3D file preview (no API)
  // ------------------------------------------------------------------
  const onLocalFileSelected = useCallback((selected: FileList | null) => {
    const file = selected?.[0];
    if (!file) return;
    setError("");
    setLocalViewer(null);
    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    let format: AssetFormat = "unknown";
    let kind: AssetKind = "other";
    if (ext === "glb" || ext === "gltf") {
      format = ext === "glb" ? "glb" : "gltf";
      kind = "textured_mesh";
    } else if (ext === "spz") {
      format = "spz";
      kind = "gaussian_splat";
    } else if (ext === "ply") {
      // PLY ambiguous — treat as splat by default, allow user note.
      format = "ply";
      kind = "gaussian_splat";
    } else if (ext === "png") {
      format = "png";
      kind = "panorama";
    } else {
      setError(`不支持本地预览的格式：.${ext}（支持 glb/gltf/spz/ply/png）。`);
      setLocalFile(null);
      return;
    }
    const url = URL.createObjectURL(file);
    if (localViewer?.url.startsWith("blob:")) URL.revokeObjectURL(localViewer.url);
    setLocalFile(file);
    setLocalViewer({ url, format, kind });
  }, [localViewer]);

  // ------------------------------------------------------------------
  // Render helpers
  // ------------------------------------------------------------------
  const previewCard = useMemo(() => {
    if (!job || job.status === "succeeded") return null;
    const status = job.status;
    return (
      <div className="rounded-lg border border-white/10 bg-white/[0.04] px-4 py-3 text-sm">
        <div className="flex items-center justify-between">
          <span className="font-semibold text-white">
            {statusLabels[status] ?? status}
          </span>
          {status === "processing" || status === "submitted" || status === "created" || status === "uploading" ? (
            <span className="h-2 w-2 animate-pulse rounded-full bg-amber-300" />
          ) : null}
        </div>
        <p className="mt-1 text-xs text-slate-400">任务 ID：{job.job_id}</p>
        {job.error ? <p className="mt-1 text-xs text-rose-300">{job.error}</p> : null}
      </div>
    );
  }, [job]);

  return (
    <div className="flex h-full min-h-[560px] flex-col gap-4 overflow-y-auto p-5">
      <div>
        <h2 className="text-lg font-semibold text-white">3D 世界生成</h2>
        <p className="mt-1 text-sm leading-6 text-slate-400">
          上传现实场景的图片或视频，World Labs 世界模型将生成可探索的 3D 世界。生成约需数分钟，完成后可在下方预览。
        </p>
      </div>

      {/* 素材输入 */}
      <div className="rounded-lg border border-white/10 bg-white/[0.04] p-4">
        <div className="flex flex-wrap items-center gap-2">
          {(["image", "multi_image", "video"] as InputMode[]).map((m) => (
            <button
              className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                mode === m
                  ? "border border-brand-300/40 bg-brand-300/10 text-brand-100"
                  : "border border-white/10 bg-white/[0.05] text-slate-300 hover:border-white/20"
              }`}
              key={m}
              onClick={() => {
                setMode(m);
                setFiles([]);
                setError("");
              }}
              type="button"
            >
              {m === "image" ? "单张图片" : m === "multi_image" ? "多张图片" : "视频"}
            </button>
          ))}
        </div>

        <label className="mt-4 flex cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed border-white/15 py-8 text-center transition hover:border-brand-300/40 hover:bg-brand-300/5">
          <span className="text-2xl">📷</span>
          <span className="mt-2 text-sm font-semibold text-slate-300">
            点击选择或拖拽 {isImageMode ? "图片" : "视频"}
          </span>
          <span className="mt-1 text-xs text-slate-500">
            {isImageMode ? "支持 JPG/PNG/WebP" : "支持 MP4/MOV/WebM/MKV/AVI"}，上限 50MB
            {mode === "multi_image" ? `，最多 ${MAX_FILES} 张` : ""}
          </span>
          <input
            accept={isImageMode ? SUPPORTED_IMAGE.join(",") : SUPPORTED_VIDEO.join(",")}
            className="hidden"
            multiple={mode === "multi_image"}
            onChange={(e) => onFilesSelected(e.target.files ?? [])}
            type="file"
          />
        </label>

        {files.length > 0 ? (
          <ul className="mt-3 space-y-2">
            {files.map((file, index) => (
              <li
                className="flex items-center justify-between gap-3 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-xs"
                key={`${file.name}-${index}`}
              >
                <span className="truncate text-slate-200">{file.name}</span>
                <span className="shrink-0 text-slate-400">{formatBytes(file.size)}</span>
                <button
                  className="shrink-0 text-slate-400 transition hover:text-rose-300"
                  onClick={() => removeFile(index)}
                  type="button"
                >
                  删除
                </button>
              </li>
            ))}
          </ul>
        ) : null}

        <input
          className="mt-3 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none placeholder:text-slate-500 focus:border-brand-300/40"
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="可选：用文字描述你希望生成的世界（如“阳光充足的客厅”）"
          value={prompt}
        />

        <button
          className="mt-3 w-full rounded-full bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-50"
          disabled={isCreating || files.length === 0}
          onClick={createJob}
          type="button"
        >
          {isCreating ? "创建中..." : files.length === 0 ? "请先选择素材" : "开始生成 3D 世界"}
        </button>
      </div>

      {error ? (
        <div className="rounded-lg border border-rose-300/25 bg-rose-400/10 px-4 py-3 text-sm text-rose-100">
          {error}
        </div>
      ) : null}

      {/* 任务状态 */}
      {previewCard}

      {/* 生成结果 + 3D 查看 */}
      {job && job.status === "succeeded" ? (
        <div className="rounded-lg border border-white/10 bg-white/[0.04] p-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-white">生成结果</h3>
            {job.caption ? <span className="text-xs text-slate-500">已完成</span> : null}
          </div>
          {job.caption ? (
            <p className="mt-2 line-clamp-3 text-xs leading-5 text-slate-400">{job.caption}</p>
          ) : null}
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {job.assets.map((asset) => (
              <button
                className="flex items-center justify-between rounded-lg border border-white/10 bg-white/[0.05] px-3 py-2 text-left text-xs transition hover:border-brand-300/40 hover:bg-brand-300/10"
                key={asset.id}
                onClick={() => setViewerAsset(asset)}
                type="button"
              >
                <span className="font-medium text-slate-200">{asset.kind}</span>
                <span className="text-slate-400">
                  {asset.format.toUpperCase()} {formatBytes(asset.size_bytes)}
                </span>
              </button>
            ))}
          </div>
          {job.provider === "marble" &&
          !job.assets.some((asset) => asset.format === "ply") ? (
            <button
              className="mt-3 w-full rounded-full border border-amber-300/30 bg-amber-300/10 px-4 py-2 text-xs font-semibold text-amber-100 transition hover:bg-amber-300/15 disabled:opacity-50"
              disabled={isExportingPly}
              onClick={exportPly}
              type="button"
            >
              {isExportingPly
                ? "正在导出 PLY…"
                : "导出 PLY（可能消耗 Credits，需再次确认）"}
            </button>
          ) : null}
        </div>
      ) : null}

      {/* 3D 查看器 */}
      {viewerAsset ? (
        <div className="h-[420px]">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold text-slate-300">
              预览：{viewerAsset.format.toUpperCase()} · {viewerAsset.kind}
            </span>
            <button
              className="text-xs text-slate-400 transition hover:text-slate-200"
              onClick={() => setViewerAsset(null)}
              type="button"
            >
              关闭
            </button>
          </div>
          <FourViewer
            format={viewerAsset.format}
            kind={viewerAsset.kind}
            source={viewerAsset.url}
          />
        </div>
      ) : null}

      {/* 本地 3D 文件测试（不调用 API） */}
      <div className="rounded-lg border border-white/10 bg-white/[0.04] p-4">
        <h3 className="text-sm font-semibold text-white">本地 3D 文件预览</h3>
        <p className="mt-1 text-xs text-slate-500">
          不调用世界模型，直接上传 3D 文件验证前端渲染：支持 glb / gltf / spz / ply / png 全景。
        </p>
        <label className="mt-3 flex cursor-pointer items-center justify-center rounded-lg border border-dashed border-white/15 py-6 text-sm text-slate-400 transition hover:border-brand-300/40 hover:bg-brand-300/5">
          点击选择本地 3D 文件
          <input
            accept=".glb,.gltf,.spz,.ply,.png"
            className="hidden"
            onChange={(e) => onLocalFileSelected(e.target.files)}
            type="file"
          />
        </label>
        {localFile ? (
          <p className="mt-2 text-xs text-slate-500">已选择：{localFile.name}</p>
        ) : null}
        {localViewer ? (
          <div className="mt-3 h-[360px]">
            <FourViewer
              format={localViewer.format}
              kind={localViewer.kind}
              source={localViewer.url}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
