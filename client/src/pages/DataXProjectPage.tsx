import { FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import PageContainer from "../components/PageContainer";
import type { DataXImportJob, DataXIndicator, DataXModel, DataXProjectDetail, DataXResult, DataXSource } from "../types/datax";

type Tab = "sources" | "models" | "indicators" | "analysis";

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(String(payload.detail || `请求失败：${response.status}`));
  return payload as T;
}

async function waitForImport(job: DataXImportJob): Promise<DataXImportJob> {
  let current = job;
  for (let attempt = 0; attempt < 120 && ["pending", "processing"].includes(current.status); attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 500));
    current = await requestJson<DataXImportJob>(`/api/datax/import-jobs/${job.job_id}`);
  }
  return current;
}

function safeName(value: string, fallback: string) {
  const normalized = value.trim().replace(/[^A-Za-z0-9_]+/g, "_").replace(/^\d+/, "");
  return normalized || fallback;
}

function fieldRole(dataType: string, name: string): "dimension" | "time" | "measure" {
  if (/DATE|TIME/i.test(dataType) || /date|time|日期|时间/i.test(name)) return "time";
  if (/INT|DOUBLE|FLOAT|DECIMAL|NUMERIC/i.test(dataType)) return "measure";
  return "dimension";
}

function StatusPill({ value }: { value: string }) {
  const tone = value === "ready" || value === "published" ? "bg-emerald-400/10 text-emerald-200" : value === "failed" ? "bg-rose-400/10 text-rose-200" : "bg-amber-300/10 text-amber-100";
  const label: Record<string, string> = { ready: "可用", published: "已发布", draft: "草稿", failed: "失败", pending: "等待", processing: "处理中", archived: "已归档" };
  return <span className={`rounded-full px-2 py-0.5 text-[11px] ${tone}`}>{label[value] || value}</span>;
}

export default function DataXProjectPage() {
  const { projectId = "" } = useParams();
  const [detail, setDetail] = useState<DataXProjectDetail | null>(null);
  const [tab, setTab] = useState<Tab>("sources");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [modelName, setModelName] = useState("主语义模型");
  const [modelSourceId, setModelSourceId] = useState("");
  const [indicatorType, setIndicatorType] = useState<"basic" | "derived">("basic");
  const [indicatorName, setIndicatorName] = useState("");
  const [indicatorCode, setIndicatorCode] = useState("");
  const [indicatorModelId, setIndicatorModelId] = useState("");
  const [aggregation, setAggregation] = useState("sum");
  const [measureField, setMeasureField] = useState("");
  const [formula, setFormula] = useState("");
  const [queryIndicator, setQueryIndicator] = useState("");
  const [queryDimension, setQueryDimension] = useState("");
  const [queryView, setQueryView] = useState<DataXResult["view"]>("table");
  const [result, setResult] = useState<DataXResult | null>(null);

  async function load() {
    try {
      const payload = await requestJson<DataXProjectDetail>(`/api/datax/projects/${projectId}`);
      setDetail(payload);
      const readySource = payload.sources.find((item) => item.status === "ready");
      const firstModel = payload.models[0];
      const firstPublished = payload.indicators.find((item) => item.status === "published");
      setModelSourceId((current) => current || readySource?.source_id || "");
      setIndicatorModelId((current) => current || firstModel?.model_id || "");
      setQueryIndicator((current) => current || firstPublished?.code || "");
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目加载失败");
    }
  }

  useEffect(() => { void load(); }, [projectId]);

  const selectedIndicatorModel = detail?.models.find((item) => item.model_id === indicatorModelId);
  const selectedQueryIndicator = detail?.indicators.find((item) => item.code === queryIndicator);
  const queryModel = detail?.models.find((item) => item.model_id === selectedQueryIndicator?.model_id);
  const publishedIndicators = detail?.indicators.filter((item) => item.status === "published") || [];

  async function uploadSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const input = event.currentTarget.elements.namedItem("source") as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    setBusy("upload");
    try {
      const body = new FormData();
      body.append("file", file);
      const job = await requestJson<DataXImportJob>(`/api/datax/projects/${projectId}/sources`, { method: "POST", body });
      input.value = "";
      setNotice("数据源快照已进入导入队列。");
      await load();
      const completed = await waitForImport(job);
      if (completed.status === "failed") throw new Error(completed.error || "数据源导入失败");
      if (completed.status !== "ready") throw new Error("数据源导入仍在后台处理中，请稍后刷新");
      setNotice("数据源快照已导入并完成字段画像。");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "导入失败");
    } finally { setBusy(""); }
  }

  async function createModel(event: FormEvent) {
    event.preventDefault();
    const source = detail?.sources.find((item) => item.source_id === modelSourceId && item.status === "ready");
    if (!source) return;
    setBusy("model");
    try {
      const entityId = "entity_main";
      const fields = (source.profile.columns || []).map((column, index) => ({
        field_id: `field_${index + 1}`,
        entity_id: entityId,
        source_field: column.name,
        name: safeName(column.name, `field_${index + 1}`),
        label: column.name,
        data_type: column.data_type,
        role: fieldRole(column.data_type, column.name),
      }));
      await requestJson(`/api/datax/projects/${projectId}/models`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: modelName,
          description: `基于 ${source.file_name} 的单实体语义模型`,
          entities: [{ entity_id: entityId, source_id: source.source_id, alias: "main", label: source.name }],
          joins: [],
          fields,
        }),
      });
      setNotice("语义模型已创建，可继续调整字段角色并定义指标。");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建模型失败");
    } finally { setBusy(""); }
  }

  async function createIndicator(event: FormEvent) {
    event.preventDefault();
    if (!indicatorModelId || !indicatorName.trim() || !indicatorCode.trim()) return;
    setBusy("indicator");
    try {
      await requestJson(`/api/datax/projects/${projectId}/indicators`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model_id: indicatorModelId,
          code: safeName(indicatorCode, "metric"),
          name: indicatorName.trim(),
          indicator_type: indicatorType,
          aggregation: indicatorType === "basic" ? aggregation : null,
          measure_field: indicatorType === "basic" && aggregation !== "count" ? measureField : null,
          formula: indicatorType === "derived" ? formula : null,
          default_dimensions: [],
          filters: [],
          tags: [],
        }),
      });
      setIndicatorName(""); setIndicatorCode(""); setFormula("");
      setNotice("指标草稿已创建，发布后才会进入 Agent 检索目录。");
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建指标失败");
    } finally { setBusy(""); }
  }

  async function publish(indicator: DataXIndicator) {
    setBusy(indicator.indicator_id);
    try {
      await requestJson(`/api/datax/indicators/${indicator.indicator_id}/publish`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ revision: indicator.revision }),
      });
      setNotice(`${indicator.name} 已发布为新的不可变版本。`);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "发布失败"); }
    finally { setBusy(""); }
  }

  async function runQuery(event: FormEvent) {
    event.preventDefault();
    if (!selectedQueryIndicator) return;
    setBusy("query");
    try {
      const payload = await requestJson<DataXResult>("/api/datax/query", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          model_id: selectedQueryIndicator.model_id,
          indicators: [selectedQueryIndicator.code],
          dimensions: queryDimension ? [queryDimension] : [],
          filters: [], limit: 100, view: queryView,
        }),
      });
      setResult(payload);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "查询失败"); }
    finally { setBusy(""); }
  }

  if (!detail) return <PageContainer><div className="mx-auto max-w-6xl px-6 py-16 text-sm text-slate-400">{error || "正在加载 Data X 项目..."}</div></PageContainer>;

  const tabs: Array<{ id: Tab; label: string; count?: number }> = [
    { id: "sources", label: "数据源", count: detail.sources.length },
    { id: "models", label: "语义模型", count: detail.models.length },
    { id: "indicators", label: "指标", count: detail.indicators.length },
    { id: "analysis", label: "分析" },
  ];

  return (
    <PageContainer>
      <main className="mx-auto w-full max-w-[1560px] px-4 py-5 sm:px-6 lg:px-8">
        <header className="border-b border-white/10 pb-4">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <Link className="text-xs font-semibold text-cyan-200 hover:text-cyan-100" to="/datax">‹ 全部 Data X 项目</Link>
              <div className="mt-2 flex items-center gap-3"><h1 className="text-xl font-semibold text-white">{detail.name}</h1><StatusPill value={detail.status} /></div>
              <p className="mt-1 max-w-3xl text-sm text-slate-400">{detail.description || "本地 DuckDB 语义指标项目"}</p>
            </div>
            <div className="flex items-center gap-2">
              <Link className="rounded-md border border-white/10 px-3 py-2 text-sm font-semibold text-slate-200 hover:bg-white/5" to={`/datax/${projectId}/inbox`}>提案 Inbox</Link>
              <button className="rounded-md bg-white px-3 py-2 text-sm font-semibold text-slate-950 hover:bg-slate-200" onClick={() => void load()} type="button">刷新数据</button>
            </div>
          </div>
          <nav className="mt-5 flex gap-1 overflow-x-auto" aria-label="Data X 项目导航">
            {tabs.map((item) => <button className={`min-h-9 shrink-0 rounded-md px-3 text-sm font-semibold ${tab === item.id ? "bg-cyan-300 text-slate-950" : "text-slate-400 hover:bg-white/5 hover:text-white"}`} key={item.id} onClick={() => setTab(item.id)} type="button">{item.label}{item.count !== undefined ? ` ${item.count}` : ""}</button>)}
          </nav>
        </header>

        {error && <div className="mt-4 rounded-md bg-rose-500/10 px-3 py-2 text-sm text-rose-200" role="alert">{error}</div>}
        {notice && <div className="mt-4 flex items-center justify-between rounded-md bg-emerald-400/10 px-3 py-2 text-sm text-emerald-100"><span>{notice}</span><button className="text-xs font-semibold" onClick={() => setNotice("")} type="button">关闭</button></div>}

        {tab === "sources" && <SourcesTab busy={busy} detail={detail} onUpload={uploadSource} />}
        {tab === "models" && <ModelsTab busy={busy} detail={detail} modelName={modelName} modelSourceId={modelSourceId} onCreate={createModel} setModelName={setModelName} setModelSourceId={setModelSourceId} />}
        {tab === "indicators" && <IndicatorsTab aggregation={aggregation} busy={busy} detail={detail} indicatorCode={indicatorCode} indicatorModelId={indicatorModelId} indicatorName={indicatorName} indicatorType={indicatorType} measureField={measureField} onCreate={createIndicator} onPublish={publish} formula={formula} selectedModel={selectedIndicatorModel} setAggregation={setAggregation} setFormula={setFormula} setIndicatorCode={setIndicatorCode} setIndicatorModelId={setIndicatorModelId} setIndicatorName={setIndicatorName} setIndicatorType={setIndicatorType} setMeasureField={setMeasureField} />}
        {tab === "analysis" && <AnalysisTab busy={busy} dimension={queryDimension} indicator={queryIndicator} model={queryModel} onRun={runQuery} published={publishedIndicators} result={result} setDimension={setQueryDimension} setIndicator={setQueryIndicator} setView={setQueryView} view={queryView} />}
      </main>
    </PageContainer>
  );
}

function SectionHeading({ title, description }: { title: string; description: string }) {
  return <div><h2 className="text-sm font-semibold text-white">{title}</h2><p className="mt-1 text-xs leading-5 text-slate-500">{description}</p></div>;
}

function SourcesTab({ detail, busy, onUpload }: { detail: DataXProjectDetail; busy: string; onUpload: (event: FormEvent<HTMLFormElement>) => void }) {
  return <div className="mt-6 grid gap-6 xl:grid-cols-[310px_minmax(0,1fr)]">
    <form className="self-start border-t border-white/10 pt-4" onSubmit={onUpload}>
      <SectionHeading title="导入不可变快照" description="单文件 50 MB，最多 100 万行。重复内容按 SHA-256 复用。" />
      <input accept=".csv,.xlsx,.parquet" className="mt-4 block w-full rounded-md border border-white/10 bg-white/[0.035] px-3 py-2 text-xs text-slate-300 file:mr-3 file:rounded file:border-0 file:bg-white file:px-2 file:py-1 file:text-xs file:font-semibold file:text-slate-950" name="source" required type="file" />
      <button className="mt-3 w-full rounded-md bg-cyan-300 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50" disabled={busy === "upload"} type="submit">{busy === "upload" ? "导入中..." : "导入数据源"}</button>
    </form>
    <div className="overflow-x-auto border-y border-white/10">
      <table className="w-full min-w-[760px] text-left text-xs"><thead className="bg-white/[0.03] text-slate-400"><tr><th className="px-3 py-2.5">文件</th><th className="px-3 py-2.5">格式</th><th className="px-3 py-2.5">状态</th><th className="px-3 py-2.5 text-right">行</th><th className="px-3 py-2.5 text-right">字段</th><th className="px-3 py-2.5">更新时间</th></tr></thead>
      <tbody className="divide-y divide-white/10">{detail.sources.map((source) => <tr key={source.source_id} className="text-slate-300"><td className="px-3 py-3"><div className="font-semibold text-white">{source.file_name}</div><div className="mt-1 text-[11px] text-slate-500">{(source.byte_size / 1024 / 1024).toFixed(2)} MB{source.error ? ` · ${source.error}` : ""}</div></td><td className="px-3 py-3 uppercase text-slate-400">{source.file_type}</td><td className="px-3 py-3"><StatusPill value={source.status} /></td><td className="px-3 py-3 text-right tabular-nums">{source.row_count.toLocaleString()}</td><td className="px-3 py-3 text-right tabular-nums">{source.column_count}</td><td className="px-3 py-3 text-slate-500">{new Date(source.updated_at * 1000).toLocaleString("zh-CN")}</td></tr>)}</tbody></table>
      {!detail.sources.length && <div className="py-16 text-center text-sm text-slate-500">导入第一个 CSV、XLSX 或 Parquet 快照。</div>}
    </div>
  </div>;
}

function ModelsTab({ detail, busy, modelName, modelSourceId, setModelName, setModelSourceId, onCreate }: { detail: DataXProjectDetail; busy: string; modelName: string; modelSourceId: string; setModelName: (value: string) => void; setModelSourceId: (value: string) => void; onCreate: (event: FormEvent) => void }) {
  const ready = detail.sources.filter((item) => item.status === "ready");
  return <div className="mt-6 grid gap-6 xl:grid-cols-[310px_minmax(0,1fr)]">
    <form className="self-start border-t border-white/10 pt-4" onSubmit={onCreate}><SectionHeading title="从快照建立模型" description="首版表单创建单实体模型；API 已支持 1 至 5 个实体和显式 inner/left 等值连接。" /><label className="mt-4 block text-xs font-semibold text-slate-300">模型名称</label><input className="mt-1 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => setModelName(event.target.value)} value={modelName} /><label className="mt-3 block text-xs font-semibold text-slate-300">源快照</label><select className="mt-1 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => setModelSourceId(event.target.value)} value={modelSourceId}><option value="">选择 ready 快照</option>{ready.map((source) => <option key={source.source_id} value={source.source_id}>{source.file_name}</option>)}</select><button className="mt-3 w-full rounded-md bg-cyan-300 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50" disabled={busy === "model" || !modelSourceId} type="submit">{busy === "model" ? "创建中..." : "推断字段并创建"}</button></form>
    <div className="space-y-5">{detail.models.map((model) => <section className="border-t border-white/10 pt-4" key={model.model_id}><div className="flex flex-wrap items-start justify-between gap-3"><div><h3 className="text-sm font-semibold text-white">{model.name}</h3><p className="mt-1 text-xs text-slate-500">{model.entities.length} 个实体 · {model.joins.length} 条连接 · revision {model.revision}</p></div><code className="text-[11px] text-cyan-200">{model.model_id}</code></div><div className="mt-3 overflow-x-auto"><table className="w-full min-w-[650px] text-left text-xs"><thead className="text-slate-500"><tr><th className="py-2">字段</th><th className="py-2">源字段</th><th className="py-2">类型</th><th className="py-2">角色</th></tr></thead><tbody className="divide-y divide-white/5">{model.fields.slice(0, 30).map((field) => <tr key={field.field_id}><td className="py-2 font-medium text-slate-200">{field.name}</td><td className="py-2 text-slate-500">{field.source_field}</td><td className="py-2 text-slate-500">{field.data_type}</td><td className="py-2"><span className="rounded bg-white/5 px-2 py-1 text-slate-300">{field.role}</span></td></tr>)}</tbody></table></div></section>)}{!detail.models.length && <div className="border-y border-white/10 py-16 text-center text-sm text-slate-500">准备好源快照后创建语义模型。</div>}</div>
  </div>;
}

interface IndicatorsTabProps { detail: DataXProjectDetail; selectedModel?: DataXModel; busy: string; indicatorType: "basic" | "derived"; indicatorName: string; indicatorCode: string; indicatorModelId: string; aggregation: string; measureField: string; formula: string; setIndicatorType: (value: "basic" | "derived") => void; setIndicatorName: (value: string) => void; setIndicatorCode: (value: string) => void; setIndicatorModelId: (value: string) => void; setAggregation: (value: string) => void; setMeasureField: (value: string) => void; setFormula: (value: string) => void; onCreate: (event: FormEvent) => void; onPublish: (indicator: DataXIndicator) => void }
function IndicatorsTab(props: IndicatorsTabProps) {
  const measures = props.selectedModel?.fields.filter((field) => field.role === "measure") || [];
  return <div className="mt-6 grid gap-6 xl:grid-cols-[340px_minmax(0,1fr)]"><form className="self-start border-t border-white/10 pt-4" onSubmit={props.onCreate}><SectionHeading title="定义指标草稿" description="基础指标由聚合 DSL 编译；派生指标只允许引用已发布 code 的四则运算。" /><div className="mt-4 grid grid-cols-2 gap-1 rounded-md bg-white/[0.035] p-1"><button className={`rounded px-2 py-1.5 text-xs font-semibold ${props.indicatorType === "basic" ? "bg-white text-slate-950" : "text-slate-400"}`} onClick={() => props.setIndicatorType("basic")} type="button">基础指标</button><button className={`rounded px-2 py-1.5 text-xs font-semibold ${props.indicatorType === "derived" ? "bg-white text-slate-950" : "text-slate-400"}`} onClick={() => props.setIndicatorType("derived")} type="button">派生指标</button></div><label className="mt-3 block text-xs font-semibold text-slate-300">语义模型</label><select className="mt-1 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => props.setIndicatorModelId(event.target.value)} value={props.indicatorModelId}><option value="">选择模型</option>{props.detail.models.map((model) => <option key={model.model_id} value={model.model_id}>{model.name}</option>)}</select><div className="mt-3 grid grid-cols-2 gap-2"><div><label className="block text-xs font-semibold text-slate-300">名称</label><input className="mt-1 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => props.setIndicatorName(event.target.value)} value={props.indicatorName} /></div><div><label className="block text-xs font-semibold text-slate-300">Code</label><input className="mt-1 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 font-mono text-sm text-white" onChange={(event) => props.setIndicatorCode(event.target.value)} placeholder="gross_revenue" value={props.indicatorCode} /></div></div>{props.indicatorType === "basic" ? <><label className="mt-3 block text-xs font-semibold text-slate-300">聚合</label><select className="mt-1 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => props.setAggregation(event.target.value)} value={props.aggregation}>{["sum", "count", "count_distinct", "avg", "min", "max"].map((value) => <option key={value}>{value}</option>)}</select>{props.aggregation !== "count" && <><label className="mt-3 block text-xs font-semibold text-slate-300">度量字段</label><select className="mt-1 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => props.setMeasureField(event.target.value)} value={props.measureField}><option value="">选择 measure</option>{measures.map((field) => <option key={field.field_id} value={field.name}>{field.label || field.name}</option>)}</select></>}</> : <><label className="mt-3 block text-xs font-semibold text-slate-300">公式</label><input className="mt-1 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 font-mono text-sm text-white" onChange={(event) => props.setFormula(event.target.value)} placeholder="revenue / orders" value={props.formula} /></>}<button className="mt-4 w-full rounded-md bg-cyan-300 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50" disabled={props.busy === "indicator"} type="submit">{props.busy === "indicator" ? "保存中..." : "保存指标草稿"}</button></form><div className="overflow-x-auto border-y border-white/10"><table className="w-full min-w-[760px] text-left text-xs"><thead className="bg-white/[0.03] text-slate-400"><tr><th className="px-3 py-2.5">指标</th><th className="px-3 py-2.5">类型</th><th className="px-3 py-2.5">定义</th><th className="px-3 py-2.5">状态</th><th className="px-3 py-2.5 text-right">操作</th></tr></thead><tbody className="divide-y divide-white/10">{props.detail.indicators.map((indicator) => <tr key={indicator.indicator_id}><td className="px-3 py-3"><div className="font-semibold text-white">{indicator.name}</div><code className="mt-1 block text-[11px] text-cyan-200">{indicator.code}</code></td><td className="px-3 py-3 text-slate-400">{indicator.indicator_type}</td><td className="px-3 py-3 font-mono text-slate-400">{indicator.indicator_type === "basic" ? `${indicator.aggregation}(${indicator.measure_field || "*"})` : indicator.formula}</td><td className="px-3 py-3"><StatusPill value={indicator.status} /></td><td className="px-3 py-3 text-right">{indicator.status === "draft" ? <button className="rounded bg-white px-2.5 py-1.5 font-semibold text-slate-950 disabled:opacity-50" disabled={props.busy === indicator.indicator_id} onClick={() => props.onPublish(indicator)} type="button">{props.busy === indicator.indicator_id ? "发布中" : "发布版本"}</button> : <span className="text-slate-500">v{indicator.current_version}</span>}</td></tr>)}</tbody></table>{!props.detail.indicators.length && <div className="py-16 text-center text-sm text-slate-500">创建第一个业务指标。</div>}</div></div>;
}

interface AnalysisProps { published: DataXIndicator[]; model?: DataXModel; indicator: string; dimension: string; view: DataXResult["view"]; result: DataXResult | null; busy: string; setIndicator: (value: string) => void; setDimension: (value: string) => void; setView: (value: DataXResult["view"]) => void; onRun: (event: FormEvent) => void }
function AnalysisTab(props: AnalysisProps) {
  const dimensions = props.model?.fields.filter((field) => field.role === "dimension" || field.role === "time") || [];
  return <div className="mt-6 grid gap-6 xl:grid-cols-[300px_minmax(0,1fr)]"><form className="self-start border-t border-white/10 pt-4" onSubmit={props.onRun}><SectionHeading title="运行受限分析" description="这里只查询已发布指标。明细 SQL、物理表和文件路径不会暴露给浏览器。" /><label className="mt-4 block text-xs font-semibold text-slate-300">指标</label><select className="mt-1 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => { props.setIndicator(event.target.value); props.setDimension(""); }} value={props.indicator}><option value="">选择已发布指标</option>{props.published.map((indicator) => <option key={indicator.indicator_id} value={indicator.code}>{indicator.name} · {indicator.code}</option>)}</select><label className="mt-3 block text-xs font-semibold text-slate-300">分组维度</label><select className="mt-1 w-full rounded-md border border-white/10 bg-ink-950 px-3 py-2 text-sm text-white" onChange={(event) => props.setDimension(event.target.value)} value={props.dimension}><option value="">不分组，返回 KPI</option>{dimensions.map((field) => <option key={field.field_id} value={field.name}>{field.label || field.name}</option>)}</select><label className="mt-3 block text-xs font-semibold text-slate-300">视图</label><div className="mt-1 grid grid-cols-4 gap-1">{(["kpi", "table", "line", "bar"] as const).map((view) => <button className={`rounded px-2 py-1.5 text-xs font-semibold ${props.view === view ? "bg-cyan-300 text-slate-950" : "bg-white/5 text-slate-400"}`} key={view} onClick={() => props.setView(view)} type="button">{view}</button>)}</div><button className="mt-4 w-full rounded-md bg-white px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-50" disabled={props.busy === "query" || !props.indicator} type="submit">{props.busy === "query" ? "查询中..." : "运行指标查询"}</button></form><ResultView result={props.result} /></div>;
}

function ResultView({ result }: { result: DataXResult | null }) {
  if (!result) return <div className="flex min-h-96 items-center justify-center border-y border-white/10 text-sm text-slate-500">选择已发布指标并运行查询。</div>;
  const dimension = result.columns[0]; const measure = result.columns[result.columns.length - 1];
  if (result.view === "kpi") return <div className="border-y border-white/10 py-12"><p className="text-sm text-slate-500">{measure}</p><p className="mt-2 text-4xl font-semibold tabular-nums text-white">{String(result.rows[0]?.[measure] ?? "-")}</p><p className="mt-3 text-xs text-slate-500">artifact {result.artifact_id}</p></div>;
  if ((result.view === "line" || result.view === "bar") && result.rows.length) return <div className="border-y border-white/10 py-5"><div className="mb-4 flex items-center justify-between text-xs text-slate-500"><span>{result.row_count} 行</span><span>{measure} by {dimension}</span></div><div className="h-[420px] w-full"><ResponsiveContainer height="100%" width="100%">{result.view === "line" ? <LineChart data={result.rows}><CartesianGrid stroke="rgba(148,163,184,.12)" vertical={false} /><XAxis dataKey={dimension} stroke="#64748b" tick={{ fill: "#94a3b8", fontSize: 11 }} /><YAxis stroke="#64748b" tick={{ fill: "#94a3b8", fontSize: 11 }} /><Tooltip contentStyle={{ background: "#0b1220", border: "1px solid rgba(148,163,184,.18)", borderRadius: 6 }} /><Line dataKey={measure} dot={false} stroke="#67e8f9" strokeWidth={2} type="monotone" /></LineChart> : <BarChart data={result.rows}><CartesianGrid stroke="rgba(148,163,184,.12)" vertical={false} /><XAxis dataKey={dimension} stroke="#64748b" tick={{ fill: "#94a3b8", fontSize: 11 }} /><YAxis stroke="#64748b" tick={{ fill: "#94a3b8", fontSize: 11 }} /><Tooltip contentStyle={{ background: "#0b1220", border: "1px solid rgba(148,163,184,.18)", borderRadius: 6 }} /><Bar dataKey={measure} fill="#67e8f9" radius={[3, 3, 0, 0]} /></BarChart>}</ResponsiveContainer></div></div>;
  return <div className="overflow-x-auto border-y border-white/10"><table className="w-full min-w-[600px] text-left text-xs"><thead className="bg-white/[0.03] text-slate-400"><tr>{result.columns.map((column) => <th className="px-3 py-2.5" key={column}>{column}</th>)}</tr></thead><tbody className="divide-y divide-white/10">{result.rows.map((row, index) => <tr key={index}>{result.columns.map((column) => <td className="px-3 py-2.5 text-slate-300" key={column}>{String(row[column] ?? "")}</td>)}</tr>)}</tbody></table></div>;
}
