import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { createPortal } from "react-dom";
import {
  cancelChatFileAnalysis,
  confirmChatFile,
  confirmChatFileAnalysis,
  createChatFileAnalysis,
  deleteChatFile,
  fetchChatFileAnalysis,
  fetchFileAnalysisTargets,
  fetchFileCapabilities,
  listChatFileAnalyses,
  preflightChatFileAnalysis,
  uploadChatAnalysisFile,
  type FileAnalysisArtifact,
  type FileAnalysisJob,
  type FileAnalysisMode,
  type FileAnalysisTarget,
  type ParsedDocumentPreview,
} from "../data/fileCapabilities";
import { computeChatFileDrawerRect } from "./ChatFileComposer";
import type { ChatVisualAnalysisHandoff } from "./ChatFileComposer";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.webp";

export interface PreparedChatAnalysisFile {
  assetId: string;
  analysisArtifactId: string;
  analysisPrompt: string;
  displayName: string;
  format: string;
  byteSize: number;
  handling: "extract";
  confirmationRevision: number;
  preview: ParsedDocumentPreview;
  mode: FileAnalysisMode;
}

export interface ChatVisualAnalysisState {
  files: PreparedChatAnalysisFile[];
  count: number;
  busy: boolean;
  allConfirmed: boolean;
}

interface ChatVisualAnalysisPanelProps {
  modelId: string;
  scopeId: string;
  disabled: boolean;
  blockedReason?: string;
  drawerHost: HTMLElement | null;
  inputBoundary?: HTMLElement | null;
  knowledgeBases: Array<{ id: string; name: string }>;
  resetVersion: number;
  discardVersion: number;
  hideTrigger?: boolean;
  onError: (message: string) => void;
  onCapabilityChange?: (state: "loading" | "ready" | "disabled") => void;
  onStateChange: (state: ChatVisualAnalysisState) => void;
}

export function parseVisualAnalysisPages(value: string) {
  const clean = value.trim();
  if (!clean) return [];
  const pages = new Set<number>();
  for (const token of clean.split(",")) {
    const part = token.trim();
    if (!part) continue;
    const range = /^(\d+)-(\d+)$/.exec(part);
    if (range) {
      const start = Number(range[1]);
      const end = Number(range[2]);
      if (start < 1 || end < start || end - start > 19) {
        throw new Error("页码范围无效；每次最多选择 20 页。");
      }
      for (let page = start; page <= end; page += 1) pages.add(page);
    } else if (/^\d+$/.test(part)) {
      const page = Number(part);
      if (page < 1) throw new Error("页码必须从 1 开始。");
      pages.add(page);
    } else {
      throw new Error("页码格式示例：1-3,5。");
    }
  }
  const result = [...pages].sort((a, b) => a - b);
  if (result.length > 20) throw new Error("每次最多选择 20 页。");
  return result;
}

function artifactPreview(
  artifact: FileAnalysisArtifact,
  artifactId: string,
): ParsedDocumentPreview {
  return {
    asset_id: artifact.asset_id,
    artifact_id: artifactId,
    artifact_expires_at: "",
    format: artifact.format,
    title: artifact.source_filename,
    sections: artifact.sections.map((section) => ({
      text: section.text,
      page: section.page,
      line_range: null,
      slide: null,
      sheet: null,
      row_range: null,
      time_range: null,
      heading_path: [],
    })),
    warnings: [...artifact.warnings],
    extracted_chars: artifact.extracted_chars,
    truncated: artifact.truncated,
  };
}

function statusLabel(job: FileAnalysisJob | null) {
  if (!job) return "尚未开始";
  return {
    queued: "等待处理",
    running: "正在处理",
    completed: "识别完成，等待选择用途",
    failed: "识别失败，可重新配置后再试",
    cancel_requested: "正在请求取消",
    cancelled: "已取消",
    interrupted: "服务重启后已中断，不会自动重放",
  }[job.status];
}

async function saveAnalysisToRag(
  kbId: string,
  assetId: string,
  artifactId: string,
  scopeId: string,
) {
  const response = await fetch(
    `/api/rag/knowledge_bases/${encodeURIComponent(kbId)}/documents/from-file-analysis`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        asset_id: assetId,
        analysis_artifact_id: artifactId,
        chat_scope_id: scopeId,
      }),
    },
  );
  if (!response.ok) {
    let message = "保存到资料库失败，请稍后重试。";
    try {
      const payload = (await response.json()) as {
        detail?: string | { message?: string };
      };
      if (typeof payload.detail === "string") message = payload.detail;
      else if (payload.detail?.message) message = payload.detail.message;
    } catch {
      // Stable fallback only; never render a raw provider response.
    }
    throw new Error(message);
  }
}

export default function ChatVisualAnalysisPanel({
  modelId,
  scopeId,
  disabled,
  blockedReason,
  drawerHost,
  inputBoundary,
  knowledgeBases,
  resetVersion,
  discardVersion,
  hideTrigger = false,
  onError,
  onCapabilityChange,
  onStateChange,
}: ChatVisualAnalysisPanelProps) {
  const titleId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const [open, setOpen] = useState(false);
  const [capability, setCapability] = useState<"loading" | "ready" | "disabled">("loading");
  const [targets, setTargets] = useState<FileAnalysisTarget[]>([]);
  const [asset, setAsset] = useState<{
    id: string;
    name: string;
    format: string;
    size: number;
  } | null>(null);
  const [mode, setMode] = useState<FileAnalysisMode>("vision");
  const [targetId, setTargetId] = useState("");
  const [pages, setPages] = useState("");
  const [prompt, setPrompt] = useState("");
  const [paidAcknowledged, setPaidAcknowledged] = useState(false);
  const [job, setJob] = useState<FileAnalysisJob | null>(null);
  const [prepared, setPrepared] = useState<PreparedChatAnalysisFile | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [saveKbId, setSaveKbId] = useState("");
  const [drawerStyle, setDrawerStyle] = useState<CSSProperties>({});

  const modeTargets = useMemo(
    () => targets.filter((target) => target.mode === mode),
    [mode, targets],
  );
  const selectedTarget = modeTargets.find((target) => target.target_id === targetId) ?? null;
  const activeJob = Boolean(
    job && ["queued", "running", "cancel_requested"].includes(job.status),
  );
  const hasVisionTarget = targets.some((target) => target.mode === "vision");
  const fileAccept = hasVisionTarget ? ACCEPT : ".pdf";

  useEffect(() => {
    onCapabilityChange?.(capability);
  }, [capability, onCapabilityChange]);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      fetchFileCapabilities(controller.signal, { purpose: "chat", modelId }),
      fetchFileAnalysisTargets(controller.signal),
      listChatFileAnalyses(scopeId, controller.signal),
    ])
      .then(([registry, targetItems, jobs]) => {
        const analysis = registry?.capabilities.find(
          (item) =>
            item.purpose === "chat" &&
            item.input_kind === "visual_analysis" &&
            item.interaction_status === "ready" &&
            item.analysis_options.some((option) => option.interaction_status === "ready"),
        );
        if (!analysis || targetItems.length === 0) {
          setCapability("disabled");
          return;
        }
        setTargets(targetItems);
        setCapability("ready");
        const preferred =
          targetItems.find((target) => target.mode === "vision" && target.model_id === modelId) ??
          targetItems.find((target) => target.mode === "vision") ??
          targetItems[0];
        setMode(preferred.mode);
        setTargetId(preferred.target_id);
        const recovered = jobs.find((item) =>
          ["queued", "running", "cancel_requested", "completed", "failed", "interrupted"].includes(item.status),
        );
        if (recovered) {
          setJob(recovered);
          const result = recovered.result;
          setAsset({
            id: recovered.asset_id,
            name: result?.source_filename ?? `任务 ${recovered.analysis_id.slice(0, 8)}`,
            format: result?.format ?? "",
            size: 0,
          });
          setMode(recovered.mode);
          setTargetId(recovered.target_id);
          setPages(recovered.selected_pages.join(","));
          if (recovered.status === "completed") {
            setMessage(
              "任务已恢复。出于隐私保护，一次性提示正文不会持久化；用于本轮发送前请重新输入与原任务完全相同的提示。",
            );
          }
        }
      })
      .catch(() => setCapability("disabled"));
    return () => controller.abort();
  }, [modelId, scopeId]);

  useEffect(() => {
    const active = job?.status === "queued" || job?.status === "running" || job?.status === "cancel_requested";
    if (!active || !asset) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      void fetchChatFileAnalysis(asset.id, job.analysis_id, scopeId, controller.signal)
        .then(setJob)
        .catch(() => undefined);
    }, 1000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [asset, job?.analysis_id, job?.status, scopeId]);

  useEffect(() => {
    onStateChange({
      files: prepared ? [prepared] : [],
      count: asset ? 1 : 0,
      busy: busy || Boolean(job && ["queued", "running", "cancel_requested"].includes(job.status)),
      allConfirmed: Boolean(prepared),
    });
  }, [asset, busy, job, onStateChange, prepared]);

  useEffect(() => {
    if (!open) return;
    const update = () => {
      if (!drawerHost) return;
      const rect = computeChatFileDrawerRect(
        drawerHost.getBoundingClientRect(),
        inputBoundary?.getBoundingClientRect() ?? null,
        window.innerWidth,
        window.innerHeight,
      );
      if (!rect) return;
      setDrawerStyle({
        position: "fixed",
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
        zIndex: 60,
      });
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    window.requestAnimationFrame(() => closeRef.current?.focus());
    return () => {
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [drawerHost, inputBoundary, open]);

  useEffect(() => {
    const openPanel = (event: Event) => {
      if (capability !== "ready") return;
      const detail = (event as CustomEvent<ChatVisualAnalysisHandoff>).detail;
      if (detail?.assetId) {
        controllerRef.current?.abort();
        setAsset({
          id: detail.assetId,
          name: detail.displayName,
          format: detail.format,
          size: detail.byteSize,
        });
        setJob(null);
        setPrepared(null);
        setPages("");
        setPrompt("");
        setPaidAcknowledged(false);
        const preferred =
          targets.find((target) => target.mode === "vision") ?? targets[0];
        if (preferred) {
          setMode(preferred.mode);
          setTargetId(preferred.target_id);
        }
        setMessage(
          "已接管扫描 PDF 原件，未重新上传且尚未外发。请选择页码、明确目标并确认。",
        );
      }
      setOpen(true);
    };
    window.addEventListener("modelmirror:open-chat-visual-analysis", openPanel);
    return () => window.removeEventListener("modelmirror:open-chat-visual-analysis", openPanel);
  }, [capability, targets]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        window.requestAnimationFrame(() => triggerRef.current?.focus());
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open]);

  function clearLocal() {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setAsset(null);
    setJob(null);
    setPrepared(null);
    setPages("");
    setPrompt("");
    setPaidAcknowledged(false);
    setMessage("");
  }

  useEffect(() => {
    if (resetVersion > 0) clearLocal();
    // The backend consumes the original after successful message_end.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetVersion]);

  useEffect(() => {
    if (discardVersion <= 0) return;
    const current = asset;
    const running = job;
    clearLocal();
    if (current && running && ["queued", "running", "cancel_requested"].includes(running.status)) {
      void cancelChatFileAnalysis(current.id, running.analysis_id, scopeId).catch(() => undefined);
    }
    if (current) void deleteChatFile(current.id, scopeId).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [discardVersion]);

  async function chooseFile(file: File) {
    if (blockedReason) return onError(blockedReason);
    if (activeJob) {
      return onError("当前任务仍在处理；请先请求取消并等待状态更新。");
    }
    if (!/\.(pdf|png|jpe?g|webp)$/i.test(file.name)) {
      return onError("一次性视觉/OCR 仅支持 PDF、JPEG、PNG 或 WebP。");
    }
    if (!/\.pdf$/i.test(file.name) && !hasVisionTarget) {
      return onError("当前没有实时可调用的视觉模型；供应商 OCR 只接受 PDF。");
    }
    if (file.size === 0 || file.size > 10 * 1024 * 1024) {
      return onError("文件必须非空且不超过 10 MiB。");
    }
    if (asset) {
      try {
        await deleteChatFile(asset.id, scopeId);
      } catch (error) {
        return onError(
          error instanceof Error
            ? error.message
            : "旧文件未能安全删除，请稍后重试。",
        );
      }
    }
    clearLocal();
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    try {
      const uploaded = await uploadChatAnalysisFile(file, scopeId, controller.signal);
      setAsset({
        id: uploaded.asset_id,
        name: uploaded.display_name,
        format: uploaded.format,
        size: uploaded.byte_size,
      });
      if (uploaded.format !== "pdf") {
        setMode("vision");
        const vision = targets.find((target) => target.mode === "vision");
        setTargetId(vision?.target_id ?? "");
      }
      setMessage("文件仅完成本地预检，尚未外发。请选择目标并确认。 ");
    } catch (error) {
      onError(error instanceof Error ? error.message : "视觉/OCR 文件上传失败。");
    } finally {
      setBusy(false);
      controllerRef.current = null;
    }
  }

  async function startAnalysis() {
    if (!asset || !selectedTarget) return;
    if (mode === "provider_ocr" && !paidAcknowledged) {
      return onError("使用 OpenRouter mistral-ocr 前必须确认付费与外发说明。");
    }
    let selectedPages: number[];
    try {
      selectedPages = parseVisualAnalysisPages(pages);
    } catch (error) {
      return onError(error instanceof Error ? error.message : "页码无效。");
    }
    const preflightInput = {
      scope_id: scopeId,
      mode,
      target_id: selectedTarget.target_id,
      selected_pages: selectedPages,
      prompt,
    };
    const confirmedInput = {
      ...preflightInput,
      paid_acknowledged: paidAcknowledged,
    };
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true);
    setPrepared(null);
    setMessage("");
    try {
      const preflight = await preflightChatFileAnalysis(asset.id, preflightInput, controller.signal);
      const confirmation = await confirmChatFileAnalysis(asset.id, confirmedInput, controller.signal);
      const created = await createChatFileAnalysis(
        asset.id,
        { ...confirmedInput, confirmation_revision: confirmation.confirmation_revision },
        controller.signal,
      );
      setPages(preflight.selected_pages.join(","));
      setJob(created);
      setMessage("服务端确认已绑定当前文件、目标、页码和提示摘要；任务不会自动重试。");
    } catch (error) {
      onError(error instanceof Error ? error.message : "视觉/OCR 任务创建失败。");
    } finally {
      setBusy(false);
      controllerRef.current = null;
    }
  }

  async function prepareForChat() {
    if (!asset || job?.status !== "completed" || !job.result || !job.result_artifact_id) return;
    setBusy(true);
    try {
      const confirmed = await confirmChatFile(
        asset.id,
        scopeId,
        "extract",
        undefined,
        { artifactId: job.result_artifact_id, prompt },
      );
      setPrepared({
        assetId: asset.id,
        analysisArtifactId: job.result_artifact_id,
        analysisPrompt: prompt,
        displayName: job.result.source_filename,
        format: job.result.format,
        byteSize: asset.size,
        handling: "extract",
        confirmationRevision: confirmed.confirmation_revision,
        preview: artifactPreview(job.result, job.result_artifact_id),
        mode: job.result.mode,
      });
      setMessage("已绑定当前识别结果用于本轮发送；修改一次性提示后需重新确认。");
    } catch (error) {
      onError(error instanceof Error ? error.message : "识别结果确认失败。");
    } finally {
      setBusy(false);
    }
  }

  async function saveToKnowledgeBase() {
    if (!asset || job?.status !== "completed" || !job.result_artifact_id || !saveKbId) return;
    setBusy(true);
    try {
      await saveAnalysisToRag(
        saveKbId,
        asset.id,
        job.result_artifact_id,
        scopeId,
      );
      setMessage("识别结果已保存为资料库派生文档；Chat 原件生命周期未延长。");
    } catch (error) {
      onError(error instanceof Error ? error.message : "保存到资料库失败。");
    } finally {
      setBusy(false);
    }
  }

  const panel = open && drawerHost ? createPortal(
    <section
      aria-labelledby={titleId}
      className="overflow-y-auto rounded-2xl border border-cyan-300/25 bg-ink-950/98 p-4 text-slate-100 shadow-2xl backdrop-blur"
      role="region"
      style={drawerStyle}
    >
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold" id={titleId}>一次性视觉 / OCR</h3>
          <p className="mt-1 text-xs leading-5 text-slate-400">
            每个文件单独确认。未确认不会外发或保存，也不会自动切换模型。
          </p>
        </div>
        <button
          className="min-h-11 min-w-11 rounded-full border border-white/10 text-sm hover:bg-white/10"
          onClick={() => {
            setOpen(false);
            window.requestAnimationFrame(() => triggerRef.current?.focus());
          }}
          ref={closeRef}
          type="button"
        >
          关闭
        </button>
      </div>

      <input
        accept={fileAccept}
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (file) void chooseFile(file);
        }}
        ref={inputRef}
        type="file"
      />
      <button
        className="min-h-11 rounded-xl border border-cyan-300/30 bg-cyan-300/10 px-4 text-sm font-semibold text-cyan-100 disabled:opacity-50"
        disabled={busy || activeJob || capability !== "ready" || Boolean(blockedReason)}
        onClick={() => inputRef.current?.click()}
        type="button"
      >
        {asset ? "重新选择单个文件" : "选择 PDF 或图片"}
      </button>
      {asset ? <p className="mt-2 text-sm">{asset.name} · {asset.format.toUpperCase()}</p> : null}

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-slate-300">
          处理方式
          <select
            className="mt-1 min-h-11 w-full rounded-lg border border-white/10 bg-ink-900 px-3"
            disabled={!asset || busy}
            onChange={(event) => {
              const next = event.target.value as FileAnalysisMode;
              setMode(next);
              setTargetId(targets.find((target) => target.mode === next)?.target_id ?? "");
              setPrepared(null);
            }}
            value={mode}
          >
            {hasVisionTarget ? <option value="vision">视觉理解</option> : null}
            {asset?.format === "pdf" && targets.some((item) => item.mode === "provider_ocr") ? (
              <option value="provider_ocr">供应商 OCR（OpenRouter mistral-ocr）</option>
            ) : null}
          </select>
        </label>
        <label className="text-xs text-slate-300">
          明确连接与模型
          <select
            className="mt-1 min-h-11 w-full rounded-lg border border-white/10 bg-ink-900 px-3"
            disabled={!asset || busy}
            onChange={(event) => {
              setTargetId(event.target.value);
              setPrepared(null);
            }}
            value={targetId}
          >
            {modeTargets.map((target) => (
              <option key={target.target_id} value={target.target_id}>
                {target.connection_name} · {target.model_name}
              </option>
            ))}
          </select>
        </label>
      </div>

      <label className="mt-3 block text-xs text-slate-300">
        PDF 页码（留空表示全部；最多 20 页）
        <input
          className="mt-1 min-h-11 w-full rounded-lg border border-white/10 bg-ink-900 px-3"
          disabled={!asset || asset.format !== "pdf" || busy}
          onChange={(event) => { setPages(event.target.value); setPrepared(null); }}
          placeholder="例如 1-3,5"
          value={pages}
        />
      </label>
      <label className="mt-3 block text-xs text-slate-300">
        一次性提示（最多 2,000 字）
        <textarea
          className="mt-1 min-h-24 w-full rounded-lg border border-white/10 bg-ink-900 px-3 py-2"
          disabled={!asset || busy}
          maxLength={2000}
          onChange={(event) => { setPrompt(event.target.value); setPrepared(null); }}
          placeholder={mode === "vision" ? "例如：读取表格并概括异常趋势" : "OCR 完成后，发送给当前聊天模型时如何使用这些文字"}
          value={prompt}
        />
      </label>

      <div className="mt-3 rounded-xl border border-amber-300/20 bg-amber-300/5 p-3 text-xs leading-5 text-amber-100">
        <p>外发对象：{selectedTarget ? `${selectedTarget.connection_name} / ${selectedTarget.model_name}` : "尚未选择"}</p>
        <p>{mode === "vision" ? "只发送选中页的本地渲染图，不发送完整 PDF。" : "只向官方 OpenRouter 发送本地裁剪后的 PDF，并强制 mistral-ocr；费用以 OpenRouter 实际账单为准。"}</p>
        <p>无自动重试、无跨供应商 fallback。供应商可能接收文件内容。</p>
        <p>
          选择“用于本轮发送”后，识别文字{mode === "provider_ocr" ? "和本次提示" : ""}还会发送给当前聊天模型
          {modelId ? ` ${modelId}` : ""}；不会再次调用 OCR。
        </p>
      </div>
      {mode === "provider_ocr" ? (
        <label className="mt-3 flex min-h-11 items-center gap-3 text-sm">
          <input
            checked={paidAcknowledged}
            onChange={(event) => setPaidAcknowledged(event.target.checked)}
            type="checkbox"
          />
          我确认本次会产生供应商费用，并知悉 OpenRouter 与所选下游模型会接收内容。
        </label>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        <button
          className="min-h-11 rounded-xl bg-cyan-300 px-4 text-sm font-bold text-ink-950 disabled:opacity-50"
          disabled={!asset || !selectedTarget || busy || Boolean(prepared)}
          onClick={() => void startAnalysis()}
          type="button"
        >
          本地预检、确认并开始
        </button>
        {job && ["queued", "running", "cancel_requested"].includes(job.status) && asset ? (
          <button
            className="min-h-11 rounded-xl border border-rose-300/30 px-4 text-sm text-rose-100"
            onClick={() => void cancelChatFileAnalysis(asset.id, job.analysis_id, scopeId).then(setJob).catch((error) => onError(error.message))}
            type="button"
          >
            请求取消
          </button>
        ) : null}
      </div>

      {job ? (
        <div aria-live="polite" className="mt-4 rounded-xl border border-white/10 bg-white/[0.04] p-3">
          <p className="text-sm font-semibold">{statusLabel(job)}</p>
          <p className="mt-1 text-xs text-slate-400">
            已处理 {job.processed_pages}/{job.page_count} 页
            {job.actual_cost_usd ? ` · 供应商报告实际费用 $${job.actual_cost_usd}` : ""}
          </p>
          {job.result?.failed_pages.length ? <p className="mt-1 text-xs text-amber-200">部分失败页：{job.result.failed_pages.join(", ")}</p> : null}
          {job.error_code ? <p className="mt-1 text-xs text-rose-200">稳定错误码：{job.error_code}</p> : null}
        </div>
      ) : null}

      {job?.status === "completed" && job.result && job.result_artifact_id ? (
        <div className="mt-4 space-y-3">
          <div className="max-h-64 space-y-3 overflow-y-auto rounded-xl border border-white/10 p-3">
            {job.result.sections.map((section, index) => (
              <article key={`${section.page}-${section.kind}-${index}`}>
                <p className="text-xs font-semibold text-cyan-200">第 {section.page} 页 · {section.kind}</p>
                <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-slate-200">{section.text}</p>
              </article>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              className="min-h-11 rounded-xl bg-brand-300 px-4 text-sm font-bold text-ink-950 disabled:opacity-50"
              disabled={busy}
              onClick={() => void prepareForChat()}
              type="button"
            >
              用于本轮发送
            </button>
            <select
              aria-label="选择保存到的资料库"
              className="min-h-11 rounded-xl border border-white/10 bg-ink-900 px-3 text-sm"
              onChange={(event) => setSaveKbId(event.target.value)}
              value={saveKbId}
            >
              <option value="">选择资料库</option>
              {knowledgeBases.map((kb) => <option key={kb.id} value={kb.id}>{kb.name}</option>)}
            </select>
            <button
              className="min-h-11 rounded-xl border border-emerald-300/30 px-4 text-sm text-emerald-100 disabled:opacity-50"
              disabled={!saveKbId || busy}
              onClick={() => void saveToKnowledgeBase()}
              type="button"
            >
              保存识别结果到资料库
            </button>
          </div>
        </div>
      ) : null}
      {message ? <p aria-live="polite" className="mt-3 text-xs leading-5 text-slate-300">{message}</p> : null}
    </section>,
    document.body,
  ) : null;

  return (
    <>
      {!hideTrigger ? <button
        aria-controls={open ? titleId : undefined}
        aria-expanded={open}
        className="min-h-11 rounded-full border border-cyan-300/25 bg-cyan-300/10 px-3 text-xs font-semibold text-cyan-100 disabled:cursor-not-allowed disabled:opacity-50"
        disabled={disabled || capability !== "ready"}
        onClick={() => setOpen((value) => !value)}
        ref={triggerRef}
        title={capability === "disabled" ? "视觉/OCR 功能未启用或没有实时可调用目标" : "一次性视觉 / OCR"}
        type="button"
      >
        视觉/OCR{asset ? " · 1" : ""}
      </button> : null}
      {panel}
    </>
  );
}
