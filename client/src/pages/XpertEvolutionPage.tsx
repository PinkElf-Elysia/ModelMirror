import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  Beaker,
  CheckCircle2,
  FlaskConical,
  GitCompareArrows,
  LoaderCircle,
  Play,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Square,
  TrendingUp,
  XCircle,
} from "lucide-react";
import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import PageContainer from "../components/PageContainer";
import { models } from "../data/models";
import { getXpert, listXpertVersions, listXperts } from "../utils/xpertApi";
import type { XpertDefinition, XpertSummary, XpertVersion } from "../types/xpert";

interface Dataset {
  dataset_id: string;
  name: string;
  status: string;
  published_version: number | null;
  case_count: number;
}

interface DatasetVersion {
  dataset_id: string;
  version: number;
  case_count: number;
  checksum: string;
}

interface PromptProfile {
  id: string;
  name: string;
  status: string;
  draft_revision: number;
  published_version: number | null;
  template: string;
}

interface Candidate {
  candidate_id: string;
  checksum: string;
  fields: Record<string, string>;
  summary: string;
  mutations?: Array<Record<string, unknown>>;
  diff?: StructureDiff;
}

interface Ranking {
  candidate_id: string;
  score: number;
  failed_count: number;
  estimated_tokens: number;
  average_latency_ms: number;
  p95_latency_ms?: number;
  model_calls?: number;
  node_count?: number;
  edge_count?: number;
  node_delta?: number;
  metrics: Record<string, number>;
}

interface StructureDiff {
  added_nodes?: Array<{ node_id: string; kind: string; title: string }>;
  removed_nodes?: Array<{ node_id: string; kind: string; title: string }>;
  replaced_nodes?: Array<{ node_id: string; from_kind: string; to_kind: string }>;
  added_edge_ids?: string[];
  removed_edge_ids?: string[];
  baseline_node_count?: number;
  candidate_node_count?: number;
  node_delta?: number;
  baseline_edge_count?: number;
  candidate_edge_count?: number;
  edge_delta?: number;
}

interface StructureCapabilityItem {
  id?: string;
  kind?: string;
  name?: string;
  title?: string;
  description?: string;
  high_risk?: boolean;
}

interface EvolutionCapabilities {
  structure?: {
    operations: string[];
    nodes: StructureCapabilityItem[];
    middleware: StructureCapabilityItem[];
    external_xperts: StructureCapabilityItem[];
    knowledge_bases: StructureCapabilityItem[];
    toolsets: StructureCapabilityItem[];
    plugins: StructureCapabilityItem[];
    default_scope?: {
      allowed_node_kinds: string[];
    };
  };
}

interface CandidateGraph {
  id: string;
  title: string;
  nodes: Array<{
    id: string;
    type: string;
    position?: { x: number; y: number } | null;
    data: Record<string, unknown>;
  }>;
  edges: Array<{
    id: string;
    source: string;
    target: string;
    sourceHandle?: string | null;
    targetHandle?: string | null;
  }>;
  diff: StructureDiff;
}

interface EvolutionRun {
  run_id: string;
  status: string;
  phase: string;
  target: {
    kind: "xpert" | "prompt_profile";
    evolution_kind?: "prompt" | "structure";
    target_id: string;
    base_revision: number;
    name: string;
    selected_fields: string[];
    baseline_prompts: Record<string, string>;
  };
  dataset: {
    dataset_id: string;
    version: number;
    name: string;
  };
  request: {
    evolution_kind?: "prompt" | "structure";
    generations: number;
    population_size: number;
    min_score_delta: number;
    max_metric_regression: number;
    model_policy: string;
    gate?: {
      min_score_delta: number;
      max_metric_regression: number;
      max_model_call_increase_ratio: number;
      max_token_increase_ratio: number;
      max_p95_latency_increase_ratio: number;
    };
  };
  train_case_ids: string[];
  validation_case_ids: string[];
  generations: Array<{
    generation: number;
    repair_used: boolean;
    candidates: Candidate[];
    ranking: Ranking[];
    rejected_candidates?: Array<{
      index: number;
      summary: string;
      issues: string[];
    }>;
  }>;
  finalists: Array<{ candidate_id: string; checksum: string }>;
  report: {
    training_baseline?: Ranking;
    validation?: {
      targets?: Array<Ranking & { target_id: string; label: string }>;
    };
    gate?: {
      passed: boolean;
      reason: string;
      best_candidate_id?: string;
      baseline_score?: number;
      candidate_score?: number;
      score_delta?: number;
      metric_regressions?: Record<string, number>;
      new_failures?: boolean;
      cost_regressions?: string[];
      costs?: Record<string, {
        baseline: number;
        candidate: number;
        limit: number;
        exceeded: boolean;
      }>;
      complexity?: {
        added_nodes: number;
        removed_nodes: number;
        node_delta: number;
        candidate_node_count: number;
        passed: boolean;
      };
    };
  };
  warnings: string[];
  proposal_id?: string | null;
  proposal_revision?: number | null;
  stale: boolean;
  error?: string | null;
  created_at: number;
  completed_at?: number | null;
}

type TargetKind = "xpert" | "prompt_profile" | "structure";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload && typeof payload === "object"
      ? (payload as { detail?: unknown }).detail
      : null;
    throw new Error(typeof detail === "string" ? detail : `请求失败：${response.status}`);
  }
  return payload as T;
}

function postJson(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

function score(value?: number) {
  return value == null ? "-" : `${(value * 100).toFixed(1)}%`;
}

function statusTone(status: string) {
  if (status === "completed") return "border-emerald-300/30 bg-emerald-300/10 text-emerald-100";
  if (status === "no_improvement" || status === "stale") return "border-amber-300/30 bg-amber-300/10 text-amber-100";
  if (status === "failed" || status === "cancelled") return "border-rose-300/30 bg-rose-300/10 text-rose-100";
  return "border-cyan-300/30 bg-cyan-300/10 text-cyan-100";
}

export default function XpertEvolutionPage() {
  const { runId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const [targetKind, setTargetKind] = useState<TargetKind>(
    searchParams.get("evolution_kind") === "structure"
      ? "structure"
      : searchParams.get("prompt_profile_id")
        ? "prompt_profile"
        : "xpert",
  );
  const [capabilities, setCapabilities] = useState<EvolutionCapabilities | null>(null);
  const [xperts, setXperts] = useState<XpertSummary[]>([]);
  const [profiles, setProfiles] = useState<PromptProfile[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetVersions, setDatasetVersions] = useState<DatasetVersion[]>([]);
  const [selectedXpertId, setSelectedXpertId] = useState(searchParams.get("xpert_id") ?? "");
  const [selectedProfileId, setSelectedProfileId] = useState(searchParams.get("prompt_profile_id") ?? "");
  const [selectedXpert, setSelectedXpert] = useState<XpertDefinition | null>(null);
  const [hostXpertId, setHostXpertId] = useState("");
  const [hostVersions, setHostVersions] = useState<XpertVersion[]>([]);
  const [hostVersion, setHostVersion] = useState(0);
  const [promptFields, setPromptFields] = useState<string[]>([]);
  const [datasetId, setDatasetId] = useState("");
  const [datasetVersion, setDatasetVersion] = useState(0);
  const [optimizerModelId, setOptimizerModelId] = useState(models[0]?.id ?? "");
  const [modelPolicy, setModelPolicy] = useState<"snapshot" | "override">("snapshot");
  const [overrideModelId, setOverrideModelId] = useState(models[0]?.id ?? "");
  const [judgeModelId, setJudgeModelId] = useState(models[0]?.id ?? "");
  const [defaultAgentModelId, setDefaultAgentModelId] = useState(models[0]?.id ?? "");
  const [generations, setGenerations] = useState(2);
  const [populationSize, setPopulationSize] = useState(4);
  const [seed, setSeed] = useState(42);
  const [allowedNodeKinds, setAllowedNodeKinds] = useState<string[]>([]);
  const [externalXpertIds, setExternalXpertIds] = useState<string[]>([]);
  const [knowledgeBaseIds, setKnowledgeBaseIds] = useState<string[]>([]);
  const [toolsetIds, setToolsetIds] = useState<string[]>([]);
  const [pluginIds, setPluginIds] = useState<string[]>([]);
  const [middlewareIds, setMiddlewareIds] = useState<string[]>([]);
  const [allowedOperations, setAllowedOperations] = useState<string[]>([]);
  const [maxOperations, setMaxOperations] = useState(4);
  const [maxAddedNodes, setMaxAddedNodes] = useState(4);
  const [maxRemovedNodes, setMaxRemovedNodes] = useState(4);
  const [maxModelCallIncrease, setMaxModelCallIncrease] = useState(1);
  const [maxTokenIncrease, setMaxTokenIncrease] = useState(1);
  const [maxLatencyIncrease, setMaxLatencyIncrease] = useState(1);
  const [run, setRun] = useState<EvolutionRun | null>(null);
  const [runs, setRuns] = useState<EvolutionRun[]>([]);
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [preflight, setPreflight] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    document.title = "模镜 - 智能体受控优化";
    void loadOptions();
  }, []);

  useEffect(() => {
    if (runId) void loadRun(runId);
  }, [runId]);

  useEffect(() => {
    if (!run || !["queued", "running"].includes(run.status)) return;
    const timer = window.setInterval(() => void loadRun(run.run_id, true), 1500);
    return () => window.clearInterval(timer);
  }, [run]);

  useEffect(() => {
    if (!selectedXpertId) {
      setSelectedXpert(null);
      return;
    }
    void getXpert(selectedXpertId)
      .then((item) => {
        setSelectedXpert(item);
        const available = promptOptions(item);
        setPromptFields((current) => current.filter((field) => available.some((option) => option.key === field)));
      })
      .catch((caught) => setError(caught instanceof Error ? caught.message : "智能体加载失败"));
  }, [selectedXpertId]);

  useEffect(() => {
    if (!hostXpertId) {
      setHostVersions([]);
      setHostVersion(0);
      return;
    }
    void listXpertVersions(hostXpertId)
      .then((items) => {
        setHostVersions(items);
        setHostVersion(items[0]?.version || 0);
      })
      .catch((caught) => setError(caught instanceof Error ? caught.message : "宿主版本加载失败"));
  }, [hostXpertId]);

  useEffect(() => {
    if (!datasetId) {
      setDatasetVersions([]);
      setDatasetVersion(0);
      return;
    }
    void requestJson<{ items: DatasetVersion[] }>(`/api/xpert-evaluations/datasets/${datasetId}/versions`)
      .then((payload) => {
        setDatasetVersions(payload.items ?? []);
        setDatasetVersion(payload.items?.[0]?.version || 0);
      })
      .catch((caught) => setError(caught instanceof Error ? caught.message : "数据集版本加载失败"));
  }, [datasetId]);

  const availablePromptFields = useMemo(
    () => selectedXpert ? promptOptions(selectedXpert) : [],
    [selectedXpert],
  );

  const selectedProfile = profiles.find((item) => item.id === selectedProfileId) ?? null;
  const allCandidates = useMemo(
    () => run?.generations.flatMap((generation) => generation.candidates) ?? [],
    [run],
  );
  const selectedCandidate = allCandidates.find((item) => item.candidate_id === selectedCandidateId)
    ?? allCandidates.find((item) => item.candidate_id === run?.report.gate?.best_candidate_id)
    ?? allCandidates[0]
    ?? null;

  async function loadOptions() {
    try {
      const [xpertPayload, profilePayload, datasetPayload, runPayload, capabilityPayload] = await Promise.all([
        listXperts({ limit: 200 }),
        requestJson<{ items: PromptProfile[] }>("/api/prompt-profiles?limit=200"),
        requestJson<{ items: Dataset[] }>("/api/xpert-evaluations/datasets"),
        requestJson<{ items: EvolutionRun[] }>("/api/xpert-evolutions/runs?limit=50"),
        requestJson<EvolutionCapabilities>("/api/xpert-evolutions/capabilities"),
      ]);
      setXperts(xpertPayload.items);
      setProfiles(profilePayload.items ?? []);
      setDatasets(datasetPayload.items ?? []);
      setRuns(runPayload.items ?? []);
      setCapabilities(capabilityPayload);
      setAllowedNodeKinds((current) => current.length
        ? current
        : capabilityPayload.structure?.default_scope?.allowed_node_kinds ?? []);
      setAllowedOperations((current) => current.length
        ? current
        : capabilityPayload.structure?.operations ?? []);
      setSelectedXpertId((current) => current || xpertPayload.items[0]?.id || "");
      setSelectedProfileId((current) => current || profilePayload.items?.[0]?.id || "");
      setHostXpertId((current) => current || xpertPayload.items.find((item) => item.published_version)?.id || "");
      setDatasetId((current) => current || datasetPayload.items?.find((item) => item.published_version)?.dataset_id || "");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "进化工作台加载失败");
    }
  }

  async function loadRun(id: string, quiet = false) {
    if (!quiet) setBusy("load");
    try {
      const payload = await requestJson<EvolutionRun>(`/api/xpert-evolutions/runs/${id}`);
      setRun(payload);
      setSelectedCandidateId((current) => current || payload.report.gate?.best_candidate_id || "");
      setError("");
    } catch (caught) {
      if (!quiet) setError(caught instanceof Error ? caught.message : "进化运行加载失败");
    } finally {
      if (!quiet) setBusy("");
    }
  }

  function requestPayload() {
    const target = targetKind === "prompt_profile" ? selectedProfile : selectedXpert;
    if (!target) throw new Error("请选择进化目标");
    if (!datasetId || !datasetVersion) throw new Error("请选择已发布评测集版本");
    const structure = targetKind === "structure";
    return {
      evolution_kind: structure ? "structure" : "prompt",
      target_kind: targetKind === "prompt_profile" ? "prompt_profile" : "xpert",
      target_id: targetKind === "prompt_profile" ? selectedProfile!.id : selectedXpert!.id,
      target_revision: targetKind === "prompt_profile" ? selectedProfile!.draft_revision : selectedXpert!.draft_revision,
      prompt_fields: targetKind === "xpert" ? promptFields : [],
      host_xpert_id: targetKind === "prompt_profile" ? hostXpertId : null,
      host_xpert_version: targetKind === "prompt_profile" ? hostVersion : null,
      dataset_id: datasetId,
      dataset_version: datasetVersion,
      optimizer_model_id: optimizerModelId,
      model_policy: modelPolicy,
      override_model_id: modelPolicy === "override" ? overrideModelId : null,
      judge_model_id: judgeModelId || null,
      seed,
      generations,
      population_size: populationSize,
      min_score_delta: 0.01,
      max_metric_regression: 0.02,
      default_agent_model_id: structure ? defaultAgentModelId : null,
      scope: structure ? {
        allowed_node_kinds: allowedNodeKinds,
        external_xpert_ids: externalXpertIds,
        knowledge_base_ids: knowledgeBaseIds,
        toolset_ids: toolsetIds,
        plugin_ids: pluginIds,
        middleware_ids: middlewareIds,
      } : undefined,
      mutation_policy: structure ? {
        allowed_operations: allowedOperations,
        max_operations_per_candidate: maxOperations,
        max_added_nodes: maxAddedNodes,
        max_removed_nodes: maxRemovedNodes,
      } : undefined,
      gate: structure ? {
        min_score_delta: 0.01,
        max_metric_regression: 0.02,
        max_model_call_increase_ratio: maxModelCallIncrease,
        max_token_increase_ratio: maxTokenIncrease,
        max_p95_latency_increase_ratio: maxLatencyIncrease,
      } : undefined,
      budget: {
        repetitions: 1,
        max_concurrency: 2,
        case_timeout_seconds: 120,
        max_model_calls: 16,
        max_tool_calls: 24,
        max_estimated_tokens: 64000,
        max_output_chars: 20000,
      },
    };
  }

  async function runPreflight() {
    setBusy("preflight");
    setError("");
    try {
      const payload = await requestJson<Record<string, unknown>>(
        "/api/xpert-evolutions/preflight",
        postJson(requestPayload()),
      );
      setPreflight(payload);
      if (payload.valid === false) {
        const issues = payload.issues as Array<{ message?: string }> | undefined;
        throw new Error(issues?.map((item) => item.message).filter(Boolean).join("；") || "预检未通过");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "预检失败");
    } finally {
      setBusy("");
    }
  }

  async function startRun() {
    setBusy("start");
    setError("");
    try {
      const payload = await requestJson<EvolutionRun>(
        "/api/xpert-evolutions/runs",
        postJson(requestPayload()),
      );
      navigate(`/agents/evolution/${payload.run_id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "启动失败");
    } finally {
      setBusy("");
    }
  }

  async function cancelRun() {
    if (!run) return;
    setBusy("cancel");
    try {
      await requestJson(`/api/xpert-evolutions/runs/${run.run_id}/cancel`, postJson({}));
      await loadRun(run.run_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "取消失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <PageContainer>
      <header className="mb-5 flex flex-col gap-4 border-b border-white/10 pb-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase text-cyan-200">EvoAgentX Evolution</p>
          <h1 className="mt-2 text-2xl font-semibold text-white">智能体受控优化</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-400">
            固定草稿、能力快照与数据集，对 Prompt 或工作流结构执行有界搜索。通过质量、成本和安全门禁后只创建待审批 Proposal。
          </p>
        </div>
        <div className="flex items-center gap-2">
          {runId ? (
            <Link className="inline-flex items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-xs text-slate-200 hover:bg-white/[0.05]" to="/agents/evolution">
              <ArrowLeft className="h-4 w-4" /> 新建运行
            </Link>
          ) : null}
          <Link className="inline-flex items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-xs text-slate-200 hover:bg-white/[0.05]" to="/agents/evaluations">
            <Beaker className="h-4 w-4" /> 评测集
          </Link>
        </div>
      </header>

      {error ? (
        <div className="mb-4 flex items-start gap-2 border border-rose-300/25 bg-rose-300/10 p-3 text-sm text-rose-100">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" /> {error}
        </div>
      ) : null}

      {runId ? (
        <RunDetail
          busy={busy}
          onCancel={() => void cancelRun()}
          onRefresh={() => void loadRun(runId)}
          run={run}
          selectedCandidate={selectedCandidate}
          selectedCandidateId={selectedCandidateId}
          setSelectedCandidateId={setSelectedCandidateId}
        />
      ) : (
        <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
          <main className="min-w-0 space-y-5">
            <section className="border border-white/10 bg-ink-950/55 p-4">
              <div className="mb-4 flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-cyan-200" />
                <h2 className="text-sm font-semibold text-white">进化目标</h2>
              </div>
              <div className="mb-4 inline-flex border border-white/10 bg-black/20 p-1">
                {(["xpert", "prompt_profile", "structure"] as TargetKind[]).map((kind) => (
                  <button
                    className={`px-3 py-1.5 text-xs ${targetKind === kind ? "bg-cyan-300 text-slate-950" : "text-slate-400 hover:text-white"}`}
                    key={kind}
                    onClick={() => {
                      setTargetKind(kind);
                      setPreflight(null);
                    }}
                    type="button"
                  >
                    {kind === "xpert"
                      ? "智能体 Agent Prompt"
                      : kind === "prompt_profile"
                        ? "Prompt Profile"
                        : "工作流结构"}
                  </button>
                ))}
              </div>
              {targetKind !== "prompt_profile" ? (
                <>
                  <label className="block text-xs text-slate-400">
                    智能体草稿
                    <select className="mt-1 w-full border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => setSelectedXpertId(event.target.value)} value={selectedXpertId}>
                      {xperts.map((item) => <option key={item.id} value={item.id}>{item.name} · r{item.draft_revision}</option>)}
                    </select>
                  </label>
                  {targetKind === "xpert" ? <div className="mt-4">
                    <p className="text-xs text-slate-400">选择 1–3 个字段联合优化</p>
                    <div className="mt-2 grid gap-2 md:grid-cols-2">
                      {availablePromptFields.map((option) => {
                        const checked = promptFields.includes(option.key);
                        return (
                          <label className={`flex cursor-pointer items-start gap-3 border p-3 ${checked ? "border-cyan-300/40 bg-cyan-300/10" : "border-white/10 bg-white/[0.02]"}`} key={option.key}>
                            <input
                              checked={checked}
                              className="mt-1"
                              onChange={() => setPromptFields((current) => checked
                                ? current.filter((value) => value !== option.key)
                                : current.length < 3 ? [...current, option.key] : current)}
                              type="checkbox"
                            />
                            <span>
                              <span className="block text-xs font-semibold text-white">{option.label}</span>
                              <span className="mt-1 line-clamp-2 block text-[11px] text-slate-500">{option.preview || "当前为空"}</span>
                            </span>
                          </label>
                        );
                      })}
                    </div>
                  </div> : (
                    <StructureScopePanel
                      allowedNodeKinds={allowedNodeKinds}
                      allowedOperations={allowedOperations}
                      capabilities={capabilities}
                      defaultAgentModelId={defaultAgentModelId}
                      externalXpertIds={externalXpertIds}
                      knowledgeBaseIds={knowledgeBaseIds}
                      maxAddedNodes={maxAddedNodes}
                      maxLatencyIncrease={maxLatencyIncrease}
                      maxModelCallIncrease={maxModelCallIncrease}
                      maxOperations={maxOperations}
                      maxRemovedNodes={maxRemovedNodes}
                      maxTokenIncrease={maxTokenIncrease}
                      middlewareIds={middlewareIds}
                      pluginIds={pluginIds}
                      setAllowedNodeKinds={setAllowedNodeKinds}
                      setAllowedOperations={setAllowedOperations}
                      setDefaultAgentModelId={setDefaultAgentModelId}
                      setExternalXpertIds={setExternalXpertIds}
                      setKnowledgeBaseIds={setKnowledgeBaseIds}
                      setMaxAddedNodes={setMaxAddedNodes}
                      setMaxLatencyIncrease={setMaxLatencyIncrease}
                      setMaxModelCallIncrease={setMaxModelCallIncrease}
                      setMaxOperations={setMaxOperations}
                      setMaxRemovedNodes={setMaxRemovedNodes}
                      setMaxTokenIncrease={setMaxTokenIncrease}
                      setMiddlewareIds={setMiddlewareIds}
                      setPluginIds={setPluginIds}
                      setToolsetIds={setToolsetIds}
                      toolsetIds={toolsetIds}
                    />
                  )}
                </>
              ) : (
                <div className="grid gap-4 md:grid-cols-2">
                  <label className="block text-xs text-slate-400">
                    Prompt Profile 草稿
                    <select className="mt-1 w-full border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => setSelectedProfileId(event.target.value)} value={selectedProfileId}>
                      {profiles.map((item) => <option key={item.id} value={item.id}>{item.name} · r{item.draft_revision}</option>)}
                    </select>
                  </label>
                  <label className="block text-xs text-slate-400">
                    固定评测宿主智能体
                    <select className="mt-1 w-full border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => setHostXpertId(event.target.value)} value={hostXpertId}>
                      {xperts.filter((item) => item.published_version).map((item) => <option key={item.id} value={item.id}>{item.name} · v{item.published_version}</option>)}
                    </select>
                  </label>
                  <label className="block text-xs text-slate-400">
                    宿主版本
                    <select className="mt-1 w-full border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => setHostVersion(Number(event.target.value))} value={hostVersion}>
                      {hostVersions.map((item) => <option key={item.version} value={item.version}>v{item.version} · {item.checksum.slice(0, 10)}</option>)}
                    </select>
                  </label>
                  <div className="border border-white/10 bg-white/[0.02] p-3 text-xs text-slate-400">
                    每条用例的 message 会作为 <code className="text-cyan-200">{"{{args}}"}</code> 渲染输入，宿主版本和资源保持不变。
                  </div>
                </div>
              )}
            </section>

            <section className="grid gap-4 border border-white/10 bg-ink-950/55 p-4 md:grid-cols-2">
              <div>
                <div className="mb-3 flex items-center gap-2">
                  <FlaskConical className="h-4 w-4 text-amber-200" />
                  <h2 className="text-sm font-semibold text-white">数据隔离</h2>
                </div>
                <label className="block text-xs text-slate-400">
                  已发布 Dataset
                  <select className="mt-1 w-full border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => setDatasetId(event.target.value)} value={datasetId}>
                    {datasets.filter((item) => item.published_version).map((item) => <option key={item.dataset_id} value={item.dataset_id}>{item.name} · {item.case_count} cases</option>)}
                  </select>
                </label>
                <label className="mt-3 block text-xs text-slate-400">
                  固定版本
                  <select className="mt-1 w-full border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => setDatasetVersion(Number(event.target.value))} value={datasetVersion}>
                    {datasetVersions.map((item) => <option key={item.version} value={item.version}>v{item.version} · {item.case_count} cases</option>)}
                  </select>
                </label>
                <label className="mt-3 block text-xs text-slate-400">
                  拆分 seed
                  <input className="mt-1 w-full border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" min={0} onChange={(event) => setSeed(Number(event.target.value))} type="number" value={seed} />
                </label>
              </div>
              <div>
                <div className="mb-3 flex items-center gap-2">
                  <GitCompareArrows className="h-4 w-4 text-violet-200" />
                  <h2 className="text-sm font-semibold text-white">搜索边界</h2>
                </div>
                <label className="block text-xs text-slate-400">
                  代数：{generations}
                  <input className="mt-2 w-full accent-cyan-300" max={3} min={1} onChange={(event) => setGenerations(Number(event.target.value))} type="range" value={generations} />
                </label>
                <label className="mt-4 block text-xs text-slate-400">
                  每代候选：{populationSize}
                  <input className="mt-2 w-full accent-cyan-300" max={5} min={2} onChange={(event) => setPopulationSize(Number(event.target.value))} type="range" value={populationSize} />
                </label>
                <div className="mt-4 border border-emerald-300/20 bg-emerald-300/[0.06] p-3 text-xs leading-5 text-emerald-100">
                  {targetKind === "structure"
                    ? "结构候选同时接受质量、模型调用、Token、P95 延迟与图复杂度门禁。静态失败候选保留报告，但不会消耗评测预算。"
                    : "验证分至少提升 1%，且任一指标不得回退超过 2%。不允许新增超时、预算或安全错误。"}
                </div>
              </div>
            </section>

            <section className="grid gap-4 border border-white/10 bg-ink-950/55 p-4 md:grid-cols-2">
              <label className="block text-xs text-slate-400">
                Optimizer 模型
                <select className="mt-1 w-full border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => setOptimizerModelId(event.target.value)} value={optimizerModelId}>
                  {models.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </label>
              <label className="block text-xs text-slate-400">
                Rubric Judge 模型
                <select className="mt-1 w-full border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => setJudgeModelId(event.target.value)} value={judgeModelId}>
                  {models.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </label>
              <label className="block text-xs text-slate-400">
                Evaluator 模型策略
                <select className="mt-1 w-full border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => setModelPolicy(event.target.value as "snapshot" | "override")} value={modelPolicy}>
                  <option value="snapshot">使用目标快照模型</option>
                  <option value="override">统一替换模型</option>
                </select>
              </label>
              {modelPolicy === "override" ? (
                <label className="block text-xs text-slate-400">
                  统一模型
                  <select className="mt-1 w-full border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => setOverrideModelId(event.target.value)} value={overrideModelId}>
                    {models.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </select>
                </label>
              ) : null}
            </section>

            <div className="flex flex-wrap items-center justify-end gap-2">
              <button className="inline-flex items-center gap-2 rounded-md border border-white/10 px-4 py-2 text-sm text-slate-200 hover:bg-white/[0.05] disabled:opacity-50" disabled={Boolean(busy)} onClick={() => void runPreflight()} type="button">
                {busy === "preflight" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <ShieldCheck className="h-4 w-4" />}
                只读预检
              </button>
              <button className="inline-flex items-center gap-2 rounded-md bg-cyan-300 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-cyan-200 disabled:opacity-50" disabled={Boolean(busy)} onClick={() => void startRun()} type="button">
                {busy === "start" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                启动受控进化
              </button>
            </div>
            {preflight?.valid === true ? (
              <div className="flex items-center gap-2 border border-emerald-300/25 bg-emerald-300/10 p-3 text-xs text-emerald-100">
                <CheckCircle2 className="h-4 w-4" /> 预检通过，优化集与验证集已按 seed 固定。
              </div>
            ) : null}
          </main>

          <aside className="min-w-0 border border-white/10 bg-ink-950/55 p-4">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-semibold text-white">最近运行</h2>
              <button className="text-slate-500 hover:text-white" onClick={() => void loadOptions()} title="刷新" type="button"><RefreshCw className="h-4 w-4" /></button>
            </div>
            <div className="space-y-2">
              {runs.map((item) => (
                <Link className="block border border-white/10 bg-white/[0.02] p-3 hover:border-cyan-300/30" key={item.run_id} to={`/agents/evolution/${item.run_id}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-xs font-semibold text-white">{item.target.name}</span>
                    <span className={`border px-2 py-0.5 text-[10px] ${statusTone(item.status)}`}>{item.status}</span>
                  </div>
                  <p className="mt-2 text-[11px] text-slate-500">{item.phase} · {item.target.kind}</p>
                </Link>
              ))}
              {!runs.length ? <p className="border border-dashed border-white/10 p-5 text-center text-xs text-slate-500">尚无进化运行</p> : null}
            </div>
          </aside>
        </div>
      )}
    </PageContainer>
  );
}

function RunDetail({
  run,
  busy,
  selectedCandidate,
  selectedCandidateId,
  setSelectedCandidateId,
  onRefresh,
  onCancel,
}: {
  run: EvolutionRun | null;
  busy: string;
  selectedCandidate: Candidate | null;
  selectedCandidateId: string;
  setSelectedCandidateId: (value: string) => void;
  onRefresh: () => void;
  onCancel: () => void;
}) {
  const structure = run?.request.evolution_kind === "structure"
    || run?.target.evolution_kind === "structure";
  const [candidateGraph, setCandidateGraph] = useState<CandidateGraph | null>(null);
  const [graphError, setGraphError] = useState("");

  useEffect(() => {
    if (!run || !structure || !selectedCandidate?.candidate_id) {
      setCandidateGraph(null);
      setGraphError("");
      return;
    }
    let active = true;
    void requestJson<CandidateGraph>(
      `/api/xpert-evolutions/runs/${run.run_id}/candidates/${selectedCandidate.candidate_id}/graph`,
    )
      .then((payload) => {
        if (active) {
          setCandidateGraph(payload);
          setGraphError("");
        }
      })
      .catch((caught) => {
        if (active) {
          setCandidateGraph(null);
          setGraphError(caught instanceof Error ? caught.message : "候选图加载失败");
        }
      });
    return () => {
      active = false;
    };
  }, [run?.run_id, selectedCandidate?.candidate_id, structure]);

  if (!run) {
    return <div className="flex min-h-64 items-center justify-center text-sm text-slate-500"><LoaderCircle className="mr-2 h-4 w-4 animate-spin" />加载运行...</div>;
  }
  const active = ["queued", "running"].includes(run.status);
  const gate = run.report.gate;
  return (
    <div className="space-y-5">
      <section className="grid gap-4 border border-white/10 bg-ink-950/55 p-4 md:grid-cols-[1fr_auto]">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold text-white">{run.target.name}</h2>
            <span className={`border px-2 py-1 text-[11px] ${statusTone(run.status)}`}>{run.status}</span>
            <span className="border border-white/10 bg-white/[0.04] px-2 py-1 text-[11px] text-slate-300">
              {structure ? "工作流结构" : "Prompt"}
            </span>
            {run.stale ? <span className="border border-amber-300/30 bg-amber-300/10 px-2 py-1 text-[11px] text-amber-100">revision stale</span> : null}
          </div>
          <p className="mt-2 text-xs text-slate-400">
            {run.phase} · Dataset {run.dataset.name} v{run.dataset.version} · train {run.train_case_ids.length} / validation {run.validation_case_ids.length}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button className="rounded-md border border-white/10 p-2 text-slate-300 hover:bg-white/[0.05]" onClick={onRefresh} title="刷新" type="button"><RefreshCw className="h-4 w-4" /></button>
          {active ? <button className="inline-flex items-center gap-2 rounded-md border border-rose-300/25 px-3 py-2 text-xs text-rose-100" disabled={Boolean(busy)} onClick={onCancel} type="button"><Square className="h-4 w-4" />取消</button> : null}
        </div>
      </section>

      {run.warnings.length ? (
        <div className="space-y-1 border border-amber-300/25 bg-amber-300/[0.07] p-3 text-xs text-amber-100">
          {run.warnings.map((warning) => <p className="flex gap-2" key={warning}><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />{warning}</p>)}
        </div>
      ) : null}

      <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]">
        <main className="min-w-0 space-y-5">
          {run.generations.map((generation) => (
            <section className="border border-white/10 bg-ink-950/55 p-4" key={generation.generation}>
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-white">Generation {generation.generation}</h3>
                  <p className="mt-1 text-[11px] text-slate-500">
                    {generation.candidates.length} 个安全候选
                    {generation.rejected_candidates?.length
                      ? ` · ${generation.rejected_candidates.length} 个静态淘汰`
                      : ""}
                    {generation.repair_used ? " · 使用一次 JSON 修复" : ""}
                  </p>
                </div>
                <TrendingUp className="h-4 w-4 text-cyan-200" />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[680px] text-left text-xs">
                  <thead className="border-b border-white/10 text-slate-500">
                    <tr><th className="py-2">排名</th><th>候选</th><th>训练分</th><th>失败</th><th>Tokens</th><th>{structure ? "节点" : "延迟"}</th><th></th></tr>
                  </thead>
                  <tbody>
                    {generation.ranking.map((ranking, index) => {
                      const candidate = generation.candidates.find((item) => item.candidate_id === ranking.candidate_id);
                      return (
                        <tr className="border-b border-white/[0.06] text-slate-300" key={ranking.candidate_id}>
                          <td className="py-2 text-slate-500">#{index + 1}</td>
                          <td><p className="font-medium text-white">{ranking.candidate_id}</p><p className="max-w-sm truncate text-[10px] text-slate-500">{candidate?.summary}</p></td>
                          <td>{score(ranking.score)}</td><td>{ranking.failed_count}</td><td>{ranking.estimated_tokens}</td>
                          <td>{structure ? `${ranking.node_count ?? "-"} (${(ranking.node_delta ?? 0) >= 0 ? "+" : ""}${ranking.node_delta ?? 0})` : `${Math.round(ranking.average_latency_ms)} ms`}</td>
                          <td><button className="text-cyan-200 hover:text-cyan-100" onClick={() => setSelectedCandidateId(ranking.candidate_id)} type="button">{structure ? "查看结构" : "查看 diff"}</button></td>
                        </tr>
                      );
                    })}
                    {!generation.ranking.length ? <tr><td className="py-4 text-slate-500" colSpan={7}>正在生成或评测本代候选...</td></tr> : null}
                  </tbody>
                </table>
              </div>
              {generation.rejected_candidates?.length ? (
                <details className="mt-3 border-t border-white/10 pt-3 text-xs text-slate-400">
                  <summary className="cursor-pointer text-amber-200">静态淘汰候选</summary>
                  <div className="mt-2 space-y-2">
                    {generation.rejected_candidates.map((item) => (
                      <div className="bg-amber-300/[0.05] p-2" key={`${generation.generation}-${item.index}`}>
                        <p className="font-medium text-slate-200">Candidate {item.index}: {item.summary || "无摘要"}</p>
                        {item.issues.map((issue) => <p className="mt-1 text-amber-100" key={issue}>{issue}</p>)}
                      </div>
                    ))}
                  </div>
                </details>
              ) : null}
            </section>
          ))}

          {gate ? (
            <section className={`border p-4 ${gate.passed ? "border-emerald-300/25 bg-emerald-300/[0.06]" : "border-amber-300/25 bg-amber-300/[0.06]"}`}>
              <div className="flex items-start gap-3">
                {gate.passed ? <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-200" /> : <AlertTriangle className="mt-0.5 h-5 w-5 text-amber-200" />}
                <div>
                  <h3 className="text-sm font-semibold text-white">{gate.passed ? "非退化门禁通过" : "未通过非退化门禁"}</h3>
                  <p className="mt-1 text-xs text-slate-300">{gate.reason}</p>
                  <div className="mt-3 flex flex-wrap gap-4 text-xs text-slate-400">
                    <span>基线 {score(gate.baseline_score)}</span>
                    <span>候选 {score(gate.candidate_score)}</span>
                    <span>差值 {score(gate.score_delta)}</span>
                    {gate.complexity ? <span>节点变化 {gate.complexity.node_delta >= 0 ? "+" : ""}{gate.complexity.node_delta}</span> : null}
                  </div>
                  {gate.costs ? (
                    <div className="mt-3 grid gap-2 sm:grid-cols-3">
                      {Object.entries(gate.costs).map(([name, value]) => (
                        <div className={`p-2 text-[11px] ${value.exceeded ? "bg-rose-300/10 text-rose-100" : "bg-white/[0.04] text-slate-300"}`} key={name}>
                          <p className="font-medium">{name}</p>
                          <p className="mt-1">{Math.round(value.candidate)} / 限额 {Math.round(value.limit)}</p>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              </div>
            </section>
          ) : null}

          {run.proposal_id ? (
            <section className="flex flex-col gap-3 border border-cyan-300/25 bg-cyan-300/[0.06] p-4 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h3 className="text-sm font-semibold text-white">待审批 Proposal 已创建</h3>
                <p className="mt-1 text-xs text-slate-400">{run.proposal_id} · 审批后只更新草稿，不自动发布。</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {run.target.kind === "xpert" ? (
                  <Link className="rounded-md border border-amber-300/25 bg-amber-300/10 px-3 py-2 text-xs font-semibold text-amber-100" to={`/agents/evaluations?target_kind=proposal&proposal_id=${run.proposal_id}`}>
                    生成评测集
                  </Link>
                ) : null}
                <Link
                  className="rounded-md bg-cyan-300 px-3 py-2 text-xs font-semibold text-slate-950"
                  to={
                    run.target.kind === "xpert"
                      ? `/agents/meta-agent?proposal_id=${run.proposal_id}`
                      : `/prompts?profile_id=${run.target.target_id}&proposal_id=${run.proposal_id}`
                  }
                >
                  进入审批
                </Link>
              </div>
            </section>
          ) : null}
          {run.error ? <div className="border border-rose-300/25 bg-rose-300/10 p-3 text-xs text-rose-100">{run.error}</div> : null}
        </main>

        <aside className="min-w-0 border border-white/10 bg-ink-950/55 p-4">
          <div className="mb-3 flex items-center gap-2">
            <GitCompareArrows className="h-4 w-4 text-violet-200" />
            <h3 className="text-sm font-semibold text-white">{structure ? "候选结构" : "Prompt Diff"}</h3>
          </div>
          <select className="mb-4 w-full border border-white/10 bg-ink-950 px-3 py-2 text-xs text-white" onChange={(event) => setSelectedCandidateId(event.target.value)} value={selectedCandidateId || selectedCandidate?.candidate_id || ""}>
            {run.generations.flatMap((generation) => generation.candidates).map((candidate) => <option key={candidate.candidate_id} value={candidate.candidate_id}>{candidate.candidate_id}</option>)}
          </select>
          {structure ? (
            selectedCandidate ? (
              <div className="space-y-4">
                <StructureGraphPreview graph={candidateGraph} loading={!candidateGraph && !graphError} />
                {graphError ? <p className="bg-rose-300/10 p-3 text-xs text-rose-100">{graphError}</p> : null}
                <StructureDiffSummary candidate={selectedCandidate} />
              </div>
            ) : <p className="text-xs text-slate-500">安全候选生成后可查看结构。</p>
          ) : (
            selectedCandidate ? Object.entries(selectedCandidate.fields).map(([field, value]) => (
              <div className="mb-4" key={field}>
                <p className="mb-2 text-[11px] font-semibold text-cyan-200">{field}</p>
                <p className="mb-1 text-[10px] uppercase text-slate-600">Baseline</p>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap border border-rose-300/15 bg-rose-300/[0.04] p-3 text-[11px] leading-5 text-slate-400">{run.target.baseline_prompts[field]}</pre>
                <p className="mb-1 mt-3 text-[10px] uppercase text-slate-600">Candidate</p>
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap border border-emerald-300/15 bg-emerald-300/[0.04] p-3 text-[11px] leading-5 text-slate-200">{value}</pre>
              </div>
            )) : <p className="text-xs text-slate-500">候选生成后可查看差异。</p>
          )}
        </aside>
      </div>
    </div>
  );
}

function promptOptions(xpert: XpertDefinition) {
  return xpert.draft.workflow.nodes.flatMap((node) => {
    const data = node.data as Record<string, unknown>;
    const kind = String(data.kind ?? node.type ?? "");
    if (kind !== "workflow_agent") return [];
    const title = String(data.label ?? data.title ?? node.id);
    return (["rolePrompt", "promptSuffix"] as const).map((field) => ({
      key: `${node.id}.${field}`,
      label: `${title} · ${field}`,
      preview: String(data[field] ?? ""),
    }));
  });
}

function StructureScopePanel({
  capabilities,
  defaultAgentModelId,
  setDefaultAgentModelId,
  allowedNodeKinds,
  setAllowedNodeKinds,
  allowedOperations,
  setAllowedOperations,
  externalXpertIds,
  setExternalXpertIds,
  knowledgeBaseIds,
  setKnowledgeBaseIds,
  toolsetIds,
  setToolsetIds,
  pluginIds,
  setPluginIds,
  middlewareIds,
  setMiddlewareIds,
  maxOperations,
  setMaxOperations,
  maxAddedNodes,
  setMaxAddedNodes,
  maxRemovedNodes,
  setMaxRemovedNodes,
  maxModelCallIncrease,
  setMaxModelCallIncrease,
  maxTokenIncrease,
  setMaxTokenIncrease,
  maxLatencyIncrease,
  setMaxLatencyIncrease,
}: {
  capabilities: EvolutionCapabilities | null;
  defaultAgentModelId: string;
  setDefaultAgentModelId: (value: string) => void;
  allowedNodeKinds: string[];
  setAllowedNodeKinds: (value: string[]) => void;
  allowedOperations: string[];
  setAllowedOperations: (value: string[]) => void;
  externalXpertIds: string[];
  setExternalXpertIds: (value: string[]) => void;
  knowledgeBaseIds: string[];
  setKnowledgeBaseIds: (value: string[]) => void;
  toolsetIds: string[];
  setToolsetIds: (value: string[]) => void;
  pluginIds: string[];
  setPluginIds: (value: string[]) => void;
  middlewareIds: string[];
  setMiddlewareIds: (value: string[]) => void;
  maxOperations: number;
  setMaxOperations: (value: number) => void;
  maxAddedNodes: number;
  setMaxAddedNodes: (value: number) => void;
  maxRemovedNodes: number;
  setMaxRemovedNodes: (value: number) => void;
  maxModelCallIncrease: number;
  setMaxModelCallIncrease: (value: number) => void;
  maxTokenIncrease: number;
  setMaxTokenIncrease: (value: number) => void;
  maxLatencyIncrease: number;
  setMaxLatencyIncrease: (value: number) => void;
}) {
  const structure = capabilities?.structure;
  return (
    <div className="mt-4 space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <label className="block text-xs text-slate-400">
          新增 Agent 默认模型
          <select className="mt-1 w-full border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => setDefaultAgentModelId(event.target.value)} value={defaultAgentModelId}>
            {models.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        <div className="border border-cyan-300/20 bg-cyan-300/[0.05] p-3 text-xs leading-5 text-cyan-50">
          现有 Agent 的 Prompt、模型和输出契约保持不变。新增节点 ID、绑定 handle 与布局由编译器生成。
        </div>
      </div>

      <ScopeSelector
        items={(structure?.nodes ?? []).map((item) => ({
          id: item.kind ?? "",
          label: item.title ?? item.kind ?? "",
          description: item.description ?? "",
        }))}
        onChange={setAllowedNodeKinds}
        selected={allowedNodeKinds}
        title="可生成控制节点"
      />
      <ScopeSelector
        items={(structure?.operations ?? []).map((item) => ({
          id: item,
          label: mutationLabel(item),
          description: item,
        }))}
        onChange={setAllowedOperations}
        selected={allowedOperations}
        title="允许 Mutation"
      />
      <div className="grid gap-3 md:grid-cols-3">
        <NumberControl label="单候选操作数" max={8} min={1} onChange={setMaxOperations} value={maxOperations} />
        <NumberControl label="最多新增节点" max={4} min={0} onChange={setMaxAddedNodes} value={maxAddedNodes} />
        <NumberControl label="最多删除节点" max={4} min={0} onChange={setMaxRemovedNodes} value={maxRemovedNodes} />
      </div>

      <div className="border-t border-white/10 pt-4">
        <h3 className="text-xs font-semibold text-white">只读资源与安全中间件授权</h3>
        <p className="mt-1 text-[11px] text-slate-500">资源默认不授权。只有此处选中的 ID 才能进入候选。</p>
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          <ScopeSelector compact items={resourceItems(structure?.external_xperts)} onChange={setExternalXpertIds} selected={externalXpertIds} title="外部智能体" />
          <ScopeSelector compact items={resourceItems(structure?.knowledge_bases)} onChange={setKnowledgeBaseIds} selected={knowledgeBaseIds} title="知识库" />
          <ScopeSelector compact items={resourceItems(structure?.toolsets)} onChange={setToolsetIds} selected={toolsetIds} title="Toolset" />
          <ScopeSelector compact items={resourceItems(structure?.plugins)} onChange={setPluginIds} selected={pluginIds} title="Plugin" />
          <ScopeSelector compact items={resourceItems(structure?.middleware)} onChange={setMiddlewareIds} selected={middlewareIds} title="Evaluator-safe 中间件" />
        </div>
      </div>

      <div className="border-t border-white/10 pt-4">
        <h3 className="text-xs font-semibold text-white">成本上浮门禁</h3>
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          <RatioControl label="模型调用" onChange={setMaxModelCallIncrease} value={maxModelCallIncrease} />
          <RatioControl label="Estimated Tokens" onChange={setMaxTokenIncrease} value={maxTokenIncrease} />
          <RatioControl label="P95 延迟" onChange={setMaxLatencyIncrease} value={maxLatencyIncrease} />
        </div>
      </div>
    </div>
  );
}

function ScopeSelector({
  title,
  items,
  selected,
  onChange,
  compact = false,
}: {
  title: string;
  items: Array<{ id: string; label: string; description: string }>;
  selected: string[];
  onChange: (value: string[]) => void;
  compact?: boolean;
}) {
  return (
    <fieldset className="border border-white/10 bg-white/[0.02] p-3">
      <div className="flex items-center justify-between gap-3">
        <legend className="px-1 text-xs font-semibold text-slate-200">{title}</legend>
        {items.length ? (
          <button
            className="text-[10px] text-cyan-200 hover:text-cyan-100"
            onClick={() => onChange(selected.length === items.length ? [] : items.map((item) => item.id))}
            type="button"
          >
            {selected.length === items.length ? "清空" : "全选"}
          </button>
        ) : null}
      </div>
      <div className={`mt-2 ${compact ? "max-h-36" : "max-h-52"} space-y-1 overflow-auto`}>
        {items.map((item) => {
          const checked = selected.includes(item.id);
          return (
            <label className={`flex cursor-pointer items-start gap-2 p-2 ${checked ? "bg-cyan-300/10 text-white" : "text-slate-400 hover:bg-white/[0.04]"}`} key={item.id}>
              <input
                checked={checked}
                className="mt-0.5 accent-cyan-300"
                onChange={() => onChange(checked ? selected.filter((value) => value !== item.id) : [...selected, item.id])}
                type="checkbox"
              />
              <span className="min-w-0">
                <span className="block truncate text-xs">{item.label}</span>
                {item.description ? <span className="mt-0.5 block truncate text-[10px] text-slate-500">{item.description}</span> : null}
              </span>
            </label>
          );
        })}
        {!items.length ? <p className="p-2 text-[11px] text-slate-600">当前没有可授权资源</p> : null}
      </div>
    </fieldset>
  );
}

function NumberControl({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block text-xs text-slate-400">
      {label}
      <input className="mt-1 w-full border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" max={max} min={min} onChange={(event) => onChange(Number(event.target.value))} type="number" value={value} />
    </label>
  );
}

function RatioControl({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block text-xs text-slate-400">
      {label}：+{Math.round(value * 100)}%
      <input className="mt-2 w-full accent-cyan-300" max={3} min={0} onChange={(event) => onChange(Number(event.target.value))} step={0.25} type="range" value={value} />
    </label>
  );
}

function StructureGraphPreview({
  graph,
  loading,
}: {
  graph: CandidateGraph | null;
  loading: boolean;
}) {
  const diff = graph?.diff ?? {};
  const added = new Set((diff.added_nodes ?? []).map((item) => item.node_id));
  const replaced = new Set((diff.replaced_nodes ?? []).map((item) => item.node_id));
  const nodes: Node[] = (graph?.nodes ?? []).map((item) => ({
    id: item.id,
    position: item.position ?? { x: 0, y: 0 },
    data: {
      label: (
        <div className="min-w-28">
          <p className="truncate text-[11px] font-semibold">{String(item.data.title ?? item.data.label ?? item.type)}</p>
          <p className="mt-1 text-[9px] opacity-70">{item.type}</p>
        </div>
      ),
    },
    style: {
      background: added.has(item.id)
        ? "rgba(34, 211, 238, 0.18)"
        : replaced.has(item.id)
          ? "rgba(167, 139, 250, 0.18)"
          : "rgba(15, 23, 42, 0.96)",
      border: `1px solid ${added.has(item.id) ? "rgba(103, 232, 249, 0.75)" : replaced.has(item.id) ? "rgba(196, 181, 253, 0.75)" : "rgba(255,255,255,0.14)"}`,
      borderRadius: 6,
      color: "#e2e8f0",
      fontSize: 11,
      padding: 8,
      width: 150,
    },
  }));
  const edges: Edge[] = (graph?.edges ?? []).map((item) => ({
    id: item.id,
    source: item.source,
    target: item.target,
    sourceHandle: item.sourceHandle ?? undefined,
    targetHandle: item.targetHandle ?? undefined,
    animated: (diff.added_edge_ids ?? []).includes(item.id),
    style: {
      stroke: (diff.added_edge_ids ?? []).includes(item.id) ? "#67e8f9" : "#64748b",
    },
  }));
  return (
    <div className="h-[360px] overflow-hidden border border-white/10 bg-slate-950">
      {loading ? (
        <div className="flex h-full items-center justify-center text-xs text-slate-500"><LoaderCircle className="mr-2 h-4 w-4 animate-spin" />加载候选图</div>
      ) : graph ? (
        <ReactFlow
          edges={edges}
          elementsSelectable={false}
          fitView
          minZoom={0.35}
          nodes={nodes}
          nodesConnectable={false}
          nodesDraggable={false}
          proOptions={{ hideAttribution: true }}
        >
          <Background color="#334155" gap={18} size={1} />
          <Controls showInteractive={false} />
        </ReactFlow>
      ) : (
        <div className="flex h-full items-center justify-center text-xs text-slate-600">暂无候选图</div>
      )}
    </div>
  );
}

function StructureDiffSummary({ candidate }: { candidate: Candidate }) {
  const diff = candidate.diff ?? {};
  return (
    <div className="space-y-3 text-xs">
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-cyan-300/[0.07] p-2 text-cyan-100"><p className="text-[10px] text-cyan-200/70">新增节点</p><p className="mt-1 font-semibold">{diff.added_nodes?.length ?? 0}</p></div>
        <div className="bg-rose-300/[0.07] p-2 text-rose-100"><p className="text-[10px] text-rose-200/70">删除节点</p><p className="mt-1 font-semibold">{diff.removed_nodes?.length ?? 0}</p></div>
        <div className="bg-violet-300/[0.07] p-2 text-violet-100"><p className="text-[10px] text-violet-200/70">替换节点</p><p className="mt-1 font-semibold">{diff.replaced_nodes?.length ?? 0}</p></div>
      </div>
      <details className="border-t border-white/10 pt-3" open>
        <summary className="cursor-pointer font-medium text-slate-200">Mutation manifest</summary>
        <div className="mt-2 space-y-1">
          {(candidate.mutations ?? []).map((item, index) => (
            <p className="bg-white/[0.03] p-2 font-mono text-[10px] text-slate-400" key={`${candidate.candidate_id}-${index}`}>
              {index + 1}. {String(item.op ?? "mutation")}
            </p>
          ))}
        </div>
      </details>
      {diff.removed_nodes?.length ? (
        <div>
          <p className="mb-1 text-[10px] text-slate-500">已删除</p>
          {diff.removed_nodes.map((item) => <p className="truncate text-rose-100" key={item.node_id}>{item.title || item.node_id} ({item.kind})</p>)}
        </div>
      ) : null}
    </div>
  );
}

function resourceItems(items: StructureCapabilityItem[] | undefined) {
  return (items ?? []).map((item) => ({
    id: item.id ?? "",
    label: item.name ?? item.title ?? item.id ?? "",
    description: item.description ?? "",
  })).filter((item) => item.id);
}

function mutationLabel(operation: string) {
  const labels: Record<string, string> = {
    add_control_node: "新增控制节点",
    remove_control_node: "删除控制节点",
    replace_control_node: "替换控制节点",
    add_control_edge: "新增控制边",
    remove_control_edge: "删除控制边",
    bind_resource: "绑定资源",
    unbind_resource: "解绑资源",
    bind_middleware: "绑定中间件",
    unbind_middleware: "解绑中间件",
  };
  return labels[operation] ?? operation;
}
