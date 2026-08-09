import {
  CircleAlert,
  Download,
  History,
  LoaderCircle,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type {
  CodingProjectKind,
  CodingRecoveryStatus,
} from "../types/coding";

export type CodingRecoveryAction =
  | "idle"
  | "resuming"
  | "downloading"
  | "discarding";

interface CodingRecoveryCardProps {
  action: CodingRecoveryAction;
  error: string;
  notice: string;
  onDiscard: () => Promise<void>;
  onDownload: () => Promise<void>;
  onResume: () => Promise<void>;
  recovery: CodingRecoveryStatus;
}

const reasonText: Record<string, string> = {
  disabled: "代码助手尚未启用，当前只能下载或放弃这份修改。",
  draft_unavailable: "当前不是修改模式，只能下载或放弃这份修改。",
  snapshot_mismatch:
    "项目已经更新，这份修改不能继续处理，但仍可下载后交给开发者查看。",
  worker_unavailable:
    "代码服务暂时不可用，这份修改仍安全保留，可以先下载备份。",
  project_changed:
    "这个项目已经更新，不能继续原来的修改，但仍可下载 Diff 留作参考。",
  project_dirty:
    "这个项目出现了新的本地改动。为避免混在一起，现在只允许下载或放弃。",
  project_not_found:
    "这个项目已不在可选列表中，现在只允许下载或放弃。",
  project_source_unavailable:
    "暂时无法读取这个项目，这份修改仍安全保留，可以先下载备份。",
  commit_recovery_conflict:
    "本地版本状态后来发生了变化。为避免覆盖人工内容，现在只允许查看、下载或放弃。",
  recovery_conflict:
    "外部内容与上次记录不一致。为避免覆盖人工内容，现在只允许查看、下载或放弃。",
};

const projectHostOfflineReasons = new Set([
  "project_host_offline",
  "project_host_unavailable",
]);

const projectHostWritebackReasons = new Set([
  "project_host_protocol_readonly",
  "project_host_writeback_disabled",
  "project_host_writeback_unavailable",
  "project_operation_unavailable",
]);

function stateText(
  state: CodingRecoveryStatus["state"],
  projectKind: CodingProjectKind,
) {
  if (state === "applied") {
    return projectKind === "host_git"
      ? "修改已写入所选本地项目"
      : "修改已写入专用项目副本";
  }
  if (state === "committed") {
    return projectKind === "host_git"
      ? "修改已在所选本地项目中保存为本地版本"
      : "修改已保存为本地版本";
  }
  if (state === "undone") {
    return projectKind === "host_git"
      ? "所选本地项目的本地版本已撤销，文件修改仍保留"
      : "本地版本已撤销，文件修改仍保留";
  }
  if (state === "reverted") {
    return projectKind === "host_git"
      ? "所选本地项目的写入已撤销，修改记录仍保留"
      : "写入已撤销，修改记录仍保留";
  }
  if (state === "conflict") return "需要人工确认外部变化";
  return "修改草稿尚未完成";
}

function recoveryReasonText(
  recovery: CodingRecoveryStatus,
  projectKind: CodingProjectKind,
) {
  const reason = recovery.reason ?? recovery.project?.writeback_reason ?? null;
  if (projectKind === "host_git" && reason) {
    if (projectHostOfflineReasons.has(reason)) {
      return "本地项目助手连接已断开。请重新打开助手并恢复连接；这份 Diff 仍保留，可以先下载备份。";
    }
    if (projectHostWritebackReasons.has(reason)) {
      return "当前助手不能继续执行写入。请使用支持写入的最新版助手重新连接；这份 Diff 仍保留，可以先下载备份。";
    }
    if (reason === "apply_recovery_conflict") {
      return "所选本地项目后来发生了变化。为避免覆盖人工内容，现在只允许查看、下载或放弃。";
    }
  }
  if (reason === "apply_recovery_conflict") {
    return "专用项目副本后来发生了变化。为避免覆盖人工内容，现在只允许查看、下载或放弃。";
  }
  return reason
    ? reasonText[reason] ?? "这份修改暂时不能继续处理，但仍可下载或放弃。"
    : "你可以继续处理，也可以先下载 Diff 留作备份。";
}

function discardDescription(projectKind: CodingProjectKind) {
  if (projectKind === "host_git") {
    return "页面将不再提供继续或下载。放弃恢复记录不会改变所选本地项目中已经写入的文件或本地版本。";
  }
  if (projectKind === "local_clone") {
    return "页面将不再提供继续或下载。已经写入受控项目副本的文件和本地版本不会被改变。";
  }
  return "页面将不再提供继续或下载。已经写入 ModelMirror 专用项目副本的文件和本地版本不会被改变。";
}

function formatSavedAt(value?: number) {
  if (!value || !Number.isFinite(value)) return "保存时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value * 1000));
}

export default function CodingRecoveryCard({
  action,
  error,
  notice,
  onDiscard,
  onDownload,
  onResume,
  recovery,
}: CodingRecoveryCardProps) {
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const recoveryInFlightRef = useRef(false);
  const [localBusy, setLocalBusy] = useState(false);
  const busy = action !== "idle" || localBusy;
  const projectKind = recovery.project?.kind ?? "builtin";
  const reason = recoveryReasonText(recovery, projectKind);

  const runRecoveryAction = async (callback: () => Promise<void>) => {
    if (recoveryInFlightRef.current || action !== "idle") return;
    recoveryInFlightRef.current = true;
    setLocalBusy(true);
    try {
      await callback();
    } finally {
      recoveryInFlightRef.current = false;
      setLocalBusy(false);
    }
  };

  useEffect(() => {
    setConfirmDiscard(false);
  }, [recovery.revision, recovery.state]);

  return (
    <section
      aria-busy={busy}
      aria-labelledby="coding-recovery-title"
      className="mb-5 overflow-hidden rounded-lg border border-cyan-300/25 bg-cyan-300/[0.07]"
    >
      <div className="flex items-start gap-3 px-4 py-4 sm:px-5">
        {recovery.state === "conflict" || recovery.can_resume === false ? (
          <CircleAlert
            aria-hidden="true"
            className="mt-0.5 shrink-0 text-amber-200"
            size={20}
          />
        ) : (
          <History
            aria-hidden="true"
            className="mt-0.5 shrink-0 text-cyan-200"
            size={20}
          />
        )}
        <div className="min-w-0 flex-1">
          <h2
            className="text-base font-semibold text-white"
            id="coding-recovery-title"
          >
            发现一份未完成的修改
          </h2>
          <p className="mt-1 break-words text-sm leading-6 text-slate-300">
            {stateText(recovery.state, projectKind)}，共 {recovery.file_count ?? 0} 个文件。
            此前对话没有保存。
          </p>
          {recovery.project ? (
            <div className="mt-1 min-w-0 text-xs font-medium text-cyan-100/90">
              <p className="min-w-0">
                对应项目：
                <span className="break-all">{recovery.project.name}</span>
              </p>
              {recovery.project.branch ? (
                <p className="mt-0.5 min-w-0">
                  记录分支：
                  <span className="break-all">{recovery.project.branch}</span>
                </p>
              ) : null}
            </div>
          ) : null}
          <p className="mt-1 break-words text-xs leading-5 text-slate-400">
            {reason} 上次保存：{formatSavedAt(recovery.updated_at)}。
          </p>
        </div>
      </div>

      <div className="border-t border-white/10 px-4 py-4 sm:px-5">
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          <button
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-cyan-300 px-4 text-sm font-semibold text-ink-950 transition hover:bg-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-100 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
            disabled={busy || recovery.can_resume !== true}
            onClick={() => void runRecoveryAction(onResume)}
            type="button"
          >
            {action === "resuming" ? (
              <LoaderCircle
                aria-hidden="true"
                className="animate-spin motion-reduce:animate-none"
                size={16}
              />
            ) : (
              <RotateCcw aria-hidden="true" size={16} />
            )}
            继续上次修改
          </button>
          <button
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-white/15 px-4 text-sm font-semibold text-slate-200 transition hover:border-cyan-300/40 hover:bg-white/[0.055] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200/70 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={busy || recovery.can_download !== true}
            onClick={() => void runRecoveryAction(onDownload)}
            type="button"
          >
            {action === "downloading" ? (
              <LoaderCircle
                aria-hidden="true"
                className="animate-spin motion-reduce:animate-none"
                size={16}
              />
            ) : (
              <Download aria-hidden="true" size={16} />
            )}
            下载 Diff
          </button>
          <button
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-4 text-sm font-semibold text-rose-100 transition hover:bg-rose-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-200/70 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={busy}
            onClick={() => setConfirmDiscard(true)}
            type="button"
          >
            <Trash2 aria-hidden="true" size={16} />
            放弃这份修改
          </button>
        </div>

        {confirmDiscard ? (
          <div
            aria-live="polite"
            className="mt-4 rounded-lg bg-rose-300/10 p-3 text-sm text-rose-100"
          >
            <p className="font-semibold">确定放弃这份恢复记录吗？</p>
            <p className="mt-1 break-words text-xs leading-5 text-rose-100/80">
              {discardDescription(projectKind)}
            </p>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <button
                className="min-h-11 rounded-lg border border-white/15 px-3 text-xs font-semibold transition hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={busy}
                onClick={() => setConfirmDiscard(false)}
                type="button"
              >
                继续保留
              </button>
              <button
                className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-rose-200 px-3 text-xs font-semibold text-rose-950 transition hover:bg-rose-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-100 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={busy}
                onClick={() => void runRecoveryAction(onDiscard)}
                type="button"
              >
                {action === "discarding" ? (
                  <LoaderCircle
                    aria-hidden="true"
                    className="animate-spin motion-reduce:animate-none"
                    size={14}
                  />
                ) : null}
                确认放弃
              </button>
            </div>
          </div>
        ) : null}

        {notice ? (
          <p
            aria-live="polite"
            className="mt-3 break-words text-sm text-cyan-100"
            role="status"
          >
            {notice}
          </p>
        ) : null}
        {error ? (
          <p
            aria-live="assertive"
            className="mt-3 break-words text-sm text-rose-100"
            role="alert"
          >
            {error}
          </p>
        ) : null}
      </div>
    </section>
  );
}
