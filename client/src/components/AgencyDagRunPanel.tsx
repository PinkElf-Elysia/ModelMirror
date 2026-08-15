import { useEffect, useState } from "react";
import type {
  AgencyDagEvent,
  AgencyDagRevisionPayload,
  AgencyDagRun,
  AgencyAgentSummary,
  AgencyExecutionCapabilities,
  AgencyInteractionDecisionPayload,
  AgencyPlanPreview,
} from "./AgencyExpertTeamTypes";

interface AgencyDagRunSummary {
  task_id: string;
  run_id: string;
  model_id: string;
  goal: string;
  team_name: string;
  selected_agent_ids: string[];
  status: AgencyDagRun["status"];
  sequence: number;
  final_output_preview: string;
  quality_status?: string | null;
  model_calls: number;
  usage: AgencyDagRun["usage"];
  lineage_model_calls?: number;
  lineage_usage?: AgencyDagRun["usage"];
  revisable?: boolean;
  revision?: AgencyDagRun["revision"];
  interaction?: {
    step_id: string;
    kind: "human_input" | "approval";
    status: "pending" | "decided" | "expired" | "cancelled";
    decision?: "replace" | "approve" | "reject" | null;
    prompt_preview: string;
  } | null;
  error_code?: string | null;
  created_at: number;
  updated_at: number;
}

interface AgencyDagRunListResponse {
  items: AgencyDagRunSummary[];
  total: number;
}

function statusLabel(status?: string) {
  const labels: Record<string, string> = {
    pending: "等待",
    running: "执行中",
    completed: "完成",
    failed: "失败",
    skipped: "跳过",
    cancelled: "已取消",
    ready: "待恢复",
    waiting: "等待人工处理",
    rejected: "用户已拒绝",
  };
  return labels[status || ""] || "等待";
}

function statusClasses(status?: string) {
  if (status === "completed") return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  if (status === "failed" || status === "cancelled" || status === "rejected") return "border-red-300/25 bg-red-300/10 text-red-100";
  if (status === "running") return "border-cyan-300/25 bg-cyan-300/10 text-cyan-100";
  return "border-white/10 bg-white/[0.045] text-slate-400";
}

function usageLabel(event?: AgencyDagEvent) {
  const input = event?.usage?.input_tokens || 0;
  const output = event?.usage?.output_tokens || 0;
  return input || output ? `${input.toLocaleString()} 入 / ${output.toLocaleString()} 出` : "暂无用量";
}

function historyHref(taskId: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("desk", "team");
  url.searchParams.set("dag_task", taskId);
  return `${url.pathname}${url.search}`;
}

function revisionStepSets(
  tasks: Array<{ task_id: string; title: string; depends_on: string[] }>,
  events: AgencyDagEvent[],
  targetTaskId: string,
) {
  const affected = new Set([targetTaskId]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const task of tasks) {
      if (!affected.has(task.task_id) && task.depends_on.some((id) => affected.has(id))) {
        affected.add(task.task_id);
        changed = true;
      }
    }
  }
  const completed = new Set(
    events
      .filter((event) => event.status === "completed" && event.output)
      .map((event) => event.task_id),
  );
  for (const task of tasks) {
    if (!completed.has(task.task_id)) affected.add(task.task_id);
  }
  return {
    affected: tasks.filter((task) => affected.has(task.task_id)),
    reused: tasks.filter((task) => completed.has(task.task_id) && !affected.has(task.task_id)),
  };
}

function downloadMarkdown(run: AgencyDagRun) {
  if (!run.final_output) return;
  const title = (run.team_name || "专家团成果").replace(/[\\/:*?"<>|]+/g, "-");
  const body = `# ${run.team_name || "专家团成果"}\n\n${run.final_output}\n`;
  const href = URL.createObjectURL(new Blob([body], { type: "text/markdown;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = `${title}.md`;
  anchor.click();
  URL.revokeObjectURL(href);
}

interface AgencyDagRunPanelProps {
  capabilities?: AgencyExecutionCapabilities | null;
  agentCatalog: AgencyAgentSummary[];
  preview: AgencyPlanPreview | null;
  invalid: boolean;
  modelName: string;
  estimatedCostCny: number | null;
  run: AgencyDagRun | null;
  error: string;
  busy: boolean;
  confirmMode: "start" | "retry" | "revise" | null;
  pendingRevision: AgencyDagRevisionPayload | null;
  onConfirm: () => void;
  onDismissConfirm: () => void;
  onCancel: () => void;
  onRetryRequest: () => void;
  onRevisionRequest: (payload: AgencyDagRevisionPayload) => void;
  onInteractionDecision?: (payload: AgencyInteractionDecisionPayload) => void | Promise<unknown>;
  onInteractionReopen?: () => void | Promise<unknown>;
}

export default function AgencyDagRunPanel({
  capabilities,
  agentCatalog,
  preview,
  invalid,
  modelName,
  estimatedCostCny,
  run,
  error,
  busy,
  confirmMode,
  onConfirm,
  onDismissConfirm,
  onCancel,
  onRetryRequest,
  onRevisionRequest,
  onInteractionDecision,
  onInteractionReopen,
  pendingRevision,
}: AgencyDagRunPanelProps) {
  const [history, setHistory] = useState<AgencyDagRunSummary[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState("");
  const [revisionTargetId, setRevisionTargetId] = useState("");
  const [revisionFeedback, setRevisionFeedback] = useState("");
  const [revisionFormError, setRevisionFormError] = useState("");
  const [interactionValue, setInteractionValue] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [interactionError, setInteractionError] = useState("");
  const [interactionConfirm, setInteractionConfirm] = useState<AgencyInteractionDecisionPayload | null>(null);
  const pendingInteraction = run?.pending_interaction;
  const stepsById = new Map((run?.steps || []).map((event) => [event.task_id, event]));
  const agentsById = new Map(
    [...agentCatalog, ...(preview?.selected_agents || [])].map((agent) => [agent.id, agent]),
  );
  const taskDefinitions = preview?.plan.tasks || run?.task_definitions || [];
  const totalInput = run?.usage?.input_tokens || 0;
  const totalOutput = run?.usage?.output_tokens || 0;
  const dependedOn = new Set(taskDefinitions.flatMap((task) => task.depends_on));
  const sinkTaskId = [...taskDefinitions].reverse().find((task) => !dependedOn.has(task.task_id))?.task_id || "";
  const revisionSets = revisionTargetId
    ? revisionStepSets(taskDefinitions, run?.steps || [], revisionTargetId)
    : { affected: [], reused: [] };
  const revisionTargetTask = taskDefinitions.find((task) => task.task_id === revisionTargetId);
  const revisionTargetEvent = stepsById.get(revisionTargetId);
  const revisionTargetAgent = revisionTargetTask?.agent_id
    ? agentsById.get(revisionTargetTask.agent_id)
    : undefined;

  useEffect(() => {
    setRevisionTargetId("");
    setRevisionFeedback("");
    setRevisionFormError("");
    setInteractionValue("");
    setRejectReason("");
    setInteractionError("");
    setInteractionConfirm(null);
  }, [run?.task_id]);

  function requestHumanInput() {
    if (!pendingInteraction) return;
    const value = interactionValue.trim();
    if (!value || value.length > (capabilities?.hitl?.max_input_chars || 20_000)) {
      setInteractionError("请输入 1–20000 个字符。不要提交 API Key、访问令牌或其他秘密。");
      return;
    }
    setInteractionError("");
    setInteractionConfirm({
      approval_id: pendingInteraction.approval_id,
      revision: pendingInteraction.revision,
      decision: "replace",
      replacement_text: value,
    });
  }

  function requestApproval() {
    if (!pendingInteraction) return;
    setInteractionError("");
    setInteractionConfirm({
      approval_id: pendingInteraction.approval_id,
      revision: pendingInteraction.revision,
      decision: "approve",
    });
  }

  function rejectApproval() {
    if (!pendingInteraction) return;
    const reason = rejectReason.trim();
    if (reason.length < 2 || reason.length > 4000) {
      setInteractionError("拒绝时请填写 2–4000 个字符的原因。");
      return;
    }
    setInteractionError("");
    void onInteractionDecision?.({
      approval_id: pendingInteraction.approval_id,
      revision: pendingInteraction.revision,
      decision: "reject",
      message: reason,
    });
  }

  function beginRevision(taskId: string) {
    setRevisionTargetId(taskId);
    setRevisionFeedback("");
    setRevisionFormError("");
  }

  function requestRevision() {
    const feedback = revisionFeedback.trim();
    if (!revisionTargetId || feedback.length < 10 || feedback.length > 4000) {
      setRevisionFormError("请输入 10–4000 个字符的具体修改意见。");
      return;
    }
    setRevisionFormError("");
    onRevisionRequest({ target_task_id: revisionTargetId, feedback });
  }

  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/expert-team/dag-runs?limit=12", { signal: controller.signal })
      .then(async (response) => {
        const payload = (await response.json()) as AgencyDagRunListResponse & { error?: string };
        if (!response.ok) throw new Error(payload.error || `请求失败（${response.status}）`);
        setHistory(payload.items);
        setHistoryError("");
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        setHistoryError(caught instanceof Error ? caught.message : "无法读取 DAG 历史任务。");
      });
    return () => controller.abort();
  }, [run?.task_id, run?.status]);

  async function copyFinalOutput() {
    if (!run?.final_output) return;
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error("Clipboard API unavailable");
      }
      await navigator.clipboard.writeText(run.final_output);
      setCopied(true);
      setCopyError("");
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
      setCopyError("复制失败，请改用 Markdown 下载。");
    }
  }

  return (
    <div className="space-y-4">
      {confirmMode ? (
        <section
          aria-labelledby="dag-payment-confirmation"
          className="rounded-lg border border-amber-300/30 bg-amber-300/10 p-5"
        >
          <h3 id="dag-payment-confirmation" className="text-base font-semibold text-amber-100">
            提交前请确认
          </h3>
          <p className="mt-2 text-sm leading-6 text-amber-50/90">
            {confirmMode === "retry"
              ? `将使用 ${modelName} 创建新的续跑任务，复用已完成步骤，只重新执行失败及下游步骤。累计还可调用 ${Math.max(0, (capabilities?.max_model_calls || 10) - (run?.model_calls || 0))} 次；复用内容不会再次调用模型，但续跑仍可能产生费用。`
              : confirmMode === "revise"
                ? `将使用原模型 ${modelName} 修改步骤 ${pendingRevision?.target_task_id || ""}。本次返工新开最多 ${capabilities?.revision?.max_model_calls || 10} 次调用，并发 ${capabilities?.max_concurrency || 2}，最长 ${Math.round((capabilities?.timeout_seconds || 900) / 60)} 分钟；源任务用量不会挤占本次预算，但返工仍可能产生费用。`
                : `将使用 ${modelName} 执行受控 DAG。最多 ${capabilities?.max_model_calls || 10} 次模型调用，并发 ${capabilities?.max_concurrency || 2}，最长 ${Math.round((capabilities?.timeout_seconds || 900) / 60)} 分钟，可能产生费用。`}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              className="rounded-full bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:opacity-50"
              disabled={busy}
              onClick={onConfirm}
              type="button"
            >
              {busy
                ? "正在提交..."
                : confirmMode === "retry"
                  ? "确认并续跑"
                  : confirmMode === "revise"
                    ? "确认并返工"
                    : "确认并启动"}
            </button>
            <button
              className="rounded-full border border-white/15 px-4 py-2.5 text-sm font-semibold text-slate-200 transition hover:border-white/30"
              disabled={busy}
              onClick={onDismissConfirm}
              type="button"
            >
              返回检查
            </button>
          </div>
        </section>
      ) : null}

      {interactionConfirm ? (
        <section className="rounded-lg border border-amber-300/30 bg-amber-300/10 p-5">
          <h3 className="text-base font-semibold text-amber-100">恢复下游执行前请确认</h3>
          <p className="mt-2 text-sm leading-6 text-amber-50/90">
            将继续使用原模型 {modelName} 恢复下游任务。模型调用与此前片段累计最多 {capabilities?.max_model_calls || 10} 次，
            并发 {capabilities?.max_concurrency || 2}，主动执行累计最长 {Math.round((capabilities?.timeout_seconds || 900) / 60)} 分钟，可能产生费用。
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              className="rounded-full bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950 disabled:opacity-50"
              disabled={busy}
              onClick={() => {
                const payload = interactionConfirm;
                setInteractionConfirm(null);
                void onInteractionDecision?.(payload);
              }}
              type="button"
            >
              {busy ? "正在提交..." : "确认并恢复执行"}
            </button>
            <button
              className="rounded-full border border-white/15 px-4 py-2.5 text-sm font-semibold text-slate-200"
              disabled={busy}
              onClick={() => setInteractionConfirm(null)}
              type="button"
            >
              返回检查
            </button>
          </div>
        </section>
      ) : null}

      {pendingInteraction ? (
        <section className="rounded-lg border border-cyan-300/30 bg-cyan-300/[0.08] p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold text-cyan-100">
                {pendingInteraction.kind === "human_input" ? "等待人工输入" : "等待执行审批"}
              </h3>
              <p className="mt-1 break-all text-xs text-slate-400">
                步骤 {pendingInteraction.step_id} · 审批修订 {pendingInteraction.revision}
              </p>
            </div>
            <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${statusClasses(pendingInteraction.status)}`}>
              {statusLabel(pendingInteraction.status)}
            </span>
          </div>
          <p className="mt-3 whitespace-pre-wrap text-sm leading-6 text-slate-200">
            {pendingInteraction.prompt}
          </p>
          {pendingInteraction.content_preview ? (
            <pre className="mt-3 max-h-52 overflow-auto whitespace-pre-wrap rounded-lg border border-white/10 bg-ink-950/55 p-3 text-xs leading-5 text-slate-400">
              {pendingInteraction.content_preview}
            </pre>
          ) : null}
          {pendingInteraction.status === "expired" ? (
            <button
              className="mt-4 rounded-full border border-cyan-300/30 px-4 py-2 text-sm font-semibold text-cyan-100 disabled:opacity-50"
              disabled={busy || !capabilities?.hitl?.enabled}
              onClick={() => void onInteractionReopen?.()}
              type="button"
            >
              重新开启 24 小时
            </button>
          ) : pendingInteraction.kind === "human_input" ? (
            <>
              <textarea
                className="mt-4 min-h-32 w-full rounded-lg border border-white/10 bg-ink-950/80 p-3 text-sm leading-6 text-white outline-none focus:border-cyan-300/70"
                maxLength={capabilities?.hitl?.max_input_chars || 20_000}
                onChange={(event) => setInteractionValue(event.target.value)}
                placeholder="输入继续执行所需的信息。请勿提交 API Key、访问令牌或其他秘密。"
                value={interactionValue}
              />
              <button
                className="mt-3 rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 disabled:opacity-50"
                disabled={busy || !capabilities?.hitl?.enabled || Boolean(interactionConfirm)}
                onClick={requestHumanInput}
                type="button"
              >
                检查费用并提交
              </button>
            </>
          ) : (
            <div className="mt-4 space-y-3">
              <div className="flex flex-wrap gap-2">
                <button
                  className="rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 disabled:opacity-50"
                  disabled={busy || !capabilities?.hitl?.enabled || Boolean(interactionConfirm)}
                  onClick={requestApproval}
                  type="button"
                >
                  检查费用并通过
                </button>
              </div>
              <textarea
                className="min-h-20 w-full rounded-lg border border-red-300/20 bg-ink-950/80 p-3 text-sm leading-6 text-white outline-none focus:border-red-300/60"
                maxLength={4000}
                onChange={(event) => setRejectReason(event.target.value)}
                placeholder="拒绝原因（必填，拒绝后不再调用下游模型）"
                value={rejectReason}
              />
              <button
                className="rounded-full border border-red-300/30 px-4 py-2 text-sm font-semibold text-red-100 disabled:opacity-50"
                disabled={busy || !capabilities?.hitl?.enabled}
                onClick={rejectApproval}
                type="button"
              >
                拒绝并终止任务
              </button>
            </div>
          )}
          {!capabilities?.hitl?.enabled ? (
            <p className="mt-3 text-xs text-amber-100">人工交互开关已关闭；历史仍可查看，也可以取消任务，但不能提交或重新开启。</p>
          ) : null}
          {interactionError ? <p className="mt-3 text-xs text-red-100">{interactionError}</p> : null}
        </section>
      ) : null}

      {invalid ? (
        <div className="rounded-lg border border-red-300/25 bg-red-300/10 p-4 text-sm leading-6 text-red-100">
          已载入计划与当前目标或阵容不一致。请返回“自动派工 → 智能组队预览”重新校验并应用。
        </div>
      ) : null}
      {error ? (
        <div className="rounded-lg border border-red-300/25 bg-red-300/10 p-4 text-sm leading-6 text-red-100">
          {error}
        </div>
      ) : null}

      {run?.revision ? (
        <section className="rounded-lg border border-cyan-300/25 bg-cyan-300/10 p-4 text-sm leading-6 text-cyan-50">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p>
              修订 {run.revision.revision_index} · 修改步骤 {run.revision.target_task_id}
            </p>
            <a
              className="rounded-full border border-cyan-200/30 px-3 py-1 text-xs font-semibold text-cyan-100 hover:bg-cyan-200/10"
              href={historyHref(run.revision.parent_task_id)}
            >
              查看父版本
            </a>
          </div>
          {run.revision.feedback ? (
            <p className="mt-2 whitespace-pre-wrap text-xs text-cyan-50/85">
              修改意见：{run.revision.feedback}
            </p>
          ) : null}
        </section>
      ) : null}

      {revisionTargetId && run ? (
        <section className="rounded-lg border border-hire-300/30 bg-hire-300/10 p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h3 className="text-base font-semibold text-hire-100">要求专家修改</h3>
              <p className="mt-1 text-sm text-slate-300">
                {revisionTargetAgent?.emoji || "专"} {revisionTargetAgent?.name || revisionTargetTask?.agent_id || "未绑定专家"}
                {" · "}{revisionTargetTask?.title || revisionTargetId} · {revisionTargetId}
              </p>
            </div>
            <button
              className="rounded-full border border-white/15 px-3 py-1 text-xs text-slate-300"
              onClick={() => setRevisionTargetId("")}
              type="button"
            >
              关闭
            </button>
          </div>
          <p className="mt-3 line-clamp-4 whitespace-pre-wrap text-xs leading-5 text-slate-400">
            上一版输出：{revisionTargetEvent?.output || "未找到可返工输出"}
          </p>
          <textarea
            className="mt-4 min-h-28 w-full rounded-lg border border-white/10 bg-ink-950/80 p-3 text-sm leading-6 text-white outline-none focus:border-hire-300/70"
            maxLength={4000}
            onChange={(event) => setRevisionFeedback(event.target.value)}
            placeholder="说明需要保留、删除或改写的内容，以及新的约束。"
            value={revisionFeedback}
          />
          <p className="mt-2 text-xs text-slate-400">
            将执行：{revisionSets.affected.map((task) => task.title).join("、") || "无"}；
            将复用：{revisionSets.reused.map((task) => task.title).join("、") || "无"}。
          </p>
          {revisionFormError ? <p className="mt-2 text-xs text-red-200">{revisionFormError}</p> : null}
          <button
            className="mt-4 rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 disabled:opacity-50"
            disabled={busy || confirmMode === "revise"}
            onClick={requestRevision}
            type="button"
          >
            检查费用并继续
          </button>
        </section>
      ) : null}

      <section className="surface-panel rounded-lg p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-lg font-semibold text-white">DAG 执行进度</h3>
            <p className="mt-1 text-sm text-slate-400">
              同一专家可承担多个步骤；每张任务卡按 task_id 独立记录。
            </p>
          </div>
          <div className="flex items-center gap-2">
            {run ? (
              <span className={`rounded-full border px-2.5 py-1 text-xs font-semibold ${statusClasses(run.status)}`}>
                {statusLabel(run.status)}
              </span>
            ) : null}
            {run && ["running", "waiting", "ready"].includes(run.status) ? (
              <button
                className="rounded-full border border-red-300/25 px-3 py-1.5 text-xs font-semibold text-red-100 transition hover:bg-red-300/10 disabled:opacity-50"
                disabled={busy}
                onClick={onCancel}
                type="button"
              >
                取消 DAG 执行
              </button>
            ) : null}
            {run?.status === "failed" && run.retryable && !confirmMode ? (
              <button
                className="rounded-full border border-amber-300/30 px-3 py-1.5 text-xs font-semibold text-amber-100 transition hover:bg-amber-300/10 disabled:opacity-50"
                disabled={busy}
                onClick={onRetryRequest}
                type="button"
              >
                重试失败步骤
              </button>
            ) : null}
          </div>
        </div>

        <div className="mt-4 space-y-3">
          {taskDefinitions.length > 0 ? taskDefinitions.map((task) => {
            const event = stepsById.get(task.task_id);
            const agent = task.agent_id ? agentsById.get(task.agent_id) : undefined;
            return (
              <article className="rounded-lg border border-white/10 bg-white/[0.035] p-4" key={task.task_id}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="break-all font-mono text-[11px] text-slate-500">{task.task_id}</p>
                    <h4 className="mt-1 text-sm font-semibold text-white">{task.title}</h4>
                    <p className="mt-1 text-xs text-slate-400">
                      {(task.task_type || "expert") === "expert"
                        ? `${agent?.emoji || "专"} ${agent?.name || task.agent_id || "未绑定专家"}`
                        : task.task_type === "approval"
                          ? "人工审批 · 不调用模型"
                          : "人工输入 · 不调用模型"}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {run?.revisable && (task.task_type || "expert") === "expert" && event?.status === "completed" && event.output && capabilities?.revision?.enabled ? (
                      <button
                        className="rounded-full border border-hire-300/30 px-2.5 py-1 text-[11px] font-semibold text-hire-100 hover:bg-hire-300/10"
                        disabled={busy || Boolean(confirmMode)}
                        onClick={() => beginRevision(task.task_id)}
                        type="button"
                      >
                        要求修改
                      </button>
                    ) : null}
                    <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${statusClasses(event?.status)}`}>
                      {event?.reused ? "已复用" : statusLabel(event?.status)}
                    </span>
                  </div>
                </div>
                <dl className="mt-3 grid gap-2 text-xs leading-5 text-slate-400 sm:grid-cols-2">
                  <div><dt className="text-slate-500">依赖</dt><dd className="break-words text-slate-300">{task.depends_on.join("、") || "无"}</dd></div>
                  <div><dt className="text-slate-500">用量</dt><dd className="text-slate-300">{usageLabel(event)}</dd></div>
                </dl>
                {(task.task_type || "expert") === "expert" ? (
                  <p className="mt-3 text-xs leading-5 text-slate-400">
                    <span className="text-slate-500">验收：</span>{task.acceptance || "未设置"}
                  </p>
                ) : (
                  <p className="mt-3 text-xs leading-5 text-cyan-100">
                    交互提示：{task.interaction_prompt || task.objective}
                  </p>
                )}
                {task.method_skill_ids?.length ? (
                  <p className="mt-2 text-xs leading-5 text-cyan-100">
                    方法 Skill：{task.method_skill_ids.join("、")}
                  </p>
                ) : null}
                {event?.verification ? (
                  <p className={`mt-3 rounded-lg border px-3 py-2 text-xs leading-5 ${statusClasses(event.verification.pass ? "completed" : "failed")}`}>
                    {event.verification.pass ? "验收通过" : "验收未完全通过"}
                    {event.verification.reworked ? " · 已自动返工一次" : ""}
                    {event.verification.failed.length > 0 ? ` · ${event.verification.failed.join("；")}` : ""}
                  </p>
                ) : null}
                {event?.output || event?.error || event?.message ? (
                  <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-white/10 bg-ink-950/65 p-3 font-sans text-xs leading-6 text-slate-300">
                    {event.output || event.message || event.error}
                  </pre>
                ) : null}
              </article>
            );
          }) : (
            <p className="py-6 text-center text-sm text-slate-500">载入有效 Agency 计划后可查看任务 DAG。</p>
          )}
        </div>
        {run?.error_message ? (
          <p className="mt-4 rounded-lg border border-red-300/20 bg-red-300/[0.07] p-3 text-sm leading-6 text-red-100">
            {run.error_message}
          </p>
        ) : null}
        {run?.resumed_from_task_id ? (
          <p className="mt-3 break-all text-xs text-cyan-100">
            本任务续跑自 {run.resumed_from_task_id}；标记“已复用”的步骤未再次调用模型。
          </p>
        ) : null}
      </section>

      <section className="surface-panel rounded-lg p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-lg font-semibold text-hire-100">最终汇点输出</h3>
          {run?.final_output ? (
            <div className="flex flex-wrap gap-2">
              <button
                className="rounded-full border border-white/15 px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-white/30"
                onClick={() => void copyFinalOutput()}
                type="button"
              >
                {copied ? "已复制" : "复制结果"}
              </button>
              <button
                className="rounded-full border border-hire-300/30 px-3 py-1.5 text-xs font-semibold text-hire-100 transition hover:bg-hire-300/10"
                onClick={() => downloadMarkdown(run)}
                type="button"
              >
                下载 Markdown
              </button>
              {run.revisable && sinkTaskId && capabilities?.revision?.enabled ? (
                <button
                  className="rounded-full border border-cyan-300/30 px-3 py-1.5 text-xs font-semibold text-cyan-100 transition hover:bg-cyan-300/10"
                  disabled={busy || Boolean(confirmMode)}
                  onClick={() => beginRevision(sinkTaskId)}
                  type="button"
                >
                  继续完善
                </button>
              ) : null}
            </div>
          ) : null}
          {copyError ? <p className="mt-2 text-xs text-amber-100">{copyError}</p> : null}
        </div>
        <div className="mt-3 grid gap-2 text-xs text-slate-400 sm:grid-cols-3">
          <p>调用：{run?.model_calls || 0} / {capabilities?.max_model_calls || 10}</p>
          <p>Token：{totalInput.toLocaleString()} 入 / {totalOutput.toLocaleString()} 出</p>
          <p>预估费用：{estimatedCostCny === null ? "价格数据缺失" : `¥${estimatedCostCny.toFixed(4)}`}</p>
        </div>
        {run?.lineage_model_calls !== undefined ? (
          <p className="mt-2 text-xs text-cyan-100">
            版本链累计：{run.lineage_model_calls} 次调用 · {(run.lineage_usage?.input_tokens || 0).toLocaleString()} 输入 / {(run.lineage_usage?.output_tokens || 0).toLocaleString()} 输出 token
          </p>
        ) : null}
        {run?.revisable && !capabilities?.revision?.enabled ? (
          <p className="mt-2 text-xs text-slate-500">对话式返工当前未启用；历史结果仍可查看和下载。</p>
        ) : null}
        {run?.warnings?.length ? (
          <ul className="mt-3 space-y-1 text-xs leading-5 text-amber-100">
            {run.warnings.map((warning) => <li key={warning}>· {warning}</li>)}
          </ul>
        ) : null}
        <p className="mt-4 whitespace-pre-wrap break-words text-sm leading-7 text-slate-200">
          {run?.final_output || "执行完成后，最终汇点输出会显示在这里。"}
        </p>
      </section>

      <section className="surface-panel rounded-lg p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="text-lg font-semibold text-white">最近 DAG 任务</h3>
            <p className="mt-1 text-sm text-slate-400">历史来自服务端，清理浏览器缓存后仍可重新打开。</p>
          </div>
          <span className="text-xs text-slate-500">最近 {history.length} 项</span>
        </div>
        {historyError ? <p className="mt-3 text-sm text-red-200">{historyError}</p> : null}
        <div className="mt-4 space-y-2">
          {history.map((item) => (
            <article
              className="rounded-lg border border-white/10 bg-white/[0.035] p-3 transition hover:border-hire-300/30 hover:bg-white/[0.06]"
              key={item.task_id}
            >
              <a className="block" href={historyHref(item.task_id)}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-white">
                      {item.revision ? `修订 ${item.revision.revision_index}` : "原版"} · {item.team_name || item.goal || "未命名专家团"}
                    </p>
                    {item.revision?.feedback_preview ? (
                      <p className="mt-1 line-clamp-1 text-[11px] text-cyan-100">
                        {item.revision.target_task_id}：{item.revision.feedback_preview}
                      </p>
                    ) : null}
                    {item.interaction ? (
                      <p className="mt-1 line-clamp-1 text-[11px] text-amber-100">
                        {item.interaction.kind === "approval" ? "审批" : "人工输入"} · {item.interaction.step_id} · {statusLabel(item.interaction.status)}
                      </p>
                    ) : null}
                    <p className="mt-1 line-clamp-2 text-xs leading-5 text-slate-400">{item.final_output_preview || item.goal}</p>
                  </div>
                  <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${statusClasses(item.status)}`}>
                    {statusLabel(item.status)}
                  </span>
                </div>
                <p className="mt-2 text-[11px] text-slate-500">
                  {new Date(item.updated_at * 1000).toLocaleString()} · 本次 {item.model_calls} 次调用
                  {item.lineage_model_calls !== undefined ? ` · 版本链 ${item.lineage_model_calls} 次` : ""}
                </p>
              </a>
              {item.revision?.parent_task_id ? (
                <a
                  className="mt-2 inline-flex text-[11px] font-semibold text-cyan-100 hover:text-cyan-50"
                  href={historyHref(item.revision.parent_task_id)}
                >
                  查看父版本
                </a>
              ) : null}
            </article>
          ))}
          {!historyError && history.length === 0 ? (
            <p className="py-5 text-center text-sm text-slate-500">还没有服务端 DAG 历史。</p>
          ) : null}
        </div>
      </section>
    </div>
  );
}
