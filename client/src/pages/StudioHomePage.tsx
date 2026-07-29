import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import matrixOasisTeaser from "../assets/matrix-oasis/matrix-oasis-teaser-640.webp";
import PageContainer from "../components/PageContainer";
import { agents } from "../data/agents";
import { mcpProjects } from "../data/mcpProjects";
import { skillProjects } from "../data/skillProjects";
import {
  getFriendlyRunStatusLabel,
  getFriendlyRunTypeLabel,
  replaceLegacyAgentTerms,
} from "../utils/userFriendlyText";

type WorkspaceCategory =
  | "all"
  | "agents"
  | "workflow"
  | "knowledge"
  | "mcp"
  | "api_tools"
  | "database"
  | "skills"
  | "prompts"
  | "environment"
  | "runs";

type ResourceTag =
  | "all"
  | "runnable"
  | "creatable"
  | "observable"
  | "planned"
  | "xpert";

type Tone = "ready" | "partial" | "planned" | "error";

interface Loadable<T> {
  data: T;
  error: string;
  loading: boolean;
}

interface KnowledgeBasePayload {
  id: string;
  name: string;
}

interface FileAssetPayload {
  file_asset_id?: string;
  file_name?: string;
  filename?: string;
  name?: string;
}

interface ArtifactPayload {
  artifact_id: string;
  title: string;
  chunk_count?: number;
}

interface McpSessionPayload {
  session_id: string;
  server_name?: string | null;
  server_id?: string | null;
  status?: string | null;
}

interface RegistryToolPayload {
  name: string;
  description?: string | null;
  server_id?: string;
}

interface InstalledSkillPayload {
  skill_id: string;
  name: string;
  description: string;
}

interface RuntimeRunPayload {
  run_id: string;
  run_type: string;
  status: string;
  title: string;
  created_at: number;
  updated_at: number;
}

interface DataXProjectPayload {
  project_id: string;
  name: string;
  status: string;
}

interface ResourceAction {
  disabled?: boolean;
  href?: string;
  label: string;
}

interface ResourceCardModel {
  category: Exclude<WorkspaceCategory, "all">;
  count: string;
  description: string;
  error?: string;
  icon: string;
  id: string;
  items: string[];
  loading?: boolean;
  metricLabel: string;
  primaryAction: ResourceAction;
  secondaryAction?: ResourceAction;
  status: string;
  tags: Exclude<ResourceTag, "all">[];
  title: string;
  tone: Tone;
}

interface QuickAction {
  description: string;
  href: string;
  label: string;
  title: string;
}

function MatrixOasisTeaser() {
  return (
    <Link
      aria-label="进入矩阵绿洲预告"
      className="group relative mb-6 flex min-h-40 overflow-hidden rounded-xl bg-ink-950 shadow-[0_1px_0_rgba(255,255,255,0.08)_inset] focus-visible:outline-none sm:min-h-48"
      to="/matrix-oasis"
    >
      <img
        alt="发光的蓝紫色矩阵门廊"
        className="absolute inset-0 h-full w-full object-cover object-center opacity-55 transition duration-500 ease-out group-hover:scale-[1.025] group-hover:opacity-70"
        decoding="async"
        height="320"
        src={matrixOasisTeaser}
        width="640"
      />
      <span className="absolute inset-0 bg-[linear-gradient(90deg,rgba(3,6,17,0.96)_0%,rgba(3,6,17,0.76)_48%,rgba(3,6,17,0.18)_100%)]" />
      <span className="absolute inset-x-0 bottom-0 h-px bg-[linear-gradient(90deg,rgba(36,217,255,0.8),rgba(124,58,237,0.7),transparent)]" />

      <span className="relative flex w-full flex-col justify-between gap-5 p-5 sm:flex-row sm:items-end sm:p-7">
        <span className="max-w-xl">
          <span className="inline-flex rounded-full border border-brand-300/30 bg-brand-300/10 px-2.5 py-1 text-[11px] font-semibold text-brand-100">
            世界生成中
          </span>
          <span className="mt-3 block text-2xl font-semibold tracking-[-0.025em] text-white sm:text-3xl">
            矩阵绿洲
          </span>
          <span className="mt-2 block text-sm leading-6 text-slate-300">
            一段尚未完成的人机开放世界预告。
          </span>
        </span>
        <span className="inline-flex min-h-11 shrink-0 items-center justify-center self-start rounded-full border border-white/20 bg-white/10 px-4 text-sm font-semibold text-white transition duration-200 group-hover:border-brand-200/60 group-hover:bg-brand-300 group-hover:text-ink-950 sm:self-auto">
          进入预告
          <span aria-hidden="true" className="ml-2 transition-transform duration-200 group-hover:translate-x-0.5">→</span>
        </span>
      </span>
    </Link>
  );
}

const categories: Array<{ key: WorkspaceCategory; label: string }> = [
  { key: "all", label: "全部" },
  { key: "agents", label: "数字专家" },
  { key: "workflow", label: "工作流" },
  { key: "knowledge", label: "知识库" },
  { key: "mcp", label: "MCP 工具集" },
  { key: "api_tools", label: "API 工具" },
  { key: "database", label: "数据库" },
  { key: "skills", label: "Skill" },
  { key: "prompts", label: "提示词" },
  { key: "environment", label: "环境" },
  { key: "runs", label: "运行记录" },
];

const tagFilters: Array<{ key: ResourceTag; label: string }> = [
  { key: "all", label: "全部标签" },
  { key: "runnable", label: "可运行" },
  { key: "creatable", label: "可创建" },
  { key: "observable", label: "可观测" },
  { key: "planned", label: "待接入" },
  { key: "xpert", label: "智能体可用" },
];

const quickActions: QuickAction[] = [
  {
    title: "创建 Data X 项目",
    description: "导入本地文件，建立语义模型并发布可由 Agent 查询的业务指标。",
    href: "/datax",
    label: "打开 Data X",
  },
  {
    title: "智能体自动化",
    description: "按单次、间隔或 Cron 调度固定版本智能体，处理重试、预算与死信。",
    href: "/agents/automations",
    label: "打开自动化工作台",
  },
  {
    title: "长期 Goal",
    description: "审核规划智能体生成的依赖计划，暂停、恢复并追踪多智能体协作执行。",
    href: "/agents/goals",
    label: "打开 Goal 工作台",
  },
  {
    title: "创建智能体",
    description: "从默认 Agent 工作流开始，保存草稿并发布不可变版本。",
    href: "/agents/studio/new",
    label: "新建智能体",
  },
  {
    title: "我的智能体",
    description: "管理草稿、发布版本，并进入独立聊天页运行。",
    href: "/agents/studio",
    label: "打开 Studio",
  },
  {
    title: "创建工作流",
    description: "进入经典画布，编排 Agent、工具和知识节点。",
    href: "/workflow",
    label: "打开画布",
  },
  {
    title: "生成工作流草稿",
    description: "用元智能体把自然语言目标拆成可编辑工作流。",
    href: "/agents/meta-agent",
    label: "进入工作台",
  },
  {
    title: "管理知识库",
    description: "上传文档，查看知识流水线和引用锚点。",
    href: "/rag",
    label: "打开 RAG",
  },
  {
    title: "连接 MCP",
    description: "管理 MCP server 会话和可调用工具。",
    href: "/mcps",
    label: "管理 MCP",
  },
  {
    title: "安装 Skill",
    description: "查看已安装 Skill，并从仓库注册能力。",
    href: "/skills",
    label: "打开 Skill",
  },
  {
    title: "查看运行运维",
    description: "集中查看 MCP、工具、run 和 checkpoint。",
    href: "/runtime",
    label: "打开运维",
  },
];

const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

function createLoadable<T>(data: T): Loadable<T> {
  return { data, error: "", loading: true };
}

async function fetchJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`请求失败：${response.status}`);
  }
  return response.json() as Promise<T>;
}

function toneClass(tone: Tone) {
  if (tone === "ready") {
    return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  }
  if (tone === "partial") {
    return "border-hire-300/30 bg-hire-300/10 text-hire-100";
  }
  if (tone === "error") {
    return "border-rose-300/30 bg-rose-300/10 text-rose-100";
  }
  return "border-white/10 bg-white/[0.055] text-slate-300";
}

function formatRunTime(value: number) {
  if (!Number.isFinite(value)) return "刚刚";
  const timestamp = value > 10_000_000_000 ? value : value * 1000;
  return dateFormatter.format(new Date(timestamp));
}

function compactItems(items: string[]) {
  return items.filter(Boolean).slice(0, 4);
}

function countBy<T extends string>(items: T[]) {
  return items.reduce<Record<string, number>>((result, item) => {
    result[item] = (result[item] ?? 0) + 1;
    return result;
  }, {});
}

function ActionLink({ action, primary = false }: { action: ResourceAction; primary?: boolean }) {
  const baseClass = primary
    ? "rounded-full bg-hire-300 px-3 py-1.5 text-xs font-semibold text-ink-950 transition hover:bg-hire-200"
    : "rounded-full border border-white/10 bg-white/[0.055] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100";

  if (action.disabled || !action.href) {
    return (
      <button
        className="cursor-not-allowed rounded-full border border-white/10 bg-white/[0.035] px-3 py-1.5 text-xs font-semibold text-slate-500"
        disabled
        type="button"
      >
        {action.label}
      </button>
    );
  }

  return (
    <Link className={baseClass} to={action.href}>
      {action.label}
    </Link>
  );
}

function ResourceCard({ card }: { card: ResourceCardModel }) {
  const visibleItems = compactItems(card.items);

  return (
    <article className="rounded-lg border border-white/10 bg-ink-950/72 p-4 transition duration-200 hover:-translate-y-0.5 hover:border-hire-300/35 hover:bg-surface-900/88">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-hire-300/25 bg-hire-300/10 text-xs font-semibold text-hire-100">
            {card.icon}
          </span>
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-white">{card.title}</h2>
            <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-400">
              {card.description}
            </p>
          </div>
        </div>
        <span
          className={`shrink-0 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${toneClass(
            card.error ? "error" : card.tone,
          )}`}
        >
          {card.error ? "暂不可用" : card.status}
        </span>
      </div>

      <div className="mt-4 grid grid-cols-[minmax(0,1fr)_auto] items-end gap-3 border-t border-white/10 pt-4">
        <div>
          <p className="text-[11px] text-slate-500">{card.metricLabel}</p>
          <p className="mt-1 text-2xl font-semibold text-white">
            {card.loading ? "..." : card.count}
          </p>
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          {card.secondaryAction ? <ActionLink action={card.secondaryAction} /> : null}
          <ActionLink action={card.primaryAction} primary />
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-1.5">
        {card.tags.map((tag) => (
          <span
            className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[11px] text-slate-400"
            key={tag}
          >
            {tagFilters.find((item) => item.key === tag)?.label ?? tag}
          </span>
        ))}
      </div>

      {card.error ? (
        <p className="mt-3 rounded-lg border border-rose-300/20 bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-100">
          {card.error}
        </p>
      ) : visibleItems.length > 0 ? (
        <ul className="mt-3 space-y-2">
          {visibleItems.map((item) => (
            <li className="truncate text-xs text-slate-400" key={item}>
              {item}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-xs text-slate-400">
          暂无最近项目，进入模块后可继续创建或连接资源。
        </p>
      )}
    </article>
  );
}

function WorkspaceSidebar() {
  return (
    <div>
      <p className="text-sm font-semibold text-white">工作空间</p>
      <p className="mt-2 text-sm leading-6 text-slate-400">
        集中查看智能体、工作流、知识库、工具、模型服务与近期运行状态。
      </p>
      <div className="mt-4 space-y-2">
        <Link
          className="block rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-3 py-2 text-sm font-semibold text-cyan-100 transition hover:bg-cyan-300/20"
          to="/agents/goals"
        >
          打开长期 Goal
        </Link>
        <Link
          className="block rounded-lg border border-hire-300/25 bg-hire-300/10 px-3 py-2 text-sm font-semibold text-hire-100 transition hover:bg-hire-300/20"
          to="/agents/studio"
        >
          打开 Agent Studio
        </Link>
        <Link
          className="block rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10"
          to="/workflow"
        >
          打开工作流画布
        </Link>
        <Link
          className="block rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10"
          to="/agents/meta-agent"
        >
          进入任务工作台
        </Link>
        <Link
          className="block rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10"
          to="/runtime"
        >
          打开 Runtime 运维
        </Link>
      </div>
    </div>
  );
}

export default function StudioHomePage() {
  const [activeCategory, setActiveCategory] = useState<WorkspaceCategory>("all");
  const [activeTag, setActiveTag] = useState<ResourceTag>("all");
  const [searchTerm, setSearchTerm] = useState("");
  const [knowledgeBases, setKnowledgeBases] = useState(
    createLoadable<KnowledgeBasePayload[]>([]),
  );
  const [fileAssets, setFileAssets] = useState(createLoadable<FileAssetPayload[]>([]));
  const [artifacts, setArtifacts] = useState(createLoadable<ArtifactPayload[]>([]));
  const [mcpSessions, setMcpSessions] = useState(createLoadable<McpSessionPayload[]>([]));
  const [registryTools, setRegistryTools] = useState(
    createLoadable<RegistryToolPayload[]>([]),
  );
  const [installedSkills, setInstalledSkills] = useState(
    createLoadable<InstalledSkillPayload[]>([]),
  );
  const [runtimeRuns, setRuntimeRuns] = useState(createLoadable<RuntimeRunPayload[]>([]));
  const [dataXProjects, setDataXProjects] = useState(
    createLoadable<DataXProjectPayload[]>([]),
  );

  useEffect(() => {
    document.title = "模镜 - 组织工作空间";
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadKnowledge() {
      try {
        const [kbData, assetData, artifactData] = await Promise.all([
          fetchJson<{ knowledge_bases: KnowledgeBasePayload[] }>(
            "/api/rag/knowledge_bases",
          ),
          fetchJson<{ assets: FileAssetPayload[] }>("/api/rag/pipeline/assets"),
          fetchJson<{ artifacts: ArtifactPayload[] }>("/api/rag/pipeline/artifacts"),
        ]);
        if (cancelled) return;
        setKnowledgeBases({
          data: kbData.knowledge_bases ?? [],
          error: "",
          loading: false,
        });
        setFileAssets({ data: assetData.assets ?? [], error: "", loading: false });
        setArtifacts({ data: artifactData.artifacts ?? [], error: "", loading: false });
      } catch (error) {
        if (cancelled) return;
        const message =
          error instanceof Error ? error.message : "知识库资源加载失败";
        setKnowledgeBases({ data: [], error: message, loading: false });
        setFileAssets({ data: [], error: message, loading: false });
        setArtifacts({ data: [], error: message, loading: false });
      }
    }

    async function loadMcp() {
      try {
        const [sessionsData, toolsData] = await Promise.all([
          fetchJson<{ sessions: McpSessionPayload[] }>("/api/mcp/sessions"),
          fetchJson<{ tools: RegistryToolPayload[] }>("/api/registry/tools"),
        ]);
        if (cancelled) return;
        setMcpSessions({ data: sessionsData.sessions ?? [], error: "", loading: false });
        setRegistryTools({ data: toolsData.tools ?? [], error: "", loading: false });
      } catch (error) {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : "MCP 运行态加载失败";
        setMcpSessions({ data: [], error: message, loading: false });
        setRegistryTools({ data: [], error: message, loading: false });
      }
    }

    async function loadSkills() {
      try {
        const data = await fetchJson<{ skills: InstalledSkillPayload[] }>(
          "/api/skills/installed",
        );
        if (cancelled) return;
        setInstalledSkills({ data: data.skills ?? [], error: "", loading: false });
      } catch (error) {
        if (cancelled) return;
        setInstalledSkills({
          data: [],
          error: error instanceof Error ? error.message : "Skill 加载失败",
          loading: false,
        });
      }
    }

    async function loadRuns() {
      try {
        const data = await fetchJson<RuntimeRunPayload[]>("/api/runtime/runs?limit=8");
        if (cancelled) return;
        setRuntimeRuns({ data: data ?? [], error: "", loading: false });
      } catch (error) {
        if (cancelled) return;
        setRuntimeRuns({
          data: [],
          error: error instanceof Error ? error.message : "运行记录加载失败",
          loading: false,
        });
      }
    }

    async function loadDataX() {
      try {
        const data = await fetchJson<{ items: DataXProjectPayload[] }>(
          "/api/datax/projects",
        );
        if (cancelled) return;
        setDataXProjects({ data: data.items ?? [], error: "", loading: false });
      } catch (error) {
        if (cancelled) return;
        setDataXProjects({
          data: [],
          error: error instanceof Error ? error.message : "Data X 加载失败",
          loading: false,
        });
      }
    }

    void loadKnowledge();
    void loadMcp();
    void loadSkills();
    void loadRuns();
    void loadDataX();

    return () => {
      cancelled = true;
    };
  }, []);

  const runStats = useMemo(() => {
    const byType = countBy(runtimeRuns.data.map((run) => run.run_type));
    const byStatus = countBy(runtimeRuns.data.map((run) => run.status));
    return { byStatus, byType };
  }, [runtimeRuns.data]);

  const resourceCards = useMemo<ResourceCardModel[]>(() => {
    const knowledgeError =
      knowledgeBases.error || fileAssets.error || artifacts.error || "";
    const mcpError = mcpSessions.error || registryTools.error || "";

    return [
      {
        category: "agents",
        count: String(agents.length),
        description: "本地智能体市场与元智能体任务工作台入口。",
        icon: "AI",
        id: "agents",
        items: agents.slice(0, 4).map((agent) => agent.name),
        metricLabel: "可用智能体",
        primaryAction: { href: "/agents", label: "浏览专家" },
        secondaryAction: { href: "/agents/meta-agent", label: "任务工作台" },
        status: "可用",
        tags: ["runnable", "creatable", "xpert"],
        title: "数字专家",
        tone: "ready",
      },
      {
        category: "workflow",
        count: "2",
        description: "经典画布与 native 校验线，承载 Agent、Handoff、Toolset 与知识节点。",
        icon: "FLOW",
        id: "workflow",
        items: ["classic /workflow", "workflow-native validate", "节点运行观测", "RunRegistry"],
        metricLabel: "工作流入口",
        primaryAction: { href: "/workflow", label: "打开画布" },
        secondaryAction: { href: "/agents/meta-agent", label: "生成草稿" },
        status: "可运行",
        tags: ["runnable", "creatable", "observable", "xpert"],
        title: "工作流",
        tone: "ready",
      },
      {
        category: "knowledge",
        count: knowledgeError ? "0" : String(knowledgeBases.data.length),
        description: "本地 RAG 知识库、FileAsset、Artifact 与 CitationAnchor 只读视图。",
        error: knowledgeError,
        icon: "KB",
        id: "knowledge",
        items: knowledgeError
          ? []
          : [
              ...knowledgeBases.data.map((kb) => kb.name),
              `${fileAssets.data.length} 个 FileAsset`,
              `${artifacts.data.length} 个 Artifact`,
            ],
        loading: knowledgeBases.loading || fileAssets.loading || artifacts.loading,
        metricLabel: "知识库",
        primaryAction: { href: "/rag", label: "管理知识库" },
        status: "可运行",
        tags: ["runnable", "creatable", "observable", "xpert"],
        title: "知识库",
        tone: "ready",
      },
      {
        category: "mcp",
        count: mcpError ? "0" : String(registryTools.data.length),
        description: "MCP Server 会话、候选工具箱和全局工具注册表入口。",
        error: mcpError,
        icon: "MCP",
        id: "mcp",
        items: mcpError
          ? []
          : [
              `${mcpSessions.data.length} 个运行会话`,
              `${mcpProjects.length} 个候选工具箱`,
              ...registryTools.data.map((tool) => tool.name),
            ],
        loading: mcpSessions.loading || registryTools.loading,
        metricLabel: "已注册工具",
        primaryAction: { href: "/toolsets", label: "管理 Toolset" },
        secondaryAction: { href: "/runtime", label: "查看运维" },
        status: "可运行",
        tags: ["runnable", "creatable", "observable", "xpert"],
        title: "MCP 工具集",
        tone: "ready",
      },
      {
        category: "api_tools",
        count: "可配置",
        description: "通过 Toolset 管理 API 与 HTTP 工具，并复用于智能体和工作流。",
        icon: "API",
        id: "api-tools",
        items: ["OpenAPI 工具集", "鉴权配置", "请求模板", "响应 schema"],
        metricLabel: "API 工具",
        primaryAction: { href: "/toolsets", label: "管理 Toolset" },
        secondaryAction: { href: "/runtime", label: "查看运行" },
        status: "可配置",
        tags: ["runnable", "creatable", "observable", "xpert"],
        title: "API 工具",
        tone: "ready",
      },
      {
        category: "database",
        count: dataXProjects.error ? "0" : String(dataXProjects.data.length),
        description: "本地文件快照、DuckDB 语义模型、版本化指标与 Agent 数据分析。",
        error: dataXProjects.error,
        icon: "DX",
        id: "database",
        items: dataXProjects.error
          ? []
          : [
              ...dataXProjects.data.slice(0, 4).map((project) => project.name),
              "CSV / XLSX / Parquet",
              "基础与派生指标",
            ],
        loading: dataXProjects.loading,
        metricLabel: "Data X 项目",
        primaryAction: { href: "/datax", label: "打开 Data X" },
        status: "可运行",
        tags: ["runnable", "creatable", "observable", "xpert"],
        title: "Data X 语义指标",
        tone: "ready",
      },
      {
        category: "skills",
        count: installedSkills.error ? "0" : String(installedSkills.data.length),
        description: "Skill 市场、已安装技能和仓库安装能力。",
        error: installedSkills.error,
        icon: "SK",
        id: "skills",
        items: installedSkills.error
          ? []
          : [
              ...installedSkills.data.map((skill) => skill.name),
              `${skillProjects.length} 个市场候选技能`,
            ],
        loading: installedSkills.loading,
        metricLabel: "已安装",
        primaryAction: { href: "/skills", label: "安装 Skill" },
        status: "部分实现",
        tags: ["creatable", "observable", "xpert"],
        title: "Skill",
        tone: "partial",
      },
      {
        category: "prompts",
        count: "可发布",
        description: "版本化 Prompt Command 与声明式 Plugin，支持固定版本绑定智能体。",
        icon: "PR",
        id: "prompts",
        items: ["Prompt Profile 草稿与版本", "/命令与 // 转义", "Plugin 资源包"],
        metricLabel: "Prompt / Plugin",
        primaryAction: { href: "/prompts", label: "管理 Prompt" },
        secondaryAction: { href: "/plugins", label: "管理 Plugin" },
        status: "部分实现",
        tags: ["runnable", "creatable", "xpert"],
        title: "Prompt 与 Plugin",
        tone: "partial",
      },
      {
        category: "environment",
        count: "Default",
        description: "模型服务连接、环境变量和运行依赖入口。",
        icon: "ENV",
        id: "environment",
        items: ["OpenRouter / newAPI 网关", "Docker runtime", "MCP / Skill 依赖"],
        metricLabel: "当前环境",
        primaryAction: { href: "/settings", label: "进入设置" },
        status: "可配置",
        tags: ["runnable", "observable", "xpert"],
        title: "环境",
        tone: "ready",
      },
      {
        category: "runs",
        count: runtimeRuns.error ? "0" : String(runtimeRuns.data.length),
        description: "Runtime Ops 只读入口，汇总 MCP、工具、Skill 与 workflow/chat/agent/handoff run。",
        error: runtimeRuns.error,
        icon: "RUN",
        id: "runs",
        items: runtimeRuns.error
          ? []
          : runtimeRuns.data.map(
              (run) =>
                `${getFriendlyRunTypeLabel(run.run_type)} · ${getFriendlyRunStatusLabel(run.status)}`,
            ),
        loading: runtimeRuns.loading,
        metricLabel: "最近运行",
        primaryAction: { href: "/runtime", label: "打开运维" },
        status: "可观测",
        tags: ["observable", "xpert"],
        title: "运行记录",
        tone: "partial",
      },
    ];
  }, [
    artifacts,
    dataXProjects,
    fileAssets,
    installedSkills,
    knowledgeBases,
    mcpSessions,
    registryTools,
    runtimeRuns,
  ]);

  const filteredCards = useMemo(() => {
    const normalizedSearch = searchTerm.trim().toLowerCase();
    return resourceCards.filter((card) => {
      const matchesCategory =
        activeCategory === "all" || card.category === activeCategory;
      const matchesTag = activeTag === "all" || card.tags.includes(activeTag);
      const matchesSearch =
        normalizedSearch.length === 0 ||
        [
          card.title,
          card.description,
          card.status,
          card.count,
          ...card.items,
          ...card.tags,
        ]
          .join(" ")
          .toLowerCase()
          .includes(normalizedSearch);
      return matchesCategory && matchesTag && matchesSearch;
    });
  }, [activeCategory, activeTag, resourceCards, searchTerm]);

  return (
    <PageContainer
      maxWidthClassName="max-w-[1760px]"
      sidebar={<WorkspaceSidebar />}
    >
      <header className="mb-6 border-y border-hire-300/20 py-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full border border-hire-300/30 bg-hire-300/10 px-3 py-1 text-xs font-semibold text-hire-100">
                资源与运行总览
              </span>
              <span className="rounded-full border border-white/10 bg-white/[0.055] px-3 py-1 text-xs text-slate-300">
                只读聚合视图
              </span>
            </div>
            <h1 className="mt-3 text-2xl font-semibold text-white sm:text-3xl">
              组织工作空间
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
              从这里进入 Agent Studio、工作流、知识库、工具集、模型服务和运行诊断；所有卡片都指向当前可操作入口。
            </p>
          </div>
          <label className="relative block w-full max-w-sm">
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-xs font-semibold text-slate-400">
              搜索
            </span>
            <input
              className="h-11 w-full rounded-lg border border-white/10 bg-ink-950/72 pl-12 pr-3 text-sm text-white outline-none transition placeholder:text-slate-500 hover:border-white/20 focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10"
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder="资源、状态、运行类型"
              type="search"
              value={searchTerm}
            />
          </label>
        </div>
      </header>

      <MatrixOasisTeaser />

      <section className="mb-6 rounded-lg border border-white/10 bg-white/[0.045] p-4">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-sm font-semibold text-white">快速创建 / 连接</h2>
            <p className="mt-1 text-xs text-slate-400">
              常用入口集中在这里，减少在多个资源页之间来回找入口。
            </p>
          </div>
          <span className="text-xs text-slate-500">入口状态来自当前模块与运行数据</span>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {quickActions.map((action) => (
            <Link
              className="rounded-lg border border-white/10 bg-ink-950/55 p-3 transition hover:border-hire-300/35 hover:bg-hire-300/10"
              key={action.title}
              to={action.href}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-white">{action.title}</p>
                  <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-400">
                    {action.description}
                  </p>
                </div>
                <span className="shrink-0 rounded-full border border-hire-300/25 bg-hire-300/10 px-2.5 py-1 text-[11px] font-semibold text-hire-100">
                  {action.label}
                </span>
              </div>
            </Link>
          ))}
        </div>
      </section>

      <section className="mb-5 space-y-3">
        <div className="flex min-w-0 flex-wrap gap-2">
          {categories.map((category) => (
            <button
              className={`rounded-full border px-3 py-2 text-sm font-semibold transition ${
                activeCategory === category.key
                  ? "border-hire-300 bg-hire-300 text-ink-950"
                  : "border-white/10 bg-white/[0.045] text-slate-300 hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
              }`}
              key={category.key}
              onClick={() => setActiveCategory(category.key)}
              type="button"
            >
              {category.label}
            </button>
          ))}
        </div>
        <div className="flex min-w-0 flex-wrap gap-2">
          {tagFilters.map((tag) => (
            <button
              className={`rounded-full border px-3 py-1.5 text-xs font-semibold transition ${
                activeTag === tag.key
                  ? "border-hire-300/60 bg-hire-300/15 text-hire-100"
                  : "border-white/10 bg-white/[0.035] text-slate-400 hover:border-hire-300/35 hover:text-hire-100"
              }`}
              key={tag.key}
              onClick={() => setActiveTag(tag.key)}
              type="button"
            >
              {tag.label}
            </button>
          ))}
        </div>
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section>
          {filteredCards.length > 0 ? (
            <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
              {filteredCards.map((card) => (
                <ResourceCard card={card} key={card.id} />
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-white/10 bg-white/[0.045] p-8 text-center">
              <p className="text-sm font-semibold text-white">没有匹配的资源</p>
              <p className="mt-2 text-sm text-slate-400">
                换一个分类、标签或搜索词，资源入口仍保留在顶部快速操作中。
              </p>
            </div>
          )}
        </section>

        <aside className="space-y-4">
          <section className="rounded-lg border border-white/10 bg-ink-950/72 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-white">运行观测摘要</h2>
                <p className="mt-1 text-xs text-slate-400">
                  最近 8 条 RunRegistry 记录。
                </p>
              </div>
              <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${toneClass(runtimeRuns.error ? "error" : "partial")}`}>
                {runtimeRuns.error ? "暂不可用" : "内存态"}
              </span>
            </div>
            {runtimeRuns.error ? (
              <p className="mt-4 rounded-lg border border-rose-300/20 bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-100">
                {runtimeRuns.error}
              </p>
            ) : runtimeRuns.loading ? (
              <p className="mt-4 text-sm text-slate-400">运行记录加载中...</p>
            ) : runtimeRuns.data.length > 0 ? (
              <div className="mt-4 space-y-4">
                <div className="grid grid-cols-2 gap-2">
                  {Object.entries(runStats.byStatus).slice(0, 4).map(([status, count]) => (
                    <div
                      className="rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2"
                      key={status}
                    >
                      <p className="text-[11px] text-slate-500">{status}</p>
                      <p className="mt-1 text-lg font-semibold text-white">{count}</p>
                    </div>
                  ))}
                </div>
                <div className="flex flex-wrap gap-2">
                  {Object.entries(runStats.byType).slice(0, 5).map(([type, count]) => (
                    <span
                      className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[11px] text-slate-300"
                      key={type}
                    >
                      {type}: {count}
                    </span>
                  ))}
                </div>
                <div className="space-y-3">
                  {runtimeRuns.data.slice(0, 8).map((run) => (
                    <article
                      className="rounded-lg border border-white/10 bg-white/[0.045] p-3"
                      key={run.run_id}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <p className="truncate text-xs font-semibold text-white">
                          {replaceLegacyAgentTerms(
                            run.title || getFriendlyRunTypeLabel(run.run_type),
                          )}
                        </p>
                        <span className="shrink-0 rounded-full border border-white/10 bg-white/[0.055] px-2 py-0.5 text-[11px] text-slate-300">
                          {getFriendlyRunStatusLabel(run.status)}
                        </span>
                      </div>
                      <p className="mt-2 text-xs text-slate-500">
                        {getFriendlyRunTypeLabel(run.run_type)} · {formatRunTime(run.updated_at ?? run.created_at)}
                      </p>
                    </article>
                  ))}
                </div>
                <Link
                  className="inline-flex rounded-full border border-hire-300/25 bg-hire-300/10 px-3 py-1.5 text-xs font-semibold text-hire-100 transition hover:bg-hire-300/20"
                  to="/runtime"
                >
                  查看 Runtime 运维
                </Link>
              </div>
            ) : (
              <p className="mt-4 rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-xs text-slate-400">
                暂无运行记录。运行工作流或开启 Chat Toolset 后会出现在这里。
              </p>
            )}
          </section>

          <section className="rounded-lg border border-white/10 bg-white/[0.045] p-4">
            <h2 className="text-sm font-semibold text-white">推荐操作</h2>
            <ol className="mt-3 space-y-2 text-xs leading-5 text-slate-400">
              <li>1. 在 Agent Studio 创建或发布智能体版本。</li>
              <li>2. 在工作流画布组合智能体、知识库与 Toolset。</li>
              <li>3. 在知识库运行流水线并检查引用结果。</li>
              <li>4. 在运行诊断查看失败摘要和待处理审批。</li>
            </ol>
          </section>
        </aside>
      </div>
    </PageContainer>
  );
}
