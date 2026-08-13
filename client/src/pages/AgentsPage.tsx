import { useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";
import { useNavigate } from "react-router-dom";
import AgentCard from "../components/AgentCard";
import ModelWorkbenchSidebar from "../components/ModelWorkbenchSidebar";
import PageContainer from "../components/PageContainer";
import PlatformCapabilityCard, {
  type PlatformCapability,
} from "../components/PlatformCapabilityCard";
import {
  DEFAULT_CHAT_MODEL_ID,
  useModelPreference,
} from "../context/ModelPreferenceContext";
import {
  agentDepartmentCounts,
  agentDepartments,
  agents,
  type AgentProfile,
} from "../data/agents";
import {
  AGENT_DEFAULT_MODEL_NOTICE_KEY,
  buildAgentChatPath,
  saveAgentInterview,
} from "../utils/agentInterview";

export const featuredPlatformCapability: PlatformCapability = {
  id: "xpert-studio",
  icon: "XP",
  title: "智能体发布中心",
  summary: "创建、发布并管理可组合的智能体应用。",
  detail: "复用经典工作流内核，把模型、Toolset、知识、中间件与 Handoff 组合成不可变发布版本。",
  tag: "可用",
  eta: "已开放草稿、发布与聊天运行",
  actionLabel: "管理智能体",
};

export const platformCapabilities: PlatformCapability[] = [
  {
    id: "meta-agent",
    icon: "元",
    title: "AI 工作流生成器",
    summary: "一句话生成可编辑工作流。",
    detail: "输入目标后生成原生 React Flow 工作流，并通过现有工作流运行接口执行。",
    tag: "Beta",
    eta: "已接入生成与运行工作台",
    actionLabel: "生成工作流",
  },
  {
    id: "xpert-automations",
    icon: "AT",
    title: "自动化任务",
    summary: "定时运行已发布智能体。",
    detail: "支持预算、并发、失败重试与死信处理。",
    tag: "Beta",
    eta: "已开放自动化工作台",
    actionLabel: "管理任务",
  },
  {
    id: "conversation-goals",
    icon: "GL",
    title: "长期任务",
    summary: "规划可暂停、可恢复的长期目标。",
    detail: "把对话目标拆成可审核的依赖计划，并由已发布智能体持续协作执行。",
    tag: "Beta",
    eta: "已开放规划、执行与恢复工作台",
    actionLabel: "管理任务",
  },
  {
    id: "datax",
    icon: "DX",
    title: "Data X 数据分析",
    summary: "导入数据，生成指标与分析。",
    detail: "支持 CSV、Parquet 与 Excel 数据的本地分析和可视化。",
    tag: "可用",
    eta: "已开放数据导入、建模、指标与分析",
    statusLabel: "服务状态：已开放",
    actionLabel: "分析数据",
  },
  {
    id: "expert-squad",
    icon: "团",
    title: "多智能体协作",
    summary: "组合多个专家协同完成任务。",
    detail: "支持模型融合、自动派工和团队协作。",
    tag: "可用",
    eta: "专家会诊室已上线",
    actionLabel: "组建协作",
  },
];

export const agentWorkspaceCapability: PlatformCapability = {
  id: "agent-workspace",
  icon: "GA",
  title: "AI 应用开发工坊",
  summary: "配置模型、工具与 Agent State。",
  detail: "当前开放配置工作台；执行 Session 与审批能力按实际服务状态提供。",
  tag: "Beta",
  eta: "配置面已开放",
  actionLabel: "开发应用",
};

export function platformCapabilityPath(capabilityId: string) {
  return {
    "agent-workspace": "/agents/workbench",
    "conversation-goals": "/agents/goals",
    datax: "/datax",
    "meta-agent": "/agents/meta-agent",
    "xpert-automations": "/agents/automations",
    "xpert-studio": "/agents/studio",
    "expert-squad": "/expert-team",
  }[capabilityId];
}

export default function AgentsPage() {
  const navigate = useNavigate();
  const { hasPreferredModel, preferredModelId } = useModelPreference();
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedDepartment, setSelectedDepartment] = useState("全部");
  const [expandedAgentIds, setExpandedAgentIds] = useState<string[]>([]);
  const [agentWorkspaceEnabled, setAgentWorkspaceEnabled] = useState(false);

  useEffect(() => {
    document.title = "模镜 - Agent人才市场";
    void fetch("/api/agent-workspace/status")
      .then((response) => (response.ok ? response.json() : null))
      .then((status: { enabled?: boolean } | null) =>
        setAgentWorkspaceEnabled(status?.enabled === true),
      )
      .catch(() => setAgentWorkspaceEnabled(false));
  }, []);

  const visiblePlatformCapabilities = useMemo(
    () =>
      agentWorkspaceEnabled
        ? [
            platformCapabilities[0],
            agentWorkspaceCapability,
            ...platformCapabilities.slice(1),
          ]
        : platformCapabilities,
    [agentWorkspaceEnabled],
  );

  const filteredAgents = useMemo(() => {
    const normalizedSearch = searchTerm.trim().toLowerCase();

    return agents.filter((agent) => {
      const matchesDepartment =
        selectedDepartment === "全部" ||
        agent.department === selectedDepartment;
      const matchesSearch =
        normalizedSearch.length === 0 ||
        [
          agent.name,
          agent.department,
          agent.expertise,
          agent.scenarios,
          agent.sourcePath,
        ]
          .join(" ")
          .toLowerCase()
          .includes(normalizedSearch);

      return matchesDepartment && matchesSearch;
    });
  }, [searchTerm, selectedDepartment]);

  function toggleDetails(agentId: string) {
    setExpandedAgentIds((current) =>
      current.includes(agentId)
        ? current.filter((id) => id !== agentId)
        : [...current, agentId],
    );
  }

  function startInterview(agent: AgentProfile) {
    saveAgentInterview(agent);
    const selectedModelId = hasPreferredModel
      ? preferredModelId
      : DEFAULT_CHAT_MODEL_ID;

    if (!hasPreferredModel) {
      window.sessionStorage.setItem(
        AGENT_DEFAULT_MODEL_NOTICE_KEY,
        "您尚未选择模型，已为您使用默认模型 GPT-4o-mini。",
      );
    }

    navigate(buildAgentChatPath(agent, selectedModelId));
  }

  function openPlatformCapability(capability: PlatformCapability) {
    const path = platformCapabilityPath(capability.id);
    if (path) navigate(path);
  }

  return (
    <PageContainer
      activeResource="agents"
      maxWidthClassName="max-w-[1500px]"
      mobileSidebar={<ModelWorkbenchSidebar compact />}
      showSystemCapabilityBar={false}
      sidebar={<ModelWorkbenchSidebar />}
      sidebarGridClassName="xl:grid-cols-[230px_minmax(0,1fr)]"
    >
      <header className="relative border-b border-white/10 pb-6 pt-2 sm:pt-4">
        <div className="pointer-events-none absolute right-3 top-0 hidden h-20 w-40 opacity-40 lg:block">
          <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-cyan-200" />
          <span className="absolute right-14 top-10 h-1 w-1 rounded-full bg-hire-200" />
          <span className="absolute right-28 top-5 h-1 w-1 rounded-full bg-cyan-300" />
          <span className="absolute right-5 top-4 h-px w-28 -rotate-[12deg] bg-cyan-200/35" />
          <span className="absolute right-12 top-9 h-px w-20 rotate-[18deg] bg-hire-200/25" />
        </div>

        <div className="relative flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight text-white sm:text-4xl">
              Agent人才市场
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-300">
              按部门、专长和任务场景筛选开源智能体专家，选择模型后即可开始对话。
            </p>
          </div>
          <p className="relative shrink-0 text-sm text-slate-300">
            <span className="font-semibold text-hire-100">
              {agentDepartments.length}
            </span>{" "}
            个部门 ·{" "}
            <span className="font-semibold text-hire-100">{agents.length}</span>{" "}
            位专家
          </p>
        </div>

        <label className="relative mt-5 block">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-slate-400"
            size={19}
          />
          <span className="sr-only">搜索专家</span>
          <input
            className="h-14 w-full rounded-lg border border-white/10 bg-ink-950/65 pl-12 pr-4 text-sm text-white outline-none transition placeholder:text-slate-500 hover:border-white/20 focus:border-hire-300/60 focus:ring-4 focus:ring-hire-300/10"
            onChange={(event) => setSearchTerm(event.target.value)}
            placeholder="搜索专家姓名、部门、专长或适用场景"
            type="search"
            value={searchTerm}
          />
        </label>
      </header>

      <section className="border-b border-white/10 py-6">
        <div className="mb-3">
          <h2 className="text-xl font-semibold text-white">平台能力</h2>
          <p className="mt-1 text-sm text-slate-400">
            创建、发布与运行你的 AI 应用。
          </p>
        </div>

        <div className="grid gap-4 lg:grid-cols-[minmax(280px,0.95fr)_minmax(0,2fr)]">
          <PlatformCapabilityCard
            capability={featuredPlatformCapability}
            featured
            onOpen={openPlatformCapability}
          />
          <div className="grid gap-3 sm:grid-cols-2">
            {visiblePlatformCapabilities.map((capability) => (
              <PlatformCapabilityCard
                capability={capability}
                key={capability.id}
                onOpen={openPlatformCapability}
              />
            ))}
          </div>
        </div>
      </section>

      <section className="pt-6">
        <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-xl font-semibold text-white">部门招聘牌</h2>
            <p className="mt-1 text-sm text-slate-400">
              按工程、设计、营销、产品等部门快速挑选专家。
            </p>
          </div>
          <button
            className="min-h-11 w-fit rounded-full border border-white/10 bg-white/[0.05] px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/40 hover:bg-hire-300/10 hover:text-hire-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-100"
            onClick={() => {
              setSelectedDepartment("全部");
              setSearchTerm("");
            }}
            type="button"
          >
            清空岗位要求
          </button>
        </div>

        <div className="flex gap-2 overflow-x-auto pb-2">
          {["全部", ...agentDepartments].map((department) => {
            const isActive = selectedDepartment === department;
            const count =
              department === "全部"
                ? agents.length
                : agentDepartmentCounts[department] ?? 0;

            return (
              <button
                className={`min-h-11 shrink-0 rounded-full border px-4 py-2 text-sm font-semibold transition duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-100 ${
                  isActive
                    ? "border-hire-200/50 bg-hire-300 text-ink-950"
                    : "border-white/10 bg-white/[0.05] text-slate-300 hover:border-hire-300/40 hover:bg-hire-300/10 hover:text-hire-100"
                }`}
                key={department}
                onClick={() => setSelectedDepartment(department)}
                type="button"
              >
                {department}
                <span className="ml-2 opacity-70">{count}</span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="mt-6">
        {filteredAgents.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {filteredAgents.map((agent) => (
              <AgentCard
                agent={agent}
                isExpanded={expandedAgentIds.includes(agent.id)}
                key={agent.id}
                onInterview={startInterview}
                onToggleDetails={toggleDetails}
              />
            ))}
          </div>
        ) : (
          <div className="surface-panel rounded-lg px-6 py-16 text-center">
            <img
              alt="模镜 ModelMirror"
              className="mx-auto h-16 w-16 rounded-lg object-cover shadow-neon"
              src="/logo.png"
            />
            <p className="mt-5 text-lg font-semibold text-white">
              人才市场暂时没有符合要求的候选人
            </p>
            <p className="mt-2 text-sm text-slate-400">
              换个部门，或把搜索关键词放宽一点。
            </p>
          </div>
        )}
      </section>

      <footer className="mt-10 border-t border-white/10 py-6 text-sm text-slate-500">
        © 2026 模镜 ModelMirror
      </footer>
    </PageContainer>
  );
}
