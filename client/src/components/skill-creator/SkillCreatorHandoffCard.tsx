import { Link } from "react-router-dom";
import type { WorkflowRunEvent } from "../../types/workflow";
import SkillCreatorCaptureButton, {
  type SkillCreatorCaptureSource,
} from "./SkillCreatorCaptureButton";

export interface SkillCreatorHandoffEvent extends WorkflowRunEvent {
  event: "skill_creator_handoff";
  status: "ready" | "failed";
  session_id?: string;
  error_code?: string;
}

export function latestSkillCreatorHandoff(
  events: WorkflowRunEvent[],
): SkillCreatorHandoffEvent | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (
      event.event === "skill_creator_handoff"
      && (event.status === "ready" || event.status === "failed")
    ) {
      return event as SkillCreatorHandoffEvent;
    }
  }
  return null;
}

export function skillCreatorHandoffFailureCopy(errorCode?: string) {
  if (errorCode === "skill_creator_handoff_unavailable") {
    return "Creator 当前未启用或暂不可用。启用后可从这次可信运行重新创建会话。";
  }
  if (errorCode === "skill_creator_handoff_conflict") {
    return "这次运行已存在冲突的 Creator 来源记录，需要核对来源后再重试。";
  }
  return "工作流结果已保留，但 Creator 会话没有创建成功。你可以安全重试。";
}

export default function SkillCreatorHandoffCard({
  event,
  captureEnabled,
  captureSource,
}: {
  event: SkillCreatorHandoffEvent;
  captureEnabled: boolean;
  captureSource: SkillCreatorCaptureSource | null;
}) {
  const sessionId = event.session_id?.trim() ?? "";
  const ready = event.status === "ready" && Boolean(sessionId);

  if (ready) {
    return (
      <section
        aria-labelledby="skill-creator-handoff-title"
        className="rounded-lg border border-emerald-300/25 bg-emerald-300/[0.07] p-3"
      >
        <div className="flex flex-col gap-3">
          <div className="min-w-0">
            <p
              className="text-xs font-semibold text-emerald-100"
              id="skill-creator-handoff-title"
            >
              Creator 会话已准备好
            </p>
            <p className="mt-1 text-[11px] leading-5 text-slate-400">
              原始需求已安全转交。工作流分析会作为待确认素材显示，你检查后再生成方案；这里不会直接创建或安装 Skill。
            </p>
          </div>
          <Link
            className="inline-flex min-h-10 w-full items-center justify-center rounded-full border border-emerald-300/30 bg-emerald-300/10 px-4 text-xs font-semibold text-emerald-50 transition hover:bg-emerald-300/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-200/50"
            to={`/skills/create/${encodeURIComponent(sessionId)}`}
          >
            前往 Creator 检查分析
          </Link>
        </div>
      </section>
    );
  }

  const errorCode = event.error_code?.trim() || "skill_creator_handoff_failed";
  return (
    <section
      aria-labelledby="skill-creator-handoff-failed-title"
      className="rounded-lg border border-amber-300/30 bg-amber-300/[0.08] p-3"
    >
      <div className="flex flex-col gap-3">
        <div className="min-w-0">
          <p
            className="text-xs font-semibold text-amber-50"
            id="skill-creator-handoff-failed-title"
          >
            Creator 交接未完成
          </p>
          <p className="mt-1 text-[11px] leading-5 text-amber-100/80">
            {skillCreatorHandoffFailureCopy(errorCode)}
          </p>
          <p className="mt-1 break-all font-mono text-[10px] text-amber-200/60">
            错误码：{errorCode}
          </p>
        </div>
        {captureEnabled && captureSource ? (
          <SkillCreatorCaptureButton
            busyLabel="正在重试..."
            enabled
            label="重试创建 Creator 会话"
            source={captureSource}
          />
        ) : null}
      </div>
    </section>
  );
}
