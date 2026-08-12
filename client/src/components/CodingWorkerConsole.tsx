import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Archive,
  ArrowRight,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Code2,
  Eye,
  FileCode2,
  FolderPlus,
  FolderTree,
  GitCompareArrows,
  History,
  Menu,
  Pause,
  Pin,
  Play,
  Plus,
  Search,
  Send,
  ShieldCheck,
  Square,
  TestTube2,
  TerminalSquare,
  X,
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
import {
  activitiesFromEvents,
  currentProgressStage,
  evidenceStatus,
  formatRelativeTime,
  groupTasks,
  pendingApprovals,
  routeLabel,
  shortId,
  taskStateCopy,
  terminalTaskStates,
  type WorkerProgressStage,
  type WorkerTaskGroup,
} from "./coding-worker/viewModel";

type ConsoleContext = "coding" | "agent";
type InspectorTab = "files" | "diff" | "changesets" | "diagnostics" | "evidence" | "terminal";

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function publicPlanText(event: CodingWorkerEvent) {
  if (event.type !== "provider_event" || event.payload.kind !== "plan") return null;
  const data = event.payload.data;
  if (typeof data === "string") return data;
  if (!data || typeof data !== "object") return null;
  const record = data as Record<string, unknown>;
  const value = [record.summary, record.message, record.text].find((item) => typeof item === "string");
  return typeof value === "string" ? value : "计划已更新。";
}

function approvalField(request: Record<string, unknown>, key: string) {
  const value = request[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : null;
}

function outputTone(stream: CodingWorkerOperationOutputChunk["stream"]) {
  if (stream === "stderr") return "text-rose-200";
  if (stream === "system") return "text-amber-200";
  return "text-slate-200";
}

const taskGroupCopy: Record<WorkerTaskGroup, { label: string; empty: string }> = {
  attention: { label: "需要处理", empty: "没有待处理任务" },
  active: { label: "运行中", empty: "没有运行中任务" },
  queued: { label: "排队", empty: "队列为空" },
  history: { label: "最近任务", empty: "暂无历史任务" },
};

const progressStages: Array<{ value: WorkerProgressStage; label: string }> = [
  { value: "analyze", label: "分析" },
  { value: "reproduce", label: "复现" },
  { value: "change", label: "修改" },
  { value: "verify", label: "验收" },
];

function taskStateTone(state: CodingWorkerTask["state"]) {
  if (["completed"].includes(state)) return "text-emerald-200";
  if (["waiting_approval", "interrupted", "blocked", "budget_limited"].includes(state)) return "text-amber-200";
  if (["failed", "cancelled", "expired"].includes(state)) return "text-rose-200";
  if (["running", "testing", "preparing"].includes(state)) return "text-cyan-200";
  return "text-slate-300";
}

function approvalSummary(approval: CodingWorkerApproval) {
  const command = approval.request.command ?? approval.request.script;
  if (typeof command === "string" && command.trim()) return command;
  return approval.capability === "shell"
    ? `Shell ${approvalField(approval.request, "mode") ?? "受控执行"}`
    : "执行一次受控命令";
}

function approvalCapabilityLabel(capability: string) {
  if (capability === "shell") return "Shell 单次审批";
  if (capability === "command") return "命令单次审批";
  return `${capability} 单次审批`;
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
  const [showMobileTasks, setShowMobileTasks] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [taskQuery, setTaskQuery] = useState("");
  const [objective, setObjective] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [revision, setRevision] = useState("");
  const [checkIds, setCheckIds] = useState<string[]>([]);
  const [modelRoute, setModelRoute] = useState("coding/default");
  const [codingProjects, setCodingProjects] = useState<CodingProjectSummary[]>([]);
  const [selection, setSelection] = useState<CodingProjectSelection | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [transportWarning, setTransportWarning] = useState(false);
  const [error, setError] = useState("");
  const operationRef = useRef(false);
  const selectionProjectIdsRef = useRef<Set<string>>(new Set());
  const refreshTimerRef = useRef<number | null>(null);

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
  const routeOptions = useMemo(
    () => status?.model_routes?.length ? status.model_routes : ["coding/default"],
    [status?.model_routes],
  );
  const filteredTasks = useMemo(() => {
    const query = taskQuery.trim().toLocaleLowerCase();
    return query
      ? tasks.filter((task) => `${task.spec.objective} ${task.spec.origin.module} ${taskStateCopy[task.state]}`.toLocaleLowerCase().includes(query))
      : tasks;
  }, [taskQuery, tasks]);
  const groupedTasks = useMemo(() => groupTasks(filteredTasks), [filteredTasks]);
  const activities = useMemo(() => activitiesFromEvents(events), [events]);
  const approvalsPending = useMemo(() => pendingApprovals(approvals), [approvals]);
  const progressStage = useMemo(
    () => selectedTask ? currentProgressStage(selectedTask, events) : "analyze",
    [events, selectedTask],
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
        setCheckIds((current) => {
          const retained = current.filter((item) => nextStatus.acceptance_checks.includes(item));
          return retained.length ? retained : nextStatus.acceptance_checks.slice(0, 1);
        });
        setModelRoute((current) => nextStatus.model_routes?.includes(current)
          ? current
          : nextStatus.model_routes?.[0] ?? "coding/default");
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
        if (refreshTimerRef.current !== null) window.clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = window.setTimeout(() => {
          void refreshTaskPanels(selectedTaskId).catch(() => setTransportWarning(true));
        }, 350);
      },
      onTransportError: () => { if (active) setTransportWarning(true); },
    });
    return () => {
      active = false;
      disconnect();
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
    };
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
    if (!objective.trim() || !sourceId.trim() || !revision.trim() || checkIds.length === 0) {
      setError("请填写任务目标、选择受控来源，并至少勾选一项必需检查。");
      return;
    }
    const suffix = crypto.randomUUID().replaceAll("-", "");
    const spec: CodingWorkerTaskSpec = {
      client_task_id: `console_${suffix}`,
      objective: objective.trim(),
      workspace_source: { kind: context === "coding" ? "host_snapshot" : "builtin", source_id: sourceId.trim(), revision: revision.trim() },
      acceptance: {
        contract_id: `contract_${suffix}`,
        required_checks: checkIds.map((item) => ({ check_id: item, kind: "command", label: item, required: true })),
        required_artifacts: [],
      },
      policy_profile: "develop",
      model_route: modelRoute,
      budget: { max_seconds: 3600, max_turns: 64, max_tool_calls: 512, max_output_bytes: 8 * 1024 * 1024 },
      context_refs: [],
    };
    const task = await createCodingWorkerTask(spec);
    await refreshTasks(task.task_id);
    setShowCreate(false); setObjective(""); setSourceId(""); setRevision(""); setCheckIds([]);
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
    <div className="mx-auto flex min-h-[calc(100vh-5rem)] max-w-[1920px] flex-col px-3 py-3 lg:px-5">
      <header className="flex flex-wrap items-center gap-x-5 gap-y-3 border-b border-white/10 pb-3">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-cyan-300/10 text-cyan-200">
            <Code2 className="h-5 w-5" aria-hidden="true" />
          </span>
          <div className="min-w-0">
            <h1 className="text-lg font-semibold text-white">Coding Worker</h1>
            <p className="truncate text-xs text-slate-400">隔离执行 · 证据持久化 · Provider 中立</p>
          </div>
        </div>

        {selectedTask && (
          <div className="order-3 min-w-0 basis-full lg:order-none lg:basis-auto">
            <p className="truncate text-sm font-medium text-slate-100">{selectedTask.spec.objective}</p>
            <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-xs text-slate-400">
              <span>{routeLabel(selectedTask.spec.model_route)}</span><span aria-hidden="true">·</span>
              <span>{selectedTask.spec.workspace_source.kind === "host_snapshot" ? "Host Snapshot" : selectedTask.spec.workspace_source.kind}</span><span aria-hidden="true">·</span>
              <code>{shortId(selectedTask.spec.workspace_source.revision, 6)}</code>
            </p>
          </div>
        )}

        <div className="ml-auto flex items-center gap-2">
          <span className="hidden items-center gap-2 text-xs text-slate-400 sm:inline-flex">
            <span className="h-2 w-2 rounded-full bg-emerald-300" aria-hidden="true" />
            {Math.min(tasks.filter((task) => ["preparing", "running", "testing"].includes(task.state)).length, status.max_active_tasks)} / {status.max_active_tasks} 槽位活跃
          </span>
          <button
            type="button"
            className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-cyan-300 px-4 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200 disabled:opacity-50"
            onClick={() => setShowCreate(true)}
            disabled={busy}
          >
            <Plus className="h-4 w-4" aria-hidden="true" />创建任务
          </button>
        </div>
      </header>

      {showCreate && (
        <section className="border-b border-white/10 py-5" aria-labelledby="worker-create-heading">
          <div className="mx-auto max-w-6xl">
            <div className="mb-5 flex items-start justify-between gap-4">
              <div><h2 id="worker-create-heading" className="text-xl font-semibold text-white">新建开发任务</h2><p className="mt-1 text-sm text-slate-400">项目基准、执行方式与验收检查会在任务创建后冻结。</p></div>
              <button type="button" onClick={() => setShowCreate(false)} className="grid min-h-11 min-w-11 place-items-center rounded-lg text-slate-300 hover:bg-white/5" aria-label="关闭创建任务"><X className="h-5 w-5" /></button>
            </div>
            <form className="grid gap-x-6 gap-y-4 lg:grid-cols-[minmax(0,1fr)_20rem]" onSubmit={(event) => { event.preventDefault(); void createTask(); }}>
              <div className="space-y-4">
                <label className="block text-sm font-medium text-slate-200">任务目标
                  <textarea value={objective} onChange={(event) => setObjective(event.target.value)} className="mt-1.5 min-h-32 w-full resize-y rounded-lg border border-white/15 bg-slate-950/70 px-3 py-2.5 text-white placeholder:text-slate-500" placeholder="说明要修复或实现的内容、不能改变的边界，以及期望的复现和复测方式。" maxLength={1_048_576} />
                </label>
                <div className="grid gap-4 sm:grid-cols-2">
                  {context === "coding" ? (
                    <div className="text-sm font-medium text-slate-200">
                      <label htmlFor="worker-host-project">项目</label>
                      <select id="worker-host-project" value={sourceId} onChange={(event) => void selectCodingProject(event.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-white/15 bg-slate-950/70 px-3 text-white">
                        <option value="">选择已授权项目</option>
                        {codingProjects.map((project) => <option key={project.id} value={project.id} disabled={project.state !== "available" || !project.head}>{project.name}{project.branch ? ` · ${project.branch}` : ""}{project.state !== "available" ? " · 当前不可用" : ""}</option>)}
                      </select>
                      <button type="button" onClick={() => void addCodingProject()} disabled={busy || selection?.status === "pending" || selection?.status === "dispatched"} className="mt-2 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border border-cyan-300/35 px-3 text-sm text-cyan-100 hover:bg-cyan-300/10 disabled:opacity-50">
                        <FolderPlus className="h-4 w-4" />{selection?.status === "pending" || selection?.status === "dispatched" ? "等待 Helper 选择" : "添加本地项目"}
                      </button>
                    </div>
                  ) : <label className="text-sm font-medium text-slate-200">不透明来源 ID<input value={sourceId} onChange={(event) => setSourceId(event.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-white/15 bg-slate-950/70 px-3 text-white" /></label>}
                  <label className="text-sm font-medium text-slate-200">执行方式
                    <select value={modelRoute} onChange={(event) => setModelRoute(event.target.value)} className="mt-1.5 min-h-11 w-full rounded-lg border border-white/15 bg-slate-950/70 px-3 text-white">
                      {routeOptions.map((route) => <option key={route} value={route}>{routeLabel(route)}{route === "coding/default" ? " · 常规修复" : route === "coding/quality" ? " · 复杂诊断" : ""}</option>)}
                    </select>
                  </label>
                  <label className="text-sm font-medium text-slate-200">基准 revision<input value={revision} onChange={(event) => setRevision(event.target.value)} readOnly={context === "coding"} className="mt-1.5 min-h-11 w-full rounded-lg border border-white/15 bg-slate-950/70 px-3 font-mono text-xs text-white read-only:text-slate-300" /></label>
                  <fieldset className="text-sm font-medium text-slate-200"><legend>必需检查</legend><div className="mt-1.5 space-y-1.5">{status.acceptance_checks.length === 0 ? <p className="min-h-11 rounded-lg border border-white/10 px-3 py-3 text-xs text-slate-500">当前没有可用检查</p> : status.acceptance_checks.map((item) => <label key={item} className="flex min-h-11 cursor-pointer items-center gap-2 rounded-lg border border-white/10 px-3 text-sm text-slate-200 hover:bg-white/5"><input type="checkbox" checked={checkIds.includes(item)} onChange={(event) => setCheckIds((current) => event.target.checked ? [...current, item] : current.filter((value) => value !== item))} className="h-4 w-4 accent-cyan-300" /><span className="break-all">{item}</span></label>)}</div></fieldset>
                </div>
              </div>
              <aside className="border-t border-white/10 pt-4 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-0">
                <h3 className="text-sm font-semibold text-white">任务边界</h3>
                <ul className="mt-3 space-y-3 text-sm leading-5 text-slate-300">
                  <li className="flex gap-2"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-cyan-200" /><span>Worker 只修改隔离 Workspace，宿主写回继续走 v13 确认链。</span></li>
                  <li className="flex gap-2"><TerminalSquare className="mt-0.5 h-4 w-4 shrink-0 text-cyan-200" /><span>Shell 按操作审批，绑定脚本摘要、目录和超时。</span></li>
                  <li className="flex gap-2"><GitCompareArrows className="mt-0.5 h-4 w-4 shrink-0 text-cyan-200" /><span>必需检查全部通过后，任务才会标记为完成。</span></li>
                </ul>
                <button type="submit" disabled={busy || checkIds.length === 0 || !modelRoute} className="mt-5 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-cyan-300 px-4 text-sm font-semibold text-slate-950 hover:bg-cyan-200 disabled:opacity-50"><Play className="h-4 w-4" />创建并开始</button>
              </aside>
            </form>
          </div>
        </section>
      )}

      {error && <div className="rounded-lg bg-rose-500/15 px-4 py-3 text-sm text-rose-100" role="alert">{error}</div>}
      {transportWarning && <div className="rounded-lg bg-amber-400/10 px-4 py-3 text-sm text-amber-100" role="status">事件连接已中断，任务状态仍可从持久存储恢复。</div>}

      <div className="grid min-h-0 flex-1 gap-0 lg:grid-cols-[18rem_minmax(0,1fr)] xl:grid-cols-[18rem_minmax(0,1fr)_27rem]">
        <div className="flex items-center justify-between border-b border-white/10 py-2 lg:hidden">
          <button type="button" onClick={() => setShowMobileTasks((value) => !value)} className="inline-flex min-h-11 items-center gap-2 text-sm font-medium text-slate-200" aria-expanded={showMobileTasks}><Menu className="h-4 w-4" />{showMobileTasks ? "返回当前任务" : "查看任务列表"}</button>
          <span className={selectedTask ? `text-xs ${taskStateTone(selectedTask.state)}` : "text-xs text-slate-400"}>{selectedTask ? taskStateCopy[selectedTask.state] : `${tasks.length} 个任务`}</span>
        </div>

        <aside className={`${showMobileTasks || !selectedTask ? "block" : "hidden"} min-h-0 border-b border-white/10 py-3 lg:block lg:border-b-0 lg:border-r lg:pr-3`} aria-label="Worker 任务列表">
          <div className="flex items-center gap-2">
            <label className="relative min-w-0 flex-1"><span className="sr-only">搜索任务</span><Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" /><input value={taskQuery} onChange={(event) => setTaskQuery(event.target.value)} placeholder="搜索任务" className="min-h-11 w-full rounded-lg border border-white/10 bg-slate-950/55 pl-9 pr-3 text-sm text-white placeholder:text-slate-500" /></label>
          </div>
          <div className="mt-3 max-h-[38rem] space-y-4 overflow-y-auto pr-1 lg:max-h-[calc(100vh-12rem)]">
            {tasks.length === 0 && <p className="py-8 text-center text-sm text-slate-400">还没有任务。创建任务后会在这里跟踪执行状态。</p>}
            {(["attention", "active", "queued", "history"] as WorkerTaskGroup[]).map((group) => {
              const items = groupedTasks[group];
              if (group === "history" && !showHistory) {
                return items.length ? <button key={group} type="button" onClick={() => setShowHistory(true)} className="flex min-h-11 w-full items-center justify-between text-sm text-slate-400 hover:text-slate-200"><span className="inline-flex items-center gap-2"><History className="h-4 w-4" />查看最近任务</span><span>{items.length}</span></button> : null;
              }
              if (!items.length && (taskQuery || group === "history")) return null;
              return <section key={group} aria-labelledby={`worker-group-${group}`}>
                <div className="mb-1.5 flex items-center justify-between"><h2 id={`worker-group-${group}`} className="text-xs font-medium text-slate-400">{taskGroupCopy[group].label}</h2><span className="text-xs text-slate-600">{items.length}</span></div>
                {items.length === 0 ? <p className="px-2 py-2 text-xs text-slate-600">{taskGroupCopy[group].empty}</p> : <div className="space-y-1">{items.slice(0, group === "history" ? 12 : undefined).map((task) => <button key={task.task_id} type="button" onClick={() => { setSelectedTaskId(task.task_id); setShowMobileTasks(false); }} className={`w-full rounded-lg px-3 py-2.5 text-left transition ${task.task_id === selectedTaskId ? "bg-cyan-300/10 text-white" : "text-slate-300 hover:bg-white/5"}`} aria-current={task.task_id === selectedTaskId ? "true" : undefined}>
                  <span className="line-clamp-2 text-sm font-medium leading-5">{task.spec.objective}</span>
                  <span className="mt-1.5 flex items-center justify-between gap-2 text-xs"><span className={taskStateTone(task.state)}>{taskStateCopy[task.state]}</span><span className="text-slate-500">{formatRelativeTime(task.updated_at)}</span></span>
                </button>)}</div>}
              </section>;
            })}
            {taskQuery && filteredTasks.length === 0 && <p className="py-8 text-center text-sm text-slate-400">没有匹配的任务。</p>}
          </div>
        </aside>

        <main className={`${showMobileTasks && selectedTask ? "hidden" : "block"} min-w-0 py-3 lg:block lg:px-5`}>
          {!selectedTask ? <div className="flex min-h-96 flex-col items-center justify-center text-center"><Activity className="h-8 w-8 text-slate-600" /><p className="mt-3 text-sm text-slate-300">选择任务后查看目标、下一动作和执行证据。</p></div> : <div className="flex h-full min-h-[36rem] flex-col">
            <section className="border-b border-white/10 pb-4" aria-labelledby="worker-task-heading">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0"><h2 id="worker-task-heading" className="max-w-[65ch] break-words text-xl font-semibold text-white">{selectedTask.spec.objective}</h2><p className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-400"><span className={taskStateTone(selectedTask.state)}>{taskStateCopy[selectedTask.state]}</span><span>{selectedTask.spec.origin.module}</span><code>{shortId(selectedTask.task_id)}</code></p></div>
                <div className="flex items-center gap-1">
                  {selectedTask.state === "running" || selectedTask.state === "testing" ? <button type="button" onClick={() => void taskAction("pause")} disabled={busy} className="grid min-h-11 min-w-11 place-items-center rounded-lg text-slate-300 hover:bg-white/5" aria-label="暂停任务"><Pause className="h-4 w-4" /></button> : null}
                  {selectedTask.state === "paused" || selectedTask.state === "interrupted" ? <button type="button" onClick={() => void taskAction("resume")} disabled={busy} className="inline-flex min-h-11 items-center gap-2 rounded-lg border border-cyan-300/35 px-3 text-sm text-cyan-100"><Play className="h-4 w-4" />继续任务</button> : null}
                  {!terminalTaskStates.has(selectedTask.state) && <button type="button" onClick={() => void taskAction("cancel")} disabled={busy} className="grid min-h-11 min-w-11 place-items-center rounded-lg text-slate-300 hover:bg-white/5" aria-label="取消任务"><Square className="h-4 w-4" /></button>}
                  <button type="button" onClick={() => void taskAction(selectedTask.pinned ? "unpin" : "pin")} disabled={busy} aria-pressed={selectedTask.pinned} className="grid min-h-11 min-w-11 place-items-center rounded-lg text-slate-300 hover:bg-white/5" aria-label={selectedTask.pinned ? "取消固定" : "固定任务"}><Pin className="h-4 w-4" /></button>
                </div>
              </div>
              <ol className="mt-5 grid grid-cols-4 gap-1" aria-label="任务进度">{progressStages.map((stage, index) => { const currentIndex = progressStages.findIndex((item) => item.value === progressStage); const completed = index < currentIndex || selectedTask.state === "completed"; const current = index === currentIndex && selectedTask.state !== "completed"; return <li key={stage.value} className="min-w-0"><div className={`h-1 rounded-full ${completed ? "bg-emerald-300" : current ? "bg-cyan-300" : "bg-white/10"}`} /><span className={`mt-1.5 block text-xs ${completed ? "text-emerald-200" : current ? "text-cyan-100" : "text-slate-500"}`}>{stage.label}</span></li>; })}</ol>
            </section>

            {approvalsPending.length > 0 && <section className="my-4 rounded-xl bg-amber-300/10 p-4" aria-labelledby="worker-actions-heading" aria-live="polite">
              <div className="flex flex-wrap items-center justify-between gap-2"><h2 id="worker-actions-heading" className="inline-flex items-center gap-2 text-sm font-semibold text-amber-50"><ShieldCheck className="h-4 w-4 text-amber-200" />需要你的决定</h2><span className="text-xs text-amber-100/70">{approvalsPending.length} 项待处理</span></div>
              {approvalsPending.map((approval) => <article key={approval.approval_id} className="mt-3 border-t border-amber-100/10 pt-3 first:border-t-0 first:pt-0">
                <p className="mb-2 text-sm font-medium text-amber-50">{approvalCapabilityLabel(approval.capability)}</p>
                <div className="rounded-lg bg-slate-950/60 px-3 py-2 font-mono text-sm text-slate-100">{approvalSummary(approval)}</div>
                <dl className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs">{approvalField(approval.request, "mode") && <div><dt className="inline text-slate-500">模式 </dt><dd className="inline text-slate-200">{approvalField(approval.request, "mode")}</dd></div>}{approvalField(approval.request, "cwd") && <div><dt className="inline text-slate-500">目录 </dt><dd className="inline text-slate-200">{approvalField(approval.request, "cwd")}</dd></div>}{approvalField(approval.request, "timeout_seconds") && <div><dt className="inline text-slate-500">超时 </dt><dd className="inline text-slate-200">{approvalField(approval.request, "timeout_seconds")} 秒</dd></div>}<div><dt className="inline text-slate-500">网络 </dt><dd className="inline text-slate-200">{approval.request.network_scope_sha256 ? "受限范围" : "关闭"}</dd></div></dl>
                <details className="mt-2 text-xs text-slate-400"><summary className="min-h-11 cursor-pointer py-2">查看操作绑定</summary><code className="block break-all pb-2">operation {approval.operation_id}{approvalField(approval.request, "script_sha256") && <><br />script {approvalField(approval.request, "script_sha256")}</>}</code></details>
                <div className="mt-3 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end"><button type="button" disabled={busy} onClick={() => void decide(approval.approval_id, "reject")} className="min-h-11 rounded-lg border border-white/15 px-4 text-sm text-slate-200">拒绝并反馈</button><button type="button" disabled={busy} onClick={() => void decide(approval.approval_id, "approve_once")} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-amber-200 px-4 text-sm font-semibold text-slate-950"><CheckCircle2 className="h-4 w-4" />批准本次执行</button></div>
              </article>)}
            </section>}

            <section className="border-b border-white/10 py-4" aria-labelledby="goal-heading">
              <div className="flex items-center justify-between gap-3"><h2 id="goal-heading" className="text-sm font-semibold text-white">验收条件</h2><span className="text-xs text-slate-500">创建后不可降级</span></div>
              <ul className="mt-2 flex flex-wrap gap-2">{selectedTask.spec.acceptance.required_checks.map((check) => { const state = evidenceStatus(check.check_id, evidence); return <li key={check.check_id} className="inline-flex min-h-9 items-center gap-2 rounded-lg bg-white/5 px-3 text-xs text-slate-200"><span className={state === "passed" ? "text-emerald-300" : state === "failed" ? "text-rose-300" : state === "invalidated" ? "text-amber-300" : "text-slate-500"}>{state === "passed" ? "通过" : state === "failed" ? "失败" : state === "invalidated" ? "已失效" : "待执行"}</span><span>{check.label}</span></li>; })}</ul>
            </section>

            {latestPlan && <section className="border-b border-white/10 py-4" aria-labelledby="worker-plan-heading"><div className="flex items-center justify-between gap-3"><h2 id="worker-plan-heading" className="text-sm font-semibold text-white">当前计划</h2><span className="text-xs text-slate-500">公开摘要</span></div><p className="mt-2 max-w-[72ch] whitespace-pre-wrap break-words text-sm leading-6 text-slate-300">{latestPlan}</p></section>}

            <section className="min-h-0 flex-1 py-4" aria-label="任务进展" aria-live="polite">
              <h2 className="text-sm font-semibold text-white">任务进展</h2>
              {activities.length === 0 ? <p className="mt-3 text-sm text-slate-400">等待 Worker 开始。状态、计划与工具摘要会显示在这里。</p> : <ol className="mt-4 space-y-5">{activities.slice(-80).map((activity) => <li key={activity.sequence} className="flex gap-3"><span className={`mt-1 grid h-7 w-7 shrink-0 place-items-center rounded-full ${activity.tone === "success" ? "bg-emerald-300/10 text-emerald-200" : activity.tone === "warning" ? "bg-amber-300/10 text-amber-200" : activity.tone === "danger" ? "bg-rose-300/10 text-rose-200" : "bg-cyan-300/10 text-cyan-200"}`}>{activity.tone === "success" ? <CheckCircle2 className="h-4 w-4" /> : activity.tone === "warning" ? <Clock3 className="h-4 w-4" /> : activity.tone === "danger" ? <CircleAlert className="h-4 w-4" /> : <Activity className="h-4 w-4" />}</span><div className="min-w-0 flex-1"><div className="flex flex-wrap items-baseline justify-between gap-2"><h3 className="text-sm font-medium text-slate-100">{activity.title}</h3><span className="text-xs text-slate-600">{activity.meta}</span></div><p className="mt-1 max-w-[72ch] whitespace-pre-wrap break-words text-sm leading-6 text-slate-300">{activity.detail}</p>{activity.operationId && <button type="button" onClick={() => setTab("terminal")} className="mt-1 min-h-11 text-xs text-cyan-200">查看操作 {shortId(activity.operationId)}</button>}</div></li>)}</ol>}
            </section>

            <form className="sticky bottom-0 border-t border-white/10 bg-[#060916]/95 py-3 backdrop-blur" onSubmit={(event) => { event.preventDefault(); void submitMessage(); }}>
              <label className="sr-only" htmlFor="worker-message">追加指令</label><div className="flex gap-2"><textarea id="worker-message" value={message} onChange={(event) => setMessage(event.target.value)} disabled={busy || terminalTaskStates.has(selectedTask.state)} placeholder="追加约束或调整方向，当前工具结束后生效" className="min-h-12 flex-1 resize-y rounded-lg border border-white/15 bg-slate-950/80 px-3 py-2 text-sm text-white placeholder:text-slate-500 disabled:opacity-60" /><button type="submit" disabled={busy || !message.trim() || terminalTaskStates.has(selectedTask.state)} className="inline-flex min-h-12 min-w-12 items-center justify-center gap-2 rounded-lg bg-cyan-300 px-4 text-sm font-semibold text-slate-950 disabled:opacity-50"><Send className="h-4 w-4" /><span className="hidden sm:inline">发送</span></button></div>
            </form>
          </div>}
        </main>

        <aside className="min-w-0 border-t border-white/10 py-3 lg:col-span-2 xl:col-span-1 xl:border-l xl:border-t-0 xl:pl-4" aria-label="任务检查器">
          {selectedTask?.state === "completed" && <section className="mb-4 rounded-xl bg-emerald-300/10 p-4" aria-labelledby="worker-result-heading"><div className="flex gap-3"><span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-emerald-300 text-slate-950"><CheckCircle2 className="h-5 w-5" /></span><div><h2 id="worker-result-heading" className="font-semibold text-emerald-50">任务完成</h2><p className="mt-1 text-xs leading-5 text-emerald-100/75">必需检查已通过，结果绑定当前 Workspace tree。</p></div></div><button type="button" onClick={() => setTab("diff")} className="mt-3 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg border border-emerald-200/25 text-sm text-emerald-50"><Eye className="h-4 w-4" />检查完整 Diff</button></section>}
          <div className="flex overflow-x-auto border-b border-white/10" role="tablist" aria-label="任务检查器视图">
            {([ ["files", FolderTree, "文件"], ["diff", GitCompareArrows, "Diff"], ["changesets", Archive, "变更"], ["diagnostics", CircleAlert, "诊断"], ["evidence", TestTube2, "测试"], ["terminal", TerminalSquare, "终端"] ] as const).map(([value, Icon, label]) => <button key={value} type="button" role="tab" aria-selected={tab === value} onClick={() => setTab(value)} className={`inline-flex min-h-11 shrink-0 items-center gap-1.5 px-3 text-sm ${tab === value ? "border-b-2 border-cyan-300 text-white" : "text-slate-400 hover:text-slate-200"}`}><Icon className="h-4 w-4" />{label}</button>)}
          </div>
          <div className="max-h-[34rem] overflow-auto py-3 text-sm xl:max-h-[calc(100vh-20rem)]">
            {tab === "files" && <div><p className="mb-2 break-all text-xs text-slate-400">tree {treeHash || "尚未创建"}</p>{preview ? <div><button type="button" className="mb-2 min-h-11 text-cyan-200" onClick={() => setPreview(null)}>返回文件树</button><p className="mb-2 break-all text-slate-300">{preview.path}</p><pre className="overflow-auto whitespace-pre-wrap break-words rounded-lg bg-slate-950/80 p-3 text-xs text-slate-200">{preview.content}</pre></div> : <ul className="space-y-1">{entries.map((entry) => <li key={entry.entry_id}><button type="button" disabled={entry.kind !== "file" || busy} onClick={() => void openEntry(entry)} className="flex min-h-11 w-full items-center gap-2 rounded-md px-2 text-left text-slate-300 hover:bg-white/5 disabled:cursor-default"><FileCode2 className="h-4 w-4 shrink-0" /><span className="min-w-0 break-all">{entry.display_path}</span></button></li>)}</ul>}</div>}
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
            {tab === "evidence" && <div className="space-y-5"><section><div className="flex items-center justify-between"><h3 className="font-semibold text-white">必需检查</h3><span className="text-xs text-slate-500">{evidence.filter((item) => item.status === "passed").length}/{selectedTask?.spec.acceptance.required_checks.length ?? 0} 通过</span></div>{evidence.length === 0 ? <p className="mt-3 text-slate-400">尚未产生检查证据。</p> : <ul className="mt-3 space-y-2">{evidence.map((item) => <li key={item.evidence_id} className="rounded-lg bg-white/5 p-3"><div className="flex items-center justify-between gap-2"><span className="break-all text-slate-200">{item.check_id}</span><span className={item.status === "passed" ? "text-emerald-300" : item.status === "failed" ? "text-rose-300" : "text-amber-300"}>{item.status === "passed" ? "通过" : item.status === "failed" ? "失败" : "已失效"}</span></div><span className="mt-1 block text-xs text-slate-400">exit {item.exit_code} · tree {shortId(item.workspace_tree_hash, 6)}</span></li>)}</ul>}</section><section><h3 className="font-semibold text-white">归档内容</h3>{artifacts.length === 0 ? <p className="mt-3 text-slate-400">暂无可下载内容。</p> : <ul className="mt-2 space-y-1">{artifacts.map((item) => <li key={item.artifact_id}><a className="block min-h-11 break-all rounded-md px-2 py-2.5 text-cyan-200 hover:bg-white/5" href={codingWorkerArtifactUrl(item.task_id, item.artifact_id)}>{item.media_type} · {shortId(item.artifact_id)}</a></li>)}</ul>}</section></div>}
            {tab === "terminal" && (
              <div aria-busy={inspectorLoading}>
                <p className="mb-2 text-xs text-slate-400">输出按 operation 归属并从持久事件补发；刷新页面不会丢失已归档片段。</p>
                {!status.capabilities.operation_output ? <p className="text-slate-400">终端补发当前关闭。</p> : null}
                {status.capabilities.operation_output && Object.values(operationOutputs).every((items) => items.length === 0) ? <p className="text-slate-400">{inspectorLoading ? "正在补发终端输出。" : "尚无命令输出。"}</p> : null}
                <div className="space-y-3">{Object.entries(operationOutputs).filter(([, chunks]) => chunks.length > 0).map(([operationId, chunks]) => <section key={operationId} className="overflow-hidden rounded-lg bg-slate-950/80"><h3 className="border-b border-white/10 px-3 py-2 break-all text-xs font-semibold text-cyan-100">{operationId}</h3><pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words p-3 text-xs">{chunks.map((chunk) => <span key={chunk.sequence} className={outputTone(chunk.stream)}>{chunk.text}{chunk.truncated ? "\n[输出已截断]\n" : ""}</span>)}</pre></section>)}</div>
              </div>
            )}
          </div>
          {context === "coding" && selectedTask && <section className="mt-3 border-t border-white/10 pt-4"><div className="flex items-center gap-2"><GitCompareArrows className="h-4 w-4 text-cyan-300" /><h3 className="font-semibold text-white">宿主写回</h3></div><p className="mt-1 text-xs leading-5 text-slate-400">Worker 不直接修改用户仓库。完成后继续使用 v13 的应用、提交、撤销和发布确认链。</p>{selectedTask.state === "completed" && selectedTask.spec.workspace_source.kind === "host_snapshot" ? <button type="button" onClick={() => void handoffToWriteback()} disabled={busy} className="mt-3 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-cyan-300 px-3 text-sm font-semibold text-slate-950 hover:bg-cyan-200 disabled:opacity-50">进入写回确认<ArrowRight className="h-4 w-4" /></button> : <p className="mt-3 text-xs text-slate-500">Host Snapshot 任务通过全部必需检查后开放。</p>}</section>}
        </aside>
      </div>
    </div>
  );
}
