import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

type PanelUrls = { scienceConsoleUrl: string; matrixOasisConsoleUrl: string };
const defaults: PanelUrls = {
  scienceConsoleUrl: "http://127.0.0.1:8900/",
  matrixOasisConsoleUrl: "http://127.0.0.1:43110/",
};

export function resolvePanelUrl(value: unknown, fallback: string): string {
  if (typeof value !== "string" || !value.trim()) return fallback;
  try {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) return fallback;
    return url.toString();
  } catch { return fallback; }
}

export default function StudioBetaPanels() {
  const [urls, setUrls] = useState(defaults);
  useEffect(() => {
    const controller = new AbortController();
    void fetch("/runtime-config.json", { cache: "no-store", signal: controller.signal })
      .then(async (response) => response.ok ? await response.json() as Partial<PanelUrls> : null)
      .then((config) => {
        if (controller.signal.aborted || !config) return;
        setUrls({ scienceConsoleUrl: resolvePanelUrl(config.scienceConsoleUrl, defaults.scienceConsoleUrl),
          matrixOasisConsoleUrl: resolvePanelUrl(config.matrixOasisConsoleUrl, defaults.matrixOasisConsoleUrl) });
      }).catch(() => { /* Local previews retain documented loopback defaults. */ });
    return () => controller.abort();
  }, []);

  const panels = [
    { id: "science", title: "Science", subtitle: "文献研究与来源复核", description: "进入 Research Console，管理研究项目、复核来源并导出成果。研究结论仍需人工审阅。", url: urls.scienceConsoleUrl },
    { id: "matrix-oasis", title: "矩阵绿洲", subtitle: "Creator · 世界创作", description: "进入 Creator 控制面板，查看创作流程与运行证据。当前开放实验控制台，完整世界体验仍在开发。", url: urls.matrixOasisConsoleUrl },
  ];
  return (
    <section aria-label="Beta 控制面板" className="mb-6 grid gap-4 md:grid-cols-2">
      {panels.map((panel) => <article key={panel.id} className="flex flex-col rounded-xl border border-white/10 bg-white/[0.025] p-5 sm:p-6">
        <div className="flex items-center gap-3"><h2 className="text-lg font-semibold text-white">{panel.title}</h2><span className="rounded-full border border-amber-300/25 bg-amber-300/10 px-2 py-0.5 text-xs font-semibold text-amber-200">Beta</span></div>
        <p className="mt-3 text-sm font-medium text-slate-200">{panel.subtitle}</p>
        <p className="mt-2 text-sm leading-6 text-slate-400">{panel.description}</p>
        <p className="mt-3 text-xs leading-5 text-slate-500">独立面板将在新标签页打开；需先启动对应服务。无法连接时请查看入口说明。</p>
        <div className="mt-auto flex flex-wrap items-center gap-4 pt-5">
          <a href={panel.url} target="_blank" rel="noopener noreferrer" aria-label={`打开 ${panel.title} 控制面板（新标签页）`} className="inline-flex min-h-11 items-center rounded-lg border border-white/15 px-4 text-sm font-semibold text-white transition hover:border-brand-200/60 hover:bg-white/5 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-200">打开控制面板 ↗</a>
          <Link to={`/help/modules/experimental/${panel.id}`} className="py-3 text-sm text-slate-400 underline-offset-4 hover:text-white hover:underline">入口说明</Link>
          {panel.id === "matrix-oasis" && <Link to="/matrix-oasis" className="py-3 text-sm text-slate-400 underline-offset-4 hover:text-white hover:underline">查看预告</Link>}
        </div>
      </article>)}
    </section>
  );
}
