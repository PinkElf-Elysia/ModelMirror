import { AlertTriangle, Check, LoaderCircle, RefreshCw, Route, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

type Objective = "balanced" | "quality" | "low_latency";
type Requirement = "exact_terms" | "semantic_rewrite" | "cross_language" | "long_context" | "confusable_content" | "citation_precision";

interface Profile {
  profile_id: string;
  title: string;
  confidence: "low" | "medium" | "high";
  reasons: string[];
  evidence: Array<{ rule_id: string; classification: string; source: string }>;
  warnings: string[];
  diff: Array<{ field: string; current: unknown; recommended: unknown }>;
}

interface Recommendation {
  recommendation_id: string;
  rules_version: string;
  state: "ready" | "applied" | "stale" | "insufficient_data";
  draft_version: number;
  corpus_profile: Record<string, unknown>;
  profiles: Profile[];
  warnings: string[];
  insufficient_reasons: string[];
  created_at: number;
}

interface Capabilities {
  rules_version: string;
  score_threshold_fixed: number;
  embedding: { provider: string; degraded: boolean };
  rerank: { available: boolean };
}

interface Props {
  kbId: string;
  open: boolean;
  onClose: () => void;
  onApplied: () => Promise<void> | void;
}

const OBJECTIVES: Array<{ id: Objective; label: string }> = [
  { id: "balanced", label: "均衡" },
  { id: "quality", label: "质量优先" },
  { id: "low_latency", label: "低延迟" },
];

const REQUIREMENTS: Array<{ id: Requirement; label: string; hint: string }> = [
  { id: "exact_terms", label: "精确术语", hint: "编号、代码与专有名词" },
  { id: "semantic_rewrite", label: "语义改写", hint: "问题与原文措辞差异较大" },
  { id: "cross_language", label: "跨语言", hint: "查询与文档语言不同" },
  { id: "long_context", label: "长上下文", hint: "答案依赖章节级上下文" },
  { id: "confusable_content", label: "易混淆内容", hint: "相似或冲突条款较多" },
  { id: "citation_precision", label: "引用精度", hint: "需要细粒度稳定证据" },
];

const EMPTY_REQUIREMENTS: Record<Requirement, boolean> = {
  exact_terms: false,
  semantic_rewrite: false,
  cross_language: false,
  long_context: false,
  confusable_content: false,
  citation_precision: false,
};

function errorMessage(data: unknown, fallback: string) {
  if (!data || typeof data !== "object") return fallback;
  const detail = (data as { detail?: unknown }).detail;
  return typeof detail === "string" ? detail : fallback;
}

function displayValue(value: unknown) {
  if (Array.isArray(value)) return value.map((item) => String(item).replaceAll("\n", "\\n")).join(" · ");
  if (value && typeof value === "object") return JSON.stringify(value);
  if (typeof value === "boolean") return value ? "启用" : "关闭";
  return value === null || value === undefined || value === "" ? "未设置" : String(value);
}

function stateLabel(state: Recommendation["state"]) {
  if (state === "ready") return "可应用";
  if (state === "applied") return "已应用";
  if (state === "stale") return "已过期";
  return "证据不足";
}

export default function RagStrategyRouterPanel({ kbId, open, onClose, onApplied }: Props) {
  const [objective, setObjective] = useState<Objective>("balanced");
  const [requirements, setRequirements] = useState(EMPTY_REQUIREMENTS);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [items, setItems] = useState<Recommendation[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [profileId, setProfileId] = useState("primary");
  const [confirmLow, setConfirmLow] = useState(false);
  const [busy, setBusy] = useState<"load" | "recommend" | "apply" | "">("");
  const [message, setMessage] = useState<{ tone: "error" | "ok"; text: string } | null>(null);

  const selected = useMemo(
    () => items.find((item) => item.recommendation_id === selectedId) ?? items[0] ?? null,
    [items, selectedId],
  );
  const profile = useMemo(
    () => selected?.profiles.find((item) => item.profile_id === profileId) ?? selected?.profiles[0] ?? null,
    [profileId, selected],
  );

  const load = useCallback(async () => {
    setBusy("load");
    setMessage(null);
    try {
      const [capResponse, listResponse] = await Promise.all([
        fetch("/api/rag/strategy-router/capabilities"),
        fetch(`/api/rag/strategy-router/recommendations?kb_id=${encodeURIComponent(kbId)}`),
      ]);
      if (!capResponse.ok) throw new Error("策略能力加载失败。");
      setCapabilities((await capResponse.json()) as Capabilities);
      if (listResponse.ok) {
        const recommendations = ((await listResponse.json()) as { recommendations: Recommendation[] }).recommendations || [];
        setItems(recommendations);
        setSelectedId((current) => current || recommendations[0]?.recommendation_id || "");
      }
    } catch (caught) {
      setMessage({ tone: "error", text: caught instanceof Error ? caught.message : "策略能力加载失败。" });
    } finally {
      setBusy("");
    }
  }, [kbId]);

  useEffect(() => { if (open) void load(); }, [load, open]);
  useEffect(() => {
    setProfileId(selected?.profiles[0]?.profile_id || "primary");
    setConfirmLow(false);
  }, [selected?.recommendation_id, selected?.profiles]);

  async function createRecommendation() {
    setBusy("recommend");
    setMessage(null);
    try {
      const response = await fetch("/api/rag/strategy-router/recommendations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kb_id: kbId, objective, requirements }),
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorMessage(payload, "语料分析失败。"));
      const recommendation = payload as Recommendation;
      setItems((current) => [recommendation, ...current]);
      setSelectedId(recommendation.recommendation_id);
      setProfileId(recommendation.profiles[0]?.profile_id || "primary");
      setMessage({
        tone: "ok",
        text: recommendation.state === "insufficient_data"
          ? "分析完成，但当前证据不足，未生成可应用配置。"
          : "分析完成。请检查依据与配置差异后再应用。",
      });
    } catch (caught) {
      setMessage({ tone: "error", text: caught instanceof Error ? caught.message : "语料分析失败。" });
    } finally {
      setBusy("");
    }
  }

  async function applyRecommendation() {
    if (!selected || !profile) return;
    setBusy("apply");
    setMessage(null);
    try {
      const response = await fetch(
        `/api/rag/strategy-router/recommendations/${encodeURIComponent(selected.recommendation_id)}/apply`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            expected_draft_version: selected.draft_version,
            profile_id: profile.profile_id,
            confirm_low_confidence: confirmLow,
          }),
        },
      );
      const payload = await response.json().catch(() => null);
      if (!response.ok) throw new Error(errorMessage(payload, "推荐应用失败。"));
      const updated = (payload as { recommendation: Recommendation }).recommendation;
      setItems((current) => current.map((item) => item.recommendation_id === updated.recommendation_id ? updated : item));
      setMessage({ tone: "ok", text: "已写入 Pipeline Draft，并同步画布配置。活动索引未改变。" });
      await onApplied();
    } catch (caught) {
      setMessage({ tone: "error", text: caught instanceof Error ? caught.message : "推荐应用失败。" });
    } finally {
      setBusy("");
    }
  }

  if (!open) return null;
  const canApply = Boolean(selected && profile && selected.state === "ready" && (profile.confidence !== "low" || confirmLow));
  const corpus = selected?.corpus_profile || {};

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-surface-950/70" role="dialog" aria-modal="true" aria-label="RAG 策略路由">
      <button className="absolute inset-0 cursor-default" aria-label="关闭策略路由" onClick={onClose} type="button" />
      <aside className="relative flex h-full w-full max-w-[640px] flex-col border-l border-white/10 bg-surface-950 shadow-2xl">
        <header className="flex items-start justify-between gap-4 border-b border-white/10 px-5 py-4">
          <div className="flex min-w-0 gap-3">
            <span className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-cyan-300/10 text-cyan-200"><Route aria-hidden="true" size={18} /></span>
            <div><h2 className="text-base font-semibold text-white">RAG 策略路由</h2><p className="mt-1 text-xs leading-5 text-slate-400">分析语料后推荐分块与检索配置，只写入草稿。</p></div>
          </div>
          <button className="grid h-9 w-9 shrink-0 place-items-center rounded-md text-slate-400 hover:bg-white/[0.06] hover:text-white focus:outline-none focus:ring-2 focus:ring-cyan-300/50" onClick={onClose} title="关闭" type="button"><X aria-hidden="true" size={18} /></button>
        </header>

        <div className="flex-1 overflow-y-auto">
          <section className="border-b border-white/10 px-5 py-5">
            <div className="flex items-center justify-between gap-3"><h3 className="text-sm font-semibold text-white">需求画像</h3>{capabilities ? <span className="font-mono text-[10px] text-slate-500">{capabilities.rules_version}</span> : null}</div>
            <div className="mt-3 grid grid-cols-3 gap-2" role="group" aria-label="优化目标">
              {OBJECTIVES.map((item) => <button className={`rounded-md border px-3 py-2 text-xs font-semibold ${objective === item.id ? "border-cyan-300/45 bg-cyan-300/10 text-cyan-50" : "border-white/10 text-slate-300 hover:bg-white/[0.05]"}`} key={item.id} onClick={() => setObjective(item.id)} type="button">{item.label}</button>)}
            </div>
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              {REQUIREMENTS.map((item) => <label className="flex min-h-14 cursor-pointer items-start gap-3 rounded-md border border-white/10 bg-white/[0.025] px-3 py-2 hover:bg-white/[0.05]" key={item.id}><input checked={requirements[item.id]} className="mt-1 h-4 w-4 accent-cyan-300" onChange={(event) => setRequirements((current) => ({ ...current, [item.id]: event.target.checked }))} type="checkbox" /><span><span className="block text-xs font-semibold text-slate-200">{item.label}</span><span className="mt-0.5 block text-[10px] leading-4 text-slate-500">{item.hint}</span></span></label>)}
            </div>
            <button className="mt-4 flex w-full items-center justify-center gap-2 rounded-md bg-cyan-300 px-4 py-2.5 text-sm font-bold text-surface-950 hover:bg-cyan-200 disabled:opacity-50" disabled={Boolean(busy)} onClick={() => void createRecommendation()} type="button">{busy === "recommend" ? <LoaderCircle className="animate-spin" size={16} /> : <RefreshCw size={16} />}分析并生成推荐</button>
            <p className="mt-2 text-[10px] leading-4 text-slate-500">最多分析 100 个文档、500,000 字符。不会调用模型或创建索引版本。</p>
          </section>

          {busy === "load" ? <div className="space-y-3 px-5 py-6"><div className="h-16 animate-pulse rounded-md bg-white/[0.05]" /><div className="h-32 animate-pulse rounded-md bg-white/[0.05]" /></div> : null}

          {selected ? (
            <>
              <section className="border-b border-white/10 px-5 py-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div><h3 className="text-sm font-semibold text-white">语料画像</h3><p className="mt-1 text-[10px] text-slate-500">Draft v{selected.draft_version} · {new Date(selected.created_at * 1000).toLocaleString("zh-CN", { hour12: false })}</p></div>
                  <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${selected.state === "ready" ? "bg-emerald-300/10 text-emerald-200" : selected.state === "applied" ? "bg-cyan-300/10 text-cyan-200" : "bg-amber-300/10 text-amber-200"}`}>{stateLabel(selected.state)}</span>
                </div>
                <div className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-4">
                  <Metric label="已分析文档" value={`${Number(corpus.analyzed_document_count || 0)} / ${Number(corpus.document_count || 0)}`} />
                  <Metric label="采样字符" value={Number(corpus.sampled_character_count || 0).toLocaleString()} />
                  <Metric label="结构块" value={Number(corpus.block_count || 0).toLocaleString()} />
                  <Metric label="长块比例" value={`${Math.round(Number(corpus.long_block_ratio || 0) * 100)}%`} />
                </div>
                <div className="mt-4 flex flex-wrap gap-2 text-[10px] text-slate-400">
                  <span className="rounded-full bg-white/[0.05] px-2.5 py-1">Embedding: {capabilities?.embedding.provider || "unknown"}{capabilities?.embedding.degraded ? "（降级）" : ""}</span>
                  <span className="rounded-full bg-white/[0.05] px-2.5 py-1">Rerank: {capabilities?.rerank.available ? "可用" : "未就绪"}</span>
                  <span className="rounded-full bg-white/[0.05] px-2.5 py-1">Threshold: {capabilities?.score_threshold_fixed ?? 0}</span>
                </div>
              </section>

              {selected.state === "insufficient_data" ? (
                <section className="px-5 py-5"><div className="flex gap-3 rounded-md bg-amber-300/10 p-4 text-amber-100"><AlertTriangle className="mt-0.5 shrink-0" size={17} /><div><h3 className="text-sm font-semibold">当前证据不足</h3>{selected.insufficient_reasons.map((reason) => <p className="mt-1 text-xs leading-5" key={reason}>{reason}</p>)}</div></div></section>
              ) : null}

              {profile ? (
                <>
                  <section className="border-b border-white/10 px-5 py-5">
                    <div className="flex items-center justify-between gap-3"><h3 className="text-sm font-semibold text-white">推荐方案</h3><span className="text-[11px] font-semibold text-slate-400">置信度：{profile.confidence === "medium" ? "中" : profile.confidence === "high" ? "高" : "低"}</span></div>
                    {selected.profiles.length > 1 ? <div className="mt-3 flex gap-2 overflow-x-auto pb-1">{selected.profiles.map((item) => <button className={`shrink-0 rounded-md border px-3 py-2 text-xs font-semibold ${profile.profile_id === item.profile_id ? "border-cyan-300/45 bg-cyan-300/10 text-cyan-50" : "border-white/10 text-slate-300 hover:bg-white/[0.05]"}`} key={item.profile_id} onClick={() => setProfileId(item.profile_id)} type="button">{item.title}</button>)}</div> : null}
                    <div className="mt-4 space-y-2">{profile.reasons.map((reason) => <div className="flex gap-2 text-xs leading-5 text-slate-300" key={reason}><Check className="mt-0.5 shrink-0 text-emerald-300" size={14} /><span>{reason}</span></div>)}</div>
                    <div className="mt-4 flex flex-wrap gap-2">{profile.evidence.map((item) => <span className="rounded-full border border-white/10 px-2.5 py-1 font-mono text-[10px] text-slate-400" key={`${item.rule_id}-${item.source}`}>{item.rule_id} · {item.classification} · {item.source}</span>)}</div>
                  </section>

                  <section className="border-b border-white/10 px-5 py-5">
                    <h3 className="text-sm font-semibold text-white">配置差异</h3>
                    {profile.diff.length ? <div className="mt-3 overflow-hidden rounded-md border border-white/10"><div className="grid grid-cols-[minmax(120px,0.8fr)_1fr_1fr] bg-white/[0.04] px-3 py-2 text-[10px] font-semibold text-slate-400"><span>字段</span><span>当前</span><span>推荐</span></div>{profile.diff.map((item) => <div className="grid grid-cols-[minmax(120px,0.8fr)_1fr_1fr] gap-3 border-t border-white/[0.07] px-3 py-2 text-[11px] leading-5" key={item.field}><code className="break-all text-slate-300">{item.field}</code><span className="break-all text-slate-500">{displayValue(item.current)}</span><span className="break-all font-medium text-cyan-100">{displayValue(item.recommended)}</span></div>)}</div> : <p className="mt-3 text-xs text-slate-500">当前草稿已与该方案一致。</p>}
                  </section>

                  <section className="px-5 py-5">
                    {[...selected.warnings, ...profile.warnings].filter((value, index, values) => values.indexOf(value) === index).map((warning) => <p className="mb-2 flex gap-2 text-xs leading-5 text-amber-100/90" key={warning}><AlertTriangle className="mt-0.5 shrink-0" size={14} /><span>{warning}</span></p>)}
                    {profile.confidence === "low" && selected.state === "ready" ? <label className="mt-4 flex cursor-pointer items-start gap-3 rounded-md border border-amber-300/20 bg-amber-300/[0.06] p-3 text-xs leading-5 text-amber-50"><input checked={confirmLow} className="mt-1 h-4 w-4 accent-amber-300" onChange={(event) => setConfirmLow(event.target.checked)} type="checkbox" /><span>我已检查证据与配置差异，确认将低置信方案写入草稿。</span></label> : null}
                    <button className="mt-4 flex w-full items-center justify-center gap-2 rounded-md border border-cyan-300/35 bg-cyan-300/10 px-4 py-2.5 text-sm font-semibold text-cyan-50 hover:bg-cyan-300/15 disabled:opacity-45" disabled={!canApply || Boolean(busy)} onClick={() => void applyRecommendation()} type="button">{busy === "apply" ? <LoaderCircle className="animate-spin" size={16} /> : <Check size={16} />}应用到 Pipeline Draft</button>
                    <p className="mt-2 text-center text-[10px] leading-4 text-slate-500">不会执行流水线、创建候选版本或切换活动索引。</p>
                  </section>
                </>
              ) : null}
            </>
          ) : busy !== "load" ? <div className="px-5 py-10 text-center text-sm text-slate-500">选择需求后生成第一条策略推荐。</div> : null}
        </div>

        {message ? <div className={`border-t px-5 py-3 text-xs leading-5 ${message.tone === "error" ? "border-rose-300/20 bg-rose-300/10 text-rose-100" : "border-emerald-300/20 bg-emerald-300/10 text-emerald-100"}`} aria-live="polite">{message.text}</div> : null}
      </aside>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><p className="text-[10px] text-slate-500">{label}</p><p className="mt-1 text-sm font-semibold text-slate-100">{value}</p></div>;
}
