import { ArrowRight, Play, RefreshCw } from "lucide-react";
import { FormEvent, useCallback, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { ApiError, api } from "../api";
import { ErrorNotice, FixtureNotice, LoadingRows, PageHeader } from "../components/Page";
import { RunTable } from "../components/RunTable";
import { Status } from "../components/Status";
import { usePolling } from "../hooks/usePolling";
import type { CaseId } from "../types";

const cases: { id: CaseId; label: string; description: string }[] = [
  { id: "success", label: "成功夹具", description: "验证正常执行、日志与证据同步。" },
  { id: "task_error", label: "任务错误夹具", description: "验证退出码与 Inspect 原始终态分离。" },
  { id: "long_running_cancel", label: "长运行取消夹具", description: "验证取消意图、受理事实与终态保留。" },
];

export function OverviewPage() {
  const navigate = useNavigate();
  const [caseId, setCaseId] = useState<CaseId>("success");
  const [submitting, setSubmitting] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const idempotencyKey = useRef<string | null>(null);
  const system = usePolling(useCallback((signal: AbortSignal) => api.system(signal), []), 10_000);
  const summary = usePolling(useCallback((signal: AbortSignal) => api.summary(signal), []), 5_000);
  const recent = usePolling(
    useCallback((signal: AbortSignal) => api.runs(new URLSearchParams({ limit: "5" }), signal), []),
    5_000,
  );
  const creationBlocked = !system.data || system.data.status === "not_ready";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting || creationBlocked) return;
    setSubmitting(true);
    setCreateError(null);
    idempotencyKey.current ??= `console:${crypto.randomUUID()}`;
    try {
      const run = await api.createRun(caseId, idempotencyKey.current);
      idempotencyKey.current = null;
      navigate(`/runs/${run.runId}`);
    } catch (caught) {
      setCreateError(caught instanceof ApiError ? caught.message : "创建请求未完成，请使用同一请求重试。 ");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page">
      <PageHeader eyebrow="AI Research / AR1" title="科研执行控制台" description="从受控夹具创建到 Inspect 原始终态、证据完整性与 MLflow 记录，集中查看工程闭环。" />
      <FixtureNotice />

      <section className="section" aria-labelledby="dependency-title">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="section-title" id="dependency-title">依赖状态</h2>
          <Status value={system.data?.status ?? "not_ready"} />
        </div>
        {system.loading ? <LoadingRows count={1} /> : null}
        {system.error ? <ErrorNotice message={system.error.message} onRetry={system.refresh} /> : null}
        {system.data ? (
          <ul className="mt-4 grid gap-px bg-[var(--border)] sm:grid-cols-2 lg:grid-cols-4">
            {system.data.checks.map((check) => (
              <li className="flex items-center justify-between gap-3 bg-[var(--surface)] px-3 py-3" key={check.id}>
                <span className="text-sm text-[#c8d0d5]">{check.id}</span>
                <Status value={check.status} label={check.required ? undefined : `${check.status === "ready" ? "就绪" : "离线"} · 可选`} />
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section className="section" aria-labelledby="summary-title">
        <h2 className="section-title" id="summary-title">运行摘要</h2>
        {summary.data ? (
          <dl className="mt-4 grid gap-px bg-[var(--border)] sm:grid-cols-4">
            <div className="bg-[var(--surface)] p-4"><dt className="text-xs text-[var(--muted)]">全部</dt><dd className="mt-1 text-xl font-semibold">{summary.data.total}</dd></div>
            <div className="bg-[var(--surface)] p-4"><dt className="text-xs text-[var(--muted)]">运行中</dt><dd className="mt-1 text-xl font-semibold">{summary.data.phases.running}</dd></div>
            <div className="bg-[var(--surface)] p-4"><dt className="text-xs text-[var(--muted)]">成功</dt><dd className="mt-1 text-xl font-semibold">{summary.data.outcomes.success}</dd></div>
            <div className="bg-[var(--surface)] p-4"><dt className="text-xs text-[var(--muted)]">证据待处理</dt><dd className="mt-1 text-xl font-semibold">{summary.data.evidenceStates.pending + summary.data.evidenceStates.failed}</dd></div>
          </dl>
        ) : summary.error ? <ErrorNotice message={summary.error.message} onRetry={summary.refresh} /> : <LoadingRows count={1} />}
      </section>

      <section className="section" aria-labelledby="create-title">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="section-title" id="create-title">创建工程夹具</h2>
          {creationBlocked ? <span className="text-xs text-[var(--amber)]">必需依赖未就绪，当前禁止创建</span> : null}
        </div>
        <form className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end" onSubmit={submit}>
          <label className="field-label">
            夹具场景
            <select className="field" value={caseId} onChange={(event) => { setCaseId(event.target.value as CaseId); idempotencyKey.current = null; }}>
              {cases.map((item) => <option key={item.id} value={item.id}>{item.label} — {item.description}</option>)}
            </select>
          </label>
          <button className="button button-primary" type="submit" disabled={submitting || creationBlocked}>
            {submitting ? <RefreshCw aria-hidden="true" className="animate-spin" size={16} /> : <Play aria-hidden="true" size={16} />}
            {submitting ? "正在创建" : "创建并查看"}
          </button>
        </form>
        {createError ? <ErrorNotice message={createError} /> : null}
      </section>

      <section className="section" aria-labelledby="recent-title">
        <div className="flex items-center justify-between gap-3">
          <h2 className="section-title" id="recent-title">最近运行</h2>
          <Link className="inline-flex items-center gap-1 text-xs font-semibold text-[var(--cyan)] hover:underline" to="/runs">全部运行 <ArrowRight aria-hidden="true" size={14} /></Link>
        </div>
        {recent.data ? <RunTable runs={recent.data.items} /> : recent.error ? <ErrorNotice message={recent.error.message} onRetry={recent.refresh} /> : <LoadingRows />}
      </section>
      <p className="sr-only" aria-live="polite">{system.refreshing || recent.refreshing ? "状态正在更新" : "状态已更新"}</p>
    </div>
  );
}
