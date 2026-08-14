import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import ModelWorkbenchSidebar from "../components/ModelWorkbenchSidebar";
import PageContainer from "../components/PageContainer";
import {
  getFriendlyRunStatusLabel,
  getFriendlyRunTypeLabel,
  replaceLegacyAgentTerms,
} from "../utils/userFriendlyText";

type Loadable<T> = {
  data: T;
  error: string;
  loading: boolean;
};

interface McpSessionPayload {
  session_id: string;
  server_command?: string[];
  status?: string;
  created_at?: number;
  uptime_seconds?: number;
  idle_seconds?: number;
  tools_count?: number;
}

interface RegistryToolPayload {
  name: string;
  description?: string | null;
  input_schema?: Record<string, unknown>;
  inputSchema?: Record<string, unknown>;
  server_id?: string;
  session_id?: string;
  registered_at?: number;
}

interface RuntimeRunPayload {
  run_id: string;
  run_type: string;
  status: string;
  title: string;
  source_id?: string | null;
  parent_run_id?: string | null;
  metadata?: Record<string, unknown>;
  created_at: number;
  updated_at: number;
  error?: string | null;
}

interface RuntimeCheckpointPayload {
  checkpoint_id: string;
  run_id: string;
  event_type: string;
  title: string;
  summary?: string | null;
  severity: string;
  metadata?: Record<string, unknown>;
  created_at: number;
}

interface InstalledSkillPayload {
  skill_id: string;
  name: string;
  description?: string;
  repo_url?: string;
  sub_path?: string;
  installed_at?: number;
}

interface EnvironmentSummaryPayload {
  llm_gateway_configured: boolean;
  openrouter_configured: boolean;
  model_gateway_ready: boolean;
  git_available: boolean;
  node_available: boolean;
  npm_available: boolean;
  npx_available: boolean;
  python_available: boolean;
  redacted: boolean;
  updated_at: number;
}

interface ClientHostPayload {
  host_id: string;
  name: string;
  token_prefix: string;
  status: string;
  version: string;
  capabilities: Array<{ name: string }>;
  host_type?: "chrome" | "office";
  office_app?: "word" | "excel" | "powerpoint" | "";
  document_binding?: {
    bound?: boolean;
    binding_id?: string;
    title?: string;
  };
  requirement_sets?: string[];
  bound_tab: {
    bound?: boolean;
    origin?: string;
    title?: string;
  };
  revoked: boolean;
  last_heartbeat_at?: number | null;
}

interface ClientPairingPayload {
  pairing_id: string;
  pairing_code: string;
  expires_at: number;
  single_use: boolean;
  host_type?: "chrome" | "office";
}

type RuntimeFilter = "all" | "workflow" | "workflow_agent" | "agent_task" | "agent_handoff" | "chat" | "goal";
type StatusFilter = "all" | "pending" | "running" | "completed" | "failed" | "cancelled";
type McpStatusFilter = "all" | "active" | "failed" | "closed" | "unknown";
type RuntimeResourceView = "mcp" | "tools" | "skills" | "environment";

const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});

const runTypeFilters: Array<{ label: string; value: RuntimeFilter }> = [
  { label: "全部类型", value: "all" },
  { label: "工作流", value: "workflow" },
  { label: "工作流智能体", value: "workflow_agent" },
  { label: "智能体任务", value: "agent_task" },
  { label: "智能体交接", value: "agent_handoff" },
  { label: "聊天", value: "chat" },
  { label: "长期目标", value: "goal" },
];

const statusFilters: Array<{ label: string; value: StatusFilter }> = [
  { label: "全部状态", value: "all" },
  { label: "等待中", value: "pending" },
  { label: "运行中", value: "running" },
  { label: "已完成", value: "completed" },
  { label: "失败", value: "failed" },
  { label: "已取消", value: "cancelled" },
];

const mcpStatusFilters: Array<{ label: string; value: McpStatusFilter }> = [
  { label: "全部", value: "all" },
  { label: "活跃", value: "active" },
  { label: "异常", value: "failed" },
  { label: "已关闭", value: "closed" },
  { label: "未知", value: "unknown" },
];

function createLoadable<T>(data: T, loading = false): Loadable<T> {
  return { data, error: "", loading };
}

async function readJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`请求失败：${response.status}`);
  }
  return response.json() as Promise<T>;
}

function formatTime(value: number | null | undefined) {
  if (!value || !Number.isFinite(value)) return "暂无";
  const timestamp = value > 10_000_000_000 ? value : value * 1000;
  return dateFormatter.format(new Date(timestamp));
}

function shortId(value: string | null | undefined) {
  if (!value) return "无";
  return value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

function normalizeStatus(value: string | null | undefined) {
  return (value || "unknown").toLowerCase();
}

function getMcpStatusBucket(status: string | null | undefined): Exclude<McpStatusFilter, "all"> {
  const normalized = normalizeStatus(status);
  if (["active", "connected", "running", "completed", "succeeded"].includes(normalized)) {
    return "active";
  }
  if (["failed", "error"].includes(normalized)) {
    return "failed";
  }
  if (["cancelled", "closed", "stopped", "disconnected"].includes(normalized)) {
    return "closed";
  }
  return "unknown";
}

function getFriendlyMcpStatusLabel(status: string | null | undefined) {
  const bucket = getMcpStatusBucket(status);
  if (bucket === "active") return "活跃";
  if (bucket === "failed") return "异常";
  if (bucket === "closed") return "已关闭";
  return "未知";
}

function getFriendlySeverityLabel(severity: string | null | undefined) {
  const normalized = normalizeStatus(severity);
  if (normalized === "error") return "错误";
  if (normalized === "warning") return "警告";
  return "信息";
}

function statusClass(status: string) {
  const normalized = normalizeStatus(status);
  if (["active", "connected", "running", "completed", "succeeded"].includes(normalized)) {
    return "border-emerald-300/25 bg-emerald-300/10 text-emerald-100";
  }
  if (["failed", "error"].includes(normalized)) {
    return "border-rose-300/25 bg-rose-300/10 text-rose-100";
  }
  if (["cancelled", "closed", "stopped"].includes(normalized)) {
    return "border-slate-300/20 bg-white/[0.055] text-slate-300";
  }
  return "border-hire-300/25 bg-hire-300/10 text-hire-100";
}

function metadataPreview(metadata: Record<string, unknown> | undefined) {
  if (!metadata) return "无 metadata";
  const entries = Object.entries(metadata)
    .filter(([, value]) => typeof value !== "object")
    .slice(0, 3)
    .map(([key, value]) => `${key}: ${String(value)}`);
  return entries.length ? entries.join(" · ") : "metadata 已记录";
}

function schemaFieldCount(tool: RegistryToolPayload) {
  const schema = tool.input_schema ?? tool.inputSchema ?? {};
  const properties = schema.properties;
  if (properties && typeof properties === "object" && !Array.isArray(properties)) {
    return Object.keys(properties as Record<string, unknown>).length;
  }
  return 0;
}

function booleanStatus(value: boolean) {
  return value ? "就绪" : "未就绪";
}

function booleanTone(value: boolean) {
  return value
    ? "border-emerald-300/25 bg-emerald-300/10 text-emerald-100"
    : "border-slate-300/20 bg-white/[0.055] text-slate-300";
}

function severityCounts(checkpoints: RuntimeCheckpointPayload[]) {
  return checkpoints.reduce(
    (acc, checkpoint) => {
      const severity = normalizeStatus(checkpoint.severity);
      if (severity === "error") acc.error += 1;
      else if (severity === "warning") acc.warning += 1;
      else acc.info += 1;
      return acc;
    },
    { error: 0, info: 0, warning: 0 },
  );
}

function HealthSummaryItem({
  detail,
  label,
  tone = "neutral",
  value,
}: {
  detail: string;
  label: string;
  tone?: "neutral" | "brand" | "success" | "warning" | "error";
  value: string | number;
}) {
  const toneMap = {
    brand: "text-brand-100",
    error: "text-rose-100",
    neutral: "text-white",
    success: "text-emerald-100",
    warning: "text-hire-100",
  } as const;

  return (
    <div className="min-w-0 px-4 py-3.5">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm font-medium text-slate-300">{label}</p>
        <p className={`text-lg font-semibold ${toneMap[tone]}`}>{value}</p>
      </div>
      <p className="mt-1 truncate text-xs leading-5 text-slate-400">{detail}</p>
    </div>
  );
}

function SectionShell({
  action,
  children,
  description,
  error,
  title,
}: {
  action?: ReactNode;
  children: React.ReactNode;
  description: string;
  error?: string;
  title: string;
}) {
  return (
    <section className="rounded-lg border border-white/10 bg-ink-950/72 shadow-prism">
      <div className="flex flex-col gap-3 border-b border-white/10 p-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-base font-semibold text-white">{title}</h2>
          <p className="mt-1 text-sm leading-6 text-slate-400">{description}</p>
        </div>
        {action}
      </div>
      {error ? (
        <div className="m-4 rounded-lg border border-rose-300/20 bg-rose-300/10 px-3 py-2 text-sm text-rose-100" role="alert">
          {error}
        </div>
      ) : (
        children
      )}
    </section>
  );
}

export default function RuntimeOpsPage() {
  const [mcpSessions, setMcpSessions] = useState(createLoadable<McpSessionPayload[]>([], true));
  const [registryTools, setRegistryTools] = useState(createLoadable<RegistryToolPayload[]>([], true));
  const [runs, setRuns] = useState(createLoadable<RuntimeRunPayload[]>([], true));
  const [skills, setSkills] = useState(createLoadable<InstalledSkillPayload[]>([], true));
  const [environment, setEnvironment] = useState(
    createLoadable<EnvironmentSummaryPayload | null>(null, true),
  );
  const [clientHosts, setClientHosts] = useState(
    createLoadable<ClientHostPayload[]>([], true),
  );
  const [clientPairing, setClientPairing] = useState<ClientPairingPayload | null>(null);
  const [clientHostBusy, setClientHostBusy] = useState("");
  const [clientHostType, setClientHostType] = useState<"all" | "chrome" | "office">("all");
  const [runType, setRunType] = useState<RuntimeFilter>("all");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [mcpStatusFilter, setMcpStatusFilter] = useState<McpStatusFilter>("all");
  const [resourceView, setResourceView] = useState<RuntimeResourceView>("mcp");
  const [keyword, setKeyword] = useState("");
  const [selectedRunId, setSelectedRunId] = useState("");
  const [lastRefreshedAt, setLastRefreshedAt] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(true);
  const [checkpoints, setCheckpoints] = useState(
    createLoadable<RuntimeCheckpointPayload[]>([]),
  );

  useEffect(() => {
    document.title = "模镜 - Runtime Ops 运维";
  }, []);

  const loadMcp = useCallback(async () => {
    setMcpSessions((current) => ({ ...current, loading: true, error: "" }));
    try {
      const data = await readJson<{ sessions: McpSessionPayload[] }>("/api/mcp/sessions");
      setMcpSessions(createLoadable(data.sessions ?? []));
    } catch (error) {
      setMcpSessions({
        data: [],
        error: error instanceof Error ? error.message : "MCP Runtime 加载失败",
        loading: false,
      });
    }
  }, []);

  const loadTools = useCallback(async () => {
    setRegistryTools((current) => ({ ...current, loading: true, error: "" }));
    try {
      const data = await readJson<{ tools: RegistryToolPayload[] }>("/api/registry/tools");
      setRegistryTools(createLoadable(data.tools ?? []));
    } catch (error) {
      setRegistryTools({
        data: [],
        error: error instanceof Error ? error.message : "工具注册表加载失败",
        loading: false,
      });
    }
  }, []);

  const loadRuns = useCallback(async () => {
    setRuns((current) => ({ ...current, loading: true, error: "" }));
    try {
      const params = new URLSearchParams({ limit: "20" });
      if (runType !== "all") params.set("run_type", runType);
      if (status !== "all") params.set("status", status);
      const data = await readJson<RuntimeRunPayload[]>(`/api/runtime/runs?${params.toString()}`);
      setRuns(createLoadable(data ?? []));
    } catch (error) {
      setRuns({
        data: [],
        error: error instanceof Error ? error.message : "运行记录加载失败",
        loading: false,
      });
    }
  }, [runType, status]);

  const loadSkills = useCallback(async () => {
    setSkills((current) => ({ ...current, loading: true, error: "" }));
    try {
      const data = await readJson<{ skills: InstalledSkillPayload[] }>("/api/skills/installed");
      setSkills(createLoadable(data.skills ?? []));
    } catch (error) {
      setSkills({
        data: [],
        error: error instanceof Error ? error.message : "Skill 状态加载失败",
        loading: false,
      });
    }
  }, []);

  const loadEnvironment = useCallback(async () => {
    setEnvironment((current) => ({ ...current, loading: true, error: "" }));
    try {
      const data = await readJson<EnvironmentSummaryPayload>(
        "/api/runtime/environment-summary",
      );
      setEnvironment(createLoadable(data));
    } catch (error) {
      setEnvironment({
        data: null,
        error: error instanceof Error ? error.message : "环境观测加载失败",
        loading: false,
      });
    }
  }, []);

  const loadClientHosts = useCallback(async () => {
    setClientHosts((current) => ({ ...current, loading: true, error: "" }));
    try {
      const data = await readJson<{ hosts: ClientHostPayload[] }>(
        "/api/runtime/client-hosts",
      );
      setClientHosts(createLoadable(data.hosts ?? []));
    } catch (error) {
      setClientHosts({
        data: [],
        error: error instanceof Error ? error.message : "客户端宿主加载失败",
        loading: false,
      });
    }
  }, []);

  const refreshOverview = useCallback(async () => {
    await Promise.all([
      loadMcp(),
      loadTools(),
      loadSkills(),
      loadEnvironment(),
      loadClientHosts(),
    ]);
  }, [loadClientHosts, loadEnvironment, loadMcp, loadSkills, loadTools]);

  const refreshAll = useCallback(async () => {
    setRefreshing(true);
    try {
      await Promise.all([refreshOverview(), loadRuns()]);
      setLastRefreshedAt(Date.now());
    } finally {
      setRefreshing(false);
    }
  }, [loadRuns, refreshOverview]);

  async function createClientPairing(hostType: "chrome" | "office") {
    setClientHostBusy(`pairing-${hostType}`);
    try {
      const response = await fetch("/api/runtime/client-hosts/pairings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: hostType === "office" ? "Office Host" : "Chrome Host",
          host_type: hostType,
        }),
      });
      if (!response.ok) throw new Error("生成配对码失败");
      setClientPairing((await response.json()) as ClientPairingPayload);
    } catch (error) {
      setClientHosts((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "生成配对码失败",
      }));
    } finally {
      setClientHostBusy("");
    }
  }

  async function unbindClientHost(hostId: string) {
    setClientHostBusy(hostId);
    try {
      const response = await fetch(
        `/api/runtime/client-hosts/${encodeURIComponent(hostId)}/unbind`,
        { method: "POST" },
      );
      if (!response.ok) throw new Error("解除文档绑定失败");
      await loadClientHosts();
    } catch (error) {
      setClientHosts((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "解除文档绑定失败",
      }));
    } finally {
      setClientHostBusy("");
    }
  }

  async function revokeClientHost(hostId: string) {
    setClientHostBusy(hostId);
    try {
      const response = await fetch(
        `/api/runtime/client-hosts/${encodeURIComponent(hostId)}/revoke`,
        { method: "POST" },
      );
      if (!response.ok) throw new Error("撤销客户端宿主失败");
      await loadClientHosts();
    } catch (error) {
      setClientHosts((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "撤销客户端宿主失败",
      }));
    } finally {
      setClientHostBusy("");
    }
  }

  useEffect(() => {
    let cancelled = false;

    setRefreshing(true);
    void refreshOverview().finally(() => {
      if (!cancelled) {
        setLastRefreshedAt(Date.now());
        setRefreshing(false);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [refreshOverview]);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  useEffect(() => {
    if (!selectedRunId) {
      setCheckpoints(createLoadable([]));
      return;
    }

    let cancelled = false;
    async function loadCheckpoints() {
      setCheckpoints({ data: [], error: "", loading: true });
      try {
        const data = await readJson<RuntimeCheckpointPayload[]>(
          `/api/runtime/runs/${selectedRunId}/checkpoints?limit=30`,
        );
        if (!cancelled) setCheckpoints(createLoadable(data ?? []));
      } catch (error) {
        if (!cancelled) {
          setCheckpoints({
            data: [],
            error: error instanceof Error ? error.message : "Checkpoint 加载失败",
            loading: false,
          });
        }
      }
    }

    void loadCheckpoints();
    return () => {
      cancelled = true;
    };
  }, [selectedRunId]);

  const normalizedKeyword = keyword.trim().toLowerCase();

  const visibleSessions = useMemo(() => {
    return mcpSessions.data.filter((session) => {
      const matchesStatus =
        mcpStatusFilter === "all" || getMcpStatusBucket(session.status) === mcpStatusFilter;
      const matchesKeyword =
        !normalizedKeyword ||
        [
          session.session_id,
          session.status,
          ...(session.server_command ?? []),
        ]
          .join(" ")
          .toLowerCase()
          .includes(normalizedKeyword);
      return matchesStatus && matchesKeyword;
    });
  }, [mcpSessions.data, mcpStatusFilter, normalizedKeyword]);

  const visibleTools = useMemo(() => {
    if (!normalizedKeyword) return registryTools.data;
    return registryTools.data.filter((tool) =>
      [tool.name, tool.server_id, tool.session_id, tool.description]
        .join(" ")
        .toLowerCase()
        .includes(normalizedKeyword),
    );
  }, [normalizedKeyword, registryTools.data]);

  const visibleRuns = useMemo(() => {
    if (!normalizedKeyword) return runs.data;
    return runs.data.filter((run) =>
      [
        run.run_id,
        run.run_type,
        run.status,
        run.title,
        run.source_id,
        run.parent_run_id,
        metadataPreview(run.metadata),
      ]
        .join(" ")
        .toLowerCase()
        .includes(normalizedKeyword),
    );
  }, [normalizedKeyword, runs.data]);

  const visibleSkills = useMemo(() => {
    if (!normalizedKeyword) return skills.data;
    return skills.data.filter((skill) =>
      [skill.name, skill.description, skill.repo_url, skill.sub_path]
        .join(" ")
        .toLowerCase()
        .includes(normalizedKeyword),
    );
  }, [normalizedKeyword, skills.data]);

  const mcpStatus = useMemo(() => {
    const counts = { active: 0, closed: 0, failed: 0, total: mcpSessions.data.length, unknown: 0 };
    for (const session of mcpSessions.data) {
      counts[getMcpStatusBucket(session.status)] += 1;
    }
    return counts;
  }, [mcpSessions.data]);

  const runStatusSummary = useMemo(() => {
    const failed = runs.data.filter((run) => run.status === "failed");
    const cancelled = runs.data.filter((run) => run.status === "cancelled");
    const running = runs.data.filter((run) => run.status === "running");
    return {
      cancelled: cancelled.length,
      failed: failed.length,
      latestFailed: failed[0],
      running: running.length,
    };
  }, [runs.data]);

  const selectedRun = useMemo(
    () => runs.data.find((run) => run.run_id === selectedRunId),
    [runs.data, selectedRunId],
  );

  const selectedCheckpointCounts = useMemo(
    () => severityCounts(checkpoints.data),
    [checkpoints.data],
  );

  const dependencyRows = useMemo(() => {
    const data = environment.data;
    if (!data) return [];
    return [
      { label: "模型网关", value: data.model_gateway_ready },
      { label: "OpenRouter", value: data.openrouter_configured },
      { label: "LLM Gateway", value: data.llm_gateway_configured },
      { label: "git", value: data.git_available },
      { label: "node", value: data.node_available },
      { label: "npm", value: data.npm_available },
      { label: "npx", value: data.npx_available },
      { label: "python", value: data.python_available },
    ];
  }, [environment.data]);

  const clientHostSummary = useMemo(() => {
    const total = clientHosts.data.length;
    const active = clientHosts.data.filter(
      (host) => !host.revoked && getMcpStatusBucket(host.status) === "active",
    ).length;
    return { active, attention: Math.max(0, total - active), total };
  }, [clientHosts.data]);

  const readyDependencyCount = useMemo(
    () => dependencyRows.filter((item) => item.value).length,
    [dependencyRows],
  );

  return (
    <PageContainer
      activeResource="runtime"
      maxWidthClassName="max-w-[1760px]"
      mobileSidebar={<ModelWorkbenchSidebar compact />}
      showSystemCapabilityBar={false}
      sidebar={<ModelWorkbenchSidebar />}
      sidebarGridClassName="xl:grid-cols-[230px_minmax(0,1fr)] xl:gap-x-[54px]"
    >
      <header className="mb-4 flex flex-col gap-4 border-b border-white/10 pb-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold text-white sm:text-3xl">Runtime 运维</h1>
            <span className="rounded-full border border-hire-300/25 bg-hire-300/10 px-2.5 py-1 text-xs font-semibold text-hire-100">
              只读诊断
            </span>
          </div>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
            集中查看运行异常、MCP 连接和环境状态，管理操作仍在对应页面完成。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <p aria-live="polite" className="text-xs text-slate-400">
            {refreshing
              ? "正在刷新全部状态"
              : lastRefreshedAt
                ? `最近更新 ${formatTime(lastRefreshedAt)}`
                : "尚未刷新"}
          </p>
          <button
            className="min-h-11 rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-hire-200 disabled:cursor-wait disabled:opacity-65"
            disabled={refreshing}
            onClick={() => void refreshAll()}
            type="button"
          >
            {refreshing ? "刷新中" : "刷新全部"}
          </button>
        </div>
      </header>

      <section
        aria-label="运行健康摘要"
        className="mb-4 grid divide-y divide-white/10 overflow-hidden rounded-lg border border-white/10 bg-ink-950/62 md:grid-cols-4 md:divide-x md:divide-y-0"
      >
        <HealthSummaryItem
          detail={`${runStatusSummary.running} 运行中 · ${runStatusSummary.cancelled} 已取消`}
          label="运行状态"
          tone={runStatusSummary.failed > 0 ? "error" : "success"}
          value={runs.loading ? "..." : runStatusSummary.failed > 0 ? `${runStatusSummary.failed} 项异常` : "正常"}
        />
        <HealthSummaryItem
          detail={`${mcpStatus.failed} 异常 · ${mcpStatus.unknown} 未知`}
          label="MCP 连接"
          tone={mcpStatus.failed > 0 ? "error" : mcpStatus.active > 0 ? "brand" : "neutral"}
          value={mcpSessions.loading ? "..." : mcpStatus.total > 0 ? `${mcpStatus.active}/${mcpStatus.total} 活跃` : "未连接"}
        />
        <HealthSummaryItem
          detail={clientHostSummary.total > 0 ? `${clientHostSummary.attention} 个需要检查` : "可配对 Chrome 或 Office"}
          label="客户端宿主"
          tone={clientHostSummary.attention > 0 ? "warning" : clientHostSummary.active > 0 ? "success" : "neutral"}
          value={clientHosts.loading ? "..." : clientHostSummary.total > 0 ? `${clientHostSummary.active}/${clientHostSummary.total} 在线` : "未配对"}
        />
        <HealthSummaryItem
          detail="仅显示就绪状态，不展示密钥"
          label="环境依赖"
          tone={environment.data?.model_gateway_ready ? "success" : "warning"}
          value={environment.loading ? "..." : `${readyDependencyCount}/${dependencyRows.length} 就绪`}
        />
      </section>

      <section className="mb-4 grid gap-3 rounded-lg border border-white/10 bg-white/[0.045] p-4 lg:grid-cols-[minmax(0,1fr)_170px_170px_auto] lg:items-end">
        <label className="block">
          <span className="text-xs font-semibold text-slate-300">搜索运行资源</span>
          <input
            className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-ink-950/72 px-3 text-sm text-white outline-none transition placeholder:text-slate-400 hover:border-white/20 focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10"
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="MCP session、工具名、run id、Skill"
            type="search"
            value={keyword}
          />
        </label>
        <label className="block">
          <span className="text-xs font-semibold text-slate-300">运行类型</span>
          <select
            className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-ink-950/72 px-3 text-sm text-white outline-none transition focus:border-hire-300/70"
            onChange={(event) => setRunType(event.target.value as RuntimeFilter)}
            value={runType}
          >
            {runTypeFilters.map((filter) => (
              <option className="bg-ink-950" key={filter.value} value={filter.value}>
                {filter.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-xs font-semibold text-slate-300">运行状态</span>
          <select
            className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-ink-950/72 px-3 text-sm text-white outline-none transition focus:border-hire-300/70"
            onChange={(event) => setStatus(event.target.value as StatusFilter)}
            value={status}
          >
            {statusFilters.map((filter) => (
              <option className="bg-ink-950" key={filter.value} value={filter.value}>
                {filter.label}
              </option>
            ))}
          </select>
        </label>
        <button
          className="min-h-11 rounded-lg border border-white/10 bg-white/[0.045] px-4 text-sm font-semibold text-slate-200 transition hover:border-white/20 hover:bg-white/[0.075] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-300/60 disabled:cursor-default disabled:opacity-40"
          disabled={!keyword && runType === "all" && status === "all"}
          onClick={() => {
            setKeyword("");
            setRunType("all");
            setStatus("all");
          }}
          type="button"
        >
          清除筛选
        </button>
      </section>

      <div
        className={`mb-5 grid gap-4 ${
          selectedRunId
            ? "xl:grid-cols-[minmax(0,1fr)_minmax(320px,0.46fr)]"
            : ""
        }`}
      >
        <SectionShell
          action={
            <span className="rounded-full border border-hire-300/25 bg-hire-300/10 px-3 py-1.5 text-xs font-semibold text-hire-100">
              {visibleRuns.length} 条
            </span>
          }
          description="优先查看失败和运行中的工作流、对话及智能体任务。"
          error={runs.error}
          title="运行记录"
        >
          {runs.loading ? (
            <div className="p-4 text-sm text-slate-300">运行记录加载中...</div>
          ) : visibleRuns.length === 0 ? (
            <div className="px-4 py-7 text-center">
              <p className="text-base font-semibold text-white">暂无匹配运行记录</p>
              <p className="mt-2 text-sm text-slate-300">
                运行工作流或开启 Chat Runtime Toolset 后，记录会显示在这里。
              </p>
            </div>
          ) : (
            <div className="max-h-[34rem] divide-y divide-white/10 overflow-y-auto overscroll-contain">
              {visibleRuns.map((run) => (
                <button
                  aria-controls="runtime-run-detail"
                  aria-expanded={selectedRunId === run.run_id}
                  className={`block w-full border border-transparent px-4 py-3 text-left transition hover:bg-white/[0.045] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-hire-300/60 ${
                    selectedRunId === run.run_id
                      ? "border-hire-300/30 bg-hire-300/10"
                      : ""
                  } ${
                    run.status === "failed"
                      ? "border-rose-300/20 bg-rose-300/10"
                      : run.status === "cancelled"
                        ? "border-slate-300/15 bg-white/[0.055]"
                        : ""
                  }`}
                  key={run.run_id}
                  onClick={() =>
                    setSelectedRunId((current) =>
                      current === run.run_id ? "" : run.run_id,
                    )
                  }
                  type="button"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-white">
                        {replaceLegacyAgentTerms(
                          run.title || getFriendlyRunTypeLabel(run.run_type),
                        )}
                      </p>
                      <p className="mt-1 text-xs text-slate-400">
                        {getFriendlyRunTypeLabel(run.run_type)} · {shortId(run.run_id)}
                      </p>
                    </div>
                    <span
                      className={`shrink-0 rounded-full border px-2 py-0.5 text-xs font-semibold ${statusClass(run.status)}`}
                    >
                      {getFriendlyRunStatusLabel(run.status)}
                    </span>
                  </div>
                  <p className="mt-2 line-clamp-1 text-xs text-slate-300">
                    {metadataPreview(run.metadata)}
                  </p>
                  {run.error ? (
                    <p className="mt-2 line-clamp-2 rounded-md border border-rose-300/20 bg-rose-300/10 px-2 py-1 text-xs leading-5 text-rose-100">
                      {run.error}
                    </p>
                  ) : null}
                  <p className="mt-2 text-xs text-slate-400">
                    更新于 {formatTime(run.updated_at ?? run.created_at)}
                  </p>
                </button>
              ))}
            </div>
          )}
        </SectionShell>

        {selectedRunId ? (
          <section className="rounded-lg border border-hire-300/20 bg-ink-950/72 xl:sticky xl:top-24 xl:self-start" id="runtime-run-detail">
            <div className="flex items-start justify-between gap-3 border-b border-white/10 p-4">
              <div>
                <h2 className="text-base font-semibold text-white">运行详情</h2>
                <p className="mt-1 text-xs text-slate-300">
                  {shortId(selectedRunId)} 的最近 30 条检查点
                </p>
              </div>
              <button
                className="min-h-9 rounded-full border border-white/10 bg-white/[0.055] px-3 text-xs font-semibold text-slate-200 transition hover:bg-white/10"
                onClick={() => setSelectedRunId("")}
                type="button"
              >
                关闭详情
              </button>
            </div>
            {selectedRun ? (
              <div className="border-b border-white/10 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${statusClass(selectedRun.status)}`}
                  >
                    {getFriendlyRunStatusLabel(selectedRun.status)}
                  </span>
                  <span className="text-xs text-rose-100">错误 {selectedCheckpointCounts.error}</span>
                  <span className="text-xs text-hire-100">警告 {selectedCheckpointCounts.warning}</span>
                  <span className="text-xs text-slate-300">信息 {selectedCheckpointCounts.info}</span>
                </div>
                {selectedRun.error ? (
                  <p className="mt-3 line-clamp-3 text-xs leading-5 text-rose-100">
                    {selectedRun.error}
                  </p>
                ) : null}
              </div>
            ) : null}
            {checkpoints.loading ? (
              <p className="p-4 text-sm text-slate-300">检查点加载中...</p>
            ) : checkpoints.error ? (
              <p className="m-4 rounded-lg border border-rose-300/20 bg-rose-300/10 px-3 py-2 text-sm text-rose-100">
                {checkpoints.error}
              </p>
            ) : checkpoints.data.length === 0 ? (
              <p className="p-4 text-sm text-slate-300">该运行暂无检查点。</p>
            ) : (
              <div className="divide-y divide-white/10">
                {checkpoints.data.slice(0, 8).map((checkpoint) => (
                  <article className="px-4 py-3" key={checkpoint.checkpoint_id}>
                    <div className="flex items-start justify-between gap-3">
                      <p className="text-xs font-semibold text-white">
                        {checkpoint.title || checkpoint.event_type}
                      </p>
                      <span
                        className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${statusClass(checkpoint.severity)}`}
                      >
                        {getFriendlySeverityLabel(checkpoint.severity)}
                      </span>
                    </div>
                    {checkpoint.summary ? (
                      <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-300">
                        {checkpoint.summary}
                      </p>
                    ) : null}
                    <p className="mt-2 text-xs text-slate-400">
                      {checkpoint.event_type} · {formatTime(checkpoint.created_at)}
                    </p>
                  </article>
                ))}
              </div>
            )}
          </section>
        ) : null}
      </div>

      <SectionShell
        action={
          <div className="flex flex-wrap gap-2">
            <button
              className="min-h-11 rounded-full bg-cyan-300 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-cyan-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200 disabled:opacity-50"
              disabled={clientHostBusy.startsWith("pairing-")}
              onClick={() => void createClientPairing("chrome")}
              type="button"
            >
              配对 Chrome
            </button>
            <button
              className="min-h-11 rounded-full border border-emerald-300/30 bg-emerald-300/10 px-4 py-2 text-sm font-semibold text-emerald-100 transition hover:bg-emerald-300/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-200 disabled:opacity-50"
              disabled={clientHostBusy.startsWith("pairing-")}
              onClick={() => void createClientPairing("office")}
              type="button"
            >
              配对 Office
            </button>
          </div>
        }
        description="配对 Chrome 当前标签页或 Office 当前文档。Token 只保存在客户端。"
        error={clientHosts.error}
        title="客户端宿主"
      >
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-white/10 px-4 py-2.5 text-xs text-slate-400">
          <span className="font-medium text-slate-300">首次使用</span>
          <a className="hover:text-cyan-100" href="/api/runtime/client-hosts/extension.zip">下载 Chrome 扩展</a>
          <a className="hover:text-emerald-100" href="/api/runtime/office-host/manifest.xml">下载 Office Manifest</a>
          <a className="hover:text-cyan-100" href="/api/runtime/client-tools/fixture" rel="noreferrer" target="_blank">打开测试页</a>
          <a className="sm:ml-auto hover:text-emerald-100" href="https://localhost:8443" rel="noreferrer" target="_blank">检查 Office HTTPS</a>
        </div>
        {clientPairing ? (
          <div className="border-b border-white/10 bg-cyan-300/[0.07] px-4 py-3">
            <p className="text-xs text-cyan-100">
              在 {clientPairing.host_type === "office" ? "Office Task Pane" : "扩展 Popup"} 中输入以下一次性配对码，5 分钟内有效：
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <code className="rounded-md border border-cyan-300/20 bg-black/25 px-4 py-2 text-xl font-semibold text-white">
                {clientPairing.pairing_code}
              </code>
              <span className="text-[11px] text-slate-400">失效时间 {formatTime(clientPairing.expires_at)}</span>
              <button className="text-[11px] text-slate-400 hover:text-white" onClick={() => setClientPairing(null)} type="button">隐藏</button>
            </div>
          </div>
        ) : null}
        {clientHosts.loading ? (
          <div className="p-4 text-sm text-slate-400">客户端宿主加载中...</div>
        ) : clientHosts.data.length === 0 ? (
          <div className="flex flex-col gap-1 px-4 py-5 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm font-semibold text-white">尚未配对客户端宿主</p>
            <p className="text-sm text-slate-400">选择上方宿主生成一次性配对码。</p>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap gap-2 border-b border-white/10 px-4 py-3">
              {(["all", "chrome", "office"] as const).map((type) => (
                <button
                  aria-pressed={clientHostType === type}
                  className={`rounded-full border px-3 py-1 text-xs font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200 ${clientHostType === type ? "border-cyan-300/40 bg-cyan-300/15 text-cyan-100" : "border-white/10 text-slate-300"}`}
                  key={type}
                  onClick={() => setClientHostType(type)}
                  type="button"
                >
                  {type === "all" ? "全部" : type === "chrome" ? "Chrome" : "Office"}
                </button>
              ))}
            </div>
            <div className="grid gap-3 p-4 lg:grid-cols-2">
            {clientHosts.data
              .filter((host) => clientHostType === "all" || (host.host_type ?? "chrome") === clientHostType)
              .map((host) => (
              <article className="rounded-lg border border-white/10 bg-white/[0.045] p-3" key={host.host_id}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-white">{host.name}</p>
                    <p className="mt-1 truncate font-mono text-[10px] text-slate-500">{host.host_id} · {host.token_prefix}...</p>
                  </div>
                  <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${statusClass(host.status)}`}>{getFriendlyMcpStatusLabel(host.status)}</span>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-400">
                  <p>{(host.host_type ?? "chrome") === "office" ? `Office ${host.office_app || "未识别"}` : "Chrome 扩展"} v{host.version || "-"}</p>
                  <p>{host.capabilities.length} 个工具</p>
                  <p className="col-span-2 truncate">
                    {(host.host_type ?? "chrome") === "office"
                      ? host.document_binding?.bound
                        ? `已绑定文档 · ${host.document_binding.title || host.document_binding.binding_id}`
                        : "Office 文档未绑定"
                      : host.bound_tab?.bound
                        ? `已绑定标签页 · ${host.bound_tab.title || host.bound_tab.origin}`
                        : "标签页未绑定"}
                  </p>
                  {(host.host_type ?? "chrome") === "office" ? <p className="col-span-2 truncate">{host.requirement_sets?.join(" · ") || "Requirement Set 待检测"}</p> : null}
                  <p className="col-span-2">最近心跳 {formatTime(host.last_heartbeat_at)}</p>
                </div>
                <div className="mt-3 flex items-center justify-between border-t border-white/10 pt-2">
                  {(host.host_type ?? "chrome") === "office" ? (
                    <button className="text-[11px] text-amber-200 hover:text-amber-100 disabled:opacity-50" disabled={clientHostBusy === host.host_id || !host.document_binding?.bound} onClick={() => void unbindClientHost(host.host_id)} type="button">解除文档绑定</button>
                  ) : (
                    <a className="text-[11px] text-cyan-200 hover:text-cyan-100" href="/api/runtime/client-tools/fixture" target="_blank" rel="noreferrer">测试标签页</a>
                  )}
                  {!host.revoked ? (
                    <button className="text-[11px] text-rose-200 hover:text-rose-100 disabled:opacity-50" disabled={clientHostBusy === host.host_id} onClick={() => void revokeClientHost(host.host_id)} type="button">撤销 Token</button>
                  ) : null}
                </div>
              </article>
            ))}
            </div>
          </>
        )}
      </SectionShell>

      <div className="mt-5">
        <SectionShell
          action={
            resourceView === "mcp" ? (
              <Link className="rounded-full border border-white/10 bg-white/[0.055] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100" to="/mcps">
                管理 MCP
              </Link>
            ) : resourceView === "skills" ? (
              <Link className="rounded-full border border-white/10 bg-white/[0.055] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100" to="/skills">
                管理 Skill
              </Link>
            ) : resourceView === "environment" && environment.data?.redacted ? (
              <span className="rounded-full border border-emerald-300/25 bg-emerald-300/10 px-3 py-1.5 text-xs font-semibold text-emerald-100">已脱敏</span>
            ) : (
              <span className="rounded-full border border-brand-300/25 bg-brand-300/10 px-3 py-1.5 text-xs font-semibold text-brand-100">{visibleTools.length} 个工具</span>
            )
          }
          description="在一个面板中切换查看连接、工具、Skill 和运行依赖。"
          error={resourceView === "mcp" ? mcpSessions.error : resourceView === "tools" ? registryTools.error : resourceView === "skills" ? skills.error : environment.error}
          title="运行资源"
        >
          <div aria-label="运行资源视图" className="flex flex-wrap gap-2 border-b border-white/10 px-4 py-3">
            {([
              { count: mcpSessions.data.length, label: "MCP 连接", value: "mcp" },
              { count: visibleTools.length, label: "工具", value: "tools" },
              { count: visibleSkills.length, label: "Skill", value: "skills" },
              { count: `${readyDependencyCount}/${dependencyRows.length}`, label: "环境依赖", value: "environment" },
            ] as const).map((item) => (
              <button
                aria-pressed={resourceView === item.value}
                className={`min-h-11 rounded-full border px-4 py-2 text-sm font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-hire-300/60 ${
                  resourceView === item.value
                    ? "border-hire-300/40 bg-hire-300/15 text-hire-100"
                    : "border-white/10 bg-white/[0.04] text-slate-300 hover:border-white/20 hover:bg-white/[0.075]"
                }`}
                key={item.value}
                onClick={() => setResourceView(item.value)}
                type="button"
              >
                {item.label} <span className="ml-1 text-xs opacity-70">{item.count}</span>
              </button>
            ))}
          </div>

          {resourceView === "mcp" ? (
            mcpSessions.loading ? (
              <div className="p-4 text-sm text-slate-400">MCP 连接加载中...</div>
            ) : (
              <>
                <div className="flex flex-wrap gap-2 border-b border-white/10 px-4 py-3">
                  {mcpStatusFilters.map((filter) => (
                    <button
                      aria-pressed={mcpStatusFilter === filter.value}
                      className={`rounded-full border px-3 py-1 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-cyan-200 ${
                        mcpStatusFilter === filter.value
                          ? "border-cyan-300/35 bg-cyan-300/10 text-cyan-100"
                          : "border-white/10 text-slate-400 hover:text-white"
                      }`}
                      key={filter.value}
                      onClick={() => setMcpStatusFilter(filter.value)}
                      type="button"
                    >
                      {filter.label}
                    </button>
                  ))}
                </div>
                {visibleSessions.length === 0 ? (
                  <div className="flex flex-col gap-1 px-4 py-5 sm:flex-row sm:items-center sm:justify-between">
                    <p className="text-sm font-semibold text-white">没有符合条件的 MCP 连接</p>
                    <p className="text-sm text-slate-400">前往 MCP 工具采购连接服务。</p>
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-white/10 text-sm">
                      <thead className="bg-white/[0.035] text-left text-xs text-slate-400">
                        <tr>
                          <th className="px-4 py-3">Session</th>
                          <th className="px-4 py-3">状态</th>
                          <th className="px-4 py-3">工具</th>
                          <th className="px-4 py-3">运行时间</th>
                          <th className="px-4 py-3">命令</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-white/10">
                        {visibleSessions.map((session) => (
                          <tr className="align-top text-slate-300" key={session.session_id}>
                            <td className="px-4 py-3 font-mono text-xs text-brand-100">{shortId(session.session_id)}</td>
                            <td className="px-4 py-3"><span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${statusClass(session.status ?? "unknown")}`}>{getFriendlyMcpStatusLabel(session.status)}</span></td>
                            <td className="px-4 py-3 text-white">{session.tools_count ?? 0}</td>
                            <td className="px-4 py-3 text-slate-400">{Math.max(0, Math.floor(session.uptime_seconds ?? 0))}s</td>
                            <td className="max-w-xl px-4 py-3 font-mono text-xs text-slate-500">{(session.server_command ?? []).join(" ") || "未记录"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )
          ) : resourceView === "tools" ? (
            registryTools.loading ? (
              <div className="p-4 text-sm text-slate-400">工具注册表加载中...</div>
            ) : visibleTools.length === 0 ? (
              <div className="flex flex-col gap-1 px-4 py-5 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm font-semibold text-white">当前没有注册工具</p>
                <p className="text-sm text-slate-400">连接 MCP 服务后，工具会显示在这里。</p>
              </div>
            ) : (
              <div className="divide-y divide-white/10">
                {visibleTools.slice(0, 12).map((tool) => (
                  <article className="grid gap-2 px-4 py-3 sm:grid-cols-[minmax(180px,0.35fr)_minmax(0,1fr)_auto] sm:items-center" key={`${tool.session_id ?? tool.server_id ?? "tool"}-${tool.name}`}>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-white">{tool.name}</p>
                      <p className="mt-1 truncate text-xs text-brand-100">{tool.server_id ?? "unknown server"}</p>
                    </div>
                    <p className="line-clamp-2 text-xs leading-5 text-slate-400">{tool.description || "暂无工具描述"}</p>
                    <span className="w-fit rounded-full border border-white/10 bg-white/[0.055] px-2 py-0.5 text-[11px] text-slate-300">{schemaFieldCount(tool)} 参数</span>
                  </article>
                ))}
              </div>
            )
          ) : resourceView === "skills" ? (
            skills.loading ? (
              <div className="p-4 text-sm text-slate-400">Skill 状态加载中...</div>
            ) : visibleSkills.length === 0 ? (
              <div className="flex flex-col gap-1 px-4 py-5 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-sm font-semibold text-white">暂无已安装 Skill</p>
                <p className="text-sm text-slate-400">前往 Skill 技能培训安装后查看。</p>
              </div>
            ) : (
              <div className="divide-y divide-white/10">
                {visibleSkills.slice(0, 8).map((skill) => (
                  <article className="grid gap-2 px-4 py-3 sm:grid-cols-[minmax(180px,0.35fr)_minmax(0,1fr)_auto] sm:items-center" key={skill.skill_id}>
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-white">{skill.name}</p>
                      <p className="mt-1 truncate text-xs text-slate-500">{skill.repo_url ?? "本地 Skill"}</p>
                    </div>
                    <p className="line-clamp-2 text-xs leading-5 text-slate-400">{skill.description || "暂无描述"}</p>
                    <span className="w-fit rounded-full border border-emerald-300/25 bg-emerald-300/10 px-2 py-0.5 text-[11px] font-semibold text-emerald-100">已安装</span>
                  </article>
                ))}
              </div>
            )
          ) : environment.loading ? (
            <div className="p-4 text-sm text-slate-400">环境摘要加载中...</div>
          ) : environment.data ? (
            <div className="grid gap-x-6 gap-y-2 p-4 sm:grid-cols-2 lg:grid-cols-4">
              {dependencyRows.map((item) => (
                <div className="flex items-center justify-between gap-3 border-b border-white/10 py-2" key={item.label}>
                  <span className="text-sm text-slate-300">{item.label}</span>
                  <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${booleanTone(item.value)}`}>{booleanStatus(item.value)}</span>
                </div>
              ))}
              <p className="sm:col-span-2 lg:col-span-4 text-xs leading-5 text-slate-500">更新时间 {formatTime(environment.data.updated_at)}。不展示 `.env` 内容、API key 或本地路径。</p>
            </div>
          ) : (
            <div className="p-4 text-sm text-slate-400">暂无环境摘要。</div>
          )}
        </SectionShell>
      </div>
    </PageContainer>
  );
}
