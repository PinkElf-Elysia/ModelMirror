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
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import type {
  CodingDraftChanges,
  CodingDraftFile,
} from "../types/coding";
import { CodingApiError, getCodingDiff } from "../utils/codingApi";

interface CodingChangesPanelProps {
  changes: CodingDraftChanges | null;
  disabled: boolean;
  loading: boolean;
  onDiscard: () => Promise<void>;
  onDownload: () => Promise<void>;
  onValidate: () => Promise<void>;
  sessionId: string | null;
}

type ActionState = "idle" | "checking" | "downloading" | "discarding";

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

export default function CodingChangesPanel({
  changes,
  disabled,
  loading,
  onDiscard,
  onDownload,
  onValidate,
  sessionId,
}: CodingChangesPanelProps) {
  const [action, setAction] = useState<ActionState>("idle");
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [expandedPath, setExpandedPath] = useState<string | null>(null);
  const [diffs, setDiffs] = useState<Record<string, string>>({});
  const [diffLoading, setDiffLoading] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    setExpandedPath(null);
    setDiffs({});
    setConfirmDiscard(false);
    setMessage("");
  }, [changes?.revision, sessionId]);

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
  const isActing = action !== "idle";

  return (
    <section className="overflow-hidden rounded-lg border border-white/10 bg-ink-950/72">
      <div className="flex flex-col gap-3 border-b border-white/10 px-4 py-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <FileDiff aria-hidden="true" className="text-cyan-200" size={18} />
            <h2 className="text-sm font-semibold text-white">本轮修改</h2>
          </div>
          <p className="mt-1.5 max-w-2xl text-xs leading-5 text-slate-400">
            这些文件只存在于临时副本，真实项目没有改变。展开文件可以逐行查看增加和移除的内容。
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
          <div className="h-4 w-2/3 animate-pulse rounded bg-white/10" />
          <div className="h-12 animate-pulse rounded-lg bg-white/[0.055]" />
          <div className="h-12 animate-pulse rounded-lg bg-white/[0.055]" />
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
                        className="shrink-0 animate-spin text-slate-400"
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

            <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
              <button
                className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-white/15 px-3 text-sm font-semibold text-slate-200 transition hover:border-cyan-300/45 hover:bg-cyan-300/10 hover:text-cyan-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/70 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={disabled || isActing}
                onClick={() =>
                  void runAction("checking", onValidate)
                }
                type="button"
              >
                {action === "checking" ? (
                  <LoaderCircle
                    aria-hidden="true"
                    className="animate-spin"
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
                onClick={() =>
                  void runAction("downloading", onDownload)
                }
                type="button"
              >
                {action === "downloading" ? (
                  <LoaderCircle
                    aria-hidden="true"
                    className="animate-spin"
                    size={16}
                  />
                ) : (
                  <Download aria-hidden="true" size={16} />
                )}
                下载 Diff
              </button>
              <button
                className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg px-3 text-sm font-semibold text-rose-100 transition hover:bg-rose-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-rose-300/60 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={disabled || isActing}
                onClick={() => setConfirmDiscard(true)}
                type="button"
              >
                <Trash2 aria-hidden="true" size={16} />
                放弃修改
              </button>
            </div>

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
                        className="animate-spin"
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
