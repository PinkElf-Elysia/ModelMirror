import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import PageContainer from "../components/PageContainer";
import AuthoringProposalNotice from "../components/authoring/AuthoringProposalNotice";
import RuntimeApprovalPanel from "../components/runtime/RuntimeApprovalPanel";
import BrowserSessionPanel from "../components/runtime/BrowserSessionPanel";
import ClientToolPanel from "../components/runtime/ClientToolPanel";
import SandboxWorkspacePanel from "../components/runtime/SandboxWorkspacePanel";
import {
  type ConversationGoal,
  type GoalStatus,
  type GoalStep,
  type GoalSummary,
} from "../types/goal";
import { type XpertSummary } from "../types/xpert";
import {
  getGoal,
  goalAction,
  listGoals,
  reassignGoalStep,
  replanGoal,
  retryGoalStep,
  saveGoalPlan,
  skipGoalStep,
} from "../utils/goalApi";
import { listXperts } from "../utils/xpertApi";

interface RuntimeCheckpoint {
  checkpoint_id: string;
  event_type: string;
  title: string;
  summary: string;
  severity: string;
  created_at: number;
}

const statusOptions: Array<{ value: GoalStatus | "all"; label: string }> = [
  { value: "all", label: "全部" },
  { value: "planning", label: "规划中" },
  { value: "awaiting_review", label: "待审核" },
  { value: "running", label: "执行中" },
  { value: "paused", label: "已暂停" },
  { value: "needs_attention", label: "需处理" },
  { value: "completed", label: "已完成" },
  { value: "cancelled", label: "已取消" },
];

function statusLabel(status: GoalStatus) {
  return statusOptions.find((item) => item.value === status)?.label ?? status;
}

function statusTone(status: GoalStatus | GoalStep["status"]) {
  if (status === "completed") return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  if (status === "running") return "border-cyan-300/25 bg-cyan-300/10 text-cyan-100";
  if (status === "needs_attention" || status === "failed") return "border-rose-300/25 bg-rose-300/10 text-rose-100";
  if (status === "paused" || status === "blocked") return "border-amber-300/25 bg-amber-300/10 text-amber-100";
  return "border-white/10 bg-white/[0.055] text-slate-300";
}

function formatTime(value: number) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value * 1000));
}

function cloneSteps(steps: GoalStep[]) {
  return steps.map((step) => ({ ...step, depends_on: [...step.depends_on] }));
}

function shortId(value: string | null | undefined) {
  if (!value) return "-";
  return value.length > 14 ? `${value.slice(0, 7)}...${value.slice(-5)}` : value;
}

export default function ConversationGoalsPage() {
  const { goalId = "" } = useParams();
  const navigate = useNavigate();
  const [goals, setGoals] = useState<GoalSummary[]>([]);
  const [goal, setGoal] = useState<ConversationGoal | null>(null);
  const [xperts, setXperts] = useState<XpertSummary[]>([]);
  const [status, setStatus] = useState<GoalStatus | "all">("all");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [action, setAction] = useState("");
  const [draftSteps, setDraftSteps] = useState<GoalStep[]>([]);
  const [draftSummary, setDraftSummary] = useState("");
  const [draftFinalStepId, setDraftFinalStepId] = useState("");
  const [checkpoints, setCheckpoints] = useState<RuntimeCheckpoint[]>([]);

  const loadList = useCallback(async () => {
    try {
      const payload = await listGoals({ status, search, limit: 100 });
      setGoals(payload.items);
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Goal 列表加载失败");
    } finally {
      setLoading(false);
    }
  }, [search, status]);

  const loadDetail = useCallback(async (id: string) => {
    if (!id) {
      setGoal(null);
      return;
    }
    setDetailLoading(true);
    try {
      const payload = await getGoal(id);
      setGoal(payload);
      setDraftSteps(cloneSteps(payload.steps));
      setDraftSummary(payload.plan_summary);
      setDraftFinalStepId(payload.final_step_id ?? "");
      if (payload.run_id) {
        const response = await fetch(`/api/runtime/runs/${payload.run_id}/checkpoints`);
        setCheckpoints(response.ok ? ((await response.json()) as RuntimeCheckpoint[]) : []);
      } else {
        setCheckpoints([]);
      }
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Goal 详情加载失败");
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    void Promise.all([
      loadList(),
      listXperts({ status: "published", limit: 200 }).then((payload) => setXperts(payload.items)),
    ]);
  }, [loadList]);

  useEffect(() => {
    void loadDetail(goalId);
  }, [goalId, loadDetail]);

  useEffect(() => {
    if (!goal || !["planning", "running", "paused", "needs_attention"].includes(goal.status)) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      void loadDetail(goal.goal_id);
      void loadList();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [goal, loadDetail, loadList]);

  const progress = useMemo(() => {
    if (!goal?.steps.length) return 0;
    const done = goal.steps.filter((step) => ["completed", "skipped"].includes(step.status)).length;
    return Math.round((done / goal.steps.length) * 100);
  }, [goal]);

  async function runAction(name: string, task: () => Promise<ConversationGoal>) {
    setAction(name);
    try {
      const updated = await task();
      setGoal(updated);
      await loadList();
      await loadDetail(updated.goal_id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作失败");
    } finally {
      setAction("");
    }
  }

  async function savePlan() {
    if (!goal) return;
    await runAction("save", () =>
      saveGoalPlan(goal.goal_id, {
        plan_revision: goal.plan_revision,
        summary: draftSummary,
        final_step_id: draftFinalStepId,
        steps: draftSteps.map((step) => ({
          step_id: step.step_id,
          title: step.title,
          instruction: step.instruction,
          target_xpert_id: step.target_xpert_id,
          depends_on: step.depends_on,
        })),
      }),
    );
  }

  function patchDraftStep(stepId: string, patch: Partial<GoalStep>) {
    setDraftSteps((current) =>
      current.map((step) => (step.step_id === stepId ? { ...step, ...patch } : step)),
    );
  }

  function addDraftStep() {
    const base = `step-${draftSteps.length + 1}`;
    let stepId = base;
    let suffix = 1;
    while (draftSteps.some((step) => step.step_id === stepId)) stepId = `${base}-${suffix++}`;
    setDraftSteps((current) => [
      ...current,
      {
        step_id: stepId,
        title: "新步骤",
        instruction: "描述该步骤需要交付的结果。",
        target_xpert_id: xperts[0]?.id ?? "",
        target_version: null,
        depends_on: [],
        status: "pending",
        task_id: null,
        handoff_id: null,
        xpert_run_id: null,
        result: null,
        error: null,
        attempts: 0,
        created_at: Date.now() / 1000,
        updated_at: Date.now() / 1000,
      },
    ]);
  }

  return (
    <PageContainer activeResource="agents" maxWidthClassName="max-w-[1680px]">
      <header className="mb-4 flex flex-col gap-3 border-b border-white/10 pb-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-hire-100">
            <span aria-hidden="true" className="text-xs font-bold">GL</span>
            智能体长期任务
          </div>
          <h1 className="mt-2 text-2xl font-semibold text-white">Goal 工作台</h1>
          <p className="mt-1 max-w-2xl text-sm leading-6 text-slate-400">
            审核自动计划，观察依赖执行，在失败时改派或恢复步骤。
          </p>
        </div>
        <button
          className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-white/10 bg-white/[0.055] px-3 text-sm font-semibold text-slate-200 transition hover:border-hire-300/35 hover:text-hire-100"
          onClick={() => { void loadList(); if (goalId) void loadDetail(goalId); }}
          type="button"
        >
          <span aria-hidden="true">↻</span> 刷新状态
        </button>
      </header>

      {error ? (
        <div className="mb-4 rounded-lg border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-100">{error}</div>
      ) : null}

      <div className="grid min-h-[720px] gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
        <aside className="min-h-0 overflow-hidden rounded-lg border border-white/10 bg-surface-900/72">
          <div className="space-y-3 border-b border-white/10 p-3">
            <label className="relative block">
              <span aria-hidden="true" className="pointer-events-none absolute left-3 top-2 text-base text-slate-500">⌕</span>
              <input
                className="h-9 w-full rounded-lg border border-white/10 bg-white/[0.045] pl-9 pr-3 text-sm text-white outline-none placeholder:text-slate-500 focus:border-hire-300/40"
                onChange={(event) => setSearch(event.target.value)}
                placeholder="搜索目标"
                value={search}
              />
            </label>
            <select
              className="h-9 w-full rounded-lg border border-white/10 bg-white/[0.045] px-3 text-sm text-white outline-none"
              onChange={(event) => setStatus(event.target.value as GoalStatus | "all")}
              value={status}
            >
              {statusOptions.map((item) => <option className="bg-ink-950" key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </div>
          <div className="max-h-[650px] overflow-y-auto p-2">
            {loading ? (
              <div className="space-y-2 p-2">{[0, 1, 2].map((item) => <div className="h-24 animate-pulse rounded-lg bg-white/[0.05]" key={item} />)}</div>
            ) : goals.length === 0 ? (
              <div className="p-8 text-center text-sm text-slate-400">
                <span aria-hidden="true" className="mx-auto mb-3 block text-lg font-bold text-slate-500">GL</span>
                暂无长期 Goal。可从已发布智能体的聊天页创建。
              </div>
            ) : goals.map((item) => (
              <button
                className={`mb-2 w-full rounded-lg border p-3 text-left transition ${goalId === item.goal_id ? "border-hire-300/40 bg-hire-300/10" : "border-white/10 bg-white/[0.035] hover:bg-white/[0.065]"}`}
                key={item.goal_id}
                onClick={() => navigate(`/agents/goals/${item.goal_id}`)}
                type="button"
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="line-clamp-2 text-sm font-semibold text-white">{item.title}</p>
                  <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${statusTone(item.status)}`}>{statusLabel(item.status)}</span>
                </div>
                <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-400">{item.objective_preview}</p>
                <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
                  <span>{item.completed_step_count}/{item.step_count} 步</span>
                  <span>{formatTime(item.updated_at)}</span>
                </div>
              </button>
            ))}
          </div>
        </aside>

        <main className="min-w-0 overflow-hidden rounded-lg border border-white/10 bg-surface-900/72">
          {!goalId ? (
            <div className="flex min-h-[720px] items-center justify-center p-8 text-center">
              <div className="max-w-md">
                <span aria-hidden="true" className="mx-auto block text-2xl font-semibold text-hire-200">DAG</span>
                <h2 className="mt-4 text-lg font-semibold text-white">选择一个长期 Goal</h2>
                <p className="mt-2 text-sm leading-6 text-slate-400">查看计划、依赖、执行结果和恢复操作。</p>
              </div>
            </div>
          ) : detailLoading && !goal ? (
            <div className="h-[720px] animate-pulse bg-white/[0.035]" />
          ) : goal ? (
            <div>
              <section className="border-b border-white/10 p-4 sm:p-5">
                <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-xl font-semibold text-white">{goal.title}</h2>
                      <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${statusTone(goal.status)}`}>{statusLabel(goal.status)}</span>
                    </div>
                    <p className="mt-2 max-w-3xl whitespace-pre-wrap text-sm leading-6 text-slate-300">{goal.objective}</p>
                    <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
                      <span>计划 v{goal.plan_revision}</span>
                      <span>并发上限 {goal.max_parallel}</span>
                      <span>Planner v{goal.planner_version}</span>
                      <span>Run {shortId(goal.run_id)}</span>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {goal.status === "awaiting_review" ? <button className="inline-flex h-9 items-center gap-2 rounded-lg bg-hire-300 px-3 text-sm font-semibold text-ink-950 disabled:opacity-50" disabled={Boolean(action)} onClick={() => void runAction("start", () => goalAction(goal.goal_id, "start"))} type="button"><span aria-hidden="true">▶</span>开始执行</button> : null}
                    {goal.status === "running" ? <button className="inline-flex h-9 items-center gap-2 rounded-lg border border-amber-300/25 bg-amber-300/10 px-3 text-sm font-semibold text-amber-100" onClick={() => void runAction("pause", () => goalAction(goal.goal_id, "pause"))} type="button"><span aria-hidden="true">Ⅱ</span>暂停派发</button> : null}
                    {goal.status === "paused" ? <button className="inline-flex h-9 items-center gap-2 rounded-lg bg-hire-300 px-3 text-sm font-semibold text-ink-950" onClick={() => void runAction("resume", () => goalAction(goal.goal_id, "resume"))} type="button"><span aria-hidden="true">▶</span>恢复执行</button> : null}
                    {goal.status === "needs_attention" && goal.steps.length === 0 ? <button className="inline-flex h-9 items-center gap-2 rounded-lg border border-white/10 bg-white/[0.055] px-3 text-sm font-semibold text-slate-200" onClick={() => void runAction("replan", () => replanGoal(goal.goal_id))} type="button"><span aria-hidden="true">↻</span>重新规划</button> : null}
                    {!['completed', 'cancelled'].includes(goal.status) ? <button className="inline-flex h-9 items-center gap-2 rounded-lg border border-rose-300/20 bg-rose-300/10 px-3 text-sm font-semibold text-rose-100" onClick={() => void runAction("cancel", () => goalAction(goal.goal_id, "cancel"))} type="button"><span aria-hidden="true">■</span>取消 Goal</button> : null}
                  </div>
                </div>
                {goal.steps.length > 0 ? <div className="mt-4"><div className="mb-1 flex items-center justify-between text-xs text-slate-400"><span>完成进度</span><span>{progress}%</span></div><div className="h-2 overflow-hidden rounded-full bg-white/[0.07]"><div className="h-full rounded-full bg-hire-300 transition-[width] duration-200" style={{ width: `${progress}%` }} /></div></div> : null}
                {goal.error ? <p className="mt-3 rounded-lg border border-rose-300/25 bg-rose-300/10 px-3 py-2 text-sm text-rose-100">{goal.error}</p> : null}
              </section>

              <div className="px-4 pt-4 sm:px-5">
                <div className="mb-3">
                  <AuthoringProposalNotice sourceId={goal.goal_id} />
                </div>
                <RuntimeApprovalPanel
                  onResolved={async () => {
                    await Promise.all([loadDetail(goal.goal_id), loadList()]);
                  }}
                  scopeId={goal.goal_id}
                  scopeType="goal"
                  title="Goal 步骤等待审批"
                />
                <div className="mt-3 overflow-hidden rounded-lg border border-white/10">
                  <SandboxWorkspacePanel
                    compact
                    scopeIdPrefix={`${goal.goal_id}:`}
                    scopeType="goal"
                  />
                  <BrowserSessionPanel
                    compact
                    scopeIdPrefix={`${goal.goal_id}:`}
                    scopeType="goal"
                  />
                  <ClientToolPanel
                    compact
                    onResolved={async () => {
                      await Promise.all([loadDetail(goal.goal_id), loadList()]);
                    }}
                    scopeIdPrefix={`${goal.goal_id}:`}
                    scopeType="goal"
                  />
                </div>
              </div>

              {goal.status === "planning" ? (
                <section className="p-8 text-center"><span aria-hidden="true" className="mx-auto block animate-pulse text-xl text-cyan-200">•••</span><h3 className="mt-3 text-base font-semibold text-white">规划智能体正在生成计划</h3><p className="mt-2 text-sm text-slate-400">计划生成后会进入人工审核，不会自动开始执行。</p></section>
              ) : goal.status === "awaiting_review" ? (
                <section className="p-4 sm:p-5">
                  <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
                    <label className="min-w-0 flex-1 text-xs font-semibold text-slate-400">计划摘要<textarea className="mt-1 min-h-20 w-full resize-y rounded-lg border border-white/10 bg-white/[0.045] p-3 text-sm leading-6 text-white outline-none focus:border-hire-300/40" onChange={(event) => setDraftSummary(event.target.value)} value={draftSummary} /></label>
                    <div className="flex gap-2"><button className="inline-flex h-9 items-center gap-2 rounded-lg border border-white/10 bg-white/[0.055] px-3 text-sm font-semibold text-slate-200" onClick={addDraftStep} type="button"><span aria-hidden="true">＋</span>添加步骤</button><button className="h-9 rounded-lg bg-hire-300 px-4 text-sm font-semibold text-ink-950 disabled:opacity-50" disabled={action === "save"} onClick={() => void savePlan()} type="button">{action === "save" ? "保存中" : "保存计划"}</button></div>
                  </div>
                  <div className="space-y-3">
                    {draftSteps.map((step, index) => (
                      <article className="rounded-lg border border-white/10 bg-white/[0.035] p-4" key={step.step_id}>
                        <div className="flex items-center justify-between gap-3"><div className="flex items-center gap-2"><span className="flex h-7 w-7 items-center justify-center rounded-full bg-white/[0.07] text-xs font-semibold text-slate-200">{index + 1}</span><label className="flex items-center gap-2 text-xs text-slate-400"><input checked={draftFinalStepId === step.step_id} name="final-step" onChange={() => setDraftFinalStepId(step.step_id)} type="radio" />最终交付</label></div><button aria-label="删除步骤" className="rounded-md p-1.5 text-base text-slate-500 hover:bg-rose-300/10 hover:text-rose-100" onClick={() => setDraftSteps((current) => current.filter((item) => item.step_id !== step.step_id).map((item) => ({ ...item, depends_on: item.depends_on.filter((id) => id !== step.step_id) })))} type="button">×</button></div>
                        <div className="mt-3 grid gap-3 lg:grid-cols-2"><label className="text-xs font-semibold text-slate-400">步骤 ID<input className="mt-1 h-9 w-full cursor-not-allowed rounded-lg border border-white/10 bg-white/[0.025] px-3 text-sm text-slate-500 outline-none" readOnly value={step.step_id} /></label><label className="text-xs font-semibold text-slate-400">标题<input className="mt-1 h-9 w-full rounded-lg border border-white/10 bg-white/[0.045] px-3 text-sm text-white outline-none" onChange={(event) => patchDraftStep(step.step_id, { title: event.target.value })} value={step.title} /></label></div>
                        <label className="mt-3 block text-xs font-semibold text-slate-400">执行要求<textarea className="mt-1 min-h-24 w-full resize-y rounded-lg border border-white/10 bg-white/[0.045] p-3 text-sm leading-6 text-white outline-none" onChange={(event) => patchDraftStep(step.step_id, { instruction: event.target.value })} value={step.instruction} /></label>
                        <div className="mt-3 grid gap-3 lg:grid-cols-2"><label className="text-xs font-semibold text-slate-400">执行智能体<select className="mt-1 h-9 w-full rounded-lg border border-white/10 bg-white/[0.045] px-3 text-sm text-white outline-none" onChange={(event) => patchDraftStep(step.step_id, { target_xpert_id: event.target.value })} value={step.target_xpert_id}>{xperts.map((item) => <option className="bg-ink-950" key={item.id} value={item.id}>{item.name} / {item.slug}</option>)}</select></label><div><p className="text-xs font-semibold text-slate-400">依赖步骤</p><div className="mt-1 flex min-h-9 flex-wrap gap-2 rounded-lg border border-white/10 bg-white/[0.035] p-2">{draftSteps.filter((item) => item.step_id !== step.step_id).map((candidate) => <label className="flex items-center gap-1.5 text-xs text-slate-300" key={candidate.step_id}><input checked={step.depends_on.includes(candidate.step_id)} onChange={(event) => patchDraftStep(step.step_id, { depends_on: event.target.checked ? [...step.depends_on, candidate.step_id] : step.depends_on.filter((id) => id !== candidate.step_id) })} type="checkbox" />{candidate.step_id}</label>)}</div></div></div>
                      </article>
                    ))}
                  </div>
                </section>
              ) : (
                <section className="grid gap-4 p-4 sm:p-5 2xl:grid-cols-[minmax(0,1fr)_360px]">
                  <div className="space-y-3">
                    {goal.steps.map((step, index) => (
                      <article className={`rounded-lg border p-4 ${step.step_id === goal.final_step_id ? "border-hire-300/25 bg-hire-300/[0.055]" : "border-white/10 bg-white/[0.035]"}`} key={step.step_id}>
                        <div className="flex flex-wrap items-start justify-between gap-3"><div className="flex min-w-0 items-start gap-3"><span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-white/10 bg-white/[0.06] text-xs font-semibold text-slate-200">{index + 1}</span><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="font-semibold text-white">{step.title}</h3>{step.step_id === goal.final_step_id ? <span className="rounded-full border border-hire-300/25 bg-hire-300/10 px-2 py-0.5 text-[10px] font-semibold text-hire-100">最终交付</span> : null}</div><p className="mt-1 text-xs text-slate-500">{step.step_id} · 智能体 v{step.target_version ?? "-"} · 尝试 {step.attempts}</p></div></div><span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${statusTone(step.status)}`}>{step.status}</span></div>
                        <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-300">{step.instruction}</p>
                        {step.depends_on.length > 0 ? <p className="mt-2 text-xs text-slate-500">依赖：{step.depends_on.join("、")}</p> : null}
                        {step.error ? <p className="mt-3 rounded-lg border border-rose-300/20 bg-rose-300/10 px-3 py-2 text-xs leading-5 text-rose-100">{step.error}</p> : null}
                        {step.result ? <details className="mt-3 rounded-lg border border-white/10 bg-ink-950/45"><summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-slate-300">查看步骤结果</summary><p className="max-h-64 overflow-y-auto whitespace-pre-wrap border-t border-white/10 px-3 py-2 text-xs leading-5 text-slate-300">{step.result}</p></details> : null}
                        {goal.status === "needs_attention" && ["failed", "blocked", "pending"].includes(step.status) ? <div className="mt-3 flex flex-wrap items-center gap-2"><select className="h-8 min-w-48 rounded-lg border border-white/10 bg-white/[0.055] px-2 text-xs text-white outline-none" onChange={(event) => patchDraftStep(step.step_id, { target_xpert_id: event.target.value })} value={draftSteps.find((item) => item.step_id === step.step_id)?.target_xpert_id ?? step.target_xpert_id}>{xperts.map((item) => <option className="bg-ink-950" key={item.id} value={item.id}>{item.name}</option>)}</select><button className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.055] px-2.5 text-xs font-semibold text-slate-200" onClick={() => void runAction(`reassign-${step.step_id}`, () => reassignGoalStep(goal.goal_id, step.step_id, { target_xpert_id: draftSteps.find((item) => item.step_id === step.step_id)?.target_xpert_id ?? step.target_xpert_id }))} type="button">改派</button><button className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-cyan-300/25 bg-cyan-300/10 px-2.5 text-xs font-semibold text-cyan-100" onClick={() => void runAction(`retry-${step.step_id}`, () => retryGoalStep(goal.goal_id, step.step_id))} type="button"><span aria-hidden="true">↻</span>重试</button>{step.step_id !== goal.final_step_id ? <button className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-amber-300/25 bg-amber-300/10 px-2.5 text-xs font-semibold text-amber-100" onClick={() => void runAction(`skip-${step.step_id}`, () => skipGoalStep(goal.goal_id, step.step_id))} type="button"><span aria-hidden="true">→</span>跳过</button> : null}</div> : null}
                        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-slate-600"><span>task {shortId(step.task_id)}</span><span>handoff {shortId(step.handoff_id)}</span><span>run {shortId(step.xpert_run_id)}</span></div>
                      </article>
                    ))}
                    {goal.result ? <section className="rounded-lg border border-emerald-300/25 bg-emerald-300/[0.07] p-4"><div className="flex items-center gap-2 text-sm font-semibold text-emerald-100"><span aria-hidden="true">✓</span>最终交付</div><p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-100">{goal.result}</p></section> : null}
                  </div>
                  <aside className="min-w-0"><div className="sticky top-4 rounded-lg border border-white/10 bg-ink-950/55"><div className="border-b border-white/10 px-4 py-3"><h3 className="text-sm font-semibold text-white">运行时间线</h3><p className="mt-1 text-xs text-slate-500">最近 {checkpoints.length} 条 checkpoint</p></div><div className="max-h-[620px] space-y-3 overflow-y-auto p-4">{checkpoints.length === 0 ? <p className="text-sm text-slate-500">暂无运行记录。</p> : checkpoints.slice(0, 30).map((checkpoint) => <div className="relative pl-5" key={checkpoint.checkpoint_id}><span className={`absolute left-0 top-1.5 h-2 w-2 rounded-full ${checkpoint.severity === "error" ? "bg-rose-300" : checkpoint.severity === "warning" ? "bg-amber-300" : "bg-cyan-300"}`} /><p className="text-xs font-semibold text-slate-200">{checkpoint.title}</p><p className="mt-1 line-clamp-3 text-[11px] leading-5 text-slate-500">{checkpoint.summary || checkpoint.event_type}</p><p className="mt-1 text-[10px] text-slate-600">{formatTime(checkpoint.created_at)}</p></div>)}</div></div></aside>
                </section>
              )}
            </div>
          ) : null}
        </main>
      </div>
    </PageContainer>
  );
}
