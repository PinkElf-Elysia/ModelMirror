import { ArrowLeft, Database, ExternalLink, Library, RefreshCw, SearchCheck } from "lucide-react";
import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, api } from "../api";
import { ErrorNotice, LoadingRows, PageHeader } from "../components/Page";
import { Status } from "../components/Status";
import { usePolling } from "../hooks/usePolling";
import type { LibraryCollection } from "../types";

function collectionName(collection: LibraryCollection) {
  return collection.name?.trim() || `集合 ${collection.id}`;
}

function isEligible(collection: LibraryCollection) {
  const indexed = (collection.indexed_document_count ?? 0) > 0;
  return indexed && collection.is_public === true && collection.agent_enabled === true;
}

export function ProjectSourcesPage() {
  const { projectId = "" } = useParams();
  const [action, setAction] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const project = usePolling(
    useCallback((signal: AbortSignal) => api.project(projectId, signal), [projectId]),
    5_000,
    Boolean(projectId),
  );
  const session = usePolling(useCallback((signal: AbortSignal) => api.literatureSession(signal), []), 10_000);
  const unlocked = session.data?.status === "ready";
  const hasResult = Boolean(project.data?.completedRunId);
  const sources = usePolling(
    useCallback((signal: AbortSignal) => api.sources(projectId, signal), [projectId]),
    10_000,
    hasResult,
  );
  const collections = usePolling(
    useCallback((signal: AbortSignal) => api.collections(signal), []),
    10_000,
    unlocked,
  );
  const zotero = usePolling(
    useCallback((signal: AbortSignal) => api.zoteroStatus(signal), []),
    10_000,
    unlocked,
  );

  const runAction = async (id: string, work: () => Promise<unknown>) => {
    setAction(id);
    setActionError(null);
    try {
      await work();
      collections.refresh();
      zotero.refresh();
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : "资料库操作未完成。");
    } finally {
      setAction(null);
    }
  };

  if (project.loading) return <div className="page"><LoadingRows count={6} /></div>;
  if (project.error || !project.data) return <div className="page"><PageHeader eyebrow="来源与资料库" title="无法读取项目" description="项目不存在，或项目文件未通过完整性检查。" /><ErrorNotice message={project.error?.message ?? "项目不可用"} onRetry={project.refresh} /></div>;

  return (
    <div className="page">
      <PageHeader
        eyebrow="来源与资料库"
        title={project.data.title}
        description="左侧复核本次研究实际引用的公开来源；右侧管理 LDR Library 与 Zotero，同步和索引都由上游能力完成。"
        actions={<Link className="button" to={`/projects/${projectId}`}><ArrowLeft size={15} />返回项目</Link>}
      />
      <div className="notice" role="note">集合只有在已建立索引、标记为公开且允许 Agent 使用时，才可主动选入下一次综述。模镜不会改变集合隐私设置。</div>

      <div className="research-split">
        <section className="section research-pane" aria-labelledby="source-list-title">
          <div className="flex items-center justify-between gap-3">
            <h2 className="section-title" id="source-list-title">本次研究来源</h2>
            {sources.data ? <Status value={sources.data.integrityStatus} /> : null}
          </div>
          {!hasResult ? <div className="empty-state"><p className="font-semibold text-white">尚无正式来源包</p><p>完成一次文献研究后，来源将在这里按上游顺序展示。</p></div> : null}
          {hasResult && sources.loading ? <LoadingRows count={5} /> : null}
          {sources.error ? <ErrorNotice message={sources.error.message} onRetry={sources.refresh} /> : null}
          {sources.data?.sources.length === 0 ? <div className="empty-state"><p className="font-semibold text-white">成果包没有来源</p><p>该结果不能作为完整文献成果使用，请返回项目检查同步错误。</p></div> : null}
          {sources.data?.sources.length ? (
            <ol className="source-list">
              {sources.data.sources.map((source, index) => (
                <li key={`${source.url}:${index}`}>
                  <span className="source-index">{source.index ?? index + 1}</span>
                  <a href={source.url} target="_blank" rel="noopener noreferrer">
                    <strong>{source.title || "未命名来源"}</strong>
                    <span>{source.url}</span>
                    <ExternalLink aria-hidden="true" size={14} />
                  </a>
                </li>
              ))}
            </ol>
          ) : null}
        </section>

        <aside className="section research-pane" aria-labelledby="library-title">
          <div className="flex items-start justify-between gap-3">
            <div><h2 className="section-title" id="library-title">LDR Library</h2><p className="mt-2 text-sm leading-6 text-[var(--muted)]">Zotero 密钥只在 LDR 中配置，Research Console 只读取非敏感状态。</p></div>
            <Library aria-hidden="true" className="text-[var(--cyan)]" size={19} />
          </div>
          {!unlocked ? <div className="empty-state"><p className="font-semibold text-white">需要先解锁 LDR</p><p>返回项目输入本地 LDR 账户，之后才能读取集合和 Zotero 状态。</p><Link className="button mt-3" to={`/projects/${projectId}`}>返回解锁</Link></div> : null}
          {unlocked && zotero.loading ? <LoadingRows count={1} /> : null}
          {zotero.error ? <ErrorNotice message={zotero.error.message} onRetry={zotero.refresh} /> : null}
          {zotero.data ? (
            <div className="library-status">
              <div><Database size={15} /><span>Zotero</span><Status value={zotero.data.config.configured || zotero.data.config.has_api_key ? "ready" : "not_ready"} label={zotero.data.config.configured || zotero.data.config.has_api_key ? "已配置" : "未配置"} /></div>
              <button className="button" type="button" disabled={Boolean(action)} onClick={() => runAction("zotero", () => api.syncZotero())}><RefreshCw className={action === "zotero" ? "animate-spin" : ""} size={14} />同步 Zotero</button>
            </div>
          ) : null}
          {actionError ? <ErrorNotice message={actionError} /> : null}
          {unlocked && collections.loading ? <LoadingRows count={4} /> : null}
          {collections.error ? <ErrorNotice message={collections.error.message} onRetry={collections.refresh} /> : null}
          {collections.data?.collections.length === 0 ? <div className="empty-state"><p className="font-semibold text-white">资料库暂无集合</p><p>请在 LDR 中同步 Zotero 或创建集合。</p></div> : null}
          {collections.data?.collections.length ? (
            <ul className="collection-list">
              {collections.data.collections.map((collection) => {
                const eligible = isEligible(collection);
                const selected = project.data?.collectionId === collection.id;
                return (
                  <li key={collection.id}>
                    <div className="flex items-start justify-between gap-3"><div className="min-w-0"><strong>{collectionName(collection)}</strong><p>{collection.indexed_document_count ?? 0} / {collection.document_count ?? 0} 篇已索引</p></div><Status value={eligible ? "ready" : "pending"} label={eligible ? "可用于综述" : "未通过使用门禁"} /></div>
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button className="button" type="button" disabled={Boolean(action)} onClick={() => runAction(`index:${collection.id}`, () => api.indexCollection(collection.id))}><SearchCheck size={14} />{action === `index:${collection.id}` ? "正在索引" : "建立索引"}</button>
                      {eligible ? <Link className={`button ${selected ? "button-primary" : ""}`} to={`/projects/${projectId}?collectionId=${encodeURIComponent(collection.id)}`}>{selected ? "当前项目集合" : "用于下一次研究"}</Link> : null}
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </aside>
      </div>
    </div>
  );
}
