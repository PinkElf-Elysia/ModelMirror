import { Ban, ExternalLink } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, NavLink, useParams } from "react-router-dom";

import { api } from "../api";
import { ErrorNotice, LoadingRows, PageHeader } from "../components/Page";
import { formatTime } from "../components/RunTable";
import { Status } from "../components/Status";
import { usePolling } from "../hooks/usePolling";

export function RunDetailPage() {
  const { runId = "" } = useParams();
  const [confirming, setConfirming] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const confirmCancelButton = useRef<HTMLButtonElement>(null);
  const run = usePolling(
    useCallback((signal: AbortSignal) => api.run(runId, signal), [runId]),
    2_000,
    true,
    (value) => value.phase !== "terminal",
  );

  useEffect(() => {
    if (confirming) confirmCancelButton.current?.focus();
  }, [confirming]);

  const dismissConfirmation = () => {
    setConfirming(false);
    requestAnimationFrame(() => cancelButton.current?.focus());
  };

  const cancel = async () => {
    setCancelling(true);
    setCancelError(null);
    try {
      await api.cancel(runId);
      setConfirming(false);
      run.refresh();
      requestAnimationFrame(() => cancelButton.current?.focus());
    } catch (caught) {
      setCancelError(caught instanceof Error ? caught.message : "取消请求失败");
    } finally {
      setCancelling(false);
    }
  };

  return (
    <div className="page">
      <PageHeader
        eyebrow="Run detail"
        title="运行详情"
        description={runId}
        actions={run.data?.phase !== "terminal" ? (
          <button ref={cancelButton} className="button button-danger" type="button" onClick={() => setConfirming(true)}><Ban aria-hidden="true" size={15} />请求取消</button>
        ) : undefined}
      />
      <nav className="mt-5 flex flex-wrap gap-1 border-b border-[var(--border)]" aria-label="运行详情页面">
        {[
          ["", "概览"], ["events", "事件"], ["evidence", "证据"],
        ].map(([path, label]) => (
          <NavLink end={path === ""} key={path} to={path || "."} className={({ isActive }) => `border-b-2 px-3 py-2 text-sm font-semibold ${isActive ? "border-[var(--cyan)] text-white" : "border-transparent text-[var(--muted)] hover:text-white"}`}>{label}</NavLink>
        ))}
      </nav>

      {confirming ? (
        <div className="mt-5 flex flex-wrap items-center justify-between gap-4 border border-[#6c4a32] bg-[#241c13] px-4 py-3" role="alertdialog" aria-labelledby="cancel-title" aria-describedby="cancel-description" onKeyDown={(event) => { if (event.key === "Escape" && !cancelling) dismissConfirmation(); }}>
          <div><h2 className="text-sm font-semibold text-[#ffd79a]" id="cancel-title">确认请求取消？</h2><p className="mt-1 text-xs text-[#c7b89f]" id="cancel-description">控制面会记录取消意图；Inspect 是否受理将作为独立事实保留。</p></div>
          <div className="flex gap-2"><button className="button" type="button" onClick={dismissConfirmation}>返回</button><button ref={confirmCancelButton} className="button button-danger" disabled={cancelling} type="button" onClick={cancel}>{cancelling ? "正在请求" : "确认取消"}</button></div>
        </div>
      ) : null}
      {cancelError ? <ErrorNotice message={cancelError} /> : null}

      {run.data ? (
        <>
          <section className="section" aria-labelledby="lifecycle-title">
            <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="section-title" id="lifecycle-title">生命周期</h2><div className="flex flex-wrap gap-2"><Status value={run.data.phase} /><Status value={run.data.outcome} /></div></div>
            <dl className="definition-grid mt-3">
              <div className="definition-row"><dt>夹具</dt><dd>{run.data.caseId}</dd></div>
              <div className="definition-row"><dt>Inspect 原始状态</dt><dd><Status value={run.data.inspectStatus} /></dd></div>
              <div className="definition-row"><dt>创建</dt><dd>{formatTime(run.data.createdAt)}</dd></div>
              <div className="definition-row"><dt>开始</dt><dd>{formatTime(run.data.startedAt)}</dd></div>
              <div className="definition-row"><dt>终止</dt><dd>{formatTime(run.data.terminalAt)}</dd></div>
              <div className="definition-row"><dt>证据同步</dt><dd>{formatTime(run.data.evidenceSyncedAt)}</dd></div>
            </dl>
          </section>
          <section className="section" aria-labelledby="cancel-facts-title"><h2 className="section-title" id="cancel-facts-title">取消事实</h2><dl className="definition-grid mt-3"><div className="definition-row"><dt>已请求</dt><dd>{run.data.cancelRequested ? `是 · ${formatTime(run.data.cancelRequestedAt)}` : "否"}</dd></div><div className="definition-row"><dt>Inspect 已受理</dt><dd>{run.data.cancelApplied ? `是 · ${formatTime(run.data.cancelAppliedAt)}` : "否"}</dd></div></dl></section>
          <section className="section" aria-labelledby="error-title"><h2 className="section-title" id="error-title">错误与标识</h2><dl className="definition-grid mt-3"><div className="definition-row"><dt>错误类型</dt><dd>{run.data.errorType ?? "—"}</dd></div><div className="definition-row"><dt>错误信息</dt><dd>{run.data.errorMessage ?? "—"}</dd></div><div className="definition-row"><dt>MLflow Run</dt><dd>{run.data.mlflowRunId ?? "—"}</dd></div><div className="definition-row"><dt>配置重放</dt><dd>{run.data.replayVerified ? "已验证" : "未验证"}</dd></div></dl></section>
          <div className="mt-6 flex flex-wrap gap-2"><Link className="button" to="events">查看事件</Link><Link className="button" to="evidence">检查证据 <ExternalLink aria-hidden="true" size={14} /></Link></div>
        </>
      ) : run.error ? <ErrorNotice message={run.error.message} onRetry={run.refresh} /> : <LoadingRows count={5} />}
      <p className="sr-only" aria-live="polite">{run.refreshing ? "运行状态正在更新" : "运行状态已更新"}</p>
    </div>
  );
}
