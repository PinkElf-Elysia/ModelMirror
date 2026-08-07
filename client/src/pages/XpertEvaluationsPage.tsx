import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  AlertTriangle,
  BarChart3,
  Beaker,
  BookOpenCheck,
  CheckCircle2,
  Clock3,
  Database,
  FileUp,
  GitCompareArrows,
  LoaderCircle,
  Play,
  Plus,
  RefreshCw,
  Save,
  Square,
  XCircle,
} from "lucide-react";
import BenchmarkCatalogPanel from "../components/evaluations/BenchmarkCatalogPanel";
import PageContainer from "../components/PageContainer";
import { models } from "../data/models";
import { listXpertVersions, listXperts } from "../utils/xpertApi";

type TargetKind = "xpert_version" | "proposal";

interface EvaluationCase {
  case_id?: string;
  name?: string;
  message: string;
  messages?: Array<{ role: "system" | "user" | "assistant"; content: string }>;
  tags?: string[];
  expected?: {
    exact_answer?: string;
    contains?: string[];
    json_schema?: Record<string, unknown>;
    citation_ids?: string[];
    chunk_ids?: string[];
    document_names?: string[];
    rubric?: string;
  };
  weights?: Record<string, number>;
}

interface DatasetSummary {
  dataset_id: string;
  name: string;
  description: string;
  status: string;
  revision: number;
  published_version: number | null;
  case_count: number;
  version_count: number;
  origin?: "manual" | "catalog" | "generated" | string;
  catalog_ref?: {
    pack_id?: string;
    version?: number;
    checksum?: string;
  };
  updated_at: number;
}

interface DatasetDetail extends DatasetSummary {
  cases: EvaluationCase[];
}

interface DatasetVersion {
  dataset_id: string;
  version: number;
  case_count: number;
  checksum: string;
  published_at: number;
}

interface TargetOption {
  key: string;
  kind: TargetKind;
  label: string;
  xpert_id?: string;
  version?: number;
  proposal_id?: string;
  proposal_revision?: number;
}

interface EvaluationItem {
  item_id: string;
  target_id: string;
  target_label: string;
  case_id: string;
  repetition: number;
  status: string;
  output?: string;
  score?: number;
  metrics?: Array<{
    kind: string;
    score: number;
    passed: boolean;
    reason: string;
  }>;
  latency_ms?: number;
  error?: string | null;
  usage?: Record<string, number | boolean>;
}

interface EvaluationRun {
  run_id: string;
  status: string;
  dataset: {
    dataset_id: string;
    version: number;
    name: string;
    cases?: EvaluationCase[];
  };
  targets: Array<{
    target_id: string;
    label: string;
    source: Record<string, unknown>;
    stale?: boolean;
    warnings?: string[];
  }>;
  baseline_target_id?: string | null;
  config: {
    model_policy: "snapshot" | "override";
    override_model_id?: string | null;
    judge_model_id?: string | null;
    budget: Record<string, number>;
  };
  item_count: number;
  completed_item_count: number;
  items?: EvaluationItem[];
  warnings: string[];
  report?: {
    targets?: Array<{
      target_id: string;
      label: string;
      score: number;
      case_count: number;
      completed_count: number;
      failed_count: number;
      metrics: Record<string, number>;
      average_latency_ms: number;
      p95_latency_ms: number;
      model_calls: number;
      tool_calls: number;
      estimated_tokens: number;
    }>;
    comparisons?: Array<{
      target_id: string;
      baseline_target_id: string;
      score_delta: number;
      wins: number;
      ties: number;
      losses: number;
    }>;
  };
  created_at: number;
  completed_at?: number | null;
  error?: string | null;
}

interface AuthoringProposalSummary {
  proposal_id: string;
  revision: number;
  title: string;
  kind: string;
  status: string;
}

type EvaluationWorkspaceView = "catalog" | "datasets" | "reports";

const defaultCases: EvaluationCase[] = [
  {
    name: "基础回答",
    message: "请用一句话说明你的处理方案。",
    expected: {
      contains: ["方案"],
    },
  },
];

function readError(payload: unknown, fallback: string) {
  if (payload && typeof payload === "object") {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
    const error = (payload as { error?: unknown }).error;
    if (typeof error === "string") return error;
  }
  return fallback;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(readError(payload, `请求失败：${response.status}`));
  return payload as T;
}

function jsonInit(method: "POST" | "PATCH", body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

function formatTime(value?: number | null) {
  if (!value) return "-";
  return new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });
}

function percent(value?: number) {
  if (value == null || Number.isNaN(value)) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

function statusTone(status: string) {
  if (status === "completed") return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  if (status === "failed") return "border-rose-300/25 bg-rose-300/10 text-rose-100";
  if (status === "cancelled") return "border-slate-400/20 bg-slate-400/10 text-slate-300";
  if (status === "running") return "border-cyan-300/25 bg-cyan-300/10 text-cyan-100";
  return "border-amber-300/25 bg-amber-300/10 text-amber-100";
}

export default function XpertEvaluationsPage() {
  const { runId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const importRef = useRef<HTMLInputElement>(null);
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [dataset, setDataset] = useState<DatasetDetail | null>(null);
  const [datasetVersions, setDatasetVersions] = useState<DatasetVersion[]>([]);
  const [runs, setRuns] = useState<EvaluationRun[]>([]);
  const [run, setRun] = useState<EvaluationRun | null>(null);
  const [targetOptions, setTargetOptions] = useState<TargetOption[]>([]);
  const [selectedTargets, setSelectedTargets] = useState<string[]>([]);
  const [baselineKey, setBaselineKey] = useState("");
  const [datasetVersion, setDatasetVersion] = useState(0);
  const [newDatasetName, setNewDatasetName] = useState("");
  const [casesText, setCasesText] = useState(JSON.stringify(defaultCases, null, 2));
  const [modelPolicy, setModelPolicy] = useState<"snapshot" | "override">("snapshot");
  const [overrideModelId, setOverrideModelId] = useState(models[0]?.id ?? "");
  const [judgeModelId, setJudgeModelId] = useState(models[0]?.id ?? "");
  const [repetitions, setRepetitions] = useState(1);
  const [maxConcurrency, setMaxConcurrency] = useState(2);
  const [timeoutSeconds, setTimeoutSeconds] = useState(120);
  const [maxModelCalls, setMaxModelCalls] = useState(16);
  const [maxToolCalls, setMaxToolCalls] = useState(24);
  const [selectedItemId, setSelectedItemId] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [workspaceView, setWorkspaceView] = useState<EvaluationWorkspaceView>(
    runId ? "reports" : "catalog",
  );

  const selectedItem = useMemo(
    () => run?.items?.find((item) => item.item_id === selectedItemId) ?? run?.items?.[0] ?? null,
    [run, selectedItemId],
  );

  useEffect(() => {
    document.title = "模镜 - 智能体评测";
    void loadWorkspace();
  }, []);

  useEffect(() => {
    if (runId) {
      setWorkspaceView("reports");
      void loadRun(runId);
    }
  }, [runId]);

  useEffect(() => {
    if (!run || !["queued", "running"].includes(run.status)) return;
    const timer = window.setInterval(() => void loadRun(run.run_id, true), 1500);
    return () => window.clearInterval(timer);
  }, [run?.run_id, run?.status]);

  useEffect(() => {
    const proposalId = searchParams.get("proposal_id");
    const proposalRevision = Number(searchParams.get("proposal_revision") || 0);
    const xpertId = searchParams.get("xpert_id");
    const version = Number(searchParams.get("version") || 0);
    const key = proposalId && proposalRevision
      ? `proposal:${proposalId}:${proposalRevision}`
      : xpertId && version
        ? `xpert:${xpertId}:${version}`
        : "";
    if (key && targetOptions.some((item) => item.key === key)) {
      setSelectedTargets((current) => current.includes(key) ? current : [key, ...current]);
    }
  }, [searchParams, targetOptions]);

  async function loadWorkspace() {
    setError("");
    try {
      const [datasetPayload, runPayload, xperts, proposals] = await Promise.all([
        requestJson<{ items: DatasetSummary[] }>("/api/xpert-evaluations/datasets"),
        requestJson<{ items: EvaluationRun[] }>("/api/xpert-evaluations/runs?limit=50"),
        listXperts({ status: "published", limit: 100 }),
        requestJson<{ items: AuthoringProposalSummary[] }>(
          "/api/runtime/authoring-proposals?status=pending&source_type=meta_planner&limit=100",
        ).catch(() => ({ items: [] })),
      ]);
      setDatasets(datasetPayload.items ?? []);
      setRuns(runPayload.items ?? []);
      const versionGroups = await Promise.all(
        xperts.items
          .filter((item) => item.published_version)
          .map(async (item) => ({
            xpert: item,
            versions: await listXpertVersions(item.id),
          })),
      );
      const versionTargets = versionGroups.flatMap(({ xpert, versions }) =>
        versions.map((version) => ({
          key: `xpert:${xpert.id}:${version.version}`,
          kind: "xpert_version" as const,
          label: `${xpert.name} v${version.version}`,
          xpert_id: xpert.id,
          version: version.version,
        })),
      );
      const proposalTargets = (proposals.items ?? [])
        .filter((item) => ["xpert_create", "xpert_update"].includes(item.kind))
        .map((item) => ({
          key: `proposal:${item.proposal_id}:${item.revision}`,
          kind: "proposal" as const,
          label: `${item.title} · r${item.revision}`,
          proposal_id: item.proposal_id,
          proposal_revision: item.revision,
        }));
      setTargetOptions([...proposalTargets, ...versionTargets]);
      const firstDataset = datasetPayload.items?.[0];
      if (firstDataset) await selectDataset(firstDataset.dataset_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "评测工作台加载失败。");
    }
  }

  async function selectDataset(datasetId: string) {
    const [detail, versions] = await Promise.all([
      requestJson<DatasetDetail>(`/api/xpert-evaluations/datasets/${datasetId}`),
      requestJson<{ items: DatasetVersion[] }>(
        `/api/xpert-evaluations/datasets/${datasetId}/versions`,
      ),
    ]);
    setDataset(detail);
    setDatasetVersions(versions.items ?? []);
    setCasesText(JSON.stringify(detail.cases ?? [], null, 2));
    const preferred = detail.published_version ?? versions.items?.[0]?.version ?? 0;
    setDatasetVersion(preferred);
  }

  async function openInstantiatedDataset(item: {
    dataset_id: string;
    name: string;
    published_version: number;
  }) {
    await loadWorkspace();
    await selectDataset(item.dataset_id);
    setWorkspaceView("datasets");
    setNotice(`${item.name} 已加入工作区，并固定发布为 v${item.published_version}。`);
  }

  async function loadRun(id: string, silent = false) {
    if (!silent) setBusy("run");
    try {
      const detail = await requestJson<EvaluationRun>(`/api/xpert-evaluations/runs/${id}`);
      setRun(detail);
      setRuns((current) => [detail, ...current.filter((item) => item.run_id !== id)]);
      if (!selectedItemId && detail.items?.[0]) setSelectedItemId(detail.items[0].item_id);
    } catch (caught) {
      if (!silent) setError(caught instanceof Error ? caught.message : "评测运行加载失败。");
    } finally {
      if (!silent) setBusy("");
    }
  }

  async function createDataset() {
    if (!newDatasetName.trim()) return;
    setBusy("dataset-create");
    setError("");
    try {
      const created = await requestJson<DatasetDetail>(
        "/api/xpert-evaluations/datasets",
        jsonInit("POST", { name: newDatasetName.trim(), description: "" }),
      );
      setNewDatasetName("");
      setDatasets((current) => [created, ...current]);
      await selectDataset(created.dataset_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "数据集创建失败。");
    } finally {
      setBusy("");
    }
  }

  async function saveCases() {
    if (!dataset) return;
    setBusy("cases");
    setError("");
    try {
      const parsed = JSON.parse(casesText) as EvaluationCase[];
      if (!Array.isArray(parsed) || parsed.length === 0) {
        throw new Error("用例 JSON 必须是非空数组。");
      }
      const updated = await requestJson<DatasetDetail>(
        `/api/xpert-evaluations/datasets/${dataset.dataset_id}/cases`,
        jsonInit("POST", { revision: dataset.revision, cases: parsed, replace: true }),
      );
      setDataset(updated);
      setDatasets((current) =>
        current.map((item) => item.dataset_id === updated.dataset_id ? updated : item),
      );
      setCasesText(JSON.stringify(updated.cases ?? [], null, 2));
      setNotice("评测用例草稿已保存。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "评测用例保存失败。");
    } finally {
      setBusy("");
    }
  }

  async function publishDataset() {
    if (!dataset) return;
    setBusy("dataset-publish");
    setError("");
    try {
      const published = await requestJson<DatasetVersion>(
        `/api/xpert-evaluations/datasets/${dataset.dataset_id}/publish`,
        jsonInit("POST", { revision: dataset.revision, release_notes: "Evaluation dataset snapshot" }),
      );
      await selectDataset(dataset.dataset_id);
      setDatasetVersion(published.version);
      setNotice(`数据集 v${published.version} 已发布，后续草稿修改不会影响该版本。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "数据集发布失败。");
    } finally {
      setBusy("");
    }
  }

  async function importCases(file: File) {
    if (!dataset) return;
    setBusy("import");
    setError("");
    const form = new FormData();
    form.append("file", file);
    try {
      const updated = await requestJson<DatasetDetail>(
        `/api/xpert-evaluations/datasets/${dataset.dataset_id}/import?revision=${dataset.revision}`,
        { method: "POST", body: form },
      );
      setDataset(updated);
      setCasesText(JSON.stringify(updated.cases ?? [], null, 2));
      setNotice(`已导入 ${file.name}。`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "用例导入失败。");
    } finally {
      setBusy("");
      if (importRef.current) importRef.current.value = "";
    }
  }

  function targetPayload(key: string) {
    const option = targetOptions.find((item) => item.key === key);
    if (!option) throw new Error(`目标已失效：${key}`);
    return option.kind === "proposal"
      ? {
          kind: option.kind,
          proposal_id: option.proposal_id,
          proposal_revision: option.proposal_revision,
          label: option.label,
        }
      : {
          kind: option.kind,
          xpert_id: option.xpert_id,
          version: option.version,
          label: option.label,
        };
  }

  async function startRun() {
    if (!dataset || datasetVersion < 1) {
      setError("请先发布并选择一个数据集版本。");
      return;
    }
    if (selectedTargets.length === 0) {
      setError("请至少选择一个候选目标。");
      return;
    }
    setBusy("start");
    setError("");
    try {
      const payload = {
        dataset_id: dataset.dataset_id,
        dataset_version: datasetVersion,
        case_ids: [],
        baseline: baselineKey ? targetPayload(baselineKey) : null,
        candidates: selectedTargets.map(targetPayload),
        model_policy: modelPolicy,
        override_model_id: modelPolicy === "override" ? overrideModelId : null,
        judge_model_id: judgeModelId || null,
        seed: 0,
        budget: {
          repetitions,
          max_concurrency: maxConcurrency,
          case_timeout_seconds: timeoutSeconds,
          max_model_calls: maxModelCalls,
          max_tool_calls: maxToolCalls,
          max_estimated_tokens: 64_000,
          max_output_chars: 20_000,
        },
      };
      const preflight = await requestJson<{ valid: boolean; issues?: Array<{ message: string }> }>(
        "/api/xpert-evaluations/preflight",
        jsonInit("POST", {
          baseline: payload.baseline,
          candidates: payload.candidates,
          model_policy: payload.model_policy,
          override_model_id: payload.override_model_id,
        }),
      );
      if (!preflight.valid) {
        throw new Error(preflight.issues?.map((item) => item.message).join("；") || "只读安全预检未通过。");
      }
      const created = await requestJson<EvaluationRun>(
        "/api/xpert-evaluations/runs",
        jsonInit("POST", payload),
      );
      setRun(created);
      setRuns((current) => [created, ...current]);
      setWorkspaceView("reports");
      navigate(`/agents/evaluations/${created.run_id}`, { replace: true });
      setNotice("评测已排队，执行快照不会随草稿变化。");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "评测启动失败。");
    } finally {
      setBusy("");
    }
  }

  async function cancelRun() {
    if (!run) return;
    setBusy("cancel");
    try {
      const cancelled = await requestJson<EvaluationRun>(
        `/api/xpert-evaluations/runs/${run.run_id}/cancel`,
        { method: "POST" },
      );
      setRun(cancelled);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "取消评测失败。");
    } finally {
      setBusy("");
    }
  }

  return (
    <PageContainer activeResource="agents" hideSidebar maxWidthClassName="max-w-[1820px]">
      <header className="mb-5 flex flex-col gap-4 border-b border-white/10 pb-5 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <div className="flex items-center gap-2 text-xs font-semibold text-cyan-200">
            <Beaker className="h-4 w-4" />
            EVOAGENTX EVALUATOR
          </div>
          <h1 className="mt-2 text-2xl font-semibold text-white">智能体版本评测</h1>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-400">
            用不可变数据集和固定执行快照比较基线、已发布版本及 Meta Planner 候选。评测只生成报告，不修改草稿或发布状态。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link className="rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-xs font-semibold text-slate-200 hover:border-cyan-300/30" to="/agents/meta-agent">
            Meta Planner
          </Link>
          <button className="inline-flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-xs font-semibold text-slate-200 hover:border-cyan-300/30" onClick={() => void loadWorkspace()} type="button">
            <RefreshCw className="h-3.5 w-3.5" />刷新
          </button>
        </div>
      </header>

      {error || notice ? (
        <div className={`mb-4 rounded-md border px-3 py-2 text-sm ${error ? "border-rose-300/25 bg-rose-300/10 text-rose-100" : "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"}`}>
          {error || notice}
        </div>
      ) : null}

      <nav aria-label="评测工作台视图" className="mb-5 flex flex-wrap gap-1 border-b border-white/10" role="tablist">
        {([
          ["catalog", "标准基准", BookOpenCheck],
          ["datasets", "我的评测集", Database],
          ["reports", "运行报告", BarChart3],
        ] as const).map(([view, label, Icon]) => (
          <button
            aria-selected={workspaceView === view}
            className={`inline-flex items-center gap-2 border-b-2 px-4 py-3 text-sm font-semibold transition ${
              workspaceView === view
                ? "border-cyan-300 text-cyan-100"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
            key={view}
            onClick={() => setWorkspaceView(view)}
            role="tab"
            type="button"
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        ))}
      </nav>

      {workspaceView === "catalog" ? (
        <BenchmarkCatalogPanel onInstantiated={openInstantiatedDataset} />
      ) : workspaceView === "reports" ? (
        <section className="grid min-w-0 gap-5 lg:grid-cols-[340px_minmax(0,1fr)]">
          <aside className="min-w-0 border-r border-white/10 pr-5">
            <div className="flex items-center gap-2 text-sm font-semibold text-white">
              <Clock3 className="h-4 w-4 text-cyan-200" />运行记录
            </div>
            <div className="mt-4 max-h-[560px] space-y-2 overflow-y-auto pr-1">
              {runs.map((item) => (
                <button
                  className={`w-full rounded-md border p-3 text-left transition ${
                    run?.run_id === item.run_id
                      ? "border-cyan-300/35 bg-cyan-300/10"
                      : "border-white/10 bg-white/[0.025] hover:border-white/20"
                  }`}
                  key={item.run_id}
                  onClick={() => {
                    navigate(`/agents/evaluations/${item.run_id}`);
                    void loadRun(item.run_id);
                  }}
                  type="button"
                >
                  <div className="flex items-center justify-between gap-3">
                    <span className="truncate text-xs font-semibold text-slate-200">
                      {item.dataset?.name ?? "Evaluation"}
                    </span>
                    <span className={`rounded border px-1.5 py-0.5 text-[10px] ${statusTone(item.status)}`}>
                      {item.status}
                    </span>
                  </div>
                  <p className="mt-2 text-[10px] text-slate-500">{formatTime(item.created_at)}</p>
                </button>
              ))}
              {runs.length === 0 ? (
                <p className="rounded-md border border-dashed border-white/10 p-4 text-xs text-slate-500">
                  暂无评测运行。
                </p>
              ) : null}
            </div>
          </aside>
          <div className="grid min-h-[280px] place-items-center rounded-md border border-dashed border-white/10 px-6 text-center text-sm leading-6 text-slate-500">
            {run
              ? "已选择运行。完整指标、基线对比和逐样例结果显示在下方。"
              : "选择一条运行记录查看完整评测报告。"}
          </div>
        </section>
      ) : (
      <div className="grid min-w-0 gap-5 2xl:grid-cols-[280px_minmax(0,1fr)_minmax(420px,0.9fr)]">
        <aside className="min-w-0 border-r border-white/10 pr-5">
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            <Database className="h-4 w-4 text-cyan-200" />评测集
          </div>
          <div className="mt-3 flex gap-2">
            <input className="min-w-0 flex-1 rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-cyan-300/40" onChange={(event) => setNewDatasetName(event.target.value)} placeholder="新数据集名称" value={newDatasetName} />
            <button aria-label="创建数据集" className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-cyan-300 text-ink-950 disabled:opacity-50" disabled={!newDatasetName.trim() || Boolean(busy)} onClick={() => void createDataset()} title="创建数据集" type="button">
              <Plus className="h-4 w-4" />
            </button>
          </div>
          <div className="mt-4 space-y-2">
            {datasets.map((item) => (
              <button className={`w-full rounded-md border p-3 text-left transition ${dataset?.dataset_id === item.dataset_id ? "border-cyan-300/35 bg-cyan-300/10" : "border-white/10 bg-white/[0.025] hover:border-white/20"}`} key={item.dataset_id} onClick={() => void selectDataset(item.dataset_id)} type="button">
                <div className="flex items-start justify-between gap-2">
                  <span className="truncate text-sm font-semibold text-slate-100">{item.name}</span>
                  <span className="flex shrink-0 items-center gap-1.5 text-[10px] text-slate-500">
                    {item.origin === "catalog" ? <span className="text-cyan-200">标准</span> : null}
                    r{item.revision}
                  </span>
                </div>
                <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
                  <span>{item.case_count} 用例</span>
                  <span>{item.published_version ? `v${item.published_version}` : "未发布"}</span>
                </div>
              </button>
            ))}
          </div>
          <div className="mt-6 border-t border-white/10 pt-4">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-white">
              <Clock3 className="h-4 w-4 text-slate-400" />最近运行
            </div>
            <div className="space-y-2">
              {runs.slice(0, 10).map((item) => (
                <Link className="block rounded-md border border-white/10 bg-white/[0.025] p-3 hover:border-white/20" key={item.run_id} to={`/agents/evaluations/${item.run_id}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-xs font-semibold text-slate-200">{item.dataset?.name ?? "Evaluation"}</span>
                    <span className={`rounded border px-1.5 py-0.5 text-[10px] ${statusTone(item.status)}`}>{item.status}</span>
                  </div>
                  <p className="mt-1 text-[10px] text-slate-500">{formatTime(item.created_at)}</p>
                </Link>
              ))}
            </div>
          </div>
        </aside>

        <section className="min-w-0 space-y-5">
          <div className="border-b border-white/10 pb-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold text-white">{dataset?.name ?? "选择评测集"}</h2>
                <p className="mt-1 text-xs text-slate-500">草稿 revision 与发布版本相互隔离。</p>
              </div>
              {dataset ? (
                <div className="flex gap-2">
                  <input accept=".json,.csv" className="hidden" onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) void importCases(file);
                  }} ref={importRef} type="file" />
                  <button className="inline-flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-xs text-slate-200" onClick={() => importRef.current?.click()} type="button">
                    <FileUp className="h-3.5 w-3.5" />JSON / CSV
                  </button>
                  <button className="inline-flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-xs text-slate-200" disabled={Boolean(busy)} onClick={() => void saveCases()} type="button">
                    <Save className="h-3.5 w-3.5" />保存草稿
                  </button>
                  <button className="inline-flex items-center gap-2 rounded-md bg-cyan-300 px-3 py-2 text-xs font-semibold text-ink-950 disabled:opacity-50" disabled={!dataset.case_count || Boolean(busy)} onClick={() => void publishDataset()} type="button">
                    <CheckCircle2 className="h-3.5 w-3.5" />发布版本
                  </button>
                </div>
              ) : null}
            </div>
          </div>

          {dataset ? (
            <>
              <textarea className="min-h-[380px] w-full resize-y rounded-md border border-white/10 bg-ink-950/70 p-4 font-mono text-xs leading-6 text-slate-200 outline-none focus:border-cyan-300/40" onChange={(event) => setCasesText(event.target.value)} spellCheck={false} value={casesText} />
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-xs font-semibold text-slate-300">
                  固定数据集版本
                  <select className="mt-1 h-10 w-full rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white" onChange={(event) => setDatasetVersion(Number(event.target.value))} value={datasetVersion}>
                    <option value={0}>请选择已发布版本</option>
                    {datasetVersions.map((item) => <option key={item.version} value={item.version}>v{item.version} · {item.case_count} 用例</option>)}
                  </select>
                </label>
                <div className="rounded-md border border-white/10 bg-white/[0.025] p-3 text-xs leading-5 text-slate-400">
                  运行创建后会固定 Dataset、智能体、Proposal revision、知识索引和资源版本。外部 Provider 响应可能变化，报告会明确标记该限制。
                </div>
              </div>
            </>
          ) : (
            <div className="grid min-h-[440px] place-items-center rounded-md border border-dashed border-white/10 text-sm text-slate-500">创建或选择一个评测集。</div>
          )}
        </section>

        <aside className="min-w-0 space-y-5 border-l border-white/10 pl-5">
          <section>
            <div className="flex items-center gap-2 text-sm font-semibold text-white">
              <GitCompareArrows className="h-4 w-4 text-cyan-200" />基线与候选
            </div>
            <label className="mt-3 block text-xs font-semibold text-slate-300">
              基线（可选）
              <select className="mt-1 h-10 w-full rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white" onChange={(event) => setBaselineKey(event.target.value)} value={baselineKey}>
                <option value="">无基线，仅比较候选</option>
                {targetOptions.filter((item) => item.kind === "xpert_version").map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
              </select>
            </label>
            <div className="mt-3 max-h-56 space-y-2 overflow-y-auto pr-1">
              {targetOptions.map((item) => (
                <label className="flex cursor-pointer items-start gap-3 rounded-md border border-white/10 bg-white/[0.025] p-3" key={item.key}>
                  <input checked={selectedTargets.includes(item.key)} className="mt-0.5 h-4 w-4 accent-cyan-300" onChange={(event) => setSelectedTargets((current) => event.target.checked ? [...current, item.key].slice(0, 5) : current.filter((key) => key !== item.key))} type="checkbox" />
                  <span className="min-w-0">
                    <span className="block truncate text-xs font-semibold text-slate-200">{item.label}</span>
                    <span className="mt-1 block text-[10px] uppercase tracking-wide text-slate-500">{item.kind === "proposal" ? "Meta Planner Proposal" : "已发布智能体"}</span>
                  </span>
                </label>
              ))}
            </div>
          </section>

          <section className="border-t border-white/10 pt-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="text-xs font-semibold text-slate-300">
                模型策略
                <select className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2 text-xs text-white" onChange={(event) => setModelPolicy(event.target.value as "snapshot" | "override")} value={modelPolicy}>
                  <option value="snapshot">固定快照模型</option>
                  <option value="override">统一替换模型</option>
                </select>
              </label>
              <label className="text-xs font-semibold text-slate-300">
                重复次数
                <input className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2 text-xs text-white" max={3} min={1} onChange={(event) => setRepetitions(Number(event.target.value))} type="number" value={repetitions} />
              </label>
            </div>
            {modelPolicy === "override" ? (
              <label className="mt-3 block text-xs font-semibold text-slate-300">
                统一模型
                <select className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2 text-xs text-white" onChange={(event) => setOverrideModelId(event.target.value)} value={overrideModelId}>
                  {models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
                </select>
              </label>
            ) : null}
            <label className="mt-3 block text-xs font-semibold text-slate-300">
              Rubric Judge 模型
              <select className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2 text-xs text-white" onChange={(event) => setJudgeModelId(event.target.value)} value={judgeModelId}>
                <option value="">不启用 Judge</option>
                {models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
              </select>
            </label>
            <div className="mt-3 grid grid-cols-2 gap-3">
              {[
                ["并发", maxConcurrency, setMaxConcurrency, 1, 4],
                ["超时（秒）", timeoutSeconds, setTimeoutSeconds, 10, 600],
                ["模型调用", maxModelCalls, setMaxModelCalls, 1, 64],
                ["工具调用", maxToolCalls, setMaxToolCalls, 0, 100],
              ].map(([label, value, setter, min, max]) => (
                <label className="text-[11px] font-semibold text-slate-400" key={String(label)}>
                  {String(label)}
                  <input className="mt-1 h-9 w-full rounded-md border border-white/10 bg-ink-950 px-2 text-xs text-white" max={Number(max)} min={Number(min)} onChange={(event) => (setter as (value: number) => void)(Number(event.target.value))} type="number" value={Number(value)} />
                </label>
              ))}
            </div>
            <button className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-md bg-cyan-300 px-4 py-3 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-50" disabled={busy === "start" || !datasetVersion || selectedTargets.length === 0} onClick={() => void startRun()} type="button">
              {busy === "start" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
              安全预检并运行
            </button>
          </section>
        </aside>
      </div>
      )}

      {workspaceView === "reports" && run ? (
        <section className="mt-7 border-t border-white/10 pt-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <BarChart3 className="h-5 w-5 text-cyan-200" />
                <h2 className="text-lg font-semibold text-white">评测报告</h2>
                <span className={`rounded border px-2 py-0.5 text-[11px] ${statusTone(run.status)}`}>{run.status}</span>
              </div>
              <p className="mt-1 text-xs text-slate-500">{run.run_id} · {run.completed_item_count}/{run.item_count} 项</p>
            </div>
            <div className="flex gap-2">
              {["queued", "running"].includes(run.status) ? (
                <button className="inline-flex items-center gap-2 rounded-md border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-xs font-semibold text-rose-100" onClick={() => void cancelRun()} type="button">
                  <Square className="h-3.5 w-3.5" />取消
                </button>
              ) : null}
              <button className="inline-flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.04] px-3 py-2 text-xs text-slate-200" onClick={() => void loadRun(run.run_id)} type="button">
                <RefreshCw className="h-3.5 w-3.5" />刷新报告
              </button>
            </div>
          </div>

          {run.targets.some((target) => target.stale) ? (
            <div className="mt-4 flex items-start gap-2 rounded-md border border-amber-300/25 bg-amber-300/10 p-3 text-xs leading-5 text-amber-100">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              Proposal revision 已变化。本报告仍准确描述原固定快照，但不代表当前候选。
            </div>
          ) : null}

          <div className="mt-5 grid gap-4 lg:grid-cols-3">
            {(run.report?.targets ?? []).map((target) => (
              <article className="rounded-md border border-white/10 bg-white/[0.025] p-4" key={target.target_id}>
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <h3 className="text-sm font-semibold text-white">{target.label}</h3>
                    <p className="mt-1 text-[11px] text-slate-500">{target.completed_count} 完成 · {target.failed_count} 失败</p>
                  </div>
                  <span className="text-xl font-semibold text-cyan-100">{percent(target.score)}</span>
                </div>
                <div className="mt-4 grid grid-cols-2 gap-2 text-[11px]">
                  <div className="rounded bg-white/[0.035] p-2 text-slate-400">平均延迟<br /><strong className="text-slate-200">{Math.round(target.average_latency_ms)} ms</strong></div>
                  <div className="rounded bg-white/[0.035] p-2 text-slate-400">P95<br /><strong className="text-slate-200">{Math.round(target.p95_latency_ms)} ms</strong></div>
                  <div className="rounded bg-white/[0.035] p-2 text-slate-400">模型 / 工具<br /><strong className="text-slate-200">{target.model_calls} / {target.tool_calls}</strong></div>
                  <div className="rounded bg-white/[0.035] p-2 text-slate-400">估算 Token<br /><strong className="text-slate-200">{target.estimated_tokens}</strong></div>
                </div>
                <div className="mt-3 space-y-1">
                  {Object.entries(target.metrics).map(([name, score]) => (
                    <div className="flex items-center justify-between text-[11px]" key={name}>
                      <span className="text-slate-400">{name}</span>
                      <span className="font-semibold text-slate-200">{percent(score)}</span>
                    </div>
                  ))}
                </div>
              </article>
            ))}
          </div>

          {(run.report?.comparisons ?? []).length > 0 ? (
            <div className="mt-4 flex flex-wrap gap-3">
              {run.report?.comparisons?.map((item) => (
                <div className="rounded-md border border-white/10 bg-white/[0.025] px-3 py-2 text-xs text-slate-300" key={item.target_id}>
                  Δ <strong className={item.score_delta >= 0 ? "text-emerald-200" : "text-rose-200"}>{item.score_delta >= 0 ? "+" : ""}{percent(item.score_delta)}</strong>
                  <span className="ml-3 text-slate-500">胜 {item.wins} · 平 {item.ties} · 负 {item.losses}</span>
                </div>
              ))}
            </div>
          ) : null}

          <div className="mt-5 grid min-w-0 gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
            <div className="max-h-[540px] space-y-2 overflow-y-auto pr-1">
              {(run.items ?? []).map((item) => (
                <button className={`w-full rounded-md border p-3 text-left ${selectedItem?.item_id === item.item_id ? "border-cyan-300/35 bg-cyan-300/10" : "border-white/10 bg-white/[0.025]"}`} key={item.item_id} onClick={() => setSelectedItemId(item.item_id)} type="button">
                  <div className="flex items-center justify-between gap-3">
                    <span className="truncate text-xs font-semibold text-slate-200">{item.target_label}</span>
                    {item.status === "completed" ? <CheckCircle2 className="h-4 w-4 text-emerald-300" /> : item.status === "failed" ? <XCircle className="h-4 w-4 text-rose-300" /> : <LoaderCircle className="h-4 w-4 animate-spin text-cyan-300" />}
                  </div>
                  <div className="mt-2 flex items-center justify-between text-[10px] text-slate-500">
                    <span>{item.case_id} · #{item.repetition}</span>
                    <span>{item.score == null ? "-" : percent(item.score)}</span>
                  </div>
                </button>
              ))}
            </div>
            <div className="min-w-0 rounded-md border border-white/10 bg-ink-950/55 p-4">
              {selectedItem ? (
                <>
                  <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-3">
                    <div>
                      <h3 className="text-sm font-semibold text-white">{selectedItem.target_label}</h3>
                      <p className="mt-1 text-[11px] text-slate-500">{selectedItem.case_id} · {Math.round(selectedItem.latency_ms ?? 0)} ms</p>
                    </div>
                    <span className={`rounded border px-2 py-0.5 text-[11px] ${statusTone(selectedItem.status)}`}>{selectedItem.status}</span>
                  </div>
                  {selectedItem.error ? <p className="mt-3 rounded border border-rose-300/20 bg-rose-300/10 p-3 text-xs text-rose-100">{selectedItem.error}</p> : null}
                  <pre className="mt-4 max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-black/20 p-3 text-xs leading-6 text-slate-200">{selectedItem.output || "暂无最终输出。"}</pre>
                  <div className="mt-4 space-y-2">
                    {(selectedItem.metrics ?? []).map((metricItem) => (
                      <div className="rounded-md border border-white/10 bg-white/[0.025] p-3" key={metricItem.kind}>
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-semibold text-slate-200">{metricItem.kind}</span>
                          <span className={metricItem.passed ? "text-xs font-semibold text-emerald-200" : "text-xs font-semibold text-rose-200"}>{percent(metricItem.score)}</span>
                        </div>
                        <p className="mt-1 text-[11px] leading-5 text-slate-500">{metricItem.reason}</p>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <div className="grid min-h-64 place-items-center text-sm text-slate-500">运行后选择一个样例查看结果。</div>
              )}
            </div>
          </div>
        </section>
      ) : null}
    </PageContainer>
  );
}
