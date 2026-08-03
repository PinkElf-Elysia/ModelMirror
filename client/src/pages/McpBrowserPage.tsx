import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import McpServerCard, {
  type McpSessionSummary,
} from "../components/McpServerCard";
import PageContainer from "../components/PageContainer";
import {
  mcpCatalogSources,
  mcpCategories,
  mcpProjects,
  type McpCategory,
  type McpCompatibility,
} from "../data/mcpProjects";

interface RegistryTool {
  name: string;
  description?: string | null;
  input_schema: Record<string, unknown>;
  server_id: string;
  session_id: string;
  registered_at: number;
}

type CatalogCategory = "全部" | McpCategory;
type CompatibilityFilter = "all" | McpCompatibility;

const INITIAL_VISIBLE_COUNT = 18;

function commandKey(command?: string[]) {
  return command?.join("\u0000") ?? "";
}

export default function McpBrowserPage() {
  const [sessions, setSessions] = useState<McpSessionSummary[]>([]);
  const [registryTools, setRegistryTools] = useState<RegistryTool[]>([]);
  const [activeView, setActiveView] = useState<"servers" | "registry">("servers");
  const [query, setQuery] = useState("");
  const [selectedCategory, setSelectedCategory] =
    useState<CatalogCategory>("全部");
  const [compatibilityFilter, setCompatibilityFilter] =
    useState<CompatibilityFilter>("all");
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE_COUNT);
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

  const connectableCount = mcpProjects.filter(
    (project) => project.compatibility === "local-stdio",
  ).length;
  const plannedCount = mcpProjects.length - connectableCount;
  const categoryCounts = useMemo(() => {
    const counts = new Map<McpCategory, number>();
    for (const category of mcpCategories) counts.set(category, 0);
    for (const project of mcpProjects) {
      counts.set(project.category, (counts.get(project.category) ?? 0) + 1);
    }
    return counts;
  }, []);
  const filteredProjects = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase("zh-CN");
    return mcpProjects.filter((project) => {
      if (selectedCategory !== "全部" && project.category !== selectedCategory) {
        return false;
      }
      if (
        compatibilityFilter !== "all" &&
        project.compatibility !== compatibilityFilter
      ) {
        return false;
      }
      if (!normalizedQuery) return true;
      const searchableText = [
        project.name,
        project.repoName,
        project.category,
        project.description,
        project.readmeSummary,
        ...project.tags,
        ...project.requirements,
      ]
        .join(" ")
        .toLocaleLowerCase("zh-CN");
      return searchableText.includes(normalizedQuery);
    });
  }, [compatibilityFilter, query, selectedCategory]);
  const visibleProjects = useMemo(
    () => filteredProjects.slice(0, visibleCount),
    [filteredProjects, visibleCount],
  );

  useEffect(() => {
    setVisibleCount(INITIAL_VISIBLE_COUNT);
  }, [compatibilityFilter, query, selectedCategory]);
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
            汇集两个社区清单，并按当前安全边界区分“本地可连”和“已收录、待适配”。
          </p>
          <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">已上架工具</p>
            <p className="mt-1 text-sm font-semibold text-hire-100">
              {mcpProjects.length} 个
            </p>
          </div>
          <div className="mt-3 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">覆盖分类</p>
            <p className="mt-1 text-sm font-semibold text-brand-100">
              {mcpCategories.length} 类
            </p>
          </div>
          <div className="mt-3 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">本地 stdio 可连</p>
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
        <div className="absolute left-0 top-0 h-px w-full bg-[linear-gradient(90deg,transparent,rgba(251,146,60,0.82),rgba(253,186,116,0.72),transparent)]" />
        <div className="relative grid min-w-0 gap-8 lg:grid-cols-[minmax(0,1fr)_360px] lg:items-end">
          <div>
            <p className="text-sm font-semibold text-hire-200">
              中文 MCP 工具目录
            </p>
            <h1 className="mt-3 max-w-4xl text-4xl font-semibold tracking-normal text-white sm:text-6xl">
              MCP 工具采购
            </h1>
            <p className="mt-5 max-w-2xl text-base leading-7 text-slate-300">
              从 {mcpProjects.length} 个中文化条目中按场景与适配状态筛选。当前只开放无需 OAuth、Token、额外运行时或桌面宿主的本地 stdio Server。
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
                <p className="text-lg font-semibold text-hire-100">
                  {mcpCategories.length}
                </p>
                <p className="mt-1 truncate text-slate-400">工具分类</p>
              </div>
              <div className="rounded-lg bg-white/[0.055] px-2 py-3">
                <p className="text-lg font-semibold text-brand-100">
                  {plannedCount}
                </p>
                <p className="mt-1 truncate text-slate-400">待适配</p>
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

        {activeView === "servers" ? (
          <div className="mb-5 space-y-4">
            <div className="rounded-lg border border-brand-300/20 bg-brand-300/[0.07] p-4 text-sm">
              <p className="max-w-4xl leading-6 text-slate-300">
                条目整理自{" "}
                {mcpCatalogSources.map((source, index) => (
                  <span key={source.id}>
                    {index > 0 ? " 与 " : null}
                    <a
                      className="font-semibold text-brand-100 underline-offset-4 hover:underline"
                      href={source.url}
                      rel="noreferrer"
                      target="_blank"
                    >
                      {source.name}
                    </a>
                  </span>
                ))}
                ，并按模镜当前 stdio 边界重新分类、翻译和核验。
              </p>
              <p className="mt-2 text-xs leading-5 text-slate-400">
                核验日期 2026-08-02 · MIT 清单来源 · 不提供 OAuth、Token、外部运行时、桌面宿主或外站认证入口
              </p>
            </div>

            <div className="rounded-lg border border-amber-300/25 bg-amber-300/[0.08] p-4 text-sm leading-6 text-amber-50">
              “待适配”不代表项目不可用，而是当前模镜不会代管其凭证、账号授权、远程连接或桌面运行环境。此类条目仅展示中文用途与接入条件，按钮保持不可连接状态。
            </div>

            <div className="rounded-lg border border-white/10 bg-white/[0.035] p-4">
              <label className="block text-sm font-semibold text-slate-200" htmlFor="mcp-search">
                搜索工具
              </label>
              <div className="mt-2 flex flex-col gap-2 sm:flex-row">
                <input
                  className="min-w-0 flex-1 rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2.5 text-sm text-white outline-none placeholder:text-slate-400 focus:border-brand-300/60"
                  id="mcp-search"
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="搜索名称、仓库、用途或标签"
                  type="search"
                  value={query}
                />
                {query || selectedCategory !== "全部" || compatibilityFilter !== "all" ? (
                  <button
                    className="rounded-lg border border-white/10 bg-white/[0.055] px-4 py-2.5 text-sm font-semibold text-slate-200 transition hover:border-brand-300/35 hover:text-brand-100"
                    onClick={() => {
                      setQuery("");
                      setSelectedCategory("全部");
                      setCompatibilityFilter("all");
                    }}
                    type="button"
                  >
                    清除筛选
                  </button>
                ) : null}
              </div>
              <div className="mt-4 border-t border-white/10 pt-4">
                <p className="text-xs font-semibold text-slate-300">按当前适配状态</p>
                <div
                  aria-label="按 MCP 适配状态筛选"
                  className="mt-2 flex flex-wrap gap-2"
                  role="group"
                >
                  {([
                    ["all", "全部状态", mcpProjects.length],
                    ["local-stdio", "本地 stdio 可连", connectableCount],
                    ["planned", "已收录、待适配", plannedCount],
                  ] as const).map(([value, label, count]) => {
                    const isSelected = compatibilityFilter === value;
                    return (
                      <button
                        aria-pressed={isSelected}
                        className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
                          isSelected
                            ? value === "planned"
                              ? "border-amber-300/60 bg-amber-300 text-ink-950"
                              : "border-emerald-300/60 bg-emerald-300 text-ink-950"
                            : "border-white/10 bg-white/[0.045] text-slate-300 hover:border-brand-300/35 hover:text-brand-100"
                        }`}
                        key={value}
                        onClick={() => setCompatibilityFilter(value)}
                        type="button"
                      >
                        {label} · {count}
                      </button>
                    );
                  })}
                </div>
              </div>
              <div
                aria-label="按 MCP 场景筛选"
                className="mt-3 flex flex-wrap gap-2"
                role="group"
              >
                {(["全部", ...mcpCategories] as const).map((category) => {
                  const isSelected = selectedCategory === category;
                  const count =
                    category === "全部"
                      ? mcpProjects.length
                      : (categoryCounts.get(category) ?? 0);
                  return (
                    <button
                      aria-pressed={isSelected}
                      className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
                        isSelected
                          ? "border-hire-300/60 bg-hire-300 text-ink-950"
                          : "border-white/10 bg-white/[0.045] text-slate-300 hover:border-hire-300/35 hover:text-hire-100"
                      }`}
                      key={category}
                      onClick={() => setSelectedCategory(category)}
                      type="button"
                    >
                      {category} · {count}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        ) : null}

        <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-white">
              {activeView === "servers" ? "已上架工具箱" : "全局工具注册表"}
            </h2>
            <p className="mt-1 text-sm text-slate-400">
              {activeView === "servers"
                ? "本地可连项由后端以 stdio 启动；待适配项只展示中文配置边界和使用场景。"
                : "这里聚合所有已连接 MCP Server 的工具；重名工具按首次出现保留。"}
            </p>
          </div>
          <span className="w-fit rounded-full border border-emerald-300/25 bg-emerald-300/10 px-3 py-1.5 text-xs font-semibold text-emerald-100">
            {activeView === "servers"
              ? `显示 ${visibleProjects.length} / 匹配 ${filteredProjects.length}`
              : `${registryTools.length} 个已发现工具`}
          </span>
        </div>

        {activeView === "servers" ? (
          filteredProjects.length > 0 ? (
            <>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-3">
                {visibleProjects.map((project) => (
                  <McpServerCard
                    key={project.id}
                    onConnectionChange={() => void refreshRuntime()}
                    project={project}
                    restoredSession={sessionsByCommand.get(commandKey(project.command))}
                  />
                ))}
              </div>
              {visibleProjects.length < filteredProjects.length ? (
                <div className="mt-6 flex justify-center">
                  <button
                    className="rounded-lg border border-brand-300/30 bg-brand-300/10 px-5 py-2.5 text-sm font-semibold text-brand-100 transition hover:bg-brand-300/15 focus:outline-none focus:ring-2 focus:ring-brand-300/50"
                    onClick={() =>
                      setVisibleCount((count) => count + INITIAL_VISIBLE_COUNT)
                    }
                    type="button"
                  >
                    加载更多（剩余 {filteredProjects.length - visibleProjects.length} 个）
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            <div className="rounded-lg border border-white/10 bg-white/[0.035] p-8 text-center">
              <h3 className="text-lg font-semibold text-white">没有匹配的 MCP Server</h3>
              <p className="mx-auto mt-2 max-w-xl text-sm leading-6 text-slate-400">
                尝试缩短关键词、切换分类，或清除当前筛选条件查看完整目录。
              </p>
              <button
                className="mt-4 rounded-full bg-brand-300 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-brand-200"
                onClick={() => {
                  setQuery("");
                  setSelectedCategory("全部");
                  setCompatibilityFilter("all");
                }}
                type="button"
              >
                查看全部工具
              </button>
            </div>
          )
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
    </PageContainer>
  );
}
