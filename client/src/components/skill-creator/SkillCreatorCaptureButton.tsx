import { useState } from "react";
import { useNavigate } from "react-router-dom";
import type { WorkflowRunEvent } from "../../types/workflow";
import type { XpertConversationMessage } from "../../types/xpert";
import {
  createSkillCreatorSession,
  SkillCreatorApiError,
} from "../../utils/skillCreatorApi";

export type SkillCreatorCaptureSource =
  | {
      sourceKind: "xpert_chat";
      taskId: string;
      runId: string;
      xpertId: string;
      conversationId: string;
      messageId: string;
    }
  | {
      sourceKind: "workflow_classic";
      taskId: string;
      runId: string;
    };

function cleanId(value: string | null | undefined) {
  return value?.trim() ?? "";
}

export function xpertMessageCaptureSource(
  message: XpertConversationMessage,
  xpertId: string,
  conversationId: string,
): SkillCreatorCaptureSource | null {
  const taskId = cleanId(message.source_task_id);
  const runId = cleanId(message.source_run_id);
  const messageId = cleanId(message.message_id);
  const cleanXpertId = cleanId(xpertId);
  const cleanConversationId = cleanId(conversationId);
  if (
    message.role !== "assistant"
    || !taskId
    || !runId
    || !messageId
    || !cleanXpertId
    || !cleanConversationId
  ) {
    return null;
  }
  return {
    sourceKind: "xpert_chat",
    taskId,
    runId,
    xpertId: cleanXpertId,
    conversationId: cleanConversationId,
    messageId,
  };
}

export function completedWorkflowCaptureSource(
  events: WorkflowRunEvent[],
  taskId: string | null,
  runId: string | null,
  isRunning: boolean,
): SkillCreatorCaptureSource | null {
  const cleanTaskId = cleanId(taskId);
  const cleanRunId = cleanId(runId);
  if (isRunning || !cleanTaskId || !cleanRunId) return null;

  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.event === "workflow_end") {
      return {
        sourceKind: "workflow_classic",
        taskId: cleanTaskId,
        runId: cleanRunId,
      };
    }
    if (
      event.event === "error"
      || event.event === "human_intervention_pending"
      || event.event === "runtime_approval_pending"
      || event.event === "client_tool_waiting"
    ) {
      return null;
    }
  }
  return null;
}

export default function SkillCreatorCaptureButton({
  enabled,
  source,
}: {
  enabled: boolean;
  source: SkillCreatorCaptureSource;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!enabled) return null;

  async function capture() {
    setBusy(true);
    setError("");
    try {
      const session = await createSkillCreatorSession(
        source.sourceKind === "xpert_chat"
          ? {
              mode: "run",
              source_kind: "xpert_chat",
              source_task_id: source.taskId,
              source_run_id: source.runId,
              source_xpert_id: source.xpertId,
              source_conversation_id: source.conversationId,
              source_message_id: source.messageId,
            }
          : {
              mode: "run",
              source_kind: "workflow_classic",
              source_task_id: source.taskId,
              source_run_id: source.runId,
            },
      );
      navigate(`/skills/create/${encodeURIComponent(session.session_id)}`);
    } catch (caught) {
      setError(
        caught instanceof SkillCreatorApiError
          ? caught.message
          : "这次运行暂时无法沉淀，请刷新后重试。",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex flex-col items-start gap-1">
      <button
        className="inline-flex min-h-8 items-center rounded-full border border-emerald-300/25 bg-emerald-300/10 px-3 text-[11px] font-semibold text-emerald-100 transition hover:border-emerald-300/45 hover:bg-emerald-300/15 disabled:cursor-wait disabled:opacity-60"
        disabled={busy}
        onClick={() => void capture()}
        type="button"
      >
        {busy ? "正在创建会话..." : "沉淀为 Skill"}
      </button>
      {error ? (
        <span className="max-w-72 text-[11px] leading-5 text-rose-200" role="alert">
          {error}
        </span>
      ) : null}
    </span>
  );
}
