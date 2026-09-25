import { useMemo, useState } from "react";
import { ArrowLeft, Braces, Copy, Send, ShieldCheck } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import ResourceNav from "../components/ResourceNav";
import { models } from "../data/models";

const DEFAULT_QUESTIONS = JSON.stringify(
  {
    priority: {
      type: "choice",
      instructions: "应当如何安排这项请求？",
      criteria: {
        immediate: "需要立即处理，延迟会造成明显风险或损失",
        planned: "重要但可以纳入近期计划",
        defer: "当前信息不足或优先级较低，可以暂缓",
      },
    },
  },
  null,
  2,
);

export default function DecisionPage() {
  const { modelId = "" } = useParams();
  const decodedModelId = decodeURIComponent(modelId);
  const model = models.find((item) => item.id === decodedModelId);
  const [state, setState] = useState("");
  const [questionsSource, setQuestionsSource] = useState(DEFAULT_QUESTIONS);
  const [copied, setCopied] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<unknown>(null);
  const [submitError, setSubmitError] = useState("");

  const parsedQuestions = useMemo(() => {
    try {
      const value = JSON.parse(questionsSource) as unknown;
      if (!value || Array.isArray(value) || typeof value !== "object") {
        return { value: null, error: "questions 必须是 JSON 对象。" };
      }
      return { value, error: "" };
    } catch {
      return { value: null, error: "questions 不是有效 JSON。" };
    }
  }, [questionsSource]);

  const estimatedTokens = Math.ceil((state.length + questionsSource.length) / 4);
  const inputPrice = model?.pricing.input ?? 0;
  const estimatedCost = (estimatedTokens / 1_000_000) * inputPrice;
  const requestBody = {
    model: decodedModelId,
    state,
    questions: parsedQuestions.value,
  };

  const copyRequest = async () => {
    if (!state.trim() || parsedQuestions.error) return;
    await navigator.clipboard.writeText(JSON.stringify(requestBody, null, 2));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  };

  const submitDecision = async () => {
    if (!state.trim() || parsedQuestions.error) return;
    setSubmitting(true);
    setSubmitError("");
    setResult(null);
    try {
      const response = await fetch("/api/decisions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });
      const payload = (await response.json()) as unknown;
      if (!response.ok) {
        const message =
          payload && typeof payload === "object" && "error" in payload
            ? String(payload.error)
            : `决策请求失败（HTTP ${response.status}）`;
        throw new Error(message);
      }
      setResult(payload);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "决策请求失败，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  };

  if (!model || model.ui_entrypoint !== "decisions") {
    return <Link to="/models">该模型没有可用的结构化决策工作台。</Link>;
  }

  return (
    <main className="museum-grid min-h-screen bg-ink-950 px-4 pb-16 pt-5 text-slate-100 lg:pt-24">
      <ResourceNav activeResource="models" />
      <div className="mx-auto w-full max-w-6xl">
        <Link className="inline-flex items-center gap-2 text-sm text-slate-400 hover:text-white" to="/models">
          <ArrowLeft className="h-4 w-4" />返回模型市场
        </Link>
        <section className="mt-6 grid gap-6 lg:grid-cols-[1.15fr_.85fr]">
          <div className="rounded-3xl border border-violet-300/20 bg-slate-950/75 p-6 shadow-2xl">
            <div className="flex items-center gap-3 text-violet-200">
              <Braces className="h-6 w-6" />
              <span className="text-sm font-semibold uppercase tracking-[0.18em]">结构化决策</span>
            </div>
            <h1 className="mt-4 text-3xl font-bold">{model.name}</h1>
            <p className="mt-3 text-sm leading-6 text-slate-300">
              {model.name} 不生成聊天文本。它根据共享状态回答一组类型化问题，适合路由、排序、验证和快速决策。
            </p>
            <label className="mt-7 block text-sm font-semibold" htmlFor="decision-state">状态</label>
            <textarea
              className="mt-2 min-h-44 w-full rounded-2xl border border-white/10 bg-black/25 p-4 text-sm outline-none focus:border-violet-300/50"
              id="decision-state"
              onChange={(event) => setState(event.target.value)}
              placeholder="描述需要作出判断的事实、约束和上下文……"
              value={state}
            />
            <label className="mt-5 block text-sm font-semibold" htmlFor="decision-questions">类型化问题（JSON）</label>
            <textarea
              className="mt-2 min-h-64 w-full rounded-2xl border border-white/10 bg-black/25 p-4 font-mono text-xs leading-5 outline-none focus:border-violet-300/50"
              id="decision-questions"
              onChange={(event) => setQuestionsSource(event.target.value)}
              value={questionsSource}
            />
            {parsedQuestions.error && <p className="mt-2 text-sm text-rose-300">{parsedQuestions.error}</p>}
            <div className="mt-5 flex flex-wrap gap-3">
              <button
                className="inline-flex min-h-11 items-center gap-2 rounded-full bg-violet-300 px-5 font-semibold text-slate-950 disabled:cursor-not-allowed disabled:opacity-40"
                disabled={!state.trim() || Boolean(parsedQuestions.error) || submitting}
                onClick={submitDecision}
                type="button"
              >
                <Send className="h-4 w-4" />{submitting ? "正在决策…" : "提交决策"}
              </button>
              <button
                className="inline-flex min-h-11 items-center gap-2 rounded-full border border-white/15 px-5 font-semibold text-slate-200 disabled:cursor-not-allowed disabled:opacity-40"
                disabled={!state.trim() || Boolean(parsedQuestions.error)}
                onClick={copyRequest}
                type="button"
              >
                <Copy className="h-4 w-4" />{copied ? "已复制" : "复制请求"}
              </button>
            </div>
            {submitError && <p className="mt-4 rounded-xl bg-rose-400/10 p-3 text-sm text-rose-200">{submitError}</p>}
            {result !== null && (
              <div className="mt-5">
                <h2 className="text-sm font-semibold text-violet-100">类型化结果</h2>
                <pre className="mt-2 max-h-96 overflow-auto rounded-2xl border border-violet-300/15 bg-black/30 p-4 text-xs leading-5 text-slate-200">
                  {JSON.stringify(result, null, 2)}
                </pre>
              </div>
            )}
          </div>
          <aside className="space-y-5">
            <div className="rounded-3xl border border-white/10 bg-slate-950/65 p-6">
              <h2 className="font-semibold">契约</h2>
              <dl className="mt-4 space-y-3 text-sm text-slate-300">
                <div className="flex justify-between gap-4"><dt>端点</dt><dd className="font-mono text-xs">/api/alpha/decisions</dd></div>
                <div className="flex justify-between gap-4"><dt>问题类型</dt><dd>noul / choice / score</dd></div>
                <div className="flex justify-between gap-4"><dt>输出</dt><dd>类型化 answers</dd></div>
                <div className="flex justify-between gap-4"><dt>上下文</dt><dd>{model.context_length.toLocaleString()} Token</dd></div>
              </dl>
            </div>
            <div className="rounded-3xl border border-emerald-300/20 bg-emerald-300/[0.06] p-6">
              <div className="flex items-center gap-2 text-emerald-200"><ShieldCheck className="h-5 w-5" /><h2 className="font-semibold">费用预估</h2></div>
              <p className="mt-3 text-2xl font-bold">约 ${estimatedCost.toFixed(6)}</p>
              <p className="mt-2 text-sm text-slate-300">按约 {estimatedTokens.toLocaleString()} 输入 Token、${inputPrice.toFixed(3)} / M 估算；输出定价为 $0。</p>
            </div>
            <p className="rounded-2xl border border-amber-300/20 bg-amber-300/[0.05] p-4 text-sm leading-6 text-amber-100">
              提交会将这里填写的状态与问题发送至 OpenRouter Decisions API。请勿填写密钥或未经授权的敏感数据。
            </p>
          </aside>
        </section>
      </div>
    </main>
  );
}
