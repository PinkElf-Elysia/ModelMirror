import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { DEFAULT_CHAT_MODEL_ID } from "../../data/modelOptions";
import { models } from "../../data/models";
import { type WorkflowDefinition } from "../../types/workflow";
import { type XpertDraft, type XpertSummary } from "../../types/xpert";
import { listXperts, toXpertDraftWorkflow } from "../../utils/xpertApi";
import ProviderRouteReceiptSummary, {
  type ProviderRouteReceipt,
} from "./ProviderRouteReceiptSummary";
import {
  authoringDiffSummary,
  buildMetadataPatch,
  headlessStateMode,
  normalizeGraphPatchEnvelope,
  normalizeGraphPatchPreview,
  normalizeHeadlessProposalState,
  normalizeAuthoringDiagnostics,
  type GraphPatchEnvelopeV1,
  type GraphPatchPreview,
  type HeadlessAuthoringProposalState,
} from "./metaAuthoring";
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
  task_binding?: "required" | "optional" | "forbidden";
  planner?: {
    task_binding?: "required" | "optional" | "forbidden";
  };
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
  ir_version: 2 | 3;
  supported_ir_versions: Array<2 | 3>;
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
  authoring_protocol_version?: string | number;
  authoring_limits?: Record<string, unknown>;
}

const DEFAULT_META_PLANNER_MODEL_ID = models.some(
  (model) => model.id === DEFAULT_CHAT_MODEL_ID,
)
  ? DEFAULT_CHAT_MODEL_ID
  : (models[0]?.id ?? "");

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
  ir_version: 2 | 3;
  graph_ir: Record<string, unknown> | null;
  graph_ir_checksum: string;
  compatibility: {
    source_version: 2 | 3;
    upgraded: boolean;
    lossy: boolean;
    warnings: string[];
  };
  run_id?: string | null;
  provider_route_receipts?: ProviderRouteReceipt | null;
}

interface ControlFlowScenarioSummary {
  id: string;
  outcomes: string[];
  success_sources: string[];
  error_sources: string[];
}

interface ControlFlowReportSummary {
  version: number;
  router_count: number;
  scenario_count: number;
  final_source_count: number;
  scenarios: ControlFlowScenarioSummary[];
}

interface AuthoringProposal {
  proposal_id: string;
  revision: number;
  apply_key: string;
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

type AuthoringAvailability =
  | { mode: "legacy"; reason: string }
  | { mode: "headless"; state: HeadlessAuthoringProposalState }
  | { mode: "unavailable"; reason: string };

interface PendingAuthoringPreview {
  patch: GraphPatchEnvelopeV1;
  preview: GraphPatchPreview;
  source: "canvas" | "metadata";
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
    if (typeof detail === "object" && detail !== null) {
      const message = (detail as { message?: unknown }).message;
      if (typeof message === "string") return message;
    }
    const error = (payload as { error?: unknown }).error;
    if (typeof error === "string") return error;
  }
  return fallback;
}

function readAuthoringError(payload: unknown, fallback: string) {
  const base = readError(payload, fallback);
  if (typeof payload !== "object" || payload === null) return base;
  const detail = (payload as { detail?: unknown }).detail;
  const diagnostics = normalizeAuthoringDiagnostics(
    typeof detail === "object" && detail !== null
      ? (detail as { diagnostics?: unknown }).diagnostics
      : (payload as { diagnostics?: unknown }).diagnostics,
  );
  if (!diagnostics.length) return base;
  return `${base} ${diagnostics.map((item) => item.message).join("；")}`;
}

function controlFlowReportFrom(value: unknown): ControlFlowReportSummary | null {
  if (typeof value !== "object" || value === null) return null;
  const source = value as Record<string, unknown>;
  const graph =
    typeof source.graph_ir === "object" && source.graph_ir !== null
      ? (source.graph_ir as Record<string, unknown>)
      : source;
  const raw = graph.control_flow_report;
  if (typeof raw !== "object" || raw === null) return null;
  const report = raw as Record<string, unknown>;
  const scenarios = Array.isArray(report.scenarios)
    ? report.scenarios.flatMap((item) => {
        if (typeof item !== "object" || item === null) return [];
        const row = item as Record<string, unknown>;
        const strings = (field: string) =>
          Array.isArray(row[field])
            ? row[field].filter((entry): entry is string => typeof entry === "string")
            : [];
        return [{
          id: typeof row.id === "string" ? row.id : "scenario",
          outcomes: strings("outcomes"),
          success_sources: strings("success_sources"),
          error_sources: strings("error_sources"),
        }];
      })
    : [];
  const number = (field: string) =>
    typeof report[field] === "number" ? Number(report[field]) : 0;
  return {
    version: number("version"),
    router_count: number("router_count"),
    scenario_count: number("scenario_count"),
    final_source_count: number("final_source_count"),
    scenarios,
  };
}

export function candidateGenerationOutcome(
  validation: Record<string, unknown>,
  repairUsed: boolean,
) {
  if (validation.valid === false) {
    return {
      error: "候选已保留，但一次定向修复后仍未通过验证，需要人工修复。",
      notice: "",
    };
  }
  return {
    error: "",
    notice: repairUsed
      ? "候选已生成并完成一次定向修复。"
      : "候选已生成并写入审批提案。",
  };
}

function candidateFromProposal(proposal: AuthoringProposal): CandidateXpert {
  const source =
    proposal.kind === "xpert_update"
      ? (proposal.payload.patch as Record<string, unknown> | undefined)
      : proposal.payload;
  if (!source || typeof source !== "object" || !source.draft) {
    throw new Error("候选提案缺少可编辑的智能体草稿。");
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
    title: "外部智能体",
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
    DEFAULT_META_PLANNER_MODEL_ID,
  );
  const [agentModelId, setAgentModelId] = useState(
    DEFAULT_META_PLANNER_MODEL_ID,
  );
  const [temperature, setTemperature] = useState(0.2);
  const [maxAgents, setMaxAgents] = useState(5);
  const [proposal, setProposal] = useState<AuthoringProposal | null>(null);
  const [candidate, setCandidate] = useState<CandidateXpert | null>(null);
  const [authoringAvailability, setAuthoringAvailability] =
    useState<AuthoringAvailability>({
      mode: "legacy",
      reason: "尚未加载候选。",
    });
  const [pendingAuthoringPreview, setPendingAuthoringPreview] =
    useState<PendingAuthoringPreview | null>(null);
  const [plan, setPlan] = useState<PlannerPlan | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [repairUsed, setRepairUsed] = useState(false);
  const [controlFlowReport, setControlFlowReport] =
    useState<ControlFlowReportSummary | null>(null);
  const [snapshotHash, setSnapshotHash] = useState("");
  const [routeReceipt, setRouteReceipt] = useState<ProviderRouteReceipt | null>(null);
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

  async function loadProposal(
    proposalId: string,
    showNotice = true,
    clearRouteReceipt = true,
  ) {
    if (clearRouteReceipt) setRouteReceipt(null);
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
    setPendingAuthoringPreview(null);
    setProposal(restored);
    setCandidate(candidateFromProposal(restored));
    setMode(restored.kind === "xpert_update" ? "update" : "create");
    setTargetXpertId(restored.target_id ?? "");
    setPlan((report.plan as PlannerPlan | undefined) ?? null);
    setWarnings(Array.isArray(report.warnings) ? (report.warnings as string[]) : []);
    setRepairUsed(Boolean(report.repair_used));
    setControlFlowReport(controlFlowReportFrom(report));
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
    await loadAuthoringState(restored);
    if (showNotice) setNotice("已恢复持久化候选。");
  }

  async function loadAuthoringState(restored: AuthoringProposal) {
    const report = reportFromProposal(restored);
    const compatibility =
      typeof report.compatibility === "object" && report.compatibility !== null
        ? (report.compatibility as { lossy?: unknown })
        : null;
    if (compatibility?.lossy === true) {
      setAuthoringAvailability({
        mode: "legacy",
        reason: "该候选存在有损 V2/V3 转换，继续使用兼容保存路径。",
      });
      return;
    }
    try {
      const response = await fetch(
        `/api/meta-agent/authoring/proposals/${restored.proposal_id}`,
      );
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(readError(payload, "无法加载无头编排状态。"));
      }
      const state = normalizeHeadlessProposalState(payload);
      if (!state) throw new Error("无头编排状态响应不完整。候选未降级保存。");
      if (
        state.proposal_id !== restored.proposal_id ||
        state.proposal_revision !== restored.revision
      ) {
        throw new Error("Proposal revision 已变化，请重新加载候选后再编辑。");
      }
      if (headlessStateMode(state) !== "headless") {
        setAuthoringAvailability({
          mode: "unavailable",
          reason: state.compatibility.lossy
            ? "该候选无法无损反编译；类型化编辑已锁定。"
            : "该候选无法满足类型化编排门禁；请处理诊断并重新加载，系统不会降级为整包保存。",
        });
        return;
      }
      setAuthoringAvailability({ mode: "headless", state });
    } catch (stateError) {
      setAuthoringAvailability({
        mode: "unavailable",
        reason:
          stateError instanceof Error ? stateError.message : "无头编排状态加载失败。",
      });
    }
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
      setError("更新模式必须选择目标智能体。");
      return;
    }
    setIsGenerating(true);
    setError("");
    setNotice("");
    setRouteReceipt(null);
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
        | {
            detail?: string;
            error?: string;
            provider_route_receipts?: ProviderRouteReceipt | null;
          }
        | null;
      if (!response.ok || !payload || !("proposal_id" in payload)) {
        setRouteReceipt(payload?.provider_route_receipts ?? null);
        throw new Error(readError(payload, "Meta Planner 生成失败。"));
      }
      const generated = payload as MetaPlannerResponse;
      setCandidate(generated.candidate);
      setPlan(generated.plan);
      setWarnings(generated.warnings);
      setRepairUsed(generated.repair_used);
      setSnapshotHash(generated.capability_snapshot_hash);
      setRouteReceipt(generated.provider_route_receipts ?? null);
      await loadProposal(generated.proposal_id, false, false);
      const outcome = candidateGenerationOutcome(
        generated.validation,
        generated.repair_used,
      );
      setError(outcome.error);
      setNotice(outcome.notice);
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

  async function saveLegacyCandidate(definition?: WorkflowDefinition) {
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
    setNotice("兼容候选已保存，Proposal revision 已更新。");
  }

  async function previewGraphPatch(
    patch: GraphPatchEnvelopeV1,
    source: PendingAuthoringPreview["source"],
  ) {
    if (!proposal) return;
    const response = await fetch(
      `/api/meta-agent/authoring/proposals/${proposal.proposal_id}/patch/preview`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      },
    );
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const prefix = response.status === 409 ? "预览冲突" : "Patch 预览失败";
      throw new Error(`${prefix}：${readAuthoringError(payload, "服务端拒绝了当前变更。")}`);
    }
    const preview = normalizeGraphPatchPreview(payload);
    if (!preview) throw new Error("Patch 预览响应不完整，未执行任何写入。");
    setPendingAuthoringPreview({ patch, preview, source });
    setNotice(
      preview.can_apply
        ? "变更预览已生成；确认前 Proposal 保持不变。"
        : "变更未通过门禁；请查看预览诊断。",
    );
  }

  async function previewEditorDiff(definition: WorkflowDefinition) {
    if (!proposal) return;
    const response = await fetch(
      `/api/meta-agent/authoring/proposals/${proposal.proposal_id}/editor-diff`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          proposal_revision: proposal.revision,
          definition,
        }),
      },
    );
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      const prefix = response.status === 409 ? "编辑冲突" : "画布差异转换失败";
      throw new Error(`${prefix}：${readAuthoringError(payload, "存在无法表达的画布修改。")}`);
    }
    const patch = normalizeGraphPatchEnvelope(payload);
    if (!patch) throw new Error("服务端未返回有效的 Graph Patch；Proposal 未发生变化。");
    if (patch.operations.length === 0) {
      setNotice("画布没有需要应用的语义或布局变更。");
      return;
    }
    await previewGraphPatch(patch, "canvas");
  }

  async function saveCandidate(definition?: WorkflowDefinition) {
    if (!proposal || !candidate) return;
    setIsSaving(true);
    setError("");
    setNotice("");
    try {
      if (authoringAvailability.mode === "headless") {
        if (definition) {
          await previewEditorDiff(definition);
        } else {
          await previewGraphPatch(
            buildMetadataPatch(authoringAvailability.state, {
              name: candidate.name,
              description: candidate.description,
              tags: candidate.tags,
              starters: candidate.starters,
            }),
            "metadata",
          );
        }
      } else if (authoringAvailability.mode === "legacy") {
        await saveLegacyCandidate(definition);
      } else {
        throw new Error(
          `${authoringAvailability.reason} 为避免绕过类型化门禁，V3 候选不会降级为整包保存。`,
        );
      }
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : "保存候选失败。";
      setError(message);
      throw saveError instanceof Error ? saveError : new Error(message);
    } finally {
      setIsSaving(false);
    }
  }

  async function applyPendingPreview() {
    if (!proposal || !pendingAuthoringPreview) return;
    setIsSaving(true);
    setError("");
    try {
      const response = await fetch(
        `/api/meta-agent/authoring/proposals/${proposal.proposal_id}/patch/apply`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            patch: pendingAuthoringPreview.patch,
            preview_checksum: pendingAuthoringPreview.preview.preview_checksum,
          }),
        },
      );
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        if (response.status === 409) {
          setPendingAuthoringPreview(null);
          throw new Error(
            `应用冲突：${readAuthoringError(payload, "Proposal、目标草稿或能力快照已变化，请重新加载并预览。")}`,
          );
        }
        throw new Error(readAuthoringError(payload, "应用 Graph Patch 失败。"));
      }
      // A successful Apply is authoritative even if the follow-up read fails.
      // Clear the one-shot preview before reloading so it cannot be submitted twice.
      setPendingAuthoringPreview(null);
      const applied =
        typeof payload === "object" && payload !== null && "proposal" in payload
          ? (payload as { proposal?: unknown }).proposal
          : payload;
      if (
        typeof applied !== "object" ||
        applied === null ||
        !("proposal_id" in applied) ||
        typeof (applied as { proposal_id?: unknown }).proposal_id !== "string"
      ) {
        throw new Error(
          "服务端已接受 Patch，但回执缺少 Proposal 标识；请重新加载确认状态，不要重复应用。",
        );
      }
      const appliedProposalId = String(
        (applied as { proposal_id: unknown }).proposal_id,
      );
      const appliedRevision =
        typeof (applied as { proposal_revision?: unknown }).proposal_revision === "number"
          ? Number((applied as { proposal_revision: number }).proposal_revision)
          : typeof (applied as { revision?: unknown }).revision === "number"
            ? Number((applied as { revision: number }).revision)
            : null;
      const expectedRevision = proposal.revision + 1;
      try {
        await loadProposal(appliedProposalId, false);
      } catch (reloadError) {
        const message =
          reloadError instanceof Error ? reloadError.message : "无法重新加载候选。";
        throw new Error(
          `类型化变更已应用，但刷新候选失败：${message} 请重新加载确认状态，不要重复应用。`,
        );
      }
      setNotice(
        appliedRevision === expectedRevision
          ? "类型化变更已原子应用，Proposal revision 增加一次。"
          : "变更已应用，但 revision 回执异常；请在继续审批前重新核对候选。",
      );
    } catch (applyError) {
      setError(applyError instanceof Error ? applyError.message : "应用 Graph Patch 失败。");
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
            ...(action === "approve" ? { apply_key: proposal.apply_key } : {}),
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
          throw new Error("提案已批准，但未返回目标智能体 ID。");
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
              智能体草稿。
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
                  {item === "create" ? "创建智能体" : "更新智能体"}
                </button>
              ))}
            </div>

            {mode === "update" ? (
              <label className="mt-3 block">
                <span className="text-xs font-semibold text-slate-300">目标智能体</span>
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
                placeholder="例如：构建一个负责研究、事实核查与审稿协作的智能体"
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
              {isGenerating ? "三阶段生成中..." : "生成候选智能体"}
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
                                    {(item.task_binding ?? item.planner?.task_binding) ===
                                    "forbidden" ? (
                                      <span className="rounded border border-cyan-300/30 bg-cyan-300/10 px-1 text-[9px] text-cyan-100">
                                        辅助节点
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
                      className="rounded-md border border-amber-300/30 bg-amber-300/10 px-3 py-2 text-xs font-semibold text-amber-100 hover:bg-amber-300/15"
                      onClick={() =>
                        navigate(
                          `/agents/evaluations?target_kind=proposal&proposal_id=${proposal.proposal_id}`,
                        )
                      }
                      type="button"
                    >
                      生成评测集
                    </button>
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
                      onClick={() => void saveCandidate().catch(() => undefined)}
                      type="button"
                    >
                      {authoringAvailability.mode === "headless"
                        ? "预览元数据变更"
                        : "保存元数据"}
                    </button>
                    <button
                      className="rounded-md border border-cyan-300/30 bg-cyan-300/10 px-3 py-2 text-xs font-semibold text-cyan-100 hover:bg-cyan-300/15"
                      disabled={isSaving || Boolean(pendingAuthoringPreview)}
                      onClick={() => void proposalAction("validate")}
                      type="button"
                    >
                      重新预检
                    </button>
                    <button
                      className="rounded-md bg-emerald-300 px-3 py-2 text-xs font-semibold text-emerald-950 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
                      disabled={isSaving || Boolean(pendingAuthoringPreview)}
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
                  {controlFlowReport ? (
                    <div className="mt-3 rounded-md border border-cyan-300/15 bg-cyan-300/[0.05] p-2.5">
                      <div className="flex flex-wrap items-center justify-between gap-2 text-[11px]">
                        <span className="font-semibold text-cyan-100">控制流静态证据</span>
                        <span className="text-cyan-100/65">
                          {controlFlowReport.router_count} 路由 · {controlFlowReport.scenario_count} 场景 · {controlFlowReport.final_source_count} 成功来源
                        </span>
                      </div>
                      <div className="mt-2 max-h-28 space-y-1 overflow-y-auto text-[10px] leading-4 text-slate-400">
                        {controlFlowReport.scenarios.slice(0, 12).map((scenario) => (
                          <p key={scenario.id}>
                            <span className="font-mono text-slate-300">{scenario.id}</span>
                            {scenario.outcomes.length ? ` · ${scenario.outcomes.join(", ")}` : " · 无路由 outcome"}
                            {scenario.success_sources.length
                              ? ` · 成功 ${scenario.success_sources.join(", ")}`
                              : ` · 错误 ${scenario.error_sources.join(", ")}`}
                          </p>
                        ))}
                      </div>
                    </div>
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

              <div
                className={`rounded-lg border px-3 py-2 text-xs leading-5 ${
                  authoringAvailability.mode === "headless"
                    ? "border-cyan-300/20 bg-cyan-300/[0.07] text-cyan-100"
                    : authoringAvailability.mode === "legacy"
                      ? "border-amber-300/20 bg-amber-300/[0.07] text-amber-100"
                      : "border-rose-300/25 bg-rose-300/[0.08] text-rose-100"
                }`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p>
                    {authoringAvailability.mode === "headless" ? (
                      <>
                        无头编排已启用。画布与元数据修改会先转换为类型化 Patch；确认应用前不会修改
                        Proposal。当前授权 {authoringAvailability.state.allowed_node_kinds.length} 类节点。
                      </>
                    ) : (
                      authoringAvailability.reason
                    )}
                  </p>
                  <button
                    className="rounded-md border border-current/20 px-2.5 py-1 font-semibold hover:bg-white/5 disabled:opacity-50"
                    disabled={isSaving}
                    onClick={() => {
                      setIsSaving(true);
                      setError("");
                      void loadProposal(proposal.proposal_id)
                        .catch((reloadError) => {
                          setError(
                            reloadError instanceof Error
                              ? reloadError.message
                              : "重新加载候选失败。",
                          );
                        })
                        .finally(() => setIsSaving(false));
                    }}
                    type="button"
                  >
                    重新加载候选
                  </button>
                </div>
              </div>

              <div className="h-[820px] overflow-hidden rounded-lg border border-white/10 bg-slate-950">
                {authoringAvailability.mode === "unavailable" ? (
                  <div className="flex h-full items-center justify-center p-8 text-center">
                    <div className="max-w-xl rounded-lg border border-rose-300/20 bg-rose-300/[0.06] p-5 text-sm leading-6 text-rose-100">
                      <p className="font-semibold">候选画布已锁定为只读</p>
                      <p className="mt-2 text-rose-100/75">
                        {authoringAvailability.reason}
                      </p>
                    </div>
                  </div>
                ) : (
                  <WorkflowEditor
                  authoringPolicy={
                    authoringAvailability.mode === "headless"
                      ? {
                          allowedNodeKinds: [
                            ...authoringAvailability.state.allowed_node_kinds,
                            ...(authoringAvailability.state.allowed_middleware_ids.length
                              ? (["runtime_middleware"] as const)
                              : []),
                          ],
                          compilerManagedNodeKinds:
                            authoringAvailability.state.compiler_managed_node_kinds,
                          allowedRuntimeMiddlewareIds:
                            authoringAvailability.state.allowed_middleware_ids,
                          allowedSourceAgentIds:
                            authoringAvailability.state.allowed_source_agent_ids,
                        }
                      : undefined
                  }
                  initialDefinition={workflow}
                  key={`${proposal.proposal_id}-${proposal.revision}`}
                  onSave={saveCandidate}
                  saveCompletionLabel={
                    authoringAvailability.mode === "headless"
                      ? "变更预览已生成，等待确认应用"
                      : "候选画布已保存"
                  }
                  saveLabel={
                    authoringAvailability.mode === "headless"
                      ? "预览候选画布"
                      : "保存候选画布"
                  }
                  workflowId={`meta-planner-${proposal.proposal_id}`}
                  />
                )}
              </div>
            </>
          ) : (
            <div className="flex min-h-[540px] items-center justify-center rounded-lg border border-dashed border-white/10 bg-white/[0.02] p-8 text-center">
              <div>
                <p className="text-base font-semibold text-white">尚未生成候选智能体</p>
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
          {routeReceipt ? (
            <ProviderRouteReceiptSummary receipt={routeReceipt} />
          ) : null}
          {error ? (
            <div className="rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-sm text-rose-100">
              {error}
            </div>
          ) : null}
        </div>
      </div>
      {pendingAuthoringPreview ? (
        <div
          aria-labelledby="meta-authoring-preview-title"
          aria-modal="true"
          className="fixed inset-0 z-[90] flex items-center justify-center bg-slate-950/80 p-4 backdrop-blur-sm"
          role="dialog"
        >
          <div className="max-h-[86vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-cyan-300/25 bg-slate-950 p-5 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-base font-semibold text-white" id="meta-authoring-preview-title">
                  确认类型化变更
                </h2>
                <p className="mt-1 text-xs leading-5 text-slate-400">
                  {pendingAuthoringPreview.source === "canvas" ? "候选画布" : "候选元数据"}
                  已完成无副作用预览。只有点击“确认应用”后 Proposal revision 才会更新一次。
                </p>
              </div>
              <button
                aria-label="关闭变更预览"
                className="flex h-8 w-8 items-center justify-center rounded-md border border-white/10 text-slate-400 hover:bg-white/5 hover:text-white"
                onClick={() => setPendingAuthoringPreview(null)}
                type="button"
              >
                ×
              </button>
            </div>

            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <div className="rounded-md border border-white/10 bg-white/[0.035] p-3">
                <p className="text-xs font-semibold text-slate-200">Patch 操作</p>
                <div className="mt-2 space-y-1 text-xs text-slate-400">
                  {pendingAuthoringPreview.patch.operations.map((operation, index) => (
                    <p key={`${operation.op}-${index}`}>
                      {index + 1}. <span className="font-mono text-cyan-100">{operation.op}</span>
                    </p>
                  ))}
                </div>
              </div>
              <div className="rounded-md border border-white/10 bg-white/[0.035] p-3">
                <p className="text-xs font-semibold text-slate-200">结构差异</p>
                <div className="mt-2 space-y-1 text-xs text-slate-400">
                  {authoringDiffSummary(pendingAuthoringPreview.preview.diff).map((line) => (
                    <p key={line}>{line}</p>
                  ))}
                </div>
              </div>
            </div>

            {pendingAuthoringPreview.preview.diagnostics.length ? (
              <div className="mt-3 rounded-md border border-rose-300/20 bg-rose-300/[0.07] p-3">
                <p className="text-xs font-semibold text-rose-100">门禁诊断</p>
                <div className="mt-2 space-y-1 text-xs text-rose-100/90">
                  {pendingAuthoringPreview.preview.diagnostics.map((diagnostic, index) => (
                    <p key={`${diagnostic.code ?? "diagnostic"}-${index}`}>
                      {diagnostic.code ? `[${diagnostic.code}] ` : ""}{diagnostic.message}
                    </p>
                  ))}
                </div>
              </div>
            ) : null}

            {pendingAuthoringPreview.preview.warnings.length ? (
              <div className="mt-3 rounded-md border border-amber-300/20 bg-amber-300/[0.07] p-3 text-xs text-amber-100">
                {pendingAuthoringPreview.preview.warnings.map((warning) => (
                  <p key={warning}>{warning}</p>
                ))}
              </div>
            ) : null}

            <div className="mt-5 flex flex-wrap justify-end gap-2">
              <button
                className="rounded-md border border-white/10 px-4 py-2 text-sm font-semibold text-slate-200 hover:bg-white/5"
                disabled={isSaving}
                onClick={() => setPendingAuthoringPreview(null)}
                type="button"
              >
                放弃预览
              </button>
              <button
                className="rounded-md bg-cyan-300 px-4 py-2 text-sm font-semibold text-slate-950 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
                disabled={isSaving || !pendingAuthoringPreview.preview.can_apply}
                onClick={() => void applyPendingPreview()}
                type="button"
              >
                {isSaving ? "正在应用..." : "确认应用"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
