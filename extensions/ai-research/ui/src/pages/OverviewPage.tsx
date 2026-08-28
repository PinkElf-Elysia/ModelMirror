import { ArrowRight, BookOpen, FolderPlus, Library, Wrench } from "lucide-react";
import { useCallback } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { ErrorNotice, LoadingRows, PageHeader } from "../components/Page";
import { formatTime } from "../components/RunTable";
import { Status } from "../components/Status";
import { usePolling } from "../hooks/usePolling";
import type { LiteratureOutcome, LiteraturePhase } from "../types";

function projectStatus(phase: LiteraturePhase, outcome: LiteratureOutcome | null) {
  if (phase === "queued" || phase === "running") return { value: "running", label: "研究中" };
  if (outcome === "completed") return { value: "success", label: "综述已完成" };
  if (outcome === "cancelled") return { value: "cancelled", label: "已取消" };
  if (outcome) return { value: "failed", label: "需要处理" };
  return { value: "pending", label: "待开始" };
}

export function OverviewPage() {
  const system = usePolling(useCallback((signal: AbortSignal) => api.system(signal), []), 10_000);
  const projects = usePolling(
    useCallback((signal: AbortSignal) => api.projects(new URLSearchParams({ limit: "5" }), signal), []),
    5_000,
  );
  const literature = system.data?.literatureCapability;

  return (
    <div className="page">
      <PageHeader
        eyebrow="AI / Agent Research"
        title="继续你的文献研究"
        description="以 Research Project 为主线，复用 Local Deep Research 完成公开论文检索、来源复核、资料库关联与可验证成果导出。"
        actions={<Link className="button button-primary" to="/projects/new"><FolderPlus size={16} />创建研究项目</Link>}
      />
      <div className="notice" role="note">本轮形成真实文献研究与来源包；报告需人工复核，scientificClaim=none。工程夹具保留为独立验证工具。</div>

      <section className="section" aria-labelledby="capability-title">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h2 className="section-title" id="capability-title">文献能力</h2><p className="mt-2 text-sm text-[var(--muted)]">固定模型桥、OpenAlex 主检索、LDR Library 与 Zotero。</p></div>
          <Status value={literature?.status ?? "not_ready"} label={literature?.status === "ready" ? "可启动研究" : "需要配置"} />
        </div>
        {system.loading ? <LoadingRows count={1} /> : null}
        {system.error ? <ErrorNotice message={system.error.message} onRetry={system.refresh} /> : null}
        {literature ? (
          <dl className="definition-grid mt-4">
            <div className="definition-row"><dt>Local Deep Research</dt><dd><Status value={literature.serviceStatus} /></dd></div>
            <div className="definition-row"><dt>模型桥</dt><dd><Status value={literature.modelBridgeStatus ?? "not_ready"} /></dd></div>
            <div className="definition-row"><dt>账户会话</dt><dd><Status value={literature.sessionStatus === "ready" ? "ready" : "pending"} label={literature.sessionStatus === "ready" ? `已解锁 · ${literature.username ?? "本地账户"}` : "进入项目后解锁"} /></dd></div>
            <div className="definition-row"><dt>研究配置</dt><dd>OpenAlex · langgraph-agent · 2 轮</dd></div>
          </dl>
        ) : null}
      </section>

      <section className="section" aria-labelledby="journey-title">
        <h2 className="section-title" id="journey-title">V0.1 科研主线</h2>
        <ol className="journey-line">
          <li><FolderPlus size={16} /><span><strong>定义项目</strong><small>AI / Agent 研究问题</small></span></li>
          <li><BookOpen size={16} /><span><strong>运行文献研究</strong><small>公开学术来源与固定模型</small></span></li>
          <li><Library size={16} /><span><strong>关联资料库</strong><small>Zotero 与本地索引门禁</small></span></li>
          <li><ArrowRight size={16} /><span><strong>复核并导出</strong><small>综述、引用、来源和 manifest</small></span></li>
        </ol>
      </section>

      <section className="section" aria-labelledby="recent-projects-title">
        <div className="flex items-center justify-between gap-3"><h2 className="section-title" id="recent-projects-title">最近项目</h2><Link className="inline-flex items-center gap-1 text-xs font-semibold text-[var(--cyan)] hover:underline" to="/projects">全部项目 <ArrowRight size={14} /></Link></div>
        {projects.loading ? <LoadingRows count={4} /> : null}
        {projects.error ? <ErrorNotice message={projects.error.message} onRetry={projects.refresh} /> : null}
        {projects.data?.items.length === 0 ? <div className="empty-state"><p className="font-semibold text-white">还没有研究项目</p><p>创建一个明确的 AI/Agent 研究问题，项目创建本身不会调用模型。</p><Link className="button button-primary mt-3" to="/projects/new"><FolderPlus size={15} />创建第一个项目</Link></div> : null}
        {projects.data?.items.length ? <ul className="project-list mt-4">{projects.data.items.map((project) => { const state = projectStatus(project.literaturePhase, project.literatureOutcome); return <li key={project.projectId}><Link className="project-row" to={`/projects/${project.projectId}`}><span className="min-w-0"><span className="block truncate font-semibold text-[#edf2f4]">{project.title}</span><span className="mt-1 block line-clamp-2 text-sm leading-6 text-[var(--muted)]">{project.researchQuestion}</span><span className="mt-2 block text-xs text-[#7f8a93]">更新于 {formatTime(project.updatedAt)}</span></span><span className="flex shrink-0 items-center gap-3"><Status value={state.value} label={state.label} /><ArrowRight size={16} /></span></Link></li>; })}</ul> : null}
      </section>

      <section className="section compact-callout" aria-labelledby="fixture-link-title">
        <Wrench aria-hidden="true" size={18} />
        <div><h2 className="section-title" id="fixture-link-title">工程夹具与证据框架</h2><p className="mt-1 text-sm text-[var(--muted)]">AR1 的 success、task_error、cancel 夹具仍可独立运行，不混入真实研究项目。</p></div>
        <Link className="button" to="/runs">打开工程夹具 <ArrowRight size={14} /></Link>
      </section>
      <p className="sr-only" aria-live="polite">{system.refreshing || projects.refreshing ? "状态正在更新" : "状态已更新"}</p>
    </div>
  );
}
