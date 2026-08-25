import { RotateCcw, Search } from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../api";
import { ErrorNotice, LoadingRows, PageHeader } from "../components/Page";
import { RunTable } from "../components/RunTable";
import { usePolling } from "../hooks/usePolling";
import type { CaseId, EvidenceState, Outcome, Phase } from "../types";

const known = ["q", "caseId", "phase", "outcome", "evidenceState", "cursor"] as const;
const cases = new Set<CaseId>(["success", "task_error", "long_running_cancel"]);
const phases = new Set<Phase>(["queued", "running", "terminal"]);
const outcomes = new Set<Outcome>(["success", "task_error", "cancelled", "infrastructure_error"]);
const evidence = new Set<EvidenceState>(["pending", "synced", "failed"]);

function legal<T extends string>(value: string | null, values: Set<T>): T | "" {
  return value && values.has(value as T) ? (value as T) : "";
}

export function RunsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const q = (searchParams.get("q") ?? "").slice(0, 100);
  const [draft, setDraft] = useState(q);
  useEffect(() => setDraft(q), [q]);
  const filters = {
    caseId: legal(searchParams.get("caseId"), cases),
    phase: legal(searchParams.get("phase"), phases),
    outcome: legal(searchParams.get("outcome"), outcomes),
    evidenceState: legal(searchParams.get("evidenceState"), evidence),
  };
  const apiParams = useMemo(() => {
    const next = new URLSearchParams();
    if (q) next.set("q", q);
    for (const [key, value] of Object.entries(filters)) if (value) next.set(key, value);
    const cursor = searchParams.get("cursor");
    if (cursor) next.set("cursor", cursor);
    next.set("limit", "25");
    return next;
  }, [q, filters.caseId, filters.phase, filters.outcome, filters.evidenceState, searchParams]);
  const runs = usePolling(
    useCallback((signal: AbortSignal) => api.runs(apiParams, signal), [apiParams]),
    5_000,
  );

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value); else next.delete(key);
    if (key !== "cursor") next.delete("cursor");
    setSearchParams(next);
  };

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setFilter("q", draft.trim().slice(0, 100));
  };

  const clear = () => {
    const next = new URLSearchParams(searchParams);
    known.forEach((key) => next.delete(key));
    setDraft("");
    setSearchParams(next);
  };

  return (
    <div className="page">
      <PageHeader eyebrow="Runs" title="运行记录" description="按夹具、生命周期、结果和证据状态组合筛选；所有筛选轴按 AND 生效。" />
      <section className="section" aria-label="运行筛选">
        <form className="grid gap-3 lg:grid-cols-[minmax(180px,1.5fr)_repeat(4,minmax(130px,1fr))_auto] lg:items-end" onSubmit={submitSearch}>
          <label className="field-label">搜索
            <span className="relative"><Search aria-hidden="true" className="absolute left-3 top-3 text-[var(--muted)]" size={15} /><input className="field pl-9" maxLength={100} value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="运行 ID、夹具或错误类型" /></span>
          </label>
          <label className="field-label">夹具<select className="field" value={filters.caseId} onChange={(event) => setFilter("caseId", event.target.value)}><option value="">全部</option><option value="success">success</option><option value="task_error">task_error</option><option value="long_running_cancel">long_running_cancel</option></select></label>
          <label className="field-label">阶段<select className="field" value={filters.phase} onChange={(event) => setFilter("phase", event.target.value)}><option value="">全部</option><option value="queued">queued</option><option value="running">running</option><option value="terminal">terminal</option></select></label>
          <label className="field-label">结果<select className="field" value={filters.outcome} onChange={(event) => setFilter("outcome", event.target.value)}><option value="">全部</option><option value="success">success</option><option value="task_error">task_error</option><option value="cancelled">cancelled</option><option value="infrastructure_error">infrastructure_error</option></select></label>
          <label className="field-label">证据<select className="field" value={filters.evidenceState} onChange={(event) => setFilter("evidenceState", event.target.value)}><option value="">全部</option><option value="pending">pending</option><option value="synced">synced</option><option value="failed">failed</option></select></label>
          <div className="flex flex-wrap gap-2 lg:justify-end">
            <button className="button button-primary" type="submit"><Search aria-hidden="true" size={15} />搜索</button>
            <button className="button" type="button" onClick={clear}><RotateCcw aria-hidden="true" size={15} />清空</button>
          </div>
        </form>
      </section>

      <section className="section" aria-labelledby="results-title">
        <div className="flex items-center justify-between gap-3">
          <h2 className="section-title" id="results-title">筛选结果</h2>
          {runs.refreshing ? <span className="text-xs text-[var(--muted)]">正在更新…</span> : null}
        </div>
        {runs.data ? <RunTable runs={runs.data.items} empty="没有匹配当前条件的运行" /> : runs.error ? <ErrorNotice message={runs.error.message} onRetry={runs.refresh} /> : <LoadingRows count={5} />}
        <div className="mt-5 flex items-center justify-end gap-2">
          {searchParams.has("cursor") ? <button className="button" type="button" onClick={() => navigate(-1)}>上一页</button> : null}
          {runs.data?.nextCursor ? <button className="button" type="button" onClick={() => setFilter("cursor", runs.data?.nextCursor ?? "")}>下一页</button> : null}
        </div>
        <p className="sr-only" aria-live="polite">{runs.data ? `当前显示 ${runs.data.items.length} 条运行` : runs.loading ? "正在加载运行" : "运行加载失败"}</p>
      </section>
    </div>
  );
}
