import type {
  AgencyDagEvent,
  AgencyDagRun,
  AgencyAgentSummary,
  AgencyExecutionCapabilities,
  AgencyPlanPreview,
} from "./AgencyExpertTeamTypes";

function statusLabel(status?: string) {
  const labels: Record<string, string> = {
    pending: "等待",
    running: "执行中",
    completed: "完成",
    failed: "失败",
    skipped: "跳过",
    cancelled: "已取消",
    ready: "待恢复",
  };
  return labels[status || ""] || "等待";
}

function statusClasses(status?: string) {
  if (status === "completed") return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  if (status === "failed" || status === "cancelled") return "border-red-300/25 bg-red-300/10 text-red-100";
  if (status === "running") return "border-cyan-300/25 bg-cyan-300/10 text-cyan-100";
  return "border-white/10 bg-white/[0.045] text-slate-400";
}

function usageLabel(event?: AgencyDagEvent) {
  const input = event?.usage?.input_tokens || 0;
  const output = event?.usage?.output_tokens || 0;
  return input || output ? `${input.toLocaleString()} 入 / ${output.toLocaleString()} 出` : "暂无用量";
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
  confirmOpen: boolean;
  onConfirm: () => void;
  onDismissConfirm: () => void;
  onCancel: () => void;
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
  confirmOpen,
  onConfirm,
  onDismissConfirm,
  onCancel,
}: AgencyDagRunPanelProps) {
  const stepsById = new Map((run?.steps || []).map((event) => [event.task_id, event]));
  const agentsById = new Map(
    [...agentCatalog, ...(preview?.selected_agents || [])].map((agent) => [agent.id, agent]),
  );
  const taskDefinitions = preview?.plan.tasks || run?.task_definitions || [];
  const totalInput = run?.usage?.input_tokens || 0;
  const totalOutput = run?.usage?.output_tokens || 0;

  return (
    <div className="space-y-4">
      {confirmOpen ? (
        <section
          aria-labelledby="dag-payment-confirmation"
          className="rounded-lg border border-amber-300/30 bg-amber-300/10 p-5"
        >
          <h3 id="dag-payment-confirmation" className="text-base font-semibold text-amber-100">
            提交前请确认
          </h3>
          <p className="mt-2 text-sm leading-6 text-amber-50/90">
            将使用 {modelName} 执行受控 DAG。最多 {capabilities?.max_model_calls || 10} 次模型调用，
            并发 {capabilities?.max_concurrency || 2}，最长 {Math.round((capabilities?.timeout_seconds || 900) / 60)} 分钟，可能产生费用。
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              className="rounded-full bg-hire-300 px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:opacity-50"
              disabled={busy}
              onClick={onConfirm}
              type="button"
            >
              {busy ? "正在启动..." : "确认并启动"}
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
            {run?.status === "running" ? (
              <button
                className="rounded-full border border-red-300/25 px-3 py-1.5 text-xs font-semibold text-red-100 transition hover:bg-red-300/10 disabled:opacity-50"
                disabled={busy}
                onClick={onCancel}
                type="button"
              >
                取消 DAG 执行
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
                      {agent?.emoji || "专"} {agent?.name || task.agent_id || "未绑定专家"}
                    </p>
                  </div>
                  <span className={`rounded-full border px-2.5 py-1 text-[11px] font-semibold ${statusClasses(event?.status)}`}>
                    {statusLabel(event?.status)}
                  </span>
                </div>
                <dl className="mt-3 grid gap-2 text-xs leading-5 text-slate-400 sm:grid-cols-2">
                  <div><dt className="text-slate-500">依赖</dt><dd className="break-words text-slate-300">{task.depends_on.join("、") || "无"}</dd></div>
                  <div><dt className="text-slate-500">用量</dt><dd className="text-slate-300">{usageLabel(event)}</dd></div>
                </dl>
                <p className="mt-3 text-xs leading-5 text-slate-400">
                  <span className="text-slate-500">验收：</span>{task.acceptance || "未设置"}
                </p>
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
      </section>

      <section className="surface-panel rounded-lg p-5">
        <h3 className="text-lg font-semibold text-hire-100">最终汇点输出</h3>
        <div className="mt-3 grid gap-2 text-xs text-slate-400 sm:grid-cols-3">
          <p>调用：{run?.model_calls || 0} / {capabilities?.max_model_calls || 10}</p>
          <p>Token：{totalInput.toLocaleString()} 入 / {totalOutput.toLocaleString()} 出</p>
          <p>预估费用：{estimatedCostCny === null ? "价格数据缺失" : `¥${estimatedCostCny.toFixed(4)}`}</p>
        </div>
        {run?.warnings?.length ? (
          <ul className="mt-3 space-y-1 text-xs leading-5 text-amber-100">
            {run.warnings.map((warning) => <li key={warning}>· {warning}</li>)}
          </ul>
        ) : null}
        <p className="mt-4 whitespace-pre-wrap break-words text-sm leading-7 text-slate-200">
          {run?.final_output || "执行完成后，最终汇点输出会显示在这里。"}
        </p>
      </section>
    </div>
  );
}
