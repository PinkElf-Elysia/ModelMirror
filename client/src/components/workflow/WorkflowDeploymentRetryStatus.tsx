import type { WorkflowExecutionSummary } from "../../utils/workflowDeployments";

export interface WorkflowDeploymentRetryEvent {
  event: "node_retry_scheduled" | "node_retry_started" | "node_error_routed";
  nodeId: string;
  attempt: number;
  maxAttempts: number;
  resumeAt?: number;
  errorCode?: string;
  classification?: "transient" | "permanent";
}

const SAFE_IDENTIFIER = /^[A-Za-z0-9_.:-]{1,128}$/;
const SAFE_ERROR_CODE = /^[A-Z][A-Z0-9_]{0,63}$/;
const MAX_JAVASCRIPT_EPOCH_SECONDS = 8_640_000_000_000;

function finitePositiveInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value > 0
    ? value
    : null;
}

function safeEpochSeconds(value: unknown): number | null {
  return typeof value === "number"
    && Number.isFinite(value)
    && value > 0
    && value <= MAX_JAVASCRIPT_EPOCH_SECONDS
    ? value
    : null;
}

export function workflowDeploymentRetryEvents(
  execution: WorkflowExecutionSummary,
): WorkflowDeploymentRetryEvent[] {
  const raw = execution.trigger_summary?.retry_events;
  if (!Array.isArray(raw)) return [];
  return raw.slice(-8).flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const value = item as Record<string, unknown>;
    const event = value.event;
    if (
      event !== "node_retry_scheduled"
      && event !== "node_retry_started"
      && event !== "node_error_routed"
    ) {
      return [];
    }
    const nodeId = typeof value.node_id === "string" ? value.node_id : "";
    const attempt = finitePositiveInteger(value.attempt);
    const maxAttempts = finitePositiveInteger(value.max_attempts);
    if (
      !SAFE_IDENTIFIER.test(nodeId)
      || attempt === null
      || maxAttempts === null
      || attempt > maxAttempts
      || maxAttempts > 3
    ) {
      return [];
    }
    const resumeAt = safeEpochSeconds(value.resume_at);
    const errorCode = typeof value.error_code === "string" && SAFE_ERROR_CODE.test(value.error_code)
      ? value.error_code
      : undefined;
    const classification = value.classification === "transient" || value.classification === "permanent"
      ? value.classification
      : undefined;
    return [{
      event,
      nodeId,
      attempt,
      maxAttempts: Math.max(attempt, maxAttempts),
      ...(resumeAt !== null ? { resumeAt } : {}),
      ...(errorCode ? { errorCode } : {}),
      ...(classification ? { classification } : {}),
    }];
  });
}

function waitKindCopy(waitKind: string | null | undefined) {
  if (waitKind === "node_retry") return "等待自动重试";
  if (waitKind === "timer") return "定时等待";
  if (waitKind === "agent_handoff") return "等待协作结果";
  if (waitKind === "approval") return "等待审批";
  return waitKind ? "等待恢复" : "";
}

function retryEventCopy(event: WorkflowDeploymentRetryEvent) {
  if (event.event === "node_retry_started") {
    return `第 ${event.attempt}/${event.maxAttempts} 次尝试已开始`;
  }
  if (event.event === "node_error_routed") {
    return `第 ${event.attempt}/${event.maxAttempts} 次失败已进入错误分支`;
  }
  return `第 ${event.attempt}/${event.maxAttempts} 次尝试已排队`;
}

export default function WorkflowDeploymentRetryStatus({
  execution,
}: {
  execution: WorkflowExecutionSummary;
}) {
  const retryEvents = workflowDeploymentRetryEvents(execution);
  const waitCopy = waitKindCopy(execution.wait_kind);
  const resumeAt = safeEpochSeconds(execution.resume_at);
  if (!waitCopy && retryEvents.length === 0 && resumeAt === null) return null;

  return (
    <div className="mt-2 space-y-2 border-t border-cyan-200/10 pt-2">
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-cyan-50">
        {waitCopy ? <span>{waitCopy}</span> : null}
        {resumeAt !== null ? (
          <span>
            预计恢复：{new Date(resumeAt * 1000).toLocaleString()}
          </span>
        ) : null}
      </div>
      {retryEvents.length ? (
        <ul aria-label="部署重试历史" className="space-y-1 text-[11px] leading-5 text-slate-300">
          {retryEvents.map((event) => (
            <li
              className="flex flex-wrap gap-x-2"
              key={`${event.event}:${event.nodeId}:${event.attempt}`}
            >
              <span className="font-medium text-slate-200">{event.nodeId}</span>
              <span>{retryEventCopy(event)}</span>
              {event.errorCode ? <span>{event.errorCode}</span> : null}
              {event.classification ? (
                <span>{event.classification === "transient" ? "临时故障" : "永久故障"}</span>
              ) : null}
              {event.resumeAt && event.event === "node_retry_scheduled" ? (
                <time dateTime={new Date(event.resumeAt * 1000).toISOString()}>
                  {new Date(event.resumeAt * 1000).toLocaleString()}
                </time>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
