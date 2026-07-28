import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import AgentCard from "../components/AgentCard";
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

const platformCapabilities: PlatformCapability[] = [
  {
    id: "xpert-automations",
    icon: "AT",
    title: "Xpert 自动化",
    summary: "按单次、间隔或 Cron 调度已发布 Xpert，并在失败后重试或进入死信。",
    detail:
      "每条自动化固定 Xpert 发布版本，支持预算、并发与重叠策略；审批和客户端等待可以在后台恢复。",
    tag: "后台执行 · Beta",
    eta: "已开放自动化工作台",
  },
  {
    id: "conversation-goals",
    icon: "GL",
    title: "长期 Goal",
    summary: "把对话目标拆成可审核的依赖计划，并由已发布 Xpert 持续协作执行。",
    detail:
      "选择 Planner Xpert 自动生成计划，人工确认步骤和依赖后启动。支持暂停、恢复、失败重试、改派与最终结果汇总。",
    tag: "长期任务 · Beta",
    eta: "已开放规划、执行与恢复工作台",
  },
  {
    id: "xpert-studio",
    icon: "XP",
    title: "Xpert Studio",
    summary: "创建、版本化发布并直接运行可组合的智能体应用。",
    detail:
      "复用经典工作流内核，把模型、Toolset、知识、中间件与 Handoff 组合成不可变发布版本。",
    tag: "可发布智能体 · Beta",
    eta: "已开放草稿、发布与聊天运行",
  },
  {
    id: "workflow-builder",
    icon: "流",
    title: "自定义工作流",
    summary:
      "像搭积木一样创建你的 AI 工作流，拖拽、连线、一键运行。",
    detail:
      "未来将支持定时触发、条件分支、多模型协作和人工确认节点。现在先作为招聘会里的低代码展位预告，不会影响现有智能体面试功能。",
    tag: "低代码 · 即将开放",
    eta: "预计 2025 年下半年开放内测",
  },
  {
    id: "meta-agent",
    icon: "元",
    title: "元智能体",
    summary:
      "用自然语言生成可编辑 Agent 工作流，并直接接入模镜经典画布试运行。",
    detail:
      "输入目标后，元智能体会拆解子任务、推断变量依赖、生成原生 React Flow 工作流，并通过现有 /api/workflow/run 执行。",
    tag: "自然语言驱动 · Beta",
    eta: "已接入生成与运行工作台",
  },
  {
    id: "expert-squad",
    icon: "团",
    title: "专家团",
    summary:
      "多个 AI 专家角色组成部门，支持模型融合、自动派工和团队协作。",
    detail:
      "例如“产品发布专家组”可包含产品经理、设计师、开发、测试和运营。现在可以进入专家会诊室，体验 Fusion 模型融合、自动路由和 AI Team 接力。",
    tag: "多智能体协作 · 已开放",
    eta: "专家会诊室已上线",
  },
];

export default function AgentsPage() {
  const navigate = useNavigate();
  const { hasPreferredModel, preferredModelId } = useModelPreference();
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedDepartment, setSelectedDepartment] = useState("全部");
  const [expandedAgentIds, setExpandedAgentIds] = useState<string[]>([]);
  const [selectedCapability, setSelectedCapability] =
    useState<PlatformCapability | null>(null);

  useEffect(() => {
    document.title = "模镜 - AI 人才市场";
  }, []);

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
    if (capability.id === "xpert-automations") {
      navigate("/agents/automations");
      return;
    }
    if (capability.id === "conversation-goals") {
      navigate("/agents/goals");
      return;
    }
    if (capability.id === "xpert-studio") {
      navigate("/agents/studio");
      return;
    }
    if (capability.id === "workflow-builder") {
      navigate("/workflow/new");
      return;
    }
    if (capability.id === "meta-agent") {
      navigate("/agents/meta-agent");
      return;
    }
    if (capability.id === "expert-squad") {
      navigate("/expert-team");
      return;
    }

    setSelectedCapability(capability);
  }

  return (
    <PageContainer
      activeResource="agents"
      sidebar={
        <div>
          <p className="text-sm font-semibold text-white">资源分区</p>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            AI 人才市场收录 {agents.length} 位带完整岗位人设的智能体专家。
          </p>
          <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.045] p-3">
            <p className="text-xs text-slate-400">当前可面试</p>
            <p className="mt-1 text-sm font-semibold text-hire-100">
              {filteredAgents.length} / {agents.length}
            </p>
          </div>
        </div>
      }
    >
        <header className="relative overflow-hidden border-y border-hire-300/20 py-8 sm:py-10 lg:py-12">
          <div className="absolute inset-x-6 top-0 h-16 rounded-b-[50%] border-x border-b border-hire-300/30 bg-[linear-gradient(180deg,rgba(251,146,60,0.18),transparent)]" />
          <div className="absolute left-0 top-0 h-px w-full animate-pulse-line bg-[linear-gradient(90deg,transparent,rgba(251,146,60,0.82),rgba(253,186,116,0.72),transparent)]" />

          <div className="grid min-w-0 gap-8 lg:grid-cols-[minmax(0,1fr)_360px] lg:items-end">
            <div className="min-w-0">
              <div className="max-w-4xl">
                <p className="text-sm font-semibold text-hire-200">
                  {agents.length} 位 AI 专家现场递简历
                </p>
                <h1 className="mt-3 max-w-4xl text-4xl font-semibold tracking-normal text-white sm:text-6xl">
                  AI 人才市场
                  <span className="block text-hire-100">开源专家等你来招</span>
                </h1>
                <p className="mt-5 max-w-2xl text-base leading-7 text-slate-300">
                  来源于 agency-agents-zh 的最新中文智能体快照，按部门、专长和任务场景筛选。看中哪位专家，就带进面试间直接开聊。
                </p>
              </div>
            </div>

            <div className="surface-card min-w-0 overflow-hidden rounded-lg p-4">
              <div className="flex items-center justify-between border-b border-white/10 pb-4">
                <span className="text-sm text-slate-400">人才市场规模</span>
                <span className="text-2xl font-semibold text-white">
                  {agents.length}
                </span>
              </div>
              <div className="mt-4 grid grid-cols-[repeat(3,minmax(0,1fr))] gap-2 text-center text-xs">
                <div className="rounded-lg bg-white/[0.055] px-2 py-3">
                  <p className="text-lg font-semibold text-hire-100">
                    {agentDepartments.length}
                  </p>
                  <p className="mt-1 truncate text-slate-400">部门</p>
                </div>
                <div className="rounded-lg bg-white/[0.055] px-2 py-3">
                  <p className="text-lg font-semibold text-brand-100">
                    {filteredAgents.length}
                  </p>
                  <p className="mt-1 truncate text-slate-400">可面试</p>
                </div>
                <div className="rounded-lg bg-white/[0.055] px-2 py-3">
                  <p className="text-lg font-semibold text-emerald-100">0</p>
                  <p className="mt-1 truncate text-slate-400">招聘费</p>
                </div>
              </div>
            </div>
          </div>

          <div className="mt-8 grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
            <label className="group relative block">
              <span className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-sm font-medium text-slate-400 transition group-focus-within:text-hire-100">
                搜索
              </span>
              <input
                className="h-14 w-full rounded-full border border-white/10 bg-ink-950/70 pl-20 pr-5 text-sm text-white outline-none shadow-dock backdrop-blur-xl transition duration-200 placeholder:text-slate-500 hover:border-white/20 focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10"
                onChange={(event) => setSearchTerm(event.target.value)}
                placeholder="搜索专家姓名、部门、专长或适用场景"
                type="search"
                value={searchTerm}
              />
            </label>

            <p className="rounded-full border border-white/10 bg-white/[0.055] px-4 py-3 text-sm text-slate-300">
              当前{" "}
              <span className="font-semibold text-white">
                {filteredAgents.length}
              </span>{" "}
              位专家可约面试
            </p>
          </div>
        </header>

        <section className="mt-6">
          <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">特色能力专区</h2>
              <p className="mt-1 text-sm text-slate-400">
                这些不是单个专家，而是未来的人才编排和工作流展位。
              </p>
            </div>
            <span className="w-fit rounded-full border border-hire-300/30 bg-hire-300/10 px-3 py-1.5 text-xs font-semibold text-hire-100">
              3 个平台能力排队入场
            </span>
          </div>
          <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-4">
            {platformCapabilities.map((capability) => (
              <PlatformCapabilityCard
                capability={capability}
                key={capability.id}
                onOpen={openPlatformCapability}
              />
            ))}
          </div>
        </section>

        <section className="mt-6">
          <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">部门招聘牌</h2>
              <p className="mt-1 text-sm text-slate-400">
                按工程、设计、营销、产品等部门快速挑专家。
              </p>
            </div>
            <button
              className="w-fit rounded-full border border-white/10 bg-white/[0.06] px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/40 hover:bg-hire-300/10 hover:text-hire-100"
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
                  className={`shrink-0 rounded-full border px-4 py-2 text-sm font-semibold transition duration-200 ${
                    isActive
                      ? "border-hire-200/50 bg-hire-300 text-ink-950 shadow-[0_0_22px_rgba(251,146,60,0.18)]"
                      : "border-white/10 bg-white/[0.055] text-slate-300 hover:border-hire-300/40 hover:bg-hire-300/10 hover:text-hire-100"
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

        <section className="mt-8">
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

        {selectedCapability ? (
          <div
            aria-modal="true"
            className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/78 p-4 backdrop-blur-sm"
            role="dialog"
          >
            <div className="surface-card w-full max-w-lg rounded-lg p-6">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <span className="inline-flex h-12 w-12 items-center justify-center rounded-lg border border-hire-300/35 bg-hire-300/10 text-lg font-bold text-hire-100">
                    {selectedCapability.icon}
                  </span>
                  <h2 className="mt-4 text-2xl font-semibold text-white">
                    {selectedCapability.title}
                  </h2>
                </div>
                <button
                  aria-label="关闭"
                  className="rounded-full border border-white/10 bg-white/[0.06] px-3 py-1.5 text-sm font-semibold text-slate-200 transition hover:bg-white/10"
                  onClick={() => setSelectedCapability(null)}
                  type="button"
                >
                  关闭
                </button>
              </div>
              <p className="mt-4 text-sm leading-6 text-slate-300">
                {selectedCapability.detail}
              </p>
              <div className="mt-5 rounded-lg border border-white/10 bg-white/[0.045] p-3">
                <p className="text-xs text-slate-400">预计上线时间</p>
                <p className="mt-1 text-sm font-semibold text-hire-100">
                  {selectedCapability.eta}
                </p>
              </div>
            </div>
          </div>
        ) : null}
    </PageContainer>
  );
}
