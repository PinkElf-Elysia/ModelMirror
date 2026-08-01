import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import type { XpertSummary } from "../types/xpert";
import { listXperts } from "../utils/xpertApi";

type AutomationStatus = "draft" | "scheduled" | "paused" | "archived";
type ExecutionStatus =
  | "pending"
  | "running"
  | "waiting_approval"
  | "waiting_client"
  | "completed"
  | "failed"
  | "dead_letter"
  | "skipped"
  | "budget_limited"
  | "cancelled";

interface AutomationTrigger {
  type: "once" | "interval" | "cron";
  once_at?: number | null;
  interval_seconds?: number | null;
  cron?: string | null;
  timezone: string;
}

interface AutomationDefinition {
  automation_id: string;
  name: string;
  prompt?: string;
  target_xpert_id: string;
  target_xpert_slug: string;
  target_xpert_version: number;
  trigger: AutomationTrigger;
  status: AutomationStatus;
  revision: number;
  next_run_at: number | null;
  last_run_at: number | null;
  updated_at: number;
  executions?: AutomationExecution[];
}

interface AutomationExecution {
  execution_id: string;
  automation_id: string;
  status: ExecutionStatus;
  attempt: number;
  scheduled_at: number;
  available_at: number;
  run_id: string | null;
  workflow_task_id: string | null;
  wait_kind: string | null;
  wait_id: string | null;
  result: string | null;
  error: string | null;
  updated_at: number;
}

interface AutomationListResponse {
  items: AutomationDefinition[];
}

interface ExecutionListResponse {
  items: AutomationExecution[];
}

async function responseError(response: Response) {
  try {
    const payload = (await response.json()) as { detail?: string; error?: string };
    return payload.detail || payload.error || `请求失败：${response.status}`;
  } catch {
    return `请求失败：${response.status}`;
  }
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error(await responseError(response));
  return response.json() as Promise<T>;
}

function formatTime(value: number | null | undefined) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value * 1000));
}

function statusTone(status: string) {
  if (status === "completed" || status === "scheduled") {
    return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  }
  if (status === "running") return "border-cyan-300/25 bg-cyan-300/10 text-cyan-100";
  if (status.startsWith("waiting")) return "border-amber-300/25 bg-amber-300/10 text-amber-100";
  if (["failed", "dead_letter", "budget_limited"].includes(status)) {
    return "border-rose-300/25 bg-rose-300/10 text-rose-100";
  }
  return "border-white/10 bg-white/[0.05] text-slate-300";
}

function triggerLabel(trigger: AutomationTrigger) {
  if (trigger.type === "once") return `单次 · ${formatTime(trigger.once_at)}`;
  if (trigger.type === "interval") return `间隔 · ${trigger.interval_seconds ?? 0} 秒`;
  return `Cron · ${trigger.cron || "-"} · ${trigger.timezone}`;
}

export default function AutomationsPage() {
  const [searchParams] = useSearchParams();
  const [items, setItems] = useState<AutomationDefinition[]>([]);
  const [executions, setExecutions] = useState<AutomationExecution[]>([]);
  const [xperts, setXperts] = useState<XpertSummary[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [status, setStatus] = useState("all");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [targetXpertId, setTargetXpertId] = useState(
    searchParams.get("xpert_id") || "",
  );
  const [triggerType, setTriggerType] = useState<AutomationTrigger["type"]>("interval");
  const [onceAt, setOnceAt] = useState("");
  const [intervalSeconds, setIntervalSeconds] = useState(3600);
  const [cron, setCron] = useState("0 9 * * 1-5");
  const [timezone, setTimezone] = useState("Asia/Shanghai");

  const load = useCallback(async () => {
    try {
      const [definitions, recentExecutions] = await Promise.all([
        fetchJson<AutomationListResponse>(
          `/api/runtime/automations?limit=200${status === "all" ? "" : `&status=${status}`}`,
        ),
        fetchJson<ExecutionListResponse>("/api/runtime/automation-executions?limit=200"),
      ]);
      setItems((current) =>
        definitions.items.map((item) => ({
          ...current.find((value) => value.automation_id === item.automation_id),
          ...item,
        })),
      );
      setExecutions(recentExecutions.items);
      setSelectedId((current) =>
        current && definitions.items.some((item) => item.automation_id === current)
          ? current
          : definitions.items[0]?.automation_id || "",
      );
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "自动化任务加载失败");
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    listXperts({ status: "published", limit: 200 })
      .then((payload) => {
        setXperts(payload.items);
        setTargetXpertId((current) => current || payload.items[0]?.id || "");
      })
      .catch(() => setXperts([]));
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    let active = true;
    fetchJson<AutomationDefinition>(`/api/runtime/automations/${selectedId}`)
      .then((detail) => {
        if (!active) return;
        setItems((current) =>
          current.map((item) =>
            item.automation_id === detail.automation_id ? { ...item, ...detail } : item,
          ),
        );
        if (detail.executions) {
          setExecutions((current) => [
            ...detail.executions!,
            ...current.filter((item) => item.automation_id !== detail.automation_id),
          ]);
        }
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [selectedId]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [load]);

  const selected = useMemo(
    () => items.find((item) => item.automation_id === selectedId) ?? null,
    [items, selectedId],
  );
  const selectedExecutions = useMemo(
    () => executions.filter((item) => item.automation_id === selectedId),
    [executions, selectedId],
  );

  async function createAutomation() {
    if (!name.trim() || !prompt.trim() || !targetXpertId) return;
    const trigger: Record<string, unknown> = { type: triggerType, timezone };
    if (triggerType === "once") trigger.once_at = new Date(onceAt).getTime() / 1000;
    if (triggerType === "interval") trigger.interval_seconds = intervalSeconds;
    if (triggerType === "cron") trigger.cron = cron;
    setBusy("create");
    try {
      const created = await fetchJson<AutomationDefinition>("/api/runtime/automations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          prompt: prompt.trim(),
          target_xpert_id: targetXpertId,
          trigger,
          status: "scheduled",
          overlap_policy: "skip",
          misfire_policy: "latest",
          max_attempts: 3,
          budget: { max_runs_per_day: 100, max_runtime_seconds: 1800 },
        }),
      });
      setShowCreate(false);
      setName("");
      setPrompt("");
      await load();
      setSelectedId(created.automation_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "创建失败");
    } finally {
      setBusy("");
    }
  }

  async function definitionAction(action: "pause" | "resume" | "run-now" | "archive") {
    if (!selected) return;
    setBusy(action);
    try {
      await fetchJson(`/api/runtime/automations/${selected.automation_id}/${action}`, {
        method: "POST",
      });
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作失败");
    } finally {
      setBusy("");
    }
  }

  async function executionAction(execution: AutomationExecution, action: "retry" | "cancel") {
    setBusy(`${action}:${execution.execution_id}`);
    try {
      await fetchJson(`/api/runtime/automation-executions/${execution.execution_id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: action === "retry" ? JSON.stringify({ reset_attempts: true }) : undefined,
      });
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Execution 操作失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <PageContainer activeResource="agents" maxWidthClassName="max-w-[1680px]">
      <header className="mb-4 flex flex-col gap-3 border-b border-white/10 pb-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.14em] text-hire-200">
            智能体自动化 Beta
          </div>
          <h1 className="mt-2 text-2xl font-semibold text-white">自动化工作台</h1>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-400">
            将已发布智能体固定到具体版本，按单次、间隔或 Cron 触发；支持预算、重试、死信与人工等待恢复。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link className="rounded-lg border border-white/10 bg-white/[0.05] px-3 py-2 text-sm font-semibold text-slate-200" to="/agents/studio">
            管理智能体
          </Link>
          <button className="rounded-lg bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950" onClick={() => setShowCreate((value) => !value)} type="button">
            {showCreate ? "收起创建" : "新建自动化"}
          </button>
        </div>
      </header>

      {error ? <div className="mb-4 rounded-lg border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-100">{error}</div> : null}

      {showCreate ? (
        <section className="mb-4 grid gap-4 rounded-lg border border-white/10 bg-surface-900/72 p-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(300px,0.8fr)]">
          <div className="space-y-3">
            <label className="block text-xs font-semibold text-slate-400">名称<input className="mt-1 h-10 w-full rounded-lg border border-white/10 bg-ink-950/60 px-3 text-sm text-white outline-none focus:border-hire-300/40" onChange={(event) => setName(event.target.value)} value={name} /></label>
            <label className="block text-xs font-semibold text-slate-400">执行指令<textarea className="mt-1 min-h-28 w-full resize-y rounded-lg border border-white/10 bg-ink-950/60 p-3 text-sm leading-6 text-white outline-none focus:border-hire-300/40" onChange={(event) => setPrompt(event.target.value)} value={prompt} /></label>
            <label className="block text-xs font-semibold text-slate-400">目标已发布智能体<select className="mt-1 h-10 w-full rounded-lg border border-white/10 bg-ink-950/60 px-3 text-sm text-white" onChange={(event) => setTargetXpertId(event.target.value)} value={targetXpertId}>{xperts.map((xpert) => <option key={xpert.id} value={xpert.id}>{xpert.name} · v{xpert.published_version}</option>)}</select></label>
          </div>
          <div className="space-y-3">
            <div className="grid grid-cols-3 gap-2">{(["once", "interval", "cron"] as const).map((type) => <button className={`h-9 rounded-lg border text-xs font-semibold ${triggerType === type ? "border-hire-300/40 bg-hire-300/10 text-hire-100" : "border-white/10 bg-white/[0.04] text-slate-400"}`} key={type} onClick={() => setTriggerType(type)} type="button">{type === "once" ? "单次" : type === "interval" ? "间隔" : "Cron"}</button>)}</div>
            {triggerType === "once" ? <label className="block text-xs font-semibold text-slate-400">执行时间<input className="mt-1 h-10 w-full rounded-lg border border-white/10 bg-ink-950/60 px-3 text-sm text-white" min={new Date().toISOString().slice(0, 16)} onChange={(event) => setOnceAt(event.target.value)} type="datetime-local" value={onceAt} /></label> : null}
            {triggerType === "interval" ? <label className="block text-xs font-semibold text-slate-400">间隔秒数<input className="mt-1 h-10 w-full rounded-lg border border-white/10 bg-ink-950/60 px-3 text-sm text-white" min={30} onChange={(event) => setIntervalSeconds(Number(event.target.value))} type="number" value={intervalSeconds} /></label> : null}
            {triggerType === "cron" ? <label className="block text-xs font-semibold text-slate-400">5 段 Cron<input className="mt-1 h-10 w-full rounded-lg border border-white/10 bg-ink-950/60 px-3 font-mono text-sm text-white" onChange={(event) => setCron(event.target.value)} value={cron} /></label> : null}
            <label className="block text-xs font-semibold text-slate-400">IANA 时区<input className="mt-1 h-10 w-full rounded-lg border border-white/10 bg-ink-950/60 px-3 text-sm text-white" onChange={(event) => setTimezone(event.target.value)} value={timezone} /></label>
            <button className="h-10 w-full rounded-lg bg-hire-300 text-sm font-semibold text-ink-950 disabled:opacity-50" disabled={busy === "create" || !name.trim() || !prompt.trim() || !targetXpertId || (triggerType === "once" && !onceAt)} onClick={() => void createAutomation()} type="button">{busy === "create" ? "创建中..." : "创建并启用"}</button>
          </div>
        </section>
      ) : null}

      <div className="grid min-h-[680px] gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
        <aside className="overflow-hidden rounded-lg border border-white/10 bg-surface-900/72">
          <div className="border-b border-white/10 p-3"><select className="h-9 w-full rounded-lg border border-white/10 bg-ink-950/60 px-3 text-sm text-white" onChange={(event) => setStatus(event.target.value)} value={status}><option value="all">全部状态</option><option value="scheduled">已调度</option><option value="paused">已暂停</option><option value="draft">草稿</option><option value="archived">已归档</option></select></div>
          <div className="max-h-[650px] overflow-y-auto p-2">{loading ? <div className="h-24 animate-pulse rounded-lg bg-white/[0.05]" /> : items.length === 0 ? <div className="p-8 text-center text-sm text-slate-400">暂无自动化任务</div> : items.map((item) => <button className={`mb-2 w-full rounded-lg border p-3 text-left ${selectedId === item.automation_id ? "border-hire-300/40 bg-hire-300/10" : "border-white/10 bg-white/[0.035] hover:bg-white/[0.06]"}`} key={item.automation_id} onClick={() => setSelectedId(item.automation_id)} type="button"><div className="flex items-start justify-between gap-2"><span className="font-semibold text-white">{item.name}</span><span className={`rounded-full border px-2 py-0.5 text-[10px] ${statusTone(item.status)}`}>{item.status}</span></div><p className="mt-2 text-xs text-slate-400">{item.target_xpert_slug} · v{item.target_xpert_version}</p><p className="mt-1 text-[11px] text-slate-500">{triggerLabel(item.trigger)}</p></button>)}</div>
        </aside>

        <main className="min-w-0 rounded-lg border border-white/10 bg-surface-900/72">
          {!selected ? <div className="flex min-h-[680px] items-center justify-center p-8 text-sm text-slate-400">选择一条自动化查看执行状态</div> : <div className="p-4 sm:p-5"><div className="flex flex-col gap-4 border-b border-white/10 pb-4 lg:flex-row lg:items-start lg:justify-between"><div><div className="flex flex-wrap items-center gap-2"><h2 className="text-xl font-semibold text-white">{selected.name}</h2><span className={`rounded-full border px-2.5 py-1 text-xs ${statusTone(selected.status)}`}>{selected.status}</span></div><p className="mt-2 max-w-3xl whitespace-pre-wrap text-sm leading-6 text-slate-300">{selected.prompt || "打开详情后可查看完整执行指令。"}</p><div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-500"><span>{triggerLabel(selected.trigger)}</span><span>下次：{formatTime(selected.next_run_at)}</span><span>更新：{formatTime(selected.updated_at)}</span></div></div><div className="flex flex-wrap gap-2">{selected.status === "scheduled" ? <button className="rounded-lg border border-white/10 px-3 py-2 text-xs font-semibold text-amber-100" disabled={Boolean(busy)} onClick={() => void definitionAction("pause")} type="button">暂停</button> : selected.status !== "archived" ? <button className="rounded-lg border border-white/10 px-3 py-2 text-xs font-semibold text-emerald-100" disabled={Boolean(busy)} onClick={() => void definitionAction("resume")} type="button">恢复</button> : null}<button className="rounded-lg bg-hire-300 px-3 py-2 text-xs font-semibold text-ink-950" disabled={Boolean(busy) || selected.status === "archived"} onClick={() => void definitionAction("run-now")} type="button">立即运行</button><button className="rounded-lg border border-rose-300/20 px-3 py-2 text-xs font-semibold text-rose-200" disabled={Boolean(busy) || selected.status === "archived"} onClick={() => void definitionAction("archive")} type="button">归档</button></div></div><section className="mt-5"><div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-semibold text-white">Execution 历史</h3><span className="text-xs text-slate-500">{selectedExecutions.length} 条</span></div><div className="space-y-2">{selectedExecutions.length === 0 ? <div className="rounded-lg border border-dashed border-white/10 p-8 text-center text-sm text-slate-400">尚未触发</div> : selectedExecutions.map((execution) => <article className="rounded-lg border border-white/10 bg-white/[0.035] p-3" key={execution.execution_id}><div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2"><span className={`rounded-full border px-2 py-0.5 text-[10px] ${statusTone(execution.status)}`}>{execution.status}</span><span className="font-mono text-[11px] text-slate-500">{execution.execution_id.slice(0, 18)}</span></div><span className="text-xs text-slate-500">{formatTime(execution.scheduled_at)} · attempt {execution.attempt}</span></div>{execution.result ? <p className="mt-2 line-clamp-3 whitespace-pre-wrap text-sm leading-6 text-slate-300">{execution.result}</p> : null}{execution.error ? <p className="mt-2 text-sm text-rose-200">{execution.error}</p> : null}<div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-slate-500">{execution.run_id ? <Link className="text-hire-200 hover:underline" to="/runtime">Run {execution.run_id.slice(0, 12)}</Link> : null}{execution.wait_kind ? <span>等待 {execution.wait_kind}</span> : null}{["dead_letter", "failed", "budget_limited", "cancelled"].includes(execution.status) ? <button className="font-semibold text-amber-200" disabled={Boolean(busy)} onClick={() => void executionAction(execution, "retry")} type="button">重试</button> : null}{["pending", "running", "waiting_approval", "waiting_client"].includes(execution.status) ? <button className="font-semibold text-rose-200" disabled={Boolean(busy)} onClick={() => void executionAction(execution, "cancel")} type="button">取消</button> : null}</div></article>)}</div></section></div>}
        </main>
      </div>
    </PageContainer>
  );
}
