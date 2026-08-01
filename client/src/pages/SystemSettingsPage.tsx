import { useEffect, useMemo, useState } from "react";
import PageContainer from "../components/PageContainer";
import ModelServiceConnections from "../components/settings/ModelServiceConnections";
import MarbleConnectionSettings from "../components/settings/MarbleConnectionSettings";
import SmartRoutingSettings from "../components/settings/SmartRoutingSettings";

const DEFAULT_NEWAPI_WEB_URL = "http://localhost:3000";

function trimTrailingSlash(value: string) {
  return value.replace(/\/+$/, "");
}

function buildNewApiFrameUrl() {
  const baseUrl = trimTrailingSlash(
    import.meta.env.VITE_NEWAPI_WEB_URL || DEFAULT_NEWAPI_WEB_URL,
  );
  const url = new URL(baseUrl);
  url.searchParams.set("embed", "true");
  url.searchParams.set("source", "modelmirror");
  return url.toString();
}

export default function SystemSettingsPage() {
  const [loaded, setLoaded] = useState(false);

  const frameSrc = useMemo(() => buildNewApiFrameUrl(), []);

  useEffect(() => {
    document.title = "模镜 - 系统设置";
  }, []);

  useEffect(() => {
    setLoaded(false);
  }, [frameSrc]);

  return (
    <PageContainer activeResource="agents" maxWidthClassName="max-w-[1760px]">
      <header className="mb-6 overflow-hidden rounded-lg border border-hire-300/20 bg-[linear-gradient(135deg,rgba(67,20,7,0.74),rgba(6,9,22,0.92)_52%,rgba(8,51,68,0.48))] p-6 shadow-prism">
        <p className="text-sm font-semibold text-hire-100">系统设置</p>
        <h1 className="mt-3 text-3xl font-semibold text-white sm:text-4xl">
          模型服务与智能调度
        </h1>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300">
          在这里完成模型服务连接、智能调度和上下文优化。日常使用无需理解底层网关；
          newAPI 控制台仍作为高级渠道管理入口保留。
        </p>
      </header>

      <MarbleConnectionSettings />
      <ModelServiceConnections />
      <SmartRoutingSettings />

      <section className="overflow-hidden rounded-lg border border-white/10 bg-ink-950/82 shadow-prism">
        <div className="flex flex-col gap-3 border-b border-white/10 bg-white/[0.035] px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-hire-100">
              newAPI Gateway
            </p>
            <h2 className="mt-2 text-2xl font-semibold text-white">
              newAPI 统一网关管理
            </h2>
            <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-400">
              当前页面加载本地 newAPI 控制台。若 iframe 长时间空白，请确认
              <code className="mx-1 rounded bg-white/10 px-1 py-0.5">
                modelmirror-new-api
              </code>
              容器已启动，或通过
              <code className="mx-1 rounded bg-white/10 px-1 py-0.5">
                VITE_NEWAPI_WEB_URL
              </code>
              指定外部控制台地址。
            </p>
          </div>
          <a
            className="inline-flex items-center justify-center rounded-full border border-hire-300/30 bg-hire-300/10 px-4 py-2 text-sm font-semibold text-hire-100 transition hover:bg-hire-300/20"
            href={frameSrc}
            rel="noreferrer"
            target="_blank"
          >
            新窗口打开
          </a>
        </div>

        <div className="relative h-[calc(100vh-300px)] min-h-[620px] bg-slate-950">
          {!loaded ? (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-slate-950">
              <div className="w-full max-w-md rounded-lg border border-white/10 bg-white/[0.045] p-5 text-center">
                <div className="mx-auto h-10 w-10 animate-pulse rounded-full border border-hire-300/35 bg-hire-300/15" />
                <p className="mt-4 text-sm font-semibold text-white">
                  正在连接 newAPI 控制台
                </p>
                <p className="mt-2 text-xs leading-5 text-slate-400">
                  默认地址为
                  <code className="mx-1 rounded bg-white/10 px-1 py-0.5">
                    {DEFAULT_NEWAPI_WEB_URL}
                  </code>
                  。如果本地服务未启动，可先通过 Docker Compose 拉起。
                </p>
              </div>
            </div>
          ) : null}
          <iframe
            className="h-full w-full border-0"
            onLoad={() => setLoaded(true)}
            sandbox="allow-downloads allow-forms allow-modals allow-popups allow-popups-to-escape-sandbox allow-same-origin allow-scripts"
            src={frameSrc}
            title="newAPI 统一网关管理"
          />
        </div>
      </section>
    </PageContainer>
  );
}
