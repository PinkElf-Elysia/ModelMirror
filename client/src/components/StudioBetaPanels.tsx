import { Link } from "react-router-dom";
import { useStudioPanelUrls } from "../hooks/useStudioPanelUrls";

export default function StudioBetaPanels() {
  const { scienceConsoleUrl } = useStudioPanelUrls();
  const card = "flex flex-col rounded-xl border border-white/10 bg-white/[0.025] p-5 sm:p-6";
  return (
    <section aria-label="Science 与 RPG" className="mb-6 grid gap-4 md:grid-cols-2">
      <article className={card}>
        <div className="flex items-center gap-3"><h2 className="text-lg font-semibold text-white">Science</h2><span className="rounded-full border border-amber-300/25 bg-amber-300/10 px-2 py-0.5 text-xs font-semibold text-amber-200">Beta</span></div>
        <p className="mt-3 text-sm leading-6 text-slate-400">进入 Research Console，管理文献研究项目、复核来源并导出成果。研究结论仍需人工审阅。</p>
        <div className="mt-auto flex flex-wrap items-center gap-4 pt-5">
          <a href={scienceConsoleUrl} target="_blank" rel="noopener noreferrer" aria-label="打开 Science 控制面板（新标签页）" className="inline-flex min-h-11 items-center rounded-lg border border-white/15 px-4 text-sm font-semibold text-white transition hover:border-brand-200/60 hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-200">打开控制面板 ↗</a>
          <Link to="/help/modules/experimental/science" className="py-3 text-sm text-slate-400 hover:text-white">入口说明</Link>
        </div>
      </article>
      <Link to="/rpg" className={`${card} transition-colors hover:border-white/25 hover:bg-white/[0.04] focus-visible:outline focus-visible:outline-2`}>
        <h2 className="text-lg font-semibold text-white">RPG · 角色与世界</h2>
        <p className="mt-3 text-sm leading-6 text-slate-400">地球 OL 人生模拟器与行间多世界 RPG，创建角色、进入故事、恢复历史。</p>
        <span className="mt-auto inline-flex min-h-11 items-center pt-5 text-sm font-semibold text-zinc-300">进入 RPG →</span>
      </Link>
    </section>
  );
}
