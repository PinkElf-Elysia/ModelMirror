import {
  AlertTriangle,
  BookOpenCheck,
  CheckCircle2,
  FileSearch,
  RefreshCw,
} from "lucide-react";

export type SkillApplicationStatus =
  | "required"
  | "available"
  | "reading"
  | "staged"
  | "resource_accessed"
  | "repair_requested"
  | "verified"
  | "failed";

export interface SkillRuntimeStatusEventLike {
  event: string;
  status?: string;
  skill_id?: string;
  activated_skill_id?: string;
  required_skill_ids?: string[];
  available_skill_ids?: string[];
  skill_version_id?: string;
  resource_count?: number;
  resource_paths?: string[];
  error_code?: string;
}

export interface SkillApplicationState {
  skillId: string;
  requirement: "required" | "available";
  versionId: string;
  read: boolean;
  stagedResourceCount: number;
  accessedResourceCount: number;
  resourcePaths: string[];
  repairRequested: boolean;
  verified: boolean;
  failed: boolean;
  errorCode: string;
}

export function requiredSkillIdsFromWorkflowNodes(
  nodes: Array<{
    data?: {
      kind?: unknown;
      runtimeMiddlewareId?: unknown;
      runtimeMiddlewareConfig?: unknown;
    };
  }>,
): string[] {
  const ids = new Set<string>();
  nodes.forEach((node) => {
    const data = node.data;
    if (
      !data
      || data.kind !== "runtime_middleware"
      || data.runtimeMiddlewareId !== "skills_runtime"
    ) return;
    const config = data.runtimeMiddlewareConfig;
    if (!config || typeof config !== "object" || Array.isArray(config)) return;
    String((config as Record<string, unknown>).skill_ids || "")
      .split(/[,\n]+/)
      .map((value) => value.trim())
      .filter(Boolean)
      .forEach((skillId) => ids.add(skillId));
  });
  return [...ids].sort();
}

function cleanIds(values: unknown): string[] {
  return Array.isArray(values)
    ? values
        .filter((value): value is string => typeof value === "string")
        .map((value) => value.trim())
        .filter(Boolean)
    : [];
}

function createState(
  skillId: string,
  requirement: SkillApplicationState["requirement"] = "required",
): SkillApplicationState {
  return {
    skillId,
    requirement,
    versionId: "",
    read: false,
    stagedResourceCount: 0,
    accessedResourceCount: 0,
    resourcePaths: [],
    repairRequested: false,
    verified: false,
    failed: false,
    errorCode: "",
  };
}

export function buildSkillApplicationStates(
  events: SkillRuntimeStatusEventLike[],
  expectedRequiredSkillIds: string[] = [],
): SkillApplicationState[] {
  const states = new Map<string, SkillApplicationState>();
  const ensure = (
    skillId: string,
    requirement: SkillApplicationState["requirement"] = "required",
  ) => {
    const clean = skillId.trim();
    if (!clean) return null;
    const existing = states.get(clean);
    if (existing) {
      if (requirement === "required") existing.requirement = "required";
      return existing;
    }
    const next = createState(clean, requirement);
    states.set(clean, next);
    return next;
  };

  expectedRequiredSkillIds.forEach((skillId) => ensure(skillId, "required"));

  events.forEach((event) => {
    if (event.event !== "skill_runtime_status") return;
    const status = event.status as SkillApplicationStatus | undefined;
    const directSkillId = String(
      event.skill_id || event.activated_skill_id || "",
    ).trim();
    const requiredIds = cleanIds(event.required_skill_ids);
    const availableIds = cleanIds(event.available_skill_ids);
    if (status === "required") {
      ensure(directSkillId, "required");
      requiredIds.forEach((skillId) => ensure(skillId, "required"));
    }
    if (status === "available") {
      ensure(directSkillId, "available");
      availableIds.forEach((skillId) => ensure(skillId, "available"));
    }
    if (["enable", "install", "upgrade"].includes(String(status))) {
      ensure(directSkillId, "required");
    }

    let targets = directSkillId ? [directSkillId] : requiredIds;
    if (
      targets.length === 0
      && ["repair_requested", "verified", "failed"].includes(String(status))
    ) {
      targets = [...states.values()]
        .filter((state) => state.requirement === "required")
        .map((state) => state.skillId);
    }
    targets.forEach((skillId) => {
      const currentRequirement = states.get(skillId)?.requirement;
      const state = ensure(
        skillId,
        status === "available"
          ? "available"
          : currentRequirement ?? "required",
      );
      if (!state) return;
      if (event.skill_version_id) state.versionId = event.skill_version_id;
      if (status === "reading") state.read = true;
      if (status === "staged") {
        state.stagedResourceCount = Math.max(
          state.stagedResourceCount,
          Number(event.resource_count) || 0,
        );
      }
      if (status === "resource_accessed") {
        state.accessedResourceCount += Math.max(0, Number(event.resource_count) || 0);
      }
      if (status === "repair_requested") state.repairRequested = true;
      if (status === "verified") state.verified = true;
      if (status === "failed") {
        state.failed = true;
        state.errorCode = String(event.error_code || "skill_application_failed");
      }
      cleanIds(event.resource_paths).forEach((path) => {
        if (!state.resourcePaths.includes(path) && state.resourcePaths.length < 12) {
          state.resourcePaths.push(path);
        }
      });
    });
  });

  return [...states.values()].sort((left, right) => {
    if (left.requirement !== right.requirement) {
      return left.requirement === "required" ? -1 : 1;
    }
    return left.skillId.localeCompare(right.skillId);
  });
}

function statePresentation(state: SkillApplicationState) {
  if (state.failed) {
    return {
      label: "未通过",
      className: "border-rose-300/25 bg-rose-300/10 text-rose-100",
      Icon: AlertTriangle,
    };
  }
  if (state.verified) {
    return {
      label: "已核验",
      className: "border-emerald-300/25 bg-emerald-300/10 text-emerald-100",
      Icon: CheckCircle2,
    };
  }
  if (state.repairRequested) {
    return {
      label: "正在纠偏",
      className: "border-amber-300/25 bg-amber-300/10 text-amber-100",
      Icon: RefreshCw,
    };
  }
  if (state.read) {
    return {
      label: "已读取",
      className: "border-cyan-300/25 bg-cyan-300/10 text-cyan-100",
      Icon: BookOpenCheck,
    };
  }
  return {
    label: state.requirement === "required" ? "等待读取" : "可选",
    className: "border-white/10 bg-white/[0.04] text-slate-300",
    Icon: FileSearch,
  };
}

export default function SkillApplicationCard({
  className = "",
  events,
  expectedRequiredSkillIds = [],
}: {
  className?: string;
  events: SkillRuntimeStatusEventLike[];
  expectedRequiredSkillIds?: string[];
}) {
  const states = buildSkillApplicationStates(events, expectedRequiredSkillIds);
  if (states.length === 0) return null;
  const verifiedCount = states.filter((state) => state.verified).length;
  const requiredCount = states.filter((state) => state.requirement === "required").length;

  return (
    <section
      aria-label="Skill 应用状态"
      aria-live="polite"
      className={`rounded-lg border border-cyan-300/20 bg-cyan-300/[0.055] ${className}`}
    >
      <div className="flex flex-col gap-1 border-b border-cyan-200/10 px-3 py-2.5 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h3 className="text-xs font-semibold text-cyan-50">Skill 应用</h3>
          <p className="mt-0.5 text-[11px] leading-4 text-slate-400">
            {requiredCount > 0
              ? `${verifiedCount}/${requiredCount} 个必用 Skill 已通过应用门`
              : "当前仅有可选 Skill"}
          </p>
        </div>
        <span className="text-[10px] text-cyan-100/70">读取证明交付，不代表质量认证</span>
      </div>
      <div className="divide-y divide-white/10">
        {states.map((state) => {
          const presentation = statePresentation(state);
          const Icon = presentation.Icon;
          return (
            <div className="px-3 py-2.5" key={state.skillId}>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="break-all text-xs font-semibold text-slate-100">
                      {state.skillId}
                    </span>
                    <span className="rounded-full border border-white/10 px-2 py-0.5 text-[10px] text-slate-400">
                      {state.requirement === "required" ? "必须应用" : "插件提供，可选"}
                    </span>
                  </div>
                  {state.versionId ? (
                    <p className="mt-1 truncate font-mono text-[10px] text-slate-500">
                      {state.versionId}
                    </p>
                  ) : null}
                </div>
                <span
                  className={`inline-flex w-fit shrink-0 items-center gap-1.5 rounded-full border px-2 py-1 text-[10px] font-semibold ${presentation.className}`}
                >
                  <Icon aria-hidden="true" size={12} />
                  {presentation.label}
                </span>
              </div>
              {state.stagedResourceCount > 0 || state.accessedResourceCount > 0 ? (
                <p className="mt-2 text-[11px] leading-4 text-slate-400">
                  已暂存 {state.stagedResourceCount} 个资源，实际读取 {state.accessedResourceCount} 个
                  {state.resourcePaths.length > 0
                    ? `：${state.resourcePaths.slice(0, 4).join("、")}${state.resourcePaths.length > 4 ? " 等" : ""}`
                    : ""}
                </p>
              ) : null}
              {state.failed ? (
                <p className="mt-2 break-all text-[11px] text-rose-100/90">
                  {state.errorCode}
                </p>
              ) : null}
            </div>
          );
        })}
      </div>
    </section>
  );
}
