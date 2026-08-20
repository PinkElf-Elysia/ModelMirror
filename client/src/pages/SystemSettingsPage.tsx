import { useEffect, useMemo, useState } from "react";
import PageContainer from "../components/PageContainer";
import ModelServiceConnections from "../components/settings/ModelServiceConnections";
import MarbleConnectionSettings from "../components/settings/MarbleConnectionSettings";
import SmartRoutingSettings from "../components/settings/SmartRoutingSettings";
import ProviderAdminGate from "../components/settings/ProviderAdminGate";

function trimTrailingSlash(value: string) {
  return value.replace(/\/+$/, "");
}

export function resolveNewApiConsoleUrl(value: string | undefined) {
  const configured = trimTrailingSlash(value?.trim() ?? "");
  if (!configured) return null;
  try {
    const url = new URL(configured);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    ) {
      return null;
    }
    return url;
  } catch {
    return null;
  }
}

type RuntimeConfig = {
  newApiWebUrl?: string;
};

export default function SystemSettingsPage() {
  const buildTimeConsoleUrl = useMemo(
    () => resolveNewApiConsoleUrl(import.meta.env.VITE_NEWAPI_WEB_URL),
    [],
  );
  const [consoleUrl, setConsoleUrl] = useState<URL | null>(buildTimeConsoleUrl);

  useEffect(() => {
    document.title = "模镜 - 系统设置";
  }, []);

  useEffect(() => {
    let active = true;
    void fetch("/runtime-config.json", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) return null;
        return (await response.json()) as RuntimeConfig;
      })
      .then((config) => {
        if (!active || !config) return;
        setConsoleUrl(
          resolveNewApiConsoleUrl(config.newApiWebUrl) ?? buildTimeConsoleUrl,
        );
      })
      .catch(() => {
        // Vite development and static-only deployments keep the build-time fallback.
      });
    return () => {
      active = false;
    };
  }, [buildTimeConsoleUrl]);

  return (
    <PageContainer activeResource="agents" maxWidthClassName="max-w-[1760px]">
      <header className="mb-6 overflow-hidden rounded-lg border border-hire-300/20 bg-[linear-gradient(135deg,rgba(67,20,7,0.74),rgba(6,9,22,0.92)_52%,rgba(8,51,68,0.48))] p-6 shadow-prism">
        <p className="text-sm font-semibold text-hire-100">系统设置</p>
        <h1 className="mt-3 text-3xl font-semibold text-white sm:text-4xl">
          模型服务与智能调度
        </h1>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300">
          在这里完成模型服务连接、Marble 世界生成、智能调度和上下文优化。
          日常使用无需理解底层网关；外部数据面与 ModelMirror 控制面保持独立。
        </p>
      </header>

      <MarbleConnectionSettings />
      <ProviderAdminGate>
        {({ csrfToken }) => (
          <>
            <ModelServiceConnections csrfToken={csrfToken} />
            <SmartRoutingSettings csrfToken={csrfToken} />
          </>
        )}
      </ProviderAdminGate>

      <section className="overflow-hidden rounded-lg border border-white/10 bg-ink-950/82 shadow-prism">
        <div className="flex flex-col gap-3 border-b border-white/10 bg-white/[0.035] px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-hire-100">
              newAPI Gateway
            </p>
            <h2 className="mt-2 text-2xl font-semibold text-white">
              外部 newAPI 管理入口
            </h2>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-400">
              ModelMirror 不嵌入或代理 newAPI 管理界面。通过客户端容器环境变量
              <code className="mx-1 rounded bg-white/10 px-1 py-0.5">
                NEWAPI_WEB_URL
              </code>
              配置后只需重启前端容器，无需重新构建镜像；该状态不代表网关运行健康。
            </p>
          </div>
          {consoleUrl ? (
            <a
              className="inline-flex items-center justify-center rounded-full border border-hire-300/30 bg-hire-300/10 px-4 py-2 text-sm font-semibold text-hire-100 transition hover:bg-hire-300/20"
              href={consoleUrl.toString()}
              rel="noopener noreferrer"
              target="_blank"
            >
              在新窗口管理
            </a>
          ) : null}
        </div>

        <div className="grid gap-4 px-5 py-5 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
          <div>
            <p className="text-sm font-semibold text-white">
              {consoleUrl ? "外部管理入口已配置" : "外部管理入口未配置"}
            </p>
            <p className="mt-1 text-sm leading-6 text-slate-400">
              {consoleUrl
                ? `管理主机：${consoleUrl.host}`
                : "未设置 NEWAPI_WEB_URL；请在 client 服务环境中配置后重启前端容器。ModelMirror 不会猜测或自动加载本地控制台。"}
            </p>
          </div>
          <span className="rounded-full border border-white/10 bg-white/[0.045] px-3 py-1.5 text-xs text-slate-300">
            {consoleUrl ? "仅外链" : "未配置"}
          </span>
        </div>
      </section>
    </PageContainer>
  );
}
