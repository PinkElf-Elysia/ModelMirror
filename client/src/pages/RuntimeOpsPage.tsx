import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
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
  { label: "pending", value: "pending" },
  { label: "running", value: "running" },
  { label: "completed", value: "completed" },
  { label: "failed", value: "failed" },
  { label: "cancelled", value: "cancelled" },
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

function Sidebar() {
  return (
    <div>
      <p className="text-sm font-semibold text-white">Runtime Ops</p>
      <p className="mt-2 text-sm leading-6 text-slate-400">
        这里先做只读运维总览：MCP Runtime、全局工具、RunRegistry 与 Skill 安装状态。
      </p>
      <div className="mt-4 space-y-2">
        <Link
          className="block rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
          to="/mcps"
        >
          管理 MCP 工具
        </Link>
        <Link
          className="block rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
          to="/skills"
        >
          管理 Skill
        </Link>
        <Link
          className="block rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
          to="/studio"
        >
          返回工作空间
        </Link>
      </div>
    </div>
  );
}

function MetricCard({
  hint,
  label,
  tone = "neutral",
  value,
}: {
  hint: string;
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
    <div className="rounded-lg border border-white/10 bg-ink-950/72 p-4 shadow-prism">
      <p className="text-xs text-slate-400">{label}</p>
      <p className={`mt-2 text-3xl font-semibold ${toneMap[tone]}`}>{value}</p>
      <p className="mt-2 text-xs leading-5 text-slate-500">{hint}</p>
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
        <div className="m-4 rounded-lg border border-rose-300/20 bg-rose-300/10 px-3 py-2 text-sm text-rose-100">
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
  const [keyword, setKeyword] = useState("");
  const [selectedRunId, setSelectedRunId] = useState("");
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

  const refreshAll = useCallback(() => {
    void loadMcp();
    void loadTools();
    void loadRuns();
    void loadSkills();
    void loadEnvironment();
    void loadClientHosts();
  }, [loadClientHosts, loadEnvironment, loadMcp, loadRuns, loadSkills, loadTools]);

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
    refreshAll();
  }, [refreshAll]);

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

  return (
    <PageContainer
      activeResource="runtime"
      maxWidthClassName="max-w-[1760px]"
      sidebar={<Sidebar />}
    >
      <header className="mb-6 border-y border-hire-300/20 py-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded-full border border-hire-300/30 bg-hire-300/10 px-3 py-1 text-xs font-semibold text-hire-100">
                智能体运行诊断 Beta
              </span>
              <span className="rounded-full border border-white/10 bg-white/[0.055] px-3 py-1 text-xs text-slate-300">
                只读观测，不改变运行协议
              </span>
            </div>
            <h1 className="mt-3 text-2xl font-semibold text-white sm:text-3xl">
              Runtime 运维
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-400">
              汇总 MCP Runtime、工具注册表、RunRegistry 与 Skill 安装状态。这里先做观测与跳转，管理操作继续保留在各自页面。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              className="rounded-full bg-hire-300 px-4 py-2 text-sm font-semibold text-ink-950 transition hover:bg-hire-200"
              onClick={refreshAll}
              type="button"
            >
              刷新运行态
            </button>
            <Link
              className="rounded-full border border-white/10 bg-white/[0.055] px-4 py-2 text-sm font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
              to="/mcps"
            >
              连接 MCP
            </Link>
          </div>
        </div>
      </header>

      <section className="mb-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
        <MetricCard
          hint={`${mcpStatus.active} 活跃 · ${mcpStatus.failed} 异常 · ${mcpStatus.closed} 已关闭 · ${mcpStatus.unknown} 未知`}
          label="MCP 实例"
          tone="brand"
          value={mcpSessions.loading ? "..." : mcpStatus.total}
        />
        <MetricCard
          hint="来自全局 ToolRegistry"
          label="注册工具"
          tone="success"
          value={registryTools.loading ? "..." : registryTools.data.length}
        />
        <MetricCard
          hint={`${runStatusSummary.running} running · ${runStatusSummary.cancelled} cancelled`}
          label="最近失败 Run"
          tone={runStatusSummary.failed > 0 ? "error" : "success"}
          value={runs.loading ? "..." : runStatusSummary.failed}
        />
        <MetricCard
          hint="来自本地 Skill runtime"
          label="已安装 Skill"
          tone="success"
          value={skills.loading ? "..." : skills.data.length}
        />
        <MetricCard
          hint="仅显示布尔就绪态，不展示密钥值"
          label="环境摘要"
          tone={environment.data?.model_gateway_ready ? "success" : "warning"}
          value={environment.loading ? "..." : environment.data?.model_gateway_ready ? "就绪" : "检查"}
        />
      </section>

      <section className="mb-5 grid gap-3 rounded-lg border border-white/10 bg-white/[0.045] p-4 lg:grid-cols-[minmax(0,1fr)_180px_180px_auto] lg:items-end">
        <label className="block">
          <span className="text-xs font-semibold text-slate-300">搜索运行资源</span>
          <input
            className="mt-2 h-11 w-full rounded-lg border border-white/10 bg-ink-950/72 px-3 text-sm text-white outline-none transition placeholder:text-slate-500 hover:border-white/20 focus:border-hire-300/70 focus:ring-4 focus:ring-hire-300/10"
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="MCP session、工具名、run id、Skill"
            type="search"
            value={keyword}
          />
        </label>
        <label className="block">
          <span className="text-xs font-semibold text-slate-300">Run 类型</span>
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
          <span className="text-xs font-semibold text-slate-300">Run 状态</span>
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
          className="h-11 rounded-lg border border-hire-300/25 bg-hire-300/10 px-4 text-sm font-semibold text-hire-100 transition hover:bg-hire-300/20"
          onClick={refreshAll}
          type="button"
        >
          应用并刷新
        </button>
      </section>

      <SectionShell
        action={
          <div className="flex flex-wrap gap-2">
            <a
              className="rounded-full border border-white/10 bg-white/[0.055] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-cyan-300/30 hover:text-cyan-100"
              href="/api/runtime/client-hosts/extension.zip"
            >
              下载 Chrome 扩展
            </a>
            <a
              className="rounded-full border border-white/10 bg-white/[0.055] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-emerald-300/30 hover:text-emerald-100"
              href="/api/runtime/office-host/manifest.xml"
            >
              下载 Office Manifest
            </a>
            <button
              className="rounded-full bg-cyan-300 px-3 py-1.5 text-xs font-semibold text-slate-950 disabled:opacity-50"
              disabled={clientHostBusy.startsWith("pairing-")}
              onClick={() => void createClientPairing("chrome")}
              type="button"
            >
              配对 Chrome
            </button>
            <button
              className="rounded-full bg-emerald-300 px-3 py-1.5 text-xs font-semibold text-slate-950 disabled:opacity-50"
              disabled={clientHostBusy.startsWith("pairing-")}
              onClick={() => void createClientPairing("office")}
              type="button"
            >
              配对 Office
            </button>
          </div>
        }
        description="Chrome 绑定当前标签页；Office 加载项绑定当前 Word、Excel 或 PowerPoint 文档。Token 只保存在各宿主本地。Office 需要先运行证书脚本并启动 office profile。"
        error={clientHosts.error}
        title="客户端宿主"
      >
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
          <div className="p-8 text-center">
            <p className="text-base font-semibold text-white">尚未配对客户端宿主</p>
            <p className="mt-2 text-sm text-slate-400">下载对应宿主并使用一次性配对码连接。配对后仍需主动绑定当前标签页或 Office 文档。</p>
            <a className="mt-3 inline-flex text-xs font-semibold text-cyan-200 hover:text-cyan-100" href="/api/runtime/client-tools/fixture" target="_blank" rel="noreferrer">打开无敏感数据测试页</a>
          </div>
        ) : (
          <>
            <div className="flex flex-wrap gap-2 border-b border-white/10 px-4 py-3">
              {(["all", "chrome", "office"] as const).map((type) => (
                <button
                  className={`rounded-full border px-3 py-1 text-xs font-semibold ${clientHostType === type ? "border-cyan-300/40 bg-cyan-300/15 text-cyan-100" : "border-white/10 text-slate-300"}`}
                  key={type}
                  onClick={() => setClientHostType(type)}
                  type="button"
                >
                  {type === "all" ? "全部" : type === "chrome" ? "Chrome" : "Office"}
                </button>
              ))}
              <a className="ml-auto text-xs text-emerald-200 hover:text-emerald-100" href="https://localhost:8443" target="_blank" rel="noreferrer">检查 Office HTTPS</a>
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
                  <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${statusClass(host.status)}`}>{host.status}</span>
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

      <div className="mt-5 grid gap-5 2xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.58fr)]">
        <div className="space-y-5">
          <SectionShell
            action={
              <Link
                className="rounded-full border border-white/10 bg-white/[0.055] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
                to="/mcps"
              >
                管理 MCP
              </Link>
            }
            description="查看由 MCP 管理器启动的 stdio runtime 实例。"
            error={mcpSessions.error}
            title="MCP Runtime"
          >
            {mcpSessions.loading ? (
              <div className="p-4 text-sm text-slate-400">MCP runtime 加载中...</div>
            ) : (
              <>
                <div className="flex flex-wrap gap-2 border-b border-white/10 px-4 py-3">
                  {mcpStatusFilters.map((filter) => (
                    <button
                      className={`rounded-full border px-3 py-1 text-xs font-semibold transition ${
                        mcpStatusFilter === filter.value
                          ? "border-hire-300/40 bg-hire-300/15 text-hire-100"
                          : "border-white/10 bg-white/[0.045] text-slate-300 hover:border-white/20 hover:bg-white/[0.075]"
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
                  <div className="p-8 text-center">
                    <p className="text-base font-semibold text-white">未找到 MCP runtime</p>
                    <p className="mt-2 text-sm text-slate-400">
                      在 `/mcps` 连接 MCP Server 后，runtime 实例会显示在这里。
                    </p>
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="min-w-full divide-y divide-white/10 text-sm">
                      <thead className="bg-white/[0.04] text-left text-xs text-slate-400">
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
                            <td className="px-4 py-3 font-mono text-xs text-brand-100">
                              {shortId(session.session_id)}
                            </td>
                            <td className="px-4 py-3">
                              <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${statusClass(session.status ?? "unknown")}`}>
                                {session.status ?? "unknown"}
                              </span>
                            </td>
                            <td className="px-4 py-3 text-white">{session.tools_count ?? 0}</td>
                            <td className="px-4 py-3 text-slate-400">
                              {Math.max(0, Math.floor(session.uptime_seconds ?? 0))}s
                            </td>
                            <td className="max-w-xl px-4 py-3 font-mono text-xs text-slate-500">
                              {(session.server_command ?? []).join(" ") || "未记录"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}
          </SectionShell>

          <SectionShell
            action={
              <span className="rounded-full border border-brand-300/25 bg-brand-300/10 px-3 py-1.5 text-xs font-semibold text-brand-100">
                {visibleTools.length} 个工具
              </span>
            }
            description="聚合已连接 MCP Server 暴露的工具，供 workflow/chat/runtime toolset 复用。"
            error={registryTools.error}
            title="Tool Registry"
          >
            {registryTools.loading ? (
              <div className="p-4 text-sm text-slate-400">工具注册表加载中...</div>
            ) : visibleTools.length === 0 ? (
              <div className="p-8 text-center">
                <p className="text-base font-semibold text-white">当前没有注册工具</p>
                <p className="mt-2 text-sm text-slate-400">
                  连接 MCP Server 后，工具会进入全局 ToolRegistry。
                </p>
              </div>
            ) : (
              <div className="grid gap-3 p-4 lg:grid-cols-2">
                {visibleTools.slice(0, 12).map((tool) => (
                  <article
                    className="rounded-lg border border-white/10 bg-white/[0.045] p-3"
                    key={`${tool.session_id ?? tool.server_id ?? "tool"}-${tool.name}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-white">{tool.name}</p>
                        <p className="mt-1 text-xs text-brand-100">
                          {tool.server_id ?? "unknown server"}
                        </p>
                      </div>
                      <span className="shrink-0 rounded-full border border-white/10 bg-white/[0.055] px-2 py-0.5 text-[11px] text-slate-300">
                        {schemaFieldCount(tool)} 参数
                      </span>
                    </div>
                    <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-400">
                      {tool.description || "暂无工具描述"}
                    </p>
                  </article>
                ))}
              </div>
            )}
          </SectionShell>
        </div>

        <div className="space-y-5">
          <SectionShell
            action={
              <span className="rounded-full border border-hire-300/25 bg-hire-300/10 px-3 py-1.5 text-xs font-semibold text-hire-100">
                {visibleRuns.length} 条
              </span>
            }
            description="查看最近 workflow、chat、agent_task、agent_handoff 等运行记录。"
            error={runs.error}
            title="RunRegistry"
          >
            {runs.loading ? (
              <div className="p-4 text-sm text-slate-400">运行记录加载中...</div>
            ) : visibleRuns.length === 0 ? (
              <div className="p-8 text-center">
                <p className="text-base font-semibold text-white">暂无匹配运行记录</p>
                <p className="mt-2 text-sm text-slate-400">
                  运行工作流或开启 Chat Runtime Toolset 后会产生 run。
                </p>
              </div>
            ) : (
              <div className="divide-y divide-white/10">
                {visibleRuns.map((run) => (
                  <button
                    className={`block w-full border border-transparent px-4 py-3 text-left transition hover:bg-white/[0.045] ${
                      selectedRunId === run.run_id ? "border-hire-300/30 bg-hire-300/10" : ""
                    } ${
                      run.status === "failed"
                        ? "border-rose-300/20 bg-rose-300/10"
                        : run.status === "cancelled"
                          ? "border-slate-300/15 bg-white/[0.055]"
                          : ""
                    }`}
                    key={run.run_id}
                    onClick={() => setSelectedRunId((current) => (current === run.run_id ? "" : run.run_id))}
                    type="button"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-white">
                          {replaceLegacyAgentTerms(
                            run.title || getFriendlyRunTypeLabel(run.run_type),
                          )}
                        </p>
                        <p className="mt-1 text-xs text-slate-500">
                          {getFriendlyRunTypeLabel(run.run_type)} · {shortId(run.run_id)}
                        </p>
                      </div>
                      <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[11px] font-semibold ${statusClass(run.status)}`}>
                        {getFriendlyRunStatusLabel(run.status)}
                      </span>
                    </div>
                    <p className="mt-2 line-clamp-1 text-xs text-slate-400">
                      {metadataPreview(run.metadata)}
                    </p>
                    {run.error ? (
                      <p className="mt-2 line-clamp-2 rounded-md border border-rose-300/20 bg-rose-300/10 px-2 py-1 text-xs leading-5 text-rose-100">
                        {run.error}
                      </p>
                    ) : null}
                    {run.status === "failed" || run.status === "cancelled" ? (
                      <span className="mt-2 inline-flex rounded-full border border-white/10 bg-white/[0.055] px-2.5 py-1 text-[11px] font-semibold text-slate-300">
                        重试待接入
                      </span>
                    ) : null}
                    <p className="mt-2 text-xs text-slate-500">
                      更新于 {formatTime(run.updated_at ?? run.created_at)}
                    </p>
                  </button>
                ))}
              </div>
            )}
          </SectionShell>

          {selectedRunId ? (
            <section className="rounded-lg border border-hire-300/20 bg-hire-300/10 p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <h2 className="text-sm font-semibold text-white">Checkpoint 摘要</h2>
                  <p className="mt-1 text-xs text-slate-400">
                    Run {shortId(selectedRunId)} 的最近 30 条 checkpoint。
                  </p>
                </div>
                <button
                  className="rounded-full border border-white/10 bg-white/[0.055] px-2.5 py-1 text-xs font-semibold text-slate-200 transition hover:bg-white/10"
                  onClick={() => setSelectedRunId("")}
                  type="button"
                >
                  关闭
                </button>
              </div>
              {selectedRun ? (
                <div className="mt-4 rounded-lg border border-white/10 bg-ink-950/60 p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${statusClass(selectedRun.status)}`}>
                      {selectedRun.status}
                    </span>
                    <span className="rounded-full border border-white/10 bg-white/[0.055] px-2 py-0.5 text-[11px] text-slate-300">
                      error {selectedCheckpointCounts.error}
                    </span>
                    <span className="rounded-full border border-white/10 bg-white/[0.055] px-2 py-0.5 text-[11px] text-slate-300">
                      warning {selectedCheckpointCounts.warning}
                    </span>
                    <span className="rounded-full border border-white/10 bg-white/[0.055] px-2 py-0.5 text-[11px] text-slate-300">
                      info {selectedCheckpointCounts.info}
                    </span>
                  </div>
                  {selectedRun.error ? (
                    <p className="mt-3 line-clamp-3 text-xs leading-5 text-rose-100">
                      {selectedRun.error}
                    </p>
                  ) : null}
                  {selectedRun.status === "failed" || selectedRun.status === "cancelled" ? (
                    <button
                      className="mt-3 rounded-full border border-white/10 bg-white/[0.055] px-3 py-1 text-xs font-semibold text-slate-400"
                      disabled
                      type="button"
                    >
                      重试能力待接入
                    </button>
                  ) : null}
                </div>
              ) : null}
              {checkpoints.loading ? (
                <p className="mt-4 text-sm text-slate-400">Checkpoint 加载中...</p>
              ) : checkpoints.error ? (
                <p className="mt-4 rounded-lg border border-rose-300/20 bg-rose-300/10 px-3 py-2 text-sm text-rose-100">
                  {checkpoints.error}
                </p>
              ) : checkpoints.data.length === 0 ? (
                <p className="mt-4 rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2 text-sm text-slate-400">
                  该 run 暂无 checkpoint。
                </p>
              ) : (
                <div className="mt-4 space-y-2">
                  {checkpoints.data.slice(0, 8).map((checkpoint) => (
                    <article
                      className="rounded-lg border border-white/10 bg-ink-950/60 p-3"
                      key={checkpoint.checkpoint_id}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <p className="text-xs font-semibold text-white">
                          {checkpoint.title || checkpoint.event_type}
                        </p>
                        <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${statusClass(checkpoint.severity)}`}>
                          {checkpoint.severity}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-brand-100">{checkpoint.event_type}</p>
                      {checkpoint.summary ? (
                        <p className="mt-2 line-clamp-2 text-xs leading-5 text-slate-400">
                          {checkpoint.summary}
                        </p>
                      ) : null}
                      <p className="mt-2 text-xs text-slate-500">
                        {formatTime(checkpoint.created_at)}
                      </p>
                    </article>
                  ))}
                </div>
              )}
            </section>
          ) : null}

          <SectionShell
            action={
              <Link
                className="rounded-full border border-white/10 bg-white/[0.055] px-3 py-1.5 text-xs font-semibold text-slate-200 transition hover:border-hire-300/35 hover:bg-hire-300/10 hover:text-hire-100"
                to="/skills"
              >
                管理 Skill
              </Link>
            }
            description="展示当前已安装 Skill，完整安装和卸载仍在 Skill 页面完成。"
            error={skills.error}
            title="Skill Runtime"
          >
            {skills.loading ? (
              <div className="p-4 text-sm text-slate-400">Skill 状态加载中...</div>
            ) : visibleSkills.length === 0 ? (
              <div className="p-8 text-center">
                <p className="text-base font-semibold text-white">暂无已安装 Skill</p>
                <p className="mt-2 text-sm text-slate-400">
                  前往 `/skills` 安装后，这里会显示运行态摘要。
                </p>
              </div>
            ) : (
              <div className="divide-y divide-white/10">
                {visibleSkills.slice(0, 6).map((skill) => (
                  <article className="px-4 py-3" key={skill.skill_id}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-semibold text-white">
                          {skill.name}
                        </p>
                        <p className="mt-1 line-clamp-1 text-xs text-slate-400">
                          {skill.description || "暂无描述"}
                        </p>
                      </div>
                      <span className="shrink-0 rounded-full border border-emerald-300/25 bg-emerald-300/10 px-2 py-0.5 text-[11px] font-semibold text-emerald-100">
                        已安装
                      </span>
                    </div>
                    <p className="mt-2 text-xs text-slate-500">
                      {skill.repo_url ?? "本地 Skill"} {skill.installed_at ? `· ${formatTime(skill.installed_at)}` : ""}
                    </p>
                  </article>
                ))}
              </div>
            )}
          </SectionShell>

          <SectionShell
            action={
              environment.data?.redacted ? (
                <span className="rounded-full border border-emerald-300/25 bg-emerald-300/10 px-3 py-1.5 text-xs font-semibold text-emerald-100">
                  已脱敏
                </span>
              ) : null
            }
            description="只展示网关与运行依赖的布尔就绪态，不读取或显示密钥值。"
            error={environment.error}
            title="环境与依赖观测"
          >
            {environment.loading ? (
              <div className="p-4 text-sm text-slate-400">环境摘要加载中...</div>
            ) : environment.data ? (
              <div className="grid gap-2 p-4 sm:grid-cols-2">
                {dependencyRows.map((item) => (
                  <div
                    className="flex items-center justify-between gap-3 rounded-lg border border-white/10 bg-white/[0.045] px-3 py-2"
                    key={item.label}
                  >
                    <span className="text-sm text-slate-300">{item.label}</span>
                    <span className={`rounded-full border px-2 py-0.5 text-[11px] font-semibold ${booleanTone(item.value)}`}>
                      {booleanStatus(item.value)}
                    </span>
                  </div>
                ))}
                <p className="sm:col-span-2 text-xs leading-5 text-slate-500">
                  更新时间 {formatTime(environment.data.updated_at)}。该区不展示 `.env` 内容、API key 或本地路径。
                </p>
              </div>
            ) : (
              <div className="p-4 text-sm text-slate-400">暂无环境摘要。</div>
            )}
          </SectionShell>
        </div>
      </div>
    </PageContainer>
  );
}
