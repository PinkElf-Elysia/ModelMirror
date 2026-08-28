import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import type { Model, ModelServingVariant } from "../data/models";
import {
  batchResultText,
  fetchOpenRouterBatch,
  submitOpenRouterBatch,
  TERMINAL_BATCH_STATUSES,
  type OpenRouterBatchJob,
  type OpenRouterBatchStatus,
} from "../data/openrouterBatch";
import BrandLogo from "./BrandLogo";
import ResourceNav from "./ResourceNav";

interface OpenRouterBatchWorkspaceProps {
  model: Model;
  variant: ModelServingVariant;
}

interface RequestDraft {
  id: number;
  prompt: string;
}

const statusLabels: Record<OpenRouterBatchStatus, string> = {
  submitting: "正在提交",
  validating: "正在校验",
  in_progress: "后台处理中",
  finalizing: "正在汇总",
  completed: "已完成",
  failed: "处理失败",
  expired: "已过期",
  cancelling: "正在取消",
  cancelled: "已取消",
  uncertain: "提交结果待确认",
};

function storageKey(modelId: string) {
  return `modelmirror-openrouter-batch:${modelId}`;
}

function pendingIdempotencyKey(modelId: string) {
  return `modelmirror-openrouter-batch-pending:${modelId}`;
}

function initialDrafts(): RequestDraft[] {
  return [
    { id: 1, prompt: "" },
    { id: 2, prompt: "" },
  ];
}

function formatCny(value: number) {
  return `¥${value.toFixed(2)}`;
}

function formatUsd(value: number | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "待上游结算";
  return `$${value.toFixed(value < 0.01 ? 4 : 2)}`;
}

export default function OpenRouterBatchWorkspace({
  model,
  variant,
}: OpenRouterBatchWorkspaceProps) {
  const [drafts, setDrafts] = useState<RequestDraft[]>(initialDrafts);
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(2048);
  const [job, setJob] = useState<OpenRouterBatchJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [polling, setPolling] = useState(false);
  const [error, setError] = useState("");
  const [pendingKey, setPendingKey] = useState(() =>
    window.localStorage.getItem(pendingIdempotencyKey(model.id)),
  );
  const isEmbedding = variant.endpoint === "/v1/embeddings";
  const realtimeTarget = model.ui_entrypoint === "rag"
    ? "/rag"
    : `/chat/${encodeURIComponent(model.id)}`;
  const validRequests = useMemo(
    () => drafts.filter((draft) => draft.prompt.trim()),
    [drafts],
  );

  useEffect(() => {
    const storedBatchId = window.localStorage.getItem(storageKey(model.id));
    if (!storedBatchId) return;
    let cancelled = false;
    let controller: AbortController | null = null;
    let retryTimer: number | undefined;

    const restore = async () => {
      controller = new AbortController();
      setPolling(true);
      try {
        const payload = await fetchOpenRouterBatch(storedBatchId, controller.signal);
        if (!cancelled) {
          setJob(payload);
          setError("");
        }
      } catch {
        if (!cancelled) {
          setError(
            `暂时无法恢复已保存的批处理任务 ${storedBatchId}，任务编号仍保留在本机。`,
          );
          retryTimer = window.setTimeout(() => void restore(), 5_000);
        }
      } finally {
        if (!cancelled) setPolling(false);
      }
    };

    void restore();
    return () => {
      cancelled = true;
      controller?.abort();
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    };
  }, [model.id]);

  useEffect(() => {
    if (!job || TERMINAL_BATCH_STATUSES.has(job.status)) return;
    let cancelled = false;
    let controller: AbortController | null = null;
    let timer: number | undefined;

    const schedule = () => {
      timer = window.setTimeout(() => void poll(), 5_000);
    };
    const poll = async () => {
      controller = new AbortController();
      setPolling(true);
      try {
        const payload = await fetchOpenRouterBatch(job.id, controller.signal);
        if (!cancelled) {
          setJob(payload);
          setError("");
        }
      } catch (cause) {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : "批处理状态刷新失败。");
        }
      } finally {
        if (!cancelled) {
          setPolling(false);
          schedule();
        }
      }
    };

    schedule();
    return () => {
      cancelled = true;
      controller?.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [job?.id, job?.status]);

  function updateDraft(id: number, prompt: string) {
    setDrafts((current) =>
      current.map((draft) => (draft.id === id ? { ...draft, prompt } : draft)),
    );
  }

  function addDraft() {
    setDrafts((current) => [
      ...current,
      {
        id: Math.max(0, ...current.map((draft) => draft.id)) + 1,
        prompt: "",
      },
    ]);
  }

  function removeDraft(id: number) {
    setDrafts((current) =>
      current.length > 1 ? current.filter((draft) => draft.id !== id) : current,
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (validRequests.length === 0) {
      setError("至少填写一条批处理请求。");
      return;
    }
    setBusy(true);
    setError("");
    const idempotencyKey = pendingKey ?? window.crypto.randomUUID();
    if (!pendingKey) {
      setPendingKey(idempotencyKey);
      window.localStorage.setItem(
        pendingIdempotencyKey(model.id),
        idempotencyKey,
      );
    }
    try {
      const payload = await submitOpenRouterBatch({
        model_id: variant.request_model_id,
        endpoint: variant.endpoint as
          | "/v1/chat/completions"
          | "/v1/embeddings",
        requests: validRequests.map((request, index) => ({
          custom_id: `request-${index + 1}`,
          input: request.prompt.trim(),
        })),
        ...(isEmbedding ? {} : { temperature, max_tokens: maxTokens }),
      }, idempotencyKey);
      setJob(payload);
      window.localStorage.setItem(storageKey(model.id), payload.id);
      setPendingKey(null);
      window.localStorage.removeItem(pendingIdempotencyKey(model.id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "批处理提交失败。");
    } finally {
      setBusy(false);
    }
  }

  function startNewBatch() {
    setJob(null);
    setDrafts(initialDrafts());
    setError("");
    window.localStorage.removeItem(storageKey(model.id));
    clearPendingKey();
  }

  function clearPendingKey() {
    setPendingKey(null);
    window.localStorage.removeItem(pendingIdempotencyKey(model.id));
  }

  return (
    <div className="min-h-screen bg-ink-950 text-slate-100">
      <header className="border-b border-white/10 bg-ink-950/90 px-4 py-4 backdrop-blur-xl sm:px-6">
        <div className="mx-auto flex max-w-[1680px] items-center justify-between gap-4">
          <BrandLogo compact />
          <ResourceNav activeResource="models" />
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl gap-6 px-4 py-8 lg:grid-cols-[minmax(0,1fr)_340px]">
        <section className="rounded-xl border border-sky-300/20 bg-white/[0.045] p-5 sm:p-7">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="max-w-2xl">
              <p className="text-sm font-semibold text-sky-200">异步批处理</p>
              <h1 className="mt-2 text-3xl font-semibold text-white">{model.name}</h1>
              <p className="mt-3 text-sm leading-6 text-slate-400">
                每条输入会作为独立请求提交。任务在后台处理，本页每 5 秒刷新一次状态。
              </p>
            </div>
            <Link className="text-sm text-slate-300 hover:text-white" to="/models">
              返回模型招聘会
            </Link>
          </div>

          <div className="mt-6 inline-flex rounded-lg border border-white/10 bg-ink-950/65 p-1" aria-label="调用模式">
            <Link
              className="rounded-md px-4 py-2 text-sm font-semibold text-slate-300 transition hover:bg-white/[0.06] hover:text-white"
              to={realtimeTarget}
            >
              {model.ui_entrypoint === "rag" ? "资料库调用" : "实时调用"}
            </Link>
            <span className="rounded-md bg-sky-300 px-4 py-2 text-sm font-semibold text-ink-950">
              批量处理
            </span>
          </div>

          {error ? (
            <p className="mt-6 rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-sm text-rose-100" role="alert">
              {error}
            </p>
          ) : null}

          {!job && pendingKey ? (
            <div className="mt-4 rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 py-3 text-sm text-amber-50">
              <p>存在一项待确认提交。再次提交会复用原请求标识，便于 Managed 模式识别重复提交。</p>
              <button
                className="mt-2 text-xs font-semibold text-amber-100 underline decoration-amber-200/50 underline-offset-4"
                onClick={clearPendingKey}
                type="button"
              >
                放弃待确认提交并创建新任务
              </button>
            </div>
          ) : null}

          {job ? (
            <div className="mt-7" aria-live="polite">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-4">
                <div>
                  <p className="text-xs text-slate-500">任务编号</p>
                  <p className="mt-1 break-all font-mono text-sm text-slate-200">{job.id}</p>
                </div>
                <span className="rounded-full border border-sky-300/30 bg-sky-300/10 px-3 py-1.5 text-xs font-semibold text-sky-100">
                  {statusLabels[job.status]}
                  {polling ? " · 刷新中" : ""}
                </span>
              </div>

              <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-3">
                <div className="rounded-lg bg-white/[0.045] p-3">
                  <dt className="text-xs text-slate-500">请求进度</dt>
                  <dd className="mt-1 font-semibold text-white">
                    {job.request_counts.completed + job.request_counts.failed} / {job.request_counts.total}
                  </dd>
                </div>
                <div className="rounded-lg bg-white/[0.045] p-3">
                  <dt className="text-xs text-slate-500">失败请求</dt>
                  <dd className="mt-1 font-semibold text-white">{job.request_counts.failed}</dd>
                </div>
                <div className="rounded-lg bg-white/[0.045] p-3">
                  <dt className="text-xs text-slate-500">实际费用</dt>
                  <dd className="mt-1 font-semibold text-white">{formatUsd(job.usage?.cost)}</dd>
                </div>
              </dl>

              {job.results?.length ? (
                <div className="mt-6 space-y-4">
                  {job.results.map((result) => (
                    <article className="rounded-lg bg-ink-950/60 p-4" key={result.id}>
                      <div className="flex items-center justify-between gap-3">
                        <h2 className="text-sm font-semibold text-white">{result.custom_id}</h2>
                        <span className="text-xs text-slate-500">
                          HTTP {result.response?.status_code ?? "错误"}
                        </span>
                      </div>
                      <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-300">
                        {batchResultText(result)}
                      </p>
                    </article>
                  ))}
                </div>
              ) : (
                <p className="mt-6 text-sm leading-6 text-slate-400">
                  {job.status === "uncertain"
                    ? "提交结果暂时无法确认。ModelMirror 已阻止同一幂等键再次发送，不会自动产生第二个 Batch。"
                    : "结果只会在任务完成后返回。你可以离开页面，稍后通过本机保存的任务编号继续查看。"}
                </p>
              )}

              {TERMINAL_BATCH_STATUSES.has(job.status) ? (
                <button
                  className="mt-6 rounded-lg border border-white/10 px-4 py-2.5 text-sm font-semibold text-slate-200 transition hover:bg-white/[0.06]"
                  onClick={startNewBatch}
                  type="button"
                >
                  创建新任务
                </button>
              ) : null}
            </div>
          ) : (
            <form className="mt-7 space-y-5" onSubmit={submit}>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold text-white">批处理请求</h2>
                  <p className="mt-1 text-xs text-slate-500">当前 {validRequests.length} 条有效输入，最多 100 条。</p>
                </div>
                <button
                  className="rounded-lg border border-white/10 px-3 py-2 text-xs font-semibold text-slate-200 transition hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:text-slate-600"
                  disabled={drafts.length >= 100}
                  onClick={addDraft}
                  type="button"
                >
                  添加请求
                </button>
              </div>

              <div className="space-y-3">
                {drafts.map((draft, index) => (
                  <label className="block rounded-lg bg-ink-950/55 p-3" key={draft.id}>
                    <span className="flex items-center justify-between gap-3 text-xs font-semibold text-slate-300">
                      请求 {index + 1}
                      <button
                        className="font-normal text-slate-500 transition hover:text-rose-200 disabled:cursor-not-allowed disabled:text-slate-700"
                        disabled={drafts.length === 1}
                        onClick={() => removeDraft(draft.id)}
                        type="button"
                      >
                        移除
                      </button>
                    </span>
                    <textarea
                      className="mt-2 min-h-28 w-full resize-y rounded-lg border border-white/10 bg-ink-950/80 px-3 py-2.5 text-sm leading-6 text-white outline-none placeholder:text-slate-500 focus:border-sky-300/55"
                      maxLength={20_000}
                      onChange={(event) => updateDraft(draft.id, event.target.value)}
                      placeholder={isEmbedding ? "输入需要生成向量的文本" : "输入需要分类、摘要、提取或评测的文本任务"}
                      value={draft.prompt}
                    />
                  </label>
                ))}
              </div>

              {!isEmbedding ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="text-xs font-semibold text-slate-300">
                    温度
                    <input
                      className="mt-2 min-h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-sm text-white outline-none focus:border-sky-300/55"
                      max="2"
                      min="0"
                      onChange={(event) => setTemperature(Number(event.target.value))}
                      step="0.1"
                      type="number"
                      value={temperature}
                    />
                  </label>
                  <label className="text-xs font-semibold text-slate-300">
                    最大输出 Token
                    <input
                      className="mt-2 min-h-11 w-full rounded-lg border border-white/10 bg-ink-950/80 px-3 text-sm text-white outline-none focus:border-sky-300/55"
                      max="128000"
                      min="1"
                      onChange={(event) => setMaxTokens(Number(event.target.value))}
                      type="number"
                      value={maxTokens}
                    />
                  </label>
                </div>
              ) : null}

              <button
                className="min-h-11 rounded-lg bg-sky-300 px-5 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-sky-200 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-slate-500"
                disabled={busy || validRequests.length === 0}
                type="submit"
              >
                {busy ? "正在提交" : "提交批处理任务"}
              </button>
            </form>
          )}
        </section>

        <aside className="space-y-5">
          <section className="rounded-xl border border-white/10 bg-white/[0.04] p-5">
            <h2 className="text-sm font-semibold text-white">服务档位</h2>
            <dl className="mt-4 space-y-3 text-sm">
              <div>
                <dt className="text-slate-500">目录 ID</dt>
                <dd className="mt-1 break-all font-mono text-[11px] text-slate-300">{variant.catalog_id}</dd>
              </div>
              <div>
                <dt className="text-slate-500">请求模型</dt>
                <dd className="mt-1 break-all font-mono text-[11px] text-slate-300">{variant.request_model_id}</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-slate-500">输入价格</dt>
                <dd className="font-semibold text-sky-100">{formatCny(variant.price_cny.input)} / M</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-slate-500">输出价格</dt>
                <dd className="font-semibold text-sky-100">{formatCny(variant.price_cny.output)} / M</dd>
              </div>
              <div className="flex items-center justify-between gap-3">
                <dt className="text-slate-500">处理窗口</dt>
                <dd className="font-semibold text-slate-200">最多约 24 小时</dd>
              </div>
            </dl>
          </section>

          <section className="rounded-xl border border-amber-300/25 bg-amber-300/10 p-5">
            <h2 className="text-sm font-semibold text-amber-100">数据与能力边界</h2>
            <ul className="mt-3 space-y-2 text-xs leading-5 text-amber-50/85">
              <li>当前批处理只接受文本输入，不发送图片、音频、视频或文件。</li>
              <li>任务异步执行，不提供流式输出，也不适合实时工具循环。</li>
              <li>OpenRouter 会保留 Batch 输入与结果 30 天，请勿提交不应离开本地的敏感材料。</li>
              <li>ModelMirror 只保存本地任务映射和脱敏状态，不保存输入或结果正文。</li>
              <li>返回的 usage/cost 仅为 Provider 报告元数据，不构成 ModelMirror 计费依据。</li>
            </ul>
          </section>
        </aside>
      </main>
    </div>
  );
}
