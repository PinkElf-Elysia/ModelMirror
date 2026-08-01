import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import MetaPlannerV2 from "../components/meta/MetaPlannerV2";
import RuntimeApprovalPanel from "../components/runtime/RuntimeApprovalPanel";
import BrowserSessionPanel from "../components/runtime/BrowserSessionPanel";
import ClientToolPanel from "../components/runtime/ClientToolPanel";
import SandboxWorkspacePanel from "../components/runtime/SandboxWorkspacePanel";
import WorkflowNodeCard from "../components/workflow/WorkflowNodeCard";
import WorkflowRun from "../components/workflow/WorkflowRun";
import { models } from "../data/models";
import {
  type WorkflowDefinition,
  type WorkflowEdge,
  type WorkflowNode,
  type WorkflowNodeData,
} from "../types/workflow";
import { saveStoredWorkflow } from "../utils/workflowStorage";

interface MetaAgentParameter {
  name: string;
  type: string;
  description: string;
  required: boolean;
}

interface MetaAgentGeneratedAgent {
  name: string;
  description: string;
  prompt: string;
  tool_names: string[] | null;
}

interface MetaAgentSubTask {
  name: string;
  description: string;
  reason?: string | null;
  inputs: MetaAgentParameter[];
  outputs: MetaAgentParameter[];
  agent?: MetaAgentGeneratedAgent | null;
  agents?: MetaAgentGeneratedAgent[] | null;
}

interface MetaAgentPlan {
  thought: string;
  sub_tasks: MetaAgentSubTask[];
}

interface ValidationIssue {
  code: string;
  message: string;
  severity: "error" | "warning";
  node_id?: string | null;
  edge_id?: string | null;
}

interface MetaAgentValidation {
  valid: boolean;
  issues: ValidationIssue[];
  order: string[];
  node_count: number;
  edge_count: number;
}

interface MetaAgentGenerateResponse {
  goal: string;
  plan: MetaAgentPlan;
  workflow: WorkflowDefinition;
  warnings: string[];
  validation: MetaAgentValidation;
}

interface AgentTaskSummary {
  task_id: string;
  title: string;
  status: string;
  assigned_agent: string | null;
  created_at: number;
  updated_at: number;
}

interface AgentTaskDetail extends AgentTaskSummary {
  input: string;
  result: string | null;
  error: string | null;
  source_agent: string | null;
  metadata: Record<string, unknown>;
}

interface AgentHandoffSummary {
  handoff_id: string;
  task_id: string;
  source_agent: string;
  target_agent: string;
  reason: string;
  status: string;
  metadata: Record<string, unknown>;
  created_at: number;
  updated_at: number;
}

interface AuthoringProposalSummary {
  proposal_id: string;
  kind: string;
  source_id: string;
  status: string;
}

const nodeTypes = {
  workflowNode: WorkflowNodeCard,
};

const goalTemplates = [
  {
    title: "市场调研",
    goal: "为一款面向中小团队的 AI 模型浏览器生成市场调研工作流，输出竞品洞察、用户画像和行动建议。",
  },
  {
    title: "发布计划",
    goal: "为新增元智能体工作台制定发布计划，包含需求拆解、风险评估、验收标准和上线清单。",
  },
  {
    title: "代码原型",
    goal: "生成一个浏览器端待办事项应用的开发工作流，拆分需求、界面、实现、测试和交付说明。",
  },
];

const plannerModelOptions = models
  .filter(
    (model) =>
      model.active &&
      model.input_modalities.includes("text") &&
      model.capabilities.includes("text"),
  )
  .slice(0, 120);

const defaultPlannerModel =
  plannerModelOptions.find((model) => model.id === "openai/gpt-5.6-sol")?.id ??
  plannerModelOptions[0]?.id ??
  "openai/gpt-5.6-sol";

function compactName(value: string) {
  return value.replace(/_/g, " ");
}

function errorMessage(payload: unknown, fallback: string) {
  if (!payload || typeof payload !== "object") return fallback;
  const detail = "detail" in payload ? payload.detail : undefined;
  const error = "error" in payload ? payload.error : undefined;
  if (typeof error === "string") return error;
  if (typeof detail === "string") return detail;
  if (detail) return JSON.stringify(detail);
  return fallback;
}

function agentTaskTitle(goal: string) {
  const compactGoal = goal.replace(/\s+/g, " ").trim();
  const suffix = compactGoal.length > 60 ? "..." : "";
  return `元智能体任务：${compactGoal.slice(0, 60)}${suffix}`;
}

function formatTaskTime(value: number | null | undefined) {
  if (!value) return "-";
  return new Date(value * 1000).toLocaleString();
}

function taskStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "待处理",
    running: "运行中",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };
  return labels[status] ?? status;
}

function taskStatusClass(status: string) {
  if (status === "completed") return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  if (status === "failed") return "border-rose-300/25 bg-rose-300/10 text-rose-100";
  if (status === "cancelled") return "border-slate-400/20 bg-slate-400/10 text-slate-300";
  if (status === "running") return "border-cyan-300/25 bg-cyan-300/10 text-cyan-100";
  return "border-hire-300/25 bg-hire-300/10 text-hire-100";
}

function handoffStatusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "待处理",
    accepted: "已接受",
    retry_wait: "等待重试",
    rejected: "已拒绝",
    completed: "已完成",
    dead_letter: "死信",
  };
  return labels[status] ?? status;
}

function handoffStatusClass(status: string) {
  if (status === "completed") return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  if (status === "rejected") return "border-rose-300/25 bg-rose-300/10 text-rose-100";
  if (status === "dead_letter") return "border-rose-300/35 bg-rose-300/15 text-rose-100";
  if (status === "retry_wait") return "border-amber-300/25 bg-amber-300/10 text-amber-100";
  if (status === "accepted") return "border-cyan-300/25 bg-cyan-300/10 text-cyan-100";
  return "border-hire-300/25 bg-hire-300/10 text-hire-100";
}

function handoffMetaText(handoff: AgentHandoffSummary, key: string) {
  const value = handoff.metadata?.[key];
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function handoffMetaTime(handoff: AgentHandoffSummary, key: string) {
  const value = handoff.metadata?.[key];
  return typeof value === "number" ? formatTaskTime(value) : "";
}

function nodeColor(node: WorkflowNode) {
  const data = node.data as WorkflowNodeData;
  if (data.kind === "input") return "rgba(16,185,129,0.92)";
  if (data.kind === "agent") return "rgba(167,139,250,0.92)";
  if (data.kind === "variable_aggregator") return "rgba(251,146,60,0.92)";
  if (data.kind === "output") return "rgba(125,211,252,0.92)";
  return "rgba(148,163,184,0.92)";
}

function GraphPreview({ workflow }: { workflow: WorkflowDefinition | null }) {
  const nodes = useMemo(
    () =>
      (workflow?.nodes ?? []).map((node) => ({
        ...node,
        connectable: false,
        draggable: false,
        selectable: false,
      })),
    [workflow],
  );
  const edges = useMemo(
    () =>
      (workflow?.edges ?? []).map((edge) => ({
        ...edge,
        selectable: false,
      })),
    [workflow],
  );

  if (!workflow) {
    return (
      <div className="flex h-full min-h-[520px] items-center justify-center rounded-lg border border-dashed border-white/15 bg-white/[0.025] px-6 text-center">
        <div className="max-w-sm">
          <p className="text-sm font-semibold text-slate-200">等待生成</p>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            输入目标后会在这里出现可导入的原生 React Flow 工作流草稿。
          </p>
        </div>
      </div>
    );
  }

  return (
    <ReactFlow
      edges={edges as WorkflowEdge[]}
      elementsSelectable={false}
      fitView
      minZoom={0.35}
      nodes={nodes as WorkflowNode[]}
      nodesConnectable={false}
      nodesDraggable={false}
      nodeTypes={nodeTypes}
      panOnDrag
      panOnScroll
      proOptions={{ hideAttribution: true }}
      zoomOnScroll
    >
      <Background
        color="rgba(253, 186, 116, 0.18)"
        gap={24}
        variant={BackgroundVariant.Dots}
      />
      <Controls className="modelmirror-flow-controls" />
      <MiniMap
        maskColor="rgba(6, 9, 22, 0.72)"
        nodeColor={(node) => nodeColor(node as WorkflowNode)}
        pannable
        zoomable
      />
    </ReactFlow>
  );
}

function VariableList({ items }: { items: MetaAgentParameter[] }) {
  if (items.length === 0) {
    return <span className="text-xs text-slate-500">none</span>;
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <span
          className="rounded-md border border-white/10 bg-white/[0.055] px-2 py-1 text-[11px] font-semibold text-slate-200"
          key={`${item.name}-${item.type}`}
        >
          {item.name}
        </span>
      ))}
    </div>
  );
}

export default function MetaAgentPage() {
  const navigate = useNavigate();
  const [goal, setGoal] = useState(goalTemplates[1].goal);
  const [modelId, setModelId] = useState(defaultPlannerModel);
  const [temperature, setTemperature] = useState(0.3);
  const [maxTasks, setMaxTasks] = useState(5);
  const [result, setResult] = useState<MetaAgentGenerateResponse | null>(null);
  const [error, setError] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const [agentTasks, setAgentTasks] = useState<AgentTaskSummary[]>([]);
  const [selectedTask, setSelectedTask] = useState<AgentTaskDetail | null>(null);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [tasksError, setTasksError] = useState("");
  const [taskActionError, setTaskActionError] = useState("");
  const [cancellingTaskId, setCancellingTaskId] = useState<string | null>(null);
  const [selectedTaskHandoffs, setSelectedTaskHandoffs] = useState<
    AgentHandoffSummary[]
  >([]);
  const [handoffInbox, setHandoffInbox] = useState<AgentHandoffSummary[]>([]);
  const [handoffAuthoringProposals, setHandoffAuthoringProposals] = useState<
    AuthoringProposalSummary[]
  >([]);
  const [handoffError, setHandoffError] = useState("");
  const [handoffActionId, setHandoffActionId] = useState<string | null>(null);
  const [handoffStatusFilter, setHandoffStatusFilter] = useState("all");
  const [handoffTargetFilter, setHandoffTargetFilter] = useState("");
  const [handoffCompleteDrafts, setHandoffCompleteDrafts] = useState<
    Record<string, string>
  >({});
  const [expandedCompleteHandoffId, setExpandedCompleteHandoffId] =
    useState<string | null>(null);

  useEffect(() => {
    document.title = "模镜 - 元智能体工作台";
  }, []);

  useEffect(() => {
    void loadAgentTasks();
    void loadHandoffInbox();
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      void loadHandoffInbox();
      if (selectedTask?.task_id) {
        void loadAgentTask(selectedTask.task_id);
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [handoffStatusFilter, handoffTargetFilter, selectedTask?.task_id]);

  async function loadAgentTaskHandoffs(taskId: string) {
    try {
      const response = await fetch(`/api/runtime/agent-tasks/${taskId}/handoffs`);
      const payload = (await response.json().catch(() => null)) as
        | AgentHandoffSummary[]
        | { error?: string; detail?: unknown }
        | null;
      if (!response.ok || !Array.isArray(payload)) {
        throw new Error(errorMessage(payload, "Handoff records failed to load."));
      }
      setSelectedTaskHandoffs(payload);
      setHandoffError("");
    } catch (handoffLoadError) {
      setSelectedTaskHandoffs([]);
      setHandoffError(
        handoffLoadError instanceof Error
          ? handoffLoadError.message
          : "Handoff records failed to load.",
      );
    }
  }

  async function loadHandoffInbox() {
    try {
      const params = new URLSearchParams({ limit: "20" });
      if (handoffStatusFilter !== "all") {
        params.set("status", handoffStatusFilter);
      }
      const targetFilter = handoffTargetFilter.trim();
      if (targetFilter) {
        params.set("target_agent", targetFilter);
      }
      const response = await fetch(`/api/runtime/agent-handoffs?${params}`);
      const payload = (await response.json().catch(() => null)) as
        | AgentHandoffSummary[]
        | { error?: string; detail?: unknown }
        | null;
      if (!response.ok || !Array.isArray(payload)) {
        throw new Error(errorMessage(payload, "Handoff Inbox failed to load."));
      }
      setHandoffInbox(payload);
      try {
        const proposalResponse = await fetch(
          "/api/runtime/authoring-proposals?status=pending&limit=500",
        );
        if (proposalResponse.ok) {
          const proposalPayload = (await proposalResponse.json()) as {
            items?: AuthoringProposalSummary[];
          };
          setHandoffAuthoringProposals(proposalPayload.items ?? []);
        }
      } catch {
        // Proposal summaries are optional and must not break Handoff Inbox.
      }
      setHandoffError("");
    } catch (handoffLoadError) {
      setHandoffError(
        handoffLoadError instanceof Error
          ? handoffLoadError.message
          : "Handoff Inbox failed to load.",
      );
    }
  }

  async function loadAgentTask(taskId: string) {
    try {
      const response = await fetch(`/api/runtime/agent-tasks/${taskId}`);
      const payload = (await response.json().catch(() => null)) as
        | AgentTaskDetail
        | { error?: string; detail?: unknown }
        | null;
      if (!response.ok) {
        throw new Error(errorMessage(payload, "任务详情加载失败。"));
      }
      setSelectedTask(payload as AgentTaskDetail);
      await loadAgentTaskHandoffs(taskId);
      setTaskActionError("");
    } catch (taskError) {
      setTaskActionError(
        taskError instanceof Error ? taskError.message : "任务详情加载失败。",
      );
    }
  }

  async function loadAgentTasks(selectTaskId?: string) {
    setTasksLoading(true);
    try {
      const response = await fetch("/api/runtime/agent-tasks?limit=20");
      const payload = (await response.json().catch(() => null)) as
        | AgentTaskSummary[]
        | { error?: string; detail?: unknown }
        | null;
      if (!response.ok || !Array.isArray(payload)) {
        throw new Error(errorMessage(payload, "任务列表加载失败。"));
      }
      setAgentTasks(payload);
      setTasksError("");
      if (selectTaskId) {
        await loadAgentTask(selectTaskId);
      }
    } catch (taskError) {
      setTasksError(
        taskError instanceof Error ? taskError.message : "任务列表加载失败。",
      );
    } finally {
      setTasksLoading(false);
    }
  }

  async function createAgentTaskForResult(
    generated: MetaAgentGenerateResponse,
    trimmedGoal: string,
  ) {
    try {
      const response = await fetch("/api/runtime/agent-tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: agentTaskTitle(trimmedGoal),
          input: trimmedGoal,
          source_agent: "meta-agent",
          assigned_agent: "workflow-planner",
          metadata: {
            model_id: modelId,
            max_tasks: maxTasks,
            workflow_id: generated.workflow.id,
            workflow_title: generated.workflow.title,
            sub_tasks_count: generated.plan.sub_tasks.length,
            validation_valid: generated.validation.valid,
          },
        }),
      });
      const payload = (await response.json().catch(() => null)) as
        | { task_id?: string }
        | { error?: string; detail?: unknown }
        | null;
      if (!response.ok || !payload || !("task_id" in payload) || !payload.task_id) {
        throw new Error(errorMessage(payload, "任务记录创建失败。"));
      }
      setTaskActionError("");
      await loadAgentTasks(payload.task_id);
    } catch (taskError) {
      setTaskActionError(
        taskError instanceof Error
          ? `工作流已生成，但${taskError.message}`
          : "工作流已生成，但任务记录创建失败。",
      );
    }
  }

  async function cancelAgentTask(taskId: string) {
    setCancellingTaskId(taskId);
    try {
      const response = await fetch(`/api/runtime/agent-tasks/${taskId}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "用户在元智能体工作台取消" }),
      });
      const payload = (await response.json().catch(() => null)) as
        | { error?: string; detail?: unknown }
        | null;
      if (!response.ok) {
        throw new Error(errorMessage(payload, "取消任务失败。"));
      }
      setTaskActionError("");
      await loadAgentTasks(taskId);
    } catch (taskError) {
      setTaskActionError(
        taskError instanceof Error ? taskError.message : "取消任务失败。",
      );
    } finally {
      setCancellingTaskId(null);
    }
  }

  async function updateHandoffStatus(
    handoff: AgentHandoffSummary,
    action: "accept" | "reject" | "complete" | "execute" | "requeue",
  ) {
    setHandoffActionId(handoff.handoff_id);
    try {
      const operator = "meta-agent-operator";
      const completionResult =
        handoffCompleteDrafts[handoff.handoff_id]?.trim() ||
        "Completed in MetaAgent handoff inbox.";
      const body =
        action === "requeue"
          ? {
              operator,
              reset_attempts: true,
              repin_version: true,
            }
          : action === "reject"
          ? {
              rejected_by: operator,
              reason: "Rejected in MetaAgent handoff inbox.",
            }
          : action === "complete"
            ? {
                completed_by: operator,
                result: completionResult,
              }
            : { accepted_by: operator };
      const response = await fetch(
        `/api/runtime/agent-handoffs/${handoff.handoff_id}/${action}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      const payload = (await response.json().catch(() => null)) as
        | AgentHandoffSummary
        | { error?: string; detail?: unknown }
        | null;
      if (!response.ok) {
        throw new Error(errorMessage(payload, "Handoff status update failed."));
      }
      setHandoffError("");
      await loadHandoffInbox();
      if (selectedTask?.task_id) {
        await loadAgentTask(selectedTask.task_id);
      }
      setExpandedCompleteHandoffId(null);
    } catch (handoffActionError) {
      setHandoffError(
        handoffActionError instanceof Error
          ? handoffActionError.message
          : "Handoff status update failed.",
      );
    } finally {
      setHandoffActionId(null);
    }
  }

  function renderHandoffActions(handoff: AgentHandoffSummary) {
    const busy = handoffActionId === handoff.handoff_id;
    const automatic =
      handoffMetaText(handoff, "execution_mode") === "xpert_auto" ||
      handoff.target_agent.startsWith("xpert:");
    if (automatic && handoff.status === "pending") {
      return (
        <div className="mt-2 flex gap-2">
          <button
            className="rounded-md border border-cyan-300/25 bg-cyan-300/10 px-2.5 py-1 text-[11px] font-semibold text-cyan-100 transition hover:bg-cyan-300/15 disabled:opacity-50"
            disabled={busy}
            onClick={() => void updateHandoffStatus(handoff, "execute")}
            type="button"
          >
            {busy ? "执行中" : "立即执行"}
          </button>
          <button
            className="rounded-md border border-rose-300/25 bg-rose-300/10 px-2.5 py-1 text-[11px] font-semibold text-rose-100 transition hover:bg-rose-300/15 disabled:opacity-50"
            disabled={busy}
            onClick={() => void updateHandoffStatus(handoff, "reject")}
            type="button"
          >
            拒绝
          </button>
        </div>
      );
    }
    if (
      automatic &&
      (handoff.status === "retry_wait" || handoff.status === "dead_letter")
    ) {
      return (
        <button
          className="mt-2 rounded-md border border-amber-300/25 bg-amber-300/10 px-2.5 py-1 text-[11px] font-semibold text-amber-100 transition hover:bg-amber-300/15 disabled:opacity-50"
          disabled={busy}
          onClick={() => void updateHandoffStatus(handoff, "requeue")}
          type="button"
        >
          {busy ? "重新入队中" : "重新入队"}
        </button>
      );
    }
    if (automatic && handoff.status === "accepted") {
      return (
        <p className="mt-2 text-[11px] text-cyan-200">
          目标智能体正在执行，完成后会自动回写结果。
        </p>
      );
    }
    if (handoff.status === "pending") {
      return (
        <div className="mt-2 flex gap-2">
          <button
            className="rounded-full border border-cyan-300/25 bg-cyan-300/10 px-2.5 py-1 text-[11px] font-semibold text-cyan-100 transition hover:bg-cyan-300/15 disabled:opacity-50"
            disabled={busy}
            onClick={() => void updateHandoffStatus(handoff, "accept")}
            type="button"
          >
            接受
          </button>
          <button
            className="rounded-full border border-rose-300/25 bg-rose-300/10 px-2.5 py-1 text-[11px] font-semibold text-rose-100 transition hover:bg-rose-300/15 disabled:opacity-50"
            disabled={busy}
            onClick={() => void updateHandoffStatus(handoff, "reject")}
            type="button"
          >
            拒绝
          </button>
        </div>
      );
    }
    if (handoff.status === "accepted") {
      const expanded = expandedCompleteHandoffId === handoff.handoff_id;
      return (
        <div className="mt-2 space-y-2">
          {expanded ? (
            <textarea
              className="min-h-16 w-full resize-none rounded-md border border-white/10 bg-slate-950/35 px-2 py-1.5 text-[11px] leading-5 text-slate-200 outline-none transition placeholder:text-slate-500 focus:border-emerald-300/45 focus:ring-2 focus:ring-emerald-300/10"
              onChange={(event) =>
                setHandoffCompleteDrafts((current) => ({
                  ...current,
                  [handoff.handoff_id]: event.target.value,
                }))
              }
              placeholder="填写完成结果摘要"
              value={handoffCompleteDrafts[handoff.handoff_id] ?? ""}
            />
          ) : null}
          <div className="flex gap-2">
            <button
              className="rounded-full border border-emerald-300/25 bg-emerald-300/10 px-2.5 py-1 text-[11px] font-semibold text-emerald-100 transition hover:bg-emerald-300/15 disabled:opacity-50"
              disabled={busy}
              onClick={() => {
                if (!expanded) {
                  setExpandedCompleteHandoffId(handoff.handoff_id);
                  return;
                }
                void updateHandoffStatus(handoff, "complete");
              }}
              type="button"
            >
              {expanded ? "提交完成" : "填写结果"}
            </button>
            {expanded ? (
              <button
                className="rounded-full border border-white/10 bg-white/[0.045] px-2.5 py-1 text-[11px] font-semibold text-slate-300 transition hover:bg-white/[0.07] disabled:opacity-50"
                disabled={busy}
                onClick={() => setExpandedCompleteHandoffId(null)}
                type="button"
              >
                收起
              </button>
            ) : null}
          </div>
        </div>
      );
    }
    if (false && handoff.status === "accepted") {
      return (
        <button
          className="mt-2 rounded-full border border-emerald-300/25 bg-emerald-300/10 px-2.5 py-1 text-[11px] font-semibold text-emerald-100 transition hover:bg-emerald-300/15 disabled:opacity-50"
          disabled={busy}
          onClick={() => void updateHandoffStatus(handoff, "complete")}
          type="button"
        >
          完成
        </button>
      );
    }
    return null;
  }

  function renderHandoffMetaSummary(handoff: AgentHandoffSummary) {
    const acceptedBy = handoffMetaText(handoff, "accepted_by");
    const rejectedBy = handoffMetaText(handoff, "rejected_by");
    const completedBy = handoffMetaText(handoff, "completed_by");
    const result = handoffMetaText(handoff, "result");
    const reason = handoffMetaText(handoff, "reason");
    const lastError = handoffMetaText(handoff, "last_error");
    const targetVersion = handoffMetaText(handoff, "target_xpert_version");
    const attempts = handoffMetaText(handoff, "attempts");
    const maxAttempts = handoffMetaText(handoff, "max_attempts");
    const time =
      handoffMetaTime(handoff, "completed_at") ||
      handoffMetaTime(handoff, "rejected_at") ||
      handoffMetaTime(handoff, "accepted_at");
    const handler = completedBy || rejectedBy || acceptedBy;
    if (
      !handler &&
      !result &&
      !reason &&
      !time &&
      !lastError &&
      !targetVersion &&
      !attempts
    )
      return null;
    return (
      <div className="mt-2 rounded-md border border-white/10 bg-slate-950/25 px-2 py-1.5 text-[11px] leading-5 text-slate-400">
        {handler ? <p>处理者：{handler}</p> : null}
        {time ? <p>处理时间：{time}</p> : null}
        {targetVersion ? <p>目标版本：v{targetVersion}</p> : null}
        {attempts ? (
          <p>
            执行尝试：{attempts}
            {maxAttempts ? ` / ${maxAttempts}` : ""}
          </p>
        ) : null}
        {result ? <p className="line-clamp-2">结果：{result}</p> : null}
        {lastError ? <p className="line-clamp-2 text-rose-200">错误：{lastError}</p> : null}
        {reason && handoff.status === "rejected" ? (
          <p className="line-clamp-2">原因：{reason}</p>
        ) : null}
      </div>
    );
  }

  async function generateWorkflow() {
    const trimmedGoal = goal.trim();
    if (trimmedGoal.length < 10) {
      setError("目标至少需要 10 个字符。");
      return;
    }

    setError("");
    setIsGenerating(true);
    try {
      const response = await fetch("/api/meta-agent/generate-workflow", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          goal: trimmedGoal,
          model_id: modelId,
          temperature,
          max_tasks: maxTasks,
        }),
      });
      const payload = (await response.json().catch(() => null)) as
        | MetaAgentGenerateResponse
        | { error?: string; detail?: unknown }
        | null;
      if (!response.ok) {
        throw new Error(errorMessage(payload, "生成失败，请检查模型网关配置。"));
      }
      const generated = payload as MetaAgentGenerateResponse;
      setResult(generated);
      await createAgentTaskForResult(generated, trimmedGoal);
    } catch (generateError) {
      setError(generateError instanceof Error ? generateError.message : "生成失败。");
    } finally {
      setIsGenerating(false);
    }
  }

  function importWorkflow() {
    if (!result) return;
    const workflowId = `meta-agent-${Date.now()}`;
    const imported: WorkflowDefinition = {
      ...result.workflow,
      id: workflowId,
      title: result.workflow.title || "元智能体工作流",
      updatedAt: new Date().toISOString(),
    };
    saveStoredWorkflow(imported);
    navigate(`/workflow/${workflowId}`);
  }

  return (
    <PageContainer
      activeResource="agents"
      contentClassName="min-w-0"
      maxWidthClassName="max-w-[1880px]"
      sidebar={
        <div>
          <p className="text-sm font-semibold text-white">元智能体 Beta</p>
          <p className="mt-2 text-sm leading-6 text-slate-400">
            基于目标生成可编辑工作流草稿，并沿用经典画布运行路径。
          </p>
          <Link
            className="mt-4 inline-flex rounded-full border border-hire-300/30 bg-hire-300/10 px-3 py-1.5 text-xs font-semibold text-hire-100 transition hover:bg-hire-300/20"
            to="/agents"
          >
            返回智能体
          </Link>
        </div>
      }
    >
      <header className="mb-5 overflow-hidden rounded-lg border border-hire-300/20 bg-[linear-gradient(135deg,rgba(67,20,7,0.70),rgba(6,9,22,0.92)_48%,rgba(8,51,68,0.62))] p-5 shadow-prism sm:p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="text-sm font-semibold text-hire-100">
              智能体任务工作台 Beta
            </p>
            <h1 className="mt-2 text-3xl font-semibold text-white sm:text-4xl">
              元智能体工作台
            </h1>
            <p className="mt-3 max-w-3xl text-sm leading-6 text-slate-300">
              从自然语言目标生成 Agent 工作流草稿，校验后可导入经典画布继续编辑。
            </p>
          </div>
          <div className="grid grid-cols-3 gap-2 text-center text-xs text-slate-300">
            <div className="rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2">
              <p className="font-semibold text-white">{result?.plan.sub_tasks.length ?? 0}</p>
              <p className="mt-1">任务</p>
            </div>
            <div className="rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2">
              <p className="font-semibold text-white">
                {result?.workflow.nodes.length ?? 0}
              </p>
              <p className="mt-1">节点</p>
            </div>
            <div className="rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2">
              <p className="font-semibold text-white">
                {result?.validation.valid ? "通过" : result ? "待修" : "待生成"}
              </p>
              <p className="mt-1">校验</p>
            </div>
          </div>
        </div>
      </header>

      <MetaPlannerV2 />

      <div className="grid min-h-[760px] gap-5 xl:grid-cols-[320px_minmax(0,1fr)_380px]">
        <section className="surface-panel rounded-lg p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-white">目标</p>
              <p className="mt-1 text-xs text-slate-400">
                Goal {"->"} sub_tasks {"->"} workflow
              </p>
            </div>
            <span className="rounded-full border border-cyan-300/25 bg-cyan-300/10 px-2.5 py-1 text-xs font-semibold text-cyan-100">
              Beta
            </span>
          </div>

          <div className="mt-4 grid grid-cols-3 gap-2">
            {goalTemplates.map((template) => (
              <button
                className="rounded-lg border border-white/10 bg-white/[0.045] px-2 py-2 text-xs font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
                key={template.title}
                onClick={() => setGoal(template.goal)}
                type="button"
              >
                {template.title}
              </button>
            ))}
          </div>

          <label className="mt-4 block">
            <span className="text-xs font-semibold text-slate-300">目标描述</span>
            <textarea
              className="mt-2 min-h-44 w-full resize-none rounded-lg border border-white/10 bg-white/[0.055] px-3 py-2 text-sm leading-6 text-white outline-none transition placeholder:text-slate-500 focus:border-hire-300/55 focus:ring-4 focus:ring-hire-300/10"
              onChange={(event) => setGoal(event.target.value)}
              value={goal}
            />
          </label>

          <label className="mt-4 block">
            <span className="text-xs font-semibold text-slate-300">规划模型</span>
            <select
              className="mt-2 h-10 w-full rounded-lg border border-white/10 bg-ink-950 px-3 text-sm text-white outline-none transition focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10"
              onChange={(event) => setModelId(event.target.value)}
              value={modelId}
            >
              {plannerModelOptions.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name}
                </option>
              ))}
            </select>
          </label>

          <div className="mt-4 grid grid-cols-2 gap-3">
            <label className="block">
              <span className="text-xs font-semibold text-slate-300">温度</span>
              <input
                className="mt-2 h-10 w-full rounded-lg border border-white/10 bg-white/[0.055] px-3 text-sm text-white outline-none transition focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10"
                max={1.2}
                min={0}
                onChange={(event) => setTemperature(Number(event.target.value))}
                step={0.1}
                type="number"
                value={temperature}
              />
            </label>
            <label className="block">
              <span className="text-xs font-semibold text-slate-300">任务数</span>
              <input
                className="mt-2 h-10 w-full rounded-lg border border-white/10 bg-white/[0.055] px-3 text-sm text-white outline-none transition focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10"
                max={8}
                min={1}
                onChange={(event) => setMaxTasks(Number(event.target.value))}
                type="number"
                value={maxTasks}
              />
            </label>
          </div>

          <button
            className="mt-5 w-full rounded-full bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950 shadow-[0_0_24px_rgba(251,146,60,0.18)] transition hover:bg-hire-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
            disabled={isGenerating}
            onClick={() => void generateWorkflow()}
            type="button"
          >
            {isGenerating ? "生成中..." : "生成工作流"}
          </button>

          {error ? (
            <div className="mt-4 rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-sm leading-6 text-rose-100">
              {error}
            </div>
          ) : null}
        </section>

        <section className="surface-panel min-h-[640px] overflow-hidden rounded-lg">
          <div className="flex items-center justify-between gap-4 border-b border-white/10 px-4 py-3">
            <div>
              <p className="text-sm font-semibold text-white">工作流预览</p>
              <p className="mt-1 text-xs text-slate-400">
                {result?.workflow.title ?? "尚未生成草稿"}
              </p>
            </div>
            {result ? (
              <span className="rounded-full border border-emerald-300/25 bg-emerald-300/10 px-2.5 py-1 text-xs font-semibold text-emerald-100">
                {result.workflow.edges.length} edges
              </span>
            ) : null}
          </div>
          <div className="h-[calc(100%-57px)] min-h-[580px]">
            <GraphPreview workflow={result?.workflow ?? null} />
          </div>
        </section>

        <aside className="min-h-0 space-y-4">
          <section className="surface-panel rounded-lg p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-white">任务工作台</p>
                <p className="mt-1 text-xs text-slate-400">
                  元智能体任务工作台 Beta
                </p>
              </div>
              <button
                className="rounded-full border border-white/10 bg-white/[0.055] px-2.5 py-1 text-xs font-semibold text-slate-300 transition hover:border-hire-300/35 hover:text-hire-100"
                disabled={tasksLoading}
                onClick={() => void loadAgentTasks(selectedTask?.task_id)}
                type="button"
              >
                {tasksLoading ? "刷新中" : "刷新"}
              </button>
            </div>

            {tasksError ? (
              <div className="mt-3 rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-100">
                {tasksError}
              </div>
            ) : null}
            {taskActionError ? (
              <div className="mt-3 rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-100">
                {taskActionError}
              </div>
            ) : null}
            {handoffError ? (
              <div className="mt-3 rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs leading-5 text-amber-100">
                {handoffError}
              </div>
            ) : null}

            <div className="mt-3 max-h-48 space-y-2 overflow-y-auto pr-1">
              {tasksLoading && agentTasks.length === 0 ? (
                <p className="rounded-lg border border-white/10 bg-white/[0.035] px-3 py-2 text-xs text-slate-400">
                  正在加载任务...
                </p>
              ) : agentTasks.length === 0 ? (
                <p className="rounded-lg border border-dashed border-white/15 bg-white/[0.025] px-3 py-3 text-xs leading-5 text-slate-400">
                  暂无任务。生成一个工作流后，会自动创建元智能体任务记录。
                </p>
              ) : (
                agentTasks.map((task) => (
                  <button
                    className={`w-full rounded-lg border px-3 py-2 text-left transition ${
                      selectedTask?.task_id === task.task_id
                        ? "border-hire-300/45 bg-hire-300/10"
                        : "border-white/10 bg-white/[0.045] hover:border-white/20 hover:bg-white/[0.07]"
                    }`}
                    key={task.task_id}
                    onClick={() => void loadAgentTask(task.task_id)}
                    type="button"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="min-w-0 truncate text-xs font-semibold text-white">
                        {task.title}
                      </p>
                      <span
                        className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${taskStatusClass(
                          task.status,
                        )}`}
                      >
                        {taskStatusLabel(task.status)}
                      </span>
                    </div>
                    <p className="mt-1 truncate text-[11px] text-slate-500">
                      {task.assigned_agent ?? "未分配"} · {formatTaskTime(task.created_at)}
                    </p>
                  </button>
                ))
              )}
            </div>

            {selectedTask ? (
              <div className="mt-3 rounded-lg border border-white/10 bg-white/[0.035] p-3">
                <div className="flex items-center justify-between gap-3">
                  <p className="truncate text-xs font-semibold text-slate-200">
                    {selectedTask.title}
                  </p>
                  {selectedTask.metadata?.workflow_id === result?.workflow.id ? (
                    <span className="shrink-0 rounded-full border border-cyan-300/25 bg-cyan-300/10 px-2 py-0.5 text-[10px] font-semibold text-cyan-100">
                      当前草稿
                    </span>
                  ) : null}
                </div>
                <p className="mt-2 line-clamp-3 text-xs leading-5 text-slate-400">
                  {selectedTask.input}
                </p>
                <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-slate-400">
                  <div className="rounded-md border border-white/10 bg-slate-950/30 px-2 py-1.5">
                    <p className="text-slate-500">状态</p>
                    <p className="mt-1 font-semibold text-slate-200">
                      {taskStatusLabel(selectedTask.status)}
                    </p>
                  </div>
                  <div className="rounded-md border border-white/10 bg-slate-950/30 px-2 py-1.5">
                    <p className="text-slate-500">负责 Agent</p>
                    <p className="mt-1 truncate font-semibold text-slate-200">
                      {selectedTask.assigned_agent ?? "未分配"}
                    </p>
                  </div>
                  <div className="rounded-md border border-white/10 bg-slate-950/30 px-2 py-1.5">
                    <p className="text-slate-500">工作流</p>
                    <p className="mt-1 truncate font-semibold text-slate-200">
                      {typeof selectedTask.metadata?.workflow_title === "string"
                        ? selectedTask.metadata.workflow_title
                        : "未关联"}
                    </p>
                  </div>
                  <div className="rounded-md border border-white/10 bg-slate-950/30 px-2 py-1.5">
                    <p className="text-slate-500">子任务</p>
                    <p className="mt-1 font-semibold text-slate-200">
                      {typeof selectedTask.metadata?.sub_tasks_count === "number"
                        ? selectedTask.metadata.sub_tasks_count
                        : "-"}
                    </p>
                  </div>
                </div>
                <div className="mt-3 rounded-md border border-white/10 bg-slate-950/25 p-2">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-[11px] font-semibold text-slate-300">
                      Handoff records
                    </p>
                    <span className="text-[10px] text-slate-500">
                      {selectedTaskHandoffs.length}
                    </span>
                  </div>
                  {selectedTaskHandoffs.length === 0 ? (
                    <p className="mt-2 text-[11px] text-slate-500">
                      No handoff records for this task.
                    </p>
                  ) : (
                    <div className="mt-2 max-h-40 space-y-2 overflow-y-auto pr-1">
                      {selectedTaskHandoffs.map((handoff) => (
                        <div
                          className="rounded-md border border-white/10 bg-white/[0.035] px-2 py-1.5"
                          key={handoff.handoff_id}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <p className="min-w-0 truncate text-[11px] font-semibold text-slate-200">
                              {handoff.source_agent} -&gt; {handoff.target_agent}
                            </p>
                            <span
                              className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${handoffStatusClass(
                                handoff.status,
                              )}`}
                            >
                              {handoffStatusLabel(handoff.status)}
                            </span>
                          </div>
                          <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-slate-500">
                            {handoff.reason}
                          </p>
                          {renderHandoffMetaSummary(handoff)}
                          {renderHandoffActions(handoff)}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                {selectedTask.error ? (
                  <p className="mt-2 rounded-md border border-rose-300/25 bg-rose-300/10 px-2 py-1.5 text-xs text-rose-100">
                    {selectedTask.error}
                  </p>
                ) : null}
                <button
                  className="mt-3 w-full rounded-full border border-white/10 bg-white/[0.055] px-3 py-2 text-xs font-semibold text-slate-200 transition hover:border-rose-300/35 hover:bg-rose-300/10 hover:text-rose-100 disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={
                    selectedTask.status === "cancelled" ||
                    cancellingTaskId === selectedTask.task_id
                  }
                  onClick={() => void cancelAgentTask(selectedTask.task_id)}
                  type="button"
                >
                  {cancellingTaskId === selectedTask.task_id ? "取消中..." : "取消任务"}
                </button>
              </div>
            ) : null}
          </section>

          <section className="surface-panel rounded-lg p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-white">
                  Handoff Inbox Beta
                </p>
                <p className="mt-1 text-xs text-slate-400">
                  人工处理与智能体自动协作
                </p>
              </div>
              <button
                className="rounded-full border border-white/10 bg-white/[0.055] px-2.5 py-1 text-xs font-semibold text-slate-300 transition hover:border-hire-300/35 hover:text-hire-100"
                onClick={() => void loadHandoffInbox()}
                type="button"
              >
                刷新
              </button>
            </div>
            <div className="mt-3 grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_auto] gap-2">
              <select
                className="h-9 rounded-md border border-white/10 bg-slate-950/45 px-2 text-xs text-slate-200 outline-none transition focus:border-hire-300/45 focus:ring-2 focus:ring-hire-300/10"
                onChange={(event) => setHandoffStatusFilter(event.target.value)}
                value={handoffStatusFilter}
              >
                <option value="all">全部状态</option>
                <option value="pending">待处理</option>
                <option value="accepted">已接受</option>
                <option value="retry_wait">等待重试</option>
                <option value="rejected">已拒绝</option>
                <option value="completed">已完成</option>
                <option value="dead_letter">死信</option>
              </select>
              <input
                className="h-9 rounded-md border border-white/10 bg-slate-950/45 px-2 text-xs text-slate-200 outline-none transition placeholder:text-slate-500 focus:border-hire-300/45 focus:ring-2 focus:ring-hire-300/10"
                onChange={(event) => setHandoffTargetFilter(event.target.value)}
                placeholder="target agent"
                value={handoffTargetFilter}
              />
              <button
                className="h-9 rounded-md border border-white/10 bg-white/[0.055] px-3 text-xs font-semibold text-slate-300 transition hover:border-hire-300/35 hover:text-hire-100"
                onClick={() => void loadHandoffInbox()}
                type="button"
              >
                应用
              </button>
            </div>
            <div className="mt-3">
              <RuntimeApprovalPanel
                compact
                onResolved={async () => {
                  await loadHandoffInbox();
                  if (selectedTask?.task_id) {
                    await loadAgentTaskHandoffs(selectedTask.task_id);
                  }
                }}
                scopeType="handoff"
                title="Handoff 审批"
              />
            </div>
            <div className="mt-3 max-h-64 space-y-2 overflow-y-auto pr-1">
              {handoffInbox.length === 0 ? (
                <p className="rounded-lg border border-dashed border-white/15 bg-white/[0.025] px-3 py-3 text-xs leading-5 text-slate-400">
                  No recent handoffs.
                </p>
              ) : (
                handoffInbox.map((handoff) => (
                  <article
                    className="rounded-lg border border-white/10 bg-white/[0.045] p-3"
                    key={handoff.handoff_id}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-xs font-semibold text-white">
                          {handoff.source_agent} -&gt; {handoff.target_agent}
                        </p>
                        <p className="mt-1 break-all font-mono text-[10px] text-slate-500">
                          {handoff.handoff_id}
                        </p>
                        {handoff.target_agent.startsWith("xpert:") ? (
                          <span className="mt-1 inline-flex rounded-md border border-violet-300/25 bg-violet-300/10 px-1.5 py-0.5 text-[10px] font-semibold text-violet-100">
                            智能体自动执行
                          </span>
                        ) : null}
                      </div>
                      <span
                        className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${handoffStatusClass(
                          handoff.status,
                        )}`}
                      >
                        {handoffStatusLabel(handoff.status)}
                      </span>
                    </div>
                    <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-400">
                      {handoff.reason}
                    </p>
                    {renderHandoffMetaSummary(handoff)}
                    {handoffAuthoringProposals.some(
                      (proposal) => proposal.source_id === handoff.handoff_id,
                    ) ? (
                      <div className="mt-2 flex items-center justify-between gap-2 rounded-md border border-violet-300/20 bg-violet-300/[0.07] px-2 py-1.5 text-[10px] text-violet-100">
                        <span>
                          {
                            handoffAuthoringProposals.filter(
                              (proposal) => proposal.source_id === handoff.handoff_id,
                            ).length
                          } 条自编写提案待审核
                        </span>
                        <span className="flex gap-2">
                          <Link to="/agents/studio">Agent Studio</Link>
                          <Link to="/skills?tab=proposals">Skill</Link>
                        </span>
                      </div>
                    ) : null}
                    <p className="mt-1 break-all text-[11px] text-slate-500">
                      task: {handoff.task_id}
                    </p>
                    {renderHandoffActions(handoff)}
                    <div className="mt-3 overflow-hidden rounded-lg border border-white/10">
                      <SandboxWorkspacePanel
                        compact
                        scopeId={handoff.handoff_id}
                        scopeType="handoff"
                      />
                      <BrowserSessionPanel
                        compact
                        scopeId={handoff.handoff_id}
                        scopeType="handoff"
                      />
                      <ClientToolPanel
                        compact
                        onResolved={() => loadHandoffInbox()}
                        scopeId={handoff.handoff_id}
                        scopeType="handoff"
                      />
                    </div>
                  </article>
                ))
              )}
            </div>
          </section>

          <section className="surface-panel rounded-lg p-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-semibold text-white">计划</p>
              {result ? (
                <span className="rounded-full border border-white/10 bg-white/[0.055] px-2.5 py-1 text-xs text-slate-300">
                  {result.plan.sub_tasks.length} steps
                </span>
              ) : null}
            </div>
            {result ? (
              <div className="mt-3 max-h-72 space-y-3 overflow-y-auto pr-1">
                {result.plan.thought ? (
                  <p className="rounded-lg border border-white/10 bg-white/[0.045] p-3 text-xs leading-5 text-slate-300">
                    {result.plan.thought}
                  </p>
                ) : null}
                {result.plan.sub_tasks.map((task, index) => (
                  <article
                    className="rounded-lg border border-white/10 bg-white/[0.045] p-3"
                    key={`${task.name}-${index}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-white">
                          {index + 1}. {compactName(task.name)}
                        </p>
                        <p className="mt-1 text-xs leading-5 text-slate-400">
                          {task.description}
                        </p>
                      </div>
                    </div>
                    <div className="mt-3 grid gap-2">
                      <div>
                        <p className="mb-1 text-[11px] font-semibold text-slate-500">
                          Inputs
                        </p>
                        <VariableList items={task.inputs} />
                      </div>
                      <div>
                        <p className="mb-1 text-[11px] font-semibold text-slate-500">
                          Outputs
                        </p>
                        <VariableList items={task.outputs} />
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-sm leading-6 text-slate-400">
                生成后会显示拆解任务、输入输出变量和 Agent 提示词来源。
              </p>
            )}
          </section>

          <section className="surface-panel rounded-lg p-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-semibold text-white">校验与导入</p>
              {result ? (
                <span
                  className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${
                    result.validation.valid
                      ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                      : "border-rose-300/25 bg-rose-300/10 text-rose-100"
                  }`}
                >
                  {result.validation.valid ? "valid" : "invalid"}
                </span>
              ) : null}
            </div>

            {result ? (
              <div className="mt-3 space-y-3">
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <div className="rounded-lg border border-white/10 bg-white/[0.045] p-3">
                    <p className="text-slate-500">Nodes</p>
                    <p className="mt-1 text-lg font-semibold text-white">
                      {result.validation.node_count}
                    </p>
                  </div>
                  <div className="rounded-lg border border-white/10 bg-white/[0.045] p-3">
                    <p className="text-slate-500">Edges</p>
                    <p className="mt-1 text-lg font-semibold text-white">
                      {result.validation.edge_count}
                    </p>
                  </div>
                </div>

                {result.warnings.length ? (
                  <div className="rounded-lg border border-amber-300/25 bg-amber-300/10 p-3 text-xs leading-5 text-amber-100">
                    {result.warnings.map((warning) => (
                      <p key={warning}>{warning}</p>
                    ))}
                  </div>
                ) : null}

                {result.validation.issues.length ? (
                  <div className="max-h-36 space-y-2 overflow-y-auto">
                    {result.validation.issues.map((issue) => (
                      <div
                        className="rounded-lg border border-rose-300/25 bg-rose-300/10 p-3 text-xs leading-5 text-rose-100"
                        key={`${issue.code}-${issue.node_id ?? issue.edge_id ?? "graph"}`}
                      >
                        <p className="font-semibold">{issue.code}</p>
                        <p className="mt-1">{issue.message}</p>
                      </div>
                    ))}
                  </div>
                ) : null}

                <button
                  className="w-full rounded-full bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
                  disabled={!result.validation.valid}
                  onClick={importWorkflow}
                  type="button"
                >
                  导入经典画布
                </button>
              </div>
            ) : (
              <p className="mt-3 text-sm leading-6 text-slate-400">
                校验通过后可以保存为本地草稿，并进入经典画布继续编辑。
              </p>
            )}
          </section>

          {result ? <WorkflowRun definition={result.workflow} /> : null}
        </aside>
      </div>
    </PageContainer>
  );
}
