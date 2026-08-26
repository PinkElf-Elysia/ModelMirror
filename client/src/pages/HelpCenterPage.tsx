import {
  Activity,
  ArrowLeft,
  ArrowRight,
  Bot,
  Boxes,
  ChevronRight,
  CircleHelp,
  Clock3,
  Compass,
  FlaskConical,
  GraduationCap,
  Image as ImageIcon,
  MessageSquareText,
  Pause,
  Play,
  Plug,
  Search,
  Settings2,
  ShieldCheck,
  Workflow,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import {
  findHelpArticle,
  findHelpSection,
  helpContentTypeLabels,
  helpModules,
  searchHelpContent,
} from "../content/help-center";

const moduleIcons: Record<string, LucideIcon> = {
  models: Boxes,
  agents: Bot,
  mcps: Plug,
  skills: GraduationCap,
  prompts: MessageSquareText,
  runtime: Activity,
  workspace: Settings2,
  experimental: FlaskConical,
};

const carouselSlides = [
  {
    eyebrow: "推荐顺序 1 / 3",
    title: "找到能看图片的模型",
    summary: "筛选支持图片输入和图片识别的模型，进入聊天后找到图片选择入口。",
    meta: "约 4 分钟 · 不发送 · 不计费",
    to: "/help/start-with-a-model",
    action: "开始入门教程",
    icon: ImageIcon,
  },
  {
    eyebrow: "推荐顺序 2 / 3",
    title: "模型、Agent 还是 Workflow？",
    summary: "按一次任务、重复角色和固定多步骤，选对开始位置。",
    meta: "约 3 分钟 · 先判断再操作",
    to: "/help/choose-model-agent-workflow",
    action: "比较三种入口",
    icon: Compass,
  },
  {
    eyebrow: "推荐顺序 3 / 3",
    title: "使用前检查费用与数据",
    summary: "发送或上传前，检查当前状态、价格和资料是否允许外发。",
    meta: "约 4 分钟 · 停在发送前",
    to: "/help/check-availability-cost-data",
    action: "查看检查清单",
    icon: ShieldCheck,
  },
];

function IndexPanel({ sectionId }: { sectionId: "troubleshooting" | "safety" }) {
  const section = findHelpSection(sectionId)!;
  const Icon = sectionId === "troubleshooting" ? CircleHelp : ShieldCheck;
  return (
    <section className="rounded-xl border border-white/10 bg-[#071a2b]/88 p-5 sm:p-6">
      <Link className="group flex items-center gap-3" to={section.path}>
        <span className="flex h-11 w-11 items-center justify-center rounded-full border border-cyan-300/20 bg-cyan-300/[0.06] text-cyan-200">
          <Icon aria-hidden="true" className="h-6 w-6" />
        </span>
        <h2 className="text-xl font-bold text-white group-hover:text-cyan-100">{section.title}</h2>
        <ChevronRight aria-hidden="true" className="ml-auto h-5 w-5 text-slate-500 group-hover:text-cyan-200" />
      </Link>
      <div className="mt-4 divide-y divide-white/[0.08] border-t border-white/[0.08]">
        {section.items.map((item) => (
          <Link className="group flex min-h-11 items-center gap-3 py-3 text-sm text-slate-300 hover:text-cyan-100" key={item.id} to={item.to}>
            <span className="min-w-0 flex-1">{item.title}</span>
            <ChevronRight aria-hidden="true" className="h-4 w-4 shrink-0 text-slate-600 group-hover:text-cyan-200" />
          </Link>
        ))}
      </div>
    </section>
  );
}

export default function HelpCenterPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get("q") ?? "";
  const results = useMemo(() => searchHelpContent(query).slice(0, 16), [query]);
  const [activeSlide, setActiveSlide] = useState(0);
  const [paused, setPaused] = useState(false);
  const [interacting, setInteracting] = useState(false);
  const [cycleKey, setCycleKey] = useState(0);

  useEffect(() => { document.title = "帮助中心 · ModelMirror"; }, []);

  useEffect(() => {
    if (paused || interacting) return;
    const timer = window.setInterval(() => setActiveSlide((current) => (current + 1) % carouselSlides.length), 3000);
    return () => window.clearInterval(timer);
  }, [paused, interacting, cycleKey]);

  const updateQuery = (value: string) => {
    const next = new URLSearchParams(searchParams);
    if (value.trim()) next.set("q", value);
    else next.delete("q");
    setSearchParams(next, { replace: true });
  };

  const selectSlide = (index: number) => {
    setActiveSlide((index + carouselSlides.length) % carouselSlides.length);
    setCycleKey((value) => value + 1);
  };

  const gettingStarted = findHelpSection("getting-started")!;
  const goals = findHelpSection("goals")!;
  const active = carouselSlides[activeSlide];
  const ActiveIcon = active.icon;

  return (
    <PageContainer className="help-center-shell" hideSidebar maxWidthClassName="max-w-[1480px]" showSystemCapabilityBar={false}>
      <div className="mx-auto max-w-[1360px] pb-4">
        <header className="border-b border-white/10 pb-8 pt-1 lg:pt-4">
          <div className="max-w-4xl">
            <p className="text-sm font-semibold tracking-wide text-cyan-200">帮助中心</p>
            <h1 className="mt-3 text-4xl font-bold tracking-tight text-white sm:text-5xl">你想完成什么？</h1>
            <p className="mt-3 max-w-3xl text-base leading-7 text-slate-300">模镜把模型、Agent、工具、Skill 和工作台放在同一处。搜索你要做的事，或从下方入口开始。</p>
            <form className="relative mt-6" role="search" onSubmit={(event) => event.preventDefault()}>
              <label className="sr-only" htmlFor="help-search">搜索帮助</label>
              <Search aria-hidden="true" className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-slate-500" />
              <input
                autoComplete="off"
                className="h-14 w-full rounded-xl border border-white/15 bg-[#06111f]/90 pl-12 pr-12 text-base text-white shadow-[0_16px_40px_rgba(0,0,0,0.18)] placeholder:text-slate-500 focus:border-cyan-300/55 focus:outline-none"
                id="help-search"
                onChange={(event) => updateQuery(event.target.value)}
                placeholder="搜索帮助，例如：看图、费用、功能不可用"
                type="search"
                value={query}
              />
              {query ? (
                <button aria-label="清除帮助搜索" className="absolute right-2 top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-lg text-slate-400 hover:bg-white/[0.07] hover:text-white" onClick={() => updateQuery("")} type="button">
                  <X aria-hidden="true" className="h-4 w-4" />
                </button>
              ) : null}
            </form>
            <p className="mt-4 max-w-3xl text-sm leading-6 text-slate-400">
              模镜先帮你找到能力，再在聊天或工作台中完成任务，最后用 Runtime 查看运行和连接状态。
              <Link className="ml-2 inline-flex items-center gap-1 font-semibold text-cyan-200 hover:text-cyan-100" to="/help/modules-and-terms">
                先认识模镜的整体结构
                <ArrowRight aria-hidden="true" className="h-3.5 w-3.5" />
              </Link>
            </p>
          </div>
        </header>

        {query ? (
          <section aria-labelledby="help-search-results" className="py-9">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-cyan-200">搜索结果</p>
                <h2 className="mt-2 text-2xl font-bold text-white" id="help-search-results">与“{query}”有关的帮助</h2>
              </div>
              <p aria-live="polite" className="text-sm text-slate-400">找到 {results.length} 项</p>
            </div>
            {results.length ? (
              <div className="mt-5 grid gap-3 md:grid-cols-2">
                {results.map((entry) => (
                  <Link className="group rounded-xl border border-white/10 bg-[#071a2b]/76 p-4 hover:border-cyan-300/30 hover:bg-cyan-300/[0.045]" key={`${entry.kind}-${entry.id}`} to={entry.to}>
                    <div className="flex items-start gap-3">
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-semibold text-cyan-200">{entry.category}</p>
                        <h3 className="mt-1 text-base font-semibold text-white group-hover:text-cyan-100">{entry.title}</h3>
                        <p className="mt-1 text-sm leading-6 text-slate-400">{entry.summary}</p>
                      </div>
                      <ChevronRight aria-hidden="true" className="mt-1 h-4 w-4 shrink-0 text-slate-600 group-hover:text-cyan-200" />
                    </div>
                  </Link>
                ))}
              </div>
            ) : (
              <div className="mt-5 rounded-xl border border-white/10 bg-[#071a2b]/76 p-6">
                <h3 className="text-lg font-semibold text-white">没有找到相关帮助</h3>
                <p className="mt-2 text-sm leading-6 text-slate-400">换用任务词，例如“图片”“费用”或“不可用”，也可以清除搜索浏览全部入口。</p>
                <button className="mt-4 inline-flex min-h-11 items-center rounded-lg border border-cyan-300/30 bg-cyan-300/10 px-4 text-sm font-semibold text-cyan-100" onClick={() => updateQuery("")} type="button">清除搜索并浏览全部</button>
              </div>
            )}
          </section>
        ) : (
          <div className="space-y-9 py-9">
            <div className="grid gap-5 xl:grid-cols-[minmax(0,1.65fr)_minmax(360px,0.9fr)]">
              <section
                aria-label="第一次使用推荐路径"
                aria-roledescription="carousel"
                className="min-w-0 rounded-xl border border-white/10 bg-[#071a2b]/82 p-4 sm:p-5"
                onBlurCapture={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setInteracting(false); }}
                onFocusCapture={() => setInteracting(true)}
                onMouseEnter={() => setInteracting(true)}
                onMouseLeave={() => setInteracting(false)}
              >
                <div className="flex items-center justify-between gap-4">
                  <h2><Link className="text-xl font-bold text-white hover:text-cyan-100" to={gettingStarted.path}>第一次使用</Link></h2>
                  <span className="text-xs text-slate-500">自动切换</span>
                </div>
                <div className="mt-4 grid gap-3 lg:grid-cols-[minmax(0,1.65fr)_minmax(170px,0.65fr)_minmax(170px,0.65fr)]">
                  <article aria-live="polite" className="min-h-[250px] rounded-xl border border-cyan-300/35 bg-cyan-300/[0.055] p-5 sm:p-6">
                    <div className="flex h-full flex-col sm:flex-row sm:items-center sm:gap-4">
                      <div className="flex h-20 w-20 shrink-0 items-center justify-center rounded-2xl border border-cyan-300/25 bg-cyan-300/[0.08] text-cyan-200 sm:h-24 sm:w-24">
                        <ActiveIcon aria-hidden="true" className="h-10 w-10 sm:h-12 sm:w-12" />
                      </div>
                      <div className="mt-5 min-w-0 flex-1 sm:mt-0">
                        <p className="text-xs font-semibold text-cyan-200">{active.eyebrow}</p>
                        <h3 className="mt-2 break-words text-2xl font-bold text-white sm:break-keep">{active.title}</h3>
                        <p className="mt-2 text-sm leading-6 text-slate-300">{active.summary}</p>
                        <p className="mt-3 flex items-center gap-2 text-xs text-slate-400"><Clock3 aria-hidden="true" className="h-4 w-4" />{active.meta}</p>
                        <Link className="mt-5 inline-flex min-h-11 items-center gap-2 rounded-lg bg-cyan-300 px-4 text-sm font-bold text-[#04111f] hover:bg-cyan-200" to={active.to}>{active.action}<ArrowRight aria-hidden="true" className="h-4 w-4" /></Link>
                      </div>
                    </div>
                  </article>
                  {carouselSlides.map((slide, index) => {
                    if (index === activeSlide) return null;
                    const Icon = slide.icon;
                    return (
                      <button className="group hidden min-h-[250px] rounded-xl border border-white/10 bg-[#061522]/88 p-5 text-left hover:border-cyan-300/25 lg:flex lg:flex-col" key={slide.title} onClick={() => selectSlide(index)} type="button">
                        <Icon aria-hidden="true" className="h-9 w-9 text-cyan-300/75" />
                        <span className="mt-auto text-xs font-semibold text-slate-500">{slide.eyebrow}</span>
                        <span className="mt-2 text-lg font-semibold leading-7 text-slate-200 group-hover:text-cyan-100">{slide.title}</span>
                        <ArrowRight aria-hidden="true" className="mt-4 h-5 w-5 self-end text-slate-600 group-hover:text-cyan-200" />
                      </button>
                    );
                  })}
                </div>
                <div className="mt-4 flex items-center justify-center gap-4">
                  <button aria-label="上一个入门主题" className="flex h-10 w-10 items-center justify-center rounded-full border border-white/10 text-slate-300 hover:border-cyan-300/30 hover:text-cyan-100" onClick={() => selectSlide(activeSlide - 1)} type="button"><ArrowLeft aria-hidden="true" className="h-4 w-4" /></button>
                  <span className="min-w-10 text-center text-sm text-slate-300">{activeSlide + 1} / {carouselSlides.length}</span>
                  <div className="hidden gap-2 sm:flex" aria-hidden="true">{carouselSlides.map((slide, index) => <span className={`h-1.5 w-10 rounded-full ${index === activeSlide ? "bg-cyan-300" : "bg-white/10"}`} key={slide.title} />)}</div>
                  <button aria-label="下一个入门主题" className="flex h-10 w-10 items-center justify-center rounded-full border border-white/10 text-slate-300 hover:border-cyan-300/30 hover:text-cyan-100" onClick={() => selectSlide(activeSlide + 1)} type="button"><ArrowRight aria-hidden="true" className="h-4 w-4" /></button>
                  <button aria-label={paused ? "继续自动切换" : "暂停自动切换"} className="flex h-10 w-10 items-center justify-center rounded-full border border-white/10 text-slate-300 hover:border-cyan-300/30 hover:text-cyan-100" onClick={() => setPaused((value) => !value)} type="button">{paused ? <Play aria-hidden="true" className="h-4 w-4" /> : <Pause aria-hidden="true" className="h-4 w-4" />}</button>
                </div>
              </section>

              <section className="min-w-0 rounded-xl border border-white/10 bg-[#071a2b]/82 p-5 sm:p-6">
                <Link className="group flex items-center justify-between gap-3" to={goals.path}>
                  <span><h2 className="text-xl font-bold text-white group-hover:text-cyan-100">按目标找指南</h2><span className="mt-1 block text-sm leading-6 text-slate-400">先选任务类型，再决定从哪里开始。</span></span>
                  <ChevronRight aria-hidden="true" className="h-5 w-5 text-slate-600 group-hover:text-cyan-200" />
                </Link>
                <div className="mt-5 space-y-3">
                  {goals.items.slice(0, 3).map((item, index) => {
                    const GoalIcon = index === 0 ? MessageSquareText : index === 1 ? Bot : Workflow;
                    return (
                      <Link className="group flex min-h-[76px] items-center gap-4 rounded-xl border border-white/10 bg-[#061522]/82 p-4 hover:border-cyan-300/25" key={item.id} to={item.to}>
                        <GoalIcon aria-hidden="true" className="h-7 w-7 shrink-0 text-cyan-300/85" />
                        <span className="min-w-0 flex-1"><span className="block font-semibold text-slate-100 group-hover:text-cyan-100">{item.title}</span><span className="mt-1 block text-sm leading-5 text-slate-400">{item.summary}</span></span>
                        <ChevronRight aria-hidden="true" className="h-4 w-4 shrink-0 text-slate-600 group-hover:text-cyan-200" />
                      </Link>
                    );
                  })}
                </div>
                <Link className="mt-5 inline-flex min-h-11 items-center gap-2 text-sm font-semibold text-cyan-200 hover:text-cyan-100" to={goals.path}>查看全部目标<ArrowRight aria-hidden="true" className="h-4 w-4" /></Link>
              </section>
            </div>

            <section aria-labelledby="help-modules-heading">
              <div className="flex items-end justify-between gap-4">
                <div><h2 id="help-modules-heading"><Link className="text-2xl font-bold text-white hover:text-cyan-100" to="/help/sections/modules">按模块浏览</Link></h2><p className="mt-1 text-sm leading-6 text-slate-400">首页只展示常用二级入口；点击模块进入完整分级目录。</p></div>
              </div>
              <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
                {helpModules.map((module) => {
                  const Icon = moduleIcons[module.id] ?? Boxes;
                  const homeTopics = module.homeTopicIds.map((id) => module.topics.find((topic) => topic.id === id)!).filter(Boolean);
                  return (
                    <article className="min-w-0 border-l border-white/10 px-4 first:border-l-0 sm:first:border-l" key={module.id}>
                      <Link className="group flex min-h-11 items-center gap-2 font-semibold text-slate-100 hover:text-cyan-100" to={`/help/modules/${module.id}`}><Icon aria-hidden="true" className="h-5 w-5 shrink-0 text-cyan-300" /><span className="min-w-0 flex-1">{module.title}</span><ChevronRight aria-hidden="true" className="h-4 w-4 text-slate-600 group-hover:text-cyan-200" /></Link>
                      <div className="mt-2 space-y-1.5 border-t border-white/[0.08] pt-2">
                        {homeTopics.map((topic) => <Link className="flex min-h-8 items-center gap-2 text-sm leading-5 text-slate-400 hover:text-cyan-100" key={topic.id} to={`/help/modules/${module.id}/${topic.id}`}><span className="min-w-0 flex-1">{topic.title}</span>{topic.badge ? <span className="rounded border border-amber-300/25 bg-amber-300/[0.07] px-1.5 py-0.5 text-[10px] text-amber-200">{topic.badge}</span> : null}</Link>)}
                      </div>
                    </article>
                  );
                })}
              </div>
            </section>

            <div className="grid gap-5 lg:grid-cols-2"><IndexPanel sectionId="troubleshooting" /><IndexPanel sectionId="safety" /></div>
          </div>
        )}
      </div>
    </PageContainer>
  );
}
