import {
  Check,
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  Download,
  FileDiff,
  FilePlus2,
  LoaderCircle,
  RotateCcw,
  Save,
  Trash2,
  Undo2,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import CodingApplyPanel from "./CodingApplyPanel";
import CodingPublishPanel from "./CodingPublishPanel";
import CodingVerificationPanel from "./CodingVerificationPanel";
import type {
  CodingApplyResult,
  CodingCapabilities,
  CodingCommitResult,
  CodingDraftChanges,
  CodingDraftFile,
  CodingPublishResult,
  CodingVerification,
} from "../types/coding";
import { CodingApiError, getCodingDiff } from "../utils/codingApi";

interface CodingChangesPanelProps {
  applyCapability: CodingCapabilities["apply"];
  applyError: string;
  applyResult: CodingApplyResult | null;
  commitCapability: CodingCapabilities["commit"];
  commitError: string;
  commitResult: CodingCommitResult | null;
  changes: CodingDraftChanges | null;
  disabled: boolean;
  frozen: boolean;
  loading: boolean;
  readOnly?: boolean;
  onApply: () => Promise<void>;
  onClose: () => Promise<void>;
  onCommit: (message: string) => Promise<void>;
  onContinue?: () => Promise<void>;
  onDiscard: () => Promise<void>;
  onDownload: () => Promise<void>;
  onCancelVerification: () => Promise<void>;
  onRequestFix: (prompt: string) => void;
  onRunVerification: () => Promise<void>;
  onMarkPublishReady: () => Promise<void>;
  onPublish: (title: string, body: string) => Promise<void>;
  onRevert: () => Promise<void>;
  onUndoCommit: () => Promise<void>;
  onValidate: () => Promise<void>;
  publishCapability: CodingCapabilities["publish"];
  publishError: string;
  publishResult: CodingPublishResult | null;
  sessionId: string | null;
  verification: CodingVerification | null;
  verificationAvailable: boolean;
  verificationError: string;
}

type ActionState = "idle" | "checking" | "downloading" | "discarding";

type CommitAction = "idle" | "closing" | "committing" | "undoing";

const reviewError: Record<string, string> = {
  stale_revision: "修改内容刚刚发生变化，请重新展开文件后再试。",
  validation_failed: "检查尚未通过，请先根据提示修正修改。",
  draft_is_empty: "当前没有可下载的修改。",
  draft_busy: "代码助手仍在处理，请等待本轮结束。",
  session_not_found: "本次修改草稿已经过期，请重新开始。",
  worker_unavailable: "暂时无法读取修改草稿，请稍后重试。",
};

function describeReviewError(error: unknown) {
  if (error instanceof CodingApiError) {
    return reviewError[error.code] ?? "操作未完成，请稍后重试。";
  }
  return "操作未完成，请稍后重试。";
}

function fileStatus(file: CodingDraftFile) {
  return file.status === "added" ? "新增文件" : "已修改";
}

function lineStyle(line: string) {
  if (line.startsWith("+") && !line.startsWith("+++")) {
    return "bg-emerald-300/10 text-emerald-100";
  }
  if (line.startsWith("-") && !line.startsWith("---")) {
    return "bg-rose-300/10 text-rose-100";
  }
  if (line.startsWith("@@")) {
    return "bg-cyan-300/10 text-cyan-100";
  }
  if (
    line.startsWith("diff --git") ||
    line.startsWith("---") ||
    line.startsWith("+++") ||
    line.startsWith("new file mode")
  ) {
    return "text-slate-500";
  }
  return "text-slate-300";
}

const commitReasonText: Record<string, string> = {
  baseline_mismatch:
    "专用项目副本的版本与当前修改不一致，请由开发者重新创建副本。",
  commit_conflict:
    "本地版本记录已被其他操作改变。为避免覆盖内容，本次操作已停止。",
  committer_not_configured:
    "尚未设置独立的本地项目副本。修改仍可查看、下载和撤销应用。",
  committer_timeout:
    "保存等待时间过长，结果暂时无法确认。请保留原说明并再次尝试。",
  committer_unavailable:
    "本地保存服务未启动。修改仍可查看、下载和撤销应用。",
  dirty_index:
    "专用项目副本已有待保存内容，请由开发者先处理后再试。",
  repository_has_remote:
    "专用项目副本仍连接着远程平台。移除远程连接后才能创建本地版本。",
  repository_not_independent:
    "当前副本不是独立的本地仓库，因此只能应用修改，不能创建本地提交。",
  repository_not_ready:
    "专用项目副本尚未准备好，请由开发者检查后再试。",
  rollback_failed:
    "自动恢复未完成，请停止操作并由开发者检查专用项目副本。",
  shared_git_directory:
    "当前副本与其他项目共享版本记录，为保护其他目录，不能在这里保存。",
  snapshot_mismatch:
    "项目版本正在更新，当前修改暂时不能保存为本地版本。",
  target_changed:
    "文件在保存后又发生了变化。为避免覆盖人工内容，本次操作已停止。",
  undo_conflict:
    "保存后文件或版本记录又发生了变化，因此不能安全撤销。",
  unsafe_repository:
    "专用项目副本未通过安全检查，请由开发者重新创建。",
  wrong_branch:
    "专用项目副本不在约定的本地分支，请由开发者切回后再试。",
};

function describeCommitError(error: unknown) {
  if (error instanceof CodingApiError) {
    return (
      commitReasonText[error.code] ??
      "本地保存没有完成，请查看提示后重试。"
    );
  }
  return "本地保存没有完成，请稍后重试。";
}

interface CodingCommitPanelProps {
  applyResult: CodingApplyResult | null;
  capability: CodingCapabilities["commit"];
  changes: CodingDraftChanges;
  disabled: boolean;
  error: string;
  onCommit: (message: string) => Promise<void>;
  onContinue?: () => Promise<void>;
  onClose: () => Promise<void>;
  onUndo: () => Promise<void>;
  publishLocked: boolean;
  result: CodingCommitResult | null;
}

function CodingCommitPanel({
  applyResult,
  capability,
  changes,
  disabled,
  error,
  onCommit,
  onContinue,
  onClose,
  onUndo,
  publishLocked,
  result,
}: CodingCommitPanelProps) {
  const [action, setAction] = useState<CommitAction>("idle");
  const [confirmCommit, setConfirmCommit] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [confirmUndo, setConfirmUndo] = useState(false);
  const [message, setMessage] = useState("");
  const [localError, setLocalError] = useState("");

  useEffect(() => {
    setMessage(result?.message ?? result?.suggested_message ?? "");
    setConfirmCommit(false);
    setConfirmClose(false);
    setConfirmUndo(false);
    setLocalError("");
  }, [
    changes.revision,
    result?.commit_id,
    result?.message,
    result?.suggested_message,
  ]);

  if (applyResult?.state !== "applied" || !applyResult.apply_id) {
    return null;
  }

  const maxChars = capability?.max_message_chars ?? 2_000;
  const subjectLength = (message.split("\n", 1)[0] ?? "").length;
  const normalizedMessage = message.trim();
  const validMessage =
    normalizedMessage.length > 0 &&
    normalizedMessage.length <= maxChars &&
    subjectLength <= 120;
  const committed = result?.state === "committed";
  const waiting =
    action !== "idle" ||
    result?.state === "committing" ||
    result?.state === "undoing";
  const unavailableCopy =
    commitReasonText[capability?.reason ?? ""] ??
    "当前不能创建本地版本，修改仍安全保留在专用项目副本中。";
  const resultError =
    result?.state === "failed"
      ? commitReasonText[result.reason ?? ""] ??
        "本次保存没有完成。修改仍保留，可以使用相同说明重试。"
      : "";
  const technicalReason =
    result?.reason || capability?.reason || (localError || error ? "request_failed" : "");

  const runAction = async (
    nextAction: Exclude<CommitAction, "idle">,
    callback: () => Promise<void>,
  ) => {
    setAction(nextAction);
    setLocalError("");
    try {
      await callback();
      setConfirmCommit(false);
      setConfirmUndo(false);
    } catch (requestError) {
      setLocalError(describeCommitError(requestError));
    } finally {
      setAction("idle");
    }
  };

  if (committed) {
    return (
      <section
        aria-labelledby="coding-commit-title"
        className="mt-5 rounded-lg border border-cyan-300/20 bg-cyan-300/[0.055] p-4"
      >
        <div className="flex items-start gap-3">
          <CheckCircle2
            aria-hidden="true"
            className="mt-0.5 shrink-0 text-cyan-200"
            size={18}
          />
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-white" id="coding-commit-title">
              已保存一个本地版本
            </h3>
            <p aria-live="polite" className="mt-1 text-xs leading-5 text-slate-300">
              {publishLocked
                ? "这份本地版本已进入 GitHub 发布流程，不能再撤销或继续追加修改。"
                : "这份版本只保存在专用项目副本中，不会上传到远程平台。"}
            </p>
            <dl className="mt-3 grid min-w-0 gap-2 text-xs sm:grid-cols-[88px_minmax(0,1fr)]">
              <dt className="text-slate-500">版本说明</dt>
              <dd className="min-w-0 whitespace-pre-wrap break-words text-slate-200">
                {result.message}
              </dd>
              <dt className="text-slate-500">本地编号</dt>
              <dd className="min-w-0">
                <code className="rounded bg-black/25 px-2 py-1 text-cyan-100">
                  {result.short_sha}
                </code>
              </dd>
            </dl>
          </div>
        </div>

        <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          {onContinue && !publishLocked ? (
            <button
              className="inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-lg bg-cyan-300 px-3 text-sm font-semibold text-ink-950 transition hover:bg-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-100 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
              disabled={disabled || waiting}
              onClick={() => void runAction("closing", onContinue)}
              type="button"
            >
              {action === "closing" ? (
                <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={14} />
              ) : null}
              继续修改
            </button>
          ) : null}
          {!publishLocked ? (
            <button
              className="inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-lg border border-amber-300/30 px-3 text-sm font-semibold text-amber-100 transition hover:bg-amber-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-200/70 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
              disabled={disabled || waiting || !result.can_undo}
              onClick={() => {
                setConfirmUndo(true);
                setConfirmClose(false);
              }}
              type="button"
            >
              <Undo2 aria-hidden="true" size={16} />
              撤销本地提交
            </button>
          ) : null}
          <button
            className="inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-lg border border-white/15 px-3 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
            disabled={disabled || waiting}
            onClick={() => {
              setConfirmClose(true);
              setConfirmUndo(false);
            }}
            type="button"
          >
            <X aria-hidden="true" size={16} />
            结束本次修改
          </button>
        </div>

        {confirmUndo ? (
          <div
            aria-live="polite"
            className="mt-4 rounded-lg border border-amber-300/15 bg-amber-300/[0.065] p-3 text-sm text-amber-50"
          >
            <p className="font-semibold">撤销这条本地版本记录吗？</p>
            <p className="mt-1 text-xs leading-5 text-amber-100/80">
              文件修改会继续保留，你可以修改说明后重新保存，或再撤销已应用的修改。
            </p>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <button
                className="min-h-9 rounded-lg border border-white/15 px-3 text-xs font-semibold hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50"
                onClick={() => setConfirmUndo(false)}
                type="button"
              >
                保留本地版本
              </button>
              <button
                className="inline-flex min-h-9 items-center justify-center gap-2 rounded-lg bg-amber-200 px-3 text-xs font-semibold text-amber-950 hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-100"
                onClick={() => void runAction("undoing", onUndo)}
                type="button"
              >
                {action === "undoing" ? (
                  <LoaderCircle
                    aria-hidden="true"
                    className="animate-spin motion-reduce:animate-none"
                    size={14}
                  />
                ) : null}
                确认撤销记录
              </button>
            </div>
          </div>
        ) : null}

        {confirmClose ? (
          <div
            aria-live="polite"
            className="mt-4 rounded-lg bg-white/[0.045] p-3 text-sm text-slate-200"
          >
            <p className="font-semibold">结束本次修改吗？</p>
            <p className="mt-1 text-xs leading-5 text-slate-400">
              本地版本会继续保留；结束后，本页面将不能再撤销这条记录。
            </p>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <button
                className="min-h-9 rounded-lg border border-white/15 px-3 text-xs font-semibold hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50"
                onClick={() => setConfirmClose(false)}
                type="button"
              >
                继续留在本页
              </button>
              <button
                className="inline-flex min-h-9 items-center justify-center gap-2 rounded-lg bg-slate-200 px-3 text-xs font-semibold text-ink-950 hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white"
                onClick={() => void runAction("closing", onClose)}
                type="button"
              >
                {action === "closing" ? (
                  <LoaderCircle
                    aria-hidden="true"
                    className="animate-spin motion-reduce:animate-none"
                    size={14}
                  />
                ) : null}
                确认结束
              </button>
            </div>
          </div>
        ) : null}

        {localError || error ? (
          <p
            aria-live="assertive"
            className="mt-3 rounded-lg bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-100"
            role="alert"
          >
            {localError || error}
          </p>
        ) : null}
      </section>
    );
  }

  return (
    <section
      aria-labelledby="coding-commit-title"
      className="mt-5 rounded-lg border border-white/10 bg-white/[0.025] p-4"
    >
      <div className="flex items-start gap-3">
        {capability?.available ? (
          <Save aria-hidden="true" className="mt-0.5 shrink-0 text-cyan-200" size={18} />
        ) : (
          <CircleAlert
            aria-hidden="true"
            className="mt-0.5 shrink-0 text-slate-400"
            size={18}
          />
        )}
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-white" id="coding-commit-title">
            保存一个可找回的本地版本
          </h3>
          <p className="mt-1 text-xs leading-5 text-slate-400">
            {capability?.available
              ? "填写版本说明后创建本地提交。只保存在专用副本，不会上传，也不会改变你当前使用的项目目录。"
              : unavailableCopy}
          </p>
        </div>
      </div>

      {result?.state === "undone" ? (
        <p
          aria-live="polite"
          className="mt-4 rounded-lg bg-cyan-300/10 px-3 py-2 text-xs leading-5 text-cyan-100"
        >
          本地提交已撤销，文件修改仍然保留。你可以调整说明后重新保存。
        </p>
      ) : null}

      <label
        className="mt-4 block text-xs font-semibold text-slate-200"
        htmlFor="coding-commit-message"
      >
        版本说明
      </label>
      <textarea
        aria-describedby="coding-commit-message-help"
        className="mt-2 min-h-24 w-full min-w-0 resize-y rounded-lg border border-white/10 bg-ink-950/75 px-3 py-2 text-sm leading-6 text-white outline-none transition placeholder:text-slate-500 hover:border-white/20 focus:border-cyan-300/70 focus:ring-4 focus:ring-cyan-300/10 disabled:cursor-not-allowed disabled:opacity-60"
        disabled={!capability?.available || disabled || waiting}
        id="coding-commit-message"
        maxLength={maxChars}
        onChange={(event) => {
          setMessage(event.target.value);
          setConfirmCommit(false);
          setLocalError("");
        }}
        value={message}
      />
      <div
        className="mt-2 flex flex-col gap-1 text-xs text-slate-500 sm:flex-row sm:justify-between"
        id="coding-commit-message-help"
      >
        <span>
          {subjectLength > 120
            ? "第一行最多 120 个字符，请缩短标题。"
            : "第一行写清主要变化，下面可补充原因。"}
        </span>
        <span>{message.length.toLocaleString("zh-CN")} / {maxChars.toLocaleString("zh-CN")}</span>
      </div>

      <button
        className="mt-4 inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-lg bg-cyan-300 px-3 text-sm font-semibold text-ink-950 transition hover:bg-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-100 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400 sm:w-auto"
        disabled={!capability?.available || disabled || waiting || !validMessage}
        onClick={() => setConfirmCommit(true)}
        type="button"
      >
        <Save aria-hidden="true" size={16} />
        创建本地提交
      </button>

      {confirmCommit ? (
        <div
          aria-live="polite"
          className="mt-4 rounded-lg border border-cyan-300/15 bg-cyan-300/[0.065] p-3 text-sm text-cyan-50"
        >
          <p className="font-semibold">确认保存 {changes.file_count} 个文件吗？</p>
          <ul className="mt-2 space-y-1 text-xs leading-5 text-cyan-100/80">
            <li>会在专用项目副本中创建一条本地版本记录。</li>
            <li>不会提交到你当前使用的项目目录，也不会上传。</li>
            <li>保存后仍可安全撤销记录，文件修改会继续保留。</li>
          </ul>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <button
              className="min-h-9 rounded-lg border border-white/15 px-3 text-xs font-semibold hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50"
              onClick={() => setConfirmCommit(false)}
              type="button"
            >
              返回修改说明
            </button>
            <button
              className="inline-flex min-h-9 items-center justify-center gap-2 rounded-lg bg-cyan-200 px-3 text-xs font-semibold text-cyan-950 hover:bg-cyan-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-100"
              onClick={() =>
                void runAction("committing", () => onCommit(normalizedMessage))
              }
              type="button"
            >
              {action === "committing" ? (
                <LoaderCircle
                  aria-hidden="true"
                  className="animate-spin motion-reduce:animate-none"
                  size={14}
                />
              ) : null}
              确认保存到本地
            </button>
          </div>
        </div>
      ) : null}

      {resultError || localError || error ? (
        <p
          aria-live="assertive"
          className="mt-3 rounded-lg bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-100"
          role="alert"
        >
          {localError || error || resultError}
        </p>
      ) : null}

      {technicalReason ? (
        <details className="mt-3 text-xs text-slate-500">
          <summary className="cursor-pointer outline-none hover:text-slate-300 focus-visible:ring-2 focus-visible:ring-cyan-300/60">
            查看技术原因
          </summary>
          <code className="mt-2 block break-all">{technicalReason}</code>
        </details>
      ) : null}
    </section>
  );
}

export default function CodingChangesPanel({
  applyCapability,
  applyError,
  applyResult,
  commitCapability,
  commitError,
  commitResult,
  changes,
  disabled,
  frozen,
  loading,
  readOnly = false,
  onApply,
  onClose,
  onCommit,
  onContinue,
  onDiscard,
  onDownload,
  onCancelVerification,
  onRequestFix,
  onRunVerification,
  onMarkPublishReady,
  onPublish,
  onRevert,
  onUndoCommit,
  onValidate,
  publishCapability,
  publishError,
  publishResult,
  sessionId,
  verification,
  verificationAvailable,
  verificationError,
}: CodingChangesPanelProps) {
  const [action, setAction] = useState<ActionState>("idle");
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [confirmDownload, setConfirmDownload] = useState(false);
  const [expandedPath, setExpandedPath] = useState<string | null>(null);
  const [diffs, setDiffs] = useState<Record<string, string>>({});
  const [diffLoading, setDiffLoading] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    setExpandedPath(null);
    setDiffs({});
    setConfirmDiscard(false);
    setConfirmDownload(false);
    setMessage("");
  }, [
    changes?.revision,
    frozen,
    sessionId,
    verification?.result,
    verification?.stale,
    verification?.state,
  ]);

  const runAction = async (
    nextAction: Exclude<ActionState, "idle">,
    callback: () => Promise<void>,
  ) => {
    setAction(nextAction);
    setMessage("");
    try {
      await callback();
      if (nextAction === "discarding") {
        setConfirmDiscard(false);
      }
    } catch (error) {
      setMessage(describeReviewError(error));
    } finally {
      setAction("idle");
    }
  };

  const toggleDiff = async (file: CodingDraftFile) => {
    if (!sessionId || !changes) return;
    if (expandedPath === file.path) {
      setExpandedPath(null);
      return;
    }
    setExpandedPath(file.path);
    setMessage("");
    if (diffs[file.path]) return;
    setDiffLoading(file.path);
    try {
      const diff = await getCodingDiff(
        sessionId,
        file.path,
        changes.revision,
      );
      setDiffs((current) => ({ ...current, [file.path]: diff }));
    } catch (error) {
      setExpandedPath(null);
      setMessage(describeReviewError(error));
    } finally {
      setDiffLoading("");
    }
  };

  const hasChanges = Boolean(changes?.files.length);
  const completedWithoutChanges = Boolean(
    changes && changes.revision > 0 && !hasChanges,
  );
  const isActing = action !== "idle";
  const verificationRunning =
    verification?.state === "running" && verification.stale === false;
  const verificationSupportsDownload =
    verification?.revision === changes?.revision &&
    verification?.stale === false &&
    (verification?.result === "passed" ||
      verification?.result === "not_applicable");

  const requestDownload = () => {
    if (verificationSupportsDownload) {
      void runAction("downloading", onDownload);
      return;
    }
    setConfirmDownload(true);
  };

  return (
    <section className="overflow-hidden rounded-lg border border-white/10 bg-ink-950/72">
      <div className="flex flex-col gap-3 border-b border-white/10 px-4 py-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <FileDiff aria-hidden="true" className="text-cyan-200" size={18} />
            <h2 className="text-sm font-semibold text-white">本轮修改</h2>
          </div>
          <p className="mt-1.5 max-w-2xl text-xs leading-5 text-slate-400">
            {applyResult?.state === "reverted"
              ? "以下记录继续保留供你查看；专用项目副本已恢复，当前项目目录没有改变。"
              : applyResult?.apply_id
                ? "以下修改已写入专用项目副本，当前项目目录没有改变。你仍可展开文件逐行查看。"
                : hasChanges
                  ? "这些文件只存在于临时副本，当前项目目录没有改变。展开文件可以逐行查看增加和移除的内容。"
                  : completedWithoutChanges
                    ? "本轮没有待保存的新文件变化，此前保存的修改仍保留在下方记录中。"
                    : "代码助手产生的文件变化会显示在这里，当前项目目录不会被直接改变。"}
          </p>
        </div>
        {hasChanges ? (
          <div
            aria-label={`${changes?.file_count} 个文件，增加 ${changes?.additions} 行，移除 ${changes?.deletions} 行`}
            className="flex shrink-0 items-center gap-2 text-xs"
          >
            <span className="text-slate-300">{changes?.file_count} 个文件</span>
            <span className="font-semibold text-emerald-200">
              +{changes?.additions}
            </span>
            <span className="font-semibold text-rose-200">
              -{changes?.deletions}
            </span>
          </div>
        ) : null}
      </div>

      {loading ? (
        <div aria-label="正在读取修改草稿" className="space-y-3 p-4">
          <div className="h-4 w-2/3 animate-pulse rounded bg-white/10 motion-reduce:animate-none" />
          <div className="h-12 animate-pulse rounded-lg bg-white/[0.055] motion-reduce:animate-none" />
          <div className="h-12 animate-pulse rounded-lg bg-white/[0.055] motion-reduce:animate-none" />
        </div>
      ) : hasChanges && changes ? (
        <>
          <div className="divide-y divide-white/10">
            {changes.files.map((file) => {
              const isExpanded = expandedPath === file.path;
              const diff = diffs[file.path];
              return (
                <div key={file.path}>
                  <button
                    aria-expanded={isExpanded}
                    className="flex w-full items-center gap-3 px-4 py-3 text-left outline-none transition hover:bg-white/[0.04] focus-visible:bg-white/[0.06] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-cyan-300/70"
                    disabled={disabled || isActing}
                    onClick={() => void toggleDiff(file)}
                    type="button"
                  >
                    {file.status === "added" ? (
                      <FilePlus2
                        aria-hidden="true"
                        className="shrink-0 text-emerald-200"
                        size={17}
                      />
                    ) : (
                      <FileDiff
                        aria-hidden="true"
                        className="shrink-0 text-cyan-200"
                        size={17}
                      />
                    )}
                    <span className="min-w-0 flex-1">
                      <span className="block break-all text-sm font-medium text-slate-100">
                        {file.path}
                      </span>
                      <span className="mt-1 block text-xs text-slate-500">
                        {fileStatus(file)}，增加 {file.additions} 行，移除{" "}
                        {file.deletions} 行
                      </span>
                    </span>
                    {diffLoading === file.path ? (
                      <LoaderCircle
                        aria-label="正在读取文件修改"
                        className="shrink-0 animate-spin text-slate-400 motion-reduce:animate-none"
                        size={17}
                      />
                    ) : (
                      <ChevronDown
                        aria-hidden="true"
                        className={`shrink-0 text-slate-400 transition-transform ${
                          isExpanded ? "rotate-180" : ""
                        }`}
                        size={18}
                      />
                    )}
                  </button>
                  {isExpanded && diff ? (
                    <div className="border-t border-white/10 bg-black/25">
                      <div className="flex items-center justify-between px-4 py-2 text-xs text-slate-400">
                        <span>逐行修改</span>
                        <span>+ 表示增加，- 表示移除</span>
                      </div>
                      <pre
                        aria-label={`${file.path} 的修改内容`}
                        className="max-h-[420px] overflow-auto border-t border-white/10 py-2 font-mono text-xs leading-5"
                        tabIndex={0}
                      >
                        {diff.split("\n").map((line, index) => (
                          <code
                            className={`block min-w-max px-4 ${lineStyle(line)}`}
                            key={`${index}-${line}`}
                          >
                            {line || " "}
                          </code>
                        ))}
                      </pre>
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>

          <div className="border-t border-white/10 px-4 py-4">
            <div
              className={`flex items-start gap-2 text-sm ${
                changes.validation_status === "passed"
                  ? "text-emerald-100"
                  : "text-amber-100"
              }`}
            >
              {changes.validation_status === "passed" ? (
                <CheckCircle2 aria-hidden="true" className="mt-0.5" size={17} />
              ) : (
                <CircleAlert aria-hidden="true" className="mt-0.5" size={17} />
              )}
              <div>
                <p className="font-semibold">
                  {changes.validation_status === "passed"
                    ? "检查通过，可以下载 Diff"
                    : "检查发现问题，暂时不能下载"}
                </p>
                <p className="mt-1 text-xs leading-5 opacity-80">
                  {changes.validation_status === "passed"
                    ? "下载后仍需由你或开发者决定是否应用到项目。"
                    : "修改草稿会继续保留，可以请代码助手修正后再次检查。"}
                </p>
              </div>
            </div>

            <ul className="mt-3 space-y-2">
              {changes.checks.map((check) => (
                <li
                  className="flex items-start gap-2 text-xs leading-5"
                  key={check.id}
                >
                  {check.status === "passed" ? (
                    <Check
                      aria-hidden="true"
                      className="mt-0.5 shrink-0 text-emerald-200"
                      size={14}
                    />
                  ) : (
                    <X
                      aria-hidden="true"
                      className="mt-0.5 shrink-0 text-amber-200"
                      size={14}
                    />
                  )}
                  <span className="text-slate-300">
                    <span className="font-medium text-slate-200">
                      {check.label}：
                    </span>
                    {check.message}
                  </span>
                </li>
              ))}
            </ul>

            <CodingVerificationPanel
              available={verificationAvailable}
              disabled={
                disabled || readOnly || frozen || isActing || !changes.can_download
              }
              error={verificationError}
              onCancel={onCancelVerification}
              onRequestFix={onRequestFix}
              onRun={onRunVerification}
              verification={verification}
            />

            {commitResult?.state !== "committed" ? (
              <CodingApplyPanel
                capability={applyCapability}
                changes={changes}
                disabled={disabled || readOnly || isActing || verificationRunning}
                error={applyError}
                onApply={onApply}
                onClose={onClose}
                onRevert={onRevert}
                result={applyResult}
                verification={verification}
              />
            ) : null}

            <CodingCommitPanel
              applyResult={applyResult}
              capability={commitCapability}
              changes={changes}
              disabled={disabled || readOnly || isActing}
              error={commitError}
              onCommit={onCommit}
              onContinue={onContinue}
              onClose={onClose}
              onUndo={onUndoCommit}
              publishLocked={Boolean(publishResult?.publish_id)}
              result={commitResult}
            />

            <CodingPublishPanel
              capability={publishCapability}
              changes={changes}
              commit={commitResult}
              disabled={disabled || readOnly || isActing}
              error={publishError}
              onMarkReady={onMarkPublishReady}
              onPublish={onPublish}
              result={publishResult}
            />

            <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
              <button
                className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-white/15 px-3 text-sm font-semibold text-slate-200 transition hover:border-cyan-300/45 hover:bg-cyan-300/10 hover:text-cyan-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/70 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={disabled || readOnly || frozen || isActing}
                onClick={() =>
                  void runAction("checking", onValidate)
                }
                type="button"
              >
                {action === "checking" ? (
                  <LoaderCircle
                    aria-hidden="true"
                    className="animate-spin motion-reduce:animate-none"
                    size={16}
                  />
                ) : (
                  <RotateCcw aria-hidden="true" size={16} />
                )}
                检查修改
              </button>
              <button
                className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-cyan-300 px-3 text-sm font-semibold text-ink-950 transition hover:bg-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-100 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
                disabled={
                  disabled ||
                  isActing ||
                  !changes.can_download
                }
                onClick={requestDownload}
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
                className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg px-3 text-sm font-semibold text-rose-100 transition hover:bg-rose-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300/60 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={
                  disabled || readOnly || frozen || isActing || verificationRunning
                }
                onClick={() => setConfirmDiscard(true)}
                type="button"
              >
                <Trash2 aria-hidden="true" size={16} />
                放弃修改
              </button>
            </div>

            {confirmDownload ? (
              <div
                aria-live="polite"
                className="mt-4 rounded-lg bg-amber-300/10 p-3 text-sm text-amber-100"
              >
                <p className="font-semibold">项目验证尚未通过</p>
                <p className="mt-1 text-xs leading-5 text-amber-100/80">
                  你仍可下载当前 Diff，但建议先查看项目验证提示，并在应用前由开发者确认。
                </p>
                <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                  <button
                    className="min-h-9 rounded-lg border border-white/15 px-3 text-xs font-semibold transition hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50"
                    onClick={() => setConfirmDownload(false)}
                    type="button"
                  >
                    返回查看
                  </button>
                  <button
                    className="inline-flex min-h-9 items-center justify-center gap-2 rounded-lg bg-amber-200 px-3 text-xs font-semibold text-amber-950 transition hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-100"
                    onClick={() => {
                      setConfirmDownload(false);
                      void runAction("downloading", onDownload);
                    }}
                    type="button"
                  >
                    <Download aria-hidden="true" size={14} />
                    仍然下载
                  </button>
                </div>
              </div>
            ) : null}

            {confirmDiscard ? (
              <div
                aria-live="polite"
                className="mt-4 rounded-lg bg-rose-300/10 p-3 text-sm text-rose-100"
              >
                <p className="font-semibold">确定放弃全部修改吗？</p>
                <p className="mt-1 text-xs leading-5 text-rose-100/80">
                  草稿会恢复到最初状态，此操作不能撤销。
                </p>
                <div className="mt-3 flex flex-col gap-2 sm:flex-row">
                  <button
                    className="min-h-9 rounded-lg border border-white/15 px-3 text-xs font-semibold transition hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50"
                    onClick={() => setConfirmDiscard(false)}
                    type="button"
                  >
                    继续保留
                  </button>
                  <button
                    className="inline-flex min-h-9 items-center justify-center gap-2 rounded-lg bg-rose-200 px-3 text-xs font-semibold text-rose-950 transition hover:bg-rose-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-100"
                    onClick={() =>
                      void runAction("discarding", onDiscard)
                    }
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
          </div>
        </>
      ) : completedWithoutChanges && changes ? (
        <div className="px-4 py-5">
          <div className="flex items-start gap-3 rounded-lg bg-emerald-300/[0.07] p-3">
            <CheckCircle2
              aria-hidden="true"
              className="mt-0.5 shrink-0 text-emerald-200"
              size={18}
            />
            <div className="min-w-0">
              <p className="text-sm font-semibold text-emerald-100">
                本轮没有待保存的新修改
              </p>
              <p className="mt-1 text-xs leading-5 text-slate-300">
                代码助手本轮没有留下新的文件变化。若刚完成修复，说明文件已回到本轮开始时的状态。
              </p>
              <p className="mt-2 text-xs font-medium text-emerald-100">
                草稿检查已完成，无需应用或创建新的本地版本。
              </p>
            </div>
          </div>
          <CodingVerificationPanel
            available={verificationAvailable}
            disabled
            empty
            error={verificationError}
            onCancel={onCancelVerification}
            onRequestFix={onRequestFix}
            onRun={onRunVerification}
            verification={verification}
          />
        </div>
      ) : (
        <div className="px-4 py-8 text-center">
          <FileDiff aria-hidden="true" className="mx-auto text-slate-500" size={25} />
          <p className="mt-3 text-sm font-semibold text-slate-200">
            还没有修改草稿
          </p>
          <p className="mx-auto mt-2 max-w-lg text-xs leading-5 text-slate-500">
            可以直接描述想调整的页面或功能。回答完成后，修改过的文件会出现在这里，供你逐项查看。
          </p>
        </div>
      )}

      {message ? (
        <div
          aria-live="assertive"
          className="flex items-start gap-2 border-t border-white/10 bg-rose-300/10 px-4 py-3 text-sm text-rose-100"
          role="alert"
        >
          <CircleAlert aria-hidden="true" className="mt-0.5 shrink-0" size={16} />
          {message}
        </div>
      ) : null}
    </section>
  );
}
