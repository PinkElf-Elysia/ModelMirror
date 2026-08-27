import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import RuntimeApprovalPanel from "../runtime/RuntimeApprovalPanel";
import BrowserSessionPanel from "../runtime/BrowserSessionPanel";
import ClientToolPanel from "../runtime/ClientToolPanel";
import SandboxWorkspacePanel from "../runtime/SandboxWorkspacePanel";
import DataXResultCard from "../datax/DataXResultCard";
import FileOutputTray from "../FileOutputTray";
import SkillCreatorCaptureButton, {
  completedWorkflowCaptureSource,
} from "../skill-creator/SkillCreatorCaptureButton";
import SkillCreatorHandoffCard, {
  latestSkillCreatorHandoff,
} from "../skill-creator/SkillCreatorHandoffCard";
import SkillApplicationCard, {
  requiredSkillIdsFromWorkflowNodes,
} from "../skill-runtime/SkillApplicationCard";
import SkillHookApplicationCard, {
  hookSkillIdsFromWorkflowNodes,
} from "../skill-runtime/SkillHookApplicationCard";
import ProviderRouteReceiptSummary from "../meta/ProviderRouteReceiptSummary";
import { useSkillCreatorStatus } from "../../hooks/useSkillCreatorStatus";
import {
  fetchFileOutputs,
  type FileOutput,
  type FileOutputReuseConfirmation,
} from "../../data/fileOutputs";
import {
  type NodeRunStatus,
  type WorkflowDefinition,
  type WorkflowRunEvent,
  type ProviderRouteReceipt,
  type WorkflowValue,
  type WorkflowVariableDeclaration,
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
  fileInputFocusRequest?: {
    requestId: number;
    variableName: string;
  } | null;
  onRunStart?: () => void;
  /** 运行事件 → 画布节点高亮回调。"idle" 表示运行开始时重置。 */
  onNodeStatusChange?: (nodeId: string, status: NodeRunStatus | "idle") => void;
  /** 点击运行步骤时高亮画布对应节点（日志↔节点联动）。 */
  onStepSelect?: (nodeId: string) => void;
}

interface RunHistoryEntry {
  runId: string | null;
  taskId: string | null;
  finishedAt: number;
  status: "completed" | "cancelled" | "error";
  summary: string;
}

interface WorkflowRunRecoveryPointer {
  taskId: string;
  runId: string | null;
}

interface WorkflowStreamProgress {
  lastSequence: number;
  waitingForAgentHandoff: boolean;
  terminal: boolean;
}

const WORKFLOW_RUN_RECOVERY_PREFIX = "modelmirror-workflow-run-v1";
const WORKFLOW_TASK_ID_PATTERN = /^[0-9a-f]{32}$/i;
const RUNTIME_RUN_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const AGENT_HANDOFF_STREAM_POLL_MS = 1000;

export function workflowRunRecoveryKey(workflowId: string) {
  return `${WORKFLOW_RUN_RECOVERY_PREFIX}:${workflowId.trim()}`;
}

export function readWorkflowRunRecovery(
  workflowId: string,
): WorkflowRunRecoveryPointer | null {
  if (typeof window === "undefined" || !workflowId.trim()) return null;
  const key = workflowRunRecoveryKey(workflowId);
  try {
    const parsed = JSON.parse(window.sessionStorage.getItem(key) ?? "null") as
      | { taskId?: unknown; runId?: unknown }
      | null;
    const taskId = typeof parsed?.taskId === "string" ? parsed.taskId.trim() : "";
    const runId = typeof parsed?.runId === "string" ? parsed.runId.trim() : "";
    if (
      !WORKFLOW_TASK_ID_PATTERN.test(taskId)
      || (runId && !RUNTIME_RUN_ID_PATTERN.test(runId))
    ) {
      window.sessionStorage.removeItem(key);
      return null;
    }
    return { taskId, runId: runId || null };
  } catch {
    window.sessionStorage.removeItem(key);
    return null;
  }
}

export function persistWorkflowRunRecovery(
  workflowId: string,
  pointer: WorkflowRunRecoveryPointer,
) {
  const cleanWorkflowId = workflowId.trim();
  const taskId = pointer.taskId.trim();
  const runId = pointer.runId?.trim() || null;
  if (
    typeof window === "undefined"
    || !cleanWorkflowId
    || !taskId
    || !WORKFLOW_TASK_ID_PATTERN.test(taskId)
    || (runId !== null && !RUNTIME_RUN_ID_PATTERN.test(runId))
  ) return;
  try {
    window.sessionStorage.setItem(
      workflowRunRecoveryKey(cleanWorkflowId),
      JSON.stringify({ taskId, runId }),
    );
  } catch {
    // Session recovery is best-effort and never blocks workflow execution.
  }
}

interface WorkflowFileFormatCapability {
  extensions: string[];
  interaction_status: "ready" | "planned" | "disabled";
  status_reason?: string | null;
}

interface WorkflowFileCapability {
  input_kind: string;
  interaction_status: "ready" | "planned" | "disabled";
  max_bytes_per_file: number;
  status_reason?: string | null;
  formats: WorkflowFileFormatCapability[];
}

interface WorkflowFileCapabilitiesResponse {
  capabilities: WorkflowFileCapability[];
}

interface WorkflowFileAssetListResponse {
  items: WorkflowFileAsset[];
  total: number;
}

interface WorkflowFileAsset {
  asset_id: string;
  display_name: string;
  byte_size: number;
  format: string;
  status: string;
}

interface WorkflowFileSelection {
  asset: WorkflowFileAsset | null;
  busy: boolean;
  error: string;
  notice: string;
}

interface PendingHumanIntervention {
  nodeId: string;
  nodeTitle: string;
  prompt: string;
  outputVariable: string;
}

type RunStepStatus = "running" | "done" | "waiting" | "skipped" | "error";

interface WorkflowRunStep {
  id: string;
  title: string;
  type?: WorkflowRunEvent["node_type"];
  status: RunStepStatus;
  output: string;
  variable?: string;
  providerRouteReceipt?: ProviderRouteReceipt;
}

export interface WorkflowBatchReceipt {
  index: number;
  status: "completed";
  projectId: string;
  version: number;
  executionId: string;
  taskId: string;
  runId: string;
  result: string;
}

export interface KnowledgeProposalReceipt {
  status: "pending" | "approved" | "rejected";
  proposalId: string;
  knowledgeBaseId: string;
  revision: number;
  reused: boolean;
  contentLength: number;
}

const WORKFLOW_PROJECT_ID_PATTERN = /^wf_[0-9a-f]{32}$/i;
const WORKFLOW_EXECUTION_ID_PATTERN = /^wfx_[0-9a-f]{32}$/i;
const WORKFLOW_TRIGGER_TASK_ID_PATTERN = /^wft_[0-9a-f]{32}$/i;
const KNOWLEDGE_PROPOSAL_ID_PATTERN = /^kwp_[0-9a-f]{32}$/i;
const KNOWLEDGE_BASE_ID_PATTERN = /^kb_[0-9a-f]{32}$/i;

export function parseKnowledgeProposalReceipt(
  value: string,
): KnowledgeProposalReceipt | null {
  let candidate: unknown;
  try {
    candidate = JSON.parse(value);
  } catch {
    return null;
  }
  if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return null;
  const receipt = candidate as Record<string, unknown>;
  if (
    !["pending", "approved", "rejected"].includes(String(receipt.status))
    || typeof receipt.proposalId !== "string"
    || !KNOWLEDGE_PROPOSAL_ID_PATTERN.test(receipt.proposalId)
    || typeof receipt.knowledgeBaseId !== "string"
    || !KNOWLEDGE_BASE_ID_PATTERN.test(receipt.knowledgeBaseId)
    || !Number.isInteger(receipt.revision)
    || Number(receipt.revision) < 1
    || typeof receipt.reused !== "boolean"
    || !Number.isInteger(receipt.contentLength)
    || Number(receipt.contentLength) < 1
  ) return null;
  return receipt as unknown as KnowledgeProposalReceipt;
}

export function parseWorkflowBatchReceipts(
  value: string,
): WorkflowBatchReceipt[] | null {
  let candidate: unknown;
  try {
    candidate = JSON.parse(value);
  } catch {
    return null;
  }
  if (!Array.isArray(candidate) || candidate.length === 0) return null;

  const receipts: WorkflowBatchReceipt[] = [];
  for (const item of candidate) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null;
    const receipt = item as Record<string, unknown>;
    if (
      !Number.isInteger(receipt.index)
      || Number(receipt.index) < 0
      || receipt.status !== "completed"
      || typeof receipt.projectId !== "string"
      || !WORKFLOW_PROJECT_ID_PATTERN.test(receipt.projectId)
      || !Number.isInteger(receipt.version)
      || Number(receipt.version) < 1
      || typeof receipt.executionId !== "string"
      || !WORKFLOW_EXECUTION_ID_PATTERN.test(receipt.executionId)
      || typeof receipt.taskId !== "string"
      || !WORKFLOW_TRIGGER_TASK_ID_PATTERN.test(receipt.taskId)
      || typeof receipt.runId !== "string"
      || !RUNTIME_RUN_ID_PATTERN.test(receipt.runId)
      || typeof receipt.result !== "string"
    ) return null;
    receipts.push(receipt as unknown as WorkflowBatchReceipt);
  }
  return receipts;
}

export function workflowRunCompletedSummary(output: string | undefined) {
  if (!output) return "运行完成。";
  const receipts = parseWorkflowBatchReceipts(output);
  return receipts ? `批次完成：${receipts.length} 项` : output;
}

function WorkflowBatchReceiptList({
  receipts,
  compact = false,
}: {
  receipts: WorkflowBatchReceipt[];
  compact?: boolean;
}) {
  return (
    <div className={compact ? "mt-2" : "mt-3"}>
      <div className="mb-2 flex items-center justify-between gap-3">
        <p className="text-xs font-semibold text-emerald-100">
          批次回执 · {receipts.length} 项
        </p>
        <span className="rounded-full border border-emerald-300/25 bg-emerald-300/10 px-2 py-0.5 text-[11px] text-emerald-100">
          全部完成
        </span>
      </div>
      <div className={`grid gap-2 ${compact ? "max-h-56 overflow-y-auto" : "max-h-72 overflow-y-auto"}`}>
        {receipts.map((receipt) => (
          <div
            className="rounded-lg border border-white/10 bg-slate-950/30 px-3 py-2.5"
            key={`${receipt.executionId}:${receipt.index}`}
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs font-semibold text-slate-100">第 {receipt.index + 1} 项</p>
              <div className="flex items-center gap-2 text-[11px] text-slate-400">
                <span>固定目标 v{receipt.version}</span>
                <span>子执行 …{receipt.executionId.slice(-8)}</span>
              </div>
            </div>
            <p className="mt-1.5 whitespace-pre-wrap break-words text-xs leading-5 text-slate-300">
              {receipt.result || "（无文本结果）"}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
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
    variables: definition.variables?.map((variable) => ({
      ...variable,
      defaultValue:
        variable.defaultValue === undefined
          ? undefined
          : structuredClone(variable.defaultValue),
    })),
  };
}

export function workflowDeclaredInputText(
  declaration: WorkflowVariableDeclaration,
) {
  if (declaration.defaultValue === undefined) return "";
  return declaration.valueType === "json"
    ? JSON.stringify(declaration.defaultValue, null, 2)
    : String(declaration.defaultValue);
}

export function parseWorkflowDeclaredInputs(
  declarations: WorkflowVariableDeclaration[],
  rawValues: Record<string, string>,
): { inputs: Record<string, WorkflowValue>; error: string } {
  const inputs: Record<string, WorkflowValue> = {};
  for (const declaration of declarations) {
    if (declaration.kind !== "input") continue;
    const raw = rawValues[declaration.id] ?? "";
    if (!raw && declaration.defaultValue === undefined && declaration.valueType !== "text") continue;
    if (declaration.valueType === "text") {
      inputs[declaration.name] = raw;
    } else if (declaration.valueType === "number") {
      const value = Number(raw);
      if (!raw.trim() || !Number.isFinite(value)) return { inputs: {}, error: `${declaration.name} 需要有限数字。` };
      inputs[declaration.name] = value;
    } else if (declaration.valueType === "boolean") {
      if (raw !== "true" && raw !== "false") return { inputs: {}, error: `${declaration.name} 需要选择 true 或 false。` };
      inputs[declaration.name] = raw === "true";
    } else if (raw.trim()) {
      try { inputs[declaration.name] = JSON.parse(raw) as WorkflowValue; }
      catch { return { inputs: {}, error: `${declaration.name} 的 JSON 格式无效。` }; }
    }
  }
  return { inputs, error: "" };
}

export function workflowFileScopeId(workflowId: string) {
  return `workflow:${workflowId}`;
}

export function workflowOutputsForRun(
  outputs: FileOutput[],
  runId: string | null,
) {
  return runId ? outputs.filter((output) => output.source_run_id === runId) : [];
}

export function recoveredWorkflowOutputs(
  outputs: FileOutput[],
  runId: string | null,
) {
  return outputs.filter((output) => !runId || output.source_run_id !== runId);
}

export function replaceWorkflowOutputSubset(
  current: FileOutput[],
  previousSubset: FileOutput[],
  nextSubset: FileOutput[],
) {
  const previousIds = new Set(previousSubset.map((output) => output.output_id));
  return [
    ...current.filter((output) => !previousIds.has(output.output_id)),
    ...nextSubset,
  ];
}

export function apiErrorMessage(payload: unknown, fallback: string) {
  if (!payload || typeof payload !== "object") return fallback;
  const candidate = payload as {
    error?: unknown;
    detail?: unknown;
  };
  if (typeof candidate.error === "string" && candidate.error.trim()) {
    return candidate.error;
  }
  if (typeof candidate.detail === "string" && candidate.detail.trim()) {
    return candidate.detail;
  }
  if (Array.isArray(candidate.detail)) {
    const firstMessage = candidate.detail.find(
      (item): item is { msg: string } =>
        Boolean(
          item &&
            typeof item === "object" &&
            "msg" in item &&
            typeof item.msg === "string" &&
            item.msg.trim(),
        ),
    )?.msg;
    if (firstMessage) return firstMessage;
  }
  if (
    candidate.detail &&
    typeof candidate.detail === "object" &&
    "message" in candidate.detail &&
    typeof candidate.detail.message === "string"
  ) {
    return candidate.detail.message;
  }
  return fallback;
}

export const workflowFileDeleteConfirmation =
  "彻底删除此文件？这会解除当前工作流绑定；如果这是最后一个引用，原件和派生物也会被清理。此操作不可撤销。";

export function confirmWorkflowFileDeletion(
  confirmAction: (message: string) => boolean,
) {
  return confirmAction(workflowFileDeleteConfirmation);
}

function formatFileSize(byteSize: number) {
  if (byteSize < 1024) return `${byteSize} B`;
  if (byteSize < 1024 * 1024) return `${(byteSize / 1024).toFixed(1)} KiB`;
  return `${(byteSize / (1024 * 1024)).toFixed(1)} MiB`;
}

function readSseEvent(eventText: string) {
  return eventText
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trim())
    .filter(Boolean);
}

export function localizeWorkflowStepOutput(
  output: string,
  nodeType: WorkflowRunEvent["node_type"],
) {
  if (nodeType !== "iteration") return output;
  return output.replace(/^completed (\d+)\/(\d+)$/gm, "已完成 $1/$2");
}

const HTTP_BODY_PREVIEW_LIMIT = 2_000;
const KNOWLEDGE_PROPOSAL_SOURCE_WITHHELD = "knowledge proposal source withheld";
const KNOWLEDGE_PROPOSAL_SOURCE_WITHHELD_COPY =
  "提议正文已隐藏，仅可在 Knowledge Inbox 中查看。";

export function workflowStepOutputPreview(
  output: string,
  nodeType: WorkflowRunEvent["node_type"],
  variable?: string,
) {
  if (nodeType !== "http_request") return output;
  try {
    const payload = JSON.parse(output) as Record<string, unknown>;
    if (!payload || Array.isArray(payload) || typeof payload.body !== "string") {
      return output;
    }
    if (payload.body.length <= HTTP_BODY_PREVIEW_LIMIT) return output;
    const details = [
      typeof payload.statusCode === "number" ? `HTTP ${payload.statusCode}` : "HTTP 响应",
      typeof payload.contentType === "string" && payload.contentType
        ? payload.contentType
        : null,
      typeof payload.receivedBytes === "number" && Number.isFinite(payload.receivedBytes)
        ? `${payload.receivedBytes} 字节`
        : null,
    ].filter((detail): detail is string => Boolean(detail));
    const destination = variable ? `变量 ${variable} 中` : "该节点完整输出中";
    return `${details.join(" · ")}\n响应正文已折叠（${payload.body.length} 个字符），完整内容仍保存在${destination}，后续节点可继续读取。`;
  } catch {
    return output;
  }
}

function appendStepOutput(
  current: string,
  next: string | undefined,
  nodeType: WorkflowRunEvent["node_type"],
) {
  if (!next) return current;
  const localizedNext = next === KNOWLEDGE_PROPOSAL_SOURCE_WITHHELD
    ? KNOWLEDGE_PROPOSAL_SOURCE_WITHHELD_COPY
    : localizeWorkflowStepOutput(next, nodeType);
  if (
    localizedNext === KNOWLEDGE_PROPOSAL_SOURCE_WITHHELD_COPY
    && current.includes(KNOWLEDGE_PROPOSAL_SOURCE_WITHHELD_COPY)
  ) {
    return current;
  }
  if (!current) return localizedNext;
  if (nodeType === "llm" || nodeType === "workflow_agent") {
    return `${current}${localizedNext}`;
  }
  return `${current}\n${localizedNext}`;
}

function statusCopy(status: RunStepStatus) {
  if (status === "done") return "完成";
  if (status === "waiting") return "等待输入";
  if (status === "skipped") return "已跳过";
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
  if (status === "skipped") {
    return "border-slate-300/20 bg-slate-300/10 text-slate-300";
  }
  if (status === "error") {
    return "border-rose-300/25 bg-rose-300/10 text-rose-100";
  }
  return "border-hire-300/25 bg-hire-300/10 text-hire-100";
}

export function shouldShowHandoffInboxLink(step: WorkflowRunStep): boolean {
  return (
    (step.type === "agent_handoff" || step.type === "handoff_router")
    && (step.status === "waiting" || step.status === "done")
  );
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

export function buildRunSteps(events: WorkflowRunEvent[]) {
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
    if (
      event.event === "workflow_meta"
      || event.event === "workflow_end"
      || event.event === "skill_creator_handoff"
    ) {
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
    if (event.event === "skill_runtime_status" || event.event === "skill_hook_status") {
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
    if (event.event === "agent_handoff_waiting") {
      step.status = "waiting";
      const target = event.target_id
        ? `${event.target_id}${event.target_version ? ` · v${event.target_version}` : ""}`
        : "协作接收方";
      step.output = appendStepOutput(
        step.output,
        event.message || `任务已移交给 ${target}，正在等待完成。`,
        step.type,
      );
      return;
    }
    if (event.event === "node_delta") {
      step.output = appendStepOutput(step.output, event.output, step.type);
      return;
    }
    if (event.event === "node_skipped") {
      step.status = "skipped";
      step.output = event.message ?? "未命中当前分支，已跳过。";
      return;
    }
    if (event.event === "node_end") {
      const completedHandoff = (
        step.status === "waiting"
        && (step.type === "agent_handoff" || step.type === "handoff_router")
      );
      if (step.status !== "error") step.status = "done";
      step.variable = event.variable ?? step.variable;
      step.providerRouteReceipt = event.provider_route_receipts ?? step.providerRouteReceipt;
      if (completedHandoff) {
        step.output = event.output || "协作任务已完成，工作流已继续执行。";
      } else if (!step.output) {
        step.output = appendStepOutput(step.output, event.output, step.type);
      }
      return;
    }
    if (event.event === "error") {
      step.status = "error";
      step.output = appendStepOutput(step.output, event.message, step.type);
      step.providerRouteReceipt = event.provider_route_receipts ?? step.providerRouteReceipt;
    }
  });

  return steps;
}

export function shouldRecordNodeStreamFailure(
  failedNodeCount: number,
  terminalHistoryRecorded: boolean,
): boolean {
  return failedNodeCount > 0 && !terminalHistoryRecorded;
}

export default function WorkflowRun({
  definition,
  embedded = false,
  fileInputFocusRequest = null,
  onRunStart,
  onNodeStatusChange,
  onStepSelect,
}: WorkflowRunProps) {
  const { status: skillCreatorStatus } = useSkillCreatorStatus();
  const [input, setInput] = useState("请帮我把这个需求拆成三步执行计划。");
  const [declaredInputValues, setDeclaredInputValues] = useState<
    Record<string, string>
  >({});
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
  const [fileCapabilities, setFileCapabilities] = useState<
    WorkflowFileCapability[]
  >([]);
  const [fileCapabilityLoading, setFileCapabilityLoading] = useState(false);
  const [fileCapabilityError, setFileCapabilityError] = useState("");
  const [fileSelections, setFileSelections] = useState<
    Record<string, WorkflowFileSelection>
  >({});
  const [workflowFileAssets, setWorkflowFileAssets] = useState<
    WorkflowFileAsset[]
  >([]);
  const [fileOutputs, setFileOutputs] = useState<FileOutput[]>([]);
  const [workflowFileListLoading, setWorkflowFileListLoading] = useState(false);
  const [workflowFileListError, setWorkflowFileListError] = useState("");
  const [workflowFileListNotice, setWorkflowFileListNotice] = useState("");
  const [deletingWorkflowAssetId, setDeletingWorkflowAssetId] = useState("");
  const fileInputCardRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const workflowFileListGeneration = useRef(0);
  const workflowOutputGeneration = useRef(0);
  const runAbortRef = useRef<AbortController | null>(null);
  const failedNodesRef = useRef<Set<string>>(new Set());
  const runningNodesRef = useRef<Set<string>>(new Set());
  const runMetaRef = useRef<{ taskId: string | null; runId: string | null }>({
    taskId: null,
    runId: null,
  });
  const terminalHistoryRecordedRef = useRef(false);
  const lastNodeErrorRef = useRef("");
  const [runHistory, setRunHistory] = useState<RunHistoryEntry[]>([]);
  const [finishedOutcome, setFinishedOutcome] = useState<
    "completed" | "cancelled" | "error" | null
  >(null);

  const declaredInputs = useMemo(
    () => (definition.variables ?? []).filter((variable) => variable.kind === "input"),
    [definition.variables],
  );

  useEffect(() => {
    setDeclaredInputValues((current) =>
      Object.fromEntries(
        declaredInputs.map((declaration) => [
          declaration.id,
          current[declaration.id] ?? workflowDeclaredInputText(declaration),
        ]),
      ),
    );
  }, [declaredInputs]);

  useEffect(() => {
    if (!fileInputFocusRequest) return;
    const frame = window.requestAnimationFrame(() => {
      const card = fileInputCardRefs.current[fileInputFocusRequest.variableName];
      if (!card) return;
      card.scrollIntoView?.({ block: "nearest" });
      card.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [fileInputFocusRequest]);

  const inputVariable = useMemo(() => {
    const inputNode = definition.nodes.find((node) => node.data.kind === "input");
    const variableName = inputNode?.data.variableName;
    return typeof variableName === "string" && variableName.trim()
      ? variableName.trim()
      : "user_input";
  }, [definition.nodes]);

  const fileAssetKinds = useMemo(() => {
    const values = new Map<string, "document" | "visual_analysis">();
    definition.nodes.forEach((node) => {
      if (
        node.data.kind !== "document_extractor" &&
        node.data.kind !== "vision_understanding"
      ) return;
      const variable =
        typeof node.data.assetIdVariable === "string"
          ? node.data.assetIdVariable.trim()
          : "";
      if (!variable) return;
      values.set(
        variable,
        node.data.kind === "vision_understanding" ? "visual_analysis" : "document",
      );
    });
    return values;
  }, [definition.nodes]);

  const fileAssetVariables = useMemo(
    () => Array.from(fileAssetKinds.keys()),
    [fileAssetKinds],
  );

  const fileCapabilityForVariable = useCallback(
    (variableName: string) =>
      fileCapabilities.find(
        (item) => item.input_kind === fileAssetKinds.get(variableName),
      ) ?? null,
    [fileAssetKinds, fileCapabilities],
  );

  const fileAcceptForVariable = useCallback(
    (variableName: string) =>
      fileCapabilityForVariable(variableName)?.formats
        .filter((format) => format.interaction_status === "ready")
        .flatMap((format) => format.extensions)
        .join(",") ?? "",
    [fileCapabilityForVariable],
  );

  const fileFormatAllowedForVariable = useCallback(
    (variableName: string, format: string) =>
      fileCapabilityForVariable(variableName)?.formats.some(
        (item) =>
          item.interaction_status === "ready" &&
          item.extensions.some(
            (extension) =>
              extension.replace(/^\./, "").toLowerCase() === format.toLowerCase(),
          ),
      ) ?? false,
    [fileCapabilityForVariable],
  );

  const fileScopeId = useMemo(
    () => workflowFileScopeId(definition.id),
    [definition.id],
  );
  const workflowFileScopeRef = useRef(fileScopeId);
  workflowFileScopeRef.current = fileScopeId;

  useEffect(() => {
    workflowFileListGeneration.current += 1;
    workflowOutputGeneration.current += 1;
    setFileSelections({});
    setWorkflowFileAssets([]);
    setFileOutputs([]);
    setWorkflowFileListError("");
    setWorkflowFileListNotice("");
  }, [fileScopeId]);

  const refreshWorkflowFileOutputs = useCallback(async () => {
    const requestedScopeId = fileScopeId;
    const generation = workflowOutputGeneration.current + 1;
    workflowOutputGeneration.current = generation;
    const items = await fetchFileOutputs("workflow", requestedScopeId).catch(() => []);
    if (
      requestedScopeId !== workflowFileScopeRef.current ||
      generation !== workflowOutputGeneration.current
    ) return;
    setFileOutputs(items);
  }, [fileScopeId]);

  async function prepareWorkflowOutputReuse(
    output: FileOutput,
    confirmation: FileOutputReuseConfirmation,
  ) {
    const asset: WorkflowFileAsset = {
      asset_id: confirmation.asset_id,
      display_name: output.display_name,
      byte_size: output.byte_size,
      format: output.format,
      status: "ready",
    };
    setWorkflowFileAssets((current) => [
      asset,
      ...current.filter((item) => item.asset_id !== asset.asset_id),
    ]);
    if (fileAssetVariables.length === 1) {
      const variableName = fileAssetVariables[0];
      setFileSelections((current) => ({
        ...current,
        [variableName]: {
          asset,
          busy: false,
          error: "",
          notice: "输出副本已加入本轮；仍需点击运行才会使用。",
        },
      }));
    } else {
      setWorkflowFileListNotice("输出副本已加入已有资产，请为目标变量显式选择。" );
    }
    await refreshWorkflowFileAssets();
  }

  useEffect(() => {
    void refreshWorkflowFileOutputs();
    return () => {
      workflowOutputGeneration.current += 1;
    };
  }, [refreshWorkflowFileOutputs]);

  useEffect(() => {
    if (fileAssetVariables.length === 0) {
      setFileCapabilities([]);
      setFileCapabilityError("");
      setFileCapabilityLoading(false);
      return;
    }

    let active = true;
    setFileCapabilityLoading(true);
    setFileCapabilityError("");
    fetch("/api/files/capabilities?purpose=workflow")
      .then(async (response) => {
        const payload = (await response.json().catch(() => null)) as
          | WorkflowFileCapabilitiesResponse
          | null;
        if (!response.ok || !payload) {
          throw new Error(
            apiErrorMessage(payload, "无法读取工作流文件能力。"),
          );
        }
        const requiredKinds = new Set(fileAssetKinds.values());
        const capabilities = payload.capabilities.filter((item) =>
          requiredKinds.has(item.input_kind as "document" | "visual_analysis"),
        );
        if (capabilities.length !== requiredKinds.size) {
          throw new Error("工作流文件能力尚未登记。");
        }
        if (active) setFileCapabilities(capabilities);
      })
      .catch((caught) => {
        if (!active) return;
        setFileCapabilities([]);
        setFileCapabilityError(
          caught instanceof Error ? caught.message.trim() : "无法读取工作流文件能力。",
        );
      })
      .finally(() => {
        if (active) setFileCapabilityLoading(false);
      });

    return () => {
      active = false;
    };
  }, [fileAssetKinds, fileAssetVariables.length]);

  const workflowFileInputEnabled =
    fileAssetVariables.length > 0 &&
    fileAssetVariables.every(
      (variableName) =>
        fileCapabilityForVariable(variableName)?.interaction_status === "ready",
    );
  const workflowFileDisabledReason =
    fileCapabilityError ||
    fileCapabilities.find((item) => item.interaction_status !== "ready")
      ?.status_reason ||
    (fileCapabilityLoading ? "正在读取文件能力..." : "工作流文件资产当前未启用。");
  const workflowFilesReady = fileAssetVariables.every(
    (variable) => fileSelections[variable]?.asset?.status === "ready",
  );
  const workflowFileBusy = fileAssetVariables.some(
    (variable) => fileSelections[variable]?.busy,
  ) || Boolean(deletingWorkflowAssetId);

  const refreshWorkflowFileAssets = useCallback(async () => {
    const requestedScopeId = fileScopeId;
    if (requestedScopeId !== workflowFileScopeRef.current) return;
    const generation = workflowFileListGeneration.current + 1;
    workflowFileListGeneration.current = generation;
    setWorkflowFileListLoading(true);
    setWorkflowFileListError("");
    try {
      const response = await fetch(
        `/api/files?purpose=workflow&scope_id=${encodeURIComponent(fileScopeId)}`,
      );
      const payload = (await response.json().catch(() => null)) as
        | WorkflowFileAssetListResponse
        | null;
      if (!response.ok || !payload || !Array.isArray(payload.items)) {
        throw new Error(apiErrorMessage(payload, "无法读取已有文件资产。"));
      }
      if (
        requestedScopeId !== workflowFileScopeRef.current ||
        generation !== workflowFileListGeneration.current
      ) return;
      setWorkflowFileAssets(payload.items);
    } catch (caught) {
      if (
        requestedScopeId !== workflowFileScopeRef.current ||
        generation !== workflowFileListGeneration.current
      ) return;
      setWorkflowFileAssets([]);
      setWorkflowFileListError(
        caught instanceof Error ? caught.message : "无法读取已有文件资产。",
      );
    } finally {
      if (
        requestedScopeId === workflowFileScopeRef.current &&
        generation === workflowFileListGeneration.current
      ) {
        setWorkflowFileListLoading(false);
      }
    }
  }, [fileScopeId]);

  useEffect(() => {
    if (fileAssetVariables.length === 0 || !workflowFileInputEnabled) {
      workflowFileListGeneration.current += 1;
      setWorkflowFileAssets([]);
      setWorkflowFileListError("");
      setWorkflowFileListLoading(false);
      return;
    }
    void refreshWorkflowFileAssets();
    return () => {
      workflowFileListGeneration.current += 1;
    };
  }, [
    fileAssetVariables.length,
    refreshWorkflowFileAssets,
    workflowFileInputEnabled,
  ]);

  const finalOutput = useMemo(() => {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      if (events[index].event === "workflow_end") {
        return events[index].final_output ?? "";
      }
    }

    return "";
  }, [events]);
  const finalBatchReceipts = useMemo(
    () => parseWorkflowBatchReceipts(finalOutput),
    [finalOutput],
  );

  const runSteps = useMemo(() => buildRunSteps(events), [events]);
  const expectedHookSkillIds = useMemo(
    () => hookSkillIdsFromWorkflowNodes(definition.nodes),
    [definition.nodes],
  );
  const expectedRequiredSkillIds = useMemo(
    () => requiredSkillIdsFromWorkflowNodes(definition.nodes),
    [definition.nodes],
  );
  const skillCaptureSource = useMemo(
    () => completedWorkflowCaptureSource(events, taskId, runId, isRunning),
    [events, isRunning, runId, taskId],
  );
  const skillCreatorHandoff = useMemo(
    () => latestSkillCreatorHandoff(events),
    [events],
  );
  const skillCaptureEnabled = Boolean(
    skillCreatorStatus?.enabled
    && skillCreatorStatus.supported_sources.includes("workflow_classic"),
  );

  async function consumeWorkflowResponse(
    response: Response,
    replaceEvents = false,
  ): Promise<WorkflowStreamProgress> {
    const reader = response.body?.getReader();
    if (!reader) throw new Error("当前浏览器不支持流式运行结果。");
    if (replaceEvents) setEvents([]);

    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let receivedEvent = false;
    const progress: WorkflowStreamProgress = {
      lastSequence: 0,
      waitingForAgentHandoff: false,
      terminal: false,
    };
    const acceptEvent = (event: WorkflowRunEvent) => {
      receivedEvent = true;
      progress.lastSequence = Math.max(progress.lastSequence, event.sequence ?? 0);
      if (event.event === "agent_handoff_waiting") {
        progress.waitingForAgentHandoff = true;
      }
      if (["workflow_end", "workflow_cancelled", "error"].includes(event.event)) {
        progress.terminal = true;
      }
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
    if (receivedEvent) {
      await refreshWorkflowFileOutputs();
    }
    return progress;
  }

  async function consumeWorkflowThroughAgentHandoff(
    response: Response,
    options: {
      signal: AbortSignal;
      taskId: string;
      replaceEvents?: boolean;
    },
  ) {
    let progress = await consumeWorkflowResponse(
      response,
      options.replaceEvents ?? false,
    );
    if (!progress.waitingForAgentHandoff || progress.terminal) return;

    const followTaskId = options.taskId.trim();
    if (!WORKFLOW_TASK_ID_PATTERN.test(followTaskId)) {
      throw new Error("无法继续跟踪协作任务。请刷新页面恢复运行状态。");
    }
    let cursor = progress.lastSequence;
    while (!progress.terminal) {
      await new Promise<void>((resolve) => {
        window.setTimeout(resolve, AGENT_HANDOFF_STREAM_POLL_MS);
      });
      if (options.signal.aborted) {
        throw new DOMException("Workflow stream aborted.", "AbortError");
      }
      const followResponse = await fetch(
        "/api/workflow/run/"
          + encodeURIComponent(followTaskId)
          + "/stream?after_sequence="
          + cursor,
        { signal: options.signal },
      );
      if (!followResponse.ok) {
        throw new Error("无法继续跟踪协作任务。请刷新页面恢复运行状态。");
      }
      const next = await consumeWorkflowResponse(followResponse);
      cursor = Math.max(cursor, next.lastSequence);
      progress = {
        lastSequence: cursor,
        waitingForAgentHandoff: true,
        terminal: next.terminal,
      };
    }
  }

  useEffect(() => {
    const recovery = readWorkflowRunRecovery(definition.id);
    if (!recovery) return;
    const abort = new AbortController();
    let active = true;

    setTaskId(recovery.taskId);
    setRunId(recovery.runId);
    runMetaRef.current = {
      taskId: recovery.taskId,
      runId: recovery.runId,
    };
    setIsResuming(true);
    fetch(
      `/api/workflow/run/${encodeURIComponent(recovery.taskId)}/stream?after_sequence=0`,
      { signal: abort.signal },
    )
      .then(async (response) => {
        if (!response.ok) {
          if (response.status === 404) {
            window.sessionStorage.removeItem(workflowRunRecoveryKey(definition.id));
          }
          throw new Error("无法恢复上次运行。请重新运行工作流。");
        }
        await consumeWorkflowThroughAgentHandoff(response, {
          signal: abort.signal,
          taskId: recovery.taskId,
          replaceEvents: true,
        });
      })
      .catch((caught) => {
        if (!active || (caught instanceof DOMException && caught.name === "AbortError")) {
          return;
        }
        setError(caught instanceof Error ? caught.message : "无法恢复上次运行。");
      })
      .finally(() => {
        if (active) setIsResuming(false);
      });

    return () => {
      active = false;
      abort.abort();
    };
    // The persisted task pointer is scoped by immutable workflow id. Event
    // handlers intentionally remain the same ones used by live execution.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [definition.id]);

  async function selectWorkflowFile(
    variableName: string,
    event: React.ChangeEvent<HTMLInputElement>,
  ) {
    const uploadScopeId = fileScopeId;
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    const fileCapability = fileCapabilityForVariable(variableName);
    if (!file || !workflowFileInputEnabled || !fileCapability) return;
    if (file.size > fileCapability.max_bytes_per_file) {
      setFileSelections((current) => ({
        ...current,
        [variableName]: {
          asset: current[variableName]?.asset ?? null,
          busy: false,
          error: `文件超过 ${formatFileSize(fileCapability.max_bytes_per_file)} 上限。`,
          notice: "",
        },
      }));
      return;
    }

    setFileSelections((current) => ({
      ...current,
      [variableName]: {
        asset: current[variableName]?.asset ?? null,
        busy: true,
        error: "",
        notice: "",
      },
    }));
    setWorkflowFileListNotice("");
    try {
      const body = new FormData();
      body.append("purpose", "workflow");
      body.append("scope_id", fileScopeId);
      body.append(
        "input_kind",
        fileAssetKinds.get(variableName) ?? "document",
      );
      body.append("file", file);
      const response = await fetch("/api/files", {
        method: "POST",
        body,
      });
      const payload = (await response.json().catch(() => null)) as
        | WorkflowFileAsset
        | null;
      if (!response.ok || !payload?.asset_id) {
        throw new Error(apiErrorMessage(payload, "文件上传失败，请重试。"));
      }
      if (uploadScopeId !== workflowFileScopeRef.current) return;
      setFileSelections((current) => ({
        ...current,
        [variableName]: {
          asset: payload,
          busy: false,
          error: "",
          notice: "文件已上传并选中用于本轮。",
        },
      }));
      await refreshWorkflowFileAssets();
    } catch (caught) {
      if (uploadScopeId !== workflowFileScopeRef.current) return;
      setFileSelections((current) => ({
        ...current,
        [variableName]: {
          asset: current[variableName]?.asset ?? null,
          busy: false,
          error: caught instanceof Error ? caught.message : "文件上传失败，请重试。",
          notice: "",
        },
      }));
    }
  }

  function selectExistingWorkflowFile(variableName: string, assetId: string) {
    const asset = workflowFileAssets.find((item) => item.asset_id === assetId) ?? null;
    setFileSelections((current) => ({
      ...current,
      [variableName]: {
        asset,
        busy: false,
        error: "",
        notice: asset ? "已选择已有文件用于本轮。" : "",
      },
    }));
  }

  function removeWorkflowFileFromRun(variableName: string) {
    setFileSelections((current) => ({
      ...current,
      [variableName]: {
        asset: null,
        busy: false,
        error: "",
        notice: "已从本轮移除，文件仍保留在当前工作流。",
      },
    }));
  }

  async function deleteWorkflowFile(asset: WorkflowFileAsset) {
    if (!confirmWorkflowFileDeletion(window.confirm.bind(window))) return;
    const deleteScopeId = fileScopeId;
    setDeletingWorkflowAssetId(asset.asset_id);
    setWorkflowFileListError("");
    setWorkflowFileListNotice("");
    try {
      const response = await fetch(
        `/api/files/${encodeURIComponent(asset.asset_id)}?purpose=workflow&scope_id=${encodeURIComponent(fileScopeId)}`,
        { method: "DELETE" },
      );
      if (!response.ok && response.status !== 202) {
        const payload = await response.json().catch(() => null);
        throw new Error(apiErrorMessage(payload, "文件删除失败，请重试。"));
      }
      const cleanupPending = response.status === 202;
      if (deleteScopeId !== workflowFileScopeRef.current) return;
      setFileSelections((current) =>
        Object.fromEntries(
          Object.entries(current).map(([variableName, selection]) => [
            variableName,
            selection.asset?.asset_id === asset.asset_id
              ? { asset: null, busy: false, error: "", notice: "" }
              : selection,
          ]),
        ),
      );
      setWorkflowFileListNotice(
        cleanupPending
          ? "已解除绑定，物理清理待完成。"
          : "已解除当前工作流绑定。",
      );
      await refreshWorkflowFileAssets();
    } catch (caught) {
      if (deleteScopeId !== workflowFileScopeRef.current) return;
      setWorkflowFileListError(
        caught instanceof Error ? caught.message : "文件删除失败，请重试。",
      );
    } finally {
      setDeletingWorkflowAssetId("");
    }
  }

  async function runWorkflow() {
    if (fileAssetVariables.length > 0 && !workflowFileInputEnabled) {
      setError(workflowFileDisabledReason);
      return;
    }
    if (!workflowFilesReady) {
      setError("请先为每个文件资产变量选择一个已就绪文件。");
      return;
    }
    const declared = parseWorkflowDeclaredInputs(
      definition.variables ?? [],
      declaredInputValues,
    );
    if (declared.error) {
      setError(declared.error);
      return;
    }
    onRunStart?.();
    definition.nodes.forEach((node) =>
      onNodeStatusChange?.(node.id, "idle"),
    );
    failedNodesRef.current.clear();
    runningNodesRef.current.clear();
    setFinishedOutcome(null);
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
    terminalHistoryRecordedRef.current = false;
    lastNodeErrorRef.current = "";

    const abort = new AbortController();
    runAbortRef.current = abort;

    try {
      const response = await fetch("/api/workflow/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: abort.signal,
        body: JSON.stringify({
          workflow: serializeWorkflow(definition),
          inputs: {
            [inputVariable]: input,
            ...(inputVariable === "user_input" ? {} : { user_input: input }),
            ...declared.inputs,
            ...Object.fromEntries(
              fileAssetVariables.map((variableName) => [
                variableName,
                fileSelections[variableName]?.asset?.asset_id ?? "",
              ]),
            ),
          },
        }),
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(apiErrorMessage(payload, "工作流运行失败。"));
      }

      const responseTaskId = response.headers.get(
        "X-ModelMirror-Runtime-Task-Id",
      );
      const responseRunId = response.headers.get(
        "X-ModelMirror-Runtime-Run-Id",
      );
      if (responseTaskId) {
        setTaskId(responseTaskId);
        runMetaRef.current.taskId = responseTaskId;
      }
      if (responseRunId) {
        setRunId(responseRunId);
        runMetaRef.current.runId = responseRunId;
      }
      if (responseTaskId) {
        persistWorkflowRunRecovery(definition.id, {
          taskId: responseTaskId,
          runId: responseRunId,
        });
      }

      await consumeWorkflowThroughAgentHandoff(response, {
        signal: abort.signal,
        taskId: responseTaskId ?? "",
      });
      if (
        shouldRecordNodeStreamFailure(
          failedNodesRef.current.size,
          terminalHistoryRecordedRef.current,
        )
      ) {
        const message = lastNodeErrorRef.current || "工作流运行异常。";
        setFinishedOutcome("error");
        setError(message);
        recordRunHistory("error", message);
      }
    } catch (runError) {
      const cancelled =
        runError instanceof DOMException && runError.name === "AbortError";
      if (cancelled) {
        setFinishedOutcome("cancelled");
        // 取消时把仍在运行的节点清回 idle，避免残留"运行中"高亮。
        runningNodesRef.current.forEach((nodeId) =>
          onNodeStatusChange?.(nodeId, "idle"),
        );
        runningNodesRef.current.clear();
        recordRunHistory("cancelled", "已取消。");
      } else {
        setFinishedOutcome("error");
        setError(
          runError instanceof Error ? runError.message : "工作流运行失败。",
        );
      }
    } finally {
      setIsRunning(false);
      runAbortRef.current = null;
    }
  }

  async function cancelWorkflow() {
    const abort = runAbortRef.current;
    const activeTaskId = runMetaRef.current.taskId;
    if (!abort) return;
    if (!activeTaskId) {
      abort.abort();
      return;
    }

    setError("");
    try {
      const response = await fetch(`/api/workflow/run/${activeTaskId}/cancel`, {
        method: "POST",
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as
          | { error?: string; detail?: string }
          | null;
        throw new Error(
          payload?.error ?? payload?.detail ?? "工作流取消失败，请重试。",
        );
      }
      abort.abort();
    } catch (cancelError) {
      setError(
        cancelError instanceof Error
          ? cancelError.message
          : "工作流取消失败，请重试。",
      );
    }
  }

  function retryWorkflow() {
    void runWorkflow();
  }

  function recordRunHistory(status: RunHistoryEntry["status"], summary?: string) {
    if (terminalHistoryRecordedRef.current) return;
    const { taskId: refTaskId, runId: refRunId } = runMetaRef.current;
    if (!refTaskId && !refRunId) return;
    terminalHistoryRecordedRef.current = true;
    setRunHistory((current) => [
      {
        runId: refRunId,
        taskId: refTaskId,
        finishedAt: Date.now(),
        status,
        summary: summary ?? status,
      },
      ...current,
    ]);
  }

  function handleRunEvent(event: WorkflowRunEvent) {
    if (event.event === "node_start" && event.node_id) {
      failedNodesRef.current.delete(event.node_id);
      runningNodesRef.current.add(event.node_id);
      onNodeStatusChange?.(event.node_id, "running");
    }
    if (event.event === "node_end" && event.node_id) {
      runningNodesRef.current.delete(event.node_id);
      // 节点已失败（收到过带 node_id 的 error）时不被 node_end(completed) 覆盖。
      if (!failedNodesRef.current.has(event.node_id)) {
        onNodeStatusChange?.(event.node_id, "done");
      }
    }
    if (event.event === "node_skipped" && event.node_id) {
      runningNodesRef.current.delete(event.node_id);
      onNodeStatusChange?.(event.node_id, "skipped");
    }
    if (event.event === "error") {
      if (event.node_id) {
        lastNodeErrorRef.current = event.message ?? "工作流节点运行异常。";
        failedNodesRef.current.add(event.node_id);
        runningNodesRef.current.delete(event.node_id);
        onNodeStatusChange?.(event.node_id, "error");
      } else {
        // 顶层致命错误：把仍在运行的节点全部标记为失败。
        runningNodesRef.current.forEach((nodeId) =>
          onNodeStatusChange?.(nodeId, "error"),
        );
        runningNodesRef.current.clear();
        setFinishedOutcome("error");
        recordRunHistory("error", event.message ?? "工作流运行异常。");
      }
    }
    if (event.event === "workflow_meta" && event.task_id) {
      setTaskId(event.task_id);
      runMetaRef.current.taskId = event.task_id;
    }
    if (event.event === "workflow_cancelled") {
      runningNodesRef.current.forEach((nodeId) =>
        onNodeStatusChange?.(nodeId, "idle"),
      );
      runningNodesRef.current.clear();
      setPendingHuman(null);
      setHumanInput("");
      setFinishedOutcome("cancelled");
      recordRunHistory("cancelled", event.message ?? "已取消。");
    }
    if (
      (event.event === "workflow_meta" || event.event === "workflow_end") &&
      event.run_id
    ) {
      setRunId(event.run_id);
      runMetaRef.current.runId = event.run_id;
    }
    if (event.event === "human_intervention_pending") {
      if (event.contract_version !== 2) {
        setPendingHuman({
          nodeId: event.node_id ?? "",
          nodeTitle: event.node_title ?? "人工介入",
          prompt: event.prompt ?? "请补充人工输入。",
          outputVariable: event.output_variable ?? "human_input",
        });
        setHumanInput("");
      }
    }
    if (event.event === "node_end") {
      setPendingHuman((current) =>
        current?.nodeId === event.node_id ? null : current,
      );
    }
    if (event.event === "workflow_end") {
      runningNodesRef.current.forEach((nodeId) =>
        onNodeStatusChange?.(nodeId, "done"),
      );
      runningNodesRef.current.clear();
      setPendingHuman(null);
      setHumanInput("");
      setFinishedOutcome("completed");
      recordRunHistory(
        "completed",
        workflowRunCompletedSummary(event.final_output),
      );
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
    const abort = new AbortController();
    try {
      const response = await fetch(
        `/api/workflow/run/${taskId}/stream?after_sequence=0`,
        { signal: abort.signal },
      );
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as
          | { detail?: string }
          | null;
        throw new Error(payload?.detail || "恢复执行流失败");
      }
      await consumeWorkflowThroughAgentHandoff(response, {
        signal: abort.signal,
        taskId,
        replaceEvents: true,
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "恢复执行流失败");
    } finally {
      abort.abort();
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
          ? "flex min-h-0 min-w-0 flex-1 flex-col"
          : "surface-panel flex min-h-0 min-w-0 flex-col rounded-lg"
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

        {declaredInputs.length > 0 ? (
          <div className="space-y-3 rounded-lg border border-brand-300/15 bg-brand-300/[0.04] p-3">
            <div>
              <p className="text-xs font-semibold text-slate-200">工作流输入</p>
              <p className="mt-1 text-[11px] leading-5 text-slate-500">
                本轮值会覆盖同名输入默认值，但不能覆盖常量。
              </p>
            </div>
            {declaredInputs.map((declaration) => (
              <label className="block" key={declaration.id}>
                <span className="flex items-center justify-between gap-2 text-xs font-semibold text-slate-300">
                  <code>{declaration.name}</code>
                  <span className="text-[10px] font-normal uppercase text-slate-500">
                    {declaration.valueType}
                  </span>
                </span>
                {declaration.description ? (
                  <span className="mt-1 block text-[11px] leading-5 text-slate-500">
                    {declaration.description}
                  </span>
                ) : null}
                {declaration.valueType === "boolean" ? (
                  <select
                    className="modelmirror-form-control mt-2 min-h-11 w-full rounded-md border border-white/10 bg-slate-950/60 px-3 text-sm text-white outline-none focus:border-brand-300/45"
                    onChange={(event) =>
                      setDeclaredInputValues((current) => ({
                        ...current,
                        [declaration.id]: event.target.value,
                      }))
                    }
                    value={declaredInputValues[declaration.id] ?? ""}
                  >
                    {declaration.defaultValue === undefined ? (
                      <option value="">请选择</option>
                    ) : null}
                    <option value="true">true</option>
                    <option value="false">false</option>
                  </select>
                ) : declaration.valueType === "json" ? (
                  <textarea
                    className="mt-2 min-h-24 w-full resize-y rounded-md border border-white/10 bg-slate-950/60 px-3 py-2 font-mono text-xs leading-5 text-white outline-none focus:border-brand-300/45"
                    onChange={(event) =>
                      setDeclaredInputValues((current) => ({
                        ...current,
                        [declaration.id]: event.target.value,
                      }))
                    }
                    placeholder='{"key":"value"}'
                    value={declaredInputValues[declaration.id] ?? ""}
                  />
                ) : (
                  <input
                    className="modelmirror-form-control mt-2 min-h-11 w-full rounded-md border border-white/10 bg-slate-950/60 px-3 text-sm text-white outline-none focus:border-brand-300/45"
                    inputMode={declaration.valueType === "number" ? "decimal" : undefined}
                    onChange={(event) =>
                      setDeclaredInputValues((current) => ({
                        ...current,
                        [declaration.id]: event.target.value,
                      }))
                    }
                    type={declaration.valueType === "number" ? "number" : "text"}
                    value={declaredInputValues[declaration.id] ?? ""}
                  />
                )}
              </label>
            ))}
          </div>
        ) : null}

        {fileAssetVariables.length > 0 ? (
          <div className="space-y-2 border-t border-white/10 pt-3">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-xs font-semibold text-slate-300">文件资产</p>
                <p className="mt-1 text-[11px] leading-5 text-slate-500">
                  文件只绑定到当前工作流作用域，运行请求仅提交 asset_id。
                </p>
              </div>
              <span
                aria-live="polite"
                className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] ${
                  workflowFileInputEnabled
                    ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                    : "border-amber-300/25 bg-amber-300/10 text-amber-100"
                }`}
                role="status"
              >
                {fileCapabilityLoading
                  ? "检查中"
                  : workflowFileInputEnabled
                    ? "可用"
                    : "未启用"}
              </span>
            </div>

            {!workflowFileInputEnabled ? (
              <p
                aria-live="polite"
                className="rounded-md border border-amber-300/20 bg-amber-300/10 px-2.5 py-2 text-[11px] leading-5 text-amber-100"
                role="status"
              >
                {workflowFileDisabledReason}
              </p>
            ) : null}

            {fileAssetVariables.map((variableName) => {
              const selection = fileSelections[variableName];
              const asset = selection?.asset;
              return (
                <div
                  aria-label={`${variableName} 文件资产`}
                  className="rounded-md border border-white/10 bg-white/[0.035] px-3 py-2.5 outline-none transition focus:border-cyan-300/50 focus:ring-2 focus:ring-cyan-300/20"
                  key={variableName}
                  ref={(element) => {
                    fileInputCardRefs.current[variableName] = element;
                  }}
                  tabIndex={-1}
                >
                  <p className="truncate font-mono text-[11px] text-slate-300">
                    {variableName}
                  </p>
                  <select
                    aria-label={`${variableName} 已有文件`}
                    className="modelmirror-form-control mt-2 min-h-11 w-full rounded-md border border-white/10 bg-slate-950/60 px-2.5 py-1.5 text-xs text-white outline-none transition focus:border-hire-300/45 disabled:cursor-not-allowed disabled:text-slate-500"
                    disabled={
                      !workflowFileInputEnabled ||
                      workflowFileListLoading ||
                      Boolean(selection?.busy) ||
                      Boolean(deletingWorkflowAssetId) ||
                      isRunning
                    }
                    onChange={(event) =>
                      selectExistingWorkflowFile(variableName, event.target.value)
                    }
                    value={asset?.asset_id ?? ""}
                  >
                    <option value="">选择已有文件</option>
                    {workflowFileAssets
                      .filter((item) =>
                        fileFormatAllowedForVariable(variableName, item.format),
                      )
                      .map((item) => (
                      <option key={item.asset_id} value={item.asset_id}>
                        {item.display_name} · {item.format.toUpperCase()}
                      </option>
                      ))}
                  </select>
                  {asset ? (
                    <p className="mt-1.5 truncate text-[11px] text-slate-400">
                      本轮：{asset.display_name} · {formatFileSize(asset.byte_size)}
                    </p>
                  ) : null}
                  <div className="mt-2 flex flex-wrap gap-2">
                    <label
                      className={`flex min-h-11 items-center rounded-full border px-3 py-1 text-[11px] font-semibold outline-none transition focus-within:ring-2 focus-within:ring-hire-300/60 focus-within:ring-offset-2 focus-within:ring-offset-slate-950 ${
                        workflowFileInputEnabled && !selection?.busy && !isRunning
                          ? "cursor-pointer border-hire-300/30 bg-hire-300/10 text-hire-100 hover:bg-hire-300/20"
                          : "cursor-not-allowed border-white/10 bg-white/[0.03] text-slate-500"
                      }`}
                    >
                      {selection?.busy ? "上传中" : "上传新文件"}
                      <input
                        accept={fileAcceptForVariable(variableName)}
                        aria-label={`为 ${variableName} 上传新文件`}
                        className="sr-only"
                        disabled={
                          !workflowFileInputEnabled ||
                          Boolean(selection?.busy) ||
                          Boolean(deletingWorkflowAssetId) ||
                          isRunning
                        }
                        onChange={(event) =>
                          void selectWorkflowFile(variableName, event)
                        }
                        type="file"
                      />
                    </label>
                    {asset ? (
                      <button
                        aria-label={`从本轮移除 ${asset.display_name}`}
                        className="min-h-11 rounded-full border border-white/15 px-3 py-1 text-[11px] font-semibold text-slate-300 transition hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-50"
                        disabled={Boolean(selection?.busy) || isRunning}
                        onClick={() => removeWorkflowFileFromRun(variableName)}
                        type="button"
                      >
                        移出本轮
                      </button>
                    ) : null}
                  </div>
                  {selection?.error ? (
                    <p
                      aria-live="assertive"
                      className="mt-2 text-[11px] leading-5 text-rose-200"
                      role="alert"
                    >
                      {selection.error}
                    </p>
                  ) : null}
                  {selection?.notice ? (
                    <p
                      aria-live="polite"
                      className="mt-2 text-[11px] leading-5 text-emerald-200"
                      role="status"
                    >
                      {selection.notice}
                    </p>
                  ) : null}
                </div>
              );
            })}

            {workflowFileInputEnabled ? (
              <div className="border-t border-white/10 pt-2.5">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-[11px] font-semibold text-slate-300">
                    当前工作流已有文件（{workflowFileAssets.length}）
                  </p>
                  <button
                    className="min-h-11 rounded-full border border-white/10 px-3 py-1 text-[11px] font-semibold text-slate-300 transition hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={workflowFileListLoading || Boolean(deletingWorkflowAssetId)}
                    onClick={() => void refreshWorkflowFileAssets()}
                    type="button"
                  >
                    {workflowFileListLoading ? "刷新中" : "刷新列表"}
                  </button>
                </div>
                {workflowFileListError ? (
                  <p
                    aria-live="assertive"
                    className="mt-2 text-[11px] leading-5 text-rose-200"
                    role="alert"
                  >
                    {workflowFileListError}
                  </p>
                ) : null}
                {workflowFileListNotice ? (
                  <p
                    aria-live="polite"
                    className="mt-2 text-[11px] leading-5 text-emerald-200"
                    role="status"
                  >
                    {workflowFileListNotice}
                  </p>
                ) : null}
                {!workflowFileListLoading && workflowFileAssets.length === 0 ? (
                  <p className="mt-2 text-[11px] leading-5 text-slate-500">
                    暂无持久文件。上传后会出现在这里，刷新页面也可重新选择。
                  </p>
                ) : null}
                {workflowFileAssets.length > 0 ? (
                  <div className="mt-2 max-h-36 space-y-1.5 overflow-y-auto">
                    {workflowFileAssets.map((item) => (
                      <div
                        className="flex items-center justify-between gap-3 rounded-md bg-slate-950/30 px-2.5 py-1.5"
                        key={item.asset_id}
                      >
                        <p className="min-w-0 truncate text-[11px] text-slate-300">
                          {item.display_name}
                          <span className="ml-1.5 text-[10px] text-slate-500">
                            {item.format.toUpperCase()} · {formatFileSize(item.byte_size)}
                          </span>
                        </p>
                        <button
                          aria-label={`彻底删除 ${item.display_name}`}
                          className="min-h-11 shrink-0 rounded-full border border-rose-300/25 px-3 py-1 text-[11px] font-semibold text-rose-100 transition hover:bg-rose-300/10 disabled:cursor-not-allowed disabled:opacity-50"
                          disabled={Boolean(deletingWorkflowAssetId) || isRunning}
                          onClick={() => void deleteWorkflowFile(item)}
                          type="button"
                        >
                          {deletingWorkflowAssetId === item.asset_id
                            ? "删除中"
                            : "彻底删除"}
                        </button>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="flex gap-2">
          {isRunning ? (
            <button
              className="w-full rounded-full border border-rose-300/40 bg-rose-400/10 px-4 py-2.5 text-sm font-semibold text-rose-100 transition hover:bg-rose-400/25 active:scale-[0.98]"
              onClick={cancelWorkflow}
              type="button"
            >
              取消运行
            </button>
          ) : (
            <button
              className="w-full rounded-full bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 active:scale-[0.98] disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
              disabled={
                workflowFileBusy ||
                (fileAssetVariables.length > 0 &&
                  (!workflowFileInputEnabled || !workflowFilesReady))
              }
              onClick={() => void runWorkflow()}
              type="button"
            >
              运行工作流
            </button>
          )}
          {finishedOutcome ? (
            <button
              className="shrink-0 rounded-full border border-brand-300/35 bg-brand-300/10 px-3 py-2.5 text-sm font-semibold text-brand-100 transition hover:bg-brand-300/20 active:scale-[0.98]"
              onClick={retryWorkflow}
              title="重新运行"
              type="button"
            >
              ↻
            </button>
          ) : null}
        </div>
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
            requestTypes={["tool_call", "final_output", "manual_input", "execution_gate", "browser_domain"]}
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
        <div
          aria-live="assertive"
          className="mx-4 rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-sm text-rose-100"
          role="alert"
        >
          {error}
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <SkillApplicationCard
          className="mb-3"
          events={events}
          expectedRequiredSkillIds={expectedRequiredSkillIds}
        />
        <SkillHookApplicationCard
          className="mb-3"
          events={events}
          expectedSkillIds={expectedHookSkillIds}
        />
        {runHistory.length > 0 ? (
          <div className="mb-3 rounded-lg border border-white/10 bg-white/[0.03] p-3">
            <p className="text-[11px] font-semibold text-slate-400">最近运行</p>
            <div className="mt-2 space-y-1.5">
              {runHistory.map((entry, index) => (
                <div
                  className="flex items-center justify-between gap-3 text-[11px]"
                  key={`${entry.taskId}-${entry.finishedAt}`}
                >
                  <span className="min-w-0 text-slate-400">
                    <span className="block font-medium text-slate-300">
                      {index === 0 ? "当前" : `上次 ${index}`} ·{" "}
                      {new Date(entry.finishedAt).toLocaleTimeString()}
                      {entry.runId || entry.taskId
                        ? ` · ${(entry.runId ?? entry.taskId)?.slice(0, 8)}`
                        : ""}
                    </span>
                    <span className="mt-0.5 block truncate" title={entry.summary}>
                      {entry.summary}
                    </span>
                  </span>
                  <span
                    className={`shrink-0 rounded-full border px-2 py-0.5 ${
                      entry.status === "completed"
                        ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
                        : entry.status === "cancelled"
                          ? "border-slate-300/25 bg-slate-300/10 text-slate-200"
                          : "border-rose-300/25 bg-rose-300/10 text-rose-100"
                    }`}
                  >
                    {entry.status === "completed"
                      ? "完成"
                      : entry.status === "cancelled"
                        ? "取消"
                        : "异常"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <div className="space-y-2">
          {runSteps.length === 0 ? (
            <div className="rounded-lg border border-dashed border-white/15 bg-white/[0.035] px-4 py-8 text-center text-sm leading-6 text-slate-400">
              {isRunning
                ? "正在等待工作流事件..."
                : "暂无运行记录。点击“运行工作流”后，这里会按节点汇总展示过程。"}
            </div>
          ) : (
            runSteps.map((step) => {
              const outputPreview = workflowStepOutputPreview(
                step.output,
                step.type,
                step.variable,
              );
              const batchReceipts =
                step.type === "http_request"
                  ? null
                  : parseWorkflowBatchReceipts(step.output);
              const knowledgeProposalReceipt = step.type === "knowledge_write_proposal"
                ? parseKnowledgeProposalReceipt(step.output)
                : null;
              return (
              <div className="rounded-lg border border-white/10 bg-white/[0.045]" key={step.id}>
                <button
                  className="w-full px-3 py-2 text-left transition hover:bg-white/[0.025]"
                  onClick={() => onStepSelect?.(step.id)}
                  title="在画布上定位该节点"
                  type="button"
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
                  batchReceipts ? (
                    <WorkflowBatchReceiptList compact receipts={batchReceipts} />
                  ) : (
                    <>
                    <DataXResultCard content={outputPreview} />
                    <p className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap break-words rounded-md bg-slate-950/35 p-2 text-xs leading-5 text-slate-300">
                      {outputPreview}
                    </p>
                    </>
                  )
                ) : null}
                </button>
                {step.providerRouteReceipt ? (
                  <div className="px-3 pb-2">
                    <ProviderRouteReceiptSummary compact receipt={step.providerRouteReceipt} />
                  </div>
                ) : null}
                {shouldShowHandoffInboxLink(step) ? (
                  <div className="px-3 pb-3">
                    <a
                      className="inline-flex rounded-md border border-violet-300/25 bg-violet-300/10 px-2.5 py-1.5 text-xs font-semibold text-violet-100 transition hover:border-violet-200/45 hover:bg-violet-300/15"
                      href="/agents/meta-agent#handoff-inbox"
                      target="_blank"
                      rel="noreferrer"
                    >
                      前往 Handoff Inbox
                    </a>
                  </div>
                ) : null}
                {knowledgeProposalReceipt ? (
                  <div className="px-3 pb-3">
                    <a
                      className="inline-flex rounded-md border border-teal-300/25 bg-teal-300/10 px-2.5 py-1.5 text-xs font-semibold text-teal-100 transition hover:border-teal-200/45 hover:bg-teal-300/15"
                      href={`/rag/${encodeURIComponent(knowledgeProposalReceipt.knowledgeBaseId)}/inbox?proposal_id=${encodeURIComponent(knowledgeProposalReceipt.proposalId)}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      打开 Knowledge Inbox 审批
                    </a>
                  </div>
                ) : null}
              </div>
              );
            })
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

      {skillCreatorHandoff ? (
        <div className="border-t border-white/10 p-4">
          <SkillCreatorHandoffCard
            captureEnabled={skillCaptureEnabled}
            captureSource={skillCaptureSource}
            event={skillCreatorHandoff}
          />
        </div>
      ) : null}

      {!skillCreatorHandoff && skillCaptureSource && skillCaptureEnabled ? (
        <div className="border-t border-white/10 p-4">
          <div className="flex flex-col gap-3 rounded-lg border border-emerald-300/20 bg-emerald-300/[0.06] p-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-xs font-semibold text-emerald-100">将这次成功运行沉淀为可复用流程</p>
              <p className="mt-1 text-[11px] leading-5 text-slate-400">只会读取服务端生成的脱敏素材，完整参数和原始输出不会进入草稿。</p>
            </div>
            <SkillCreatorCaptureButton
              enabled={skillCaptureEnabled}
              source={skillCaptureSource}
            />
          </div>
        </div>
      ) : null}

      {finalOutput ? (
        <div className="border-t border-white/10 p-4">
          <p className="text-xs font-semibold text-hire-100">最终交付</p>
          {finalBatchReceipts ? (
            <WorkflowBatchReceiptList receipts={finalBatchReceipts} />
          ) : (
            <p className="mt-2 max-h-44 overflow-y-auto whitespace-pre-wrap rounded-lg border border-hire-300/25 bg-hire-300/10 p-3 text-sm leading-6 text-hire-50">
              {finalOutput}
            </p>
          )}
        </div>
      ) : null}

      {workflowOutputsForRun(fileOutputs, runId).length > 0 ? (
        <div className="border-t border-white/10 px-4 pb-4">
          <FileOutputTray
            onChange={(next) => setFileOutputs((current) =>
              replaceWorkflowOutputSubset(
                current,
                workflowOutputsForRun(current, runId),
                next,
              ))}
            outputs={workflowOutputsForRun(fileOutputs, runId)}
            onReuse={workflowFileInputEnabled ? prepareWorkflowOutputReuse : undefined}
            purpose="workflow"
            reuseTargetId={definition.id}
            scopeId={fileScopeId}
            title="本次运行文件输出"
          />
        </div>
      ) : null}

      {recoveredWorkflowOutputs(fileOutputs, runId).length > 0 ? (
        <div className="border-t border-white/10 px-4 pb-4">
          <FileOutputTray
            onChange={(next) => setFileOutputs((current) =>
              replaceWorkflowOutputSubset(
                current,
                recoveredWorkflowOutputs(current, runId),
                next,
              ))}
            outputs={recoveredWorkflowOutputs(fileOutputs, runId)}
            onReuse={workflowFileInputEnabled ? prepareWorkflowOutputReuse : undefined}
            purpose="workflow"
            reuseTargetId={definition.id}
            scopeId={fileScopeId}
            title="已恢复的文件输出"
          />
        </div>
      ) : null}
    </aside>
  );
}
