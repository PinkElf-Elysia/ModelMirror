import {
  Check,
  CheckCircle2,
  CircleAlert,
  ExternalLink,
  GitPullRequest,
  LoaderCircle,
  Send,
} from "lucide-react";
import { useEffect, useState } from "react";
import type {
  CodingCapabilities,
  CodingCommitResult,
  CodingDraftChanges,
  CodingPublishResult,
} from "../types/coding";
import { CodingApiError } from "../utils/codingApi";

interface CodingPublishPanelProps {
  capability: CodingCapabilities["publish"];
  changes: CodingDraftChanges;
  commit: CodingCommitResult | null;
  disabled: boolean;
  error: string;
  onMarkReady: () => Promise<void>;
  onPublish: (title: string, body: string) => Promise<void>;
  result: CodingPublishResult | null;
}

type PublishAction = "idle" | "publishing" | "marking_ready";

const publishReasonText: Record<string, string> = {
  base_branch_changed:
    "GitHub 项目已更新，这份修改不再基于最新版本。请重新开始一份修改。",
  credential_rejected:
    "GitHub 授权未通过，请由开发者检查应用配置后再试。",
  installation_not_found:
    "GitHub 应用没有安装到约定项目，请由开发者检查配置。",
  invalid_response:
    "GitHub 返回了无法安全确认的结果，本次发布已停止。",
  permission_denied:
    "GitHub 应用缺少发布所需权限，请由开发者检查配置。",
  publish_already_started:
    "这份修改已经开始发布，标题和说明不能再更换。请查看当前状态。",
  publish_manifest_invalid:
    "这份本地版本不满足发布要求，请由开发者检查文件和版本记录。",
  publisher_internal_error:
    "发布服务未能完成操作，请稍后按原内容重试。",
  publisher_not_configured:
    "尚未配置 GitHub 发布服务。本地修改、检查和保存仍可正常使用。",
  publisher_timeout:
    "等待 GitHub 的时间过长，结果暂时无法确认。请稍后查看状态。",
  publisher_unavailable:
    "GitHub 发布服务未启动。本地修改、检查和保存仍可正常使用。",
  publish_incomplete:
    "本地版本可能已经上传，但草稿 PR 尚未确认。按原内容重试会继续核对，不会重复上传。",
  publish_not_completed:
    "上次发布没有写入远程内容，可以按原内容安全重试。",
  recovery_storage_unavailable:
    "发布结果暂时无法安全保存，请稍后按原内容重试。",
  remote_branch_conflict:
    "远程发布位置已被其他内容占用。为避免覆盖，系统没有强制上传。",
  remote_pr_conflict:
    "这份 PR 已在 GitHub 上发生变化。为避免覆盖，请由开发者在 GitHub 检查。",
  repository_mismatch:
    "GitHub 应用连接的不是约定项目，本次发布已停止。",
  repository_not_ready:
    "本地项目副本暂时无法完成发布前检查，请稍后按原内容重试。",
  unsafe_repository:
    "本地项目副本未通过发布前检查，请由开发者重新准备。",
  workflow_change_unsupported:
    "这份修改包含 GitHub 自动化流程文件，当前版本不允许发布。",
};

function describePublishError(error: unknown) {
  if (error instanceof CodingApiError) {
    return (
      publishReasonText[error.code] ??
      "发布没有完成，本地版本仍安全保留，请稍后重试。"
    );
  }
  return "发布没有完成，本地版本仍安全保留，请稍后重试。";
}

const unsafeControlCharacters = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;
const likelySecretPatterns = [
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/i,
  /\bgh[pousr]_[A-Za-z0-9_]{20,}\b/,
  /\bsk-[A-Za-z0-9_-]{20,}\b/,
  /\b(?:api[_-]?key|token|secret)\s*[:=]\s*["']?[A-Za-z0-9_./+=-]{16,}/i,
];
const publishSteps = ["检查远程项目", "上传本地版本", "创建草稿 PR"];

function containsLikelySecret(value: string) {
  return likelySecretPatterns.some((pattern) => pattern.test(value));
}

function statusCopy(result: CodingPublishResult | null) {
  if (!result) return "";
  if (result.state === "publishing") return "正在创建草稿 PR";
  if (result.state === "marking_ready") return "正在标记为可审阅";
  if (result.state === "draft") return "草稿 PR 已创建";
  if (result.state === "ready") return "PR 已标记为可审阅";
  if (result.state === "conflict") return "远程内容发生冲突";
  if (result.state === "failed") return "本次操作未完成";
  return "尚未发布";
}

export default function CodingPublishPanel({
  capability,
  changes,
  commit,
  disabled,
  error,
  onMarkReady,
  onPublish,
  result,
}: CodingPublishPanelProps) {
  const [action, setAction] = useState<PublishAction>("idle");
  const [body, setBody] = useState("");
  const [confirmPublish, setConfirmPublish] = useState(false);
  const [confirmReady, setConfirmReady] = useState(false);
  const [localError, setLocalError] = useState("");
  const [title, setTitle] = useState("");

  const commitId = commit?.commit_id;
  useEffect(() => {
    const suggestedTitle = commit?.message?.split("\n", 1)[0]?.trim() ?? "";
    setTitle(result?.title || suggestedTitle);
    setBody(
      result?.body ||
        `包含 ${changes.file_count} 个文件的修改，已完成本地检查和项目验证。`,
    );
    setConfirmPublish(false);
    setConfirmReady(false);
    setLocalError("");
  }, [changes.file_count, commitId, commit?.message, result?.publish_id]);

  if (commit?.state !== "committed" || !commit.commit_id) return null;

  const normalizedTitle = title.trim();
  const normalizedBody = body.trim();
  const textHasSecret = containsLikelySecret(`${normalizedTitle}\n${normalizedBody}`);
  const textHasControl = unsafeControlCharacters.test(`${title}\n${body}`);
  const titleValid = normalizedTitle.length > 0 && normalizedTitle.length <= 120;
  const bodyValid = normalizedBody.length <= 10_000;
  const textValid = titleValid && bodyValid && !textHasSecret && !textHasControl;
  const waiting =
    action !== "idle" ||
    result?.state === "publishing" ||
    result?.state === "marking_ready";
  const hasRemotePr = Boolean(result?.pr_number && result.pr_url);
  const published = result?.state === "draft" || result?.state === "ready";
  const isConflict = result?.state === "conflict";
  const canRetryPublish = result?.state === "failed" && !hasRemotePr;
  const canMarkReady =
    result?.state === "draft" ||
    (result?.state === "failed" && result.can_mark_ready);
  const reason = result?.reason || capability?.reason || "";
  const reasonCopy =
    publishReasonText[reason] ??
    (reason ? "远程操作未能安全完成，本地版本没有改变。" : "");
  const technicalReason = reason || (localError || error ? "request_failed" : "");

  const runAction = async (
    nextAction: Exclude<PublishAction, "idle">,
    callback: () => Promise<void>,
  ) => {
    setAction(nextAction);
    setLocalError("");
    try {
      await callback();
      setConfirmPublish(false);
      setConfirmReady(false);
    } catch (requestError) {
      setLocalError(describePublishError(requestError));
    } finally {
      setAction("idle");
    }
  };

  return (
    <section
      aria-labelledby="coding-publish-title"
      className="mt-5 min-w-0 rounded-lg border border-white/10 bg-white/[0.025] p-4"
    >
      <div className="flex min-w-0 items-start gap-3">
        {published ? (
          <CheckCircle2
            aria-hidden="true"
            className="mt-0.5 shrink-0 text-emerald-200"
            size={18}
          />
        ) : capability?.available ? (
          <GitPullRequest
            aria-hidden="true"
            className="mt-0.5 shrink-0 text-cyan-200"
            size={18}
          />
        ) : (
          <CircleAlert
            aria-hidden="true"
            className="mt-0.5 shrink-0 text-slate-400"
            size={18}
          />
        )}
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold text-white" id="coding-publish-title">
            发布到 GitHub
          </h3>
          <p className="mt-1 text-xs leading-5 text-slate-400">
            {capability?.available
              ? "把已保存的本地版本上传到固定项目，并创建一份草稿 PR。不会合并。"
              : reasonCopy ||
                "GitHub 发布暂时不可用，本地修改、检查和保存仍可正常使用。"}
          </p>
        </div>
      </div>

      {waiting || published || isConflict || result?.state === "failed" ? (
        <div aria-live="polite" className="mt-4 min-w-0">
          <p
            className={`text-sm font-semibold ${
              isConflict || result?.state === "failed"
                ? "text-amber-100"
                : "text-emerald-100"
            }`}
          >
            {statusCopy(result)}
          </p>

          {result?.state === "publishing" || published ? (
            <ol className="mt-3 grid min-w-0 gap-2 text-xs sm:grid-cols-3">
              {publishSteps.map((step, index) => (
                <li
                  className="flex min-w-0 items-center gap-2 rounded-lg bg-white/[0.04] px-3 py-2 text-slate-300"
                  key={step}
                >
                  {published ? (
                    <Check aria-hidden="true" className="shrink-0 text-emerald-200" size={14} />
                  ) : index === 0 ? (
                    <LoaderCircle
                      aria-hidden="true"
                      className="shrink-0 animate-spin text-cyan-200 motion-reduce:animate-none"
                      size={14}
                    />
                  ) : (
                    <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full bg-slate-600" />
                  )}
                  <span className="min-w-0 break-words">{step}</span>
                </li>
              ))}
            </ol>
          ) : null}

          {hasRemotePr ? (
            <div className="mt-3 min-w-0">
              <p className="break-words text-sm font-medium text-slate-200">
                {result?.title}
              </p>
              <div className="mt-2 flex min-w-0 flex-col gap-2 text-xs sm:flex-row sm:flex-wrap sm:items-center">
                <span className="text-slate-400">PR #{result?.pr_number}</span>
              <a
                className="inline-flex min-h-9 w-full min-w-0 items-center justify-center gap-2 rounded-lg border border-white/15 px-3 font-semibold text-cyan-100 transition hover:bg-cyan-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-300/70 sm:w-auto"
                href={result?.pr_url ?? undefined}
                rel="noopener noreferrer"
                target="_blank"
              >
                打开 PR
                <ExternalLink aria-hidden="true" size={14} />
              </a>
              </div>
            </div>
          ) : null}

          {reasonCopy ? (
            <p className="mt-3 rounded-lg bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-100">
              {reasonCopy}
            </p>
          ) : null}
        </div>
      ) : null}

      {!published && !waiting && !isConflict && !hasRemotePr ? (
        <div className="mt-4 min-w-0">
          <label className="block text-xs font-semibold text-slate-200" htmlFor="coding-pr-title">
            PR 标题
          </label>
          <input
            aria-describedby="coding-pr-title-help"
            className="mt-2 min-h-10 w-full min-w-0 rounded-lg border border-white/10 bg-ink-950/75 px-3 text-sm text-white outline-none transition placeholder:text-slate-500 hover:border-white/20 focus:border-cyan-300/70 focus:ring-4 focus:ring-cyan-300/10 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={!capability?.available || disabled}
            id="coding-pr-title"
            maxLength={120}
            onChange={(event) => {
              setTitle(event.target.value);
              setConfirmPublish(false);
              setLocalError("");
            }}
            value={title}
          />
          <div className="mt-1 flex flex-col gap-1 text-xs text-slate-500 sm:flex-row sm:justify-between" id="coding-pr-title-help">
            <span>{titleValid ? "用一句话概括这次修改。" : "请填写 1–120 个字符。"}</span>
            <span>{title.length} / 120</span>
          </div>

          <label className="mt-4 block text-xs font-semibold text-slate-200" htmlFor="coding-pr-body">
            PR 说明
          </label>
          <textarea
            aria-describedby="coding-pr-body-help"
            className="mt-2 min-h-28 w-full min-w-0 resize-y rounded-lg border border-white/10 bg-ink-950/75 px-3 py-2 text-sm leading-6 text-white outline-none transition placeholder:text-slate-500 hover:border-white/20 focus:border-cyan-300/70 focus:ring-4 focus:ring-cyan-300/10 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={!capability?.available || disabled}
            id="coding-pr-body"
            maxLength={10_000}
            onChange={(event) => {
              setBody(event.target.value);
              setConfirmPublish(false);
              setLocalError("");
            }}
            value={body}
          />
          <div className="mt-1 flex flex-col gap-1 text-xs text-slate-500 sm:flex-row sm:justify-between" id="coding-pr-body-help">
            <span>
              {textHasSecret
                ? "内容中可能包含密钥，请删除后再发布。"
                : textHasControl
                  ? "内容中包含不可见控制字符，请清理后再发布。"
                  : "可补充修改目的、验证方式和注意事项。"}
            </span>
            <span>{body.length.toLocaleString("zh-CN")} / 10,000</span>
          </div>

          <button
            className="mt-4 inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-lg bg-cyan-300 px-3 text-sm font-semibold text-ink-950 transition hover:bg-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-100 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400 sm:w-auto"
            disabled={!capability?.available || disabled || !textValid}
            onClick={() => setConfirmPublish(true)}
            type="button"
          >
            <Send aria-hidden="true" size={16} />
            {canRetryPublish ? "重新创建草稿 PR" : "发布到 GitHub"}
          </button>
        </div>
      ) : null}

      {confirmPublish ? (
        <div aria-live="polite" className="mt-4 rounded-lg border border-cyan-300/15 bg-cyan-300/[0.065] p-3 text-sm text-cyan-50">
          <p className="font-semibold">确认发布这份本地版本吗？</p>
          <ul className="mt-2 space-y-1 text-xs leading-5 text-cyan-100/80">
            <li>会上传已保存的本地版本，共 {changes.file_count} 个当前修改文件。</li>
            <li>会在固定 GitHub 项目中创建草稿 PR。</li>
            <li>不会合并，也不会改变你当前使用的项目目录。</li>
          </ul>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <button className="min-h-9 rounded-lg border border-white/15 px-3 text-xs font-semibold hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50" onClick={() => setConfirmPublish(false)} type="button">
              返回检查
            </button>
            <button
              className="inline-flex min-h-9 items-center justify-center gap-2 rounded-lg bg-cyan-200 px-3 text-xs font-semibold text-cyan-950 hover:bg-cyan-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-100"
              onClick={() => void runAction("publishing", () => onPublish(normalizedTitle, normalizedBody))}
              type="button"
            >
              {action === "publishing" ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={14} /> : null}
              创建草稿 PR
            </button>
          </div>
        </div>
      ) : null}

      {canMarkReady && !waiting ? (
        <div className="mt-4">
          <button
            className="inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-lg border border-emerald-300/30 px-3 text-sm font-semibold text-emerald-100 transition hover:bg-emerald-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-200/70 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
            disabled={disabled}
            onClick={() => setConfirmReady(true)}
            type="button"
          >
            <Check aria-hidden="true" size={16} />
            标记为可审阅
          </button>
        </div>
      ) : null}

      {confirmReady ? (
        <div aria-live="polite" className="mt-4 rounded-lg border border-emerald-300/15 bg-emerald-300/[0.065] p-3 text-sm text-emerald-50">
          <p className="font-semibold">确认标记为可审阅吗？</p>
          <p className="mt-1 text-xs leading-5 text-emerald-100/80">
            GitHub 会把草稿 PR 改为可审阅状态；仍不会自动合并。
          </p>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <button className="min-h-9 rounded-lg border border-white/15 px-3 text-xs font-semibold hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/50" onClick={() => setConfirmReady(false)} type="button">
              保持草稿
            </button>
            <button
              className="inline-flex min-h-9 items-center justify-center gap-2 rounded-lg bg-emerald-200 px-3 text-xs font-semibold text-emerald-950 hover:bg-emerald-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-100"
              onClick={() => void runAction("marking_ready", onMarkReady)}
              type="button"
            >
              {action === "marking_ready" ? <LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={14} /> : null}
              确认可审阅
            </button>
          </div>
        </div>
      ) : null}

      {localError || error ? (
        <p aria-live="assertive" className="mt-3 rounded-lg bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-100" role="alert">
          {localError || error}
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
