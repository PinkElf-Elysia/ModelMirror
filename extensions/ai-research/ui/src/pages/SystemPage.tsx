import { ExternalLink, RefreshCw } from "lucide-react";
import { useCallback } from "react";

import { api } from "../api";
import { ErrorNotice, FixtureNotice, LoadingRows, PageHeader } from "../components/Page";
import { formatTime } from "../components/RunTable";
import { Status } from "../components/Status";
import { usePolling } from "../hooks/usePolling";

const names: Record<string, string> = {
  controlLedger: "Control Ledger",
  worker: "Inspect Worker",
  tracking: "MLflow Tracking",
  inspectView: "Inspect View",
};

export function SystemPage() {
  const system = usePolling(useCallback((signal: AbortSignal) => api.system(signal), []), 10_000);
  const module = usePolling(useCallback((signal: AbortSignal) => api.module(signal), []), 60_000);
  return (
    <div className="page">
      <PageHeader eyebrow="System" title="系统与运行时" description="区分必需依赖和可选只读视图；可选服务离线只会降级，不阻止创建夹具。" actions={<button className="button" type="button" onClick={() => { system.refresh(); module.refresh(); }}><RefreshCw aria-hidden="true" size={14} />刷新</button>} />
      <FixtureNotice />
      <section className="section" aria-labelledby="health-title">
        <div className="flex items-center justify-between gap-3"><h2 className="section-title" id="health-title">依赖检查</h2><Status value={system.data?.status ?? "not_ready"} /></div>
        {system.data ? (
          <ul className="mt-4 divide-y divide-[var(--border)] border-y border-[var(--border)]">
            {system.data.checks.map((check) => <li className="flex items-center justify-between gap-3 py-3" key={check.id}><div><p className="text-sm font-semibold text-[#dde4e8]">{names[check.id] ?? check.id}</p><p className="mt-1 text-xs text-[var(--muted)]">{check.required ? "必需 · 影响创建门禁" : "可选 · 离线仅降级"}</p></div><Status value={check.status} /></li>)}
          </ul>
        ) : system.error ? <ErrorNotice message={system.error.message} onRetry={system.refresh} /> : <LoadingRows count={4} />}
        {system.data ? <p className="mt-3 text-xs text-[var(--muted)]">检查时间 {formatTime(system.data.checkedAt)}</p> : null}
      </section>

      <section className="section" aria-labelledby="runtime-title">
        <h2 className="section-title" id="runtime-title">精确运行时</h2>
        {module.data ? <dl className="definition-grid mt-3">{Object.entries(module.data.runtimes).map(([name, version]) => <div className="definition-row" key={name}><dt>{name}</dt><dd className="font-mono">{version}</dd></div>)}</dl> : module.error ? <ErrorNotice message={module.error.message} onRetry={module.refresh} /> : <LoadingRows count={3} />}
      </section>

      <section className="section" aria-labelledby="boundary-title">
        <h2 className="section-title" id="boundary-title">模块边界</h2>
        {module.data ? <><dl className="definition-grid mt-3"><div className="definition-row"><dt>模块版本</dt><dd>{module.data.moduleVersion}</dd></div><div className="definition-row"><dt>控制协议</dt><dd>{module.data.apiVersion}</dd></div><div className="definition-row"><dt>Worker 协议</dt><dd>{module.data.workerProtocolVersion}</dd></div><div className="definition-row"><dt>声明级别</dt><dd>{module.data.claimLevel} · {module.data.packStatus}</dd></div></dl><ul className="mt-4 list-inside list-disc space-y-2 text-sm text-[var(--muted)]">{module.data.limitations.map((item) => <li key={item}>{item}</li>)}</ul><p className="mt-4 border-l-2 border-[#826b36] pl-3 text-xs leading-5 text-[#c8b98f]">MLflow 是本机上游开发界面，可修改其自身记录；Control Ledger 与 canonical receipt 才是本模块的权威事实。</p><div className="mt-4 flex flex-wrap gap-2"><a className="button" href={module.data.links.inspectView} target="_blank" rel="noopener noreferrer">Inspect View <ExternalLink aria-hidden="true" size={14} /></a><a className="button" href={module.data.links.mlflow} target="_blank" rel="noopener noreferrer">打开 MLflow 开发界面 <ExternalLink aria-hidden="true" size={14} /></a></div></> : null}
      </section>
    </div>
  );
}
