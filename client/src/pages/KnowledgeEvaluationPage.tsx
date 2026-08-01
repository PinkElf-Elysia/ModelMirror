import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";

interface KnowledgeBase {
  id: string;
  name: string;
}

interface RagDocument {
  id: string;
  filename: string;
}

interface PipelineVersion {
  version_id: string;
  version: number;
  status: string;
  active: boolean;
  chunk_count: number;
  created_at: number;
}

interface ExpectedReference {
  document_id: string;
  chunk_id?: string | null;
  source_block_id?: string | null;
  page_number?: number | null;
  relevance: number;
  document_name?: string;
}

interface EvaluationCase {
  case_id: string;
  query: string;
  expected_refs: ExpectedReference[];
  tags: string[];
  notes: string;
}

interface EvaluationSet {
  eval_set_id: string;
  kb_id: string;
  name: string;
  description: string;
  revision: number;
  status: string;
  cases: EvaluationCase[];
  updated_at: number;
}

interface GatePolicy {
  kb_id: string;
  mode: "advisory" | "required";
  min_recall_at_5: number;
  max_mrr_regression: number;
  max_citation_hit_regression: number;
  max_no_result_increase: number;
  max_p95_latency_ratio: number;
  require_zero_errors: boolean;
}

interface RankingItem {
  rank: number;
  chunk_id: string;
  document_id: string;
  document_name: string;
  relevance: number;
  matched_reference_id?: string | null;
  score?: number | null;
}

interface CaseResult {
  case_id: string;
  query_preview: string;
  status: string;
  metrics: Record<string, number>;
  ranking: RankingItem[];
  latency_ms: number;
  error?: string | null;
}

interface TargetResult {
  target_id: string;
  version_id: string;
  version: number;
  label: string;
  metrics: Record<string, number>;
  case_results: CaseResult[];
  promotion_gate: {
    passed: boolean;
    mode: string;
    checks: Array<{ id: string; passed: boolean; actual: number; threshold: number; message: string }>;
  };
}

interface EvaluationRun {
  run_id: string;
  status: string;
  progress: number;
  eval_set_id: string;
  baseline_version_id?: string | null;
  target_results: TargetResult[];
  created_at: number;
  error?: string | null;
}

interface PreviewSource {
  chunk_id: string;
  doc_id: string;
  source_document_id?: string | null;
  document_name: string;
  score: number;
  source_block_id?: string | null;
  page_number?: number | null;
  text?: string;
}

function errorMessage(value: unknown, fallback: string) {
  if (value && typeof value === "object" && "detail" in value) return String(value.detail);
  return fallback;
}

function metric(value: number | undefined, percent = true) {
  if (value == null || Number.isNaN(value)) return "-";
  return percent ? `${(value * 100).toFixed(1)}%` : value.toFixed(3);
}

function timestamp(value: number) {
  return new Date(value * 1000).toLocaleString("zh-CN", { hour12: false });
}

export default function KnowledgeEvaluationPage() {
  const { kbId = "" } = useParams();
  const importRef = useRef<HTMLInputElement>(null);
  const [knowledgeBase, setKnowledgeBase] = useState<KnowledgeBase | null>(null);
  const [documents, setDocuments] = useState<RagDocument[]>([]);
  const [versions, setVersions] = useState<PipelineVersion[]>([]);
  const [evaluationSets, setEvaluationSets] = useState<EvaluationSet[]>([]);
  const [selectedSetId, setSelectedSetId] = useState("");
  const [gate, setGate] = useState<GatePolicy | null>(null);
  const [runs, setRuns] = useState<EvaluationRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<EvaluationRun | null>(null);
  const [selectedVersions, setSelectedVersions] = useState<string[]>([]);
  const [baselineVersionId, setBaselineVersionId] = useState("");
  const [newSetName, setNewSetName] = useState("");
  const [query, setQuery] = useState("");
  const [tags, setTags] = useState("");
  const [references, setReferences] = useState<ExpectedReference[]>([]);
  const [previewSources, setPreviewSources] = useState<PreviewSource[]>([]);
  const [previewVersionId, setPreviewVersionId] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const selectedSet = useMemo(
    () => evaluationSets.find((item) => item.eval_set_id === selectedSetId) ?? null,
    [evaluationSets, selectedSetId],
  );

  useEffect(() => {
    if (!kbId) return;
    void loadWorkspace();
  }, [kbId]);

  useEffect(() => {
    const active = versions.find((item) => item.active) ?? versions[0];
    if (!previewVersionId && active) setPreviewVersionId(active.version_id);
    if (selectedVersions.length === 0 && versions.length > 0) {
      const initial = versions.slice(0, 2).map((item) => item.version_id);
      setSelectedVersions(initial);
      setBaselineVersionId((versions.find((item) => item.active) ?? versions[versions.length - 1]).version_id);
    }
  }, [versions, previewVersionId, selectedVersions.length]);

  useEffect(() => {
    if (!selectedRun || !["queued", "running"].includes(selectedRun.status)) return;
    const timer = window.setInterval(() => void refreshRun(selectedRun.run_id), 900);
    return () => window.clearInterval(timer);
  }, [selectedRun?.run_id, selectedRun?.status]);

  async function loadWorkspace() {
    setBusy("load");
    setError("");
    try {
      const [kbResponse, documentsResponse, versionsResponse, setsResponse, gateResponse, runsResponse] = await Promise.all([
        fetch("/api/rag/knowledge_bases"),
        fetch(`/api/rag/knowledge_bases/${encodeURIComponent(kbId)}/documents`),
        fetch(`/api/rag/pipeline/versions?kb_id=${encodeURIComponent(kbId)}`),
        fetch(`/api/rag/evaluation-sets?kb_id=${encodeURIComponent(kbId)}`),
        fetch(`/api/rag/evaluation-gate/${encodeURIComponent(kbId)}`),
        fetch(`/api/rag/evaluation-runs?kb_id=${encodeURIComponent(kbId)}&limit=20`),
      ]);
      if (![kbResponse, documentsResponse, versionsResponse, setsResponse, gateResponse, runsResponse].every((response) => response.ok)) {
        throw new Error("知识评估工作台加载失败。");
      }
      const kbData = await kbResponse.json();
      const docsData = await documentsResponse.json();
      const versionsData = await versionsResponse.json();
      const setsData = await setsResponse.json();
      const gateData = await gateResponse.json();
      const runsData = await runsResponse.json();
      setKnowledgeBase((kbData.knowledge_bases as KnowledgeBase[]).find((item) => item.id === kbId) ?? null);
      setDocuments(docsData.documents ?? []);
      setVersions(versionsData.versions ?? []);
      setEvaluationSets(setsData.evaluation_sets ?? []);
      setSelectedSetId((current) => current || setsData.evaluation_sets?.[0]?.eval_set_id || "");
      setGate(gateData);
      setRuns(runsData.evaluation_runs ?? []);
      if (!selectedRun && runsData.evaluation_runs?.[0]) await refreshRun(runsData.evaluation_runs[0].run_id, false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "知识评估工作台加载失败。");
    } finally {
      setBusy("");
    }
  }

  async function reloadSets(preferId?: string) {
    const response = await fetch(`/api/rag/evaluation-sets?kb_id=${encodeURIComponent(kbId)}`);
    if (!response.ok) return;
    const data = await response.json();
    setEvaluationSets(data.evaluation_sets ?? []);
    if (preferId) setSelectedSetId(preferId);
  }

  async function createSet() {
    if (!newSetName.trim()) return;
    setBusy("set");
    setError("");
    const response = await fetch("/api/rag/evaluation-sets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kb_id: kbId, name: newSetName.trim() }),
    });
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "创建评估集失败。"));
    setNewSetName("");
    await reloadSets(data.eval_set_id);
  }

  function addDocumentReference(documentId: string, documentName?: string) {
    if (!documentId || references.some((item) => item.document_id === documentId && !item.chunk_id)) return;
    setReferences((current) => [...current, { document_id: documentId, document_name: documentName, relevance: 2 }]);
  }

  function addPreviewReference(source: PreviewSource) {
    const documentId = source.source_document_id || source.doc_id;
    if (references.some((item) => item.chunk_id === source.chunk_id)) return;
    setReferences((current) => [...current, {
      document_id: documentId,
      document_name: source.document_name,
      chunk_id: source.chunk_id,
      source_block_id: source.source_block_id,
      page_number: source.page_number,
      relevance: 3,
    }]);
  }

  async function previewRetrieval() {
    if (!query.trim() || !previewVersionId) return;
    setBusy("preview");
    setError("");
    const response = await fetch(`/api/rag/pipeline/versions/${encodeURIComponent(previewVersionId)}/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: query.trim(), top_k: 10 }),
    });
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "试检索失败。"));
    setPreviewSources(data.sources ?? []);
  }

  async function addCase() {
    if (!selectedSet || !query.trim() || references.length === 0) return;
    setBusy("case");
    setError("");
    const response = await fetch(`/api/rag/evaluation-sets/${selectedSet.eval_set_id}/cases`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: selectedSet.revision,
        case: {
          query: query.trim(),
          expected_refs: references.map(({ document_name: _name, ...item }) => item),
          tags: tags.split(",").map((item) => item.trim()).filter(Boolean),
        },
      }),
    });
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "保存评估问题失败。"));
    setQuery("");
    setTags("");
    setReferences([]);
    setPreviewSources([]);
    await reloadSets(selectedSet.eval_set_id);
    setNotice("评估问题已保存。");
  }

  async function deleteCase(caseId: string) {
    if (!selectedSet) return;
    const response = await fetch(
      `/api/rag/evaluation-sets/${selectedSet.eval_set_id}/cases/${caseId}?expected_revision=${selectedSet.revision}`,
      { method: "DELETE" },
    );
    if (!response.ok) return setError(errorMessage(await response.json().catch(() => null), "删除失败。"));
    await reloadSets(selectedSet.eval_set_id);
  }

  async function importCases(file: File) {
    if (!selectedSet) return;
    const form = new FormData();
    form.append("file", file);
    setBusy("import");
    const response = await fetch(
      `/api/rag/evaluation-sets/${selectedSet.eval_set_id}/import?expected_revision=${selectedSet.revision}`,
      { method: "POST", body: form },
    );
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "导入失败。"));
    await reloadSets(selectedSet.eval_set_id);
    setNotice("评估集已导入。");
  }

  async function createRun() {
    if (!selectedSet || selectedVersions.length === 0) return;
    setBusy("run");
    setError("");
    const response = await fetch("/api/rag/evaluation-runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        eval_set_id: selectedSet.eval_set_id,
        targets: selectedVersions.map((versionId) => ({ version_id: versionId })),
        baseline_version_id: selectedVersions.includes(baselineVersionId) ? baselineVersionId : null,
        ks: [1, 3, 5, 10],
      }),
    });
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "启动评估失败。"));
    setSelectedRun(data);
    setRuns((current) => [data, ...current.filter((item) => item.run_id !== data.run_id)]);
  }

  async function refreshRun(runId: string, refreshList = true) {
    const response = await fetch(`/api/rag/evaluation-runs/${encodeURIComponent(runId)}`);
    if (!response.ok) return;
    const data = await response.json();
    setSelectedRun(data);
    if (refreshList) {
      setRuns((current) => [data, ...current.filter((item) => item.run_id !== data.run_id)]);
    }
  }

  async function saveGate() {
    if (!gate) return;
    setBusy("gate");
    const { kb_id: _kb, ...payload } = gate;
    const response = await fetch(`/api/rag/evaluation-gate/${encodeURIComponent(kbId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "保存 Gate 失败。"));
    setGate(data);
    setNotice("Promotion Gate 已保存。");
  }

  async function promote(versionId: string) {
    if (!selectedRun) return;
    setBusy(`promote:${versionId}`);
    const response = await fetch(`/api/rag/pipeline/versions/${encodeURIComponent(versionId)}/promote`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ evaluation_run_id: selectedRun.run_id }),
    });
    const data = await response.json().catch(() => null);
    setBusy("");
    if (!response.ok) return setError(errorMessage(data, "推广版本失败。"));
    setVersions((current) => current.map((item) => ({ ...item, active: item.version_id === versionId })));
    setNotice(`知识索引 v${data.version} 已通过评估并激活。`);
  }

  return (
    <PageContainer activeResource="prompts" hideSidebar maxWidthClassName="max-w-[1720px]">
      <div className="space-y-4">
        <header className="flex flex-col gap-4 border-b border-white/10 pb-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-3 text-xs font-semibold">
              <Link className="text-hire-100 hover:text-hire-50" to="/rag">知识库</Link>
              <span className="text-slate-600">/</span>
              <Link className="text-slate-300 hover:text-white" to={`/rag/${encodeURIComponent(kbId)}/pipeline`}>执行画布</Link>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <h1 className="text-2xl font-semibold text-white">{knowledgeBase?.name || "知识评估"}</h1>
              <span className="rounded-full border border-cyan-300/25 bg-cyan-300/10 px-2.5 py-1 text-[11px] font-semibold text-cyan-100">Evaluation Beta</span>
            </div>
            <p className="mt-1 text-sm text-slate-400">{documents.length} 文档 · {versions.length} 索引版本 · {selectedSet?.cases.length ?? 0} 评估问题</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="rounded-lg border border-white/10 bg-white/[0.05] px-3 py-2 text-sm font-semibold text-slate-200 hover:bg-white/[0.09]" onClick={() => void loadWorkspace()} type="button">刷新</button>
            <Link className="rounded-lg bg-hire-300 px-4 py-2 text-sm font-bold text-surface-950 hover:bg-hire-200" to={`/rag/${encodeURIComponent(kbId)}/pipeline`}>返回流水线</Link>
          </div>
        </header>

        {error || notice ? (
          <div className={`rounded-lg border px-4 py-3 text-sm ${error ? "border-rose-300/30 bg-rose-400/10 text-rose-100" : "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"}`}>{error || notice}</div>
        ) : null}

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.05fr)_minmax(480px,0.95fr)]">
          <section className="surface-panel rounded-lg border border-white/10 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-3">
              <div>
                <h2 className="text-sm font-semibold text-white">评估数据集</h2>
                <p className="mt-1 text-xs text-slate-500">revision {selectedSet?.revision ?? "-"}</p>
              </div>
              <div className="flex gap-2">
                <select className="rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white" onChange={(event) => setSelectedSetId(event.target.value)} value={selectedSetId}>
                  <option value="">选择评估集</option>
                  {evaluationSets.map((item) => <option key={item.eval_set_id} value={item.eval_set_id}>{item.name} ({item.cases.length})</option>)}
                </select>
                <button className="rounded-lg border border-white/10 px-3 py-2 text-sm text-slate-200 disabled:opacity-40" disabled={!selectedSet} onClick={() => importRef.current?.click()} type="button">导入</button>
                <input accept=".json,.csv,application/json,text/csv" className="hidden" onChange={(event) => { const file = event.target.files?.[0]; if (file) void importCases(file); event.target.value = ""; }} ref={importRef} type="file" />
              </div>
            </div>

            <div className="mt-3 flex gap-2">
              <input className="min-w-0 flex-1 rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/40" onChange={(event) => setNewSetName(event.target.value)} placeholder="新评估集名称" value={newSetName} />
              <button className="rounded-lg border border-hire-300/25 bg-hire-300/10 px-3 py-2 text-sm font-semibold text-hire-100 disabled:opacity-40" disabled={!newSetName.trim() || busy === "set"} onClick={() => void createSet()} type="button">创建</button>
            </div>

            <div className="mt-5 space-y-3 border-t border-white/10 pt-4">
              <textarea className="min-h-24 w-full resize-y rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm leading-6 text-white outline-none focus:border-hire-300/40" onChange={(event) => setQuery(event.target.value)} placeholder="评估问题" value={query} />
              <div className="grid gap-2 sm:grid-cols-[1fr_180px_auto]">
                <select className="rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white" defaultValue="" onChange={(event) => { const document = documents.find((item) => item.id === event.target.value); if (document) addDocumentReference(document.id, document.filename); event.target.value = ""; }}>
                  <option value="">添加期望文档</option>
                  {documents.map((document) => <option key={document.id} value={document.id}>{document.filename}</option>)}
                </select>
                <select className="rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white" onChange={(event) => setPreviewVersionId(event.target.value)} value={previewVersionId}>
                  {versions.map((version) => <option key={version.version_id} value={version.version_id}>v{version.version}{version.active ? " · active" : ""}</option>)}
                </select>
                <button className="rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-3 py-2 text-sm font-semibold text-cyan-100 disabled:opacity-40" disabled={!query.trim() || !previewVersionId || busy === "preview"} onClick={() => void previewRetrieval()} type="button">试检索</button>
              </div>
              <input className="w-full rounded-lg border border-white/10 bg-surface-950 px-3 py-2 text-sm text-white outline-none focus:border-hire-300/40" onChange={(event) => setTags(event.target.value)} placeholder="标签，逗号分隔" value={tags} />

              {references.length > 0 ? (
                <div className="divide-y divide-white/10 rounded-lg border border-white/10">
                  {references.map((reference, index) => (
                    <div className="flex items-center gap-3 px-3 py-2 text-xs" key={`${reference.document_id}:${reference.chunk_id || index}`}>
                      <span className="min-w-0 flex-1 truncate text-slate-200">{reference.document_name || reference.document_id}{reference.page_number ? ` · p${reference.page_number}` : ""}</span>
                      <select className="rounded border border-white/10 bg-surface-950 px-2 py-1 text-slate-200" onChange={(event) => setReferences((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, relevance: Number(event.target.value) } : item))} value={reference.relevance}>
                        <option value={1}>相关</option><option value={2}>重要</option><option value={3}>关键</option>
                      </select>
                      <button className="text-rose-200 hover:text-rose-100" onClick={() => setReferences((current) => current.filter((_, itemIndex) => itemIndex !== index))} type="button">移除</button>
                    </div>
                  ))}
                </div>
              ) : null}

              {previewSources.length > 0 ? (
                <div className="max-h-52 divide-y divide-white/10 overflow-y-auto rounded-lg border border-white/10">
                  {previewSources.map((source, index) => (
                    <button className="flex w-full items-start gap-3 px-3 py-2 text-left hover:bg-white/[0.04]" key={source.chunk_id} onClick={() => addPreviewReference(source)} type="button">
                      <span className="w-6 shrink-0 text-xs font-semibold text-slate-500">{index + 1}</span>
                      <span className="min-w-0 flex-1"><span className="block truncate text-xs font-semibold text-white">{source.document_name}</span><span className="mt-1 line-clamp-2 block text-xs text-slate-400">{source.text}</span></span>
                      <span className="text-[11px] text-cyan-100">{source.score.toFixed(3)}</span>
                    </button>
                  ))}
                </div>
              ) : null}

              <button className="w-full rounded-lg bg-hire-300 px-4 py-2.5 text-sm font-bold text-surface-950 disabled:opacity-40" disabled={!selectedSet || !query.trim() || references.length === 0 || busy === "case"} onClick={() => void addCase()} type="button">保存评估问题</button>
            </div>

            <div className="mt-5 max-h-[360px] divide-y divide-white/10 overflow-y-auto border-t border-white/10">
              {selectedSet?.cases.length ? selectedSet.cases.map((item, index) => (
                <div className="py-3" key={item.case_id}>
                  <div className="flex gap-3"><span className="text-xs font-semibold text-slate-500">{index + 1}</span><p className="min-w-0 flex-1 text-sm text-slate-100">{item.query}</p><button className="text-xs text-rose-200" onClick={() => void deleteCase(item.case_id)} type="button">删除</button></div>
                  <p className="mt-2 pl-7 text-xs text-slate-500">{item.expected_refs.length} 个期望引用 · {item.tags.join(" · ") || "未标记"}</p>
                </div>
              )) : <p className="py-10 text-center text-sm text-slate-500">尚无评估问题</p>}
            </div>
          </section>

          <section className="surface-panel rounded-lg border border-white/10 p-4">
            <div className="border-b border-white/10 pb-3">
              <h2 className="text-sm font-semibold text-white">版本对比</h2>
              <p className="mt-1 text-xs text-slate-500">最多选择 5 个不可变索引版本</p>
            </div>
            <div className="mt-3 divide-y divide-white/10 rounded-lg border border-white/10">
              {versions.map((version) => {
                const checked = selectedVersions.includes(version.version_id);
                return (
                  <label className="flex cursor-pointer items-center gap-3 px-3 py-3" key={version.version_id}>
                    <input checked={checked} className="h-4 w-4 accent-hire-300" onChange={(event) => setSelectedVersions((current) => event.target.checked ? [...current, version.version_id].slice(0, 5) : current.filter((item) => item !== version.version_id))} type="checkbox" />
                    <span className="min-w-0 flex-1"><span className="block text-sm font-semibold text-white">v{version.version} {version.active ? <span className="text-emerald-200">active</span> : null}</span><span className="text-xs text-slate-500">{version.chunk_count} chunks · {timestamp(version.created_at)}</span></span>
                    <label className="flex items-center gap-1.5 text-xs text-slate-400"><input checked={baselineVersionId === version.version_id} disabled={!checked} name="baseline" onChange={() => setBaselineVersionId(version.version_id)} type="radio" />基线</label>
                  </label>
                );
              })}
            </div>
            <button className="mt-3 w-full rounded-lg bg-cyan-300 px-4 py-2.5 text-sm font-bold text-surface-950 disabled:opacity-40" disabled={!selectedSet?.cases.length || selectedVersions.length === 0 || busy === "run"} onClick={() => void createRun()} type="button">运行离线评估</button>

            <div className="mt-5 border-t border-white/10 pt-4">
              <div className="flex items-center justify-between"><h3 className="text-sm font-semibold text-white">Promotion Gate</h3><button className="text-xs font-semibold text-hire-100 disabled:opacity-40" disabled={!gate || busy === "gate"} onClick={() => void saveGate()} type="button">保存</button></div>
              {gate ? (
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <label className="text-xs text-slate-400">模式<select className="mt-1 w-full rounded-lg border border-white/10 bg-surface-950 px-2 py-2 text-sm text-white" onChange={(event) => setGate({ ...gate, mode: event.target.value as GatePolicy["mode"] })} value={gate.mode}><option value="advisory">提示</option><option value="required">强制</option></select></label>
                  <label className="text-xs text-slate-400">最低 Recall@5<input className="mt-1 w-full rounded-lg border border-white/10 bg-surface-950 px-2 py-2 text-sm text-white" max={1} min={0} onChange={(event) => setGate({ ...gate, min_recall_at_5: Number(event.target.value) })} step={0.05} type="number" value={gate.min_recall_at_5} /></label>
                  <label className="text-xs text-slate-400">MRR 最大回退<input className="mt-1 w-full rounded-lg border border-white/10 bg-surface-950 px-2 py-2 text-sm text-white" max={1} min={0} onChange={(event) => setGate({ ...gate, max_mrr_regression: Number(event.target.value) })} step={0.01} type="number" value={gate.max_mrr_regression} /></label>
                  <label className="text-xs text-slate-400">P95 延迟倍数<input className="mt-1 w-full rounded-lg border border-white/10 bg-surface-950 px-2 py-2 text-sm text-white" max={10} min={1} onChange={(event) => setGate({ ...gate, max_p95_latency_ratio: Number(event.target.value) })} step={0.1} type="number" value={gate.max_p95_latency_ratio} /></label>
                </div>
              ) : null}
            </div>

            <div className="mt-5 border-t border-white/10 pt-4">
              <h3 className="text-sm font-semibold text-white">最近评估</h3>
              <div className="mt-2 max-h-44 divide-y divide-white/10 overflow-y-auto">
                {runs.map((run) => <button className={`flex w-full items-center justify-between px-1 py-2 text-left text-xs ${selectedRun?.run_id === run.run_id ? "text-cyan-100" : "text-slate-400 hover:text-slate-200"}`} key={run.run_id} onClick={() => void refreshRun(run.run_id)} type="button"><span>{timestamp(run.created_at)}</span><span>{run.status} · {run.progress}%</span></button>)}
              </div>
            </div>
          </section>
        </div>

        <section className="surface-panel overflow-hidden rounded-lg border border-white/10">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
            <div><h2 className="text-sm font-semibold text-white">评估结果</h2><p className="mt-1 text-xs text-slate-500">{selectedRun ? `${selectedRun.status} · ${selectedRun.progress}% · ${selectedRun.run_id}` : "选择或运行一次评估"}</p></div>
            {selectedRun?.error ? <span className="text-xs text-rose-200">{selectedRun.error}</span> : null}
          </div>
          {selectedRun?.target_results.length ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[980px] text-left text-xs">
                <thead className="border-b border-white/10 bg-white/[0.025] text-slate-500"><tr><th className="px-4 py-3">版本</th><th>Recall@1</th><th>Recall@5</th><th>MRR@10</th><th>nDCG@10</th><th>引用命中</th><th>无结果</th><th>P95</th><th>Gate</th><th className="pr-4">操作</th></tr></thead>
                <tbody className="divide-y divide-white/10">
                  {selectedRun.target_results.map((target) => (
                    <tr key={target.version_id}>
                      <td className="px-4 py-3 font-semibold text-white">v{target.version}</td>
                      <td>{metric(target.metrics.recall_at_1)}</td><td>{metric(target.metrics.recall_at_5)}</td><td>{metric(target.metrics.mrr_at_10, false)}</td><td>{metric(target.metrics.ndcg_at_10, false)}</td><td>{metric(target.metrics.citation_hit_rate)}</td><td>{metric(target.metrics.no_result_rate)}</td><td>{target.metrics.p95_latency_ms?.toFixed(0) ?? "-"} ms</td>
                      <td><span className={target.promotion_gate.passed ? "text-emerald-200" : "text-rose-200"}>{target.promotion_gate.passed ? "通过" : "未通过"}</span></td>
                      <td className="pr-4"><button className="rounded-md border border-emerald-300/25 px-2.5 py-1.5 font-semibold text-emerald-100 disabled:cursor-not-allowed disabled:opacity-35" disabled={!target.promotion_gate.passed || busy === `promote:${target.version_id}`} onClick={() => void promote(target.version_id)} type="button">推广</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="grid gap-4 border-t border-white/10 p-4 xl:grid-cols-2">
                {selectedRun.target_results.map((target) => (
                  <div className="rounded-lg border border-white/10" key={`cases:${target.version_id}`}>
                    <div className="border-b border-white/10 px-3 py-2 text-xs font-semibold text-white">v{target.version} 案例明细</div>
                    <div className="max-h-72 divide-y divide-white/10 overflow-y-auto">
                      {target.case_results.map((caseResult) => (
                        <details className="px-3 py-2" key={caseResult.case_id}>
                          <summary className="cursor-pointer text-xs text-slate-200">{caseResult.query_preview}<span className="ml-2 text-slate-500">R@5 {metric(caseResult.metrics.recall_at_5)} · {caseResult.latency_ms.toFixed(0)}ms</span></summary>
                          <div className="mt-2 space-y-1 pl-3">{caseResult.ranking.slice(0, 10).map((item) => <div className="flex gap-2 text-[11px]" key={`${caseResult.case_id}:${item.rank}`}><span className="w-5 text-slate-600">{item.rank}</span><span className={`min-w-0 flex-1 truncate ${item.relevance ? "text-emerald-200" : "text-slate-500"}`}>{item.document_name || item.document_id}</span><span className="text-slate-600">{item.score?.toFixed(3) ?? "-"}</span></div>)}</div>
                        </details>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : <div className="py-16 text-center text-sm text-slate-500">{busy === "load" ? "正在加载..." : "暂无评估结果"}</div>}
        </section>
      </div>
    </PageContainer>
  );
}
