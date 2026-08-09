import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, LoaderCircle, Play, RefreshCw, Square } from "lucide-react";
import { models } from "../../data/models";

interface DocumentOption { id: string; filename: string }
interface VersionOption { version_id: string; version: number; status: string; active: boolean }
interface Preflight {
  valid: boolean;
  target?: { label?: string; checksum?: string } | null;
  coverage: { available: string[]; recommended: string[]; selected?: string[] };
  sampling?: {
    document_count: number;
    stable_evidence_count: number;
    sampled_evidence_count: number;
    estimated_context_chars: number;
    max_context_chars: number;
  };
  warnings: string[];
  issues: Array<{ message: string }>;
}
interface GenerationJob {
  job_id: string;
  status: string;
  dataset_id?: string | null;
  dataset_revision?: number | null;
  target?: { label?: string };
  generation?: { case_count?: number; repair_used?: boolean };
  calibration?: {
    status?: string;
    reason?: string;
    counts?: Record<string, number>;
  };
  error?: string | null;
}

const coverageLabels: Record<string, string> = {
  factual_lookup: "事实定位",
  paraphrase: "同义改写",
  section_context: "章节上下文",
  cross_language: "跨语言",
  multi_evidence: "多证据",
  confusable_content: "易混淆内容",
};

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === "string"
      ? detail
      : detail?.issues?.map((item: { message: string }) => item.message).join("；");
    throw new Error(message || `请求失败：${response.status}`);
  }
  return payload as T;
}

function postJson(value: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  };
}

export default function KnowledgeBenchmarkGenerator({
  kbId,
  versions,
  documents,
  onDatasetReady,
}: {
  kbId: string;
  versions: VersionOption[];
  documents: DocumentOption[];
  onDatasetReady: (evalSetId: string) => Promise<void> | void;
}) {
  const activeVersion = useMemo(
    () => versions.find((item) => item.active) ?? versions[0],
    [versions],
  );
  const [versionId, setVersionId] = useState("");
  const [documentIds, setDocumentIds] = useState<string[]>([]);
  const [generatorModelId, setGeneratorModelId] = useState(models[0]?.id ?? "");
  const [caseCount, setCaseCount] = useState(12);
  const [noResultCount, setNoResultCount] = useState(0);
  const [seed, setSeed] = useState(0);
  const [locales, setLocales] = useState<string[]>(["zh-CN", "en-US"]);
  const [coverage, setCoverage] = useState<string[]>([]);
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (!versionId && activeVersion) setVersionId(activeVersion.version_id);
  }, [activeVersion, versionId]);

  useEffect(() => {
    if (!job || ["completed", "failed", "cancelled"].includes(job.status)) return;
    const timer = window.setInterval(() => void refreshJob(job.job_id), 900);
    return () => window.clearInterval(timer);
  }, [job?.job_id, job?.status]);

  useEffect(() => {
    if (job?.status === "completed" && job.dataset_id) {
      void onDatasetReady(job.dataset_id);
    }
  }, [job?.status, job?.dataset_id]);

  function target() {
    return {
      kind: "knowledge_version",
      kb_id: kbId,
      pipeline_version_id: versionId,
      document_ids: documentIds,
    };
  }

  async function analyze() {
    setBusy("preflight");
    setError("");
    try {
      const result = await requestJson<Preflight>(
        "/api/benchmarks/generations/preflight",
        postJson({ target: target(), coverage, locales, conversation_selections: [] }),
      );
      setPreflight(result);
      if (!result.valid) throw new Error(result.issues.map((item) => item.message).join("；"));
      if (!coverage.length) setCoverage(result.coverage.recommended);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "知识版本分析失败。");
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
          target: target(),
          generator_model_id: generatorModelId,
          case_count: caseCount,
          no_result_count: noResultCount,
          locales,
          coverage,
          conversation_selections: [],
          seed,
        }),
      );
      setJob(created);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "生成任务创建失败。");
    } finally {
      setBusy("");
    }
  }

  async function refreshJob(jobId: string) {
    try {
      setJob(await requestJson<GenerationJob>(`/api/benchmarks/generations/${jobId}`));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "生成进度加载失败。");
    }
  }

  async function cancelJob() {
    if (!job) return;
    setJob(await requestJson<GenerationJob>(
      `/api/benchmarks/generations/${job.job_id}/cancel`,
      { method: "POST" },
    ));
  }

  const maxNoResult = Math.min(5, Math.floor(caseCount / 5));
  const canGenerate = Boolean(
    versionId && generatorModelId && coverage.length && preflight?.valid && !busy,
  );

  return (
    <section className="surface-panel rounded-lg border border-white/10 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/10 pb-3">
        <div>
          <h2 className="text-sm font-semibold text-white">针对当前知识库生成 Gold 评测集</h2>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-400">
            固定索引版本并抽取真实证据，模型只负责生成问题；document、chunk 与 source block 由服务端映射和校验。生成后会立即运行一次真实检索校准。
          </p>
        </div>
        {preflight?.valid ? <span className="inline-flex items-center gap-1 text-xs text-emerald-200"><CheckCircle2 className="h-4 w-4" />证据可用</span> : null}
      </div>

      {error ? <p className="mt-3 rounded-md border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-xs text-rose-100">{error}</p> : null}

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <label className="text-xs font-semibold text-slate-300">固定索引版本
          <select className="mt-1 h-10 w-full rounded-md border border-white/10 bg-surface-950 px-3 text-sm text-white" onChange={(event) => { setVersionId(event.target.value); setPreflight(null); }} value={versionId}>
            {versions.filter((item) => ["ready", "active"].includes(item.status)).map((item) => <option key={item.version_id} value={item.version_id}>v{item.version} {item.active ? "（活动）" : ""}</option>)}
          </select>
        </label>
        <label className="text-xs font-semibold text-slate-300">生成模型
          <select className="mt-1 h-10 w-full rounded-md border border-white/10 bg-surface-950 px-3 text-sm text-white" onChange={(event) => setGeneratorModelId(event.target.value)} value={generatorModelId}>
            {models.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}
          </select>
        </label>
      </div>

      <div className="mt-4">
        <div className="flex items-center justify-between gap-3"><span className="text-xs font-semibold text-slate-300">文档范围</span><button className="text-xs text-cyan-100" onClick={() => setDocumentIds([])} type="button">全部文档</button></div>
        <div className="mt-2 grid max-h-36 gap-2 overflow-y-auto sm:grid-cols-2 xl:grid-cols-3">
          {documents.map((document) => <label className="flex items-center gap-2 rounded-md border border-white/10 px-3 py-2 text-xs text-slate-300" key={document.id}><input checked={documentIds.includes(document.id)} onChange={(event) => setDocumentIds((current) => event.target.checked ? [...current, document.id] : current.filter((item) => item !== document.id))} type="checkbox" /><span className="truncate">{document.filename}</span></label>)}
        </div>
        <p className="mt-2 text-[11px] text-slate-500">未勾选时覆盖固定版本中的全部文档。</p>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="text-xs text-slate-400">用例数<input className="mt-1 h-9 w-full rounded-md border border-white/10 bg-surface-950 px-3 text-sm text-white" max={30} min={6} onChange={(event) => { const value = Number(event.target.value); setCaseCount(value); setNoResultCount((current) => Math.min(current, Math.min(5, Math.floor(value / 5)))); }} type="number" value={caseCount} /></label>
        <label className="text-xs text-slate-400">无答案题<input className="mt-1 h-9 w-full rounded-md border border-white/10 bg-surface-950 px-3 text-sm text-white" max={maxNoResult} min={0} onChange={(event) => setNoResultCount(Number(event.target.value))} type="number" value={noResultCount} /></label>
        <label className="text-xs text-slate-400">Seed<input className="mt-1 h-9 w-full rounded-md border border-white/10 bg-surface-950 px-3 text-sm text-white" min={0} onChange={(event) => setSeed(Number(event.target.value))} type="number" value={seed} /></label>
        <div className="text-xs text-slate-400">语言<div className="mt-1 flex h-9 items-center gap-3 rounded-md border border-white/10 bg-surface-950 px-3">{["zh-CN", "en-US"].map((locale) => <label className="flex items-center gap-1" key={locale}><input checked={locales.includes(locale)} onChange={(event) => setLocales((current) => event.target.checked ? [...current, locale] : current.filter((item) => item !== locale))} type="checkbox" />{locale}</label>)}</div></div>
      </div>

      <div className="mt-4">
        <div className="flex items-center justify-between gap-3"><span className="text-xs font-semibold text-slate-300">覆盖矩阵</span><button className="inline-flex items-center gap-1 rounded-md border border-white/10 px-3 py-2 text-xs text-slate-200 disabled:opacity-40" disabled={!versionId || busy === "preflight"} onClick={() => void analyze()} type="button">{busy === "preflight" ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}分析证据</button></div>
        <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{(preflight?.coverage.available ?? Object.keys(coverageLabels)).map((item) => <label className="flex items-center gap-2 rounded-md border border-white/10 bg-white/[0.025] px-3 py-2 text-xs text-slate-200" key={item}><input checked={coverage.includes(item)} onChange={(event) => setCoverage((current) => event.target.checked ? [...current, item] : current.filter((value) => value !== item))} type="checkbox" />{coverageLabels[item] ?? item}</label>)}</div>
      </div>

      {preflight?.sampling ? <div className="mt-4 grid gap-2 sm:grid-cols-4">{[
        ["范围文档", preflight.sampling.document_count],
        ["稳定证据块", preflight.sampling.stable_evidence_count],
        ["发送证据块", preflight.sampling.sampled_evidence_count],
        ["预计字符", preflight.sampling.estimated_context_chars.toLocaleString()],
      ].map(([label, value]) => <div className="rounded-md border border-white/10 bg-white/[0.025] p-3 text-xs text-slate-500" key={String(label)}>{label}<strong className="mt-1 block text-slate-100">{value}</strong></div>)}</div> : null}

      {preflight?.warnings?.length ? <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-300/25 bg-amber-300/10 p-3 text-xs text-amber-100"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><span>{preflight.warnings.join("；")}</span></div> : null}

      <button className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-md bg-cyan-300 px-4 py-3 text-sm font-semibold text-surface-950 disabled:cursor-not-allowed disabled:opacity-45" disabled={!canGenerate} onClick={() => void createGeneration()} type="button">{busy === "generate" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}生成并自动校准</button>

      {job ? <div className="mt-4 border-t border-white/10 pt-4">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-sm font-semibold text-white">{job.target?.label || "定向生成任务"}</p><p className="mt-1 font-mono text-[10px] text-slate-500">{job.job_id}</p></div><div className="flex items-center gap-2"><span className="rounded border border-white/10 px-2 py-1 text-xs text-slate-300">{job.status}</span>{!["completed", "failed", "cancelled"].includes(job.status) ? <button className="inline-flex items-center gap-1 rounded border border-rose-300/25 px-2 py-1 text-xs text-rose-100" onClick={() => void cancelJob()} type="button"><Square className="h-3 w-3" />取消</button> : null}</div></div>
        {job.calibration?.status ? <div className="mt-3 grid gap-2 sm:grid-cols-4"><div className="rounded border border-white/10 p-3 text-xs text-slate-500">校准状态<strong className="mt-1 block text-white">{job.calibration.status}</strong></div>{Object.entries(job.calibration.counts ?? {}).slice(0, 3).map(([key, value]) => <div className="rounded border border-white/10 p-3 text-xs text-slate-500" key={key}>{key}<strong className="mt-1 block text-white">{value}</strong></div>)}</div> : null}
        {job.calibration?.reason ? <p className="mt-3 text-xs text-amber-100">{job.calibration.reason}</p> : null}
        {job.error ? <p className="mt-3 text-xs text-rose-200">{job.error}</p> : null}
      </div> : null}
    </section>
  );
}
