import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  CircleAlert,
  FileCode2,
  FolderPlus,
  FolderTree,
  GitCompareArrows,
  MessageSquareText,
  Pause,
  Pin,
  Play,
  Plus,
  ShieldCheck,
  Square,
  TerminalSquare,
} from "lucide-react";
import type {
  CodingWorkerApproval,
  CodingWorkerArtifact,
  CodingWorkerChangeset,
  CodingWorkerDiagnosticsSnapshot,
  CodingWorkerEntry,
  CodingWorkerEvent,
  CodingWorkerEvidence,
  CodingWorkerOperationOutputChunk,
  CodingWorkerStatus,
  CodingWorkerTask,
  CodingWorkerTaskSpec,
} from "../types/codingWorker";
import {
  changeCodingWorkerTask,
  codingWorkerArtifactUrl,
  connectCodingWorkerEvents,
  createCodingWorkerTask,
  decideCodingWorkerApproval,
  getCodingWorkerChangeset,
  getCodingWorkerDiagnostics,
  getCodingWorkerTask,
  getCodingWorkerStatus,
  handoffCodingWorkerTask,
  listCodingWorkerApprovals,
  listCodingWorkerArtifacts,
  listCodingWorkerEvidence,
  listCodingWorkerOperationOutput,
  listCodingWorkerTasks,
  listCodingWorkerTree,
  readCodingWorkerDiff,
  readCodingWorkerEntry,
  sendCodingWorkerMessage,
  type CodingWorkerHandoffResult,
} from "../utils/codingWorkerApi";
import type { CodingProjectSelection, CodingProjectSummary } from "../types/coding";
import {
  createCodingProjectSelection,
  getCodingProjectSelection,
  getCodingProjects,
  getCodingWorkerHostSource,
} from "../utils/codingApi";

type ConsoleContext = "coding" | "agent";
type InspectorTab = "files" | "diff" | "changesets" | "diagnostics" | "evidence" | "terminal";

const terminalStates = new Set([
  "completed", "blocked", "failed", "cancelled", "budget_limited", "expired",
]);

const stateCopy: Record<string, string> = {
  queued: "排队中", preparing: "准备工作区", running: "执行中",
  waiting_approval: "等待批准", paused: "已暂停", testing: "运行验收",
  interrupted: "已中断", completed: "已完成", blocked: "已阻塞",
  failed: "失败", cancelled: "已取消", budget_limited: "预算已用完", expired: "已过期",
};

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function payloadText(event: CodingWorkerEvent) {
  const candidates = [event.payload.message, event.payload.text, event.payload.summary, event.payload.output];
  const value = candidates.find((item) => typeof item === "string");
  return typeof value === "string" ? value : null;
}

function publicPlanText(event: CodingWorkerEvent) {
  if (event.type !== "provider_event" || event.payload.kind !== "plan") return null;
  const data = event.payload.data;
  if (typeof data === "string") return data;
  if (!data || typeof data !== "object") return null;
  const record = data as Record<string, unknown>;
  const value = [record.summary, record.message, record.text].find((item) => typeof item === "string");
  return typeof value === "string" ? value : JSON.stringify(record);
}

function approvalField(request: Record<string, unknown>, key: string) {
  const value = request[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "未请求";
}

function outputTone(stream: CodingWorkerOperationOutputChunk["stream"]) {
  if (stream === "stderr") return "text-rose-200";
  if (stream === "system") return "text-amber-200";
  return "text-slate-200";
}

interface CodingWorkerConsoleProps {
  context: ConsoleContext;
  onCodingHandoff?: (result: CodingWorkerHandoffResult) => void;
}

export default function CodingWorkerConsole({ context, onCodingHandoff }: CodingWorkerConsoleProps) {
  const [status, setStatus] = useState<CodingWorkerStatus | null>(null);
  const [tasks, setTasks] = useState<CodingWorkerTask[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [events, setEvents] = useState<CodingWorkerEvent[]>([]);
  const [approvals, setApprovals] = useState<CodingWorkerApproval[]>([]);
  const [evidence, setEvidence] = useState<CodingWorkerEvidence[]>([]);
  const [artifacts, setArtifacts] = useState<CodingWorkerArtifact[]>([]);
  const [entries, setEntries] = useState<CodingWorkerEntry[]>([]);
  const [treeHash, setTreeHash] = useState("");
  const [operationOutputs, setOperationOutputs] = useState<Record<string, CodingWorkerOperationOutputChunk[]>>({});
  const [changesets, setChangesets] = useState<CodingWorkerChangeset[]>([]);
  const [diagnostics, setDiagnostics] = useState<CodingWorkerDiagnosticsSnapshot[]>([]);
  const [inspectorLoading, setInspectorLoading] = useState(false);
  const [preview, setPreview] = useState<{ path: string; content: string } | null>(null);
  const [diff, setDiff] = useState("");
  const [tab, setTab] = useState<InspectorTab>("files");
  const [message, setMessage] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [objective, setObjective] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [revision, setRevision] = useState("");
  const [checkId, setCheckId] = useState("");
  const [codingProjects, setCodingProjects] = useState<CodingProjectSummary[]>([]);
  const [selection, setSelection] = useState<CodingProjectSelection | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [transportWarning, setTransportWarning] = useState(false);
  const [error, setError] = useState("");
  const operationRef = useRef(false);
  const selectionProjectIdsRef = useRef<Set<string>>(new Set());

  const selectedTask = useMemo(
    () => tasks.find((task) => task.task_id === selectedTaskId) ?? null,
    [selectedTaskId, tasks],
  );
  const operationIds = useMemo(() => {
    const ids = new Set<string>();
    events.forEach((event) => {
      const operationId = event.payload.operation_id;
      if (typeof operationId === "string" && operationId.length <= 128) ids.add(operationId);
    });
    return [...ids].slice(-32);
  }, [events]);
  const latestPlan = useMemo(
    () => events.map(publicPlanText).filter((item): item is string => Boolean(item)).at(-1) ?? null,
    [events],
  );

  const refreshCodingProjects = useCallback(async (preferredId?: string, selectNew = false) => {
    if (context !== "coding") return null;
    const response = await getCodingProjects();
    const hostProjects = response.projects.filter((project) => project.kind === "host_git");
    setCodingProjects(hostProjects);
    const preferred = hostProjects.find(
      (project) => project.id === preferredId && project.state === "available" && project.head,
    );
    const newlyAdded = selectNew
      ? hostProjects.filter(
        (project) => !selectionProjectIdsRef.current.has(project.id)
          && project.state === "available"
          && project.head,
      )
      : [];
    const selected = preferred ?? (newlyAdded.length === 1 ? newlyAdded[0] : undefined);
    if (selected?.head) {
      const binding = await getCodingWorkerHostSource(selected.id);
      if (
        binding.source_id !== selected.id
        || binding.branch !== selected.branch
        || !binding.revision.startsWith(selected.head)
      ) {
        throw new Error("本地项目基准已改变，请刷新后重新选择。");
      }
      setSourceId(binding.source_id);
      setRevision(binding.revision);
      return selected.id;
    }
    return null;
  }, [context]);

  const refreshTasks = useCallback(async (preferredId?: string) => {
    const next = await listCodingWorkerTasks();
    setTasks(next);
    setSelectedTaskId((current) => {
      const candidate = preferredId ?? current;
      return candidate && next.some((task) => task.task_id === candidate)
        ? candidate
        : next[0]?.task_id ?? null;
    });
    return next;
  }, []);

  const refreshTaskPanels = useCallback(async (taskId: string) => {
    const [task, approvalItems, evidenceItems, artifactItems, tree, nextDiff] = await Promise.all([
      getCodingWorkerTask(taskId),
      listCodingWorkerApprovals(taskId),
      listCodingWorkerEvidence(taskId),
      listCodingWorkerArtifacts(taskId),
      listCodingWorkerTree(taskId).catch(() => null),
      readCodingWorkerDiff(taskId).catch(() => ""),
    ]);
    setTasks((current) => current.map((item) => item.task_id === taskId ? task : item));
    setApprovals(approvalItems);
    setEvidence(evidenceItems);
    setArtifacts(artifactItems);
    setEntries(tree?.entries ?? []);
    setTreeHash(tree?.tree_hash ?? "");
    setDiff(nextDiff);
  }, []);

  useEffect(() => {
    let active = true;
    setLoading(true);
    void Promise.all([getCodingWorkerStatus(), refreshTasks()])
      .then(([nextStatus]) => {
        if (!active) return;
        setStatus(nextStatus);
        setCheckId((current) => nextStatus.acceptance_checks.includes(current)
          ? current
          : nextStatus.acceptance_checks[0] ?? "");
      })
      .catch((caught) => { if (active) setError(errorMessage(caught, "Coding Worker 加载失败")); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refreshTasks]);

  useEffect(() => {
    if (context !== "coding") return;
    void refreshCodingProjects().catch((caught) => {
      setError(errorMessage(caught, "本地项目列表加载失败"));
    });
  }, [context, refreshCodingProjects]);

  useEffect(() => {
    if (!selection || !["pending", "dispatched"].includes(selection.status)) return;
    let active = true;
    let timer = 0;
    const poll = async () => {
      try {
        const next = await getCodingProjectSelection(selection.request_id);
        if (!active) return;
        setSelection(next);
        if (next.status === "completed" && next.project_id) {
          await refreshCodingProjects(next.project_id);
          return;
        }
        if (next.status === "failed" || next.status === "expired") {
          const recoveredProjectId = await refreshCodingProjects(undefined, true);
          if (recoveredProjectId) return;
          setError(next.error === "project_selection_cancelled"
            ? "已取消选择，本地项目列表没有变化。"
            : next.status === "expired"
              ? "选择请求已超时；若 Helper 已完成授权，请从本地项目列表中选择该项目。"
              : "没有添加项目，请重新选择一个干净的 Git 仓库。");
          return;
        }
        timer = window.setTimeout(() => void poll(), 800);
      } catch (caught) {
        if (active) setError(errorMessage(caught, "本地项目选择失败"));
      }
    };
    timer = window.setTimeout(() => void poll(), 500);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [refreshCodingProjects, selection]);

  useEffect(() => {
    if (!selectedTaskId) {
      setEvents([]); setApprovals([]); setEvidence([]); setArtifacts([]);
      setEntries([]); setPreview(null); setDiff(""); setTreeHash("");
      setOperationOutputs({}); setChangesets([]); setDiagnostics([]);
      return;
    }
    let active = true;
    let cursor = 0;
    setEvents([]);
    setPreview(null);
    setOperationOutputs({});
    setChangesets([]);
    setDiagnostics([]);
    setTransportWarning(false);
    void refreshTaskPanels(selectedTaskId).catch((caught) => {
      if (active) setError(errorMessage(caught, "任务详情加载失败"));
    });
    const disconnect = connectCodingWorkerEvents(selectedTaskId, cursor, {
      onEvent: (event) => {
        if (!active || event.sequence <= cursor) return;
        cursor = event.sequence;
        setEvents((current) => [...current, event].slice(-400));
        setTransportWarning(false);
        void refreshTaskPanels(selectedTaskId).catch(() => setTransportWarning(true));
      },
      onTransportError: () => { if (active) setTransportWarning(true); },
    });
    return () => { active = false; disconnect(); };
  }, [refreshTaskPanels, selectedTaskId]);

  useEffect(() => {
    if (!selectedTaskId || operationIds.length === 0) return;
    let active = true;
    const loadStructuredInspector = async () => {
      setInspectorLoading(true);
      try {
        if (tab === "terminal" && status?.capabilities.operation_output) {
          const values = await Promise.all(operationIds.map(async (operationId) => [
            operationId,
            await listCodingWorkerOperationOutput(selectedTaskId, operationId).catch(() => []),
          ] as const));
          if (active) setOperationOutputs(Object.fromEntries(values));
        } else if (tab === "changesets" && status?.capabilities.changesets) {
          const values = await Promise.all(operationIds.map((operationId) =>
            getCodingWorkerChangeset(selectedTaskId, operationId).catch(() => null)));
          if (active) setChangesets(values.filter((item): item is CodingWorkerChangeset => item !== null));
        } else if (tab === "diagnostics" && status?.capabilities.code_intelligence) {
          const values = await Promise.all(operationIds.map((operationId) =>
            getCodingWorkerDiagnostics(selectedTaskId, operationId).catch(() => null)));
          if (active) setDiagnostics(values.filter((item): item is CodingWorkerDiagnosticsSnapshot => item !== null));
        }
      } finally {
        if (active) setInspectorLoading(false);
      }
    };
    void loadStructuredInspector();
    return () => { active = false; };
  }, [operationIds, selectedTaskId, status?.capabilities, tab]);

  const run = useCallback(async (operation: () => Promise<unknown>) => {
    if (operationRef.current) return;
    operationRef.current = true;
    setBusy(true);
    setError("");
    try { await operation(); }
    catch (caught) { setError(errorMessage(caught, "操作失败")); }
    finally { operationRef.current = false; setBusy(false); }
  }, []);

  const createTask = () => run(async () => {
    if (!objective.trim() || !sourceId.trim() || !revision.trim() || !checkId.trim()) {
      setError("请填写目标、来源 ID、基准 revision 和必需检查 ID。");
      return;
    }
    const suffix = crypto.randomUUID().replaceAll("-", "");
    const spec: CodingWorkerTaskSpec = {
      client_task_id: `console_${suffix}`,
      objective: objective.trim(),
      workspace_source: { kind: context === "coding" ? "host_snapshot" : "builtin", source_id: sourceId.trim(), revision: revision.trim() },
      acceptance: {
        contract_id: `contract_${suffix}`,
        required_checks: [{ check_id: checkId.trim(), kind: "command", label: "必需检查", required: true }],
        required_artifacts: [],
      },
      policy_profile: "develop",
      model_route: "coding/default",
      budget: { max_seconds: 3600, max_turns: 64, max_tool_calls: 512, max_output_bytes: 8 * 1024 * 1024 },
      context_refs: [],
    };
    const task = await createCodingWorkerTask(spec);
    await refreshTasks(task.task_id);
    setShowCreate(false); setObjective(""); setSourceId(""); setRevision(""); setCheckId("");
  });

  const addCodingProject = () => run(async () => {
    selectionProjectIdsRef.current = new Set(codingProjects.map((project) => project.id));
    const next = await createCodingProjectSelection();
    setSelection(next);
    if (next.status === "completed" && next.project_id) {
      await refreshCodingProjects(next.project_id);
    } else if (next.status === "failed" || next.status === "expired") {
      const recoveredProjectId = await refreshCodingProjects(undefined, true);
      if (recoveredProjectId) return;
      setError(next.error === "project_selection_cancelled"
        ? "已取消选择，本地项目列表没有变化。"
        : next.status === "expired"
          ? "选择请求已超时；若 Helper 已完成授权，请从本地项目列表中选择该项目。"
          : "没有添加项目，请重新选择一个干净的 Git 仓库。");
    }
  });

  const selectCodingProject = (projectId: string) => run(async () => {
    const project = codingProjects.find((item) => item.id === projectId);
    if (!project || project.state !== "available" || !project.head) {
      setSourceId("");
      setRevision("");
      return;
    }
    const binding = await getCodingWorkerHostSource(project.id);
    if (
      binding.source_id !== project.id
      || binding.branch !== project.branch
      || !binding.revision.startsWith(project.head)
    ) {
      throw new Error("本地项目基准已改变，请刷新后重新选择。");
    }
    setSourceId(binding.source_id);
    setRevision(binding.revision);
  });

  const submitMessage = () => run(async () => {
    if (!selectedTask || !message.trim()) return;
    await sendCodingWorkerMessage(selectedTask.task_id, message.trim());
    setMessage("");
    await refreshTaskPanels(selectedTask.task_id);
  });

  const taskAction = (action: "pause" | "resume" | "cancel" | "pin" | "unpin") => run(async () => {
    if (!selectedTask) return;
    const task = await changeCodingWorkerTask(selectedTask.task_id, action);
    setTasks((current) => current.map((item) => item.task_id === task.task_id ? task : item));
  });

  const decide = (approvalId: string, decision: "approve_once" | "approve_task" | "reject") => run(async () => {
    if (!selectedTask) return;
    await decideCodingWorkerApproval(selectedTask.task_id, approvalId, decision);
    await refreshTaskPanels(selectedTask.task_id);
  });

  const openEntry = (entry: CodingWorkerEntry) => run(async () => {
    if (!selectedTask || entry.kind !== "file") return;
    const content = await readCodingWorkerEntry(selectedTask.task_id, entry.entry_id);
    setPreview({ path: entry.display_path, content });
  });

  const handoffToWriteback = () => run(async () => {
    if (!selectedTask || selectedTask.state !== "completed") return;
    const result = await handoffCodingWorkerTask(selectedTask.task_id);
    onCodingHandoff?.(result);
  });

  if (loading) return <div className="min-h-[60vh] animate-pulse rounded-xl bg-white/5" aria-label="正在加载 Coding Worker" />;

  if (!status?.enabled || !status.available) {
    return (
      <section className="mx-auto max-w-3xl px-4 py-20 text-center" role="status">
        <CircleAlert className="mx-auto h-9 w-9 text-amber-300" aria-hidden="true" />
        <h1 className="mt-4 text-xl font-semibold text-white">Coding Worker 尚未启用</h1>
        <p className="mx-auto mt-2 max-w-[65ch] text-sm text-slate-300">
          管理员启用 CODING_WORKER_V14_ENABLED 后，新任务会进入统一 Worker。已有会话继续使用原执行面。
        </p>
      </section>
    );
  }

  return (
    <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-[1800px] flex-col gap-3 px-3 py-4 lg:px-5">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 pb-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-white">Coding Worker</h1>
          <p className="mt-1 text-sm text-slate-300">两个隔离槽位，任务证据与 Diff 可在重启后继续读取。</p>
          <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Worker 通用能力">
            {([
              ["专业文件", status.capabilities.professional_file_tools],
              ["Shell", status.capabilities.shell],
              ["终端补发", status.capabilities.operation_output],
              ["原子变更", status.capabilities.changesets],
              ["代码诊断", status.capabilities.code_intelligence],
            ] as const).map(([label, available]) => (
              <span key={label} className={`rounded-full px-2.5 py-1 text-xs ${available ? "bg-cyan-300/10 text-cyan-100" : "bg-white/5 text-slate-500"}`}>
                {label} · {available ? "可用" : "关闭"}
              </span>
            ))}
          </div>
        </div>
        <button type="button" onClick={() => setShowCreate((value) => !value)} className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-cyan-400 px-4 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:opacity-50" disabled={busy}>
          <Plus className="h-4 w-4" aria-hidden="true" />创建任务
        </button>
      </header>

      {showCreate && (
        <form className="grid gap-3 border-b border-white/10 pb-4 md:grid-cols-2 xl:grid-cols-5" onSubmit={(event) => { event.preventDefault(); void createTask(); }}>
          <label className="md:col-span-2 xl:col-span-2 text-sm text-slate-200">任务目标
            <textarea value={objective} onChange={(event) => setObjective(event.target.value)} className="mt-1 min-h-24 w-full rounded-lg border border-white/15 bg-slate-950/70 px-3 py-2 text-white" maxLength={1_048_576} />
          </label>
          {context === "coding" ? (
            <div className="text-sm text-slate-200">
              <label htmlFor="worker-host-project">本地项目</label>
              <select
                id="worker-host-project"
                value={sourceId}
                onChange={(event) => void selectCodingProject(event.target.value)}
                className="mt-1 min-h-11 w-full rounded-lg border border-white/15 bg-slate-950/70 px-3 text-white outline-none focus:border-cyan-300/70 focus:ring-4 focus:ring-cyan-300/10"
              >
                <option value="">选择已授权项目</option>
                {codingProjects.map((project) => (
                  <option
                    key={project.id}
                    value={project.id}
                    disabled={project.state !== "available" || !project.head}
                  >
                    {project.name}{project.branch ? ` · ${project.branch}` : ""}
                    {project.head ? ` · ${project.head.slice(0, 12)}` : ""}
                    {project.state !== "available" ? " · 当前不可用" : ""}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => void addCodingProject()}
                disabled={busy || selection?.status === "pending" || selection?.status === "dispatched"}
                className="mt-2 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border border-cyan-300/35 px-3 text-sm font-semibold text-cyan-100 transition hover:bg-cyan-300/10 focus-visible:outline-none focus-visible:ring-4 focus-visible:ring-cyan-300/20 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <FolderPlus className="h-4 w-4" aria-hidden="true" />
                {selection?.status === "pending" || selection?.status === "dispatched"
                  ? "等待 Helper 选择"
                  : "添加本地项目"}
              </button>
              <p className="mt-2 text-xs leading-5 text-slate-400">
                点击后在 Helper 弹出的窗口中选择干净的 Git 仓库，物理路径不会发送到 Server。
              </p>
            </div>
          ) : (
            <label className="text-sm text-slate-200">不透明来源 ID<input value={sourceId} onChange={(event) => setSourceId(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-white/15 bg-slate-950/70 px-3 text-white" /></label>
          )}
          <label className="text-sm text-slate-200">基准 revision<input value={revision} onChange={(event) => setRevision(event.target.value)} readOnly={context === "coding"} className="mt-1 min-h-11 w-full rounded-lg border border-white/15 bg-slate-950/70 px-3 text-white read-only:text-slate-300" /></label>
          <label className="text-sm text-slate-200">冻结验收检查<select value={checkId} onChange={(event) => setCheckId(event.target.value)} disabled={!status.acceptance_checks.length} className="mt-1 min-h-11 w-full rounded-lg border border-white/15 bg-slate-950/70 px-3 text-white disabled:opacity-60">{status.acceptance_checks.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
          <div className="flex items-end md:col-span-2 xl:col-span-5"><button type="submit" disabled={busy || !checkId} className="min-h-11 rounded-lg bg-cyan-400 px-5 text-sm font-semibold text-slate-950 disabled:opacity-50">提交到队列</button></div>
        </form>
      )}

      {error && <div className="rounded-lg bg-rose-500/15 px-4 py-3 text-sm text-rose-100" role="alert">{error}</div>}
      {transportWarning && <div className="rounded-lg bg-amber-400/10 px-4 py-3 text-sm text-amber-100" role="status">事件连接已中断，任务状态仍可从持久存储恢复。</div>}

      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[16rem_minmax(0,1fr)] xl:grid-cols-[16rem_minmax(0,1fr)_26rem]">
        <aside className="min-h-0 border-r border-white/10 pr-3" aria-label="Worker 任务列表">
          <div className="mb-2 flex items-center justify-between"><h2 className="text-sm font-semibold text-slate-200">任务</h2><span className="text-xs text-slate-400">{tasks.length}</span></div>
          <div className="flex max-h-[38vh] flex-col gap-1 overflow-y-auto lg:max-h-[calc(100vh-11rem)]">
            {tasks.length === 0 && <p className="rounded-lg bg-white/5 p-3 text-sm text-slate-300">还没有任务。先从受控来源创建一个任务。</p>}
            {tasks.map((task) => (
              <button key={task.task_id} type="button" onClick={() => setSelectedTaskId(task.task_id)} className={`min-h-14 rounded-lg px-3 py-2 text-left transition ${task.task_id === selectedTaskId ? "bg-cyan-400/15 text-white" : "text-slate-300 hover:bg-white/5"}`}>
                <span className="block truncate text-sm font-medium">{task.spec.objective}</span>
                <span className="mt-1 flex items-center justify-between gap-2 text-xs text-slate-400"><span>{stateCopy[task.state] ?? task.state}</span><span>{task.spec.origin.module}</span></span>
              </button>
            ))}
          </div>
        </aside>

        <main className="min-w-0">
          {!selectedTask ? (
            <div className="flex min-h-80 items-center justify-center text-sm text-slate-400">选择任务后查看目标、计划和运行事件。</div>
          ) : (
            <div className="flex h-full min-h-[32rem] flex-col">
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/10 pb-3">
                <div className="min-w-0"><p className="break-words text-lg font-semibold text-white">{selectedTask.spec.objective}</p><p className="mt-1 break-all text-xs text-slate-400">{selectedTask.task_id} · {stateCopy[selectedTask.state] ?? selectedTask.state}</p></div>
                <div className="flex flex-wrap gap-2">
                  {selectedTask.state === "running" || selectedTask.state === "testing" ? <button type="button" onClick={() => void taskAction("pause")} disabled={busy} className="inline-flex min-h-11 items-center gap-1 rounded-lg border border-white/15 px-3 text-sm text-slate-200"><Pause className="h-4 w-4" />暂停</button> : null}
                  {selectedTask.state === "paused" || selectedTask.state === "interrupted" ? <button type="button" onClick={() => void taskAction("resume")} disabled={busy} className="inline-flex min-h-11 items-center gap-1 rounded-lg border border-cyan-300/40 px-3 text-sm text-cyan-100"><Play className="h-4 w-4" />继续</button> : null}
                  {!terminalStates.has(selectedTask.state) && <button type="button" onClick={() => void taskAction("cancel")} disabled={busy} className="inline-flex min-h-11 items-center gap-1 rounded-lg border border-white/15 px-3 text-sm text-slate-200"><Square className="h-4 w-4" />取消</button>}
                  <button type="button" onClick={() => void taskAction(selectedTask.pinned ? "unpin" : "pin")} disabled={busy} aria-pressed={selectedTask.pinned} className="inline-flex min-h-11 items-center gap-1 rounded-lg border border-white/15 px-3 text-sm text-slate-200"><Pin className="h-4 w-4" />{selectedTask.pinned ? "取消固定" : "固定"}</button>
                </div>
              </div>

              <section className="mt-3 border-b border-white/10 pb-3" aria-labelledby="goal-heading">
                <div className="flex items-center gap-2"><CheckCircle2 className="h-4 w-4 text-cyan-300" /><h2 id="goal-heading" className="text-sm font-semibold text-white">目标与验收</h2></div>
                <ul className="mt-2 space-y-1 text-sm text-slate-300">{selectedTask.spec.acceptance.required_checks.map((check) => <li key={check.check_id}>• {check.label} <code className="break-all text-xs text-slate-400">{check.check_id}</code></li>)}</ul>
              </section>

              {latestPlan && (
                <section className="border-b border-white/10 py-3" aria-labelledby="worker-plan-heading">
                  <div className="flex items-center justify-between gap-3">
                    <h2 id="worker-plan-heading" className="text-sm font-semibold text-white">当前公开计划</h2>
                    <span className="text-xs text-slate-500">Provider 中立</span>
                  </div>
                  <p className="mt-2 max-w-[72ch] whitespace-pre-wrap break-words text-sm leading-6 text-slate-300">{latestPlan}</p>
                </section>
              )}

              <section className="min-h-0 flex-1 overflow-y-auto py-3" aria-label="任务事件" aria-live="polite">
                {events.length === 0 ? <p className="text-sm text-slate-400">等待 Worker 事件。公开事件只包含计划、状态和工具摘要。</p> : events.map((event) => (
                  <article key={event.sequence} className="mb-3 flex gap-3"><span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-cyan-300" aria-hidden="true" /><div className="min-w-0"><p className="text-xs font-medium text-slate-400">{event.type} · #{event.sequence}</p><p className="mt-1 whitespace-pre-wrap break-words text-sm text-slate-200">{payloadText(event) ?? JSON.stringify(event.payload)}</p></div></article>
                ))}
              </section>

              <form className="border-t border-white/10 pt-3" onSubmit={(event) => { event.preventDefault(); void submitMessage(); }}>
                <label className="sr-only" htmlFor="worker-message">追加指令</label>
                <div className="flex gap-2"><textarea id="worker-message" value={message} onChange={(event) => setMessage(event.target.value)} disabled={busy || terminalStates.has(selectedTask.state)} placeholder="追加指令或运行中 steering" className="min-h-12 flex-1 resize-y rounded-lg border border-white/15 bg-slate-950/70 px-3 py-2 text-sm text-white placeholder:text-slate-400 disabled:opacity-60" /><button type="submit" disabled={busy || !message.trim() || terminalStates.has(selectedTask.state)} className="min-h-12 rounded-lg bg-cyan-400 px-4 text-sm font-semibold text-slate-950 disabled:opacity-50">发送指令</button></div>
              </form>
            </div>
          )}
        </main>

        <aside className="min-w-0 border-t border-white/10 pt-3 xl:border-l xl:border-t-0 xl:pl-3 xl:pt-0" aria-label="任务检查器">
          <div className="flex overflow-x-auto border-b border-white/10" role="tablist">
            {([ ["files", FolderTree, "文件"], ["diff", GitCompareArrows, "Diff"], ["changesets", GitCompareArrows, "变更"], ["diagnostics", CircleAlert, "诊断"], ["evidence", ShieldCheck, "证据"], ["terminal", TerminalSquare, "终端"] ] as const).map(([value, Icon, label]) => <button key={value} type="button" role="tab" aria-selected={tab === value} onClick={() => setTab(value)} className={`inline-flex min-h-11 items-center gap-1 px-3 text-sm ${tab === value ? "border-b-2 border-cyan-300 text-white" : "text-slate-400"}`}><Icon className="h-4 w-4" />{label}</button>)}
          </div>
          <div className="max-h-[46rem] overflow-auto py-3 text-sm">
            {tab === "files" && <div><p className="mb-2 break-all text-xs text-slate-400">tree {treeHash || "尚未创建"}</p>{preview ? <div><button type="button" className="mb-2 min-h-11 text-cyan-200" onClick={() => setPreview(null)}>返回文件树</button><p className="mb-2 break-all text-slate-300">{preview.path}</p><pre className="overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-950/80 p-3 text-xs text-slate-200">{preview.content}</pre></div> : <ul className="space-y-1">{entries.map((entry) => <li key={entry.entry_id}><button type="button" disabled={entry.kind !== "file" || busy} onClick={() => void openEntry(entry)} className="flex min-h-10 w-full items-center gap-2 rounded-md px-2 text-left text-slate-300 hover:bg-white/5 disabled:cursor-default"><FileCode2 className="h-4 w-4 shrink-0" /><span className="min-w-0 break-all">{entry.display_path}</span></button></li>)}</ul>}</div>}
            {tab === "diff" && <pre className="overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-950/80 p-3 text-xs text-slate-200">{diff || "工作区还没有变更。"}</pre>}
            {tab === "changesets" && (
              <div className="space-y-3" aria-busy={inspectorLoading}>
                {!status.capabilities.changesets ? <p className="text-slate-400">原子 changeset 当前关闭。</p> : null}
                {status.capabilities.changesets && changesets.length === 0 ? <p className="text-slate-400">{inspectorLoading ? "正在读取变更记录。" : "尚无 changeset。"}</p> : null}
                {changesets.map((changeset) => (
                  <section key={changeset.changeset_id} className="rounded-lg bg-white/5 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="break-all font-semibold text-white">{changeset.changeset_id}</h3><span className={changeset.state === "applied" ? "text-emerald-300" : "text-amber-200"}>{changeset.state}</span></div>
                    <p className="mt-1 break-all text-xs text-slate-400">operation {changeset.operation_id}</p>
                    <p className="mt-1 break-all text-xs text-slate-500">tree {changeset.base_tree_hash.slice(0, 12)} → {changeset.result_tree_hash?.slice(0, 12) ?? "未发布"}</p>
                    <ul className="mt-3 space-y-2">{changeset.entries.map((entry) => <li key={`${changeset.changeset_id}-${entry.entry_id}`} className="break-all text-slate-200"><span className="mr-2 rounded bg-slate-800 px-1.5 py-0.5 text-xs text-cyan-100">{entry.kind}</span>{entry.display_path}{entry.destination_display_path ? ` → ${entry.destination_display_path}` : ""}{entry.binary ? <span className="ml-2 text-xs text-amber-200">二进制</span> : null}</li>)}</ul>
                    {changeset.artifact_id ? <a className="mt-3 block min-h-11 break-all py-2 text-cyan-200" href={codingWorkerArtifactUrl(changeset.task_id, changeset.artifact_id)}>下载 changeset Artifact</a> : null}
                  </section>
                ))}
              </div>
            )}
            {tab === "diagnostics" && (
              <div className="space-y-3" aria-busy={inspectorLoading}>
                {!status.capabilities.code_intelligence ? <p className="text-slate-400">代码诊断当前关闭。</p> : null}
                {status.capabilities.code_intelligence && diagnostics.length === 0 ? <p className="text-slate-400">{inspectorLoading ? "正在读取诊断。" : "尚无诊断结果。"}</p> : null}
                {diagnostics.map((snapshot) => (
                  <section key={snapshot.operation_id} className="rounded-lg bg-white/5 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="break-all font-semibold text-white">{entries.find((entry) => entry.entry_id === snapshot.entry_id)?.display_path ?? snapshot.entry_id}</h3><span className="text-xs text-slate-400">{snapshot.language}</span></div>
                    {snapshot.stale ? <p className="mt-2 rounded-md bg-amber-300/10 px-2 py-1.5 text-xs text-amber-100" role="status">工作区已改变，此诊断仅作历史证据。</p> : null}
                    <ul className="mt-3 space-y-2">{snapshot.diagnostics.map((item) => <li key={item.diagnostic_id} className="break-words text-slate-200"><span className={item.severity === "error" ? "text-rose-300" : item.severity === "warning" ? "text-amber-200" : "text-cyan-200"}>{item.severity}</span><span className="ml-2 text-xs text-slate-500">{item.range.start.line + 1}:{item.range.start.character + 1}{item.code ? ` · ${item.code}` : ""}</span><p className="mt-1 leading-5">{item.message}</p></li>)}</ul>
                  </section>
                ))}
              </div>
            )}
            {tab === "evidence" && <div className="space-y-4"><section><h3 className="font-semibold text-white">审批</h3>{approvals.filter((item) => item.status === "pending").map((item) => <div key={item.approval_id} className="mt-2 rounded-lg bg-amber-300/10 p-3"><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-amber-100">{item.capability === "shell" ? "Shell 单次审批" : item.capability}</p><code className="break-all text-xs text-slate-400">{item.operation_id}</code></div>{item.capability === "shell" ? <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs"><dt className="text-slate-400">模式</dt><dd className="break-all text-slate-200">{approvalField(item.request, "mode")}</dd><dt className="text-slate-400">工作目录</dt><dd className="break-all text-slate-200">{approvalField(item.request, "cwd")}</dd><dt className="text-slate-400">超时</dt><dd className="break-all text-slate-200">{approvalField(item.request, "timeout_seconds")} 秒</dd><dt className="text-slate-400">脚本摘要</dt><dd className="break-all font-mono text-slate-200">{approvalField(item.request, "script_sha256")}</dd><dt className="text-slate-400">网络范围</dt><dd className="break-all font-mono text-slate-200">{approvalField(item.request, "network_scope_sha256")}</dd></dl> : <p className="mt-1 break-words text-xs text-slate-300">{JSON.stringify(item.request)}</p>}<div className="mt-3 flex flex-wrap gap-2"><button type="button" disabled={busy} onClick={() => void decide(item.approval_id, "approve_once")} className="min-h-11 rounded-lg bg-cyan-400 px-3 font-semibold text-slate-950">批准一次</button>{item.capability !== "shell" ? <button type="button" disabled={busy} onClick={() => void decide(item.approval_id, "approve_task")} className="min-h-11 rounded-lg border border-cyan-300/40 px-3 text-cyan-100">本任务批准</button> : null}<button type="button" disabled={busy} onClick={() => void decide(item.approval_id, "reject")} className="min-h-11 rounded-lg border border-white/15 px-3 text-slate-200">拒绝</button></div></div>)}</section><section><h3 className="font-semibold text-white">检查证据</h3><ul className="mt-2 space-y-2">{evidence.map((item) => <li key={item.evidence_id} className="rounded-lg bg-white/5 p-3"><span className={item.status === "passed" ? "text-emerald-300" : item.status === "failed" ? "text-rose-300" : "text-amber-300"}>{item.status}</span><span className="ml-2 break-all text-slate-200">{item.check_id}</span><span className="mt-1 block text-xs text-slate-400">exit {item.exit_code}</span></li>)}</ul></section><section><h3 className="font-semibold text-white">Artifacts</h3><ul className="mt-2 space-y-1">{artifacts.map((item) => <li key={item.artifact_id}><a className="block min-h-11 break-all rounded-md px-2 py-2.5 text-cyan-200 hover:bg-white/5" href={codingWorkerArtifactUrl(item.task_id, item.artifact_id)}>{item.artifact_id} · {item.media_type}</a></li>)}</ul></section></div>}
            {tab === "terminal" && (
              <div aria-busy={inspectorLoading}>
                <p className="mb-2 text-xs text-slate-400">输出按 operation 归属并从持久事件补发；刷新页面不会丢失已归档片段。</p>
                {!status.capabilities.operation_output ? <p className="text-slate-400">终端补发当前关闭。</p> : null}
                {status.capabilities.operation_output && Object.values(operationOutputs).every((items) => items.length === 0) ? <p className="text-slate-400">{inspectorLoading ? "正在补发终端输出。" : "尚无命令输出。"}</p> : null}
                <div className="space-y-3">{Object.entries(operationOutputs).filter(([, chunks]) => chunks.length > 0).map(([operationId, chunks]) => <section key={operationId} className="overflow-hidden rounded-lg bg-slate-950/80"><h3 className="border-b border-white/10 px-3 py-2 break-all text-xs font-semibold text-cyan-100">{operationId}</h3><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words p-3 text-xs">{chunks.map((chunk) => <span key={chunk.sequence} className={outputTone(chunk.stream)}>{chunk.text}{chunk.truncated ? "\n[输出已截断]\n" : ""}</span>)}</pre></section>)}</div>
              </div>
            )}
          </div>
          {context === "coding" && selectedTask && <section className="mt-3 border-t border-white/10 pt-3"><div className="flex items-center gap-2"><GitCompareArrows className="h-4 w-4 text-cyan-300" /><h3 className="font-semibold text-white">宿主写回</h3></div><p className="mt-1 text-xs text-slate-400">完成后继续使用 v13 的应用、提交、撤销和发布确认链。Worker 不直接写用户仓库。</p>{selectedTask.state === "completed" && selectedTask.spec.workspace_source.kind === "host_snapshot" ? <button type="button" onClick={() => void handoffToWriteback()} disabled={busy} className="mt-3 min-h-11 w-full rounded-lg bg-cyan-400 px-3 text-sm font-semibold text-slate-950 transition hover:bg-cyan-300 disabled:opacity-50">进入 v13 写回确认</button> : <p className="mt-3 text-xs text-slate-500">Host Snapshot 任务通过全部必需检查后，才会开放写回确认。</p>}<div className="mt-3 flex flex-wrap gap-2" aria-label="Coding 领域动作"><span className="rounded-full bg-white/5 px-3 py-1 text-xs text-slate-300">应用</span><span className="rounded-full bg-white/5 px-3 py-1 text-xs text-slate-300">提交</span><span className="rounded-full bg-white/5 px-3 py-1 text-xs text-slate-300">撤销</span><span className="rounded-full bg-white/5 px-3 py-1 text-xs text-slate-300">发布</span></div></section>}
        </aside>
      </div>
    </div>
  );
}
