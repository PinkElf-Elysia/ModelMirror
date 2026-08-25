import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import AgencyDagRunPanel from "../components/AgencyDagRunPanel";
import ProviderRouteReceiptSummary from "../components/ProviderRouteReceiptSummary";
import {
  type AgencyAgentSummary,
  type AgencyDagRevisionPayload,
  type AgencyPlanPreview,
  type AgencyPlanTask,
  type AgencyPlannerCapabilities,
  type AgencyTeamAsset,
  type AgencyValidationIssue,
  type ProviderRouteCallReceipt,
  type ProviderRouteReceipt,
} from "../components/AgencyExpertTeamTypes";
import { useAgencyAssets } from "../components/useAgencyAssets";
import { useAgencyDagRun } from "../components/useAgencyDagRun";
import {
  readAgencyPlanDraft,
  writeAgencyPlanDraft,
} from "../components/agencyPlanDraftStorage";
import { syncWorkflowToPlan } from "../components/agencyWorkflowPlanSync";
import { DEFAULT_CHAT_MODEL_ID } from "../context/ModelPreferenceContext";
import { agents, agentDepartments, type AgentProfile } from "../data/agents";
import { models, USD_TO_CNY } from "../data/models";
import {
  fetchJsonEventStream,
  type JsonStreamEvent,
} from "../utils/fetchJsonEventStream";
import { tokenPricingForPrompt } from "../utils/tokenPricing";

type ExpertDesk = "fusion" | "route" | "team";
type RunStatus = "idle" | "running" | "done" | "error";
type RoutePlannerMode = "quick" | "agency";

interface GroupedAgencyValidationIssue extends AgencyValidationIssue {
  count: number;
}

function groupAgencyValidationIssues(
  issues: AgencyValidationIssue[] = [],
): GroupedAgencyValidationIssue[] {
  const grouped = new Map<string, GroupedAgencyValidationIssue>();
  issues.forEach((issue) => {
    const message = issue.message || issue.code || "工作流校验失败";
    const key = `${issue.severity || "error"}\u0000${message}`;
    const current = grouped.get(key);
    if (current) {
      current.count += 1;
      return;
    }
    grouped.set(key, { ...issue, message, count: 1 });
  });
  return [...grouped.values()];
}

export function validateAgencyHitlPlan(tasks: AgencyPlanTask[]): AgencyValidationIssue[] {
  const interactions = tasks.filter(
    (task) => task.task_type === "human_input" || task.task_type === "approval",
  );
  const issues: AgencyValidationIssue[] = [];
  if (tasks.length > 6) {
    issues.push({ code: "agency_hitl_max_steps", message: "计划最多包含 6 个节点。" });
  }
  if (interactions.length > 2) {
    issues.push({ code: "agency_hitl_max_interactions", message: "人工交互节点最多 2 个。" });
  }
  const taskIds = new Set(tasks.map((task) => task.task_id));
  const downstream = new Map<string, string[]>();
  tasks.forEach((task) => downstream.set(task.task_id, []));
  tasks.forEach((task) => task.depends_on.forEach((dependency) => {
    if (taskIds.has(dependency)) downstream.get(dependency)?.push(task.task_id);
  }));
  const reaches = (source: string, target: string) => {
    const pending = [...(downstream.get(source) || [])];
    const seen = new Set<string>();
    while (pending.length) {
      const current = pending.pop()!;
      if (current === target) return true;
      if (seen.has(current)) continue;
      seen.add(current);
      pending.push(...(downstream.get(current) || []));
    }
    return false;
  };
  interactions.forEach((interaction) => {
    if (!interaction.interaction_prompt?.trim()) {
      issues.push({
        code: "agency_hitl_prompt_required",
        message: `交互节点 ${interaction.task_id} 缺少提示语。`,
        node_id: interaction.task_id,
      });
    }
    if (
      interaction.agent_id
      || interaction.method_skill_ids?.length
      || interaction.acceptance.trim()
      || !interaction.output_variable
    ) {
      issues.push({
        code: "agency_hitl_fields_invalid",
        message: `交互节点 ${interaction.task_id} 不能绑定专家、Skill 或验收标准，并且必须定义输出变量。`,
        node_id: interaction.task_id,
      });
    }
    tasks.forEach((task) => {
      if (task.task_id === interaction.task_id) return;
      if (!reaches(interaction.task_id, task.task_id) && !reaches(task.task_id, interaction.task_id)) {
        issues.push({
          code: "agency_hitl_barrier_required",
          message: `交互节点 ${interaction.task_id} 必须是完整 DAG 屏障，不能与 ${task.task_id} 并行。`,
          node_id: interaction.task_id,
        });
      }
    });
  });
  const dependedOn = new Set(tasks.flatMap((task) => task.depends_on));
  const sinks = tasks.filter((task) => !dependedOn.has(task.task_id));
  if (sinks.length !== 1 || (sinks[0]?.task_type || "expert") !== "expert") {
    issues.push({
      code: "agency_hitl_sink_invalid",
      message: "计划必须只有一个最终汇点，且最终汇点必须是专家任务。",
    });
  } else if (!sinks[0].acceptance.trim()) {
    issues.push({
      code: "agency_hitl_sink_acceptance_required",
      message: "最终汇点专家任务必须设置验收标准。",
      node_id: sinks[0].task_id,
    });
  }
  return issues;
}

type AgentSummary = AgencyAgentSummary;

interface FusionModelResult {
  modelId: string;
  output: string;
  status: "waiting" | "running" | "done" | "error";
  error?: string;
}

export const FUSION_ROUTING_BOUNDARY_COPY =
  "原生 Fusion 为 Beta 通道；备用路径仅由当前控制面策略明确决定。";

export function fusionReceiptFromEvent(
  event: JsonStreamEvent,
): ProviderRouteReceipt | null {
  const receipt = event.provider_route_receipts;
  if (
    !receipt ||
    typeof receipt !== "object" ||
    !("contract_version" in receipt) ||
    receipt.contract_version !== "modelmirror-provider-workload-routing-v1" ||
    !("entry_id" in receipt) ||
    receipt.entry_id !== "fusion" ||
    !("routing_mode" in receipt) ||
    receipt.routing_mode !== "managed_required" ||
    !("run_reference" in receipt) ||
    typeof receipt.run_reference !== "string" ||
    !("status" in receipt) ||
    !["running", "passed", "failed", "uncertain", "cancelled"].includes(
      String(receipt.status),
    ) ||
    !("call_count" in receipt) ||
    typeof receipt.call_count !== "number" ||
    !("reason_codes" in receipt) ||
    !Array.isArray(receipt.reason_codes) ||
    !("calls" in receipt) ||
    !Array.isArray(receipt.calls)
  ) {
    return null;
  }
  const calls: ProviderRouteCallReceipt[] = [];
  for (const rawCall of receipt.calls) {
    if (
      !rawCall ||
      typeof rawCall !== "object" ||
      !("call_sequence" in rawCall) ||
      typeof rawCall.call_sequence !== "number" ||
      !("model_id" in rawCall) ||
      typeof rawCall.model_id !== "string" ||
      !("dispatched" in rawCall) ||
      typeof rawCall.dispatched !== "boolean" ||
      !("status" in rawCall) ||
      !["running", "passed", "failed", "uncertain", "cancelled"].includes(
        String(rawCall.status),
      )
    ) {
      return null;
    }
    calls.push({
      call_sequence: rawCall.call_sequence,
      model_id: rawCall.model_id,
      actual_model:
        "actual_model" in rawCall && typeof rawCall.actual_model === "string"
          ? rawCall.actual_model
          : null,
      dispatched: rawCall.dispatched,
      status: rawCall.status as ProviderRouteCallReceipt["status"],
      error_code:
        "error_code" in rawCall && typeof rawCall.error_code === "string"
          ? rawCall.error_code
          : null,
      prompt_tokens:
        "prompt_tokens" in rawCall && typeof rawCall.prompt_tokens === "number"
          ? rawCall.prompt_tokens
          : null,
      completion_tokens:
        "completion_tokens" in rawCall &&
        typeof rawCall.completion_tokens === "number"
          ? rawCall.completion_tokens
          : null,
      total_tokens:
        "total_tokens" in rawCall && typeof rawCall.total_tokens === "number"
          ? rawCall.total_tokens
          : null,
    });
  }
  return {
    contract_version: "modelmirror-provider-workload-routing-v1",
    entry_id: "fusion",
    routing_mode: "managed_required",
    run_reference: receipt.run_reference,
    status: receipt.status as ProviderRouteReceipt["status"],
    call_count: receipt.call_count,
    reason_codes: receipt.reason_codes.filter(
      (item): item is string => typeof item === "string",
    ),
    calls,
  };
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

interface KnowledgeBaseSummary {
  id: string;
  name: string;
  document_count: number;
}

interface AgencyKnowledgeSource {
  chunk_id: string;
  document_id: string;
  document_name: string;
  score: number;
  page_number?: number | null;
  slide?: number | null;
  sheet?: string | null;
  row_range?: string | null;
}

interface AgencyKnowledgeContext {
  knowledge_base: { id: string; name: string };
  version_id?: string | null;
  sources: AgencyKnowledgeSource[];
}

type AgencyPlanPreviewWithKnowledge = AgencyPlanPreview & {
  knowledge_context?: AgencyKnowledgeContext | null;
};

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
    model.output_modalities.includes("text") &&
    model.operations.includes("chat")
  );
}

export function recommendedChatModels() {
  const preferred = defaultFusionIds
    .map((modelId) => models.find((model) => model.id === modelId))
    .filter((model): model is (typeof models)[number] => Boolean(model));
  const seen = new Set(preferred.map((model) => model.id));
  const remaining = models
    .filter((model) => isLikelyChatModel(model) && !seen.has(model.id));
  return [...preferred, ...remaining];
}

export function searchableFusionModels(
  selectedModelIds: string[],
  query: string,
  limit = 48,
) {
  const allModels = recommendedChatModels();
  const selected = new Set(selectedModelIds);
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const matches = normalizedQuery
    ? allModels.filter((model) =>
        `${model.name}\n${model.id}`.toLocaleLowerCase().includes(normalizedQuery),
      )
    : allModels;
  return [
    ...allModels.filter((model) => selected.has(model.id)),
    ...matches.filter((model) => !selected.has(model.id)),
  ].slice(0, Math.max(selected.size, limit));
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

function selectedAgentIdsFromAgencyPlan(preview: AgencyPlanPreview) {
  return preview.selected_agents
    .map((agent) => agent.id)
    .filter((id) => agents.some((agent) => agent.id === id))
    .slice(0, 6);
}

function agentTasksFromAgencyPlan(
  preview: AgencyPlanPreview,
  selectedIds: string[],
) {
  const tasksByAgent: Record<string, string[]> = {};
  preview.plan.tasks.forEach((task) => {
    if (!task.agent_id || !selectedIds.includes(task.agent_id)) return;
    const details = [
      task.objective,
      task.depends_on.length > 0
        ? `依赖：${task.depends_on.join("、")}`
        : "",
      task.acceptance ? `验收：${task.acceptance}` : "",
      task.method_skill_ids?.length
        ? `方法 Skill：${task.method_skill_ids.join("、")}`
        : "",
    ]
      .filter(Boolean)
      .join("\n");
    tasksByAgent[task.agent_id] = [
      ...(tasksByAgent[task.agent_id] || []),
      details,
    ];
  });
  return Object.fromEntries(
    Object.entries(tasksByAgent).map(([agentId, tasks]) => [
      agentId,
      tasks.join("\n\n"),
    ]),
  );
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

async function responseJson<T>(response: Response): Promise<T> {
  const payload = (await response.json()) as T & { error?: string };
  if (!response.ok) {
    throw new Error(payload.error || `请求失败（${response.status}）`);
  }
  return payload;
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
  const [initialAgencyDraft] = useState(readAgencyPlanDraft);
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
  const [activeDesk, setActiveDesk] = useState<ExpertDesk>(() => {
    const desk = searchParams.get("desk");
    return desk === "route" || desk === "team" || desk === "fusion"
      ? desk
      : initialAgencyDraft?.loaded_plan
        ? "team"
        : "fusion";
  });
  const [judgeModelId, setJudgeModelId] = useState(DEFAULT_CHAT_MODEL_ID);
  const [sharedModelId, setSharedModelId] = useState(
    initialAgencyDraft?.execution_model_id || DEFAULT_CHAT_MODEL_ID,
  );

  const [fusionQuestion, setFusionQuestion] = useState(
    "请比较低代码工作流和传统定制开发，给出适合中小团队的落地建议。",
  );
  const [fusionModelIds, setFusionModelIds] = useState<string[]>(
    initialFusionIds.length >= 2
      ? initialFusionIds
      : textModelIds.slice(0, 3),
  );
  const [fusionModelSearch, setFusionModelSearch] = useState("");
  const modelPool = useMemo(
    () => searchableFusionModels(fusionModelIds, fusionModelSearch),
    [fusionModelIds, fusionModelSearch],
  );
  const [fusionResults, setFusionResults] = useState<FusionModelResult[]>([]);
  const [fusionFinal, setFusionFinal] = useState("");
  const [fusionStatus, setFusionStatus] = useState<RunStatus>("idle");
  const [fusionLog, setFusionLog] = useState<string[]>([]);
  const [fusionProviderReceipt, setFusionProviderReceipt] =
    useState<ProviderRouteReceipt | null>(null);
  const [useNativeFusion, setUseNativeFusion] = useState(true);

  const [routeMessage, setRouteMessage] = useState(
    initialAgencyDraft?.goal ||
      "我想做一个 SaaS 产品的首页改版，需要兼顾转化、性能和移动端体验。",
  );
  const [routeMatches, setRouteMatches] = useState<AgentSummary[]>([]);
  const [routeAnswer, setRouteAnswer] = useState("");
  const [routeStatus, setRouteStatus] = useState<RunStatus>("idle");
  const [routeError, setRouteError] = useState("");
  const [routePlannerMode, setRoutePlannerMode] =
    useState<RoutePlannerMode>(initialAgencyDraft?.preview ? "agency" : "quick");
  const [agencyCapabilities, setAgencyCapabilities] =
    useState<AgencyPlannerCapabilities | null>(null);
  const [agencyCapabilitiesError, setAgencyCapabilitiesError] = useState("");
  const [agencyPlannerModelId, setAgencyPlannerModelId] =
    useState(initialAgencyDraft?.planner_model_id || DEFAULT_CHAT_MODEL_ID);
  const [agencyAgentModelId, setAgencyAgentModelId] =
    useState(initialAgencyDraft?.agent_model_id || DEFAULT_CHAT_MODEL_ID);
  const [agencyLineupMode, setAgencyLineupMode] =
    useState<"auto" | "pinned">("auto");
  const [agencyMaxAgents, setAgencyMaxAgents] = useState(5);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBaseSummary[]>([]);
  const [knowledgeBasesError, setKnowledgeBasesError] = useState("");
  const [agencyKnowledgeBaseId, setAgencyKnowledgeBaseId] = useState("");
  const [agencyKnowledgeConsent, setAgencyKnowledgeConsent] = useState(false);
  const [agencyStatus, setAgencyStatus] = useState<RunStatus>(
    initialAgencyDraft?.preview ? "done" : "idle",
  );
  const [agencyError, setAgencyError] = useState("");
  const [agencyPreview, setAgencyPreview] =
    useState<AgencyPlanPreviewWithKnowledge | null>(
      initialAgencyDraft?.preview ?? null,
    );
  const [agencyValidationStale, setAgencyValidationStale] = useState(
    initialAgencyDraft?.validation_stale ?? false,
  );
  const [agencyAppliedNotice, setAgencyAppliedNotice] = useState(false);
  const [loadedAgencyPlan, setLoadedAgencyPlan] =
    useState<AgencyPlanPreview | null>(initialAgencyDraft?.loaded_plan ?? null);
  const [loadedAgencyGoal, setLoadedAgencyGoal] = useState(
    initialAgencyDraft?.loaded_goal ?? "",
  );
  const [loadedAgencyPlanInvalid, setLoadedAgencyPlanInvalid] = useState(
    initialAgencyDraft?.loaded_invalid ?? false,
  );
  const [dagConfirmMode, setDagConfirmMode] = useState<"start" | "retry" | "revise" | null>(null);
  const [pendingDagRevision, setPendingDagRevision] =
    useState<AgencyDagRevisionPayload | null>(null);
  const agencyDag = useAgencyDagRun();
  const agencyAssets = useAgencyAssets();
  const [agencyMethodSkillId, setAgencyMethodSkillId] = useState("");
  const [taskTemplateName, setTaskTemplateName] = useState("");
  const [assetNotice, setAssetNotice] = useState("");

  const [teamTask, setTeamTask] = useState(
    initialAgencyDraft?.loaded_goal ||
      "为一个新上线的 AI 模型浏览器制定产品发布方案，包括技术风险、设计亮点和增长打法。",
  );
  const [teamMode, setTeamMode] = useState<"serial" | "debate" | "dag">(
    initialAgencyDraft?.loaded_plan && !initialAgencyDraft.loaded_invalid
      ? "dag"
      : "serial",
  );
  const [selectedDepartment, setSelectedDepartment] = useState("全部");
  const [agentSearch, setAgentSearch] = useState("");
  const [selectedAgentIds, setSelectedAgentIds] = useState<string[]>(
    initialAgencyDraft?.loaded_plan
      ? selectedAgentIdsFromAgencyPlan(initialAgencyDraft.loaded_plan)
      : agents.slice(0, 3).map((agent) => agent.id),
  );
  const [agentTasks, setAgentTasks] = useState<Record<string, string>>(() => {
    if (!initialAgencyDraft?.loaded_plan) return {};
    const selectedIds = selectedAgentIdsFromAgencyPlan(
      initialAgencyDraft.loaded_plan,
    );
    return agentTasksFromAgencyPlan(initialAgencyDraft.loaded_plan, selectedIds);
  });
  const [teamOutputs, setTeamOutputs] = useState<TeamAgentOutput[]>([]);
  const [teamFinal, setTeamFinal] = useState("");
  const [teamStatus, setTeamStatus] = useState<RunStatus>("idle");
  const [savedTeams] = useState<TeamSavedConfig[]>(readSavedTeams);
  const [teamName, setTeamName] = useState(
    initialAgencyDraft?.team_name ||
      initialAgencyDraft?.loaded_plan?.candidate.name ||
      "产品发布专家组",
  );

  useEffect(() => {
    document.title = "模镜 - 专家团会诊室";
  }, []);

  useEffect(() => {
    writeAgencyPlanDraft({
      goal: routeMessage,
      preview: agencyPreview,
      validation_stale: agencyValidationStale,
      loaded_plan: loadedAgencyPlan,
      loaded_goal: loadedAgencyGoal,
      loaded_invalid: loadedAgencyPlanInvalid,
      planner_model_id: agencyPlannerModelId,
      agent_model_id: agencyAgentModelId,
      execution_model_id: sharedModelId,
      team_name: teamName,
    });
  }, [
    agencyAgentModelId,
    agencyPlannerModelId,
    agencyPreview,
    agencyValidationStale,
    loadedAgencyGoal,
    loadedAgencyPlan,
    loadedAgencyPlanInvalid,
    routeMessage,
    sharedModelId,
    teamName,
  ]);

  useEffect(() => {
    let active = true;
    void fetch("/api/rag/knowledge_bases")
      .then((response) =>
        responseJson<{ knowledge_bases: KnowledgeBaseSummary[] }>(response),
      )
      .then((payload) => {
        if (!active) return;
        setKnowledgeBases(payload.knowledge_bases);
        setKnowledgeBasesError("");
      })
      .catch((error: unknown) => {
        if (!active) return;
        setKnowledgeBasesError(
          error instanceof Error ? error.message : "无法读取资料库列表。",
        );
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const desk = searchParams.get("desk");
    if (desk === "route" || desk === "team" || desk === "fusion") {
      setActiveDesk(desk);
    }
  }, [searchParams]);

  useEffect(() => {
    const restored = agencyDag.run;
    if (!restored?.task_id) return;
    setActiveDesk("team");
    setTeamMode("dag");
    const restoredAgentIds = (
      restored.selected_agent_ids ||
      restored.task_definitions?.map((task) => task.agent_id) ||
      []
    ).filter(
      (agentId, index, values) =>
        values.indexOf(agentId) === index &&
        agents.some((agent) => agent.id === agentId),
    );
    if (restoredAgentIds.length > 0) setSelectedAgentIds(restoredAgentIds);
    if (restored.goal) {
      setTeamTask(restored.goal);
      setLoadedAgencyGoal(restored.goal);
    }
    if (restored.team_name) setTeamName(restored.team_name);
    if (restored.task_definitions?.length) {
      const grouped = new Map<string, string[]>();
      for (const task of restored.task_definitions) {
        const existing = grouped.get(task.agent_id) || [];
        const details = [
          task.objective,
          task.depends_on.length ? `依赖：${task.depends_on.join("、")}` : "",
          task.acceptance ? `验收：${task.acceptance}` : "",
          task.method_skill_ids?.length
            ? `方法 Skill：${task.method_skill_ids.join("、")}`
            : "",
        ].filter(Boolean).join("\n");
        grouped.set(task.agent_id, [...existing, details]);
      }
      setAgentTasks(Object.fromEntries(
        [...grouped.entries()].map(([agentId, tasks]) => [
          agentId,
          tasks.join("\n\n"),
        ]),
      ));
    }
    setLoadedAgencyPlanInvalid(false);
  }, [agencyDag.run?.task_id]);

  const dagEstimatedCostCny = useMemo(() => {
    if (!agencyDag.run) return null;
    const model = models.find(
      (item) => item.id === (agencyDag.run?.model_id || sharedModelId),
    );
    if (!model || model.pricing_status === "dynamic") return null;
    const inputTokens = agencyDag.run.usage.input_tokens || 0;
    const outputTokens = agencyDag.run.usage.output_tokens || 0;
    const pricing = tokenPricingForPrompt(model, inputTokens);
    return (
      (inputTokens / 1_000_000) * pricing.input * USD_TO_CNY +
      (outputTokens / 1_000_000) * pricing.output * USD_TO_CNY
    );
  }, [agencyDag.run, sharedModelId]);

  useEffect(() => {
    let active = true;
    void fetch("/api/expert-team/planner-capabilities")
      .then((response) => responseJson<AgencyPlannerCapabilities>(response))
      .then((payload) => {
        if (active) setAgencyCapabilities(payload);
      })
      .catch((error: unknown) => {
        if (active) {
          setAgencyCapabilitiesError(
            error instanceof Error ? error.message : "无法读取智能组队状态。",
          );
        }
      });
    return () => {
      active = false;
    };
  }, []);

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

  function invalidateLoadedAgencyPlan() {
    if (loadedAgencyPlan) setLoadedAgencyPlanInvalid(true);
    setDagConfirmMode(null);
  }

  function updateRouteMessage(value: string) {
    setRouteMessage(value);
    if (loadedAgencyPlan && value !== loadedAgencyGoal) {
      setLoadedAgencyPlanInvalid(true);
    }
  }

  function updateAgencyKnowledgeBase(value: string) {
    setAgencyKnowledgeBaseId(value);
    setAgencyKnowledgeConsent(false);
    setAgencyPreview(null);
    setAgencyValidationStale(false);
    setAgencyStatus("idle");
    invalidateLoadedAgencyPlan();
  }

  function selectAgencyTemplate(reference: string) {
    if (!reference) return;
    const template = agencyAssets.assets.templates.find(
      (item) => item.ref === reference,
    );
    const garden = agencyAssets.assets.garden.find(
      (item) => `garden:${item.id}` === reference,
    );
    const content = template?.content || garden?.content;
    if (!content) return;
    updateRouteMessage(content);
    setAgencyPreview(null);
    setAgencyValidationStale(false);
    setAgencyStatus("idle");
    setAssetNotice(
      template ? `已载入任务模板：${template.name}` : `已载入 Prompt Garden：${garden?.name}`,
    );
  }

  function loadServerTeam(team: AgencyTeamAsset) {
    if (teamMode === "dag") return;
    const memberIds = team.roles
      .map((role) => role.role)
      .filter((id, index, values) =>
        values.indexOf(id) === index && agents.some((agent) => agent.id === id),
      )
      .slice(0, 6);
    setTeamName(team.name);
    setSelectedAgentIds(memberIds);
    setAgencyLineupMode("pinned");
    setAssetNotice(`已载入固定阵容：${team.name}`);
    invalidateLoadedAgencyPlan();
  }

  function updateAgencyMethodSkill(value: string) {
    setAgencyMethodSkillId(value);
    setAgencyPreview(null);
    setAgencyValidationStale(false);
    setAgencyStatus("idle");
    invalidateLoadedAgencyPlan();
  }

  function updateTeamTask(value: string) {
    setTeamTask(value);
    if (teamMode === "dag" && value !== loadedAgencyGoal) {
      setLoadedAgencyPlanInvalid(true);
      setDagConfirmMode(null);
    }
  }

  function toggleTeamAgent(agentId: string) {
    if (teamMode === "dag") return;
    setSelectedAgentIds((current) => {
      if (current.includes(agentId)) {
        return current.filter((item) => item !== agentId);
      }
      return current.length >= 6 ? current : [...current, agentId];
    });
    invalidateLoadedAgencyPlan();
  }

  function selectDepartmentAgents(department: string) {
    if (teamMode === "dag") return;
    const departmentIds = agents
      .filter((agent) => agent.department === department)
      .slice(0, 6)
      .map((agent) => agent.id);
    setSelectedAgentIds(departmentIds);
    invalidateLoadedAgencyPlan();
  }

  async function saveCurrentTeam() {
    if (selectedAgentIds.length === 0) return;
    const name = teamName.trim() || "未命名专家团";
    try {
      await agencyAssets.saveTeam({
        name,
        description: teamTask.trim(),
        agent_ids: selectedAgentIds,
      });
      setAssetNotice(`固定阵容“${name}”已保存到服务端。`);
    } catch {
      // The hook exposes the server message next to the asset controls.
    }
  }

  async function saveCurrentTaskTemplate() {
    const name = taskTemplateName.trim();
    if (!name || !routeMessage.trim()) return;
    try {
      await agencyAssets.saveTemplate({
        name,
        content: routeMessage.trim(),
        note: "由专家团智能组队保存",
      });
      setTaskTemplateName("");
      setAssetNotice(`任务模板“${name}”已保存；同名保存会保留版本历史。`);
    } catch {
      // The hook exposes the server message next to the asset controls.
    }
  }

  function loadTeam(team: TeamSavedConfig) {
    if (teamMode === "dag") return;
    setTeamName(team.name);
    setSelectedAgentIds(team.members.filter((id) => agents.some((agent) => agent.id === id)));
    invalidateLoadedAgencyPlan();
  }

  function updateAgentTask(agentId: string, value: string) {
    setAgentTasks((current) => ({ ...current, [agentId]: value }));
    invalidateLoadedAgencyPlan();
  }

  async function runFusion() {
    if (fusionModelIds.length < 2 || !fusionQuestion.trim()) return;
    setFusionStatus("running");
    setFusionFinal("");
    setFusionProviderReceipt(null);
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
          const receipt = fusionReceiptFromEvent(event);
          if (receipt) setFusionProviderReceipt(receipt);
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
          if (eventName === "fusion_end") {
            const warning = eventText(event, "warning");
            if (warning) setFusionLog((current) => [...current, warning]);
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

  async function runAgencyPreview() {
    if (!routeMessage.trim() || !agencyCapabilities?.enabled) return;
    if (agencyLineupMode === "pinned" && selectedAgentIds.length === 0) {
      setAgencyError("固定阵容至少需要一位已选 AI Team 专家。");
      return;
    }
    setAgencyStatus("running");
    setAgencyError("");
    setAgencyPreview(null);
    setAgencyValidationStale(false);
    invalidateLoadedAgencyPlan();
    try {
      const response = await fetch("/api/expert-team/plan-preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          goal: routeMessage,
          planner_model_id: agencyPlannerModelId,
          default_agent_model_id: agencyAgentModelId,
          mode: agencyLineupMode,
          pinned_agent_ids:
            agencyLineupMode === "pinned" ? selectedAgentIds : [],
          max_agents: agencyMaxAgents,
          temperature: 0.2,
          knowledge_base_id: agencyKnowledgeBaseId || null,
          allow_knowledge_context:
            Boolean(agencyKnowledgeBaseId) && agencyKnowledgeConsent,
          method_skill_id: agencyMethodSkillId || null,
        }),
      });
      const preview = await responseJson<AgencyPlanPreviewWithKnowledge>(response);
      setAgencyPreview(preview);
      setAgencyStatus("done");
    } catch (error) {
      setAgencyStatus("error");
      setAgencyError(
        error instanceof Error ? error.message : "智能组队预览失败。",
      );
    }
  }

  function updateAgencyTask(
    taskId: string,
    patch: Partial<
      Pick<
        AgencyPlanTask,
        | "title"
        | "objective"
        | "acceptance"
        | "method_skill_ids"
        | "interaction_prompt"
        | "output_variable"
      >
    >,
  ) {
    setAgencyPreview((current) => {
      if (!current) return current;
      const tasks = current.plan.tasks.map((task) =>
        task.task_id === taskId ? { ...task, ...patch } : task,
      );
      const workflow = syncWorkflowToPlan(current.workflow, tasks);
      return {
        ...current,
        plan: { ...current.plan, tasks },
        workflow,
        candidate: {
          ...current.candidate,
          draft: { ...current.candidate.draft, workflow },
        },
      };
    });
    setAgencyValidationStale(true);
    invalidateLoadedAgencyPlan();
  }

  function toggleAgencyDependency(taskId: string, dependencyId: string) {
    setAgencyPreview((current) => {
      if (!current) return current;
      const tasks = current.plan.tasks.map((task) => {
        if (task.task_id !== taskId) return task;
        const depends_on = task.depends_on.includes(dependencyId)
          ? task.depends_on.filter((item) => item !== dependencyId)
          : [...task.depends_on, dependencyId];
        const input_contract = depends_on.length
          ? depends_on.map((dependency) => {
              const source = current.plan.tasks.find((item) => item.task_id === dependency);
              return source?.output_variable || `${dependency}_output`;
            })
          : ["user_input"];
        return { ...task, depends_on, input_contract };
      });
      const workflow = syncWorkflowToPlan(current.workflow, tasks);
      return {
        ...current,
        plan: { ...current.plan, tasks },
        workflow,
        candidate: {
          ...current.candidate,
          draft: { ...current.candidate.draft, workflow },
        },
      };
    });
    setAgencyValidationStale(true);
    invalidateLoadedAgencyPlan();
  }

  function insertAgencyInteraction(taskType: "human_input" | "approval") {
    setAgencyPreview((current) => {
      if (!current) return current;
      const interactions = current.plan.tasks.filter(
        (task) => task.task_type === "human_input" || task.task_type === "approval",
      );
      if (current.plan.tasks.length >= 6 || interactions.length >= 2) return current;
      const dependedOn = new Set(current.plan.tasks.flatMap((task) => task.depends_on));
      const sink = [...current.plan.tasks]
        .reverse()
        .find((task) => !dependedOn.has(task.task_id) && (task.task_type || "expert") === "expert");
      if (!sink) return current;
      const prefix = taskType === "human_input" ? "human_input" : "approval";
      let suffix = interactions.length + 1;
      while (current.plan.tasks.some((task) => task.task_id === `${prefix}_${suffix}`)) suffix += 1;
      const taskId = `${prefix}_${suffix}`;
      const interactionTask: AgencyPlanTask = {
        task_id: taskId,
        title: taskType === "human_input" ? "补充必要信息" : "执行前审批",
        objective: taskType === "human_input"
          ? "等待用户补充继续执行所必需的信息。"
          : "等待用户确认是否继续执行下游任务。",
        depends_on: [...sink.depends_on],
        input_contract: sink.depends_on.length
          ? sink.depends_on.map((dependency) => {
              const source = current.plan.tasks.find((item) => item.task_id === dependency);
              return source?.output_variable || `${dependency}_output`;
            })
          : ["user_input"],
        output_contract: `${taskId}_output`,
        agent_id: null,
        acceptance: "",
        method_skill_ids: [],
        task_type: taskType,
        interaction_prompt: taskType === "human_input"
          ? "请补充继续完成任务所必需的信息。"
          : "请确认是否允许继续执行后续任务。",
        output_variable: `${taskId}_output`,
      };
      const tasks = current.plan.tasks.map((task) =>
        task.task_id === sink.task_id
          ? {
              ...task,
              depends_on: [taskId],
              input_contract: [`${taskId}_output`],
            }
          : task,
      );
      const sinkIndex = tasks.findIndex((task) => task.task_id === sink.task_id);
      tasks.splice(Math.max(0, sinkIndex), 0, interactionTask);
      const workflow = syncWorkflowToPlan(current.workflow, tasks);
      return {
        ...current,
        plan: { ...current.plan, tasks },
        workflow,
        candidate: {
          ...current.candidate,
          draft: { ...current.candidate.draft, workflow },
        },
      };
    });
    setAgencyValidationStale(true);
    invalidateLoadedAgencyPlan();
  }

  function removeAgencyInteraction(taskId: string) {
    setAgencyPreview((current) => {
      if (!current) return current;
      const removed = current.plan.tasks.find((task) => task.task_id === taskId);
      if (!removed || (removed.task_type || "expert") === "expert") return current;
      const tasks = current.plan.tasks
        .filter((task) => task.task_id !== taskId)
        .map((task) => {
          const depends_on = task.depends_on.includes(taskId)
            ? [...new Set([
                ...task.depends_on.filter((dependency) => dependency !== taskId),
                ...removed.depends_on,
              ])].filter((dependency) => dependency !== task.task_id)
            : task.depends_on;
          const input_contract = depends_on.length
            ? depends_on.map((dependency) => {
                const source = current.plan.tasks.find((item) => item.task_id === dependency);
                return source?.output_variable || `${dependency}_output`;
              })
            : ["user_input"];
          return { ...task, depends_on, input_contract };
        });
      const workflow = syncWorkflowToPlan(current.workflow, tasks);
      return {
        ...current,
        plan: { ...current.plan, tasks },
        workflow,
        candidate: {
          ...current.candidate,
          draft: { ...current.candidate.draft, workflow },
        },
      };
    });
    setAgencyValidationStale(true);
    invalidateLoadedAgencyPlan();
  }

  async function revalidateAgencyWorkflow() {
    if (!agencyPreview) return;
    setAgencyStatus("running");
    setAgencyError("");
    try {
      const response = await fetch("/api/workflow-native/validate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workflow: agencyPreview.workflow }),
      });
      const validation = await responseJson<{
        valid: boolean;
        issues: AgencyValidationIssue[];
      }>(response);
      const hitlIssues = validateAgencyHitlPlan(agencyPreview.plan.tasks);
      const combinedIssues = [...validation.issues, ...hitlIssues];
      const combinedValid = validation.valid && hitlIssues.length === 0;
      setAgencyPreview((current) =>
        current
          ? {
              ...current,
              validation: {
                valid: combinedValid,
                issues: combinedIssues,
                stages: [
                  {
                    id: "workflow",
                    valid: combinedValid,
                    issues: combinedIssues,
                  },
                ],
              },
            }
          : current,
      );
      setAgencyValidationStale(false);
      setAgencyStatus("done");
    } catch (error) {
      setAgencyStatus("error");
      setAgencyError(
        error instanceof Error ? error.message : "工作流重新校验失败。",
      );
    }
  }

  function applyAgencyPlanToTeam() {
    if (!agencyPreview || agencyValidationStale || !agencyPreview.validation.valid) {
      return;
    }
    const selectedIds = selectedAgentIdsFromAgencyPlan(agencyPreview);
    agencyDag.clear();
    setSelectedAgentIds(selectedIds);
    setAgentTasks(agentTasksFromAgencyPlan(agencyPreview, selectedIds));
    setTeamTask(routeMessage);
    setTeamName(agencyPreview.candidate.name || "智能组队专家团");
    setSharedModelId(agencyAgentModelId);
    setLoadedAgencyPlan(agencyPreview);
    setLoadedAgencyGoal(routeMessage);
    setLoadedAgencyPlanInvalid(false);
    setTeamMode(agencyCapabilities?.execution?.enabled ? "dag" : "serial");
    setAgencyAppliedNotice(true);
    setActiveDesk("team");
  }

  async function startAgencyDag() {
    if (!loadedAgencyPlan || loadedAgencyPlanInvalid) return;
    const methodSkillIds = new Set(
      loadedAgencyPlan.plan.tasks.flatMap(
        (task) => task.method_skill_ids || [],
      ),
    );
    const method_skill_digests = Object.fromEntries(
      agencyAssets.assets.method_skills
        .filter((skill) => methodSkillIds.has(skill.skill_id))
        .map((skill) => [skill.skill_id, skill.digest]),
    );
    try {
      await agencyDag.start({
        goal: loadedAgencyGoal,
        plan: loadedAgencyPlan.plan,
        workflow: loadedAgencyPlan.workflow,
        model_id: sharedModelId,
        capability_snapshot_version:
          loadedAgencyPlan.capability_snapshot_version,
        capability_snapshot_hash: loadedAgencyPlan.capability_snapshot_hash,
        upstream_revision: loadedAgencyPlan.upstream_revision,
        method_skill_digests,
      });
      setDagConfirmMode(null);
    } catch {
      // The hook exposes the actionable server message in the DAG panel.
    }
  }

  async function retryAgencyDag() {
    try {
      await agencyDag.retry();
      setDagConfirmMode(null);
    } catch {
      // The hook exposes the actionable server message in the DAG panel.
    }
  }

  async function reviseAgencyDag() {
    if (!pendingDagRevision) return;
    try {
      await agencyDag.revise(pendingDagRevision);
      setDagConfirmMode(null);
      setPendingDagRevision(null);
    } catch {
      // The hook exposes the actionable server message in the DAG panel.
    }
  }

  async function runTeam() {
    if (teamMode === "dag") {
      if (
        !loadedAgencyPlan ||
        loadedAgencyPlanInvalid ||
        !agencyCapabilities?.execution?.enabled
      ) {
        return;
      }
      setDagConfirmMode("start");
      return;
    }
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
            {FUSION_ROUTING_BOUNDARY_COPY}
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
          description={`输入需求，系统从 ${agents.length} 位专家中自动匹配岗位。`}
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
                使用原生 Fusion
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
              <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs font-semibold text-slate-400">
                  候选模型池（最多 5 位）
                </p>
                <input
                  aria-label="搜索候选模型"
                  className="h-9 w-full rounded-lg border border-white/10 bg-ink-950/76 px-3 text-xs text-white outline-none transition placeholder:text-slate-400 focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10 sm:w-64"
                  onChange={(event) => setFusionModelSearch(event.target.value)}
                  placeholder="按名称或模型 ID 搜索"
                  type="search"
                  value={fusionModelSearch}
                />
              </div>
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
                {modelPool.length === 0 ? (
                  <p className="py-4 text-sm text-slate-400">
                    未找到匹配的文本模型。
                  </p>
                ) : null}
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
              <ProviderRouteReceiptSummary
                receipts={fusionProviderReceipt}
                title="Fusion 控制面"
              />
            </div>
          </div>
        </section>
      ) : null}

      {activeDesk === "route" ? (
        <section className="mt-6">
          <div
            aria-label="派工方式"
            className="inline-flex rounded-full border border-white/10 bg-white/[0.045] p-1"
            role="tablist"
          >
            {(
              [
                ["quick", "快速单专家"],
                ["agency", "智能组队预览"],
              ] as const
            ).map(([mode, label]) => (
              <button
                aria-selected={routePlannerMode === mode}
                className={`rounded-full px-4 py-2 text-sm font-semibold transition ${
                  routePlannerMode === mode
                    ? "bg-hire-300 text-ink-950"
                    : "text-slate-300 hover:text-white"
                }`}
                key={mode}
                onClick={() => setRoutePlannerMode(mode)}
                role="tab"
                type="button"
              >
                {label}
              </button>
            ))}
          </div>

          {routePlannerMode === "quick" ? (
            <div className="mt-4 grid gap-6 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
              <div className="surface-panel rounded-lg p-5">
                <h2 className="text-xl font-semibold text-white">自动路由派工</h2>
                <p className="mt-1 text-sm text-slate-400">
                  按名称、部门、专长和场景匹配一位专家，随后直接作答。
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
                  onChange={(event) => updateRouteMessage(event.target.value)}
                  placeholder="描述你要完成的任务"
                  value={routeMessage}
                />
                <button
                  className="mt-5 w-full rounded-full bg-hire-300 px-5 py-3 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={routeStatus === "running" || !routeMessage.trim()}
                  onClick={runRouteAgent}
                  type="button"
                >
                  {routeStatus === "running"
                    ? "正在自动派工..."
                    : "匹配专家并作答"}
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
            </div>
          ) : (
            <div className="mt-4 grid gap-6 xl:grid-cols-[minmax(0,0.78fr)_minmax(0,1.22fr)]">
              <div className="surface-panel self-start rounded-lg p-5">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h2 className="text-xl font-semibold text-white">
                      智能组队预览
                    </h2>
                    <p className="mt-1 text-sm leading-6 text-slate-400">
                      显式生成任务 DAG，不会自动启动团队或执行计划。
                    </p>
                  </div>
                  <span
                    className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${
                      agencyCapabilities?.enabled
                        ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                        : "border-slate-400/20 bg-white/[0.045] text-slate-400"
                    }`}
                  >
                    {agencyCapabilities?.enabled ? "已启用" : "默认关闭"}
                  </span>
                </div>

                {agencyCapabilitiesError ? (
                  <p className="mt-4 rounded-lg border border-red-300/20 bg-red-300/10 p-3 text-sm text-red-100">
                    {agencyCapabilitiesError}
                  </p>
                ) : null}
                {!agencyCapabilities?.enabled ? (
                  <p className="mt-4 rounded-lg border border-white/10 bg-white/[0.04] p-3 text-sm leading-6 text-slate-300">
                    服务端设置 EXPERT_TEAM_AGENCY_PLANNER_ENABLED=1 后可用。状态读取不调用模型。
                  </p>
                ) : null}

                <textarea
                  className="mt-5 min-h-36 w-full rounded-lg border border-white/10 bg-ink-950/76 p-4 text-sm leading-6 text-white outline-none transition placeholder:text-slate-500 focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10"
                  onChange={(event) => updateRouteMessage(event.target.value)}
                  placeholder="描述需要拆解并组队的目标"
                  value={routeMessage}
                />
                <div className="mt-3 grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                  <label className="block">
                    <span className="text-xs font-semibold text-slate-400">
                      任务模板
                    </span>
                    <select
                      aria-label="载入任务模板"
                      className="mt-2 h-10 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-xs text-white outline-none focus:border-hire-300/70"
                      defaultValue=""
                      onChange={(event) => {
                        selectAgencyTemplate(event.target.value);
                        event.target.value = "";
                      }}
                    >
                      <option value="">选择已保存模板或 Prompt Garden</option>
                      {agencyAssets.assets.templates.map((template) => (
                        <option key={template.ref} value={template.ref}>
                          已保存 · {template.name} · v{template.version_count}
                        </option>
                      ))}
                      {agencyAssets.assets.garden
                        .filter((seed) => seed.mode === "user")
                        .map((seed) => (
                          <option key={seed.id} value={`garden:${seed.id}`}>
                            Prompt Garden · {seed.name}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label className="block">
                    <span className="text-xs font-semibold text-slate-400">
                      保存当前目标
                    </span>
                    <input
                      className="mt-2 h-10 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-xs text-white outline-none placeholder:text-slate-500 focus:border-hire-300/70"
                      onChange={(event) => setTaskTemplateName(event.target.value)}
                      placeholder="模板名称"
                      value={taskTemplateName}
                    />
                  </label>
                  <button
                    className="self-end rounded-full border border-white/10 bg-white/[0.06] px-4 py-2.5 text-xs font-semibold text-slate-100 transition hover:border-hire-300/35 hover:text-hire-100 disabled:cursor-not-allowed disabled:opacity-40"
                    disabled={
                      agencyAssets.busy ||
                      !taskTemplateName.trim() ||
                      !routeMessage.trim()
                    }
                    onClick={() => void saveCurrentTaskTemplate()}
                    type="button"
                  >
                    保存模板
                  </button>
                </div>
                <p className="mt-2 text-xs leading-5 text-slate-500">
                  复用 Agency Prompt 版本存储；同名再次保存会追加版本，不会调用模型。
                </p>
                <label className="mt-4 block">
                  <span className="text-xs font-semibold text-slate-400">
                    参考资料库（可选）
                  </span>
                  <select
                    className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-sm text-white outline-none focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10"
                    onChange={(event) =>
                      updateAgencyKnowledgeBase(event.target.value)
                    }
                    value={agencyKnowledgeBaseId}
                  >
                    <option value="">不使用资料库</option>
                    {knowledgeBases.map((knowledgeBase) => (
                      <option key={knowledgeBase.id} value={knowledgeBase.id}>
                        {knowledgeBase.name} · {knowledgeBase.document_count} 个文档
                      </option>
                    ))}
                  </select>
                  <span className="mt-2 block text-xs leading-5 text-slate-500">
                    复用现有 RAG 检索，不创建第二份上传或解析链路。
                    {" "}
                    <a className="text-hire-200 hover:text-hire-100" href="/rag">
                      管理资料库
                    </a>
                  </span>
                </label>
                {agencyKnowledgeBaseId ? (
                  <label className="mt-3 flex items-start gap-2 rounded-lg border border-amber-300/20 bg-amber-300/[0.07] p-3 text-xs leading-5 text-amber-50">
                    <input
                      checked={agencyKnowledgeConsent}
                      className="mt-0.5"
                      onChange={(event) =>
                        setAgencyKnowledgeConsent(event.target.checked)
                      }
                      type="checkbox"
                    />
                    <span>
                      我确认：点击生成后，服务端会检索最多 4 个片段，并可能按现有 RAG 检索配置处理查询与候选片段；最多 12,000 字符的命中原文会发送给当前规划模型及其配置网关。未勾选不会把资料库内容用于本次规划。
                    </span>
                  </label>
                ) : null}
                {knowledgeBasesError ? (
                  <p className="mt-2 text-xs leading-5 text-amber-100">
                    资料库列表读取失败：{knowledgeBasesError}
                  </p>
                ) : null}
                <div className="mt-4 grid gap-4 md:grid-cols-2">
                  <ModelSelector
                    label="规划模型"
                    onChange={setAgencyPlannerModelId}
                    value={agencyPlannerModelId}
                  />
                  <ModelSelector
                    label="专家执行模型"
                    onChange={setAgencyAgentModelId}
                    value={agencyAgentModelId}
                  />
                </div>

                <label className="mt-4 block">
                  <span className="text-xs font-semibold text-slate-400">
                    方法 Skill（可选）
                  </span>
                  <select
                    className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-sm text-white outline-none focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10"
                    onChange={(event) => updateAgencyMethodSkill(event.target.value)}
                    value={agencyMethodSkillId}
                  >
                    <option value="">不注入方法 Skill</option>
                    {agencyAssets.assets.method_skills.map((skill) => (
                      <option key={skill.skill_id} value={skill.skill_id}>
                        {skill.name} · {skill.description}
                      </option>
                    ))}
                  </select>
                  <span className="mt-2 block text-xs leading-5 text-slate-500">
                    仅提供方法说明，不开放工具或外部副作用；服务端会在执行前核对 Skill 摘要。
                  </span>
                </label>

                <div className="mt-4 grid gap-4 md:grid-cols-2">
                  <label className="block">
                    <span className="text-xs font-semibold text-slate-400">
                      组队范围
                    </span>
                    <select
                      className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-sm text-white outline-none focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10"
                      onChange={(event) =>
                        setAgencyLineupMode(
                          event.target.value as "auto" | "pinned",
                        )
                      }
                      value={agencyLineupMode}
                    >
                      <option value="auto">从全部专家自动选择</option>
                      <option value="pinned">固定当前 AI Team 阵容</option>
                    </select>
                  </label>
                  <label className="block">
                    <span className="text-xs font-semibold text-slate-400">
                      最多专家数
                    </span>
                    <select
                      className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-sm text-white outline-none focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10"
                      onChange={(event) =>
                        setAgencyMaxAgents(Number(event.target.value))
                      }
                      value={agencyMaxAgents}
                    >
                      {[1, 2, 3, 4, 5, 6].map((value) => (
                        <option key={value} value={value}>
                          {value} 位
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                {agencyLineupMode === "pinned" ? (
                  <div className="mt-4 rounded-lg border border-white/10 bg-white/[0.04] p-3">
                    <p className="text-xs font-semibold text-slate-300">
                      当前固定阵容（{selectedAgentIds.length}/6）
                    </p>
                    <p className="mt-2 text-xs leading-5 text-slate-400">
                      {selectedAgentIds
                        .map(
                          (id) => agents.find((agent) => agent.id === id)?.name,
                        )
                        .filter(Boolean)
                        .join("、") || "请先在 AI Team 选择专家。"}
                    </p>
                    {agencyAssets.assets.teams.length > 0 ? (
                      <label className="mt-3 block">
                        <span className="text-xs font-semibold text-slate-400">
                          载入服务端固定阵容
                        </span>
                        <select
                          aria-label="载入服务端固定阵容"
                          className="mt-2 h-10 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-xs text-white outline-none focus:border-hire-300/70"
                          defaultValue=""
                          onChange={(event) => {
                            const team = agencyAssets.assets.teams.find(
                              (item) => item.ref === event.target.value,
                            );
                            if (team) loadServerTeam(team);
                            event.target.value = "";
                          }}
                        >
                          <option value="">选择已保存阵容</option>
                          {agencyAssets.assets.teams.map((team) => (
                            <option key={team.ref} value={team.ref}>
                              {team.name} · {team.roles.length} 位
                            </option>
                          ))}
                        </select>
                      </label>
                    ) : null}
                  </div>
                ) : null}

                {agencyAssets.error ? (
                  <p className="mt-4 rounded-lg border border-amber-300/20 bg-amber-300/[0.07] p-3 text-xs leading-5 text-amber-100">
                    可复用资产暂不可用：{agencyAssets.error}
                  </p>
                ) : null}
                {assetNotice ? (
                  <p className="mt-4 rounded-lg border border-emerald-300/20 bg-emerald-300/[0.07] p-3 text-xs leading-5 text-emerald-100">
                    {assetNotice}
                  </p>
                ) : null}
                {agencyError ? (
                  <p className="mt-4 rounded-lg border border-red-300/20 bg-red-300/10 p-3 text-sm text-red-100">
                    {agencyError}
                  </p>
                ) : null}
                <button
                  className="mt-5 w-full rounded-full bg-hire-300 px-5 py-3 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={
                    agencyStatus === "running" ||
                    !agencyCapabilities?.enabled ||
                    !routeMessage.trim() ||
                    (Boolean(agencyKnowledgeBaseId) &&
                      !agencyKnowledgeConsent) ||
                    (agencyLineupMode === "pinned" &&
                      selectedAgentIds.length === 0)
                  }
                  onClick={runAgencyPreview}
                  type="button"
                >
                  {agencyStatus === "running"
                    ? "正在生成组队计划..."
                    : "生成智能组队预览"}
                </button>
                <p className="mt-3 text-xs leading-5 text-slate-500">
                  只有点击此按钮才会调用规划模型。最多 3 次模型调用，具体费用由所选模型决定。
                </p>
              </div>

              <div className="space-y-4">
                {agencyPreview ? (
                  <>
                    <div className="surface-panel rounded-lg p-5">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div>
                          <h3 className="text-lg font-semibold text-white">
                            {agencyPreview.candidate.name}
                          </h3>
                          <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-400">
                            {agencyPreview.plan.summary}
                          </p>
                        </div>
                        <span className="rounded-full border border-hire-300/25 bg-hire-300/10 px-3 py-1.5 text-xs font-semibold text-hire-100">
                          {agencyPreview.plan.tasks.length} 个任务 · {agencyPreview.selected_agents.length} 位专家
                        </span>
                      </div>
                      <div className="mt-4 flex flex-wrap gap-2">
                        {agencyPreview.selected_agents.map((agent) => (
                          <span
                            className="rounded-full border border-white/10 bg-white/[0.055] px-3 py-1.5 text-xs text-slate-200"
                            key={agent.id}
                          >
                            {agent.emoji || "专"} {agent.name} · {agent.department}
                          </span>
                        ))}
                      </div>
                      <p className="mt-4 text-xs leading-5 text-slate-500">
                        规则路由基线：
                        {agencyPreview.baseline_matches
                          .map((agent) => agent.name)
                          .join("、") || "无匹配"}
                      </p>
                      {agencyPreview.knowledge_context ? (
                        <div className="mt-4 rounded-lg border border-cyan-300/20 bg-cyan-300/[0.06] p-3">
                          <p className="text-xs font-semibold text-cyan-100">
                            已引用 {agencyPreview.knowledge_context.knowledge_base.name} · {agencyPreview.knowledge_context.sources.length} 个片段
                          </p>
                          <ul className="mt-2 space-y-1 text-xs leading-5 text-slate-300">
                            {agencyPreview.knowledge_context.sources.map((source) => (
                              <li key={`${source.document_id}:${source.chunk_id}`}>
                                · {source.document_name}
                                {source.page_number ? ` · 第 ${source.page_number} 页` : ""}
                                {source.slide ? ` · 第 ${source.slide} 页幻灯片` : ""}
                                {source.sheet ? ` · ${source.sheet}` : ""}
                              </li>
                            ))}
                          </ul>
                        </div>
                      ) : null}
                    </div>

                    {agencyCapabilities?.execution?.hitl?.enabled ? (
                      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-cyan-300/20 bg-cyan-300/[0.05] p-3">
                        <span className="mr-auto text-xs leading-5 text-slate-300">
                          人工节点默认插入最终汇点之前，并自动重连依赖。每个计划最多 2 个。
                        </span>
                        <button
                          className="rounded-full border border-cyan-300/25 px-3 py-1.5 text-xs font-semibold text-cyan-100 disabled:opacity-40"
                          disabled={agencyPreview.plan.tasks.length >= 6 || agencyPreview.plan.tasks.filter((task) => (task.task_type || "expert") !== "expert").length >= 2}
                          onClick={() => insertAgencyInteraction("human_input")}
                          type="button"
                        >
                          添加人工输入
                        </button>
                        <button
                          className="rounded-full border border-amber-300/25 px-3 py-1.5 text-xs font-semibold text-amber-100 disabled:opacity-40"
                          disabled={agencyPreview.plan.tasks.length >= 6 || agencyPreview.plan.tasks.filter((task) => (task.task_type || "expert") !== "expert").length >= 2}
                          onClick={() => insertAgencyInteraction("approval")}
                          type="button"
                        >
                          添加审批
                        </button>
                      </div>
                    ) : (
                      <p className="rounded-lg border border-white/10 bg-white/[0.035] p-3 text-xs text-slate-400">
                        人工输入与审批功能当前未启用；现有普通 DAG 仍可使用。
                      </p>
                    )}

                    <div className="space-y-3">
                      {agencyPreview.plan.tasks.map((task, index) => {
                        const expert = agencyPreview.selected_agents.find(
                          (item) => item.id === task.agent_id,
                        );
                        const isInteraction = (task.task_type || "expert") !== "expert";
                        return (
                          <article
                            className="surface-card rounded-lg p-4"
                            key={task.task_id}
                          >
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <p className="text-xs font-semibold text-hire-100">
                                任务 {index + 1} · {task.task_id}
                              </p>
                              <div className="flex items-center gap-2">
                                <span className="rounded-full border border-white/10 px-2.5 py-1 text-xs text-slate-300">
                                  {task.task_type === "human_input"
                                    ? "人工输入"
                                    : task.task_type === "approval"
                                      ? "审批"
                                      : "专家任务"}
                                </span>
                                <span className="text-xs text-slate-400">
                                  {isInteraction
                                    ? "无需模型或专家"
                                    : `${expert?.emoji || "专"} ${expert?.name || task.agent_id}`}
                                </span>
                                {isInteraction ? (
                                  <button
                                    className="rounded-full border border-red-300/20 px-2.5 py-1 text-xs text-red-100"
                                    onClick={() => removeAgencyInteraction(task.task_id)}
                                    type="button"
                                  >
                                    删除
                                  </button>
                                ) : null}
                              </div>
                            </div>
                            <input
                              aria-label={`${task.task_id} 任务标题`}
                              className="mt-3 h-10 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 text-sm font-semibold text-white outline-none focus:border-hire-300/70"
                              onChange={(event) =>
                                updateAgencyTask(task.task_id, {
                                  title: event.target.value,
                                })
                              }
                              value={task.title}
                            />
                            <textarea
                              aria-label={`${task.task_id} 任务目标`}
                              className="mt-3 min-h-24 w-full rounded-lg border border-white/10 bg-ink-950/70 p-3 text-sm leading-6 text-slate-200 outline-none focus:border-hire-300/70"
                              onChange={(event) =>
                                updateAgencyTask(task.task_id, {
                                  objective: event.target.value,
                                })
                              }
                              value={task.objective}
                            />
                            {isInteraction ? (
                              <textarea
                                aria-label={`${task.task_id} 交互提示`}
                                className="mt-3 min-h-20 w-full rounded-lg border border-cyan-300/20 bg-ink-950/70 p-3 text-sm leading-6 text-slate-200 outline-none focus:border-cyan-300/70"
                                maxLength={4000}
                                onChange={(event) =>
                                  updateAgencyTask(task.task_id, {
                                    interaction_prompt: event.target.value,
                                  })
                                }
                                placeholder={task.task_type === "approval" ? "说明需要用户批准的决策点" : "说明需要用户补充的信息"}
                                value={task.interaction_prompt || ""}
                              />
                            ) : (
                            <label className="mt-3 block">
                              <span className="text-xs font-semibold text-slate-400">
                                此步骤的方法 Skill
                              </span>
                              <select
                                aria-label={`${task.task_id} 方法 Skill`}
                                className="mt-2 h-10 w-full rounded-lg border border-white/10 bg-ink-950/70 px-3 text-xs text-white outline-none focus:border-hire-300/70"
                                onChange={(event) =>
                                  updateAgencyTask(task.task_id, {
                                    method_skill_ids: event.target.value
                                      ? [event.target.value]
                                      : [],
                                  })
                                }
                                value={task.method_skill_ids?.[0] || ""}
                              >
                                <option value="">不注入方法 Skill</option>
                                {agencyAssets.assets.method_skills.map((skill) => (
                                  <option key={skill.skill_id} value={skill.skill_id}>
                                    {skill.name}
                                  </option>
                                ))}
                              </select>
                            </label>
                            )}
                            <fieldset className="mt-3">
                              <legend className="text-xs font-semibold text-slate-400">
                                依赖任务
                              </legend>
                              <div className="mt-2 flex flex-wrap gap-2">
                                {agencyPreview.plan.tasks
                                  .filter((candidate) => candidate.task_id !== task.task_id)
                                  .map((candidate) => (
                                    <label
                                      className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.045] px-3 py-1.5 text-xs text-slate-300"
                                      key={candidate.task_id}
                                    >
                                      <input
                                        checked={task.depends_on.includes(
                                          candidate.task_id,
                                        )}
                                        className="h-3.5 w-3.5 accent-orange-400"
                                        onChange={() =>
                                          toggleAgencyDependency(
                                            task.task_id,
                                            candidate.task_id,
                                          )
                                        }
                                        type="checkbox"
                                      />
                                      {candidate.task_id}
                                    </label>
                                  ))}
                                {agencyPreview.plan.tasks.length === 1 ? (
                                  <span className="text-xs text-slate-500">无依赖</span>
                                ) : null}
                              </div>
                            </fieldset>
                            {!isInteraction ? (
                            <textarea
                              aria-label={`${task.task_id} 验收标准`}
                              className="mt-3 min-h-20 w-full rounded-lg border border-white/10 bg-ink-950/70 p-3 text-sm leading-6 text-slate-200 outline-none placeholder:text-slate-500 focus:border-hire-300/70"
                              onChange={(event) =>
                                updateAgencyTask(task.task_id, {
                                  acceptance: event.target.value,
                                })
                              }
                              placeholder="填写可检查的验收标准"
                              value={task.acceptance}
                            />
                            ) : (
                              <p className="mt-3 text-xs leading-5 text-slate-400">
                                该节点不绑定专家、Skill 或验收标准；运行到此处会持久暂停并退出 Worker。
                              </p>
                            )}
                          </article>
                        );
                      })}
                    </div>

                    <div className="surface-panel rounded-lg p-5">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <h3 className="text-lg font-semibold text-white">验证结果</h3>
                        <span
                          className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${
                            agencyValidationStale
                              ? "border-amber-300/25 bg-amber-300/10 text-amber-100"
                              : agencyPreview.validation.valid
                                ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                                : "border-red-300/25 bg-red-300/10 text-red-100"
                          }`}
                        >
                          {agencyValidationStale
                            ? "编辑后待校验"
                            : agencyPreview.validation.valid
                              ? "通过"
                              : "未通过"}
                        </span>
                      </div>
                      {groupAgencyValidationIssues(agencyPreview.validation.issues).filter(
                        (issue) => issue.severity !== "warning",
                      ).length > 0 ? (
                        <ul className="mt-3 space-y-2 text-sm text-red-100">
                          {groupAgencyValidationIssues(agencyPreview.validation.issues)
                            .filter((issue) => issue.severity !== "warning")
                            .map((issue, index) => (
                              <li
                                className="rounded-lg border border-red-300/15 bg-red-300/[0.07] px-3 py-2"
                                key={`${issue.code || "issue"}-${index}`}
                              >
                                {issue.message || issue.code || "工作流校验失败"}
                                {issue.count > 1 ? `（重复 ${issue.count} 次）` : ""}
                              </li>
                            ))}
                        </ul>
                      ) : (
                        <p className="mt-3 text-sm text-slate-400">
                          当前计划未发现工作流结构错误。
                        </p>
                      )}
                      {groupAgencyValidationIssues(agencyPreview.validation.issues).some(
                        (issue) => issue.severity === "warning",
                      ) ? (
                        <ul className="mt-3 space-y-2 text-sm text-amber-100">
                          {groupAgencyValidationIssues(agencyPreview.validation.issues)
                            .filter((issue) => issue.severity === "warning")
                            .map((issue, index) => (
                              <li
                                className="rounded-lg border border-amber-300/15 bg-amber-300/[0.07] px-3 py-2"
                                key={`${issue.code || "warning"}-${index}`}
                              >
                                {issue.message || issue.code || "工作流校验警告"}
                                {issue.count > 1 ? `（重复 ${issue.count} 次）` : ""}
                              </li>
                            ))}
                        </ul>
                      ) : null}
                      {agencyPreview.warnings.length > 0 ? (
                        <ul className="mt-3 space-y-1 text-xs leading-5 text-amber-100">
                          {agencyPreview.warnings.map((warning, index) => (
                            <li key={`${warning}-${index}`}>{warning}</li>
                          ))}
                        </ul>
                      ) : null}
                      <div className="mt-3 rounded-lg border border-white/10 bg-white/[0.035] p-3 text-xs leading-5 text-slate-300">
                        <p className="font-semibold text-slate-200">计划假设</p>
                        <ul className="mt-1 space-y-1">
                          {agencyPreview.plan.assumptions.map((assumption) => (
                            <li key={assumption}>· {assumption}</li>
                          ))}
                        </ul>
                        <p className="mt-2 text-slate-400">
                          本次规划：{agencyPreview.model_calls || 0} 次调用 · {(agencyPreview.usage.input_tokens || 0).toLocaleString()} 入 / {(agencyPreview.usage.output_tokens || 0).toLocaleString()} 出
                        </p>
                        <ProviderRouteReceiptSummary
                          receipts={agencyPreview.provider_route_receipts}
                          title="Planner 控制面"
                        />
                      </div>
                      <div className="mt-4 flex flex-wrap gap-2">
                        <button
                          className="rounded-full border border-white/10 bg-white/[0.06] px-4 py-2.5 text-sm font-semibold text-slate-100 transition hover:border-hire-300/35 hover:text-hire-100 disabled:opacity-50"
                          disabled={agencyStatus === "running"}
                          onClick={revalidateAgencyWorkflow}
                          type="button"
                        >
                          重新校验工作流
                        </button>
                        <button
                          className="rounded-full bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-not-allowed disabled:opacity-50"
                          disabled={
                            agencyValidationStale ||
                            !agencyPreview.validation.valid
                          }
                          onClick={applyAgencyPlanToTeam}
                          type="button"
                        >
                          应用到 AI Team
                        </button>
                      </div>
                      <p className="mt-4 break-all text-xs leading-5 text-slate-500">
                        来源：{agencyPreview.upstream_project}@
                        {agencyPreview.upstream_revision} · 快照 {agencyPreview.capability_snapshot_version} / {agencyPreview.capability_snapshot_hash}
                        {agencyPreview.repair_used ? " · 已使用一次修复" : ""}
                      </p>
                    </div>
                  </>
                ) : (
                  <div className="surface-panel rounded-lg p-8 text-center">
                    <p className="text-sm font-semibold text-white">尚未生成组队计划</p>
                    <p className="mx-auto mt-2 max-w-lg text-sm leading-6 text-slate-400">
                      填写目标并点击“生成智能组队预览”。计划生成后可编辑任务、依赖和验收标准，再载入现有 AI Team。
                    </p>
                  </div>
                )}
              </div>
            </div>
          )}
        </section>
      ) : null}

      {activeDesk === "team" ? (
        <section className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
          {agencyAppliedNotice ? (
            <div className="rounded-lg border border-hire-300/25 bg-hire-300/10 p-4 text-sm leading-6 text-hire-50 xl:col-span-2">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <p>
                  {agencyCapabilities?.execution?.enabled
                    ? "智能组队计划已载入，并默认选择 DAG Beta。计划不会自动运行，请检查成本护栏后再启动。"
                    : "智能组队计划已载入。当前按 AI Team 接力/辩论模式执行，DAG 自动执行尚未启用。"}
                </p>
                <button
                  className="rounded-full border border-hire-200/25 px-3 py-1 text-xs font-semibold text-hire-100 transition hover:bg-hire-300/10"
                  onClick={() => setAgencyAppliedNotice(false)}
                  type="button"
                >
                  关闭提示
                </button>
              </div>
            </div>
          ) : null}
          <div className="surface-panel rounded-lg p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-xl font-semibold text-white">组建 AI Team</h2>
                <p className="mt-1 text-sm text-slate-400">
                  最多选择 6 位专家；可串行接力、独立辩论，或执行已校验的 DAG Beta。
                </p>
              </div>
              <div className="flex rounded-full border border-white/10 bg-white/[0.045] p-1">
                {(["serial", "debate", "dag"] as const).map((mode) => (
                  <button
                    className={`rounded-full px-3 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 ${
                      teamMode === mode
                        ? "bg-hire-300 text-ink-950"
                        : "text-slate-300 hover:text-white"
                    }`}
                    key={mode}
                    disabled={
                      mode === "dag" &&
                      !agencyDag.run &&
                      (!loadedAgencyPlan ||
                        loadedAgencyPlanInvalid ||
                        !agencyCapabilities?.execution?.enabled)
                    }
                    onClick={() => setTeamMode(mode)}
                    type="button"
                  >
                    {mode === "serial"
                      ? "串行接力"
                      : mode === "debate"
                        ? "独立辩论"
                        : "DAG Beta"}
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
              onChange={(event) => updateTeamTask(event.target.value)}
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
                          className={`rounded-lg border p-3 text-left transition disabled:cursor-not-allowed disabled:opacity-60 ${
                            selected
                              ? "border-hire-300/55 bg-hire-300/12"
                              : "border-white/10 bg-white/[0.045] hover:border-hire-300/30"
                          }`}
                          disabled={teamMode === "dag"}
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
                        disabled={teamMode === "dag"}
                        onChange={(event) =>
                          updateAgentTask(agent.id, event.target.value)
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
                disabled={
                  teamMode === "dag"
                    ? agencyDag.busy ||
                      agencyDag.run?.status === "running" ||
                      !loadedAgencyPlan ||
                      loadedAgencyPlanInvalid ||
                      !agencyCapabilities?.execution?.enabled
                    : teamStatus === "running" || selectedAgentIds.length === 0
                }
                onClick={runTeam}
                type="button"
              >
                {teamMode === "dag" && agencyDag.run?.status === "running"
                  ? "DAG 执行中..."
                  : teamStatus === "running"
                    ? "专家组协作中..."
                    : "启动 AI Team"}
              </button>
              <button
                className="rounded-full border border-white/10 bg-white/[0.06] px-5 py-3 text-sm font-semibold text-slate-100 transition hover:border-hire-300/35 hover:text-hire-100"
                disabled={
                  teamMode === "dag" ||
                  agencyAssets.busy ||
                  selectedAgentIds.length === 0
                }
                onClick={() => void saveCurrentTeam()}
                type="button"
              >
                {agencyAssets.busy ? "正在保存..." : "保存固定阵容"}
              </button>
            </div>

            {agencyAssets.error ? (
              <p className="mt-4 rounded-lg border border-amber-300/20 bg-amber-300/[0.07] p-3 text-xs leading-5 text-amber-100">
                服务端阵容暂不可用：{agencyAssets.error}
              </p>
            ) : null}
            {assetNotice ? (
              <p className="mt-4 text-xs leading-5 text-emerald-100">
                {assetNotice}
              </p>
            ) : null}
            {agencyAssets.assets.teams.length > 0 ? (
              <div className="mt-4">
                <p className="text-xs font-semibold text-slate-400">
                  服务端固定阵容
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {agencyAssets.assets.teams.map((team) => (
                    <button
                      className="rounded-full border border-hire-300/20 bg-hire-300/[0.07] px-3 py-1.5 text-xs text-hire-100 transition hover:border-hire-300/45 disabled:cursor-not-allowed disabled:opacity-40"
                      disabled={teamMode === "dag"}
                      key={team.ref}
                      onClick={() => loadServerTeam(team)}
                      type="button"
                    >
                      载入：{team.name} · {team.roles.length} 位
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
            {savedTeams.length > 0 ? (
              <div className="mt-4">
                <p className="text-xs font-semibold text-slate-500">
                  此浏览器旧阵容
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {savedTeams.map((team) => (
                    <button
                      className="rounded-full border border-white/10 bg-white/[0.045] px-3 py-1.5 text-xs text-slate-300 transition hover:border-hire-300/35 hover:text-hire-100 disabled:cursor-not-allowed disabled:opacity-40"
                      disabled={teamMode === "dag"}
                      key={team.id}
                      onClick={() => loadTeam(team)}
                      type="button"
                    >
                      载入：{team.name}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
          </div>

          <div className="space-y-4">
            {teamMode === "dag" ? (
              <AgencyDagRunPanel
                agentCatalog={agents}
                busy={agencyDag.busy}
                capabilities={agencyCapabilities?.execution}
                confirmMode={dagConfirmMode}
                error={agencyDag.error}
                estimatedCostCny={dagEstimatedCostCny}
                invalid={loadedAgencyPlanInvalid}
                modelName={modelLabel(agencyDag.run?.model_id || sharedModelId)}
                onCancel={() => void agencyDag.cancel()}
                onConfirm={() => {
                  if (dagConfirmMode === "retry") {
                    void retryAgencyDag();
                  } else if (dagConfirmMode === "revise") {
                    void reviseAgencyDag();
                  } else {
                    void startAgencyDag();
                  }
                }}
                onDismissConfirm={() => {
                  if (dagConfirmMode === "revise") setPendingDagRevision(null);
                  setDagConfirmMode(null);
                }}
                onInteractionDecision={(payload) => agencyDag.decideInteraction(payload)}
                onInteractionReopen={() => agencyDag.reopenInteraction()}
                onRetryRequest={() => setDagConfirmMode("retry")}
                onRevisionRequest={(payload) => {
                  setPendingDagRevision(payload);
                  setDagConfirmMode("revise");
                }}
                pendingRevision={pendingDagRevision}
                preview={loadedAgencyPlan}
                run={agencyDag.run}
              />
            ) : (
              <>
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
              </>
            )}
          </div>
        </section>
      ) : null}
    </PageContainer>
  );
}
