import {
  CheckCircle2,
  CircleAlert,
  CircleDashed,
  LoaderCircle,
  Play,
  RotateCcw,
  Square,
  WandSparkles,
} from "lucide-react";
import { useState } from "react";
import type {
  CodingVerification,
  CodingVerificationStep,
} from "../types/coding";

interface CodingVerificationPanelProps {
  available: boolean;
  disabled: boolean;
  empty?: boolean;
  error: string;
  onCancel: () => Promise<void>;
  onConfirm: () => Promise<void>;
  onRequestFix: (prompt: string) => void;
  onRun: () => Promise<void>;
  verification: CodingVerification | null;
}

type ActionState = "idle" | "starting" | "stopping" | "confirming";

const reasonText: Record<string, string> = {
  dependency_change_unsupported:
    "修改涉及项目运行所需的组件清单。当前服务不会临时下载新内容，因此未运行。",
  documentation_only: "本次只修改说明文字，无需运行项目验证。",
  no_changes: "当前没有需要验证的修改。",
  verifier_unavailable: "项目验证服务未启动，仍可查看和下载修改。",
  snapshot_mismatch: "项目版本正在更新，暂时无法运行验证，请稍后重试。",
  verification_timeout: "检查等待时间过长，已自动停止。你可以稍后重新运行。",
  runner_environment_not_ready:
    "当前运行环境与项目依赖不一致，因此没有把环境问题误报为代码问题。你仍可查看和下载修改。",
  no_project_checks:
    "此项目没有配置可运行的测试或检查，因此无法判断代码是否正确；这不代表验证通过。",
};

function formatArgv(argv: string[]) {
  return argv.map((argument) => JSON.stringify(argument)).join(" ");
}

function stepState(step: CodingVerificationStep) {
  if (step.state === "running") return "正在检查";
  if (step.result === "passed") return "通过";
  if (step.result === "failed") return "发现问题";
  if (step.state === "cancelled") return "已停止";
  return "等待";
}

function statusCopy(verification: CodingVerification | null, empty: boolean) {
  if (empty) {
    return {
      title: "本轮无需项目验证",
      description: "当前没有待保存的新文件变化，此前完成的修改不受影响。",
      tone: "text-emerald-100",
    };
  }
  if (!verification || verification.stale) {
    return {
      title: verification?.stale
        ? "草稿已更新，需要重新验证"
        : "尚未运行项目验证",
      description: "运行后会自动选择需要检查的项目部分。",
      tone: "text-slate-200",
    };
  }
  if (verification.state === "running") {
    return {
      title: "正在验证当前修改",
      description: "你可以继续查看文件；验证完成前不能开始新一轮修改。",
      tone: "text-cyan-100",
    };
  }
  if (verification.state === "awaiting_confirmation") {
    return {
      title: "等待你确认",
      description:
        "下方列出了准备运行的检查。确认后只会在临时项目副本中运行，不能联网，也不会改变你的本地项目。",
      tone: "text-cyan-100",
    };
  }
  if (verification.state === "cancelled") {
    return {
      title: "项目验证已停止",
      description: "修改草稿仍然保留，可以随时重新运行。",
      tone: "text-amber-100",
    };
  }
  if (verification.result === "passed") {
    return {
      title: "项目验证通过",
      description: "当前修改通过了适用的项目检查。",
      tone: "text-emerald-100",
    };
  }
  if (verification.result === "failed") {
    return {
      title: "项目验证发现问题",
      description: "修改草稿仍然保留，可以查看原因并让代码助手继续修复。",
      tone: "text-amber-100",
    };
  }
  if (
    verification.result === "not_applicable" &&
    verification.reason !== "no_project_checks"
  ) {
    return {
      title: "本次修改无需项目验证",
      description:
        reasonText[verification.reason ?? ""] ?? "当前修改不需要运行项目检查。",
      tone: "text-slate-200",
    };
  }
  return {
    title: "项目验证未运行",
    description:
      reasonText[verification.reason ?? ""] ??
      "当前没有可用的验证结果，仍可查看和下载修改。",
    tone: "text-amber-100",
  };
}

export default function CodingVerificationPanel({
  available,
  disabled,
  empty = false,
  error,
  onCancel,
  onConfirm,
  onRequestFix,
  onRun,
  verification,
}: CodingVerificationPanelProps) {
  const [action, setAction] = useState<ActionState>("idle");
  const copy = statusCopy(verification, empty);
  const isRunning =
    verification?.state === "running" && verification.stale === false;
  const isAwaiting =
    verification?.state === "awaiting_confirmation" &&
    verification.stale === false;
  const failedSteps = empty
    ? []
    : verification?.steps.filter((step) => step.result === "failed") ?? [];

  const runAction = async (
    nextAction: Exclude<ActionState, "idle">,
    callback: () => Promise<void>,
  ) => {
    setAction(nextAction);
    try {
      await callback();
    } finally {
      setAction("idle");
    }
  };

  const prepareFix = () => {
    const summary = failedSteps
      .map(
        (step) =>
          `${step.label}：${step.summary || "请查看并修正发现的问题"}`,
      )
      .join("\n");
    onRequestFix(
      [
        "请修复当前修改草稿中项目验证发现的问题：",
        summary,
        "请保留原有目标，不要扩大修改范围。修复完成后说明调整了什么。",
      ].join("\n"),
    );
  };

  return (
    <section
      aria-labelledby="coding-verification-title"
      className="mt-5 rounded-lg border border-white/10 bg-white/[0.025] p-4"
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            {empty ? (
              <CheckCircle2
                aria-hidden="true"
                className="shrink-0 text-emerald-200"
                size={17}
              />
            ) : isRunning ? (
              <LoaderCircle
                aria-hidden="true"
                className="shrink-0 animate-spin text-cyan-200 motion-reduce:animate-none"
                size={17}
              />
            ) : verification?.result === "passed" ? (
              <CheckCircle2
                aria-hidden="true"
                className="shrink-0 text-emerald-200"
                size={17}
              />
            ) : verification?.result === "failed" ? (
              <CircleAlert
                aria-hidden="true"
                className="shrink-0 text-amber-200"
                size={17}
              />
            ) : (
              <CircleDashed
                aria-hidden="true"
                className="shrink-0 text-slate-400"
                size={17}
              />
            )}
            <h3
              className="text-sm font-semibold text-white"
              id="coding-verification-title"
            >
              项目验证
            </h3>
          </div>
          <div aria-live="polite" className="mt-2" role="status">
            <p className={`text-sm font-semibold ${copy.tone}`}>{copy.title}</p>
            <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-400">
              {copy.description}
            </p>
          </div>
        </div>

        {empty ? null : isAwaiting ? (
          <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
            <button
              className="inline-flex min-h-10 items-center justify-center rounded-lg border border-white/15 px-3 text-xs font-semibold text-slate-200 transition hover:bg-white/[0.05] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-300/60 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={action !== "idle"}
              onClick={() => void runAction("stopping", onCancel)}
              type="button"
            >
              暂不运行
            </button>
            <button
              className="inline-flex min-h-10 items-center justify-center gap-2 rounded-lg bg-cyan-200 px-3 text-xs font-semibold text-slate-950 transition hover:bg-cyan-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-100 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={action !== "idle"}
              onClick={() => void runAction("confirming", onConfirm)}
              type="button"
            >
              {action === "confirming" ? (
                <LoaderCircle
                  aria-hidden="true"
                  className="animate-spin motion-reduce:animate-none"
                  size={14}
                />
              ) : (
                <Play aria-hidden="true" size={14} />
              )}
              允许这些检查
            </button>
          </div>
        ) : isRunning ? (
          <button
            className="inline-flex min-h-9 shrink-0 items-center justify-center gap-2 rounded-lg border border-amber-300/30 px-3 text-xs font-semibold text-amber-100 transition hover:bg-amber-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-200/70 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={action !== "idle"}
            onClick={() => void runAction("stopping", onCancel)}
            type="button"
          >
            {action === "stopping" ? (
              <LoaderCircle
                aria-hidden="true"
                className="animate-spin motion-reduce:animate-none"
                size={14}
              />
            ) : (
              <Square aria-hidden="true" fill="currentColor" size={11} />
            )}
            停止验证
          </button>
        ) : (
          <button
            className="inline-flex min-h-9 shrink-0 items-center justify-center gap-2 rounded-lg border border-cyan-300/35 px-3 text-xs font-semibold text-cyan-100 transition hover:bg-cyan-300/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200/70 disabled:cursor-not-allowed disabled:opacity-50"
            disabled={!available || disabled || action !== "idle"}
            onClick={() => void runAction("starting", onRun)}
            type="button"
          >
            {action === "starting" ? (
              <LoaderCircle
                aria-hidden="true"
                className="animate-spin motion-reduce:animate-none"
                size={14}
              />
            ) : verification ? (
              <RotateCcw aria-hidden="true" size={14} />
            ) : (
              <Play aria-hidden="true" size={14} />
            )}
            {verification ? "重新运行" : "运行项目验证"}
          </button>
        )}
      </div>

      {!available && !empty ? (
        <div className="mt-3 rounded-lg bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-100">
          项目验证服务未启动，仍可查看和下载修改。
        </div>
      ) : null}

      {verification?.steps.length ? (
        <ol className="mt-4 space-y-2">
          {verification.steps.map((step) => (
            <li
              className="rounded-lg border border-white/10 bg-black/15 px-3 py-2.5"
              key={step.id}
            >
              <div className="flex min-w-0 items-center justify-between gap-3">
                <span className="min-w-0 break-words text-xs font-medium text-slate-200">
                  {step.label}
                </span>
                <span
                  className={`shrink-0 text-[11px] font-semibold ${
                    step.result === "passed"
                      ? "text-emerald-200"
                      : step.result === "failed"
                        ? "text-amber-200"
                        : step.state === "running"
                          ? "text-cyan-200"
                          : "text-slate-500"
                  }`}
                >
                  {stepState(step)}
                </span>
              </div>
              {step.summary ? (
                <p className="mt-1.5 break-words text-xs leading-5 text-slate-400">
                  {step.summary}
                </p>
              ) : null}
              {step.command ? (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs font-medium text-slate-400 outline-none hover:text-slate-200 focus-visible:ring-2 focus-visible:ring-cyan-300/60">
                    查看命令
                  </summary>
                  <div className="mt-2 min-w-0 rounded-lg bg-black/25 p-3">
                    <code className="block overflow-x-auto whitespace-pre-wrap break-all font-mono text-xs leading-5 text-slate-300">
                      {formatArgv(step.command.argv)}
                    </code>
                    {step.command.cwd !== "." ? (
                      <p className="mt-1 text-[11px] text-slate-500">
                        运行位置：{step.command.cwd}
                      </p>
                    ) : null}
                  </div>
                </details>
              ) : null}
              {step.details ? (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs font-medium text-slate-400 outline-none hover:text-slate-200 focus-visible:ring-2 focus-visible:ring-cyan-300/60">
                    查看技术详情
                  </summary>
                  <pre
                    className="mt-2 max-h-56 max-w-full overflow-auto whitespace-pre-wrap break-words rounded-lg bg-black/25 p-3 text-xs leading-5 text-slate-400"
                    tabIndex={0}
                  >
                    {step.details}
                  </pre>
                </details>
              ) : null}
            </li>
          ))}
        </ol>
      ) : null}

      {failedSteps.length ? (
        <button
          className="mt-4 inline-flex min-h-9 items-center justify-center gap-2 rounded-lg bg-amber-200 px-3 text-xs font-semibold text-amber-950 transition hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber-100"
          onClick={prepareFix}
          type="button"
        >
          <WandSparkles aria-hidden="true" size={14} />
          让代码助手修复
        </button>
      ) : null}

      {error ? (
        <div
          className="mt-3 flex items-start gap-2 rounded-lg bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-100"
          role="alert"
        >
          <CircleAlert
            aria-hidden="true"
            className="mt-0.5 shrink-0"
            size={14}
          />
          {error}
        </div>
      ) : null}
    </section>
  );
}
