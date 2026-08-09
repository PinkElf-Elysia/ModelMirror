import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface McpApprovalTargetPreview {
  action_label: string;
  resource: {
    type: string;
    label: string;
    id_suffix: string;
  };
  changes: Array<{
    field: string;
    summary: string;
  }>;
  content: {
    bytes: number;
    sha256_prefix: string;
  } | null;
  impact: string;
  destructive: boolean;
}

export interface McpCatalogApprovalRequest {
  code: "approval_required";
  message: string;
  approval_id: string;
  summary?: string;
  argument_digest: string;
  expires_at: number | string;
  idempotency_key?: string;
  target_preview?: McpApprovalTargetPreview;
  context_kind?: "workspace" | "remote-resource" | "browser-session" | string;
}

interface McpApprovalDialogProps {
  approval: McpCatalogApprovalRequest;
  busy: boolean;
  onCancel: () => void | Promise<void>;
  onConfirm: () => void | Promise<void>;
}

function expiryMilliseconds(value: number | string) {
  if (typeof value === "number") return value > 10_000_000_000 ? value : value * 1000;
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return numeric > 10_000_000_000 ? numeric : numeric * 1000;
  return Date.parse(value);
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
}

function shorten(value: string) {
  if (value.length <= 18) return value;
  return `${value.slice(0, 10)}…${value.slice(-6)}`;
}

export default function McpApprovalDialog({
  approval,
  busy,
  onCancel,
  onConfirm,
}: McpApprovalDialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const cancelButtonRef = useRef<HTMLButtonElement | null>(null);
  const cancelHandlerRef = useRef(onCancel);
  const busyRef = useRef(busy);
  const expiresAt = useMemo(
    () => expiryMilliseconds(approval.expires_at),
    [approval.expires_at],
  );
  const [now, setNow] = useState(() => Date.now());
  const secondsLeft = Number.isFinite(expiresAt)
    ? Math.max(0, Math.ceil((expiresAt - now) / 1000))
    : 0;
  const expired = secondsLeft <= 0;
  const preview = approval.target_preview;
  const isBrowserApproval =
    approval.context_kind === "browser-session" ||
    preview?.resource.type === "browser-session" ||
    preview?.resource.type === "browser-domain";
  const confirmationBlocked = expired || preview?.destructive === true;

  useEffect(() => {
    cancelHandlerRef.current = onCancel;
  }, [onCancel]);

  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [approval.approval_id]);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const root = document.getElementById("root");
    const previousRootInert = root?.inert ?? false;
    const previousOverflow = document.body.style.overflow;
    if (root) root.inert = true;
    document.body.style.overflow = "hidden";
    const focusTimer = window.setTimeout(() => cancelButtonRef.current?.focus(), 0);

    function handleKeyDown(event: KeyboardEvent) {
      const dialog = dialogRef.current;
      if (!dialog) return;
      if (event.key === "Escape" && !busyRef.current) {
        event.preventDefault();
        void cancelHandlerRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (!focusable.length) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", handleKeyDown);
      if (root) root.inert = previousRootInert;
      document.body.style.overflow = previousOverflow;
      previouslyFocused?.focus();
    };
  }, [approval.approval_id]);

  return createPortal(
    <div
      aria-describedby={descriptionId}
      aria-labelledby={titleId}
      aria-modal="true"
      className="fixed inset-0 z-[100] flex items-end justify-center bg-slate-950/85 p-3 backdrop-blur-sm sm:items-center sm:p-6"
      role="dialog"
    >
      <div
        className="surface-card max-h-[92dvh] w-full max-w-xl overflow-y-auto rounded-lg border border-amber-300/25 p-5 sm:p-6"
        ref={dialogRef}
        tabIndex={-1}
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-amber-100">
              {isBrowserApproval ? "一次性浏览器操作审批" : "一次性写入审批"}
            </p>
            <h2 className="mt-2 text-xl font-semibold text-white" id={titleId}>
              {preview?.action_label ?? "确认本次受控操作"}
            </h2>
          </div>
          <span className="rounded-full border border-amber-300/25 bg-amber-300/10 px-2.5 py-1 text-xs font-semibold text-amber-100">
            {expired ? "审批已过期" : `${secondsLeft} 秒后失效`}
          </span>
        </div>

        <div className="mt-4 space-y-3" id={descriptionId}>
          {preview ? (
            <>
              <section className="rounded-lg border border-white/10 bg-white/[0.045] p-4">
                <p className="text-xs font-semibold text-slate-400">
                  {isBrowserApproval ? "目标域与会话" : "目标资源"}
                </p>
                <p className="mt-2 text-sm font-semibold text-white">{preview.resource.label}</p>
                <p className="mt-1 text-xs text-slate-400">
                  {preview.resource.type} · 标识尾号 {preview.resource.id_suffix || "未提供"}
                </p>
              </section>

              <section className="rounded-lg border border-white/10 bg-white/[0.045] p-4">
                <p className="text-xs font-semibold text-slate-400">
                  {isBrowserApproval ? "本次动作摘要" : "本次变更摘要"}
                </p>
                {preview.changes.length > 0 ? (
                  <dl className="mt-2 space-y-2">
                    {preview.changes.map((change, index) => (
                      <div className="grid gap-1 sm:grid-cols-[8rem_1fr]" key={`${change.field}-${index}`}>
                        <dt className="text-xs font-semibold text-slate-300">{change.field}</dt>
                        <dd className="text-sm leading-5 text-white">{change.summary}</dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <p className="mt-2 text-sm text-slate-300">服务端已冻结本次参数，未返回正文。</p>
                )}
                {preview.content ? (
                  <p className="mt-3 text-xs text-slate-400">
                    内容体积 {formatBytes(preview.content.bytes)} · SHA-256 前缀 {preview.content.sha256_prefix}
                  </p>
                ) : null}
              </section>

              <section className="rounded-lg border border-amber-300/20 bg-amber-300/[0.07] p-4">
                <p className="text-xs font-semibold text-amber-100">影响范围</p>
                <p className="mt-2 text-sm leading-6 text-slate-200">{preview.impact}</p>
                <p className={`mt-2 text-xs font-semibold ${preview.destructive ? "text-rose-200" : "text-emerald-200"}`}>
                  {preview.destructive
                    ? "终止性操作已被前端阻断"
                    : isBrowserApproval
                      ? "受控页面操作 · 仅执行一次"
                      : "非终止性写入 · 仅执行一次"}
                </p>
              </section>
            </>
          ) : (
            <p className="rounded-lg border border-white/10 bg-white/[0.045] p-4 text-sm leading-6 text-slate-300">
              {approval.summary ?? approval.message}
            </p>
          )}
        </div>

        <div className="mt-3 rounded-lg bg-black/15 p-3 text-[11px] leading-5 text-slate-400">
          <p>
            {isBrowserApproval
              ? "确认绑定当前项目、浏览器会话、可访问性快照、目标域、工具和冻结参数，只能使用一次；导航、重连或可检测的快照漂移会使审批失效。元素角色与名称来自目标页，不能证明网站真实性；页面脚本仍可能在摘要不变时改变行为，确认前请核对 Origin 和最新截图，勿在不可信页面执行会产生现实后果的操作。"
              : "确认绑定当前项目、会话、工具和冻结参数，只能使用一次；页面不会展示原始参数或正文。"}
          </p>
          {approval.idempotency_key ? (
            <p className="mt-1 font-mono">幂等键：{shorten(approval.idempotency_key)}</p>
          ) : null}
          <p className="mt-1 break-all font-mono">参数摘要：{shorten(approval.argument_digest)}</p>
        </div>

        <p aria-live="polite" className="mt-3 min-h-5 text-xs text-rose-200" role="status">
          {expired
            ? "审批已过期，请取消后重新发起预览。"
            : preview?.destructive
              ? "本批不允许终止性操作，无法确认执行。"
              : ""}
        </p>

        <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            className="min-h-11 rounded-full border border-white/10 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300 disabled:opacity-50"
            disabled={busy}
            onClick={() => void onCancel()}
            ref={cancelButtonRef}
            type="button"
          >
            取消本次操作
          </button>
          <button
            aria-busy={busy}
            className="min-h-11 rounded-full bg-amber-300 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-amber-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-100 focus-visible:ring-offset-2 focus-visible:ring-offset-ink-950 disabled:cursor-not-allowed disabled:opacity-45"
            disabled={busy || confirmationBlocked}
            onClick={() => void onConfirm()}
            type="button"
          >
            {busy
              ? "正在确认…"
              : isBrowserApproval
                ? "确认执行一次"
                : "确认写入一次"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
