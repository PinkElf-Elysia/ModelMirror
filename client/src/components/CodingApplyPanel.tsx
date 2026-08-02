import {
  CheckCircle2,
  CircleAlert,
  FolderCheck,
  LoaderCircle,
  Undo2,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import type {
  CodingApplyResult,
  CodingCapabilities,
  CodingDraftChanges,
  CodingVerification,
} from "../types/coding";
import { CodingApiError } from "../utils/codingApi";

interface CodingApplyPanelProps {
  capability: CodingCapabilities["apply"];
  changes: CodingDraftChanges;
  disabled: boolean;
  error: string;
  onApply: (confirmQualityRisks: boolean) => Promise<void>;
  onClose: () => Promise<void>;
  onRevert: () => Promise<void>;
  result: CodingApplyResult | null;
  verification: CodingVerification | null;
}

type ActionState = "idle" | "applying" | "reverting" | "closing";

const reasonText: Record<string, string> = {
  applier_not_configured:
    "尚未设置专用项目副本，仍可查看和下载当前修改。",
  applier_unavailable:
    "本地应用服务未启动，仍可查看和下载当前修改。",
  applier_timeout: "写入等待时间过长，未能确认结果，请稍后查看状态。",
  apply_in_progress: "正在写入修改，请等待当前操作完成。",
  dependency_change_unsupported:
    "这次修改涉及项目运行所需的组件清单，当前不能安全应用。",
  revert_conflict:
    "专用项目副本在应用后又发生了变化，为避免覆盖人工内容，本次撤销已停止。",
  rollback_failed:
    "自动恢复没有完成，请停止操作并由开发者检查专用项目副本。",
  session_frozen: "修改已经写入，本次草稿已锁定。",
  snapshot_mismatch:
    "专用项目副本与当前草稿版本不一致，请由开发者重新创建副本。",
  target_changed:
    "专用项目副本已有其他修改，为避免覆盖内容，本次操作已停止。",
  target_not_ready:
    "专用项目副本不是干净的初始状态，请由开发者检查或重新创建。",
  unsafe_workspace_root:
    "专用项目副本未通过安全检查，请由开发者重新创建。",
  validation_failed: "常见问题检查尚未通过，暂时不能应用。",
  verification_cancelled: "项目验证已停止，请重新运行并等待通过。",
  verification_failed: "项目验证发现问题，修正并重新通过后才能应用。",
  verification_in_progress: "项目验证仍在运行，请等待完成。",
  verification_required: "请先运行项目验证并等待通过。",
  verification_stale: "草稿已更新，请重新运行项目验证。",
};

const hardRetryFailures = new Set(["rollback_failed", "recovery_conflict"]);

function describeError(error: unknown) {
  if (error instanceof CodingApiError) {
    return reasonText[error.code] ?? "操作未完成，请稍后重试。";
  }
  return "操作未完成，请稍后重试。";
}

function gateMessage(
  capability: CodingCapabilities["apply"],
  changes: CodingDraftChanges,
  verification: CodingVerification | null,
) {
  if (!capability?.available) {
    return (
      reasonText[capability?.reason ?? ""] ??
      "本地应用功能暂时不可用，仍可查看和下载修改。"
    );
  }
  if (verification?.state === "running" && verification.stale === false) {
    return "项目验证正在运行，可以停止或等待完成后再应用。";
  }
  const qualityWarnings: string[] = [];
  if (!changes.can_download || changes.validation_status !== "passed") {
    qualityWarnings.push("常见问题检查发现了内容");
  }
  if (
    !verification ||
    verification.revision !== changes.revision ||
    verification.stale
  ) {
    qualityWarnings.push("当前草稿尚未完成项目验证");
  } else if (
    verification.result !== "passed" &&
    !(
      verification.result === "not_applicable" &&
      verification.reason === "documentation_only"
    )
  ) {
    qualityWarnings.push(
      verification.result === "failed"
        ? "项目验证发现了问题"
        : "当前项目验证没有得出通过结果",
    );
  }
  if (qualityWarnings.length) {
    return `${qualityWarnings.join("，")}。你可以先修正，也可以确认风险后继续写入专用副本。`;
  }
  return "检查结果正常，可以写入专用项目副本。";
}

export default function CodingApplyPanel({
  capability,
  changes,
  disabled,
  error,
  onApply,
  onClose,
  onRevert,
  result,
  verification,
}: CodingApplyPanelProps) {
  const [action, setAction] = useState<ActionState>("idle");
  const [confirmApply, setConfirmApply] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    setConfirmApply(false);
    setConfirmClose(false);
    setMessage("");
  }, [changes.revision]);

  const runAction = async (
    nextAction: Exclude<ActionState, "idle">,
    callback: () => Promise<void>,
  ) => {
    setAction(nextAction);
    setMessage("");
    try {
      await callback();
    } catch (requestError) {
      setMessage(describeError(requestError));
    } finally {
      setAction("idle");
    }
  };

  const hasAppliedCopy = Boolean(result?.apply_id);
  const failedAttempt = result?.state === "failed" && !hasAppliedCopy;
  const hardRetryFailure = Boolean(
    failedAttempt && hardRetryFailures.has(result?.reason ?? ""),
  );
  const verificationAllowsApply =
    verification?.revision === changes.revision &&
    verification.stale === false &&
    verification.state === "completed" &&
    (verification.result === "passed" ||
      (verification.result === "not_applicable" &&
        verification.reason === "documentation_only"));
  const hasQualityRisks =
    changes.validation_status !== "passed" ||
    !changes.can_download ||
    !verificationAllowsApply;
  const verificationRunning =
    verification?.state === "running" && verification.stale === false;
  const canApply =
    !hardRetryFailure &&
    capability?.available === true &&
    !verificationRunning;
  const gateCopy = hardRetryFailure
    ? reasonText[result?.reason ?? ""] ??
      "上次操作的结果无法确认，请由开发者检查专用项目副本。"
    : failedAttempt
      ? `${reasonText[result?.reason ?? ""] ?? "上次写入没有完成。"} 你可以在问题处理后重试当前草稿。`
      : gateMessage(capability, changes, verification);
  const isApplied = result?.state === "applied";
  const isReverted = result?.state === "reverted";
  const failedAfterApply = result?.state === "failed" && hasAppliedCopy;
  const technicalReason =
    result?.reason || capability?.reason || (message ? "request_failed" : "");

  if (isApplied || isReverted || failedAfterApply) {
    return (
      <section
        aria-labelledby="coding-apply-title"
        className={`mt-5 rounded-lg border p-4 ${
          isReverted
            ? "border-slate-300/15 bg-white/[0.025]"
            : failedAfterApply
              ? "border-amber-300/20 bg-amber-300/[0.055]"
              : "border-emerald-300/20 bg-emerald-300/[0.055]"
        }`}
      >
        <div className="flex items-start gap-3">
          {failedAfterApply ? (
            <CircleAlert
              aria-hidden="true"
              className="mt-0.5 shrink-0 text-amber-200"
              size={18}
            />
          ) : (
            <CheckCircle2
              aria-hidden="true"
              className={`mt-0.5 shrink-0 ${
                isReverted ? "text-slate-300" : "text-emerald-200"
              }`}
              size={18}
            />
          )}
          <div className="min-w-0">
            <h3 className="text-sm font-semibold text-white" id="coding-apply-title">
              {isReverted
                ? "本次应用已撤销"
                : failedAfterApply
                  ? "无法安全撤销"
                  : "修改已应用"}
            </h3>
            <p aria-live="polite" className="mt-1 text-xs leading-5 text-slate-300">
              {isReverted
                ? "专用项目副本已恢复到应用前的状态。当前项目目录始终没有改变。"
                : failedAfterApply
                  ? reasonText[result?.reason ?? ""] ??
                    "专用项目副本发生了其他变化，本次操作已停止。"
                  : `已将 ${result?.file_count ?? changes.file_count} 个文件写入专用项目副本。没有提交，也没有上传；当前项目目录没有改变。`}
            </p>
          </div>
        </div>

        <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          {isApplied && result?.can_revert ? (
            <button
              className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-amber-300/30 px-3 text-sm font-semibold text-amber-100 transition hover:bg-amber-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-200/70 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={disabled || action !== "idle"}
              onClick={() => void runAction("reverting", onRevert)}
              type="button"
            >
              {action === "reverting" ? (
                <LoaderCircle
                  aria-hidden="true"
                  className="animate-spin motion-reduce:animate-none"
                  size={16}
                />
              ) : (
                <Undo2 aria-hidden="true" size={16} />
              )}
              撤销本次应用
            </button>
          ) : null}
          <button
            className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg border border-white/15 px-3 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={disabled || action !== "idle"}
            onClick={() => setConfirmClose(true)}
            type="button"
          >
            <X aria-hidden="true" size={16} />
            结束本次修改
          </button>
        </div>

        {confirmClose ? (
          <div
            aria-live="polite"
            className="mt-4 rounded-lg bg-white/[0.045] p-3 text-sm text-slate-200"
          >
            <p className="font-semibold">确定结束本次修改吗？</p>
            <p className="mt-1 text-xs leading-5 text-slate-400">
              {isReverted
                ? "结束后会释放代码助手，你可以开始新的修改。"
                : "结束后页面将不能再撤销；专用项目副本中的内容会继续保留。"}
            </p>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <button
                className="min-h-9 rounded-lg border border-white/15 px-3 text-xs font-semibold hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50"
                onClick={() => setConfirmClose(false)}
                type="button"
              >
                继续保留本页
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

        {message || error ? (
          <div
            className="mt-3 flex items-start gap-2 rounded-lg bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-100"
            role="alert"
          >
            <CircleAlert
              aria-hidden="true"
              className="mt-0.5 shrink-0"
              size={14}
            />
            {message || error}
          </div>
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

  return (
    <section
      aria-labelledby="coding-apply-title"
      className="mt-5 rounded-lg border border-white/10 bg-white/[0.025] p-4"
    >
      <div className="flex items-start gap-3">
        {canApply && !hasQualityRisks ? (
          <FolderCheck
            aria-hidden="true"
            className="mt-0.5 shrink-0 text-emerald-200"
            size={18}
          />
        ) : (
          <CircleAlert
            aria-hidden="true"
            className={`mt-0.5 shrink-0 ${
              canApply ? "text-amber-200" : "text-slate-400"
            }`}
            size={18}
          />
        )}
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-white" id="coding-apply-title">
            应用到本地项目副本
          </h3>
          <p
            aria-live="polite"
            className={`mt-1 text-xs leading-5 ${
              canApply && !hasQualityRisks
                ? "text-emerald-100/85"
                : canApply
                  ? "text-amber-100/85"
                  : "text-slate-400"
            }`}
          >
            {gateCopy}
          </p>
        </div>
      </div>

      <button
        className="mt-4 inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-lg bg-emerald-200 px-3 text-sm font-semibold text-emerald-950 transition hover:bg-emerald-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-100 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400 sm:w-auto"
        disabled={!canApply || disabled || action !== "idle"}
        onClick={() => setConfirmApply(true)}
        type="button"
      >
        <FolderCheck aria-hidden="true" size={16} />
        应用到本地项目副本
      </button>

      {confirmApply ? (
        <div
          aria-live="polite"
          className={`mt-4 rounded-lg border p-3 text-sm ${
            hasQualityRisks
              ? "border-amber-300/20 bg-amber-300/[0.07] text-amber-50"
              : "border-emerald-300/15 bg-emerald-300/[0.065] text-emerald-50"
          }`}
        >
          <p className="font-semibold">
            {hasQualityRisks
              ? `仍要写入 ${changes.file_count} 个文件吗？`
              : `确认写入 ${changes.file_count} 个文件吗？`}
          </p>
          {hasQualityRisks ? (
            <p className="mt-2 text-xs leading-5 text-amber-100/85">
              检查结果未全部通过，写入后可能需要继续修正。文件与仓库安全边界仍会再次检查。
            </p>
          ) : null}
          <ul
            className={`mt-2 space-y-1 text-xs leading-5 ${
              hasQualityRisks ? "text-amber-100/80" : "text-emerald-100/80"
            }`}
          >
            <li>修改会写入预先准备的专用项目副本。</li>
            <li>不会提交，也不会上传到远程平台。</li>
            <li>你当前使用的项目目录不会改变。</li>
          </ul>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <button
              className="min-h-9 rounded-lg border border-white/15 px-3 text-xs font-semibold hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50"
              onClick={() => setConfirmApply(false)}
              type="button"
            >
              返回检查
            </button>
            <button
              className={`inline-flex min-h-9 items-center justify-center gap-2 rounded-lg px-3 text-xs font-semibold focus-visible:outline-none focus-visible:ring-2 ${
                hasQualityRisks
                  ? "bg-amber-200 text-amber-950 hover:bg-amber-100 focus-visible:ring-amber-100"
                  : "bg-emerald-200 text-emerald-950 hover:bg-emerald-100 focus-visible:ring-emerald-100"
              }`}
              onClick={() =>
                void runAction("applying", () => onApply(hasQualityRisks))
              }
              type="button"
            >
              {action === "applying" ? (
                <LoaderCircle
                  aria-hidden="true"
                  className="animate-spin motion-reduce:animate-none"
                  size={14}
                />
              ) : null}
              {hasQualityRisks ? "了解风险并应用" : "确认应用"}
            </button>
          </div>
        </div>
      ) : null}

      {message || error ? (
        <div
          className="mt-3 flex items-start gap-2 rounded-lg bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-100"
          role="alert"
        >
          <CircleAlert
            aria-hidden="true"
            className="mt-0.5 shrink-0"
            size={14}
          />
          {message || error}
        </div>
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
