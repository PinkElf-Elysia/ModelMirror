import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import ResourceNav from "../components/ResourceNav";
import { models } from "../data/models";

export function parseEntities(source: string): Record<string, string> | null {
  try {
    const value: unknown = JSON.parse(source);
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const entries = Object.entries(value);
    if (entries.length < 1 || entries.length > 50) return null;
    if (entries.some(([key, text]) => !key.trim() || key.length > 80 || typeof text !== "string" || !text.trim() || text.length > 1000)) return null;
    return value as Record<string, string>;
  } catch { return null; }
}

export default function ScaleDownPage() {
  const { modelId = "" } = useParams();
  // React Router has already decoded the route parameter.
  return <ScaleDownWorkspace key={modelId} modelId={modelId} />;
}

function ScaleDownWorkspace({ modelId }: { modelId: string }) {
  const model = models.find((item) => item.id === modelId);
  const extraction = modelId === "scaledown/extract";
  const [text, setText] = useState("");
  const [instructions, setInstructions] = useState("");
  const [entitiesSource, setEntitiesSource] = useState('{\n  "person": "The full name of a person mentioned in the source"\n}');
  const outputLimit = extraction ? 4000 : 1000;
  const [maxTokens, setMaxTokens] = useState(extraction ? 2000 : 1000);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const entities = extraction ? parseEntities(entitiesSource) : null;
  const valid = Boolean(text.trim()) && text.length <= 100000 && (!extraction || entities !== null)
    && Number.isInteger(maxTokens) && maxTokens >= 1 && maxTokens <= outputLimit;
  const estimatedTokens = Math.ceil((text.length + instructions.length + (extraction ? entitiesSource.length : 0)) / 4);
  const cost = estimatedTokens * (model?.pricing.input ?? 0) / 1_000_000;
  const submit = async () => {
    if (!valid || controller.current) return;
    const current = new AbortController();
    controller.current = current;
    setPending(true); setError(""); setResult("");
    try {
      const response = await fetch("/api/scaledown", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: current.signal,
        body: JSON.stringify({ model: modelId, text, instructions, max_tokens: maxTokens, ...(extraction ? { entities } : {}) }),
      });
      const body = await response.json() as { content?: unknown; error?: string };
      if (!response.ok) throw new Error(body.error || `请求失败（HTTP ${response.status}）`);
      if (typeof body.content !== "string" || !body.content.trim()) throw new Error("没有收到有效文字结果。");
      setResult(body.content);
    } catch (reason) {
      if (!current.signal.aborted) setError(reason instanceof Error ? reason.message : "请求失败；不会自动重试。");
    } finally {
      if (!current.signal.aborted) { setPending(false); controller.current = null; }
    }
  };
  if (!model || model.ui_entrypoint !== "scaledown") return <Link to="/models">返回模型市场：该模型不支持此工作台。</Link>;
  const inputClass = "mt-2 w-full rounded-xl border border-white/15 bg-ink-950 p-3 text-sm text-slate-100 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-violet-300";
  return (
    <main className="museum-grid min-h-screen bg-ink-950 px-4 pb-16 pt-5 text-slate-100 lg:pt-24">
      <ResourceNav activeResource="models" />
      <div className="mx-auto max-w-6xl">
        <Link to="/models" className="text-sm text-slate-300 hover:text-white">返回模型市场</Link>
        <h1 className="mt-5 break-words text-3xl font-bold">{model.name}</h1>
        <p className="mt-3 max-w-prose text-sm leading-6 text-slate-300">{extraction ? "描述需要的字段，从原文中提取结构化信息。" : "把长文整理为摘要，可指定重点、语言与格式。"}提交后一次性返回结果，不使用聊天流。</p>
        <div className="mt-6 grid gap-8 lg:grid-cols-[minmax(0,1fr)_20rem]">
          <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
            <fieldset disabled={pending} className="space-y-5 disabled:opacity-70">
              <div><label htmlFor="scaledown-text" className="font-semibold">原文</label>
                <textarea id="scaledown-text" className={`${inputClass} min-h-64`} maxLength={100000} value={text} onChange={(e) => setText(e.target.value)} placeholder="粘贴需要处理的文字……" required />
                <p className="mt-1 text-xs text-slate-300">{text.length.toLocaleString()} / 100,000 字符</p></div>
              <div><label htmlFor="scaledown-instructions" className="font-semibold">处理要求（可选）</label>
                <textarea id="scaledown-instructions" className={`${inputClass} min-h-24`} maxLength={4000} value={instructions} onChange={(e) => setInstructions(e.target.value)} placeholder={extraction ? "例如：只提取明确写出的事实，不推测缺失信息。" : "例如：用中文概括主要结论，保留关键数据。"} /></div>
              {extraction && <div><label htmlFor="scaledown-entities" className="font-semibold">提取字段（JSON）</label>
                <p id="entities-help" className="mt-1 text-sm text-slate-300">填写字段名与描述；建议使用英文描述。最多 50 个字段，名称最长 80 字符、描述最长 1,000 字符。</p>
                <textarea id="scaledown-entities" aria-describedby="entities-help" aria-invalid={!entities} className={`${inputClass} min-h-40 font-mono`} value={entitiesSource} onChange={(e) => setEntitiesSource(e.target.value)} />
                {!entities && <p role="alert" className="mt-2 text-sm text-rose-300">请填写有效的非空 JSON 对象，每个字段的描述必须是文字。</p>}</div>}
              <div><label htmlFor="scaledown-max-tokens" className="font-semibold">最大输出 Token</label>
                <input id="scaledown-max-tokens" className={inputClass} type="number" min={1} max={outputLimit} step={1} value={Number.isNaN(maxTokens) ? "" : maxTokens} onChange={(e) => setMaxTokens(e.target.valueAsNumber)} required />
                <p className="mt-1 text-xs text-slate-300">{extraction ? "本站单次上限 4,000；提取较多字段时请分段提交。" : "摘要服务单次输出上限为 1,000 Token。"}</p></div>
              <button type="submit" disabled={!valid || pending} className="min-h-11 rounded-full bg-violet-300 px-6 font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-40">{pending ? "处理中…" : extraction ? "提取字段" : "生成摘要"}</button>
            </fieldset>
            {error && <p role="alert" className="mt-4 rounded-xl bg-rose-400/10 p-4 text-rose-200">{error}</p>}
            <section aria-live="polite" aria-busy={pending} className="mt-6">
              <h2 className="text-lg font-semibold">处理结果</h2>
              {result ? <pre className="mt-3 max-h-[36rem] overflow-auto whitespace-pre-wrap break-words rounded-xl border border-white/15 p-4 text-sm">{result}</pre> : <p className="mt-2 text-sm text-slate-300">{pending ? "正在等待结果，请勿重复提交。" : "提交后在此查看摘要或提取结果。"}</p>}
            </section>
          </form>
          <aside className="space-y-6 text-sm leading-6 text-slate-300">
            <section className="rounded-xl border border-white/15 p-5"><h2 className="text-lg font-semibold text-white">费用预估</h2><p className="mt-3 text-xl font-semibold text-violet-200">约 ${cost.toFixed(6)}</p><p className="mt-2">按约 {estimatedTokens.toLocaleString()} 输入 Token、${model.pricing.input} / M 估算。输出定价为 $0。</p><p className="mt-2">字符换算仅供参考，实际 Token 与账单以上游为准。</p></section>
            <section className="rounded-xl border border-amber-300/25 p-5"><h2 className="font-semibold text-amber-200">使用范围</h2><p className="mt-2">首轮仅支持文字。图片与 PDF 尚未适配，请勿粘贴文件编码。</p><p className="mt-2">提交内容会发送至 OpenRouter 的 ScaleDown 服务，请勿输入密钥或未经授权的敏感材料。</p><p className="mt-2">失败或超时不会自动重试；离开页面不保证上游任务取消。</p></section>
          </aside>
        </div>
      </div>
    </main>
  );
}
