import { AlertCircle, Check, Clock3, LoaderCircle, RefreshCw, Save, Square, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  cancelSkillCreatorEvaluation,
  readSkillCreatorEvaluation,
  retrySkillCreatorEvaluation,
  saveSkillCreatorEvaluationFeedback,
  submitSkillCreatorEvaluationReview,
  type SkillCreatorDraft,
  type SkillCreatorSession,
  type SkillEvaluationAssertion,
  type SkillEvaluationCase,
  type SkillEvaluationItem,
  type SkillEvaluationRun,
} from "../../utils/skillCreatorApi";

const TERMINAL = new Set(["completed", "failed", "cancelled", "stale"]);

const ERROR_LABELS: Record<string, string> = {
  skill_not_read: "本次运行没有实际使用 Skill，结果不能用于最终判断。",
  model_gateway_unconfigured: "模型服务尚未配置，无法开始试用。",
  sandbox_unavailable: "隔离运行环境暂不可用，本次试用已安全停止。",
  evaluation_stale: "Skill 或测试任务已经变化，本次结果仅供查看。",
  model_mismatch: "对比两侧使用了不同模型，结果不可直接比较。",
  tool_not_allowed: "运行尝试使用未允许的工具，已安全停止。",
  network_not_allowed: "运行尝试访问网络，已被离线环境阻止。",
  skill_evaluation_unresolved_tool_call: "模型给出了工具指令但没有真正执行，本次结果已作废。",
};

function statusLabel(status: SkillEvaluationRun["status"]) {
  if (status === "queued") return "等待执行";
  if (status === "running") return "评测中";
  if (status === "completed") return "等待人工评审";
  if (status === "cancelled") return "已取消";
  if (status === "stale") return "评测已过期";
  return "评测失败";
}

function itemStatusLabel(status: SkillEvaluationItem["status"]) {
  if (status === "pending" || status === "queued") return "等待执行";
  if (status === "running") return "执行中";
  if (status === "completed") return "已完成";
  if (status === "cancelled") return "已取消";
  if (status === "skill_not_read") return "未读取 Skill";
  return "执行失败";
}

function assertionLabel(assertion: SkillEvaluationAssertion) {
  if (assertion.kind === "exact_match") return "完全匹配";
  if (assertion.kind === "contains") return `包含 ${assertion.value ?? "指定文本"}`;
  if (assertion.kind === "not_contains") return `不包含 ${assertion.value ?? "指定文本"}`;
  if (assertion.kind === "json_schema") return "符合 JSON Schema";
  if (assertion.kind === "file_exists") return `生成 ${assertion.path ?? "指定文件"}`;
  return `${assertion.path ?? "文件"} SHA-256 匹配`;
}

function assertionMessage(value?: string | null) {
  if (!value) return "";
  const translations: Record<string, string> = {
    "Output did not contain the required text.": "输出缺少要求的内容。",
    "Output contained the required text.": "输出包含要求的内容。",
    "Output contained the forbidden text.": "输出包含了不应出现的内容。",
    "Output excluded the forbidden text.": "输出未包含禁用内容。",
  };
  return translations[value] ?? value;
}

function targetLabel(target: SkillEvaluationItem["target"]) {
  if (target === "baseline") return "未使用 Skill";
  if (target === "previous") return "改进前";
  return "当前 Skill";
}

function ItemResult({ item, target }: { item?: SkillEvaluationItem; target: SkillEvaluationItem["target"] }) {
  if (!item) {
    return <div className="grid min-h-48 place-items-center rounded-lg bg-white/[0.025] text-sm text-slate-500">尚无结果</div>;
  }
  const errorCode = item.error_code ?? (item.status === "skill_not_read" ? "skill_not_read" : null);
  const errorText = errorCode ? ERROR_LABELS[errorCode] ?? item.error ?? errorCode : item.error;
  const assertionResults = item.assertion_results ?? item.assertions ?? [];
  const tokenCount = item.usage?.total_tokens ?? item.usage?.estimated_tokens ?? (
    (item.usage?.input_tokens != null || item.usage?.output_tokens != null)
      ? (item.usage.input_tokens ?? 0) + (item.usage.output_tokens ?? 0)
      : null
  );
  return (
    <article className="min-w-0 rounded-lg bg-white/[0.025] p-4" aria-label={`${targetLabel(target)} 结果`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-white">{targetLabel(target)}</h4>
        <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${item.status === "completed" ? "bg-emerald-300/10 text-emerald-100" : item.status === "failed" || item.status === "skill_not_read" ? "bg-rose-300/10 text-rose-100" : "bg-white/[0.055] text-slate-300"}`}>{itemStatusLabel(item.status)}</span>
      </div>
      {target !== "baseline" ? (
        <p className={`mt-3 flex items-center gap-2 text-xs font-semibold ${item.skill_read ? "text-emerald-100" : "text-rose-100"}`}>
          {item.skill_read ? <Check aria-hidden="true" size={13} /> : <X aria-hidden="true" size={13} />}
          {item.skill_read ? "已确认实际使用 Skill" : "没有实际使用 Skill"}
        </p>
      ) : null}
      {errorText ? <p className="mt-3 rounded-md bg-rose-300/10 p-3 text-xs leading-5 text-rose-50" role="alert">{errorText}</p> : null}
      <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-md bg-ink-950/70 p-3 text-xs leading-5 text-slate-200">{item.output || (item.status === "running" || item.status === "queued" ? "正在等待输出…" : "没有文本输出")}</pre>
      {assertionResults.length ? (
        <ul className="mt-3 space-y-2" aria-label="断言结果">
          {assertionResults.map((assertion, index) => (
            <li className={`flex items-start gap-2 text-xs leading-5 ${assertion.passed === false ? "text-rose-100" : assertion.passed ? "text-emerald-100" : "text-slate-400"}`} key={`${assertion.kind}-${index}`}>
              {assertion.passed === false ? <X aria-hidden="true" className="mt-1 shrink-0" size={12} /> : <Check aria-hidden="true" className="mt-1 shrink-0" size={12} />}
              <span>{assertionLabel(assertion)}{assertion.message || assertion.reason ? `：${assertionMessage(assertion.message ?? assertion.reason)}` : ""}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {item.work_manifest?.length ? (
        <details className="mt-3 text-xs text-slate-400">
          <summary className="cursor-pointer font-semibold text-slate-300">查看 work/ 输出文件（{item.work_manifest.length}）</summary>
          <ul className="mt-2 space-y-2">
            {item.work_manifest.map((file) => (
              <li className="rounded-md bg-black/20 p-2" key={file.path}>
                <p className="break-all font-mono text-slate-200">{file.path}</p>
                <p className="mt-1">{file.size_bytes ?? file.size ?? 0} bytes{file.sha256 ? ` · ${file.sha256.slice(0, 12)}…` : ""}</p>
                {file.text_preview || file.preview ? <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap text-slate-400">{file.text_preview ?? file.preview}{file.preview_truncated ? "\n…预览已截断" : ""}</pre> : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <details className="mt-3 text-[11px] text-slate-500"><summary className="cursor-pointer">查看运行信息</summary><div className="mt-2 flex flex-wrap gap-x-4 gap-y-1"><span>{item.latency_ms != null ? `${(item.latency_ms / 1000).toFixed(1)} 秒` : "耗时待记录"}</span><span>{tokenCount != null && tokenCount > 0 ? `${tokenCount} tokens` : "token 用量未提供"}</span><span>{item.actual_model || "实际模型待记录"}</span></div></details>
    </article>
  );
}

function CaseComparison({ evaluationCase, items, comparisons, onRetry, retrying, canRetry }: {
  evaluationCase: SkillEvaluationCase;
  items: SkillEvaluationItem[];
  comparisons: NonNullable<SkillEvaluationRun["report"]>["pairs"];
  onRetry: () => void;
  retrying: boolean;
  canRetry: boolean;
}) {
  const [mobileTarget, setMobileTarget] = useState<SkillEvaluationItem["target"]>("candidate");
  const [requestedRepetition, setRequestedRepetition] = useState<number | null>(null);
  const repetitions = [...new Set(items.map((item) => item.repetition))].sort((a, b) => a - b);
  const selectedRepetition = requestedRepetition != null && repetitions.includes(requestedRepetition)
    ? requestedRepetition
    : repetitions.at(-1) ?? 1;
  const baseline = items.find((item) => item.target === "baseline" && item.repetition === selectedRepetition);
  const previous = items.find((item) => item.target === "previous" && item.repetition === selectedRepetition);
  const candidate = items.find((item) => item.target === "candidate" && item.repetition === selectedRepetition);
  const targets: SkillEvaluationItem["target"][] = previous ? ["baseline", "previous", "candidate"] : ["baseline", "candidate"];
  const comparison = comparisons?.find((item) => item.case_id === evaluationCase.case_id && item.repetition === selectedRepetition);
  const comparisonLabels = { regressed: "出现退化", improved: "已改善", flat: "结果持平", inconclusive: "证据不足" } as const;
  return (
    <section className="border-t border-white/10 py-6 first:border-t-0 first:pt-0" aria-labelledby={`result-${evaluationCase.case_id}`}>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 className="text-base font-semibold text-white" id={`result-${evaluationCase.case_id}`}>{evaluationCase.name}</h3>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-400">{evaluationCase.expected_behavior}</p>
          {comparison ? <span className={`mt-2 inline-flex rounded-full px-2.5 py-1 text-[11px] font-semibold ${comparison.classification === "regressed" ? "bg-rose-300/10 text-rose-100" : comparison.classification === "improved" ? "bg-emerald-300/10 text-emerald-100" : "bg-white/[0.055] text-slate-300"}`}>{comparisonLabels[comparison.classification]}</span> : null}
          {repetitions.length > 1 ? (
            <div className="mt-2 flex flex-wrap items-center gap-1" aria-label={`${evaluationCase.name} 重复运行`}>
              <span className="mr-1 text-[11px] text-slate-500">重复运行</span>
              {repetitions.map((repetition) => (
                <button aria-pressed={selectedRepetition === repetition} className={`rounded px-2 py-1 text-[11px] ${selectedRepetition === repetition ? "bg-brand-300/15 text-brand-100" : "bg-white/[0.04] text-slate-400"}`} key={repetition} onClick={() => setRequestedRepetition(repetition)} type="button">第 {repetition} 次</button>
              ))}
            </div>
          ) : null}
        </div>
        <button className="inline-flex shrink-0 items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-slate-200 transition hover:bg-white/[0.055] disabled:opacity-40" disabled={!canRetry || retrying} onClick={onRetry} type="button"><RefreshCw aria-hidden="true" className={retrying ? "animate-spin motion-reduce:animate-none" : ""} size={13} />重跑此用例</button>
      </div>
      <div className={`mt-4 grid gap-1 rounded-md bg-white/[0.045] p-1 sm:hidden ${targets.length === 3 ? "grid-cols-3" : "grid-cols-2"}`} role="tablist" aria-label={`${evaluationCase.name} 结果视图`}>
        {targets.map((target) => (
          <button aria-selected={mobileTarget === target} className={`min-h-11 rounded px-2 py-2 text-xs font-semibold ${mobileTarget === target ? "bg-surface-800 text-white" : "text-slate-400"}`} key={target} onClick={() => setMobileTarget(target)} role="tab" type="button">{targetLabel(target)}</button>
        ))}
      </div>
      <div className={`mt-3 hidden gap-3 sm:grid ${previous ? "sm:grid-cols-3" : "sm:grid-cols-2"}`}>
        <ItemResult item={baseline} target="baseline" />
        {previous ? <ItemResult item={previous} target="previous" /> : null}
        <ItemResult item={candidate} target="candidate" />
      </div>
      <div className="mt-3 sm:hidden">
        <ItemResult item={mobileTarget === "baseline" ? baseline : mobileTarget === "previous" ? previous : candidate} target={mobileTarget} />
      </div>
    </section>
  );
}

export default function SkillEvaluationReview({
  session,
  draft,
  run,
  onRunChange,
  onSessionRefresh,
  onError,
  onNotice,
}: {
  session: SkillCreatorSession;
  draft: SkillCreatorDraft;
  run: SkillEvaluationRun;
  onRunChange: (run: SkillEvaluationRun) => void;
  onSessionRefresh: () => Promise<void>;
  onError: (error: unknown, fallback: string) => void;
  onNotice: (message: string) => void;
}) {
  const [busy, setBusy] = useState("");
  const [feedback, setFeedback] = useState(session.review_feedback ?? run.feedback ?? run.reviews?.at(-1)?.feedback ?? "");
  const [failedAcknowledged, setFailedAcknowledged] = useState(false);
  const [acknowledgedRegressionIds, setAcknowledgedRegressionIds] = useState<Set<string>>(
    () => new Set(run.reviews?.at(-1)?.acknowledged_regression_item_ids ?? []),
  );

  useEffect(() => {
    if (TERMINAL.has(run.status)) return;
    const timer = window.setInterval(() => {
      void readSkillCreatorEvaluation(run.run_id)
        .then(async (nextRun) => {
          onRunChange(nextRun);
          if (TERMINAL.has(nextRun.status)) await onSessionRefresh();
        })
        .catch((error) => onError(error, "评测进度刷新失败。"));
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [onError, onRunChange, onSessionRefresh, run.run_id, run.status]);

  const completedItems = run.items.filter((item) => item.status === "completed").length;
  const failedAssertions = run.items.filter((item) => item.target === "candidate").some((item) => (item.assertion_results ?? item.assertions)?.some((assertion) => assertion.passed === false));
  const candidateItems = run.items.filter((item) => item.target === "candidate");
  const candidateRead = candidateItems.length > 0 && candidateItems.every((item) => item.status === "completed" && item.skill_read === true);
  const actualModels = new Set(run.items.flatMap((item) => item.actual_model ? [item.actual_model] : []));
  const digestMatches = run.frozen_digest.toLowerCase() === draft.content_digest.toLowerCase();
  const targetCount = run.previous_overlay_id ? 3 : 2;
  const expectedItemCount = run.cases.length * targetCount * Math.max(run.repetitions, 1);
  const allItemsComplete = run.items.length === expectedItemCount && run.items.every((item) => item.status === "completed" && !item.error_code);
  const modelIdentityComplete = run.items.length > 0 && run.items.every((item) => Boolean(item.actual_model)) && actualModels.size === 1;
  const comparable = run.status === "completed" && run.cases.length >= 3 && allItemsComplete && candidateRead && modelIdentityComplete && digestMatches && run.report?.eligible_for_accept !== false;
  const regressionIds = run.report?.regression_item_ids ?? [];
  const regressionsAcknowledged = regressionIds.every((itemId) => acknowledgedRegressionIds.has(itemId));
  const reviewPending = (run.review_state ?? "pending") === "pending" && !["accepted", "revise", "waived"].includes(session.review_state ?? "none");
  const canAccept = reviewPending && comparable && regressionsAcknowledged && (!regressionIds.length || Boolean(feedback.trim())) && (!failedAssertions || failedAcknowledged);
  const runError = run.error_code ? ERROR_LABELS[run.error_code] ?? run.error ?? run.error_code : run.error;

  async function cancel() {
    setBusy("cancel");
    try {
      const result = await cancelSkillCreatorEvaluation(session, draft, run);
      onRunChange(result.run);
      await onSessionRefresh();
      onNotice("已请求取消评测，完成中的配对结果会保留。");
    } catch (error) {
      onError(error, "评测取消失败。");
    } finally {
      setBusy("");
    }
  }

  async function retry(caseIds?: string[]) {
    setBusy(caseIds?.[0] ? `retry-${caseIds[0]}` : "retry");
    try {
      const result = await retrySkillCreatorEvaluation(session, draft, run, caseIds);
      onRunChange(result.run);
      await onSessionRefresh();
      onNotice(caseIds?.length ? "已重新排队该用例的 baseline/with-skill 配对。" : "未完成的配对已重新排队。");
    } catch (error) {
      onError(error, "评测重试失败。");
    } finally {
      setBusy("");
    }
  }

  async function saveFeedback() {
    setBusy("feedback");
    try {
      const result = await saveSkillCreatorEvaluationFeedback(session, draft, run, feedback);
      onRunChange(result.run);
      await onSessionRefresh();
      onNotice("人工反馈已保存并绑定本次评测与草稿摘要。");
    } catch (error) {
      onError(error, "评审反馈保存失败。");
    } finally {
      setBusy("");
    }
  }

  async function review(decision: "accept" | "revise") {
    if (decision === "revise" && !feedback.trim()) return;
    setBusy(decision);
    try {
      let currentSession = session;
      let currentRun = run;
      if (feedback.trim() !== (run.feedback ?? "").trim()) {
        const saved = await saveSkillCreatorEvaluationFeedback(session, draft, run, feedback);
        currentRun = saved.run;
        currentSession = saved.session ?? session;
        onRunChange(currentRun);
      }
      const result = await submitSkillCreatorEvaluationReview(currentSession, draft, currentRun, decision, {
        feedback,
        reason: decision === "accept" && (failedAssertions || regressionIds.length)
          ? feedback.trim() || "人工检查后接受断言失败项"
          : undefined,
        confirm_failed_assertions: failedAcknowledged,
        acknowledged_regression_item_ids: [...acknowledgedRegressionIds].sort(),
      });
      onRunChange(result.run);
      await onSessionRefresh();
      onNotice(decision === "accept" ? "当前摘要的评测已人工接受。安装仍需要单独确认。" : "已记录修改意见，可进入下一步生成改进提案。");
    } catch (error) {
      onError(error, decision === "accept" ? "评测接受失败。" : "修改决定保存失败。");
    } finally {
      setBusy("");
    }
  }

  return (
    <div className="mt-5 space-y-5">
      <section className="rounded-lg border border-white/10 bg-surface-900/80 p-5 sm:p-6" aria-labelledby="creator-evaluation-results-heading">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-xl font-semibold text-white" id="creator-evaluation-results-heading">看看 Skill 是否真的更好用</h2>
              <span className={`rounded-full px-3 py-1 text-xs font-semibold ${run.status === "completed" ? "bg-emerald-300/10 text-emerald-100" : run.status === "failed" || run.status === "stale" ? "bg-rose-300/10 text-rose-100" : "bg-brand-300/10 text-brand-100"}`}>{statusLabel(run.status)}</span>
            </div>
            <p className="mt-2 text-sm leading-6 text-slate-400">同一批任务、同一个模型，比较未使用 Skill、改进前和当前版本的结果。</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {!TERMINAL.has(run.status) ? <button className="inline-flex items-center gap-2 rounded-md border border-rose-300/20 px-3 py-2 text-xs font-semibold text-rose-100 disabled:opacity-40" disabled={Boolean(busy)} onClick={() => void cancel()} type="button"><Square aria-hidden="true" size={12} />取消评测</button> : null}
            {run.status === "failed" || run.status === "cancelled" ? <button className="inline-flex items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-white disabled:opacity-40" disabled={!reviewPending || Boolean(busy)} onClick={() => void retry()} type="button"><RefreshCw aria-hidden="true" size={13} />重试未完成项</button> : null}
          </div>
        </div>

        <div className="mt-5 grid gap-3 sm:grid-cols-3">
          <div className="rounded-lg bg-white/[0.025] p-4"><p className="text-xs text-slate-500">运行进度</p><p className="mt-2 text-lg font-semibold text-white">{completedItems}/{run.items.length}</p></div>
          <div className="rounded-lg bg-white/[0.025] p-4"><p className="text-xs text-slate-500">是否真的使用了 Skill</p><p className={`mt-2 text-sm font-semibold ${candidateRead ? "text-emerald-100" : "text-amber-100"}`}>{candidateRead ? "全部确认" : "尚未满足"}</p></div>
          <div className="rounded-lg bg-white/[0.025] p-4"><p className="text-xs text-slate-500">结果是否可比较</p><p className={`mt-2 text-sm font-semibold ${digestMatches && modelIdentityComplete ? "text-emerald-100" : "text-rose-100"}`}>{digestMatches && modelIdentityComplete ? "可以比较" : "需要重试"}</p></div>
        </div>

        {!TERMINAL.has(run.status) ? (
          <p className="mt-5 flex items-center gap-2 text-sm text-brand-100" role="status"><LoaderCircle aria-hidden="true" className="animate-spin motion-reduce:animate-none" size={16} />评测在服务端继续运行，页面每 2 秒刷新一次。</p>
        ) : null}
        {runError ? <p className="mt-5 rounded-lg bg-rose-300/10 p-4 text-sm leading-6 text-rose-50" role="alert">{runError}</p> : null}
        {!digestMatches ? <p className="mt-5 rounded-lg bg-rose-300/10 p-4 text-sm leading-6 text-rose-50" role="alert">草稿摘要已变化，本次结果只可查看，不能接受。请返回测试设计并重新运行当前套件。</p> : null}

        <div className="mt-6">
          {run.cases.map((evaluationCase) => (
            <CaseComparison
              evaluationCase={evaluationCase}
              comparisons={run.report?.pairs}
              canRetry={reviewPending && TERMINAL.has(run.status)}
              items={run.items.filter((item) => item.case_id === evaluationCase.case_id)}
              key={evaluationCase.case_id}
              onRetry={() => void retry([evaluationCase.case_id])}
              retrying={busy === `retry-${evaluationCase.case_id}`}
            />
          ))}
        </div>
      </section>

      <section className="rounded-lg border border-white/10 bg-surface-900/80 p-5 sm:p-6" aria-labelledby="creator-human-review-heading">
        <h2 className="text-lg font-semibold text-white" id="creator-human-review-heading">告诉我们你的判断</h2>
        <p className="mt-2 text-sm leading-6 text-slate-400">结果符合预期就继续；有问题就指出哪一项需要改。系统不会用另一个模型替你做最终决定。</p>
        <label className="mt-4 block" htmlFor="creator-review-feedback">
          <span className="text-xs font-semibold text-slate-300">你观察到了什么？</span>
          <textarea className="mt-2 min-h-28 w-full resize-y rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2.5 text-sm leading-6 text-white focus:border-brand-300/50 focus:outline-none" id="creator-review-feedback" maxLength={4_000} onChange={(event) => setFeedback(event.target.value)} placeholder="指出哪一个用例、哪一处行为需要保留或修改。选择“需要修改”时必填。" value={feedback} />
        </label>
        {failedAssertions ? (
          <label className="mt-4 flex items-start gap-3 rounded-lg bg-amber-300/[0.08] p-4 text-sm leading-6 text-amber-50">
            <input checked={failedAcknowledged} className="mt-1 h-4 w-4 accent-amber-300" onChange={(event) => setFailedAcknowledged(event.target.checked)} type="checkbox" />
            存在失败断言。我已逐项检查，并理解接受会以人工判断覆盖这些辅助断言。
          </label>
        ) : null}
        {regressionIds.length ? (
          <fieldset className="mt-4 rounded-lg border border-rose-300/20 bg-rose-300/[0.055] p-4">
            <legend className="px-1 text-sm font-semibold text-rose-100">逐项确认新增退化</legend>
            <p className="mt-1 text-xs leading-5 text-rose-50/75">以下 candidate 相比 previous 新增失败。必须逐项确认并在反馈中说明理由，不能静默忽略。</p>
            <div className="mt-3 space-y-2">
              {regressionIds.map((itemId) => {
                const item = run.items.find((candidate) => candidate.item_id === itemId);
                const caseName = run.cases.find((candidate) => candidate.case_id === item?.case_id)?.name ?? itemId;
                return (
                  <label className="flex items-start gap-3 text-sm leading-6 text-rose-50" key={itemId}>
                    <input checked={acknowledgedRegressionIds.has(itemId)} className="mt-1 h-4 w-4 accent-rose-300" onChange={(event) => setAcknowledgedRegressionIds((current) => {
                      const next = new Set(current);
                      if (event.target.checked) next.add(itemId); else next.delete(itemId);
                      return next;
                    })} type="checkbox" />
                    <span>{caseName} · 第 {item?.repetition ?? 1} 次</span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        ) : null}
        {!comparable ? (
          <p className="mt-4 flex items-start gap-2 text-sm leading-6 text-amber-100"><AlertCircle aria-hidden="true" className="mt-1 shrink-0" size={15} />只有当前摘要的全部目标与用例完成、应用凭据可信且实际模型一致时，才能接受。</p>
        ) : null}
        <div className="mt-5 flex flex-wrap justify-end gap-2 border-t border-white/10 pt-5">
          <button className="inline-flex items-center gap-2 rounded-md border border-white/15 px-4 py-2.5 text-sm font-semibold text-white disabled:opacity-40" disabled={!reviewPending || Boolean(busy)} onClick={() => void saveFeedback()} type="button"><Save aria-hidden="true" size={14} />{busy === "feedback" ? "正在保存…" : reviewPending ? "保存反馈" : "评审已冻结"}</button>
          <button className="inline-flex min-h-11 items-center gap-2 rounded-md border border-amber-300/25 bg-amber-300/[0.08] px-4 py-2.5 text-sm font-semibold text-amber-50 disabled:opacity-40" disabled={!reviewPending || !feedback.trim() || !TERMINAL.has(run.status) || Boolean(busy)} onClick={() => void review("revise")} type="button"><Clock3 aria-hidden="true" size={14} />还要修改</button>
          <button className="inline-flex min-h-11 items-center gap-2 rounded-md bg-emerald-300 px-4 py-2.5 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500" disabled={!canAccept || Boolean(busy)} onClick={() => void review("accept")} type="button"><Check aria-hidden="true" size={15} />效果可以，继续</button>
        </div>
      </section>
    </div>
  );
}
