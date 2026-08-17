import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  ChevronLeft,
  CircleAlert,
  FileCode2,
  Pencil,
  Plus,
  Settings2,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";
import ConversationPanel from "../components/agent-workspace/ConversationPanel";
import SessionSidebar from "../components/agent-workspace/SessionSidebar";
import WorkspacePanel from "../components/agent-workspace/WorkspacePanel";
import EngineShadowPanel from "../components/agent-workspace/EngineShadowPanel";
import CodingWorkerConsole from "../components/CodingWorkerConsole";
import PageContainer from "../components/PageContainer";
import { useModelPreference } from "../context/ModelPreferenceContext";
import {
  chatModelOptions,
  DEFAULT_AGENT_BUILDER_MODEL_ID,
} from "../data/modelOptions";
import type {
  AgentApproval,
  AgentRuntimeEvent,
  AgentSession,
  AgentSessionDetail,
  AgentSkillset,
  AgentSummary,
  AgentThinkingLevel,
  AgentWorkspaceEntry,
  ApprovalMode,
} from "../types/agentWorkspace";
import {
  agentWorkspaceDownloadUrl,
  connectAgentWorkspaceEvents,
  createAgentSession,
  createAgentTask,
  decideAgentApproval,
  deleteAgentSession,
  generateWorkspaceAgent,
  listAgentSessions,
  listAgentSkillsets,
  listAgentSubagents,
  listAgentWorkspace,
  listWorkspaceAgents,
  readAgentSession,
  readAgentWorkspaceFile,
  readAgentWorkspaceStatus,
  readWorkspaceAgent,
  renameAgentSession,
  retryWorkspaceAgentGeneration,
  stopAgentTask,
  updateAgentSessionApprovalMode,
} from "../utils/agentWorkspaceApi";
import { getCodingWorkerStatus } from "../utils/codingWorkerApi";

interface WorkspacePreview {
  path: string;
  content: string;
  size: number;
}

const refreshEventTypes = new Set([
  "approval_waiting",
  "approval_decided",
  "approval_mode_changed",
  "task_completed",
  "task_failed",
  "task_stopped",
  "completed",
  "failed",
  "stopped",
]);

const workspaceEventTypes = new Set(["tool_output", "completed", "agent_generated"]);

function LegacyAgentWorkbenchPage({
  onReturnToWorker,
}: {
  onReturnToWorker?: () => void;
}) {
  const { preferredModelId, setPreferredModelId } = useModelPreference();
  const [enabled, setEnabled] = useState(true);
  const [engineShadowEnabled, setEngineShadowEnabled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [skillsets, setSkillsets] = useState<AgentSkillset[]>([]);
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [newSessionAgentId, setNewSessionAgentId] = useState("default_agent");
  const [newSessionSkillsetId, setNewSessionSkillsetId] = useState("general-agent-default");
  const [detail, setDetail] = useState<AgentSessionDetail | null>(null);
  const [events, setEvents] = useState<AgentRuntimeEvent[]>([]);
  const [subagents, setSubagents] = useState<AgentSession[]>([]);
  const [workspacePath, setWorkspacePath] = useState("");
  const workspacePathRef = useRef("");
  const [workspaceEntries, setWorkspaceEntries] = useState<AgentWorkspaceEntry[]>([]);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [preview, setPreview] = useState<WorkspacePreview | null>(null);
  const [modelId, setModelId] = useState(preferredModelId);
  const [thinkingLevel, setThinkingLevel] = useState<AgentThinkingLevel>("medium");
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>("always-ask");
  const [prompt, setPrompt] = useState("");
  const [creatingSession, setCreatingSession] = useState(false);
  const [sending, setSending] = useState(false);
  const [retryingGeneration, setRetryingGeneration] = useState(false);
  const [decidingApprovalId, setDecidingApprovalId] = useState<string | null>(null);
  const [updatingApprovalMode, setUpdatingApprovalMode] = useState(false);
  const [error, setError] = useState("");
  const [transportWarning, setTransportWarning] = useState(false);
  const [showGenerator, setShowGenerator] = useState(false);
  const [generationPrompt, setGenerationPrompt] = useState("");
  const [generationModelId, setGenerationModelId] = useState(
    DEFAULT_AGENT_BUILDER_MODEL_ID,
  );
  const [generating, setGenerating] = useState(false);

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.agent_id === newSessionAgentId) ?? agents[0],
    [agents, newSessionAgentId],
  );
  const modelOptions = useMemo(() => {
    if (chatModelOptions.some((model) => model.id === modelId)) return chatModelOptions;
    return [{ id: modelId, name: modelId }, ...chatModelOptions];
  }, [modelId]);
  const generationModelOptions = useMemo(() => {
    if (chatModelOptions.some((model) => model.id === generationModelId)) {
      return chatModelOptions;
    }
    return [
      { id: generationModelId, name: generationModelId },
      ...chatModelOptions,
    ];
  }, [generationModelId]);

  const reportError = useCallback((caught: unknown, fallback: string) => {
    setError(caught instanceof Error ? caught.message : fallback);
  }, []);

  const refreshSessions = useCallback(async (preferredId?: string) => {
    const items = await listAgentSessions();
    setSessions(items);
    setSelectedSessionId((current) => {
      const candidate = preferredId ?? current;
      if (candidate && items.some((item) => item.session_id === candidate)) return candidate;
      return items[0]?.session_id ?? null;
    });
    return items;
  }, []);

  const refreshWorkspace = useCallback(async (sessionId: string, path: string) => {
    setWorkspaceLoading(true);
    try {
      setWorkspaceEntries(await listAgentWorkspace(sessionId, path));
    } catch (caught) {
      if (path) {
        setWorkspacePath("");
        setWorkspaceEntries(await listAgentWorkspace(sessionId, ""));
      } else {
        reportError(caught, "Workspace 加载失败");
      }
    } finally {
      setWorkspaceLoading(false);
    }
  }, [reportError]);

  const refreshDetail = useCallback(async (sessionId: string) => {
    const [nextDetail, nextSubagents] = await Promise.all([
      readAgentSession(sessionId),
      listAgentSubagents(sessionId),
    ]);
    setDetail(nextDetail);
    setSubagents(nextSubagents);
    setSessions((current) =>
      current.map((session) =>
        session.session_id === nextDetail.session.session_id
          ? nextDetail.session
          : session,
      ),
    );
    return nextDetail;
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setSessionsLoading(true);
    setError("");
    try {
      const status = await readAgentWorkspaceStatus();
      setEnabled(status.enabled && status.runtime_enabled);
      setEngineShadowEnabled(status.engine_shadow_enabled);
      if (!status.enabled || !status.runtime_enabled) {
        setAgents([]);
        setSessions([]);
        setSelectedSessionId(null);
        return;
      }
      const [agentItems, skillsetItems] = await Promise.all([
        listWorkspaceAgents(),
        listAgentSkillsets(),
        refreshSessions(),
      ]);
      setAgents(agentItems);
      setSkillsets(skillsetItems);
      setNewSessionAgentId((current) =>
        agentItems.some((item) => item.agent_id === current)
          ? current
          : agentItems[0]?.agent_id ?? "default_agent",
      );
      setNewSessionSkillsetId((current) =>
        skillsetItems.some((item) => item.skillset_id === current)
          ? current
          : skillsetItems[0]?.skillset_id ?? "general-agent-default",
      );
    } catch (caught) {
      reportError(caught, "Agent 工作区加载失败");
    } finally {
      setLoading(false);
      setSessionsLoading(false);
    }
  }, [refreshSessions, reportError]);

  useEffect(() => {
    document.title = "Agent 执行工作区 - 模镜";
    void load();
  }, [load]);

  useEffect(() => {
    if (!selectedSessionId) {
      setDetail(null);
      setEvents([]);
      setWorkspaceEntries([]);
      return;
    }
    let disposed = false;
    let disconnect: (() => void) | undefined;
    setError("");
    setTransportWarning(false);
    setPreview(null);
    setWorkspacePath("");
    workspacePathRef.current = "";
    void Promise.all([
      refreshDetail(selectedSessionId),
      refreshWorkspace(selectedSessionId, ""),
    ])
      .then(([nextDetail]) => {
        if (disposed) return;
        setModelId(nextDetail.session.model_id);
        setThinkingLevel(nextDetail.session.thinking_level);
        setApprovalMode(nextDetail.session.approval_mode);
        setEvents([]);
        disconnect = connectAgentWorkspaceEvents(
          selectedSessionId,
          0,
          {
            onEvent: (event) => {
              setTransportWarning(false);
              setEvents((current) => {
                if (current.some((item) => item.sequence === event.sequence)) return current;
                return [...current, event].sort((a, b) => a.sequence - b.sequence);
              });
              if (refreshEventTypes.has(event.type)) {
                void refreshDetail(selectedSessionId).catch((caught) =>
                  reportError(caught, "会话状态刷新失败"),
                );
                void refreshSessions(selectedSessionId);
              }
              if (workspaceEventTypes.has(event.type)) {
                void refreshWorkspace(selectedSessionId, workspacePathRef.current);
              }
              if (event.type === "agent_generated") {
                void listWorkspaceAgents().then(setAgents);
              }
            },
            onTransportError: () => setTransportWarning(true),
          },
        );
      })
      .catch((caught) => reportError(caught, "会话加载失败"));
    return () => {
      disposed = true;
      disconnect?.();
    };
  }, [selectedSessionId, refreshDetail, refreshSessions, refreshWorkspace, reportError]);

  async function handleCreateSession() {
    if (!selectedAgent) return;
    setCreatingSession(true);
    setError("");
    try {
      const agentState = await readWorkspaceAgent(selectedAgent.agent_id);
      const selectedSkillset = skillsets.find(
        (item) => item.skillset_id === newSessionSkillsetId,
      );
      if (!selectedSkillset) throw new Error("所选 Skillset 不存在，请重新加载。");
      if (selectedSkillset.skillset_id !== agentState.config.skillset_id) {
        const installed = new Map(
          agentState.skills.map((skill) => [skill.skill_id, skill.digest]),
        );
        const incompatible = selectedSkillset.members.find(
          (member) => installed.get(member.skill_id) !== member.digest,
        );
        if (incompatible) {
          throw new Error(
            `所选 Skillset 与 ${selectedAgent.name} 的快照不兼容：${incompatible.skill_id}`,
          );
        }
      }
      const created = await createAgentSession({
        agent_id: selectedAgent.agent_id,
        model_id: preferredModelId,
        thinking_level: "medium",
        approval_mode: "always-ask",
        skillset_id: selectedSkillset.skillset_id,
        title: `${selectedAgent.name} 会话`,
      });
      await refreshSessions(created.session_id);
    } catch (caught) {
      reportError(caught, "会话创建失败");
    } finally {
      setCreatingSession(false);
    }
  }

  async function handleSend() {
    if (!detail || !prompt.trim()) return;
    setSending(true);
    setError("");
    try {
      await createAgentTask(detail.session.session_id, {
        prompt: prompt.trim(),
        model_id: modelId,
        thinking_level: thinkingLevel,
        approval_mode: approvalMode,
      });
      setPrompt("");
      setPreferredModelId(modelId);
      await refreshDetail(detail.session.session_id);
    } catch (caught) {
      reportError(caught, "任务发送失败");
    } finally {
      setSending(false);
    }
  }

  async function handleStop() {
    const activeTask = [...(detail?.tasks ?? [])]
      .reverse()
      .find((task) => ["pending", "running", "waiting_approval"].includes(task.status));
    if (!activeTask || !detail) return;
    try {
      await stopAgentTask(activeTask.task_id);
      await refreshDetail(detail.session.session_id);
    } catch (caught) {
      reportError(caught, "停止任务失败");
    }
  }

  async function handleRetryGeneration(taskId: string) {
    if (!detail) return;
    setRetryingGeneration(true);
    setError("");
    try {
      await retryWorkspaceAgentGeneration(taskId);
      await refreshDetail(detail.session.session_id);
    } catch (caught) {
      reportError(caught, "Agent 生成重试失败");
    } finally {
      setRetryingGeneration(false);
    }
  }

  async function handleApproval(
    approval: AgentApproval,
    decision: "approve" | "reject",
  ) {
    setDecidingApprovalId(approval.approval_id);
    try {
      await decideAgentApproval(approval.approval_id, decision);
      await refreshDetail(approval.session_id);
    } catch (caught) {
      reportError(caught, "审批操作失败");
    } finally {
      setDecidingApprovalId(null);
    }
  }

  async function handleApprovalModeChange(next: ApprovalMode) {
    if (
      next === "allow-all" &&
      approvalMode !== "allow-all" &&
      !window.confirm("全部放行将自动执行写文件和命令等操作。确认仅在可信 Workspace 中启用？")
    ) {
      return;
    }
    if (!detail) {
      setApprovalMode(next);
      return;
    }
    setUpdatingApprovalMode(true);
    setError("");
    try {
      const updated = await updateAgentSessionApprovalMode(
        detail.session.session_id,
        next,
      );
      setApprovalMode(updated.approval_mode);
      await Promise.all([
        refreshDetail(detail.session.session_id),
        refreshSessions(detail.session.session_id),
      ]);
    } catch (caught) {
      setApprovalMode(detail.session.approval_mode);
      reportError(caught, "审批模式更新失败");
    } finally {
      setUpdatingApprovalMode(false);
    }
  }

  async function handleGenerate() {
    if (!generationPrompt.trim()) return;
    setGenerating(true);
    setError("");
    try {
      const result = await generateWorkspaceAgent({
        prompt: generationPrompt.trim(),
        model_id: generationModelId,
        thinking_level: thinkingLevel,
        approval_mode: approvalMode,
      });
      setGenerationPrompt("");
      setShowGenerator(false);
      await refreshSessions(result.session.session_id);
    } catch (caught) {
      reportError(caught, "Agent 生成任务创建失败");
    } finally {
      setGenerating(false);
    }
  }

  async function handleRename() {
    if (!detail) return;
    const title = window.prompt("会话名称", detail.session.title)?.trim();
    if (!title || title === detail.session.title) return;
    try {
      await renameAgentSession(detail.session.session_id, title);
      await Promise.all([
        refreshDetail(detail.session.session_id),
        refreshSessions(detail.session.session_id),
      ]);
    } catch (caught) {
      reportError(caught, "会话重命名失败");
    }
  }

  async function handleDelete() {
    if (!detail || !window.confirm(`删除会话“${detail.session.title}”？Workspace 将保留。`)) return;
    try {
      await deleteAgentSession(detail.session.session_id);
      await refreshSessions();
    } catch (caught) {
      reportError(caught, "会话删除失败");
    }
  }

  async function handleOpenFile(path: string) {
    if (!detail) return;
    try {
      setPreview(await readAgentWorkspaceFile(detail.session.session_id, path));
    } catch (caught) {
      reportError(caught, "文件预览失败");
    }
  }

  const generatedAgentId = [...events]
    .reverse()
    .filter((event) => event.type === "agent_generated")
    .map((event) => event.payload.agent_id)
    .find((value): value is string => typeof value === "string" && Boolean(value));

  return (
    <PageContainer
      activeResource="agents"
      className="pb-8"
      hideSidebar
      maxWidthClassName="max-w-[1880px]"
    >
      {enabled && engineShadowEnabled ? <EngineShadowPanel /> : null}
      <header className="mb-4 flex flex-col gap-3 border-b border-white/10 pb-4 xl:flex-row xl:items-end xl:justify-between">
        <div>
          <Link className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-white" to="/agents">
            <ChevronLeft aria-hidden size={14} /> 返回智能体市场
          </Link>
          <div className="mt-3 flex items-center gap-3">
            <span className="flex h-9 w-9 items-center justify-center rounded-md border border-cyan-300/20 bg-cyan-300/10 text-cyan-100">
              <Bot aria-hidden size={18} />
            </span>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight text-white">Agent 执行工作区</h1>
              <p className="mt-0.5 text-xs text-slate-500">原生 Tool Calling · 持久化 Session Workspace</p>
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          {onReturnToWorker ? (
            <button
              className="inline-flex min-h-11 items-center rounded-lg border border-cyan-300/30 bg-cyan-300/10 px-3 text-xs font-semibold text-cyan-50 hover:bg-cyan-300/15"
              onClick={onReturnToWorker}
              type="button"
            >
              返回 Coding Worker
            </button>
          ) : null}
          <label className="min-w-44 text-[11px] font-medium text-slate-400">
            新会话使用
            <select
              aria-label="新会话 Agent"
              className="mt-1 w-full rounded-md border border-white/10 bg-slate-950 px-2.5 py-2 text-xs text-white outline-none focus:border-cyan-300/50"
              onChange={(event) => setNewSessionAgentId(event.target.value)}
              value={newSessionAgentId}
            >
              {agents.map((agent) => (
                <option key={agent.agent_id} value={agent.agent_id}>{agent.name}</option>
              ))}
            </select>
          </label>
          <label className="min-w-44 text-[11px] font-medium text-slate-400">
            Skillset
            <select
              aria-label="新会话 Skillset"
              className="mt-1 w-full rounded-md border border-white/10 bg-slate-950 px-2.5 py-2 text-xs text-white outline-none focus:border-cyan-300/50"
              onChange={(event) => setNewSessionSkillsetId(event.target.value)}
              value={newSessionSkillsetId}
            >
              {skillsets.map((skillset) => (
                <option key={skillset.skillset_id} value={skillset.skillset_id}>
                  {skillset.name} · {skillset.members.length}
                </option>
              ))}
            </select>
          </label>
          <button
            className="inline-flex min-h-9 items-center gap-1.5 rounded-md border border-cyan-300/25 bg-cyan-300/10 px-3 text-xs font-semibold text-cyan-50 hover:bg-cyan-300/15 disabled:opacity-40"
            disabled={!enabled}
            onClick={() => setShowGenerator(true)}
            type="button"
          >
            <Sparkles aria-hidden size={14} /> 一句话创建 Agent
          </button>
          {detail ? (
            <>
              <Link
                className="inline-flex min-h-9 items-center gap-1.5 rounded-md border border-white/10 px-3 text-xs font-semibold text-slate-300 hover:bg-white/[0.05] hover:text-white"
                to={`/agents/workbench/agents/${detail.session.agent_id}`}
              >
                <Settings2 aria-hidden size={14} /> 配置 Agent
              </Link>
              <button aria-label="重命名会话" className="rounded-md border border-white/10 p-2 text-slate-400 hover:bg-white/[0.05] hover:text-white" onClick={() => void handleRename()} type="button">
                <Pencil aria-hidden size={14} />
              </button>
              <button aria-label="删除会话" className="rounded-md border border-white/10 p-2 text-slate-400 hover:bg-rose-300/10 hover:text-rose-200" onClick={() => void handleDelete()} type="button">
                <Trash2 aria-hidden size={14} />
              </button>
            </>
          ) : null}
        </div>
      </header>

      {error ? (
        <div className="mb-4 flex items-start gap-2 rounded-md border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm text-rose-50" role="alert">
          <CircleAlert className="mt-0.5 shrink-0" size={16} />
          <span className="flex-1">{error}</span>
          <button aria-label="关闭错误" onClick={() => setError("")} type="button"><X aria-hidden size={15} /></button>
        </div>
      ) : null}
      {transportWarning ? (
        <div className="mb-4 rounded-md border border-amber-300/20 bg-amber-300/[0.07] px-4 py-2 text-xs text-amber-100" role="status">
          实时连接正在重试；已保存的消息和任务不会重复执行。
        </div>
      ) : null}
      {generatedAgentId ? (
        <div className="mb-4 flex items-center justify-between gap-3 rounded-md border border-emerald-300/20 bg-emerald-300/[0.07] px-4 py-2 text-xs text-emerald-100" role="status">
          <span>Agent “{generatedAgentId}” 已通过校验并创建。</span>
          <Link className="font-semibold underline underline-offset-4" to={`/agents/workbench/agents/${generatedAgentId}`}>打开配置</Link>
        </div>
      ) : null}

      {!enabled && !loading ? (
        <section className="rounded-lg border border-amber-300/25 bg-amber-300/10 p-6">
          <h2 className="text-lg font-semibold text-amber-50">Agent 工作区已关闭</h2>
          <p className="mt-2 text-sm leading-6 text-amber-100/75">
            设置 AGENT_WORKSPACE_ENABLED=1 后，独立 API 与入口才会开放；既有聊天、工作流和智能体市场不受影响。
          </p>
        </section>
      ) : null}

      {enabled ? (
        <div className="overflow-hidden rounded-xl border border-white/10 bg-slate-950/45 shadow-2xl shadow-black/20 lg:grid lg:h-[calc(100vh-13rem)] lg:min-h-[640px] lg:grid-cols-[250px_minmax(0,1fr)_320px]">
          <SessionSidebar
            creating={creatingSession}
            loading={sessionsLoading}
            onCreate={() => void handleCreateSession()}
            onSelect={setSelectedSessionId}
            selectedId={selectedSessionId}
            sessions={sessions}
          />
          {detail ? (
            <ConversationPanel
              approvalMode={approvalMode}
              decidingApprovalId={decidingApprovalId}
              detail={detail}
              events={events}
              modelId={modelId}
              modelOptions={modelOptions}
              onApprovalModeChange={(value) => void handleApprovalModeChange(value)}
              onDecideApproval={(approval, decision) => void handleApproval(approval, decision)}
              onModelChange={setModelId}
              onPromptChange={setPrompt}
              onRetryGeneration={(taskId) => void handleRetryGeneration(taskId)}
              onSend={() => void handleSend()}
              onStop={() => void handleStop()}
              onThinkingLevelChange={setThinkingLevel}
              prompt={prompt}
              retryingGeneration={retryingGeneration}
              sending={sending}
              thinkingLevel={thinkingLevel}
              updatingApprovalMode={updatingApprovalMode}
            />
          ) : (
            <section className="flex min-h-[520px] items-center justify-center px-6 py-16 text-center lg:min-h-0">
              <div className="max-w-md">
                <FileCode2 className="mx-auto text-cyan-200" size={32} />
                <h2 className="mt-4 text-xl font-semibold text-white">创建首个执行会话</h2>
                <p className="mt-2 text-sm leading-6 text-slate-400">
                  每个会话都保存消息、审批、任务状态与独立 Workspace。容器重建后仍可恢复。
                </p>
                <button
                  className="mt-5 inline-flex min-h-10 items-center gap-2 rounded-md bg-cyan-300 px-4 text-sm font-semibold text-slate-950 hover:bg-cyan-200"
                  onClick={() => void handleCreateSession()}
                  type="button"
                >
                  <Plus aria-hidden size={16} /> 新建会话
                </button>
              </div>
            </section>
          )}
          {detail ? (
            <WorkspacePanel
              downloadUrl={preview ? agentWorkspaceDownloadUrl(detail.session.session_id, preview.path) : "#"}
              entries={workspaceEntries}
              loading={workspaceLoading}
              onClosePreview={() => setPreview(null)}
              onGoUp={() => {
                const parent = workspacePath.split("/").slice(0, -1).join("/");
                setWorkspacePath(parent);
                workspacePathRef.current = parent;
                setPreview(null);
                void refreshWorkspace(detail.session.session_id, parent);
              }}
              onOpenDirectory={(path) => {
                setWorkspacePath(path);
                workspacePathRef.current = path;
                setPreview(null);
                void refreshWorkspace(detail.session.session_id, path);
              }}
              onOpenFile={(path) => void handleOpenFile(path)}
              path={workspacePath}
              preview={preview}
              sessionId={detail.session.session_id}
              subagents={subagents}
            />
          ) : (
            <aside className="hidden border-l border-white/10 bg-slate-950/65 p-5 text-xs leading-5 text-slate-500 lg:block">
              Workspace 文件与子 Agent 状态将在选择会话后显示。
            </aside>
          )}
        </div>
      ) : null}

      {showGenerator ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onMouseDown={(event) => {
          if (event.currentTarget === event.target && !generating) setShowGenerator(false);
        }}>
          <section aria-labelledby="generate-agent-title" aria-modal="true" className="w-full max-w-xl rounded-xl border border-white/15 bg-slate-950 p-5 shadow-2xl" role="dialog">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.16em] text-cyan-200">General Agent Builder</p>
                <h2 className="mt-2 text-xl font-semibold text-white" id="generate-agent-title">一句话创建 Agent</h2>
                <p className="mt-2 text-sm leading-6 text-slate-400">
                  General Agent 会在隔离 staging 目录生成候选 State；只有严格校验通过后才会原子创建，绝不覆盖同名 Agent。
                </p>
              </div>
              <button aria-label="关闭创建 Agent 对话框" className="rounded-md border border-white/10 p-2 text-slate-400 hover:text-white" disabled={generating} onClick={() => setShowGenerator(false)} type="button"><X aria-hidden size={15} /></button>
            </div>
            <label className="mt-5 block text-xs font-medium text-slate-300" htmlFor="generate-agent-prompt">Agent 需求</label>
            <textarea
              autoFocus
              className="mt-2 min-h-32 w-full rounded-md border border-white/10 bg-black/25 px-3 py-2 text-sm leading-6 text-white outline-none placeholder:text-slate-600 focus:border-cyan-300/50 focus:ring-2 focus:ring-cyan-300/10"
              id="generate-agent-prompt"
              onChange={(event) => setGenerationPrompt(event.target.value)}
              placeholder="例如：创建一个负责审查 Python API 安全性并输出修复建议的 Agent"
              value={generationPrompt}
            />
            <label className="mt-4 block text-xs font-medium text-slate-300" htmlFor="generate-agent-model">
              Builder 模型
            </label>
            <select
              className="mt-2 min-h-10 w-full rounded-md border border-white/10 bg-slate-900 px-3 text-sm text-white outline-none focus:border-cyan-300/50 focus:ring-2 focus:ring-cyan-300/10"
              disabled={generating}
              id="generate-agent-model"
              onChange={(event) => setGenerationModelId(event.target.value)}
              value={generationModelId}
            >
              {generationModelOptions.map((model) => (
                <option key={model.id} value={model.id}>{model.name}</option>
              ))}
            </select>
            <p className="mt-2 text-[11px] leading-5 text-slate-500">
              默认使用 DeepSeek V4 Flash 0731。候选 State 会经过领域覆盖复审与后端质量门禁，通过后才会创建。
            </p>
            <div className="mt-4 flex items-center justify-between gap-3 border-t border-white/10 pt-4">
              <span className="text-[11px] text-slate-500">Builder 模型独立于普通会话；沿用当前思考等级和审批模式</span>
              <div className="flex gap-2">
                <button className="min-h-9 rounded-md border border-white/10 px-3 text-xs font-semibold text-slate-300 hover:bg-white/[0.05]" disabled={generating} onClick={() => setShowGenerator(false)} type="button">取消</button>
                <button className="inline-flex min-h-9 items-center gap-1.5 rounded-md bg-cyan-300 px-3 text-xs font-semibold text-slate-950 hover:bg-cyan-200 disabled:opacity-40" disabled={!generationPrompt.trim() || generating} onClick={() => void handleGenerate()} type="button">
                  <Sparkles aria-hidden size={14} /> {generating ? "创建任务中…" : "开始生成"}
                </button>
              </div>
            </div>
          </section>
        </div>
      ) : null}
    </PageContainer>
  );
}

export default function AgentWorkbenchPage() {
  const [surface, setSurface] = useState<"loading" | "legacy" | "worker">("loading");
  const [preferWorker, setPreferWorker] = useState(false);

  useEffect(() => {
    let active = true;
    void getCodingWorkerStatus()
      .then(async (status) => {
        if (!status.enabled || !status.available || preferWorker) return "worker" as const;
        const legacySessions = await listAgentSessions();
        return legacySessions.some((session) =>
          session.status === "running" || session.status === "waiting_approval"
        )
          ? "legacy" as const
          : "worker" as const;
      })
      .then((nextSurface) => { if (active) setSurface(nextSurface); })
      .catch(() => { if (active) setSurface("legacy"); });
    return () => { active = false; };
  }, [preferWorker]);

  if (surface === "loading") {
    return <div className="mx-auto mt-10 min-h-[60vh] max-w-7xl animate-pulse rounded-xl bg-white/5" aria-label="正在选择 Agent 执行面" />;
  }
  return surface === "worker"
    ? (
      <CodingWorkerConsole
        context="agent"
        onOpenLegacy={() => setSurface("legacy")}
      />
    )
    : (
      <LegacyAgentWorkbenchPage
        onReturnToWorker={() => {
          setPreferWorker(true);
          setSurface("worker");
        }}
      />
    );
}
