import { ArrowLeft, Download, ExternalLink, FileArchive, ShieldCheck } from "lucide-react";
import { useCallback } from "react";
import ReactMarkdown from "react-markdown";
import { Link, useParams } from "react-router-dom";
import remarkGfm from "remark-gfm";

import { api } from "../api";
import { ErrorNotice, LoadingRows, PageHeader } from "../components/Page";
import { Status } from "../components/Status";
import { usePolling } from "../hooks/usePolling";

const artifacts = [
  ["literature-review.md", "Markdown 综述"],
  ["literature-review.qmd", "Quarto 文稿"],
  ["references.bib", "BibTeX 引用"],
  ["references.ris", "RIS 引用"],
  ["sources.json", "来源清单"],
  ["artifact-manifest.json", "完整性 manifest"],
  ["literature-receipt.json", "研究 receipt"],
  ["upstream-quarto.zip", "上游 Quarto ZIP"],
] as const;

function safeHttps(value: string | undefined) {
  if (!value) return undefined;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" ? parsed.toString() : undefined;
  } catch {
    return undefined;
  }
}

export function ProjectReviewPage() {
  const { projectId = "" } = useParams();
  const project = usePolling(
    useCallback((signal: AbortSignal) => api.project(projectId, signal), [projectId]),
    10_000,
    Boolean(projectId),
  );
  const hasResult = Boolean(project.data?.completedRunId);
  const review = usePolling(
    useCallback((signal: AbortSignal) => api.review(projectId, signal), [projectId]),
    30_000,
    hasResult,
  );
  const hasGapSection = /(^|\n)#{1,6}\s+.*(?:research\s+gaps?|研究缺口|研究空白)/im.test(review.data?.markdown ?? "");

  if (project.loading) return <div className="page"><LoadingRows count={6} /></div>;
  if (project.error || !project.data) return <div className="page"><PageHeader eyebrow="文献综述" title="无法读取项目" description="项目不存在，或项目文件未通过完整性检查。" /><ErrorNotice message={project.error?.message ?? "项目不可用"} onRetry={project.refresh} /></div>;

  return (
    <div className="page review-page">
      <PageHeader
        eyebrow="文献综述"
        title={project.data.title}
        description="原样呈现 Local Deep Research 的 Markdown 报告，并提供经过哈希验证的引用与导出包。"
        actions={<Link className="button" to={`/projects/${projectId}`}><ArrowLeft size={15} />返回项目</Link>}
      />
      <div className="notice" role="note">该报告由上游研究流程生成，需人工核对来源与论证，不构成模镜的科研结论。</div>

      <div className="review-split">
        <article className="section review-document" aria-labelledby="review-document-title">
          <div className="flex items-center justify-between gap-3"><h2 className="section-title" id="review-document-title">综述正文</h2>{review.data ? <Status value={review.data.integrityStatus} /> : null}</div>
          {!hasResult ? <div className="empty-state"><p className="font-semibold text-white">尚无正式综述</p><p>完成并同步一次文献研究后，报告将在这里显示。</p></div> : null}
          {hasResult && review.loading ? <LoadingRows count={8} /> : null}
          {review.error ? <ErrorNotice message={review.error.message} onRetry={review.refresh} /> : null}
          {review.data ? (
            <div className="markdown-body">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                urlTransform={(url) => safeHttps(url) ?? ""}
                components={{
                  a: ({ href, children }) => {
                    const safe = safeHttps(href);
                    return safe ? <a href={safe} target="_blank" rel="noopener noreferrer">{children}<ExternalLink aria-hidden="true" size={12} /></a> : <span>{children}</span>;
                  },
                  img: () => null,
                }}
              >{review.data.markdown}</ReactMarkdown>
            </div>
          ) : null}
        </article>

        <aside className="section review-assets" aria-labelledby="artifact-package-title">
          <div className="flex items-start justify-between gap-3"><div><h2 className="section-title" id="artifact-package-title">成果包</h2><p className="mt-2 text-sm leading-6 text-[var(--muted)]">下载前由 Control 再次核验文件名、路径、大小与 SHA-256。</p></div><FileArchive aria-hidden="true" className="text-[var(--cyan)]" size={19} /></div>
          {review.data ? <ul className="artifact-list">{artifacts.map(([name, label]) => <li key={name}><a href={`/api/v1/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(name)}`} download><span><strong>{label}</strong><small>{name}</small></span><Download aria-hidden="true" size={15} /></a></li>)}</ul> : <p className="mt-4 text-sm text-[var(--muted)]">成果同步完成后开放下载。</p>}
          <div className="constraint-panel mt-5"><p className="flex items-center gap-2 font-semibold text-[#dbe4e7]"><ShieldCheck size={15} />研究缺口</p><p className="mt-2 text-sm leading-6 text-[var(--muted)]">{hasGapSection ? "上游报告包含研究缺口章节，请结合来源人工复核。" : "上游报告未提供独立研究缺口章节；模镜不会自行补写。下一次可在研究问题中明确要求分析研究缺口。"}</p></div>
        </aside>
      </div>
    </div>
  );
}
