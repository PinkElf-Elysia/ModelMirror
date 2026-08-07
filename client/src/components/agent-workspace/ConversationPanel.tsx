import { useMemo, useState, type KeyboardEvent } from "react";
import {
  Bot,
  Check,
  ChevronDown,
  CircleAlert,
  LoaderCircle,
  RefreshCw,
  Send,
  ShieldAlert,
  Square,
  Terminal,
  UserRound,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import type {
  AgentApproval,
  AgentRuntimeEvent,
  AgentSessionDetail,
  AgentThinkingLevel,
  ApprovalMode,
} from "../../types/agentWorkspace";

interface ModelOption {
  id: string;
  name: string;
}

interface ConversationPanelProps {
  detail: AgentSessionDetail;
  events: AgentRuntimeEvent[];
  modelOptions: ModelOption[];
  modelId: string;
  thinkingLevel: AgentThinkingLevel;
  approvalMode: ApprovalMode;
  prompt: string;
  sending: boolean;
  retryingGeneration: boolean;
  decidingApprovalId: string | null;
  updatingApprovalMode: boolean;
  onModelChange: (value: string) => void;
  onThinkingLevelChange: (value: AgentThinkingLevel) => void;
  onApprovalModeChange: (value: ApprovalMode) => void;
  onPromptChange: (value: string) => void;
  onSend: () => void;
  onRetryGeneration: (taskId: string) => void;
  onStop: () => void;
  onDecideApproval: (
    approval: AgentApproval,
    decision: "approve" | "reject",
  ) => void;
}

const activeTaskStatuses = new Set(["pending", "running", "waiting_approval"]);

function payloadText(event: AgentRuntimeEvent, key: string) {
  const value = event.payload[key];
  return typeof value === "string" ? value : "";
}

export default function ConversationPanel({
  detail,
  events,
  modelOptions,
  modelId,
  thinkingLevel,
  approvalMode,
  prompt,
  sending,
  retryingGeneration,
  decidingApprovalId,
  updatingApprovalMode,
  onModelChange,
  onThinkingLevelChange,
  onApprovalModeChange,
  onPromptChange,
  onSend,
  onRetryGeneration,
  onStop,
  onDecideApproval,
}: ConversationPanelProps) {
  const [showThinking, setShowThinking] = useState(false);
  const latestTask = detail.tasks.at(-1);
  const active = Boolean(
    latestTask && activeTaskStatuses.has(latestTask.status),
  );
  const latestTaskEvents = useMemo(
    () => events.filter((event) => event.task_id === latestTask?.task_id),
    [events, latestTask?.task_id],
  );
  const streamedText = latestTaskEvents
    .filter((event) => event.type === "text_delta")
    .map((event) => payloadText(event, "delta"))
    .join("");
  const thinking = latestTaskEvents
    .filter((event) => event.type === "thinking_delta")
    .map((event) => payloadText(event, "delta"))
    .join("");
  const toolEvents = latestTaskEvents.filter((event) =>
    [
      "tool_call",
      "tool_output",
      "subagent_status",
      "generation_validation_failed",
      "generation_quality_review_started",
      "generation_config_normalized",
    ].includes(event.type),
  );
  const pendingApprovals = detail.approvals.filter(
    (approval) => approval.status === "pending",
  );
  const generated = events.some((event) => event.type === "agent_generated");
  const generationTask = [...detail.tasks]
    .reverse()
    .find((task) => task.kind === "generate_agent");
  const generationPending = Boolean(generationTask && !generated);
  const canRetryGeneration = Boolean(
    generationTask &&
      !generated &&
      ["failed", "stopped"].includes(generationTask.status),
  );

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (prompt.trim() && !active && !sending && !generationPending) onSend();
    }
  }

  return (
    <section className="flex min-h-[640px] min-w-0 flex-col bg-slate-950/35 lg:min-h-0">
      <div className="flex flex-wrap items-center gap-2 border-b border-white/10 px-4 py-3">
        <label className="min-w-[180px] flex-1 text-[11px] font-medium text-slate-400">
          模型
          <select
            aria-label="模型"
            className="mt-1 w-full rounded-md border border-white/10 bg-slate-950 px-2.5 py-2 text-xs text-white outline-none focus:border-cyan-300/50"
            onChange={(event) => onModelChange(event.target.value)}
            value={modelId}
          >
            {modelOptions.map((model) => (
              <option key={model.id} value={model.id}>
                {model.name}
              </option>
            ))}
          </select>
        </label>
        <label className="min-w-[112px] text-[11px] font-medium text-slate-400">
          思考等级
          <select
            aria-label="思考等级"
            className="mt-1 w-full rounded-md border border-white/10 bg-slate-950 px-2.5 py-2 text-xs text-white outline-none focus:border-cyan-300/50"
            onChange={(event) =>
              onThinkingLevelChange(event.target.value as AgentThinkingLevel)
            }
            value={thinkingLevel}
          >
            {(["low", "medium", "high", "xhigh"] as const).map((level) => (
              <option key={level} value={level}>
                {level}
              </option>
            ))}
          </select>
        </label>
        <label className="min-w-[140px] text-[11px] font-medium text-slate-400">
          审批模式
          <select
            aria-label="审批模式"
            className="mt-1 w-full rounded-md border border-white/10 bg-slate-950 px-2.5 py-2 text-xs text-white outline-none focus:border-cyan-300/50"
            disabled={updatingApprovalMode}
            onChange={(event) =>
              onApprovalModeChange(event.target.value as ApprovalMode)
            }
            value={approvalMode}
          >
            <option value="always-ask">总是询问</option>
            <option value="read-only">放行只读</option>
            <option value="allow-all">全部放行</option>
            <option value="deny-all">全部拒绝</option>
          </select>
          <span className="mt-1 block text-[10px] font-normal text-slate-500" role="status">
            {updatingApprovalMode ? "正在应用…" : "当前任务立即生效"}
          </span>
        </label>
        <span className="rounded-md border border-white/10 bg-white/[0.035] px-2.5 py-2 text-[11px] text-slate-400">
          Skillset · {detail.session.skillset_id}
        </span>
      </div>

      <div
        className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-5"
        aria-live="polite"
        data-testid="conversation-scroll"
      >
        {!detail.messages.length ? (
          <div className="mx-auto max-w-md py-20 text-center">
            <Bot className="mx-auto text-cyan-200" size={30} />
            <h2 className="mt-4 text-lg font-semibold text-white">准备执行任务</h2>
            <p className="mt-2 text-sm leading-6 text-slate-400">
              General Agent 将在此会话的持久化 Workspace 中读取文件、调用工具并交付结果。
            </p>
          </div>
        ) : null}

        {detail.messages
          .filter((message) => message.role !== "system")
          .map((message) => {
            const user = message.role === "user";
            const tool = message.role === "tool";
            return (
              <article
                className={`flex gap-3 ${user ? "justify-end" : "justify-start"}`}
                key={message.message_id}
              >
                {!user ? (
                  <span className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-cyan-300/20 bg-cyan-300/10 text-cyan-100">
                    {tool ? <Terminal aria-hidden size={14} /> : <Bot aria-hidden size={14} />}
                  </span>
                ) : null}
                <div
                  className={`max-w-[84%] rounded-lg border px-4 py-3 text-sm leading-6 ${
                    user
                      ? "border-cyan-300/20 bg-cyan-300/10 text-cyan-50"
                      : tool
                        ? "border-white/10 bg-black/25 font-mono text-xs text-slate-300"
                        : "border-white/10 bg-white/[0.045] text-slate-200"
                  }`}
                >
                  {tool ? (
                    <details>
                      <summary className="cursor-pointer text-slate-400">工具返回</summary>
                      <pre className="mt-2 whitespace-pre-wrap break-words">{message.content}</pre>
                    </details>
                  ) : (
                    <ReactMarkdown>{message.content}</ReactMarkdown>
                  )}
                </div>
                {user ? (
                  <span className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-white/10 bg-white/[0.05] text-slate-300">
                    <UserRound aria-hidden size={14} />
                  </span>
                ) : null}
              </article>
            );
          })}

        {active && (streamedText || thinking || toolEvents.length) ? (
          <article className="flex gap-3">
            <span className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-cyan-300/20 bg-cyan-300/10 text-cyan-100">
              <LoaderCircle className="animate-spin" aria-hidden size={14} />
            </span>
            <div className="min-w-0 flex-1 space-y-3 rounded-lg border border-cyan-300/15 bg-cyan-300/[0.045] p-4">
              {thinking ? (
                <div>
                  <button
                    aria-expanded={showThinking}
                    className="flex items-center gap-1 text-xs font-medium text-slate-400 hover:text-slate-200"
                    onClick={() => setShowThinking((value) => !value)}
                    type="button"
                  >
                    <ChevronDown
                      aria-hidden
                      className={`transition ${showThinking ? "rotate-180" : ""}`}
                      size={14}
                    />
                    思考过程
                  </button>
                  {showThinking ? (
                    <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-500">
                      {thinking}
                    </p>
                  ) : null}
                </div>
              ) : null}
              {streamedText ? (
                <div className="text-sm leading-6 text-slate-200">
                  <ReactMarkdown>{streamedText}</ReactMarkdown>
                </div>
              ) : null}
              {toolEvents.map((event) => (
                <details
                  className="rounded-md border border-white/10 bg-black/20 px-3 py-2"
                  key={event.sequence}
                >
                  <summary className="cursor-pointer font-mono text-xs text-cyan-100">
                    {event.type === "generation_validation_failed"
                      ? `候选校验未通过 · 第 ${String(event.payload.attempt || "?")} 次`
                      : event.type === "generation_quality_review_started"
                        ? "候选已进入强制领域复审"
                      : event.type === "generation_config_normalized"
                        ? "已恢复继承运行配置"
                      : event.type === "tool_call"
                      ? `调用 ${String(event.payload.tool_name || "tool")}`
                      : event.type === "tool_output"
                        ? `返回 ${String(event.payload.tool_name || "tool")}`
                        : `子 Agent · ${String(event.payload.status || "更新")}`}
                  </summary>
                  <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-5 text-slate-400">
                    {JSON.stringify(event.payload, null, 2)}
                  </pre>
                </details>
              ))}
            </div>
          </article>
        ) : null}

        {canRetryGeneration && generationTask ? (
          <div className="flex items-start gap-3 rounded-md border border-rose-300/25 bg-rose-300/10 p-3 text-sm text-rose-50" role="alert">
            <CircleAlert className="mt-0.5 shrink-0" size={16} />
            <div className="min-w-0 flex-1">
              <p>{generationTask.error || "Agent 生成已停止，候选未提升。"}</p>
              <p className="mt-1 text-xs leading-5 text-rose-100/70">
                此会话仍是受控生成会话，普通聊天不会创建 Agent。请从干净 staging 重试。
              </p>
              <button
                className="mt-3 inline-flex min-h-9 items-center gap-1.5 rounded-md bg-rose-100 px-3 text-xs font-semibold text-slate-950 hover:bg-white disabled:opacity-50"
                disabled={retryingGeneration}
                onClick={() => onRetryGeneration(generationTask.task_id)}
                type="button"
              >
                <RefreshCw
                  aria-hidden
                  className={retryingGeneration ? "animate-spin" : ""}
                  size={14}
                />
                {retryingGeneration ? "重试中…" : "重新执行生成"}
              </button>
            </div>
          </div>
        ) : latestTask?.status === "failed" ? (
          <div className="flex gap-2 rounded-md border border-rose-300/25 bg-rose-300/10 p-3 text-sm text-rose-50" role="alert">
            <CircleAlert className="mt-0.5 shrink-0" size={16} />
            <span>{latestTask.error || "任务执行失败，可调整输入后重试。"}</span>
          </div>
        ) : null}
      </div>

      {pendingApprovals.length ? (
        <section
          aria-label="待审批工具调用"
          className="max-h-[42vh] shrink-0 space-y-3 overflow-y-auto border-y border-amber-300/25 bg-slate-950 px-4 py-3 shadow-[0_-12px_30px_rgba(0,0,0,0.3)]"
          data-testid="approval-dock"
        >
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-xs font-semibold text-amber-100">
              <ShieldAlert aria-hidden size={16} />
              待审批工具调用
            </div>
            <span className="text-[11px] text-amber-100/65">
              {pendingApprovals.length} 项 · 无需滚动对话
            </span>
          </div>
          {pendingApprovals.map((approval) => (
            <section
              className="rounded-lg border border-amber-300/25 bg-amber-300/[0.07] p-4"
              key={approval.approval_id}
            >
              <div className="flex gap-3">
                <ShieldAlert className="mt-0.5 shrink-0 text-amber-200" size={18} />
                <div className="min-w-0 flex-1">
                  <h3 className="text-sm font-semibold text-amber-50">
                    工具调用等待审批 · {approval.tool_name}
                  </h3>
                  <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-md bg-black/25 p-3 text-[11px] leading-5 text-amber-50/75">
                    {JSON.stringify(approval.arguments, null, 2)}
                  </pre>
                  <div className="mt-3 flex gap-2">
                    <button
                      className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-amber-200 px-3 text-xs font-semibold text-slate-950 hover:bg-amber-100 disabled:opacity-50"
                      disabled={decidingApprovalId === approval.approval_id}
                      onClick={() => onDecideApproval(approval, "approve")}
                      type="button"
                    >
                      <Check aria-hidden size={14} /> 批准
                    </button>
                    <button
                      className="inline-flex min-h-9 items-center gap-1.5 rounded-md border border-white/15 px-3 text-xs font-semibold text-slate-200 hover:bg-white/[0.06] disabled:opacity-50"
                      disabled={decidingApprovalId === approval.approval_id}
                      onClick={() => onDecideApproval(approval, "reject")}
                      type="button"
                    >
                      <X aria-hidden size={14} /> 拒绝
                    </button>
                  </div>
                </div>
              </div>
            </section>
          ))}
        </section>
      ) : null}

      <div className="border-t border-white/10 bg-slate-950/70 p-4">
        <div className="rounded-lg border border-white/10 bg-black/20 p-2 focus-within:border-cyan-300/40 focus-within:ring-2 focus-within:ring-cyan-300/10">
          <label className="sr-only" htmlFor="agent-task-prompt">
            任务消息
          </label>
          <textarea
            className="min-h-24 w-full resize-none bg-transparent px-2 py-1 text-sm leading-6 text-white outline-none placeholder:text-slate-600"
            disabled={active || sending || generationPending}
            id="agent-task-prompt"
            onChange={(event) => onPromptChange(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              active
                ? "任务运行中，可停止后继续"
                : generationPending
                  ? "此生成会话只能通过校验或使用上方按钮重试"
                  : "描述任务，Enter 发送，Shift+Enter 换行"
            }
            value={prompt}
          />
          <div className="flex items-center justify-between gap-3 px-1 pb-1">
            <span className="text-[11px] text-slate-500">
              Workspace · {detail.session.workspace_id.slice(0, 10)}
            </span>
            {active ? (
              <button
                className="inline-flex min-h-9 items-center gap-1.5 rounded-md border border-rose-300/25 bg-rose-300/10 px-3 text-xs font-semibold text-rose-100 hover:bg-rose-300/15"
                onClick={onStop}
                type="button"
              >
                <Square aria-hidden size={12} /> 停止
              </button>
            ) : (
              <button
                className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-cyan-300 px-3 text-xs font-semibold text-slate-950 hover:bg-cyan-200 disabled:cursor-not-allowed disabled:opacity-40"
                disabled={!prompt.trim() || sending || generationPending}
                onClick={onSend}
                type="button"
              >
                <Send aria-hidden size={14} /> {sending ? "发送中…" : "发送"}
              </button>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
