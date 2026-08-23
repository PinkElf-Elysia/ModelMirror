import { ShieldCheck } from "lucide-react";

export interface SkillHookStatusEventLike {
  event: string;
  status?: string;
  skill_id?: string;
  hook_id?: string;
  hook_event?: "session_start" | "pre_tool_use" | "post_tool_use" | "session_end";
  hook_mode?: "annotation" | "validation" | "guard";
  tool_name?: string;
  code?: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function skillIds(value: unknown) {
  return [...new Set(String(value ?? "").split(/[,\n]+/).map((item) => item.trim()).filter(Boolean))];
}

export function hookSkillIdsFromWorkflowNodes(nodes: Array<{
  data?: {
    kind?: unknown;
    runtimeMiddlewareId?: unknown;
    runtimeMiddlewareConfig?: unknown;
  };
}>) {
  return [...new Set(nodes.flatMap((node) => {
    const data = node.data;
    if (
      !data
      || data.kind !== "runtime_middleware"
      || data.runtimeMiddlewareId !== "plugin_hooks"
      || !isRecord(data.runtimeMiddlewareConfig)
      || data.runtimeMiddlewareConfig.hook_mode !== "typed_v2"
    ) return [];
    return skillIds(data.runtimeMiddlewareConfig.skill_ids);
  }))];
}

const STATUS_COPY: Record<string, string> = {
  planned: "已计划",
  running: "运行中",
  annotated: "已提示",
  validated: "验证通过",
  denied: "已阻断",
  failed: "失败",
  completed: "完成",
};

function statusClass(status: string) {
  if (["validated", "completed", "annotated"].includes(status)) return "text-emerald-100";
  if (["denied", "failed"].includes(status)) return "text-rose-100";
  return "text-amber-100";
}

export default function SkillHookApplicationCard({
  className = "",
  events,
  expectedSkillIds = [],
}: {
  className?: string;
  events: SkillHookStatusEventLike[];
  expectedSkillIds?: string[];
}) {
  const hookEvents = events.filter((event) => event.event === "skill_hook_status");
  if (!expectedSkillIds.length && !hookEvents.length) return null;

  const latest = new Map<string, SkillHookStatusEventLike>();
  hookEvents.forEach((event, index) => {
    const key = `${event.skill_id || "unknown"}:${event.hook_id || index}`;
    latest.set(key, event);
  });
  const observedSkills = new Set(hookEvents.map((event) => event.skill_id).filter(Boolean));
  const waiting = expectedSkillIds.filter((skillId) => !observedSkills.has(skillId));

  return (
    <section className={`min-h-[92px] rounded-lg border border-amber-300/20 bg-amber-300/[0.045] p-3 ${className}`} aria-labelledby="skill-hook-application-heading">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-xs font-semibold text-amber-50" id="skill-hook-application-heading">
          <ShieldCheck aria-hidden="true" size={15} />Skill Hook 应用
        </h3>
        <span className="text-[11px] text-amber-100/70">Typed V2 · 离线 Sandbox</span>
      </div>
      <div className="mt-3 space-y-2">
        {waiting.map((skillId) => (
          <div className="flex items-center justify-between gap-3 text-xs" key={`waiting-${skillId}`}>
            <span className="min-w-0 truncate font-mono text-slate-300">{skillId}</span>
            <span className="shrink-0 text-slate-500">等待事件边界</span>
          </div>
        ))}
        {[...latest.values()].map((event, index) => (
          <div className="border-t border-white/[0.06] pt-2 text-xs first:border-t-0 first:pt-0" key={`${event.skill_id}-${event.hook_id}-${index}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="min-w-0 truncate font-mono text-slate-200">{event.skill_id || "unknown"} / {event.hook_id || "hook"}</span>
              <span className={`shrink-0 font-semibold ${statusClass(event.status || "planned")}`}>{STATUS_COPY[event.status || "planned"] || event.status}</span>
            </div>
            <p className="mt-1 text-[11px] leading-5 text-slate-400">
              {[event.hook_event, event.hook_mode, event.tool_name, event.code].filter(Boolean).join(" · ")}
              {event.hook_event === "post_tool_use" && ["failed", "denied"].includes(event.status || "") ? " · 工具已执行，副作用未自动回滚" : ""}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}
