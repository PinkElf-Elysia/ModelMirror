import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import { DEFAULT_CHAT_MODEL_ID } from "../context/ModelPreferenceContext";
import { agents, agentDepartments, type AgentProfile } from "../data/agents";
import { models } from "../data/models";
import {
  fetchJsonEventStream,
  type JsonStreamEvent,
} from "../utils/fetchJsonEventStream";

type ExpertDesk = "fusion" | "route" | "team";
type RunStatus = "idle" | "running" | "done" | "error";

interface AgentSummary {
  id: string;
  name: string;
  department: string;
  expertise: string;
  scenarios: string;
  emoji?: string;
  popularity?: number;
  score?: number;
}

interface FusionModelResult {
  modelId: string;
  output: string;
  status: "waiting" | "running" | "done" | "error";
  error?: string;
}

interface TeamSavedConfig {
  id: string;
  name: string;
  members: string[];
}

interface TeamAgentOutput {
  agent: AgentSummary;
  output: string;
  status: "running" | "done" | "error";
  task: string;
}

const savedTeamStorageKey = "modelmirror-expert-teams";
const defaultFusionIds = [
  "openai/gpt-5.6-sol",
  "anthropic/claude-opus-5",
  "anthropic/claude-fable-5",
  "moonshotai/kimi-k3",
  "google/gemini-3.6-flash",
];

function isLikelyChatModel(model: (typeof models)[number]) {
  return (
    model.active &&
    model.input_modalities.includes("text") &&
    model.capabilities.includes("text")
  );
}

function recommendedChatModels() {
  const preferred = defaultFusionIds
    .map((modelId) => models.find((model) => model.id === modelId))
    .filter((model): model is (typeof models)[number] => Boolean(model));
  const seen = new Set(preferred.map((model) => model.id));
  const remaining = models
    .filter((model) => isLikelyChatModel(model) && !seen.has(model.id))
    .slice(0, 180);
  return [...preferred, ...remaining];
}

function eventText(event: JsonStreamEvent, key: string) {
  const value = event[key];
  return typeof value === "string" ? value : "";
}

function isAgentSummary(value: unknown): value is AgentSummary {
  if (!value || typeof value !== "object") return false;
  const record = value as Record<string, unknown>;
  return typeof record.id === "string" && typeof record.name === "string";
}

function agentSummaryFromProfile(agent: AgentProfile): AgentSummary {
  return {
    id: agent.id,
    name: agent.name,
    department: agent.department,
    expertise: agent.expertise,
    scenarios: agent.scenarios,
    emoji: agent.emoji,
    popularity: agent.popularity,
  };
}

function modelLabel(modelId: string) {
  const model = models.find((item) => item.id === modelId);
  return model ? `${model.name} · ${model.id}` : modelId;
}

function readSavedTeams(): TeamSavedConfig[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(savedTeamStorageKey);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (item): item is TeamSavedConfig =>
        Boolean(item) &&
        typeof item === "object" &&
        "id" in item &&
        "name" in item &&
        "members" in item &&
        typeof item.id === "string" &&
        typeof item.name === "string" &&
        Array.isArray(item.members),
    );
  } catch {
    return [];
  }
}

function saveTeams(teams: TeamSavedConfig[]) {
  window.localStorage.setItem(savedTeamStorageKey, JSON.stringify(teams));
}

function FeatureTab({
  active,
  description,
  icon,
  onClick,
  title,
}: {
  active: boolean;
  description: string;
  icon: string;
  onClick: () => void;
  title: string;
}) {
  return (
    <button
      className={`group relative overflow-hidden rounded-lg border p-4 text-left transition duration-200 ${
        active
          ? "border-hire-200/60 bg-hire-300/14 shadow-[0_0_0_1px_rgba(253,186,116,0.18),0_20px_48px_rgba(124,45,18,0.24)]"
          : "border-white/10 bg-white/[0.045] hover:border-hire-300/35 hover:bg-hire-300/10"
      }`}
      onClick={onClick}
      type="button"
    >
      <div className="pointer-events-none absolute inset-x-0 top-0 h-px bg-[linear-gradient(90deg,transparent,rgba(253,186,116,0.72),transparent)] opacity-70" />
      <div className="flex items-start gap-3">
        <span
          className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border text-lg font-black ${
            active
              ? "border-hire-100/40 bg-hire-300 text-ink-950"
              : "border-white/10 bg-white/[0.06] text-hire-100"
          }`}
        >
          {icon}
        </span>
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-white">{title}</span>
          <span className="mt-1 block text-xs leading-5 text-slate-400">
            {description}
          </span>
        </span>
      </div>
    </button>
  );
}

function ModelSelector({
  label,
  onChange,
  value,
}: {
  label: string;
  onChange: (modelId: string) => void;
  value: string;
}) {
  const options = useMemo(
    () => recommendedChatModels(),
    [],
  );

  return (
    <label className="block">
      <span className="text-xs font-semibold text-slate-400">{label}</span>
      <select
        className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-sm text-white outline-none transition hover:border-hire-300/30 focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10"
        onChange={(event) => onChange(event.target.value)}
        value={value}
      >
        {options.map((model) => (
          <option key={model.id} value={model.id}>
            {model.name} · {model.id}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function ExpertTeamPage() {
  const [searchParams] = useSearchParams();
  const textModelIds = useMemo(
    () => recommendedChatModels().map((model) => model.id),
    [],
  );
  const initialFusionIds = useMemo(
    () =>
      defaultFusionIds
        .filter((modelId) => textModelIds.includes(modelId))
        .slice(0, 3),
    [textModelIds],
  );
  const modelPool = useMemo(
    () => recommendedChatModels().slice(0, 48),
    [],
  );

  const [activeDesk, setActiveDesk] = useState<ExpertDesk>(() => {
    const desk = searchParams.get("desk");
    return desk === "route" || desk === "team" || desk === "fusion"
      ? desk
      : "fusion";
  });
  const [judgeModelId, setJudgeModelId] = useState(DEFAULT_CHAT_MODEL_ID);
  const [sharedModelId, setSharedModelId] = useState(DEFAULT_CHAT_MODEL_ID);

  const [fusionQuestion, setFusionQuestion] = useState(
    "请比较低代码工作流和传统定制开发，给出适合中小团队的落地建议。",
  );
  const [fusionModelIds, setFusionModelIds] = useState<string[]>(
    initialFusionIds.length >= 2
      ? initialFusionIds
      : textModelIds.slice(0, 3),
  );
  const [fusionResults, setFusionResults] = useState<FusionModelResult[]>([]);
  const [fusionFinal, setFusionFinal] = useState("");
  const [fusionStatus, setFusionStatus] = useState<RunStatus>("idle");
  const [fusionLog, setFusionLog] = useState<string[]>([]);
  const [useNativeFusion, setUseNativeFusion] = useState(true);

  const [routeMessage, setRouteMessage] = useState(
    "我想做一个 SaaS 产品的首页改版，需要兼顾转化、性能和移动端体验。",
  );
  const [routeMatches, setRouteMatches] = useState<AgentSummary[]>([]);
  const [routeAnswer, setRouteAnswer] = useState("");
  const [routeStatus, setRouteStatus] = useState<RunStatus>("idle");
  const [routeError, setRouteError] = useState("");

  const [teamTask, setTeamTask] = useState(
    "为一个新上线的 AI 模型浏览器制定产品发布方案，包括技术风险、设计亮点和增长打法。",
  );
  const [teamMode, setTeamMode] = useState<"serial" | "debate">("serial");
  const [selectedDepartment, setSelectedDepartment] = useState("全部");
  const [agentSearch, setAgentSearch] = useState("");
  const [selectedAgentIds, setSelectedAgentIds] = useState<string[]>(
    agents.slice(0, 3).map((agent) => agent.id),
  );
  const [agentTasks, setAgentTasks] = useState<Record<string, string>>({});
  const [teamOutputs, setTeamOutputs] = useState<TeamAgentOutput[]>([]);
  const [teamFinal, setTeamFinal] = useState("");
  const [teamStatus, setTeamStatus] = useState<RunStatus>("idle");
  const [savedTeams, setSavedTeams] = useState<TeamSavedConfig[]>(readSavedTeams);
  const [teamName, setTeamName] = useState("产品发布专家组");

  useEffect(() => {
    document.title = "模镜 - 专家团会诊室";
  }, []);

  useEffect(() => {
    const desk = searchParams.get("desk");
    if (desk === "route" || desk === "team" || desk === "fusion") {
      setActiveDesk(desk);
    }
  }, [searchParams]);

  const filteredAgents = useMemo(() => {
    const normalizedSearch = agentSearch.trim().toLowerCase();
    return agents
      .filter((agent) => {
        const matchesDepartment =
          selectedDepartment === "全部" || agent.department === selectedDepartment;
        const matchesSearch =
          !normalizedSearch ||
          [agent.name, agent.department, agent.expertise, agent.scenarios]
            .join(" ")
            .toLowerCase()
            .includes(normalizedSearch);
        return matchesDepartment && matchesSearch;
      })
      .slice(0, 80);
  }, [agentSearch, selectedDepartment]);

  function toggleFusionModel(modelId: string) {
    setFusionModelIds((current) => {
      if (current.includes(modelId)) {
        return current.length <= 2
          ? current
          : current.filter((item) => item !== modelId);
      }
      return current.length >= 5 ? current : [...current, modelId];
    });
  }

  function toggleTeamAgent(agentId: string) {
    setSelectedAgentIds((current) => {
      if (current.includes(agentId)) {
        return current.filter((item) => item !== agentId);
      }
      return current.length >= 6 ? current : [...current, agentId];
    });
  }

  function selectDepartmentAgents(department: string) {
    const departmentIds = agents
      .filter((agent) => agent.department === department)
      .slice(0, 6)
      .map((agent) => agent.id);
    setSelectedAgentIds(departmentIds);
  }

  function saveCurrentTeam() {
    const nextTeam: TeamSavedConfig = {
      id: `team-${Date.now()}`,
      name: teamName.trim() || "未命名专家团",
      members: selectedAgentIds,
    };
    const nextTeams = [nextTeam, ...savedTeams].slice(0, 8);
    setSavedTeams(nextTeams);
    saveTeams(nextTeams);
  }

  function loadTeam(team: TeamSavedConfig) {
    setTeamName(team.name);
    setSelectedAgentIds(team.members.filter((id) => agents.some((agent) => agent.id === id)));
  }

  async function runFusion() {
    if (fusionModelIds.length < 2 || !fusionQuestion.trim()) return;
    setFusionStatus("running");
    setFusionFinal("");
    setFusionLog(["正在咨询多位模型专家..."]);
    setFusionResults(
      fusionModelIds.map((modelId) => ({
        modelId,
        output: "",
        status: "waiting",
      })),
    );

    try {
      await fetchJsonEventStream({
        url: "/api/fusion/chat",
        payload: {
          model_ids: fusionModelIds,
          judge_model_id: judgeModelId,
          use_native_fusion: useNativeFusion,
          messages: [{ role: "user", content: fusionQuestion }],
          temperature: 0.7,
          max_tokens: 2048,
        },
        onEvent: (event) => {
          const eventName = event.event;
          if (eventName === "fusion_stage") {
            const message = eventText(event, "message");
            if (message) setFusionLog((current) => [...current, message]);
          }
          if (eventName === "model_start") {
            const modelId = eventText(event, "model_id");
            setFusionResults((current) =>
              current.map((item) =>
                item.modelId === modelId ? { ...item, status: "running" } : item,
              ),
            );
          }
          if (eventName === "model_end") {
            const modelId = eventText(event, "model_id");
            const output = eventText(event, "output");
            setFusionResults((current) =>
              current.map((item) =>
                item.modelId === modelId
                  ? { ...item, status: "done", output }
                  : item,
              ),
            );
          }
          if (eventName === "model_error") {
            const modelId = eventText(event, "model_id");
            const message = eventText(event, "message");
            setFusionResults((current) =>
              current.map((item) =>
                item.modelId === modelId
                  ? { ...item, status: "error", error: message }
                  : item,
              ),
            );
          }
          if (eventName === "fusion_delta") {
            setFusionFinal((current) => current + eventText(event, "output"));
          }
          if (eventName === "error") {
            throw new Error(eventText(event, "message"));
          }
        },
      });
      setFusionStatus("done");
    } catch (error) {
      setFusionStatus("error");
      setFusionLog((current) => [
        ...current,
        error instanceof Error ? error.message : "模型融合失败。",
      ]);
    }
  }

  async function runRouteAgent() {
    if (!routeMessage.trim()) return;
    setRouteStatus("running");
    setRouteAnswer("");
    setRouteMatches([]);
    setRouteError("");

    try {
      await fetchJsonEventStream({
        url: "/api/route-agent",
        payload: {
          message: routeMessage,
          model_id: sharedModelId,
          top_k: 3,
          temperature: 0.7,
          max_tokens: 2048,
        },
        onEvent: (event) => {
          if (event.event === "route_result" && Array.isArray(event.matches)) {
            setRouteMatches(event.matches.filter(isAgentSummary));
          }
          if (event.event === "answer_delta") {
            setRouteAnswer((current) => current + eventText(event, "output"));
          }
          if (event.event === "error") {
            throw new Error(eventText(event, "message"));
          }
        },
      });
      setRouteStatus("done");
    } catch (error) {
      setRouteStatus("error");
      setRouteError(error instanceof Error ? error.message : "自动路由失败。");
    }
  }

  async function runTeam() {
    if (!teamTask.trim() || selectedAgentIds.length === 0) return;
    setTeamStatus("running");
    setTeamOutputs([]);
    setTeamFinal("");

    try {
      await fetchJsonEventStream({
        url: "/api/team/chat",
        payload: {
          model_id: sharedModelId,
          mode: teamMode,
          message: teamTask,
          max_tokens: 1800,
          temperature: 0.65,
          members: selectedAgentIds.map((agentId) => ({
            agent_id: agentId,
            task: agentTasks[agentId] || "",
          })),
        },
        onEvent: (event) => {
          const eventAgent = event.agent;
          if (event.event === "agent_start" && isAgentSummary(eventAgent)) {
            setTeamOutputs((current) => [
              ...current,
              {
                agent: eventAgent,
                output: "",
                status: "running",
                task: eventText(event, "task"),
              },
            ]);
          }
          if (event.event === "agent_delta") {
            const agentId = eventText(event, "agent_id");
            const output = eventText(event, "output");
            setTeamOutputs((current) =>
              current.map((item) =>
                item.agent.id === agentId
                  ? { ...item, output: item.output + output }
                  : item,
              ),
            );
          }
          if (event.event === "agent_end" && isAgentSummary(eventAgent)) {
            setTeamOutputs((current) =>
              current.map((item) =>
                item.agent.id === eventAgent.id
                  ? { ...item, status: "done", output: eventText(event, "output") }
                  : item,
              ),
            );
          }
          if (event.event === "summary_delta") {
            setTeamFinal((current) => current + eventText(event, "output"));
          }
          if (event.event === "error") {
            throw new Error(eventText(event, "message"));
          }
        },
      });
      setTeamStatus("done");
    } catch (error) {
      setTeamStatus("error");
      setTeamFinal(error instanceof Error ? error.message : "AI Team 协作失败。");
    }
  }

  return (
    <PageContainer
      activeResource="agents"
      sidebar={
        <div>
          <p className="text-sm font-semibold text-white">专家团服务台</p>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            Fusion、自动派工和 AI Team 都在这里开会。当前专家库共 {agents.length} 位。
          </p>
          <div className="mt-4 rounded-lg border border-hire-300/20 bg-hire-300/10 p-3 text-xs leading-5 text-hire-50">
            原生融合为 Beta 通道，系统会自动准备本地裁判兜底。
          </div>
        </div>
      }
    >
      <header className="relative overflow-hidden rounded-lg border border-hire-300/20 bg-[linear-gradient(135deg,rgba(67,20,7,0.72),rgba(6,9,22,0.92)_46%,rgba(8,51,68,0.72))] p-6 shadow-prism sm:p-8">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_0%,rgba(253,186,116,0.22),transparent_32%),radial-gradient(circle_at_84%_80%,rgba(36,217,255,0.18),transparent_36%)]" />
        <div className="relative max-w-4xl">
          <p className="text-sm font-semibold text-hire-100">专家会诊室正式开门</p>
          <h1 className="mt-3 text-4xl font-semibold tracking-normal text-white sm:text-6xl">
            专家团
            <span className="block text-hire-100">Fusion · 自动路由 · AI Team</span>
          </h1>
          <p className="mt-5 max-w-2xl text-sm leading-7 text-slate-300 sm:text-base">
            一个问题交给多位模型候选人会诊；一个需求自动派给最合适的智能体；一个复杂项目交给多专家接力完成。
          </p>
        </div>
      </header>

      <section className="mt-6 grid gap-3 lg:grid-cols-3">
        <FeatureTab
          active={activeDesk === "fusion"}
          description="2-5 个模型并行作答，再由裁判模型整合共识。"
          icon="融"
          onClick={() => setActiveDesk("fusion")}
          title="Fusion 模型融合"
        />
        <FeatureTab
          active={activeDesk === "route"}
          description="输入需求，系统从 215 位专家中自动匹配岗位。"
          icon="派"
          onClick={() => setActiveDesk("route")}
          title="自动路由派工"
        />
        <FeatureTab
          active={activeDesk === "team"}
          description="自由组建项目组，让多位专家串行接力或独立辩论。"
          icon="团"
          onClick={() => setActiveDesk("team")}
          title="AI Team 协作"
        />
      </section>

      {activeDesk === "fusion" ? (
        <section className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          <div className="surface-panel rounded-lg p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-xl font-semibold text-white">模型融合会诊</h2>
                <p className="mt-1 text-sm text-slate-400">
                  选 2-5 位模型候选人，同题作答后综合成一份更稳的结论。
                </p>
              </div>
              <label className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.055] px-3 py-2 text-xs font-semibold text-slate-200">
                <input
                  checked={useNativeFusion}
                  className="h-4 w-4 accent-orange-400"
                  onChange={(event) => setUseNativeFusion(event.target.checked)}
                  type="checkbox"
                />
                优先使用原生融合
              </label>
            </div>

            <textarea
              className="mt-5 min-h-36 w-full rounded-lg border border-white/10 bg-ink-950/76 p-4 text-sm leading-6 text-white outline-none transition placeholder:text-slate-500 focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10"
              onChange={(event) => setFusionQuestion(event.target.value)}
              placeholder="把需要会诊的问题写在这里"
              value={fusionQuestion}
            />

            <div className="mt-5 grid gap-4 md:grid-cols-2">
              <ModelSelector
                label="裁判模型"
                onChange={setJudgeModelId}
                value={judgeModelId}
              />
              <div>
                <p className="text-xs font-semibold text-slate-400">已选模型</p>
                <div className="mt-2 flex min-h-11 flex-wrap gap-2 rounded-lg border border-white/10 bg-white/[0.035] p-2">
                  {fusionModelIds.map((modelId) => (
                    <button
                      className="rounded-full border border-hire-300/30 bg-hire-300/10 px-3 py-1.5 text-xs font-semibold text-hire-100 transition hover:bg-hire-300/20"
                      key={modelId}
                      onClick={() => toggleFusionModel(modelId)}
                      type="button"
                    >
                      {modelId} ×
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="mt-5 max-h-64 overflow-y-auto rounded-lg border border-white/10 bg-white/[0.035] p-3">
              <p className="mb-3 text-xs font-semibold text-slate-400">
                候选模型池（最多 5 位）
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                {modelPool.map((model) => {
                  const checked = fusionModelIds.includes(model.id);
                  return (
                    <button
                      className={`rounded-lg border px-3 py-2 text-left text-xs transition ${
                        checked
                          ? "border-hire-300/55 bg-hire-300/12 text-hire-50"
                          : "border-white/10 bg-white/[0.045] text-slate-300 hover:border-hire-300/30"
                      }`}
                      key={model.id}
                      onClick={() => toggleFusionModel(model.id)}
                      type="button"
                    >
                      <span className="block truncate font-semibold">{model.name}</span>
                      <span className="mt-1 block truncate text-slate-500">
                        {model.id}
                      </span>
                    </button>
                  );
                })}
              </div>
            </div>

            <button
              className="mt-5 w-full rounded-full bg-hire-300 px-5 py-3 text-sm font-semibold text-ink-950 shadow-[0_0_24px_rgba(251,146,60,0.22)] transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={fusionStatus === "running" || fusionModelIds.length < 2}
              onClick={runFusion}
              type="button"
            >
              {fusionStatus === "running" ? "专家团会诊中..." : "开始 Fusion 会诊"}
            </button>
          </div>

          <div className="space-y-4">
            <div className="surface-panel rounded-lg p-5">
              <h3 className="text-lg font-semibold text-white">会诊进度</h3>
              <div className="mt-3 space-y-2 text-sm text-slate-300">
                {fusionLog.length > 0 ? (
                  fusionLog.map((item, index) => (
                    <p
                      className="rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2"
                      key={`${item}-${index}`}
                    >
                      {item}
                    </p>
                  ))
                ) : (
                  <p className="text-slate-500">等待开会。</p>
                )}
              </div>
            </div>

            {fusionResults.length > 0 ? (
              <div className="grid gap-3">
                {fusionResults.map((result) => (
                  <article className="surface-card rounded-lg p-4" key={result.modelId}>
                    <div className="flex items-center justify-between gap-3">
                      <h4 className="truncate text-sm font-semibold text-white">
                        {modelLabel(result.modelId)}
                      </h4>
                      <span className="rounded-full border border-white/10 bg-white/[0.055] px-2.5 py-1 text-[11px] text-slate-300">
                        {result.status === "done"
                          ? "已答复"
                          : result.status === "running"
                            ? "答复中"
                            : result.status === "error"
                              ? "异常"
                              : "排队"}
                      </span>
                    </div>
                    <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-300">
                      {result.error || result.output || "暂无输出"}
                    </p>
                  </article>
                ))}
              </div>
            ) : null}

            <div className="surface-panel rounded-lg p-5">
              <h3 className="text-lg font-semibold text-hire-100">专家团综合意见</h3>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-200">
                {fusionFinal || "Fusion 完成后，综合意见会出现在这里。"}
              </p>
            </div>
          </div>
        </section>
      ) : null}

      {activeDesk === "route" ? (
        <section className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <div className="surface-panel rounded-lg p-5">
            <h2 className="text-xl font-semibold text-white">自动路由派工</h2>
            <p className="mt-1 text-sm text-slate-400">
              输入需求，系统按名称、部门、专长和场景从 215 位专家里匹配最合适的人。
            </p>
            <div className="mt-5">
              <ModelSelector
                label="执行模型"
                onChange={setSharedModelId}
                value={sharedModelId}
              />
            </div>
            <textarea
              className="mt-5 min-h-44 w-full rounded-lg border border-white/10 bg-ink-950/76 p-4 text-sm leading-6 text-white outline-none transition placeholder:text-slate-500 focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10"
              onChange={(event) => setRouteMessage(event.target.value)}
              placeholder="描述你要完成的任务"
              value={routeMessage}
            />
            <button
              className="mt-5 w-full rounded-full bg-hire-300 px-5 py-3 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={routeStatus === "running" || !routeMessage.trim()}
              onClick={runRouteAgent}
              type="button"
            >
              {routeStatus === "running" ? "正在自动派工..." : "自动匹配专家并作答"}
            </button>
          </div>

          <div className="space-y-4">
            <div className="surface-panel rounded-lg p-5">
              <h3 className="text-lg font-semibold text-white">匹配到的专家</h3>
              <div className="mt-3 grid gap-3 md:grid-cols-3">
                {routeMatches.length > 0 ? (
                  routeMatches.map((agent, index) => (
                    <article
                      className={`rounded-lg border p-3 ${
                        index === 0
                          ? "border-hire-300/45 bg-hire-300/10"
                          : "border-white/10 bg-white/[0.045]"
                      }`}
                      key={agent.id}
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-lg">{agent.emoji || "专"}</span>
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-white">
                            {agent.name}
                          </p>
                          <p className="text-xs text-slate-400">
                            {agent.department} · 匹配 {agent.score ?? "-"}
                          </p>
                        </div>
                      </div>
                      <p className="mt-2 line-clamp-3 text-xs leading-5 text-slate-400">
                        {agent.expertise}
                      </p>
                    </article>
                  ))
                ) : (
                  <p className="text-sm text-slate-500">尚未派工。</p>
                )}
              </div>
            </div>

            <div className="surface-panel rounded-lg p-5">
              <h3 className="text-lg font-semibold text-hire-100">专家回复</h3>
              {routeError ? (
                <p className="mt-3 rounded-lg border border-red-300/20 bg-red-300/10 p-3 text-sm text-red-100">
                  {routeError}
                </p>
              ) : null}
              <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-200">
                {routeAnswer || "自动路由完成后，专家会在这里回复。"}
              </p>
            </div>
          </div>
        </section>
      ) : null}

      {activeDesk === "team" ? (
        <section className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          <div className="surface-panel rounded-lg p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-xl font-semibold text-white">组建 AI Team</h2>
                <p className="mt-1 text-sm text-slate-400">
                  最多选择 6 位专家，串行接力或独立辩论后由项目经理汇总。
                </p>
              </div>
              <div className="flex rounded-full border border-white/10 bg-white/[0.045] p-1">
                {(["serial", "debate"] as const).map((mode) => (
                  <button
                    className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                      teamMode === mode
                        ? "bg-hire-300 text-ink-950"
                        : "text-slate-300 hover:text-white"
                    }`}
                    key={mode}
                    onClick={() => setTeamMode(mode)}
                    type="button"
                  >
                    {mode === "serial" ? "串行接力" : "独立辩论"}
                  </button>
                ))}
              </div>
            </div>

            <div className="mt-5 grid gap-4 md:grid-cols-2">
              <ModelSelector
                label="团队执行模型"
                onChange={setSharedModelId}
                value={sharedModelId}
              />
              <label className="block">
                <span className="text-xs font-semibold text-slate-400">团队名称</span>
                <input
                  className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-sm text-white outline-none focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10"
                  onChange={(event) => setTeamName(event.target.value)}
                  value={teamName}
                />
              </label>
            </div>

            <textarea
              className="mt-5 min-h-32 w-full rounded-lg border border-white/10 bg-ink-950/76 p-4 text-sm leading-6 text-white outline-none transition placeholder:text-slate-500 focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10"
              onChange={(event) => setTeamTask(event.target.value)}
              placeholder="把团队任务交代清楚"
              value={teamTask}
            />

            <div className="mt-5 grid gap-3 lg:grid-cols-[160px_minmax(0,1fr)]">
              <div className="space-y-2">
                <button
                  className={`w-full rounded-lg border px-3 py-2 text-left text-xs font-semibold transition ${
                    selectedDepartment === "全部"
                      ? "border-hire-300/45 bg-hire-300/10 text-hire-100"
                      : "border-white/10 bg-white/[0.045] text-slate-300"
                  }`}
                  onClick={() => setSelectedDepartment("全部")}
                  type="button"
                >
                  全部部门
                </button>
                {agentDepartments.slice(0, 12).map((department) => (
                  <button
                    className={`w-full rounded-lg border px-3 py-2 text-left text-xs font-semibold transition ${
                      selectedDepartment === department
                        ? "border-hire-300/45 bg-hire-300/10 text-hire-100"
                        : "border-white/10 bg-white/[0.045] text-slate-300 hover:border-hire-300/30"
                    }`}
                    key={department}
                    onClick={() => setSelectedDepartment(department)}
                    onDoubleClick={() => selectDepartmentAgents(department)}
                    type="button"
                  >
                    {department}
                  </button>
                ))}
              </div>

              <div className="min-w-0">
                <input
                  className="h-11 w-full rounded-full border border-white/10 bg-ink-950/80 px-4 text-sm text-white outline-none focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10"
                  onChange={(event) => setAgentSearch(event.target.value)}
                  placeholder="搜索专家姓名或专长"
                  value={agentSearch}
                />
                <div className="mt-3 max-h-72 overflow-y-auto rounded-lg border border-white/10 bg-white/[0.035] p-3">
                  <div className="grid gap-2 md:grid-cols-2">
                    {filteredAgents.map((agent) => {
                      const selected = selectedAgentIds.includes(agent.id);
                      return (
                        <button
                          className={`rounded-lg border p-3 text-left transition ${
                            selected
                              ? "border-hire-300/55 bg-hire-300/12"
                              : "border-white/10 bg-white/[0.045] hover:border-hire-300/30"
                          }`}
                          key={agent.id}
                          onClick={() => toggleTeamAgent(agent.id)}
                          type="button"
                        >
                          <div className="flex items-center gap-2">
                            <span>{agent.emoji}</span>
                            <span className="min-w-0">
                              <span className="block truncate text-sm font-semibold text-white">
                                {agent.name}
                              </span>
                              <span className="text-xs text-slate-500">
                                {agent.department}
                              </span>
                            </span>
                          </div>
                          <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-400">
                            {agent.expertise}
                          </p>
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
            </div>

            <div className="mt-5 rounded-lg border border-white/10 bg-white/[0.045] p-3">
              <p className="text-xs font-semibold text-slate-400">
                已选专家（可填写个人任务）
              </p>
              <div className="mt-3 space-y-2">
                {selectedAgentIds.map((agentId) => {
                  const agent = agents.find((item) => item.id === agentId);
                  if (!agent) return null;
                  return (
                    <div className="grid gap-2 md:grid-cols-[160px_minmax(0,1fr)]" key={agent.id}>
                      <p className="rounded-lg border border-hire-300/30 bg-hire-300/10 px-3 py-2 text-sm font-semibold text-hire-100">
                        {agent.emoji} {agent.name}
                      </p>
                      <input
                        className="rounded-lg border border-white/10 bg-ink-950/70 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/70"
                        onChange={(event) =>
                          setAgentTasks((current) => ({
                            ...current,
                            [agent.id]: event.target.value,
                          }))
                        }
                        placeholder="可选：给 TA 分配本轮任务"
                        value={agentTasks[agent.id] || ""}
                      />
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="mt-5 flex flex-wrap gap-2">
              <button
                className="rounded-full bg-hire-300 px-5 py-3 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-50"
                disabled={teamStatus === "running" || selectedAgentIds.length === 0}
                onClick={runTeam}
                type="button"
              >
                {teamStatus === "running" ? "专家组协作中..." : "启动 AI Team"}
              </button>
              <button
                className="rounded-full border border-white/10 bg-white/[0.06] px-5 py-3 text-sm font-semibold text-slate-100 transition hover:border-hire-300/35 hover:text-hire-100"
                onClick={saveCurrentTeam}
                type="button"
              >
                保存团队
              </button>
            </div>

            {savedTeams.length > 0 ? (
              <div className="mt-4 flex flex-wrap gap-2">
                {savedTeams.map((team) => (
                  <button
                    className="rounded-full border border-white/10 bg-white/[0.045] px-3 py-1.5 text-xs text-slate-300 transition hover:border-hire-300/35 hover:text-hire-100"
                    key={team.id}
                    onClick={() => loadTeam(team)}
                    type="button"
                  >
                    载入：{team.name}
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          <div className="space-y-4">
            {teamOutputs.length > 0 ? (
              teamOutputs.map((step) => (
                <article className="surface-card rounded-lg p-4" key={step.agent.id}>
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-3">
                      <span className="flex h-10 w-10 items-center justify-center rounded-lg border border-hire-300/30 bg-hire-300/10 text-lg">
                        {step.agent.emoji || "专"}
                      </span>
                      <div className="min-w-0">
                        <h3 className="truncate text-sm font-semibold text-white">
                          {step.agent.name}
                        </h3>
                        <p className="text-xs text-slate-400">
                          {step.agent.department} · {step.status === "done" ? "已交棒" : "发言中"}
                        </p>
                      </div>
                    </div>
                    <span className="rounded-full border border-white/10 bg-white/[0.055] px-2.5 py-1 text-[11px] text-slate-300">
                      {teamMode === "serial" ? "接力" : "辩论"}
                    </span>
                  </div>
                  <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-300">
                    {step.output || "正在输出..."}
                  </p>
                </article>
              ))
            ) : (
              <div className="surface-panel rounded-lg p-8 text-center text-sm text-slate-400">
                AI Team 启动后，每位专家的接力过程会显示在这里。
              </div>
            )}

            <div className="surface-panel rounded-lg p-5">
              <h3 className="text-lg font-semibold text-hire-100">团队综合意见</h3>
              <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-slate-200">
                {teamFinal || "项目经理汇总会出现在这里。"}
              </p>
            </div>
          </div>
        </section>
      ) : null}
    </PageContainer>
  );
}
