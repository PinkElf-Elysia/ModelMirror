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
}

interface Ranking {
  candidate_id: string;
  score: number;
  failed_count: number;
  estimated_tokens: number;
  average_latency_ms: number;
  metrics: Record<string, number>;
}

interface EvolutionRun {
  run_id: string;
  status: string;
  phase: string;
  target: {
    kind: "xpert" | "prompt_profile";
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
    generations: number;
    population_size: number;
    min_score_delta: number;
    max_metric_regression: number;
    model_policy: string;
  };
  train_case_ids: string[];
  validation_case_ids: string[];
  generations: Array<{
    generation: number;
    repair_used: boolean;
    candidates: Candidate[];
    ranking: Ranking[];
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

type TargetKind = "xpert" | "prompt_profile";

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
    searchParams.get("prompt_profile_id") ? "prompt_profile" : "xpert",
  );
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
  const [generations, setGenerations] = useState(2);
  const [populationSize, setPopulationSize] = useState(4);
  const [seed, setSeed] = useState(42);
  const [run, setRun] = useState<EvolutionRun | null>(null);
  const [runs, setRuns] = useState<EvolutionRun[]>([]);
  const [selectedCandidateId, setSelectedCandidateId] = useState("");
  const [preflight, setPreflight] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    document.title = "模镜 - Prompt 受控进化";
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
      .catch((caught) => setError(caught instanceof Error ? caught.message : "Xpert 加载失败"));
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
      const [xpertPayload, profilePayload, datasetPayload, runPayload] = await Promise.all([
        listXperts({ limit: 200 }),
        requestJson<{ items: PromptProfile[] }>("/api/prompt-profiles?limit=200"),
        requestJson<{ items: Dataset[] }>("/api/xpert-evaluations/datasets"),
        requestJson<{ items: EvolutionRun[] }>("/api/xpert-evolutions/runs?limit=50"),
      ]);
      setXperts(xpertPayload.items);
      setProfiles(profilePayload.items ?? []);
      setDatasets(datasetPayload.items ?? []);
      setRuns(runPayload.items ?? []);
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
    const target = targetKind === "xpert" ? selectedXpert : selectedProfile;
    if (!target) throw new Error("请选择进化目标");
    if (!datasetId || !datasetVersion) throw new Error("请选择已发布评测集版本");
    return {
      target_kind: targetKind,
      target_id: targetKind === "xpert" ? selectedXpert!.id : selectedProfile!.id,
      target_revision: targetKind === "xpert" ? selectedXpert!.draft_revision : selectedProfile!.draft_revision,
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
          <h1 className="mt-2 text-2xl font-semibold text-white">Prompt 受控进化</h1>
          <p className="mt-1 max-w-3xl text-sm text-slate-400">
            固定草稿与数据集，按同预算生成、评测和验证候选。通过非退化门禁后只创建待审批 Proposal。
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
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_340px]">
          <main className="space-y-5">
            <section className="border border-white/10 bg-ink-950/55 p-4">
              <div className="mb-4 flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-cyan-200" />
                <h2 className="text-sm font-semibold text-white">进化目标</h2>
              </div>
              <div className="mb-4 inline-flex border border-white/10 bg-black/20 p-1">
                {(["xpert", "prompt_profile"] as TargetKind[]).map((kind) => (
                  <button
                    className={`px-3 py-1.5 text-xs ${targetKind === kind ? "bg-cyan-300 text-slate-950" : "text-slate-400 hover:text-white"}`}
                    key={kind}
                    onClick={() => {
                      setTargetKind(kind);
                      setPreflight(null);
                    }}
                    type="button"
                  >
                    {kind === "xpert" ? "Xpert Agent Prompt" : "Prompt Profile"}
                  </button>
                ))}
              </div>
              {targetKind === "xpert" ? (
                <>
                  <label className="block text-xs text-slate-400">
                    Xpert 草稿
                    <select className="mt-1 w-full border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => setSelectedXpertId(event.target.value)} value={selectedXpertId}>
                      {xperts.map((item) => <option key={item.id} value={item.id}>{item.name} · r{item.draft_revision}</option>)}
                    </select>
                  </label>
                  <div className="mt-4">
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
                  </div>
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
                    固定评测宿主 Xpert
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
                  验证分至少提升 1%，且任一指标不得回退超过 2%。不允许新增超时、预算或安全错误。
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

          <aside className="border border-white/10 bg-ink-950/55 p-4">
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

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(320px,0.65fr)]">
        <main className="space-y-5">
          {run.generations.map((generation) => (
            <section className="border border-white/10 bg-ink-950/55 p-4" key={generation.generation}>
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-white">Generation {generation.generation}</h3>
                  <p className="mt-1 text-[11px] text-slate-500">{generation.candidates.length} 个安全候选{generation.repair_used ? " · 使用一次 JSON 修复" : ""}</p>
                </div>
                <TrendingUp className="h-4 w-4 text-cyan-200" />
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[680px] text-left text-xs">
                  <thead className="border-b border-white/10 text-slate-500">
                    <tr><th className="py-2">排名</th><th>候选</th><th>训练分</th><th>失败</th><th>Tokens</th><th>延迟</th><th></th></tr>
                  </thead>
                  <tbody>
                    {generation.ranking.map((ranking, index) => {
                      const candidate = generation.candidates.find((item) => item.candidate_id === ranking.candidate_id);
                      return (
                        <tr className="border-b border-white/[0.06] text-slate-300" key={ranking.candidate_id}>
                          <td className="py-2 text-slate-500">#{index + 1}</td>
                          <td><p className="font-medium text-white">{ranking.candidate_id}</p><p className="max-w-sm truncate text-[10px] text-slate-500">{candidate?.summary}</p></td>
                          <td>{score(ranking.score)}</td><td>{ranking.failed_count}</td><td>{ranking.estimated_tokens}</td><td>{Math.round(ranking.average_latency_ms)} ms</td>
                          <td><button className="text-cyan-200 hover:text-cyan-100" onClick={() => setSelectedCandidateId(ranking.candidate_id)} type="button">查看 diff</button></td>
                        </tr>
                      );
                    })}
                    {!generation.ranking.length ? <tr><td className="py-4 text-slate-500" colSpan={7}>正在生成或评测本代候选...</td></tr> : null}
                  </tbody>
                </table>
              </div>
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
                  </div>
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
            </section>
          ) : null}
          {run.error ? <div className="border border-rose-300/25 bg-rose-300/10 p-3 text-xs text-rose-100">{run.error}</div> : null}
        </main>

        <aside className="border border-white/10 bg-ink-950/55 p-4">
          <div className="mb-3 flex items-center gap-2">
            <GitCompareArrows className="h-4 w-4 text-violet-200" />
            <h3 className="text-sm font-semibold text-white">Prompt Diff</h3>
          </div>
          <select className="mb-4 w-full border border-white/10 bg-ink-950 px-3 py-2 text-xs text-white" onChange={(event) => setSelectedCandidateId(event.target.value)} value={selectedCandidateId || selectedCandidate?.candidate_id || ""}>
            {run.generations.flatMap((generation) => generation.candidates).map((candidate) => <option key={candidate.candidate_id} value={candidate.candidate_id}>{candidate.candidate_id}</option>)}
          </select>
          {selectedCandidate ? Object.entries(selectedCandidate.fields).map(([field, value]) => (
            <div className="mb-4" key={field}>
              <p className="mb-2 text-[11px] font-semibold text-cyan-200">{field}</p>
              <p className="mb-1 text-[10px] uppercase text-slate-600">Baseline</p>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap border border-rose-300/15 bg-rose-300/[0.04] p-3 text-[11px] leading-5 text-slate-400">{run.target.baseline_prompts[field]}</pre>
              <p className="mb-1 mt-3 text-[10px] uppercase text-slate-600">Candidate</p>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap border border-emerald-300/15 bg-emerald-300/[0.04] p-3 text-[11px] leading-5 text-slate-200">{value}</pre>
            </div>
          )) : <p className="text-xs text-slate-500">候选生成后可查看差异。</p>}
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
