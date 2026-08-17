import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Boxes,
  CheckCircle2,
  ChevronDown,
  Grid2X2,
  Plug,
  RefreshCw,
  Search,
} from "lucide-react";
import McpServerCard, {
  type McpSessionSummary,
} from "../components/McpServerCard";
import ModelWorkbenchSidebar from "../components/ModelWorkbenchSidebar";
import PageContainer from "../components/PageContainer";
import {
  mcpCategories,
  mcpProjects,
  type McpCategory,
} from "../data/mcpProjects";
import {
  type McpAvailability,
  type McpCatalogAdapterStatus,
} from "../data/mcpAdaptationPlan";

interface RegistryTool {
  name: string;
  description?: string | null;
  input_schema: Record<string, unknown>;
  server_id: string;
  session_id: string;
  registered_at: number;
}

type CatalogCategory = "全部类别" | McpCategory;

export function prioritizeReadyProjects<T>(
  projects: readonly T[],
  getAvailability: (project: T) => string,
  enabled: boolean,
): T[] {
  if (!enabled) return [...projects];
  return projects
    .map((project, index) => ({ project, index }))
    .sort((left, right) => {
      const leftReady = getAvailability(left.project) === "ready";
      const rightReady = getAvailability(right.project) === "ready";
      return Number(rightReady) - Number(leftReady) || left.index - right.index;
    })
    .map(({ project }) => project);
}
type AvailabilityFilter =
  | "all"
  | "ready"
  | "adapting"
  | "planned"
  | "blocked";

const INITIAL_VISIBLE_COUNT = 18;
const PRIMARY_CATEGORY_COUNT = 6;

function matchesAvailability(
  availability: McpAvailability,
  filter: AvailabilityFilter,
) {
  if (filter === "all") return true;
  return availability === filter;
}

export default function McpBrowserPage() {
  const [sessions, setSessions] = useState<McpSessionSummary[]>([]);
  const [registryTools, setRegistryTools] = useState<RegistryTool[]>([]);
  const [activeView, setActiveView] = useState<"servers" | "registry">("servers");
  const [query, setQuery] = useState("");
  const [selectedCategory, setSelectedCategory] =
    useState<CatalogCategory>("全部类别");
  const [availabilityFilter, setAvailabilityFilter] =
    useState<AvailabilityFilter>("all");
  const [categoriesExpanded, setCategoriesExpanded] = useState(false);
  const [adapterStatuses, setAdapterStatuses] = useState<
    McpCatalogAdapterStatus[]
  >([]);
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE_COUNT);
  const [isLoadingRuntime, setIsLoadingRuntime] = useState(false);
  const [runtimeError, setRuntimeError] = useState("");
  const [favorites, setFavorites] = useState<Set<string>>(() => {
    try {
      const raw = window.localStorage.getItem("modelmirror-mcp-favorites");
      const parsed = raw ? (JSON.parse(raw) as unknown) : null;
      return Array.isArray(parsed)
        ? new Set(parsed.filter((item): item is string => typeof item === "string"))
        : new Set<string>();
    } catch {
      return new Set<string>();
    }
  });

  useEffect(() => {
    document.title = "模镜 - MCP 工具采购";
  }, []);

  const refreshRuntime = useCallback(async () => {
    setIsLoadingRuntime(true);
    setRuntimeError("");
    try {
      const [sessionsResponse, registryResponse, adaptersResponse] = await Promise.all([
        fetch("/api/mcp/sessions"),
        fetch("/api/registry/tools"),
        fetch("/api/mcp/catalog/adapters"),
      ]);
      if (!sessionsResponse.ok) throw new Error("无法获取 MCP 会话列表");
      if (!registryResponse.ok) throw new Error("无法获取全局工具注册表");
      if (!adaptersResponse.ok) throw new Error("无法获取 MCP 适配状态");
      const sessionsData = (await sessionsResponse.json()) as {
        sessions: McpSessionSummary[];
      };
      const registryData = (await registryResponse.json()) as {
        tools: RegistryTool[];
      };
      const adaptersData = (await adaptersResponse.json()) as {
        adapters: McpCatalogAdapterStatus[];
      };
      setSessions(sessionsData.sessions);
      setRegistryTools(registryData.tools);
      setAdapterStatuses(adaptersData.adapters);
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

  const adapterByProject = useMemo(
    () => new Map(adapterStatuses.map((adapter) => [adapter.project_id, adapter])),
    [adapterStatuses],
  );
  const effectiveAvailability = useCallback(
    (projectId: string) =>
      adapterByProject.get(projectId)?.availability ??
      mcpProjects.find((project) => project.id === projectId)?.availability ??
      "blocked",
    [adapterByProject],
  );
  const readyCount = mcpProjects.filter(
    (project) => effectiveAvailability(project.id) === "ready",
  ).length;
  const plannedCount = mcpProjects.filter(
    (project) => effectiveAvailability(project.id) === "planned",
  ).length;
  const adaptingCount = mcpProjects.filter(
    (project) => effectiveAvailability(project.id) === "adapting",
  ).length;
  const blockedCount = mcpProjects.filter(
    (project) => effectiveAvailability(project.id) === "blocked",
  ).length;
  const categoryCounts = useMemo(() => {
    const counts = new Map<McpCategory, number>();
    for (const category of mcpCategories) counts.set(category, 0);
    for (const project of mcpProjects) {
      counts.set(project.category, (counts.get(project.category) ?? 0) + 1);
    }
    return counts;
  }, []);
  const rankedCategories = useMemo(
    () =>
      [...mcpCategories].sort((left, right) => {
        const countDelta =
          (categoryCounts.get(right) ?? 0) - (categoryCounts.get(left) ?? 0);
        return countDelta || mcpCategories.indexOf(left) - mcpCategories.indexOf(right);
      }),
    [categoryCounts],
  );
  const primaryCategories = useMemo(() => {
    const primary = rankedCategories.slice(0, PRIMARY_CATEGORY_COUNT);
    if (
      selectedCategory !== "全部类别" &&
      !primary.includes(selectedCategory)
    ) {
      return [...primary.slice(0, PRIMARY_CATEGORY_COUNT - 1), selectedCategory];
    }
    return primary;
  }, [rankedCategories, selectedCategory]);
  const remainingCategories = useMemo(
    () => rankedCategories.filter((category) => !primaryCategories.includes(category)),
    [primaryCategories, rankedCategories],
  );
  const filteredProjects = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase("zh-CN");
    const matches = mcpProjects.filter((project) => {
      if (
        selectedCategory !== "全部类别" &&
        project.category !== selectedCategory
      ) {
        return false;
      }
      if (
        !matchesAvailability(
          effectiveAvailability(project.id),
          availabilityFilter,
        )
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
        ...project.requiredCapabilities,
        ...project.adaptationLimitations,
      ]
        .join(" ")
        .toLocaleLowerCase("zh-CN");
      return searchableText.includes(normalizedQuery);
    });
    const prioritized = prioritizeReadyProjects(
      matches,
      (project) => effectiveAvailability(project.id),
      availabilityFilter === "all" && selectedCategory === "全部类别",
    );
    // 收藏的项目优先展示，紧随 ready 排序之后。
    const withFavorites = prioritized.filter((project) =>
      favorites.has(project.id),
    );
    const withoutFavorites = prioritized.filter(
      (project) => !favorites.has(project.id),
    );
    return [...withFavorites, ...withoutFavorites];
  }, [
    availabilityFilter,
    effectiveAvailability,
    favorites,
    query,
    selectedCategory,
  ]);
  const visibleProjects = useMemo(
    () => filteredProjects.slice(0, visibleCount),
    [filteredProjects, visibleCount],
  );

  function toggleFavorite(projectId: string) {
    setFavorites((current) => {
      const next = new Set(current);
      if (next.has(projectId)) {
        next.delete(projectId);
      } else {
        next.add(projectId);
      }
      try {
        window.localStorage.setItem(
          "modelmirror-mcp-favorites",
          JSON.stringify(Array.from(next)),
        );
      } catch {
        // 存储不可用时静默降级为本次会话内有效。
      }
      return next;
    });
  }

  useEffect(() => {
    setVisibleCount(INITIAL_VISIBLE_COUNT);
  }, [availabilityFilter, query, selectedCategory]);
  return (
    <PageContainer
      activeResource="mcps"
      maxWidthClassName="max-w-[1500px]"
      mobileSidebar={<ModelWorkbenchSidebar compact />}
      showSystemCapabilityBar={false}
      sidebar={<ModelWorkbenchSidebar />}
      sidebarGridClassName="xl:grid-cols-[230px_minmax(0,1fr)] xl:gap-x-[54px]"
    >
      <header className="relative overflow-hidden rounded-xl border border-hire-300/25 bg-ink-900/65 px-4 py-5 sm:px-6">
        <div className="pointer-events-none absolute right-4 top-3 hidden h-16 w-44 opacity-55 lg:block">
          <span className="absolute right-1 top-2 h-1.5 w-1.5 rounded-full bg-hire-200" />
          <span className="absolute right-14 top-9 h-1 w-1 rounded-full bg-cyan-200" />
          <span className="absolute right-28 top-4 h-1 w-1 rounded-full bg-hire-300" />
          <span className="absolute right-3 top-4 h-px w-28 -rotate-[14deg] bg-hire-200/40" />
          <span className="absolute right-10 top-8 h-px w-24 rotate-[16deg] bg-cyan-200/30" />
        </div>
        <div className="relative grid gap-5 lg:grid-cols-[minmax(250px,0.8fr)_minmax(560px,1.2fr)] lg:items-center">
          <div className="min-w-0">
            <h1 className="text-3xl font-semibold tracking-[-0.025em] text-white sm:text-4xl">
              MCP 工具采购
            </h1>
            <p className="mt-2 text-sm leading-6 text-slate-300 sm:text-base">
              安装 MCP 服务，为 AI 扩展更多工具能力
            </p>
          </div>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-3 sm:grid-cols-4 lg:pr-32">
            {([
              [Boxes, mcpProjects.length, "个工具", "text-hire-100"],
              [CheckCircle2, readyCount, "可用", "text-emerald-100"],
              [Grid2X2, mcpCategories.length, "分类", "text-hire-100"],
              [Plug, sessions.length, "已连接", "text-cyan-100"],
            ] as const).map(([Icon, value, label, tone]) => (
              <div className="flex min-w-0 items-center gap-2.5" key={label}>
                <Icon aria-hidden="true" className={tone} size={20} strokeWidth={1.8} />
                <div className="min-w-0">
                  <dt className="text-xs text-slate-400">{label}</dt>
                  <dd className={`text-xl font-semibold tabular-nums ${tone}`}>{value}</dd>
                </div>
              </div>
            ))}
          </dl>
        </div>
      </header>

      <section className="mt-4">

        {runtimeError ? (
          <div className="mb-3 rounded-lg border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-100" role="status">
            {runtimeError}
          </div>
        ) : null}

        <div className="rounded-xl border border-white/10 bg-ink-900/55 p-3 sm:p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-0 rounded-lg border border-white/10 bg-ink-950/55 p-1" role="tablist" aria-label="MCP 工具视图">
              <button
                aria-selected={activeView === "servers"}
                className={`min-h-10 rounded-md px-4 py-2 text-sm font-semibold transition ${
                  activeView === "servers"
                    ? "bg-hire-300 text-ink-950"
                    : "text-slate-300 hover:bg-white/[0.055] hover:text-white"
                }`}
                onClick={() => setActiveView("servers")}
                role="tab"
                type="button"
              >
                工具货架
              </button>
              <button
                aria-selected={activeView === "registry"}
                className={`min-h-10 rounded-md px-4 py-2 text-sm font-semibold transition ${
                  activeView === "registry"
                    ? "bg-hire-300 text-ink-950"
                    : "text-slate-300 hover:bg-white/[0.055] hover:text-white"
                }`}
                onClick={() => {
                  setActiveView("registry");
                  void refreshRuntime();
                }}
                role="tab"
                type="button"
              >
                已连接注册表
              </button>
            </div>
            <button
              className="flex min-h-10 items-center gap-2 rounded-lg border border-white/10 bg-white/[0.035] px-3 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/35 hover:text-hire-100 disabled:cursor-wait disabled:opacity-60"
              disabled={isLoadingRuntime}
              onClick={() => void refreshRuntime()}
              type="button"
            >
              <RefreshCw aria-hidden="true" className={isLoadingRuntime ? "motion-safe:animate-spin" : ""} size={16} />
              {isLoadingRuntime ? "正在刷新" : "刷新连接状态"}
            </button>
          </div>

          {activeView === "servers" ? (
            <div className="relative mt-3">
              <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={18} />
              <label className="sr-only" htmlFor="mcp-search">搜索 MCP 工具</label>
              <input
                className="min-h-11 w-full rounded-lg border border-white/10 bg-ink-950/70 py-2.5 pl-10 pr-28 text-sm text-white outline-none placeholder:text-slate-400 focus:border-hire-300/55 focus:ring-2 focus:ring-hire-300/15"
                id="mcp-search"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索名称、用途或标签"
                type="search"
                value={query}
              />
              {query || selectedCategory !== "全部类别" || availabilityFilter !== "all" ? (
                <button
                  className="absolute right-2 top-1/2 min-h-9 -translate-y-1/2 rounded-md px-3 text-xs font-semibold text-slate-300 transition hover:bg-white/[0.06] hover:text-white"
                  onClick={() => {
                    setQuery("");
                    setSelectedCategory("全部类别");
                    setAvailabilityFilter("all");
                  }}
                  type="button"
                >
                  清除筛选
                </button>
              ) : null}
            </div>
          ) : null}
        </div>

        {activeView === "servers" ? (
          <div className="mt-3 rounded-xl border border-white/10 bg-white/[0.025] px-3 py-2.5 sm:px-4">
            <div className="flex min-w-0 flex-wrap items-center gap-2.5">
              <button
                aria-pressed={availabilityFilter === "all" && selectedCategory === "全部类别"}
                className={`min-h-9 whitespace-nowrap rounded-md border px-3 py-1.5 text-xs font-semibold transition ${
                  availabilityFilter === "all" && selectedCategory === "全部类别"
                    ? "border-hire-300/55 bg-hire-300 text-ink-950"
                    : "border-white/10 bg-white/[0.035] text-slate-300 hover:border-hire-300/30 hover:text-white"
                }`}
                onClick={() => {
                  setAvailabilityFilter("all");
                  setSelectedCategory("全部类别");
                }}
                type="button"
              >
                全部 · {mcpProjects.length}
              </button>
              <div className="flex flex-wrap items-center gap-2" role="group" aria-label="按 MCP 适配状态筛选">
                <span className="whitespace-nowrap text-xs font-semibold text-slate-300">按适配状态</span>
                {([
                  ["ready", "可用", readyCount],
                  ["adapting", "适配中", adaptingCount],
                  ["planned", "已排期", plannedCount],
                  ["blocked", "未适配", blockedCount],
                ] as const).map(([value, label, count]) => {
                  const isSelected = availabilityFilter === value;
                  return (
                    <button
                      aria-pressed={isSelected}
                      className={`min-h-9 whitespace-nowrap rounded-md border px-3 py-1.5 text-xs font-semibold transition ${
                        isSelected
                          ? "border-hire-300/55 bg-hire-300 text-ink-950"
                          : "border-white/10 bg-white/[0.035] text-slate-300 hover:border-hire-300/30 hover:text-white"
                      }`}
                      key={value}
                      onClick={() => setAvailabilityFilter(value)}
                      type="button"
                    >
                      {label} · {count}
                    </button>
                  );
                })}
              </div>
              <span aria-hidden="true" className="hidden h-7 w-px shrink-0 bg-white/10 sm:block" />
              <div
                className="flex items-center gap-2 overflow-x-auto pb-1"
                role="group"
                aria-label="按工具类别筛选"
              >
                <span className="whitespace-nowrap text-xs font-semibold text-slate-300">按工具类别</span>
                {primaryCategories.map((category) => {
                  const isSelected = selectedCategory === category;
                  const count = categoryCounts.get(category) ?? 0;
                  return (
                    <button
                      aria-pressed={isSelected}
                      className={`min-h-9 whitespace-nowrap rounded-md border px-3 py-1.5 text-xs font-semibold transition ${
                        isSelected
                          ? "border-hire-300/55 bg-hire-300 text-ink-950"
                          : "border-white/10 bg-white/[0.035] text-slate-300 hover:border-hire-300/30 hover:text-white"
                      }`}
                      key={category}
                      onClick={() => setSelectedCategory(category)}
                      type="button"
                    >
                      {category} · {count}
                    </button>
                  );
                })}
                <button
                  aria-expanded={categoriesExpanded}
                  className="flex min-h-9 items-center gap-1 whitespace-nowrap rounded-md border border-hire-300/30 bg-hire-300/[0.07] px-3 py-1.5 text-xs font-semibold text-hire-100 transition hover:bg-hire-300/[0.12]"
                  onClick={() => setCategoriesExpanded((expanded) => !expanded)}
                  type="button"
                >
                  {categoriesExpanded ? "收起分类" : "更多分类"}
                  <ChevronDown aria-hidden="true" className={`transition-transform ${categoriesExpanded ? "rotate-180" : ""}`} size={14} />
                </button>
              </div>
            </div>
            {categoriesExpanded ? (
              <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-white/10 pt-2" role="group" aria-label="更多 MCP 工具类别">
                {remainingCategories.map((category) => {
                  const isSelected = selectedCategory === category;
                  return (
                    <button
                      aria-pressed={isSelected}
                      className={`min-h-9 whitespace-nowrap rounded-md border px-3 py-1.5 text-xs font-semibold transition ${
                        isSelected
                          ? "border-hire-300/55 bg-hire-300 text-ink-950"
                          : "border-white/10 bg-white/[0.035] text-slate-300 hover:border-hire-300/30 hover:text-white"
                      }`}
                      key={category}
                      onClick={() => setSelectedCategory(category)}
                      type="button"
                    >
                      {category} · {categoryCounts.get(category) ?? 0}
                    </button>
                  );
                })}
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="mb-3 mt-4 flex items-center justify-between gap-3">
          <h2 className="text-xl font-semibold text-white sm:text-2xl">
            {activeView === "servers" ? "已上架工具箱" : "已连接注册表"}
          </h2>
          <span className="w-fit shrink-0 rounded-md border border-white/10 bg-white/[0.035] px-3 py-1.5 text-xs font-semibold text-slate-300">
            {activeView === "servers"
              ? `匹配 ${filteredProjects.length}`
              : `${registryTools.length} 个工具`}
          </span>
        </div>

        {activeView === "servers" ? (
          filteredProjects.length > 0 ? (
            <>
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-3">
                {visibleProjects.map((project) => {
                  const adapterStatus = adapterByProject.get(project.id);
                  return (
                    <McpServerCard
                      adapterStatus={adapterStatus}
                      favorite={favorites.has(project.id)}
                      key={project.id}
                      onConnectionChange={() => void refreshRuntime()}
                      onToggleFavorite={() => toggleFavorite(project.id)}
                      project={project}
                      restoredSession={sessions.find(
                        (session) => session.session_id === adapterStatus?.session_id,
                      )}
                    />
                  );
                })}
              </div>
              {visibleProjects.length < filteredProjects.length ? (
                <div className="mt-6 flex justify-center">
                  <button
                    className="min-h-11 rounded-lg border border-brand-300/30 bg-brand-300/10 px-5 py-2.5 text-sm font-semibold text-brand-100 transition hover:bg-brand-300/15 focus:outline-none focus:ring-2 focus:ring-brand-300/50"
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
                className="mt-4 min-h-11 rounded-full bg-brand-300 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-brand-200"
                onClick={() => {
                  setQuery("");
                  setSelectedCategory("全部类别");
                  setAvailabilityFilter("all");
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
