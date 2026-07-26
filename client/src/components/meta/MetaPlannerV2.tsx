import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { models } from "../../data/models";
import { type WorkflowDefinition } from "../../types/workflow";
import { type XpertDraft, type XpertSummary } from "../../types/xpert";
import { listXperts, toXpertDraftWorkflow } from "../../utils/xpertApi";
import WorkflowEditor from "../workflow/WorkflowEditor";

type ScopeKey =
  | "allowed_node_kinds"
  | "external_xpert_ids"
  | "knowledge_base_ids"
  | "toolset_ids"
  | "plugin_ids"
  | "prompt_profile_ids"
  | "middleware_ids";

interface CapabilityItem {
  id?: string;
  kind?: string;
  title?: string;
  name?: string;
  description?: string;
  status?: string;
  published_version?: number | null;
  high_risk?: boolean;
  security_category?: string;
}

interface MetaPlannerScope extends Record<ScopeKey, string[]> {
  allowed_node_kinds: string[];
  external_xpert_ids: string[];
  knowledge_base_ids: string[];
  toolset_ids: string[];
  plugin_ids: string[];
  prompt_profile_ids: string[];
  middleware_ids: string[];
}

interface CapabilitySnapshot {
  version: string;
  snapshot_hash: string;
  generated_at: number;
  nodes: CapabilityItem[];
  middleware: CapabilityItem[];
  external_xperts: CapabilityItem[];
  knowledge_bases: CapabilityItem[];
  toolsets: CapabilityItem[];
  plugins: CapabilityItem[];
  prompt_profiles: CapabilityItem[];
  default_scope: MetaPlannerScope;
}

interface PlannerTask {
  task_id: string;
  title: string;
  objective: string;
  depends_on: string[];
  input_contract: string[];
  output_contract: string;
}

interface PlannerPlan {
  summary: string;
  assumptions: string[];
  tasks: PlannerTask[];
}

interface CandidateXpert {
  name: string;
  slug?: string;
  description: string;
  tags: string[];
  starters: string[];
  draft: XpertDraft;
  status?: string;
}

interface ValidationIssue {
  code?: string;
  message: string;
  severity?: "error" | "warning";
  stage?: string;
}

interface MetaPlannerResponse {
  proposal_id: string;
  proposal_revision: number;
  mode: "create" | "update";
  target_xpert_id: string | null;
  base_revision: number | null;
  plan: PlannerPlan;
  candidate: CandidateXpert;
  validation: Record<string, unknown>;
  warnings: string[];
  repair_used: boolean;
  capability_snapshot_version: string;
  capability_snapshot_hash: string;
}

interface AuthoringProposal {
  proposal_id: string;
  revision: number;
  status: string;
  kind: "xpert_create" | "xpert_update";
  title: string;
  target_id: string | null;
  base_revision: number | null;
  payload: Record<string, unknown>;
  validation: Record<string, unknown>;
  applied_resource_id: string | null;
  updated_at: number;
}

const emptyScope = (): MetaPlannerScope => ({
  allowed_node_kinds: [],
  external_xpert_ids: [],
  knowledge_base_ids: [],
  toolset_ids: [],
  plugin_ids: [],
  prompt_profile_ids: [],
  middleware_ids: [],
});

function readError(payload: unknown, fallback: string) {
  if (typeof payload === "object" && payload !== null) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    const error = (payload as { error?: unknown }).error;
    if (typeof error === "string") return error;
  }
  return fallback;
}

function candidateFromProposal(proposal: AuthoringProposal): CandidateXpert {
  const source =
    proposal.kind === "xpert_update"
      ? (proposal.payload.patch as Record<string, unknown> | undefined)
      : proposal.payload;
  if (!source || typeof source !== "object" || !source.draft) {
    throw new Error("候选提案缺少可编辑的 Xpert 草稿。");
  }
  return source as unknown as CandidateXpert;
}

function reportFromProposal(proposal: AuthoringProposal) {
  const report = proposal.payload.meta_planner_report;
  return typeof report === "object" && report !== null
    ? (report as Record<string, unknown>)
    : {};
}

function toWorkflowDefinition(candidate: CandidateXpert): WorkflowDefinition {
  const workflow = candidate.draft.workflow;
  return {
    id: workflow.id,
    title: workflow.title,
    nodes: workflow.nodes.map((node) => ({
      id: node.id,
      type: "workflowNode",
      position: node.position ?? { x: 0, y: 0 },
      data: node.data,
    })),
    edges: workflow.edges,
    updatedAt: new Date().toISOString(),
  };
}

function validationIssues(validation: Record<string, unknown>) {
  const stages = validation.stages;
  if (!Array.isArray(stages)) return [] as ValidationIssue[];
  return stages.flatMap((stage) => {
    if (typeof stage !== "object" || stage === null) return [];
    const stageName = String(
      (stage as { id?: unknown; stage?: unknown }).id ??
        (stage as { stage?: unknown }).stage ??
        "",
    );
    const issues = (stage as { issues?: unknown }).issues;
    if (!Array.isArray(issues)) return [];
    return issues.map((issue) => {
      if (typeof issue === "string") return { message: issue, stage: stageName };
      if (typeof issue === "object" && issue !== null) {
        return {
          ...(issue as ValidationIssue),
          message: String((issue as { message?: unknown }).message ?? "校验失败"),
          stage: stageName,
        };
      }
      return { message: String(issue), stage: stageName };
    });
  });
}

const capabilityGroups: Array<{
  key: ScopeKey;
  source: keyof CapabilitySnapshot;
  title: string;
  hint: string;
}> = [
  {
    key: "allowed_node_kinds",
    source: "nodes",
    title: "节点",
    hint: "允许规划器生成的真实 Workflow 节点",
  },
  {
    key: "external_xpert_ids",
    source: "external_xperts",
    title: "外部 Xpert",
    hint: "固定已发布版本的同步协作者",
  },
  {
    key: "knowledge_base_ids",
    source: "knowledge_bases",
    title: "知识库",
    hint: "遵守运行时活动索引语义",
  },
  {
    key: "toolset_ids",
    source: "toolsets",
    title: "Toolset",
    hint: "固定已发布工具集版本",
  },
  {
    key: "plugin_ids",
    source: "plugins",
    title: "Plugin",
    hint: "固定声明式 Plugin 版本",
  },
  {
    key: "prompt_profile_ids",
    source: "prompt_profiles",
    title: "Prompt Profile",
    hint: "固定已发布命令版本",
  },
  {
    key: "middleware_ids",
    source: "middleware",
    title: "中间件",
    hint: "高风险能力默认关闭，需显式授权",
  },
];

export default function MetaPlannerV2() {
  const navigate = useNavigate();
  const plannerModels = useMemo(() => models, []);
  const [capabilities, setCapabilities] = useState<CapabilitySnapshot | null>(null);
  const [editableXperts, setEditableXperts] = useState<XpertSummary[]>([]);
  const [scope, setScope] = useState<MetaPlannerScope>(emptyScope);
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState<"create" | "update">("create");
  const [targetXpertId, setTargetXpertId] = useState("");
  const [plannerModelId, setPlannerModelId] = useState(
    plannerModels[0]?.id ?? models[0]?.id ?? "",
  );
  const [agentModelId, setAgentModelId] = useState(
    plannerModels[0]?.id ?? models[0]?.id ?? "",
  );
  const [temperature, setTemperature] = useState(0.2);
  const [maxAgents, setMaxAgents] = useState(5);
  const [proposal, setProposal] = useState<AuthoringProposal | null>(null);
  const [candidate, setCandidate] = useState<CandidateXpert | null>(null);
  const [plan, setPlan] = useState<PlannerPlan | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [repairUsed, setRepairUsed] = useState(false);
  const [snapshotHash, setSnapshotHash] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setIsLoading(true);
      try {
        const [capabilityResponse, xpertResponse, proposalResponse] = await Promise.all([
          fetch("/api/meta-agent/capabilities"),
          listXperts({ status: "all", limit: 200 }),
          fetch(
            "/api/runtime/authoring-proposals?status=pending&source_type=meta_planner&limit=50",
          ),
        ]);
        if (!capabilityResponse.ok) {
          throw new Error("无法加载 Meta Planner 能力快照。");
        }
        if (!proposalResponse.ok) {
          throw new Error("无法加载 Meta Planner 候选。");
        }
        const capabilityPayload =
          (await capabilityResponse.json()) as CapabilitySnapshot;
        const proposalPayload = (await proposalResponse.json()) as {
          items?: Array<{ proposal_id: string }>;
        };
        if (cancelled) return;
        setCapabilities(capabilityPayload);
        setScope(capabilityPayload.default_scope);
        setEditableXperts(xpertResponse.items);
        const latest = proposalPayload.items?.[0];
        if (latest) {
          await loadProposal(latest.proposal_id, false);
        }
      } catch (loadError) {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : "加载失败。");
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  async function loadProposal(proposalId: string, showNotice = true) {
    const response = await fetch(`/api/runtime/authoring-proposals/${proposalId}`);
    const payload = (await response.json().catch(() => null)) as
      | AuthoringProposal
      | { detail?: string }
      | null;
    if (!response.ok || !payload || !("proposal_id" in payload)) {
      throw new Error(readError(payload, "无法恢复候选提案。"));
    }
    const restored = payload as AuthoringProposal;
    const report = reportFromProposal(restored);
    setProposal(restored);
    setCandidate(candidateFromProposal(restored));
    setMode(restored.kind === "xpert_update" ? "update" : "create");
    setTargetXpertId(restored.target_id ?? "");
    setPlan((report.plan as PlannerPlan | undefined) ?? null);
    setWarnings(Array.isArray(report.warnings) ? (report.warnings as string[]) : []);
    setRepairUsed(Boolean(report.repair_used));
    const snapshot = report.capability_snapshot;
    setSnapshotHash(
      String(
        (typeof snapshot === "object" &&
        snapshot !== null &&
        "hash" in snapshot
          ? (snapshot as { hash?: unknown }).hash
          : undefined) ??
          report.capability_snapshot_hash ??
          "",
      ),
    );
    if (showNotice) setNotice("已恢复持久化候选。");
  }

  function toggleScope(key: ScopeKey, value: string) {
    setScope((current) => {
      const values = new Set(current[key]);
      if (values.has(value)) values.delete(value);
      else values.add(value);
      return { ...current, [key]: Array.from(values) };
    });
  }

  async function generateCandidate() {
    const cleanGoal = goal.trim();
    if (cleanGoal.length < 10) {
      setError("请提供至少 10 个字符的明确目标。");
      return;
    }
    if (mode === "update" && !targetXpertId) {
      setError("更新模式必须选择目标 Xpert。");
      return;
    }
    setIsGenerating(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/meta-agent/generate-xpert-candidate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          goal: cleanGoal,
          mode,
          target_xpert_id: mode === "update" ? targetXpertId : null,
          planner_model_id: plannerModelId,
          default_agent_model_id: agentModelId,
          temperature,
          max_agents: maxAgents,
          scope,
        }),
      });
      const payload = (await response.json().catch(() => null)) as
        | MetaPlannerResponse
        | { detail?: string }
        | null;
      if (!response.ok || !payload || !("proposal_id" in payload)) {
        throw new Error(readError(payload, "Meta Planner 生成失败。"));
      }
      const generated = payload as MetaPlannerResponse;
      setCandidate(generated.candidate);
      setPlan(generated.plan);
      setWarnings(generated.warnings);
      setRepairUsed(generated.repair_used);
      setSnapshotHash(generated.capability_snapshot_hash);
      await loadProposal(generated.proposal_id, false);
      setNotice(
        generated.repair_used
          ? "候选已生成并完成一次定向修复。"
          : "候选已生成并写入审批提案。",
      );
    } catch (generateError) {
      setError(
        generateError instanceof Error ? generateError.message : "Meta Planner 生成失败。",
      );
    } finally {
      setIsGenerating(false);
    }
  }

  function proposalPayload(nextCandidate: CandidateXpert) {
    if (!proposal) return {};
    const report = reportFromProposal(proposal);
    if (proposal.kind === "xpert_update") {
      return {
        ...proposal.payload,
        xpert_id: proposal.target_id,
        patch: nextCandidate,
        meta_planner_report: report,
      };
    }
    return {
      ...nextCandidate,
      meta_planner_report: report,
    };
  }

  async function saveCandidate(definition?: WorkflowDefinition) {
    if (!proposal || !candidate) return;
    const nextCandidate: CandidateXpert = definition
      ? {
          ...candidate,
          draft: {
            ...candidate.draft,
            workflow: toXpertDraftWorkflow(definition),
          },
        }
      : candidate;
    setIsSaving(true);
    setError("");
    try {
      const response = await fetch(
        `/api/runtime/authoring-proposals/${proposal.proposal_id}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            revision: proposal.revision,
            title: `Meta Planner: ${nextCandidate.name}`,
            payload: proposalPayload(nextCandidate),
            base_revision: proposal.base_revision,
          }),
        },
      );
      const payload = (await response.json().catch(() => null)) as
        | AuthoringProposal
        | { detail?: string }
        | null;
      if (!response.ok || !payload || !("proposal_id" in payload)) {
        throw new Error(readError(payload, "保存候选失败。"));
      }
      setProposal(payload as AuthoringProposal);
      setCandidate(candidateFromProposal(payload as AuthoringProposal));
      setNotice("候选已保存，Proposal revision 已更新。");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "保存候选失败。");
    } finally {
      setIsSaving(false);
    }
  }

  async function proposalAction(action: "validate" | "approve") {
    if (!proposal) return;
    setIsSaving(true);
    setError("");
    try {
      const response = await fetch(
        `/api/runtime/authoring-proposals/${proposal.proposal_id}/${action}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            revision: proposal.revision,
            operator: "meta-planner-operator",
          }),
        },
      );
      const payload = (await response.json().catch(() => null)) as
        | AuthoringProposal
        | { detail?: string }
        | null;
      if (!response.ok || !payload || !("proposal_id" in payload)) {
        throw new Error(readError(payload, action === "approve" ? "批准失败。" : "校验失败。"));
      }
      const next = payload as AuthoringProposal;
      setProposal(next);
      if (action === "approve") {
        if (!next.applied_resource_id) {
          throw new Error("提案已批准，但未返回目标 Xpert ID。");
        }
        navigate(`/agents/studio/${next.applied_resource_id}`);
        return;
      }
      setNotice("候选已通过当前资源与发布预检。");
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "操作失败。");
    } finally {
      setIsSaving(false);
    }
  }

  const issues = proposal ? validationIssues(proposal.validation) : [];
  const workflow = candidate ? toWorkflowDefinition(candidate) : null;

  return (
    <section className="mb-6 overflow-hidden rounded-lg border border-cyan-300/20 bg-slate-950/75 shadow-prism">
      <div className="border-b border-white/10 bg-white/[0.025] px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-semibold text-white">Meta Planner V2</h2>
              <span className="rounded-full border border-cyan-300/30 bg-cyan-300/10 px-2 py-0.5 text-[11px] font-semibold text-cyan-100">
                EvoAgentX adapted
              </span>
              {proposal ? (
                <span className="rounded-full border border-white/10 px-2 py-0.5 text-[11px] text-slate-300">
                  Proposal r{proposal.revision}
                </span>
              ) : null}
            </div>
            <p className="mt-1 max-w-4xl text-sm leading-6 text-slate-400">
              任务规划、能力编译与一次定向修复。结果只进入审批提案，批准后也仅写入
              Xpert 草稿。
            </p>
          </div>
          <div className="text-right text-xs text-slate-500">
            <p>{isLoading ? "正在加载能力快照..." : capabilities?.version}</p>
            {snapshotHash ? <p className="mt-1 font-mono">{snapshotHash.slice(0, 12)}</p> : null}
          </div>
        </div>
      </div>

      <div className="grid gap-4 p-4 2xl:grid-cols-[340px_minmax(0,1fr)]">
        <div className="space-y-4">
          <div className="rounded-lg border border-white/10 bg-white/[0.035] p-4">
            <div className="grid grid-cols-2 gap-2">
              {(["create", "update"] as const).map((item) => (
                <button
                  className={`rounded-md border px-3 py-2 text-sm font-semibold transition ${
                    mode === item
                      ? "border-cyan-300/45 bg-cyan-300/15 text-cyan-100"
                      : "border-white/10 bg-white/[0.025] text-slate-400 hover:text-white"
                  }`}
                  key={item}
                  onClick={() => setMode(item)}
                  type="button"
                >
                  {item === "create" ? "创建 Xpert" : "更新 Xpert"}
                </button>
              ))}
            </div>

            {mode === "update" ? (
              <label className="mt-3 block">
                <span className="text-xs font-semibold text-slate-300">目标 Xpert</span>
                <select
                  className="mt-2 h-10 w-full rounded-md border border-white/10 bg-slate-950 px-3 text-sm text-white"
                  onChange={(event) => setTargetXpertId(event.target.value)}
                  value={targetXpertId}
                >
                  <option value="">选择当前草稿</option>
                  {editableXperts.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name} · r{item.draft_revision}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}

            <label className="mt-3 block">
              <span className="text-xs font-semibold text-slate-300">目标</span>
              <textarea
                className="mt-2 min-h-32 w-full resize-y rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-sm leading-6 text-white outline-none focus:border-cyan-300/45"
                onChange={(event) => setGoal(event.target.value)}
                placeholder="例如：构建一个研究、事实核查与审稿协作的 Xpert"
                value={goal}
              />
            </label>

            <label className="mt-3 block">
              <span className="text-xs font-semibold text-slate-300">Planner 模型</span>
              <select
                className="mt-2 h-10 w-full rounded-md border border-white/10 bg-slate-950 px-3 text-sm text-white"
                onChange={(event) => setPlannerModelId(event.target.value)}
                value={plannerModelId}
              >
                {plannerModels.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="mt-3 block">
              <span className="text-xs font-semibold text-slate-300">默认 Agent 模型</span>
              <select
                className="mt-2 h-10 w-full rounded-md border border-white/10 bg-slate-950 px-3 text-sm text-white"
                onChange={(event) => setAgentModelId(event.target.value)}
                value={agentModelId}
              >
                {models.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>

            <div className="mt-3 grid grid-cols-2 gap-3">
              <label>
                <span className="text-xs font-semibold text-slate-300">温度</span>
                <input
                  className="mt-2 h-10 w-full rounded-md border border-white/10 bg-white/[0.04] px-3 text-sm text-white"
                  max={1}
                  min={0}
                  onChange={(event) => setTemperature(Number(event.target.value))}
                  step={0.1}
                  type="number"
                  value={temperature}
                />
              </label>
              <label>
                <span className="text-xs font-semibold text-slate-300">Agent 上限</span>
                <input
                  className="mt-2 h-10 w-full rounded-md border border-white/10 bg-white/[0.04] px-3 text-sm text-white"
                  max={8}
                  min={1}
                  onChange={(event) => setMaxAgents(Number(event.target.value))}
                  type="number"
                  value={maxAgents}
                />
              </label>
            </div>

            <button
              className="mt-4 w-full rounded-md bg-cyan-300 px-4 py-2.5 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
              disabled={isGenerating || isLoading || !goal.trim()}
              onClick={() => void generateCandidate()}
              type="button"
            >
              {isGenerating ? "三阶段生成中..." : "生成候选 Xpert"}
            </button>
          </div>

          <details className="rounded-lg border border-white/10 bg-white/[0.03] p-4" open>
            <summary className="cursor-pointer text-sm font-semibold text-white">
              能力授权范围
            </summary>
            <div className="mt-3 max-h-[520px] space-y-4 overflow-y-auto pr-1">
              {capabilities
                ? capabilityGroups.map((group) => {
                    const items = capabilities[group.source] as CapabilityItem[];
                    return (
                      <div key={group.key}>
                        <div className="flex items-end justify-between gap-2">
                          <p className="text-xs font-semibold text-slate-200">{group.title}</p>
                          <p className="text-[10px] text-slate-500">{group.hint}</p>
                        </div>
                        <div className="mt-2 space-y-1.5">
                          {items.map((item) => {
                            const value = String(item.kind ?? item.id ?? "");
                            const checked = scope[group.key].includes(value);
                            return (
                              <label
                                className="flex cursor-pointer items-start gap-2 rounded-md border border-white/5 bg-white/[0.025] px-2.5 py-2"
                                key={`${group.key}-${value}`}
                              >
                                <input
                                  checked={checked}
                                  className="mt-0.5 accent-cyan-300"
                                  onChange={() => toggleScope(group.key, value)}
                                  type="checkbox"
                                />
                                <span className="min-w-0">
                                  <span className="flex flex-wrap items-center gap-1.5 text-xs font-medium text-slate-200">
                                    {item.title ?? item.name ?? value}
                                    {item.high_risk ? (
                                      <span className="rounded border border-amber-300/30 bg-amber-300/10 px-1 text-[9px] text-amber-100">
                                        高风险
                                      </span>
                                    ) : null}
                                  </span>
                                  {item.description ? (
                                    <span className="mt-0.5 line-clamp-2 block text-[10px] leading-4 text-slate-500">
                                      {item.description}
                                    </span>
                                  ) : null}
                                </span>
                              </label>
                            );
                          })}
                          {items.length === 0 ? (
                            <p className="text-[11px] text-slate-500">当前没有可授权资源。</p>
                          ) : null}
                        </div>
                      </div>
                    );
                  })
                : null}
            </div>
          </details>
        </div>

        <div className="min-w-0 space-y-4">
          {candidate && workflow && proposal ? (
            <>
              <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
                <div className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <label>
                      <span className="text-xs font-semibold text-slate-300">名称</span>
                      <input
                        className="mt-2 h-10 w-full rounded-md border border-white/10 bg-white/[0.04] px-3 text-sm text-white"
                        onChange={(event) =>
                          setCandidate((current) =>
                            current ? { ...current, name: event.target.value } : current,
                          )
                        }
                        value={candidate.name}
                      />
                    </label>
                    <label>
                      <span className="text-xs font-semibold text-slate-300">标签</span>
                      <input
                        className="mt-2 h-10 w-full rounded-md border border-white/10 bg-white/[0.04] px-3 text-sm text-white"
                        onChange={(event) =>
                          setCandidate((current) =>
                            current
                              ? {
                                  ...current,
                                  tags: event.target.value
                                    .split(",")
                                    .map((value) => value.trim())
                                    .filter(Boolean),
                                }
                              : current,
                          )
                        }
                        value={candidate.tags.join(", ")}
                      />
                    </label>
                  </div>
                  <label className="mt-3 block">
                    <span className="text-xs font-semibold text-slate-300">描述</span>
                    <textarea
                      className="mt-2 min-h-20 w-full rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white"
                      onChange={(event) =>
                        setCandidate((current) =>
                          current ? { ...current, description: event.target.value } : current,
                        )
                      }
                      value={candidate.description}
                    />
                  </label>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button
                      className="rounded-md border border-violet-300/30 bg-violet-300/10 px-3 py-2 text-xs font-semibold text-violet-100 hover:bg-violet-300/15"
                      onClick={() =>
                        navigate(
                          `/agents/evaluations?proposal_id=${proposal.proposal_id}&proposal_revision=${proposal.revision}`,
                        )
                      }
                      type="button"
                    >
                      评测候选
                    </button>
                    <button
                      className="rounded-md border border-white/10 px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-white/5"
                      disabled={isSaving}
                      onClick={() => void saveCandidate()}
                      type="button"
                    >
                      保存元数据
                    </button>
                    <button
                      className="rounded-md border border-cyan-300/30 bg-cyan-300/10 px-3 py-2 text-xs font-semibold text-cyan-100 hover:bg-cyan-300/15"
                      disabled={isSaving}
                      onClick={() => void proposalAction("validate")}
                      type="button"
                    >
                      重新预检
                    </button>
                    <button
                      className="rounded-md bg-emerald-300 px-3 py-2 text-xs font-semibold text-emerald-950 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
                      disabled={isSaving}
                      onClick={() => void proposalAction("approve")}
                      type="button"
                    >
                      批准并写入草稿
                    </button>
                  </div>
                </div>

                <div className="rounded-lg border border-white/10 bg-white/[0.03] p-4">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-sm font-semibold text-white">计划与门禁</p>
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                        issues.some((item) => item.severity !== "warning")
                          ? "bg-rose-300/10 text-rose-100"
                          : "bg-emerald-300/10 text-emerald-100"
                      }`}
                    >
                      {issues.length ? `${issues.length} issues` : "ready"}
                    </span>
                  </div>
                  <p className="mt-2 text-xs leading-5 text-slate-400">
                    {plan?.summary ?? "候选来自已持久化 Proposal。"}
                  </p>
                  <div className="mt-3 max-h-44 space-y-2 overflow-y-auto">
                    {plan?.tasks.map((task) => (
                      <div
                        className="rounded-md border border-white/5 bg-white/[0.025] px-2.5 py-2"
                        key={task.task_id}
                      >
                        <p className="text-xs font-semibold text-slate-200">{task.title}</p>
                        <p className="mt-1 text-[10px] text-slate-500">
                          {task.depends_on.length
                            ? `依赖 ${task.depends_on.join(", ")}`
                            : "入口任务"}
                        </p>
                      </div>
                    ))}
                  </div>
                  {repairUsed ? (
                    <p className="mt-3 rounded-md border border-amber-300/20 bg-amber-300/10 px-2.5 py-2 text-xs text-amber-100">
                      已使用唯一一次模型修复机会。
                    </p>
                  ) : null}
                  {issues.length ? (
                    <div className="mt-3 max-h-32 space-y-1 overflow-y-auto text-[11px] text-rose-100">
                      {issues.map((item, index) => (
                        <p key={`${item.stage}-${index}`}>
                          {item.stage ? `[${item.stage}] ` : ""}
                          {item.message}
                        </p>
                      ))}
                    </div>
                  ) : null}
                  {warnings.length ? (
                    <div className="mt-3 max-h-24 space-y-1 overflow-y-auto text-[11px] text-amber-100">
                      {warnings.map((item) => (
                        <p key={item}>{item}</p>
                      ))}
                    </div>
                  ) : null}
                </div>
              </div>

              <div className="h-[820px] overflow-hidden rounded-lg border border-white/10 bg-slate-950">
                <WorkflowEditor
                  initialDefinition={workflow}
                  key={`${proposal.proposal_id}-${proposal.revision}`}
                  onSave={saveCandidate}
                  saveLabel="保存候选画布"
                  workflowId={`meta-planner-${proposal.proposal_id}`}
                />
              </div>
            </>
          ) : (
            <div className="flex min-h-[540px] items-center justify-center rounded-lg border border-dashed border-white/10 bg-white/[0.02] p-8 text-center">
              <div>
                <p className="text-base font-semibold text-white">尚未生成候选 Xpert</p>
                <p className="mt-2 max-w-lg text-sm leading-6 text-slate-500">
                  选择能力授权范围后生成。高风险中间件不会默认进入模型可见范围。
                </p>
              </div>
            </div>
          )}

          {notice ? (
            <div className="rounded-lg border border-emerald-300/20 bg-emerald-300/10 px-3 py-2 text-sm text-emerald-100">
              {notice}
            </div>
          ) : null}
          {error ? (
            <div className="rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-sm text-rose-100">
              {error}
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
