import { Download, ExternalLink, ShieldCheck } from "lucide-react";
import { useCallback } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";
import { ErrorNotice, LoadingRows, PageHeader } from "../components/Page";
import { formatTime } from "../components/RunTable";
import { Status } from "../components/Status";
import { usePolling } from "../hooks/usePolling";

export function RunEvidencePage() {
  const { runId = "" } = useParams();
  const evidence = usePolling(
    useCallback((signal: AbortSignal) => api.evidence(runId, signal), [runId]),
    2_000,
  );
  const module = usePolling(useCallback((signal: AbortSignal) => api.module(signal), []), 60_000);
  const receipt = evidence.data?.receipt;

  return (
    <div className="page">
      <PageHeader eyebrow="Evidence" title="证据与完整性" description={`${runId} · 每次读取和下载都重新验证账本 receipt 与本地制品。`} actions={<Link className="button" to={`/runs/${runId}`}>返回详情</Link>} />
      {evidence.data ? (
        <>
          <section className="section" aria-labelledby="integrity-title">
            <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="section-title" id="integrity-title">实时完整性</h2><Status value={evidence.data.integrityStatus} /></div>
            <dl className="definition-grid mt-3">
              <div className="definition-row"><dt>账本证据状态</dt><dd><Status value={evidence.data.evidenceState} /></dd></div>
              <div className="definition-row"><dt>本次验证时间</dt><dd>{formatTime(evidence.data.verifiedAt)}</dd></div>
              <div className="definition-row"><dt>Outbox</dt><dd>{String(evidence.data.outbox?.state ?? "尚未创建")}</dd></div>
              <div className="definition-row"><dt>重试次数</dt><dd>{String(evidence.data.outbox?.attemptCount ?? 0)}</dd></div>
            </dl>
            {evidence.data.integrityError ? <ErrorNotice message={evidence.data.integrityError} /> : null}
          </section>

          <section className="section" aria-labelledby="artifact-title">
            <h2 className="section-title" id="artifact-title">Artifact 清单</h2>
            {evidence.data.artifacts.length ? (
              <ul className="mt-4 divide-y divide-[var(--border)] border-y border-[var(--border)]">
                {evidence.data.artifacts.map((artifact) => (
                  <li className="flex flex-wrap items-center justify-between gap-3 py-3" key={artifact.name}>
                    <div className="min-w-0"><p className="font-mono text-sm text-[#dfe7ea]">{artifact.name}</p><p className="mt-1 break-all font-mono text-[10px] text-[var(--muted)]">SHA-256 {artifact.sha256} · {artifact.sizeBytes} bytes</p></div>
                    <a className="button" download href={artifact.downloadUrl}><Download aria-hidden="true" size={14} />下载并复验</a>
                  </li>
                ))}
              </ul>
            ) : <p className="my-7 text-sm text-[var(--muted)]">receipt 尚未登记可下载制品。</p>}
          </section>

          <section className="section" aria-labelledby="external-title">
            <h2 className="section-title" id="external-title">复核入口</h2>
            <p className="mt-2 text-sm leading-6 text-[var(--muted)]">入口仅绑定本机回环地址。Inspect View 为只读可选服务；MLflow 展示同步后的工程记录。</p>
            <p className="mt-3 border-l-2 border-[#826b36] pl-3 text-xs leading-5 text-[#c8b98f]">MLflow 是本机上游开发界面，可修改其自身记录；生命周期与证据判定以 Control Ledger 和 canonical receipt 为准。</p>
            <div className="mt-4 flex flex-wrap gap-2">
              {module.data ? <a className="button" href={module.data.links.inspectView} target="_blank" rel="noopener noreferrer">Inspect View <ExternalLink aria-hidden="true" size={14} /></a> : null}
              {module.data ? <a className="button" href={module.data.links.mlflow} target="_blank" rel="noopener noreferrer">打开 MLflow 开发界面 <ExternalLink aria-hidden="true" size={14} /></a> : null}
            </div>
            <dl className="definition-grid mt-3"><div className="definition-row"><dt>Experiment</dt><dd>{evidence.data.mlflow.experimentId ?? "—"}</dd></div><div className="definition-row"><dt>Run</dt><dd>{evidence.data.mlflow.runId ?? "—"}</dd></div><div className="definition-row"><dt>Trace</dt><dd>{evidence.data.mlflow.traceId ?? "—"}</dd></div></dl>
          </section>

          <section className="section" aria-labelledby="receipt-title">
            <div className="flex items-center gap-2"><ShieldCheck aria-hidden="true" className="text-[var(--cyan)]" size={17} /><h2 className="section-title" id="receipt-title">Canonical receipt</h2></div>
            {receipt ? <pre className="code-block mt-4">{JSON.stringify(receipt, null, 2)}</pre> : <p className="my-7 text-sm text-[var(--muted)]">运行尚未生成 receipt。</p>}
          </section>
        </>
      ) : evidence.error ? <ErrorNotice message={evidence.error.message} onRetry={evidence.refresh} /> : <LoadingRows count={6} />}
      <p className="sr-only" aria-live="polite">{evidence.refreshing ? "正在重新验证证据" : "证据验证已更新"}</p>
    </div>
  );
}
