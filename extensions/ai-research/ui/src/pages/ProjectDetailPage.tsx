import { BookOpen, ExternalLink, KeyRound, Library, LoaderCircle, RefreshCw, Square } from "lucide-react";
import { FormEvent, useCallback, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { ApiError, api } from "../api";
import { ErrorNotice, LoadingRows, PageHeader } from "../components/Page";
import { formatTime } from "../components/RunTable";
import { Status } from "../components/Status";
import { usePolling } from "../hooks/usePolling";

const stageLabels: Record<string, string> = {
  literature: "文献研究",
  hypothesis_protocol: "假设与协议",
  research_workspace: "研究工作区",
  evaluation: "评测执行",
  analysis_report: "分析与报告",
};

const literatureLabels: Record<string, string> = {
  not_started: "尚未开始",
  queued: "排队中",
  running: "研究中",
  terminal: "已终止",
  completed: "综述已完成",
  cancelled: "已取消",
  failed: "研究失败",
  infrastructure_error: "基础设施错误",
};

export function ProjectDetailPage() {
  const { projectId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const selectedCollection = (searchParams.get("collectionId") ?? "").trim().slice(0, 200) || undefined;
  const project = usePolling(useCallback((signal: AbortSignal) => api.project(projectId, signal), [projectId]), 2_000, Boolean(projectId), (value) => ["queued", "running"].includes(value.literaturePhase));
  const session = usePolling(useCallback((signal: AbortSignal) => api.literatureSession(signal), []), 10_000);
  const system = usePolling(useCallback((signal: AbortSignal) => api.system(signal), []), 10_000);
  const module = usePolling(useCallback((signal: AbortSignal) => api.module(signal), []), 60_000);
  const [action, setAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const runKey = useRef<string | null>(null);

  const unlock = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    setAction("unlock"); setError(null);
    try {
      await api.unlockLiterature(String(form.get("username") ?? ""), String(form.get("password") ?? ""));
      formElement.reset(); session.refresh(); project.refresh();
    } catch (caught) { setError(caught instanceof ApiError ? caught.message : "账户解锁失败。 "); }
    finally { setAction(null); }
  };
  const run = async () => {
    setAction("run"); setError(null); runKey.current ??= `literature:${crypto.randomUUID()}`;
    try { await api.startLiterature(projectId, runKey.current, selectedCollection ?? project.data?.collectionId ?? undefined); runKey.current = null; project.refresh(); }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : "研究启动请求未完成。 "); }
    finally { setAction(null); }
  };
  const cancel = async () => { setAction("cancel"); setError(null); try { await api.cancelLiterature(projectId); setConfirmCancel(false); project.refresh(); } catch (caught) { setError(caught instanceof ApiError ? caught.message : "取消请求未完成。 "); } finally { setAction(null); } };
  const sync = async () => { setAction("sync"); setError(null); try { await api.syncLiterature(projectId); project.refresh(); } catch (caught) { setError(caught instanceof ApiError ? caught.message : "成果同步未完成。 "); } finally { setAction(null); } };

  if (project.loading) return <div className="page"><LoadingRows count={6} /></div>;
  if (project.error || !project.data) return <div className="page"><PageHeader eyebrow="Research Project" title="无法读取项目" description="项目不存在，或项目文件未通过完整性检查。" /><ErrorNotice message={project.error?.message ?? "项目不可用"} onRetry={project.refresh} /></div>;
  const value = project.data;
  const attempt = value.attempts.at(-1);
  const active = value.literaturePhase === "queued" || value.literaturePhase === "running";
  const completedAttempt = value.completedRunId
    ? value.attempts.find((candidate) => candidate.runId === value.completedRunId)
    : undefined;
  const formallyCompleted = completedAttempt?.integrityStatus === "verified";
  const canSyncArtifacts =
    attempt?.rawStatus === "completed" &&
    attempt.integrityStatus === "failed" &&
    Boolean(attempt.ldrResearchId);
  const literatureReady = system.data?.literatureCapability?.status === "ready";
  const readinessMessage = system.loading
    ? "正在检查固定模型执行资格。"
    : system.error
      ? "无法验证固定模型执行资格，已停止启动新研究。"
      : !literatureReady
        ? "固定模型执行资格未就绪，恢复 Provider 资格后再重试。"
        : null;
  const statusLabel = active
    ? literatureLabels[value.literaturePhase]
    : formallyCompleted
      ? literatureLabels.completed
      : attempt?.integrityStatus === "failed"
        ? "成果不完整"
        : literatureLabels[value.literatureOutcome ?? value.literaturePhase];

  return (
    <div className="page">
      <PageHeader eyebrow="AI / Agent Research" title={value.title} description={value.researchQuestion} actions={<Status value={active ? "running" : formallyCompleted ? "success" : value.literatureOutcome ? "failed" : "pending"} label={statusLabel} />} />
      <div className="notice" role="note">本轮复用 Local Deep Research 形成真实文献研究与来源包；结果需人工复核，不构成科研结论。</div>

      <ol className="stage-track" aria-label="科研阶段">
        {Object.entries(value.stages).map(([id, state], index) => <li className={state === "active" ? "stage-active" : "stage-disabled"} key={id}><span>{index + 1}</span><div><strong>{stageLabels[id] ?? id}</strong><small>{state === "active" ? "当前开放" : "后续轮次"}</small></div></li>)}
      </ol>

      <section className="section" aria-labelledby="literature-control-title">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="section-title" id="literature-control-title">文献研究</h2><p className="mt-2 text-sm text-[var(--muted)]">固定模型、OpenAlex 主检索、2 轮研究，不向项目暴露调参。</p></div><div className="flex flex-wrap gap-2"><Link className="button" to={`/projects/${projectId}/sources`}><Library size={16} />来源与资料库</Link><Link className="button" to={`/projects/${projectId}/review`}><BookOpen size={16} />查看综述</Link></div></div>
        {attempt ? <dl className="definition-grid mt-4"><div className="definition-row"><dt>上游状态</dt><dd>{attempt.rawStatus ?? "等待对账"}</dd></div><div className="definition-row"><dt>进度</dt><dd>{attempt.progress}%</dd></div><div className="definition-row"><dt>成果完整性</dt><dd>{attempt.integrityStatus}</dd></div><div className="definition-row"><dt>开始时间</dt><dd>{formatTime(attempt.startedAt ?? attempt.createdAt)}</dd></div></dl> : null}
        {attempt?.latestLog ? <pre className="code-block mt-4">{JSON.stringify(attempt.latestLog, null, 2)}</pre> : null}
        {attempt?.integrityStatus === "failed" ? (
          <div className="mt-4">
            <ErrorNotice message="上游研究已结束，但报告、来源或引用成果包未通过完整性校验。可先重新同步；如果仍失败，请在 Local Deep Research 检查本次导出。" />
            {attempt.errorMessage ? <details className="mt-2 text-xs leading-5 text-[var(--muted)]"><summary className="cursor-pointer text-[#c7d1d6]">查看技术原因</summary><p className="mt-2 break-words">{attempt.errorMessage}</p></details> : null}
          </div>
        ) : null}
        {error ? <ErrorNotice message={error} /> : null}
        <div className="mt-4 flex flex-wrap gap-2">
          {!active && !formallyCompleted && session.data?.status === "ready" ? <button className="button button-primary" type="button" disabled={Boolean(action) || !literatureReady} onClick={run}>{action === "run" ? <LoaderCircle className="animate-spin" size={16} /> : <BookOpen size={16} />}{action === "run" ? "正在启动" : attempt ? "重试文献研究" : "开始文献研究"}</button> : null}
          {active && !confirmCancel ? <button className="button button-danger" type="button" disabled={Boolean(action)} onClick={() => setConfirmCancel(true)}><Square size={14} />请求取消</button> : null}
          {canSyncArtifacts ? <button className="button" type="button" disabled={Boolean(action)} onClick={sync}><RefreshCw size={15} />重新同步成果</button> : null}
        </div>
        {!active && !formallyCompleted && session.data?.status === "ready" && readinessMessage ? <p className="mt-3 text-sm text-[var(--amber)]" role="status" aria-live="polite">{readinessMessage}</p> : null}
        {(selectedCollection ?? value.collectionId) && !active && !formallyCompleted ? <p className="mt-3 text-sm text-[#a6d8c0]">下一次研究会将已通过门禁的集合 <code>{selectedCollection ?? value.collectionId}</code> 提供给研究 Agent；OpenAlex 仍为主检索。Control 会在启动前重新验证其索引、Agent 可用性和 egress 状态。</p> : null}
        {active && confirmCancel ? <div className="confirmation-row" role="group" aria-label="确认取消文献研究"><p>取消会向 Local Deep Research 发送终止请求，并保留原始终态与取消事实。</p><div className="flex shrink-0 gap-2"><button className="button" type="button" onClick={() => setConfirmCancel(false)}>继续运行</button><button className="button button-danger" type="button" disabled={Boolean(action)} onClick={cancel}>{action === "cancel" ? "正在取消" : "确认取消"}</button></div></div> : null}
      </section>

      {session.data?.status !== "ready" ? <section className="section" aria-labelledby="unlock-title"><div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="section-title" id="unlock-title">解锁 Local Deep Research</h2><p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--muted)]">密码和会话只保留在 Control 内存。Control 重启后需要重新解锁，项目与成果不会删除。</p></div>{module.data ? <a className="button" href={`${module.data.links.localDeepResearch}/auth/register`} target="_blank" rel="noopener noreferrer">注册或管理 LDR <ExternalLink size={14} /></a> : null}</div><form className="mt-4 grid gap-3 md:grid-cols-[1fr_1fr_auto] md:items-end" onSubmit={unlock}><label className="field-label">LDR 用户名<input className="field" name="username" required minLength={3} maxLength={64} autoComplete="username" /></label><label className="field-label">LDR 密码<input className="field" name="password" required type="password" autoComplete="current-password" /></label><button className="button button-primary" type="submit" disabled={Boolean(action)}><KeyRound size={16} />{action === "unlock" ? "正在解锁" : "解锁账户"}</button></form></section> : <p className="section flex items-center gap-2 text-sm text-[#a6d8c0]"><KeyRound size={15} />已解锁为 {session.data.username}，可启动或恢复研究。</p>}
    </div>
  );
}
