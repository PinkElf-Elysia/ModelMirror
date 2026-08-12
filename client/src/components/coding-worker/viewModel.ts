import type {
  CodingWorkerApproval,
  CodingWorkerEvent,
  CodingWorkerEvidence,
  CodingWorkerTask,
  CodingWorkerTaskState,
} from "../../types/codingWorker";

export type WorkerTaskGroup = "attention" | "active" | "queued" | "history";
export type WorkerProgressStage = "analyze" | "reproduce" | "change" | "verify";
export type WorkerActivityTone = "neutral" | "running" | "success" | "warning" | "danger";

export interface WorkerActivity {
  sequence: number;
  title: string;
  detail: string;
  meta: string;
  tone: WorkerActivityTone;
  operationId: string | null;
}

export const terminalTaskStates = new Set<CodingWorkerTaskState>([
  "completed", "blocked", "failed", "cancelled", "budget_limited", "expired",
]);

export const taskStateCopy: Record<CodingWorkerTaskState, string> = {
  queued: "排队中",
  preparing: "准备工作区",
  running: "执行中",
  waiting_approval: "等待审批",
  waiting_input: "等待回答",
  waiting_subtasks: "等待子任务",
  paused: "已暂停",
  testing: "执行验收",
  interrupted: "需要恢复",
  completed: "已完成",
  blocked: "已阻塞",
  failed: "失败",
  cancelled: "已取消",
  budget_limited: "预算已用完",
  expired: "已过期",
};

export function taskGroup(task: CodingWorkerTask): WorkerTaskGroup {
  if (["waiting_approval", "waiting_input", "interrupted", "blocked"].includes(task.state)) return "attention";
  if (["preparing", "running", "waiting_subtasks", "testing", "paused"].includes(task.state)) return "active";
  if (task.state === "queued") return "queued";
  return "history";
}

export function groupTasks(tasks: CodingWorkerTask[]) {
  const groups: Record<WorkerTaskGroup, CodingWorkerTask[]> = {
    attention: [], active: [], queued: [], history: [],
  };
  tasks.forEach((task) => groups[taskGroup(task)].push(task));
  return groups;
}

export function currentProgressStage(
  task: CodingWorkerTask,
  events: CodingWorkerEvent[],
): WorkerProgressStage {
  if (["testing", "completed"].includes(task.state)) return "verify";
  if (events.some((event) => ["changeset", "workspace", "patch", "edit", "write_file"].some(
    (term) => `${event.type} ${eventPayloadText(event) ?? ""}`.toLowerCase().includes(term),
  ))) return "change";
  if (events.some((event) => ["tool_operation", "operation_output", "approval_requested"].includes(event.type))) {
    return "reproduce";
  }
  return "analyze";
}

export function eventPayloadText(event: CodingWorkerEvent) {
  const candidates = [
    event.payload.message,
    event.payload.text,
    event.payload.summary,
    event.payload.output,
  ];
  const value = candidates.find((item) => typeof item === "string");
  if (typeof value === "string") return value;
  const data = event.payload.data;
  if (typeof data === "string") return data;
  if (data && typeof data === "object") {
    const record = data as Record<string, unknown>;
    const nested = [record.message, record.text, record.summary, record.output]
      .find((item) => typeof item === "string");
    if (typeof nested === "string") return nested;
  }
  return null;
}

function eventTitle(event: CodingWorkerEvent) {
  if (event.type === "task_created") return "任务已进入队列";
  if (event.type === "task_state") {
    const next = event.payload.to;
    return typeof next === "string" && next in taskStateCopy
      ? `状态更新为${taskStateCopy[next as CodingWorkerTaskState]}`
      : "任务状态已更新";
  }
  if (event.type === "approval_requested") return "需要批准一次工具操作";
  if (event.type === "approval_decided") return "审批已处理";
  if (event.type === "plan_updated") return "结构化计划已更新";
  if (event.type === "todo_updated") return "待办状态已更新";
  if (event.type === "question_requested") return "Worker 正在等待回答";
  if (event.type === "question_resolved") return "问题已回答";
  if (event.type === "context_compacted") return "公开上下文已压缩";
  if (event.type === "subtask_created") return "子任务已创建";
  if (event.type === "subtask_completed") return "子任务已完成";
  if (event.type === "subtask_failed") return "子任务执行失败";
  if (event.type === "changeset_merged") return "子任务变更已合并";
  if (event.type === "changeset_conflicted") return "子任务变更存在冲突";
  if (event.type === "tool_operation") {
    const state = event.payload.state;
    if (state === "completed") return "工具操作已完成";
    if (state === "failed") return "工具操作失败";
    if (state === "unknown") return "工具结果需要核对";
    return "工具操作已更新";
  }
  if (event.type === "operation_output") return "终端输出已更新";
  if (event.type === "acceptance_evaluated") return "必需检查已执行";
  if (event.type === "acceptance_retry") return "检查未通过，开始修复";
  if (event.type === "steering_queued") return "追加指令已排队";
  if (event.type === "provider_event") {
    if (event.payload.kind === "plan") return "执行计划已更新";
    if (event.payload.kind === "message") return "Worker 回复";
    if (event.payload.kind === "tool_call") return "Worker 请求工具";
    if (event.payload.kind === "turn_completed") return "本轮执行完成";
  }
  return event.type.replaceAll("_", " ");
}

function eventTone(event: CodingWorkerEvent): WorkerActivityTone {
  const combined = `${event.type} ${String(event.payload.state ?? "")}`;
  if (/failed|blocked|rejected/.test(combined)) return "danger";
  if (/unknown|approval|interrupted|retry/.test(combined)) return "warning";
  if (/completed|decided|evaluated/.test(combined)) return "success";
  if (/running|provider|operation/.test(combined)) return "running";
  return "neutral";
}

export function activitiesFromEvents(events: CodingWorkerEvent[]): WorkerActivity[] {
  return events.filter((event) => {
    if (event.type !== "provider_event") return event.type !== "artifact_created";
    return event.payload.kind === "message" || event.payload.kind === "turn_completed";
  }).map((event) => {
    const operationId = typeof event.payload.operation_id === "string"
      ? event.payload.operation_id
      : null;
    return {
      sequence: event.sequence,
      title: eventTitle(event),
      detail: eventPayloadText(event) ?? eventDetail(event),
      meta: `#${event.sequence}`,
      tone: eventTone(event),
      operationId,
    };
  });
}

function eventDetail(event: CodingWorkerEvent) {
  if (event.type === "task_state") {
    const reason = event.payload.reason;
    return typeof reason === "string" && reason ? `原因：${reason}` : "状态已持久保存。";
  }
  if (event.type === "tool_operation") {
    const operationId = event.payload.operation_id;
    return typeof operationId === "string" ? `操作 ${shortId(operationId)}` : "工具执行状态已持久保存。";
  }
  if (event.type === "approval_requested") return "一次性审批已进入 Action Center。";
  if (event.type === "approval_decided") return "审批决定已持久保存。";
  if (event.type === "acceptance_evaluated") {
    const evidence = event.payload.evidence;
    if (Array.isArray(evidence)) return `已记录 ${evidence.length} 项检查结果。`;
  }
  return "详细数据已归档，可在检查器中查看。";
}

export function evidenceStatus(checkId: string, evidence: CodingWorkerEvidence[]) {
  return evidence
    .filter((item) => item.check_id === checkId)
    .sort((a, b) => b.created_at - a.created_at)[0]?.status ?? "pending";
}

export function pendingApprovals(approvals: CodingWorkerApproval[]) {
  return approvals.filter((approval) => approval.status === "pending");
}

export function routeLabel(route: string) {
  if (route === "coding/default") return "标准执行";
  if (route === "coding/quality") return "深度执行";
  return route.startsWith("coding/") ? route.slice("coding/".length) : route;
}

export function shortId(value: string, visible = 8) {
  return value.length > visible * 2 ? `${value.slice(0, visible)}…${value.slice(-visible)}` : value;
}

export function formatRelativeTime(timestamp: number) {
  const delta = Math.max(0, Math.round(Date.now() / 1000 - timestamp));
  if (delta < 60) return "刚刚";
  if (delta < 3600) return `${Math.floor(delta / 60)} 分钟前`;
  if (delta < 86400) return `${Math.floor(delta / 3600)} 小时前`;
  return `${Math.floor(delta / 86400)} 天前`;
}
