import { useCallback, useEffect, useMemo, useState } from "react";
import { FileText, FlaskConical, RefreshCw, Square } from "lucide-react";
import type {
  AgentThinkingLevel,
  AgentWorkspaceEntry,
  EngineShadowRun,
} from "../../types/agentWorkspace";
import {
  connectEngineShadowEvents,
  createEngineShadowRun,
  listEngineShadowRuns,
  listEngineShadowWorkspace,
  readEngineShadowRun,
  readEngineShadowWorkspaceFile,
  stopEngineShadowRun,
} from "../../utils/agentWorkspaceApi";

const ACTIVE_STATUSES = new Set(["pending", "running"]);

function publicStatus(run: EngineShadowRun): string {
  if (run.status === "candidate_ready") return "候选已就绪（未验收）";
  const labels: Record<string, string> = {
    pending: "等待启动",
    running: "运行中",
    blocked: "已阻塞",
    budget_limited: "预算已用尽",
    stopped: "已停止",
    interrupted: "已中断",
    failed: "失败",
  };
  return labels[run.status] ?? run.status;
}

export default function EngineShadowPanel() {
  const [objective, setObjective] = useState("");
  const [modelBaseId, setModelBaseId] = useState("deepseek-v4-flash-0731");
  const [thinkingLevel, setThinkingLevel] =
    useState<AgentThinkingLevel>("medium");
  const [tokenBudget, setTokenBudget] = useState(750_000);
  const [runs, setRuns] = useState<EngineShadowRun[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<EngineShadowRun | null>(null);
  const [entries, setEntries] = useState<AgentWorkspaceEntry[]>([]);
  const [preview, setPreview] = useState<{ path: string; content: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refreshRuns = useCallback(async (preferred?: string) => {
    const next = await listEngineShadowRuns();
    setRuns(next);
    setSelectedId((current) => preferred ?? current ?? next[0]?.run_id ?? null);
  }, []);

  const refreshSelected = useCallback(async (runId: string) => {
    const [detail, workspace] = await Promise.all([
      readEngineShadowRun(runId),
      listEngineShadowWorkspace(runId),
    ]);
    setSelected(detail.run);
    setEntries(workspace);
  }, []);

  useEffect(() => {
    void refreshRuns().catch((caught) =>
      setError(caught instanceof Error ? caught.message : "无法加载影子运行"),
    );
  }, [refreshRuns]);

  useEffect(() => {
    if (!selectedId) {
      setSelected(null);
      setEntries([]);
      return;
    }
    setPreview(null);
    void refreshSelected(selectedId).catch((caught) =>
      setError(caught instanceof Error ? caught.message : "无法加载影子运行"),
    );
    return connectEngineShadowEvents(selectedId, 0, {
      onEvent: () => {
        void refreshSelected(selectedId);
        void refreshRuns(selectedId);
      },
      onTransportError: () => {
        window.setTimeout(() => void refreshSelected(selectedId), 1_000);
      },
    });
  }, [refreshRuns, refreshSelected, selectedId]);

  const progress = useMemo(() => {
    if (!selected) return "";
    return [
      `${selected.goal_round}/${selected.max_goal_rounds} Goal Round`,
      `${selected.token_total.toLocaleString()}/${selected.token_budget.toLocaleString()} Token`,
      `${selected.model_turns} 次模型轮次`,
      `${selected.tool_calls} 次安全工具调用`,
      ...(selected.retry_count > 0 ? [`${selected.retry_count} 次 Worker 重试`] : []),
      ...(selected.tool_failures > 0 ? [`${selected.tool_failures} 次工具失败`] : []),
    ].join(" · ");
  }, [selected]);

  async function handleStart() {
    if (!objective.trim()) return;
    setBusy(true);
    setError("");
    try {
      const run = await createEngineShadowRun({
        objective: objective.trim(),
        model_base_id: modelBaseId.trim(),
        thinking_level: thinkingLevel,
        token_budget: tokenBudget,
      });
      setObjective("");
      await refreshRuns(run.run_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "启动失败");
    } finally {
      setBusy(false);
    }
  }

  async function handleStop() {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      await stopEngineShadowRun(selected.run_id);
      await refreshSelected(selected.run_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "停止失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section
      className="mb-4 overflow-hidden rounded-xl border border-violet-300/20 bg-violet-950/20"
      data-testid="engine-shadow-panel"
    >
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-white/10 px-4 py-3">
        <div className="flex gap-3">
          <FlaskConical className="mt-0.5 text-violet-200" size={20} />
          <div>
            <h2 className="font-semibold text-white">上游内核影子构建</h2>
            <p className="mt-1 text-xs text-slate-400">
              固定 Penguin Core 执行 Goal；只生成隔离候选，不验收、不发布，也不创建 App、版本或 Artifact。
            </p>
          </div>
        </div>
        <button
          aria-label="刷新影子运行"
          className="rounded-md border border-white/10 p-2 text-slate-300 hover:bg-white/5"
          onClick={() => void refreshRuns(selectedId ?? undefined)}
          type="button"
        >
          <RefreshCw size={14} />
        </button>
      </header>

      <div className="grid gap-4 p-4 xl:grid-cols-[320px_minmax(0,1fr)_300px]">
        <div>
          <textarea
            aria-label="影子构建目标"
            className="min-h-24 w-full rounded-md border border-white/10 bg-slate-950/70 p-3 text-sm text-white"
            onChange={(event) => setObjective(event.target.value)}
            placeholder="描述需要构建的候选应用"
            value={objective}
          />
          <div className="mt-2 grid grid-cols-2 gap-2">
            <input
              aria-label="模型 base id"
              className="col-span-2 rounded-md border border-white/10 bg-slate-950 px-3 py-2 text-xs text-white"
              onChange={(event) => setModelBaseId(event.target.value)}
              value={modelBaseId}
            />
            <select
              aria-label="影子思考等级"
              className="rounded-md border border-white/10 bg-slate-950 px-2 py-2 text-xs text-white"
              onChange={(event) =>
                setThinkingLevel(event.target.value as AgentThinkingLevel)
              }
              value={thinkingLevel}
            >
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="xhigh">xhigh</option>
            </select>
            <input
              aria-label="Token 预算"
              className="rounded-md border border-white/10 bg-slate-950 px-3 py-2 text-xs text-white"
              max={1_000_000}
              min={100_000}
              onChange={(event) => setTokenBudget(Number(event.target.value))}
              step={50_000}
              type="number"
              value={tokenBudget}
            />
          </div>
          <button
            className="mt-2 w-full rounded-md bg-violet-300 px-3 py-2 text-sm font-semibold text-slate-950 disabled:opacity-40"
            disabled={busy || !objective.trim()}
            onClick={() => void handleStart()}
            type="button"
          >
            启动影子构建
          </button>
          <div className="mt-3 max-h-40 space-y-1 overflow-auto">
            {runs.map((run) => (
              <button
                className={`w-full rounded-md border px-2 py-2 text-left text-xs ${
                  selectedId === run.run_id
                    ? "border-violet-300/50 bg-violet-300/10"
                    : "border-white/10"
                }`}
                key={run.run_id}
                onClick={() => setSelectedId(run.run_id)}
                type="button"
              >
                <span className="block truncate text-slate-200">{run.objective}</span>
                <span className="text-slate-500">
                  {publicStatus(run)} · Round {run.goal_round}/{run.max_goal_rounds}
                </span>
              </button>
            ))}
          </div>
        </div>

        <div className="min-w-0 rounded-md border border-white/10 bg-slate-950/40 p-4">
          {selected ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-xs text-slate-500">{selected.run_id}</p>
                  <p className="mt-1 text-lg font-semibold text-white">{publicStatus(selected)}</p>
                </div>
                {ACTIVE_STATUSES.has(selected.status) ? (
                  <button
                    className="inline-flex items-center gap-1 rounded-md border border-rose-300/30 px-2 py-1 text-xs text-rose-100"
                    disabled={busy}
                    onClick={() => void handleStop()}
                    type="button"
                  >
                    <Square size={12} />停止
                  </button>
                ) : null}
              </div>
              <p className="mt-3 text-sm text-slate-300">{progress}</p>
              <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
                <div>
                  <dt className="text-slate-500">候选 SHA-256</dt>
                  <dd className="mt-1 break-all text-slate-300">
                    {selected.candidate_sha256 || "—"}
                  </dd>
                </div>
                <div>
                  <dt className="text-slate-500">上游 revision</dt>
                  <dd className="mt-1 break-all text-slate-300">{selected.upstream_revision}</dd>
                </div>
              </dl>
              {selected.public_error ? (
                <p className="mt-3 rounded-md border border-rose-300/20 bg-rose-300/10 p-2 text-xs text-rose-100">
                  {selected.public_error}
                </p>
              ) : null}
              <p className="mt-4 text-xs text-amber-100/80">
                此功能不会调用 Browser，也不会创建 App、Version、Artifact 或发布记录。
              </p>
            </>
          ) : (
            <p className="text-sm text-slate-500">选择或启动一次影子构建。</p>
          )}
        </div>

        <div className="min-w-0 rounded-md border border-white/10 bg-slate-950/40 p-3">
          <h3 className="text-sm font-semibold text-white">Shadow Workspace</h3>
          <p className="mt-1 text-[11px] text-slate-500">只读候选文件</p>
          <div className="mt-3 space-y-1">
            {entries.map((entry) => (
              <button
                className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs text-slate-300 hover:bg-white/5 disabled:opacity-50"
                disabled={entry.kind !== "file"}
                key={entry.path}
                onClick={() => {
                  if (!selected) return;
                  void readEngineShadowWorkspaceFile(selected.run_id, entry.path)
                    .then((file) => setPreview({ path: file.path, content: file.content }))
                    .catch((caught) =>
                      setError(caught instanceof Error ? caught.message : "无法读取候选文件"),
                    );
                }}
                type="button"
              >
                <FileText size={13} />{entry.name}
              </button>
            ))}
          </div>
          {preview ? (
            <div className="mt-3">
              <p className="mb-1 truncate text-[11px] text-slate-500">{preview.path}</p>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-black/30 p-2 text-[11px] text-slate-300">
                {preview.content}
              </pre>
            </div>
          ) : null}
        </div>
      </div>

      {error ? (
        <p
          className="border-t border-rose-300/20 bg-rose-300/10 px-4 py-2 text-xs text-rose-100"
          role="alert"
        >
          {error}
        </p>
      ) : null}
    </section>
  );
}
