import { ArrowRight, FolderPlus, Search } from "lucide-react";
import { FormEvent, useCallback, useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api";
import { ErrorNotice, LoadingRows, PageHeader } from "../components/Page";
import { formatTime } from "../components/RunTable";
import { Status } from "../components/Status";
import { usePolling } from "../hooks/usePolling";
import type { LiteratureOutcome, LiteraturePhase, ResearchProject } from "../types";

const phases: LiteraturePhase[] = ["not_started", "queued", "running", "terminal"];
const outcomes: LiteratureOutcome[] = ["completed", "cancelled", "failed", "infrastructure_error"];

function phaseLabel(value: LiteraturePhase) {
  return { not_started: "未开始", queued: "排队中", running: "研究中", terminal: "已终止" }[value];
}

function outcomeLabel(value: LiteratureOutcome) {
  return {
    completed: "综述已完成",
    cancelled: "已取消",
    failed: "需要处理",
    infrastructure_error: "系统异常",
  }[value];
}

function projectStatus(project: ResearchProject) {
  if (project.literaturePhase === "running" || project.literaturePhase === "queued") {
    return { value: "running" as const, label: phaseLabel(project.literaturePhase) };
  }
  const completedAttempt = project.completedRunId
    ? project.attempts.find((attempt) => attempt.runId === project.completedRunId)
    : undefined;
  if (completedAttempt?.integrityStatus === "verified") {
    return { value: "success" as const, label: "综述已完成" };
  }
  const latestAttempt = project.attempts.at(-1);
  if (latestAttempt?.integrityStatus === "failed") {
    return { value: "failed" as const, label: "成果不完整" };
  }
  if (project.literatureOutcome === "cancelled") {
    return { value: "cancelled" as const, label: "已取消" };
  }
  if (project.literatureOutcome) {
    return { value: "failed" as const, label: outcomeLabel(project.literatureOutcome) };
  }
  return { value: "pending" as const, label: phaseLabel(project.literaturePhase) };
}

export function ProjectsPage() {
  const [params, setParams] = useSearchParams();
  const query = (params.get("q") ?? "").slice(0, 100);
  const phase = phases.includes(params.get("literaturePhase") as LiteraturePhase)
    ? (params.get("literaturePhase") as LiteraturePhase)
    : "";
  const outcome = outcomes.includes(params.get("literatureOutcome") as LiteratureOutcome)
    ? (params.get("literatureOutcome") as LiteratureOutcome)
    : "";
  const requestParams = useMemo(() => {
    const value = new URLSearchParams({ limit: "50" });
    if (query) value.set("q", query);
    if (phase) value.set("literaturePhase", phase);
    if (outcome) value.set("literatureOutcome", outcome);
    return value;
  }, [outcome, phase, query]);
  const projects = usePolling(
    useCallback((signal: AbortSignal) => api.projects(requestParams, signal), [requestParams]),
    5_000,
  );

  const update = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    next.delete("cursor");
    setParams(next, { replace: true });
  };
  const search = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    update("q", String(data.get("q") ?? "").slice(0, 100).trim());
  };

  return (
    <div className="page">
      <PageHeader
        eyebrow="Research Projects"
        title="研究项目"
        description="每个项目保存研究问题、文献运行、来源和完整性状态，关闭页面或重启 Control 后仍可恢复。"
        actions={<Link className="button button-primary" to="/projects/new"><FolderPlus size={16} />创建项目</Link>}
      />
      <div className="notice" role="note">本轮复用 Local Deep Research 形成真实文献研究与来源包；结果需人工复核，不构成科研结论。</div>

      <section className="section" aria-labelledby="project-filter-title">
        <h2 className="section-title" id="project-filter-title">筛选项目</h2>
        <div className="mt-4 grid gap-3 md:grid-cols-[minmax(220px,1fr)_180px_210px]">
          <form className="flex gap-2" onSubmit={search}>
            <label className="sr-only" htmlFor="project-search">搜索项目</label>
            <input className="field" id="project-search" name="q" maxLength={100} defaultValue={query} placeholder="标题、问题或项目 ID" />
            <button className="button" type="submit" aria-label="搜索项目"><Search size={16} /></button>
          </form>
          <select className="field" aria-label="文献阶段" value={phase} onChange={(event) => update("literaturePhase", event.target.value)}>
            <option value="">全部阶段</option>
            {phases.map((item) => <option key={item} value={item}>{phaseLabel(item)}</option>)}
          </select>
          <select className="field" aria-label="文献结果" value={outcome} onChange={(event) => update("literatureOutcome", event.target.value)}>
            <option value="">全部结果</option>
            {outcomes.map((item) => <option key={item} value={item}>{outcomeLabel(item)}</option>)}
          </select>
        </div>
      </section>

      <section className="section" aria-labelledby="project-list-title">
        <div className="flex items-center justify-between gap-3">
          <h2 className="section-title" id="project-list-title">项目列表</h2>
          <span className="text-xs text-[var(--muted)]" aria-live="polite">{projects.data ? `${projects.data.items.length} 个结果` : "正在读取"}</span>
        </div>
        {projects.loading ? <LoadingRows count={4} /> : null}
        {projects.error ? <ErrorNotice message={projects.error.message} onRetry={projects.refresh} /> : null}
        {projects.data?.items.length === 0 ? (
          <div className="empty-state"><p className="font-semibold text-white">没有匹配的研究项目</p><p>调整筛选，或创建一个 AI/Agent 研究问题。</p></div>
        ) : null}
        {projects.data?.items.length ? (
          <ul className="project-list mt-4">
            {projects.data.items.map((project) => {
              const status = projectStatus(project);
              return <li key={project.projectId}>
                <Link className="project-row" to={`/projects/${project.projectId}`}>
                  <span className="min-w-0">
                    <span className="block truncate font-semibold text-[#edf2f4]">{project.title}</span>
                    <span className="mt-1 block line-clamp-2 text-sm leading-6 text-[var(--muted)]">{project.researchQuestion}</span>
                    <span className="mt-2 block text-xs text-[#7f8a93]">更新于 {formatTime(project.updatedAt)}</span>
                  </span>
                  <span className="flex shrink-0 items-center gap-3">
                    <Status value={status.value} label={status.label} />
                    <ArrowRight aria-hidden="true" size={16} />
                  </span>
                </Link>
              </li>;
            })}
          </ul>
        ) : null}
      </section>
    </div>
  );
}
