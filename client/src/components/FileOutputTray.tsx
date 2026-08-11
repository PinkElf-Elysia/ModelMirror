import { useEffect, useMemo, useRef, useState } from "react";
import {
  Database,
  Download,
  Eye,
  FileOutput as FileOutputIcon,
  RefreshCcw,
  Repeat2,
  Trash2,
  X,
} from "lucide-react";
import type { FileHandling, FilePurpose } from "../data/fileCapabilities";
import {
  confirmFileOutputReuse,
  deleteFileOutput,
  fetchFileOutputKnowledgeBases,
  fetchFileOutputPreview,
  fileOutputDownloadUrl,
  fileOutputPreviewUrl,
  retryFileOutput,
  saveFileOutputToRag,
  type FileOutput,
  type FileOutputKnowledgeBase,
  type FileOutputPreview,
  type FileOutputReuseConfirmation,
} from "../data/fileOutputs";

function canReuseOutput(output: FileOutput, purpose: FilePurpose) {
  return (
    ["text", "document"].includes(output.preview_kind) ||
    (purpose === "chat" &&
      ["image", "audio", "video"].includes(output.preview_kind))
  );
}

interface FileOutputTrayProps {
  outputs: FileOutput[];
  purpose: FilePurpose;
  scopeId: string;
  modelId?: string;
  reuseTargetId?: string;
  title?: string;
  onChange?: (outputs: FileOutput[]) => void;
  onReuse?: (
    output: FileOutput,
    confirmation: FileOutputReuseConfirmation,
  ) => void | Promise<void>;
  onSaveToRag?: (output: FileOutput) => void | Promise<void>;
}

const statusLabels: Record<FileOutput["status"], string> = {
  queued: "等待生成",
  running: "生成中",
  completed: "可用",
  failed: "生成失败",
  cancel_requested: "正在取消",
  cancelled: "已取消",
  interrupted: "生成中断",
  deleting: "已解绑，清理待完成",
  deleted: "已删除",
  expired: "已过期",
};

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / 1024 / 1024).toFixed(1)} MiB`;
}

function previewDocumentText(preview: FileOutputPreview) {
  if (preview.text) return preview.text;
  const document = preview.document;
  if (!document) return "此文件没有可显示的文本预览。";
  const sections = Array.isArray(document.sections) ? document.sections : [];
  const blocks = sections.flatMap((section, index) => {
    if (!section || typeof section !== "object") return [];
    const item = section as Record<string, unknown>;
    if (typeof item.text !== "string") return [];
    const locations = [
      typeof item.page === "number" ? `第 ${item.page} 页` : "",
      typeof item.slide === "number" ? `第 ${item.slide} 张` : "",
      typeof item.sheet === "string" ? `工作表「${item.sheet}」` : "",
      typeof item.row_range === "string" ? item.row_range : "",
      Array.isArray(item.heading_path)
        ? item.heading_path.filter((part) => typeof part === "string").join(" › ")
        : "",
    ].filter(Boolean);
    return [`${locations.join(" · ") || `片段 ${index + 1}`}\n${item.text}`];
  });
  return blocks.join("\n\n") || "此文件没有可显示的文本预览。";
}

export default function FileOutputTray({
  outputs,
  purpose,
  scopeId,
  modelId,
  reuseTargetId,
  title = "文件输出",
  onChange,
  onReuse,
  onSaveToRag,
}: FileOutputTrayProps) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [preview, setPreview] = useState<FileOutputPreview | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [saveOutputId, setSaveOutputId] = useState<string | null>(null);
  const [knowledgeBases, setKnowledgeBases] = useState<FileOutputKnowledgeBase[]>([]);
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState("");
  const triggerRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const closePreviewRef = useRef<HTMLButtonElement | null>(null);
  const active = useMemo(
    () => outputs.find((output) => output.output_id === openId) ?? null,
    [openId, outputs],
  );

  useEffect(() => {
    if (openId && !active) {
      setOpenId(null);
      setPreview(null);
    }
  }, [active, openId]);

  useEffect(() => {
    if (!openId) return;
    const frame = window.requestAnimationFrame(() => closePreviewRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [openId]);

  if (outputs.length === 0) return null;

  function replace(output: FileOutput) {
    onChange?.(
      outputs.map((item) =>
        item.output_id === output.output_id ? output : item,
      ),
    );
  }

  function closePreview() {
    const previousId = openId;
    setOpenId(null);
    setPreview(null);
    setError("");
    window.requestAnimationFrame(() => triggerRefs.current[previousId ?? ""]?.focus());
  }

  async function openPreview(output: FileOutput) {
    setError("");
    setNotice("");
    setOpenId(output.output_id);
    setPreview(null);
    if (["image", "audio", "video"].includes(output.preview_kind)) return;
    setBusyId(output.output_id);
    try {
      setPreview(
        await fetchFileOutputPreview(output.output_id, purpose, scopeId),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法加载文件预览。");
    } finally {
      setBusyId(null);
    }
  }

  async function retry(output: FileOutput) {
    setBusyId(output.output_id);
    setError("");
    try {
      const next = await retryFileOutput(output.output_id, purpose, scopeId);
      replace(next);
      setNotice(next.status === "completed" ? "文件已重新生成。" : "已创建重试任务。请稍后刷新状态。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法重试文件生成。");
    } finally {
      setBusyId(null);
    }
  }

  async function reuse(output: FileOutput, handling: FileHandling = "extract") {
    if (!canReuseOutput(output, purpose)) return;
    const targetId = purpose === "chat" ? modelId : reuseTargetId;
    if (!targetId || !onReuse) return;
    setBusyId(output.output_id);
    setError("");
    try {
      const confirmation = await confirmFileOutputReuse(
        output.output_id,
        scopeId,
        targetId,
        handling,
        purpose as Extract<FilePurpose, "chat" | "agent" | "workflow">,
      );
      await onReuse(output, confirmation);
      setNotice("已加入输入托盘；仍需在发送前由你明确确认。模型或处理方式变化后需重新加入。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法加入下轮输入。");
    } finally {
      setBusyId(null);
    }
  }

  async function remove(output: FileOutput) {
    if (!window.confirm(`彻底删除“${output.display_name}”？此操作不会撤回已发送的消息。`)) return;
    setBusyId(output.output_id);
    setError("");
    try {
      const result = await deleteFileOutput(output.output_id, purpose, scopeId);
      if (result.cleanupPending) {
        replace({ ...output, status: "deleting", error_code: "cleanup_pending" });
        setNotice("输出已解绑，物理清理尚未完成；刷新后可以继续重试删除。");
      } else {
        onChange?.(outputs.filter((item) => item.output_id !== output.output_id));
        if (openId === output.output_id) closePreview();
        setNotice("输出文件已删除。");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法删除输出文件。");
    } finally {
      setBusyId(null);
    }
  }

  async function beginSaveToRag(output: FileOutput) {
    if (!['text', 'document'].includes(output.preview_kind)) return;
    if (onSaveToRag) {
      setBusyId(output.output_id);
      setError("");
      try {
        await onSaveToRag(output);
        setNotice("输出已保存为独立资料库文档；原输出仍按 7 天期限保留。");
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "无法保存到资料库。");
      } finally {
        setBusyId(null);
      }
      return;
    }
    setBusyId(output.output_id);
    setError("");
    try {
      const items = await fetchFileOutputKnowledgeBases();
      setKnowledgeBases(items);
      setSelectedKnowledgeBaseId(items[0]?.id ?? "");
      setSaveOutputId(output.output_id);
      if (items.length === 0) setNotice("当前没有可写入的资料库。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法读取资料库列表。");
    } finally {
      setBusyId(null);
    }
  }

  async function confirmSaveToRag() {
    const output = outputs.find((item) => item.output_id === saveOutputId);
    if (!output || !selectedKnowledgeBaseId) return;
    setBusyId(output.output_id);
    setError("");
    try {
      await saveFileOutputToRag(output, selectedKnowledgeBaseId);
      setSaveOutputId(null);
      setNotice("输出已保存为独立资料库文档；重复保存会返回同一份文档。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法保存到资料库。");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section
      aria-label={title}
      className="mt-3 overflow-hidden rounded-lg border border-cyan-300/20 bg-ink-950/35"
      onKeyDown={(event) => {
        if (event.key === "Escape" && openId) {
          event.preventDefault();
          closePreview();
        }
      }}
    >
      <div className="flex items-center gap-2 border-b border-white/10 px-3 py-2">
        <FileOutputIcon aria-hidden="true" className="h-4 w-4 text-cyan-200" />
        <h3 className="text-xs font-semibold text-cyan-50">{title}</h3>
        <span className="text-[11px] text-slate-400">{outputs.length} 个</span>
      </div>
      <ul className="divide-y divide-white/10">
        {outputs.map((output) => {
          const completed = output.status === "completed";
          const failed = ["failed", "cancelled", "interrupted"].includes(output.status);
          const busy = busyId === output.output_id;
          return (
            <li className="px-3 py-2" key={output.output_id}>
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <div className="min-w-[10rem] flex-1">
                  <p className="truncate text-xs font-semibold text-slate-100" title={output.display_name}>
                    {output.display_name}
                  </p>
                  <p className="mt-0.5 text-[11px] text-slate-400">
                    {statusLabels[output.status]} · {output.format.toUpperCase()} · {formatBytes(output.byte_size)}
                  </p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {output.preview_kind !== "none" && completed ? (
                    <button
                      aria-expanded={openId === output.output_id}
                      className="inline-flex min-h-11 items-center gap-1 rounded-md border border-white/10 px-2.5 text-[11px] font-semibold text-slate-200 transition hover:border-cyan-300/35 hover:text-cyan-100 focus:outline-none focus:ring-4 focus:ring-cyan-300/10"
                      disabled={busy}
                      onClick={() => void openPreview(output)}
                      ref={(node) => { triggerRefs.current[output.output_id] = node; }}
                      type="button"
                    >
                      <Eye aria-hidden="true" className="h-3.5 w-3.5" />预览
                    </button>
                  ) : null}
                  {completed ? (
                    <a
                      className="inline-flex min-h-11 items-center gap-1 rounded-md border border-white/10 px-2.5 text-[11px] font-semibold text-slate-200 transition hover:border-cyan-300/35 hover:text-cyan-100 focus:outline-none focus:ring-4 focus:ring-cyan-300/10"
                      download
                      href={fileOutputDownloadUrl(output.output_id, purpose, scopeId)}
                    >
                      <Download aria-hidden="true" className="h-3.5 w-3.5" />下载
                    </a>
                  ) : null}
                  {completed && ["chat", "agent", "workflow"].includes(purpose) ? (
                    <button
                      className="inline-flex min-h-11 items-center gap-1 rounded-md border border-white/10 px-2.5 text-[11px] font-semibold text-slate-200 transition hover:border-cyan-300/35 hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-45 focus:outline-none focus:ring-4 focus:ring-cyan-300/10"
                      disabled={busy || !canReuseOutput(output, purpose) || !(purpose === "chat" ? modelId : reuseTargetId) || !onReuse}
                      onClick={() => void reuse(output)}
                      title={!canReuseOutput(output, purpose)
                        ? "该模块没有与此输出类型对应的输入流程。"
                        : !onReuse
                          ? "当前页面尚未接通同会话复用。"
                          : undefined}
                      type="button"
                    >
                      <Repeat2 aria-hidden="true" className="h-3.5 w-3.5" />下轮复用
                    </button>
                  ) : null}
                  {completed ? (
                    <button
                      className="inline-flex min-h-11 items-center gap-1 rounded-md border border-white/10 px-2.5 text-[11px] font-semibold text-slate-200 transition hover:border-cyan-300/35 hover:text-cyan-100 disabled:cursor-not-allowed disabled:opacity-45 focus:outline-none focus:ring-4 focus:ring-cyan-300/10"
                      disabled={busy || !["text", "document"].includes(output.preview_kind)}
                      onClick={() => void beginSaveToRag(output)}
                      title={!["text", "document"].includes(output.preview_kind)
                        ? "媒体或未知格式需在资料库入口另行确认处理。"
                        : undefined}
                      type="button"
                    >
                      <Database aria-hidden="true" className="h-3.5 w-3.5" />保存资料库
                    </button>
                  ) : null}
                  {failed ? (
                    <button
                      className="inline-flex min-h-11 items-center gap-1 rounded-md border border-amber-300/25 px-2.5 text-[11px] font-semibold text-amber-100 transition hover:border-amber-200/50 focus:outline-none focus:ring-4 focus:ring-amber-300/10"
                      disabled={busy}
                      onClick={() => void retry(output)}
                      type="button"
                    >
                      <RefreshCcw aria-hidden="true" className="h-3.5 w-3.5" />重试
                    </button>
                  ) : null}
                  <button
                    className="inline-flex min-h-11 items-center gap-1 rounded-md border border-rose-300/20 px-2.5 text-[11px] font-semibold text-rose-100 transition hover:border-rose-200/45 focus:outline-none focus:ring-4 focus:ring-rose-300/10"
                    disabled={busy}
                    onClick={() => void remove(output)}
                    type="button"
                  >
                    <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />删除
                  </button>
                </div>
              </div>
              {output.error_code ? (
                <p className="mt-1 text-[11px] text-amber-100">错误：{output.error_code}</p>
              ) : null}
            </li>
          );
        })}
      </ul>

      {saveOutputId ? (
        <div
          aria-label="选择保存资料库"
          className="border-t border-white/10 bg-black/15 p-3"
          role="region"
        >
          <label className="text-xs font-semibold text-slate-200" htmlFor={`file-output-kb-${saveOutputId}`}>
            保存到资料库
          </label>
          <div className="mt-2 flex flex-col gap-2 sm:flex-row sm:items-center">
            <select
              className="min-h-11 min-w-0 flex-1 rounded-md border border-white/10 bg-ink-950 px-3 text-xs text-slate-100 focus:outline-none focus:ring-4 focus:ring-cyan-300/10"
              id={`file-output-kb-${saveOutputId}`}
              onChange={(event) => setSelectedKnowledgeBaseId(event.target.value)}
              value={selectedKnowledgeBaseId}
            >
              {knowledgeBases.map((item) => (
                <option key={item.id} value={item.id}>{item.name}</option>
              ))}
            </select>
            <button
              className="min-h-11 rounded-md bg-cyan-300 px-3 text-xs font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-45"
              disabled={!selectedKnowledgeBaseId || Boolean(busyId)}
              onClick={() => void confirmSaveToRag()}
              type="button"
            >
              确认保存
            </button>
            <button
              className="min-h-11 rounded-md border border-white/10 px-3 text-xs font-semibold text-slate-200"
              onClick={() => setSaveOutputId(null)}
              type="button"
            >
              取消
            </button>
          </div>
          <p className="mt-2 text-[11px] leading-5 text-slate-400">
            只复制本地可解析内容，不调用 OCR、视觉模型或外部 embedding；输出原件的过期时间不变。
          </p>
        </div>
      ) : null}

      {active ? (
        <div
          aria-label={`${active.display_name} 预览`}
          className="border-t border-cyan-300/20 bg-surface-900/95 p-3"
          role="region"
        >
          <div className="mb-2 flex items-center justify-between gap-3">
            <p className="truncate text-xs font-semibold text-slate-100">{active.display_name}</p>
            <button
              aria-label="关闭文件预览"
              className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-md border border-white/10 text-slate-300 hover:border-cyan-300/35 hover:text-cyan-100 focus:outline-none focus:ring-4 focus:ring-cyan-300/10"
              onClick={closePreview}
              ref={closePreviewRef}
              type="button"
            >
              <X aria-hidden="true" className="h-4 w-4" />
            </button>
          </div>
          {busyId === active.output_id ? <p className="text-xs text-slate-400">正在加载安全预览…</p> : null}
          {active.preview_kind === "image" ? (
            <img alt={active.display_name} className="max-h-72 w-full rounded-md bg-black/30 object-contain" src={fileOutputPreviewUrl(active.output_id, purpose, scopeId)} />
          ) : null}
          {active.preview_kind === "audio" ? <audio className="w-full" controls src={fileOutputPreviewUrl(active.output_id, purpose, scopeId)} /> : null}
          {active.preview_kind === "video" ? <video className="max-h-72 w-full rounded-md bg-black" controls src={fileOutputPreviewUrl(active.output_id, purpose, scopeId)} /> : null}
          {preview ? (
            <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md border border-white/10 bg-black/25 p-3 text-xs leading-5 text-slate-200">
              {previewDocumentText(preview)}
            </pre>
          ) : null}
        </div>
      ) : null}

      <div aria-live="polite" className="px-3" role="status">
        {notice ? <p className="py-2 text-[11px] text-cyan-100">{notice}</p> : null}
        {error ? <p className="py-2 text-[11px] text-rose-100">{error}</p> : null}
      </div>
    </section>
  );
}
