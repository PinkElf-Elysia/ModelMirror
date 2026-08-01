import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import McpServerCard, {
  type McpSessionSummary,
} from "../components/McpServerCard";
import PageContainer from "../components/PageContainer";
import { mcpProjects } from "../data/mcpProjects";

interface RegistryTool {
  name: string;
  description?: string | null;
  input_schema: Record<string, unknown>;
  server_id: string;
  session_id: string;
  registered_at: number;
}

const nextMcpShelves = [
  "Markitdown MCP",
  "Chrome DevTools MCP",
  "Notion MCP Server",
  "DBHub",
];

function formatStars(stars: number) {
  return `${(stars / 1000).toFixed(1)}k`;
}

function commandKey(command?: string[]) {
  return command?.join("\u0000") ?? "";
}

export default function McpBrowserPage() {
  const [sessions, setSessions] = useState<McpSessionSummary[]>([]);
  const [registryTools, setRegistryTools] = useState<RegistryTool[]>([]);
  const [activeView, setActiveView] = useState<"servers" | "registry">("servers");
  const [isLoadingRuntime, setIsLoadingRuntime] = useState(false);
  const [runtimeError, setRuntimeError] = useState("");

  useEffect(() => {
    document.title = "模镜 - MCP 工具采购";
  }, []);

  const refreshRuntime = useCallback(async () => {
    setIsLoadingRuntime(true);
    setRuntimeError("");
    try {
      const [sessionsResponse, registryResponse] = await Promise.all([
        fetch("/api/mcp/sessions"),
        fetch("/api/registry/tools"),
      ]);
      if (!sessionsResponse.ok) throw new Error("无法获取 MCP 会话列表");
      if (!registryResponse.ok) throw new Error("无法获取全局工具注册表");
      const sessionsData = (await sessionsResponse.json()) as {
        sessions: McpSessionSummary[];
      };
      const registryData = (await registryResponse.json()) as {
        tools: RegistryTool[];
      };
      setSessions(sessionsData.sessions);
      setRegistryTools(registryData.tools);
    } catch (exc) {
      setRuntimeError(
        exc instanceof Error ? exc.message : "MCP 运行态信息加载失败",
      );
    } finally {
      setIsLoadingRuntime(false);
    }
  }, []);

  useEffect(() => {
    void refreshRuntime();
  }, [refreshRuntime]);

  const totalStars = mcpProjects.reduce((sum, project) => sum + project.stars, 0);
  const connectableCount = mcpProjects.filter((project) => project.command?.length).length;
  const sessionsByCommand = useMemo(() => {
    const map = new Map<string, McpSessionSummary>();
    for (const session of sessions) {
      const key = commandKey(session.server_command);
      if (!map.has(key)) map.set(key, session);
    }
    return map;
  }, [sessions]);

  return (
    <PageContainer
      activeResource="mcps"
      sidebar={
        <div>
          <p className="text-sm font-semibold text-white">工具采购清单</p>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            首批上架浏览器自动化、最新文档和 GitHub 协作三类 MCP 工具。
          </p>
          <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">已上架工具</p>
            <p className="mt-1 text-sm font-semibold text-hire-100">
              {mcpProjects.length} 个
            </p>
          </div>
          <div className="mt-3 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">GitHub 热度</p>
            <p className="mt-1 text-sm font-semibold text-brand-100">
              {formatStars(totalStars)} stars
            </p>
          </div>
          <div className="mt-3 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">可原生连接</p>
            <p className="mt-1 text-sm font-semibold text-emerald-100">
              {connectableCount} 个
            </p>
          </div>
          <button
            className="mt-4 w-full rounded-full border border-brand-300/25 bg-brand-300/10 px-4 py-2 text-sm font-semibold text-brand-100 transition hover:bg-brand-300/15"
            onClick={() => void refreshRuntime()}
            type="button"
          >
            刷新连接状态
          </button>
          <Link
            className="mt-2 block w-full rounded-full border border-cyan-300/25 bg-cyan-300/10 px-4 py-2 text-center text-sm font-semibold text-cyan-100 transition hover:bg-cyan-300/15"
            to="/toolsets?tab=mcp"
          >
            管理 Toolset
          </Link>
        </div>
      }
    >
      <header className="relative overflow-hidden border-y border-hire-300/20 py-8 sm:py-10 lg:py-12">
        <div className="absolute inset-x-6 top-0 h-16 rounded-b-[50%] border-x border-b border-hire-300/30 bg-[linear-gradient(180deg,rgba(251,146,60,0.18),transparent)]" />
        <div className="absolute left-0 top-0 h-px w-full animate-pulse-line bg-[linear-gradient(90deg,transparent,rgba(251,146,60,0.82),rgba(253,186,116,0.72),transparent)]" />
        <div className="relative grid min-w-0 gap-8 lg:grid-cols-[minmax(0,1fr)_360px] lg:items-end">
          <div>
            <p className="text-sm font-semibold text-hire-200">
              万能工具招领处开张
            </p>
            <h1 className="mt-3 max-w-4xl text-4xl font-semibold tracking-normal text-white sm:text-6xl">
              MCP 工具采购
            </h1>
            <p className="mt-5 max-w-2xl text-base leading-7 text-slate-300">
              这里陈列可接入 AI 工作流的 MCP 工具箱。先上架最常用的浏览器、文档和 GitHub 协作工具，后续继续补货。
            </p>
          </div>

          <div className="surface-card rounded-lg p-4">
            <div className="flex items-center justify-between border-b border-white/10 pb-4">
              <span className="text-sm text-slate-400">采购台状态</span>
              <span className="text-2xl font-semibold text-white">
                {mcpProjects.length}
              </span>
            </div>
            <div className="mt-4 grid grid-cols-3 gap-2 text-center text-xs">
              <div className="rounded-lg bg-white/[0.055] px-2 py-3">
                <p className="text-lg font-semibold text-hire-100">3</p>
                <p className="mt-1 truncate text-slate-400">已验货</p>
              </div>
              <div className="rounded-lg bg-white/[0.055] px-2 py-3">
                <p className="text-lg font-semibold text-brand-100">2</p>
                <p className="mt-1 truncate text-slate-400">安装方式</p>
              </div>
              <div className="rounded-lg bg-white/[0.055] px-2 py-3">
                <p className="text-lg font-semibold text-emerald-100">
                  {connectableCount}
                </p>
                <p className="mt-1 truncate text-slate-400">可连接</p>
              </div>
            </div>
          </div>
        </div>
      </header>

      <section className="mt-8">
        <div className="mb-5 grid gap-3 md:grid-cols-3">
          <div className="rounded-lg border border-white/10 bg-white/[0.045] p-4">
            <p className="text-xs text-slate-400">已连接 Server</p>
            <p className="mt-2 text-2xl font-semibold text-white">
              {sessions.length}
            </p>
          </div>
          <div className="rounded-lg border border-white/10 bg-white/[0.045] p-4">
            <p className="text-xs text-slate-400">全局工具数</p>
            <p className="mt-2 text-2xl font-semibold text-brand-100">
              {registryTools.length}
            </p>
          </div>
          <div className="rounded-lg border border-white/10 bg-white/[0.045] p-4">
            <p className="text-xs text-slate-400">运行态</p>
            <p className="mt-2 text-sm font-semibold text-emerald-100">
              {isLoadingRuntime ? "同步中..." : "已同步"}
            </p>
          </div>
        </div>

        {runtimeError ? (
          <div className="mb-5 rounded-lg border border-rose-300/25 bg-rose-300/10 p-4 text-sm text-rose-100">
            {runtimeError}
          </div>
        ) : null}

        <div className="mb-5 flex flex-wrap gap-2">
          <button
            className={`rounded-full px-4 py-2 text-sm font-semibold transition ${
              activeView === "servers"
                ? "bg-hire-300 text-ink-950"
                : "border border-white/10 bg-white/[0.055] text-slate-200 hover:border-hire-300/30"
            }`}
            onClick={() => setActiveView("servers")}
            type="button"
          >
            工具货架
          </button>
          <button
            className={`rounded-full px-4 py-2 text-sm font-semibold transition ${
              activeView === "registry"
                ? "bg-brand-300 text-ink-950"
                : "border border-white/10 bg-white/[0.055] text-slate-200 hover:border-brand-300/30"
            }`}
            onClick={() => {
              setActiveView("registry");
              void refreshRuntime();
            }}
            type="button"
          >
            全局工具注册表
          </button>
        </div>

        <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-white">
              {activeView === "servers" ? "已上架工具箱" : "全局工具注册表"}
            </h2>
            <p className="mt-1 text-sm text-slate-400">
              {activeView === "servers"
                ? "点击“连接”即可由后端以 stdio 启动 MCP Server；连接后可查看工具、填写参数并执行。"
                : "这里聚合所有已连接 MCP Server 的工具；重名工具按首次出现保留。"}
            </p>
          </div>
          <span className="w-fit rounded-full border border-emerald-300/25 bg-emerald-300/10 px-3 py-1.5 text-xs font-semibold text-emerald-100">
            {activeView === "servers"
              ? `${connectableCount} 个支持原生连接`
              : `${registryTools.length} 个已发现工具`}
          </span>
        </div>

        {activeView === "servers" ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {mcpProjects.map((project) => (
              <McpServerCard
                key={project.id}
                onConnectionChange={() => void refreshRuntime()}
                project={project}
                restoredSession={sessionsByCommand.get(commandKey(project.command))}
              />
            ))}
          </div>
        ) : (
          <div className="overflow-hidden rounded-lg border border-white/10 bg-white/[0.045]">
            {registryTools.length === 0 ? (
              <div className="p-6 text-sm leading-6 text-slate-400">
                当前还没有已注册工具。先回到“工具货架”连接一个 MCP Server。
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full divide-y divide-white/10 text-sm">
                  <thead className="bg-white/[0.04] text-left text-xs uppercase tracking-wide text-slate-400">
                    <tr>
                      <th className="px-4 py-3">工具名</th>
                      <th className="px-4 py-3">所属 Server</th>
                      <th className="px-4 py-3">Session</th>
                      <th className="px-4 py-3">描述</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/10">
                    {registryTools.map((tool) => (
                      <tr className="align-top text-slate-300" key={`${tool.session_id}-${tool.name}`}>
                        <td className="px-4 py-3 font-semibold text-white">
                          {tool.name}
                        </td>
                        <td className="px-4 py-3 text-brand-100">
                          {tool.server_id}
                        </td>
                        <td className="px-4 py-3 font-mono text-xs text-slate-500">
                          {tool.session_id.slice(0, 10)}
                        </td>
                        <td className="max-w-xl px-4 py-3 text-slate-400">
                          {tool.description ?? "暂无描述"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </section>

      <section className="mt-8 rounded-lg border border-white/10 bg-white/[0.045] p-5">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-white">下批货架预告</h2>
            <p className="mt-1 text-sm text-slate-400">
              这些工具正在验货，确认安装方式后再上架。
            </p>
          </div>
          <span className="w-fit rounded-full border border-hire-300/30 bg-hire-300/10 px-3 py-1.5 text-xs font-semibold text-hire-100">
            即将到货
          </span>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {nextMcpShelves.map((name) => (
            <div
              className="rounded-lg border border-white/10 bg-ink-950/50 p-4"
              key={name}
            >
              <p className="font-semibold text-white">{name}</p>
              <p className="mt-2 text-xs text-slate-400">货架编号待确认</p>
            </div>
          ))}
        </div>
      </section>
    </PageContainer>
  );
}
