import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import ModelServiceConnections from "../components/settings/ModelServiceConnections";
import MarbleConnectionSettings from "../components/settings/MarbleConnectionSettings";
import ProviderAdminGate from "../components/settings/ProviderAdminGate";
import ProviderCatalogPanel from "../components/settings/ProviderCatalogPanel";
import ProviderControlPlaneOverview from "../components/settings/ProviderControlPlaneOverview";
import ProviderWorkloadControlSettings from "../components/settings/ProviderWorkloadControlSettings";
import SmartRoutingSettings from "../components/settings/SmartRoutingSettings";

type SettingsSection = "overview" | "providers" | "routing";
const SECTIONS: Array<{ id: SettingsSection; label: string; description: string }> = [
  { id: "overview", label: "总览", description: "目录与运行准备度" },
  { id: "providers", label: "Provider 与 Catalog", description: "连接、Inventory 与认证" },
  { id: "routing", label: "路由与实验", description: "Managed 路由、Native 门禁与实验" },
];

function trimTrailingSlash(value: string) { return value.replace(/\/+$/, ""); }

export function resolveNewApiConsoleUrl(value: string | undefined) {
  const configured = trimTrailingSlash(value?.trim() ?? "");
  if (!configured) return null;
  try {
    const url = new URL(configured);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash) return null;
    return url;
  } catch { return null; }
}

function NewApiExternalConsole({ consoleUrl }: { consoleUrl: URL | null }) {
  return (
    <section className="overflow-hidden rounded-lg border border-white/10 bg-ink-950/82 shadow-prism">
      <div className="flex flex-col gap-3 border-b border-white/10 bg-white/[0.035] px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-hire-100">newAPI Gateway</p>
          <h2 className="mt-2 text-2xl font-semibold text-white">外部 newAPI 管理入口</h2>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-400">ModelMirror 不嵌入或代理 newAPI 管理界面。通过客户端容器环境变量 <code className="mx-1 rounded bg-white/10 px-1 py-0.5">NEWAPI_WEB_URL</code> 配置；该状态不代表网关健康。</p>
        </div>
        {consoleUrl ? <a className="inline-flex items-center justify-center rounded-full border border-hire-300/30 bg-hire-300/10 px-4 py-2 text-sm font-semibold text-hire-100" href={consoleUrl.toString()} rel="noopener noreferrer" target="_blank">在新窗口管理</a> : null}
      </div>
      <div className="grid gap-4 px-5 py-5 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
        <div><p className="text-sm font-semibold text-white">{consoleUrl ? "外部管理入口已配置" : "外部管理入口未配置"}</p><p className="mt-1 text-sm leading-6 text-slate-400">{consoleUrl ? `管理主机：${consoleUrl.host}` : "未设置 NEWAPI_WEB_URL；请在 client 服务环境中配置后重启前端容器。ModelMirror 不会猜测或自动加载本地控制台。"}</p></div>
        <span className="rounded-full border border-white/10 bg-white/[0.045] px-3 py-1.5 text-xs text-slate-300">{consoleUrl ? "仅外链" : "未配置"}</span>
      </div>
    </section>
  );
}

export default function SystemSettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = searchParams.get("section");
  const section: SettingsSection = SECTIONS.some((item) => item.id === requested) ? requested as SettingsSection : "overview";
  const buildTimeConsoleUrl = useMemo(() => resolveNewApiConsoleUrl(import.meta.env.VITE_NEWAPI_WEB_URL), []);
  const [consoleUrl, setConsoleUrl] = useState<URL | null>(buildTimeConsoleUrl);

  useEffect(() => { document.title = "模镜 - 系统设置"; }, []);
  useEffect(() => {
    let active = true;
    void fetch("/runtime-config.json", { cache: "no-store" }).then(async (response) => response.ok ? await response.json() as { newApiWebUrl?: string } : null).then((config) => {
      if (active && config) setConsoleUrl(resolveNewApiConsoleUrl(config.newApiWebUrl) ?? buildTimeConsoleUrl);
    }).catch(() => undefined);
    return () => { active = false; };
  }, [buildTimeConsoleUrl]);

  return (
    <PageContainer activeResource="agents" maxWidthClassName="max-w-[1760px]">
      <header className="mb-6 overflow-hidden rounded-lg border border-hire-300/20 bg-[linear-gradient(135deg,rgba(67,20,7,0.74),rgba(6,9,22,0.92)_52%,rgba(8,51,68,0.48))] p-6 shadow-prism">
        <p className="text-sm font-semibold text-hire-100">系统设置</p>
        <h1 className="mt-3 text-3xl font-semibold text-white sm:text-4xl">Model Provider Control Plane</h1>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300">统一管理模型发现、运行准备度和路由实验；Catalog 统一不改变各模态数据面，也不代表默认 Provider 已切换。</p>
      </header>

      <section aria-label="Provider Control Plane" className="mb-8">
        <nav aria-label="Provider 设置分区" className="mb-5 grid gap-2 rounded-lg border border-white/10 bg-ink-950/82 p-2 md:grid-cols-3">
          {SECTIONS.map((item) => <button aria-current={section === item.id ? "page" : undefined} className={`rounded-lg px-4 py-3 text-left transition ${section === item.id ? "bg-cyan-300/12 text-cyan-100 ring-1 ring-cyan-300/25" : "text-slate-300 hover:bg-white/[0.045]"}`} key={item.id} onClick={() => setSearchParams({ section: item.id }, { replace: true })} type="button"><span className="block text-sm font-semibold">{item.label}</span><span className="mt-1 block text-xs text-slate-400">{item.description}</span></button>)}
        </nav>
        <ProviderAdminGate>
          {({ csrfToken }) => section === "overview" ? <ProviderControlPlaneOverview /> : section === "providers" ? <div><ModelServiceConnections csrfToken={csrfToken} /><ProviderCatalogPanel csrfToken={csrfToken} /><ProviderWorkloadControlSettings csrfToken={csrfToken} view="certifications" /><NewApiExternalConsole consoleUrl={consoleUrl} /></div> : <SmartRoutingSettings csrfToken={csrfToken} />}
        </ProviderAdminGate>
      </section>

      <section aria-labelledby="other-integrations-title">
        <div className="mb-3"><p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">Provider Control Plane 之外</p><h2 className="mt-1 text-xl font-semibold text-white" id="other-integrations-title">其他集成</h2><p className="mt-1 text-sm text-slate-400">以下设置不受 Provider 管理配对状态影响。</p></div>
        <MarbleConnectionSettings />
      </section>
    </PageContainer>
  );
}
