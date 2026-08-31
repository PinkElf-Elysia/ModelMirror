import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Boxes,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  Compass,
  ExternalLink,
  Menu,
  Search,
  ShieldCheck,
} from "lucide-react";
import { useEffect, type FormEvent, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import remarkGfm from "remark-gfm";
import PageContainer from "../components/PageContainer";
import {
  findHelpArticle,
  findHelpModule,
  findHelpModuleTopic,
  findHelpSection,
  helpContentTypeLabels,
  helpModules,
  helpSections,
  verifiedBaseline,
  type HelpArticle,
  type HelpModule,
  type HelpModuleTopic,
  type HelpSection,
} from "../content/help-center";

function headingId(title: string) {
  return title.trim().toLocaleLowerCase().replace(/[“”‘’"'：:，,。.!！?？、/]/g, "").replace(/\s+/g, "-");
}

function childrenText(children: ReactNode) {
  return Array.isArray(children) ? children.join("") : String(children ?? "");
}

function getTableOfContents(markdown: string) {
  return markdown.split("\n").filter((line) => line.startsWith("## ")).map((line) => line.slice(3).trim()).map((title) => ({ id: headingId(title), title }));
}

const markdownComponents: Components = {
  h2: ({ children }) => <h2 className="scroll-mt-28 border-t border-white/10 pt-8 text-2xl font-bold text-white first:border-t-0 first:pt-0" id={headingId(childrenText(children))}>{children}</h2>,
  h3: ({ children }) => <h3 className="text-lg font-bold text-slate-100">{children}</h3>,
  p: ({ children, node }) => {
    const isStandaloneImage = node?.children.length === 1
      && node.children[0].type === "element"
      && node.children[0].tagName === "img";
    return isStandaloneImage
      ? <>{children}</>
      : <p className="text-base leading-8 tracking-[0.005em] text-slate-300">{children}</p>;
  },
  ul: ({ children }) => <ul className="list-disc space-y-2 pl-6 text-base leading-8 text-slate-300 marker:text-cyan-300">{children}</ul>,
  ol: ({ children, start }) => <ol className="list-decimal space-y-4 pl-6 text-base leading-8 text-slate-300 marker:font-bold marker:text-cyan-200" start={start}>{children}</ol>,
  li: ({ children }) => <li className="pl-1">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-slate-100">{children}</strong>,
  code: ({ children }) => <code className="rounded bg-white/[0.07] px-1.5 py-0.5 text-[0.95em] text-cyan-100">{children}</code>,
  a: ({ children, href = "" }) => href.startsWith("/") ? <Link className="font-semibold text-cyan-200 underline decoration-cyan-300/35 underline-offset-4 hover:text-cyan-100" to={href}>{children}</Link> : <a className="inline-flex items-center gap-1 font-semibold text-cyan-200 underline decoration-cyan-300/35 underline-offset-4 hover:text-cyan-100" href={href} rel="noreferrer" target="_blank">{children}<ExternalLink aria-label="外部链接" className="h-3.5 w-3.5" /></a>,
  img: ({ alt = "", src }) => <figure className="my-7"><img alt={alt} className="h-auto w-full rounded-xl border border-white/10 bg-[#04111f] shadow-panel" loading="lazy" src={src} /><figcaption className="mt-2 text-sm leading-6 text-slate-500">{alt}</figcaption></figure>,
  table: ({ children }) => <div className="w-full overflow-x-auto rounded-xl border border-white/10"><table className="w-full min-w-[620px] text-left text-sm text-slate-300">{children}</table></div>,
  thead: ({ children }) => <thead className="bg-white/[0.055] text-slate-100">{children}</thead>,
  th: ({ children }) => <th className="min-w-32 border-b border-white/10 px-4 py-3 font-semibold">{children}</th>,
  td: ({ children }) => <td className="border-b border-white/[0.07] px-4 py-3 align-top leading-6">{children}</td>,
};

function DirectoryLink({ active, children, current = active, to }: { active?: boolean; children: ReactNode; current?: boolean; to: string }) {
  return <Link aria-current={current ? "page" : undefined} className={`flex min-h-10 items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium transition ${active ? "bg-cyan-300/12 text-cyan-100 shadow-[inset_2px_0_0_rgba(34,211,238,0.9)]" : "text-slate-300 hover:bg-white/[0.055] hover:text-white"}`} to={to}>{children}</Link>;
}

function HelpDirectory({ activeModuleId, activeSectionId, activeTopicId }: { activeModuleId?: string; activeSectionId?: string; activeTopicId?: string }) {
  const location = useLocation();
  const navigate = useNavigate();
  const submitSearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = new FormData(event.currentTarget).get("q")?.toString().trim() ?? "";
    navigate(value ? `/help?q=${encodeURIComponent(value)}` : "/help");
  };
  const sectionIcons = { "getting-started": BookOpen, goals: Compass, modules: Boxes, troubleshooting: CircleHelp, safety: ShieldCheck } as const;
  const currentTarget = `${location.pathname}${location.hash}`;
  const itemIsCurrent = (to: string) => {
    const [pathname, hash] = to.split("#");
    if (pathname !== location.pathname) return false;
    if (!hash) return !location.hash;
    let currentHash = location.hash.slice(1);
    try {
      currentHash = decodeURIComponent(currentHash);
    } catch {
      // Compare the original hash when it is not valid percent-encoded text.
    }
    return currentHash === hash;
  };
  const renderSection = (section: HelpSection) => {
    const Icon = sectionIcons[section.id];
    const isOpen = activeSectionId === section.id;
    return (
      <div key={section.id}>
        <DirectoryLink active={isOpen} current={location.pathname === section.path} to={section.path}>
          <Icon aria-hidden="true" className="h-4 w-4 text-cyan-300" />
          <span className="min-w-0 flex-1">{section.title}</span>
          {isOpen ? <ChevronDown aria-hidden="true" className="h-4 w-4 text-slate-500" /> : <ChevronRight aria-hidden="true" className="h-4 w-4 text-slate-600" />}
        </DirectoryLink>
        {isOpen ? (
          <div className="ml-4 mt-1 space-y-0.5 border-l border-cyan-300/15 pl-2">
            {section.items.map((item) => (
              <DirectoryLink active={itemIsCurrent(item.to)} key={item.id} to={item.to}>
                <span className="min-w-0 flex-1">{item.title}</span>
              </DirectoryLink>
            ))}
          </div>
        ) : null}
      </div>
    );
  };

  return (
    <nav aria-label="帮助目录">
      <h2 className="text-lg font-bold text-white">帮助目录</h2>
      <form className="relative mt-4" onSubmit={submitSearch} role="search">
        <label className="sr-only" htmlFor="help-directory-search">搜索帮助</label>
        <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
        <input className="h-11 w-full rounded-lg border border-white/15 bg-[#04111f]/85 pl-10 pr-3 text-sm text-white placeholder:text-slate-500 focus:border-cyan-300/50 focus:outline-none" id="help-directory-search" name="q" placeholder="搜索帮助" />
      </form>
      <div className="mt-4 space-y-1">
        {helpSections.slice(0, 2).map(renderSection)}

        <div className="pt-1">
          <DirectoryLink active={activeSectionId === "modules"} current={location.pathname === "/help/sections/modules"} to="/help/sections/modules"><Boxes aria-hidden="true" className="h-4 w-4 text-cyan-300" /><span className="min-w-0 flex-1">按模块浏览</span><ChevronDown aria-hidden="true" className="h-4 w-4 text-slate-500" /></DirectoryLink>
          <div className="ml-4 mt-1 space-y-1 border-l border-white/10 pl-2">
            <DirectoryLink active={currentTarget === "/help/modules-and-terms"} to="/help/modules-and-terms"><span className="min-w-0 flex-1">整体结构与常用词</span></DirectoryLink>
            {helpModules.map((module) => {
              const isOpen = module.id === activeModuleId;
              return (
                <div key={module.id}>
                  <DirectoryLink active={isOpen} current={currentTarget === `/help/modules/${module.id}`} to={`/help/modules/${module.id}`}><span className="min-w-0 flex-1">{module.title}</span><ChevronRight aria-hidden="true" className={`h-4 w-4 text-slate-600 ${isOpen ? "rotate-90" : ""}`} /></DirectoryLink>
                  {isOpen ? <div className="ml-3 space-y-0.5 border-l border-cyan-300/15 pl-2">{module.topics.map((topic) => <DirectoryLink active={activeTopicId === topic.id} key={topic.id} to={`/help/modules/${module.id}/${topic.id}`}><span className="min-w-0 flex-1">{topic.title}</span>{topic.badge ? <span className="rounded border border-amber-300/25 px-1.5 py-0.5 text-[10px] text-amber-200">{topic.badge}</span> : null}</DirectoryLink>)}</div> : null}
                </div>
              );
            })}
          </div>
        </div>

        {helpSections.slice(3).map(renderSection)}
      </div>
      <div className="mt-6 border-t border-white/10 pt-4"><DirectoryLink active={location.pathname === "/help"} to="/help"><ArrowLeft aria-hidden="true" className="h-4 w-4" />返回帮助首页</DirectoryLink></div>
    </nav>
  );
}

function Metadata({
  audience,
  minutes,
  verifiedCommit = verifiedBaseline.commit,
  verifiedDate = verifiedBaseline.date,
}: {
  audience: string;
  minutes: number;
  verifiedCommit?: string;
  verifiedDate?: string;
}) {
  return (
    <div>
      <dl className="mt-5 flex flex-wrap gap-x-5 gap-y-2 text-sm text-slate-400">
        <div className="flex gap-1.5"><dt className="sr-only">适用对象</dt><dd>{audience}</dd></div>
        <div className="flex items-center gap-1.5"><Clock3 aria-hidden="true" className="h-4 w-4" /><dt className="sr-only">预计阅读时间</dt><dd>约 {minutes} 分钟</dd></div>
        <div className="flex gap-1.5"><dt>界面核对</dt><dd>{verifiedDate}</dd></div>
      </dl>
      <details className="mt-3 text-xs text-slate-500">
        <summary className="w-fit cursor-pointer py-1 hover:text-slate-300">维护信息</summary>
        <p className="mt-1">验证基线 <code className="font-mono text-slate-400">{verifiedCommit}</code></p>
      </details>
    </div>
  );
}

function PageHeader({ eyebrow, summary, title }: { eyebrow: string; summary: string; title: string }) {
  return <header className="border-b border-white/10 pb-7"><p className="text-sm font-semibold text-cyan-200">{eyebrow}</p><h1 className="mt-3 text-4xl font-bold tracking-tight text-white sm:text-5xl">{title}</h1><p className="mt-4 max-w-3xl text-base leading-7 text-slate-300">{summary}</p></header>;
}

function ModulePage({ module, topic }: { module: HelpModule; topic?: HelpModuleTopic }) {
  if (!topic) {
    return (
      <>
        <PageHeader eyebrow="模块目录" summary={module.summary} title={module.title} />
        <Metadata audience="适合准备使用这一模块的用户" minutes={3} />
        <div className="mt-9 max-w-[76ch] space-y-10">
          <section>
            <h2 className="text-2xl font-bold text-white">二级功能</h2>
            <p className="mt-3 text-base leading-8 text-slate-300">选择要完成的任务。节点、工具、模型和配置项等下一级内容会在相应指南中说明，不在这里全部展开。</p>
            <div className="mt-5 divide-y divide-white/[0.08] border-y border-white/[0.08]">
              {module.topics.map((item) => (
                <Link className="group flex min-h-[76px] items-center gap-4 py-4" key={item.id} to={`/help/modules/${module.id}/${item.id}`}>
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2 font-semibold text-slate-100 group-hover:text-cyan-100">
                      {item.title}
                      {item.badge ? <span className="rounded border border-amber-300/25 px-1.5 py-0.5 text-[10px] text-amber-200">{item.badge}</span> : null}
                    </span>
                    <span className="mt-1 block text-sm leading-6 text-slate-400">{item.summary}</span>
                  </span>
                  <ChevronRight aria-hidden="true" className="h-4 w-4 shrink-0 text-slate-600 group-hover:text-cyan-200" />
                </Link>
              ))}
            </div>
          </section>
          {module.productRoute ? <Link className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-cyan-300/30 bg-cyan-300/[0.08] px-4 text-sm font-semibold text-cyan-100 hover:bg-cyan-300/[0.12]" to={module.productRoute}>打开模块入口<ArrowRight aria-hidden="true" className="h-4 w-4" /></Link> : null}
        </div>
      </>
    );
  }

  const current = topic;
  return (
    <>
      <PageHeader eyebrow={module.title} summary={current.summary} title={current.title} />
      <Metadata audience="适合准备使用这一模块的用户" minutes={3} verifiedCommit={current.verifiedCommit} verifiedDate={current.verifiedDate} />
      <div className="mt-9 max-w-[76ch] space-y-10">
        <section><h2 className="text-2xl font-bold text-white">你可以做什么</h2><p className="mt-3 text-base leading-8 text-slate-300">{current.outcome}</p><div className="mt-5 divide-y divide-white/[0.08] border-y border-white/[0.08]">{current.points.map((point) => <div className="flex gap-3 py-4" key={point}><span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-cyan-300" /><p className="text-base leading-7 text-slate-300">{point}</p></div>)}</div></section>
        <section><h2 className="text-2xl font-bold text-white">同一模块的其他功能</h2><div className="mt-4 divide-y divide-white/[0.08] border-y border-white/[0.08]">{module.topics.filter((item) => item.id !== current.id).map((item) => <Link className="group flex min-h-[72px] items-center gap-4 py-4" key={item.id} to={`/help/modules/${module.id}/${item.id}`}><span className="min-w-0 flex-1"><span className="block font-semibold text-slate-100 group-hover:text-cyan-100">{item.title}</span><span className="mt-1 block text-sm leading-6 text-slate-400">{item.summary}</span></span><ChevronRight aria-hidden="true" className="h-4 w-4 shrink-0 text-slate-600 group-hover:text-cyan-200" /></Link>)}</div></section>
        {current.productRoute ? <Link className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-cyan-300/30 bg-cyan-300/[0.08] px-4 text-sm font-semibold text-cyan-100 hover:bg-cyan-300/[0.12]" to={current.productRoute}>打开当前产品入口<ArrowRight aria-hidden="true" className="h-4 w-4" /></Link> : <p className="rounded-xl border border-amber-300/15 bg-amber-300/[0.045] p-4 text-sm leading-6 text-amber-100">当前最新基线没有确认可直接进入的独立产品入口。请以页面可见导航和开放状态为准，不要猜测地址。</p>}
      </div>
    </>
  );
}

const sectionGuidance: Record<string, string[]> = {
  "no-response": ["先确认页面是否仍在加载，并只等待一个明确结果。", "查看按钮是否禁用、是否出现错误或权限提示。", "不要连续重复点击；记录页面、任务和可见提示后再求助。"],
  configuration: ["普通用户不要自行尝试不明凭据或修改系统开关。", "记录需要完成的任务、当前入口和原始提示。", "由有权限的人检查连接、配额和功能开关。"],
};

function SectionPage({ section }: { section: HelpSection }) {
  return (
    <>
      <PageHeader eyebrow="一级索引" summary={section.summary} title={section.title} />
      <Metadata audience="适合按任务快速定位帮助的用户" minutes={3} />
      <div className="mt-9 max-w-[82ch] space-y-5">
        {section.items.map((item) => {
          const guidance = sectionGuidance[item.id];
          return (
            <section className="scroll-mt-28 rounded-xl border border-white/10 bg-[#071a2b]/68 p-5" id={item.id} key={item.id}>
              <div className="flex items-start gap-4"><div className="min-w-0 flex-1"><h2 className="text-xl font-bold text-white">{item.title}</h2><p className="mt-2 text-sm leading-6 text-slate-400">{item.summary}</p></div><Link aria-label={`打开${item.title}`} className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-white/10 text-slate-400 hover:border-cyan-300/30 hover:text-cyan-100" to={item.to}><ArrowRight aria-hidden="true" className="h-4 w-4" /></Link></div>
              {guidance ? <ul className="mt-4 space-y-2 border-t border-white/[0.08] pt-4 text-sm leading-6 text-slate-300">{guidance.map((line) => <li className="flex gap-3" key={line}><span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-cyan-300" />{line}</li>)}</ul> : null}
            </section>
          );
        })}
      </div>
    </>
  );
}

function ArticlePage({ article }: { article: HelpArticle }) {
  const toc = getTableOfContents(article.content);
  const nextArticle = findHelpArticle(article.nextSlug);
  return (
    <>
      <PageHeader eyebrow={`${article.category} · ${helpContentTypeLabels[article.contentType]}`} summary={article.summary} title={article.title} />
      <Metadata
        audience={article.audience}
        minutes={article.estimatedMinutes}
        verifiedCommit={article.verifiedCommit}
        verifiedDate={article.verifiedDate}
      />
      <details className="mt-6 max-w-[72ch] rounded-xl border border-white/10 bg-[#071a2b]/68 p-4"><summary className="min-h-11 cursor-pointer py-2 text-sm font-bold text-slate-100">本文目录</summary><ol className="mt-3 grid gap-2 border-t border-white/10 pt-4 sm:grid-cols-2">{toc.map((item) => <li key={item.id}><a className="block py-1 text-sm leading-5 text-slate-400 hover:text-cyan-100" href={`#${item.id}`}>{item.title}</a></li>)}</ol></details>
      <article className="help-article mt-9 max-w-[72ch] space-y-6"><ReactMarkdown components={markdownComponents} remarkPlugins={[remarkGfm]}>{article.content}</ReactMarkdown></article>
      {nextArticle ? <aside aria-label="下一篇建议" className="mt-10 max-w-[72ch] border-t border-white/10 pt-7"><p className="text-sm font-semibold text-slate-500">下一篇建议</p><Link className="group mt-3 flex items-center justify-between gap-4 rounded-xl border border-white/10 bg-[#071a2b]/68 p-4 hover:border-cyan-300/30" to={`/help/${nextArticle.slug}`}><span><span className="block font-semibold text-white group-hover:text-cyan-100">{nextArticle.title}</span><span className="mt-1 block text-sm leading-6 text-slate-400">{nextArticle.summary}</span></span><ArrowRight aria-hidden="true" className="h-5 w-5 shrink-0 text-slate-500 group-hover:text-cyan-100" /></Link></aside> : null}
    </>
  );
}

function HelpNotFound() {
  return <><PageHeader eyebrow="找不到页面" summary="链接可能已过期或地址有误。你可以从左侧目录重新选择，或返回帮助首页搜索。" title="这篇帮助不存在" /><Link className="mt-7 inline-flex min-h-11 items-center gap-2 rounded-lg bg-cyan-300 px-4 text-sm font-bold text-[#04111f]" to="/help">打开帮助首页<ArrowRight aria-hidden="true" className="h-4 w-4" /></Link></>;
}

export default function HelpArticlePage() {
  const location = useLocation();
  const { moduleId, sectionId, slug, topicId } = useParams();
  const article = findHelpArticle(slug);
  const section = findHelpSection(sectionId);
  const module = findHelpModule(moduleId);
  const topic = findHelpModuleTopic(moduleId, topicId);
  const exists = Boolean(article || section || (module && (!topicId || topic)));
  const activeSectionId = module ? "modules" : section?.id ?? helpSections.find((item) => item.title === article?.category)?.id;

  useEffect(() => {
    const title = article?.title ?? topic?.title ?? module?.title ?? section?.title;
    document.title = title ? `${title} · ModelMirror 帮助` : "未找到帮助 · ModelMirror";
  }, [article, module, section, topic]);

  useEffect(() => {
    const encodedTargetId = location.hash.slice(1);
    if (encodedTargetId) {
      let targetId = encodedTargetId;
      try {
        targetId = decodeURIComponent(encodedTargetId);
      } catch {
        // Keep the original hash when it is not valid percent-encoded text.
      }
      document.getElementById(targetId)?.scrollIntoView();
      return;
    }
    window.scrollTo({ left: 0, top: 0 });
  }, [location.hash, location.pathname]);

  return (
    <PageContainer className="help-center-shell" hideSidebar maxWidthClassName="max-w-[1480px]" showSystemCapabilityBar={false}>
      <details className="mb-5 rounded-xl border border-white/10 bg-[#071a2b]/92 p-4 xl:hidden"><summary className="flex min-h-11 cursor-pointer list-none items-center gap-2 text-sm font-bold text-white"><Menu aria-hidden="true" className="h-5 w-5 text-cyan-300" />查看帮助目录<ChevronDown aria-hidden="true" className="ml-auto h-4 w-4 text-slate-500" /></summary><div className="mt-4 border-t border-white/10 pt-4"><HelpDirectory activeModuleId={moduleId} activeSectionId={activeSectionId} activeTopicId={topicId} /></div></details>
      <div className="grid min-w-0 gap-8 xl:grid-cols-[300px_minmax(0,1fr)]">
        <aside className="hidden xl:block"><div className="sticky top-24 max-h-[calc(100vh-7rem)] overflow-y-auto rounded-xl border border-white/10 bg-[#061522]/94 p-5"><HelpDirectory activeModuleId={moduleId} activeSectionId={activeSectionId} activeTopicId={topicId} /></div></aside>
        <main className="min-w-0 rounded-xl border border-white/[0.08] bg-[#061522]/54 px-5 py-7 sm:px-8 lg:px-12 lg:py-10">
          <nav aria-label="面包屑" className="mb-6 text-sm text-slate-500"><ol className="flex flex-wrap items-center gap-2"><li><Link className="hover:text-cyan-100" to="/help">帮助中心</Link></li><li aria-hidden="true">/</li><li>{module ? "按模块浏览" : section?.title ?? article?.category ?? "未知页面"}</li>{module ? <><li aria-hidden="true">/</li><li className="text-slate-300">{module.title}</li></> : null}</ol></nav>
          {!exists ? <HelpNotFound /> : article ? <ArticlePage article={article} /> : section ? <SectionPage section={section} /> : module ? <ModulePage module={module} topic={topic} /> : null}
        </main>
      </div>
    </PageContainer>
  );
}
