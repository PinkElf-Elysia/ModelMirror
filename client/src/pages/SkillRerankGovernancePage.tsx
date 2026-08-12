import {
  ArrowLeft,
  CheckCircle2,
  CircleAlert,
  Play,
  RefreshCw,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import {
  clearSkillRerankFeedback,
  promoteSkillRerankPolicy,
  readSkillRerankEvaluation,
  readSkillRerankPolicy,
  rollbackSkillRerankPolicy,
  SkillRerankApiError,
  startSkillRerankEvaluation,
  type SkillRerankEvaluation,
  type SkillRerankPolicyStatus,
} from "../utils/skillRerankApi";

const GATE_LABELS: Record<string, string> = {
  gold_cases_complete: "固定金标全部完成",
  recall_at_24_preserved: "Recall@24 无缺口",
  policy_violations_zero: "候选与信任策略违规为 0",
  provider_success_rate: "Provider 成功率不低于 95%",
  p95_latency: "P95 不高于 3 秒",
  mrr_regression: "MRR@6 下降不超过 0.01",
  ndcg_regression: "nDCG@6 下降不超过 0.01",
  meaningful_improvement: "至少一项提升不低于 0.03",
  near_miss_not_worse: "近似反例误召不增加",
  provider_identity_stable: "Provider 与模型身份一致",
};

function formatMetric(value: unknown, digits = 3) {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(digits)
    : "—";
}

function formatPercent(value: unknown) {
  return typeof value === "number" && Number.isFinite(value)
    ? `${(value * 100).toFixed(1)}%`
    : "—";
}

function formatTime(value: number | null | undefined) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value * 1000));
}

export default function SkillRerankGovernancePage() {
  const [status, setStatus] = useState<SkillRerankPolicyStatus | null>(null);
  const [evaluation, setEvaluation] = useState<SkillRerankEvaluation | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<"evaluate" | "promote" | "rollback" | "clear" | "">("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const next = await readSkillRerankPolicy();
      setStatus(next);
      setEvaluation(next.evaluations?.[0] ?? null);
    } catch (caught) {
      setError(
        caught instanceof SkillRerankApiError
          ? caught.message
          : "无法读取 Skill 重排治理状态。",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!evaluation || !["queued", "running"].includes(evaluation.status)) return;
    const timer = window.setInterval(() => {
      void readSkillRerankEvaluation(evaluation.evaluationId)
        .then((next) => {
          setEvaluation(next);
          if (!["queued", "running"].includes(next.status)) void load();
        })
        .catch(() => setError("评测状态刷新失败，请手动刷新。"));
    }, 2_000);
    return () => window.clearInterval(timer);
  }, [evaluation, load]);

  const baseline = evaluation?.baseline ?? null;
  const semantic = evaluation?.semantic ?? null;
  const providerLabel = status
    ? status.providerAvailable
      ? `${status.provider} 已配置`
      : `${status.provider} 不可用`
    : "读取中";
  const failedGates = useMemo(
    () => evaluation?.gates.filter((gate) => !gate.passed) ?? [],
    [evaluation],
  );
  const degradedCases = useMemo(
    () => evaluation?.caseReports?.filter((item) => item.fallbackReason) ?? [],
    [evaluation],
  );

  async function startEvaluation() {
    if (!status) return;
    setBusy("evaluate");
    setError("");
    setNotice("");
    try {
      const next = await startSkillRerankEvaluation(status.governanceRevision);
      setEvaluation(next);
      setNotice("固定金标评测已开始，可离开页面后再回来查看。 ");
      const refreshed = await readSkillRerankPolicy();
      setStatus(refreshed);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "启动评测失败。 ");
    } finally {
      setBusy("");
    }
  }

  async function promote() {
    if (!status || !evaluation?.eligibleForPromotion) return;
    if (!window.confirm("确认让 Runtime Router 使用当前语义排序吗？环境变量 off 仍可立即回退。")) return;
    setBusy("promote");
    setError("");
    try {
      const response = await promoteSkillRerankPolicy({
        expectedRevision: status.governanceRevision,
        evaluationId: evaluation.evaluationId,
      });
      setStatus(response.status);
      setNotice("Router 已晋级为真实语义重排。索引、模型或策略变化时会自动退回影子模式。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "晋级失败。");
    } finally {
      setBusy("");
    }
  }

  async function rollback() {
    if (!status || !window.confirm("确认立即恢复 Router 词典排序吗？")) return;
    setBusy("rollback");
    setError("");
    try {
      const response = await rollbackSkillRerankPolicy(status.governanceRevision);
      setStatus(response.status);
      setNotice("Router 已恢复影子模式，真实返回顺序为词典排序。 ");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "回退失败。 ");
    } finally {
      setBusy("");
    }
  }

  async function clearFeedback() {
    if (!status || !window.confirm("删除全部本地显式相关性反馈吗？此操作不会改变排序权重。")) return;
    setBusy("clear");
    setError("");
    try {
      await clearSkillRerankFeedback(status.governanceRevision);
      await load();
      setNotice("本地显式反馈已清空。 ");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "清理反馈失败。 ");
    } finally {
      setBusy("");
    }
  }

  return (
    <PageContainer activeResource="skills" hideSidebar maxWidthClassName="max-w-[1260px]">
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <Link className="inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-slate-300 transition hover:text-white" to="/skills">
          <ArrowLeft aria-hidden="true" size={16} />
          返回 Skill 市场
        </Link>
        <button className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-4 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] disabled:opacity-50" disabled={loading} onClick={() => void load()} type="button">
          <RefreshCw aria-hidden="true" className={loading ? "animate-spin motion-reduce:animate-none" : ""} size={16} />
          刷新状态
        </button>
      </div>

      <header className="border-y border-cyan-300/20 py-7 sm:py-9">
        <p className="text-sm font-semibold text-cyan-100">Skill 检索控制面</p>
        <h1 className="mt-2 text-3xl font-semibold text-white sm:text-4xl">语义重排治理</h1>
        <p className="mt-4 max-w-3xl text-sm leading-7 text-slate-300">
          对比固定金标、显式反馈和 Router 影子统计。评测通过后仍需本地控制台确认；不会自动训练、调权、安装或改变信任门。
        </p>
      </header>

      {error ? <div className="mt-6 rounded-lg border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-50" role="alert">{error}</div> : null}
      {notice ? <div className="mt-6 rounded-lg bg-emerald-300/10 px-4 py-3 text-sm text-emerald-50" role="status">{notice}</div> : null}

      <section className="mt-7 grid gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-label="当前治理状态">
        {[
          ["Provider", providerLabel],
          ["Router 环境模式", status?.routerMode ?? "—"],
          ["Router 生效模式", status?.effectiveRouterMode ?? "—"],
          ["本地显式反馈", status ? `${status.feedbackCount} 条` : "—"],
        ].map(([label, value]) => (
          <div className="rounded-lg bg-white/[0.055] p-4" key={label}>
            <p className="text-xs text-slate-400">{label}</p>
            <p className="mt-2 break-words text-lg font-semibold text-white">{value}</p>
          </div>
        ))}
      </section>

      {!status?.governanceAvailable ? (
        <section className="mt-6 rounded-lg border border-amber-300/25 bg-amber-300/10 p-5">
          <div className="flex items-start gap-3">
            <CircleAlert aria-hidden="true" className="mt-0.5 shrink-0 text-amber-200" size={19} />
            <div>
              <h2 className="font-semibold text-amber-50">治理 Store 不可用</h2>
              <p className="mt-2 text-sm leading-6 text-amber-100/80">反馈和晋级已停止；市场和 Router 会继续使用词典排序或影子模式，不覆盖损坏文件。</p>
            </div>
          </div>
        </section>
      ) : null}

      <section className="mt-8 grid min-w-0 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className="text-xl font-semibold text-white">固定金标评测</h2>
              <p className="mt-1 text-sm text-slate-400">不少于 60 条中英双语正例与近似反例，绑定当前目录指纹。</p>
            </div>
            <button className="inline-flex min-h-11 items-center gap-2 rounded-full bg-cyan-200 px-4 text-sm font-semibold text-ink-950 transition hover:bg-white disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500" disabled={!status?.governanceAvailable || !status.providerAvailable || busy === "evaluate" || evaluation?.status === "queued" || evaluation?.status === "running"} onClick={() => void startEvaluation()} type="button">
              <Play aria-hidden="true" size={16} />
              {busy === "evaluate" ? "正在启动…" : "运行固定评测"}
            </button>
          </div>

          {evaluation ? (
            <div className="mt-5 rounded-lg border border-white/10 bg-ink-950/55 p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-white">{evaluation.status === "completed" ? "评测已完成" : evaluation.status === "failed" ? "评测失败" : "评测运行中"}</p>
                  <p className="mt-1 break-all text-xs text-slate-500">{evaluation.evaluationId} · {formatTime(evaluation.createdAt)}</p>
                </div>
                <span className={`rounded-full px-3 py-1 text-xs font-semibold ${evaluation.eligibleForPromotion ? "bg-emerald-300/15 text-emerald-100" : evaluation.status === "failed" ? "bg-rose-300/15 text-rose-100" : "bg-amber-300/15 text-amber-100"}`}>
                  {evaluation.eligibleForPromotion ? "达到晋级门槛" : evaluation.status}
                </span>
              </div>
              {evaluation.status === "completed" ? (
                <p className="mt-3 text-xs text-slate-400">
                  实际身份：{evaluation.provider}
                  {evaluation.model ? ` / ${evaluation.model}` : " / 未报告模型"}
                </p>
              ) : null}

              {evaluation.status === "queued" || evaluation.status === "running" ? (
                <div className="mt-5 h-2 overflow-hidden rounded-full bg-white/10" role="status" aria-live="polite">
                  <div className="h-full w-1/2 animate-pulse rounded-full bg-cyan-300 motion-reduce:animate-none" />
                </div>
              ) : null}

              {baseline && semantic ? (
                <div className="mt-5 overflow-x-auto">
                  <table className="w-full min-w-[620px] text-left text-sm">
                    <thead className="text-xs text-slate-400"><tr><th className="pb-3 font-medium">指标</th><th className="pb-3 font-medium">词典基线</th><th className="pb-3 font-medium">语义重排</th></tr></thead>
                    <tbody className="divide-y divide-white/10 text-slate-200">
                      {([
                        ["Recall@24", baseline.recallAt24, semantic.recallAt24],
                        ["MRR@6", baseline.mrrAt6, semantic.mrrAt6],
                        ["nDCG@6", baseline.nDCGAt6, semantic.nDCGAt6],
                        ["Top-1", baseline.top1, semantic.top1],
                        ["近似反例误召", baseline.nearMissFalsePositiveRate, semantic.nearMissFalsePositiveRate],
                      ] as Array<[string, unknown, unknown]>).map(([label, left, right]) => (
                        <tr key={String(label)}><th className="py-3 font-medium text-white">{label}</th><td className="py-3">{formatMetric(left)}</td><td className="py-3">{formatMetric(right)}</td></tr>
                      ))}
                      <tr><th className="py-3 font-medium text-white">Provider 成功率</th><td className="py-3">—</td><td className="py-3">{formatPercent(semantic.providerSuccessRate)}</td></tr>
                      <tr><th className="py-3 font-medium text-white">P95 延迟</th><td className="py-3">—</td><td className="py-3">{typeof semantic.p95DurationMs === "number" ? `${semantic.p95DurationMs} ms` : "—"}</td></tr>
                    </tbody>
                  </table>
                </div>
              ) : null}

              {degradedCases.length ? (
                <div className="mt-5 border-t border-white/10 pt-4">
                  <h3 className="text-sm font-semibold text-white">失败或降级用例</h3>
                  <p className="mt-1 text-xs leading-5 text-slate-400">
                    仅展示固定 case ID 和脱敏错误码，不保存原始查询。
                  </p>
                  <div className="mt-3 space-y-2">
                    {degradedCases.slice(0, 12).map((item) => (
                      <div
                        className="grid min-w-0 gap-1 rounded-md bg-amber-300/10 px-3 py-2 text-xs sm:grid-cols-[minmax(0,1fr)_auto]"
                        key={item.caseId}
                      >
                        <span className="min-w-0 break-all font-semibold text-amber-50">
                          {item.caseId} · {item.fallbackReason}
                        </span>
                        <span className="text-amber-100/70">
                          {item.durationMs} ms · 名次变化 {item.rankChanges}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="mt-5 rounded-lg bg-white/[0.045] p-5 text-sm leading-6 text-slate-400">尚未运行语义评测。影子统计不会替代固定金标。</div>
          )}
        </div>

        <aside className="min-w-0 space-y-5">
          <section className="rounded-lg border border-white/10 bg-white/[0.045] p-4">
            <h2 className="font-semibold text-white">晋级门槛</h2>
            <div className="mt-4 space-y-3">
              {(evaluation?.gates ?? []).map((gate) => (
                <div className="flex items-start gap-2 text-xs leading-5" key={gate.code}>
                  {gate.passed ? <CheckCircle2 aria-hidden="true" className="mt-0.5 shrink-0 text-emerald-300" size={15} /> : <CircleAlert aria-hidden="true" className="mt-0.5 shrink-0 text-amber-300" size={15} />}
                  <span className={gate.passed ? "text-slate-300" : "text-amber-100"}>{GATE_LABELS[gate.code] ?? gate.code}</span>
                </div>
              ))}
              {!evaluation?.gates.length ? <p className="text-xs leading-5 text-slate-500">评测完成后显示全部硬门槛。</p> : null}
            </div>
            {failedGates.length ? <p className="mt-4 text-xs leading-5 text-amber-100">仍有 {failedGates.length} 项未满足，不能晋级。</p> : null}
            <button className="mt-4 min-h-11 w-full rounded-full bg-emerald-200 px-4 text-sm font-semibold text-ink-950 transition hover:bg-white disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500" disabled={!evaluation?.eligibleForPromotion || busy === "promote" || !status?.governanceAvailable} onClick={() => void promote()} type="button">{busy === "promote" ? "正在确认…" : "确认晋级 Router"}</button>
            <button className="mt-2 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-full border border-white/15 px-4 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06] disabled:opacity-50" disabled={!status || status.effectiveRouterMode !== "on" || busy === "rollback"} onClick={() => void rollback()} type="button"><RotateCcw aria-hidden="true" size={15} />立即回退词典排序</button>
          </section>

          <section className="rounded-lg bg-white/[0.045] p-4">
            <h2 className="font-semibold text-white">影子与反馈</h2>
            <dl className="mt-4 space-y-3 text-xs">
              <div className="flex justify-between gap-3"><dt className="text-slate-400">影子样本</dt><dd className="font-semibold text-white">{status?.shadow?.sampleCount ?? 0}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-slate-400">降级率</dt><dd className="font-semibold text-white">{formatPercent(status?.shadow?.fallbackRate)}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-slate-400">P95</dt><dd className="font-semibold text-white">{status?.shadow ? `${status.shadow.p95DurationMs} ms` : "—"}</dd></div>
              <div className="flex justify-between gap-3"><dt className="text-slate-400">显式反馈</dt><dd className="font-semibold text-white">{status?.feedbackCount ?? 0}</dd></div>
            </dl>
            {evaluation?.feedbackSummary ? (
              <div className="mt-4 border-t border-white/10 pt-4 text-xs leading-5 text-slate-400">
                <p>
                  相关 {evaluation.feedbackSummary.relevantCount ?? 0} 条；不相关 {evaluation.feedbackSummary.notRelevantCount ?? 0} 条
                </p>
                <p className="mt-1">
                  语义名次未变差：相关 {evaluation.feedbackSummary.relevantNonWorseCount ?? 0} 条，不相关 {evaluation.feedbackSummary.irrelevantNonWorseCount ?? 0} 条
                </p>
              </div>
            ) : null}
            <button className="mt-4 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-full border border-rose-300/25 px-4 text-sm font-semibold text-rose-100 transition hover:bg-rose-300/10 disabled:opacity-50" disabled={!status?.feedbackCount || busy === "clear"} onClick={() => void clearFeedback()} type="button"><Trash2 aria-hidden="true" size={15} />清空本地反馈</button>
          </section>
        </aside>
      </section>
    </PageContainer>
  );
}
