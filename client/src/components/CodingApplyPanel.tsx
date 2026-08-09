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
  CodingProjectSummary,
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
  selectedProject: CodingProjectSummary | null;
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
  git_remote_not_allowed:
    "所选项目仍连接远程平台。移除远程连接并确认项目状态后再试。",
  project_changed:
    "所选项目的版本已经变化。为避免覆盖现有内容，本次写入已停止。",
  project_host_offline:
    "本地项目助手连接已断开。当前状态和操作区会保留，重新连接后可继续。",
  project_host_protocol_readonly:
    "当前助手版本只支持查看和下载。请更新助手后重新连接，再决定是否写入。",
  project_host_unavailable:
    "本地项目助手连接已断开。当前状态和操作区会保留，重新连接后可继续。",
  project_host_writeback_disabled:
    "本地项目写入已关闭。当前修改仍可查看和下载。",
  project_host_writeback_unavailable:
    "本地项目助手暂时不能执行写入。当前状态和操作区会保留，重新连接后可继续。",
  project_writer_not_configured:
    "本地项目写入服务尚未配置，仍可查看和下载当前修改。",
  project_writer_timeout:
    "写入等待时间过长，结果暂时无法确认。请保留当前页面并稍后查看状态。",
  project_writer_unavailable:
    "本地项目写入服务未启动，仍可查看和下载当前修改。",
  revert_conflict:
    "目标项目在写入后又发生了变化，为避免覆盖人工内容，本次撤销已停止。",
  rollback_failed:
    "自动恢复没有完成，请停止操作并由开发者检查专用项目副本。",
  session_frozen: "修改已经写入，本次草稿已锁定。",
  snapshot_mismatch:
    "目标项目与当前草稿版本不一致，请刷新状态或重新开始修改。",
  operation_result_unknown:
    "本次操作的结果暂时无法确认。当前状态和操作区会保留，重新连接后将继续核对。",
  target_changed:
    "目标项目已有其他修改，为避免覆盖内容，本次操作已停止。",
  target_not_ready:
    "目标项目不是干净的初始状态，请先处理已有修改后再试。",
  unsafe_workspace_root:
    "专用项目副本未通过安全检查，请由开发者重新创建。",
  validation_failed: "常见问题检查尚未通过，暂时不能应用。",
  verification_cancelled: "项目验证已停止，请重新运行并等待通过。",
  verification_failed: "项目验证发现问题，修正并重新通过后才能应用。",
  verification_in_progress: "项目验证仍在运行，请等待完成。",
  verification_required: "请先运行项目验证并等待通过。",
  verification_stale: "草稿已更新，请重新运行项目验证。",
  writeback_branch_required:
    "所选项目不在允许写入的本地分支，请由开发者切换后再试。",
  writeback_not_enabled:
    "所选项目没有开放本地写入，仍可查看和下载当前修改。",
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
  selectedProject: CodingProjectSummary | null,
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
    return `${qualityWarnings.join("，")}。你可以先修正，也可以确认风险后继续写入${Boolean(selectedProject) ? "所选本地项目" : "专用副本"}。`;
  }
  return `检查结果正常，可以写入${Boolean(selectedProject) ? "所选本地项目" : "专用项目副本"}。`;
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
  selectedProject,
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
    if (action !== "idle") return;
    setAction(nextAction);
    setMessage("");
    try {
      await callback();
      setConfirmApply(false);
      setConfirmClose(false);
    } catch (requestError) {
      setMessage(describeError(requestError));
    } finally {
      setAction("idle");
    }
  };

  const hasAppliedCopy = Boolean(result?.apply_id);
  const failedAttempt = result?.state === "failed" && !hasAppliedCopy;
  const operationResultUnknown =
    result?.state === "failed" &&
    result.reason === "operation_result_unknown";
  const applyResultUnknown = operationResultUnknown && !hasAppliedCopy;
  const revertResultUnknown = operationResultUnknown && hasAppliedCopy;
  const hardRetryFailure = Boolean(
    failedAttempt && hardRetryFailures.has(result?.reason ?? ""),
  );
  const operationPending =
    result?.state === "applying" || result?.state === "reverting";
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
    !operationPending &&
    capability?.available === true &&
    !verificationRunning;
  const gateCopy = operationPending
    ? result?.state === "reverting"
      ? "正在核对撤销结果，请等待状态更新；当前页面和 Diff 会继续保留。"
      : "正在核对写入结果，请等待状态更新；当前页面和 Diff 会继续保留。"
    : hardRetryFailure
    ? reasonText[result?.reason ?? ""] ??
      `上次操作的结果无法确认，请由开发者检查${Boolean(selectedProject) ? "所选本地项目" : "专用项目副本"}。`
    : applyResultUnknown
      ? "上次结果暂时无法确认。重新连接后可核对原操作；只有确认尚未写入时，系统才会继续本次写入。"
    : failedAttempt
      ? `${reasonText[result?.reason ?? ""] ?? "上次写入没有完成。"} 你可以在问题处理后重试当前草稿。`
      : gateMessage(capability, changes, verification, selectedProject);
  const isApplied = result?.state === "applied";
  const isReverted = result?.state === "reverted";
  const failedAfterApply =
    result?.state === "failed" && hasAppliedCopy && !revertResultUnknown;
  const technicalReason =
    result?.reason || capability?.reason || (message ? "request_failed" : "");

  if (isApplied || isReverted || failedAfterApply || revertResultUnknown) {
    return (
      <section
        aria-busy={action !== "idle"}
        aria-labelledby="coding-apply-title"
        className={`mt-5 rounded-lg border p-4 ${
          isReverted
            ? "border-slate-300/15 bg-white/[0.025]"
            : failedAfterApply || revertResultUnknown
              ? "border-amber-300/20 bg-amber-300/[0.055]"
              : "border-emerald-300/20 bg-emerald-300/[0.055]"
        }`}
      >
        <div className="flex items-start gap-3">
          {failedAfterApply || revertResultUnknown ? (
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
                ? Boolean(selectedProject)
                  ? "本次写入已撤销"
                  : "本次应用已撤销"
                : revertResultUnknown
                  ? "撤销结果待核对"
                : failedAfterApply
                  ? "无法安全撤销"
                  : Boolean(selectedProject)
                    ? "修改已写入"
                    : "修改已应用"}
            </h3>
            <p
              aria-atomic="true"
              aria-live="polite"
              className="mt-1 text-xs leading-5 text-slate-300"
              role="status"
            >
              {isReverted
                ? Boolean(selectedProject)
                  ? "所选本地项目已恢复到写入前的状态。"
                  : "专用项目副本已恢复到应用前的状态。当前项目目录始终没有改变。"
                : revertResultUnknown
                  ? "上次撤销结果暂时无法确认。重新连接后可核对原操作；系统不会盲目重复撤销。"
                : failedAfterApply
                  ? reasonText[result?.reason ?? ""] ??
                    `${Boolean(selectedProject) ? "所选本地项目" : "专用项目副本"}发生了其他变化，本次操作已停止。`
                  : Boolean(selectedProject)
                    ? `已将 ${result?.file_count ?? changes.file_count} 个文件写入所选本地项目。尚未创建本地版本，也没有上传。`
                    : `已将 ${result?.file_count ?? changes.file_count} 个文件写入专用项目副本。没有提交，也没有上传；当前项目目录没有改变。`}
            </p>
          </div>
        </div>

        <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          {(isApplied && result?.can_revert) || revertResultUnknown ? (
            <button
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-amber-300/30 px-3 text-sm font-semibold text-amber-100 transition hover:bg-amber-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-200/70 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={
                disabled || action !== "idle" || capability?.available !== true
              }
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
              {revertResultUnknown
                ? "核对本次撤销"
                : Boolean(selectedProject)
                  ? "撤销本次写入"
                  : "撤销本次应用"}
            </button>
          ) : null}
          {!revertResultUnknown ? (
            <button
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-white/15 px-3 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={disabled || action !== "idle"}
              onClick={() => setConfirmClose(true)}
              type="button"
            >
              <X aria-hidden="true" size={16} />
              结束本次修改
            </button>
          ) : null}
        </div>

        {confirmClose && !revertResultUnknown ? (
          <div
            aria-live="polite"
            className="mt-4 rounded-lg bg-white/[0.045] p-3 text-sm text-slate-200"
          >
            <p className="font-semibold">确定结束本次修改吗？</p>
            <p className="mt-1 text-xs leading-5 text-slate-400">
              {isReverted
                ? "结束后会释放代码助手，你可以开始新的修改。"
                : Boolean(selectedProject)
                  ? "结束后页面将不能再撤销；所选本地项目中的修改会继续保留。"
                  : "结束后页面将不能再撤销；专用项目副本中的内容会继续保留。"}
            </p>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <button
                className="min-h-11 rounded-lg border border-white/15 px-3 text-xs font-semibold hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={action !== "idle"}
                onClick={() => setConfirmClose(false)}
                type="button"
              >
                继续保留本页
              </button>
              <button
                className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-slate-200 px-3 text-xs font-semibold text-ink-950 hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white disabled:cursor-not-allowed disabled:opacity-50"
                disabled={action !== "idle"}
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

        {(message || error) && !operationResultUnknown ? (
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
      aria-busy={operationPending || action !== "idle"}
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
            {Boolean(selectedProject)
              ? "写入所选本地项目"
              : "应用到本地项目副本"}
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
        className="mt-4 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-emerald-200 px-3 text-sm font-semibold text-emerald-950 transition hover:bg-emerald-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-100 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400 sm:w-auto"
        disabled={!canApply || disabled || action !== "idle"}
        onClick={() => setConfirmApply(true)}
        type="button"
      >
        <FolderCheck aria-hidden="true" size={16} />
        {applyResultUnknown
          ? Boolean(selectedProject)
            ? "核对本次写入"
            : "核对本次应用"
          : Boolean(selectedProject)
            ? "写入所选本地项目"
            : "应用到本地项目副本"}
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
            {applyResultUnknown
              ? "核对上一次写入结果吗？"
              : hasQualityRisks
              ? `仍要写入 ${changes.file_count} 个文件吗？`
              : `确认写入 ${changes.file_count} 个文件吗？`}
          </p>
          {Boolean(selectedProject) ? (
            <dl className="mt-3 grid min-w-0 gap-2 rounded-lg border border-white/10 bg-black/15 p-3 text-xs sm:grid-cols-[4rem_minmax(0,1fr)]">
              <dt className="text-slate-400">项目</dt>
              <dd className="min-w-0 break-all font-semibold text-white">
                {selectedProject?.name}
              </dd>
              <dt className="text-slate-400">当前分支</dt>
              <dd className="min-w-0">
                <code className="block break-all text-cyan-100">
                  {selectedProject?.branch ?? "暂时无法确认"}
                </code>
              </dd>
            </dl>
          ) : null}
          {applyResultUnknown ? (
            <p className="mt-2 text-xs leading-5 text-cyan-100/85">
              系统会先核对原操作；只有明确确认尚未写入时，才会继续同一次写入，不会盲目重复修改项目。
            </p>
          ) : null}
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
            <li>
              {Boolean(selectedProject)
                ? "修改会直接写入上方确认的本地项目，不会上传到远程平台。"
                : "修改会写入预先准备的专用项目副本。"}
            </li>
            <li>
              {Boolean(selectedProject)
                ? "本次只写入文件，不会自动创建本地版本。"
                : "不会自动创建本地版本，也不会上传到远程平台。"}
            </li>
            <li>
              {Boolean(selectedProject)
                ? "写入前会再次确认项目版本和现有文件，发现外部改动会立即停止。"
                : "你当前使用的项目目录不会改变。"}
            </li>
          </ul>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <button
              className="min-h-11 rounded-lg border border-white/15 px-3 text-xs font-semibold hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={action !== "idle"}
              onClick={() => setConfirmApply(false)}
              type="button"
            >
              返回检查
            </button>
            <button
              className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-lg px-3 text-xs font-semibold focus-visible:outline-none focus-visible:ring-2 disabled:cursor-not-allowed disabled:opacity-50 ${
                hasQualityRisks
                  ? "bg-amber-200 text-amber-950 hover:bg-amber-100 focus-visible:ring-amber-100"
                  : "bg-emerald-200 text-emerald-950 hover:bg-emerald-100 focus-visible:ring-emerald-100"
              }`}
              disabled={action !== "idle" || !canApply}
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
              {applyResultUnknown
                ? "核对并继续本次写入"
                : hasQualityRisks
                ? Boolean(selectedProject)
                  ? "了解风险并写入"
                  : "了解风险并应用"
                : Boolean(selectedProject)
                  ? "确认写入"
                  : "确认应用"}
            </button>
          </div>
        </div>
      ) : null}

      {(message || error) && !operationResultUnknown ? (
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
