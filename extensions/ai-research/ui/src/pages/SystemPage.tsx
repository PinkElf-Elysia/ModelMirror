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
  const fixtureClaim = module.data?.capabilityClaims.fixtureExecution;
  const literatureClaim = module.data?.capabilityClaims.literatureResearch;
  return (
    <div className="page">
      <PageHeader eyebrow="系统" title="能力与运行时" description="夹具执行和真实文献研究使用独立门禁；LDR 锁定不会破坏 AR1 历史与证据浏览。" actions={<button className="button" type="button" onClick={() => { system.refresh(); module.refresh(); }}><RefreshCw aria-hidden="true" size={14} />刷新</button>} />
      <FixtureNotice />
      <section className="section" aria-labelledby="health-title">
        <div className="flex items-center justify-between gap-3"><div><h2 className="section-title" id="health-title">工程夹具能力</h2><p className="mt-2 text-sm text-[var(--muted)]">Control Ledger、Worker 与 Tracking 共同决定创建门禁。</p></div><Status value={system.data?.status ?? "not_ready"} /></div>
        {system.data ? (
          <ul className="mt-4 divide-y divide-[var(--border)] border-y border-[var(--border)]">
            {system.data.checks.map((check) => <li className="flex items-center justify-between gap-3 py-3" key={check.id}><div><p className="text-sm font-semibold text-[#dde4e8]">{names[check.id] ?? check.id}</p><p className="mt-1 text-xs text-[var(--muted)]">{check.required ? "必需 · 影响创建门禁" : "可选 · 离线仅降级"}</p></div><Status value={check.status} /></li>)}
          </ul>
        ) : system.error ? <ErrorNotice message={system.error.message} onRetry={system.refresh} /> : <LoadingRows count={4} />}
        {system.data ? <p className="mt-3 text-xs text-[var(--muted)]">检查时间 {formatTime(system.data.checkedAt)}</p> : null}
        {fixtureClaim ? <dl className="definition-grid mt-3"><div className="definition-row"><dt>夹具声明等级</dt><dd>{fixtureClaim.claimLevel}</dd></div><div className="definition-row"><dt>内容状态</dt><dd>{fixtureClaim.packStatus}</dd></div></dl> : null}
      </section>

      <section className="section" aria-labelledby="literature-health-title">
        <div className="flex items-center justify-between gap-3"><div><h2 className="section-title" id="literature-health-title">文献研究能力</h2><p className="mt-2 text-sm text-[var(--muted)]">Local Deep Research、固定 profile、受限模型桥与内存会话。</p></div><Status value={system.data?.literatureCapability?.status ?? "not_ready"} /></div>
        {system.data?.literatureCapability ? <dl className="definition-grid mt-3"><div className="definition-row"><dt>LDR 服务</dt><dd><Status value={system.data.literatureCapability.serviceStatus} /></dd></div><div className="definition-row"><dt>固定 profile</dt><dd><Status value={system.data.literatureCapability.profileStatus} /></dd></div><div className="definition-row"><dt>模型桥</dt><dd><Status value={system.data.literatureCapability.modelBridgeStatus ?? "not_ready"} /></dd></div><div className="definition-row"><dt>账户会话</dt><dd><Status value={system.data.literatureCapability.sessionStatus === "ready" ? "ready" : "pending"} label={system.data.literatureCapability.sessionStatus === "ready" ? `已解锁 · ${system.data.literatureCapability.username ?? "本地账户"}` : system.data.literatureCapability.sessionStatus} /></dd></div><div className="definition-row"><dt>工作流来源</dt><dd>{literatureClaim?.workflowSource === "local_deep_research" ? "Local Deep Research" : "待确认"}</dd></div><div className="definition-row"><dt>科学声明</dt><dd>{system.data.literatureCapability.scientificClaim}，结果需人工复核</dd></div></dl> : <p className="mt-4 text-sm text-[var(--muted)]">当前 Control 尚未返回 V0.1 文献能力状态。</p>}
        {literatureClaim ? <div className="constraint-panel mt-4 flex flex-wrap items-center gap-3"><Status value="pending" label="功能已实现，真实环境验收待完成" /><p className="m-0 text-xs leading-5 text-[var(--muted)]">该状态描述产品验收进度，不代表科学有效性验证。</p></div> : null}
      </section>

      <section className="section" aria-labelledby="runtime-title">
        <h2 className="section-title" id="runtime-title">精确运行时</h2>
        {module.data ? <dl className="definition-grid mt-3">{Object.entries(module.data.runtimes).map(([name, version]) => <div className="definition-row" key={name}><dt>{name}</dt><dd className="font-mono">{version}</dd></div>)}</dl> : module.error ? <ErrorNotice message={module.error.message} onRetry={module.refresh} /> : <LoadingRows count={3} />}
      </section>

      <section className="section" aria-labelledby="boundary-title">
        <h2 className="section-title" id="boundary-title">模块边界</h2>
        {module.data ? <><dl className="definition-grid mt-3"><div className="definition-row"><dt>模块版本</dt><dd>{module.data.moduleVersion}</dd></div><div className="definition-row"><dt>控制协议</dt><dd>{module.data.apiVersion}</dd></div><div className="definition-row"><dt>Worker 协议</dt><dd>{module.data.workerProtocolVersion}</dd></div></dl><ul className="mt-4 list-inside list-disc space-y-2 text-sm text-[var(--muted)]">{module.data.limitations.map((item) => <li key={item}>{item}</li>)}</ul><p className="constraint-panel mt-4 text-xs leading-5 text-[#c8b98f]">MLflow 是本机上游开发界面，可修改其自身记录；Control Ledger 与 canonical receipt 才是夹具能力的权威事实。文献成果另以项目目录中的 manifest 和 receipt 为准。</p><div className="mt-4 flex flex-wrap gap-2"><a className="button" href={module.data.links.localDeepResearch} target="_blank" rel="noopener noreferrer">Local Deep Research <ExternalLink aria-hidden="true" size={14} /></a><a className="button" href={module.data.links.inspectView} target="_blank" rel="noopener noreferrer">Inspect View <ExternalLink aria-hidden="true" size={14} /></a><a className="button" href={module.data.links.mlflow} target="_blank" rel="noopener noreferrer">打开 MLflow 开发界面 <ExternalLink aria-hidden="true" size={14} /></a></div></> : null}
      </section>
    </div>
  );
}
