import { useMemo, useState } from "react";
import RuntimeApprovalPanel from "../runtime/RuntimeApprovalPanel";
import BrowserSessionPanel from "../runtime/BrowserSessionPanel";
import ClientToolPanel from "../runtime/ClientToolPanel";
import SandboxWorkspacePanel from "../runtime/SandboxWorkspacePanel";
import DataXResultCard from "../datax/DataXResultCard";
import {
  type WorkflowDefinition,
  type WorkflowRunEvent,
} from "../../types/workflow";

interface WorkflowObservationEvent {
  id: string;
  type: string;
  payload: Record<string, unknown>;
  task_id?: string | null;
  trace_id?: string | null;
  severity: string;
  created_at: number;
}

interface WorkflowToolAuditRecord {
  record_id: string;
  tool_name: string;
  status: string;
  started_at: number;
  finished_at: number | null;
  duration_ms: number | null;
  output_length: number | null;
  content_types?: string[];
  error: string | null;
}

interface WorkflowObservationData {
  task_id: string;
  events: WorkflowObservationEvent[];
  event_count: number;
  tool_audit_records: WorkflowToolAuditRecord[];
  tool_audit_count: number;
}

interface RuntimeRunSummary {
  run_id: string;
  run_type: string;
  status: string;
  title: string;
  source_id: string | null;
  parent_run_id: string | null;
  metadata: Record<string, unknown>;
  created_at: number;
  updated_at: number;
  cancelled_at: number | null;
  error: string | null;
}

interface RuntimeRunCheckpoint {
  checkpoint_id: string;
  run_id: string;
  event_type: string;
  title: string;
  summary: string;
  severity: string;
  metadata: Record<string, unknown>;
  created_at: number;
}

interface WorkflowRunProps {
  definition: WorkflowDefinition;
  embedded?: boolean;
  onRunStart?: () => void;
}

interface PendingHumanIntervention {
  nodeId: string;
  nodeTitle: string;
  prompt: string;
  outputVariable: string;
}

type RunStepStatus = "running" | "done" | "waiting" | "error";

interface WorkflowRunStep {
  id: string;
  title: string;
  type?: WorkflowRunEvent["node_type"];
  status: RunStepStatus;
  output: string;
  variable?: string;
}

function serializeWorkflow(definition: WorkflowDefinition) {
  return {
    id: definition.id,
    title: definition.title,
    nodes: definition.nodes.map((node) => ({
      id: node.id,
      type: node.data.kind,
      position: node.position,
      data: node.data,
    })),
    edges: definition.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle,
      targetHandle: edge.targetHandle,
    })),
  };
}

function readSseEvent(eventText: string) {
  return eventText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim())
    .filter(Boolean);
}

function appendStepOutput(
  current: string,
  next: string | undefined,
  nodeType: WorkflowRunEvent["node_type"],
) {
  if (!next) return current;
  if (!current) return next;
  if (nodeType === "llm") return `${current}${next}`;
  return `${current}\n${next}`;
}

function statusCopy(status: RunStepStatus) {
  if (status === "done") return "完成";
  if (status === "waiting") return "等待输入";
  if (status === "error") return "异常";
  return "运行中";
}

function statusClass(status: RunStepStatus) {
  if (status === "done") {
    return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  }
  if (status === "waiting") {
    return "border-sky-300/25 bg-sky-300/10 text-sky-100";
  }
  if (status === "error") {
    return "border-rose-300/25 bg-rose-300/10 text-rose-100";
  }
  return "border-hire-300/25 bg-hire-300/10 text-hire-100";
}

function formatObservationTime(value: number | null | undefined) {
  if (!value) return "";
  return new Date(value * 1000).toLocaleTimeString();
}

function observationPayloadSummary(payload: Record<string, unknown>) {
  const toolName = payload.tool_name;
  const outputLength = payload.output_length;
  const contentTypes = payload.content_types;
  const error = payload.error;
  const parts: string[] = [];
  if (typeof toolName === "string" && toolName) {
    parts.push(`tool=${toolName}`);
  }
  if (typeof outputLength === "number") {
    parts.push(`output=${outputLength}`);
  }
  if (Array.isArray(contentTypes) && contentTypes.length > 0) {
    parts.push(`types=${contentTypes.join(",")}`);
  }
  if (typeof error === "string" && error) {
    parts.push(`error=${error}`);
  }
  return parts.join(" · ");
}

function runMetadataText(
  metadata: Record<string, unknown> | undefined,
  key: string,
) {
  const value = metadata?.[key];
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function checkpointSeverityClass(severity: string) {
  if (severity === "error") return "text-rose-200";
  if (severity === "warning") return "text-amber-200";
  return "text-slate-300";
}

function RuntimeCheckpointList({
  checkpoints,
  limit = 6,
}: {
  checkpoints: RuntimeRunCheckpoint[];
  limit?: number;
}) {
  if (checkpoints.length === 0) {
    return (
      <p className="mt-2 rounded-md bg-slate-950/25 px-2 py-1.5 text-[11px] text-slate-500">
        暂无 checkpoint。
      </p>
    );
  }

  return (
    <div className="mt-2 max-h-44 space-y-1 overflow-y-auto">
      {checkpoints.slice(0, limit).map((checkpoint) => (
        <div
          className="rounded-md bg-slate-950/35 px-2 py-1.5"
          key={checkpoint.checkpoint_id}
        >
          <div className="flex items-center justify-between gap-2">
            <span
              className={`truncate text-[11px] font-semibold ${checkpointSeverityClass(
                checkpoint.severity,
              )}`}
            >
              {checkpoint.title || checkpoint.event_type}
            </span>
            <span className="shrink-0 text-[10px] text-slate-500">
              {formatObservationTime(checkpoint.created_at)}
            </span>
          </div>
          <p className="mt-1 truncate text-[11px] text-slate-500">
            {checkpoint.event_type}
            {checkpoint.summary ? ` · ${checkpoint.summary}` : ""}
          </p>
        </div>
      ))}
      {checkpoints.length > limit ? (
        <p className="text-[11px] text-slate-500">
          仅展示最近 {limit} 条，共 {checkpoints.length} 条。
        </p>
      ) : null}
    </div>
  );
}

function buildRunSteps(events: WorkflowRunEvent[]) {
  const steps: WorkflowRunStep[] = [];
  const byNodeId = new Map<string, WorkflowRunStep>();

  function getStep(event: WorkflowRunEvent, index: number) {
    const id = event.node_id ?? `workflow-${index}`;
    const existing = byNodeId.get(id);
    if (existing) {
      existing.title = event.node_title ?? existing.title;
      existing.type = event.node_type ?? existing.type;
      return existing;
    }

    const step: WorkflowRunStep = {
      id,
      title: event.node_title ?? "工作流",
      type: event.node_type,
      status: "running",
      output: "",
      variable: event.variable ?? event.output_variable,
    };
    byNodeId.set(id, step);
    steps.push(step);
    return step;
  }

  events.forEach((event, index) => {
    if (event.event === "workflow_meta" || event.event === "workflow_end") {
      return;
    }
    if (event.event === "error" && !event.node_id) {
      steps.push({
        id: `workflow-error-${index}`,
        title: "工作流",
        status: "error",
        output: event.message ?? "工作流运行异常。",
      });
      return;
    }

    const step = getStep(event, index);
    if (event.event === "human_intervention_pending") {
      step.status = "waiting";
      step.output = appendStepOutput(step.output, event.prompt, step.type);
      step.variable = event.output_variable ?? step.variable;
      return;
    }
    if (event.event === "runtime_approval_pending") {
      step.status = "waiting";
      step.output = appendStepOutput(
        step.output,
        event.message || `等待审批${event.tool_name ? `：${event.tool_name}` : ""}`,
        step.type,
      );
      return;
    }
    if (event.event === "runtime_approval_resolved") {
      step.status = "running";
      step.output = appendStepOutput(step.output, event.message || "审批已处理，继续执行。", step.type);
      return;
    }
    if (event.event === "client_tool_waiting") {
      step.status = "waiting";
      step.output = appendStepOutput(
        step.output,
        event.message || `等待客户端工具：${event.tool_name ?? "client tool"}`,
        step.type,
      );
      return;
    }
    if (event.event === "node_delta") {
      step.output = appendStepOutput(step.output, event.output, step.type);
      return;
    }
    if (event.event === "node_end") {
      step.status = "done";
      step.variable = event.variable ?? step.variable;
      if (!step.output) {
        step.output = appendStepOutput(step.output, event.output, step.type);
      }
      return;
    }
    if (event.event === "error") {
      step.status = "error";
      step.output = appendStepOutput(step.output, event.message, step.type);
    }
  });

  return steps;
}

export default function WorkflowRun({
  definition,
  embedded = false,
  onRunStart,
}: WorkflowRunProps) {
  const [input, setInput] = useState("请帮我把这个需求拆成三步执行计划。");
  const [events, setEvents] = useState<WorkflowRunEvent[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [pendingHuman, setPendingHuman] =
    useState<PendingHumanIntervention | null>(null);
  const [humanInput, setHumanInput] = useState("");
  const [isResuming, setIsResuming] = useState(false);
  const [showObservation, setShowObservation] = useState(false);
  const [observationData, setObservationData] =
    useState<WorkflowObservationData | null>(null);
  const [observationLoading, setObservationLoading] = useState(false);
  const [runSummary, setRunSummary] = useState<RuntimeRunSummary | null>(null);
  const [runSummaryLoading, setRunSummaryLoading] = useState(false);
  const [childRuns, setChildRuns] = useState<RuntimeRunSummary[]>([]);
  const [childRunsLoading, setChildRunsLoading] = useState(false);
  const [runCheckpoints, setRunCheckpoints] = useState<
    Record<string, RuntimeRunCheckpoint[]>
  >({});
  const [runCheckpointsLoading, setRunCheckpointsLoading] = useState(false);

  const inputVariable = useMemo(() => {
    const inputNode = definition.nodes.find((node) => node.data.kind === "input");
    const variableName = inputNode?.data.variableName;
    return typeof variableName === "string" && variableName.trim()
      ? variableName.trim()
      : "user_input";
  }, [definition.nodes]);

  const finalOutput = useMemo(() => {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      if (events[index].event === "workflow_end") {
        return events[index].final_output ?? "";
      }
    }

    return "";
  }, [events]);

  const runSteps = useMemo(() => buildRunSteps(events), [events]);

  async function consumeWorkflowResponse(response: Response, replaceEvents = false) {
    const reader = response.body?.getReader();
    if (!reader) throw new Error("当前浏览器不支持流式运行结果。");
    if (replaceEvents) setEvents([]);

    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    const acceptEvent = (event: WorkflowRunEvent) => {
      handleRunEvent(event);
      if (event.event !== "heartbeat") {
        setEvents((current) => [...current, event]);
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const chunks = buffer.split(/\r?\n\r?\n/);
      buffer = chunks.pop() ?? "";
      for (const chunk of chunks) {
        for (const data of readSseEvent(chunk)) {
          acceptEvent(JSON.parse(data) as WorkflowRunEvent);
        }
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) {
      for (const data of readSseEvent(buffer)) {
        acceptEvent(JSON.parse(data) as WorkflowRunEvent);
      }
    }
  }

  async function runWorkflow() {
    onRunStart?.();
    setEvents([]);
    setError("");
    setTaskId(null);
    setRunId(null);
    setPendingHuman(null);
    setHumanInput("");
    setShowObservation(false);
    setObservationData(null);
    setObservationLoading(false);
    setRunSummary(null);
    setRunSummaryLoading(false);
    setChildRuns([]);
    setChildRunsLoading(false);
    setRunCheckpoints({});
    setRunCheckpointsLoading(false);
    setIsRunning(true);

    try {
      const response = await fetch("/api/workflow/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workflow: serializeWorkflow(definition),
          inputs: {
            [inputVariable]: input,
            ...(inputVariable === "user_input" ? {} : { user_input: input }),
          },
        }),
      });

      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as
          | { error?: string; detail?: string }
          | null;
        throw new Error(payload?.error ?? payload?.detail ?? "工作流运行失败。");
      }

      await consumeWorkflowResponse(response);
    } catch (runError) {
      setError(runError instanceof Error ? runError.message : "工作流运行失败。");
    } finally {
      setIsRunning(false);
    }
  }

  function handleRunEvent(event: WorkflowRunEvent) {
    if (event.event === "workflow_meta" && event.task_id) {
      setTaskId(event.task_id);
    }
    if (
      (event.event === "workflow_meta" || event.event === "workflow_end") &&
      event.run_id
    ) {
      setRunId(event.run_id);
    }
    if (event.event === "human_intervention_pending") {
      setPendingHuman({
        nodeId: event.node_id ?? "",
        nodeTitle: event.node_title ?? "人工介入",
        prompt: event.prompt ?? "请补充人工输入。",
        outputVariable: event.output_variable ?? "human_input",
      });
      setHumanInput("");
    }
    if (event.event === "node_end") {
      setPendingHuman((current) =>
        current?.nodeId === event.node_id ? null : current,
      );
    }
    if (event.event === "workflow_end") {
      setPendingHuman(null);
      setHumanInput("");
    }
  }

  async function resumeWorkflow() {
    if (!taskId || !pendingHuman) return;
    setError("");
    setIsResuming(true);

    try {
      const response = await fetch(`/api/workflow/run/${taskId}/resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          input_text: humanInput,
          node_id: pendingHuman.nodeId,
        }),
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as
          | { error?: string; detail?: string }
          | null;
        throw new Error(payload?.error ?? payload?.detail ?? "人工输入提交失败。");
      }
    } catch (resumeError) {
      setError(
        resumeError instanceof Error ? resumeError.message : "人工输入提交失败。",
      );
    } finally {
      setIsResuming(false);
    }
  }

  async function resumeDurableExecution() {
    if (!taskId) return;
    setError("");
    setIsResuming(true);
    try {
      const response = await fetch(`/api/workflow/run/${taskId}/stream?after_sequence=0`);
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as
          | { detail?: string }
          | null;
        throw new Error(payload?.detail || "恢复执行流失败");
      }
      await consumeWorkflowResponse(response, true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "恢复执行流失败");
    } finally {
      setIsResuming(false);
    }
  }

  async function fetchObservation() {
    if (!taskId) return;
    setObservationLoading(true);
    setRunSummaryLoading(Boolean(runId));
    setChildRunsLoading(Boolean(runId));
    setRunCheckpointsLoading(Boolean(runId));
    try {
      const response = await fetch(`/api/workflow/runtime-events/${taskId}`);
      if (response.ok) {
        const payload = (await response.json()) as WorkflowObservationData;
        setObservationData(payload);
      }
      if (runId) {
        const checkpointRunIds = [runId];
        const runResponse = await fetch(`/api/runtime/runs/${runId}`);
        if (runResponse.ok) {
          const runPayload = (await runResponse.json()) as RuntimeRunSummary;
          setRunSummary(runPayload);
        }
        const childRunsResponse = await fetch(
          `/api/runtime/runs?parent_run_id=${encodeURIComponent(runId)}&limit=80`,
        );
        if (childRunsResponse.ok) {
          const childRunPayload =
            (await childRunsResponse.json()) as RuntimeRunSummary[];
          setChildRuns(childRunPayload);
          checkpointRunIds.push(
            ...childRunPayload.map((run) => run.run_id).filter(Boolean),
          );
        }
        const checkpointPairs = await Promise.all(
          checkpointRunIds.map(async (checkpointRunId) => {
            const checkpointsResponse = await fetch(
              `/api/runtime/runs/${checkpointRunId}/checkpoints?limit=30`,
            );
            if (!checkpointsResponse.ok) {
              return [checkpointRunId, []] as const;
            }
            const checkpoints =
              (await checkpointsResponse.json()) as RuntimeRunCheckpoint[];
            return [checkpointRunId, checkpoints] as const;
          }),
        );
        setRunCheckpoints(Object.fromEntries(checkpointPairs));
      }
    } catch {
      // Observability is best-effort; workflow execution output remains primary.
    } finally {
      setObservationLoading(false);
      setRunSummaryLoading(false);
      setChildRunsLoading(false);
      setRunCheckpointsLoading(false);
    }
  }

  function toggleObservation() {
    setShowObservation((current) => {
      const next = !current;
      if (
        next &&
        taskId &&
        ((!observationData && !observationLoading) ||
          (runId && !runSummary && !runSummaryLoading) ||
          (runId && childRuns.length === 0 && !childRunsLoading) ||
          (runId &&
            Object.keys(runCheckpoints).length === 0 &&
            !runCheckpointsLoading))
      ) {
        void fetchObservation();
      }
      return next;
    });
  }

  return (
    <aside
      className={
        embedded
          ? "flex min-h-0 flex-1 flex-col"
          : "surface-panel flex min-h-0 flex-col rounded-lg"
      }
    >
      <div className="border-b border-white/10 p-4">
        <p className="text-sm font-semibold text-white">流水线试运行</p>
        <p className="mt-1 text-xs leading-5 text-slate-400">
          输入一份任务，观察每个工位的产出。MVP 会按线性和条件分支执行。
        </p>
      </div>

      <div className="space-y-3 p-4">
        <label className="block">
          <span className="text-xs font-semibold text-slate-300">
            {inputVariable}
          </span>
          <textarea
            className="mt-2 min-h-28 w-full resize-none rounded-lg border border-white/10 bg-white/[0.055] px-3 py-2 text-sm leading-6 text-white outline-none transition placeholder:text-slate-500 focus:border-brand-300/50 focus:ring-4 focus:ring-brand-300/10"
            onChange={(event) => setInput(event.target.value)}
            value={input}
          />
        </label>

        <button
          className="w-full rounded-full bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
          disabled={isRunning}
          onClick={() => void runWorkflow()}
          type="button"
        >
          {isRunning ? "流水线运行中" : "运行工作流"}
        </button>
      </div>

      {pendingHuman ? (
        <div className="mx-4 mb-3 rounded-lg border border-sky-300/30 bg-sky-300/10 p-3 text-sky-50">
          <div className="flex items-center justify-between gap-3">
            <p className="text-sm font-semibold">{pendingHuman.nodeTitle}</p>
            <span className="rounded-full border border-sky-200/25 bg-sky-200/10 px-2 py-0.5 text-[11px] text-sky-100">
              等待人工输入
            </span>
          </div>
          <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-sky-100">
            {pendingHuman.prompt}
          </p>
          <p className="mt-2 text-[11px] text-sky-200/80">
            写入变量：{pendingHuman.outputVariable}
          </p>
          <textarea
            className="mt-3 min-h-24 w-full resize-none rounded-lg border border-sky-200/25 bg-slate-950/50 px-3 py-2 text-sm leading-6 text-white outline-none transition placeholder:text-slate-500 focus:border-sky-200/60 focus:ring-4 focus:ring-sky-300/10"
            onChange={(event) => setHumanInput(event.target.value)}
            placeholder="输入人工补充内容..."
            value={humanInput}
          />
          <button
            className="mt-3 w-full rounded-full bg-sky-200 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-sky-100 active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
            disabled={!taskId || isResuming}
            onClick={() => void resumeWorkflow()}
            type="button"
          >
            {isResuming ? "提交中..." : "提交并继续"}
          </button>
        </div>
      ) : null}

      {taskId ? (
        <div className="mx-4 mb-3">
          <RuntimeApprovalPanel
            compact
            onResolved={() => resumeDurableExecution()}
            requestTypes={["tool_call", "final_output", "browser_domain"]}
            taskId={taskId}
            title="Agent 运行审批"
          />
        </div>
      ) : null}

      {taskId ? (
        <SandboxWorkspacePanel
          compact
          scopeIdPrefix={`${taskId}:`}
          scopeType="workflow"
        />
      ) : null}

      {taskId ? (
        <BrowserSessionPanel
          compact
          scopeIdPrefix={`${taskId}:`}
          scopeType="workflow"
        />
      ) : null}

      {taskId ? (
        <ClientToolPanel
          compact
          onResolved={() => resumeDurableExecution()}
          taskId={taskId}
        />
      ) : null}

      {error ? (
        <div className="mx-4 rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-sm text-rose-100">
          {error}
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="space-y-2">
          {runSteps.length === 0 ? (
            <div className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] px-4 py-8 text-center text-sm leading-6 text-slate-400">
              {isRunning
                ? "正在等待工作流事件..."
                : "暂无运行记录。点击“运行工作流”后，这里会按节点汇总展示过程。"}
            </div>
          ) : (
            runSteps.map((step) => (
              <div
                className="rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2"
                key={step.id}
              >
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-xs font-semibold text-slate-200">
                      {step.title}
                    </p>
                    {step.variable ? (
                      <p className="mt-0.5 truncate text-[11px] text-slate-500">
                        写入变量：{step.variable}
                      </p>
                    ) : null}
                  </div>
                  <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] ${statusClass(step.status)}`}>
                    {statusCopy(step.status)}
                  </span>
                </div>
                {step.output ? (
                  <>
                    <DataXResultCard content={step.output} />
                    <p className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-md bg-slate-950/35 p-2 text-xs leading-5 text-slate-300">
                      {step.output}
                    </p>
                  </>
                ) : null}
              </div>
            ))
          )}
        </div>
      </div>

      {taskId ? (
        <div className="border-t border-white/10">
          <button
            className="flex w-full items-center justify-between px-4 py-3 text-left text-xs font-semibold text-slate-300 transition hover:bg-white/5"
            onClick={toggleObservation}
            type="button"
          >
            <span>运行观测</span>
            <span className="text-[11px] text-slate-500">
              {showObservation ? "收起" : "展开"}
            </span>
          </button>
          {showObservation ? (
            <div className="space-y-3 px-4 pb-4">
              {observationLoading ? (
                <p className="rounded-lg border border-white/10 bg-white/[0.035] px-3 py-2 text-xs text-slate-400">
                  加载中...
                </p>
              ) : observationData ? (
                <>
                  {runId ? (
                    <div className="rounded-lg border border-hire-300/20 bg-hire-300/10 p-3">
                      <div className="flex items-center justify-between gap-3">
                        <p className="text-xs font-semibold text-hire-100">
                          RunRegistry
                        </p>
                        <span className="rounded-full border border-hire-300/25 bg-hire-300/10 px-2 py-0.5 text-[10px] uppercase text-hire-100">
                          {runSummary?.status ?? "loading"}
                        </span>
                      </div>
                      <p className="mt-2 break-all font-mono text-[11px] text-slate-400">
                        {runId}
                      </p>
                      {runSummaryLoading ? (
                        <p className="mt-2 text-[11px] text-slate-500">
                          正在读取 run 摘要...
                        </p>
                      ) : runSummary ? (
                        <div className="mt-2 grid gap-1 text-[11px] text-slate-400">
                          <p>类型：{runSummary.run_type}</p>
                          <p>标题：{runSummary.title}</p>
                          {runSummary.source_id ? (
                            <p className="break-all">
                              source：{runSummary.source_id}
                            </p>
                          ) : null}
                          {runSummary.error ? (
                            <p className="text-rose-200">
                              error：{runSummary.error}
                            </p>
                          ) : null}
                        </div>
                      ) : (
                        <p className="mt-2 text-[11px] text-slate-500">
                          暂无 run 摘要。
                        </p>
                      )}
                      <div className="mt-3 border-t border-white/10 pt-2">
                        <div className="flex items-center justify-between gap-3">
                          <p className="text-[11px] font-semibold text-slate-300">
                            Checkpoints
                          </p>
                          <span className="text-[10px] text-slate-500">
                            {runCheckpointsLoading
                              ? "..."
                              : runCheckpoints[runId]?.length ?? 0}
                          </span>
                        </div>
                        <RuntimeCheckpointList
                          checkpoints={runCheckpoints[runId] ?? []}
                          limit={8}
                        />
                      </div>
                    </div>
                  ) : null}

                  {runId ? (
                    <div className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
                      <div className="flex items-center justify-between gap-3">
                        <p className="text-xs font-semibold text-slate-200">
                          子 Run
                        </p>
                        <span className="text-[11px] text-slate-500">
                          {childRunsLoading ? "..." : childRuns.length}
                        </span>
                      </div>
                      {childRunsLoading ? (
                        <p className="mt-2 text-xs text-slate-500">
                          正在读取 AgentTask / Handoff 子 run...
                        </p>
                      ) : childRuns.length === 0 ? (
                        <p className="mt-2 text-xs text-slate-500">
                          暂无子 run。
                        </p>
                      ) : (
                        <div className="mt-2 max-h-44 space-y-1 overflow-y-auto">
                          {childRuns.slice(0, 30).map((run) => {
                            const agentTaskId = runMetadataText(
                              run.metadata,
                              "agent_task_id",
                            );
                            const handoffId = runMetadataText(
                              run.metadata,
                              "handoff_id",
                            );
                            const targetAgent = runMetadataText(
                              run.metadata,
                              "target_agent",
                            );
                            const acceptedBy = runMetadataText(
                              run.metadata,
                              "accepted_by",
                            );
                            const completedBy = runMetadataText(
                              run.metadata,
                              "completed_by",
                            );
                            const result = runMetadataText(run.metadata, "result");
                            const handler = completedBy || acceptedBy;
                            return (
                              <div
                                className="rounded-md bg-slate-950/35 px-2 py-1.5"
                                key={run.run_id}
                              >
                                <div className="flex items-center justify-between gap-2">
                                  <span className="truncate text-[11px] font-semibold text-slate-200">
                                    {run.title || run.run_type}
                                  </span>
                                  <span className="shrink-0 rounded-full border border-white/10 bg-white/[0.045] px-2 py-0.5 text-[10px] uppercase text-slate-400">
                                    {run.status}
                                  </span>
                                </div>
                                <p className="mt-1 truncate text-[11px] text-slate-500">
                                  {run.run_type}
                                  {targetAgent ? ` · target=${targetAgent}` : ""}
                                  {agentTaskId ? ` · task=${agentTaskId}` : ""}
                                  {handoffId ? ` · handoff=${handoffId}` : ""}
                                </p>
                                {handler || result ? (
                                  <p className="mt-1 truncate text-[11px] text-slate-500">
                                    {handler ? `handler=${handler}` : ""}
                                    {handler && result ? " 路 " : ""}
                                    {result ? `result=${result}` : ""}
                                  </p>
                                ) : null}
                                <RuntimeCheckpointList
                                  checkpoints={runCheckpoints[run.run_id] ?? []}
                                  limit={3}
                                />
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  ) : null}

                  <div className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-xs font-semibold text-slate-200">
                        运行事件
                      </p>
                      <span className="text-[11px] text-slate-500">
                        {observationData.event_count}
                      </span>
                    </div>
                    {observationData.events.length === 0 ? (
                      <p className="mt-2 text-xs text-slate-500">暂无事件。</p>
                    ) : (
                      <div className="mt-2 max-h-44 space-y-1 overflow-y-auto">
                        {observationData.events.slice(0, 30).map((event) => {
                          const summary = observationPayloadSummary(event.payload);
                          return (
                            <div
                              className="rounded-md bg-slate-950/35 px-2 py-1.5"
                              key={event.id}
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="truncate text-[11px] font-semibold text-hire-100">
                                  {event.type}
                                </span>
                                <span className="shrink-0 text-[10px] uppercase text-slate-500">
                                  {event.severity}
                                </span>
                              </div>
                              <p className="mt-1 truncate text-[11px] text-slate-500">
                                {formatObservationTime(event.created_at)}
                                {summary ? ` · ${summary}` : ""}
                              </p>
                            </div>
                          );
                        })}
                        {observationData.event_count > 30 ? (
                          <p className="text-[11px] text-slate-500">
                            仅展示前 30 条，共 {observationData.event_count} 条。
                          </p>
                        ) : null}
                      </div>
                    )}
                  </div>

                  <div className="rounded-lg border border-white/10 bg-white/[0.035] p-3">
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-xs font-semibold text-slate-200">
                        工具调用审计
                      </p>
                      <span className="text-[11px] text-slate-500">
                        {observationData.tool_audit_count}
                      </span>
                    </div>
                    {observationData.tool_audit_records.length === 0 ? (
                      <p className="mt-2 text-xs text-slate-500">
                        暂无工具调用记录。
                      </p>
                    ) : (
                      <div className="mt-2 max-h-40 space-y-1 overflow-y-auto">
                        {observationData.tool_audit_records
                          .slice(0, 20)
                          .map((record) => (
                            <div
                              className="rounded-md bg-slate-950/35 px-2 py-1.5"
                              key={record.record_id}
                            >
                              <div className="flex items-center justify-between gap-2">
                                <span className="truncate text-[11px] font-semibold text-slate-200">
                                  {record.tool_name}
                                </span>
                                <span className="shrink-0 text-[10px] uppercase text-slate-500">
                                  {record.status}
                                </span>
                              </div>
                              <p className="mt-1 truncate text-[11px] text-slate-500">
                                {record.duration_ms != null
                                  ? `${record.duration_ms.toFixed(0)}ms`
                                  : "duration n/a"}
                                {record.output_length != null
                                  ? ` · ${record.output_length} chars`
                                  : ""}
                                {record.error ? ` · ${record.error}` : ""}
                              </p>
                            </div>
                          ))}
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <p className="rounded-lg border border-white/10 bg-white/[0.035] px-3 py-2 text-xs text-slate-400">
                  展开后会读取本次运行的 runtime events 和工具审计摘要。
                </p>
              )}
            </div>
          ) : null}
        </div>
      ) : null}

      {finalOutput ? (
        <div className="border-t border-white/10 p-4">
          <p className="text-xs font-semibold text-hire-100">最终交付</p>
          <p className="mt-2 max-h-44 overflow-y-auto whitespace-pre-wrap rounded-lg border border-hire-300/25 bg-hire-300/10 p-3 text-sm leading-6 text-hire-50">
            {finalOutput}
          </p>
        </div>
      ) : null}
    </aside>
  );
}
