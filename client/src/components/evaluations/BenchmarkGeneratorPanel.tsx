import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  LoaderCircle,
  Play,
  RefreshCw,
  Sparkles,
  Square,
} from "lucide-react";
import { models } from "../../data/models";
import { listXpertVersions, listXperts } from "../../utils/xpertApi";
import { type XpertSummary } from "../../types/xpert";

type TargetKind = "xpert_draft" | "xpert_version" | "proposal" | "prompt_profile";

interface PromptProfileSummary {
  id: string;
  name: string;
  draft_revision: number;
  status: string;
}

interface ProposalSummary {
  proposal_id: string;
  revision: number;
  title: string;
  kind: string;
  status: string;
}

interface VersionOption {
  key: string;
  xpert_id: string;
  version: number;
  label: string;
}

interface GenerationJob {
  job_id: string;
  status: string;
  dataset_id?: string | null;
  dataset_revision?: number | null;
  evaluation_run_id?: string | null;
  target?: { label?: string };
  generation?: {
    case_count?: number;
    repair_used?: boolean;
    assumptions?: string[];
    targeting?: TargetingSummary;
  };
  calibration?: {
    status?: string;
    baseline_score?: number;
    generic_counterfactual_score?: number | null;
    targeting_advantage?: number | null;
    easy_count?: number;
    hard_count?: number;
    warnings?: string[];
  };
  error?: string | null;
  created_at: number;
}

interface TargetAnchor {
  id: string;
  kind: string;
  axis?: string;
  label: string;
  summary: string;
  focus_terms?: string[];
  coverage: string[];
}

interface TargetingSummary {
  difficulty_counts?: Record<string, number>;
  target_ref_counts?: Record<string, number>;
  coverage_counts?: Record<string, number>;
  capability_matrix_counts?: Record<string, number>;
  combined_case_count?: number;
  combined_capabilities?: string[];
  focus_term_counts?: Record<string, number>;
  cases_with_focus?: number;
  pressure_type_counts?: Record<string, number>;
  discriminator_count?: number;
  blueprint_case_count?: number;
  normalized_case_count?: number;
  normalization_note_counts?: Record<string, number>;
  target_anchor_count?: number;
  missing_count?: number;
}

interface GeneratedCase {
  case_id: string;
  name: string;
  messages: Array<{ role: string; content: string }>;
  message: string;
  tags: string[];
  expected: Record<string, unknown>;
  targeting?: {
    blueprint_id?: string;
    difficulty: "basic" | "edge" | "adversarial";
    target_refs: string[];
    capability_matrix?: string[];
    focus_terms?: string[];
    pressure_types?: string[];
    rationale: string;
    challenge?: string;
    discriminator?: string;
    normalization_notes?: string[];
  };
}

interface GeneratedDatasetDetail {
  dataset_id: string;
  cases: GeneratedCase[];
  coverage?: {
    target_anchors?: TargetAnchor[];
  };
}

interface EvaluationRunDetail {
  targets?: Array<{ target_id: string; benchmark_counterfactual?: boolean }>;
  items?: Array<{
    target_id: string;
    case_id: string;
    status: string;
    score?: number;
    metrics?: Array<{ kind: string; score?: number; passed?: boolean }>;
    error?: string | null;
  }>;
}

interface PreflightResult {
  valid: boolean;
  target?: { label?: string; checksum?: string } | null;
  coverage: {
    available: string[];
    recommended: string[];
    reasons?: Record<string, string>;
  };
  target_anchors?: TargetAnchor[];
  targeting?: {
    focus_term_count?: number;
    focus_terms?: string[];
    domain_anchor_count?: number;
    resource_anchor_count?: number;
  };
  conversation_seed_count: number;
  warnings: string[];
  issues: Array<{ message: string }>;
}

interface Props {
  onDatasetReady: (datasetId: string) => Promise<void> | void;
}

const coverageLabels: Record<string, string> = {
  instruction_following: "指令遵循",
  structured_output: "结构输出",
  multi_turn: "多轮上下文",
  tool_routing: "工具路由",
  knowledge_citation: "知识引用",
  prompt_command: "Prompt Command",
};

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    throw new Error(
      typeof detail === "string"
        ? detail
        : detail?.issues?.map((item: { message: string }) => item.message).join("；")
          || `请求失败：${response.status}`,
    );
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

function statusTone(status: string) {
  if (["completed", "calibrated"].includes(status)) return "border-emerald-300/30 bg-emerald-300/10 text-emerald-100";
  if (["failed", "stale"].includes(status)) return "border-rose-300/30 bg-rose-300/10 text-rose-100";
  if (status === "cancelled") return "border-slate-400/20 bg-slate-400/10 text-slate-300";
  return "border-amber-300/30 bg-amber-300/10 text-amber-100";
}

function difficultyTone(difficulty?: string) {
  if (difficulty === "adversarial") return "border-rose-300/30 bg-rose-300/10 text-rose-100";
  if (difficulty === "edge") return "border-amber-300/30 bg-amber-300/10 text-amber-100";
  return "border-cyan-300/25 bg-cyan-300/10 text-cyan-100";
}

function expectationSummary(expected: Record<string, unknown>) {
  if (typeof expected.exact_answer === "string" && expected.exact_answer) {
    return `精确答案：${expected.exact_answer}`;
  }
  if (Array.isArray(expected.contains) && expected.contains.length) {
    return `必须包含：${expected.contains.join(" / ")}`;
  }
  if (expected.json_schema && typeof expected.json_schema === "object") {
    return `JSON Schema：${JSON.stringify(expected.json_schema)}`;
  }
  if (Array.isArray(expected.required_tools) && expected.required_tools.length) {
    return `必需工具：${expected.required_tools.join(" → ")}`;
  }
  if (Array.isArray(expected.forbidden_tools) && expected.forbidden_tools.length) {
    return `禁用工具：${expected.forbidden_tools.join("、")}`;
  }
  if (Array.isArray(expected.citation_ids) && expected.citation_ids.length) {
    return `预期引用：${expected.citation_ids.join("、")}`;
  }
  return "已配置确定性评分契约";
}

function calibrationWarningText(warning: string) {
  if (warning.includes("easy and the fixed target does not outperform")) {
    return "至少 80% 的样例对专业目标过易，且相对同模型通用对照的优势不足 10 个百分点；当前数据不能证明针对性。";
  }
  if (warning.includes("Target-specific advantage")) {
    return "专业目标相对同模型通用对照的优势不足 10 个百分点，请提高领域约束和判别难度。";
  }
  if (warning === "At least 80% of cases are very hard for the fixed baseline.") {
    return "固定基线在至少 80% 的样例上得分不高于 20%，这些样例实测过难，请核对 Gold 与评分契约。";
  }
  return warning;
}

export default function BenchmarkGeneratorPanel({ onDatasetReady }: Props) {
  const search = useMemo(() => new URLSearchParams(window.location.search), []);
  const [targetKind, setTargetKind] = useState<TargetKind>(
    (search.get("target_kind") as TargetKind) || "xpert_draft",
  );
  const [xperts, setXperts] = useState<XpertSummary[]>([]);
  const [versions, setVersions] = useState<VersionOption[]>([]);
  const [proposals, setProposals] = useState<ProposalSummary[]>([]);
  const [profiles, setProfiles] = useState<PromptProfileSummary[]>([]);
  const [selectedId, setSelectedId] = useState(
    search.get("target_id") || search.get("xpert_id") || "",
  );
  const [hostVersionKey, setHostVersionKey] = useState("");
  const [generatorModelId, setGeneratorModelId] = useState(models[0]?.id ?? "");
  const [caseCount, setCaseCount] = useState(12);
  const [coverage, setCoverage] = useState<string[]>([]);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [jobs, setJobs] = useState<GenerationJob[]>([]);
  const [activeJob, setActiveJob] = useState<GenerationJob | null>(null);
  const [datasetPreview, setDatasetPreview] = useState<GeneratedDatasetDetail | null>(null);
  const [calibrationRun, setCalibrationRun] = useState<EvaluationRunDetail | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const targetChoices = useMemo(() => {
    if (targetKind === "xpert_draft") {
      return xperts
        .filter((item) => item.status !== "archived")
        .map((item) => ({ value: item.id, label: `${item.name} · r${item.draft_revision}` }));
    }
    if (targetKind === "xpert_version") {
      return versions.map((item) => ({ value: item.key, label: item.label }));
    }
    if (targetKind === "proposal") {
      return proposals.map((item) => ({
        value: item.proposal_id,
        label: `${item.title} · r${item.revision}`,
      }));
    }
    return profiles.map((item) => ({
      value: item.id,
      label: `${item.name} · r${item.draft_revision}`,
    }));
  }, [profiles, proposals, targetKind, versions, xperts]);

  const targetAnchors = useMemo(
    () => datasetPreview?.coverage?.target_anchors ?? [],
    [datasetPreview],
  );
  const anchorsById = useMemo(
    () => new Map(targetAnchors.map((anchor) => [anchor.id, anchor])),
    [targetAnchors],
  );
  const calibrationByCase = useMemo(() => {
    const genericTargetId = calibrationRun?.targets?.find((item) => item.benchmark_counterfactual)?.target_id;
    const grouped = new Map<string, { specialist: Array<{ score: number; status: string; error?: string | null }>; generic: Array<{ score: number; status: string; error?: string | null }> }>();
    for (const item of calibrationRun?.items ?? []) {
      const values = grouped.get(item.case_id) ?? { specialist: [], generic: [] };
      const bucket = item.target_id === genericTargetId ? values.generic : values.specialist;
      bucket.push({ score: Number(item.score ?? 0), status: item.status, error: item.error });
      grouped.set(item.case_id, values);
    }
    return new Map(
      [...grouped.entries()].map(([caseId, values]) => [
        caseId,
        {
          score: values.specialist.length ? values.specialist.reduce((total, item) => total + item.score, 0) / values.specialist.length : 0,
          genericScore: values.generic.length ? values.generic.reduce((total, item) => total + item.score, 0) / values.generic.length : null,
          status: values.specialist.every((item) => item.status === "completed") ? "completed" : values.specialist[0]?.status,
          error: values.specialist.find((item) => item.error)?.error,
        },
      ]),
    );
  }, [calibrationRun]);

  useEffect(() => {
    void loadOptions();
    void loadJobs();
  }, []);

  useEffect(() => {
    if (!activeJob || ["completed", "failed", "cancelled"].includes(activeJob.status)) return;
    const timer = window.setInterval(() => void refreshJob(activeJob.job_id), 1500);
    return () => window.clearInterval(timer);
  }, [activeJob?.job_id, activeJob?.status]);

  useEffect(() => {
    let cancelled = false;
    setDatasetPreview(null);
    setCalibrationRun(null);
    setPreviewError("");
    if (!activeJob?.dataset_id) return () => { cancelled = true; };
    const loadPreview = async () => {
      try {
        const [dataset, run] = await Promise.all([
          requestJson<GeneratedDatasetDetail>(
            `/api/xpert-evaluations/datasets/${activeJob.dataset_id}`,
          ),
          activeJob.evaluation_run_id
            ? requestJson<EvaluationRunDetail>(
              `/api/xpert-evaluations/runs/${activeJob.evaluation_run_id}`,
            )
            : Promise.resolve(null),
        ]);
        if (!cancelled) {
          setDatasetPreview(dataset);
          setCalibrationRun(run);
        }
      } catch (caught) {
        if (!cancelled) {
          setPreviewError(caught instanceof Error ? caught.message : "样例证据加载失败。");
        }
      }
    };
    void loadPreview();
    return () => { cancelled = true; };
  }, [activeJob?.dataset_id, activeJob?.evaluation_run_id, activeJob?.status]);

  useEffect(() => {
    setPreflight(null);
    setCoverage([]);
    if (!selectedId && targetChoices[0]) setSelectedId(targetChoices[0].value);
  }, [targetKind, targetChoices, selectedId]);

  async function loadOptions() {
    setError("");
    try {
      const [xpertPayload, proposalPayload, profilePayload] = await Promise.all([
        listXperts({ status: "all", limit: 200 }),
        requestJson<{ items: ProposalSummary[] }>(
          "/api/runtime/authoring-proposals?status=pending&limit=200",
        ),
        requestJson<{ items: PromptProfileSummary[] }>("/api/prompt-profiles?limit=200"),
      ]);
      setXperts(xpertPayload.items);
      setProposals(
        proposalPayload.items.filter((item) =>
          ["xpert_create", "xpert_update"].includes(item.kind),
        ),
      );
      setProfiles(profilePayload.items.filter((item) => item.status !== "archived"));
      const groups = await Promise.all(
        xpertPayload.items
          .filter((item) => item.published_version)
          .map(async (xpert) => ({ xpert, versions: await listXpertVersions(xpert.id) })),
      );
      const nextVersions = groups.flatMap(({ xpert, versions: items }) =>
        items.map((version) => ({
          key: `${xpert.id}:${version.version}`,
          xpert_id: xpert.id,
          version: version.version,
          label: `${xpert.name} v${version.version}`,
        })),
      );
      setVersions(nextVersions);
      setHostVersionKey((current) => current || nextVersions[0]?.key || "");
      if (!selectedId) {
        const preferred =
          search.get("prompt_profile_id")
          || search.get("proposal_id")
          || search.get("xpert_id");
        setSelectedId(preferred || xpertPayload.items[0]?.id || "");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "生成目标加载失败。");
    }
  }

  async function loadJobs() {
    const payload = await requestJson<{ items: GenerationJob[] }>(
      "/api/benchmarks/generations?limit=30",
    );
    setJobs(payload.items);
    setActiveJob((current) => current ?? payload.items[0] ?? null);
  }

  function targetPayload() {
    if (targetKind === "xpert_draft") {
      const target = xperts.find((item) => item.id === selectedId);
      if (!target) throw new Error("请选择 Xpert 草稿。");
      return {
        kind: targetKind,
        xpert_id: target.id,
        draft_revision: target.draft_revision,
        label: `${target.name} draft`,
      };
    }
    if (targetKind === "xpert_version") {
      const target = versions.find((item) => item.key === selectedId);
      if (!target) throw new Error("请选择已发布 Xpert 版本。");
      return {
        kind: targetKind,
        xpert_id: target.xpert_id,
        version: target.version,
        label: target.label,
      };
    }
    if (targetKind === "proposal") {
      const target = proposals.find((item) => item.proposal_id === selectedId);
      if (!target) throw new Error("请选择 Authoring Proposal。");
      return {
        kind: targetKind,
        proposal_id: target.proposal_id,
        proposal_revision: target.revision,
        label: target.title,
      };
    }
    const profile = profiles.find((item) => item.id === selectedId);
    const host = versions.find((item) => item.key === hostVersionKey);
    if (!profile || !host) throw new Error("请选择 Prompt Profile 与固定宿主版本。");
    return {
      kind: targetKind,
      prompt_profile_id: profile.id,
      prompt_profile_revision: profile.draft_revision,
      host_xpert_id: host.xpert_id,
      host_xpert_version: host.version,
      label: `${profile.name} via ${host.label}`,
    };
  }

  async function analyze() {
    setBusy("preflight");
    setError("");
    try {
      const result = await requestJson<PreflightResult>(
        "/api/benchmarks/generations/preflight",
        postJson({ target: targetPayload(), coverage, conversation_selections: [] }),
      );
      setPreflight(result);
      if (!result.valid) throw new Error(result.issues.map((item) => item.message).join("；"));
      setCoverage((current) => current.length ? current : result.coverage.recommended);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "目标分析失败。");
    } finally {
      setBusy("");
    }
  }

  async function createGeneration() {
    setBusy("generate");
    setError("");
    try {
      const created = await requestJson<GenerationJob>(
        "/api/benchmarks/generations",
        postJson({
          target: targetPayload(),
          generator_model_id: generatorModelId,
          case_count: caseCount,
          locales: ["zh-CN", "en-US"],
          coverage,
          conversation_selections: [],
          seed: 0,
        }),
      );
      setActiveJob(created);
      setJobs((current) => [created, ...current]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "生成任务创建失败。");
    } finally {
      setBusy("");
    }
  }

  async function refreshJob(jobId: string) {
    try {
      const item = await requestJson<GenerationJob>(`/api/benchmarks/generations/${jobId}`);
      setActiveJob(item);
      setJobs((current) => [item, ...current.filter((job) => job.job_id !== item.job_id)]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "生成进度加载失败。");
    }
  }

  async function cancelJob() {
    if (!activeJob) return;
    const item = await requestJson<GenerationJob>(
      `/api/benchmarks/generations/${activeJob.job_id}/cancel`,
      { method: "POST" },
    );
    setActiveJob(item);
  }

  return (
    <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(0,1fr)_380px]">
      <section className="min-w-0 space-y-5">
        <div className="border-b border-white/10 pb-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            <Sparkles className="h-4 w-4 text-cyan-200" />针对目标生成评测集
          </div>
          <p className="mt-2 text-xs leading-5 text-slate-400">
            生成结果先进入草稿，并自动以固定目标运行一次校准。校准不会用当前回答改写 Gold。
          </p>
        </div>

        {error ? (
          <p className="rounded-md border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-xs text-rose-100">{error}</p>
        ) : null}

        <div className="grid gap-4 md:grid-cols-2">
          <label className="text-xs font-semibold text-slate-300">
            目标类型
            <select className="mt-1 h-10 w-full rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white" onChange={(event) => { setTargetKind(event.target.value as TargetKind); setSelectedId(""); }} value={targetKind}>
              <option value="xpert_draft">Xpert 草稿</option>
              <option value="xpert_version">已发布 Xpert 版本</option>
              <option value="proposal">Authoring Proposal</option>
              <option value="prompt_profile">Prompt Profile</option>
            </select>
          </label>
          <label className="text-xs font-semibold text-slate-300">
            评测对象
            <select className="mt-1 h-10 w-full rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white" onChange={(event) => { setSelectedId(event.target.value); setPreflight(null); setCoverage([]); }} value={selectedId}>
              <option value="">请选择</option>
              {targetChoices.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </label>
        </div>

        {targetKind === "prompt_profile" ? (
          <label className="block text-xs font-semibold text-slate-300">
            固定评测宿主 XpertVersion
            <select className="mt-1 h-10 w-full rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white" onChange={(event) => setHostVersionKey(event.target.value)} value={hostVersionKey}>
              {versions.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
            </select>
          </label>
        ) : null}

        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_180px]">
          <label className="text-xs font-semibold text-slate-300">
            生成模型
            <select className="mt-1 h-10 w-full rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white" onChange={(event) => setGeneratorModelId(event.target.value)} value={generatorModelId}>
              {models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
            </select>
          </label>
          <label className="text-xs font-semibold text-slate-300">
            用例数量
            <input className="mt-1 h-10 w-full rounded-md border border-white/10 bg-ink-950 px-3 text-sm text-white" max={30} min={6} onChange={(event) => setCaseCount(Number(event.target.value))} type="number" value={caseCount} />
          </label>
        </div>

        <div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs font-semibold text-slate-300">覆盖矩阵</span>
            <button className="inline-flex items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-xs text-slate-200" disabled={!selectedId || busy === "preflight"} onClick={() => void analyze()} type="button">
              {busy === "preflight" ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}分析目标
            </button>
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {(preflight?.coverage.available ?? Object.keys(coverageLabels)).map((item) => {
              const available = !preflight || preflight.coverage.available.includes(item);
              return (
                <label className={`flex items-center gap-2 rounded-md border px-3 py-2 text-xs ${available ? "border-white/10 bg-white/[0.03] text-slate-200" : "border-white/5 text-slate-600"}`} key={item}>
                  <input checked={coverage.includes(item)} disabled={!available} onChange={(event) => setCoverage((current) => event.target.checked ? [...current, item] : current.filter((value) => value !== item))} type="checkbox" />
                  {coverageLabels[item] ?? item}
                </label>
              );
            })}
          </div>
          {preflight?.target_anchors?.length ? (
            <div className="mt-4 border-t border-white/10 pt-4">
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs font-semibold text-slate-300">固定目标锚点</span>
                <span className="text-[11px] text-slate-500">生成样例必须逐条引用</span>
              </div>
              <div className="mt-2 grid gap-2 md:grid-cols-2">
                {preflight.target_anchors.map((anchor) => (
                  <div className="min-w-0 border-l-2 border-cyan-300/35 pl-3" key={anchor.id}>
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-xs font-semibold text-slate-200">{anchor.label}</span>
                      <span className="shrink-0 font-mono text-[9px] text-slate-500">{anchor.kind}</span>
                      {anchor.axis ? <span className="shrink-0 rounded border border-white/10 px-1.5 py-0.5 text-[9px] text-slate-500">{anchor.axis}</span> : null}
                    </div>
                    <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-slate-500">{anchor.summary}</p>
                    {anchor.focus_terms?.length ? (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {anchor.focus_terms.slice(0, 6).map((term) => (
                          <span className="rounded bg-cyan-300/10 px-1.5 py-0.5 text-[9px] text-cyan-100" key={`${anchor.id}-${term}`}>{term}</span>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>

        <button className="inline-flex w-full items-center justify-center gap-2 rounded-md bg-cyan-300 px-4 py-3 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-50" disabled={!selectedId || !generatorModelId || coverage.length === 0 || Boolean(busy)} onClick={() => void createGeneration()} type="button">
          {busy === "generate" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}生成并自动校准
        </button>

        {activeJob ? (
          <article className="border-t border-white/10 pt-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-white">{activeJob.target?.label || "生成任务"}</p>
                <p className="mt-1 font-mono text-[10px] text-slate-500">{activeJob.job_id}</p>
              </div>
              <span className={`rounded border px-2 py-1 text-[11px] ${statusTone(activeJob.status)}`}>{activeJob.status}</span>
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <div className="rounded-md border border-white/10 bg-white/[0.025] p-3 text-xs text-slate-400">生成用例<br /><strong className="text-slate-100">{activeJob.generation?.case_count ?? "-"}</strong></div>
              <div className="rounded-md border border-white/10 bg-white/[0.025] p-3 text-xs text-slate-400">专业目标<br /><strong className="text-slate-100">{activeJob.calibration?.baseline_score == null ? "-" : `${(activeJob.calibration.baseline_score * 100).toFixed(1)}%`}</strong></div>
              <div className="rounded-md border border-white/10 bg-white/[0.025] p-3 text-xs text-slate-400">通用对照<br /><strong className="text-slate-100">{activeJob.calibration?.generic_counterfactual_score == null ? "-" : `${(activeJob.calibration.generic_counterfactual_score * 100).toFixed(1)}%`}</strong></div>
              <div className="rounded-md border border-white/10 bg-white/[0.025] p-3 text-xs text-slate-400">针对性优势<br /><strong className="text-slate-100">{activeJob.calibration?.targeting_advantage == null ? "-" : `${(activeJob.calibration.targeting_advantage * 100).toFixed(1)} pp`}</strong></div>
            </div>
            {activeJob.calibration?.warnings?.length ? (
              <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-300/25 bg-amber-300/10 p-3 text-xs text-amber-100"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><span>{activeJob.calibration.warnings.map(calibrationWarningText).join("；")}</span></div>
            ) : null}
            {previewError ? (
              <p className="mt-3 rounded-md border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-xs text-rose-100">{previewError}</p>
            ) : null}
            {datasetPreview?.cases?.length ? (
              <section className="mt-5 border-t border-white/10 pt-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-white">逐例针对性证据</p>
                    <p className="mt-1 text-xs leading-5 text-slate-400">
                      目标锚点来自生成时固定的 Prompt、输出契约或资源配置；校准分数来自同一固定基线的实际执行。
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2 text-[10px]">
                    {Object.entries(activeJob.generation?.targeting?.difficulty_counts ?? {}).map(([difficulty, count]) => (
                      <span className={`rounded border px-2 py-1 ${difficultyTone(difficulty)}`} key={difficulty}>{difficulty} {count}</span>
                    ))}
                    <span className="rounded border border-cyan-300/25 px-2 py-1 text-cyan-100">
                      复合能力 {activeJob.generation?.targeting?.combined_case_count ?? 0}
                    </span>
                    <span className="rounded border border-emerald-300/25 px-2 py-1 text-emerald-100">
                      专业锚定 {activeJob.generation?.targeting?.cases_with_focus ?? 0}
                    </span>
                    <span className="rounded border border-violet-300/25 px-2 py-1 text-violet-100">
                      服务端蓝图 {activeJob.generation?.targeting?.blueprint_case_count ?? 0}
                    </span>
                  </div>
                </div>

                {datasetPreview.cases.some((item) => !item.targeting) ? (
                  <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-300/25 bg-amber-300/10 p-3 text-xs text-amber-100">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>这是旧契约生成的任务，部分样例没有可验证的目标锚点。请重新生成后再判断针对性。</span>
                  </div>
                ) : null}

                <div className="mt-4 divide-y divide-white/10 border-y border-white/10">
                  {datasetPreview.cases.map((item, index) => {
                    const targeting = item.targeting;
                    const result = calibrationByCase.get(item.case_id);
                    const caseCoverage = targeting?.capability_matrix?.length
                      ? targeting.capability_matrix
                      : item.tags.filter((tag) => Boolean(coverageLabels[tag])).slice(0, 1);
                    return (
                      <div className="py-4" key={item.case_id}>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-[10px] text-slate-600">#{index + 1}</span>
                          {targeting?.blueprint_id ? <span className="font-mono text-[9px] text-violet-200/80">{targeting.blueprint_id}</span> : null}
                          <span className="text-xs font-semibold text-slate-100">{item.name}</span>
                          {targeting ? <span className={`rounded border px-2 py-0.5 text-[10px] ${difficultyTone(targeting.difficulty)}`}>{targeting.difficulty}</span> : null}
                          {caseCoverage?.map((capability) => (
                            <span className="rounded border border-white/10 px-2 py-0.5 text-[10px] text-slate-400" key={`${item.case_id}-${capability}`}>{coverageLabels[capability] ?? capability}</span>
                          ))}
                          <span className={`ml-auto rounded border px-2 py-0.5 text-[10px] ${result?.score != null && result.score < 0.95 ? "border-cyan-300/25 text-cyan-100" : "border-amber-300/25 text-amber-100"}`}>
                            专业 {result?.status === "completed" ? `${(result.score * 100).toFixed(0)}%` : result?.status ?? "待校准"}{result?.genericScore == null ? "" : ` · 通用 ${(result.genericScore * 100).toFixed(0)}%`}
                          </span>
                        </div>
                        {item.messages?.length ? (
                          <div className="mt-3 border-l-2 border-white/10 pl-3">
                            <p className="text-[10px] font-semibold uppercase text-slate-500">输入上下文</p>
                            <div className="mt-1 space-y-1">
                              {item.messages.map((message, messageIndex) => (
                                <p className="text-[11px] leading-4 text-slate-500" key={`${item.case_id}-history-${messageIndex}`}>
                                  <span className="mr-2 font-mono text-slate-600">{message.role}</span>{message.content}
                                </p>
                              ))}
                            </div>
                          </div>
                        ) : null}
                        <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-300">{item.message}</p>
                        {targeting ? (
                          <div className="mt-3 grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
                            <div className="border-l-2 border-cyan-300/35 pl-3">
                              <p className="text-[10px] font-semibold uppercase text-cyan-100">验证目标</p>
                              <p className="mt-1 text-xs leading-5 text-slate-300">{targeting.rationale}</p>
                              {targeting.challenge ? <p className="mt-1 text-[11px] leading-4 text-slate-500">压力点：{targeting.challenge}</p> : null}
                              {targeting.discriminator ? <p className="mt-1 text-[11px] leading-4 text-cyan-100/75">区分证据：{targeting.discriminator}</p> : null}
                              {targeting.pressure_types?.length ? (
                                <div className="mt-2 flex flex-wrap gap-1">
                                  {targeting.pressure_types.map((pressure) => <span className="rounded border border-amber-300/20 px-1.5 py-0.5 text-[9px] text-amber-100" key={`${item.case_id}-${pressure}`}>{pressure}</span>)}
                                </div>
                              ) : null}
                              {targeting.focus_terms?.length ? (
                                <div className="mt-2 flex flex-wrap gap-1">
                                  {targeting.focus_terms.map((term) => <span className="rounded bg-emerald-300/10 px-1.5 py-0.5 text-[9px] text-emerald-100" key={`${item.case_id}-${term}`}>{term}</span>)}
                                </div>
                              ) : null}
                              {targeting.normalization_notes?.length ? (
                                <div className="mt-2 border-l-2 border-amber-300/30 pl-2 text-[10px] leading-4 text-amber-100/80">
                                  {targeting.normalization_notes.join("; ")}
                                </div>
                              ) : null}
                            </div>
                            <div className="space-y-2">
                              {targeting.target_refs.map((ref) => {
                                const anchor = anchorsById.get(ref);
                                return (
                                  <div className="min-w-0" key={ref}>
                                    <p className="truncate font-mono text-[10px] text-cyan-200">{anchor?.label ?? ref}</p>
                                    {anchor?.summary ? <p className="mt-0.5 line-clamp-2 text-[11px] leading-4 text-slate-500">{anchor.summary}</p> : null}
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        ) : null}
                        <p className="mt-3 text-[11px] leading-4 text-slate-500">Gold：{expectationSummary(item.expected)}</p>
                        {result?.error ? <p className="mt-1 text-[11px] text-rose-200">{result.error}</p> : null}
                      </div>
                    );
                  })}
                </div>
              </section>
            ) : null}
            {activeJob.status === "completed" && activeJob.dataset_id ? (
              <button className="mt-4 inline-flex items-center gap-2 rounded-md border border-emerald-300/25 bg-emerald-300/10 px-3 py-2 text-xs font-semibold text-emerald-100" onClick={() => void onDatasetReady(activeJob.dataset_id!)} type="button"><CheckCircle2 className="h-3.5 w-3.5" />打开评测集草稿</button>
            ) : null}
            {!(["completed", "failed", "cancelled"].includes(activeJob.status)) ? (
              <button className="mt-4 inline-flex items-center gap-2 rounded-md border border-rose-300/25 px-3 py-2 text-xs text-rose-100" onClick={() => void cancelJob()} type="button"><Square className="h-3.5 w-3.5" />取消任务</button>
            ) : null}
          </article>
        ) : null}
      </section>

      <aside className="min-w-0 border-l border-white/10 pl-5">
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm font-semibold text-white">最近生成</span>
          <button aria-label="刷新生成任务" className="grid h-8 w-8 place-items-center rounded-md border border-white/10 text-slate-300" onClick={() => void loadJobs()} title="刷新" type="button"><RefreshCw className="h-3.5 w-3.5" /></button>
        </div>
        <div className="mt-3 space-y-2">
          {jobs.map((job) => (
            <button className={`w-full rounded-md border p-3 text-left ${activeJob?.job_id === job.job_id ? "border-cyan-300/35 bg-cyan-300/10" : "border-white/10 bg-white/[0.025]"}`} key={job.job_id} onClick={() => { setActiveJob(job); if (!(["completed", "failed", "cancelled"].includes(job.status))) void refreshJob(job.job_id); }} type="button">
              <div className="flex items-center justify-between gap-2"><span className="truncate text-xs font-semibold text-slate-200">{job.target?.label || "待处理目标"}</span><span className={`rounded border px-1.5 py-0.5 text-[10px] ${statusTone(job.status)}`}>{job.status}</span></div>
              <p className="mt-2 text-[10px] text-slate-500">{new Date(job.created_at * 1000).toLocaleString("zh-CN", { hour12: false })}</p>
              {job.error ? <p className="mt-2 line-clamp-2 text-[11px] text-rose-200">{job.error}</p> : null}
            </button>
          ))}
          {!jobs.length ? <p className="rounded-md border border-dashed border-white/10 p-5 text-center text-xs text-slate-500">暂无生成任务</p> : null}
        </div>
      </aside>
    </div>
  );
}
