import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  LoaderCircle,
  Play,
  RefreshCw,
  SlidersHorizontal,
  Square,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

type Objective = "balanced" | "quality" | "low_latency";
type RunScope = "optimization_only" | "full";

interface PipelineVersion {
  version_id: string;
  version: number;
  status: string;
  active: boolean;
  chunk_count: number;
}

interface EvaluationSet {
  eval_set_id: string;
  name: string;
  latest_version?: number | null;
}

interface EvaluationSetVersion {
  version_id: string;
  version: number;
  cases: unknown[];
  checksum: string;
}

interface Recommendation {
  recommendation_id: string;
  state: string;
  created_at: number;
}

interface Metrics {
  recall_at_5?: number;
  mrr_at_10?: number;
  ndcg_at_10?: number;
  citation_coverage?: number;
  no_result_accuracy?: number;
  p95_latency_ms?: number;
}

interface Finalist {
  candidate_id: string;
  retrieval: {
    mode: string;
    top_k: number;
    score_threshold: number;
    vector_weight?: number;
    abstention_enabled?: boolean;
    abstention_score_domain?: string;
    abstention_threshold?: number;
  };
  holdout_metrics: Metrics;
  cost: { chunk_count: number; estimated_index_bytes: number; size_is_estimated: boolean };
  promotion_gate: { passed: boolean };
  improvement: { effective: boolean };
  rerank_call_count: number;
  threshold_selection_reason?: string | null;
  statistical_validation?: {
    validation_version: string;
    passed: boolean;
    quality_delta?: number;
    confidence_level?: number;
    confidence_interval?: { lower: number; upper: number };
    stable_resample_count?: number;
    required_stable_resamples?: number;
    query_repetitions?: number;
  };
}

interface OptimizationCandidate {
  candidate_id: string;
  retrieval: {
    mode: string;
    top_k: number;
  };
  optimization_metrics: Metrics;
  promotion_target_diagnostics?: {
    maximum_no_result_accuracy_at_required_recall?: number;
  };
  optimization_gate?: { passed?: boolean };
  ineligible_reason?: string;
}

interface TuningRun {
  run_id: string;
  request?: {
    run_scope?: RunScope;
    enable_rerank?: boolean;
  };
  status: string;
  stage: string;
  progress: number;
  warnings: string[];
  candidates?: OptimizationCandidate[];
  finalists: Finalist[];
  pareto_front: string[];
  final_version_id?: string | null;
  evaluation_run_id?: string | null;
  winner?: (Finalist & { materialized_version_id?: string }) | null;
  no_improvement_reason?: "optimization_gate" | "holdout_gate" | "statistical_gate" | "full_evaluation_gate" | null;
  optimization_gate_summary?: {
    evaluated_count?: number;
    passed_count?: number;
    eligible_count?: number;
    failed_check_counts?: Record<string, number>;
  };
  chunk_sensitivity?: {
    status: "sufficient" | "insufficient" | "not_measured" | "not_applicable";
    probe_count: number;
    unique_realized_outcomes: number;
  };
  retrieval_deduplication?: {
    candidate_count: number;
    unique_semantic_outcomes: number;
    duplicate_count: number;
  };
  statistical_summary?: {
    evaluated_finalist_count: number;
    statistically_non_degrading_count: number;
    eligible_count: number;
    query_repetitions: number;
    resample_count: number;
    confidence_level: number;
  };
  scope_receipt?: {
    receipt_version: "rag-strategy-run-scope-v1";
    requested_scope: RunScope;
    effective_stop: "after_optimization_gate" | "full_pipeline";
    optimization_candidate_count: number;
    optimization_eligible_count: number;
    holdout_query_executions: number;
    finalist_count: number;
    materialization_attempted: boolean;
    evaluation_run_created: boolean;
  };
  error?: string | null;
}

interface TuningReadiness {
  status: "ready" | "report_only" | "insufficient_data";
  benchmark_role: "unclassified" | "regression_guard" | "strategy_tuning" | "promotion_evidence";
  selection_eligible: boolean;
  evidence_strength: string;
  counts: {
    total: number;
    positive: number;
    no_result: number;
    reviewed_hard_negative: number;
  };
  dimensions: {
    retrieval: { eligible: boolean };
    threshold: { eligible: boolean };
    chunking: { eligible: boolean; sensitivity_probe_required: boolean };
  };
  checks: Array<{
    check_id: string;
    passed: boolean;
    severity: string;
    actual: unknown;
    required: unknown;
    message: string;
  }>;
  blockers: string[];
  warnings: string[];
}

interface Preflight {
  snapshot_hash: string;
  run_scope: RunScope;
  eval_case_count: number;
  chunk_tuning_available: boolean;
  threshold_tuning_available: boolean;
  benchmark_role: TuningReadiness["benchmark_role"];
  selection_eligible: boolean;
  tuning_readiness: TuningReadiness;
  retrieval_only: boolean;
  embedding_degraded: boolean;
  rerank_available: boolean;
  execution_budget?: {
    candidate_profile_upper_bound: number;
    optimization_query_executions_upper_bound: number;
    trial_index_builds_upper_bound: number;
    holdout_query_executions_upper_bound: number;
    rerank_query_executions_upper_bound: number;
    formal_query_executions_upper_bound: number;
    total_retrieval_query_executions_upper_bound: number;
  };
  warnings: string[];
}

interface Props {
  kbId: string;
  open: boolean;
  onClose: () => void;
  onCompleted: () => Promise<void> | void;
}

const ACTIVE_STATUSES = new Set([
  "queued",
  "profiling",
  "searching",
  "building",
  "evaluating",
  "materializing",
  "validating",
]);

function errorMessage(value: unknown, fallback: string) {
  if (!value || typeof value !== "object") return fallback;
  const detail = (value as { detail?: unknown }).detail;
  return typeof detail === "string" ? detail : fallback;
}

function percent(value?: number) {
  return value == null ? "-" : `${(value * 100).toFixed(1)}%`;
}

function confidenceInterval(item: Finalist) {
  const interval = item.statistical_validation?.confidence_interval;
  if (!interval) return "-";
  return `${(interval.lower * 100).toFixed(1)}% ~ ${(interval.upper * 100).toFixed(1)}%`;
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    profiling: "固定快照",
    searching: "检索搜索",
    building: "构建 Trial",
    evaluating: "Holdout 验证",
    materializing: "物化胜者",
    validating: "完整评测",
    completed: "已完成",
    no_improvement: "无有效改善",
    failed: "失败",
    cancelled: "已取消",
  };
  return labels[status] || status;
}

function readinessLabel(status: TuningReadiness["status"]) {
  if (status === "ready") return "可进入调优";
  if (status === "report_only") return "仅回归报告";
  return "证据不足";
}

function benchmarkRoleLabel(role: TuningReadiness["benchmark_role"]) {
  const labels: Record<TuningReadiness["benchmark_role"], string> = {
    unclassified: "未分类",
    regression_guard: "回归护栏",
    strategy_tuning: "策略调优",
    promotion_evidence: "推广证据",
  };
  return labels[role];
}

function chunkSensitivityLabel(status: NonNullable<TuningRun["chunk_sensitivity"]>["status"]) {
  const labels: Record<NonNullable<TuningRun["chunk_sensitivity"]>["status"], string> = {
    sufficient: "候选产生可区分结果",
    insufficient: "候选等价，已降级为仅检索",
    not_measured: "探针不足",
    not_applicable: "不适用",
  };
  return labels[status];
}

export default function RagStrategyTunerPanel({ kbId, open, onClose, onCompleted }: Props) {
  const [versions, setVersions] = useState<PipelineVersion[]>([]);
  const [sets, setSets] = useState<EvaluationSet[]>([]);
  const [evaluationVersions, setEvaluationVersions] = useState<EvaluationSetVersion[]>([]);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [baseVersionId, setBaseVersionId] = useState("");
  const [evalSetId, setEvalSetId] = useState("");
  const [evalVersion, setEvalVersion] = useState("");
  const [recommendationId, setRecommendationId] = useState("");
  const [objective, setObjective] = useState<Objective>("balanced");
  const [runScope, setRunScope] = useState<RunScope>("optimization_only");
  const [enableRerank, setEnableRerank] = useState(false);
  const [maxChunkIndexes, setMaxChunkIndexes] = useState(1);
  const [maxRetrievalTrials, setMaxRetrievalTrials] = useState(3);
  const [maxFinalists, setMaxFinalists] = useState(1);
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [run, setRun] = useState<TuningRun | null>(null);
  const [busy, setBusy] = useState<"load" | "preflight" | "start" | "cancel" | "retry" | "">("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setBusy("load");
    setError("");
    try {
      const [versionResponse, setResponse, recommendationResponse] = await Promise.all([
        fetch(`/api/rag/pipeline/versions?kb_id=${encodeURIComponent(kbId)}`),
        fetch(`/api/rag/evaluation-sets?kb_id=${encodeURIComponent(kbId)}`),
        fetch(`/api/rag/strategy-router/recommendations?kb_id=${encodeURIComponent(kbId)}`),
      ]);
      if (!versionResponse.ok || !setResponse.ok) throw new Error("调优资源加载失败。");
      const nextVersions = ((await versionResponse.json()) as { versions: PipelineVersion[] }).versions || [];
      const nextSets = ((await setResponse.json()) as { evaluation_sets: EvaluationSet[] }).evaluation_sets || [];
      setVersions(nextVersions.filter((item) => item.status === "ready" || item.status === "active"));
      setSets(nextSets.filter((item) => Number(item.latest_version || 0) > 0));
      setBaseVersionId((current) => current || nextVersions.find((item) => item.active)?.version_id || nextVersions[0]?.version_id || "");
      setEvalSetId((current) => current || nextSets.find((item) => Number(item.latest_version || 0) > 0)?.eval_set_id || "");
      if (recommendationResponse.ok) {
        const nextRecommendations = ((await recommendationResponse.json()) as { recommendations: Recommendation[] }).recommendations || [];
        setRecommendations(nextRecommendations.filter((item) => item.state === "ready" || item.state === "applied"));
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "调优资源加载失败。");
    } finally {
      setBusy("");
    }
  }, [kbId]);

  const loadSetVersions = useCallback(async () => {
    if (!evalSetId) {
      setEvaluationVersions([]);
      setEvalVersion("");
      return;
    }
    const response = await fetch(`/api/rag/evaluation-sets/${encodeURIComponent(evalSetId)}/versions`);
    if (!response.ok) return;
    const values = ((await response.json()) as { versions: EvaluationSetVersion[] }).versions || [];
    setEvaluationVersions(values);
    setEvalVersion((current) => current || String(values[0]?.version || ""));
  }, [evalSetId]);

  useEffect(() => { if (open) void load(); }, [load, open]);
  useEffect(() => { if (open) void loadSetVersions(); }, [loadSetVersions, open]);
  useEffect(() => {
    setPreflight(null);
  }, [baseVersionId, evalSetId, evalVersion, recommendationId, objective, runScope, enableRerank, maxChunkIndexes, maxRetrievalTrials, maxFinalists]);

  useEffect(() => {
    if (!run || !ACTIVE_STATUSES.has(run.status)) return undefined;
    const timer = window.setInterval(async () => {
      const response = await fetch(`/api/rag/strategy-tuner/runs/${encodeURIComponent(run.run_id)}`);
      if (!response.ok) return;
      const next = (await response.json()) as TuningRun;
      setRun(next);
      if (!ACTIVE_STATUSES.has(next.status) && next.status === "completed") await onCompleted();
    }, 1800);
    return () => window.clearInterval(timer);
  }, [onCompleted, run]);

  function requestBody(expectedSnapshotHash?: string) {
    return {
      kb_id: kbId,
      base_version_id: baseVersionId,
      eval_set_id: evalSetId,
      eval_set_version: Number(evalVersion),
      recommendation_id: recommendationId || null,
      objective,
      run_scope: runScope,
      seed: 42,
      max_chunk_indexes: maxChunkIndexes,
      max_retrieval_trials: maxRetrievalTrials,
      max_finalists: maxFinalists,
      enable_rerank: runScope === "full" && enableRerank,
      rerank_provider: "auto",
      ...(expectedSnapshotHash ? { expected_snapshot_hash: expectedSnapshotHash } : {}),
    };
  }

  async function runPreflight() {
    setBusy("preflight");
    setError("");
    try {
      const response = await fetch("/api/rag/strategy-tuner/preflight", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody()),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorMessage(payload, "调优预检失败。"));
      setPreflight(payload as Preflight);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "调优预检失败。");
    } finally {
      setBusy("");
    }
  }

  async function startRun() {
    setBusy("start");
    setError("");
    try {
      const response = await fetch("/api/rag/strategy-tuner/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody(preflight?.snapshot_hash)),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorMessage(payload, "调优任务创建失败。"));
      setRun(payload as TuningRun);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "调优任务创建失败。");
    } finally {
      setBusy("");
    }
  }

  async function cancelRun() {
    if (!run) return;
    setBusy("cancel");
    const response = await fetch(`/api/rag/strategy-tuner/runs/${encodeURIComponent(run.run_id)}/cancel`, { method: "POST" });
    if (response.ok) setRun((await response.json()) as TuningRun);
    else setError(errorMessage(await response.json().catch(() => null), "取消失败。"));
    setBusy("");
  }

  async function retryRun() {
    if (!run) return;
    const originalScope = run.request?.run_scope ?? "full";
    setRunScope(originalScope);
    setEnableRerank(originalScope === "full" && Boolean(run.request?.enable_rerank));
    setBusy("retry");
    setError("");
    const response = await fetch(
      `/api/rag/strategy-tuner/runs/${encodeURIComponent(run.run_id)}/retry`,
      { method: "POST" },
    );
    const payload = await response.json().catch(() => null);
    if (response.ok) setRun(payload as TuningRun);
    else setError(errorMessage(payload, "重试失败。"));
    setBusy("");
  }

  if (!open) return null;
  const canPreflight = Boolean(baseVersionId && evalSetId && evalVersion);
  const active = Boolean(run && ACTIVE_STATUSES.has(run.status));
  const optimizationPassed = Boolean(
    run?.status === "completed"
    && run.scope_receipt?.requested_scope === "optimization_only"
    && (run.scope_receipt.optimization_eligible_count ?? 0) > 0,
  );
  const failedOptimizationChecks = Object.entries(
    run?.optimization_gate_summary?.failed_check_counts ?? {},
  ).filter(([, count]) => count > 0);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-surface-950/70" role="dialog" aria-modal="true" aria-label="RAG 评测调优">
      <button className="absolute inset-0 cursor-default" aria-label="关闭评测调优" onClick={onClose} type="button" />
      <aside className="relative flex h-full w-full max-w-[720px] flex-col border-l border-white/10 bg-surface-950 shadow-2xl">
        <header className="flex items-start justify-between gap-4 border-b border-white/10 px-5 py-4">
          <div className="flex min-w-0 gap-3">
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-emerald-300/10 text-emerald-200"><SlidersHorizontal size={18} /></span>
            <div><h2 className="text-base font-semibold text-white">Benchmark 驱动的策略调优</h2><p className="mt-1 text-xs leading-5 text-slate-400">固定知识与 Gold 快照，搜索分块和检索参数；胜者仍需显式推广。</p></div>
          </div>
          <button className="grid h-9 w-9 place-items-center rounded-md text-slate-400 hover:bg-white/[0.06] hover:text-white" onClick={onClose} title="关闭" type="button"><X size={18} /></button>
        </header>

        <div className="flex-1 overflow-y-auto">
          <section className="border-b border-white/10 px-5 py-5">
            <div className="grid gap-3 sm:grid-cols-2">
              <Select label="固定知识版本" value={baseVersionId} onChange={setBaseVersionId} options={versions.map((item) => ({ value: item.version_id, label: `v${item.version}${item.active ? " · active" : ""} · ${item.chunk_count} chunks` }))} />
              <Select label="已发布评测集" value={evalSetId} onChange={(value) => { setEvalSetId(value); setEvalVersion(""); }} options={sets.map((item) => ({ value: item.eval_set_id, label: `${item.name} · v${item.latest_version}` }))} />
              <Select label="评测版本" value={evalVersion} onChange={setEvalVersion} options={evaluationVersions.map((item) => ({ value: String(item.version), label: `v${item.version} · ${item.cases.length} cases` }))} />
              <Select label="Router 推荐（可选）" value={recommendationId} onChange={setRecommendationId} options={[{ value: "", label: "仅使用当前配置与规则邻域" }, ...recommendations.map((item) => ({ value: item.recommendation_id, label: `${item.state} · ${new Date(item.created_at * 1000).toLocaleString("zh-CN", { hour12: false })}` }))]} />
            </div>
            <div className="mt-4 grid grid-cols-3 gap-2" role="group" aria-label="调优目标">
              {([['balanced', '均衡'], ['quality', '质量优先'], ['low_latency', '低延迟']] as Array<[Objective, string]>).map(([value, label]) => <button className={`rounded-md border px-3 py-2 text-xs font-semibold ${objective === value ? "border-emerald-300/45 bg-emerald-300/10 text-emerald-50" : "border-white/10 text-slate-300 hover:bg-white/[0.05]"}`} key={value} onClick={() => setObjective(value)} type="button">{label}</button>)}
            </div>
            <fieldset className="mt-4 border-t border-white/10 pt-4">
              <legend className="text-xs font-semibold text-slate-300">执行范围</legend>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                <label className={`flex cursor-pointer items-start gap-3 rounded-md border px-3 py-3 text-xs leading-5 ${runScope === "optimization_only" ? "border-emerald-300/45 bg-emerald-300/10 text-emerald-50" : "border-white/10 text-slate-300 hover:bg-white/[0.05]"}`}>
                  <input checked={runScope === "optimization_only"} className="mt-1 h-4 w-4 shrink-0 accent-emerald-300" disabled={active} name="strategy-tuner-run-scope" onChange={() => { setRunScope("optimization_only"); setEnableRerank(false); setMaxChunkIndexes(1); setMaxRetrievalTrials(3); setMaxFinalists(1); }} type="radio" value="optimization_only" />
                  <span><strong className="block text-slate-100">仅优化集（建议）</strong>保存候选指标和特征证据；不会执行 Holdout、候选物化或 Formal。</span>
                </label>
                <label className={`flex cursor-pointer items-start gap-3 rounded-md border px-3 py-3 text-xs leading-5 ${runScope === "full" ? "border-amber-300/45 bg-amber-300/10 text-amber-50" : "border-white/10 text-slate-300 hover:bg-white/[0.05]"}`}>
                  <input checked={runScope === "full"} className="mt-1 h-4 w-4 shrink-0 accent-amber-300" disabled={active} name="strategy-tuner-run-scope" onChange={() => { setRunScope("full"); setMaxChunkIndexes(4); setMaxRetrievalTrials(24); setMaxFinalists(3); }} type="radio" value="full" />
                  <span><strong className="block text-slate-100">完整调优</strong>候选通过后进入重复 Holdout，并可能物化候选和创建完整评测；仍不自动激活。</span>
                </label>
              </div>
            </fieldset>
            <fieldset className="mt-4 border-t border-white/10 pt-4">
              <legend className="text-xs font-semibold text-slate-300">执行预算</legend>
              <div className="mt-2 grid gap-3 sm:grid-cols-3">
                <NumberInput disabled={active} label="分块索引上限" max={4} min={1} onChange={setMaxChunkIndexes} value={maxChunkIndexes} />
                <NumberInput disabled={active} label="检索候选上限" max={24} min={1} onChange={setMaxRetrievalTrials} value={maxRetrievalTrials} />
                <NumberInput disabled={active} label="Finalist 上限" max={3} min={1} onChange={setMaxFinalists} value={maxFinalists} />
              </div>
            </fieldset>
            <label className={`mt-4 flex items-start gap-3 border-t border-white/10 pt-4 text-xs leading-5 ${runScope === "full" ? "text-slate-300" : "text-slate-500"}`}><input checked={enableRerank} className="mt-1 h-4 w-4 accent-emerald-300" disabled={active || runScope !== "full"} onChange={(event) => setEnableRerank(event.target.checked)} type="checkbox" /><span><strong className={runScope === "full" ? "block text-slate-100" : "block text-slate-400"}>显式授权 finalist Rerank 实测</strong>{runScope === "full" ? "默认关闭；最多对两个非 Rerank finalist 调用已配置 Provider。" : "仅完整调优会产生 finalist；切换到完整调优后才能授权。"}</span></label>
            <div className="mt-4 flex gap-2">
              <button className="inline-flex flex-1 items-center justify-center gap-2 rounded-md border border-white/10 px-3 py-2 text-sm font-semibold text-slate-200 hover:bg-white/[0.06] disabled:opacity-45" disabled={!canPreflight || Boolean(busy) || active} onClick={() => void runPreflight()} type="button">{busy === "preflight" ? <LoaderCircle className="animate-spin" size={15} /> : <RefreshCw size={15} />}运行预检</button>
              <button className="inline-flex flex-1 items-center justify-center gap-2 rounded-md bg-emerald-300 px-3 py-2 text-sm font-bold text-surface-950 hover:bg-emerald-200 disabled:opacity-45" disabled={!preflight?.selection_eligible || Boolean(busy) || active} onClick={() => void startRun()} type="button"><Play size={15} />{runScope === "optimization_only" ? "运行优化集" : "开始完整调优"}</button>
            </div>
            <p className="mt-2 text-[10px] leading-4 text-slate-500">当前请求：最多 {maxChunkIndexes} 个分块索引、{maxRetrievalTrials} 组检索候选和 {maxFinalists} 个 finalist。预检后才会锁定执行上限；不会修改 Draft 或活动索引。</p>
          </section>

          {preflight ? <section className="border-b border-white/10 px-5 py-5">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-white">调优资格与固定快照</h3>
              <span className={`px-2 py-1 text-[11px] font-semibold ${preflight.selection_eligible ? "bg-emerald-300/10 text-emerald-200" : "bg-amber-300/10 text-amber-100"}`}>{readinessLabel(preflight.tuning_readiness.status)}</span>
            </div>
            <p className="mt-2 text-xs leading-5 text-slate-400">评测角色：{benchmarkRoleLabel(preflight.benchmark_role)}。标准回归 Pack 只能验证引擎一致性，不能单独选择调优胜者。</p>
            <p className="mt-2 text-xs leading-5 text-slate-400">执行范围：{preflight.run_scope === "optimization_only" ? "仅优化集，Optimization Gate 后终止" : "完整调优，可进入 Holdout 与候选物化"}。</p>
            <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
              <Metric label="正样例" value={`${preflight.tuning_readiness.counts.positive}/30`} />
              <Metric label="困难负例" value={`${preflight.tuning_readiness.counts.reviewed_hard_negative}/12`} />
              <Metric label="分块搜索" value={preflight.chunk_tuning_available ? "探针后可用" : "仅检索"} />
              <Metric label="阈值搜索" value={preflight.threshold_tuning_available ? "可用" : "固定基线"} />
              <Metric label="Embedding" value={preflight.embedding_degraded ? "Hash 降级" : "真实向量"} />
              <Metric label="Rerank" value={preflight.rerank_available ? "可用" : "不可用"} />
            </div>
            {preflight.execution_budget ? <p className="mt-4 rounded-md border border-cyan-300/15 bg-cyan-300/[0.05] px-3 py-2 text-xs leading-5 text-cyan-50">检索查询执行上限 {preflight.execution_budget.total_retrieval_query_executions_upper_bound} 次；其中优化集 {preflight.execution_budget.optimization_query_executions_upper_bound} 次、Holdout {preflight.execution_budget.holdout_query_executions_upper_bound} 次、Formal {preflight.execution_budget.formal_query_executions_upper_bound} 次。Trial 索引构建最多 {preflight.execution_budget.trial_index_builds_upper_bound} 次，Rerank 查询最多 {preflight.execution_budget.rerank_query_executions_upper_bound} 次。</p> : null}
            {preflight.tuning_readiness.checks.filter((item) => !item.passed).map((item) => <p className={`mt-3 flex gap-2 text-xs leading-5 ${item.severity === "blocker" ? "text-rose-100" : "text-amber-100"}`} key={item.check_id}><AlertTriangle className="mt-0.5 shrink-0" size={14} />{item.message}</p>)}
          </section> : null}

          {run ? <section className="px-5 py-5"><div className="flex items-center justify-between gap-3"><div><h3 className="text-sm font-semibold text-white">调优运行</h3><p className="mt-1 font-mono text-[10px] text-slate-500">{run.run_id}</p></div><span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${optimizationPassed || (run.status === "completed" && run.scope_receipt?.requested_scope !== "optimization_only") ? "bg-emerald-300/10 text-emerald-200" : run.status === "failed" ? "bg-rose-300/10 text-rose-200" : run.status === "completed" ? "bg-amber-300/10 text-amber-100" : "bg-cyan-300/10 text-cyan-200"}`}>{statusLabel(run.status)}</span></div><div className="mt-4 h-2 overflow-hidden rounded-full bg-white/[0.06]"><div className="h-full bg-emerald-300 transition-[width]" style={{ width: `${Math.max(2, run.progress)}%` }} /></div><p className="mt-2 text-xs text-slate-400">{run.stage} · {run.progress}%</p>
            <p className="mt-2 text-xs text-slate-400">本次范围：{(run.request?.run_scope ?? "full") === "optimization_only" ? "仅优化集" : "完整调优"}。</p>
            {active ? <button className="mt-4 inline-flex items-center gap-2 rounded-md border border-rose-300/25 px-3 py-2 text-xs font-semibold text-rose-100 hover:bg-rose-300/10 disabled:opacity-45" disabled={busy === "cancel"} onClick={() => void cancelRun()} type="button"><Square size={13} />取消后续搜索</button> : null}
            {run.status === "failed" || run.status === "cancelled" ? <button className="mt-4 inline-flex items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-slate-100 hover:bg-white/[0.06] disabled:opacity-45" disabled={busy === "retry"} onClick={() => void retryRun()} type="button">{busy === "retry" ? <LoaderCircle className="animate-spin" size={13} /> : <RefreshCw size={13} />}重新开始搜索</button> : null}
            {run.error ? <p className="mt-4 rounded-md bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-100">{run.error}</p> : null}
            {run.chunk_sensitivity?.status ? <p className="mt-4 text-xs leading-5 text-slate-400">分块敏感性：{chunkSensitivityLabel(run.chunk_sensitivity.status)} · {run.chunk_sensitivity.unique_realized_outcomes}/{run.chunk_sensitivity.probe_count} 个真实结果指纹。</p> : null}
            {run.retrieval_deduplication?.candidate_count ? <p className="mt-2 text-xs leading-5 text-slate-400">检索语义去重：{run.retrieval_deduplication.unique_semantic_outcomes}/{run.retrieval_deduplication.candidate_count} 个独立结果，排除 {run.retrieval_deduplication.duplicate_count} 个等价候选。</p> : null}
            {run.statistical_summary ? <p className="mt-2 text-xs leading-5 text-slate-400">稳健验证：固定 Holdout 内 {run.statistical_summary.resample_count} 组分层重采样，每题查询 {run.statistical_summary.query_repetitions} 次后取中位延迟；{run.statistical_summary.statistically_non_degrading_count}/{run.statistical_summary.evaluated_finalist_count} 个 finalist 通过统计非退化门禁。</p> : null}
            {run.status === "no_improvement" ? <p className="mt-4 rounded-md bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-100">{run.no_improvement_reason === "optimization_gate" ? `优化集上没有候选通过当前质量门禁，因此未进入 Holdout、也未生成候选版本。已检查 ${run.optimization_gate_summary?.evaluated_count ?? 0} 个配置。` : run.no_improvement_reason === "statistical_gate" ? "候选在单点指标上达到改善，但配对重采样的质量区间无法证明其稳定非退化，因此未生成版本。" : run.evaluation_run_id ? "Holdout 胜者已生成候选版本，但未通过完整评测集复核，因此不可推广；可查看下方候选与评测证据。" : "固定 Holdout 上没有候选同时通过质量门禁并达到有效改善阈值，因此未生成版本。"}</p> : null}
            {run.status === "completed" && run.scope_receipt?.requested_scope === "optimization_only" ? <div className={`mt-4 flex gap-3 rounded-md border px-3 py-3 ${optimizationPassed ? "border-emerald-300/20 bg-emerald-300/[0.07]" : "border-amber-300/20 bg-amber-300/[0.07]"}`}>{optimizationPassed ? <CheckCircle2 className="mt-0.5 shrink-0 text-emerald-300" size={17} /> : <AlertTriangle className="mt-0.5 shrink-0 text-amber-200" size={17} />}<div><p className={`text-sm font-semibold ${optimizationPassed ? "text-emerald-50" : "text-amber-50"}`}>{optimizationPassed ? "执行完成，候选通过优化门禁" : "执行完成，质量门禁未通过"}</p><p className="mt-1 text-xs leading-5 text-slate-300">已检查 {run.scope_receipt.optimization_candidate_count} 个候选，其中 {run.scope_receipt.optimization_eligible_count} 个通过 Optimization Gate。Holdout 查询为 0，未物化候选、未创建 Formal 评测。</p>{failedOptimizationChecks.length ? <p className="mt-2 text-[11px] leading-5 text-amber-100">失败检查：{failedOptimizationChecks.map(([check, count]) => `${check} (${count})`).join("、")}</p> : null}</div></div> : null}
            {run.candidates?.length ? <div className="mt-5 overflow-x-auto border-t border-white/10 pt-4"><h4 className="mb-3 text-xs font-semibold text-slate-200">优化候选证据</h4><table className="w-full min-w-[700px] text-left text-xs"><thead className="text-slate-500"><tr><th className="pb-2 font-semibold">配置</th><th className="pb-2 font-semibold">Recall@5</th><th className="pb-2 font-semibold">No-result</th><th className="pb-2 font-semibold">达标 Recall 时最大 No-result</th><th className="pb-2 font-semibold">P95</th><th className="pb-2 font-semibold">门禁</th></tr></thead><tbody>{run.candidates.map((item) => <tr className="border-t border-white/[0.07] text-slate-300" key={item.candidate_id}><td className="py-3"><span className="font-semibold text-white">{item.retrieval.mode}</span><span className="ml-2 text-slate-500">K={item.retrieval.top_k}</span></td><td className="py-3">{percent(item.optimization_metrics.recall_at_5)}</td><td className="py-3">{percent(item.optimization_metrics.no_result_accuracy)}</td><td className="py-3">{percent(item.promotion_target_diagnostics?.maximum_no_result_accuracy_at_required_recall)}</td><td className="py-3">{item.optimization_metrics.p95_latency_ms?.toFixed(1) ?? "-"} ms</td><td className="py-3">{item.optimization_gate?.passed ? <span className="text-emerald-200">通过</span> : <span className="text-amber-100">未通过{item.ineligible_reason ? ` · ${item.ineligible_reason}` : ""}</span>}</td></tr>)}</tbody></table></div> : null}
            {run.finalists.length ? <div className="mt-5 overflow-x-auto border-t border-white/10 pt-4"><table className="w-full min-w-[820px] text-left text-xs"><thead className="text-slate-500"><tr><th className="pb-2 font-semibold">配置</th><th className="pb-2 font-semibold">nDCG@10</th><th className="pb-2 font-semibold">Recall@5</th><th className="pb-2 font-semibold">No-result</th><th className="pb-2 font-semibold">稳健 P95</th><th className="pb-2 font-semibold">质量差异 90% CI</th><th className="pb-2 font-semibold">门禁</th></tr></thead><tbody>{run.finalists.map((item) => <tr className="border-t border-white/[0.07] text-slate-300" key={item.candidate_id}><td className="py-3"><span className="font-semibold text-white">{item.retrieval.mode}</span><span className="ml-2 text-slate-500">K={item.retrieval.top_k} · Fused 预滤={item.retrieval.score_threshold.toFixed(3)}</span>{item.retrieval.abstention_enabled ? <span className="mt-1 block text-slate-500">拒答：{item.retrieval.abstention_score_domain ?? "vector_score"} &lt; {(item.retrieval.abstention_threshold ?? 0).toFixed(3)}</span> : <span className="mt-1 block text-slate-600">拒答：关闭</span>}{["hard_negative_abstention_improved", "hard_negative_false_positive_improved"].includes(item.threshold_selection_reason ?? "") ? <span className="mt-1 block text-emerald-200">困难负例拒答 Pareto</span> : null}</td><td className="py-3">{percent(item.holdout_metrics.ndcg_at_10)}</td><td className="py-3">{percent(item.holdout_metrics.recall_at_5)}</td><td className="py-3">{percent(item.holdout_metrics.no_result_accuracy)}</td><td className="py-3">{item.holdout_metrics.p95_latency_ms?.toFixed(1) ?? "-"} ms</td><td className="py-3"><span className={item.statistical_validation?.passed ? "text-emerald-200" : "text-amber-100"}>{confidenceInterval(item)}</span><span className="mt-1 block text-[10px] text-slate-500">稳定 {item.statistical_validation?.stable_resample_count ?? 0}/{item.statistical_validation?.required_stable_resamples ?? 0}</span></td><td className="py-3">{item.promotion_gate.passed && item.improvement.effective && item.statistical_validation?.passed ? <span className="text-emerald-200">可晋级</span> : <span className="text-slate-500">未通过</span>}</td></tr>)}</tbody></table></div> : null}
            {run.status === "completed" && run.final_version_id ? <div className="mt-5 flex gap-3 border-t border-white/10 pt-4"><CheckCircle2 className="mt-0.5 shrink-0 text-emerald-300" size={18} /><div><p className="text-sm font-semibold text-white">候选版本已物化并通过完整评测</p><p className="mt-1 text-xs leading-5 text-slate-400">版本仍为 promotion_required，活动索引没有变化。</p><Link className="mt-3 inline-flex items-center gap-2 text-xs font-semibold text-emerald-200 hover:text-emerald-100" to={`/rag/${encodeURIComponent(kbId)}/evaluation`}>查看 Evaluation 与推广入口<ExternalLink size={13} /></Link></div></div> : null}
          </section> : null}
        </div>
        {error ? <div className="border-t border-rose-300/20 bg-rose-300/10 px-5 py-3 text-xs leading-5 text-rose-100" aria-live="polite">{error}</div> : null}
      </aside>
    </div>
  );
}

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: Array<{ value: string; label: string }> }) {
  return <label className="text-xs font-semibold text-slate-300">{label}<select className="mt-2 w-full rounded-md border border-white/10 bg-surface-950 px-3 py-2 text-sm font-normal text-white" onChange={(event) => onChange(event.target.value)} value={value}><option value="">请选择</option>{options.map((item) => <option key={`${item.value}-${item.label}`} value={item.value}>{item.label}</option>)}</select></label>;
}

function NumberInput({ disabled, label, max, min, onChange, value }: { disabled: boolean; label: string; max: number; min: number; onChange: (value: number) => void; value: number }) {
  return <label className="text-xs font-semibold text-slate-300">{label}<input className="mt-2 w-full rounded-md border border-white/10 bg-surface-950 px-3 py-2 text-sm font-normal text-white disabled:opacity-50" disabled={disabled} max={max} min={min} onChange={(event) => onChange(Math.max(min, Math.min(max, Number(event.target.value) || min)))} type="number" value={value} /></label>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><p className="text-[10px] font-semibold uppercase text-slate-500">{label}</p><p className="mt-1 text-sm font-semibold text-slate-100">{value}</p></div>;
}
